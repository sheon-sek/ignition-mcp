"""Small deterministic MCP client used by the G2 probe."""
from __future__ import annotations

import json
import time
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

PROTOCOL_VERSION = "2025-06-18"
ACCEPT = "application/json, text/event-stream"
MAX_RESPONSE_BYTES = 1_048_576


class ProbeError(RuntimeError):
    pass


class McpClient:
    def __init__(self, url: str, *, timeout: float = 20.0, api_token: str | None = None) -> None:
        self.url = url
        self.timeout = timeout
        self.api_token = api_token
        self.session_id: str | None = None
        self.request_id = 0

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": ACCEPT,
            "Content-Type": "application/json",
            "User-Agent": "ignition-mcp-g2-probe/1",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        if self.api_token:
            headers["X-Ignition-API-Token"] = self.api_token
        return headers

    def _post(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any] | None]:
        request = Request(
            self.url,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise ProbeError(f"MCP response exceeded {MAX_RESPONSE_BYTES} bytes")
                content_type = response.headers.get("Content-Type", "")
                session = response.headers.get("Mcp-Session-Id")
                if session:
                    self.session_id = session
                status = response.status
        except HTTPError as error:
            body = error.read(MAX_RESPONSE_BYTES + 1)
            raise ProbeError(
                f"HTTP {error.code} from {self.url}: {body[:2000].decode('utf-8', errors='replace')}"
            ) from error
        except (OSError, TimeoutError) as error:
            raise ProbeError(f"MCP transport unavailable: {type(error).__name__}: {error}") from error

        if not body.strip():
            return status, None
        text = body.decode("utf-8")
        if "text/event-stream" in content_type:
            events: list[dict[str, Any]] = []
            for line in text.splitlines():
                if line.startswith("data:"):
                    decoded = json.loads(line[5:].strip())
                    if isinstance(decoded, dict):
                        events.append(decoded)
            if not events:
                raise ProbeError("SSE response had no JSON data event")
            return status, events[-1]
        decoded = json.loads(text)
        if not isinstance(decoded, dict):
            raise ProbeError("JSON-RPC response must be an object")
        return status, decoded

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.request_id += 1
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": self.request_id, "method": method}
        if params is not None:
            payload["params"] = params
        _, response = self._post(payload)
        if response is None:
            raise ProbeError(f"{method} returned no response")
        if response.get("jsonrpc") != "2.0" or response.get("id") != self.request_id:
            raise ProbeError(f"{method} JSON-RPC response identity mismatch")
        if "error" in response:
            raise ProbeError(f"{method} JSON-RPC error: {response['error']}")
        if not isinstance(response.get("result"), dict):
            raise ProbeError(f"{method} result must be an object")
        return response

    def initialize(self, *, startup_timeout: float = 240.0) -> dict[str, Any]:
        deadline = time.monotonic() + startup_timeout
        last_error = ""
        while time.monotonic() < deadline:
            self.session_id = None
            try:
                response = self.call(
                    "initialize",
                    {
                        "protocolVersion": PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "ignition-mcp-g2-probe", "version": "1.0.0"},
                    },
                )
                self.notify_initialized()
                return response
            except ProbeError as error:
                last_error = str(error)
                time.sleep(2.0)
        raise ProbeError(f"initialize did not become ready: {last_error}")

    def notify_initialized(self) -> None:
        try:
            status, response = self._post(
                {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
            )
        except ProbeError:
            # New stateless FastMCP negotiation may not need this notification.
            return
        if status not in {200, 202} or (response is not None and "error" in response):
            raise ProbeError("initialized notification failed")


def list_result(response: dict[str, Any], key: str) -> list[dict[str, Any]]:
    result = response.get("result")
    value = result.get(key) if isinstance(result, dict) else None
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ProbeError(f"missing result.{key}")
    return value
