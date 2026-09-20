"""Shared bounded Ignition REST transport."""

from __future__ import annotations

from typing import Any, cast

import httpx

from ignition_rest_mcp.errors import GatewayError, map_http_error


class GatewayClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_token: str,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={
                "Accept": "application/json",
                "X-Ignition-API-Token": api_token,
                "User-Agent": "ignition-rest-mcp/0.1.0a0",
            },
            follow_redirects=False,
            timeout=httpx.Timeout(timeout_seconds),
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get_json(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            response = await self._client.get(path, params=params)
            response.raise_for_status()
            value = response.json()
            if not isinstance(value, dict):
                raise ValueError("expected JSON object")
            return cast(dict[str, Any], value)
        except Exception as error:
            if isinstance(error, GatewayError):
                raise
            raise map_http_error(error) from error

    async def get_bytes(self, path: str) -> bytes:
        try:
            response = await self._client.get(path, headers={"Accept": "application/json"})
            response.raise_for_status()
            return response.content
        except Exception as error:
            if isinstance(error, GatewayError):
                raise
            raise map_http_error(error) from error

    async def gateway_info(self) -> dict[str, Any]:
        return await self.get_json("/data/api/v1/gateway-info")

    async def healthy_modules(self) -> dict[str, Any]:
        return await self.get_json("/data/api/v1/modules/healthy", params={"limit": 500, "offset": 0})

    async def openapi(self) -> bytes:
        return await self.get_bytes("/openapi.json")
