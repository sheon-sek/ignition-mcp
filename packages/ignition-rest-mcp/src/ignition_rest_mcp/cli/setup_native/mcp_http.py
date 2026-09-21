"""Minimal bounded MCP Streamable HTTP JSON-RPC client for ``setup-native``.

Wire semantics mirror the Phase 2 probe (single POST per request, ``Accept:
application/json, text/event-stream``, optional ``Mcp-Session-Id`` echo, SSE
``data:`` decoding, 1 MiB response ceiling, JSON-RPC error = failure) but use
the package's ``httpx`` dependency.  ``doctor`` and ``verify`` are diagnostics,
not installers: ``initialize`` gets exactly one attempt under a bounded timeout
and never a readiness wait loop. Waiting for a starting Gateway belongs to the
live harness.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from ignition_rest_mcp.cli.setup_native.inputs import Endpoint

PROTOCOL_VERSION = "2025-06-18"
ACCEPT = "application/json, text/event-stream"
MAX_RESPONSE_BYTES = 1_048_576
MAX_LIST_PAGES = 20
INITIALIZE_TIMEOUT_SECONDS = 30.0
ERROR_BODY_SNIPPET = 160
METHOD_NOT_FOUND = -32601
INVALID_REQUEST = -32600
#: The live Gateway module answers unimplemented list capabilities (an empty
#: Prompt inventory omits the prompts capability, the G1 lesson confirmed live in
#: run 35588754132) with ``-32600 Invalid Request`` rather than ``-32601 Method
#: not found``.  For list methods both codes mean "this endpoint does not offer
#: that capability"; anywhere else ``-32600`` stays a hard failure.
LIST_METHODS = frozenset({"tools/list", "resources/list", "prompts/list"})
USER_AGENT = "ignition-mcp-setup-native"
#: An Ignition API token (``name:key``, the key being unpadded Base64URL).  The
#: Gateway module's MCP endpoint authenticates it through ``X-Ignition-API-Token``
#: and does not accept the bearer scheme; a value shaped like an API token is
#: therefore also sent in that header.  JWTs and opaque MCP tokens (no colon)
#: keep the pure bearer behavior.
API_TOKEN_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}:[A-Za-z0-9_-]{20,128}$")

class McpProbeError(RuntimeError):
    """The MCP endpoint failed a probe; the message is safe to report."""


class McpMethodNotFound(McpProbeError):
    """The endpoint answered ``method not found``; may be legitimate per capabilities."""

    def __init__(self, method: str, detail: str) -> None:
        super().__init__(f"{method} is not supported by the endpoint: {detail}")
        self.method = method


@dataclass(frozen=True, slots=True)
class ToolResult:
    """A ``tools/call`` outcome with the structured content passed through verbatim."""

    structured: dict[str, Any]
    text: str


@dataclass(slots=True)
class McpHttpClient:
    """One Streamable HTTP session: ``initialize``, list, read and call probes."""

    endpoint: Endpoint
    token: str | None = None
    timeout_seconds: float = 10.0
    transport: httpx.AsyncBaseTransport | None = None
    client_name: str = "ignition-mcp-setup-native"
    client_version: str = "0.1.0a0"
    session_id: str | None = None
    server_capabilities: dict[str, Any] = field(default_factory=dict, init=False)
    server_info: dict[str, Any] = field(default_factory=dict, init=False)
    protocol_version: str = field(default="", init=False)
    _request_id: int = field(default=0, init=False)
    _client: httpx.AsyncClient | None = field(default=None, init=False)

    async def __aenter__(self) -> McpHttpClient:
        await self._connect()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def _connect(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout_seconds),
                follow_redirects=False,
                transport=self.transport,
            )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def advertises(self, capability: str) -> bool:
        """Whether ``initialize`` advertised a server capability (``tools`` / ``resources`` / ``prompts``)."""

        return capability in self.server_capabilities

    async def initialize(self) -> dict[str, Any]:
        """Exactly one ``initialize`` attempt, then the ``initialized`` notification."""

        await self._connect()
        result = await self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": self.client_name, "version": self.client_version},
            },
            timeout_seconds=INITIALIZE_TIMEOUT_SECONDS,
        )
        capabilities = result.get("capabilities")
        self.server_capabilities = capabilities if isinstance(capabilities, dict) else {}
        info = result.get("serverInfo")
        self.server_info = info if isinstance(info, dict) else {}
        version = result.get("protocolVersion")
        self.protocol_version = version if isinstance(version, str) else ""
        await self.notify("notifications/initialized")
        return result

    async def reachability(self) -> str:
        """Cheap liveness probe of the endpoint URL; any HTTP answer counts as reachable."""

        await self._connect()
        client = self._require_client()
        probe_headers = {"Accept": ACCEPT, **self._auth_headers()}
        snippet = bytearray()
        try:
            async with client.stream("GET", self.endpoint.url, headers=probe_headers) as response:
                status = response.status_code
                try:
                    async for chunk in response.aiter_bytes():
                        snippet.extend(chunk[: max(0, ERROR_BODY_SNIPPET - len(snippet))])
                        if len(snippet) >= ERROR_BODY_SNIPPET:
                            break
                except httpx.HTTPError:
                    pass  # the status code already answered the reachability question
        except httpx.HTTPError as error:
            raise McpProbeError(f"endpoint unreachable: {_reason(error)}") from error
        if status < 400 or status in (404, 405, 406, 415):
            # A method or content-negotiation refusal still proves something is listening
            # (the live Gateway module answers the GET probe with 415, run 35589554998).
            return f"HTTP {status}"
        raise McpProbeError(
            f"endpoint answered HTTP {status}: {_redact(_snippet(bytes(snippet)), self.token or '')}"
        )

    async def request(
        self, method: str, params: dict[str, Any] | None = None, *, timeout_seconds: float | None = None
    ) -> dict[str, Any]:
        self._request_id += 1
        request_id = self._request_id
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            payload["params"] = params
        response = await self._post(payload, timeout_seconds=timeout_seconds)
        if response is None:
            raise McpProbeError(f"{method} returned an empty response")
        if response.get("jsonrpc") != "2.0" or response.get("id") != request_id:
            raise McpProbeError(f"{method} response identity mismatch")
        error = response.get("error")
        if isinstance(error, dict):
            code = error.get("code")
            detail = json.dumps(error, separators=(",", ":"), sort_keys=True)[:ERROR_BODY_SNIPPET]
            if code == METHOD_NOT_FOUND or (code == INVALID_REQUEST and method in LIST_METHODS):
                raise McpMethodNotFound(method, detail)
            raise McpProbeError(f"{method} failed: {_redact(detail, self.token or '')}")
        result = response.get("result")
        if not isinstance(result, dict):
            raise McpProbeError(f"{method} result was not a JSON object")
        return result

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        await self._post(payload, expect_result=False)

    async def list_names(self, method: str, key: str, identity: str) -> list[str]:
        """Complete (cursor-paginated) inventory of one list method as sorted identifiers."""

        items: list[str] = []
        cursor: str | None = None
        for _ in range(MAX_LIST_PAGES):
            params: dict[str, Any] = {} if cursor is None else {"cursor": cursor}
            result = await self.request(method, params)
            page = result.get(key)
            if not isinstance(page, list) or any(not isinstance(item, dict) for item in page):
                raise McpProbeError(f"{method} returned no {key} list")
            for item in page:
                name = item.get(identity)
                if not isinstance(name, str) or not name:
                    raise McpProbeError(f"{method} returned a {key[:-1] or key} entry without {identity}")
                items.append(name)
            nxt = result.get("nextCursor")
            if not isinstance(nxt, str) or not nxt:
                return sorted(items)
            if nxt == cursor:
                raise McpProbeError(f"{method} pagination cursor did not advance")
            cursor = nxt
        raise McpProbeError(f"{method} needs more than {MAX_LIST_PAGES} pages")

    async def tools_list(self) -> list[str]:
        return await self.list_names("tools/list", "tools", "name")

    async def resources_list(self) -> list[str]:
        return await self.list_names("resources/list", "resources", "uri")

    async def prompts_list(self) -> list[str]:
        return await self.list_names("prompts/list", "prompts", "name")

    async def resource_read(self, uri: str) -> str:
        """Text payload of one resource read; empty content is a probe failure."""

        result = await self.request("resources/read", {"uri": uri})
        contents = result.get("contents")
        if not isinstance(contents, list) or not contents or not isinstance(contents[0], dict):
            raise McpProbeError(f"resources/read returned no content for {uri}")
        entry = contents[0]
        blob = entry.get("text")
        if not isinstance(blob, str) or not blob:
            raise McpProbeError(f"resources/read returned no text content for {uri}")
        blob.encode("utf-8")
        return blob

    async def prompt_get(self, name: str) -> dict[str, Any]:
        result = await self.request("prompts/get", {"name": name})
        messages = result.get("messages")
        if not isinstance(messages, list) or not messages:
            raise McpProbeError(f"prompts/get returned no messages for {name}")
        return result

    async def tool_call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        result = await self.request("tools/call", {"name": name, "arguments": arguments})
        structured = result.get("structuredContent")
        text = " ".join(
            str(item.get("text", ""))
            for item in result.get("content", [])
            if isinstance(item, dict)
        )[:ERROR_BODY_SNIPPET]
        if result.get("isError") is True:
            raise McpProbeError(f"tools/call {name} returned an error result: {_redact(text, self.token or '')}")
        return ToolResult(structured=structured if isinstance(structured, dict) else {}, text=text)

    def _auth_headers(self) -> dict[str, str]:
        """Bearer always; plus the Gateway API-token header when the shape says so."""

        if not self.token:
            return {}
        headers = {"Authorization": f"Bearer {self.token}"}
        if API_TOKEN_RE.fullmatch(self.token):
            headers["X-Ignition-API-Token"] = self.token
        return headers

    async def _post(
        self, payload: dict[str, Any], *, expect_result: bool = True, timeout_seconds: float | None = None
    ) -> dict[str, Any] | None:
        await self._connect()
        client = self._require_client()
        headers = {
            "Accept": ACCEPT,
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        headers.update(self._auth_headers())
        request_body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        timeout = timeout_seconds if timeout_seconds is not None else self.timeout_seconds
        method = str(payload.get("method", ""))
        raw = bytearray()
        try:
            async with client.stream(
                "POST",
                self.endpoint.url,
                content=request_body,
                headers=headers,
                timeout=httpx.Timeout(timeout),
            ) as response:
                session = response.headers.get("mcp-session-id")
                if session:
                    self.session_id = session
                content_type = response.headers.get("content-type", "")
                status = response.status_code
                declared = response.headers.get("content-length")
                if declared is not None and declared.isdigit() and int(declared) > MAX_RESPONSE_BYTES:
                    raise McpProbeError(
                        f"{method} response announces {declared} bytes; "
                        f"the bound is {MAX_RESPONSE_BYTES} bytes"
                    )
                async for chunk in response.aiter_bytes():
                    if len(raw) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise McpProbeError(f"{method} response exceeded {MAX_RESPONSE_BYTES} bytes")
                    raw.extend(chunk)
        except httpx.HTTPError as error:
            raise McpProbeError(f"MCP transport failed: {_reason(error)}") from error
        response_body = bytes(raw)
        if not expect_result:
            if status >= 400:
                raise McpProbeError(f"{method} notification failed: HTTP {status}")
            return None
        if status >= 400:
            raise McpProbeError(
                f"{method} returned HTTP {status}: {_redact(_snippet(response_body), self.token or '')}"
            )
        if not response_body.strip():
            return None
        try:
            text = response_body.decode("utf-8")
        except UnicodeDecodeError as error:
            raise McpProbeError(f"{method} response is not valid UTF-8: {_reason(error)}") from error
        return _decode_rpc(content_type, text)

    def _require_client(self) -> httpx.AsyncClient:
        if self._client is None:  # pragma: no cover - _connect always runs first
            raise McpProbeError("MCP client is not connected")
        return self._client


def _decode_rpc(content_type: str, text: str) -> dict[str, Any]:
    if "text/event-stream" in content_type:
        events: list[dict[str, Any]] = []
        for line in text.splitlines():
            if not line.startswith("data:"):
                continue
            try:
                decoded = json.loads(line[5:].strip())
            except ValueError as error:
                raise McpProbeError(f"SSE data line is not JSON: {_reason(error)}") from error
            if isinstance(decoded, dict):
                events.append(decoded)
        if not events:
            raise McpProbeError("SSE response carried no JSON data event")
        return events[-1]
    try:
        decoded = json.loads(text)
    except ValueError as error:
        raise McpProbeError(f"response body is not JSON: {_reason(error)}") from error
    if not isinstance(decoded, dict):
        raise McpProbeError("JSON-RPC response was not an object")
    return decoded


def _snippet(content: bytes) -> str:
    """One report-safe line: decoded with replacements, whitespace collapsed, truncated."""

    text = content[:ERROR_BODY_SNIPPET].decode("utf-8", errors="replace")
    return " ".join(text.split())


def _reason(error: BaseException) -> str:
    joined = " ".join(str(arg) for arg in error.args if arg)
    return (joined or type(error).__name__)[:ERROR_BODY_SNIPPET]


def _redact(text: str, *secrets: str) -> str:
    safe = text
    for secret in secrets:
        if secret:
            safe = safe.replace(secret, "[redacted]")
    return safe
