"""``ignition-mcp setup`` on the Runtime plane (D32 sections 5 to 10, issue #74).

One stage, ``runtime``. Its plan reads the Gateway and the repository checkout and
writes nothing. Its apply runs these steps in order, each with a start and an end line:

1. the MCP Module: the ``.modl`` whose SHA-256 matches the pinned build is uploaded,
   its certificate and EULA accepted, installed, and the Gateway restarted;
2. the Runtime bundle: built from the checkout with ``tooling.native`` and imported
   as the managed project, after a backup of the managed project it replaces;
3. the Security Levels ``Authenticated/IgnitionMcpAnalysis`` and
   ``Authenticated/IgnitionMcpEngineer``, in one edit of the singleton;
4. per role, one Gateway API token granted the role's level, its secret in the
   deployment directory;
5. per role, the Server Config with the explicit Tool list of the role's profile and
   a permissions tree generated from the role's level;
6. the Runtime Target Policy generated from the environment;
7. the generated documents, stored in the deployment directory;
8. per role, the closing check: ``initialize`` and ``tools/list`` at the role's
   endpoint with the role's own token. A 403 names its cause.

The Gateway writes go through ``setup_native``'s curated writer, so the guards, the
optimistic preconditions and the read-backs Phases 4 to 6 verified still apply. The
module also adds the D32 section 9 check that the pasted setup key's Security Level
is ticked under every permission in Security > General Settings.

Tests replace :data:`SETTINGS`: the Gateway transport, the sleeper for the restart
wait, the folders searched for the ``.modl`` and the checkout.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from ignition_rest_mcp.cli.engine.deployment import read_secret, remove_secret
from ignition_rest_mcp.cli.engine.errors import CliError, ErrorCode
from ignition_rest_mcp.cli.engine.main import (
    COMMANDS,
    ApplyContext,
    Context,
    Plan,
    Stage,
    register_stage,
)
from ignition_rest_mcp.cli.engine.report import Status
from ignition_rest_mcp.cli.engine.resolve import PROG, TOKEN_CHECKS, InputSpec, Kind, Needed, Risk
from ignition_rest_mcp.cli.setup_native import documents as docs
from ignition_rest_mcp.cli.setup_native import gateway as gw
from ignition_rest_mcp.cli.setup_native import install_module, security
from ignition_rest_mcp.cli.setup_native.apply import _confirm_policy
from ignition_rest_mcp.cli.setup_native.inputs import (
    API_TOKEN_TYPE,
    DEFAULT_BUNDLE_PROJECT,
    MAX_MODULE_BYTES,
    MODULE_NAME_TOKEN,
    SECURITY_LEVEL_PARENT,
    SECURITY_LEVELS_TYPE,
    Endpoint,
    Inputs,
    ModuleInputs,
    UsageError,
)
from ignition_rest_mcp.cli.setup_native.mcp_http import McpHttpClient, McpMethodNotFound, McpProbeError
from ignition_rest_mcp.cli.setup_native.writer import GatewayWriter, WriteError

STAGE_NAME = "runtime"

#: The MCP Module build this repository pins (``tests/fixtures/modules``). D32 section
#: 7 installs only a local file with this SHA-256 and never a lower build.
PINNED_MODULE_SHA256 = "b1142a5796f2fd834555f13f03de706599d745f7172a68e54f2f7908b67fe365"
PINNED_MODULE_BUILD = "2026021307"
MODULE_FIXTURE_DIR = "tests/fixtures/modules"
BUNDLE_PROJECT_DIR = "packages/ignition-runtime-bundle/project"
PROJECT = DEFAULT_BUNDLE_PROJECT

SERVICE_IDENTITY = "ignition-mcp-service"
SHELVE_CAP_SECONDS = 3600
POLICY_FILE = "runtime-policy.json"
PERMISSIONS_FILE = "permissions-{role}.json"
BACKUP_DIR = "backups"

#: The restart wait: one ``modules/healthy`` read per poll, bounded (D10).
RESTART_POLL_SECONDS = 5.0
RESTART_READY_SECONDS = 600.0
#: How often the closing check re-announces a Server Config whose endpoint serves no
#: Tool yet (the Module builds the server before the project's provider registers).
REFRESH_ATTEMPTS = 3
REFRESH_WAIT_SECONDS = 2.0

SECURITY_PROPERTIES_PATH = "/data/api/v1/resources/singleton/ignition/security-properties"
API_TOKEN_DELETE_PATH = "/data/api/v1/resources/ignition/api-token/{name}/{signature}"
#: The permissions of Security > General Settings, as the page labels them.
GATEWAY_PERMISSIONS = (
    ("accessPermissions", "Gateway Access"),
    ("readPermissions", "Gateway Read"),
    ("writePermissions", "Gateway Write"),
    ("designerPermissions", "Designer"),
)


@dataclass(frozen=True, slots=True)
class Role:
    """One Assistant role on the Runtime plane (D32 section 8)."""

    name: str
    profile: str
    level: str

    @property
    def server_config(self) -> str:
        return self.name

    @property
    def token(self) -> str:
        return f"ignition-mcp-{self.name}"

    @property
    def secret(self) -> str:
        return f"runtime-{self.name}"

    @property
    def level_path(self) -> str:
        return f"{SECURITY_LEVEL_PARENT}/{self.level}"

    def permissions(self) -> dict[str, Any]:
        """The Server Config permissions tree, generated from the role's level."""

        return {
            "type": "AllOf",
            "securityLevels": [
                {"name": SECURITY_LEVEL_PARENT, "children": [{"name": self.level, "children": []}]}
            ],
        }


ROLES = {
    "analysis": Role("analysis", "readonly", "IgnitionMcpAnalysis"),
    "engineer": Role("engineer", "full", "IgnitionMcpEngineer"),
}


@dataclass(slots=True)
class Settings:
    """What tests replace. ``None`` means the real thing."""

    transport: httpx.AsyncBaseTransport | None = None
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    module_dirs: list[Path] | None = None
    checkout: Path | None = None


SETTINGS = Settings()


# ------------------------------------------------------------ checkout and bundle


def find_checkout() -> Path | None:
    """The repository checkout: above this file, else above the working directory."""

    if SETTINGS.checkout is not None:
        return SETTINGS.checkout
    for start in (Path(__file__).resolve(), Path.cwd().resolve()):
        for candidate in (start, *start.parents):
            if (candidate / BUNDLE_PROJECT_DIR).is_dir() and (candidate / "tooling" / "native").is_dir():
                return candidate
    return None


def _require_checkout(ctx: Context) -> Path:
    checkout = find_checkout()
    if checkout is None:
        raise CliError(
            ErrorCode.STEP_FAILED,
            "setup builds the Runtime bundle from the repository checkout, and none was found above "
            "the installed package or the working directory",
            next_action=f"cd <ignition-mcp checkout> && {PROG} setup --deployment {ctx.deployment.name}",
        )
    return checkout


@dataclass(frozen=True, slots=True)
class Bundle:
    """What the checkout says the Runtime bundle is."""

    checkout: Path
    version: str
    #: ``{profile: {"tools"|"resources"|"prompts": [...]}}`` for the roles' profiles.
    inventories: dict[str, dict[str, list[str]]]
    #: The bundled Runtime Tools whose contract names a Mutation class.
    mutation_tools: list[str]

    @property
    def manifest(self) -> dict[str, Any]:
        """The manifest subset ``setup_native``'s document builders read."""

        return {"bundleVersion": self.version, "profileInventories": self.inventories}

    def tools(self, role: Role) -> list[str]:
        return list(self.inventories[role.profile]["tools"])


def read_bundle(checkout: Path) -> Bundle:
    try:
        version = (checkout / BUNDLE_PROJECT_DIR).parent.joinpath("BUNDLE_VERSION").read_text("utf-8").strip()
        inventories: dict[str, dict[str, list[str]]] = {}
        for profile in sorted({role.profile for role in ROLES.values()}):
            document = json.loads((checkout / "contracts" / "profiles" / f"{profile}.yaml").read_text("utf-8"))
            inventories[profile] = {
                key: [str(item) for item in document.get(key, [])] for key in ("tools", "resources", "prompts")
            }
        mutation_tools = []
        for tool in inventories["full"]["tools"]:
            contract = json.loads(
                (checkout / "contracts" / "tools" / "runtime" / f"{tool}.contract.json").read_text("utf-8")
            )
            if contract.get("mutationClass", "NONE") != "NONE":
                mutation_tools.append(tool)
    except (OSError, ValueError, AttributeError) as error:
        raise CliError(
            ErrorCode.STEP_FAILED,
            f"the checkout at {checkout} has no readable bundle version or profiles ({type(error).__name__})",
            next_action=f"git -C {checkout} status",
        ) from error
    return Bundle(checkout, version, inventories, sorted(mutation_tools))


def _git_revision(checkout: Path) -> str | None:
    try:
        answer = subprocess.run(
            ["git", "-C", str(checkout), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    revision = answer.stdout.strip()
    return revision if answer.returncode == 0 and len(revision) == 40 else None


def build_bundle(checkout: Path, output: Path) -> bytes:
    """Build the bundle ZIP with the deterministic builder the release uses."""

    if str(checkout) not in sys.path:
        sys.path.insert(0, str(checkout))
    archive: Any = importlib.import_module("tooling.native.archive")
    validation: Any = importlib.import_module("tooling.native.validation")
    try:
        archive.build_project(checkout / BUNDLE_PROJECT_DIR, output, source_revision=_git_revision(checkout))
    except validation.ValidationError as error:
        raise CliError(
            ErrorCode.STEP_FAILED,
            f"the bundle in the checkout does not build: {error}",
            next_action=f"uv run --no-sync python -m tooling.native.cli validate --project-dir {BUNDLE_PROJECT_DIR}",
        ) from error
    return output.read_bytes()


# ------------------------------------------------------------------- the Module


def _module_dirs() -> list[Path]:
    if SETTINGS.module_dirs is not None:
        return SETTINGS.module_dirs
    dirs = [Path.home() / "Downloads"]
    checkout = find_checkout()
    if checkout is not None:
        dirs.insert(0, checkout / MODULE_FIXTURE_DIR)
    return dirs


def file_sha256(path: Path) -> str:
    """The file's SHA-256, or ``""`` when it cannot be read or passes the ``.modl`` bound."""

    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                size += len(block)
                if size > MAX_MODULE_BYTES:
                    return ""
                digest.update(block)
    except OSError:
        return ""
    return digest.hexdigest()


def discover_module(_values: Mapping[str, str]) -> str | None:
    """The first ``.modl`` in the searched folders whose SHA-256 is the pinned one."""

    for directory in _module_dirs():
        for path in sorted(directory.glob("*.modl")) if directory.is_dir() else ():
            if file_sha256(path) == PINNED_MODULE_SHA256:
                return str(path)
    return None


def check_module_file(value: str, _values: Mapping[str, str]) -> str:
    path = Path(value)
    if not path.is_file():
        return f"{path} is not a file"
    digest = file_sha256(path)
    if digest != PINNED_MODULE_SHA256:
        found = digest[:16] + "..." if digest else "unreadable"
        return (
            f"{path} has SHA-256 {found}, not the pinned MCP Module build {PINNED_MODULE_BUILD} "
            f"({PINNED_MODULE_SHA256[:16]}...); nothing was uploaded"
        )
    return ""


MODULE_INPUT = InputSpec(
    name="module_file",
    flag="--module-file",
    question=f"MCP Module .modl file (setup looks in {MODULE_FIXTURE_DIR} and ~/Downloads)",
    kind=Kind.PATH,
    default=discover_module,
    check=check_module_file,
)


def _artifact(path: Path, endpoint: Endpoint) -> install_module.Artifact:
    name = path.name if MODULE_NAME_TOKEN.fullmatch(path.name) else "MCP-module.modl"
    inputs = ModuleInputs(
        command=install_module.INSTALL,
        module_file=path,
        upload_name=name,
        sha256=PINNED_MODULE_SHA256,
        gateway_url=endpoint,
        gateway_token="",
        timeout_seconds=10.0,
        allow_insecure_authorize=True,
        as_json=False,
        accept_certificate=True,
        accept_eula=True,
        acknowledge_upgrade=True,
        restart=True,
    )
    try:
        return install_module.read_artifact(inputs)
    except UsageError as error:
        raise CliError(ErrorCode.INVALID_INPUT, str(error).replace("--file", "--module-file")) from error


# -------------------------------------------------------------- security levels


def leaf_paths(levels: Any) -> list[tuple[str, ...]]:
    """The selected levels of a Security Level tree: every path that ends in a leaf."""

    paths: list[tuple[str, ...]] = []

    def walk(nodes: Any, prefix: tuple[str, ...]) -> None:
        for node in nodes if isinstance(nodes, list) else ():
            if not isinstance(node, dict) or not isinstance(node.get("name"), str):
                continue
            here = (*prefix, str(node["name"]))
            children = node.get("children")
            if isinstance(children, list) and children:
                walk(children, here)
            else:
                paths.append(here)

    walk(levels, ())
    return paths


def satisfies(held: list[tuple[str, ...]], permission: Any) -> bool:
    """Whether levels ``held`` satisfy one permission object (``AnyOf`` or ``AllOf``).

    A level satisfies a requirement when it is that level or one below it. An empty
    requirement lets everyone through, as the Gateway's own description says.
    """

    if not isinstance(permission, dict):
        return True
    required = leaf_paths(permission.get("securityLevels"))
    if not required:
        return True
    hits = [any(level[: len(want)] == want for level in held) for want in required]
    return all(hits) if permission.get("type") == "AllOf" else any(hits)


def token_levels(document: Any) -> list[tuple[str, ...]]:
    """The levels an API token resource document grants."""

    config = document.get("config") if isinstance(document, dict) else None
    profile = config.get("profile") if isinstance(config, dict) else None
    return leaf_paths(profile.get("securityLevels") if isinstance(profile, dict) else None)


def _dotted(paths: list[tuple[str, ...]]) -> str:
    return ", ".join("/".join(path) for path in paths) or "none"


def _endpoint_of(url: str, path: str = "") -> Endpoint:
    parts = urlsplit(url)
    return Endpoint(
        url=url.rstrip("/") + path,
        scheme=parts.scheme,
        host=str(parts.hostname),
        port=parts.port or (443 if parts.scheme == "https" else 80),
    )


def check_setup_key(url: str, token: str) -> str:
    """D32 section 9: the setup key's level must be ticked under every Gateway permission.

    Returns ``""`` when it is, else a reason that names the permissions to tick, so the
    wizard asks for the key again.
    """

    return asyncio.run(_check_setup_key(url, token))


async def _check_setup_key(url: str, token: str) -> str:
    name = token.partition(":")[0]
    async with gw.GatewayRest(_endpoint_of(url), token, transport=SETTINGS.transport) as client:
        try:
            document = await client.resource_document(API_TOKEN_TYPE, name)
            properties = await client.get_json(SECURITY_PROPERTIES_PATH, params={"defaultIfUndefined": "true"})
        except gw.GatewayProbeError as error:
            if "HTTP 403" in str(error):
                return (
                    "the key's Security Level may not read the Gateway's security settings; tick it under "
                    "every permission in Security > General Settings"
                )
            return f"the key's permissions could not be read ({error})"
    if document is None:
        return f"the Gateway serves no API key named {name!r}; paste the whole <name>:<key> line"
    held = token_levels(document)
    config = properties.get("config") if isinstance(properties, dict) else None
    settings = config if isinstance(config, dict) else {}
    missing = [label for key, label in GATEWAY_PERMISSIONS if not satisfies(held, settings.get(key))]
    if missing:
        return (
            f"the key's Security Level ({_dotted(held)}) is not ticked under {', '.join(missing)} in "
            "Security > General Settings; tick it under every permission there, then paste the key again"
        )
    return ""


# -------------------------------------------------------------------- the plan


@dataclass(slots=True)
class TokenPlan:
    #: ``none``, ``create`` or ``recreate``.
    action: str = "none"
    reason: str = ""
    #: The existing token document, for its signature when it is deleted.
    document: dict[str, Any] | None = None
    #: A secret file that must go before the new one is written.
    remove_file: bool = False


@dataclass(slots=True)
class ConfigPlan:
    #: ``none``, ``create`` or ``update``.
    action: str = "none"
    reason: str = ""
    config: dict[str, Any] = field(default_factory=dict)
    signature: str = ""


@dataclass(slots=True)
class RuntimePlan:
    endpoint: Endpoint
    bundle: Bundle
    roles: list[Role]
    environment: str
    insecure_channel: bool
    module_file: Path
    artifact: install_module.Artifact
    installed: gw.ModuleIdentity | None
    project: gw.ProjectState
    levels: list[dict[str, Any]]
    levels_signature: str
    levels_collection: str
    new_levels: list[Role]
    tokens: dict[str, TokenPlan]
    configs: dict[str, ConfigPlan]
    policy_text: str
    #: ``none``, ``create`` or ``update``.
    policy_action: str
    documents: dict[str, str]

    @property
    def install_module(self) -> bool:
        return self.installed is None or self.installed.build != self.artifact.build

    @property
    def project_action(self) -> str:
        if self.project.classification == gw.ABSENT:
            return "create"
        return "none" if self.project.bundle_version == self.bundle.version else "update"


def role_inputs(plan: RuntimePlan, role: Role) -> Inputs:
    """The ``setup_native`` inputs one role's documents are built from. No secret in it."""

    return Inputs(
        command="apply",
        manifest_path=plan.bundle.checkout,
        manifest=plan.bundle.manifest,
        gateway_url=plan.endpoint,
        mcp_url=None,
        bundle_zip=None,
        profile=role.profile,
        bundle_project=PROJECT,
        server_config_name=role.server_config,
        gateway_token="",
        mcp_token=None,
        timeout_seconds=10.0,
        allow_insecure_authorize=True,
        as_json=False,
        provision_security_levels=True,
        security_level_name=role.level,
        create_runtime_token=True,
        runtime_token_name=role.token,
        runtime_token_insecure_channel=plan.insecure_channel,
    )


def policy_document(bundle: Bundle, environment: str) -> str:
    """The generated Runtime Target Policy, in the canonical text the reader checks."""

    allowlists = {tool: ["*"] for tool in bundle.mutation_tools} if environment == "dev" else {}
    return docs.canonical_text(
        {
            "schemaVersion": 1,
            "allowlists": allowlists,
            "serviceIdentity": SERVICE_IDENTITY,
            "auditMode": "best_effort",
            "alarmShelveMaxSeconds": SHELVE_CAP_SECONDS,
        }
    )


def _read_local(path: Path) -> str | None:
    try:
        return path.read_text("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def plan_runtime(ctx: Context) -> Plan:
    checkout = _require_checkout(ctx)
    bundle = read_bundle(checkout)
    environment = ctx.resolved.values["environment"]
    roles = [ROLES[name] for name in ctx.resolved.list("roles")]
    endpoint = _endpoint_of(ctx.resolved.values["gateway_url"])
    module_file = Path(ctx.resolved.values["module_file"])
    artifact = _artifact(module_file, endpoint)
    observed = asyncio.run(_observe(ctx, roles))
    installed, project, levels_doc, token_docs, config_docs, policy = observed

    if installed is not None and install_module.classify_build(installed, artifact) == install_module.REFUSED:
        raise CliError(
            ErrorCode.STEP_FAILED,
            f"the Gateway runs MCP Module build {installed.build or 'unreadable'} and the pinned file "
            f"{module_file} is build {artifact.build}; a lower build is never installed",
            next_action=f"curl -sS {endpoint.url}{gw.MODULES_PATH}",
        )
    if project.classification in (gw.UNMANAGED_SAME_NAME, gw.MARKER_INVALID):
        detail = "no ownership marker" if project.classification == gw.UNMANAGED_SAME_NAME else "an invalid marker"
        raise CliError(
            ErrorCode.STEP_FAILED,
            f"the Gateway has a project named {PROJECT} with {detail}; setup never takes over an unmanaged project",
            next_action=f"curl -sS {endpoint.url}{gw.PROJECT_FIND_PATH.format(name=PROJECT)}",
        )
    if project.managed and project.inheritable is True:
        raise CliError(
            ErrorCode.STEP_FAILED,
            f"the managed project {PROJECT} is inheritable; make it standalone in the Designer first",
            next_action=f"curl -sS {endpoint.url}{gw.PROJECT_FIND_PATH.format(name=PROJECT)}",
        )

    tree, signature, collection = _levels(levels_doc, endpoint)
    new_levels = _new_levels(ctx, roles, tree, environment)
    insecure = endpoint.scheme == "http" and environment == "dev"
    plan = RuntimePlan(
        endpoint=endpoint,
        bundle=bundle,
        roles=roles,
        environment=environment,
        insecure_channel=insecure,
        module_file=module_file,
        artifact=artifact,
        installed=installed,
        project=project,
        levels=tree,
        levels_signature=signature,
        levels_collection=collection,
        new_levels=new_levels,
        tokens={},
        configs={},
        policy_text=policy_document(bundle, environment),
        policy_action="none",
        documents={},
    )
    changes: list[str] = []
    needed: list[Needed] = []
    if plan.install_module:
        verb = "install" if installed is None else f"upgrade build {installed.build} to"
        changes.append(
            f"{verb} MCP Module build {artifact.build} from {module_file}, accept its certificate and EULA, "
            "and restart the Gateway"
        )
        detail = f"MCP Module build {artifact.build}"
        needed += [Needed(Risk.CERTIFICATE, detail), Needed(Risk.EULA, detail), Needed(Risk.RESTART, detail)]
    if plan.project_action == "create":
        changes.append(f"deploy the Runtime bundle {bundle.version} as the managed project {PROJECT}")
    elif plan.project_action == "update":
        changes.append(
            f"replace the managed bundle {project.bundle_version} with {bundle.version} in project {PROJECT}, "
            f"after a backup into {ctx.deployment.directory / BACKUP_DIR}"
        )
    for role in new_levels:
        changes.append(f"create the Security Level {role.level_path}")

    for role in roles:
        token = _plan_token(ctx, plan, role, token_docs.get(role.name), needed)
        plan.tokens[role.name] = token
        if token.action != "none":
            changes.append(token.reason)
        config = _plan_config(plan, role, config_docs.get(role.name), needed)
        plan.configs[role.name] = config
        if config.action != "none":
            changes.append(config.reason)
    if insecure and any(token.action != "none" for token in plan.tokens.values()):
        needed.append(Needed(Risk.UNENCRYPTED_CHANNEL, f"{endpoint.url} is http"))

    change = _plan_policy(ctx, plan, policy, needed)
    if change:
        changes.append(change)
    plan.documents = {POLICY_FILE: plan.policy_text}
    for role in roles:
        plan.documents[PERMISSIONS_FILE.format(role=role.name)] = _json(role.permissions())
    for name, text in plan.documents.items():
        if _read_local(ctx.deployment.directory / name) != text:
            changes.append(f"write the generated {name} into {ctx.deployment.directory}")
    return Plan(changes=changes, needed=needed, data=plan)


Observed = tuple[
    gw.ModuleIdentity | None,
    gw.ProjectState,
    dict[str, Any] | None,
    dict[str, dict[str, Any] | None],
    dict[str, dict[str, Any] | None],
    docs.PolicyObservation,
]


async def _observe(ctx: Context, roles: list[Role]) -> Observed:
    """Every Gateway read the plan needs, with the GET-only client."""

    try:
        async with ctx.gateway_reader(SETTINGS.transport) as client:
            installed = await client.mcp_module()
            project = await client.find_project(PROJECT)
            levels = await client.singleton_document(SECURITY_LEVELS_TYPE)
            tokens = {role.name: await client.resource_document(API_TOKEN_TYPE, role.token) for role in roles}
            configs: dict[str, dict[str, Any] | None] = {}
            for role in roles:
                # Without the Module the Server Config type does not exist yet.
                present = installed is not None
                configs[role.name] = await client.server_config_document(role.server_config) if present else None
            policy = await docs.observe_policy(client)
    except gw.GatewayProbeError as error:
        raise CliError(
            ErrorCode.STEP_FAILED,
            f"the Gateway's current state could not be read ({error})",
            next_action=f"curl -sSI {ctx.resolved.values['gateway_url']}{gw.GATEWAY_INFO_PATH}",
        ) from error
    if policy.error:
        raise CliError(
            ErrorCode.STEP_FAILED,
            f"the Runtime Target Policy could not be read ({policy.error})",
            next_action=f"{PROG} status --deployment {ctx.deployment.name}",
        )
    return installed, project, levels, tokens, configs, policy


def _levels(document: dict[str, Any] | None, endpoint: Endpoint) -> tuple[list[dict[str, Any]], str, str]:
    tree = security.level_tree(document)
    signature = str(document.get("signature") or "") if isinstance(document, dict) else ""
    if tree is None or not signature:
        raise CliError(
            ErrorCode.STEP_FAILED,
            "the Gateway's Security Level tree is not readable with a signature, so it cannot be edited safely",
            next_action=f"curl -sS {endpoint.url}{gw.SECURITY_LEVELS_PATH}",
        )
    return tree, signature, security.singleton_collection(document)


def _new_levels(ctx: Context, roles: list[Role], tree: list[dict[str, Any]], environment: str) -> list[Role]:
    missing: list[Role] = []
    for role in roles:
        found = security.find_level(tree, role.level)
        if found is None:
            missing.append(role)
            continue
        path, node = found
        problem = security.level_shape_problem(node)
        if path != [SECURITY_LEVEL_PARENT, role.level] or problem:
            where = "/".join(path)
            raise CliError(
                ErrorCode.STEP_FAILED,
                f"a Security Level named {role.level} exists at {where}{f' and {problem}' if problem else ''}; "
                "setup never modifies a level it did not create",
                next_action=f"{PROG} status --deployment {ctx.deployment.name}",
            )
    if missing and environment == "prod" and not getattr(ctx.args, "provision_security_levels", False):
        raise CliError(
            ErrorCode.ACCEPTANCE_REQUIRED,
            f"prod creates Security Levels only when asked; {', '.join(r.level_path for r in missing)} "
            "is missing. Nothing was written",
            next_action=f"{PROG} setup --deployment {ctx.deployment.name} --provision-security-levels",
        )
    for role in missing:
        _, reason = security.with_managed_level(tree, _level_inputs(role))
        if reason:
            raise CliError(
                ErrorCode.STEP_FAILED, reason, next_action=f"{PROG} status --deployment {ctx.deployment.name}"
            )
    return missing


def _level_inputs(role: Role) -> Inputs:
    """Just enough ``Inputs`` for the Security Level helpers, which read the role only."""

    return Inputs(
        command="apply", manifest_path=Path("."), manifest={}, gateway_url=_endpoint_of("http://localhost"),
        mcp_url=None, bundle_zip=None, profile=role.profile, bundle_project=PROJECT,
        server_config_name=role.server_config, gateway_token="", mcp_token=None, timeout_seconds=10.0,
        allow_insecure_authorize=True, as_json=False, security_level_name=role.level,
    )


def _plan_token(
    ctx: Context, plan: RuntimePlan, role: Role, document: dict[str, Any] | None, needed: list[Needed]
) -> TokenPlan:
    """D32 section 10's first rule: a lost secret is reported and recreated after confirmation."""

    path = ctx.deployment.secret_path(role.secret)
    secret = security.observe_secret_file(path)
    if secret.error and document is not None:
        raise CliError(
            ErrorCode.SECRET_FILE_INVALID,
            f"{path}: {secret.error}",
            next_action=f"chmod 600 {path}",
        )
    create = (
        f"create the API token {role.token} granted {role.level_path}"
        f"{' over http' if plan.insecure_channel else ''}; its secret goes to {path}"
    )
    if document is None:
        stale = secret.exists or bool(secret.error)
        return TokenPlan("create", create + ("; the stale file there is replaced" if stale else ""), None, stale)
    stored = security.stored_token_hash(document)
    if secret.exists and secret.name == role.token and secret.hashes_to(stored):
        drift = _token_drift(plan, role, document)
        if not drift:
            return TokenPlan("none", "", document)
        needed.append(Needed(Risk.OVERWRITE_HAND_EDIT, f"API token {role.token}: {drift}"))
        return TokenPlan(
            "recreate", f"delete the API token {role.token} and create it again, because {drift}", document, True
        )
    lost = "its secret file is missing" if not secret.exists else "its secret file holds another credential"
    reason = f"delete the API token {role.token} and create it again, because {lost} ({path})"
    if not ctx.dry_run and not getattr(ctx.args, "recreate_tokens", False):
        question = f"The API token {role.token} exists on the Gateway but {lost}. Delete it and create a new one?"
        if ctx.prompter is None:
            raise CliError(
                ErrorCode.ACCEPTANCE_REQUIRED,
                f"the API token {role.token} exists on the Gateway but {lost}; pass --recreate-tokens to "
                "delete it and create a new one. Nothing was written",
                next_action=f"{PROG} setup --deployment {ctx.deployment.name} --recreate-tokens --yes",
            )
        if not ctx.prompter.confirm(question):
            raise CliError(
                ErrorCode.NOT_CONFIRMED,
                f"the API token {role.token} was kept, so the {role.name} role has no usable secret. "
                "Nothing was written",
            )
        ctx.args.recreate_tokens = True
    return TokenPlan("recreate", reason, document, secret.exists or bool(secret.error))


def _token_drift(plan: RuntimePlan, role: Role, document: dict[str, Any]) -> str:
    config = document.get("config")
    profile = config.get("profile") if isinstance(config, dict) else None
    secure = profile.get("secureChannelRequired") if isinstance(profile, dict) else None
    held = token_levels(document)
    if held != [(SECURITY_LEVEL_PARENT, role.level)]:
        return f"it grants {_dotted(held)}, not {role.level_path}"
    if secure is not (not plan.insecure_channel):
        return f"it has secureChannelRequired={str(secure).lower()}"
    return ""


def _plan_config(
    plan: RuntimePlan, role: Role, document: dict[str, Any] | None, needed: list[Needed]
) -> ConfigPlan:
    """The role's Server Config. A permissions tree that differs is a change (D32 section 10)."""

    held = document.get("config") if isinstance(document, dict) else None
    desired = docs.desired_server_config(role_inputs(plan, role), held, role.permissions())
    tools = plan.bundle.tools(role)
    if document is None:
        return ConfigPlan(
            "create",
            f"create the Server Config {role.server_config} with the {len(tools)} Tools of the "
            f"{role.profile} profile and a permissions tree of {role.level_path}",
            desired,
        )
    differences: list[str] = []
    hand_edits: list[str] = []
    held_config = held if isinstance(held, dict) else {}
    if held_config.get("permissions") != desired["permissions"]:
        hand_edits.append("its permissions tree differs from the one generated from " + role.level_path)
    observed, note = docs.observed_tools(document, PROJECT)
    if observed is None or sorted(observed) != sorted(tools):
        drift = note or _tool_drift(observed or [], tools)
        (hand_edits if held_config.get("version") == plan.bundle.version else differences).append(
            f"its Tool list {drift}"
        )
    if held_config.get("version") != plan.bundle.version:
        differences.append(f"it names bundle {held_config.get('version')}, not {plan.bundle.version}")
    for key in ("resources", "prompts"):
        entry = held_config.get(key)
        if not isinstance(entry, dict) or entry.get(f"project/{PROJECT}") != "*":
            differences.append(f"its {key} entry for project/{PROJECT} differs")
    if document.get("enabled") is False:
        hand_edits.append("it is disabled")
    if not differences and not hand_edits:
        return ConfigPlan("none", "", desired, str(document.get("signature") or ""))
    for edit in hand_edits:
        needed.append(Needed(Risk.OVERWRITE_HAND_EDIT, f"Server Config {role.server_config}: {edit}"))
    return ConfigPlan(
        "update",
        f"update the Server Config {role.server_config}: {'; '.join(hand_edits + differences)}",
        desired,
        str(document.get("signature") or ""),
    )


def _tool_drift(observed: list[str], desired: list[str]) -> str:
    adds = sorted(set(desired) - set(observed))
    removes = sorted(set(observed) - set(desired))
    parts = []
    if adds:
        parts.append("lacks " + ", ".join(adds))
    if removes:
        parts.append("has extra " + ", ".join(removes))
    return " and ".join(parts) or "differs"


def _plan_policy(ctx: Context, plan: RuntimePlan, policy: docs.PolicyObservation, needed: list[Needed]) -> str:
    """The generated policy. A served document setup did not write is a hand edit."""

    text = plan.policy_text
    summary = (
        f"'*' for {len(plan.bundle.mutation_tools)} Runtime Mutation Tools"
        if plan.environment == "dev"
        else "empty allowlists"
    )
    if policy.provider_present and policy.matches(text):
        return ""
    if plan.environment == "dev":
        needed.append(Needed(Risk.WILDCARD_ALLOWLIST, f"Runtime Target Policy, {summary}"))
    if not policy.provider_present or policy.policy_text is None:
        plan.policy_action = "create"
        return f"write the Runtime Target Policy ({plan.environment}: {summary}, shelve cap {SHELVE_CAP_SECONDS} s)"
    plan.policy_action = "update"
    written = _read_local(ctx.deployment.directory / POLICY_FILE)
    if policy.policy_text != written:
        needed.append(Needed(Risk.OVERWRITE_HAND_EDIT, "Runtime Target Policy: the served document is not the "
                                                        "one setup wrote"))
        return (
            f"restore the Runtime Target Policy ({plan.environment}: {summary}); the served document is not the "
            "one setup wrote, so someone changed it by hand"
        )
    return f"replace the Runtime Target Policy with the {plan.environment} one ({summary})"


def _json(document: Any) -> str:
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


# ------------------------------------------------------------------ the apply


def apply_runtime(ctx: ApplyContext, plan_: Plan) -> None:
    plan: RuntimePlan = plan_.data
    with ctx.reporter.step("runtime deployment", "saving deployment.toml") as end:
        before = dict(ctx.deployment.values)
        after = ctx.save().values
        end.set(Status.CHANGED if after != before else Status.OK, f"{ctx.deployment.directory}")
    with ctx.reporter.step("runtime module", f"MCP Module build {plan.artifact.build}") as end:
        if plan.install_module:
            asyncio.run(_install_module(ctx, plan))
            end.set(Status.CHANGED, f"installed build {plan.artifact.build} from {plan.module_file}; restarted")
        else:
            end.set(Status.OK, f"build {plan.artifact.build} is installed; the pinned file is {plan.module_file}")
    with ctx.reporter.step("runtime bundle", f"project {PROJECT}") as end:
        if plan.project_action == "none":
            end.set(Status.OK, f"managed bundle {plan.bundle.version} is deployed")
        else:
            end.set(Status.CHANGED, asyncio.run(_deploy_bundle(ctx, plan)))
    with ctx.reporter.step("runtime security levels", ", ".join(r.level_path for r in plan.roles)) as end:
        if plan.new_levels:
            asyncio.run(_write_levels(ctx, plan))
            end.set(Status.CHANGED, "created " + ", ".join(r.level_path for r in plan.new_levels))
        else:
            end.set(Status.OK, "every role's level is present")
    for role in plan.roles:
        token = plan.tokens[role.name]
        with ctx.reporter.step(f"runtime token {role.name}", role.token) as end:
            if token.action == "none":
                end.set(Status.OK, f"exists and matches {ctx.deployment.secret_path(role.secret)}")
            else:
                end.set(Status.CHANGED, asyncio.run(_write_token(ctx, plan, role, token)))
        config = plan.configs[role.name]
        with ctx.reporter.step(f"runtime server config {role.name}", role.server_config) as end:
            if config.action == "none":
                end.set(Status.OK, f"{len(plan.bundle.tools(role))} Tools and the generated permissions tree")
            else:
                end.set(Status.CHANGED, asyncio.run(_write_config(ctx, plan, role, config)))
    with ctx.reporter.step("runtime policy", docs.POLICY_PATH) as end:
        if plan.policy_action == "none":
            end.set(Status.OK, "the served document is the generated one")
        else:
            end.set(Status.CHANGED, asyncio.run(_write_policy(ctx, plan)))
    with ctx.reporter.step("runtime documents", str(ctx.deployment.directory)) as end:
        written = [name for name, text in plan.documents.items() if _write_local(ctx, name, text)]
        end.set(Status.CHANGED if written else Status.OK, ", ".join(written) or "unchanged")
    for role in plan.roles:
        with ctx.reporter.step(f"runtime check {role.name}", f"{plan.endpoint.url}/data/mcp/{role.server_config}") as end:
            end.set(Status.OK, asyncio.run(_check_role(ctx, plan, role)))


def _write_local(ctx: ApplyContext, name: str, text: str) -> bool:
    path = ctx.deployment.directory / name
    if _read_local(path) == text:
        return False
    staging = path.with_name(name + ".tmp")
    staging.write_text(text, encoding="utf-8", newline="\n")
    os.replace(staging, path)
    return True


def _failed(message: str, ctx: Context) -> CliError:
    return CliError(ErrorCode.STEP_FAILED, message, next_action=f"{PROG} status --deployment {ctx.deployment.name}")


async def _install_module(ctx: ApplyContext, plan: RuntimePlan) -> None:
    artifact = plan.artifact
    name = plan.module_file.name if MODULE_NAME_TOKEN.fullmatch(plan.module_file.name) else "MCP-module.modl"
    async with ctx.gateway_writer(SETTINGS.transport) as writer:
        try:
            upload = await writer.upload_module(name, artifact.payload)
            served = upload.get("moduleId")
            if isinstance(served, str) and served and served != artifact.module_id:
                raise _failed(f"the Gateway stored the upload as {served!r}, not {artifact.module_id!r}", ctx)
            if await writer.reads.module_certificate(artifact.module_id) is not None:
                await writer.accept_module_certificate(artifact.module_id)
            if await writer.reads.module_eula_size(artifact.module_id) is not None:
                await writer.accept_module_eula(artifact.module_id)
            await writer.install_module(artifact.module_id)
        except (WriteError, gw.GatewayProbeError) as error:
            raise _failed(f"the Module install stopped: {error}", ctx) from error
        try:
            await writer.restart_gateway()
        except WriteError as error:
            if error.status != 0:
                raise _failed(f"the restart request was refused: {error}", ctx) from error
        polls = int(RESTART_READY_SECONDS // RESTART_POLL_SECONDS)
        last = "the Gateway has not answered yet"
        for attempt in range(polls):
            try:
                identity = await writer.reads.module_identity(artifact.module_id)
            except gw.GatewayProbeError as error:
                identity, last = None, str(error)
            if identity is not None and identity.build == artifact.build:
                return
            if identity is not None:
                last = f"it serves build {identity.build}"
            if attempt + 1 < polls:
                await SETTINGS.sleep(RESTART_POLL_SECONDS)
    raise _failed(
        f"the Module did not come back as build {artifact.build} within {RESTART_READY_SECONDS:g} s ({last})", ctx
    )


async def _deploy_bundle(ctx: ApplyContext, plan: RuntimePlan) -> str:
    with tempfile.TemporaryDirectory(prefix="ignition-mcp-bundle-") as scratch:
        archive = build_bundle(plan.bundle.checkout, Path(scratch) / f"ignition-runtime-bundle-{plan.bundle.version}.zip")
    async with ctx.gateway_writer(SETTINGS.transport) as writer:
        backup = ""
        try:
            if plan.project_action == "update":
                backup = "; backed up to " + str(await _backup(ctx, plan, writer))
            await writer.import_project(PROJECT, archive, overwrite=plan.project_action == "update")
            state = await writer.reads.find_project(PROJECT)
        except (WriteError, gw.GatewayProbeError) as error:
            raise _failed(f"the bundle import stopped: {error}", ctx) from error
    if not state.managed or state.bundle_version != plan.bundle.version:
        raise _failed(
            f"the import was accepted but {PROJECT} reads back as {state.classification} "
            f"bundle {state.bundle_version}",
            ctx,
        )
    return f"deployed managed bundle {plan.bundle.version} ({len(archive)} bytes){backup}"


async def _backup(ctx: ApplyContext, plan: RuntimePlan, writer: GatewayWriter) -> Path:
    """D32 section 7: a managed project is backed up before setup replaces it."""

    archive = await writer.project_export(PROJECT)
    directory = ctx.deployment.directory / BACKUP_DIR
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    stem = f"{PROJECT}-{plan.project.bundle_version}"
    path = directory / f"{stem}.zip"
    counter = 1
    while path.exists():
        counter += 1
        path = directory / f"{stem}-{counter}.zip"
    path.write_bytes(archive)
    return path


async def _write_levels(ctx: ApplyContext, plan: RuntimePlan) -> None:
    tree = plan.levels
    for role in plan.new_levels:
        merged, reason = security.with_managed_level(tree, _level_inputs(role))
        if merged is None:
            raise _failed(reason, ctx)
        tree = merged
    async with ctx.gateway_writer(SETTINGS.transport) as writer:
        try:
            await writer.update_security_levels(
                tree, signature=plan.levels_signature, collection=plan.levels_collection
            )
            served = await writer.reads.singleton_document(SECURITY_LEVELS_TYPE)
        except (WriteError, gw.GatewayProbeError) as error:
            raise _failed(f"the Security Level edit stopped: {error}", ctx) from error
    for role in plan.roles:
        problem = security.verify_readback(served, plan.levels, _level_inputs(role))
        if problem:
            raise _failed(f"the Security Level edit was accepted but {problem}", ctx)


async def _write_token(ctx: ApplyContext, plan: RuntimePlan, role: Role, token: TokenPlan) -> str:
    inputs = role_inputs(plan, role)
    path = ctx.deployment.secret_path(role.secret)
    async with ctx.gateway_writer(SETTINGS.transport) as writer:
        try:
            if token.action == "recreate" and token.document is not None:
                signature = str(token.document.get("signature") or "")
                if not signature:
                    raise _failed(f"the API token {role.token} serves no signature, so it cannot be deleted", ctx)
                # The writer has no named delete; its single write method still checks the gate.
                await writer._write(
                    "DELETE",
                    API_TOKEN_DELETE_PATH.format(name=role.token, signature=signature),
                    body=b"",
                    content_type="application/json",
                    action="delete API token",
                )
            levels = await writer.reads.singleton_document(SECURITY_LEVELS_TYPE)
            grant = security.grant_tree(security.level_tree(levels) or [], [SECURITY_LEVEL_PARENT, role.level])
            if grant is None:
                raise _failed(f"{role.level_path} is not in the Gateway's Security Level tree", ctx)
            key, declared = security.credential(await writer.generate_api_token())
            ctx.reporter.hide(key, security.token_secret(role.token, key))
            config = security.token_config(inputs, grant, token_hash=declared, timestamp_ms=int(time.time() * 1000))
            await writer.create_api_token(role.token, config, description=security.token_description(inputs))
            served = await writer.reads.resource_document(API_TOKEN_TYPE, role.token)
        except (WriteError, gw.GatewayProbeError, security.CredentialError) as error:
            raise _failed(f"the API token {role.token} was not created: {error}", ctx) from error
    if security.stored_token_hash(served) != declared:
        raise _failed(f"the API token {role.token} reads back a different token hash", ctx)
    if token.remove_file:
        remove_secret(ctx.deployment, role.secret)
    ctx.write_secret(role.secret, security.token_secret(role.token, key))
    verb = "recreated" if token.action == "recreate" else "created"
    return f"{verb} {role.token} granted {role.level_path}; secret in {path} (mode 0600)"


async def _write_config(ctx: ApplyContext, plan: RuntimePlan, role: Role, config: ConfigPlan) -> str:
    async with ctx.gateway_writer(SETTINGS.transport) as writer:
        try:
            signature = config.signature
            if config.action == "create":
                await writer.create_server_config(role.server_config, config.config, enabled=False)
                created = await writer.reads.server_config_document(role.server_config)
                signature = str((created or {}).get("signature") or "")
            # The Module builds the endpoint on an update, so a new config is enabled by one.
            await writer.update_server_config(role.server_config, config.config, signature=signature, enabled=True)
            served = await writer.reads.server_config_document(role.server_config)
        except (WriteError, gw.GatewayProbeError) as error:
            raise _failed(f"the Server Config {role.server_config} was not written: {error}", ctx) from error
    held = (served or {}).get("config")
    observed, note = docs.observed_tools(served or {}, PROJECT)
    if not isinstance(held, dict) or held.get("permissions") != config.config["permissions"]:
        raise _failed(f"the Server Config {role.server_config} reads back another permissions tree", ctx)
    if observed is None or sorted(observed) != sorted(plan.bundle.tools(role)):
        raise _failed(f"the Server Config {role.server_config} reads back {note or 'another Tool list'}", ctx)
    verb = "created" if config.action == "create" else "updated"
    return f"{verb} with {len(observed)} Tools and the permissions tree of {role.level_path}"


async def _write_policy(ctx: ApplyContext, plan: RuntimePlan) -> str:
    text = plan.policy_text
    async with ctx.gateway_writer(SETTINGS.transport) as writer:
        try:
            created = await writer.ensure_policy_provider()
            if created:
                await writer.await_policy_provider()
            outcome = await writer.import_policy(text, first_policy="Abort" if created else "MergeOverwrite")
            await _confirm_policy(writer, docs.Documents(policy_text=text))
        except (WriteError, gw.GatewayProbeError) as error:
            raise _failed(f"the Runtime Target Policy was not written: {error}", ctx) from error
    return f"wrote {docs.byte_length(text)} bytes in {outcome.attempt_count} import(s); read back equal"


# ------------------------------------------------------------ the closing check


async def _check_role(ctx: ApplyContext, plan: RuntimePlan, role: Role) -> str:
    """``initialize`` and ``tools/list`` at the role's endpoint with the role's own token."""

    path = ctx.deployment.secret_path(role.secret)
    try:
        token = read_secret(path)
    except CliError:
        token = ""
    ctx.reporter.hide(token)
    endpoint = _endpoint_of(plan.endpoint.url, f"/data/mcp/{role.server_config}")
    expected = plan.bundle.tools(role)
    tools: list[str] = []
    refreshes = 0
    for attempt in range(REFRESH_ATTEMPTS + 1):
        try:
            async with McpHttpClient(endpoint, token or None, transport=SETTINGS.transport) as client:
                await client.initialize()
                tools = await client.tools_list() if client.advertises("tools") else []
        except McpMethodNotFound:
            tools = []
        except McpProbeError as error:
            if "HTTP 403" in str(error):
                raise _failed(await _explain_403(ctx, plan, role, bool(token)), ctx) from error
            raise _failed(f"the {role.name} endpoint failed: {error}", ctx) from error
        if tools or attempt == REFRESH_ATTEMPTS:
            break
        # The endpoint came up before the project's Tools registered; announcing the
        # same document again makes the Module build it once more.
        await SETTINGS.sleep(REFRESH_WAIT_SECONDS)
        await _reannounce(ctx, plan, role)
        refreshes += 1
    if sorted(tools) != sorted(expected):
        raise _failed(
            f"the {role.name} endpoint lists {len(tools)} Tools and {_tool_drift(tools, expected)} "
            f"compared with the {role.profile} profile",
            ctx,
        )
    note = f", after {refreshes} re-announcement(s)" if refreshes else ""
    return f"initialize with the role's token; tools/list matches the {len(expected)} Tools of {role.profile}{note}"


async def _reannounce(ctx: ApplyContext, plan: RuntimePlan, role: Role) -> None:
    async with ctx.gateway_writer(SETTINGS.transport) as writer:
        try:
            current = await writer.reads.server_config_document(role.server_config) or {}
            await writer.update_server_config(
                role.server_config,
                plan.configs[role.name].config,
                signature=str(current.get("signature") or ""),
                enabled=True,
            )
        except (WriteError, gw.GatewayProbeError) as error:
            raise _failed(f"the Server Config {role.server_config} could not be announced again: {error}", ctx) from error


async def _explain_403(ctx: Context, plan: RuntimePlan, role: Role, token_sent: bool) -> str:
    """D32 section 4: a 403 is reported as its cause, never as the status alone."""

    prefix = f"the {role.name} endpoint refused initialize because "
    if not token_sent:
        return prefix + f"no token was sent: {ctx.deployment.secret_path(role.secret)} holds no usable secret"
    try:
        async with ctx.gateway_reader(SETTINGS.transport) as client:
            token = await client.resource_document(API_TOKEN_TYPE, role.token)
            config = await client.server_config_document(role.server_config)
    except gw.GatewayProbeError as error:
        return prefix + f"of a cause the CLI could not read ({error})"
    profile = ((token or {}).get("config") or {}).get("profile") or {}
    if plan.endpoint.scheme == "http" and profile.get("secureChannelRequired") is True:
        return prefix + "the token requires a secure channel and the Gateway URL is http"
    held = token_levels(token)
    permissions = ((config or {}).get("config") or {}).get("permissions")
    if not satisfies(held, permissions):
        required = _dotted(leaf_paths((permissions or {}).get("securityLevels")))
        return prefix + f"the token's Security Level ({_dotted(held)}) does not satisfy the Server Config's " \
            f"permissions ({required})"
    return prefix + "of none of the known causes: a token was sent, its level satisfies the permissions, and " \
        "the channel is allowed"


# ---------------------------------------------------------------- registration


def _flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--recreate-tokens",
        action="store_true",
        help="delete and recreate a role's Gateway token whose secret file is lost",
    )
    parser.add_argument(
        "--provision-security-levels",
        action="store_true",
        help="in prod, create the roles' missing Security Levels",
    )


def _words(args: argparse.Namespace) -> list[str]:
    words = []
    if getattr(args, "recreate_tokens", False):
        words.append("--recreate-tokens")
    if getattr(args, "provision_security_levels", False):
        words.append("--provision-security-levels")
    return words


STAGE = Stage(STAGE_NAME, plan_runtime, apply_runtime, (MODULE_INPUT,), flags=_flags, words=_words)


def register() -> Stage:
    """Add the Runtime stage to ``setup`` and the setup-key check to the token input."""

    if not any(stage.name == STAGE_NAME for stage in COMMANDS["setup"].stages):
        register_stage(STAGE)
    if check_setup_key not in TOKEN_CHECKS:
        TOKEN_CHECKS.append(check_setup_key)
    return STAGE


def unregister() -> None:
    stages = COMMANDS["setup"].stages
    stages[:] = [stage for stage in stages if stage.name != STAGE_NAME]
    if check_setup_key in TOKEN_CHECKS:
        TOKEN_CHECKS.remove(check_setup_key)
