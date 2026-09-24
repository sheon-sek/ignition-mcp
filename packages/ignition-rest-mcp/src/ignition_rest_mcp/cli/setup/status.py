"""``ignition-mcp status``: the read-only report (D32 sections 2, 4 and 10, issue #76).

One line per check, in this order:

* the deployment directory, its environment and its roles;
* the Gateway: reachability, version, and every managed resource read in one pass;
* the MCP Module build against the pinned one;
* the managed bundle project against the bundle the checkout builds;
* per role: its Security Level, its API token and local secret, its Server Config and
  the closing check at its endpoint with its own token;
* the Runtime Target Policy served by the reserved Tag provider;
* the REST plane: the ``ignition-mcp-rest`` token, the roles' Named static tokens,
  and the settings ``start`` derives;
* whether the Gateway's Named Query registry variable is set, read through
  ``database_query_list`` at one role's endpoint;
* files left in the deployment directory for a role the deployment no longer serves.

Every check reads. Nothing here writes, fixes or deletes: D32 section 10's lost-secret
and hand-edit cases are reported with the command that repairs them.

A check whose state cannot be read is ``SKIPPED``. The first read failure is the
``gateway`` line's ``FAILED`` reason, and every later check that needs that state is
skipped with the same reason, so one cause never becomes a line per check. The Gateway
reads, the closing check and the endpoint call come from
:mod:`ignition_rest_mcp.cli.setup.runtime` and :mod:`ignition_rest_mcp.cli.setup.rest`,
so tests replace ``runtime.SETTINGS`` exactly as the Runtime stage's tests do.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ignition_rest_mcp.cli.engine import main as engine
from ignition_rest_mcp.cli.engine.deployment import DEPLOYMENT_FILE, Deployment
from ignition_rest_mcp.cli.engine.errors import CliError, ErrorCode
from ignition_rest_mcp.cli.engine.report import Status
from ignition_rest_mcp.cli.engine.resolve import PROG
from ignition_rest_mcp.cli.setup import rest, runtime, start
from ignition_rest_mcp.cli.setup.runtime import Bundle, Role, RuntimeTargets
from ignition_rest_mcp.cli.setup_native import documents as docs
from ignition_rest_mcp.cli.setup_native import gateway as gw
from ignition_rest_mcp.cli.setup_native import security
from ignition_rest_mcp.cli.setup_native.inputs import API_TOKEN_TYPE, SECURITY_LEVEL_PARENT
from ignition_rest_mcp.cli.setup_native.mcp_http import McpHttpClient, McpProbeError

#: The Runtime Tool whose answer says whether the Gateway's Named Query registry
#: variable is set (D32 section 12).
QUERY_LIST_TOOL = "database_query_list"


class _SkippedCheck(Exception):
    """A check that cannot be judged because the state it reads is not available."""


@dataclass(frozen=True, slots=True)
class Observation:
    """Every Gateway read ``status`` reasons over, taken in one pass."""

    url: str
    version: str
    environment: str
    bundle: Bundle | None
    #: The checkout the bundle is read from, when one was found.
    checkout: Path | None
    #: Why the checkout's bundle cannot be read, when that is the case.
    checkout_error: str
    targets: RuntimeTargets | None
    installed: gw.ModuleIdentity | None
    project: gw.ProjectState
    levels: dict[str, Any] | None
    tokens: dict[str, dict[str, Any] | None]
    configs: dict[str, dict[str, Any] | None]
    policy: docs.PolicyObservation
    rest_token: dict[str, Any] | None
    setup_key: str
    setup_document: dict[str, Any] | None

    @property
    def runtime_plain_http(self) -> bool:
        """The Runtime token rule: a plain ``http`` Gateway in ``dev`` needs no TLS."""

        return self.url.startswith("http:") and self.environment == "dev"

    @property
    def rest_requires_secure_channel(self) -> bool:
        """The REST token rule: only an ``https`` Gateway URL needs a secure channel."""

        return self.url.startswith("https:")

    @property
    def policy_text(self) -> str | None:
        """The policy document this deployment generates, or ``None`` without a checkout."""

        if self.bundle is None:
            return None
        return runtime.policy_document(self.bundle, self.environment)


def _environment(deployment: Deployment) -> str:
    saved = deployment.values.get("environment")
    return saved if isinstance(saved, str) and saved else "dev"


def _setup_next(ctx: engine.Context) -> str:
    return f"{PROG} setup --deployment {ctx.deployment.name}"


def _setup_yes(ctx: engine.Context) -> str:
    return f"{_setup_next(ctx)} --yes"


def _recreate(ctx: engine.Context) -> str:
    return f"{_setup_next(ctx)} --recreate-tokens --yes"


# ------------------------------------------------------------------ the Gateway read


async def _read(ctx: engine.Context, roles: list[Role]) -> Observation:
    deployment = ctx.deployment
    url = str(ctx.resolved.values["gateway_url"]).rstrip("/")
    installed, project, levels, tokens, configs, policy = await runtime._observe(ctx, roles)
    setup_key = ctx.resolved.secrets["gateway_token"].reveal().partition(":")[0]
    try:
        async with ctx.gateway_reader(runtime.SETTINGS.transport) as client:
            info = await client.gateway_info()
            rest_token = await client.resource_document(API_TOKEN_TYPE, rest.REST_TOKEN_NAME)
            setup_document = await client.resource_document(API_TOKEN_TYPE, setup_key)
    except gw.GatewayProbeError as error:
        raise CliError(
            ErrorCode.STEP_FAILED,
            f"the Gateway's state could not be read ({error})",
            next_action=f"curl -sSI {url}{gw.GATEWAY_INFO_PATH}",
        ) from error
    checkout = runtime.find_checkout()
    bundle: Bundle | None = None
    checkout_error = ""
    if checkout is not None:
        try:
            bundle = runtime.read_bundle(checkout)
        except CliError as error:
            checkout_error = error.message
    environment = _environment(deployment)
    targets = (
        None
        if bundle is None
        else RuntimeTargets(
            bundle=bundle,
            endpoint=runtime._endpoint_of(url),
            insecure_channel=url.startswith("http:") and environment == "dev",
        )
    )
    return Observation(
        url=url,
        version=str(info.get("ignitionVersion") or "an unknown version"),
        environment=environment,
        bundle=bundle,
        checkout=checkout,
        checkout_error=checkout_error,
        targets=targets,
        installed=installed,
        project=project,
        levels=levels,
        tokens=tokens,
        configs=configs,
        policy=policy,
        rest_token=rest_token,
        setup_key=setup_key,
        setup_document=setup_document,
    )


class _Facts:
    """The one Gateway read, taken when the first check needs it.

    The first failure is a :class:`CliError`, reported by the check that triggered it.
    Every later check gets :class:`_SkippedCheck` instead, so one cause never becomes
    a line per check.
    """

    def __init__(self, ctx: engine.Context, roles: list[Role]) -> None:
        self._ctx = ctx
        self._roles = roles
        self._value: Observation | None = None
        self._error: CliError | None = None

    def get(self) -> Observation:
        if self._value is not None:
            return self._value
        if self._error is not None:
            raise _SkippedCheck(self._error.message)
        try:
            self._value = asyncio.run(_read(self._ctx, self._roles))
        except CliError as error:
            self._error = error
            raise
        return self._value


# ----------------------------------------------------------------------- checks


def _deployment_reason(ctx: engine.Context, roles: list[Role]) -> str:
    deployment = ctx.deployment
    if not deployment.exists:
        raise CliError(
            ErrorCode.DEPLOYMENT_UNREADABLE,
            f"{deployment.directory} has no {DEPLOYMENT_FILE}",
            next_action=_setup_next(ctx),
        )
    if not roles:
        raise CliError(
            ErrorCode.DEPLOYMENT_UNREADABLE,
            f"{deployment.directory} names no Assistant role, so setup has not finished here",
            next_action=_setup_next(ctx),
        )
    return (
        f"{deployment.directory}, environment {_environment(deployment)}, "
        f"roles {', '.join(role.name for role in roles)}"
    )


def _gateway_reason(facts: Observation) -> str:
    return f"{facts.url} answers {facts.version}"


def _module_reason(ctx: engine.Context, facts: Observation) -> str:
    installed = facts.installed
    if installed is None:
        raise CliError(
            ErrorCode.STEP_FAILED, "the Gateway has no MCP Module installed", next_action=_setup_next(ctx)
        )
    build = installed.build or installed.raw_version
    if installed.build != runtime.PINNED_MODULE_BUILD:
        raise CliError(
            ErrorCode.STEP_FAILED,
            f"the Gateway runs MCP Module build {build}; this CLI installs the pinned build "
            f"{runtime.PINNED_MODULE_BUILD}",
            next_action=_setup_next(ctx),
        )
    return f"build {build} is installed, the pinned build"


def _bundle_reason(ctx: engine.Context, facts: Observation) -> str:
    state = facts.project
    if state.classification == gw.ABSENT:
        raise CliError(
            ErrorCode.STEP_FAILED,
            f"the Gateway serves no project named {runtime.PROJECT}",
            next_action=_setup_next(ctx),
        )
    if state.classification != gw.MANAGED:
        detail = "no ownership marker" if state.classification == gw.UNMANAGED_SAME_NAME else "an invalid marker"
        raise CliError(
            ErrorCode.STEP_FAILED,
            f"the Gateway has a project named {runtime.PROJECT} with {detail}, so it is not one setup deployed",
            next_action=f"curl -sS {facts.url}{gw.PROJECT_FIND_PATH.format(name=runtime.PROJECT)}",
        )
    if facts.bundle is None:
        if facts.checkout_error:
            raise CliError(
                ErrorCode.STEP_FAILED,
                f"the deployed bundle cannot be compared with the checkout: {facts.checkout_error}",
                next_action=f"git -C {facts.checkout} status",
            )
        return f"managed bundle {state.bundle_version} is deployed; no checkout was found to compare it"
    if state.bundle_version != facts.bundle.version:
        raise CliError(
            ErrorCode.STEP_FAILED,
            f"the Gateway serves managed bundle {state.bundle_version}; the checkout builds {facts.bundle.version}",
            next_action=_setup_next(ctx),
        )
    return f"managed bundle {state.bundle_version} is deployed and matches the checkout"


def _level_reason(ctx: engine.Context, facts: Observation, role: Role) -> str:
    tree = security.level_tree(facts.levels)
    if tree is None:
        raise CliError(
            ErrorCode.STEP_FAILED,
            "the Gateway's Security Level tree is not readable, so nothing can be compared with it",
        )
    found = security.find_level(tree, role.level)
    if found is None:
        raise CliError(
            ErrorCode.STEP_FAILED,
            f"{role.level_path} is missing from the Gateway's Security Levels",
            next_action=_setup_next(ctx),
        )
    path, node = found
    if path != [SECURITY_LEVEL_PARENT, role.level]:
        raise CliError(
            ErrorCode.STEP_FAILED,
            f"a Security Level named {role.level} sits at {'/'.join(path)}, not {role.level_path}",
            next_action=_setup_next(ctx),
        )
    problem = security.level_shape_problem(node)
    if problem:
        raise CliError(ErrorCode.STEP_FAILED, f"{role.level_path} {problem}", next_action=_setup_next(ctx))
    return f"{role.level_path} exists as a leaf"


def _token_reason(ctx: engine.Context, facts: Observation, role: Role) -> str:
    path = ctx.deployment.secret_path(role.secret)
    secret = security.observe_secret_file(path)
    if secret.error:
        raise CliError(ErrorCode.SECRET_FILE_INVALID, f"{path}: {secret.error}", next_action=f"chmod 600 {path}")
    document = facts.tokens.get(role.name)
    if document is None:
        if secret.exists:
            raise CliError(
                ErrorCode.STEP_FAILED,
                f"the Gateway has no API token {role.token} while {path} holds a secret",
                next_action=_setup_next(ctx),
            )
        raise CliError(
            ErrorCode.STEP_FAILED,
            f"the Gateway has no API token {role.token} and {path} does not exist",
            next_action=_setup_next(ctx),
        )
    if not secret.exists:
        raise CliError(
            ErrorCode.STEP_FAILED,
            f"the Gateway has the API token {role.token} but its secret file {path} is lost; setup deletes "
            "the token and creates a new one",
            next_action=_recreate(ctx),
        )
    if secret.name != role.token or not secret.hashes_to(security.stored_token_hash(document)):
        raise CliError(
            ErrorCode.STEP_FAILED,
            f"{path} holds another credential, not the key of the API token {role.token}",
            next_action=_recreate(ctx),
        )
    drift = runtime._token_drift(role, document, insecure_channel=facts.runtime_plain_http)
    if drift:
        raise CliError(
            ErrorCode.STEP_FAILED,
            f"the API token {role.token} was changed on the Gateway: {drift}",
            next_action=_setup_yes(ctx),
        )
    return f"grants {role.level_path}; its secret is in {path}"


def _config_reason(ctx: engine.Context, facts: Observation, role: Role) -> str:
    document = facts.configs.get(role.name)
    if document is None:
        raise CliError(
            ErrorCode.STEP_FAILED,
            f"the Gateway serves no Server Config {role.server_config}",
            next_action=_setup_next(ctx),
        )
    config = document.get("config")
    held = config if isinstance(config, dict) else {}
    if document.get("enabled") is False:
        raise CliError(
            ErrorCode.STEP_FAILED,
            f"the Server Config {role.server_config} is disabled",
            next_action=_setup_yes(ctx),
        )
    if held.get("permissions") != role.permissions():
        raise CliError(
            ErrorCode.STEP_FAILED,
            f"the Server Config {role.server_config} was changed by hand: its permissions tree differs from "
            f"the one setup generates from {role.level_path}",
            next_action=_setup_yes(ctx),
        )
    if facts.bundle is None:
        return f"{role.server_config} is enabled with the permissions tree of {role.level_path}"
    tools = facts.bundle.tools(role)
    observed, note = docs.observed_tools(document, runtime.PROJECT)
    if observed is None or sorted(observed) != sorted(tools):
        drift = note or runtime._tool_drift(observed or [], tools)
        raise CliError(
            ErrorCode.STEP_FAILED,
            f"the Server Config {role.server_config} was changed by hand: its Tool list {drift}",
            next_action=_setup_yes(ctx),
        )
    if held.get("version") != facts.bundle.version:
        raise CliError(
            ErrorCode.STEP_FAILED,
            f"the Server Config {role.server_config} names bundle {held.get('version')}, not {facts.bundle.version}",
            next_action=_setup_yes(ctx),
        )
    return f"{len(observed)} Tools of the {role.profile} profile and the permissions tree of {role.level_path}"


def _endpoint_reason(ctx: engine.Context, facts: Observation, role: Role) -> str:
    path = ctx.deployment.secret_path(role.secret)
    secret = security.observe_secret_file(path)
    if not secret.exists:
        raise _SkippedCheck(f"{path} holds no usable secret, so the {role.name} endpoint cannot be called")
    if facts.targets is None:
        raise _SkippedCheck("no checkout was found, so the endpoint's inventories cannot be compared")
    token = security.token_secret(secret.name, secret.key)
    try:
        return asyncio.run(runtime.closing_check(ctx, facts.targets, role, token))
    except CliError as error:
        raise CliError(ErrorCode.STEP_FAILED, error.message, next_action=_setup_yes(ctx)) from error


def _policy_reason(ctx: engine.Context, facts: Observation) -> str:
    policy = facts.policy
    if policy.error:
        raise CliError(
            ErrorCode.STEP_FAILED,
            f"the Runtime Target Policy could not be read ({policy.error})",
            next_action=_setup_next(ctx),
        )
    if not policy.provider_present:
        raise CliError(
            ErrorCode.STEP_FAILED,
            f"the Gateway serves no {docs.POLICY_PATH} tag provider",
            next_action=_setup_next(ctx),
        )
    desired = facts.policy_text
    if desired is None:
        return f"{docs.POLICY_PATH} is served; no checkout was found to compare it"
    if not policy.matches(desired):
        raise CliError(
            ErrorCode.STEP_FAILED,
            "the served Runtime Target Policy is not the one setup wrote, so someone changed it by hand",
            next_action=_setup_yes(ctx),
        )
    count = len(facts.bundle.mutation_tools) if facts.bundle is not None else 0
    summary = f"'*' for {count} Runtime Mutation Tools" if facts.environment == "dev" else "empty allowlists"
    return f"the served document is the generated {facts.environment} one ({summary})"


def _rest_token_reason(ctx: engine.Context, facts: Observation) -> str:
    path = ctx.deployment.secret_path(rest.REST_TOKEN_SECRET)
    secret = security.observe_secret_file(path)
    if secret.error:
        raise CliError(ErrorCode.SECRET_FILE_INVALID, f"{path}: {secret.error}", next_action=f"chmod 600 {path}")
    document = facts.rest_token
    if document is None:
        if secret.exists:
            raise CliError(
                ErrorCode.STEP_FAILED,
                f"the Gateway has no API token {rest.REST_TOKEN_NAME} while {path} holds a secret",
                next_action=_setup_next(ctx),
            )
        raise CliError(
            ErrorCode.STEP_FAILED,
            f"the Gateway has no API token {rest.REST_TOKEN_NAME} and {path} does not exist",
            next_action=_setup_next(ctx),
        )
    if not secret.exists:
        raise CliError(
            ErrorCode.STEP_FAILED,
            f"the Gateway has the API token {rest.REST_TOKEN_NAME} but its secret file {path} is lost; setup "
            "deletes the token and creates a new one",
            next_action=_recreate(ctx),
        )
    if not secret.hashes_to(security.stored_token_hash(document)):
        raise CliError(
            ErrorCode.STEP_FAILED,
            f"{path} holds a key that does not match the API token {rest.REST_TOKEN_NAME}",
            next_action=_recreate(ctx),
        )
    grant = rest._grant(facts.setup_document)
    if not grant:
        raise CliError(
            ErrorCode.STEP_FAILED,
            f"the setup key {facts.setup_key} reads back no Security Level grant to compare against",
        )
    drift = rest.token_drift(document, grant, facts.rest_requires_secure_channel)
    if drift:
        raise CliError(
            ErrorCode.STEP_FAILED,
            f"the API token {rest.REST_TOKEN_NAME} was changed on the Gateway: {', '.join(drift)}",
            next_action=_setup_yes(ctx),
        )
    return (
        f"grants {rest._level_names(grant)} with secureChannelRequired "
        f"{str(facts.rest_requires_secure_channel).lower()}; its secret is in {path}"
    )


def _rest_static_reason(ctx: engine.Context, roles: list[Role]) -> str:
    if not roles:
        raise _SkippedCheck("the deployment serves no role, so it needs no Named static token")
    missing: list[str] = []
    for role in roles:
        name = rest.ROLE_TOKEN_NAMES[role.name]
        path = ctx.deployment.secret_path(rest.static_token_secret(role.name))
        state = security.observe_secret_file(path)
        if state.error:
            raise CliError(ErrorCode.SECRET_FILE_INVALID, f"{path}: {state.error}", next_action=f"chmod 600 {path}")
        if not state.exists:
            missing.append(f"{name} ({path})")
        elif state.name != name:
            raise CliError(
                ErrorCode.STEP_FAILED,
                f"{path} names {state.name}, not the Named static token {name}",
                next_action=_recreate(ctx),
            )
    if missing:
        raise CliError(
            ErrorCode.STEP_FAILED,
            "the deployment holds no secret for " + ", ".join(missing),
            next_action=_setup_next(ctx),
        )
    return f"{len(roles)} Named static token(s): " + ", ".join(rest.ROLE_TOKEN_NAMES[role.name] for role in roles)


def _rest_settings_reason(ctx: engine.Context, roles: list[Role]) -> str:
    try:
        settings = rest.RestSettings.from_values(ctx.deployment.values)
    except CliError as error:
        raise CliError(error.code, error.message, next_action=_setup_next(ctx)) from error
    host, port = start.parse_bind(start.DEFAULT_BIND)
    start.settings_from(start.server_environment(ctx.deployment, host, port))
    record = rest.recorded_risks(ctx.deployment.values)
    unrecorded = [entry for entry in settings.risks() if entry not in record]
    if unrecorded:
        raise CliError(
            ErrorCode.STEP_FAILED,
            f"{', '.join(unrecorded)} is active with no record that setup accepted it, so start asks for "
            "acceptance again",
            next_action=f"{PROG} start --deployment {ctx.deployment.name} --yes",
        )
    return f"{settings.describe()}; the REST server accepts the settings start derives for it"


def _named_query_reason(ctx: engine.Context, roles: list[Role]) -> str:
    if not roles:
        raise _SkippedCheck("the deployment serves no role, so no Runtime endpoint can be asked")
    role = roles[0]
    path = ctx.deployment.secret_path(role.secret)
    secret = security.observe_secret_file(path)
    if not secret.exists:
        raise _SkippedCheck(f"{path} holds no usable secret, so {QUERY_LIST_TOOL} cannot be called")
    endpoint = runtime._endpoint_of(
        str(ctx.resolved.values["gateway_url"]).rstrip("/"), f"/data/mcp/{role.server_config}"
    )

    async def call() -> dict[str, Any]:
        async with McpHttpClient(
            endpoint, security.token_secret(secret.name, secret.key), transport=runtime.SETTINGS.transport
        ) as client:
            result = await client.tool_call(QUERY_LIST_TOOL, {})
        return result.structured

    try:
        structured = asyncio.run(call())
    except McpProbeError as error:
        raise CliError(
            ErrorCode.STEP_FAILED,
            f"{role.name}'s endpoint did not answer {QUERY_LIST_TOOL} ({error})",
            next_action=_setup_yes(ctx),
        ) from error
    entries = structured.get("entries")
    count = len(entries) if isinstance(entries, list) else 0
    if count:
        return f"the Gateway's registry variable is set: {QUERY_LIST_TOOL} lists {count} approved alias(es)"
    return f"the Gateway's registry variable is not set, so {QUERY_LIST_TOOL} lists no query"


def _leftover_reason(ctx: engine.Context, roles: list[Role]) -> str:
    served = {role.name for role in roles}
    left: list[str] = []
    for name, role in runtime.ROLES.items():
        if name in served:
            continue
        for file in (
            f"{role.secret}.secret",
            runtime.PERMISSIONS_FILE.format(role=name),
            f"{rest.static_token_secret(name)}.secret",
        ):
            path = ctx.deployment.directory / file
            if path.exists() or path.is_symlink():
                left.append(str(path))
    if left:
        raise CliError(
            ErrorCode.STEP_FAILED,
            "a role this deployment no longer serves has files here: " + ", ".join(left),
            next_action="rm " + " ".join(left),
        )
    return "no file is left for a role the deployment does not serve"


# ---------------------------------------------------------------------- the run


@dataclass(frozen=True, slots=True)
class Check:
    """One ``status`` line."""

    name: str
    what: str
    read: Callable[[], str]


def _role_checks(ctx: engine.Context, facts: _Facts, role: Role) -> list[Check]:
    """The four checks that report one role's own state."""

    def level() -> str:
        return _level_reason(ctx, facts.get(), role)

    def token() -> str:
        return _token_reason(ctx, facts.get(), role)

    def config() -> str:
        return _config_reason(ctx, facts.get(), role)

    def endpoint() -> str:
        return _endpoint_reason(ctx, facts.get(), role)

    url = str(ctx.resolved.values["gateway_url"]).rstrip("/")
    return [
        Check(f"level {role.name}", role.level_path, level),
        Check(f"token {role.name}", role.token, token),
        Check(f"server config {role.name}", role.server_config, config),
        Check(f"endpoint {role.name}", f"{url}/data/mcp/{role.server_config}", endpoint),
    ]


def _checks(ctx: engine.Context, roles: list[Role], facts: _Facts) -> list[Check]:
    """Every check, in report order. Each reads the Gateway's state when it runs."""

    url = str(ctx.resolved.values["gateway_url"]).rstrip("/")
    checks = [
        Check("deployment", str(ctx.deployment.directory), lambda: _deployment_reason(ctx, roles)),
        Check("gateway", f"GET {url}{gw.GATEWAY_INFO_PATH}", lambda: _gateway_reason(facts.get())),
        Check("module", f"MCP Module build {runtime.PINNED_MODULE_BUILD}",
              lambda: _module_reason(ctx, facts.get())),
        Check("bundle", runtime.PROJECT, lambda: _bundle_reason(ctx, facts.get())),
    ]
    for role in roles:
        checks.extend(_role_checks(ctx, facts, role))
    checks.extend(
        [
            Check("runtime policy", docs.POLICY_PATH, lambda: _policy_reason(ctx, facts.get())),
            Check("rest token", rest.REST_TOKEN_NAME, lambda: _rest_token_reason(ctx, facts.get())),
            Check("rest static tokens", "the roles' Named static tokens", lambda: _rest_static_reason(ctx, roles)),
            Check("rest settings", "the settings start derives", lambda: _rest_settings_reason(ctx, roles)),
            Check("named-query registry", QUERY_LIST_TOOL, lambda: _named_query_reason(ctx, roles)),
            Check("leftover files", str(ctx.deployment.directory), lambda: _leftover_reason(ctx, roles)),
        ]
    )
    return checks


def _line(ctx: engine.Context, check: Check) -> None:
    """One check as one line: ``OK`` with its reason, ``FAILED`` with its next action."""

    ctx.reporter.start(check.name, check.what)
    try:
        reason = check.read()
    except _SkippedCheck as skipped:
        ctx.reporter.end(check.name, Status.SKIPPED, str(skipped))
        return
    except CliError as error:
        ctx.reporter.end(
            check.name,
            Status.FAILED,
            error.message,
            next_action=error.next_action or ctx.reporter.default_next_action,
            code=error.code.value,
        )
        return
    ctx.reporter.end(check.name, Status.OK, reason)


def status(ctx: engine.Context) -> None:
    """Report every check one line at a time. Nothing here writes."""

    roles = runtime.saved_roles(ctx)
    for check in _checks(ctx, roles, _Facts(ctx, roles)):
        _line(ctx, check)


def register() -> None:
    """Give ``status`` its body and the setup key it reads the Gateway with."""

    command = engine.COMMANDS["status"]
    command.handler = status
    command.extra_inputs = [runtime.OBSERVED_TOKEN_INPUT]
