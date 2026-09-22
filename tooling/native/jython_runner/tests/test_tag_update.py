"""D29 recorded-Jython coverage for `tag_update` (Phase 4 ticket #10).

Every fixture replays an ordered list of native calls, so a test can assert what
the handler did *not* do as well as what it returned: a stale fingerprint
dispatches nothing, a refused allowlist never reads a target, a missing target is
never created, and `off` mode makes no audit call at all. The fixtures replay the
shipped handler's own Tool Errors, so a test can assert *which* refusal a batch
produced and in which order the checks ran.
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
    / "packages/ignition-runtime-bundle/project/com.inductiveautomation.mcp/tools/tag_update"
    / "onToolCalled.py"
)
CONTRACT = ROOT / "contracts/tools/runtime/tag_update.contract.json"


def _fixture(name: str) -> Path:
    return FIXTURES / f"tag_update-{name}.json"


def _error(name: str, code: str) -> dict:
    return run_recorded_tool_error("tag_update", _fixture(name), expected_code=code)


def _structured(name: str) -> dict:
    return run_recorded_tool("tag_update", _fixture(name))["structuredContent"]


def _problem_reasons(error: dict) -> list[tuple[str, str]]:
    return [(item.get("path"), item.get("reason")) for item in error["details"]["items"]]


def _statuses(structured: dict) -> list[tuple[str, str]]:
    return [(item["path"], item["status"]) for item in structured["items"]]


WRITE = "[default]IgnitionMCP_CI/WriteTarget"
TEXT = "[default]IgnitionMCP_CI/TextTarget"
UDT = "[default]_types_/IgnitionMCP_CI/ProbeType"


def test_an_allowlisted_merge_update_reports_the_native_outcome_and_observed_state() -> None:
    structured = _structured("allowlisted")

    assert _statuses(structured) == [(WRITE, "executed")]
    assert structured["items"][0]["nativeOutcome"] == {
        "code": 192, "name": "Good", "level": "Good", "good": True, "diagnosticMessage": {
            "$ignition": "null",
        },
    }
    assert structured["summary"] == {
        "requested": 1,
        "succeeded": 1,
        "failed": 0,
        "outcomeUnknown": 0,
        "notExecuted": 0,
        "auditMode": "best_effort",
        "auditRecorded": True,
    }
    # Observed state is the tag_get_config re-read, and it carries the token the
    # caller's next update would use: the changed configuration has a new one.
    observed = structured["observed"][0]
    assert observed["status"] == "ok"
    assert observed["configuration"][0]["documentation"] == "phase4-updated"
    assert observed["fingerprint"].startswith("tcf1:")
    assert observed["fingerprint"] != run_recorded_tool_error(
        "tag_update", _fixture("fingerprint-mismatch"), expected_code="conflict"
    )["details"]["items"][0]["observedFingerprint"]


def test_the_structured_result_satisfies_the_committed_output_schema() -> None:
    structured = _structured("allowlisted")
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    schema = json.loads((ROOT / contract["outputSchema"]).read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(structured)

    assert contract["permissionClass"] == "CONFIG"
    assert contract["mutationClass"] == "CONFIG_MUTATION"
    assert contract["destructive"] is False
    assert contract["parameters"]["items"]["maxItems"] == 100
    assert contract["parameters"]["items"]["itemRequired"] == ["path", "expectedFingerprint", "config"]
    assert contract["preconditionToken"]["kind"] == "tag_config_fingerprint"


def test_batch_reloads_every_fingerprint_before_any_item_is_dispatched() -> None:
    """The fixture records the whole call sequence: both Preflight reads happen
    before the first system.tag.configure, which is what D30 §3 requires."""
    document = json.loads(_fixture("batch-with-a-bad-native-outcome").read_text(encoding="utf-8"))
    targets = [call["target"] for call in document["calls"]]

    reads = [index for index, target in enumerate(targets) if target == "system.tag.getConfiguration"]
    existence = [index for index, target in enumerate(targets) if target == "system.tag.exists"]
    first_configure = targets.index("system.tag.configure")
    assert targets[first_configure - 1] == "system.util.audit"
    # One existence check per target, each followed by its own configuration read,
    # and all of them before the first dispatch. The later reads are the observed
    # half of the result.
    assert len(existence) == 2
    assert len(reads) == 4
    assert all(index < first_configure for index in existence + reads[:2])

    structured = _structured("batch-with-a-bad-native-outcome")
    assert _statuses(structured) == [(WRITE, "executed"), (TEXT, "executed")]
    assert [item["nativeOutcome"]["good"] for item in structured["items"]] == [True, False]
    assert structured["summary"]["succeeded"] == 1
    assert structured["summary"]["failed"] == 1


def test_a_stale_fingerprint_is_conflict_and_dispatches_nothing() -> None:
    # The recorded call list stops at the Preflight read, so "nothing was
    # dispatched" is asserted by the runner rather than by the message.
    error = _error("fingerprint-mismatch", "conflict")

    assert error["details"]["reason"] == "preflightPreconditionFailed"
    assert error["details"]["allowlistKey"] == "tag_update"
    item = error["details"]["items"][0]
    assert item["reason"] == "fingerprintMismatch"
    assert item["expectedFingerprint"] == "tcf1:" + "0" * 64
    assert item["observedFingerprint"].startswith("tcf1:")


def test_one_stale_item_refuses_the_whole_batch_before_anything_executes() -> None:
    error = _error("preflight-refuses-whole-batch-on-stale-fingerprint", "conflict")

    assert [item["path"] for item in error["details"]["items"]] == [TEXT]
    assert error["details"]["items"][0]["reason"] == "fingerprintMismatch"


def test_a_missing_target_is_not_found_and_is_never_created() -> None:
    """A configuration read cannot answer this question: the Gateway synthesizes a
    default node for a path that is not there (the ticket #10 live run recorded the
    same one on 8.3.8 and 8.3.9), so `system.tag.exists` decides, and the missing
    fixture records only that call — the handler never dispatches anything."""
    error = _error("missing-target", "not_found")

    assert error["details"]["reason"] == "preflightPreconditionFailed"
    assert _problem_reasons(error) == [("[default]IgnitionMCP_CI/Missing", "targetMissing")]


def test_an_existence_check_that_raises_refuses_the_batch() -> None:
    error = _error("existence-check-fails", "upstream_error")

    assert _problem_reasons(error) == [(WRITE, "existenceCheckFailed")]


def test_an_existence_check_that_answers_something_else_is_not_a_yes() -> None:
    error = _error("existence-check-indeterminate", "upstream_error")

    assert _problem_reasons(error) == [(WRITE, "existenceCheckIndeterminate")]


def test_an_empty_configuration_read_for_an_existing_target_is_refused() -> None:
    error = _error("configuration-unavailable", "upstream_error")

    assert _problem_reasons(error) == [(WRITE, "configurationUnavailable")]


def test_the_first_failing_item_decides_the_refusal_code() -> None:
    """D30 §7 fixes the codes; item order fixes which one a mixed batch reports."""
    error = _error("first-failure-decides-the-code-missing", "not_found")

    assert _problem_reasons(error) == [
        ("[default]IgnitionMCP_CI/Missing", "targetMissing"),
        (TEXT, "fingerprintMismatch"),
    ]


def test_a_target_outside_the_allowlist_is_refused_at_the_segment_boundary() -> None:
    error = _error("target-not-allowlisted", "permission_denied")

    assert error["details"]["reason"] == "preflightTargetRefused"
    assert _problem_reasons(error) == [("[default]IgnitionMCP_CI2/WriteTarget", "targetNotAllowlisted")]


def test_one_refused_target_rejects_the_whole_batch() -> None:
    error = _error("preflight-refuses-whole-batch-on-target", "permission_denied")

    assert _problem_reasons(error) == [("[default]IgnitionMCP_CI2/WriteTarget", "targetNotAllowlisted")]


def test_the_reserved_policy_provider_is_refused_even_under_an_explicit_wildcard() -> None:
    error = _error("reserved-provider-under-wildcard", "permission_denied")

    assert _problem_reasons(error) == [("[IgnitionMCPPolicy]WriteProbe", "reservedProvider")]


def test_a_policy_without_this_tools_key_allows_no_target() -> None:
    error = _error("policy-without-this-tools-allowlist", "permission_denied")

    assert _problem_reasons(error) == [(WRITE, "targetNotAllowlisted")]


def test_a_udt_definition_needs_an_explicit_types_entry() -> None:
    """D30 §6: a bare * does not cover `_types_`, and neither does a plain Tag
    prefix, even though the target starts with the same characters."""
    under_wildcard = _error("udt-definition-under-wildcard", "permission_denied")
    under_prefix = _error("udt-definition-under-plain-prefix", "permission_denied")

    assert _problem_reasons(under_wildcard) == [(UDT, "udtDefinitionNotAllowlisted")]
    assert _problem_reasons(under_prefix) == [(UDT, "udtDefinitionNotAllowlisted")]


def test_an_explicit_types_entry_lets_a_udt_definition_update_through() -> None:
    structured = _structured("udt-definition-with-explicit-types-entry")

    assert _statuses(structured) == [(UDT, "executed")]
    assert structured["summary"]["succeeded"] == 1
    assert structured["observed"][0]["configuration"][0]["documentation"] == "phase4-updated"


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


def test_a_dispatch_that_raises_ends_the_batch_without_replaying_it() -> None:
    structured = _structured("dispatch-raises")

    assert _statuses(structured) == [
        (WRITE, "executed"),
        (TEXT, "outcome_unknown"),
        (UDT, "not_executed"),
    ]
    assert structured["summary"] == {
        "requested": 3,
        "succeeded": 1,
        "failed": 0,
        "outcomeUnknown": 1,
        "notExecuted": 1,
        "auditMode": "best_effort",
        "auditRecorded": True,
    }


def test_an_indeterminate_native_outcome_does_not_stop_the_batch() -> None:
    structured = _structured("native-outcome-indeterminate")

    assert _statuses(structured) == [(WRITE, "outcome_unknown"), (TEXT, "executed")]
    assert structured["summary"]["outcomeUnknown"] == 1
    assert structured["summary"]["succeeded"] == 1
    assert structured["summary"]["notExecuted"] == 0


@pytest.mark.parametrize("name", ["observed-read-fails", "observed-read-empty"])
def test_a_failed_observed_read_does_not_change_the_item_outcome(name: str) -> None:
    structured = _structured(name)

    assert structured["items"][0]["status"] == "executed"
    assert structured["observed"][0]["status"] == "error"
    assert structured["observed"][0]["error"]["code"] == "upstream_error"
    assert structured["summary"]["auditRecorded"] is True


@pytest.mark.parametrize(
    ("name", "reason", "code"),
    [
        ("items-not-an-array", "itemsNotAnArray", "invalid_argument"),
        ("items-empty", "itemsNotAnArray", "invalid_argument"),
        ("items-over-hard-limit", "itemsOverHardLimit", "limit_exceeded"),
    ],
)
def test_the_item_array_is_bounded_and_validated_before_the_policy_read(name: str, reason: str, code: str) -> None:
    # These fixtures record no native call at all, so the runner fails the run if
    # the handler reads the policy before refusing the input.
    error = _error(name, code)

    assert error["details"]["reason"] == reason


def test_every_item_shape_refusal_is_reported_in_one_batch() -> None:
    error = _error("invalid-items", "invalid_argument")

    assert error["details"]["reason"] == "preflightInputFailed"
    assert [item["reason"] for item in error["details"]["items"]] == [
        "itemNotAnObject",
        "itemKeysMustBePathFingerprintAndConfig",
        "pathNotAConfigPath",
        "pathNotAConfigPath",
        "pathNotAConfigPath",
        "pathNotAConfigPath",
        "fingerprintNotATagConfigFingerprint",
        "fingerprintNotATagConfigFingerprint",
        "configEmpty",
        "configValueIsAControlWrite",
        "configNestedTagsNotAllowed",
        "configNameChangeNotAllowed",
    ]


def test_the_item_hard_ceiling_reports_what_it_refused() -> None:
    error = _error("items-over-hard-limit", "limit_exceeded")

    assert error["details"] == {"reason": "itemsOverHardLimit", "requested": 101, "limit": 100}


RECORDED_FIXTURES = sorted(path.name for path in FIXTURES.glob("tag_update-*.json"))


def test_every_recorded_fixture_is_exercised_by_this_module() -> None:
    # The read-token flow pairs a tag_get_config fixture with a tag_update one, so
    # both modules are searched for a reference.
    sources = [
        Path(__file__).read_text(encoding="utf-8"),
        (Path(__file__).with_name("test_tag_get_config.py")).read_text(encoding="utf-8"),
    ]
    for name in RECORDED_FIXTURES:
        case = name[len("tag_update-") : -len(".json")]
        assert any(f'"{case}"' in source or f'"{name[:-len(".json")]}"' in source for source in sources), (
            f"{name} is not referenced by a test"
        )


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
        "configuration-unavailable",
        "existence-check-fails",
        "existence-check-indeterminate",
        "batch-with-a-bad-native-outcome",
        "fingerprint-mismatch",
        "preflight-refuses-whole-batch-on-stale-fingerprint",
        "missing-target",
        "target-not-allowlisted",
        "reserved-provider-under-wildcard",
        "udt-definition-under-wildcard",
        "udt-definition-with-explicit-types-entry",
        "audit-off",
        "audit-required-profile-missing",
        "policy-without-this-tools-allowlist",
        "policy-broken-allowlist-entry",
    ]
    for name in valid:
        policies = replayed_policies(name)
        assert policies, name
        for policy in policies:
            validator.validate(policy)
    for name in ["policy-malformed", "policy-null-audit-profile"]:
        policies = replayed_policies(name)
        assert policies, name
        for policy in policies:
            with pytest.raises(ValidationError):
                validator.validate(policy)


def test_handler_is_self_contained_and_tab_indented() -> None:
    source = HANDLER.read_text(encoding="utf-8")
    for line in source.splitlines()[1:]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indentation = line[: len(line) - len(line.lstrip())]
        assert set(indentation) <= {"\t"}, line


def _recorded_targets(name: str) -> list[str]:
    """The ordered native calls a fixture replays (the negative half of a case)."""
    document = json.loads(_fixture(name).read_text(encoding="utf-8"))
    return [entry["target"] for entry in document["calls"]]


def test_the_batch_default_is_twenty_and_a_deployment_may_raise_it_within_the_cap() -> None:
    """D10: the project safe default is 20 targets, the deployment may raise it up
    to the 100-target hard ceiling through the Runtime Target Policy."""
    error = _error("over-policy-limit", "limit_exceeded")

    assert error["details"] == {
        "reason": "itemsOverPolicyLimit",
        "requested": 21,
        "limit": 20,
    }
    # The same 21 targets run when the document raises the limit to 25, so the
    # refusal above is the deployment default and not a hidden hard cap.
    structured = _structured("policy-raises-item-limit")
    assert structured["summary"]["requested"] == 21
    assert structured["summary"]["succeeded"] == 21


def test_a_policy_item_limit_outside_the_d10_hard_cap_fails_closed() -> None:
    error = _error("policy-item-limit-invalid", "operation_disabled")

    assert error["details"]["reason"] == "policyTagUpdateMaxItems"


def test_a_path_over_the_byte_ceiling_is_refused_before_any_native_call() -> None:
    error = _error("path-over-limit", "limit_exceeded")

    assert error["details"]["reason"] == "pathOverLength"
    assert error["details"]["index"] == 0
    assert error["details"]["requested"] == 2053
    assert error["details"]["limit"] == 2048


def test_a_configuration_string_over_the_byte_ceiling_is_refused_before_any_native_call() -> None:
    error = _error("config-string-over-limit", "limit_exceeded")

    assert error["details"]["reason"] == "configStringOverLimit"
    assert error["details"]["index"] == 0
    assert error["details"]["requested"] == 16385
    assert error["details"]["limit"] == 16384


def test_a_configuration_array_over_the_element_ceiling_is_refused_before_any_native_call() -> None:
    error = _error("config-array-over-limit", "limit_exceeded")

    assert error["details"]["reason"] == "configArrayOverLimit"
    assert error["details"]["requested"] == 1001
    assert error["details"]["limit"] == 1000


def test_a_configuration_deeper_than_the_depth_ceiling_is_refused_before_any_native_call() -> None:
    error = _error("config-over-depth", "limit_exceeded")

    assert error["details"]["reason"] == "configOverDepth"
    assert error["details"]["requested"] == 9
    assert error["details"]["limit"] == 8


def test_a_configuration_over_its_byte_budget_is_refused_before_any_native_call() -> None:
    error = _error("config-over-byte-budget", "limit_exceeded")

    assert error["details"]["reason"] == "configOverByteBudget"
    assert error["details"]["limit"] == 32768
    assert error["details"]["requested"] > error["details"]["limit"]


def test_the_aggregate_input_byte_budget_is_finite() -> None:
    """Every item is inside its own ceilings and the batch is still refused, so the
    aggregate budget is what bounds the request."""
    error = _error("input-over-byte-budget", "limit_exceeded")

    assert error["details"]["reason"] == "inputOverByteBudget"
    assert error["details"]["limit"] == 65536
    assert error["details"]["requested"] > error["details"]["limit"]


def test_a_denied_target_is_audited_as_a_decision_and_dispatches_no_change() -> None:
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


def test_a_precondition_denial_is_audited_as_a_decision_too() -> None:
    assert _recorded_targets("decision-audit-on-conflict") == [
        "system.tag.readBlocking",
        "system.tag.readBlocking",
        "system.tag.exists",
        "system.tag.getConfiguration",
        "system.util.audit",
    ]
    conflict = _error("decision-audit-on-conflict", "conflict")
    assert conflict["details"]["auditRecorded"] is True

    assert _recorded_targets("decision-audit-on-missing-target") == [
        "system.tag.readBlocking",
        "system.tag.readBlocking",
        "system.tag.exists",
        "system.util.audit",
    ]
    missing = _error("decision-audit-on-missing-target", "not_found")
    assert missing["details"]["auditRecorded"] is True


def test_audit_off_still_records_no_denial_row() -> None:
    assert _recorded_targets("decision-audit-off") == [
        "system.tag.readBlocking",
        "system.tag.readBlocking",
    ]
    error = _error("decision-audit-off", "permission_denied")

    assert error["details"]["auditRecorded"] is False


def test_required_mode_gates_the_denial_row_on_the_audit_profile() -> None:
    recorded = _error("decision-audit-required", "permission_denied")
    assert recorded["details"]["auditRecorded"] is True

    # A required mode whose denial row cannot be written refuses the call rather
    # than reporting an unaudited denial.
    failed = _error("decision-audit-required-write-fails", "operation_disabled")
    assert failed["details"]["reason"] == "auditAttemptFailed"
    assert failed["details"]["phase"] == "decision"


def test_an_observed_configuration_over_its_budget_does_not_decide_the_item_outcome() -> None:
    structured = _structured("observed-configuration-over-budget")

    assert structured["items"][0]["status"] == "executed"
    assert structured["summary"]["succeeded"] == 1
    observed = structured["observed"][0]
    assert observed["status"] == "error"
    assert observed["error"]["code"] == "limit_exceeded"
    assert "16384" in observed["error"]["message"]


def test_the_observed_state_budget_marks_only_the_configurations_it_cannot_return() -> None:
    structured = _structured("observed-state-budget-exhausted")

    assert [item["status"] for item in structured["items"]] == ["executed"] * 9
    assert structured["summary"]["succeeded"] == 9
    statuses = [entry["status"] for entry in structured["observed"]]
    assert statuses[:6] == ["ok"] * 6
    assert statuses[6:] == ["error"] * 3
    assert structured["observed"][6]["error"]["code"] == "limit_exceeded"


def test_a_serialization_failure_still_reports_the_native_outcomes() -> None:
    """The change was dispatched and its outcome is known; failing to serialize the
    Observed state must not turn the batch into an `outcome_unknown`."""
    structured = _structured("serialization-fails")

    assert structured["items"][0]["status"] == "executed"
    assert structured["summary"]["succeeded"] == 1
    assert structured["summary"]["auditRecorded"] is True
    assert structured["observed"][0]["status"] == "error"
    assert structured["observed"][0]["error"]["code"] == "schema_mismatch"
    assert "serializ" in structured["observed"][0]["error"]["message"]


def test_the_contract_declares_the_d10_input_bounds() -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))

    assert contract["inputBounds"]["defaultItems"] == 20
    assert contract["inputBounds"]["hardItems"] == 100
    assert contract["inputBounds"]["hardItemsPolicyField"] == "tagUpdateMaxItems"
    assert contract["inputBounds"]["maxInputBytes"] == 65536
    assert contract["inputBounds"]["outputMaxBytes"] == 262144
