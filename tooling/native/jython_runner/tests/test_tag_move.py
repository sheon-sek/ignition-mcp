"""D29 recorded-Jython coverage for `tag_move` (Phase 4 ticket #12).

Every fixture replays the ordered native call list, so a test can assert what
the handler did *not* do as well as what it returned: a refused Preflight
dispatches no `system.tag.move` at all, an occupied destination is conflict
before anything moves, and the Observed state is the destination present with a
fresh fingerprint plus the source gone.
"""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from tooling.native.jython_runner import run_recorded_tool, run_recorded_tool_error

ROOT = Path(__file__).resolve().parents[4]
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
CONTRACT = ROOT / "contracts/tools/runtime/tag_move.contract.json"

WRITE = "[default]IgnitionMCP_CI/WriteTarget"
TEXT = "[default]IgnitionMCP_CI/TextTarget"
NESTED_WRITE = "[default]IgnitionMCP_CI/Nested/WriteTarget"
NESTED_PARENT = "[default]IgnitionMCP_CI/Nested"
NESTED_TEXT = "[default]IgnitionMCP_CI/Nested/TextTarget"
SIBLING = "[default]IgnitionMCP_CI2/WriteTarget"
SIBLING_TEXT = "[default]IgnitionMCP_CI2/TextTarget"
UDT = "[default]_types_/IgnitionMCP_CI/ProbeType"
UDT_NESTED = "[default]_types_/IgnitionMCP_CI/Nested/ProbeType"
UDT_SIBLING = "[default]_types_/IgnitionMCP_CI2/ProbeType"
RESERVED = "[IgnitionMCPPolicy]WriteProbe"
RESERVED_MOVED = "[default]IgnitionMCP_CI/WriteProbe"
BOTH = ",".join([NESTED_WRITE, WRITE])
ZERO_FP = "tcf1:" + "0" * 64
EXECUTED_OUTCOME = {
    "code": 192,
    "name": "Good",
    "level": "Good",
    "good": True,
    "diagnosticMessage": {"$ignition": "null"},
}


def _fixture(name: str) -> Path:
    return FIXTURES / f"tag_move-{name}.json"


def _error(name: str, code: str) -> dict:
    return run_recorded_tool_error("tag_move", _fixture(name), expected_code=code)


def _structured(name: str) -> dict:
    return run_recorded_tool("tag_move", _fixture(name))["structuredContent"]


def _problem_reasons(error: dict) -> list[tuple[str, str, str]]:
    return [
        (item.get("sourcePath"), item.get("destinationPath"), item.get("reason"))
        for item in error["details"]["items"]
    ]


def _statuses(structured: dict) -> list[tuple[str, str, str]]:
    return [
        (item["sourcePath"], item["destinationPath"], item["status"])
        for item in structured["items"]
    ]


def _recorded(name: str) -> list[dict]:
    return json.loads(_fixture(name).read_text(encoding="utf-8"))["calls"]


def _recorded_targets(name: str) -> list[str]:
    return [entry["target"] for entry in _recorded(name)]


def test_an_allowlisted_move_reports_the_native_outcome_and_both_endpoints() -> None:
    structured = _structured("allowlisted")

    assert _statuses(structured) == [(WRITE, NESTED_WRITE, "executed")]
    assert structured["items"][0]["nativeOutcome"] == EXECUTED_OUTCOME
    assert structured["summary"] == {
        "requested": 1,
        "succeeded": 1,
        "failed": 0,
        "outcomeUnknown": 0,
        "notExecuted": 0,
        "auditMode": "best_effort",
        "auditRecorded": True,
    }
    destination, source = structured["observed"]
    assert destination["path"] == NESTED_WRITE
    assert destination["status"] == "ok"
    assert destination["absent"] is False
    assert destination["configuration"][0]["name"] == "WriteTarget"
    assert destination["fingerprint"].startswith("tcf1:")
    assert len(destination["fingerprint"]) == 69
    # The moved path's next Mutation token comes from the destination, not the
    # stale read the caller already holds.
    assert source == {"path": WRITE, "status": "ok", "absent": True}


def test_the_native_call_moves_the_source_list_into_the_destination_parent() -> None:
    """One `system.tag.move` per item: the source as a one-element path list,
    the destination's parent folder, and the fixed Abort collision policy."""
    move_calls = [entry for entry in _recorded("allowlisted") if entry["target"] == "system.tag.move"]

    assert move_calls == [
        {
            "target": "system.tag.move",
            "args": [[WRITE], NESTED_PARENT, "Abort"],
            "result": {
                "kind": "quality-codes",
                "items": [{"code": 192, "name": "Good", "level": "Good", "good": True, "diagnosticMessage": None}],
            },
        }
    ]


def test_the_structured_result_satisfies_the_committed_output_schema() -> None:
    structured = _structured("allowlisted")
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    schema = json.loads((ROOT / contract["outputSchema"]).read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(structured)

    assert contract["permissionClass"] == "CONFIG"
    assert contract["mutationClass"] == "CONFIG_MUTATION"
    assert contract["destructive"] is True
    assert contract["preconditionToken"]["kind"] == "tag_config_fingerprint"
    assert contract["collisionPolicy"] == "Abort"
    assert contract["inputBounds"]["hardItemsPolicyField"] == "tagMoveMaxItems"


def test_the_dispatch_never_precedes_the_preflight_checks() -> None:
    """D30 3: source existence, source fingerprint and destination freedom are
    all checked before the first move, and the attempt row precedes dispatch."""
    targets = _recorded_targets("allowlisted")
    first_dispatch = targets.index("system.tag.move")

    assert targets[:2] == ["system.tag.readBlocking", "system.tag.readBlocking"]
    assert targets[2:first_dispatch] == [
        "system.tag.exists",
        "system.tag.getConfiguration",
        "system.tag.exists",
        "system.util.audit",
    ]
    assert targets[first_dispatch + 1:] == [
        "system.util.audit",
        "system.tag.exists",
        "system.tag.getConfiguration",
        "system.tag.exists",
    ]


def test_one_refused_endpoint_rejects_the_whole_batch_and_moves_nothing() -> None:
    error = _error("preflight-refuses-whole-batch", "permission_denied")

    assert error["details"]["reason"] == "preflightTargetRefused"
    assert error["details"]["allowlistKey"] == "tag_move"
    assert error["details"]["auditRecorded"] is True
    assert _problem_reasons(error) == [(TEXT, SIBLING_TEXT, "targetNotAllowlisted")]
    assert "system.tag.move" not in _recorded_targets("preflight-refuses-whole-batch")


def test_the_reserved_provider_is_refused_under_an_explicit_wildcard() -> None:
    """Owner ruling 1 puts the provider check ahead of both endpoints, so even
    `*` cannot move a node out of the policy provider."""
    error = _error("reserved-provider-under-wildcard", "permission_denied")

    assert _problem_reasons(error) == [(RESERVED, RESERVED_MOVED, "reservedProvider")]
    assert "system.tag.move" not in _recorded_targets("reserved-provider-under-wildcard")


def test_a_source_outside_the_allowlist_is_refused_at_the_segment_boundary() -> None:
    error = _error("target-not-allowlisted", "permission_denied")

    assert _problem_reasons(error) == [(SIBLING, NESTED_WRITE, "targetNotAllowlisted")]
    assert "system.tag.move" not in _recorded_targets("target-not-allowlisted")


def test_a_destination_outside_the_allowlist_is_refused_at_the_segment_boundary() -> None:
    """The source is fully allowlisted; the `IgnitionMCP_CI2` destination shares
    the entry's text but crosses a segment boundary, and the whole batch stops."""
    error = _error("destination-not-allowlisted", "permission_denied")

    assert _problem_reasons(error) == [(WRITE, SIBLING, "targetNotAllowlisted")]
    assert "system.tag.move" not in _recorded_targets("destination-not-allowlisted")


def test_a_udt_definition_under_a_bare_wildcard_is_refused() -> None:
    error = _error("udt-definition-under-wildcard", "permission_denied")

    assert _problem_reasons(error) == [(UDT, UDT_NESTED, "udtDefinitionNotAllowlisted")]
    assert "system.tag.move" not in _recorded_targets("udt-definition-under-wildcard")


def test_explicit_types_entries_move_a_udt_definition() -> None:
    structured = _structured("udt-definition-with-explicit-types-entry")

    assert _statuses(structured) == [(UDT, UDT_SIBLING, "executed")]
    assert structured["observed"][0]["absent"] is False
    assert structured["observed"][0]["path"] == UDT_SIBLING
    assert structured["observed"][1] == {"path": UDT, "status": "ok", "absent": True}
    assert structured["summary"]["succeeded"] == 1


def test_a_stale_fingerprint_is_conflict_and_moves_nothing() -> None:
    error = _error("fingerprint-mismatch", "conflict")

    assert error["details"]["reason"] == "preflightPreconditionFailed"
    item = error["details"]["items"][0]
    assert item["sourcePath"] == WRITE
    assert item["reason"] == "fingerprintMismatch"
    assert item["expectedFingerprint"] == ZERO_FP
    assert item["observedFingerprint"].startswith("tcf1:")
    assert len(item["observedFingerprint"]) == 69
    assert "system.tag.move" not in _recorded_targets("fingerprint-mismatch")


def test_a_missing_source_is_not_found_and_its_destination_is_never_read() -> None:
    error = _error("source-missing", "not_found")

    assert error["details"]["reason"] == "preflightPreconditionFailed"
    assert _problem_reasons(error) == [(WRITE, NESTED_WRITE, "sourceMissing")]
    targets = _recorded_targets("source-missing")
    assert targets.count("system.tag.exists") == 1
    assert "system.tag.move" not in targets


def test_an_occupied_destination_is_conflict_before_anything_moves() -> None:
    """D30 4: freedom of the destination is a Preflight precondition; the
    refusal names the destination, and Abort also governs the race window."""
    error = _error("destination-exists", "conflict")

    assert error["details"]["reason"] == "preflightPreconditionFailed"
    assert _problem_reasons(error) == [(WRITE, NESTED_WRITE, "destinationExists")]
    assert error["details"]["items"][0]["path"] == NESTED_WRITE
    assert "system.tag.move" not in _recorded_targets("destination-exists")


def test_a_destination_leaf_that_differs_from_the_source_is_invalid() -> None:
    """`system.tag.move` lands each source under its own name, so a destination
    naming a different leaf would not move the node where the caller asked; the
    refusal happens in the input pass, before the policy read."""
    error = _error("destination-leaf-differs", "invalid_argument")

    assert error["details"]["reason"] == "preflightInputFailed"
    assert [(item.get("path"), item.get("reason")) for item in error["details"]["items"]] == [
        (NESTED_TEXT, "destinationLeafDiffersFromSource")
    ]
    assert _recorded_targets("destination-leaf-differs") == []


def test_an_over_policy_batch_is_refused_without_reading_a_target() -> None:
    error = _error("over-policy-limit", "limit_exceeded")

    assert error["details"] == {"reason": "itemsOverPolicyLimit", "requested": 3, "limit": 2}
    assert _recorded_targets("over-policy-limit") == [
        "system.tag.readBlocking",
        "system.tag.readBlocking",
    ]


def test_audit_off_moves_but_records_nothing() -> None:
    structured = _structured("audit-off")

    assert _statuses(structured) == [(WRITE, NESTED_WRITE, "executed")]
    assert structured["summary"]["auditMode"] == "off"
    assert structured["summary"]["auditRecorded"] is False
    assert "system.util.audit" not in _recorded_targets("audit-off")


RECORDED_FIXTURES = sorted(path.name for path in FIXTURES.glob("tag_move-*.json"))


def test_every_recorded_fixture_is_exercised_by_this_module() -> None:
    source = Path(__file__).read_text(encoding="utf-8")
    for name in RECORDED_FIXTURES:
        case = name[len("tag_move-") : -len(".json")]
        assert f'"{case}"' in source, f"{name} is not referenced by a test"
