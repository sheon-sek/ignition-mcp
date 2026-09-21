"""Bounded Native REST client for the Phase 4 live characterization harness.

Stdlib only: the Phase 4 probe harness is a test consumer of Gateway behavior,
not a product code path, and it must not import the shipped package.
"""

from __future__ import annotations

import hashlib
import http.client
import json
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_OPENAPI_BYTES = 16 * 1024 * 1024


class RestError(RuntimeError):
    """A Gateway REST call failed or returned an unusable payload."""


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """A redirect would move the request to an origin the guard never approved."""

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        raise RestError(f"redirect refused: HTTP {code} to {newurl}")


_OPENER = urllib.request.build_opener(_NoRedirects)


def request(
    base_url: str,
    token: str,
    method: str,
    path: str,
    *,
    body: bytes | None = None,
    content_type: str | None = None,
    query: dict[str, str] | None = None,
    timeout: float = 60.0,
    max_bytes: int = MAX_RESPONSE_BYTES,
) -> tuple[int, bytes]:
    url = base_url.rstrip("/") + path
    if query is not None:
        url += "?" + urllib.parse.urlencode(query, quote_via=urllib.parse.quote)
    request_object = urllib.request.Request(url, data=body, method=method)
    request_object.add_header("Accept", "application/json")
    request_object.add_header("X-Ignition-API-Token", token)
    request_object.add_header("User-Agent", "ignition-mcp-p4-driver/1")
    if content_type is not None:
        request_object.add_header("Content-Type", content_type)
    try:
        with _OPENER.open(request_object, timeout=timeout) as response:
            payload = response.read(max_bytes + 1)
            status = int(response.status)
    except urllib.error.HTTPError as error:
        payload = error.read(max_bytes + 1)
        status = int(error.code)
    except (urllib.error.URLError, OSError, http.client.HTTPException) as error:
        # A Gateway that is still starting resets or drops connections; that is a
        # retryable transport failure, not a crash in the caller.
        raise RestError(f"{method} {path} failed: {type(error).__name__}: {error}") from error
    if len(payload) > max_bytes:
        raise RestError(f"{method} {path} response exceeded the bounded size")
    return status, payload


def decode(payload: bytes) -> Any:
    if not payload.strip():
        return None
    try:
        return json.loads(payload.decode("utf-8"))
    except ValueError as error:
        raise RestError("Gateway response was not JSON") from error


def gateway_info(base_url: str, token: str) -> dict[str, Any]:
    status, payload = request(base_url, token, "GET", "/data/api/v1/gateway-info")
    if status != 200:
        raise RestError(f"gateway-info returned HTTP {status}")
    document = decode(payload)
    if not isinstance(document, dict):
        raise RestError("gateway-info was not an object")
    return document


def openapi_endpoints(base_url: str, token: str) -> set[tuple[str, str]]:
    status, payload = request(
        base_url, token, "GET", "/openapi.json", timeout=30.0, max_bytes=MAX_OPENAPI_BYTES,
    )
    if status != 200:
        raise RestError(f"openapi.json returned HTTP {status}")
    document = decode(payload)
    if not isinstance(document, dict) or not isinstance(document.get("paths"), dict):
        raise RestError("openapi.json carried no paths")
    endpoints: set[tuple[str, str]] = set()
    for route, operations in document["paths"].items():
        if not isinstance(route, str) or not isinstance(operations, dict):
            continue
        for method in operations:
            upper = str(method).upper()
            if upper in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                endpoints.add((upper, route))
    return endpoints


def create_tag_provider(base_url: str, token: str, resource: dict[str, Any]) -> tuple[int, Any]:
    body = json.dumps([resource], separators=(",", ":")).encode("utf-8")
    status, payload = request(
        base_url, token, "POST", "/data/api/v1/resources/ignition/tag-provider",
        body=body, content_type="application/json",
    )
    return status, decode(payload)


def find_resource(base_url: str, token: str, resource_type: str, name: str) -> tuple[int, Any]:
    status, payload = request(
        base_url, token, "GET", f"/data/api/v1/resources/find/{resource_type}/{name}",
    )
    return status, decode(payload)


def import_tags(
    base_url: str,
    token: str,
    provider: str,
    document: bytes,
    *,
    path: str = "",
    collision_policy: str = "Abort",
    timeout: float = 60.0,
) -> tuple[int, Any]:
    status, payload = request(
        base_url, token, "POST", "/data/api/v1/tags/import",
        body=document, content_type="application/octet-stream",
        query={"provider": provider, "path": path, "type": "json", "collisionPolicy": collision_policy},
        timeout=timeout,
    )
    return status, decode(payload)


def export_tags(
    base_url: str, token: str, provider: str, *, path: str = "", timeout: float = 60.0,
) -> tuple[int, bytes]:
    return request(
        base_url, token, "GET", "/data/api/v1/tags/export",
        query={"provider": provider, "path": path, "type": "json"},
        timeout=timeout,
    )


def import_failures(payload: Any) -> list[Any] | None:
    """Normalize the two observed Tag import wire shapes; None means no failure.

    The committed 8.3.8 OpenAPI describes a list of non-Good QualityCodes while
    live 8.3.8/8.3.9 answer a summary object; anything unrecognized counts as a
    failure so the harness fails closed.
    """
    if payload is None or payload == []:
        return None
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        failures = payload.get("failures")
        if payload.get("failureCount") == 0 and (failures is None or failures == []):
            return None
        return failures if isinstance(failures, list) and failures else [payload]
    return [payload]


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
