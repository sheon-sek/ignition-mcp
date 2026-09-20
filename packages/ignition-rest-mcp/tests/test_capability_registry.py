from __future__ import annotations

import asyncio
import json

import httpx

from ignition_rest_mcp.capabilities.registry import CapabilityRegistry
from ignition_rest_mcp.client.gateway import GatewayClient


def _transport(openapi_status: int = 200) -> httpx.MockTransport:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/data/api/v1/gateway-info":
            return httpx.Response(200, json={"ignitionVersion": "8.3.8 (b2026071409)"}, request=request)
        if request.url.path == "/data/api/v1/modules/healthy":
            return httpx.Response(
                200,
                json={"items": [{"id": "com.inductiveautomation.mcp", "version": "1.3.5-SNAPSHOT"}]},
                request=request,
            )
        if request.url.path == "/openapi.json":
            body = json.dumps({"paths": {"/data/api/v1/gateway-info": {"get": {}}}}).encode()
            return httpx.Response(openapi_status, content=body, request=request)
        raise AssertionError(request.url)

    return httpx.MockTransport(handler)


def test_refresh_builds_immutable_ready_snapshot() -> None:
    async def scenario() -> None:
        client = GatewayClient(
            base_url="http://gateway",
            api_token="ci:key",
            timeout_seconds=10,
            transport=_transport(),
        )
        registry = CapabilityRegistry(client)
        try:
            snapshot = await registry.refresh()
            assert snapshot.state == "READY"
            assert snapshot.generation == 1
            assert snapshot.semantic_capabilities == frozenset({"gateway_info"})
            assert snapshot.openapi_sha256 is not None
            try:
                snapshot.module_versions["x"] = "y"  # type: ignore[index]
            except TypeError:
                pass
            else:
                raise AssertionError("module_versions must be immutable")
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_failed_refresh_preserves_last_snapshot_as_stale() -> None:
    async def scenario() -> None:
        client = GatewayClient(
            base_url="http://gateway",
            api_token="ci:key",
            timeout_seconds=10,
            transport=_transport(),
        )
        registry = CapabilityRegistry(client)
        try:
            first = await registry.refresh()
            assert first.state == "READY"
            await client.aclose()

            failed = GatewayClient(
                base_url="http://gateway",
                api_token="ci:key",
                timeout_seconds=10,
                transport=_transport(openapi_status=503),
            )
            registry._client = failed  # type: ignore[attr-defined]
            try:
                second = await registry.refresh()
                assert second.state == "STALE"
                assert second.generation == 1
                assert second.openapi_sha256 == first.openapi_sha256
                assert second.semantic_capabilities == first.semantic_capabilities
            finally:
                await failed.aclose()
        finally:
            pass

    asyncio.run(scenario())
