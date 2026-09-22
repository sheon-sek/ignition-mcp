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
    tag-create         Runtime MCP: verify `tag_create` on the configurator profile:
                       the created node, its collision, its refusals and its ceilings.
    tag-copy           Runtime MCP: verify `tag_copy`: the copied node, its occupied
                       destination, the leaf rule, the source half and both ends of the
                       reserved provider.
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
        "tag-create", "tag-copy", "tag-move", "tag-rename", "tag-delete",
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
    provider_ready_deadline_seconds: float = PROVIDER_READY_DEADLINE_SECONDS
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
    deadline = time.monotonic() + config.provider_ready_deadline_seconds
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


def provider_read_state(config: Config, client: mcp_client.McpClient) -> dict[str, Any]:
    """One handler-scope read of an absent Tag path in the policy provider.

    A provider that finished starting answers `Bad_NotFound` for a path that does
    not exist. A provider that is still starting — or whose Tag actors never
    started because an import was applied while it was loading — answers
    `Error_Configuration` instead. That second case is recorded: in `Phase 4 Live
    Gateway G4a` run 35654626095 (8.3.9) the provision stage imported the policy
    Tag during the provider's initial load, the Gateway logged `Error creating
    actor for tag ... cleanPath is null` (`ImportTagLoaderAdapter.onInitialLoad`),
    and the running provider served nothing for the rest of the job while
    `/resources/find` and `/tags/export` kept answering 200. Readiness therefore
    has to come from a handler read, not from the config plane.

    `client` must already have opened its MCP session; the Module answers
    `tools/call` with HTTP 400 (`Session is required for method: tools/call`)
    otherwise, which live run 35667242361 recorded for a caller that skipped
    `initialize`.
    """

    try:
        report = client.structured("policy_probe", policy_probe_arguments(config))
    except (mcp_client.McpError, StageFailure) as exc:
        return {"ok": False, "missingPathQuality": "", "error": str(exc)[:400]}
    missing = measurement(report, "tag.readBlocking.missing")
    item = first_item(missing)
    return {"ok": bool(missing.get("ok")), "missingPathQuality": str(item.get("quality", ""))}


def provider_is_serving(state: dict[str, Any]) -> bool:
    return str(state.get("missingPathQuality", "")).startswith("Bad_NotFound")


def session_is_required(state: dict[str, Any]) -> bool:
    """Whether a failed probe read was refused for a missing MCP session.

    The Module drops an idle session, and every request after that answers HTTP
    400 until the caller initializes again, so the gate re-opens its session
    instead of reporting a provider that is not serving.
    """

    return "Session is required" in str(state.get("error", ""))


def wait_for_handler_read(
    config: Config, *, client: mcp_client.McpClient | None = None,
) -> dict[str, Any]:
    """Wait until the running provider answers a handler-scope read.

    The REST readiness poll (`wait_for_provider`) cannot see a provider that is
    still loading its Tags: both recorded provider-startup hazards happen after
    `/resources/find` and `/tags/export` already answer 200. This gate is the one
    that keeps the policy import from being applied in that window.
    """

    if client is None:
        client = mcp_client.McpClient(config.mcp_url, config.api_token)
        client.initialize()
    deadline = time.monotonic() + config.provider_ready_deadline_seconds
    started = time.monotonic()
    attempts: list[dict[str, Any]] = []
    while True:
        state = provider_read_state(config, client)
        if session_is_required(state):
            try:
                client.initialize()
            except (mcp_client.McpError, StageFailure) as exc:
                state = {"ok": False, "missingPathQuality": "", "error": str(exc)[:400]}
            else:
                state = provider_read_state(config, client)
        attempts.append(bounded(state, 2000))
        result = {
            "serving": provider_is_serving(state),
            "attempts": len(attempts),
            "waitedMs": int((time.monotonic() - started) * 1000),
            "missingPathQuality": str(state.get("missingPathQuality", "")),
            "lastAttempts": attempts[-3:],
        }
        if result["serving"] or time.monotonic() >= deadline:
            return result
        time.sleep(2.0)


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

    # The REST readiness above is not enough: a provider that has not finished
    # loading its Tags answers config-plane reads while an import applied in that
    # window leaves a Tag whose actor never starts (run 35654626095). Import only
    # once a handler-scope read proves the running provider is serving.
    handler_readiness = wait_for_handler_read(config)
    facts["providerHandlerReadAttempts"] = handler_readiness["attempts"]
    facts["providerHandlerReadWaitedMs"] = handler_readiness["waitedMs"]
    facts["providerHandlerReadQuality"] = handler_readiness["missingPathQuality"]
    facts["providerHandlerReadServing"] = handler_readiness["serving"]
    raw["providerHandlerRead"] = bounded(handler_readiness, 4000)
    if not handler_readiness["serving"]:
        raise StageFailure(
            "the policy Tag provider never served a handler-scope read; an import "
            "applied now would leave a Tag whose actor never starts: "
            + json.dumps(handler_readiness, sort_keys=True)[:800]
        )

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
    # The probe labels the state it reached, but whether the provider actually
    # served the value is the quality of the read it made. A provider that is still
    # loading its Tags, or whose Tag actors never started, answers
    # Error_Configuration here while the gate still reports "served" (live run
    # 35654626095 recorded exactly that), so the verification reads the quality.
    facts["policyGateValueQuality"] = str(gate.get("quality", ""))
    facts["policyGateValueQualityIsGood"] = quality_is_good(facts["policyGateValueQuality"])
    facts["policyGateUnserved"] = (
        str(gate.get("gate", "")) == "served" and not facts["policyGateValueQualityIsGood"]
    )
    facts["policyGatedReadServedAndVerified"] = (
        gate.get("gate") == "served"
        and facts["policyGateValueQualityIsGood"]
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
            "policyGateValueQuality": str(facts.get("policyGateValueQuality", "")),
            "missingPathQuality": str(facts.get("missingPathQuality", "")),
            "handlerWriteQualityCodes": facts.get("handlerWriteQualityCodes", []),
            "error": error,
        }
        # A provider that is not serving its Tags at all cannot be repaired by
        # another config-plane import (run 35654626095 spent the whole deadline
        # proving that), so ask whether the probe's own read of an absent path was
        # answered at all, and only re-import when the provider is serving but the
        # document is not.
        provider_serving: bool | None = None
        if not healthy and not deterministic_refusal:
            attempt["providerReadQuality"] = str(facts.get("missingPathQuality", ""))
            provider_serving = provider_is_serving({"missingPathQuality": facts.get("missingPathQuality", "")})
        # Retry only while the provider is not serving the document yet. A served
        # document whose report carries no gate measurement is a stale recorded
        # payload (the expectation drift check reports it), and a gate that
        # answered "oversize"/"blocked" is a deterministic refusal.
        if healthy or deterministic_refusal or (served_document and not gate_reported) or time.monotonic() >= deadline:
            attempts.append(attempt)
            break
        if provider_serving is False:
            # Wait for the provider to serve again instead of re-importing into it.
            attempts.append(attempt)
            time.sleep(3.0)
            continue
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
        last = attempts[-1] if attempts else {}
        detail = "the policy Tag was never served by the provider: " + json.dumps(last, sort_keys=True)[:800]
        if last.get("providerReadQuality") and not str(last["providerReadQuality"]).startswith("Bad_NotFound"):
            detail = (
                "the running provider is not serving Tags at all (a handler read of an absent path "
                "answered " + str(last["providerReadQuality"]) + "); re-importing the policy cannot "
                "repair that provider state, and the recorded heal is a Gateway restart: "
                + json.dumps(last, sort_keys=True)[:600]
            )
        raise StageFailure(detail)
    if not facts.get("policyGatedReadServedAndVerified") and not (served_document and not gate_reported):
        # The direct read proved the document, but the two-step gate the storage
        # recommendation depends on did not verify it (a gate read that was not Good
        # is the provider-startup state run 35654626095 recorded). Fail closed
        # instead of reporting a green row for a gate that did not run.
        raise StageFailure(
            "the policy gate did not verify the served document: "
            + json.dumps(attempts[-1] if attempts else {}, sort_keys=True)[:800]
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
        "writeProbe": policy_document.WRITE_PROBE_PATH,
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
    and the repository's Python one then have to agree on real Gateway data. The
    published `configuration` is *already* the encoded value, so it is hashed as
    it stands — encoding it again would escape a published null marker or literal
    `$ignition` object a second time and report a false mismatch.
    """
    return lint.tag_config_fingerprint(configuration)


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
    # D18: a refused Precondition is a denied Mutation and is audited too.
    facts["tagUpdateStaleFingerprintAuditRecorded"] = (stale.get("details") or {}).get("auditRecorded")
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
    # D18: the denial is audited, and the response says whether its decision row
    # was written.
    facts["tagUpdateSiblingDenialAuditRecorded"] = (sibling.get("details") or {}).get("auditRecorded")
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

    # Case 7: an explicit _types_ entry lets the target through, and the token it
    # compares is the one the caller's own `tag_get_config` read published for that
    # exact definition — the read has to be allowed for the update to be reachable
    # at all. The definition does not exist on the disposable Gateway, so the flow
    # ends at the existence check; that is what proves the entry is honoured.
    definition_read = tag_config(client, paths["udtTarget"])
    raw["udtDefinitionRead"] = bounded(definition_read, 8_000)
    facts["tagUpdateUdtDefinitionReadIsAllowed"] = definition_read["fingerprint"].startswith("tcf1:")
    facts["tagUpdateUdtDefinitionReadNodes"] = len(definition_read["configuration"])
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
            "expectedFingerprint": definition_read["fingerprint"],
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

    # Case 9b: D10's deployment item limit refuses an over-budget batch before any
    # native call.
    over = expect_tool_error(client, "tag_update", {
        "items": [{
            "path": paths["target"],
            "expectedFingerprint": "tcf1:" + "0" * 64,
            "config": {"documentation": "phase4-should-not-apply"},
        }] * 21,
    })
    raw["overPolicyLimit"] = bounded(over)
    facts["tagUpdateOverPolicyLimitCode"] = str(over.get("code", ""))
    facts["tagUpdateOverPolicyLimitReason"] = str((over.get("details") or {}).get("reason", ""))
    facts["tagUpdateOverPolicyLimitIsRefused"] = (
        facts["tagUpdateOverPolicyLimitCode"] == "limit_exceeded"
        and facts["tagUpdateOverPolicyLimitReason"] == "itemsOverPolicyLimit"
    )
    if not facts["tagUpdateOverPolicyLimitIsRefused"]:
        raise StageFailure(f"an over-budget batch must be refused: {json.dumps(over)[:600]}")

    # Case 10: with the ordinary allowlist back in place, a batch whose second item
    # is outside it is refused whole, so the first item's target keeps its values.
    # (The wildcard policy above covers the sibling, so the allowlist has to come
    # back before this case means what it says.)
    reinstalled = install_tag_update_policy(config, client, allowlist=policy_document.TAG_UPDATE_ALLOWLIST)
    raw["installAllowlistPolicy"] = bounded(reinstalled, 20_000)
    facts["tagUpdateAllowlistPolicyReinstalled"] = reinstalled["ok"]
    if not reinstalled["ok"]:
        raise StageFailure(
            "the running provider never served the plain allowlist policy again: "
            f"{json.dumps(reinstalled['attempts'][-1], sort_keys=True)[:800]}"
        )
    current = tag_config(client, paths["target"])
    batch = expect_tool_error(client, "tag_update", {
        "items": [
            {"path": paths["target"], "expectedFingerprint": current["fingerprint"],
             "config": {"documentation": "phase4-batch-should-not-apply"}},
            {"path": paths["siblingTarget"], "expectedFingerprint": "tcf1:" + "0" * 64,
             "config": {"documentation": "phase4-batch-should-not-apply"}},
        ],
    })
    raw["preflightRefusal"] = bounded(batch)
    facts["tagUpdatePreflightRefusalCode"] = str(batch.get("code", ""))
    facts["tagUpdatePreflightRefusalItems"] = len((batch.get("details") or {}).get("items") or [])
    batch_after = tag_config(client, paths["target"])
    facts["tagUpdatePreflightExecutedNothing"] = batch_after["fingerprint"] == current["fingerprint"]
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
# Stages: ticket #11 (`tag_create` and `tag_copy`)
#
# No `tag-create-setup`/`tag-copy-setup`: the probe project's `tag_fixture_probe`
# Tool — which `tag-update-setup` already runs — seeds the `IgnitionMCP_CI` root and
# its `IgnitionMCP_CI2` sibling, and every ticket #11 target is either a fresh path
# under that root or one of those seeded Tags. The only state these two stages add
# is the Runtime Target Policy document, and each installs its own through the same
# `install_policy` primitive, so a second setup stage would provision nothing.
# --------------------------------------------------------------------------- #

#: The two D10 ceilings a create batch can cross with no native call at all. They
#: belong to the shipped handler, and the stage quotes them back so a change to the
#: documented budget shows up as drift rather than as a case that silently stopped
#: testing anything.
HARD_ITEM_CEILING = 100
PATH_CEILING_BYTES = 2048


def tag_create_paths() -> dict[str, str]:
    """The `tag_create` targets of this run, keyed as the recorded bodies template them."""
    return {
        "createTarget": policy_document.TAG_CREATE_TARGET,
        "batchTarget": policy_document.TAG_CREATE_BATCH_TARGET,
        "existingTarget": policy_document.TAG_CREATE_EXISTING_TARGET,
        "siblingTarget": policy_document.TAG_CREATE_SIBLING_TARGET,
        "writeProbe": policy_document.WRITE_PROBE_PATH,
        "udtTarget": policy_document.TAG_CREATE_UDT_TARGET,
    }


def tag_copy_paths() -> dict[str, str]:
    """The `tag_copy` endpoint pairs of this run, keyed as the recorded bodies do."""
    return {
        "source": policy_document.TAG_COPY_SOURCE,
        "destination": policy_document.TAG_COPY_DESTINATION,
        "siblingSource": policy_document.TAG_COPY_SIBLING_SOURCE,
        "siblingDestination": policy_document.TAG_COPY_SIBLING_DESTINATION,
        "udtDestination": policy_document.TAG_COPY_UDT_DESTINATION,
        "reservedSource": policy_document.TAG_COPY_RESERVED_SOURCE,
        "reservedSourceDestination": policy_document.TAG_COPY_RESERVED_SOURCE_DESTINATION,
        "reservedDestination": policy_document.TAG_COPY_RESERVED_DESTINATION,
        "missingSource": policy_document.TAG_COPY_MISSING_SOURCE,
        "missingSourceDestination": policy_document.TAG_COPY_MISSING_SOURCE_DESTINATION,
    }


def install_tag_create_policy(
    config: Config, client: mcp_client.McpClient, *, allowlist: tuple[str, ...],
    audit_mode: str = "best_effort", max_items: int | None = None, deadline_seconds: float = 150.0,
) -> dict[str, Any]:
    """Install the ticket #11 `tag_create` policy over the same reserved provider."""
    return install_policy(
        config, client,
        document=policy_document.tag_create_tag_document_bytes(
            allowlist=allowlist, audit_mode=audit_mode, max_items=max_items,
        ),
        expected_sha256=policy_document.tag_create_policy_sha256(
            allowlist=allowlist, audit_mode=audit_mode, max_items=max_items,
        ),
        deadline_seconds=deadline_seconds,
    )


def install_tag_copy_policy(
    config: Config, client: mcp_client.McpClient, *, allowlist: tuple[str, ...],
    audit_mode: str = "best_effort", max_items: int | None = None, deadline_seconds: float = 150.0,
) -> dict[str, Any]:
    """Install the ticket #11 `tag_copy` policy over the same reserved provider."""
    return install_policy(
        config, client,
        document=policy_document.tag_copy_tag_document_bytes(
            allowlist=allowlist, audit_mode=audit_mode, max_items=max_items,
        ),
        expected_sha256=policy_document.tag_copy_policy_sha256(
            allowlist=allowlist, audit_mode=audit_mode, max_items=max_items,
        ),
        deadline_seconds=deadline_seconds,
    )


def install_and_require(
    config: Config, client: mcp_client.McpClient, facts: dict[str, Any], tool: str,
    key: str, *, allowlist: tuple[str, ...], raw: dict[str, Any],
    max_items: int | None = None,
) -> None:
    """Install one policy state or fail closed: a refusal measured against a Policy
    that never became served proves nothing about the rule it refuses for.
    """
    installers = {
        "create": install_tag_create_policy, "copy": install_tag_copy_policy,
        "delete": lambda config, client, **kw: install_ticket12_policy(config, client, "tag_delete", **kw),
        "move": lambda config, client, **kw: install_ticket12_policy(config, client, "tag_move", **kw),
        "rename": lambda config, client, **kw: install_ticket12_policy(config, client, "tag_rename", **kw),
    }
    installed = installers[tool](config, client, allowlist=allowlist, max_items=max_items)
    raw[f"install{key}Policy"] = bounded(installed, 20_000)
    facts[f"tag{key}PolicyInstalled"] = installed["ok"]
    if not installed["ok"]:
        raise StageFailure(
            f"the running provider never served the tag_{tool} policy ({key}): "
            f"{json.dumps(installed['attempts'][-1], sort_keys=True)[:800]}"
        )


def provider_export(config: Config) -> Any:
    """The `default` provider's own export document."""
    status, payload = gateway_rest.export_tags(config.base_url, config.api_token, "default")
    return gateway_rest.decode(payload) if status == 200 else None


def exported_node(config: Config, name: str) -> dict[str, Any] | None:
    """One node of that export: the independent presence check.

    A configuration read cannot answer "is this path there" — a Gateway answers a path
    that is not there with a synthesized node (ticket #10 evidence) — so a create's
    landing, and a refused Preflight's not landing, are both measured on the export.
    """
    return policy_document.find_tag(provider_export(config), name)


def audited_rows(
    config: Config, correlation: str, action: str,
) -> tuple[int, list[dict[str, Any]]]:
    """The Runtime audit rows one dispatched Mutation wrote, read back through REST."""
    status, rows = gateway_rest.audit_rows(
        config.base_url, config.api_token, config.audit_profile, action=action,
    )
    return status, [row for row in rows if correlation and correlation in str(row.get("actionValue", ""))]


def stage_tag_create(config: Config) -> dict[str, Any]:
    """The ticket #11 `tag_create` live cases: the create, the collision, the refusals."""
    raw: dict[str, Any] = {}
    facts: dict[str, Any] = {}
    paths = tag_create_paths()
    client = mcp_client.McpClient(config.configurator_url, config.api_token)
    raw["initialize"] = bounded(client.initialize())

    # Case 0: the CONFIG Mutations of ticket #11 belong to the `configurator` profile,
    # whose deployed inventory equals its contract exactly, and to no other profile.
    inventory = sorted(client.tools_list())
    expected_inventory = sorted(profile_tools("configurator"))
    facts["tagCreateConfiguratorInventory"] = inventory
    facts["tagCreateConfiguratorProfile"] = expected_inventory
    facts["tagCreateConfiguratorInventoryMatchesProfile"] = inventory == expected_inventory
    facts["tagCreateConfiguratorCarriesBothTools"] = (
        "tag_create" in inventory and "tag_copy" in inventory
    )
    if not facts["tagCreateConfiguratorInventoryMatchesProfile"]:
        raise StageFailure(
            "the deployed configurator inventory does not equal contracts/profiles/configurator.yaml: "
            f"{inventory} != {expected_inventory}"
        )
    operator = mcp_client.McpClient(config.operator_url, config.api_token)
    raw["operatorInitialize"] = bounded(operator.initialize())
    operator_inventory = sorted(operator.tools_list())
    facts["tagCreateOperatorInventory"] = operator_inventory
    facts["tagCreateOperatorInventoryExcludesBothTools"] = (
        "tag_create" not in operator_inventory and "tag_copy" not in operator_inventory
        and operator_inventory == sorted(profile_tools("operator"))
    )
    if not facts["tagCreateOperatorInventoryExcludesBothTools"]:
        raise StageFailure(
            f"the CONTROL profile must not serve a CONFIG Mutation: {operator_inventory}"
        )

    # Case 0b: the D10 input ceilings are measured over the request, so an over-budget
    # batch is refused with its own reason even while a policy that would allow it (or
    # refuse it) is the one being served.
    overlong = expect_tool_error(client, "tag_create", {
        "items": [{
            "path": (
                f"[{policy_document.TAG_FIXTURE_PROVIDER}]{policy_document.TAG_FIXTURE_ROOT}/"
                + policy_document.OVERLONG_PATH_LEAF
            ),
            "config": dict(policy_document.TAG_CREATE_CONFIG),
        }],
    })
    raw["pathOverCeiling"] = bounded(overlong, 4_000)
    overlong_details = overlong.get("details") or {}
    facts["tagCreatePathOverCeilingCode"] = str(overlong.get("code", ""))
    facts["tagCreatePathOverCeilingReason"] = str(overlong_details.get("reason", ""))
    facts["tagCreatePathOverCeilingNamesTheCeiling"] = (
        overlong_details.get("limit") == PATH_CEILING_BYTES
        and int(overlong_details.get("requested") or 0) > PATH_CEILING_BYTES
    )
    if not facts["tagCreatePathOverCeilingNamesTheCeiling"]:
        raise StageFailure(f"an over-budget path must name its ceiling: {json.dumps(overlong)[:600]}")
    hard_batch = expect_tool_error(client, "tag_create", {
        "items": [
            {"path": paths["createTarget"], "config": dict(policy_document.TAG_CREATE_CONFIG)}
        ] * (HARD_ITEM_CEILING + 1),
    })
    raw["itemsOverHardCeiling"] = bounded(hard_batch, 4_000)
    hard_details = hard_batch.get("details") or {}
    facts["tagCreateHardItemCeilingCode"] = str(hard_batch.get("code", ""))
    facts["tagCreateHardItemCeilingReason"] = str(hard_details.get("reason", ""))
    facts["tagCreateHardItemCeilingIsRefused"] = (
        facts["tagCreateHardItemCeilingCode"] == "limit_exceeded"
        and facts["tagCreateHardItemCeilingReason"] == "itemsOverHardLimit"
        and hard_details.get("limit") == HARD_ITEM_CEILING
    )
    if not facts["tagCreateHardItemCeilingIsRefused"]:
        raise StageFailure(f"the item hard ceiling must be refused: {json.dumps(hard_batch)[:600]}")

    install_and_require(
        config, client, facts, "create", "Create",
        allowlist=policy_document.TAG_CREATE_ALLOWLIST, raw=raw,
    )

    # Case 1: an allowlisted create. The node the Tool promises is the node an
    # independent `tag_get_config` read and the provider's own export both show, and
    # the Observed fingerprint is exactly that read's — the token the caller's next
    # tag_update or tag_delete is meant to carry.
    structured = expect_structured(client, "tag_create", {
        "items": [{"path": paths["createTarget"], "config": dict(policy_document.TAG_CREATE_CONFIG)}],
    })
    raw["allowlistedCreate"] = bounded(structured, 40_000)
    items = structured.get("items") or [{}]
    observed = structured.get("observed") or [{}]
    summary = structured.get("summary") or {}
    facts["tagCreateStatus"] = str(items[0].get("status", ""))
    facts["tagCreateNativeOutcome"] = str((items[0].get("nativeOutcome") or {}).get("name", ""))
    facts["tagCreateSucceeded"] = summary.get("succeeded")
    facts["tagCreateAuditMode"] = str(summary.get("auditMode", ""))
    facts["tagCreateAuditRecorded"] = summary.get("auditRecorded")
    after = tag_config(client, paths["createTarget"])
    raw["tagGetConfigCreated"] = bounded(after, 20_000)
    node = after["configuration"][0] if after["configuration"] else {}
    facts["tagCreateIndependentReadShowsTheNode"] = (
        node.get("documentation") == policy_document.TAG_CREATE_CONFIG["documentation"]
        and node.get("dataType") == policy_document.TAG_CREATE_CONFIG["dataType"]
        and node.get("name") == paths["createTarget"].rsplit("/", 1)[-1]
    )
    facts["tagCreateObservedFingerprintIsIndependentRead"] = bool(observed) and (
        observed[0].get("fingerprint") == after["fingerprint"]
    )
    facts["tagCreateObservedFingerprintIsDerivable"] = bool(observed) and derived_fingerprint(
        observed[0].get("configuration")
    ) == observed[0].get("fingerprint")
    facts["tagCreateIndependentReadIsDerivable"] = (
        derived_fingerprint(after["configuration"]) == after["fingerprint"]
    )
    created = exported_node(config, "CreateTarget")
    raw["createdExport"] = bounded(created, 2_000)
    facts["tagCreateNodeVisibleInExport"] = created is not None
    if not (
        facts["tagCreateStatus"] == "executed"
        and facts["tagCreateNativeOutcome"].startswith("Good")
        and facts["tagCreateIndependentReadShowsTheNode"]
        and facts["tagCreateNodeVisibleInExport"]
    ):
        raise StageFailure(f"the allowlisted create did not land: {json.dumps(structured)[:800]}")

    # D18: the dispatched Mutation writes an attempt row and a result row, under the
    # Service identity the Policy names — not the connection's user.
    correlation = str((structured.get("meta") or {}).get("correlationId", ""))
    audit_status, rows = audited_rows(config, correlation, "ignition-mcp.tag_create")
    raw["auditQuery"] = {"status": audit_status, "rows": bounded(rows, 20_000)}
    facts["tagCreateAuditCorrelationIdPresent"] = bool(correlation)
    facts["tagCreateAuditRowsForCorrelation"] = len(rows)
    facts["tagCreateAuditAttemptAndResultRecorded"] = len(rows) >= 2
    facts["tagCreateAuditActorIsServiceIdentity"] = bool(rows) and all(
        str(row.get("actor", "")) == policy_document.SERVICE_IDENTITY for row in rows
    )
    if not facts["tagCreateAuditAttemptAndResultRecorded"]:
        raise StageFailure(f"the create wrote no audit pair for {correlation}: {audit_status}")

    # Case 2: an existing target is conflict, and because every item is checked before
    # any item executes, a batch that pairs it with a target that would have succeeded
    # creates neither.
    existing_before = tag_config(client, paths["existingTarget"])
    collision = expect_tool_error(client, "tag_create", {
        "items": [{"path": paths["existingTarget"], "config": dict(policy_document.TAG_CREATE_CONFIG)}],
    })
    raw["collision"] = bounded(collision)
    collision_items = (collision.get("details") or {}).get("items") or [{}]
    facts["tagCreateCollisionCode"] = str(collision.get("code", ""))
    facts["tagCreateCollisionReason"] = str(collision_items[0].get("reason", ""))
    facts["tagCreateCollisionIsConflict"] = (
        facts["tagCreateCollisionCode"] == "conflict"
        and facts["tagCreateCollisionReason"] == "targetExists"
    )
    facts["tagCreateCollisionAuditRecorded"] = (collision.get("details") or {}).get("auditRecorded")
    unchanged = tag_config(client, paths["existingTarget"])
    facts["tagCreateCollisionChangedNothing"] = (
        unchanged["fingerprint"] == existing_before["fingerprint"]
        and unchanged["configuration"] == existing_before["configuration"]
    )
    if not facts["tagCreateCollisionIsConflict"]:
        raise StageFailure(f"an existing create target must be conflict: {json.dumps(collision)[:600]}")
    if not facts["tagCreateCollisionChangedNothing"]:
        raise StageFailure("a refused create collision changed the Tag that was already there")
    collision_batch = expect_tool_error(client, "tag_create", {
        "items": [
            {"path": paths["batchTarget"], "config": dict(policy_document.TAG_CREATE_CONFIG)},
            {"path": paths["existingTarget"], "config": dict(policy_document.TAG_CREATE_CONFIG)},
        ],
    })
    raw["collisionBatch"] = bounded(collision_batch)
    facts["tagCreateCollisionBatchListsOnlyTheCollision"] = (
        (collision_batch.get("details") or {}).get("reason") == "preflightPreconditionFailed"
        and len((collision_batch.get("details") or {}).get("items") or []) == 1
    )
    facts["tagCreateBatchTargetAbsentFromExport"] = exported_node(config, "CreateBatchTarget") is None
    if not facts["tagCreateCollisionBatchListsOnlyTheCollision"]:
        raise StageFailure(
            f"a refused create batch is not a partial batch: {json.dumps(collision_batch)[:600]}"
        )

    # Case 3: the segment-boundary sibling is refused, and the refusal is audited as a
    # denied Mutation before anything dispatches.
    sibling = expect_tool_error(client, "tag_create", {
        "items": [{"path": paths["siblingTarget"], "config": dict(policy_document.TAG_CREATE_CONFIG)}],
    })
    raw["siblingDenial"] = bounded(sibling)
    sibling_items = (sibling.get("details") or {}).get("items") or [{}]
    facts["tagCreateSiblingDenialCode"] = str(sibling.get("code", ""))
    facts["tagCreateSiblingDenialReason"] = str(sibling_items[0].get("reason", ""))
    facts["tagCreateSiblingDenialIsSegmentBoundary"] = (
        facts["tagCreateSiblingDenialCode"] == "permission_denied"
        and facts["tagCreateSiblingDenialReason"] == "targetNotAllowlisted"
    )
    facts["tagCreateSiblingDenialAuditRecorded"] = (sibling.get("details") or {}).get("auditRecorded")
    if not facts["tagCreateSiblingDenialIsSegmentBoundary"]:
        raise StageFailure(f"the segment-boundary sibling was not refused: {json.dumps(sibling)[:600]}")

    # Case 3b: one refused item refuses the whole batch, so the allowed item keeps its
    # absence — the no-rollback half of D30 3, measured on a create.
    batch = expect_tool_error(client, "tag_create", {
        "items": [
            {"path": paths["batchTarget"], "config": dict(policy_document.TAG_CREATE_CONFIG)},
            {"path": paths["siblingTarget"], "config": dict(policy_document.TAG_CREATE_CONFIG)},
        ],
    })
    raw["preflightRefusal"] = bounded(batch)
    batch_details = batch.get("details") or {}
    batch_items = batch_details.get("items") or []
    facts["tagCreatePreflightRefusalCode"] = str(batch.get("code", ""))
    facts["tagCreatePreflightRefusalReason"] = str(batch_details.get("reason", ""))
    facts["tagCreatePreflightRefusalItems"] = len(batch_items)
    facts["tagCreatePreflightRefusalNamesOnlyTheRefusedEnd"] = bool(batch_items) and (
        str(batch_items[0].get("path", "")) == paths["siblingTarget"]
    )
    # The batch's allowed item is the proof: a Preflight that executed part of the
    # batch would have created it.
    facts["tagCreatePreflightExecutedNothing"] = exported_node(config, "CreateBatchTarget") is None
    if not facts["tagCreatePreflightExecutedNothing"]:
        raise StageFailure("a refused create Preflight executed part of its batch")

    # Case 4: D30 6. A definition target needs an entry that itself names `_types_`,
    # so a plain Tag prefix does not reach it even though the path shares the prefix's
    # provider, and a bare `*` does not reach it either.
    udt = expect_tool_error(client, "tag_create", {
        "items": [{"path": paths["udtTarget"], "config": dict(policy_document.TAG_CREATE_CONFIG)}],
    })
    raw["udtUnderPlainPrefix"] = bounded(udt)
    udt_items = (udt.get("details") or {}).get("items") or [{}]
    facts["tagCreateUdtDenialCode"] = str(udt.get("code", ""))
    facts["tagCreateUdtDenialReason"] = str(udt_items[0].get("reason", ""))
    facts["tagCreateUdtNeedsExplicitTypesEntry"] = (
        facts["tagCreateUdtDenialCode"] == "permission_denied"
        and facts["tagCreateUdtDenialReason"] == "udtDefinitionNotAllowlisted"
    )
    if not facts["tagCreateUdtNeedsExplicitTypesEntry"]:
        raise StageFailure(f"a UDT definition needs an explicit _types_ entry: {json.dumps(udt)[:600]}")

    # Case 5: the D10 ceiling a deployment owns. Two items are inside the 20-target
    # project default, so a batch of two is refused only once the document says one.
    over = expect_tool_error(client, "tag_create", {
        "items": [
            {"path": paths["createTarget"], "config": dict(policy_document.TAG_CREATE_CONFIG)}
        ] * 21,
    })
    raw["overPolicyLimit"] = bounded(over)
    over_details = over.get("details") or {}
    facts["tagCreateOverPolicyLimitCode"] = str(over.get("code", ""))
    facts["tagCreateOverPolicyLimitReason"] = str(over_details.get("reason", ""))
    facts["tagCreateOverPolicyLimitIsRefused"] = (
        facts["tagCreateOverPolicyLimitCode"] == "limit_exceeded"
        and facts["tagCreateOverPolicyLimitReason"] == "itemsOverPolicyLimit"
    )
    if not facts["tagCreateOverPolicyLimitIsRefused"]:
        raise StageFailure(f"an over-budget batch must be refused: {json.dumps(over)[:600]}")
    install_and_require(
        config, client, facts, "create", "CreateCeiling",
        allowlist=policy_document.TAG_CREATE_ALLOWLIST, raw=raw, max_items=1,
    )
    lowered = expect_tool_error(client, "tag_create", {
        "items": [
            {"path": paths["createTarget"], "config": dict(policy_document.TAG_CREATE_CONFIG)},
            {"path": paths["batchTarget"], "config": dict(policy_document.TAG_CREATE_CONFIG)},
        ],
    })
    raw["overPolicyCeiling"] = bounded(lowered)
    lowered_details = lowered.get("details") or {}
    facts["tagCreatePolicyCeilingIsHonoured"] = (
        str(lowered.get("code", "")) == "limit_exceeded"
        and str(lowered_details.get("reason", "")) == "itemsOverPolicyLimit"
        and lowered_details.get("limit") == 1
        and lowered_details.get("requested") == 2
    )
    if not facts["tagCreatePolicyCeilingIsHonoured"]:
        raise StageFailure(
            f"the deployment's item ceiling never reached the refusal: {json.dumps(lowered)[:600]}"
        )

    # Case 6: with an explicit `*` the reserved provider is still refused, and neither
    # the probe Tag nor the policy document moves: the refusal is by provider, before
    # the allowlist is consulted at all.
    install_and_require(
        config, client, facts, "create", "CreateWildcard",
        allowlist=policy_document.WILDCARD_ALLOWLIST, raw=raw,
    )
    probe_before = read_tag_value(client, config.write_probe_path).get("value")
    policy_before = read_tag_value(client, config.policy_path).get("value")
    reserved = expect_tool_error(client, "tag_create", {
        "items": [{"path": paths["writeProbe"], "config": dict(policy_document.TAG_CREATE_CONFIG)}],
    })
    raw["reservedProviderRefusal"] = bounded(reserved)
    reserved_items = (reserved.get("details") or {}).get("items") or [{}]
    facts["tagCreateReservedProviderCode"] = str(reserved.get("code", ""))
    facts["tagCreateReservedProviderReason"] = str(reserved_items[0].get("reason", ""))
    facts["tagCreateReservedProviderRefusedUnderWildcard"] = (
        facts["tagCreateReservedProviderCode"] == "permission_denied"
        and facts["tagCreateReservedProviderReason"] == "reservedProvider"
    )
    probe_after = read_tag_value(client, config.write_probe_path).get("value")
    policy_after = read_tag_value(client, config.policy_path).get("value")
    facts["tagCreateReservedProviderValueUnchanged"] = probe_before == probe_after
    facts["tagCreatePolicyDocumentUnclobbered"] = policy_before == policy_after
    if not facts["tagCreateReservedProviderRefusedUnderWildcard"]:
        raise StageFailure(f"the reserved provider was not refused: {json.dumps(reserved)[:600]}")
    wildcard_udt = expect_tool_error(client, "tag_create", {
        "items": [{"path": paths["udtTarget"], "config": dict(policy_document.TAG_CREATE_CONFIG)}],
    })
    raw["udtUnderWildcard"] = bounded(wildcard_udt)
    wildcard_items = (wildcard_udt.get("details") or {}).get("items") or [{}]
    facts["tagCreateBareWildcardDoesNotCoverUdt"] = (
        str(wildcard_udt.get("code", "")) == "permission_denied"
        and str(wildcard_items[0].get("reason", "")) == "udtDefinitionNotAllowlisted"
    )
    if not facts["tagCreateBareWildcardDoesNotCoverUdt"]:
        raise StageFailure(f"a bare * must not cover a UDT definition: {json.dumps(wildcard_udt)[:600]}")

    # Case 7: the explicit `_types_` entry is honoured. A definition target that passes
    # the allowlist stage would then have to be written for the entry to mean anything,
    # so the case measures the allowlist decision alone and never creates one: a batch
    # whose first item is that target and whose second is the segment-boundary sibling
    # refuses wholly and lists only the sibling, where the same target alone was refused
    # as a definition under a plain prefix (Case 4). What changed is the entry.
    install_and_require(
        config, client, facts, "create", "CreateTypes",
        allowlist=policy_document.TAG_CREATE_TYPES_ALLOWLIST, raw=raw,
    )
    honoured = expect_tool_error(client, "tag_create", {
        "items": [
            {"path": paths["udtTarget"], "config": dict(policy_document.TAG_CREATE_CONFIG)},
            {"path": paths["siblingTarget"], "config": dict(policy_document.TAG_CREATE_CONFIG)},
        ],
    })
    raw["udtUnderTypesEntry"] = bounded(honoured)
    honoured_items = (honoured.get("details") or {}).get("items") or []
    facts["tagCreateTypesEntryIsHonoured"] = (
        str((honoured.get("details") or {}).get("reason", "")) == "preflightTargetRefused"
        and len(honoured_items) == 1
        and honoured_items[0].get("index") == 1
        and str(honoured_items[0].get("reason", "")) == "targetNotAllowlisted"
    )
    if not facts["tagCreateTypesEntryIsHonoured"]:
        raise StageFailure(
            f"the explicit _types_ entry was not honoured: {json.dumps(honoured)[:600]}"
        )
    facts["tagCreateTypesPreflightExecutedNothing"] = (
        exported_node(config, "CreateProbe") is None
        and exported_node(config, "CreateBatchTarget") is None
    )
    if not facts["tagCreateTypesPreflightExecutedNothing"]:
        raise StageFailure("a refused create Preflight executed part of its batch")
    return {
        "stage": "tag-create",
        "ok": True,
        "identity": identity(config),
        "guard": config.guard,
        "facts": facts,
        "raw": raw,
    }


def stage_tag_copy(config: Config) -> dict[str, Any]:
    """The ticket #11 `tag_copy` live cases: the copy, the refusals, both ends."""
    raw: dict[str, Any] = {}
    facts: dict[str, Any] = {}
    paths = tag_copy_paths()
    client = mcp_client.McpClient(config.configurator_url, config.api_token)
    raw["initialize"] = bounded(client.initialize())

    def item(source: str = "", destination: str = "") -> dict[str, str]:
        return {
            "sourcePath": source or paths["source"],
            "destinationPath": destination or paths["destination"],
        }

    # Case 0: one `system.tag.copy` call lands each source under its own name, so a
    # destination whose leaf differs would not be where the caller named it. That makes
    # the leaf rule an input rule: it is refused with `preflightInputFailed` before the
    # Policy is read, and a copy that renames is tag_rename's.
    leaf = expect_tool_error(client, "tag_copy", {"items": [item(destination=f"{paths['destination']}Renamed")]})
    raw["leafMismatch"] = bounded(leaf)
    leaf_details = leaf.get("details") or {}
    leaf_items = leaf_details.get("items") or [{}]
    facts["tagCopyLeafMismatchCode"] = str(leaf.get("code", ""))
    facts["tagCopyLeafMismatchDetailsReason"] = str(leaf_details.get("reason", ""))
    facts["tagCopyLeafMismatchReason"] = str(leaf_items[0].get("reason", ""))
    facts["tagCopyLeafMismatchNamesTheDestination"] = str(leaf_items[0].get("path", "")) == (
        f"{paths['destination']}Renamed"
    )
    facts["tagCopyLeafRuleIsRefusedBeforeAnyRead"] = (
        facts["tagCopyLeafMismatchCode"] == "invalid_argument"
        and facts["tagCopyLeafMismatchDetailsReason"] == "preflightInputFailed"
        and facts["tagCopyLeafMismatchReason"] == "destinationLeafDiffersFromSource"
    )
    if not facts["tagCopyLeafRuleIsRefusedBeforeAnyRead"]:
        raise StageFailure(f"the destination leaf rule must be an input refusal: {json.dumps(leaf)[:600]}")
    hard_batch = expect_tool_error(client, "tag_copy", {"items": [item()] * (HARD_ITEM_CEILING + 1)})
    raw["itemsOverHardCeiling"] = bounded(hard_batch, 4_000)
    hard_details = hard_batch.get("details") or {}
    facts["tagCopyHardItemCeilingCode"] = str(hard_batch.get("code", ""))
    facts["tagCopyHardItemCeilingReason"] = str(hard_details.get("reason", ""))
    facts["tagCopyHardItemCeilingIsRefused"] = (
        facts["tagCopyHardItemCeilingCode"] == "limit_exceeded"
        and facts["tagCopyHardItemCeilingReason"] == "itemsOverHardLimit"
        and hard_details.get("limit") == HARD_ITEM_CEILING
        and hard_details.get("requested") == HARD_ITEM_CEILING + 1
    )
    if not facts["tagCopyHardItemCeilingIsRefused"]:
        raise StageFailure(f"the copy item hard ceiling must be refused: {json.dumps(hard_batch)[:600]}")
    # ...and the same ceiling on the other end: a copy bounds both its source path and
    # its destination path, and the refusal quotes the end that crossed it. The
    # destination keeps the source's leaf, because a destination with another leaf is
    # refused as invalid_argument before the ceiling is ever measured.
    overlong_source = paths["source"]
    overlong = expect_tool_error(client, "tag_copy", {
        "items": [{
            "sourcePath": overlong_source,
            "destinationPath": (
                f"[{policy_document.TAG_FIXTURE_PROVIDER}]{policy_document.TAG_FIXTURE_ROOT}/"
                + policy_document.OVERLONG_PATH_LEAF + "/" + overlong_source.rsplit("/", 1)[-1]
            ),
        }],
    })
    raw["pathOverCeiling"] = bounded(overlong, 4_000)
    overlong_details = overlong.get("details") or {}
    facts["tagCopyPathOverCeilingCode"] = str(overlong.get("code", ""))
    facts["tagCopyPathOverCeilingReason"] = str(overlong_details.get("reason", ""))
    facts["tagCopyPathOverCeilingNamesTheCeiling"] = (
        overlong_details.get("limit") == PATH_CEILING_BYTES
        and int(overlong_details.get("requested") or 0) > PATH_CEILING_BYTES
    )
    if not facts["tagCopyPathOverCeilingNamesTheCeiling"]:
        raise StageFailure(f"an over-budget copy path must name its ceiling: {json.dumps(overlong)[:600]}")

    install_and_require(
        config, client, facts, "copy", "Copy", allowlist=policy_document.TAG_COPY_ALLOWLIST, raw=raw,
    )

    # Case 1: the positive copy. Both ends are read independently afterwards: the
    # destination because it is the node this call created and the Observed state
    # reports, and the source because a copy is not a move.

    source_before = tag_config(client, paths["source"])
    structured = expect_structured(client, "tag_copy", {"items": [item()]})
    raw["allowlistedCopy"] = bounded(structured, 40_000)
    items = structured.get("items") or [{}]
    observed = structured.get("observed") or [{}]
    summary = structured.get("summary") or {}
    facts["tagCopyStatus"] = str(items[0].get("status", ""))
    facts["tagCopyNativeOutcome"] = str((items[0].get("nativeOutcome") or {}).get("name", ""))
    facts["tagCopySucceeded"] = summary.get("succeeded")
    facts["tagCopyAuditMode"] = str(summary.get("auditMode", ""))
    facts["tagCopyAuditRecorded"] = summary.get("auditRecorded")
    facts["tagCopyItemCarriesBothEnds"] = (
        items[0].get("sourcePath") == paths["source"]
        and items[0].get("destinationPath") == paths["destination"]
    )
    after = tag_config(client, paths["destination"])
    raw["tagGetConfigDestination"] = bounded(after, 20_000)
    copied = after["configuration"][0] if after["configuration"] else {}
    original = source_before["configuration"][0] if source_before["configuration"] else {}
    facts["tagCopyIndependentReadShowsTheCopy"] = (
        copied.get("name") == paths["destination"].rsplit("/", 1)[-1]
        and copied.get("dataType") == original.get("dataType")
        and copied.get("documentation") == original.get("documentation")
    )
    facts["tagCopyObservedFingerprintIsIndependentRead"] = bool(observed) and (
        observed[0].get("fingerprint") == after["fingerprint"]
    )
    facts["tagCopyObservedFingerprintIsDerivable"] = bool(observed) and derived_fingerprint(
        observed[0].get("configuration")
    ) == observed[0].get("fingerprint")
    source_after = tag_config(client, paths["source"])
    facts["tagCopySourceUnchanged"] = (
        source_after["fingerprint"] == source_before["fingerprint"]
        and source_after["configuration"] == source_before["configuration"]
    )
    if not (
        facts["tagCopyStatus"] == "executed"
        and facts["tagCopyNativeOutcome"].startswith("Good")
        and facts["tagCopyIndependentReadShowsTheCopy"]
        and facts["tagCopySourceUnchanged"]
    ):
        raise StageFailure(f"the allowlisted copy did not land: {json.dumps(structured)[:800]}")

    # D18: the dispatched copy writes its audit pair under its own action name.
    correlation = str((structured.get("meta") or {}).get("correlationId", ""))
    audit_status, rows = audited_rows(config, correlation, "ignition-mcp.tag_copy")
    raw["auditQuery"] = {"status": audit_status, "rows": bounded(rows, 20_000)}
    facts["tagCopyAuditCorrelationIdPresent"] = bool(correlation)
    facts["tagCopyAuditRowsForCorrelation"] = len(rows)
    facts["tagCopyAuditAttemptAndResultRecorded"] = len(rows) >= 2
    facts["tagCopyAuditActorIsServiceIdentity"] = bool(rows) and all(
        str(row.get("actor", "")) == policy_document.SERVICE_IDENTITY for row in rows
    )
    if not facts["tagCopyAuditAttemptAndResultRecorded"]:
        raise StageFailure(f"the copy wrote no audit pair for {correlation}: {audit_status}")

    # Case 2: the destination the first copy built is now occupied, so the same call is
    # conflict and leaves it exactly as it was. D30 2 gives a copy no Precondition
    # token, so this occupancy pair is its concurrency rule.
    occupied = expect_tool_error(client, "tag_copy", {"items": [item()]})
    raw["occupiedDestination"] = bounded(occupied)
    occupied_items = (occupied.get("details") or {}).get("items") or [{}]
    facts["tagCopyOccupiedDestinationCode"] = str(occupied.get("code", ""))
    facts["tagCopyOccupiedDestinationReason"] = str(occupied_items[0].get("reason", ""))
    facts["tagCopyOccupiedDestinationNamesTheDestination"] = (
        str(occupied_items[0].get("path", "")) == paths["destination"]
    )
    facts["tagCopyOccupiedDestinationIsConflict"] = (
        facts["tagCopyOccupiedDestinationCode"] == "conflict"
        and facts["tagCopyOccupiedDestinationReason"] == "destinationExists"
    )
    facts["tagCopyOccupiedDestinationAuditRecorded"] = (occupied.get("details") or {}).get("auditRecorded")
    if not facts["tagCopyOccupiedDestinationIsConflict"]:
        raise StageFailure(f"an occupied destination must be conflict: {json.dumps(occupied)[:600]}")
    untouched = tag_config(client, paths["destination"])
    facts["tagCopyOccupiedDestinationChangedNothing"] = (
        untouched["fingerprint"] == after["fingerprint"]
        and untouched["configuration"] == after["configuration"]
    )
    if not facts["tagCopyOccupiedDestinationChangedNothing"]:
        raise StageFailure("a refused copy overwrote the destination")

    # Case 3: the source half. A source that is not there is `not_found`, and the
    # refusal names the source end; the destination it would have created stays absent.
    missing = expect_tool_error(client, "tag_copy", {
        "items": [item(paths["missingSource"], paths["missingSourceDestination"])],
    })
    raw["sourceMissing"] = bounded(missing)
    missing_items = (missing.get("details") or {}).get("items") or [{}]
    facts["tagCopySourceMissingCode"] = str(missing.get("code", ""))
    facts["tagCopySourceMissingReason"] = str(missing_items[0].get("reason", ""))
    facts["tagCopySourceMissingNamesTheSource"] = str(missing_items[0].get("path", "")) == paths["missingSource"]
    facts["tagCopySourceMissingIsNotFound"] = (
        facts["tagCopySourceMissingCode"] == "not_found"
        and facts["tagCopySourceMissingReason"] == "sourceMissing"
    )
    if not facts["tagCopySourceMissingIsNotFound"]:
        raise StageFailure(f"a missing source must be not_found: {json.dumps(missing)[:600]}")

    # Case 4: the destination alone is measured against the Target allowlist. The
    # segment-boundary sibling destination is refused and stays absent, while the same
    # Tag as a *source* is answered by the endpoint stage instead of the allowlist one.
    sibling = expect_tool_error(client, "tag_copy", {"items": [item(destination=paths["siblingDestination"])]})
    raw["siblingDenial"] = bounded(sibling)
    sibling_items = (sibling.get("details") or {}).get("items") or [{}]
    facts["tagCopySiblingDenialCode"] = str(sibling.get("code", ""))
    facts["tagCopySiblingDenialReason"] = str(sibling_items[0].get("reason", ""))
    facts["tagCopySiblingDenialNamesTheDestination"] = (
        str(sibling_items[0].get("path", "")) == paths["siblingDestination"]
    )
    facts["tagCopySiblingDenialIsSegmentBoundary"] = (
        facts["tagCopySiblingDenialCode"] == "permission_denied"
        and facts["tagCopySiblingDenialReason"] == "targetNotAllowlisted"
    )
    facts["tagCopySiblingDenialAuditRecorded"] = (sibling.get("details") or {}).get("auditRecorded")
    if not facts["tagCopySiblingDenialIsSegmentBoundary"]:
        raise StageFailure(f"the destination segment boundary was not refused: {json.dumps(sibling)[:600]}")
    exempt = expect_tool_error(client, "tag_copy", {"items": [item(paths["siblingSource"], paths["destination"])]})
    raw["sourceExemption"] = bounded(exempt)
    exempt_items = (exempt.get("details") or {}).get("items") or [{}]
    facts["tagCopySourceIsExemptFromAllowlist"] = (
        str(exempt.get("code", "")) == "conflict"
        and str(exempt_items[0].get("reason", "")) == "destinationExists"
    )
    if not facts["tagCopySourceIsExemptFromAllowlist"]:
        raise StageFailure(f"the source must not be measured by the allowlist: {json.dumps(exempt)[:600]}")

    # Case 5: D30 6 on the destination, under a plain Tag prefix and under a bare `*`.
    udt = expect_tool_error(client, "tag_copy", {"items": [item(destination=paths["udtDestination"])]})
    raw["udtUnderPlainPrefix"] = bounded(udt)
    udt_items = (udt.get("details") or {}).get("items") or [{}]
    facts["tagCopyUdtDenialCode"] = str(udt.get("code", ""))
    facts["tagCopyUdtDenialReason"] = str(udt_items[0].get("reason", ""))
    facts["tagCopyUdtNeedsExplicitTypesEntry"] = (
        facts["tagCopyUdtDenialCode"] == "permission_denied"
        and facts["tagCopyUdtDenialReason"] == "udtDefinitionNotAllowlisted"
    )
    if not facts["tagCopyUdtNeedsExplicitTypesEntry"]:
        raise StageFailure(f"a UDT destination needs an explicit _types_ entry: {json.dumps(udt)[:600]}")

    # Case 6: D10's item ceiling, twice: the project default that refuses twenty-one
    # items, and the deployment's own number, which refuses two.
    over = expect_tool_error(client, "tag_copy", {"items": [item()] * 21})
    raw["overPolicyLimit"] = bounded(over)
    over_details = over.get("details") or {}
    facts["tagCopyOverPolicyLimitCode"] = str(over.get("code", ""))
    facts["tagCopyOverPolicyLimitReason"] = str(over_details.get("reason", ""))
    facts["tagCopyOverPolicyLimitIsRefused"] = (
        facts["tagCopyOverPolicyLimitCode"] == "limit_exceeded"
        and facts["tagCopyOverPolicyLimitReason"] == "itemsOverPolicyLimit"
        and over_details.get("limit") == 20
    )
    if not facts["tagCopyOverPolicyLimitIsRefused"]:
        raise StageFailure(f"an over-budget copy batch must be refused: {json.dumps(over)[:600]}")
    install_and_require(
        config, client, facts, "copy", "CopyCeiling",
        allowlist=policy_document.TAG_COPY_ALLOWLIST, raw=raw, max_items=1,
    )
    lowered = expect_tool_error(client, "tag_copy", {"items": [item(), item()]})
    raw["overPolicyCeiling"] = bounded(lowered)
    lowered_details = lowered.get("details") or {}
    facts["tagCopyPolicyCeilingIsHonoured"] = (
        str(lowered.get("code", "")) == "limit_exceeded"
        and str(lowered_details.get("reason", "")) == "itemsOverPolicyLimit"
        and lowered_details.get("limit") == 1
        and lowered_details.get("requested") == 2
    )
    if not facts["tagCopyPolicyCeilingIsHonoured"]:
        raise StageFailure(
            f"the deployment's copy ceiling never reached the refusal: {json.dumps(lowered)[:600]}"
        )

    # Case 7: the reserved provider bounds both ends under an explicit `*`, before any
    # read — so a source that is not there still answers with the provider reason,
    # which is what makes it a provider rule rather than an access rule. The policy
    # document and the probe Tag do not move.
    install_and_require(
        config, client, facts, "copy", "CopyWildcard",
        allowlist=policy_document.WILDCARD_ALLOWLIST, raw=raw,
    )
    probe_before = read_tag_value(client, config.write_probe_path).get("value")
    policy_before = read_tag_value(client, config.policy_path).get("value")
    reserved_source = expect_tool_error(client, "tag_copy", {
        "items": [item(paths["reservedSource"], paths["reservedSourceDestination"])],
    })
    raw["reservedSourceRefusal"] = bounded(reserved_source)
    source_refused = (reserved_source.get("details") or {}).get("items") or [{}]
    facts["tagCopyReservedSourceCode"] = str(reserved_source.get("code", ""))
    facts["tagCopyReservedSourceReason"] = str(source_refused[0].get("reason", ""))
    facts["tagCopyReservedSourceRefusedUnderWildcard"] = (
        facts["tagCopyReservedSourceCode"] == "permission_denied"
        and facts["tagCopyReservedSourceReason"] == "reservedProvider"
        and str(source_refused[0].get("path", "")) == paths["reservedSource"]
    )
    reserved_destination = expect_tool_error(client, "tag_copy", {
        "items": [item(destination=paths["reservedDestination"])],
    })
    raw["reservedDestinationRefusal"] = bounded(reserved_destination)
    destination_refused = (reserved_destination.get("details") or {}).get("items") or [{}]
    facts["tagCopyReservedDestinationCode"] = str(reserved_destination.get("code", ""))
    facts["tagCopyReservedDestinationReason"] = str(destination_refused[0].get("reason", ""))
    facts["tagCopyReservedDestinationRefusedUnderWildcard"] = (
        facts["tagCopyReservedDestinationCode"] == "permission_denied"
        and facts["tagCopyReservedDestinationReason"] == "reservedProvider"
        and str(destination_refused[0].get("path", "")) == paths["reservedDestination"]
    )
    probe_after = read_tag_value(client, config.write_probe_path).get("value")
    policy_after = read_tag_value(client, config.policy_path).get("value")
    facts["tagCopyReservedProviderValueUnchanged"] = probe_before == probe_after
    facts["tagCopyPolicyDocumentUnclobbered"] = policy_before == policy_after
    if not (
        facts["tagCopyReservedSourceRefusedUnderWildcard"]
        and facts["tagCopyReservedDestinationRefusedUnderWildcard"]
    ):
        raise StageFailure(
            f"the reserved provider was not refused at one end: {json.dumps(reserved_source)[:300]} "
            f"{json.dumps(reserved_destination)[:300]}"
        )
    wildcard_udt = expect_tool_error(client, "tag_copy", {"items": [item(destination=paths["udtDestination"])]})
    raw["udtUnderWildcard"] = bounded(wildcard_udt)
    wildcard_items = (wildcard_udt.get("details") or {}).get("items") or [{}]
    facts["tagCopyBareWildcardDoesNotCoverUdt"] = (
        str(wildcard_udt.get("code", "")) == "permission_denied"
        and str(wildcard_items[0].get("reason", "")) == "udtDefinitionNotAllowlisted"
    )
    if not facts["tagCopyBareWildcardDoesNotCoverUdt"]:
        raise StageFailure(f"a bare * must not cover a UDT destination: {json.dumps(wildcard_udt)[:600]}")

    # Case 8: the explicit `_types_` entry is honoured. A destination inside the
    # definition namespace would now have to be written for the entry to mean anything,
    # and a copy never writes a definition folder on a disposable Gateway, so the case
    # measures the allowlist stage alone: a batch whose first item is that destination
    # and whose second is the segment-boundary sibling refuses wholly and lists only
    # the sibling. Under a plain prefix the same batch lists both ends (Case 5 asked
    # the first one alone), so the entry is what changed.
    install_and_require(
        config, client, facts, "copy", "CopyTypes",
        allowlist=policy_document.TAG_COPY_TYPES_ALLOWLIST, raw=raw,
    )
    honoured = expect_tool_error(client, "tag_copy", {
        "items": [item(destination=paths["udtDestination"]), item(destination=paths["siblingDestination"])],
    })
    raw["udtUnderTypesEntry"] = bounded(honoured)
    honoured_items = (honoured.get("details") or {}).get("items") or []
    facts["tagCopyTypesEntryIsHonoured"] = (
        str((honoured.get("details") or {}).get("reason", "")) == "preflightTargetRefused"
        and len(honoured_items) == 1
        and honoured_items[0].get("index") == 1
        and str(honoured_items[0].get("reason", "")) == "targetNotAllowlisted"
    )
    if not facts["tagCopyTypesEntryIsHonoured"]:
        raise StageFailure(f"the explicit _types_ entry was not honoured: {json.dumps(honoured)[:600]}")
    # Nothing dispatched, so neither the definition probe folder nor the missing
    # source's destination exists anywhere the provider can show.
    exported = provider_export(config)
    facts["tagCopyPreflightExecutedNothing"] = (
        policy_document.find_tag(exported, policy_document.TAG_COPY_PROBE_FOLDER) is None
        and policy_document.find_tag(exported, "MissingSource") is None
        and policy_document.find_tag(exported, "ReservedSource") is None
    )
    if not facts["tagCopyPreflightExecutedNothing"]:
        raise StageFailure("a refused copy Preflight executed part of its batch")
    return {
        "stage": "tag-copy",
        "ok": True,
        "identity": identity(config),
        "guard": config.guard,
        "facts": facts,
        "raw": raw,
    }


# --------------------------------------------------------------------------- #
# Stages: ticket #12 (`tag_delete`, `tag_move` and `tag_rename`)
#
# No setup stage: `tag-update-setup` already seeds the fixture Tags, the audit
# profile and the policy provider, and each of these three Tools installs its own
# policy document over the same reserved provider. The stages run in the
# workflow's order — create, copy, move, rename, delete — because a move borrows
# `tag_create`'s node as its source, a rename creates the occupied name a delete
# then reasons about, and a Folder delete takes everything beneath it.
# --------------------------------------------------------------------------- #


def tag_move_paths() -> dict[str, str]:
    """The `tag_move` endpoint pairs of this run, keyed as the recorded bodies do."""
    return {
        "source": policy_document.TAG_MOVE_SOURCE,
        "writeTarget": policy_document.TAG_FIXTURE_PATH,
        "destination": policy_document.TAG_MOVE_DESTINATION,
        "occupiedDestination": policy_document.TAG_MOVE_OCCUPIED_DESTINATION,
        "missingSource": policy_document.TAG_MOVE_MISSING_SOURCE,
        "missingDestination": policy_document.TAG_MOVE_MISSING_DESTINATION,
        "leafMismatchDestination": policy_document.TAG_MOVE_LEAF_MISMATCH_DESTINATION,
        "siblingSource": policy_document.TAG_MOVE_SIBLING_SOURCE,
        "siblingDestination": policy_document.TAG_MOVE_SIBLING_DESTINATION,
        "reservedSource": policy_document.TAG_MOVE_RESERVED_SOURCE,
        "reservedSourceDestination": policy_document.TAG_MOVE_RESERVED_SOURCE_DESTINATION,
        "reservedDestination": policy_document.TAG_MOVE_RESERVED_DESTINATION,
        "udtSource": policy_document.TAG_MOVE_UDT_SOURCE,
        "udtDestination": policy_document.TAG_MOVE_UDT_DESTINATION,
    }


def tag_rename_paths() -> dict[str, str]:
    """The `tag_rename` targets of this run, keyed as the recorded bodies do."""
    return {
        "target": policy_document.TAG_RENAME_TARGET,
        "targetNewName": policy_document.TAG_RENAME_TARGET_NEW_NAME,
        "targetNewPath": policy_document.TAG_RENAME_TARGET_NEW_PATH,
        "occupiedSource": policy_document.TAG_RENAME_OCCUPIED_SOURCE,
        "occupiedNewName": policy_document.TAG_RENAME_TARGET_NEW_NAME,
        "staleSource": policy_document.TAG_RENAME_OCCUPIED_SOURCE,
        "staleNewName": policy_document.TAG_RENAME_STALE_NEW_NAME,
        "missingTarget": policy_document.TAG_RENAME_MISSING_TARGET,
        "missingNewName": policy_document.TAG_RENAME_MISSING_NEW_NAME,
        "siblingTarget": policy_document.TAG_RENAME_SIBLING_TARGET,
        "siblingNewName": policy_document.TAG_RENAME_SIBLING_NEW_NAME,
        "reservedTarget": policy_document.TAG_RENAME_RESERVED_TARGET,
        "reservedNewName": policy_document.TAG_RENAME_RESERVED_NEW_NAME,
        "multiSegmentName": policy_document.TAG_RENAME_MULTI_SEGMENT_NAME,
        "udtTarget": policy_document.TAG_RENAME_UDT_TARGET,
        "udtNewName": policy_document.TAG_RENAME_UDT_NEW_NAME,
        "udtNewPath": (
            f"[{policy_document.TAG_FIXTURE_PROVIDER}]{policy_document.UDT_NAMESPACE}/"
            f"{policy_document.TAG_FIXTURE_ROOT}/{policy_document.TAG_RENAME_UDT_NEW_NAME}"
        ),
        "siblingNewPath": (
            f"[{policy_document.TAG_FIXTURE_PROVIDER}]{policy_document.TAG_FIXTURE_SIBLING_ROOT}/"
            f"{policy_document.TAG_RENAME_SIBLING_NEW_NAME}"
        ),
        "staleNewPath": (
            f"[{policy_document.TAG_FIXTURE_PROVIDER}]{policy_document.TAG_FIXTURE_ROOT}/"
            f"{policy_document.TAG_RENAME_STALE_NEW_NAME}"
        ),
        "missingNewPath": (
            f"[{policy_document.TAG_FIXTURE_PROVIDER}]{policy_document.TAG_FIXTURE_ROOT}/"
            f"{policy_document.TAG_RENAME_MISSING_NEW_NAME}"
        ),
        "reservedNewPath": (
            f"[{policy_document.TAG_FIXTURE_PROVIDER}]{policy_document.TAG_FIXTURE_ROOT}/"
            f"{policy_document.TAG_RENAME_RESERVED_NEW_NAME}"
        ),
    }


def tag_delete_paths() -> dict[str, str]:
    """The `tag_delete` targets of this run, keyed as the recorded bodies do."""
    return {
        "target": policy_document.TAG_DELETE_TARGET,
        "folder": policy_document.TAG_DELETE_FOLDER,
        "folderChild": policy_document.TAG_DELETE_FOLDER_CHILD,
        "staleTarget": policy_document.TAG_DELETE_STALE_TARGET,
        "siblingTarget": policy_document.TAG_DELETE_SIBLING_TARGET,
        "missingTarget": policy_document.TAG_DELETE_MISSING_TARGET,
        "reservedTarget": policy_document.TAG_DELETE_RESERVED_TARGET,
        "udtTarget": policy_document.TAG_DELETE_UDT_TARGET,
    }


def install_ticket12_policy(
    config: Config, client: mcp_client.McpClient, tool: str, *, allowlist: tuple[str, ...],
    audit_mode: str = "best_effort", max_items: int | None = None,
    deadline_seconds: float = 150.0,
) -> dict[str, Any]:
    """Install one ticket #12 Tool's policy over the same reserved provider."""
    raw_builder = getattr(policy_document, f"{tool}_tag_document_bytes")
    sha = getattr(policy_document, f"{tool}_policy_sha256")
    overrides: dict[str, Any] = {"allowlist": allowlist, "audit_mode": audit_mode}
    if max_items is not None:
        overrides["max_items"] = max_items
    return install_policy(
        config, client,
        document=raw_builder(**overrides),
        expected_sha256=sha(**overrides),
        deadline_seconds=deadline_seconds,
    )


def expect_refusal(
    client: mcp_client.McpClient, tool: str, arguments: dict[str, Any], *, code: str,
    details_reason: str, reasons: list[tuple[int, str, str]], raw: dict[str, Any], key: str,
    path_key: str = "path",
) -> dict[str, Any]:
    """Record one Preflight refusal and assert its code, reason and every item.

    A refusal is the Tool's whole answer, so the case is only meaningful if the
    code, the Preflight reason and the per-item reasons are the ones the D30 rule
    names. `reasons` is `(index, reason, path)` in the handler's own item order.
    """
    error = expect_tool_error(client, tool, arguments)
    raw[key] = bounded(error, 4_000)
    details = error.get("details") or {}
    items = details.get("items") or []
    seen = [
        (int(item.get("index", -1)), str(item.get("reason", "")), str(item.get(path_key, "")))
        for item in items
    ]
    if str(error.get("code", "")) != code or str(details.get("reason", "")) != details_reason:
        raise StageFailure(
            f"{tool} {key}: expected {code}/{details_reason}: {json.dumps(error)[:600]}"
        )
    if seen != reasons:
        raise StageFailure(
            f"{tool} {key}: expected items {reasons}, observed {seen}: {json.dumps(error)[:600]}"
        )
    return error


def exported_at(config: Config, path: str) -> dict[str, Any] | None:
    """One node of the provider export at an exact path, or None.

    A Configuration read cannot answer "is this path there" — a Gateway answers a
    path that is not there with a synthesized node (ticket #10 evidence) — so the
    provider's own export is the independent presence check. Two shapes are walked:
    the nested Designer document a Gateway serves, whose nodes carry only their own
    name, and the flat list the recorded fake serves in its place, whose nodes carry
    their full path.
    """
    return _export_walk(provider_export(config), _relative_tag_path(path), "")


def _relative_tag_path(path: str) -> str:
    return path.split("]", 1)[-1].strip("/")


def _export_walk(node: Any, wanted: str, prefix: str) -> dict[str, Any] | None:
    children = node.get("tags") if isinstance(node, dict) else None
    for child in children if isinstance(children, list) else []:
        if not isinstance(child, dict):
            continue
        name = str(child.get("name", ""))
        candidate = str(child.get("path") or (prefix + "/" + name if prefix else name))
        if _relative_tag_path(candidate) == wanted:
            return child
        found = _export_walk(child, wanted, _relative_tag_path(candidate))
        if found is not None:
            return found
    return None




def stage_tag_move(config: Config) -> dict[str, Any]:
    """The ticket #12 `tag_move` live cases: the move, its collision, its refusals."""
    raw: dict[str, Any] = {}
    facts: dict[str, Any] = {}
    paths = tag_move_paths()
    client = mcp_client.McpClient(config.configurator_url, config.api_token)
    raw["initialize"] = bounded(client.initialize())
    zero = "tcf1:" + "0" * 64
    inventory = sorted(client.tools_list())
    facts["tagMoveConfiguratorInventory"] = inventory
    facts["tagMoveConfiguratorCarriesAllThree"] = all(
        tool in inventory for tool in ("tag_delete", "tag_move", "tag_rename")
    )
    if not facts["tagMoveConfiguratorCarriesAllThree"]:
        raise StageFailure(f"the configurator deployment does not serve all three Tools: {inventory}")

    def item(source: str = "", destination: str = "", fingerprint: str = "") -> dict[str, str]:
        return {
            "sourcePath": source or paths["source"],
            "destinationPath": destination or paths["destination"],
            "expectedFingerprint": fingerprint or zero,
        }

    # Case 0: one `system.tag.move` call lands each source under its own name, so a
    # destination whose leaf differs is an input refusal, and a move that renames is
    # `tag_rename`'s.
    expect_refusal(
        client, "tag_move", {"items": [item(destination=paths["leafMismatchDestination"])]},
        code="invalid_argument", details_reason="preflightInputFailed",
        reasons=[(0, "destinationLeafDiffersFromSource", paths["leafMismatchDestination"])],
        raw=raw, key="leafMismatch",
    )
    facts["tagMoveLeafMismatchCode"] = "invalid_argument"
    facts["tagMoveLeafMismatchReason"] = "destinationLeafDiffersFromSource"
    expect_refusal(
        client, "tag_move", {"items": [item()] * (HARD_ITEM_CEILING + 1)},
        code="limit_exceeded", details_reason="itemsOverHardLimit", reasons=[],
        raw=raw, key="itemsOverHardCeiling",
    )
    facts["tagMoveHardItemCeilingReason"] = "itemsOverHardLimit"
    overlong = policy_document.OVERLONG_PATH_LEAF
    expect_refusal(
        client, "tag_move", {"items": [item(
            source=f"[{policy_document.TAG_FIXTURE_PROVIDER}]{policy_document.TAG_FIXTURE_ROOT}/{overlong}",
            destination=f"{policy_document.TAG_UPDATE_FOLDER}/{overlong}",
        )]},
        code="limit_exceeded", details_reason="pathOverLength", reasons=[],
        raw=raw, key="pathOverCeiling",
    )
    facts["tagMovePathOverCeilingReason"] = "pathOverLength"

    install_and_require(
        config, client, facts, "move", "Move",
        allowlist=policy_document.TAG_MOVE_ALLOWLIST, raw=raw,
    )

    # Case 1: the positive move. Both ends are read independently afterwards through
    # the provider's own export at the two exact paths, because the destination is
    # what the call promised to create and the source is what it promised to remove.
    source_before = tag_config(client, paths["source"])
    structured = expect_structured(client, "tag_move", {"items": [item(
        fingerprint=source_before["fingerprint"],
    )]})
    raw["allowlistedMove"] = bounded(structured, 40_000)
    items = structured.get("items") or [{}]
    observed = structured.get("observed") or [{}]
    summary = structured.get("summary") or {}
    facts["tagMoveStatus"] = str(items[0].get("status", ""))
    facts["tagMoveNativeOutcome"] = str((items[0].get("nativeOutcome") or {}).get("name", ""))
    facts["tagMoveAuditMode"] = str(summary.get("auditMode", ""))
    facts["tagMoveAuditRecorded"] = summary.get("auditRecorded")
    destination_read = tag_config(client, paths["destination"])
    raw["tagGetConfigDestination"] = bounded(destination_read, 20_000)
    facts["tagMoveObservedDestinationPresent"] = (
        len(observed) == 2
        and str(observed[0].get("path", "")) == paths["destination"]
        and observed[0].get("status") == "ok"
        and observed[0].get("absent") is False
    )
    facts["tagMoveObservedDestinationMatchesIndependentRead"] = (
        observed[0].get("fingerprint") == destination_read["fingerprint"]
        and derived_fingerprint(observed[0].get("configuration")) == observed[0].get("fingerprint")
    )
    facts["tagMoveSourceObservedAbsent"] = (
        len(observed) == 2
        and str(observed[1].get("path", "")) == paths["source"]
        and observed[1].get("status") == "ok"
        and observed[1].get("absent") is True
    )
    facts["tagMoveExportShowsDestination"] = (
        exported_at(config, paths["destination"]) is not None
    )
    facts["tagMoveExportSourceGone"] = (
        exported_at(config, paths["source"]) is None
    )
    if not (
        facts["tagMoveStatus"] == "executed"
        and facts["tagMoveNativeOutcome"].startswith("Good")
        and facts["tagMoveObservedDestinationPresent"]
        and facts["tagMoveSourceObservedAbsent"]
        and facts["tagMoveExportShowsDestination"]
        and facts["tagMoveExportSourceGone"]
    ):
        raise StageFailure(f"the allowlisted move did not land: {json.dumps(structured)[:800]}")

    correlation = str((structured.get("meta") or {}).get("correlationId", ""))
    audit_status, rows = audited_rows(config, correlation, "ignition-mcp.tag_move")
    raw["auditQuery"] = {"status": audit_status, "rows": bounded(rows, 20_000)}
    facts["tagMoveAuditRowsForCorrelation"] = len(rows)
    facts["tagMoveAuditActorIsServiceIdentity"] = bool(rows) and all(
        str(row.get("actor", "")) == policy_document.SERVICE_IDENTITY for row in rows
    )
    if len(rows) < 2:
        raise StageFailure(f"the move wrote no audit pair for {correlation}: {audit_status}")

    # Case 2: the destination `tag_copy` built inside `Nested` is occupied, so the
    # same move is conflict and leaves both ends exactly as they were.
    occupied_before = tag_config(client, paths["occupiedDestination"])
    source_unchanged = tag_config(client, policy_document.TAG_FIXTURE_PATH)
    expect_refusal(
        client, "tag_move", {"items": [item(
            source=policy_document.TAG_FIXTURE_PATH,
            destination=paths["occupiedDestination"],
            fingerprint=tag_config(client, policy_document.TAG_FIXTURE_PATH)["fingerprint"],
        )]},
        code="conflict", details_reason="preflightPreconditionFailed",
        reasons=[(0, "destinationExists", paths["occupiedDestination"])],
        raw=raw, key="occupiedDestination",
    )
    facts["tagMoveOccupiedDestinationCode"] = "conflict"
    facts["tagMoveOccupiedDestinationReason"] = "destinationExists"
    occupied_after = tag_config(client, paths["occupiedDestination"])
    facts["tagMoveOccupiedDestinationChangedNothing"] = (
        occupied_after["fingerprint"] == occupied_before["fingerprint"]
        and occupied_after["configuration"] == occupied_before["configuration"]
    )
    facts["tagMoveOccupiedDestinationLeftTheSource"] = (
        tag_config(client, policy_document.TAG_FIXTURE_PATH)["fingerprint"] == source_unchanged["fingerprint"]
    )
    if not facts["tagMoveOccupiedDestinationChangedNothing"]:
        raise StageFailure("a refused move overwrote the destination it was refused for")

    # Case 3: the Precondition token. A stale fingerprint is conflict, and a source
    # that is not there is not_found; neither dispatches anything.
    expect_refusal(
        client, "tag_move", {"items": [item(
            source=policy_document.TAG_FIXTURE_PATH, destination=paths["occupiedDestination"],
        )]},
        code="conflict", details_reason="preflightPreconditionFailed",
        reasons=[(0, "fingerprintMismatch", policy_document.TAG_FIXTURE_PATH)],
        raw=raw, key="staleFingerprint",
    )
    facts["tagMoveStaleFingerprintCode"] = "conflict"
    facts["tagMoveStaleFingerprintReason"] = "fingerprintMismatch"
    expect_refusal(
        client, "tag_move", {"items": [item(
            source=paths["missingSource"], destination=paths["missingDestination"],
        )]},
        code="not_found", details_reason="preflightPreconditionFailed",
        reasons=[(0, "sourceMissing", paths["missingSource"])],
        raw=raw, key="missingSource",
    )
    facts["tagMoveMissingSourceCode"] = "not_found"
    facts["tagMoveMissingSourceReason"] = "sourceMissing"

    # Case 4: D30 6 measures the source and the destination. The segment-boundary
    # sibling is refused at the source end, which is the half a copy never checks.
    expect_refusal(
        client, "tag_move", {"items": [item(
            source=paths["siblingSource"], destination=paths["siblingDestination"],
        )]},
        code="permission_denied", details_reason="preflightTargetRefused",
        reasons=[(0, "targetNotAllowlisted", paths["siblingSource"])],
        raw=raw, key="siblingDenial",
    )
    facts["tagMoveSiblingDenialReason"] = "targetNotAllowlisted"
    expect_refusal(
        client, "tag_move", {"items": [item(
            source=paths["udtSource"], destination=paths["udtDestination"],
        )]},
        code="permission_denied", details_reason="preflightTargetRefused",
        reasons=[(0, "udtDefinitionNotAllowlisted", paths["udtSource"])],
        raw=raw, key="udtUnderPlainPrefix",
    )
    facts["tagMoveUdtNeedsExplicitTypesEntry"] = True

    # Case 5: D10's deployment ceiling, and the whole-batch refusal that keeps the
    # allowed item of a batch from moving.
    expect_refusal(
        client, "tag_move", {"items": [item()] * 21},
        code="limit_exceeded", details_reason="itemsOverPolicyLimit", reasons=[],
        raw=raw, key="overPolicyLimit",
    )
    facts["tagMoveOverPolicyLimitReason"] = "itemsOverPolicyLimit"
    install_and_require(
        config, client, facts, "move", "MoveCeiling",
        allowlist=policy_document.TAG_MOVE_ALLOWLIST, raw=raw, max_items=1,
    )
    expect_refusal(
        client, "tag_move", {"items": [item(), item(source=policy_document.TAG_FIXTURE_PATH,
                                                     destination=paths["occupiedDestination"])]},
        code="limit_exceeded", details_reason="itemsOverPolicyLimit", reasons=[],
        raw=raw, key="overPolicyCeiling",
    )
    facts["tagMovePolicyCeilingIsHonoured"] = True

    # Case 6: the reserved provider bounds both ends under an explicit `*`, before
    # any read — so a source that is not there still answers with the provider
    # reason, which is what makes it a provider rule and not an access rule.
    install_and_require(
        config, client, facts, "move", "MoveWildcard",
        allowlist=policy_document.WILDCARD_ALLOWLIST, raw=raw,
    )
    probe_before = read_tag_value(client, config.write_probe_path).get("value")
    policy_before = read_tag_value(client, config.policy_path).get("value")
    expect_refusal(
        client, "tag_move", {"items": [item(
            source=paths["reservedSource"], destination=paths["reservedSourceDestination"],
        )]},
        code="permission_denied", details_reason="preflightTargetRefused",
        reasons=[(0, "reservedProvider", paths["reservedSource"])],
        raw=raw, key="reservedSourceRefusal",
    )
    facts["tagMoveReservedSourceReason"] = "reservedProvider"
    expect_refusal(
        client, "tag_move", {"items": [item(destination=paths["reservedDestination"])]},
        code="permission_denied", details_reason="preflightTargetRefused",
        reasons=[(0, "reservedProvider", paths["reservedDestination"])],
        raw=raw, key="reservedDestinationRefusal",
    )
    facts["tagMoveReservedDestinationReason"] = "reservedProvider"
    probe_after = read_tag_value(client, config.write_probe_path).get("value")
    policy_after = read_tag_value(client, config.policy_path).get("value")
    facts["tagMoveReservedProviderValueUnchanged"] = probe_before == probe_after
    facts["tagMovePolicyDocumentUnclobbered"] = policy_before == policy_after
    expect_refusal(
        client, "tag_move", {"items": [item(
            source=paths["udtSource"], destination=paths["udtDestination"],
        )]},
        code="permission_denied", details_reason="preflightTargetRefused",
        reasons=[(0, "udtDefinitionNotAllowlisted", paths["udtSource"])],
        raw=raw, key="udtUnderWildcard",
    )
    facts["tagMoveBareWildcardDoesNotCoverUdt"] = True

    # Case 7: the explicit `_types_` entry is honoured. A batch whose first item is
    # the definition pair and whose second is the segment-boundary sibling refuses
    # wholly and lists only the sibling, where the same pair alone was refused as a
    # definition under a plain prefix (Case 4). What changed is the entry.
    install_and_require(
        config, client, facts, "move", "MoveTypes",
        allowlist=policy_document.TAG_TYPES_ALLOWLIST, raw=raw,
    )
    expect_refusal(
        client, "tag_move", {"items": [
            item(source=paths["udtSource"], destination=paths["udtDestination"]),
            item(source=paths["siblingSource"], destination=paths["siblingDestination"]),
        ]},
        code="permission_denied", details_reason="preflightTargetRefused",
        reasons=[(1, "targetNotAllowlisted", paths["siblingSource"])],
        raw=raw, key="udtUnderTypesEntry",
    )
    facts["tagMoveTypesEntryIsHonoured"] = True
    facts["tagMovePreflightExecutedNothing"] = (
        exported_at(config, paths["udtDestination"]) is None
        and exported_at(config, paths["siblingDestination"]) is None
    )
    if not facts["tagMovePreflightExecutedNothing"]:
        raise StageFailure("a refused move Preflight executed part of its batch")
    return {
        "stage": "tag-move",
        "ok": True,
        "identity": identity(config),
        "guard": config.guard,
        "facts": facts,
        "raw": raw,
    }


def stage_tag_rename(config: Config) -> dict[str, Any]:
    """The ticket #12 `tag_rename` live cases: the rename, its collision, its refusals."""
    raw: dict[str, Any] = {}
    facts: dict[str, Any] = {}
    paths = tag_rename_paths()
    client = mcp_client.McpClient(config.configurator_url, config.api_token)
    raw["initialize"] = bounded(client.initialize())
    zero = "tcf1:" + "0" * 64

    def item(path: str = "", new_name: str = "", fingerprint: str = "") -> dict[str, str]:
        return {
            "path": path or paths["target"],
            "newName": new_name or paths["targetNewName"],
            "expectedFingerprint": fingerprint or zero,
        }

    # Case 0: a rename names one leaf, never a path, because `system.tag.rename`
    # keeps the target's own parent; the D10 ceilings are measured over the request.
    expect_refusal(
        client, "tag_rename", {"items": [item(new_name=paths["multiSegmentName"])]},
        code="invalid_argument", details_reason="preflightInputFailed",
        reasons=[(0, "newNameNotASingleSegment", paths["target"])],
        raw=raw, key="multiSegmentName",
    )
    facts["tagRenameMultiSegmentNameCode"] = "invalid_argument"
    facts["tagRenameMultiSegmentNameReason"] = "newNameNotASingleSegment"
    expect_refusal(
        client, "tag_rename", {"items": [item()] * (HARD_ITEM_CEILING + 1)},
        code="limit_exceeded", details_reason="itemsOverHardLimit", reasons=[],
        raw=raw, key="itemsOverHardCeiling",
    )
    facts["tagRenameHardItemCeilingReason"] = "itemsOverHardLimit"
    expect_refusal(
        client, "tag_rename", {"items": [item(
            path=f"[{policy_document.TAG_FIXTURE_PROVIDER}]{policy_document.TAG_FIXTURE_ROOT}/"
                 + policy_document.OVERLONG_PATH_LEAF,
            new_name="Overlong",
        )]},
        code="limit_exceeded", details_reason="pathOverLength", reasons=[],
        raw=raw, key="pathOverCeiling",
    )
    facts["tagRenamePathOverCeilingReason"] = "pathOverLength"

    install_and_require(
        config, client, facts, "rename", "Rename",
        allowlist=policy_document.TAG_RENAME_ALLOWLIST, raw=raw,
    )

    # Case 1: the positive rename. The new path is what the call promised to create
    # and the old path is what it promised to remove, so both are read independently
    # afterwards, through the provider's own export at each exact path.
    target_before = tag_config(client, paths["target"])
    structured = expect_structured(client, "tag_rename", {"items": [item(
        fingerprint=target_before["fingerprint"],
    )]})
    raw["allowlistedRename"] = bounded(structured, 40_000)
    items = structured.get("items") or [{}]
    observed = structured.get("observed") or [{}]
    summary = structured.get("summary") or {}
    facts["tagRenameStatus"] = str(items[0].get("status", ""))
    facts["tagRenameNativeOutcome"] = str((items[0].get("nativeOutcome") or {}).get("name", ""))
    facts["tagRenameItemCarriesBothPaths"] = (
        str(items[0].get("path", "")) == paths["target"]
        and str(items[0].get("newPath", "")) == paths["targetNewPath"]
    )
    facts["tagRenameAuditMode"] = str(summary.get("auditMode", ""))
    facts["tagRenameAuditRecorded"] = summary.get("auditRecorded")
    new_path_read = tag_config(client, paths["targetNewPath"])
    raw["tagGetConfigNewPath"] = bounded(new_path_read, 20_000)
    facts["tagRenameObservedNewPathPresent"] = (
        len(observed) == 2
        and str(observed[0].get("path", "")) == paths["targetNewPath"]
        and observed[0].get("status") == "ok"
        and observed[0].get("absent") is False
    )
    facts["tagRenameObservedNewPathMatchesIndependentRead"] = (
        observed[0].get("fingerprint") == new_path_read["fingerprint"]
        and derived_fingerprint(observed[0].get("configuration")) == observed[0].get("fingerprint")
    )
    facts["tagRenameOldPathObservedAbsent"] = (
        len(observed) == 2
        and str(observed[1].get("path", "")) == paths["target"]
        and observed[1].get("status") == "ok"
        and observed[1].get("absent") is True
    )
    facts["tagRenameExportShowsNewPath"] = (
        exported_at(config, paths["targetNewPath"]) is not None
    )
    facts["tagRenameExportOldPathGone"] = (
        exported_at(config, paths["target"]) is None
    )
    if not (
        facts["tagRenameStatus"] == "executed"
        and facts["tagRenameNativeOutcome"].startswith("Good")
        and facts["tagRenameObservedNewPathPresent"]
        and facts["tagRenameOldPathObservedAbsent"]
        and facts["tagRenameExportShowsNewPath"]
        and facts["tagRenameExportOldPathGone"]
    ):
        raise StageFailure(f"the allowlisted rename did not land: {json.dumps(structured)[:800]}")

    correlation = str((structured.get("meta") or {}).get("correlationId", ""))
    audit_status, rows = audited_rows(config, correlation, "ignition-mcp.tag_rename")
    raw["auditQuery"] = {"status": audit_status, "rows": bounded(rows, 20_000)}
    facts["tagRenameAuditRowsForCorrelation"] = len(rows)
    facts["tagRenameAuditActorIsServiceIdentity"] = bool(rows) and all(
        str(row.get("actor", "")) == policy_document.SERVICE_IDENTITY for row in rows
    )
    if len(rows) < 2:
        raise StageFailure(f"the rename wrote no audit pair for {correlation}: {audit_status}")

    # Case 2: the name the positive case just created is occupied, so the same new
    # name on another target is conflict and leaves both it and the new path alone.
    occupied_before = tag_config(client, paths["targetNewPath"])
    source_before = tag_config(client, paths["occupiedSource"])
    expect_refusal(
        client, "tag_rename", {"items": [item(
            path=paths["occupiedSource"], new_name=paths["occupiedNewName"],
            fingerprint=source_before["fingerprint"],
        )]},
        code="conflict", details_reason="preflightPreconditionFailed",
        reasons=[(0, "newPathExists", paths["targetNewPath"])],
        raw=raw, key="occupiedNewPath", path_key="refusedPath",
    )
    facts["tagRenameOccupiedNewPathCode"] = "conflict"
    facts["tagRenameOccupiedNewPathReason"] = "newPathExists"
    occupied_after = tag_config(client, paths["targetNewPath"])
    facts["tagRenameOccupiedNewPathChangedNothing"] = (
        occupied_after["fingerprint"] == occupied_before["fingerprint"]
    )
    facts["tagRenameOccupiedNewPathLeftTheTarget"] = (
        tag_config(client, paths["occupiedSource"])["fingerprint"] == source_before["fingerprint"]
    )
    if not facts["tagRenameOccupiedNewPathChangedNothing"]:
        raise StageFailure("a refused rename overwrote the new path it was refused for")

    # Case 3: the Precondition token. A stale fingerprint is conflict and a target
    # that is not there is not_found; neither dispatches anything.
    expect_refusal(
        client, "tag_rename", {"items": [item(
            path=paths["staleSource"], new_name=paths["staleNewName"],
        )]},
        code="conflict", details_reason="preflightPreconditionFailed",
        reasons=[(0, "fingerprintMismatch", paths["staleSource"])],
        raw=raw, key="staleFingerprint",
    )
    facts["tagRenameStaleFingerprintCode"] = "conflict"
    facts["tagRenameStaleFingerprintReason"] = "fingerprintMismatch"
    expect_refusal(
        client, "tag_rename", {"items": [item(
            path=paths["missingTarget"], new_name=paths["missingNewName"],
        )]},
        code="not_found", details_reason="preflightPreconditionFailed",
        reasons=[(0, "targetMissing", paths["missingTarget"])],
        raw=raw, key="missingTarget",
    )
    facts["tagRenameMissingTargetCode"] = "not_found"
    facts["tagRenameMissingTargetReason"] = "targetMissing"

    # Case 4: D30 6 measures the new path, and the segment-boundary sibling's new
    # path is outside the allowlist even though its target's parent was too.
    expect_refusal(
        client, "tag_rename", {"items": [item(
            path=paths["siblingTarget"], new_name=paths["siblingNewName"],
        )]},
        code="permission_denied", details_reason="preflightTargetRefused",
        reasons=[(0, "targetNotAllowlisted", paths["siblingNewPath"])],
        raw=raw, key="siblingDenial", path_key="refusedPath",
    )
    facts["tagRenameSiblingDenialReason"] = "targetNotAllowlisted"
    expect_refusal(
        client, "tag_rename", {"items": [item(
            path=paths["udtTarget"], new_name=paths["udtNewName"],
        )]},
        code="permission_denied", details_reason="preflightTargetRefused",
        reasons=[(0, "udtDefinitionNotAllowlisted",
                  f"[{policy_document.TAG_FIXTURE_PROVIDER}]{policy_document.UDT_NAMESPACE}/"
                  f"{policy_document.TAG_FIXTURE_ROOT}/{paths['udtNewName']}")],
        raw=raw, key="udtUnderPlainPrefix", path_key="refusedPath",
    )
    facts["tagRenameUdtNeedsExplicitTypesEntry"] = True

    # Case 5: D10's deployment ceiling, and the whole-batch refusal.
    expect_refusal(
        client, "tag_rename", {"items": [item()] * 21},
        code="limit_exceeded", details_reason="itemsOverPolicyLimit", reasons=[],
        raw=raw, key="overPolicyLimit",
    )
    facts["tagRenameOverPolicyLimitReason"] = "itemsOverPolicyLimit"
    install_and_require(
        config, client, facts, "rename", "RenameCeiling",
        allowlist=policy_document.TAG_RENAME_ALLOWLIST, raw=raw, max_items=1,
    )
    expect_refusal(
        client, "tag_rename", {"items": [item(), item(path=paths["staleSource"], new_name=paths["staleNewName"])]},
        code="limit_exceeded", details_reason="itemsOverPolicyLimit", reasons=[],
        raw=raw, key="overPolicyCeiling",
    )
    facts["tagRenamePolicyCeilingIsHonoured"] = True

    # Case 6: the reserved provider is refused at the target end under an explicit
    # `*`, and the probe Tag and policy document do not move.
    install_and_require(
        config, client, facts, "rename", "RenameWildcard",
        allowlist=policy_document.WILDCARD_ALLOWLIST, raw=raw,
    )
    probe_before = read_tag_value(client, config.write_probe_path).get("value")
    policy_before = read_tag_value(client, config.policy_path).get("value")
    expect_refusal(
        client, "tag_rename", {"items": [item(
            path=paths["reservedTarget"], new_name=paths["reservedNewName"],
        )]},
        code="permission_denied", details_reason="preflightTargetRefused",
        reasons=[(0, "reservedProvider", paths["reservedTarget"])],
        raw=raw, key="reservedProviderRefusal",
    )
    facts["tagRenameReservedProviderReason"] = "reservedProvider"
    probe_after = read_tag_value(client, config.write_probe_path).get("value")
    policy_after = read_tag_value(client, config.policy_path).get("value")
    facts["tagRenameReservedProviderValueUnchanged"] = probe_before == probe_after
    facts["tagRenamePolicyDocumentUnclobbered"] = policy_before == policy_after
    expect_refusal(
        client, "tag_rename", {"items": [item(
            path=paths["udtTarget"], new_name=paths["udtNewName"],
        )]},
        code="permission_denied", details_reason="preflightTargetRefused",
        reasons=[(0, "udtDefinitionNotAllowlisted",
                  f"[{policy_document.TAG_FIXTURE_PROVIDER}]{policy_document.UDT_NAMESPACE}/"
                  f"{policy_document.TAG_FIXTURE_ROOT}/{paths['udtNewName']}")],
        raw=raw, key="udtUnderWildcard", path_key="refusedPath",
    )
    facts["tagRenameBareWildcardDoesNotCoverUdt"] = True

    # Case 7: the explicit `_types_` entry is honoured, measured the same way a
    # create and a copy measure theirs: the definition item passes the allowlist and
    # the batch is refused for its sibling alone.
    install_and_require(
        config, client, facts, "rename", "RenameTypes",
        allowlist=policy_document.TAG_TYPES_ALLOWLIST, raw=raw,
    )
    expect_refusal(
        client, "tag_rename", {"items": [
            item(path=paths["udtTarget"], new_name=paths["udtNewName"]),
            item(path=paths["siblingTarget"], new_name=paths["siblingNewName"]),
        ]},
        code="permission_denied", details_reason="preflightTargetRefused",
        reasons=[(1, "targetNotAllowlisted", paths["siblingNewPath"])],
        raw=raw, key="udtUnderTypesEntry", path_key="refusedPath",
    )
    facts["tagRenameTypesEntryIsHonoured"] = True
    facts["tagRenamePreflightExecutedNothing"] = (
        exported_at(config, paths["udtNewPath"]) is None
        and exported_at(config, paths["siblingNewPath"]) is None
    )
    if not facts["tagRenamePreflightExecutedNothing"]:
        raise StageFailure("a refused rename Preflight executed part of its batch")
    return {
        "stage": "tag-rename",
        "ok": True,
        "identity": identity(config),
        "guard": config.guard,
        "facts": facts,
        "raw": raw,
    }


def stage_tag_delete(config: Config) -> dict[str, Any]:
    """The ticket #12 `tag_delete` live cases: the delete, the partial batch, the refusals."""
    raw: dict[str, Any] = {}
    facts: dict[str, Any] = {}
    paths = tag_delete_paths()
    client = mcp_client.McpClient(config.configurator_url, config.api_token)
    raw["initialize"] = bounded(client.initialize())
    zero = "tcf1:" + "0" * 64

    def item(path: str = "", fingerprint: str = "") -> dict[str, str]:
        return {"path": path or paths["target"], "expectedFingerprint": fingerprint or zero}

    def delete_fingerprint(path: str) -> str:
        return tag_config(client, path)["fingerprint"]

    # Case 0: the D10 ceilings a request crosses with no native call.
    expect_refusal(
        client, "tag_delete", {"items": [item()] * (HARD_ITEM_CEILING + 1)},
        code="limit_exceeded", details_reason="itemsOverHardLimit", reasons=[],
        raw=raw, key="itemsOverHardCeiling",
    )
    facts["tagDeleteHardItemCeilingReason"] = "itemsOverHardLimit"
    expect_refusal(
        client, "tag_delete", {"items": [item(
            path=f"[{policy_document.TAG_FIXTURE_PROVIDER}]{policy_document.TAG_FIXTURE_ROOT}/"
                 + policy_document.OVERLONG_PATH_LEAF,
        )]},
        code="limit_exceeded", details_reason="pathOverLength", reasons=[],
        raw=raw, key="pathOverCeiling",
    )
    facts["tagDeletePathOverCeilingReason"] = "pathOverLength"
    expect_refusal(
        client, "tag_delete", {"items": [{"expectedFingerprint": zero}]},
        code="invalid_argument", details_reason="preflightInputFailed",
        reasons=[(0, "itemKeysMustBePathAndFingerprint", "")],
        raw=raw, key="itemKeys",
    )
    facts["tagDeleteItemKeysReason"] = "itemKeysMustBePathAndFingerprint"

    install_and_require(
        config, client, facts, "delete", "Delete",
        allowlist=policy_document.TAG_DELETE_ALLOWLIST, raw=raw,
    )

    # Case 1: a partial-failure batch after Preflight. Both items pass every
    # Preflight rule — the folder and a Tag inside it both exist and both
    # fingerprints match — and then the first item's `system.tag.deleteTags` takes
    # the folder with everything beneath it, so the second item's own call answers a
    # Bad QualityCode. D30 3: no rollback, per-item outcomes, and the batch is not
    # retried.
    folder_before = tag_config(client, paths["folder"])
    structured = expect_structured(client, "tag_delete", {"items": [
        item(path=paths["folder"], fingerprint=folder_before["fingerprint"]),
        item(path=paths["folderChild"], fingerprint=delete_fingerprint(paths["folderChild"])),
    ]})
    raw["partialBatch"] = bounded(structured, 40_000)
    items = structured.get("items") or [{}, {}]
    summary = structured.get("summary") or {}
    observed = structured.get("observed") or [{}]
    facts["tagDeletePartialBatchStatuses"] = [str(entry.get("status", "")) for entry in items]
    facts["tagDeletePartialBatchFirstOutcome"] = str(
        (items[0].get("nativeOutcome") or {}).get("name", "")
    )
    facts["tagDeletePartialBatchSecondOutcome"] = str(
        (items[1].get("nativeOutcome") or {}).get("name", "")
    )
    facts["tagDeletePartialBatchSucceeded"] = summary.get("succeeded")
    facts["tagDeletePartialBatchFailed"] = summary.get("failed")
    facts["tagDeletePartialBatchRetriedNothing"] = summary.get("outcomeUnknown") == 0
    facts["tagDeletePartialBatchObservedAbsent"] = len(observed) == 2 and all(
        entry.get("status") == "ok" and entry.get("absent") is True for entry in observed
    )
    facts["tagDeleteFolderExportGone"] = (
        exported_at(config, paths["folder"]) is None
    )
    if not (
        facts["tagDeletePartialBatchStatuses"] == ["executed", "executed"]
        and facts["tagDeletePartialBatchFirstOutcome"].startswith("Good")
        and facts["tagDeletePartialBatchSucceeded"] == 1
        and facts["tagDeletePartialBatchFailed"] == 1
        and facts["tagDeletePartialBatchObservedAbsent"]
        and facts["tagDeleteFolderExportGone"]
    ):
        raise StageFailure(
            f"the partial-failure delete batch is not a partial batch: {json.dumps(structured)[:800]}"
        )

    # Case 2: the positive delete, confirmed by the provider's own export at that
    # exact path — a Configuration read cannot answer absence (ticket #10 evidence).
    target_before = tag_config(client, paths["target"])
    structured = expect_structured(client, "tag_delete", {"items": [item(
        fingerprint=target_before["fingerprint"],
    )]})
    raw["allowlistedDelete"] = bounded(structured, 40_000)
    items = structured.get("items") or [{}]
    observed = structured.get("observed") or [{}]
    summary = structured.get("summary") or {}
    facts["tagDeleteStatus"] = str(items[0].get("status", ""))
    facts["tagDeleteNativeOutcome"] = str((items[0].get("nativeOutcome") or {}).get("name", ""))
    facts["tagDeleteAuditMode"] = str(summary.get("auditMode", ""))
    facts["tagDeleteAuditRecorded"] = summary.get("auditRecorded")
    facts["tagDeleteObservedAbsent"] = (
        len(observed) == 1
        and str(observed[0].get("path", "")) == paths["target"]
        and observed[0].get("status") == "ok"
        and observed[0].get("absent") is True
    )
    facts["tagDeleteExportGone"] = exported_at(config, paths["target"]) is None
    if not (
        facts["tagDeleteStatus"] == "executed"
        and facts["tagDeleteNativeOutcome"].startswith("Good")
        and facts["tagDeleteObservedAbsent"]
        and facts["tagDeleteExportGone"]
    ):
        raise StageFailure(f"the allowlisted delete did not land: {json.dumps(structured)[:800]}")

    correlation = str((structured.get("meta") or {}).get("correlationId", ""))
    audit_status, rows = audited_rows(config, correlation, "ignition-mcp.tag_delete")
    raw["auditQuery"] = {"status": audit_status, "rows": bounded(rows, 20_000)}
    facts["tagDeleteAuditRowsForCorrelation"] = len(rows)
    facts["tagDeleteAuditActorIsServiceIdentity"] = bool(rows) and all(
        str(row.get("actor", "")) == policy_document.SERVICE_IDENTITY for row in rows
    )
    if len(rows) < 2:
        raise StageFailure(f"the delete wrote no audit pair for {correlation}: {audit_status}")

    # Case 3: the Precondition token. A stale fingerprint is conflict and a target
    # that is not there is not_found; neither dispatches anything, and the target the
    # stale token named is still there.
    stale_before = tag_config(client, paths["staleTarget"])
    expect_refusal(
        client, "tag_delete", {"items": [item(path=paths["staleTarget"])]},
        code="conflict", details_reason="preflightPreconditionFailed",
        reasons=[(0, "fingerprintMismatch", paths["staleTarget"])],
        raw=raw, key="staleFingerprint",
    )
    facts["tagDeleteStaleFingerprintCode"] = "conflict"
    facts["tagDeleteStaleFingerprintReason"] = "fingerprintMismatch"
    stale_after = tag_config(client, paths["staleTarget"])
    facts["tagDeleteStaleFingerprintChangedNothing"] = (
        stale_after["fingerprint"] == stale_before["fingerprint"]
    )
    if not facts["tagDeleteStaleFingerprintChangedNothing"]:
        raise StageFailure("a refused delete removed the target it was refused for")
    expect_refusal(
        client, "tag_delete", {"items": [item(path=paths["missingTarget"])]},
        code="not_found", details_reason="preflightPreconditionFailed",
        reasons=[(0, "targetMissing", paths["missingTarget"])],
        raw=raw, key="missingTarget",
    )
    facts["tagDeleteMissingTargetCode"] = "not_found"
    facts["tagDeleteMissingTargetReason"] = "targetMissing"

    # Case 4: the segment-boundary sibling and a UDT definition without an explicit
    # `_types_` entry, each refused before anything is read.
    expect_refusal(
        client, "tag_delete", {"items": [item(path=paths["siblingTarget"])]},
        code="permission_denied", details_reason="preflightTargetRefused",
        reasons=[(0, "targetNotAllowlisted", paths["siblingTarget"])],
        raw=raw, key="siblingDenial",
    )
    facts["tagDeleteSiblingDenialReason"] = "targetNotAllowlisted"
    expect_refusal(
        client, "tag_delete", {"items": [item(path=paths["udtTarget"])]},
        code="permission_denied", details_reason="preflightTargetRefused",
        reasons=[(0, "udtDefinitionNotAllowlisted", paths["udtTarget"])],
        raw=raw, key="udtUnderPlainPrefix",
    )
    facts["tagDeleteUdtNeedsExplicitTypesEntry"] = True

    # Case 5: D10's deployment ceiling, and the whole-batch refusal that keeps the
    # allowed item of a batch from being deleted.
    expect_refusal(
        client, "tag_delete", {"items": [item()] * 21},
        code="limit_exceeded", details_reason="itemsOverPolicyLimit", reasons=[],
        raw=raw, key="overPolicyLimit",
    )
    facts["tagDeleteOverPolicyLimitReason"] = "itemsOverPolicyLimit"
    install_and_require(
        config, client, facts, "delete", "DeleteCeiling",
        allowlist=policy_document.TAG_DELETE_ALLOWLIST, raw=raw, max_items=1,
    )
    expect_refusal(
        client, "tag_delete", {"items": [item(), item(path=paths["staleTarget"])]},
        code="limit_exceeded", details_reason="itemsOverPolicyLimit", reasons=[],
        raw=raw, key="overPolicyCeiling",
    )
    facts["tagDeletePolicyCeilingIsHonoured"] = True

    # Case 6: the reserved provider is refused under an explicit `*`, before any
    # read, and neither the probe Tag nor the policy document moves.
    install_and_require(
        config, client, facts, "delete", "DeleteWildcard",
        allowlist=policy_document.WILDCARD_ALLOWLIST, raw=raw,
    )
    probe_before = read_tag_value(client, config.write_probe_path).get("value")
    policy_before = read_tag_value(client, config.policy_path).get("value")
    expect_refusal(
        client, "tag_delete", {"items": [item(path=paths["reservedTarget"])]},
        code="permission_denied", details_reason="preflightTargetRefused",
        reasons=[(0, "reservedProvider", paths["reservedTarget"])],
        raw=raw, key="reservedProviderRefusal",
    )
    facts["tagDeleteReservedProviderReason"] = "reservedProvider"
    probe_after = read_tag_value(client, config.write_probe_path).get("value")
    policy_after = read_tag_value(client, config.policy_path).get("value")
    facts["tagDeleteReservedProviderValueUnchanged"] = probe_before == probe_after
    facts["tagDeletePolicyDocumentUnclobbered"] = policy_before == policy_after
    expect_refusal(
        client, "tag_delete", {"items": [item(path=paths["udtTarget"])]},
        code="permission_denied", details_reason="preflightTargetRefused",
        reasons=[(0, "udtDefinitionNotAllowlisted", paths["udtTarget"])],
        raw=raw, key="udtUnderWildcard",
    )
    facts["tagDeleteBareWildcardDoesNotCoverUdt"] = True

    # Case 7: the explicit `_types_` entry is honoured: the definition item passes
    # the allowlist and the batch is refused for its sibling alone.
    install_and_require(
        config, client, facts, "delete", "DeleteTypes",
        allowlist=policy_document.TAG_TYPES_ALLOWLIST, raw=raw,
    )
    expect_refusal(
        client, "tag_delete", {"items": [
            item(path=paths["udtTarget"]), item(path=paths["siblingTarget"]),
        ]},
        code="permission_denied", details_reason="preflightTargetRefused",
        reasons=[(1, "targetNotAllowlisted", paths["siblingTarget"])],
        raw=raw, key="udtUnderTypesEntry",
    )
    facts["tagDeleteTypesEntryIsHonoured"] = True
    facts["tagDeletePreflightExecutedNothing"] = (
        exported_at(config, paths["staleTarget"]) is not None
    )
    if not facts["tagDeletePreflightExecutedNothing"]:
        raise StageFailure("a refused delete Preflight executed part of its batch")
    return {
        "stage": "tag-delete",
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
                    "auditRecorded": facts.get("tagUpdateStaleFingerprintAuditRecorded"),
                },
                "inputBounds": {
                    "overPolicyLimitCode": facts.get("tagUpdateOverPolicyLimitCode"),
                    "overPolicyLimitReason": facts.get("tagUpdateOverPolicyLimitReason"),
                },
                "deniedMutationAudit": {
                    "siblingDenialRecorded": facts.get("tagUpdateSiblingDenialAuditRecorded"),
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
                    "definitionReadIsAllowed": facts.get("tagUpdateUdtDefinitionReadIsAllowed"),
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
            # Ticket #11: the two CONFIG Mutations that write a node rather than merge
            # into one. Each reports its own dispatch, collision, allowlist, ceiling and
            # reserved-provider answers, because the rules they fix are different: a
            # create has no Precondition token and an occupied target is its collision,
            # and a copy measures only its destination against the allowlist while the
            # reserved provider bounds both of its ends.
            "runtimeTagConfigMutations": {
                "tagCreate": {
                    "configuratorCarriesBothTools": facts.get(
                        "tagCreateConfiguratorCarriesBothTools"
                    ),
                    "operatorExcludesBothTools": facts.get(
                        "tagCreateOperatorInventoryExcludesBothTools"
                    ),
                    "allowlisted": {
                        "status": facts.get("tagCreateStatus"),
                        "nativeOutcome": facts.get("tagCreateNativeOutcome"),
                        "independentReadShowsTheNode": facts.get(
                            "tagCreateIndependentReadShowsTheNode"
                        ),
                        "observedIsIndependentRead": facts.get(
                            "tagCreateObservedFingerprintIsIndependentRead"
                        ),
                        "visibleInExport": facts.get("tagCreateNodeVisibleInExport"),
                    },
                    "existingTarget": {
                        "code": facts.get("tagCreateCollisionCode"),
                        "reason": facts.get("tagCreateCollisionReason"),
                        "changedNothing": facts.get("tagCreateCollisionChangedNothing"),
                        "auditRecorded": facts.get("tagCreateCollisionAuditRecorded"),
                    },
                    "targetAllowlist": {
                        "siblingRefusedAtSegmentBoundary": facts.get(
                            "tagCreateSiblingDenialIsSegmentBoundary"
                        ),
                        "preflightRefusalCode": facts.get("tagCreatePreflightRefusalCode"),
                        "preflightExecutedNothing": facts.get("tagCreatePreflightExecutedNothing"),
                    },
                    "udtDefinitions": {
                        "refusedUnderPlainPrefix": facts.get("tagCreateUdtNeedsExplicitTypesEntry"),
                        "refusedUnderBareWildcard": facts.get("tagCreateBareWildcardDoesNotCoverUdt"),
                        "explicitTypesEntryHonoured": facts.get("tagCreateTypesEntryIsHonoured"),
                        "preflightExecutedNothing": facts.get(
                            "tagCreateTypesPreflightExecutedNothing"
                        ),
                    },
                    "reservedProvider": {
                        "refusedUnderExplicitWildcard": facts.get(
                            "tagCreateReservedProviderRefusedUnderWildcard"
                        ),
                        "targetValueUnchanged": facts.get("tagCreateReservedProviderValueUnchanged"),
                        "policyDocumentUnclobbered": facts.get(
                            "tagCreatePolicyDocumentUnclobbered"
                        ),
                    },
                    "inputBounds": {
                        "pathOverCeiling": facts.get("tagCreatePathOverCeilingReason"),
                        "hardItemCeiling": facts.get("tagCreateHardItemCeilingReason"),
                        "overPolicyLimit": facts.get("tagCreateOverPolicyLimitReason"),
                        "deploymentCeilingHonoured": facts.get(
                            "tagCreatePolicyCeilingIsHonoured"
                        ),
                    },
                    "audit": {
                        "mode": facts.get("tagCreateAuditMode"),
                        "recorded": facts.get("tagCreateAuditRecorded"),
                        "rowsForCorrelation": facts.get("tagCreateAuditRowsForCorrelation"),
                        "actorIsServiceIdentity": facts.get("tagCreateAuditActorIsServiceIdentity"),
                    },
                },
                "tagCopy": {
                    "allowlisted": {
                        "status": facts.get("tagCopyStatus"),
                        "nativeOutcome": facts.get("tagCopyNativeOutcome"),
                        "independentReadShowsTheCopy": facts.get(
                            "tagCopyIndependentReadShowsTheCopy"
                        ),
                        "observedIsIndependentRead": facts.get(
                            "tagCopyObservedFingerprintIsIndependentRead"
                        ),
                        "sourceUnchanged": facts.get("tagCopySourceUnchanged"),
                    },
                    "occupiedDestination": {
                        "code": facts.get("tagCopyOccupiedDestinationCode"),
                        "reason": facts.get("tagCopyOccupiedDestinationReason"),
                        "changedNothing": facts.get("tagCopyOccupiedDestinationChangedNothing"),
                        "auditRecorded": facts.get("tagCopyOccupiedDestinationAuditRecorded"),
                    },
                    "destinationLeafRule": {
                        "code": facts.get("tagCopyLeafMismatchCode"),
                        "reason": facts.get("tagCopyLeafMismatchReason"),
                    },
                    "source": {
                        "missingCode": facts.get("tagCopySourceMissingCode"),
                        "missingReason": facts.get("tagCopySourceMissingReason"),
                        "exemptFromAllowlist": facts.get("tagCopySourceIsExemptFromAllowlist"),
                    },
                    "targetAllowlist": {
                        "siblingRefusedAtSegmentBoundary": facts.get(
                            "tagCopySiblingDenialIsSegmentBoundary"
                        ),
                        "preflightExecutedNothing": facts.get("tagCopyPreflightExecutedNothing"),
                    },
                    "udtDefinitions": {
                        "refusedUnderPlainPrefix": facts.get("tagCopyUdtNeedsExplicitTypesEntry"),
                        "refusedUnderBareWildcard": facts.get("tagCopyBareWildcardDoesNotCoverUdt"),
                        "explicitTypesEntryHonoured": facts.get("tagCopyTypesEntryIsHonoured"),
                    },
                    "reservedProvider": {
                        "sourceRefusedUnderExplicitWildcard": facts.get(
                            "tagCopyReservedSourceRefusedUnderWildcard"
                        ),
                        "destinationRefusedUnderExplicitWildcard": facts.get(
                            "tagCopyReservedDestinationRefusedUnderWildcard"
                        ),
                        "targetValueUnchanged": facts.get("tagCopyReservedProviderValueUnchanged"),
                        "policyDocumentUnclobbered": facts.get(
                            "tagCopyPolicyDocumentUnclobbered"
                        ),
                    },
                    "inputBounds": {
                        "hardItemCeiling": facts.get("tagCopyHardItemCeilingReason"),
                        "overPolicyLimit": facts.get("tagCopyOverPolicyLimitReason"),
                        "deploymentCeilingHonoured": facts.get("tagCopyPolicyCeilingIsHonoured"),
                    },
                    "audit": {
                        "mode": facts.get("tagCopyAuditMode"),
                        "recorded": facts.get("tagCopyAuditRecorded"),
                        "rowsForCorrelation": facts.get("tagCopyAuditRowsForCorrelation"),
                        "actorIsServiceIdentity": facts.get("tagCopyAuditActorIsServiceIdentity"),
                    },
                },
                # Ticket #12: the two Mutations that relocate a node and the one that
                # removes it. Each reports its own dispatch, collision, allowlist,
                # ceiling and reserved-provider answers, because the rules differ: a
                # move checks both ends, a rename checks the new path its own parent
                # plus the new name makes, and a delete has no collision policy at all.
                "tagMove": {
                    "configuratorCarriesAllThree": facts.get(
                        "tagMoveConfiguratorCarriesAllThree"
                    ),
                    "allowlisted": {
                        "status": facts.get("tagMoveStatus"),
                        "nativeOutcome": facts.get("tagMoveNativeOutcome"),
                        "observedDestinationPresent": facts.get(
                            "tagMoveObservedDestinationPresent"
                        ),
                        "observedDestinationMatchesIndependentRead": facts.get(
                            "tagMoveObservedDestinationMatchesIndependentRead"
                        ),
                        "sourceObservedAbsent": facts.get("tagMoveSourceObservedAbsent"),
                        "exportShowsDestination": facts.get("tagMoveExportShowsDestination"),
                        "exportSourceGone": facts.get("tagMoveExportSourceGone"),
                    },
                    "destinationLeafRule": {
                        "code": facts.get("tagMoveLeafMismatchCode"),
                        "reason": facts.get("tagMoveLeafMismatchReason"),
                    },
                    "occupiedDestination": {
                        "code": facts.get("tagMoveOccupiedDestinationCode"),
                        "reason": facts.get("tagMoveOccupiedDestinationReason"),
                        "changedNothing": facts.get("tagMoveOccupiedDestinationChangedNothing"),
                        "leftTheSource": facts.get("tagMoveOccupiedDestinationLeftTheSource"),
                    },
                    "preconditionToken": {
                        "staleCode": facts.get("tagMoveStaleFingerprintCode"),
                        "staleReason": facts.get("tagMoveStaleFingerprintReason"),
                        "missingSourceCode": facts.get("tagMoveMissingSourceCode"),
                        "missingSourceReason": facts.get("tagMoveMissingSourceReason"),
                    },
                    "targetAllowlist": {
                        "siblingDenialReason": facts.get("tagMoveSiblingDenialReason"),
                        "preflightExecutedNothing": facts.get("tagMovePreflightExecutedNothing"),
                    },
                    "udtDefinitions": {
                        "refusedUnderPlainPrefix": facts.get("tagMoveUdtNeedsExplicitTypesEntry"),
                        "refusedUnderBareWildcard": facts.get("tagMoveBareWildcardDoesNotCoverUdt"),
                        "explicitTypesEntryHonoured": facts.get("tagMoveTypesEntryIsHonoured"),
                    },
                    "reservedProvider": {
                        "sourceReason": facts.get("tagMoveReservedSourceReason"),
                        "destinationReason": facts.get("tagMoveReservedDestinationReason"),
                        "targetValueUnchanged": facts.get("tagMoveReservedProviderValueUnchanged"),
                        "policyDocumentUnclobbered": facts.get(
                            "tagMovePolicyDocumentUnclobbered"
                        ),
                    },
                    "inputBounds": {
                        "hardItemCeiling": facts.get("tagMoveHardItemCeilingReason"),
                        "pathOverCeiling": facts.get("tagMovePathOverCeilingReason"),
                        "overPolicyLimit": facts.get("tagMoveOverPolicyLimitReason"),
                        "deploymentCeilingHonoured": facts.get("tagMovePolicyCeilingIsHonoured"),
                    },
                    "audit": {
                        "mode": facts.get("tagMoveAuditMode"),
                        "recorded": facts.get("tagMoveAuditRecorded"),
                        "rowsForCorrelation": facts.get("tagMoveAuditRowsForCorrelation"),
                        "actorIsServiceIdentity": facts.get("tagMoveAuditActorIsServiceIdentity"),
                    },
                },
                "tagRename": {
                    "allowlisted": {
                        "status": facts.get("tagRenameStatus"),
                        "nativeOutcome": facts.get("tagRenameNativeOutcome"),
                        "itemCarriesBothPaths": facts.get("tagRenameItemCarriesBothPaths"),
                        "observedNewPathPresent": facts.get("tagRenameObservedNewPathPresent"),
                        "observedNewPathMatchesIndependentRead": facts.get(
                            "tagRenameObservedNewPathMatchesIndependentRead"
                        ),
                        "oldPathObservedAbsent": facts.get("tagRenameOldPathObservedAbsent"),
                        "exportShowsNewPath": facts.get("tagRenameExportShowsNewPath"),
                        "exportOldPathGone": facts.get("tagRenameExportOldPathGone"),
                    },
                    "newNameRule": {
                        "code": facts.get("tagRenameMultiSegmentNameCode"),
                        "reason": facts.get("tagRenameMultiSegmentNameReason"),
                    },
                    "occupiedNewPath": {
                        "code": facts.get("tagRenameOccupiedNewPathCode"),
                        "reason": facts.get("tagRenameOccupiedNewPathReason"),
                        "changedNothing": facts.get("tagRenameOccupiedNewPathChangedNothing"),
                        "leftTheTarget": facts.get("tagRenameOccupiedNewPathLeftTheTarget"),
                    },
                    "preconditionToken": {
                        "staleCode": facts.get("tagRenameStaleFingerprintCode"),
                        "staleReason": facts.get("tagRenameStaleFingerprintReason"),
                        "missingTargetCode": facts.get("tagRenameMissingTargetCode"),
                        "missingTargetReason": facts.get("tagRenameMissingTargetReason"),
                    },
                    "targetAllowlist": {
                        "siblingDenialReason": facts.get("tagRenameSiblingDenialReason"),
                        "preflightExecutedNothing": facts.get(
                            "tagRenamePreflightExecutedNothing"
                        ),
                    },
                    "udtDefinitions": {
                        "refusedUnderPlainPrefix": facts.get(
                            "tagRenameUdtNeedsExplicitTypesEntry"
                        ),
                        "refusedUnderBareWildcard": facts.get(
                            "tagRenameBareWildcardDoesNotCoverUdt"
                        ),
                        "explicitTypesEntryHonoured": facts.get("tagRenameTypesEntryIsHonoured"),
                    },
                    "reservedProvider": {
                        "reason": facts.get("tagRenameReservedProviderReason"),
                        "targetValueUnchanged": facts.get(
                            "tagRenameReservedProviderValueUnchanged"
                        ),
                        "policyDocumentUnclobbered": facts.get(
                            "tagRenamePolicyDocumentUnclobbered"
                        ),
                    },
                    "inputBounds": {
                        "hardItemCeiling": facts.get("tagRenameHardItemCeilingReason"),
                        "pathOverCeiling": facts.get("tagRenamePathOverCeilingReason"),
                        "overPolicyLimit": facts.get("tagRenameOverPolicyLimitReason"),
                        "deploymentCeilingHonoured": facts.get("tagRenamePolicyCeilingIsHonoured"),
                    },
                    "audit": {
                        "mode": facts.get("tagRenameAuditMode"),
                        "recorded": facts.get("tagRenameAuditRecorded"),
                        "rowsForCorrelation": facts.get("tagRenameAuditRowsForCorrelation"),
                        "actorIsServiceIdentity": facts.get(
                            "tagRenameAuditActorIsServiceIdentity"
                        ),
                    },
                },
                "tagDelete": {
                    "allowlisted": {
                        "status": facts.get("tagDeleteStatus"),
                        "nativeOutcome": facts.get("tagDeleteNativeOutcome"),
                        "observedAbsent": facts.get("tagDeleteObservedAbsent"),
                        "exportGone": facts.get("tagDeleteExportGone"),
                    },
                    "partialFailureBatch": {
                        "statuses": facts.get("tagDeletePartialBatchStatuses"),
                        "firstOutcome": facts.get("tagDeletePartialBatchFirstOutcome"),
                        "secondOutcome": facts.get("tagDeletePartialBatchSecondOutcome"),
                        "succeeded": facts.get("tagDeletePartialBatchSucceeded"),
                        "failed": facts.get("tagDeletePartialBatchFailed"),
                        "retriedNothing": facts.get("tagDeletePartialBatchRetriedNothing"),
                        "observedAbsent": facts.get("tagDeletePartialBatchObservedAbsent"),
                        "folderExportGone": facts.get("tagDeleteFolderExportGone"),
                    },
                    "preconditionToken": {
                        "staleCode": facts.get("tagDeleteStaleFingerprintCode"),
                        "staleReason": facts.get("tagDeleteStaleFingerprintReason"),
                        "staleChangedNothing": facts.get(
                            "tagDeleteStaleFingerprintChangedNothing"
                        ),
                        "missingTargetCode": facts.get("tagDeleteMissingTargetCode"),
                        "missingTargetReason": facts.get("tagDeleteMissingTargetReason"),
                    },
                    "inputRules": {
                        "itemKeysReason": facts.get("tagDeleteItemKeysReason"),
                    },
                    "targetAllowlist": {
                        "siblingDenialReason": facts.get("tagDeleteSiblingDenialReason"),
                        "preflightExecutedNothing": facts.get(
                            "tagDeletePreflightExecutedNothing"
                        ),
                    },
                    "udtDefinitions": {
                        "refusedUnderPlainPrefix": facts.get(
                            "tagDeleteUdtNeedsExplicitTypesEntry"
                        ),
                        "refusedUnderBareWildcard": facts.get(
                            "tagDeleteBareWildcardDoesNotCoverUdt"
                        ),
                        "explicitTypesEntryHonoured": facts.get("tagDeleteTypesEntryIsHonoured"),
                    },
                    "reservedProvider": {
                        "reason": facts.get("tagDeleteReservedProviderReason"),
                        "targetValueUnchanged": facts.get(
                            "tagDeleteReservedProviderValueUnchanged"
                        ),
                        "policyDocumentUnclobbered": facts.get(
                            "tagDeletePolicyDocumentUnclobbered"
                        ),
                    },
                    "inputBounds": {
                        "hardItemCeiling": facts.get("tagDeleteHardItemCeilingReason"),
                        "pathOverCeiling": facts.get("tagDeletePathOverCeilingReason"),
                        "overPolicyLimit": facts.get("tagDeleteOverPolicyLimitReason"),
                        "deploymentCeilingHonoured": facts.get("tagDeletePolicyCeilingIsHonoured"),
                    },
                    "audit": {
                        "mode": facts.get("tagDeleteAuditMode"),
                        "recorded": facts.get("tagDeleteAuditRecorded"),
                        "rowsForCorrelation": facts.get("tagDeleteAuditRowsForCorrelation"),
                        "actorIsServiceIdentity": facts.get(
                            "tagDeleteAuditActorIsServiceIdentity"
                        ),
                    },
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


EVIDENCE_TICKETS = {MILESTONE_4A: ["#6", "#7", "#8"], MILESTONE_4B: ["#10", "#11", "#12"]}
EVIDENCE_TITLES = {
    MILESTONE_4A: (
        "Characterize the Runtime Target Policy and the exact-path alarm query bound, "
        "and verify tag_write, alarm_shelve and alarm_unshelve live"
    ),
    MILESTONE_4B: (
        "Verify the Tag config fingerprint, tag_update, tag_create, tag_copy, tag_move, "
        "tag_rename and tag_delete (milestone 4b) live"
    ),
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
            "tag-update-no-policy", "tag-update-setup", "tag-update",
            "tag-create", "tag-copy", "tag-move", "tag-rename", "tag-delete", "summarize",
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
        elif stage == "tag-create":
            record = stage_tag_create(config)
            write_stage(config, "tag-create", record)
            code = EXIT_OK
        elif stage == "tag-copy":
            record = stage_tag_copy(config)
            write_stage(config, "tag-copy", record)
            code = EXIT_OK
        elif stage == "tag-move":
            record = stage_tag_move(config)
            write_stage(config, "tag-move", record)
            code = EXIT_OK
        elif stage == "tag-rename":
            record = stage_tag_rename(config)
            write_stage(config, "tag-rename", record)
            code = EXIT_OK
        elif stage == "tag-delete":
            record = stage_tag_delete(config)
            write_stage(config, "tag-delete", record)
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
