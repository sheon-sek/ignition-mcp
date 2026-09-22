from __future__ import annotations

import ast
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


#: Loaded under its own name: `wait_for_gateway` and the rehearsal import `driver`,
#: and one module instance means one retry clock and one monkeypatch target.
driver = _load("driver", PHASE4 / "driver.py")
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
        "writeProbe": policy_document.WRITE_PROBE_PATH,
        "siblingTarget": policy_document.TAG_FIXTURE_SIBLING_PATH,
        "missingTarget": policy_document.TAG_FIXTURE_MISSING_PATH,
        "udtTarget": policy_document.TAG_UPDATE_UDT_TARGET,
    }


class _VirtualClock:
    """A retry clock that moves only when a loop waits for it.

    The interval the loop asks for is the live one, so every deadline, elapsed
    measurement and attempt count stays what a live run would produce; only the
    wall time the waiting costs disappears.
    """

    def __init__(self) -> None:
        self.seconds = 0.0
        self.waits: list[float] = []

    def now(self) -> float:
        return self.seconds

    def wait(self, seconds: float) -> None:
        self.waits.append(seconds)
        self.seconds += seconds


@pytest.fixture(autouse=True)
def retry_clock(monkeypatch: pytest.MonkeyPatch) -> _VirtualClock:
    """Run every case in this module on the virtual clock instead of the wall one.

    The wall clock is what a live Gateway needs (2 s and 3 s intervals in
    `driver`), and this fake answers at once, so the harness cases would otherwise
    spend those intervals per retry: the whole file is minutes of sleeping. The
    injected clock keeps the retries and their intervals and removes the waiting.
    """
    clock = _VirtualClock()
    monkeypatch.setattr(driver, "CLOCK", clock)
    return clock


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
    stub_mcp: dict[str, Any], tmp_path: Path, retry_clock: _VirtualClock,
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
    # The rejected import and the repair both waited the live settle interval, so
    # the two attempts are the live two.
    assert retry_clock.waits == [driver.PROVIDER_SETTLE_RETRY_SECONDS] * 2


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


def test_the_handler_read_gate_opens_an_mcp_session(tmp_path: Path) -> None:
    """The Module refuses `tools/call` with HTTP 400 (`Session is required for
    method: tools/call`) until the caller has initialized, which live run
    35667242361 recorded for a gate that called the probe without a session: 61
    attempts, all refused, and the provision stage failed on both rows. This
    endpoint enforces the same rule against the real `McpClient`."""
    sessions: list[str] = []

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
            method = str(payload.get("method", ""))
            if method == "initialize":
                session = "mcp-session-1"
                sessions.append(session)
                self._json({
                    "jsonrpc": "2.0", "id": payload.get("id"),
                    "result": {"protocolVersion": "2025-06-18", "serverInfo": {"name": "probe"}},
                }, session)
                return
            if self.headers.get("Mcp-Session-Id") not in sessions or not sessions:
                body = json.dumps({
                    "message": f"Session is required for method: {method}",
                    "url": self.path.split("?")[0],
                    "status": "400",
                }).encode()
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            report = json.loads((FIXTURES / "policy-probe.json").read_text(encoding="utf-8"))
            for entry in report["measurements"]:
                if entry["name"] == "tag.readBlocking.missing":
                    entry["items"] = [{"quality": "Bad_NotFound", "valueType": "NoneType"}]
            self._json({
                "jsonrpc": "2.0", "id": payload.get("id"),
                "result": {"content": [{"type": "text", "text": "ok"}], "structuredContent": report},
            }, None)
            return

        def _json(self, value: Any, session: str | None) -> None:
            body = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            if session:
                self.send_header("Mcp-Session-Id", session)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:
            return

    class _Server(socketserver.ThreadingTCPServer):
        daemon_threads = True
        allow_reuse_address = True

    server = _Server(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        config = _config(
            tmp_path, base_url=f"http://127.0.0.1:{server.server_address[1]}",
            provider_ready_deadline_seconds=5.0,
        )
        readiness = driver.wait_for_handler_read(config)
    finally:
        server.shutdown()
        server.server_close()

    assert readiness["serving"] is True
    assert readiness["attempts"] == 1
    assert sessions == ["mcp-session-1"]


def test_policy_provision_gates_the_import_on_a_handler_read(tmp_path: Path, retry_clock: _VirtualClock) -> None:
    """REST readiness cannot see a provider that is still loading its Tags, and an
    import applied in that window leaves a Tag whose actor never starts (live run
    35654626095). The provision stage must prove the provider serves a handler read
    before it imports anything."""
    with RecordedGateway(
        policy_provider=policy_document.POLICY_PROVIDER,
        policy_provider_unready_reads=2,
    ) as gateway:
        config = _config(
            tmp_path, base_url=gateway.base_url, api_token=API_TOKEN,
            provider_ready_deadline_seconds=30.0,
        )
        record = driver.stage_policy_provision(config)
    facts = record["facts"]
    assert facts["providerHandlerReadAttempts"] == 3
    # The two probes that answered "not serving" each waited the live interval, so
    # the attempt count above is the live one with the waiting taken out.
    assert retry_clock.waits[:2] == [driver.PROVIDER_READY_RETRY_SECONDS] * 2
    assert facts["providerHandlerReadServing"] is True
    assert facts["providerHandlerReadQuality"].startswith("Bad_NotFound")
    assert facts["policyImported"] is True
    kinds = [
        "import" if request["path"].startswith("/data/api/v1/tags/import")
        else "probe" if request["path"].startswith("/data/mcp/")
        else "other"
        for request in gateway.requests
    ]
    # Two probes answered "not serving" and the third was, so no import may appear
    # before the third probe.
    assert kinds[: kinds.index("import")].count("probe") >= 3


def test_policy_provision_fails_when_the_provider_never_serves_a_handler_read(tmp_path: Path) -> None:
    with RecordedGateway(
        policy_provider=policy_document.POLICY_PROVIDER,
        policy_provider_unready_reads=-1,
    ) as gateway:
        config = _config(
            tmp_path, base_url=gateway.base_url, api_token=API_TOKEN,
            provider_ready_deadline_seconds=1.0,
        )
        with pytest.raises(driver.StageFailure) as caught:
            driver.stage_policy_provision(config)
    assert "never served a handler-scope read" in str(caught.value)


def test_policy_read_does_not_re_import_into_a_provider_that_serves_nothing(
    stub_mcp: dict[str, Any], tmp_path: Path,
) -> None:
    """The recorded 8.3.9 failure: the provider answers no handler read at all, so
    another config-plane import cannot help, and the stage must say so instead of
    spending its deadline on futile repairs."""
    unserved = json.loads(json.dumps(_fixture("policy-probe.json")))
    for entry in unserved["measurements"]:
        if entry["name"] in {"tag.readBlocking.policy", "tag.readBlocking.missing"}:
            entry["items"] = [{
                "quality": "Error_Configuration", "valueType": "NoneType",
                "valueLength": 0, "valueByteLength": 0, "valueSha256": "", "valuePrefix": "",
            }]
            entry["jsonKeys"] = []
            entry["jsonKind"] = ""
    with RecordedGateway(policy_provider=policy_document.POLICY_PROVIDER) as gateway:
        config = _config(
            tmp_path, base_url=gateway.base_url, api_token=API_TOKEN,
            policy_read_deadline_seconds=0.0,
        )
        driver.stage_policy_provision(config)

        def imports() -> int:
            return len([
                request for request in gateway.requests
                if request["path"].startswith("/data/api/v1/tags/import")
            ])

        before = imports()
        _StubMcp.reports = {"policy_probe": unserved, "alarm_probe": _fixture("alarm-probe.json")}
        _StubMcp.sequences = {}
        with pytest.raises(driver.StageFailure) as caught:
            driver.stage_policy_read(config)
        assert imports() == before
    assert "is not serving Tags at all" in str(caught.value)


def test_a_served_gate_label_without_a_good_value_read_is_not_verified(
    stub_mcp: dict[str, Any], tmp_path: Path,
) -> None:
    """The probe labels its state "served" once it has read a value; whether the
    provider actually served that value is the quality of the read it made. The
    live 8.3.9 failure recorded a "served" gate whose value read was
    Error_Configuration, so the verification must read the quality too."""
    unserved = json.loads(json.dumps(_with_gate(_fixture("policy-probe.json"))))
    for entry in unserved["measurements"]:
        if entry["name"] == "tag.gatedRead.policy":
            entry["quality"] = 'Error_Configuration("The Tag provider is not serving tags.")'
            entry["lengthMatchesValue"] = False
            entry["valueByteLength"] = 0
            entry["valueSha256"] = ""
    _StubMcp.reports = {"policy_probe": unserved, "alarm_probe": _fixture("alarm-probe.json")}
    _StubMcp.sequences = {}
    config = _config(tmp_path, policy_read_deadline_seconds=0.0)
    with pytest.raises(driver.StageFailure) as caught:
        driver.stage_policy_read(config)
    facts = driver.derive_policy_read_facts(config, unserved)
    assert facts["policyGateState"] == "served"
    assert facts["policyGateValueQualityIsGood"] is False
    assert facts["policyGateUnserved"] is True
    assert facts["policyGatedReadServedAndVerified"] is False
    assert "the policy gate did not verify the served document" in str(caught.value)


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
        tag_create_paths=driver.tag_create_paths(),
        tag_copy_paths=driver.tag_copy_paths(),
        tag_delete_paths=driver.tag_delete_paths(),
        tag_move_paths=driver.tag_move_paths(),
        tag_rename_paths=driver.tag_rename_paths(),
    ) as gateway:
        config = _config(tmp_path, base_url=gateway.base_url, api_token=API_TOKEN, stages=driver.MILESTONE_4B)
        stages = [
            ("tag-update-no-policy", driver.stage_tag_update_no_policy),
            ("policy-provision", driver.stage_policy_provision),
            ("tag-update-setup", driver.stage_tag_update_setup),
            ("tag-update", driver.stage_tag_update),
            ("tag-create", driver.stage_tag_create),
            ("tag-copy", driver.stage_tag_copy),
            ("tag-move", driver.stage_tag_move),
            ("tag-rename", driver.stage_tag_rename),
            ("tag-delete", driver.stage_tag_delete),
        ]
        for name, stage in stages:
            _record_stage(tmp_path, name, stage(config))
    evidence, code = driver.stage_summarize(config)
    assert code == driver.EXIT_OK
    assert evidence["milestone"] == driver.MILESTONE_4B
    assert evidence["tickets"] == ["#10", "#11", "#12"]
    assert evidence["drift"] == {}
    assert evidence["verdict"]["runtimeTagConfigMutation"]["update"]["status"] == "executed"
    assert evidence["verdict"]["runtimeTagConfigMutation"]["staleFingerprint"]["changedNothing"] is True


def test_tag_create_case_selector_replays_the_recorded_refusals() -> None:
    """The rehearsal's `tag_create` selection mirrors the shipped handler's own order.

    The two D10 ceilings a request crosses with no native call come first, then the
    policy gate, then the deployment's ceiling, the reserved provider, the allowlist
    with D30 6's `_types_` rule, and last the existence check a create answers by being
    absent. Every selected case has to be a body the fake can actually replay.
    """

    class _Base:
        policy_provider_created = True
        policy_value = ""
        tag_create_paths: dict[str, str] = {}
        tag_config = {
            policy_document.TAG_CREATE_EXISTING_TARGET: [{"name": "WriteTarget", "value": 0}],
        }

    def serve(policy: dict[str, Any]) -> Any:
        return type("Server", (_Base,), {
            "policy_value": json.dumps(policy, sort_keys=True, separators=(",", ":")),
        })()

    def create(*paths: str) -> dict[str, Any]:
        return {"items": [
            {"path": path, "config": dict(policy_document.TAG_CREATE_CONFIG)} for path in paths
        ]}

    plain = serve(policy_document.tag_create_policy())
    types = serve(policy_document.tag_create_policy(
        allowlist=policy_document.TAG_CREATE_TYPES_ALLOWLIST,
    ))
    wildcard = serve(policy_document.tag_create_policy(allowlist=policy_document.WILDCARD_ALLOWLIST))
    ceiling = serve(policy_document.tag_create_policy(max_items=1))
    # A Gateway where nothing is seeded yet: the only state that lets a create execute.
    absent = serve(policy_document.tag_create_policy())
    absent.tag_config = {}

    cases = [
        (absent, create(policy_document.TAG_CREATE_TARGET), "created"),
        (plain, create(policy_document.TAG_CREATE_EXISTING_TARGET), "target-exists"),
        (plain, create(policy_document.TAG_CREATE_BATCH_TARGET,
                       policy_document.TAG_CREATE_EXISTING_TARGET), "target-exists"),
        (plain, create(policy_document.TAG_CREATE_SIBLING_TARGET), "sibling-denial"),
        (plain, create(policy_document.TAG_CREATE_BATCH_TARGET,
                       policy_document.TAG_CREATE_SIBLING_TARGET), "preflight-refusal"),
        (plain, create(policy_document.TAG_CREATE_UDT_TARGET), "udt-not-allowlisted"),
        (wildcard, create(policy_document.TAG_CREATE_UDT_TARGET), "udt-not-allowlisted"),
        (wildcard, create(policy_document.WRITE_PROBE_PATH), "reserved-provider-refusal"),
        # The entry is what changed: the definition item passes and only the sibling is
        # listed, so a refused batch dispatches nothing on either item.
        (types, create(policy_document.TAG_CREATE_UDT_TARGET,
                       policy_document.TAG_CREATE_SIBLING_TARGET), "preflight-refusal"),
        (plain, create(*[policy_document.TAG_CREATE_TARGET] * 21), "over-policy-limit"),
        (ceiling, create(policy_document.TAG_CREATE_TARGET,
                         policy_document.TAG_CREATE_BATCH_TARGET), "over-policy-limit"),
        (plain, create(*[policy_document.TAG_CREATE_TARGET] * 101), "items-over-hard-limit"),
        (plain, create(f"[{policy_document.TAG_FIXTURE_PROVIDER}]"
                       f"{policy_document.TAG_FIXTURE_ROOT}/{policy_document.OVERLONG_PATH_LEAF}"),
         "path-over-length"),
    ]
    for server, arguments, expected in cases:
        case, paths = recorded_gateway._tag_create_case(server, arguments)
        assert case == expected, (arguments, expected, case)
        assert paths == [item["path"] for item in arguments["items"]]
        if case != "created":
            assert (FIXTURES / f"tag-create-{case}.json").is_file(), case

    class _NoPolicy(_Base):
        policy_provider_created = False
        policy_value = ""

    case, _paths = recorded_gateway._tag_create_case(
        _NoPolicy(), create(policy_document.TAG_CREATE_TARGET),
    )
    assert case == "no-policy"
    # The ceilings are measured over the request, so they answer before the gate does.
    assert recorded_gateway._tag_create_case(
        _NoPolicy(), create(*[policy_document.TAG_CREATE_TARGET] * 101),
    )[0] == "items-over-hard-limit"


def test_tag_copy_case_selector_replays_the_recorded_refusals() -> None:
    """The rehearsal's `tag_copy` selection keeps the leaf rule first and the source exempt."""

    class _Base:
        policy_provider_created = True
        policy_value = ""
        tag_copy_paths: dict[str, str] = {}
        tag_config = {
            policy_document.TAG_COPY_SOURCE: [{"name": "WriteTarget", "value": 0}],
            policy_document.TAG_COPY_SIBLING_SOURCE: [{"name": "WriteTarget", "value": 0}],
        }

    def serve(policy: dict[str, Any], **config: Any) -> Any:
        return type("Server", (_Base,), {
            "policy_value": json.dumps(policy, sort_keys=True, separators=(",", ":")),
            "tag_config": dict(_Base.tag_config, **config),
        })()

    def pair(source: str = "", destination: str = "") -> dict[str, Any]:
        return {"items": [{
            "sourcePath": source or policy_document.TAG_COPY_SOURCE,
            "destinationPath": destination or policy_document.TAG_COPY_DESTINATION,
        }]}

    def batch(*destinations: str) -> dict[str, Any]:
        return {"items": [pair()["items"][0] | {"destinationPath": d} for d in destinations]}

    plain = serve(policy_document.tag_copy_policy())
    occupied = serve(policy_document.tag_copy_policy(), **{
        policy_document.TAG_COPY_DESTINATION: [{"name": "WriteTarget"}],
    })
    types = serve(policy_document.tag_copy_policy(allowlist=policy_document.TAG_COPY_TYPES_ALLOWLIST))
    wildcard = serve(policy_document.tag_copy_policy(allowlist=policy_document.WILDCARD_ALLOWLIST))
    ceiling = serve(policy_document.tag_copy_policy(max_items=1))

    cases = [
        (plain, pair(), "copied"),
        # A copy is not a move, and an occupied destination is the collision it refuses.
        (occupied, pair(), "destination-exists"),
        (plain, pair(policy_document.TAG_COPY_MISSING_SOURCE,
                     policy_document.TAG_COPY_MISSING_SOURCE_DESTINATION), "source-missing"),
        # The source is exempt: the same Tag one segment outside the allowed prefix is
        # answered by the endpoint stage, never by the allowlist one.
        (occupied, pair(source=policy_document.TAG_COPY_SIBLING_SOURCE), "destination-exists"),
        (plain, pair(destination=policy_document.TAG_COPY_SIBLING_DESTINATION), "sibling-denial"),
        (plain, pair(destination=policy_document.TAG_COPY_UDT_DESTINATION), "udt-not-allowlisted"),
        (wildcard, pair(destination=policy_document.TAG_COPY_UDT_DESTINATION), "udt-not-allowlisted"),
        (wildcard, pair(policy_document.TAG_COPY_RESERVED_SOURCE,
                        policy_document.TAG_COPY_RESERVED_SOURCE_DESTINATION),
         "reserved-source-refusal"),
        (wildcard, pair(destination=policy_document.TAG_COPY_RESERVED_DESTINATION),
         "reserved-destination-refusal"),
        (types, batch(policy_document.TAG_COPY_UDT_DESTINATION,
                      policy_document.TAG_COPY_SIBLING_DESTINATION),
         "preflight-refusal"),
        (plain, batch(policy_document.TAG_COPY_UDT_DESTINATION,
                      policy_document.TAG_COPY_SIBLING_DESTINATION),
         "udt-not-allowlisted"),
        (plain, {"items": [pair()["items"][0]] * 21}, "over-policy-limit"),
        (ceiling, {"items": [pair()["items"][0]] * 2}, "over-policy-limit"),
        (plain, {"items": [pair()["items"][0]] * 101}, "items-over-hard-limit"),
        # The ceiling is measured after the input pass, so a destination that is both
        # over-long and leaf-mismatched reports the leaf — the order the handler takes
        # and the reason the live over-budget case keeps the source's leaf.
        (plain, pair(destination=f"[{policy_document.TAG_FIXTURE_PROVIDER}]"
                                 f"{policy_document.TAG_FIXTURE_ROOT}/"
                                 f"{policy_document.OVERLONG_PATH_LEAF}"),
         "destination-leaf-mismatch"),
        (plain, pair(destination=f"[{policy_document.TAG_FIXTURE_PROVIDER}]"
                                 f"{policy_document.TAG_FIXTURE_ROOT}/"
                                 f"{policy_document.OVERLONG_PATH_LEAF}/WriteTarget"),
         "path-over-length"),
    ]
    for server, arguments, expected in cases:
        case, _paths = recorded_gateway._tag_copy_case(server, arguments)
        assert case == expected, (arguments, expected, case)
        if case != "copied":
            assert (FIXTURES / f"tag-copy-{case}.json").is_file(), case

    # The leaf rule is an input rule, so it is refused even where an allowlist refusal
    # would otherwise follow.
    leaf_cases = [
        (plain, pair(destination=f"{policy_document.TAG_COPY_DESTINATION}Renamed"),
         "destination-leaf-mismatch"),
        (plain, pair(destination=policy_document.TAG_COPY_SIBLING_DESTINATION + "Renamed"),
         "destination-leaf-mismatch"),
        # A leaf mismatch outranks everything else, including an allowlist refusal of
        # the same destination.
        (plain, batch(f"{policy_document.TAG_COPY_DESTINATION}Renamed",
                      policy_document.TAG_COPY_SIBLING_DESTINATION),
         "destination-leaf-mismatch"),
    ]
    for server, arguments, expected in leaf_cases:
        case, _paths = recorded_gateway._tag_copy_case(server, arguments)
        assert case == expected, (arguments, expected, case)

    class _NoPolicy(_Base):
        policy_provider_created = False
        policy_value = ""

    assert recorded_gateway._tag_copy_case(_NoPolicy(), pair())[0] == "no-policy"
    # The gate is read before the endpoints: a source that is not there answers
    # `sourceMissing` only once a usable Policy exists.
    assert recorded_gateway._tag_copy_case(
        _NoPolicy(), pair(policy_document.TAG_COPY_MISSING_SOURCE,
                          policy_document.TAG_COPY_MISSING_SOURCE_DESTINATION),
    )[0] == "no-policy"


def _ticket_11_records(tmp_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """The two ticket #11 stage records, over the chain both tickets provision.

    The Tools run on the fixtures and audit profile `tag-update-setup` seeds, so a test
    that wants their evidence replays that whole chain; this is where it lives.
    """
    with RecordedGateway(
        policy_provider=policy_document.POLICY_PROVIDER,
        runtime_tools=("policy_probe", "alarm_probe", "tag_fixture_probe"),
        audit_profile=policy_document.AUDIT_PROFILE_NAME,
        alarm_root=ALARM_ROOT,
        tag_update_paths=_tag_update_paths(),
        tag_create_paths=driver.tag_create_paths(),
        tag_copy_paths=driver.tag_copy_paths(),
    ) as gateway:
        config = _config(tmp_path, base_url=gateway.base_url, api_token=API_TOKEN,
                         stages=driver.MILESTONE_4B)
        driver.stage_tag_update_no_policy(config)
        driver.stage_policy_provision(config)
        driver.stage_tag_update_setup(config)
        driver.stage_tag_update(config)
        return driver.stage_tag_create(config), driver.stage_tag_copy(config)


def _ticket_11_facts(tmp_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    create, copy = _ticket_11_records(tmp_path)
    return create["facts"], copy["facts"]


def test_tag_create_stage_records_the_live_facts(tmp_path: Path) -> None:
    """`tag_create` creates, refuses a collision, and never executes part of a batch."""
    create, _copy = _ticket_11_facts(tmp_path)
    # The CONFIG class is deployed on the configurator profile and nowhere else.
    assert create["tagCreateConfiguratorInventoryMatchesProfile"] is True
    assert create["tagCreateConfiguratorCarriesBothTools"] is True
    assert create["tagCreateOperatorInventoryExcludesBothTools"] is True
    # The created node is what an independent read and the provider's export show, and
    # the Observed fingerprint is the token that read publishes.
    assert create["tagCreateStatus"] == "executed"
    assert create["tagCreateNativeOutcome"] == "Good"
    assert create["tagCreateIndependentReadShowsTheNode"] is True
    assert create["tagCreateObservedFingerprintIsIndependentRead"] is True
    assert create["tagCreateObservedFingerprintIsDerivable"] is True
    assert create["tagCreateNodeVisibleInExport"] is True
    assert create["tagCreateAuditRowsForCorrelation"] == 2
    assert create["tagCreateAuditActorIsServiceIdentity"] is True
    # An existing target is the collision a create refuses, and it changes nothing.
    assert create["tagCreateCollisionIsConflict"] is True
    assert create["tagCreateCollisionChangedNothing"] is True
    assert create["tagCreateCollisionAuditRecorded"] is True
    assert create["tagCreateBatchTargetAbsentFromExport"] is True
    # The allowlist refusal is at a segment boundary, and one refused item refuses the
    # batch: the allowed item stays absent.
    assert create["tagCreateSiblingDenialIsSegmentBoundary"] is True
    assert create["tagCreateSiblingDenialAuditRecorded"] is True
    assert create["tagCreatePreflightRefusalNamesOnlyTheRefusedEnd"] is True
    assert create["tagCreatePreflightExecutedNothing"] is True
    # D30 6 both ways, the reserved provider under an explicit *, and every D10 ceiling.
    assert create["tagCreateUdtNeedsExplicitTypesEntry"] is True
    assert create["tagCreateBareWildcardDoesNotCoverUdt"] is True
    assert create["tagCreateTypesEntryIsHonoured"] is True
    assert create["tagCreateTypesPreflightExecutedNothing"] is True
    assert create["tagCreateReservedProviderRefusedUnderWildcard"] is True
    assert create["tagCreateReservedProviderValueUnchanged"] is True
    assert create["tagCreatePolicyDocumentUnclobbered"] is True
    assert create["tagCreatePathOverCeilingNamesTheCeiling"] is True
    assert create["tagCreateHardItemCeilingIsRefused"] is True
    assert create["tagCreateOverPolicyLimitIsRefused"] is True
    assert create["tagCreatePolicyCeilingIsHonoured"] is True


def test_tag_copy_stage_records_the_live_facts(tmp_path: Path) -> None:
    """`tag_copy` copies without moving, and refuses both ends of a violation."""
    _create, copy = _ticket_11_facts(tmp_path)
    assert copy["tagCopyStatus"] == "executed"
    assert copy["tagCopyNativeOutcome"] == "Good"
    assert copy["tagCopyItemCarriesBothEnds"] is True
    assert copy["tagCopyIndependentReadShowsTheCopy"] is True
    assert copy["tagCopyObservedFingerprintIsIndependentRead"] is True
    assert copy["tagCopySourceUnchanged"] is True
    assert copy["tagCopyAuditRowsForCorrelation"] == 2
    assert copy["tagCopyAuditActorIsServiceIdentity"] is True
    assert copy["tagCopyOccupiedDestinationIsConflict"] is True
    assert copy["tagCopyOccupiedDestinationChangedNothing"] is True
    assert copy["tagCopyOccupiedDestinationAuditRecorded"] is True
    assert copy["tagCopyLeafRuleIsRefusedBeforeAnyRead"] is True
    assert copy["tagCopyLeafMismatchNamesTheDestination"] is True
    assert copy["tagCopySourceMissingIsNotFound"] is True
    assert copy["tagCopySourceMissingNamesTheSource"] is True
    assert copy["tagCopySiblingDenialIsSegmentBoundary"] is True
    assert copy["tagCopySiblingDenialNamesTheDestination"] is True
    assert copy["tagCopySourceIsExemptFromAllowlist"] is True
    assert copy["tagCopyUdtNeedsExplicitTypesEntry"] is True
    assert copy["tagCopyBareWildcardDoesNotCoverUdt"] is True
    assert copy["tagCopyTypesEntryIsHonoured"] is True
    assert copy["tagCopyPreflightExecutedNothing"] is True
    assert copy["tagCopyReservedSourceRefusedUnderWildcard"] is True
    assert copy["tagCopyReservedDestinationRefusedUnderWildcard"] is True
    assert copy["tagCopyReservedProviderValueUnchanged"] is True
    assert copy["tagCopyPolicyDocumentUnclobbered"] is True
    assert copy["tagCopyPathOverCeilingNamesTheCeiling"] is True
    assert copy["tagCopyHardItemCeilingIsRefused"] is True
    assert copy["tagCopyOverPolicyLimitIsRefused"] is True
    assert copy["tagCopyPolicyCeilingIsHonoured"] is True


def test_tag_create_stage_fails_closed_on_a_change_that_did_not_land(tmp_path: Path) -> None:
    """A create the Gateway refuses is a stage failure, not a fact about a landing.

    Running the stage twice on one Gateway is what produces the state: the first run
    creates its positive target, so the second run's "positive" call answers the
    collision refusal and the stage that expects a change has to stop on it.
    """
    _ticket_11_facts(tmp_path)
    with RecordedGateway(
        policy_provider=policy_document.POLICY_PROVIDER,
        runtime_tools=("policy_probe", "alarm_probe", "tag_fixture_probe"),
        audit_profile=policy_document.AUDIT_PROFILE_NAME,
        alarm_root=ALARM_ROOT,
        tag_update_paths=_tag_update_paths(),
        tag_create_paths=driver.tag_create_paths(),
        tag_copy_paths=driver.tag_copy_paths(),
    ) as gateway:
        config = _config(tmp_path / "second", base_url=gateway.base_url, api_token=API_TOKEN,
                         stages=driver.MILESTONE_4B)
        driver.stage_tag_update_no_policy(config)
        driver.stage_policy_provision(config)
        driver.stage_tag_update_setup(config)
        driver.stage_tag_update(config)
        gateway._server.tag_config[policy_document.TAG_CREATE_TARGET] = [{"name": "CreateTarget"}]
        with pytest.raises(driver.StageFailure, match="tag_create failed"):
            driver.stage_tag_create(config)


def test_tag_create_stage_fails_closed_on_a_policy_that_never_became_served(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An import the provider never serves cannot stand in as a measured refusal."""
    monkeypatch.setattr(
        driver, "install_policy",
        lambda *args, **kwargs: {"ok": False, "attemptCount": 1, "attempts": [{}], "servedSha256": ""},
    )
    with RecordedGateway(
        policy_provider=policy_document.POLICY_PROVIDER,
        runtime_tools=("policy_probe", "alarm_probe", "tag_fixture_probe"),
        audit_profile=policy_document.AUDIT_PROFILE_NAME,
        alarm_root=ALARM_ROOT,
        tag_update_paths=_tag_update_paths(),
        tag_create_paths=driver.tag_create_paths(),
        tag_copy_paths=driver.tag_copy_paths(),
    ) as gateway:
        config = _config(tmp_path, base_url=gateway.base_url, api_token=API_TOKEN,
                         stages=driver.MILESTONE_4B)
        with pytest.raises(driver.StageFailure, match="never served the tag_create policy"):
            driver.stage_tag_create(config)


def _ticket_12_records(
    tmp_path: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """The three ticket #12 stage records, over the chain all of 4b provisions.

    A move borrows `tag_create`'s node, a rename uses the seeded `TextTarget` and
    creates the occupied name a later case reads, and a delete takes a Folder with
    everything beneath it — so these three run after the ticket #10/#11 stages on one
    Gateway, which is what the live workflow does.
    """
    with RecordedGateway(
        policy_provider=policy_document.POLICY_PROVIDER,
        runtime_tools=("policy_probe", "alarm_probe", "tag_fixture_probe"),
        audit_profile=policy_document.AUDIT_PROFILE_NAME,
        alarm_root=ALARM_ROOT,
        tag_update_paths=_tag_update_paths(),
        tag_create_paths=driver.tag_create_paths(),
        tag_copy_paths=driver.tag_copy_paths(),
        tag_delete_paths=driver.tag_delete_paths(),
        tag_move_paths=driver.tag_move_paths(),
        tag_rename_paths=driver.tag_rename_paths(),
    ) as gateway:
        config = _config(tmp_path, base_url=gateway.base_url, api_token=API_TOKEN,
                         stages=driver.MILESTONE_4B)
        driver.stage_tag_update_no_policy(config)
        driver.stage_policy_provision(config)
        driver.stage_tag_update_setup(config)
        driver.stage_tag_update(config)
        driver.stage_tag_create(config)
        driver.stage_tag_copy(config)
        move = driver.stage_tag_move(config)
        rename = driver.stage_tag_rename(config)
        delete = driver.stage_tag_delete(config)
        return move, rename, delete


def _ticket_12_facts(
    tmp_path: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    move, rename, delete = _ticket_12_records(tmp_path)
    return move["facts"], rename["facts"], delete["facts"]


def test_tag_move_stage_records_the_live_facts(tmp_path: Path) -> None:
    """`tag_move` relocates a node, checks both ends, and never overwrites a name."""
    move, _rename, _delete = _ticket_12_facts(tmp_path)
    assert move["tagMoveConfiguratorCarriesAllThree"] is True
    assert move["tagMoveStatus"] == "executed"
    assert move["tagMoveNativeOutcome"] == "Good"
    # Both ends are read independently: the destination is what the call created and
    # the source is what it removed, each through the provider's own export.
    assert move["tagMoveObservedDestinationPresent"] is True
    assert move["tagMoveObservedDestinationMatchesIndependentRead"] is True
    assert move["tagMoveSourceObservedAbsent"] is True
    assert move["tagMoveExportShowsDestination"] is True
    assert move["tagMoveExportSourceGone"] is True
    assert move["tagMoveAuditRowsForCorrelation"] == 2
    assert move["tagMoveAuditActorIsServiceIdentity"] is True
    # One native call lands each source under its own name, so a differing destination
    # leaf is an input refusal and a move that renames is `tag_rename`'s.
    assert move["tagMoveLeafMismatchReason"] == "destinationLeafDiffersFromSource"
    # An occupied destination is conflict, and it changes neither end.
    assert move["tagMoveOccupiedDestinationCode"] == "conflict"
    assert move["tagMoveOccupiedDestinationReason"] == "destinationExists"
    assert move["tagMoveOccupiedDestinationChangedNothing"] is True
    assert move["tagMoveOccupiedDestinationLeftTheSource"] is True
    # The Precondition token, on the source.
    assert move["tagMoveStaleFingerprintReason"] == "fingerprintMismatch"
    assert move["tagMoveMissingSourceCode"] == "not_found"
    assert move["tagMoveMissingSourceReason"] == "sourceMissing"
    # D30 6 measures both ends, and the reserved provider still bounds both of them.
    assert move["tagMoveSiblingDenialReason"] == "targetNotAllowlisted"
    assert move["tagMoveUdtNeedsExplicitTypesEntry"] is True
    assert move["tagMoveBareWildcardDoesNotCoverUdt"] is True
    assert move["tagMoveTypesEntryIsHonoured"] is True
    assert move["tagMovePreflightExecutedNothing"] is True
    assert move["tagMoveReservedSourceReason"] == "reservedProvider"
    assert move["tagMoveReservedDestinationReason"] == "reservedProvider"
    assert move["tagMoveReservedProviderValueUnchanged"] is True
    assert move["tagMovePolicyDocumentUnclobbered"] is True
    # Every D10 ceiling, including the deployment's own number.
    assert move["tagMoveHardItemCeilingReason"] == "itemsOverHardLimit"
    assert move["tagMovePathOverCeilingReason"] == "pathOverLength"
    assert move["tagMoveOverPolicyLimitReason"] == "itemsOverPolicyLimit"
    assert move["tagMovePolicyCeilingIsHonoured"] is True


def test_tag_rename_stage_records_the_live_facts(tmp_path: Path) -> None:
    """`tag_rename` moves a node inside its own parent and never takes an occupied name."""
    _move, rename, _delete = _ticket_12_facts(tmp_path)
    assert rename["tagRenameStatus"] == "executed"
    assert rename["tagRenameNativeOutcome"] == "Good"
    assert rename["tagRenameItemCarriesBothPaths"] is True
    assert rename["tagRenameObservedNewPathPresent"] is True
    assert rename["tagRenameObservedNewPathMatchesIndependentRead"] is True
    assert rename["tagRenameOldPathObservedAbsent"] is True
    assert rename["tagRenameExportShowsNewPath"] is True
    assert rename["tagRenameExportOldPathGone"] is True
    assert rename["tagRenameAuditRowsForCorrelation"] == 2
    assert rename["tagRenameAuditActorIsServiceIdentity"] is True
    # A new name is one path segment, and the new path is the old parent plus it.
    assert rename["tagRenameMultiSegmentNameCode"] == "invalid_argument"
    assert rename["tagRenameMultiSegmentNameReason"] == "newNameNotASingleSegment"
    assert rename["tagRenameOccupiedNewPathCode"] == "conflict"
    assert rename["tagRenameOccupiedNewPathReason"] == "newPathExists"
    assert rename["tagRenameOccupiedNewPathChangedNothing"] is True
    assert rename["tagRenameOccupiedNewPathLeftTheTarget"] is True
    assert rename["tagRenameStaleFingerprintReason"] == "fingerprintMismatch"
    assert rename["tagRenameMissingTargetCode"] == "not_found"
    assert rename["tagRenameMissingTargetReason"] == "targetMissing"
    # D30 6 measures the new path, and the reserved provider covers both ends.
    assert rename["tagRenameSiblingDenialReason"] == "targetNotAllowlisted"
    assert rename["tagRenameUdtNeedsExplicitTypesEntry"] is True
    assert rename["tagRenameBareWildcardDoesNotCoverUdt"] is True
    assert rename["tagRenameTypesEntryIsHonoured"] is True
    assert rename["tagRenamePreflightExecutedNothing"] is True
    assert rename["tagRenameReservedProviderReason"] == "reservedProvider"
    assert rename["tagRenameReservedProviderValueUnchanged"] is True
    assert rename["tagRenamePolicyDocumentUnclobbered"] is True
    assert rename["tagRenameHardItemCeilingReason"] == "itemsOverHardLimit"
    assert rename["tagRenamePathOverCeilingReason"] == "pathOverLength"
    assert rename["tagRenameOverPolicyLimitReason"] == "itemsOverPolicyLimit"
    assert rename["tagRenamePolicyCeilingIsHonoured"] is True


def test_tag_delete_stage_records_the_live_facts(tmp_path: Path) -> None:
    """`tag_delete` removes a target, takes a Folder with it, and changes nothing else."""
    _move, _rename, delete = _ticket_12_facts(tmp_path)
    assert delete["tagDeleteStatus"] == "executed"
    assert delete["tagDeleteNativeOutcome"] == "Good"
    assert delete["tagDeleteObservedAbsent"] is True
    assert delete["tagDeleteExportGone"] is True
    assert delete["tagDeleteAuditRowsForCorrelation"] == 2
    assert delete["tagDeleteAuditActorIsServiceIdentity"] is True
    # D30 3's partial failure after Preflight: the folder's own call takes the Tag the
    # second item names, that item answers its own Bad outcome, and nothing is retried
    # or rolled back.
    assert delete["tagDeletePartialBatchStatuses"] == ["executed", "executed"]
    assert delete["tagDeletePartialBatchFirstOutcome"] == "Good"
    assert delete["tagDeletePartialBatchSucceeded"] == 1
    assert delete["tagDeletePartialBatchFailed"] == 1
    assert delete["tagDeletePartialBatchRetriedNothing"] is True
    assert delete["tagDeletePartialBatchObservedAbsent"] is True
    assert delete["tagDeleteFolderExportGone"] is True
    # The Precondition token, the input rule, both allowlist halves and every ceiling.
    assert delete["tagDeleteStaleFingerprintCode"] == "conflict"
    assert delete["tagDeleteStaleFingerprintReason"] == "fingerprintMismatch"
    assert delete["tagDeleteStaleFingerprintChangedNothing"] is True
    assert delete["tagDeleteMissingTargetCode"] == "not_found"
    assert delete["tagDeleteMissingTargetReason"] == "targetMissing"
    assert delete["tagDeleteItemKeysReason"] == "itemKeysMustBePathAndFingerprint"
    assert delete["tagDeleteSiblingDenialReason"] == "targetNotAllowlisted"
    assert delete["tagDeleteUdtNeedsExplicitTypesEntry"] is True
    assert delete["tagDeleteBareWildcardDoesNotCoverUdt"] is True
    assert delete["tagDeleteTypesEntryIsHonoured"] is True
    assert delete["tagDeletePreflightExecutedNothing"] is True
    assert delete["tagDeleteReservedProviderReason"] == "reservedProvider"
    assert delete["tagDeleteReservedProviderValueUnchanged"] is True
    assert delete["tagDeletePolicyDocumentUnclobbered"] is True
    assert delete["tagDeleteHardItemCeilingReason"] == "itemsOverHardLimit"
    assert delete["tagDeletePathOverCeilingReason"] == "pathOverLength"
    assert delete["tagDeleteOverPolicyLimitReason"] == "itemsOverPolicyLimit"
    assert delete["tagDeletePolicyCeilingIsHonoured"] is True


def test_ticket_12_case_selectors_replay_the_recorded_refusals() -> None:
    """The rehearsal's case selection keeps each Tool's own rule order.

    The selection is what makes the negative cases reachable in a rehearsal, so a
    changed order — an allowlist read before the reserved provider, an input rule after
    the ceilings — has to fail here rather than quietly replay a different body.
    """

    class _Base:
        policy_provider_created = True
        policy_value = ""
        tag_config = {
            policy_document.TAG_FIXTURE_PATH: [{"name": "WriteTarget", "value": 0}],
            policy_document.TAG_FIXTURE_SIBLING_PATH: [{"name": "WriteTarget", "value": 0}],
            policy_document.TAG_MOVE_SOURCE: [{"name": "CreateTarget", "value": 0}],
            policy_document.TAG_RENAME_TARGET: [{"name": "TextTarget", "value": 0}],
            policy_document.TAG_DELETE_FOLDER: [{"name": "Nested", "tagType": "Folder"}],
            policy_document.TAG_DELETE_FOLDER_CHILD: [{"name": "Inner", "value": 0}],
        }

    def serve(tool: str, policy: dict[str, Any], **config: Any) -> Any:
        return type("Server", (_Base,), {
            "policy_value": json.dumps(policy, sort_keys=True, separators=(",", ":")),
            "tag_config": dict(_Base.tag_config, **config),
        })()

    def fingerprint(path: str, **config: Any) -> str:
        """The fingerprint the fake serves for one path, which a Precondition case
        has to name to reach the stage after the token compare."""
        return recorded_gateway._tag_config_fingerprint(
            dict(_Base.tag_config, **config)[path],
        )

    zero = "tcf1:" + "0" * 64
    plain_move = serve("tag_move", policy_document.tag_move_policy())
    occupied_move = serve("tag_move", policy_document.tag_move_policy(), **{
        policy_document.TAG_MOVE_OCCUPIED_DESTINATION: [{"name": "WriteTarget", "value": 0}],
    })
    wildcard_move = serve("tag_move", policy_document.tag_move_policy(
        allowlist=policy_document.WILDCARD_ALLOWLIST,
    ))
    ceiling_move = serve("tag_move", policy_document.tag_move_policy(max_items=1))
    move_cases = [
        (plain_move, [{"sourcePath": policy_document.TAG_MOVE_SOURCE,
                       "destinationPath": policy_document.TAG_MOVE_DESTINATION,
                       "expectedFingerprint": fingerprint(policy_document.TAG_MOVE_SOURCE)}],
         "moved"),
        (plain_move, [{"sourcePath": policy_document.TAG_MOVE_SOURCE,
                       "destinationPath": policy_document.TAG_MOVE_LEAF_MISMATCH_DESTINATION,
                       "expectedFingerprint": zero}], "destination-leaf-mismatch"),
        (plain_move, [{"sourcePath": policy_document.TAG_MOVE_MISSING_SOURCE,
                       "destinationPath": policy_document.TAG_MOVE_MISSING_DESTINATION,
                       "expectedFingerprint": zero}], "source-missing"),
        (plain_move, [{"sourcePath": policy_document.TAG_FIXTURE_PATH,
                       "destinationPath": policy_document.TAG_MOVE_OCCUPIED_DESTINATION,
                       "expectedFingerprint": zero}], "stale-fingerprint"),
        # A matching token reaches the destination check, where the occupied path is
        # the collision the Tool refuses instead of overwriting it.
        (occupied_move, [{"sourcePath": policy_document.TAG_FIXTURE_PATH,
                          "destinationPath": policy_document.TAG_MOVE_OCCUPIED_DESTINATION,
                          "expectedFingerprint": fingerprint(policy_document.TAG_FIXTURE_PATH)}],
         "destination-exists"),
        (plain_move, [{"sourcePath": policy_document.TAG_MOVE_SIBLING_SOURCE,
                       "destinationPath": policy_document.TAG_MOVE_SIBLING_DESTINATION,
                       "expectedFingerprint": zero}], "source-not-allowlisted"),
        (plain_move, [{"sourcePath": policy_document.TAG_MOVE_UDT_SOURCE,
                       "destinationPath": policy_document.TAG_MOVE_UDT_DESTINATION,
                       "expectedFingerprint": zero}], "udt-source-not-allowlisted"),
        (wildcard_move, [{"sourcePath": policy_document.TAG_MOVE_RESERVED_SOURCE,
                          "destinationPath": policy_document.TAG_MOVE_RESERVED_SOURCE_DESTINATION,
                          "expectedFingerprint": zero}], "reserved-source-refusal"),
        (wildcard_move, [{"sourcePath": policy_document.TAG_MOVE_SOURCE,
                          "destinationPath": policy_document.TAG_MOVE_RESERVED_DESTINATION,
                          "expectedFingerprint": zero}], "reserved-destination-refusal"),
        (plain_move, [{"sourcePath": policy_document.TAG_MOVE_SOURCE,
                       "destinationPath": policy_document.TAG_MOVE_DESTINATION,
                       "expectedFingerprint": zero}] * 21, "over-policy-limit"),
        (plain_move, [{"sourcePath": policy_document.TAG_MOVE_SOURCE,
                       "destinationPath": policy_document.TAG_MOVE_DESTINATION,
                       "expectedFingerprint": zero}] * 101, "items-over-hard-limit"),
        (ceiling_move, [{"sourcePath": policy_document.TAG_MOVE_SOURCE,
                         "destinationPath": policy_document.TAG_MOVE_DESTINATION,
                         "expectedFingerprint": zero}] * 2, "over-policy-limit"),
        # The leaf rule is an input rule, so an over-long destination whose leaf still
        # matches is measured against the ceiling instead.
        (plain_move, [{"sourcePath": policy_document.TAG_MOVE_SOURCE,
                       "destinationPath": f"[{policy_document.TAG_FIXTURE_PROVIDER}]"
                                          f"{policy_document.TAG_FIXTURE_ROOT}/"
                                          f"{policy_document.OVERLONG_PATH_LEAF}",
                       "expectedFingerprint": zero}], "destination-leaf-mismatch"),
    ]
    for server, items, expected in move_cases:
        case, _paths = recorded_gateway._tag_move_case(server, {"items": items})
        assert case == expected, (expected, case)
        if case != "moved":
            assert (FIXTURES / f"tag-move-{case}.json").is_file(), case

    plain_rename = serve("tag_rename", policy_document.tag_rename_policy())
    ceiling_rename = serve("tag_rename", policy_document.tag_rename_policy(max_items=1))
    occupied_rename = serve("tag_rename", policy_document.tag_rename_policy(), **{
        policy_document.TAG_RENAME_TARGET_NEW_PATH: [{"name": "RenamedText", "value": 0}],
    })

    def rename_item(path: str, name: str, fingerprint: str = zero) -> dict[str, str]:
        return {"path": path, "newName": name, "expectedFingerprint": fingerprint}

    rename_cases = [
        (plain_rename, [rename_item(policy_document.TAG_RENAME_TARGET,
                                    policy_document.TAG_RENAME_TARGET_NEW_NAME,
                                    fingerprint(policy_document.TAG_RENAME_TARGET))], "renamed"),
        (plain_rename, [rename_item(policy_document.TAG_RENAME_TARGET,
                                    policy_document.TAG_RENAME_MULTI_SEGMENT_NAME)],
         "new-name-not-a-segment"),
        (occupied_rename, [rename_item(policy_document.TAG_RENAME_OCCUPIED_SOURCE,
                                       policy_document.TAG_RENAME_TARGET_NEW_NAME,
                                       fingerprint(policy_document.TAG_RENAME_OCCUPIED_SOURCE))],
         "new-path-exists"),
        (plain_rename, [rename_item(policy_document.TAG_RENAME_MISSING_TARGET,
                                    policy_document.TAG_RENAME_MISSING_NEW_NAME)], "missing-target"),
        (plain_rename, [rename_item(policy_document.TAG_RENAME_SIBLING_TARGET,
                                    policy_document.TAG_RENAME_SIBLING_NEW_NAME)],
         "target-not-allowlisted"),
        (plain_rename, [rename_item(policy_document.TAG_RENAME_UDT_TARGET,
                                    policy_document.TAG_RENAME_UDT_NEW_NAME)],
         "udt-not-allowlisted"),
        (plain_rename, [rename_item(policy_document.TAG_RENAME_RESERVED_TARGET,
                                    policy_document.TAG_RENAME_RESERVED_NEW_NAME)],
         "reserved-provider-refusal"),
        (plain_rename, [rename_item(policy_document.TAG_RENAME_TARGET,
                                    policy_document.TAG_RENAME_STALE_NEW_NAME)],
         "stale-fingerprint"),
        (plain_rename, [rename_item(policy_document.TAG_RENAME_TARGET,
                                    policy_document.TAG_RENAME_TARGET_NEW_NAME)] * 21,
         "over-policy-limit"),
        (ceiling_rename, [rename_item(policy_document.TAG_RENAME_TARGET,
                                     policy_document.TAG_RENAME_TARGET_NEW_NAME)] * 2,
         "over-policy-limit"),
        (plain_rename, [rename_item(policy_document.TAG_RENAME_TARGET,
                                    policy_document.TAG_RENAME_TARGET_NEW_NAME)] * 101,
         "items-over-hard-limit"),
    ]
    for server, items, expected in rename_cases:
        case, _paths = recorded_gateway._tag_rename_case(server, {"items": items})
        assert case == expected, (expected, case)
        if case != "renamed":
            assert (FIXTURES / f"tag-rename-{case}.json").is_file(), case

    plain_delete = serve("tag_delete", policy_document.tag_delete_policy())
    wildcard_delete = serve("tag_delete", policy_document.tag_delete_policy(
        allowlist=policy_document.WILDCARD_ALLOWLIST,
    ))
    ceiling_delete = serve("tag_delete", policy_document.tag_delete_policy(max_items=1))

    def delete_item(path: str, fingerprint: str = zero) -> dict[str, str]:
        return {"path": path, "expectedFingerprint": fingerprint}

    delete_cases = [
        (plain_delete, [delete_item(policy_document.TAG_FIXTURE_PATH,
                                    fingerprint(policy_document.TAG_FIXTURE_PATH))], "deleted"),
        (plain_delete, [{"expectedFingerprint": zero}], "item-keys"),
        (plain_delete, [delete_item(policy_document.TAG_DELETE_FOLDER,
                                    fingerprint(policy_document.TAG_DELETE_FOLDER)),
                        delete_item(policy_document.TAG_DELETE_FOLDER_CHILD,
                                    fingerprint(policy_document.TAG_DELETE_FOLDER_CHILD))],
         "deleted"),
        (plain_delete, [delete_item(policy_document.TAG_DELETE_MISSING_TARGET)], "missing-target"),
        (plain_delete, [delete_item(policy_document.TAG_DELETE_SIBLING_TARGET)], "sibling-denial"),
        (plain_delete, [delete_item(policy_document.TAG_DELETE_UDT_TARGET)], "udt-not-allowlisted"),
        (wildcard_delete, [delete_item(policy_document.TAG_DELETE_RESERVED_TARGET)],
         "reserved-provider-refusal"),
        (plain_delete, [delete_item(policy_document.TAG_FIXTURE_PATH)], "stale-fingerprint"),
        (plain_delete, [delete_item(policy_document.TAG_FIXTURE_PATH)] * 21, "over-policy-limit"),
        (ceiling_delete, [delete_item(policy_document.TAG_FIXTURE_PATH)] * 2, "over-policy-limit"),
        (plain_delete, [delete_item(policy_document.TAG_FIXTURE_PATH)] * 101, "items-over-hard-limit"),
    ]
    for server, items, expected in delete_cases:
        case, _paths = recorded_gateway._tag_delete_case(server, {"items": items})
        assert case == expected, (expected, case)
        if case != "deleted":
            assert (FIXTURES / f"tag-delete-{case}.json").is_file(), case

    class _NoPolicy(_Base):
        policy_provider_created = False
        policy_value = ""

    # A refusal that needs no Policy is still measured against the input pass first:
    # the item-key rule and the ceilings precede the gate.
    assert recorded_gateway._tag_delete_case(_NoPolicy(), {"items": [{"expectedFingerprint": zero}]})[0] == "item-keys"
    assert recorded_gateway._tag_delete_case(_NoPolicy(), {"items": [delete_item("x")] * 101})[0] == "items-over-hard-limit"
    assert recorded_gateway._tag_delete_case(_NoPolicy(), {"items": [delete_item(policy_document.TAG_FIXTURE_PATH)]})[0] == "no-policy"
    assert recorded_gateway._tag_move_case(_NoPolicy(), {"items": [{
        "sourcePath": policy_document.TAG_MOVE_SOURCE,
        "destinationPath": policy_document.TAG_MOVE_DESTINATION,
        "expectedFingerprint": zero,
    }]})[0] == "no-policy"
    assert recorded_gateway._tag_rename_case(_NoPolicy(), {"items": [rename_item(
        policy_document.TAG_RENAME_TARGET, policy_document.TAG_RENAME_TARGET_NEW_NAME,
    )]})[0] == "no-policy"


def test_summarize_verdict_carries_the_tag_config_mutations(tmp_path: Path) -> None:
    """The 4b verdict reports ticket #10 and ticket #11 side by side."""
    with RecordedGateway(
        policy_provider=policy_document.POLICY_PROVIDER,
        runtime_tools=("policy_probe", "alarm_probe", "tag_fixture_probe"),
        audit_profile=policy_document.AUDIT_PROFILE_NAME,
        alarm_root=ALARM_ROOT,
        tag_update_paths=_tag_update_paths(),
        tag_create_paths=driver.tag_create_paths(),
        tag_copy_paths=driver.tag_copy_paths(),
        tag_delete_paths=driver.tag_delete_paths(),
        tag_move_paths=driver.tag_move_paths(),
        tag_rename_paths=driver.tag_rename_paths(),
    ) as gateway:
        config = _config(tmp_path, base_url=gateway.base_url, api_token=API_TOKEN,
                         stages=driver.MILESTONE_4B)
        for name, stage in [
            ("tag-update-no-policy", driver.stage_tag_update_no_policy),
            ("policy-provision", driver.stage_policy_provision),
            ("tag-update-setup", driver.stage_tag_update_setup),
            ("tag-update", driver.stage_tag_update),
            ("tag-create", driver.stage_tag_create),
            ("tag-copy", driver.stage_tag_copy),
            ("tag-move", driver.stage_tag_move),
            ("tag-rename", driver.stage_tag_rename),
            ("tag-delete", driver.stage_tag_delete),
        ]:
            _record_stage(tmp_path, name, stage(config))
    evidence, code = driver.stage_summarize(config)
    assert code == driver.EXIT_OK
    assert evidence["tickets"] == ["#10", "#11", "#12"]
    assert evidence["drift"] == {}
    mutations = evidence["verdict"]["runtimeTagConfigMutations"]
    assert mutations["tagCreate"]["allowlisted"]["status"] == "executed"
    assert mutations["tagCreate"]["existingTarget"]["reason"] == "targetExists"
    assert mutations["tagCreate"]["targetAllowlist"]["preflightExecutedNothing"] is True
    assert mutations["tagCreate"]["udtDefinitions"]["explicitTypesEntryHonoured"] is True
    assert mutations["tagCreate"]["reservedProvider"]["refusedUnderExplicitWildcard"] is True
    assert mutations["tagCreate"]["audit"]["rowsForCorrelation"] == 2
    assert mutations["tagCopy"]["allowlisted"]["sourceUnchanged"] is True
    assert mutations["tagCopy"]["occupiedDestination"]["reason"] == "destinationExists"
    assert mutations["tagCopy"]["destinationLeafRule"]["reason"] == (
        "destinationLeafDiffersFromSource"
    )
    assert mutations["tagCopy"]["source"]["exemptFromAllowlist"] is True
    assert mutations["tagCopy"]["reservedProvider"]["destinationRefusedUnderExplicitWildcard"] is True
    assert mutations["tagCopy"]["inputBounds"]["deploymentCeilingHonoured"] is True
    assert mutations["tagMove"]["allowlisted"]["sourceObservedAbsent"] is True
    assert mutations["tagMove"]["occupiedDestination"]["reason"] == "destinationExists"
    assert mutations["tagMove"]["reservedProvider"]["destinationReason"] == "reservedProvider"
    assert mutations["tagRename"]["allowlisted"]["exportOldPathGone"] is True
    assert mutations["tagRename"]["occupiedNewPath"]["reason"] == "newPathExists"
    assert mutations["tagRename"]["newNameRule"]["reason"] == "newNameNotASingleSegment"
    assert mutations["tagDelete"]["partialFailureBatch"] == {
        "statuses": ["executed", "executed"], "firstOutcome": "Good", "secondOutcome": "Bad_NotFound",
        "succeeded": 1, "failed": 1, "retriedNothing": True, "observedAbsent": True,
        "folderExportGone": True,
    }
    assert mutations["tagDelete"]["preconditionToken"]["staleChangedNothing"] is True
    assert mutations["tagDelete"]["reservedProvider"]["policyDocumentUnclobbered"] is True
    assert evidence["verdict"]["runtimeTagConfigMutation"]["update"]["status"] == "executed"


def test_summarize_reports_a_broken_tag_config_mutation_fact_as_drift(tmp_path: Path) -> None:
    """A create fact that stops holding is drift, not a passing milestone."""
    with RecordedGateway(
        policy_provider=policy_document.POLICY_PROVIDER,
        runtime_tools=("policy_probe", "alarm_probe", "tag_fixture_probe"),
        audit_profile=policy_document.AUDIT_PROFILE_NAME,
        alarm_root=ALARM_ROOT,
        tag_update_paths=_tag_update_paths(),
        tag_create_paths=driver.tag_create_paths(),
        tag_copy_paths=driver.tag_copy_paths(),
        tag_delete_paths=driver.tag_delete_paths(),
        tag_move_paths=driver.tag_move_paths(),
        tag_rename_paths=driver.tag_rename_paths(),
    ) as gateway:
        config = _config(tmp_path, base_url=gateway.base_url, api_token=API_TOKEN,
                         stages=driver.MILESTONE_4B)
        for name, stage in [
            ("tag-update-no-policy", driver.stage_tag_update_no_policy),
            ("policy-provision", driver.stage_policy_provision),
            ("tag-update-setup", driver.stage_tag_update_setup),
            ("tag-update", driver.stage_tag_update),
            ("tag-create", driver.stage_tag_create),
            ("tag-copy", driver.stage_tag_copy),
            ("tag-move", driver.stage_tag_move),
            ("tag-rename", driver.stage_tag_rename),
            ("tag-delete", driver.stage_tag_delete),
        ]:
            _record_stage(tmp_path, name, stage(config))
    record = json.loads((tmp_path / "tag-create.json").read_text(encoding="utf-8"))
    record["facts"]["tagCreateCollisionChangedNothing"] = False
    _record_stage(tmp_path, "tag-create", record)
    evidence, code = driver.stage_summarize(config)
    assert code == driver.EXIT_DRIFTED
    assert evidence["drift"]["tagCreateCollisionChangedNothing"]["observed"] is False
    assert evidence["verdict"]["runtimeTagConfigMutations"]["tagCreate"][
        "existingTarget"
    ]["changedNothing"] is False


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


def test_the_drivers_cli_accepts_every_registered_stage() -> None:
    """A stage in a milestone's set has to be runnable, or `summarize` waits for nothing.

    The CLI's choices and the stage sets are two lists that drift apart silently: a name
    missing from `choices` exits at argparse, and `policy-read` is the one stage whose
    record names are labelled rather than literal.
    """
    records = {"policy-read-before-restart": "policy-read", "policy-read-after-restart": "policy-read"}
    for milestone, stages in driver.STAGE_SETS.items():
        for name in stages:
            stage = records.get(name, name)
            config = driver.build_config(
                [stage, "--api-token", "t", "--gateway-version", "8.3.8", "--stages", milestone],
            )
            assert config.stage == stage
            assert config.stages == milestone
        assert driver.build_config(
            ["summarize", "--api-token", "t", "--gateway-version", "8.3.8", "--stages", milestone],
        ).stage == "summarize"


def test_recorded_tag_config_mutation_bodies_are_canonical_and_schemata_valid(
    tmp_path: Path,
) -> None:
    """The bodies the rehearsal replays are the shipped Tools' own results and errors.

    A positive result is modelled by the fake rather than recorded, so it has to satisfy
    the Tool's shipped output schema exactly; a refusal carries the canonical D06 error
    object in its single text part, with only the detail keys the contract documents.
    """
    create, copy = _ticket_11_records(tmp_path)
    move, rename, delete = _ticket_12_records(tmp_path)
    for tool, record, key in (
        ("tag_create", create, "allowlistedCreate"),
        ("tag_copy", copy, "allowlistedCopy"),
        ("tag_move", move, "allowlistedMove"),
        ("tag_rename", rename, "allowlistedRename"),
        ("tag_delete", delete, "allowlistedDelete"),
    ):
        contract = json.loads((ROOT / f"contracts/tools/runtime/{tool}.contract.json").read_text())
        schema = json.loads((ROOT / contract["outputSchema"]).read_text(encoding="utf-8"))
        Draft202012Validator(schema).validate(record["raw"][key])
    bodies = (
        sorted(FIXTURES.glob("tag-create-*.json")) + sorted(FIXTURES.glob("tag-copy-*.json"))
        + sorted(FIXTURES.glob("tag-delete-*.json")) + sorted(FIXTURES.glob("tag-move-*.json"))
        + sorted(FIXTURES.glob("tag-rename-*.json"))
    )
    for path in bodies:
        body = json.loads(path.read_text(encoding="utf-8"))
        assert body["isError"] is True, path.name
        error = json.loads(body["content"][0]["text"])
        assert set(error) <= {"code", "message", "correlationId", "details"}, path.name
        assert error["code"] in {
            "conflict", "invalid_argument", "limit_exceeded", "not_found",
            "operation_disabled", "permission_denied",
        }, path.name
        assert set(error["details"]) <= {
            "reason", "allowlistKey", "items", "auditRecorded", "policyPath",
            "index", "limit", "requested", "path",
        }, path.name


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


#: How long the two origin probes below wait between attempts. A busy port is a
#: concurrent run, not a Gateway that needs settling time: the reservation waits its
#: own deadline out either way, so polling it often only shortens the delay.
ORIGIN_POLL_SECONDS = 0.05


def _origin_socket(port: int = driver.EXPECTED_ORIGIN_PORT, deadline_seconds: float = 300.0) -> int:
    """The one origin the guard accepts, waiting out a concurrent run rather than skipping.

    Every Phase 4 fake and rehearsal binds the same origin, so two agents on one
    workstation collide by construction. A busy port is a queue, not a result:
    waiting for it keeps the case green or red on its own evidence, where skipping
    it would hide a real drift behind somebody else's socket. The wait polls; the
    port is either free or not, and it needs no settling time of its own.
    """
    deadline = time.monotonic() + deadline_seconds
    while True:
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"127.0.0.1:{port} stayed busy") from None
                time.sleep(ORIGIN_POLL_SECONDS)
                continue
            return probe.getsockname()[1]


@pytest.fixture()
def disposable_gateway() -> Any:
    """The recorded fake bound to the one origin the guard accepts."""
    _origin_socket()
    with RecordedGateway(
        policy_provider=policy_document.POLICY_PROVIDER, port=driver.EXPECTED_ORIGIN_PORT,
    ) as gateway:
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
                time.sleep(ORIGIN_POLL_SECONDS)
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
    # Readiness points, each waiting on both hosted endpoints: after commissioning,
    # after the policy-read heal restart, and after the restart that proves the
    # document survives one.
    assert text.count("wait_for_gateway.py") == 3
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
    for stage in ("tag-update-no-policy", "policy-provision", "tag-update-setup", "tag-update",
                  "tag-create", "tag-copy", "tag-move", "tag-rename", "tag-delete", "summarize"):
        assert stage in text, stage
    assert "docker compose -f \"$COMPOSE_FILE\" down -v --remove-orphans" in text
    assert "rehearse_local.py --stages 4b" in text
    # Ticket #11 runs after ticket #10 and before the milestone's summarize, and each
    # stage's own log is evidence.
    assert text.index("driver.py tag-update ") < text.index("driver.py tag-create") < (
        text.index("driver.py tag-copy")
    ) < text.index("driver.py tag-move") < text.index("driver.py tag-rename") < (
        text.index("driver.py tag-delete")
    ) < text.index("driver.py summarize")
    for log in ("driver-tag-create.log", "driver-tag-copy.log", "driver-tag-move.log",
                "driver-tag-rename.log", "driver-tag-delete.log"):
        assert f'$EVIDENCE_DIR/{log}"' in text, log
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


def _is_main_guard(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "__name__"
        and any(
            isinstance(comparator, ast.Constant) and comparator.value == "__main__"
            for comparator in node.test.comparators
        )
    )


def test_every_harness_script_ends_with_its_main_guard() -> None:
    """A script executes top to bottom, so a guard above the definitions its own call
    path needs is a run-time ``NameError``, not an import-time one.

    `apply_stage.py` shipped that way: `_reverify` and `_reverify_after_reload` sat
    below `if __name__ == "__main__": raise SystemExit(main())`, so the live apply
    row's retry branch raised `NameError: name '_reverify' is not defined` (run
    35713927140, head `0801e51`) while the rehearsal stayed green, because a
    successful `apply` never reaches that branch. Fixed in `5be2767`; this pin keeps
    a later helper from being appended below a guard again.
    """

    directories = [
        ROOT / "tests/harness/phase4-live",
        ROOT / "tests/harness/phase4-live-rest",
        ROOT / "tests/harness/phase3-live",
        ROOT / "tests/harness/runtime-binding",
    ]
    checked = 0
    for directory in directories:
        for path in sorted(directory.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            body = [node for node in tree.body if not isinstance(node, ast.Expr)]
            guards = [node for node in body if _is_main_guard(node)]
            if not guards:
                continue
            checked += 1
            assert body[-1] is guards[-1], (
                f"{path.relative_to(ROOT)}: the `__main__` guard must be the last statement, "
                "or a script-mode run cannot reach the helpers defined after it"
            )
    assert checked >= 5, "expected to check the harness entry points"
