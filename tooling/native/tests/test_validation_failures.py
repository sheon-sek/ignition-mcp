from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest

from tooling.native.project import ValidationError, validate_project

PROJECT = Path(__file__).resolve().parents[3] / "packages/ignition-runtime-bundle/project"


class ValidationFailureTest(unittest.TestCase):
    def _copy(self, temporary: str) -> Path:
        target = Path(temporary) / "project"
        shutil.copytree(PROJECT, target)
        return target

    def test_rejects_extra_parameter_schema_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = self._copy(temporary)
            resource = project / "com.inductiveautomation.mcp/tools/bundle_info/resource.json"
            data = json.loads(resource.read_text(encoding="utf-8"))
            data["attributes"]["parameters"] = [
                {
                    "name": "x",
                    "description": "x",
                    "type": "string",
                    "required": False,
                    "default": "",
                    "unexpected": True
                }
            ]
            resource.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(ValidationError, "parameter keys"):
                validate_project(project)

    def test_rejects_code_before_handler(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = self._copy(temporary)
            handler = project / "com.inductiveautomation.mcp/tools/bundle_info/onToolCalled.py"
            handler.write_text("# comment\n" + handler.read_text(encoding="utf-8"), encoding="utf-8")
            with self.assertRaisesRegex(ValidationError, "first line"):
                validate_project(project)

    def test_rejects_space_indentation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = self._copy(temporary)
            handler = project / "com.inductiveautomation.mcp/tools/bundle_info/onToolCalled.py"
            handler.write_text('def onToolCalled(builder):\n    return builder.text("x")\n', encoding="utf-8")
            with self.assertRaisesRegex(ValidationError, "Tabs only"):
                validate_project(project)


    def test_rejects_crlf_data_bin_with_line_ending_message(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = self._copy(temporary)
            relative = "com.inductiveautomation.mcp/resources/contracts/bundle-info-output/data.bin"
            payload = project / relative
            payload.write_bytes(payload.read_bytes().replace(b"\n", b"\r\n"))
            with self.assertRaises(ValidationError) as caught:
                validate_project(project)
            message = str(caught.exception)
            self.assertTrue(message.startswith(relative + ": must use LF line endings"), message)
            self.assertIn("D31 section 5", message)
            self.assertNotIn("size must match", message)


if __name__ == "__main__":
    unittest.main()
