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
from phase4_fixtures import (
    CONFIG,
    CORE_COLLECTION,
    OTHER_COLLECTION,
    PROFILE,
    READ_INVENTORY,
    RESOURCE,
    TOKEN_TYPE,
    UPDATE_TOOL,
    audit_rows,
    envelope,
    mutation_settings,
    operation_record,
    read_settings,
    resource_route_requests,
    seed_config_resources,
    structured,
    write_requests,
)
from phase4_fixtures import Session as _Session
from phase4_fixtures import CONFIG_MUTATION_TOOLS as MUTATION_TOOL_NAMES

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tests/harness"))

from recorded_gateway import API_TOKEN, RecordedGateway  # noqa: E402


# ------------------------------------------------------------------- fixtures


def _seed(gateway: RecordedGateway) -> None:
    """The config resources every case in this module needs (shared with the other
    Phase 4 mutation modules)."""

    seed_config_resources(gateway)


def _mutation_settings(**overrides: Any) -> Any:
    """The update Tool's deployment: only it is enabled, and only the one Target."""

    targets = overrides.pop("mutation_targets", None)
    return mutation_settings(
        UPDATE_TOOL,
        targets=targets or {UPDATE_TOOL: (f"{PROFILE}/{RESOURCE}",)},
        **overrides,
    )


# --------------------------------------------------------------- signature read


def test_config_resource_get_emits_the_resource_signature(tmp_path: Path) -> None:
    """D30: the read Tool publishes the Precondition token the caller must send
    back; without it a caller cannot make a preconditioned change at all."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        settings = read_settings(
            data_dir=str(tmp_path), gateway_url=gateway.base_url, gateway_api_token=API_TOKEN,
        )

        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = _Session(http, "reader-secret").read(PROFILE, RESOURCE)

    body = structured(result)
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
        settings = read_settings(
            data_dir=str(tmp_path), gateway_url=gateway.base_url, gateway_api_token=API_TOKEN,
        )

        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = _Session(http, "reader-secret").read(PROFILE, RESOURCE)

    assert structured(result)["signature"] is None


# ------------------------------------------------------------------ inventory


def test_update_tool_declares_the_config_scope() -> None:
    server = server_module.create_server(_mutation_settings())

    async def scenario() -> Any:
        return await server.get_tool(UPDATE_TOOL)

    tool = asyncio.run(scenario())
    assert tool is not None
    assert scope_tag(CONFIG) in tool.tags


@pytest.mark.parametrize("class_enabled", [True, False])
def test_mutation_tool_visibility_follows_the_class_gate(
    tmp_path: Path, class_enabled: bool,
) -> None:
    """Each Mutation Tool is discoverable only when CONFIG_MUTATION is enabled for
    the deployment; everything else in the inventory is unchanged (D08). The class
    gate, not the operation allowlist, decides discovery: only `config_resource_update`
    is in this deployment's operation allowlist, and the other Phase 4 Mutation Tools
    are still listed — they refuse at call time instead."""

    mutation_tools = set(MUTATION_TOOL_NAMES)
    assert UPDATE_TOOL in mutation_tools
    with RecordedGateway() as gateway:
        _seed(gateway)
        settings = _mutation_settings(
            data_dir=str(tmp_path), gateway_url=gateway.base_url, gateway_api_token=API_TOKEN,
            config_mutation_enabled=class_enabled,
        )

        with TestClient(server_module.create_server(settings).http_app()) as http:
            names = _Session(http, "cfg-secret").tools()

    assert names == (READ_INVENTORY | mutation_tools if class_enabled else READ_INVENTORY)


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


def test_an_allowlisted_update_changes_the_resource_and_reports_observed_state(
    tmp_path: Path,
) -> None:
    """The tracer bullet: read the signature, change the resource, prove the change
    with a bounded re-read, and never send a caller-chosen knob.

    D30's owner ruling 5 is asserted on the wire here and in every case that follows:
    an omitted ``collection`` means ``core``, so the pre-dispatch read, the change item
    and the verification read-back all name it.
    """

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

        puts = write_requests(gateway, "PUT")
        reads = [request["path"] for request in resource_route_requests(gateway) if request["method"] == "GET"]
        stored = gateway.resource(PROFILE, RESOURCE)

    assert len(puts) == 1, puts
    assert puts[0]["path"] == f"/data/api/v1/resources/{PROFILE}?allowInvalidReferences=false"
    assert json.loads(puts[0]["body"]) == [{
        "name": RESOURCE,
        "signature": before,
        "collection": CORE_COLLECTION,
        "config": {"profile": {"type": "local", "retentionDays": 30}},
        "description": "CI audit profile (30 days)",
    }]
    assert reads == [
        f"/data/api/v1/resources/find/{PROFILE}/{RESOURCE}?collection={CORE_COLLECTION}",
    ] * 2, "the pre-dispatch read and the read-back both name the core collection"
    assert stored["config"] == {"profile": {"type": "local", "retentionDays": 30}}
    assert stored["signature"] != before, "the Gateway must have moved the signature"

    body = structured(result)
    assert body["resourceType"] == PROFILE
    assert body["name"] == RESOURCE
    assert body["collection"] == CORE_COLLECTION
    assert body["signature"] == stored["signature"]
    assert body["observedState"]["description"] == "CI audit profile (30 days)"
    assert body["observedState"]["config"]["profile"]["retentionDays"] == 30
    assert body["correlationId"]
    assert operation_record(tmp_path, body["correlationId"]) == (UPDATE_TOOL, "succeeded", None)


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

        rows = audit_rows(tmp_path)

    assert structured(result)["correlationId"] == rows[0]["correlation_id"]
    assert [
        (row["phase"], row["outcome"], row["operation_class"], row["destructive"], row["safe_fields_json"])
        for row in rows
    ] == [
        ("decision", "allowed", "CONFIG", 0,
         '{"collection":"core","name":"MCP_CI_AUDIT","resourceType":"ignition/audit-profile"}'),
        ("attempt", "attempted", "CONFIG", 0,
         '{"collection":"core","name":"MCP_CI_AUDIT","resourceType":"ignition/audit-profile"}'),
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

        puts = write_requests(gateway, "PUT")
        rows = audit_rows(tmp_path)
        stored = gateway.resource(PROFILE, RESOURCE)

    error = envelope(result)
    assert error["code"] == "conflict"
    assert puts == [], "a stale signature must not reach the Gateway"
    assert stored["description"] == "changed by hand"
    assert stored["signature"] == current
    assert [(row["phase"], row["outcome"]) for row in rows] == [
        ("decision", "denied:precondition:conflict"),
    ]
    assert rows[0]["correlation_id"] == error["correlationId"]


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

        puts = write_requests(gateway, "PUT")

    assert envelope(result)["code"] == "conflict"
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

        puts = write_requests(gateway, "PUT")
        rows = audit_rows(tmp_path)
        stored = gateway.resource(TOKEN_TYPE, "ignition-mcp-ci")

    error = envelope(result)
    assert error["code"] == "permission_denied"
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

        rows = audit_rows(tmp_path)

    error = envelope(result)
    assert error["code"] == "conflict"
    assert "stale" in error["message"]
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
    assert rows[0]["correlation_id"] == error["correlationId"]


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

        rows = audit_rows(tmp_path)

    error = envelope(result)
    assert error["code"] == "conflict"
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
        gateway.seed_resource(PROFILE, "OTHERPROFILE", config={"profile": {"type": "local"}})
        settings = _mutation_settings(
            data_dir=str(tmp_path), gateway_url=gateway.base_url, gateway_api_token=API_TOKEN,
        )

        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = _Session(http, "cfg-secret").call(UPDATE_TOOL, {
                "resourceType": PROFILE,
                "expectedSignature": gateway.signature(PROFILE, "OTHERPROFILE"),
                "name": "OTHERPROFILE", "enabled": False,
            })

        puts = write_requests(gateway, "PUT")
        rows = audit_rows(tmp_path)

    error = envelope(result)
    assert error["code"] == "permission_denied"
    assert puts == []
    assert rows[0]["outcome"] == "denied:target-allowlist:target-not-allowlisted"


def test_the_target_denial_code_is_declared_per_operation() -> None:
    """The D30 §7 mapping is Tool-scoped on purpose: the Phase 3 operation that G3
    evidence pins keeps its recorded code, so no frozen artifact changes."""

    assert CONFIG_RESOURCE_UPDATE.target_denial_code == "permission_denied"
    assert PROJECT_IMPORT_OPERATION.target_denial_code == "operation_disabled"
    # D30 §2: this Tool's explicit Gateway rejections are final, so no read-back can
    # turn them into a success; the Phase 3 operation keeps its recorded behaviour.
    assert CONFIG_RESOURCE_UPDATE.rejection_is_final is True
    assert PROJECT_IMPORT_OPERATION.rejection_is_final is False


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

        puts = write_requests(gateway, "PUT")

    assert envelope(result)["code"] == "operation_disabled"
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

        puts = write_requests(gateway, "PUT")

    assert envelope(result)["code"] == "invalid_argument"
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

        puts = write_requests(gateway, "PUT")

    assert UPDATE_TOOL not in names
    assert envelope(denied)["code"] == "permission_denied"
    assert puts == []


# ------------------------------------------------------------ collections (D30)


def test_an_explicit_core_collection_is_accepted(tmp_path: Path) -> None:
    """D30 owner ruling 5: a caller may name the collection the Mutations are pinned
    to, and the Gateway sees the same request either way."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        before = gateway.signature(PROFILE, RESOURCE)
        settings = _mutation_settings(
            data_dir=str(tmp_path), gateway_url=gateway.base_url, gateway_api_token=API_TOKEN,
        )

        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = _Session(http, "cfg-secret").call(UPDATE_TOOL, {
                "resourceType": PROFILE, "expectedSignature": before,
                "name": RESOURCE, "collection": CORE_COLLECTION, "enabled": False,
            })

        puts = write_requests(gateway, "PUT")
        reads = [
            request["path"] for request in resource_route_requests(gateway)
            if request["method"] == "GET"
        ]

    assert structured(result)["collection"] == CORE_COLLECTION
    assert len(puts) == 1, puts
    assert json.loads(puts[0]["body"]) == [{
        "name": RESOURCE, "signature": before, "collection": CORE_COLLECTION, "enabled": False,
    }]
    assert reads == [
        f"/data/api/v1/resources/find/{PROFILE}/{RESOURCE}?collection={CORE_COLLECTION}",
    ] * 2
    assert gateway.resource(PROFILE, RESOURCE)["enabled"] is False


def test_a_non_core_collection_is_refused_and_dispatches_nothing(tmp_path: Path) -> None:
    """The Target allowlist names a resource, not a collection, so a change into
    another one is refused rather than resolved to a look-alike — and the refusal
    happens before the resource is even read."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        core_signature = gateway.signature(PROFILE, RESOURCE, CORE_COLLECTION)
        other_signature = gateway.signature(PROFILE, RESOURCE, OTHER_COLLECTION)
        settings = _mutation_settings(
            data_dir=str(tmp_path), gateway_url=gateway.base_url, gateway_api_token=API_TOKEN,
            mutation_targets={UPDATE_TOOL: ("*",)},
        )

        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = _Session(http, "cfg-secret").call(UPDATE_TOOL, {
                "resourceType": PROFILE, "expectedSignature": other_signature,
                "name": RESOURCE, "collection": OTHER_COLLECTION, "enabled": False,
            })

        requests = resource_route_requests(gateway)
        rows = audit_rows(tmp_path)

    assert envelope(result)["code"] == "invalid_argument"
    assert requests == [], "a non-core collection must not reach the Gateway at all"
    assert rows == [], "the refusal is input validation, before any audited decision"
    assert gateway.signature(PROFILE, RESOURCE, OTHER_COLLECTION) == other_signature
    assert gateway.signature(PROFILE, RESOURCE, CORE_COLLECTION) == core_signature


def test_the_core_collection_resource_is_the_one_a_change_applies_to(tmp_path: Path) -> None:
    """Two collections really are two resources: the change applies to the core one
    and leaves the same-named look-alike in the other collection untouched."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        other_signature = gateway.signature(PROFILE, RESOURCE, OTHER_COLLECTION)
        settings = _mutation_settings(
            data_dir=str(tmp_path), gateway_url=gateway.base_url, gateway_api_token=API_TOKEN,
        )

        with TestClient(server_module.create_server(settings).http_app()) as http:
            session = _Session(http, "cfg-secret")
            refused = session.call(UPDATE_TOOL, {
                "resourceType": PROFILE,
                "expectedSignature": gateway.signature(PROFILE, RESOURCE, OTHER_COLLECTION),
                "name": RESOURCE, "collection": OTHER_COLLECTION, "enabled": False,
            })
            applied = session.call(UPDATE_TOOL, {
                "resourceType": PROFILE,
                "expectedSignature": gateway.signature(PROFILE, RESOURCE),
                "name": RESOURCE, "description": "core collection only",
            })

        bodies = [json.loads(request["body"]) for request in write_requests(gateway, "PUT")]

    assert envelope(refused)["code"] == "invalid_argument"
    assert structured(applied)["collection"] == CORE_COLLECTION
    assert bodies == [[{
        "name": RESOURCE, "signature": bodies[0][0]["signature"],
        "collection": CORE_COLLECTION,
        "description": "core collection only",
    }]]
    assert gateway.resource(PROFILE, RESOURCE)["description"] == "core collection only"
    assert gateway.resource(PROFILE, RESOURCE, OTHER_COLLECTION)["enabled"] is True
    assert gateway.signature(PROFILE, RESOURCE, OTHER_COLLECTION) == other_signature
    assert gateway.resource(PROFILE, RESOURCE)["collection"] == CORE_COLLECTION
    assert gateway.resource(PROFILE, RESOURCE, OTHER_COLLECTION)["collection"] == OTHER_COLLECTION


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

        puts = write_requests(gateway, "PUT")

    error = envelope(result)
    assert error["code"] == "invalid_argument"
    assert "/config/profile/type" in error["message"]
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

    assert structured(result)["observedState"]["config"]["profile"]["type"] == "database"


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

        bodies = [json.loads(request["body"]) for request in write_requests(gateway, "PUT")]

    assert structured(result)["observedState"]["description"] == "after"
    assert bodies == [[{
        "signature": bodies[0][0]["signature"], "collection": CORE_COLLECTION,
        "description": "after",
    }]], "a singleton's item carries no name, but it does name the core collection"


def test_another_writer_winning_the_race_is_never_reported_as_our_success(
    tmp_path: Path,
) -> None:
    """Deterministic race: the requested description already matches, and another
    writer changes an unrelated field at PUT time. The Gateway rejects our stale
    signature, and since the requested values were already in place nothing can be
    attributed to this call — the explicit rejection stays authoritative (D30 §2)."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        already = str(gateway.resource(PROFILE, RESOURCE)["description"])
        before = gateway.signature(PROFILE, RESOURCE)
        gateway.race_update_with(enabled=False)
        settings = _mutation_settings(
            data_dir=str(tmp_path), gateway_url=gateway.base_url, gateway_api_token=API_TOKEN,
        )

        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = _Session(http, "cfg-secret").call(UPDATE_TOOL, {
                "resourceType": PROFILE, "expectedSignature": before,
                "name": RESOURCE, "description": already,
            })

        rows = audit_rows(tmp_path)
        stored = gateway.resource(PROFILE, RESOURCE)
        puts = write_requests(gateway, "PUT")

    error = envelope(result)
    assert error["code"] == "conflict"
    assert len(puts) == 1, "the change was dispatched once"
    assert stored["enabled"] is False, "the other writer's change is the only one applied"
    assert stored["description"] == already
    assert gateway.signature(PROFILE, RESOURCE) != before
    assert [(row["phase"], row["outcome"], row["error_code"]) for row in rows] == [
        ("decision", "allowed", None),
        ("attempt", "attempted", None),
        ("result", "rejected", "conflict"),
        ("result", "failed", "conflict"),
    ]


def test_a_competing_writer_making_the_requested_change_is_not_our_success(
    tmp_path: Path,
) -> None:
    """The hardest race: the competing writer applies the very values this call asked
    for, at PUT time. The Gateway rejects our stale signature, and the read-back cannot
    tell whose change it is — so the explicit rejection is the result (D30 §2), never a
    recovered success, whatever the read-back shows."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        before = gateway.signature(PROFILE, RESOURCE)
        contested = "changed by the other writer"
        gateway.race_update_with(description=contested)
        settings = _mutation_settings(
            data_dir=str(tmp_path), gateway_url=gateway.base_url, gateway_api_token=API_TOKEN,
        )

        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = _Session(http, "cfg-secret").call(UPDATE_TOOL, {
                "resourceType": PROFILE, "expectedSignature": before,
                "name": RESOURCE, "description": contested,
            })

        rows = audit_rows(tmp_path)
        stored = gateway.resource(PROFILE, RESOURCE)
        puts = write_requests(gateway, "PUT")

    error = envelope(result)
    assert error["code"] == "conflict"
    assert len(puts) == 1
    assert stored["description"] == contested, "the value is present, but not from this call"
    assert gateway.signature(PROFILE, RESOURCE) != before
    assert [(row["phase"], row["outcome"], row["error_code"]) for row in rows] == [
        ("decision", "allowed", None),
        ("attempt", "attempted", None),
        ("result", "rejected", "conflict"),
        ("result", "failed", "conflict"),
    ]


def test_an_ambiguous_dispatch_whose_read_back_matches_is_never_a_success(
    tmp_path: Path,
) -> None:
    """A possibly-sent dispatch (5xx, no usable response) whose read-back shows the
    requested values still cannot be credited to this call when another writer could
    have made the same change: the result is outcome_unknown (D30 §2/§7)."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        before = gateway.signature(PROFILE, RESOURCE)
        contested = "written while the dispatch was ambiguous"
        gateway.fail_updates_with(500)
        gateway.race_update_with(description=contested)
        settings = _mutation_settings(
            data_dir=str(tmp_path), gateway_url=gateway.base_url, gateway_api_token=API_TOKEN,
        )

        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = _Session(http, "cfg-secret").call(UPDATE_TOOL, {
                "resourceType": PROFILE, "expectedSignature": before,
                "name": RESOURCE, "description": contested,
            })

        rows = audit_rows(tmp_path)
        stored = gateway.resource(PROFILE, RESOURCE)
        puts = write_requests(gateway, "PUT")

    error = envelope(result)
    assert error["code"] == "outcome_unknown"
    assert len(puts) == 1
    assert stored["description"] == contested, "the value is present, but unattributable"
    assert gateway.signature(PROFILE, RESOURCE) != before
    assert [row["outcome"] for row in rows] == [
        "allowed", "attempted", "outcome_unknown", "outcome_unknown",
    ]


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

        rows = audit_rows(tmp_path)

    assert envelope(denied)["code"] == "permission_denied"
    assert [(row["tool"], row["destructive"]) for row in rows] == [("destructive_probe", 1)]
