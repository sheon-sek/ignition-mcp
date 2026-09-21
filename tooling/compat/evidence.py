"""D21/D23 compatibility evidence: schema, parser and fail-closed validator.

The committed G0-G2 rows are frozen legacy evidence and are validated
read-only against the documented legacy forms below; nothing here may rewrite
them. Phase 3 rules (binding, not suggestion):

- no evidence row and no release manifest may claim ``SUPPORTED``;
- the D27 outputSchema exception is exact-tuple only, never inherited;
- a missing outputSchema on a non-D27 tuple must be recorded honestly
  (``UNVERIFIED_LIMITATION`` / ``FAILED_NATIVE_BINDING``), never excused.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

D27_TUPLE = {
    "gatewayVersion": "8.3.8",
    "gatewayBuild": "2026071409",
    "mcpModuleVersion": "1.3.5-SNAPSHOT",
    "mcpModuleBuild": "2026021307",
    "mcpModuleSha256": "b1142a5796f2fd834555f13f03de706599d745f7172a68e54f2f7908b67fe365",
}

COMPATIBILITY_STATUSES = ("SUPPORTED", "UNTESTED", "INCOMPATIBLE", "UNKNOWN")
BINDING_STATUSES = (
    "NATIVE_BINDING_PENDING", "VERIFIED", "VERIFIED_WITH_LIMITATION", "FAILED",
    "FAILED_NATIVE_BINDING", "UNVERIFIED_LIMITATION", "UNVERIFIED",
)
GATES = ("G0", "G1", "G2", "G3")

_SHA = re.compile(r"^[0-9a-f]{64}$")
_BUILD = re.compile(r"^[0-9]{10}$")
_VERSIONISH = re.compile(r"^[0-9A-Za-z._-]+$")

# Legacy compatibility: G1 (schemaVersion 2) predates the explicit d27 flag.
LEGACY_GATES = {"G0": ("schemaVersion", "gate", "d27OutputSchemaExceptionApplied"),
                "G1": ("schemaVersion", "gate")}


class EvidenceError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class EvidenceRow:
    gate: str
    directory: str
    bundle_version: str
    gateway_version: str
    gateway_build: str
    mcp_module_version: str
    mcp_module_build: str
    mcp_module_sha256: str
    compatibility_status: str
    native_response_binding: str
    d27_exception_applied: bool
    output_schema_published: bool | None
    raw: dict[str, Any] = field(default_factory=dict, compare=False)

    @property
    def is_d27_tuple(self) -> bool:
        expected = {
            "gatewayVersion": self.gateway_version,
            "gatewayBuild": self.gateway_build,
            "mcpModuleVersion": self.mcp_module_version,
            "mcpModuleBuild": self.mcp_module_build,
            "mcpModuleSha256": self.mcp_module_sha256,
        }
        return all(D27_TUPLE[key] == value for key, value in expected.items())

    def as_tested_tuple(self) -> dict[str, Any]:
        return {
            "gate": self.gate,
            "gatewayVersion": self.gateway_version,
            "gatewayBuild": self.gateway_build,
            "mcpModuleVersion": self.mcp_module_version,
            "mcpModuleBuild": self.mcp_module_build,
            "mcpModuleSha256": self.mcp_module_sha256,
            "bundleVersion": self.bundle_version,
            "compatibilityStatus": self.compatibility_status,
            "nativeResponseBinding": self.native_response_binding,
        }


def _text(doc: dict[str, Any], key: str, where: str, *, pattern: re.Pattern[str] | None = None) -> str:
    value = doc.get(key)
    if not isinstance(value, str) or not value:
        raise EvidenceError(f"{where}: {key} must be a non-empty string")
    if pattern is not None and pattern.fullmatch(value) is None:
        raise EvidenceError(f"{where}: {key} has an invalid format")
    return value


def _boolean(doc: dict[str, Any], key: str, where: str) -> bool:
    value = doc.get(key)
    if not isinstance(value, bool):
        raise EvidenceError(f"{where}: {key} must be a boolean")
    return value


def parse_row(directory: Path, doc: dict[str, Any]) -> EvidenceRow:
    where = directory.name
    gate = _text(doc, "gate", where)
    if gate not in GATES:
        raise EvidenceError(f"{where}: unknown gate {gate!r}")
    schema_version = doc.get("schemaVersion")
    if not isinstance(schema_version, int) or isinstance(schema_version, bool) or schema_version < 1:
        raise EvidenceError(f"{where}: schemaVersion must be a positive integer")

    raw_status = doc.get("compatibilityStatus")
    if raw_status is None and gate == "G0":
        # documented legacy form: the G0 characterization predates D21 deployment status
        compatibility = "UNTESTED"
    else:
        compatibility = _text(doc, "compatibilityStatus", where)
    if compatibility not in COMPATIBILITY_STATUSES:
        raise EvidenceError(f"{where}: compatibilityStatus must be one of {COMPATIBILITY_STATUSES}")

    binding = doc.get("nativeResponseBinding", doc.get("nativeResponseBindingStatus"))
    if not isinstance(binding, str) or binding not in BINDING_STATUSES:
        raise EvidenceError(f"{where}: native response binding status is missing or unknown")

    runtime = doc.get("runtime")
    output_schema_published: bool | None = None
    if isinstance(runtime, dict) and isinstance(runtime.get("outputSchemaPublished"), bool):
        output_schema_published = runtime["outputSchemaPublished"]
    elif isinstance(doc.get("outputSchemaPublished"), bool):
        output_schema_published = doc["outputSchemaPublished"]

    flag = doc.get("d27ExceptionApplied", doc.get("d27OutputSchemaExceptionApplied"))
    if flag is None and gate in ("G1",) and schema_version <= 2:
        # documented legacy form: G1 predates the explicit flag
        flag = binding == "VERIFIED_WITH_LIMITATION"
    if not isinstance(flag, bool):
        raise EvidenceError(f"{where}: d27 exception flag must be an explicit boolean")

    row = EvidenceRow(
        gate=gate,
        directory=directory.name,
        bundle_version=_text(doc, "bundleVersion", where, pattern=_VERSIONISH),
        gateway_version=_text(doc, "gatewayVersion", where),
        gateway_build=_text(doc, "gatewayBuild", where, pattern=_BUILD),
        mcp_module_version=_text(doc, "mcpModuleVersion", where),
        mcp_module_build=_text(doc, "mcpModuleBuild", where, pattern=_BUILD),
        mcp_module_sha256=_text(doc, "mcpModuleSha256", where, pattern=_SHA),
        compatibility_status=compatibility,
        native_response_binding=binding,
        d27_exception_applied=flag,
        output_schema_published=output_schema_published,
        raw=doc,
    )
    _apply_d27_rules(row, where)
    if gate == "G3":
        _apply_g3_rules(row, doc, where)
    return row


def _apply_d27_rules(row: EvidenceRow, where: str) -> None:
    if row.d27_exception_applied:
        if not row.is_d27_tuple:
            raise EvidenceError(
                f"{where}: d27ExceptionApplied=true but the Gateway/Module tuple is not "
                "the exact characterized D27 identity (fail-closed)"
            )
        if row.native_response_binding != "VERIFIED_WITH_LIMITATION":
            raise EvidenceError(f"{where}: the D27 exception requires VERIFIED_WITH_LIMITATION")
        if row.output_schema_published is True:
            raise EvidenceError(f"{where}: a row applying the D27 exception must record outputSchemaPublished=false")
    else:
        if row.native_response_binding == "VERIFIED_WITH_LIMITATION":
            raise EvidenceError(
                f"{where}: VERIFIED_WITH_LIMITATION without the d27 exception flag is not admissible"
            )
        if row.output_schema_published is False and row.native_response_binding in {
            "VERIFIED", "NATIVE_BINDING_PENDING",
        }:
            raise EvidenceError(
                f"{where}: a missing native outputSchema on a non-D27 tuple must be recorded as "
                "UNVERIFIED_LIMITATION or FAILED_NATIVE_BINDING, never as success or pending"
            )
    if row.gate == "G0" and row.is_d27_tuple and row.directory.startswith("g0-"):
        # the G0 characterization tuple must also match the pinned module artifact version
        artifact = row.raw.get("mcpModuleArtifactVersion")
        if artifact != "1.3.5.2026021307-SNAPSHOT":
            raise EvidenceError(f"{where}: G0 module artifact version mismatch")


def _apply_g3_rules(row: EvidenceRow, doc: dict[str, Any], where: str) -> None:
    deviations = doc.get("ownerAcceptedDeviations")
    if not isinstance(deviations, list) or "phase3-live-environment-protection" not in deviations:
        raise EvidenceError(
            f"{where}: G3 evidence must record the owner-accepted phase3-live environment deviation"
        )
    stability = doc.get("fingerprintStability")
    concurrency_safe = doc.get("projectMutationConcurrencySafe")
    if stability not in {"STABLE", "UNSTABLE"} or not isinstance(concurrency_safe, bool):
        raise EvidenceError(f"{where}: G3 rows must record fingerprintStability and the derived safety flag")
    if stability == "UNSTABLE" and concurrency_safe:
        raise EvidenceError(f"{where}: an unstable fingerprint row must mark Project mutation not concurrency-safe")
    deployed_sha = doc.get("deployedBundleSha256")
    if not isinstance(deployed_sha, str) or _SHA.fullmatch(deployed_sha) is None:
        raise EvidenceError(f"{where}: G3 rows must record the SHA-256 of the exact deployed release ZIP")


def load_evidence(evidence_dir: str | Path, *, reject_supported: bool = True) -> list[EvidenceRow]:
    root = Path(evidence_dir)
    if not root.is_dir():
        raise EvidenceError(f"{root}: evidence directory not found")
    rows: list[EvidenceRow] = []
    for directory in sorted(root.glob("g[0-9]-*")):
        manifest = directory / "evidence.json"
        if not manifest.is_file():
            raise EvidenceError(f"{directory.name}: missing evidence.json")
        try:
            doc = json.loads(manifest.read_text(encoding="utf-8"))
        except ValueError as error:
            raise EvidenceError(f"{directory.name}: invalid JSON: {error}") from error
        if not isinstance(doc, dict):
            raise EvidenceError(f"{directory.name}: evidence.json must be an object")
        if reject_supported and _claims_supported(doc):
            raise EvidenceError(
                f"{directory.name}: SUPPORTED compatibility may not be claimed (evidence must be certified)"
            )
        rows.append(parse_row(directory, doc))
    return rows


def _claims_supported(doc: dict[str, Any]) -> bool:
    if doc.get("compatibilityStatus") == "SUPPORTED":
        return True
    return any(isinstance(value, str) and value == "SUPPORTED" and key.endswith("Status")
               for key, value in doc.items())


def tested_tuples_for(rows: list[EvidenceRow], bundle_version: str) -> list[dict[str, Any]]:
    """Exact-tuple rows that apply to one bundle version, sorted deterministically."""

    selected = [row.as_tested_tuple() for row in rows if row.bundle_version == bundle_version]
    return sorted(selected, key=lambda item: (item["gate"], item["gatewayVersion"], item["mcpModuleBuild"]))
