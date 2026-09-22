"""D29 recorded-Jython coverage for `tag_write` (Phase 4 ticket #7).

Every fixture replays an ordered list of native calls, so a test can assert what
the handler did *not* do as well as what it returned: no write is dispatched by a
refused batch, the policy document is never read after an over-cap declared
length, and `off` mode makes no audit call at all.
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
    / "packages/ignition-runtime-bundle/project/com.inductiveautomation.mcp/tools/tag_write"
    / "onToolCalled.py"
)


def _fixture(name: str) -> Path:
    return FIXTURES / f"tag_write-{name}.json"


def _error(name: str, code: str) -> dict:
    return run_recorded_tool_error("tag_write", _fixture(name), expected_code=code)


def _recorded_targets(name: str) -> list[str]:
    """The ordered native calls a fixture replays (the negative half of a case)."""
    document = json.loads(_fixture(name).read_text(encoding="utf-8"))
    return [entry["target"] for entry in document["calls"]]


def _item_reasons(error: dict) -> list[tuple[str, str]]:
    return [(item.get("path"), item.get("reason")) for item in error["details"]["items"]]


def test_allowlisted_batch_reports_per_item_quality_and_observed_state() -> None:
    structured = run_recorded_tool("tag_write", _fixture("allowlisted-batch"))["structuredContent"]

    assert [(item["path"], item["status"], item["quality"]["name"]) for item in structured["items"]] == [
        ("[default]AHU/Setpoint", "executed", "Good"),
        ("[default]AHU/Missing", "executed", "Bad_NotFound"),
    ]
    assert structured["summary"] == {
        "requested": 2,
        "succeeded": 1,
        "failed": 1,
        "outcomeUnknown": 0,
        "auditMode": "best_effort",
        "auditRecorded": True,
    }
    # Observed state is read back and never decides success: the Bad_NotFound
    # item keeps its Native outcome while the read shows a null value.
    observed = structured["observed"]
    assert observed[0]["value"] == 22
    assert observed[0]["quality"]["good"] is True
    assert observed[1]["value"] == {"$ignition": "null"}
    assert observed[1]["status"] == "ok"
    assert observed[1]["quality"]["name"] == "Bad_NotFound"


def test_contract_schema_covers_the_structured_result() -> None:
    structured = run_recorded_tool("tag_write", _fixture("allowlisted-batch"))["structuredContent"]
    contract = json.loads(
        (ROOT / "contracts/tools/runtime/tag_write.contract.json").read_text(encoding="utf-8")
    )
    schema = json.loads((ROOT / contract["outputSchema"]).read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(structured)

    assert contract["permissionClass"] == "CONTROL"
    assert contract["mutationClass"] == "CONTROL_MUTATION"
    assert contract["destructive"] is False
    assert contract["parameters"]["writes"]["maxItems"] == 100
    assert contract["parameters"]["writes"]["required"] is True


def test_indeterminate_item_is_outcome_unknown_without_failing_the_batch() -> None:
    structured = run_recorded_tool(
        "tag_write", _fixture("native-outcome-indeterminate")
    )["structuredContent"]

    assert structured["items"][1] == {"path": "[default]AHU/Missing", "status": "outcome_unknown"}
    assert structured["summary"]["succeeded"] == 1
    assert structured["summary"]["outcomeUnknown"] == 1
    assert structured["summary"]["failed"] == 0


def test_unattributable_native_outcome_is_an_outcome_unknown_tool_error() -> None:
    error = _error("native-outcome-count-mismatch", "outcome_unknown")

    assert error["details"]["reason"] == "nativeOutcomeUnavailable"
    assert error["details"]["requested"] == 2
    assert error["details"]["returned"] == 1
    # The attempt and the indeterminate result are both audited.
    assert error["details"]["auditRecorded"] is True


def test_dispatch_failure_is_outcome_unknown_and_never_replayed() -> None:
    error = _error("dispatch-raises", "outcome_unknown")

    assert error["details"]["reason"] == "dispatchOutcomeUnknown"
    assert error["details"]["stage"] == "dispatch"


def test_target_outside_the_allowlist_is_refused_at_the_segment_boundary() -> None:
    # The policy allowlist entry is [default]AHU; [default]AHU2 is a different
    # segment, and the recorded call list proves no write was dispatched.
    error = _error("target-not-allowlisted", "permission_denied")

    assert error["details"]["reason"] == "preflightTargetRefused"
    assert error["details"]["allowlistKey"] == "tag_write"
    assert _item_reasons(error) == [("[default]AHU2/Setpoint", "targetNotAllowlisted")]


def test_reserved_policy_provider_is_refused_even_under_an_explicit_wildcard() -> None:
    error = _error("reserved-provider-under-wildcard", "permission_denied")

    assert _item_reasons(error) == [("[IgnitionMCPPolicy]WriteProbe", "reservedProvider")]
    # The same wildcard policy lets an ordinary provider through, so the refusal
    # is by provider rather than by the allowlist.
    allowed = run_recorded_tool("tag_write", _fixture("wildcard-allows-target"))["structuredContent"]
    assert allowed["items"][0]["status"] == "executed"
    assert allowed["summary"]["succeeded"] == 1


def test_one_bad_item_rejects_the_whole_batch_before_anything_executes() -> None:
    error = _error("preflight-refuses-whole-batch", "permission_denied")

    assert _item_reasons(error) == [("[default]AHU2/Setpoint", "targetNotAllowlisted")]


@pytest.mark.parametrize(
    ("name", "reason"),
    [
        ("policy-missing", "declaredLengthUnavailable"),
        ("policy-oversize", "declaredLengthOversize"),
        ("policy-length-mismatch", "policyLengthMismatch"),
        ("policy-malformed", "policyAuditMode"),
        # The document contract omits a null-valued property; an explicit null is
        # not "absent" and must fail closed like any other malformed field.
        ("policy-null-audit-profile", "policyAuditProfile"),
    ],
)
def test_an_unusable_policy_fails_closed_with_operation_disabled(name: str, reason: str) -> None:
    error = _error(name, "operation_disabled")

    assert error["details"]["reason"] == reason
    assert error["details"]["policyPath"] == "[IgnitionMCPPolicy]RuntimeTargetPolicy"


def test_an_over_cap_declared_length_never_materializes_the_document() -> None:
    # The fixture records only the length read; the runner fails the run if the
    # handler makes a call the fixture did not record (including reading the
    # policy Tag), so this asserts the bound rather than the message.
    error = _error("policy-oversize", "operation_disabled")
    assert error["details"]["reason"] == "declaredLengthOversize"


def test_document_array_and_item_shape_errors_are_all_reported_before_the_gateway() -> None:
    error = _error("invalid-values", "invalid_argument")

    assert error["details"]["reason"] == "preflightInputFailed"
    assert _item_reasons(error) == [
        ("[default]AHU/Setpoint", "documentValue"),
        ("[default]AHU/Other", "itemKeysMustBePathAndValue"),
        ("[default]AHU/Nested", "arrayValueElements"),
    ]


def test_required_audit_mode_checks_the_audit_profile_before_executing() -> None:
    error = _error("audit-required-profile-missing", "operation_disabled")

    assert error["details"]["reason"] == "auditProfileUnavailable"
    assert error["details"]["auditProfile"] == "MCP_CI_AUDIT"


def test_required_audit_mode_fails_closed_when_the_attempt_record_fails() -> None:
    error = _error("audit-required-attempt-fails", "operation_disabled")

    assert error["details"]["reason"] == "auditAttemptFailed"


def test_a_failed_result_audit_stays_a_success_with_audit_recorded_false() -> None:
    structured = run_recorded_tool("tag_write", _fixture("audit-result-fails"))["structuredContent"]

    assert structured["summary"]["succeeded"] == 1
    assert structured["summary"]["auditRecorded"] is False
    assert structured["summary"]["auditMode"] == "best_effort"


def test_audit_off_records_nothing() -> None:
    structured = run_recorded_tool("tag_write", _fixture("audit-off"))["structuredContent"]

    assert structured["summary"]["auditMode"] == "off"
    assert structured["summary"]["auditRecorded"] is False


def test_a_failed_observed_read_does_not_change_the_item_outcome() -> None:
    structured = run_recorded_tool("tag_write", _fixture("observed-read-fails"))["structuredContent"]

    assert structured["items"][0]["status"] == "executed"
    assert structured["items"][0]["quality"]["good"] is True
    assert structured["summary"]["succeeded"] == 1
    assert structured["observed"][0]["status"] == "error"
    assert structured["observed"][0]["error"]["code"] == "upstream_error"


def test_timeout_outside_the_contract_range_is_rejected_before_the_policy_read() -> None:
    error = _error("timeout-out-of-range", "invalid_argument")
    assert error["details"]["reason"] == "timeoutOutOfRange"


def test_hard_write_ceiling_is_enforced_before_the_policy_read() -> None:
    error = _error("over-hard-limit", "limit_exceeded")

    assert error["details"]["reason"] == "writesOverHardLimit"
    assert error["details"] == {
        "reason": "writesOverHardLimit",
        "requested": 101,
        "limit": 100,
    }


def test_the_batch_default_is_twenty_and_a_deployment_may_raise_it_within_the_cap() -> None:
    """D10: the project safe default is 20 writes, the deployment may raise it up
    to the 100-item hard ceiling through the Runtime Target Policy."""
    error = _error("over-policy-limit", "limit_exceeded")

    assert error["details"] == {
        "reason": "writesOverPolicyLimit",
        "requested": 21,
        "limit": 20,
    }
    # The same 21 writes run when the document raises the limit to 25, so the
    # refusal above is the deployment default and not a hidden hard cap.
    structured = run_recorded_tool(
        "tag_write", _fixture("policy-raises-write-limit")
    )["structuredContent"]
    assert structured["summary"]["requested"] == 21
    assert structured["summary"]["succeeded"] == 21


def test_a_policy_write_limit_outside_the_d10_hard_cap_fails_closed() -> None:
    error = _error("policy-write-limit-invalid", "operation_disabled")

    assert error["details"]["reason"] == "policyTagWriteMaxWrites"


def test_a_path_over_the_byte_ceiling_is_refused_before_any_native_call() -> None:
    error = _error("path-over-limit", "limit_exceeded")

    assert error["details"]["reason"] == "pathOverLength"
    assert error["details"]["index"] == 0
    # The path is 2053 bytes; the count stops one byte past the 2048-byte ceiling,
    # so the reported amount is a lower bound and the error says so.
    assert error["details"]["requested"] == 2049
    assert error["details"]["limit"] == 2048
    assert "lower bound" in error["message"]


def test_a_string_value_over_the_byte_ceiling_is_refused_before_any_native_call() -> None:
    error = _error("string-value-over-limit", "limit_exceeded")

    assert error["details"]["reason"] == "stringValueOverLimit"
    assert error["details"]["index"] == 0
    assert error["details"]["requested"] == 16385
    assert error["details"]["limit"] == 16384


def test_an_array_over_the_element_ceiling_is_refused_before_any_native_call() -> None:
    error = _error("array-elements-over-limit", "limit_exceeded")

    assert error["details"]["reason"] == "arrayElementsOverLimit"
    assert error["details"]["requested"] == 1001
    assert error["details"]["limit"] == 1000


def test_the_aggregate_input_byte_budget_is_finite() -> None:
    """Every per-item value is inside its own ceiling and the batch is still
    refused, so the aggregate budget is what bounds the request."""
    error = _error("input-over-byte-budget", "limit_exceeded")

    assert error["details"]["reason"] == "inputOverByteBudget"
    assert error["details"]["requested"] == 66075  # five 13200-byte values plus their paths
    assert error["details"]["limit"] == 65536


def test_a_denied_target_is_audited_as_a_decision_and_dispatches_no_write() -> None:
    """D08/D18: a denied mutation is audited. The ordered call list is the proof:
    one decision row after the policy read, and no `writeBlocking` at all."""
    assert _recorded_targets("decision-audit-on-denial") == [
        "system.tag.readBlocking",
        "system.tag.readBlocking",
        "system.util.audit",
    ]
    error = _error("decision-audit-on-denial", "permission_denied")

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
    recorded = _error("decision-audit-required", "permission_denied")
    assert recorded["details"]["auditRecorded"] is True

    # A required mode whose denial row cannot be written refuses the call rather
    # than reporting an unaudited denial.
    failed = _error("decision-audit-required-write-fails", "operation_disabled")
    assert failed["details"]["reason"] == "auditAttemptFailed"
    assert failed["details"]["phase"] == "decision"


def test_an_observed_value_over_its_budget_does_not_decide_the_item_outcome() -> None:
    structured = run_recorded_tool(
        "tag_write", _fixture("observed-value-over-budget")
    )["structuredContent"]

    assert structured["items"][0]["status"] == "executed"
    assert structured["summary"]["succeeded"] == 1
    observed = structured["observed"][0]
    assert observed["status"] == "error"
    assert observed["error"]["code"] == "limit_exceeded"
    # The value is 20000 bytes and the walk stops at the budget, so the message
    # reports what it counted rather than the size it would have had to encode.
    assert "at least 8193 bytes, over the 8192-byte Observed-state value budget" in observed["error"]["message"]


def test_the_observed_state_budget_marks_only_the_values_it_cannot_return() -> None:
    structured = run_recorded_tool(
        "tag_write", _fixture("observed-state-budget-exhausted")
    )["structuredContent"]

    assert [item["status"] for item in structured["items"]] == ["executed"] * 9
    assert structured["summary"]["succeeded"] == 9
    assert [entry["status"] for entry in structured["observed"]] == ["ok"] * 8 + ["error"]
    assert structured["observed"][8]["error"]["code"] == "limit_exceeded"


def test_a_serialization_failure_still_reports_the_native_outcomes() -> None:
    """The write completed and its outcome is known; failing to serialize the
    Observed state must not turn the batch into an `outcome_unknown`."""
    structured = run_recorded_tool(
        "tag_write", _fixture("serialization-fails")
    )["structuredContent"]

    assert structured["items"][0]["status"] == "executed"
    assert structured["summary"]["succeeded"] == 1
    assert structured["summary"]["auditRecorded"] is True
    assert structured["observed"][0]["status"] == "error"
    assert structured["observed"][0]["error"]["code"] == "schema_mismatch"
    assert "serializ" in structured["observed"][0]["error"]["message"]


def test_the_contract_declares_the_d10_input_bounds() -> None:
    contract = json.loads(
        (ROOT / "contracts/tools/runtime/tag_write.contract.json").read_text(encoding="utf-8")
    )

    assert contract["inputBounds"]["defaultItems"] == 20
    assert contract["inputBounds"]["hardItems"] == 100
    assert contract["inputBounds"]["hardItemsPolicyField"] == "tagWriteMaxWrites"
    assert contract["inputBounds"]["maxInputBytes"] == 65536
    assert contract["inputBounds"]["overBudgetCode"] == "limit_exceeded"


def test_a_dataset_observed_value_is_measured_before_it_is_materialized() -> None:
    """A one-cell Dataset can hold an arbitrarily large string, so the observed
    budget has to walk the cells instead of trusting the cell count: measuring a
    cell by reading it would defeat the budget it is there to enforce."""
    recorded = json.loads(_fixture("observed-dataset-over-budget").read_text(encoding="utf-8"))
    assert recorded["calls"][-1]["result"]["items"][0]["value"]["nativeType"] == "Dataset"

    structured = run_recorded_tool(
        "tag_write", _fixture("observed-dataset-over-budget")
    )["structuredContent"]

    assert structured["items"][0]["status"] == "executed"
    assert structured["summary"]["succeeded"] == 1
    observed = structured["observed"][0]
    assert observed["status"] == "error"
    assert observed["error"]["code"] == "limit_exceeded"
    # The cell holds 60000 bytes and the walk stops at the budget, so the message
    # reports what it counted rather than the size it would have had to encode to
    # learn: a measurement that needed the whole value would be the bug.
    message = observed["error"]["message"]
    assert "at least 8193 bytes, over the 8192-byte Observed-state value budget" in message
    assert "60000" not in message


def test_a_dataset_column_name_is_measured_with_its_cells() -> None:
    """`jsonValue` copies every column name, so a tiny cell under a very large name
    is exactly the Dataset a cell-only estimate lets through."""
    recorded = json.loads(
        _fixture("observed-dataset-column-name-over-budget").read_text(encoding="utf-8")
    )
    dataset = recorded["calls"][-1]["result"]["items"][0]["value"]
    assert len(dataset["columns"][0]) == 20000
    assert dataset["rows"] == [[1]]

    structured = run_recorded_tool(
        "tag_write", _fixture("observed-dataset-column-name-over-budget")
    )["structuredContent"]

    assert structured["items"][0]["status"] == "executed"
    assert structured["summary"]["succeeded"] == 1
    observed = structured["observed"][0]
    assert observed["status"] == "error"
    assert observed["error"]["code"] == "limit_exceeded"
    assert "over the 8192-byte Observed-state value budget" in observed["error"]["message"]


def test_a_deeply_nested_observed_value_is_refused_rather_than_raised() -> None:
    """The walk carries a depth limit, so a pathologically nested cell reaches the
    structured Observed budget error instead of exhausting the interpreter stack
    while it is measured and materialized."""
    recorded = json.loads(_fixture("observed-dataset-deep-cell").read_text(encoding="utf-8"))
    cell = recorded["calls"][-1]["result"]["items"][0]["value"]["rows"][0][0]
    depth = 0
    while isinstance(cell, list):
        depth += 1
        cell = cell[0]
    assert depth == 40

    structured = run_recorded_tool(
        "tag_write", _fixture("observed-dataset-deep-cell")
    )["structuredContent"]

    assert structured["items"][0]["status"] == "executed"
    observed = structured["observed"][0]
    assert observed["status"] == "error"
    assert observed["error"]["code"] == "limit_exceeded"
    assert "nests deeper than the 16-level Observed-state depth budget" in observed["error"]["message"]


def test_a_dataset_inside_the_budget_is_still_reported_as_observed_state() -> None:
    structured = run_recorded_tool(
        "tag_write", _fixture("observed-dataset-small")
    )["structuredContent"]

    assert structured["items"][0]["status"] == "executed"
    assert structured["observed"][0]["status"] == "ok"
    assert structured["observed"][0]["value"] == {
        "columns": ["Number", "Text"], "rows": [[1, "ok"], [2, "fine"]],
    }


def test_an_oversize_native_diagnostic_keeps_the_outcome_and_marks_the_limit() -> None:
    """The provider's free text has no bound of its own, so its representation is
    bounded before the item is built: code, name, level and good stay exact, the
    text is a bounded prefix, and the marker carries the size it had."""
    structured = run_recorded_tool(
        "tag_write", _fixture("native-outcome-oversize-diagnostic")
    )["structuredContent"]

    quality = structured["items"][0]["quality"]
    assert (quality["code"], quality["name"], quality["level"], quality["good"]) == (
        260, "Bad_NotFound", "Error", False,
    )
    assert quality["diagnosticMessageOverLimitBytes"] == 4000
    assert quality["diagnosticMessage"] == "d" * 512
    assert structured["summary"]["failed"] == 1


def test_an_over_limit_quality_name_is_omitted_not_truncated() -> None:
    """A truncated identifier asserts one the provider never reported (D10), so an
    over-limit name is omitted with its size instead, and the numeric code - which
    cannot be oversized - still identifies the outcome."""
    quality = run_recorded_tool(
        "tag_write", _fixture("native-outcome-oversize-name")
    )["structuredContent"]["items"][0]["quality"]

    assert quality["code"] == 260
    assert quality["good"] is False
    assert quality["name"] == {"$ignition": "null"}
    assert quality["nameOverLimitBytes"] == 200
    assert "nameOverLimitBytes" not in json.dumps(
        run_recorded_tool("tag_write", _fixture("allowlisted-batch"))["structuredContent"]
    )


def test_an_over_limit_quality_level_is_omitted_not_truncated() -> None:
    quality = run_recorded_tool(
        "tag_write", _fixture("native-outcome-oversize-level")
    )["structuredContent"]["items"][0]["quality"]

    assert quality["code"] == 260
    assert quality["level"] == {"$ignition": "null"}
    assert quality["levelOverLimitBytes"] == 200
    assert quality["name"] == "Bad_NotFound"


def test_long_diagnostics_cannot_hide_a_full_batch_of_outcomes() -> None:
    """100 Native outcomes each carrying a 1500-byte diagnostic: the bounded
    representation has to keep every QualityCode inside the 256 KiB ceiling, so
    the items alone can never become the reason a Tool Error is returned."""
    structured = run_recorded_tool(
        "tag_write", _fixture("items-with-long-diagnostics")
    )["structuredContent"]

    assert len(structured["items"]) == 100
    assert structured["summary"] == {
        "requested": 100,
        "succeeded": 0,
        "failed": 100,
        "outcomeUnknown": 0,
        "auditMode": "best_effort",
        "auditRecorded": True,
    }
    assert all(item["quality"]["name"] == "Bad_NotFound" for item in structured["items"])
    assert {item["quality"]["diagnosticMessageOverLimitBytes"] for item in structured["items"]} == {1500}
    assert all(item["quality"]["diagnosticMessage"] == "e" * 512 for item in structured["items"])


VALID_POLICY_FIXTURES = (
    "tag_write-allowlisted-batch",
    "tag_write-native-outcome-indeterminate",
    "tag_write-native-outcome-count-mismatch",
    "tag_write-dispatch-raises",
    "tag_write-target-not-allowlisted",
    "tag_write-reserved-provider-under-wildcard",
    "tag_write-preflight-refuses-whole-batch",
    "tag_write-wildcard-allows-target",
    "tag_write-mixed-tool-allowlists",
    # Shape-valid: only the Tool's own key is held to its entry grammar.
    "tag_write-policy-broken-allowlist-entry",
    # The length-mismatch fixture replays a valid document with a wrong declared
    # length; the oversize fixture deliberately replays no document at all.
    "tag_write-policy-length-mismatch",
    "tag_write-audit-required-profile-missing",
    "tag_write-audit-required-attempt-fails",
    "tag_write-audit-result-fails",
    "tag_write-audit-off",
    "tag_write-observed-read-fails",
    # D10 bounds, the denial decision rows and the Observed-state budget.
    "tag_write-over-policy-limit",
    "tag_write-policy-raises-write-limit",
    "tag_write-decision-audit-on-denial",
    "tag_write-decision-audit-off",
    "tag_write-decision-audit-required",
    "tag_write-decision-audit-required-write-fails",
    "tag_write-observed-value-over-budget",
    "tag_write-observed-state-budget-exhausted",
    "tag_write-serialization-fails",
    # Round 2: Dataset Observed values and an unbounded QualityCode diagnostic.
    "tag_write-observed-dataset-over-budget",
    "tag_write-observed-dataset-small",
    "tag_write-native-outcome-oversize-diagnostic",
    "tag_write-items-with-long-diagnostics",
    # Round 3: column names, bounded counting, depth, and the identifiers.
    "tag_write-observed-dataset-column-name-over-budget",
    "tag_write-observed-dataset-deep-cell",
    "tag_write-native-outcome-oversize-name",
    "tag_write-native-outcome-oversize-level",
)
#: Fixtures whose whole point is that the document does NOT satisfy the contract.
INVALID_POLICY_FIXTURES = (
    "tag_write-policy-malformed",
    "tag_write-policy-null-audit-profile",
    # A policy may not raise the write ceiling above D10's 100-item hard cap.
    "tag_write-policy-write-limit-invalid",
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


def test_another_tools_allowlist_grammar_does_not_disable_this_tool() -> None:
    """The deployment document carries every Tool's allowlist. An Alarm source
    pattern is not a Tag path, so tag_write must validate its own key strictly and
    leave the others to the Tools that read them — the live run refused the
    ticket #6 document shape until this was fixed."""
    structured = run_recorded_tool("tag_write", _fixture("mixed-tool-allowlists"))["structuredContent"]

    assert structured["summary"]["succeeded"] == 1
    assert structured["items"][0]["quality"]["good"] is True


def test_this_tools_own_allowlist_entry_must_be_a_tag_path() -> None:
    error = _error("policy-broken-allowlist-entry", "operation_disabled")

    assert error["details"]["reason"] == "policyAllowlists"


def test_recorded_policy_documents_satisfy_the_shipped_schema() -> None:
    """The policy text a fixture replays must satisfy the contract schema the
    shipped reader implements, and the fixtures that exist to be refused must not."""
    contract = json.loads(
        (ROOT / "contracts/tools/runtime/tag_write.contract.json").read_text(encoding="utf-8")
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
