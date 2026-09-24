"""D17/D18 data-directory validation and private layout helpers."""

from __future__ import annotations

import logging
import os
from pathlib import Path
import stat
import sys

from ignition_rest_mcp.config import ConfigurationError, temp_filesystem_prefixes

LOGGER = logging.getLogger("ignition_rest_mcp.storage")
_posix_mode_warning_logged = False


def _warn_posix_modes_unavailable() -> None:
    global _posix_mode_warning_logged
    if _posix_mode_warning_logged:
        return
    _posix_mode_warning_logged = True
    LOGGER.warning(
        "POSIX file modes are unavailable on this platform; the operator must protect "
        "IGNITION_MCP_DATA_DIR with filesystem ACLs"
    )


def validate_data_directory(raw: str, deployment_profile: str) -> Path:
    """Fail closed unless the directory exists (or can be created) as a private,
    writable, non-temporary filesystem location."""

    if not Path(raw).is_absolute():
        raise ConfigurationError("IGNITION_MCP_DATA_DIR must be an absolute path")
    path = Path(raw)
    if path.is_symlink():
        raise ConfigurationError("IGNITION_MCP_DATA_DIR must not be a symbolic link")
    if deployment_profile in {"trusted-internal", "secured"}:
        resolved_prefixes = {
            os.path.normcase(os.path.realpath(prefix)) for prefix in temp_filesystem_prefixes() if os.path.exists(prefix)
        }
        normalized = os.path.normcase(os.path.realpath(path.parent) if path.exists() else os.path.realpath(path))
        for prefix in resolved_prefixes:
            if normalized == prefix or normalized.startswith(prefix.rstrip(os.sep) + os.sep):
                raise ConfigurationError(
                    f"IGNITION_MCP_DATA_DIR resolves onto temporary filesystem {prefix}; "
                    "choose persistent storage for this deployment profile"
                )
    if path.exists():
        if not path.is_dir():
            raise ConfigurationError("IGNITION_MCP_DATA_DIR must be a directory")
    else:
        try:
            path.mkdir(parents=True, mode=0o700)
        except OSError as error:
            raise ConfigurationError(f"IGNITION_MCP_DATA_DIR cannot be created: {error}") from error
    if not os.access(path, os.W_OK | os.X_OK):
        raise ConfigurationError("IGNITION_MCP_DATA_DIR is not writable by the service account")
    try:
        os.chmod(path, 0o700)
    except OSError as error:
        raise ConfigurationError(f"IGNITION_MCP_DATA_DIR permissions cannot be tightened: {error}") from error
    if sys.platform == "win32":
        _warn_posix_modes_unavailable()
        return path
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode != 0o700:
        raise ConfigurationError(f"IGNITION_MCP_DATA_DIR must have mode 0700, found {oct(mode)}")
    return path


def ensure_private_dir(base: Path, *parts: str) -> Path:
    path = base.joinpath(*parts) if parts else base
    path.mkdir(mode=0o700, exist_ok=True)
    if path.is_symlink():
        raise ConfigurationError(f"{path} must not be a symbolic link")
    os.chmod(path, 0o700)
    return path


def make_private_file(path: Path) -> None:
    os.chmod(path, 0o600)
