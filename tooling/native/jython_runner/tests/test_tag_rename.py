"""D29 recorded-Jython coverage for `tag_rename` (Phase 4 ticket #12).

Every fixture replays the ordered native call list, so a test can assert what
the handler did *not* do as well as what it returned: the allowlist stage
measures the new path (the target's own parent plus the new name), an occupied
new path is conflict before anything renames, and `system.tag.rename` answers
one QualityCode per item.
"""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from tooling.native.jython_runner import run_recorded_tool, run_recorded_tool_error

ROOT = Path(__file__).resolve().parents[4]
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
CONTRACT = ROOT / "contracts/tools/runtime/tag_rename.contract.json"

WRITE = "[default]IgnitionMCP_CI/WriteTarget"
TEXT = "[default]IgnitionMCP_CI/TextTarget"
SIBLING = "[default]IgnitionMCP_CI2/WriteTarget"
RENAMED = "[default]IgnitionMCP_CI/RenamedTarget"
SIBLING_RENAMED = "[default]IgnitionMCP_CI2/RenamedTarget"
SIBLING_RENAMED_OTHER = "[default]IgnitionMCP_CI2/RenamedOther"
UDT = "[default]_types_/IgnitionMCP_CI/ProbeType"
UDT_RENAMED = "[default]_types_/IgnitionMCP_CI/ProbeTypeV2"
RESERVED = "[IgnitionMCPPolicy]WriteProbe"
RESERVED_RENAMED = "[IgnitionMCPPolicy]WriteProbeV2"
BOTH = ",".join([RENAMED, WRITE])
ZERO_FP = "tcf1:" + "0" * 64
EXECUTED_OUTCOME = {
    "code": 192,
    "name": "Good",
    "level": "Good",
    "good": True,
    "diagnosticMessage": {"$ignition": "null"},
}


def _fixture(name: str) -> Path:
    return FIXTURES / f"tag_rename-{name}.json"


def _error(name: str, code: str) -> dict:
    return run_recorded_tool_error("tag_rename", _fixture(name), expected_code=code)


def _structured(name: str) -> dict:
    return run_recorded_tool("tag_rename", _fixture(name))["structuredContent"]


def _problem_reasons(error: dict) -> list[tuple[str, str, str]]:
    return [
        (item.get("path"), item.get("newPath"), item.get("reason"))
        for item in error["details"]["items"]
    ]


def _statuses(structured: dict) -> list[tuple[str, str, str]]:
    return [
        (item["path"], item["newPath"], item["status"])
        for item in structured["items"]
    ]


def _recorded(name: str) -> list[dict]:
    return json.loads(_fixture(name).read_text(encoding="utf-8"))["calls"]


def _recorded_targets(name: str) -> list[str]:
    return [entry["target"] for entry in _recorded(name)]


def test_an_allowlisted_rename_reports_the_native_outcome_and_both_paths() -> None:
    structured = _structured("allowlisted")

    assert _statuses(structured) == [(WRITE, RENAMED, "executed")]
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
    renamed_entry, old_entry = structured["observed"]
    assert renamed_entry["path"] == RENAMED
    assert renamed_entry["status"] == "ok"
    assert renamed_entry["absent"] is False
    assert renamed_entry["configuration"][0]["name"] == "RenamedTarget"
    assert renamed_entry["fingerprint"].startswith("tcf1:")
    assert len(renamed_entry["fingerprint"]) == 69
    assert old_entry == {"path": WRITE, "status": "ok", "absent": True}


def test_the_native_call_renames_the_target_under_its_own_parent() -> None:
    """One `system.tag.rename` per item: the target path and a bare name, never
    a full destination; the Toolkit answers a single QualityCode."""
    rename_calls = [entry for entry in _recorded("allowlisted") if entry["target"] == "system.tag.rename"]

    assert rename_calls == [
        {
            "target": "system.tag.rename",
            "args": [WRITE, "RenamedTarget", "Abort"],
            "result": {
                "kind": "quality",
                "code": 192,
                "name": "Good",
                "level": "Good",
                "good": True,
                "diagnosticMessage": None,
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
    assert contract["destructive"] is False
    assert contract["preconditionToken"]["kind"] == "tag_config_fingerprint"
    assert contract["collisionPolicy"] == "Abort"
    assert contract["inputBounds"]["hardItemsPolicyField"] == "tagRenameMaxItems"


def test_the_dispatch_never_precedes_the_preflight_checks() -> None:
    """D30 3: target existence, target fingerprint and new-path freedom are all
    checked before the first rename, and the attempt row precedes dispatch."""
    targets = _recorded_targets("allowlisted")
    first_dispatch = targets.index("system.tag.rename")

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


def test_one_refused_new_path_rejects_the_whole_batch_and_renames_nothing() -> None:
    """The allowlist stage measures each item's new path; a refused second item
    stops the fully valid first item too."""
    error = _error("preflight-refuses-whole-batch", "permission_denied")

    assert error["details"]["reason"] == "preflightTargetRefused"
    assert error["details"]["allowlistKey"] == "tag_rename"
    assert error["details"]["auditRecorded"] is True
    assert _problem_reasons(error) == [(SIBLING, SIBLING_RENAMED_OTHER, "targetNotAllowlisted")]
    assert "system.tag.rename" not in _recorded_targets("preflight-refuses-whole-batch")


def test_the_reserved_provider_is_refused_under_an_explicit_wildcard() -> None:
    """D30 1: renaming inside the policy provider is refused before the
    allowlist is consulted, so even `*` cannot reach it."""
    error = _error("reserved-provider-under-wildcard", "permission_denied")

    assert _problem_reasons(error) == [(RESERVED, RESERVED_RENAMED, "reservedProvider")]
    assert "system.tag.rename" not in _recorded_targets("reserved-provider-under-wildcard")


def test_a_new_path_outside_the_allowlist_is_refused_at_the_segment_boundary() -> None:
    """The rename keeps its parent, so a `IgnitionMCP_CI2` target renames inside
    `IgnitionMCP_CI2` - a sibling of the entry, not under it."""
    error = _error("target-not-allowlisted", "permission_denied")

    assert _problem_reasons(error) == [(SIBLING, SIBLING_RENAMED, "targetNotAllowlisted")]
    assert "system.tag.rename" not in _recorded_targets("target-not-allowlisted")


def test_a_udt_definition_under_a_bare_wildcard_is_refused() -> None:
    """D30 6: `*` never covers the `_types_` namespace."""
    error = _error("udt-definition-under-wildcard", "permission_denied")

    assert _problem_reasons(error) == [(UDT, UDT_RENAMED, "udtDefinitionNotAllowlisted")]
    assert "system.tag.rename" not in _recorded_targets("udt-definition-under-wildcard")


def test_an_explicit_types_entry_renames_a_udt_definition() -> None:
    structured = _structured("udt-definition-with-explicit-types-entry")

    assert _statuses(structured) == [(UDT, UDT_RENAMED, "executed")]
    assert structured["observed"][0]["absent"] is False
    assert structured["observed"][0]["path"] == UDT_RENAMED
    assert structured["observed"][1] == {"path": UDT, "status": "ok", "absent": True}
    assert structured["summary"]["succeeded"] == 1


def test_a_stale_fingerprint_is_conflict_and_renames_nothing() -> None:
    error = _error("fingerprint-mismatch", "conflict")

    assert error["details"]["reason"] == "preflightPreconditionFailed"
    item = error["details"]["items"][0]
    assert item["path"] == WRITE
    assert item["reason"] == "fingerprintMismatch"
    assert item["expectedFingerprint"] == ZERO_FP
    assert item["observedFingerprint"].startswith("tcf1:")
    assert len(item["observedFingerprint"]) == 69
    assert "system.tag.rename" not in _recorded_targets("fingerprint-mismatch")


def test_a_missing_target_is_not_found_and_its_new_path_is_never_read() -> None:
    error = _error("target-missing", "not_found")

    assert error["details"]["reason"] == "preflightPreconditionFailed"
    assert _problem_reasons(error) == [(WRITE, RENAMED, "targetMissing")]
    targets = _recorded_targets("target-missing")
    assert targets.count("system.tag.exists") == 1
    assert "system.tag.rename" not in targets


def test_an_occupied_new_path_is_conflict_before_anything_renames() -> None:
    """D30 4: freedom of the new path is a Preflight precondition; the refusal
    names the new path, and Abort also governs the race window."""
    error = _error("new-path-exists", "conflict")

    assert error["details"]["reason"] == "preflightPreconditionFailed"
    assert _problem_reasons(error) == [(WRITE, RENAMED, "newPathExists")]
    assert error["details"]["items"][0]["refusedPath"] == RENAMED
    assert "system.tag.rename" not in _recorded_targets("new-path-exists")


def test_a_new_name_that_is_not_one_segment_is_invalid() -> None:
    """`system.tag.rename` takes a name, not a path: a `newName` carrying a
    separator is refused in the input pass, before the policy read."""
    error = _error("new-name-not-a-segment", "invalid_argument")

    assert error["details"]["reason"] == "preflightInputFailed"
    item = error["details"]["items"][0]
    assert item["path"] == WRITE
    assert item["reason"] == "newNameNotASingleSegment"
    assert _recorded_targets("new-name-not-a-segment") == []


def test_an_over_policy_batch_is_refused_without_reading_a_target() -> None:
    error = _error("over-policy-limit", "limit_exceeded")

    assert error["details"] == {"reason": "itemsOverPolicyLimit", "requested": 3, "limit": 2}
    assert _recorded_targets("over-policy-limit") == [
        "system.tag.readBlocking",
        "system.tag.readBlocking",
    ]


def test_audit_off_renames_but_records_nothing() -> None:
    structured = _structured("audit-off")

    assert _statuses(structured) == [(WRITE, RENAMED, "executed")]
    assert structured["summary"]["auditMode"] == "off"
    assert structured["summary"]["auditRecorded"] is False
    assert "system.util.audit" not in _recorded_targets("audit-off")


RECORDED_FIXTURES = sorted(path.name for path in FIXTURES.glob("tag_rename-*.json"))


def test_every_recorded_fixture_is_exercised_by_this_module() -> None:
    source = Path(__file__).read_text(encoding="utf-8")
    for name in RECORDED_FIXTURES:
        case = name[len("tag_rename-") : -len(".json")]
        assert f'"{case}"' in source, f"{name} is not referenced by a test"
