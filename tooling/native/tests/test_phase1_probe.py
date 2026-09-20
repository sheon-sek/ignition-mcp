"""Probe regressions: test protocol decisions, not a fake Ignition runtime."""
from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("g1_probe", ROOT / "tests/harness/phase1-live/probe.py")
assert SPEC and SPEC.loader
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class Client:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = 0

    def call(self, method, params):
        assert method == "prompts/list"
        self.calls += 1
        if self.error:
            raise self.error
        return self.response


def test_absent_prompts_capability_is_not_a_successful_list(tmp_path):
    client = Client(error=AssertionError("must not call unsupported method"))
    result = probe.probe_empty_prompts(client, {"result": {"capabilities": {}}}, tmp_path, "runtime")
    assert result == {"inventory": [], "capabilityAdvertised": False, "listStatus": "NOT_APPLICABLE"}
    assert client.calls == 0


def test_advertised_empty_prompts_is_actually_listed(tmp_path):
    client = Client({"result": {"prompts": []}})
    result = probe.probe_empty_prompts(client, {"result": {"capabilities": {"prompts": {}}}}, tmp_path, "runtime")
    assert result["listStatus"] == "PASS"
    assert client.calls == 1


@pytest.mark.parametrize("response", [{"result": {"prompts": [{"name": "unexpected"}]}},
                                     {"result": {"prompts": [], "nextCursor": "more"}}])
def test_unexpected_prompt_inventory_is_rejected(tmp_path, response):
    with pytest.raises(probe.ProbeError):
        probe.probe_empty_prompts(Client(response), {"result": {"capabilities": {"prompts": {}}}}, tmp_path, "runtime")


def test_advertised_capability_error_is_not_swallowed(tmp_path):
    with pytest.raises(probe.ProbeError, match="broken"):
        probe.probe_empty_prompts(Client(error=probe.ProbeError("broken")),
                                 {"result": {"capabilities": {"prompts": {}}}}, tmp_path, "runtime")


def test_structured_checker_rejects_error_and_text_divergence():
    with pytest.raises(probe.ProbeError):
        probe._structured({"result": {"isError": True, "structuredContent": {}}})
    with pytest.raises(probe.ProbeError, match="disagree"):
        probe._structured({"result": {"structuredContent": {}, "content": [{"type": "text", "text": '{"x":null}'}]}})


@pytest.mark.parametrize("name", ["bundle-info", "tag-read", "tag-browse"])
def test_resource_validation_uses_declared_mime_size_and_payload(name):
    directory = ROOT / "packages/ignition-runtime-bundle/project/com.inductiveautomation.mcp/resources/contracts" / (name + "-output")
    metadata = json.loads((directory / "resource.json").read_text())["attributes"]
    resource = {"title": metadata["title"], "mimeType": metadata["mimeType"],
                "size": metadata["size"], "uri": "ignition://test"}
    contents = [{"text": (directory / "data.bin").read_text(), "mimeType": metadata["mimeType"],
                 "uri": resource["uri"]}]
    probe.validate_runtime_resource(resource, contents)
    contents[0]["mimeType"] = "text/plain"
    with pytest.raises(probe.ProbeError, match="metadata"):
        probe.validate_runtime_resource(resource, contents)


def native_null_encoder():
    # Extract only a pure helper from the actual handler, no Java/native emulation.
    path = ROOT / "packages/ignition-runtime-bundle/project/com.inductiveautomation.mcp/tools/tag_read/onToolCalled.py"
    handler = ast.parse(path.read_text()).body[0]
    helper = next(node for node in handler.body if isinstance(node, ast.FunctionDef) and node.name == "encodeNulls")
    namespace = {}
    exec(compile(ast.Module(body=[helper], type_ignores=[]), str(path), "exec"), namespace)
    return namespace["encodeNulls"]


def decode_nulls(value):
    if isinstance(value, list):
        return [decode_nulls(child) for child in value]
    if isinstance(value, dict):
        if value == {"$ignition": "null"}:
            return None
        if value.get("$ignition") == "object":
            return {key: decode_nulls(child) for key, child in value["entries"]}
        return {key: decode_nulls(child) for key, child in value.items()}
    return value


@pytest.mark.parametrize("value", [None, [None, 0, False, "中文"], {"x": None}, {"$ignition": "null"},
                                  {"$ignition": "object", "entries": [["x", None]]},
                                  {"nested": [{"$ignition": None, "other": {"x": None}}]}])
def test_native_null_encoding_is_collision_free_and_schema_valid(value):
    encoded = native_null_encoder()(value)
    assert decode_nulls(encoded) == value
    schema = json.loads((ROOT / "contracts/schemas/tag-read.output.schema.json").read_text())
    Draft202012Validator({"$ref": "#/$defs/value", "$defs": schema["$defs"]}).validate(encoded)
    # Simulate only the documented wire defect: no encoded object has null members.
    def check(node):
        if isinstance(node, dict):
            assert all(child is not None for child in node.values())
            for child in node.values():
                check(child)
        elif isinstance(node, list):
            for child in node:
                check(child)
    check(encoded)
