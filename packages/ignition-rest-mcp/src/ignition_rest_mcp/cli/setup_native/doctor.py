"""``setup-native doctor``: ordered read-only diagnosis of a Runtime Bundle deployment.

Every check yields ``{name, status, detail}`` with status in
``PASS | FAIL | SKIP | NOT_APPLICABLE | UNKNOWN``.  The order is fixed, the
compatibility mapping is deterministic (D21), and nothing here can mutate a
Gateway: capabilities are questions asked of the OpenAPI path inventory.
"""

from __future__ import annotations

import ipaddress
import json
from dataclasses import dataclass, field
from typing import Any, Sequence

import httpx

from ignition_rest_mcp.cli.setup_native import gateway as gw
from ignition_rest_mcp.cli.setup_native.inputs import Inputs, SetupInputs, UsageError
from ignition_rest_mcp.cli.setup_native.writer import GatewayWriter
from ignition_rest_mcp.cli.setup_native.mcp_http import McpHttpClient, McpMethodNotFound, McpProbeError

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"
NOT_APPLICABLE = "NOT_APPLICABLE"
UNKNOWN = "UNKNOWN"

BUNDLE_INFO_TOOL = "bundle_info"
#: Identity fields compared against ``manifest.testedTuples`` rows.  The evidence
#: ``gate`` and ``mcpModuleSha256`` are not observable over these APIs, so a row
#: matches on the five fields the Gateway and the bundle actually report.
TUPLE_FIELDS = ("gatewayVersion", "gatewayBuild", "mcpModuleVersion", "mcpModuleBuild", "bundleVersion")
#: Statuses a compatibility row may report that still count as a healthy finding.
_COMPAT_PASS = frozenset({"SUPPORTED"})
_COMPAT_FAIL = frozenset({"INCOMPATIBLE"})

NETWORK_SKIP_FIELDS = (
    "openapi-sha256",
    "module-installed",
    "bundle-project",
    "server-config-presence",
)


@dataclass(frozen=True, slots=True)
class Check:
    """One named diagnostic outcome."""

    name: str
    status: str
    detail: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"name": self.name, "status": self.status, "detail": self.detail}


@dataclass(slots=True)
class GatewayObservation:
    """What the Gateway plane revealed, shared by doctor, plan and verify."""

    reachable: bool = False
    gateway_version: str | None = None
    gateway_build: str | None = None
    gateway_name: str = ""
    openapi_sha256: str | None = None
    endpoints: frozenset[tuple[str, str]] = frozenset()
    module: gw.ModuleIdentity | None = None
    module_error: str = ""
    project: gw.ProjectState | None = None
    server_config_exists: bool | None = None
    server_config: dict[str, Any] | None = None
    server_config_error: str = ""
    error: str = ""

    def capability(self, name: str) -> bool:
        return gw.CAPABILITY_ENDPOINTS[name] in self.endpoints

    @property
    def project_probeable(self) -> bool:
        return gw.PROJECT_FIND_ENDPOINT in self.endpoints

    @property
    def identity(self) -> dict[str, str | None]:
        return {
            "gatewayVersion": self.gateway_version,
            "gatewayBuild": self.gateway_build,
            "mcpModuleVersion": self.module.version if self.module else None,
            "mcpModuleBuild": self.module.build if self.module else None,
        }


@dataclass(slots=True)
class McpObservation:
    """What the Runtime MCP endpoint plane revealed."""

    reachable: bool = False
    initialized: bool = False
    tools: list[str] = field(default_factory=list)
    resources: list[str] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)
    bundle: dict[str, Any] | None = None


def make_gateway(inputs: SetupInputs, transport: httpx.AsyncBaseTransport | None = None) -> gw.GatewayRest:
    """Build the Gateway probe, refusing to carry a token over untls plain HTTP broadly."""

    refuse_untls_token(inputs)
    return gw.GatewayRest(inputs.gateway_url, inputs.gateway_token, timeout_seconds=inputs.timeout_seconds,
                          transport=transport)


def make_writer(inputs: SetupInputs, transport: httpx.AsyncBaseTransport | None = None) -> GatewayWriter:
    """Build ``install-module``'s writer under the same token-carrying rule (D20)."""

    refuse_untls_token(inputs)
    return GatewayWriter(inputs.gateway_url, inputs.gateway_token, timeout_seconds=inputs.timeout_seconds,
                         transport=transport)


def refuse_untls_token(inputs: SetupInputs) -> None:
    """Refuse to send the Gateway API token in the clear beyond the loopback (D07/D20)."""

    endpoint = inputs.gateway_url
    if endpoint.scheme == "http" and not inputs.allow_insecure_authorize and not _is_loopback(endpoint.host):
        raise UsageError(
            f"refusing to send the Gateway API token over plain HTTP to {endpoint.authority}: "
            "use https, or pass --allow-insecure-authorize for a trusted lab network"
        )


def make_mcp(inputs: Inputs, transport: httpx.AsyncBaseTransport | None = None) -> McpHttpClient:
    endpoint = inputs.runtime_endpoint()
    if endpoint is None:  # pragma: no cover - load_inputs requires one for doctor/verify
        raise UsageError("this command needs --mcp-url (or --server-config-name to derive it)")
    return McpHttpClient(
        endpoint=endpoint,
        token=inputs.mcp_token,
        timeout_seconds=inputs.timeout_seconds,
        transport=transport,
    )


def _is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


# --------------------------------------------------------------------------- inventory


def inventory_diff(expected: Sequence[str], actual: Sequence[str]) -> tuple[list[str], list[str]]:
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    return missing, extra


def inventory_check(name: str, expected: Sequence[str], actual: Sequence[str]) -> Check:
    """Exact inventory comparison: a superset and a subset both fail (D20)."""

    missing, extra = inventory_diff(expected, actual)
    if not missing and not extra:
        return Check(name, PASS, f"exact: {len(actual)} advertised, {len(expected)} expected")
    drift = []
    if missing:
        drift.append(f"missing=[{_names(missing)}]")
    if extra:
        drift.append(f"extra=[{_names(extra)}]")
    return Check(
        name,
        FAIL,
        f"expected {len(expected)}, endpoint advertised {len(actual)}: {'; '.join(drift)}",
    )


def _names(values: Sequence[str], limit: int = 8) -> str:
    listed = list(values)
    shown = ", ".join(listed[:limit])
    return shown + (f", ... (+{len(listed) - limit} more)" if len(listed) > limit else "")


# ---------------------------------------------------------------------- gateway probes


async def probe_gateway(client: gw.GatewayRest, inputs: Inputs) -> tuple[list[Check], GatewayObservation]:
    """Gateway-plane checks in report order: identity, OpenAPI, module, capabilities, projects."""

    checks: list[Check] = []
    observation = GatewayObservation()
    try:
        info = await client.gateway_info()
    except gw.GatewayProbeError as error:
        observation.error = str(error)
        checks.append(Check("gateway-info", FAIL, str(error)))
        unavailable = "Gateway did not answer /data/api/v1/gateway-info"
        checks.extend(Check(name, SKIP, unavailable) for name in NETWORK_SKIP_FIELDS)
        checks.extend(Check(f"capabilities.{name}", SKIP, unavailable) for name in gw.CAPABILITY_ORDER)
        return checks, observation
    observation.reachable = True
    raw_version = info.get("ignitionVersion")
    version, build = gw.parse_gateway_identity(raw_version)
    observation.gateway_version, observation.gateway_build = version, build
    name = info.get("name")
    observation.gateway_name = name if isinstance(name, str) else ""
    if not isinstance(raw_version, str) or not raw_version:
        checks.append(Check("gateway-info", FAIL, "gateway-info reported no ignitionVersion"))
    else:
        note = (
            ""
            if version is not None
            else "; version is not '<semver> (b<10-digit build>)', so compatibility stays UNKNOWN"
        )
        checks.append(
            Check("gateway-info", PASS, f"ignitionVersion={raw_version} gateway={observation.gateway_name or '?'}{note}")
        )

    try:
        digest, endpoints = await client.openapi()
    except gw.GatewayProbeError as error:
        checks.append(Check("openapi-sha256", FAIL, str(error)))
        observation.endpoints = frozenset()
    else:
        observation.endpoints = endpoints
        checks.append(
            Check(
                "openapi-sha256",
                PASS,
                f"{digest} ({len(endpoints)} documented operations, {count_capabilities(endpoints)} MCP operations)",
            )
        )

    checks.append(await _module_check(client, observation))

    for capability in gw.CAPABILITY_ORDER:
        if not observation.reachable or not observation.endpoints:
            checks.append(
                Check(f"capabilities.{capability}", SKIP, "OpenAPI inventory unavailable")
            )
            continue
        method, path = gw.CAPABILITY_ENDPOINTS[capability]
        if observation.capability(capability):
            checks.append(Check(f"capabilities.{capability}", PASS, f"{method} {path} documented"))
        else:
            checks.append(
                Check(
                    f"capabilities.{capability}",
                    NOT_APPLICABLE,
                    f"{method} {path} absent from /openapi.json (capability not offered)",
                )
            )

    checks.append(await _project_check(client, inputs, observation))
    checks.append(await _server_config_check(client, inputs, observation))
    return checks, observation


def count_capabilities(endpoints: frozenset[tuple[str, str]]) -> int:
    """Number of documented ``com.inductiveautomation.mcp`` operations."""

    return sum(1 for _, path in endpoints if gw.MCP_MODULE_ID in path)


async def _module_check(client: gw.GatewayRest, observation: GatewayObservation) -> Check:
    try:
        module = await client.mcp_module()
    except gw.GatewayProbeError as error:
        observation.module_error = str(error)
        return Check("module-installed", FAIL, str(error))
    if module is None:
        return Check(
            "module-installed",
            FAIL,
            f"{gw.MCP_MODULE_ID} is not installed and healthy (/data/api/v1/modules/healthy)",
        )
    observation.module = module
    identity = _module_identity_detail(module)
    return Check("module-installed", PASS, f"{gw.MCP_MODULE_ID} {identity} (healthy)")


def _module_identity_detail(module: gw.ModuleIdentity) -> str:
    version = module.version or "?"
    build = module.build or "?"
    return f"version={version} build={build} reported={module.raw_version}"


async def _project_check(client: gw.GatewayRest, inputs: Inputs, observation: GatewayObservation) -> Check:
    name = "bundle-project"
    if not observation.project_probeable:
        return Check(
            name,
            SKIP,
            f"Gateway does not document {gw.PROJECT_FIND_ENDPOINT[0]} "
            f"{gw.PROJECT_FIND_ENDPOINT[1]}; project state unknown",
        )
    try:
        state = await client.find_project(inputs.bundle_project)
    except gw.GatewayProbeError as error:
        return Check(name, UNKNOWN, f"{inputs.bundle_project}: {error}")
    observation.project = state
    standalone = _standalone_detail(state)
    if state.classification == gw.ABSENT:
        return Check(name, PASS, f"{state.name}: ABSENT (nothing deployed){standalone}")
    if state.classification == gw.MANAGED:
        aligned = "" if state.bundle_version == inputs.bundle_version else (
            f" != manifest {inputs.bundle_version}"
        )
        status = PASS if state.inheritable is not True else FAIL
        return Check(
            name,
            status,
            f"{state.name}: MANAGED bundle={state.bundle_version}{aligned}{standalone}",
        )
    if state.classification == gw.UNMANAGED_SAME_NAME:
        return Check(name, FAIL, f"{state.name}: UNMANAGED_SAME_NAME (no ownership marker){standalone}")
    return Check(name, FAIL, f"{state.name}: MARKER_INVALID (malformed or foreign ownership marker){standalone}")


def _json_snippet(value: object) -> str:
    """Compact JSON for a report line, bounded so a chatty server cannot bloat it."""

    return json.dumps(value, separators=(",", ":"), sort_keys=True)[:120]


def _standalone_detail(state: gw.ProjectState) -> str:
    if state.classification == gw.ABSENT:
        return ""
    if state.inheritable is True:
        return "; NOT standalone (inheritable=true; the bundle project must not be inherited)"
    if state.inheritable is False:
        return "; standalone (inheritable=false)"
    return "; inheritable flag not reported"


async def _server_config_check(
    client: gw.GatewayRest, inputs: Inputs, observation: GatewayObservation
) -> Check:
    name = "server-config-presence"
    if inputs.server_config_name is None:
        return Check(name, SKIP, "no --server-config-name supplied; presence not probed")
    if not observation.capability("server-config"):
        return Check(
            name,
            SKIP,
            "Gateway does not document the server-config find route; nothing to probe",
        )
    try:
        document = await client.server_config_document(inputs.server_config_name)
    except gw.GatewayProbeError as error:
        observation.server_config_error = str(error)
        return Check(name, UNKNOWN, f"server-config {inputs.server_config_name}: {error}")
    observation.server_config = document
    observation.server_config_exists = document is not None
    if document is not None:
        return Check(name, PASS, f"server-config {inputs.server_config_name} exists")
    return Check(
        name,
        FAIL,
        f"server-config {inputs.server_config_name} is absent; plan proposes CREATE",
    )


# -------------------------------------------------------------------------- mcp probes


async def probe_mcp(client: McpHttpClient, inputs: Inputs) -> tuple[list[Check], McpObservation]:
    """MCP-plane checks: initialize, exact inventories, ``bundle_info``."""

    observation = McpObservation()
    try:
        await client.initialize()
    except McpProbeError as error:
        checks = [Check("mcp-initialize", FAIL, str(error))]
        checks.extend(
            Check(name, SKIP, "MCP session unavailable")
            for name in ("inventory-tools", "inventory-resources", "inventory-prompts", "bundle-info")
        )
        return checks, observation
    observation.initialized = True
    observation.reachable = True
    server = _json_snippet(client.server_info)
    advertised = ", ".join(sorted(kind for kind in ("tools", "resources", "prompts") if client.advertises(kind)))
    checks = [
        Check(
            "mcp-initialize",
            PASS,
            f"protocol={client.protocol_version or '?'} server={server} capabilities=[{advertised or '-'}]",
        )
    ]
    checks.append(await _inventory_of(client, observation, inputs, "tools"))
    checks.append(await _inventory_of(client, observation, inputs, "resources"))
    checks.append(await _inventory_of(client, observation, inputs, "prompts"))
    bundle_check, bundle = await bundle_info_check(client, inputs)
    checks.append(bundle_check)
    observation.bundle = bundle
    return checks, observation


async def _inventory_of(
    client: McpHttpClient, observation: McpObservation, inputs: Inputs, kind: str
) -> Check:
    name = f"inventory-{kind}"
    expected = inputs.profile_inventory(kind)
    listed = client.tools_list if kind == "tools" else (
        client.resources_list if kind == "resources" else client.prompts_list
    )
    try:
        actual = await listed()
    except McpMethodNotFound as error:
        if not client.advertises(kind) and not expected:
            return Check(name, NOT_APPLICABLE, f"endpoint does not offer {kind}; profile expects none")
        return Check(name, FAIL, f"{error} (profile {inputs.profile} expects {len(expected)})")
    except McpProbeError as error:
        return Check(name, FAIL, str(error))
    if kind == "tools":
        observation.tools = actual
    elif kind == "resources":
        observation.resources = actual
    else:
        observation.prompts = actual
    if not actual and not expected and not client.advertises(kind):
        return Check(name, NOT_APPLICABLE, f"endpoint does not offer {kind}; profile expects none")
    return inventory_check(name, expected, actual)


async def bundle_info_check(client: McpHttpClient, inputs: Inputs) -> tuple[Check, dict[str, Any] | None]:
    """``bundle_info`` must exist, agree with the manifest version and (when stamped) revision."""

    name = "bundle-info"
    try:
        result = await client.tool_call(BUNDLE_INFO_TOOL, {})
    except McpProbeError as error:
        return Check(name, FAIL, str(error)), None
    if not result.structured:
        return Check(name, FAIL, "bundle_info returned no structuredContent"), None
    ok, detail = describe_bundle(result.structured, inputs.manifest)
    return Check(name, PASS if ok else FAIL, detail), result.structured


def describe_bundle(bundle: dict[str, Any], manifest: dict[str, Any]) -> tuple[bool, str]:
    """Compare the bundle-reported identity with the manifest the operator supplied."""

    expected_version = str(manifest["bundleVersion"])
    reported_version = bundle.get("bundleVersion")
    parts = [f"bundleVersion={reported_version!r}"]
    ok = reported_version == expected_version
    if not ok:
        parts.append(f"manifest declares {expected_version!r}")
    revision = manifest["sourceRevision"]
    if revision == "UNSTAMPED":
        parts.append("source revision compare skipped (manifest is UNSTAMPED)")
    else:
        source = bundle.get("bundleSourceRevision")
        if source is None:
            parts.append("handler reported no bundleSourceRevision (legacy bundle); compare skipped")
        elif source == revision:
            parts.append(f"bundleSourceRevision={str(source)[:12]}... matches")
        else:
            ok = False
            parts.append(f"bundleSourceRevision={source!r} != manifest {revision!r}")
    gateway_version = bundle.get("gatewayVersion")
    if isinstance(gateway_version, str) and gateway_version:
        parts.append(f"gatewayVersion={gateway_version}")
    module_version = bundle.get("mcpModuleVersion")
    if isinstance(module_version, str) and module_version:
        parts.append(f"mcpModuleVersion={module_version}")
    reported_status = bundle.get("compatibilityStatus")
    if isinstance(reported_status, str) and reported_status:
        parts.append(f"handler compatibilityStatus={reported_status}")
    return ok, "; ".join(parts)


def compatibility_check(inputs: Inputs, gateway: GatewayObservation, mcp: McpObservation) -> Check:
    """Deterministic D21 mapping onto ``manifest.testedTuples`` (never upgraded)."""

    name = "compatibility"
    if not gateway.reachable:
        return Check(name, SKIP, "Gateway did not answer /data/api/v1/gateway-info")
    observed = dict(gateway.identity)
    bundle_version = mcp.bundle.get("bundleVersion") if mcp.bundle else None
    observed["bundleVersion"] = bundle_version if isinstance(bundle_version, str) and bundle_version else None
    incomplete = [key for key, value in observed.items() if not isinstance(value, str) or not value]
    if incomplete:
        return Check(
            name,
            UNKNOWN,
            f"identity incomplete ({', '.join(sorted(incomplete))}); compatibility stays UNKNOWN",
        )
    rows = inputs.tested_tuples()
    tuple_text = ", ".join(f"{key}={observed[key]}" for key in TUPLE_FIELDS)
    for row in rows:
        if all(str(row.get(key)) == str(observed[key]) for key in TUPLE_FIELDS):
            status = str(row.get("compatibilityStatus"))
            outcome = (
                PASS if status in _COMPAT_PASS else FAIL if status in _COMPAT_FAIL else UNKNOWN
            )
            binding = row.get("nativeResponseBinding")
            gate = row.get("gate")
            return Check(
                name,
                outcome,
                f"testedTuples match (gate={gate}, binding={binding}) => {status}; {tuple_text}",
            )
    return Check(
        name,
        UNKNOWN,
        f"no testedTuples row for the observed tuple => UNTESTED; {tuple_text} "
        f"({len(rows)} row(s) in manifest)",
    )


# ---------------------------------------------------------------------------- report


def text_lines(checks: Sequence[Check]) -> list[str]:
    return [f"{check.status:<15}{check.name}: {check.detail}" for check in checks]


def summary(checks: Sequence[Check]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for check in checks:
        counts[check.status] = counts.get(check.status, 0) + 1
    return counts


def failed(checks: Sequence[Check]) -> list[Check]:
    return [check for check in checks if check.status == FAIL]


def doctor_report(inputs: Inputs, checks: Sequence[Check], exit_code: int) -> dict[str, Any]:
    return {
        "command": "doctor",
        "manifest": str(inputs.manifest_path),
        "bundleVersion": inputs.bundle_version,
        "profile": inputs.profile,
        "gatewayUrl": inputs.gateway_url.url,
        "mcpUrl": None if inputs.mcp_url is None else inputs.mcp_url.url,
        "checks": [check.as_dict() for check in checks],
        "summary": summary(checks),
        "exitCode": exit_code,
    }


def emit(inputs: SetupInputs, payload: dict[str, Any], lines: Sequence[str]) -> None:
    """Single output funnel: JSON report on stdout, or the plain text lines."""

    if inputs.as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for line in lines:
            print(line)


async def run(
    inputs: Inputs,
    *,
    gateway_transport: httpx.AsyncBaseTransport | None = None,
    mcp_transport: httpx.AsyncBaseTransport | None = None,
) -> int:
    """Execute the ordered doctor checks and report; 0 when nothing failed.

    The transports are an injection seam for tests (``httpx.MockTransport``); a real
    run leaves them unset and talks to the operator's endpoints.
    """

    checks: list[Check] = []
    async with make_gateway(inputs, gateway_transport) as gateway_client:
        gateway_checks, gateway_observation = await probe_gateway(gateway_client, inputs)
    checks.extend(gateway_checks)
    async with make_mcp(inputs, mcp_transport) as mcp_client:
        mcp_checks, mcp_observation = await probe_mcp(mcp_client, inputs)
    checks.extend(mcp_checks)
    checks.append(compatibility_check(inputs, gateway_observation, mcp_observation))

    exit_code = 1 if failed(checks) else 0
    lines = text_lines(checks) + [
        f"doctor: {len(checks)} check(s) {json.dumps(summary(checks), sort_keys=True)} => exit {exit_code}"
    ]
    emit(inputs, doctor_report(inputs, checks, exit_code), lines)
    return exit_code
