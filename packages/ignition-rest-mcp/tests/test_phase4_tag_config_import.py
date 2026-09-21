"""Phase 4 milestone 4c (ticket #17): ``tag_config_import``.

D11's Phase 4 amendment makes this Tool create Tags only: ``collisionPolicy=Abort`` is
always sent and the caller cannot choose it, so an existing Tag refuses the whole import
instead of being overwritten. D30 §2 gives it no Precondition token — the collision
policy is its concurrency rule — and its verification is the bounded re-export of the
same provider and path, returned as Observed state.

These tests drive the real server against the recorded Gateway (fixture first) over
Streamable HTTP, so every assertion is an MCP-visible result, an observed Gateway
request or a persisted row — never an internal call. The recorded Gateway models the
provider's Tag state, so an import this server dispatched is visible to the re-export
that verifies it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

from starlette.testclient import TestClient

import ignition_rest_mcp.server as server_module
from ignition_rest_mcp.artifacts.local import LocalArtifactStore, quotas_from_settings
from ignition_rest_mcp.authorization import scope_tag
from ignition_rest_mcp.storage.database import Database
from ignition_rest_mcp.storage.schema import STATE_DDL
from phase4_fixtures import (
    CONFIG,
    TAG_IMPORT_TOOL,
    audit_rows,
    envelope,
    mutation_settings,
    structured,
    write_requests,
)
from phase4_fixtures import Session as _Session

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tests/harness"))

from recorded_gateway import API_TOKEN, RecordedGateway  # noqa: E402

PROVIDER = "MCP_CI_TAGS"
OTHER_PROVIDER = "MCP_CI_TAGS_OTHER"
#: The path the artifact was exported from, and the two destinations the cases use.
SOURCE_PATH = "source"
TARGET_PATH = "target"
#: A path the Target allowlist deliberately does not name.
OTHER_PATH = "not-allowlisted"

CONFIG_CREDENTIAL = "cfg-secret"
READER_CREDENTIAL = "reader-secret"
CONFIG_PRINCIPAL = "static-token:config-agent"
READER_PRINCIPAL = "static-token:reader"

#: The Tag paths the source document declares, and therefore the ones the import
#: creates and the re-export has to show. The document is the export of ``source``, so
#: its own root node is part of what it declares (the reading the live harness's
#: convention probe records).
DECLARED = (
    f"{TARGET_PATH}/source",
    f"{TARGET_PATH}/source/Alpha",
    f"{TARGET_PATH}/source/Nested",
    f"{TARGET_PATH}/source/Nested/Beta",
)


def _tag(name: str, *, tag_type: str = "AtomicTag", children: list[dict[str, Any]] | None = None,
         dataType: str = "Int4", value: Any = 1) -> dict[str, Any]:
    node: dict[str, Any] = {
        "name": name, "tagType": tag_type, "valueSource": "memory", "enabled": True,
    }
    if children is None:
        node["dataType"] = dataType
        node["value"] = value
    else:
        node["tags"] = children
    return node


def _source_tags() -> list[dict[str, Any]]:
    """One folder of Tags under the exported path, plus a Tag the import must not touch."""

    return [
        _tag("source", tag_type="Folder", children=[
            _tag("Alpha", value=7),
            _tag("Nested", tag_type="Folder", children=[
                _tag("Beta", dataType="String", value="b"),
            ]),
        ]),
        _tag("unrelated", dataType="String", value="keep"),
    ]


def _settings(tmp_path: Path, gateway: RecordedGateway, *, targets: dict[str, tuple[str, ...]] | None = None,
              operations: tuple[str, ...] = (TAG_IMPORT_TOOL,), **overrides: Any) -> Any:
    values: dict[str, Any] = {
        "data_dir": str(tmp_path),
        "gateway_url": gateway.base_url,
        "gateway_api_token": API_TOKEN,
        "gateway_id": "gw-p4-tag-import",
        "sensitive_exports_enabled": True,
    }
    values.update(overrides)
    if targets is None:
        targets = {TAG_IMPORT_TOOL: (f"[{PROVIDER}]{TARGET_PATH}",)}
    return mutation_settings(*operations, targets=targets, **values)


def _seed_gateway() -> RecordedGateway:
    gateway = RecordedGateway()
    gateway.seed_tags(PROVIDER, _source_tags())
    gateway.seed_tags(OTHER_PROVIDER, [])
    return gateway


def _export(session: _Session, provider: str = PROVIDER, path: str = SOURCE_PATH) -> dict[str, Any]:
    return structured(session.call("tag_config_export", {
        "provider": provider, "path": path, "recursive": True, "includeUdts": False,
    }))


def _artifact(session: _Session, provider: str = PROVIDER, path: str = SOURCE_PATH) -> str:
    return str(_export(session, provider, path)["artifact"]["artifactId"])


def _import(
    session: _Session, artifact_id: str, *, provider: str = PROVIDER, path: str = TARGET_PATH,
) -> dict[str, Any]:
    return session.call(TAG_IMPORT_TOOL, {
        "artifactId": artifact_id, "provider": provider, "path": path,
    })


def _import_requests(gateway: RecordedGateway) -> list[dict[str, Any]]:
    return [
        request for request in write_requests(gateway, "POST")
        if str(request["path"]).startswith("/data/api/v1/tags/import")
    ]


def _export_requests(gateway: RecordedGateway) -> list[str]:
    return [
        str(request["path"]) for request in gateway.requests
        if str(request["path"]).startswith("/data/api/v1/tags/export")
    ]


def _audit(tmp_path: Path, tool: str = TAG_IMPORT_TOOL) -> list[dict[str, Any]]:
    return [row for row in audit_rows(tmp_path) if row["tool"] == tool]


def _outcomes(tmp_path: Path) -> list[str]:
    return [row["outcome"] for row in _audit(tmp_path)]


async def _publish_document(tmp_path: Path, settings: Any, document: Any, *, owner: str = CONFIG_PRINCIPAL,
                            kind: str = "tag_config_export", filename: str = "tags.json") -> str:
    """Publish a Tag export artifact straight through the store.

    The public ingress route accepts Project archives only, so a document that no
    ``tag_config_export`` call could have produced (an oversize one, an undecodable one,
    an artifact of another kind) is published here to prove the Tool refuses it anyway.
    """

    db = Database("state", tmp_path / "state.db", STATE_DDL)
    await db.open()
    try:
        store = LocalArtifactStore(db, tmp_path, quotas_from_settings(settings))
        payload = document if isinstance(document, bytes) else json.dumps(document).encode("utf-8")
        writer = await store.create(
            kind=kind, sensitivity="CONFIDENTIAL", retention_class="EXPORT",
            owner=owner, filename=filename,
            media_type="application/json" if kind == "tag_config_export" else "application/zip",
            correlation_id="fixture",
        )
        await writer.write(payload)
        artifact = await store.publish(writer)
    finally:
        await db.close()
    return artifact.artifact_id


# ------------------------------------------------------------------ the happy path


def test_an_allowlisted_import_creates_the_declared_tags_and_reports_the_reexport(
    tmp_path: Path,
) -> None:
    """The whole flow: export the provider, import the artifact under an allowlisted
    path, and see the created Tags — and the bounded re-export that verified them."""

    with _seed_gateway() as gateway:
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            artifact_id = _artifact(agent)
            result = _import(agent, artifact_id)

    body = structured(result)
    assert body["provider"] == PROVIDER
    assert body["path"] == TARGET_PATH
    assert body["artifact"]["artifactId"] == artifact_id
    assert body["artifact"]["kind"] == "tag_config_export"
    assert body["observedState"] == {"present": list(DECLARED), "missing": []}
    # The Gateway really created them: they are in the provider's Tag state, and the
    # Tag the document did not select is untouched.
    assert gateway.tags(PROVIDER, f"{TARGET_PATH}/source/Nested") is not None
    assert gateway.tags(PROVIDER, f"{TARGET_PATH}/source/Nested/Beta")["value"] == "b"
    assert gateway.tags(PROVIDER, f"{TARGET_PATH}/source")["tagType"] == "Folder"
    assert gateway.tag_imports == [
        {"provider": PROVIDER, "path": TARGET_PATH, "names": ["source"]},
    ]
    # The import carries D30 §4's fixed collision policy and the documented content
    # type, and its body is the artifact the caller named.
    imports = _import_requests(gateway)
    assert len(imports) == 1
    assert imports[0]["path"] == (
        f"/data/api/v1/tags/import?provider={PROVIDER}&type=json"
        f"&collisionPolicy=Abort&path={TARGET_PATH}"
    )
    assert imports[0]["headers"]["Content-Type"] == "application/octet-stream"
    # The bytes dispatched are the artifact's own bytes, not a re-encoding: the
    # re-export the verification read is compared against exactly what was sent.
    assert hashlib.sha256(imports[0]["body"]).hexdigest() == body["artifact"]["sha256"]
    assert json.loads(imports[0]["body"])["name"] == "source"
    # The verification read the same provider and path back (D30 §6 Observed state),
    # after the import rather than instead of it.
    exports = _export_requests(gateway)
    assert len(exports) == 3  # the caller's source export, then the two verifications
    assert f"provider={PROVIDER}" in exports[-1] and f"path={TARGET_PATH}" in exports[-1]


def test_the_import_does_not_change_tags_outside_the_target_path(tmp_path: Path) -> None:
    """D30 §4: 'Abort' keeps the import inside its named Target — it creates Tags."""

    with _seed_gateway() as gateway:
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            unchanged = gateway.tags(PROVIDER, "source")
            _import(agent, _artifact(agent))

    assert gateway.tags(PROVIDER, "source") == unchanged
    assert gateway.tags(PROVIDER, "unrelated")["value"] == "keep"


def test_the_import_is_audited_with_the_provider_and_path(tmp_path: Path) -> None:
    with _seed_gateway() as gateway:
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            _import(agent, _artifact(agent))

    phases = [(row["phase"], row["outcome"]) for row in _audit(tmp_path)]
    assert phases == [("decision", "allowed"), ("attempt", "attempted"), ("result", "completed")]
    decision = _audit(tmp_path)[0]
    assert decision["target_id"] == f"[{PROVIDER}]{TARGET_PATH}"
    assert json.loads(decision["safe_fields_json"]) == {"provider": PROVIDER, "path": TARGET_PATH}


def test_the_tool_is_registered_as_a_config_mutation_without_being_destructive(
    tmp_path: Path,
) -> None:
    with _seed_gateway() as gateway:
        settings = _settings(tmp_path, gateway)
        server = server_module.create_server(settings)
        with TestClient(server.http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            tools = agent.tools()
            tool = asyncio.run(server.get_tool(TAG_IMPORT_TOOL))

    assert TAG_IMPORT_TOOL in tools
    assert scope_tag(CONFIG) in (tool.tags or set())
    assert "destructive" not in (tool.tags or set())
    assert "mutation" in (tool.tags or set())


# ------------------------------------------------------------------ the collision rule


def test_a_destination_that_already_holds_a_declared_tag_is_a_conflict(tmp_path: Path) -> None:
    """D11's collision policy, checked against the Gateway before anything is sent."""

    with _seed_gateway() as gateway:
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            artifact_id = _artifact(agent)
            gateway.change_tags_out_of_band(PROVIDER, TARGET_PATH, [_tag("source", tag_type="Folder")])
            result = _import(agent, artifact_id)

    assert envelope(result)["code"] == "conflict"
    assert _import_requests(gateway) == []
    # Nothing was dispatched, so the competing Tag keeps its own value.
    assert gateway.tags(PROVIDER, f"{TARGET_PATH}/source")["tagType"] == "Folder"


def test_the_gateway_refusal_of_a_collision_is_final(tmp_path: Path) -> None:
    """A competing writer lands the whole document between the check and the dispatch.

    The Gateway then refuses the import inside its 200 (the recorded ``Abort`` answer,
    ``failureCount`` with ``qualitySubCode`` 527). The refusal is the result: the Tags
    that are present are another writer's, so no read-back may report them as this
    call's success.
    """

    with _seed_gateway() as gateway:
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            artifact_id = _artifact(agent)
            gateway.race_tag_import_with(PROVIDER, TARGET_PATH, [
                _tag("source", tag_type="Folder", children=[
                    _tag("Alpha", value=1), _tag("Nested", tag_type="Folder", children=[
                        _tag("Beta", dataType="String", value="b"),
                    ]),
                ]),
            ])
            result = _import(agent, artifact_id)

    assert envelope(result)["code"] == "conflict"
    assert gateway.tags(PROVIDER, f"{TARGET_PATH}/source/Nested/Beta") is not None
    assert len(_import_requests(gateway)) == 1
    # The executor's own result row says the Gateway refused this call.
    assert "rejected" in _outcomes(tmp_path)


def test_a_reported_import_failure_that_is_not_a_collision_is_an_upstream_error(
    tmp_path: Path,
) -> None:
    with _seed_gateway() as gateway:
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            artifact_id = _artifact(agent)
            gateway.refuse_tag_imports_with("Provider is still starting")
            result = _import(agent, artifact_id)

    assert envelope(result)["code"] == "upstream_error"
    assert gateway.tags(PROVIDER, TARGET_PATH) is None


def test_the_documented_qualitycode_list_shape_is_read_as_a_refusal(tmp_path: Path) -> None:
    """The committed OpenAPI documents a list of non-Good QualityCodes, not the summary
    object live 8.3.8/8.3.9 answer; the Tool reads both."""

    with _seed_gateway() as gateway:
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            artifact_id = _artifact(agent)
            gateway.answer_tag_import_failures_with("list")
            gateway.refuse_tag_imports_with("Tag '[MCP_CI_TAGS]target/Alpha' already exists")
            result = _import(agent, artifact_id)

    assert envelope(result)["code"] == "conflict"


# ------------------------------------------------------------- ambiguous dispatches


def test_an_ambiguous_dispatch_that_created_nothing_is_not_applied(tmp_path: Path) -> None:
    with _seed_gateway() as gateway:
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            artifact_id = _artifact(agent)
            gateway.fail_tag_imports_with(500)
            result = _import(agent, artifact_id)

    assert envelope(result)["code"] == "conflict"
    assert "did not land" in envelope(result)["message"]
    assert gateway.tags(PROVIDER, TARGET_PATH) is None
    assert "not_applied" in _outcomes(tmp_path)


def test_an_ambiguous_dispatch_whose_tags_are_present_is_outcome_unknown(tmp_path: Path) -> None:
    """The lesson of #14/#15 on this Tool: a re-export showing the intended Tags does
    not attribute them to this call, so an ambiguous dispatch stays unresolved."""

    with _seed_gateway() as gateway:
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            artifact_id = _artifact(agent)
            gateway.race_tag_import_with(PROVIDER, TARGET_PATH, [
                _tag("source", tag_type="Folder", children=[
                    _tag("Alpha", value=1), _tag("Nested", tag_type="Folder", children=[
                        _tag("Beta", dataType="String", value="b"),
                    ]),
                ]),
            ])
            gateway.fail_tag_imports_with(500)
            result = _import(agent, artifact_id)

    assert envelope(result)["code"] == "outcome_unknown"
    assert gateway.tags(PROVIDER, f"{TARGET_PATH}/source/Alpha") is not None
    assert "outcome_unknown" in _outcomes(tmp_path)


def test_a_partial_import_is_recovery_required_and_never_a_success(tmp_path: Path) -> None:
    """A report of some successes and some failures is not a claim: the Gateway did not
    refuse the call and did not complete it either, so the final state is unresolved."""

    with _seed_gateway() as gateway:
        settings = _settings(tmp_path, gateway)
        document = {"tags": [_tag("Alpha"), _tag("Beta")]}
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            # The document is a provider-root shape with two root nodes, so exactly one
            # of them can land.
            artifact_id = asyncio.run(_publish_document(tmp_path, settings, document))
            gateway.partial_tag_imports_with(1)
            result = _import(agent, artifact_id)

    body = envelope(result)
    assert body["code"] == "outcome_unknown"
    # The message names what the bounded re-export was not showing, so the caller can
    # reconcile without another read.
    assert "Beta" in body["message"]
    assert gateway.tags(PROVIDER, f"{TARGET_PATH}/Alpha") is not None
    assert gateway.tags(PROVIDER, f"{TARGET_PATH}/Beta") is None


def test_a_claimed_success_whose_tags_are_missing_is_recovery_required(tmp_path: Path) -> None:
    """A claimed success is confirmed only by the bounded re-export (D30 §2)."""

    with _seed_gateway() as gateway:
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            artifact_id = _artifact(agent)
            # The Gateway accepts the import and creates nothing: the claim stands,
            # the state does not, and nothing may report that as a success.
            gateway.claim_tag_imports_without_applying()
            result = _import(agent, artifact_id)

    assert envelope(result)["code"] == "outcome_unknown"
    assert "recovery_required" in _outcomes(tmp_path)


# ------------------------------------------------------------------ the D08 chain


def test_a_target_outside_the_allowlist_is_permission_denied(tmp_path: Path) -> None:
    with _seed_gateway() as gateway:
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            artifact_id = _artifact(agent)
            result = _import(agent, artifact_id, path=OTHER_PATH)

    assert envelope(result)["code"] == "permission_denied"
    assert _import_requests(gateway) == []
    assert gateway.tags(PROVIDER, OTHER_PATH) is None
    assert gateway.tag_imports == []


def test_a_provider_outside_the_allowlist_is_permission_denied(tmp_path: Path) -> None:
    """The Target names the provider as well as the path (the allowlist entry here is
    the provider root, so a sibling provider is a different Target)."""

    with _seed_gateway() as gateway:
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            artifact_id = _artifact(agent)
            result = _import(agent, artifact_id, provider=OTHER_PROVIDER)

    assert envelope(result)["code"] == "permission_denied"
    assert _import_requests(gateway) == []


def test_the_provider_root_is_addressable_as_an_explicit_target(tmp_path: Path) -> None:
    """An empty path is the provider root, and the Target identity says so."""

    with _seed_gateway() as gateway:
        gateway.seed_tags("MCP_CI_TAGS_ROOT", [_tag("existing", dataType="String", value="e")])
        settings = _settings(tmp_path, gateway, targets={TAG_IMPORT_TOOL: ("[MCP_CI_TAGS_ROOT]",)})
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            artifact_id = _artifact(agent)
            result = _import(agent, artifact_id, provider="MCP_CI_TAGS_ROOT", path="")

    body = structured(result)
    assert body["path"] == ""
    assert body["observedState"]["present"] == ["source", "source/Alpha", "source/Nested",
                                                "source/Nested/Beta"]
    assert gateway.tags("MCP_CI_TAGS_ROOT", "source/Nested/Beta") is not None
    # The Tag the root already held is untouched: the import created Tags only.
    assert gateway.tags("MCP_CI_TAGS_ROOT", "existing")["value"] == "e"


def test_a_read_only_principal_is_denied_the_mutation(tmp_path: Path) -> None:
    with _seed_gateway() as gateway:
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            reader = _Session(http, READER_CREDENTIAL)
            artifact_id = _artifact(reader)
            result = _import(reader, artifact_id)
            visible = TAG_IMPORT_TOOL in reader.tools()

    assert envelope(result)["code"] == "permission_denied"
    # D07: a read-only credential does not even see a CONFIG-scoped Mutation Tool.
    assert not visible
    assert _import_requests(gateway) == []


def test_an_artifact_of_another_principal_answers_not_found(tmp_path: Path) -> None:
    with _seed_gateway() as gateway:
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            reader = _Session(http, READER_CREDENTIAL)
            # The reader may export (READ scope, sensitive exports on), so it owns it.
            artifact_id = _artifact(reader)
            result = _import(agent, artifact_id)

    assert envelope(result)["code"] == "not_found"
    assert _import_requests(gateway) == []


def test_the_class_gate_and_the_capability_decide_the_inventory(tmp_path: Path) -> None:
    with _seed_gateway() as gateway:
        with TestClient(server_module.create_server(_settings(
            tmp_path, gateway, config_mutation_enabled=False,
        )).http_app()) as http:
            disabled = _Session(http, CONFIG_CREDENTIAL).tools()
        with TestClient(server_module.create_server(_settings(
            tmp_path, gateway, mutation_operations=(),
        )).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            visible = TAG_IMPORT_TOOL in agent.tools()
            refused = _import(agent, "a" * 8)

    assert TAG_IMPORT_TOOL not in disabled
    # The capability is present, so the class gate alone decides discovery; the
    # operation allowlist is enforced on the call.
    assert visible
    assert envelope(refused)["code"] == "operation_disabled"


def test_a_gateway_without_the_import_route_hides_the_tool(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    with _seed_gateway() as gateway:
        settings = _settings(tmp_path, gateway)

        async def without_route(self: Any) -> bytes:
            document = json.loads(
                (ROOT / "tests/fixtures/recorded/gateway-8.3/phase2/openapi-required.json")
                .read_text(encoding="utf-8")
            )
            paths = document["paths"]
            assert "/data/api/v1/tags/import" in paths
            paths.pop("/data/api/v1/tags/import")
            return json.dumps(document).encode("utf-8")

        monkeypatch.setattr(server_module.GatewayClient, "openapi", without_route)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            tools = agent.tools()
            result = _import(agent, "a" * 8)

    assert TAG_IMPORT_TOOL not in tools
    # A disabled Tool never reaches the D08 chain, so the refusal is FastMCP's own
    # shape rather than the D06 envelope; what matters is that it is refused.
    assert result.get("isError") is True


def test_a_stale_snapshot_withholds_the_capability_at_call_time(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    """D08: the capability layer is checked again around the dispatch, not only when
    the Tool was made visible."""

    with _seed_gateway() as gateway:
        settings = _settings(tmp_path, gateway)
        original = server_module.CapabilityRegistry.supports
        monkeypatch.setattr(
            server_module.CapabilityRegistry, "supports",
            lambda self, name: name != TAG_IMPORT_TOOL and original(self, name),
        )
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            artifact_id = _artifact(agent)
            result = _import(agent, artifact_id)

    assert envelope(result)["code"] == "unsupported_capability"
    assert _import_requests(gateway) == []


# ------------------------------------------------------------------ input bounds


def test_an_artifact_that_is_not_a_tag_export_is_refused(tmp_path: Path) -> None:
    with _seed_gateway() as gateway:
        settings = _settings(tmp_path, gateway)
        artifact_id = asyncio.run(_publish_document(
            tmp_path, settings, {"tags": [{"name": "Alpha"}]},
            kind="project_archive", filename="not-a-tag-export.zip",
        ))
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            result = _import(agent, artifact_id)

    assert envelope(result)["code"] == "invalid_argument"
    assert _import_requests(gateway) == []


def test_a_document_that_declares_no_tags_is_refused(tmp_path: Path) -> None:
    with _seed_gateway() as gateway:
        settings = _settings(tmp_path, gateway)
        artifact_id = asyncio.run(_publish_document(tmp_path, settings, {"tags": []}))
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            result = _import(agent, artifact_id)

    assert envelope(result)["code"] == "invalid_argument"
    assert _import_requests(gateway) == []


def test_a_document_that_is_not_json_is_refused(tmp_path: Path) -> None:
    with _seed_gateway() as gateway:
        settings = _settings(tmp_path, gateway)
        artifact_id = asyncio.run(_publish_document(tmp_path, settings, b"not json at all"))
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            result = _import(agent, artifact_id)

    assert envelope(result)["code"] == "invalid_argument"
    assert _import_requests(gateway) == []


def test_an_oversize_document_fails_explicitly(tmp_path: Path) -> None:
    """D10: the ceiling is checked before anything is dispatched, and the artifact is
    never partially imported."""

    with _seed_gateway() as gateway:
        settings = _settings(tmp_path, gateway)
        document = {"tags": [_tag(f"Tag{index}") for index in range(501)]}
        artifact_id = asyncio.run(_publish_document(tmp_path, settings, document))
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            result = _import(agent, artifact_id)

    assert envelope(result)["code"] == "limit_exceeded"
    assert _import_requests(gateway) == []
    assert gateway.tag_imports == []


def test_a_document_that_declares_udt_definitions_needs_an_explicit_types_target(
    tmp_path: Path,
) -> None:
    """D30 §6: UDT definitions live at ``[provider]_types_/``, so a document that
    declares them may only be imported into that explicit Target — never into the
    provider root, and a ``_types_`` folder deeper in a path is an ordinary folder."""

    document = {"tags": [
        {"name": "_types_", "tagType": "Folder", "tags": [_tag("McpCiType")]},
        _tag("Alpha"),
    ]}
    with _seed_gateway() as gateway:
        settings = _settings(tmp_path, gateway, targets={TAG_IMPORT_TOOL: (f"[{PROVIDER}]",)})
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            refused = _import(
                agent,
                asyncio.run(_publish_document(tmp_path, settings, document)),
                path="",
            )

    assert envelope(refused)["code"] == "invalid_argument"
    assert _import_requests(gateway) == []

    with _seed_gateway() as gateway:
        settings = _settings(tmp_path, gateway, targets={TAG_IMPORT_TOOL: (f"[{PROVIDER}]_types_",)})
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            accepted = _import(
                agent,
                asyncio.run(_publish_document(tmp_path, settings, {"tags": [_tag("McpCiType")]})),
                path="_types_",
            )

    assert structured(accepted)["observedState"]["present"] == ["_types_/McpCiType"]


def test_a_path_that_could_name_two_targets_is_refused(tmp_path: Path) -> None:
    with _seed_gateway() as gateway:
        settings = _settings(tmp_path, gateway, targets={TAG_IMPORT_TOOL: ("*",)})
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            artifact_id = _artifact(agent)
            results = [
                _import(agent, artifact_id, path=path)
                for path in ("/target", "target/", "target//sub", "target/../target")
            ]

    assert [envelope(result)["code"] for result in results] == ["invalid_argument"] * 4
    assert _import_requests(gateway) == []


def test_a_provider_name_that_could_not_be_a_target_identity_is_refused(tmp_path: Path) -> None:
    with _seed_gateway() as gateway:
        settings = _settings(tmp_path, gateway, targets={TAG_IMPORT_TOOL: ("*",)})
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            artifact_id = _artifact(agent)
            result = _import(agent, artifact_id, provider="tags]x")

    assert envelope(result)["code"] == "invalid_argument"
    assert _import_requests(gateway) == []


def test_an_import_into_a_multi_segment_path_verifies_provider_relative(
    tmp_path: Path,
) -> None:
    """The re-export is normalized against the import path, so a nested destination
    verifies the same way a single-segment one does."""

    with _seed_gateway() as gateway:
        settings = _settings(tmp_path, gateway, targets={TAG_IMPORT_TOOL: (f"[{PROVIDER}]a/b",)})
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            artifact_id = _artifact(agent)
            result = _import(agent, artifact_id, path="a/b")

    assert structured(result)["observedState"]["present"] == [
        "a/b/source", "a/b/source/Alpha", "a/b/source/Nested", "a/b/source/Nested/Beta",
    ]
