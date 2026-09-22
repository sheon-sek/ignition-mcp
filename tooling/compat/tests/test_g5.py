"""G5 close-out row (D26's Phase 5 amendment, ticket P5-3).

The composer's whole job is to refuse: a row is admissible only when the run it cites is
green, when the stage document really carries every live Perspective case the amendment
names, and when every cited transaction case resolves to committed G3/G4 evidence. These
tests pin those refusals against a stage document of the driver's own shape.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil

import pytest

from tooling.compat.evidence import (
    EvidenceError,
    G5_CITED_CASES,
    G5_LIVE_CASES,
    load_evidence,
    parse_row,
)
from tooling.compat.g5 import G5Error, build_g5_row, generate_g5

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE = ROOT / "tests/compatibility/evidence"
CLOSE = ROOT / "tests/compatibility/g5/close-8.3.8.json"
G4_ROW = "g4-8.3.8-mcp-2026021307"

RUN = {
    "runId": "35999999999",
    "workflow": "Phase 4 Live Gateway REST mutation",
    "head": "a" * 40,
    "conclusion": "success",
}


def _stage() -> dict:
    """A class-enabled pass of the driver's shape, with the live cases it must carry."""

    case_ids = [case_id for case_ids in G5_LIVE_CASES.values() for case_id in case_ids]
    cases = [
        {"case": "inventory-agent-exact", "expected": [], "observed": [], "ok": True},
        *(
            {"case": case_id, "expected": "x", "observed": "x", "ok": True}
            for case_id in case_ids
        ),
    ]
    return {"schemaVersion": 1, "gate": "G4", "modes": ["gate-on"], "cases": cases, "passed": True}


def _identity() -> dict:
    return {
        "schemaVersion": 1,
        "gatewayVersion": "8.3.8",
        "gatewayBuild": "2026071409",
        "gatewayImage": "inductiveautomation/ignition:8.3.8",
        "gatewayImageDigest": "sha256:" + "b" * 64,
        "sourceRevision": RUN["head"],
        "runId": RUN["runId"],
    }


def _tree(tmp_path: Path) -> Path:
    """An evidence tree holding only the row the close document cites."""

    tree = tmp_path / "evidence"
    tree.mkdir()
    shutil.copytree(EVIDENCE / G4_ROW, tree / G4_ROW)
    return tree


def _close() -> dict:
    return json.loads(CLOSE.read_text(encoding="utf-8"))


def _row(tmp_path: Path, stage: dict | None = None, close: dict | None = None) -> dict:
    tree = _tree(tmp_path)
    return build_g5_row(
        close or _close(), stage or _stage(), _identity(), RUN,
        evidence_root=tree, repo_root=ROOT,
    )


def test_the_composed_row_passes_the_g5_rules(tmp_path: Path) -> None:
    row = _row(tmp_path)
    assert row["gate"] == "G5"
    assert row["gateResult"] == "VERIFIED"
    assert row["compatibilityStatus"] == "UNTESTED"
    assert set(row["g5"]["livePerspective"]) == set(G5_LIVE_CASES)
    assert set(row["g5"]["citedTransaction"]) == set(G5_CITED_CASES)
    for entry in row["g5"]["livePerspective"].values():
        assert entry["verdict"] == "LIVE" and entry["runIds"] == [RUN["runId"]]
    # The tuple and binding status come from the row the close document cites, never
    # from this stage: it deploys no MCP Module of its own.
    cited = json.loads((EVIDENCE / G4_ROW / "evidence.json").read_text(encoding="utf-8"))
    assert row["mcpModuleSha256"] == cited["mcpModuleSha256"]
    assert row["nativeResponseBinding"] == cited["nativeResponseBinding"]
    assert row["d27ExceptionApplied"] is cited["d27ExceptionApplied"]


def test_generate_g5_writes_a_row_the_evidence_tree_accepts(tmp_path: Path) -> None:
    tree = _tree(tmp_path)
    stage_path = tmp_path / "observations.json"
    stage_path.write_text(json.dumps(_stage()), encoding="utf-8")
    identity_path = tmp_path / "identity.json"
    identity_path.write_text(json.dumps(_identity()), encoding="utf-8")
    directory = generate_g5(
        CLOSE, stage_path, identity_path, tree, evidence_root=tree, repo_root=ROOT, run=RUN,
    )
    assert directory == tree / "g5-8.3.8-mcp-2026021307"
    rows = load_evidence(tree)
    g5 = [row for row in rows if row.gate == "G5"]
    assert len(g5) == 1
    assert g5[0].is_d27_tuple


def test_a_live_case_the_stage_does_not_carry_is_refused(tmp_path: Path) -> None:
    stage = _stage()
    dropped = G5_LIVE_CASES["unrelated-resource preservation"][0]
    stage["cases"] = [case for case in stage["cases"] if case["case"] != dropped]
    with pytest.raises(G5Error) as caught:
        _row(tmp_path, stage=stage)
    assert dropped in str(caught.value)


def test_a_failed_case_is_never_corroborated(tmp_path: Path) -> None:
    stage = _stage()
    stage["cases"][-1]["ok"] = False
    stage["passed"] = False
    with pytest.raises(G5Error) as caught:
        _row(tmp_path, stage=stage)
    assert "green stage" in str(caught.value)


def test_a_row_may_not_be_composed_from_inside_a_run_whose_conclusion_is_unknown(
    tmp_path: Path,
) -> None:
    tree = _tree(tmp_path)
    with pytest.raises(G5Error) as caught:
        build_g5_row(
            _close(), _stage(), _identity(), {**RUN, "conclusion": ""},
            evidence_root=tree, repo_root=ROOT,
        )
    assert "green run" in str(caught.value)


def test_the_delegation_condition_must_be_recorded(tmp_path: Path) -> None:
    close = _close()
    close["transactionServiceUnchanged"] = False
    with pytest.raises(G5Error) as caught:
        _row(tmp_path, close=close)
    assert "transactionServiceUnchanged" in str(caught.value)


def test_a_cited_case_must_name_evidence_that_exists(tmp_path: Path) -> None:
    close = _close()
    close["citedTransaction"]["backup failure abort-before-import"]["evidence"] = "g9-nope"
    with pytest.raises(G5Error) as caught:
        _row(tmp_path, close=close)
    assert "g9-nope" in str(caught.value)


def test_g5_rules_refuse_a_cited_case_whose_row_is_absent(tmp_path: Path) -> None:
    row = _row(tmp_path)
    row["g5"]["citedTransaction"]["backup failure abort-before-import"]["evidence"] = (
        "g4-9.9.9-mcp-2026021307"
    )
    with pytest.raises(EvidenceError) as caught:
        parse_row(tmp_path / "evidence" / "g5-8.3.8-mcp-2026021307", row)
    assert "not in this evidence tree" in str(caught.value)


def test_g5_rules_refuse_a_live_claim_with_no_cited_run(tmp_path: Path) -> None:
    row = _row(tmp_path)
    row["g5"]["livePerspective"]["no-op"]["runIds"] = []
    with pytest.raises(EvidenceError) as caught:
        parse_row(tmp_path / "evidence" / "g5-8.3.8-mcp-2026021307", row)
    assert "claims LIVE" in str(caught.value)


def test_g5_rules_refuse_a_live_claim_that_names_an_uncited_run(tmp_path: Path) -> None:
    row = _row(tmp_path)
    row["g5"]["livePerspective"]["no-op"]["runIds"] = ["1"]
    with pytest.raises(EvidenceError) as caught:
        parse_row(tmp_path / "evidence" / "g5-8.3.8-mcp-2026021307", row)
    assert "runs[] does not hold" in str(caught.value)


def test_g5_rules_refuse_a_partial_case_split(tmp_path: Path) -> None:
    row = _row(tmp_path)
    row["g5"]["citedTransaction"].pop(next(iter(G5_CITED_CASES)))
    with pytest.raises(EvidenceError) as caught:
        parse_row(tmp_path / "evidence" / "g5-8.3.8-mcp-2026021307", row)
    assert "citedTransaction" in str(caught.value)


def test_g5_rules_require_the_phase4_live_deviation(tmp_path: Path) -> None:
    row = _row(tmp_path)
    row["ownerAcceptedDeviations"] = []
    with pytest.raises(EvidenceError) as caught:
        parse_row(tmp_path / "evidence" / "g5-8.3.8-mcp-2026021307", row)
    assert "phase4-live-environment-protection" in str(caught.value)


def test_the_two_close_documents_cite_the_rows_of_their_own_tuple() -> None:
    for version in ("8.3.8", "8.3.9"):
        document = json.loads(
            (ROOT / f"tests/compatibility/g5/close-{version}.json").read_text(encoding="utf-8")
        )
        cited = document["citedRow"]
        assert (EVIDENCE / cited / "evidence.json").is_file()
        assert json.loads((EVIDENCE / cited / "evidence.json").read_text(encoding="utf-8"))[
            "gatewayVersion"
        ] == version
        assert set(document["citedTransaction"]) == set(G5_CITED_CASES)
        for entry in document["citedTransaction"].values():
            assert (EVIDENCE / entry["evidence"] / "evidence.json").is_file()


def test_a_row_copied_from_another_stage_is_still_validated(tmp_path: Path) -> None:
    """The composer never relaxes a rule for a row it did not build."""

    row = copy.deepcopy(_row(tmp_path))
    row["gateResult"] = "VERIFIED_WITH_LIMITATION"
    row["limitations"] = []
    from tooling.compat.evidence import parse_row

    with pytest.raises(EvidenceError) as caught:
        parse_row(tmp_path / "evidence" / "g5-8.3.8-mcp-2026021307", row)
    assert "limitations" in str(caught.value)
