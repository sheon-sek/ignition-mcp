#!/usr/bin/env python3
"""Test-only provisioning for the Phase 4 REST mutation harness.

This is a harness, not a second installer (the same rule the Phase 0-2
provisioning harness follows): it prepares the disposable config resources the
live cases need on a CI-owned Gateway, through the Gateway's own Native REST API.

It provisions four ``ignition/audit-profile`` resources:

- ``MCP_CI_AUDIT`` — the Target the harness's update allowlist names;
- ``MCP_CI_AUDIT_OTHER`` — a second resource of the same type that the allowlists do
  *not* name, so the Target-allowlist refusal is exercised live;
- ``MCP_CI_AUDIT_RENAME_SOURCE`` and ``MCP_CI_AUDIT_RENAME_SOURCE_2`` — the rename
  sources, so the rename cases do not depend on the create Tool working first.

All are created without a signature (creation takes no Precondition token) and are
read back with a signature, which is the proof that the live cases have a real
Precondition token to work with. The name the create case publishes is deliberately
*not* provisioned. Re-running is safe: an existing resource is left alone.

It also creates the three Tag-provider *config resources* D30 owner ruling 4 (ticket #36)
is about: the reserved `IgnitionMCPPolicy` provider — the resource the Runtime Target
Policy lives in — an ordinary provider, and a longer name that only begins with the
reserved one. Each is read back for its Resource signature, so the reserved-name cases can
show that a refused call left the reserved resource exactly as it was, and the block
records how this Gateway answers a read of the reserved name with its case folded
(Ignition documents no rule for it, so the Tool folds case and fails closed).

It also confirms the two disposable Projects the ``project_import`` cases address
(installed into the Gateway's data directory by the workflow, which is why this
harness cannot create them itself): the Target the Project-import allowlist names, and
a second existing Project the allowlist deliberately does not name. A missing Project
fails here, before a single live case runs.

Phase 5 adds the Perspective fixture: a parent Project and an allowlisted child Project,
both run-unique, imported through the Gateway's own project route. The parent defines one
View the child does not define locally, so the child *inherits* it; the child has one
local View, a Page configuration, Session properties, and one unrelated resource. Both
are read back through the export route the Perspective Tools themselves read, so a
missing Project, a View the Gateway dropped, or a child that no longer inherits fails
here rather than in the middle of a live case. The same block records whether the
Perspective module is loaded, which is what makes those entries meaningful at all.
"""

from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import zipfile

RESOURCE_TYPE = "ignition/audit-profile"
#: An allowed singleton target (the documented change item carries no name).
SINGLETON_TYPE = "ignition/cobranding"
COLLECTION_PATH = f"/data/api/v1/resources/{RESOURCE_TYPE}"
#: D30 owner ruling 5: the collection every config Mutation addresses, named on every
#: read and write this harness makes so the provisioned resources sit where the Tools
#: look for them rather than in whatever the Gateway calls its default.
COLLECTION = "core"
FIND_PATH = f"/data/api/v1/resources/find/{RESOURCE_TYPE}/"
RENAME_PATH = f"/data/api/v1/resources/rename/{RESOURCE_TYPE}/"
ALLOWLISTED = "MCP_CI_AUDIT"
UNALLOWLISTED = "MCP_CI_AUDIT_OTHER"
RENAME_SOURCES = ("MCP_CI_AUDIT_RENAME_SOURCE", "MCP_CI_AUDIT_RENAME_SOURCE_2")
#: The Tag surface the #17 cases need: a disposable provider, the source Tags it is
#: provisioned with, and the paths the driver exports from and imports into.
TAG_PROVIDER_PATH = "/data/api/v1/resources/ignition/tag-provider"
TAG_PROVIDER_FIND_PATH = "/data/api/v1/resources/find/ignition/tag-provider/"
TAG_IMPORT_PATH = "/data/api/v1/tags/import"
TAG_EXPORT_PATH = "/data/api/v1/tags/export"
TAG_PROVIDER_READY_DEADLINE_SECONDS = 180.0
TAG_IMPORT_ATTEMPTS = 6

#: Phase 4 ticket #36 (D30 owner ruling 4): the Tag-provider *config resources* the
#: reserved-name cases address. `IgnitionMCPPolicy` is the provider the Runtime Target
#: Policy lives in and is refused by name; the other two prove the refusal is by name
#: inside an *allowed* type — one is a different provider, the other begins with the
#: reserved name but is a different resource.
POLICY_PROVIDER = "IgnitionMCPPolicy"
OTHER_PROVIDER = "MCP_CI_TAG_CONFIG"
LOOKALIKE_PROVIDER = "IgnitionMCPPolicyStaging"
PROVIDER_RESOURCES = (POLICY_PROVIDER, OTHER_PROVIDER, LOOKALIKE_PROVIDER)
PROVIDER_RENAME_PATH = "/data/api/v1/resources/rename/ignition/tag-provider/"
#: The Tag names the source document holds, which the live cases assert are served at
#: the destination afterwards.
TAG_SOURCE_NAMES = ("Folder", "Int", "Inner", "Text", "Sibling")
#: The throwaway paths the import-convention probe imports into: one for a document
#: whose root names its own node (the export of a sub-path) and one for a
#: provider-root document, which names nothing.
TAG_PROBE_PATH = "convention_probe_named"
TAG_PROBE_NAMELESS_PATH = "convention_probe_flat"

#: Phase 4 ticket #18: the documented Alarm Notification Pipeline runtime route. The
#: cancel and the bounded status read the Tool verifies with are the same path, and both
#: must be documented for the harness to provision a Gateway the Tool can run against.
PIPELINE_PATH = "/data/alarm-notification/api/v1/pipeline"

#: Phase 5 (P5-3): the Perspective fixture. Confirmed live on 8.3.8 (b2026071409), whose
#: module parses these exact resources: ``PerspectiveProjectCacheImpl.parseViews``
#: resolves ``views/<Logical path>`` through ``ViewConfig.fromProjectResource``,
#: ``PagesConfig.fromResource`` reads the ``page-config`` resource's ``config.json`` and
#: ``SessionPropsConfig.fromProjectResource`` the ``session-props`` resource's
#: ``props.json``. Each of the three resource kinds also carries a ``resource.json``
#: beside its data file: the Gateway's project import keeps an entry only when the
#: resource metadata declares it, so a bare data file is dropped.
PERSPECTIVE_MODULE = "com.inductiveautomation.perspective"
PERSPECTIVE_RESOURCE_METADATA = "resource.json"
#: The two Views the fixture addresses: the parent's (the child inherits it, so a write
#: to it in the child is the D15 refusal) and the child's own. The child has no local
#: View at the inherited path, which is the whole point of the pair.
INHERITED_VIEW = "Pages/Inherited"
LOCAL_VIEW = "Dashboard/Overview"
#: The child's unrelated resource: the Project's content outside the Perspective module,
#: which the preservation case compares across an edit. A named query is stored verbatim,
#: which is what makes a byte comparison meaningful.
UNRELATED_QUERY = "ignition/named-query/mcp_p5_probe/query.sql"
#: The documented routes of the fixture: the project import (which creates a Project that
#: does not exist yet) and its export, which is how the checks read the Projects back.
PROJECT_IMPORT_PATH = "/data/api/v1/projects/import/{name}"
PROJECT_EXPORT_PATH = "/data/api/v1/projects/export/{name}"
#: The import creates a Project that does not exist and replaces one that does: the
#: harness re-provisions the same run-unique names on a retry, so the overwrite flag
#: keeps provisioning idempotent instead of answering a 409 "name already in use".
PROJECT_IMPORT_OVERWRITE = "?overwrite=true"
#: A route the Perspective module itself registers. It is the REST-visible proof that the
#: module is loaded: a Gateway without Perspective answers no such path.
PERSPECTIVE_MODULE_ROUTE = ("GET", "/data/perspective/api/v1/sessions/")

REQUIRED_ENDPOINTS = frozenset({
    ("GET", f"/data/api/v1/resources/type/{RESOURCE_TYPE}"),
    ("GET", f"/data/api/v1/resources/find/{RESOURCE_TYPE}/{{name}}"),
    ("POST", COLLECTION_PATH),
    ("PUT", COLLECTION_PATH),
    ("DELETE", f"{COLLECTION_PATH}/{{name}}/{{signature}}"),
    ("POST", f"{RENAME_PATH}{{name}}"),
    ("GET", f"/data/api/v1/resources/singleton/{SINGLETON_TYPE}"),
    ("PUT", f"/data/api/v1/resources/{SINGLETON_TYPE}"),
    ("DELETE", f"/data/api/v1/resources/{SINGLETON_TYPE}/{{signature}}"),
    ("POST", TAG_PROVIDER_PATH),
    ("POST", TAG_IMPORT_PATH),
    ("GET", TAG_EXPORT_PATH),
    # Phase 4 ticket #36: the Tag-provider *config resource* through the generic config
    # Mutations. The reserved-name refusal is decided from the Target alone, but the
    # capability snapshot withholds the update/delete/rename route of a type whose route
    # is not documented — and a type without a route never reaches the refusal — so the
    # harness must not run on a Gateway that lacks them.
    ("GET", f"{TAG_PROVIDER_FIND_PATH}{{name}}"),
    ("PUT", TAG_PROVIDER_PATH),
    ("DELETE", f"{TAG_PROVIDER_PATH}/{{name}}/{{signature}}"),
    ("POST", f"{PROVIDER_RENAME_PATH}{{name}}"),
    # Phase 4 ticket #18: the cancel Tool needs both documented pipeline routes — the
    # status read it verifies with and the cancel itself — or the harness must not spend
    # a live run on a Gateway that cannot expose it.
    ("GET", PIPELINE_PATH),
    ("DELETE", PIPELINE_PATH),
    # Phase 5 (P5-3): the Perspective fixture is imported and read back over the
    # documented project routes, and the module's own route is what proves the module is
    # loaded. A Gateway without it answers no such path, and every fixture import would
    # land as a Project with no content.
    ("POST", PROJECT_IMPORT_PATH),
    ("GET", PROJECT_EXPORT_PATH),
    PERSPECTIVE_MODULE_ROUTE,
})
MAX_RESPONSE_BYTES = 1_048_576
MAX_OPENAPI_BYTES = 16 * 1_048_576
PROJECT_LIST_PATH = "/data/api/v1/projects/list"



class ProvisionError(RuntimeError):
    pass


def _request(
    base_url: str,
    token: str,
    method: str,
    path: str,
    *,
    body: bytes | None = None,
    allowed_error_statuses: frozenset[int] = frozenset(),
    max_response_bytes: int = MAX_RESPONSE_BYTES,
    timeout: float = 20.0,
    content_type: str = "application/json",
) -> tuple[int, Any]:
    url = base_url.rstrip("/") + path
    headers = {"X-Ignition-API-Token": token, "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = content_type
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - loopback CI Gateway
            payload = response.read(max_response_bytes + 1)
            status = response.status
    except HTTPError as error:
        payload = error.read(max_response_bytes + 1)
        status = error.code
        if status not in allowed_error_statuses:
            raise ProvisionError(f"{method} {path} returned HTTP {status}: {payload[:400]!r}") from error
    except (URLError, TimeoutError, OSError) as error:
        raise ProvisionError(f"{method} {path} failed: {type(error).__name__}: {error}") from error
    if len(payload) > max_response_bytes:
        raise ProvisionError(f"{method} {path} response exceeded the bounded size")
    try:
        decoded: Any = json.loads(payload) if payload else {}
    except ValueError:
        decoded = payload[:400].decode("utf-8", errors="replace")
    return status, decoded


def _endpoint_inventory(document: Any) -> set[tuple[str, str]]:
    endpoints: set[tuple[str, str]] = set()
    paths = document.get("paths") if isinstance(document, dict) else None
    if not isinstance(paths, dict):
        raise ProvisionError("Gateway OpenAPI document has no paths object")
    for path, operations in paths.items():
        if not isinstance(path, str) or not isinstance(operations, dict):
            continue
        for method in operations:
            method_upper = str(method).upper()
            if method_upper in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                endpoints.add((method_upper, path))
    return endpoints


def await_required_endpoints(base_url: str, token: str, *, timeout: float = 180.0) -> dict[str, Any]:
    """The readiness gate: no fixture mutation before the routes exist."""

    deadline = time.monotonic() + timeout
    last_detail = "OpenAPI not requested"
    while time.monotonic() < deadline:
        try:
            status, payload = _request(
                base_url, token, "GET", "/openapi.json", max_response_bytes=MAX_OPENAPI_BYTES,
            )
            if status != 200:
                raise ProvisionError(f"Gateway OpenAPI returned unexpected HTTP {status}")
            endpoints = _endpoint_inventory(payload)
            missing = sorted(REQUIRED_ENDPOINTS - endpoints)
            if not missing:
                return {
                    "endpointCount": len(endpoints),
                    "requiredEndpoints": [
                        f"{method} {path}" for method, path in sorted(REQUIRED_ENDPOINTS)
                    ],
                }
            last_detail = "missing=" + ", ".join(f"{method} {path}" for method, path in missing)
        except ProvisionError as error:
            last_detail = str(error)
        time.sleep(3.0)
    raise ProvisionError(f"required OpenAPI endpoints never became available: {last_detail}")


def _create_profile(base_url: str, token: str, name: str, description: str) -> int:
    body = json.dumps([{
        "name": name,
        "collection": COLLECTION,
        "enabled": True,
        "description": description,
        "config": {"profile": {"type": "local"}, "settings": {}},
    }], separators=(",", ":")).encode("utf-8")
    status, _ = _request(
        base_url, token, "POST", COLLECTION_PATH, body=body, allowed_error_statuses=frozenset({409}),
    )
    if status not in {200, 201, 409}:
        raise ProvisionError(f"creating {RESOURCE_TYPE}/{name} returned unexpected HTTP {status}")
    return status


def _read_profile(base_url: str, token: str, name: str) -> dict[str, Any]:
    status, payload = _request(
        base_url, token, "GET", f"{FIND_PATH}{name}?collection={COLLECTION}",
        allowed_error_statuses=frozenset({404}),
    )
    if status != 200 or not isinstance(payload, dict):
        raise ProvisionError(f"reading {RESOURCE_TYPE}/{name} returned HTTP {status}")
    signature = payload.get("signature")
    if not isinstance(signature, str) or not signature:
        raise ProvisionError(f"{RESOURCE_TYPE}/{name} reports no Resource signature")
    return payload


def _project_names(base_url: str, token: str) -> set[str]:
    """Every Project name the Gateway lists, bounded to one page of 500."""

    status, payload = _request(
        base_url, token, "GET", PROJECT_LIST_PATH + "?limit=500&offset=0",
    )
    if status != 200 or not isinstance(payload, dict):
        raise ProvisionError(f"listing Projects returned HTTP {status}")
    items = payload.get("items")
    if not isinstance(items, list):
        raise ProvisionError("the Project listing has no items array")
    return {
        str(item["name"]) for item in items
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }


def provision_providers(base_url: str, token: str) -> dict[str, Any]:
    """The three Tag-provider config resources ticket #36 is about.

    They are ordinary `ignition/tag-provider` resources created through the Gateway's own
    Native REST API — the same route the deployment-owned `ignition-mcp setup` uses for the
    policy provider, and the same *type* D30 §5 keeps allowed. Creating
    ``IgnitionMCPPolicy`` here is deliberately part of the fixture: the cases must be able
    to show that a refused call left the policy's storage provider exactly as it was, which
    needs the resource to exist.

    The block also records how this Gateway treats the *case* of a resource name: the
    reserved name is looked up once with its case folded, and the status the Gateway
    answers is recorded. Ignition documents no rule for it, which is why the refusal itself
    folds case (fail-closed); this measurement is what a reader uses to judge the cost of
    that choice.
    """

    created: dict[str, Any] = {}
    for name in PROVIDER_RESOURCES:
        body = json.dumps([{
            "name": name,
            "collection": COLLECTION,
            "description": f"Disposable Phase 4 CI Tag-provider resource ({name})",
            "enabled": True,
            "config": {"profile": {"type": "STANDARD"}, "settings": {}},
        }], separators=(",", ":")).encode("utf-8")
        status, _ = _request(
            base_url, token, "POST", TAG_PROVIDER_PATH, body=body,
            allowed_error_statuses=frozenset({409}),
        )
        if status not in {200, 201, 409}:
            raise ProvisionError(
                f"creating the Tag-provider resource {name} returned unexpected HTTP {status}"
            )
        read_status, payload = _request(
            base_url, token, "GET", f"{TAG_PROVIDER_FIND_PATH}{name}?collection={COLLECTION}",
            allowed_error_statuses=frozenset({404}),
        )
        if read_status != 200 or not isinstance(payload, dict):
            raise ProvisionError(f"reading the Tag-provider resource {name} returned HTTP {read_status}")
        signature = payload.get("signature")
        if not isinstance(signature, str) or not signature:
            raise ProvisionError(f"the Tag-provider resource {name} reports no Resource signature")
        created[name] = {"createStatus": status, "signature": signature}
    folded_status, _ = _request(
        base_url, token, "GET",
        f"{TAG_PROVIDER_FIND_PATH}{POLICY_PROVIDER.lower()}?collection={COLLECTION}",
        allowed_error_statuses=frozenset({404}),
    )
    return {
        "resourceType": "ignition/tag-provider",
        "reserved": POLICY_PROVIDER,
        "resources": created,
        "caseFoldedLookup": {
            "name": POLICY_PROVIDER.lower(),
            "status": folded_status,
            "note": (
                "how this Gateway answers a read of the reserved resource's name with its "
                "case folded: 200 means the two names address one resource, 404 means they "
                "do not. The Tool folds case anyway (fail-closed), because Ignition "
                "documents no rule for it."
            ),
        },
    }


def _source_document() -> bytes:
    """The JSON Tag document the ``tag_config_import`` cases export and import.

    A folder with a nested folder and a sibling Tag, so the cases cover a created
    folder, a created leaf, a nested created leaf and a neighbouring Tag the import
    must leave exactly as it found it.
    """

    return json.dumps({"tags": [
        {"name": "Folder", "tagType": "Folder", "tags": [
            {"name": "Int", "tagType": "AtomicTag", "valueSource": "memory",
             "dataType": "Int4", "value": 7, "enabled": True},
            {"name": "Inner", "tagType": "Folder", "tags": [
                {"name": "Text", "tagType": "AtomicTag", "valueSource": "memory",
                 "dataType": "String", "value": "p4-rest", "enabled": True},
            ]},
        ]},
        {"name": "Sibling", "tagType": "AtomicTag", "valueSource": "memory",
         "dataType": "String", "value": "keep", "enabled": True},
    ]}, separators=(",", ":")).encode("utf-8")


def _tag_import_detail(payload: Any) -> str | None:
    """The failure one Tag import response reports, or ``None``.

    The committed 8.3.8 OpenAPI describes a list of non-Good QualityCodes while live
    8.3.8/8.3.9 answer a summary object; anything unrecognized counts as a failure, so
    provisioning fails closed.
    """

    if payload is None or payload == []:
        return None
    if isinstance(payload, list):
        return str(payload)
    if isinstance(payload, dict):
        failures = payload.get("failures")
        if payload.get("failureCount") == 0 and failures in (None, []):
            return None
    return str(payload)[:400]


def _tag_names(payload: Any) -> set[str]:
    names: set[str] = set()
    stack: list[Any] = [payload]
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


def _create_tag_provider(base_url: str, token: str, name: str) -> int:
    body = json.dumps([{
        "name": name,
        "description": "Disposable Phase 4 CI Tag provider",
        "enabled": True,
        "config": {"profile": {"type": "STANDARD"}, "settings": {}},
    }], separators=(",", ":")).encode("utf-8")
    status, _ = _request(
        base_url, token, "POST", TAG_PROVIDER_PATH, body=body,
        allowed_error_statuses=frozenset({409}),
    )
    if status not in {200, 201, 409}:
        raise ProvisionError(f"creating the Tag provider {name} returned unexpected HTTP {status}")
    return status


def _await_tag_provider(base_url: str, token: str, name: str) -> dict[str, Any]:
    """A freshly created provider answers 404 to both its own read and an export until
    it is running, so nothing may be imported before both answer 200."""

    deadline = time.monotonic() + TAG_PROVIDER_READY_DEADLINE_SECONDS
    attempts = 0
    last = ""
    while time.monotonic() < deadline:
        attempts += 1
        find_status, _ = _request(
            base_url, token, "GET", TAG_PROVIDER_FIND_PATH + name,
            allowed_error_statuses=frozenset({404, 500}),
        )
        export_status, _ = _request(
            base_url, token, "GET", f"{TAG_EXPORT_PATH}?provider={name}&type=json",
            allowed_error_statuses=frozenset({404, 500}),
        )
        if find_status == 200 and export_status == 200:
            return {"ready": True, "attempts": attempts}
        last = f"find={find_status} export={export_status}"
        time.sleep(2.0)
    raise ProvisionError(f"the Tag provider {name} never became readable ({last})")


def _import_document(
    base_url: str, token: str, provider: str, path: str, document: bytes,
) -> dict[str, Any]:
    """Import one Tag document, retrying the freshly-created-provider failure.

    The recorded 8.3.8 run showed a new Tag provider answering ``/tags/import`` with
    ``Bad 776 TagPath.getPathLength() ... cleanPath is null`` while it was still
    starting, so one attempt is not evidence of anything. ``MergeOverwrite`` is used
    here because this is the harness writing its own fixture, not the Tool: the Tool
    always sends ``Abort`` (D30 §4) and the driver is what exercises that.
    """

    last: dict[str, Any] = {}
    for attempt in range(1, TAG_IMPORT_ATTEMPTS + 1):
        status, payload = _request(
            base_url, token, "POST",
            f"{TAG_IMPORT_PATH}?provider={provider}&path={path}&type=json&collisionPolicy=MergeOverwrite",
            body=document, content_type="application/octet-stream",
            allowed_error_statuses=frozenset({500}),
        )
        detail = _tag_import_detail(payload) if status == 200 else f"HTTP {status}: {str(payload)[:200]}"
        last = {"attempts": attempt, "status": status, "failureDetail": detail}
        if detail is None:
            return last
        time.sleep(3.0)
    raise ProvisionError(f"importing the source Tags never succeeded: {last}")


def _import_source_tags(base_url: str, token: str, provider: str, path: str) -> dict[str, Any]:
    """Publish the source document at ``path``."""

    return _import_document(base_url, token, provider, path, _source_document())


def _exported_names(base_url: str, token: str, provider: str, path: str) -> set[str]:
    status, payload = _request(
        base_url, token, "GET", f"{TAG_EXPORT_PATH}?provider={provider}&path={path}&type=json",
        allowed_error_statuses=frozenset({404, 500}),
    )
    if status != 200:
        raise ProvisionError(f"exporting {provider}:{path} returned HTTP {status}")
    return _tag_names(payload)


def provision_tags(base_url: str, token: str, *, provider: str, source_path: str) -> dict[str, Any]:
    """The Tag surface the #17 cases address: a disposable provider holding the source
    Tags, and the proof that the Gateway serves them before any live case runs."""

    create_status = _create_tag_provider(base_url, token, provider)
    readiness = _await_tag_provider(base_url, token, provider)
    import_result = _import_source_tags(base_url, token, provider, source_path)
    served = _exported_names(base_url, token, provider, source_path)
    missing = sorted(set(TAG_SOURCE_NAMES) - served)
    if missing:
        raise ProvisionError(f"the source Tags are not served at {provider}:{source_path}: {missing}")
    return {
        "name": provider,
        "createStatus": create_status,
        "readiness": readiness,
        "sourcePath": source_path,
        "sourceTags": sorted(served),
        "import": import_result,
        "convention": probe_import_convention(base_url, token, provider=provider),
    }


def _provider_paths(payload: Any) -> list[str]:
    """Every provider-relative Tag path one *provider-root* export holds.

    Used only by the convention probe below: it reads an export the harness itself
    requested at the provider root, so the paths it returns are the paths the Gateway
    really serves — no assumption about the Tool's document rule is involved.
    """

    paths: list[str] = []

    def walk(nodes: Any, prefix: str) -> None:
        if not isinstance(nodes, list):
            return
        for node in nodes:
            if not isinstance(node, dict):
                continue
            name = node.get("name")
            if not isinstance(name, str) or not name:
                continue
            current = f"{prefix}/{name}" if prefix else name
            paths.append(current)
            walk(node.get("tags"), current)

    children = payload.get("tags") if isinstance(payload, dict) else None
    if isinstance(children, list):
        walk(children, "")
    return paths


def _probe_documents() -> dict[str, tuple[str, bytes]]:
    """The two document shapes the import convention is recorded for.

    A document exported from a *sub-path* names its own root
    (``{"name": "source", "tagType": "Folder", ...}``); one exported from the provider
    root names nothing and contributes its ``tags`` entries. The Tool decides which Tag
    paths an import declares, so where each shape lands is a live fact, not a
    preference, and every live row records it.
    """

    def node(name: str, value: str) -> dict[str, Any]:
        return {"name": name, "tagType": "AtomicTag", "valueSource": "memory",
                "dataType": "String", "value": value, "enabled": True}

    named = {"name": "Probe", "tagType": "Folder", "tags": [node("Leaf", "named")]}
    nameless = {"tags": [node("Flat", "nameless")]}
    return {
        "namedRoot": (TAG_PROBE_PATH, json.dumps(named, separators=(",", ":")).encode("utf-8")),
        "namelessRoot": (
            TAG_PROBE_NAMELESS_PATH, json.dumps(nameless, separators=(",", ":")).encode("utf-8"),
        ),
    }


def probe_import_convention(base_url: str, token: str, *, provider: str) -> dict[str, Any]:
    """Record where the Gateway puts each import document shape.

    Both probe documents are imported into their own throwaway path, then the *provider
    root* is exported once: the paths it serves say exactly what the Gateway created,
    without involving the Tool or any assumption about its document rule.
    """

    records: dict[str, Any] = {}
    for shape, (path, document) in _probe_documents().items():
        records[shape] = {
            "importPath": path,
            "import": _import_document(base_url, token, provider, path, document),
        }
    status, exported = _request(
        base_url, token, "GET", f"{TAG_EXPORT_PATH}?provider={provider}&type=json",
        allowed_error_statuses=frozenset({404, 500}),
    )
    paths = _provider_paths(exported) if status == 200 else []
    records["exportStatus"] = status
    records["providerPaths"] = sorted(paths)
    for shape, record in _probe_documents().items():
        prefix = record[0]
        records[shape]["pathsUnderImportPath"] = sorted(
            path for path in paths if path.split("/", 1)[0] == prefix
        )
    return records


# ------------------------------------------------------------------ Phase 5 (P5-3)
def _request_bytes(
    base_url: str, token: str, method: str, path: str, *,
    body: bytes | None = None, content_type: str = "application/json",
    allowed_error_statuses: frozenset[int] = frozenset(),
    max_response_bytes: int = 32 * 1_048_576, timeout: float = 60.0,
) -> tuple[int, bytes]:
    """One request whose answer is *bytes*: the project export is a ZIP archive."""

    url = base_url.rstrip("/") + path
    headers = {"X-Ignition-API-Token": token, "Accept": "*/*"}
    if body is not None:
        headers["Content-Type"] = content_type
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - loopback CI Gateway
            payload = response.read(max_response_bytes + 1)
            status = response.status
    except HTTPError as error:
        payload = error.read(max_response_bytes + 1)
        status = error.code
        if status not in allowed_error_statuses:
            raise ProvisionError(f"{method} {path} returned HTTP {status}: {payload[:400]!r}") from error
    except (URLError, TimeoutError, OSError) as error:
        raise ProvisionError(f"{method} {path} failed: {type(error).__name__}: {error}") from error
    if len(payload) > max_response_bytes:
        raise ProvisionError(f"{method} {path} response exceeded the bounded size")
    return status, payload


def resource_metadata(files: list[str]) -> bytes:
    """The ``resource.json`` one Perspective resource carries beside its data file.

    The Gateway's project import keeps an entry only when the resource metadata declares
    it, so a fixture (or a write) that publishes a bare data file publishes nothing.
    """

    return json.dumps({
        "scope": "G", "version": 1, "restricted": False, "overridable": True, "files": files,
    }).encode("utf-8")


def view_document(name: str) -> dict[str, Any]:
    """One small but well-formed Perspective View document."""

    return {
        "root": {
            "type": "ia.container.flex",
            "props": {"style": {"overflow": "auto"}},
            "children": [
                {
                    "type": "ia.display.label",
                    "meta": {"name": "Title"},
                    "position": {"basis": "auto"},
                    "props": {"text": name},
                },
            ],
        },
        "custom": {},
    }


#: The documents the fixture imports, one per resource. The driver compares what the
#: Perspective reads serve against exactly these, so a read that answers a plausible but
#: different document fails its case instead of passing on a path and a component type.
CHILD_VIEW_DOCUMENT: dict[str, Any] = view_document(f"{LOCAL_VIEW} (child)")
PARENT_VIEW_DOCUMENT: dict[str, Any] = view_document(f"{INHERITED_VIEW} (parent)")
CHILD_PAGE_CONFIG_DOCUMENT: dict[str, Any] = {"pages": {}, "docks": {}}
CHILD_SESSION_PROPS_DOCUMENT: dict[str, Any] = {"props": {}}

def named_query_metadata() -> bytes:
    """The metadata one named-query resource carries beside its ``query.sql``.

    The attributes are the recorded Phase 3 fixture's, because this resource is the
    fixture's *unrelated* content: it exists so the preservation case has something
    outside the Perspective module to compare, and it has to be content the Gateway
    stores verbatim rather than regenerates.
    """

    return json.dumps({
        "scope": "DG", "version": 2, "restricted": False, "overridable": True,
        "files": ["query.sql"],
        "attributes": {
            "useMaxReturnSize": True, "autoBatchEnabled": False, "fallbackValue": "",
            "maxReturnSize": 10, "cacheUnit": "SEC", "type": "Query", "enabled": True,
            "cacheAmount": 1, "cacheEnabled": False, "database": "", "fallbackEnabled": False,
            "permissions": [], "parameters": [],
        },
    }).encode("utf-8")


def perspective_project_archive(
    *,
    title: str,
    parent: str = "",
    views: dict[str, dict[str, Any]] | None = None,
    page_config: dict[str, Any] | None = None,
    session_props: dict[str, Any] | None = None,
    named_query: tuple[str, bytes] | None = None,
    entries: dict[str, bytes] | None = None,
) -> bytes:
    """One Project archive in the layout a real 8.3 export uses.

    The entry names are the ones the live Gateway's Perspective module resolves (see the
    constants above): a View is ``views/<Logical path>/view.json`` with its
    ``resource.json``, and the Project's Page configuration and Session properties are the
    ``page-config`` and ``session-props`` resources' own data files, each with its
    metadata file beside it. ``named_query`` is ``(path, body)`` of one named query, with
    the metadata entry the Gateway needs to keep it.
    """

    files: dict[str, bytes] = {
        "project.json": json.dumps({
            "title": title, "description": "Disposable Phase 5 CI Project", "enabled": True,
            "inheritable": not parent, "parent": parent,
        }).encode("utf-8"),
    }
    for path, document in (views or {}).items():
        files[f"{PERSPECTIVE_MODULE}/views/{path}/view.json"] = (
            json.dumps(document, sort_keys=True).encode("utf-8")
        )
        files[f"{PERSPECTIVE_MODULE}/views/{path}/{PERSPECTIVE_RESOURCE_METADATA}"] = (
            resource_metadata(["view.json"])
        )
    if page_config is not None:
        files[f"{PERSPECTIVE_MODULE}/page-config/config.json"] = (
            json.dumps(page_config, sort_keys=True).encode("utf-8")
        )
        files[f"{PERSPECTIVE_MODULE}/page-config/{PERSPECTIVE_RESOURCE_METADATA}"] = (
            resource_metadata(["config.json"])
        )
    if session_props is not None:
        files[f"{PERSPECTIVE_MODULE}/session-props/props.json"] = (
            json.dumps(session_props, sort_keys=True).encode("utf-8")
        )
        files[f"{PERSPECTIVE_MODULE}/session-props/{PERSPECTIVE_RESOURCE_METADATA}"] = (
            resource_metadata(["props.json"])
        )
    if named_query is not None:
        path, body = named_query
        files[f"{path.rsplit('/', 1)[0]}/{PERSPECTIVE_RESOURCE_METADATA}"] = named_query_metadata()
        files[path] = body
    files.update(entries or {})

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in files.items():
            archive.writestr(zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0)), body)
    return buffer.getvalue()


def export_entries(payload: bytes) -> dict[str, bytes]:
    """Every entry of one exported Project archive, by name."""

    if not zipfile.is_zipfile(io.BytesIO(payload)):
        raise ProvisionError("the Project export is not a ZIP archive")
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        return {info.filename: archive.read(info.filename) for info in archive.infolist()}


def view_paths(entries: dict[str, bytes]) -> list[str]:
    """Every Logical View path one exported Project holds *locally*."""

    prefix = f"{PERSPECTIVE_MODULE}/views/"
    suffix = "/view.json"
    return sorted(
        name[len(prefix):-len(suffix)]
        for name in entries
        if name.startswith(prefix) and name.endswith(suffix)
    )


def parent_project_archive(title: str) -> bytes:
    """The parent Project: the one View only it defines.

    The driver compares what the Perspective reads serve against these documents, so the
    fixture and the expectation cannot drift apart: both sides name the same constants.
    """

    return perspective_project_archive(
        title=title, views={INHERITED_VIEW: PARENT_VIEW_DOCUMENT},
    )


def child_project_archive(title: str, parent: str) -> bytes:
    """The child Project: its own View, Page configuration, Session properties, and one
    unrelated named query, with ``parent`` as the Project it inherits from."""

    return perspective_project_archive(
        title=title, parent=parent, views={LOCAL_VIEW: CHILD_VIEW_DOCUMENT},
        page_config=CHILD_PAGE_CONFIG_DOCUMENT, session_props=CHILD_SESSION_PROPS_DOCUMENT,
        named_query=(UNRELATED_QUERY, b"SELECT 1\n"),
    )


def provision_perspective(base_url: str, token: str, *, parent: str, child: str) -> dict[str, Any]:
    """Import the parent and child Perspective Projects, then read them back.

    The import creates a Project that does not exist yet, so this is the whole fixture:
    the parent defines the View the child inherits and only the child is allowlisted for
    the Perspective writes (which is what makes the inherited refusal a rule about the
    resource rather than about the Target). Every check below reads the Projects back
    through the export route the Tools themselves read, so a Gateway that dropped a
    resource fails here instead of confusing a live case.
    """

    parent_status, _ = _request_bytes(
        base_url, token, "POST", PROJECT_IMPORT_PATH.format(name=parent) + PROJECT_IMPORT_OVERWRITE,
        body=parent_project_archive(f"Phase 5 CI parent {parent}"),
        content_type="application/zip",
    )
    child_status, _ = _request_bytes(
        base_url, token, "POST", PROJECT_IMPORT_PATH.format(name=child) + PROJECT_IMPORT_OVERWRITE,
        body=child_project_archive(f"Phase 5 CI child {child}", parent),
        content_type="application/zip",
    )
    statuses = {"parent": parent_status, "child": child_status}
    unexpected = sorted(f"{name}={status}" for name, status in statuses.items() if status != 200)
    if unexpected:
        raise ProvisionError(f"importing the Perspective Projects failed: {unexpected}")

    exports: dict[str, dict[str, bytes]] = {}
    for name in (parent, child):
        status, payload = _request_bytes(base_url, token, "GET", PROJECT_EXPORT_PATH.format(name=name))
        if status != 200:
            raise ProvisionError(f"exporting {name} returned HTTP {status}")
        exports[name] = export_entries(payload)
    parent_entries, child_entries = exports[parent], exports[child]
    parent_views, child_views = view_paths(parent_entries), view_paths(child_entries)
    manifest = json.loads(child_entries.get("project.json", b"{}") or b"{}")

    checks: dict[str, Any] = {
        "parentDefinesTheInheritedView": INHERITED_VIEW in parent_views,
        "childDefinesItsLocalView": LOCAL_VIEW in child_views,
        "childHasNoLocalInheritedView": INHERITED_VIEW not in child_views,
        "childDeclaresTheParent": (
            isinstance(manifest, dict) and manifest.get("parent") == parent
        ),
        "childHasAPageConfig": f"{PERSPECTIVE_MODULE}/page-config/config.json" in child_entries,
        "childHasSessionProps": f"{PERSPECTIVE_MODULE}/session-props/props.json" in child_entries,
        "childHasTheUnrelatedResource": UNRELATED_QUERY in child_entries,
    }
    failed = sorted(name for name, ok in checks.items() if not ok)
    if failed:
        raise ProvisionError(
            f"the Perspective fixture did not survive the import/export round trip: {failed}"
        )
    return {
        "module": {
            "id": PERSPECTIVE_MODULE,
            "route": f"{PERSPECTIVE_MODULE_ROUTE[0]} {PERSPECTIVE_MODULE_ROUTE[1]}",
            # The readiness gate above requires that route, so reaching this point means
            # the Gateway has the module registered, which is what makes the Views, Page
            # configuration and Session properties entries mean anything.
            "loaded": True,
        },
        "parent": {
            "name": parent, "importStatus": parent_status, "views": parent_views,
            "entries": sorted(parent_entries),
        },
        "child": {
            "name": child, "importStatus": child_status, "views": child_views,
            "entries": sorted(child_entries),
        },
        "unrelatedResource": UNRELATED_QUERY,
        "inheritedView": INHERITED_VIEW,
        "localView": LOCAL_VIEW,
        "checks": checks,
    }


def provision(
    base_url: str,
    token: str,
    *,
    project: str,
    control_project: str,
    tag_provider: str = "",
    tag_source_path: str = "",
    perspective_parent: str = "",
    perspective_child: str = "",
) -> dict[str, Any]:
    readiness = await_required_endpoints(base_url, token)
    projects = _project_names(base_url, token)
    missing = sorted({project, control_project} - projects)
    if missing:
        raise ProvisionError(f"the disposable Projects are not installed: {missing}")
    resources = {
        name: {
            "createStatus": _create_profile(base_url, token, name, description),
            "signature": _read_profile(base_url, token, name)["signature"],
            "allowlisted": name not in {UNALLOWLISTED},
        }
        for name, description in (
            (ALLOWLISTED, "Disposable Phase 4 CI audit profile (allowlisted Target)"),
            (UNALLOWLISTED, "Disposable Phase 4 CI audit profile (allowlist control)"),
            (RENAME_SOURCES[0], "Disposable Phase 4 CI audit profile (rename source)"),
            (RENAME_SOURCES[1], "Disposable Phase 4 CI audit profile (rename conflict source)"),
        )
    }
    token_status, token_payload = _request(
        base_url, token, "GET",
        f"/data/api/v1/resources/find/ignition/api-token/ignition-mcp-ci?collection={COLLECTION}",
        allowed_error_statuses=frozenset({404}),
    )
    singleton_status, singleton_payload = _request(
        base_url, token, "GET",
        f"/data/api/v1/resources/singleton/{SINGLETON_TYPE}?collection={COLLECTION}",
        allowed_error_statuses=frozenset({404}),
    )
    return {
        "schemaVersion": 1,
        "resourceType": RESOURCE_TYPE,
        "resources": resources,
        "refusedResourceType": {
            "resourceType": "ignition/api-token",
            "name": "ignition-mcp-ci",
            "present": token_status == 200 and isinstance(token_payload, dict),
        },
        "singletonTarget": {
            "resourceType": SINGLETON_TYPE,
            "present": singleton_status == 200 and isinstance(singleton_payload, dict),
            "signature": (
                singleton_payload.get("signature")
                if singleton_status == 200 and isinstance(singleton_payload, dict)
                else None
            ),
        },
        "readiness": readiness,
        "policyProviders": provision_providers(base_url, token),
        "perspective": (
            provision_perspective(
                base_url, token, parent=perspective_parent, child=perspective_child,
            )
            if perspective_parent and perspective_child
            else None
        ),
        "tagProvider": (
            provision_tags(base_url, token, provider=tag_provider, source_path=tag_source_path)
            if tag_provider and tag_source_path
            else None
        ),
        "projects": {
            "target": {"name": project, "present": project in projects, "allowlisted": True},
            "control": {"name": control_project, "present": control_project in projects,
                        "allowlisted": False},
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway-url", default="http://127.0.0.1:8088")
    parser.add_argument("--api-token", required=True)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--project", required=True)
    parser.add_argument("--control-project", required=True)
    parser.add_argument("--tag-provider", default="")
    parser.add_argument("--tag-source-path", default="")
    #: Phase 5 (P5-3): the parent and child Projects the Perspective cases address. Both
    #: are run-unique and imported by this script, so neither needs any fixture plumbing
    #: in the workflow.
    parser.add_argument("--perspective-parent", default="")
    parser.add_argument("--perspective-child", default="")
    args = parser.parse_args()
    report = provision(
        args.gateway_url, args.api_token,
        project=args.project, control_project=args.control_project,
        tag_provider=args.tag_provider, tag_source_path=args.tag_source_path,
        perspective_parent=args.perspective_parent,
        perspective_child=args.perspective_child,
    )
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "resources": {name: item["signature"] for name, item in report["resources"].items()},
        "refusedResourcePresent": report["refusedResourceType"]["present"],
        "singletonTargetPresent": report["singletonTarget"]["present"],
        "policyProviders": {
            name: item["signature"]
            for name, item in report["policyProviders"]["resources"].items()
        },
        "caseFoldedLookupStatus": report["policyProviders"]["caseFoldedLookup"]["status"],
        "projects": sorted(
            item["name"] for item in report["projects"].values() if item["present"]
        ),
        "tagProvider": (report["tagProvider"] or {}).get("name"),
        "tagSourceTags": (report["tagProvider"] or {}).get("sourceTags"),
        "tagConvention": (report["tagProvider"] or {}).get("convention"),
        "perspective": {
            "parent": (report["perspective"] or {}).get("parent", {}).get("name"),
            "child": (report["perspective"] or {}).get("child", {}).get("name"),
            "childViews": (report["perspective"] or {}).get("child", {}).get("views"),
            "moduleLoaded": (report["perspective"] or {}).get("module", {}).get("loaded"),
            "checks": (report["perspective"] or {}).get("checks"),
        } if report["perspective"] else None,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
