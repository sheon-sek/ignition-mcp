"""D29 recorded-Jython coverage for `tag_copy` (Phase 4 ticket #11).

D30 §6 checks the destination of a copy, the source has to be readable, and D30 §2
gives the Tool no Precondition token — so an occupied destination is `conflict` and
the fixtures record that nothing was dispatched after the check. The reserved policy
provider is refused at *both* ends (owner ruling 1), which is the one rule that
reaches the source path, and a destination whose leaf differs from the source's is
refused before any native call because one `system.tag.copy` call copies a path list
into one destination folder under each source's own name.
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
    / "packages/ignition-runtime-bundle/project/com.inductiveautomation.mcp/tools/tag_copy"
    / "onToolCalled.py"
)
CONTRACT = ROOT / "contracts/tools/runtime/tag_copy.contract.json"

SOURCE = "[default]IgnitionMCP_CI/WriteTarget"
TEXT_SOURCE = "[default]IgnitionMCP_CI/TextTarget"
COPY = "[default]IgnitionMCP_CI/Copied/WriteTarget"
COPY2 = "[default]IgnitionMCP_CI/Copied/TextTarget"
SIBLING_COPY = "[default]IgnitionMCP_CI2/Copied/WriteTarget"
UDT = "[default]_types_/IgnitionMCP_CI/ProbeType"
UDT_COPY = "[default]_types_/Phase4Probe/ProbeType"
RESERVED = "[IgnitionMCPPolicy]WriteProbe"


def _fixture(name: str) -> Path:
    return FIXTURES / f"tag_copy-{name}.json"


def _error(name: str, code: str) -> dict:
    return run_recorded_tool_error("tag_copy", _fixture(name), expected_code=code)


def _structured(name: str) -> dict:
    return run_recorded_tool("tag_copy", _fixture(name))["structuredContent"]


def _problem_reasons(error: dict) -> list[tuple[str, str]]:
    return [(item.get("path"), item.get("reason")) for item in error["details"]["items"]]


def _statuses(structured: dict) -> list[tuple[str, str, str]]:
    return [(item["sourcePath"], item["destinationPath"], item["status"]) for item in structured["items"]]


def _recorded_targets(name: str) -> list[str]:
    document = json.loads(_fixture(name).read_text(encoding="utf-8"))
    return [entry["target"] for entry in document["calls"]]


def test_a_readable_source_is_copied_and_the_destination_is_re_read() -> None:
    structured = _structured("allowlisted")

    assert _statuses(structured) == [(SOURCE, COPY, "executed")]
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
    assert observed["path"] == COPY
    assert observed["configuration"][0]["name"] == "WriteTarget"
    assert observed["fingerprint"].startswith("tcf1:")
    assert len(observed["fingerprint"]) == 69


def test_the_native_call_copies_into_the_destination_parent_with_abort() -> None:
    """One `system.tag.copy` per item: the source as a one-element path list, the
    destination's parent folder as the native destination, and the fixed Abort
    collision policy that keeps the copy from overwriting anything."""
    document = json.loads(_fixture("allowlisted").read_text(encoding="utf-8"))
    copies = [call for call in document["calls"] if call["target"] == "system.tag.copy"]

    assert copies == [{"target": "system.tag.copy",
                       "args": [[SOURCE], "[default]IgnitionMCP_CI/Copied", "Abort"],
                       "result": {"kind": "quality-codes", "items": [{
                           "code": 192, "name": "Good", "level": "Good", "good": True,
                           "diagnosticMessage": None}]}}]


def test_the_structured_result_satisfies_the_committed_output_schema() -> None:
    structured = _structured("allowlisted")
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    schema = json.loads((ROOT / contract["outputSchema"]).read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(structured)

    assert contract["permissionClass"] == "CONFIG"
    assert contract["mutationClass"] == "CONFIG_MUTATION"
    assert contract["destructive"] is False
    assert contract["parameters"]["items"]["itemRequired"] == ["sourcePath", "destinationPath"]
    assert contract["preconditionToken"]["kind"] == "none"
    assert contract["collisionPolicy"] == "Abort"
    assert contract["inputBounds"]["hardItemsPolicyField"] == "tagCopyMaxItems"


def test_the_dispatch_never_precedes_the_endpoint_checks() -> None:
    """D30 §3: every item's source, source read and destination are checked before
    the first item executes."""
    targets = _recorded_targets("batch-with-a-bad-native-outcome")
    first_copy = targets.index("system.tag.copy")
    preflight = targets[:first_copy]

    # Two items, each with a source existence check, a source read and a
    # destination existence check, and the attempt row last.
    assert preflight.count("system.tag.exists") == 4
    assert preflight.count("system.tag.getConfiguration") == 2
    assert preflight[-1] == "system.util.audit"


def test_a_folder_source_copies_like_a_tag() -> None:
    structured = _structured("folder-source")

    assert _statuses(structured) == [
        ("[default]IgnitionMCP_CI/Nested", "[default]IgnitionMCP_CI/NestedCopy/Nested", "executed")
    ]


def test_an_occupied_destination_is_conflict_and_nothing_is_dispatched() -> None:
    error = _error("destination-exists", "conflict")

    assert error["details"]["reason"] == "preflightPreconditionFailed"
    assert _problem_reasons(error) == [(COPY, "destinationExists")]
    assert error["details"]["items"][0]["sourcePath"] == SOURCE
    assert error["details"]["auditRecorded"] is True
    # The decision row is the only audit call, and it follows the endpoint checks.
    assert _recorded_targets("destination-exists") == [
        "system.tag.readBlocking",
        "system.tag.readBlocking",
        "system.tag.exists",
        "system.tag.getConfiguration",
        "system.tag.exists",
        "system.util.audit",
    ]


def test_a_source_that_is_not_there_is_not_found() -> None:
    error = _error("source-missing", "not_found")

    assert _problem_reasons(error) == [(SOURCE, "sourceMissing")]
    assert _problem_reasons(error) == [(SOURCE, "sourceMissing")]
    assert "system.tag.copy" not in _recorded_targets("source-missing")


@pytest.mark.parametrize(
    ("name", "reason"),
    [("source-read-fails", "sourceReadFailed"),
     ("source-configuration-unavailable", "sourceConfigurationUnavailable")],
)
def test_a_source_that_cannot_be_read_is_upstream_error(name: str, reason: str) -> None:
    """D30 §6: the source must be readable, and a read that raises or answers
    nothing is never treated as "there is something to copy"."""
    error = _error(name, "upstream_error")

    assert _problem_reasons(error) == [(SOURCE, reason)]
    assert "system.tag.copy" not in _recorded_targets(name)


@pytest.mark.parametrize(
    ("name", "path", "reason"),
    [
        ("source-existence-check-fails", SOURCE, "existenceCheckFailed"),
        ("source-existence-check-indeterminate", SOURCE, "existenceCheckIndeterminate"),
        ("destination-existence-check-fails", COPY, "existenceCheckFailed"),
        ("destination-existence-check-indeterminate", COPY, "existenceCheckIndeterminate"),
    ],
)
def test_an_unanswered_existence_check_is_never_read_as_present_or_absent(
    name: str, path: str, reason: str
) -> None:
    error = _error(name, "upstream_error")

    assert _problem_reasons(error) == [(path, reason)]


def test_a_destination_outside_the_allowlist_is_refused_at_the_segment_boundary() -> None:
    error = _error("destination-not-allowlisted", "permission_denied")

    assert error["details"]["reason"] == "preflightTargetRefused"
    assert _problem_reasons(error) == [(SIBLING_COPY, "targetNotAllowlisted")]


def test_one_refused_destination_rejects_the_whole_batch() -> None:
    error = _error("preflight-refuses-whole-batch-on-destination", "permission_denied")

    assert _problem_reasons(error) == [(SIBLING_COPY, "targetNotAllowlisted")]


def test_the_reserved_provider_is_refused_at_the_destination_and_at_the_source() -> None:
    """Owner ruling 1: a copy into the policy provider would create a node there,
    and a copy out of it would publish the policy document elsewhere, so both ends
    are refused before any read — even under an explicit *."""
    destination = _error("reserved-provider-destination-under-wildcard", "permission_denied")
    source = _error("reserved-provider-source-under-wildcard", "permission_denied")

    assert _problem_reasons(destination) == [(RESERVED, "reservedProvider")]
    assert _problem_reasons(source) == [(RESERVED, "reservedProvider")]
    assert _recorded_targets("reserved-provider-destination-under-wildcard") == [
        "system.tag.readBlocking",
        "system.tag.readBlocking",
        "system.util.audit",
    ]


def test_a_udt_definition_destination_needs_an_explicit_types_entry() -> None:
    under_wildcard = _error("udt-definition-under-wildcard", "permission_denied")
    under_prefix = _error("udt-definition-under-plain-prefix", "permission_denied")

    assert _problem_reasons(under_wildcard) == [(UDT_COPY, "udtDefinitionNotAllowlisted")]
    assert _problem_reasons(under_prefix) == [(UDT_COPY, "udtDefinitionNotAllowlisted")]


def test_an_explicit_types_entry_copies_a_udt_definition() -> None:
    structured = _structured("udt-definition-with-explicit-types-entry")

    assert _statuses(structured) == [(UDT, UDT_COPY, "executed")]


def test_a_destination_whose_leaf_differs_from_the_source_is_invalid_argument() -> None:
    """One `system.tag.copy` call copies a path list into one destination folder
    under each source's own name, so a renaming copy cannot land where the caller
    named and is refused before any native call."""
    error = _error("invalid-items", "invalid_argument")

    assert error["details"]["reason"] == "preflightInputFailed"
    assert [item["reason"] for item in error["details"]["items"]] == [
        "itemNotAnObject",
        "itemKeysMustBeSourceAndDestinationPath",
        "sourcePathNotAConfigPath",
        "destinationPathNotAConfigPath",
        "destinationPathNotAConfigPath",
        "destinationLeafDiffersFromSource",
    ]
    assert _recorded_targets("invalid-items") == []


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


def test_a_policy_without_this_tools_key_allows_no_destination() -> None:
    error = _error("policy-without-this-tools-allowlist", "permission_denied")

    assert _problem_reasons(error) == [(COPY, "targetNotAllowlisted")]


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


def test_audit_off_records_nothing() -> None:
    structured = _structured("audit-off")

    assert structured["summary"]["auditMode"] == "off"
    assert structured["summary"]["auditRecorded"] is False
    assert _recorded_targets("audit-off").count("system.util.audit") == 0


def test_a_denied_destination_is_audited_as_a_decision_and_copies_nothing() -> None:
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
        "system.tag.getConfiguration",
        "system.tag.exists",
    ]
    error = _error("decision-audit-off", "conflict")

    assert error["details"]["auditRecorded"] is False


def test_required_mode_gates_the_decision_row_on_the_audit_profile() -> None:
    recorded = _error("decision-audit-required", "conflict")
    assert recorded["details"]["auditRecorded"] is True

    failed = _error("decision-audit-required-write-fails", "operation_disabled")
    assert failed["details"]["reason"] == "auditAttemptFailed"
    assert failed["details"]["phase"] == "decision"


def test_a_dispatch_that_raises_ends_the_batch_without_replaying_it() -> None:
    structured = _structured("dispatch-raises")

    assert _statuses(structured) == [
        (SOURCE, COPY, "executed"),
        (TEXT_SOURCE, COPY2, "outcome_unknown"),
        ("[default]IgnitionMCP_CI/Nested", "[default]IgnitionMCP_CI/Copied/Nested", "not_executed"),
    ]
    assert structured["summary"]["outcomeUnknown"] == 1
    assert structured["summary"]["notExecuted"] == 1


def test_an_indeterminate_native_outcome_does_not_stop_the_batch() -> None:
    structured = _structured("native-outcome-indeterminate")

    assert _statuses(structured) == [(SOURCE, COPY, "outcome_unknown"), (TEXT_SOURCE, COPY2, "executed")]
    assert structured["summary"]["notExecuted"] == 0


@pytest.mark.parametrize("name", ["observed-read-fails", "observed-read-empty"])
def test_a_failed_observed_read_does_not_change_the_item_outcome(name: str) -> None:
    structured = _structured(name)

    assert structured["items"][0]["status"] == "executed"
    assert structured["observed"][0]["status"] == "error"
    assert structured["observed"][0]["error"]["code"] == "upstream_error"


def test_a_serialization_failure_still_reports_the_native_outcomes() -> None:
    structured = _structured("serialization-fails")

    assert structured["items"][0]["status"] == "executed"
    assert structured["observed"][0]["error"]["code"] == "schema_mismatch"
    assert "serializ" in structured["observed"][0]["error"]["message"]


def test_an_observed_configuration_over_its_budget_does_not_decide_the_item_outcome() -> None:
    structured = _structured("observed-configuration-over-budget")

    assert structured["items"][0]["status"] == "executed"
    assert structured["observed"][0]["error"]["code"] == "limit_exceeded"
    assert "16384" in structured["observed"][0]["error"]["message"]


def test_the_observed_state_budget_marks_only_the_configurations_it_cannot_return() -> None:
    structured = _structured("observed-state-budget-exhausted")

    assert [item["status"] for item in structured["items"]] == ["executed"] * 9
    # Each recorded configuration measures ~11.2 KiB against the 64 KiB Observed-state
    # budget, so five fit (they reach 56215 bytes) and the sixth is where it is spent.
    statuses = [entry["status"] for entry in structured["observed"]]
    assert statuses[:5] == ["ok"] * 5
    assert statuses[5:] == ["error"] * 4


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
    error = _error(name, code)

    assert error["details"]["reason"] == reason
    assert _recorded_targets(name) == []


def test_the_item_hard_ceiling_reports_what_it_refused() -> None:
    error = _error("items-over-hard-limit", "limit_exceeded")

    assert error["details"] == {"reason": "itemsOverHardLimit", "requested": 101, "limit": 100}


@pytest.mark.parametrize("name", ["path-over-limit", "destination-path-over-limit"])
def test_a_path_over_the_byte_ceiling_is_refused_before_any_native_call(name: str) -> None:
    error = _error(name, "limit_exceeded")

    assert error["details"]["reason"] == "pathOverLength"
    assert error["details"]["limit"] == 2048
    assert error["details"]["requested"] > 2048
    assert _recorded_targets(name) == []


def test_the_aggregate_input_byte_budget_is_finite() -> None:
    error = _error("input-over-byte-budget", "limit_exceeded")

    assert error["details"]["reason"] == "inputOverByteBudget"
    assert error["details"]["limit"] == 65536
    assert error["details"]["requested"] > error["details"]["limit"]


def test_the_batch_default_is_twenty_and_a_deployment_may_raise_it_within_the_cap() -> None:
    error = _error("over-policy-limit", "limit_exceeded")

    assert error["details"] == {"reason": "itemsOverPolicyLimit", "requested": 21, "limit": 20}
    structured = _structured("policy-raises-item-limit")
    assert structured["summary"]["requested"] == 21
    assert structured["summary"]["succeeded"] == 21


def test_a_policy_item_limit_outside_the_d10_hard_cap_fails_closed() -> None:
    error = _error("policy-item-limit-invalid", "operation_disabled")

    assert error["details"]["reason"] == "policyTagCopyMaxItems"


RECORDED_FIXTURES = sorted(path.name for path in FIXTURES.glob("tag_copy-*.json"))


def test_every_recorded_fixture_is_exercised_by_this_module() -> None:
    source = Path(__file__).read_text(encoding="utf-8")
    for name in RECORDED_FIXTURES:
        case = name[len("tag_copy-") : -len(".json")]
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
        "folder-source",
        "batch-with-a-bad-native-outcome",
        "destination-exists",
        "source-missing",
        "source-read-fails",
        "source-configuration-unavailable",
        "destination-not-allowlisted",
        "preflight-refuses-whole-batch-on-destination",
        "reserved-provider-destination-under-wildcard",
        "reserved-provider-source-under-wildcard",
        "udt-definition-under-wildcard",
        "udt-definition-under-plain-prefix",
        "udt-definition-with-explicit-types-entry",
        "audit-off",
        "audit-required-profile-missing",
        "decision-audit-on-denial",
        "policy-without-this-tools-allowlist",
        "policy-raises-item-limit",
    ]
    for name in valid:
        policies = replayed_policies(name)
        assert policies, name
        for policy in policies:
            validator.validate(policy)
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
