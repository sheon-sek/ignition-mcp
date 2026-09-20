#!/usr/bin/env python3
"""Provision deterministic Phase 2 fixtures through Native REST.

The target must be the disposable CI Gateway. This script is intentionally narrow:
it creates only resources and Tags needed by G2 readonly smoke tests.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

MAX_RESPONSE_BYTES = 1_048_576
MAX_OPENAPI_BYTES = 16 * 1_048_576

# GATEWAY_MODULES_ENABLED is a whitelist. Phase 2 explicitly enables only the
# modules required by this disposable certification environment. Provisioning is
# gated by the actual OpenAPI routes those modules contribute, not by module-health
# labels or certificate side effects.
_REQUIRED_OPENAPI_ENDPOINTS = frozenset({
    ("GET", "/data/alarm-notification/api/v1/pipeline"),
    ("GET", "/data/alarm-notification/api/v1/pipelines"),
    ("POST", "/data/api/v1/resources/com.inductiveautomation.historian/historian-provider"),
    ("POST", "/data/api/v1/resources/ignition/audit-profile"),
    ("POST", "/data/api/v1/resources/ignition/database-connection"),
    ("POST", "/data/api/v1/tags/import"),
})


class ProvisionError(RuntimeError):
    pass


def _request(
    base_url: str,
    token: str,
    method: str,
    path: str,
    *,
    body: bytes | None = None,
    content_type: str = "application/json",
    query: dict[str, str] | None = None,
    timeout: float = 20.0,
    allowed_error_statuses: frozenset[int] = frozenset(),
    max_response_bytes: int = MAX_RESPONSE_BYTES,
) -> tuple[int, Any]:
    url = base_url.rstrip("/") + path
    if query:
        url += "?" + urlencode(query)
    request = Request(
        url,
        data=body,
        method=method,
        headers={
            "Accept": "application/json",
            "Content-Type": content_type,
            "X-Ignition-API-Token": token,
            "User-Agent": "ignition-mcp-g2-provision/1",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read(max_response_bytes + 1)
            if len(raw) > max_response_bytes:
                raise ProvisionError(f"{method} {path} response exceeded {max_response_bytes} bytes")
            return response.status, json.loads(raw) if raw.strip() else None
    except HTTPError as error:
        raw = error.read(max_response_bytes + 1)
        if len(raw) > max_response_bytes:
            detail = f"<response exceeded {max_response_bytes} bytes>"
        else:
            detail = raw[:2000].decode("utf-8", errors="replace")
        if error.code in allowed_error_statuses:
            try:
                parsed = json.loads(raw) if raw.strip() else None
            except json.JSONDecodeError:
                parsed = detail
            return error.code, parsed
        raise ProvisionError(f"{method} {path} returned HTTP {error.code}: {detail}") from error
    except (URLError, TimeoutError, OSError) as error:
        raise ProvisionError(f"{method} {path} failed: {type(error).__name__}: {error}") from error


def _create_resource(base_url: str, token: str, resource_type: str, resource: dict[str, Any]) -> int:
    body = json.dumps([resource], separators=(",", ":")).encode("utf-8")
    status, payload = _request(
        base_url,
        token,
        "POST",
        "/data/api/v1/resources/" + resource_type,
        body=body,
        allowed_error_statuses=frozenset({422}),
    )
    if status == 422:
        # The committed OpenAPI body schemas are known to omit wire-required
        # discriminator details (e.g. 8.3 config password is a typed {type,data}
        # credential object, not a string). Pull the live type description into
        # the diagnostics so the next triage does not need another full run.
        try:
            _, describe = _request(
                base_url, token, "GET", "/data/api/v1/resources/type/" + resource_type,
            )
            describe_text = json.dumps(describe, separators=(",", ":"))[:6000]
        except ProvisionError as error:
            describe_text = f"<describe failed: {error}>"
        raise ProvisionError(
            f"creating {resource_type} rejected: {json.dumps(payload, separators=(',', ':'))[:2000]} "
            f"| live type description: {describe_text}"
        )
    if status not in {200, 201}:
        raise ProvisionError(f"creating {resource_type} returned unexpected HTTP {status}")
    return status


def _tag_document(value: int) -> bytes:
    document = [
        {
            "name": "McpCiType",
            "tagType": "UdtType",
            "tags": [
                {"name": "Member", "tagType": "AtomicTag", "valueSource": "memory", "dataType": "Int4", "value": 7}
            ],
        },
        {
            "name": "_mcp_ci",
            "tagType": "Folder",
            "tags": [
                {
                    "name": "Value",
                    "tagType": "AtomicTag",
                    "valueSource": "memory",
                    "dataType": "Int4",
                    "value": value,
                    "historyEnabled": True,
                    "historyProvider": "MCP_CI_HISTORY",
                    "historySampleRate": 1000,
                    "historySampleRateUnits": "MS",
                    "historyMode": "OnChange",
                },
                {"name": "Instance", "tagType": "UdtInstance", "typeId": "McpCiType"},
            ],
        },
    ]
    return json.dumps(document, separators=(",", ":")).encode("utf-8")


def _import_tags(base_url: str, token: str, value: int) -> int:
    status, payload = _request(
        base_url,
        token,
        "POST",
        "/data/api/v1/tags/import",
        body=_tag_document(value),
        content_type="application/octet-stream",
        query={
            "provider": "default",
            "path": "",
            "type": "json",
            "collisionPolicy": "MergeOverwrite",
        },
    )
    if status != 200:
        raise ProvisionError(f"Tag import returned unexpected HTTP {status}")
    if payload not in (None, []):
        raise ProvisionError(f"Tag import returned non-Good QualityCodes: {payload}")
    return status


def _resource_names(base_url: str, token: str, resource_type: str) -> list[str]:
    status, payload = _request(
        base_url, token, "GET", "/data/api/v1/resources/names/" + resource_type,
    )
    if status != 200 or not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ProvisionError(f"discovering {resource_type} names returned unexpected HTTP {status}")
    names: list[str] = []
    for item in payload["items"]:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict) and isinstance(item.get("name"), str):
            names.append(item["name"])
    return names


# D04 principle applied to fixtures: discover what the target Gateway actually bundles
# instead of hard-coding driver assumptions. A fresh 8.3.x standard Gateway does not
# include a PostgreSQL JDBC driver; the first live G2 attempt proved this with
# "422 Invalid reference: 'PostgreSQL'".
_DB_KEYWORDS = ("mariadb", "mysql")
_DB_URL_BY_KEYWORD = {
    "mariadb": "jdbc:mariadb://db:3306/ignition_mcp_ci",
    "mysql": "jdbc:mysql://db:3306/ignition_mcp_ci",
}


def _select_database_identity(base_url: str, token: str) -> tuple[str, str, str]:
    drivers = _resource_names(base_url, token, "ignition/database-driver")
    translators = _resource_names(base_url, token, "ignition/database-translator")
    driver = next((name for name in drivers if any(k in name.lower() for k in _DB_KEYWORDS)), None)
    translator = next((name for name in translators if any(k in name.lower() for k in _DB_KEYWORDS)), None)
    if driver is None or translator is None:
        raise ProvisionError(
            "No CI-compatible database driver/translator is installed: "
            f"drivers={sorted(drivers)} translators={sorted(translators)}"
        )
    connect_url = next((url for key, url in _DB_URL_BY_KEYWORD.items() if key in driver.lower()), None)
    if connect_url is None:
        raise ProvisionError(f"selected driver {driver!r} has no known CI JDBC URL")
    return driver, translator, connect_url


def _select_database_identity_when_ready(
    base_url: str, token: str, timeout: float = 180.0,
) -> tuple[str, str, str]:
    # Driver resources can register asynchronously after the Gateway becomes
    # authenticated. Discovery is retried under a bounded deadline instead of
    # assuming a fixed startup delay.
    deadline = time.monotonic() + timeout
    while True:
        try:
            return _select_database_identity(base_url, token)
        except ProvisionError as error:
            if time.monotonic() >= deadline:
                raise
            print(f"database identity not ready yet: {error}", flush=True)
            time.sleep(5.0)


def _openapi_endpoint_inventory(payload: Any) -> set[tuple[str, str]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("paths"), dict):
        raise ProvisionError("Gateway OpenAPI document is missing paths")
    endpoints: set[tuple[str, str]] = set()
    for path, operations in payload["paths"].items():
        if not isinstance(path, str) or not isinstance(operations, dict):
            continue
        for method in operations:
            method_upper = str(method).upper()
            if method_upper in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                endpoints.add((method_upper, path))
    return endpoints


def _await_required_openapi_endpoints(
    base_url: str, token: str, timeout: float = 180.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_detail = "OpenAPI not requested"
    while time.monotonic() < deadline:
        try:
            status, payload = _request(
                base_url,
                token,
                "GET",
                "/openapi.json",
                timeout=20.0,
                max_response_bytes=MAX_OPENAPI_BYTES,
            )
            if status != 200:
                raise ProvisionError(f"Gateway OpenAPI returned unexpected HTTP {status}")
            endpoints = _openapi_endpoint_inventory(payload)
            missing = sorted(_REQUIRED_OPENAPI_ENDPOINTS - endpoints)
            if not missing:
                return {
                    "endpointCount": len(endpoints),
                    "requiredEndpoints": [
                        f"{method} {path}" for method, path in sorted(_REQUIRED_OPENAPI_ENDPOINTS)
                    ],
                }
            last_detail = "missing=" + ", ".join(f"{method} {path}" for method, path in missing)
        except ProvisionError as error:
            last_detail = str(error)
        time.sleep(3.0)
    raise ProvisionError(f"required OpenAPI endpoints never became available: {last_detail}")


def provision(base_url: str, token: str) -> dict[str, Any]:
    # Step 0: prove that the exact Gateway/module composition exposes every Native
    # REST route required by this fixture before issuing any mutation. This prevents
    # module-health labels from being mistaken for capability readiness.
    openapi_readiness = _await_required_openapi_endpoints(base_url, token)
    driver, translator, connect_url = _select_database_identity_when_ready(base_url, token)
    resources = [
        (
            "ignition/database-connection",
            {
                "name": "MCP_CI_DB",
                "enabled": True,
                "description": "Disposable Phase 2 CI fixture database",
                # 8.3 wire truth: config `password` is a typed {type,data} credential
                # object (Embedded requires pre-encrypted material), never a plain
                # string. The disposable CI database user is created without a
                # password, so the property is omitted entirely.
                "config": {
                    "driver": driver,
                    "translator": translator,
                    "connectURL": connect_url,
                    "username": "ignition_mcp_ci",
                    "poolMaxActive": 4,
                    "poolMaxIdle": 2,
                    "poolMaxWait": 5000,
                    "validationQuery": "SELECT 1",
                    "testOnBorrow": True,
                },
            },
        ),
        (
            "ignition/audit-profile",
            {
                "name": "MCP_CI_AUDIT",
                "enabled": True,
                "description": "Disposable Phase 2 CI local audit profile",
                "config": {"profile": {"type": "local"}, "settings": {}},
            },
        ),
        (
            "com.inductiveautomation.historian/historian-provider",
            {
                "name": "MCP_CI_HISTORY",
                "enabled": True,
                "description": "Bounded disposable Phase 2 CI historian",
                # 8.3's Core Historian supersedes the legacy Internal Historian
                # profile; empty settings accept the Gateway-documented defaults.
                "config": {"profile": {"type": "CoreHistorian"}, "settings": {}},
            },
        ),
    ]
    # Alarm Journal provisioning and alarm_status/alarm_journal smoke are omitted:
    # both Tools are deferred per the D12 Phase 2 bounded-execution amendment.
    results: dict[str, Any] = {
        "resources": {},
        "openapiReadiness": openapi_readiness,
        "databaseDriver": driver,
        "databaseTranslator": translator,
        "databaseConnectUrl": connect_url,
    }
    for resource_type, resource in resources:
        results["resources"][resource_type] = _create_resource(base_url, token, resource_type, resource)

    # Resource changes are hot-loaded asynchronously. Retrying is bounded and does not
    # repeat an ambiguous mutation: each failed attempt here is a read-independent Tag
    # import whose HTTP response is known, and the disposable namespace is idempotent.
    deadline = time.monotonic() + 120.0
    last_error = ""
    while time.monotonic() < deadline:
        try:
            results["initialTagImport"] = _import_tags(base_url, token, 10)
            break
        except ProvisionError as error:
            last_error = str(error)
            time.sleep(2.0)
    else:
        raise ProvisionError(f"Tag fixture import did not become ready: {last_error}")

    time.sleep(3.0)
    results["activeTagImport"] = _import_tags(base_url, token, 42)
    time.sleep(3.0)
    return results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gateway-url", default="http://127.0.0.1:8088")
    parser.add_argument("--api-token", required=True)
    parser.add_argument("--evidence", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = {"status": "PASS", "stage": "provision", **provision(args.gateway_url, args.api_token)}
    except Exception as error:
        result = {"status": "FAILED", "stage": "provision", "fatalError": f"{type(error).__name__}: {error}"}
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(result["fatalError"])
        return 2
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
