"""Phase 4 milestone 4c (ticket #14): the first REST Mutation Tool.

D30 makes a Resource signature the Precondition token for config-resource
changes: the caller reads it with ``config_resource_get`` and hands it back as
``expectedSignature``. These tests drive the real server against the recorded
Gateway (fixture first) over Streamable HTTP, so every assertion is an
MCP-visible result or an observed Gateway request, never an internal call.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
from typing import Any

import pytest
from starlette.testclient import TestClient

import ignition_rest_mcp.server as server_module
from ignition_rest_mcp.authorization import scope_tag
from ignition_rest_mcp.projects.transactions import PROJECT_IMPORT_OPERATION
from ignition_rest_mcp.services.config_mutation import CONFIG_RESOURCE_UPDATE
from ignition_rest_mcp.config import StaticToken
from ignition_rest_mcp.storage.database import Database
from ignition_rest_mcp.storage.records import OperationRecordStore
from ignition_rest_mcp.storage.schema import AUDIT_DDL, STATE_DDL
from test_config import _settings

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tests/harness"))

from recorded_gateway import API_TOKEN, RecordedGateway  # noqa: E402

READ = "ignition.read"
CONFIG = "ignition.config"
PROFILE = "ignition/audit-profile"
TOKEN_TYPE = "ignition/api-token"
RESOURCE = "MCP_CI_AUDIT"
ACCEPT = "application/json, text/event-stream"
PROTOCOL_VERSION = "2025-06-18"

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
UPDATE_TOOL = "config_resource_update"


# ------------------------------------------------------------------- fixtures


def _seed(gateway: RecordedGateway) -> None:
    """The two config resources every case in this module needs: an allowlisted
    update target and the refused API-token resource the CI Gateway really has."""

    gateway.seed_resource(
        PROFILE, RESOURCE,
        config={"profile": {"type": "local", "retentionDays": 14}, "settings": {}},
        description="CI audit profile",
    )
    gateway.seed_resource(
        PROFILE, RESOURCE, collection="custom",
        config={"profile": {"type": "local", "retentionDays": 3}},
        description="same name, other collection",
    )
    gateway.seed_resource(
        TOKEN_TYPE, "ignition-mcp-ci",
        config={"profile": {"type": "basic-token"}, "settings": {"tokenHash": "<redacted>"}},
        description="Disposable CI-only API token",
    )


def _mutation_settings(**overrides: Any) -> Any:
    values: dict[str, Any] = {
        "auth_mode": "static-token",
        "static_tokens": (
            StaticToken(name="reader", token="reader-secret", scopes=(READ,)),
            StaticToken(name="config-agent", token="cfg-secret", scopes=(READ, CONFIG)),
        ),
        "config_mutation_enabled": True,
        "mutation_operations": (UPDATE_TOOL,),
        "mutation_targets": {UPDATE_TOOL: (f"{PROFILE}/{RESOURCE}",)},
    }
    values.update(overrides)
    return _settings(**values)


def _read_settings(**overrides: Any) -> Any:
    values: dict[str, Any] = {"auth_mode": "none"}
    values.update(overrides)
    return _settings(**values)


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
        decoded = _decode(response)
        assert "error" not in decoded, decoded
        return decoded["result"]

    def tools(self) -> frozenset[str]:
        return frozenset(str(tool["name"]) for tool in self.request("tools/list", {})["tools"])

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self.request("tools/call", {"name": name, "arguments": arguments})

    def read(self, resource_type: str, name: str) -> dict[str, Any]:
        return self.call("config_resource_get", {
            "resourceType": resource_type, "name": name,
            "collection": "", "defaultIfUndefined": False,
        })


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


def _structured(result: dict[str, Any]) -> dict[str, Any]:
    assert result.get("isError") is not True, result
    assert isinstance(result.get("structuredContent"), dict), result
    return result["structuredContent"]


def _envelope(result: dict[str, Any]) -> dict[str, Any]:
    assert result.get("isError") is True, result
    for item in result.get("content", ()):
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            decoded = json.loads(item["text"])
            if isinstance(decoded, dict) and "code" in decoded:
                return decoded
    raise AssertionError(f"no structured error envelope in {result!r}")


# --------------------------------------------------------------- signature read


def test_config_resource_get_emits_the_resource_signature(tmp_path: Path) -> None:
    """D30: the read Tool publishes the Precondition token the caller must send
    back; without it a caller cannot make a preconditioned change at all."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        settings = _read_settings(
            data_dir=str(tmp_path), gateway_url=gateway.base_url, gateway_api_token=API_TOKEN,
        )

        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = _Session(http, "reader-secret").read(PROFILE, RESOURCE)

    body = _structured(result)
    assert body["signature"] == gateway.signature(PROFILE, RESOURCE)
    assert body["resource"]["config"]["profile"] == {"type": "local", "retentionDays": 14}


def test_config_resource_get_reports_no_signature_when_the_gateway_omits_one(
    tmp_path: Path,
) -> None:
    """A Gateway response without a signature must not be invented: the read stays
    honest, and a caller that needs a token has nothing to send."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        gateway.resource(PROFILE, RESOURCE).pop("signature")
        settings = _read_settings(
            data_dir=str(tmp_path), gateway_url=gateway.base_url, gateway_api_token=API_TOKEN,
        )

        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = _Session(http, "reader-secret").read(PROFILE, RESOURCE)

    assert _structured(result)["signature"] is None


# ------------------------------------------------------------------ inventory


def test_update_tool_declares_the_config_scope() -> None:
    server = server_module.create_server(_mutation_settings())

    async def scenario() -> Any:
        return await server.get_tool(UPDATE_TOOL)

    tool = asyncio.run(scenario())
    assert tool is not None
    assert scope_tag(CONFIG) in tool.tags


@pytest.mark.parametrize("class_enabled", [True, False])
def test_update_tool_visibility_follows_the_class_gate(
    tmp_path: Path, class_enabled: bool,
) -> None:
    """The Tool is discoverable only when CONFIG_MUTATION is enabled for the
    deployment; everything else in the inventory is unchanged (D08)."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        settings = _mutation_settings(
            data_dir=str(tmp_path), gateway_url=gateway.base_url, gateway_api_token=API_TOKEN,
            config_mutation_enabled=class_enabled,
        )

        with TestClient(server_module.create_server(settings).http_app()) as http:
            names = _Session(http, "cfg-secret").tools()

    assert (UPDATE_TOOL in names) is class_enabled
    assert names - {UPDATE_TOOL} == READ_INVENTORY


def test_update_tool_is_hidden_when_the_gateway_lacks_the_update_capability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The D04 capability registry still decides discovery: a Gateway whose
    OpenAPI documents no resource PUT route cannot expose the update Tool."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        document = json.loads(
            (ROOT / "tests/fixtures/recorded/gateway-8.3/phase2/openapi-required.json").read_text("utf-8")
        )
        paths = document["paths"]
        paths["/data/api/v1/resources/type/ignition/audit-profile"] = {"get": {}}
        paths["/data/api/v1/resources/find/ignition/audit-profile/{name}"] = {"get": {}}
        paths["/data/api/v1/resources/com.inductiveautomation.mcp/server-config"] = {"get": {}}

        async def read_only_openapi(self: Any) -> bytes:
            return json.dumps({"paths": paths}).encode("utf-8")

        monkeypatch.setattr(server_module.GatewayClient, "openapi", read_only_openapi)
        settings = _mutation_settings(
            data_dir=str(tmp_path), gateway_url=gateway.base_url, gateway_api_token=API_TOKEN,
        )

        with TestClient(server_module.create_server(settings).http_app()) as http:
            names = _Session(http, "cfg-secret").tools()

    assert UPDATE_TOOL not in names
    assert "config_resource_get" in names


def test_update_tool_is_hidden_when_the_update_route_has_no_request_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D03: a documented route without a documented request schema leaves nothing to
    validate the write against, so the route is withheld and the Tool is hidden."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        paths = {
            "/data/api/v1/gateway-info": {"get": {}},
            f"/data/api/v1/resources/type/{PROFILE}": {"get": {}},
            f"/data/api/v1/resources/find/{PROFILE}/{{name}}": {"get": {}},
            f"/data/api/v1/resources/{PROFILE}": {"put": {}},
        }

        async def schema_less_openapi(self: Any) -> bytes:
            return json.dumps({"paths": paths}).encode("utf-8")

        monkeypatch.setattr(server_module.GatewayClient, "openapi", schema_less_openapi)
        settings = _mutation_settings(
            data_dir=str(tmp_path), gateway_url=gateway.base_url, gateway_api_token=API_TOKEN,
        )

        with TestClient(server_module.create_server(settings).http_app()) as http:
            names = _Session(http, "cfg-secret").tools()

    assert UPDATE_TOOL not in names
    assert "config_resource_get" in names


# ------------------------------------------------------------------- mutation


def _put_requests(gateway: RecordedGateway) -> list[dict[str, Any]]:
    return [request for request in gateway.requests if request["method"] == "PUT"]


def test_an_allowlisted_update_changes_the_resource_and_reports_observed_state(
    tmp_path: Path,
) -> None:
    """The tracer bullet: read the signature, change the resource, prove the change
    with a bounded re-read, and never send a caller-chosen knob."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        before = gateway.signature(PROFILE, RESOURCE)
        settings = _mutation_settings(
            data_dir=str(tmp_path), gateway_url=gateway.base_url, gateway_api_token=API_TOKEN,
        )

        with TestClient(server_module.create_server(settings).http_app()) as http:
            session = _Session(http, "cfg-secret")
            result = session.call(UPDATE_TOOL, {
                "resourceType": PROFILE,
                "expectedSignature": before,
                "name": RESOURCE,
                "config": {"profile": {"type": "local", "retentionDays": 30}},
                "description": "CI audit profile (30 days)",
            })

        puts = _put_requests(gateway)
        stored = gateway.resource(PROFILE, RESOURCE)

    assert len(puts) == 1, puts
    assert puts[0]["path"] == f"/data/api/v1/resources/{PROFILE}?allowInvalidReferences=false"
    assert json.loads(puts[0]["body"]) == [{
        "name": RESOURCE,
        "signature": before,
        "config": {"profile": {"type": "local", "retentionDays": 30}},
        "description": "CI audit profile (30 days)",
    }]
    assert stored["config"] == {"profile": {"type": "local", "retentionDays": 30}}
    assert stored["signature"] != before, "the Gateway must have moved the signature"

    body = _structured(result)
    assert body["resourceType"] == PROFILE
    assert body["name"] == RESOURCE
    assert body["signature"] == stored["signature"]
    assert body["observedState"]["description"] == "CI audit profile (30 days)"
    assert body["observedState"]["config"]["profile"]["retentionDays"] == 30
    assert body["correlationId"]
    assert _operation_record(tmp_path, body["correlationId"]) == (UPDATE_TOOL, "succeeded", None)


def test_an_update_leaves_one_audited_decision_attempt_and_result(tmp_path: Path) -> None:
    """D18 ordering: the decision and the attempt are durable before dispatch, and
    the result carries the correlation ID the caller sees."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        signature = gateway.signature(PROFILE, RESOURCE)
        settings = _mutation_settings(
            data_dir=str(tmp_path), gateway_url=gateway.base_url, gateway_api_token=API_TOKEN,
        )

        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = _Session(http, "cfg-secret").call(UPDATE_TOOL, {
                "resourceType": PROFILE, "expectedSignature": signature,
                "name": RESOURCE, "enabled": False,
            })

        rows = _audit_rows(tmp_path)

    assert _structured(result)["correlationId"] == rows[0]["correlation_id"]
    assert [
        (row["phase"], row["outcome"], row["operation_class"], row["destructive"], row["safe_fields_json"])
        for row in rows
    ] == [
        ("decision", "allowed", "CONFIG", 0,
         '{"collection":"","name":"MCP_CI_AUDIT","resourceType":"ignition/audit-profile"}'),
        ("attempt", "attempted", "CONFIG", 0,
         '{"collection":"","name":"MCP_CI_AUDIT","resourceType":"ignition/audit-profile"}'),
        ("result", "completed", "CONFIG", 0, "{}"),
    ]
    assert rows[0]["actor_key"] == "static-token:config-agent"
    assert rows[0]["target_id"] == f"{PROFILE}/{RESOURCE}"


def test_a_stale_signature_is_a_conflict_that_never_dispatches(tmp_path: Path) -> None:
    """D30 §2: a Precondition token mismatch fails with ``conflict`` before anything
    is dispatched, and the resource is left exactly as the other writer left it."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        stale = gateway.signature(PROFILE, RESOURCE)
        current = gateway.change_resource_out_of_band(PROFILE, RESOURCE, description="changed by hand")
        settings = _mutation_settings(
            data_dir=str(tmp_path), gateway_url=gateway.base_url, gateway_api_token=API_TOKEN,
        )

        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = _Session(http, "cfg-secret").call(UPDATE_TOOL, {
                "resourceType": PROFILE, "expectedSignature": stale,
                "name": RESOURCE, "enabled": False,
            })

        puts = _put_requests(gateway)
        rows = _audit_rows(tmp_path)
        stored = gateway.resource(PROFILE, RESOURCE)

    envelope = _envelope(result)
    assert envelope["code"] == "conflict"
    assert puts == [], "a stale signature must not reach the Gateway"
    assert stored["description"] == "changed by hand"
    assert stored["signature"] == current
    assert [(row["phase"], row["outcome"]) for row in rows] == [
        ("decision", "denied:precondition:conflict"),
    ]
    assert rows[0]["correlation_id"] == envelope["correlationId"]


def test_a_signature_the_gateway_will_not_report_cannot_be_preconditioned(tmp_path: Path) -> None:
    with RecordedGateway() as gateway:
        _seed(gateway)
        gateway.resource(PROFILE, RESOURCE).pop("signature")
        settings = _mutation_settings(
            data_dir=str(tmp_path), gateway_url=gateway.base_url, gateway_api_token=API_TOKEN,
        )

        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = _Session(http, "cfg-secret").call(UPDATE_TOOL, {
                "resourceType": PROFILE, "expectedSignature": "sig-from-nowhere",
                "name": RESOURCE, "enabled": False,
            })

        puts = _put_requests(gateway)

    assert _envelope(result)["code"] == "conflict"
    assert puts == []


@pytest.mark.parametrize(
    "targets",
    [
        ("*",),
        (f"{PROFILE}/{RESOURCE}",),
    ],
    ids=["wildcard", "narrow"],
)
def test_a_refused_resource_type_is_denied_whatever_the_allowlist_says(
    tmp_path: Path, targets: tuple[str, ...],
) -> None:
    """D30 §5: the refused set is contract-listed, precedes the Target allowlist,
    and never reaches the Gateway — including the Gateway's own API tokens."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        signature = gateway.signature(TOKEN_TYPE, "ignition-mcp-ci")
        settings = _mutation_settings(
            data_dir=str(tmp_path), gateway_url=gateway.base_url, gateway_api_token=API_TOKEN,
            mutation_targets={UPDATE_TOOL: targets},
        )

        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = _Session(http, "cfg-secret").call(UPDATE_TOOL, {
                "resourceType": TOKEN_TYPE, "expectedSignature": signature,
                "name": "ignition-mcp-ci", "enabled": False,
            })

        puts = _put_requests(gateway)
        rows = _audit_rows(tmp_path)
        stored = gateway.resource(TOKEN_TYPE, "ignition-mcp-ci")

    envelope = _envelope(result)
    assert envelope["code"] == "permission_denied"
    assert puts == [], "a refused resource type must never reach the Gateway"
    assert stored["enabled"] is True
    assert [(row["phase"], row["outcome"], row["target_id"]) for row in rows] == [
        ("decision", f"denied:target-class:refused-resource-type:{TOKEN_TYPE}",
         f"{TOKEN_TYPE}/ignition-mcp-ci"),
    ]


def test_a_gateway_refusal_inside_a_success_response_is_a_conflict(tmp_path: Path) -> None:
    """Ignition reports a refused resource change as 200 with ``success=false``.
    That is a known rejection, never a claimed success."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        gateway.refuse_updates_with("Signature mismatch for resource MCP_CI_AUDIT")
        signature = gateway.signature(PROFILE, RESOURCE)
        settings = _mutation_settings(
            data_dir=str(tmp_path), gateway_url=gateway.base_url, gateway_api_token=API_TOKEN,
        )

        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = _Session(http, "cfg-secret").call(UPDATE_TOOL, {
                "resourceType": PROFILE, "expectedSignature": signature,
                "name": RESOURCE, "enabled": False,
            })

        rows = _audit_rows(tmp_path)

    envelope = _envelope(result)
    assert envelope["code"] == "conflict"
    assert "stale" in envelope["message"]
    # The guarded executor records the refusal; the lifecycle then records that the
    # Tool call failed under the same correlation ID (two result rows, D18).
    assert [
        (row["phase"], row["outcome"], row["error_code"]) for row in rows
    ] == [
        ("decision", "allowed", None),
        ("attempt", "attempted", None),
        ("result", "rejected", "conflict"),
        ("result", "failed", "conflict"),
    ]
    assert rows[0]["correlation_id"] == envelope["correlationId"]


def test_a_refused_change_whose_values_were_already_in_place_is_still_a_conflict(
    tmp_path: Path,
) -> None:
    """A Gateway refusal must not become a success just because the requested values
    happened to equal the pre-state: nothing this call asked for is observable, so a
    stale Precondition token stays `conflict` (D30 §2)."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        existing = str(gateway.resource(PROFILE, RESOURCE)["description"])
        before = gateway.signature(PROFILE, RESOURCE)
        gateway.refuse_updates_with("Signature mismatch for resource MCP_CI_AUDIT")
        settings = _mutation_settings(
            data_dir=str(tmp_path), gateway_url=gateway.base_url, gateway_api_token=API_TOKEN,
        )

        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = _Session(http, "cfg-secret").call(UPDATE_TOOL, {
                "resourceType": PROFILE, "expectedSignature": before,
                "name": RESOURCE, "description": existing,
            })

        rows = _audit_rows(tmp_path)

    envelope = _envelope(result)
    assert envelope["code"] == "conflict"
    assert gateway.signature(PROFILE, RESOURCE) == before
    assert [(row["phase"], row["outcome"], row["error_code"]) for row in rows] == [
        ("decision", "allowed", None),
        ("attempt", "attempted", None),
        ("result", "rejected", "conflict"),
        ("result", "failed", "conflict"),
    ]


def test_a_change_outside_the_target_allowlist_is_permission_denied(tmp_path: Path) -> None:
    """D30 §7: a Target outside the Target allowlist is `permission_denied` for a
    Phase 4 Mutation, before anything is dispatched."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        gateway.seed_resource(PROFILE, "OTHER_PROFILE", config={"profile": {"type": "local"}})
        settings = _mutation_settings(
            data_dir=str(tmp_path), gateway_url=gateway.base_url, gateway_api_token=API_TOKEN,
        )

        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = _Session(http, "cfg-secret").call(UPDATE_TOOL, {
                "resourceType": PROFILE,
                "expectedSignature": gateway.signature(PROFILE, "OTHER_PROFILE"),
                "name": "OTHER_PROFILE", "enabled": False,
            })

        puts = _put_requests(gateway)
        rows = _audit_rows(tmp_path)

    envelope = _envelope(result)
    assert envelope["code"] == "permission_denied"
    assert puts == []
    assert rows[0]["outcome"] == "denied:target-allowlist:target-not-allowlisted"


def test_the_target_denial_code_is_declared_per_operation() -> None:
    """The D30 §7 mapping is Tool-scoped on purpose: the Phase 3 operation that G3
    evidence pins keeps its recorded code, so no frozen artifact changes."""

    assert CONFIG_RESOURCE_UPDATE.target_denial_code == "permission_denied"
    assert PROJECT_IMPORT_OPERATION.target_denial_code == "operation_disabled"


def test_an_operation_outside_the_allowlist_is_refused_before_dispatch(tmp_path: Path) -> None:
    with RecordedGateway() as gateway:
        _seed(gateway)
        settings = _mutation_settings(
            data_dir=str(tmp_path), gateway_url=gateway.base_url, gateway_api_token=API_TOKEN,
            mutation_operations=(),
        )

        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = _Session(http, "cfg-secret").call(UPDATE_TOOL, {
                "resourceType": PROFILE,
                "expectedSignature": gateway.signature(PROFILE, RESOURCE),
                "name": RESOURCE, "enabled": False,
            })

        puts = _put_requests(gateway)

    assert _envelope(result)["code"] == "operation_disabled"
    assert puts == []


def test_an_empty_change_is_rejected_without_consuming_the_signature(tmp_path: Path) -> None:
    with RecordedGateway() as gateway:
        _seed(gateway)
        before = gateway.signature(PROFILE, RESOURCE)
        settings = _mutation_settings(
            data_dir=str(tmp_path), gateway_url=gateway.base_url, gateway_api_token=API_TOKEN,
        )

        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = _Session(http, "cfg-secret").call(UPDATE_TOOL, {
                "resourceType": PROFILE, "expectedSignature": before, "name": RESOURCE,
            })

        puts = _put_requests(gateway)

    assert _envelope(result)["code"] == "invalid_argument"
    assert puts == []
    assert gateway.signature(PROFILE, RESOURCE) == before


def test_a_read_only_credential_cannot_see_or_call_the_update_tool(tmp_path: Path) -> None:
    with RecordedGateway() as gateway:
        _seed(gateway)
        settings = _mutation_settings(
            data_dir=str(tmp_path), gateway_url=gateway.base_url, gateway_api_token=API_TOKEN,
        )

        with TestClient(server_module.create_server(settings).http_app()) as http:
            reader = _Session(http, "reader-secret")
            names = reader.tools()
            denied = reader.call(UPDATE_TOOL, {
                "resourceType": PROFILE,
                "expectedSignature": gateway.signature(PROFILE, RESOURCE),
                "name": RESOURCE, "enabled": False,
            })

        puts = _put_requests(gateway)

    assert UPDATE_TOOL not in names
    assert _envelope(denied)["code"] == "permission_denied"
    assert puts == []


# ------------------------------------------------------------ collections (D30)


def test_a_non_default_collection_is_refused_and_dispatches_nothing(tmp_path: Path) -> None:
    """The Target allowlist names a resource, not a collection, so a
    collection-qualified change is refused rather than resolved to a look-alike."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        default_signature = gateway.signature(PROFILE, RESOURCE)
        other_signature = gateway.signature(PROFILE, RESOURCE, "custom")
        settings = _mutation_settings(
            data_dir=str(tmp_path), gateway_url=gateway.base_url, gateway_api_token=API_TOKEN,
            mutation_targets={UPDATE_TOOL: ("*",)},
        )

        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = _Session(http, "cfg-secret").call(UPDATE_TOOL, {
                "resourceType": PROFILE, "expectedSignature": other_signature,
                "name": RESOURCE, "collection": "custom", "enabled": False,
            })

        puts = _put_requests(gateway)

    assert _envelope(result)["code"] == "invalid_argument"
    assert puts == [], "a collection-qualified change must not reach the Gateway"
    assert gateway.signature(PROFILE, RESOURCE, "custom") == other_signature
    assert gateway.signature(PROFILE, RESOURCE) == default_signature


def test_the_default_collection_resource_is_the_one_a_change_applies_to(tmp_path: Path) -> None:
    """Two collections really are two resources: the refused collection change left
    the other collection alone, and the default one still updates."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        other_signature = gateway.signature(PROFILE, RESOURCE, "custom")
        settings = _mutation_settings(
            data_dir=str(tmp_path), gateway_url=gateway.base_url, gateway_api_token=API_TOKEN,
        )

        with TestClient(server_module.create_server(settings).http_app()) as http:
            session = _Session(http, "cfg-secret")
            refused = session.call(UPDATE_TOOL, {
                "resourceType": PROFILE,
                "expectedSignature": gateway.signature(PROFILE, RESOURCE, "custom"),
                "name": RESOURCE, "collection": "custom", "enabled": False,
            })
            applied = session.call(UPDATE_TOOL, {
                "resourceType": PROFILE,
                "expectedSignature": gateway.signature(PROFILE, RESOURCE),
                "name": RESOURCE, "description": "default collection only",
            })

        bodies = [json.loads(request["body"]) for request in _put_requests(gateway)]

    assert _envelope(refused)["code"] == "invalid_argument"
    assert _structured(applied)["collection"] == ""
    assert bodies == [[{
        "name": RESOURCE, "signature": bodies[0][0]["signature"],
        "description": "default collection only",
    }]]
    assert gateway.resource(PROFILE, RESOURCE)["description"] == "default collection only"
    assert gateway.resource(PROFILE, RESOURCE, "custom")["enabled"] is True
    assert gateway.signature(PROFILE, RESOURCE, "custom") == other_signature


# ------------------------------------------------- D03 request schema (issue #14)


def test_a_change_that_violates_the_gateway_request_schema_never_dispatches(
    tmp_path: Path,
) -> None:
    """D03: the change item is validated against the Gateway's own documented PUT
    schema, so a value the type's schema forbids is refused locally."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        before = gateway.signature(PROFILE, RESOURCE)
        settings = _mutation_settings(
            data_dir=str(tmp_path), gateway_url=gateway.base_url, gateway_api_token=API_TOKEN,
        )

        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = _Session(http, "cfg-secret").call(UPDATE_TOOL, {
                "resourceType": PROFILE, "expectedSignature": before, "name": RESOURCE,
                # The documented item schema allows database|edge|local|remote.
                "config": {"profile": {"type": "not-a-documented-type"}},
            })

        puts = _put_requests(gateway)

    envelope = _envelope(result)
    assert envelope["code"] == "invalid_argument"
    assert "/config/profile/type" in envelope["message"]
    assert puts == [], "a schema-invalid change must not reach the Gateway"
    assert gateway.signature(PROFILE, RESOURCE) == before


def test_a_schema_valid_change_still_dispatches(tmp_path: Path) -> None:
    """The guard is only worth its line if the documented shape still passes."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        settings = _mutation_settings(
            data_dir=str(tmp_path), gateway_url=gateway.base_url, gateway_api_token=API_TOKEN,
        )

        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = _Session(http, "cfg-secret").call(UPDATE_TOOL, {
                "resourceType": PROFILE,
                "expectedSignature": gateway.signature(PROFILE, RESOURCE),
                "name": RESOURCE,
                "config": {"profile": {"type": "database", "retentionDays": 7}},
            })

    assert _structured(result)["observedState"]["config"]["profile"]["type"] == "database"


def test_a_singleton_change_item_carries_no_name_the_gateway_does_not_document(
    tmp_path: Path,
) -> None:
    """A singleton's documented item schema requires only `signature`; the change
    item is built from that schema, not from an assumed shape."""

    with RecordedGateway() as gateway:
        gateway.seed_resource(
            "ignition/cobranding", "cobranding",
            config={"enabled": True}, description="before",
        )
        settings = _mutation_settings(
            data_dir=str(tmp_path), gateway_url=gateway.base_url, gateway_api_token=API_TOKEN,
            mutation_targets={UPDATE_TOOL: ("ignition/cobranding",)},
        )

        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = _Session(http, "cfg-secret").call(UPDATE_TOOL, {
                "resourceType": "ignition/cobranding",
                "expectedSignature": gateway.signature("ignition/cobranding", "cobranding"),
                "description": "after",
            })

        bodies = [json.loads(request["body"]) for request in _put_requests(gateway)]

    assert _structured(result)["observedState"]["description"] == "after"
    assert bodies == [[{
        "signature": bodies[0][0]["signature"], "description": "after",
    }]]


# ------------------------------------------------- destructive declaration (#13)


def test_a_denied_destructive_tool_records_its_declared_flag(tmp_path: Path) -> None:
    """#13 review nit: the audit row must carry the Tool's own D08 declaration
    instead of assuming every denial is non-destructive."""

    with RecordedGateway() as gateway:
        settings = _mutation_settings(
            data_dir=str(tmp_path), gateway_url=gateway.base_url, gateway_api_token=API_TOKEN,
        )
        server = server_module.create_server(settings)

        @server.tool(name="destructive_probe", tags={"mutation", "destructive", scope_tag(CONFIG)})
        async def destructive_probe() -> str:  # pragma: no cover - never reached
            return "ran"

        with TestClient(server.http_app()) as http:
            denied = _Session(http, "reader-secret").call("destructive_probe", {})

        rows = _audit_rows(tmp_path)

    assert _envelope(denied)["code"] == "permission_denied"
    assert [(row["tool"], row["destructive"]) for row in rows] == [("destructive_probe", 1)]


def _operation_record(tmp_path: Path, correlation_id: str) -> tuple[str, str, str | None] | None:
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


def _audit_rows(tmp_path: Path) -> list[dict[str, Any]]:
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
