"""Phase 5 (P5-3) harness checks: the Perspective fixture and the live case section.

The live stage cannot run from a workstation, so what these tests pin is the part that
decides whether a live run is worth spending: the archive layout the fixture and the Tools
have to agree on, the pure comparisons the preservation and refusal cases rest on, the
plain failure a deployment without the write Tools gets, and, against a stub MCP session
that models the Tools' contract, the whole `perspective_cases` sequence.

The stub is a model of the *contract* (`perspective_view_upsert` commits, the same
document is a `NO_CHANGE`, a stale token is a `conflict`, an inherited path is refused),
so it proves the driver's own chaining and assertions rather than the shipped Tools.
"""

from __future__ import annotations

import asyncio
import importlib.util
import io
import json
from pathlib import Path
import sys
from types import ModuleType
from typing import Any
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[3]
HARNESS = ROOT / "tests/harness/phase4-live-rest"


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


sys.path.insert(0, str(HARNESS))
driver = _load("p5_rest_driver", HARNESS / "rest_driver.py")
provision = _load("p5_provision", HARNESS / "provision.py")

PERSPECTIVE = "com.inductiveautomation.perspective"
PARENT = "MCP_CI_P5_PARENT"
CHILD = "MCP_CI_P5_CHILD"


def _child_archive() -> bytes:
    """The fixture's own child Project, so the stub serves what a live run imports."""

    return provision.child_project_archive("child", PARENT)


# ------------------------------------------------------------------ pure helpers
def test_the_perspective_archive_uses_the_confirmed_layout() -> None:
    entries = provision.export_entries(_child_archive())
    assert sorted(entries) == [
        f"{PERSPECTIVE}/page-config/config.json",
        f"{PERSPECTIVE}/page-config/resource.json",
        f"{PERSPECTIVE}/session-props/props.json",
        f"{PERSPECTIVE}/session-props/resource.json",
        f"{PERSPECTIVE}/views/{driver.DEFAULT_LOCAL_VIEW}/resource.json",
        f"{PERSPECTIVE}/views/{driver.DEFAULT_LOCAL_VIEW}/view.json",
        driver.DEFAULT_UNRELATED_QUERY,
        f"{driver.DEFAULT_UNRELATED_QUERY.rsplit('/', 1)[0]}/resource.json",
        "project.json",
    ]
    # The metadata file beside every data file is what makes the Gateway keep the
    # resource through a project import: a bare data file is dropped.
    manifest = json.loads(entries["project.json"])
    assert manifest["parent"] == PARENT and manifest["enabled"] is True
    # The fixture's documents are ``provision.py``'s constants, the same ones the driver
    # compares the reads against, so the fixture and the expectation cannot drift apart.
    view = json.loads(entries[f"{PERSPECTIVE}/views/{driver.DEFAULT_LOCAL_VIEW}/view.json"])
    assert view == provision.CHILD_VIEW_DOCUMENT
    config = json.loads(entries[f"{PERSPECTIVE}/page-config/config.json"])
    assert config == provision.CHILD_PAGE_CONFIG_DOCUMENT
    props = json.loads(entries[f"{PERSPECTIVE}/session-props/props.json"])
    assert props == provision.CHILD_SESSION_PROPS_DOCUMENT


def test_view_paths_reads_the_logical_paths_of_one_export() -> None:
    entries = provision.export_entries(
        provision.perspective_project_archive(
            title="two",
            views={"Pages/A": provision.view_document("A"), "Pages/B/C": provision.view_document("B")},
        )
    )
    assert provision.view_paths(entries) == ["Pages/A", "Pages/B/C"]
    assert provision.view_paths({}) == []


def test_a_non_archive_export_is_refused() -> None:
    with pytest.raises(provision.ProvisionError):
        provision.export_entries(b"not a zip")


def test_changed_entries_skips_the_target_directory_only() -> None:
    before = {"a/b": b"1", "target/view.json": b"1", "target/resource.json": b"1", "gone": b"1"}
    after = {"a/b": b"1", "target/view.json": b"2", "target/resource.json": b"2", "new": b"1"}
    assert driver.changed_entries(before, after, skip_prefix="target/") == ["gone", "new"]
    assert driver.changed_entries(before, before, skip_prefix="target/") == []


def test_nested_reads_a_path_or_nothing() -> None:
    assert driver._nested({"a": {"b": 1}}, "a", "b") == 1
    assert driver._nested({"a": {"b": 1}}, "a", "c") is None
    assert driver._nested({"a": 1}, "a", "b") is None
    assert driver._nested(None, "a") is None


def test_a_refusal_reason_is_read_from_the_envelope() -> None:
    result = {
        "isError": True,
        "content": [{
            "type": "text",
            "text": json.dumps({"code": "invalid_argument", "message": "reason: inherited_resource"}),
        }],
    }
    assert driver._refusal_mentions(result, driver.INHERITED_REASON) is True
    assert driver._refusal_mentions(result, "something-else") is False
    assert driver._refusal_mentions({"isError": False, "structuredContent": {}}, "x") is False


def test_the_write_tools_are_the_class_enabled_inventory_delta() -> None:
    assert set(driver.PERSPECTIVE_WRITE_TOOLS) <= driver.GATE_ON_INVENTORY
    assert not set(driver.PERSPECTIVE_WRITE_TOOLS) & driver.READ_INVENTORY
    assert driver.GATE_ON_INVENTORY == driver.READ_INVENTORY | set(driver.CONFIG_MUTATION_TOOLS) | set(
        driver.PERSPECTIVE_WRITE_TOOLS
    )


# ------------------------------------------------------------------- the stub Tools
def _zipped(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in entries.items():
            archive.writestr(zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0)), body)
    return buffer.getvalue()


class _FakeProject:
    """A Project the stub Tools read and patch, fingerprinted by the shipped algorithm."""

    def __init__(
        self, entries: dict[str, bytes], tmp_dir: Path, *, inherited: frozenset[str] = frozenset(),
    ) -> None:
        self.entries = dict(entries)
        self.inherited = inherited
        self._tmp = tmp_dir
        self.writes: list[str] = []

    def fingerprint(self) -> str:
        from ignition_rest_mcp.projects.fingerprint import project_fingerprint

        path = self._tmp / "fingerprint.zip"
        path.write_bytes(self.archive())
        return project_fingerprint(str(path))

    def archive(self) -> bytes:
        return _zipped(self.entries)

    def document(self, entry: str) -> Any:
        body = self.entries.get(entry)
        return json.loads(body) if body is not None else None

    def views(self) -> list[str]:
        return provision.view_paths(self.entries)


class _StubSession:
    """The MCP surface `perspective_cases` uses, over one modelled Project."""

    def __init__(self, project: _FakeProject, *, writes: bool = True) -> None:
        self._project = project
        self.available = frozenset({
            driver.PERSPECTIVE_VIEW_LIST_TOOL, driver.PERSPECTIVE_VIEW_GET_TOOL,
            driver.PERSPECTIVE_VIEW_VALIDATE_TOOL, driver.PERSPECTIVE_PAGE_CONFIG_GET_TOOL,
            driver.PERSPECTIVE_SESSION_PROPS_GET_TOOL, "project_export",
        }) | (frozenset(driver.PERSPECTIVE_WRITE_TOOLS) if writes else frozenset())

    async def tools(self) -> frozenset[str]:
        return self.available

    def _ok(self, body: dict[str, Any]) -> dict[str, Any]:
        return {"isError": False, "structuredContent": body}

    def _refusal(self, code: str, message: str = "") -> dict[str, Any]:
        return {
            "isError": True,
            "content": [{"type": "text", "text": json.dumps({"code": code, "message": message})}],
        }

    async def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        project = self._project
        document_entry = f"{PERSPECTIVE}/views/{arguments.get('path', '')}/view.json"
        if tool == "project_export":
            return self._ok({
                "fingerprint": project.fingerprint(),
                "artifact": {"artifactId": "a1", "download": {"path": "/artifacts/a1"}},
            })
        if tool == driver.PERSPECTIVE_VIEW_LIST_TOOL:
            return self._ok({
                "projectName": CHILD, "items": project.views(),
                "page": {"total": 1, "matching": 1, "limit": 100, "offset": 0},
            })
        if tool == driver.PERSPECTIVE_VIEW_GET_TOOL:
            document = project.document(document_entry)
            if document is None:
                return self._refusal("not_found")
            return self._ok({
                "projectName": CHILD, "path": arguments["path"], "view": document,
                "fingerprint": project.fingerprint(),
            })
        if tool == driver.PERSPECTIVE_VIEW_VALIDATE_TOOL:
            view = arguments.get("view")
            if not isinstance(view, dict) or not isinstance(view.get("root"), dict):
                return self._refusal("invalid_argument", "root must be an object")
            return self._ok({"valid": True, "bytes": 12, "depth": 3})
        if tool == driver.PERSPECTIVE_PAGE_CONFIG_GET_TOOL:
            config = project.document(f"{PERSPECTIVE}/page-config/config.json")
            if config is None:
                return self._refusal("not_found")
            return self._ok({"projectName": CHILD, "config": config, "fingerprint": project.fingerprint()})
        if tool == driver.PERSPECTIVE_SESSION_PROPS_GET_TOOL:
            props = project.document(f"{PERSPECTIVE}/session-props/props.json")
            if props is None:
                return self._refusal("not_found")
            return self._ok({"projectName": CHILD, "props": props, "fingerprint": project.fingerprint()})
        if tool == driver.PERSPECTIVE_VIEW_UPSERT_TOOL:
            if arguments["expectedFingerprint"] != project.fingerprint():
                return self._refusal("conflict", "the Project changed since the token was read")
            path = arguments["path"]
            if path in project.inherited and path not in project.views():
                # D15: the resource is defined by an ancestor and not locally, so a write
                # would create a silent local override.
                return self._refusal("invalid_argument", f"reason: {driver.INHERITED_REASON}")
            body = json.dumps(arguments["view"], sort_keys=True).encode("utf-8")
            before = project.entries.get(document_entry)
            project.entries[document_entry] = body
            project.entries[f"{PERSPECTIVE}/views/{arguments['path']}/resource.json"] = (
                provision.resource_metadata(["view.json"])
            )
            if before != body:
                # D16: a candidate equal to the baseline is a NO_CHANGE, and nothing is
                # dispatched, which is what makes the no-op case's `importDispatched` assertion
                # meaningful rather than an echo of the stub.
                project.writes.append(tool)
            return self._ok({
                "projectName": CHILD, "path": arguments["path"],
                "state": "NO_CHANGE" if before == body else "COMMITTED",
                "baselineFingerprint": "pcf1:" + "0" * 64,
                "candidateFingerprint": "pcf1:" + "0" * 64,
                "resultFingerprint": project.fingerprint() if before != body else None,
                "importDispatched": before != body,
            })
        if tool == driver.PERSPECTIVE_VIEW_DELETE_TOOL:
            if arguments["expectedFingerprint"] != project.fingerprint():
                return self._refusal("conflict")
            if project.document(document_entry) is None:
                return self._refusal("not_found")
            del project.entries[document_entry]
            del project.entries[f"{PERSPECTIVE}/views/{arguments['path']}/resource.json"]
            project.writes.append(tool)
            return self._ok({"projectName": CHILD, "state": "COMMITTED", "importDispatched": True})
        if tool == driver.PERSPECTIVE_PAGE_CONFIG_UPDATE_TOOL:
            if arguments["expectedFingerprint"] != project.fingerprint():
                return self._refusal("conflict")
            project.entries[f"{PERSPECTIVE}/page-config/config.json"] = json.dumps(
                arguments["config"], sort_keys=True,
            ).encode("utf-8")
            project.writes.append(tool)
            return self._ok({
                "projectName": CHILD, "state": "COMMITTED", "importDispatched": True,
                "resultFingerprint": project.fingerprint(),
            })
        if tool == driver.PERSPECTIVE_SESSION_PROPS_UPDATE_TOOL:
            if arguments["expectedFingerprint"] != project.fingerprint():
                return self._refusal("conflict")
            project.entries[f"{PERSPECTIVE}/session-props/props.json"] = json.dumps(
                arguments["props"], sort_keys=True,
            ).encode("utf-8")
            project.writes.append(tool)
            return self._ok({
                "projectName": CHILD, "state": "COMMITTED", "importDispatched": True,
                "resultFingerprint": project.fingerprint(),
            })
        raise AssertionError(f"unexpected tool call: {tool}")


class _StaleListSession(_StubSession):
    """A stub whose listing never shows a View that an upsert created.

    The create case claims more than "the write committed": the new View has to appear in
    `perspective_view_list`. This stub is how that claim is exercised, because a listing
    that lags is exactly the failure the case exists to catch.
    """

    def __init__(self, project: _FakeProject) -> None:
        super().__init__(project)
        self._initial = project.views()

    async def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = await super().call(tool, arguments)
        if tool == driver.PERSPECTIVE_VIEW_LIST_TOOL and not result.get("isError"):
            result["structuredContent"]["items"] = list(self._initial)
        return result


class _StubArtifacts:
    """The one artifact route the section uses: the export download."""

    def __init__(self, project: _FakeProject) -> None:
        self._project = project

    async def download(self, path: str, token: str) -> bytes:
        return self._project.archive()

    async def aclose(self) -> None:
        return None


def _run_cases(tmp_path: Path, session: _StubSession, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(driver, "Artifacts", lambda rest_url: _StubArtifacts(session._project))
    return asyncio.run(driver.perspective_cases(
        agent=session, rest_url="http://127.0.0.1:1", agent_token="token",
        parent_project=PARENT, child_project=CHILD, raw_dir=tmp_path,
    ))


def _project(tmp_path: Path) -> _FakeProject:
    return _FakeProject(
        provision.export_entries(_child_archive()), tmp_path,
        inherited=frozenset({driver.DEFAULT_INHERITED_VIEW}),
    )


def test_a_deployment_without_the_write_tools_fails_with_their_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _StubSession(_project(tmp_path), writes=False)
    cases, observations = _run_cases(tmp_path, session, monkeypatch)
    failed = [case for case in cases if not case["ok"]]
    assert [case["case"] for case in failed] == ["perspective-write-tools-are-available"]
    assert failed[0]["observed"] == sorted(driver.PERSPECTIVE_WRITE_TOOLS)
    assert observations["perspectiveWritesAborted"].startswith(
        "the Perspective write Tools are not registered"
    )
    # The reads still ran, so the fixture and the reads are proven either way.
    assert [case["case"] for case in cases if case["case"].startswith("perspective-view-list")]


def test_the_section_passes_against_the_modelled_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _StubSession(_project(tmp_path))
    cases, observations = _run_cases(tmp_path, session, monkeypatch)
    assert [case["case"] for case in cases if not case["ok"]] == []
    names = [case["case"] for case in cases]
    for case in (
        "perspective-view-upsert-commits",
        "perspective-view-upsert-of-the-current-document-is-no-change",
        "perspective-view-upsert-preserves-every-other-entry",
        "perspective-view-upsert-creates-a-view-the-project-lacks",
        "perspective-view-upsert-of-an-inherited-view-is-invalid-argument",
        "perspective-view-upsert-of-an-inherited-view-names-the-reason",
        "perspective-view-upsert-after-an-external-change-is-conflict",
        "perspective-view-upsert-after-an-external-change-imports-nothing",
        "perspective-view-delete-commits",
        "perspective-page-config-update-commits",
        "perspective-session-props-update-commits",
    ):
        assert case in names, case
    assert observations["preservation"]["changedOutsideTarget"] == []
    # The delete case removed the child's own View; the View the create case published is
    # still served, which is the state the last two document updates left behind.
    assert session._project.views() == [driver.CREATED_VIEW_PATH]
    # Every dispatched write ran once, in the order the cases address them: the edit, the
    # create, the external change, the delete, then the two document updates.
    assert session._project.writes == [
        driver.PERSPECTIVE_VIEW_UPSERT_TOOL,
        driver.PERSPECTIVE_VIEW_UPSERT_TOOL,
        driver.PERSPECTIVE_SESSION_PROPS_UPDATE_TOOL,
        driver.PERSPECTIVE_VIEW_DELETE_TOOL,
        driver.PERSPECTIVE_PAGE_CONFIG_UPDATE_TOOL,
        driver.PERSPECTIVE_SESSION_PROPS_UPDATE_TOOL,
    ]


def test_the_create_case_checks_the_listing_and_not_only_the_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _StaleListSession(_project(tmp_path))
    cases, _ = _run_cases(tmp_path, session, monkeypatch)
    assert [case["case"] for case in cases if not case["ok"]] == [
        "perspective-view-upsert-creates-a-view-the-project-lacks"
    ]


class _PlausibleReadSession(_StubSession):
    """A stub that answers a plausible document instead of the provisioned one.

    These are the responses review round 1 named: a View read that returns the requested
    path and a real component type while replacing that View's props and children, and a
    Page configuration or Session properties read that answers an empty object instead of
    the document the fixture imported.
    """

    async def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = await super().call(tool, arguments)
        if result.get("isError"):
            return result
        if tool == driver.PERSPECTIVE_VIEW_GET_TOOL:
            view = result["structuredContent"]["view"]
            view["root"] = {"type": view["root"]["type"], "props": {}, "children": []}
        if tool == driver.PERSPECTIVE_PAGE_CONFIG_GET_TOOL:
            result["structuredContent"]["config"] = {}
        if tool == driver.PERSPECTIVE_SESSION_PROPS_GET_TOOL:
            result["structuredContent"]["props"] = {}
        return result


def test_a_plausible_but_different_document_fails_its_read_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _PlausibleReadSession(_project(tmp_path))
    cases, _ = _run_cases(tmp_path, session, monkeypatch)
    failed = {case["case"] for case in cases if not case["ok"]}
    # A read that only gets the path and the component type right is not the provisioned
    # document, so each of the three document reads has to fail. The cases that compare a
    # document through a later read fail with them, which is what the set records.
    assert {
        "perspective-view-get-returns-the-provisioned-view",
        "perspective-page-config-get-returns-the-document",
        "perspective-session-props-get-returns-the-document",
    } <= failed
