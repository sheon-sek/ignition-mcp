"""Slice 11 (Phase 3 / G3): evidence generator.

The generator must (a) refuse inconsistent or failed driver observations,
(b) map the D27 exact tuple onto the documented VERIFIED_WITH_LIMITATION
limitation and never inherit it, (c) always emit a row that the slice-9
validator accepts, and (d) never overwrite existing evidence.
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
from typing import Any
import unittest

from tooling.compat.evidence import load_evidence
from tooling.compat.generate import GenerateError, build_row, generate

D27_SHA = "b1142a5796f2fd834555f13f03de706599d745f7172a68e54f2f7908b67fe365"


def _observations(**overrides: object) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schemaVersion": 1,
        "gate": "G3",
        "fatal": None,
        "checks": [{"name": "txn-committed", "status": "PASS"}],
        "openapiSha256": "c" * 64,
        "runtimeOutputSchemaPublished": False,
        "runtimeNativeBindingAccepted": True,
        "fingerprintStability": "STABLE",
        "projectMutationConcurrencySafe": True,
        "runtime": {"outputSchemaPublished": False, "bundleInfo": {"bundleVersion": "0.2.0"}},
        "setupNative": {"doctor": {"exitCode": 0}},
        "restPlane": {"artifactId": "0198abcd"},
        "authzDenials": {"denials": {"read-only-jwt": {"code": "permission_denied"}}},
        "transactions": {
            "NO_CHANGE": {"state": "NO_CHANGE"},
            "COMMITTED": {"state": "COMMITTED"},
            "CONFLICTED": {"state": "CONFLICTED"},
        },
    }
    base.update(overrides)
    return base


def _identity(**overrides: object) -> dict[str, Any]:
    base: dict[str, Any] = {
        "gatewayVersion": "8.3.8",
        "gatewayBuild": "2026071409",
        "gatewayImage": "inductiveautomation/ignition:8.3.8",
        "gatewayImageDigest": "sha256:" + "d" * 64,
        "mcpModuleVersion": "1.3.5-SNAPSHOT",
        "mcpModuleArtifactVersion": "1.3.5.2026021307-SNAPSHOT",
        "mcpModuleBuild": "2026021307",
        "mcpModuleSha256": D27_SHA,
        "bundleVersion": "0.2.0",
        "bundleSha256": "e" * 64,
        "deployedBundleSha256": "e" * 64,
        "sourceRevision": "f" * 40,
        "runId": "12345",
    }
    base.update(overrides)
    return base


def _write(root: Path, name: str, value: object) -> Path:
    path = root / name
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


class GenerateTest(unittest.TestCase):
    def test_d27_row_maps_the_documented_limitation_and_validates(self) -> None:
        row = build_row(_observations(), _identity())
        self.assertEqual(row["nativeResponseBinding"], "VERIFIED_WITH_LIMITATION")
        self.assertTrue(row["d27ExceptionApplied"])
        self.assertEqual(row["status"], "VERIFIED")
        self.assertEqual(row["compatibilityStatus"], "UNTESTED")
        self.assertIn("phase3-live-environment-protection", row["ownerAcceptedDeviations"])
        self.assertEqual(row["deployedBundleSha256"], "e" * 64)

        temp = Path(tempfile.mkdtemp())
        directory = generate(_write(temp, "obs.json", _observations()), temp, _write(temp, "id.json", _identity()))
        self.assertEqual(directory.name, "g3-8.3.8-mcp-2026021307")
        rows = load_evidence(temp)
        self.assertEqual([r.gate for r in rows], ["G3"])
        self.assertTrue(rows[0].is_d27_tuple)

    def test_a_run_without_the_setup_section_still_composes_and_validates(self) -> None:
        """P7-6 deleted the setup stage, so a new run observes no ``setupNative`` section."""

        observations = _observations()
        del observations["setupNative"]
        row = build_row(observations, _identity())
        self.assertIsNone(row["setupNative"])
        with self.assertRaisesRegex(GenerateError, "authzDenials"):
            build_row({key: value for key, value in observations.items() if key != "authzDenials"}, _identity())

        temp = Path(tempfile.mkdtemp())
        generate(_write(temp, "obs.json", observations), temp, _write(temp, "id.json", _identity()))
        rows = load_evidence(temp)
        self.assertEqual(rows[0].raw["setupNative"], None)

    def test_non_d27_tuple_never_inherits_the_exception(self) -> None:
        row = build_row(_observations(), _identity(gatewayVersion="8.3.9", gatewayBuild="2026082511"))
        self.assertEqual(row["nativeResponseBinding"], "UNVERIFIED_LIMITATION")
        self.assertFalse(row["d27ExceptionApplied"])
        self.assertEqual(row["status"], "FAILED_NATIVE_BINDING")

    def test_failed_or_fatal_observations_are_refused(self) -> None:
        with self.assertRaisesRegex(GenerateError, "failed check"):
            build_row(_observations(checks=[{"name": "boom", "status": "FAIL"}]), _identity())
        with self.assertRaisesRegex(GenerateError, "fatal"):
            build_row(_observations(fatal="ProbeError: stage failed"), _identity())

    def test_dishonest_stability_flag_is_refused(self) -> None:
        with self.assertRaisesRegex(GenerateError, "concurrency-safe"):
            build_row(_observations(fingerprintStability="UNSTABLE", projectMutationConcurrencySafe=True),
                      _identity())

    def test_deployment_hash_mismatch_is_refused(self) -> None:
        with self.assertRaisesRegex(GenerateError, "deployed bundle hash"):
            build_row(_observations(), _identity(deployedBundleSha256="a" * 64))

    def test_missing_transaction_branches_are_refused(self) -> None:
        observations = _observations()
        del observations["transactions"]["COMMITTED"]
        with self.assertRaisesRegex(GenerateError, "COMMITTED"):
            build_row(observations, _identity())

    def test_existing_directory_is_never_overwritten(self) -> None:
        temp = Path(tempfile.mkdtemp())
        generate(_write(temp, "obs.json", _observations()), temp, _write(temp, "id.json", _identity()))
        with self.assertRaisesRegex(GenerateError, "refusing to overwrite"):
            generate(_write(temp, "obs2.json", _observations()), temp, _write(temp, "id2.json", _identity()))

    def test_gate_off_report_is_embedded_verbatim(self) -> None:
        temp = Path(tempfile.mkdtemp())
        report = {"passed": True, "tools": ["gateway_info"]}
        directory = generate(
            _write(temp, "obs.json", _observations()), temp, _write(temp, "id.json", _identity()),
            gate_off_report=_write(temp, "gateoff.json", report),
        )
        row = json.loads((directory / "evidence.json").read_text(encoding="utf-8"))
        self.assertEqual(row["gateOffInventory"], report)


if __name__ == "__main__":
    unittest.main()
