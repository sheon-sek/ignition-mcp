#!/usr/bin/env python3
"""Phase 4 milestone 4c live driver: the REST Mutations on a real Gateway.

Run against a running ``ignition-rest`` server (``--mode gate-on``) and again
against the same server restarted with ``IGNITION_MCP_CONFIG_MUTATION_ENABLED=false``
(``--mode gate-off``). The driver talks MCP over HTTP, the artifact data plane and
the Gateway's own read routes, exactly as an agent would; it never mutates the
Gateway directly, so everything it reports is something an agent could observe.

Live cases (tickets #14, #15, #16, #17, #18 and #19):

- the effective REST inventory is exact with the class enabled *and* disabled, and
  the config-scoped credential sees the Mutation Tools while the read-only one does
  not (D07 discovery);
- an allowlisted update applies, is verified by an independent re-read, and moves
  the Resource signature;
- a stale ``expectedSignature`` is a ``conflict`` and leaves the resource alone;
- a Refused resource type (the Gateway's own API token) is ``permission_denied``
  under a ``*`` Target allowlist, and the resource it refuses to change still works;
- a change to a resource the Target allowlist does not name never reaches Ignition;
- a create publishes a resource, an existing target is a ``conflict``, and a
  refused or non-allowlisted create leaves nothing behind;
- a delete removes the resource (the signature travels in the native path), a second
  delete is ``not_found``, a stale signature is a ``conflict``, and the refused and
  non-allowlisted cases change nothing;
- a rename moves the resource, an occupied destination is a ``conflict``, a
  non-allowlisted source or destination is ``permission_denied``, and a stale
  signature is a ``conflict``;
- a Project import commits an uploaded archive into an existing Project, the
  post-import export is fingerprinted independently by this harness and carries the
  marker entry the archive held, re-importing that content is a ``NO_CHANGE``, the
  pre-commit fingerprint is a stale-token ``conflict``, a Project outside the Target
  allowlist is ``permission_denied``, and an archive owned by another principal is
  ``not_found``;
- an Alarm Notification Pipeline cancel (#18) is refused for the config credential
  (the Tool's effect is CONTROL), for a pipeline the Target allowlist does not name,
  and for a path that is a prefix of — or under — the allowlisted one (D30 §6: exact
  paths, never prefixes); both inputs are bounded; and a pipeline that holds no run for
  the alarm event is a ``not_found`` that changes nothing. A fresh CI Gateway serves no
  pipeline runs, so the dispatched-and-verified path is proven by the unit fixture and
  the runbook records the limitation.
- an artifact removal (#19) needs no Gateway call at all: D30 dropped the artifact HTTP
  route, so the Tool removes its Target from the server's own D17 store. The cases prove
  the Observed state (absence, confirmed by an independent ``artifact_info`` and
  ``artifact_list``), the D30 §6 ownership rule in both directions, the CONFIG scope for
  a read-only credential, both D10 input bounds, the data plane's missing ``DELETE``, and
  the class gate at discovery and at call time.

The Project cases need the D16 writer enabled (``IGNITION_MCP_PROJECT_WRITER_ENABLED``,
``IGNITION_MCP_GATEWAY_ID``), artifact upload and the sensitive exports this driver
reads the Precondition token from. Those gates also decide the expected inventory:
this deployment enables both read gates, so the two sensitive-export Tools are part
of it.

One note on the Target-allowlist expectation: D30 §7 decides ``permission_denied``
for a Phase 4 Mutation whose Target is not in the Target allowlist, and the Tool-scoped
mapping keeps the frozen Phase 3 machinery on its recorded ``operation_disabled`` (G3
evidence asserts that code). This driver therefore asserts exactly one code.

No compatibility evidence row is produced here: a G4 row carries the Gateway/Module
tuple, and this harness deploys no MCP Module. The observations are uploaded as
workflow artifacts and referenced by the G4 close-out.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
from pathlib import Path
import re
import sqlite3
import sys
import time
from typing import Any
import zipfile

import httpx

HARNESS = Path(__file__).resolve().parent
sys.path.insert(0, str(HARNESS.parent / "phase3-live"))

from harness_common import McpHttp, ProbeError, error_envelope  # noqa: E402

UPDATE_TOOL = "config_resource_update"
CREATE_TOOL = "config_resource_create"
DELETE_TOOL = "config_resource_delete"
RENAME_TOOL = "config_resource_rename"
IMPORT_TOOL = "project_import"
TAG_IMPORT_TOOL = "tag_config_import"
ALARM_CANCEL_TOOL = "alarm_pipeline_cancel"
#: D26 ticket #19: the artifact removal is a CONFIG-class REST Mutation with no Gateway
#: route at all (D30 drops the artifact HTTP route), so its discovery follows the class
#: gate and nothing else.
ARTIFACT_DELETE_TOOL = "artifact_delete"
#: Every Phase 4 CONFIG-class REST Mutation Tool (tickets #14–#17 and #19). The
#: CONTROL-class cancel (#18) needs its own scope, so the two lanes are listed
#: separately: the config credential must never see the cancel, and the operator
#: credential must never see these.
CONFIG_MUTATION_TOOLS = (
    UPDATE_TOOL, CREATE_TOOL, DELETE_TOOL, RENAME_TOOL, IMPORT_TOOL, TAG_IMPORT_TOOL,
    ARTIFACT_DELETE_TOOL,
)
CONTROL_MUTATION_TOOLS = (ALARM_CANCEL_TOOL,)
MUTATION_TOOLS = CONFIG_MUTATION_TOOLS + CONTROL_MUTATION_TOOLS
REFUSED_TYPE = "ignition/api-token"
REFUSED_NAME = "ignition-mcp-ci"
#: An allowed *singleton* (its documented change item carries no name).
SINGLETON_TYPE = "ignition/cobranding"
#: The names the create, delete and rename cases use. They are provisioned by
#: ``provision.py`` except the one the create case publishes.
CREATED_RESOURCE = "MCP_CI_AUDIT_CREATED"
RENAME_SOURCE = "MCP_CI_AUDIT_RENAME_SOURCE"
RENAME_SOURCE_2 = "MCP_CI_AUDIT_RENAME_SOURCE_2"
RENAMED_RESOURCE = "MCP_CI_AUDIT_RENAMED"
#: A destination name no Target allowlist lists, so the denial is the allowlist's.
UNALLOWLISTED_RENAMED = "MCP_CI_AUDIT_RENAMED_NOT_ALLOWED"
#: A name of the allowlisted type that the Target allowlists deliberately omit.
NOT_ALLOWLISTED_RESOURCE = "MCP_CI_AUDIT_OTHER"

#: D30 owner ruling 5 (ticket #35): every config Mutation is made in the ``core``
#: collection and names it on the wire; the other collection is what a case offers as
#: the value that must be refused.
CORE_COLLECTION = "core"
OTHER_COLLECTION = "custom"
#: The documented collection route of the allowlisted type: what a write has to be
#: dispatched to (the reads and the ``DELETE``/rename routes carry the collection as a
#: query parameter instead).
RESOURCE_COLLECTION_PATH = "/data/api/v1/resources/"

#: The two names the Project import cases use: the allowlisted Target and an existing
#: Project the Target allowlist deliberately does not name.
DEFAULT_PROJECT = "MCP_CI_IMPORT"
DEFAULT_CONTROL_PROJECT = "MCP_CI_IMPORT_CONTROL"

#: The pipeline cancel cases (#18): the exact pipeline path the Target allowlist names
#: (inside the Project the import cases provision, so it is run-unique), a second
#: pipeline it deliberately does not name, and an Alarm Event identifier no run holds.
#: A freshly commissioned Gateway serves no notification pipeline runs — the Phase 2
#: live probe recorded exactly that — so ``no-run`` is the state the live cases prove.
DEFAULT_PIPELINE = f"project:{DEFAULT_PROJECT}:/pipeline:MCP_CI_Notify"
DEFAULT_CONTROL_PIPELINE = f"project:{DEFAULT_CONTROL_PROJECT}:/pipeline:MCP_CI_Notify"
DEFAULT_ALARM_EVENT_ID = "00000000-0000-4000-8000-000000000000"
#: The D10 bounds this driver asserts against, from the Tool's own contract.
MAX_PIPELINE_PATH_LENGTH = 512
MAX_ALARM_EVENT_ID_LENGTH = 128
#: The candidate's edit. G3's live runs proved the Gateway rewrites ``project.json`` on
#: import (docs: tests/harness/phase3-live/driver.py), so a candidate that changes that
#: file — or that adds an entry the Gateway does not recognise as a resource — does not
#: round-trip byte-for-byte, and D16 could never observe C == B. The edit therefore
#: appends a SQL comment to the first named-query payload, which the Gateway stores
#: verbatim (the same edit the G3 live transaction case uses).
IMPORT_QUERY_RE = re.compile(r"^ignition/named-query/.+/query\.sql$")
IMPORT_MARKER_PREFIX = "mcp-p4-import"

#: The Tag import cases (#17): the provider ``provision.py`` creates, the path its
#: source Tags are provisioned at, the destination the Target allowlist names (as a
#: *prefix*, D30 §1), a second destination it deliberately does not, and the provider
#: D30 §1 reserves for the Runtime Target Policy.
DEFAULT_TAG_PROVIDER = "MCP_CI_TAGS"
DEFAULT_TAG_SOURCE_PATH = "source"
DEFAULT_TAG_TARGET_PATH = "target"
DEFAULT_TAG_CONTROL_PATH = "target_not_allowed"
DEFAULT_RESERVED_TAG_PROVIDER = "IgnitionMCPPolicy"

#: The effective REST inventory: this deployment enables the sensitive-export gate as
#: well as the config mutation class (the Project cases read their Precondition token
#: with ``project_export``), and with the class disabled the two sensitive exports and
#: the read Tools remain. ``readonly`` itself is unchanged by Phase 4.
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
    "project_export",
    "tag_config_export",
})
GATE_ON_INVENTORY = READ_INVENTORY | set(CONFIG_MUTATION_TOOLS)
#: The CONTROL credential's inventory: the cancel Tool appears for it and none of the
#: CONFIG-class Tools do (D07: discovery follows the credential's scopes).
OPERATOR_INVENTORY = READ_INVENTORY | set(CONTROL_MUTATION_TOOLS)

#: D30 §7: a Target outside the Target allowlist is `permission_denied` for a Phase 4
#: Mutation. Exactly one code is accepted; the driver must not tolerate the frozen
#: Phase 3 code, or the Tool's contract would be unverified.
TARGET_DENIAL_CODE = "permission_denied"


class DriverError(RuntimeError):
    pass


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _check(cases: list[dict[str, Any]], name: str, expected: Any, observed: Any) -> None:
    cases.append({"case": name, "expected": expected, "observed": observed, "ok": expected == observed})


class Session:
    """One MCP session plus the raw-body recorder for the evidence directory."""

    def __init__(self, url: str, token: str, raw_dir: Path) -> None:
        self._mcp = McpHttp(url, token)
        self._raw = raw_dir
        self._serial = 0

    async def aclose(self) -> None:
        await self._mcp.aclose()

    async def initialize(self) -> None:
        await self._mcp.initialize()

    async def tools(self) -> frozenset[str]:
        tools = await self._mcp.tools_list()
        names = frozenset(str(tool.get("name")) for tool in tools)
        self._record("tools-list", [{"name": tool.get("name"), "tags": tool.get("tags")} for tool in tools])
        return names

    async def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = await self._mcp.tool_call(tool, arguments)
        self._record(f"call-{tool}", result)
        return result

    async def call_raw(self, tool: str, arguments: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """``tools/call`` returning the request id as well (ticket #20).

        A JSON-RPC error object comes back as the response rather than as a raised
        ``ProbeError``, because the cancellation case asserts the peer's own code for a
        request this client cancelled.
        """

        identifier, decoded = await self._mcp.call_raw(tool, arguments)
        self._record(f"call-{tool}", decoded)
        return identifier, decoded

    async def cancel_in_flight(self) -> int:
        """Cancel the call this session is currently waiting on (ticket #20)."""

        identifier = self._mcp.last_request_id
        await self._mcp.notify("notifications/cancelled", {
            "requestId": identifier, "reason": "phase4 rest fault harness",
        })
        return identifier

    async def signature(self, resource_type: str, name: str = "") -> str:
        body = await self.get(resource_type, name)
        signature = body.get("signature")
        if not isinstance(signature, str) or not signature:
            raise DriverError(f"{resource_type}/{name} reported no Resource signature")
        return signature

    async def get(self, resource_type: str, name: str = "") -> dict[str, Any]:
        result = await self.call("config_resource_get", {
            "resourceType": resource_type, "name": name, "collection": "", "defaultIfUndefined": False,
        })
        if result.get("isError"):
            raise DriverError(f"config_resource_get failed: {error_envelope(result).get('code')}")
        structured = result.get("structuredContent")
        if not isinstance(structured, dict):
            raise DriverError("config_resource_get returned no structuredContent")
        return structured

    def _record(self, label: str, value: Any) -> None:
        self._serial += 1
        _write(self._raw / f"{self._serial:02d}-{label}.json", value)


def _envelope_code(result: dict[str, Any]) -> str:
    envelope = error_envelope(result)
    code = envelope.get("code")
    if not isinstance(code, str) or not code:
        raise DriverError(f"error envelope carried no code: {envelope!r}")
    return code


def structured(result: dict[str, Any]) -> dict[str, Any]:
    if result.get("isError"):
        raise DriverError(f"expected a successful result, got {error_envelope(result)!r}")
    body = result.get("structuredContent")
    if not isinstance(body, dict):
        raise DriverError("the Tool returned no structuredContent")
    return body


def _refusal_code(result: dict[str, Any]) -> Any:
    """What a call that was expected to be refused actually did.

    A refusal case must fail on the observed value, never on a driver traceback: a
    result that came back successful — or refused in a shape the envelope reader cannot
    parse — is recorded as itself and fails the case.
    """

    if not result.get("isError"):
        return {"isError": False, "structuredContent": result.get("structuredContent")}
    try:
        return _envelope_code(result)
    except (DriverError, ProbeError):
        return {"unreadableRefusal": str(result.get("content"))[:160]}


async def _pipeline_state(session: "Session", path: str) -> Any:
    """What the bounded status read serves for one pipeline path.

    The comparison the pipeline cases need: the runs' alarm event ids, or the D06 code
    the read refused with. Two equal values mean the pipeline did not change.
    """

    result = await session.call("alarm_pipeline_status", {"path": path, "limit": 100, "offset": 0})
    if result.get("isError"):
        return _refusal_code(result)
    body = result.get("structuredContent")
    items = body.get("items") if isinstance(body, dict) else None
    return [item.get("alarmEventId") for item in (items or [])]


async def _absent(agent: "Session", resource_type: str, name: str) -> bool:
    """Whether the Gateway really has no such resource.

    The read Tool answering ``not_found`` is the only shape that counts as absent: a
    denial or a transport failure must not be mistaken for a missing resource.
    """

    result = await agent.call("config_resource_get", {
        "resourceType": resource_type, "name": name, "collection": "", "defaultIfUndefined": False,
    })
    if not result.get("isError"):
        return False
    return _envelope_code(result) == "not_found"


class Artifacts:
    """The D17 artifact data plane, used exactly as an agent would use it."""

    def __init__(self, rest_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=rest_url, timeout=180.0)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def download(self, path: str, token: str) -> bytes:
        response = await self._client.get(path, headers={"Authorization": "Bearer " + token})
        if response.status_code != 200:
            raise DriverError(f"artifact download {path} returned HTTP {response.status_code}")
        return response.content

    async def upload(self, payload: bytes, token: str, *, label: str) -> str:
        response = await self._client.post(
            "/artifacts", params={"kind": "project_archive"}, content=payload,
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/zip"},
        )
        if response.status_code != 201:
            raise DriverError(
                f"artifact upload ({label}) returned HTTP {response.status_code}: {response.text[:200]}",
            )
        artifact_id = response.json().get("artifactId")
        if not isinstance(artifact_id, str) or not artifact_id:
            raise DriverError(f"artifact upload ({label}) returned no artifactId")
        return artifact_id


def _entries(payload: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        return {info.filename: archive.read(info.filename) for info in archive.infolist()}


def _zipped(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in entries.items():
            archive.writestr(zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0)), body)
    return buffer.getvalue()


def _edited_candidate(payload: bytes, marker: str) -> bytes:
    """The exported archive with one named-query payload appended to.

    The D16 fingerprint ignores container representation, so this candidate's logical
    content differs from the Project by exactly the marker comment.
    """

    entries = _entries(payload)
    for name in sorted(entries):
        if IMPORT_QUERY_RE.match(name):
            text = entries[name].decode("utf-8", errors="replace").rstrip()
            entries[name] = f"{text}\n-- {marker}\n".encode("utf-8")
            break
    else:
        raise DriverError("the disposable Project has no named-query payload to edit")
    return _zipped(entries)


def _marker_present(payload: bytes, marker: str) -> bool:
    needle = marker.encode("utf-8")
    return any(needle in body for body in _entries(payload).values())


def _entry_digests(payload: bytes) -> dict[str, str]:
    return {
        name: hashlib.sha256(body).hexdigest() for name, body in _entries(payload).items()
    }


def _fingerprint(payload: bytes, raw_dir: Path, name: str) -> str:
    """The D16 ``pcf1`` fingerprint of an archive, computed in this process.

    The harness runs from the repository, so it can check the server's reported
    fingerprint against the algorithm the contract publishes instead of trusting it.
    """

    from ignition_rest_mcp.projects.fingerprint import project_fingerprint

    path = raw_dir / name
    path.write_bytes(payload)
    return project_fingerprint(str(path))


def _round_trip_diff(candidate: bytes, observed: bytes, raw_dir: Path) -> dict[str, Any]:
    """Which entries differ between the staged candidate and the post-import export.

    ``candidate`` is the archive this harness uploaded, whose bytes are what the
    transaction staged and dispatched; ``observed`` is the Gateway's own re-export. A
    mismatch names the entries the Gateway changed, so one failed run is enough to
    diagnose it (the diagnostic the G3 transaction case had to add after its first
    round-trip mismatch).
    """

    before = _entry_digests(candidate)
    after = _entry_digests(observed)
    return {
        "candidateEntries": sorted(before),
        "observedEntries": sorted(after),
        "changedEntries": sorted(
            name for name in set(before) | set(after) if before.get(name) != after.get(name)
        ),
        "candidateFingerprint": _fingerprint(candidate, raw_dir, "import-candidate.zip"),
        "observedFingerprint": _fingerprint(observed, raw_dir, "import-reexport.zip"),
    }


async def project_import_cases(
    *,
    agent: "Session",
    rest_url: str,
    reader_token: str,
    agent_token: str,
    project: str,
    control_project: str,
    raw_dir: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The D16 Project import cases (ticket #16)."""

    cases: list[dict[str, Any]] = []
    observations: dict[str, Any] = {}
    marker = f"{IMPORT_MARKER_PREFIX}={project}"
    artifacts = Artifacts(rest_url)
    try:
        exported = structured(await agent.call("project_export", {"projectName": project}))
        baseline = str(exported["fingerprint"])
        observations["importBaselineFingerprint"] = baseline
        exported_archive = await artifacts.download(
            str(exported["artifact"]["download"]["path"]), agent_token,
        )
        candidate_bytes = _edited_candidate(exported_archive, marker)
        candidate = await artifacts.upload(candidate_bytes, agent_token, label="candidate")
        result = await agent.call(IMPORT_TOOL, {
            "projectName": project, "artifactId": candidate, "expectedFingerprint": baseline,
        })
        body = result.get("structuredContent") if not result.get("isError") else None
        observations["importResult"] = body if isinstance(body, dict) else error_envelope(result)
        _check(cases, "project-import-commits", "COMMITTED",
               body.get("state") if isinstance(body, dict) else None)
        if not isinstance(body, dict) or body.get("state") != "COMMITTED":
            # Gather the round-trip diagnostic before failing, so one run answers what
            # the Gateway did with the candidate (the G3 transaction lesson), and do not
            # run the cases that depend on a committed import.
            fresh = structured(await agent.call("project_export", {"projectName": project}))
            fresh_archive = await artifacts.download(
                str(fresh["artifact"]["download"]["path"]), agent_token,
            )
            observations["importRoundTrip"] = _round_trip_diff(candidate_bytes, fresh_archive, raw_dir)
            observations["importAborted"] = "the commit case did not reach COMMITTED"
            return cases, observations
        _check(cases, "project-import-baseline-is-the-callers-read", baseline,
               body.get("baselineFingerprint"))
        _check(cases, "project-import-verifies-its-own-candidate", body.get("candidateFingerprint"),
               body.get("resultFingerprint"))
        # importDispatched answers "did an import request leave the server", not "did the
        # Gateway acknowledge it": a commit (confirmed or recovered) reports true, and only
        # a call that sent nothing — here the no-op below — reports false.
        _check(cases, "project-import-reports-the-dispatch", True, body.get("importDispatched"))

        # Independent evidence: re-export the Project, fingerprint it here, and look for
        # the marker the candidate carried.
        fresh = structured(await agent.call("project_export", {"projectName": project}))
        fresh_archive = await artifacts.download(
            str(fresh["artifact"]["download"]["path"]), agent_token,
        )
        independent = _fingerprint(fresh_archive, raw_dir, "import-reexport.zip")
        observations["importIndependentFingerprint"] = independent
        _check(cases, "project-export-fingerprint-is-independent", fresh.get("fingerprint"), independent)
        _check(cases, "project-import-content-lands", independent, body.get("candidateFingerprint"))
        _check(cases, "project-import-marker-is-present", True, _marker_present(fresh_archive, marker))

        # D16 no-op idempotency: importing the content the Tool just committed.
        again = structured(await agent.call(IMPORT_TOOL, {
            "projectName": project, "artifactId": candidate, "expectedFingerprint": independent,
        }))
        _check(cases, "project-import-of-the-current-content-is-no-change", "NO_CHANGE",
               again.get("state"))
        _check(cases, "project-import-no-change-dispatches-nothing", False, again.get("importDispatched"))

        # The token the caller read before that commit is now stale (D30 §2).
        stale = await agent.call(IMPORT_TOOL, {
            "projectName": project, "artifactId": candidate, "expectedFingerprint": baseline,
        })
        _check(cases, "project-import-stale-fingerprint-is-conflict", "conflict", _envelope_code(stale))
        after_stale = structured(await agent.call("project_export", {"projectName": project}))
        _check(cases, "project-import-stale-fingerprint-changes-nothing", independent,
               after_stale.get("fingerprint"))

        # A Project the Target allowlist does not name (D30 §7) ...
        control = structured(await agent.call("project_export", {"projectName": control_project}))
        denied = await agent.call(IMPORT_TOOL, {
            "projectName": control_project, "artifactId": candidate,
            "expectedFingerprint": control["fingerprint"],
        })
        _check(cases, "project-import-non-allowlisted-project-is-permission-denied",
               TARGET_DENIAL_CODE, _envelope_code(denied))
        control_after = structured(await agent.call("project_export", {"projectName": control_project}))
        _check(cases, "project-import-non-allowlisted-project-changes-nothing",
               control.get("fingerprint"), control_after.get("fingerprint"))

        # ... and an archive owned by another principal (D30 §6).
        foreign = await artifacts.upload(candidate_bytes, reader_token, label="other-principal")
        invisible = await agent.call(IMPORT_TOOL, {
            "projectName": project, "artifactId": foreign, "expectedFingerprint": independent,
        })
        _check(cases, "project-import-invisible-artifact-is-not-found", "not_found",
               _envelope_code(invisible))
    finally:
        await artifacts.aclose()
    return cases, observations


def _tag_names(payload: bytes) -> set[str]:
    """Every Tag name one exported document holds *below its root*.

    The driver's independent view of an export: the Tool reports exact Tag paths as its
    Observed state, and these names are what a *separate* export of the destination
    serves, so the two are compared without trusting either one's encoding of the other.
    The document's own root is the requested path node (or the provider root), so it is
    skipped: what matters is the Tags the export contains.
    """

    try:
        document = json.loads(payload)
    except (ValueError, UnicodeDecodeError) as error:
        raise DriverError(f"the Tag export body is not JSON: {error}") from error
    names: set[str] = set()
    stack: list[Any] = []
    if isinstance(document, dict) and isinstance(document.get("name"), str) and document.get("name"):
        stack.extend(document.get("tags") or [])
    else:
        stack.append(document)
    while stack:
        node = stack.pop()
        if isinstance(node, list):
            stack.extend(node)
            continue
        if not isinstance(node, dict):
            continue
        name = node.get("name")
        if isinstance(name, str) and name:
            names.add(name)
        children = node.get("tags")
        if isinstance(children, list):
            stack.extend(children)
    return names


async def _tag_names_at(
    agent: "Session", artifacts: "Artifacts", token: str, provider: str, path: str,
) -> set[str] | None:
    """The Tag names the Gateway serves at one path (``None`` = it serves no such path)."""

    exported = await agent.call("tag_config_export", {
        "provider": provider, "path": path, "recursive": True, "includeUdts": False,
    })
    if exported.get("isError"):
        # A path that does not exist is exactly what a denied import must leave behind.
        return None
    body = structured(exported)
    payload = await artifacts.download(str(body["artifact"]["download"]["path"]), token)
    return _tag_names(payload)


async def tag_import_cases(
    *,
    agent: "Session",
    reader: "Session",
    rest_url: str,
    agent_token: str,
    provider: str,
    source_path: str,
    target_path: str,
    control_path: str,
    nested_path: str = "",
    reserved_provider: str = DEFAULT_RESERVED_TAG_PROVIDER,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The Tag config import cases (ticket #17).

    ``provision.py`` published the source Tags and the MCP Module published the export
    artifact, so the import's content is real Gateway state and the bytes it dispatched
    are the bytes ``tag_config_export`` produced. ``nested_path`` defaults to a path
    below ``target_path``, which is the destination the Target allowlist authorizes as a
    prefix (D30 §1).
    """

    nested_path = nested_path or f"{target_path}/nested"

    cases: list[dict[str, Any]] = []
    observations: dict[str, Any] = {}
    artifacts = Artifacts(rest_url)
    try:
        source = structured(await agent.call("tag_config_export", {
            "provider": provider, "path": source_path, "recursive": True, "includeUdts": False,
        }))
        source_payload = await artifacts.download(
            str(source["artifact"]["download"]["path"]), agent_token,
        )
        declared = _tag_names(source_payload)
        observations["tagSourceNames"] = sorted(declared)
        _check(cases, "tag-import-source-is-not-empty", True, bool(declared))
        if not declared:
            observations["tagImportAborted"] = "the source export held no Tags"
            return cases, observations

        imported = await agent.call(TAG_IMPORT_TOOL, {
            "artifactId": source["artifact"]["artifactId"], "provider": provider, "path": target_path,
        })
        body = imported.get("structuredContent") if not imported.get("isError") else None
        observations["tagImportResult"] = body if isinstance(body, dict) else error_envelope(imported)
        if not isinstance(body, dict):
            # Record the destination and the source as the Gateway serves them *before*
            # failing: one run is then enough to tell a refused import from a verification
            # whose document rule does not match the Gateway's.
            observations["tagAbortedDestination"] = sorted(
                await _tag_names_at(agent, artifacts, agent_token, provider, target_path) or ()
            )
            observations["tagAbortedSource"] = sorted(
                await _tag_names_at(agent, artifacts, agent_token, provider, source_path) or ()
            )
            _check(cases, "tag-import-applies", "a structured result",
                   observations["tagImportResult"])
            observations["tagImportAborted"] = "the create case did not return a result"
            return cases, observations
        observed = body.get("observedState")
        _check(cases, "tag-import-reports-no-missing-tag",
               [], observed.get("missing") if isinstance(observed, dict) else None)

        # Independent evidence: a second export of the destination, downloaded here.
        served = await _tag_names_at(agent, artifacts, agent_token, provider, target_path)
        observations["tagTargetNames"] = sorted(served or ())
        _check(cases, "tag-import-destination-serves-every-source-tag", True, declared <= (served or set()))
        present = observed.get("present") if isinstance(observed, dict) else None
        present_names = {str(path).rsplit("/", 1)[-1] for path in (present or [])}
        _check(cases, "tag-import-observed-state-covers-the-source-tags", True,
               declared <= present_names)
        _check(cases, "tag-import-observed-state-is-relative-to-the-target", True,
               all(str(path).startswith(f"{target_path}/") for path in (present or [])))
        # 'Abort' keeps the mutation inside its Target: the source path is untouched.
        source_after = await _tag_names_at(agent, artifacts, agent_token, provider, source_path)
        _check(cases, "tag-import-leaves-the-source-path-untouched", sorted(declared),
               sorted(source_after or ()))

        # D30 §4/D11: the same artifact into the same destination collides.
        collision = await agent.call(TAG_IMPORT_TOOL, {
            "artifactId": source["artifact"]["artifactId"], "provider": provider, "path": target_path,
        })
        _check(cases, "tag-import-into-an-occupied-destination-is-conflict", "conflict",
               _envelope_code(collision))
        after = await _tag_names_at(agent, artifacts, agent_token, provider, target_path)
        _check(cases, "tag-import-conflict-changes-nothing", sorted(served or ()), sorted(after or ()))

        # A destination the Target allowlist does not name (D30 §7).
        denied = await agent.call(TAG_IMPORT_TOOL, {
            "artifactId": source["artifact"]["artifactId"], "provider": provider, "path": control_path,
        })
        _check(cases, "tag-import-non-allowlisted-path-is-permission-denied", TARGET_DENIAL_CODE,
               _envelope_code(denied))
        control = await _tag_names_at(agent, artifacts, agent_token, provider, control_path)
        observations["tagControlNames"] = sorted(control or ())
        _check(cases, "tag-import-non-allowlisted-path-creates-nothing", True,
               not (control or set()) & declared)

        # ... and an artifact owned by another principal (D30 §6).
        reader_export = structured(await reader.call("tag_config_export", {
            "provider": provider, "path": source_path, "recursive": True, "includeUdts": False,
        }))
        invisible = await agent.call(TAG_IMPORT_TOOL, {
            "artifactId": reader_export["artifact"]["artifactId"], "provider": provider,
            "path": target_path,
        })
        _check(cases, "tag-import-invisible-artifact-is-not-found", "not_found",
               _envelope_code(invisible))

        # D30 §1/D08: the allowlisted entry is a provider-qualified path *prefix*, so a
        # destination below the path it names is authorized, and the Tags really land.
        nested = await agent.call(TAG_IMPORT_TOOL, {
            "artifactId": source["artifact"]["artifactId"], "provider": provider,
            "path": nested_path,
        })
        nested_body = nested.get("structuredContent") if not nested.get("isError") else None
        observations["tagNestedResult"] = (
            nested_body if isinstance(nested_body, dict) else error_envelope(nested)
        )
        _check(cases, "tag-import-under-the-allowlisted-prefix-applies", True,
               isinstance(nested_body, dict))
        nested_served = await _tag_names_at(agent, artifacts, agent_token, provider, nested_path)
        observations["tagNestedNames"] = sorted(nested_served or ())
        _check(cases, "tag-import-prefix-destination-serves-every-source-tag", True,
               declared <= (nested_served or set()))

        # D30 §1: the Runtime Target Policy's provider is reserved, whatever the Target
        # allowlist says, so a Tag import addressed to it never reaches the Gateway.
        reserved = await agent.call(TAG_IMPORT_TOOL, {
            "artifactId": source["artifact"]["artifactId"], "provider": reserved_provider,
            "path": target_path,
        })
        observations["tagReservedProviderResult"] = error_envelope(reserved)
        _check(cases, "tag-import-reserved-policy-provider-is-permission-denied",
               TARGET_DENIAL_CODE, _envelope_code(reserved))
        _check(cases, "tag-import-reserved-policy-provider-says-which-rule",
               True, "reserved Runtime Target Policy" in str(
                   observations["tagReservedProviderResult"].get("message", "")
               ))
    finally:
        await artifacts.aclose()
    return cases, observations


async def alarm_pipeline_cancel_cases(
    *,
    agent: "Session",
    operator: "Session",
    pipeline: str,
    control_pipeline: str,
    alarm_event_id: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The Alarm Notification Pipeline cancel cases (ticket #18).

    A freshly commissioned CI Gateway serves no notification pipeline runs — the Phase 2
    live probe recorded exactly that, and creating a *run* needs an Alarm Event
    notifying through a provisioned profile. So the live cases are the ones that hold
    whatever the Gateway is running: the class gate, the scope-by-effect gate, the exact
    Target rule (D30 §6), the D10 input bounds, and the bounded pre-dispatch read that
    refuses a cancel for a run that does not exist. The dispatched-and-verified path
    itself is fixture-proven in the unit suite; the runbook records the limitation and
    what would close it.
    """

    cases: list[dict[str, Any]] = []
    observations: dict[str, Any] = {
        "pipelinePath": pipeline, "pipelineControlPath": control_pipeline,
        "pipelineAlarmEventId": alarm_event_id,
    }
    before = await _pipeline_state(agent, pipeline)
    observations["pipelineStateBefore"] = before

    # D07: this Tool's effect is CONTROL, so the config credential is refused and never
    # reaches the handler.
    credential = await agent.call(ALARM_CANCEL_TOOL, {
        "path": pipeline, "alarmEventId": alarm_event_id,
    })
    _check(cases, "pipeline-cancel-config-credential-is-permission-denied", "permission_denied",
           _refusal_code(credential))

    # D30 §7: a pipeline the Target allowlist does not name.
    target = await operator.call(ALARM_CANCEL_TOOL, {
        "path": control_pipeline, "alarmEventId": alarm_event_id,
    })
    _check(cases, "pipeline-cancel-non-allowlisted-pipeline-is-permission-denied",
           TARGET_DENIAL_CODE, _refusal_code(target))

    # D30 §6: exact paths, never prefixes — neither a path under the allowlisted one nor
    # its own parent may be authorized by that entry.
    child = await operator.call(ALARM_CANCEL_TOOL, {
        "path": f"{pipeline}/child", "alarmEventId": alarm_event_id,
    })
    _check(cases, "pipeline-cancel-path-under-the-target-is-permission-denied",
           TARGET_DENIAL_CODE, _refusal_code(child))
    parent = await operator.call(ALARM_CANCEL_TOOL, {
        "path": pipeline.rsplit(":/pipeline:", 1)[0], "alarmEventId": alarm_event_id,
    })
    _check(cases, "pipeline-cancel-target-parent-path-is-permission-denied",
           TARGET_DENIAL_CODE, _refusal_code(parent))

    # D10: both inputs are bounded, and the refusal names the requested value.
    long_path = "project:MCP_CI:/pipeline:" + "P" * (MAX_PIPELINE_PATH_LENGTH + 10)
    oversize_path = await operator.call(ALARM_CANCEL_TOOL, {
        "path": long_path, "alarmEventId": alarm_event_id,
    })
    _check(cases, "pipeline-cancel-oversize-path-is-limit-exceeded", "limit_exceeded",
           _refusal_code(oversize_path))
    oversize_event = await operator.call(ALARM_CANCEL_TOOL, {
        "path": pipeline, "alarmEventId": "e" * (MAX_ALARM_EVENT_ID_LENGTH + 1),
    })
    _check(cases, "pipeline-cancel-oversize-event-is-limit-exceeded", "limit_exceeded",
           _refusal_code(oversize_event))
    blank = await operator.call(ALARM_CANCEL_TOOL, {"path": "   ", "alarmEventId": alarm_event_id})
    _check(cases, "pipeline-cancel-blank-path-is-invalid-argument", "invalid_argument",
           _refusal_code(blank))

    # The bounded pre-dispatch read: no run for that alarm event means nothing to cancel,
    # so the call is refused without a dispatch.
    no_run = await operator.call(ALARM_CANCEL_TOOL, {
        "path": pipeline, "alarmEventId": alarm_event_id,
    })
    observations["pipelineNoRunResult"] = (
        no_run.get("structuredContent") if not no_run.get("isError") else _refusal_code(no_run)
    )
    _check(cases, "pipeline-cancel-without-a-run-for-the-event-is-not-found", "not_found",
           _refusal_code(no_run))

    after = await _pipeline_state(agent, pipeline)
    observations["pipelineStateAfter"] = after
    _check(cases, "pipeline-cancel-refusals-change-nothing", before, after)
    return cases, observations


async def _artifact_present(session: "Session", artifact_id: str) -> Any:
    """Whether one artifact is still served to the caller that owns it.

    ``artifact_list`` is the independent read: the identifier is present or it is not,
    and a refusal the envelope reader cannot parse is reported as itself.
    """

    result = await session.call("artifact_list", {"kind": "", "limit": 100, "offset": 0})
    if result.get("isError"):
        return _refusal_code(result)
    body = result.get("structuredContent")
    items = body.get("items") if isinstance(body, dict) else None
    return any(item.get("artifactId") == artifact_id for item in (items or []))


async def artifact_delete_cases(
    *,
    agent: "Session",
    reader: "Session",
    rest_url: str,
    agent_token: str,
    reader_token: str,
    project: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The artifact removal cases (ticket #19).

    D30 drops the artifact HTTP route, so this Tool is a CONFIG Mutation that dispatches
    nothing: the cases below prove the whole flow on the server's own store — the
    artifact disappears from every read path, the remover is the principal that owns it,
    an artifact another principal owns is invisible, both D10 input bounds hold, and the
    dropped route really is absent.

    The Target allowlist in this deployment is ``*`` (D30 §3 requires an explicit
    wildcard to allow all): artifact identifiers are generated at removal time, so a
    denial case would need a fixed identifier no run can name. The unit fixture pins the
    Target-allowlist denial; ``docs/development/phase-4.md`` records the limitation.
    """

    cases: list[dict[str, Any]] = []
    observations: dict[str, Any] = {}
    artifacts = Artifacts(rest_url)
    try:
        exported = structured(await agent.call("project_export", {"projectName": project}))
        artifact_id = str(exported["artifact"]["artifactId"])
        observations["artifactDeleteTarget"] = artifact_id
        _check(cases, "artifact-delete-target-is-served", True,
               await _artifact_present(agent, artifact_id))

        deleted = await agent.call(ARTIFACT_DELETE_TOOL, {"artifactId": artifact_id})
        body = deleted.get("structuredContent") if not deleted.get("isError") else None
        observations["artifactDeleteResult"] = (
            body if isinstance(body, dict) else _refusal_code(deleted)
        )
        _check(cases, "artifact-delete-removes-the-artifact", True, isinstance(body, dict))
        if not isinstance(body, dict):
            observations["artifactDeleteAborted"] = "the removal did not return a result"
            return cases, observations
        _check(cases, "artifact-delete-reports-absence", artifact_id, body.get("artifactId"))
        _check(cases, "artifact-delete-reports-the-kind", "project_export", body.get("kind"))
        _check(cases, "artifact-delete-observed-state-is-absence", False, body.get("present"))

        # Independent evidence: the metadata read, then the caller's own listing.
        info = await agent.call("artifact_info", {"artifactId": artifact_id})
        _check(cases, "artifact-delete-independent-reread-is-not-found", "not_found",
               _refusal_code(info))
        _check(cases, "artifact-delete-listing-no-longer-serves-it", False,
               await _artifact_present(agent, artifact_id))

        # D17: a second removal is not a success and not a second effect.
        again = await agent.call(ARTIFACT_DELETE_TOOL, {"artifactId": artifact_id})
        _check(cases, "artifact-delete-of-an-absent-target-is-not-found", "not_found",
               _refusal_code(again))

        # D30 §6: the owner is the authorization, and an artifact another principal owns
        # answers exactly as an unknown identifier does. The removal above was the
        # caller's own artifact — project_export attributed it to this credential.
        reader_export = structured(await reader.call("project_export", {"projectName": project}))
        invisible_id = str(reader_export["artifact"]["artifactId"])
        invisible = await agent.call(ARTIFACT_DELETE_TOOL, {"artifactId": invisible_id})
        _check(cases, "artifact-delete-invisible-artifact-is-not-found", "not_found",
               _refusal_code(invisible))
        _check(cases, "artifact-delete-invisible-artifact-survives", True,
               await _artifact_present(reader, invisible_id))

        # D07: the effect is CONFIG, so the read-only credential never reaches it.
        reader_upload = await artifacts.upload(
            _zipped({"project.json": b'{"title":"p4"}'}), reader_token,
            label="p4-rest-artifact-delete-denied",
        )
        denied = await reader.call(ARTIFACT_DELETE_TOOL, {"artifactId": reader_upload})
        _check(cases, "artifact-delete-reader-credential-is-permission-denied",
               "permission_denied", _refusal_code(denied))
        _check(cases, "artifact-delete-denied-call-changes-nothing", True,
               await _artifact_present(reader, reader_upload))

        # D10: both input bounds, refused before anything is resolved.
        oversize = await agent.call(ARTIFACT_DELETE_TOOL, {"artifactId": "a" * 129})
        _check(cases, "artifact-delete-oversize-identifier-is-invalid-argument",
               "invalid_argument", _refusal_code(oversize))
        malformed = await agent.call(ARTIFACT_DELETE_TOOL, {"artifactId": "../objects/x"})
        _check(cases, "artifact-delete-malformed-identifier-is-invalid-argument",
               "invalid_argument", _refusal_code(malformed))

        # D30: the data plane keeps GET, HEAD and POST — this Tool is the only delete path.
        status = await _delete_route_status(rest_url, agent_token, artifact_id)
        _check(cases, "artifact-delete-data-plane-route-is-absent", 405, status)
    finally:
        await artifacts.aclose()
    return cases, observations


async def _delete_route_status(rest_url: str, token: str, path: str) -> Any:
    """The status the artifact data plane answers to ``DELETE`` (D30: 405, no route)."""

    async with httpx.AsyncClient(base_url=rest_url, timeout=30.0) as client:
        response = await client.delete(
            f"/artifacts/{path}", headers={"Authorization": "Bearer " + token},
        )
    return response.status_code


async def run_gate_on(
    *,
    rest_url: str,
    reader_token: str,
    agent_token: str,
    operator_token: str = "",
    resource_type: str,
    allowlisted: str,
    unallowlisted: str,
    singleton_type: str,
    created_name: str,
    rename_source: str,
    rename_source_2: str,
    renamed_name: str,
    unallowlisted_renamed: str,
    project: str = DEFAULT_PROJECT,
    control_project: str = DEFAULT_CONTROL_PROJECT,
    tag_provider: str = DEFAULT_TAG_PROVIDER,
    tag_source_path: str = DEFAULT_TAG_SOURCE_PATH,
    tag_target_path: str = DEFAULT_TAG_TARGET_PATH,
    tag_control_path: str = DEFAULT_TAG_CONTROL_PATH,
    pipeline: str = DEFAULT_PIPELINE,
    control_pipeline: str = DEFAULT_CONTROL_PIPELINE,
    alarm_event_id: str = DEFAULT_ALARM_EVENT_ID,
    raw_dir: Path,
) -> dict[str, Any]:
    """The live cases that need a mutation class enabled.

    The CONFIG class is enabled for the config credential and the CONTROL class for the
    operator credential, so this run also proves D07's scope-by-effect rule twice: each
    credential sees its own Mutation Tools and neither sees the other's.
    """

    cases: list[dict[str, Any]] = []
    observations: dict[str, Any] = {}
    reader = Session(rest_url + "/mcp", reader_token, raw_dir)
    agent = Session(rest_url + "/mcp", agent_token, raw_dir)
    operator = Session(rest_url + "/mcp", operator_token or agent_token, raw_dir)
    try:
        await reader.initialize()
        await agent.initialize()
        await operator.initialize()

        _check(cases, "inventory-agent-exact", sorted(GATE_ON_INVENTORY), sorted(await agent.tools()))
        _check(cases, "inventory-reader-exact", sorted(READ_INVENTORY), sorted(await reader.tools()))
        _check(
            cases, "inventory-operator-exact", sorted(OPERATOR_INVENTORY),
            sorted(await operator.tools()),
        )

        before = await agent.signature(resource_type, allowlisted)
        observations["signatureBefore"] = before
        updated = await agent.call(UPDATE_TOOL, {
            "resourceType": resource_type,
            "expectedSignature": before,
            "name": allowlisted,
            "description": "Disposable Phase 4 CI audit profile (updated live)",
            "config": {"profile": {"type": "local", "retentionDays": 21}},
        })
        _check(cases, "allowlisted-update-applies", True, not updated.get("isError"))
        body = updated.get("structuredContent") if isinstance(updated.get("structuredContent"), dict) else {}
        after = body.get("signature")
        observations["signatureAfter"] = after
        _check(cases, "update-moves-the-signature", True, isinstance(after, str) and after != before)
        _check(
            cases, "observed-state-carries-the-change",
            {"retentionDays": 21, "description": "Disposable Phase 4 CI audit profile (updated live)"},
            {
                "retentionDays": (body.get("observedState") or {}).get("config", {}).get("profile", {})
                .get("retentionDays"),
                "description": (body.get("observedState") or {}).get("description"),
            },
        )
        # D30 owner ruling 5: an omitted collection means core, and the result says so.
        _check(cases, "update-reports-the-core-collection", CORE_COLLECTION, body.get("collection"))
        reread = await agent.get(resource_type, allowlisted)
        _check(cases, "independent-reread-confirms", after, reread.get("signature"))

        stale = await agent.call(UPDATE_TOOL, {
            "resourceType": resource_type,
            "expectedSignature": before,
            "name": allowlisted,
            "enabled": False,
        })
        _check(cases, "stale-signature-is-conflict", "conflict", _envelope_code(stale))
        _check(cases, "stale-signature-changes-nothing", after, await agent.signature(resource_type, allowlisted))

        refused_signature = await agent.signature(REFUSED_TYPE, REFUSED_NAME)
        refused = await agent.call(UPDATE_TOOL, {
            "resourceType": REFUSED_TYPE,
            "expectedSignature": refused_signature,
            "name": REFUSED_NAME,
            "enabled": False,
        })
        _check(cases, "refused-resource-type-is-permission-denied", "permission_denied", _envelope_code(refused))
        _check(
            cases, "refused-resource-still-usable", refused_signature,
            await agent.signature(REFUSED_TYPE, REFUSED_NAME),
        )

        other_before = await agent.signature(resource_type, unallowlisted)
        denied = await agent.call(UPDATE_TOOL, {
            "resourceType": resource_type,
            "expectedSignature": other_before,
            "name": unallowlisted,
            "enabled": False,
        })
        denied_code = _envelope_code(denied)
        observations["targetDenialCode"] = denied_code
        _check(cases, "non-allowlisted-target-is-permission-denied", TARGET_DENIAL_CODE, denied_code)
        _check(
            cases, "non-allowlisted-target-changes-nothing", other_before,
            await agent.signature(resource_type, unallowlisted),
        )

        # A singleton's documented change item carries no name; its update proves the
        # item is built from the Gateway's own request schema.
        singleton_before = await agent.signature(singleton_type)
        singleton = await agent.call(UPDATE_TOOL, {
            "resourceType": singleton_type,
            "expectedSignature": singleton_before,
            "description": "Disposable Phase 4 CI branding (updated live)",
        })
        _check(cases, "singleton-update-applies", True, not singleton.get("isError"))
        singleton_signature = (
            singleton.get("structuredContent") or {}
        ).get("signature")
        _check(
            cases, "singleton-update-moves-the-signature", True,
            isinstance(singleton_signature, str) and singleton_signature != singleton_before,
        )

        # --------------------------------------------- the core collection (#35)
        # D30 owner ruling 5: the caller may name the one collection these Mutations
        # address, and anything else is refused as input.
        core_signature = await agent.signature(resource_type, allowlisted)
        explicit = await agent.call(UPDATE_TOOL, {
            "resourceType": resource_type,
            "expectedSignature": core_signature,
            "name": allowlisted,
            "collection": CORE_COLLECTION,
            "description": "Disposable Phase 4 CI audit profile (core collection)",
        })
        _check(cases, "explicit-core-collection-is-accepted", True, not explicit.get("isError"))
        explicit_body = structured(explicit) if not explicit.get("isError") else {}
        observations["explicitCoreSignature"] = explicit_body.get("signature")
        _check(
            cases, "explicit-core-collection-reports-core", CORE_COLLECTION,
            explicit_body.get("collection"),
        )

        refused_collection = await agent.call(UPDATE_TOOL, {
            "resourceType": resource_type,
            "expectedSignature": explicit_body.get("signature"),
            "name": allowlisted,
            "collection": OTHER_COLLECTION,
            "enabled": False,
        })
        observations["nonCoreCollectionRefusal"] = refused_collection
        _check(
            cases, "non-core-collection-is-invalid-argument", "invalid_argument",
            _refusal_code(refused_collection),
        )
        _check(
            cases, "non-core-collection-changes-nothing", explicit_body.get("signature"),
            await agent.signature(resource_type, allowlisted),
        )

        # ------------------------------------------------------------ create (#15)
        created = await agent.call(CREATE_TOOL, {
            "resourceType": resource_type,
            "name": created_name,
            "description": "Disposable Phase 4 CI audit profile (created live)",
            "config": {"profile": {"type": "local", "retentionDays": 11}},
        })
        _check(cases, "allowlisted-create-applies", True, not created.get("isError"))
        created_body = structured(created)
        created_signature = created_body.get("signature")
        _check(
            cases, "create-reports-the-published-resource",
            {"name": created_name, "retentionDays": 11,
             "description": "Disposable Phase 4 CI audit profile (created live)"},
            {
                "name": created_body.get("name"),
                "retentionDays": (created_body.get("observedState") or {})
                .get("config", {}).get("profile", {}).get("retentionDays"),
                "description": (created_body.get("observedState") or {}).get("description"),
            },
        )
        published = await agent.get(resource_type, created_name)
        _check(cases, "create-independent-reread-confirms", created_signature, published.get("signature"))

        again = await agent.call(CREATE_TOOL, {
            "resourceType": resource_type, "name": created_name,
        })
        _check(cases, "create-of-an-existing-target-is-conflict", "conflict", _envelope_code(again))
        _check(
            cases, "create-of-an-existing-target-changes-nothing", created_signature,
            await agent.signature(resource_type, created_name),
        )

        refused_create = await agent.call(CREATE_TOOL, {
            "resourceType": REFUSED_TYPE, "name": f"{REFUSED_NAME}-impostor",
        })
        _check(
            cases, "create-of-a-refused-resource-type-is-permission-denied",
            "permission_denied", _envelope_code(refused_create),
        )
        _check(
            cases, "create-of-a-refused-resource-type-publishes-nothing", True,
            await _absent(agent, REFUSED_TYPE, f"{REFUSED_NAME}-impostor"),
        )

        denied_create = await agent.call(CREATE_TOOL, {
            "resourceType": resource_type, "name": f"{unallowlisted}_CREATED",
        })
        _check(
            cases, "create-outside-the-target-allowlist-is-permission-denied",
            TARGET_DENIAL_CODE, _envelope_code(denied_create),
        )
        _check(
            cases, "create-outside-the-target-allowlist-publishes-nothing", True,
            await _absent(agent, resource_type, f"{unallowlisted}_CREATED"),
        )

        # ------------------------------------------------------------ delete (#15)
        deleted = await agent.call(DELETE_TOOL, {
            "resourceType": resource_type,
            "expectedSignature": created_signature,
            "name": created_name,
        })
        _check(cases, "allowlisted-delete-applies", True, not deleted.get("isError"))
        _check(cases, "delete-reports-absence", False, structured(deleted).get("present"))
        _check(cases, "delete-independent-reread-shows-absence", True,
               await _absent(agent, resource_type, created_name))

        gone_again = await agent.call(DELETE_TOOL, {
            "resourceType": resource_type,
            "expectedSignature": created_signature,
            "name": created_name,
        })
        _check(cases, "delete-of-an-absent-target-is-not-found", "not_found", _envelope_code(gone_again))

        # The token belongs to another resource, so the Gateway's own read-compare
        # refuses it; the signature the Target carries right now is what proves the
        # refusal changed nothing.
        current_signature = await agent.signature(resource_type, allowlisted)
        stale_delete = await agent.call(DELETE_TOOL, {
            "resourceType": resource_type,
            "expectedSignature": singleton_signature,
            "name": allowlisted,
        })
        _check(cases, "delete-with-a-stale-signature-is-conflict", "conflict",
               _envelope_code(stale_delete))
        _check(cases, "delete-with-a-stale-signature-changes-nothing", current_signature,
               await agent.signature(resource_type, allowlisted))

        refused_delete = await agent.call(DELETE_TOOL, {
            "resourceType": REFUSED_TYPE,
            "expectedSignature": await agent.signature(REFUSED_TYPE, REFUSED_NAME),
            "name": REFUSED_NAME,
        })
        _check(
            cases, "delete-of-a-refused-resource-type-is-permission-denied",
            "permission_denied", _envelope_code(refused_delete),
        )
        _check(cases, "refused-resource-survives-the-delete-denial", True,
               not await _absent(agent, REFUSED_TYPE, REFUSED_NAME))

        denied_delete = await agent.call(DELETE_TOOL, {
            "resourceType": resource_type,
            "expectedSignature": await agent.signature(resource_type, unallowlisted),
            "name": unallowlisted,
        })
        _check(
            cases, "delete-outside-the-target-allowlist-is-permission-denied",
            TARGET_DENIAL_CODE, _envelope_code(denied_delete),
        )
        _check(cases, "delete-outside-the-target-allowlist-changes-nothing", True,
               not await _absent(agent, resource_type, unallowlisted))

        # ------------------------------------------------------------ rename (#15)
        source_signature = await agent.signature(resource_type, rename_source)
        renamed = await agent.call(RENAME_TOOL, {
            "resourceType": resource_type,
            "expectedSignature": source_signature,
            "name": rename_source,
            "newName": renamed_name,
        })
        _check(cases, "allowlisted-rename-applies", True, not renamed.get("isError"))
        renamed_body = structured(renamed)
        _check(
            cases, "rename-reports-both-names",
            {"previousName": rename_source, "name": renamed_name},
            {"previousName": renamed_body.get("previousName"), "name": renamed_body.get("name")},
        )
        moved_signature = renamed_body.get("signature")
        _check(cases, "rename-independently-shows-the-old-name-vacant", True,
               await _absent(agent, resource_type, rename_source))
        _check(cases, "rename-independently-shows-the-new-name-holding-it", moved_signature,
               (await agent.get(resource_type, renamed_name)).get("signature"))

        occupied = await agent.call(RENAME_TOOL, {
            "resourceType": resource_type,
            "expectedSignature": await agent.signature(resource_type, rename_source_2),
            "name": rename_source_2,
            "newName": renamed_name,
        })
        _check(cases, "rename-onto-an-occupied-destination-is-conflict", "conflict",
               _envelope_code(occupied))
        _check(cases, "rename-onto-an-occupied-destination-changes-nothing", True,
               not await _absent(agent, resource_type, rename_source_2))

        stale_rename = await agent.call(RENAME_TOOL, {
            "resourceType": resource_type,
            # A token that belongs to a different resource: the read-compare refuses it
            # before anything is dispatched, whatever the destination looks like.
            "expectedSignature": moved_signature,
            "name": rename_source_2,
            "newName": renamed_name,
        })
        _check(cases, "rename-with-a-stale-signature-is-conflict", "conflict",
               _envelope_code(stale_rename))
        _check(cases, "rename-with-a-stale-signature-changes-nothing", True,
               not await _absent(agent, resource_type, rename_source_2))

        refused_rename = await agent.call(RENAME_TOOL, {
            "resourceType": REFUSED_TYPE,
            "expectedSignature": await agent.signature(REFUSED_TYPE, REFUSED_NAME),
            "name": REFUSED_NAME,
            "newName": f"{REFUSED_NAME}-renamed",
        })
        _check(
            cases, "rename-of-a-refused-resource-type-is-permission-denied",
            "permission_denied", _envelope_code(refused_rename),
        )
        _check(cases, "refused-resource-survives-the-rename-denial", True,
               not await _absent(agent, REFUSED_TYPE, REFUSED_NAME))

        denied_rename = await agent.call(RENAME_TOOL, {
            "resourceType": resource_type,
            "expectedSignature": moved_signature,
            "name": renamed_name,
            "newName": unallowlisted_renamed,
        })
        _check(
            cases, "rename-into-an-unallowlisted-destination-is-permission-denied",
            TARGET_DENIAL_CODE, _envelope_code(denied_rename),
        )
        _check(cases, "rename-into-an-unallowlisted-destination-changes-nothing", moved_signature,
               await agent.signature(resource_type, renamed_name))

        # --------------------------------------------------- project import (#16)
        try:
            import_cases, import_observations = await project_import_cases(
                agent=agent, rest_url=rest_url, reader_token=reader_token, agent_token=agent_token,
                project=project, control_project=control_project, raw_dir=raw_dir,
            )
        except (DriverError, ProbeError) as error:
            # The section still reports what it managed to observe, and the run fails on
            # this row rather than on a bare traceback.
            import_cases = [{
                "case": "project-import-section", "expected": "no driver error",
                "observed": str(error), "ok": False,
            }]
            import_observations = {}
        cases.extend(import_cases)
        observations.update(import_observations)

        # --------------------------------------------------- tag config import (#17)
        try:
            tag_cases, tag_observations = await tag_import_cases(
                agent=agent, reader=reader, rest_url=rest_url, agent_token=agent_token,
                provider=tag_provider, source_path=tag_source_path,
                target_path=tag_target_path, control_path=tag_control_path,
            )
        except (DriverError, ProbeError) as error:
            tag_cases = [{
                "case": "tag-import-section", "expected": "no driver error",
                "observed": str(error), "ok": False,
            }]
            tag_observations = {}
        cases.extend(tag_cases)
        observations.update(tag_observations)

        # --------------------------------------------- alarm pipeline cancel (#18)
        try:
            pipeline_cases, pipeline_observations = await alarm_pipeline_cancel_cases(
                agent=agent, operator=operator, pipeline=pipeline,
                control_pipeline=control_pipeline, alarm_event_id=alarm_event_id,
            )
        except (DriverError, ProbeError) as error:
            pipeline_cases = [{
                "case": "pipeline-cancel-section", "expected": "no driver error",
                "observed": str(error), "ok": False,
            }]
            pipeline_observations = {}
        cases.extend(pipeline_cases)
        observations.update(pipeline_observations)

        # ------------------------------------------------------ artifact delete (#19)
        try:
            artifact_cases, artifact_observations = await artifact_delete_cases(
                agent=agent, reader=reader, rest_url=rest_url, agent_token=agent_token,
                reader_token=reader_token, project=project,
            )
        except (DriverError, ProbeError) as error:
            artifact_cases = [{
                "case": "artifact-delete-section", "expected": "no driver error",
                "observed": str(error), "ok": False,
            }]
            artifact_observations = {}
        cases.extend(artifact_cases)
        observations.update(artifact_observations)
    finally:
        await reader.aclose()
        await agent.aclose()
        await operator.aclose()
    return {"mode": "gate-on", "cases": cases, "observations": observations}


async def run_gate_off(*, rest_url: str, agent_token: str, raw_dir: Path) -> dict[str, Any]:
    """The same inventory question with the class disabled, plus the call-time deny.

    Two Tools are refused at call time: the capability-backed update and the artifact
    removal, which has no Gateway capability to lose and is therefore hidden and
    refused by the class gate alone.
    """

    cases: list[dict[str, Any]] = []
    agent = Session(rest_url + "/mcp", agent_token, raw_dir)
    try:
        await agent.initialize()
        _check(cases, "inventory-gate-off-exact", sorted(READ_INVENTORY), sorted(await agent.tools()))

        executed = "EXECUTED-UNEXPECTED"
        try:
            result = await agent.call(UPDATE_TOOL, {
                "resourceType": "ignition/audit-profile", "expectedSignature": "sig",
                "name": "MCP_CI_AUDIT",
            })
        except ProbeError as error:
            # A component FastMCP disabled is refused at the router: a JSON-RPC
            # error is exactly the call-time refusal this case demands.
            executed = f"router-refused: {str(error)[:160]}"
        else:
            if result.get("isError"):
                try:
                    executed = f"isError:{_envelope_code(result)}"
                except (DriverError, ProbeError):
                    # FastMCP refuses a disabled component with a plain-text error
                    # result rather than the D06 envelope. Any refusal shape is
                    # admissible here; EXECUTED-UNEXPECTED is not.
                    executed = f"isError:{str(result.get('content'))[:160]}"
        _check(cases, "disabled-class-call-is-refused", True, executed != "EXECUTED-UNEXPECTED")

        artifact_executed = "EXECUTED-UNEXPECTED"
        try:
            artifact_result = await agent.call(ARTIFACT_DELETE_TOOL, {"artifactId": "0" * 32})
        except ProbeError as error:
            artifact_executed = f"router-refused: {str(error)[:160]}"
        else:
            if artifact_result.get("isError"):
                try:
                    artifact_executed = f"isError:{_envelope_code(artifact_result)}"
                except (DriverError, ProbeError):
                    artifact_executed = f"isError:{str(artifact_result.get('content'))[:160]}"
        _check(
            cases, "disabled-class-artifact-delete-is-refused", True,
            artifact_executed != "EXECUTED-UNEXPECTED",
        )
    finally:
        await agent.aclose()
    return {"mode": "gate-off", "cases": cases, "observations": {}}


# ------------------------------------------------------------------ ticket #20
#: The fault-injecting proxy (`fault_proxy.py`): one stdlib-only hop between this
#: server and the Gateway, armed over its control port. It is the only way the live
#: harness can produce a transport failure the *server* has to classify.
FAULT_CONNECT_REFUSED = "connect_refused"
FAULT_DROP_MID_BODY = "drop_mid_body"
FAULT_DROP_AFTER_BODY = "drop_after_body"
FAULT_DELAY_RESPONSE = "delay_response"
FAULT_REFUSE_AFTER_FORWARD = "refuse_after_forward"
#: Which route each fault is armed against. A fault must never catch a read the Tool
#: needs for its Precondition or its bounded read-back, and one-shot arming means the
#: next matching request is the dispatch the case is about.
RESOURCE_WRITE_ROUTE: dict[str, Any] = {
    "method": "PUT", "pathContains": "/data/api/v1/resources/",
}
#: The read every config-resource Mutation makes to compare the caller's Precondition
#: token before it dispatches.
RESOURCE_READ_ROUTE: dict[str, Any] = {
    "method": "GET", "pathContains": "/data/api/v1/resources/",
}
IMPORT_WRITE_ROUTE: dict[str, Any] = {
    "method": "POST", "pathContains": "/projects/import/",
}
#: The two exports the D16 transaction reads before it dispatches (its baseline and its
#: mandatory pre-import re-export), so a refused connect can be aimed at the import
#: itself instead of at the reads it needs first.
IMPORT_EXPORT_ROUTE: dict[str, Any] = {
    "method": "GET", "pathContains": "/projects/export/",
}
IMPORT_PRE_DISPATCH_EXPORTS = 2
#: The D18 phases an audited Mutation leaves, in order: the guarded executor's
#: ``decision``, ``attempt`` and ``result``, then the one the invocation lifecycle writes
#: for the call itself. Both result rows are load-bearing and the frozen Phase 3/4 suite
#: pins them (``_outcomes()[-1]`` is the D06 code the caller was given): the executor's
#: names the *dispatch boundary* — ``cancelled``/``outcome_unknown`` for a write that died
#: in flight — and the lifecycle's is the outcome the call ended with.
AUDIT_PHASES = ("decision", "attempt", "result", "result")
#: JSON-RPC "Request cancelled": the peer's answer to a request this client cancelled.
REQUEST_CANCELLED = -32800
#: The two dispatch boundaries an ambiguous write can be classified into. Both mean
#: "possibly dispatched, no answer", which is what the D30 §2/D16 rules then read back.
AMBIGUOUS_BOUNDARIES = ("sent_partial", "sent_complete_no_response")
#: D16 transaction states a reconciliation has ended.
TERMINAL_STATES = (
    "COMMITTED", "NO_CHANGE", "CONFLICTED", "FAILED_PRE_IMPORT", "NOT_APPLIED",
    "OUTCOME_UNKNOWN", "RECOVERY_REQUIRED",
)


class ProxyFaults:
    """The fault proxy's control channel, as a case uses it."""

    def __init__(self, control_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=control_url, timeout=30.0)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def state(self) -> dict[str, Any]:
        response = await self._client.get("/state")
        if response.status_code != 200:
            raise DriverError(f"the fault proxy /state returned HTTP {response.status_code}")
        body = response.json()
        if not isinstance(body, dict):
            raise DriverError("the fault proxy /state did not return an object")
        return body

    async def arm(self, mode: str, **match: Any) -> dict[str, Any]:
        response = await self._client.post("/fault", json={"mode": mode, **match})
        if response.status_code != 200:
            raise DriverError(f"the fault proxy refused mode {mode}: HTTP {response.status_code}")
        body = response.json()
        if not isinstance(body, dict):
            raise DriverError("the fault proxy /fault did not return an object")
        return body

    async def counter(self, name: str) -> int:
        """One proxy counter, which is how a case proves how many writes left the server."""

        return int((await self.state()).get("counters", {}).get(name, 0))

    async def mark(self) -> int:
        """The sequence number of the last request the proxy saw.

        A case takes this before it arms, so it addresses *its own* faulted request and
        never a request an earlier case faulted with the same mode.
        """

        return int((await self.state()).get("requestsSeen", 0))

    async def faulted(self, mode: str, *, after: int = 0) -> dict[str, Any] | None:
        """The request entry the proxy faulted with ``mode`` after sequence ``after``."""

        for entry in reversed((await self.state()).get("requests", [])):
            if entry.get("fault") == mode and int(entry.get("seq", 0)) > after:
                return entry
        return None

    async def wait_forwarded(self, mode: str, *, after: int, timeout: float) -> dict[str, Any]:
        """Block until this case's faulted request reached the Gateway.

        A case that cancels a call has to know the dispatch is in flight before it sends
        the cancellation, or it would cancel a call that never left the process.
        """

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            entry = await self.faulted(mode, after=after)
            if entry is not None and entry.get("forwarded"):
                return entry
            await asyncio.sleep(0.1)
        raise DriverError(f"the {mode} fault never forwarded its request to the Gateway")


class Evidence:
    """Read-only views of the server's own durable state (harness-only evidence).

    The fault cases need what the *server* recorded, not what the caller saw: its D18
    audit rows and, for a D16 import, the transaction row a reconciliation ends. Both
    databases belong to the deployment's data directory, and this reader opens them
    read-only, so observing a run can never change it.
    """

    def __init__(self, data_dir: Path) -> None:
        self._dir = data_dir

    def _connect(self, name: str) -> sqlite3.Connection:
        path = self._dir / name
        if not path.is_file():
            raise DriverError(f"{path} is missing: is --data-dir the running server's?")
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=20.0)
        connection.row_factory = sqlite3.Row
        return connection

    def audit_mark(self) -> int:
        """The audit log's high-water mark, so a case reads only its own rows."""

        with self._connect("audit.db") as connection:
            row = connection.execute("SELECT COALESCE(MAX(seq), 0) FROM audit_log").fetchone()
        return int(row[0])

    def audit_rows(self, mark: int, tool: str) -> list[dict[str, Any]]:
        with self._connect("audit.db") as connection:
            rows = connection.execute(
                "SELECT phase, outcome, error_code, target_type, target_id FROM audit_log"
                " WHERE seq > ? AND tool = ? ORDER BY seq",
                (mark, tool),
            ).fetchall()
        return [dict(row) for row in rows]

    def transaction_mark(self) -> int:
        """The transaction table's high-water rowid.

        A case takes this before it calls the Tool so it reads *its own* row: a call that
        is refused before the transaction starts (an unreachable hop) creates none, and a
        read that silently fell back to an earlier case's row would hide exactly that.
        """

        with self._connect("state.db") as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(rowid), 0) FROM project_transactions",
            ).fetchone()
        return int(row[0])

    def transaction(self, project: str, *, after: int = 0) -> dict[str, Any] | None:
        with self._connect("state.db") as connection:
            row = connection.execute(
                "SELECT state, import_dispatched, import_outcome, import_status, error_code,"
                " candidate_fingerprint, result_fingerprint FROM project_transactions"
                " WHERE project_name = ? AND rowid > ? ORDER BY rowid DESC LIMIT 1",
                (project, after),
            ).fetchone()
        return None if row is None else dict(row)

    async def await_transaction_settled(
        self, project: str, *, after: int = 0, timeout: float,
    ) -> dict[str, Any]:
        """Wait for the D16 reconcile loop to end the interrupted import's row."""

        deadline = time.monotonic() + timeout
        last: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            last = self.transaction(project, after=after)
            if last is not None and str(last["state"]) in TERMINAL_STATES:
                return last
            await asyncio.sleep(0.5)
        raise DriverError(f"the import transaction for {project} never settled: {last!r}")


def _audit_phases(rows: list[dict[str, Any]]) -> list[str]:
    return [str(row["phase"]) for row in rows]


def _audit_outcomes(rows: list[dict[str, Any]]) -> list[str]:
    return [str(row["outcome"]) for row in rows]


async def _fault_update(
    agent: "Session", resource_type: str, name: str, signature: str, *,
    retention_days: int, description: str,
) -> dict[str, Any]:
    """One update call whose Precondition token the case read before it armed the fault."""

    return await agent.call(UPDATE_TOOL, {
        "resourceType": resource_type, "name": name, "expectedSignature": signature,
        "description": description,
        "config": {"profile": {"type": "local", "retentionDays": retention_days}},
    })


def _retention(body: dict[str, Any]) -> Any:
    """``retentionDays`` from either shape the read Tools return.

    A ``config_resource_get`` nests the resource under ``resource``; a Mutation result
    reports the same data as its Observed state.
    """

    for holder in (body.get("resource"), body.get("observedState")):
        profile = ((holder or {}).get("config") or {}).get("profile") or {}
        if "retentionDays" in profile:
            return profile["retentionDays"]
    return None


async def _await_retention(
    agent: "Session", resource_type: str, name: str, expected: Any, *, timeout: float = 15.0,
) -> Any:
    """Read the resource until the intended value is served, or give up and report it.

    A cancelled dispatch may still be being applied by the Gateway when the call
    settles, so a single read would race it; the value this returns is what the case
    compares against.
    """

    deadline = time.monotonic() + timeout
    seen: Any = None
    while time.monotonic() < deadline:
        seen = _retention(await agent.get(resource_type, name))
        if seen == expected:
            return seen
        await asyncio.sleep(0.2)
    return seen


async def fault_update_cases(
    *, agent: "Session", resource_type: str, allowlisted: str, faults: ProxyFaults,
    evidence: Evidence, tool_timeout_seconds: float, raw_dir: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The transport faults against ``config_resource_update`` (ticket #20).

    Every case arms the fault for the resource write route, reads the Precondition token
    through the same proxy, calls the Tool the way an agent would, and then judges the
    result against three independent records: what the caller saw, what the *Gateway* saw
    (the proxy's account of the hop it owns), and what the *server* audited.
    """

    cases: list[dict[str, Any]] = []
    observations: dict[str, Any] = {}
    description = "Disposable Phase 4 CI audit profile (fault cases)"

    # --------------------------- the core collection on the wire (ticket #35)
    # A real Gateway answers a read that omits the collection exactly as it answers one
    # that names `core`, so Gateway state alone cannot show which request the server
    # sent. The proxy owns the hop the server's own client wrote through, and it records
    # every request target: that record is what proves the pin live (D30 owner ruling 5).
    signature = await agent.signature(resource_type, allowlisted)
    hop = await faults.mark()
    pinned = await agent.call(UPDATE_TOOL, {
        "resourceType": resource_type,
        "expectedSignature": signature,
        "name": allowlisted,
        "description": description,
    })
    pinned_body = structured(pinned) if not pinned.get("isError") else {}
    _check(cases, "core-collection-update-applies", True, not pinned.get("isError"))
    _check(
        cases, "core-collection-update-moves-the-signature", True,
        isinstance(pinned_body.get("signature"), str) and pinned_body.get("signature") != signature,
    )
    hop_state = await faults.state()
    pinned_requests = [
        entry for entry in hop_state.get("requests", [])
        if int(entry.get("seq", 0)) > hop
        and RESOURCE_COLLECTION_PATH in str(entry.get("target", ""))
    ]
    observations["coreCollectionHop"] = pinned_requests
    reads = [entry for entry in pinned_requests if entry.get("method") == "GET"]
    writes = [entry for entry in pinned_requests if entry.get("method") == "PUT"]
    _check(cases, "core-collection-read-count", 2, len(reads))
    _check(
        cases, "core-collection-is-on-every-read", True,
        reads and all(f"collection={CORE_COLLECTION}" in str(entry["target"]) for entry in reads),
    )
    _check(cases, "core-collection-write-count", 1, len(writes))
    _check(
        cases, "core-collection-write-names-the-collection-route",
        f"{RESOURCE_COLLECTION_PATH}{resource_type}?allowInvalidReferences=false",
        writes[0]["target"] if writes else None,
    )

    # ------------------------------------------- the hop is gone before the call (D23)
    # The first failure D23 lists is "Gateway unreachable": the proxy takes its data
    # listener away before the call starts, so the Tool's own read-compare of the
    # Precondition token is the first thing to fail. Nothing may be dispatched, and the
    # audit must show a refusal rather than an attempt.
    signature = await agent.signature(resource_type, allowlisted)
    armed = await faults.arm(FAULT_CONNECT_REFUSED)
    observations["unreachableListening"] = armed.get("listening")
    mark = evidence.audit_mark()
    result = await _fault_update(
        agent, resource_type, allowlisted, signature, retention_days=40, description=description,
    )
    code = _refusal_code(result)
    observations["unreachableCode"] = code
    _check(cases, "fault-unreachable-hop-is-gateway-unavailable", "gateway_unavailable", code)
    rows = evidence.audit_rows(mark, UPDATE_TOOL)
    observations["unreachableAudit"] = rows
    _check(cases, "fault-unreachable-hop-dispatches-nothing", False,
           any(str(row["phase"]) == "attempt" for row in rows))
    reopened = await faults.arm("none")
    _check(
        cases, "fault-unreachable-hop-proxy-reopened-its-listener", True, reopened.get("listening"),
    )
    _check(
        cases, "fault-unreachable-hop-changes-nothing", signature,
        await agent.signature(resource_type, allowlisted),
    )

    # ------------------------------------------------ a known non-attempt (D08)
    # The Tool reads the resource once to compare the caller's Precondition token before
    # it dispatches, so the refused connect is aimed past that read: the proxy answers it
    # and then takes its data listener away, and the *dispatch* connect is refused. (A
    # pooled keep-alive connection would otherwise let the write ride a live socket, and
    # arming the refusal before the read would only refuse the read-balance step.)
    signature = await agent.signature(resource_type, allowlisted)
    await faults.arm(
        FAULT_REFUSE_AFTER_FORWARD, afterCount=1, **RESOURCE_READ_ROUTE,
    )
    mark = evidence.audit_mark()
    result = await _fault_update(
        agent, resource_type, allowlisted, signature, retention_days=41, description=description,
    )
    code = _refusal_code(result)
    observations["notSentCode"] = code
    _check(cases, "fault-not-sent-is-a-known-non-attempt", "gateway_unavailable", code)
    state_after = await faults.state()
    observations["notSentProxy"] = state_after
    _check(
        cases, "fault-not-sent-closed-the-hop-before-the-write", False,
        state_after.get("listening"),
    )
    rows = evidence.audit_rows(mark, UPDATE_TOOL)
    observations["notSentAudit"] = rows
    _check(cases, "fault-not-sent-audits-the-attempt", list(AUDIT_PHASES), _audit_phases(rows))
    _check(
        cases, "fault-not-sent-is-recorded-as-a-non-attempt",
        ["allowed", "attempted", "not_sent", "failed"], _audit_outcomes(rows),
    )
    _check(cases, "fault-not-sent-audits-no-unknown-outcome", False, "outcome_unknown" in [
        str(row["error_code"]) for row in rows
    ])
    # The read-back needs the proxy listening again: the refusal closed its data port.
    reopened = await faults.arm("none")
    _check(cases, "fault-not-sent-proxy-reopened-its-listener", True, reopened.get("listening"))
    _check(
        cases, "fault-not-sent-changes-nothing", signature,
        await agent.signature(resource_type, allowlisted),
    )
    reread = await agent.get(resource_type, allowlisted)
    _check(cases, "fault-not-sent-service-still-usable", True, isinstance(reread.get("signature"), str))

    # ------------------------------------------------ dropped while the body is written
    signature = await agent.signature(resource_type, allowlisted)
    writes_before = await faults.counter("method_PUT")
    hop = await faults.mark()
    mark = evidence.audit_mark()
    await faults.arm(FAULT_DROP_MID_BODY, **RESOURCE_WRITE_ROUTE)
    result = await _fault_update(
        agent, resource_type, allowlisted, signature, retention_days=42, description=description,
    )
    code = _refusal_code(result)
    observations["midBodyCode"] = code
    _check(cases, "fault-mid-body-is-not-attributed-to-this-call", "conflict", code)
    entry = await faults.faulted(FAULT_DROP_MID_BODY, after=hop)
    observations["midBodyHop"] = entry
    _check(
        cases, "fault-mid-body-never-reaches-the-gateway",
        {"forwarded": False, "dropped": True},
        {"forwarded": (entry or {}).get("forwarded"), "dropped": (entry or {}).get("dropped")},
    )
    _check(cases, "fault-mid-body-attempts-the-write-once", 1,
           await faults.counter("method_PUT") - writes_before)
    _check(
        cases, "fault-mid-body-changes-nothing", signature,
        await agent.signature(resource_type, allowlisted),
    )
    rows = evidence.audit_rows(mark, UPDATE_TOOL)
    observations["midBodyAudit"] = rows
    _check(cases, "fault-mid-body-audits-the-attempt", list(AUDIT_PHASES), _audit_phases(rows))
    _check(
        cases, "fault-mid-body-audits-not-applied",
        ["allowed", "attempted", "not_applied", "failed"], _audit_outcomes(rows),
    )
    _check(cases, "fault-mid-body-audits-the-refusal", "conflict",
           str(rows[2]["error_code"]) if len(rows) > 2 else None)

    # -------------------------------------- dropped after the body, before the answer
    signature = await agent.signature(resource_type, allowlisted)
    hop = await faults.mark()
    mark = evidence.audit_mark()
    await faults.arm(FAULT_DROP_AFTER_BODY, **RESOURCE_WRITE_ROUTE)
    result = await _fault_update(
        agent, resource_type, allowlisted, signature, retention_days=43, description=description,
    )
    code = _refusal_code(result)
    observations["afterBodyCode"] = code
    _check(cases, "fault-after-full-body-is-ambiguous", "outcome_unknown", code)
    entry = await faults.faulted(FAULT_DROP_AFTER_BODY, after=hop)
    observations["afterBodyHop"] = entry
    _check(
        cases, "fault-after-full-body-reaches-the-gateway",
        {"forwarded": True, "bodyComplete": True},
        {"forwarded": (entry or {}).get("forwarded"), "bodyComplete": (entry or {}).get("bodyComplete")},
    )
    _check(
        cases, "fault-after-full-body-was-answered-before-the-drop", True,
        int((entry or {}).get("responseBytes", 0)) > 0,
    )
    landed = await agent.get(resource_type, allowlisted)
    observations["afterBodyRetention"] = _retention(landed)
    _check(cases, "fault-after-full-body-applied-the-change", 43, _retention(landed))
    _check(cases, "fault-after-full-body-is-never-a-success", True, bool(result.get("isError")))
    rows = evidence.audit_rows(mark, UPDATE_TOOL)
    observations["afterBodyAudit"] = rows
    _check(cases, "fault-after-full-body-audits-the-attempt", list(AUDIT_PHASES), _audit_phases(rows))
    _check(
        cases, "fault-after-full-body-audits-an-unresolved-outcome",
        ["allowed", "attempted", "outcome_unknown", "outcome_unknown"], _audit_outcomes(rows),
    )

    # ------------------------------------------------------- delayed past the deadline
    signature = await agent.signature(resource_type, allowlisted)
    writes_before = await faults.counter("method_PUT")
    mark = evidence.audit_mark()
    delay = tool_timeout_seconds + 10.0
    await faults.arm(FAULT_DELAY_RESPONSE, delaySeconds=delay, **RESOURCE_WRITE_ROUTE)
    started = time.monotonic()
    result = await _fault_update(
        agent, resource_type, allowlisted, signature, retention_days=44, description=description,
    )
    elapsed = time.monotonic() - started
    code = _refusal_code(result)
    observations["deadlineCode"] = code
    observations["deadlineElapsedSeconds"] = round(elapsed, 3)
    _check(cases, "fault-deadline-is-a-timeout", "timeout", code)
    _check(
        cases, "fault-deadline-fires-at-the-deployment-deadline", True,
        elapsed >= tool_timeout_seconds * 0.9 and elapsed < delay - 5.0,
    )
    _check(cases, "fault-deadline-never-retries", 1,
           await faults.counter("method_PUT") - writes_before)
    rows = evidence.audit_rows(mark, UPDATE_TOOL)
    observations["deadlineAudit"] = rows
    _check(cases, "fault-deadline-audits-the-attempt", list(AUDIT_PHASES), _audit_phases(rows))
    _check(
        cases, "fault-deadline-audits-a-cancelled-dispatch",
        ["allowed", "attempted", "cancelled", "failed"], _audit_outcomes(rows),
    )
    _check(
        cases, "fault-deadline-audits-the-unresolved-boundary", "outcome_unknown",
        str(rows[2]["error_code"]) if len(rows) > 2 else None,
    )
    _check(
        cases, "fault-deadline-audits-its-target", f"{resource_type}/{allowlisted}",
        str(rows[2]["target_id"]) if len(rows) > 2 else None,
    )
    _check(
        cases, "fault-deadline-audits-the-code-the-caller-saw", "timeout",
        str(rows[-1]["error_code"]) if rows else None,
    )
    recovered = await agent.get(resource_type, allowlisted)
    observations["deadlineRetention"] = _retention(recovered)
    # The proxy forwarded the complete body before it held the answer back, so the
    # Gateway applied the change and the caller's timeout says nothing about that: this
    # is why the audit row is a cancelled dispatch with an unknown outcome, and why a
    # timeout on a Mutation is never a licence to replay it.
    landed = await _await_retention(agent, resource_type, allowlisted, 44)
    observations["deadlineRetentionSettled"] = landed
    _check(cases, "fault-deadline-left-a-possibly-applied-change", 44, landed)
    _check(
        cases, "fault-deadline-leaves-a-usable-service", True,
        isinstance(recovered.get("signature"), str),
    )
    follow_up = await _fault_update(
        agent, resource_type, allowlisted, str(recovered.get("signature")),
        retention_days=45, description=description,
    )
    _check(cases, "fault-deadline-allows-a-fresh-token-afterwards", True, not follow_up.get("isError"))

    # ------------------------------------------------------------- cancellation (D23)
    signature = await agent.signature(resource_type, allowlisted)
    writes_before = await faults.counter("method_PUT")
    hop = await faults.mark()
    mark = evidence.audit_mark()
    await faults.arm(FAULT_DELAY_RESPONSE, delaySeconds=60.0, **RESOURCE_WRITE_ROUTE)
    call = asyncio.create_task(agent.call_raw(UPDATE_TOOL, {
        "resourceType": resource_type, "name": allowlisted, "expectedSignature": signature,
        "description": description,
        "config": {"profile": {"type": "local", "retentionDays": 46}},
    }))
    entry = await faults.wait_forwarded(FAULT_DELAY_RESPONSE, after=hop, timeout=20.0)
    observations["cancelledHop"] = entry
    request_id = await agent.cancel_in_flight()
    observations["cancelledRequestId"] = request_id
    identifier, decoded = await asyncio.wait_for(call, timeout=60.0)
    error = decoded.get("error") if isinstance(decoded, dict) else None
    observations["cancelledOutcome"] = error if error is not None else decoded
    _check(
        cases, "fault-cancellation-answers-request-cancelled", REQUEST_CANCELLED,
        (error or {}).get("code") if isinstance(error, dict) else None,
    )
    _check(cases, "fault-cancellation-cancels-the-request-it-sent", request_id, identifier)
    _check(cases, "fault-cancellation-never-retries", 1,
           await faults.counter("method_PUT") - writes_before)
    rows = evidence.audit_rows(mark, UPDATE_TOOL)
    observations["cancelledAudit"] = rows
    _check(cases, "fault-cancellation-audits-the-attempt", list(AUDIT_PHASES), _audit_phases(rows))
    _check(
        cases, "fault-cancellation-audits-a-cancelled-dispatch",
        ["allowed", "attempted", "cancelled", "cancelled"], _audit_outcomes(rows),
    )
    _check(
        cases, "fault-cancellation-audits-the-unresolved-boundary", "outcome_unknown",
        str(rows[2]["error_code"]) if len(rows) > 2 else None,
    )
    _check(
        cases, "fault-cancellation-audits-its-target", f"{resource_type}/{allowlisted}",
        str(rows[2]["target_id"]) if len(rows) > 2 else None,
    )
    after_cancel = await agent.get(resource_type, allowlisted)
    observations["cancelledRetention"] = _retention(after_cancel)
    observations["cancelledSignatureMoved"] = after_cancel.get("signature") != signature
    # The dispatch was in flight when the client cancelled, so the Gateway applied the
    # change and the caller learned nothing: that is exactly why the audit row says the
    # outcome is unknown rather than recording a non-attempt. Poll, because the Gateway
    # may still have been applying it when the cancellation settled the call.
    landed = await _await_retention(agent, resource_type, allowlisted, 46)
    observations["cancelledRetentionSettled"] = landed
    _check(cases, "fault-cancellation-leaves-the-change-in-place", 46, landed)
    return cases, observations


async def fault_import_cases(
    *, agent: "Session", rest_url: str, agent_token: str, project: str, faults: ProxyFaults,
    evidence: Evidence, raw_dir: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The same transport faults against ``project_import`` (ticket #20).

    The D16 transaction makes each case state a different thing: a known non-attempt
    ends the transaction ``NOT_APPLIED`` with ``importDispatched=false``, a body that
    never completed is ``NOT_APPLIED`` on the read-back, an ambiguity whose candidate is
    demonstrably in place is the D16 reconciliation (``COMMITTED``), and a dispatch cut
    off by a cancellation is left to the reconcile loop, which may never call it a
    success.
    """

    cases: list[dict[str, Any]] = []
    observations: dict[str, Any] = {}
    artifacts = Artifacts(rest_url)

    async def baseline_and_candidate(marker: str) -> tuple[str, str, bytes]:
        exported = structured(await agent.call("project_export", {"projectName": project}))
        fingerprint = str(exported["fingerprint"])
        archive = await artifacts.download(str(exported["artifact"]["download"]["path"]), agent_token)
        candidate_bytes = _edited_candidate(archive, marker)
        candidate = await artifacts.upload(candidate_bytes, agent_token, label=marker)
        return fingerprint, candidate, candidate_bytes

    async def current_fingerprint(name: str, label: str) -> str:
        fresh = structured(await agent.call("project_export", {"projectName": name}))
        return _fingerprint(
            await artifacts.download(str(fresh["artifact"]["download"]["path"]), agent_token),
            raw_dir, label,
        )

    try:
        # ------------------------------------------- the hop is gone before the call (D23)
        # D23's first External failure is "Gateway unreachable": the transaction fails
        # during its baseline export, before it can reach the dispatch at all, and the
        # audit must hold no attempt because no write was ever attempted.
        baseline, candidate, _ = await baseline_and_candidate("fault-unreachable")
        txn_mark = evidence.transaction_mark()
        await faults.arm(FAULT_CONNECT_REFUSED)
        mark = evidence.audit_mark()
        result = await agent.call(IMPORT_TOOL, {
            "projectName": project, "artifactId": candidate, "expectedFingerprint": baseline,
        })
        code = _refusal_code(result)
        observations["unreachableImportCode"] = code
        _check(cases, "fault-import-unreachable-hop-is-gateway-unavailable",
               "gateway_unavailable", code)
        rows = evidence.audit_rows(mark, IMPORT_TOOL)
        observations["unreachableImportAudit"] = rows
        _check(cases, "fault-import-unreachable-hop-dispatches-nothing", False,
               any(str(row["phase"]) == "attempt" for row in rows))
        # The hop is also gone for the read the transaction does before it inserts its row,
        # so the refusal lands before any transaction exists: a row here would mean the
        # call had already started one.
        row = evidence.transaction(project, after=txn_mark)
        observations["unreachableTransaction"] = row
        _check(cases, "fault-import-unreachable-hop-starts-no-transaction", None, row)
        await faults.arm("none")
        _check(
            cases, "fault-import-unreachable-hop-changes-nothing", baseline,
            await current_fingerprint(project, "fault-unreachable-reexport.zip"),
        )

        # ------------------------------------------------ a known non-attempt (D08)
        # The transaction exports a baseline and re-exports before it dispatches, so the
        # refused connect has to be aimed at the dispatch: the proxy answers those two
        # exports and then takes its data listener away. The case is self-checking — a
        # refusal that landed on an export would leave no attempt in the audit at all.
        baseline, candidate, _ = await baseline_and_candidate("fault-not-sent")
        posts_before = await faults.counter("method_POST")
        txn_mark = evidence.transaction_mark()
        await faults.arm(
            FAULT_REFUSE_AFTER_FORWARD, afterCount=IMPORT_PRE_DISPATCH_EXPORTS,
            **IMPORT_EXPORT_ROUTE,
        )
        mark = evidence.audit_mark()
        result = await agent.call(IMPORT_TOOL, {
            "projectName": project, "artifactId": candidate, "expectedFingerprint": baseline,
        })
        code = _refusal_code(result)
        observations["notSentImportCode"] = code
        _check(cases, "fault-import-not-sent-is-a-known-non-attempt", "gateway_unavailable", code)
        state_after = await faults.state()
        observations["notSentImportProxy"] = state_after
        _check(
            cases, "fault-import-not-sent-closed-the-hop-after-the-pre-dispatch-reads", False,
            state_after.get("listening"),
        )
        _check(
            cases, "fault-import-not-sent-refused-the-dispatch-connect", 0,
            int(state_after.get("counters", {}).get("method_POST", 0)) - posts_before,
        )
        row = evidence.transaction(project, after=txn_mark)
        observations["notSentTransaction"] = row
        # A refused connect is a known non-attempt: nothing was dispatched, and the row
        # says so. Its *state* is FAILED_PRE_IMPORT rather than NOT_APPLIED because this
        # failure takes the hop away for the whole call, and the D16 drift re-export that
        # finalizes a NOT_APPLIED row needs the same hop — so the transaction cannot
        # finalize and ends in the other release-set state (no recovery lock either way).
        # Both facts the case is about are on the row: no dispatch, and the boundary.
        _check(cases, "fault-import-not-sent-dispatched-nothing", 0, (row or {}).get("import_dispatched"))
        _check(cases, "fault-import-not-sent-records-the-boundary", "not_sent",
               (row or {}).get("import_outcome"))
        # The state names the failure phase, and it is FAILED_PRE_IMPORT rather than
        # NOT_APPLIED because the hop stays down for the whole call: D16 finalizes
        # NOT_APPLIED only after its drift re-export, which needs the same hop. Still a
        # release-set state — no recovery lock, no replay — with `importDispatched: false`
        # and the boundary above on the row.
        _check(cases, "fault-import-not-sent-finalizes-as-a-release-set-state",
               "FAILED_PRE_IMPORT", (row or {}).get("state"))
        _check(cases, "fault-import-not-sent-reports-the-transport-error", "gateway_unavailable",
               (row or {}).get("error_code"))
        rows = evidence.audit_rows(mark, IMPORT_TOOL)
        observations["notSentImportAudit"] = rows
        _check(cases, "fault-import-not-sent-audits-the-attempt", list(AUDIT_PHASES), _audit_phases(rows))
        _check(
            cases, "fault-import-not-sent-audits-the-non-attempt",
            ["allowed", "attempted", "not_sent", "failed"], _audit_outcomes(rows),
        )
        # The read-back needs the proxy listening again: the refusal closed its data port.
        await faults.arm("none")
        _check(
            cases, "fault-import-not-sent-changes-nothing", baseline,
            await current_fingerprint(project, "fault-not-sent-reexport.zip"),
        )

        # ------------------------------------------------ dropped while the body is written
        baseline, candidate, _ = await baseline_and_candidate("fault-mid-body")
        posts_before = await faults.counter("method_POST")
        txn_mark = evidence.transaction_mark()
        mark = evidence.audit_mark()
        await faults.arm(FAULT_DROP_MID_BODY, **IMPORT_WRITE_ROUTE)
        result = await agent.call(IMPORT_TOOL, {
            "projectName": project, "artifactId": candidate, "expectedFingerprint": baseline,
        })
        code = _refusal_code(result)
        observations["midBodyImportCode"] = code
        _check(cases, "fault-import-mid-body-is-not-attributed", "conflict", code)
        entry = await faults.faulted(FAULT_DROP_MID_BODY)
        observations["midBodyImportHop"] = entry
        _check(
            cases, "fault-import-mid-body-never-reaches-the-gateway", False,
            bool((entry or {}).get("forwarded")),
        )
        _check(cases, "fault-import-mid-body-attempts-once", 1,
               await faults.counter("method_POST") - posts_before)
        _check(
            cases, "fault-import-mid-body-changes-nothing", baseline,
            await current_fingerprint(project, "fault-mid-body-reexport.zip"),
        )
        row = evidence.transaction(project, after=txn_mark)
        observations["midBodyTransaction"] = row
        _check(cases, "fault-import-mid-body-ends-not-applied", "NOT_APPLIED", (row or {}).get("state"))
        _check(
            cases, "fault-import-mid-body-records-an-ambiguous-boundary", True,
            str((row or {}).get("import_outcome")) in AMBIGUOUS_BOUNDARIES,
        )
        rows = evidence.audit_rows(mark, IMPORT_TOOL)
        observations["midBodyImportAudit"] = rows
        _check(cases, "fault-import-mid-body-audits-the-attempt", list(AUDIT_PHASES), _audit_phases(rows))
        _check(
            cases, "fault-import-mid-body-audits-not-applied",
            ["allowed", "attempted", "not_applied", "failed"], _audit_outcomes(rows),
        )

        # ------------------------------- dropped after the body: D16 reconciliation
        baseline, candidate, _ = await baseline_and_candidate("fault-after-body")
        txn_mark = evidence.transaction_mark()
        mark = evidence.audit_mark()
        await faults.arm(FAULT_DROP_AFTER_BODY, **IMPORT_WRITE_ROUTE)
        result = await agent.call(IMPORT_TOOL, {
            "projectName": project, "artifactId": candidate, "expectedFingerprint": baseline,
        })
        body = result.get("structuredContent") if not result.get("isError") else None
        observations["afterBodyImportResult"] = body if isinstance(body, dict) else _refusal_code(result)
        _check(cases, "fault-import-after-full-body-commits", "COMMITTED",
               (body or {}).get("state") if isinstance(body, dict) else None)
        _check(
            cases, "fault-import-after-full-body-reports-the-dispatch", True,
            (body or {}).get("importDispatched") if isinstance(body, dict) else None,
        )
        _check(
            cases, "fault-import-after-full-body-reconciles-its-candidate",
            (body or {}).get("candidateFingerprint") if isinstance(body, dict) else None,
            (body or {}).get("resultFingerprint") if isinstance(body, dict) else None,
        )
        independent = await current_fingerprint(project, "fault-after-body-reexport.zip")
        observations["afterBodyIndependentFingerprint"] = independent
        _check(cases, "fault-import-after-full-body-content-lands", independent,
               (body or {}).get("candidateFingerprint") if isinstance(body, dict) else None)
        entry = await faults.faulted(FAULT_DROP_AFTER_BODY)
        observations["afterBodyImportHop"] = entry
        _check(
            cases, "fault-import-after-full-body-reached-the-gateway",
            {"forwarded": True, "bodyComplete": True},
            {"forwarded": (entry or {}).get("forwarded"),
             "bodyComplete": (entry or {}).get("bodyComplete")},
        )
        row = evidence.transaction(project, after=txn_mark)
        observations["afterBodyTransaction"] = row
        _check(
            cases, "fault-import-after-full-body-records-the-boundary", True,
            str((row or {}).get("import_outcome")) in AMBIGUOUS_BOUNDARIES,
        )

        # ------------------------- a cancelled dispatch, settled by reconciliation
        baseline, candidate, _ = await baseline_and_candidate("fault-cancelled")
        posts_before = await faults.counter("method_POST")
        hop = await faults.mark()
        mark = evidence.audit_mark()
        await faults.arm(FAULT_DELAY_RESPONSE, delaySeconds=60.0, **IMPORT_WRITE_ROUTE)
        call = asyncio.create_task(agent.call_raw(IMPORT_TOOL, {
            "projectName": project, "artifactId": candidate, "expectedFingerprint": baseline,
        }))
        entry = await faults.wait_forwarded(FAULT_DELAY_RESPONSE, after=hop, timeout=30.0)
        observations["cancelledImportHop"] = entry
        request_id = await agent.cancel_in_flight()
        identifier, decoded = await asyncio.wait_for(call, timeout=60.0)
        error = decoded.get("error") if isinstance(decoded, dict) else None
        observations["cancelledImportOutcome"] = error if error is not None else decoded
        _check(
            cases, "fault-import-cancellation-answers-request-cancelled", REQUEST_CANCELLED,
            (error or {}).get("code") if isinstance(error, dict) else None,
        )
        _check(cases, "fault-import-cancellation-cancels-its-own-request", request_id, identifier)
        _check(cases, "fault-import-cancellation-never-retries", 1,
               await faults.counter("method_POST") - posts_before)
        rows = evidence.audit_rows(mark, IMPORT_TOOL)
        observations["cancelledImportAudit"] = rows
        _check(
            cases, "fault-import-cancellation-audits-the-attempt", list(AUDIT_PHASES),
            _audit_phases(rows),
        )
        _check(
            cases, "fault-import-cancellation-audits-a-cancelled-dispatch",
            ["allowed", "attempted", "cancelled", "cancelled"], _audit_outcomes(rows),
        )
        settled = await evidence.await_transaction_settled(project, after=txn_mark, timeout=60.0)
        observations["cancelledImportTransaction"] = settled
        _check(
            cases, "fault-import-cancellation-reconciles-without-a-replay", "OUTCOME_UNKNOWN",
            settled.get("state"),
        )
        _check(
            cases, "fault-import-cancellation-reconciles-to-an-unresolved-outcome", "outcome_unknown",
            settled.get("error_code"),
        )
    finally:
        await artifacts.aclose()
    return cases, observations


async def run_fault_mode(
    *, rest_url: str, agent_token: str, proxy_control_url: str, data_dir: Path,
    resource_type: str, allowlisted: str, project: str, tool_timeout_seconds: float, raw_dir: Path,
) -> dict[str, Any]:
    """D23's injected timeout, ambiguous outcome and cancellation, on a real Gateway.

    The server this mode drives has its Gateway URL pointed at the fault proxy, so the
    faults are injected in the hop and not in the server: the classification under test
    is the production one.
    """

    cases: list[dict[str, Any]] = []
    if not proxy_control_url:
        raise DriverError("--mode fault needs --proxy-control-url (the fault proxy's control port)")
    if data_dir is None:
        raise DriverError("--mode fault needs --data-dir (the running server's data directory)")
    observations: dict[str, Any] = {
        "proxyControlUrl": proxy_control_url,
        "dataDir": str(data_dir),
        "toolTimeoutSeconds": tool_timeout_seconds,
    }
    agent = Session(rest_url + "/mcp", agent_token, raw_dir)
    faults = ProxyFaults(proxy_control_url)
    evidence = Evidence(data_dir)
    try:
        await agent.initialize()
        update_cases, update_observations = await fault_update_cases(
            agent=agent, resource_type=resource_type, allowlisted=allowlisted, faults=faults,
            evidence=evidence, tool_timeout_seconds=tool_timeout_seconds, raw_dir=raw_dir,
        )
        cases.extend(update_cases)
        observations.update(update_observations)
        import_cases, import_observations = await fault_import_cases(
            agent=agent, rest_url=rest_url, agent_token=agent_token, project=project,
            faults=faults, evidence=evidence, raw_dir=raw_dir,
        )
        cases.extend(import_cases)
        observations.update(import_observations)
    finally:
        await faults.arm("none")
        await agent.aclose()
        await faults.aclose()
    return {"mode": "fault", "cases": cases, "observations": observations}


def _load_report(path: Path) -> dict[str, Any]:
    """The merged report, so the two modes of one live run land in one file."""

    if not path.is_file():
        return {"cases": [], "observations": {}, "modes": []}
    existing = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(existing, dict):
        raise DriverError(f"{path}: existing observations must be an object")
    return existing


async def _run(args: argparse.Namespace) -> int:
    if args.mode == "gate-on":
        mode = await run_gate_on(
            rest_url=args.rest_url, reader_token=args.reader_token, agent_token=args.agent_token,
            operator_token=args.operator_token,
            resource_type=args.resource_type, allowlisted=args.allowlisted,
            unallowlisted=args.unallowlisted, singleton_type=args.singleton_type,
            created_name=args.created_name, rename_source=args.rename_source,
            rename_source_2=args.rename_source_2, renamed_name=args.renamed_name,
            unallowlisted_renamed=args.unallowlisted_renamed,
            project=args.project, control_project=args.control_project,
            tag_provider=args.tag_provider, tag_source_path=args.tag_source_path,
            tag_target_path=args.tag_target_path, tag_control_path=args.tag_control_path,
            pipeline=args.pipeline, control_pipeline=args.control_pipeline,
            alarm_event_id=args.alarm_event_id,
            raw_dir=args.raw_dir,
        )
    elif args.mode == "fault":
        mode = await run_fault_mode(
            rest_url=args.rest_url, agent_token=args.agent_token,
            proxy_control_url=args.proxy_control_url, data_dir=args.data_dir,
            resource_type=args.resource_type, allowlisted=args.allowlisted, project=args.project,
            tool_timeout_seconds=args.tool_timeout_seconds, raw_dir=args.raw_dir,
        )
    else:
        mode = await run_gate_off(
            rest_url=args.rest_url, agent_token=args.agent_token, raw_dir=args.raw_dir,
        )

    report = _load_report(args.observations)
    report.update({"schemaVersion": 1, "gate": "G4", "milestone": "4c", "tool": UPDATE_TOOL})
    report["modes"] = [*report.get("modes", []), args.mode]
    report["cases"] = [*report.get("cases", []), *mode["cases"]]
    report["observations"] = {**report.get("observations", {}), **mode["observations"]}
    report["passed"] = all(case["ok"] for case in report["cases"])
    _write(args.observations, report)
    for case in report["cases"]:
        print(f"{'ok  ' if case['ok'] else 'FAIL'} {case['case']}: {json.dumps(case['observed'])[:160]}")
    return 0 if report["passed"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("gate-on", "gate-off", "fault"), required=True)
    parser.add_argument("--rest-url", default="http://127.0.0.1:8765")
    parser.add_argument("--reader-token", default="")
    parser.add_argument("--agent-token", required=True)
    #: The CONTROL credential: the cancel Tool requires `ignition.control`, so the run
    #: proves scope-by-effect with a credential that has it and one that does not.
    parser.add_argument("--operator-token", default="")
    parser.add_argument("--resource-type", default="ignition/audit-profile")
    parser.add_argument("--allowlisted", default="MCP_CI_AUDIT")
    parser.add_argument("--unallowlisted", default="MCP_CI_AUDIT_OTHER")
    parser.add_argument("--singleton-type", default=SINGLETON_TYPE)
    parser.add_argument("--created-name", default=CREATED_RESOURCE)
    parser.add_argument("--rename-source", default=RENAME_SOURCE)
    parser.add_argument("--rename-source-2", default=RENAME_SOURCE_2)
    parser.add_argument("--renamed-name", default=RENAMED_RESOURCE)
    parser.add_argument("--unallowlisted-renamed", default=UNALLOWLISTED_RENAMED)
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--control-project", default=DEFAULT_CONTROL_PROJECT)
    parser.add_argument("--tag-provider", default=DEFAULT_TAG_PROVIDER)
    parser.add_argument("--tag-source-path", default=DEFAULT_TAG_SOURCE_PATH)
    parser.add_argument("--tag-target-path", default=DEFAULT_TAG_TARGET_PATH)
    parser.add_argument("--tag-control-path", default=DEFAULT_TAG_CONTROL_PATH)
    parser.add_argument("--pipeline", default=DEFAULT_PIPELINE)
    parser.add_argument("--control-pipeline", default=DEFAULT_CONTROL_PIPELINE)
    parser.add_argument("--alarm-event-id", default=DEFAULT_ALARM_EVENT_ID)
    #: The fault mode (#20): the proxy it arms and the server's own data directory, which
    #: holds the D18 audit log and the D16 transaction rows the cases read back.
    parser.add_argument("--proxy-control-url", default="")
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--tool-timeout-seconds", type=float, default=0.0)
    parser.add_argument("--observations", required=True, type=Path)
    parser.add_argument("--raw-dir", required=True, type=Path)
    args = parser.parse_args()
    args.rest_url = args.rest_url.rstrip("/")
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
