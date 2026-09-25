"""``ignition-mcp setup`` on the Runtime plane (D32 sections 5 to 10, issue #74).

One stage, ``runtime``. Its plan reads the Gateway and the repository checkout and
writes nothing. Its apply runs these steps in order, each with a start and an end line:

1. the MCP Module: the ``.modl`` whose SHA-256 matches the pinned build is uploaded,
   its certificate and EULA accepted, installed, and the Gateway restarted. The wait
   ends when the listing shows the pinned build in state ACTIVE; any other state fails
   the step, because the routes the Module hosts do not exist without it (issue #81);
2. the Runtime bundle: built from the checkout with ``tooling.native`` and imported
   as the managed project. A replace follows D16: the per-Gateway and per-Project
   writer lock, a baseline export kept as the backup, a fresh export just before the
   import whose ``pcf1`` fingerprint must equal the baseline's, and an export of the
   result that must hold the bundle's content;
3. the Security Levels ``Authenticated/IgnitionMcpAnalysis`` and
   ``Authenticated/IgnitionMcpEngineer``, in one edit of the singleton;
4. per role, one Gateway API token granted the role's level, its secret in the
   deployment directory;
5. per role, the Server Config with the explicit Tool list of the role's profile and
   a permissions tree generated from the role's level;
6. the Runtime Target Policy generated from the environment;
7. the generated documents, stored in the deployment directory;
8. per role, the closing check: the verify sequence at the role's
   endpoint with the role's own token, which checks the exact Tool, Resource and
   Prompt inventories, reads each Resource, gets each Prompt and calls
   ``bundle_info``. A 403 names its cause.

A role the saved deployment served and this run does not, as after a move from
``dev`` to ``prod``, is removed before step 3: its Server Config, its token, its
local files, and in step 3 its Security Level. Only a Gateway resource the
deployment's ``created`` record names is removed; one it does not name is left in
place and reported as ``SKIPPED``, as ``reset`` does. A Module upgrade and a MAJOR or
downgrade Bundle change each need their own Explicit acceptance.

The plan exports the managed project and compares its Tools, Text Resources and
Prompts with the bundle it would import (:mod:`~ignition_rest_mcp.cli.gateway_ops.bundle_content`).
A difference at the same bundle version is a hand edit: the plan names it, and the
restore needs the ``overwrite_hand_edit`` Explicit acceptance (D32 section 10).

The Gateway writes go through the curated writer in ``cli/gateway_ops``, so the guards, the
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
from urllib.parse import quote, urlsplit

import httpx

from ignition_rest_mcp.cli.engine.deployment import CREATED_KEY, DIRECTORY_MODE, read_secret, remove_secret
from ignition_rest_mcp.cli.engine.errors import CliError, ErrorCode
from ignition_rest_mcp.cli.engine.main import (
    COMMANDS,
    ApplyContext,
    Context,
    Plan,
    Stage,
    _default_roles,
    register_stage,
)
from ignition_rest_mcp.cli.engine.report import Status
from ignition_rest_mcp.cli.engine.resolve import PROG, TOKEN_CHECKS, InputSpec, Kind, Needed, Risk, Source
from ignition_rest_mcp.cli.gateway_ops import bundle_content
from ignition_rest_mcp.cli.gateway_ops import documents as docs
from ignition_rest_mcp.cli.gateway_ops import gateway as gw
from ignition_rest_mcp.cli.gateway_ops import install_module, security, verify
from ignition_rest_mcp.cli.gateway_ops.action import needs_acknowledgement, upgrade_class
from ignition_rest_mcp.cli.gateway_ops.policy import confirm_policy
from ignition_rest_mcp.cli.gateway_ops.inputs import (
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
from ignition_rest_mcp.cli.gateway_ops.mcp_http import McpHttpClient, McpMethodNotFound, McpProbeError
from ignition_rest_mcp.cli.gateway_ops.writer import GatewayWriter, WriteError
from ignition_rest_mcp.cli.setup.start import DATA_DIRECTORY as REST_DATA_DIRECTORY
from ignition_rest_mcp.errors import GatewayError
from ignition_rest_mcp.projects import designers
from ignition_rest_mcp.projects.fingerprint import project_fingerprint
from ignition_rest_mcp.projects.locks import SINGLE_WRITER_LIMITATION, ProjectFileLock, project_lock_path
from ignition_rest_mcp.projects.zip_safety import UnsafeArchiveError

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

#: ``created`` record entries for the resources this stage creates (issue #76 review).
#: ``reset`` deletes only what a record names, so an entry is written after the write
#: that created the resource, never for one that was already there.
MODULE_RECORD = "module"
POLICY_RECORD = "policy"
PROJECT_RECORD = f"project:{PROJECT}"
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
SERVER_CONFIG_DELETE_PATH = "/data/api/v1/resources/com.inductiveautomation.mcp/server-config/{name}/{signature}"
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

    @property
    def level_record(self) -> str:
        """The ``created`` entry for this role's Security Level (issue #76 review)."""

        return f"level:{self.level_path}"

    @property
    def token_record(self) -> str:
        """The ``created`` entry for this role's Gateway API token."""

        return f"runtime-token:{self.token}"

    @property
    def config_record(self) -> str:
        """The ``created`` entry for this role's Server Config."""

        return f"server-config:{self.server_config}"

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


def saved_roles(ctx: Context) -> list[Role]:
    """The Assistant roles the saved deployment serves, in this module's order.

    ``status`` and ``reset`` read the roles from ``deployment.toml`` rather than from
    this run's flags: both report or remove what a previous ``setup`` created.
    """

    names = ctx.deployment.values.get("roles")
    saved = [str(name) for name in names] if isinstance(names, list) else []
    return [role for name, role in ROLES.items() if name in saved]


#: The setup key as ``status`` and ``reset`` take it: a file, with no Gateway check.
#: Both must report or refuse before they contact the Gateway, so a probe and the D32
#: section 9 permission check would come too early: an unreachable Gateway would mask
#: ``reset``'s prod refusal and ``status``'s own lines.
OBSERVED_TOKEN_INPUT = InputSpec(
    name="gateway_token",
    flag="--gateway-token-file",
    question=(
        "file that holds the Gateway API key whose Security Level is ticked under every "
        "permission in Security > General Settings"
    ),
    kind=Kind.SECRET,
    secret_name="gateway-token",
)


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
        """The manifest subset the document builders and the verify sequence read.

        The build is stamped with the checkout's revision, but a deployed bundle of the
        same version may carry an older one, so ``bundle_info`` compares the version only.
        """

        return {"bundleVersion": self.version, "sourceRevision": "UNSTAMPED", "profileInventories": self.inventories}

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
        # Every Server Config serves the bundle's Resources and Prompts with "*", so each
        # role's endpoint lists the bundled ones, which the readonly profile names.
        for profile in inventories:
            for key in ("resources", "prompts"):
                inventories[profile][key] = list(inventories["readonly"][key])
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
class RemovalPlan:
    """A role this deployment no longer serves, and what of it the Gateway still holds."""

    role: Role
    config_signature: str = ""
    token_signature: str = ""
    level: bool = False
    files: list[str] = field(default_factory=list)
    #: What the Gateway holds of the role that the ``created`` record does not name.
    kept: list[str] = field(default_factory=list)


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
    #: The saved environment, when this run moves the deployment to another one.
    previous_environment: str = ""
    removals: list[RemovalPlan] = field(default_factory=list)
    #: The bundle archive this run imports, built from the checkout by the plan.
    archive: bytes = b""
    #: The D16 ``pcf1`` fingerprint of the managed project the plan exported.
    project_fingerprint: str = ""
    #: The bundle's Tools, Text Resources and Prompts the managed project holds differently.
    project_drift: list[str] = field(default_factory=list)
    #: The Designer sessions on the managed project the plan showed and dev accepted.
    designer_note: str = ""
    #: What the plan's Designer listing held; the check under the lock compares with it.
    designer_view: DesignerView | None = None

    @property
    def install_module(self) -> bool:
        return self.installed is None or self.installed.build != self.artifact.build

    @property
    def project_action(self) -> str:
        """``create``, ``update`` (another bundle version), ``restore`` (a hand edit) or ``none``."""

        if self.project.classification == gw.ABSENT:
            return "create"
        if self.project.bundle_version != self.bundle.version:
            return "update"
        return "restore" if self.project_drift else "none"


@dataclass(frozen=True, slots=True)
class RuntimeTargets:
    """What a read-only role check needs from a Runtime plan.

    :func:`role_inputs` and :func:`closing_check` take this or a whole
    :class:`RuntimePlan`, so ``status`` checks a deployment without building a plan.
    """

    bundle: Bundle
    endpoint: Endpoint
    insecure_channel: bool


def role_inputs(plan: RuntimePlan | RuntimeTargets, role: Role, mcp_token: str | None = None) -> Inputs:
    """The ``Inputs`` for one role. Only the closing check gets the role's token."""

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
        mcp_token=mcp_token,
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
    previous = _environment_change(ctx)
    roles = [ROLES[name] for name in ctx.resolved.list("roles")]
    endpoint = _endpoint_of(ctx.resolved.values["gateway_url"])
    module_file = Path(ctx.resolved.values["module_file"])
    artifact = _artifact(module_file, endpoint)
    observed = asyncio.run(_observe(ctx, list(ROLES.values())))
    installed, project, levels_doc, token_docs, config_docs, policy = observed

    if installed is not None and install_module.classify_build(installed, artifact) == install_module.REFUSED:
        raise CliError(
            ErrorCode.STEP_FAILED,
            f"the Gateway runs MCP Module build {installed.build or 'unreadable'} and the pinned file "
            f"{module_file} is build {artifact.build}; a lower build is never installed",
            next_action=f"curl -sS {endpoint.url}{gw.MODULES_PATH}",
        )
    if installed is not None:
        problem = install_module.state_problem(installed)
        if problem:
            raise CliError(
                ErrorCode.MODULE_NOT_ACTIVE,
                f"MCP Module build {installed.build or installed.raw_version} is not ACTIVE, so setup stops "
                f"before it writes anything: {problem}. A Module the Gateway does not run hosts no Server "
                "Config route",
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
    archive, fingerprint, drift = _plan_project(ctx, bundle, project, endpoint)
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
        previous_environment=previous,
        archive=archive,
        project_fingerprint=fingerprint,
        project_drift=drift,
    )
    changes: list[str] = []
    needed: list[Needed] = []
    if previous:
        changes.append(f"move the deployment from {previous} to {environment} and use the {environment} defaults")
    if plan.install_module:
        verb = "install" if installed is None else f"upgrade build {installed.build} to"
        changes.append(
            f"{verb} MCP Module build {artifact.build} from {module_file}, accept its certificate and EULA, "
            "and restart the Gateway"
        )
        detail = f"MCP Module build {artifact.build}"
        needed += [Needed(Risk.CERTIFICATE, detail), Needed(Risk.EULA, detail), Needed(Risk.RESTART, detail)]
        if installed is not None:
            needed.append(Needed(Risk.MODULE_UPGRADE, f"build {installed.build} to {artifact.build}"))
    if plan.project_action == "create":
        changes.append(f"deploy the Runtime bundle {bundle.version} as the managed project {PROJECT}")
    elif plan.project_action == "update":
        kind = upgrade_class(project.bundle_version, bundle.version)
        changes.append(
            f"replace the managed bundle {project.bundle_version} with {bundle.version} ({kind}) in project "
            f"{PROJECT}, after a backup into {ctx.deployment.directory / BACKUP_DIR}"
        )
        if needs_acknowledgement(kind):
            needed.append(Needed(Risk.BUNDLE_UPGRADE, f"{kind}: {project.bundle_version} to {bundle.version}"))
    elif plan.project_action == "restore":
        edited = bundle_content.summary(drift)
        changes.append(
            f"restore the managed project {PROJECT} to bundle {bundle.version}, after a backup into "
            f"{ctx.deployment.directory / BACKUP_DIR}; its content differs from the bundle, so someone changed "
            f"it by hand: {edited}"
        )
        needed.append(Needed(Risk.OVERWRITE_HAND_EDIT, f"project {PROJECT}: {edited}"))
    if plan.project_action in ("update", "restore"):
        plan.designer_view = asyncio.run(_designer_view(ctx))
        plan.designer_note = plan.designer_view.note
        if plan.designer_note and environment == "prod":
            raise CliError(
                ErrorCode.CONFLICT,
                f"{plan.designer_note}; in prod setup never replaces the managed project while a Designer may "
                "hold unsaved work on it. Nothing was written",
                next_action=_close_designer(ctx),
            )
        if plan.designer_note:
            changes.append(
                f"replace the managed project {PROJECT} although {plan.designer_note}; unsaved work there is lost"
            )
            needed.append(Needed(Risk.OVERWRITE_HAND_EDIT, f"project {PROJECT}: {plan.designer_note}"))
    for role in new_levels:
        changes.append(f"create the Security Level {role.level_path}")
    for removal in _plan_removals(ctx, roles, tree, token_docs, config_docs):
        plan.removals.append(removal)
        what = [
            name
            for name, present in (
                (f"the Server Config {removal.role.server_config}", removal.config_signature),
                (f"the API token {removal.role.token}", removal.token_signature),
                (f"the Security Level {removal.role.level_path}", removal.level),
            )
            if present
        ] + [str(ctx.deployment.directory / name) for name in removal.files]
        if what:
            changes.append(
                f"remove the {removal.role.name} role, which this run no longer deploys: {', '.join(what)}"
            )
        for kept in removal.kept:
            changes.append(_left(kept))

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


def _environment_change(ctx: Context) -> str:
    """The saved environment when this run changes it, else ``""``.

    D32 section 5: the new environment's defaults replace the saved roles, unless
    ``--roles`` sets them in this run. The policy allowlists always follow the
    environment, so they need nothing here.
    """

    saved = ctx.deployment.values.get("environment")
    environment = ctx.resolved.values["environment"]
    if not isinstance(saved, str) or saved == environment:
        return ""
    if ctx.resolved.sources.get("roles") is Source.SAVED:
        ctx.resolved.values["roles"] = _default_roles(ctx.resolved.values)
        ctx.resolved.sources["roles"] = Source.DEFAULT
    return saved


def _plan_removals(
    ctx: Context,
    roles: list[Role],
    tree: list[dict[str, Any]],
    token_docs: dict[str, dict[str, Any] | None],
    config_docs: dict[str, dict[str, Any] | None],
) -> list[RemovalPlan]:
    """The saved roles this run no longer deploys, with what of each is still there."""

    saved = ctx.deployment.values.get("roles")
    record = ctx.deployment.values.get(CREATED_KEY)
    created = {str(item) for item in record} if isinstance(record, list) else set()
    keep = {role.name for role in roles}
    removals: list[RemovalPlan] = []
    for name in saved if isinstance(saved, list) else []:
        role = ROLES.get(name)
        if role is None or name in keep:
            continue
        found = security.find_level(tree, role.level)
        level = found is not None and found[0] == [SECURITY_LEVEL_PARENT, role.level] and not (
            security.level_shape_problem(found[1])
        )
        config_signature = str((config_docs.get(name) or {}).get("signature") or "")
        token_signature = str((token_docs.get(name) or {}).get("signature") or "")
        # A token left in place keeps its secret file, so the token stays usable.
        secret_file = ctx.deployment.secret_path(role.secret).name
        keep_secret = bool(token_signature) and role.token_record not in created
        files = [
            file
            for file in (secret_file, PERMISSIONS_FILE.format(role=role.name))
            if (ctx.deployment.directory / file).exists() and not (keep_secret and file == secret_file)
        ]
        kept = [
            what
            for what, present, entry in (
                (f"the Server Config {role.server_config}", config_signature, role.config_record),
                (f"the API token {role.token}", token_signature, role.token_record),
                (f"the Security Level {role.level_path}", level, role.level_record),
            )
            if present and entry not in created
        ]
        removal = RemovalPlan(
            role,
            config_signature=config_signature if role.config_record in created else "",
            token_signature=token_signature if role.token_record in created else "",
            level=level and role.level_record in created,
            files=files,
            kept=kept,
        )
        if removal.config_signature or removal.token_signature or removal.level or removal.files or removal.kept:
            removals.append(removal)
    return removals


def _left(what: str) -> str:
    """The plan line and step reason for a resource the ``created`` record does not name."""

    return f"leave {what} in place: the deployment does not record that setup created it"


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


def _plan_project(
    ctx: Context, bundle: Bundle, project: gw.ProjectState, endpoint: Endpoint
) -> tuple[bytes, str, list[str]]:
    """The bundle archive, and for a managed project its ``pcf1`` and its content drift.

    Drift is looked for only at the bundle version the checkout has. Across versions
    every changed Tool differs, and the replace already reports the version change.
    """

    with tempfile.TemporaryDirectory(prefix="ignition-mcp-bundle-") as scratch:
        archive = build_bundle(bundle.checkout, Path(scratch) / f"ignition-runtime-bundle-{bundle.version}.zip")
    if not project.managed:
        return archive, "", []
    try:
        served = asyncio.run(_export_project(ctx))
    except gw.GatewayProbeError as error:
        raise CliError(
            ErrorCode.STEP_FAILED,
            f"the managed project {PROJECT} could not be exported ({error})",
            next_action=f"curl -sS -o /dev/null -w '%{{http_code}}' {endpoint.url}"
            f"{gw.PROJECT_EXPORT_PATH.format(name=PROJECT)}",
        ) from error
    fingerprint = _fingerprint(ctx, served)
    drift = bundle_content.differences(served, archive) if project.bundle_version == bundle.version else []
    return archive, fingerprint, drift


@dataclass(frozen=True, slots=True)
class DesignerView:
    """One read of the Gateway's Designer sessions on the managed project."""

    #: ``(id, user)`` of each active session.
    sessions: frozenset[tuple[str, str]]
    #: Whether the Gateway provided the listing at all.
    readable: bool
    #: ``""``, or what holds or may hold the project, for the plan and the refusal.
    note: str

    def news(self, planned: DesignerView) -> str:
        """What this later read shows that ``planned`` did not; ``""`` when nothing.

        A session the plan did not list, or a listing that has become unreadable,
        refuses the import in both environments (issue #80 review round 2).
        """

        if not self.readable and planned.readable:
            return self.note
        added = self.sessions - planned.sessions
        if not added:
            return ""
        who = sorted(user or identifier or "unnamed" for identifier, user in added)
        return f"{len(added)} Designer session(s) the plan did not list now hold the project {PROJECT} " \
            f"({', '.join(who[:5])}{f' and {len(who) - 5} more' if len(who) > 5 else ''})"


async def _designer_view(ctx: Context, client: gw.GatewayRest | None = None) -> DesignerView:
    """D16's Designer-session check on the managed project.

    A listing the Gateway cannot provide counts as a session, because an open
    Designer cannot be ruled out (coordinator ruling on issue #80).
    """

    async def read(reader: gw.GatewayRest) -> list[dict[str, str]]:
        async def fetch(limit: int, offset: int) -> Any:
            return await reader.get_json(
                designers.DESIGNERS_PATH, params={"limit": str(limit), "offset": str(offset)}
            )

        return await designers.list_project_sessions(fetch, PROJECT)

    try:
        if client is not None:
            sessions = await read(client)
        else:
            async with ctx.gateway_reader(SETTINGS.transport) as reader:
                sessions = await read(reader)
    except (GatewayError, gw.GatewayProbeError) as error:
        return DesignerView(
            frozenset(),
            False,
            f"the Gateway's Designer session listing could not be read ({error}), so an open Designer on "
            f"{PROJECT} cannot be ruled out",
        )
    found = frozenset((session["id"], session["user"]) for session in sessions)
    if not sessions:
        return DesignerView(found, True, "")
    who = [session["user"] or session["id"] or "unnamed" for session in sessions]
    shown = ", ".join(who[:5]) + (f" and {len(who) - 5} more" if len(who) > 5 else "")
    return DesignerView(found, True, f"{len(sessions)} active Designer session(s) hold the project {PROJECT} ({shown})")


def _close_designer(ctx: Context) -> str:
    return f"close the Designer on {PROJECT}, then run: {PROG} setup --deployment {ctx.deployment.name}"


async def _export_project(ctx: Context) -> bytes:
    async with ctx.gateway_reader(SETTINGS.transport) as client:
        return await client.project_archive(PROJECT)


def _fingerprint(ctx: Context, archive: bytes) -> str:
    """The D16 ``pcf1`` fingerprint of one export, after the D15 ZIP safety gate."""

    with tempfile.TemporaryDirectory(prefix="ignition-mcp-export-") as scratch:
        path = Path(scratch) / f"{PROJECT}.zip"
        path.write_bytes(archive)
        try:
            return project_fingerprint(str(path))
        except UnsafeArchiveError as error:
            raise _failed(f"the export of {PROJECT} is not a safe project archive: {error}", ctx) from error


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
        drift = _token_drift(role, document, insecure_channel=plan.insecure_channel)
        if not drift:
            return TokenPlan("none", "", document)
        if not (plan.previous_environment and drift.startswith("it has secureChannelRequired")):
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


def _token_drift(role: Role, document: dict[str, Any], *, insecure_channel: bool) -> str:
    config = document.get("config")
    profile = config.get("profile") if isinstance(config, dict) else None
    secure = profile.get("secureChannelRequired") if isinstance(profile, dict) else None
    held = token_levels(document)
    if held != [(SECURITY_LEVEL_PARENT, role.level)]:
        return f"it grants {_dotted(held)}, not {role.level_path}"
    if secure is not (not insecure_channel):
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
    narrowing = "narrow" if plan.environment == "prod" else "replace"
    return f"{narrowing} the Runtime Target Policy to the {plan.environment} one ({summary})"


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
            if plan.installed is None:
                # A fresh install is this deployment's resource. A replaced older build
                # is not: the Module existed before this run.
                ctx.record_created(MODULE_RECORD)
            end.set(Status.CHANGED, f"installed build {plan.artifact.build} from {plan.module_file}; restarted")
        else:
            end.set(Status.OK, f"build {plan.artifact.build} is installed; the pinned file is {plan.module_file}")
    with ctx.reporter.step("runtime bundle", f"project {PROJECT}") as end:
        if plan.project_action == "none":
            end.set(Status.OK, f"managed bundle {plan.bundle.version} is deployed and holds the bundle's content")
        else:
            result = _deploy_bundle(ctx, plan)
            if plan.project_action == "create":
                ctx.record_created(PROJECT_RECORD)
            end.set(Status.CHANGED, result)
    for removal in plan.removals:
        with ctx.reporter.step(f"runtime remove {removal.role.name}", "a role this run no longer deploys") as end:
            if removal.config_signature or removal.token_signature or removal.files:
                end.set(Status.CHANGED, asyncio.run(_remove_role(ctx, removal)))
            else:
                end.set(Status.SKIPPED, "nothing of the role that setup created is left on the Gateway")
        if removal.kept:
            with ctx.reporter.step(f"runtime keep {removal.role.name}", "resources setup did not create") as end:
                end.set(Status.SKIPPED, "; ".join(_left(what) for what in removal.kept))
    removed_levels = [removal.role for removal in plan.removals if removal.level]
    with ctx.reporter.step("runtime security levels", ", ".join(r.level_path for r in plan.roles)) as end:
        if plan.new_levels or removed_levels:
            asyncio.run(_write_levels(ctx, plan, removed_levels))
            ctx.record_created(*(role.level_record for role in plan.new_levels))
            done = [f"created {r.level_path}" for r in plan.new_levels]
            done += [f"removed {r.level_path}" for r in removed_levels]
            end.set(Status.CHANGED, ", ".join(done))
        else:
            end.set(Status.OK, "every role's level is present")
    for role in plan.roles:
        token = plan.tokens[role.name]
        with ctx.reporter.step(f"runtime token {role.name}", role.token) as end:
            if token.action == "none":
                end.set(Status.OK, f"exists and matches {ctx.deployment.secret_path(role.secret)}")
            else:
                result = asyncio.run(_write_token(ctx, plan, role, token))
                if token.action in ("create", "recreate"):
                    # A recreated token is this run's resource: the served one is the new
                    # one, and the old one is gone.
                    ctx.record_created(role.token_record)
                end.set(Status.CHANGED, result)
        config = plan.configs[role.name]
        with ctx.reporter.step(f"runtime server config {role.name}", role.server_config) as end:
            if config.action == "none":
                end.set(Status.OK, f"{len(plan.bundle.tools(role))} Tools and the generated permissions tree")
            else:
                result = asyncio.run(_write_config(ctx, plan, role, config))
                if config.action == "create":
                    ctx.record_created(role.config_record)
                end.set(Status.CHANGED, result)
    with ctx.reporter.step("runtime policy", docs.POLICY_PATH) as end:
        if plan.policy_action == "none":
            end.set(Status.OK, "the served document is the generated one")
        else:
            result = asyncio.run(_write_policy(ctx, plan))
            if plan.policy_action == "create":
                ctx.record_created(POLICY_RECORD)
            end.set(Status.CHANGED, result)
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
            else:
                if identity is not None:
                    build = identity.build or identity.raw_version
                    problem = install_module.state_problem(identity)
                    if identity.build == artifact.build and not problem:
                        return
                    last = f"it serves build {build}" + (f": {problem}" if problem else "")
            if attempt + 1 < polls:
                await SETTINGS.sleep(RESTART_POLL_SECONDS)
    raise CliError(
        ErrorCode.MODULE_NOT_ACTIVE,
        f"the Module did not come back ACTIVE as build {artifact.build} within {RESTART_READY_SECONDS:g} s "
        f"({last})",
        next_action=f"{PROG} status --deployment {ctx.deployment.name}",
    )


def _project_lock(ctx: ApplyContext) -> ProjectFileLock:
    """The D16 writer lock for the managed project, shared with this deployment's REST server.

    ``start`` gives the REST server the data directory ``rest-data`` and the Gateway
    ID of the deployment's name, and the server takes this same lock file for each
    project Mutation. It is held for one project write, so a running REST server
    does not keep ``setup`` out, and a second ``setup`` run is refused while it is held.
    """

    data = ctx.deployment.directory / REST_DATA_DIRECTORY
    data.mkdir(mode=DIRECTORY_MODE, parents=True, exist_ok=True)
    lock = ProjectFileLock(project_lock_path(data, ctx.deployment.name, PROJECT))
    if not lock.try_acquire():
        raise CliError(
            ErrorCode.CONFLICT,
            f"another setup run or this deployment's REST server is writing the project {PROJECT} "
            f"({lock.path} is locked); nothing was imported. D16 limits the lock to writers on this machine "
            f"({SINGLE_WRITER_LIMITATION})",
            next_action=f"{PROG} setup --deployment {ctx.deployment.name}",
        )
    return lock


def _deploy_bundle(ctx: ApplyContext, plan: RuntimePlan) -> str:
    lock = _project_lock(ctx)
    try:
        return asyncio.run(_import_bundle(ctx, plan))
    finally:
        lock.release()


def _conflict(ctx: ApplyContext, what: str, backup: Path) -> CliError:
    return CliError(
        ErrorCode.CONFLICT,
        f"the managed project {PROJECT} changed on the Gateway {what}, for example through a Designer save or "
        f"another writer; nothing was imported. The baseline is in {backup}",
        next_action=f"{PROG} setup --deployment {ctx.deployment.name}",
    )


async def _import_bundle(ctx: ApplyContext, plan: RuntimePlan) -> str:
    """Import the bundle; a replace follows D16's baseline, re-checks and read-back.

    Only the managed project is replaced here, and the writer lock is held by the
    caller. The baseline export is the durable backup, so a failure after it leaves
    the recovery copy in place. Nothing is retried after the import was sent: an
    import without a clear answer is reconciled from a fresh export instead.
    """

    archive = plan.archive
    replace = plan.project_action != "create"
    note = ""
    baseline = ""
    backup: Path | None = None
    async with ctx.gateway_writer(SETTINGS.transport) as writer:
        try:
            if replace:
                backup = _backup(ctx, plan, await writer.project_export(PROJECT))
                baseline = _fingerprint(ctx, backup.read_bytes())
                if baseline != plan.project_fingerprint:
                    raise _conflict(ctx, "after the plan read it", backup)
                if _fingerprint(ctx, await writer.project_export(PROJECT)) != baseline:
                    raise _conflict(ctx, "between the baseline export and the import", backup)
                planned = plan.designer_view or DesignerView(frozenset(), True, "")
                designer = (await _designer_view(ctx, writer.reads)).news(planned)
                if designer:
                    raise CliError(
                        ErrorCode.CONFLICT,
                        f"{designer} since the plan read the Gateway; nothing was imported. The baseline is in "
                        f"{backup}",
                        next_action=_close_designer(ctx),
                    )
                note = f"; backed up to {backup}, whose pcf1 matched the project just before the import"
            try:
                await writer.import_project(PROJECT, archive, overwrite=replace)
            except WriteError as error:
                # D16: a lost answer or a 5xx leaves the outcome open; a 4xx or an
                # explicit refusal inside a 2xx body is a definite "not applied".
                if 0 < error.status < 500:
                    raise
                return await _reconcile(ctx, plan, writer, baseline, backup, str(error)) + note
            state = await writer.reads.find_project(PROJECT)
            result = await writer.project_export(PROJECT)
        except (WriteError, gw.GatewayProbeError) as error:
            raise _failed(f"the bundle import stopped: {error}", ctx) from error
    if not state.managed or state.bundle_version != plan.bundle.version:
        raise _failed(
            f"the import was accepted but {PROJECT} reads back as {state.classification} "
            f"bundle {state.bundle_version}",
            ctx,
        )
    _fingerprint(ctx, result)
    drift = bundle_content.differences(result, archive)
    if drift:
        raise _failed(
            f"the import was accepted but {PROJECT} reads back other content: {bundle_content.summary(drift)}", ctx
        )
    return f"deployed managed bundle {plan.bundle.version} ({len(archive)} bytes) and read it back equal{note}"


async def _reconcile(
    ctx: ApplyContext, plan: RuntimePlan, writer: GatewayWriter, baseline: str, backup: Path | None, lost: str
) -> str:
    """D16's reconciliation after an import without a clear answer: export C and compare.

    C equal to the bundle means the import was applied. C equal to baseline A, or no
    project where setup was creating one, means it was not applied. Anything else is
    an unknown outcome, and setup stops without a retry.
    """

    kept = f" The baseline is in {backup}." if backup is not None else ""
    try:
        state = await writer.reads.find_project(PROJECT)
        current = None if state.classification == gw.ABSENT else await writer.project_export(PROJECT)
    except (WriteError, gw.GatewayProbeError) as error:
        raise _failed(
            f"the import gave no clear answer ({lost}) and {PROJECT} could not be read back ({error}), so the outcome "
            f"is unknown and setup does not retry.{kept}",
            ctx,
        ) from error
    if (
        current is not None
        and state.managed
        and state.bundle_version == plan.bundle.version
        and not bundle_content.differences(current, plan.archive)
    ):
        return (
            f"deployed managed bundle {plan.bundle.version} ({len(plan.archive)} bytes); the import gave no clear answer "
            f"({lost}), and the project read back afterwards holds the bundle"
        )
    unchanged = current is None if not baseline else current is not None and _fingerprint(ctx, current) == baseline
    if unchanged:
        raise CliError(
            ErrorCode.STEP_FAILED,
            f"the import gave no clear answer ({lost}) and {PROJECT} reads back unchanged, so the import was not "
            f"applied; re-running setup is safe.{kept}",
            next_action=f"{PROG} setup --deployment {ctx.deployment.name}",
        )
    raise _failed(
        f"the import gave no clear answer ({lost}) and {PROJECT} reads back neither as before nor as the bundle, so the "
        f"outcome is unknown and setup does not retry.{kept}",
        ctx,
    )


def _backup(ctx: ApplyContext, plan: RuntimePlan, archive: bytes) -> Path:
    """D32 section 7 and D16: the baseline export, stored before the project is replaced."""

    directory = ctx.deployment.directory / BACKUP_DIR
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    stem = f"{PROJECT}-{plan.project.bundle_version}"
    path = directory / f"{stem}.zip"
    counter = 1
    while path.exists():
        counter += 1
        path = directory / f"{stem}-{counter}.zip"
    staging = path.with_name(path.name + ".tmp")
    with staging.open("wb") as stream:
        stream.write(archive)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(staging, path)
    return path


async def _remove_role(ctx: ApplyContext, removal: RemovalPlan) -> str:
    """Delete a dropped role's Server Config and token, then its local files."""

    role = removal.role
    done: list[str] = []
    async with ctx.gateway_writer(SETTINGS.transport) as writer:
        try:
            # The writer has no named delete; its single write method still checks the gate.
            if removal.config_signature:
                await writer._write(
                    "DELETE",
                    SERVER_CONFIG_DELETE_PATH.format(
                        name=role.server_config, signature=quote(removal.config_signature, safe="")
                    ),
                    body=b"", content_type="application/json", action="delete server config",
                )
                done.append(f"Server Config {role.server_config}")
            if removal.token_signature:
                await writer._write(
                    "DELETE",
                    API_TOKEN_DELETE_PATH.format(name=role.token, signature=quote(removal.token_signature, safe="")),
                    body=b"", content_type="application/json", action="delete API token",
                )
                done.append(f"API token {role.token}")
        except WriteError as error:
            raise _failed(f"the {role.name} role was not removed: {error}", ctx) from error
    for name in removal.files:
        (ctx.deployment.directory / name).unlink(missing_ok=True)
        done.append(name)
    return "removed " + ", ".join(done)


def _without_level(tree: list[dict[str, Any]], level: str) -> list[dict[str, Any]]:
    """The tree without one leaf under ``Authenticated``; every other node travels verbatim."""

    result: list[dict[str, Any]] = []
    for node in tree:
        children = node.get("children")
        if node.get("name") == SECURITY_LEVEL_PARENT and isinstance(children, list):
            node = {**node, "children": [c for c in children if not (isinstance(c, dict) and c.get("name") == level)]}
        result.append(node)
    return result


def _levels_problem(
    served: dict[str, Any] | None, before: list[dict[str, Any]], created: list[Role], removed: list[Role]
) -> str:
    """How the served tree differs from the edit; ``""`` when it landed as written."""

    tree = security.level_tree(served)
    if tree is None:
        return "the Security Level tree is not readable after the write"
    paths = security.level_paths(tree)
    for role in created:
        if (SECURITY_LEVEL_PARENT, role.level) not in paths:
            return f"{role.level_path} is not in the served tree"
    gone = {(SECURITY_LEVEL_PARENT, role.level) for role in removed}
    if paths & gone:
        return "a removed level is still in the served tree"
    dropped = sorted(security.level_paths(before) - paths - gone)
    if dropped:
        return "the write dropped other levels: " + ", ".join("/".join(path) for path in dropped)
    return ""


async def _write_levels(ctx: ApplyContext, plan: RuntimePlan, removed: list[Role]) -> None:
    tree = plan.levels
    for role in plan.new_levels:
        merged, reason = security.with_managed_level(tree, _level_inputs(role))
        if merged is None:
            raise _failed(reason, ctx)
        tree = merged
    for role in removed:
        tree = _without_level(tree, role.level)
    async with ctx.gateway_writer(SETTINGS.transport) as writer:
        try:
            # The Module install restarts the Gateway after the plan read the tree, and a
            # restart gives the singleton a new signature. The precondition is the
            # signature read now, and only while the tree is still the one the plan showed.
            current = await writer.reads.singleton_document(SECURITY_LEVELS_TYPE)
            if security.level_tree(current) != plan.levels:
                raise _failed("the Security Level tree changed on the Gateway after the plan read it", ctx)
            signature = str((current or {}).get("signature") or "")
            await writer.update_security_levels(
                tree, signature=signature, collection=security.singleton_collection(current)
            )
            served = await writer.reads.singleton_document(SECURITY_LEVELS_TYPE)
        except (WriteError, gw.GatewayProbeError) as error:
            raise _failed(f"the Security Level edit stopped: {error}", ctx) from error
    problem = _levels_problem(served, plan.levels, plan.new_levels, removed)
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
            await confirm_policy(writer, docs.Documents(policy_text=text))
        except (WriteError, gw.GatewayProbeError) as error:
            raise _failed(f"the Runtime Target Policy was not written: {error}", ctx) from error
    return f"wrote {docs.byte_length(text)} bytes in {outcome.attempt_count} import(s); read back equal"


# ------------------------------------------------------------ the closing check


async def _check_role(ctx: ApplyContext, plan: RuntimePlan, role: Role) -> str:
    """D20's verify sequence at the role's endpoint, with the role's own token.

    The check reads only. The one exception is a Server Config this same run wrote:
    when its endpoint serves no Tool yet, the planned document is announced again,
    because the Module built the server before the project's Tools registered.
    """

    path = ctx.deployment.secret_path(role.secret)
    try:
        token = read_secret(path)
    except CliError:
        token = ""
    ctx.reporter.hide(token)
    refreshes = 0
    if plan.configs[role.name].action != "none" and token:
        refreshes = await _await_tools(ctx, plan, role, token)
    summary = await closing_check(ctx, plan, role, token)
    note = f", after {refreshes} re-announcement(s) of the Server Config this run wrote" if refreshes else ""
    return summary + note


async def closing_check(ctx: Context, plan: RuntimePlan | RuntimeTargets, role: Role, token: str) -> str:
    """The verify sequence at the role's endpoint with the role's token. Reads only.

    ``status`` reports this as a check of its own, so it must not write: the
    re-announcement :func:`_check_role` may make stays there.
    """

    ctx.reporter.hide(token)
    report, _, code = await verify.collect(
        role_inputs(plan, role, mcp_token=token or None), mcp_transport=SETTINGS.transport
    )
    checks = [check for check in report["checks"] if isinstance(check, dict)]
    if code != 0:
        failed = [check for check in checks if check.get("status") not in ("PASS", "NOT_APPLICABLE")]
        if any("HTTP 403" in str(check.get("detail")) for check in failed):
            raise _failed(await _explain_403(ctx, plan.endpoint, role, bool(token)), ctx)
        detail = "; ".join(f"{check.get('name')}: {check.get('detail')}" for check in failed[:3])
        raise _failed(f"the {role.name} endpoint failed {len(failed)} check(s): {detail}", ctx)
    return (
        f"{len(checks)} checks passed with the role's token: initialize, the exact Tool, Resource and "
        f"Prompt inventories of {role.profile}, resources/read, prompts/get and bundle_info"
    )


async def _await_tools(ctx: ApplyContext, plan: RuntimePlan, role: Role, token: str) -> int:
    """Wait, bounded, until the endpoint of a Server Config written in this run serves Tools."""

    endpoint = _endpoint_of(plan.endpoint.url, f"/data/mcp/{role.server_config}")
    for attempt in range(REFRESH_ATTEMPTS + 1):
        try:
            async with McpHttpClient(endpoint, token, transport=SETTINGS.transport) as client:
                await client.initialize()
                tools = await client.tools_list() if client.advertises("tools") else []
        except McpMethodNotFound:
            tools = []
        except McpProbeError:
            return attempt  # verify reports the failure and its cause
        if tools or attempt == REFRESH_ATTEMPTS:
            return attempt
        await SETTINGS.sleep(REFRESH_WAIT_SECONDS)
        await _reannounce(ctx, plan, role)
    return REFRESH_ATTEMPTS


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


async def _explain_403(ctx: Context, endpoint: Endpoint, role: Role, token_sent: bool) -> str:
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
    if endpoint.scheme == "http" and profile.get("secureChannelRequired") is True:
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
        help="delete and recreate every managed token whose secret file is lost, on either plane",
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
