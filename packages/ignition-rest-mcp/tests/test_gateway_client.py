from __future__ import annotations

import asyncio

import httpx

from ignition_rest_mcp.client.gateway import GatewayClient
from ignition_rest_mcp.errors import GatewayError


def test_client_sends_api_token_and_disables_redirect_following() -> None:
    async def scenario() -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["X-Ignition-API-Token"] == "ci:key"
            return httpx.Response(200, json={"name": "gw"}, request=request)

        client = GatewayClient(
            base_url="http://gateway",
            api_token="ci:key",
            timeout_seconds=10,
            transport=httpx.MockTransport(handler),
        )
        try:
            assert await client.get_json("/x") == {"name": "gw"}
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_permission_error_is_stable() -> None:
    async def scenario() -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, json={"error": "secret upstream detail"}, request=request)

        client = GatewayClient(
            base_url="http://gateway",
            api_token="ci:key",
            timeout_seconds=10,
            transport=httpx.MockTransport(handler),
        )
        try:
            try:
                await client.get_json("/x")
            except GatewayError as error:
                assert error.code == "permission_denied"
                assert "secret upstream detail" not in error.message
            else:
                raise AssertionError("expected GatewayError")
        finally:
            await client.aclose()

    asyncio.run(scenario())
