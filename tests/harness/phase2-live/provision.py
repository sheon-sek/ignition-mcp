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
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ProvisionError(f"{method} {path} response exceeded {MAX_RESPONSE_BYTES} bytes")
            return response.status, json.loads(raw) if raw.strip() else None
    except HTTPError as error:
        raw = error.read(MAX_RESPONSE_BYTES + 1)
        detail = raw[:2000].decode("utf-8", errors="replace")
        raise ProvisionError(f"{method} {path} returned HTTP {error.code}: {detail}") from error
    except (URLError, TimeoutError, OSError) as error:
        raise ProvisionError(f"{method} {path} failed: {type(error).__name__}: {error}") from error


def _create_resource(base_url: str, token: str, resource_type: str, resource: dict[str, Any]) -> int:
    body = json.dumps([resource], separators=(",", ":")).encode("utf-8")
    status, _ = _request(
        base_url,
        token,
        "POST",
        "/data/api/v1/resources/" + resource_type,
        body=body,
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


def provision(base_url: str, token: str) -> dict[str, Any]:
    resources = [
        (
            "ignition/database-connection",
            {
                "name": "MCP_CI_POSTGRES",
                "enabled": True,
                "description": "Disposable Phase 2 CI PostgreSQL",
                "config": {
                    "driver": "PostgreSQL",
                    "translator": "POSTGRESQL",
                    "connectURL": "jdbc:postgresql://postgres:5432/ignition_mcp_ci",
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
                "config": {
                    "profile": {"type": "InternalHistorian"},
                    "settings": {
                        "timeLimit": {"enabled": True, "size": 1, "sizeUnits": "DAY"},
                        "pointLimit": {"enabled": True, "size": 10000},
                        "remoteSync": {"enabled": False},
                    },
                },
            },
        ),
    ]
    # Alarm Journal provisioning and alarm_status/alarm_journal smoke are omitted:
    # both Tools are deferred per the D12 Phase 2 bounded-execution amendment.
    results: dict[str, Any] = {"resources": {}}
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
        result = {"status": "PASS", **provision(args.gateway_url, args.api_token)}
    except Exception as error:
        result = {"status": "FAILED", "fatalError": f"{type(error).__name__}: {error}"}
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
