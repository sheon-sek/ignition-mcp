#!/usr/bin/env python3
"""Phase 2 G2 dual-plane real-Gateway probe."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from protocol import McpClient, ProbeError, list_result

ROOT = Path(__file__).resolve().parents[3]
PROJECT = ROOT / "packages/ignition-runtime-bundle/project"
SCHEMAS = ROOT / "contracts/schemas"
PROFILE = ROOT / "contracts/profiles/readonly.yaml"
D27_MODULE_SHA256 = "b1142a5796f2fd834555f13f03de706599d745f7172a68e54f2f7908b67fe365"
D27_IDENTITY = ("8.3.8", "2026071409", "1.3.5-SNAPSHOT", "2026021307", D27_MODULE_SHA256)


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tools_by_name(tools: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for tool in tools:
        name = tool.get("name")
        if not isinstance(name, str) or not name or name in result:
            raise ProbeError(f"invalid or duplicate Tool name: {name!r}")
        result[name] = tool
    return result


def _assert_input_schema(tool: dict[str, Any], expected: dict[str, Any]) -> None:
    name = str(tool.get("name"))
    schema = tool.get("inputSchema")
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise ProbeError(f"{name} inputSchema must be an object schema")
    properties = schema.get("properties")
    if not isinstance(properties, dict) or set(properties) != set(expected):
        raise ProbeError(f"{name} parameter inventory mismatch")
    required = set(schema.get("required", []))
    expected_required = {key for key, descriptor in expected.items() if descriptor["required"]}
    if required != expected_required:
        raise ProbeError(f"{name} required parameter mismatch: {required} != {expected_required}")
    for parameter, descriptor in expected.items():
        actual = properties.get(parameter)
        if not isinstance(actual, dict) or actual.get("type") != descriptor["type"]:
            raise ProbeError(f"{name}.{parameter} type mismatch")


def _runtime_source_tools() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    root = PROJECT / "com.inductiveautomation.mcp/tools"
    for resource_path in root.glob("*/resource.json"):
        resource = json.loads(resource_path.read_text(encoding="utf-8"))
        attributes = resource["attributes"]
        result[attributes["title"]] = {
            parameter["name"]: {
                "type": parameter["type"],
                "required": parameter["required"],
            }
            for parameter in attributes["parameters"]
        }
    return result


def _schema_name(tool: str) -> str:
    return tool.replace("_", "-") + ".output.schema.json"


def _structured(response: dict[str, Any], tool: str) -> dict[str, Any]:
    result = response.get("result")
    if not isinstance(result, dict) or result.get("isError", False) is not False:
        raise ProbeError(f"{tool} expected successful Tool result")
    value = result.get("structuredContent")
    if not isinstance(value, dict):
        raise ProbeError(f"{tool} omitted structuredContent")
    text_values = []
    for content in result.get("content", []):
        if isinstance(content, dict) and content.get("type") == "text":
            text_values.append(json.loads(content["text"]))
    if text_values and any(text != value for text in text_values):
        raise ProbeError(f"{tool} text and structuredContent disagree")
    schema = json.loads((SCHEMAS / _schema_name(tool)).read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(value)
    return value


def _call_success(
    client: McpClient,
    raw_dir: Path,
    plane: str,
    tool: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    response = client.call("tools/call", {"name": tool, "arguments": arguments})
    _write(raw_dir / f"{plane}-{tool}.json", response)
    return _structured(response, tool)


def _call_error(
    client: McpClient,
    raw_dir: Path,
    plane: str,
    tool: str,
    arguments: dict[str, Any],
    expected_code: str,
) -> dict[str, Any]:
    response = client.call("tools/call", {"name": tool, "arguments": arguments})
    _write(raw_dir / f"{plane}-{tool}-error-{expected_code}.json", response)
    result = response.get("result")
    if not isinstance(result, dict) or result.get("isError") is not True:
        raise ProbeError(f"{tool} expected native isError=true")
    content = result.get("content")
    if not isinstance(content, list) or len(content) != 1 or content[0].get("type") != "text":
        raise ProbeError(f"{tool} Tool Error must contain one canonical JSON text item")
    error = json.loads(content[0]["text"])
    if set(error) != {"code", "message", "correlationId"} or error.get("code") != expected_code:
        raise ProbeError(f"{tool} returned unexpected canonical error: {error}")
    return error


def _empty_prompts(client: McpClient, initialize: dict[str, Any], raw_dir: Path, plane: str) -> dict[str, Any]:
    capabilities = initialize["result"].get("capabilities")
    if not isinstance(capabilities, dict):
        raise ProbeError("initialize capabilities must be an object")
    if "prompts" not in capabilities:
        observation = {"inventory": [], "capabilityAdvertised": False, "listStatus": "NOT_APPLICABLE"}
    else:
        response = client.call("prompts/list", {})
        _write(raw_dir / f"{plane}-prompts-list.json", response)
        if list_result(response, "prompts") or response["result"].get("nextCursor"):
            raise ProbeError(f"{plane} Prompt inventory must be exactly empty")
        observation = {"inventory": [], "capabilityAdvertised": True, "listStatus": "PASS"}
    _write(raw_dir / f"{plane}-prompts-observation.json", observation)
    return observation


def _validate_runtime_resources(client: McpClient, raw_dir: Path) -> list[str]:
    response = client.call("resources/list", {})
    _write(raw_dir / "runtime-resources-list.json", response)
    resources = list_result(response, "resources")
    profile = json.loads(PROFILE.read_text(encoding="utf-8"))
    expected_uris = set(profile["resources"])
    if {item.get("uri") for item in resources} != expected_uris or len(resources) != len(expected_uris):
        raise ProbeError("Runtime Resource inventory mismatch")
    source = PROJECT / "com.inductiveautomation.mcp/resources/contracts"
    for index, resource in enumerate(resources):
        uri = resource.get("uri")
        title = resource.get("title")
        if not isinstance(uri, str) or not isinstance(title, str):
            raise ProbeError("Runtime Resource metadata is incomplete")
        directory = source / (title.replace("_", "-").removesuffix("-output") + "-output")
        metadata = json.loads((directory / "resource.json").read_text(encoding="utf-8"))["attributes"]
        read_response = client.call("resources/read", {"uri": uri})
        _write(raw_dir / f"runtime-resource-{index}.json", read_response)
        contents = list_result(read_response, "contents")
        if len(contents) != 1:
            raise ProbeError("Runtime Resource read must return one content item")
        content = contents[0]
        text = content.get("text")
        if (
            resource.get("mimeType") != metadata["mimeType"]
            or resource.get("size") != metadata["size"]
            or content.get("mimeType") != metadata["mimeType"]
            or content.get("uri") != uri
            or not isinstance(text, str)
            or len(text.encode("utf-8")) != metadata["size"]
            or json.loads(text) != json.loads((directory / "data.bin").read_text(encoding="utf-8"))
        ):
            raise ProbeError("Runtime Resource metadata/payload mismatch")
    return sorted(expected_uris)


def probe_runtime(
    url: str,
    token: str,
    raw_dir: Path,
    *,
    d27_identity: bool,
) -> dict[str, Any]:
    client = McpClient(url, api_token=token)
    initialize = client.initialize()
    _write(raw_dir / "runtime-initialize.json", initialize)
    tools_response = client.call("tools/list", {})
    _write(raw_dir / "runtime-tools-list.json", tools_response)
    tools = _tools_by_name(list_result(tools_response, "tools"))
    source_tools = _runtime_source_tools()
    profile = json.loads(PROFILE.read_text(encoding="utf-8"))
    if set(tools) != set(profile["tools"]) or set(tools) != set(source_tools):
        raise ProbeError("Runtime exact Tool inventory mismatch")
    output_schema_published = True
    for name, expected in source_tools.items():
        tool = tools[name]
        _assert_input_schema(tool, expected)
        if not isinstance(tool.get("description"), str) or not tool["description"].strip():
            raise ProbeError(f"Runtime {name} missing description")
        output_schema_published = output_schema_published and isinstance(tool.get("outputSchema"), dict)
    if d27_identity and output_schema_published:
        raise ProbeError("D27 baseline behavior changed; re-characterization is required")

    smokes: dict[str, Any] = {}
    smokes["bundle_info"] = _call_success(client, raw_dir, "runtime", "bundle_info", {})
    smokes["tag_browse"] = _call_success(client, raw_dir, "runtime", "tag_browse", {
        "path": "[default]_mcp_ci", "recursive": False, "maxResults": 100,
    })
    smokes["tag_query"] = _call_success(client, raw_dir, "runtime", "tag_query", {
        "provider": "default", "pathPattern": "_mcp_ci/*", "namePattern": "*",
        "tagType": "", "valueSource": "", "includeUdtMembers": True,
        "returnProperties": ["path", "name", "tagType"], "maxResults": 100, "continuation": "",
    })
    smokes["tag_read"] = _call_success(client, raw_dir, "runtime", "tag_read", {
        "tagPaths": ["[default]_mcp_ci/Value"], "timeout": 10000, "timestampFormat": "iso8601",
    })
    smokes["tag_get_config"] = _call_success(client, raw_dir, "runtime", "tag_get_config", {
        "path": "[default]_mcp_ci", "recursive": True, "overridesOnly": False, "maxResults": 50,
    })
    smokes["udt_type_list"] = _call_success(client, raw_dir, "runtime", "udt_type_list", {
        "provider": "default", "maxResults": 100,
    })
    smokes["udt_type_get"] = _call_success(client, raw_dir, "runtime", "udt_type_get", {
        "provider": "default", "typePath": "McpCiType", "maxResults": 200,
    })
    now = datetime.now(timezone.utc)
    start = now - timedelta(minutes=15)
    # alarm_status / alarm_journal are deliberately absent: deferred per the D12
    # Phase 2 bounded-execution amendment until a pre/during-execution bound exists.
    smokes["alarm_shelved_list"] = _call_success(
        client, raw_dir, "runtime", "alarm_shelved_list", {"maxResults": 100}
    )
    browse = _call_success(client, raw_dir, "runtime", "historian_browse", {
        "rootPath": "histprov:MCP_CI_HISTORY", "nameFilters": [], "recursive": True,
        "maxResults": 100, "continuation": "",
    })
    smokes["historian_browse"] = browse
    historical_paths = [item.get("path") for item in browse.get("items", []) if isinstance(item.get("path"), str)]
    if not historical_paths:
        raise ProbeError("historian_browse did not discover the CI historical Tag")
    history_path = historical_paths[0]
    history_window = {
        "paths": [history_path],
        "startTime": start.isoformat().replace("+00:00", "Z"),
        "endTime": now.isoformat().replace("+00:00", "Z"),
    }
    smokes["historian_query_series"] = _call_success(
        client, raw_dir, "runtime", "historian_query_series", {**history_window, "sampleCount": 10}
    )
    smokes["historian_query_aggregate"] = _call_success(
        client, raw_dir, "runtime", "historian_query_aggregate", {**history_window, "aggregates": ["Average"]}
    )
    smokes["database_query_list"] = _call_success(client, raw_dir, "runtime", "database_query_list", {})
    smokes["database_query"] = _call_success(client, raw_dir, "runtime", "database_query", {
        "alias": "ci_page", "parameters": {}, "pageSize": 2, "offset": 0,
    })
    # The fixture table has exactly three rows: a full 2-row page must continue at 2.
    db_page = smokes["database_query"]["page"]
    if db_page["mode"] != "offset" or db_page["nextOffset"] != 2 or len(smokes["database_query"]["rows"]) != 2:
        raise ProbeError("database_query bounded pagination continuation mismatch")

    errors = {
        "databaseUnapproved": _call_error(
            client, raw_dir, "runtime", "database_query",
            {"alias": "not_approved", "parameters": {}, "pageSize": 0, "offset": 0}, "not_found",
        ),
        "aggregateEnum": _call_error(
            client, raw_dir, "runtime", "historian_query_aggregate",
            {**history_window, "aggregates": ["INVALID"]}, "invalid_argument",
        ),
    }
    resources = _validate_runtime_resources(client, raw_dir)
    prompts = _empty_prompts(client, initialize, raw_dir, "runtime")
    return {
        "tools": sorted(tools),
        "resources": resources,
        "prompts": prompts,
        "smokeTools": sorted(smokes),
        "negativeCases": sorted(errors),
        "outputSchemaPublished": output_schema_published,
        "successStructuredContent": True,
        "failureIsError": True,
        "outputSchemasValidated": True,
        "nativeResponseBindingAccepted": output_schema_published or d27_identity,
        "nativeResponseBinding": "VERIFIED" if output_schema_published else (
            "VERIFIED_WITH_LIMITATION" if d27_identity else "UNVERIFIED_LIMITATION"
        ),
    }


def probe_external(base_url: str, gateway_version: str, raw_dir: Path) -> dict[str, Any]:
    client = McpClient(base_url.rstrip("/") + "/mcp")
    initialize = client.initialize()
    _write(raw_dir / "rest-initialize.json", initialize)
    tools_response = client.call("tools/list", {})
    _write(raw_dir / "rest-tools-list.json", tools_response)
    tools = _tools_by_name(list_result(tools_response, "tools"))
    expected = {
        "gateway_info", "gateway_diagnose", "project_list", "config_resource_search",
        "config_resource_describe", "config_resource_names", "config_resource_list",
        "config_resource_get", "audit_query", "alarm_pipeline_list", "alarm_pipeline_status",
    }
    if set(tools) != expected:
        raise ProbeError(f"ignition-rest exact Tool inventory mismatch: {set(tools)} != {expected}")
    for name, tool in tools.items():
        if not isinstance(tool.get("outputSchema"), dict):
            raise ProbeError(f"ignition-rest {name} missing outputSchema")

    smokes: dict[str, Any] = {}
    smokes["gateway_info"] = _call_success(client, raw_dir, "rest", "gateway_info", {})
    if not str(smokes["gateway_info"].get("ignitionVersion", "")).startswith(gateway_version):
        raise ProbeError("gateway_info version mismatch")
    smokes["gateway_diagnose"] = _call_success(client, raw_dir, "rest", "gateway_diagnose", {})
    smokes["project_list"] = _call_success(client, raw_dir, "rest", "project_list", {
        "search": "", "limit": 100, "offset": 0,
    })
    smokes["config_resource_search"] = _call_success(client, raw_dir, "rest", "config_resource_search", {
        "query": "database", "limit": 100, "offset": 0,
    })
    smokes["config_resource_describe"] = _call_success(client, raw_dir, "rest", "config_resource_describe", {
        "resourceType": "ignition/database-connection",
    })
    smokes["config_resource_names"] = _call_success(client, raw_dir, "rest", "config_resource_names", {
        "resourceType": "ignition/database-connection", "search": "MCP_CI", "limit": 100, "offset": 0,
    })
    smokes["config_resource_list"] = _call_success(client, raw_dir, "rest", "config_resource_list", {
        "resourceType": "ignition/database-connection", "search": "MCP_CI", "limit": 100, "offset": 0,
    })
    smokes["config_resource_get"] = _call_success(client, raw_dir, "rest", "config_resource_get", {
        "resourceType": "ignition/database-connection", "name": "MCP_CI_DB",
        "collection": "", "defaultIfUndefined": False,
    })
    smokes["audit_query"] = _call_success(client, raw_dir, "rest", "audit_query", {
        "profile": "MCP_CI_AUDIT", "actor": "", "action": "", "target": "", "value": "",
        "system": "", "originatingContext": "", "startTime": "", "endTime": "", "limit": 100, "offset": 0,
    })
    smokes["alarm_pipeline_list"] = _call_success(client, raw_dir, "rest", "alarm_pipeline_list", {
        "search": "", "limit": 100, "offset": 0,
    })
    notes: dict[str, str] = {}
    pipelines = smokes["alarm_pipeline_list"].get("items", [])
    first_path = next(
        (item["path"] for item in pipelines if isinstance(item, dict) and isinstance(item.get("path"), str)),
        None,
    )
    if first_path is not None:
        smokes["alarm_pipeline_status"] = _call_success(client, raw_dir, "rest", "alarm_pipeline_status", {
            "path": first_path, "limit": 100, "offset": 0,
        })
        notes["alarm_pipeline_status"] = "live-pipeline"
    else:
        # A fresh Gateway has no notification pipelines. That is a domain fact, not a
        # harness pass: the Tool must still reach the real Gateway and return the
        # canonical not_found Tool Error instead of a fabricated empty success.
        _call_error(client, raw_dir, "rest", "alarm_pipeline_status", {
            "path": "__mcp_ci_no_pipeline__", "limit": 100, "offset": 0,
        }, "not_found")
        notes["alarm_pipeline_status"] = "canonical-not-found"

    resources_response = client.call("resources/list", {})
    _write(raw_dir / "rest-resources-list.json", resources_response)
    resources = list_result(resources_response, "resources")
    expected_uris = {"ignition://gateway/capabilities", "ignition://gateway/openapi-info"}
    if {item.get("uri") for item in resources} != expected_uris or len(resources) != 2:
        raise ProbeError("ignition-rest Resource inventory mismatch")
    for index, resource in enumerate(resources):
        response = client.call("resources/read", {"uri": resource["uri"]})
        _write(raw_dir / f"rest-resource-{index}.json", response)
        if len(list_result(response, "contents")) != 1:
            raise ProbeError("ignition-rest Resource read must return one content item")
    prompts = _empty_prompts(client, initialize, raw_dir, "rest")
    return {
        "tools": sorted(tools),
        "resources": sorted(expected_uris),
        "prompts": prompts,
        "smokeTools": sorted(smokes),
        "smokeNotes": notes,
        "openapiSha256": smokes["gateway_diagnose"].get("openapiSha256"),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--external-base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--runtime-url", default="http://127.0.0.1:8088/data/mcp/phase2-runtime")
    parser.add_argument("--runtime-api-token", required=True)
    parser.add_argument("--gateway-version", required=True)
    parser.add_argument("--gateway-build", required=True)
    parser.add_argument("--gateway-image", required=True)
    parser.add_argument("--gateway-image-digest", required=True)
    parser.add_argument("--module-version", required=True)
    parser.add_argument("--module-artifact-version", required=True)
    parser.add_argument("--module-build", required=True)
    parser.add_argument("--module-file", required=True, type=Path)
    parser.add_argument("--bundle-file", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--raw-dir", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    stage = "identity"
    module_sha = _sha256(args.module_file)
    d27_identity = (
        args.gateway_version, args.gateway_build, args.module_version, args.module_build, module_sha
    ) == D27_IDENTITY
    try:
        stage = "external"
        external = probe_external(args.external_base_url, args.gateway_version, args.raw_dir)
        stage = "runtime"
        runtime = probe_runtime(args.runtime_url, args.runtime_api_token, args.raw_dir, d27_identity=d27_identity)
        stage = "evidence"
        openapi_sha = external.get("openapiSha256")
        if not isinstance(openapi_sha, str) or len(openapi_sha) != 64:
            raise ProbeError("Gateway OpenAPI SHA-256 is missing or invalid")
        binding_accepted = bool(runtime["nativeResponseBindingAccepted"])
        evidence = {
            "schemaVersion": 2,
            "gate": "G2",
            "gatewayVersion": args.gateway_version,
            "gatewayBuild": args.gateway_build,
            "gatewayImage": args.gateway_image,
            "gatewayImageDigest": args.gateway_image_digest,
            "mcpModuleVersion": args.module_version,
            "mcpModuleArtifactVersion": args.module_artifact_version,
            "mcpModuleBuild": args.module_build,
            "mcpModuleSha256": module_sha,
            "bundleVersion": (ROOT / "packages/ignition-runtime-bundle/BUNDLE_VERSION").read_text(encoding="utf-8").strip(),
            "bundleSha256": _sha256(args.bundle_file),
            "openapiSha256": openapi_sha,
            "external": external,
            "runtime": runtime,
            "status": "VERIFIED" if binding_accepted else "FAILED_NATIVE_BINDING",
            "nativeResponseBinding": runtime["nativeResponseBinding"],
            "d27ExceptionApplied": d27_identity and not runtime["outputSchemaPublished"],
            "runtimeNullEncoding": "ignition-null-v1",
            "compatibilityStatus": "UNTESTED",
        }
        _write(args.evidence, evidence)
        print(json.dumps(evidence, indent=2, sort_keys=True))
        return 0 if binding_accepted else 3
    except Exception as error:
        _write(args.evidence, {
            "schemaVersion": 2,
            "gate": "G2",
            "gatewayVersion": args.gateway_version,
            "gatewayBuild": args.gateway_build,
            "gatewayImage": args.gateway_image,
            "gatewayImageDigest": args.gateway_image_digest,
            "mcpModuleVersion": args.module_version,
            "mcpModuleBuild": args.module_build,
            "mcpModuleSha256": module_sha,
            "status": "FAILED",
            "stage": stage,
            "fatalError": f"{type(error).__name__}: {error}",
            "compatibilityStatus": "UNTESTED",
        })
        print(f"G2 probe failed at {stage}: {type(error).__name__}: {error}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
