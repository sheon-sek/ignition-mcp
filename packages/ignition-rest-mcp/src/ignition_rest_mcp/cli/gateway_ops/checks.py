"""Named diagnostic checks and the bounded MCP client factory (D20, D21).

A check yields ``{name, status, detail}`` with status in
``PASS | FAIL | SKIP | NOT_APPLICABLE | UNKNOWN``. Nothing here writes: the checks
compare an inventory or an identity the caller already read.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import httpx

from ignition_rest_mcp.cli.gateway_ops.inputs import Inputs, UsageError
from ignition_rest_mcp.cli.gateway_ops.mcp_http import McpHttpClient, McpProbeError

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"
NOT_APPLICABLE = "NOT_APPLICABLE"
UNKNOWN = "UNKNOWN"

BUNDLE_INFO_TOOL = "bundle_info"


@dataclass(frozen=True, slots=True)
class Check:
    """One named diagnostic outcome."""

    name: str
    status: str
    detail: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"name": self.name, "status": self.status, "detail": self.detail}


def make_mcp(inputs: Inputs, transport: httpx.AsyncBaseTransport | None = None) -> McpHttpClient:
    endpoint = inputs.runtime_endpoint()
    if endpoint is None:  # pragma: no cover - the engine resolves the endpoint first
        raise UsageError("this command needs --mcp-url (or --server-config-name to derive it)")
    return McpHttpClient(
        endpoint=endpoint,
        token=inputs.mcp_token,
        timeout_seconds=inputs.timeout_seconds,
        transport=transport,
    )


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
