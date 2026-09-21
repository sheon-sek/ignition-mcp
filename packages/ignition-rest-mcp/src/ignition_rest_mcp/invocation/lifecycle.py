"""The single central Tool invocation lifecycle (D10/D18/D19).

Every registered Tool routes through :func:`invoke_tool`: context creation,
operation record, budget-class deadline, output budget, error mapping, metrics,
structured logging and record finalization all live here — nowhere else.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any, Awaitable, Callable, NoReturn, TypeVar

from fastmcp.exceptions import ToolError
from pydantic import BaseModel, ValidationError

from ignition_rest_mcp.audit.sink import Auditor
from ignition_rest_mcp.errors import GatewayError
from ignition_rest_mcp.operation import OperationContext
from ignition_rest_mcp.storage.database import StorageUnavailable

if TYPE_CHECKING:
    from ignition_rest_mcp.auth import Principal
    from ignition_rest_mcp.config import Settings
    from ignition_rest_mcp.runtime import RuntimeState

LOGGER = logging.getLogger("ignition_rest_mcp")
HARD_OUTPUT_BYTES = 1_048_576
TModel = TypeVar("TModel", bound=BaseModel)


def enforce_output_budget(model: BaseModel, configured_limit_bytes: int) -> None:
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


async def invoke_tool(
    *,
    state: "RuntimeState",
    settings: "Settings",
    tool: str,
    budget_class: str,
    principal: "Principal",
    handler: Callable[[OperationContext], Awaitable[TModel]],
    permission_class: str = "READ",
    destructive: bool = False,
    audited: bool = False,
) -> TModel:
    """One lifecycle for every Tool. See module docstring for responsibilities."""

    context = OperationContext.start(
        tool, principal.key, budget_class, permission_class=permission_class, destructive=destructive,
    )
    metrics = state.require_metrics()
    records = state.require_records()
    recorded = True
    try:
        await records.start(context)
    except StorageUnavailable:
        # Operation records are diagnostics for ordinary reads: count + log, never fail the call.
        recorded = False
        metrics.record_operation_record_write_failure("start")
        LOGGER.error(
            "Operation record could not be started; continuing without record",
            extra={
                "event": "operation_record_failure", "correlationId": context.correlation_id,
                "tool": tool, "outcome": "error", "errorCode": "storage_unavailable",
            },
        )
    if audited:
        context.auditor = Auditor(state.require_audit_sink(), records, context, metrics)
    try:
        async with asyncio.timeout(settings.budget_deadline_seconds(budget_class)):
            result = await handler(context)
        enforce_output_budget(result, settings.structured_output_limit_bytes)
    except (Exception, asyncio.CancelledError) as error:
        await _fail(error, context, state, records, metrics)
    if recorded:
        await _finish_best_effort(records, context, "succeeded", None, metrics)
    metrics.record_tool(tool, "success")
    log_tool(context, "success")
    return result


async def _fail(
    error: BaseException, context: OperationContext, state: "RuntimeState",
    records: Any, metrics: Any,
) -> NoReturn:
    if _is_cancelled(error):
        # Exactly one cancellation log line (G1 guarantee preserved); the record is
        # finalized as 'cancelled' before propagation.
        metrics.record_tool(context.tool, "cancelled")
        log_tool(context, "cancelled", "cancelled")
        if context.auditor is not None and context.auditor.attempt_recorded:
            await context.auditor.result("cancelled")
        await _finish_best_effort(records, context, "cancelled", None, metrics)
        raise error
    safe = await _map_error(error, context, metrics, state)
    if context.auditor is not None and context.auditor.attempt_recorded:
        await context.auditor.result(_outcome_for(error), error_code=safe.code)
    await _finish_best_effort(records, context, _outcome_for(error), safe.code, metrics)
    raise ToolError(json.dumps({
        "code": safe.code, "message": safe.message, "correlationId": context.correlation_id,
    }, separators=(",", ":"))) from error


def _is_cancelled(error: BaseException) -> bool:
    return isinstance(error, asyncio.CancelledError)


def _outcome_for(error: BaseException) -> str:
    if _is_cancelled(error):
        return "cancelled"
    if isinstance(error, GatewayError) and error.code == "outcome_unknown":
        return "outcome_unknown"
    return "failed"


async def _map_error(
    error: BaseException, context: OperationContext, metrics: Any, state: "RuntimeState",
) -> GatewayError:
    registry = state.require_registry()
    if isinstance(error, TimeoutError):
        registry.mark_stale()
        safe = GatewayError("timeout", "MCP tool execution timed out")
    elif isinstance(error, ValidationError):
        safe = GatewayError("schema_mismatch", "Ignition Gateway returned an unexpected response")
        await registry.observe_failure(safe)
    elif isinstance(error, StorageUnavailable):
        safe = GatewayError("internal_error", "The storage subsystem is unavailable")
    elif isinstance(error, GatewayError):
        safe = error
    else:
        safe = GatewayError("internal_error", "Unexpected external server error")
    metrics.record_tool(context.tool, safe.code)
    log_tool(context, "error", safe.code)
    return safe


async def _finish_best_effort(
    records: Any, context: OperationContext, outcome: str, error_code: str | None, metrics: Any,
) -> None:
    try:
        await records.finish(context.correlation_id, outcome, error_code)
    except StorageUnavailable:
        metrics.record_operation_record_write_failure("finish")
        LOGGER.error(
            "Operation record could not be finalized",
            extra={
                "event": "operation_record_failure", "correlationId": context.correlation_id,
                "tool": context.tool, "outcome": "error", "errorCode": "storage_unavailable",
            },
        )


def log_tool(context: OperationContext, outcome: str, error_code: str | None = None) -> None:
    extra: dict[str, object] = {
        "event": "tool_call",
        "correlationId": context.correlation_id,
        "tool": context.tool,
        "server": context.server,
        "actor": context.actor,
        "permissionClass": context.permission_class,
        "outcome": outcome,
        "durationMs": round(context.elapsed_ms(), 3),
    }
    if error_code is not None:
        extra["errorCode"] = error_code
    LOGGER.info("MCP tool completed", extra=extra)
