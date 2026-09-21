"""D17/D18 data-directory validation and private layout helpers."""

from __future__ import annotations

import os
from pathlib import Path
import stat

from ignition_rest_mcp.config import TEMP_FILESYSTEM_PREFIXES, ConfigurationError


def validate_data_directory(raw: str, deployment_profile: str) -> Path:
    """Fail closed unless the directory exists (or can be created) as a private,
    writable, non-temporary filesystem location."""

    if not raw.startswith("/"):
        raise ConfigurationError("IGNITION_MCP_DATA_DIR must be an absolute path")
    path = Path(raw)
    if path.is_symlink():
        raise ConfigurationError("IGNITION_MCP_DATA_DIR must not be a symbolic link")
    if deployment_profile in {"trusted-internal", "secured"}:
        resolved_prefixes = {os.path.realpath(prefix) for prefix in TEMP_FILESYSTEM_PREFIXES if os.path.exists(prefix)}
        normalized = os.path.realpath(path.parent) if path.exists() else os.path.realpath(path)
        for prefix in resolved_prefixes:
            if normalized == prefix or normalized.startswith(prefix + os.sep):
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
