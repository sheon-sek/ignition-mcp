"""Strict shared-contract and current-phase inventory consistency checks."""

from __future__ import annotations

import hashlib
import json
import math
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
#: per-Tool facts the contract must state: mutation class, scope, deployment gate,
#: whether it is destructive, its Precondition token (D30 §2 — ``none`` for a create),
#: how the Gateway enforces it, and the knobs the caller may never choose (D30 §4).
CURRENT_REST_MUTATION_TOOLS: dict[str, dict[str, Any]] = {
    "config_resource_update": {
        "mutationClass": "CONFIG_MUTATION",
        "scope": "ignition.config",
        "gate": "IGNITION_MCP_CONFIG_MUTATION_ENABLED",
        "destructive": False,
        "precondition": {"kind": "resource_signature", "enforcedBy": "gateway"},
        "fixedKnobs": {"allowInvalidReferences": "false"},
    },
    "config_resource_create": {
        "mutationClass": "CONFIG_MUTATION",
        "scope": "ignition.config",
        "gate": "IGNITION_MCP_CONFIG_MUTATION_ENABLED",
        "destructive": False,
        "precondition": {"kind": "none"},
        "fixedKnobs": {"allowInvalidReferences": "false"},
    },
    "config_resource_delete": {
        "mutationClass": "CONFIG_MUTATION",
        "scope": "ignition.config",
        "gate": "IGNITION_MCP_CONFIG_MUTATION_ENABLED",
        "destructive": True,
        "precondition": {"kind": "resource_signature", "enforcedBy": "gateway"},
        "fixedKnobs": {"confirm": "never sent"},
    },
    "config_resource_rename": {
        "mutationClass": "CONFIG_MUTATION",
        "scope": "ignition.config",
        "gate": "IGNITION_MCP_CONFIG_MUTATION_ENABLED",
        "destructive": False,
        "precondition": {"kind": "resource_signature", "enforcedBy": "server_read_compare"},
        "fixedKnobs": {"references": "ABORT"},
    },
}
REST_MUTATION_CLASSES = frozenset({"CONFIG_MUTATION", "CONTROL_MUTATION", "ADMIN_MUTATION"})
PRECONDITION_KINDS = frozenset({"resource_signature", "none"})
PRECONDITION_ENFORCERS = frozenset({"gateway", "server_read_compare"})
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

CURRENT_RUNTIME_READ_TOOLS = [
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
#: Phase 4 milestone 4a (D30). Milestone 4b adds the CONFIG Tag Mutations.
CURRENT_RUNTIME_CONTROL_TOOLS = ["tag_write", "alarm_shelve", "alarm_unshelve"]
CURRENT_RUNTIME_CONFIG_TOOLS = ["tag_update"]
CURRENT_RUNTIME_MUTATION_TOOLS = CURRENT_RUNTIME_CONTROL_TOOLS + CURRENT_RUNTIME_CONFIG_TOOLS
CURRENT_RUNTIME_TOOLS = CURRENT_RUNTIME_READ_TOOLS + CURRENT_RUNTIME_MUTATION_TOOLS
#: `readonly` never changes (D09); each mutation-capable profile is the READ
#: inventory plus exactly its own class's Mutations, never a wildcard.
EXPECTED_PROFILE_TOOLS = {
    "readonly": CURRENT_RUNTIME_READ_TOOLS,
    "operator": CURRENT_RUNTIME_READ_TOOLS + CURRENT_RUNTIME_CONTROL_TOOLS,
    "configurator": CURRENT_RUNTIME_READ_TOOLS + CURRENT_RUNTIME_CONFIG_TOOLS,
    "full": CURRENT_RUNTIME_READ_TOOLS + CURRENT_RUNTIME_CONTROL_TOOLS + CURRENT_RUNTIME_CONFIG_TOOLS,
}
RESERVED_POLICY_PROVIDER = "IgnitionMCPPolicy"
RUNTIME_TARGET_POLICY_SCHEMA = "contracts/shared/runtime-target-policy.schema.json"
#: D30 §2: the Tag config fingerprint is repo-defined, so its definition — the
#: canonical-JSON rule, the token form and the golden vectors — is a committed
#: shared contract that both the reader (`tag_get_config`) and every Tag CONFIG
#: Mutation cite.
TAG_CONFIG_FINGERPRINT_CONTRACT = "contracts/shared/tag-config-fingerprint.json"
TAG_CONFIG_FINGERPRINT_VERSION = "tcf1"
TAG_CONFIG_FINGERPRINT_HEX_LENGTH = 64
#: The read a Precondition-token compare is taken from, in both planes' words:
#: the default `tag_get_config` read of one exact target path.
TAG_CONFIG_FINGERPRINT_READ = "system.tag.getConfiguration(path, false, false)"
#: Phase 4 milestone 4b: the Runtime CONFIG Mutations implemented so far, with the
#: per-Tool facts the contract must state (D30 §2, §3, §6).
CURRENT_RUNTIME_CONFIG_MUTATIONS: dict[str, dict[str, Any]] = {
    "tag_update": {
        "destructive": False,
        "precondition": {"kind": "tag_config_fingerprint", "enforcedBy": "handler_read_compare"},
        "collisionPolicy": "MergeOverwrite",
    },
}
RUNTIME_PRECONDITION_KINDS = frozenset({"tag_config_fingerprint"})
RUNTIME_PRECONDITION_ENFORCERS = frozenset({"handler_read_compare"})
#: The fields the shipped Jython reader requires of every Runtime Target Policy,
#: whatever Tool reads it. `auditProfile` and `alarmShelveMaxSeconds` are
#: validated when present.
REQUIRED_POLICY_FIELDS = ("schemaVersion", "allowlists", "serviceIdentity", "auditMode")


class ContractError(ValueError):
    pass


def quote_json_string(value: str) -> str:
    """The one string rule of the Tag config fingerprint's canonical JSON.

    Only `"`, `\\` and the control characters are escaped, and a control character
    is always the six-character `\\u00xx` form: two implementations of this rule
    (Python 3 here, Jython 2.7 in the handler) then produce identical bytes for
    every string, and no escaping shortcut can make one of them disagree.
    """

    parts = ['"']
    for character in value:
        if character == '"':
            parts.append('\\"')
        elif character == "\\":
            parts.append("\\\\")
        elif character < " ":
            parts.append("\\u%04x" % ord(character))
        else:
            parts.append(character)
    parts.append('"')
    return "".join(parts)


def canonical_json(value: Any) -> str:
    """Canonical JSON text for the Tag config fingerprint (D30 §2).

    Object keys sort by code point, there is no insignificant whitespace, an
    integer keeps its exact decimal form, a float uses the interpreter's shortest
    round-trip form, and a string follows :func:`quote_json_string`. The handler's
    Jython copy of this function has to agree byte for byte, so the golden vectors
    in `contracts/shared/tag-config-fingerprint.json` are recomputed here on every
    lint and re-run against the shipped handler by the D29 fixture suite.
    """

    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return quote_json_string(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ContractError("canonical JSON has no representation for a non-finite number")
        return repr(value)
    if isinstance(value, list):
        return "[" + ",".join(canonical_json(child) for child in value) + "]"
    if isinstance(value, dict):
        for key in value:
            if not isinstance(key, str):
                raise ContractError("canonical JSON object keys must be strings")
        return "{" + ",".join(
            f"{quote_json_string(key)}:{canonical_json(value[key])}" for key in sorted(value)
        ) + "}"
    raise ContractError(f"canonical JSON has no representation for {type(value).__name__}")


def encode_nulls(value: Any) -> Any:
    """D28 `ignition-null-v1`, as the Runtime handlers and this lint both read it.

    A null becomes the reserved-key object, an object that carries a literal
    `$ignition` key is escaped with sorted entries, and everything else is
    traversed. The handler's Jython copy of this function has to agree with this
    one, which is what the golden vectors' `nativeConfiguration` -> `configuration`
    step checks.
    """

    if value is None:
        return {"$ignition": "null"}
    if isinstance(value, dict):
        if "$ignition" in value:
            return {"$ignition": "object", "entries": [[key, encode_nulls(child)] for key, child in sorted(value.items())]}
        return {key: encode_nulls(child) for key, child in value.items()}
    if isinstance(value, list):
        return [encode_nulls(child) for child in value]
    return value


def tag_config_fingerprint(encoded_configuration: Any) -> str:
    """`tcf1:<sha256>` over the canonical JSON of an encoded Tag configuration."""
    text = canonical_json(encoded_configuration)
    return f"{TAG_CONFIG_FINGERPRINT_VERSION}:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _check_tag_config_fingerprint(document: dict[str, Any]) -> None:
    """D30 §2: the repo-defined Tag config fingerprint and its golden vectors."""

    if document.get("decision") != "D30" or document.get("name") != "tag_config_fingerprint":
        raise ContractError("tag-config-fingerprint: the owning decision is D30")
    version = document.get("version")
    if version != TAG_CONFIG_FINGERPRINT_VERSION or document.get("prefix") != f"{version}:":
        raise ContractError("tag-config-fingerprint: version and prefix drift")
    if document.get("tokenForm") != f"{version}:<{TAG_CONFIG_FINGERPRINT_HEX_LENGTH} lowercase hexadecimal characters>":
        raise ContractError("tag-config-fingerprint: token form drift")
    read_shape = document.get("readShape")
    if not isinstance(read_shape, dict) or read_shape.get("call") != TAG_CONFIG_FINGERPRINT_READ:
        raise ContractError(
            "tag-config-fingerprint: the compared read must be the default tag_get_config read"
        )
    canonical = document.get("canonicalJson")
    if not isinstance(canonical, dict) or set(canonical) != {
        "keys", "whitespace", "strings", "integers", "floats", "null", "booleans",
    }:
        raise ContractError("tag-config-fingerprint: the canonical-JSON rule must be stated in full")
    for key in ("coverage", "raceWindow", "definition"):
        if not isinstance(document.get(key), str) or not document[key].strip():
            raise ContractError(f"tag-config-fingerprint: {key} must be documented")
    if "D30" not in str(document.get("raceWindow")):
        raise ContractError("tag-config-fingerprint: the race window must cite D30 §2")
    if "notASecret" not in document:
        raise ContractError("tag-config-fingerprint: a fingerprint is a Precondition token, never a credential")
    vectors = document.get("goldenVectors")
    if not isinstance(vectors, list) or not vectors:
        raise ContractError("tag-config-fingerprint: D30 §2 requires a golden vector")
    names: list[str] = []
    for vector in vectors:
        if not isinstance(vector, dict):
            raise ContractError("tag-config-fingerprint: every golden vector is an object")
        name = vector.get("name")
        if not isinstance(name, str) or not name:
            raise ContractError("tag-config-fingerprint: every golden vector needs a name")
        names.append(name)
        if not isinstance(vector.get("documentation"), str) or not vector["documentation"].strip():
            raise ContractError(f"tag-config-fingerprint: vector {name} needs documentation")
        configuration = vector.get("configuration")
        if not isinstance(configuration, list) or not configuration:
            raise ContractError(f"tag-config-fingerprint: vector {name} needs a configuration")
        if encode_nulls(vector.get("nativeConfiguration")) != configuration:
            raise ContractError(
                f"tag-config-fingerprint: vector {name} configures a native read that D28 does not encode "
                "into its own configuration"
            )
        expected = tag_config_fingerprint(configuration)
        if vector.get("fingerprint") != expected:
            raise ContractError(
                f"tag-config-fingerprint: vector {name} fingerprint drift: "
                f"{vector.get('fingerprint')!r} != {expected!r}"
            )
        if vector.get("canonicalJson") != canonical_json(configuration):
            raise ContractError(f"tag-config-fingerprint: vector {name} canonical text drift")
    if len(set(names)) != len(names):
        raise ContractError("tag-config-fingerprint: a golden vector is named twice")


def _check_additive_output_fields(
    tool: dict[str, Any], tool_name: str, repo_root: Path, fingerprint_contract: dict[str, Any]
) -> None:
    """D30 §2's additive READ fields must be in the schema and be required."""

    fields = tool.get("additiveOutputFields")
    if fields is None:
        return
    if not isinstance(fields, list) or not fields:
        raise ContractError(f"{tool_name}: additiveOutputFields must be a non-empty list")
    output_schema_path = tool.get("outputSchema")
    if not isinstance(output_schema_path, str):
        raise ContractError(f"{tool_name}: additiveOutputFields needs an outputSchema")
    schema = _load(repo_root / output_schema_path)
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    for field in fields:
        if not isinstance(field, dict):
            raise ContractError(f"{tool_name}: an additive output field must be declared with its facts")
        name = field.get("name")
        if not isinstance(name, str) or not name:
            raise ContractError(f"{tool_name}: an additive output field needs a name")
        if name not in properties or name not in required:
            raise ContractError(f"{tool_name}: the additive field {name!r} must be required by the output schema")
        if field.get("definition") != TAG_CONFIG_FINGERPRINT_CONTRACT:
            raise ContractError(f"{tool_name}: the additive field {name!r} must cite {TAG_CONFIG_FINGERPRINT_CONTRACT}")
        if field.get("definitionVersion") != fingerprint_contract["version"]:
            raise ContractError(f"{tool_name}: the additive field {name!r} definition version drift")
        if field.get("additive") is not True:
            raise ContractError(f"{tool_name}: D30 §2 makes {name!r} an additive change")


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ContractError(f"{path}: invalid JSON-compatible contract: {error}") from error
    if type(value) is not dict:
        raise ContractError(f"{path}: document must be an object")
    return cast(dict[str, Any], value)


def _check_runtime_mutation(tool: dict[str, Any], tool_name: str, repo_root: Path) -> None:
    """D30 rules every Runtime Mutation contract carries, whichever class it is."""

    output_schema = tool.get("outputSchema")
    if not isinstance(output_schema, str) or not (repo_root / output_schema).is_file():
        raise ContractError(f"{tool_name}: outputSchema must reference a committed schema")
    if tool.get("nativeResponseBinding") != "VERIFIED_WITH_LIMITATION":
        raise ContractError(f"{tool_name}: D27 native binding status drift")
    if tool.get("nativeOutputSchema") != "UNAVAILABLE_ON_D27_BASELINE":
        raise ContractError(f"{tool_name}: D27 native outputSchema limitation drift")
    if tool.get("automaticRetryAfterAmbiguousOutcome") is not False:
        raise ContractError(f"{tool_name}: D08 forbids an automatic retry after an ambiguous outcome")
    if tool.get("budgetClass") != "FAST":
        raise ContractError(f"{tool_name}: a Runtime Tag Mutation without an artifact is FAST")
    if tool.get("preflight") != "input, reserved provider and Target allowlist for every item before any item executes, with no rollback":
        raise ContractError(f"{tool_name}: D30 §3 requires an all-items Preflight with no rollback")
    policy = tool.get("runtimeTargetPolicy")
    if not isinstance(policy, dict):
        raise ContractError(f"{tool_name}: a Runtime Mutation must declare its Runtime Target Policy rules")
    if policy.get("required") is not True or policy.get("missingOrMalformed") != "operation_disabled":
        raise ContractError(f"{tool_name}: D30 §1 requires a fail-closed Runtime Target Policy")
    if policy.get("allowlistKey") != tool_name:
        raise ContractError(f"{tool_name}: the policy allowlist key must be the Tool name")
    if policy.get("documentSchema") != RUNTIME_TARGET_POLICY_SCHEMA:
        raise ContractError(f"{tool_name}: the Runtime Target Policy schema must be {RUNTIME_TARGET_POLICY_SCHEMA}")
    if policy.get("reservedProvider") != RESERVED_POLICY_PROVIDER:
        raise ContractError(f"{tool_name}: the reserved policy provider must be {RESERVED_POLICY_PROVIDER}")
    refusal = policy.get("reservedProviderRefusal", "")
    if not isinstance(refusal, str) or "including an explicit *" not in refusal:
        raise ContractError(f"{tool_name}: the reserved-provider refusal must cover an explicit *")
    if policy.get("reservedProviderRefusalCode") != "permission_denied":
        raise ContractError(f"{tool_name}: a reserved-provider refusal is permission_denied (D30 §7)")


def _check_input_bounds(tool: dict[str, Any], tool_name: str) -> None:
    """D10's numeric budgets are declared by the contract the handler implements."""

    bounds = tool.get("inputBounds")
    if not isinstance(bounds, dict):
        raise ContractError(f"{tool_name}: D10 requires declared inputBounds")
    if bounds.get("defaultItems") != 20 or bounds.get("hardItems") != 100:
        raise ContractError(
            f"{tool_name}: D10 fixes the 20-item project default and the 100-item hard ceiling"
        )
    if bounds.get("overBudgetCode") != "limit_exceeded":
        raise ContractError(f"{tool_name}: an over-budget request is limit_exceeded (D10)")
    if bounds.get("itemsParameter") not in tool.get("parameters", {}):
        raise ContractError(f"{tool_name}: inputBounds.itemsParameter must name a declared parameter")
    if not isinstance(bounds.get("hardItemsPolicyField"), str) or not bounds["hardItemsPolicyField"]:
        raise ContractError(f"{tool_name}: D10's deployment override must name its Policy field")
    for key in ("maxInputBytes", "outputMaxBytes"):
        value = bounds.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ContractError(f"{tool_name}: inputBounds.{key} must be a positive byte ceiling")


def _check_runtime_config_mutation(
    tool: dict[str, Any], tool_name: str, fingerprint_contract: dict[str, Any]
) -> None:
    """The D30 §2, §3 and §6 facts a Runtime CONFIG Mutation must declare."""

    spec = CURRENT_RUNTIME_CONFIG_MUTATIONS[tool_name]
    precondition = tool.get("preconditionToken")
    if not isinstance(precondition, dict) or precondition.get("kind") not in RUNTIME_PRECONDITION_KINDS:
        raise ContractError(f"{tool_name}: D30 §2 requires the Precondition token to be declared")
    if precondition.get("kind") != spec["precondition"]["kind"]:
        raise ContractError(f"{tool_name}: Precondition token kind drift")
    if precondition.get("enforcedBy") != spec["precondition"]["enforcedBy"]:
        raise ContractError(f"{tool_name}: D30 §2 says a Tag config fingerprint is read-compared by the handler")
    if precondition.get("enforcedBy") not in RUNTIME_PRECONDITION_ENFORCERS:
        raise ContractError(f"{tool_name}: a Precondition token must say what enforces it")
    if precondition.get("parameter") != "items[].expectedFingerprint":
        raise ContractError(f"{tool_name}: D30 §2 takes the fingerprint per target")
    if precondition.get("source") != "tag_get_config.fingerprint":
        raise ContractError(f"{tool_name}: the fingerprint comes from the caller's own tag_get_config read")
    if precondition.get("definition") != TAG_CONFIG_FINGERPRINT_CONTRACT:
        raise ContractError(f"{tool_name}: the fingerprint definition must be {TAG_CONFIG_FINGERPRINT_CONTRACT}")
    if precondition.get("definitionVersion") != fingerprint_contract["version"]:
        raise ContractError(f"{tool_name}: the fingerprint definition version drift")
    if precondition.get("mismatchCode") != "conflict":
        raise ContractError(f"{tool_name}: a stale fingerprint fails with conflict before anything dispatches")
    if precondition.get("raceWindowDocumented") is not True:
        raise ContractError(f"{tool_name}: D30 §2 requires the Precondition race window to be documented")
    if tool.get("collisionPolicy") != spec["collisionPolicy"]:
        raise ContractError(f"{tool_name}: the handler fixes the Gateway collision policy (D30 §4)")
    if tool.get("neverCreatesTarget") != "not_found":
        raise ContractError(f"{tool_name}: a missing target fails with not_found instead of being created")
    udt = tool.get("udtDefinitionTargets", "")
    if not isinstance(udt, str) or "explicit" not in udt or "_types_" not in udt or "does not" not in udt:
        raise ContractError(
            f"{tool_name}: D30 §6 allows a UDT definition target only under an explicit _types_ allowlist entry"
        )
    refused_keys = tool.get("refusedConfigKeys")
    if not isinstance(refused_keys, dict):
        raise ContractError(f"{tool_name}: the Tool must declare the configuration keys it refuses")
    for key, rule in refused_keys.items():
        if not isinstance(key, str) or not isinstance(rule, str) or not rule:
            raise ContractError(f"{tool_name}: a refused configuration key must state its rule")
    for required in ("value", "tags", "name"):
        if required not in refused_keys:
            raise ContractError(
                f"{tool_name}: {required!r} must be declared: a value write is CONTROL's, a child is its "
                "own target with its own fingerprint, and a name change is tag_rename's"
            )
    if not isinstance(tool.get("targetPath"), dict):
        raise ContractError(f"{tool_name}: the Tool must declare its target path rules")


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
        if tools != EXPECTED_PROFILE_TOOLS[name]:
            raise ContractError(f"{name}: profile Tool inventory drift")
        if not set(tools) <= set(CURRENT_RUNTIME_TOOLS):
            raise ContractError(f"{name}: profile references an unbundled Runtime Tool")

    compatibility = _load(root_path / "shared/compatibility-status.json")
    if compatibility.get("supportedRequiresMachineEvidence") is not True:
        raise ContractError("SUPPORTED compatibility must come from machine evidence")
    if compatibility.get("supportedRequiresVerifiedNativeResponseBinding") is not True:
        raise ContractError("SUPPORTED compatibility requires verified native response binding")

    fingerprint_contract = _load(root_path / "shared/tag-config-fingerprint.json")
    _check_tag_config_fingerprint(fingerprint_contract)

    for tool_name in CURRENT_RUNTIME_READ_TOOLS:
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
        _check_additive_output_fields(tool, tool_name, repo_root, fingerprint_contract)

    for tool_name in CURRENT_RUNTIME_CONTROL_TOOLS:
        tool = _load(root_path / f"tools/runtime/{tool_name}.contract.json")
        if tool.get("name") != tool_name:
            raise ContractError(f"{tool_name}: contract name drift")
        if tool.get("permissionClass") != "CONTROL" or tool.get("mutationClass") != "CONTROL_MUTATION":
            raise ContractError(f"{tool_name}: Runtime CONTROL mutation contract drift")
        if tool.get("destructive") is not False:
            raise ContractError(f"{tool_name}: a CONTROL Tag write is not destructive")
        _check_runtime_mutation(tool, tool_name, repo_root)

    for tool_name in CURRENT_RUNTIME_CONFIG_TOOLS:
        tool = _load(root_path / f"tools/runtime/{tool_name}.contract.json")
        if tool.get("permissionClass") != "CONFIG" or tool.get("mutationClass") != "CONFIG_MUTATION":
            raise ContractError(f"{tool_name}: Runtime CONFIG mutation contract drift")
        if not isinstance(tool.get("destructive"), bool):
            raise ContractError(f"{tool_name}: a CONFIG mutation must declare destructive explicitly")
        if tool.get("destructive") is not CURRENT_RUNTIME_CONFIG_MUTATIONS[tool_name]["destructive"]:
            raise ContractError(f"{tool_name}: destructive declaration drift (D08/D26)")
        _check_runtime_mutation(tool, tool_name, repo_root)
        _check_runtime_config_mutation(tool, tool_name, fingerprint_contract)
        _check_input_bounds(tool, tool_name)

    declared_mutations = sorted(
        path.name[: -len(".contract.json")]
        for path in (root_path / "tools/runtime").glob("*.contract.json")
        if _load(path).get("mutationClass") != "NONE"
    )
    if declared_mutations != sorted(CURRENT_RUNTIME_MUTATION_TOOLS):
        raise ContractError("Runtime Mutation contract inventory drift")

    if CURRENT_RUNTIME_MUTATION_TOOLS:
        policy_schema = _load(root_path / "shared/runtime-target-policy.schema.json")
        if tuple(policy_schema.get("required", ())) != REQUIRED_POLICY_FIELDS:
            raise ContractError("Runtime Target Policy document schema drift")
        if policy_schema.get("properties", {}).get("auditMode", {}).get("enum") != [
            "best_effort", "required", "off",
        ]:
            raise ContractError("Runtime Target Policy audit-mode vocabulary drift")
        # D10's deployment override lives in the Policy document, so the field a
        # contract names must be part of that document's schema. A contract on this
        # lane that does not declare D10 bounds yet is skipped: the #7 fix declares
        # them for the CONTROL Tools, and the merged tree checks every Mutation.
        policy_properties = policy_schema.get("properties", {})
        for mutation_name in CURRENT_RUNTIME_MUTATION_TOOLS:
            mutation = _load(root_path / f"tools/runtime/{mutation_name}.contract.json")
            bounds = mutation.get("inputBounds")
            if not isinstance(bounds, dict):
                continue
            field = bounds["hardItemsPolicyField"]
            if field not in policy_properties:
                raise ContractError(
                    f"{mutation_name}: {field} must be a Runtime Target Policy document field"
                )

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
    for tool_name, spec in CURRENT_REST_MUTATION_TOOLS.items():
        tool = _load(root_path / f"tools/rest/{tool_name}.contract.json")
        if tool.get("name") != tool_name or tool.get("server") != "ignition-rest":
            raise ContractError(f"{tool_name}: REST mutation contract drift")
        mutation_class = spec["mutationClass"]
        if tool.get("operationKind") != "mutation" or tool.get("mutationClass") != mutation_class:
            raise ContractError(f"{tool_name}: mutation class drift")
        if mutation_class not in REST_MUTATION_CLASSES or mutation_class not in declared_mutation_classes:
            raise ContractError(f"{tool_name}: undeclared mutation class")
        if tool.get("permissionClass") != "CONFIG" or tool.get("requiredScope") != spec["scope"]:
            raise ContractError(f"{tool_name}: mutation scope drift")
        if tool.get("deploymentGate") != spec["gate"] or tool.get("audited") is not True:
            raise ContractError(f"{tool_name}: mutation gate/audit drift")
        if tool.get("destructive") is not spec["destructive"]:
            raise ContractError(f"{tool_name}: destructive declaration drift (D08/D26)")
        if tool.get("capabilityId") != tool_name:
            raise ContractError(f"{tool_name}: a mutation contract must be capability-backed")
        precondition = tool.get("preconditionToken")
        if not isinstance(precondition, dict) or precondition.get("kind") not in PRECONDITION_KINDS:
            raise ContractError(f"{tool_name}: D30 §2 requires the Precondition token to be declared")
        if precondition.get("kind") != spec["precondition"]["kind"]:
            raise ContractError(f"{tool_name}: Precondition token kind drift")
        if precondition.get("kind") == "resource_signature":
            enforcer = precondition.get("enforcedBy")
            if enforcer not in PRECONDITION_ENFORCERS:
                raise ContractError(f"{tool_name}: a Precondition token must say what enforces it")
            if enforcer != spec["precondition"]["enforcedBy"]:
                raise ContractError(f"{tool_name}: Precondition token enforcer drift")
            if enforcer == "gateway" and precondition.get("alsoReadComparedBeforeDispatch") is not True:
                raise ContractError(f"{tool_name}: a Gateway-enforced token must also be read-compared")
            if precondition.get("raceWindowDocumented") is not True:
                raise ContractError(f"{tool_name}: the Precondition race window must be documented")
        if tool.get("refusedResourceTypes", {}).get("unclassified") != "refused":
            raise ContractError(f"{tool_name}: unclassified resource types must be refused")
        if tool.get("fixedKnobs") != spec["fixedKnobs"]:
            raise ContractError(f"{tool_name}: the D30 §4 fixed knobs must be declared exactly")
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
        # D30 §7 maps both a stale Precondition token and a collision to `conflict`,
        # so every mutation's rejection policy must say so.
        if not re.search(r"conflict", str(rejection.get("rule", ""))):
            raise ContractError(f"{tool_name}: the rejection policy must state the D30 §7 conflict mapping")
        if rejection.get("recoveredSuccess") != "unreachable for this Tool":
            raise ContractError(f"{tool_name}: rejection_is_final forbids a recovered success")
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
