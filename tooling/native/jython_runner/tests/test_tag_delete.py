"""D29 recorded-Jython coverage for `tag_delete` (Phase 4 ticket #12).

Every fixture replays the ordered native call list, so a test can assert what
the handler did *not* do as well as what it returned: a refused Preflight
dispatches no `system.tag.deleteTags` at all, `off` mode makes no audit call,
and the Observed state of a delete is the target's own absence.
"""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from tooling.native.jython_runner import run_recorded_tool, run_recorded_tool_error

ROOT = Path(__file__).resolve().parents[4]
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
CONTRACT = ROOT / "contracts/tools/runtime/tag_delete.contract.json"

WRITE = "[default]IgnitionMCP_CI/WriteTarget"
TEXT = "[default]IgnitionMCP_CI/TextTarget"
SIBLING = "[default]IgnitionMCP_CI2/WriteTarget"
UDT = "[default]_types_/IgnitionMCP_CI/ProbeType"
RESERVED = "[IgnitionMCPPolicy]WriteProbe"
ZERO_FP = "tcf1:" + "0" * 64
EXECUTED_OUTCOME = {
    "code": 192,
    "name": "Good",
    "level": "Good",
    "good": True,
    "diagnosticMessage": {"$ignition": "null"},
}


def _fixture(name: str) -> Path:
    return FIXTURES / f"tag_delete-{name}.json"


def _error(name: str, code: str) -> dict:
    return run_recorded_tool_error("tag_delete", _fixture(name), expected_code=code)


def _structured(name: str) -> dict:
    return run_recorded_tool("tag_delete", _fixture(name))["structuredContent"]


def _problem_reasons(error: dict) -> list[tuple[str, str]]:
    return [(item.get("path"), item.get("reason")) for item in error["details"]["items"]]


def _statuses(structured: dict) -> list[tuple[str, str]]:
    return [(item["path"], item["status"]) for item in structured["items"]]


def _recorded(name: str) -> list[dict]:
    return json.loads(_fixture(name).read_text(encoding="utf-8"))["calls"]


def _recorded_targets(name: str) -> list[str]:
    return [entry["target"] for entry in _recorded(name)]


def test_an_allowlisted_target_is_deleted_and_observed_as_absent() -> None:
    structured = _structured("allowlisted")

    assert _statuses(structured) == [(WRITE, "executed")]
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
    # The Observed state of a delete is the deleted target's own absence.
    assert structured["observed"] == [{"path": WRITE, "status": "ok", "absent": True}]


def test_the_native_call_deletes_exactly_the_named_path() -> None:
    """One `system.tag.deleteTags` call per item: the exact path to remove in a
    one-element list, answered by that path's QualityCode."""
    delete_calls = [entry for entry in _recorded("allowlisted") if entry["target"] == "system.tag.deleteTags"]

    assert delete_calls == [
        {
            "target": "system.tag.deleteTags",
            "args": [[WRITE]],
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
    assert contract["collisionPolicy"] == "not_applicable"
    assert contract["inputBounds"]["hardItemsPolicyField"] == "tagDeleteMaxItems"


def test_the_dispatch_never_precedes_the_preflight_checks() -> None:
    """D30 3: every target's existence and fingerprint are checked before any
    item executes, and the attempt row is written before the dispatch."""
    targets = _recorded_targets("allowlisted")
    first_dispatch = targets.index("system.tag.deleteTags")

    assert targets[:2] == ["system.tag.readBlocking", "system.tag.readBlocking"]
    assert targets[first_dispatch - 1] == "system.util.audit"
    # The Preflight exists/getConfiguration pair for the item, and nothing of the
    # observed half (the post-delete exists) is above the dispatch.
    assert targets[2:first_dispatch] == ["system.tag.exists", "system.tag.getConfiguration", "system.util.audit"]
    assert targets[first_dispatch + 1] == "system.util.audit"
    assert targets[first_dispatch + 2:] == ["system.tag.exists"]


def test_one_refused_target_rejects_the_whole_batch_and_deletes_nothing() -> None:
    """The first item is allowlisted and fully deletable; the second is not, so
    D30 3 refuses the batch and the recorder proves the first was never sent."""
    error = _error("preflight-refuses-whole-batch", "permission_denied")

    assert error["details"]["reason"] == "preflightTargetRefused"
    assert error["details"]["allowlistKey"] == "tag_delete"
    assert error["details"]["auditRecorded"] is True
    assert _problem_reasons(error) == [(SIBLING, "targetNotAllowlisted")]
    assert "system.tag.deleteTags" not in _recorded_targets("preflight-refuses-whole-batch")


def test_the_reserved_provider_is_refused_under_an_explicit_wildcard() -> None:
    """D30 1: the provider check runs before the allowlist, so even `*` cannot
    reach the Runtime Target Policy document's own provider."""
    error = _error("reserved-provider-under-wildcard", "permission_denied")

    assert _problem_reasons(error) == [(RESERVED, "reservedProvider")]
    assert "system.tag.deleteTags" not in _recorded_targets("reserved-provider-under-wildcard")


def test_a_target_outside_the_allowlist_is_refused_at_the_segment_boundary() -> None:
    """The allowlist names `[default]IgnitionMCP_CI`; the `IgnitionMCP_CI2`
    sibling shares its text but not a path segment, and a prefix match across
    that boundary is a refusal."""
    error = _error("target-not-allowlisted", "permission_denied")

    assert _problem_reasons(error) == [(SIBLING, "targetNotAllowlisted")]
    assert "system.tag.deleteTags" not in _recorded_targets("target-not-allowlisted")


def test_a_udt_definition_under_a_bare_wildcard_is_refused() -> None:
    """D30 6: `*` never covers the `_types_` namespace; deleting a definition
    needs an explicit `_types_` entry."""
    error = _error("udt-definition-under-wildcard", "permission_denied")

    assert _problem_reasons(error) == [(UDT, "udtDefinitionNotAllowlisted")]
    assert "system.tag.deleteTags" not in _recorded_targets("udt-definition-under-wildcard")


def test_an_explicit_types_entry_deletes_a_udt_definition() -> None:
    structured = _structured("udt-definition-with-explicit-types-entry")

    assert _statuses(structured) == [(UDT, "executed")]
    assert structured["observed"] == [{"path": UDT, "status": "ok", "absent": True}]
    assert structured["summary"]["succeeded"] == 1


def test_a_stale_fingerprint_is_conflict_and_deletes_nothing() -> None:
    """D30 2: the token is compared against the target's own configuration read
    before any dispatch; the call list stops at the Preflight."""
    error = _error("fingerprint-mismatch", "conflict")

    assert error["details"]["reason"] == "preflightPreconditionFailed"
    item = error["details"]["items"][0]
    assert item["path"] == WRITE
    assert item["reason"] == "fingerprintMismatch"
    assert item["expectedFingerprint"] == ZERO_FP
    assert item["observedFingerprint"].startswith("tcf1:")
    assert len(item["observedFingerprint"]) == 69
    assert "system.tag.deleteTags" not in _recorded_targets("fingerprint-mismatch")


def test_a_missing_target_is_not_found_and_nothing_is_read_or_deleted() -> None:
    error = _error("target-missing", "not_found")

    assert error["details"]["reason"] == "preflightPreconditionFailed"
    assert _problem_reasons(error) == [(WRITE, "targetMissing")]
    targets = _recorded_targets("target-missing")
    assert targets.count("system.tag.getConfiguration") == 0
    assert "system.tag.deleteTags" not in targets


def test_an_over_policy_batch_is_refused_without_reading_a_target() -> None:
    """D10: the 20-target default is raised by the policy field, never read
    past; three targets against a cap of two dispatch nothing."""
    error = _error("over-policy-limit", "limit_exceeded")

    assert error["details"] == {"reason": "itemsOverPolicyLimit", "requested": 3, "limit": 2}
    assert _recorded_targets("over-policy-limit") == [
        "system.tag.readBlocking",
        "system.tag.readBlocking",
    ]


def test_audit_off_deletes_but_records_nothing() -> None:
    structured = _structured("audit-off")

    assert _statuses(structured) == [(WRITE, "executed")]
    assert structured["summary"]["auditMode"] == "off"
    assert structured["summary"]["auditRecorded"] is False
    assert "system.util.audit" not in _recorded_targets("audit-off")

RECORDED_FIXTURES = sorted(path.name for path in FIXTURES.glob("tag_delete-*.json"))


def test_every_recorded_fixture_is_exercised_by_this_module() -> None:
    source = Path(__file__).read_text(encoding="utf-8")

    for name in RECORDED_FIXTURES:
        case = name[len("tag_delete-") : -len(".json")]
        assert f'"{case}"' in source, f"{name} is not referenced by a test"
