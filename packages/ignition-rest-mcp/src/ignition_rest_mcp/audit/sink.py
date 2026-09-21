"""D18 audit: canonical vocabulary, per-Tool allowlisted safe fields, durable phases."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
import logging
import time
from typing import Any

from ignition_rest_mcp.errors import GatewayError
from ignition_rest_mcp.operation import OperationContext
from ignition_rest_mcp.storage.database import Database, StorageUnavailable, transaction
from ignition_rest_mcp.storage.records import OperationRecordStore, iso_utc
from ignition_rest_mcp.observability.metrics import Metrics

LOGGER = logging.getLogger("ignition_rest_mcp.audit")

SAFE_FIELD_VALUE_LIMIT = 256
SAFE_FIELDS_JSON_LIMIT = 4096

# Allowlist, not a denylist (D18 "Each Tool defines safe audit fields").
# Phase 4 mutation Tools must be added here explicitly before their first audit row.
SAFE_AUDIT_FIELDS: dict[str, frozenset[str]] = {
    "project_export": frozenset({"projectName"}),
    "tag_config_export": frozenset({"provider", "path", "recursive", "includeUdts"}),
    "artifact_access": frozenset({"artifactId", "kind", "sensitivity", "method", "bytesSent"}),
    "artifact_info_restricted": frozenset({"artifactId", "kind"}),
    "config_resource_update": frozenset({"resourceType", "name", "collection"}),
    "config_resource_create": frozenset({"resourceType", "name", "collection"}),
    "config_resource_delete": frozenset({"resourceType", "name", "collection"}),
    "config_resource_rename": frozenset({"resourceType", "name", "collection"}),
    "project_import": frozenset({"projectName"}),
    "tag_config_import": frozenset({"provider", "path"}),
    "alarm_pipeline_cancel": frozenset({"path", "alarmEventId"}),
    #: Phase 4 ticket #19: the destructiveness of an artifact removal is only auditable
    #: if the row says what was destroyed. The identifier is the row's target_id, and
    #: the owning principal is the row's actor — neither is repeated here.
    "artifact_delete": frozenset({"kind", "sensitivity", "retentionClass"}),
}


class AuditWriteError(RuntimeError):
    """The durable audit write failed; audited operations must fail closed."""


@dataclass(frozen=True, slots=True)
class AuditRow:
    timestamp: str
    correlation_id: str
    server: str
    tool: str
    actor: str
    operation_class: str
    destructive: bool
    phase: str
    outcome: str
    target_type: str | None = None
    target_id: str | None = None
    duration_ms: float | None = None
    error_code: str | None = None
    transaction_id: str | None = None
    safe_fields: dict[str, Any] = field(default_factory=dict)


def allowlisted_fields(tool: str, provided: dict[str, Any]) -> dict[str, Any]:
    allowed = SAFE_AUDIT_FIELDS.get(tool, frozenset())
    result: dict[str, Any] = {}
    for key in sorted(allowed):
        if key not in provided:
            continue
        value = provided[key]
        if isinstance(value, (bool, int, str)):
            coerced: Any = value if not isinstance(value, str) else value[:SAFE_FIELD_VALUE_LIMIT]
            result[key] = coerced
    dumped = json.dumps(result, separators=(",", ":"))
    if len(dumped) > SAFE_FIELDS_JSON_LIMIT:
        # Bound the row explicitly rather than silently truncating a value.
        return {key: "omitted:oversize" for key in result}
    return result


class SqliteAuditSink:
    """Durable SQLite audit sink (``synchronous=FULL`` via its Database)."""

    def __init__(self, db: Database) -> None:
        self._db = db

    @property
    def healthy(self) -> bool:
        return self._db.healthy

    async def probe(self) -> bool:
        return await self._db.probe()

    async def write(self, row: AuditRow) -> None:
        payload = allowlisted_fields(row.tool, row.safe_fields)

        def _write(conn: Any) -> None:
            with transaction(conn):
                conn.execute(
                    "INSERT INTO audit_log (timestamp, correlation_id, server, tool, actor_key,"
                    " operation_class, destructive, target_type, target_id, phase, outcome, duration_ms,"
                    " error_code, transaction_id, safe_fields_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        row.timestamp, row.correlation_id, row.server, row.tool, row.actor,
                        row.operation_class, 1 if row.destructive else 0, row.target_type, row.target_id,
                        row.phase, row.outcome, row.duration_ms, row.error_code, row.transaction_id,
                        json.dumps(payload, separators=(",", ":")),
                    ),
                )

        try:
            await self._db.run(_write)
        except StorageUnavailable as error:
            raise AuditWriteError(str(error)) from error

    async def enforce_retention(
        self, max_rows: int, max_age_days: int, batch_rows: int, deadline_seconds: float,
    ) -> int:
        cutoff = iso_utc(datetime.now(timezone.utc) - timedelta(days=max_age_days))
        deleted = 0
        started_clock = time.monotonic()

        async def _pass(operation: Any, *, count: bool = False) -> int:
            nonlocal deleted
            if time.monotonic() - started_clock >= deadline_seconds:
                return -1
            result = int(await self._db.run(operation))
            if not count:
                deleted += result
            return result

        while True:
            removed = await _pass(
                lambda conn: _delete_audit_batch(
                    conn,
                    "DELETE FROM audit_log WHERE seq IN"
                    " (SELECT seq FROM audit_log WHERE timestamp < ? ORDER BY seq ASC LIMIT ?)",
                    (cutoff, batch_rows),
                )
            )
            if removed < 0 or removed == 0:
                break
        while True:
            remaining = await _pass(_count_audit_rows, count=True)
            if remaining < 0 or remaining <= max_rows:
                break
            removed = await _pass(
                lambda conn: _delete_audit_batch(
                    conn,
                    "DELETE FROM audit_log WHERE seq IN (SELECT seq FROM audit_log ORDER BY seq ASC LIMIT ?)",
                    (batch_rows,),
                )
            )
            if removed < 0 or removed == 0:
                break
        return deleted

    async def count(self) -> int:
        def _read(conn: Any) -> int:
            return int(conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0])

        return await self._db.run(_read)


def _delete_audit_batch(conn: Any, statement: str, parameters: tuple[Any, ...]) -> int:
    with transaction(conn):
        cursor = conn.execute(statement, parameters)
        return int(cursor.rowcount or 0)


def _count_audit_rows(conn: Any) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0])


class Auditor:
    """Slice-1 phase ordering for one audited operation (D08/D18).

    - ``decision`` before any Gateway call; allowed+write-fail fails closed.
    - ``attempt`` durably committed before dispatch; write-fail means no dispatch.
    - ``result`` after verification; a failed result write never rewrites a
      known outcome — it marks the operation record ``auditResultMissing``.
    """

    def __init__(
        self,
        sink: SqliteAuditSink,
        records: OperationRecordStore,
        context: OperationContext,
        metrics: "Metrics",
    ) -> None:
        self._sink = sink
        self._records = records
        self._context = context
        self._metrics = metrics
        self.attempt_recorded = False

    async def _write(self, phase: str, outcome: str, **kw: Any) -> bool:
        row = AuditRow(
            timestamp=iso_utc(datetime.now(timezone.utc)),
            correlation_id=self._context.correlation_id,
            server=self._context.server,
            tool=self._context.tool,
            actor=self._context.actor,
            operation_class=self._context.permission_class,
            destructive=self._context.destructive,
            phase=phase,
            outcome=outcome,
            transaction_id=self._context.transaction_id,
            **kw,
        )
        try:
            await self._sink.write(row)
            return True
        except AuditWriteError as error:
            if self._metrics is not None:
                self._metrics.record_audit_write_failure(phase)
            LOGGER.error(
                "Audit write failed",
                extra={
                    "event": "audit_write_failure", "correlationId": self._context.correlation_id,
                    "tool": self._context.tool, "outcome": "error", "errorCode": type(error).__name__,
                },
            )
            return False

    async def decision(self, *, allowed: bool, reason: str = "", **kw: Any) -> None:
        written = await self._write(
            "decision", "allowed" if allowed else f"denied:{reason or 'policy'}", **kw,
        )
        if not written and allowed:
            raise GatewayError(
                "internal_error",
                "The audit subsystem could not record the pre-dispatch decision; "
                "the audited operation was not dispatched.",
            )

    async def attempt(self, **kw: Any) -> None:
        written = await self._write("attempt", "attempted", **kw)
        if not written:
            raise GatewayError(
                "internal_error",
                "The audit subsystem could not durably commit the attempt record; "
                "the audited operation was not dispatched.",
            )
        self.attempt_recorded = True

    async def result(self, outcome: str, *, error_code: str | None = None, **kw: Any) -> None:
        written = await self._write("result", outcome, error_code=error_code, **kw)
        if not written:
            # A missing result after an attempt is the D18 interruption signal. The true
            # outcome must still be returned; the record flags the audit gap.
            try:
                await self._records.mark_audit_result_missing(self._context.correlation_id)
            except StorageUnavailable:
                LOGGER.error(
                    "Operation record also unavailable for auditResultMissing marking",
                    extra={
                        "event": "audit_result_record_failure",
                        "correlationId": self._context.correlation_id,
                        "tool": self._context.tool,
                    },
                )
