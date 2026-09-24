"""``ignition-mcp reset``: undo a ``setup`` run (D32 section 2, issue #76).

One stage, ``reset``, on the ``reset`` command. Its plan reads the Gateway and the
deployment directory and writes nothing; the engine shows the plan, takes the
acceptances and the confirmation (``--yes`` in one-line mode), and only then runs the
apply, whose every write goes through the gated
:class:`~ignition_rest_mcp.cli.engine.main.ApplyContext`.

What ``setup`` created, and what the apply therefore removes:

* per role, its Server Config and its Gateway API token, with the two deletes
  :mod:`ignition_rest_mcp.cli.setup.runtime` already verified;
* the ``ignition-mcp-rest`` Gateway API token;
* the roles' Security Levels, by one minimal edit of the singleton;
* the Runtime Target Policy, by deleting the reserved Tag provider that holds it;
* the managed bundle project;
* the MCP Module, whose uninstall the Gateway applies on its next restart;
* the deployment directory itself.

Two things it refuses:

* a ``prod`` deployment, as D32 section 2 decides;
* a project carrying the bundle's name that ``setup`` did not deploy (no ownership
  marker, or an invalid one). That project is named in the plan and left alone, the
  same rule that stops ``setup`` taking it over.

The Module uninstall is the one removal the Gateway applies on a restart, so its plan
line carries the D32 section 6 restart acceptance.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from ignition_rest_mcp.cli.engine import main as engine
from ignition_rest_mcp.cli.engine.deployment import DEPLOYMENT_FILE
from ignition_rest_mcp.cli.engine.errors import CliError, ErrorCode
from ignition_rest_mcp.cli.engine.report import Status
from ignition_rest_mcp.cli.engine.resolve import PROG, Needed, Risk
from ignition_rest_mcp.cli.setup import rest, runtime
from ignition_rest_mcp.cli.setup.runtime import RemovalPlan, Role
from ignition_rest_mcp.cli.setup_native import documents as docs
from ignition_rest_mcp.cli.setup_native import gateway as gw
from ignition_rest_mcp.cli.setup_native import security
from ignition_rest_mcp.cli.setup_native.inputs import (
    API_TOKEN_TYPE,
    CONFIG_COLLECTION,
    SECURITY_LEVEL_PARENT,
    SECURITY_LEVELS_TYPE,
)
from ignition_rest_mcp.cli.setup_native.writer import RESOURCE_COLLECTION_PATH, WriteError

STAGE_NAME = "reset"

#: ``DELETE /data/api/v1/projects/{name}``: the route that removes a project.
PROJECT_DELETE_PATH = "/data/api/v1/projects/{name}"
#: ``DELETE /data/api/v1/modules/uninstall``: marks modules for removal on the next restart.
MODULE_UNINSTALL_PATH = "/data/api/v1/modules/uninstall"


@dataclass(slots=True)
class ResetPlan:
    """What the plan found on the Gateway and in the deployment directory."""

    installed: gw.ModuleIdentity | None
    project: gw.ProjectState
    #: The Security Level tree as served, and the two values an edit needs.
    levels: list[dict[str, Any]]
    levels_signature: str
    levels_collection: str
    #: The roles whose Security Level this reset removes.
    removed_levels: list[Role] = field(default_factory=list)
    #: Per role, the served signatures of its Server Config and its API token.
    removals: list[RemovalPlan] = field(default_factory=list)
    #: The served ``ignition-mcp-rest`` document.
    rest_token: dict[str, Any] | None = None
    #: The served reserved Tag provider document, when the policy provider exists.
    provider: dict[str, Any] | None = None
    #: The served bundle version when the managed project exists.
    project_version: str = ""
    #: Files and directories inside the deployment directory.
    entries: list[str] = field(default_factory=list)

    @property
    def module_build(self) -> str:
        if self.installed is None:
            return ""
        return self.installed.build or self.installed.raw_version

    @property
    def unmanaged(self) -> bool:
        return self.project.classification in (gw.UNMANAGED_SAME_NAME, gw.MARKER_INVALID)


def _unmanaged_detail(state: gw.ProjectState) -> str:
    return "no ownership marker" if state.classification == gw.UNMANAGED_SAME_NAME else "an invalid marker"


# ------------------------------------------------------------------------- plan


async def _observe(
    ctx: engine.Context, roles: list[Role]
) -> tuple[
    gw.ModuleIdentity | None,
    gw.ProjectState,
    dict[str, Any] | None,
    dict[str, dict[str, Any] | None],
    dict[str, dict[str, Any] | None],
    dict[str, Any] | None,
    dict[str, Any] | None,
]:
    """Every Gateway read the plan needs, with the GET-only client."""

    try:
        async with ctx.gateway_reader(runtime.SETTINGS.transport) as client:
            installed = await client.mcp_module()
            project = await client.find_project(runtime.PROJECT)
            levels = await client.singleton_document(SECURITY_LEVELS_TYPE)
            tokens = {role.name: await client.resource_document(API_TOKEN_TYPE, role.token) for role in roles}
            configs: dict[str, dict[str, Any] | None] = {}
            for role in roles:
                # Without the Module the Server Config type does not exist.
                configs[role.name] = (
                    await client.server_config_document(role.server_config) if installed is not None else None
                )
            rest_token = await client.resource_document(API_TOKEN_TYPE, rest.REST_TOKEN_NAME)
            provider = await client.resource_document(docs.TAG_PROVIDER_TYPE, docs.PROVIDER)
    except gw.GatewayProbeError as error:
        raise CliError(
            ErrorCode.STEP_FAILED,
            f"the Gateway's state could not be read ({error})",
            next_action=f"curl -sSI {ctx.resolved.values['gateway_url']}{gw.GATEWAY_INFO_PATH}",
        ) from error
    return installed, project, levels, tokens, configs, rest_token, provider


def plan(ctx: engine.Context) -> engine.Plan:
    """Read the Gateway and the deployment directory, and list every removal."""

    deployment = ctx.deployment
    if not deployment.exists:
        raise CliError(
            ErrorCode.DEPLOYMENT_UNREADABLE,
            f"{deployment.directory} has no {DEPLOYMENT_FILE}; there is no deployment to reset",
            next_action=f"{PROG} setup --deployment {deployment.name}",
        )
    environment = str(deployment.values.get("environment") or "dev")
    if environment == "prod":
        raise CliError(
            ErrorCode.INVALID_INPUT,
            f"the deployment {deployment.name} is {environment}; reset removes every managed resource and "
            "is refused outside dev",
            next_action=f"{PROG} setup --deployment {deployment.name} --environment dev",
        )
    url = str(ctx.resolved.values["gateway_url"]).rstrip("/")
    endpoint = runtime._endpoint_of(url)
    roles = runtime.saved_roles(ctx)
    installed, project, levels_doc, tokens, configs, rest_token, provider = asyncio.run(_observe(ctx, roles))
    if project.managed and project.inheritable is True:
        raise CliError(
            ErrorCode.STEP_FAILED,
            f"the managed project {runtime.PROJECT} is inheritable; make it standalone in the Designer first",
            next_action=f"curl -sS {url}{gw.PROJECT_FIND_PATH.format(name=runtime.PROJECT)}",
        )
    tree, signature, collection = runtime._levels(levels_doc, endpoint)
    reset_plan = ResetPlan(
        installed=installed,
        project=project,
        levels=tree,
        levels_signature=signature,
        levels_collection=collection,
        rest_token=rest_token,
        provider=provider,
        project_version=str(project.bundle_version or "") if project.managed else "",
    )
    changes: list[str] = []
    needed: list[Needed] = []
    for role in roles:
        removal = RemovalPlan(
            role,
            config_signature=str((configs.get(role.name) or {}).get("signature") or ""),
            token_signature=str((tokens.get(role.name) or {}).get("signature") or ""),
        )
        if removal.config_signature:
            changes.append(f"delete the Server Config {role.server_config}")
        if removal.token_signature:
            changes.append(f"delete the API token {role.token} and its local secret")
        if removal.config_signature or removal.token_signature:
            reset_plan.removals.append(removal)
        found = security.find_level(tree, role.level)
        if (
            found is not None
            and found[0] == [SECURITY_LEVEL_PARENT, role.level]
            and not security.level_shape_problem(found[1])
        ):
            reset_plan.removed_levels.append(role)
            changes.append(f"remove the Security Level {role.level_path}")
    if rest_token is not None:
        changes.append(f"delete the Gateway API token {rest.REST_TOKEN_NAME}")
    if provider is not None:
        changes.append(f"delete the Runtime Target Policy, the reserved {docs.PROVIDER} Tag provider")
    if reset_plan.project_version:
        changes.append(f"delete the managed project {runtime.PROJECT} (bundle {reset_plan.project_version})")
    elif reset_plan.unmanaged:
        changes.append(f"leave the project {runtime.PROJECT} alone: it carries {_unmanaged_detail(project)}")
    if installed is not None:
        changes.append(
            f"uninstall MCP Module build {reset_plan.module_build} and restart the Gateway, which applies it"
        )
        needed.append(Needed(Risk.RESTART, f"uninstalling MCP Module build {reset_plan.module_build}"))
    reset_plan.entries = sorted(
        str(path.relative_to(deployment.directory)) for path in deployment.directory.rglob("*")
    )
    if reset_plan.entries:
        changes.append(f"delete the deployment directory {deployment.directory} ({len(reset_plan.entries)} entries)")
    return engine.Plan(changes=changes, needed=needed, data=reset_plan)


# ------------------------------------------------------------------------ apply


def _failed(message: str, ctx: engine.ApplyContext) -> CliError:
    return CliError(ErrorCode.STEP_FAILED, message, next_action=f"{PROG} status --deployment {ctx.deployment.name}")


async def _api_token_gone(ctx: engine.Context, name: str) -> bool:
    async with ctx.gateway_reader(runtime.SETTINGS.transport) as client:
        return await client.resource_document(API_TOKEN_TYPE, name) is None


async def _server_config_gone(ctx: engine.Context, name: str) -> bool:
    async with ctx.gateway_reader(runtime.SETTINGS.transport) as client:
        return await client.server_config_document(name) is None


def apply(ctx: engine.ApplyContext, stage_plan: engine.Plan) -> None:
    reset_plan: ResetPlan = stage_plan.data
    for removal in reset_plan.removals:
        role = removal.role
        with ctx.reporter.step(f"reset {role.name}", f"the Server Config and API token of the {role.name} role") as end:
            removed = asyncio.run(runtime._remove_role(ctx, removal))
            left: list[str] = []
            if removal.config_signature and not asyncio.run(_server_config_gone(ctx, role.server_config)):
                left.append(f"the Server Config {role.server_config}")
            if removal.token_signature and not asyncio.run(_api_token_gone(ctx, role.token)):
                left.append(f"the API token {role.token}")
            if left:
                raise _failed(f"{removed}, but the Gateway still serves {', '.join(left)}", ctx)
            end.set(Status.CHANGED, removed)
    with ctx.reporter.step("reset rest token", rest.REST_TOKEN_NAME) as end:
        if reset_plan.rest_token is None:
            end.set(Status.OK, "the Gateway holds no REST server token")
        else:
            end.set(Status.CHANGED, asyncio.run(_delete_rest_token(ctx, reset_plan)))
    with ctx.reporter.step(
        "reset security levels",
        ", ".join(role.level_path for role in reset_plan.removed_levels) or "none",
    ) as end:
        if not reset_plan.removed_levels:
            end.set(Status.OK, "no Security Level created by setup is left")
        else:
            end.set(Status.CHANGED, asyncio.run(_remove_levels(ctx, reset_plan)))
    with ctx.reporter.step("reset runtime policy", docs.POLICY_PATH) as end:
        if reset_plan.provider is None:
            end.set(Status.OK, f"the reserved {docs.PROVIDER} Tag provider is absent")
        else:
            end.set(Status.CHANGED, asyncio.run(_delete_policy(ctx, reset_plan)))
    with ctx.reporter.step("reset bundle project", runtime.PROJECT) as end:
        if reset_plan.project_version:
            end.set(Status.CHANGED, asyncio.run(_delete_project(ctx, reset_plan)))
        elif reset_plan.unmanaged:
            end.set(
                Status.SKIPPED,
                f"the project {runtime.PROJECT} carries {_unmanaged_detail(reset_plan.project)}, so reset "
                "leaves it alone",
            )
        else:
            end.set(Status.OK, "the Gateway serves no managed bundle project")
    with ctx.reporter.step("reset module", f"MCP Module build {reset_plan.module_build or 'none'}") as end:
        if reset_plan.installed is None:
            end.set(Status.OK, "the Gateway has no MCP Module installed")
        else:
            end.set(Status.CHANGED, asyncio.run(_uninstall_module(ctx, reset_plan)))
    with ctx.reporter.step("reset deployment directory", str(ctx.deployment.directory)) as end:
        if not reset_plan.entries:
            end.set(Status.OK, "the deployment directory holds nothing")
        else:
            shutil.rmtree(ctx.deployment.directory)
            end.set(Status.CHANGED, f"deleted {len(reset_plan.entries)} entries")


async def _delete_rest_token(ctx: engine.ApplyContext, reset_plan: ResetPlan) -> str:
    served = reset_plan.rest_token or {}
    signature = served.get("signature")
    if not isinstance(signature, str) or not signature:
        raise _failed(f"{rest.REST_TOKEN_NAME} reads back no signature, so it was not deleted", ctx)
    collection = str(served.get("collection") or CONFIG_COLLECTION)
    async with ctx.gateway_writer(runtime.SETTINGS.transport) as writer:
        try:
            await writer._write(
                "DELETE",
                RESOURCE_COLLECTION_PATH.format(resource_type=API_TOKEN_TYPE)
                + f"/{quote(rest.REST_TOKEN_NAME, safe='')}/{quote(signature, safe='')}",
                body=b"",
                content_type="application/json",
                params={"collection": collection},
                action=f"delete API token {rest.REST_TOKEN_NAME}",
            )
        except WriteError as error:
            raise _failed(f"the Gateway API token {rest.REST_TOKEN_NAME} was not deleted: {error}", ctx) from error
    if not await _api_token_gone(ctx, rest.REST_TOKEN_NAME):
        raise _failed(f"the Gateway still serves the API token {rest.REST_TOKEN_NAME} after its delete", ctx)
    return f"deleted {rest.REST_TOKEN_NAME} from the {collection} collection"


async def _remove_levels(ctx: engine.ApplyContext, reset_plan: ResetPlan) -> str:
    tree = reset_plan.levels
    for role in reset_plan.removed_levels:
        tree = runtime._without_level(tree, role.level)
    async with ctx.gateway_writer(runtime.SETTINGS.transport) as writer:
        try:
            await writer.update_security_levels(
                tree, signature=reset_plan.levels_signature, collection=reset_plan.levels_collection
            )
            served = await writer.reads.singleton_document(SECURITY_LEVELS_TYPE)
        except (WriteError, gw.GatewayProbeError) as error:
            raise _failed(f"the Security Level edit stopped: {error}", ctx) from error
    problem = runtime._levels_problem(served, reset_plan.levels, [], reset_plan.removed_levels)
    if problem:
        raise _failed(f"the Security Level edit was accepted but {problem}", ctx)
    return "removed " + ", ".join(role.level_path for role in reset_plan.removed_levels)


async def _delete_policy(ctx: engine.ApplyContext, reset_plan: ResetPlan) -> str:
    served = reset_plan.provider or {}
    signature = served.get("signature")
    if not isinstance(signature, str) or not signature:
        raise _failed(f"the {docs.PROVIDER} provider reads back no signature, so it was not deleted", ctx)
    collection = str(served.get("collection") or CONFIG_COLLECTION)
    async with ctx.gateway_writer(runtime.SETTINGS.transport) as writer:
        try:
            await writer._write(
                "DELETE",
                RESOURCE_COLLECTION_PATH.format(resource_type=docs.TAG_PROVIDER_TYPE)
                + f"/{quote(docs.PROVIDER, safe='')}/{quote(signature, safe='')}",
                body=b"",
                content_type="application/json",
                params={"collection": collection, "confirm": "true"},
                action=f"delete the {docs.PROVIDER} Tag provider",
            )
        except WriteError as error:
            raise _failed(f"the Runtime Target Policy was not deleted: {error}", ctx) from error
    async with ctx.gateway_reader(runtime.SETTINGS.transport) as client:
        left = await client.resource_document(docs.TAG_PROVIDER_TYPE, docs.PROVIDER)
    if left is not None:
        raise _failed(f"the Gateway still serves the {docs.PROVIDER} Tag provider after its delete", ctx)
    return f"deleted the {docs.PROVIDER} Tag provider and the {docs.TAG_NAME} Tag it held"


async def _delete_project(ctx: engine.ApplyContext, reset_plan: ResetPlan) -> str:
    async with ctx.gateway_writer(runtime.SETTINGS.transport) as writer:
        try:
            await writer._write(
                "DELETE",
                PROJECT_DELETE_PATH.format(name=quote(runtime.PROJECT, safe="")),
                body=b"",
                content_type="application/json",
                params={"confirm": "true"},
                action=f"delete project {runtime.PROJECT}",
            )
            state = await writer.reads.find_project(runtime.PROJECT)
        except (WriteError, gw.GatewayProbeError) as error:
            raise _failed(f"the managed project was not deleted: {error}", ctx) from error
    if state.classification != gw.ABSENT:
        raise _failed(f"the project {runtime.PROJECT} reads back as {state.classification} after its delete", ctx)
    return f"deleted the managed project {runtime.PROJECT} (bundle {reset_plan.project_version})"


async def _uninstall_module(ctx: engine.ApplyContext, reset_plan: ResetPlan) -> str:
    """Mark the Module for uninstall, restart the Gateway, and wait for it to come back."""

    build = reset_plan.module_build
    async with ctx.gateway_writer(runtime.SETTINGS.transport) as writer:
        try:
            await writer._write(
                "DELETE",
                MODULE_UNINSTALL_PATH,
                body=json.dumps({"uninstall": [gw.MCP_MODULE_ID]}).encode("utf-8"),
                content_type="application/json",
                action=f"uninstall MCP Module {gw.MCP_MODULE_ID}",
            )
        except WriteError as error:
            raise _failed(f"the MCP Module was not marked for uninstall: {error}", ctx) from error
        try:
            await writer.restart_gateway()
        except WriteError as error:
            if error.status != 0:
                raise _failed(f"the restart request was refused: {error}", ctx) from error
        polls = int(runtime.RESTART_READY_SECONDS // runtime.RESTART_POLL_SECONDS)
        last = "the Gateway has not answered since the restart"
        for attempt in range(polls):
            try:
                identity = await writer.reads.module_identity(gw.MCP_MODULE_ID)
            except gw.GatewayProbeError as error:
                # A Gateway that is restarting refuses every read; that is not the Module
                # still being installed, so only a read that succeeds can end the wait.
                last = f"the Gateway is not answering ({error})"
            else:
                if identity is None:
                    return f"uninstalled MCP Module build {build} and restarted the Gateway"
                last = f"the Gateway still runs build {identity.build}"
            if attempt + 1 < polls:
                await runtime.SETTINGS.sleep(runtime.RESTART_POLL_SECONDS)
    raise _failed(f"the MCP Module did not go away within {runtime.RESTART_READY_SECONDS:g} s ({last})", ctx)


# ------------------------------------------------------------------ registration


STAGE = engine.Stage(STAGE_NAME, plan, apply)


def register() -> None:
    """Add the ``reset`` stage, so the command runs the plan and confirmation sequence."""

    command = engine.COMMANDS["reset"]
    # The prod refusal and the plan's own Gateway read come first, so the setup key is
    # taken as a file here instead of through the shared input's Gateway probe.
    command.inputs = ("gateway_url",)
    command.extra_inputs = [runtime.OBSERVED_TOKEN_INPUT]
    if not any(stage.name == STAGE_NAME for stage in command.stages):
        engine.register_stage(STAGE, command="reset")


def unregister() -> None:
    """Remove the stage again; tests use it to leave the engine as they found it."""

    stages = engine.COMMANDS["reset"].stages
    stages[:] = [stage for stage in stages if stage.name != STAGE_NAME]
    engine.COMMANDS["reset"].handler = engine._not_implemented
