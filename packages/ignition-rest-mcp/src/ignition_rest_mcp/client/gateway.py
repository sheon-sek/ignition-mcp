"""Shared bounded Ignition REST transport.

Transport only (D25): no transaction, artifact-lifecycle or policy logic lives
here. ``dispatch_write`` is the single write primitive; it records the exact
dispatch boundary (never retries) so the guarded executor can decide what the
Gateway may or may not have seen.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
import json
from typing import Any, AsyncIterator, Protocol, cast

import httpx

from ignition_rest_mcp.errors import GatewayError, map_http_error
from ignition_rest_mcp.operation import OperationContext


class ArtifactSink(Protocol):
    async def write(self, chunk: bytes) -> None: ...


# OpenAPI is internal metadata, not an MCP output. Still impose a finite ceiling.
OPENAPI_LIMIT_BYTES = 16 * 1024 * 1024
JSON_LIMIT_BYTES = 1_048_576


class DispatchOutcome(str, Enum):
    """Where the request died relative to the network boundary (D08)."""

    NOT_SENT = "not_sent"                          # known not attempted (connect stage)
    SENT_PARTIAL = "sent_partial"                  # body may have partially reached Ignition
    SENT_COMPLETE_NO_RESPONSE = "sent_complete_no_response"  # full body sent, outcome unknown
    RESPONDED = "responded"                        # real HTTP response observed


@dataclass(frozen=True, slots=True)
class WriteDispatchResult:
    outcome: DispatchOutcome
    status: int | None = None
    body: dict[str, Any] | None = None
    transport_error: GatewayError | None = None    # safe mapped error for NOT_SENT


POSSIBLY_DISPATCHED = frozenset(
    {DispatchOutcome.SENT_PARTIAL, DispatchOutcome.SENT_COMPLETE_NO_RESPONSE}
)


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

    async def stream_get_to(
        self, path: str, *, params: dict[str, Any] | None = None,
        sink: "ArtifactSink", limit_bytes: int, deadline_seconds: float,
        context: OperationContext | None = None,
    ) -> int:
        """Transport-only streamed GET feeding an async sink under a byte cap and an
        elapsed deadline. Binary bodies never materialize whole in memory (D17).

        Raises the same typed GatewayError mapping as the other transports; partial
        delivery is the caller's problem to discard (an aborted sink publishes
        nothing). Returns the number of bytes delivered to the sink.
        """

        if limit_bytes < 1:
            raise GatewayError("internal_error", "stream limit must be positive")
        headers = {"X-Correlation-ID": context.correlation_id} if context else {}
        delivered = 0
        try:
            async with asyncio.timeout(deadline_seconds):
                async with self._client.stream(
                    "GET", path, params=params, headers=headers,
                    timeout=httpx.Timeout(deadline_seconds),
                ) as response:
                    response.raise_for_status()
                    if response.headers.get("content-encoding", "identity").lower() != "identity":
                        raise GatewayError("schema_mismatch", "Gateway ignored identity content encoding")
                    async for chunk in response.aiter_bytes():
                        delivered += len(chunk)
                        if delivered > limit_bytes:
                            raise GatewayError(
                                "limit_exceeded",
                                f"Gateway stream requires at least {delivered} bytes; limit is "
                                f"{limit_bytes} bytes",
                            )
                        await sink.write(chunk)
                    return delivered
        except Exception as error:
            if isinstance(error, GatewayError):
                raise
            raise map_http_error(error) from error

    async def dispatch_write(
        self, method: str, path: str, *, body_chunks: AsyncIterator[bytes],
        content_type: str, params: dict[str, Any] | None = None,
        deadline_seconds: float, context: OperationContext | None = None,
        phase_report: "dict[str, bool] | None" = None,
    ) -> WriteDispatchResult:
        """Exactly-once streaming write dispatch with a typed dispatch boundary.

        NEVER retries. The returned outcome records what the Gateway may have
        seen: only ``NOT_SENT`` is a known non-attempt; everything ambiguous
        stays ambiguous and the caller must verify state, never replay.
        ``phase_report`` lets a cancellation (which always re-raises) still be
        classified by the caller from the shared boundary flags.
        """
        """Exactly-once streaming write dispatch with a typed dispatch boundary.

        NEVER retries. The returned outcome records what the Gateway may have
        seen: only ``NOT_SENT`` is a known non-attempt; everything ambiguous
        stays ambiguous and the caller must verify state, never replay.
        """

        if method.upper() not in {"POST", "PUT", "PATCH", "DELETE"}:
            raise GatewayError("internal_error", "dispatch_write is for write methods only")
        method_upper = method.upper()

        headers = {
            "Content-Type": content_type,
            "Accept": "application/json",
            "Accept-Encoding": "identity",
        }
        if context is not None:
            headers["X-Correlation-ID"] = context.correlation_id
        phase = phase_report if phase_report is not None else {
            "body_complete": False, "response_started": False,
        }

        async def tracking_body() -> AsyncIterator[bytes]:
            async for chunk in body_chunks:
                yield chunk
            phase["body_complete"] = True

        try:
            async with asyncio.timeout(deadline_seconds):
                async with self._client.stream(
                    method_upper, path, params=params, headers=headers, content=tracking_body(),
                ) as response:
                    phase["response_started"] = True
                    status = response.status_code
                    body = await _bounded_json_body(response)
                    return WriteDispatchResult(DispatchOutcome.RESPONDED, status=status, body=body)
        except asyncio.CancelledError:
            # phase_report carries the boundary; the caller classifies and audits.
            raise
        except Exception as error:
            return _classify_dispatch_error(error, phase, context)
        return WriteDispatchResult(DispatchOutcome.RESPONDED, status=None)  # unreachable guard


    async def gateway_info(self, context: OperationContext | None = None) -> dict[str, Any]:
        return await self.get_json("/data/api/v1/gateway-info", context=context)

    async def healthy_modules(self, context: OperationContext | None = None) -> dict[str, Any]:
        return await self.get_json(
            "/data/api/v1/modules/healthy", params={"limit": 500, "offset": 0}, context=context,
        )

    async def openapi(self) -> bytes:
        return await self.get_bytes("/openapi.json", limit_bytes=OPENAPI_LIMIT_BYTES)


async def _bounded_json_body(response: httpx.Response) -> dict[str, Any] | None:
    body = bytearray()
    async for chunk in response.aiter_bytes():
        if len(body) + len(chunk) > JSON_LIMIT_BYTES:
            # Status was already observed: the response is real; the oversized
            # payload is dropped explicitly rather than misclassifying dispatch.
            return None
        body.extend(chunk)
    if not body:
        return {}
    try:
        value = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return {}
    return value if isinstance(value, dict) else {"value": value}


_NOT_SENT_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)
_PARTIAL_ERRORS = (httpx.WriteError,)          # includes WriteTimeout
_NO_RESPONSE_ERRORS = (httpx.ReadError, httpx.ReadTimeout, httpx.RemoteProtocolError)


def _classify_dispatch_error(
    error: BaseException, phase: dict[str, bool], context: OperationContext | None,
) -> WriteDispatchResult:
    if isinstance(error, _NOT_SENT_ERRORS) and not phase["body_complete"]:
        return WriteDispatchResult(DispatchOutcome.NOT_SENT, transport_error=map_http_error(error))
    if isinstance(error, _PARTIAL_ERRORS) and not phase["body_complete"]:
        return WriteDispatchResult(DispatchOutcome.SENT_PARTIAL)
    if isinstance(error, _NO_RESPONSE_ERRORS):
        return WriteDispatchResult(DispatchOutcome.SENT_COMPLETE_NO_RESPONSE)
    if isinstance(error, TimeoutError):
        if phase["response_started"]:
            return WriteDispatchResult(DispatchOutcome.SENT_COMPLETE_NO_RESPONSE)
        if phase["body_complete"]:
            return WriteDispatchResult(DispatchOutcome.SENT_COMPLETE_NO_RESPONSE)
        return WriteDispatchResult(DispatchOutcome.SENT_PARTIAL)
    if isinstance(error, httpx.LocalProtocolError):
        return WriteDispatchResult(DispatchOutcome.NOT_SENT, transport_error=map_http_error(error))
    # Unknown transport failures after some bytes were sent are treated as the
    # most advanced known boundary (fail-safe toward "ambiguous", never "clean").
    if phase["response_started"] or phase["body_complete"]:
        return WriteDispatchResult(DispatchOutcome.SENT_COMPLETE_NO_RESPONSE)
    return WriteDispatchResult(DispatchOutcome.SENT_PARTIAL)
