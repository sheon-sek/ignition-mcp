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

    def test_writes_a_crlf_schema_with_lf_line_endings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(temporary)
            crlf = root / "contracts/schemas/tag-browse.output.schema.json"
            lf = crlf.read_bytes()
            crlf.write_bytes(lf.replace(b"\n", b"\r\n"))

            sync_schemas(root)

            target = root / RESOURCES / "tag-browse-output"
            self.assertEqual((target / "data.bin").read_bytes(), lf)
            written = json.loads((target / "resource.json").read_text(encoding="utf-8"))
            self.assertEqual(written["attributes"]["size"], len(lf))

    def test_rejects_a_lone_cr_before_writing_any_resource(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(temporary)
            bad = root / "contracts/schemas/tag-browse.output.schema.json"
            bad.write_bytes(bad.read_bytes().replace(b"\n", b"\r", 1))

            with self.assertRaises(ValidationError):
                sync_schemas(root)

            for name in SCHEMA_NAMES:
                self.assertFalse((root / RESOURCES / f"{name}-output" / "data.bin").exists())

    def test_main_prints_one_error_line_and_exits_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(temporary)
            bad = root / "contracts/schemas/tag-read.output.schema.json"
            bad.write_bytes(bad.read_bytes().replace(b"\n", b"\r", 1))

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                status = main(root)

            self.assertEqual(status, 1)
            lines = stderr.getvalue().splitlines()
            self.assertEqual(len(lines), 1, lines)
            self.assertTrue(lines[0].startswith("ERROR: contracts/schemas/tag-read.output.schema.json: contains a carriage return"), lines[0])


if __name__ == "__main__":
    unittest.main()
