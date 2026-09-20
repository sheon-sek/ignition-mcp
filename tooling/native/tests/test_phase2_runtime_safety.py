"""Pure/static regressions for Phase 2 Runtime safety boundaries.

These tests deliberately do not emulate Ignition. Real Jython/JVM behavior remains a G2
live-Gateway responsibility under D23.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
import re
from typing import Any

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[3]
TOOLS = ROOT / "packages/ignition-runtime-bundle/project/com.inductiveautomation.mcp/tools"
DEFERRED = ROOT / "packages/ignition-runtime-bundle/deferred"
CONTRACTS = ROOT / "contracts/tools/runtime"
PROFILE = ROOT / "contracts/profiles/readonly.yaml"


class _System:
    value: str | None = None

    @classmethod
    def getenv(cls, name: str) -> str | None:
        assert name == "IGNITION_MCP_DATABASE_QUERY_REGISTRY_JSON"
        return cls.value


def _database_registry_loader(tool: str):
    """Extract registry parsing helpers as pure CPython functions."""
    path = TOOLS / tool / "onToolCalled.py"
    handler = ast.parse(path.read_text(encoding="utf-8")).body[0]
    assert isinstance(handler, ast.FunctionDef)
    names = {"integer", "normalizeParameter", "loadRegistry"}
    helpers = [node for node in handler.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in helpers} == names
    namespace: dict[str, Any] = {
        "System": _System,
        "basestring": str,
        "long": int,
        "unicode": str,
        "json": json,
        "re": re,
    }
    exec(compile(ast.Module(body=helpers, type_ignores=[]), str(path), "exec"), namespace)
    return namespace["loadRegistry"]


def _registry(*, path: str = "mcp_ci/page", limit_parameter: str = "limit") -> str:
    return json.dumps({
        "schemaVersion": 1,
        "entries": [{
            "alias": "ci_page",
            "description": "Bounded CI query",
            "project": "mcp_ci",
            "path": path,
            "resultMode": "dataset",
            "datasourcePolicy": "named-query-fixed",
            "parameters": {},
            "pagination": {
                "mode": "offset",
                "limitParameter": limit_parameter,
                "offsetParameter": "offset",
                "defaultPageSize": 10,
                "hardPageSize": 100,
                "maxOffset": 1000,
            },
        }],
    })


@pytest.mark.parametrize("tool", ["database_query", "database_query_list"])
def test_registry_normalizes_before_rejecting_parent_path(tool: str) -> None:
    _System.value = _registry(path=" ../escape")
    with pytest.raises(ValueError, match="project-relative"):
        _database_registry_loader(tool)()


@pytest.mark.parametrize("tool", ["database_query", "database_query_list"])
def test_registry_rejects_unsafe_native_pagination_parameter(tool: str) -> None:
    _System.value = _registry(limit_parameter="limit-name")
    with pytest.raises(ValueError, match="simple native"):
        _database_registry_loader(tool)()


def test_database_query_separates_registry_and_caller_validation() -> None:
    source = (TOOLS / "database_query/onToolCalled.py").read_text(encoding="utf-8")
    assert 'except ValueError as exc:\n\t\t\treturn toolError("schema_mismatch"' in source
    assert 'except ValueError as exc:\n\t\treturn toolError("invalid_argument"' in source
    assert "candidateOffset > pagination[\"maxOffset\"]" in source
    assert "int(offset) + effectivePageSize - 1 > pagination[\"maxOffset\"]" in source


def test_database_number_markers_do_not_collide_with_domain_objects() -> None:
    path = TOOLS / "database_query/onToolCalled.py"
    handler = ast.parse(path.read_text(encoding="utf-8")).body[0]
    assert isinstance(handler, ast.FunctionDef)
    nodes = [
        node for node in handler.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef))
        and node.name in {"EncodedNumber", "encodeNulls"}
    ]
    namespace: dict[str, Any] = {}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)
    marker = namespace["EncodedNumber"]("decimal", "1.25")
    encode = namespace["encodeNulls"]
    assert encode(marker) == {"type": "decimal", "text": "1.25"}
    collision = encode({"type": "decimal", "text": "domain", "other": None})
    assert collision["$ignition"] == "object"
    schema = json.loads((ROOT / "contracts/schemas/database-query.output.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator({"$ref": "#/$defs/value", "$defs": schema["$defs"]})
    validator.validate(encode(marker))
    validator.validate(collision)


def test_runtime_string_and_raw_array_limits_are_contractual() -> None:
    expected = {
        "historian_browse": {
            ("rootPath", "maxLength"): 2048,
            ("nameFilters", "itemMaxLength"): 256,
            ("continuation", "maxLength"): 8192,
        },
        "historian_query_series": {("paths", "itemMaxLength"): 2048},
        "historian_query_aggregate": {
            ("paths", "itemMaxLength"): 2048,
            ("aggregates", "maxItems"): 18,
        },
        "alarm_status": {
            ("alarmPaths", "itemMaxLength"): 2048,
            ("providers", "itemMaxLength"): 128,
            ("allProperties", "stringValueMaxLength"): 4096,
        },
        "alarm_journal": {
            ("journalName", "maxLength"): 256,
            ("alarmPaths", "itemMaxLength"): 2048,
            ("providers", "itemMaxLength"): 128,
            ("allProperties", "stringValueMaxLength"): 4096,
        },
    }
    for tool, assertions in expected.items():
        contract = json.loads((CONTRACTS / f"{tool}.contract.json").read_text(encoding="utf-8"))
        for (parameter, key), value in assertions.items():
            assert contract["parameters"][parameter][key] == value


def test_unbounded_alarm_reads_are_deferred_from_every_profile() -> None:
    deferred_tools = {"alarm_status", "alarm_journal"}
    profile = json.loads(PROFILE.read_text(encoding="utf-8"))
    assert deferred_tools.isdisjoint(profile["tools"])
    assert not (TOOLS / "alarm_status").exists()
    assert not (TOOLS / "alarm_journal").exists()
    for name in deferred_tools:
        assert (DEFERRED / name / "onToolCalled.py").is_file()
        assert (DEFERRED / name / "resource.json").is_file()


def test_alarm_journal_rejects_unbounded_associated_data_before_native_call() -> None:
    source = (DEFERRED / "alarm_journal/onToolCalled.py").read_text(encoding="utf-8")
    guard = source.index('if includeData:')
    native = source.index('system.alarm.queryJournal')
    assert guard < native
    contract = json.loads((CONTRACTS / "alarm_journal.contract.json").read_text(encoding="utf-8"))
    assert contract["parameters"]["includeData"]["trueBehavior"] == "unsupported_capability"


def test_tag_query_normalizes_native_full_path_before_json_conversion() -> None:
    source = (TOOLS / "tag_query/onToolCalled.py").read_text(encoding="utf-8")
    raw_values = source.index('rawValues = dict((unicode(key), value) for key, value in nativePairs)')
    full_path = source.index('path = rawValues.get("fullPath")')
    path_text = source.index('pathText = unicode(path)')
    json_values = source.index('values = dict((key, jsonValue(value)) for key, value in rawValues.items())')
    canonical = source.index('values["path"] = pathText')
    assert raw_values < full_path < path_text < json_values < canonical
    assert 'del values["fullPath"]' in source


def test_tag_query_uses_documented_pywrapper_continuation_property() -> None:
    source = (TOOLS / "tag_query/onToolCalled.py").read_text(encoding="utf-8")
    assert 'getattr(result, "continuationPoint", None)' in source
    assert 'result.getContinuationPoint()' not in source
    assert 'nextCursor = unicode(nextCursor)' in source


def test_tag_query_upstream_errors_identify_safe_execution_stage() -> None:
    source = (TOOLS / "tag_query/onToolCalled.py").read_text(encoding="utf-8")
    for stage in (
        "native_query_continuation",
        "native_query_initial",
        "result_normalization",
        "continuation_read",
        "serialization",
    ):
        assert f'stage = "{stage}"' in source
    assert '" stage=" + stage + " exceptionType=" + exceptionType' in source
    assert '"The Tag query operation could not be completed during " + stage' in source


def test_tag_query_serializes_only_public_property_allowlist() -> None:
    source = (TOOLS / "tag_query/onToolCalled.py").read_text(encoding="utf-8")
    assert 'values = {"path": pathText}' in source
    assert 'for propertyName in ("name", "tagType", "dataType", "valueSource", "typeId", "quality")' in source
    assert 'values[propertyName] = queryPropertyValue(propertyName, rawValues[propertyName])' in source
    assert 'jsonValue(' not in source
    assert '"fullPath"' not in source[source.index('values = {"path": pathText}'):source.index('stage = "continuation_read"')]
