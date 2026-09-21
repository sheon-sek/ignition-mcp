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
    # Phase 4 ticket #6 characterization: the Native REST write path for the
    # deployment-owned Runtime Target Policy provider and its Tags.
    ("post", "/data/api/v1/resources/ignition/tag-provider"),
    ("get", "/data/api/v1/resources/find/ignition/tag-provider/{name}"),
    ("get", "/data/api/v1/resources/type/ignition/tag-provider"),
    # Phase 4 ticket #7: the audit profile the Runtime audit mode names, and the
    # audit log the recorded attempt/result rows are read back from.
    ("get", "/data/api/v1/resources/find/ignition/audit-profile/{name}"),
    ("get", "/data/api/v1/audit/log/{name}"),
)

#: Path segments under ``/data/api/v1/resources/`` that name an operation rather
#: than a resource type, so a collection path can never be confused with them.
_RESERVED_RESOURCE_SEGMENTS = frozenset({
    "copy", "delete", "move", "rename", "datafile", "names", "list", "find", "singleton", "type",
})


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


#: The repository's committed 8.3.8 specification. The recorded Gateway advertises
#: the *real* PUT request body for a resource type it serves: an update route
#: without the request schema D03 requires cannot be exposed, so a schema-less stub
#: would silently disable the Tool under test instead of exercising it.
COMMITTED_OPENAPI = ROOT / "docs/ignition-8.3.8-openapi/openapi.min.json"
_committed_openapi: dict[str, Any] | None = None


def _committed_update_body(resource_type: str) -> dict[str, Any] | None:
    global _committed_openapi
    if _committed_openapi is None:
        _committed_openapi = json.loads(COMMITTED_OPENAPI.read_text(encoding="utf-8"))
    operation = (_committed_openapi.get("paths") or {}).get(
        f"/data/api/v1/resources/{resource_type}",
    )
    body = (operation or {}).get("put", {}).get("requestBody")
    return body if isinstance(body, dict) else None


def _recorded_operation(method: str, operation_path: str) -> dict[str, Any]:
    if method != "put":
        return {}
    resource_type = _resource_type_segment(operation_path)
    body = _committed_update_body(resource_type) if resource_type else None
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
    global _committed_openapi
    if _committed_openapi is None:
        _committed_openapi = json.loads(COMMITTED_OPENAPI.read_text(encoding="utf-8"))
    node: Any = _committed_openapi
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


GOOD_QUALITY = {
    "code": 192, "name": "Good", "level": "Good", "good": True, "diagnosticMessage": None,
}


def _record_tag_import(server: Any, body: bytes) -> None:
    """Track the policy document an accepted import leaves in the provider.

    `setup-native apply` (and the ticket #7 harness) verifies the *served*
    document through a handler-scope read, so the fake has to remember what it
    was asked to write instead of only replaying the response body.
    """
    try:
        document = json.loads(body)
    except (ValueError, TypeError):
        return
    if not isinstance(document, dict):
        return
    for entry in document.get("tags") or []:
        if not isinstance(entry, dict):
            continue
        value = entry.get("value")
        if entry.get("name") == "RuntimeTargetPolicy" and isinstance(value, str):
            server.policy_value = value
        if isinstance(value, str) and "WriteProbe" == entry.get("name"):
            server.write_probe_value = value


def _tag_read_replay(server: Any, arguments: dict[str, Any]) -> dict[str, Any] | None:
    """The recorded `tag_read` domain for a path the fake models, else None.

    The fake answers an invalid path with the recorded Tool Error the Phase 3
    rehearsal relies on, so only modeled paths take this branch.
    """
    paths = [str(path) for path in arguments.get("tagPaths") or []]
    if not paths:
        return None
    items = []
    for path in paths:
        if path == "[IgnitionMCPPolicy]RuntimeTargetPolicy" and server.policy_value:
            items.append({"path": path, "status": "ok", "value": server.policy_value,
                          "quality": GOOD_QUALITY, "timestamp": "2026-09-22T00:00:00Z"})
            continue
        if path == "[IgnitionMCPPolicy]WriteProbe" and server.write_probe_value:
            items.append({"path": path, "status": "ok", "value": server.write_probe_value,
                          "quality": GOOD_QUALITY, "timestamp": "2026-09-22T00:00:00Z"})
            continue
        if path in server.tag_state:
            items.append({"path": path, "status": "ok", "value": server.tag_state[path],
                          "quality": GOOD_QUALITY, "timestamp": "2026-09-22T00:00:00Z"})
            continue
        return None
    return {
        "items": items,
        "summary": {"requested": len(paths), "succeeded": len(items), "failed": 0},
        "meta": {"correlationId": "recorded-replay"},
    }


def _tag_write_case(server: Any, arguments: dict[str, Any]) -> tuple[str, list[str]]:
    """Which recorded `tag_write` body the fake replays for these arguments."""
    writes = arguments.get("writes")
    paths = [
        str(item.get("path", ""))
        for item in (writes or []) if isinstance(item, dict)
    ]
    if not server.policy_provider_created:
        return "no-policy", paths
    if any(path.startswith("[IgnitionMCPPolicy]") for path in paths):
        return "reserved-provider-refusal", paths
    if any("IgnitionMCP_CI2" in path for path in paths):
        return ("preflight-refusal" if len(paths) > 1 else "sibling-denial"), paths
    return "allowlisted-batch", paths


def _apply_tag_write_case(server: Any, case: str, body: dict[str, Any]) -> None:
    if case != "allowlisted-batch":
        return
    structured = body.get("structuredContent") or {}
    server.tag_write_correlation_id = str((structured.get("meta") or {}).get("correlationId", ""))
    for item in structured.get("observed") or []:
        if isinstance(item, dict) and item.get("status") == "ok" and item.get("path") in server.tag_state:
            server.tag_state[str(item["path"])] = item.get("value")


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
            project = server.projects.get(name)
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
        if path.startswith("/data/api/v1/audit/log/"):
            if server.tag_write_correlation_id:
                document = json.loads(json.dumps(_fixture("phase4/audit-log.json")))
                for row in document["items"]:
                    row["actionValue"] = str(row["actionValue"]).replace("__CORRELATION__", server.tag_write_correlation_id)
                self._json(200, document)
                return
            self._json(200, {"items": [], "metadata": {"total": 0.0, "matching": 0.0, "limit": 100, "offset": 0}})
            return
        if path.startswith("/data/api/v1/resources/find/ignition/tag-provider/"):
            name = path.rsplit("/", 1)[-1]
            if name != server.policy_provider:
                self._json(404, {"message": "No resource", "status": "404"})
                return
            if not server.policy_provider_created:
                self._json(404, {"message": "No resource", "status": "404"})
                return
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
            provider = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("provider", [""])[0]
            if provider == server.policy_provider:
                if not server.policy_provider_created:
                    self._json(404, {"message": "No tag provider", "status": "404"})
                    return
                self._send(200, json.dumps(
                    _fixture("phase4/tag-export.json"), separators=(",", ":"),
                ).encode("utf-8"), "application/octet-stream")
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
                if str(path).endswith("phase4-operator"):
                    operator_tools = [
                        str(item["name"]) for item in _fixture("phase4/tools-list-operator.json")["tools"]
                    ]
                else:
                    operator_tools = list(server.runtime_tools)
                self._json(200, {
                    "jsonrpc": "2.0",
                    "id": payload.get("id"),
                    "result": {"tools": [
                        {"name": name, "description": f"{name} recorded replay"}
                        for name in operator_tools
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
                params = payload.get("params") or {}
                tool = str(params.get("name"))
                tool_arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
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
                    domain = _tag_read_replay(server, tool_arguments)
                    if domain is None:
                        result = {
                            "content": [{"type": "text", "text": "tag path is not valid"}],
                            "isError": True,
                        }
                    else:
                        result = {
                            "content": [{"type": "text", "text": "recorded replay"}],
                            "isError": False,
                            "structuredContent": domain,
                        }
                elif tool == "tag_fixture_probe":
                    report = json.loads(json.dumps(_fixture("phase4/tag-fixture-probe.json")))
                    for entry in report.get("initialValues") or []:
                        if isinstance(entry, dict) and isinstance(entry.get("path"), str):
                            raw = str(entry.get("value"))
                            server.tag_state[entry["path"]] = int(raw) if raw.lstrip("-").isdigit() else raw
                    server.tag_state.setdefault("[default]IgnitionMCP_CI/Nested/Inner", 0)
                    result = {
                        "content": [{"type": "text", "text": "recorded replay"}],
                        "isError": False,
                        "structuredContent": report,
                    }
                elif tool == "tag_write":
                    case, _paths = _tag_write_case(server, tool_arguments)
                    body = json.loads(json.dumps(_fixture(f"phase4/tag-write-{case}.json")))
                    _apply_tag_write_case(server, case, body)
                    result = body
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
        if path.startswith("/data/api/v1/projects/import/"):
            name = path.rsplit("/", 1)[-1]
            server.projects[name] = _gateway_import(server.projects.get(name), body)
            server.imports.append(name)
            self._json(200, {"message": f"Project {name} imported"})
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
            _record_tag_import(server, body)
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
        audit_profile: str = "",
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
        # Ticket #7: the fake models Tag values so a `tag_read` after a Mutation
        # reports what that Mutation recorded, and the audit log can answer with
        # the correlation ID the recorded `tag_write` result carried.
        self.audit_profile = audit_profile
        self.tag_state: dict[str, Any] = {}
        self.tag_write_correlation_id = ""
        self.policy_value = ""
        self.write_probe_value = "phase4-write-probe-value"
        #: config resource state: resource type -> (name, collection) -> document.
        #: Keying by collection as well as name is what makes the fixture able to
        #: tell two resources with one name in different collections apart. Seed it
        #: through :meth:`RecordedGateway.seed_resource`.
        self.resources: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}
        self.signature_serial = 0
        #: Modelled (not recorded) Gateway rejection: when set, every resource PUT
        #: answers 200 with ``success=false`` and this ``problem`` message, the
        #: documented shape for a change the Gateway refused to apply.
        self.update_problem: str | None = None
        #: Modelled (not recorded) competing writer: when set, the addressed resource
        #: is changed (and its signature moved) at PUT time, after the caller's own
        #: read and before the signature check — the race window D30 §2 documents.
        self.update_race: dict[str, Any] = {}
        #: Modelled (not recorded) ambiguous dispatch: when set, every resource PUT
        #: answers this status (500 by default) without applying the change, the
        #: boundary at which the caller cannot know whether its write landed.
        self.update_status: int | None = None

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
            return self._resource(resource_type, name, _first(query, "collection", ""))
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

        try:
            changes = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            return 400, {"message": "Invalid request body", "status": "400"}
        if not isinstance(changes, list) or not changes:
            return 400, {"message": "Invalid request body", "status": "400"}
        entries = self.resources[resource_type]
        targets: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for change in changes:
            if not isinstance(change, dict):
                return 400, {"message": "Invalid request body", "status": "400"}
            current = self._change_target(resource_type, entries, change)
            if current is None:
                return 404, {"message": "No such resource", "status": "404"}
            targets.append((change, current))
        if self.update_race:
            fields, self.update_race = self.update_race, {}
            for _change, current in targets:
                current.update(fields)
                current["signature"] = self.next_signature()
        if self.update_status is not None:
            return self.update_status, {
                "message": "Internal Server Error", "status": str(self.update_status),
            }
        for change, current in targets:
            if change.get("signature") != current.get("signature"):
                return 409, {
                    "message": "Signature mismatch: the resource changed after it was read",
                    "status": "409",
                }
        if self.update_problem is not None:
            return 200, {"success": False, "problem": {"message": self.update_problem, "stacktrace": []}}
        applied: list[dict[str, Any]] = []
        for change, current in targets:
            for key in ("config", "enabled", "description"):
                if key in change:
                    current[key] = change[key]
            current["signature"] = self.next_signature()
            applied.append({
                "name": current.get("name", ""),
                "type": resource_type.rsplit("/", 1)[-1],
                "collection": current.get("collection", ""),
                "newSignature": current["signature"],
            })
        return 200, {"success": True, "changes": applied}

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
        collection = change.get("collection", "")
        if not isinstance(collection, str):
            return None
        if name is None:
            return self._singleton(resource_type)
        if not isinstance(name, str) or not name.strip():
            return None
        return entries.get((name, collection))


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
        audit_profile: str = "",
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
            audit_profile,
        )
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        if audit_profile:
            # The Phase 4 policy names an audit profile the harness creates
            # through Native REST; publish it as a config resource so the fake
            # answers the same find the live Gateway does.
            self.seed_resource(
                "ignition/audit-profile", audit_profile,
                config={"profile": {"type": "local"}, "settings": {}},
                enabled=True, description="Disposable Phase 4 CI local audit profile",
            )

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

    # ------------------------------------------------------- config resources

    def seed_resource(
        self,
        resource_type: str,
        name: str,
        *,
        config: dict[str, Any] | None = None,
        enabled: bool = True,
        description: str = "",
        collection: str = "",
    ) -> dict[str, Any]:
        """Publish one config resource, returning the stored document."""

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

    def resource(self, resource_type: str, name: str, collection: str = "") -> dict[str, Any]:
        return self._server.resources[resource_type][(name, collection)]

    def signature(self, resource_type: str, name: str, collection: str = "") -> str:
        return str(self.resource(resource_type, name, collection)["signature"])

    def race_update_with(self, **fields: Any) -> None:
        """Model another writer changing the addressed resource at PUT time.

        The competing change lands after this harness's pre-dispatch read and before
        the Gateway's signature check, so the Gateway refuses the caller's own change
        with a signature mismatch: the deterministic race D30 §2 documents.
        """

        self._server.update_race = dict(fields)

    def fail_updates_with(self, status: int = 500) -> None:
        """Model a Gateway that answers every resource PUT with ``status``.

        Nothing is applied, and the caller cannot tell whether its write landed before
        the failure — the ambiguous dispatch boundary of D08.
        """

        self._server.update_status = status

    def change_resource_out_of_band(
        self, resource_type: str, name: str, collection: str = "", **fields: Any,
    ) -> str:
        """Change a resource without the MCP server, as another operator would; the
        stored signature moves, so a token read before the change is now stale."""

        document = self.resource(resource_type, name, collection)
        document.update(fields)
        document["signature"] = self._server.next_signature()
        return str(document["signature"])

    def refuse_updates_with(self, problem: str | None) -> None:
        """Model a Gateway that answers 200 with ``success=false`` for every PUT."""

        self._server.update_problem = problem

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
