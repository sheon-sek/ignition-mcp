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
    "artifact_list",
    "artifact_info",
    "project_export",
    "tag_config_export",
    "operation_diagnose",
]
CURRENT_REST_STORAGE_TOOLS = frozenset({"artifact_list", "artifact_info", "operation_diagnose"})
CURRENT_REST_SENSITIVE_EXPORT_TOOLS = frozenset({"project_export", "tag_config_export"})
SENSITIVE_EXPORT_GATE = "IGNITION_MCP_SENSITIVE_EXPORTS_ENABLED"
EXPECTED_ARTIFACT_KINDS = ("project_archive", "project_export", "tag_config_export")
EXPECTED_SENSITIVITY_CLASSES = ("INTERNAL", "CONFIDENTIAL", "RESTRICTED")
EXPECTED_RETENTION_CLASSES = ("EPHEMERAL", "EXPORT", "RECOVERY")
EXPECTED_PROJECT_TRANSACTION_STATES = (
    "PREPARING", "BASELINE_CAPTURED", "CANDIDATE_VALIDATED", "BACKUP_PERSISTED",
    "CONCURRENCY_VERIFIED", "IMPORT_SENT", "VERIFYING", "COMMITTED",
)
EXPECTED_PROJECT_TRANSACTION_TERMINAL_STATES = frozenset({
    "COMMITTED", "NO_CHANGE", "CONFLICTED", "FAILED_PRE_IMPORT",
    "NOT_APPLIED", "OUTCOME_UNKNOWN", "RECOVERY_REQUIRED",
})
EXPECTED_RECOVERY_LOCK_RELEASED_ON = frozenset({"COMMITTED", "NOT_APPLIED", "CONFLICTED", "FAILED_PRE_IMPORT"})
EXPECTED_RECOVERY_LOCK_HELD_ON = frozenset({"OUTCOME_UNKNOWN", "RECOVERY_REQUIRED"})

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

    rest_inventory = sorted(
        path.name[: -len(".contract.json")]
        for path in (root_path / "tools/rest").glob("*.contract.json")
    )
    if rest_inventory != sorted(CURRENT_REST_READ_TOOLS):
        raise ContractError("REST Tool contract inventory drift (Phase 3 freeze; zero mutation Tools)")

    for tool_name in CURRENT_REST_STORAGE_TOOLS:
        tool = _load(root_path / f"tools/rest/{tool_name}.contract.json")
        if tool.get("storageBacked") is not True or tool.get("principalScoped") is not True:
            raise ContractError(f"{tool_name}: storage-backed principal-scoped contract drift")
        if tool.get("budgetClass") != "FAST" or tool.get("requiredScope") != "ignition.read":
            raise ContractError(f"{tool_name}: storage Tool budget/scope drift")

    for tool_name in CURRENT_REST_SENSITIVE_EXPORT_TOOLS:
        tool = _load(root_path / f"tools/rest/{tool_name}.contract.json")
        if tool.get("sensitivity") != "CONFIDENTIAL" or tool.get("retentionClass") != "EXPORT":
            raise ContractError(f"{tool_name}: sensitive export class drift")
        if tool.get("deploymentGate") != SENSITIVE_EXPORT_GATE or tool.get("audited") is not True:
            raise ContractError(f"{tool_name}: sensitive export gate/audit drift")
        if tool.get("budgetClass") != "ARTIFACT" or tool.get("requiredScope") != "ignition.read":
            raise ContractError(f"{tool_name}: sensitive export budget/scope drift")
        capability = tool.get("capabilityId")
        requirements = tool.get("nativeRequirements")
        if not isinstance(capability, str) or not capability:
            raise ContractError(f"{tool_name}: sensitive export must be capability-backed")
        if not isinstance(requirements, list) or not requirements:
            raise ContractError(f"{tool_name}: sensitive export must declare native requirements")

    artifact_classes = _load(root_path / "shared/artifact-classes.json")
    if tuple(artifact_classes.get("artifactKinds", ())) != EXPECTED_ARTIFACT_KINDS:
        raise ContractError("Artifact kind inventory drift")
    if tuple(artifact_classes.get("sensitivityClasses", ())) != EXPECTED_SENSITIVITY_CLASSES:
        raise ContractError("Artifact sensitivity class drift")
    if frozenset(artifact_classes.get("retentionClasses", {})) != frozenset(EXPECTED_RETENTION_CLASSES):
        raise ContractError("Artifact retention class drift")
    if artifact_classes.get("publicDeleteToolPhase3") is not False:
        raise ContractError("D26: artifact_delete remains a Phase 4 mutation")

    artifact_ref = _load(root_path / "shared/artifact-ref.schema.json")
    ref_properties = cast(dict[str, Any], artifact_ref.get("properties", {}))
    for field, expected_enum in (
        ("kind", EXPECTED_ARTIFACT_KINDS),
        ("sensitivity", EXPECTED_SENSITIVITY_CLASSES),
        ("retentionClass", EXPECTED_RETENTION_CLASSES),
    ):
        enum_value = cast(dict[str, Any], ref_properties.get(field, {})).get("enum", ())
        if tuple(enum_value) != expected_enum:
            raise ContractError(f"artifact-ref {field} enum drift")
    if "token" in json.dumps(artifact_ref).lower():
        raise ContractError("artifact-ref download is a relative data-plane path; never a token")
    if "principal" in json.dumps(artifact_ref):
        raise ContractError("the owning principal is internal metadata, not part of ArtifactRef")

    transactions = _load(root_path / "shared/project-transaction-states.json")
    if tuple(transactions.get("states", ())) != EXPECTED_PROJECT_TRANSACTION_STATES:
        raise ContractError("D16 Project transaction state order drift")
    if frozenset(transactions.get("terminalStates", ())) != EXPECTED_PROJECT_TRANSACTION_TERMINAL_STATES:
        raise ContractError("D16 transaction terminal state drift")
    if frozenset(transactions.get("recoveryLockReleasedOn", ())) != EXPECTED_RECOVERY_LOCK_RELEASED_ON:
        raise ContractError("D16 recovery-lock release set drift")
    if frozenset(transactions.get("recoveryLockHeldOn", ())) != EXPECTED_RECOVERY_LOCK_HELD_ON:
        raise ContractError("D16 recovery-lock hold set drift")
    if tuple(transactions.get("possiblyDispatchedStates", ())) != ("IMPORT_SENT", "VERIFYING"):
        raise ContractError("D16 possibly-dispatched state set drift")
    for key in ("automaticRollback", "automaticReplay", "automaticMerge"):
        if transactions.get(key) is not False:
            raise ContractError(f"D16 forbids automatic {key}")


def main() -> int:
    lint_contracts(Path(__file__).resolve().parents[2] / "contracts")
    print("contracts: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
