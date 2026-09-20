#!/usr/bin/env python3
"""G1 dual-plane real-Gateway probe."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time
from typing import Any
import urllib.error
import urllib.request

PROTOCOL_VERSION = "2025-06-18"
ACCEPT = "application/json, text/event-stream"


class ProbeError(RuntimeError):
    pass


class McpClient:
    def __init__(self, url: str, *, timeout: float = 10.0, api_token: str | None = None) -> None:
        self.url = url
        self.timeout = timeout
        self.api_token = api_token
        self.session_id: str | None = None
        self.request_id = 0

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": ACCEPT,
            "Content-Type": "application/json",
            "User-Agent": "ignition-mcp-g1-probe/1",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        if self.api_token:
            headers["X-Ignition-API-Token"] = self.api_token
        return headers

    def _post(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any] | None]:
        request = urllib.request.Request(
            self.url,
            data=json.dumps(payload, separators=(",", ":")).encode(),
            headers=self._headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                status = response.status
                body = response.read()
                content_type = response.headers.get("Content-Type", "")
                session = response.headers.get("Mcp-Session-Id")
                if session:
                    self.session_id = session
        except urllib.error.HTTPError as error:
            body = error.read()
            raise ProbeError(
                f"HTTP {error.code} from {self.url}: {body[:1000].decode('utf-8', errors='replace')}"
            ) from error
        except (OSError, TimeoutError) as error:
            raise ProbeError(f"MCP transport unavailable: {type(error).__name__}: {error}") from error

        if not body.strip():
            return status, None
        text = body.decode("utf-8")
        if "text/event-stream" in content_type:
            events: list[dict[str, Any]] = []
            for line in text.splitlines():
                if line.startswith("data:"):
                    decoded = json.loads(line[5:].strip())
                    if isinstance(decoded, dict):
                        events.append(decoded)
            if not events:
                raise ProbeError("SSE response had no JSON data event")
            return status, events[-1]
        decoded = json.loads(text)
        if not isinstance(decoded, dict):
            raise ProbeError("JSON-RPC response must be an object")
        return status, decoded

    def initialize(self, *, startup_timeout: float = 180.0) -> dict[str, Any]:
        deadline = time.monotonic() + startup_timeout
        last = ""
        while time.monotonic() < deadline:
            self.session_id = None
            try:
                response = self.call(
                    "initialize",
                    {
                        "protocolVersion": PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "ignition-mcp-g1-probe", "version": "1.0.0"},
                    },
                )
                self.notify_initialized()
                return response
            except ProbeError as error:
                last = str(error)
                time.sleep(2)
        raise ProbeError(f"initialize did not become ready: {last}")

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.request_id += 1
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": self.request_id, "method": method}
        if params is not None:
            payload["params"] = params
        _, response = self._post(payload)
        if response is None:
            raise ProbeError(f"{method} returned no response")
        if "error" in response:
            raise ProbeError(f"{method} JSON-RPC error: {response['error']}")
        result = response.get("result")
        if not isinstance(result, dict):
            raise ProbeError(f"{method} result must be an object")
        return response

    def notify_initialized(self) -> None:
        try:
            status, response = self._post(
                {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
            )
        except ProbeError:
            # FastMCP 4 may negotiate the new stateless protocol where this notification
            # is not necessary; normal tool/list calls below are the authoritative proof.
            return
        if status not in {200, 202}:
            raise ProbeError(f"initialized notification HTTP {status}")
        if response is not None and "error" in response:
            raise ProbeError("initialized notification returned error")


def _list(response: dict[str, Any], key: str) -> list[dict[str, Any]]:
    result = response.get("result")
    value = result.get(key) if isinstance(result, dict) else None
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ProbeError(f"missing result.{key}")
    return value


def _tools_by_name(tools: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for tool in tools:
        name = tool.get("name")
        if not isinstance(name, str) or not name:
            raise ProbeError("Tool discovery returned an invalid name")
        if name in result:
            raise ProbeError(f"duplicate Tool name in discovery: {name}")
        result[name] = tool
    return result


def _assert_input_schema(
    tool: dict[str, Any],
    *,
    properties: dict[str, str],
    required: set[str],
) -> None:
    name = str(tool.get("name"))
    schema = tool.get("inputSchema")
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise ProbeError(f"{name} inputSchema must be an object schema")
    actual_properties = schema.get("properties")
    if not isinstance(actual_properties, dict):
        raise ProbeError(f"{name} inputSchema.properties must be an object")
    if set(actual_properties) != set(properties):
        raise ProbeError(
            f"{name} parameter inventory mismatch: {set(actual_properties)} != {set(properties)}"
        )
    for parameter, expected_type in properties.items():
        descriptor = actual_properties.get(parameter)
        if not isinstance(descriptor, dict) or descriptor.get("type") != expected_type:
            raise ProbeError(
                f"{name}.{parameter} type mismatch: "
                f"{descriptor.get('type') if isinstance(descriptor, dict) else None} != {expected_type}"
            )
    raw_required = schema.get("required", [])
    if not isinstance(raw_required, list) or not all(isinstance(item, str) for item in raw_required):
        raise ProbeError(f"{name} inputSchema.required must be a string array")
    if set(raw_required) != required:
        raise ProbeError(f"{name} required parameter mismatch: {set(raw_required)} != {required}")


def _structured(response: dict[str, Any]) -> dict[str, Any]:
    result = response.get("result")
    value = result.get("structuredContent") if isinstance(result, dict) else None
    if not isinstance(value, dict):
        raise ProbeError("tool call did not return structuredContent")
    return value


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _http_json(url: str, *, token: str | None = None) -> tuple[int, Any]:
    headers = {"Accept": "application/json"}
    if token:
        headers["X-Ignition-API-Token"] = token
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as error:
        raw = error.read()
        value: Any = raw.decode("utf-8", errors="replace")
        return error.code, value


def probe_external(base_url: str, raw_dir: Path) -> dict[str, Any]:
    client = McpClient(base_url.rstrip("/") + "/mcp")
    init = client.initialize()
    _write(raw_dir / "rest-initialize.json", init)

    tools_response = client.call("tools/list", {})
    _write(raw_dir / "rest-tools-list.json", tools_response)
    tools = _list(tools_response, "tools")
    tools_by_name = _tools_by_name(tools)
    names = set(tools_by_name)
    expected = {"gateway_info", "gateway_diagnose"}
    if names != expected:
        raise ProbeError(f"ignition-rest inventory mismatch: {names}")
    for name in sorted(expected):
        tool = tools_by_name[name]
        _assert_input_schema(tool, properties={}, required=set())
        if not isinstance(tool.get("description"), str) or not tool["description"].strip():
            raise ProbeError(f"ignition-rest {name} missing description")
        if not isinstance(tool.get("outputSchema"), dict):
            raise ProbeError(f"ignition-rest {name} missing native outputSchema")

    info_response = client.call("tools/call", {"name": "gateway_info", "arguments": {}})
    _write(raw_dir / "rest-gateway-info.json", info_response)
    info = _structured(info_response)
    if not str(info.get("ignitionVersion", "")).startswith("8.3.8"):
        raise ProbeError("gateway_info did not return the expected Gateway version")

    diagnose_response = client.call("tools/call", {"name": "gateway_diagnose", "arguments": {}})
    _write(raw_dir / "rest-gateway-diagnose.json", diagnose_response)
    diagnose = _structured(diagnose_response)
    if diagnose.get("gatewayReachable") is not True or diagnose.get("authenticationOk") is not True:
        raise ProbeError("gateway_diagnose did not prove connectivity/authentication")

    resources_response = client.call("resources/list", {})
    _write(raw_dir / "rest-resources-list.json", resources_response)
    resources = _list(resources_response, "resources")
    uris = {item.get("uri") for item in resources}
    expected_uris = {"ignition://gateway/capabilities", "ignition://gateway/openapi-info"}
    if uris != expected_uris:
        raise ProbeError(f"ignition-rest Resource inventory mismatch: {uris}")
    for uri in sorted(expected_uris):
        response = client.call("resources/read", {"uri": uri})
        _write(raw_dir / ("rest-resource-" + uri.rsplit("/", 1)[-1] + ".json"), response)

    prompts_response = client.call("prompts/list", {})
    _write(raw_dir / "rest-prompts-list.json", prompts_response)
    if _list(prompts_response, "prompts"):
        raise ProbeError("ignition-rest Phase 1 prompt inventory must be empty")

    live_status, _ = _http_json(base_url.rstrip("/") + "/health/live")
    ready_status, ready = _http_json(base_url.rstrip("/") + "/health/ready")
    if live_status != 200 or ready_status != 200 or not isinstance(ready, dict) or ready.get("ready") is not True:
        raise ProbeError("ignition-rest health endpoints are not ready")

    metrics_request = urllib.request.Request(base_url.rstrip("/") + "/metrics")
    with urllib.request.urlopen(metrics_request, timeout=10) as response:
        metrics = response.read().decode("utf-8")
    if "ignition_mcp_tool_calls_total" not in metrics:
        raise ProbeError("metrics endpoint did not expose the Phase 1 tool metric")

    return {
        "tools": sorted(str(name) for name in names),
        "resources": sorted(str(uri) for uri in uris),
        "gatewayInfo": info,
        "diagnose": diagnose,
        "healthLive": live_status,
        "healthReady": ready_status,
    }


def _unauthorized_runtime(url: str) -> int:
    request = urllib.request.Request(
        url,
        data=json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "unauthorized-probe", "version": "1"},
            },
        }).encode(),
        headers={"Accept": ACCEPT, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def probe_runtime(url: str, token: str, raw_dir: Path) -> dict[str, Any]:
    unauthorized = _unauthorized_runtime(url)
    if unauthorized not in {401, 403}:
        raise ProbeError(f"Runtime MCP unauthenticated initialize expected 401/403, got {unauthorized}")

    client = McpClient(url, api_token=token)
    init = client.initialize()
    _write(raw_dir / "runtime-initialize.json", init)

    tools_response = client.call("tools/list", {})
    _write(raw_dir / "runtime-tools-list.json", tools_response)
    tools = _list(tools_response, "tools")
    tools_by_name = _tools_by_name(tools)
    names = set(tools_by_name)
    expected_schemas = {
        "bundle_info": ({}, set()),
        "tag_browse": (
            {"path": "string", "recursive": "boolean", "maxResults": "integer"},
            {"path"},
        ),
        "tag_read": (
            {"tagPaths": "array", "timeout": "integer", "timestampFormat": "string"},
            {"tagPaths"},
        ),
    }
    if names != set(expected_schemas):
        raise ProbeError(f"Runtime Tool inventory mismatch: {names}")
    for name, (properties, required) in expected_schemas.items():
        tool = tools_by_name[name]
        _assert_input_schema(tool, properties=properties, required=required)
        if not isinstance(tool.get("description"), str) or not tool["description"].strip():
            raise ProbeError(f"Runtime {name} missing description")

    bundle_response = client.call("tools/call", {"name": "bundle_info", "arguments": {}})
    _write(raw_dir / "runtime-bundle-info.json", bundle_response)
    bundle = _structured(bundle_response)
    if bundle.get("gatewayVersion") != "8.3.8":
        raise ProbeError(f"bundle_info Gateway version mismatch: {bundle}")

    browse_response = client.call(
        "tools/call",
        {
            "name": "tag_browse",
            "arguments": {"path": "[System]Gateway", "recursive": False, "maxResults": 100},
        },
    )
    _write(raw_dir / "runtime-tag-browse.json", browse_response)
    browse = _structured(browse_response)
    if not isinstance(browse.get("nodes"), list) or not browse["nodes"]:
        raise ProbeError("tag_browse returned no Gateway System Tag nodes")

    read_response = client.call(
        "tools/call",
        {
            "name": "tag_read",
            "arguments": {
                "tagPaths": ["[System]Gateway/SystemName"],
                "timeout": 10000,
                "timestampFormat": "iso8601",
            },
        },
    )
    _write(raw_dir / "runtime-tag-read.json", read_response)
    read = _structured(read_response)
    items = read.get("items")
    if (
        not isinstance(items, list)
        or len(items) != 1
        or not isinstance(items[0], dict)
        or items[0].get("status") != "ok"
    ):
        raise ProbeError(f"tag_read SystemName smoke failed: {read}")

    resources_response = client.call("resources/list", {})
    _write(raw_dir / "runtime-resources-list.json", resources_response)
    resources = _list(resources_response, "resources")
    titles = {item.get("title") for item in resources}
    expected_titles = {"bundle_info_output", "tag_browse_output", "tag_read_output"}
    if titles != expected_titles:
        raise ProbeError(f"Runtime Resource inventory mismatch: {titles}")
    for index, resource in enumerate(resources):
        uri = resource.get("uri")
        if not isinstance(uri, str):
            raise ProbeError("Runtime Resource missing URI")
        response = client.call("resources/read", {"uri": uri})
        _write(raw_dir / f"runtime-resource-{index}.json", response)

    prompts_response = client.call("prompts/list", {})
    _write(raw_dir / "runtime-prompts-list.json", prompts_response)
    if _list(prompts_response, "prompts"):
        raise ProbeError("Runtime Phase 1 prompt inventory must be empty")

    return {
        "unauthenticatedInitializeHttpStatus": unauthorized,
        "tools": sorted(str(name) for name in names),
        "resourceTitles": sorted(str(title) for title in titles),
        "bundleInfo": bundle,
        "tagBrowseReturned": len(browse["nodes"]),
        "tagReadSystemName": items[0].get("value"),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--external-base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--runtime-url", default="http://127.0.0.1:8088/data/mcp/phase1-runtime")
    parser.add_argument("--runtime-api-token", required=True)
    parser.add_argument("--gateway-version", default="8.3.8")
    parser.add_argument("--gateway-build", required=True)
    parser.add_argument("--gateway-image", default="inductiveautomation/ignition:8.3.8")
    parser.add_argument("--gateway-image-digest", required=True)
    parser.add_argument("--module-version", default="1.3.5-SNAPSHOT")
    parser.add_argument("--module-artifact-version", default="1.3.5.2026021307-SNAPSHOT")
    parser.add_argument("--module-build", default="2026021307")
    parser.add_argument("--module-file", required=True, type=Path)
    parser.add_argument("--bundle-file", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--raw-dir", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        external = probe_external(args.external_base_url, args.raw_dir)
        runtime = probe_runtime(args.runtime_url, args.runtime_api_token, args.raw_dir)
        bundle_version = runtime["bundleInfo"].get("bundleVersion")
        openapi_sha256 = external["diagnose"].get("openapiSha256")
        if not isinstance(bundle_version, str) or not bundle_version:
            raise ProbeError("bundle_info did not return bundleVersion")
        if not isinstance(openapi_sha256, str) or len(openapi_sha256) != 64:
            raise ProbeError("gateway_diagnose did not return a valid OpenAPI SHA-256")
        evidence = {
            "schemaVersion": 2,
            "gate": "G1",
            "gatewayVersion": args.gateway_version,
            "gatewayBuild": args.gateway_build,
            "gatewayImage": args.gateway_image,
            "gatewayImageDigest": args.gateway_image_digest,
            "mcpModuleVersion": args.module_version,
            "mcpModuleArtifactVersion": args.module_artifact_version,
            "mcpModuleBuild": args.module_build,
            "mcpModuleSha256": _sha256_file(args.module_file),
            "bundleVersion": bundle_version,
            "bundleSha256": _sha256_file(args.bundle_file),
            "openapiSha256": openapi_sha256,
            "external": external,
            "runtime": runtime,
            "status": "VERIFIED",
        }
        _write(args.evidence, evidence)
        print(json.dumps(evidence, indent=2, sort_keys=True))
        return 0
    except Exception as error:
        _write(
            args.evidence,
            {
                "schemaVersion": 2,
                "gate": "G1",
                "status": "FAILED",
                "fatalError": f"{type(error).__name__}: {error}",
            },
        )
        print(f"G1 probe failed: {type(error).__name__}: {error}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
