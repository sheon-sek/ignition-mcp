from __future__ import annotations

import asyncio
import json
import logging

import httpx
import pytest
from fastmcp import Client
from pydantic import ValidationError

from ignition_rest_mcp.capabilities.registry import CapabilityRegistry
from ignition_rest_mcp.client.gateway import GatewayClient
from ignition_rest_mcp.errors import GatewayError
from ignition_rest_mcp.models import PageMetadata
from ignition_rest_mcp.operation import OperationContext
from ignition_rest_mcp.server import create_server
from ignition_rest_mcp.services.gateway import gateway_info
from test_config import _settings


@pytest.fixture
def gateway(monkeypatch):
    state = {"failure": None, "direct": 0, "refresh": 0}

    async def info(self, context=None):
        if context is not None:
            state["direct"] += 1
            if state["failure"]:
                raise GatewayError(state["failure"], "Safe gateway failure")
        return dict(name="test", edition="standard", ignitionVersion="8.3.8",
                    redundancyRole="Independent", timeZoneId="UTC", jvmVersion="17")

    async def modules(self, context=None):
        return {"items": []}

    async def openapi(self):
        state["refresh"] += 1
        return b'{"paths":{"/data/api/v1/gateway-info":{"get":{}}}}'

    monkeypatch.setattr(GatewayClient, "gateway_info", info)
    monkeypatch.setattr(GatewayClient, "healthy_modules", modules)
    monkeypatch.setattr(GatewayClient, "openapi", openapi)
    return state


@pytest.mark.parametrize("tool", ["gateway_info", "gateway_diagnose"])
@pytest.mark.parametrize("failure", ["gateway_unavailable", "schema_mismatch", "not_found"])
def test_direct_failure_updates_wire_resources(gateway, tool, failure):
    async def scenario():
        server = create_server(_settings())
        async with Client(server) as client:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.http_app()), base_url="http://test") as health:
                assert (await health.get("/health/ready")).status_code == 200
            gateway["failure"] = failure
            result = await client.call_tool(tool, {}, raise_on_error=False)
            if tool == "gateway_info":
                assert result.is_error
                error = json.loads(result.content[0].text)
                assert set(error) == {"code", "message", "correlationId"}
                assert error["code"] == failure
            else:
                assert not result.is_error
                assert result.data.registryState == "STALE"
            resource = await client.read_resource("ignition://gateway/capabilities")
            assert json.loads(resource[0].text)["state"] == "STALE"
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.http_app()), base_url="http://test") as health:
                response = await health.get("/health/ready")
                assert response.status_code == 503
                payload = response.json()
                assert (payload["ready"], payload["registryState"]) == (False, "STALE")
                # A Gateway failure must never implicate the storage subsystems.
                assert payload["storageReady"] is True
            assert gateway["direct"] == 1  # refresh is metadata, not operation replay
            assert gateway["refresh"] == (1 if failure == "gateway_unavailable" else 2)
    asyncio.run(scenario())


@pytest.mark.parametrize("tool", ["gateway_info", "gateway_diagnose"])
@pytest.mark.parametrize("failure", ["expected", "timeout", "unexpected"])
def test_canonical_errors_and_telemetry(monkeypatch, caplog, gateway, tool, failure):
    async def service(*args):
        if failure == "timeout":
            await asyncio.Event().wait()
        if failure == "expected":
            raise GatewayError("permission_denied", "Safe rejection")
        raise RuntimeError("DO-NOT-LEAK")

    monkeypatch.setattr("ignition_rest_mcp.server." + ("info_service" if tool == "gateway_info" else "diagnose_service"), service)
    recorded = []
    monkeypatch.setattr("ignition_rest_mcp.observability.metrics.Metrics.record_tool",
                        lambda self, name, outcome: recorded.append((name, outcome)))
    caplog.set_level(logging.INFO, logger="ignition_rest_mcp")

    async def scenario():
        async with Client(create_server(_settings(tool_timeout_seconds=0.01))) as client:
            result = await client.call_tool(tool, {}, raise_on_error=False)
            assert result.is_error
            error = json.loads(result.content[0].text)
            code = {"expected": "permission_denied", "timeout": "timeout", "unexpected": "internal_error"}[failure]
            assert error["code"] == code
            assert "DO-NOT-LEAK" not in str(result.content)
            assert recorded == [(tool, code)]
            logs = [r for r in caplog.records if getattr(r, "event", None) == "tool_call"]
            assert len(logs) == 1
            assert logs[0].correlationId == error["correlationId"]
            assert logs[0].errorCode == code
            assert logs[0].durationMs >= 0
    asyncio.run(scenario())


def test_pydantic_validation_error_is_canonical_and_degrades_readiness(gateway, monkeypatch):
    with pytest.raises(ValidationError) as captured:
        PageMetadata(total=-1, matching=0, limit=1, offset=0)
    failure = captured.value
    calls = 0

    async def service(*args):
        nonlocal calls
        calls += 1
        raise failure

    monkeypatch.setattr("ignition_rest_mcp.server.info_service", service)

    async def scenario():
        server = create_server(_settings())
        async with Client(server) as client:
            result = await client.call_tool("gateway_info", {}, raise_on_error=False)
            assert result.is_error
            error = json.loads(result.content[0].text)
            assert error["code"] == "schema_mismatch"
            assert set(error) == {"code", "message", "correlationId"}
            resource = await client.read_resource("ignition://gateway/capabilities")
            assert json.loads(resource[0].text)["state"] == "STALE"
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=server.http_app()), base_url="http://test",
            ) as health:
                response = await health.get("/health/ready")
                assert response.status_code == 503
                payload = response.json()
                assert (payload["ready"], payload["registryState"]) == (False, "STALE")
                assert payload["storageReady"] is True
            assert calls == 1
            assert gateway["refresh"] == 2

    asyncio.run(scenario())


def test_mismatch_refresh_coalesces_without_replay(gateway, monkeypatch):
    async def scenario():
        client = GatewayClient(base_url="http://gateway", api_token="key", timeout_seconds=1)
        registry = CapabilityRegistry(client)
        await registry.refresh()
        started, release = asyncio.Event(), asyncio.Event()

        async def openapi(self):
            gateway["refresh"] += 1
            started.set()
            await release.wait()
            return b'{"paths":{}}'

        monkeypatch.setattr(GatewayClient, "openapi", openapi)
        gateway["failure"] = "schema_mismatch"
        tasks = [asyncio.create_task(gateway_info(client, registry, OperationContext.start("gateway_info", "test", "FAST"))) for _ in range(5)]
        await started.wait()
        assert registry.snapshot.state == "STALE"
        release.set()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        assert all(isinstance(result, GatewayError) for result in results)
        assert gateway["direct"] == 5
        assert gateway["refresh"] == 2
        assert registry.snapshot.state == "STALE"
        await registry.aclose()
        await client.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["refresh", "fingerprint_changed"])
def test_registry_failure_drains_sibling_requests(monkeypatch, operation):
    async def scenario():
        entered, closed = asyncio.Event(), asyncio.Event()

        async def info(self, context=None):
            await entered.wait()
            raise GatewayError("gateway_unavailable", "unavailable")

        async def modules(self, context=None):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                closed.set()

        async def openapi(self):
            await asyncio.Event().wait()

        monkeypatch.setattr(GatewayClient, "gateway_info", info)
        monkeypatch.setattr(GatewayClient, "healthy_modules", modules)
        monkeypatch.setattr(GatewayClient, "openapi", openapi)
        client = GatewayClient(base_url="http://gateway", api_token="key", timeout_seconds=1)
        registry = CapabilityRegistry(client)
        await getattr(registry, operation)()
        assert closed.is_set()
        await registry.aclose()
        await client.aclose()
    asyncio.run(scenario())


def test_cancelled_initialization_closes_client(monkeypatch, gateway):
    async def scenario():
        entered, closed, request_closed = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def info(self, context=None):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                request_closed.set()

        original_close = GatewayClient.aclose

        async def close(self):
            await original_close(self)
            closed.set()

        monkeypatch.setattr(GatewayClient, "gateway_info", info)
        monkeypatch.setattr(GatewayClient, "aclose", close)
        server = create_server(_settings())

        async def start():
            async with server._lifespan_manager():
                pass

        task = asyncio.create_task(start())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert closed.is_set()
        assert request_closed.is_set()
    asyncio.run(scenario())


@pytest.mark.parametrize("tool", ["gateway_info", "gateway_diagnose"])
def test_wire_cancellation_records_once(monkeypatch, caplog, gateway, tool):
    async def scenario():
        entered, cancelled = asyncio.Event(), asyncio.Event()

        async def service(*args):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        monkeypatch.setattr("ignition_rest_mcp.server." + ("info_service" if tool == "gateway_info" else "diagnose_service"), service)
        recorded = []
        monkeypatch.setattr("ignition_rest_mcp.observability.metrics.Metrics.record_tool",
                            lambda self, name, outcome: recorded.append((name, outcome)))
        caplog.set_level(logging.INFO, logger="ignition_rest_mcp")
        async with Client(create_server(_settings(tool_timeout_seconds=0.01))) as client:
            task = asyncio.create_task(client.call_tool(tool, {}))
            await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await asyncio.wait_for(cancelled.wait(), 2)
        assert recorded == [(tool, "cancelled")]
        logs = [r for r in caplog.records if getattr(r, "event", None) == "tool_call"]
        assert len(logs) == 1
        assert logs[0].outcome == "cancelled"
    asyncio.run(scenario())
