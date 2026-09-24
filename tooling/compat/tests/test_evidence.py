"""Slice 9 (Phase 3 / G3): compatibility evidence validator.

Frozen G0-G2 evidence must pass read-only in its documented legacy forms. The
committed G3 rows must also pass the Phase 3 rules for D27 exact-tuple handling,
fingerprint stability, and deployment provenance.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from tooling.compat.evidence import EvidenceError, load_evidence
from tooling.compat.evidence import tested_tuples_for as bundle_tuples

REPO_EVIDENCE = Path(__file__).resolve().parents[3] / "tests/compatibility/evidence"
D27_DIR = "g2-8.3.8-mcp-2026021307"
NON_D27_DIR = "g2-8.3.9-mcp-2026021307"
G3_D27_DIR = "g3-8.3.8-mcp-2026021307"
G3_NON_D27_DIR = "g3-8.3.9-mcp-2026021307"
G4_DIRS = ("g4-8.3.8-mcp-2026021307", "g4-8.3.9-mcp-2026021307")
G6_DIRS = ("g6-8.3.8-mcp-2026021307", "g6-8.3.9-mcp-2026021307")
G7_DIRS = ("g7-8.3.8-mcp-2026021307", "g7-8.3.9-mcp-2026021307")


def _rows_with(gate_dir: str, mutate) -> list:  # type: ignore[no-untyped-def]
    temp = Path(tempfile.mkdtemp())
    shutil.copytree(REPO_EVIDENCE / gate_dir, temp / gate_dir)
    manifest = temp / gate_dir / "evidence.json"
    doc = json.loads(manifest.read_text(encoding="utf-8"))
    mutate(doc)
    manifest.write_text(json.dumps(doc), encoding="utf-8")
    return load_evidence(temp)


class EvidenceTest(unittest.TestCase):
    def test_repository_evidence_passes_readonly(self) -> None:
        rows = load_evidence(REPO_EVIDENCE)
        # G0 (1 row), G1 (1), and two Gateway tuples per gate from G2 on: 14 rows now
        # that the G7 rows (issue #78) are committed.
        self.assertEqual({row.gate for row in rows}, {"G0", "G1", "G2", "G3", "G4", "G5", "G6", "G7"})
        self.assertEqual(len(rows), 14)
        for gate, directories in (("G6", G6_DIRS), ("G7", G7_DIRS)):
            for directory in directories:
                row = next(row for row in rows if row.directory == directory)
                self.assertEqual(row.gate, gate)
                self.assertEqual(row.compatibility_status, "UNTESTED")
                self.assertEqual(row.d27_exception_applied, row.is_d27_tuple)
        d27 = next(row for row in rows if row.directory == D27_DIR)
        self.assertTrue(d27.is_d27_tuple and d27.d27_exception_applied)
        g3_d27 = next(row for row in rows if row.directory == G3_D27_DIR)
        self.assertTrue(g3_d27.is_d27_tuple and g3_d27.d27_exception_applied)
        g3_candidate = next(row for row in rows if row.directory == G3_NON_D27_DIR)
        self.assertFalse(g3_candidate.d27_exception_applied)
        self.assertEqual(g3_candidate.native_response_binding, "UNVERIFIED_LIMITATION")
        for directory in G4_DIRS:
            g4 = next(row for row in rows if row.directory == directory)
            self.assertEqual(g4.gate, "G4")
            self.assertEqual(g4.compatibility_status, "UNTESTED")
            self.assertEqual(g4.d27_exception_applied, g4.is_d27_tuple)
        legacy_g0 = next(row for row in rows if row.gate == "G0")
        self.assertTrue(legacy_g0.d27_exception_applied)  # d27OutputSchemaExceptionApplied alias
        legacy_g1 = next(row for row in rows if row.gate == "G1")
        self.assertTrue(legacy_g1.d27_exception_applied)  # documented inference for schemaVersion 2

    def test_supported_claim_is_rejected(self) -> None:
        with self.assertRaisesRegex(EvidenceError, "SUPPORTED"):
            _rows_with(D27_DIR, lambda doc: doc.__setitem__("compatibilityStatus", "SUPPORTED"))

    def test_d27_exception_cannot_inherit_to_another_tuple(self) -> None:
        with self.assertRaisesRegex(EvidenceError, "not the exact characterized D27 identity"):
            _rows_with(NON_D27_DIR, lambda doc: doc.__setitem__("d27ExceptionApplied", True))

    def test_verified_with_limitation_requires_the_flag(self) -> None:
        with self.assertRaisesRegex(EvidenceError, "without the d27 exception flag"):
            _rows_with(D27_DIR, lambda doc: doc.__setitem__("d27ExceptionApplied", False))

    def test_missing_output_schema_must_be_honest(self) -> None:
        def dishonest(doc: dict) -> None:
            doc["nativeResponseBinding"] = "VERIFIED"
            doc["runtime"]["nativeResponseBinding"] = "VERIFIED"

        with self.assertRaisesRegex(EvidenceError, "UNVERIFIED_LIMITATION or FAILED_NATIVE_BINDING"):
            _rows_with(NON_D27_DIR, dishonest)

    def test_tuple_format_rules(self) -> None:
        with self.assertRaisesRegex(EvidenceError, "mcpModuleBuild"):
            _rows_with(NON_D27_DIR, lambda doc: doc.__setitem__("mcpModuleBuild", "99"))
        with self.assertRaisesRegex(EvidenceError, "mcpModuleSha256"):
            _rows_with(NON_D27_DIR, lambda doc: doc.__setitem__("mcpModuleSha256", "zz"))
        with self.assertRaisesRegex(EvidenceError, "unknown gate"):
            _rows_with(NON_D27_DIR, lambda doc: doc.__setitem__("gate", "G9"))

    def test_g3_row_rules(self) -> None:
        base = json.loads((REPO_EVIDENCE / D27_DIR / "evidence.json").read_text(encoding="utf-8"))
        row = copy.deepcopy(base)
        row.update({
            "gate": "G3", "bundleVersion": "0.2.0",
            "ownerAcceptedDeviations": ["phase3-live-environment-protection"],
            "fingerprintStability": "STABLE", "projectMutationConcurrencySafe": True,
            "deployedBundleSha256": "a" * 64,
        })

        def make(temp: Path, doc: dict) -> Path:
            directory = temp / "g3-8.3.8-mcp-2026021307"
            directory.mkdir(parents=True)
            (directory / "evidence.json").write_text(json.dumps(doc), encoding="utf-8")
            return temp

        temp = Path(tempfile.mkdtemp())
        make(temp, row)
        rows = load_evidence(temp)
        self.assertEqual(rows[0].gate, "G3")

        missing = copy.deepcopy(row)
        missing.pop("ownerAcceptedDeviations")
        temp = Path(tempfile.mkdtemp())
        make(temp, missing)
        with self.assertRaisesRegex(EvidenceError, "deviation"):
            load_evidence(temp)

        unstable = copy.deepcopy(row)
        unstable.update({"fingerprintStability": "UNSTABLE"})
        temp = Path(tempfile.mkdtemp())
        make(temp, unstable)
        with self.assertRaisesRegex(EvidenceError, "not concurrency-safe"):
            load_evidence(temp)

        no_hash = copy.deepcopy(row)
        no_hash["deployedBundleSha256"] = "short"
        temp = Path(tempfile.mkdtemp())
        make(temp, no_hash)
        with self.assertRaisesRegex(EvidenceError, "deployed release ZIP"):
            load_evidence(temp)

    def test_tested_tuples_filter_exact_bundle_and_order(self) -> None:
        rows = load_evidence(REPO_EVIDENCE)
        tuples_020 = bundle_tuples(rows, "0.2.0")
        self.assertEqual(
            [
                (item["gate"], item["gatewayVersion"], item["nativeResponseBinding"])
                for item in tuples_020
            ],
            [
                ("G3", "8.3.8", "VERIFIED_WITH_LIMITATION"),
                ("G3", "8.3.9", "UNVERIFIED_LIMITATION"),
            ],
        )
        tuples_010 = bundle_tuples(rows, "0.1.0")
        self.assertEqual({item["gate"] for item in tuples_010}, {"G1", "G2"})
        self.assertEqual(tuples_010, sorted(
            tuples_010, key=lambda i: (i["gate"], i["gatewayVersion"], i["mcpModuleBuild"])
        ))


if __name__ == "__main__":
    unittest.main()
