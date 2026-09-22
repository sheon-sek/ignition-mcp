"""Bounded, read-only Gateway REST probes for ``setup-native`` (D04, D20).

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

from ignition_rest_mcp.cli.setup_native.inputs import Endpoint

JSON_LIMIT_BYTES = 1_048_576
OPENAPI_LIMIT_BYTES = 16 * 1024 * 1024
ERROR_BODY_SNIPPET = 160

MCP_MODULE_ID = "com.inductiveautomation.mcp"
GATEWAY_INFO_PATH = "/data/api/v1/gateway-info"
MODULES_PATH = "/data/api/v1/modules/healthy"
PROJECT_FIND_PATH = "/data/api/v1/projects/find/{name}"
SERVER_CONFIG_FIND_PATH = "/data/api/v1/resources/find/com.inductiveautomation.mcp/server-config/{name}"
#: The find route of any config resource type, for the reads ``apply`` reasons over
#: (the reserved policy Tag provider's resource).
RESOURCE_FIND_PATH = "/data/api/v1/resources/find/{resource_type}/{name}"
#: The Tag export route: the documented read-back of a Tag provider's content.
TAGS_EXPORT_PATH = "/data/api/v1/tags/export"
SECURITY_LEVELS_PATH = "/data/api/v1/resources/singleton/ignition/security-levels"
API_TOKEN_PATH = "/data/api/v1/resources/ignition/api-token"
DESIGNERS_PATH = "/data/api/v1/designers"
PROJECT_IMPORT_PATH = "/data/api/v1/projects/import/{name}"

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
                "User-Agent": "ignition-mcp-setup-native",
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

    async def healthy_modules(self) -> list[dict[str, Any]]:
        document = await self.get_json(MODULES_PATH, params={"limit": "500", "offset": "0"})
        items = document.get("items") if isinstance(document, dict) else None
        if not isinstance(items, list):
            raise GatewayProbeError("modules/healthy returned no items list")
        return [item for item in items if isinstance(item, dict)]

    async def mcp_module(self) -> ModuleIdentity | None:
        """Identity of the installed MCP Module, or ``None`` when it is absent."""

        for item in await self.healthy_modules():
            if item.get("id") == MCP_MODULE_ID:
                identity = parse_module_identity(item.get("version"))
                if identity is None:
                    raise GatewayProbeError("the MCP Module entry carries no usable version")
                return identity
        return None

    async def find_project(self, name: str) -> ProjectState:
        document = await self.get_json(PROJECT_FIND_PATH.format(name=quote(name, safe="")), allow_404=True)
        return classify_project(name, document if isinstance(document, dict) else None)

    async def server_config_document(self, name: str) -> dict[str, Any] | None:
        """The Server Config resource document, or ``None`` when it is absent."""

        document = await self.get_json(SERVER_CONFIG_FIND_PATH.format(name=quote(name, safe="")), allow_404=True)
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
        try:
            async with self._client.stream(method, path, params=params) as response:
                declared = response.headers.get("content-length")
                if declared is not None and declared.isdigit() and int(declared) > limit_bytes:
                    raise GatewayProbeError(
                        f"{method} {path} announces {declared} bytes; the bound is {limit_bytes} bytes"
                    )
                if response.status_code == 404 and allow_404:
                    await response.aread()
                    return Probe(response.status_code, b"")
                if not response.is_success:
                    await response.aread()
                    snippet = _redact(_snippet(response.content), self._token)
                    raise GatewayProbeError(
                        f"{method} {path} returned HTTP {response.status_code}"
                        + (f": {snippet}" if snippet else "")
                    )
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(body) + len(chunk) > limit_bytes:
                        raise GatewayProbeError(
                            f"{method} {path} exceeded {limit_bytes} bytes; refusing to buffer more"
                        )
                    body.extend(chunk)
                return Probe(response.status_code, bytes(body))
        except httpx.HTTPError as error:
            raise GatewayProbeError(f"{method} {path} failed: {_redact(_reason(error), self._token)}") from error


def _decode_json(content: bytes, path: str) -> Any:
    try:
        return json.loads(content)
    except ValueError as error:
        raise GatewayProbeError(f"{path} did not return valid JSON: {_reason(error)}") from error


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
