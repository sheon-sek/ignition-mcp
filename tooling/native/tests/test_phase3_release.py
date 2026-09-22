"""Slice 9 (Phase 3 / G3): deterministic Runtime Bundle release artifacts,
single identity source and build-time revision stamping (D21/D23)."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest
import zipfile

from tooling.native.archive import build_project, stamp_source_revision
from tooling.native.project import validate_project
from tooling.native.release import release, validate_manifest
from tooling.native.validation import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[3]
PROJECT = REPO_ROOT / "packages/ignition-runtime-bundle/project"
EVIDENCE = REPO_ROOT / "tests/compatibility/evidence"
HANDLER_MEMBER = "com.inductiveautomation.mcp/tools/bundle_info/onToolCalled.py"
#: D21 makes `BUNDLE_VERSION` the single identity source, so the tests read it
#: rather than pinning a version: a D21 MINOR bump per released milestone would
#: otherwise churn every assertion here.
BUNDLE_VERSION = (PROJECT.parent / "BUNDLE_VERSION").read_text(encoding="utf-8").strip()
SHA_A = "a" * 40
SHA_B = "b" * 40


def _copy_project(target_parent: Path) -> Path:
    target_parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(PROJECT.parent / "BUNDLE_VERSION", target_parent / "BUNDLE_VERSION")
    shutil.copy(PROJECT.parent / "RESOURCE_SCHEMA_VERSION", target_parent / "RESOURCE_SCHEMA_VERSION")
    project = target_parent / "project"
    shutil.copytree(PROJECT, project)
    return project


class ReleaseTest(unittest.TestCase):
    def test_release_is_byte_reproducible(self) -> None:
        out_a = Path(tempfile.mkdtemp()) / "a"
        out_b = Path(tempfile.mkdtemp()) / "b"
        paths_a = release(project_dir=PROJECT, out_dir=out_a, source_revision=SHA_A,
                          evidence_dir=EVIDENCE, repo_root=REPO_ROOT)
        paths_b = release(project_dir=PROJECT, out_dir=out_b, source_revision=SHA_A,
                          evidence_dir=EVIDENCE, repo_root=REPO_ROOT)
        for key in ("zip", "manifest", "sha256"):
            self.assertEqual(paths_a[key].read_bytes(), paths_b[key].read_bytes(), key)

    def test_release_names_hashes_and_stamp(self) -> None:
        out = Path(tempfile.mkdtemp())
        paths = release(project_dir=PROJECT, out_dir=out, source_revision=SHA_B,
                        evidence_dir=EVIDENCE, repo_root=REPO_ROOT)
        self.assertEqual(paths["zip"].name, f"ignition-runtime-bundle-{BUNDLE_VERSION}.zip")
        digest = paths["sha256"].read_text(encoding="ascii")
        import hashlib

        expected = hashlib.sha256(paths["zip"].read_bytes()).hexdigest()
        self.assertEqual(digest, f"{expected}  ignition-runtime-bundle-{BUNDLE_VERSION}.zip\n")
        manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
        self.assertEqual(manifest["sourceRevision"], SHA_B)
        self.assertEqual(manifest["artifact"]["sha256"], expected)
        self.assertEqual(manifest["bundleVersion"], BUNDLE_VERSION)
        self.assertEqual(manifest["resourceSchemaVersion"], 1)
        self.assertEqual(manifest["nativeResponseBindingStatus"], "VERIFIED_WITH_LIMITATION")
        # The committed evidence rows certify an earlier bundle, so a Phase 4
        # release carries no tuple until Phase 4 produces its own rows (D21 exact
        # tuples).
        self.assertEqual(manifest["testedTuples"], [])
        handler = zipfile.ZipFile(paths["zip"]).read(HANDLER_MEMBER).decode("utf-8")
        self.assertIn(SHA_B, handler)
        self.assertNotIn("__BUNDLE_SOURCE_REVISION__", handler)
        rendered = paths["manifest"].read_bytes().decode("utf-8")
        self.assertTrue(rendered.endswith("}\n"))
        self.assertEqual(rendered, json.dumps(json.loads(rendered), indent=2, sort_keys=True,
                                             ensure_ascii=False) + "\n")

    def test_build_defaults_to_unstamped_and_direct_lib_stamps(self) -> None:
        out = Path(tempfile.mkdtemp())
        files = build_project(PROJECT, out / "runtime.zip")
        self.assertEqual(files[
            "com.inductiveautomation.mcp/tools/bundle_info/onToolCalled.py"
        ].count(b"UNSTAMPED"), 1)
        files2 = build_project(PROJECT, out / "runtime2.zip", source_revision=SHA_A)
        self.assertIn(SHA_A.encode(), files2[
            "com.inductiveautomation.mcp/tools/bundle_info/onToolCalled.py"
        ])
        with self.assertRaises(ValidationError):
            stamp_source_revision(validate_project(PROJECT), "nope")

    def test_manifest_rejects_supported_tuples_and_key_drift(self) -> None:
        out = Path(tempfile.mkdtemp())
        paths = release(project_dir=PROJECT, out_dir=out, source_revision=SHA_A,
                        evidence_dir=EVIDENCE, repo_root=REPO_ROOT)
        manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
        manifest["testedTuples"] = [{
            "gate": "G3", "gatewayVersion": "8.3.8", "gatewayBuild": "2026071409",
            "mcpModuleVersion": "1.3.5-SNAPSHOT", "mcpModuleBuild": "2026021307",
            "mcpModuleSha256": "b" * 64, "bundleVersion": "0.3.0",
            "compatibilityStatus": "SUPPORTED", "nativeResponseBinding": "VERIFIED",
        }]
        with self.assertRaises(ValidationError):
            validate_manifest(manifest)
        clean = json.loads(paths["manifest"].read_text(encoding="utf-8"))
        clean["unexpectedKey"] = 1
        with self.assertRaises(ValidationError):
            validate_manifest(clean)

    def test_single_identity_source_enforced_by_validator(self) -> None:
        parent = Path(tempfile.mkdtemp()) / "bundle"
        project = _copy_project(parent)
        (parent / "BUNDLE_VERSION").write_text("9.9.9\n", encoding="utf-8")
        with self.assertRaises(ValidationError) as caught:
            validate_project(project)
        self.assertIn("BUNDLE_VERSION", str(caught.exception))

    def test_marker_must_match_bundle_version(self) -> None:
        parent = Path(tempfile.mkdtemp()) / "bundle"
        project = _copy_project(parent)
        manifest_path = project / "project.json"
        doc = json.loads(manifest_path.read_text(encoding="utf-8"))
        doc["description"] = doc["description"].replace(f"bundle={BUNDLE_VERSION}", "bundle=0.1.0")
        manifest_path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        with self.assertRaises(ValidationError) as caught:
            validate_project(project)
        self.assertIn("marker", str(caught.exception))

    def test_missing_marker_is_rejected(self) -> None:
        parent = Path(tempfile.mkdtemp()) / "bundle"
        project = _copy_project(parent)
        manifest_path = project / "project.json"
        doc = json.loads(manifest_path.read_text(encoding="utf-8"))
        doc["description"] = "no marker here"
        manifest_path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        with self.assertRaises(ValidationError):
            validate_project(project)

    def test_token_must_appear_exactly_once_in_source(self) -> None:
        parent = Path(tempfile.mkdtemp()) / "bundle"
        project = _copy_project(parent)
        handler = project / "com.inductiveautomation.mcp/tools/bundle_info/onToolCalled.py"
        text = handler.read_text(encoding="utf-8")
        handler.write_text(
            text.replace('bundleSourceRevision = "__BUNDLE_SOURCE_REVISION__"',
                         'bundleSourceRevision = "__BUNDLE_SOURCE_REVISION__"\n'
                         'other = "__BUNDLE_SOURCE_REVISION__"', ),
            encoding="utf-8",
        )
        with self.assertRaises(ValidationError):
            validate_project(project)

    def test_validator_accepts_the_stamped_tree(self) -> None:
        out = Path(tempfile.mkdtemp()) / "unpacked"
        files = build_project(PROJECT, out / "runtime.zip", source_revision=SHA_A)
        unpacked = out / "tree"
        with zipfile.ZipFile(out / "runtime.zip") as archive:
            archive.extractall(unpacked)
        # the stamped project.json/BUNDLE_VERSION siblings must exist for validation
        shutil.copy(PROJECT.parent / "BUNDLE_VERSION", unpacked.parent / "BUNDLE_VERSION")
        validate_project(unpacked)
        self.assertEqual(len(files), 45)


if __name__ == "__main__":
    unittest.main()
