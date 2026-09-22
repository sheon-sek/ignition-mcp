"""D29 recorded-Jython coverage for `alarm_shelve` (Phase 4 ticket #8).

Every fixture replays an ordered list of native calls, so a test can assert what
the handler did *not* do as well as what it returned: a refused batch never calls
`system.alarm.shelve`, an over-cap policy document is never read, and `off` mode
makes no audit call at all.
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
    / "packages/ignition-runtime-bundle/project/com.inductiveautomation.mcp/tools/alarm_shelve"
    / "onToolCalled.py"
)
EXACT = "prov:default:/tag:MCP_P4_1/Exact:/alm:ProbeHi"
SIBLING = "prov:default:/tag:MCP_P4_1/ExactSibling:/alm:ProbeHi"
NESTED = "prov:default:/tag:MCP_P4_1/Fold/ChildA:/alm:ProbeHi"
WILDCARD_PATH = "prov:default:/tag:MCP_P4_1/*"
#: A target *inside* the reserved provider: the `prov:` component is the reserved
#: name, which is the only thing D30's owner ruling reserves.
RESERVED_PATH = "prov:IgnitionMCPPolicy:/tag:RuntimeTargetPolicy:/alm:ProbeHi"
#: Targets whose *provider* is `default` while a later Tag segment spells the
#: reserved name. `RESERVED_NAME_PATH` is the sibling the review named; the two
#: must never be refused as `reservedProvider`.
RESERVED_NAME_PATH = "prov:default:/tag:IgnitionMCPPolicyPump:/alm:High"
RESERVED_SEGMENT_PATH = "prov:default:/tag:IgnitionMCPPolicy/RuntimeTargetPolicy:/alm:ProbeHi"


def _fixture(name: str) -> Path:
    return FIXTURES / f"alarm_shelve-{name}.json"


def _error(name: str, code: str) -> dict:
    return run_recorded_tool_error("alarm_shelve", _fixture(name), expected_code=code)


def _item_reasons(error: dict) -> list[tuple[str, str]]:
    return [(item.get("path"), item.get("reason")) for item in error["details"]["items"]]


def _recorded_targets(name: str) -> list[str]:
    """The ordered native calls a fixture replays (the negative half of a case)."""
    document = json.loads(_fixture(name).read_text(encoding="utf-8"))
    return [entry["target"] for entry in document["calls"]]


def test_allowlisted_batch_shelves_each_item_and_reports_the_shelved_state() -> None:
    structured = run_recorded_tool("alarm_shelve", _fixture("allowlisted-batch"))["structuredContent"]

    assert structured["items"] == [
        {"path": EXACT, "status": "executed"},
        {"path": NESTED, "status": "executed"},
    ]
    assert structured["summary"] == {
        "requested": 2,
        "executed": 2,
        "outcomeUnknown": 0,
        "timeoutSeconds": 3600,
        "auditMode": "best_effort",
        "auditRecorded": True,
    }
    # D12: alarm_shelved_list state for the exact paths is the Observed state,
    # including the shelving identity and expiration alarm_status cannot give.
    assert structured["observed"][0] == {
        "path": EXACT,
        "status": "ok",
        "shelved": True,
        "user": "ignition-mcp-service",
        "expiration": "2026-09-22T01:00:00Z",
        "expired": False,
    }
    assert structured["observed"][1]["shelved"] is True
    assert structured["observed"][1]["expired"] is True
    assert structured["meta"]["correlationId"]


def test_a_shelve_of_a_pattern_the_view_does_not_show_is_still_reported_honestly() -> None:
    """Observed state never decides success: a literal pattern that matches no
    Alarm is not a failure (D12), so the item is executed and the view says so."""
    structured = run_recorded_tool(
        "alarm_shelve", _fixture("observed-not-shelved")
    )["structuredContent"]

    assert structured["items"] == [{"path": EXACT, "status": "executed"}]
    assert structured["summary"]["executed"] == 1
    assert structured["observed"] == [{"path": EXACT, "status": "ok", "shelved": False}]


def test_indeterminate_item_is_outcome_unknown_without_failing_the_batch() -> None:
    structured = run_recorded_tool("alarm_shelve", _fixture("dispatch-raises"))["structuredContent"]

    assert structured["items"] == [
        {"path": EXACT, "status": "outcome_unknown"},
        {"path": NESTED, "status": "executed"},
    ]
    assert structured["summary"]["executed"] == 1
    assert structured["summary"]["outcomeUnknown"] == 1
    assert structured["observed"][0] == {"path": EXACT, "status": "ok", "shelved": False}


def test_contract_schema_covers_the_structured_result() -> None:
    structured = run_recorded_tool("alarm_shelve", _fixture("allowlisted-batch"))["structuredContent"]
    contract = json.loads(
        (ROOT / "contracts/tools/runtime/alarm_shelve.contract.json").read_text(encoding="utf-8")
    )
    schema = json.loads((ROOT / contract["outputSchema"]).read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(structured)

    assert contract["permissionClass"] == "CONTROL"
    assert contract["mutationClass"] == "CONTROL_MUTATION"
    assert contract["destructive"] is False
    assert contract["parameters"]["paths"]["maxItems"] == 100
    assert contract["parameters"]["paths"]["required"] is True
    assert contract["parameters"]["timeoutSeconds"]["required"] is True
    assert contract["parameters"]["timeoutSeconds"]["hardMaximum"] == 86400
    assert contract["parameters"]["timeoutSeconds"]["policyCapField"] == "alarmShelveMaxSeconds"
    assert contract["automaticRetryAfterAmbiguousOutcome"] is False


@pytest.mark.parametrize(
    ("seconds", "reason", "details"),
    [
        (None, "durationRequired", None),
        (0, "durationOutOfRange", {"requested": 0, "minimum": 1, "maximum": 86400}),
        (86401, "durationOutOfRange", {"requested": 86401, "minimum": 1, "maximum": 86400}),
    ],
)
def test_the_duration_is_required_and_bounded_before_anything_is_read(
    seconds: object, reason: str, details: dict | None,
) -> None:
    """D12 Phase 4 amendment: an explicit duration, 1 s minimum, 24 h hard max.
    The fixtures record no native call at all, so the range check really does
    precede the policy read."""
    name = {None: "duration-null", 0: "duration-out-of-range", 86401: "duration-over-hard-max"}[seconds]
    error = _error(name, "invalid_argument")

    assert error["details"]["reason"] == reason
    if details is not None:
        for key, value in details.items():
            assert error["details"][key] == value


def test_the_policy_cap_lowers_the_maximum_and_is_checked_before_dispatch() -> None:
    error = _error("duration-over-policy-cap", "invalid_argument")

    assert error["details"] == {
        "reason": "durationOverPolicyCap",
        "requested": 7200,
        "cap": 3600,
        "hardMaximum": 86400,
    }


def test_wildcard_targets_are_refused_before_any_native_call() -> None:
    """D12 forbids wildcard mutation. A pattern without * matches only the source
    it spells out (ticket #6), so "no wildcard" is what makes a target exact."""
    error = _error("wildcard-path", "invalid_argument")

    assert error["details"]["reason"] == "preflightInputFailed"
    assert _item_reasons(error) == [(WILDCARD_PATH, "wildcardPath")]


def test_target_outside_the_allowlist_is_refused_at_the_segment_boundary() -> None:
    # The allowlist entry is .../MCP_P4_1/Exact; ExactSibling is a different
    # segment, and the recorded call list proves no shelve was dispatched.
    error = _error("target-not-allowlisted", "permission_denied")

    assert error["details"]["reason"] == "preflightTargetRefused"
    assert error["details"]["allowlistKey"] == "alarm_shelve"
    assert _item_reasons(error) == [(SIBLING, "targetNotAllowlisted")]


def test_reserved_policy_provider_is_refused_even_under_an_explicit_wildcard() -> None:
    error = _error("reserved-provider", "permission_denied")

    assert _item_reasons(error) == [(RESERVED_PATH, "reservedProvider")]
    # The same wildcard policy lets an ordinary Alarm path through, so the
    # refusal is by provider rather than by the allowlist.
    allowed = run_recorded_tool("alarm_shelve", _fixture("wildcard-allows-target"))["structuredContent"]
    assert allowed["items"][0]["status"] == "executed"
    assert allowed["summary"]["executed"] == 1


def test_a_later_segment_that_spells_the_reserved_name_is_not_the_reserved_provider() -> None:
    """D30's owner ruling matches the provider component only, so a target under an
    allowed provider is never refused for a Tag or Alarm segment that spells the
    reserved name (the review's `IgnitionMCPPolicyPump` sibling)."""
    structured = run_recorded_tool(
        "alarm_shelve", _fixture("reserved-name-in-later-segment")
    )["structuredContent"]

    assert structured["items"] == [
        {"path": RESERVED_NAME_PATH, "status": "executed"},
        {"path": RESERVED_SEGMENT_PATH, "status": "executed"},
    ]
    assert structured["summary"]["executed"] == 2
    assert [entry["shelved"] for entry in structured["observed"]] == [True, True]


def test_a_reserved_name_outside_the_allowlist_is_refused_as_unallowlisted() -> None:
    """The refusals stay distinct: a target outside the allowlist answers for that
    reason, even when a later segment spells the reserved name, so a refusal is
    never misattributed to the provider."""
    error = _error("reserved-name-not-allowlisted", "permission_denied")

    assert _item_reasons(error) == [(RESERVED_NAME_PATH, "targetNotAllowlisted")]


def test_one_bad_item_rejects_the_whole_batch_before_anything_executes() -> None:
    error = _error("preflight-refuses-whole-batch", "permission_denied")

    assert _item_reasons(error) == [(SIBLING, "targetNotAllowlisted")]


@pytest.mark.parametrize(
    ("name", "reason"),
    [
        ("policy-missing", "declaredLengthUnavailable"),
        ("policy-oversize", "declaredLengthOversize"),
        ("policy-length-mismatch", "policyLengthMismatch"),
        ("policy-unparseable", "policyMalformed"),
        ("policy-malformed", "policyAuditMode"),
        # The document contract omits a null-valued property; an explicit null is
        # not "absent" and must fail closed like any other malformed field.
        ("policy-null-audit-profile", "policyAuditProfile"),
        ("policy-cap-invalid", "policyAlarmShelveMaxSeconds"),
        ("policy-broken-allowlist-entry", "policyAllowlists"),
    ],
)
def test_an_unusable_policy_fails_closed_with_operation_disabled(name: str, reason: str) -> None:
    error = _error(name, "operation_disabled")

    assert error["details"]["reason"] == reason
    assert error["details"]["policyPath"] == "[IgnitionMCPPolicy]RuntimeTargetPolicy"


def test_this_tools_own_allowlist_entry_must_be_an_alarm_path() -> None:
    """A Tag path prefix and an Alarm source pattern are different languages: an
    entry written as a Tag path grants nothing, so it fails loudly instead."""
    error = _error("policy-broken-allowlist-entry", "operation_disabled")

    assert error["details"]["reason"] == "policyAllowlists"


def test_required_audit_mode_checks_the_audit_profile_before_executing() -> None:
    error = _error("audit-required-profile-missing", "operation_disabled")

    assert error["details"]["reason"] == "auditProfileUnavailable"
    assert error["details"]["auditProfile"] == "MCP_CI_AUDIT"


def test_required_audit_mode_fails_closed_when_the_attempt_record_fails() -> None:
    error = _error("audit-required-attempt-fails", "operation_disabled")

    assert error["details"]["reason"] == "auditAttemptFailed"


def test_a_failed_result_audit_stays_a_success_with_audit_recorded_false() -> None:
    structured = run_recorded_tool("alarm_shelve", _fixture("audit-result-fails"))["structuredContent"]

    assert structured["summary"]["executed"] == 1
    assert structured["summary"]["auditRecorded"] is False
    assert structured["summary"]["auditMode"] == "best_effort"


def test_audit_off_records_nothing() -> None:
    structured = run_recorded_tool("alarm_shelve", _fixture("audit-off"))["structuredContent"]

    assert structured["summary"]["auditMode"] == "off"
    assert structured["summary"]["auditRecorded"] is False


def test_a_failed_observed_read_does_not_change_the_item_outcome() -> None:
    structured = run_recorded_tool(
        "alarm_shelve", _fixture("observed-read-fails")
    )["structuredContent"]

    assert structured["items"] == [{"path": EXACT, "status": "executed"}]
    assert structured["summary"]["executed"] == 1
    assert structured["observed"][0]["status"] == "error"
    assert structured["observed"][0]["error"]["code"] == "upstream_error"


def test_an_oversize_shelved_view_is_reported_rather_than_materialized_further() -> None:
    structured = run_recorded_tool(
        "alarm_shelve", _fixture("observed-read-over-limit")
    )["structuredContent"]

    assert structured["items"][0]["status"] == "executed"
    assert structured["observed"][0]["status"] == "error"
    assert "bounded read limit" in structured["observed"][0]["error"]["message"]


def test_input_limits_are_enforced_before_the_policy_read() -> None:
    empty = _error("paths-not-an-array", "invalid_argument")
    assert empty["details"]["reason"] == "pathsNotAnArray"

    oversized = _error("paths-over-hard-limit", "limit_exceeded")
    assert oversized["details"] == {
        "reason": "pathsOverHardLimit",
        "requested": 101,
        "limit": 100,
    }


def test_the_target_default_is_twenty_and_a_deployment_may_raise_it_within_the_cap() -> None:
    """D10: the project safe default is 20 targets, raisable by the deployment up
    to the 100-target hard ceiling."""
    error = _error("paths-over-policy-limit", "limit_exceeded")

    assert error["details"] == {
        "reason": "pathsOverPolicyLimit",
        "requested": 21,
        "limit": 20,
    }
    structured = run_recorded_tool(
        "alarm_shelve", _fixture("policy-raises-path-limit")
    )["structuredContent"]
    assert structured["summary"]["requested"] == 3
    assert structured["summary"]["executed"] == 3


def test_a_policy_target_limit_outside_the_d10_hard_cap_fails_closed() -> None:
    error = _error("policy-path-limit-invalid", "operation_disabled")

    assert error["details"]["reason"] == "policyAlarmMaxPaths"


def test_the_aggregate_input_byte_budget_is_finite() -> None:
    error = _error("input-over-byte-budget", "limit_exceeded")

    assert error["details"]["reason"] == "inputOverByteBudget"
    assert error["details"]["requested"] == 80080  # forty 2002-byte paths
    assert error["details"]["limit"] == 65536


def test_a_denied_target_is_audited_as_a_decision_and_shelves_nothing() -> None:
    # The ordered call list is the proof: the policy read, one decision row, and
    # no `system.alarm.shelve` at all.
    assert _recorded_targets("target-not-allowlisted") == [
        "system.tag.readBlocking",
        "system.tag.readBlocking",
        "system.util.audit",
    ]
    error = _error("target-not-allowlisted", "permission_denied")

    assert error["details"]["reason"] == "preflightTargetRefused"
    assert error["details"]["auditRecorded"] is True


def test_audit_off_still_records_no_denial_row() -> None:
    assert _recorded_targets("decision-audit-off") == [
        "system.tag.readBlocking",
        "system.tag.readBlocking",
    ]
    error = _error("decision-audit-off", "permission_denied")

    assert error["details"]["auditRecorded"] is False


def test_required_mode_gates_the_denial_row_on_the_audit_profile() -> None:
    failed = _error("decision-audit-required-write-fails", "operation_disabled")

    assert failed["details"]["reason"] == "auditAttemptFailed"
    assert failed["details"]["phase"] == "decision"


def test_an_oversize_shelving_identity_does_not_decide_the_item_outcome() -> None:
    structured = run_recorded_tool(
        "alarm_shelve", _fixture("observed-user-over-budget")
    )["structuredContent"]

    assert structured["items"] == [{"path": EXACT, "status": "executed"}]
    assert structured["summary"]["executed"] == 1
    observed = structured["observed"][0]
    assert observed["status"] == "error"
    assert observed["error"]["code"] == "limit_exceeded"
    assert "400" in observed["error"]["message"]
    assert "256" in observed["error"]["message"]


def test_a_serialization_failure_still_reports_the_native_outcomes() -> None:
    structured = run_recorded_tool(
        "alarm_shelve", _fixture("serialization-fails")
    )["structuredContent"]

    assert structured["items"] == [{"path": EXACT, "status": "executed"}]
    assert structured["summary"]["executed"] == 1
    assert structured["observed"][0]["status"] == "error"
    assert structured["observed"][0]["error"]["code"] == "schema_mismatch"


def test_the_contract_declares_the_d10_input_bounds() -> None:
    contract = json.loads(
        (ROOT / "contracts/tools/runtime/alarm_shelve.contract.json").read_text(encoding="utf-8")
    )

    assert contract["inputBounds"]["defaultItems"] == 20
    assert contract["inputBounds"]["hardItems"] == 100
    assert contract["inputBounds"]["hardItemsPolicyField"] == "alarmMaxPaths"
    assert contract["inputBounds"]["maxInputBytes"] == 65536
    assert contract["inputBounds"]["overBudgetCode"] == "limit_exceeded"


VALID_POLICY_FIXTURES = (
    "alarm_shelve-allowlisted-batch",
    "alarm_shelve-observed-not-shelved",
    "alarm_shelve-dispatch-raises",
    "alarm_shelve-duration-over-policy-cap",
    "alarm_shelve-target-not-allowlisted",
    "alarm_shelve-reserved-provider",
    "alarm_shelve-reserved-name-in-later-segment",
    "alarm_shelve-reserved-name-not-allowlisted",
    "alarm_shelve-wildcard-allows-target",
    "alarm_shelve-preflight-refuses-whole-batch",
    "alarm_shelve-audit-required-profile-missing",
    "alarm_shelve-audit-required-attempt-fails",
    "alarm_shelve-audit-result-fails",
    "alarm_shelve-audit-off",
    "alarm_shelve-observed-read-fails",
    "alarm_shelve-observed-read-over-limit",
    # Shape-valid: the document is well formed and this Tool's own key is
    # grammar-checked, while the *entry* in it is what the refusal is about.
    "alarm_shelve-policy-length-mismatch",
    "alarm_shelve-policy-broken-allowlist-entry",
    # D10 bounds, the denial decision rows and the Observed-state budget.
    "alarm_shelve-paths-over-policy-limit",
    "alarm_shelve-policy-raises-path-limit",
    "alarm_shelve-decision-audit-off",
    "alarm_shelve-decision-audit-required-write-fails",
    "alarm_shelve-observed-user-over-budget",
    "alarm_shelve-serialization-fails",
)
#: Fixtures whose whole point is that the document does NOT satisfy the contract.
INVALID_POLICY_FIXTURES = (
    "alarm_shelve-policy-malformed",
    "alarm_shelve-policy-null-audit-profile",
    "alarm_shelve-policy-cap-invalid",
    # A policy may not raise the target ceiling above D10's 100-target hard cap.
    "alarm_shelve-policy-path-limit-invalid",
)


def _replayed_policies(name: str) -> list[dict]:
    """Every policy document a fixture replays into the handler."""
    document = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    policies = []
    for call in document["calls"]:
        for item in (call.get("result") or {}).get("items") or []:
            if not isinstance(item, dict):
                continue
            value = item.get("value")
            if isinstance(value, str) and value.startswith("{"):
                policies.append(json.loads(value))
    return policies


def test_recorded_policy_documents_satisfy_the_shipped_schema() -> None:
    contract = json.loads(
        (ROOT / "contracts/tools/runtime/alarm_shelve.contract.json").read_text(encoding="utf-8")
    )
    schema = json.loads(
        (ROOT / contract["runtimeTargetPolicy"]["documentSchema"]).read_text(encoding="utf-8")
    )
    validator = Draft202012Validator(schema)
    for name in VALID_POLICY_FIXTURES:
        policies = _replayed_policies(name)
        assert policies, name
        for policy in policies:
            validator.validate(policy)
    for name in INVALID_POLICY_FIXTURES:
        policies = _replayed_policies(name)
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
