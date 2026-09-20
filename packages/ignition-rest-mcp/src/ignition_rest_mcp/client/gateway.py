"""Shared bounded Ignition REST transport."""

from __future__ import annotations

import asyncio
import json
from typing import Any, cast

import httpx

from ignition_rest_mcp.errors import GatewayError, map_http_error
from ignition_rest_mcp.operation import OperationContext

# OpenAPI is internal metadata, not an MCP output. Still impose a finite ceiling.
OPENAPI_LIMIT_BYTES = 16 * 1024 * 1024
JSON_LIMIT_BYTES = 1_048_576


class GatewayClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_token: str,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "X-Ignition-API-Token": api_token,
                "User-Agent": "ignition-rest-mcp/0.1.0a0",
            },
            follow_redirects=False,
            timeout=httpx.Timeout(timeout_seconds),
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get_json(
        self, path: str, *, params: dict[str, Any] | None = None,
        context: OperationContext | None = None,
    ) -> dict[str, Any]:
        body = await self.get_bytes(path, params=params, context=context, limit_bytes=JSON_LIMIT_BYTES)
        try:
            value = json.loads(body)
            if not isinstance(value, dict):
                raise ValueError("expected JSON object")
            return cast(dict[str, Any], value)
        except (ValueError, TypeError) as error:
            raise map_http_error(error) from error

    async def get_bytes(
        self, path: str, *, params: dict[str, Any] | None = None,
        context: OperationContext | None = None, limit_bytes: int = JSON_LIMIT_BYTES,
    ) -> bytes:
        headers = {"X-Correlation-ID": context.correlation_id} if context else {}
        try:
            async with asyncio.timeout(self._timeout_seconds):
                async with self._client.stream("GET", path, params=params, headers=headers) as response:
                    response.raise_for_status()
                    if response.headers.get("content-encoding", "identity").lower() != "identity":
                        raise GatewayError("schema_mismatch", "Gateway ignored identity content encoding")
                    body = bytearray()
                    # Identity encoding avoids allocating an unbounded decompression buffer.
                    async for chunk in response.aiter_bytes():
                        size = len(body) + len(chunk)
                        if size > limit_bytes:
                            raise GatewayError(
                                "limit_exceeded",
                                f"Gateway response requires at least {size} bytes; limit is "
                                f"{limit_bytes} bytes. Reduce the requested data or split the request.",
                            )
                        body.extend(chunk)
                    return bytes(body)
        except Exception as error:
            if isinstance(error, GatewayError):
                raise
            raise map_http_error(error) from error

    async def gateway_info(self, context: OperationContext | None = None) -> dict[str, Any]:
        return await self.get_json("/data/api/v1/gateway-info", context=context)

    async def healthy_modules(self, context: OperationContext | None = None) -> dict[str, Any]:
        return await self.get_json(
            "/data/api/v1/modules/healthy", params={"limit": 500, "offset": 0}, context=context,
        )

    async def openapi(self) -> bytes:
        return await self.get_bytes("/openapi.json", limit_bytes=OPENAPI_LIMIT_BYTES)
