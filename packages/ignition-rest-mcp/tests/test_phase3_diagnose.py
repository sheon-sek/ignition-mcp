"""Slice 8 (Phase 3 / G3): artifact_list, artifact_info, operation_diagnose,
gateway_diagnose storage fields and the low-cardinality metric additions."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastmcp import Client
from dataplane_fixtures import make_zip_bytes

import ignition_rest_mcp.server as server_module
from ignition_rest_mcp.artifacts.local import LocalArtifactStore, quotas_from_settings
from ignition_rest_mcp.auth import Principal
from ignition_rest_mcp.client.gateway import GatewayClient
from ignition_rest_mcp.errors import GatewayError
from ignition_rest_mcp.operation import uuid7
from ignition_rest_mcp.services.artifacts import artifact_info, operation_diagnose
from ignition_rest_mcp.storage.database import Database
from ignition_rest_mcp.storage.records import OperationRecordStore
from ignition_rest_mcp.storage.schema import AUDIT_DDL, STATE_DDL
from test_config import _settings


def _run(coro: Any) -> Any:
    async def main() -> Any:
        return await coro

    return asyncio.run(main())


def _store(tmp_path: Path) -> tuple[Database, LocalArtifactStore]:
    db = Database("state", tmp_path / "state.db", STATE_DDL)
    _run(db.open())
    store = LocalArtifactStore(db, tmp_path, quotas_from_settings(_settings(data_dir=str(tmp_path))))
    return db, store


def _publish(tmp_path: Path, *, owner: str, sensitivity: str = "CONFIDENTIAL",
             kind: str = "project_export", filename: str = "p.zip") -> dict[str, Any]:
    db, store = _store(tmp_path)

    async def go() -> dict[str, Any]:
        writer = await store.create(
            kind=kind, sensitivity=sensitivity, retention_class="EXPORT", owner=owner,
            filename=filename, media_type="application/zip", correlation_id="fixture",
        )
        await writer.write(make_zip_bytes())
        artifact = await store.publish(writer)
        await db.close()
        return artifact.to_ref()

    return _run(go())


@pytest.fixture
def stub_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    async def info(self: GatewayClient, context: Any = None) -> dict[str, str]:
        return {"ignitionVersion": "8.3.8"}

    async def modules(self: GatewayClient, context: Any = None) -> dict[str, list[Any]]:
        return {"items": []}

    async def openapi(self: GatewayClient) -> bytes:
        paths = {"/data/api/v1/gateway-info": {"get": {}}, "/data/api/v1/projects/list": {"get": {}}}
        return json.dumps({"paths": paths}).encode()

    async def get_json(self: GatewayClient, path: str, **kw: Any) -> dict[str, Any]:
        return {"items": [], "metadata": {"total": 0, "matching": 0, "limit": 100, "offset": 0}}

    monkeypatch.setattr(GatewayClient, "gateway_info", info)
    monkeypatch.setattr(GatewayClient, "healthy_modules", modules)
    monkeypatch.setattr(GatewayClient, "openapi", openapi)
    monkeypatch.setattr(GatewayClient, "get_json", get_json)


class _Live:
    """create_server with a live lifespan and an in-memory MCP client."""

    def __init__(self, tmp_path: Path, **overrides: Any) -> None:
        self.settings = _settings(
            data_dir=str(tmp_path), watcher_interval_seconds=3600.0,
            retention_interval_seconds=3600.0, storage_probe_interval_seconds=3600.0,
            artifact_cleanup_interval_seconds=3600.0, **overrides,
        )

    async def __aenter__(self) -> "Any":
        self.server = server_module.create_server(self.settings)
        self.mcp = Client(self.server)
        await self.mcp.__aenter__()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.mcp.__aexit__(None, None, None)


# --------------------------------------------------------------------------- artifact tools


def test_artifact_list_pagination_and_principal_scope(tmp_path: Path, stub_gateway: None) -> None:
    _publish(tmp_path, owner="none:test")
    _publish(tmp_path, owner="none:test", kind="tag_config_export", filename="t.json")

    async def scenario() -> None:
        async with _Live(tmp_path) as live:
            result = await live.mcp.call_tool("artifact_list", {"limit": 1, "offset": 0})
            assert not result.is_error
            page = result.data
            assert len(page.items) == 1
            assert page.page.matching == 2 and page.page.total == 2
            assert page.page.nextOffset == 1
            second = await live.mcp.call_tool("artifact_list", {"limit": 10, "offset": 1})
            assert second.data.page.nextOffset is None
            assert second.data.items[0].artifactId != page.items[0].artifactId
            kinded = await live.mcp.call_tool("artifact_list", {"kind": "tag_config_export"})
            assert kinded.data.page.matching == 1
            bad = await live.mcp.call_tool("artifact_list", {"kind": "gateway_backup"}, raise_on_error=False)
            assert bad.is_error and json.loads(bad.content[0].text)["code"] == "invalid_argument"

    _run(scenario())


def test_artifact_list_hides_foreign_records_without_admin(tmp_path: Path, stub_gateway: None) -> None:
    _publish(tmp_path, owner="jwt:someone-else")

    async def scenario() -> None:
        async with _Live(tmp_path) as live:
            result = await live.mcp.call_tool("artifact_list", {})
            assert result.data.items == [] and result.data.page.matching == 0

    _run(scenario())


def test_artifact_info_states_and_visibility(tmp_path: Path, stub_gateway: None) -> None:
    mine = _publish(tmp_path, owner="none:test")

    async def scenario() -> None:
        async with _Live(tmp_path) as live:
            ok = await live.mcp.call_tool("artifact_info", {"artifactId": mine["artifactId"]})
            assert not ok.is_error
            assert ok.data.artifact.artifactId == mine["artifactId"]
            missing = await live.mcp.call_tool("artifact_info", {"artifactId": uuid7()}, raise_on_error=False)
            assert json.loads(missing.content[0].text)["code"] == "not_found"
            bad = await live.mcp.call_tool("artifact_info", {"artifactId": "../etc"}, raise_on_error=False)
            assert json.loads(bad.content[0].text)["code"] == "invalid_argument"

    _run(scenario())


def test_restricted_artifact_info_is_audited_and_fail_closed(tmp_path: Path) -> None:
    from ignition_rest_mcp.operation import OperationContext

    ref = _publish(tmp_path, owner="none:test", sensitivity="RESTRICTED")

    async def scenario() -> None:
        db = Database("state", tmp_path / "state.db", STATE_DDL)
        await db.open()
        store = LocalArtifactStore(db, tmp_path, quotas_from_settings(_settings(data_dir=str(tmp_path))))
        audit_db = Database("audit", tmp_path / "audit.db", AUDIT_DDL)
        await audit_db.open()
        from ignition_rest_mcp.audit.sink import SqliteAuditSink
        from ignition_rest_mcp.observability.metrics import Metrics

        sink = SqliteAuditSink(audit_db)
        metrics = Metrics()
        principal = Principal(key="none:test", scopes=frozenset({"ignition.read"}), auth_mode="none")
        context = OperationContext.start("artifact_info", "none:test", "FAST")
        info = await artifact_info(store, sink, context, principal=principal, artifact_id=ref["artifactId"],
                                   metrics=metrics)
        assert info.artifact.sensitivity == "RESTRICTED"
        rows = await audit_db.run(
            lambda conn: conn.execute("SELECT phase, outcome, tool FROM audit_log").fetchall()
        )
        assert [ (r[0], r[2]) for r in rows ] == [("attempt", "artifact_info_restricted")]

        # fail-closed when the audit sink cannot persist
        audit_db._healthy = False
        with pytest.raises(GatewayError) as captured:
            await artifact_info(store, sink, context, principal=principal, artifact_id=ref["artifactId"],
                                metrics=metrics)
        assert captured.value.code == "internal_error"
        assert metrics.audit_write_failures["attempt"] == 1
        await db.close()
        await audit_db.close()

    _run(scenario())


# --------------------------------------------------------------------------- operation_diagnose


def test_operation_diagnose_round_trip(tmp_path: Path, stub_gateway: None) -> None:
    async def scenario() -> None:
        async with _Live(tmp_path) as live:
            before = await live.mcp.call_tool("project_list", {})
            assert not before.is_error
            correlation = before.data.correlationId
            diagnosis = await live.mcp.call_tool("operation_diagnose", {"correlationId": correlation})
            assert not diagnosis.is_error, diagnosis.content
            payload = diagnosis.data
            assert payload.tool == "project_list"
            assert payload.outcome == "succeeded" and payload.errorCode is None
            assert payload.startedAt and payload.finishedAt
            assert payload.transactionId is None and payload.auditResultMissing is False
            unknown = await live.mcp.call_tool("operation_diagnose", {"correlationId": uuid7()},
                raise_on_error=False,
            )
            assert json.loads(unknown.content[0].text)["code"] == "not_found"
            malformed = await live.mcp.call_tool(
                "operation_diagnose", {"correlationId": "0e7f5c2a-1b2c-4d3e-8f90-112233445566"},
                raise_on_error=False,
            )
            assert json.loads(malformed.content[0].text)["code"] == "invalid_argument"

    _run(scenario())


def test_operation_diagnose_error_codes_are_stable(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = Database("state", tmp_path / "state.db", STATE_DDL)
        await db.open()
        records = OperationRecordStore(db)
        from ignition_rest_mcp.operation import OperationContext

        context = OperationContext.start("project_list", "none:test", "FAST")
        await records.start(context)
        await records.append_phase(context.correlation_id, "gateway-call")
        await records.finish(context.correlation_id, "failed", "upstream_error")
        principal = Principal(key="none:test", scopes=frozenset({"ignition.read"}), auth_mode="none")
        result = await operation_diagnose(
            records, OperationContext.start("operation_diagnose", "none:test", "FAST"),
            principal=principal, correlation_id=context.correlation_id,
        )
        assert result.outcome == "failed" and result.errorCode == "upstream_error"
        assert [phase.name for phase in result.phases] == ["gateway-call"]
        # foreign principal without admin cannot see it
        other = Principal(key="jwt:intruder", scopes=frozenset({"ignition.read"}), auth_mode="jwt")
        with pytest.raises(GatewayError) as denied:
            await operation_diagnose(
                records, OperationContext.start("operation_diagnose", "jwt:intruder", "FAST"),
                principal=other, correlation_id=context.correlation_id,
            )
        assert denied.value.code == "not_found"
        admin = Principal(key="jwt:admin", scopes=frozenset({"ignition.read", "ignition.admin"}), auth_mode="jwt")
        seen = await operation_diagnose(
            records, OperationContext.start("operation_diagnose", "jwt:admin", "FAST"),
            principal=admin, correlation_id=context.correlation_id,
        )
        assert seen.tool == "project_list"
        await db.close()

    _run(scenario())


def test_interrupted_record_is_diagnosable(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = Database("state", tmp_path / "state.db", STATE_DDL)
        await db.open()
        records = OperationRecordStore(db)
        from ignition_rest_mcp.operation import OperationContext

        context = OperationContext.start("project_export", "none:test", "ARTIFACT")
        await records.start(context)
        assert await records.mark_interrupted_stale(0.001) == 0  # fresh rows survive the sweep
        await db.run(
            lambda conn: conn.execute(
                "UPDATE operation_records SET started_at = '2000-01-01T00:00:00.000000Z' WHERE correlation_id = ?",
                (context.correlation_id,),
            )
        )
        assert await records.mark_interrupted_stale(300.0) == 1
        principal = Principal(key="none:test", scopes=frozenset({"ignition.read"}), auth_mode="none")
        result = await operation_diagnose(
            records, OperationContext.start("operation_diagnose", "none:test", "FAST"),
            principal=principal, correlation_id=context.correlation_id,
        )
        assert result.outcome == "interrupted" and result.finishedAt is not None
        await db.close()

    _run(scenario())


# --------------------------------------------------------------------------- diagnostics + metrics


def test_gateway_diagnose_reports_storage_subsystems(tmp_path: Path, stub_gateway: None) -> None:
    _publish(tmp_path, owner="none:test")

    async def scenario() -> None:
        async with _Live(tmp_path) as live:
            result = await live.mcp.call_tool("gateway_diagnose", {})
            storage = result.data.storage
            assert storage is not None
            assert storage.dataDirectoryConfigured is True
            assert storage.stateHealthy is True and storage.auditHealthy is True
            assert storage.artifactsReady == 1
            assert storage.projectWriterEnabled is False
            assert "operator" in storage.singleWriterLimitation

    _run(scenario())


def test_storage_degradation_flips_readiness_but_keeps_live(
    tmp_path: Path, stub_gateway: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _publish(tmp_path, owner="none:test", sensitivity="RESTRICTED", filename="r.zip")
    opened: dict[str, Database] = {}
    original_open = Database.open

    async def capture_open(self: Database) -> None:
        await original_open(self)
        opened[self.name] = self

    monkeypatch.setattr(Database, "open", capture_open)

    async def scenario() -> None:
        async with _Live(tmp_path) as live:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=live.server.http_app()), base_url="http://test",
            ) as http:
                ok = await http.get("/health/ready")
                assert ok.status_code == 200 and ok.json()["storageReady"] is True
                metrics = await http.get("/metrics")
                assert "ignition_mcp_artifacts_ready 1" in metrics.text
                assert "ignition_mcp_artifact_bytes_total" in metrics.text
                assert 'subsystem="state"' in metrics.text and 'subsystem="audit"' in metrics.text

                # Simulate the running audit subsystem dying: the next audited
                # write must fail closed and readiness must flip (inventory does not).
                opened["audit"]._healthy = False
                listing = await live.mcp.call_tool("artifact_list", {"kind": ""})
                restricted_id = next(
                    item.artifactId for item in listing.data.items if item.sensitivity == "RESTRICTED"
                )
                info = await live.mcp.call_tool(
                    "artifact_info", {"artifactId": restricted_id}, raise_on_error=False,
                )
                assert info.is_error  # internal_error, fail-closed
                assert json.loads(info.content[0].text)["code"] == "internal_error"
                ready = await http.get("/health/ready")
                assert ready.status_code == 503 and ready.json()["storageReady"] is False
                assert any(
                    item["name"] == "audit" and not item["healthy"] for item in ready.json()["subsystems"]
                )
                live_body = await http.get("/health/live")
                assert live_body.status_code == 200  # liveness is untouched
                gauge = await http.get("/metrics")
                assert 'subsystem="audit"} 0' in gauge.text
                # ordinary Gateway reads continue despite the failing audit plane
                reads = await live.mcp.call_tool("project_list", {})
                assert not reads.is_error
                diagnose = await live.mcp.call_tool("gateway_diagnose", {})
                assert diagnose.data.storage is not None
                assert diagnose.data.storage.auditHealthy is False

    _run(scenario())


def test_transaction_terminal_counter_is_low_cardinality(tmp_path: Path) -> None:
    from ignition_rest_mcp.observability.metrics import Metrics

    metrics = Metrics()
    metrics.record_transaction_terminal("COMMITTED")
    metrics.record_transaction_terminal("COMMITTED")
    metrics.record_artifact_cleanup(deleted=3, orphans_removed=2)
    rendered = metrics.render()
    assert 'ignition_mcp_project_transactions_terminal_total{state="COMMITTED"} 2' in rendered
    assert "ignition_mcp_artifact_cleanup_deleted_total 3" in rendered
    assert "ignition_mcp_artifact_orphans_removed_total 2" in rendered
