from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from tooling.native.sync_schemas import SCHEMA_NAMES, main, sync_schemas
from tooling.native.validation import ValidationError

ROOT = Path(__file__).resolve().parents[3]
RESOURCES = "packages/ignition-runtime-bundle/project/com.inductiveautomation.mcp/resources/contracts"


class SyncSchemasTest(unittest.TestCase):
    def _root(self, temporary: str) -> Path:
        root = Path(temporary)
        for name in SCHEMA_NAMES:
            source = root / f"contracts/schemas/{name}.output.schema.json"
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes((ROOT / f"contracts/schemas/{name}.output.schema.json").read_bytes())
            target = root / RESOURCES / f"{name}-output"
            target.mkdir(parents=True)
            (target / "resource.json").write_text(
                json.dumps({"attributes": {"size": -1}}), encoding="utf-8"
            )
        return root

    def test_rejects_crlf_schema_before_writing_any_resource(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(temporary)
            crlf = root / "contracts/schemas/tag-browse.output.schema.json"
            crlf.write_bytes(crlf.read_bytes().replace(b"\n", b"\r\n"))

            with self.assertRaises(ValidationError) as caught:
                sync_schemas(root)

            message = str(caught.exception)
            self.assertTrue(message.startswith("contracts/schemas/tag-browse.output.schema.json: must use LF line endings"), message)
            self.assertIn("D31 section 5", message)
            for name in SCHEMA_NAMES:
                target = root / RESOURCES / f"{name}-output"
                self.assertFalse((target / "data.bin").exists())
                written = json.loads((target / "resource.json").read_text(encoding="utf-8"))
                self.assertEqual(written["attributes"]["size"], -1)

    def test_main_prints_one_error_line_and_exits_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(temporary)
            crlf = root / "contracts/schemas/tag-read.output.schema.json"
            crlf.write_bytes(crlf.read_bytes().replace(b"\n", b"\r\n"))

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                status = main(root)

            self.assertEqual(status, 1)
            lines = stderr.getvalue().splitlines()
            self.assertEqual(len(lines), 1, lines)
            self.assertTrue(lines[0].startswith("ERROR: contracts/schemas/tag-read.output.schema.json: must use LF line endings"), lines[0])
            self.assertIn("D31 section 5", lines[0])


if __name__ == "__main__":
    unittest.main()
