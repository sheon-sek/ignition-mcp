"""``setup`` on the REST plane (D32 sections 5, 8 and 9, issues #75 and #79).

The stage makes these things:

* ``Authenticated/IgnitionMcpRest``, the REST server's own Security Level. It is
  created with D20's procedure, the same one the Runtime stage uses for the role
  levels. In ``prod`` it is created only with ``--provision-security-levels``.
* The General Settings grants of that level (issue #79). Native REST checks two
  entries of the ``security-properties`` singleton for an API token:
  ``readPermissions`` opens every read route and ``writePermissions`` every write
  route. ``prod`` grants read, ``dev`` grants read and write. ``accessPermissions``
  and ``designerPermissions`` are never changed. An ``AllOf`` entry is refused,
  because an added level there would lock out everyone who holds only the others.
* ``ignition-mcp-rest``, the Gateway API token the REST server uses for its own
  calls. The Gateway generates its key, and the token holds only the dedicated level.
  The operator's setup key is never given to the REST server.
* One Named static token per deployed Assistant role, with the scopes of D32
  section 8. They exist only in the deployment directory; the Gateway never sees them.
* The REST Mutation settings of D32 section 5, saved in ``deployment.toml``:
  the enabled Mutation classes, the Target allowlist and the project writer.

The level and each General Settings entry it was added to are recorded as created,
so ``reset`` removes exactly those. A move from ``dev`` to ``prod`` removes the
recorded write grant; a move back adds it again.

``start`` (:mod:`ignition_rest_mcp.cli.setup.start`) turns the saved values and the
secret files into the ``IGNITION_MCP_*`` values, so the operator never sets one.

A secret file holds one ``<name>:<key>`` line. For a Named static token the whole
line is the bearer value a client sends, so :func:`static_token_value` is all
``connect`` needs.

The named risks the saved settings carry, each ``*`` allowlist and the ADMIN class,
are recorded in ``deployment.toml`` under ``rest_accepted`` once a run accepts them.
A risky value without a matching record needs Explicit acceptance again, in
``setup`` and in ``start``, so a hand edit of ``deployment.toml`` cannot skip it.

When the Deployment environment changes, a REST setting that no flag of this run
sets takes the new environment's default, and the plan lists each narrowing or
widening (D32 section 5).

When ``ignition-mcp-rest`` exists on the Gateway and its secret file is lost, D32
section 10 applies: the token is deleted and created again with ``--recreate-tokens``
or after the wizard's confirmation. The Runtime stage registers that flag. When
the token's served profile, Security Levels or secure-channel setting differ from the
desired ones, the plan restores them, which needs the Explicit acceptance for
overwriting a hand edit. A token that still holds the setup key's level, as Phase 7
created it, moves to the dedicated level without that acceptance.
"""
from __future__ import annotations

import asyncio
import json
import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from ignition_rest_mcp.cli.engine import main as engine
from ignition_rest_mcp.cli.engine.deployment import CREATED_KEY, Deployment, Value, read_secret
from ignition_rest_mcp.cli.engine.errors import CliError, ErrorCode
from ignition_rest_mcp.cli.engine.report import Status
from ignition_rest_mcp.cli.engine.resolve import PROG, InputSpec, Kind, Needed, Risk, Source
from ignition_rest_mcp.cli.gateway_ops import gateway as gw
from ignition_rest_mcp.cli.gateway_ops import security
from ignition_rest_mcp.cli.gateway_ops.inputs import (
    API_TOKEN_TYPE,
    CONFIG_COLLECTION,
    SECURITY_LEVEL_PARENT,
    SECURITY_LEVELS_TYPE,
)
from ignition_rest_mcp.cli.gateway_ops.writer import RESOURCE_COLLECTION_PATH, GatewayWriter, WriteError
from ignition_rest_mcp.config import CONFIG_SCOPE, CONTROL_SCOPE, READ_SCOPE

STAGE_NAME = "rest"

#: The Gateway API token of the REST server (D32 section 9).
REST_TOKEN_NAME = "ignition-mcp-rest"
#: The ``created`` record entry for the REST server's Gateway API token (issue #76
#: review). ``reset`` deletes only what a record names, and a restored token was not
#: created by this run.
REST_TOKEN_RECORD = f"rest-token:{REST_TOKEN_NAME}"
REST_TOKEN_SECRET = "rest-gateway-token"
REST_TOKEN_DESCRIPTION = "ignition-mcp REST server credential (deployment-owned)."

#: The REST server's own Security Level (issue #79). The token holds only this level.
REST_LEVEL = "IgnitionMcpRest"
REST_LEVEL_PATH = (SECURITY_LEVEL_PARENT, REST_LEVEL)
REST_LEVEL_NAME = "/".join(REST_LEVEL_PATH)
REST_LEVEL_RECORD = f"rest-level:{REST_LEVEL_NAME}"
REST_LEVEL_DESCRIPTION = "ignition-mcp REST server Security Level (deployment-owned)."

SECURITY_PROPERTIES_PATH = "/data/api/v1/resources/singleton/ignition/security-properties"
#: The General Settings entries Native REST checks for an API token. Measured on
#: 8.3.8 for issue #79: ``readPermissions`` opens every read route the REST server
#: calls and ``writePermissions`` every write route, while ``accessPermissions``
#: alone opens nothing and is not needed.
READ_ENTRY = "readPermissions"
WRITE_ENTRY = "writePermissions"
PERMISSION_ENTRIES = (READ_ENTRY, WRITE_ENTRY)


def permission_entries(environment: str) -> tuple[str, ...]:
    """The General Settings entries the REST server's level passes in one environment."""

    return (READ_ENTRY,) if environment == "prod" else PERMISSION_ENTRIES


def permission_record(entry: str) -> str:
    """The ``created`` entry for one General Settings entry setup added the level to."""

    return f"rest-permission:{entry}"


def _level_node() -> dict[str, Any]:
    return {"name": REST_LEVEL, "description": REST_LEVEL_DESCRIPTION, "children": []}


def dedicated_grant() -> list[dict[str, Any]]:
    """The granted-levels tree of ``ignition-mcp-rest``: the dedicated level only."""

    return [{"name": SECURITY_LEVEL_PARENT, "children": [{"name": REST_LEVEL, "children": []}]}]

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

    def risks(self) -> list[str]:
        """The named risks these settings carry, as ``rest_accepted`` records them."""

        entries: list[str] = []
        if self.wildcard_targets and self.classes:
            enabled = ",".join(name for name in MUTATION_CLASSES if name in self.classes)
            entries.append(f"{Risk.WILDCARD_ALLOWLIST.value}:{enabled}")
        if "admin" in self.classes:
            entries.append(Risk.ADMIN_CLASS.value)
        return entries


#: The key in ``deployment.toml`` that records the accepted REST risks.
RECORD_KEY = "rest_accepted"


def recorded_risks(values: Mapping[str, Value]) -> list[str]:
    record = values.get(RECORD_KEY)
    return list(record) if isinstance(record, list) else []


def needed_for(entry: str) -> Needed:
    """The acceptance item behind one ``rest_accepted`` entry."""

    risk, _, classes = entry.partition(":")
    if risk == Risk.ADMIN_CLASS.value:
        return Needed(Risk.ADMIN_CLASS, "REST server")
    return Needed(Risk.WILDCARD_ALLOWLIST, f"REST {classes.upper().replace(',', ', ')} Mutations")


def _width(name: str, value: str) -> int:
    """How much a REST setting allows, to tell a narrowing from a widening."""

    if name == "rest_mutation_classes":
        return 0 if value == NONE else len(value.split(","))
    return int(value in (WILDCARD, "on"))


def _change_environment(ctx: engine.Context) -> list[str]:
    """Give each saved REST setting the new environment's default; return the plan lines.

    Only a value that came from the saved deployment changes. A flag or an answer of
    this run keeps its value. This changes the resolved values in memory only, so the
    stage's ``save`` writes them after the plan is confirmed.
    """

    before = ctx.deployment.values.get("environment")
    after = ctx.resolved.values.get("environment")
    if not isinstance(before, str) or before == after:
        return []
    lines: list[str] = []
    for spec in INPUTS:
        if ctx.resolved.sources.get(spec.name) is not Source.SAVED:
            continue
        old = ctx.resolved.values[spec.name]
        new = spec.default_for(ctx.resolved.values)
        if new is None or new == old:
            continue
        ctx.resolved.values[spec.name] = new
        ctx.resolved.sources[spec.name] = Source.DEFAULT
        verb = "narrow" if _width(spec.name, new) < _width(spec.name, old) else "widen"
        lines.append(f"{verb} {spec.flag.removeprefix('--')} from {old} to {new} (environment {before} to {after})")
    return lines


# ------------------------------------------------------------------------ plan


@dataclass(slots=True)
class RestPlan:
    """What :func:`plan` found, handed to :func:`apply`."""

    environment: str = "dev"
    #: Whether this run creates ``Authenticated/IgnitionMcpRest``.
    create_level: bool = False
    #: The General Settings entries this run adds the level to or removes it from.
    grant_entries: list[str] = field(default_factory=list)
    revoke_entries: list[str] = field(default_factory=list)
    #: ``""`` when the Gateway token and its file agree, else ``create``, ``recreate``
    #: or ``restore`` (the token was changed by hand, or still holds the setup key's level).
    token_action: str = ""
    #: The served ``ignition-mcp-rest`` document for ``recreate`` and ``restore``.
    served: dict[str, Any] | None = None
    #: The granted-levels tree of the dedicated level.
    grant: list[dict[str, Any]] = field(default_factory=list)
    #: The levels the token held before a move to the dedicated level, else ``""``.
    moved_from: str = ""
    secure_channel: bool = False
    #: Roles whose Named static token file must be created.
    new_static_tokens: list[str] = field(default_factory=list)
    save: bool = False
    #: The ``rest_accepted`` record to save, ``None`` when it is already right.
    record: list[str] | None = None


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


def _level_paths(grant: list[dict[str, Any]]) -> set[str]:
    return {path for path in _level_names(grant).split(", ") if path}


def token_drift(served: dict[str, Any] | None, grant: list[dict[str, Any]], secure_channel: bool) -> list[str]:
    """What differs between the served token's profile and the desired one."""

    config = served.get("config") if isinstance(served, dict) else None
    profile = config.get("profile") if isinstance(config, dict) else None
    if not isinstance(profile, dict):
        return ["profile"]
    drift: list[str] = []
    if profile.get("type") != security.BASIC_TOKEN_PROFILE:
        drift.append("profile type")
    served_grant = profile.get("securityLevels")
    if not isinstance(served_grant, list) or _level_paths(served_grant) != _level_paths(grant):
        drift.append("Security Levels")
    if profile.get("secureChannelRequired") is not secure_channel:
        drift.append("secureChannelRequired")
    return drift


def _recreate_flag(ctx: engine.Context) -> bool:
    return bool(getattr(ctx.args, "recreate_tokens", False))


def _recorded(values: Mapping[str, Value]) -> list[str]:
    saved = values.get(CREATED_KEY)
    return [str(item) for item in saved] if isinstance(saved, list) else []


def _properties_next(ctx: engine.Context) -> str:
    return f"curl -sS {ctx.resolved.values['gateway_url'].rstrip('/')}{SECURITY_PROPERTIES_PATH}"


def _plan_level(ctx: engine.Context, tree: list[dict[str, Any]] | None) -> bool:
    """Whether this run creates the dedicated level. A level of that name elsewhere is refused."""

    if tree is None:
        raise CliError(
            ErrorCode.STEP_FAILED,
            "the Gateway's Security Level tree is not readable, so the REST server's level cannot be checked",
            next_action=f"curl -sS {ctx.resolved.values['gateway_url'].rstrip('/')}{gw.SECURITY_LEVELS_PATH}",
        )
    found = security.find_level(tree, REST_LEVEL)
    if found is not None:
        path, node = found
        problem = security.level_shape_problem(node)
        if tuple(path) != REST_LEVEL_PATH or problem:
            raise CliError(
                ErrorCode.STEP_FAILED,
                f"a Security Level named {REST_LEVEL} exists at {'/'.join(path)}"
                f"{f' and {problem}' if problem else ''}; setup never modifies a level it did not create",
                next_action=f"{PROG} status --deployment {ctx.deployment.name}",
            )
        return False
    _, reason = security.with_child_level(tree, SECURITY_LEVEL_PARENT, _level_node())
    if reason:
        raise CliError(ErrorCode.STEP_FAILED, reason, next_action=f"{PROG} status --deployment {ctx.deployment.name}")
    return True


def _plan_entries(
    ctx: engine.Context, config: dict[str, Any] | None, environment: str
) -> tuple[list[str], list[str]]:
    """The General Settings entries to add the level to and to remove it from."""

    if config is None:
        raise CliError(
            ErrorCode.STEP_FAILED,
            "the Gateway's General Settings (security-properties) are not readable, so the REST server's "
            "access cannot be granted",
            next_action=_properties_next(ctx),
        )
    created = _recorded(ctx.deployment.values)
    grant: list[str] = []
    revoke: list[str] = []
    for entry in PERMISSION_ENTRIES:
        if entry in permission_entries(environment):
            changed, reason = security.permission_with_level(config.get(entry), entry, REST_LEVEL_PATH)
            target = grant
        elif permission_record(entry) in created:
            changed, reason = security.permission_without_level(config.get(entry), entry, REST_LEVEL_PATH)
            target = revoke
        else:
            continue
        if reason:
            raise CliError(ErrorCode.STEP_FAILED, reason + ". Nothing was written", next_action=_properties_next(ctx))
        if changed is not None:
            target.append(entry)
    return grant, revoke


def plan(ctx: engine.Context) -> engine.Plan:
    """Read the Gateway token, the level, General Settings, the secret files and the saved settings."""

    environment_changes = _change_environment(ctx)
    settings = RestSettings.from_values(ctx.resolved.values)
    roles = ctx.resolved.list("roles")
    environment = str(ctx.resolved.values.get("environment") or "dev")
    rest_plan = RestPlan(environment=environment)
    changes: list[str] = []
    needed: list[Needed] = []
    secure_channel = ctx.resolved.values["gateway_url"].startswith("https:")

    token_file = ctx.deployment.secret_path(REST_TOKEN_SECRET)
    observed = security.observe_secret_file(token_file)
    if observed.error:
        raise CliError(ErrorCode.SECRET_FILE_INVALID, f"{token_file}: {observed.error}")
    setup_key = ctx.resolved.secrets["gateway_token"].reveal().partition(":")[0]

    async def read() -> tuple[dict[str, Any] | None, ...]:
        async with ctx.gateway_reader() as reader:
            served = await reader.resource_document(API_TOKEN_TYPE, REST_TOKEN_NAME)
            setup = await reader.resource_document(API_TOKEN_TYPE, setup_key)
            levels = await reader.singleton_document(SECURITY_LEVELS_TYPE)
            properties = await reader.singleton_document(security.SECURITY_PROPERTIES_TYPE)
        return served, setup, levels, properties

    served, setup_document, levels_document, properties_document = asyncio.run(read())
    tree = security.level_tree(levels_document)
    rest_plan.create_level = _plan_level(ctx, tree)
    grant_entries, revoke_entries = _plan_entries(ctx, security.properties_config(properties_document), environment)
    rest_plan.grant_entries, rest_plan.revoke_entries = grant_entries, revoke_entries
    if (
        environment == "prod"
        and (rest_plan.create_level or grant_entries)
        and not getattr(ctx.args, "provision_security_levels", False)
    ):
        what = [f"create {REST_LEVEL_NAME}"] if rest_plan.create_level else []
        what += [f"add it to {', '.join(grant_entries)}"] if grant_entries else []
        raise CliError(
            ErrorCode.ACCEPTANCE_REQUIRED,
            f"prod changes Security Levels only when asked; the REST server needs setup to {' and '.join(what)}. "
            "Nothing was written",
            next_action=f"{PROG} setup --deployment {ctx.deployment.name} --provision-security-levels",
        )
    if rest_plan.create_level:
        changes.append(f"create the Security Level {REST_LEVEL_NAME} for the REST server's Gateway API token")
    access = "read access" if environment == "prod" else "read and write access"
    if grant_entries:
        changes.append(
            f"add {REST_LEVEL_NAME} to the General Settings entries {', '.join(grant_entries)} "
            f"(environment {environment}: {access} through Native REST)"
        )
    if revoke_entries:
        changes.append(
            f"remove {REST_LEVEL_NAME} from the General Settings entries {', '.join(revoke_entries)} "
            f"(environment {environment}: {access} only)"
        )

    rest_plan.grant = security.grant_tree(
        tree or [], list(REST_LEVEL_PATH), _level_node() if rest_plan.create_level else None
    ) or []
    rest_plan.secure_channel = secure_channel
    if served is not None and observed.exists:
        if not observed.hashes_to(security.stored_token_hash(served)):
            raise CliError(
                ErrorCode.SECRET_FILE_INVALID,
                f"{token_file} holds a key that does not match the Gateway token {REST_TOKEN_NAME}",
                next_action=f"rm {token_file} && {PROG} setup --deployment {ctx.deployment.name} --recreate-tokens",
            )
        drift = token_drift(served, rest_plan.grant, secure_channel)
        if drift:
            rest_plan.token_action = "restore"
            rest_plan.served = served
            held = _grant(served)
            setup_grant = _grant(setup_document)
            if drift == ["Security Levels"] and setup_grant and _level_paths(held) == _level_paths(setup_grant):
                # Phase 7 gave the token the setup key's level. Moving it is this
                # issue's own change, not an overwrite of someone else's edit.
                rest_plan.moved_from = _level_names(held)
                changes.append(
                    f"move the Gateway token {REST_TOKEN_NAME} from the setup key's level "
                    f"{rest_plan.moved_from} to {REST_LEVEL_NAME}"
                )
            else:
                changes.append(
                    f"restore the Gateway token {REST_TOKEN_NAME}, whose {', '.join(drift)} changed on the Gateway, "
                    f"to {REST_LEVEL_NAME} and secureChannelRequired {str(secure_channel).lower()}"
                )
                needed.append(
                    Needed(Risk.OVERWRITE_HAND_EDIT, f"Gateway token {REST_TOKEN_NAME}: {', '.join(drift)}")
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
        verb = "delete the Gateway token whose secret file is lost, then create" if served else "create"
        changes.append(
            f"{verb} the Gateway API token {REST_TOKEN_NAME} granted {REST_LEVEL_NAME} only; "
            f"its secret goes to {token_file}"
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
        changes.extend(environment_changes or [f"save the REST settings: {settings.describe()}"])

    # A risky value needs acceptance until a run that accepted it recorded it, so a
    # hand edit of deployment.toml cannot skip the question.
    risks = settings.risks()
    record = recorded_risks(ctx.deployment.values)
    needed.extend(needed_for(entry) for entry in risks if entry not in record)
    if risks != record:
        rest_plan.record = risks
        changes.append("record the accepted REST risks: " + (", ".join(risks) or "none"))
    return engine.Plan(changes=changes, needed=needed, data=rest_plan)


# ----------------------------------------------------------------------- apply


def _failed(ctx: engine.Context, message: str) -> CliError:
    return CliError(ErrorCode.STEP_FAILED, message, next_action=f"{PROG} status --deployment {ctx.deployment.name}")


def forget_created(ctx: engine.ApplyContext, *entries: str) -> None:
    """Drop entries from the ``created`` record once the Gateway no longer holds them."""

    known = _recorded(ctx.deployment.values)
    keep = [item for item in known if item not in entries]
    if keep != known:
        ctx.save({CREATED_KEY: keep})


def apply(ctx: engine.ApplyContext, stage_plan: engine.Plan) -> None:
    rest_plan: RestPlan = stage_plan.data
    token_file = ctx.deployment.secret_path(REST_TOKEN_SECRET)
    with ctx.reporter.step("rest level", REST_LEVEL_NAME) as end:
        if rest_plan.create_level:
            asyncio.run(add_rest_level(ctx, ctx.gateway_writer()))
            ctx.record_created(REST_LEVEL_RECORD)
            end.set(Status.CHANGED, f"created {REST_LEVEL_NAME} under {SECURITY_LEVEL_PARENT}")
        else:
            end.set(Status.OK, f"{REST_LEVEL_NAME} exists as a leaf under {SECURITY_LEVEL_PARENT}")

    entries = permission_entries(rest_plan.environment)
    with ctx.reporter.step("rest general settings", f"{REST_LEVEL_NAME} in {', '.join(entries)}") as end:
        stale = [permission_record(entry) for entry in PERMISSION_ENTRIES if entry not in entries]
        if rest_plan.grant_entries or rest_plan.revoke_entries:
            done = asyncio.run(
                edit_general_settings(ctx, ctx.gateway_writer(), rest_plan.grant_entries, rest_plan.revoke_entries)
            )
            ctx.record_created(*(permission_record(entry) for entry in rest_plan.grant_entries))
            forget_created(ctx, *stale)
            end.set(Status.CHANGED, f"{'; '.join(done)} (environment {rest_plan.environment})")
        else:
            forget_created(ctx, *stale)
            end.set(Status.OK, f"{REST_LEVEL_NAME} passes {', '.join(entries)} (environment {rest_plan.environment})")

    with ctx.reporter.step(f"rest token {REST_TOKEN_NAME}", "the REST server's own Gateway API token") as end:
        if not rest_plan.token_action:
            end.set(Status.OK, f"granted {REST_LEVEL_NAME} only; its key matches {token_file}")
        elif rest_plan.token_action == "restore":
            asyncio.run(_restore_rest_token(rest_plan, ctx.gateway_writer()))
            if rest_plan.moved_from:
                reason = f"moved from the setup key's level {rest_plan.moved_from} to {REST_LEVEL_NAME}"
            else:
                reason = f"restored {REST_LEVEL_NAME} and secureChannelRequired {str(rest_plan.secure_channel).lower()}"
            end.set(Status.CHANGED, f"{reason}; the key is unchanged")
        else:
            asyncio.run(_create_rest_token(ctx, rest_plan, ctx.gateway_writer()))
            ctx.record_created(REST_TOKEN_RECORD)
            end.set(
                Status.CHANGED,
                f"{'recreated' if rest_plan.token_action == 'recreate' else 'created'} with {REST_LEVEL_NAME} "
                f"as its only level; its secret was written to {token_file} with mode 0600",
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
        if rest_plan.save or rest_plan.record is not None:
            ctx.save({RECORD_KEY: settings.risks()})
            end.set(Status.CHANGED, f"{settings.describe()}; accepted risks recorded")
        else:
            end.set(Status.OK, settings.describe())


async def _read_levels(ctx: engine.Context, writer: GatewayWriter) -> tuple[list[dict[str, Any]], str, str]:
    """The Security Level tree and its signature, read right before a write.

    The Runtime stage may have restarted the Gateway and edited the tree after this
    stage's plan read it, so the plan's signature is never reused.
    """

    current = await writer.reads.singleton_document(SECURITY_LEVELS_TYPE)
    tree = security.level_tree(current)
    signature = str((current or {}).get("signature") or "")
    if tree is None or not signature:
        raise _failed(ctx, "the Security Level tree is not readable with a signature, so it was not edited")
    return tree, signature, security.singleton_collection(current)


async def _write_levels(
    ctx: engine.Context, writer: GatewayWriter, before: list[dict[str, Any]], tree: list[dict[str, Any]],
    signature: str, collection: str, want: bool,
) -> None:
    await writer.update_security_levels(tree, signature=signature, collection=collection)
    served = security.level_tree(await writer.reads.singleton_document(SECURITY_LEVELS_TYPE))
    expected = security.level_paths(before)
    expected = expected | {REST_LEVEL_PATH} if want else expected - {REST_LEVEL_PATH}
    if served is None or security.level_paths(served) != expected:
        raise _failed(ctx, "the Security Level edit was accepted but the served tree is not the one written")


async def add_rest_level(ctx: engine.Context, writer: GatewayWriter) -> None:
    """Create ``Authenticated/IgnitionMcpRest`` with D20's procedure: read, add one leaf, write, read back."""

    async with writer:
        try:
            tree, signature, collection = await _read_levels(ctx, writer)
            if security.find_level(tree, REST_LEVEL) is not None:
                raise _failed(ctx, f"a level named {REST_LEVEL} appeared on the Gateway after the plan read the tree")
            merged, reason = security.with_child_level(tree, SECURITY_LEVEL_PARENT, _level_node())
            if merged is None:
                raise _failed(ctx, reason)
            await _write_levels(ctx, writer, tree, merged, signature, collection, True)
        except (WriteError, gw.GatewayProbeError) as error:
            raise _failed(ctx, f"the Security Level edit stopped: {error}") from error


async def remove_rest_level(ctx: engine.Context, writer: GatewayWriter) -> bool:
    """Remove ``Authenticated/IgnitionMcpRest`` with D20's procedure. ``False`` when it is already gone."""

    async with writer:
        try:
            tree, signature, collection = await _read_levels(ctx, writer)
            found = security.find_level(tree, REST_LEVEL)
            if found is None:
                return False
            if tuple(found[0]) != REST_LEVEL_PATH or security.level_shape_problem(found[1]):
                raise _failed(ctx, f"{REST_LEVEL} no longer has the shape setup created, so it was left in place")
            reduced = security.without_child_level(tree, SECURITY_LEVEL_PARENT, REST_LEVEL)
            await _write_levels(ctx, writer, tree, reduced, signature, collection, False)
        except (WriteError, gw.GatewayProbeError) as error:
            raise _failed(ctx, f"the Security Level edit stopped: {error}") from error
    return True


async def edit_general_settings(
    ctx: engine.Context, writer: GatewayWriter, grant: list[str], revoke: list[str]
) -> list[str]:
    """Add the level to ``grant`` and remove it from ``revoke`` in one D20 edit; return what changed.

    The singleton is read right before the write and its signature is the
    precondition. The read-back must equal the written config exactly, so an entry
    this stage did not mean to change cannot have moved.
    """

    done: list[str] = []
    async with writer:
        try:
            current = await writer.reads.singleton_document(security.SECURITY_PROPERTIES_TYPE)
            config = security.properties_config(current)
            signature = str((current or {}).get("signature") or "")
            if config is None or not signature:
                raise _failed(ctx, "the General Settings are not readable with a signature, so they were not edited")
            desired = dict(config)
            for entries, change, verb in (
                (grant, security.permission_with_level, "added to"),
                (revoke, security.permission_without_level, "removed from"),
            ):
                changed_here: list[str] = []
                for entry in entries:
                    changed, reason = change(config.get(entry), entry, REST_LEVEL_PATH)
                    if reason:
                        raise _failed(ctx, f"{reason}. Nothing was written")
                    if changed is not None:
                        desired[entry] = changed
                        changed_here.append(entry)
                if changed_here:
                    done.append(f"{REST_LEVEL_NAME} {verb} {', '.join(changed_here)}")
            if desired == config:
                return [f"{REST_LEVEL_NAME} already had the planned General Settings entries"]
            item = {"collection": security.singleton_collection(current), "signature": signature, "config": desired}
            await writer._write(
                "PUT",
                RESOURCE_COLLECTION_PATH.format(resource_type=security.SECURITY_PROPERTIES_TYPE),
                body=json.dumps([item], separators=(",", ":")).encode("utf-8"),
                content_type="application/json",
                action="modify General Settings permissions",
            )
            served = security.properties_config(
                await writer.reads.singleton_document(security.SECURITY_PROPERTIES_TYPE)
            )
        except (WriteError, gw.GatewayProbeError) as error:
            raise _failed(ctx, f"the General Settings edit stopped: {error}") from error
    if served != desired:
        moved = sorted(key for key in {*desired, *(served or {})} if (served or {}).get(key) != desired.get(key))
        raise _failed(ctx, f"the General Settings edit was accepted but {', '.join(moved)} read back differently")
    return done


async def _restore_rest_token(rest_plan: RestPlan, writer: GatewayWriter) -> None:
    """Write the desired profile over a hand-edited ``ignition-mcp-rest``; the key stays."""

    served = rest_plan.served or {}
    signature = served.get("signature")
    if not isinstance(signature, str) or not signature:
        raise CliError(ErrorCode.STEP_FAILED, f"{REST_TOKEN_NAME} reads back no signature; not restored")
    stored_hash = security.stored_token_hash(served)
    config = {
        "profile": {
            "type": security.BASIC_TOKEN_PROFILE,
            "secureChannelRequired": rest_plan.secure_channel,
            "securityLevels": rest_plan.grant,
            "timestamp": int(time.time() * 1000),
        },
        "settings": {"tokenHash": stored_hash},
    }
    async with writer:
        item = writer._api_token_change(REST_TOKEN_NAME, config, REST_TOKEN_DESCRIPTION)
        item["collection"] = str(served.get("collection") or CONFIG_COLLECTION)
        item["signature"] = signature
        await writer._write(
            "PUT",
            RESOURCE_COLLECTION_PATH.format(resource_type=API_TOKEN_TYPE),
            body=json.dumps([item], separators=(",", ":")).encode("utf-8"),
            content_type="application/json",
            action=f"restore API token {REST_TOKEN_NAME}",
        )
        after = await writer.reads.resource_document(API_TOKEN_TYPE, REST_TOKEN_NAME)
    if security.stored_token_hash(after) != stored_hash or token_drift(after, rest_plan.grant, rest_plan.secure_channel):
        raise CliError(
            ErrorCode.STEP_FAILED,
            f"{REST_TOKEN_NAME} was written but does not read back as restored",
        )


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
