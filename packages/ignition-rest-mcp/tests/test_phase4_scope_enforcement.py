"""Phase 4 milestone 4c (ticket #13): central D07 scope enforcement.

D07 requires scope-based authorization to be centralized in middleware: the
caller sees only the components its scopes allow, and the check repeats at call
time. These tests drive the real server — the in-memory client for the
dispatch-order proof, and a named-token Streamable-HTTP session against the
recorded Gateway for the credential path.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
from typing import Any

import pytest
from fastmcp import Client
from starlette.testclient import TestClient

import ignition_rest_mcp.server as server_module
from ignition_rest_mcp.audit.sink import AuditWriteError, SqliteAuditSink
from ignition_rest_mcp.authorization import SCOPE_PERMISSION_CLASS, declared_scope, scope_tag
from ignition_rest_mcp.client.gateway import GatewayClient
from ignition_rest_mcp.config import CANONICAL_SCOPES, StaticToken
from ignition_rest_mcp.storage.database import Database
from ignition_rest_mcp.storage.schema import AUDIT_DDL
from test_config import _settings

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tests/harness"))

from recorded_gateway import API_TOKEN, RecordedGateway  # noqa: E402

READ = "ignition.read"
CONFIG = "ignition.config"
ACCEPT = "application/json, text/event-stream"
PROTOCOL_VERSION = "2025-06-18"
RESOURCE_URIS = ("ignition://gateway/capabilities", "ignition://gateway/openapi-info")

#: The Tools a caller holding only `ignition.read` sees when the recorded Gateway
#: advertises the whole Phase 3 read surface and both deployment gates are on.
READ_INVENTORY = frozenset({
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


def _envelope(result: dict[str, Any]) -> dict[str, Any]:
    """The D06 error envelope carried by an ``isError`` tool result."""

    assert result.get("isError") is True, result
    for item in result.get("content", ()):
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            decoded = json.loads(item["text"])
            if isinstance(decoded, dict) and "code" in decoded:
                return decoded
    raise AssertionError(f"no structured error envelope in {result!r}")


def _result_body(result: Any) -> dict[str, Any]:
    """A FastMCP in-memory tool result, as a plain JSON-RPC-shaped dict."""

    return {"isError": bool(result.is_error), "content": [item.model_dump() for item in result.content]}


# ------------------------------------------------------------------ tag vocabulary


@pytest.mark.parametrize(("tags", "expected"), [
    ({"read", scope_tag(READ)}, READ),
    ({"read"}, None),
    (set(), None),
    ({"scope:ignition.write"}, None),
    ({scope_tag(READ), scope_tag(CONFIG)}, None),
    ({"read", "scope:ignition.read", "capability:gateway_info"}, READ),
])
def test_declared_scope_resolves_only_one_canonical_scope(tags: set[str], expected: str | None) -> None:
    assert declared_scope(tags) == expected


def test_scope_to_operation_class_matches_the_permission_contract() -> None:
    contract = json.loads((ROOT / "contracts/shared/permission-classes.json").read_text(encoding="utf-8"))

    assert SCOPE_PERMISSION_CLASS == {
        entry["externalScope"]: name for name, entry in contract["classes"].items()
    }


def test_every_registered_component_declares_the_contract_scope() -> None:
    """The tag is the runtime declaration; ``contracts/`` is the authority."""

    server = server_module.create_server(_settings(auth_mode="none"))

    async def scenario() -> None:
        contracts = sorted((ROOT / "contracts/tools/rest").glob("*.contract.json"))
        assert contracts, "the REST Tool contracts must be present"
        for path in contracts:
            contract = json.loads(path.read_text(encoding="utf-8"))
            tool = await server.get_tool(contract["name"])
            assert tool is not None, f"{contract['name']} is not registered"
            assert scope_tag(contract["requiredScope"]) in tool.tags, contract["name"]
            assert declared_scope(tool.tags) in CANONICAL_SCOPES, contract["name"]
        for uri in RESOURCE_URIS:
            resource = await server.get_resource(uri)
            assert resource is not None, uri
            assert declared_scope(resource.tags) == READ, uri

    asyncio.run(scenario())


# ------------------------------------------------------------------ in-memory dispatch order


def test_a_principal_without_ignition_config_never_dispatches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """`auth=none` is read-only, so a CONFIG Tool is filtered from discovery and
    refused at call time — its body never runs."""

    _stub_gateway(monkeypatch)
    server = server_module.create_server(_settings(auth_mode="none", data_dir=str(tmp_path)))
    calls: list[str] = []

    @server.tool(name="config_probe", tags={"config", scope_tag(CONFIG)})
    async def config_probe() -> str:
        calls.append("ran")
        return "ran"

    async def scenario() -> None:
        async with Client(server) as client:
            listed = frozenset(tool.name for tool in await client.list_tools())
            assert "config_probe" not in listed
            assert "gateway_info" in listed
            denied = await client.call_tool("config_probe", {}, raise_on_error=False)
            assert _envelope(_result_body(denied))["code"] == "permission_denied"

    asyncio.run(scenario())
    assert calls == [], "a denied Tool must never reach its handler"


def test_a_denied_tool_call_leaves_one_audited_decision_row(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """D18: a scope denial is a durable audit decision, not only a log line.

    The row names the verified principal, carries the denied Tool's effect class
    and shares one correlation ID with the caller's error envelope. Nothing is
    dispatched, so no attempt row may appear.
    """

    _stub_gateway(monkeypatch)
    server = server_module.create_server(_settings(auth_mode="none", data_dir=str(tmp_path)))
    calls: list[str] = []

    @server.tool(name="config_probe", tags={"config", scope_tag(CONFIG)})
    async def config_probe() -> str:
        calls.append("ran")
        return "ran"

    async def scenario() -> tuple[dict[str, Any], list[dict[str, Any]]]:
        async with Client(server) as client:
            denied = await client.call_tool("config_probe", {}, raise_on_error=False)
            envelope = _envelope(_result_body(denied))
        return envelope, await _audit_rows(tmp_path)

    envelope, rows = asyncio.run(scenario())

    assert calls == []
    assert envelope["code"] == "permission_denied"
    assert [
        (row["tool"], row["phase"], row["outcome"], row["operation_class"], row["actor_key"])
        for row in rows
    ] == [
        ("config_probe", "decision", "denied:authz-scope:missing-scope:ignition.config", "CONFIG", "none:test"),
    ]
    assert rows[0]["correlation_id"] == envelope["correlationId"]


def test_a_denial_survives_an_unwritable_audit_sink(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """A storage failure must never turn a denial into an allow (D18 rule)."""

    _stub_gateway(monkeypatch)
    server = server_module.create_server(_settings(auth_mode="none", data_dir=str(tmp_path)))
    calls: list[str] = []

    @server.tool(name="config_probe", tags={"config", scope_tag(CONFIG)})
    async def config_probe() -> str:
        calls.append("ran")
        return "ran"

    async def failing_write(self: SqliteAuditSink, row: Any) -> None:
        raise AuditWriteError("audit storage is unavailable")

    monkeypatch.setattr(SqliteAuditSink, "write", failing_write)

    async def scenario() -> tuple[dict[str, Any], list[dict[str, Any]]]:
        async with Client(server) as client:
            denied = await client.call_tool("config_probe", {}, raise_on_error=False)
            envelope = _envelope(_result_body(denied))
        return envelope, await _audit_rows(tmp_path)

    envelope, rows = asyncio.run(scenario())

    assert calls == []
    assert envelope["code"] == "permission_denied"
    assert rows == []


async def _audit_rows(tmp_path: Path) -> list[dict[str, Any]]:
    columns = ("correlation_id", "tool", "actor_key", "operation_class", "phase", "outcome")
    db = Database("audit", tmp_path / "audit.db", AUDIT_DDL)
    await db.open()
    try:
        rows = await db.run(
            lambda conn: conn.execute(f"SELECT {', '.join(columns)} FROM audit_log ORDER BY seq").fetchall()
        )
        return [dict(zip(columns, row, strict=True)) for row in rows]
    finally:
        await db.close()


def test_capability_gated_tools_keep_the_router_refusal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Scope authorization must not mask capability gating with an authz denial."""

    _stub_gateway(monkeypatch, with_pipelines=False)
    server = server_module.create_server(_settings(auth_mode="none", data_dir=str(tmp_path)))

    async def scenario() -> None:
        async with Client(server) as client:
            listed = frozenset(tool.name for tool in await client.list_tools())
            assert "alarm_pipeline_status" not in listed
            result = await client.call_tool("alarm_pipeline_status", {"path": "x"}, raise_on_error=False)
            assert result.is_error
            assert "permission_denied" not in str(result.content)

    asyncio.run(scenario())


# ------------------------------------------------------------------ named tokens over HTTP


class _Session:
    """A minimal Streamable-HTTP MCP session carrying one bearer credential."""

    def __init__(self, http: TestClient, credential: str) -> None:
        self._http = http
        self._headers = {
            "Authorization": "Bearer " + credential,
            "Accept": ACCEPT,
            "Content-Type": "application/json",
        }
        self._request_id = 0
        self.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION, "capabilities": {},
            "clientInfo": {"name": "p4-auth-test", "version": "0"},
        })
        self._http.post(
            "/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=self._headers,
        )

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._request_id += 1
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": self._request_id, "method": method}
        if params is not None:
            payload["params"] = params
        response = self._http.post("/mcp", json=payload, headers=self._headers)
        assert response.status_code == 200, response.text
        session = response.headers.get("mcp-session-id")
        if session:
            self._headers["Mcp-Session-Id"] = session
        decoded = _decode(response)
        assert decoded.get("id") == self._request_id
        assert "error" not in decoded, decoded
        return decoded["result"]

    def tools(self) -> frozenset[str]:
        result = self.request("tools/list", {})
        return frozenset(str(tool["name"]) for tool in result["tools"])

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self.request("tools/call", {"name": name, "arguments": arguments})

    def read_resource(self, uri: str) -> dict[str, Any]:
        payload = {"jsonrpc": "2.0", "id": 99, "method": "resources/read", "params": {"uri": uri}}
        response = self._http.post("/mcp", json=payload, headers=self._headers)
        assert response.status_code == 200, response.text
        return _decode(response)

    def list_resources(self) -> set[str]:
        result = self.request("resources/list", {})
        return {str(resource["uri"]) for resource in result["resources"]}


def _decode(response: Any) -> dict[str, Any]:
    if "text/event-stream" in response.headers.get("content-type", ""):
        events = [
            json.loads(line[5:].strip())
            for line in response.text.splitlines()
            if line.startswith("data:")
        ]
        assert events, response.text
        return events[-1]
    return json.loads(response.text)


def _named_tokens(*tokens: StaticToken, **overrides: Any) -> Any:
    intervals = {
        "watcher_interval_seconds": 3600.0, "retention_interval_seconds": 3600.0,
        "storage_probe_interval_seconds": 3600.0, "artifact_cleanup_interval_seconds": 3600.0,
    }
    return _settings(auth_mode="static-token", static_tokens=tokens, **{**intervals, **overrides})


def test_named_token_scopes_drive_discovery_and_call_time_enforcement(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    with RecordedGateway() as gateway:
        settings = _named_tokens(
            StaticToken(name="reader", token="reader-secret", scopes=(READ,)),
            StaticToken(name="config-agent", token="cfg-secret", scopes=(CONFIG,)),
            data_dir=str(tmp_path), gateway_url=gateway.base_url, gateway_api_token=API_TOKEN,
            sensitive_exports_enabled=True,
        )
        server = server_module.create_server(settings)
        calls: list[str] = []

        @server.tool(name="config_probe", tags={"config", scope_tag(CONFIG)})
        async def config_probe() -> str:
            calls.append("ran")
            return "ran"

        with TestClient(server.http_app()) as http:
            reader = _Session(http, "reader-secret")
            configurator = _Session(http, "cfg-secret")

            assert reader.tools() == READ_INVENTORY
            assert reader.list_resources() == set(RESOURCE_URIS)
            assert configurator.tools() == frozenset({"config_probe"})
            assert configurator.list_resources() == set()

            # A CONFIG-only credential is denied before dispatch: no Gateway traffic.
            dispatched = len(gateway.requests)
            denied = configurator.call("gateway_info", {})
            assert _envelope(denied)["code"] == "permission_denied"
            assert len(gateway.requests) == dispatched, "a denied call must not reach the Gateway"

            # The mirror case: a read-only credential may not reach a CONFIG Tool.
            denied_config = reader.call("config_probe", {})
            assert _envelope(denied_config)["code"] == "permission_denied"
            assert calls == []

            allowed = configurator.call("config_probe", {})
            assert allowed.get("isError") is not True
            assert calls == ["ran"]

            # Resources are reads too, and a denial names the missing scope.
            assert "permission_denied" in json.dumps(configurator.read_resource(RESOURCE_URIS[0]))
            contents = reader.read_resource(RESOURCE_URIS[0])["result"]["contents"]
            assert json.loads(contents[0]["text"])["state"] == "READY"

            for body in (str(denied), str(denied_config), str(allowed)):
                assert "cfg-secret" not in body and "reader-secret" not in body

            # Both denials are durable audit decisions sharing the caller's
            # correlation ID, with the token name as the Mutation principal.
            rows = asyncio.run(_audit_rows(tmp_path))
            assert [
                (row["tool"], row["outcome"], row["actor_key"], row["correlation_id"])
                for row in rows
            ] == [
                ("gateway_info", "denied:authz-scope:missing-scope:ignition.read",
                 "static-token:config-agent", _envelope(denied)["correlationId"]),
                ("config_probe", "denied:authz-scope:missing-scope:ignition.config",
                 "static-token:reader", _envelope(denied_config)["correlationId"]),
            ]

            # Denials are logged by principal name, and the credential never is.
            logged = "\n".join(
                f"{record.getMessage()} {record.__dict__}" for record in caplog.records
            )
            assert "denied:missing-scope:ignition.read" in logged
            assert "static-token:config-agent" in logged
            assert "cfg-secret" not in logged and "reader-secret" not in logged


def test_an_unknown_credential_is_refused_before_the_mcp_layer(tmp_path: Path) -> None:
    with RecordedGateway() as gateway:
        settings = _named_tokens(
            StaticToken(name="reader", token="reader-secret", scopes=(READ,)),
            data_dir=str(tmp_path), gateway_url=gateway.base_url, gateway_api_token=API_TOKEN,
        )
        server = server_module.create_server(settings)
        with TestClient(server.http_app()) as http:
            for headers in (
                {"Authorization": "Bearer wrong-secret"},
                {},  # no credential at all: never treated as the anonymous read-only principal
            ):
                response = http.post(
                    "/mcp",
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                    headers={"Accept": ACCEPT, "Content-Type": "application/json", **headers},
                )
                assert response.status_code == 401, (headers, response.status_code)
                assert "wrong-secret" not in response.text


# ------------------------------------------------------------------ stubs


def _stub_gateway(monkeypatch: pytest.MonkeyPatch, *, with_pipelines: bool = True) -> None:
    async def info(self: GatewayClient, context: Any = None) -> dict[str, str]:
        return {"ignitionVersion": "8.3.8"}

    async def modules(self: GatewayClient, context: Any = None) -> dict[str, list[Any]]:
        return {"items": []}

    async def openapi(self: GatewayClient) -> bytes:
        paths: dict[str, Any] = {
            "/data/api/v1/gateway-info": {"get": {}},
            "/data/api/v1/projects/list": {"get": {}},
        }
        if with_pipelines:
            paths["/data/alarm-notification/api/v1/pipelines"] = {"get": {}}
            paths["/data/alarm-notification/api/v1/pipeline"] = {"get": {}}
        return json.dumps({"paths": paths}).encode()

    monkeypatch.setattr(GatewayClient, "gateway_info", info)
    monkeypatch.setattr(GatewayClient, "healthy_modules", modules)
    monkeypatch.setattr(GatewayClient, "openapi", openapi)
