"""Deterministic Runtime Bundle release artifacts (D21/D23).

Produces, atomically and byte-reproducibly:

- ``ignition-runtime-bundle-<bundleVersion>.zip``   (deterministic builder, stamped revision)
- ``ignition-runtime-bundle-<bundleVersion>.manifest.json``
- ``ignition-runtime-bundle-<bundleVersion>.sha256`` (``sha256sum -c`` compatible)

``testedTuples`` is derived ONLY from evidence rows that pass the
slice-9 validator, invoked here before the manifest is built; the release
also re-validates its own manifest against the committed schema.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from tooling.compat.evidence import EvidenceError, load_evidence, tested_tuples_for
from tooling.native.archive import build_project, pending_native_bindings
from tooling.native.jsonio import load_json_object
from tooling.native.validation import ValidationError, require

SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")

MANIFEST_REQUIRED_TOP = (
    "schemaVersion", "bundleVersion", "sourceRevision", "resourceSchemaVersion",
    "nativeResponseBindingStatus", "artifact", "tools", "resources", "prompts",
    "toolRequirements", "profileInventories", "testedTuples",
)
MANIFEST_KEYS = frozenset(MANIFEST_REQUIRED_TOP)
PROFILE_NAMES = ("readonly", "operator", "configurator", "full")
VALID_STATUSES = ("SUPPORTED", "UNTESTED", "INCOMPATIBLE", "UNKNOWN")
BINDING_STATUSES = (
    "NATIVE_BINDING_PENDING", "VERIFIED", "VERIFIED_WITH_LIMITATION", "FAILED",
    "FAILED_NATIVE_BINDING", "UNVERIFIED_LIMITATION", "UNVERIFIED",
)


def read_bundle_versions(project_dir: Path) -> tuple[str, int]:
    bundle_version = (project_dir.parent / "BUNDLE_VERSION").read_text(encoding="utf-8").strip()
    require(SEMVER.fullmatch(bundle_version) is not None, "BUNDLE_VERSION", "must be MAJOR.MINOR.PATCH")
    resource_schema = (project_dir.parent / "RESOURCE_SCHEMA_VERSION").read_text(encoding="utf-8").strip()
    require(re.fullmatch(r"[1-9][0-9]*", resource_schema) is not None,
            "RESOURCE_SCHEMA_VERSION", "must be a positive integer")
    return bundle_version, int(resource_schema)


def _file_sha256(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(block)
            digest.update(block)
    return digest.hexdigest(), size


def _string_list(value: Any, where: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ValidationError(f"{where}: must be a list of non-empty strings")
    return list(value)


def build_manifest(
    *, repo_root: Path, project_dir: Path, bundle_version: str, resource_schema_version: int,
    source_revision: str, artifact_filename: str, artifact_sha256: str, artifact_size: int,
    native_binding_status: str, evidence_dir: Path,
) -> dict[str, Any]:
    contracts_root = repo_root / "contracts"
    require(contracts_root.is_dir(), contracts_root, "contracts/ must exist for a release build")

    inventory = _runtime_inventory(contracts_root)
    profiles = _profile_inventories(contracts_root, inventory["tools"])
    requirements = _tool_requirements(contracts_root, inventory["tools"])
    require(
        len(inventory["resources"]) == len(profiles["readonly"]["resources"]),
        "resources", "bundled Text Resource count must match the readonly profile inventory",
    )

    try:
        rows = load_evidence(evidence_dir)
    except EvidenceError as error:
        raise ValidationError(f"compatibility evidence rejected: {error}") from error
    tested = tested_tuples_for(rows, bundle_version)

    manifest: dict[str, Any] = {
        "schemaVersion": 1,
        "bundleVersion": bundle_version,
        "sourceRevision": source_revision,
        "resourceSchemaVersion": resource_schema_version,
        "nativeResponseBindingStatus": native_binding_status,
        "artifact": {
            "filename": artifact_filename,
            "sha256": artifact_sha256,
            "sizeBytes": artifact_size,
        },
        "tools": inventory["tools"],
        "resources": inventory["resources"],
        "prompts": inventory["prompts"],
        "toolRequirements": requirements,
        "profileInventories": profiles,
        "testedTuples": tested,
    }
    validate_manifest(manifest)
    return manifest


def _runtime_inventory(contracts_root: Path) -> dict[str, list[str]]:
    """Bundled primitives from the Designer source, cross-checked against contracts.

    ``contracts/tools/runtime`` also keeps the D12-deferred alarm contracts; the
    bundled inventory is the frozen Phase 2 13-Tool set, and every bundled Tool
    must have a read-only runtime contract with a matching name.
    """
    from tooling.contracts.lint import CURRENT_RUNTIME_TOOLS

    for path in sorted((contracts_root / "tools/runtime").glob("*.contract.json")):
        document = load_json_object(path.read_bytes(), str(path))
        name = document.get("name")
        require(isinstance(name, str) and name == path.name.removesuffix(".contract.json"),
                path, "runtime contract name must match its filename")
        require(document.get("server") == "ignition-runtime", path, "runtime contract server drift")
        require(document.get("mutationClass") == "NONE", path, "Phase 3: Runtime contracts must be read-only")

    files = _source_files(contracts_root)
    require(files["tools"] == sorted(CURRENT_RUNTIME_TOOLS), contracts_root,
            f"bundled Tool inventory drift: {files['tools']} vs {sorted(CURRENT_RUNTIME_TOOLS)}")
    contract_names = set(_contract_tools(contracts_root))
    require(set(files["tools"]) <= contract_names, contracts_root,
            "every bundled Tool needs a committed runtime contract")
    return {"tools": files["tools"], "resources": files["resources"], "prompts": files["prompts"]}


def _source_files(contracts_root: Path) -> dict[str, list[str]]:
    from tooling.native.project import validate_project
    from tooling.native.constants import PROMPTS_DIR, RESOURCES_DIR, TOOLS_DIR

    project_dir = contracts_root.parent / "packages/ignition-runtime-bundle/project"
    files = validate_project(project_dir)
    inventories: dict[str, list[str]] = {"tools": [], "resources": [], "prompts": []}
    for base, key in ((TOOLS_DIR, "tools"), (RESOURCES_DIR, "resources"), (PROMPTS_DIR, "prompts")):
        for name in sorted(files):
            if name.startswith(base + "/") and name.endswith("/resource.json"):
                metadata = load_json_object(files[name], name)
                attributes = metadata.get("attributes")
                if not isinstance(attributes, dict) or not isinstance(attributes.get("title"), str) \
                        or not attributes["title"]:
                    raise ValidationError(f"{name}: primitive attributes.title required")
                inventories[key].append(attributes["title"])
    return {"tools": sorted(inventories["tools"]), "resources": sorted(inventories["resources"]),
            "prompts": sorted(inventories["prompts"])}


def _contract_tools(contracts_root: Path) -> list[str]:
    return [path.name.removesuffix(".contract.json")
            for path in (contracts_root / "tools/runtime").glob("*.contract.json")]


def _tool_requirements(contracts_root: Path, tools: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name in tools:
        document = load_json_object(
            (contracts_root / "tools/runtime" / f"{name}.contract.json").read_bytes(), name,
        )
        requirements = _string_list(document.get("nativeRequirements"), f"{name}.nativeRequirements")
        if not requirements:
            # D14: a registry-backed Tool has no native function requirement;
            # only a committed registrySource may replace one.
            registry_source = document.get("registrySource")
            if not isinstance(registry_source, str) or not registry_source:
                raise ValidationError(f"{name}: a native-function-free Tool must declare a registrySource")
        binding = document.get("nativeResponseBinding")
        require(binding in BINDING_STATUSES, name, "nativeResponseBinding status required")
        result[name] = {
            "nativeRequirements": sorted(requirements),
            "nativeResponseBinding": binding,
            "budgetClass": document.get("budgetClass"),
            "permissionClass": document.get("permissionClass"),
        }
    return result


def _profile_inventories(contracts_root: Path, runtime_tools: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for profile in PROFILE_NAMES:
        document = load_json_object(
            (contracts_root / "profiles" / f"{profile}.yaml").read_bytes(), profile,
        )
        require(document.get("name") == profile, profile, "profile name drift")
        # Profiles created before D21 may omit the primitive inventories; absence
        # is an explicit empty inventory, never a guessed one.
        tools = _string_list(document.get("tools"), f"{profile}.tools")
        resources = _string_list(document.get("resources", []), f"{profile}.resources")
        prompts = _string_list(document.get("prompts", []), f"{profile}.prompts")
        permissions = _string_list(document.get("permissions", []), f"{profile}.permissions")
        require(len(tools) == len(set(tools)), profile, "profile tools must be a duplicate-free list")
        require(all(tool in runtime_tools for tool in tools), profile,
                "profile may only reference bundled Tools")
        result[profile] = {
            "permissions": sorted(permissions), "tools": tools,
            "resources": resources, "prompts": prompts,
        }
    return result


def validate_manifest(manifest: dict[str, Any]) -> None:
    """Stdlib structural validation mirroring contracts/shared/bundle-manifest.schema.json."""

    require(frozenset(manifest) == MANIFEST_KEYS, "manifest", "unexpected or missing top-level keys")
    require(manifest["schemaVersion"] == 1, "manifest.schemaVersion", "must be 1")
    require(SEMVER.fullmatch(str(manifest["bundleVersion"])) is not None, "manifest.bundleVersion", "SemVer required")
    revision = manifest["sourceRevision"]
    require(isinstance(revision, str), "manifest.sourceRevision", "must be a string")
    require(revision == "UNSTAMPED" or SHA40.fullmatch(revision) is not None,
            "manifest.sourceRevision", "must be a 40-hex git SHA or UNSTAMPED")
    require(isinstance(manifest["resourceSchemaVersion"], int) and manifest["resourceSchemaVersion"] >= 1,
            "manifest.resourceSchemaVersion", "must be a positive integer")
    require(manifest["nativeResponseBindingStatus"] in BINDING_STATUSES,
            "manifest.nativeResponseBindingStatus", "unknown binding status")
    artifact = manifest["artifact"]
    require(frozenset(artifact) == {"filename", "sha256", "sizeBytes"}, "manifest.artifact", "key drift")
    require(
        re.fullmatch(rf"ignition-runtime-bundle-{re.escape(str(manifest['bundleVersion']))}\.zip",
                     str(artifact["filename"])) is not None,
        "manifest.artifact.filename", "must follow the release naming",
    )
    require(SHA256_HEX.fullmatch(str(artifact["sha256"])) is not None, "manifest.artifact.sha256", "hex sha256 required")
    require(isinstance(artifact["sizeBytes"], int) and artifact["sizeBytes"] > 0,
            "manifest.artifact.sizeBytes", "positive size required")
    for key in ("tools", "resources", "prompts"):
        value = manifest[key]
        require(isinstance(value, list) and value == sorted(value) and len(set(value)) == len(value),
                f"manifest.{key}", "must be a sorted unique list")
    require(manifest["tools"], "manifest.tools", "must not be empty")
    requirements = manifest["toolRequirements"]
    require(frozenset(requirements) == frozenset(manifest["tools"]), "manifest.toolRequirements",
            "must cover exactly the bundled Tools")
    profiles = manifest["profileInventories"]
    require(frozenset(profiles) == frozenset(PROFILE_NAMES), "manifest.profileInventories", "profile set drift")
    for profile, entry in profiles.items():
        require(frozenset(entry) == {"permissions", "tools", "resources", "prompts"}, profile, "profile keys drift")
        require(all(tool in manifest["tools"] for tool in entry["tools"]), profile,
                "profile references an unbundled Tool")
    tuples = manifest["testedTuples"]
    require(isinstance(tuples, list), "manifest.testedTuples", "must be a list")
    for row in tuples:
        require(frozenset(row) == {"gate", "gatewayVersion", "gatewayBuild", "mcpModuleVersion",
                                   "mcpModuleBuild", "mcpModuleSha256", "bundleVersion",
                                   "compatibilityStatus", "nativeResponseBinding"},
                "manifest.testedTuples", "tuple keys drift")
        require(row["compatibilityStatus"] in VALID_STATUSES, "manifest.testedTuples", "unknown status")
        require(row["compatibilityStatus"] != "SUPPORTED", "manifest.testedTuples",
                "Phase 3 releases may not carry SUPPORTED tuples")
        require(row["bundleVersion"] == manifest["bundleVersion"], "manifest.testedTuples",
                "tested tuples must belong to this bundle version")
    require(tuples == sorted(tuples, key=lambda item: (item["gate"], item["gatewayVersion"],
                                                        item["mcpModuleBuild"])),
            "manifest.testedTuples", "must be deterministically ordered")


def render_manifest(manifest: dict[str, Any]) -> bytes:
    return (json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def release(
    *, project_dir: Path, out_dir: Path, source_revision: str, evidence_dir: Path,
    repo_root: Path | None = None,
) -> dict[str, Path]:
    if SHA40.fullmatch(source_revision) is None:
        raise ValidationError("release --source-revision must be a 40-hex-lowercase git SHA")
    bundle_version, resource_schema_version = read_bundle_versions(project_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_name = f"ignition-runtime-bundle-{bundle_version}.zip"
    zip_path = out_dir / zip_name
    files = build_project(project_dir, zip_path, source_revision=source_revision)
    pending = pending_native_bindings(files)
    binding_status = "NATIVE_BINDING_PENDING" if pending else "VERIFIED_WITH_LIMITATION"
    sha256, size = _file_sha256(zip_path)
    manifest = build_manifest(
        repo_root=(repo_root or Path(__file__).resolve().parents[2]),
        project_dir=project_dir, bundle_version=bundle_version,
        resource_schema_version=resource_schema_version, source_revision=source_revision,
        artifact_filename=zip_name, artifact_sha256=sha256, artifact_size=size,
        native_binding_status=binding_status, evidence_dir=evidence_dir,
    )
    _atomic_write(out_dir / f"ignition-runtime-bundle-{bundle_version}.manifest.json",
                  render_manifest(manifest))
    _atomic_write(out_dir / f"ignition-runtime-bundle-{bundle_version}.sha256",
                  f"{sha256}  {zip_name}\n".encode("ascii"))
    return {
        "zip": zip_path,
        "manifest": out_dir / f"ignition-runtime-bundle-{bundle_version}.manifest.json",
        "sha256": out_dir / f"ignition-runtime-bundle-{bundle_version}.sha256",
    }


def _atomic_write(path: Path, payload: bytes) -> None:
    temp = path.with_name(path.name + ".tmp")
    temp.write_bytes(payload)
    temp.replace(path)
