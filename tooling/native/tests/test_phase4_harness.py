from __future__ import annotations

import http.server
import importlib.util
import json
from pathlib import Path
import socket
import socketserver
import sys
import threading
import time
from types import ModuleType
from typing import Any

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[3]
PHASE4 = ROOT / "tests/harness/phase4-live"
FIXTURES = ROOT / "tests/fixtures/recorded/gateway-8.3/phase4"
COMPOSE = PHASE4 / "docker-compose.yml"
WORKFLOW = ROOT / ".github/workflows/phase4-live-g4a.yml"
WORKFLOW_G4B = ROOT / ".github/workflows/phase4-live-g4b.yml"
SERVER_CONFIGS = PHASE4 / "gateway-config/com.inductiveautomation.mcp/server-config"
POLICY_SHA256 = "b98bedf5a697fcf178dfad7cdcbae1e40c4be57071d57674f6cdf11487ba58f6"

sys.path.insert(0, str(ROOT / "tests/harness"))
sys.path.insert(0, str(PHASE4))

import recorded_gateway  # noqa: E402
from recorded_gateway import API_TOKEN, RecordedGateway  # noqa: E402

from tooling.native.project import validate_project  # noqa: E402


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


driver = _load("phase4_driver", PHASE4 / "driver.py")
policy_document = _load("phase4_policy_document", PHASE4 / "policy_document.py")
gateway_rest = _load("phase4_gateway_rest", PHASE4 / "gateway_rest.py")
mcp_client = _load("phase4_mcp_client", PHASE4 / "mcp_client.py")
#: Captured before `stub_mcp` patches `driver.mcp_client.McpClient`, so the stub
#: can still speak to the recorded Gateway fake for the ticket #7 Tools.
REAL_MCP_CLIENT = mcp_client.McpClient
#: The run-unique Alarm root this module's driver config uses. The recorded Alarm
#: bodies carry it as `__ALARM_ROOT__` (or as the root of the run that recorded
#: the probe report), and the fake substitutes it on the way out.
ALARM_ROOT = "MCP_P4_1"


def _fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _config(evidence: Path, **overrides: Any) -> Any:
    base_url = str(overrides.get("base_url", "http://127.0.0.1:1"))
    values: dict[str, Any] = {
        "base_url": base_url,
        "api_token": "stub-token",
        "mcp_url": base_url + driver.PROBE_MCP_PATH,
        "operator_mcp_url": base_url + driver.OPERATOR_MCP_PATH,
        "configurator_mcp_url": base_url + driver.CONFIGURATOR_MCP_PATH,
        "evidence_dir": evidence,
        "run_id": "1",
        "gateway_version": "8.3.8",
        "gateway_build": "2026071409",
        "root_name": "MCP_P4_1",
    }
    values.update(overrides)
    return driver.Config(**values)


def _tag_update_paths() -> dict[str, str]:
    """The Tag CONFIG Mutation paths a ticket #10 run uses, for the fake's templates."""
    return {
        "writeTarget": policy_document.TAG_UPDATE_TARGET,
        "textTarget": policy_document.TAG_UPDATE_TEXT_TARGET,
        "nestedFolder": policy_document.TAG_UPDATE_FOLDER,
        "siblingTarget": policy_document.TAG_FIXTURE_SIBLING_PATH,
        "missingTarget": policy_document.TAG_FIXTURE_MISSING_PATH,
        "udtTarget": policy_document.TAG_UPDATE_UDT_TARGET,
    }


class _StubMcp:
    """Stands in for the live Module-hosted endpoint using recorded payloads.

    The ticket #6 probe reports are injected (their variants are crafted per
    test), while the ticket #7 Tools are served by the recorded Gateway fake
    itself: those stages verify state that the fake models (Tag values, the
    policy document, audit rows), so replaying them through the real client is
    what the rehearsal does too.
    """

    reports: dict[str, Any] = {}
    sequences: dict[str, list[Any]] = {}

    def __init__(self, url: str, token: str, **_kwargs: Any) -> None:
        self.url = url

    def _delegate(self) -> Any:
        return REAL_MCP_CLIENT(self.url, API_TOKEN)

    def initialize(self) -> dict[str, Any]:
        return {"protocolVersion": "2025-06-18", "serverInfo": {"name": "ignition-runtime"}}

    def tools_list(self) -> list[str]:
        # The operator and configurator inventories are modelled state (the fake
        # answers them from the recorded operator fixture and from the profile
        # contract), so those URLs go to the real client.
        if self.url.endswith(driver.OPERATOR_MCP_PATH) or self.url.endswith(driver.CONFIGURATOR_MCP_PATH):
            return list(self._delegate().tools_list())
        return ["alarm_probe", "policy_probe", "tag_fixture_probe"]

    def tool_result(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._delegate().tool_result(name, arguments)

    def tool_call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._delegate().tool_call(name, arguments)

    def structured(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "tag_fixture_probe":
            return self._delegate().structured(name, arguments)
        sequence = _StubMcp.sequences.get(name)
        if sequence:
            return sequence[0] if len(sequence) == 1 else sequence.pop(0)
        return self.reports[name]


def _with_gate(
    report: dict[str, Any], *, include_gate: bool = True, served_document: bool = True,
    gate_state: str = "served", declared: int | None = None, materialized: bool | None = None,
) -> dict[str, Any]:
    """Return the report with the gate measurements the current handler produces.

    Every number comes from the report's own policy read (or from the explicit
    override a variant test passes), and any gate measurement already present is
    replaced, so the variant tests and the default stub share one construction.
    The committed recorded report is asserted directly by
    `test_recorded_policy_probe_carries_the_gate_measurements`.
    """
    document = json.loads(json.dumps(report))
    document["measurements"] = [
        entry for entry in document["measurements"] if not str(entry.get("name", "")).startswith("tag.gatedRead.")
    ]
    if not include_gate:
        return document
    policy_read = next(m for m in document["measurements"] if m["name"] == "tag.readBlocking.policy")
    item = policy_read["items"][0]
    length = item["valueByteLength"] if declared is None else declared
    served = gate_state == "served" and served_document
    document["measurements"].insert(2, {
        "name": "tag.gatedRead.policy", "ok": True, "elapsedMs": 1, "label": "policy",
        "cap": policy_document.POLICY_MAX_BYTES, "lengthQuality": "Good", "declaredLength": length,
        "materialized": served if materialized is None else materialized, "gate": gate_state,
        "quality": item["quality"], "valueLength": item["valueLength"],
        "valueByteLength": item["valueByteLength"] if served_document else 0,
        "valueSha256": item["valueSha256"] if served_document else "",
        "valueText": item.get("valueText", "") if served_document else "",
        "lengthMatchesValue": served_document and length == item["valueByteLength"],
    })
    document["measurements"].insert(3, {
        "name": "tag.gatedRead.oversize", "ok": True, "elapsedMs": 1, "label": "oversize",
        "cap": policy_document.POLICY_MAX_BYTES, "lengthQuality": "Good",
        "declaredLength": policy_document.OVERSIZE_POLICY_BYTES, "materialized": False,
        "gate": "oversize", "reason": "declared length exceeds the configured maximum",
    })
    document["schemaVersion"] = 2
    document["maxPolicyBytes"] = policy_document.POLICY_MAX_BYTES
    return document


@pytest.fixture()
def stub_mcp(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    # The recorded probe report names the root of the run it was recorded from,
    # while the stages under test use this module's Alarm root: substitute it the
    # way the recorded Gateway fake does, so a fact derived from the report (the
    # exact Alarm path the shelve cases target) is comparable.
    alarm_text = (FIXTURES / "alarm-probe.json").read_text(encoding="utf-8")
    recorded_root = str(json.loads(alarm_text).get("rootName", ""))
    alarm_report = json.loads(alarm_text.replace(recorded_root, ALARM_ROOT)) if recorded_root else json.loads(alarm_text)
    reports = {
        "policy_probe": _with_gate(_fixture("policy-probe.json")),
        "alarm_probe": alarm_report,
    }
    _StubMcp.reports = reports
    _StubMcp.sequences = {}
    monkeypatch.setattr(driver.mcp_client, "McpClient", _StubMcp)
    return reports


def test_probe_project_is_a_valid_runtime_bundle_profile() -> None:
    files = validate_project(PHASE4 / "project")
    assert "com.inductiveautomation.mcp/tools/policy_probe/onToolCalled.py" in files
    assert "com.inductiveautomation.mcp/tools/alarm_probe/onToolCalled.py" in files
    # The probe project is harness-only: it must never ship bundle_info identity.
    assert "com.inductiveautomation.mcp/tools/bundle_info/onToolCalled.py" not in files


def test_compose_enables_only_the_mcp_module_and_never_binds_the_real_gateway_port() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    prefix = '      GATEWAY_MODULES_ENABLED: "'
    line = next(line for line in text.splitlines() if line.startswith(prefix))
    assert set(line.removeprefix(prefix).removesuffix('"').split(",")) == {"com.inductiveautomation.mcp"}
    assert '      ACCEPT_MODULE_CERTS: "com.inductiveautomation.mcp"' in text
    assert '"127.0.0.1:8093:8088"' in text
    assert '"127.0.0.1:8088:8088"' not in text


def test_policy_document_is_deterministic_and_provider_qualified() -> None:
    assert policy_document.policy_json() == policy_document.policy_json()
    assert policy_document.policy_sha256() == POLICY_SHA256
    assert policy_document.POLICY_TAG_PATH == "[IgnitionMCPPolicy]RuntimeTargetPolicy"
    document = policy_document.tag_document()
    tag = policy_document.find_tag(document, policy_document.POLICY_TAG_NAME)
    assert tag is not None and tag["value"] == policy_document.policy_json()
    assert tag["dataType"] == "String"
    assert policy_document.find_tag(document, "Absent") is None


def test_harness_policy_documents_satisfy_the_shipped_schema() -> None:
    """The harness writes the policy `setup-native apply` will write, so both
    documents must satisfy the contract schema the shipped reader implements."""
    contract = json.loads(
        (ROOT / "contracts/tools/runtime/tag_write.contract.json").read_text(encoding="utf-8")
    )
    schema = json.loads(
        (ROOT / contract["runtimeTargetPolicy"]["documentSchema"]).read_text(encoding="utf-8")
    )
    for document in (
        policy_document.POLICY,
        policy_document.tag_write_policy(),
        policy_document.tag_write_policy(allowlist=policy_document.WILDCARD_ALLOWLIST),
        policy_document.tag_write_policy(audit_mode="required"),
        policy_document.alarm_policy(allowlist=policy_document.alarm_allowlist("mcp_p4_1")),
        policy_document.alarm_policy(allowlist=("prov:default:/tag:mcp_p4_1",), audit_mode="required"),
    ):
        Draft202012Validator(schema).validate(document)
    # The Ticket #6 characterization document carries wildcard-shaped Alarm
    # allowlist entries; it stays shape-valid, and the Alarm Tools refuse an entry
    # they cannot parse, which is what the ticket #8 fixtures pin.
    Draft202012Validator(schema).validate(policy_document.POLICY)
    # The length companion is what bounds the read, so it must measure bytes.
    assert policy_document.tag_write_policy_byte_length() == len(
        policy_document.tag_write_policy_json().encode("utf-8")
    )
    assert policy_document.alarm_policy_byte_length(
        allowlist=policy_document.alarm_allowlist("mcp_p4_1"),
    ) == len(
        policy_document.alarm_policy_json(allowlist=policy_document.alarm_allowlist("mcp_p4_1")).encode("utf-8")
    )


def test_import_failure_normalization_accepts_both_recorded_wire_shapes() -> None:
    assert gateway_rest.import_failures({"successCount": 2, "failureCount": 0, "failures": []}) is None
    assert gateway_rest.import_failures([]) is None
    assert gateway_rest.import_failures(None) is None
    assert gateway_rest.import_failures({"successCount": 0, "failureCount": 1, "failures": [{"quality": "Error"}]})
    assert gateway_rest.import_failures([{"quality": "Error"}])
    assert gateway_rest.import_failures("unexpected") == ["unexpected"]


def test_policy_provision_writes_the_provider_and_reads_the_document_back(tmp_path: Path) -> None:
    with RecordedGateway(policy_provider=policy_document.POLICY_PROVIDER) as gateway:
        config = _config(tmp_path, base_url=gateway.base_url, api_token=API_TOKEN)
        record = driver.stage_policy_provision(config)
    facts = record["facts"]
    assert facts["openapiMissingRoutes"] == []
    assert facts["policyImported"] is True
    # The recorded 8.3.8 run answered the first import while the provider was
    # still starting, so the write path must retry instead of failing.
    assert facts["policyImportRetried"] is True
    assert facts["policyImportAttemptCount"] == 2
    assert facts["abortPolicyRejectsExistingTarget"] is True
    assert facts["mergeOverwriteReimportSucceeded"] is True
    assert facts["restReadBackMatches"] is True
    assert facts["providerResourceSignature"]


def test_policy_probe_facts_come_from_the_recorded_handler_report(stub_mcp: dict[str, Any], tmp_path: Path) -> None:
    config = _config(tmp_path)
    facts = driver.stage_policy_read(config)["facts"]
    assert facts["policyReadQualityIsGood"] is True
    assert facts["policyReadResultCount"] == 1
    assert facts["policyReadMatchesAppliedDocument"] is True
    assert facts["policyReadJsonKeys"] == sorted(policy_document.POLICY.keys())
    assert facts["missingPathFailsClosed"] is True
    assert facts["policyConfigReadOk"] is True
    assert facts["handlerWriteInsidePolicyProviderSucceeded"] is True
    assert facts["handlerScopeHasSystemConfig"] is True
    assert facts["systemConfigResourceReadOk"] is True
    assert facts["handlerProjectName"] == "mcp_p4_probe"


def test_policy_read_gate_checks_the_declared_length_before_reading(
    stub_mcp: dict[str, Any], tmp_path: Path,
) -> None:
    facts = driver.stage_policy_read(_config(tmp_path))["facts"]
    assert facts["policyMaxBytes"] == policy_document.POLICY_MAX_BYTES
    assert facts["policyGateState"] == "served"
    assert facts["policyDeclaredLength"] == policy_document.policy_byte_length()
    assert facts["policyDeclaredLengthMatchesAppliedDocument"] is True
    assert facts["policyGatedReadServedAndVerified"] is True
    assert facts["oversizePolicyGateState"] == "oversize"
    assert facts["oversizePolicyDeclaredLengthExceedsCap"] is True
    assert facts["oversizePolicyMaterialized"] is False


def test_policy_read_fails_closed_when_the_gate_refuses_the_document(
    stub_mcp: dict[str, Any], tmp_path: Path,
) -> None:
    """An over-cap declared length must fail closed without reading the value."""
    _StubMcp.reports = {
        "policy_probe": _with_gate(
            _fixture("policy-probe.json"),
            declared=policy_document.OVERSIZE_POLICY_BYTES,
            gate_state="oversize",
            materialized=False,
        ),
        "alarm_probe": _fixture("alarm-probe.json"),
    }
    config = _config(tmp_path, policy_read_deadline_seconds=0.0)
    with pytest.raises(driver.StageFailure) as caught:
        driver.stage_policy_read(config)
    assert "refused an oversize document" in str(caught.value)


def test_policy_read_retries_a_gate_that_cannot_read_its_length_tag(
    stub_mcp: dict[str, Any], tmp_path: Path,
) -> None:
    """The provider-startup case: an unreadable companion Tag is not a deliberate
    refusal, so the read path repairs with a re-import instead of failing."""
    unserved = _with_gate(_fixture("policy-probe.json"), served_document=False)
    for entry in unserved["measurements"]:
        if entry["name"] == "tag.readBlocking.policy":
            entry["items"] = [{
                "quality": "Bad_NotFound", "valueType": "NoneType",
                "valueLength": 0, "valueByteLength": 0, "valueSha256": "", "valuePrefix": "",
            }]
            entry["jsonKeys"] = []
            entry["jsonKind"] = ""
    blocked = json.loads(json.dumps(unserved))
    for entry in blocked["measurements"]:
        if entry["name"] == "tag.gatedRead.policy":
            entry["gate"] = "blocked"
            entry["lengthQuality"] = "Bad_NotFound"
            entry["reason"] = "declared length is not readable"
    with RecordedGateway(policy_provider=policy_document.POLICY_PROVIDER) as gateway:
        config = _config(
            tmp_path, base_url=gateway.base_url, api_token=API_TOKEN,
            policy_read_deadline_seconds=30.0,
        )
        driver.stage_policy_provision(config)
        _StubMcp.sequences = {"policy_probe": [blocked, stub_mcp["policy_probe"]]}
        facts = driver.stage_policy_read(config)["facts"]
    assert facts["policyReadAttempts"] == 2
    assert facts["policyReadRepairImports"] == 1
    assert facts["policyGatedReadServedAndVerified"] is True


def test_policy_read_reports_a_stale_fixture_as_drift_not_failure(
    stub_mcp: dict[str, Any], tmp_path: Path,
) -> None:
    # A served document whose report predates the gate is drift, not a hang and
    # not a hard failure: the live workflow's drift check catches it.
    _StubMcp.reports = {
        "policy_probe": _with_gate(_fixture("policy-probe.json"), include_gate=False),
        "alarm_probe": _fixture("alarm-probe.json"),
    }
    facts = driver.stage_policy_read(_config(tmp_path))["facts"]
    assert facts["policyGateReported"] is False
    assert facts["policyGatedReadServedAndVerified"] is False
    assert facts["policyReadMatchesAppliedDocument"] is True


def test_alarm_probe_facts_come_from_the_recorded_handler_report(stub_mcp: dict[str, Any], tmp_path: Path) -> None:
    config = _config(tmp_path)
    facts = driver.stage_alarm(config)["facts"]
    assert facts["alarmConclusion"] == "measured"
    assert facts["exactPathCountIsOnePerAlarm"] is True
    assert facts["exactPathMatchesOnlyOwnSource"] is True
    assert facts["exactPathSourceFormMatchesPathForm"] is True
    assert facts["tagPathOnlyPatternMatchesNothing"] is True
    assert facts["folderPathExpandsDescendants"] is False
    assert facts["partialLeafPathMatchesNothing"] is True
    assert facts["alarmNameWildcardMatchesOneAlarm"] is True
    assert facts["rootWildcardMatchesEveryFixtureAlarm"] is True
    assert facts["exactPathBoundedBasis"]["literalMatchingOnly"] is True


def test_recorded_alarm_report_shows_events_accumulating_without_acknowledgement(
    stub_mcp: dict[str, Any], tmp_path: Path,
) -> None:
    """The recorded live run is what parks alarm_acknowledge: one exact Alarm path
    grows one event per unacknowledged activate/clear cycle, so an exact-path
    queryStatus is not bounded before or during execution."""
    facts = driver.stage_alarm(_config(tmp_path))["facts"]
    assert facts["perPathCountStableAcrossCycles"] is False
    assert facts["cycleCounts"] == [(1, 1), (2, 2), (3, 3)]
    assert facts["acknowledgeAttempted"] == 3
    assert facts["acknowledgeStatesAfter"] == ["Cleared, Acknowledged"]
    assert facts["exactPathBoundedBasis"]["noAccumulationWithoutAck"] is False


def test_policy_read_repairs_a_provider_that_serves_no_tags(
    stub_mcp: dict[str, Any], tmp_path: Path,
) -> None:
    """Recorded 8.3.8: an accepted import can leave the running provider serving
    no Tags, so the read path must re-import and probe again instead of recording
    an empty policy read."""
    unserved = json.loads(json.dumps(_fixture("policy-probe.json")))
    for entry in unserved["measurements"]:
        if entry["name"] == "tag.readBlocking.policy":
            entry["items"] = [{
                "quality": "Error_Configuration", "valueType": "NoneType",
                "valueLength": 0, "valueByteLength": 0, "valueSha256": "", "valuePrefix": "",
            }]
            entry["jsonKeys"] = []
            entry["jsonKind"] = ""
    with RecordedGateway(policy_provider=policy_document.POLICY_PROVIDER) as gateway:
        config = _config(tmp_path, base_url=gateway.base_url, api_token=API_TOKEN)
        driver.stage_policy_provision(config)
        _StubMcp.sequences = {
        "policy_probe": [_with_gate(unserved, served_document=False), stub_mcp["policy_probe"]],
    }
        record = driver.stage_policy_read(config)
    facts = record["facts"]
    assert facts["policyReadAttempts"] == 2
    assert facts["policyReadRepairImports"] == 1
    assert facts["policyReadQualityIsGood"] is True
    assert facts["policyReadMatchesAppliedDocument"] is True


def test_recorded_policy_probe_carries_the_gate_measurements() -> None:
    """The committed recording is the evidence for the size gate, so assert the
    recorded shape rather than trusting the test double."""
    report = _fixture("policy-probe.json")
    assert report["schemaVersion"] == 2
    assert report["maxPolicyBytes"] == policy_document.POLICY_MAX_BYTES
    gate = driver.measurement(report, "tag.gatedRead.policy")
    assert gate["gate"] == "served"
    assert gate["materialized"] is True
    assert gate["declaredLength"] == policy_document.policy_byte_length()
    assert gate["valueByteLength"] == gate["declaredLength"]
    assert gate["valueSha256"] == policy_document.policy_sha256()
    assert gate["lengthMatchesValue"] is True
    oversize = driver.measurement(report, "tag.gatedRead.oversize")
    assert oversize["gate"] == "oversize"
    assert oversize["materialized"] is False
    assert oversize["declaredLength"] > policy_document.POLICY_MAX_BYTES


def test_policy_read_fails_closed_on_a_non_positive_declared_length(
    stub_mcp: dict[str, Any], tmp_path: Path,
) -> None:
    """The proposed reader is `0 < declared <= cap`; zero must not reach the read."""
    _StubMcp.reports = {
        "policy_probe": _with_gate(
            _fixture("policy-probe.json"), declared=0, gate_state="invalid", materialized=False,
        ),
        "alarm_probe": _fixture("alarm-probe.json"),
    }
    config = _config(tmp_path, policy_read_deadline_seconds=0.0)
    with pytest.raises(driver.StageFailure) as caught:
        driver.stage_policy_read(config)
    assert "refused an invalid declared length" in str(caught.value)


def test_recorded_probe_reports_show_the_policy_surviving_a_restart() -> None:
    before = _fixture("policy-probe.json")
    after = _fixture("policy-probe-after-restart.json")
    before_item = driver.first_item(driver.measurement(before, "tag.readBlocking.policy"))
    after_item = driver.first_item(driver.measurement(after, "tag.readBlocking.policy"))
    assert before_item["valueSha256"] == after_item["valueSha256"] == policy_document.policy_sha256()
    assert before_item["valueLength"] == after_item["valueLength"] == len(policy_document.policy_json())
    assert driver.measurement(after, "tag.readBlocking.policy")["jsonKeys"] == sorted(policy_document.POLICY.keys())


def _record_stage(evidence: Path, name: str, record: dict[str, Any]) -> None:
    (evidence / f"{name}.json").write_text(json.dumps(record), encoding="utf-8")


def _record_every_stage(evidence: Path, gateway: RecordedGateway) -> dict[str, Any]:
    """Write the seven stage records the way the live workflow does."""
    evidence.mkdir(parents=True, exist_ok=True)
    no_policy = driver.stage_tag_write_no_policy(
        _config(evidence, base_url=gateway.base_url, api_token=API_TOKEN),
    )
    _record_stage(evidence, "tag-write-no-policy", no_policy)
    alarm_no_policy = driver.stage_alarm_no_policy(
        _config(evidence, base_url=gateway.base_url, api_token=API_TOKEN),
    )
    _record_stage(evidence, "alarm-no-policy", alarm_no_policy)
    provision = driver.stage_policy_provision(
        _config(evidence, base_url=gateway.base_url, api_token=API_TOKEN),
    )
    _record_stage(evidence, "policy-provision", provision)
    _record_stage(evidence, "policy-read-before-restart", driver.stage_policy_read(_config(evidence)))
    _record_stage(
        evidence, "policy-read-after-restart",
        driver.stage_policy_read(_config(evidence, label="after-restart")),
    )
    alarm = driver.stage_alarm(_config(evidence))
    _record_stage(evidence, "alarm", alarm)
    _record_stage(
        evidence, "tag-write-setup",
        driver.stage_tag_write_setup(_config(evidence, base_url=gateway.base_url, api_token=API_TOKEN)),
    )
    _record_stage(
        evidence, "tag-write",
        driver.stage_tag_write(_config(evidence, base_url=gateway.base_url, api_token=API_TOKEN)),
    )
    _record_stage(
        evidence, "alarm-shelve",
        driver.stage_alarm_shelve(_config(evidence, base_url=gateway.base_url, api_token=API_TOKEN)),
    )
    return alarm


def test_summarize_reports_no_drift_and_the_recorded_verdict(
    stub_mcp: dict[str, Any], tmp_path: Path,
) -> None:
    with RecordedGateway(
        policy_provider=policy_document.POLICY_PROVIDER,
        runtime_tools=("policy_probe", "alarm_probe", "tag_fixture_probe"),
        audit_profile=policy_document.AUDIT_PROFILE_NAME,
        alarm_root=ALARM_ROOT,
    ) as gateway:
        _record_every_stage(tmp_path, gateway)
    evidence, code = driver.stage_summarize(_config(tmp_path))
    assert code == driver.EXIT_OK
    assert evidence["drift"] == {}
    assert evidence["verdict"]["exactPathAlarmQuery"]["bounded"] is False
    assert evidence["verdict"]["exactPathAlarmQuery"]["literalMatchingOnly"] is True
    assert evidence["verdict"]["runtimeTargetPolicyStorage"]["chosenLocation"] == "[IgnitionMCPPolicy]RuntimeTargetPolicy"
    assert evidence["verdict"]["runtimeTargetPolicyStorage"]["survivesGatewayRestart"] is True
    assert evidence["tickets"] == ["#6", "#7", "#8"]


def test_summarize_verdict_carries_the_tag_write_result(
    stub_mcp: dict[str, Any], tmp_path: Path,
) -> None:
    with RecordedGateway(
        policy_provider=policy_document.POLICY_PROVIDER,
        runtime_tools=("policy_probe", "alarm_probe", "tag_fixture_probe"),
        audit_profile=policy_document.AUDIT_PROFILE_NAME,
        alarm_root=ALARM_ROOT,
    ) as gateway:
        _record_every_stage(tmp_path, gateway)
    evidence, code = driver.stage_summarize(_config(tmp_path))
    assert code == driver.EXIT_OK
    verdict = evidence["verdict"]["runtimeTagWrite"]
    assert verdict["operatorInventoryMatchesProfile"] is True
    assert verdict["allowlistedBatch"] == {
        "requested": 4, "succeeded": 3, "failed": 1, "outcomeUnknown": 0,
        "nativeOutcomes": True, "observedMatchesWritten": True,
    }
    assert verdict["targetAllowlist"] == {
        "siblingRefusedAtSegmentBoundary": True, "preflightExecutedNothing": True,
    }
    assert verdict["reservedProvider"] == {
        "refusedUnderExplicitWildcard": True, "targetValueUnchanged": True,
        "policyDocumentUnclobbered": True,
    }
    assert verdict["audit"]["mode"] == "best_effort"
    assert verdict["audit"]["recorded"] is True
    assert verdict["audit"]["rowsForCorrelation"] == 2


def test_summarize_verdict_carries_the_complete_refusal_rule(
    stub_mcp: dict[str, Any], tmp_path: Path,
) -> None:
    """The evidence verdict is what a later implementer reads, so it has to state
    every refused mutation and both ends of the source/destination pairs."""
    with RecordedGateway(
        policy_provider=policy_document.POLICY_PROVIDER,
        runtime_tools=("policy_probe", "alarm_probe", "tag_fixture_probe"),
        audit_profile=policy_document.AUDIT_PROFILE_NAME,
        alarm_root=ALARM_ROOT,
    ) as gateway:
        _record_every_stage(tmp_path, gateway)
    evidence, _code = driver.stage_summarize(_config(tmp_path))
    sentence = evidence["verdict"]["runtimeTargetPolicyStorage"]["runtimeWritePrevention"]
    for mutation in ("tag_write", "tag_update", "tag_delete", "tag_create",
                     "tag_move", "tag_rename", "tag_copy"):
        assert mutation in sentence, mutation
    assert "source or destination" in sentence
    assert "including an explicit *" in sentence
    assert policy_document.POLICY_PROVIDER in sentence
    assert "tag_copy destination" not in sentence


def test_summarize_detects_a_descendant_matching_regression(
    stub_mcp: dict[str, Any], tmp_path: Path,
) -> None:
    with RecordedGateway(
        policy_provider=policy_document.POLICY_PROVIDER,
        runtime_tools=("policy_probe", "alarm_probe", "tag_fixture_probe"),
        audit_profile=policy_document.AUDIT_PROFILE_NAME,
        alarm_root=ALARM_ROOT,
    ) as gateway:
        alarm = _record_every_stage(tmp_path, gateway)
    alarm["facts"]["folderPathExpandsDescendants"] = True
    _record_stage(tmp_path, "alarm", alarm)
    evidence, code = driver.stage_summarize(_config(tmp_path))
    assert code == driver.EXIT_DRIFTED
    assert "folderPathExpandsDescendants" in evidence["drift"]
    assert evidence["verdict"]["exactPathAlarmQuery"]["bounded"] is False


def test_tag_write_stages_record_the_live_facts(
    stub_mcp: dict[str, Any], tmp_path: Path,
) -> None:
    """The three ticket #7 stages derive their facts from the recorded Gateway."""
    with RecordedGateway(
        policy_provider=policy_document.POLICY_PROVIDER,
        runtime_tools=("policy_probe", "alarm_probe", "tag_fixture_probe"),
        audit_profile=policy_document.AUDIT_PROFILE_NAME,
        alarm_root=ALARM_ROOT,
    ) as gateway:
        config = _config(tmp_path, base_url=gateway.base_url, api_token=API_TOKEN)
        no_policy = driver.stage_tag_write_no_policy(config)["facts"]
        driver.stage_policy_provision(config)
        setup = driver.stage_tag_write_setup(config)["facts"]
        facts = driver.stage_tag_write(config)["facts"]

    # No policy on the Gateway: fail closed, before anything executes.
    assert no_policy["tagWriteNoPolicyErrorCode"] == "operation_disabled"
    assert no_policy["tagWriteNoPolicyFailsClosed"] is True
    # Test-only provisioning: fixture Tags, the audit profile and the policy.
    assert setup["tagFixtureConfigured"] is True
    assert setup["tagFixturePathsMatch"] is True
    assert setup["auditProfileAvailable"] is True
    assert setup["tagWritePolicyInstalled"] is True
    assert setup["tagWritePolicyServedSha256"] == policy_document.tag_write_policy_sha256()
    # The batch, both refusals, the reserved provider and the audit read-back.
    assert facts["tagWriteOperatorInventoryMatchesProfile"] is True
    assert facts["tagWriteBatchNativeOutcomes"] is True
    assert facts["tagWriteObservedMatchesWritten"] is True
    assert facts["tagWriteObservedMissingQualityIsBad"] is True
    assert facts["tagWriteSiblingDenialIsSegmentBoundary"] is True
    assert facts["tagWriteSiblingValueUnchanged"] is True
    assert facts["tagWritePreflightExecutedNothing"] is True
    assert facts["tagWriteReservedProviderRefusedUnderWildcard"] is True
    assert facts["tagWriteReservedProviderValueUnchanged"] is True
    assert facts["tagWritePolicyDocumentUnclobbered"] is True
    assert facts["tagWriteAuditAttemptAndResultRecorded"] is True
    assert facts["tagWriteAuditActorIsServiceIdentity"] is True


def test_tag_write_refuses_a_deployed_inventory_that_differs_from_the_profile(
    stub_mcp: dict[str, Any], tmp_path: Path,
) -> None:
    """D09: the served inventory must equal the profile exactly, not merely cover it."""
    with RecordedGateway(
        policy_provider=policy_document.POLICY_PROVIDER,
        runtime_tools=("policy_probe", "alarm_probe", "tag_fixture_probe"),
        audit_profile=policy_document.AUDIT_PROFILE_NAME,
        alarm_root=ALARM_ROOT,
    ) as gateway:
        config = _config(tmp_path, base_url=gateway.base_url, api_token=API_TOKEN)
        driver.stage_policy_provision(config)
        driver.stage_tag_write_setup(config)
        monkeypatch = pytest.MonkeyPatch()
        original = _StubMcp.tools_list

        def extra(self: Any) -> list[str]:
            return original(self) + ["tag_delete"]

        monkeypatch.setattr(_StubMcp, "tools_list", extra)
        try:
            with pytest.raises(driver.StageFailure) as caught:
                driver.stage_tag_write(config)
        finally:
            monkeypatch.undo()
        assert "operator inventory" in str(caught.value)


def test_tag_write_setup_requires_the_provider_to_serve_the_policy(
    stub_mcp: dict[str, Any], tmp_path: Path,
) -> None:
    """An accepted import is not proof the running provider serves it (ticket #6),
    so the install loop must verify the handler-scope read and give up loudly."""
    with RecordedGateway(
        policy_provider=policy_document.POLICY_PROVIDER,
        runtime_tools=("policy_probe", "alarm_probe", "tag_fixture_probe"),
        audit_profile=policy_document.AUDIT_PROFILE_NAME,
        alarm_root=ALARM_ROOT,
    ) as gateway:
        config = _config(tmp_path, base_url=gateway.base_url, api_token=API_TOKEN)
        driver.stage_policy_provision(config)
        monkeypatch = pytest.MonkeyPatch()
        # A provider that accepts the import and serves nothing (the recorded
        # 8.3.8 failure) must not look like a successful install.
        monkeypatch.setattr(recorded_gateway, "_record_tag_import", lambda server, body: None)
        try:
            installed = driver.install_tag_write_policy(
                config, mcp_client.McpClient(config.operator_url, API_TOKEN),
                allowlist=policy_document.TAG_WRITE_ALLOWLIST, deadline_seconds=0.0,
            )
        finally:
            monkeypatch.undo()
    assert installed["ok"] is False
    assert installed["attemptCount"] == 1
    assert installed["servedSha256"] == policy_document.policy_sha256()


def test_tag_write_case_selector_replays_the_recorded_refusals() -> None:
    """The rehearsal's case selection is what makes the negative cases reachable."""

    class _Server:
        policy_provider_created = True

    cases = {
        "reserved-provider-refusal": [{"path": "[IgnitionMCPPolicy]WriteProbe"}],
        "sibling-denial": [{"path": "[default]IgnitionMCP_CI2/WriteTarget"}],
        "preflight-refusal": [{"path": "[default]IgnitionMCP_CI/WriteTarget"}, {"path": "[default]IgnitionMCP_CI2/WriteTarget"}],
        "allowlisted-batch": [{"path": "[default]IgnitionMCP_CI/WriteTarget"}],
    }
    for expected, writes in cases.items():
        case, _paths = recorded_gateway._tag_write_case(_Server(), {"writes": writes})
        assert case == expected, (expected, case)


def test_tag_update_case_selector_replays_the_recorded_refusals() -> None:
    """The rehearsal's case selection mirrors the shipped handler's refusal order."""

    class _Server:
        policy_provider_created = True
        tag_update_paths: dict[str, str] = {}
        tag_config = {
            policy_document.TAG_UPDATE_TARGET: [{"name": "WriteTarget", "value": 0}],
            policy_document.TAG_UPDATE_FOLDER: [{"name": "Nested", "tagType": "Folder"}],
        }

    class _Policy(_Server):
        policy_value = json.dumps(policy_document.tag_update_policy(), sort_keys=True, separators=(",", ":"))

    class _TypesPolicy(_Server):
        policy_value = json.dumps(
            policy_document.tag_update_policy(allowlist=policy_document.TAG_UPDATE_TYPES_ALLOWLIST),
            sort_keys=True, separators=(",", ":"),
        )

    def item(path: str, fingerprint: str) -> dict[str, Any]:
        return {"path": path, "expectedFingerprint": fingerprint, "config": {"setpoint": 1}}

    target = policy_document.TAG_UPDATE_TARGET
    fresh = recorded_gateway._tag_config_fingerprint(_Server.tag_config[target])
    folder_fresh = recorded_gateway._tag_config_fingerprint(
        _Server.tag_config[policy_document.TAG_UPDATE_FOLDER]
    )
    cases = [
        (_Policy, [item(target, fresh)], "allowlisted"),
        (_Policy, [item(policy_document.TAG_UPDATE_FOLDER, folder_fresh)], "allowlisted"),
        (_Policy, [item(target, "tcf1:" + "0" * 64)], "stale-fingerprint"),
        (_Policy, [item("[IgnitionMCPPolicy]WriteProbe", fresh)], "reserved-provider-refusal"),
        (_Policy, [item(policy_document.TAG_FIXTURE_SIBLING_PATH, fresh)], "sibling-denial"),
        (_Policy, [item("[default]IgnitionMCP_CI/Missing", fresh)], "missing-target"),
        (_Policy, [item(policy_document.TAG_UPDATE_UDT_TARGET, fresh)], "udt-not-allowlisted"),
        (_TypesPolicy, [item(policy_document.TAG_UPDATE_UDT_TARGET, fresh)], "missing-target"),
    ]
    for server_class, items, expected in cases:
        case, _paths = recorded_gateway._tag_update_case(server_class(), {"items": items})
        assert case == expected, (items, expected, case)

    class _NoPolicy:
        policy_provider_created = False
        policy_value = ""

    case, _paths = recorded_gateway._tag_update_case(
        _NoPolicy(), {"items": [item(target, "tcf1:" + "0" * 64)]},
    )
    assert case == "no-policy"


def test_tag_update_stages_record_the_live_facts(stub_mcp: dict[str, Any], tmp_path: Path) -> None:
    """The ticket #10 stages derive their facts from the recorded Gateway."""
    with RecordedGateway(
        policy_provider=policy_document.POLICY_PROVIDER,
        runtime_tools=("policy_probe", "alarm_probe", "tag_fixture_probe"),
        audit_profile=policy_document.AUDIT_PROFILE_NAME,
        alarm_root=ALARM_ROOT,
        tag_update_paths=_tag_update_paths(),
    ) as gateway:
        config = _config(tmp_path, base_url=gateway.base_url, api_token=API_TOKEN, stages=driver.MILESTONE_4B)
        no_policy = driver.stage_tag_update_no_policy(config)["facts"]
        _record_stage(tmp_path, "tag-update-no-policy", {"stage": "tag-update-no-policy", "facts": no_policy,
                                                         "identity": driver.identity(config), "guard": config.guard})
        driver.stage_policy_provision(config)
        _record_stage(
            tmp_path, "policy-provision",
            {"stage": "policy-provision", "facts": driver.stage_policy_provision(config)["facts"],
             "identity": driver.identity(config), "guard": config.guard},
        )
        setup = driver.stage_tag_update_setup(config)["facts"]
        _record_stage(tmp_path, "tag-update-setup", {"stage": "tag-update-setup", "facts": setup,
                                                     "identity": driver.identity(config), "guard": config.guard})
        facts = driver.stage_tag_update(config)["facts"]
        _record_stage(tmp_path, "tag-update", {"stage": "tag-update", "facts": facts,
                                               "identity": driver.identity(config), "guard": config.guard})

    # The CONFIG class is deployed on the configurator profile and nowhere else.
    assert facts["tagUpdateConfiguratorInventoryMatchesProfile"] is True
    assert facts["tagUpdateOperatorInventoryExcludesConfigMutation"] is True
    # The fingerprint is the documented rule over what the read published, and the
    # handler compared exactly the token the caller held.
    assert facts["tagUpdateFingerprintRecomputesFromPublishedConfiguration"] is True
    assert facts["tagUpdateFingerprintStableAcrossReads"] is True
    assert facts["tagUpdateUpdateStatus"] == "executed"
    assert facts["tagUpdateIndependentReadShowsTheChange"] is True
    # A Folder is a target too, and the existence check answers for one.
    assert facts["tagUpdateFolderTargetStatus"] == "executed"
    assert facts["tagUpdateFolderTargetIndependentReadShowsTheChange"] is True
    assert facts["tagUpdateFolderTargetFingerprintChanged"] is True
    assert facts["tagUpdateObservedFingerprintChanged"] is True
    assert facts["tagUpdateIndependentReadMatchesObserved"] is True
    assert facts["tagUpdateStaleFingerprintIsConflict"] is True
    assert facts["tagUpdateStaleFingerprintChangedNothing"] is True
    assert facts["tagUpdateNeverCreatesTarget"] is True
    assert facts["tagUpdateMissingTargetAbsentFromExport"] is True
    assert facts["tagUpdateSiblingDenialIsSegmentBoundary"] is True
    assert facts["tagUpdateUdtNeedsExplicitTypesEntry"] is True
    assert facts["tagUpdateUdtDefinitionReadIsAllowed"] is True
    assert facts["tagUpdateTypesEntryIsHonoured"] is True
    assert facts["tagUpdateBareWildcardDoesNotCoverUdt"] is True
    assert facts["tagUpdateReservedProviderRefusedUnderWildcard"] is True
    assert facts["tagUpdatePolicyDocumentUnclobbered"] is True
    assert facts["tagUpdatePreflightExecutedNothing"] is True
    assert facts["tagUpdateOverPolicyLimitIsRefused"] is True
    assert facts["tagUpdateSiblingDenialAuditRecorded"] is True
    assert facts["tagUpdateStaleFingerprintAuditRecorded"] is True
    assert no_policy["tagUpdateNoPolicyFailsClosed"] is True
    assert no_policy["tagUpdateNoPolicyReason"] == "declaredLengthUnavailable"
    assert setup["tagUpdatePolicyInstalled"] is True


def test_summarize_verdict_carries_the_tag_update_result(tmp_path: Path) -> None:
    """The milestone selector picks the stage set, the expectations file and the verdict."""
    with RecordedGateway(
        policy_provider=policy_document.POLICY_PROVIDER,
        runtime_tools=("policy_probe", "alarm_probe", "tag_fixture_probe"),
        audit_profile=policy_document.AUDIT_PROFILE_NAME,
        alarm_root=ALARM_ROOT,
        tag_update_paths=_tag_update_paths(),
    ) as gateway:
        config = _config(tmp_path, base_url=gateway.base_url, api_token=API_TOKEN, stages=driver.MILESTONE_4B)
        stages = [
            ("tag-update-no-policy", driver.stage_tag_update_no_policy),
            ("policy-provision", driver.stage_policy_provision),
            ("tag-update-setup", driver.stage_tag_update_setup),
            ("tag-update", driver.stage_tag_update),
        ]
        for name, stage in stages:
            _record_stage(tmp_path, name, stage(config))
    evidence, code = driver.stage_summarize(config)
    assert code == driver.EXIT_OK
    assert evidence["milestone"] == driver.MILESTONE_4B
    assert evidence["tickets"] == ["#10"]
    assert evidence["drift"] == {}
    assert evidence["verdict"]["runtimeTagConfigMutation"]["update"]["status"] == "executed"
    assert evidence["verdict"]["runtimeTagConfigMutation"]["staleFingerprint"]["changedNothing"] is True


def test_alarm_mutation_case_selector_replays_the_recorded_refusals() -> None:
    """The rehearsal's case selection is what makes the negative cases reachable."""

    class _Server:
        policy_provider_created = True
        policy_value = '{"alarmShelveMaxSeconds":3600,"schemaVersion":1}'

    server = _Server()
    cases = [
        (("alarm_shelve", {"paths": ["prov:default:/tag:mcp_p4_1/*"], "timeoutSeconds": 60}), "wildcard-refusal"),
        (("alarm_shelve", {"paths": ["prov:default:/tag:mcp_p4_1_sibling/Exact"], "timeoutSeconds": 60}), "sibling-denial"),
        (("alarm_shelve", {"paths": ["prov:default:/tag:mcp_p4_1/Exact"], "timeoutSeconds": 86401}), "hard-max-refusal"),
        (("alarm_shelve", {"paths": ["prov:default:/tag:mcp_p4_1/Exact"], "timeoutSeconds": 7200}), "cap-refusal"),
        (("alarm_shelve", {"paths": ["prov:default:/tag:mcp_p4_1/Exact"], "timeoutSeconds": 3600}), "allowlisted"),
        (("alarm_unshelve", {"paths": ["prov:default:/tag:mcp_p4_1/Exact"]}), "allowlisted"),
        (("alarm_unshelve", {"paths": ["prov:default:/tag:mcp_p4_1/*"]}), "wildcard-refusal"),
    ]
    for (tool, arguments), expected in cases:
        case, _paths = recorded_gateway._alarm_mutation_case(server, tool, arguments)
        assert case == expected, (tool, arguments, expected, case)

    class _NoPolicy:
        policy_provider_created = False

    case, _paths = recorded_gateway._alarm_mutation_case(
        _NoPolicy(), "alarm_shelve", {"paths": ["prov:default:/tag:mcp_p4_1/Exact"], "timeoutSeconds": 60},
    )
    assert case == "no-policy"


def test_alarm_stages_record_the_live_facts(
    stub_mcp: dict[str, Any], tmp_path: Path,
) -> None:
    """The two ticket #8 stages derive their facts from the recorded Gateway."""
    with RecordedGateway(
        policy_provider=policy_document.POLICY_PROVIDER,
        runtime_tools=("policy_probe", "alarm_probe", "tag_fixture_probe"),
        audit_profile=policy_document.AUDIT_PROFILE_NAME,
        alarm_root=ALARM_ROOT,
    ) as gateway:
        config = _config(tmp_path, base_url=gateway.base_url, api_token=API_TOKEN)
        no_policy = driver.stage_alarm_no_policy(config)["facts"]
        driver.stage_policy_provision(config)
        _record_stage(tmp_path, "alarm", driver.stage_alarm(config))
        facts = driver.stage_alarm_shelve(config)["facts"]

    # No policy on the Gateway: both Alarm Mutations fail closed before executing.
    assert no_policy["alarmNoPolicyShelveCode"] == "operation_disabled"
    assert no_policy["alarmNoPolicyUnshelveCode"] == "operation_disabled"
    assert no_policy["alarmNoPolicyFailsClosed"] is True
    # The shelve, its Observed state and the audit read-back.
    assert facts["alarmShelvePathMatchesAlarmFixture"] is True
    assert facts["alarmShelveOperatorInventoryMatchesProfile"] is True
    assert facts["alarmShelvePolicyInstalled"] is True
    assert facts["alarmShelveObservedShelved"] is True
    assert facts["alarmShelveListShowsExactPath"] is True
    assert facts["alarmShelveAuditAttemptAndResultRecorded"] is True
    assert facts["alarmShelveAuditActorIsServiceIdentity"] is True
    # The duration bounds and both refusals.
    assert facts["alarmShelveCapRefusalReason"] == "durationOverPolicyCap"
    assert facts["alarmShelveCapRefusalCap"] == policy_document.ALARM_SHELVE_CAP_SECONDS
    assert facts["alarmShelveHardMaxRefusalReason"] == "durationOutOfRange"
    assert facts["alarmShelveWildcardRefusalReason"] == "wildcardPath"
    assert facts["alarmShelveSiblingDenialReason"] == "targetNotAllowlisted"
    assert facts["alarmShelvePreflightExecutedNothing"] is True
    assert facts["alarmShelveDurationRefusalsShelvedNothing"] is True
    # The unshelve and its refusals.
    assert facts["alarmUnshelveObservedNotShelved"] is True
    assert facts["alarmUnshelveExactPathRemoved"] is True
    assert facts["alarmUnshelveSiblingDenialReason"] == "targetNotAllowlisted"
    assert facts["alarmUnshelveWildcardRefusalReason"] == "wildcardPath"


def test_summarize_verdict_carries_the_alarm_mutation_result(
    stub_mcp: dict[str, Any], tmp_path: Path,
) -> None:
    with RecordedGateway(
        policy_provider=policy_document.POLICY_PROVIDER,
        runtime_tools=("policy_probe", "alarm_probe", "tag_fixture_probe"),
        audit_profile=policy_document.AUDIT_PROFILE_NAME,
        alarm_root=ALARM_ROOT,
    ) as gateway:
        _record_every_stage(tmp_path, gateway)
    evidence, code = driver.stage_summarize(_config(tmp_path))
    assert code == driver.EXIT_OK
    verdict = evidence["verdict"]["runtimeAlarmMutations"]
    assert verdict["operatorInventoryMatchesProfile"] is True
    assert verdict["shelve"]["observedShelved"] is True
    assert verdict["shelve"]["shelvedListShowsPath"] is True
    assert verdict["shelve"]["capRefusal"] == {
        "code": "invalid_argument", "reason": "durationOverPolicyCap",
        "cap": policy_document.ALARM_SHELVE_CAP_SECONDS,
    }
    assert verdict["shelve"]["hardMaxRefusalReason"] == "durationOutOfRange"
    assert verdict["shelve"]["wildcardRefusalReason"] == "wildcardPath"
    assert verdict["shelve"]["targetAllowlist"] == {
        "siblingRefusedAtSegmentBoundary": True, "preflightExecutedNothing": True,
    }
    assert verdict["shelve"]["noPolicy"] == {
        "code": "operation_disabled", "reason": "declaredLengthUnavailable",
    }
    assert verdict["shelve"]["audit"]["actorIsServiceIdentity"] is True
    assert verdict["shelve"]["audit"]["rowsForCorrelation"] == 2
    assert verdict["unshelve"]["observedNotShelved"] is True
    assert verdict["unshelve"]["pathRemoved"] is True
    assert verdict["unshelve"]["siblingRefusalCode"] == "permission_denied"
    assert verdict["unshelve"]["wildcardRefusalReason"] == "wildcardPath"


def test_recorded_alarm_tool_bodies_satisfy_the_shipped_schemas() -> None:
    """The bodies the rehearsal replays are the shipped Tools' own output, so a
    schema change that outgrows them has to fail here rather than at live CI."""
    cases = {
        "alarm_shelve": ("alarm-shelve-allowlisted",),
        "alarm_unshelve": ("alarm-unshelve-allowlisted",),
    }
    for tool, names in cases.items():
        contract = json.loads(
            (ROOT / f"contracts/tools/runtime/{tool}.contract.json").read_text(encoding="utf-8")
        )
        schema = json.loads((ROOT / contract["outputSchema"]).read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema)
        for name in names:
            body = _fixture(f"{name}.json")
            assert body["isError"] is False
            validator.validate(body["structuredContent"])
        # The refusals carry the canonical D06 error shape instead.
        for name in sorted(path.stem for path in FIXTURES.glob(f"{tool.replace('_', '-')}-*.json")):
            body = _fixture(f"{name}.json")
            if body["isError"]:
                error = json.loads(body["content"][0]["text"])
                assert set(error) <= {"code", "message", "correlationId", "details"}
                assert error["code"] in {
                    "invalid_argument", "permission_denied", "operation_disabled",
                }, name


def test_phase4_live_workflow_verifies_the_alarm_mutations() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "driver.py alarm-no-policy" in text
    assert "driver.py alarm-shelve" in text
    assert "Refuse the Alarm Mutations while no Runtime Target Policy exists" in text


def _marker_document(config: Any, **overrides: Any) -> dict[str, Any]:
    document = {
        "marker": "ignition-mcp-phase4-live",
        "environment": "phase4-live",
        "runId": config.run_id,
        "gatewayVersion": config.gateway_version,
        "gatewayBuild": config.gateway_build,
        "gatewayId": f"phase4-g4a-{config.gateway_version}-{config.run_id}",
        "trustedRepo": "sheon-sek/ignition-mcp",
        "policyProvider": config.provider,
        "alarmRoot": config.root_name,
        "runtimeProject": config.runtime_project,
        "auditProfile": config.audit_profile,
    }
    document.update(overrides)
    return document


def _marker_file(path: Path, config: Any, **overrides: Any) -> Path:
    path.write_text(json.dumps(_marker_document(config, **overrides), indent=2) + "\n", encoding="utf-8")
    return path


def _guarded_config(tmp_path: Path, *, base_url: str, **overrides: Any) -> Any:
    values: dict[str, Any] = {
        "base_url": base_url,
        "api_token": API_TOKEN,
        "mcp_url": base_url.rstrip("/") + driver.EXPECTED_MCP_PATH,
        "evidence_dir": tmp_path,
        "run_id": "424242",
        "gateway_version": "8.3.8",
        "gateway_build": "2026071409",
        "root_name": "mcp_p4_424242",
    }
    values.update(overrides)
    return driver.Config(**values)


@pytest.fixture()
def disposable_gateway() -> Any:
    """The recorded fake bound to the one origin the guard accepts."""
    try:
        gateway = RecordedGateway(policy_provider=policy_document.POLICY_PROVIDER, port=driver.EXPECTED_ORIGIN_PORT)
    except OSError as error:  # pragma: no cover - depends on the workstation
        pytest.skip(f"127.0.0.1:{driver.EXPECTED_ORIGIN_PORT} is not available: {error}")
    with gateway:
        yield gateway


def test_guard_rejects_a_foreign_origin_before_any_request(tmp_path: Path) -> None:
    """A wrong port must be refused locally: the first request is the bug."""
    with RecordedGateway(policy_provider=policy_document.POLICY_PROVIDER) as gateway:
        config = _guarded_config(tmp_path, base_url=gateway.base_url)
        marker = _marker_file(tmp_path / "ci-marker.json", config)
        config = driver.Config(**{**config.__dict__, "ci_marker": marker})
        with pytest.raises(driver.GuardError) as caught:
            driver.verify_guard(config)
        assert str(driver.EXPECTED_ORIGIN_PORT) in str(caught.value)
        assert gateway.requests == []


def test_guard_rejects_the_real_gateway_port_and_foreign_hosts(tmp_path: Path) -> None:
    for base_url in ("http://127.0.0.1:8088", "http://192.0.2.10:8093", "https://127.0.0.1:8093",
                     "http://127.0.0.1:8093/extra"):
        config = _guarded_config(tmp_path, base_url=base_url)
        marker = _marker_file(tmp_path / "ci-marker.json", config)
        with pytest.raises(driver.GuardError):
            driver.verify_guard(driver.Config(**{**config.__dict__, "ci_marker": marker}))


def test_guard_rejects_a_foreign_mcp_path(tmp_path: Path) -> None:
    origin = f"http://127.0.0.1:{driver.EXPECTED_ORIGIN_PORT}"
    config = _guarded_config(tmp_path, base_url=origin, mcp_url=origin + "/data/mcp/other-server")
    marker = _marker_file(tmp_path / "ci-marker.json", config)
    with pytest.raises(driver.GuardError):
        driver.verify_guard(driver.Config(**{**config.__dict__, "ci_marker": marker}))


def test_guard_requires_the_marker_to_name_this_run_policy_and_alarm_root(
    disposable_gateway: Any, tmp_path: Path,
) -> None:
    origin = disposable_gateway.base_url
    config = _guarded_config(tmp_path, base_url=origin)
    marker = tmp_path / "ci-marker.json"
    for override in (
        {"gatewayId": "phase4-g4a-8.3.8-999"},
        {"policyProvider": "SomeOtherProvider"},
        {"alarmRoot": "mcp_p4_1"},
        {"runId": "999"},
        {"trustedRepo": "someone-else/ignition-mcp"},
        {"marker": "ignition-mcp-phase3-live"},
        {"environment": "phase3-live"},
        {"runtimeProject": "someone-elses-project"},
        {"auditProfile": "SomeOtherProfile"},
    ):
        _marker_file(marker, config, **override)
        with pytest.raises(driver.GuardError) as caught:
            driver.verify_guard(driver.Config(**{**config.__dict__, "ci_marker": marker}))
        assert next(iter(override)) in str(caught.value)
    _marker_file(marker, config)
    driver.verify_guard(driver.Config(**{**config.__dict__, "ci_marker": marker}))


class _RedirectOnly(http.server.BaseHTTPRequestHandler):
    """A Gateway look-alike that answers every path with a redirect."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *_args: object) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802
        self.send_response(302)
        self.send_header("Location", f"{self.server.target}{self.path}")  # type: ignore[attr-defined]
        self.send_header("Content-Length", "0")
        self.end_headers()


def test_guard_refuses_a_redirected_gateway_info(tmp_path: Path) -> None:
    """A redirect must not bounce the guard onto another origin."""
    target = RecordedGateway(policy_provider=policy_document.POLICY_PROVIDER)
    try:
        redirector = http.server.ThreadingHTTPServer(("127.0.0.1", driver.EXPECTED_ORIGIN_PORT), _RedirectOnly)
    except OSError as error:  # pragma: no cover - depends on the workstation
        pytest.skip(f"127.0.0.1:{driver.EXPECTED_ORIGIN_PORT} is not available: {error}")
    redirector.target = target.base_url  # type: ignore[attr-defined]
    thread = threading.Thread(target=redirector.serve_forever, daemon=True)
    thread.start()
    try:
        with target:
            origin = f"http://127.0.0.1:{driver.EXPECTED_ORIGIN_PORT}"
            config = _guarded_config(tmp_path, base_url=origin)
            marker = _marker_file(tmp_path / "ci-marker.json", config)
            with pytest.raises(driver.GuardError) as caught:
                driver.verify_guard(driver.Config(**{**config.__dict__, "ci_marker": marker}))
            assert "redirect" in str(caught.value)
            assert target.requests == []
    finally:
        redirector.shutdown()
        redirector.server_close()
        thread.join(timeout=5)


def test_driver_guard_fails_closed_on_a_missing_marker(tmp_path: Path) -> None:
    with RecordedGateway(policy_provider=policy_document.POLICY_PROVIDER) as gateway:
        config = _guarded_config(tmp_path, base_url=gateway.base_url)
        with pytest.raises(driver.GuardError):
            driver.verify_guard(config)
        assert gateway.requests == []


def _waiter() -> ModuleType:
    return _load("phase4_wait_for_gateway", PHASE4 / "wait_for_gateway.py")


def test_wait_for_gateway_refuses_a_foreign_origin_before_any_request(tmp_path: Path) -> None:
    """The waiter sends the CI token, so it must apply the same origin check the
    driver does: a real workstation Gateway on 8088 must not be contacted."""
    waiter = _waiter()
    for base_url, mcp_url in (
        ("http://127.0.0.1:8088", "http://127.0.0.1:8088" + driver.EXPECTED_MCP_PATH),
        (f"http://127.0.0.1:{driver.EXPECTED_ORIGIN_PORT}", f"http://127.0.0.1:{driver.EXPECTED_ORIGIN_PORT}/data/mcp/other"),
        ("http://192.0.2.10:8093", "http://192.0.2.10:8093" + driver.EXPECTED_MCP_PATH),
    ):
        code = waiter.main([
            "--base-url", base_url,
            "--api-token", API_TOKEN,
            "--mcp-url", mcp_url,
            "--timeout", "1",
        ])
        assert code == waiter.EXIT_ORIGIN_REFUSED, (base_url, code)


def test_wait_for_gateway_reports_readiness_against_the_recorded_fake(
    disposable_gateway: Any, tmp_path: Path,
) -> None:
    waiter = _waiter()
    code = waiter.main([
        "--base-url", disposable_gateway.base_url,
        "--api-token", API_TOKEN,
        "--mcp-url", disposable_gateway.base_url + driver.EXPECTED_MCP_PATH,
        "--timeout", "10",
        "--evidence-dir", str(tmp_path),
    ])
    assert code == waiter.EXIT_READY
    assert (tmp_path / "gateway-info.json").is_file()


class _ResetOnly(socketserver.BaseRequestHandler):
    """A Gateway port that accepts and drops connections while it starts."""

    def handle(self) -> None:
        self.request.close()


def test_clients_survive_a_gateway_that_resets_connections(tmp_path: Path) -> None:
    """The recorded live failure: the readiness wait crashed with
    ConnectionResetError because a socket error escaped the REST client."""
    waiter = _waiter()
    try:
        reset = http.server.ThreadingHTTPServer(("127.0.0.1", driver.EXPECTED_ORIGIN_PORT), _ResetOnly)
    except OSError as error:  # pragma: no cover - depends on the workstation
        pytest.skip(f"127.0.0.1:{driver.EXPECTED_ORIGIN_PORT} is not available: {error}")
    thread = threading.Thread(target=reset.serve_forever, daemon=True)
    thread.start()
    try:
        origin = f"http://127.0.0.1:{driver.EXPECTED_ORIGIN_PORT}"
        with pytest.raises(gateway_rest.RestError):
            gateway_rest.gateway_info(origin, API_TOKEN)
        with pytest.raises(mcp_client.McpError):
            mcp_client.McpClient(origin + driver.EXPECTED_MCP_PATH, API_TOKEN, timeout=5.0).initialize()
        code = waiter.main([
            "--base-url", origin,
            "--api-token", API_TOKEN,
            "--mcp-url", origin + driver.EXPECTED_MCP_PATH,
            "--timeout", "1",
        ])
        assert code == waiter.EXIT_NOT_READY
    finally:
        reset.shutdown()
        reset.server_close()
        thread.join(timeout=5)


def test_wait_for_gateway_times_out_when_the_origin_does_not_answer(tmp_path: Path) -> None:
    """The accepted origin with nothing listening is 'not ready', not a refusal."""
    waiter = _waiter()
    free = False
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", driver.EXPECTED_ORIGIN_PORT))
                free = True
                break
            except OSError:
                time.sleep(0.2)
    if not free:  # pragma: no cover - depends on the workstation
        pytest.skip(f"127.0.0.1:{driver.EXPECTED_ORIGIN_PORT} never became free")
    origin = f"http://127.0.0.1:{driver.EXPECTED_ORIGIN_PORT}"
    code = waiter.main([
        "--base-url", origin,
        "--api-token", API_TOKEN,
        "--mcp-url", origin + driver.EXPECTED_MCP_PATH,
        "--timeout", "1",
    ])
    assert code == waiter.EXIT_NOT_READY


def test_rehearsal_propagates_drift_from_the_summarize_stage(tmp_path: Path) -> None:
    """Exit 3 is the pre-live drift signal, so the rehearsal must not swallow it."""
    rehearsal = _load("phase4_rehearse_local", PHASE4 / "rehearse_local.py")
    drifted = json.loads((PHASE4 / "characterization.json").read_text(encoding="utf-8"))
    drifted["8.3.8"]["policyGateState"] = "blocked"
    path = tmp_path / "characterization.json"
    path.write_text(json.dumps(drifted, indent=2) + "\n", encoding="utf-8")
    try:
        code = rehearsal.main(["--characterization", str(path)])
    except OSError as error:  # pragma: no cover - depends on the workstation
        pytest.skip(f"127.0.0.1:{driver.EXPECTED_ORIGIN_PORT} is not available: {error}")
    assert code == rehearsal.EXIT_DRIFTED


def test_rehearsal_is_clean_on_the_committed_fixtures() -> None:
    rehearsal = _load("phase4_rehearse_local", PHASE4 / "rehearse_local.py")
    try:
        code = rehearsal.main([])
    except OSError as error:  # pragma: no cover - depends on the workstation
        pytest.skip(f"127.0.0.1:{driver.EXPECTED_ORIGIN_PORT} is not available: {error}")
    assert code == rehearsal.EXIT_OK


def test_phase4_live_workflow_is_guarded_and_environment_scoped() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "pull_request:" in text
    assert "environment: phase4-live" in text
    assert "github.event.pull_request.head.repo.full_name == github.repository" in text
    for stage in ("tag-write-no-policy", "policy-provision", "policy-read", "alarm",
                  "tag-write-setup", "tag-write", "summarize"):
        assert stage in text, stage
    assert "docker compose -f \"$COMPOSE_FILE\" down -v --remove-orphans" in text
    assert "python tests/harness/phase4-live/rehearse_local.py" in text
    # Two readiness points, each waiting on both hosted endpoints.
    assert text.count("wait_for_gateway.py") == 2
    assert "MAX_RESPONSE" not in text
    # Frozen expectations: drift must fail the job now.
    assert 'if [[ "$rc" == "3" ]]; then' in text
    assert 'echo "characterization drifted' in text


def test_the_live_fingerprint_verifier_reproduces_the_golden_vectors() -> None:
    """The driver recomputes the published fingerprint with the contracts linter's
    own copy of the D30 rule. The published configuration is already D28 encoded,
    so the verifier hashes it as it stands: a null marker or a literal `$ignition`
    object must not be escaped a second time."""
    document = json.loads(
        (ROOT / "contracts/shared/tag-config-fingerprint.json").read_text(encoding="utf-8")
    )
    for vector in document["goldenVectors"]:
        assert driver.derived_fingerprint(vector["configuration"]) == vector["fingerprint"], vector["name"]
    # The two vectors that reach the encoding rule are the ones the double encoding
    # would break, so the check is not vacuous.
    names = {vector["name"] for vector in document["goldenVectors"]}
    assert {"explicit-null-property", "escaped-reserved-key-object"} <= names


def test_phase4_g4b_workflow_is_guarded_and_environment_scoped() -> None:
    text = WORKFLOW_G4B.read_text(encoding="utf-8")
    assert "pull_request:" in text
    assert "environment: phase4-live" in text
    assert "github.event.pull_request.head.repo.full_name == github.repository" in text
    for stage in ("tag-update-no-policy", "policy-provision", "tag-update-setup", "tag-update", "summarize"):
        assert stage in text, stage
    assert "docker compose -f \"$COMPOSE_FILE\" down -v --remove-orphans" in text
    assert "rehearse_local.py --stages 4b" in text
    assert 'P4_MILESTONE: "4b"' in text
    assert "P4_MARKER_LABEL: g4b" in text
    assert '"gatewayId": "phase4-g4b-${GATEWAY_VERSION}-${GITHUB_RUN_ID}"' in text
    assert "phase4-configurator" in text
    assert "MAX_RESPONSE" not in text
    assert 'if [[ "$rc" == "3" ]]; then' in text
    assert "milestone 4b drifted" in text


@pytest.mark.parametrize("profile", ["operator", "configurator"])
def test_the_deployed_server_config_lists_the_profile_exactly(profile: str) -> None:
    """D09: a deployment selects its profile's explicit list, never a wildcard.

    The frozen G1-G3 harnesses used `*` while the bundle happened to hold exactly
    the read-only Tools; once the bundle carries Mutations, a wildcard serves a
    Mutation from a deployment that did not ask for it, so each Phase 4 Server
    Config has to equal its profile contract.
    """
    document = json.loads((SERVER_CONFIGS / f"phase4-{profile}" / "config.json").read_text(encoding="utf-8"))
    contract = json.loads((ROOT / "contracts/profiles" / f"{profile}.yaml").read_text(encoding="utf-8"))

    assert document["tools"] == {"project/ignition_runtime": contract["tools"]}
    bundle_version = (ROOT / "packages/ignition-runtime-bundle/BUNDLE_VERSION").read_text(encoding="utf-8").strip()
    assert document["version"] == bundle_version


def test_the_forbidden_profile_never_carries_a_mutation_of_another_class() -> None:
    """CONTROL is not CONFIG: the operator deployment must not serve `tag_update`."""
    operator = json.loads((SERVER_CONFIGS / "phase4-operator" / "config.json").read_text(encoding="utf-8"))
    configurator = json.loads((SERVER_CONFIGS / "phase4-configurator" / "config.json").read_text(encoding="utf-8"))

    assert "tag_update" in configurator["tools"]["project/ignition_runtime"]
    assert "tag_update" not in operator["tools"]["project/ignition_runtime"]
    assert "tag_write" in operator["tools"]["project/ignition_runtime"]
    assert "tag_write" not in configurator["tools"]["project/ignition_runtime"]
