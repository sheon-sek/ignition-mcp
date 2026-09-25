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


    def test_reads_a_crlf_checkout_as_lf_and_builds_the_same_zip(self) -> None:
        from tooling.native.archive import build_project

        with tempfile.TemporaryDirectory() as temporary:
            lf = self._copy(temporary)
            shutil.copy(PROJECT.parent / "BUNDLE_VERSION", Path(temporary) / "BUNDLE_VERSION")
            crlf = Path(temporary) / "crlf"
            shutil.copytree(lf, crlf)
            for path in crlf.rglob("*"):
                if path.is_file():
                    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
            validate_project(crlf)
            build_project(lf, Path(temporary) / "lf.zip")
            build_project(crlf, Path(temporary) / "crlf.zip")
            self.assertEqual((Path(temporary) / "lf.zip").read_bytes(), (Path(temporary) / "crlf.zip").read_bytes())

    def test_rejects_a_lone_carriage_return(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = self._copy(temporary)
            relative = "com.inductiveautomation.mcp/resources/contracts/bundle-info-output/data.bin"
            payload = project / relative
            payload.write_bytes(payload.read_bytes().replace(b"\n", b"\r", 1))
            with self.assertRaises(ValidationError) as caught:
                validate_project(project)
            self.assertTrue(str(caught.exception).startswith(relative + ": contains a carriage return"))

if __name__ == "__main__":
    unittest.main()
