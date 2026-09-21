#!/usr/bin/env python3
"""Phase 4 ticket #6 characterization driver.

Two questions, both answered with live evidence from a disposable Gateway:

1. Where can the deployment-owned Runtime Target Policy live so that a Runtime
   Tool handler reads it at bounded cost, the Runtime plane cannot write it, and
   `setup-native apply` can write it through Native REST?
2. Is `system.alarm.queryStatus` on one exact Alarm path bounded before or during
   execution, to the D12 Phase 2 amendment standard?

Stages:

    policy-provision   Native REST: create the dedicated Tag provider, import the
                       policy Tag, prove the Abort collision policy and read the
                       document back through /tags/export.
    policy-read        Runtime MCP: call the `policy_probe` handler and record
                       every candidate read primitive.
    alarm              Runtime MCP: call the `alarm_probe` handler and record
                       queryStatus matching, cardinality and cost.
    summarize          Merge the stage records, compare against
                       `characterization.json`, and write `evidence.json`.

Every stage verifies the L5 CI marker, the trusted repository and the live
Gateway identity before it touches the Gateway, and fails closed otherwise.
Exit codes: 0 characterized as expected, 3 characterized but drifted from the
committed expectations, 2 a stage could not be characterized.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gateway_rest  # noqa: E402
import mcp_client  # noqa: E402
import policy_document  # noqa: E402

EXPECTED_MARKER = "ignition-mcp-phase4-live"
TRUSTED_REPO = "sheon-sek/ignition-mcp"
PHASE4_ENVIRONMENT = "phase4-live"
READ_TIMEOUT_MS = 5000
WRITE_PROBE_VALUE = "phase4-write-probe-value"
REQUIRED_ROUTES = {
    ("POST", "/data/api/v1/resources/ignition/tag-provider"),
    ("POST", "/data/api/v1/tags/import"),
    ("GET", "/data/api/v1/tags/export"),
}
PROVIDER_READY_DEADLINE_SECONDS = 120.0
POLICY_READ_DEADLINE_SECONDS = 240.0
RAW_LIMIT = 24_000
EXIT_OK = 0
EXIT_STAGE_FAILED = 2
EXIT_DRIFTED = 3


class GuardError(RuntimeError):
    """The L5 pre-conditions for touching a Gateway were not met."""


class StageFailure(RuntimeError):
    """A stage could not characterize its question."""


@dataclass
class Config:
    stage: str = ""
    base_url: str = "http://127.0.0.1:8093"
    api_token: str = ""
    mcp_url: str = ""
    evidence_dir: Path = Path("artifacts/g4a")
    ci_marker: Path = Path("artifacts/ci-marker.json")
    run_id: str = ""
    gateway_version: str = ""
    gateway_build: str = ""
    image_digest: str = ""
    module_version: str = ""
    module_build: str = ""
    module_sha256: str = ""
    fixture_zip: str = ""
    fixture_sha256: str = ""
    source_revision: str = ""
    provider: str = policy_document.POLICY_PROVIDER
    label: str = "before-restart"
    root_name: str = ""
    noise_count: int = 60
    cycles: int = 3
    repeats: int = 3
    ack_username: str = "ignition-mcp-service"
    guard: dict[str, Any] = field(default_factory=dict)

    @property
    def policy_path(self) -> str:
        return f"[{self.provider}]{policy_document.POLICY_TAG_NAME}"

    @property
    def write_probe_path(self) -> str:
        return f"[{self.provider}]{policy_document.WRITE_PROBE_TAG_NAME}"

    @property
    def alarm_provider(self) -> str:
        return "default"


def bounded(value: Any, limit: int = RAW_LIMIT) -> Any:
    """Keep recorded raw payloads inside a bounded evidence size."""
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    if len(text) <= limit:
        return value
    return {"truncated": True, "length": len(text), "prefix": text[:limit]}


def measurement(report: dict[str, Any], name: str) -> dict[str, Any]:
    for entry in report.get("measurements") or []:
        if isinstance(entry, dict) and entry.get("name") == name:
            return entry
    return {}


def quality_is_good(quality: str) -> bool:
    return isinstance(quality, str) and quality.startswith("Good")


def first_item(entry: dict[str, Any]) -> dict[str, Any]:
    items = entry.get("items")
    if isinstance(items, list) and items and isinstance(items[0], dict):
        return items[0]
    return {}


def write_stage(config: Config, name: str, payload: dict[str, Any]) -> Path:
    config.evidence_dir.mkdir(parents=True, exist_ok=True)
    path = config.evidence_dir / f"{name}.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def verify_guard(config: Config) -> None:
    marker_path = Path(config.ci_marker)
    if not marker_path.is_file():
        raise GuardError(f"CI marker {marker_path} is missing; refusing to touch a Gateway")
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    checks = {
        "marker": marker.get("marker") == EXPECTED_MARKER,
        "environment": marker.get("environment") == PHASE4_ENVIRONMENT,
        "trustedRepo": marker.get("trustedRepo") == TRUSTED_REPO,
        "runId": str(marker.get("runId")) == str(config.run_id),
        "gatewayVersion": str(marker.get("gatewayVersion")) == config.gateway_version,
    }
    info = gateway_rest.gateway_info(config.base_url, config.api_token)
    version_text = str(info.get("ignitionVersion", ""))
    checks["liveGatewayVersion"] = config.gateway_version in version_text
    expected_build = str(marker.get("gatewayBuild") or config.gateway_build)
    if expected_build:
        checks["liveGatewayBuild"] = f"b{expected_build}" in version_text
    if not all(checks.values()):
        raise GuardError(f"L5 guard failed: {json.dumps(checks, sort_keys=True)}")
    config.guard = {"checks": checks, "gatewayInfo": info, "marker": marker}


def identity(config: Config) -> dict[str, Any]:
    return {
        "gatewayVersion": config.gateway_version,
        "gatewayBuild": config.gateway_build,
        "imageDigest": config.image_digest,
        "mcpModuleVersion": config.module_version,
        "mcpModuleBuild": config.module_build,
        "mcpModuleSha256": config.module_sha256,
        "fixtureProjectZip": config.fixture_zip,
        "fixtureProjectZipSha256": config.fixture_sha256,
        "sourceRevision": config.source_revision,
        "runId": config.run_id,
        "environment": PHASE4_ENVIRONMENT,
        "ownerAcceptedDeviations": [
            "phase4-live environment exists without protection rules (same owner-accepted deviation as phase3-live); compensating controls are the trusted-repo guard, no repository secrets in the job, localhost-only Gateway endpoints and the driver-enforced CI marker, Gateway identity and run-unique resource names",
        ],
    }


# --------------------------------------------------------------------------- #
# Stage: policy-provision
# --------------------------------------------------------------------------- #

def wait_for_provider(config: Config) -> dict[str, Any]:
    deadline = time.monotonic() + PROVIDER_READY_DEADLINE_SECONDS
    started = time.monotonic()
    attempts = 0
    find_status = 0
    export_status = 0
    while time.monotonic() < deadline:
        attempts += 1
        find_status, _ = gateway_rest.find_resource(
            config.base_url, config.api_token, "ignition/tag-provider", config.provider,
        )
        export_status, _ = gateway_rest.export_tags(
            config.base_url, config.api_token, config.provider,
        )
        if find_status == 200 and export_status == 200:
            return {
                "ready": True,
                "attempts": attempts,
                "waitedMs": int((time.monotonic() - started) * 1000),
                "findStatus": find_status,
                "exportStatus": export_status,
            }
        time.sleep(2.0)
    return {
        "ready": False,
        "attempts": attempts,
        "waitedMs": int((time.monotonic() - started) * 1000),
        "findStatus": find_status,
        "exportStatus": export_status,
    }


def import_policy(
    config: Config, document: bytes, *, deadline_seconds: float = 150.0, first_policy: str = "Abort",
) -> dict[str, Any]:
    """Import the policy Tag document, retrying until the provider accepts it.

    The recorded 8.3.8 run showed a freshly created Tag provider answering
    `/tags/import` with `Bad 776 TagPath.getPathLength() ... cleanPath is null`
    while the provider was still starting, so `setup-native apply` cannot treat a
    single import as reliable. The first attempt keeps D30's `Abort` policy; the
    retries are idempotent so a partially applied import cannot wedge the write.
    """
    started = time.monotonic()
    attempts: list[dict[str, Any]] = []
    while True:
        policy = first_policy if not attempts else "MergeOverwrite"
        status, payload = gateway_rest.import_tags(
            config.base_url, config.api_token, config.provider, document,
            collision_policy=policy,
        )
        failures = gateway_rest.import_failures(payload)
        attempts.append({
            "collisionPolicy": policy,
            "status": status,
            "body": bounded(payload),
            "failures": bounded(failures),
            "elapsedMs": int((time.monotonic() - started) * 1000),
        })
        if status == 200 and failures is None:
            return {"ok": True, "attempts": attempts, "attemptCount": len(attempts)}
        if time.monotonic() - started >= deadline_seconds:
            return {"ok": False, "attempts": attempts, "attemptCount": len(attempts)}
        time.sleep(3.0)


def stage_policy_provision(config: Config) -> dict[str, Any]:
    facts: dict[str, Any] = {}
    raw: dict[str, Any] = {}
    endpoints = gateway_rest.openapi_endpoints(config.base_url, config.api_token)
    missing = sorted(f"{method} {route}" for method, route in REQUIRED_ROUTES - endpoints)
    facts["openapiRequiredRoutes"] = sorted(f"{method} {route}" for method, route in REQUIRED_ROUTES)
    facts["openapiMissingRoutes"] = missing
    if missing:
        raise StageFailure(f"required Native REST routes are missing: {missing}")

    status, payload = gateway_rest.create_tag_provider(
        config.base_url, config.api_token, policy_document.provider_resource(),
    )
    raw["createProvider"] = {"status": status, "body": bounded(payload)}
    if status not in {200, 201}:
        describe_status, describe = gateway_rest.request(
            config.base_url, config.api_token, "GET",
            "/data/api/v1/resources/type/ignition/tag-provider",
        )
        raw["typeDescription"] = {"status": describe_status, "body": bounded(describe)}
        raise StageFailure(f"creating the policy Tag provider returned HTTP {status}")

    readiness = wait_for_provider(config)
    facts["providerReadiness"] = readiness
    if not readiness["ready"]:
        raise StageFailure(f"the policy Tag provider never became readable: {readiness}")

    imported = import_policy(config, policy_document.tag_document_bytes())
    raw["importPolicy"] = bounded(imported)
    facts["policyImportAttemptCount"] = imported["attemptCount"]
    facts["policyImportRetried"] = imported["attemptCount"] > 1
    if not imported["ok"]:
        raise StageFailure(
            f"importing the policy Tag failed after {imported['attemptCount']} attempt(s): "
            f"{json.dumps(imported['attempts'][-1])[:800]}"
        )
    facts["policyImported"] = True

    status, payload = gateway_rest.import_tags(
        config.base_url, config.api_token, config.provider,
        policy_document.tag_document_bytes(), collision_policy="Abort",
    )
    abort_failures = gateway_rest.import_failures(payload)
    raw["importPolicyAbort"] = {"status": status, "body": bounded(payload)}
    facts["abortReimportStatus"] = status
    facts["abortPolicyRejectsExistingTarget"] = abort_failures is not None

    status, payload = gateway_rest.import_tags(
        config.base_url, config.api_token, config.provider,
        policy_document.tag_document_bytes(), collision_policy="MergeOverwrite",
    )
    merge_failures = gateway_rest.import_failures(payload)
    raw["importPolicyMerge"] = {"status": status, "body": bounded(payload)}
    facts["mergeReimportStatus"] = status
    facts["mergeOverwriteReimportSucceeded"] = status == 200 and merge_failures is None

    status, exported = gateway_rest.export_tags(
        config.base_url, config.api_token, config.provider,
    )
    facts["exportStatus"] = status
    if status != 200:
        raise StageFailure(f"exporting the policy Tag returned HTTP {status}")
    exported_document = gateway_rest.decode(exported)
    tag = policy_document.find_tag(exported_document, policy_document.POLICY_TAG_NAME)
    value = tag.get("value") if isinstance(tag, dict) else None
    facts["exportedTagPresent"] = tag is not None
    facts["exportedValueIsString"] = isinstance(value, str)
    facts["exportedValueLength"] = len(value) if isinstance(value, str) else 0
    facts["exportedValueSha256"] = gateway_rest.sha256_text(value) if isinstance(value, str) else ""
    facts["exportedTagType"] = str(tag.get("tagType")) if isinstance(tag, dict) else ""
    facts["restReadBackMatches"] = (
        facts["exportedValueSha256"] == policy_document.policy_sha256()
    )
    raw["export"] = bounded(exported_document)

    find_status, found = gateway_rest.find_resource(
        config.base_url, config.api_token, "ignition/tag-provider", config.provider,
    )
    raw["providerResource"] = {"status": find_status, "body": bounded(found)}
    facts["providerResourceSignature"] = (
        str(found.get("signature")) if isinstance(found, dict) else ""
    )
    return {
        "stage": "policy-provision",
        "ok": True,
        "identity": identity(config),
        "guard": config.guard,
        "facts": facts,
        "raw": raw,
    }


# --------------------------------------------------------------------------- #
# Stage: policy-read
# --------------------------------------------------------------------------- #

def policy_probe_arguments(config: Config) -> dict[str, Any]:
    return {
        "policyPath": config.policy_path,
        "readTimeoutMs": READ_TIMEOUT_MS,
        "missingPath": f"[{config.provider}]MissingPolicy",
        "writeProbePath": config.write_probe_path,
        "writeProbeValue": WRITE_PROBE_VALUE,
        "configModuleId": "ignition",
        "configTypeId": "tag-provider",
        "configName": config.provider,
    }


def derive_policy_read_facts(config: Config, report: dict[str, Any]) -> dict[str, Any]:
    facts: dict[str, Any] = {}
    policy_read = measurement(report, "tag.readBlocking.policy")
    item = first_item(policy_read)
    facts["policyReadOk"] = bool(policy_read.get("ok"))
    facts["policyReadResultCount"] = policy_read.get("count")
    facts["policyReadQuality"] = str(item.get("quality", ""))
    facts["policyReadQualityIsGood"] = quality_is_good(str(item.get("quality", "")))
    facts["policyReadValueType"] = str(item.get("valueType", ""))
    facts["policyReadValueLength"] = item.get("valueLength")
    facts["policyReadValueByteLength"] = item.get("valueByteLength")
    facts["policyReadSha256"] = str(item.get("valueSha256", ""))
    facts["policyReadMatchesAppliedDocument"] = (
        facts["policyReadSha256"] == policy_document.policy_sha256()
    )
    facts["policyReadJsonKind"] = str(policy_read.get("jsonKind", ""))
    facts["policyReadJsonKeys"] = policy_read.get("jsonKeys", [])
    facts["policyReadElapsedMs"] = policy_read.get("elapsedMs")

    missing_read = measurement(report, "tag.readBlocking.missing")
    missing_item = first_item(missing_read)
    facts["missingPathReadOk"] = bool(missing_read.get("ok"))
    facts["missingPathQuality"] = str(missing_item.get("quality", ""))
    facts["missingPathFailsClosed"] = not quality_is_good(facts["missingPathQuality"])

    policy_config = measurement(report, "tag.getConfiguration.policy")
    config_entries = policy_config.get("entries")
    first_entry = config_entries[0] if isinstance(config_entries, list) and config_entries else {}
    facts["policyConfigReadOk"] = bool(policy_config.get("ok"))
    facts["policyConfigEntryCount"] = policy_config.get("count")
    facts["policyConfigKeys"] = first_entry.get("keys", []) if isinstance(first_entry, dict) else []
    provider_root_config = measurement(report, "tag.getConfiguration.providerRoot")
    facts["providerRootConfigReadOk"] = bool(provider_root_config.get("ok"))

    write_measure = measurement(report, "tag.writeBlocking.probe")
    codes = [str(code) for code in write_measure.get("writeQualityCodes") or []]
    after_item = first_item(write_measure.get("afterWrite") or {})
    facts["handlerWriteQualityCodes"] = codes
    facts["handlerWriteInsidePolicyProviderSucceeded"] = (
        bool(write_measure.get("ok"))
        and bool(codes)
        and all(quality_is_good(code) for code in codes)
        and after_item.get("valueSha256") == gateway_rest.sha256_text(WRITE_PROBE_VALUE)
    )

    namespaces = measurement(report, "system.namespaces")
    facts["handlerScopeHasSystemConfig"] = bool(namespaces.get("hasConfig"))
    facts["handlerScopeHasSystemAlarm"] = bool(namespaces.get("hasAlarm"))
    facts["handlerScopeNamespaces"] = namespaces.get("namespaces", [])
    configuration_resource = measurement(report, "system.config.getResource")
    facts["systemConfigResourceReadOk"] = bool(configuration_resource.get("ok"))
    facts["systemConfigResourceSignature"] = str(configuration_resource.get("resourceSignature", ""))
    facts["systemConfigResourceConfigKeys"] = configuration_resource.get("configKeys", [])
    resource_types = measurement(report, "system.config.getResourceTypes")
    facts["systemConfigResourceTypeCount"] = resource_types.get("count")
    facts["systemConfigResourceTypes"] = resource_types.get("items", [])
    environment = measurement(report, "java.lang.System.getenv")
    facts["handlerScopeCanReadProcessEnvironment"] = bool(environment.get("available"))
    facts["handlerScopeEnvironmentPresent"] = bool(environment.get("present"))
    project = measurement(report, "system.util.getProjectName")
    facts["handlerProjectName"] = str(project.get("projectName", ""))

    return facts


def stage_policy_read(config: Config) -> dict[str, Any]:
    raw: dict[str, Any] = {}
    client = mcp_client.McpClient(config.mcp_url, config.api_token)
    raw["initialize"] = bounded(client.initialize())
    tools = client.tools_list()
    if "policy_probe" not in tools:
        raise StageFailure(f"policy_probe is not discoverable; tools/list = {sorted(tools)}")
    arguments = policy_probe_arguments(config)
    deadline = time.monotonic() + POLICY_READ_DEADLINE_SECONDS
    attempts: list[dict[str, Any]] = []
    repairs = 0
    facts: dict[str, Any] = {}
    report: dict[str, Any] = {}
    while True:
        report = {}
        error = ""
        try:
            report = client.structured("policy_probe", arguments)
        except mcp_client.McpError as exc:
            error = str(exc)
        facts = derive_policy_read_facts(config, report) if report else {}
        healthy = bool(facts.get("policyReadQualityIsGood")) and bool(
            facts.get("policyReadMatchesAppliedDocument")
        )
        attempt: dict[str, Any] = {
            "servedPolicyTag": healthy,
            "policyReadQuality": str(facts.get("policyReadQuality", "")),
            "handlerWriteQualityCodes": facts.get("handlerWriteQualityCodes", []),
            "error": error,
        }
        if healthy or time.monotonic() >= deadline:
            attempts.append(attempt)
            break
        # Two recorded 8.3.8 provider-startup failures motivate this loop: the
        # first /tags/import can be rejected while the provider is starting, and
        # an accepted import can be written to config while the running provider
        # serves no Tags at all. Re-import idempotently and probe again; a
        # config-only write is not enough for `setup-native apply` either.
        repair = import_policy(
            config, policy_document.tag_document_bytes(),
            deadline_seconds=30.0, first_policy="MergeOverwrite",
        )
        repairs = repairs + 1
        attempt["repairImport"] = bounded(repair, 4000)
        attempts.append(attempt)
        time.sleep(3.0)
    facts["tools"] = sorted(tools)
    facts["policyReadAttempts"] = len(attempts)
    facts["policyReadRepairImports"] = repairs
    raw["policyProbe"] = bounded(report)
    raw["policyProbeAttempts"] = bounded(attempts, 40_000)
    if not facts.get("policyReadQualityIsGood") or not facts.get("policyReadMatchesAppliedDocument"):
        raise StageFailure(
            "the policy Tag was never served by the provider: "
            + json.dumps(attempts[-1], sort_keys=True)[:800]
        )
    if config.label == "after-restart":
        previous = config.evidence_dir / "policy-read-before-restart.json"
        before_sha = ""
        if previous.is_file():
            before_sha = str(json.loads(previous.read_text(encoding="utf-8")).get("facts", {}).get("policyReadSha256", ""))
        facts["policyReadBeforeRestartSha256"] = before_sha
        facts["policyReadSurvivesGatewayRestart"] = bool(before_sha) and before_sha == facts["policyReadSha256"]
    return {
        "stage": f"policy-read-{config.label}",
        "ok": True,
        "label": config.label,
        "identity": identity(config),
        "guard": config.guard,
        "facts": facts,
        "raw": raw,
    }


# --------------------------------------------------------------------------- #
# Stage: alarm
# --------------------------------------------------------------------------- #

def stage_alarm(config: Config) -> dict[str, Any]:
    facts: dict[str, Any] = {}
    raw: dict[str, Any] = {}
    client = mcp_client.McpClient(config.mcp_url, config.api_token)
    raw["initialize"] = bounded(client.initialize())
    tools = client.tools_list()
    if "alarm_probe" not in tools:
        raise StageFailure(f"alarm_probe is not discoverable; tools/list = {sorted(tools)}")
    report = client.structured("alarm_probe", {
        "providerRoot": "[default]",
        "rootName": config.root_name,
        "provider": config.alarm_provider,
        "noiseCount": config.noise_count,
        "cycles": config.cycles,
        "repeats": config.repeats,
        "ackUsername": config.ack_username,
    })
    raw["alarmProbe"] = bounded(report, 200_000)
    facts["alarmFixtureAcceptedShape"] = str(report.get("accepted", ""))
    facts["alarmConclusion"] = str(report.get("conclusion", ""))
    if report.get("conclusion") != "measured":
        raise StageFailure(f"the Alarm fixture could not be created: {raw['alarmProbe']}")

    counts: dict[str, int] = {}
    timings: dict[str, int] = {}
    for entry in report.get("measurements") or []:
        name = str(entry.get("name", ""))
        if not name.startswith("queryStatus."):
            continue
        label = name.split(".", 1)[1]
        if isinstance(entry.get("count"), int):
            counts[label] = entry["count"]
        if isinstance(entry.get("medianElapsedMs"), int):
            timings[label] = entry["medianElapsedMs"]
    facts["queryCounts"] = counts
    facts["queryMedianElapsedMs"] = timings
    expected_active = int(report.get("expectedActive") or 0)
    facts["expectedActiveAlarms"] = expected_active

    exact = counts.get("exact.source", -1)
    sibling = counts.get("sibling.source", -1)
    tag_path_only = counts.get("exact.tagPathOnly", -1)
    folder_tag_path = counts.get("folder.tagPathOnly", -1)
    folder_partial = counts.get("folder.partialLeaf", -1)
    folder_wildcard = counts.get("folder.trailingWildcard", -1)
    alarm_name_wildcard = counts.get("almName.wildcard", -1)
    root_wildcard = counts.get("root.trailingWildcard", -1)
    system_unfiltered = counts.get("system.unfiltered", -1)

    facts["exactPathSourcePattern"] = str(report.get("exactSourcePattern", ""))
    facts["exactPathCount"] = exact
    facts["exactPathCountIsOnePerAlarm"] = exact == 1
    facts["exactPathMatchesOnlyOwnSource"] = exact == 1 and sibling == 1
    facts["exactPathStateFilterCount"] = counts.get("exact.sourceWithState", -1)
    facts["exactPathSourceFormMatchesPathForm"] = counts.get("exact.sourceForm", -1) == exact
    facts["tagPathOnlyPatternMatchesNothing"] = tag_path_only == 0
    facts["tagPathOnlyPatternCount"] = tag_path_only
    facts["bracketTagPathFormCount"] = counts.get("exact.bracketTagPath", -1)
    facts["bareTagPathFormCount"] = counts.get("exact.bareTagPath", -1)
    facts["folderPathExpandsDescendants"] = folder_tag_path > 0
    facts["folderTagPathCount"] = folder_tag_path
    facts["partialLeafPathMatchesNothing"] = folder_partial == 0
    facts["folderTrailingWildcardCount"] = folder_wildcard
    facts["alarmNameWildcardMatchesOneAlarm"] = alarm_name_wildcard == 1
    facts["rootTrailingWildcardCount"] = root_wildcard
    facts["rootWildcardMatchesEveryFixtureAlarm"] = root_wildcard == expected_active
    facts["systemUnfilteredCount"] = system_unfiltered
    facts["wildcardFormMatchesMoreThanExactPath"] = root_wildcard > exact > 0

    cycle_results = report.get("cycleResults") or []
    cycle_counts = [
        (entry.get("activeCount"), entry.get("clearedCount"))
        for entry in cycle_results if isinstance(entry, dict)
    ]
    facts["cycleCounts"] = cycle_counts
    facts["perPathCountStableAcrossCycles"] = bool(cycle_counts) and all(
        active == 1 and cleared == 1 for active, cleared in cycle_counts
    )

    detail = measurement(report, "queryStatus.exact.eventDetail")
    events = detail.get("events") or []
    first_event = events[0] if events and isinstance(events[0], dict) else {}
    facts["eventSourceForm"] = str(first_event.get("getSource", ""))
    facts["eventStateForm"] = str(first_event.get("getState", ""))
    facts["eventDisplayPath"] = str(first_event.get("getDisplayPath", ""))
    facts["eventName"] = str(first_event.get("getName", ""))
    facts["eventMethodsSample"] = (detail.get("eventMethods") or [])[:60]

    acknowledge = measurement(report, "alarm.acknowledge.exact")
    facts["acknowledgeAttempted"] = acknowledge.get("attempted")
    facts["acknowledgeRemaining"] = acknowledge.get("remaining") or []
    facts["acknowledgeStatesAfter"] = acknowledge.get("statesAfter") or []

    if exact > 0 and system_unfiltered > 0:
        facts["exactToUnfilteredMedianRatio"] = round(
            timings.get("exact.source", 0) / float(max(timings.get("system.unfiltered", 0), 1)), 4,
        )
    else:
        facts["exactToUnfilteredMedianRatio"] = None
    facts["exactPathBoundedBasis"] = {
        "literalMatchingOnly": (
            exact == 1 and sibling == 1 and tag_path_only == 0 and folder_tag_path == 0
        ),
        "noAccumulationWithoutAck": facts["perPathCountStableAcrossCycles"],
        "timing": {
            "exactMedianMs": timings.get("exact.source"),
            "rootWildcardMedianMs": timings.get("root.trailingWildcard"),
            "systemUnfilteredMedianMs": timings.get("system.unfiltered"),
        },
    }
    return {
        "stage": "alarm",
        "ok": True,
        "identity": identity(config),
        "guard": config.guard,
        "facts": facts,
        "raw": raw,
    }


# --------------------------------------------------------------------------- #
# Stage: summarize
# --------------------------------------------------------------------------- #

def load_stage(config: Config, name: str) -> dict[str, Any]:
    path = config.evidence_dir / f"{name}.json"
    if not path.is_file():
        raise StageFailure(f"stage record {path} is missing")
    return json.loads(path.read_text(encoding="utf-8"))


def stage_summarize(config: Config) -> tuple[dict[str, Any], int]:
    provision = load_stage(config, "policy-provision")
    before = load_stage(config, "policy-read-before-restart")
    stages = [provision, before]
    after_path = config.evidence_dir / "policy-read-after-restart.json"
    after = json.loads(after_path.read_text(encoding="utf-8")) if after_path.is_file() else None
    if after is not None:
        stages.append(after)
    alarm = load_stage(config, "alarm")
    stages.append(alarm)

    facts: dict[str, Any] = {}
    for stage in stages:
        facts.update(stage.get("facts", {}))

    expectations_path = Path(__file__).resolve().parent / "characterization.json"
    expectations = json.loads(expectations_path.read_text(encoding="utf-8"))
    expected = expectations.get(config.gateway_version)
    if expected is None:
        raise StageFailure(f"characterization.json has no entry for {config.gateway_version}")
    drift = {
        key: {"expected": value, "observed": facts.get(key)}
        for key, value in expected.items() if facts.get(key) != value
    }

    literal = bool(facts.get("exactPathCountIsOnePerAlarm")) and not bool(facts.get("folderPathExpandsDescendants"))
    stable = bool(facts.get("perPathCountStableAcrossCycles"))
    verdict = {
        "runtimeTargetPolicyStorage": {
            "chosenLocation": config.policy_path,
            "readPrimitive": "system.tag.readBlocking([path], timeoutMs)",
            "restWritePath": [
                "POST /data/api/v1/resources/ignition/tag-provider",
                "POST /data/api/v1/tags/import?type=json&collisionPolicy=Abort",
            ],
            "restReadBackPath": "GET /data/api/v1/tags/export?type=json",
            "handlerReadMatchesAppliedDocument": facts.get("policyReadMatchesAppliedDocument"),
            "restReadBackMatches": facts.get("restReadBackMatches"),
            "survivesGatewayRestart": facts.get("policyReadSurvivesGatewayRestart"),
            "handlerCanWriteInsidePolicyProvider": facts.get("handlerWriteInsidePolicyProviderSucceeded"),
            "runtimeWritePrevention": "product rule: every Runtime Tag mutation (tag_write, tag_update, tag_delete, tag_move, tag_rename, tag_copy destination) must refuse any target inside the policy provider before Preflight execution, whatever the allowlist says, including *",
        },
        "exactPathAlarmQuery": {
            "literalMatchingOnly": literal,
            "noAccumulationWithoutAck": stable,
            "bounded": literal and stable,
            "basis": facts.get("exactPathBoundedBasis"),
        },
    }
    evidence = {
        "schemaVersion": 1,
        "ticket": "#6",
        "title": "Characterize Runtime Target Policy storage and bounded exact-path alarm queryStatus",
        "identity": identity(config),
        "stages": stages,
        "facts": facts,
        "expectations": expected,
        "drift": drift,
        "verdict": verdict,
    }
    config.evidence_dir.mkdir(parents=True, exist_ok=True)
    (config.evidence_dir / "evidence.json").write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    return evidence, (EXIT_DRIFTED if drift else EXIT_OK)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def build_config(argv: list[str]) -> Config:
    parser = argparse.ArgumentParser(description="Phase 4 ticket #6 characterization driver")
    parser.add_argument("stage", choices=["policy-provision", "policy-read", "alarm", "summarize"])
    parser.add_argument("--base-url", default=os.environ.get("P4_BASE_URL", "http://127.0.0.1:8093"))
    parser.add_argument("--api-token", default=os.environ.get("CI_API_TOKEN", ""))
    parser.add_argument("--mcp-url", default=os.environ.get("P4_MCP_URL", ""))
    parser.add_argument("--evidence-dir", default=os.environ.get("EVIDENCE_DIR", "artifacts/g4a"))
    parser.add_argument("--ci-marker", default=os.environ.get("P4_CI_MARKER", "artifacts/ci-marker.json"))
    parser.add_argument("--run-id", default=os.environ.get("GITHUB_RUN_ID", ""))
    parser.add_argument("--gateway-version", default=os.environ.get("GATEWAY_VERSION", ""))
    parser.add_argument("--gateway-build", default=os.environ.get("GATEWAY_BUILD", ""))
    parser.add_argument("--image-digest", default=os.environ.get("IMAGE_DIGEST", ""))
    parser.add_argument("--module-version", default=os.environ.get("MCP_MODULE_VERSION", ""))
    parser.add_argument("--module-build", default=os.environ.get("MCP_MODULE_BUILD", ""))
    parser.add_argument("--module-sha256", default=os.environ.get("MCP_MODULE_SHA256", ""))
    parser.add_argument("--fixture-zip", default=os.environ.get("FIXTURE_ZIP", ""))
    parser.add_argument("--fixture-sha256", default=os.environ.get("FIXTURE_SHA256", ""))
    parser.add_argument("--source-revision", default=os.environ.get("SOURCE_REVISION", ""))
    parser.add_argument("--provider", default=os.environ.get("P4_POLICY_PROVIDER", policy_document.POLICY_PROVIDER))
    parser.add_argument("--label", default="before-restart", choices=["before-restart", "after-restart"])
    parser.add_argument("--root-name", default=os.environ.get("P4_ALARM_ROOT", ""))
    parser.add_argument("--noise-count", type=int, default=int(os.environ.get("P4_NOISE_COUNT", "60")))
    parser.add_argument("--cycles", type=int, default=int(os.environ.get("P4_CYCLES", "3")))
    parser.add_argument("--repeats", type=int, default=int(os.environ.get("P4_REPEATS", "3")))
    parser.add_argument("--ack-username", default=os.environ.get("P4_ACK_USERNAME", "ignition-mcp-service"))
    args = parser.parse_args(argv)
    if not args.api_token:
        parser.error("--api-token (or CI_API_TOKEN) is required")
    config = Config(
        stage=args.stage,
        base_url=args.base_url,
        api_token=args.api_token,
        mcp_url=args.mcp_url or args.base_url.rstrip("/") + "/data/mcp/phase4-policy-probe",
        evidence_dir=Path(args.evidence_dir),
        ci_marker=Path(args.ci_marker),
        run_id=str(args.run_id),
        gateway_version=args.gateway_version,
        gateway_build=args.gateway_build,
        image_digest=args.image_digest,
        module_version=args.module_version,
        module_build=args.module_build,
        module_sha256=args.module_sha256,
        fixture_zip=args.fixture_zip,
        fixture_sha256=args.fixture_sha256,
        source_revision=args.source_revision,
        provider=args.provider,
        label=args.label,
        root_name=args.root_name or f"MCP_P4_{args.run_id}",
        noise_count=args.noise_count,
        cycles=args.cycles,
        repeats=args.repeats,
        ack_username=args.ack_username,
    )
    if not config.gateway_version:
        parser.error("--gateway-version (or GATEWAY_VERSION) is required")
    return config


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    config = build_config(arguments)
    stage = config.stage
    try:
        verify_guard(config)
        if stage == "policy-provision":
            record = stage_policy_provision(config)
            write_stage(config, "policy-provision", record)
            code = EXIT_OK
        elif stage == "policy-read":
            record = stage_policy_read(config)
            write_stage(config, f"policy-read-{config.label}", record)
            code = EXIT_OK
        elif stage == "alarm":
            record = stage_alarm(config)
            write_stage(config, "alarm", record)
            code = EXIT_OK
        else:
            record, code = stage_summarize(config)
    except (GuardError, StageFailure, gateway_rest.RestError, mcp_client.McpError) as error:
        failure = {"stage": stage, "ok": False, "error": str(error)}
        try:
            write_stage(config, f"{stage}-failure", failure)
        except OSError:
            pass
        print(json.dumps(failure, indent=2)[:4000], file=sys.stderr)
        return EXIT_STAGE_FAILED

    summary = {
        "stage": record.get("stage", stage),
        "ok": bool(record.get("ok", True)),
        "exitCode": code,
        "facts": record.get("facts"),
        "verdict": record.get("verdict"),
        "drift": record.get("drift"),
    }
    print(json.dumps(bounded(summary, 16_000), indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
