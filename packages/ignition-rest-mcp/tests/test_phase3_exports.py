"""Slice 5 (Phase 3 / G3): Gateway streamed download + project_export +
tag_config_export. Bounds, error mapping, audit ordering and gate behavior."""

from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
import re
import zipfile
from typing import Any

import httpx
import pytest
from fastmcp import Client

import ignition_rest_mcp.server as server_module
from ignition_rest_mcp.artifacts.local import LocalArtifactStore, quotas_from_settings
from ignition_rest_mcp.audit.sink import Auditor, SqliteAuditSink
from ignition_rest_mcp.capabilities.registry import CapabilityRegistry
from ignition_rest_mcp.client.gateway import GatewayClient
from ignition_rest_mcp.errors import GatewayError
from ignition_rest_mcp.observability.metrics import Metrics
from ignition_rest_mcp.operation import OperationContext
from ignition_rest_mcp.services.exports import project_export, tag_config_export
from ignition_rest_mcp.storage.database import Storage
from ignition_rest_mcp.storage.records import OperationRecordStore
from test_config import _settings

PCF1 = re.compile(r"^pcf1:[0-9a-f]{64}$")


def _zip_bytes(entries: dict[str, bytes] | None = None, *, stamp: tuple[int, ...] = (2026, 1, 1, 0, 0, 0)) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in (entries or {"project.json": b'{"title": "Demo"}'}).items():
            info = zipfile.ZipInfo(name, date_time=stamp)
            archive.writestr(info, body)
    return buffer.getvalue()


def _openapi_paths(*, with_project: bool = True, with_tags: bool = True) -> bytes:
    paths: dict[str, Any] = {
        "/data/api/v1/gateway-info": {"get": {}},
        "/data/api/v1/projects/list": {"get": {}},
    }
    if with_project:
        paths["/data/api/v1/projects/export/{name}"] = {"get": {}}
    if with_tags:
        paths["/data/api/v1/tags/export"] = {"get": {}}
    return json.dumps({"paths": paths}).encode()


class _Env:
    def __init__(self, tmp_path: Path, *, handler: Any, max_bytes: int = 1_000_000,
                 with_project: bool = True, with_tags: bool = True) -> None:
        self.settings = _settings(
            data_dir=str(tmp_path), artifact_max_bytes=max_bytes,
            artifact_total_bytes=max_bytes * 10,
        )
        self.transport_handler = handler
        self.client = GatewayClient(
            base_url="http://gw", api_token="t", timeout_seconds=10,
            transport=httpx.MockTransport(handler),
        )
        self._openapi = _openapi_paths(with_project=with_project, with_tags=with_tags)
        self.registry = CapabilityRegistry(self.client)
        self.storage = Storage(tmp_path)
        self.metrics = Metrics()

    async def start(self) -> None:
        await self.storage.open()
        self.store = LocalArtifactStore(
            self.storage.state, Path(self.settings.data_dir), quotas_from_settings(self.settings),
        )
        self.store.prepare()
        self.records = OperationRecordStore(self.storage.state)
        self.sink = SqliteAuditSink(self.storage.audit)

        async def openapi() -> bytes:
            return self._openapi

        async def gateway_info(context: Any = None) -> dict[str, str]:
            return {"ignitionVersion": "8.3.8"}

        async def modules(context: Any = None) -> dict[str, list[Any]]:
            return {"items": []}

        self.client.openapi = openapi  # type: ignore[method-assign]
        self.client.gateway_info = gateway_info  # type: ignore[method-assign]
        self.client.healthy_modules = modules  # type: ignore[method-assign]
        await self.registry.refresh()

    async def stop(self) -> None:
        await self.client.aclose()
        await self.storage.close()

    def context(self, tool: str) -> OperationContext:
        context = OperationContext.start(tool, "none:test", "ARTIFACT")
        context.auditor = Auditor(self.sink, self.records, context, self.metrics)
        return context

    async def audit_rows(self) -> list[tuple[str, str]]:
        return list(await self.storage.audit.run(
            lambda conn: conn.execute("SELECT phase, outcome FROM audit_log ORDER BY seq").fetchall()
        ))

    async def artifact_count(self) -> int:
        def _read(conn: Any) -> int:
            return int(conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0])

        return await self.storage.state.run(_read)

    async def staging_leftovers(self) -> list[str]:
        staging = Path(self.settings.data_dir) / "artifacts" / "staging"
        return [entry.name for entry in staging.iterdir()] if staging.exists() else []


def _run(coro: Any) -> Any:
    async def main() -> Any:
        return await coro

    return asyncio.run(main())


# --------------------------------------------------------------------------- transport


def test_stream_get_to_feeds_sink_with_cap_deadline_and_identity_enforcement(tmp_path: Path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/slow":
            await asyncio.sleep(5)
            return httpx.Response(200, content=b"late", request=request)
        if request.url.path == "/gzip":
            return httpx.Response(
                200, content=b"x", headers={"content-encoding": "gzip"}, request=request,
            )
        if request.url.path == "/big":
            return httpx.Response(200, content=b"x" * 5000, request=request)
        return httpx.Response(200, content=b"hello", request=request)

    client = GatewayClient(base_url="http://g", api_token="t", timeout_seconds=10,
                           transport=httpx.MockTransport(handler))
    received = bytearray()

    class Sink:
        async def write(self, chunk: bytes) -> None:
            received.extend(chunk)

    async def scenario() -> None:
        context = OperationContext.start("project_export", "none:test", "ARTIFACT")
        delivered = await client.stream_get_to(
            "/ok", sink=Sink(), limit_bytes=100, deadline_seconds=5, context=context,
        )
        assert delivered == 5 and bytes(received) == b"hello"

        received.clear()
        with pytest.raises(GatewayError) as cap:
            await client.stream_get_to("/big", sink=Sink(), limit_bytes=100, deadline_seconds=5, context=context)
        assert cap.value.code == "limit_exceeded"

        received.clear()
        with pytest.raises(GatewayError) as smaller:
            await client.stream_get_to("/big", sink=Sink(), limit_bytes=2_000, deadline_seconds=5,
                                       context=context)
        assert smaller.value.code == "limit_exceeded"
        assert len(received) <= 2_000  # only the accepted prefix ever reached the sink

        with pytest.raises(GatewayError) as enc:
            await client.stream_get_to("/gzip", sink=Sink(), limit_bytes=100, deadline_seconds=5,
                                       context=context)
        assert enc.value.code == "schema_mismatch"
        assert not received  # identity encoding is enforced before any delivery

    _run(scenario())
    _run(client.aclose())


# --------------------------------------------------------------------------- project_export


def test_project_export_happy_path_persists_artifact_and_audit(tmp_path: Path) -> None:
    body = _zip_bytes()

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/data/api/v1/projects/export/Demo"
        return httpx.Response(200, content=body, request=request)

    env = _Env(tmp_path, handler=handler)

    async def scenario() -> None:
        await env.start()
        context = env.context("project_export")
        result = await project_export(
            env.client, env.registry, env.store, context,
            settings_gate_on=True, project_name="Demo", gateway_id="", deadline_seconds=10,
        )
        assert PCF1.match(result.fingerprint)
        assert result.projectName == "Demo"
        assert result.artifact.sensitivity == "CONFIDENTIAL"
        assert result.artifact.retentionClass == "EXPORT"
        assert result.artifact.kind == "project_export"
        assert result.artifact.download.path == f"/artifacts/{result.artifact.artifactId}"
        assert await env.artifact_count() == 1
        assert [r[0] for r in await env.audit_rows()] == ["decision", "attempt", "result"]
        assert await env.staging_leftovers() == []
        await env.stop()

    _run(scenario())


def test_project_export_name_encoding_for_spaces_and_unicode(tmp_path: Path) -> None:
    seen: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.raw_path.decode("ascii"))
        return httpx.Response(200, content=_zip_bytes(), request=request)

    env = _Env(tmp_path, handler=handler)

    async def scenario() -> None:
        await env.start()
        context = env.context("project_export")
        await project_export(
            env.client, env.registry, env.store, context,
            settings_gate_on=True, project_name="My Projéct/x", gateway_id="", deadline_seconds=10,
        )
        # single path segment, percent-encoded, no invented charset
        assert seen == ["/data/api/v1/projects/export/My%20Proj%C3%A9ct%2Fx"]
        await env.stop()

    _run(scenario())


@pytest.mark.parametrize(
    ("project_name", "code"),
    [("", "invalid_argument"), ("   ", "invalid_argument"), ("x\ny", "invalid_argument"),
     ("x" * 300, "limit_exceeded")],
)
def test_project_export_input_bounds(tmp_path: Path, project_name: str, code: str) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("gateway must not be called for invalid input")

    env = _Env(tmp_path, handler=handler)

    async def scenario() -> None:
        await env.start()
        context = env.context("project_export")
        with pytest.raises(GatewayError) as captured:
            await project_export(
                env.client, env.registry, env.store, context,
                settings_gate_on=True, project_name=project_name, gateway_id="", deadline_seconds=10,
            )
        assert captured.value.code == code
        assert await env.artifact_count() == 0
        await env.stop()

    _run(scenario())


def test_project_export_status_mapping(tmp_path: Path) -> None:
    for status, code in ((404, "not_found"), (403, "permission_denied"), (500, "upstream_error")):
        env = _Env(tmp_path / str(status), handler=lambda r, status=status: httpx.Response(  # type: ignore[misc]
            status, request=r,
        ))

        async def scenario(env: _Env = env, code: str = code) -> None:
            await env.start()
            context = env.context("project_export")
            with pytest.raises(GatewayError) as captured:
                await project_export(
                    env.client, env.registry, env.store, context,
                    settings_gate_on=True, project_name="Demo", gateway_id="", deadline_seconds=10,
                )
            assert captured.value.code == code
            assert await env.artifact_count() == 0  # failed fetch publishes nothing
            assert await env.staging_leftovers() == []
            await env.stop()

        _run(scenario())


def test_project_export_non_zip_body_publishes_nothing(tmp_path: Path) -> None:
    env = _Env(tmp_path, handler=lambda r: httpx.Response(200, content=b"PK\x03\x04broken", request=r))

    async def scenario() -> None:
        await env.start()
        context = env.context("project_export")
        with pytest.raises(GatewayError) as captured:
            await project_export(
                env.client, env.registry, env.store, context,
                settings_gate_on=True, project_name="Demo", gateway_id="", deadline_seconds=10,
            )
        assert captured.value.code == "invalid_argument"
        assert await env.artifact_count() == 0
        assert await env.staging_leftovers() == []
        rows = await env.audit_rows()
        assert [r[0] for r in rows] == ["decision", "attempt", "result"]
        assert rows[-1][1] == "failed"
        await env.stop()

    _run(scenario())


def test_project_export_truncated_stream_publishes_nothing(tmp_path: Path) -> None:
    # body cut mid-ZIP: fingerprint validation of the staged bytes must refuse
    env = _Env(tmp_path, handler=lambda r: httpx.Response(200, content=_zip_bytes()[:6], request=r))

    async def scenario() -> None:
        await env.start()
        context = env.context("project_export")
        with pytest.raises(GatewayError):
            await project_export(
                env.client, env.registry, env.store, context,
                settings_gate_on=True, project_name="Demo", gateway_id="", deadline_seconds=10,
            )
        assert await env.artifact_count() == 0
        await env.stop()

    _run(scenario())


def test_project_export_deadline_stops_transfer(tmp_path: Path) -> None:
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(2)
        return httpx.Response(200, content=_zip_bytes(), request=request)

    env = _Env(tmp_path, handler=slow)

    async def scenario() -> None:
        await env.start()
        context = env.context("project_export")
        with pytest.raises(GatewayError) as captured:
            await project_export(
                env.client, env.registry, env.store, context,
                settings_gate_on=True, project_name="Demo", gateway_id="", deadline_seconds=0.05,
            )
        assert captured.value.code == "timeout"
        assert await env.artifact_count() == 0
        await env.stop()

    _run(scenario())


def test_project_export_byte_cap_mid_stream(tmp_path: Path) -> None:
    env = _Env(tmp_path, handler=lambda r: httpx.Response(200, content=_zip_bytes() * 60, request=r),
               max_bytes=2048)

    async def scenario() -> None:
        await env.start()
        context = env.context("project_export")
        with pytest.raises(GatewayError) as captured:
            await project_export(
                env.client, env.registry, env.store, context,
                settings_gate_on=True, project_name="Demo", gateway_id="", deadline_seconds=10,
            )
        assert captured.value.code == "limit_exceeded"
        assert await env.artifact_count() == 0
        assert await env.staging_leftovers() == []
        await env.stop()

    _run(scenario())


# --------------------------------------------------------------------------- tag_config_export


def test_tag_config_export_happy_path_locks_json_type(tmp_path: Path) -> None:
    payload = json.dumps({"tags": [{"name": "A"}]}).encode()

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["type"] == "json"
        assert request.url.params["provider"] == "default"
        assert request.url.params["path"] == "Facility"
        assert request.url.params["recursive"] == "true"
        assert request.url.params["includeUdts"] == "false"
        return httpx.Response(200, content=payload, request=request)

    env = _Env(tmp_path, handler=handler)

    async def scenario() -> None:
        await env.start()
        context = env.context("tag_config_export")
        result = await tag_config_export(
            env.client, env.registry, env.store, context, settings_gate_on=True,
            provider="default", path="Facility", recursive=True, include_udts=False,
            gateway_id="", deadline_seconds=10,
        )
        assert result.artifact.kind == "tag_config_export"
        assert result.artifact.mediaType == "application/json"
        assert result.provider == "default" and result.path == "Facility"
        assert [r[0] for r in await env.audit_rows()] == ["decision", "attempt", "result"]
        await env.stop()

    _run(scenario())


def test_tag_config_export_rejects_invalid_json_body(tmp_path: Path) -> None:
    env = _Env(tmp_path, handler=lambda r: httpx.Response(200, content=b"<html>nope", request=r))

    async def scenario() -> None:
        await env.start()
        context = env.context("tag_config_export")
        with pytest.raises(GatewayError) as captured:
            await tag_config_export(
                env.client, env.registry, env.store, context, settings_gate_on=True,
                provider="default", path="", recursive=True, include_udts=False,
                gateway_id="", deadline_seconds=10,
            )
        assert captured.value.code == "invalid_argument"
        assert await env.artifact_count() == 0
        await env.stop()

    _run(scenario())


def test_tag_config_export_validation_bounds(tmp_path: Path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not dispatch")

    env = _Env(tmp_path, handler=handler)

    async def scenario() -> None:
        await env.start()
        for kwargs in (
            {"provider": "", "path": "", "recursive": True, "include_udts": False},
            {"provider": "x" * 300, "path": "", "recursive": True, "include_udts": False},
            {"provider": "default", "path": "y" * 1100, "recursive": True, "include_udts": False},
        ):
            context = env.context("tag_config_export")
            with pytest.raises(GatewayError):
                await tag_config_export(
                    env.client, env.registry, env.store, context, settings_gate_on=True,
                    gateway_id="", deadline_seconds=10, **kwargs,
                )
        assert await env.artifact_count() == 0
        await env.stop()

    _run(scenario())


# --------------------------------------------------------------------------- gates and capability


def test_gate_off_denies_with_audit_row_before_any_dispatch(tmp_path: Path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("gate-off exports must not reach the Gateway")

    env = _Env(tmp_path, handler=handler)

    async def scenario() -> None:
        await env.start()
        context = env.context("project_export")
        with pytest.raises(GatewayError) as captured:
            await project_export(
                env.client, env.registry, env.store, context,
                settings_gate_on=False, project_name="Demo", gateway_id="", deadline_seconds=10,
            )
        assert captured.value.code == "operation_disabled"
        rows = await env.audit_rows()
        assert rows == [("decision", "denied:operation_disabled")]
        await env.stop()

    _run(scenario())


def test_missing_capability_denies_with_audit_row(tmp_path: Path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("capability-less export must not reach the Gateway")

    env = _Env(tmp_path, handler=handler, with_project=False)

    async def scenario() -> None:
        await env.start()
        context = env.context("project_export")
        with pytest.raises(GatewayError) as captured:
            await project_export(
                env.client, env.registry, env.store, context,
                settings_gate_on=True, project_name="Demo", gateway_id="", deadline_seconds=10,
            )
        assert captured.value.code == "unsupported_capability"
        rows = await env.audit_rows()
        assert rows == [("decision", "denied:unsupported_capability")]
        await env.stop()

    _run(scenario())


def _list_tools(settings) -> dict[str, str]:  # type: ignore[no-untyped-def]
    async def scenario() -> dict[str, str]:
        server = server_module.create_server(settings)
        async with Client(server) as client:
            tools = await client.list_tools()
            return {tool.name: "enabled" for tool in tools}

    return _run(scenario())


@pytest.fixture
def stub_gateway(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state = {"with_project": True, "with_tags": True}

    async def info(self: GatewayClient, context: Any = None) -> dict[str, str]:
        return {"ignitionVersion": "8.3.8"}

    async def modules(self: GatewayClient, context: Any = None) -> dict[str, list[Any]]:
        return {"items": []}

    async def openapi(self: GatewayClient) -> bytes:
        return _openapi_paths(with_project=state["with_project"], with_tags=state["with_tags"])

    monkeypatch.setattr(GatewayClient, "gateway_info", info)
    monkeypatch.setattr(GatewayClient, "healthy_modules", modules)
    monkeypatch.setattr(GatewayClient, "openapi", openapi)
    return state


def test_discovery_requires_gate_and_capability(tmp_path: Path, stub_gateway: dict[str, Any]) -> None:
    def visible(**overrides: Any) -> set[str]:
        settings = _settings(data_dir=str(tmp_path), **overrides)
        return set(_list_tools(settings))

    base_present = {"gateway_info", "gateway_diagnose", "project_list"}
    got = visible()
    assert base_present <= got
    assert "project_export" not in got and "tag_config_export" not in got  # gate off (default)

    got = visible(sensitive_exports_enabled=True)
    assert "project_export" in got and "tag_config_export" in got

    stub_gateway["with_project"] = False
    stub_gateway["with_tags"] = False
    got = visible(sensitive_exports_enabled=True)
    assert "project_export" not in got and "tag_config_export" not in got  # capability absent
