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


VALID_POLICY_FIXTURES = (
    "tag_write-allowlisted-batch",
    "tag_write-native-outcome-indeterminate",
    "tag_write-native-outcome-count-mismatch",
    "tag_write-dispatch-raises",
    "tag_write-target-not-allowlisted",
    "tag_write-reserved-provider-under-wildcard",
    "tag_write-preflight-refuses-whole-batch",
    "tag_write-wildcard-allows-target",
    # The length-mismatch fixture replays a valid document with a wrong declared
    # length; the oversize fixture deliberately replays no document at all.
    "tag_write-policy-length-mismatch",
    "tag_write-audit-required-profile-missing",
    "tag_write-audit-required-attempt-fails",
    "tag_write-audit-result-fails",
    "tag_write-audit-off",
    "tag_write-observed-read-fails",
)
#: Fixtures whose whole point is that the document does NOT satisfy the contract.
INVALID_POLICY_FIXTURES = (
    "tag_write-policy-malformed",
    "tag_write-policy-null-audit-profile",
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
