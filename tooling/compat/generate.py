"""G3 live-evidence generator (D21/D23, slice 11).

Turns the driver's raw observations plus the run's identity material into a
schema-valid ``g3-<gateway>-mcp-<build>/evidence.json`` row under the evidence
directory, then re-validates the whole directory through the slice-9 validator
(``load_evidence``) so no harness ever hands the release a row it would reject.
Frozen G0-G2 evidence is only ever read, never rewritten.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from tooling.compat.evidence import D27_TUPLE, EvidenceError, load_evidence

SCHEMA_VERSION = 3
OWNER_ACCEPTED_DEVIATIONS = ["phase3-live-environment-protection"]


class GenerateError(ValueError):
    """The observations are internally inconsistent or incomplete."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise GenerateError(f"{path}: unreadable observations: {error}") from error
    if not isinstance(document, dict):
        raise GenerateError(f"{path}: observations must be a JSON object")
    return document


def _require_text(document: dict[str, Any], key: str, where: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value:
        raise GenerateError(f"{where}: {key} missing")
    return value


def build_row(observations: dict[str, Any], identity: dict[str, str], *,
              gate_off_report: dict[str, Any] | None = None) -> dict[str, Any]:
    """Compose one G3 evidence row from driver observations + identity material."""
    if observations.get("gate") != "G3":
        raise GenerateError("observations: expected gate G3")
    if observations.get("fatal") is not None:
        raise GenerateError(f"observations record a fatal stage error: {observations['fatal']}")
    checks = observations.get("checks")
    if not isinstance(checks, list) or any(not isinstance(c, dict) for c in checks):
        raise GenerateError("observations: checks must be a list")
    failed = [c for c in checks if isinstance(c, dict) and c.get("status") == "FAIL"]
    if failed:
        raise GenerateError(f"observations: {len(failed)} failed check(s): {[c.get('name') for c in failed]}")

    gateway_version = _require_text(identity, "gatewayVersion", "identity")
    gateway_build = _require_text(identity, "gatewayBuild", "identity")
    module_version = _require_text(identity, "mcpModuleVersion", "identity")
    module_build = _require_text(identity, "mcpModuleBuild", "identity")
    module_sha = _require_text(identity, "mcpModuleSha256", "identity")
    bundle_version = _require_text(identity, "bundleVersion", "identity")
    openapi_sha = _require_text(observations, "openapiSha256", "observations")

    is_d27 = {
        "gatewayVersion": gateway_version, "gatewayBuild": gateway_build,
        "mcpModuleVersion": module_version, "mcpModuleBuild": module_build,
        "mcpModuleSha256": module_sha,
    } == D27_TUPLE
    published = observations.get("runtimeOutputSchemaPublished")
    accepted = observations.get("runtimeNativeBindingAccepted")
    if not isinstance(published, bool) or not isinstance(accepted, bool):
        raise GenerateError("observations: runtime binding flags must be booleans")
    if published and accepted:
        binding, flag, status = "VERIFIED", False, "VERIFIED"
    elif is_d27 and accepted and not published:
        binding, flag, status = "VERIFIED_WITH_LIMITATION", True, "VERIFIED"
    else:
        binding, flag, status = "UNVERIFIED_LIMITATION", False, "FAILED_NATIVE_BINDING"
    if flag and binding != "VERIFIED_WITH_LIMITATION":  # defensive: never inherit D27
        raise GenerateError("internal mapping error")

    stability = observations.get("fingerprintStability")
    concurrency = observations.get("projectMutationConcurrencySafe")
    if stability not in {"STABLE", "UNSTABLE"} or not isinstance(concurrency, bool):
        raise GenerateError("observations: fingerprint stability fields missing or inconsistent")
    if stability == "UNSTABLE" and concurrency:
        raise GenerateError("observations: unstable fingerprint must mark mutation not concurrency-safe")

    runtime = observations.get("runtime")
    transactions = observations.get("transactions")
    rest_plane = observations.get("restPlane")
    if not isinstance(runtime, dict) or not isinstance(transactions, dict) or not isinstance(rest_plane, dict):
        raise GenerateError("observations: runtime/transactions/restPlane sections are required")
    for section in ("NO_CHANGE", "COMMITTED", "CONFLICTED"):
        entry = transactions.get(section)
        if not isinstance(entry, dict) or not entry.get("state"):
            raise GenerateError(f"observations: transactions.{section} missing")
    deployed_sha = _require_text(identity, "deployedBundleSha256", "identity")
    if deployed_sha != _require_text(identity, "bundleSha256", "identity"):
        raise GenerateError("deployed bundle hash must equal the release ZIP hash")

    row: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "gate": "G3",
        "gatewayVersion": gateway_version,
        "gatewayBuild": gateway_build,
        "gatewayImage": _require_text(identity, "gatewayImage", "identity"),
        "gatewayImageDigest": _require_text(identity, "gatewayImageDigest", "identity"),
        "mcpModuleVersion": module_version,
        "mcpModuleArtifactVersion": _require_text(identity, "mcpModuleArtifactVersion", "identity"),
        "mcpModuleBuild": module_build,
        "mcpModuleSha256": module_sha,
        "bundleVersion": bundle_version,
        "bundleSha256": deployed_sha,
        "sourceRevision": _require_text(identity, "sourceRevision", "identity"),
        "runId": _require_text(identity, "runId", "identity"),
        "openapiSha256": openapi_sha,
        "deployedBundleSha256": deployed_sha,
        "runtime": runtime,
        "setupNative": observations.get("setupNative"),
        "restPlane": rest_plane,
        "gateOffInventory": gate_off_report or "not-captured",
        "authzDenials": observations.get("authzDenials"),
        "transactions": transactions,
        "tagExport": observations.get("tagExport"),
        "deployment": observations.get("deployment"),
        "disposableProject": observations.get("disposableProject"),
        "fingerprintStability": stability,
        "projectMutationConcurrencySafe": concurrency,
        "ownerAcceptedDeviations": list(OWNER_ACCEPTED_DEVIATIONS),
        "runtimeNullEncoding": "ignition-null-v1",
        "nativeResponseBinding": binding,
        "d27ExceptionApplied": flag,
        "status": status,
        # D21: Phase 3 never certifies deployment compatibility; the row records what
        # was verified, never a SUPPORTED promotion.
        "compatibilityStatus": "UNTESTED",
    }
    for key in ("setupNative", "authzDenials"):
        if not isinstance(row[key], dict):
            raise GenerateError(f"observations: {key} section missing")
    return row


def generate(observations: Path, out_dir: Path, identity_path: Path,
             gate_off_report: Path | None = None) -> Path:
    """Write and self-validate one G3 row; returns the row directory."""
    identity = _load(identity_path)
    observations_doc = _load(observations)
    gate_off = _load(gate_off_report) if gate_off_report is not None else None
    row = build_row(observations_doc, identity, gate_off_report=gate_off)
    directory = out_dir / f"g3-{row['gatewayVersion']}-mcp-{row['mcpModuleBuild']}"
    if directory.exists():
        raise GenerateError(f"{directory}: refusing to overwrite existing evidence (frozen once written)")
    directory.mkdir(parents=True)
    (directory / "evidence.json").write_text(json.dumps(row, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        rows = load_evidence(out_dir)
    except EvidenceError as error:
        raise GenerateError(f"generated row rejected by the validator: {error}") from error
    if sum(1 for item in rows if item.gate == "G3") < 1:
        raise GenerateError("generated G3 row not found on re-validation")
    return directory


def sha256_file(path: Path) -> str:
    return _sha256(path)
