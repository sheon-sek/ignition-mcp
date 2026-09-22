"""G5 evidence row (D26's Phase 5 amendment, ticket P5-3).

Phase 5 closes G5 with a *live stage*, not a new workflow: the Perspective cases run
inside the existing `phase4-live-rest` workflow's class-enabled pass, and D26's Phase 5
amendment decides which of the milestone's cases that stage owns and which it may cite
from the G3/G4 evidence that already proved them.

A G5 row is therefore composed from two inputs plus the stage's own artifact:

- an **authored close document** (`tests/compatibility/g5/close-<version>.json`) which
  records the row that the transaction cases are cited from, the source of each
  citation, and any limitation the stage's operator knows about;
- the **stage artifact**, the class-enabled driver pass `observations.json`, whose
  case rows are re-read here so a case recorded as green really was green *and* every
  live Perspective case the amendment names is present; and
- the **run** itself, which the caller names on the command line because only GitHub
  knows it after the run has finished (a row may not be written from inside the run it
  cites: its conclusion is not known yet).

The builder refuses to compose anything it cannot corroborate, and the emitted row is
re-validated through `tooling.compat.evidence` (the G5 rules) before it is written, so a
row this module produces can never claim more than the artifacts show and can never claim
`SUPPORTED`.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from tooling.compat.evidence import (
    G5_CITED_CASES,
    G5_LIVE_CASES,
    EvidenceError,
    parse_row,
)

SCHEMA_VERSION = 5
GATE = "G5"
#: The workflows a G5 row may cite. The Perspective cases live in the class-enabled pass
#: of the Phase 4 REST workflow, which is the only stage that drives them.
RUN_WORKFLOWS = ("Phase 4 Live Gateway REST mutation",)
#: The stage document a cited run must hold, and the store the row records for it.
STAGE_DOCUMENT = "observations.json"
#: What a G5 row always records about the delegation D26's amendment grants it.
DELEGATION_LIMITATION = (
    "The generic transaction cases (backup failure abort-before-import, ambiguous import "
    "outcome reconciliation, ZIP safety, post-import verification failure and the "
    "recovery-required path) are cited from the committed G3/G4 evidence, per D26's "
    "Phase 5 amendment, because the Perspective writes call the unchanged "
    "ProjectTransactionService and ZIP safety."
)
_SHA40 = re.compile(r"^[0-9a-f]{40}$")


class G5Error(ValueError):
    """The close document or a cited artifact contradicts the row it would produce."""


def _load(path: Path, what: str) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise G5Error(f"{path}: unreadable {what}: {error}") from error
    if not isinstance(document, dict):
        raise G5Error(f"{path}: {what} must be a JSON object")
    return document


def _text(document: dict[str, Any], key: str, where: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value:
        raise G5Error(f"{where}: {key} is missing")
    return value


def _stage_cases(stage: dict[str, Any], where: str) -> dict[str, dict[str, Any]]:
    if stage.get("passed") is not True:
        raise G5Error(
            f"{where}: the class-enabled pass does not record a passing run; a G5 row may only "
            "be composed from a green stage"
        )
    cases = stage.get("cases")
    if not isinstance(cases, list) or not cases:
        raise G5Error(f"{where}: the stage document records no cases")
    failed = [
        case.get("case") for case in cases
        if not isinstance(case, dict) or case.get("ok") is not True
    ]
    if failed:
        raise G5Error(f"{where}: {len(failed)} case(s) failed: {failed[:5]}")
    return {
        str(case["case"]): case for case in cases if isinstance(case, dict) and "case" in case
    }


def verify_stage(stage: dict[str, Any], run: dict[str, Any], where: str = "stage") -> str:
    """The stage artifact, checked for the cases the amendment puts in this stage."""

    cases = _stage_cases(stage, where)
    missing: list[str] = []
    for case, case_ids in G5_LIVE_CASES.items():
        absent = [case_id for case_id in case_ids if case_id not in cases]
        if absent:
            missing.append(f"{case}: {absent}")
    if missing:
        raise G5Error(
            f"{where}: the stage does not carry every live Perspective case D26's Phase 5 "
            f"amendment names: {missing}"
        )
    return f"{len(cases)} cases, {len(G5_LIVE_CASES)} live Perspective cases corroborated"


def _verify_identity(identity: dict[str, Any], run: dict[str, Any], where: str) -> dict[str, Any]:
    for key, expected in (
        ("runId", run["runId"]),
        ("sourceRevision", run["head"]),
    ):
        if identity.get(key) != expected:
            raise G5Error(f"{where}: {key} is {identity.get(key)!r}, the row claims {expected!r}")
    for key in ("gatewayVersion", "gatewayBuild", "gatewayImage", "gatewayImageDigest"):
        if not isinstance(identity.get(key), str) or not identity[key]:
            raise G5Error(f"{where}: {key} is missing from the run identity")
    return identity


def _load_cited_row(evidence_root: Path, name: str, where: str) -> dict[str, Any]:
    manifest = evidence_root / name / "evidence.json"
    if not manifest.is_file():
        raise G5Error(f"{where}: {name!r} is not a row in {evidence_root}")
    document = _load(manifest, "cited row")
    if document.get("gate") not in {"G3", "G4"}:
        raise G5Error(f"{where}: {name!r} is a {document.get('gate')!r} row, not G3/G4 evidence")
    return document


def _cited_transaction(
    close: dict[str, Any], evidence_root: Path, repo_root: Path, where: str,
) -> dict[str, Any]:
    """The four cases the amendment cites, each checked against what it names."""

    declared = close.get("citedTransaction")
    if not isinstance(declared, dict) or set(declared) != set(G5_CITED_CASES):
        raise G5Error(f"{where}: citedTransaction must account for exactly {sorted(G5_CITED_CASES)}")
    resolved: dict[str, Any] = {}
    for case in G5_CITED_CASES:
        entry = declared[case]
        if not isinstance(entry, dict):
            raise G5Error(f"{where}: citedTransaction[{case!r}] must be an object")
        source = entry.get("source")
        evidence = entry.get("evidence")
        if not isinstance(source, str) or not source:
            raise G5Error(f"{where}: citedTransaction[{case!r}] must name its source")
        if not isinstance(evidence, str) or not evidence:
            raise G5Error(f"{where}: citedTransaction[{case!r}] must name the evidence it rests on")
        if (evidence_root / evidence / "evidence.json").is_file():
            _load_cited_row(evidence_root, evidence, f"{where}: citedTransaction[{case!r}]")
        elif not (repo_root / evidence).is_file():
            raise G5Error(
                f"{where}: citedTransaction[{case!r}] names {evidence!r}, which is neither a row "
                "in the evidence tree nor a file in this repository"
            )
        resolved[case] = {"verdict": "CITED", "evidence": evidence, "source": source}
    return resolved


def build_g5_row(
    close: dict[str, Any],
    stage: dict[str, Any],
    identity: dict[str, Any],
    run: dict[str, Any],
    *,
    evidence_root: Path,
    repo_root: Path,
) -> dict[str, Any]:
    """Compose one schema-valid G5 row from the close document and the stage artifact."""

    where = "close"
    if close.get("schemaVersion") != 1:
        raise G5Error(f"{where}: schemaVersion must be 1")
    if close.get("transactionServiceUnchanged") is not True:
        raise G5Error(
            f"{where}: transactionServiceUnchanged must be true, the D26 Phase 5 amendment "
            "condition for citing the transaction cases instead of running them live"
        )
    workflow = _text(run, "workflow", "run")
    if workflow not in RUN_WORKFLOWS:
        raise G5Error(f"run: workflow {workflow!r} is not a G5 stage workflow ({RUN_WORKFLOWS})")
    if run.get("conclusion") != "success":
        raise G5Error(f"run: conclusion is {run.get('conclusion')!r}; a green run is the only admissible live evidence")
    head = _text(run, "head", "run")
    if _SHA40.fullmatch(head) is None:
        raise G5Error("run: head must be a 40-character commit SHA")

    verified = _verify_identity(identity, run, "identity")
    corroborated = verify_stage(stage, run)
    cited = _load_cited_row(evidence_root, _text(close, "citedRow", where), where)
    citations = _cited_transaction(close, evidence_root, repo_root, where)

    declared_limitations = close.get("limitations")
    if not isinstance(declared_limitations, list) or not all(
        isinstance(item, str) and item for item in declared_limitations
    ):
        raise G5Error(f"{where}: limitations must be a list of strings (it may be empty)")
    unsatisfied = close.get("unsatisfiedAcceptance")
    if not isinstance(unsatisfied, list) or not all(isinstance(item, str) for item in unsatisfied):
        raise G5Error(f"{where}: unsatisfiedAcceptance must be a list of strings (it may be empty)")

    row: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "gate": GATE,
        # The tuple, the release and the binding status are the ones the cited row
        # established: this stage deploys no MCP Module, so it may not measure them.
        "bundleVersion": _text(cited, "bundleVersion", "cited row"),
        "bundleSha256": _text(cited, "bundleSha256", "cited row"),
        "gatewayVersion": verified["gatewayVersion"],
        "gatewayBuild": verified["gatewayBuild"],
        "gatewayImage": verified["gatewayImage"],
        "gatewayImageDigest": verified["gatewayImageDigest"],
        "mcpModuleVersion": _text(cited, "mcpModuleVersion", "cited row"),
        "mcpModuleArtifactVersion": _text(cited, "mcpModuleArtifactVersion", "cited row"),
        "mcpModuleBuild": _text(cited, "mcpModuleBuild", "cited row"),
        "mcpModuleSha256": _text(cited, "mcpModuleSha256", "cited row"),
        "nativeResponseBinding": _text(cited, "nativeResponseBinding", "cited row"),
        "d27ExceptionApplied": cited.get("d27ExceptionApplied"),
        "outputSchemaPublished": cited.get("outputSchemaPublished", False),
        "status": _text(cited, "status", "cited row"),
        # D21: Phase 5 never certifies deployment compatibility.
        "compatibilityStatus": "UNTESTED",
        "gateResult": "VERIFIED" if not declared_limitations and not unsatisfied else "VERIFIED_WITH_LIMITATION",
        "sourceRevision": head,
        "ownerAcceptedDeviations": ["phase4-live-environment-protection"],
        "transactionServiceUnchanged": True,
        "runs": [
            {
                "runId": run["runId"],
                "workflow": workflow,
                "head": head,
                "conclusion": run["conclusion"],
                "gatewayVersion": verified["gatewayVersion"],
                "gatewayBuild": verified["gatewayBuild"],
                "gatewayImage": verified["gatewayImage"],
                "gatewayImageDigest": verified["gatewayImageDigest"],
                "harnessDocument": STAGE_DOCUMENT,
                "citation": "the class-enabled pass, whose Perspective cases are this row's live evidence",
            }
        ],
        "g5": {
            "livePerspective": {
                case: {
                    "verdict": "LIVE",
                    "runIds": [run["runId"]],
                    "source": (
                        "the class-enabled pass of "
                        f"{workflow} run {run['runId']}: "
                        + ", ".join(case_ids)
                        + f" ({STAGE_DOCUMENT})"
                    ),
                }
                for case, case_ids in G5_LIVE_CASES.items()
            },
            "citedTransaction": citations,
        },
        "limitations": [DELEGATION_LIMITATION, *declared_limitations],
        "unsatisfiedAcceptance": unsatisfied,
        "stageCorroboration": corroborated,
    }
    if not isinstance(row["d27ExceptionApplied"], bool):
        raise G5Error(f"{where}: the cited row does not record an explicit d27 exception flag")
    return row


def generate_g5(
    close_path: Path,
    stage_path: Path,
    identity_path: Path,
    out_dir: Path,
    *,
    evidence_root: Path | None = None,
    repo_root: Path | None = None,
    run: dict[str, Any],
) -> Path:
    """Write and self-validate one G5 row; returns the row directory."""

    close = _load(close_path, "close document")
    stage = _load(stage_path, "stage document")
    identity = _load(identity_path, "run identity")
    tree = evidence_root or out_dir
    root = repo_root or Path(__file__).resolve().parents[2]
    row = build_g5_row(close, stage, identity, dict(run), evidence_root=tree, repo_root=root)
    directory = out_dir / f"g5-{row['gatewayVersion']}-mcp-{row['mcpModuleBuild']}"
    if directory.exists():
        raise G5Error(f"{directory}: refusing to overwrite existing evidence (frozen once written)")
    directory.mkdir(parents=True)
    (directory / "evidence.json").write_text(
        json.dumps(row, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    try:
        # Validate the row the way the tree's loader will: the rules resolve a cited row
        # against the evidence tree, so the probe directory has to sit in that tree.
        parse_row(tree / f"g5-{row['gatewayVersion']}-mcp-{row['mcpModuleBuild']}", row)
    except EvidenceError as error:
        raise G5Error(f"generated row rejected by the validator: {error}") from error
    return directory
