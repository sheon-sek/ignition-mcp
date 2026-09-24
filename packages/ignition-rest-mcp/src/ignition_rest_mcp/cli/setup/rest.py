"""``setup`` on the REST plane (D32 sections 5, 8 and 9, issue #75).

The stage makes three things:

* ``ignition-mcp-rest``, the Gateway API token the REST server uses for its own
  calls. The Gateway generates its key, and the token gets the same Security Level
  grant as the operator's setup key, because D32 section 9 asks for that key's level
  to be ticked under every permission. The setup key itself is never given to the
  REST server.
* One Named static token per deployed Assistant role, with the scopes of D32
  section 8. They exist only in the deployment directory; the Gateway never sees them.
* The REST Mutation settings of D32 section 5, saved in ``deployment.toml``:
  the enabled Mutation classes, the Target allowlist and the project writer.

``start`` (:mod:`ignition_rest_mcp.cli.setup.start`) turns the saved values and the
secret files into the ``IGNITION_MCP_*`` values, so the operator never sets one.

A secret file holds one ``<name>:<key>`` line. For a Named static token the whole
line is the bearer value a client sends, so :func:`static_token_value` is all
``connect`` needs.

When ``ignition-mcp-rest`` exists on the Gateway and its secret file is lost, D32
section 10 applies: the token is deleted and created again with ``--recreate-tokens``
or after the wizard's confirmation. The Runtime stage registers that flag.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from ignition_rest_mcp.cli.engine import main as engine
from ignition_rest_mcp.cli.engine.deployment import Deployment, Value, read_secret
from ignition_rest_mcp.cli.engine.errors import CliError, ErrorCode
from ignition_rest_mcp.cli.engine.report import Status
from ignition_rest_mcp.cli.engine.resolve import PROG, InputSpec, Kind, Needed, Risk
from ignition_rest_mcp.cli.setup_native import security
from ignition_rest_mcp.cli.setup_native.inputs import API_TOKEN_TYPE, CONFIG_COLLECTION
from ignition_rest_mcp.cli.setup_native.writer import RESOURCE_COLLECTION_PATH, GatewayWriter
from ignition_rest_mcp.config import CONFIG_SCOPE, CONTROL_SCOPE, READ_SCOPE

STAGE_NAME = "rest"

#: The Gateway API token of the REST server (D32 section 9).
REST_TOKEN_NAME = "ignition-mcp-rest"
REST_TOKEN_SECRET = "rest-gateway-token"
REST_TOKEN_DESCRIPTION = "ignition-mcp REST server credential (deployment-owned)."

#: D32 section 8: each role's Named static token and its scopes.
ROLE_TOKEN_NAMES = {"analysis": "ignition-mcp-analysis", "engineer": "ignition-mcp-engineer"}
ROLE_SCOPES: dict[str, tuple[str, ...]] = {
    "analysis": (READ_SCOPE,),
    "engineer": (READ_SCOPE, CONFIG_SCOPE, CONTROL_SCOPE),
}

MUTATION_CLASSES = ("config", "control", "admin")
NONE = "none"
WILDCARD = "*"


def static_token_secret(role: str) -> str:
    """The secret file name of one role's Named static token."""

    return f"rest-{role}-token"


def static_token_value(deployment: Deployment, role: str) -> str:
    """The bearer value of one role's Named static token, read from its file."""

    return read_secret(deployment.secret_path(static_token_secret(role)))


# --------------------------------------------------------------------- settings


def _prod(values: Mapping[str, str]) -> bool:
    return values.get("environment") == "prod"


def _default_classes(values: Mapping[str, str]) -> str:
    return NONE if _prod(values) else "config,control"


def _default_allowlist(values: Mapping[str, str]) -> str:
    return NONE if _prod(values) else WILDCARD


def _default_writer(values: Mapping[str, str]) -> str:
    return "off" if _prod(values) else "on"


def check_classes(value: str, _values: Mapping[str, str]) -> str:
    """``none``, or a comma-separated list of config, control and admin."""

    if value == NONE:
        return ""
    items = [item.strip() for item in value.split(",")]
    if not all(item in MUTATION_CLASSES for item in items) or len(set(items)) != len(items):
        return f"{value!r} must be none or a comma-separated list of {', '.join(MUTATION_CLASSES)}"
    return ""


INPUTS = (
    InputSpec(
        name="rest_mutation_classes",
        flag="--rest-mutation-classes",
        question="REST Mutation classes to turn on (none, or a list of config, control, admin)",
        default=_default_classes,
        check=check_classes,
    ),
    InputSpec(
        name="rest_target_allowlist",
        flag="--rest-target-allowlist",
        question="REST Target allowlist for the enabled Mutation classes",
        kind=Kind.CHOICE,
        choices=(WILDCARD, NONE),
        default=_default_allowlist,
    ),
    InputSpec(
        name="rest_project_writer",
        flag="--rest-project-writer",
        question="REST project writer",
        kind=Kind.CHOICE,
        choices=("on", "off"),
        default=_default_writer,
    ),
)


@dataclass(frozen=True, slots=True)
class RestSettings:
    """The REST Mutation settings of one deployment."""

    classes: frozenset[str]
    wildcard_targets: bool
    project_writer: bool

    @classmethod
    def from_values(cls, values: Mapping[str, Value]) -> RestSettings:
        """Read the settings from resolved values or from ``deployment.toml``."""

        raw = {spec.name: values.get(spec.name) for spec in INPUTS}
        missing = [name for name, value in raw.items() if not isinstance(value, str)]
        if missing:
            raise CliError(
                ErrorCode.DEPLOYMENT_UNREADABLE,
                f"the deployment has no {', '.join(missing)}; setup has not run on the REST plane",
            )
        classes = str(raw["rest_mutation_classes"])
        if check_classes(classes, {}):
            raise CliError(ErrorCode.DEPLOYMENT_UNREADABLE, check_classes(classes, {}))
        return cls(
            classes=frozenset() if classes == NONE else frozenset(item.strip() for item in classes.split(",")),
            wildcard_targets=raw["rest_target_allowlist"] == WILDCARD,
            project_writer=raw["rest_project_writer"] == "on",
        )

    def describe(self) -> str:
        classes = ", ".join(name.upper() for name in MUTATION_CLASSES if name in self.classes) or "no"
        targets = "'*'" if self.wildcard_targets and self.classes else "empty"
        writer = "on" if self.project_writer else "off"
        return f"{classes} Mutation classes on, Target allowlists {targets}, project writer {writer}"


# ------------------------------------------------------------------------ plan


@dataclass(slots=True)
class RestPlan:
    """What :func:`plan` found, handed to :func:`apply`."""

    #: ``""`` when the Gateway token and its file agree, ``create`` or ``recreate``.
    token_action: str = ""
    #: The served ``ignition-mcp-rest`` document for ``recreate``.
    served: dict[str, Any] | None = None
    #: The setup key's name and the grant copied from it for ``create``/``recreate``.
    setup_key: str = ""
    grant: list[dict[str, Any]] = field(default_factory=list)
    secure_channel: bool = False
    #: Roles whose Named static token file must be created.
    new_static_tokens: list[str] = field(default_factory=list)
    save: bool = False


def _grant(document: dict[str, Any] | None) -> list[dict[str, Any]]:
    config = document.get("config") if isinstance(document, dict) else None
    profile = config.get("profile") if isinstance(config, dict) else None
    grant = profile.get("securityLevels") if isinstance(profile, dict) else None
    if isinstance(grant, list) and grant and all(isinstance(node, dict) for node in grant):
        return grant
    return []


def _level_names(grant: list[dict[str, Any]]) -> str:
    """The granted level paths, such as ``Authenticated/Administrator``."""

    paths: list[str] = []

    def walk(nodes: list[Any], prefix: str) -> None:
        for node in nodes:
            if not isinstance(node, dict):
                continue
            path = f"{prefix}/{node.get('name')}" if prefix else str(node.get("name"))
            children = node.get("children")
            if isinstance(children, list) and children:
                walk(children, path)
            else:
                paths.append(path)

    walk(grant, "")
    return ", ".join(paths)


def _recreate_flag(ctx: engine.Context) -> bool:
    return bool(getattr(ctx.args, "recreate_tokens", False))


def plan(ctx: engine.Context) -> engine.Plan:
    """Read the Gateway token, the secret files and the saved settings. Writes nothing."""

    settings = RestSettings.from_values(ctx.resolved.values)
    roles = ctx.resolved.list("roles")
    rest_plan = RestPlan()
    changes: list[str] = []
    needed: list[Needed] = []

    token_file = ctx.deployment.secret_path(REST_TOKEN_SECRET)
    observed = security.observe_secret_file(token_file)
    if observed.error:
        raise CliError(ErrorCode.SECRET_FILE_INVALID, f"{token_file}: {observed.error}")
    setup_key = ctx.resolved.secrets["gateway_token"].reveal().partition(":")[0]

    async def read() -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        async with ctx.gateway_reader() as reader:
            served = await reader.resource_document(API_TOKEN_TYPE, REST_TOKEN_NAME)
            setup = await reader.resource_document(API_TOKEN_TYPE, setup_key)
        return served, setup

    served, setup_document = asyncio.run(read())
    if served is not None and observed.exists:
        if not observed.hashes_to(security.stored_token_hash(served)):
            raise CliError(
                ErrorCode.SECRET_FILE_INVALID,
                f"{token_file} holds a key that does not match the Gateway token {REST_TOKEN_NAME}",
                next_action=f"rm {token_file} && {PROG} setup --deployment {ctx.deployment.name} --recreate-tokens",
            )
    elif served is None and observed.exists:
        raise CliError(
            ErrorCode.SECRET_FILE_INVALID,
            f"{token_file} holds a key for {REST_TOKEN_NAME}, which the Gateway no longer has",
            next_action=f"rm {token_file} && {PROG} setup --deployment {ctx.deployment.name}",
        )
    else:
        if served is not None:
            if ctx.prompter is None and not _recreate_flag(ctx):
                raise CliError(
                    ErrorCode.MISSING_INPUT,
                    f"the Gateway has the token {REST_TOKEN_NAME} but its secret file {token_file} is lost; "
                    "--recreate-tokens deletes the token and creates a new one. Nothing was written",
                    next_action=f"{PROG} setup --deployment {ctx.deployment.name} --recreate-tokens --yes",
                )
            rest_plan.token_action = "recreate"
            rest_plan.served = served
        else:
            rest_plan.token_action = "create"
        grant = _grant(setup_document)
        if not grant:
            raise CliError(
                ErrorCode.STEP_FAILED,
                f"the setup key {setup_key} reads back no Security Level grant, so {REST_TOKEN_NAME} "
                "cannot be given the same one",
                next_action=f"{PROG} setup --deployment {ctx.deployment.name} --gateway-token-file FILE",
            )
        rest_plan.setup_key = setup_key
        rest_plan.grant = grant
        rest_plan.secure_channel = ctx.resolved.values["gateway_url"].startswith("https:")
        verb = "delete the Gateway token whose secret file is lost, then create" if served else "create"
        changes.append(
            f"{verb} the Gateway API token {REST_TOKEN_NAME} with the Security Levels of the setup key "
            f"{setup_key} ({_level_names(grant)}); its secret goes to {token_file}"
        )

    for role in roles:
        path = ctx.deployment.secret_path(static_token_secret(role))
        state = security.observe_secret_file(path)
        if state.error:
            raise CliError(ErrorCode.SECRET_FILE_INVALID, f"{path}: {state.error}")
        if not state.exists:
            rest_plan.new_static_tokens.append(role)
            changes.append(
                f"create the Named static token {ROLE_TOKEN_NAMES[role]} for the {role} role "
                f"({', '.join(ROLE_SCOPES[role])}); its secret goes to {path}"
            )

    saved = {spec.name: ctx.deployment.values.get(spec.name) for spec in INPUTS}
    if saved != {spec.name: ctx.resolved.values.get(spec.name) for spec in INPUTS}:
        rest_plan.save = True
        changes.append(f"save the REST settings: {settings.describe()}")

    # A run takes these risks only when it writes the settings that carry them.
    if rest_plan.save and "admin" in settings.classes:
        needed.append(Needed(Risk.ADMIN_CLASS, "REST server"))
    if rest_plan.save and settings.wildcard_targets and settings.classes:
        enabled = ", ".join(name.upper() for name in MUTATION_CLASSES if name in settings.classes)
        needed.append(Needed(Risk.WILDCARD_ALLOWLIST, f"REST {enabled} Mutations"))
    return engine.Plan(changes=changes, needed=needed, data=rest_plan)


# ----------------------------------------------------------------------- apply


def apply(ctx: engine.ApplyContext, stage_plan: engine.Plan) -> None:
    rest_plan: RestPlan = stage_plan.data
    token_file = ctx.deployment.secret_path(REST_TOKEN_SECRET)
    with ctx.reporter.step(f"rest token {REST_TOKEN_NAME}", "the REST server's own Gateway API token") as end:
        if not rest_plan.token_action:
            end.set(Status.OK, f"exists and matches {token_file}")
        else:
            asyncio.run(_create_rest_token(ctx, rest_plan, ctx.gateway_writer()))
            end.set(
                Status.CHANGED,
                f"{'recreated' if rest_plan.token_action == 'recreate' else 'created'} with the Security Levels "
                f"of {rest_plan.setup_key} ({_level_names(rest_plan.grant)}); its secret was written to "
                f"{token_file} with mode 0600",
            )

    for role in ctx.resolved.list("roles"):
        name = ROLE_TOKEN_NAMES[role]
        with ctx.reporter.step(f"rest static token {name}", f"the {role} role's Named static token") as end:
            path = ctx.deployment.secret_path(static_token_secret(role))
            if role not in rest_plan.new_static_tokens:
                end.set(Status.OK, f"exists in {path}")
                continue
            written = ctx.write_secret(static_token_secret(role), f"{name}:{secrets.token_urlsafe(32)}")
            end.set(Status.CHANGED, f"created with {', '.join(ROLE_SCOPES[role])}; written to {written}")

    with ctx.reporter.step("rest settings", "REST Mutation classes, Target allowlists, project writer") as end:
        settings = RestSettings.from_values(ctx.resolved.values)
        if rest_plan.save:
            ctx.save()
            end.set(Status.CHANGED, settings.describe())
        else:
            end.set(Status.OK, settings.describe())


async def _create_rest_token(ctx: engine.ApplyContext, rest_plan: RestPlan, writer: GatewayWriter) -> None:
    async with writer:
        if rest_plan.token_action == "recreate":
            current = rest_plan.served or {}
            signature = current.get("signature")
            if not isinstance(signature, str) or not signature:
                raise CliError(ErrorCode.STEP_FAILED, f"{REST_TOKEN_NAME} reads back no signature; not deleted")
            collection = current.get("collection") or CONFIG_COLLECTION
            await writer._write(
                "DELETE",
                RESOURCE_COLLECTION_PATH.format(resource_type=API_TOKEN_TYPE)
                + f"/{quote(REST_TOKEN_NAME, safe='')}/{quote(signature, safe='')}",
                body=b"",
                content_type="application/json",
                params={"collection": str(collection)},
                action=f"delete API token {REST_TOKEN_NAME}",
            )
        key, declared = security.credential(await writer.generate_api_token())
        config = {
            "profile": {
                "type": security.BASIC_TOKEN_PROFILE,
                "secureChannelRequired": rest_plan.secure_channel,
                "securityLevels": rest_plan.grant,
                "timestamp": int(time.time() * 1000),
            },
            "settings": {"tokenHash": declared},
        }
        await writer.create_api_token(REST_TOKEN_NAME, config, description=REST_TOKEN_DESCRIPTION)
        served = await writer.reads.resource_document(API_TOKEN_TYPE, REST_TOKEN_NAME)
    if security.stored_token_hash(served) != declared:
        raise CliError(
            ErrorCode.STEP_FAILED,
            f"{REST_TOKEN_NAME} was created but reads back a different token hash, so its key was not stored",
            next_action=f"{PROG} setup --deployment {ctx.deployment.name} --recreate-tokens --yes",
        )
    ctx.write_secret(REST_TOKEN_SECRET, security.token_secret(REST_TOKEN_NAME, key))


STAGE = engine.Stage(STAGE_NAME, plan, apply, INPUTS)


def register() -> None:
    """Add the REST stage to ``setup`` once."""

    if STAGE not in engine.COMMANDS["setup"].stages:
        engine.register_stage(STAGE)
