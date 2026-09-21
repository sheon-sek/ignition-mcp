"""Strict shared-contract and current-phase inventory consistency checks."""

from __future__ import annotations

import json
from pathlib import Path
import re
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
#: Phase 4 milestone 4c: the REST Mutation Tools implemented so far, with the
#: deployment gate and mutation class each one needs.
CURRENT_REST_MUTATION_TOOLS = {
    "config_resource_update": ("CONFIG_MUTATION", "ignition.config", "IGNITION_MCP_CONFIG_MUTATION_ENABLED"),
}
REST_MUTATION_CLASSES = frozenset({"CONFIG_MUTATION", "CONTROL_MUTATION", "ADMIN_MUTATION"})
REFUSED_RESOURCE_TYPES_CONTRACT = "contracts/shared/refused-resource-types.json"
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
    expected_rest_inventory = sorted([*CURRENT_REST_READ_TOOLS, *CURRENT_REST_MUTATION_TOOLS])
    if rest_inventory != expected_rest_inventory:
        raise ContractError("REST Tool contract inventory drift")

    declared_mutation_classes = _load(root_path / "shared/mutation-classes.json").get("classes", {})
    if not isinstance(declared_mutation_classes, dict):
        raise ContractError("mutation-classes: classes must be an object")
    for tool_name, (mutation_class, scope, gate) in CURRENT_REST_MUTATION_TOOLS.items():
        tool = _load(root_path / f"tools/rest/{tool_name}.contract.json")
        if tool.get("name") != tool_name or tool.get("server") != "ignition-rest":
            raise ContractError(f"{tool_name}: REST mutation contract drift")
        if tool.get("operationKind") != "mutation" or tool.get("mutationClass") != mutation_class:
            raise ContractError(f"{tool_name}: mutation class drift")
        if mutation_class not in REST_MUTATION_CLASSES or mutation_class not in declared_mutation_classes:
            raise ContractError(f"{tool_name}: undeclared mutation class")
        if tool.get("permissionClass") != "CONFIG" or tool.get("requiredScope") != scope:
            raise ContractError(f"{tool_name}: mutation scope drift")
        if tool.get("deploymentGate") != gate or tool.get("audited") is not True:
            raise ContractError(f"{tool_name}: mutation gate/audit drift")
        if not isinstance(tool.get("destructive"), bool):
            raise ContractError(f"{tool_name}: destructive must be declared as a boolean")
        if tool.get("capabilityId") != tool_name:
            raise ContractError(f"{tool_name}: a mutation contract must be capability-backed")
        precondition = tool.get("preconditionToken")
        if not isinstance(precondition, dict) or precondition.get("kind") != "resource_signature":
            raise ContractError(f"{tool_name}: mutation must declare its Precondition token")
        if precondition.get("enforcedBy") != "gateway":
            raise ContractError(f"{tool_name}: a Gateway-enforced token must say so")
        if tool.get("refusedResourceTypes", {}).get("unclassified") != "refused":
            raise ContractError(f"{tool_name}: unclassified resource types must be refused")
        if tool.get("fixedKnobs", {}).get("allowInvalidReferences") != "false":
            raise ContractError(f"{tool_name}: allowInvalidReferences is fixed false (D30)")
        target = tool.get("targetId")
        if not isinstance(target, dict) or target.get("denialCode") != "permission_denied":
            raise ContractError(f"{tool_name}: D30 §7 decides permission_denied for a Target denial")
        if target.get("wildcard") != "*" or target.get("denyByDefault") is not True:
            raise ContractError(f"{tool_name}: the Target allowlist stays deny-by-default with an explicit *")
        schema_validation = tool.get("requestSchemaValidation")
        if not isinstance(schema_validation, dict) or (
            schema_validation.get("source") != "gateway-openapi-capability-snapshot"
        ):
            raise ContractError(f"{tool_name}: D03 request-schema validation must be declared")
        if schema_validation.get("unavailableDisposition") is None:
            raise ContractError(f"{tool_name}: an unusable request schema must fail closed")
        rejection = tool.get("rejectionPolicy")
        if not isinstance(rejection, dict) or "D30 §2" not in str(rejection.get("rule", "")):
            raise ContractError(f"{tool_name}: a mutation must declare the D30 §2 rejection policy")
        if not re.search(r"conflict", str(rejection.get("rule", ""))):
            raise ContractError(f"{tool_name}: the rejection policy must state the signature-mismatch mapping")
        output_schema = tool.get("outputSchema")
        if not isinstance(output_schema, str) or not (repo_root / output_schema).is_file():
            raise ContractError(f"{tool_name}: outputSchema must reference a committed schema")

    refused_types = _load(root_path / "shared/refused-resource-types.json")
    if refused_types.get("decision") != "D30":
        raise ContractError("refused-resource-types: the owning decision is D30")
    allowed = refused_types.get("allowed")
    refused = refused_types.get("refused")
    if not isinstance(allowed, list) or not isinstance(refused, list):
        raise ContractError("refused-resource-types: allowed and refused must be lists")
    refused_names: list[str] = []
    for entry in refused:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("resourceType"), str)
            or not isinstance(entry.get("category"), str)
        ):
            raise ContractError("refused-resource-types: every refusal needs a type and a D30 category")
        refused_names.append(entry["resourceType"])
    if len(set(allowed)) != len(allowed) or len(set(refused_names)) != len(refused_names):
        raise ContractError("refused-resource-types: a resource type is classified twice")
    if set(allowed) & set(refused_names):
        raise ContractError("refused-resource-types: a resource type is both allowed and refused")
    if "com.inductiveautomation.mcp/server-config" not in refused_names:
        raise ContractError("refused-resource-types: the MCP server-config resource must be refused")

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
