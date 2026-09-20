"""Low-cost Gateway read/diagnostic use cases."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ignition_rest_mcp.capabilities.registry import CapabilityRegistry
from ignition_rest_mcp.client.gateway import GatewayClient
from ignition_rest_mcp.errors import GatewayError
from ignition_rest_mcp.models import (
    CapabilitiesResource,
    GatewayDiagnoseResult,
    GatewayInfoResult,
    OpenApiInfoResource,
)
from ignition_rest_mcp.operation import OperationContext


async def gateway_info(client: GatewayClient, registry: CapabilityRegistry, context: OperationContext) -> GatewayInfoResult:
    if not registry.supports("gateway_info"):
        raise GatewayError("unsupported_capability", "gateway_info is not present in the current capability snapshot")
    payload = await client.gateway_info(context)
    return GatewayInfoResult(
        correlationId=context.correlation_id,
        name=_string(payload, "name"),
        edition=_string(payload, "edition"),
        ignitionVersion=_string(payload, "ignitionVersion"),
        redundancyRole=_string(payload, "redundancyRole"),
        deploymentMode=_optional_string(payload, "deploymentMode"),
        timeZoneId=_string(payload, "timeZoneId"),
        jvmVersion=_string(payload, "jvmVersion"),
    )


async def gateway_diagnose(
    client: GatewayClient,
    registry: CapabilityRegistry,
    context: OperationContext,
) -> GatewayDiagnoseResult:
    snapshot = registry.snapshot
    try:
        info = await client.gateway_info(context)
        version = _string(info, "ignitionVersion")
        modules = await client.healthy_modules(context)
        module_items = modules.get("items")
        if not isinstance(module_items, list) or any(not isinstance(item, dict) for item in module_items):
            raise GatewayError("schema_mismatch", "Gateway module items are invalid")
        module_count = len(module_items)
        return GatewayDiagnoseResult(
            correlationId=context.correlation_id,
            gatewayReachable=True,
            authenticationOk=True,
            registryState=snapshot.state,
            registryGeneration=snapshot.generation,
            openapiSha256=snapshot.openapi_sha256,
            gatewayVersion=version if isinstance(version, str) else snapshot.gateway_version,
            moduleCount=module_count,
            message="Gateway REST authentication and low-cost diagnostics succeeded.",
        )
    except GatewayError as error:
        if error.code == "limit_exceeded":
            raise
        return GatewayDiagnoseResult(
            correlationId=context.correlation_id,
            gatewayReachable=error.code not in {"gateway_unavailable", "timeout"},
            # False means not confirmed; an outage must never assert authentication succeeded.
            authenticationOk=False,
            registryState=snapshot.state,
            registryGeneration=snapshot.generation,
            openapiSha256=snapshot.openapi_sha256,
            gatewayVersion=snapshot.gateway_version,
            moduleCount=len(snapshot.module_versions),
            message=str(error),
        )


def capabilities_resource(registry: CapabilityRegistry) -> CapabilitiesResource:
    snapshot = registry.snapshot
    return CapabilitiesResource(
        state=snapshot.state,
        generation=snapshot.generation,
        fetchedAt=snapshot.fetched_at.isoformat() if snapshot.fetched_at else None,
        gatewayVersion=snapshot.gateway_version,
        openapiSha256=snapshot.openapi_sha256,
        supportedCapabilities=sorted(snapshot.semantic_capabilities),
        endpointCount=len(snapshot.endpoints),
        moduleVersions=dict(snapshot.module_versions),
    )


def openapi_info_resource(registry: CapabilityRegistry) -> OpenApiInfoResource:
    snapshot = registry.snapshot
    age: float | None = None
    if snapshot.fetched_at is not None:
        age = max(0.0, (datetime.now(timezone.utc) - snapshot.fetched_at).total_seconds())
    return OpenApiInfoResource(
        state=snapshot.state,
        generation=snapshot.generation,
        fetchedAt=snapshot.fetched_at.isoformat() if snapshot.fetched_at else None,
        ageSeconds=age,
        gatewayVersion=snapshot.gateway_version,
        environmentFingerprint=snapshot.environment_fingerprint,
        openapiSha256=snapshot.openapi_sha256,
    )


def _string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise GatewayError("schema_mismatch", f"Gateway response field {key} is invalid")
    return value


def _optional_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key, "")
    if not isinstance(value, str):
        raise GatewayError("schema_mismatch", f"Gateway response field {key} is invalid")
    return value
