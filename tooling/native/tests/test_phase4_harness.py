from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
PHASE4 = ROOT / "tests/harness/phase4-live"
FIXTURES = ROOT / "tests/fixtures/recorded/gateway-8.3/phase4"
COMPOSE = PHASE4 / "docker-compose.yml"
WORKFLOW = ROOT / ".github/workflows/phase4-live-g4a.yml"
POLICY_SHA256 = "b98bedf5a697fcf178dfad7cdcbae1e40c4be57071d57674f6cdf11487ba58f6"

sys.path.insert(0, str(ROOT / "tests/harness"))
sys.path.insert(0, str(PHASE4))

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


def _fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _config(evidence: Path, **overrides: Any) -> Any:
    values: dict[str, Any] = {
        "base_url": "http://127.0.0.1:1",
        "api_token": "stub-token",
        "mcp_url": "http://127.0.0.1:1/data/mcp/phase4-policy-probe",
        "evidence_dir": evidence,
        "run_id": "1",
        "gateway_version": "8.3.8",
        "gateway_build": "2026071409",
        "root_name": "MCP_P4_1",
    }
    values.update(overrides)
    return driver.Config(**values)


class _StubMcp:
    """Stands in for the live Module-hosted endpoint using recorded payloads."""

    reports: dict[str, Any] = {}

    def __init__(self, url: str, token: str, **_kwargs: Any) -> None:
        self.url = url

    def initialize(self) -> dict[str, Any]:
        return {"protocolVersion": "2025-06-18", "serverInfo": {"name": "ignition-runtime"}}

    def tools_list(self) -> list[str]:
        return ["alarm_probe", "policy_probe"]

    def structured(self, name: str, _arguments: dict[str, Any]) -> dict[str, Any]:
        return self.reports[name]


@pytest.fixture()
def stub_mcp(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    reports = {"policy_probe": _fixture("policy-probe.json"), "alarm_probe": _fixture("alarm-probe.json")}
    _StubMcp.reports = reports
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
    assert facts["perPathCountStableAcrossCycles"] is True
    assert facts["exactPathBoundedBasis"]["literalMatchingOnly"] is True


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
    """Write the four stage records the way the live workflow does."""
    evidence.mkdir(parents=True, exist_ok=True)
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
    return alarm


def test_summarize_reports_no_drift_and_a_bounded_verdict(
    stub_mcp: dict[str, Any], tmp_path: Path,
) -> None:
    with RecordedGateway(policy_provider=policy_document.POLICY_PROVIDER) as gateway:
        _record_every_stage(tmp_path, gateway)
    evidence, code = driver.stage_summarize(_config(tmp_path))
    assert code == driver.EXIT_OK
    assert evidence["drift"] == {}
    assert evidence["verdict"]["exactPathAlarmQuery"]["bounded"] is True
    assert evidence["verdict"]["runtimeTargetPolicyStorage"]["chosenLocation"] == "[IgnitionMCPPolicy]RuntimeTargetPolicy"


def test_summarize_detects_a_descendant_matching_regression(
    stub_mcp: dict[str, Any], tmp_path: Path,
) -> None:
    with RecordedGateway(policy_provider=policy_document.POLICY_PROVIDER) as gateway:
        alarm = _record_every_stage(tmp_path, gateway)
    alarm["facts"]["folderPathExpandsDescendants"] = True
    _record_stage(tmp_path, "alarm", alarm)
    evidence, code = driver.stage_summarize(_config(tmp_path))
    assert code == driver.EXIT_DRIFTED
    assert "folderPathExpandsDescendants" in evidence["drift"]
    assert evidence["verdict"]["exactPathAlarmQuery"]["bounded"] is False


def test_driver_guard_fails_closed_on_a_missing_or_mismatched_marker(tmp_path: Path) -> None:
    with RecordedGateway(policy_provider=policy_document.POLICY_PROVIDER) as gateway:
        config = _config(tmp_path, base_url=gateway.base_url, api_token=API_TOKEN)
        with pytest.raises(driver.GuardError):
            driver.verify_guard(config)

        marker = tmp_path / "ci-marker.json"
        marker.write_text(json.dumps({
            "marker": "ignition-mcp-phase4-live",
            "environment": "phase4-live",
            "runId": "9999",
            "gatewayVersion": "8.3.8",
            "gatewayBuild": "2026071409",
            "trustedRepo": "sheon-sek/ignition-mcp",
        }), encoding="utf-8")
        with pytest.raises(driver.GuardError):
            driver.verify_guard(driver.Config(**{**config.__dict__, "ci_marker": marker}))

        marker.write_text(json.dumps({
            "marker": "ignition-mcp-phase4-live",
            "environment": "phase4-live",
            "runId": "1",
            "gatewayVersion": "8.3.8",
            "gatewayBuild": "2026071409",
            "trustedRepo": "someone-else/ignition-mcp",
        }), encoding="utf-8")
        with pytest.raises(driver.GuardError):
            driver.verify_guard(driver.Config(**{**config.__dict__, "ci_marker": marker}))


def test_phase4_live_workflow_is_guarded_and_environment_scoped() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "pull_request:" in text
    assert "environment: phase4-live" in text
    assert "github.event.pull_request.head.repo.full_name == github.repository" in text
    for stage in ("policy-provision", "policy-read", "alarm", "summarize"):
        assert stage in text, stage
    assert "docker compose -f \"$COMPOSE_FILE\" down -v --remove-orphans" in text
    assert "python tests/harness/phase4-live/rehearse_local.py" in text
