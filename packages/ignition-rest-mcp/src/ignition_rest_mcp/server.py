"""FastMCP 4 Streamable HTTP server for the Phase 1 external slice."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
import logging
from typing import AsyncIterator, NoReturn

from fastmcp import FastMCP
from fastmcp.exceptions import ResourceError, ToolError
from pydantic import BaseModel
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response

from ignition_rest_mcp.auth import build_auth, operation_actor
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
from ignition_rest_mcp.observability.logging import configure_logging
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
TOOL_TIMEOUT_SECONDS = 30


def create_server(settings: Settings) -> FastMCP:
    settings.validate()
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
        watcher: asyncio.Task[None] | None = None
        try:
            await registry.refresh()
            _apply_visibility(mcp, registry.snapshot)
            LOGGER.info(
                "Capability registry initialized",
                extra={
                    "event": "capability_registry",
                    "registryState": registry.snapshot.state,
                    "registryGeneration": registry.snapshot.generation,
                },
            )
            watcher = asyncio.create_task(_watch_capabilities(mcp, registry, settings.watcher_interval_seconds))
            yield
        finally:
            try:
                if watcher is not None:
                    watcher.cancel()
                    await asyncio.gather(watcher, return_exceptions=True)
                await registry.aclose()
            finally:
                try:
                    await client.aclose()
                finally:
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
    )
    async def gateway_info() -> GatewayInfoResult:
        context = OperationContext.read("gateway_info", operation_actor(settings))
        metrics = state.require_metrics()
        try:
            async with asyncio.timeout(TOOL_TIMEOUT_SECONDS):
                result = await info_service(state.require_client(), state.require_registry(), context)
            _enforce_output_budget(result, settings.structured_output_limit_bytes)
            metrics.record_tool("gateway_info", "success")
            _log_tool(context, "success")
            return result
        except (Exception, asyncio.CancelledError) as error:
            _raise_tool_error(error, context, metrics, state.require_registry())

    @mcp.tool(
        name="gateway_diagnose",
        description="Run low-cost connectivity, authentication, and capability-registry diagnostics.",
        output_schema=GatewayDiagnoseResult.model_json_schema(),
        tags={"read", "diagnostic"},
    )
    async def gateway_diagnose() -> GatewayDiagnoseResult:
        context = OperationContext.read("gateway_diagnose", operation_actor(settings))
        metrics = state.require_metrics()
        try:
            async with asyncio.timeout(TOOL_TIMEOUT_SECONDS):
                result = await diagnose_service(state.require_client(), state.require_registry(), context)
            _enforce_output_budget(result, settings.structured_output_limit_bytes)
            outcome = "success" if result.gatewayReachable and result.authenticationOk else "degraded"
            metrics.record_tool("gateway_diagnose", outcome)
            _log_tool(context, outcome)
            return result
        except (Exception, asyncio.CancelledError) as error:
            _raise_tool_error(error, context, metrics, state.require_registry())

    @mcp.resource(
        "ignition://gateway/capabilities",
        name="gateway-capabilities",
        description="Bounded metadata for the current immutable Gateway capability snapshot.",
        mime_type="application/json",
    )
    async def gateway_capabilities() -> str:
        value: CapabilitiesResource = capabilities_resource(state.require_registry())
        try:
            _enforce_output_budget(value, settings.structured_output_limit_bytes)
        except GatewayError as error:
            raise ResourceError(str(error)) from error
        return value.model_dump_json()

    @mcp.resource(
        "ignition://gateway/openapi-info",
        name="gateway-openapi-info",
        description="OpenAPI fingerprint and registry metadata; does not expose the full OpenAPI document.",
        mime_type="application/json",
    )
    async def gateway_openapi_info() -> str:
        value: OpenApiInfoResource = openapi_info_resource(state.require_registry())
        try:
            _enforce_output_budget(value, settings.structured_output_limit_bytes)
        except GatewayError as error:
            raise ResourceError(str(error)) from error
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
        try:
            if await registry.fingerprint_changed():
                await registry.refresh()
            _apply_visibility(mcp, registry.snapshot)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception(
                "Capability watcher iteration failed",
                extra={"event": "capability_watch"},
            )


def _apply_visibility(mcp: FastMCP, snapshot: CapabilitySnapshot) -> None:
    if "gateway_info" in snapshot.semantic_capabilities and snapshot.state in {"READY", "STALE"}:
        mcp.enable(names={"gateway_info"}, components={"tool"})
    else:
        mcp.disable(names={"gateway_info"}, components={"tool"})


def _enforce_output_budget(
    model: BaseModel,
    configured_limit_bytes: int,
) -> None:
    limit = min(configured_limit_bytes, HARD_OUTPUT_BYTES)
    size = len(
        json.dumps(
            model.model_dump(mode="json"),
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    )
    if size > limit:
        raise GatewayError(
            "limit_exceeded",
            f"Structured output requires {size} bytes; configured limit is {limit} bytes. "
            "Reduce requested data or raise the deployment limit within the hard ceiling.",
        )


def _raise_tool_error(
    error: BaseException, context: OperationContext, metrics: Metrics, registry: CapabilityRegistry,
) -> NoReturn:
    if isinstance(error, asyncio.CancelledError):
        metrics.record_tool(context.tool, "cancelled")
        _log_tool(context, "cancelled", "cancelled")
        raise error
    if isinstance(error, TimeoutError):
        registry.mark_stale()
        safe = GatewayError("timeout", "MCP tool execution timed out")
    elif isinstance(error, GatewayError):
        safe = error
    else:
        safe = GatewayError("internal_error", "Unexpected external server error")
    metrics.record_tool(context.tool, safe.code)
    _log_tool(context, "error", safe.code)
    raise ToolError(json.dumps({
        "code": safe.code, "message": safe.message, "correlationId": context.correlation_id,
    }, separators=(",", ":"))) from error


def _log_tool(context: OperationContext, outcome: str, error_code: str | None = None) -> None:
    duration_ms = max(
        0.0,
        (datetime.now(timezone.utc) - context.started_at).total_seconds() * 1000.0,
    )
    extra: dict[str, object] = {
        "event": "tool_call",
        "correlationId": context.correlation_id,
        "tool": context.tool,
        "server": context.server,
        "actor": context.actor,
        "permissionClass": context.permission_class,
        "outcome": outcome,
        "durationMs": round(duration_ms, 3),
    }
    if error_code is not None:
        extra["errorCode"] = error_code
    LOGGER.info("MCP tool completed", extra=extra)


def main() -> None:
    settings = Settings.from_env()
    configure_logging(log_format=settings.resolved_log_format, level=os_log_level())
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
