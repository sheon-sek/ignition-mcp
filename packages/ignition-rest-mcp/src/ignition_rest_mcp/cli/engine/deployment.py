"""The deployment directory (D32 section 9).

Each deployment is ``~/.config/ignition-mcp/deployments/<name>/``. It holds
``deployment.toml`` with the plain values, such as the Gateway URL, the Deployment
environment and the Assistant roles, and one file per secret. The generated policy
and permissions documents also live here; the tickets that generate them choose
their file names.

Secret files go through ``cli/gateway_ops/security.py``, the handling Phase 4
verified: ``O_CREAT|O_EXCL``, mode ``0600``, no symlinks, one ``<name>:<key>`` line,
and on Windows one warning in place of the mode check (D31 section 4.1).
"""

from __future__ import annotations

import json
import os
import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from ignition_rest_mcp.cli.engine.errors import CliError, ErrorCode
from ignition_rest_mcp.cli.gateway_ops import security
from ignition_rest_mcp.cli.gateway_ops.inputs import warn_posix_modes_unavailable

DEPLOYMENT_FILE = "deployment.toml"
SECRET_SUFFIX = ".secret"
DIRECTORY_MODE = 0o700
NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
#: The key in ``deployment.toml`` that lists the Gateway resources this deployment
#: created, one ``<kind>:<name>`` entry each (D32 section 10, issue #76 review).
#: ``setup`` writes it as it creates a resource; ``reset`` deletes only what it names.
CREATED_KEY = "created"

#: A value ``deployment.toml`` can hold. Nested tables are not needed yet.
Value = str | bool | int | list[str]


def default_root() -> Path:
    """``~/.config/ignition-mcp/deployments`` on every platform, as D32 names it."""

    return Path.home() / ".config" / "ignition-mcp" / "deployments"


def check_name(name: str) -> str:
    """``""`` when ``name`` can be a deployment or secret name, else the reason."""

    if NAME_PATTERN.fullmatch(name) is None:
        return f"{name!r} must match [A-Za-z0-9][A-Za-z0-9._-]{{0,63}}"
    return ""


@dataclass(frozen=True, slots=True)
class Deployment:
    """One deployment directory and the values its ``deployment.toml`` holds.

    ``values`` is empty for a deployment that has not been saved yet. Keys are the
    input names from :mod:`ignition_rest_mcp.cli.engine.resolve`.
    """

    name: str
    directory: Path
    values: dict[str, Value] = field(default_factory=dict)

    @property
    def exists(self) -> bool:
        return (self.directory / DEPLOYMENT_FILE).is_file()

    def secret_path(self, secret: str) -> Path:
        reason = check_name(secret)
        if reason:
            raise ValueError(f"secret name {reason}")
        return self.directory / f"{secret}{SECRET_SUFFIX}"


def open_deployment(name: str, root: Path | None = None) -> Deployment:
    """Read ``<root>/<name>/deployment.toml``; a missing file gives empty values."""

    reason = check_name(name)
    if reason:
        raise CliError(ErrorCode.INVALID_INPUT, f"--deployment: {reason}")
    directory = (root or default_root()) / name
    path = directory / DEPLOYMENT_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return Deployment(name, directory)
    except (OSError, UnicodeDecodeError) as error:
        raise CliError(
            ErrorCode.DEPLOYMENT_UNREADABLE, f"cannot read {path} ({type(error).__name__})"
        ) from error
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise CliError(ErrorCode.DEPLOYMENT_UNREADABLE, f"{path} is not valid TOML ({error})") from error
    values: dict[str, Value] = {}
    for key, value in document.items():
        if isinstance(value, (str, bool, int)) or (
            isinstance(value, list) and all(isinstance(item, str) for item in value)
        ):
            values[key] = value
        else:
            raise CliError(ErrorCode.DEPLOYMENT_UNREADABLE, f"{path}: {key} holds an unsupported value")
    return Deployment(name, directory, values)


def save_deployment(deployment: Deployment, values: dict[str, Value]) -> Deployment:
    """Write ``deployment.toml`` with ``values`` merged over the saved ones.

    The directory is created with mode ``0700`` where POSIX modes exist. The file is
    written to a temporary name and renamed, so a crash never leaves half a file.
    Secrets never go into this file; use :func:`write_secret`.
    """

    merged = {**deployment.values, **values}
    _make_directory(deployment.directory)
    path = deployment.directory / DEPLOYMENT_FILE
    staging = path.with_name(DEPLOYMENT_FILE + ".tmp")
    staging.write_text(_toml(merged), encoding="utf-8", newline="\n")
    os.replace(staging, path)
    return Deployment(deployment.name, deployment.directory, merged)


def write_secret(deployment: Deployment, secret: str, value: str) -> Path:
    """Create one secret file with mode ``0600``. An existing file is never overwritten."""

    _make_directory(deployment.directory)
    path = deployment.secret_path(secret)
    try:
        security.write_secret_file(path, value)
    except security.FileError as error:
        raise CliError(ErrorCode.SECRET_FILE_INVALID, str(error)) from error
    return path


def read_secret(path: Path) -> str:
    """The one ``<name>:<key>`` line of a secret file, checked for mode and shape."""

    observed = security.observe_secret_file(path)
    if observed.error:
        raise CliError(ErrorCode.SECRET_FILE_INVALID, f"{path}: {observed.error}")
    if not observed.exists:
        raise CliError(ErrorCode.SECRET_FILE_INVALID, f"{path} does not exist")
    return security.token_secret(observed.name, observed.key)


def remove_secret(deployment: Deployment, secret: str) -> bool:
    """Delete one secret file; ``False`` when there was none."""

    try:
        deployment.secret_path(secret).unlink()
    except FileNotFoundError:
        return False
    return True


def _make_directory(directory: Path) -> None:
    directory.mkdir(mode=DIRECTORY_MODE, parents=True, exist_ok=True)
    if sys.platform == "win32":
        warn_posix_modes_unavailable()
    else:
        os.chmod(directory, DIRECTORY_MODE)


def _toml(values: dict[str, Value]) -> str:
    lines = ["# Written by ignition-mcp. Secrets live in the *.secret files, never here."]
    for key in sorted(values):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", key):
            raise ValueError(f"deployment key {key!r} is not a bare TOML key")
        lines.append(f"{key} = {_toml_value(values[key])}")
    return "\n".join(lines) + "\n"


def _toml_value(value: Value) -> str:
    # A JSON string is a valid TOML basic string: both escape quotes, backslashes
    # and control characters the same way, and json.dumps never emits ``\/``.
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value)
    return "[" + ", ".join(json.dumps(item) for item in value) + "]"
