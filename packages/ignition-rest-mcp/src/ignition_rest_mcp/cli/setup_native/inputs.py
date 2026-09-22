"""Validated command inputs for ``ignition-mcp setup-native`` (D20, D21).

Everything the CLI wants to see is derived from two operator-provided artifacts:
the bundle manifest JSON (the whole desired state, validated structurally here)
and optionally the bundle ZIP it describes (SHA-256 verified).  Credentials come
from ``0600`` files or environment variables and are never echoed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn, Sequence
from urllib.parse import urlsplit

COMMANDS = ("doctor", "plan", "verify", "apply")
PROFILE_NAMES = ("readonly", "operator", "configurator", "full")

#: The Resource type and collection ``apply`` writes the MCP Server Config through
#: (D20: Native REST only, never the filesystem). D30 §5 refuses that type to the
#: generic config Mutations, which is why ``apply`` has its own curated writer.
SERVER_CONFIG_TYPE = "com.inductiveautomation.mcp/server-config"
CONFIG_COLLECTION = "core"

ENV_GATEWAY_URL = "IGNITION_MCP_SETUP_GATEWAY_URL"
ENV_MCP_URL = "IGNITION_MCP_SETUP_MCP_URL"
ENV_GATEWAY_TOKEN = "IGNITION_MCP_SETUP_GATEWAY_TOKEN"
ENV_MCP_TOKEN = "IGNITION_MCP_SETUP_MCP_TOKEN"

DEFAULT_BUNDLE_PROJECT = "ignition_runtime"
DEFAULT_TIMEOUT_SECONDS = 10.0
MAX_TIMEOUT_SECONDS = 120.0
MCP_TIMEOUT_SECONDS = 30.0

MANIFEST_KEYS = frozenset(
    {
        "schemaVersion",
        "bundleVersion",
        "sourceRevision",
        "resourceSchemaVersion",
        "nativeResponseBindingStatus",
        "artifact",
        "tools",
        "resources",
        "prompts",
        "toolRequirements",
        "profileInventories",
        "testedTuples",
    }
)
ARTIFACT_KEYS = frozenset({"filename", "sha256", "sizeBytes"})
PROFILE_ENTRY_KEYS = frozenset({"permissions", "tools", "resources", "prompts"})
TESTED_TUPLE_KEYS = frozenset(
    {
        "gate",
        "gatewayVersion",
        "gatewayBuild",
        "mcpModuleVersion",
        "mcpModuleBuild",
        "mcpModuleSha256",
        "bundleVersion",
        "compatibilityStatus",
        "nativeResponseBinding",
    }
)
BINDING_STATUSES = frozenset(
    {
        "NATIVE_BINDING_PENDING",
        "VERIFIED",
        "VERIFIED_WITH_LIMITATION",
        "FAILED",
        "FAILED_NATIVE_BINDING",
        "UNVERIFIED_LIMITATION",
        "UNVERIFIED",
    }
)
COMPATIBILITY_STATUSES = frozenset({"SUPPORTED", "UNTESTED", "INCOMPATIBLE", "UNKNOWN"})

_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
#: A resource, project or Server Config name this CLI is willing to address.
NAME_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

EXIT_CODE_DOC = """\
exit codes:
  0  command completed with no FAIL (doctor/verify) and no BLOCKED (plan)
  1  a check failed or a transport error occurred
  2  usage error: bad flags, unreadable/invalid manifest, credential file rejected
  3  plan reports at least one BLOCKED action (apply writes nothing and exits 3)
"""

ENV_DOC = f"""\
environment fallbacks (flags win):
  {ENV_GATEWAY_URL}    Gateway base URL, e.g. http://127.0.0.1:8088
  {ENV_MCP_URL}        Runtime MCP endpoint URL, e.g. http://127.0.0.1:8000/mcp
  {ENV_GATEWAY_TOKEN}  Ignition API token (prefer --gateway-token-file)
  {ENV_MCP_TOKEN}      bearer token for a secured/static-auth MCP endpoint

Token files must be regular, non-symlink files readable only by their owner
(mode 0600) holding exactly one non-empty line.  ``apply`` writes the bundle
project, the Server Config and the Runtime Target Policy through documented
Native REST routes and stops before it writes while any plan line is BLOCKED;
``install-module`` (Phase 6) does not exist yet.
"""


class UsageError(Exception):
    """Bad flag, unreadable artifact, or rejected credential input (exit 2)."""


class UsageParser(argparse.ArgumentParser):
    """Argument parser that reports usage problems as :class:`UsageError`."""

    def error(self, message: str) -> NoReturn:
        raise UsageError(message)


@dataclass(frozen=True, slots=True)
class Endpoint:
    """A validated absolute http(s) URL plus its TCP connect target."""

    url: str
    scheme: str
    host: str
    port: int

    @property
    def tls(self) -> bool:
        return self.scheme == "https"

    @property
    def authority(self) -> str:
        return f"{self.host}:{self.port}"


@dataclass(frozen=True, slots=True)
class Inputs:
    """Everything the setup-native commands are allowed to know."""

    command: str
    manifest_path: Path
    manifest: dict[str, Any]
    gateway_url: Endpoint
    mcp_url: Endpoint | None
    bundle_zip: Path | None
    profile: str
    bundle_project: str
    server_config_name: str | None
    gateway_token: str
    mcp_token: str | None
    timeout_seconds: float
    allow_insecure_authorize: bool
    as_json: bool
    #: ``apply`` inputs (D20). The two documents are loaded and validated by the
    #: command that needs them, so ``plan`` can run without them.
    policy_file: Path | None = None
    permissions_file: Path | None = None
    acknowledge_upgrade: bool = False
    backup_dir: Path | None = None

    @property
    def bundle_version(self) -> str:
        return str(self.manifest["bundleVersion"])

    def profile_inventory(self, key: str) -> list[str]:
        """The selected profile's Tool / Resource / Prompt inventory."""

        return manifest_inventory(self.manifest, self.profile, key)

    def tested_tuples(self) -> list[dict[str, Any]]:
        return tested_tuples(self.manifest)

    def runtime_endpoint(self) -> Endpoint | None:
        """The Runtime MCP endpoint to verify: ``--mcp-url``, else the Server Config path."""

        if self.mcp_url is not None:
            return self.mcp_url
        if self.server_config_name is None:
            return None
        base = self.gateway_url
        return Endpoint(
            url=f"{base.url}/data/mcp/{self.server_config_name}",
            scheme=base.scheme,
            host=base.host,
            port=base.port,
        )


def manifest_inventory(manifest: dict[str, Any], profile: str, key: str) -> list[str]:
    """One inventory of one profile; raises :class:`UsageError` on a manifest the
    structural check should already have rejected."""

    try:
        entry = manifest["profileInventories"][profile][key]
    except (KeyError, TypeError) as error:
        raise UsageError(f"manifest profileInventories.{profile}.{key} is missing") from error
    if not isinstance(entry, list) or any(not isinstance(item, str) for item in entry):
        raise UsageError(f"manifest profileInventories.{profile}.{key} must be a list of strings")
    return [str(item) for item in entry]


def tested_tuples(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    rows = manifest.get("testedTuples")
    if not isinstance(rows, list):
        raise UsageError("manifest testedTuples must be a list")
    return [row for row in rows if isinstance(row, dict)]


def build_base_parser() -> UsageParser:
    """Flags shared by every ``setup-native`` subcommand (no ``--help``)."""

    base = UsageParser(add_help=False)
    flags = base.add_argument_group("setup-native inputs")
    flags.add_argument("--bundle-manifest", required=True, metavar="PATH", help="release manifest JSON (required)")
    flags.add_argument("--bundle-zip", metavar="PATH", help="bundle ZIP; its SHA-256 must match the manifest")
    flags.add_argument("--profile", default="readonly", choices=PROFILE_NAMES, help="profile inventory (readonly)")
    flags.add_argument("--gateway-url", metavar="URL", help=f"Gateway base URL (or ${ENV_GATEWAY_URL})")
    flags.add_argument("--mcp-url", metavar="URL",
                       help=f"Runtime MCP endpoint URL (or ${ENV_MCP_URL}; doctor/verify derive it "
                            "from --server-config-name when this is absent)")
    flags.add_argument("--gateway-token-file", metavar="PATH",
                       help=f"0600 file with the Ignition API token (or ${ENV_GATEWAY_TOKEN})")
    flags.add_argument("--mcp-token-file", metavar="PATH",
                       help=f"0600 file with the MCP bearer token (or ${ENV_MCP_TOKEN})")
    flags.add_argument("--bundle-project", default=DEFAULT_BUNDLE_PROJECT, metavar="NAME",
                       help=f"project the bundle deploys into ({DEFAULT_BUNDLE_PROJECT})")
    flags.add_argument("--server-config-name", metavar="NAME",
                       help="expected MCP server-config resource name (presence check only)")
    flags.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS, metavar="SECONDS",
                       help=f"per-request HTTP budget ({DEFAULT_TIMEOUT_SECONDS:g}s; MCP initialize {MCP_TIMEOUT_SECONDS:g}s)")
    flags.add_argument("--policy-file", metavar="PATH",
                       help="Runtime Target Policy JSON document apply writes to the reserved Tag provider")
    flags.add_argument("--server-config-permissions-file", metavar="PATH",
                       help="permissions tree for a Server Config this run creates (never invented by the CLI)")
    flags.add_argument("--acknowledge-upgrade", action="store_true",
                       help="accept a MAJOR or downgrade bundle change (plan marks those lines)")
    flags.add_argument("--backup-dir", metavar="PATH",
                       help="before overwriting a managed bundle project, export the deployed one into this directory")
    flags.add_argument("--allow-insecure-authorize", action="store_true",
                       help="send the API token over plain HTTP to a non-loopback Gateway")
    flags.add_argument("--json", action="store_true", dest="as_json", help="machine-readable report on stdout")
    return base


def command_parser(command: str) -> UsageParser:
    """Standalone parser for one subcommand (used by :func:`load_inputs`)."""

    if command not in COMMANDS:
        raise UsageError(f"unknown command {command!r}; expected one of {', '.join(COMMANDS)}")
    parser = UsageParser(
        prog=f"ignition-mcp setup-native {command}",
        parents=[build_base_parser()],
        description=_COMMAND_HELP[command],
        epilog=EXIT_CODE_DOC + ENV_DOC,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    return parser


_COMMAND_HELP = {
    "doctor": "Read-only diagnosis of a Runtime Bundle deployment (never mutates anything).",
    "plan": "Report the CREATE / UPDATE / NO CHANGE / BLOCKED intentions for a deployment.",
    "verify": "Verify a provisioned deployment: exact inventories, resource/prompt smokes, bundle_info.",
    "apply": "Apply the planned bundle project, Server Config and Runtime Target Policy, then verify.",
}


def parse_command(argv: Sequence[str]) -> tuple[str, list[str]]:
    """Split ``["setup-native", "<command>", ...]`` into command plus remaining flags."""

    args = [str(item) for item in argv]
    if not args or args[0] not in COMMANDS:
        seen = args[0] if args else ""
        raise UsageError(f"expected one of {', '.join(COMMANDS)}, got {seen!r}")
    return args[0], args[1:]


def load_inputs(argv: Sequence[str], command: str) -> Inputs:
    """Parse ``argv`` for ``command`` and validate every artifact and credential."""

    parser = command_parser(command)
    try:
        namespace = parser.parse_args([str(item) for item in argv])
    except SystemExit as exit_signal:  # --help exits 0; argparse errors become UsageError instead
        if exit_signal.code in (0, None):
            raise
        raise UsageError(str(exit_signal.code)) from exit_signal

    profile = _text(namespace.profile, "--profile")
    manifest_path = _require_path(_text(namespace.bundle_manifest, "--bundle-manifest"), "--bundle-manifest")
    manifest = _load_manifest(manifest_path)
    bundle_zip = (
        None
        if namespace.bundle_zip is None
        else _require_path(_text(namespace.bundle_zip, "--bundle-zip"), "--bundle-zip")
    )
    if bundle_zip is not None:
        _verify_artifact_hash(bundle_zip, manifest)

    gateway_raw = _text_or_none(namespace.gateway_url) or os.environ.get(ENV_GATEWAY_URL)
    if not gateway_raw:
        raise UsageError(f"--gateway-url (or ${ENV_GATEWAY_URL}) is required")
    gateway_url = _endpoint(gateway_raw, "--gateway-url")
    server_config_name = (
        None
        if namespace.server_config_name is None
        else _require_name(_text(namespace.server_config_name, "--server-config-name"), "--server-config-name")
    )
    mcp_raw = _text_or_none(namespace.mcp_url) or os.environ.get(ENV_MCP_URL)
    mcp_url = None if mcp_raw is None else _endpoint(mcp_raw, "--mcp-url")
    # `doctor` and `verify` need an endpoint to talk to: either the URL or the Server
    # Config whose documented path it is (`/data/mcp/<name>`), which is what `apply`
    # has just written and what it verifies through.
    if command in ("doctor", "verify") and mcp_url is None and server_config_name is None:
        raise UsageError(
            f"an MCP endpoint is required for {command}: --mcp-url (or ${ENV_MCP_URL}), "
            "or --server-config-name to derive it"
        )

    gateway_token = _resolve_token(
        _text_or_none(namespace.gateway_token_file),
        os.environ.get(ENV_GATEWAY_TOKEN),
        "--gateway-token-file",
        ENV_GATEWAY_TOKEN,
    )
    if gateway_token is None:
        raise UsageError(f"a Gateway API token is required: --gateway-token-file or ${ENV_GATEWAY_TOKEN}")
    mcp_token = _resolve_token(
        _text_or_none(namespace.mcp_token_file),
        os.environ.get(ENV_MCP_TOKEN),
        "--mcp-token-file",
        ENV_MCP_TOKEN,
    )

    timeout = float(namespace.timeout_seconds)
    if not 0.0 < timeout <= MAX_TIMEOUT_SECONDS:
        raise UsageError(f"--timeout-seconds must be in (0, {MAX_TIMEOUT_SECONDS:g}]")

    policy_file = _optional_path(_text_or_none(namespace.policy_file), "--policy-file")
    permissions_file = _optional_path(
        _text_or_none(namespace.server_config_permissions_file), "--server-config-permissions-file",
    )
    backup_dir = _optional_path(_text_or_none(namespace.backup_dir), "--backup-dir")
    if command == "apply":
        missing = [
            flag for flag, value in (
                ("--server-config-name", server_config_name),
                ("--bundle-zip", bundle_zip),
                ("--policy-file", policy_file),
            ) if value is None
        ]
        if missing:
            raise UsageError(
                f"apply needs {', '.join(missing)}: it writes that Server Config, that project "
                "archive and that Runtime Target Policy document"
            )

    return Inputs(
        command=command,
        manifest_path=manifest_path,
        manifest=manifest,
        gateway_url=gateway_url,
        mcp_url=mcp_url,
        bundle_zip=bundle_zip,
        profile=profile,
        bundle_project=_require_name(_text_or_none(namespace.bundle_project) or DEFAULT_BUNDLE_PROJECT, "--bundle-project"),
        server_config_name=server_config_name,
        gateway_token=gateway_token,
        mcp_token=mcp_token,
        timeout_seconds=timeout,
        allow_insecure_authorize=bool(namespace.allow_insecure_authorize),
        as_json=bool(namespace.as_json),
        policy_file=policy_file,
        permissions_file=permissions_file,
        acknowledge_upgrade=bool(namespace.acknowledge_upgrade),
        backup_dir=backup_dir,
    )


def _optional_path(value: str | None, flag: str) -> Path | None:
    if value is None or not value.strip():
        return None
    return _require_path(value.strip(), flag)


def validate_manifest(document: Any) -> dict[str, Any]:
    """Structural check mirroring the bundle-manifest schema; raises :class:`UsageError`."""

    def fail(message: str) -> NoReturn:
        raise UsageError(f"manifest rejected: {message}")

    if not isinstance(document, dict):
        fail("top level must be a JSON object")
    keys = frozenset(str(key) for key in document)
    if keys != MANIFEST_KEYS:
        missing = sorted(MANIFEST_KEYS - keys)
        extra = sorted(keys - MANIFEST_KEYS)
        fail(f"top-level keys must match the bundle manifest schema (missing={missing or '-'}, extra={extra or '-'})")
    if document["schemaVersion"] != 1:
        fail("schemaVersion must be 1")
    version = document["bundleVersion"]
    if not isinstance(version, str) or _SEMVER.fullmatch(version) is None:
        fail("bundleVersion must be MAJOR.MINOR.PATCH")
    revision = document["sourceRevision"]
    if not isinstance(revision, str) or (revision != "UNSTAMPED" and _SHA40.fullmatch(revision) is None):
        fail("sourceRevision must be a 40-hex git SHA or UNSTAMPED")
    schema = document["resourceSchemaVersion"]
    if not isinstance(schema, int) or isinstance(schema, bool) or schema < 1:
        fail("resourceSchemaVersion must be a positive integer")
    if document["nativeResponseBindingStatus"] not in BINDING_STATUSES:
        fail("nativeResponseBindingStatus is not a known binding status")

    artifact = document["artifact"]
    if not isinstance(artifact, dict) or frozenset(str(key) for key in artifact) != ARTIFACT_KEYS:
        fail("artifact must hold filename, sha256 and sizeBytes")
    if not isinstance(artifact["filename"], str) or not artifact["filename"].startswith("ignition-runtime-bundle-"):
        fail("artifact.filename must follow the release naming")
    if not isinstance(artifact["sha256"], str) or _SHA256.fullmatch(artifact["sha256"]) is None:
        fail("artifact.sha256 must be a lowercase hex SHA-256")
    if not isinstance(artifact["sizeBytes"], int) or isinstance(artifact["sizeBytes"], bool) or artifact["sizeBytes"] < 1:
        fail("artifact.sizeBytes must be a positive integer")

    for key in ("tools", "resources", "prompts"):
        value = document[key]
        _name_list(value, key, fail)
        if isinstance(value, list) and (value != sorted(value) or len(set(value)) != len(value)):
            fail(f"{key} must be a sorted unique list")
    if not document["tools"]:
        fail("tools must not be empty")
    requirements = document["toolRequirements"]
    if not isinstance(requirements, dict) or frozenset(str(key) for key in requirements) != frozenset(document["tools"]):
        fail("toolRequirements must cover exactly the bundled Tools")
    profiles = document["profileInventories"]
    if not isinstance(profiles, dict) or frozenset(str(key) for key in profiles) != frozenset(PROFILE_NAMES):
        fail("profileInventories must hold exactly readonly, operator, configurator and full")
    bundled = frozenset(str(name) for name in document["tools"])
    for name in PROFILE_NAMES:
        entry = profiles[name]
        if not isinstance(entry, dict) or frozenset(str(key) for key in entry) != PROFILE_ENTRY_KEYS:
            fail(f"profileInventories.{name} must hold permissions, tools, resources and prompts")
        for kind in ("permissions", "tools", "resources", "prompts"):
            _name_list(entry[kind], f"profileInventories.{name}.{kind}", fail)
        if not bundled.issuperset(str(tool) for tool in entry["tools"]):
            fail(f"profileInventories.{name}.tools references an unbundled Tool")

    rows = document["testedTuples"]
    if not isinstance(rows, list):
        fail("testedTuples must be a list")
    for row in rows:
        if not isinstance(row, dict) or frozenset(str(key) for key in row) != TESTED_TUPLE_KEYS:
            fail("testedTuples rows must carry the nine evidence identity fields")
        if row["compatibilityStatus"] not in COMPATIBILITY_STATUSES:
            fail("testedTuples row carries an unknown compatibilityStatus")
    return document


def _name_list(value: Any, where: str, fail: Any) -> None:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        fail(f"{where} must be a list of non-empty strings")


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as error:
        raise UsageError(f"--bundle-manifest: cannot read {path}: {type(error).__name__}") from error
    except UnicodeDecodeError as error:
        raise UsageError(f"--bundle-manifest: {path} is not UTF-8 text") from error
    try:
        document = json.loads(raw)
    except ValueError as error:
        raise UsageError(f"--bundle-manifest: {path} is not valid JSON ({error.args[0]})") from error
    return validate_manifest(document)


def _verify_artifact_hash(path: Path, manifest: dict[str, Any]) -> None:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as error:
        raise UsageError(f"--bundle-zip: cannot read {path}: {type(error).__name__}") from error
    actual = digest.hexdigest()
    expected = str(manifest["artifact"]["sha256"])
    if actual != expected:
        raise UsageError(
            f"--bundle-zip: SHA-256 mismatch (file {actual} != manifest {expected}); "
            "refusing to plan or verify against a different artifact"
        )


def _resolve_token(file_value: str | None, env_value: str | None, flag: str, env_name: str) -> str | None:
    """A ``--*-token-file`` wins over the environment variable holding the raw token."""

    if file_value:
        return _read_token_file(file_value, flag)
    if env_value and env_value.strip():
        return env_value.strip()
    return None



def _read_token_file(source: str, flag: str) -> str:
    path = Path(source).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    if path.is_symlink():
        raise UsageError(f"{flag}: {path} is a symlink; token files must be regular files")
    if not path.is_file():
        raise UsageError(f"{flag}: {path} is not a regular file")
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError as error:  # pragma: no cover - racing removal
        raise UsageError(f"{flag}: cannot stat {path}: {type(error).__name__}") from error
    if mode & 0o077:
        raise UsageError(
            f"{flag}: {path} is accessible to group or others (mode {mode:04o}); require 0600 and chmod the file first"
        )
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise UsageError(f"{flag}: cannot read {path}: {type(error).__name__}") from error
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) != 1:
        raise UsageError(f"{flag}: {path} must hold exactly one non-empty line, found {len(lines)}")
    token = lines[0].strip()
    if not token:
        raise UsageError(f"{flag}: {path} holds an empty token")
    return token


def _endpoint(raw: str, flag: str) -> Endpoint:
    """Validate an absolute http(s) URL that carries no embedded credentials."""

    value = raw.strip().rstrip("/")
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError as error:
        raise UsageError(f"{flag}: malformed URL ({error.args[0]})") from error
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise UsageError(f"{flag}: must be an absolute http(s) URL with a host")
    if parts.username or parts.password:
        raise UsageError(f"{flag}: credentials in the URL are not accepted; use a token file or environment variable")
    default_port = 443 if parts.scheme == "https" else 80
    return Endpoint(url=value, scheme=parts.scheme, host=str(parts.hostname), port=port or default_port)


def _require_path(raw: str, flag: str) -> Path:
    path = Path(raw).expanduser()
    if path.is_dir():
        raise UsageError(f"{flag}: {path} is a directory")
    return path


def _require_name(value: str, flag: str) -> str:
    name = value.strip()
    if NAME_TOKEN.fullmatch(name) is None:
        raise UsageError(f"{flag}: {value!r} must match [A-Za-z0-9][A-Za-z0-9._-]{{0,63}}")
    return name


def _text(value: Any, flag: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise UsageError(f"{flag}: a non-empty value is required")
    return value.strip()


def _text_or_none(value: Any) -> str | None:
    return None if value is None else str(value)

