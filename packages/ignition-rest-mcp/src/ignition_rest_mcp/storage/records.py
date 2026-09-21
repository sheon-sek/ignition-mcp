"""D18 operation records: durable, bounded, startup-interruptible diagnostics.

Ordinary-read operation records are diagnostics, not audit. Write failures here
are counted and logged but never fail the operation they describe.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import time
from typing import Any, Callable

from ignition_rest_mcp.operation import OperationContext
from ignition_rest_mcp.storage.database import Database, transaction

MAX_PHASES = 32
PHASE_NAME_LIMIT = 64


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass(frozen=True, slots=True)
class OperationRecord:
    correlation_id: str
    tool: str
    permission_class: str
    budget_class: str
    destructive: bool
    principal_key: str
    transaction_id: str | None
    client_request_id: str | None
    status: str
    outcome: str | None
    error_code: str | None
    phases: tuple[dict[str, str], ...]
    phases_truncated: bool
    audit_result_missing: bool
    started_at: str
    finished_at: str | None
    downstream_correlation_id: str | None

    def as_diagnose_payload(self) -> dict[str, Any]:
        """The D19-safe projection: never the principal key, never logs or bodies."""

        return {
            "correlationId": self.correlation_id,
            "tool": self.tool,
            "outcome": self.outcome,
            "errorCode": self.error_code,
            "startedAt": self.started_at,
            "finishedAt": self.finished_at,
            "phases": [dict(phase) for phase in self.phases],
            "phasesTruncated": self.phases_truncated,
            "transactionId": self.transaction_id,
            "downstreamCorrelationId": self.downstream_correlation_id,
            "auditResultMissing": self.audit_result_missing,
        }


class OperationRecordStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    @property
    def healthy(self) -> bool:
        return self._db.healthy

    async def start(self, context: OperationContext) -> None:
        def _write(conn: Any) -> None:
            with transaction(conn):
                conn.execute(
                    "INSERT INTO operation_records (correlation_id, tool, permission_class, budget_class,"
                    " destructive, principal_key, transaction_id, client_request_id, status, started_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        context.correlation_id, context.tool, context.permission_class, context.budget_class,
                        1 if context.destructive else 0, context.actor, context.transaction_id,
                        context.client_request_id, "in_progress", iso_utc(context.started_at),
                    ),
                )

        await self._db.run(_write)

    async def append_phase(self, correlation_id: str, name: str) -> None:
        phase_name = str(name)[:PHASE_NAME_LIMIT]
        at = iso_utc(utc_now())

        def _write(conn: Any) -> None:
            with transaction(conn):
                row = conn.execute(
                    "SELECT phases_json, phases_truncated FROM operation_records WHERE correlation_id = ?",
                    (correlation_id,),
                ).fetchone()
                if row is None:
                    return
                phases = json.loads(row[0])
                truncated = bool(row[1])
                if len(phases) >= MAX_PHASES:
                    # Bounded and *explicit*: drop the oldest and flag it. Never silent.
                    phases.pop(0)
                    truncated = True
                phases.append({"name": phase_name, "at": at})
                conn.execute(
                    "UPDATE operation_records SET phases_json = ?, phases_truncated = ? WHERE correlation_id = ?",
                    (json.dumps(phases, separators=(",", ":")), 1 if truncated else 0, correlation_id),
                )

        await self._db.run(_write)

    async def finish(self, correlation_id: str, outcome: str, error_code: str | None = None) -> None:
        def _write(conn: Any) -> None:
            with transaction(conn):
                conn.execute(
                    "UPDATE operation_records SET status = 'completed', outcome = ?, error_code = ?,"
                    " finished_at = ? WHERE correlation_id = ? AND status = 'in_progress'",
                    (outcome, error_code, iso_utc(utc_now()), correlation_id),
                )

        await self._db.run(_write)

    async def mark_audit_result_missing(self, correlation_id: str) -> None:
        def _write(conn: Any) -> None:
            with transaction(conn):
                conn.execute(
                    "UPDATE operation_records SET audit_result_missing = 1 WHERE correlation_id = ?",
                    (correlation_id,),
                )

        await self._db.run(_write)

    async def set_transaction(self, correlation_id: str, transaction_id: str) -> None:
        def _write(conn: Any) -> None:
            with transaction(conn):
                conn.execute(
                    "UPDATE operation_records SET transaction_id = ? WHERE correlation_id = ?",
                    (transaction_id, correlation_id),
                )

        await self._db.run(_write)

    async def set_downstream_correlation(self, correlation_id: str, downstream: str) -> None:
        def _write(conn: Any) -> None:
            with transaction(conn):
                conn.execute(
                    "UPDATE operation_records SET downstream_correlation_id = ? WHERE correlation_id = ?",
                    (downstream, correlation_id),
                )

        await self._db.run(_write)

    async def fetch(self, correlation_id: str) -> OperationRecord | None:
        def _read(conn: Any) -> OperationRecord | None:
            row = conn.execute(
                "SELECT correlation_id, tool, permission_class, budget_class, destructive, principal_key,"
                " transaction_id, client_request_id, status, outcome, error_code, phases_json,"
                " phases_truncated, audit_result_missing, started_at, finished_at, downstream_correlation_id"
                " FROM operation_records WHERE correlation_id = ?",
                (correlation_id,),
            ).fetchone()
            return _to_record(row) if row is not None else None

        return await self._db.run(_read)

    async def mark_interrupted_stale(self, max_age_seconds: float) -> int:
        """Startup sweep: in_progress rows older than the maximum ARTIFACT deadline are
        diagnosable interruptions, never re-executed."""

        cutoff = iso_utc(utc_now() - timedelta(seconds=max_age_seconds))

        def _write(conn: Any) -> int:
            with transaction(conn):
                cursor = conn.execute(
                    "UPDATE operation_records SET status = 'completed', outcome = 'interrupted',"
                    " finished_at = ? WHERE status = 'in_progress' AND started_at < ?",
                    (iso_utc(utc_now()), cutoff),
                )
                return int(cursor.rowcount or 0)

        return await self._db.run(_write)

    async def enforce_retention(
        self, max_rows: int, max_age_hours: int, batch_rows: int, deadline_seconds: float,
    ) -> int:
        """Bounded batches with an elapsed deadline; never removes in_progress rows."""

        cutoff = iso_utc(utc_now() - timedelta(hours=max_age_hours))
        deleted = 0
        started_clock = time.monotonic()

        async def _pass(operation: Callable[[Any], int], *, count: bool = False) -> int:
            nonlocal deleted
            if time.monotonic() - started_clock >= deadline_seconds:
                return -1
            result = int(await self._db.run(operation))
            if not count:
                deleted += result
            return result

        while True:
            removed = await _pass(
                lambda conn: _delete_batch(
                    conn,
                    "DELETE FROM operation_records WHERE correlation_id IN"
                    " (SELECT correlation_id FROM operation_records WHERE status != 'in_progress'"
                    " AND started_at < ? ORDER BY started_at ASC LIMIT ?)",
                    (cutoff, batch_rows),
                )
            )
            if removed < 0 or removed == 0:
                break
        while True:
            remaining = await _pass(_count_not_in_progress, count=True)
            if remaining < 0 or remaining <= max_rows:
                break
            removed = await _pass(
                lambda conn: _delete_batch(
                    conn,
                    "DELETE FROM operation_records WHERE correlation_id IN"
                    " (SELECT correlation_id FROM operation_records WHERE status != 'in_progress'"
                    " ORDER BY started_at ASC LIMIT ?)",
                    (batch_rows,),
                )
            )
            if removed < 0 or removed == 0:
                break
        return deleted

    async def count(self) -> int:
        def _read(conn: Any) -> int:
            row = conn.execute("SELECT COUNT(*) FROM operation_records").fetchone()
            return int(row[0])

        return await self._db.run(_read)


def _delete_batch(conn: Any, statement: str, parameters: tuple[Any, ...]) -> int:
    with transaction(conn):
        cursor = conn.execute(statement, parameters)
        return int(cursor.rowcount or 0)


def _count_not_in_progress(conn: Any) -> int:
    row = conn.execute("SELECT COUNT(*) FROM operation_records WHERE status != 'in_progress'").fetchone()
    return int(row[0])


def _to_record(row: Any) -> OperationRecord:
    return OperationRecord(
        correlation_id=row[0],
        tool=row[1],
        permission_class=row[2],
        budget_class=row[3],
        destructive=bool(row[4]),
        principal_key=row[5],
        transaction_id=row[6],
        client_request_id=row[7],
        status=row[8],
        outcome=row[9],
        error_code=row[10],
        phases=tuple(json.loads(row[11])),
        phases_truncated=bool(row[12]),
        audit_result_missing=bool(row[13]),
        started_at=row[14],
        finished_at=row[15],
        downstream_correlation_id=row[16],
    )
