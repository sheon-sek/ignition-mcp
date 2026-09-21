"""Minimal synchronous MCP Streamable-HTTP client for the Phase 4 probe harness.

It talks to the Module-hosted Runtime MCP endpoint with the CI API token, so the
harness exercises the same wire path a real agent would use. Responses may be
plain JSON or an SSE stream; both are accepted, and every response is capped.
"""

from __future__ import annotations

import json
from typing import Any
import urllib.error
import urllib.request

PROTOCOL_VERSION = "2025-06-18"
ACCEPT = "application/json, text/event-stream"
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class McpError(RuntimeError):
    """The MCP endpoint failed, or returned an unusable/error result."""


def _parse_body(content_type: str, payload: bytes) -> dict[str, Any] | None:
    text = payload.decode("utf-8", errors="replace")
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
        raise McpError("MCP response was not an object")
    return decoded


class McpClient:
    def __init__(self, url: str, token: str, *, timeout: float = 180.0) -> None:
        self._url = url
        self._token = token
        self._timeout = timeout
        self._session_id: str | None = None
        self._request_id = 0

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": ACCEPT,
            "Content-Type": "application/json",
            "Authorization": "Bearer " + self._token,
            "X-Ignition-API-Token": self._token,
            "User-Agent": "ignition-mcp-p4-driver/1",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    def _post(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        request_object = urllib.request.Request(
            self._url, data=json.dumps(payload).encode("utf-8"), method="POST",
        )
        for name, value in self._headers().items():
            request_object.add_header(name, value)
        try:
            with urllib.request.urlopen(request_object, timeout=self._timeout) as response:
                content_type = response.headers.get("Content-Type", "")
                session = response.headers.get("Mcp-Session-Id")
                body = response.read(MAX_RESPONSE_BYTES + 1)
                status = int(response.status)
        except urllib.error.HTTPError as error:
            detail = error.read(2048).decode("utf-8", errors="replace")
            raise McpError(f"MCP endpoint returned HTTP {error.code}: {detail[:400]}") from error
        except urllib.error.URLError as error:
            raise McpError(f"MCP endpoint unreachable: {error}") from error
        if status >= 400:
            raise McpError(f"MCP endpoint returned HTTP {status}")
        if len(body) > MAX_RESPONSE_BYTES:
            raise McpError("MCP response exceeded the bounded size")
        if session:
            self._session_id = session
        return _parse_body(content_type, body)

    def initialize(self) -> dict[str, Any]:
        result = self.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "ignition-mcp-p4-driver", "version": "0.0.0"},
        })
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return result

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._request_id += 1
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": self._request_id, "method": method}
        if params is not None:
            payload["params"] = params
        decoded = self._post(payload)
        if decoded is None:
            raise McpError(f"{method} returned an empty response")
        if decoded.get("id") != self._request_id:
            raise McpError(f"{method} response identity mismatch")
        if "error" in decoded:
            raise McpError(f"{method} JSON-RPC error: {json.dumps(decoded['error'])[:400]}")
        result = decoded.get("result")
        if not isinstance(result, dict):
            raise McpError(f"{method} result was not an object")
        return result

    def tools_list(self) -> list[str]:
        tools: list[str] = []
        cursor: str | None = None
        for _page in range(20):
            params = {} if cursor is None else {"cursor": cursor}
            result = self.request("tools/list", params)
            page = result.get("tools")
            if not isinstance(page, list):
                raise McpError("tools/list returned no tools list")
            for item in page:
                if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                    raise McpError("tools/list returned a malformed tool")
                tools.append(item["name"])
            cursor = result.get("nextCursor")
            if not isinstance(cursor, str) or not cursor:
                return tools
        raise McpError("tools/list needed more than 20 pages")

    def tool_call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = self.request("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise McpError(f"tool {name} returned isError: {json.dumps(result)[:600]}")
        return result

    def structured(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = self.tool_call(name, arguments)
        structured = result.get("structuredContent")
        if not isinstance(structured, dict):
            raise McpError(f"tool {name} returned no structuredContent")
        return structured
