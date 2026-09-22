"""Shared constants and wire helpers for the Phase 3 G3 live harness.

Used by ``driver.py`` and ``inventory_gate_off.py``. This module is
test-only code: it lives outside the shipped package, may import the
package internals for assembly, and never mutates anything by itself.
"""
from __future__ import annotations

import json
from typing import Any

import httpx

#: The exact effective ``ignition-rest`` Tool inventory with
#: ``IGNITION_MCP_SENSITIVE_EXPORTS_ENABLED=true`` on a fully capable Gateway
#: (frozen in tests/test_phase3_safety_structure.py READ_TOOLS).
EXPECTED_GATE_ON_TOOLS = frozenset({
    "gateway_info",
    "gateway_diagnose",
    "project_list",
    "config_resource_search",
    "config_resource_describe",
    "config_resource_names",
    "config_resource_list",
    "config_resource_get",
    "audit_query",
    "alarm_pipeline_list",
    "alarm_pipeline_status",
    "project_export",
    "tag_config_export",
    "artifact_list",
    "artifact_info",
    "operation_diagnose",
})

#: The same inventory with the sensitive-export gate off (exports hidden from
#: discovery; they must also fail at call time).
EXPECTED_GATE_OFF_TOOLS = EXPECTED_GATE_ON_TOOLS - {"project_export", "tag_config_export"}

#: Phase 4 candidates that must never appear in any effective inventory.
MUTATION_TOOL_NAMES = frozenset({
    "project_import",
    "tag_config_import",
    "artifact_delete",
    "config_resource_create",
    "config_resource_update",
    "config_resource_delete",
    "config_resource_rename",
    "alarm_pipeline_cancel",
})

PROTOCOL_VERSION = "2025-06-18"
ACCEPT = "application/json, text/event-stream"
MAX_RESPONSE_BYTES = 4_194_304


class ProbeError(RuntimeError):
    """A live check failed; the message is report-safe (never contains a token)."""


def parse_rpc_body(content_type: str, body: bytes) -> dict[str, Any] | None:
    """JSON or SSE-encoded JSON-RPC response -> last data event / object."""
    text = body.decode("utf-8", errors="replace")
    if "text/event-stream" in content_type:
        events: list[dict[str, Any]] = []
        for line in text.splitlines():
            if line.startswith("data:"):
                decoded = json.loads(line[5:].strip())
                if isinstance(decoded, dict):
                    events.append(decoded)
        return events[-1] if events else None
    if not text.strip():
        return None
    decoded = json.loads(text)
    if not isinstance(decoded, dict):
        raise ProbeError("JSON-RPC response was not an object")
    return decoded


class McpHttp:
    """Async Streamable-HTTP MCP session over httpx with bearer auth."""

    def __init__(self, url: str, bearer: str, *, timeout: float = 60.0) -> None:
        self._url = url
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout), follow_redirects=False,
        )
        self._bearer = bearer
        self._session_id: str | None = None
        self._request_id = 0

    async def aclose(self) -> None:
        await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": ACCEPT,
            "Content-Type": "application/json",
            "Authorization": "Bearer " + self._bearer,
            "User-Agent": "ignition-mcp-g3-driver/1",
        }
        if ":" in self._bearer:
            # The value is an Ignition API token (name:key): the Gateway module
            # plane historically authenticates it via its dedicated header. Send
            # both; a JWT bearer (no colon) is unaffected.
            headers["X-Ignition-API-Token"] = self._bearer
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    async def _post(self, payload: dict[str, Any]) -> tuple[dict[str, Any] | None, httpx.Response]:
        response = await self._client.post(
            self._url, headers=self._headers(), content=json.dumps(payload).encode(),
        )
        session = response.headers.get("mcp-session-id")
        if session:
            self._session_id = session
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise ProbeError("MCP response exceeded the bounded size")
        if response.status_code >= 400:
            raise ProbeError(f"MCP endpoint returned HTTP {response.status_code}")
        decoded = parse_rpc_body(response.headers.get("content-type", ""), response.content)
        return decoded, response

    async def initialize(self) -> dict[str, Any]:
        result = await self.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "ignition-mcp-g3-driver", "version": "0.0.0"},
        })
        await self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return result

    async def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._request_id += 1
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": self._request_id, "method": method}
        if params is not None:
            payload["params"] = params
        decoded, _ = await self._post(payload)
        if decoded is None:
            raise ProbeError(f"{method} returned an empty response")
        if decoded.get("jsonrpc") != "2.0" or decoded.get("id") != self._request_id:
            raise ProbeError(f"{method} response identity mismatch")
        if "error" in decoded:
            raise ProbeError(f"{method} JSON-RPC error: {json.dumps(decoded['error'])[:400]}")
        result = decoded.get("result")
        if not isinstance(result, dict):
            raise ProbeError(f"{method} result was not an object")
        return result

    async def tools_list(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(20):
            params = {} if cursor is None else {"cursor": cursor}
            result = await self.request("tools/list", params)
            page = result.get("tools")
            if not isinstance(page, list) or any(not isinstance(item, dict) for item in page):
                raise ProbeError("tools/list returned no tools list")
            tools.extend(page)
            nxt = result.get("nextCursor")
            if not isinstance(nxt, str) or not nxt:
                return tools
            cursor = nxt
        raise ProbeError("tools/list needs more than 20 pages")

    async def tool_call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Returns the raw tools/call result object (never raises on isError)."""
        return await self.request("tools/call", {"name": name, "arguments": arguments})

    @property
    def last_request_id(self) -> int:
        """The id of the most recent request this session sent.

        A caller that cancels an in-flight call needs it: the D23 cancellation case
        sends ``notifications/cancelled`` for the request the peer is still working on.
        """

        return self._request_id

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Send one JSON-RPC notification; the peer answers a notification with no body."""

        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        await self._post(payload)

    async def call_raw(
        self, name: str, arguments: dict[str, Any],
    ) -> tuple[int, dict[str, Any]]:
        """``tools/call`` that also returns the request id.

        Unlike ``request``, a JSON-RPC error object is returned rather than raised: the
        cancellation case asserts the peer's own error code for a request it cancelled.
        """

        self._request_id += 1
        payload = {
            "jsonrpc": "2.0", "id": self._request_id, "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        decoded, _ = await self._post(payload)
        if decoded is None:
            raise ProbeError("tools/call returned an empty response")
        return self._request_id, decoded

    async def call_structured(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Success path only: structuredContent of a non-error tool result."""
        result = await self.tool_call(name, arguments)
        if result.get("isError"):
            raise ProbeError(f"tool {name} failed: {error_envelope(result)['code']}")
        structured = result.get("structuredContent")
        if not isinstance(structured, dict):
            raise ProbeError(f"tool {name} returned no structuredContent")
        return structured


def error_envelope(result: dict[str, Any]) -> dict[str, Any]:
    """The D06 error envelope carried in an isError tool result's text block."""
    for item in result.get("content", []):
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            try:
                decoded = json.loads(item["text"])
            except ValueError:
                continue
            if isinstance(decoded, dict) and "code" in decoded:
                return decoded
    raise ProbeError("isError tool result carried no parseable error envelope")
