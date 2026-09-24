"""G7 row (issue #78): the composer refuses anything the stage document does not show.

The stage document has the shape ``tests/harness/phase4-live/setup_stage.py`` writes.
The tests cover the happy path, a refusal per case, a red run, and SUPPORTED.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil

import pytest

from tooling.compat.evidence import G7_LIVE_CASES, EvidenceError, load_evidence, parse_row
from tooling.compat.g7 import G7Error, build_g7_row, generate_g7

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE = ROOT / "tests/compatibility/evidence"

RUN = {
    "runId": "37000000000",
    "workflow": "Phase 4 Live Gateway apply",
    "head": "a" * 40,
    "conclusion": "success",
}


def _stage() -> dict:
    def role(tools: int, other: str) -> dict:
        names = [f"tool_{index}" for index in range(tools)]
        return {
            "inventoryMatches": True,
            "tools": names,
            "toolCount": tools,
            "otherTokensRefused": {other: "MCP endpoint returned HTTP 403"},
        }

    return {
        "schemaVersion": 1,
        "stage": "g7-setup-cli",
        "bundleVersion": "0.7.0",
        "ok": True,
        "steps": {
            "setup": {
                "exitCode": 0,
                "changedSteps": ["runtime bundle", "runtime module", "runtime policy"],
                "steps": [
                    {"step": "runtime module", "status": "CHANGED", "reason": "installed"},
                    {"step": "runtime check analysis", "status": "OK", "reason": "10 checks passed"},
                    {"step": "runtime check engineer", "status": "OK", "reason": "10 checks passed"},
                ],
            },
            "roles": {"analysis": role(13, "engineer"), "engineer": role(22, "analysis")},
            "rest": {
                "analysis": {"readAllowed": True, "mutationRefused": "permission_denied"},
                "engineer": {"mutationScopeGranted": True},
            },
            "setupAgain": {"exitCode": 0, "plannedChanges": 0, "changedSteps": []},
            "reset": {
                "exitCode": 0,
                "created": ["module", "project:ignition_runtime", "policy"],
                "leftOnGateway": {},
                "deploymentDirectoryRemoved": True,
            },
        },
    }


def _identity() -> dict:
    return {
        "schemaVersion": 1,
        "runId": RUN["runId"],
        "sourceRevision": RUN["head"],
        "gatewayVersion": "8.3.8",
        "gatewayBuild": "2026071409",
        "gatewayImage": "inductiveautomation/ignition:8.3.8",
        "gatewayImageDigest": "sha256:" + "b" * 64,
        "mcpModuleVersion": "1.3.5-SNAPSHOT",
        "mcpModuleArtifactVersion": "1.3.5.2026021307-SNAPSHOT",
        "mcpModuleBuild": "2026021307",
        "mcpModuleSha256": "b1142a5796f2fd834555f13f03de706599d745f7172a68e54f2f7908b67fe365",
        "bundleVersion": "0.7.0",
        "bundleSha256": "c" * 64,
        "nativeResponseBindingStatus": "VERIFIED_WITH_LIMITATION",
    }


def test_the_composed_row_passes_the_g7_rules(tmp_path: Path) -> None:
    row = build_g7_row(_stage(), _identity(), RUN)
    assert row["gate"] == "G7"
    assert row["compatibilityStatus"] == "UNTESTED"
    assert set(row["g7"]["cases"]) == set(G7_LIVE_CASES)
    assert row["d27ExceptionApplied"] is True
    parse_row(tmp_path / "g7-8.3.8-mcp-2026021307", row)


def test_generate_g7_writes_a_row_the_committed_tree_accepts(tmp_path: Path) -> None:
    tree = tmp_path / "evidence"
    shutil.copytree(EVIDENCE, tree)
    stage_path = tmp_path / "setup-g7.json"
    stage_path.write_text(json.dumps(_stage()), encoding="utf-8")
    identity_path = tmp_path / "identity.json"
    identity_path.write_text(json.dumps(_identity()), encoding="utf-8")
    directory = generate_g7(stage_path, identity_path, tree, evidence_root=tree, run=RUN)
    assert directory.name == "g7-8.3.8-mcp-2026021307"
    assert "G7" in {row.gate for row in load_evidence(tree)}
    with pytest.raises(G7Error, match="refusing to overwrite"):
        generate_g7(stage_path, identity_path, tree, evidence_root=tree, run=RUN)


@pytest.mark.parametrize(
    ("step", "change", "message"),
    [
        ("setup", {"changedSteps": ["runtime bundle"]}, "did not install the Module"),
        ("roles", {"analysis": {"inventoryMatches": False}}, "exact inventory"),
        ("rest", {"analysis": {"readAllowed": True, "mutationRefused": ""}}, "read-only"),
        ("setupAgain", {"changedSteps": ["runtime bundle"]}, "planned or changed"),
        ("reset", {"leftOnGateway": {"policy": "GET still answers 200"}}, "left something behind"),
    ],
)
def test_each_case_refuses_a_stage_that_does_not_show_it(step: str, change: dict, message: str) -> None:
    stage = copy.deepcopy(_stage())
    stage["steps"][step].update(change)
    with pytest.raises(G7Error, match=message):
        build_g7_row(stage, _identity(), RUN)


def test_a_stage_that_is_not_green_is_refused() -> None:
    stage = _stage()
    stage["ok"] = False
    with pytest.raises(G7Error, match="green stage"):
        build_g7_row(stage, _identity(), RUN)


def test_a_red_run_is_refused() -> None:
    with pytest.raises(G7Error, match="green run"):
        build_g7_row(_stage(), _identity(), {**RUN, "conclusion": "failure"})


def test_g7_rules_refuse_supported_and_a_partial_case_split(tmp_path: Path) -> None:
    row = build_g7_row(_stage(), _identity(), RUN)
    supported = {**row, "compatibilityStatus": "SUPPORTED"}
    with pytest.raises(EvidenceError, match="SUPPORTED"):
        parse_row(tmp_path / "g7-x", supported)
    partial = copy.deepcopy(row)
    partial["g7"]["cases"].pop(next(iter(G7_LIVE_CASES)))
    with pytest.raises(EvidenceError, match="g7.cases"):
        parse_row(tmp_path / "g7-x", partial)
