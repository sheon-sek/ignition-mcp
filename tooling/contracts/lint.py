"""Strict shared-contract and current-phase inventory consistency checks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

EXPECTED_ERROR_CODES = (
    "invalid_argument", "unsupported_capability", "permission_denied", "operation_disabled",
    "not_found", "conflict", "limit_exceeded", "rate_limited", "timeout", "outcome_unknown",
    "gateway_unavailable", "upstream_error", "schema_mismatch", "internal_error",
)
EXPECTED_PERMISSIONS = frozenset({"READ", "CONFIG", "CONTROL", "ADMIN"})
EXPECTED_PROFILES = {
    "readonly": frozenset({"READ"}),
    "operator": frozenset({"READ", "CONTROL"}),
    "configurator": frozenset({"READ", "CONFIG"}),
    "full": frozenset({"READ", "CONFIG", "CONTROL"}),
}
CURRENT_REST_READ_TOOLS = [
    "gateway_info",
    "gateway_diagnose",
    "project_list",
    "config_resource_search",
    "config_resource_describe",
    "config_resource_names",
    "config_resource_list",
    "config_resource_get",
    "audit_query",
    "alarm_pipeline_list",
    "alarm_pipeline_status",
]

CURRENT_RUNTIME_TOOLS = [
    "bundle_info",
    "tag_browse",
    "tag_query",
    "tag_read",
    "tag_get_config",
    "udt_type_list",
    "udt_type_get",
    "alarm_shelved_list",
    "historian_browse",
    "historian_query_series",
    "historian_query_aggregate",
    "database_query_list",
    "database_query"
]


class ContractError(ValueError):
    pass


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ContractError(f"{path}: invalid JSON-compatible contract: {error}") from error
    if type(value) is not dict:
        raise ContractError(f"{path}: document must be an object")
    return cast(dict[str, Any], value)


def lint_contracts(root: str | Path) -> None:
    root_path = Path(root)
    repo_root = root_path.parent
    errors = _load(root_path / "shared/error-codes.json")
    if tuple(errors.get("codes", ())) != EXPECTED_ERROR_CODES:
        raise ContractError("stable D06 error taxonomy drift")

    permissions = _load(root_path / "shared/permission-classes.json")
    if frozenset(permissions.get("classes", {})) != EXPECTED_PERMISSIONS:
        raise ContractError("permission class drift")
    if permissions.get("implicitHierarchy") is not False:
        raise ContractError("permissions must not imply a hierarchy")

    mutations = _load(root_path / "shared/mutation-classes.json")
    for name, config in mutations.get("classes", {}).items():
        if name != "NONE" and config.get("enabledByDefault") is not False:
            raise ContractError(f"{name}: mutation classes must be disabled by default")
    if mutations.get("automaticRetryAfterAmbiguousOutcome") is not False:
        raise ContractError("ambiguous mutation outcomes must never auto-retry")

    budgets = _load(root_path / "shared/budget-classes.json")
    output = budgets.get("structuredOutputBytes", {})
    if output.get("default", 0) > output.get("hard", -1) or output.get("hard", 0) > 1048576:
        raise ContractError("structured output budget exceeds D10 hard ceiling")
    for name, limits in budgets.get("timeoutsSeconds", {}).items():
        if limits.get("default", 0) > limits.get("hard", -1):
            raise ContractError(f"{name}: default timeout exceeds hard ceiling")
    if budgets.get("silentTruncation") is not False or budgets.get("inlineBinaryBase64") is not False:
        raise ContractError("D10 forbids silent truncation and inline binary Base64")

    for name, expected in EXPECTED_PROFILES.items():
        profile = _load(root_path / f"profiles/{name}.yaml")
        if profile.get("name") != name or frozenset(profile.get("permissions", ())) != expected:
            raise ContractError(f"{name}: permission profile drift")
        tools = profile.get("tools")
        if not isinstance(tools, list) or len(tools) != len(set(tools)):
            raise ContractError(f"{name}: tools must be an explicit duplicate-free list")
        if tools != CURRENT_RUNTIME_TOOLS:
            raise ContractError(f"{name}: current Runtime READ inventory drift")

    compatibility = _load(root_path / "shared/compatibility-status.json")
    if compatibility.get("supportedRequiresMachineEvidence") is not True:
        raise ContractError("SUPPORTED compatibility must come from machine evidence")
    if compatibility.get("supportedRequiresVerifiedNativeResponseBinding") is not True:
        raise ContractError("SUPPORTED compatibility requires verified native response binding")

    for tool_name in CURRENT_RUNTIME_TOOLS:
        tool = _load(root_path / f"tools/runtime/{tool_name}.contract.json")
        if tool.get("name") != tool_name:
            raise ContractError(f"{tool_name}: contract name drift")
        if tool.get("permissionClass") != "READ" or tool.get("mutationClass") != "NONE":
            raise ContractError(f"{tool_name}: Runtime read contract drift")
        if tool.get("nativeResponseBinding") != "VERIFIED_WITH_LIMITATION":
            raise ContractError(f"{tool_name}: D27 native binding status drift")
        if tool.get("nativeOutputSchema") != "UNAVAILABLE_ON_D27_BASELINE":
            raise ContractError(f"{tool_name}: D27 native outputSchema limitation drift")
        output_schema = tool.get("outputSchema")
        if not isinstance(output_schema, str) or not (repo_root / output_schema).is_file():
            raise ContractError(f"{tool_name}: outputSchema must reference a committed schema")

    for tool_name in CURRENT_REST_READ_TOOLS:
        tool = _load(root_path / f"tools/rest/{tool_name}.contract.json")
        if tool.get("name") != tool_name or tool.get("permissionClass") != "READ":
            raise ContractError(f"{tool_name}: external READ contract drift")
        if tool.get("mutationClass") != "NONE":
            raise ContractError(f"{tool_name}: external READ Tool must be read-only")
        output_schema = tool.get("outputSchema")
        if not isinstance(output_schema, str) or not (repo_root / output_schema).is_file():
            raise ContractError(f"{tool_name}: outputSchema must reference a committed schema")


def main() -> int:
    lint_contracts(Path(__file__).resolve().parents[2] / "contracts")
    print("contracts: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
