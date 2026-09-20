"""Structured logging configuration for the external MCP server."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging

_SAFE_EXTRA_FIELDS = (
    "event",
    "correlationId",
    "tool",
    "server",
    "actor",
    "permissionClass",
    "outcome",
    "errorCode",
    "durationMs",
    "registryState",
    "registryGeneration",
)


class JsonLogFormatter(logging.Formatter):
    """Render bounded diagnostic records without serializing arbitrary LogRecord state."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in _SAFE_EXTRA_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info is not None:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            default=str,
        )


def configure_logging(*, log_format: str, level: str) -> None:
    """Configure process logging once using the deployment-selected representation."""

    handler = logging.StreamHandler()
    if log_format == "json":
        handler.setFormatter(JsonLogFormatter())
    elif log_format == "text":
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
    else:
        raise ValueError(f"Unsupported log format: {log_format}")

    logging.basicConfig(level=level, handlers=[handler], force=True)
