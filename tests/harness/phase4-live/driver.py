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
    tag-write-setup    Runtime MCP: create the disposable Tag fixtures, the audit
    tag-write            profile and the tag_write policy, then verify tag_write.
    alarm-no-policy    Runtime MCP: the Alarm Mutations fail closed with no policy.
    alarm-shelve       Runtime MCP: verify alarm_shelve and alarm_unshelve.
    tag-update-no-policy
                       Runtime MCP: `tag_update` fails closed with no policy.
    tag-update-setup   Runtime MCP: the Tag fixtures, the audit profile and the
                       tag_update policy the ticket #10 cases run against.
    tag-update         Runtime MCP: verify the Tag config fingerprint and
                       `tag_update` on the configurator profile.
    summarize          Merge the stage records of one milestone, compare against
                       that milestone's expectations, and write `evidence.json`.

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
import urllib.parse

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gateway_rest  # noqa: E402
import mcp_client  # noqa: E402
import policy_document  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tooling.contracts import lint  # noqa: E402

EXPECTED_MARKER = "ignition-mcp-phase4-live"
TRUSTED_REPO = "sheon-sek/ignition-mcp"
PHASE4_ENVIRONMENT = "phase4-live"
#: The one origin this harness may ever talk to. A developer workstation keeps a
#: real Gateway on 127.0.0.1:8088, so the disposable compose Gateway publishes
#: 8093 and the driver refuses anything else before the first request.
EXPECTED_ORIGIN_HOST = "127.0.0.1"
EXPECTED_ORIGIN_PORT = 8093
#: The two Module-hosted endpoints this harness drives: the ticket #6 probe
#: project, and the shipped Runtime Bundle deployed with the `operator` profile
#: whose inventory and Tag Mutations the ticket #7 cases exercise.
PROBE_MCP_PATH = "/data/mcp/phase4-policy-probe"
OPERATOR_MCP_PATH = "/data/mcp/phase4-operator"
#: The `configurator` deployment: milestone 4b's first mutation belongs to the
#: CONFIG class, so its inventory is the one that has to equal
#: contracts/profiles/configurator.yaml exactly.
CONFIGURATOR_MCP_PATH = "/data/mcp/phase4-configurator"
EXPECTED_MCP_PATHS = (PROBE_MCP_PATH, OPERATOR_MCP_PATH, CONFIGURATOR_MCP_PATH)
EXPECTED_MCP_PATH = PROBE_MCP_PATH
#: Shipped-bundle Server Config and Designer project name on the disposable Gateway.
RUNTIME_PROJECT = "ignition_runtime"
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
#: One summarize per milestone: the stage records it merges, the expectations it
#: checks and the verdict shape it writes. Milestone 4a is the ticket #6
#: characterization plus the ticket #7/#8 Mutations; 4b is the Tag CONFIG
#: Mutation (ticket #10).
MILESTONE_4A = "4a"
MILESTONE_4B = "4b"
EXPECTATIONS = {
    MILESTONE_4A: "characterization.json",
    MILESTONE_4B: "characterization-config.json",
}
STAGE_SETS = {
    MILESTONE_4A: (
        "policy-provision", "policy-read-before-restart", "policy-read-after-restart",
        "alarm", "tag-write-no-policy", "tag-write-setup", "tag-write",
        "alarm-no-policy", "alarm-shelve",
    ),
    MILESTONE_4B: (
        "tag-update-no-policy", "tag-update-setup", "tag-update",
    ),
}
#: An optional stage record: the milestone's verdict does not need it, but it is
#: merged into the facts when it is there.
OPTIONAL_STAGES = frozenset({"policy-read-after-restart"})


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
    operator_mcp_url: str = ""
    configurator_mcp_url: str = ""
    marker_label: str = "g4a"
    stages: str = MILESTONE_4A
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
    characterization: Path | None = None
    policy_read_deadline_seconds: float = POLICY_READ_DEADLINE_SECONDS
    noise_count: int = 60
    cycles: int = 3
    repeats: int = 3
    ack_username: str = "ignition-mcp-service"
    runtime_project: str = RUNTIME_PROJECT
    audit_profile: str = policy_document.AUDIT_PROFILE_NAME
    guard: dict[str, Any] = field(default_factory=dict)

    @property
    def policy_path(self) -> str:
        return f"[{self.provider}]{policy_document.POLICY_TAG_NAME}"

    @property
    def write_probe_path(self) -> str:
        return f"[{self.provider}]{policy_document.WRITE_PROBE_TAG_NAME}"

    @property
    def policy_length_path(self) -> str:
        return f"[{self.provider}]{policy_document.POLICY_LENGTH_TAG_NAME}"

    @property
    def alarm_provider(self) -> str:
        return "default"

    @property
    def operator_url(self) -> str:
        return self.operator_mcp_url or self.base_url.rstrip("/") + OPERATOR_MCP_PATH

    @property
    def configurator_url(self) -> str:
        return self.configurator_mcp_url or self.base_url.rstrip("/") + CONFIGURATOR_MCP_PATH


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


def require_origin(base_url: str, mcp_url: str) -> dict[str, str]:
    """Refuse any URL that is not the exact disposable Gateway origin.

    This runs before the first request, so a mistyped `--base-url` (the real
    Gateway on port 8088 is one keystroke away) cannot be contacted at all. The
    readiness waiter calls it too: every client in this harness sends the CI API
    token, so every one of them needs the same gate.
    """
    expected = f"http://{EXPECTED_ORIGIN_HOST}:{EXPECTED_ORIGIN_PORT}"
    base = urllib.parse.urlsplit(base_url)
    if (
        base.scheme != "http"
        or base.hostname != EXPECTED_ORIGIN_HOST
        or base.port != EXPECTED_ORIGIN_PORT
        or base.path not in ("", "/")
        or base.query
        or base.fragment
    ):
        raise GuardError(f"base URL {base_url!r} is not the disposable Gateway origin {expected}")
    mcp = urllib.parse.urlsplit(mcp_url)
    if (
        mcp.scheme != "http"
        or mcp.hostname != EXPECTED_ORIGIN_HOST
        or mcp.port != EXPECTED_ORIGIN_PORT
        or mcp.path not in EXPECTED_MCP_PATHS
        or mcp.query
        or mcp.fragment
    ):
        raise GuardError(
            f"MCP URL {mcp_url!r} is not {expected} with one of {list(EXPECTED_MCP_PATHS)}"
        )
    return {"baseOrigin": expected, "mcpPaths": list(EXPECTED_MCP_PATHS)}


def require_disposable_origin(config: Config) -> dict[str, str]:
    return require_origin(config.base_url, config.mcp_url)


def verify_guard(config: Config) -> None:
    checks = require_disposable_origin(config)
    checks.update(require_origin(config.base_url, config.operator_url))
    checks.update(require_origin(config.base_url, config.configurator_url))
    if not config.run_id or not config.provider or not config.root_name:
        raise GuardError("run id, policy provider and alarm root must be configured")
    if str(config.run_id) not in config.root_name:
        raise GuardError(
            f"alarm root {config.root_name!r} is not run-unique for run {config.run_id!r}"
        )
    marker_path = Path(config.ci_marker)
    if not marker_path.is_file():
        raise GuardError(f"CI marker {marker_path} is missing; refusing to touch a Gateway")
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    checks.update({
        "marker": marker.get("marker") == EXPECTED_MARKER,
        "environment": marker.get("environment") == PHASE4_ENVIRONMENT,
        "trustedRepo": marker.get("trustedRepo") == TRUSTED_REPO,
        "runId": str(marker.get("runId")) == str(config.run_id),
        "gatewayVersion": str(marker.get("gatewayVersion")) == config.gateway_version,
        "gatewayId": marker.get("gatewayId") == f"phase4-{config.marker_label}-{config.gateway_version}-{config.run_id}",
        "policyProvider": marker.get("policyProvider") == config.provider,
        "alarmRoot": marker.get("alarmRoot") == config.root_name,
        "runtimeProject": marker.get("runtimeProject") == config.runtime_project,
        "auditProfile": marker.get("auditProfile") == config.audit_profile,
    })
    if not all(checks.values()):
        raise GuardError(f"L5 guard failed before any request: {json.dumps(checks, sort_keys=True)}")
    try:
        info = gateway_rest.gateway_info(config.base_url, config.api_token)
    except gateway_rest.RestError as exc:
        # A redirect is refused by the REST client, so a hijacked or rewritten
        # endpoint cannot bounce the identity check onto another origin.
        raise GuardError(f"gateway identity check failed: {exc}") from exc
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
        "runtimeProject": config.runtime_project,
        "auditProfile": config.audit_profile,
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
    # The provider export is the recorded read-back body and the rehearsal replay
    # body, so it is kept whole: truncating it would make the fixture unusable and
    # hide the policy Tag behind the harness's oversize probe pair.
    raw["export"] = bounded(exported_document, 400_000)

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
        "policyLengthPath": config.policy_length_path,
        "oversizePolicyPath": policy_document.OVERSIZE_POLICY_TAG_PATH,
        "oversizeLengthPath": policy_document.OVERSIZE_LENGTH_TAG_PATH,
        "maxPolicyBytes": policy_document.POLICY_MAX_BYTES,
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

    gate = measurement(report, "tag.gatedRead.policy")
    facts["policyGateReported"] = bool(gate)
    facts["policyMaxBytes"] = gate.get("cap", policy_document.POLICY_MAX_BYTES)
    facts["policyGateState"] = str(gate.get("gate", ""))
    facts["policyDeclaredLength"] = gate.get("declaredLength")
    facts["policyDeclaredLengthMatchesAppliedDocument"] = (
        gate.get("declaredLength") == policy_document.policy_byte_length()
    )
    facts["policyGateServedWithoutMaterializingOversize"] = gate.get("gate") == "served"
    facts["policyGatedReadServedAndVerified"] = (
        gate.get("gate") == "served"
        and bool(gate.get("lengthMatchesValue"))
        and gate.get("valueSha256") == policy_document.policy_sha256()
    )
    facts["policyGatedReadElapsedMs"] = gate.get("elapsedMs")
    oversize_gate = measurement(report, "tag.gatedRead.oversize")
    facts["oversizePolicyGateState"] = str(oversize_gate.get("gate", ""))
    facts["oversizePolicyDeclaredLength"] = oversize_gate.get("declaredLength")
    facts["oversizePolicyDeclaredLengthExceedsCap"] = (
        isinstance(oversize_gate.get("declaredLength"), int)
        and oversize_gate["declaredLength"] > policy_document.POLICY_MAX_BYTES
    )
    facts["oversizePolicyMaterialized"] = bool(oversize_gate.get("materialized"))
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
    deadline = time.monotonic() + config.policy_read_deadline_seconds
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
        served_document = bool(facts.get("policyReadQualityIsGood")) and bool(
            facts.get("policyReadMatchesAppliedDocument")
        )
        gate_reported = bool(facts.get("policyGateReported"))
        gate_state = str(facts.get("policyGateState", ""))
        # An over-cap document is a deliberate refusal and must fail at once. A
        # blocked gate (its length Tag unreadable) is the provider-startup case
        # the repair loop exists for, so it keeps retrying instead.
        oversize_refusal = gate_state == "oversize"
        invalid_refusal = gate_state == "invalid"
        deterministic_refusal = oversize_refusal or invalid_refusal
        healthy = served_document and gate_reported and bool(
            facts.get("policyGatedReadServedAndVerified")
        )
        attempt: dict[str, Any] = {
            "servedPolicyTag": served_document,
            "policyReadQuality": str(facts.get("policyReadQuality", "")),
            "policyGateReported": gate_reported,
            "policyGateState": str(facts.get("policyGateState", "")),
            "handlerWriteQualityCodes": facts.get("handlerWriteQualityCodes", []),
            "error": error,
        }
        # Retry only while the provider is not serving the document yet. A served
        # document whose report carries no gate measurement is a stale recorded
        # payload (the expectation drift check reports it), and a gate that
        # answered "oversize"/"blocked" is a deterministic refusal.
        if healthy or deterministic_refusal or (served_document and not gate_reported) or time.monotonic() >= deadline:
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
    if gate_state == "oversize":
        raise StageFailure(
            "the policy size gate refused an oversize document: "
            + json.dumps(attempts[-1], sort_keys=True)[:800]
        )
    if gate_state == "invalid":
        raise StageFailure(
            "the policy size gate refused an invalid declared length: "
            + json.dumps(attempts[-1], sort_keys=True)[:800]
        )
    if not served_document:
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
# Stages: ticket #7 (`tag_write` tracer bullet)
# --------------------------------------------------------------------------- #

def profile_tools(name: str) -> list[str]:
    """The explicit Tool inventory of one Runtime profile (D09)."""
    path = Path(__file__).resolve().parents[3] / "contracts/profiles" / f"{name}.yaml"
    document = json.loads(path.read_text(encoding="utf-8"))
    return [str(tool) for tool in document.get("tools", [])]


def canonical_error(result: dict[str, Any]) -> dict[str, Any]:
    """The canonical D06 error object a Runtime Tool Error carries in its text part."""
    content = result.get("content")
    if isinstance(content, dict):
        content = [content]
    text = content[0].get("text") if isinstance(content, list) and content and isinstance(content[0], dict) else None
    if not isinstance(text, str):
        raise StageFailure(f"Tool Error carried no canonical text part: {json.dumps(result)[:400]}")
    try:
        error = json.loads(text)
    except ValueError as exc:
        raise StageFailure(f"Tool Error text was not JSON: {text[:400]}") from exc
    if not isinstance(error, dict) or not isinstance(error.get("code"), str):
        raise StageFailure(f"Tool Error text was not a canonical error object: {text[:400]}")
    return error


def expect_tool_error(client: mcp_client.McpClient, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    result = client.tool_result(name, arguments)
    if result.get("isError") is not True:
        raise StageFailure(f"{name} was expected to refuse but returned a result: {json.dumps(result)[:600]}")
    return canonical_error(result)


def expect_structured(client: mcp_client.McpClient, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    result = client.tool_result(name, arguments)
    if result.get("isError") is True:
        raise StageFailure(f"{name} failed: {json.dumps(result)[:600]}")
    structured = result.get("structuredContent")
    if not isinstance(structured, dict):
        raise StageFailure(f"{name} returned no structuredContent: {json.dumps(result)[:600]}")
    return structured


def read_tag_value(client: mcp_client.McpClient, path: str) -> dict[str, Any]:
    structured = expect_structured(client, "tag_read", {
        "tagPaths": [path], "timeout": READ_TIMEOUT_MS, "timestampFormat": "iso8601",
    })
    items = structured.get("items")
    if not isinstance(items, list) or not items or not isinstance(items[0], dict):
        raise StageFailure(f"tag_read returned no item for {path}: {json.dumps(structured)[:400]}")
    return items[0]


def tag_fixture_arguments(config: Config) -> dict[str, Any]:
    return {
        "providerRoot": policy_document.TAG_FIXTURE_PROVIDER,
        "rootName": policy_document.TAG_FIXTURE_ROOT,
        "siblingRootName": policy_document.TAG_FIXTURE_SIBLING_ROOT,
        "targets": [
            {"name": "WriteTarget", "dataType": "Int4", "value": 0},
            {"name": "TextTarget", "dataType": "String", "value": "phase4-initial"},
        ],
    }


def tag_write_paths() -> dict[str, str]:
    root = policy_document.TAG_FIXTURE_PATH.rsplit("/", 1)[0]
    return {
        "writeTarget": policy_document.TAG_FIXTURE_PATH,
        "textTarget": root + "/TextTarget",
        "nestedTarget": root + "/Nested/Inner",
        "missingTarget": policy_document.TAG_FIXTURE_MISSING_PATH,
        "siblingTarget": policy_document.TAG_FIXTURE_SIBLING_PATH,
    }


def served_policy_sha(config: Config, client: mcp_client.McpClient) -> str:
    """The policy document the *running* provider serves, read by a Tool handler.

    An accepted `/tags/import` is not proof that the provider serves the Tags
    (ticket #6 recorded both startup failures), so the install is confirmed with
    the same handler-scope read the shipped mutation performs.
    """
    item = read_tag_value(client, config.policy_path)
    value = item.get("value")
    return gateway_rest.sha256_text(value) if isinstance(value, str) else ""


def install_policy(
    config: Config, client: mcp_client.McpClient, *, document: bytes, expected_sha256: str,
    deadline_seconds: float = 150.0,
) -> dict[str, Any]:
    """Import a policy Tag document until a Tool handler reads it back served.

    An accepted `/tags/import` is not proof that the running provider serves the
    Tags (ticket #6 recorded both startup failures), so the install is confirmed
    with the same handler-scope read the shipped mutation performs.
    """
    started = time.monotonic()
    attempts: list[dict[str, Any]] = []
    while True:
        status, payload = gateway_rest.import_tags(
            config.base_url, config.api_token, config.provider, document,
            collision_policy="MergeOverwrite",
        )
        failures = gateway_rest.import_failures(payload)
        served = ""
        served_error = ""
        if status == 200 and failures is None:
            try:
                served = served_policy_sha(config, client)
            except (mcp_client.McpError, StageFailure) as exc:
                served_error = str(exc)
        attempts.append({
            "status": status,
            "failures": bounded(failures, 4000),
            "body": bounded(payload, 4000),
            "servedSha256": served,
            "servedError": served_error[:400],
        })
        if served == expected_sha256:
            return {"ok": True, "attempts": attempts, "attemptCount": len(attempts), "servedSha256": served}
        if time.monotonic() - started >= deadline_seconds:
            return {"ok": False, "attempts": attempts, "attemptCount": len(attempts), "servedSha256": served}
        time.sleep(3.0)


def install_tag_write_policy(
    config: Config, client: mcp_client.McpClient, *, allowlist: tuple[str, ...],
    deadline_seconds: float = 150.0,
) -> dict[str, Any]:
    return install_policy(
        config, client,
        document=policy_document.tag_write_tag_document_bytes(allowlist=allowlist),
        expected_sha256=policy_document.tag_write_policy_sha256(allowlist=allowlist),
        deadline_seconds=deadline_seconds,
    )


def install_alarm_policy(
    config: Config, client: mcp_client.McpClient, *, allowlist: tuple[str, ...],
    shelve_cap: int = policy_document.ALARM_SHELVE_CAP_SECONDS, deadline_seconds: float = 150.0,
) -> dict[str, Any]:
    return install_policy(
        config, client,
        document=policy_document.alarm_policy_tag_document_bytes(allowlist=allowlist, shelve_cap=shelve_cap),
        expected_sha256=policy_document.alarm_policy_sha256(allowlist=allowlist, shelve_cap=shelve_cap),
        deadline_seconds=deadline_seconds,
    )


def stage_tag_write_no_policy(config: Config) -> dict[str, Any]:
    """A Gateway with no Runtime Target Policy must refuse the Mutation."""
    raw: dict[str, Any] = {}
    facts: dict[str, Any] = {}
    client = mcp_client.McpClient(config.operator_url, config.api_token)
    raw["initialize"] = bounded(client.initialize())
    paths = tag_write_paths()
    error = expect_tool_error(client, "tag_write", {
        "writes": [{"path": paths["writeTarget"], "value": 1}], "timeout": READ_TIMEOUT_MS,
    })
    raw["noPolicy"] = bounded(error)
    details = error.get("details") or {}
    facts["tagWriteNoPolicyErrorCode"] = str(error.get("code", ""))
    facts["tagWriteNoPolicyReason"] = str(details.get("reason", ""))
    facts["tagWriteNoPolicyFailsClosed"] = facts["tagWriteNoPolicyErrorCode"] == "operation_disabled"
    if not facts["tagWriteNoPolicyFailsClosed"]:
        raise StageFailure(f"a missing Runtime Target Policy must fail closed: {json.dumps(error)[:600]}")
    return {
        "stage": "tag-write-no-policy",
        "ok": True,
        "identity": identity(config),
        "guard": config.guard,
        "facts": facts,
        "raw": raw,
    }


def stage_tag_write_setup(config: Config) -> dict[str, Any]:
    """Test-only provisioning: fixture Tags, the audit profile and the policy state."""
    raw: dict[str, Any] = {}
    facts: dict[str, Any] = {}
    probe = mcp_client.McpClient(config.mcp_url, config.api_token)
    raw["probeInitialize"] = bounded(probe.initialize())
    tools = probe.tools_list()
    if "tag_fixture_probe" not in tools:
        raise StageFailure(f"tag_fixture_probe is not discoverable; tools/list = {sorted(tools)}")
    report = expect_structured(probe, "tag_fixture_probe", tag_fixture_arguments(config))
    raw["tagFixtureProbe"] = bounded(report, 40_000)
    fixture_paths = [str(path) for path in report.get("fixturePaths") or []]
    sibling_path = str(report.get("siblingPath", ""))
    facts["tagFixtureConfigured"] = bool(report.get("configured"))
    facts["tagFixtureReadable"] = bool(report.get("fixtureReadable"))
    facts["tagFixturePaths"] = fixture_paths
    facts["tagFixtureSiblingPath"] = sibling_path
    expected = tag_write_paths()
    facts["tagFixturePathsMatch"] = (
        sorted(fixture_paths) == sorted([expected["writeTarget"], expected["textTarget"]])
        and sibling_path == expected["siblingTarget"]
    )
    if not facts["tagFixtureConfigured"] or not facts["tagFixtureReadable"] or not facts["tagFixturePathsMatch"]:
        raise StageFailure(f"the tag_write fixture Tags were not created: {json.dumps(report)[:600]}")

    status, payload = gateway_rest.create_resource(
        config.base_url, config.api_token, "ignition/audit-profile",
        policy_document.audit_profile_resource(),
    )
    raw["createAuditProfile"] = {"status": status, "body": bounded(payload)}
    find_status, found = gateway_rest.find_resource(
        config.base_url, config.api_token, "ignition/audit-profile", config.audit_profile,
    )
    raw["auditProfileResource"] = {"status": find_status, "body": bounded(found)}
    facts["auditProfileCreateStatus"] = status
    facts["auditProfileAvailable"] = find_status == 200 and isinstance(found, dict)
    if not facts["auditProfileAvailable"]:
        raise StageFailure(
            f"the {config.audit_profile} audit profile is not readable after provisioning: "
            f"HTTP {find_status}"
        )

    operator = mcp_client.McpClient(config.operator_url, config.api_token)
    raw["operatorInitialize"] = bounded(operator.initialize())
    installed = install_tag_write_policy(config, operator, allowlist=policy_document.TAG_WRITE_ALLOWLIST)
    raw["installPolicy"] = bounded(installed, 20_000)
    facts["tagWritePolicyInstallAttempts"] = installed["attemptCount"]
    facts["tagWritePolicyInstalled"] = installed["ok"]
    facts["tagWritePolicyServedSha256"] = installed["servedSha256"]
    if not installed["ok"]:
        raise StageFailure(
            "the running provider never served the tag_write policy document: "
            f"{json.dumps(installed['attempts'][-1], sort_keys=True)[:800]}"
        )
    return {
        "stage": "tag-write-setup",
        "ok": True,
        "identity": identity(config),
        "guard": config.guard,
        "facts": facts,
        "raw": raw,
    }


def stage_tag_write(config: Config) -> dict[str, Any]:
    """The ticket #7 live cases: allowlisted write, two refusals, reserved provider."""
    raw: dict[str, Any] = {}
    facts: dict[str, Any] = {}
    paths = tag_write_paths()
    client = mcp_client.McpClient(config.operator_url, config.api_token)
    raw["initialize"] = bounded(client.initialize())

    inventory = sorted(client.tools_list())
    expected_inventory = sorted(profile_tools("operator"))
    facts["tagWriteOperatorInventory"] = inventory
    facts["tagWriteOperatorProfile"] = expected_inventory
    facts["tagWriteOperatorInventoryMatchesProfile"] = inventory == expected_inventory
    if not facts["tagWriteOperatorInventoryMatchesProfile"]:
        raise StageFailure(
            f"the deployed operator inventory does not equal contracts/profiles/operator.yaml: "
            f"{inventory} != {expected_inventory}"
        )

    # Case 1: an allowlisted batch whose items are descendants of the allowlisted
    # prefix, plus one missing path so a Bad Native outcome is exercised too.
    batch = {
        "writes": [
            {"path": paths["writeTarget"], "value": 22},
            {"path": paths["textTarget"], "value": "phase4-written"},
            {"path": paths["nestedTarget"], "value": 7},
            {"path": paths["missingTarget"], "value": 5},
        ],
        "timeout": READ_TIMEOUT_MS,
    }
    structured = expect_structured(client, "tag_write", batch)
    raw["allowlistedBatch"] = bounded(structured, 40_000)
    summary = structured.get("summary") or {}
    items = structured.get("items") or []
    observed = structured.get("observed") or []
    qualities = [str((item.get("quality") or {}).get("name", "")) for item in items]
    facts["tagWriteBatchRequested"] = summary.get("requested")
    facts["tagWriteBatchSucceeded"] = summary.get("succeeded")
    facts["tagWriteBatchFailed"] = summary.get("failed")
    facts["tagWriteBatchOutcomeUnknown"] = summary.get("outcomeUnknown")
    facts["tagWriteBatchQualityNames"] = qualities
    facts["tagWriteBatchNativeOutcomes"] = (
        len(qualities) == 4
        and all(name.startswith("Good") for name in qualities[:3])
        and qualities[3].startswith("Bad")
    )
    facts["tagWriteObservedMatchesWritten"] = (
        len(observed) == 4
        and observed[0].get("value") == 22
        and observed[1].get("value") == "phase4-written"
        and observed[2].get("value") == 7
    )
    facts["tagWriteObservedMissingQualityIsBad"] = (
        len(observed) == 4 and (observed[3].get("quality") or {}).get("good") is False
    )
    facts["tagWriteAuditMode"] = str(summary.get("auditMode", ""))
    facts["tagWriteAuditRecorded"] = summary.get("auditRecorded")
    correlation = str((structured.get("meta") or {}).get("correlationId", ""))
    audit_status, rows = gateway_rest.audit_rows(
        config.base_url, config.api_token, config.audit_profile, action="ignition-mcp.tag_write",
    )
    matching = [row for row in rows if correlation and correlation in str(row.get("actionValue", ""))]
    raw["auditQuery"] = {"status": audit_status, "rows": bounded(matching, 20_000)}
    facts["tagWriteAuditCorrelationIdPresent"] = bool(correlation)
    facts["tagWriteAuditRowsForCorrelation"] = len(matching)
    facts["tagWriteAuditAttemptAndResultRecorded"] = len(matching) >= 2
    facts["tagWriteAuditActorIsServiceIdentity"] = bool(matching) and all(
        str(row.get("actor", "")) == policy_document.SERVICE_IDENTITY for row in matching
    )

    # Case 2: [default]IgnitionMCP_CI2 shares a string prefix with the allowlisted
    # [default]IgnitionMCP_CI but is a different path segment.
    denied = expect_tool_error(client, "tag_write", {
        "writes": [{"path": paths["siblingTarget"], "value": 1}], "timeout": READ_TIMEOUT_MS,
    })
    raw["siblingDenial"] = bounded(denied)
    sibling_details = (denied.get("details") or {}).get("items") or [{}]
    facts["tagWriteSiblingDenialCode"] = str(denied.get("code", ""))
    facts["tagWriteSiblingDenialReason"] = str(sibling_details[0].get("reason", ""))
    facts["tagWriteSiblingDenialIsSegmentBoundary"] = (
        facts["tagWriteSiblingDenialCode"] == "permission_denied"
        and facts["tagWriteSiblingDenialReason"] == "targetNotAllowlisted"
    )
    if not facts["tagWriteSiblingDenialIsSegmentBoundary"]:
        raise StageFailure(f"the segment-boundary sibling was not refused: {json.dumps(denied)[:600]}")
    sibling_after = read_tag_value(client, paths["siblingTarget"])
    facts["tagWriteSiblingValueUnchanged"] = sibling_after.get("value") == 0

    # Case 3: one refused item rejects the whole batch before anything executes.
    before = read_tag_value(client, paths["writeTarget"])
    whole_batch = expect_tool_error(client, "tag_write", {
        "writes": [
            {"path": paths["writeTarget"], "value": 42},
            {"path": paths["siblingTarget"], "value": 1},
        ],
        "timeout": READ_TIMEOUT_MS,
    })
    raw["preflightRefusal"] = bounded(whole_batch)
    after = read_tag_value(client, paths["writeTarget"])
    facts["tagWritePreflightRefusalCode"] = str(whole_batch.get("code", ""))
    facts["tagWritePreflightRefusalItems"] = len((whole_batch.get("details") or {}).get("items") or [])
    facts["tagWritePreflightExecutedNothing"] = (
        before.get("value") == after.get("value") == 22
    )
    if not facts["tagWritePreflightExecutedNothing"]:
        raise StageFailure("a refused Preflight executed part of its batch")

    # Case 4: with an explicit * allowlist the reserved provider is still refused.
    installed = install_tag_write_policy(config, client, allowlist=policy_document.WILDCARD_ALLOWLIST)
    raw["installWildcardPolicy"] = bounded(installed, 20_000)
    facts["tagWriteWildcardPolicyInstalled"] = installed["ok"]
    if not installed["ok"]:
        raise StageFailure(
            "the running provider never served the wildcard policy document: "
            f"{json.dumps(installed['attempts'][-1], sort_keys=True)[:800]}"
        )
    probe_before = read_tag_value(client, config.write_probe_path).get("value")
    policy_before = read_tag_value(client, config.policy_path).get("value")
    reserved = expect_tool_error(client, "tag_write", {
        "writes": [{"path": config.write_probe_path, "value": "phase4-clobber-attempt"}],
        "timeout": READ_TIMEOUT_MS,
    })
    raw["reservedProviderRefusal"] = bounded(reserved)
    reserved_details = (reserved.get("details") or {}).get("items") or [{}]
    facts["tagWriteReservedProviderCode"] = str(reserved.get("code", ""))
    facts["tagWriteReservedProviderReason"] = str(reserved_details[0].get("reason", ""))
    facts["tagWriteReservedProviderRefusedUnderWildcard"] = (
        facts["tagWriteReservedProviderCode"] == "permission_denied"
        and facts["tagWriteReservedProviderReason"] == "reservedProvider"
    )
    if not facts["tagWriteReservedProviderRefusedUnderWildcard"]:
        raise StageFailure(f"the reserved provider was not refused: {json.dumps(reserved)[:600]}")
    probe_after = read_tag_value(client, config.write_probe_path).get("value")
    policy_after = read_tag_value(client, config.policy_path).get("value")
    facts["tagWriteReservedProviderValueUnchanged"] = probe_before == probe_after
    facts["tagWritePolicyDocumentUnclobbered"] = policy_before == policy_after
    return {
        "stage": "tag-write",
        "ok": True,
        "identity": identity(config),
        "guard": config.guard,
        "facts": facts,
        "raw": raw,
    }


# --------------------------------------------------------------------------- #
# Stages: ticket #10 (`tag_update` and the Tag config fingerprint)
# --------------------------------------------------------------------------- #

def tag_update_paths() -> dict[str, str]:
    root = policy_document.TAG_FIXTURE_ROOT
    return {
        "target": policy_document.TAG_UPDATE_TARGET,
        "textTarget": policy_document.TAG_UPDATE_TEXT_TARGET,
        "nestedTarget": f"[{policy_document.TAG_FIXTURE_PROVIDER}]{root}/Nested/Inner",
        "missingTarget": policy_document.TAG_FIXTURE_MISSING_PATH,
        "nestedFolder": policy_document.TAG_UPDATE_FOLDER,
        "siblingTarget": policy_document.TAG_FIXTURE_SIBLING_PATH,
        "udtTarget": policy_document.TAG_UPDATE_UDT_TARGET,
    }


def install_tag_update_policy(
    config: Config, client: mcp_client.McpClient, *, allowlist: tuple[str, ...],
    audit_mode: str = "best_effort", deadline_seconds: float = 150.0,
) -> dict[str, Any]:
    """Install the ticket #10 policy over the same reserved provider."""
    return install_policy(
        config, client,
        document=policy_document.tag_update_tag_document_bytes(allowlist=allowlist, audit_mode=audit_mode),
        expected_sha256=policy_document.tag_update_policy_sha256(allowlist=allowlist, audit_mode=audit_mode),
        deadline_seconds=deadline_seconds,
    )


def tag_config(client: mcp_client.McpClient, path: str) -> dict[str, Any]:
    """One `tag_get_config` read: the configuration and the fingerprint a caller
    hands back to tag_update."""
    structured = expect_structured(client, "tag_get_config", {
        "path": path, "recursive": False, "overridesOnly": False, "maxResults": 50,
    })
    fingerprint = structured.get("fingerprint")
    configuration = structured.get("configuration")
    if not isinstance(fingerprint, str) or not fingerprint.startswith("tcf1:"):
        raise StageFailure(f"tag_get_config returned no tcf1 fingerprint for {path}: {json.dumps(structured)[:400]}")
    if not isinstance(configuration, list):
        raise StageFailure(f"tag_get_config returned no configuration for {path}: {json.dumps(structured)[:400]}")
    return {"fingerprint": fingerprint, "configuration": configuration}


def tag_config_or_none(client: mcp_client.McpClient, path: str) -> dict[str, Any] | None:
    """One `tag_get_config` read that tolerates a Tool Error, for diagnostics.

    The ticket #10 live evidence needs the *shape* of what a Gateway answers for a
    path that is not there; the recorded fake answers a Tool Error for it, so a
    rehearsal records the refusal instead of the template.
    """
    try:
        return tag_config(client, path)
    except (StageFailure, mcp_client.McpError):
        return None


def derived_fingerprint(configuration: Any) -> str:
    """The documented rule, applied outside the handler.

    D30 2 defines the Tag config fingerprint over the D28-encoded configuration
    the same read publishes, so a live read lets the driver recompute it with the
    contracts linter's own copy of the rule: the handler's Jython implementation
    and the repository's Python one then have to agree on real Gateway data.
    """
    return lint.tag_config_fingerprint(lint.encode_nulls(configuration))


def stage_tag_update_no_policy(config: Config) -> dict[str, Any]:
    """A Gateway with no Runtime Target Policy must refuse the CONFIG Mutation."""
    raw: dict[str, Any] = {}
    facts: dict[str, Any] = {}
    client = mcp_client.McpClient(config.configurator_url, config.api_token)
    raw["initialize"] = bounded(client.initialize())
    paths = tag_update_paths()
    error = expect_tool_error(client, "tag_update", {
        "items": [{"path": paths["target"], "expectedFingerprint": "tcf1:" + "0" * 64,
                   "config": {"documentation": "phase4-updated"}}],
    })
    raw["noPolicy"] = bounded(error)
    details = error.get("details") or {}
    facts["tagUpdateNoPolicyErrorCode"] = str(error.get("code", ""))
    facts["tagUpdateNoPolicyReason"] = str(details.get("reason", ""))
    facts["tagUpdateNoPolicyFailsClosed"] = facts["tagUpdateNoPolicyErrorCode"] == "operation_disabled"
    if not facts["tagUpdateNoPolicyFailsClosed"]:
        raise StageFailure(f"a missing Runtime Target Policy must fail closed: {json.dumps(error)[:600]}")
    return {
        "stage": "tag-update-no-policy",
        "ok": True,
        "identity": identity(config),
        "guard": config.guard,
        "facts": facts,
        "raw": raw,
    }


def stage_tag_update_setup(config: Config) -> dict[str, Any]:
    """Test-only provisioning for the ticket #10 cases."""
    raw: dict[str, Any] = {}
    facts: dict[str, Any] = {}
    probe = mcp_client.McpClient(config.mcp_url, config.api_token)
    raw["probeInitialize"] = bounded(probe.initialize())
    tools = probe.tools_list()
    if "tag_fixture_probe" not in tools:
        raise StageFailure(f"tag_fixture_probe is not discoverable; tools/list = {sorted(tools)}")
    report = expect_structured(probe, "tag_fixture_probe", tag_fixture_arguments(config))
    raw["tagFixtureProbe"] = bounded(report, 40_000)
    facts["tagUpdateFixtureConfigured"] = bool(report.get("configured"))
    facts["tagUpdateFixtureReadable"] = bool(report.get("fixtureReadable"))
    if not facts["tagUpdateFixtureConfigured"] or not facts["tagUpdateFixtureReadable"]:
        raise StageFailure(f"the Tag fixtures were not created: {json.dumps(report)[:600]}")

    status, payload = gateway_rest.create_resource(
        config.base_url, config.api_token, "ignition/audit-profile",
        policy_document.audit_profile_resource(),
    )
    raw["createAuditProfile"] = {"status": status, "body": bounded(payload)}
    find_status, found = gateway_rest.find_resource(
        config.base_url, config.api_token, "ignition/audit-profile", config.audit_profile,
    )
    raw["auditProfileResource"] = {"status": find_status, "body": bounded(found)}
    facts["tagUpdateAuditProfileAvailable"] = find_status == 200 and isinstance(found, dict)
    if not facts["tagUpdateAuditProfileAvailable"]:
        raise StageFailure(
            f"the {config.audit_profile} audit profile is not readable after provisioning: HTTP {find_status}"
        )

    configurator = mcp_client.McpClient(config.configurator_url, config.api_token)
    raw["configuratorInitialize"] = bounded(configurator.initialize())
    installed = install_tag_update_policy(config, configurator, allowlist=policy_document.TAG_UPDATE_ALLOWLIST)
    raw["installPolicy"] = bounded(installed, 20_000)
    facts["tagUpdatePolicyInstallAttempts"] = installed["attemptCount"]
    facts["tagUpdatePolicyInstalled"] = installed["ok"]
    facts["tagUpdatePolicyServedSha256"] = installed["servedSha256"]
    if not installed["ok"]:
        raise StageFailure(
            "the running provider never served the tag_update policy: "
            f"{json.dumps(installed['attempts'][-1], sort_keys=True)[:800]}"
        )
    return {
        "stage": "tag-update-setup",
        "ok": True,
        "identity": identity(config),
        "guard": config.guard,
        "facts": facts,
        "raw": raw,
    }


def stage_tag_update(config: Config) -> dict[str, Any]:
    """The ticket #10 live cases: the fingerprint, the merge-update, the refusals."""
    raw: dict[str, Any] = {}
    facts: dict[str, Any] = {}
    paths = tag_update_paths()
    client = mcp_client.McpClient(config.configurator_url, config.api_token)
    raw["initialize"] = bounded(client.initialize())

    # The CONFIG class is deployed on the configurator profile, and the CONTROL
    # profile must not carry it: each profile's tools/list equals its contract list
    # exactly (D09 and the G4 acceptance item).
    inventory = sorted(client.tools_list())
    expected_inventory = sorted(profile_tools("configurator"))
    facts["tagUpdateConfiguratorInventory"] = inventory
    facts["tagUpdateConfiguratorProfile"] = expected_inventory
    facts["tagUpdateConfiguratorInventoryMatchesProfile"] = inventory == expected_inventory
    if not facts["tagUpdateConfiguratorInventoryMatchesProfile"]:
        raise StageFailure(
            "the deployed configurator inventory does not equal contracts/profiles/configurator.yaml: "
            f"{inventory} != {expected_inventory}"
        )
    operator = mcp_client.McpClient(config.operator_url, config.api_token)
    raw["operatorInitialize"] = bounded(operator.initialize())
    operator_inventory = sorted(operator.tools_list())
    facts["tagUpdateOperatorInventory"] = operator_inventory
    facts["tagUpdateOperatorInventoryExcludesConfigMutation"] = (
        "tag_update" not in operator_inventory and operator_inventory == sorted(profile_tools("operator"))
    )
    if not facts["tagUpdateOperatorInventoryExcludesConfigMutation"]:
        raise StageFailure(
            f"the CONTROL profile must not serve a CONFIG Mutation: {operator_inventory}"
        )

    # Case 1: the fingerprint a caller reads is the token the handler compares, and
    # the repository's own copy of the D30 rule derives it from the published
    # configuration.
    before = tag_config(client, paths["target"])
    facts["tagUpdateFingerprintForm"] = before["fingerprint"].startswith("tcf1:") and len(before["fingerprint"]) == 69
    facts["tagUpdateFingerprintRecomputesFromPublishedConfiguration"] = (
        derived_fingerprint(before["configuration"]) == before["fingerprint"]
    )
    second = tag_config(client, paths["target"])
    facts["tagUpdateFingerprintStableAcrossReads"] = second["fingerprint"] == before["fingerprint"]
    facts["tagUpdateConfigurationShape"] = sorted(
        str(key) for key in (before["configuration"][0] if before["configuration"] else {}).keys()
    )
    raw["tagGetConfigBefore"] = bounded(before, 20_000)
    if not facts["tagUpdateFingerprintRecomputesFromPublishedConfiguration"]:
        raise StageFailure(
            "the published fingerprint is not the documented rule over the published configuration: "
            f"{before['fingerprint']} != {derived_fingerprint(before['configuration'])}"
        )

    # Case 2: an allowlisted merge-update, confirmed by an independent re-read.
    structured = expect_structured(client, "tag_update", {
        "items": [{
            "path": paths["target"],
            "expectedFingerprint": before["fingerprint"],
            "config": dict(policy_document.TAG_UPDATE_CONFIG),
        }],
    })
    raw["allowlistedUpdate"] = bounded(structured, 40_000)
    items = structured.get("items") or [{}]
    summary = structured.get("summary") or {}
    observed = structured.get("observed") or [{}]
    facts["tagUpdateUpdateStatus"] = str(items[0].get("status", ""))
    facts["tagUpdateUpdateNativeOutcome"] = str((items[0].get("nativeOutcome") or {}).get("name", ""))
    facts["tagUpdateUpdateSucceeded"] = summary.get("succeeded")
    facts["tagUpdateUpdateAuditMode"] = str(summary.get("auditMode", ""))
    facts["tagUpdateUpdateAuditRecorded"] = summary.get("auditRecorded")
    facts["tagUpdateObservedFingerprintChanged"] = bool(
        observed and observed[0].get("fingerprint") not in (None, before["fingerprint"])
    )
    after = tag_config(client, paths["target"])
    raw["tagGetConfigAfter"] = bounded(after, 20_000)
    merged = (after["configuration"][0] if after["configuration"] else {})
    facts["tagUpdateIndependentReadShowsTheChange"] = (
        merged.get("documentation") == policy_document.TAG_UPDATE_CONFIG["documentation"]
        and merged.get("engUnits") == policy_document.TAG_UPDATE_CONFIG["engUnits"]
    )
    facts["tagUpdateIndependentReadMatchesObserved"] = (
        bool(observed) and observed[0].get("fingerprint") == after["fingerprint"]
    )
    facts["tagUpdateFingerprintAfterDerivable"] = derived_fingerprint(after["configuration"]) == after["fingerprint"]
    if not (
        facts["tagUpdateUpdateStatus"] == "executed"
        and facts["tagUpdateUpdateNativeOutcome"].startswith("Good")
        and facts["tagUpdateIndependentReadShowsTheChange"]
    ):
        raise StageFailure(f"the allowlisted update did not apply: {json.dumps(structured)[:800]}")

    correlation = str((structured.get("meta") or {}).get("correlationId", ""))
    audit_status, rows = gateway_rest.audit_rows(
        config.base_url, config.api_token, config.audit_profile, action="ignition-mcp.tag_update",
    )
    matching = [row for row in rows if correlation and correlation in str(row.get("actionValue", ""))]
    raw["auditQuery"] = {"status": audit_status, "rows": bounded(matching, 20_000)}
    facts["tagUpdateAuditCorrelationIdPresent"] = bool(correlation)
    facts["tagUpdateAuditRowsForCorrelation"] = len(matching)
    facts["tagUpdateAuditAttemptAndResultRecorded"] = len(matching) >= 2
    facts["tagUpdateAuditActorIsServiceIdentity"] = bool(matching) and all(
        str(row.get("actor", "")) == policy_document.SERVICE_IDENTITY for row in matching
    )

    # Case 2b: a Folder is a target like any other, and `system.tag.exists` answers
    # for one: the merge lands on the folder node and the independent read shows it.
    folder = paths["nestedFolder"]
    folder_before = tag_config(client, folder)
    folder_structured = expect_structured(client, "tag_update", {
        "items": [{
            "path": folder,
            "expectedFingerprint": folder_before["fingerprint"],
            "config": {"documentation": policy_document.TAG_UPDATE_FOLDER_CONFIG},
        }],
    })
    raw["folderUpdate"] = bounded(folder_structured, 20_000)
    folder_after = tag_config(client, folder)
    folder_items = folder_structured.get("items") or [{}]
    folder_node = folder_after["configuration"][0] if folder_after["configuration"] else {}
    facts["tagUpdateFolderTargetStatus"] = str(folder_items[0].get("status", ""))
    facts["tagUpdateFolderTargetNativeOutcome"] = str((folder_items[0].get("nativeOutcome") or {}).get("name", ""))
    facts["tagUpdateFolderTargetIndependentReadShowsTheChange"] = (
        folder_node.get("documentation") == policy_document.TAG_UPDATE_FOLDER_CONFIG
    )
    facts["tagUpdateFolderTargetFingerprintChanged"] = folder_after["fingerprint"] != folder_before["fingerprint"]

    # Case 3: the token from before the change is stale now, so the same call is
    # refused and the target keeps the values the first call wrote.
    stale = expect_tool_error(client, "tag_update", {
        "items": [{
            "path": paths["target"],
            "expectedFingerprint": before["fingerprint"],
            "config": {"documentation": "phase4-should-not-apply"},
        }],
    })
    raw["staleFingerprint"] = bounded(stale)
    stale_items = (stale.get("details") or {}).get("items") or [{}]
    facts["tagUpdateStaleFingerprintCode"] = str(stale.get("code", ""))
    facts["tagUpdateStaleFingerprintReason"] = str(stale_items[0].get("reason", ""))
    facts["tagUpdateStaleFingerprintIsConflict"] = (
        facts["tagUpdateStaleFingerprintCode"] == "conflict"
        and facts["tagUpdateStaleFingerprintReason"] == "fingerprintMismatch"
    )
    unchanged = tag_config(client, paths["target"])
    facts["tagUpdateStaleFingerprintChangedNothing"] = (
        unchanged["fingerprint"] == after["fingerprint"]
        and unchanged["configuration"] == after["configuration"]
    )
    if not facts["tagUpdateStaleFingerprintIsConflict"]:
        raise StageFailure(f"a stale Tag config fingerprint must be conflict: {json.dumps(stale)[:600]}")
    if not facts["tagUpdateStaleFingerprintChangedNothing"]:
        raise StageFailure("a refused stale fingerprint changed the target")

    # Case 4: a target that is not there is not_found, and nothing is created.
    absent = expect_tool_error(client, "tag_update", {
        "items": [{
            "path": paths["missingTarget"],
            "expectedFingerprint": "tcf1:" + "0" * 64,
            "config": {"documentation": "phase4-should-not-apply"},
        }],
    })
    raw["missingTarget"] = bounded(absent)
    absent_items = (absent.get("details") or {}).get("items") or [{}]
    facts["tagUpdateMissingTargetCode"] = str(absent.get("code", ""))
    facts["tagUpdateMissingTargetReason"] = str(absent_items[0].get("reason", ""))
    facts["tagUpdateNeverCreatesTarget"] = (
        facts["tagUpdateMissingTargetCode"] == "not_found"
        and facts["tagUpdateMissingTargetReason"] == "targetMissing"
    )
    if not facts["tagUpdateNeverCreatesTarget"]:
        raise StageFailure(f"a missing target must be not_found: {json.dumps(absent)[:600]}")
    # The read alone cannot answer "is it there": a Gateway answers a configuration
    # read for a path that is not there with a synthesized default node. The evidence
    # records that shape and then proves absence through the provider's own export.
    missing_read = tag_config_or_none(client, paths["missingTarget"])
    raw["missingTargetRead"] = bounded(missing_read, 8_000)
    facts["tagUpdateMissingTargetReadAnswersATemplate"] = bool(
        missing_read and missing_read.get("configuration")
    )
    facts["tagUpdateMissingTargetReadFingerprint"] = (missing_read or {}).get("fingerprint", "")
    export_status, exported = gateway_rest.export_tags(config.base_url, config.api_token, "default")
    found = policy_document.find_tag(gateway_rest.decode(exported), "Missing") if export_status == 200 else None
    raw["missingTargetExport"] = {"status": export_status, "found": bounded(found, 2_000)}
    facts["tagUpdateMissingTargetAbsentFromExport"] = export_status == 200 and found is None
    if not facts["tagUpdateMissingTargetAbsentFromExport"]:
        raise StageFailure("the refused update left a Tag that the provider export shows")

    # Case 5: the segment-boundary sibling is refused and untouched.
    sibling = expect_tool_error(client, "tag_update", {
        "items": [{
            "path": paths["siblingTarget"],
            "expectedFingerprint": "tcf1:" + "0" * 64,
            "config": {"documentation": "phase4-should-not-apply"},
        }],
    })
    raw["siblingDenial"] = bounded(sibling)
    sibling_items = (sibling.get("details") or {}).get("items") or [{}]
    facts["tagUpdateSiblingDenialCode"] = str(sibling.get("code", ""))
    facts["tagUpdateSiblingDenialReason"] = str(sibling_items[0].get("reason", ""))
    facts["tagUpdateSiblingDenialIsSegmentBoundary"] = (
        facts["tagUpdateSiblingDenialCode"] == "permission_denied"
        and facts["tagUpdateSiblingDenialReason"] == "targetNotAllowlisted"
    )
    if not facts["tagUpdateSiblingDenialIsSegmentBoundary"]:
        raise StageFailure(f"the segment-boundary sibling was not refused: {json.dumps(sibling)[:600]}")
    sibling_value = read_tag_value(client, paths["siblingTarget"])
    facts["tagUpdateSiblingValueUnchanged"] = sibling_value.get("value") == 0

    # Case 6: a UDT definition is refused while the allowlist is a plain prefix,
    # even though the target starts with the same characters.
    plain_udt = expect_tool_error(client, "tag_update", {
        "items": [{
            "path": paths["udtTarget"],
            "expectedFingerprint": "tcf1:" + "0" * 64,
            "config": {"documentation": "phase4-should-not-apply"},
        }],
    })
    raw["udtUnderPlainPrefix"] = bounded(plain_udt)
    plain_items = (plain_udt.get("details") or {}).get("items") or [{}]
    facts["tagUpdateUdtUnderPlainPrefixCode"] = str(plain_udt.get("code", ""))
    facts["tagUpdateUdtUnderPlainPrefixReason"] = str(plain_items[0].get("reason", ""))
    facts["tagUpdateUdtNeedsExplicitTypesEntry"] = (
        facts["tagUpdateUdtUnderPlainPrefixCode"] == "permission_denied"
        and facts["tagUpdateUdtUnderPlainPrefixReason"] == "udtDefinitionNotAllowlisted"
    )
    if not facts["tagUpdateUdtNeedsExplicitTypesEntry"]:
        raise StageFailure(f"a UDT definition needs an explicit _types_ entry: {json.dumps(plain_udt)[:600]}")

    # Case 7: an explicit _types_ entry lets the target through to the existence
    # check, which is what proves the entry is honoured rather than ignored.
    installed = install_tag_update_policy(
        config, client, allowlist=policy_document.TAG_UPDATE_TYPES_ALLOWLIST,
    )
    raw["installTypesPolicy"] = bounded(installed, 20_000)
    facts["tagUpdateTypesPolicyInstalled"] = installed["ok"]
    if not installed["ok"]:
        raise StageFailure(
            "the running provider never served the _types_ policy: "
            f"{json.dumps(installed['attempts'][-1], sort_keys=True)[:800]}"
        )
    honoured = expect_tool_error(client, "tag_update", {
        "items": [{
            "path": paths["udtTarget"],
            "expectedFingerprint": "tcf1:" + "0" * 64,
            "config": {"documentation": "phase4-should-not-apply"},
        }],
    })
    raw["udtUnderTypesEntry"] = bounded(honoured)
    honoured_items = (honoured.get("details") or {}).get("items") or [{}]
    facts["tagUpdateUdtUnderTypesEntryCode"] = str(honoured.get("code", ""))
    facts["tagUpdateUdtUnderTypesEntryReason"] = str(honoured_items[0].get("reason", ""))
    facts["tagUpdateTypesEntryIsHonoured"] = (
        facts["tagUpdateUdtUnderTypesEntryCode"] == "not_found"
        and facts["tagUpdateUdtUnderTypesEntryReason"] == "targetMissing"
    )
    if not facts["tagUpdateTypesEntryIsHonoured"]:
        raise StageFailure(
            f"the explicit _types_ entry was not honoured: {json.dumps(honoured)[:600]}"
        )

    # Case 8: with an explicit * the reserved provider is still refused, for a Tag
    # and for an Alarm path, and neither the probe Tag nor the policy document moves.
    wildcard = install_tag_update_policy(config, client, allowlist=policy_document.WILDCARD_ALLOWLIST)
    raw["installWildcardPolicy"] = bounded(wildcard, 20_000)
    facts["tagUpdateWildcardPolicyInstalled"] = wildcard["ok"]
    if not wildcard["ok"]:
        raise StageFailure(
            "the running provider never served the wildcard policy: "
            f"{json.dumps(wildcard['attempts'][-1], sort_keys=True)[:800]}"
        )
    probe_before = read_tag_value(client, config.write_probe_path).get("value")
    policy_before = read_tag_value(client, config.policy_path).get("value")
    reserved_tag = expect_tool_error(client, "tag_update", {
        "items": [{
            "path": config.write_probe_path,
            "expectedFingerprint": "tcf1:" + "0" * 64,
            "config": {"documentation": "phase4-clobber-attempt"},
        }],
    })
    raw["reservedProviderRefusal"] = bounded(reserved_tag)
    reserved_items = (reserved_tag.get("details") or {}).get("items") or [{}]
    facts["tagUpdateReservedProviderCode"] = str(reserved_tag.get("code", ""))
    facts["tagUpdateReservedProviderReason"] = str(reserved_items[0].get("reason", ""))
    facts["tagUpdateReservedProviderRefusedUnderWildcard"] = (
        facts["tagUpdateReservedProviderCode"] == "permission_denied"
        and facts["tagUpdateReservedProviderReason"] == "reservedProvider"
    )
    probe_after = read_tag_value(client, config.write_probe_path).get("value")
    policy_after = read_tag_value(client, config.policy_path).get("value")
    facts["tagUpdateReservedProviderValueUnchanged"] = probe_before == probe_after
    facts["tagUpdatePolicyDocumentUnclobbered"] = policy_before == policy_after
    if not facts["tagUpdateReservedProviderRefusedUnderWildcard"]:
        raise StageFailure(f"the reserved provider was not refused: {json.dumps(reserved_tag)[:600]}")

    # Case 9: a UDT definition is still refused under a bare *, per D30 6.
    wildcard_udt = expect_tool_error(client, "tag_update", {
        "items": [{
            "path": paths["udtTarget"],
            "expectedFingerprint": "tcf1:" + "0" * 64,
            "config": {"documentation": "phase4-should-not-apply"},
        }],
    })
    raw["udtUnderWildcard"] = bounded(wildcard_udt)
    wildcard_items = (wildcard_udt.get("details") or {}).get("items") or [{}]
    facts["tagUpdateUdtUnderWildcardCode"] = str(wildcard_udt.get("code", ""))
    facts["tagUpdateUdtUnderWildcardReason"] = str(wildcard_items[0].get("reason", ""))
    facts["tagUpdateBareWildcardDoesNotCoverUdt"] = (
        facts["tagUpdateUdtUnderWildcardCode"] == "permission_denied"
        and facts["tagUpdateUdtUnderWildcardReason"] == "udtDefinitionNotAllowlisted"
    )
    if not facts["tagUpdateBareWildcardDoesNotCoverUdt"]:
        raise StageFailure(f"a bare * must not cover a UDT definition: {json.dumps(wildcard_udt)[:600]}")

    # Case 10: a batch whose second item is refused is refused whole, so the
    # first item's target still carries the values the allowlisted call wrote.
    batch = expect_tool_error(client, "tag_update", {
        "items": [
            {"path": paths["target"], "expectedFingerprint": after["fingerprint"],
             "config": {"documentation": "phase4-batch-should-not-apply"}},
            {"path": paths["siblingTarget"], "expectedFingerprint": "tcf1:" + "0" * 64,
             "config": {"documentation": "phase4-batch-should-not-apply"}},
        ],
    })
    raw["preflightRefusal"] = bounded(batch)
    facts["tagUpdatePreflightRefusalCode"] = str(batch.get("code", ""))
    facts["tagUpdatePreflightRefusalItems"] = len((batch.get("details") or {}).get("items") or [])
    batch_after = tag_config(client, paths["target"])
    facts["tagUpdatePreflightExecutedNothing"] = batch_after["fingerprint"] == after["fingerprint"]
    if not facts["tagUpdatePreflightExecutedNothing"]:
        raise StageFailure("a refused Preflight executed part of its batch")
    return {
        "stage": "tag-update",
        "ok": True,
        "identity": identity(config),
        "guard": config.guard,
        "facts": facts,
        "raw": raw,
    }


# --------------------------------------------------------------------------- #
# Stages: ticket #8 (`alarm_shelve` and `alarm_unshelve`)
# --------------------------------------------------------------------------- #

def alarm_paths(config: Config) -> dict[str, str]:
    """The exact Alarm paths the ticket #8 cases use, under the run's Alarm root."""
    return policy_document.alarm_paths(config.root_name, config.alarm_provider)


def shelved_paths(client: mcp_client.McpClient) -> list[str]:
    """The shelved Alarm paths the *read* Tool reports, as an independent cross-check."""
    structured = expect_structured(client, "alarm_shelved_list", {"maxResults": 500})
    return [str(item.get("path", "")) for item in structured.get("items") or []]


def stage_alarm_no_policy(config: Config) -> dict[str, Any]:
    """A Gateway with no Runtime Target Policy must refuse both Alarm Mutations."""
    raw: dict[str, Any] = {}
    facts: dict[str, Any] = {}
    client = mcp_client.McpClient(config.operator_url, config.api_token)
    raw["initialize"] = bounded(client.initialize())
    paths = alarm_paths(config)
    shelve = expect_tool_error(client, "alarm_shelve", {
        "paths": [paths["exact"]], "timeoutSeconds": 60,
    })
    unshelve = expect_tool_error(client, "alarm_unshelve", {"paths": [paths["exact"]]})
    raw["shelveNoPolicy"] = bounded(shelve)
    raw["unshelveNoPolicy"] = bounded(unshelve)
    facts["alarmNoPolicyShelveCode"] = str(shelve.get("code", ""))
    facts["alarmNoPolicyShelveReason"] = str((shelve.get("details") or {}).get("reason", ""))
    facts["alarmNoPolicyUnshelveCode"] = str(unshelve.get("code", ""))
    facts["alarmNoPolicyUnshelveReason"] = str((unshelve.get("details") or {}).get("reason", ""))
    facts["alarmNoPolicyFailsClosed"] = (
        facts["alarmNoPolicyShelveCode"] == "operation_disabled"
        and facts["alarmNoPolicyUnshelveCode"] == "operation_disabled"
    )
    if not facts["alarmNoPolicyFailsClosed"]:
        raise StageFailure(
            "a missing Runtime Target Policy must fail both Alarm Mutations closed: "
            f"{json.dumps({'shelve': shelve, 'unshelve': unshelve})[:600]}"
        )
    return {
        "stage": "alarm-no-policy",
        "ok": True,
        "identity": identity(config),
        "guard": config.guard,
        "facts": facts,
        "raw": raw,
    }


def stage_alarm_shelve(config: Config) -> dict[str, Any]:
    """The ticket #8 live cases: allowlisted shelve/unshelve and their refusals."""
    raw: dict[str, Any] = {}
    facts: dict[str, Any] = {}
    paths = alarm_paths(config)
    # The Alarm fixture the ticket #6 stage built is the target of the shelve, so
    # the pattern this stage uses must be the one the probe measured.
    alarm = load_stage(config, "alarm")
    exact_pattern = str((alarm.get("facts") or {}).get("exactPathSourcePattern", ""))
    facts["alarmShelvePathMatchesAlarmFixture"] = exact_pattern == paths["exact"]
    if not facts["alarmShelvePathMatchesAlarmFixture"]:
        raise StageFailure(
            f"the measured Alarm pattern moved: {exact_pattern!r} != {paths['exact']!r}"
        )

    client = mcp_client.McpClient(config.operator_url, config.api_token)
    raw["initialize"] = bounded(client.initialize())
    inventory = sorted(client.tools_list())
    expected_inventory = sorted(profile_tools("operator"))
    facts["alarmShelveOperatorInventoryMatchesProfile"] = inventory == expected_inventory
    if not facts["alarmShelveOperatorInventoryMatchesProfile"]:
        raise StageFailure(
            f"the deployed operator inventory does not equal contracts/profiles/operator.yaml: "
            f"{inventory} != {expected_inventory}"
        )

    imported = install_alarm_policy(
        config, client, allowlist=policy_document.alarm_allowlist(config.root_name),
    )
    raw["installAlarmPolicy"] = bounded(imported, 20_000)
    facts["alarmShelvePolicyInstalled"] = imported["ok"]
    facts["alarmShelvePolicyServedSha256"] = imported["servedSha256"]
    if not imported["ok"]:
        raise StageFailure(
            "the running provider never served the Alarm policy document: "
            f"{json.dumps(imported['attempts'][-1], sort_keys=True)[:800]}"
        )
    before = shelved_paths(client)

    # Case 1: the allowlisted exact Alarm path, for one hour (the policy cap).
    shelved = expect_structured(client, "alarm_shelve", {
        "paths": [paths["exact"]], "timeoutSeconds": policy_document.ALARM_SHELVE_CAP_SECONDS,
    })
    raw["allowlistedShelve"] = bounded(shelved, 40_000)
    summary = shelved.get("summary") or {}
    observed = shelved.get("observed") or [{}]
    first = observed[0]
    facts["alarmShelveExecuted"] = summary.get("executed")
    facts["alarmShelveOutcomeUnknown"] = summary.get("outcomeUnknown")
    facts["alarmShelveTimeoutSeconds"] = summary.get("timeoutSeconds")
    facts["alarmShelveObservedShelved"] = first.get("shelved") is True
    facts["alarmShelveObservedUserPresent"] = bool(first.get("user")) and first.get("user") != {
        "$ignition": "null"
    }
    facts["alarmShelveObservedExpirationPresent"] = bool(first.get("expiration")) and first.get("expiration") != {
        "$ignition": "null"
    }
    facts["alarmShelveAuditMode"] = str(summary.get("auditMode", ""))
    facts["alarmShelveAuditRecorded"] = summary.get("auditRecorded")
    if not facts["alarmShelveObservedShelved"]:
        raise StageFailure(f"the allowlisted shelve did not take effect: {json.dumps(shelved)[:800]}")
    correlation = str((shelved.get("meta") or {}).get("correlationId", ""))
    audit_status, rows = gateway_rest.audit_rows(
        config.base_url, config.api_token, config.audit_profile, action="ignition-mcp.alarm_shelve",
    )
    matching = [row for row in rows if correlation and correlation in str(row.get("actionValue", ""))]
    raw["auditQuery"] = {"status": audit_status, "rows": bounded(matching, 20_000)}
    facts["alarmShelveAuditRowsForCorrelation"] = len(matching)
    facts["alarmShelveAuditAttemptAndResultRecorded"] = len(matching) >= 2
    facts["alarmShelveAuditActorIsServiceIdentity"] = bool(matching) and all(
        str(row.get("actor", "")) == policy_document.SERVICE_IDENTITY for row in matching
    )
    facts["alarmShelveListShowsExactPath"] = paths["exact"] in shelved_paths(client)
    if not facts["alarmShelveListShowsExactPath"]:
        raise StageFailure("alarm_shelved_list does not report the shelved exact path")

    # Case 2: a duration above the deployment cap is refused before dispatch, and
    # the target stays unshelved.
    cap = expect_tool_error(client, "alarm_shelve", {
        "paths": [paths["nested"]], "timeoutSeconds": policy_document.ALARM_SHELVE_CAP_SECONDS * 2,
    })
    raw["capRefusal"] = bounded(cap)
    cap_details = cap.get("details") or {}
    facts["alarmShelveCapRefusalCode"] = str(cap.get("code", ""))
    facts["alarmShelveCapRefusalReason"] = str(cap_details.get("reason", ""))
    facts["alarmShelveCapRefusalCap"] = cap_details.get("cap")
    if not (facts["alarmShelveCapRefusalCode"] == "invalid_argument"
            and facts["alarmShelveCapRefusalReason"] == "durationOverPolicyCap"):
        raise StageFailure(f"the policy shelve cap was not enforced: {json.dumps(cap)[:600]}")

    # Case 3: a duration above the D12 hard maximum is refused whatever the policy says.
    hard = expect_tool_error(client, "alarm_shelve", {
        "paths": [paths["nested"]], "timeoutSeconds": 86401,
    })
    raw["hardMaxRefusal"] = bounded(hard)
    facts["alarmShelveHardMaxRefusalReason"] = str((hard.get("details") or {}).get("reason", ""))
    after_duration_refusals = shelved_paths(client)
    facts["alarmShelveDurationRefusalsShelvedNothing"] = paths["nested"] not in after_duration_refusals

    # Case 4: a wildcard target is forbidden (D12) and reaches no native call.
    wildcard = expect_tool_error(client, "alarm_shelve", {
        "paths": [paths["wildcard"]], "timeoutSeconds": 60,
    })
    raw["wildcardRefusal"] = bounded(wildcard)
    wildcard_details = (wildcard.get("details") or {}).get("items") or [{}]
    facts["alarmShelveWildcardRefusalCode"] = str(wildcard.get("code", ""))
    facts["alarmShelveWildcardRefusalReason"] = str(wildcard_details[0].get("reason", ""))

    # Case 5: a sibling root that only shares a string prefix is refused.
    sibling = expect_tool_error(client, "alarm_shelve", {
        "paths": [paths["sibling"]], "timeoutSeconds": 60,
    })
    raw["siblingDenial"] = bounded(sibling)
    sibling_details = (sibling.get("details") or {}).get("items") or [{}]
    facts["alarmShelveSiblingDenialCode"] = str(sibling.get("code", ""))
    facts["alarmShelveSiblingDenialReason"] = str(sibling_details[0].get("reason", ""))
    if not (facts["alarmShelveSiblingDenialCode"] == "permission_denied"
            and facts["alarmShelveSiblingDenialReason"] == "targetNotAllowlisted"):
        raise StageFailure(f"the segment-boundary sibling was not refused: {json.dumps(sibling)[:600]}")

    # Case 6: one refused item rejects the whole batch (D30 §3 Preflight).
    whole = expect_tool_error(client, "alarm_shelve", {
        "paths": [paths["nested"], paths["sibling"]], "timeoutSeconds": 60,
    })
    raw["preflightRefusal"] = bounded(whole)
    facts["alarmShelvePreflightRefusalItems"] = len((whole.get("details") or {}).get("items") or [])
    after_refusals = shelved_paths(client)
    facts["alarmShelvePreflightExecutedNothing"] = (
        paths["nested"] not in after_refusals and paths["sibling"] not in after_refusals
    )
    if not facts["alarmShelvePreflightExecutedNothing"]:
        raise StageFailure("a refused Preflight shelved part of its batch")
    raw["shelvedBeforeAllowlistedShelve"] = bounded(before)

    # Case 7: unshelve the allowlisted path and confirm it is gone.
    unshelved = expect_structured(client, "alarm_unshelve", {"paths": [paths["exact"]]})
    raw["allowlistedUnshelve"] = bounded(unshelved, 40_000)
    unshelve_summary = unshelved.get("summary") or {}
    unshelve_observed = (unshelved.get("observed") or [{}])[0]
    facts["alarmUnshelveExecuted"] = unshelve_summary.get("executed")
    facts["alarmUnshelveAuditMode"] = str(unshelve_summary.get("auditMode", ""))
    facts["alarmUnshelveObservedNotShelved"] = unshelve_observed.get("shelved") is False
    after = shelved_paths(client)
    facts["alarmUnshelveExactPathRemoved"] = paths["exact"] not in after
    if not (facts["alarmUnshelveObservedNotShelved"] and facts["alarmUnshelveExactPathRemoved"]):
        raise StageFailure(f"the allowlisted unshelve did not take effect: {json.dumps(unshelved)[:800]}")

    # Case 8: unshelve obeys the same Target allowlist.
    unshelve_sibling = expect_tool_error(client, "alarm_unshelve", {"paths": [paths["sibling"]]})
    raw["unshelveSiblingDenial"] = bounded(unshelve_sibling)
    unshelve_details = (unshelve_sibling.get("details") or {}).get("items") or [{}]
    facts["alarmUnshelveSiblingDenialCode"] = str(unshelve_sibling.get("code", ""))
    facts["alarmUnshelveSiblingDenialReason"] = str(unshelve_details[0].get("reason", ""))

    # Case 9: unshelve forbids wildcards too (D12).
    unshelve_wildcard = expect_tool_error(client, "alarm_unshelve", {"paths": [paths["wildcard"]]})
    raw["unshelveWildcardRefusal"] = bounded(unshelve_wildcard)
    unshelve_wildcard_details = (unshelve_wildcard.get("details") or {}).get("items") or [{}]
    facts["alarmUnshelveWildcardRefusalCode"] = str(unshelve_wildcard.get("code", ""))
    facts["alarmUnshelveWildcardRefusalReason"] = str(unshelve_wildcard_details[0].get("reason", ""))
    return {
        "stage": "alarm-shelve",
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


def load_milestone_stages(config: Config) -> list[dict[str, Any]]:
    """The stage records of the selected milestone, in the order they were recorded.

    An optional record is merged when it exists and skipped when it does not, so a
    milestone's verdict never depends on a stage the workflow chose not to run.
    """
    stages = []
    for name in STAGE_SETS[config.stages]:
        try:
            stages.append(load_stage(config, name))
        except StageFailure:
            if name not in OPTIONAL_STAGES:
                raise
    return stages


def summarize_verdict(config: Config, facts: dict[str, Any]) -> dict[str, Any]:
    """The milestone's own verdict shape over the merged facts."""
    if config.stages == MILESTONE_4B:
        return {
            "runtimeTagConfigMutation": {
                "configuratorInventoryMatchesProfile": facts.get("tagUpdateConfiguratorInventoryMatchesProfile"),
                "operatorProfileExcludesConfigMutation": facts.get(
                    "tagUpdateOperatorInventoryExcludesConfigMutation"
                ),
                "fingerprint": {
                    "form": facts.get("tagUpdateFingerprintForm"),
                    "recomputesFromPublishedConfiguration": facts.get(
                        "tagUpdateFingerprintRecomputesFromPublishedConfiguration"
                    ),
                    "stableAcrossReads": facts.get("tagUpdateFingerprintStableAcrossReads"),
                },
                "update": {
                    "status": facts.get("tagUpdateUpdateStatus"),
                    "nativeOutcome": facts.get("tagUpdateUpdateNativeOutcome"),
                    "observedFingerprintChanged": facts.get("tagUpdateObservedFingerprintChanged"),
                    "independentReadShowsTheChange": facts.get("tagUpdateIndependentReadShowsTheChange"),
                    "independentReadMatchesObserved": facts.get("tagUpdateIndependentReadMatchesObserved"),
                },
                "staleFingerprint": {
                    "code": facts.get("tagUpdateStaleFingerprintCode"),
                    "reason": facts.get("tagUpdateStaleFingerprintReason"),
                    "changedNothing": facts.get("tagUpdateStaleFingerprintChangedNothing"),
                },
                "missingTarget": {
                    "code": facts.get("tagUpdateMissingTargetCode"),
                    "reason": facts.get("tagUpdateMissingTargetReason"),
                    "stillAbsent": facts.get("tagUpdateMissingTargetStillAbsent"),
                },
                "targetAllowlist": {
                    "siblingRefusedAtSegmentBoundary": facts.get("tagUpdateSiblingDenialIsSegmentBoundary"),
                    "siblingValueUnchanged": facts.get("tagUpdateSiblingValueUnchanged"),
                    "preflightRefusalCode": facts.get("tagUpdatePreflightRefusalCode"),
                    "preflightExecutedNothing": facts.get("tagUpdatePreflightExecutedNothing"),
                },
                "udtDefinitions": {
                    "refusedUnderPlainPrefix": facts.get("tagUpdateUdtNeedsExplicitTypesEntry"),
                    "refusedUnderBareWildcard": facts.get("tagUpdateBareWildcardDoesNotCoverUdt"),
                    "explicitTypesEntryHonoured": facts.get("tagUpdateTypesEntryIsHonoured"),
                },
                "reservedProvider": {
                    "refusedUnderExplicitWildcard": facts.get("tagUpdateReservedProviderRefusedUnderWildcard"),
                    "targetValueUnchanged": facts.get("tagUpdateReservedProviderValueUnchanged"),
                    "policyDocumentUnclobbered": facts.get("tagUpdatePolicyDocumentUnclobbered"),
                },
                "audit": {
                    "mode": facts.get("tagUpdateUpdateAuditMode"),
                    "recorded": facts.get("tagUpdateUpdateAuditRecorded"),
                    "rowsForCorrelation": facts.get("tagUpdateAuditRowsForCorrelation"),
                    "actorIsServiceIdentity": facts.get("tagUpdateAuditActorIsServiceIdentity"),
                },
                "noPolicy": {
                    "code": facts.get("tagUpdateNoPolicyErrorCode"),
                    "reason": facts.get("tagUpdateNoPolicyReason"),
                },
            },
        }
    # The exact-path Alarm query bound is the conjunction ticket #6 measured: literal
    # matching only, and a count that does not grow per unacknowledged cycle.
    literal = bool(facts.get("exactPathCountIsOnePerAlarm")) and not bool(facts.get("folderPathExpandsDescendants"))
    stable = bool(facts.get("perPathCountStableAcrossCycles"))
    return {
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
            "runtimeWritePrevention": policy_document.reserved_provider_rule(config.provider),
        },
        "exactPathAlarmQuery": {
            "literalMatchingOnly": literal,
            "noAccumulationWithoutAck": stable,
            "bounded": literal and stable,
            "basis": facts.get("exactPathBoundedBasis"),
        },
        "runtimeTagWrite": {
            "allowlistedBatch": {
                "requested": facts.get("tagWriteBatchRequested"),
                "succeeded": facts.get("tagWriteBatchSucceeded"),
                "failed": facts.get("tagWriteBatchFailed"),
                "outcomeUnknown": facts.get("tagWriteBatchOutcomeUnknown"),
                "nativeOutcomes": facts.get("tagWriteBatchNativeOutcomes"),
                "observedMatchesWritten": facts.get("tagWriteObservedMatchesWritten"),
            },
            "targetAllowlist": {
                "siblingRefusedAtSegmentBoundary": facts.get("tagWriteSiblingDenialIsSegmentBoundary"),
                "preflightExecutedNothing": facts.get("tagWritePreflightExecutedNothing"),
            },
            "reservedProvider": {
                "refusedUnderExplicitWildcard": facts.get("tagWriteReservedProviderRefusedUnderWildcard"),
                "targetValueUnchanged": facts.get("tagWriteReservedProviderValueUnchanged"),
                "policyDocumentUnclobbered": facts.get("tagWritePolicyDocumentUnclobbered"),
            },
            "audit": {
                "mode": facts.get("tagWriteAuditMode"),
                "recorded": facts.get("tagWriteAuditRecorded"),
                "rowsForCorrelation": facts.get("tagWriteAuditRowsForCorrelation"),
                "actorIsServiceIdentity": facts.get("tagWriteAuditActorIsServiceIdentity"),
            },
            "operatorInventoryMatchesProfile": facts.get("tagWriteOperatorInventoryMatchesProfile"),
        },
        "runtimeAlarmMutations": {
            "shelve": {
                "pathMatchesAlarmFixture": facts.get("alarmShelvePathMatchesAlarmFixture"),
                "executed": facts.get("alarmShelveExecuted"),
                "observedShelved": facts.get("alarmShelveObservedShelved"),
                "shelvedListShowsPath": facts.get("alarmShelveListShowsExactPath"),
                "capRefusal": {
                    "code": facts.get("alarmShelveCapRefusalCode"),
                    "reason": facts.get("alarmShelveCapRefusalReason"),
                    "cap": facts.get("alarmShelveCapRefusalCap"),
                },
                "hardMaxRefusalReason": facts.get("alarmShelveHardMaxRefusalReason"),
                "wildcardRefusalReason": facts.get("alarmShelveWildcardRefusalReason"),
                "targetAllowlist": {
                    "siblingRefusedAtSegmentBoundary": (
                        facts.get("alarmShelveSiblingDenialCode") == "permission_denied"
                        and facts.get("alarmShelveSiblingDenialReason") == "targetNotAllowlisted"
                    ),
                    "preflightExecutedNothing": facts.get("alarmShelvePreflightExecutedNothing"),
                },
                "noPolicy": {
                    "code": facts.get("alarmNoPolicyShelveCode"),
                    "reason": facts.get("alarmNoPolicyShelveReason"),
                },
                "audit": {
                    "mode": facts.get("alarmShelveAuditMode"),
                    "recorded": facts.get("alarmShelveAuditRecorded"),
                    "rowsForCorrelation": facts.get("alarmShelveAuditRowsForCorrelation"),
                    "actorIsServiceIdentity": facts.get("alarmShelveAuditActorIsServiceIdentity"),
                },
            },
            "unshelve": {
                "executed": facts.get("alarmUnshelveExecuted"),
                "observedNotShelved": facts.get("alarmUnshelveObservedNotShelved"),
                "pathRemoved": facts.get("alarmUnshelveExactPathRemoved"),
                "siblingRefusalCode": facts.get("alarmUnshelveSiblingDenialCode"),
                "wildcardRefusalReason": facts.get("alarmUnshelveWildcardRefusalReason"),
                "noPolicy": {
                    "code": facts.get("alarmNoPolicyUnshelveCode"),
                    "reason": facts.get("alarmNoPolicyUnshelveReason"),
                },
                "auditMode": facts.get("alarmUnshelveAuditMode"),
            },
            "operatorInventoryMatchesProfile": facts.get("alarmShelveOperatorInventoryMatchesProfile"),
        },
    }


EVIDENCE_TICKETS = {MILESTONE_4A: ["#6", "#7", "#8"], MILESTONE_4B: ["#10"]}
EVIDENCE_TITLES = {
    MILESTONE_4A: (
        "Characterize the Runtime Target Policy and the exact-path alarm query bound, "
        "and verify tag_write, alarm_shelve and alarm_unshelve live"
    ),
    MILESTONE_4B: "Verify the Tag config fingerprint and tag_update (milestone 4b) live",
}


def stage_summarize(config: Config) -> tuple[dict[str, Any], int]:
    stages = load_milestone_stages(config)

    facts: dict[str, Any] = {}
    for stage in stages:
        facts.update(stage.get("facts", {}))

    expectations_path = config.characterization
    if expectations_path is None:
        expectations_path = Path(__file__).resolve().parent / EXPECTATIONS[config.stages]
    expectations = json.loads(expectations_path.read_text(encoding="utf-8"))
    expected = expectations.get(config.gateway_version)
    if expected is None:
        raise StageFailure(f"{expectations_path.name} has no entry for {config.gateway_version}")
    drift = {
        key: {"expected": value, "observed": facts.get(key)}
        for key, value in expected.items() if facts.get(key) != value
    }

    evidence = {
        "schemaVersion": 1,
        "milestone": config.stages,
        "tickets": EVIDENCE_TICKETS[config.stages],
        "title": EVIDENCE_TITLES[config.stages],
        "identity": identity(config),
        "stages": stages,
        "facts": facts,
        "expectations": expected,
        "drift": drift,
        "verdict": summarize_verdict(config, facts),
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
    parser = argparse.ArgumentParser(description="Phase 4 Runtime mutation harness driver")
    parser.add_argument(
        "stage",
        choices=[
            "tag-write-no-policy", "policy-provision", "policy-read", "alarm",
            "tag-write-setup", "tag-write", "alarm-no-policy", "alarm-shelve",
            "tag-update-no-policy", "tag-update-setup", "tag-update", "summarize",
        ],
    )
    parser.add_argument("--base-url", default=os.environ.get("P4_BASE_URL", "http://127.0.0.1:8093"))
    parser.add_argument("--api-token", default=os.environ.get("CI_API_TOKEN", ""))
    parser.add_argument("--mcp-url", default=os.environ.get("P4_MCP_URL", ""))
    parser.add_argument("--operator-mcp-url", default=os.environ.get("P4_OPERATOR_MCP_URL", ""))
    parser.add_argument("--configurator-mcp-url", default=os.environ.get("P4_CONFIGURATOR_MCP_URL", ""))
    parser.add_argument("--marker-label", default=os.environ.get("P4_MARKER_LABEL", "g4a"))
    parser.add_argument("--stages", default=os.environ.get("P4_MILESTONE", MILESTONE_4A), choices=sorted(STAGE_SETS))
    parser.add_argument("--runtime-project", default=os.environ.get("P4_RUNTIME_PROJECT", RUNTIME_PROJECT))
    parser.add_argument("--audit-profile", default=os.environ.get("P4_AUDIT_PROFILE", policy_document.AUDIT_PROFILE_NAME))
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
    parser.add_argument(
        "--characterization",
        default=os.environ.get("P4_CHARACTERIZATION", ""),
        help="expectations file; defaults to the selected milestone's own file",
    )
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
        mcp_url=args.mcp_url or args.base_url.rstrip("/") + PROBE_MCP_PATH,
        operator_mcp_url=args.operator_mcp_url or args.base_url.rstrip("/") + OPERATOR_MCP_PATH,
        configurator_mcp_url=args.configurator_mcp_url or args.base_url.rstrip("/") + CONFIGURATOR_MCP_PATH,
        marker_label=args.marker_label,
        stages=args.stages,
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
        characterization=Path(args.characterization) if args.characterization else None,
        noise_count=args.noise_count,
        cycles=args.cycles,
        repeats=args.repeats,
        ack_username=args.ack_username,
        runtime_project=args.runtime_project,
        audit_profile=args.audit_profile,
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
        elif stage == "tag-write-no-policy":
            record = stage_tag_write_no_policy(config)
            write_stage(config, "tag-write-no-policy", record)
            code = EXIT_OK
        elif stage == "tag-write-setup":
            record = stage_tag_write_setup(config)
            write_stage(config, "tag-write-setup", record)
            code = EXIT_OK
        elif stage == "tag-write":
            record = stage_tag_write(config)
            write_stage(config, "tag-write", record)
            code = EXIT_OK
        elif stage == "alarm-no-policy":
            record = stage_alarm_no_policy(config)
            write_stage(config, "alarm-no-policy", record)
            code = EXIT_OK
        elif stage == "alarm-shelve":
            record = stage_alarm_shelve(config)
            write_stage(config, "alarm-shelve", record)
            code = EXIT_OK
        elif stage == "tag-update-no-policy":
            record = stage_tag_update_no_policy(config)
            write_stage(config, "tag-update-no-policy", record)
            code = EXIT_OK
        elif stage == "tag-update-setup":
            record = stage_tag_update_setup(config)
            write_stage(config, "tag-update-setup", record)
            code = EXIT_OK
        elif stage == "tag-update":
            record = stage_tag_update(config)
            write_stage(config, "tag-update", record)
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
