"""Input shapes shared by the Gateway helpers and the CLI commands (D20, D21, D32).

``Endpoint`` is a validated absolute http(s) URL, ``Inputs`` carries one Runtime
role's target together with the manifest its inventories are checked against, and
``ModuleInputs`` carries one local ``.modl`` file and the SHA-256 the operator named
for it. A credential enters through a ``0600`` file or the environment and is never
echoed.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: The Resource type and collection the MCP Server Config is written through
#: (D20: Native REST only, never the filesystem). D30 §5 refuses that type to the
#: generic config Mutations, which is why the writer owns a curated path for it.
SERVER_CONFIG_TYPE = "com.inductiveautomation.mcp/server-config"
CONFIG_COLLECTION = "core"

#: The two Resource types of D20's opt-in provisioning (ticket #22). D30 §5 refuses
#: both to the generic config Mutations for the same reason it refuses ``server-config``:
#: they administer the Gateway's own security, so only this curated path may write them.
SECURITY_LEVELS_TYPE = "ignition/security-levels"
API_TOKEN_TYPE = "ignition/api-token"

#: D09: dedicated Runtime Security Levels, named per privilege profile, placed as a
#: child of the Gateway's ``Authenticated`` level (the live tree the G0 run recorded).
SECURITY_LEVEL_PARENT = "Authenticated"
SECURITY_LEVEL_PREFIX = "IgnitionMcpRuntime"

DEFAULT_BUNDLE_PROJECT = "ignition_runtime"
#: D10: the largest ``.modl`` this code will read and upload. The pinned MCP Module
#: fixture is 250 KiB; the bound leaves room for a far bigger one and still refuses
#: an arbitrary file.
MAX_MODULE_BYTES = 64 * 1024 * 1024
#: The ``fileName`` the Gateway stores the upload under: one path-free name.
MODULE_NAME_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
#: A resource, project or Server Config name this code is willing to address.
NAME_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class UsageError(Exception):
    """Bad flag, unreadable artifact, or rejected credential input (exit 2)."""


def default_security_level_name(profile: str) -> str:
    """The dedicated Runtime Security Level's name for one privilege profile (D09)."""

    return f"{SECURITY_LEVEL_PREFIX}{profile.capitalize()}"


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
    """Everything a Runtime-plane helper is allowed to know about one role's target."""

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
    #: The two documents a Runtime write needs. The caller that needs one loads and
    #: validates it, so a read-only check runs without either.
    policy_file: Path | None = None
    permissions_file: Path | None = None
    acknowledge_upgrade: bool = False
    backup_dir: Path | None = None
    #: D20's two opt-in provisioning switches (ticket #22). Both default off: without
    #: them the caller detects a Security Level and an API token and writes neither.
    provision_security_levels: bool = False
    security_level_name: str | None = None
    create_runtime_token: bool = False
    runtime_token_file: Path | None = None
    runtime_token_name: str | None = None
    runtime_token_insecure_channel: bool = False

    @property
    def bundle_version(self) -> str:
        return str(self.manifest["bundleVersion"])

    @property
    def security_level(self) -> str:
        """The dedicated Runtime Security Level's name (D09: one per profile)."""

        return self.security_level_name or default_security_level_name(self.profile)

    @property
    def security_level_path(self) -> str:
        return f"{SECURITY_LEVEL_PARENT}/{self.security_level}"

    @property
    def runtime_token(self) -> str:
        """The Runtime API token resource's name; empty unless it is to be created."""

        return self.runtime_token_name or self.server_config_name or ""

    @property
    def provisions_security(self) -> bool:
        """Whether this run reasons about the Gateway's security planes at all."""

        return self.provision_security_levels or self.create_runtime_token

    def profile_inventory(self, key: str) -> list[str]:
        """The selected profile's Tool / Resource / Prompt inventory."""

        return manifest_inventory(self.manifest, self.profile, key)

    def tested_tuples(self) -> list[dict[str, Any]]:
        return tested_tuples(self.manifest)

    def runtime_endpoint(self) -> Endpoint | None:
        """The Runtime MCP endpoint: the URL given, else the Server Config's documented path."""

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


@dataclass(frozen=True, slots=True)
class ModuleInputs:
    """One local ``.modl`` file, the SHA-256 named for it, and one Gateway.

    The file itself is read, hashed and opened by the caller, never here, so a run
    that only prints ``--help`` costs nothing. The module id and build come from the
    file's own ``module.xml``, not from a manifest (D20).
    """

    command: str
    module_file: Path
    #: The validated basename the Gateway stores the upload under.
    upload_name: str
    #: The SHA-256 the operator named for this file, lowercase hex.
    sha256: str
    gateway_url: Endpoint
    gateway_token: str
    timeout_seconds: float
    allow_insecure_authorize: bool
    as_json: bool
    accept_certificate: bool
    accept_eula: bool
    acknowledge_upgrade: bool
    restart: bool


def manifest_inventory(manifest: dict[str, Any], profile: str, key: str) -> list[str]:
    """One inventory of one profile; raises :class:`UsageError` on a malformed manifest."""

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


LOGGER = logging.getLogger(__name__)
_posix_mode_warning_logged = False


def warn_posix_modes_unavailable() -> None:
    """Log, once per process, that credential-file modes cannot be enforced here."""

    global _posix_mode_warning_logged
    if _posix_mode_warning_logged:
        return
    _posix_mode_warning_logged = True
    LOGGER.warning(
        "POSIX file modes are unavailable on this platform; the operator must protect "
        "credential and token files with filesystem ACLs"
    )
