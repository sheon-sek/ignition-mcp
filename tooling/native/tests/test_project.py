from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from tooling.native.archive import build_project
from tooling.native.project import validate_project

PROJECT = Path(__file__).resolve().parents[3] / "packages/ignition-runtime-bundle/project"


class RuntimeProjectTest(unittest.TestCase):
    def test_phase0_project_is_valid_enabled_and_standalone(self) -> None:
        files = validate_project(PROJECT)
        self.assertIn("project.json", files)
        self.assertIn(b'"enabled": true', files["project.json"])
        self.assertIn(b'"inheritable": false', files["project.json"])

    def test_schema_text_resource_matches_source_schema_exactly(self) -> None:
        root = Path(__file__).resolve().parents[3]
        source = root / "contracts/schemas/bundle-info.output.schema.json"
        published = PROJECT / "com.inductiveautomation.mcp/resources/contracts/bundle-info-output/data.bin"
        self.assertEqual(source.read_bytes(), published.read_bytes())

    def test_production_build_is_deterministic_after_d27_binding_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            first = Path(temporary) / "first.zip"
            second = Path(temporary) / "second.zip"
            build_project(PROJECT, first)
            build_project(PROJECT, second)
            self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_runtime_handler_has_no_pending_binding_marker(self) -> None:
        files = validate_project(PROJECT)
        handler = files["com.inductiveautomation.mcp/tools/bundle-info/onToolCalled.py"].decode("utf-8")
        self.assertNotIn("NATIVE_BINDING_PENDING", handler)


if __name__ == "__main__":
    unittest.main()
