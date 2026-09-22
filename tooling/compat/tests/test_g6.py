"""G6 close-out row (D26's final v1 release gate, ticket #56).

The composer's whole job is to refuse: a row is admissible only when the run it cites
is green, when the stage document really records every step the gate owns (the Module
install and its NO CHANGE read-back, the fresh apply and its idempotency check, the
Bundle upgrade under acknowledgement), and when the row never claims SUPPORTED. These
tests pin those refusals against a stage document of the apply stage's own shape.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil

import pytest

from tooling.compat.evidence import (
    EvidenceError,
    G6_LIVE_CASES,
    load_evidence,
    parse_row,
)
from tooling.compat.g6 import G6Error, build_g6_row, generate_g6

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE = ROOT / "tests/compatibility/evidence"

RUN = {
    "runId": "36999999999",
    "workflow": "Phase 4 Live Gateway apply",
    "head": "a" * 40,
    "conclusion": "success",
}


def _stage() -> dict:
    """A green apply-stage document of the ticket #56 round-1 shape."""

    return {
        "schemaVersion": 1,
        "ticket": 22,
        "ok": True,
        "bundle": {
            "filename": "ignition-runtime-bundle-0.7.0.zip",
            "version": "0.7.0",
            "sha256": "c" * 64,
            "nativeResponseBindingStatus": "VERIFIED_WITH_LIMITATION",
        },
        "startingBundleVersion": "0.6.0",
        "steps": {
            "install-module": {
                "exitCode": 0,
                "report": {
                    "outcome": "INSTALL",
                    "moduleBuild": "2026021307",
                    "restart": {"requested": True, "ready": True, "build": "2026021307"},
                },
            },
            "install-module-again": {"exitCode": 0, "report": {"outcome": "NO CHANGE"}},
            "apply": {
                "exitCode": 0,
                "report": {"writes": [
                    {"kind": "security-level", "action": "CREATE", "ok": True},
                    {"kind": "runtime-token", "action": "CREATE", "ok": True},
                    {"kind": "bundle-project", "action": "CREATE", "ok": True},
                    {"kind": "server-config", "action": "CREATE", "ok": True},
                    {"kind": "runtime-policy", "action": "CREATE", "ok": True},
                ]},
            },
            "secondApply": {
                "exitCode": 0,
                "report": {"writes": [
                    {"kind": "security-level", "action": "NO CHANGE", "ok": True},
                    {"kind": "bundle-project", "action": "NO CHANGE", "ok": True},
                ]},
            },
            "upgradeApply": {
                "bundleVersionBefore": "0.6.0",
                "bundleVersionAfter": "0.7.0",
                "planLine": {
                    "action": "UPDATE",
                    "kind": "bundle-project",
                    "name": "ignition_runtime_apply",
                    "reason": "redeploy managed bundle 0.6.0 -> 0.7.0 (minor)",
                },
                "exitCode": 0,
                "verifyAttempts": 1,
                "verifyGreen": True,
                "verifyRetries": [],
                "report": {"writes": [
                    {"kind": "bundle-project", "action": "UPDATE", "ok": True},
                ]},
            },
        },
    }


def _identity() -> dict:
    """The run identity exactly as the workflow's jq step writes it: machine facts only."""

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


def _tree(tmp_path: Path) -> Path:
    """An evidence tree: the G6 rules validate the row the loader's way."""

    tree = tmp_path / "evidence"
    tree.mkdir()
    shutil.copytree(EVIDENCE / "g4-8.3.8-mcp-2026021307", tree / "g4-8.3.8-mcp-2026021307")
    return tree


def _row(tmp_path: Path, stage: dict | None = None, identity: dict | None = None) -> dict:
    return build_g6_row(stage or _stage(), identity or _identity(), RUN)


def test_the_composed_row_passes_the_g6_rules(tmp_path: Path) -> None:
    row = build_g6_row(_stage(), _identity(), RUN)
    assert row["gate"] == "G6"
    assert row["gateResult"] == "VERIFIED"
    assert row["compatibilityStatus"] == "UNTESTED"
    assert set(row["g6"]["cases"]) == set(G6_LIVE_CASES)
    for entry in row["g6"]["cases"].values():
        assert entry["verdict"] == "LIVE" and entry["runIds"] == [RUN["runId"]]
    # The row measures its own tuple: the stage installed this Module and deployed
    # this release, so the identity comes from the run, not from a cited row.
    assert row["mcpModuleSha256"] == _identity()["mcpModuleSha256"]
    assert row["bundleVersion"] == _identity()["bundleVersion"]
    # The binding facts are derived, never hand-authored: the D27 exception applies
    # to the exact characterized tuple only.
    assert row["d27ExceptionApplied"] is True
    assert row["nativeResponseBinding"] == "VERIFIED_WITH_LIMITATION"
    assert row["outputSchemaPublished"] is False
    assert row["status"] == "VERIFIED"


def test_a_non_d27_tuple_is_recorded_honestly() -> None:
    identity = _identity()
    identity.update({
        "gatewayVersion": "8.3.9", "gatewayBuild": "2026082511",
        "gatewayImage": "inductiveautomation/ignition:8.3.9",
    })
    row = build_g6_row(_stage(), identity, RUN)
    assert row["d27ExceptionApplied"] is False
    assert row["nativeResponseBinding"] == "UNVERIFIED_LIMITATION"
    assert row["outputSchemaPublished"] is False
    assert row["status"] == "FAILED_NATIVE_BINDING"


def test_generate_g6_writes_a_row_the_evidence_tree_accepts(tmp_path: Path) -> None:
    tree = _tree(tmp_path)
    stage_path = tmp_path / "setup-native-apply.json"
    stage_path.write_text(json.dumps(_stage()), encoding="utf-8")
    identity_path = tmp_path / "identity.json"
    identity_path.write_text(json.dumps(_identity()), encoding="utf-8")
    directory = generate_g6(
        stage_path, identity_path, tree, evidence_root=tree, run=RUN,
    )
    assert directory == tree / "g6-8.3.8-mcp-2026021307"
    rows = load_evidence(tree)
    g6 = [row for row in rows if row.gate == "G6"]
    assert len(g6) == 1
    assert g6[0].is_d27_tuple
    # The committed tree with the generated row still validates as a whole. The
    # repository tree now already holds both G6 rows, so the generated row replaces
    # its own copy there instead of colliding with it.
    with_committed = tmp_path / "with-committed"
    with_committed.mkdir()
    shutil.copytree(ROOT / "tests/compatibility/evidence", with_committed / "evidence")
    shutil.copytree(directory, with_committed / "evidence" / directory.name, dirs_exist_ok=True)
    assert {row.gate for row in load_evidence(with_committed / "evidence")} == {
        "G0", "G1", "G2", "G3", "G4", "G5", "G6",
    }


def test_a_stage_that_is_not_green_is_refused(tmp_path: Path) -> None:
    stage = _stage()
    stage["ok"] = False
    with pytest.raises(G6Error) as caught:
        build_g6_row(stage, _identity(), RUN)
    assert "green stage" in str(caught.value)


def test_a_failing_install_is_refused(tmp_path: Path) -> None:
    stage = _stage()
    stage["steps"]["install-module"]["exitCode"] = 1
    with pytest.raises(G6Error) as caught:
        build_g6_row(stage, _identity(), RUN)
    assert "install-module" in str(caught.value)


def test_an_install_that_never_proved_the_restart_is_refused(tmp_path: Path) -> None:
    stage = _stage()
    stage["steps"]["install-module"]["report"]["restart"]["ready"] = False
    with pytest.raises(G6Error) as caught:
        build_g6_row(stage, _identity(), RUN)
    assert "restart" in str(caught.value)


def test_a_second_install_that_uploads_again_is_refused(tmp_path: Path) -> None:
    stage = _stage()
    stage["steps"]["install-module-again"]["report"]["outcome"] = "INSTALL"
    with pytest.raises(G6Error) as caught:
        build_g6_row(stage, _identity(), RUN)
    assert "NO CHANGE" in str(caught.value)


def test_a_second_apply_that_writes_something_is_refused(tmp_path: Path) -> None:
    stage = _stage()
    stage["steps"]["secondApply"]["report"]["writes"][0]["action"] = "UPDATE"
    with pytest.raises(G6Error) as caught:
        build_g6_row(stage, _identity(), RUN)
    assert "second apply" in str(caught.value)


def test_a_missing_upgrade_step_is_refused(tmp_path: Path) -> None:
    stage = _stage()
    del stage["steps"]["upgradeApply"]
    with pytest.raises(G6Error) as caught:
        build_g6_row(stage, _identity(), RUN)
    assert "upgradeApply" in str(caught.value)


def test_an_upgrade_that_moves_the_version_backward_is_refused(tmp_path: Path) -> None:
    stage = _stage()
    stage["steps"]["upgradeApply"]["bundleVersionBefore"] = "0.8.0"
    with pytest.raises(G6Error) as caught:
        build_g6_row(stage, _identity(), RUN)
    assert "does not move the bundle version forward" in str(caught.value)


def test_an_upgrade_plan_that_observes_something_else_is_refused(tmp_path: Path) -> None:
    stage = _stage()
    stage["steps"]["upgradeApply"]["planLine"]["reason"] = "redeploy managed bundle 0.5.0 -> 0.7.0 (minor)"
    with pytest.raises(G6Error) as caught:
        build_g6_row(stage, _identity(), RUN)
    assert "observed transition" in str(caught.value)


def test_an_upgrade_that_names_the_wrong_after_version_in_the_plan_is_refused(
    tmp_path: Path,
) -> None:
    stage = _stage()
    stage["steps"]["upgradeApply"]["planLine"]["reason"] = "redeploy managed bundle 0.6.0 -> 0.9.0 (minor)"
    with pytest.raises(G6Error) as caught:
        build_g6_row(stage, _identity(), RUN)
    assert "observed transition" in str(caught.value)


def test_an_upgrade_that_deploys_another_bundle_than_the_release_is_refused(tmp_path: Path) -> None:
    stage = _stage()
    stage["steps"]["upgradeApply"]["bundleVersionAfter"] = "0.9.0"
    stage["steps"]["upgradeApply"]["planLine"]["reason"] = "redeploy managed bundle 0.6.0 -> 0.9.0 (minor)"
    with pytest.raises(G6Error) as caught:
        build_g6_row(stage, _identity(), RUN)
    assert "not the released bundle" in str(caught.value)


def test_an_upgrade_that_writes_more_than_the_project_is_refused(tmp_path: Path) -> None:
    stage = _stage()
    stage["steps"]["upgradeApply"]["report"]["writes"].append(
        {"kind": "server-config", "action": "UPDATE", "ok": True},
    )
    with pytest.raises(G6Error) as caught:
        build_g6_row(stage, _identity(), RUN)
    assert "not exactly the bundle project" in str(caught.value)


def test_an_upgrade_without_a_green_verify_is_refused(tmp_path: Path) -> None:
    stage = _stage()
    stage["steps"]["upgradeApply"]["verifyGreen"] = False
    with pytest.raises(G6Error) as caught:
        build_g6_row(stage, _identity(), RUN)
    assert "no green verify" in str(caught.value)


def test_a_stage_that_deployed_another_release_than_the_identity_names_is_refused(
    tmp_path: Path,
) -> None:
    stage = _stage()
    stage["bundle"]["sha256"] = "d" * 64
    with pytest.raises(G6Error) as caught:
        build_g6_row(stage, _identity(), RUN)
    assert "the stage deployed bundle" in str(caught.value)


def test_an_identity_without_the_binding_status_is_refused(tmp_path: Path) -> None:
    identity = _identity()
    del identity["nativeResponseBindingStatus"]
    with pytest.raises(G6Error) as caught:
        build_g6_row(_stage(), identity, RUN)
    assert "nativeResponseBindingStatus" in str(caught.value)


def test_a_row_may_not_be_composed_from_a_red_run(tmp_path: Path) -> None:
    with pytest.raises(G6Error) as caught:
        build_g6_row(_stage(), _identity(), {**RUN, "conclusion": "failure"})
    assert "green run" in str(caught.value)


def test_a_row_may_not_cite_a_foreign_workflow(tmp_path: Path) -> None:
    with pytest.raises(G6Error) as caught:
        build_g6_row(_stage(), _identity(), {**RUN, "workflow": "Phase 4 Live Gateway REST mutation"})
    assert "not a G6 stage workflow" in str(caught.value)


def test_an_identity_that_disagrees_with_the_run_is_refused(tmp_path: Path) -> None:
    identity = _identity()
    identity["runId"] = "1"
    with pytest.raises(G6Error) as caught:
        build_g6_row(_stage(), identity, RUN)
    assert "runId" in str(caught.value)


def test_g6_rules_refuse_a_live_claim_that_names_an_uncited_run(tmp_path: Path) -> None:
    row = build_g6_row(_stage(), _identity(), RUN)
    row["g6"]["cases"]["module install"]["runIds"] = ["1"]
    with pytest.raises(EvidenceError) as caught:
        parse_row(tmp_path / "evidence" / "g6-8.3.8-mcp-2026021307", row)
    assert "runs[] does not hold" in str(caught.value)


def test_g6_rules_refuse_a_partial_case_split(tmp_path: Path) -> None:
    row = build_g6_row(_stage(), _identity(), RUN)
    row["g6"]["cases"].pop(next(iter(G6_LIVE_CASES)))
    with pytest.raises(EvidenceError) as caught:
        parse_row(tmp_path / "evidence" / "g6-8.3.8-mcp-2026021307", row)
    assert "g6.cases" in str(caught.value)


def test_g6_rules_require_the_phase4_live_deviation(tmp_path: Path) -> None:
    row = build_g6_row(_stage(), _identity(), RUN)
    row["ownerAcceptedDeviations"] = []
    with pytest.raises(EvidenceError) as caught:
        parse_row(tmp_path / "evidence" / "g6-8.3.8-mcp-2026021307", row)
    assert "phase4-live-environment-protection" in str(caught.value)


def test_g6_rules_refuse_supported(tmp_path: Path) -> None:
    row = build_g6_row(_stage(), _identity(), RUN)
    row["compatibilityStatus"] = "SUPPORTED"
    with pytest.raises(EvidenceError) as caught:
        parse_row(tmp_path / "evidence" / "g6-8.3.8-mcp-2026021307", row)
    assert "SUPPORTED" in str(caught.value)


def test_a_row_copied_from_another_stage_is_still_validated(tmp_path: Path) -> None:
    """The composer never relaxes a rule for a row it did not build."""

    row = copy.deepcopy(build_g6_row(_stage(), _identity(), RUN))
    row["gateResult"] = "VERIFIED_WITH_LIMITATION"
    row["limitations"] = []
    with pytest.raises(EvidenceError) as caught:
        parse_row(tmp_path / "evidence" / "g6-8.3.8-mcp-2026021307", row)
    assert "limitations" in str(caught.value)
