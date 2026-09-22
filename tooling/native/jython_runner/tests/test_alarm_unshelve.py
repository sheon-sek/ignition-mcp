"""D29 recorded-Jython coverage for `alarm_unshelve` (Phase 4 ticket #8).

`alarm_unshelve` is its own Tool (D12): a shelve duration of zero is never a
caller-visible unshelve, and an unshelve takes exact Alarm paths with no
wildcard and no duration.
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
    / "packages/ignition-runtime-bundle/project/com.inductiveautomation.mcp/tools/alarm_unshelve"
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
    return FIXTURES / f"alarm_unshelve-{name}.json"


def _error(name: str, code: str) -> dict:
    return run_recorded_tool_error("alarm_unshelve", _fixture(name), expected_code=code)


def _item_reasons(error: dict) -> list[tuple[str, str]]:
    return [(item.get("path"), item.get("reason")) for item in error["details"]["items"]]


def _recorded_targets(name: str) -> list[str]:
    """The ordered native calls a fixture replays (the negative half of a case)."""
    document = json.loads(_fixture(name).read_text(encoding="utf-8"))
    return [entry["target"] for entry in document["calls"]]


def test_allowlisted_unshelve_reports_executed_items_and_the_cleared_state() -> None:
    structured = run_recorded_tool(
        "alarm_unshelve", _fixture("allowlisted")
    )["structuredContent"]

    assert structured["items"] == [
        {"path": EXACT, "status": "executed"},
        {"path": NESTED, "status": "executed"},
    ]
    assert structured["summary"] == {
        "requested": 2,
        "executed": 2,
        "outcomeUnknown": 0,
        "auditMode": "best_effort",
        "auditRecorded": True,
    }
    assert structured["observed"] == [
        {"path": EXACT, "status": "ok", "shelved": False},
        {"path": NESTED, "status": "ok", "shelved": False},
    ]


def test_contract_schema_covers_the_structured_result() -> None:
    structured = run_recorded_tool(
        "alarm_unshelve", _fixture("allowlisted")
    )["structuredContent"]
    contract = json.loads(
        (ROOT / "contracts/tools/runtime/alarm_unshelve.contract.json").read_text(encoding="utf-8")
    )
    schema = json.loads((ROOT / contract["outputSchema"]).read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(structured)

    assert contract["permissionClass"] == "CONTROL"
    assert contract["mutationClass"] == "CONTROL_MUTATION"
    assert contract["destructive"] is False
    assert contract["parameters"]["paths"]["maxItems"] == 100
    assert "timeoutSeconds" not in contract["parameters"]
    assert "never a caller-visible unshelve" in contract["noOverloadedTimeout"]


def test_indeterminate_item_is_outcome_unknown_and_the_state_still_shows_shelved() -> None:
    structured = run_recorded_tool(
        "alarm_unshelve", _fixture("dispatch-raises")
    )["structuredContent"]

    assert structured["items"] == [{"path": EXACT, "status": "outcome_unknown"}]
    assert structured["summary"]["outcomeUnknown"] == 1
    assert structured["summary"]["executed"] == 0
    assert structured["observed"][0]["shelved"] is True


def test_wildcard_targets_are_refused_before_any_native_call() -> None:
    error = _error("wildcard-path", "invalid_argument")

    assert error["details"]["reason"] == "preflightInputFailed"
    assert _item_reasons(error) == [(WILDCARD_PATH, "wildcardPath")]


def test_target_outside_the_allowlist_is_refused_at_the_segment_boundary() -> None:
    error = _error("target-not-allowlisted", "permission_denied")

    assert error["details"]["reason"] == "preflightTargetRefused"
    assert error["details"]["allowlistKey"] == "alarm_unshelve"
    assert _item_reasons(error) == [(SIBLING, "targetNotAllowlisted")]


def test_reserved_policy_provider_is_refused_under_an_explicit_wildcard() -> None:
    error = _error("reserved-provider", "permission_denied")

    assert _item_reasons(error) == [(RESERVED_PATH, "reservedProvider")]


def test_a_later_segment_that_spells_the_reserved_name_is_not_the_reserved_provider() -> None:
    """D30's owner ruling matches the provider component only, so a target under an
    allowed provider is never refused for a Tag or Alarm segment that spells the
    reserved name (the review's `IgnitionMCPPolicyPump` sibling)."""
    structured = run_recorded_tool(
        "alarm_unshelve", _fixture("reserved-name-in-later-segment")
    )["structuredContent"]

    assert structured["items"] == [
        {"path": RESERVED_NAME_PATH, "status": "executed"},
        {"path": RESERVED_SEGMENT_PATH, "status": "executed"},
    ]
    assert structured["summary"]["executed"] == 2


def test_a_reserved_name_outside_the_allowlist_is_refused_as_unallowlisted() -> None:
    """The refusals stay distinct: a target outside the allowlist answers for that
    reason, even when a later segment spells the reserved name, so a refusal is
    never misattributed to the provider."""
    error = _error("reserved-name-not-allowlisted", "permission_denied")

    assert _item_reasons(error) == [(RESERVED_NAME_PATH, "targetNotAllowlisted")]


def test_one_bad_item_rejects_the_whole_batch_before_anything_executes() -> None:
    error = _error("preflight-refuses-whole-batch", "permission_denied")

    assert _item_reasons(error) == [(SIBLING, "targetNotAllowlisted")]


def test_an_unusable_policy_fails_closed_with_operation_disabled() -> None:
    missing = _error("policy-missing", "operation_disabled")
    assert missing["details"]["reason"] == "declaredLengthUnavailable"

    broken = _error("policy-broken-allowlist-entry", "operation_disabled")
    assert broken["details"]["reason"] == "policyAllowlists"


def test_required_audit_mode_checks_the_audit_profile_before_executing() -> None:
    error = _error("audit-required-profile-missing", "operation_disabled")

    assert error["details"]["reason"] == "auditProfileUnavailable"


def test_audit_off_records_nothing() -> None:
    structured = run_recorded_tool("alarm_unshelve", _fixture("audit-off"))["structuredContent"]

    assert structured["summary"]["auditMode"] == "off"
    assert structured["summary"]["auditRecorded"] is False


def test_a_failed_observed_read_does_not_change_the_item_outcome() -> None:
    structured = run_recorded_tool(
        "alarm_unshelve", _fixture("observed-read-fails")
    )["structuredContent"]

    assert structured["items"] == [{"path": EXACT, "status": "executed"}]
    assert structured["observed"][0]["status"] == "error"
    assert structured["observed"][0]["error"]["code"] == "upstream_error"


def test_the_target_default_is_twenty_and_the_input_budget_is_finite() -> None:
    """D10 applies to both Alarm Mutations: a 20-target default inside the Policy,
    and a finite aggregate byte budget on the request."""
    over_default = _error("paths-over-policy-limit", "limit_exceeded")
    assert over_default["details"] == {
        "reason": "pathsOverPolicyLimit",
        "requested": 21,
        "limit": 20,
    }
    over_budget = _error("input-over-byte-budget", "limit_exceeded")
    assert over_budget["details"]["reason"] == "inputOverByteBudget"
    assert over_budget["details"]["requested"] == 80080
    assert over_budget["details"]["limit"] == 65536


def test_a_denied_target_is_audited_as_a_decision_and_unshelves_nothing() -> None:
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
        "alarm_unshelve", _fixture("observed-user-over-budget")
    )["structuredContent"]

    assert structured["items"] == [{"path": EXACT, "status": "executed"}]
    assert structured["summary"]["executed"] == 1
    observed = structured["observed"][0]
    assert observed["status"] == "error"
    assert observed["error"]["code"] == "limit_exceeded"


def test_a_serialization_failure_still_reports_the_native_outcomes() -> None:
    structured = run_recorded_tool(
        "alarm_unshelve", _fixture("serialization-fails")
    )["structuredContent"]

    assert structured["items"] == [{"path": EXACT, "status": "executed"}]
    assert structured["summary"]["executed"] == 1
    assert structured["observed"][0]["status"] == "error"
    assert structured["observed"][0]["error"]["code"] == "schema_mismatch"


def test_the_contract_declares_the_d10_input_bounds() -> None:
    contract = json.loads(
        (ROOT / "contracts/tools/runtime/alarm_unshelve.contract.json").read_text(encoding="utf-8")
    )

    assert contract["inputBounds"]["defaultItems"] == 20
    assert contract["inputBounds"]["hardItems"] == 100
    assert contract["inputBounds"]["hardItemsPolicyField"] == "alarmMaxPaths"
    assert contract["inputBounds"]["overBudgetCode"] == "limit_exceeded"


VALID_POLICY_FIXTURES = (
    "alarm_unshelve-allowlisted",
    "alarm_unshelve-dispatch-raises",
    "alarm_unshelve-target-not-allowlisted",
    "alarm_unshelve-reserved-provider",
    "alarm_unshelve-reserved-name-in-later-segment",
    "alarm_unshelve-reserved-name-not-allowlisted",
    "alarm_unshelve-preflight-refuses-whole-batch",
    "alarm_unshelve-audit-required-profile-missing",
    "alarm_unshelve-audit-off",
    "alarm_unshelve-observed-read-fails",
    # Shape-valid: only this Tool's own key is held to its entry grammar, and the
    # entry itself is what this Tool refuses.
    "alarm_unshelve-policy-broken-allowlist-entry",
    # D10 bounds, the denial decision rows and the Observed-state budget.
    "alarm_unshelve-paths-over-policy-limit",
    "alarm_unshelve-decision-audit-off",
    "alarm_unshelve-decision-audit-required-write-fails",
    "alarm_unshelve-observed-user-over-budget",
    "alarm_unshelve-serialization-fails",
)
INVALID_POLICY_FIXTURES = ()


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
        (ROOT / "contracts/tools/runtime/alarm_unshelve.contract.json").read_text(encoding="utf-8")
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
