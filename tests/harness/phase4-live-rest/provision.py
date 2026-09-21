#!/usr/bin/env python3
"""Test-only provisioning for the Phase 4 REST mutation harness.

This is a harness, not a second installer (the same rule the Phase 0-2
provisioning harness follows): it prepares the disposable config resources the
live cases need on a CI-owned Gateway, through the Gateway's own Native REST API.

It provisions two ``ignition/audit-profile`` resources:

- ``MCP_CI_AUDIT`` — the Target the harness's deployment allowlist names;
- ``MCP_CI_AUDIT_OTHER`` — a second resource of the same type that the allowlist
  does *not* name, so the Target-allowlist refusal is exercised live.

Both are created without a signature (creation takes no Precondition token) and are
read back with a signature, which is the proof that the live cases have a real
Precondition token to work with. Re-running is safe: an existing resource is left
alone.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

RESOURCE_TYPE = "ignition/audit-profile"
#: An allowed singleton target (the documented change item carries no name).
SINGLETON_TYPE = "ignition/cobranding"
COLLECTION_PATH = f"/data/api/v1/resources/{RESOURCE_TYPE}"
FIND_PATH = f"/data/api/v1/resources/find/{RESOURCE_TYPE}/"
ALLOWLISTED = "MCP_CI_AUDIT"
UNALLOWLISTED = "MCP_CI_AUDIT_OTHER"
REQUIRED_ENDPOINTS = frozenset({
    ("GET", f"/data/api/v1/resources/type/{RESOURCE_TYPE}"),
    ("GET", f"/data/api/v1/resources/find/{RESOURCE_TYPE}/{{name}}"),
    ("POST", COLLECTION_PATH),
    ("PUT", COLLECTION_PATH),
    ("GET", f"/data/api/v1/resources/singleton/{SINGLETON_TYPE}"),
    ("PUT", f"/data/api/v1/resources/{SINGLETON_TYPE}"),
})
MAX_RESPONSE_BYTES = 1_048_576
MAX_OPENAPI_BYTES = 16 * 1_048_576


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
) -> tuple[int, Any]:
    url = base_url.rstrip("/") + path
    headers = {"X-Ignition-API-Token": token, "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
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
        base_url, token, "GET", FIND_PATH + name, allowed_error_statuses=frozenset({404}),
    )
    if status != 200 or not isinstance(payload, dict):
        raise ProvisionError(f"reading {RESOURCE_TYPE}/{name} returned HTTP {status}")
    signature = payload.get("signature")
    if not isinstance(signature, str) or not signature:
        raise ProvisionError(f"{RESOURCE_TYPE}/{name} reports no Resource signature")
    return payload


def provision(base_url: str, token: str) -> dict[str, Any]:
    readiness = await_required_endpoints(base_url, token)
    resources = {
        name: {
            "createStatus": _create_profile(base_url, token, name, description),
            "signature": _read_profile(base_url, token, name)["signature"],
            "allowlisted": name == ALLOWLISTED,
        }
        for name, description in (
            (ALLOWLISTED, "Disposable Phase 4 CI audit profile (allowlisted Target)"),
            (UNALLOWLISTED, "Disposable Phase 4 CI audit profile (allowlist control)"),
        )
    }
    token_status, token_payload = _request(
        base_url, token, "GET", "/data/api/v1/resources/find/ignition/api-token/ignition-mcp-ci",
        allowed_error_statuses=frozenset({404}),
    )
    singleton_status, singleton_payload = _request(
        base_url, token, "GET", f"/data/api/v1/resources/singleton/{SINGLETON_TYPE}",
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
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway-url", default="http://127.0.0.1:8088")
    parser.add_argument("--api-token", required=True)
    parser.add_argument("--evidence", required=True, type=Path)
    args = parser.parse_args()
    report = provision(args.gateway_url, args.api_token)
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "resources": {name: item["signature"] for name, item in report["resources"].items()},
        "refusedResourcePresent": report["refusedResourceType"]["present"],
        "singletonTargetPresent": report["singletonTarget"]["present"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
