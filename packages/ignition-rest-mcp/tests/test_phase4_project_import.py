"""Phase 4 milestone 4c (ticket #16): ``project_import`` over the D16 transaction.

D30 §2 makes a Project fingerprint the Precondition token for this Tool: the caller
reads it with ``project_export`` and hands it back as ``expectedFingerprint``. These
tests drive the real server against the recorded Gateway (fixture first) over
Streamable HTTP, so every assertion is an MCP-visible result, an observed Gateway
request or a persisted row — never an internal call.

The D16 reconcile rule and D30 §2 are pinned together here, because their interaction
is the whole point of the Tool:

- a **rejected** dispatch is final (``rejection_is_final``), so a Project that happens
  to show the candidate after a 4xx is never reported as this caller's success;
- a **genuinely ambiguous** dispatch is reconciled by re-exporting the Project, and
  the import is a recovered success only when that export equals the staged candidate.
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
from ignition_rest_mcp.authorization import scope_tag
from ignition_rest_mcp.projects.transactions import (
    PROJECT_IMPORT_OPERATION,
    PROJECT_IMPORT_TOOL_OPERATION,
)
from ignition_rest_mcp.storage.database import Database
from ignition_rest_mcp.storage.schema import STATE_DDL
from phase4_fixtures import (
    CONFIG,
    IMPORT_TOOL,
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

PROJECT = "mcp-p4-import"
CONTROL_PROJECT = "mcp-p4-import-control"

CONFIG_CREDENTIAL = "cfg-secret"
READER_CREDENTIAL = "reader-secret"
#: The D07 principal key a static token mints: the token's *name* (auth.py), never
#: its value. A resource published straight through the store must carry it.
CONFIG_PRINCIPAL = "static-token:config-agent"

# The recorded Gateway serializes project.json canonically on export and on import, so
# every archive here already uses that exact form: the content fingerprint must survive
# the round trip, or nothing about C == B could be asserted.
BASE_ENTRIES = {"project.json": b'{"title":"p4 import"}'}
EDITED_ENTRIES = {"project.json": b'{"title":"p4 import"}', "mcp-imported.txt": b"imported by the tool"}
FOREIGN_ENTRIES = {"project.json": b'{"title":"p4 import"}', "foreign.txt": b"another writer"}


def _zip(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in entries.items():
            archive.writestr(zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0)), body)
    return buffer.getvalue()


def _entries(payload: bytes | None) -> dict[str, bytes]:
    assert payload is not None, "the Gateway holds no such Project"
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        return {info.filename: archive.read(info.filename) for info in archive.infolist()}


def _settings(tmp_path: Path, gateway: RecordedGateway | None, **overrides: Any) -> Any:
    values: dict[str, Any] = {
        "data_dir": str(tmp_path),
        "gateway_url": gateway.base_url if gateway is not None else "http://127.0.0.1:8000",
        "gateway_api_token": API_TOKEN,
        "project_writer_enabled": True,
        "gateway_id": "gw-p4-import",
        "artifact_upload_enabled": True,
        "sensitive_exports_enabled": True,
    }
    values.update(overrides)
    return mutation_settings(IMPORT_TOOL, targets={IMPORT_TOOL: (PROJECT,)}, **values)


def _seed_gateway() -> RecordedGateway:
    return RecordedGateway(
        projects={
            PROJECT: _zip(BASE_ENTRIES),
            CONTROL_PROJECT: _zip(BASE_ENTRIES),
        },
    )


def _upload(http: TestClient, payload: bytes, *, credential: str = CONFIG_CREDENTIAL) -> str:
    response = http.post(
        "/artifacts?kind=project_archive", content=payload,
        headers={"Authorization": "Bearer " + credential, "Content-Type": "application/zip"},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["artifactId"])


def _import(
    session: _Session, artifact_id: str, *, project: str = PROJECT, fingerprint: str,
) -> dict[str, Any]:
    return session.call(IMPORT_TOOL, {
        "projectName": project, "artifactId": artifact_id, "expectedFingerprint": fingerprint,
    })


def _current_fingerprint(session: _Session, project: str = PROJECT) -> str:
    return str(structured(session.call("project_export", {"projectName": project}))["fingerprint"])


def _import_requests(gateway: RecordedGateway) -> list[dict[str, Any]]:
    return [
        request for request in write_requests(gateway, "POST")
        if str(request["path"]).startswith("/data/api/v1/projects/import/")
    ]


def _export_paths(gateway: RecordedGateway) -> list[str]:
    return [
        str(request["path"]) for request in gateway.requests
        if str(request["path"]).startswith("/data/api/v1/projects/export/")
    ]


def _transactions(tmp_path: Path) -> list[dict[str, Any]]:
    columns = ("transaction_id", "state", "import_dispatched", "error_code")

    async def scenario() -> list[dict[str, Any]]:
        db = Database("state", tmp_path / "state.db", STATE_DDL)
        await db.open()
        try:
            rows = await db.run(
                lambda conn: conn.execute(
                    f"SELECT {', '.join(columns)} FROM project_transactions ORDER BY rowid"
                ).fetchall()
            )
            return [dict(zip(columns, row, strict=True)) for row in rows]
        finally:
            await db.close()

    return asyncio.run(scenario())


def _artifacts(tmp_path: Path) -> list[tuple[str, str, int]]:
    async def scenario() -> list[tuple[str, str, int]]:
        db = Database("state", tmp_path / "state.db", STATE_DDL)
        await db.open()
        try:
            return [
                (str(row[0]), str(row[1]), int(row[2]))
                for row in await db.run(
                    lambda conn: conn.execute(
                        "SELECT state, retention_class, retention_lock FROM artifacts ORDER BY artifact_id"
                    ).fetchall()
                )
            ]
        finally:
            await db.close()

    return asyncio.run(scenario())


async def _publish_unsafe_archive(tmp_path: Path, settings: Any) -> str:
    """Publish bytes that became READY without passing the D15 gate.

    No public ingress can do this — the upload route validates the archive and every
    capture fingerprints it — so the artifact is published straight through the store
    to prove the Tool refuses it anyway (defense in depth).
    """

    db = Database("state", tmp_path / "state.db", STATE_DDL)
    await db.open()
    store = LocalArtifactStore(db, tmp_path, quotas_from_settings(settings))
    writer = await store.create(
        kind="project_archive", sensitivity="CONFIDENTIAL", retention_class="EXPORT",
        owner=CONFIG_PRINCIPAL, filename="unsafe.zip",
        media_type="application/zip", correlation_id="fixture", project_name=PROJECT,
    )
    await writer.write(_zip({"../escape.txt": b"traversal", "project.json": b"{}"}))
    artifact = await store.publish(writer)
    await db.close()
    return artifact.artifact_id


# ---------------------------------------------------------------- the commit path


def test_an_allowlisted_import_commits_and_the_project_really_changed(tmp_path: Path) -> None:
    """The whole flow, end to end: read the Project, upload a changed archive, import
    it under the fingerprint that read produced, and observe the Gateway Project."""

    with _seed_gateway() as gateway:
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            baseline = _current_fingerprint(agent)
            artifact_id = _upload(http, _zip(EDITED_ENTRIES))
            result = _import(agent, artifact_id, fingerprint=baseline)
            diagnose = structured(agent.call("operation_diagnose", {
                "correlationId": structured(result)["correlationId"],
            }))

    body = structured(result)
    assert body["state"] == "COMMITTED"
    assert body["projectName"] == PROJECT
    assert body["baselineFingerprint"] == baseline
    assert body["resultFingerprint"] == body["candidateFingerprint"]
    assert body["baselineFingerprint"] != body["candidateFingerprint"]
    assert body["importDispatched"] is True
    assert body["designerWarning"] is False
    # The Gateway really took the archive: the marker entry is in the stored Project,
    # and the import the server sent carried the documented overwrite parameter.
    assert _entries(gateway.project(PROJECT)) == EDITED_ENTRIES
    imports = _import_requests(gateway)
    assert len(imports) == 1
    assert imports[0]["path"] == f"/data/api/v1/projects/import/{PROJECT}?overwrite=true"
    # The operation record names the transaction (D19), so the caller can follow it.
    assert diagnose["transactionId"] == body["transactionId"]
    assert gateway.imports == [PROJECT]


def test_importing_the_current_content_is_a_no_change(tmp_path: Path) -> None:
    """D16 no-op idempotency: a candidate equal to the baseline is not backed up and
    not imported, and the call is honestly reported as such."""

    with _seed_gateway() as gateway:
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            baseline = _current_fingerprint(agent)
            artifact_id = _upload(http, _zip(BASE_ENTRIES))
            result = _import(agent, artifact_id, fingerprint=baseline)

    body = structured(result)
    assert body["state"] == "NO_CHANGE"
    assert body["candidateFingerprint"] == baseline
    assert body["resultFingerprint"] is None
    assert body["importDispatched"] is False
    assert _import_requests(gateway) == []
    # Nothing was imported, and the one artifact left behind carries no lock.
    assert _entries(gateway.project(PROJECT)) == _entries(gateway.project(PROJECT))
    assert all(row[2] == 0 for row in _artifacts(tmp_path))


def test_the_zip_gate_refuses_an_unsafe_archive_before_dispatch(tmp_path: Path) -> None:
    """D15/D17: the candidate pass re-validates the archive, so an unsafe archive
    cannot reach the Gateway even if it somehow became READY."""

    with _seed_gateway() as gateway:
        settings = _settings(tmp_path, gateway)
        artifact_id = asyncio.run(_publish_unsafe_archive(tmp_path, settings))
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            result = _import(agent, artifact_id, fingerprint=_current_fingerprint(agent))

    assert envelope(result)["code"] == "invalid_argument"
    assert _import_requests(gateway) == []
    assert _entries(gateway.project(PROJECT)) == BASE_ENTRIES
    rows = _transactions(tmp_path)
    assert [row["state"] for row in rows] == ["FAILED_PRE_IMPORT"]
    assert rows[0]["error_code"] == "invalid_argument"
    assert rows[0]["import_dispatched"] == 0


# ------------------------------------------------------------ the precondition


def test_a_stale_fingerprint_conflicts_before_anything_is_dispatched(tmp_path: Path) -> None:
    """D30 §2: the caller's token names the state its plan was built on. Once another
    writer moves the Project, the import is refused with `conflict` — and the stale
    plan is not staged, let alone dispatched."""

    with _seed_gateway() as gateway:
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            stale = _current_fingerprint(agent)
            gateway.change_project_out_of_band(PROJECT, FOREIGN_ENTRIES)
            artifact_id = _upload(http, _zip(EDITED_ENTRIES))
            result = _import(agent, artifact_id, fingerprint=stale)

    assert envelope(result)["code"] == "conflict"
    assert _import_requests(gateway) == []
    assert _entries(gateway.project(PROJECT)) == FOREIGN_ENTRIES
    rows = _transactions(tmp_path)
    assert [row["state"] for row in rows] == ["CONFLICTED"]
    assert rows[0]["error_code"] == "conflict"
    assert rows[0]["import_dispatched"] == 0
    # The refusal names the fingerprint the caller should re-read.
    assert _export_paths(gateway).count(f"/data/api/v1/projects/export/{PROJECT}") == 2


def test_a_malformed_fingerprint_is_an_input_error(tmp_path: Path) -> None:
    """A token that cannot be a `pcf1` fingerprint is refused as input, so no
    transaction is created to discover it."""

    with _seed_gateway() as gateway:
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            artifact_id = _upload(http, _zip(EDITED_ENTRIES))
            result = _import(agent, artifact_id, fingerprint="pcf1:NOT-A-FINGERPRINT")

    assert envelope(result)["code"] == "invalid_argument"
    assert _export_paths(gateway) == []
    assert _import_requests(gateway) == []
    assert _transactions(tmp_path) == []


def test_an_external_change_between_the_baseline_and_the_reimport_conflicts(tmp_path: Path) -> None:
    """D16's mandatory pre-import re-export: even with a token that matched baseline A,
    a writer landing before the re-export ends the transaction CONFLICTED with nothing
    imported (the D30 §2 race window, narrowed but not closed)."""

    with _seed_gateway() as gateway:
        settings = _settings(tmp_path, gateway)
        # Exports: the caller's read, then the transaction's baseline A; the change
        # lands before the mandatory pre-import re-export A'.
        gateway.change_project_after_exports(PROJECT, 2, FOREIGN_ENTRIES)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            baseline = _current_fingerprint(agent)
            artifact_id = _upload(http, _zip(EDITED_ENTRIES))
            result = _import(agent, artifact_id, fingerprint=baseline)

    assert envelope(result)["code"] == "conflict"
    assert _import_requests(gateway) == []
    assert _entries(gateway.project(PROJECT)) == FOREIGN_ENTRIES
    rows = _transactions(tmp_path)
    assert [row["state"] for row in rows] == ["CONFLICTED"]
    assert rows[0]["import_dispatched"] == 0
    # The backup snapshot the transaction had already taken is released.
    assert all(row[2] == 0 for row in _artifacts(tmp_path))


# ------------------------------------------- D30 §2 rejection vs D16 reconcile


def test_a_gateway_rejection_is_final_even_when_the_project_shows_the_candidate(
    tmp_path: Path,
) -> None:
    """D30 §2: a rejected dispatch is the result. Another writer producing the very
    state the caller asked for must never be credited to this call."""

    with _seed_gateway() as gateway:
        settings = _settings(tmp_path, gateway)
        gateway.fail_imports_with(409)
        gateway.race_import_with(EDITED_ENTRIES)  # a competing writer lands the candidate
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            baseline = _current_fingerprint(agent)
            artifact_id = _upload(http, _zip(EDITED_ENTRIES))
            result = _import(agent, artifact_id, fingerprint=baseline)

    assert envelope(result)["code"] == "conflict"
    assert _entries(gateway.project(PROJECT)) == EDITED_ENTRIES
    rows = _transactions(tmp_path)
    assert [row["state"] for row in rows] == ["NOT_APPLIED"]
    assert rows[0]["error_code"] == "conflict"
    assert rows[0]["import_dispatched"] == 1
    assert [row["phase"] for row in audit_rows(tmp_path) if row["outcome"] == "recovered_success"] == []


def test_an_ambiguous_dispatch_that_landed_is_reconciled_to_committed(tmp_path: Path) -> None:
    """D16: C == B after an ambiguous dispatch is a recovered success, and it is the
    only way to reach one — the import really is in the Gateway afterwards."""

    with _seed_gateway() as gateway:
        settings = _settings(tmp_path, gateway)
        gateway.fail_imports_with(500)
        gateway.race_import_with(EDITED_ENTRIES)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            baseline = _current_fingerprint(agent)
            artifact_id = _upload(http, _zip(EDITED_ENTRIES))
            result = _import(agent, artifact_id, fingerprint=baseline)

    body = structured(result)
    assert body["state"] == "COMMITTED"
    assert body["resultFingerprint"] == body["candidateFingerprint"]
    assert body["importDispatched"] is True
    assert _entries(gateway.project(PROJECT)) == EDITED_ENTRIES
    outcomes = [row["outcome"] for row in audit_rows(tmp_path)]
    assert "recovered_success" in outcomes


def test_an_ambiguous_dispatch_that_did_not_land_is_not_applied(tmp_path: Path) -> None:
    """C == A: the import did not land. It is reported as such — and never retried."""

    with _seed_gateway() as gateway:
        settings = _settings(tmp_path, gateway)
        gateway.fail_imports_with(500)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            baseline = _current_fingerprint(agent)
            artifact_id = _upload(http, _zip(EDITED_ENTRIES))
            result = _import(agent, artifact_id, fingerprint=baseline)

    assert envelope(result)["code"] == "conflict"
    assert _entries(gateway.project(PROJECT)) == BASE_ENTRIES
    rows = _transactions(tmp_path)
    assert [row["state"] for row in rows] == ["NOT_APPLIED"]
    assert rows[0]["import_dispatched"] == 1
    assert len(_import_requests(gateway)) == 1  # exactly once, never replayed


def test_an_ambiguous_dispatch_with_a_foreign_state_is_outcome_unknown(tmp_path: Path) -> None:
    """C matches neither the candidate nor the baseline: the outcome cannot be
    established, so the call is `outcome_unknown` and the recovery snapshot stays
    locked for diagnosis."""

    with _seed_gateway() as gateway:
        settings = _settings(tmp_path, gateway)
        gateway.fail_imports_with(500)
        gateway.race_import_with(FOREIGN_ENTRIES)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            baseline = _current_fingerprint(agent)
            artifact_id = _upload(http, _zip(EDITED_ENTRIES))
            result = _import(agent, artifact_id, fingerprint=baseline)

    assert envelope(result)["code"] == "outcome_unknown"
    rows = _transactions(tmp_path)
    assert [row["state"] for row in rows] == ["OUTCOME_UNKNOWN"]
    assert rows[0]["import_dispatched"] == 1
    locked = [row for row in _artifacts(tmp_path) if row[1] == "RECOVERY"]
    assert locked and all(row[2] == 1 for row in locked)


# ------------------------------------------------------------- input and policy


def test_a_non_allowlisted_project_is_denied_before_the_project_is_read(tmp_path: Path) -> None:
    """D30 §7/D30 §3: a Target outside the allowlist answers `permission_denied`, and
    the refusal happens before the transaction — so the Project is never exported and
    no transaction row exists."""

    with _seed_gateway() as gateway:
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            artifact_id = _upload(http, _zip(EDITED_ENTRIES))
            result = _import(
                agent, artifact_id, project=CONTROL_PROJECT, fingerprint="pcf1:" + "0" * 64,
            )

    assert envelope(result)["code"] == "permission_denied"
    # The denial happens before the transaction, so the Project is never exported.
    assert _export_paths(gateway) == []
    assert _import_requests(gateway) == []
    assert _transactions(tmp_path) == []
    decisions = [
        row["outcome"] for row in audit_rows(tmp_path)
        if row["tool"] == IMPORT_TOOL and row["phase"] == "decision"
    ]
    assert decisions == ["denied:target-allowlist:target-not-allowlisted"]


def test_a_project_that_does_not_exist_is_not_found(tmp_path: Path) -> None:
    """D30 §6: anything but an existing Project fails with `not_found`."""

    with _seed_gateway() as gateway:
        settings = _settings(tmp_path, gateway, mutation_targets={IMPORT_TOOL: ("*",)})
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            baseline = _current_fingerprint(agent)
            artifact_id = _upload(http, _zip(EDITED_ENTRIES))
            result = _import(agent, artifact_id, project="mcp-p4-absent", fingerprint=baseline)

    assert envelope(result)["code"] == "not_found"
    assert _import_requests(gateway) == []


def test_the_project_writer_gate_refuses_the_tool(tmp_path: Path) -> None:
    """The D16 writer is deployment state of its own: without it the Tool refuses
    rather than importing with weaker guarantees."""

    with _seed_gateway() as gateway:
        settings = _settings(tmp_path, gateway, project_writer_enabled=False, gateway_id="")
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            artifact_id = _upload(http, _zip(EDITED_ENTRIES))
            result = _import(agent, artifact_id, fingerprint="pcf1:" + "0" * 64)

    assert envelope(result)["code"] == "operation_disabled"
    assert _import_requests(gateway) == []


def test_an_artifact_of_another_principal_is_not_found(tmp_path: Path) -> None:
    """D30 §6: the archive must be visible to the same Mutation principal. An artifact
    the caller cannot see answers exactly as a missing one does."""

    with _seed_gateway() as gateway:
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            baseline = _current_fingerprint(agent)
            artifact_id = _upload(http, _zip(EDITED_ENTRIES), credential=READER_CREDENTIAL)
            result = _import(agent, artifact_id, fingerprint=baseline)

    assert envelope(result)["code"] == "not_found"
    assert _import_requests(gateway) == []
    assert _entries(gateway.project(PROJECT)) == BASE_ENTRIES


def test_an_unknown_artifact_id_is_not_found(tmp_path: Path) -> None:
    with _seed_gateway() as gateway:
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            baseline = _current_fingerprint(agent)
            result = _import(agent, "0" * 32, fingerprint=baseline)

    assert envelope(result)["code"] == "not_found"
    assert _import_requests(gateway) == []


def test_an_artifact_that_is_not_a_project_archive_is_refused(tmp_path: Path) -> None:
    """A tag-config export is visible to the caller but is not a Project archive."""

    with _seed_gateway() as gateway:
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            baseline = _current_fingerprint(agent)
            exported = structured(agent.call("tag_config_export", {
                "provider": "default", "path": "", "recursive": True, "includeUdts": False,
            }))
            artifact_id = str(exported["artifact"]["artifactId"])
            result = _import(agent, artifact_id, fingerprint=baseline)

    assert envelope(result)["code"] == "invalid_argument"
    assert _import_requests(gateway) == []


def test_an_import_artifact_produced_by_project_export_is_accepted(tmp_path: Path) -> None:
    """D17 names server-produced exports as a binary ingress source, so the export
    round trip works: export the Project, upload changed content, import it."""

    with _seed_gateway() as gateway:
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            baseline = _current_fingerprint(agent)
            edited = _upload(http, _zip(EDITED_ENTRIES))
            first = structured(_import(agent, edited, fingerprint=baseline))
            # Re-import the content the Tool just committed, this time through an
            # artifact the Tool itself produced (project_export -> upload -> import).
            current = _current_fingerprint(agent)
            exported = structured(agent.call("project_export", {"projectName": PROJECT}))
            downloaded = http.get(
                str(exported["artifact"]["download"]["path"]),
                headers={"Authorization": "Bearer " + CONFIG_CREDENTIAL},
            )
            assert downloaded.status_code == 200, downloaded.text
            reexported = _upload(http, downloaded.content)
            second = structured(_import(agent, reexported, fingerprint=current))

    assert first["state"] == "COMMITTED"
    assert second["state"] == "NO_CHANGE"


# ------------------------------------------------------------------ inventory


def test_the_tool_declares_the_config_scope(tmp_path: Path) -> None:
    server = server_module.create_server(_settings(tmp_path, None))

    async def scenario() -> Any:
        return await server.get_tool(IMPORT_TOOL)

    tool = asyncio.run(scenario())
    assert tool is not None
    assert scope_tag(CONFIG) in tool.tags


def test_the_tool_is_hidden_when_the_gateway_documents_no_import_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D04: without the import route the capability is absent and the Tool is not
    discoverable, whatever the deployment's gate says."""

    with _seed_gateway() as gateway:
        settings = _settings(tmp_path, gateway)
        document = json.loads(
            (ROOT / "tests/fixtures/recorded/gateway-8.3/phase2/openapi-required.json").read_text("utf-8")
        )
        paths = {
            path: operations for path, operations in document["paths"].items()
            if not path.startswith("/data/api/v1/projects/")
        }
        paths["/data/api/v1/projects/list"] = {"get": {}}
        paths["/data/api/v1/projects/export/{name}"] = {"get": {}}

        async def read_only_openapi(self: Any) -> bytes:
            return json.dumps({"paths": paths}).encode("utf-8")

        monkeypatch.setattr(server_module.GatewayClient, "openapi", read_only_openapi)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            names = _Session(http, CONFIG_CREDENTIAL).tools()

    assert IMPORT_TOOL not in names
    assert {"project_list", "project_export"} <= names


@pytest.mark.parametrize("class_enabled", [True, False])
def test_the_class_gate_hides_the_tool(tmp_path: Path, class_enabled: bool) -> None:
    with _seed_gateway() as gateway:
        settings = _settings(tmp_path, gateway, config_mutation_enabled=class_enabled)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            names = _Session(http, CONFIG_CREDENTIAL).tools()

    assert (IMPORT_TOOL in names) is class_enabled


def test_the_tool_operation_and_the_frozen_g3_operation_stay_distinct() -> None:
    """D30 §7 and §2 are per operation: the Phase 4 Tool answers `permission_denied`
    for a refused Target and treats a Gateway rejection as final, while the operation
    the frozen G3 harness and evidence drive keeps its recorded behavior."""

    assert PROJECT_IMPORT_TOOL_OPERATION.op_id == PROJECT_IMPORT_OPERATION.op_id == "project_import"
    assert PROJECT_IMPORT_TOOL_OPERATION.mutation_class == PROJECT_IMPORT_OPERATION.mutation_class
    assert PROJECT_IMPORT_TOOL_OPERATION.capability == PROJECT_IMPORT_OPERATION.capability
    assert PROJECT_IMPORT_TOOL_OPERATION.destructive is PROJECT_IMPORT_OPERATION.destructive is True
    assert PROJECT_IMPORT_TOOL_OPERATION.target_denial_code == "permission_denied"
    assert PROJECT_IMPORT_TOOL_OPERATION.rejection_is_final is True
    assert PROJECT_IMPORT_OPERATION.target_denial_code == "operation_disabled"
    assert PROJECT_IMPORT_OPERATION.rejection_is_final is False
