"""Deterministic, fail-closed Runtime Bundle Designer ZIP builder."""

from __future__ import annotations

import io
import os
from pathlib import Path
import tempfile
import tokenize
import zipfile

from .constants import HANDLER, NATIVE_BINDING_PENDING_COMMENT, PROMPT_HANDLER
from .jsonio import load_json_object
from .project import validate_project
from .validation import ValidationError, require


def pending_native_bindings(files: dict[str, bytes]) -> list[str]:
    pending: list[str] = []
    for name, data in files.items():
        if not name.endswith(("/" + HANDLER, "/" + PROMPT_HANDLER)):
            continue
        source = data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        if any(token.type == tokenize.COMMENT and token.string.strip() == NATIVE_BINDING_PENDING_COMMENT for token in tokens):
            pending.append(name)
    return sorted(pending)


def build_project(project_dir: str | Path, output: str | Path, *, allow_unbound_scaffold: bool = False) -> dict[str, bytes]:
    project_root = Path(project_dir).resolve()
    output_path = Path(output).absolute()
    require(output_path.suffix.lower() == ".zip", output_path, "output must use .zip extension")
    require(project_root != output_path.resolve() and project_root not in output_path.resolve().parents, output_path, "output ZIP must be outside project source")
    require(type(allow_unbound_scaffold) is bool, output_path, "allow_unbound_scaffold must be an explicit boolean")

    files = validate_project(project_root)
    pending = pending_native_bindings(files)
    if pending and not allow_unbound_scaffold:
        raise ValidationError("Native MCP response binding pending in {}; production build refused".format(", ".join(pending)))
    if allow_unbound_scaffold:
        project = load_json_object(files["project.json"], "project.json")
        require(project["enabled"] is False, "project.json", "unbound scaffold packaging requires project.enabled=false")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".runtime-bundle-", suffix=".zip", dir=output_path.parent, delete=False) as stream:
            temporary = Path(stream.name)
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED) as archive:
            for name, data in files.items():
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                info.compress_type = zipfile.ZIP_STORED
                archive.writestr(info, data)
        with zipfile.ZipFile(temporary, "r") as archive:
            require(archive.namelist() == list(files), output_path, "ZIP member order/read-back mismatch")
            require(archive.testzip() is None, output_path, "ZIP CRC validation failed")
            for name, data in files.items():
                require(archive.read(name) == data, name, "ZIP content read-back mismatch")
        os.replace(temporary, output_path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return files
