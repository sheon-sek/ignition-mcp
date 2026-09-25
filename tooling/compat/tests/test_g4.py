"""Ticket #23 (Phase 4 / G4): the G4 close-out row.

The committed rows are a close-out composition, not one run's row, so the tests
below do two things: they prove that the two committed rows are exactly what
their committed close documents say (the L5 matrix, the limitations, the
inventories and the gate result), and they prove that the builder refuses every
dishonest composition it can be handed — a red or drifting run, an artifact whose
identity disagrees, an inventory that drifted from the contract linter, a live
claim for a case D30 makes fixture-only, and a gate result the matrix does not
support.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
from typing import Any
import unittest

from tooling.compat.evidence import EvidenceError, load_evidence
from tooling.compat.g4 import G4Error, build_g4_row, generate_g4

REPO_ROOT = Path(__file__).resolve().parents[3]
REPO_EVIDENCE = REPO_ROOT / "tests/compatibility/evidence"
CLOSE_DIR = REPO_ROOT / "tests/compatibility/g4"
CLOSE_838 = CLOSE_DIR / "close-8.3.8.json"
CLOSE_839 = CLOSE_DIR / "close-8.3.9.json"
ROW_838 = "g4-8.3.8-mcp-2026021307"
ROW_839 = "g4-8.3.9-mcp-2026021307"


def _close(version: str = "8.3.8") -> dict[str, Any]:
    return json.loads((CLOSE_838 if version == "8.3.8" else CLOSE_839).read_text(encoding="utf-8"))


def _write(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _runtime_summary(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "milestone": "4a" if run["workflow"].endswith("G4a") else "4b",
        "drift": {},
        "identity": {
            "runId": run["runId"],
            "gatewayVersion": run["gatewayVersion"],
            "gatewayBuild": run["gatewayBuild"],
            "mcpModuleVersion": run["mcpModuleVersion"],
            "mcpModuleBuild": run["mcpModuleBuild"],
            "mcpModuleSha256": run["mcpModuleSha256"],
            "sourceRevision": "a" * 40,
        },
        "stages": [{"stage": "tag-write", "ok": True, "guard": {"checks": {"marker": True}}}],
    }


def _rest_observations() -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "gate": "G4",
        "milestone": "4c",
        "passed": True,
        "modes": ["gate-on", "gate-off", "fault"],
        "cases": [{"case": "refused-resource-type-is-permission-denied", "ok": True}],
    }


def _artifacts(close: dict[str, Any], root: Path) -> Path:
    """A synthetic download tree for every run the close document cites."""

    for run in close["runs"]:
        directory = root / f"run-{run['runId']}" / "artifact"
        if "G4a" in run["workflow"] or "G4b" in run["workflow"]:
            _write(directory / "evidence.json", _runtime_summary(run))
            if run.get("bundleSha256"):
                (directory / "runtime.sha256").write_text(
                    f"{run['bundleSha256']}  runtime-bundle.zip\n", encoding="utf-8"
                )
        elif "REST" in run["workflow"]:
            _write(directory / "observations.json", _rest_observations())
            _write(directory / "identity.json", {
                "runId": run["runId"], "gatewayVersion": run["gatewayVersion"],
                "gatewayBuild": run["gatewayBuild"], "sourceRevision": "b" * 40,
            })
        else:
            _write(directory / "setup-native-apply.json", {
                "ok": True, "runId": run["runId"], "profile": "readonly",
                "serverConfig": "phase4-apply-runtime",
            })
    return root


class _Composer:
    """Compose + validate one close document against a synthetic artifact tree."""

    def __init__(self, close: dict[str, Any]) -> None:
        self.close = close
        self.temp = Path(tempfile.mkdtemp())
        self.artifacts = _artifacts(copy.deepcopy(close), self.temp / "artifacts")
        self.out = self.temp / "evidence"
        self.attempt = 0

    def compose(self, close: dict[str, Any] | None = None) -> Path:
        document = _write(self.temp / "close.json", close if close is not None else self.close)
        self.attempt += 1
        return generate_g4(document, self.out / str(self.attempt), self.artifacts)

    def summary(self) -> Path:
        return next(self.artifacts.glob("run-*/artifact/evidence.json"))

    def observations(self) -> Path:
        return next(self.artifacts.glob("run-*/artifact/observations.json"))


class G4RowTest(unittest.TestCase):
    def test_committed_rows_validate_and_match_their_close_documents(self) -> None:
        rows = load_evidence(REPO_EVIDENCE)
        # The G6 (ticket #56) and G7 (issue #78) rows are committed with their live runs' artifacts.
        self.assertEqual({row.gate for row in rows}, {"G0", "G1", "G2", "G3", "G4", "G5", "G6", "G7"})
        for version, directory in (("8.3.8", ROW_838), ("8.3.9", ROW_839)):
            row = next(item for item in rows if item.directory == directory)
            document = row.raw
            close = _close(version)
            self.assertEqual(row.compatibility_status, "UNTESTED")
            self.assertEqual(document["gate"], "G4")
            self.assertEqual(document["gateResult"], "VERIFIED_WITH_LIMITATION")
            for field in ("l5", "limitations", "inventories", "gateResult", "status",
                          "nativeResponseBinding", "d27ExceptionApplied", "bundleSha256"):
                self.assertEqual(document[field], close[field], f"{directory}: {field} drifted")
            self.assertEqual(document["deployedBundleSha256"], document["bundleSha256"])
            self.assertTrue(document["unsafeAutomaticRetryAbsent"])
            self.assertTrue(document["mutationsDisabledByDefault"])
            self.assertEqual(document["runtimeMutations"]["enabledTools"],
                             ["alarm_shelve", "alarm_unshelve", "tag_copy", "tag_create", "tag_delete",
                              "tag_move", "tag_rename", "tag_update", "tag_write"])

    def test_d30_fixture_only_cases_are_not_claimed_live(self) -> None:
        rows = load_evidence(REPO_EVIDENCE)
        for directory in (ROW_838, ROW_839):
            l5 = next(item for item in rows if item.directory == directory).raw["l5"]
            for case in ("timeout", "ambiguous outcome", "cancellation"):
                self.assertNotEqual(l5[case]["runtime"]["verdict"], "LIVE", f"{directory}: {case}")

    def test_synthetic_artifacts_compose_a_row_the_validator_accepts(self) -> None:
        composer = _Composer(_close())
        row = json.loads((composer.compose() / "evidence.json").read_text(encoding="utf-8"))
        self.assertEqual(row["gate"], "G4")
        self.assertEqual(row["gateResult"], "VERIFIED_WITH_LIMITATION")
        self.assertEqual(row["runs"][0]["runRevision"], "a" * 40)
        self.assertEqual(row["runs"][0]["verified"], "N stages=1 drift=clean")
        self.assertEqual(row["unsatisfiedAcceptance"], ["cancellation on the runtime plane"])
        self.assertEqual(build_g4_row(composer.close, composer.artifacts)["runs"],
                         row["runs"])

    def test_every_refusal_is_fail_closed(self) -> None:
        composer = _Composer(_close())

        live_runtime_timeout = copy.deepcopy(composer.close)
        live_runtime_timeout["l5"]["timeout"]["runtime"] = {"verdict": "LIVE", "runIds": ["1"],
                                                            "source": "claimed"}
        with self.assertRaisesRegex(G4Error, "fixture-only"):
            composer.compose(live_runtime_timeout)

        red = copy.deepcopy(composer.close)
        red["runs"][0]["conclusion"] = "failure"
        with self.assertRaisesRegex(G4Error, "only admissible live evidence"):
            composer.compose(red)

        drifting = _Composer(_close())
        summary = json.loads(drifting.summary().read_text(encoding="utf-8"))
        summary["drift"] = {"tag-write": "drifted"}
        drifting.summary().write_text(json.dumps(summary), encoding="utf-8")
        with self.assertRaisesRegex(G4Error, "drifted"):
            drifting.compose()

        failed_stage = _Composer(_close())
        summary = json.loads(failed_stage.summary().read_text(encoding="utf-8"))
        summary["stages"][0]["ok"] = False
        failed_stage.summary().write_text(json.dumps(summary), encoding="utf-8")
        with self.assertRaisesRegex(G4Error, "did not succeed"):
            failed_stage.compose()

        wrong_identity = _Composer(_close())
        summary = json.loads(wrong_identity.summary().read_text(encoding="utf-8"))
        summary["identity"]["gatewayBuild"] = "1970010100"
        wrong_identity.summary().write_text(json.dumps(summary), encoding="utf-8")
        with self.assertRaisesRegex(G4Error, "gatewayBuild"):
            wrong_identity.compose()

        wrong_bundle = _Composer(_close())
        bundle = wrong_bundle.summary().parent / "runtime.sha256"
        bundle.write_text(f"{'c' * 64}  runtime-bundle.zip\n", encoding="utf-8")
        with self.assertRaisesRegex(G4Error, "the artifact deployed"):
            wrong_bundle.compose()

        red_rest = _Composer(_close())
        observations = json.loads(red_rest.observations().read_text(encoding="utf-8"))
        observations["cases"][0]["ok"] = False
        red_rest.observations().write_text(json.dumps(observations), encoding="utf-8")
        with self.assertRaisesRegex(G4Error, "REST case"):
            red_rest.compose()

        drifted_inventory = copy.deepcopy(composer.close)
        drifted_inventory["inventories"]["runtime"]["operator"]["tools"] = ["tag_write"]
        with self.assertRaisesRegex(G4Error, "lint.py"):
            composer.compose(drifted_inventory)

        wrong_bundle_sha = copy.deepcopy(composer.close)
        wrong_bundle_sha["bundleSha256"] = "c" * 64
        with self.assertRaisesRegex(G4Error, "no cited run deployed the declared bundle"):
            composer.compose(wrong_bundle_sha)

        wrong_result = copy.deepcopy(composer.close)
        wrong_result["gateResult"] = "VERIFIED"
        with self.assertRaisesRegex(G4Error, "gateResult"):
            composer.compose(wrong_result)

        missing_artifact = copy.deepcopy(composer.close)
        missing_artifact["runs"][0]["runId"] = "99999999999"
        with self.assertRaisesRegex(G4Error, "only be cited when its own artifact is available"):
            composer.compose(missing_artifact)

        no_run = copy.deepcopy(composer.close)
        no_run["runs"] = []
        with self.assertRaisesRegex(G4Error, "runs must be a non-empty list"):
            composer.compose(no_run)

    def test_a_case_with_no_evidence_on_either_plane_is_unverified(self) -> None:
        close = _close()
        for plane in ("rest", "runtime"):
            close["l5"]["cancellation"][plane] = {"verdict": "NONE", "source": "none",
                                                  "limitation": "nothing"}
        close["gateResult"] = "UNVERIFIED_LIMITATION"
        close["unsatisfiedAcceptance"] = [
            "cancellation on the rest plane", "cancellation on the runtime plane",
        ]
        composer = _Composer(close)
        row = json.loads((composer.compose() / "evidence.json").read_text(encoding="utf-8"))
        self.assertEqual(row["gateResult"], "UNVERIFIED_LIMITATION")
        self.assertEqual(sorted(row["unsatisfiedAcceptance"]),
                         ["cancellation on the rest plane", "cancellation on the runtime plane"])

        # One Plane is enough for the Gate: the REST-plane live proof covers the
        # D26 case, and the Runtime gap stays a recorded limitation.
        one_plane = _close()
        one_plane["l5"]["cancellation"]["runtime"] = {"verdict": "NONE", "source": "none",
                                                     "limitation": "nothing"}
        one_plane["unsatisfiedAcceptance"] = ["cancellation on the runtime plane"]
        row = json.loads((_Composer(one_plane).compose() / "evidence.json").read_text(encoding="utf-8"))
        self.assertEqual(row["gateResult"], "VERIFIED_WITH_LIMITATION")

    def test_generate_refuses_to_overwrite_a_committed_row(self) -> None:
        composer = _Composer(_close())
        directory = composer.compose()
        self.assertTrue((directory / "evidence.json").is_file())
        with self.assertRaisesRegex(G4Error, "refusing to overwrite"):
            generate_g4(composer.temp / "close.json", directory.parent, composer.artifacts)

    def test_close_document_cannot_claim_supported(self) -> None:
        close = _close()
        close["compatibilityStatus"] = "SUPPORTED"
        composer = _Composer(close)
        directory = composer.compose()
        self.assertNotIn("SUPPORTED", (directory / "evidence.json").read_text(encoding="utf-8"))

    def test_validator_refuses_a_drifted_inventory(self) -> None:
        base = json.loads((REPO_EVIDENCE / ROW_838 / "evidence.json").read_text(encoding="utf-8"))
        drifted = copy.deepcopy(base)
        drifted["inventories"]["runtime"]["readonly"]["tools"] = ["tag_write"]
        temp = Path(tempfile.mkdtemp())
        _write(temp / ROW_838 / "evidence.json", drifted)
        with self.assertRaisesRegex(EvidenceError, "does not match tooling/contracts/lint.py"):
            load_evidence(temp)

    def test_validator_refuses_an_unproven_case_that_is_not_declared(self) -> None:
        base = json.loads((REPO_EVIDENCE / ROW_838 / "evidence.json").read_text(encoding="utf-8"))

        undeclared = copy.deepcopy(base)
        undeclared["unsatisfiedAcceptance"] = []
        temp = Path(tempfile.mkdtemp())
        _write(temp / ROW_838 / "evidence.json", undeclared)
        with self.assertRaisesRegex(EvidenceError, "must be named in unsatisfiedAcceptance"):
            load_evidence(temp)

        overclaimed = copy.deepcopy(base)
        overclaimed["unsatisfiedAcceptance"] = [
            *overclaimed["unsatisfiedAcceptance"], "timeout on the runtime plane",
        ]
        temp = Path(tempfile.mkdtemp())
        _write(temp / ROW_838 / "evidence.json", overclaimed)
        with self.assertRaisesRegex(EvidenceError, "has evidence but is declared unsatisfied"):
            load_evidence(temp)

        verified = copy.deepcopy(base)
        verified["gateResult"] = "VERIFIED"
        temp = Path(tempfile.mkdtemp())
        _write(temp / ROW_838 / "evidence.json", verified)
        with self.assertRaisesRegex(EvidenceError, "record the limitation"):
            load_evidence(temp)


if __name__ == "__main__":
    unittest.main()
