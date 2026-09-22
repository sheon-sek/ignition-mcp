"""Recorded Ignition Gateway fake shared by local live-harness rehearsals.

The public interface is ``RecordedGateway``. It starts one ephemeral HTTP
listener for Native REST and module-hosted MCP, then exposes the two URLs and
captured requests. Wire bodies come from ``tests/fixtures/recorded``.
"""
from __future__ import annotations

import http.server
import io
import json
from pathlib import Path
import threading
from typing import Any
import urllib.parse
import zipfile

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/fixtures/recorded/gateway-8.3"
API_TOKEN = "recorded:AAAAAAAAAAAAAAAAAAAAAAAAAAAA"
_OPENAPI_OPERATIONS = (
    ("get", "/data/api/v1/gateway-info"),
    ("get", "/data/api/v1/projects/list"),
    ("get", "/data/api/v1/projects/export/{name}"),
    ("get", "/data/api/v1/projects/find/{name}"),
    ("post", "/data/api/v1/projects/import/{name}"),
    ("get", "/data/api/v1/designers"),
    ("get", "/data/api/v1/tags/export"),
    ("post", "/data/api/v1/tags/import"),
    ("get", "/data/api/v1/audit/log/{name}"),
    ("get", "/data/alarm-notification/api/v1/pipelines"),
    ("get", "/data/alarm-notification/api/v1/pipeline"),
    # Phase 4 ticket #18: the pipeline cancel surface. The route is a DELETE that
    # takes its two facts in a JSON body, and the status route above is the bounded
    # read its verification uses.
    ("delete", "/data/alarm-notification/api/v1/pipeline"),
    ("get", "/data/api/v1/resources/find/com.inductiveautomation.mcp/server-config/{name}"),
    ("get", "/data/api/v1/resources/type/ignition.gateway/idp-links"),
    ("get", "/data/api/v1/resources/names/ignition.gateway/idp-links"),
    ("get", "/data/api/v1/resources/list/ignition.gateway/idp-links"),
    ("get", "/data/api/v1/resources/find/ignition.gateway/idp-links/{name}"),
    ("get", "/data/api/v1/resources/singleton/ignition/security-levels"),
    ("post", "/data/api/v1/resources/ignition/api-token"),
    ("post", "/data/api/v1/resources/com.inductiveautomation.historian/historian-provider"),
    ("post", "/data/api/v1/resources/ignition/audit-profile"),
    ("post", "/data/api/v1/resources/ignition/database-connection"),
    # Phase 4 ticket #14: the config-resource update surface. The Gateway documents
    # one collection path per resource type for POST (create) and PUT (modify).
    ("get", "/data/api/v1/resources/type/ignition/audit-profile"),
    ("get", "/data/api/v1/resources/names/ignition/audit-profile"),
    ("get", "/data/api/v1/resources/list/ignition/audit-profile"),
    ("get", "/data/api/v1/resources/find/ignition/audit-profile/{name}"),
    ("put", "/data/api/v1/resources/ignition/audit-profile"),
    ("get", "/data/api/v1/resources/type/ignition/api-token"),
    ("get", "/data/api/v1/resources/names/ignition/api-token"),
    ("get", "/data/api/v1/resources/list/ignition/api-token"),
    ("get", "/data/api/v1/resources/find/ignition/api-token/{name}"),
    ("put", "/data/api/v1/resources/ignition/api-token"),
    # An *allowed* singleton type (D30 §5 refuses security-levels): its documented
    # PUT item schema requires only `signature`, so its change item carries no name.
    ("get", "/data/api/v1/resources/type/ignition/cobranding"),
    ("get", "/data/api/v1/resources/singleton/ignition/cobranding"),
    ("put", "/data/api/v1/resources/ignition/cobranding"),
    # Phase 4 ticket #15: the create, delete and rename surface. Every type gets the
    # POST collection route (create) and the DELETE route whose *path* carries the
    # Resource signature (`/{name}/{signature}`, or `/{signature}` for a singleton);
    # a non-singleton also gets the POST rename route, which takes no signature.
    ("post", "/data/api/v1/resources/ignition/cobranding"),
    ("delete", "/data/api/v1/resources/ignition/cobranding/{signature}"),
    ("delete", "/data/api/v1/resources/ignition/audit-profile/{name}/{signature}"),
    ("post", "/data/api/v1/resources/rename/ignition/audit-profile/{name}"),
    ("delete", "/data/api/v1/resources/ignition/api-token/{name}/{signature}"),
    ("post", "/data/api/v1/resources/rename/ignition/api-token/{name}"),
    # Phase 4 ticket #6 characterization: the Native REST write path for the
    # deployment-owned Runtime Target Policy provider and its Tags.
    ("post", "/data/api/v1/resources/ignition/tag-provider"),
    ("get", "/data/api/v1/resources/find/ignition/tag-provider/{name}"),
    ("get", "/data/api/v1/resources/type/ignition/tag-provider"),
    # Phase 4 ticket #36: the same provider type through the generic config Mutations.
    # D30 owner ruling 4 refuses the resource named `IgnitionMCPPolicy` by name, and
    # the cases that prove the *rest* of the type stays manageable need its update,
    # delete and rename routes to exist — a type without them has no such Tool at all
    # (the capability snapshot withholds it), so the refusal being asserted could
    # never be reached.
    ("put", "/data/api/v1/resources/ignition/tag-provider"),
    ("delete", "/data/api/v1/resources/ignition/tag-provider/{name}/{signature}"),
    ("post", "/data/api/v1/resources/rename/ignition/tag-provider/{name}"),
)

#: Path segments under ``/data/api/v1/resources/`` that name an operation rather
#: than a resource type, so a collection path can never be confused with them.
_RESERVED_RESOURCE_SEGMENTS = frozenset({
    "copy", "delete", "move", "rename", "datafile", "names", "list", "find", "singleton", "type",
})

#: The documented rename route: ``/data/api/v1/resources/rename/<type>/{name}``.
_RENAME_ROUTE_PREFIX = "/data/api/v1/resources/rename/"

#: The Gateway's default configuration collection. A read or a change that names no
#: collection lands in this one, which is what the documented ``collection`` query
#: parameter's own example says (``core``). D30's owner ruling 5 pins generic config
#: Mutations to it explicitly, so every request a Mutation makes names it.
DEFAULT_COLLECTION = "core"


def _resource_type_segment(path: str) -> str | None:
    """The exact ``<module>/<typeId>`` a collection path addresses, or ``None``."""

    prefix = "/data/api/v1/resources/"
    if not path.startswith(prefix):
        return None
    remainder = path[len(prefix):]
    parts = remainder.split("/")
    if len(parts) != 2 or not all(parts) or parts[0] in _RESERVED_RESOURCE_SEGMENTS:
        return None
    return remainder


def _delete_target(path: str) -> tuple[str | None, str, str]:
    """Split a documented DELETE path into ``(resourceType, name, signature)``.

    ``/<type>/{name}/{signature}`` addresses a named resource and
    ``/<type>/{signature}`` the type's singleton; anything else is not a route this
    fixture serves (and ``resourceType`` is ``None``).
    """

    prefix = "/data/api/v1/resources/"
    if not path.startswith(prefix):
        return None, "", ""
    parts = path[len(prefix):].split("/")
    if len(parts) == 4 and all(parts):
        resource_type = f"{parts[0]}/{parts[1]}"
        return resource_type, parts[2], parts[3]
    if len(parts) == 3 and all(parts):
        return f"{parts[0]}/{parts[1]}", "", parts[2]
    return None, "", ""


#: The repository's committed 8.3.8 specification. The recorded Gateway advertises
#: the *real* request body for every route the server's D03 validation reads: a
#: route without the request schema the capability snapshot needs is withheld, so a
#: schema-less stub would silently disable the Tool under test instead of
#: exercising it.
COMMITTED_OPENAPI = ROOT / "docs/ignition-8.3.8-openapi/openapi.min.json"
#: Operation paths that name an action rather than a resource type, so their
#: request bodies are also part of the recorded document.
_ACTION_PREFIXES = ("/data/api/v1/resources/rename/",)
_committed_openapi: dict[str, Any] | None = None


def _committed_document() -> dict[str, Any]:
    global _committed_openapi
    if _committed_openapi is None:
        _committed_openapi = json.loads(COMMITTED_OPENAPI.read_text(encoding="utf-8"))
    return _committed_openapi


def _committed_request_body(method: str, operation_path: str) -> dict[str, Any] | None:
    """The committed request body of one documented collection or action route."""

    if method not in {"put", "post"}:
        return None
    if _resource_type_segment(operation_path) is None and not operation_path.startswith(
        _ACTION_PREFIXES,
    ):
        return None
    operation = (_committed_document().get("paths") or {}).get(operation_path)
    body = ((operation or {}).get(method) or {}).get("requestBody")
    return body if isinstance(body, dict) else None


def _recorded_operation(method: str, operation_path: str) -> dict[str, Any]:
    body = _committed_request_body(method, operation_path)
    return {"requestBody": body} if body else {}


def _committed_components() -> dict[str, Any]:
    """The committed component schemas the injected request bodies reference.

    A real request body refers to the specification's ``#/components/schemas/...``
    entries, so the recorded OpenAPI has to carry them or the reference is
    unresolvable — which is exactly the failure mode a Gateway with an unusable
    request schema must produce. Only the reachable closure is injected, so the
    recorded document stays small.
    """

    schemas: dict[str, Any] = {}
    pending: list[Any] = []
    for method, operation_path in _OPENAPI_OPERATIONS:
        operation = _recorded_operation(method, operation_path)
        if operation:
            pending.append(operation)
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            reference = node.get("$ref")
            if isinstance(reference, str) and reference.startswith("#/components/schemas/"):
                name = reference[len("#/components/schemas/"):]
                if name not in schemas:
                    target = _committed_pointer(reference)
                    schemas[name] = target
                    pending.append(target)
            pending.extend(node.values())
        elif isinstance(node, list):
            pending.extend(node)
    return {"schemas": schemas}


def _committed_pointer(pointer: str) -> Any:
    node: Any = _committed_document()
    for part in pointer[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        node = node[part]
    return node


def _fixture(path: str) -> Any:
    return json.loads((FIXTURES / path).read_text(encoding="utf-8"))


def _zip_entries(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {entry.filename: archive.read(entry.filename) for entry in archive.infolist()}


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in entries.items():
            archive.writestr(name, body)
    return output.getvalue()


def _gateway_export(project: bytes) -> bytes:
    """Apply the deterministic project.json serialization recorded on export."""
    entries = _zip_entries(project)
    entries["project.json"] = json.dumps(
        json.loads(entries["project.json"]), sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return _zip_bytes(entries)


def _gateway_import(current: bytes | None, incoming: bytes) -> bytes:
    """Model the recorded project.json-only import transition and fixed point."""
    if current is None:
        return _gateway_export(incoming)
    current_entries = _zip_entries(current)
    incoming_entries = _zip_entries(incoming)
    current_project = json.loads(current_entries["project.json"])
    incoming_project = json.loads(incoming_entries["project.json"])
    if incoming_project != current_project:
        incoming_entries["project.json"] = json.dumps(
            incoming_project, sort_keys=True,
        ).encode("utf-8")
    else:
        incoming_entries["project.json"] = current_entries["project.json"]
    return _zip_bytes(incoming_entries)


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args: object) -> None:
        pass

    def _send(self, status: int, body: bytes = b"", content_type: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, value: object) -> None:
        self._send(status, json.dumps(value, separators=(",", ":")).encode("utf-8"))

    def _read_body(self) -> bytes:
        if (self.headers.get("Transfer-Encoding") or "").lower() == "chunked":
            body = bytearray()
            while True:
                size = int(self.rfile.readline().strip().split(b";", 1)[0], 16)
                if size == 0:
                    self.rfile.readline()
                    return bytes(body)
                body.extend(self.rfile.read(size))
                self.rfile.readline()
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def do_GET(self) -> None:  # noqa: N802
        server: Any = self.server
        server.requests.append({"method": "GET", "path": self.path, "headers": dict(self.headers), "body": b""})
        path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
        if path.startswith("/data/mcp/"):
            self._json(415, _fixture("http/unsupported-media-type.json"))
            return
        if self.headers.get("X-Ignition-API-Token") != API_TOKEN:
            self._json(403, _fixture("http/forbidden.json"))
            return
        if path.startswith("/data/api/v1/projects/export/"):
            name = path.rsplit("/", 1)[-1]
            project = server.observe_export(name)
            if project is None:
                self._json(404, {"message": "No project", "status": "404"})
                return
            self._send(200, project, "application/zip")
            return
        if path == "/data/api/v1/gateway-info":
            self._json(200, {
                "name": "recorded-gateway",
                "edition": "standard",
                "ignitionVersion": "8.3.8 (b2026071409)",
            })
            return
        if path == "/openapi.json":
            if server.openapi_missing_responses > 0:
                server.openapi_missing_responses -= 1
                self._json(200, {"openapi": "3.0.1", "paths": {}})
            else:
                server.modules_active = True
                document = _fixture("phase2/openapi-required.json")
                document["components"] = _committed_components()
                paths: dict[str, dict[str, object]] = document["paths"]
                for method, operation_path in _OPENAPI_OPERATIONS:
                    paths.setdefault(operation_path, {})[method] = _recorded_operation(method, operation_path)
                self._json(200, document)
            return
        if path == "/data/api/v1/resources/names/ignition/database-driver":
            self._json(200, _fixture("phase2/database-drivers.json"))
            return
        if path == "/data/api/v1/resources/names/ignition/database-translator":
            self._json(200, _fixture("phase2/database-translators.json"))
            return
        if path == "/data/api/v1/modules/quarantined":
            payload = _fixture("phase2/modules-quarantined.json")
            payload["items"] = [
                item for item in payload["items"] if item["id"] in server.quarantined_modules
            ]
            self._json(200, payload)
            return
        if path == "/data/api/v1/modules/healthy":
            fixture = (
                "phase2/modules-healthy-after-restart.json"
                if server.modules_active
                else "phase2/modules-healthy-before-restart.json"
            )
            self._json(200, _fixture(fixture))
            return
        if path == "/data/alarm-notification/api/v1/pipeline":
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            payload = server.read_pipeline(_first(query, "path", ""), query)
            if payload is None:
                self._json(404, {"message": "No such pipeline", "status": "404"})
                return
            self._json(200, payload)
            return
        if path == "/data/api/v1/projects/list":
            names = sorted(server.projects)
            self._json(200, {
                "items": [{"name": name} for name in names],
                "metadata": {
                    "total": float(len(names)),
                    "matching": float(len(names)),
                    "limit": 500,
                    "offset": 0,
                },
            })
            return
        if path.startswith("/data/api/v1/projects/find/"):
            name = path.rsplit("/", 1)[-1]
            project = server.projects.get(name)
            if project is None:
                self._json(404, {"message": "No project", "status": "404"})
                return
            document = json.loads(_zip_entries(project)["project.json"])
            self._json(200, {
                "name": name,
                "title": document.get("title", name),
                "description": document.get("description", ""),
                "inheritable": bool(document.get("inheritable", False)),
            })
            return
        if path.startswith("/data/api/v1/resources/find/ignition/tag-provider/"):
            name = path.rsplit("/", 1)[-1]
            # Ticket #6's recorded provider document, for the harness that provisions the
            # Runtime Target Policy through this route. Every other name — and the same
            # name before that harness has created it — is the generic recorded behaviour
            # below, so a test that seeds its own Tag-provider resources (ticket #36's
            # by-name refusal) reads them back like any other resource.
            if name == server.policy_provider and server.policy_provider_created:
                self._json(200, _fixture("phase4/tag-provider-find.json"))
                return
        if path.startswith("/data/api/v1/resources/find/com.inductiveautomation.mcp/server-config/"):
            self._json(200, {"name": "phase3-runtime"})
            return
        if path.startswith("/data/api/v1/resources/"):
            payload = server.read_resource(
                path,
                urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query),
            )
            if payload is None:
                self._json(404, {"message": "No recorded resource", "status": "404"})
                return
            self._json(200, payload)
            return
        if path == "/data/api/v1/tags/export":
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            provider = query.get("provider", [""])[0]
            if provider == server.policy_provider:
                if not server.policy_provider_created:
                    self._json(404, {"message": "No tag provider", "status": "404"})
                    return
                self._send(200, json.dumps(
                    _fixture("phase4/tag-export.json"), separators=(",", ":"),
                ).encode("utf-8"), "application/octet-stream")
                return
            if provider in server.tags:
                # Phase 4 ticket #17: a modelled provider exports its own state, so an
                # import the Tool dispatched is visible to the bounded re-export that
                # verifies it.
                document = server.export_tags(
                    provider,
                    _first(query, "path", ""),
                    recursive=_first(query, "recursive", "true") != "false",
                    include_udts=_first(query, "includeUdts", "true") != "false",
                )
                if document is None:
                    self._json(404, {"message": "No such tag path", "status": "404"})
                    return
                self._send(200, json.dumps(document, separators=(",", ":")).encode("utf-8"),
                           "application/octet-stream")
                return
            self._json(200, {"path": "", "tags": [{"name": "Status", "tagType": "Boolean"}]})
            return
        if path == "/data/api/v1/designers":
            self._json(200, {
                "items": [],
                "metadata": {"total": 0.0, "matching": 0.0, "limit": 100, "offset": 0},
            })
            return
        self._json(404, {"message": "No recorded response", "status": "404"})

    def do_POST(self) -> None:  # noqa: N802
        server: Any = self.server
        body = self._read_body()
        server.requests.append({"method": "POST", "path": self.path, "headers": dict(self.headers), "body": body})
        path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
        if path.startswith("/data/mcp/"):
            if self.headers.get("X-Ignition-API-Token") != API_TOKEN:
                self._json(403, _fixture("http/forbidden.json"))
                return
            payload = json.loads(body)
            method = payload.get("method")
            if method == "initialize":
                response = _fixture("mcp/initialize.json")
                response["id"] = payload.get("id")
                response["result"]["serverInfo"] = {
                    "name": "phase3-runtime",
                    "title": "Phase 3 Runtime Readonly",
                    "version": server.bundle_version,
                }
                self._json(200, response)
                return
            if method == "notifications/initialized":
                self._send(202)
                return
            if method == "prompts/list":
                response = _fixture("mcp/prompts-list-invalid-request.json")
                response["id"] = payload.get("id")
                self._json(200, response)
                return
            if method == "tools/list":
                self._json(200, {
                    "jsonrpc": "2.0",
                    "id": payload.get("id"),
                    "result": {"tools": [
                        {"name": name, "description": f"{name} recorded replay"}
                        for name in server.runtime_tools
                    ]},
                })
                return
            if method == "resources/list":
                self._json(200, {
                    "jsonrpc": "2.0",
                    "id": payload.get("id"),
                    "result": {"resources": [
                        {"uri": uri, "name": uri.rsplit("/", 1)[-1]}
                        for uri in server.runtime_resources
                    ]},
                })
                return
            if method == "resources/read":
                uri = str((payload.get("params") or {}).get("uri"))
                self._json(200, {
                    "jsonrpc": "2.0",
                    "id": payload.get("id"),
                    "result": {"contents": [{
                        "uri": uri,
                        "mimeType": "application/json",
                        "text": json.dumps({"schemaVersion": 1, "recordedReplay": True}),
                    }]},
                })
                return
            if method == "tools/call":
                tool = str((payload.get("params") or {}).get("name"))
                if tool == "bundle_info":
                    result = {
                        "content": [{"type": "text", "text": "recorded replay"}],
                        "isError": False,
                        "structuredContent": {
                            "bundleVersion": server.bundle_version,
                            "bundleSourceRevision": server.source_revision,
                            "gatewayVersion": "8.3.8 (b2026071409)",
                            "mcpModuleVersion": "1.3.5-SNAPSHOT",
                            "compatibilityStatus": "UNKNOWN",
                        },
                    }
                elif tool == "tag_read":
                    result = {
                        "content": [{"type": "text", "text": "tag path is not valid"}],
                        "isError": True,
                    }
                elif tool in {"policy_probe", "alarm_probe"}:
                    fixture = "phase4/policy-probe.json" if tool == "policy_probe" else "phase4/alarm-probe.json"
                    result = {
                        "content": [{"type": "text", "text": "recorded replay"}],
                        "isError": False,
                        "structuredContent": _fixture(fixture),
                    }
                else:
                    self._json(200, {
                        "jsonrpc": "2.0",
                        "id": payload.get("id"),
                        "error": {"code": -32602, "message": f"unknown tool {tool}"},
                    })
                    return
                self._json(200, {"jsonrpc": "2.0", "id": payload.get("id"), "result": result})
                return
        if self.headers.get("X-Ignition-API-Token") != API_TOKEN:
            self._json(403, _fixture("http/forbidden.json"))
            return
        # Phase 4 ticket #15: a type the harness has published resource state for
        # answers the write surface from that state, so create and rename behave as
        # the real Gateway does. A type with no published state keeps the recorded
        # Phase 2/3 provisioning behaviour of its own exact-path branch below.
        if path.startswith(_RENAME_ROUTE_PREFIX):
            resource_type, _, name = path[len(_RENAME_ROUTE_PREFIX):].rpartition("/")
            # D30 owner ruling 5: the documented rename route takes the collection as a
            # query parameter, and that is the collection the rename applies to.
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            status, payload = server.apply_resource_rename(
                resource_type, name, body, _first(query, "collection", DEFAULT_COLLECTION),
            )
            self._json(status, payload)
            return
        resource_type = _resource_type_segment(path)
        if resource_type is not None and resource_type in server.resources:
            status, payload = server.apply_resource_create(resource_type, body)
            self._json(status, payload)
            return
        if path.startswith("/data/api/v1/projects/import/"):
            name = path.rsplit("/", 1)[-1]
            status, payload = server.apply_project_import(name, body)
            self._json(status, payload)
            return
        if path == "/data/api/v1/resources/ignition/database-connection":
            resources = json.loads(body)
            config = resources[0].get("config", {}) if isinstance(resources, list) and resources else {}
            if config.get("driver") == "PostgreSQL":
                self._json(422, _fixture("phase2/postgresql-missing.json"))
                return
            if isinstance(config.get("password"), str):
                self._json(422, _fixture("phase2/credential-object-required.json"))
                return
            self._json(200, {})
            return
        if path == "/data/api/v1/resources/com.inductiveautomation.historian/historian-provider":
            if not server.modules_active:
                self._json(404, _fixture("phase2/no-route.json"))
                return
            self._json(200, {})
            return
        if path == "/data/api/v1/resources/ignition/audit-profile":
            self._json(200, {})
            return
        if path == "/data/api/v1/resources/ignition/tag-provider":
            resources = json.loads(body)
            names = [item.get("name") for item in resources] if isinstance(resources, list) else []
            if server.policy_provider not in names:
                self._json(422, {"message": "Unexpected provider name", "status": "422"})
                return
            server.policy_provider_created = True
            self._json(200, _fixture("phase4/tag-provider-create.json"))
            return
        if path == "/data/api/v1/tags/import":
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            provider = query.get("provider", [""])[0]
            collision_policy = query.get("collisionPolicy", [""])[0]
            if provider == server.policy_provider:
                if not server.policy_provider_created:
                    self._json(404, _fixture("phase2/no-route.json"))
                    return
                if not server.policy_tags_imported:
                    if not server.policy_import_flaked:
                        # The recorded 8.3.8 run answered the first import while
                        # the provider was still starting; apply must retry.
                        server.policy_import_flaked = True
                        self._json(200, _fixture("phase4/tag-import-provider-not-ready.json"))
                        return
                    server.policy_tags_imported = True
                    self._json(200, _fixture("phase4/tag-import-success.json"))
                    return
                if collision_policy == "Abort":
                    self._json(200, _fixture("phase4/tag-import-abort.json"))
                    return
                self._json(200, _fixture("phase4/tag-import-merge.json"))
                return
            if provider in server.tags:
                # Phase 4 ticket #17: a modelled provider applies the documented
                # transition against its own state, so the Tool's collision check and
                # its bounded re-export both see the same Tags the Gateway serves.
                status, payload = server.apply_tag_import(
                    provider,
                    _first(query, "path", ""),
                    body,
                    collision_policy=collision_policy,
                )
                self._json(status, payload)
                return
            document = json.loads(body)
            if isinstance(document, list):
                self._json(200, _fixture("phase2/tag-import-bare-array.json"))
            else:
                # The green provision recording retained the 200 status, not a response body.
                self._send(200)
            return
        if path == "/data/api/v1/modules/certificate":
            module_id = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("moduleId", [""])[0]
            if module_id not in server.quarantined_modules:
                self._json(404, {"message": "Module not quarantined", "status": "404"})
                return
            if server.certificate_accepted:
                self._json(409, _fixture("phase2/certificate-already-accepted.json"))
                return
            server.certificate_accepted = True
            self._json(200, {})
            return
        self._json(404, {"message": "No recorded response", "status": "404"})

    def do_PUT(self) -> None:  # noqa: N802
        server: Any = self.server
        body = self._read_body()
        server.requests.append({"method": "PUT", "path": self.path, "headers": dict(self.headers), "body": body})
        path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
        if self.headers.get("X-Ignition-API-Token") != API_TOKEN:
            self._json(403, _fixture("http/forbidden.json"))
            return
        resource_type = _resource_type_segment(path)
        if resource_type is None or resource_type not in server.resources:
            self._json(404, {"message": "No recorded response", "status": "404"})
            return
        status, payload = server.apply_resource_update(resource_type, body)
        self._json(status, payload)

    def do_DELETE(self) -> None:  # noqa: N802
        server: Any = self.server
        body = self._read_body()
        server.requests.append({
            "method": "DELETE", "path": self.path, "headers": dict(self.headers), "body": body,
        })
        path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
        if self.headers.get("X-Ignition-API-Token") != API_TOKEN:
            self._json(403, _fixture("http/forbidden.json"))
            return
        # Phase 4 ticket #18: the documented cancel route takes its two facts in the
        # request body (`DELETE` with a JSON body), unlike the resource deletes below,
        # whose signature travels in the path.
        if path == "/data/alarm-notification/api/v1/pipeline":
            status, payload = server.apply_pipeline_cancel(body)
            self._json(status, payload)
            return
        # The signature travels in the path, exactly as the documented route has it:
        # `/<type>/{name}/{signature}`, or `/<type>/{signature}` for a singleton.
        resource_type, name, signature = _delete_target(path)
        if resource_type is None:
            self._json(404, {"message": "No recorded response", "status": "404"})
            return
        # D30 owner ruling 5: the documented DELETE route takes the collection as a
        # query parameter, and that is the collection the delete applies to.
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        status, payload = server.apply_resource_delete(
            resource_type, name, signature, _first(query, "collection", DEFAULT_COLLECTION),
        )
        self._json(status, payload)


class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        projects: dict[str, bytes],
        openapi_missing_responses: int,
        quarantined_modules: tuple[str, ...],
        runtime_tools: tuple[str, ...],
        runtime_resources: tuple[str, ...],
        source_revision: str,
        bundle_version: str,
        policy_provider: str = "",
        port: int = 0,
    ) -> None:
        super().__init__(("127.0.0.1", port), _Handler)
        self.requests: list[dict[str, Any]] = []
        self.projects = {name: _gateway_export(project) for name, project in projects.items()}
        self.imports: list[str] = []
        self.openapi_missing_responses = openapi_missing_responses
        self.quarantined_modules = set(quarantined_modules)
        self.certificate_accepted = False
        self.modules_active = openapi_missing_responses == 0 and not quarantined_modules
        self.runtime_tools = runtime_tools
        self.runtime_resources = runtime_resources
        self.source_revision = source_revision
        self.bundle_version = bundle_version
        self.policy_provider = policy_provider
        self.policy_provider_created = False
        self.policy_tags_imported = False
        self.policy_import_flaked = False
        #: Modelled Tag state, keyed by provider: the provider root node in the
        #: recorded JSON export shape (``{"name": "", "tagType": "Provider", "tags": [...]}``).
        #: Seed it through :meth:`RecordedGateway.seed_tags`; the tag routes answer
        #: from it exactly as the recorded Gateway answers from its own Tag state.
        self.tags: dict[str, dict[str, Any]] = {}
        #: Tag imports the fixture actually applied (provider, path, Tag names), the
        #: observation a test asserts alongside the recorded requests.
        self.tag_imports: list[dict[str, Any]] = []
        #: Which wire shape the import route answers a reported failure with: the
        #: summary object live 8.3.8/8.3.9 return, or the list of non-Good QualityCodes
        #: the committed OpenAPI documents.
        self.tag_import_wire_shape = "summary"
        #: When set, the import route applies only the first N nodes of the document
        #: and reports the rest as failures — a partial application.
        self.tag_import_partial: int | None = None
        #: Modelled, not recorded: the import route reports a clean success and creates
        #: nothing, so only the bounded re-export can tell the difference.
        self.tag_import_lies = False
        #: Modelled, not recorded: the 2xx body the import route answers with whatever
        #: the transition did, for a case that needs a response this server cannot
        #: interpret (a negative count, a count of the wrong type, ``null``). Set it
        #: through :meth:`answer_tag_import_with`.
        self.tag_import_body: Any = _UNSET
        #: config resource state: resource type -> (name, collection) -> document.
        #: Keying by collection as well as name is what makes the fixture able to
        #: tell two resources with one name in different collections apart. Seed it
        #: through :meth:`RecordedGateway.seed_resource`.
        self.resources: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}
        #: Modelled Alarm Notification Pipeline runtime state, keyed by the fully
        #: qualified pipeline path: the instances the status route serves. Seed it
        #: through :meth:`RecordedGateway.seed_pipeline`; a path with no state is a
        #: 404, exactly as a Gateway with no such pipeline answers.
        self.pipelines: dict[str, list[dict[str, Any]]] = {}
        #: The pipeline cancels the fixture actually applied, the observation a test
        #: asserts alongside the recorded requests.
        self.pipeline_cancels: list[dict[str, Any]] = []
        #: Modelled (not recorded) cancel behaviour: a refusal the Gateway reports
        #: inside a 200 (`pipeline_cancel_refusal`, its message), a 2xx that claims
        #: nothing (`pipeline_cancel_unclaimed`), an ambiguous status that applies
        #: nothing (`pipeline_cancel_status`), and a competing cancel at dispatch time
        #: (`pipeline_cancel_race`).
        self.pipeline_cancel_refusal: str | None = None
        self.pipeline_cancel_unclaimed = False
        self.pipeline_cancel_lies = False
        self.pipeline_cancel_status: int | None = None
        self.pipeline_cancel_race: dict[str, str] | None = None
        self.signature_serial = 0
        #: Modelled (not recorded) per-operation write behaviour, keyed by the
        #: operation the Tool performs ("update", "create", "delete", "rename"):
        #: a Gateway that refuses a change inside a 200 (`write_problem`), one that
        #: answers an ambiguous status without applying anything (`write_status`),
        #: and a competing writer that acts at dispatch time (`write_race`).
        self.write_problem: dict[str, str] = {}
        self.write_status: dict[str, int] = {}
        self.write_race: dict[str, dict[str, Any]] = {}
        #: Exports served, and one scheduled external Project change that lands once
        #: that many exports have been served (models a writer acting between baseline
        #: A and the D16 pre-import re-export A').
        self.exports_served = 0
        self.project_change_after: tuple[str, int, dict[str, bytes]] | None = None

    # ------------------------------------------------------- projects

    def observe_export(self, name: str) -> bytes | None:
        """Serve one Project export, applying a scheduled external change first.

        The change lands *after* the scheduled number of exports have been served, so
        the export that follows it is the first to see it.
        """

        pending = self.project_change_after
        if pending is not None and pending[0] == name and self.exports_served >= pending[1]:
            self.project_change_after = None
            self.change_project(pending[0], pending[2])
        self.exports_served += 1
        return self.projects.get(name)

    def change_project(self, name: str, entries: dict[str, bytes]) -> None:
        """Change a Project without the MCP server, as another operator would.

        The change goes through the recorded import transition, so the Project stays a
        valid archive while a fingerprint taken before it is stale.
        """

        self.projects[name] = _gateway_import(self.projects.get(name), _zip_bytes(entries))

    def apply_project_import(self, name: str, body: bytes) -> tuple[int, dict[str, Any]]:
        """Apply one recorded Gateway Project import.

        The transition itself is the recorded one (:func:`_gateway_import`); the write
        hooks the config-resource routes use model the boundaries around it, keyed by
        ``"import"``: a competing writer at dispatch time (``write_race``), an
        ambiguous status that applies nothing (``write_status``), and a refusal carried
        inside a 200 (``write_problem``).
        """

        if race := self.write_race.pop("import", None):
            self.change_project(name, {key: value for key, value in race.items()})
        if (status := self.write_status.get("import")) is not None:
            return status, {"message": "Internal Server Error", "status": str(status)}
        if (problem := self.write_problem.get("import")) is not None:
            return 200, _refused(problem)
        self.projects[name] = _gateway_import(self.projects.get(name), body)
        self.imports.append(name)
        return 200, {"message": f"Project {name} imported"}

    # ---------------------------------------------------------------- tags

    def seed_tags(self, provider: str, tags: list[dict[str, Any]]) -> dict[str, Any]:
        """Publish one modelled provider's top-level Tag nodes.

        The document is the recorded JSON export shape, so an export of the provider
        at an empty path answers exactly what ``seed_tags`` published.
        """

        root: dict[str, Any] = {"name": "", "tagType": "Provider", "tags": _tag_nodes(tags)}
        self.tags[provider] = root
        return root

    def tag_node(self, provider: str, path: str) -> dict[str, Any] | None:
        """The node one modelled provider holds at ``path`` (``None`` = no such path)."""

        root = self.tags.get(provider)
        if root is None:
            return None
        node = root
        for segment in _tag_segments(path):
            child = _tag_child(node, segment)
            if child is None:
                return None
            node = child
        return node

    def tag_folder(self, provider: str, path: str) -> dict[str, Any]:
        """The node at ``path``, creating the folders along it as an import would."""

        root = self.tags.setdefault(provider, {"name": "", "tagType": "Provider", "tags": []})
        node = root
        for segment in _tag_segments(path):
            child = _tag_child(node, segment)
            if child is None:
                child = {"name": segment, "tagType": "Folder"}
                node.setdefault("tags", []).append(child)
            node = child
        return node

    def apply_tag_write(self, provider: str, path: str, tags: list[dict[str, Any]]) -> None:
        """Create or replace Tags without any Gateway dispatch, as another writer would."""

        target = self.tag_folder(provider, path)
        for node in tags:
            existing = _tag_child(target, str(node.get("name")))
            if existing is None:
                target.setdefault("tags", []).append(_tag_copy(node))
            else:
                existing.clear()
                existing.update(_tag_copy(node))

    def export_tags(
        self, provider: str, path: str, *, recursive: bool, include_udts: bool,
    ) -> dict[str, Any] | None:
        """One modelled provider's JSON export at ``path`` (``None`` = no such path).

        ``recursive=False`` keeps the immediate children and drops their own children;
        ``include_udts=False`` drops the ``_types_`` node, which is the folder the
        recorded export carries the provider's UDT definitions under.
        """

        node = self.tag_node(provider, path)
        if node is None:
            return None
        exported = _tag_copy(node)
        if not recursive:
            for child in exported.get("tags") or []:
                if isinstance(child, dict):
                    child.pop("tags", None)
        if not include_udts:
            _drop_tag(exported, "_types_")
        return exported

    def apply_tag_import(
        self, provider: str, path: str, body: bytes, *, collision_policy: str,
    ) -> tuple[int, Any]:
        """Apply one recorded Tag import and answer with the route's own report.

        The transition is the recorded one; when a case has substituted a response body
        (:meth:`answer_tag_import_with`) the transition still happens, and only the body
        the route reports it with changes — the state is real while the response is
        unreadable, which is exactly the case a verification-based Tool must not read as
        a success.
        """

        status, payload = self._tag_import_transition(provider, path, body, collision_policy)
        if self.tag_import_body is not _UNSET:
            return 200, self.tag_import_body
        return status, payload

    def _tag_import_transition(
        self, provider: str, path: str, body: bytes, collision_policy: str,
    ) -> tuple[int, Any]:
        """The recorded Tag import transition against a modelled provider's state.

        The document's Tag nodes are created under ``path``, and ``Abort`` refuses the
        whole import when any of them already exists there (D30 §4). The write hooks
        model the same boundaries the config-resource routes model, keyed by
        ``"tag_import"``: a competing writer at dispatch time (``write_race``), an
        ambiguous status that applies nothing (``write_status``), and a reported failure
        inside a 200 (``write_problem``).
        """

        if race := self.write_race.pop("tag_import", None):
            self.apply_tag_write(race["provider"], race["path"], race["tags"])
        if (status := self.write_status.get("tag_import")) is not None:
            return status, {"message": "Internal Server Error", "status": str(status)}
        if (problem := self.write_problem.get("tag_import")) is not None:
            return 200, _tag_import_failures([problem], self.tag_import_wire_shape)
        document = _decode_tag_document(body)
        if document is None:
            return 400, _INVALID_BODY
        incoming = _incoming_tag_nodes(document)
        if self.tag_import_lies:
            #: Modelled, not recorded: a Gateway that reports a clean import and
            #: creates nothing. The bounded re-export is what has to catch it.
            return 200, _tag_import_success(len(incoming), self.tag_import_wire_shape)
        target = self.tag_folder(provider, path)
        collisions = [
            node for node in incoming
            if _tag_child(target, str(node.get("name"))) is not None
        ]
        if collisions and collision_policy == "Abort":
            messages = [
                f"Tag '[{provider}]{_join_tag_path(path, str(node.get('name')))}' already exists,"
                " and 'abort' collision policy has been specified"
                for node in collisions
            ]
            return 200, _tag_import_failures(
                messages, self.tag_import_wire_shape, sub_code=527,
            )
        applied: list[str] = []
        for index, node in enumerate(incoming):
            if self.tag_import_partial is not None and index >= self.tag_import_partial:
                break
            target.setdefault("tags", []).append(_tag_copy(node))
            applied.append(str(node.get("name")))
        if applied:
            self.tag_imports.append({"provider": provider, "path": path, "names": applied})
        if len(applied) < len(incoming):
            messages = [
                f"Tag '[{provider}]{_join_tag_path(path, str(node.get('name')))}' was not imported"
                for node in incoming[len(applied):]
            ]
            return 200, _tag_import_failures(
                messages, self.tag_import_wire_shape, success_count=len(applied),
            )
        return 200, _tag_import_success(len(incoming), self.tag_import_wire_shape)

    # ------------------------------------------------------- alarm pipelines

    def seed_pipeline(self, path: str, instances: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Publish the running instances of one modelled pipeline path."""

        published = [_pipeline_instance(path, instance) for instance in instances]
        self.pipelines[path] = published
        return published

    def pipeline_instances(self, path: str) -> list[dict[str, Any]] | None:
        """The instances one modelled pipeline path serves (``None`` = no such path)."""

        instances = self.pipelines.get(path)
        if instances is None:
            return None
        return [dict(instance) for instance in instances]

    def read_pipeline(self, path: str, query: dict[str, list[str]]) -> dict[str, Any] | None:
        """The recorded read behavior of the status route (``None`` = 404/path absent)."""

        instances = self.pipelines.get(path)
        if instances is None:
            return None
        limit = int(_first(query, "limit", "100"))
        offset = int(_first(query, "offset", "0"))
        window = instances[offset:offset + limit]
        return {
            "items": [dict(instance) for instance in window],
            "metadata": {
                "total": float(len(instances)), "matching": float(len(instances)),
                "limit": limit, "offset": offset,
            },
        }

    def cancel_pipeline_instance(self, path: str, alarm_event_id: str) -> bool:
        """Cancel one pipeline run without any Gateway dispatch, as an operator would."""

        instances = self.pipelines.get(path)
        if instances is None:
            return False
        remaining = [
            instance for instance in instances
            if instance.get("alarmEventId") != alarm_event_id
        ]
        if len(remaining) == len(instances):
            return False
        self.pipelines[path] = remaining
        return True

    def apply_pipeline_cancel(self, body: bytes) -> tuple[int, dict[str, Any]]:
        """Apply one recorded Gateway pipeline cancel against a modelled pipeline.

        The recorded transition is the documented one: the route removes the run the
        ``(path, alarmEventId)`` pair names and answers ``{"success": true,
        "alarmEventId": ...}``; a pair the pipeline does not hold is refused inside the
        200 with ``success: false``. The write hooks model the boundaries around it: a
        competing cancel at dispatch time (``pipeline_cancel_race``), an ambiguous
        status that applies nothing (``pipeline_cancel_status``), and a 2xx that claims
        nothing (``pipeline_cancel_unclaimed``).
        """

        try:
            document = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            return 400, _INVALID_BODY
        if not isinstance(document, dict):
            return 400, _INVALID_BODY
        path, alarm_event_id = document.get("path"), document.get("alarmEventId")
        if not isinstance(path, str) or not path or not isinstance(alarm_event_id, str) or not alarm_event_id:
            return 400, _INVALID_BODY
        if (race := self.pipeline_cancel_race) is not None:
            self.pipeline_cancel_race = None
            self.cancel_pipeline_instance(race["path"], race["alarmEventId"])
        if (status := self.pipeline_cancel_status) is not None:
            return status, {"message": "Internal Server Error", "status": str(status)}
        if (problem := self.pipeline_cancel_refusal) is not None:
            return 200, {
                "success": False,
                "alarmEventId": alarm_event_id,
                "problem": {"message": problem, "stacktrace": []},
            }
        if self.pipeline_cancel_lies:
            #: Modelled, not recorded: a Gateway that reports a clean cancel and leaves
            #: the run in place. The bounded re-read is what has to catch it.
            return 200, {"success": True, "alarmEventId": alarm_event_id}
        applied = self.cancel_pipeline_instance(path, alarm_event_id)
        if applied:
            self.pipeline_cancels.append({"path": path, "alarmEventId": alarm_event_id})
        if self.pipeline_cancel_unclaimed:
            return 200, {}
        return 200, {"success": applied, "alarmEventId": alarm_event_id}

    # ------------------------------------------------------- config resources

    def next_signature(self) -> str:
        self.signature_serial += 1
        return f"sig-{self.signature_serial}"

    def _resource(self, resource_type: str, name: str, collection: str) -> dict[str, Any] | None:
        return (self.resources.get(resource_type) or {}).get((name, collection))

    def _singleton(self, resource_type: str) -> dict[str, Any] | None:
        entries = self.resources.get(resource_type) or {}
        if len(entries) != 1:
            return None
        return next(iter(entries.values()))

    def read_resource(self, path: str, query: dict[str, list[str]]) -> dict[str, Any] | None:
        """The recorded read behavior for one config-resource path (``None`` = 404)."""

        for verb in ("find", "singleton"):
            prefix = f"/data/api/v1/resources/{verb}/"
            if not path.startswith(prefix):
                continue
            remainder = path[len(prefix):]
            if verb == "singleton":
                return self._singleton(remainder)
            resource_type, _, name = remainder.rpartition("/")
            if not resource_type or not name:
                return None
            return self._resource(resource_type, name, _first(query, "collection", DEFAULT_COLLECTION))
        for verb in ("names", "list"):
            prefix = f"/data/api/v1/resources/{verb}/"
            if not path.startswith(prefix):
                continue
            entries = self.resources.get(path[len(prefix):])
            if entries is None:
                return None
            return _resource_collection(entries, query, names_only=verb == "names")
        return None

    def apply_resource_update(self, resource_type: str, body: bytes) -> tuple[int, dict[str, Any]]:
        """Apply one recorded Gateway resource PUT: validate all, then change all."""

        changes = _decode_changes(body)
        if changes is None:
            return 400, _INVALID_BODY
        entries = self.resources[resource_type]
        targets: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for change in changes:
            current = self._change_target(resource_type, entries, change)
            if current is None:
                return 404, _NO_SUCH_RESOURCE
            targets.append((change, current))
        if self.write_race.get("update"):
            fields = self.write_race.pop("update")
            for _change, current in targets:
                current.update(fields)
                current["signature"] = self.next_signature()
        if (status := self.write_status.get("update")) is not None:
            return status, {"message": "Internal Server Error", "status": str(status)}
        for change, current in targets:
            if change.get("signature") != current.get("signature"):
                return 409, _SIGNATURE_MISMATCH
        if (problem := self.write_problem.get("update")) is not None:
            return 200, _refused(problem)
        applied: list[dict[str, Any]] = []
        for change, current in targets:
            for key in ("config", "enabled", "description"):
                if key in change:
                    current[key] = change[key]
            current["signature"] = self.next_signature()
            applied.append(self._change_notice(resource_type, current))
        return 200, {"success": True, "changes": applied}

    def apply_resource_create(self, resource_type: str, body: bytes) -> tuple[int, dict[str, Any]]:
        """Apply one recorded Gateway resource POST: validate all, then create all.

        A create takes no Resource signature; an existing target is the Gateway's
        own D11 collision refusal (409), which the Tool must report as ``conflict``.
        """

        changes = _decode_changes(body)
        if changes is None:
            return 400, _INVALID_BODY
        entries = self.resources[resource_type]
        targets: list[tuple[dict[str, Any], str, str]] = []
        for change in changes:
            name, collection = self._create_identity(resource_type, entries, change)
            if name is None:
                return 400, _INVALID_BODY
            targets.append((change, name, collection))
        if (race := self.write_race.get("create")) is not None:
            self.write_race.pop("create")
            for _change, name, collection in targets:
                entries[(name, collection)] = self._document(
                    resource_type, name, collection, race,
                )
        if (status := self.write_status.get("create")) is not None:
            return status, {"message": "Internal Server Error", "status": str(status)}
        for _change, name, collection in targets:
            if (name, collection) in entries:
                return 409, {
                    "message": f"A resource named {name} already exists", "status": "409",
                }
        if (problem := self.write_problem.get("create")) is not None:
            return 200, _refused(problem)
        applied: list[dict[str, Any]] = []
        for change, name, collection in targets:
            document = self._document(resource_type, name, collection, change)
            entries[(name, collection)] = document
            applied.append(self._change_notice(resource_type, document))
        return 200, {"success": True, "changes": applied}

    def apply_resource_delete(
        self, resource_type: str, name: str, signature: str, collection: str,
    ) -> tuple[int, dict[str, Any]]:
        """Apply one recorded Gateway resource DELETE.

        The route carries the signature in its path, so the Gateway itself refuses a
        stale one (modelled as the same 409 the update route answers; the live Gateway
        cannot be reached from this fixture, and D30 §2 maps a stale token to
        ``conflict``).
        """

        entries = self.resources.get(resource_type)
        if entries is None:
            return 404, _NO_SUCH_RESOURCE
        current = (
            self._singleton(resource_type) if not name else entries.get((name, collection))
        )
        if current is None:
            return 404, _NO_SUCH_RESOURCE
        race = self.write_race.pop("delete", None)
        if race is not None:
            if race.get("deleted"):
                del entries[(str(current.get("name", name)), str(current.get("collection", "")))]
                current = None
            else:
                current.update(race)
                current["signature"] = self.next_signature()
        if (status := self.write_status.get("delete")) is not None:
            return status, {"message": "Internal Server Error", "status": str(status)}
        if current is None:
            return 404, _NO_SUCH_RESOURCE
        if current.get("signature") != signature:
            return 409, _SIGNATURE_MISMATCH
        if (problem := self.write_problem.get("delete")) is not None:
            return 200, _refused(problem)
        del entries[(str(current.get("name", name)), str(current.get("collection", "")))]
        return 200, {"success": True, "changes": [
            {"name": current.get("name", name), "type": resource_type.rsplit("/", 1)[-1],
             "collection": current.get("collection", "")},
        ]}

    def apply_resource_rename(
        self, resource_type: str, name: str, body: bytes, collection: str,
    ) -> tuple[int, dict[str, Any]]:
        """Apply one recorded Gateway resource rename POST.

        The endpoint takes no Resource signature (D30 §2), so a competing writer can
        change the resource inside the window between the caller's read-compare and
        this dispatch: the documented race, modelled by ``write_race``.
        """

        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            return 400, _INVALID_BODY
        if not isinstance(payload, dict) or not isinstance(payload.get("name"), str):
            return 400, _INVALID_BODY
        if payload.get("references") != "ABORT":
            return 400, {
                "message": "references must be ABORT", "status": "400",
            }
        entries = self.resources.get(resource_type)
        if entries is None:
            return 404, _NO_SUCH_RESOURCE
        current = entries.get((name, collection))
        if current is None:
            return 404, _NO_SUCH_RESOURCE
        new_name = str(payload["name"])
        if (new_name, collection) in entries:
            return 409, {"message": f"A resource named {new_name} already exists", "status": "409"}
        race = self.write_race.pop("rename", None)
        if race is not None:
            if race.get("deleted"):
                del entries[(name, collection)]
                current = None
            else:
                current.update(race)
                current["signature"] = self.next_signature()
        if (status := self.write_status.get("rename")) is not None:
            return status, {"message": "Internal Server Error", "status": str(status)}
        if current is None:
            return 404, _NO_SUCH_RESOURCE
        if (problem := self.write_problem.get("rename")) is not None:
            return 200, _refused(problem)
        del entries[(name, collection)]
        current["name"] = new_name
        current["signature"] = self.next_signature()
        entries[(new_name, collection)] = current
        return 200, {"success": True, "changes": [self._change_notice(resource_type, current)]}

    def _document(
        self, resource_type: str, name: str, collection: str, fields: dict[str, Any],
    ) -> dict[str, Any]:
        """One stored resource document, as creation leaves it."""

        document: dict[str, Any] = {
            "type": resource_type.rsplit("/", 1)[-1],
            "name": name,
            "enabled": bool(fields.get("enabled", True)),
            "description": str(fields.get("description", "")),
            "collection": collection,
            "signature": self.next_signature(),
            "config": fields.get("config", {}),
        }
        return document

    def _change_notice(self, resource_type: str, document: dict[str, Any]) -> dict[str, Any]:
        """One entry of the recorded ``changes`` list a write answers with."""

        return {
            "name": document.get("name", ""),
            "type": resource_type.rsplit("/", 1)[-1],
            "collection": document.get("collection", ""),
            "newSignature": document.get("signature"),
        }

    def _create_identity(
        self,
        resource_type: str,
        entries: dict[tuple[str, str], dict[str, Any]],
        change: dict[str, Any],
    ) -> tuple[str | None, str]:
        """The resource a create item names, or ``(None, "")`` for an invalid item.

        A non-singleton create item names its resource (its documented schema requires
        ``name``); a singleton's item declares no name and addresses the type's single
        resource, whose replacement identity is described by its own state.
        """

        collection = change.get("collection", DEFAULT_COLLECTION)
        if not isinstance(collection, str):
            return None, ""
        name = change.get("name")
        if name is None:
            existing = self._singleton(resource_type)
            name = str(existing.get("name", resource_type)) if existing else resource_type
        if not isinstance(name, str) or not name.strip():
            return None, ""
        return name, collection

    def _change_target(
        self,
        resource_type: str,
        entries: dict[tuple[str, str], dict[str, Any]],
        change: dict[str, Any],
    ) -> dict[str, Any] | None:
        """The resource one PUT change item addresses.

        A change item names its resource, or — for a singleton, whose documented
        item schema requires only ``signature`` — addresses the type's single
        resource. The collection selects between same-named resources.
        """

        name = change.get("name")
        collection = change.get("collection", DEFAULT_COLLECTION)
        if not isinstance(collection, str):
            return None
        if name is None:
            return self._singleton(resource_type)
        if not isinstance(name, str) or not name.strip():
            return None
        return entries.get((name, collection))


#: The response bodies the modelled Gateway answers with. They are modelled, not
#: recorded: no live Gateway is reachable from this fixture, and each shape is the
#: documented or decided one (409 for a signature mismatch and a collision, a 200
#: carrying ``success=false`` with a ``problem`` for a refusal).
_INVALID_BODY: dict[str, Any] = {"message": "Invalid request body", "status": "400"}
_NO_SUCH_RESOURCE: dict[str, Any] = {"message": "No such resource", "status": "404"}


#: Sentinels for a modelled route override that has not been set. ``object()`` rather
#: than ``None`` because ``None`` is itself a modelled body (an empty or JSON-null 2xx).
_UNSET: Any = object()
_SIGNATURE_MISMATCH: dict[str, Any] = {
    "message": "Signature mismatch: the resource changed after it was read",
    "status": "409",
}


def _refused(problem: str) -> dict[str, Any]:
    """A refusal the Gateway reports inside a 2xx resource response."""

    return {"success": False, "problem": {"message": problem, "stacktrace": []}}


def _decode_changes(body: bytes) -> list[dict[str, Any]] | None:
    """The change items of one resource write, or ``None`` for an invalid body."""

    try:
        changes = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(changes, list) or not changes:
        return None
    if not all(isinstance(change, dict) for change in changes):
        return None
    return [dict(change) for change in changes]


def _resource_collection(
    entries: dict[tuple[str, str], dict[str, Any]],
    query: dict[str, list[str]],
    *,
    names_only: bool,
) -> dict[str, Any]:
    limit = int(_first(query, "limit", "100"))
    offset = int(_first(query, "offset", "0"))
    search = _first(query, "search", "").lower()
    collection = _first(query, "collection", "")
    selected = {
        name: document for (name, item_collection), document in entries.items()
        if item_collection == collection
    }
    matching = sorted(name for name in selected if not search or search in name.lower())
    window = matching[offset:offset + limit]
    items = (
        [{"name": name, "enabled": bool(selected[name].get("enabled", True))} for name in window]
        if names_only
        else [selected[name] for name in window]
    )
    return {
        "items": items,
        "metadata": {
            "total": float(len(selected)),
            "matching": float(len(matching)),
            "limit": limit,
            "offset": offset,
        },
    }


def _first(query: dict[str, list[str]], key: str, default: str) -> str:
    values = query.get(key)
    return values[0] if values else default


def _pipeline_instance(path: str, instance: dict[str, Any]) -> dict[str, Any]:
    """One modelled Alarm Notification Pipeline run, as the status route serves it.

    ``alarmEventId`` is the identity the cancel route addresses a run by, so it is
    required; the other fields default to what a healthy run reports.
    """

    alarm_event_id = instance.get("alarmEventId")
    if not isinstance(alarm_event_id, str) or not alarm_event_id:
        raise ValueError("a modelled pipeline instance needs a non-empty alarmEventId")
    return {
        "pipelinePath": instance.get("pipelinePath", path),
        "source": instance.get("source", ""),
        "displayPath": instance.get("displayPath", ""),
        "blockName": instance.get("blockName", ""),
        "status": instance.get("status", "Running"),
        "millis": instance.get("millis", 0),
        "alarmEventId": alarm_event_id,
    }


def _tag_copy(node: dict[str, Any]) -> dict[str, Any]:
    """A deep copy of one Tag node, so a caller cannot mutate the fixture's state."""

    return json.loads(json.dumps(node))


def _tag_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_tag_copy(node) for node in nodes]


def _tag_segments(path: str) -> list[str]:
    return [segment for segment in path.split("/") if segment]


def _join_tag_path(path: str, name: str) -> str:
    return f"{path}/{name}" if path else name


def _tag_child(node: dict[str, Any], name: str) -> dict[str, Any] | None:
    children = node.get("tags")
    if not isinstance(children, list):
        return None
    for child in children:
        if isinstance(child, dict) and child.get("name") == name:
            return child
    return None


def _drop_tag(node: dict[str, Any], name: str) -> None:
    children = node.get("tags")
    if isinstance(children, list):
        node["tags"] = [
            child for child in children
            if not (isinstance(child, dict) and child.get("name") == name)
        ]


def _decode_tag_document(body: bytes) -> dict[str, Any] | None:
    """The Tag document one import body carries, or ``None`` for an unusable body."""

    try:
        document = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(document, dict):
        return None
    if "tags" in document and not isinstance(document["tags"], list):
        return None
    return dict(document)


def _incoming_tag_nodes(document: dict[str, Any]) -> list[dict[str, Any]]:
    """The Tag nodes one import document creates under the target path.

    A document that names its own root imports that root (the same reading the export
    of a sub-path produces, ``{"name": "source", "tagType": "Folder", ...}``); one that
    does not — a provider-root export — contributes its ``tags`` entries, which is the
    shape the Runtime plane imports live.
    """

    name = document.get("name")
    if isinstance(name, str) and name:
        return [document]
    children = document.get("tags")
    if isinstance(children, list) and children:
        return [
            child for child in children
            if isinstance(child, dict) and isinstance(child.get("name"), str) and child.get("name")
        ]
    return []


def _tag_import_success(count: int, shape: str) -> Any:
    """A successful import in one of the two observed wire shapes."""

    if shape == "list":
        return []
    return {"failureCount": 0, "failures": [], "successCount": count}


def _tag_import_failures(
    messages: list[str], shape: str, *, success_count: int = 0, sub_code: int = 776,
) -> Any:
    """A reported import failure in one of the two observed wire shapes.

    The summary object is what live 8.3.8/8.3.9 answer (recorded in
    ``phase4/tag-import-abort.json`` for the ``Abort`` collision refusal, whose
    ``qualitySubCode`` is 527, and in ``phase4/tag-import-provider-not-ready.json``
    for a provider that is still starting, whose sub-code is 776); the list of non-Good
    QualityCodes is what the committed 8.3.8 OpenAPI documents.
    """

    if shape == "list":
        return [
            {"level": "Bad", "userCode": sub_code, "diagnosticMessage": message}
            for message in messages
        ]
    return {
        "failureCount": len(messages),
        "failures": [
            {"diagnosticMessage": message, "quality": "Bad", "qualitySubCode": sub_code}
            for message in messages
        ],
        "successCount": success_count,
    }


class RecordedGateway:
    """Context-managed replay of recorded Gateway behavior on an ephemeral port."""

    def __init__(
        self,
        *,
        projects: dict[str, bytes] | None = None,
        openapi_missing_responses: int = 0,
        quarantined_modules: tuple[str, ...] = (),
        runtime_tools: tuple[str, ...] = (),
        runtime_resources: tuple[str, ...] = (),
        source_revision: str = "UNSTAMPED",
        bundle_version: str = "0.2.0",
        policy_provider: str = "",
        port: int = 0,
    ) -> None:
        self._server = _Server(
            projects or {},
            openapi_missing_responses,
            quarantined_modules,
            runtime_tools,
            runtime_resources,
            source_revision,
            bundle_version,
            policy_provider,
            port,
        )
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}"

    @property
    def mcp_url(self) -> str:
        return self.base_url + "/data/mcp/phase3-runtime"

    @property
    def requests(self) -> list[dict[str, Any]]:
        return self._server.requests

    @property
    def imports(self) -> list[str]:
        return self._server.imports

    # ------------------------------------------------------------- projects

    def project(self, name: str) -> bytes | None:
        """The Project archive the Gateway currently serves for ``name``."""

        return self._server.projects.get(name)

    def change_project_out_of_band(self, name: str, entries: dict[str, bytes]) -> None:
        """Change a Project without the MCP server, as another operator would; a
        fingerprint read before the change is now stale."""

        self._server.change_project(name, entries)

    def change_project_after_exports(
        self, name: str, exports: int, entries: dict[str, bytes],
    ) -> None:
        """Schedule an external Project change that lands once ``exports`` exports have
        been served.

        With the baseline export being the first one, ``exports=1`` models a writer that
        changes the Project between baseline A and the D16 mandatory pre-import
        re-export A'.
        """

        self._server.project_change_after = (name, exports, dict(entries))

    def fail_imports_with(self, status: int = 500) -> None:
        """Model a Gateway that answers the Project import with ``status`` and applies
        nothing — the ambiguous dispatch boundary of D08."""

        self._server.write_status["import"] = status

    def refuse_imports_with(self, problem: str | None) -> None:
        """Model a Gateway that refuses the Project import inside a 200 response."""

        self._server.write_problem["import"] = problem

    def race_import_with(self, entries: dict[str, bytes]) -> None:
        """Model another writer importing ``entries`` at dispatch time (D30 §2's race)."""

        self._server.write_race["import"] = dict(entries)

    # ------------------------------------------------------------------ tags

    @property
    def tag_imports(self) -> list[dict[str, Any]]:
        """The Tag imports the fixture actually applied (provider, path, Tag names)."""

        return self._server.tag_imports

    def seed_tags(self, provider: str, tags: list[dict[str, Any]]) -> None:
        """Publish a Tag provider holding ``tags`` as its top-level nodes."""

        self._server.seed_tags(provider, tags)

    def tags(self, provider: str, path: str = "") -> dict[str, Any] | None:
        """The Tag node the fixture holds at ``path`` (``None`` = absent)."""

        return self._server.tag_node(provider, path)

    def change_tags_out_of_band(self, provider: str, path: str, tags: list[dict[str, Any]]) -> None:
        """Create Tags without the MCP server, as another operator would; the Tool's
        collision check and its re-export both see them."""

        self._server.apply_tag_write(provider, path, _tag_nodes(tags))

    def fail_tag_imports_with(self, status: int = 500) -> None:
        """Model a Gateway that answers the Tag import with ``status`` and applies
        nothing — the ambiguous dispatch boundary of D08."""

        self._server.write_status["tag_import"] = status

    def refuse_tag_imports_with(self, problem: str | None = None) -> None:
        """Model a Gateway that reports a failed import inside a 200 response."""

        self._server.write_problem["tag_import"] = (
            problem if problem is not None else "the import was refused"
        )

    def partial_tag_imports_with(self, applied: int) -> None:
        """Model a Gateway that imports ``applied`` nodes of the document and reports
        the rest as failures — the partial application D30 §2 does not call a claim."""

        self._server.tag_import_partial = applied

    def claim_tag_imports_without_applying(self) -> None:
        """Model a Gateway that answers a clean success and creates nothing.

        Modelled, not recorded: it is the case D30 §2's verification exists for, where
        the claim stands and the observed state does not.
        """

        self._server.tag_import_lies = True

    def race_tag_import_with(self, provider: str, path: str, tags: list[dict[str, Any]]) -> None:
        """Model another writer creating ``tags`` under ``path`` at dispatch time."""

        self._server.write_race["tag_import"] = {
            "provider": provider, "path": path, "tags": _tag_nodes(tags),
        }

    def answer_tag_import_failures_with(self, shape: str) -> None:
        """Which wire shape the import route reports failures with: the summary object
        live 8.3.8/8.3.9 answer, or the QualityCode list the 8.3.8 OpenAPI documents."""

        if shape not in {"summary", "list"}:
            raise ValueError("shape must be 'summary' or 'list'")
        self._server.tag_import_wire_shape = shape

    def answer_tag_import_with(self, body: Any) -> None:
        """Answer the Tag import with ``body`` inside a 200, whatever it did.

        Modelled, not recorded: the case is a Gateway whose 2xx report this server cannot
        interpret (a negative or non-numeric count, a count that disagrees with its
        failure list, a ``null`` body). The import itself still applies, so a Tool that
        treats the unreadable report as a success would return one here.
        """

        self._server.tag_import_body = body

    # ------------------------------------------------------- alarm pipelines

    @property
    def pipeline_cancels(self) -> list[dict[str, Any]]:
        """The pipeline cancels the fixture actually applied (path, alarm event)."""

        return self._server.pipeline_cancels

    def seed_pipeline(self, path: str, instances: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Publish one modelled pipeline path holding ``instances`` runs.

        Each instance needs a non-empty ``alarmEventId``; ``status``, ``source``,
        ``displayPath``, ``blockName`` and ``millis`` are optional, and
        ``pipelinePath`` defaults to ``path``. A path with no published state answers
        exactly as a Gateway with no such pipeline does: ``not_found``.
        """

        return self._server.seed_pipeline(path, instances)

    def pipeline(self, path: str) -> list[dict[str, Any]] | None:
        """The runs the fixture holds for ``path`` (``None`` = no such path)."""

        return self._server.pipeline_instances(path)

    def cancel_pipeline_out_of_band(self, path: str, alarm_event_id: str) -> bool:
        """Cancel one pipeline run without the MCP server, as an operator would.

        The Tool's verification re-reads afterwards, so a run removed here and a run
        this server cancelled are indistinguishable in the observed state — exactly
        the attribution boundary D30 §2 addresses.
        """

        return self._server.cancel_pipeline_instance(path, alarm_event_id)

    def refuse_cancels_with(self, problem: str | None = None) -> None:
        """Model a Gateway that refuses a pipeline cancel inside a 200 response.

        The documented route answers ``{"success": bool, "alarmEventId": str}``; a pair
        the pipeline does not hold is refused with ``success: false``. The shape is
        modelled, not recorded, like every other refusal this fixture carries.
        """

        self._server.pipeline_cancel_refusal = (
            problem if problem is not None else "No such alarm event is running on this pipeline"
        )

    def answer_cancels_without_claiming(self) -> None:
        """Model a Gateway that answers 200 with no ``success`` field at all.

        Modelled, not recorded: the route's answer is readable as neither a claim nor a
        refusal, which is exactly the case D30 §2 must not report as a success. The
        cancel is applied, so only the claim is missing.
        """

        self._server.pipeline_cancel_unclaimed = True

    def claim_cancels_without_applying(self) -> None:
        """Model a Gateway that claims a clean cancel and leaves the run in place.

        Modelled, not recorded: it is the case D30 §2's verification exists for, where
        the claim stands and the observed state does not.
        """

        self._server.pipeline_cancel_lies = True

    def fail_cancels_with(self, status: int = 500) -> None:
        """Model a Gateway that answers a cancel with ``status`` and applies nothing —
        the ambiguous dispatch boundary of D08."""

        self._server.pipeline_cancel_status = status

    def race_cancel_with(self, path: str, alarm_event_id: str) -> None:
        """Model another operator cancelling the same run at dispatch time (D30 §2)."""

        self._server.pipeline_cancel_race = {"path": path, "alarmEventId": alarm_event_id}

    # ------------------------------------------------------- config resources

    def seed_resource(
        self,
        resource_type: str,
        name: str,
        *,
        config: dict[str, Any] | None = None,
        enabled: bool = True,
        description: str = "",
        collection: str = DEFAULT_COLLECTION,
    ) -> dict[str, Any]:
        """Publish one config resource, returning the stored document.

        The collection defaults to the Gateway's own default (``core``); a case that
        needs a look-alike in another collection names it.
        """

        document: dict[str, Any] = {
            "type": resource_type.rsplit("/", 1)[-1],
            "name": name,
            "enabled": enabled,
            "description": description,
            "collection": collection,
            "signature": self._server.next_signature(),
            "config": config if config is not None else {},
        }
        self._server.resources.setdefault(resource_type, {})[(name, collection)] = document
        return document

    def resource(
        self, resource_type: str, name: str, collection: str = DEFAULT_COLLECTION,
    ) -> dict[str, Any]:
        return self._server.resources[resource_type][(name, collection)]

    def signature(self, resource_type: str, name: str, collection: str = DEFAULT_COLLECTION) -> str:
        return str(self.resource(resource_type, name, collection)["signature"])

    def refuse_writes_with(self, operation: str, problem: str | None) -> None:
        """Model a Gateway that answers 200 with ``success=false`` for one operation.

        ``operation`` is the write the Tool performs: ``update`` (PUT), ``create``
        (POST on the collection route), ``delete`` (DELETE) or ``rename`` (POST on
        the rename route).
        """

        self._server.write_problem[operation] = problem

    def fail_writes_with(self, operation: str, status: int = 500) -> None:
        """Model a Gateway that answers one write operation with ``status``.

        Nothing is applied, and the caller cannot tell whether its write landed before
        the failure — the ambiguous dispatch boundary of D08.
        """

        self._server.write_status[operation] = status

    def race_write_with(self, operation: str, **fields: Any) -> None:
        """Model another writer acting on the target at dispatch time.

        The competing change lands after this harness's pre-dispatch read and before
        the Gateway's own check, which is the deterministic race each operation's
        Precondition token is meant to narrow (D30 §2). Any field models the writer
        changing the resource; ``deleted=True`` models it *removing* the target, which
        is what a delete or a rename cannot tell apart from its own work.
        """

        self._server.write_race[operation] = dict(fields)

    # The update shorthand the reviewed #14 cases call; the generic methods above are
    # the same mechanism, named for the operation each one models.
    def race_update_with(self, **fields: Any) -> None:
        self.race_write_with("update", **fields)

    def fail_updates_with(self, status: int = 500) -> None:
        self.fail_writes_with("update", status)

    def change_resource_out_of_band(
        self, resource_type: str, name: str, collection: str = DEFAULT_COLLECTION, **fields: Any,
    ) -> str:
        """Change a resource without the MCP server, as another operator would; the
        stored signature moves, so a token read before the change is now stale."""

        document = self.resource(resource_type, name, collection)
        document.update(fields)
        document["signature"] = self._server.next_signature()
        return str(document["signature"])

    def refuse_updates_with(self, problem: str | None) -> None:
        """Model a Gateway that answers 200 with ``success=false`` for every PUT."""

        self.refuse_writes_with("update", problem)

    def restart(self) -> None:
        """Apply an accepted shared certificate as the recorded Gateway restart did."""
        if self._server.certificate_accepted:
            self._server.modules_active = True
            self._server.quarantined_modules.clear()

    def __enter__(self) -> RecordedGateway:
        self._thread.start()
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
