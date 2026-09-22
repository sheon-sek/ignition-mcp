"""Shared helpers for the Phase 4 config-Mutation tests.

Every case in the Phase 4 REST mutation modules drives the real server against the
recorded Gateway (fixture first) over Streamable HTTP, so an assertion is always an
MCP-visible result or an observed Gateway request, never an internal call. The pieces
that do that — the credential-carrying session, the deployment settings, the seeded
config resources, and the audit and operation-record readers — live here so the
mutation modules share one implementation.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from starlette.testclient import TestClient

from ignition_rest_mcp.config import StaticToken
from ignition_rest_mcp.storage.database import Database
from ignition_rest_mcp.storage.records import OperationRecordStore
from ignition_rest_mcp.storage.schema import AUDIT_DDL, STATE_DDL
from test_config import _settings

READ = "ignition.read"
CONFIG = "ignition.config"
CONTROL = "ignition.control"
ADMIN = "ignition.admin"

#: An allowed non-singleton resource type, and the names the cases give its resources.
PROFILE = "ignition/audit-profile"
RESOURCE = "MCP_CI_AUDIT"
OTHER_RESOURCE = "MCP_CI_AUDIT_OTHER"
CREATED_RESOURCE = "MCP_CI_AUDIT_CREATED"
RENAMED_RESOURCE = "MCP_CI_AUDIT_RENAMED"
SINGLETON_TYPE = "ignition/cobranding"
SINGLETON_NAME = "cobranding"
#: A *refused* resource type (D30 §5) with the name the live Gateway really holds.
TOKEN_TYPE = "ignition/api-token"
REFUSED_NAME = "ignition-mcp-ci"

#: D30 owner ruling 5: the one collection a config Mutation addresses. The cases seed
#: every Target in it and keep a same-named look-alike in ``OTHER_COLLECTION``, so a
#: change that reached the wrong resource would be visible in the fixture's state.
CORE_COLLECTION = "core"
OTHER_COLLECTION = "custom"

UPDATE_TOOL = "config_resource_update"
CREATE_TOOL = "config_resource_create"
DELETE_TOOL = "config_resource_delete"
RENAME_TOOL = "config_resource_rename"
IMPORT_TOOL = "project_import"
TAG_IMPORT_TOOL = "tag_config_import"
#: D26 milestone 4c's CONTROL Mutation Tool (ticket #18): its Target is an exact
#: Alarm Notification Pipeline path, and it is gated by the CONTROL class.
ALARM_CANCEL_TOOL = "alarm_pipeline_cancel"
#: D26 ticket #19: the artifact removal is a CONFIG-class REST Mutation too, but it
#: has no Gateway route at all (D30 drops the artifact HTTP route), so its discovery
#: follows the class gate alone.
ARTIFACT_DELETE_TOOL = "artifact_delete"
#: Every Phase 4 *CONFIG*-class REST Mutation Tool, in the order the milestone
#: introduced them. The class decides discovery and scope, so the modules that pin an
#: inventory use the lane they actually enable.
CONFIG_MUTATION_TOOLS = (
    UPDATE_TOOL, CREATE_TOOL, DELETE_TOOL, RENAME_TOOL, IMPORT_TOOL, TAG_IMPORT_TOOL,
    ARTIFACT_DELETE_TOOL,
)
CONTROL_MUTATION_TOOLS = (ALARM_CANCEL_TOOL,)
MUTATION_TOOLS = CONFIG_MUTATION_TOOLS + CONTROL_MUTATION_TOOLS

ACCEPT = "application/json, text/event-stream"
PROTOCOL_VERSION = "2025-06-18"

#: The effective REST inventory with every mutation class disabled (D07 discovery).
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
    "artifact_list",
    "artifact_info",
    "operation_diagnose",
})


def read_settings(**overrides: Any) -> Any:
    values: dict[str, Any] = {"auth_mode": "none"}
    values.update(overrides)
    return _settings(**values)


def mutation_settings(
    *operations: str, targets: dict[str, tuple[str, ...]] | None = None, **overrides: Any,
) -> Any:
    """Deployment settings with the named mutation operations enabled.

    Defaults to every Phase 4 CONFIG Mutation Tool, with the Target list these modules
    share: the allowlisted resources and the singleton. ``OTHER_RESOURCE`` is
    deliberately *not* allowlisted, so a Target-allowlist denial case only has to name
    the resource it refuses.

    Three named credentials are always configured, because D07 assigns scope by
    operation effect: ``reader-secret`` (read only), ``cfg-secret`` (read + config) and
    ``op-secret`` (read + control). A CONTROL Mutation is therefore unreachable with
    the config credential and vice versa, whatever the class gates say.

    A fourth, ``adm-secret`` (read + config + ``ignition.admin``), is configured for the
    artifacts D30 §6 lets an administrator remove: scope membership is the only rule
    (D07), so an admin credential that also holds the operation's own scope is what
    reaches those Tools.
    """

    enabled = operations or CONFIG_MUTATION_TOOLS
    allowed = (
        f"{PROFILE}/{RESOURCE}",
        f"{PROFILE}/{CREATED_RESOURCE}",
        f"{PROFILE}/{RENAMED_RESOURCE}",
        SINGLETON_TYPE,
    )
    values: dict[str, Any] = {
        "auth_mode": "static-token",
        "static_tokens": (
            StaticToken(name="reader", token="reader-secret", scopes=(READ,)),
            StaticToken(name="config-agent", token="cfg-secret", scopes=(READ, CONFIG)),
            StaticToken(name="operator-agent", token="op-secret", scopes=(READ, CONTROL)),
            StaticToken(name="admin-agent", token="adm-secret", scopes=(READ, CONFIG, ADMIN)),
        ),
        "config_mutation_enabled": True,
        "mutation_operations": tuple(enabled),
        "mutation_targets": (
            targets if targets is not None else {tool: allowed for tool in enabled}
        ),
    }
    values.update(overrides)
    return _settings(**values)


def seed_config_resources(gateway: Any) -> None:
    """The config resources every case in these modules needs.

    An allowlisted update/delete/rename Target in ``core``, a look-alike of the same
    name in another collection, a second resource of the same type the Target
    allowlist does *not* name, the refused API token the live CI Gateway really holds,
    and an allowed singleton. Every Mutation Target is seeded in ``core``, which is
    the only collection a config Mutation addresses (D30 owner ruling 5).
    """

    gateway.seed_resource(
        PROFILE, RESOURCE, collection=CORE_COLLECTION,
        config={"profile": {"type": "local", "retentionDays": 14}, "settings": {}},
        description="CI audit profile",
    )
    gateway.seed_resource(
        PROFILE, RESOURCE, collection=OTHER_COLLECTION,
        config={"profile": {"type": "local", "retentionDays": 3}},
        description="same name, other collection",
    )
    gateway.seed_resource(
        PROFILE, OTHER_RESOURCE, collection=CORE_COLLECTION,
        config={"profile": {"type": "local"}, "settings": {}},
        description="CI audit profile (allowlist control)",
    )
    gateway.seed_resource(
        TOKEN_TYPE, REFUSED_NAME, collection=CORE_COLLECTION,
        config={"profile": {"type": "basic-token"}, "settings": {"tokenHash": "<redacted>"}},
        description="Disposable CI-only API token",
    )
    gateway.seed_resource(
        SINGLETON_TYPE, SINGLETON_NAME, collection=CORE_COLLECTION,
        config={"enabled": True}, description="CI branding",
    )


class Session:
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
            "clientInfo": {"name": "p4-mutation-test", "version": "0"},
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
        decoded = decode(response)
        assert "error" not in decoded, decoded
        return decoded["result"]

    def tools(self) -> frozenset[str]:
        return frozenset(str(tool["name"]) for tool in self.request("tools/list", {})["tools"])

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self.request("tools/call", {"name": name, "arguments": arguments})

    def read(self, resource_type: str, name: str, collection: str = "") -> dict[str, Any]:
        return self.call("config_resource_get", {
            "resourceType": resource_type, "name": name,
            "collection": collection, "defaultIfUndefined": False,
        })


def decode(response: Any) -> dict[str, Any]:
    if "text/event-stream" in response.headers.get("content-type", ""):
        events = [
            json.loads(line[5:].strip())
            for line in response.text.splitlines()
            if line.startswith("data:")
        ]
        assert events, response.text
        return events[-1]
    return json.loads(response.text)


def structured(result: dict[str, Any]) -> dict[str, Any]:
    assert result.get("isError") is not True, result
    assert isinstance(result.get("structuredContent"), dict), result
    return result["structuredContent"]


def envelope(result: dict[str, Any]) -> dict[str, Any]:
    assert result.get("isError") is True, result
    for item in result.get("content", ()):
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            decoded = json.loads(item["text"])
            if isinstance(decoded, dict) and "code" in decoded:
                return decoded
    raise AssertionError(f"no structured error envelope in {result!r}")


def write_requests(gateway: Any, method: str) -> list[dict[str, Any]]:
    """Every request the server sent the Gateway with this HTTP method."""

    return [request for request in gateway.requests if request["method"] == method]


def resource_route_requests(gateway: Any) -> list[dict[str, Any]]:
    """Every request the server sent the Gateway on a config-resource route.

    A refusal that must happen before anything is read or dispatched is asserted
    against this rather than against one method: such a refusal may not even fetch the
    resource it was asked about. Each entry's ``path`` is the request target as the
    Gateway saw it, query string included.
    """

    return [
        request for request in gateway.requests
        if "/data/api/v1/resources/" in str(request["path"])
    ]


def audit_rows(tmp_path: Path) -> list[dict[str, Any]]:
    return asyncio.run(_read_audit_rows(tmp_path))


async def _read_audit_rows(tmp_path: Path) -> list[dict[str, Any]]:
    columns = (
        "correlation_id", "tool", "actor_key", "operation_class", "destructive", "phase", "outcome",
        "error_code", "target_id", "safe_fields_json",
    )
    db = Database("audit", tmp_path / "audit.db", AUDIT_DDL)
    await db.open()
    try:
        rows = await db.run(
            lambda conn: conn.execute(f"SELECT {', '.join(columns)} FROM audit_log ORDER BY seq").fetchall()
        )
        return [dict(zip(columns, row, strict=True)) for row in rows]
    finally:
        await db.close()


def operation_record(tmp_path: Path, correlation_id: str) -> tuple[str, str, str | None] | None:
    """The D19 operation record one Tool call left behind."""

    async def scenario() -> tuple[str, str, str | None] | None:
        db = Database("state", tmp_path / "state.db", STATE_DDL)
        await db.open()
        try:
            record = await OperationRecordStore(db).fetch(correlation_id)
            if record is None:
                return None
            return (record.tool, record.outcome, record.error_code)
        finally:
            await db.close()

    return asyncio.run(scenario())
