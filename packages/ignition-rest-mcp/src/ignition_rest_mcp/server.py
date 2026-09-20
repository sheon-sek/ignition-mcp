"""FastMCP 4 Streamable HTTP server for the Phase 1 external slice."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import logging
from typing import AsyncIterator

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response

from ignition_rest_mcp.auth import build_auth
from ignition_rest_mcp.capabilities.registry import CapabilityRegistry, CapabilitySnapshot
from ignition_rest_mcp.client.gateway import GatewayClient
from ignition_rest_mcp.config import Settings
from ignition_rest_mcp.errors import GatewayError
from ignition_rest_mcp.models import (
    CapabilitiesResource,
    GatewayDiagnoseResult,
    GatewayInfoResult,
    OpenApiInfoResource,
)
from ignition_rest_mcp.observability.metrics import Metrics
from ignition_rest_mcp.operation import OperationContext
from ignition_rest_mcp.runtime import RuntimeState
from ignition_rest_mcp.services.gateway import (
    capabilities_resource,
    gateway_diagnose as diagnose_service,
    gateway_info as info_service,
    openapi_info_resource,
)

LOGGER = logging.getLogger("ignition_rest_mcp")
HARD_OUTPUT_BYTES = 1_048_576


def create_server(settings: Settings) -> FastMCP:
    state = RuntimeState()

    @asynccontextmanager
    async def lifespan(_: FastMCP) -> AsyncIterator[None]:
        client = GatewayClient(
            base_url=settings.gateway_url,
            api_token=settings.gateway_api_token,
            timeout_seconds=settings.request_timeout_seconds,
        )
        registry = CapabilityRegistry(client)
        metrics = Metrics()
        state.client = client
        state.registry = registry
        state.metrics = metrics
        await registry.refresh()
        _apply_visibility(mcp, registry.snapshot)
        watcher = asyncio.create_task(_watch_capabilities(mcp, registry, settings.watcher_interval_seconds))
        try:
            yield
        finally:
            watcher.cancel()
            try:
                await watcher
            except asyncio.CancelledError:
                pass
            await client.aclose()
            state.client = None
            state.registry = None
            state.metrics = None

    mcp = FastMCP(
        name="ignition-rest",
        version="0.1.0a0",
        auth=build_auth(settings),
        lifespan=lifespan,
        mask_error_details=True,
    )

    @mcp.tool(
        name="gateway_info",
        description="Return a bounded identity summary for the connected Ignition Gateway.",
        output_schema=GatewayInfoResult.model_json_schema(),
        tags={"read", "capability:gateway_info"},
        timeout=30,
    )
    async def gateway_info() -> GatewayInfoResult:
        context = OperationContext.read("gateway_info", settings.service_identity)
        metrics = state.require_metrics()
        try:
            result = await info_service(state.require_client(), state.require_registry(), context)
            _enforce_output_budget(result)
            metrics.record_tool("gateway_info", "success")
            return result
        except GatewayError as error:
            metrics.record_tool("gateway_info", error.code)
            raise ToolError(f"{error.code}: {error.message}; correlationId={context.correlation_id}") from error

    @mcp.tool(
        name="gateway_diagnose",
        description="Run low-cost connectivity, authentication, and capability-registry diagnostics.",
        output_schema=GatewayDiagnoseResult.model_json_schema(),
        tags={"read", "diagnostic"},
        timeout=30,
    )
    async def gateway_diagnose() -> GatewayDiagnoseResult:
        context = OperationContext.read("gateway_diagnose", settings.service_identity)
        metrics = state.require_metrics()
        result = await diagnose_service(state.require_client(), state.require_registry(), context)
        _enforce_output_budget(result)
        metrics.record_tool("gateway_diagnose", "success" if result.gatewayReachable else "degraded")
        return result

    @mcp.resource(
        "ignition://gateway/capabilities",
        name="gateway-capabilities",
        description="Bounded metadata for the current immutable Gateway capability snapshot.",
        mime_type="application/json",
    )
    async def gateway_capabilities() -> str:
        value: CapabilitiesResource = capabilities_resource(state.require_registry())
        return value.model_dump_json()

    @mcp.resource(
        "ignition://gateway/openapi-info",
        name="gateway-openapi-info",
        description="OpenAPI fingerprint and registry metadata; does not expose the full OpenAPI document.",
        mime_type="application/json",
    )
    async def gateway_openapi_info() -> str:
        value: OpenApiInfoResource = openapi_info_resource(state.require_registry())
        return value.model_dump_json()

    @mcp.custom_route("/health/live", methods=["GET"], include_in_schema=False)
    async def health_live(_: Request) -> Response:
        return JSONResponse({"live": True})

    @mcp.custom_route("/health/ready", methods=["GET"], include_in_schema=False)
    async def health_ready(_: Request) -> Response:
        registry = state.registry
        ready = registry is not None and registry.snapshot.state == "READY"
        status = 200 if ready else 503
        return JSONResponse(
            {"ready": ready, "registryState": registry.snapshot.state if registry is not None else "UNAVAILABLE"},
            status_code=status,
        )

    @mcp.custom_route("/metrics", methods=["GET"], include_in_schema=False)
    async def metrics(_: Request) -> Response:
        current = state.metrics
        return PlainTextResponse(current.render() if current is not None else "", media_type="text/plain; version=0.0.4")

    return mcp


async def _watch_capabilities(mcp: FastMCP, registry: CapabilityRegistry, interval: float) -> None:
    while True:
        await asyncio.sleep(interval)
        if await registry.fingerprint_changed():
            await registry.refresh()
        _apply_visibility(mcp, registry.snapshot)


def _apply_visibility(mcp: FastMCP, snapshot: CapabilitySnapshot) -> None:
    if "gateway_info" in snapshot.semantic_capabilities and snapshot.state in {"READY", "STALE"}:
        mcp.enable(names={"gateway_info"}, components={"tool"})
    else:
        mcp.disable(names={"gateway_info"}, components={"tool"})


def _enforce_output_budget(model: GatewayInfoResult | GatewayDiagnoseResult) -> None:
    size = len(json.dumps(model.model_dump(mode="json"), separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    if size > HARD_OUTPUT_BYTES:
        raise GatewayError("limit_exceeded", f"Structured output exceeds {HARD_OUTPUT_BYTES} bytes")


def main() -> None:
    settings = Settings.from_env()
    logging.basicConfig(level=os_log_level(), format="%(message)s")
    mcp = create_server(settings)
    mcp.run(
        transport="streamable-http",
        host=settings.bind_host,
        port=settings.bind_port,
        path=settings.mcp_path,
        show_banner=False,
    )


def os_log_level() -> str:
    import os

    return os.getenv("IGNITION_MCP_LOG_LEVEL", "INFO").upper()


if __name__ == "__main__":
    main()
