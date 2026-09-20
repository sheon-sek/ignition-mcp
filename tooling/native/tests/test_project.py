from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from tooling.native.archive import build_project
from tooling.native.project import validate_project
from tooling.native.validation import ValidationError

ROOT = Path(__file__).resolve().parents[3]
PROJECT = ROOT / "packages/ignition-runtime-bundle/project"


class RuntimeProjectTest(unittest.TestCase):
    def test_phase2_project_is_valid_enabled_and_standalone(self) -> None:
        files = validate_project(PROJECT)
        self.assertIn("project.json", files)
        self.assertIn(b'"enabled": true', files["project.json"])
        self.assertIn(b'"inheritable": false', files["project.json"])

    def test_schema_text_resources_match_source_schemas_exactly(self) -> None:
        pairs = (
            ("bundle-info.output.schema.json", "bundle-info-output"),
            ("tag-browse.output.schema.json", "tag-browse-output"),
            ("tag-read.output.schema.json", "tag-read-output"),
        )
        for schema_name, resource_name in pairs:
            source = ROOT / "contracts/schemas" / schema_name
            published = PROJECT / "com.inductiveautomation.mcp/resources/contracts" / resource_name / "data.bin"
            self.assertEqual(source.read_bytes(), published.read_bytes())

    def test_production_build_is_deterministic_after_d27_binding_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            first = Path(temporary) / "first.zip"
            second = Path(temporary) / "second.zip"
            build_project(PROJECT, first)
            build_project(PROJECT, second)
            self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_runtime_handlers_have_no_pending_binding_marker(self) -> None:
        files = validate_project(PROJECT)
        handlers = [value for name, value in files.items() if name.endswith("/onToolCalled.py")]
        self.assertEqual(len(handlers), 13)
        for handler in handlers:
            self.assertNotIn(b"NATIVE_BINDING_PENDING", handler)

    def test_phase2_current_runtime_tool_inventory_is_exact(self) -> None:
        files = validate_project(PROJECT)
        identifiers = []
        titles = []
        prefix = "com.inductiveautomation.mcp/tools/"
        for path, payload in files.items():
            if path.startswith(prefix) and path.endswith("/resource.json"):
                relative = path[len(prefix):]
                identifiers.append(relative.rsplit("/", 1)[0])
                resource = json.loads(payload)
                titles.append(resource["attributes"]["title"])
        expected = [
        "alarm_journal",
        "alarm_shelved_list",
        "alarm_status",
        "bundle_info",
        "historian_browse",
        "historian_query_aggregate",
        "historian_query_series",
        "tag_browse",
        "tag_get_config",
        "tag_query",
        "tag_read",
        "udt_type_get",
        "udt_type_list"
]
        self.assertEqual(sorted(identifiers), expected)
        self.assertEqual(sorted(titles), expected)

    def test_parameter_default_is_allowed_but_unknown_key_is_rejected(self) -> None:
        source = ROOT / "packages/ignition-runtime-bundle/project/com.inductiveautomation.mcp/tools/tag_browse/resource.json"
        payload = json.loads(source.read_text(encoding="utf-8"))
        self.assertEqual(payload["attributes"]["parameters"][1]["default"], False)
        payload["attributes"]["parameters"][1]["unexpected"] = True

        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            for path in PROJECT.rglob("*"):
                relative = path.relative_to(PROJECT)
                target = project / relative
                if path.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(path.read_bytes())
            target = project / "com.inductiveautomation.mcp/tools/tag_browse/resource.json"
            target.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ValidationError):
                validate_project(project)


if __name__ == "__main__":
    unittest.main()
