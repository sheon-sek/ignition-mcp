"""Bounded, read-only Gateway REST probes (D04, D20).

Only GETs against documented read-only routes.  Write routes are never called:
capability presence is decided from the ``/openapi.json`` path inventory alone.
Any credential that could still surface in a Gateway response body is scrubbed
before it reaches a report.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from ignition_rest_mcp.cli.gateway_ops.inputs import Endpoint

JSON_LIMIT_BYTES = 1_048_576
#: The EULA route answers HTML; only its size is ever read, never its text.
EULA_LIMIT_BYTES = 1_048_576
#: An error body is read to this bound and then reported as a 160-character snippet.
ERROR_BODY_LIMIT_BYTES = 16_384
OPENAPI_LIMIT_BYTES = 16 * 1024 * 1024
ERROR_BODY_SNIPPET = 160

MCP_MODULE_ID = "com.inductiveautomation.mcp"
GATEWAY_INFO_PATH = "/data/api/v1/gateway-info"
MODULES_PATH = "/data/api/v1/modules/healthy"
#: The two reads that tell ``install-module`` what the module carries: a signing
#: certificate to show the operator and an EULA to point them at.
MODULE_CERTIFICATE_PATH = "/data/api/v1/modules/certificate"
MODULE_EULA_PATH = "/data/api/v1/modules/eula"
PROJECT_FIND_PATH = "/data/api/v1/projects/find/{name}"
PROJECT_EXPORT_PATH = "/data/api/v1/projects/export/{name}"
#: The bound on one Project export read through this client (D10).
PROJECT_ARCHIVE_LIMIT_BYTES = 64 * 1024 * 1024
SERVER_CONFIG_FIND_PATH = "/data/api/v1/resources/find/com.inductiveautomation.mcp/server-config/{name}"
#: The find route of any config resource type, for the reads ``apply`` reasons over
#: (the reserved policy Tag provider's resource).
RESOURCE_FIND_PATH = "/data/api/v1/resources/find/{resource_type}/{name}"
#: The Tag export route: the documented read-back of a Tag provider's content.
TAGS_EXPORT_PATH = "/data/api/v1/tags/export"
SECURITY_LEVELS_PATH = "/data/api/v1/resources/singleton/ignition/security-levels"
#: The find route of a resource *type's* single resource (the Security Levels tree).
SINGLETON_PATH = "/data/api/v1/resources/singleton/{resource_type}"
API_TOKEN_PATH = "/data/api/v1/resources/ignition/api-token"
DESIGNERS_PATH = "/data/api/v1/designers"
PROJECT_IMPORT_PATH = "/data/api/v1/projects/import/{name}"
#: ``modules/healthy`` is a paged collection: one page asks for this many entries, and
#: the inventory read is bounded at this many pages. A Gateway that serves more is an
#: error, never an absence.
MODULE_PAGE_SIZE = 500
MODULE_PAGES_MAX = 4

#: Diagnostic capability questions, in report order.  Each is answered from the
#: OpenAPI path inventory; the routes are never called (no write dispatch).
CAPABILITY_ENDPOINTS: dict[str, tuple[str, str]] = {
    "server-config": ("GET", SERVER_CONFIG_FIND_PATH),
    "project-import": ("POST", PROJECT_IMPORT_PATH),
    "security-levels": ("GET", SECURITY_LEVELS_PATH),
    "api-token": ("POST", API_TOKEN_PATH),
    "designers": ("GET", DESIGNERS_PATH),
}
CAPABILITY_ORDER = tuple(CAPABILITY_ENDPOINTS)
#: Presence of the read-only project lookup the bundle-project probe needs.
PROJECT_FIND_ENDPOINT = ("GET", PROJECT_FIND_PATH.format(name="{name}"))

GATEWAY_VERSION_RE = re.compile(r"^\s*(\d+\.\d+\.\d+) \(b(\d{10})\)\s*$")
#: A module *display* version as served by ``/data/api/v1/modules/healthy``:
#: ``1.3.5-SNAPSHOT (b2026021307)``. This is the display form, not the artifact form.
MODULE_DISPLAY_VERSION_RE = re.compile(r"^([0-9][0-9A-Za-z._-]*?) \(b([0-9]{10})\)$")
MANAGED_MARKER_PREFIX = "ignition-mcp-managed:"
MANAGED_MARKER_RE = re.compile(r"^ignition-mcp-managed:\s*product=(\S+)\s*;\s*bundle=(\S+)\s*$")
MANAGED_PRODUCT = "ignition-runtime-bundle"
SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")

ABSENT = "ABSENT"
MANAGED = "MANAGED"
UNMANAGED_SAME_NAME = "UNMANAGED_SAME_NAME"
MARKER_INVALID = "MARKER_INVALID"


class GatewayProbeError(RuntimeError):
    """A Gateway probe failed; the message is safe to report."""


@dataclass(frozen=True, slots=True)
class Probe:
    """A completed bounded Gateway response."""

    status_code: int
    content: bytes


@dataclass(frozen=True, slots=True)
class ModuleIdentity:
    """What the module list says about the MCP Module."""

    raw_version: str
    version: str | None
    build: str | None


@dataclass(frozen=True, slots=True)
class ProjectState:
    """Ownership classification of the bundle project name (D20 marker)."""

    name: str
    classification: str
    bundle_version: str | None
    inheritable: bool | None

    @property
    def managed(self) -> bool:
        return self.classification == MANAGED


def parse_gateway_identity(ignition_version: Any) -> tuple[str | None, str | None]:
    """``"8.3.8 (b2026071409)"`` → ``("8.3.8", "2026071409")``; anything else ``(None, None)``."""

    if isinstance(ignition_version, str):
        match = GATEWAY_VERSION_RE.fullmatch(ignition_version)
        if match is not None:
            return match.group(1), match.group(2)
    return None, None


def parse_module_identity(raw_version: Any) -> ModuleIdentity | None:
    """``1.3.5.2026021307-SNAPSHOT`` → ``("1.3.5-SNAPSHOT", "2026021307")`` and
    ``1.3.5-SNAPSHOT (b2026021307)`` → ``("1.3.5-SNAPSHOT", "2026021307")``.

    Two live shapes exist: the ``bundle_info`` handler and module resource files
    report the four-segment artifact version; ``GET /data/api/v1/modules/healthy``
    reports the *display* version with a ``(b<build>)`` suffix (live finding, G3
    run 35589924939). The dot form keeps the handler's rule: the fourth
    dot-separated segment, minus any qualifier suffix, is the build when it is
    ten digits.
    """

    if not isinstance(raw_version, str) or not raw_version.strip():
        return None
    value = raw_version.strip()
    display = MODULE_DISPLAY_VERSION_RE.fullmatch(value)
    if display is not None:
        return ModuleIdentity(raw_version=value, version=display.group(1), build=display.group(2))
    parts = value.split(".")
    logical = value
    build: str | None = None
    if len(parts) > 3:
        candidate = parts[3].split("-")[0]
        if len(candidate) == 10 and candidate.isdigit():
            build = candidate
            logical = ".".join(parts[:3]) + parts[3][len(candidate):]
    return ModuleIdentity(raw_version=value, version=logical, build=build)


def classify_project(name: str, document: dict[str, Any] | None) -> ProjectState:
    """Classify the bundle project from the last line of its ``project.json`` description."""

    if document is None:
        return ProjectState(name=name, classification=ABSENT, bundle_version=None, inheritable=None)
    raw_inheritable = document.get("inheritable")
    inheritable = raw_inheritable if isinstance(raw_inheritable, bool) else None
    description = document.get("description")
    if not isinstance(description, str) or not description.strip():
        return ProjectState(name, UNMANAGED_SAME_NAME, None, inheritable)
    marker_line = description.rstrip().splitlines()[-1].strip()
    if not marker_line.startswith(MANAGED_MARKER_PREFIX):
        return ProjectState(name, UNMANAGED_SAME_NAME, None, inheritable)
    match = MANAGED_MARKER_RE.fullmatch(marker_line)
    if match is None or match.group(1) != MANAGED_PRODUCT or SEMVER_RE.fullmatch(match.group(2)) is None:
        return ProjectState(name, MARKER_INVALID, None, inheritable)
    return ProjectState(name, MANAGED, match.group(2), inheritable)


class GatewayRest:
    """Async read-only Gateway probes over one bounded ``httpx.AsyncClient``."""

    def __init__(
        self,
        endpoint: Endpoint,
        api_token: str,
        *,
        timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._token = api_token
        self._endpoint = endpoint
        self._client = httpx.AsyncClient(
            base_url=endpoint.url,
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "X-Ignition-API-Token": api_token,
                "User-Agent": "ignition-mcp",
            },
            follow_redirects=False,
            timeout=httpx.Timeout(timeout_seconds),
            transport=transport,
        )

    @property
    def endpoint(self) -> Endpoint:
        return self._endpoint

    async def __aenter__(self) -> GatewayRest:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def gateway_info(self) -> dict[str, Any]:
        document = await self.get_json(GATEWAY_INFO_PATH)
        if not isinstance(document, dict):
            raise GatewayProbeError("gateway-info did not return a JSON object")
        return document

    async def openapi(self) -> tuple[str, frozenset[tuple[str, str]]]:
        """SHA-256 of ``/openapi.json`` plus its ``(METHOD, path)`` inventory.

        The document is hashed and scanned for path keys only; it is never
        rendered into a report.
        """

        probe = await self._request("GET", "/openapi.json", limit_bytes=OPENAPI_LIMIT_BYTES)
        digest = hashlib.sha256(probe.content).hexdigest()
        document = _decode_json(probe.content, "/openapi.json")
        paths = document.get("paths") if isinstance(document, dict) else None
        if not isinstance(paths, dict):
            raise GatewayProbeError("openapi.json has no paths object")
        endpoints = frozenset(
            (str(method).upper(), str(path))
            for path, operations in paths.items()
            if isinstance(operations, dict)
            for method in operations
        )
        return digest, endpoints

    async def module_inventory(self) -> tuple[list[dict[str, Any]], bool]:
        """The healthy module entries, and whether the whole inventory was read.

        ``modules/healthy`` serves one page at a time, so absence is only a fact when
        ``metadata.total`` is reached or a page arrives short. The next offset advances
        by what was actually served, because a Gateway may answer a page of 500 with
        fewer. A caller that treats a partial page as absence could install over a
        build it never saw (D10).
        """

        items: list[dict[str, Any]] = []
        offset = 0
        for _ in range(MODULE_PAGES_MAX):
            document = await self.get_json(
                MODULES_PATH, params={"limit": str(MODULE_PAGE_SIZE), "offset": str(offset)},
            )
            entries = document.get("items") if isinstance(document, dict) else None
            if not isinstance(entries, list):
                raise GatewayProbeError("modules/healthy returned no items list")
            items.extend(item for item in entries if isinstance(item, dict))
            served = len(entries)
            offset += served
            total = _metadata_total(document)
            if total is None:
                if served < MODULE_PAGE_SIZE:
                    return items, True
            elif offset >= total:
                return items, True
            if served == 0:
                # Nothing came back and the reported total is still ahead: this Gateway
                # is not paging, so the inventory cannot be completed here.
                return items, False
        return items, False

    async def mcp_module(self) -> ModuleIdentity | None:
        """Identity of the installed MCP Module, or ``None`` when it is absent."""

        return await self.module_identity(MCP_MODULE_ID)

    async def module_identity(self, module_id: str) -> ModuleIdentity | None:
        """Identity of one installed module, or ``None`` when a complete read lacks it."""

        items, complete = await self.module_inventory()
        if not complete:
            raise GatewayProbeError(
                f"modules/healthy serves more than {MODULE_PAGES_MAX * MODULE_PAGE_SIZE} entries, so "
                f"{module_id} was neither found nor proven absent; refusing to act on a partial list"
            )
        for item in items:
            if item.get("id") == module_id:
                identity = parse_module_identity(item.get("version"))
                if identity is None:
                    raise GatewayProbeError(f"the {module_id} entry carries no usable version")
                return identity
        return None

    async def module_certificate(self, module_id: str) -> dict[str, Any] | None:
        """The module's signing certificate, or ``None`` when it carries none."""

        document = await self.get_json(
            MODULE_CERTIFICATE_PATH, params={"moduleId": module_id}, allow_404=True,
        )
        return document if isinstance(document, dict) else None

    async def module_eula_size(self, module_id: str) -> int | None:
        """Bytes of EULA the module serves, or ``None`` when it carries none.

        The document is never rendered into a report: the operator reads it where the
        Gateway serves it, and this CLI only needs to know that there is one.
        """

        probe = await self._request(
            "GET", MODULE_EULA_PATH, params={"moduleId": module_id},
            limit_bytes=EULA_LIMIT_BYTES, allow_404=True,
        )
        if probe.status_code == 404:
            return None
        return len(probe.content)

    async def find_project(self, name: str) -> ProjectState:
        document = await self.get_json(PROJECT_FIND_PATH.format(name=quote(name, safe="")), allow_404=True)
        return classify_project(name, document if isinstance(document, dict) else None)

    async def project_archive(self, name: str) -> bytes:
        """The Project archive the Gateway exports, bounded; an absent project is an error."""

        path = PROJECT_EXPORT_PATH.format(name=quote(name, safe=""))
        return (await self._request("GET", path, limit_bytes=PROJECT_ARCHIVE_LIMIT_BYTES)).content

    async def server_config_document(self, name: str) -> dict[str, Any] | None:
        """The Server Config resource document, or ``None`` when it is absent."""

        document = await self.get_json(SERVER_CONFIG_FIND_PATH.format(name=quote(name, safe="")), allow_404=True)
        return document if isinstance(document, dict) else None

    async def singleton_document(self, resource_type: str) -> dict[str, Any] | None:
        """A resource *type's* single resource document, or ``None`` when it serves none."""

        path = SINGLETON_PATH.format(resource_type=quote(resource_type, safe="/"))
        document = await self.get_json(path, allow_404=True)
        return document if isinstance(document, dict) else None

    async def resource_document(self, resource_type: str, name: str) -> dict[str, Any] | None:
        """One config resource document of any type, or ``None`` when it is absent."""

        # The type id is a documented ``<module>/<typeId>`` and both halves are path
        # segments: escaping the separator makes the Gateway answer 404 for a resource
        # that exists (found live on the ticket #21 row), so only the name is escaped.
        path = RESOURCE_FIND_PATH.format(
            resource_type=quote(resource_type, safe="/"), name=quote(name, safe=""),
        )
        document = await self.get_json(path, allow_404=True)
        return document if isinstance(document, dict) else None

    async def export_tags(self, provider: str) -> dict[str, Any]:
        """One Tag provider's JSON export, bounded; a provider that is not there is an error."""

        document = await self.get_json(TAGS_EXPORT_PATH, params={"provider": provider, "type": "json"})
        if not isinstance(document, dict):
            raise GatewayProbeError(f"the {provider} Tag export did not return a JSON object")
        return document

    async def get_json(
        self, path: str, *, params: dict[str, str] | None = None, allow_404: bool = False
    ) -> Any:
        """Parsed JSON body, or ``None`` for a 404 when ``allow_404`` is set."""

        probe = await self._request("GET", path, params=params, limit_bytes=JSON_LIMIT_BYTES, allow_404=allow_404)
        if probe.status_code == 404:
            return None
        return _decode_json(probe.content, path)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        limit_bytes: int = JSON_LIMIT_BYTES,
        allow_404: bool = False,
    ) -> Probe:
        """One response, read through a bound whatever its status turns out to be.

        A success is buffered up to the caller's bound and refused when it passes it. A
        refusal or an allowed 404 is only sampled: its status and a snippet are the whole
        report, so an oversized body is drained and discarded rather than buffered (D10).
        """

        try:
            async with self._client.stream(method, path, params=params) as response:
                status = response.status_code
                succeeded = 200 <= status < 300
                bound = limit_bytes if succeeded else min(limit_bytes, ERROR_BODY_LIMIT_BYTES)
                declared = response.headers.get("content-length")
                if succeeded and declared is not None and declared.isdigit() and int(declared) > bound:
                    raise GatewayProbeError(
                        f"{method} {path} announces {declared} bytes; the bound is {bound} bytes"
                    )
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(body) + len(chunk) > bound:
                        if succeeded:
                            raise GatewayProbeError(
                                f"{method} {path} exceeded {bound} bytes; refusing to buffer more"
                            )
                        body.extend(chunk[: max(0, bound - len(body))])
                        break
                    body.extend(chunk)
                if status == 404 and allow_404:
                    return Probe(status, b"")
                if not succeeded:
                    snippet = _redact(_snippet(bytes(body)), self._token)
                    raise GatewayProbeError(
                        f"{method} {path} returned HTTP {status}" + (f": {snippet}" if snippet else "")
                    )
                return Probe(status, bytes(body))
        except httpx.HTTPError as error:
            raise GatewayProbeError(f"{method} {path} failed: {_redact(_reason(error), self._token)}") from error


def _decode_json(content: bytes, path: str) -> Any:
    try:
        return json.loads(content)
    except ValueError as error:
        raise GatewayProbeError(f"{path} did not return valid JSON: {_reason(error)}") from error


def _metadata_total(document: Any) -> int | None:
    """``metadata.total`` when the Gateway reports it: the size of the whole collection."""

    metadata = document.get("metadata") if isinstance(document, dict) else None
    if not isinstance(metadata, dict):
        return None
    total = metadata.get("total")
    if isinstance(total, bool) or not isinstance(total, (int, float)):
        return None
    return max(int(total), 0)


def _snippet(content: bytes) -> str:
    """One report-safe line: decoded with replacements, whitespace collapsed, truncated."""

    text = content[:ERROR_BODY_SNIPPET].decode("utf-8", errors="replace")
    return " ".join(text.split())


def _reason(error: BaseException) -> str:
    joined = " ".join(str(arg) for arg in error.args if arg)
    return (joined or type(error).__name__)[:ERROR_BODY_SNIPPET]


def _redact(text: str, *secrets: str) -> str:
    safe = text
    for secret in secrets:
        if secret:
            safe = safe.replace(secret, "[redacted]")
    return safe
