"""Phase 4 milestone 4c (ticket #15): the create, delete and rename Tools.

D30 gives each of them its own Precondition rule: a create takes no token and relies
on the D11 collision policy, a delete carries the signature in the native ``DELETE``
path (and is read-compared before dispatch), and a rename is a server-side
read-compare only because the endpoint takes no signature. These tests drive the real
server against the recorded Gateway (fixture first) over Streamable HTTP, so every
assertion is an MCP-visible result or an observed Gateway request, never an internal
call.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

import pytest
from starlette.testclient import TestClient

import ignition_rest_mcp.server as server_module
from phase4_fixtures import (
    CORE_COLLECTION,
    CREATED_RESOURCE,
    CREATE_TOOL,
    DELETE_TOOL,
    OTHER_COLLECTION,
    PROFILE,
    READ_INVENTORY,
    RENAMED_RESOURCE,
    RENAME_TOOL,
    RESOURCE,
    SINGLETON_NAME,
    SINGLETON_TYPE,
    TOKEN_TYPE,
    audit_rows,
    envelope,
    mutation_settings,
    operation_record,
    resource_route_requests,
    seed_config_resources,
    structured,
    write_requests,
)
from phase4_fixtures import Session as Session
from phase4_fixtures import ARTIFACT_DELETE_TOOL, CONFIG_MUTATION_TOOLS as MUTATION_TOOL_NAMES

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tests/harness"))

from recorded_gateway import API_TOKEN, RecordedGateway  # noqa: E402

#: Every Phase 4 REST Mutation Tool, not just this milestone's: the class gate, not
#: the operation allowlist, decides discovery (D08/D30), and the recorded Gateway
#: documents every route these Tools need.
MUTATION_TOOLS = set(MUTATION_TOOL_NAMES)


def _seed(gateway: RecordedGateway) -> None:
    seed_config_resources(gateway)


def _settings(tmp_path: Path, gateway: RecordedGateway, **overrides: Any) -> Any:
    values: dict[str, Any] = {
        "data_dir": str(tmp_path), "gateway_url": gateway.base_url, "gateway_api_token": API_TOKEN,
    }
    values.update(overrides)
    return mutation_settings(**values)


def _delete_path(name: str, signature: str) -> str:
    """The documented DELETE target, which names the collection it applies in."""

    return (
        f"/data/api/v1/resources/{PROFILE}/{name}/{signature}"
        f"?collection={CORE_COLLECTION}"
    )


def _rename_path(name: str) -> str:
    """The documented rename target, which names the collection it applies in."""

    return f"/data/api/v1/resources/rename/{PROFILE}/{name}?collection={CORE_COLLECTION}"


def _read_paths(gateway: RecordedGateway) -> list[str]:
    """The targets of every config-resource read the server sent the Gateway."""

    return [
        request["path"] for request in resource_route_requests(gateway)
        if request["method"] == "GET"
    ]


# ------------------------------------------------------------------ inventory


@pytest.mark.parametrize("class_enabled", [True, False])
def test_every_new_tool_follows_the_class_gate(tmp_path: Path, class_enabled: bool) -> None:
    """D08: the class gate decides discovery, so all three Tools appear exactly when
    CONFIG_MUTATION is enabled and nothing else in the inventory moves."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        settings = _settings(tmp_path, gateway, config_mutation_enabled=class_enabled)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            names = Session(http, "cfg-secret").tools()

    assert names == (READ_INVENTORY | MUTATION_TOOLS if class_enabled else READ_INVENTORY)


def test_a_tool_is_hidden_when_the_gateway_documents_no_such_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Gateway whose document has no create route exposes no create Tool, and the
    same holds for delete, rename and a Tag import: the D04 capability is what gates
    discovery. ``artifact_delete`` is the exception D30 creates — it has no HTTP route
    to be gated on, so the class gate is its whole discovery rule."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        document = json.loads(
            (ROOT / "tests/fixtures/recorded/gateway-8.3/phase2/openapi-required.json").read_text("utf-8")
        )
        paths = document["paths"]
        paths[f"/data/api/v1/resources/type/{PROFILE}"] = {"get": {}}
        paths[f"/data/api/v1/resources/find/{PROFILE}/{{name}}"] = {"get": {}}
        # The recorded base document advertises the Tag import route, which is a
        # mutation: a document with no mutation route at all is the case here.
        paths.pop("/data/api/v1/tags/import", None)

        async def read_only_openapi(self: Any) -> bytes:
            return json.dumps({"paths": paths}).encode("utf-8")

        monkeypatch.setattr(server_module.GatewayClient, "openapi", read_only_openapi)
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            names = Session(http, "cfg-secret").tools()

    assert (MUTATION_TOOLS - {ARTIFACT_DELETE_TOOL}).isdisjoint(names)
    assert ARTIFACT_DELETE_TOOL in names
    assert "config_resource_get" in names


# ------------------------------------------------------------------- create


def test_an_allowlisted_create_publishes_the_resource_and_reports_its_signature(
    tmp_path: Path,
) -> None:
    """A create is the one config write with no Precondition token: it sends the item
    the Gateway documents, and the published resource — with its signature — is what
    the bounded re-read reports."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = Session(http, "cfg-secret").call(CREATE_TOOL, {
                "resourceType": PROFILE,
                "name": CREATED_RESOURCE,
                "config": {"profile": {"type": "local", "retentionDays": 7}},
                "description": "created by the Phase 4 cases",
            })

        posts = write_requests(gateway, "POST")
        reads = _read_paths(gateway)
        stored = gateway.resource(PROFILE, CREATED_RESOURCE)

    assert len(posts) == 1, posts
    assert posts[0]["path"] == f"/data/api/v1/resources/{PROFILE}?allowInvalidReferences=false"
    assert json.loads(posts[0]["body"]) == [{
        "name": CREATED_RESOURCE,
        "collection": CORE_COLLECTION,
        "config": {"profile": {"type": "local", "retentionDays": 7}},
        "description": "created by the Phase 4 cases",
    }], "a create item carries no signature, but it does name the core collection"
    assert reads == [
        f"/data/api/v1/resources/find/{PROFILE}/{CREATED_RESOURCE}?collection={CORE_COLLECTION}",
    ] * 2, "the collision probe and the read-back both name the core collection"
    assert stored["config"] == {"profile": {"type": "local", "retentionDays": 7}}

    body = structured(result)
    assert (body["resourceType"], body["name"], body["collection"]) == (
        PROFILE, CREATED_RESOURCE, CORE_COLLECTION,
    )
    assert body["signature"] == stored["signature"]
    assert body["observedState"]["description"] == "created by the Phase 4 cases"
    assert operation_record(tmp_path, body["correlationId"]) == (CREATE_TOOL, "succeeded", None)


def test_a_create_that_supplies_only_a_name_still_dispatches(tmp_path: Path) -> None:
    """The documented create item requires the name and nothing else, so nothing else
    may be required of the caller."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = Session(http, "cfg-secret").call(CREATE_TOOL, {
                "resourceType": PROFILE, "name": CREATED_RESOURCE,
            })

        posts = write_requests(gateway, "POST")

    assert json.loads(posts[0]["body"]) == [
        {"name": CREATED_RESOURCE, "collection": CORE_COLLECTION},
    ]
    assert structured(result)["observedState"]["name"] == CREATED_RESOURCE


def test_a_create_leaves_one_audited_decision_attempt_and_result(tmp_path: Path) -> None:
    """D18 ordering and safe fields, exactly as the update Tool's own rows."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = Session(http, "cfg-secret").call(CREATE_TOOL, {
                "resourceType": PROFILE, "name": CREATED_RESOURCE, "enabled": False,
            })

        rows = audit_rows(tmp_path)

    assert structured(result)["correlationId"] == rows[0]["correlation_id"]
    assert [
        (row["phase"], row["outcome"], row["operation_class"], row["destructive"], row["safe_fields_json"])
        for row in rows
    ] == [
        ("decision", "allowed", "CONFIG", 0,
         '{"collection":"core","name":"MCP_CI_AUDIT_CREATED","resourceType":"ignition/audit-profile"}'),
        ("attempt", "attempted", "CONFIG", 0,
         '{"collection":"core","name":"MCP_CI_AUDIT_CREATED","resourceType":"ignition/audit-profile"}'),
        ("result", "completed", "CONFIG", 0, "{}"),
    ]
    assert rows[0]["actor_key"] == "static-token:config-agent"
    assert rows[0]["target_id"] == f"{PROFILE}/{CREATED_RESOURCE}"


def test_creating_an_existing_target_is_a_conflict_that_never_dispatches(tmp_path: Path) -> None:
    """D11 collision policy: an existing target is refused before anything is sent,
    so the caller's own read is not consumed by a pointless create."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        before = gateway.signature(PROFILE, RESOURCE)
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = Session(http, "cfg-secret").call(CREATE_TOOL, {
                "resourceType": PROFILE, "name": RESOURCE, "enabled": False,
            })

        posts = write_requests(gateway, "POST")
        rows = audit_rows(tmp_path)

    assert envelope(result)["code"] == "conflict"
    assert posts == [], "a collision must not reach the Gateway"
    assert gateway.signature(PROFILE, RESOURCE) == before
    assert [(row["phase"], row["outcome"]) for row in rows] == [
        ("decision", "denied:precondition:conflict"),
    ]


def test_a_competing_create_inside_the_race_window_is_a_conflict_not_a_success(
    tmp_path: Path,
) -> None:
    """The target is absent when the caller's read runs and present when the request
    lands, so the Gateway refuses with its own collision — and the resource that
    exists is the other writer's, never this call's success."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        contested = "created by the other writer"
        gateway.race_write_with("create", description=contested)
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = Session(http, "cfg-secret").call(CREATE_TOOL, {
                "resourceType": PROFILE, "name": CREATED_RESOURCE, "description": contested,
            })

        posts = write_requests(gateway, "POST")
        rows = audit_rows(tmp_path)
        stored = gateway.resource(PROFILE, CREATED_RESOURCE)

    assert envelope(result)["code"] == "conflict"
    assert len(posts) == 1, "the create was dispatched once"
    assert stored["description"] == contested, "the value is present, but not from this call"
    assert [(row["phase"], row["outcome"], row["error_code"]) for row in rows] == [
        ("decision", "allowed", None),
        ("attempt", "attempted", None),
        ("result", "rejected", "conflict"),
        ("result", "failed", "conflict"),
    ]


def test_an_ambiguous_create_whose_read_back_shows_the_resource_is_never_a_success(
    tmp_path: Path,
) -> None:
    """D30 §2: a possibly-sent dispatch whose read-back shows the resource cannot be
    credited to this call when another writer could have created it."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        contested = "created while the dispatch was ambiguous"
        gateway.fail_writes_with("create", 500)
        gateway.race_write_with("create", description=contested)
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = Session(http, "cfg-secret").call(CREATE_TOOL, {
                "resourceType": PROFILE, "name": CREATED_RESOURCE, "description": contested,
            })

        rows = audit_rows(tmp_path)

    assert envelope(result)["code"] == "outcome_unknown"
    assert gateway.resource(PROFILE, CREATED_RESOURCE)["description"] == contested
    assert [row["outcome"] for row in rows] == [
        "allowed", "attempted", "outcome_unknown", "outcome_unknown",
    ]


def test_creating_a_refused_resource_type_is_denied_whatever_the_allowlist_says(
    tmp_path: Path,
) -> None:
    """D30 §5: the refused set precedes the Target allowlist and is never dispatched —
    including the Gateway's own API tokens."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        settings = _settings(tmp_path, gateway, targets={CREATE_TOOL: ("*",)})
        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = Session(http, "cfg-secret").call(CREATE_TOOL, {
                "resourceType": TOKEN_TYPE, "name": "ignition-mcp-ci-impostor",
            })

        posts = write_requests(gateway, "POST")
        rows = audit_rows(tmp_path)

    assert envelope(result)["code"] == "permission_denied"
    assert posts == []
    assert rows[0]["outcome"] == f"denied:target-class:refused-resource-type:{TOKEN_TYPE}"


def test_a_create_outside_the_target_allowlist_is_permission_denied(tmp_path: Path) -> None:
    """D30 §7 through the Tool-scoped mapping: nothing reaches the Gateway."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = Session(http, "cfg-secret").call(CREATE_TOOL, {
                "resourceType": PROFILE, "name": "MCP_CI_NOT_ALLOWLISTED",
            })

        posts = write_requests(gateway, "POST")

    assert envelope(result)["code"] == "permission_denied"
    assert posts == []


def test_a_non_core_collection_create_is_refused(tmp_path: Path) -> None:
    """D30 owner ruling 5: a create into another collection is refused before the
    resource is even read, so nothing can be published outside `core`."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        settings = _settings(tmp_path, gateway, targets={CREATE_TOOL: ("*",)})
        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = Session(http, "cfg-secret").call(CREATE_TOOL, {
                "resourceType": PROFILE, "name": CREATED_RESOURCE,
                "collection": OTHER_COLLECTION,
            })

        requests = resource_route_requests(gateway)
        rows = audit_rows(tmp_path)

    assert envelope(result)["code"] == "invalid_argument"
    assert requests == [], "a non-core collection must not reach the Gateway at all"
    assert rows == [], "the refusal is input validation, before any audited decision"


def test_a_create_that_violates_the_gateway_request_schema_never_dispatches(
    tmp_path: Path,
) -> None:
    """D03: the create item is validated against the Gateway's own documented POST
    schema before anything leaves the server."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = Session(http, "cfg-secret").call(CREATE_TOOL, {
                "resourceType": PROFILE, "name": CREATED_RESOURCE,
                "config": {"profile": {"type": "not-a-documented-type"}},
            })

        posts = write_requests(gateway, "POST")

    error = envelope(result)
    assert error["code"] == "invalid_argument"
    assert "/config/profile/type" in error["message"]
    assert posts == []


def test_a_create_of_an_existing_singleton_is_a_conflict(tmp_path: Path) -> None:
    """A singleton create names nothing, so its collision is the singleton that is
    already there."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = Session(http, "cfg-secret").call(CREATE_TOOL, {"resourceType": SINGLETON_TYPE})

        posts = write_requests(gateway, "POST")

    assert envelope(result)["code"] == "conflict"
    assert posts == []


def test_a_read_only_credential_cannot_see_or_call_the_create_tool(tmp_path: Path) -> None:
    with RecordedGateway() as gateway:
        _seed(gateway)
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            reader = Session(http, "reader-secret")
            names = reader.tools()
            denied = reader.call(CREATE_TOOL, {"resourceType": PROFILE, "name": CREATED_RESOURCE})

        posts = write_requests(gateway, "POST")

    assert CREATE_TOOL not in names
    assert envelope(denied)["code"] == "permission_denied"
    assert posts == []


# ------------------------------------------------------------------- delete


def test_an_allowlisted_delete_removes_the_resource_and_reports_absence(tmp_path: Path) -> None:
    """D30 §2 puts the signature in the native DELETE path: one request, no body, and
    the absence the verification had to observe."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        before = gateway.signature(PROFILE, RESOURCE)
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            session = Session(http, "cfg-secret")
            result = session.call(DELETE_TOOL, {
                "resourceType": PROFILE, "expectedSignature": before, "name": RESOURCE,
            })
            # The Mutation's own reads, before the read Tool's independent lookup below:
            # that lookup is the caller's read, not part of this change.
            mutation_reads = _read_paths(gateway)
            gone = session.read(PROFILE, RESOURCE)

        deletes = write_requests(gateway, "DELETE")
        rows = audit_rows(tmp_path)
        look_alike = gateway.signature(PROFILE, RESOURCE, OTHER_COLLECTION)

    assert len(deletes) == 1, deletes
    assert deletes[0]["path"] == _delete_path(RESOURCE, before)
    assert deletes[0]["body"] == b"", "the documented DELETE route takes no request body"
    assert mutation_reads == [
        f"/data/api/v1/resources/find/{PROFILE}/{RESOURCE}?collection={CORE_COLLECTION}",
    ] * 2, "the pre-dispatch read and the read-back both name the core collection"
    assert envelope(gone)["code"] == "not_found", "the resource is gone, as a read confirms"

    body = structured(result)
    assert (body["resourceType"], body["name"], body["collection"]) == (
        PROFILE, RESOURCE, CORE_COLLECTION,
    )
    assert body["present"] is False
    assert operation_record(tmp_path, body["correlationId"]) == (DELETE_TOOL, "succeeded", None)
    # D08/#13: the Tool declares itself destructive, and its own audit rows say so.
    assert [row["destructive"] for row in rows] == [1, 1, 1]
    # D30 owner ruling 5: only the core collection's resource was removed; the
    # same-named look-alike in another collection is still there.
    assert gateway.signature(PROFILE, RESOURCE, OTHER_COLLECTION) == look_alike


def test_a_delete_of_a_singleton_puts_no_name_in_the_path(tmp_path: Path) -> None:
    """A singleton has no name to address, and the documented route carries only the
    signature."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        before = gateway.signature(SINGLETON_TYPE, SINGLETON_NAME)
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = Session(http, "cfg-secret").call(DELETE_TOOL, {
                "resourceType": SINGLETON_TYPE, "expectedSignature": before,
            })

        deletes = write_requests(gateway, "DELETE")

    assert structured(result)["present"] is False
    assert deletes[0]["path"] == (
        f"/data/api/v1/resources/{SINGLETON_TYPE}/{before}?collection={CORE_COLLECTION}"
    )


def test_a_stale_signature_is_a_conflict_that_never_dispatches(tmp_path: Path) -> None:
    """D30 §2: a token mismatch fails before anything is dispatched, so a resource
    that was changed by another writer is not deleted on a stale belief."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        stale = gateway.signature(PROFILE, RESOURCE)
        current = gateway.change_resource_out_of_band(PROFILE, RESOURCE, description="changed by hand")
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = Session(http, "cfg-secret").call(DELETE_TOOL, {
                "resourceType": PROFILE, "expectedSignature": stale, "name": RESOURCE,
            })

        deletes = write_requests(gateway, "DELETE")
        rows = audit_rows(tmp_path)

    assert envelope(result)["code"] == "conflict"
    assert deletes == [], "a stale signature must not reach the Gateway"
    assert gateway.signature(PROFILE, RESOURCE) == current
    assert [(row["phase"], row["outcome"]) for row in rows] == [
        ("decision", "denied:precondition:conflict"),
    ]


def test_deleting_a_target_that_does_not_exist_is_not_found(tmp_path: Path) -> None:
    """Nothing to delete is not a success and not a conflict: the Gateway's own read
    answers `not_found`, and no DELETE is sent. The Target allowlist is widened here
    so the case reaches the read instead of being denied by the policy first."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        settings = _settings(tmp_path, gateway, targets={DELETE_TOOL: ("*",)})
        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = Session(http, "cfg-secret").call(DELETE_TOOL, {
                "resourceType": PROFILE, "expectedSignature": "sig-absent", "name": "MCP_CI_MISSING",
            })

        deletes = write_requests(gateway, "DELETE")

    assert envelope(result)["code"] == "not_found"
    assert deletes == []


@pytest.mark.parametrize(
    "targets",
    [("*",), (f"{PROFILE}/{RESOURCE}",)],
    ids=["wildcard", "narrow"],
)
def test_deleting_a_refused_resource_type_is_denied(
    tmp_path: Path, targets: tuple[str, ...],
) -> None:
    """D30 §5 refuses the API-token type whatever the allowlist says, and the token
    stays usable because nothing was dispatched."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        before = gateway.signature(TOKEN_TYPE, "ignition-mcp-ci")
        settings = _settings(tmp_path, gateway, targets={DELETE_TOOL: targets})
        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = Session(http, "cfg-secret").call(DELETE_TOOL, {
                "resourceType": TOKEN_TYPE,
                "expectedSignature": before,
                "name": "ignition-mcp-ci",
            })

        deletes = write_requests(gateway, "DELETE")

    assert envelope(result)["code"] == "permission_denied"
    assert deletes == []
    assert gateway.signature(TOKEN_TYPE, "ignition-mcp-ci") == before


def test_a_delete_outside_the_target_allowlist_is_permission_denied(tmp_path: Path) -> None:
    with RecordedGateway() as gateway:
        _seed(gateway)
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = Session(http, "cfg-secret").call(DELETE_TOOL, {
                "resourceType": PROFILE,
                "expectedSignature": gateway.signature(PROFILE, "MCP_CI_AUDIT_OTHER"),
                "name": "MCP_CI_AUDIT_OTHER",
            })

        deletes = write_requests(gateway, "DELETE")
        rows = audit_rows(tmp_path)

    assert envelope(result)["code"] == "permission_denied"
    assert deletes == []
    assert rows[0]["outcome"] == "denied:target-allowlist:target-not-allowlisted"


def test_a_non_core_collection_delete_is_refused(tmp_path: Path) -> None:
    """D30 owner ruling 5: a delete addressed to another collection is refused before
    the resource is read, so the look-alike there keeps its signature."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        look_alike = gateway.signature(PROFILE, RESOURCE, OTHER_COLLECTION)
        settings = _settings(tmp_path, gateway, targets={DELETE_TOOL: ("*",)})
        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = Session(http, "cfg-secret").call(DELETE_TOOL, {
                "resourceType": PROFILE, "expectedSignature": look_alike,
                "name": RESOURCE, "collection": OTHER_COLLECTION,
            })

        requests = resource_route_requests(gateway)

    assert envelope(result)["code"] == "invalid_argument"
    assert requests == [], "a non-core collection must not reach the Gateway at all"
    assert gateway.signature(PROFILE, RESOURCE, OTHER_COLLECTION) == look_alike


def test_a_gateway_refusal_inside_a_success_response_is_final_for_a_delete(
    tmp_path: Path,
) -> None:
    """A 200 carrying `success=false` is the Gateway's own refusal: it stays a
    conflict, and the resource it refused to remove is still there."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        gateway.refuse_writes_with("delete", "Signature mismatch for resource MCP_CI_AUDIT")
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = Session(http, "cfg-secret").call(DELETE_TOOL, {
                "resourceType": PROFILE,
                "expectedSignature": gateway.signature(PROFILE, RESOURCE),
                "name": RESOURCE,
            })

        rows = audit_rows(tmp_path)

    assert envelope(result)["code"] == "conflict"
    assert gateway.resource(PROFILE, RESOURCE)["description"] == "CI audit profile"
    assert [(row["phase"], row["outcome"], row["error_code"]) for row in rows] == [
        ("decision", "allowed", None),
        ("attempt", "attempted", None),
        ("result", "rejected", "conflict"),
        ("result", "failed", "conflict"),
    ]


def test_another_writer_winning_the_delete_race_is_never_reported_as_our_success(
    tmp_path: Path,
) -> None:
    """The competing writer changes the resource between our read-compare and the
    dispatch, so the signature in the path is stale and the Gateway refuses it."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        before = gateway.signature(PROFILE, RESOURCE)
        gateway.race_write_with("delete", description="changed by the other writer")
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = Session(http, "cfg-secret").call(DELETE_TOOL, {
                "resourceType": PROFILE, "expectedSignature": before, "name": RESOURCE,
            })

        deletes = write_requests(gateway, "DELETE")
        rows = audit_rows(tmp_path)

    assert envelope(result)["code"] == "conflict"
    assert len(deletes) == 1
    assert gateway.resource(PROFILE, RESOURCE)["description"] == "changed by the other writer"
    assert [row["outcome"] for row in rows] == ["allowed", "attempted", "rejected", "failed"]


def test_a_competing_delete_makes_our_own_delete_a_not_found(tmp_path: Path) -> None:
    """If another writer removes the resource inside the window, our DELETE addresses
    nothing: the Gateway answers 404 and that is the honest result — never a success
    for work this call did not do."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        before = gateway.signature(PROFILE, RESOURCE)
        gateway.race_write_with("delete", deleted=True)
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = Session(http, "cfg-secret").call(DELETE_TOOL, {
                "resourceType": PROFILE, "expectedSignature": before, "name": RESOURCE,
            })

        deletes = write_requests(gateway, "DELETE")

    assert envelope(result)["code"] == "not_found"
    assert len(deletes) == 1, "the delete was dispatched once and not replayed"


def test_an_ambiguous_delete_whose_read_back_shows_absence_is_outcome_unknown(
    tmp_path: Path,
) -> None:
    """The signature in the path is valid, the dispatch is ambiguous (no usable
    response), and the resource is gone — but another writer could have removed it, so
    absence is not this call's success (D30 §2)."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        before = gateway.signature(PROFILE, RESOURCE)
        gateway.fail_writes_with("delete", 500)
        gateway.race_write_with("delete", deleted=True)
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = Session(http, "cfg-secret").call(DELETE_TOOL, {
                "resourceType": PROFILE, "expectedSignature": before, "name": RESOURCE,
            })

        deletes = write_requests(gateway, "DELETE")
        rows = audit_rows(tmp_path)

    assert envelope(result)["code"] == "outcome_unknown"
    assert len(deletes) == 1
    assert [row["outcome"] for row in rows] == [
        "allowed", "attempted", "outcome_unknown", "outcome_unknown",
    ]


# ------------------------------------------------------------------- rename


def test_an_allowlisted_rename_moves_the_resource_and_reports_both_names(
    tmp_path: Path,
) -> None:
    """D30 §4: `references=ABORT` is always sent, the body carries the new name, and
    the verification needs both names — the old one vacant and the new one holding the
    resource."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        before = gateway.signature(PROFILE, RESOURCE)
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = Session(http, "cfg-secret").call(RENAME_TOOL, {
                "resourceType": PROFILE, "expectedSignature": before,
                "name": RESOURCE, "newName": RENAMED_RESOURCE,
            })

        posts = write_requests(gateway, "POST")
        reads = _read_paths(gateway)
        rows = audit_rows(tmp_path)
        look_alike = gateway.resource(PROFILE, RESOURCE, OTHER_COLLECTION)

    assert len(posts) == 1, posts
    assert posts[0]["path"] == _rename_path(RESOURCE)
    assert reads == [
        f"/data/api/v1/resources/find/{PROFILE}/{RESOURCE}?collection={CORE_COLLECTION}",
        f"/data/api/v1/resources/find/{PROFILE}/{RENAMED_RESOURCE}?collection={CORE_COLLECTION}",
        f"/data/api/v1/resources/find/{PROFILE}/{RENAMED_RESOURCE}?collection={CORE_COLLECTION}",
        f"/data/api/v1/resources/find/{PROFILE}/{RESOURCE}?collection={CORE_COLLECTION}",
    ], "every read the rename makes names the core collection"
    assert json.loads(posts[0]["body"]) == {"name": RENAMED_RESOURCE, "references": "ABORT"}
    moved = gateway.resource(PROFILE, RENAMED_RESOURCE)
    assert moved["config"]["profile"] == {"type": "local", "retentionDays": 14}

    body = structured(result)
    assert body["resourceType"] == PROFILE
    assert (body["name"], body["previousName"]) == (RENAMED_RESOURCE, RESOURCE)
    assert body["collection"] == CORE_COLLECTION
    assert body["signature"] == moved["signature"]
    assert moved["collection"] == CORE_COLLECTION
    # D30 owner ruling 5: the look-alike in another collection did not move.
    assert gateway.resource(PROFILE, RESOURCE, OTHER_COLLECTION) == look_alike
    assert operation_record(tmp_path, body["correlationId"]) == (RENAME_TOOL, "succeeded", None)
    assert [(row["phase"], row["outcome"], row["destructive"]) for row in rows] == [
        ("decision", "allowed", 0), ("attempt", "attempted", 0), ("result", "completed", 0),
    ]


def test_a_stale_signature_is_a_conflict_that_never_dispatches_a_rename(tmp_path: Path) -> None:
    """The rename endpoint takes no signature, so the read-compare is the only check
    before the dispatch — and it refuses a stale token."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        stale = gateway.signature(PROFILE, RESOURCE)
        gateway.change_resource_out_of_band(PROFILE, RESOURCE, description="changed by hand")
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = Session(http, "cfg-secret").call(RENAME_TOOL, {
                "resourceType": PROFILE, "expectedSignature": stale,
                "name": RESOURCE, "newName": RENAMED_RESOURCE,
            })

        posts = write_requests(gateway, "POST")

    assert envelope(result)["code"] == "conflict"
    assert posts == []
    assert gateway.resource(PROFILE, RESOURCE)["description"] == "changed by hand"


def test_a_rename_onto_an_occupied_destination_is_a_conflict(tmp_path: Path) -> None:
    """D11 collision policy: the destination is checked against the Gateway before
    dispatch, so a collision consumes neither the signature nor the destination."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        gateway.seed_resource(PROFILE, RENAMED_RESOURCE, config={"profile": {"type": "local"}})
        before = gateway.signature(PROFILE, RESOURCE)
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = Session(http, "cfg-secret").call(RENAME_TOOL, {
                "resourceType": PROFILE, "expectedSignature": before,
                "name": RESOURCE, "newName": RENAMED_RESOURCE,
            })

        posts = write_requests(gateway, "POST")

    assert envelope(result)["code"] == "conflict"
    assert posts == [], "an occupied destination must not reach the Gateway"
    assert gateway.signature(PROFILE, RESOURCE) == before


def test_a_rename_names_the_resource_its_current_name(tmp_path: Path) -> None:
    """`name` is the resource being renamed and `newName` the destination; renaming a
    resource to the name it already has changes nothing, so it is refused."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = Session(http, "cfg-secret").call(RENAME_TOOL, {
                "resourceType": PROFILE,
                "expectedSignature": gateway.signature(PROFILE, RESOURCE),
                "name": RESOURCE, "newName": RESOURCE,
            })

        posts = write_requests(gateway, "POST")

    assert envelope(result)["code"] == "invalid_argument"
    assert posts == []


def test_a_rename_outside_the_target_allowlist_is_permission_denied(tmp_path: Path) -> None:
    """The resource being renamed is a Target too, so an unallowlisted source is
    refused before anything executes."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = Session(http, "cfg-secret").call(RENAME_TOOL, {
                "resourceType": PROFILE,
                "expectedSignature": gateway.signature(PROFILE, "MCP_CI_AUDIT_OTHER"),
                "name": "MCP_CI_AUDIT_OTHER", "newName": RENAMED_RESOURCE,
            })

        posts = write_requests(gateway, "POST")

    assert envelope(result)["code"] == "permission_denied"
    assert posts == []


def test_a_non_core_collection_rename_is_refused(tmp_path: Path) -> None:
    """D30 owner ruling 5: a rename addressed to another collection is refused before
    the source is read, so neither collection's resource moves."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        before = gateway.signature(PROFILE, RESOURCE)
        look_alike = gateway.signature(PROFILE, RESOURCE, OTHER_COLLECTION)
        settings = _settings(tmp_path, gateway, targets={RENAME_TOOL: ("*",)})
        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = Session(http, "cfg-secret").call(RENAME_TOOL, {
                "resourceType": PROFILE, "expectedSignature": look_alike,
                "name": RESOURCE, "newName": RENAMED_RESOURCE,
                "collection": OTHER_COLLECTION,
            })

        requests = resource_route_requests(gateway)

    assert envelope(result)["code"] == "invalid_argument"
    assert requests == [], "a non-core collection must not reach the Gateway at all"
    assert gateway.signature(PROFILE, RESOURCE) == before
    assert gateway.signature(PROFILE, RESOURCE, OTHER_COLLECTION) == look_alike


def test_a_rename_into_an_unallowlisted_destination_is_permission_denied(
    tmp_path: Path,
) -> None:
    """D30 §3 Preflight: a rename produces a resource at its destination, so the
    destination must be allowlisted even when the source is."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        before = gateway.signature(PROFILE, RESOURCE)
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = Session(http, "cfg-secret").call(RENAME_TOOL, {
                "resourceType": PROFILE, "expectedSignature": before,
                "name": RESOURCE, "newName": "MCP_CI_NOT_ALLOWLISTED",
            })

        posts = write_requests(gateway, "POST")
        rows = audit_rows(tmp_path)

    error = envelope(result)
    assert error["code"] == "permission_denied"
    assert posts == []
    assert rows[-1]["outcome"] == "denied:target-allowlist:target-not-allowlisted"
    assert rows[-1]["target_id"] == f"{PROFILE}/MCP_CI_NOT_ALLOWLISTED"
    assert gateway.signature(PROFILE, RESOURCE) == before


def test_renaming_a_refused_resource_type_is_permission_denied(tmp_path: Path) -> None:
    with RecordedGateway() as gateway:
        _seed(gateway)
        before = gateway.signature(TOKEN_TYPE, "ignition-mcp-ci")
        settings = _settings(tmp_path, gateway, targets={RENAME_TOOL: ("*",)})
        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = Session(http, "cfg-secret").call(RENAME_TOOL, {
                "resourceType": TOKEN_TYPE, "expectedSignature": before,
                "name": "ignition-mcp-ci", "newName": "ignition-mcp-ci-renamed",
            })

        posts = write_requests(gateway, "POST")

    assert envelope(result)["code"] == "permission_denied"
    assert posts == []
    assert gateway.signature(TOKEN_TYPE, "ignition-mcp-ci") == before


def test_a_singleton_has_no_rename_route_and_says_so(tmp_path: Path) -> None:
    """A singleton cannot change its name, and the Gateway documents no rename route
    for it: the Tool refuses rather than inventing one."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = Session(http, "cfg-secret").call(RENAME_TOOL, {
                "resourceType": SINGLETON_TYPE,
                "expectedSignature": gateway.signature(SINGLETON_TYPE, SINGLETON_NAME),
                "newName": "cobranding-renamed",
            })

        posts = write_requests(gateway, "POST")

    assert envelope(result)["code"] == "unsupported_capability"
    assert posts == []


def test_an_ambiguous_rename_that_did_not_land_is_not_a_success(tmp_path: Path) -> None:
    """A possibly-sent rename whose read-back still shows the source holding the
    caller's signature changed nothing attributable to this call."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        before = gateway.signature(PROFILE, RESOURCE)
        gateway.fail_writes_with("rename", 500)
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = Session(http, "cfg-secret").call(RENAME_TOOL, {
                "resourceType": PROFILE, "expectedSignature": before,
                "name": RESOURCE, "newName": RENAMED_RESOURCE,
            })

        posts = write_requests(gateway, "POST")

    assert envelope(result)["code"] == "conflict"
    assert len(posts) == 1, "the rename was dispatched once and never replayed"
    assert gateway.signature(PROFILE, RESOURCE) == before


def test_an_ambiguous_rename_whose_source_vanished_is_outcome_unknown(tmp_path: Path) -> None:
    """The source is gone and the destination is empty: the state matches neither the
    pre-state nor the intended one, and an ambiguous dispatch may not be replayed."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        before = gateway.signature(PROFILE, RESOURCE)
        gateway.fail_writes_with("rename", 500)
        gateway.race_write_with("rename", deleted=True)
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = Session(http, "cfg-secret").call(RENAME_TOOL, {
                "resourceType": PROFILE, "expectedSignature": before,
                "name": RESOURCE, "newName": RENAMED_RESOURCE,
            })

    assert envelope(result)["code"] == "outcome_unknown"


def test_the_documented_rename_race_window_is_reported_honestly(tmp_path: Path) -> None:
    """D30 §2 accepts that a rename's read-compare cannot be atomic with its dispatch.
    A competing writer that changes the resource inside the window therefore has its
    version renamed, and the call reports the success the Gateway claimed — nothing
    here pretends the window is closed."""

    with RecordedGateway() as gateway:
        _seed(gateway)
        before = gateway.signature(PROFILE, RESOURCE)
        gateway.race_write_with("rename", description="changed by the other writer")
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = Session(http, "cfg-secret").call(RENAME_TOOL, {
                "resourceType": PROFILE, "expectedSignature": before,
                "name": RESOURCE, "newName": RENAMED_RESOURCE,
            })

        posts = write_requests(gateway, "POST")

    assert len(posts) == 1
    body = structured(result)
    assert body["name"] == RENAMED_RESOURCE
    assert body["observedState"]["description"] == "changed by the other writer"


# ------------------------------------------------------------------ contract


def test_the_new_tools_declare_their_precondition_and_destructive_flags() -> None:
    """The three Tools' D30 declarations, pinned where the executor reads them."""

    from ignition_rest_mcp.services.config_mutation import (
        CONFIG_RESOURCE_CREATE,
        CONFIG_RESOURCE_DELETE,
        CONFIG_RESOURCE_RENAME,
    )

    assert [operation.op_id for operation in (
        CONFIG_RESOURCE_CREATE, CONFIG_RESOURCE_DELETE, CONFIG_RESOURCE_RENAME,
    )] == [CREATE_TOOL, DELETE_TOOL, RENAME_TOOL]
    assert not any(operation.destructive for operation in (
        CONFIG_RESOURCE_CREATE, CONFIG_RESOURCE_RENAME,
    ))
    assert CONFIG_RESOURCE_DELETE.destructive is True
    assert all(
        operation.target_denial_code == "permission_denied" and operation.rejection_is_final
        for operation in (CONFIG_RESOURCE_CREATE, CONFIG_RESOURCE_DELETE, CONFIG_RESOURCE_RENAME)
    )
