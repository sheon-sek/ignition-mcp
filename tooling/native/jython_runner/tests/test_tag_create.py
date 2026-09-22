"""D29 recorded-Jython coverage for `tag_create` (Phase 4 ticket #11).

`tag_create` takes no Precondition token (D30 §2): the target must not exist, so
the collision rule is the concurrency rule and the fixture that proves it records
`system.tag.exists` answering true with no `system.tag.configure` after it. Every
fixture replays the shipped handler's exact ordered native calls, so a test can
assert the negative half of a behavior — nothing dispatched, no configure at all,
no audit call under `off` — as well as the returned domain.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from tooling.native.jython_runner import run_recorded_tool, run_recorded_tool_error

ROOT = Path(__file__).resolve().parents[4]
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
HANDLER = (
    ROOT
    / "packages/ignition-runtime-bundle/project/com.inductiveautomation.mcp/tools/tag_create"
    / "onToolCalled.py"
)
CONTRACT = ROOT / "contracts/tools/runtime/tag_create.contract.json"

TARGET = "[default]IgnitionMCP_CI/CreatedTarget"
TARGET2 = "[default]IgnitionMCP_CI/CreatedTarget2"
SIBLING = "[default]IgnitionMCP_CI2/CreatedTarget"
UDT = "[default]_types_/IgnitionMCP_CI/ProbeType"
CONFIG = {"tagType": "AtomicTag", "dataType": "Int4"}


def _fixture(name: str) -> Path:
    return FIXTURES / f"tag_create-{name}.json"


def _error(name: str, code: str) -> dict:
    return run_recorded_tool_error("tag_create", _fixture(name), expected_code=code)


def _structured(name: str) -> dict:
    return run_recorded_tool("tag_create", _fixture(name))["structuredContent"]


def _problem_reasons(error: dict) -> list[tuple[str, str]]:
    return [(item.get("path"), item.get("reason")) for item in error["details"]["items"]]


def _statuses(structured: dict) -> list[tuple[str, str]]:
    return [(item["path"], item["status"]) for item in structured["items"]]


def _recorded_targets(name: str) -> list[str]:
    document = json.loads(_fixture(name).read_text(encoding="utf-8"))
    return [entry["target"] for entry in document["calls"]]


def test_an_absent_target_is_created_audited_and_re_read() -> None:
    structured = _structured("allowlisted")

    assert _statuses(structured) == [(TARGET, "executed")]
    assert structured["items"][0]["nativeOutcome"]["good"] is True
    assert structured["summary"] == {
        "requested": 1,
        "succeeded": 1,
        "failed": 0,
        "outcomeUnknown": 0,
        "notExecuted": 0,
        "auditMode": "best_effort",
        "auditRecorded": True,
    }
    observed = structured["observed"][0]
    assert observed["status"] == "ok"
    assert observed["configuration"][0]["name"] == "CreatedTarget"
    # The Observed fingerprint is the token the caller's next tag_update or
    # tag_delete uses, so it has to be a real tcf1 token.
    assert observed["fingerprint"].startswith("tcf1:")
    assert len(observed["fingerprint"]) == 69


def test_the_structured_result_satisfies_the_committed_output_schema() -> None:
    structured = _structured("allowlisted")
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    schema = json.loads((ROOT / contract["outputSchema"]).read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(structured)

    assert contract["permissionClass"] == "CONFIG"
    assert contract["mutationClass"] == "CONFIG_MUTATION"
    assert contract["destructive"] is False
    assert contract["parameters"]["items"]["maxItems"] == 100
    assert contract["parameters"]["items"]["itemRequired"] == ["path", "config"]
    assert contract["preconditionToken"]["kind"] == "none"
    assert contract["collisionPolicy"] == "Abort"
    assert contract["inputBounds"]["hardItemsPolicyField"] == "tagCreateMaxItems"


def test_the_dispatch_never_precedes_the_all_targets_existence_check() -> None:
    """D30 §3: the whole batch passes Preflight before the first item executes."""
    targets = _recorded_targets("batch-with-a-bad-native-outcome")
    first_configure = targets.index("system.tag.configure")

    assert targets.count("system.tag.exists") == 2
    assert all(
        index < first_configure
        for index, target in enumerate(targets)
        if target == "system.tag.exists"
    )
    # The attempt row is written after every Preflight check and before the dispatch.
    assert targets[first_configure - 1] == "system.util.audit"
    assert [item["nativeOutcome"]["good"] for item in _structured("batch-with-a-bad-native-outcome")["items"]] == [True, False]


def test_a_folder_is_created_like_any_other_node() -> None:
    structured = _structured("folder-target")

    assert _statuses(structured) == [("[default]IgnitionMCP_CI/FolderTarget", "executed")]
    assert structured["observed"][0]["configuration"][0]["tagType"] == "Folder"


def test_an_existing_target_is_conflict_and_nothing_is_dispatched() -> None:
    # The recorded call list stops at the existence check, so "nothing was
    # dispatched" is asserted by the runner rather than by the message.
    error = _error("target-exists", "conflict")

    assert error["details"]["reason"] == "preflightPreconditionFailed"
    assert _problem_reasons(error) == [(TARGET, "targetExists")]
    assert error["details"]["auditRecorded"] is True
    assert "system.tag.configure" not in _recorded_targets("target-exists")


def test_one_existing_item_refuses_the_whole_batch() -> None:
    error = _error("preflight-refuses-whole-batch-on-existing-target", "conflict")

    assert _problem_reasons(error) == [(TARGET2, "targetExists")]
    assert "system.tag.configure" not in _recorded_targets(
        "preflight-refuses-whole-batch-on-existing-target"
    )


@pytest.mark.parametrize(
    ("name", "reason"),
    [("existence-check-fails", "existenceCheckFailed"),
     ("existence-check-indeterminate", "existenceCheckIndeterminate")],
)
def test_an_unanswered_existence_check_is_never_read_as_absent(name: str, reason: str) -> None:
    error = _error(name, "upstream_error")

    assert _problem_reasons(error) == [(TARGET, reason)]


def test_a_target_outside_the_allowlist_is_refused_at_the_segment_boundary() -> None:
    error = _error("target-not-allowlisted", "permission_denied")

    assert error["details"]["reason"] == "preflightTargetRefused"
    assert _problem_reasons(error) == [(SIBLING, "targetNotAllowlisted")]


def test_one_refused_target_rejects_the_whole_batch() -> None:
    error = _error("preflight-refuses-whole-batch-on-target", "permission_denied")

    assert _problem_reasons(error) == [(SIBLING, "targetNotAllowlisted")]


def test_a_policy_without_this_tools_key_allows_no_target() -> None:
    error = _error("policy-without-this-tools-allowlist", "permission_denied")

    assert _problem_reasons(error) == [(TARGET, "targetNotAllowlisted")]


def test_the_reserved_policy_provider_is_refused_even_under_an_explicit_wildcard() -> None:
    error = _error("reserved-provider-under-wildcard", "permission_denied")

    assert _problem_reasons(error) == [("[IgnitionMCPPolicy]WriteProbe", "reservedProvider")]


def test_a_udt_definition_needs_an_explicit_types_entry() -> None:
    """D30 §6: a bare * does not cover `_types_`, and neither does a plain Tag
    prefix, even though the target starts with the same characters."""
    under_wildcard = _error("udt-definition-under-wildcard", "permission_denied")
    under_prefix = _error("udt-definition-under-plain-prefix", "permission_denied")

    assert _problem_reasons(under_wildcard) == [(UDT, "udtDefinitionNotAllowlisted")]
    assert _problem_reasons(under_prefix) == [(UDT, "udtDefinitionNotAllowlisted")]


def test_an_explicit_types_entry_creates_a_udt_definition() -> None:
    structured = _structured("udt-definition-with-explicit-types-entry")

    assert _statuses(structured) == [(UDT, "executed")]
    assert structured["summary"]["succeeded"] == 1


def test_a_folder_named_types_deeper_in_the_path_is_an_ordinary_target() -> None:
    """D30 §6's grammar is positional: only the *first* post-provider segment selects
    the UDT definition namespace, so a folder that merely happens to be called `_types_`
    deeper in the path is an ordinary target. It is covered by the ordinary allowlist
    entry and reaches the Gateway like any other create — which is what the recorded
    call list shows, and what the membership test alone got wrong."""
    structured = _structured("deeper-folder-types-namespace")

    assert _statuses(structured) == [("[default]IgnitionMCP_CI/_types_/Probe", "executed")]
    assert structured["summary"]["succeeded"] == 1
    # The dispatch happened: the pre-fix membership rule refused this batch before any
    # native call, so the recorded sequence is the difference between the two rules.
    assert "system.tag.configure" in _recorded_targets("deeper-folder-types-namespace")


@pytest.mark.parametrize(
    ("name", "reason"),
    [
        ("policy-missing", "declaredLengthUnavailable"),
        ("policy-oversize", "declaredLengthOversize"),
        ("policy-length-mismatch", "policyLengthMismatch"),
        ("policy-malformed", "policyAuditMode"),
        ("policy-null-audit-profile", "policyAuditProfile"),
        ("policy-broken-allowlist-entry", "policyAllowlists"),
    ],
)
def test_an_unusable_policy_fails_closed_with_operation_disabled(name: str, reason: str) -> None:
    error = _error(name, "operation_disabled")

    assert error["details"]["reason"] == reason
    assert error["details"]["policyPath"] == "[IgnitionMCPPolicy]RuntimeTargetPolicy"


def test_required_audit_mode_checks_the_audit_profile_before_executing() -> None:
    error = _error("audit-required-profile-missing", "operation_disabled")

    assert error["details"]["reason"] == "auditProfileUnavailable"


def test_required_audit_mode_fails_closed_when_the_attempt_record_fails() -> None:
    error = _error("audit-required-attempt-fails", "operation_disabled")

    assert error["details"]["reason"] == "auditAttemptFailed"


def test_a_failed_result_audit_stays_a_success_with_audit_recorded_false() -> None:
    structured = _structured("audit-result-fails")

    assert structured["summary"]["succeeded"] == 1
    assert structured["summary"]["auditRecorded"] is False
    assert structured["summary"]["auditMode"] == "best_effort"


def test_audit_off_records_nothing() -> None:
    structured = _structured("audit-off")

    assert structured["summary"]["auditMode"] == "off"
    assert structured["summary"]["auditRecorded"] is False
    assert _recorded_targets("audit-off").count("system.util.audit") == 0


def test_a_denied_target_is_audited_as_a_decision_and_creates_nothing() -> None:
    """D08/D18: a denied mutation is audited. The ordered call list is the proof:
    one decision row after the policy read, and no `configure` at all."""
    assert _recorded_targets("decision-audit-on-denial") == [
        "system.tag.readBlocking",
        "system.tag.readBlocking",
        "system.util.audit",
    ]
    error = _error("decision-audit-on-denial", "permission_denied")

    assert error["details"]["reason"] == "preflightTargetRefused"
    assert error["details"]["auditRecorded"] is True


def test_audit_off_still_records_no_decision_row() -> None:
    assert _recorded_targets("decision-audit-off") == [
        "system.tag.readBlocking",
        "system.tag.readBlocking",
        "system.tag.exists",
    ]
    error = _error("decision-audit-off", "conflict")

    assert error["details"]["auditRecorded"] is False


def test_required_mode_gates_the_decision_row_on_the_audit_profile() -> None:
    recorded = _error("decision-audit-required", "conflict")
    assert recorded["details"]["auditRecorded"] is True

    # A required mode whose denial row cannot be written refuses the call rather
    # than reporting an unaudited denial.
    failed = _error("decision-audit-required-write-fails", "operation_disabled")
    assert failed["details"]["reason"] == "auditAttemptFailed"
    assert failed["details"]["phase"] == "decision"


def test_a_dispatch_that_raises_ends_the_batch_without_replaying_it() -> None:
    structured = _structured("dispatch-raises")

    assert _statuses(structured) == [
        (TARGET, "executed"),
        (TARGET2, "outcome_unknown"),
        ("[default]IgnitionMCP_CI/CreatedTarget3", "not_executed"),
    ]
    assert structured["summary"]["outcomeUnknown"] == 1
    assert structured["summary"]["notExecuted"] == 1


def test_an_indeterminate_native_outcome_does_not_stop_the_batch() -> None:
    structured = _structured("native-outcome-indeterminate")

    assert _statuses(structured) == [(TARGET, "outcome_unknown"), (TARGET2, "executed")]
    assert structured["summary"]["outcomeUnknown"] == 1
    assert structured["summary"]["notExecuted"] == 0


def test_a_target_that_appears_after_the_existence_check_is_a_raced_conflict() -> None:
    """D11 and D30 §2: a create takes no Precondition token, so its concurrency rule is
    the collision rule. `exists` answers false and Preflight dispatches, then another
    writer creates the target and the fixed `Abort` policy refuses — the mutation never
    lands. The item is the conflict the contract requires, not a failed create, and its
    Native outcome is kept beside it.

    The provider's own collision QualityCode is not established for this tuple, so the
    handler does not infer from the code: one bounded post-failure existence check decides
    it, and the recorded call list proves both that check and that the mutation was never
    replayed.
    """
    structured = _structured("raced-collision")

    item = structured["items"][0]
    assert item["status"] == "conflict"
    assert item["reason"] == "targetExists"
    # The provider's answer is still visible, and it was not Good.
    assert item["nativeOutcome"]["good"] is False
    assert item["nativeOutcome"]["name"] == "Bad_Unsupported"
    assert structured["summary"]["succeeded"] == 0
    assert structured["summary"]["failed"] == 1
    targets = _recorded_targets("raced-collision")
    # Exactly one configure: the race is reported, never replayed.
    assert targets.count("system.tag.configure") == 1
    # The post-failure existence check runs once, after the dispatch.
    assert targets.count("system.tag.exists") == 2
    assert targets.index("system.tag.configure") < targets.index("system.tag.exists", targets.index("system.tag.configure"))


@pytest.mark.parametrize("name", ["observed-read-fails", "observed-read-empty"])
def test_a_failed_observed_read_does_not_change_the_item_outcome(name: str) -> None:
    structured = _structured(name)

    assert structured["items"][0]["status"] == "executed"
    assert structured["observed"][0]["status"] == "error"
    assert structured["observed"][0]["error"]["code"] == "upstream_error"


def test_a_serialization_failure_still_reports_the_native_outcomes() -> None:
    """The node was created and its outcome is known; failing to serialize the
    Observed state must not turn the batch into an `outcome_unknown`."""
    structured = _structured("serialization-fails")

    assert structured["items"][0]["status"] == "executed"
    assert structured["summary"]["succeeded"] == 1
    assert structured["observed"][0]["error"]["code"] == "schema_mismatch"
    assert "serializ" in structured["observed"][0]["error"]["message"]


def test_an_observed_configuration_over_its_budget_does_not_decide_the_item_outcome() -> None:
    structured = _structured("observed-configuration-over-budget")

    assert structured["items"][0]["status"] == "executed"
    observed = structured["observed"][0]
    assert observed["status"] == "error"
    assert observed["error"]["code"] == "limit_exceeded"
    assert "16384" in observed["error"]["message"]


def test_the_observed_state_budget_marks_only_the_configurations_it_cannot_return() -> None:
    structured = _structured("observed-state-budget-exhausted")

    assert [item["status"] for item in structured["items"]] == ["executed"] * 9
    # Each recorded configuration measures ~11.2 KiB against the 64 KiB Observed-state
    # budget, so five fit (they reach 56215 bytes) and the sixth is where it is spent.
    statuses = [entry["status"] for entry in structured["observed"]]
    assert statuses[:5] == ["ok"] * 5
    assert statuses[5:] == ["error"] * 4
    assert structured["observed"][5]["error"]["code"] == "limit_exceeded"


@pytest.mark.parametrize(
    ("name", "reason", "code"),
    [
        ("items-not-an-array", "itemsNotAnArray", "invalid_argument"),
        ("items-empty", "itemsNotAnArray", "invalid_argument"),
        ("items-over-hard-limit", "itemsOverHardLimit", "limit_exceeded"),
    ],
)
def test_the_item_array_is_bounded_and_validated_before_the_policy_read(
    name: str, reason: str, code: str
) -> None:
    # These fixtures record no native call at all, so the runner fails the run if
    # the handler reads the policy before refusing the input.
    error = _error(name, code)

    assert error["details"]["reason"] == reason


def test_every_item_shape_refusal_is_reported_in_one_batch() -> None:
    error = _error("invalid-items", "invalid_argument")

    assert error["details"]["reason"] == "preflightInputFailed"
    assert [item["reason"] for item in error["details"]["items"]] == [
        "itemNotAnObject",
        "itemKeysMustBePathAndConfig",
        "pathNotAConfigPath",
        "pathNotAConfigPath",
        "configEmpty",
        "configValueIsAControlWrite",
        "configNestedTagsNotAllowed",
        "configNameChangeNotAllowed",
    ]


def test_the_item_hard_ceiling_reports_what_it_refused() -> None:
    error = _error("items-over-hard-limit", "limit_exceeded")

    assert error["details"] == {"reason": "itemsOverHardLimit", "requested": 101, "limit": 100}


@pytest.mark.parametrize(
    ("name", "reason", "requested", "limit"),
    [
        ("path-over-limit", "pathOverLength", 2053, 2048),
        ("config-string-over-limit", "configStringOverLimit", 16385, 16384),
        ("config-array-over-limit", "configArrayOverLimit", 1001, 1000),
        ("config-over-depth", "configOverDepth", 9, 8),
    ],
)
def test_an_input_over_its_own_ceiling_is_refused_before_any_native_call(
    name: str, reason: str, requested: int, limit: int
) -> None:
    error = _error(name, "limit_exceeded")

    assert error["details"]["reason"] == reason
    assert error["details"]["requested"] == requested
    assert error["details"]["limit"] == limit
    assert _recorded_targets(name) == []


def test_a_configuration_over_its_byte_budget_is_refused_before_any_native_call() -> None:
    error = _error("config-over-byte-budget", "limit_exceeded")

    assert error["details"]["reason"] == "configOverByteBudget"
    assert error["details"]["limit"] == 32768
    assert _recorded_targets("config-over-byte-budget") == []


def test_the_aggregate_input_byte_budget_is_finite() -> None:
    """Every item is inside its own ceilings and the batch is still refused, so the
    aggregate budget is what bounds the request."""
    error = _error("input-over-byte-budget", "limit_exceeded")

    assert error["details"]["reason"] == "inputOverByteBudget"
    assert error["details"]["limit"] == 65536
    assert error["details"]["requested"] > error["details"]["limit"]


def test_the_batch_default_is_twenty_and_a_deployment_may_raise_it_within_the_cap() -> None:
    """D10: the project safe default is 20 targets, the deployment may raise it up
    to the 100-target hard ceiling through the Runtime Target Policy."""
    error = _error("over-policy-limit", "limit_exceeded")

    assert error["details"] == {"reason": "itemsOverPolicyLimit", "requested": 21, "limit": 20}
    # The same 21 targets run when the document raises the limit to 25, so the
    # refusal above is the deployment default and not a hidden hard cap.
    structured = _structured("policy-raises-item-limit")
    assert structured["summary"]["requested"] == 21
    assert structured["summary"]["succeeded"] == 21


def test_a_policy_item_limit_outside_the_d10_hard_cap_fails_closed() -> None:
    error = _error("policy-item-limit-invalid", "operation_disabled")

    assert error["details"]["reason"] == "policyTagCreateMaxItems"


RECORDED_FIXTURES = sorted(path.name for path in FIXTURES.glob("tag_create-*.json"))


def test_every_recorded_fixture_is_exercised_by_this_module() -> None:
    source = Path(__file__).read_text(encoding="utf-8")
    for name in RECORDED_FIXTURES:
        case = name[len("tag_create-") : -len(".json")]
        assert f'"{case}"' in source, f"{name} is not referenced by a test"


def test_recorded_policy_documents_satisfy_the_shipped_schema() -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    schema = json.loads((ROOT / contract["runtimeTargetPolicy"]["documentSchema"]).read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)

    def replayed_policies(name: str) -> list[dict]:
        document = json.loads(_fixture(name).read_text(encoding="utf-8"))
        policies = []
        for call in document["calls"]:
            for item in (call.get("result") or {}).get("items") or []:
                if not isinstance(item, dict):
                    continue
                value = item.get("value")
                if isinstance(value, str) and value.startswith("{"):
                    policies.append(json.loads(value))
        return policies

    valid = [
        "allowlisted",
        "folder-target",
        "batch-with-a-bad-native-outcome",
        "target-exists",
        "existence-check-fails",
        "existence-check-indeterminate",
        "target-not-allowlisted",
        "preflight-refuses-whole-batch-on-target",
        "preflight-refuses-whole-batch-on-existing-target",
        "reserved-provider-under-wildcard",
        "policy-without-this-tools-allowlist",
        "udt-definition-under-wildcard",
        "udt-definition-under-plain-prefix",
        "udt-definition-with-explicit-types-entry",
        "audit-off",
        "audit-required-profile-missing",
        "decision-audit-on-denial",
        "policy-raises-item-limit",
    ]
    for name in valid:
        policies = replayed_policies(name)
        assert policies, name
        for policy in policies:
            validator.validate(policy)
    # A policy whose own item limit is outside the D10 hard cap is malformed, so it
    # is one of the documents the shipped schema has to reject.
    for name in ["policy-malformed", "policy-null-audit-profile", "policy-item-limit-invalid"]:
        policies = replayed_policies(name)
        assert policies, name
        for policy in policies:
            with pytest.raises(ValidationError):
                validator.validate(policy)


def test_handler_is_self_contained_tab_indented_and_ascii() -> None:
    source = HANDLER.read_text(encoding="utf-8")
    assert all(ord(character) < 128 for character in source)
    for line in source.splitlines()[1:]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indentation = line[: len(line) - len(line.lstrip())]
        assert set(indentation) <= {"\t"}, line
