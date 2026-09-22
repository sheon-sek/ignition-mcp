"""Phase 5 (P5-1): the D15 Perspective adapter and the five Perspective reads.

The adapter half needs no Gateway: it maps Logical resource paths to archive
entries, lists Views, reads the View, Page configuration and Session properties
documents and validates a View document offline. The read half runs each Tool
against a mocked Project export endpoint, so it proves the whole chain: gate,
staged export, document read, fingerprint, and no published artifact.
"""

from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
import re
from typing import Any
import zipfile

import httpx
import pytest
from fastmcp import Client

import ignition_rest_mcp.server as server_module
from ignition_rest_mcp.artifacts.local import LocalArtifactStore, quotas_from_settings
from ignition_rest_mcp.capabilities.registry import CapabilityRegistry
from ignition_rest_mcp.client.gateway import GatewayClient
from ignition_rest_mcp.errors import GatewayError
from ignition_rest_mcp.models import PageMetadata
from ignition_rest_mcp.operation import OperationContext
from ignition_rest_mcp.projects import perspective
from ignition_rest_mcp.services.perspective import (
    perspective_page_config_get,
    perspective_session_props_get,
    perspective_view_get,
    perspective_view_list,
    perspective_view_validate,
)
from ignition_rest_mcp.storage.database import Storage
from test_config import _settings

PCF1 = re.compile(r"^pcf1:[0-9a-f]{64}$")

VIEWS = "com.inductiveautomation.perspective/views"
FOLDER_ENTRY = f"{VIEWS}/Pages/Detail/folder.json"

#: A View document that names a component type only one Ignition patch knows.
UNKNOWN_COMPONENT_VIEW: dict[str, Any] = {
    "root": {"type": "acme.custom.Marquee", "props": {"text": "hi"}},
    "custom": {},
}


def _export_zip(
    *,
    views: dict[str, Any] | None = None,
    page_config: Any = None,
    session_props: Any = None,
    extra: dict[str, bytes] | None = None,
) -> bytes:
    """One Project export laid out as Ignition 8.3 exports the Perspective module."""

    entries: dict[str, bytes] = {"project.json": b'{"title": "Demo"}'}
    if extra:
        entries.update(extra)
    for path, document in (views or {}).items():
        entries[f"{VIEWS}/{path}/view.json"] = json.dumps(document).encode("utf-8")
        entries[f"{VIEWS}/{path}/resource.json"] = json.dumps({"scope": "G"}).encode("utf-8")
    if page_config is not None:
        entries["com.inductiveautomation.perspective/page-config/config.json"] = json.dumps(
            page_config,
        ).encode("utf-8")
    if session_props is not None:
        entries["com.inductiveautomation.perspective/session-props/props.json"] = json.dumps(
            session_props,
        ).encode("utf-8")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in entries.items():
            archive.writestr(zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0)), body)
    return buffer.getvalue()


def _openapi_paths(*, with_project: bool) -> bytes:
    paths: dict[str, Any] = {"/data/api/v1/gateway-info": {"get": {}}}
    if with_project:
        paths["/data/api/v1/projects/export/{name}"] = {"get": {}}
    return json.dumps({"paths": paths}).encode()


class _Env:
    """A Gateway that serves one export body, plus a real staged artifact store."""

    def __init__(self, tmp_path: Path, *, body: bytes, with_project: bool = True) -> None:
        self.settings = _settings(
            data_dir=str(tmp_path), artifact_max_bytes=8_000_000, artifact_total_bytes=32_000_000,
        )
        self.body = body
        self.requests: list[str] = []
        self.client = GatewayClient(
            base_url="http://gw", api_token="t", timeout_seconds=10,
            transport=httpx.MockTransport(self._handler),
        )
        self._openapi = _openapi_paths(with_project=with_project)
        self.registry = CapabilityRegistry(self.client)
        self.storage = Storage(tmp_path)

    async def _handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request.url.path)
        if request.url.path.startswith("/data/api/v1/projects/export/"):
            return httpx.Response(200, content=self.body, request=request)
        return httpx.Response(404, request=request)

    async def start(self) -> None:
        await self.storage.open()
        self.store = LocalArtifactStore(
            self.storage.state, Path(self.settings.data_dir), quotas_from_settings(self.settings),
        )
        self.store.prepare()

        async def openapi() -> bytes:
            return self._openapi

        async def gateway_info(context: Any = None) -> dict[str, str]:
            return {"ignitionVersion": "8.3.8"}

        async def modules(context: Any = None) -> dict[str, list[Any]]:
            return {"items": []}

        self.client.openapi = openapi  # type: ignore[method-assign]
        self.client.gateway_info = gateway_info  # type: ignore[method-assign]
        self.client.healthy_modules = modules  # type: ignore[method-assign]
        await self.registry.refresh()

    async def stop(self) -> None:
        await self.client.aclose()
        await self.storage.close()

    def context(self, tool: str) -> OperationContext:
        return OperationContext.start(tool, "none:test", "ARTIFACT")

    async def artifact_count(self) -> int:
        def _read(conn: Any) -> int:
            return int(conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0])

        return await self.storage.state.run(_read)

    async def staging_leftovers(self) -> list[str]:
        staging = Path(self.settings.data_dir) / "artifacts" / "staging"
        return [entry.name for entry in staging.iterdir()] if staging.exists() else []


def _run(coro: Any) -> Any:
    async def main() -> Any:
        return await coro

    return asyncio.run(main())


# --------------------------------------------------------------------------- the mapping


def test_a_logical_path_maps_to_its_view_entries_and_back() -> None:
    assert perspective.view_directory("Pages/Overview") == f"{VIEWS}/Pages/Overview"
    assert perspective.view_document_entry("Pages/Overview") == f"{VIEWS}/Pages/Overview/view.json"
    assert perspective.view_resource_entry("Pages/Overview") == f"{VIEWS}/Pages/Overview/resource.json"
    assert perspective.logical_path_from_view_document(f"{VIEWS}/Pages/Overview/view.json") == "Pages/Overview"
    assert perspective.logical_path_from_view_document(f"{VIEWS}/Pages/Overview/resource.json") is None
    assert perspective.logical_path_from_view_document("project.json") is None


@pytest.mark.parametrize("path", [
    "", "/Pages/Overview", "Pages/Overview/", "Pages//Overview", "Pages/./Overview",
    "Pages/../Overview", "..", "Pages\\Overview", "Pages/Over*view", "Pages/Over:view",
    "Pages/Over\nview", "C:/Pages", "Pages/\ud800",
])
def test_a_path_that_could_escape_or_name_another_resource_is_refused(path: str) -> None:
    with pytest.raises(GatewayError) as captured:
        perspective.validate_logical_resource_path(path)

    assert captured.value.code == "invalid_argument"


def test_a_path_over_the_byte_ceiling_is_refused() -> None:
    with pytest.raises(GatewayError) as captured:
        perspective.validate_logical_resource_path("a" * (perspective.MAX_LOGICAL_PATH_BYTES + 1))

    assert captured.value.code == "limit_exceeded"


def test_list_view_paths_reports_views_and_never_a_folder(tmp_path: Path) -> None:
    archive = tmp_path / "demo.zip"
    archive.write_bytes(
        _export_zip(
            views={"Pages/Overview": {"root": {"type": "x"}}, "Pages/Detail/Sub": {"root": {"type": "y"}}},
            extra={FOLDER_ENTRY: b'{"title": "Detail"}'},
        ),
    )

    # Pages/Detail holds only the folder marker and another View, so it is not a View itself.
    assert perspective.list_view_paths(str(archive)) == ["Pages/Detail/Sub", "Pages/Overview"]


def test_validate_accepts_an_unknown_component_type_and_reports_measurements() -> None:
    validation = perspective.validate_view_document(UNKNOWN_COMPONENT_VIEW)

    assert validation.document == UNKNOWN_COMPONENT_VIEW
    # The byte ceiling is measured on the compact re-serialization, not on a dump
    # that spaces its separators out.
    assert validation.bytes == len(
        json.dumps(UNKNOWN_COMPONENT_VIEW, separators=(",", ":"), ensure_ascii=False).encode("utf-8"),
    )
    assert validation.bytes < len(json.dumps(UNKNOWN_COMPONENT_VIEW).encode("utf-8"))
    assert validation.depth == 4


def test_validate_refuses_an_oversize_document() -> None:
    oversize = {"root": {"type": "x"}, "pad": "a" * perspective.DEFAULT_VIEW_BUDGET.max_bytes}

    with pytest.raises(GatewayError) as captured:
        perspective.validate_view_document(oversize)

    assert captured.value.code == "limit_exceeded"


def test_validate_refuses_a_document_nested_past_the_depth_ceiling() -> None:
    nested: Any = {"type": "x"}
    for _ in range(perspective.DEFAULT_VIEW_BUDGET.max_depth):
        nested = {"type": "x", "child": nested}

    with pytest.raises(GatewayError) as captured:
        perspective.validate_view_document({"root": nested})

    assert captured.value.code == "limit_exceeded"


@pytest.mark.parametrize("document", [
    [], ["root"], 7, None, "", '{"root": {"type": "x"}}', {"children": []}, {"root": []},
    {"root": {"type": 7}},
])
def test_validate_refuses_a_document_that_is_not_a_view(document: Any) -> None:
    with pytest.raises(GatewayError) as captured:
        perspective.validate_view_document(document)

    assert captured.value.code == "invalid_argument"


# --------------------------------------------------------------------------- the Tools


def test_view_list_paginates_the_local_view_paths(tmp_path: Path) -> None:
    env = _Env(tmp_path, body=_export_zip(views={"Pages/Overview": {}, "Pages/Details": {}}))

    async def scenario() -> None:
        await env.start()
        context = env.context("perspective_view_list")
        first = await perspective_view_list(
            env.client, env.registry, env.store, context,
            project_name="Demo", limit=1, offset=0, deadline_seconds=10,
        )
        second = await perspective_view_list(
            env.client, env.registry, env.store, context,
            project_name="Demo", limit=1, offset=1, deadline_seconds=10,
        )
        assert first.items == ["Pages/Details"]
        assert second.items == ["Pages/Overview"]
        assert first.page == PageMetadata(total=2, matching=2, limit=1, offset=0)
        assert env.requests == ["/data/api/v1/projects/export/Demo"] * 2
        # A read stages its export privately: no artifact is published, nothing is left behind.
        assert await env.artifact_count() == 0
        assert await env.staging_leftovers() == []
        await env.stop()

    _run(scenario())


def test_view_get_returns_the_document_and_the_project_fingerprint(tmp_path: Path) -> None:
    document = {"root": {"type": "ia.container.coord", "children": []}, "custom": {}}
    env = _Env(tmp_path, body=_export_zip(views={"Pages/Overview": document}))

    async def scenario() -> None:
        await env.start()
        result = await perspective_view_get(
            env.client, env.registry, env.store, env.context("perspective_view_get"),
            project_name="Demo", path="Pages/Overview", deadline_seconds=10,
        )
        assert result.projectName == "Demo"
        assert result.path == "Pages/Overview"
        assert result.view == document
        assert PCF1.match(result.fingerprint)
        assert env.requests == ["/data/api/v1/projects/export/Demo"]
        assert await env.artifact_count() == 0
        await env.stop()

    _run(scenario())


def test_view_get_reports_a_view_this_project_does_not_have_locally(tmp_path: Path) -> None:
    env = _Env(tmp_path, body=_export_zip(views={"Pages/Overview": {"root": {"type": "x"}}}))

    async def scenario() -> None:
        await env.start()
        with pytest.raises(GatewayError) as captured:
            await perspective_view_get(
                env.client, env.registry, env.store, env.context("perspective_view_get"),
                project_name="Demo", path="Pages/Missing", deadline_seconds=10,
            )
        assert captured.value.code == "not_found"
        assert await env.artifact_count() == 0
        await env.stop()

    _run(scenario())


def test_view_get_refuses_a_traversal_path_before_anything_is_exported(tmp_path: Path) -> None:
    env = _Env(tmp_path, body=_export_zip(views={"Pages/Overview": {"root": {"type": "x"}}}))

    async def scenario() -> None:
        await env.start()
        with pytest.raises(GatewayError) as captured:
            await perspective_view_get(
                env.client, env.registry, env.store, env.context("perspective_view_get"),
                project_name="Demo", path="../Pages/Overview", deadline_seconds=10,
            )
        assert captured.value.code == "invalid_argument"
        assert env.requests == []
        assert await env.artifact_count() == 0
        await env.stop()

    _run(scenario())


def test_a_broken_view_document_error_names_the_logical_path_not_the_archive_entry(
    tmp_path: Path,
) -> None:
    """D15 keeps the archive layout server-side, so no message echoes an entry."""

    env = _Env(tmp_path, body=_export_zip(extra={f"{VIEWS}/Pages/Overview/view.json": b"not-json"}))

    async def scenario() -> None:
        await env.start()
        with pytest.raises(GatewayError) as captured:
            await perspective_view_get(
                env.client, env.registry, env.store, env.context("perspective_view_get"),
                project_name="Demo", path="Pages/Overview", deadline_seconds=10,
            )
        message = str(captured.value)
        assert captured.value.code == "schema_mismatch"
        assert "Pages/Overview" in message
        assert "view.json" not in message
        assert "com.inductiveautomation" not in message
        await env.stop()

    _run(scenario())


def test_an_oversize_document_error_names_the_logical_path(tmp_path: Path) -> None:
    archive = tmp_path / "demo.zip"
    archive.write_bytes(_export_zip(views={"Pages/Overview": {"root": {"type": "x"}}}))

    with pytest.raises(GatewayError) as captured:
        perspective.read_view_document(
            str(archive), "Pages/Overview", budget=perspective.ViewBudget(max_bytes=16),
        )

    message = str(captured.value)
    assert captured.value.code == "limit_exceeded"
    assert "Pages/Overview" in message
    assert "view.json" not in message


def test_document_reads_report_secret_fields_as_redacted(tmp_path: Path) -> None:
    env = _Env(
        tmp_path,
        body=_export_zip(
            views={"Pages/Overview": {"root": {"type": "x"}, "password": "top-secret"}},
            page_config={"pages": ["*"], "apiKey": "page-secret"},
            session_props={"props": {"accessToken": "session-secret"}},
        ),
    )

    async def scenario() -> None:
        await env.start()
        view = await perspective_view_get(
            env.client, env.registry, env.store, env.context("perspective_view_get"),
            project_name="Demo", path="Pages/Overview", deadline_seconds=10,
        )
        assert view.view["password"] == "<redacted>"
        assert view.view["root"] == {"type": "x"}
        assert "top-secret" not in json.dumps(view.view)
        config = await perspective_page_config_get(
            env.client, env.registry, env.store, env.context("perspective_page_config_get"),
            project_name="Demo", deadline_seconds=10,
        )
        assert config.config["apiKey"] == "<redacted>"
        props = await perspective_session_props_get(
            env.client, env.registry, env.store, env.context("perspective_session_props_get"),
            project_name="Demo", deadline_seconds=10,
        )
        assert props.props["props"]["accessToken"] == "<redacted>"
        assert "session-secret" not in json.dumps(props.props)
        await env.stop()

    _run(scenario())


def test_a_path_that_cannot_be_utf8_encoded_is_refused_before_any_export(tmp_path: Path) -> None:
    env = _Env(tmp_path, body=_export_zip(views={"Pages/Overview": {"root": {"type": "x"}}}))

    async def scenario() -> None:
        await env.start()
        with pytest.raises(GatewayError) as captured:
            await perspective_view_get(
                env.client, env.registry, env.store, env.context("perspective_view_get"),
                project_name="Demo", path="Pages/\ud800", deadline_seconds=10,
            )
        assert captured.value.code == "invalid_argument"
        assert env.requests == []
        assert await env.artifact_count() == 0
        await env.stop()

    _run(scenario())


def test_view_validate_makes_no_gateway_call() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("perspective_view_validate must not call the Gateway")

    client = GatewayClient(
        base_url="http://gw", api_token="t", timeout_seconds=10, transport=httpx.MockTransport(handler),
    )
    context = OperationContext.start("perspective_view_validate", "none:test", "FAST")

    result = perspective_view_validate(context, view=UNKNOWN_COMPONENT_VIEW)

    assert result.valid is True
    assert result.depth == 4
    _run(client.aclose())


def test_page_config_and_session_props_read_the_documents_they_own(tmp_path: Path) -> None:
    env = _Env(
        tmp_path,
        body=_export_zip(
            views={"Pages/Overview": {"root": {"type": "x"}}},
            page_config={"pages": ["*"]},
            session_props={"props": {"theme": "dark"}},
        ),
    )

    async def scenario() -> None:
        await env.start()
        config = await perspective_page_config_get(
            env.client, env.registry, env.store, env.context("perspective_page_config_get"),
            project_name="Demo", deadline_seconds=10,
        )
        props = await perspective_session_props_get(
            env.client, env.registry, env.store, env.context("perspective_session_props_get"),
            project_name="Demo", deadline_seconds=10,
        )
        assert config.config == {"pages": ["*"]}
        assert PCF1.match(config.fingerprint)
        assert props.props == {"props": {"theme": "dark"}}
        assert PCF1.match(props.fingerprint)
        # Both documents come from one export of the same Project state.
        assert config.fingerprint == props.fingerprint
        await env.stop()

    _run(scenario())


def test_page_config_and_session_props_are_not_found_without_a_local_document(tmp_path: Path) -> None:
    env = _Env(tmp_path, body=_export_zip(views={"Pages/Overview": {"root": {"type": "x"}}}))

    async def scenario() -> None:
        await env.start()
        with pytest.raises(GatewayError) as missing_config:
            await perspective_page_config_get(
                env.client, env.registry, env.store, env.context("perspective_page_config_get"),
                project_name="Demo", deadline_seconds=10,
            )
        assert missing_config.value.code == "not_found"
        with pytest.raises(GatewayError) as missing_props:
            await perspective_session_props_get(
                env.client, env.registry, env.store, env.context("perspective_session_props_get"),
                project_name="Demo", deadline_seconds=10,
            )
        assert missing_props.value.code == "not_found"
        assert await env.artifact_count() == 0
        assert await env.staging_leftovers() == []
        await env.stop()

    _run(scenario())


def test_a_gateway_without_the_project_export_route_exposes_no_perspective_read(tmp_path: Path) -> None:
    env = _Env(tmp_path, body=_export_zip(), with_project=False)

    async def scenario() -> None:
        await env.start()
        with pytest.raises(GatewayError) as captured:
            await perspective_view_list(
                env.client, env.registry, env.store, env.context("perspective_view_list"),
                project_name="Demo", limit=100, offset=0, deadline_seconds=10,
            )
        assert captured.value.code == "unsupported_capability"
        assert env.requests == []
        await env.stop()

    _run(scenario())


# --------------------------------------------------------------------------- registration


def test_the_registered_tools_reach_their_services_under_the_contract_parameter_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The registration half: a Tool call through the real server, parameters as declared."""

    document = {"root": {"type": "ia.container.flex"}}
    body = _export_zip(views={"Pages/Overview": document}, page_config={"pages": ["*"]})

    async def info(self: GatewayClient, context: Any = None) -> dict[str, str]:
        return {"ignitionVersion": "8.3.8"}

    async def modules(self: GatewayClient, context: Any = None) -> dict[str, list[Any]]:
        return {"items": []}

    async def openapi(self: GatewayClient) -> bytes:
        return json.dumps({"paths": {
            "/data/api/v1/gateway-info": {"get": {}},
            "/data/api/v1/projects/export/{name}": {"get": {}},
        }}).encode()

    async def stream_get_to(self: GatewayClient, path: str, **kwargs: Any) -> int:
        await kwargs["sink"].write(body)
        return len(body)

    monkeypatch.setattr(GatewayClient, "gateway_info", info)
    monkeypatch.setattr(GatewayClient, "healthy_modules", modules)
    monkeypatch.setattr(GatewayClient, "openapi", openapi)
    monkeypatch.setattr(GatewayClient, "stream_get_to", stream_get_to)

    async def scenario() -> None:
        server = server_module.create_server(_settings(data_dir=str(tmp_path)))
        async with Client(server) as client:
            validated = await client.call_tool(
                "perspective_view_validate", {"view": UNKNOWN_COMPONENT_VIEW},
            )
            assert validated.data.valid is True
            listing = await client.call_tool("perspective_view_list", {"projectName": "Demo"})
            assert listing.data.items == ["Pages/Overview"]
            view = await client.call_tool(
                "perspective_view_get", {"projectName": "Demo", "path": "Pages/Overview"},
            )
            assert view.data.view == document
            assert PCF1.match(view.data.fingerprint)
            config = await client.call_tool("perspective_page_config_get", {"projectName": "Demo"})
            assert config.data.config == {"pages": ["*"]}

    _run(scenario())
