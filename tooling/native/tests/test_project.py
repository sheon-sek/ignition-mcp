from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from tooling.native.archive import build_project
from tooling.native.project import ValidationError, validate_project

PROJECT = Path(__file__).resolve().parents[3] / "packages/ignition-runtime-bundle/project"


class RuntimeProjectTest(unittest.TestCase):
    def test_phase0_project_is_valid_and_disabled(self) -> None:
        files = validate_project(PROJECT)
        self.assertIn("project.json", files)
        self.assertIn(b'"enabled": false', files["project.json"])

    def test_schema_text_resource_matches_source_schema_exactly(self) -> None:
        root = Path(__file__).resolve().parents[3]
        source = root / "contracts/schemas/bundle-info.output.schema.json"
        published = PROJECT / "com.inductiveautomation.mcp/resources/contracts/bundle-info-output/data.bin"
        self.assertEqual(source.read_bytes(), published.read_bytes())

    def test_production_build_fails_closed_while_binding_pending(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValidationError, "binding pending"):
                build_project(PROJECT, Path(temporary) / "bundle.zip")

    def test_explicit_disabled_scaffold_build_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            first = Path(temporary) / "first.zip"
            second = Path(temporary) / "second.zip"
            build_project(PROJECT, first, allow_unbound_scaffold=True)
            build_project(PROJECT, second, allow_unbound_scaffold=True)
            self.assertEqual(first.read_bytes(), second.read_bytes())


if __name__ == "__main__":
    unittest.main()
