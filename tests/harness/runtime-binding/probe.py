#!/usr/bin/env python3
"""Deterministic MCP Streamable HTTP probe for the Phase 0 G0 fixture."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = "2025-06-18"
ACCEPT = "application/json, text/event-stream"
EXPECTED_TOOLS = {"binding-failure", "binding-success"}
EXPECTED_RESOURCE_NAME = "fixture-info"
EXPECTED_PROMPT = "fixture-echo"
EXPECTED_RESOURCE_TEXT = "ignition-mcp phase-0 native binding fixture"


class ProbeError(RuntimeError):
    pass


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _parse_streamable_body(body: bytes, content_type: str) -> dict[str, Any] | None:
    if not body.strip():
        return None
    text_body = body.decode("utf-8")
    if "text/event-stream" in content_type:
        events: list[dict[str, Any]] = []
        for line in text_body.splitlines():
            if line.startswith("data:"):
                payload = line[5:].strip()
                if payload:
                    decoded = json.loads(payload)
                    if isinstance(decoded, dict):
                        events.append(decoded)
        if not events:
            raise ProbeError("SSE response contained no JSON data event")
        return events[-1]
    decoded = json.loads(text_body)
    if not isinstance(decoded, dict):
        raise ProbeError("JSON-RPC response must be an object")
    return decoded


@dataclass
class McpClient:
    url: str
    timeout: float
    api_token: str | None = None
    session_id: str | None = None
    request_id: int = 0

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": ACCEPT,
            "Content-Type": "application/json",
            "User-Agent": "ignition-mcp-g0-probe/1",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        if self.api_token:
            headers["X-Ignition-API-Token"] = self.api_token
        return headers

    def post(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any] | None, dict[str, str]]:
        request = urllib.request.Request(
            self.url,
            data=_json_bytes(payload),
            headers=self._headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                status = response.status
                headers = {k.lower(): v for k, v in response.headers.items()}
                body = response.read()
        except urllib.error.HTTPError as error:
            body = error.read()
            raise ProbeError(
                f"HTTP {error.code} from MCP endpoint: {body[:1000].decode('utf-8', errors='replace')}"
            ) from error
        except urllib.error.URLError as error:
            raise ProbeError(f"MCP endpoint unavailable: {error}") from error
        except (OSError, TimeoutError, http.client.HTTPException) as error:
            raise ProbeError(f"MCP transport unavailable: {type(error).__name__}: {error}") from error
        try:
            parsed = _parse_streamable_body(body, headers.get("content-type", ""))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ProbeError(f"MCP response was not parseable yet: {type(error).__name__}: {error}") from error
        session = headers.get("mcp-session-id")
        if session:
            self.session_id = session
        return status, parsed, headers

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.request_id += 1
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": self.request_id, "method": method}
        if params is not None:
            payload["params"] = params
        _, response, _ = self.post(payload)
        if response is None:
            raise ProbeError(f"{method} returned no JSON-RPC response")
        if response.get("id") != self.request_id:
            raise ProbeError(f"{method} response id mismatch")
        if "error" in response:
            raise ProbeError(f"{method} JSON-RPC error: {json.dumps(response['error'], sort_keys=True)}")
        result = response.get("result")
        if not isinstance(result, dict):
            raise ProbeError(f"{method} result must be an object")
        return response

    def notify_initialized(self) -> None:
        status, response, _ = self.post(
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
        )
        if status not in {200, 202}:
            raise ProbeError(f"notifications/initialized returned HTTP {status}")
        if response is not None and "error" in response:
            raise ProbeError(f"notifications/initialized failed: {response['error']}")


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fetch_bytes(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "ignition-mcp-g0-probe/1"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _extract_list(response: dict[str, Any], key: str) -> list[dict[str, Any]]:
    result = response.get("result")
    if not isinstance(result, dict) or not isinstance(result.get(key), list):
        raise ProbeError(f"response missing result.{key} array")
    items = result[key]
    if not all(isinstance(item, dict) for item in items):
        raise ProbeError(f"result.{key} entries must be objects")
    return items


def _content_text(result: dict[str, Any]) -> str:
    content = result.get("contents") or result.get("content")
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            parts.append(item["text"])
    return "".join(parts)


def characterize(args: argparse.Namespace) -> tuple[dict[str, Any], bool]:
    raw_dir = Path(args.raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    checks = {
        "initialize": False,
        "toolsList": False,
        "resourcesList": False,
        "resourcesRead": False,
        "promptsList": False,
        "promptsGet": False,
        "outputSchemaPublished": False,
        "successStructuredContent": False,
        "failureIsError": False,
    }
    observations: list[str] = []
    client = McpClient(args.url, args.request_timeout, args.api_token)

    deadline = time.monotonic() + args.startup_timeout
    initialize_response: dict[str, Any] | None = None
    last_error = ""
    while time.monotonic() < deadline:
        try:
            initialize_response = client.call(
                "initialize",
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "ignition-mcp-g0-probe", "version": "1.0.0"},
                },
            )
            break
        except ProbeError as error:
            last_error = str(error)
            client.session_id = None
            time.sleep(args.poll_interval)
    if initialize_response is None:
        raise ProbeError(f"initialize did not become ready within {args.startup_timeout}s: {last_error}")

    _write_json(raw_dir / "initialize.json", initialize_response)
    init_result = initialize_response["result"]
    checks["initialize"] = init_result.get("protocolVersion") == PROTOCOL_VERSION and bool(client.session_id)
    if not client.session_id:
        observations.append("initialize response did not publish Mcp-Session-Id")
    client.notify_initialized()

    tools_response = client.call("tools/list", {})
    _write_json(raw_dir / "tools-list.json", tools_response)
    tools = _extract_list(tools_response, "tools")
    tool_names = {item.get("name") for item in tools if isinstance(item.get("name"), str)}
    checks["toolsList"] = tool_names == EXPECTED_TOOLS
    if tool_names != EXPECTED_TOOLS:
        observations.append(f"tool inventory mismatch: {sorted(tool_names)}")
    success_tool = next((item for item in tools if item.get("name") == "binding-success"), None)
    checks["outputSchemaPublished"] = isinstance(success_tool, dict) and isinstance(
        success_tool.get("outputSchema"), dict
    )
    if not checks["outputSchemaPublished"]:
        observations.append("binding-success did not publish outputSchema in tools/list")

    resources_response = client.call("resources/list", {})
    _write_json(raw_dir / "resources-list.json", resources_response)
    resources = _extract_list(resources_response, "resources")
    resource = next((item for item in resources if item.get("name") == EXPECTED_RESOURCE_NAME), None)
    checks["resourcesList"] = resource is not None and len(resources) == 1
    if resource is not None and isinstance(resource.get("uri"), str):
        read_response = client.call("resources/read", {"uri": resource["uri"]})
        _write_json(raw_dir / "resources-read.json", read_response)
        checks["resourcesRead"] = EXPECTED_RESOURCE_TEXT in _content_text(read_response["result"])
    else:
        observations.append("fixture-info resource missing or has no URI")

    prompts_response = client.call("prompts/list", {})
    _write_json(raw_dir / "prompts-list.json", prompts_response)
    prompts = _extract_list(prompts_response, "prompts")
    prompt = next((item for item in prompts if item.get("name") == EXPECTED_PROMPT), None)
    checks["promptsList"] = prompt is not None and len(prompts) == 1
    if prompt is not None:
        prompt_response = client.call(
            "prompts/get", {"name": EXPECTED_PROMPT, "arguments": {"value": "phase0"}}
        )
        _write_json(raw_dir / "prompts-get.json", prompt_response)
        result = prompt_response["result"]
        messages = result.get("messages") if isinstance(result, dict) else None
        checks["promptsGet"] = (
            isinstance(messages, list)
            and len(messages) == 1
            and isinstance(messages[0], dict)
            and messages[0].get("role") == "user"
            and isinstance(messages[0].get("content"), dict)
            and messages[0]["content"].get("text") == "Echo: phase0"
        )
    else:
        observations.append("fixture-echo prompt missing")

    success_response = client.call(
        "tools/call", {"name": "binding-success", "arguments": {"message": "phase0"}}
    )
    _write_json(raw_dir / "tools-call-success.json", success_response)
    success_result = success_response["result"]
    structured = success_result.get("structuredContent")
    checks["successStructuredContent"] = structured == {
        "kind": "phase0-binding",
        "message": "phase0",
        "schemaVersion": 1,
    } and success_result.get("isError") is False
    if not checks["successStructuredContent"]:
        observations.append(
            "success result did not contain the expected structuredContent/isError=false"
        )

    failure_response = client.call(
        "tools/call", {"name": "binding-failure", "arguments": {}}
    )
    _write_json(raw_dir / "tools-call-failure.json", failure_response)
    failure_result = failure_response["result"]
    checks["failureIsError"] = failure_result.get("isError") is True
    if not checks["failureIsError"]:
        observations.append("deliberate failure result did not contain isError=true")

    try:
        openapi = _fetch_bytes(args.openapi_url, args.request_timeout)
        openapi_sha256 = hashlib.sha256(openapi).hexdigest()
        (raw_dir / "openapi.json").write_bytes(openapi)
    except Exception as error:
        openapi_sha256 = None
        observations.append(
            f"OpenAPI fingerprint unavailable: {type(error).__name__}: {error}"
        )

    verified = all(checks.values())
    status = "VERIFIED" if verified else "FAILED"
    evidence = {
        "schemaVersion": 2,
        "gate": "G0",
        "gatewayVersion": args.gateway_version,
        "gatewayBuild": args.gateway_build,
        "gatewayImage": args.gateway_image,
        "gatewayImageDigest": args.gateway_image_digest,
        "mcpModuleVersion": args.module_version,
        "mcpModuleBuild": args.module_build,
        "mcpModuleSha256": _sha256_file(Path(args.module_file)),
        "bundleVersion": "phase0-characterization-fixture-1",
        "bundleSha256": _sha256_file(Path(args.fixture_zip)),
        "openapiSha256": openapi_sha256,
        **checks,
        "nativeResponseBindingStatus": status,
        "observations": observations,
    }
    return evidence, verified


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8088/data/mcp/phase0")
    parser.add_argument("--openapi-url", default="http://127.0.0.1:8088/openapi.json")
    parser.add_argument("--api-token")
    parser.add_argument("--gateway-version", default="8.3.8")
    parser.add_argument("--gateway-build", required=True)
    parser.add_argument("--gateway-image", default="inductiveautomation/ignition:8.3.8")
    parser.add_argument("--gateway-image-digest", required=True)
    parser.add_argument("--module-version", default="1.3.5.2026021307-SNAPSHOT")
    parser.add_argument("--module-build", default="2026021307")
    parser.add_argument("--module-file", required=True)
    parser.add_argument("--fixture-zip", required=True)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--raw-dir", required=True)
    parser.add_argument("--startup-timeout", type=float, default=300.0)
    parser.add_argument("--request-timeout", type=float, default=10.0)
    parser.add_argument("--poll-interval", type=float, default=3.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    evidence_path = Path(args.evidence)
    try:
        evidence, verified = characterize(args)
    except Exception as error:
        failure = {
            "schemaVersion": 2,
            "gate": "G0",
            "nativeResponseBindingStatus": "FAILED",
            "fatalError": f"{type(error).__name__}: {error}",
        }
        _write_json(evidence_path, failure)
        print(f"G0 probe failed: {failure['fatalError']}", file=sys.stderr)
        return 2
    _write_json(evidence_path, evidence)
    print(json.dumps(evidence, indent=2, sort_keys=True))
    return 0 if verified else 3


if __name__ == "__main__":
    raise SystemExit(main())
