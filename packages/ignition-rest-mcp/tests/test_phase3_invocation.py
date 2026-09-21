"""Slice 1 (Phase 3 / G3): storage foundation, central invocation lifecycle,
AuditSink and operation records. Tests assert observable behavior."""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

import pytest
from ignition_rest_mcp.audit.sink import AuditRow, Auditor, SqliteAuditSink, allowlisted_fields
from ignition_rest_mcp.auth import Principal
from ignition_rest_mcp.capabilities.registry import CapabilityRegistry, CapabilitySnapshot
from ignition_rest_mcp.errors import GatewayError
from ignition_rest_mcp.invocation.lifecycle import invoke_tool
from ignition_rest_mcp.models import GatewayInfoResult
from ignition_rest_mcp.operation import is_uuid7, uuid7
from ignition_rest_mcp.observability.metrics import Metrics
from ignition_rest_mcp.storage.database import Database, Storage, StorageUnavailable
from ignition_rest_mcp.storage.paths import validate_data_directory
from ignition_rest_mcp.storage.records import MAX_PHASES, OperationRecordStore, iso_utc
from ignition_rest_mcp.storage.schema import AUDIT_DDL, STATE_DDL
from test_config import _settings

INFO_MODEL = GatewayInfoResult(
    correlationId="c", name="g", edition="standard", ignitionVersion="8.3.8",
    redundancyRole="Independent", deploymentMode="", timeZoneId="UTC", jvmVersion="17",
)


def _principal() -> Principal:
    return Principal(key="none:test", scopes=frozenset({"ignition.read"}), auth_mode="none")


class _State:
    """Minimal stand-in for RuntimeState with real storage-backed parts."""

    def __init__(self, tmp: Path) -> None:
        self.metrics = Metrics()
        self.storage = Storage(tmp)
        self._opened = False

    async def open(self) -> None:
        await self.storage.open()
        self.records = OperationRecordStore(self.storage.state)
        self.audit_sink = SqliteAuditSink(self.storage.audit)
        self._opened = True

    async def close(self) -> None:
        await self.storage.close()

    def require_metrics(self) -> Metrics:
        return self.metrics

    def require_records(self) -> OperationRecordStore:
        return self.records  # type: ignore[attr-defined,no-any-return]

    def require_audit_sink(self) -> SqliteAuditSink:
        return self.audit_sink  # type: ignore[attr-defined,no-any-return]

    def require_registry(self) -> CapabilityRegistry:
        registry = CapabilityRegistry.__new__(CapabilityRegistry)
        registry._snapshot = CapabilitySnapshot.unavailable()
        registry._refresh_lock = asyncio.Lock()
        registry._refresh_task = None
        return registry


def _tiny_result(payload: str = "small") -> GatewayInfoResult:
    return INFO_MODEL.model_copy(update={"name": payload})


# ----------------------------------------------------------------------------- UUIDv7


def test_uuid7_is_monotonic_sortable_and_unique() -> None:
    values = [uuid7() for _ in range(5_000)]
    assert len(set(values)) == 5_000
    assert values == sorted(values)
    assert all(is_uuid7(value) for value in values)


def test_uuid7_version_and_variant_bits() -> None:
    value = uuid7()
    parsed = int(value.replace("-", ""), 16)
    assert (parsed >> 76) & 0xF == 7
    assert (parsed >> 62) & 0x3 == 0b10


def test_is_uuid7_rejects_foreign_shapes() -> None:
    assert not is_uuid7("0e7f5c2a-1b2c-4d3e-8f90-112233445566")  # uuid4
    assert not is_uuid7("not-a-uuid")
    assert not is_uuid7(uuid7().upper())  # canonical form required


# ----------------------------------------------------------------------------- data dir


def test_data_dir_is_created_private_and_validated(tmp_path: Path) -> None:
    target = tmp_path / "state-dir"
    resolved = validate_data_directory(str(target), "development")
    assert resolved == target
    assert oct(target.stat().st_mode & 0o777) == "0o700"


def test_data_dir_rejects_existing_file(tmp_path: Path) -> None:
    occupant = tmp_path / "occupant"
    occupant.write_text("x")
    with pytest.raises(Exception, match="directory"):
        validate_data_directory(str(occupant), "development")


def test_data_dir_rejects_symlink(tmp_path: Path) -> None:
    link = tmp_path / "link"
    link.symlink_to(tmp_path)
    with pytest.raises(Exception, match="symbolic link"):
        validate_data_directory(str(link), "development")


def test_data_dir_rejects_unwritable_directory(tmp_path: Path) -> None:
    locked = tmp_path / "locked"
    locked.mkdir(mode=0o500)
    with pytest.raises(Exception, match="writable"):
        validate_data_directory(str(locked), "development")
    locked.chmod(0o700)


@pytest.mark.parametrize("profile", ["trusted-internal", "secured"])
def test_production_profiles_reject_runtime_temp_filesystems(profile: str) -> None:
    with pytest.raises(Exception, match="temporary filesystem"):
        validate_data_directory("/tmp/definitely-not-persistent-state", profile)


def test_development_may_use_temp_filesystems(tmp_path: Path) -> None:
    validate_data_directory(str(tmp_path), "development")


# ----------------------------------------------------------------------------- database


def _open_state(tmp_path: Path) -> _State:
    state = _State(tmp_path)
    asyncio.run(state.open())
    return state


def test_migration_forward_only_refuses_future_version(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    import sqlite3

    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version=99")
    conn.commit()
    conn.close()
    db = Database("state", path, STATE_DDL)
    with pytest.raises(StorageUnavailable):
        asyncio.run(db.open())
    assert db.detail == "MigrationError"


def test_open_rejects_corrupt_database_file(tmp_path: Path) -> None:
    path = tmp_path / "audit.db"
    path.write_bytes(b"this is not a sqlite database" * 50)
    db = Database("audit", path, AUDIT_DDL, synchronous="FULL")
    with pytest.raises(StorageUnavailable):
        asyncio.run(db.open())


def test_wal_synchronous_and_file_modes(tmp_path: Path) -> None:
    state = _open_state(tmp_path)
    try:
        async def scenario() -> None:
            journal = await state.storage.audit.run(lambda conn: conn.execute("PRAGMA journal_mode").fetchone()[0])
            sync = await state.storage.audit.run(lambda conn: conn.execute("PRAGMA synchronous").fetchone()[0])
            assert (journal, sync) == ("wal", 2)
            await state.audit_sink.write(AuditRow(
                timestamp=iso_utc(__import__("datetime").datetime.now(__import__("datetime").timezone.utc)),
                correlation_id=uuid7(), server="ignition-rest", tool="artifact_access",
                actor="none:test", operation_class="READ", destructive=False,
                phase="attempt", outcome="attempted",
            ))
            assert oct((tmp_path / "audit.db").stat().st_mode & 0o777) == "0o600"

        asyncio.run(scenario())
    finally:
        asyncio.run(state.close())


def test_probe_recovers_and_reports_health(tmp_path: Path) -> None:
    state = _open_state(tmp_path)
    try:
        async def scenario() -> None:
            state.storage.audit._healthy = False
            assert state.storage.audit.healthy is False
            assert (await state.storage.probe_all())["audit"] is True
            assert state.storage.audit.healthy is True

        asyncio.run(scenario())
    finally:
        asyncio.run(state.close())


def test_sql_runs_off_the_event_loop(tmp_path: Path) -> None:
    state = _open_state(tmp_path)
    try:
        async def scenario() -> None:
            worker = await state.storage.state.run(lambda conn: threading.current_thread().name)
            assert worker != threading.current_thread().name

        asyncio.run(scenario())
    finally:
        asyncio.run(state.close())


# ----------------------------------------------------------------------------- lifecycle


def _invoke(scenario_handler, *, audited=False, settings=None, state=None):  # type: ignore[no-untyped-def]
    async def run() -> GatewayInfoResult:
        assert state is not None
        return await invoke_tool(
            state=state,  # type: ignore[arg-type]
            settings=settings or _settings(),
            tool="test_tool",
            budget_class="FAST",
            principal=_principal(),
            handler=scenario_handler,
            audited=audited,
        )

    return asyncio.run(run())


def test_successful_invocation_writes_completed_record(tmp_path: Path) -> None:
    state = _open_state(tmp_path)
    try:
        async def handler(context):  # type: ignore[no-untyped-def]
            assert is_uuid7(context.correlation_id)
            assert context.actor == "none:test"
            return _tiny_result()

        result = _invoke(handler, state=state)
        assert result.name == "small"

        latest = None

        async def read_latest() -> None:
            nonlocal latest
            conn_records = state.require_records()
            rows = await conn_records._db.run(
                lambda conn: conn.execute("SELECT correlation_id, outcome FROM operation_records").fetchall()
            )
            latest = rows

        asyncio.run(read_latest())
        assert [outcome for _, outcome in latest] == ["succeeded"]
    finally:
        asyncio.run(state.close())


def test_gateway_error_finalizes_record_and_maps_code(tmp_path: Path) -> None:
    state = _open_state(tmp_path)
    try:
        async def failing(context):  # type: ignore[no-untyped-def]
            raise GatewayError("permission_denied", "Safe rejection")

        with pytest.raises(Exception) as captured:
            _invoke(failing, state=state)
        payload = json.loads(str(captured.value))
        assert payload["code"] == "permission_denied"
        assert is_uuid7(payload["correlationId"])

        outcomes = []

        async def read() -> None:
            outcomes.extend(
                await state.require_records()._db.run(
                    lambda conn: conn.execute("SELECT outcome, error_code FROM operation_records").fetchall()
                )
            )

        asyncio.run(read())
        assert outcomes == [("failed", "permission_denied")]
    finally:
        asyncio.run(state.close())


def test_timeout_finalizes_record_with_timeout_code(tmp_path: Path) -> None:
    state = _open_state(tmp_path)
    try:
        async def hanging(context):  # type: ignore[no-untyped-def]
            await asyncio.Event().wait()

        with pytest.raises(Exception, match="timeout"):
            _invoke(hanging, state=state, settings=_settings(tool_timeout_seconds=0.02))

        outcomes = []

        async def read() -> None:
            outcomes.extend(
                await state.require_records()._db.run(
                    lambda conn: conn.execute("SELECT outcome, error_code FROM operation_records").fetchall()
                )
            )

        asyncio.run(read())
        assert outcomes == [("failed", "timeout")]
    finally:
        asyncio.run(state.close())


def test_cancellation_finalizes_record_and_reraises(tmp_path: Path) -> None:
    state = _open_state(tmp_path)
    try:
        started = asyncio.Event()

        async def hanging(context):  # type: ignore[no-untyped-def]
            started.set()
            await asyncio.Event().wait()

        async def scenario() -> None:
            task = asyncio.create_task(invoke_tool(
                state=state,  # type: ignore[arg-type]
                settings=_settings(),
                tool="test_tool",
                budget_class="FAST",
                principal=_principal(),
                handler=hanging,
            ))
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            rows = await state.require_records()._db.run(
                lambda conn: conn.execute("SELECT outcome FROM operation_records").fetchall()
            )
            assert rows == [("cancelled",)]

        asyncio.run(scenario())
    finally:
        asyncio.run(state.close())


def test_output_budget_violation_fails_explicitly(tmp_path: Path) -> None:
    state = _open_state(tmp_path)
    try:
        async def huge(context):  # type: ignore[no-untyped-def]
            return _tiny_result("x" * 2_000_000)

        with pytest.raises(Exception, match="limit_exceeded"):
            _invoke(huge, state=state)
    finally:
        asyncio.run(state.close())


def test_record_start_failure_never_fails_ordinary_read(tmp_path: Path) -> None:
    state = _open_state(tmp_path)
    try:
        state.storage.state._healthy = False

        async def handler(context):  # type: ignore[no-untyped-def]
            return _tiny_result()

        result = _invoke(handler, state=state)
        assert result.name == "small"
        assert state.metrics.operation_record_write_failures["start"] == 1
    finally:
        asyncio.run(state.close())


def test_phases_are_bounded_with_explicit_truncation(tmp_path: Path) -> None:
    state = _open_state(tmp_path)
    try:
        async def handler(context):  # type: ignore[no-untyped-def]
            records = state.require_records()
            for index in range(MAX_PHASES + 8):
                await records.append_phase(context.correlation_id, f"phase-{index}")
            return _tiny_result("phase-bound")

        _invoke(handler, state=state)

        rows = []

        async def read() -> None:
            rows.extend(
                await state.require_records()._db.run(
                    lambda conn: conn.execute(
                        "SELECT phases_json, phases_truncated FROM operation_records"
                    ).fetchall()
                )
            )

        asyncio.run(read())
        assert len(rows) == 1
        phases = json.loads(rows[0][0])
        assert len(phases) == MAX_PHASES
        assert rows[0][1] == 1  # truncation is explicit, never silent (D10)
        assert phases[0]["name"] == "phase-8"
        assert phases[-1]["name"] == f"phase-{MAX_PHASES + 7}"
    finally:
        asyncio.run(state.close())


def test_startup_marks_only_old_in_progress_as_interrupted(tmp_path: Path) -> None:
    state = _open_state(tmp_path)
    try:
        async def scenario() -> None:
            records = state.require_records()
            context = __import__("ignition_rest_mcp.operation", fromlist=["OperationContext"]).OperationContext.start(
                "old_tool", "none:test", "FAST",
            )
            await records.start(context)
            old_correlation = context.correlation_id
            # Age the row past the ARTIFACT hard deadline.
            await records._db.run(
                lambda conn: conn.execute(
                    "UPDATE operation_records SET started_at = '2000-01-01T00:00:00.000000Z' WHERE correlation_id = ?",
                    (old_correlation,),
                )
            )
            fresh = __import__("ignition_rest_mcp.operation", fromlist=["OperationContext"]).OperationContext.start(
                "fresh_tool", "none:test", "FAST",
            )
            await records.start(fresh)
            swept = await records.mark_interrupted_stale(300.0)
            assert swept == 1
            old = await records.fetch(old_correlation)
            fresh_row = await records.fetch(fresh.correlation_id)
            assert old is not None and old.outcome == "interrupted"
            assert fresh_row is not None and fresh_row.status == "in_progress"

        asyncio.run(scenario())
    finally:
        asyncio.run(state.close())


def test_retention_bounds_rows_and_spares_in_progress(tmp_path: Path) -> None:
    state = _open_state(tmp_path)
    try:
        async def scenario() -> None:
            records = state.require_records()
            op = __import__("ignition_rest_mcp.operation", fromlist=["OperationContext"])
            contexts = [op.OperationContext.start("t", "none:test", "FAST") for _ in range(40)]
            for context in contexts:
                await records.start(context)
                await records.finish(context.correlation_id, "succeeded")
            keep = op.OperationContext.start("running", "none:test", "FAST")
            await records.start(keep)
            deleted = await records.enforce_retention(max_rows=10, max_age_hours=72, batch_rows=7, deadline_seconds=20)
            rows = await records._db.run(
                lambda conn: conn.execute(
                    "SELECT tool, status FROM operation_records ORDER BY tool",
                ).fetchall()
            )
            # Bounded batches may overshoot below max_rows by less than one batch,
            # but never above it, and in_progress rows are untouchable.
            assert 0 < deleted < 40
            assert ("running", "in_progress") in rows
            completed = [row for row in rows if row[1] == "completed"]
            assert len(completed) <= 10 and len(completed) > 10 - 7
            assert deleted == 40 - len(completed)

        asyncio.run(scenario())
    finally:
        asyncio.run(state.close())


def test_stale_age_retention_removes_old_completed_rows(tmp_path: Path) -> None:
    state = _open_state(tmp_path)
    try:
        async def scenario() -> None:
            records = state.require_records()
            op = __import__("ignition_rest_mcp.operation", fromlist=["OperationContext"])
            context = op.OperationContext.start("t", "none:test", "FAST")
            await records.start(context)
            await records.finish(context.correlation_id, "succeeded")
            await records._db.run(
                lambda conn: conn.execute(
                    "UPDATE operation_records SET started_at = '2000-01-01T00:00:00.000000Z' WHERE correlation_id = ?",
                    (context.correlation_id,),
                )
            )
            deleted = await records.enforce_retention(max_rows=1000, max_age_hours=1, batch_rows=500, deadline_seconds=20)
            assert deleted == 1
            assert await records.count() == 0

        asyncio.run(scenario())
    finally:
        asyncio.run(state.close())


# ----------------------------------------------------------------------------- audit


def test_safe_fields_are_allowlisted_per_tool() -> None:
    filtered = allowlisted_fields("project_export", {
        "projectName": "Demo", "password": "hunter2", "raw_body": "x" * 9_000,
    })
    assert filtered == {"projectName": "Demo"}
    assert allowlisted_fields("unknown_tool", {"anything": 1}) == {}


def test_audit_row_round_trips_only_canonical_and_allowlisted_fields(tmp_path: Path) -> None:
    state = _open_state(tmp_path)
    try:
        async def scenario() -> None:
            row = AuditRow(
                timestamp=iso_utc(__import__("datetime").datetime.now(__import__("datetime").timezone.utc)),
                correlation_id=uuid7(), server="ignition-rest", tool="tag_config_export",
                actor="none:test", operation_class="READ", destructive=False,
                phase="decision", outcome="allowed",
                safe_fields={"provider": "default", "path": "Facility", "token": "SECRET"},
            )
            await state.audit_sink.write(row)
            stored = await state.storage.audit.run(
                lambda conn: conn.execute(
                    "SELECT safe_fields_json FROM audit_log WHERE correlation_id = ?", (row.correlation_id,),
                ).fetchone()
            )
            assert json.loads(stored[0]) == {"path": "Facility", "provider": "default"}

        asyncio.run(scenario())
    finally:
        asyncio.run(state.close())


def _auditor(tmp_path: Path):  # type: ignore[no-untyped-def]
    state = _open_state(tmp_path)
    op = __import__("ignition_rest_mcp.operation", fromlist=["OperationContext"])
    context = op.OperationContext.start("project_export", "none:test", "ARTIFACT")
    return state, Auditor(state.audit_sink, state.records, context, state.metrics), context


def test_decision_failure_fails_closed_before_dispatch(tmp_path: Path) -> None:
    state, auditor, context = _auditor(tmp_path)
    try:
        async def scenario() -> None:
            state.storage.audit._healthy = False
            with pytest.raises(GatewayError) as captured:
                await auditor.decision(allowed=True, target_type="project", target_id="Demo",
                                       safe_fields={"projectName": "Demo"})
            assert captured.value.code == "internal_error"
            assert "not dispatched" in captured.value.message
            assert state.metrics.audit_write_failures["decision"] == 1

        asyncio.run(scenario())
    finally:
        asyncio.run(state.close())


def test_denial_decision_failure_still_denies(tmp_path: Path) -> None:
    state, auditor, context = _auditor(tmp_path)
    try:
        async def scenario() -> None:
            state.storage.audit._healthy = False
            await auditor.decision(allowed=False, reason="gate_off")  # must not raise

        asyncio.run(scenario())
    finally:
        asyncio.run(state.close())


def test_attempt_failure_prevents_dispatch(tmp_path: Path) -> None:
    state, auditor, context = _auditor(tmp_path)
    try:
        async def scenario() -> None:
            await auditor.decision(allowed=True)
            state.storage.audit._healthy = False
            with pytest.raises(GatewayError) as captured:
                await auditor.attempt()
            assert captured.value.code == "internal_error"
            assert auditor.attempt_recorded is False

        asyncio.run(scenario())
    finally:
        asyncio.run(state.close())


def test_missing_result_marks_record_not_outcome(tmp_path: Path) -> None:
    state, auditor, context = _auditor(tmp_path)
    try:
        async def scenario() -> None:
            await state.require_records().start(context)
            await auditor.decision(allowed=True)
            await auditor.attempt()
            assert auditor.attempt_recorded is True
            state.storage.audit._healthy = False
            await auditor.result("completed")  # must never raise
            record = await state.require_records().fetch(context.correlation_id)
            assert record is not None
            assert record.audit_result_missing is True
            assert state.metrics.audit_write_failures["result"] == 1

        asyncio.run(scenario())
    finally:
        asyncio.run(state.close())


def test_audited_invocation_records_all_three_phases_in_order(tmp_path: Path) -> None:
    state, _, _ = _auditor(tmp_path)
    try:
        async def handler(context):  # type: ignore[no-untyped-def]
            auditor = context.auditor
            assert auditor is not None
            await auditor.decision(allowed=True, target_type="project", target_id="Demo",
                                   safe_fields={"projectName": "Demo"})
            await auditor.attempt(target_type="project", target_id="Demo")
            await auditor.result("completed", duration_ms=1.0)
            return _tiny_result()

        async def run() -> object:
            return await invoke_tool(
                state=state,  # type: ignore[arg-type]
                settings=_settings(),
                tool="project_export",
                budget_class="ARTIFACT",
                principal=_principal(),
                handler=handler,
                audited=True,
            )

        result = asyncio.run(run())
        correlation = getattr(result, "correlationId", None)
        del correlation

        async def check() -> None:
            rows = await state.storage.audit.run(
                lambda conn: conn.execute(
                    "SELECT phase, outcome FROM audit_log ORDER BY seq",
                ).fetchall()
            )
            assert [row[0] for row in rows] == ["decision", "attempt", "result"]
            assert rows[0][1] == "allowed" and rows[1][1] == "attempted" and rows[2][1] == "completed"
            op_rows = await state.storage.state.run(
                lambda conn: conn.execute("SELECT correlation_id, outcome FROM operation_records").fetchall(),
            )
            assert len(op_rows) == 1 and op_rows[0][1] == "succeeded"

        asyncio.run(check())
    finally:
        asyncio.run(state.close())
