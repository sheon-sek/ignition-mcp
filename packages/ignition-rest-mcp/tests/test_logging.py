from __future__ import annotations

import json
import logging

from ignition_rest_mcp.observability.logging import JsonLogFormatter


def test_json_formatter_emits_only_curated_context_fields() -> None:
    record = logging.LogRecord(
        name="ignition_rest_mcp",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="done",
        args=(),
        exc_info=None,
    )
    record.correlationId = "corr-1"
    record.tool = "gateway_info"
    record.outcome = "success"
    record.secret = "must-not-leak"

    value = json.loads(JsonLogFormatter().format(record))

    assert value["message"] == "done"
    assert value["correlationId"] == "corr-1"
    assert value["tool"] == "gateway_info"
    assert value["outcome"] == "success"
    assert "secret" not in value
