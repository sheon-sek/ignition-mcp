from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from tooling.native.jython_runner import run_recorded_tool

ROOT = Path(__file__).resolve().parents[4]
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/tag_query-full-path-continuation.json"


def test_tag_query_normalizes_recorded_full_path_and_continuation() -> None:
    result = run_recorded_tool("tag_query", FIXTURE)

    structured = result["structuredContent"]
    assert structured["items"] == [
        {
            "path": "[default]Area/Pump 1/Running",
            "properties": {
                "path": "[default]Area/Pump 1/Running",
                "tagType": "AtomicTag",
            },
        }
    ]
    assert structured["continuation"] == "recorded-continuation-2"
    assert structured["summary"] == {"returned": 1, "limit": 1, "hasMore": True}

    contract = json.loads(
        (ROOT / "contracts/tools/runtime/tag_query.contract.json").read_text(encoding="utf-8")
    )
    schema = json.loads((ROOT / contract["outputSchema"]).read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(structured)
