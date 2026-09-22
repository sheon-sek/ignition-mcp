"""Slice 7 (Phase 3 / G3): D16 Project transaction machinery on a fake Gateway.

Every persisted state and every branch of the canonical protocol: no-op, commit,
backup-failure abort, conflict, each dispatch-boundary outcome reconciled to
COMMITTED / NOT_APPLIED / OUTCOME_UNKNOWN / RECOVERY_REQUIRED, NOT_SENT known
non-attempt with drift diagnosis, 4xx rejection, lock contention/capacity, the
process flock, Designer deny/warn/ignore, cancellation, and restart
reconciliation for each re-export branch — with intermediate-artifact cleanup
and retention-lock assertions. Test builders reshape ZIPs in memory; that is
test-only convenience, never a production pattern (D15).
"""

from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
import zipfile
from typing import Any

import pytest

from ignition_rest_mcp.artifacts.local import LocalArtifactStore, quotas_from_settings
from ignition_rest_mcp.auth import VerifiedPrincipal
from ignition_rest_mcp.audit.sink import Auditor, SqliteAuditSink
from ignition_rest_mcp.capabilities.registry import CapabilityRegistry
from ignition_rest_mcp.client.gateway import DispatchOutcome, WriteDispatchResult
from ignition_rest_mcp.config import Settings
from ignition_rest_mcp.errors import GatewayError
from ignition_rest_mcp.operation import OperationContext
from ignition_rest_mcp.observability.metrics import Metrics
from ignition_rest_mcp.projects.fingerprint import project_fingerprint
from ignition_rest_mcp.projects.identity import GatewayIdentity
from ignition_rest_mcp.projects.locks import ProcessWriterGuard, ProjectLockRegistry
from ignition_rest_mcp.projects.transactions import (
    CandidateBuilder,
    ProjectTransactionService,
    TransactionState,
)
from ignition_rest_mcp.storage.database import Storage
from ignition_rest_mcp.storage.records import OperationRecordStore
from test_config import _settings

PROJECT = "mcp-proj"


def _zip(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in entries.items():
            archive.writestr(zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0)), body)
    return buffer.getvalue()


BASE_BYTES = _zip({"project.json": b'{"title": "base"}'})
EDIT_BYTES = _zip({"project.json": b'{"title": "base"}', "mcp-touched.txt": b"changed"})


def _fingerprint_bytes(payload: bytes) -> str:
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".zip") as handle:
        handle.write(payload)
        handle.flush()
        return project_fingerprint(handle.name)


class FakeGateway:
    def __init__(self, *, project_bytes: bytes = BASE_BYTES) -> None:
        self.state = project_bytes
        self.export_calls = 0
        self.dispatch_calls: list[tuple[str, bytes]] = []
        self.dispatch_response: WriteDispatchResult = WriteDispatchResult(DispatchOutcome.RESPONDED, 200, {})
        self.designer_sessions: list[dict[str, Any]] = []
        self.fail_export_after: int | None = None
        self.ambiguous_lands = False

    async def gateway_info(self, context: Any = None) -> dict[str, str]:
        return {"ignitionVersion": "8.3.8"}

    async def healthy_modules(self, context: Any = None) -> dict[str, list[Any]]:
        return {"items": []}

    async def openapi(self) -> bytes:
        paths = {
            "/data/api/v1/gateway-info": {"get": {}},
            "/data/api/v1/projects/list": {"get": {}},
            "/data/api/v1/projects/export/{name}": {"get": {}},
            "/data/api/v1/designers": {"get": {}},
            "/data/api/v1/projects/import/{name}": {"post": {}},
        }
        return json.dumps({"paths": paths}).encode()

    async def get_json(self, path: str, *, params: Any = None, context: Any = None,
                       limit_bytes: int = 1_048_576) -> dict[str, Any]:
        if path == "/data/api/v1/projects/list":
            return {
                "items": [{"name": PROJECT}],
                "metadata": {"total": 1, "matching": 1,
                             "limit": int(params["limit"]), "offset": int(params["offset"])},
            }
        if path == "/data/api/v1/designers":
            count = len(self.designer_sessions)
            return {"items": list(self.designer_sessions),
                    "metadata": {"total": count, "matching": count, "limit": 100, "offset": 0}}
        raise AssertionError(f"unexpected GET {path}")

    async def stream_get_to(self, path: str, *, params: Any = None, sink: Any,
                            limit_bytes: int, deadline_seconds: float, context: Any = None) -> int:
        assert path == f"/data/api/v1/projects/export/{PROJECT}"
        self.export_calls += 1
        if self.fail_export_after is not None and self.export_calls >= self.fail_export_after:
            raise GatewayError("gateway_unavailable", "export stream failed")
        await sink.write(self.state)
        return len(self.state)

    async def dispatch_write(self, method: str, path: str, *, body_chunks: Any,
                             content_type: str, params: Any = None, deadline_seconds: float,
                             context: Any = None, phase_report: Any = None) -> WriteDispatchResult:
        assert method == "POST" and path == f"/data/api/v1/projects/import/{PROJECT}"
        assert params == {"overwrite": "true"} and content_type == "application/zip"
        body = b"".join([chunk async for chunk in body_chunks])
        self.dispatch_calls.append((path, body))
        response = self.dispatch_response
        landed = (
            response.outcome is DispatchOutcome.RESPONDED and 200 <= (response.status or 0) < 300
        ) or (
            response.outcome in {DispatchOutcome.SENT_PARTIAL, DispatchOutcome.SENT_COMPLETE_NO_RESPONSE}
            and self.ambiguous_lands
        )
        if landed:
            self.state = body
        return response


class _CopyBuilder(CandidateBuilder):
    """Re-writes the baseline verbatim (pcf1 equality despite new container bytes)."""

    def __init__(self, hook: Any = None) -> None:
        self.hook = hook

    async def build(self, baseline: Any, out: Any) -> None:
        chunks = await _drain(baseline)
        if self.hook is not None:
            await self.hook()
        source = zipfile.ZipFile(io.BytesIO(b"".join(chunks)))
        await out.write(_zip({info.filename: source.read(info.filename) for info in source.infolist()}))


class _EditBuilder(_CopyBuilder):
    async def build(self, baseline: Any, out: Any) -> None:
        chunks = await _drain(baseline)
        if self.hook is not None:
            await self.hook()
        source = zipfile.ZipFile(io.BytesIO(b"".join(chunks)))
        entries = {info.filename: source.read(info.filename) for info in source.infolist()}
        entries["mcp-touched.txt"] = b"changed"
        await out.write(_zip(entries))


class _JunkBuilder(CandidateBuilder):
    async def build(self, baseline: Any, out: Any) -> None:
        await _drain(baseline)
        await out.write(b"definitely not a zip")


class _RaisingBuilder(CandidateBuilder):
    async def build(self, baseline: Any, out: Any) -> None:
        await _drain(baseline)
        raise RuntimeError("builder exploded")


async def _drain(reader: Any) -> list[bytes]:
    chunks: list[bytes] = []
    while True:
        chunk = await reader.read_chunk()
        if chunk is None:
            return chunks
        chunks.append(chunk)


class _Env:
    def __init__(self, tmp_path: Path, gateway: FakeGateway, **overrides: Any) -> None:
        base: dict[str, Any] = {
            "project_writer_enabled": True, "gateway_id": "gw-1",
            "config_mutation_enabled": True, "mutation_operations": ("project_import",),
            "mutation_targets": {"project_import": (PROJECT,)},
        }
        base.update(overrides)
        self.settings: Settings = _settings(data_dir=str(tmp_path), **base)
        self.gateway = gateway
        self.tmp = tmp_path
        self.metrics = Metrics()

    async def start(self) -> None:
        self.storage = Storage(self.tmp)
        await self.storage.open()
        self.registry = CapabilityRegistry(self.gateway)  # type: ignore[arg-type]
        await self.registry.refresh()
        self.store = LocalArtifactStore(self.storage.state, self.tmp, quotas_from_settings(self.settings))
        self.store.prepare()
        self.records = OperationRecordStore(self.storage.state)
        self.sink = SqliteAuditSink(self.storage.audit)
        self.locks = ProjectLockRegistry(timeout_seconds=1.0, max_entries=8)
        self._rebuild_service()

    def _rebuild_service(self) -> None:
        self.service = ProjectTransactionService(
            client=self.gateway,  # type: ignore[arg-type]
            registry=self.registry, store=self.store, settings=self.settings,
            locks=self.locks, identity=GatewayIdentity(key="gw-1", derived=False),
            db=self.storage.state,
        )

    async def stop(self) -> None:
        await self.storage.close()

    def context(self) -> OperationContext:
        context = OperationContext.start("project_import", "jwt:configurator", "ARTIFACT")
        context.auditor = Auditor(self.sink, self.records, context, self.metrics)
        return context

    def principal(self) -> VerifiedPrincipal:
        return VerifiedPrincipal._mint(
            key="jwt:configurator", scopes=frozenset({"ignition.read", "ignition.config"}), auth_mode="jwt",
        )

    async def execute(self, builder: CandidateBuilder, *, project_name: str = PROJECT) -> Any:
        return await self.service.execute(
            project_name=project_name, builder=builder, context=self.context(), principal=self.principal(),
        )

    async def txn_rows(self) -> list[tuple[str, int, str | None, str]]:
        return list(await self.storage.state.run(
            lambda conn: conn.execute(
                "SELECT state, import_dispatched, error_code, transaction_id FROM project_transactions"
                " ORDER BY rowid"
            ).fetchall()
        ))

    async def audit_rows(self) -> list[tuple[str, str]]:
        return list(await self.storage.audit.run(
            lambda conn: conn.execute("SELECT phase, outcome FROM audit_log ORDER BY seq").fetchall()
        ))

    async def artifact_states(self) -> list[tuple[str, str, int]]:
        return list(await self.storage.state.run(
            lambda conn: conn.execute(
                "SELECT state, retention_class, retention_lock FROM artifacts ORDER BY artifact_id"
            ).fetchall()
        ))


def _run(coro: Any) -> Any:
    async def main() -> Any:
        return await coro

    return asyncio.run(main())


def _ready(tmp_path: Path, gateway: FakeGateway | None = None, **overrides: Any) -> _Env:
    env = _Env(tmp_path, gateway or FakeGateway(), **overrides)
    _run(env.start())
    return env


# --------------------------------------------------------------------------- outcomes


def test_no_change_returns_before_backup_or_import(tmp_path: Path) -> None:
    env = _ready(tmp_path)

    async def scenario() -> None:
        try:
            result = await env.execute(_CopyBuilder())
            assert result.state is TransactionState.NO_CHANGE
            assert result.import_dispatched is False  # the returned object, not just the row
            assert result.error is None
            assert env.gateway.dispatch_calls == []
            rows = await env.txn_rows()
            assert len(rows) == 1 and rows[0][0] == "NO_CHANGE"
            assert await env.artifact_states() == []  # EPHEMERALs cleaned, nothing promoted
            assert await env.audit_rows() == []  # executor never reached
        finally:
            await env.stop()

    _run(scenario())


def test_normal_edit_commits_releases_lock_and_cleans_intermediates(tmp_path: Path) -> None:
    env = _ready(tmp_path)

    async def scenario() -> None:
        try:
            result = await env.execute(_EditBuilder())
            assert result.state is TransactionState.COMMITTED
            assert len(env.gateway.dispatch_calls) == 1
            assert env.gateway.state == env.gateway.dispatch_calls[0][1]
            assert result.import_dispatched is True
            assert result.baseline_fingerprint != result.candidate_fingerprint
            states = await env.artifact_states()
            assert len(states) == 1 and states[0][1] == "RECOVERY" and states[0][2] == 0
            rows = await env.audit_rows()
            assert ("decision", "allowed") in rows and ("attempt", "attempted") in rows
            assert ("result", "completed") in rows
        finally:
            await env.stop()

    _run(scenario())


def test_backup_persistence_failure_aborts_pre_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = _ready(tmp_path)

    async def refuse(*args: Any, **kw: Any) -> None:
        raise GatewayError("internal_error", "recovery promotion refused")

    async def scenario() -> None:
        try:
            monkeypatch.setattr(env.store, "promote_recovery", refuse)
            result = await env.execute(_EditBuilder())
            assert result.state is TransactionState.FAILED_PRE_IMPORT
            assert result.import_dispatched is False  # G3 rehearsal caught the True default
            assert result.error is not None and result.error.code == "internal_error"
            assert env.gateway.dispatch_calls == []
            assert await env.artifact_states() == []
        finally:
            await env.stop()

    _run(scenario())


def test_external_change_between_A_and_A_prime_conflicts_without_import(tmp_path: Path) -> None:
    env = _ready(tmp_path)
    external = _zip({"project.json": b'{"title": "base"}', "evil.txt": b"external"})

    async def mutate_server() -> None:
        env.gateway.state = external

    async def scenario() -> None:
        try:
            result = await env.execute(_EditBuilder(hook=mutate_server))
            assert result.state is TransactionState.CONFLICTED
            assert result.import_dispatched is False
            assert env.gateway.dispatch_calls == []
            assert result.error is not None and result.error.code == "conflict"
            states = await env.artifact_states()
            assert len(states) == 1 and states[0][1] == "RECOVERY" and states[0][2] == 0
        finally:
            await env.stop()

    _run(scenario())


@pytest.mark.parametrize(
    ("dispatch", "ambiguous_lands", "drift_after", "expected_state", "lock_expected"),
    [
        (WriteDispatchResult(DispatchOutcome.SENT_COMPLETE_NO_RESPONSE), True, False, "COMMITTED", "released"),
        (WriteDispatchResult(DispatchOutcome.SENT_PARTIAL), False, False, "NOT_APPLIED", "released"),
        (WriteDispatchResult(DispatchOutcome.SENT_COMPLETE_NO_RESPONSE), False, True, "OUTCOME_UNKNOWN", "held"),
        (WriteDispatchResult(DispatchOutcome.RESPONDED, 500, {}), False, True, "OUTCOME_UNKNOWN", "held"),
        (WriteDispatchResult(DispatchOutcome.RESPONDED, 200, {}), False, True, "RECOVERY_REQUIRED", "held"),
    ],
)
def test_dispatch_boundaries_reconcile_per_d16(
    tmp_path: Path, dispatch: WriteDispatchResult, ambiguous_lands: bool, drift_after: bool,
    expected_state: str, lock_expected: str,
) -> None:
    gateway = FakeGateway()
    env = _ready(tmp_path, gateway)
    gateway.dispatch_response = dispatch
    gateway.ambiguous_lands = ambiguous_lands
    if drift_after:
        original = gateway.dispatch_write

        async def drifting(*args: Any, **kw: Any) -> WriteDispatchResult:
            response = await original(*args, **kw)
            gateway.state = _zip({"project.json": b"foreign drift", "extra.bin": b"x" * 40})
            return response

        gateway.dispatch_write = drifting  # type: ignore[assignment]

    async def scenario() -> None:
        try:
            result = await env.execute(_EditBuilder())
            assert result.state.value == expected_state
            assert len(gateway.dispatch_calls) == 1  # exactly once, under every boundary
            states = await env.artifact_states()
            recovery = [row for row in states if row[1] == "RECOVERY"]
            if lock_expected == "held":
                assert result.error is not None and result.error.code == "outcome_unknown"
                assert len(recovery) >= 2 and all(row[2] == 1 for row in recovery)
            else:
                assert recovery and all(row[2] == 0 for row in recovery)
        finally:
            await env.stop()

    _run(scenario())


def test_not_sent_is_known_non_attempt_without_drift(tmp_path: Path) -> None:
    gateway = FakeGateway()
    env = _ready(tmp_path, gateway)
    gateway.dispatch_response = WriteDispatchResult(
        DispatchOutcome.NOT_SENT, transport_error=GatewayError("gateway_unavailable", "unreachable"),
    )

    async def scenario() -> None:
        try:
            result = await env.execute(_EditBuilder())
            assert result.state is TransactionState.NOT_APPLIED
            assert result.import_dispatched is False
            assert result.external_drift_detected is False
            assert result.error is not None and result.error.code == "gateway_unavailable"
            assert len(gateway.dispatch_calls) == 1  # one dispatch *attempt* that never sent
            # The durable class a restart reads: nothing was sent, so no re-export may
            # ever credit this attempt with the Project's content.
            assert await _boundary(env, result.transaction_id) == "not_sent"
        finally:
            await env.stop()

    _run(scenario())


def test_not_sent_with_external_drift_is_reported(tmp_path: Path) -> None:
    gateway = FakeGateway()
    env = _ready(tmp_path, gateway)
    gateway.dispatch_response = WriteDispatchResult(
        DispatchOutcome.NOT_SENT, transport_error=GatewayError("gateway_unavailable", "unreachable"),
    )
    drift = _zip({"project.json": b"edited elsewhere"})
    exports = {"n": 0}
    original = gateway.stream_get_to

    async def drifting_export(*args: Any, **kw: Any) -> int:
        exports["n"] += 1
        if exports["n"] == 3:  # A, A', then the NOT_SENT drift diagnosis C
            gateway.state = drift
        return await original(*args, **kw)

    async def scenario() -> None:
        try:
            gateway.stream_get_to = drifting_export  # type: ignore[assignment]
            result = await env.execute(_EditBuilder())
            assert result.state is TransactionState.NOT_APPLIED
            assert result.import_dispatched is False
            assert result.external_drift_detected is True
        finally:
            await env.stop()

    _run(scenario())


def test_rejected_4xx_is_not_applied_and_never_retried(tmp_path: Path) -> None:
    gateway = FakeGateway()
    env = _ready(tmp_path, gateway)
    gateway.dispatch_response = WriteDispatchResult(DispatchOutcome.RESPONDED, 409, {"error": "conflict"})

    async def scenario() -> None:
        try:
            result = await env.execute(_EditBuilder())
            assert result.state is TransactionState.NOT_APPLIED
            assert result.error is not None and result.error.code == "conflict"
            assert len(gateway.dispatch_calls) == 1
            assert gateway.state == BASE_BYTES
            assert result.external_drift_detected is False
            # The frozen G3 operation does not make a refusal final, so its durable class
            # stays D16's attributable one: a re-export equal to the candidate B is a
            # recovered success for it, exactly as before Phase 4.
            assert await _boundary(env, result.transaction_id) == "attributable"
        finally:
            await env.stop()

    _run(scenario())


@pytest.mark.parametrize("builder_cls", [_JunkBuilder, _RaisingBuilder])
def test_bad_or_failed_candidate_never_dispatches(tmp_path: Path, builder_cls: Any) -> None:
    env = _ready(tmp_path)

    async def scenario() -> None:
        try:
            if builder_cls is _JunkBuilder:
                result = await env.execute(builder_cls())
                assert result.state is TransactionState.FAILED_PRE_IMPORT
            else:
                with pytest.raises(RuntimeError):
                    await env.execute(builder_cls())
            assert env.gateway.dispatch_calls == []
            states = await env.artifact_states()
            # any surviving artifact is either released or gone; none locked
            assert all(row[2] == 0 for row in states)
        finally:
            await env.stop()

    _run(scenario())


# --------------------------------------------------------------------------- gates, locks, flock


def test_writer_disabled_fails_closed(tmp_path: Path) -> None:
    env = _ready(tmp_path, project_writer_enabled=False, gateway_id="")

    async def scenario() -> None:
        try:
            with pytest.raises(GatewayError) as captured:
                await env.execute(_EditBuilder())
            assert captured.value.code == "operation_disabled"
            assert await env.txn_rows() == []
        finally:
            await env.stop()

    _run(scenario())


def test_exact_case_sensitive_name_canonicalization(tmp_path: Path) -> None:
    env = _ready(tmp_path)

    async def scenario() -> None:
        try:
            with pytest.raises(GatewayError) as captured:
                await env.execute(_EditBuilder(), project_name="MCP-PROJ")
            assert captured.value.code == "not_found"
            assert env.gateway.export_calls == 0
        finally:
            await env.stop()

    _run(scenario())


def test_lock_contention_conflicts_before_any_row(tmp_path: Path) -> None:
    env = _ready(tmp_path)

    async def scenario() -> None:
        try:
            async with env.locks.acquire("gw-1", PROJECT):
                with pytest.raises(GatewayError) as captured:
                    await env.execute(_EditBuilder())
                assert captured.value.code == "conflict"
                assert await env.txn_rows() == []
        finally:
            await env.stop()

    _run(scenario())


def test_lock_registry_capacity_fails_closed(tmp_path: Path) -> None:
    env = _ready(tmp_path)
    env.locks = ProjectLockRegistry(timeout_seconds=0.5, max_entries=2)
    env._rebuild_service()

    async def scenario() -> None:
        try:
            async with env.locks.acquire("gw-1", "other-a"), env.locks.acquire("gw-1", "other-b"):
                with pytest.raises(GatewayError) as captured:
                    await env.execute(_EditBuilder())
                assert captured.value.code == "limit_exceeded"
        finally:
            await env.stop()

    _run(scenario())


def test_process_flock_is_exclusive_and_private(tmp_path: Path) -> None:
    first = ProcessWriterGuard(tmp_path)
    second = ProcessWriterGuard(tmp_path)
    _run(first.acquire())
    try:
        with pytest.raises(GatewayError) as captured:
            _run(second.acquire())
        assert captured.value.code == "conflict"
        assert oct((tmp_path / "project-writer.lock").stat().st_mode & 0o777) == "0o600"
    finally:
        _run(first.release())
    _run(second.acquire())
    _run(second.release())


def test_single_writer_limitation_text_documents_operator_obligation() -> None:
    from ignition_rest_mcp.projects.locks import SINGLE_WRITER_LIMITATION

    assert "operator" in SINGLE_WRITER_LIMITATION


# --------------------------------------------------------------------------- designer policy


def test_designer_deny_aborts_without_dispatch(tmp_path: Path) -> None:
    gateway = FakeGateway()
    gateway.designer_sessions = [{"id": "d1", "user": "engineer", "project": PROJECT}]
    env = _ready(tmp_path, gateway)

    async def scenario() -> None:
        try:
            result = await env.execute(_EditBuilder())
            assert result.state is TransactionState.FAILED_PRE_IMPORT
            assert result.error is not None and result.error.code == "conflict"
            assert gateway.dispatch_calls == []
            rows = await env.txn_rows()
            assert rows[0][0] == "FAILED_PRE_IMPORT"
        finally:
            await env.stop()

    _run(scenario())


def test_designer_unknown_shape_fails_closed(tmp_path: Path) -> None:
    gateway = FakeGateway()
    gateway.designer_sessions = [{"weird": 1}]  # no project field is fine shape-wise;
    # shape violations come from the payload itself:
    original = gateway.get_json

    async def garbage(path: str, **kw: Any) -> dict[str, Any]:
        if path == "/data/api/v1/designers":
            return {"nope": True}
        return await original(path, **kw)

    gateway.get_json = garbage  # type: ignore[assignment]
    env = _ready(tmp_path, gateway)

    async def scenario() -> None:
        try:
            result = await env.execute(_EditBuilder())
            assert result.state is TransactionState.FAILED_PRE_IMPORT
            assert result.error is not None and result.error.code == "schema_mismatch"
        finally:
            await env.stop()

    _run(scenario())


def test_designer_warn_proceeds_and_flags(tmp_path: Path) -> None:
    gateway = FakeGateway()
    gateway.designer_sessions = [{"id": "d1", "user": "engineer", "project": PROJECT}]
    env = _ready(tmp_path, gateway, project_designer_policy="warn")

    async def scenario() -> None:
        try:
            result = await env.execute(_EditBuilder())
            assert result.state is TransactionState.COMMITTED
            assert result.designer_warning is True
        finally:
            await env.stop()

    _run(scenario())


def test_designer_ignore_skips_the_endpoint(tmp_path: Path) -> None:
    gateway = FakeGateway()
    calls: list[str] = []
    original = gateway.get_json

    async def tracked(path: str, **kw: Any) -> dict[str, Any]:
        calls.append(path)
        return await original(path, **kw)

    gateway.get_json = tracked  # type: ignore[assignment]
    env = _ready(tmp_path, gateway, project_designer_policy="ignore")

    async def scenario() -> None:
        try:
            result = await env.execute(_EditBuilder())
            assert result.state is TransactionState.COMMITTED
            assert "/data/api/v1/designers" not in calls
        finally:
            await env.stop()

    _run(scenario())


# --------------------------------------------------------------------------- restart reconciliation


async def _seed(
    env: _Env, state: TransactionState, *, candidate_bytes: bytes,
    baseline_fp: str | None = None, boundary: str | None = None,
    error_code: str | None = None,
) -> str:
    a_writer = await env.store.create(
        kind="project_export", sensitivity="CONFIDENTIAL", retention_class="EPHEMERAL",
        owner="jwt:configurator", filename="a.zip", media_type="application/zip", correlation_id="c",
    )
    await a_writer.write(BASE_BYTES)
    artifact_a = await env.store.publish(a_writer)
    baseline_fp = baseline_fp or _fingerprint_bytes(BASE_BYTES)
    await env.store.promote_recovery(artifact_a.artifact_id, "txn-seed")
    b_writer = await env.store.create(
        kind="project_archive", sensitivity="CONFIDENTIAL", retention_class="EPHEMERAL",
        owner="jwt:configurator", filename="b.zip", media_type="application/zip", correlation_id="c",
    )
    await b_writer.write(candidate_bytes)
    artifact_b = await env.store.publish(b_writer)
    txn_id = "txn-seed"
    await env.service._insert(txn_id, env.context(), PROJECT)  # type: ignore[attr-defined]
    await env.service._update(  # type: ignore[attr-defined]
        txn_id, state,
        baseline_artifact=artifact_a.artifact_id, baseline_fingerprint=baseline_fp,
        candidate_artifact=artifact_b.artifact_id, candidate_fingerprint=_fingerprint_bytes(candidate_bytes),
        # a persisted IMPORT_SENT/VERIFYING row implies the pre-dispatch persist ran
        import_dispatched=state in {TransactionState.IMPORT_SENT, TransactionState.VERIFYING},
        # D16 restart reconciliation reads the durable dispatch classification: a row
        # written before Phase 4 has none and is reconciled by the read-only comparison.
        dispatch_boundary=boundary, error_code=error_code,
    )
    return txn_id


async def _row(env: _Env, txn_id: str) -> tuple[str, int, str | None]:
    row = await env.storage.state.run(
        lambda conn: conn.execute(
            "SELECT state, import_dispatched, error_code FROM project_transactions WHERE transaction_id = ?",
            (txn_id,),
        ).fetchone()
    )
    return str(row[0]), int(row[1]), None if row[2] is None else str(row[2])


async def _boundary(env: _Env, txn_id: str) -> str | None:
    """The durable dispatch class one row carries (the restart reconciliation input)."""

    row = await env.storage.state.run(
        lambda conn: conn.execute(
            "SELECT dispatch_boundary FROM project_transactions WHERE transaction_id = ?",
            (txn_id,),
        ).fetchone()
    )
    return None if row[0] is None else str(row[0])


def test_restart_pre_import_becomes_failed_pre_import(tmp_path: Path) -> None:
    env = _ready(tmp_path)

    async def scenario() -> None:
        try:
            txn_id = await _seed(env, TransactionState.BASELINE_CAPTURED, candidate_bytes=EDIT_BYTES)
            assert await env.service.reconcile_interrupted(batch=10, per_txn_seconds=10) == 1
            state, dispatched, error = await _row(env, txn_id)
            assert state == "FAILED_PRE_IMPORT" and dispatched == 0 and error == "interrupted"
            assert env.gateway.dispatch_calls == []  # reconciliation never re-imports
        finally:
            await env.stop()

    _run(scenario())


def test_restart_verifying_c_equals_b_recovers_committed(tmp_path: Path) -> None:
    gateway = FakeGateway(project_bytes=EDIT_BYTES)  # the import had in fact landed
    env = _ready(tmp_path, gateway)

    async def scenario() -> None:
        try:
            txn_id = await _seed(env, TransactionState.VERIFYING, candidate_bytes=EDIT_BYTES)
            assert await env.service.reconcile_interrupted(batch=5, per_txn_seconds=10) == 1
            state, dispatched, _ = await _row(env, txn_id)
            assert state == "COMMITTED" and dispatched == 1
            assert gateway.dispatch_calls == []
            states = await env.artifact_states()
            assert states == [] or all(row[2] == 0 for row in states)
        finally:
            await env.stop()

    _run(scenario())


def test_restart_import_sent_c_equals_a_is_not_applied(tmp_path: Path) -> None:
    env = _ready(tmp_path)  # gateway still serves BASE_BYTES

    async def scenario() -> None:
        try:
            txn_id = await _seed(env, TransactionState.IMPORT_SENT, candidate_bytes=EDIT_BYTES)
            assert await env.service.reconcile_interrupted(batch=5, per_txn_seconds=10) == 1
            state, _, error = await _row(env, txn_id)
            assert state == "NOT_APPLIED" and error == "conflict"
        finally:
            await env.stop()

    _run(scenario())


def test_restart_mismatch_verifying_is_recovery_required(tmp_path: Path) -> None:
    drifted = _zip({"project.json": b"drifted", "x": b"y"})
    env = _ready(tmp_path, FakeGateway(project_bytes=drifted))

    async def scenario() -> None:
        try:
            txn_id = await _seed(env, TransactionState.VERIFYING, candidate_bytes=EDIT_BYTES,
                                 baseline_fp="pcf1:" + "a" * 64)
            await env.service.reconcile_interrupted(batch=5, per_txn_seconds=10)
            state, _, error = await _row(env, txn_id)
            assert state == "RECOVERY_REQUIRED" and error == "outcome_unknown"
            states = await env.artifact_states()
            recovery = [row for row in states if row[1] == "RECOVERY"]
            assert len(recovery) >= 2 and all(row[2] == 1 for row in recovery)
        finally:
            await env.stop()

    _run(scenario())


def test_restart_mismatch_import_sent_is_outcome_unknown(tmp_path: Path) -> None:
    drifted = _zip({"project.json": b"drifted", "x": b"y"})
    env = _ready(tmp_path, FakeGateway(project_bytes=drifted))

    async def scenario() -> None:
        try:
            txn_id = await _seed(env, TransactionState.IMPORT_SENT, candidate_bytes=EDIT_BYTES,
                                 baseline_fp="pcf1:" + "a" * 64)
            await env.service.reconcile_interrupted(batch=5, per_txn_seconds=10)
            state, _, error = await _row(env, txn_id)
            assert state == "OUTCOME_UNKNOWN" and error == "outcome_unknown"
        finally:
            await env.stop()

    _run(scenario())


def test_restart_when_reexport_fails_is_outcome_unknown(tmp_path: Path) -> None:
    gateway = FakeGateway()
    gateway.fail_export_after = 1
    env = _ready(tmp_path, gateway)

    async def scenario() -> None:
        try:
            txn_id = await _seed(env, TransactionState.IMPORT_SENT, candidate_bytes=EDIT_BYTES,
                                 baseline_fp="pcf1:" + "a" * 64)
            await env.service.reconcile_interrupted(batch=5, per_txn_seconds=10)
            state, _, error = await _row(env, txn_id)
            assert state == "OUTCOME_UNKNOWN" and error == "outcome_unknown"
        finally:
            await env.stop()

    _run(scenario())


def test_restart_refused_dispatch_is_not_applied_without_a_read_back(tmp_path: Path) -> None:
    """D30 §2: a refusal the Gateway already gave is final, so the restarted process must
    not export the Project at all. A competing writer can hold the very candidate B this
    attempt asked for (the Gateway here serves exactly that), and no read-back can tell
    that apart from this call's own success."""

    gateway = FakeGateway(project_bytes=EDIT_BYTES)
    env = _ready(tmp_path, gateway)

    async def scenario() -> None:
        try:
            txn_id = await _seed(env, TransactionState.IMPORT_SENT, candidate_bytes=EDIT_BYTES,
                                 boundary="refused", error_code="conflict")
            assert await env.service.reconcile_interrupted(batch=5, per_txn_seconds=10) == 1
            state, dispatched, error = await _row(env, txn_id)
            assert state == "NOT_APPLIED" and error == "conflict" and dispatched == 1
            assert gateway.export_calls == 0
        finally:
            await env.stop()

    _run(scenario())


def test_restart_unrecorded_answer_is_never_a_recovered_success(tmp_path: Path) -> None:
    """The crash window *inside* the dispatch: the Gateway may have answered — a refusal
    is indistinguishable from an ambiguous boundary when nothing was written down — so a
    re-export that equals the candidate B may not be claimed as this attempt's success.
    The transaction reports OUTCOME_UNKNOWN and keeps the recovery snapshot."""

    gateway = FakeGateway(project_bytes=EDIT_BYTES)
    env = _ready(tmp_path, gateway)

    async def scenario() -> None:
        try:
            txn_id = await _seed(env, TransactionState.IMPORT_SENT, candidate_bytes=EDIT_BYTES,
                                 boundary="unattributable")
            assert await env.service.reconcile_interrupted(batch=5, per_txn_seconds=10) == 1
            state, _, error = await _row(env, txn_id)
            assert state == "OUTCOME_UNKNOWN" and error == "outcome_unknown"
            assert gateway.export_calls == 1  # the read-back happened; it just proves nothing
            recovery = [row for row in await env.artifact_states() if row[1] == "RECOVERY"]
            assert recovery and all(row[2] == 1 for row in recovery)
        finally:
            await env.stop()

    _run(scenario())


def test_restart_not_sent_dispatch_is_not_applied_without_a_read_back(tmp_path: Path) -> None:
    """A recorded known non-attempt needs no read-back either: nothing left the process,
    so the Project showing the candidate is another writer's work."""

    gateway = FakeGateway(project_bytes=EDIT_BYTES)
    env = _ready(tmp_path, gateway)

    async def scenario() -> None:
        try:
            txn_id = await _seed(env, TransactionState.IMPORT_SENT, candidate_bytes=EDIT_BYTES,
                                 boundary="not_sent", error_code="gateway_unavailable")
            assert await env.service.reconcile_interrupted(batch=5, per_txn_seconds=10) == 1
            state, dispatched, error = await _row(env, txn_id)
            assert state == "NOT_APPLIED" and error == "gateway_unavailable" and dispatched == 0
            assert gateway.export_calls == 0
        finally:
            await env.stop()

    _run(scenario())


def test_restart_claimed_success_is_committed_when_the_candidate_is_there(tmp_path: Path) -> None:
    """A recorded claim is confirmed the way D16 confirms one: C == B."""

    gateway = FakeGateway(project_bytes=EDIT_BYTES)
    env = _ready(tmp_path, gateway)

    async def scenario() -> None:
        try:
            txn_id = await _seed(env, TransactionState.IMPORT_SENT, candidate_bytes=EDIT_BYTES,
                                 boundary="claimed")
            assert await env.service.reconcile_interrupted(batch=5, per_txn_seconds=10) == 1
            state, dispatched, _ = await _row(env, txn_id)
            assert state == "COMMITTED" and dispatched == 1
        finally:
            await env.stop()

    _run(scenario())


def test_restart_claimed_success_without_the_candidate_is_recovery_required(tmp_path: Path) -> None:
    """The Gateway claimed the import and nothing landed: that is recovery_required, not
    "not applied" — the claim is unconfirmed, so the snapshot stays locked."""

    env = _ready(tmp_path)  # the Gateway still serves the baseline

    async def scenario() -> None:
        try:
            txn_id = await _seed(env, TransactionState.IMPORT_SENT, candidate_bytes=EDIT_BYTES,
                                 boundary="claimed")
            assert await env.service.reconcile_interrupted(batch=5, per_txn_seconds=10) == 1
            state, _, error = await _row(env, txn_id)
            assert state == "RECOVERY_REQUIRED" and error == "outcome_unknown"
        finally:
            await env.stop()

    _run(scenario())


def test_reconcile_defers_while_another_writer_holds_the_project(tmp_path: Path) -> None:
    env = _ready(tmp_path)

    async def scenario() -> None:
        try:
            txn_id = await _seed(env, TransactionState.IMPORT_SENT, candidate_bytes=EDIT_BYTES,
                                 baseline_fp="pcf1:" + "a" * 64)
            env.locks = ProjectLockRegistry(timeout_seconds=0.05, max_entries=8)
            env._rebuild_service()
            async with env.locks.acquire("gw-1", PROJECT):
                processed = await env.service.reconcile_interrupted(batch=5, per_txn_seconds=5)
            assert processed == 1
            state, _, _ = await _row(env, txn_id)
            assert state == "IMPORT_SENT"  # deferred, retried on a later pass
        finally:
            await env.stop()

    _run(scenario())


# --------------------------------------------------------------------------- cancellation


def test_cancellation_mid_build_leaves_persisted_truth(tmp_path: Path) -> None:
    env = _ready(tmp_path)

    class CancellingBuilder(CandidateBuilder):
        async def build(self, baseline: Any, out: Any) -> None:
            await _drain(baseline)
            raise asyncio.CancelledError

    async def scenario() -> None:
        try:
            with pytest.raises(asyncio.CancelledError):
                await env.execute(CancellingBuilder())
            assert env.gateway.dispatch_calls == []
            rows = await env.txn_rows()
            txn_id = rows[0][3]
            assert rows[0][0] == "BASELINE_CAPTURED"  # persisted truth for restart
            await env.service.reconcile_interrupted(batch=5, per_txn_seconds=10)
            state, dispatched, _ = await _row(env, txn_id)
            assert state == "FAILED_PRE_IMPORT" and dispatched == 0
            assert await env.artifact_states() == []
        finally:
            await env.stop()

    _run(scenario())
