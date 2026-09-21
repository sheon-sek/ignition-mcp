#!/usr/bin/env python3
"""Run Phase 2 fixture provisioning against the shared recorded Gateway fake."""
from __future__ import annotations

import json
from pathlib import Path
import runpy
import sys

HARNESS_DIR = Path(__file__).resolve().parents[1]
ROOT = HARNESS_DIR.parents[1]
sys.path.insert(0, str(HARNESS_DIR))

from recorded_gateway import API_TOKEN, FIXTURES, RecordedGateway  # noqa: E402


def main() -> int:
    provision_module = runpy.run_path(str(Path(__file__).with_name("provision.py")))
    with RecordedGateway() as gateway:
        result = provision_module["provision"](gateway.base_url, API_TOKEN)
    expected = json.loads((FIXTURES / "phase2/provision-success.json").read_text(encoding="utf-8"))
    comparable = {
        "databaseConnectUrl": result["databaseConnectUrl"],
        "databaseDriver": result["databaseDriver"],
        "databaseTranslator": result["databaseTranslator"],
        "initialTagImport": result["initialTagImport"],
        "activeTagImport": result["activeTagImport"],
        "resources": result["resources"],
    }
    if any(comparable[key] != expected[key] for key in comparable):
        print(json.dumps({"status": "FAILED", "result": result}, indent=2, sort_keys=True))
        return 1
    print(json.dumps({"status": "PASS", **result}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
