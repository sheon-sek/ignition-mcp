"""Runtime Bundle inventory and checksum helpers."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any

from .constants import PRIMITIVE_FILES, PROMPTS_DIR, RESOURCES_DIR, TOOLS_DIR
from .jsonio import load_json_object
from .project import validate_project


def source_inventory(project_dir: str | Path) -> dict[str, list[str]]:
    files = validate_project(project_dir)
    inventories: dict[str, list[str]] = {"tools": [], "resources": [], "prompts": []}
    key_for_base = {TOOLS_DIR: "tools", RESOURCES_DIR: "resources", PROMPTS_DIR: "prompts"}
    for base in PRIMITIVE_FILES:
        for name in files:
            prefix = base + "/"
            if name.startswith(prefix) and name.endswith("/resource.json"):
                metadata = load_json_object(files[name], name)
                inventories[key_for_base[base]].append(metadata["attributes"]["title"])
    for values in inventories.values():
        values.sort()
    return inventories


def file_sha256(path: str | Path) -> str:
    digest = sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def base_manifest(project_dir: str | Path, bundle_version: str, source_revision: str) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "bundleVersion": bundle_version,
        "sourceRevision": source_revision,
        "nativeResponseBindingStatus": "VERIFIED_WITH_LIMITATION",
        "inventory": source_inventory(project_dir),
        "testedTuples": [],
    }
