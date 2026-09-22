"""Phase 4 milestone 4c (ticket #19): ``artifact_delete`` over the D17 ArtifactStore.

D30 gives this Tool a shape no other REST Mutation has: the artifact HTTP route is
dropped, so it dispatches nothing to the Gateway and its capability is the local store.
These tests drive the real server against the recorded Gateway (fixture first) over
Streamable HTTP — plus the artifact data plane for the archives under test — so every
assertion is an MCP-visible result, an observed Gateway request, an artifact-store row
or an audit row, never an internal call.

What the module pins:

- the D08 chain runs for a local effect too: class, operation and Target allowlists,
  the scope-by-effect rule, and the ``decision`` → ``attempt`` → ``result`` ordering;
- D30 §6 visibility: the owning principal or ``ignition.admin``, and an artifact the
  caller cannot see answers exactly as an unknown identifier does (no existence oracle);
- D17 retention: a locked RECOVERY artifact is a ``conflict`` that changes nothing;
- D17 crash safety: a removal interrupted between the ``DELETING`` commit and the
  unlink is finished by ``reconcile``, and the artifact is invisible in between;
- D30's dropped route: the data plane serves no ``DELETE``.
"""

from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
import sys
from typing import Any
import zipfile

import pytest
from starlette.testclient import TestClient

import ignition_rest_mcp.server as server_module
from ignition_rest_mcp.artifacts.local import LocalArtifactStore, quotas_from_settings
from ignition_rest_mcp.errors import GatewayError
from ignition_rest_mcp.safety.policy import CONFIG_MUTATION
from ignition_rest_mcp.services.artifact_delete import ARTIFACT_DELETE
from ignition_rest_mcp.storage.database import Database
from ignition_rest_mcp.storage.schema import STATE_DDL
from phase4_fixtures import (
    ARTIFACT_DELETE_TOOL,
    audit_rows,
    envelope,
    mutation_settings,
    operation_record,
    structured,
    write_requests,
)
from phase4_fixtures import Session as _Session

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tests/harness"))

from recorded_gateway import API_TOKEN, RecordedGateway  # noqa: E402

CONFIG_CREDENTIAL = "cfg-secret"
READER_CREDENTIAL = "reader-secret"
ADMIN_CREDENTIAL = "adm-secret"
#: The D07 principal keys the named static tokens mint (auth.py: the token's *name*).
CONFIG_PRINCIPAL = "static-token:config-agent"
READER_PRINCIPAL = "static-token:reader"
#: The extra Target the D10/MCP path is driven with, and one no allowlist names.
OTHER_TARGET = "artifact-target-not-in-the-allowlist"

ARCHIVE = b""

#: The D30 §6 audit row a successful removal leaves: the safe fields name what was
#: destroyed, and the identifier is the row's target.
SUCCESS_FIELDS = json.dumps(
    {"kind": "project_archive", "retentionClass": "EXPORT", "sensitivity": "CONFIDENTIAL"},
    separators=(",", ":"), sort_keys=True,
)


def _zip(entries: dict[str, bytes] | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in (entries or {"project.json": b'{"title":"p4 artifact delete"}'}).items():
            archive.writestr(zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0)), body)
    return buffer.getvalue()


def _settings(tmp_path: Path, gateway: RecordedGateway | None, **overrides: Any) -> Any:
    values: dict[str, Any] = {
        "data_dir": str(tmp_path),
        "gateway_url": gateway.base_url if gateway is not None else "http://127.0.0.1:8000",
        "gateway_api_token": API_TOKEN,
        "artifact_upload_enabled": True,
    }
    values.update(overrides)
    return mutation_settings(
        ARTIFACT_DELETE_TOOL,
        targets=values.pop("targets", {ARTIFACT_DELETE_TOOL: ("*",)}),
        **values,
    )


def _upload(
    http: TestClient, payload: bytes | None = None, *, credential: str = CONFIG_CREDENTIAL,
) -> str:
    response = http.post(
        "/artifacts?kind=project_archive", content=payload if payload is not None else _zip(),
        headers={"Authorization": "Bearer " + credential, "Content-Type": "application/zip"},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["artifactId"])


def _delete(session: _Session, artifact_id: str) -> dict[str, Any]:
    return session.call(ARTIFACT_DELETE_TOOL, {"artifactId": artifact_id})


def _artifacts(tmp_path: Path) -> list[dict[str, Any]]:
    columns = ("artifact_id", "state", "retention_class", "retention_lock", "owner_principal")

    async def scenario() -> list[dict[str, Any]]:
        db = Database("state", tmp_path / "state.db", STATE_DDL)
        await db.open()
        try:
            rows = await db.run(
                lambda conn: conn.execute(
                    f"SELECT {', '.join(columns)} FROM artifacts ORDER BY artifact_id"
                ).fetchall()
            )
            return [dict(zip(columns, row, strict=True)) for row in rows]
        finally:
            await db.close()

    return asyncio.run(scenario())


def _object_exists(tmp_path: Path, artifact_id: str) -> bool:
    return (tmp_path / "artifacts" / "objects" / artifact_id[:2] / artifact_id).exists()


def _seed_artifact(
    tmp_path: Path, settings: Any, *, owner: str, recovery_locked: bool = False,
) -> str:
    """Publish one READY artifact straight through the store, before the server starts.

    The data plane can only produce EXPORT archives, so the RECOVERY cases need this
    path — the same one the D16 transaction uses when it promotes a baseline.
    """

    async def scenario() -> str:
        db = Database("state", tmp_path / "state.db", STATE_DDL)
        await db.open()
        try:
            store = LocalArtifactStore(db, tmp_path, quotas_from_settings(settings))
            store.prepare()
            writer = await store.create(
                kind="project_archive", sensitivity="CONFIDENTIAL", retention_class="EXPORT",
                owner=owner, filename="seeded.zip", media_type="application/zip",
                correlation_id="fixture", project_name="p4-artifact-delete",
            )
            await writer.write(_zip())
            artifact = await store.publish(writer)
            if recovery_locked:
                await store.promote_recovery(artifact.artifact_id, "txn-fixture")
            return artifact.artifact_id
        finally:
            await db.close()

    return asyncio.run(scenario())


def _install_crash_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail the removal at its ``DELETING`` split point, exactly as a crash would.

    The store's own fail-point hook is what the Phase 3 artifact tests use for every
    other durable split point; this installs the same hook on the store the server
    creates, and raises from the point that sits between the ``DELETING`` commit and
    the unlink.
    """

    original = LocalArtifactStore.__init__

    def __init__(self: Any, db: Any, data_dir: Path, quotas: Any) -> None:
        original(self, db, data_dir, quotas)
        self._fail_hook = _crash_at_deleting

    monkeypatch.setattr(LocalArtifactStore, "__init__", __init__)


def _crash_at_deleting(point: str) -> None:
    if point == "deleting":
        raise RuntimeError("simulated crash after the DELETING commit")


# ------------------------------------------------------------- the happy path


def test_an_allowlisted_delete_removes_the_artifact_and_reports_absence(tmp_path: Path) -> None:
    """The whole flow: an uploaded archive is removed by the Tool, the artifact is
    gone from every read path and from the store, and the call reports the absence."""

    with RecordedGateway() as gateway:
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            artifact_id = _upload(http)
            assert structured(agent.call("artifact_info", {"artifactId": artifact_id}))["artifact"][
                "kind"
            ] == "project_archive"
            result = _delete(agent, artifact_id)
            info = agent.call("artifact_info", {"artifactId": artifact_id})
            listed = structured(agent.call("artifact_list", {}))["items"]

        # D30: the Tool dispatches nothing to the Gateway — it has no route at all.
        writes = [*write_requests(gateway, "POST"), *write_requests(gateway, "DELETE")]

    body = structured(result)
    assert body["artifactId"] == artifact_id
    assert body["kind"] == "project_archive"
    assert body["present"] is False
    assert envelope(info)["code"] == "not_found"
    assert [item["artifactId"] for item in listed] == []
    # The store really removed it: no row and no object left behind.
    assert _artifacts(tmp_path) == []
    assert not _object_exists(tmp_path, artifact_id)
    # D30: the Tool dispatches nothing to the Gateway — it has no route at all.
    assert writes == []
    assert operation_record(tmp_path, body["correlationId"]) == (
        ARTIFACT_DELETE_TOOL, "succeeded", None,
    )


def test_a_second_delete_is_not_found_and_changes_nothing(tmp_path: Path) -> None:
    """D17: deletion is idempotent for the caller's purposes — the second call is a
    `not_found`, never a second effect and never a quiet success."""

    with RecordedGateway() as gateway:
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            artifact_id = _upload(http)
            structured(_delete(agent, artifact_id))
            second = _delete(agent, artifact_id)

        rows = audit_rows(tmp_path)

    assert envelope(second)["code"] == "not_found"
    assert _artifacts(tmp_path) == []
    assert [(row["phase"], row["outcome"]) for row in rows if row["tool"] == ARTIFACT_DELETE_TOOL] == [
        ("decision", "allowed"), ("attempt", "attempted"), ("result", "completed"),
        ("decision", "denied:precondition:not_found"),
    ]


def test_the_delete_is_audited_in_the_phase_three_order(tmp_path: Path) -> None:
    """D18/D08: an allowed Mutation is audited — one decision, one attempt before the
    effect, one result after it — and the row says what was destroyed."""

    with RecordedGateway() as gateway:
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = _delete(_Session(http, CONFIG_CREDENTIAL), _upload(http))

        rows = audit_rows(tmp_path)

    assert structured(result)["correlationId"] == rows[0]["correlation_id"]
    assert [
        (row["phase"], row["outcome"], row["operation_class"], row["destructive"],
         row["actor_key"], row["target_id"], row["safe_fields_json"])
        for row in rows
    ] == [
        ("decision", "allowed", "CONFIG", 1, CONFIG_PRINCIPAL, structured(result)["artifactId"],
         SUCCESS_FIELDS),
        ("attempt", "attempted", "CONFIG", 1, CONFIG_PRINCIPAL,
         structured(result)["artifactId"], SUCCESS_FIELDS),
        ("result", "completed", "CONFIG", 1, CONFIG_PRINCIPAL,
         structured(result)["artifactId"], "{}"),
    ]


# ------------------------------------------------------------------ visibility


def test_an_artifact_of_another_principal_is_not_found_and_survives(tmp_path: Path) -> None:
    """D30 §6: only the owner may remove an artifact. A caller that cannot see it is
    answered exactly as if it did not exist, and the artifact is untouched."""

    with RecordedGateway() as gateway:
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            reader = _Session(http, READER_CREDENTIAL)
            artifact_id = _upload(http, credential=READER_CREDENTIAL)
            result = _delete(agent, artifact_id)
            still_listed = structured(reader.call("artifact_list", {}))["items"]

        rows = audit_rows(tmp_path)

    assert envelope(result)["code"] == "not_found"
    # The denial is audited, and nothing followed it: no attempt, no result.
    assert [(row["phase"], row["outcome"], row["actor_key"]) for row in rows] == [
        ("decision", "denied:precondition:not_found", CONFIG_PRINCIPAL),
    ]
    assert [item["artifactId"] for item in still_listed] == [artifact_id]
    assert [row["state"] for row in _artifacts(tmp_path)] == ["READY"]
    assert _object_exists(tmp_path, artifact_id)


def test_the_owning_configuration_principal_can_remove_what_it_exported(tmp_path: Path) -> None:
    """The positive half of D30 §6 through the export ingress: the CONFIG credential
    exports a Tag document, owns the artifact, and removes it — and the result names
    the kind it removed."""

    with RecordedGateway() as gateway:
        gateway.seed_tags("default", [{"name": "P4_Artifact", "tagType": "AtomicTag"}])
        settings = _settings(tmp_path, gateway, sensitive_exports_enabled=True)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            exported = structured(agent.call("tag_config_export", {
                "provider": "default", "path": "", "recursive": True, "includeUdts": False,
            }))
            artifact_id = str(exported["artifact"]["artifactId"])
            result = _delete(agent, artifact_id)

    body = structured(result)
    assert body["kind"] == "tag_config_export"
    assert body["present"] is False
    assert _artifacts(tmp_path) == []
    assert audit_rows(tmp_path)[0]["actor_key"] == CONFIG_PRINCIPAL


def test_a_read_only_owner_still_needs_the_config_scope(tmp_path: Path) -> None:
    """D07: ownership is not authorization. Removing an artifact is a CONFIG effect,
    so a read-only principal cannot remove even the artifact it owns — and the
    refusal is audited as a destructive CONFIG decision."""

    with RecordedGateway() as gateway:
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            reader = _Session(http, READER_CREDENTIAL)
            artifact_id = _upload(http, credential=READER_CREDENTIAL)
            result = _delete(reader, artifact_id)

        rows = audit_rows(tmp_path)

    assert envelope(result)["code"] == "permission_denied"
    assert [
        (row["phase"], row["outcome"], row["actor_key"], row["destructive"])
        for row in rows
    ] == [("decision", "denied:authz-scope:missing-scope:ignition.config", READER_PRINCIPAL, 1)]
    assert [row["state"] for row in _artifacts(tmp_path)] == ["READY"]


def test_an_ignition_admin_principal_may_remove_another_principals_artifact(tmp_path: Path) -> None:
    """D30 §6 names the second authority: `ignition.admin` deletes what it can see."""

    with RecordedGateway() as gateway:
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            admin = _Session(http, ADMIN_CREDENTIAL)
            artifact_id = _upload(http, credential=READER_CREDENTIAL)
            result = _delete(admin, artifact_id)

    assert structured(result)["present"] is False
    assert _artifacts(tmp_path) == []
    assert not _object_exists(tmp_path, artifact_id)


def test_an_unknown_identifier_is_not_found_without_touching_the_store(tmp_path: Path) -> None:
    with RecordedGateway() as gateway:
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = _delete(_Session(http, CONFIG_CREDENTIAL), "0" * 32)

        rows = audit_rows(tmp_path)

    assert envelope(result)["code"] == "not_found"
    assert [(row["phase"], row["outcome"]) for row in rows] == [
        ("decision", "denied:precondition:not_found"),
    ]


# ------------------------------------------------------------------ retention


def test_a_retention_locked_recovery_artifact_is_a_conflict_and_survives(tmp_path: Path) -> None:
    """D17/D30 §6: the D16 recovery lock outranks this Tool. The removal is refused
    with `conflict`, the artifact keeps its lock, and nothing was unlinked."""

    with RecordedGateway() as gateway:
        settings = _settings(tmp_path, gateway)
        artifact_id = _seed_artifact(tmp_path, settings, owner=CONFIG_PRINCIPAL, recovery_locked=True)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            result = _delete(agent, artifact_id)
            still_listed = structured(agent.call("artifact_list", {}))["items"]

        rows = audit_rows(tmp_path)

    error = envelope(result)
    assert error["code"] == "conflict"
    assert "retention-locked" in error["message"]
    # The refusal is audited as a rejection, and the lifecycle records the failure.
    assert [(row["phase"], row["outcome"], row["error_code"]) for row in rows] == [
        ("decision", "allowed", None),
        ("attempt", "attempted", None),
        ("result", "rejected", "conflict"),
        ("result", "failed", "conflict"),
    ]
    assert [item["artifactId"] for item in still_listed] == [artifact_id]
    assert [(row["state"], row["retention_lock"]) for row in _artifacts(tmp_path)] == [("READY", 1)]
    assert _object_exists(tmp_path, artifact_id)


def test_a_released_recovery_artifact_can_be_removed(tmp_path: Path) -> None:
    """The lock is the only refusal: once the owning transaction releases it, the same
    artifact is removable."""

    with RecordedGateway() as gateway:
        settings = _settings(tmp_path, gateway)
        artifact_id = _seed_artifact(tmp_path, settings, owner=CONFIG_PRINCIPAL, recovery_locked=True)

        async def release() -> None:
            db = Database("state", tmp_path / "state.db", STATE_DDL)
            await db.open()
            try:
                store = LocalArtifactStore(db, tmp_path, quotas_from_settings(settings))
                await store.release_retention(artifact_id)
            finally:
                await db.close()

        asyncio.run(release())
        with TestClient(server_module.create_server(settings).http_app()) as http:
            result = _delete(_Session(http, CONFIG_CREDENTIAL), artifact_id)

    assert structured(result)["present"] is False
    assert _artifacts(tmp_path) == []


# ------------------------------------------------------------------ crash safety


def test_a_removal_interrupted_at_deleting_is_finished_by_reconcile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D17: the DELETING commit is the removal's durable point.

    A crash between that commit and the unlink leaves the artifact invisible to every
    consumer — and the store's reconcile finishes the job, removing the object and the
    row, so no partial removal survives a restart.
    """

    _install_crash_hook(monkeypatch)
    with RecordedGateway() as gateway:
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            artifact_id = _upload(http)
            crashed = _delete(agent, artifact_id)
            # The artifact left the visible state, which is what DELETING means.
            info = agent.call("artifact_info", {"artifactId": artifact_id})
            listed = structured(agent.call("artifact_list", {}))["items"]

        rows = audit_rows(tmp_path)

    assert envelope(crashed)["code"] == "internal_error"
    assert envelope(info)["code"] == "not_found"
    assert listed == []
    assert [row["state"] for row in _artifacts(tmp_path)] == ["DELETING"]
    # Nothing was lost: the object is still on disk until the recovery pass runs.
    assert _object_exists(tmp_path, artifact_id)
    assert [(row["phase"], row["outcome"]) for row in rows] == [
        ("decision", "allowed"), ("attempt", "attempted"), ("result", "failed"),
    ]

    async def recover() -> dict[str, int]:
        db = Database("state", tmp_path / "state.db", STATE_DDL)
        await db.open()
        try:
            store = LocalArtifactStore(db, tmp_path, quotas_from_settings(settings))
            store.prepare()
            return await store.reconcile(batch=10, deadline_seconds=10.0)
        finally:
            await db.close()

    stats = asyncio.run(recover())
    assert stats["finished_delete"] == 1
    assert _artifacts(tmp_path) == []
    assert not _object_exists(tmp_path, artifact_id)


# ------------------------------------------------------------------ policy


def test_a_target_outside_the_allowlist_is_permission_denied(tmp_path: Path) -> None:
    """D30 §7/D30 §3: the Target is denied before the artifact is read, so the
    identifier is never resolved and nothing is removed."""

    with RecordedGateway() as gateway:
        settings = _settings(
            tmp_path, gateway, targets={ARTIFACT_DELETE_TOOL: (OTHER_TARGET,)},
        )
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            artifact_id = _upload(http)
            result = _delete(agent, artifact_id)

        rows = audit_rows(tmp_path)

    assert envelope(result)["code"] == "permission_denied"
    assert [(row["phase"], row["outcome"], row["target_id"]) for row in rows] == [
        ("decision", "denied:target-allowlist:target-not-allowlisted", artifact_id),
    ]
    assert [row["state"] for row in _artifacts(tmp_path)] == ["READY"]
    assert _object_exists(tmp_path, artifact_id)


def test_a_read_only_credential_never_reaches_the_handler(tmp_path: Path) -> None:
    """D07: the Tool's effect is CONFIG, so a read-only credential is refused — and
    the refusal is audited as a destructive CONFIG denial, before any store access."""

    with RecordedGateway() as gateway:
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            artifact_id = _upload(http)
            result = _Session(http, READER_CREDENTIAL).call(
                ARTIFACT_DELETE_TOOL, {"artifactId": artifact_id},
            )

        rows = audit_rows(tmp_path)

    assert envelope(result)["code"] == "permission_denied"
    assert [
        (row["phase"], row["outcome"], row["operation_class"], row["destructive"])
        for row in rows
    ] == [("decision", "denied:authz-scope:missing-scope:ignition.config", "CONFIG", 1)]
    assert [row["state"] for row in _artifacts(tmp_path)] == ["READY"]


def test_a_malformed_identifier_is_refused_before_the_chain(tmp_path: Path) -> None:
    """D10 input validation: the identifier is the store's own key, so anything that
    cannot be one is refused — including a traversal attempt — and no audit row is
    written because nothing was attempted."""

    with RecordedGateway() as gateway:
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            traversal = _delete(agent, "../artifacts/objects/x")
            oversize = _delete(agent, "a" * 129)

        rows = audit_rows(tmp_path)

    assert envelope(traversal)["code"] == "invalid_argument"
    assert envelope(oversize)["code"] == "invalid_argument"
    assert rows == []
    assert [row["state"] for row in _artifacts(tmp_path)] == []


@pytest.mark.parametrize("class_enabled", [True, False])
def test_the_class_gate_decides_discovery(tmp_path: Path, class_enabled: bool) -> None:
    """D08: the mutation class is enablement, and the Tool is hidden without it —
    the deployment gate is the only thing deciding discovery for a Tool with no
    Gateway route."""

    with RecordedGateway() as gateway:
        settings = _settings(tmp_path, gateway, config_mutation_enabled=class_enabled)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            names = _Session(http, CONFIG_CREDENTIAL).tools()

    assert (ARTIFACT_DELETE_TOOL in names) is class_enabled


def test_the_tool_stays_discoverable_when_the_gateway_is_unreachable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D30 drops the artifact route, so no Gateway capability carries this Tool. A
    deployment must still be able to collect its own artifacts when the Gateway is
    down, while the capability-gated Tools correctly disappear."""

    with RecordedGateway() as gateway:
        settings = _settings(tmp_path, gateway)

        async def unreachable(self: Any) -> bytes:
            raise GatewayError("gateway_unavailable", "the Gateway is unreachable")

        monkeypatch.setattr(server_module.GatewayClient, "openapi", unreachable)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            names = _Session(http, CONFIG_CREDENTIAL).tools()

    assert ARTIFACT_DELETE_TOOL in names
    assert "project_list" not in names


def test_the_data_plane_serves_no_delete_route(tmp_path: Path) -> None:
    """D30: the artifact HTTP data plane keeps GET, HEAD and POST. The Tool is the
    only delete path, so the dropped route must stay dropped."""

    with RecordedGateway() as gateway:
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            artifact_id = _upload(http)
            headers = {"Authorization": "Bearer " + CONFIG_CREDENTIAL}
            deleted = http.delete(f"/artifacts/{artifact_id}", headers=headers)
            fetched = http.get(f"/artifacts/{artifact_id}", headers=headers)

    assert deleted.status_code == 405, deleted.text
    assert fetched.status_code == 200, "the artifact is still there: the route never ran"
    assert _artifacts(tmp_path)[0]["state"] == "READY"


def test_the_operation_declares_the_local_capability_and_the_config_class() -> None:
    """D30 §6/D30 §7 at the operation: CONFIG class, destructive, `permission_denied`
    for a refused Target, a final rejection — and no Gateway route to check, so the
    D08 capability layer is the local store the Tool resolved."""

    assert ARTIFACT_DELETE.mutation_class == CONFIG_MUTATION
    assert ARTIFACT_DELETE.destructive is True
    assert ARTIFACT_DELETE.target_denial_code == "permission_denied"
    assert ARTIFACT_DELETE.rejection_is_final is True
    assert ARTIFACT_DELETE.gateway_backed is False
    assert ARTIFACT_DELETE.capability == "artifact_store"
