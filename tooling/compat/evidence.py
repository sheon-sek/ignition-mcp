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

from tooling.contracts.lint import EXPECTED_PROFILE_TOOLS

SCHEMA_VERSION = 1

D27_TUPLE = {
    "gatewayVersion": "8.3.8",
    "gatewayBuild": "2026071409",
    "mcpModuleVersion": "1.3.5-SNAPSHOT",
    "mcpModuleBuild": "2026021307",
    "mcpModuleSha256": "b1142a5796f2fd834555f13f03de706599d745f7172a68e54f2f7908b67fe365",
}

COMPATIBILITY_STATUSES = ("SUPPORTED", "UNTESTED", "INCOMPATIBLE", "UNKNOWN")
#: D26's G4 acceptance list, verbatim. A G4 row must account for every one of these
#: on both Planes, and every entry that is not `LIVE` carries a recorded limitation.
D26_L5_CASES = (
    "partial failure",
    "timeout",
    "ambiguous outcome",
    "permission denied",
    "oversize",
    "concurrent modification",
    "audit failure",
    "cancellation",
)
#: D30's Consequences: on the Runtime plane these three are proven with recorded
#: fixtures only by decision (blocking Jython calls cannot be interrupted), so a G4
#: row may not claim them live there. The opposite claim is refused below.
D30_RUNTIME_FIXTURE_ONLY = ("timeout", "ambiguous outcome", "cancellation")
#: How a G4 L5 case is proven: a live Gateway run, the systematic replay of the exact
#: live case set against the recorded Gateway, a recorded fixture/unit test, or — the
#: honest last entry — nothing at all on that plane. ``NONE`` must be named in
#: ``unsatisfiedAcceptance`` and is what makes a gate result less than verified.
L5_EVIDENCE_CLASSES = ("LIVE", "SYSTEMATIC", "FIXTURE", "NONE")
#: D26's Phase 5 amendment (G5) splits the milestone's cases in two, and this is the
#: split verbatim: the cases the live stage must run against Perspective resources, each
#: with the driver case ids that are its evidence, and the cases that test the generic
#: transaction instead and are cited from the existing G3/G4 evidence.
G5_LIVE_CASES: dict[str, tuple[str, ...]] = {
    "normal edit": (
        "perspective-view-upsert-commits",
        "perspective-view-upsert-verifies-its-own-candidate",
        "perspective-view-upsert-is-observed-by-a-fresh-read",
        # The same case covers the create: D15's upsert creates the resource when the
        # Project has none at the path, and the list and the read are what show it landed.
        "perspective-view-upsert-creates-a-view-the-project-lacks",
    ),
    "no-op": (
        "perspective-view-upsert-of-the-current-document-is-no-change",
        "perspective-view-upsert-no-change-dispatches-nothing",
    ),
    "unrelated-resource preservation": ("perspective-view-upsert-preserves-every-other-entry",),
    "refusal of a Mutation that would override an Inherited resource": (
        "perspective-view-upsert-of-an-inherited-view-is-invalid-argument",
        "perspective-view-upsert-of-an-inherited-view-names-the-reason",
        "perspective-view-upsert-of-an-inherited-view-changes-nothing",
    ),
    "concurrent external change abort": (
        "perspective-view-upsert-after-an-external-change-is-conflict",
        "perspective-view-upsert-after-an-external-change-imports-nothing",
    ),
}
#: The cases the amendment leaves with the generic transaction: G5 cites the committed
#: G3/G4 evidence for them *only* while the Perspective writes call the unchanged
#: `ProjectTransactionService` and ZIP safety, which the row has to record.
G5_CITED_CASES = (
    "backup failure abort-before-import",
    "ambiguous import outcome reconciliation",
    "invalid ZIP, path traversal, duplicate, symlink and bomb rejection",
    "post-import verification failure and the recovery-required path",
)
#: How the amendment's cited cases are proven: a committed G3/G4 row that already holds
#: them, or a live G5 run that took them back. Anything else is not admissible.
G5_CITED_CLASSES = ("CITED", "LIVE")
G4_GATE_RESULTS = ("VERIFIED", "VERIFIED_WITH_LIMITATION", "UNVERIFIED_LIMITATION", "UNTESTED")
G4_PLANES = ("rest", "runtime")
G4_PROFILES = ("readonly", "operator", "configurator", "full")
BINDING_STATUSES = (
    "NATIVE_BINDING_PENDING", "VERIFIED", "VERIFIED_WITH_LIMITATION", "FAILED",
    "FAILED_NATIVE_BINDING", "UNVERIFIED_LIMITATION", "UNVERIFIED",
)
GATES = ("G0", "G1", "G2", "G3", "G4", "G5")

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
    if gate == "G4":
        _apply_g4_rules(row, doc, where)
    if gate == "G5":
        _apply_g5_rules(row, doc, where, directory.parent)
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


def _g4_entry(entry: Any, case: str, plane: str, where: str) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise EvidenceError(f"{where}: L5 case {case!r} on the {plane} plane must be an object")
    verdict = entry.get("verdict")
    if verdict not in L5_EVIDENCE_CLASSES:
        raise EvidenceError(
            f"{where}: L5 case {case!r} on the {plane} plane must have a verdict in {L5_EVIDENCE_CLASSES}"
        )
    source = entry.get("source")
    if not isinstance(source, str) or not source:
        raise EvidenceError(f"{where}: L5 case {case!r} on the {plane} plane must name its evidence source")
    if verdict == "LIVE":
        run_ids = entry.get("runIds")
        if not isinstance(run_ids, list) or not run_ids or not all(
            isinstance(item, str) and item for item in run_ids
        ):
            raise EvidenceError(
                f"{where}: L5 case {case!r} claims LIVE on the {plane} plane without naming a run id"
            )
    else:
        limitation = entry.get("limitation")
        if not isinstance(limitation, str) or not limitation:
            raise EvidenceError(
                f"{where}: L5 case {case!r} is not LIVE on the {plane} plane and must record a limitation"
            )
    return entry


def _apply_g4_rules(row: EvidenceRow, doc: dict[str, Any], where: str) -> None:
    """G4 close-out rules (ticket #23).

    A G4 row is composed out of band from several live runs, so the rules below
    are what keeps it honest: every D26 case is accounted for on both Planes, a
    non-live claim carries its limitation, D30's Runtime fixture-only decision
    cannot be contradicted, and every cited run is a green one.
    """
    deviations = doc.get("ownerAcceptedDeviations")
    if not isinstance(deviations, list) or "phase4-live-environment-protection" not in deviations:
        raise EvidenceError(
            f"{where}: G4 evidence must record the owner-accepted phase4-live environment deviation"
        )
    deployed_sha = doc.get("deployedBundleSha256")
    if not isinstance(deployed_sha, str) or _SHA.fullmatch(deployed_sha) is None:
        raise EvidenceError(f"{where}: G4 rows must record the SHA-256 of the exact deployed release ZIP")

    runs = doc.get("runs")
    if not isinstance(runs, list) or not runs:
        raise EvidenceError(f"{where}: G4 rows must list the live runs the row is composed from")
    for entry in runs:
        if not isinstance(entry, dict):
            raise EvidenceError(f"{where}: every run entry must be an object")
        for key in ("runId", "workflow", "head", "conclusion"):
            value = entry.get(key)
            if not isinstance(value, str) or not value:
                raise EvidenceError(f"{where}: run entry is missing {key}")
        if entry["conclusion"] != "success":
            raise EvidenceError(
                f"{where}: run {entry['runId']} is recorded as {entry['conclusion']!r}; "
                "a green run is the only admissible live evidence"
            )

    l5 = doc.get("l5")
    if not isinstance(l5, dict) or set(l5) != set(D26_L5_CASES):
        raise EvidenceError(f"{where}: l5 must account for exactly the D26 G4 cases {D26_L5_CASES}")
    for case in D26_L5_CASES:
        planes = l5[case]
        if not isinstance(planes, dict) or set(planes) != set(G4_PLANES):
            raise EvidenceError(f"{where}: L5 case {case!r} must be recorded on both Planes {G4_PLANES}")
        for plane in G4_PLANES:
            entry = _g4_entry(planes[plane], case, plane, where)
            if plane == "runtime" and case in D30_RUNTIME_FIXTURE_ONLY and entry["verdict"] == "LIVE":
                raise EvidenceError(
                    f"{where}: D30 makes the Runtime {case!r} case fixture-only; a live claim contradicts it"
                )

    gate_result = doc.get("gateResult")
    if gate_result not in G4_GATE_RESULTS:
        raise EvidenceError(f"{where}: gateResult must be one of {G4_GATE_RESULTS}")
    if gate_result == "VERIFIED" and _g4_incomplete(doc):
        raise EvidenceError(
            f"{where}: gateResult VERIFIED while an L5 case is not live; record the limitation instead"
        )
    if gate_result != "VERIFIED":
        limitations = doc.get("limitations")
        if not isinstance(limitations, list) or not limitations:
            raise EvidenceError(f"{where}: a G4 row that is not fully live must record its limitations")
    unsatisfied = doc.get("unsatisfiedAcceptance")
    if not isinstance(unsatisfied, list) or not all(isinstance(item, str) for item in unsatisfied):
        raise EvidenceError(f"{where}: unsatisfiedAcceptance must be a list of strings")
    for case in D26_L5_CASES:
        for plane in G4_PLANES:
            entry = l5[case][plane]
            if entry.get("verdict") == "NONE" and f"{case} on the {plane} plane" not in unsatisfied:
                raise EvidenceError(
                    f"{where}: L5 case {case!r} has no evidence at all on the {plane} plane and must "
                    "be named in unsatisfiedAcceptance"
                )
            if entry.get("verdict") != "NONE" and f"{case} on the {plane} plane" in unsatisfied:
                raise EvidenceError(
                    f"{where}: {case!r} on the {plane} plane has evidence but is declared unsatisfied"
                )

    for key in ("mutationsDisabledByDefault", "unsafeAutomaticRetryAbsent"):
        if doc.get(key) is not True:
            raise EvidenceError(f"{where}: G4 rows must record {key}=true")

    inventories = doc.get("inventories")
    if not isinstance(inventories, dict):
        raise EvidenceError(f"{where}: G4 rows must record the exact profile inventories")
    runtime = inventories.get("runtime")
    if not isinstance(runtime, dict) or set(runtime) != set(G4_PROFILES):
        raise EvidenceError(f"{where}: inventories.runtime must cover {G4_PROFILES}")
    for profile in G4_PROFILES:
        entry = runtime[profile]
        tools = entry.get("tools") if isinstance(entry, dict) else None
        if not isinstance(tools, list) or not tools or not all(isinstance(item, str) for item in tools):
            raise EvidenceError(f"{where}: the {profile} Runtime inventory must be an explicit Tool list")
        if len(tools) != len(set(tools)):
            raise EvidenceError(f"{where}: the {profile} Runtime inventory has a duplicate Tool")
        if not isinstance(entry.get("verifiedBy"), str) or not entry["verifiedBy"]:
            raise EvidenceError(f"{where}: the {profile} Runtime inventory must name what verified it")
        expected = EXPECTED_PROFILE_TOOLS[profile]
        if tools != expected:
            raise EvidenceError(
                f"{where}: the {profile} Runtime inventory does not match tooling/contracts/lint.py "
                f"(it must equal the profile's contract list, in its order): "
                f"expected {expected}, got {tools}"
            )
    rest_inventory = inventories.get("rest")
    if not isinstance(rest_inventory, dict) or "classEnabled" not in rest_inventory:
        raise EvidenceError(f"{where}: inventories.rest must record the class-enabled and class-disabled lists")


def _g4_incomplete(doc: dict[str, Any]) -> bool:
    """True when any D26 case is not live on both Planes."""

    l5 = doc["l5"]
    return any(
        l5[case][plane].get("verdict") != "LIVE"
        for case in D26_L5_CASES
        for plane in G4_PLANES
    )


def _g5_entry(entry: Any, case: str, where: str, *, verdicts: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise EvidenceError(f"{where}: G5 case {case!r} must be an object")
    verdict = entry.get("verdict")
    if verdict not in verdicts:
        raise EvidenceError(f"{where}: G5 case {case!r} must have a verdict in {verdicts}")
    source = entry.get("source")
    if not isinstance(source, str) or not source:
        raise EvidenceError(f"{where}: G5 case {case!r} must name its evidence source")
    if verdict == "LIVE":
        run_ids = entry.get("runIds")
        if not isinstance(run_ids, list) or not run_ids or not all(
            isinstance(item, str) and item for item in run_ids
        ):
            raise EvidenceError(f"{where}: G5 case {case!r} claims LIVE without naming a run id")
    return entry


def _apply_g5_rules(row: EvidenceRow, doc: dict[str, Any], where: str, root: Path) -> None:
    """G5 rules (D26's Phase 5 amendment).

    The amendment splits the milestone: five cases run live against Perspective
    resources, and the transaction cases that G3/G4 already proved live are *cited* from
    those committed rows instead of being run again. A row may therefore not claim the
    transaction cases as its own work, must name the committed row each citation rests
    on, that row has to be in this evidence tree, and the row must record that the
    delegation is still allowed, which is exactly the "the writes call the unchanged
    transaction service and ZIP safety" condition the amendment states.
    """

    deviations = doc.get("ownerAcceptedDeviations")
    if not isinstance(deviations, list) or "phase4-live-environment-protection" not in deviations:
        raise EvidenceError(
            f"{where}: G5 evidence must record the owner-accepted phase4-live environment "
            "deviation (phase4-live-environment-protection)"
        )
    g5 = doc.get("g5")
    if not isinstance(g5, dict):
        raise EvidenceError(f"{where}: G5 rows must record the g5 case split")
    live = g5.get("livePerspective")
    if not isinstance(live, dict) or set(live) != set(G5_LIVE_CASES):
        raise EvidenceError(
            f"{where}: g5.livePerspective must account for exactly the amendment's live cases "
            f"{sorted(G5_LIVE_CASES)}"
        )
    run_ids = {entry.get("runId") for entry in doc.get("runs", []) if isinstance(entry, dict)}
    for case in G5_LIVE_CASES:
        entry = _g5_entry(live[case], case, where, verdicts=("LIVE",))
        for run_id in entry["runIds"]:
            if run_id not in run_ids:
                raise EvidenceError(
                    f"{where}: G5 case {case!r} cites run {run_id!r}, which runs[] does not hold"
                )
    cited = g5.get("citedTransaction")
    if not isinstance(cited, dict) or set(cited) != set(G5_CITED_CASES):
        raise EvidenceError(
            f"{where}: g5.citedTransaction must account for exactly {sorted(G5_CITED_CASES)}"
        )
    if doc.get("transactionServiceUnchanged") is not True:
        # The amendment's condition: the citation is admissible only while the writes
        # still call the unchanged transaction service. A change to it returns the
        # affected cases to the live stage.
        raise EvidenceError(
            f"{where}: G5 rows must record transactionServiceUnchanged=true, the amendment's "
            "condition for citing the G3/G4 transaction cases"
        )
    for case in G5_CITED_CASES:
        entry = _g5_entry(cited[case], case, where, verdicts=G5_CITED_CLASSES)
        if entry["verdict"] == "CITED":
            evidence = entry.get("evidence")
            if not isinstance(evidence, str) or not evidence:
                raise EvidenceError(f"{where}: cited G5 case {case!r} must name the row it cites")
            manifest = root / evidence / "evidence.json"
            if not manifest.is_file():
                raise EvidenceError(
                    f"{where}: cited G5 case {case!r} names {evidence!r}, which is not in this "
                    "evidence tree; the cited cases must rest on committed rows"
                )
            try:
                cited_doc = json.loads(manifest.read_text(encoding="utf-8"))
            except ValueError as error:
                raise EvidenceError(f"{where}: {manifest.name} is unreadable: {error}") from error
            cited_gate = cited_doc.get("gate") if isinstance(cited_doc, dict) else None
            if cited_gate not in {"G3", "G4"}:
                raise EvidenceError(
                    f"{where}: cited G5 case {case!r} cites {evidence!r}, not a G3/G4 row"
                )
    gate_result = doc.get("gateResult")
    if gate_result not in G4_GATE_RESULTS:
        raise EvidenceError(f"{where}: gateResult must be one of {G4_GATE_RESULTS}")
    if gate_result != "VERIFIED":
        limitations = doc.get("limitations")
        if not isinstance(limitations, list) or not limitations:
            raise EvidenceError(f"{where}: a G5 row that is not fully verified must record its limitations")
    unsatisfied = doc.get("unsatisfiedAcceptance")
    if not isinstance(unsatisfied, list) or not all(isinstance(item, str) for item in unsatisfied):
        raise EvidenceError(f"{where}: unsatisfiedAcceptance must be a list of strings")


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
