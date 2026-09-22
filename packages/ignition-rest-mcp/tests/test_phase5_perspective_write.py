"""Phase 5 (P5-2): the four D15/D16 Perspective write Tools.

The patch half needs no Gateway: it rewrites one Perspective resource inside an
archive and must leave every other entry byte-identical. The Tool half drives the
real server against the recorded Gateway over Streamable HTTP, so each assertion is
an MCP-visible result, an observed Gateway request or a persisted row.

Two rules are pinned here because they decide safety rather than convenience: a
write never creates a silent local override of a resource an ancestor Project
defines, and a document that carries the redaction placeholder is refused before a
transaction exists at all.
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
from ignition_rest_mcp.client.gateway import GatewayClient
from ignition_rest_mcp.projects import perspective
from ignition_rest_mcp.projects.perspective import PatchKind, ResourcePatch
from ignition_rest_mcp.services.config_resources import REDACTED_PLACEHOLDER
from ignition_rest_mcp.storage.database import Database
from ignition_rest_mcp.storage.schema import STATE_DDL
from phase4_fixtures import (
    PAGE_CONFIG_UPDATE_TOOL,
    SESSION_PROPS_UPDATE_TOOL,
    VIEW_DELETE_TOOL,
    VIEW_UPSERT_TOOL,
    envelope,
    mutation_settings,
    structured,
    write_requests,
)
from phase4_fixtures import Session as _Session

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tests/harness"))

from recorded_gateway import API_TOKEN, RecordedGateway  # noqa: E402

PROJECT = "mcp-p5-writes"
PARENT = "mcp-p5-writes-parent"
GRANDPARENT = "mcp-p5-writes-grandparent"
CONTROL_PROJECT = "mcp-p5-writes-control"

VIEW_PATH = "Pages/Overview"
SIBLING_PATH = "Pages/Detail"
NEW_PATH = "Pages/New"
INHERITED_PATH = "Pages/Inherited"

#: A resource the Perspective Tools do not own: its bytes must survive every write.
UNRELATED_ENTRY = "ignition/named-query/Unrelated/resource.json"
UNRELATED_BODY = b'{"name": "Unrelated"}'

CONFIG_CREDENTIAL = "cfg-secret"
WRITE_TOOLS = (VIEW_UPSERT_TOOL, VIEW_DELETE_TOOL, PAGE_CONFIG_UPDATE_TOOL, SESSION_PROPS_UPDATE_TOOL)

VIEWS = perspective.VIEWS_DIRECTORY
PAGE_CONFIG_ENTRY = perspective.PAGE_CONFIG_ENTRY
SESSION_PROPS_ENTRY = perspective.SESSION_PROPS_ENTRY
PAGE_CONFIG_RESOURCE_ENTRY = perspective.PAGE_CONFIG_RESOURCE_ENTRY
SESSION_PROPS_RESOURCE_ENTRY = perspective.SESSION_PROPS_RESOURCE_ENTRY


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


def _compact(document: dict[str, Any]) -> bytes:
    """The serialization :func:`perspective.apply_patch` writes, so a seeded archive
    can be made byte-equal to what an upsert of the same document produces."""

    return json.dumps(document, separators=(",", ":"), ensure_ascii=False).encode("utf-8")



def _metadata(document_name: str) -> bytes:
    """The `resource.json` bytes the Gateway itself writes for a new resource.

    The G5 run recorded them on 8.3.8 and 8.3.9: `json.dumps(..., indent=2)` in the
    Gateway's key order, no trailing newline. Built here from an explicit document
    literal rather than from the adapter, so a change to the shape or the
    serialization the server writes fails these tests.
    """

    return json.dumps({
        "scope": "G",
        "version": 1,
        "restricted": False,
        "overridable": True,
        "files": [document_name],
        "attributes": {},
    }, indent=2).encode("utf-8")

def _view_document(label: str = "overview") -> dict[str, Any]:
    return {"root": {"type": "ia.container.coord", "children": []}, "custom": {"label": label}}


def _view_entries(path: str, document: dict[str, Any]) -> dict[str, bytes]:
    return {
        f"{VIEWS}/{path}/view.json": _compact(document),
        f"{VIEWS}/{path}/resource.json": b'{"scope": "G"}',
    }


def _project_archive(
    *,
    views: dict[str, dict[str, Any]] | None = None,
    page_config: dict[str, Any] | None = None,
    session_props: dict[str, Any] | None = None,
    extra: dict[str, bytes] | None = None,
) -> bytes:
    entries: dict[str, bytes] = {"project.json": b'{"title":"p5 writes"}', UNRELATED_ENTRY: UNRELATED_BODY}
    for path, document in (views or {}).items():
        entries.update(_view_entries(path, document))
    if page_config is not None:
        entries[PAGE_CONFIG_ENTRY] = _compact(page_config)
    if session_props is not None:
        entries[SESSION_PROPS_ENTRY] = _compact(session_props)
    entries.update(extra or {})
    return _zip(entries)


def _settings(tmp_path: Path, gateway: RecordedGateway, **overrides: Any) -> Any:
    values: dict[str, Any] = {
        "data_dir": str(tmp_path),
        "gateway_url": gateway.base_url,
        "gateway_api_token": API_TOKEN,
        "project_writer_enabled": True,
        "gateway_id": "gw-p5-writes",
        "sensitive_exports_enabled": True,
    }
    values.update(overrides)
    return mutation_settings(
        *WRITE_TOOLS, targets={tool: (PROJECT,) for tool in WRITE_TOOLS}, **values,
    )


def _seed_gateway() -> RecordedGateway:
    return RecordedGateway(projects={
        PROJECT: _project_archive(views={VIEW_PATH: _view_document(), SIBLING_PATH: _view_document("sibling")}),
        PARENT: _project_archive(views={INHERITED_PATH: _view_document("inherited")}),
        CONTROL_PROJECT: _project_archive(views={VIEW_PATH: _view_document("control")}),
    })


def _serves_parents(
    monkeypatch: pytest.MonkeyPatch, *, parents: dict[str, str], names: list[str],
) -> None:
    """Answer the Project listing with the inheritance ``parent`` the Gateway reports.

    The recorded Gateway models no inheritance, so the one field the D15 ancestor walk
    reads comes from here; every other ``get_json`` call goes to the recorded routes. A
    name in ``parents`` that is not in ``names`` is a parent the listing does not hold.
    """

    original = GatewayClient.get_json

    async def get_json(
        self: GatewayClient, path: str, *, params: Any = None, context: Any = None,
    ) -> dict[str, Any]:
        if path != "/data/api/v1/projects/list":
            return await original(self, path, params=params, context=context)
        items = [{"name": name, "parent": parents.get(name, "")} for name in names]
        return {
            "items": items,
            "metadata": {
                "total": len(items), "matching": len(items),
                "limit": int(params["limit"]), "offset": int(params["offset"]),
            },
        }

    monkeypatch.setattr(GatewayClient, "get_json", get_json)


def _fingerprint(session: _Session, project: str = PROJECT) -> str:
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
    columns = ("state", "import_dispatched", "error_code")

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


def _unchanged(before: dict[str, bytes], after: dict[str, bytes], *touched: str) -> None:
    """Every entry outside ``touched`` survived the write byte-identically."""

    assert set(after) - set(touched) == set(before) - set(touched)
    for name in set(before) - set(touched):
        assert after[name] == before[name], name


# ------------------------------------------------------------------ the patch half


def _patched(payload: bytes, patch: ResourcePatch) -> bytes:
    out = io.BytesIO()
    perspective.apply_patch(io.BytesIO(payload), patch, out)
    return out.getvalue()


def test_a_view_replace_rewrites_the_document_and_leaves_every_other_entry_alone() -> None:
    baseline = _project_archive(
        views={VIEW_PATH: _view_document("before"), SIBLING_PATH: _view_document("sibling")},
        page_config={"pages": ["*"]},
        session_props={"props": {"theme": "dark"}},
        extra={f"{VIEWS}/Pages/Detail/folder.json": b'{"title": "Detail"}', f"{VIEWS}/Pages/": b""},
    )
    replacement = _view_document("after")

    patched = _patched(
        baseline, ResourcePatch(kind=PatchKind.VIEW_REPLACE, logical_path=VIEW_PATH, document=replacement),
    )
    entries = _entries(patched)

    assert entries[f"{VIEWS}/{VIEW_PATH}/view.json"] == _compact(replacement)
    _unchanged(_entries(baseline), entries, f"{VIEWS}/{VIEW_PATH}/view.json")


def test_a_view_replace_creates_the_document_when_the_project_has_no_such_view() -> None:
    baseline = _project_archive(views={SIBLING_PATH: _view_document("sibling")})
    replacement = _view_document("new")

    patched = _patched(
        baseline, ResourcePatch(kind=PatchKind.VIEW_REPLACE, logical_path=NEW_PATH, document=replacement),
    )
    entries = _entries(patched)

    assert entries[f"{VIEWS}/{NEW_PATH}/view.json"] == _compact(replacement)
    # The create also writes the metadata the import needs to keep the View.
    assert entries[f"{VIEWS}/{NEW_PATH}/resource.json"] == _metadata("view.json")
    _unchanged(
        _entries(baseline), entries,
        f"{VIEWS}/{NEW_PATH}/view.json", f"{VIEWS}/{NEW_PATH}/resource.json",
    )


def test_a_view_delete_removes_the_view_and_keeps_a_folder_that_still_holds_views() -> None:
    folder_marker = f"{VIEWS}/Pages/Detail/folder.json"
    baseline = _project_archive(
        views={VIEW_PATH: _view_document("gone"), SIBLING_PATH: _view_document("sibling")},
        extra={folder_marker: b'{"title": "Detail"}'},
    )

    patched = _patched(baseline, ResourcePatch(kind=PatchKind.VIEW_DELETE, logical_path=VIEW_PATH))
    entries = _entries(patched)
    removed = set(_entries(baseline)) - set(entries)

    assert removed == set(_view_entries(VIEW_PATH, _view_document()))
    # The sibling View and the folder marker survive, byte for byte.
    before = _entries(baseline)
    assert sorted(entries) == sorted(set(before) - removed)
    for name, body in entries.items():
        assert body == before[name], name


def test_a_view_delete_drops_the_folder_marker_when_the_view_was_all_it_held() -> None:
    folder_marker = f"{VIEWS}/Pages/Detail/folder.json"
    baseline = _project_archive(
        views={SIBLING_PATH: _view_document("gone")},
        extra={folder_marker: b'{"title": "Detail"}', f"{VIEWS}/Pages/Detail/": b""},
    )

    patched = _patched(baseline, ResourcePatch(kind=PatchKind.VIEW_DELETE, logical_path=SIBLING_PATH))
    entries = _entries(patched)

    assert sorted(entries) == ["ignition/named-query/Unrelated/resource.json", "project.json"]


def test_the_target_entry_is_what_decides_whether_a_project_defines_the_resource(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "baseline.zip"
    archive.write_bytes(
        _project_archive(
            views={VIEW_PATH: _view_document()},
            page_config={"pages": ["*"]},
            extra={f"{VIEWS}/Pages/New/": b""},
        ),
    )

    assert perspective.defines_target(str(archive), ResourcePatch(PatchKind.VIEW_REPLACE, VIEW_PATH))
    assert not perspective.defines_target(str(archive), ResourcePatch(PatchKind.VIEW_REPLACE, NEW_PATH))
    assert perspective.defines_target(str(archive), ResourcePatch(PatchKind.PAGE_CONFIG_REPLACE))
    assert not perspective.defines_target(str(archive), ResourcePatch(PatchKind.SESSION_PROPS_REPLACE))


@pytest.mark.parametrize(("patch", "document_entry", "resource_entry", "document_name"), [
    (
        ResourcePatch(kind=PatchKind.VIEW_REPLACE, logical_path=NEW_PATH, document=_view_document("new")),
        f"{VIEWS}/{NEW_PATH}/view.json", f"{VIEWS}/{NEW_PATH}/resource.json", "view.json",
    ),
    (
        ResourcePatch(kind=PatchKind.PAGE_CONFIG_REPLACE, document={"pages": ["*"]}),
        PAGE_CONFIG_ENTRY, PAGE_CONFIG_RESOURCE_ENTRY, "config.json",
    ),
    (
        ResourcePatch(kind=PatchKind.SESSION_PROPS_REPLACE, document={"props": {"theme": "dark"}}),
        SESSION_PROPS_ENTRY, SESSION_PROPS_RESOURCE_ENTRY, "props.json",
    ),
])
def test_a_created_resource_gains_the_designer_metadata_ignition_requires(
    patch: ResourcePatch, document_entry: str, resource_entry: str, document_name: str,
) -> None:
    """A Project import keeps a resource directory only when a sibling `resource.json`
    declares it, so a resource this server creates must carry one in the same patch
    (confirmed on a live 8.3.8 Gateway). Without it the import reports success and
    publishes nothing."""

    baseline = _project_archive(views={SIBLING_PATH: _view_document("sibling")})

    entries = _entries(_patched(baseline, patch))

    assert entries[resource_entry] == _metadata(document_name)
    assert entries[document_entry] == _compact(patch.document or {})


def test_the_created_metadata_bytes_are_the_form_the_gateway_rewrites_to() -> None:
    """The serialization itself is the contract.

    The Gateway rewrites a new resource's metadata into its own form on import, and the
    D16 verification compares the re-export with the candidate byte-exactly, so a
    compact or differently ordered document ends the transaction RECOVERY_REQUIRED even
    though the write landed. The G5 run recorded this exact form on 8.3.8 and 8.3.9.
    """

    payload = perspective.resource_metadata_bytes(perspective.VIEW_DOCUMENT_NAME)

    assert payload == (
        b"{\n"
        b'  "scope": "G",\n'
        b'  "version": 1,\n'
        b'  "restricted": false,\n'
        b'  "overridable": true,\n'
        b'  "files": [\n'
        b'    "view.json"\n'
        b"  ],\n"
        b'  "attributes": {}\n'
        b"}"
    )
    assert perspective.resource_metadata_bytes(perspective.PAGE_CONFIG_DOCUMENT_NAME) == _metadata(
        "config.json"
    )
    assert perspective.resource_metadata_bytes(perspective.SESSION_PROPS_DOCUMENT_NAME) == _metadata(
        "props.json"
    )


def test_an_existing_resource_keeps_its_own_metadata() -> None:
    """The metadata rule is a create rule: a resource that already has a
    `resource.json` keeps it byte-identically, whatever shape it is in."""

    baseline = _project_archive(views={VIEW_PATH: _view_document("before")})
    before = _entries(baseline)

    patched = _patched(
        baseline,
        ResourcePatch(kind=PatchKind.VIEW_REPLACE, logical_path=VIEW_PATH, document=_view_document("after")),
    )
    entries = _entries(patched)

    assert entries[f"{VIEWS}/{VIEW_PATH}/resource.json"] == before[f"{VIEWS}/{VIEW_PATH}/resource.json"]
    _unchanged(before, entries, f"{VIEWS}/{VIEW_PATH}/view.json")


# ------------------------------------------------------------------ the write Tools


def test_a_view_upsert_commits_and_touches_nothing_else(tmp_path: Path) -> None:
    with _seed_gateway() as gateway:
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            before = _entries(gateway.project(PROJECT))
            baseline = _fingerprint(agent)
            replacement = _view_document("committed")
            result = _upsert(agent, view=replacement, fingerprint=baseline)
            read_back = structured(
                agent.call("perspective_view_get", {"projectName": PROJECT, "path": VIEW_PATH})
            )
            after = _entries(gateway.project(PROJECT))

    body = structured(result)
    assert body["state"] == "COMMITTED"
    assert body["projectName"] == PROJECT
    assert body["path"] == VIEW_PATH
    assert body["baselineFingerprint"] == baseline
    assert body["resultFingerprint"] == body["candidateFingerprint"]
    assert body["importDispatched"] is True
    assert read_back["view"] == replacement
    assert after[f"{VIEWS}/{VIEW_PATH}/view.json"] == _compact(replacement)
    # The sibling View, the unrelated resource and the Project document are untouched.
    _unchanged(before, after, f"{VIEWS}/{VIEW_PATH}/view.json")
    assert len(_import_requests(gateway)) == 1


def test_a_view_upsert_creates_a_view_the_project_does_not_have(tmp_path: Path) -> None:
    with _seed_gateway() as gateway:
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            before = _entries(gateway.project(PROJECT))
            created = _view_document("created")
            result = _upsert(agent, path=NEW_PATH, view=created, fingerprint=_fingerprint(agent))
            after = _entries(gateway.project(PROJECT))
            listed = structured(
                agent.call("perspective_view_list", {"projectName": PROJECT})
            )

    assert structured(result)["state"] == "COMMITTED"
    assert after[f"{VIEWS}/{NEW_PATH}/view.json"] == _compact(created)
    # The metadata is what makes the import keep the new View at all.
    assert after[f"{VIEWS}/{NEW_PATH}/resource.json"] == _metadata("view.json")
    assert NEW_PATH in listed["items"]
    _unchanged(before, after, f"{VIEWS}/{NEW_PATH}/view.json", f"{VIEWS}/{NEW_PATH}/resource.json")


def test_upserting_the_same_document_is_a_no_change(tmp_path: Path) -> None:
    """D16 no-op idempotency: the candidate equals the baseline, so nothing is backed
    up and nothing is imported."""

    with _seed_gateway() as gateway:
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            baseline = _fingerprint(agent)
            result = _upsert(agent, view=_view_document(), fingerprint=baseline)

    body = structured(result)
    assert body["state"] == "NO_CHANGE"
    assert body["candidateFingerprint"] == baseline
    assert body["resultFingerprint"] is None
    assert body["importDispatched"] is False
    assert _import_requests(gateway) == []
    assert _entries(gateway.project(PROJECT))[f"{VIEWS}/{VIEW_PATH}/view.json"] == _compact(_view_document())


def test_a_stale_fingerprint_conflicts_before_anything_is_dispatched(tmp_path: Path) -> None:
    """D30 §2: the token names the state the caller's plan was built on, so a Project
    another writer moved is refused with `conflict` and nothing is imported."""

    with _seed_gateway() as gateway:
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            stale = _fingerprint(agent)
            gateway.change_project_out_of_band(
                PROJECT, _entries(_project_archive(views={VIEW_PATH: _view_document("foreign")})),
            )
            result = _upsert(agent, view=_view_document("planned"), fingerprint=stale)

    assert envelope(result)["code"] == "conflict"
    assert _import_requests(gateway) == []
    rows = _transactions(tmp_path)
    assert [row["state"] for row in rows] == ["CONFLICTED"]
    assert rows[0]["import_dispatched"] == 0
    assert _entries(gateway.project(PROJECT))[f"{VIEWS}/{VIEW_PATH}/view.json"] == _compact(
        _view_document("foreign")
    )


def test_a_non_allowlisted_project_is_denied_before_the_project_is_read(tmp_path: Path) -> None:
    """D30 §7/D30 §3: the Target allowlist is evaluated before the ancestor walk, so a
    Project the deployment did not name is neither exported nor written, and no
    transaction row exists."""

    with _seed_gateway() as gateway:
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            result = _upsert(
                agent, project=CONTROL_PROJECT, view=_view_document("denied"),
                fingerprint="pcf1:" + "0" * 64,
            )

    assert envelope(result)["code"] == "permission_denied"
    assert _export_paths(gateway) == []
    assert _import_requests(gateway) == []
    assert _transactions(tmp_path) == []


def test_delete_removes_one_view_and_keeps_its_siblings(tmp_path: Path) -> None:
    with _seed_gateway() as gateway:
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            before = _entries(gateway.project(PROJECT))
            result = agent.call(VIEW_DELETE_TOOL, {
                "projectName": PROJECT, "path": VIEW_PATH,
                "expectedFingerprint": _fingerprint(agent),
            })
            after = _entries(gateway.project(PROJECT))
            read_back = agent.call(
                "perspective_view_get", {"projectName": PROJECT, "path": VIEW_PATH},
            )
            sibling = structured(
                agent.call("perspective_view_get", {"projectName": PROJECT, "path": SIBLING_PATH})
            )

    body = structured(result)
    assert body["state"] == "COMMITTED"
    assert body["path"] == VIEW_PATH
    # Exactly the View's own entries are gone, and every other entry is byte-identical.
    assert set(before) - set(after) == set(_view_entries(VIEW_PATH, _view_document()))
    for name in after:
        assert after[name] == before[name], name
    assert envelope(read_back)["code"] == "not_found"
    assert sibling["view"] == _view_document("sibling")


def test_delete_of_a_missing_view_is_not_found(tmp_path: Path) -> None:
    with _seed_gateway() as gateway:
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            result = agent.call(VIEW_DELETE_TOOL, {
                "projectName": PROJECT, "path": "Pages/Absent",
                "expectedFingerprint": _fingerprint(agent),
            })

    assert envelope(result)["code"] == "not_found"
    assert _import_requests(gateway) == []
    assert _transactions(tmp_path) == []


def test_page_config_update_commits_and_creates_a_document_the_project_lacks(tmp_path: Path) -> None:
    with _seed_gateway() as gateway:
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            before = _entries(gateway.project(PROJECT))
            config = {"pages": ["*"], "theme": "dark"}
            result = agent.call(PAGE_CONFIG_UPDATE_TOOL, {
                "projectName": PROJECT, "config": config,
                "expectedFingerprint": _fingerprint(agent),
            })
            after = _entries(gateway.project(PROJECT))

    assert structured(result)["state"] == "COMMITTED"
    assert after[PAGE_CONFIG_ENTRY] == _compact(config)
    assert after[PAGE_CONFIG_RESOURCE_ENTRY] == _metadata("config.json")
    _unchanged(before, after, PAGE_CONFIG_ENTRY, PAGE_CONFIG_RESOURCE_ENTRY)


def test_session_props_update_commits_and_creates_a_document_the_project_lacks(tmp_path: Path) -> None:
    with _seed_gateway() as gateway:
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            before = _entries(gateway.project(PROJECT))
            props = {"props": {"theme": "light"}}
            result = agent.call(SESSION_PROPS_UPDATE_TOOL, {
                "projectName": PROJECT, "props": props,
                "expectedFingerprint": _fingerprint(agent),
            })
            after = _entries(gateway.project(PROJECT))

    assert structured(result)["state"] == "COMMITTED"
    assert after[SESSION_PROPS_ENTRY] == _compact(props)
    assert after[SESSION_PROPS_RESOURCE_ENTRY] == _metadata("props.json")
    _unchanged(before, after, SESSION_PROPS_ENTRY, SESSION_PROPS_RESOURCE_ENTRY)


def test_an_inherited_view_is_refused_before_the_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D15: the ancestor Project defines the View and the child does not, so the write
    is `invalid_argument` with reason `inherited_resource`. Nothing is imported and no
    transaction is created, because the check runs before the transaction starts."""

    with _seed_gateway() as gateway:
        _serves_parents(
            monkeypatch, parents={PROJECT: PARENT}, names=[PROJECT, PARENT, CONTROL_PROJECT],
        )
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            before = _entries(gateway.project(PROJECT))
            result = _upsert(
                agent, path=INHERITED_PATH, view=_view_document("override"),
                fingerprint=_fingerprint(agent),
            )

    refusal = envelope(result)
    assert refusal["code"] == "invalid_argument"
    assert "inherited_resource" in refusal["message"]
    # The walk really read the ancestor: the child's export alone cannot tell that the
    # View exists in the chain.
    assert f"/data/api/v1/projects/export/{PARENT}" in _export_paths(gateway)
    assert _import_requests(gateway) == []
    assert _transactions(tmp_path) == []
    assert _entries(gateway.project(PROJECT)) == before


def test_an_inherited_page_config_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The same rule for the Project-wide documents: the target is the Page
    configuration entry, not a View directory."""

    parent_archive = _project_archive(page_config={"pages": ["*"]})
    with RecordedGateway(projects={
        PROJECT: _project_archive(views={VIEW_PATH: _view_document()}),
        PARENT: parent_archive,
    }) as gateway:
        _serves_parents(monkeypatch, parents={PROJECT: PARENT}, names=[PROJECT, PARENT])
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            result = agent.call(PAGE_CONFIG_UPDATE_TOOL, {
                "projectName": PROJECT, "config": {"pages": ["Pages/Overview"]},
                "expectedFingerprint": _fingerprint(agent),
            })

    refusal = envelope(result)
    assert refusal["code"] == "invalid_argument"
    assert "inherited_resource" in refusal["message"]
    assert _import_requests(gateway) == []
    assert _transactions(tmp_path) == []


def test_a_local_view_wins_over_an_ancestor_that_also_defines_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The rule refuses an override that does not exist yet. A View the Project does
    define locally is its own, whatever the ancestors also define."""

    parent_archive = _project_archive(views={VIEW_PATH: _view_document("inherited")})
    with RecordedGateway(projects={
        PROJECT: _project_archive(views={VIEW_PATH: _view_document("local")}),
        PARENT: parent_archive,
    }) as gateway:
        _serves_parents(monkeypatch, parents={PROJECT: PARENT}, names=[PROJECT, PARENT])
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            before = _entries(gateway.project(PROJECT))
            replacement = _view_document("local replacement")
            result = _upsert(agent, view=replacement, fingerprint=_fingerprint(agent))
            after = _entries(gateway.project(PROJECT))

    assert structured(result)["state"] == "COMMITTED"
    assert after[f"{VIEWS}/{VIEW_PATH}/view.json"] == _compact(replacement)
    _unchanged(before, after, f"{VIEWS}/{VIEW_PATH}/view.json")


def test_delete_of_an_inherited_view_is_refused_rather_than_reported_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ancestor walk runs before the not_found check, so a caller that names a View
    inherited from above gets the real reason: the Project does not own it."""

    with _seed_gateway() as gateway:
        _serves_parents(
            monkeypatch, parents={PROJECT: PARENT}, names=[PROJECT, PARENT, CONTROL_PROJECT],
        )
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            result = agent.call(VIEW_DELETE_TOOL, {
                "projectName": PROJECT, "path": INHERITED_PATH,
                "expectedFingerprint": _fingerprint(agent),
            })

    refusal = envelope(result)
    assert refusal["code"] == "invalid_argument"
    assert "inherited_resource" in refusal["message"]
    assert _import_requests(gateway) == []
    assert _transactions(tmp_path) == []


@pytest.mark.parametrize(("tool", "parameter", "document"), [
    (VIEW_UPSERT_TOOL, "view", {"root": {"type": "ia.container.coord"}, "custom": {"token": "<redacted>"}}),
    (PAGE_CONFIG_UPDATE_TOOL, "config", {"pages": ["*"], "secret": "<redacted>"}),
    (SESSION_PROPS_UPDATE_TOOL, "props", {"props": {"nested": ["<redacted>"]}}),
])
def test_a_document_carrying_the_redaction_placeholder_is_refused(
    tmp_path: Path, tool: str, parameter: str, document: dict[str, Any],
) -> None:
    """A read returns `<redacted>` in place of a secret-named field, so a document
    built from that read must never be written back as if the placeholder were the
    value. The refusal happens before any export or transaction."""

    with _seed_gateway() as gateway:
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            fingerprint = _fingerprint(agent)
            exports_before = len(_export_paths(gateway))
            call: dict[str, Any] = {
                "projectName": PROJECT, parameter: document,
                "expectedFingerprint": fingerprint,
            }
            if parameter == "view":
                call["path"] = VIEW_PATH
            result = agent.call(tool, call)

    refusal = envelope(result)
    assert refusal["code"] == "invalid_argument"
    assert "redacted_value" in refusal["message"]
    # The refusal happens before the inheritance walk, so the Project is not exported again.
    assert len(_export_paths(gateway)) == exports_before
    assert _import_requests(gateway) == []
    assert _transactions(tmp_path) == []


def test_the_placeholder_a_read_emits_is_the_value_a_write_refuses(tmp_path: Path) -> None:
    """The read path's redaction and the write path's refusal are one value.

    `perspective_view_get` returns a secret-named field as `<redacted>`, and the
    write Tools refuse a document carrying that value, so an agent cannot round-trip
    a read into a write and store the placeholder as the secret.
    """

    document = {"root": {"type": "ia.container.coord"}, "custom": {"apiKey": "s3cret"}}
    with RecordedGateway(projects={PROJECT: _project_archive(views={VIEW_PATH: document})}) as gateway:
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            read = structured(
                agent.call("perspective_view_get", {"projectName": PROJECT, "path": VIEW_PATH})
            )
            result = _upsert(
                agent, view=read["view"], fingerprint=str(read["fingerprint"]),
            )

    assert read["view"]["custom"]["apiKey"] == REDACTED_PLACEHOLDER
    refusal = envelope(result)
    assert refusal["code"] == "invalid_argument"
    assert "redacted_value" in refusal["message"]
    assert _import_requests(gateway) == []
    assert _transactions(tmp_path) == []


def test_a_parent_the_listing_names_but_does_not_hold_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The chain may not end on a name the listing cannot account for.

    Child inherits from Parent, the listing names Parent but holds no entry for it, and
    Grandparent defines the View. Parent exists and is exportable, so reading the missing
    listing entry as "no parent" would end the walk at Parent, never reach Grandparent,
    and let this write create the silent override D15 forbids. The write fails closed
    instead, and nothing above the unknown name is exported at all.
    """

    with RecordedGateway(projects={
        PROJECT: _project_archive(views={VIEW_PATH: _view_document()}),
        PARENT: _project_archive(views={VIEW_PATH: _view_document("parent local")}),
        GRANDPARENT: _project_archive(views={INHERITED_PATH: _view_document("inherited from above")}),
    }) as gateway:
        _serves_parents(monkeypatch, parents={PROJECT: PARENT}, names=[PROJECT, GRANDPARENT])
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            before = _entries(gateway.project(PROJECT))
            result = _upsert(
                agent, path=INHERITED_PATH, view=_view_document("override"),
                fingerprint=_fingerprint(agent),
            )

    refusal = envelope(result)
    assert refusal["code"] == "upstream_error"
    assert PARENT in refusal["message"]
    # Only the Project itself was exported (by the caller's read and by the local check);
    # no ancestor was read, because the chain is computed before the first ancestor export.
    assert set(_export_paths(gateway)) == {f"/data/api/v1/projects/export/{PROJECT}"}
    assert _import_requests(gateway) == []
    assert _transactions(tmp_path) == []
    # Nothing was written, so the View Grandparent defines is untouched and still inherited.
    assert _entries(gateway.project(PROJECT)) == before


@pytest.mark.parametrize(("tool", "arguments", "missing"), [
    (VIEW_DELETE_TOOL, {"path": "Pages/Absent"}, "not_found"),
    (VIEW_UPSERT_TOOL, {"path": INHERITED_PATH}, "inherited_resource"),
])
def test_a_stale_fingerprint_is_a_conflict_before_either_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tool: str, arguments: dict[str, Any],
    missing: str,
) -> None:
    """D30 §2 wins over the checks that follow it.

    The stale token is compared with the export the local check already took, so a
    caller that planned from a moved Project gets `conflict` rather than a `not_found`
    or an `inherited_resource` about a state that is no longer current.
    """

    with _seed_gateway() as gateway:
        _serves_parents(
            monkeypatch, parents={PROJECT: PARENT}, names=[PROJECT, PARENT, CONTROL_PROJECT],
        )
        settings = _settings(tmp_path, gateway)
        with TestClient(server_module.create_server(settings).http_app()) as http:
            agent = _Session(http, CONFIG_CREDENTIAL)
            stale = _fingerprint(agent)
            gateway.change_project_out_of_band(
                PROJECT, _entries(_project_archive(views={VIEW_PATH: _view_document("foreign")})),
            )
            call: dict[str, Any] = {
                "projectName": PROJECT, "expectedFingerprint": stale, **arguments,
            }
            if tool == VIEW_UPSERT_TOOL:
                call["view"] = _view_document("planned")
            result = agent.call(tool, call)

    refusal = envelope(result)
    assert refusal["code"] == "conflict"
    assert missing not in refusal["message"]
    assert _import_requests(gateway) == []
    assert _transactions(tmp_path) == []


def _upsert(
    session: _Session, *, view: dict[str, Any], fingerprint: str,
    path: str = VIEW_PATH, project: str = PROJECT,
) -> dict[str, Any]:
    return session.call(VIEW_UPSERT_TOOL, {
        "projectName": project, "path": path, "view": view,
        "expectedFingerprint": fingerprint,
    })
