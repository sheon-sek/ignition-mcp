"""The guarded mutation executor (D08 safety chain, G3 core).

This module is the ONLY caller of ``GatewayClient.dispatch_write`` anywhere in
the server (enforced by a structural test). It runs the full chain in order:

    input validation (caller/service) -> authentication (auth-minted principal)
    -> authorization scope -> deployment mutation class -> operation allowlist
    -> target allowlist -> capability -> precondition/concurrency hook
    -> audit decision -> audit attempt -> dispatch exactly once
    -> bounded verification -> audit result

No layer is skippable by a single flag, nothing is ever retried, and every
ambiguous dispatch boundary (body partially/fully sent with no response) maps
to verification-then-``outcome_unknown`` — never a replay.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
import logging
from typing import Any, AsyncIterator, Awaitable, Callable

from ignition_rest_mcp.auth import VerifiedPrincipal, is_verified_principal
from ignition_rest_mcp.capabilities.registry import CapabilityRegistry
from ignition_rest_mcp.client.gateway import (
    DispatchOutcome,
    GatewayClient,
    WriteDispatchResult,
)
from ignition_rest_mcp.config import Settings
from ignition_rest_mcp.errors import GatewayError
from ignition_rest_mcp.operation import OperationContext
from ignition_rest_mcp.safety.policy import (
    MutationOperation,
    PolicyDecision,
    authorize_scope,
    evaluate_deployment_policy,
)

LOGGER = logging.getLogger("ignition_rest_mcp.safety")


class MutationState(str, Enum):
    SUCCEEDED = "succeeded"                    # dispatched, verified as applied
    RECOVERED_SUCCESS = "recovered_success"    # ambiguous/claimed outcome verified as applied
    NOT_APPLIED = "not_applied"                # dispatched/ambiguous, verified state unchanged
    REJECTED = "rejected"                      # 4xx rejection, state verified unchanged
    NOT_SENT = "not_sent"                      # known non-attempt (connect-stage failure)
    OUTCOME_UNKNOWN = "outcome_unknown"        # possibly dispatched; state cannot be established
    RECOVERY_REQUIRED = "recovery_required"    # claimed success but observed state is wrong


class VerificationOutcome(str, Enum):
    CONFIRMED = "confirmed"          # target now matches the intended post-state
    UNCHANGED = "unchanged"          # target still matches the pre-state
    MISMATCH = "mismatch"            # observable, but matches neither pre- nor post-state
    INDETERMINATE = "indeterminate"  # could not be established within bounds


@dataclass(frozen=True, slots=True)
class MutationRequest:
    operation: MutationOperation
    principal: VerifiedPrincipal
    target_id: str
    request_path: str
    body_chunks: AsyncIterator[bytes]
    content_type: str
    method: str = "POST"
    params: dict[str, Any] = field(default_factory=dict)
    dispatch_deadline_seconds: float = 120.0
    verification_deadline_seconds: float = 30.0
    verify: Callable[[WriteDispatchResult], Awaitable[VerificationOutcome]] | None = None
    precondition: Callable[[], Awaitable[None]] | None = None
    audit_fields: dict[str, Any] = field(default_factory=dict)
    target_type: str = ""


@dataclass(frozen=True, slots=True)
class MutationResult:
    state: MutationState
    dispatch: WriteDispatchResult | None
    error: GatewayError | None = None

    @property
    def possibly_dispatched(self) -> bool:
        return self.state not in {MutationState.NOT_SENT, MutationState.REJECTED}


async def execute_mutation(
    *,
    client: GatewayClient,
    registry: CapabilityRegistry,
    settings: Settings,
    context: OperationContext,
    request: MutationRequest,
) -> MutationResult:
    """Run the D08 chain exactly once for one mutation dispatch."""

    auditor = context.auditor
    if auditor is None:
        raise GatewayError("internal_error", "mutation dispatch requires an audited invocation context")
    target_type = request.target_type or "mutation-target"

    if not is_verified_principal(request.principal):
        await auditor.decision(
            allowed=False, reason="unauthenticated-principal",
            target_type=target_type, target_id=request.target_id,
        )
        raise GatewayError("permission_denied", "mutations require a verified principal")

    for decision in (
        authorize_scope(request.principal, request.operation),
        evaluate_deployment_policy(
            settings, request.operation, request.target_id,
            registry.supports(request.operation.capability),
        ),
    ):
        if not decision.allowed:
            await auditor.decision(
                allowed=False, reason=f"{decision.layer}:{decision.reason}",
                target_type=target_type, target_id=request.target_id,
            )
            assert decision.error_code is not None
            raise GatewayError(decision.error_code, _layer_message(decision))

    if request.precondition is not None:
        try:
            await request.precondition()
        except GatewayError as error:
            await auditor.decision(
                allowed=False, reason=f"precondition:{error.code}",
                target_type=target_type, target_id=request.target_id,
            )
            raise

    await auditor.decision(
        allowed=True, target_type=target_type, target_id=request.target_id,
        safe_fields=dict(request.audit_fields),
    )
    await auditor.attempt(
        target_type=target_type, target_id=request.target_id, safe_fields=dict(request.audit_fields),
    )

    phase = {"body_complete": False, "response_started": False}
    try:
        dispatch = await client.dispatch_write(
            request.method, request.request_path, params=request.params or None,
            body_chunks=request.body_chunks, content_type=request.content_type,
            deadline_seconds=request.dispatch_deadline_seconds, context=context,
            phase_report=phase,
        )
    except asyncio.CancelledError:
        boundary = (
            DispatchOutcome.SENT_COMPLETE_NO_RESPONSE
            if phase["response_started"] or phase["body_complete"]
            else DispatchOutcome.SENT_PARTIAL
        )
        try:
            await asyncio.shield(auditor.result("cancelled", error_code="outcome_unknown"))
        except Exception:  # pragma: no cover - cancellation hygiene must not mask the result
            pass
        # The boundary is never a replay licence: it is logged for diagnostics and
        # the persisted transaction state (slice 7) drives reconciliation.
        LOGGER.error(
            "Mutation dispatch cancelled",
            extra={
                "event": "mutation_cancelled", "correlationId": context.correlation_id,
                "tool": context.tool, "outcome": "cancelled",
                "errorCode": f"boundary:{boundary.value}",
            },
        )
        raise
    except GatewayError as error:
        await auditor.result("failed", error_code=error.code, target_type=target_type,
                             target_id=request.target_id)
        raise

    return await _interpret(client=client, auditor=auditor, request=request, dispatch=dispatch)


async def _interpret(
    *, client: GatewayClient, auditor: Any, request: MutationRequest, dispatch: WriteDispatchResult,
) -> MutationResult:
    target_type = request.target_type or "mutation-target"

    async def _verify() -> VerificationOutcome:
        if request.verify is None:
            return VerificationOutcome.INDETERMINATE
        try:
            async with asyncio.timeout(request.verification_deadline_seconds):
                return await request.verify(dispatch)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Bounded verification failed or timed out: state cannot be
            # established. For possibly-dispatched outcomes that maps to
            # outcome_unknown downstream — never a replay.
            return VerificationOutcome.INDETERMINATE

    if dispatch.outcome is DispatchOutcome.NOT_SENT:
        # Known non-attempt: the ordinary transport error is returned, and the
        # failure is never dressed up as outcome_unknown.
        error = dispatch.transport_error or GatewayError("gateway_unavailable", "Gateway is unavailable")
        await auditor.result("not_sent", error_code=error.code, target_type=target_type,
                             target_id=request.target_id)
        return MutationResult(MutationState.NOT_SENT, dispatch, error)

    if dispatch.outcome is DispatchOutcome.RESPONDED and 200 <= (dispatch.status or 0) < 300:
        try:
            verified = await _verify()
        except asyncio.CancelledError:
            await auditor.result("cancelled", error_code="outcome_unknown", target_type=target_type,
                                 target_id=request.target_id)
            raise
        if verified is VerificationOutcome.CONFIRMED:
            await auditor.result("completed", target_type=target_type, target_id=request.target_id)
            return MutationResult(MutationState.SUCCEEDED, dispatch, None)
        # A claimed success whose observed state is wrong (UNCHANGED/MISMATCH) or
        # cannot be exported within budget (INDETERMINATE) is RECOVERY_REQUIRED:
        # dispatch definitively happened, and OUTCOME_UNKNOWN stays reserved for
        # possibly-dispatched (ambiguous) boundaries (D16 slice-7 mapping).
        error = GatewayError(
            "outcome_unknown",
            "Gateway claimed success but the verified target state does not confirm the "
            "intended change; recovery is required and nothing was replayed",
        )
        await auditor.result("recovery_required", error_code="outcome_unknown",
                             target_type=target_type, target_id=request.target_id)
        return MutationResult(MutationState.RECOVERY_REQUIRED, dispatch, error)

    if dispatch.outcome is DispatchOutcome.RESPONDED and dispatch.status is not None and 400 <= dispatch.status < 500:
        try:
            verified = await _verify()
        except asyncio.CancelledError:
            await auditor.result("cancelled", error_code="outcome_unknown", target_type=target_type,
                                 target_id=request.target_id)
            raise
        if verified is VerificationOutcome.CONFIRMED:
            # rejected response but state changed anyway: recovered success, recorded as such
            await auditor.result("recovered_success", target_type=target_type, target_id=request.target_id)
            return MutationResult(MutationState.RECOVERED_SUCCESS, dispatch, None)
        if verified in {VerificationOutcome.INDETERMINATE, VerificationOutcome.MISMATCH}:
            error = GatewayError("outcome_unknown", "Gateway rejected the write but the state is neither unchanged nor confirmed")
            await auditor.result("outcome_unknown", error_code="outcome_unknown",
                                 target_type=target_type, target_id=request.target_id)
            return MutationResult(MutationState.OUTCOME_UNKNOWN, dispatch, error)
        error = _map_status(dispatch.status)
        await auditor.result("rejected", error_code=error.code, target_type=target_type,
                             target_id=request.target_id)
        return MutationResult(MutationState.REJECTED, dispatch, error)

    # Ambiguous boundaries: SENT_PARTIAL / SENT_COMPLETE_NO_RESPONSE / 5xx.
    try:
        verified = await _verify()
    except asyncio.CancelledError:
        await auditor.result("cancelled", error_code="outcome_unknown", target_type=target_type,
                             target_id=request.target_id)
        raise
    if verified is VerificationOutcome.CONFIRMED:
        await auditor.result("recovered_success", target_type=target_type, target_id=request.target_id)
        return MutationResult(MutationState.RECOVERED_SUCCESS, dispatch, None)
    if verified is VerificationOutcome.UNCHANGED:
        error = GatewayError("conflict", "the possibly dispatched mutation did not land; the Gateway state is unchanged")
        await auditor.result("not_applied", error_code="conflict", target_type=target_type,
                             target_id=request.target_id)
        return MutationResult(MutationState.NOT_APPLIED, dispatch, error)
    # MISMATCH or INDETERMINATE after an ambiguous dispatch: never outcome_unknown
    # becomes a replay licence; outcome_unknown is reserved for exactly here.
    error = GatewayError(
        "outcome_unknown",
        "the mutation may have reached Ignition and its final state cannot be established; "
        "it was not and must not be replayed",
    )
    await auditor.result("outcome_unknown", error_code="outcome_unknown", target_type=target_type,
                         target_id=request.target_id)
    return MutationResult(MutationState.OUTCOME_UNKNOWN, dispatch, error)


def _map_status(status: int) -> GatewayError:
    from ignition_rest_mcp.errors import map_http_status

    return map_http_status(status)


def _layer_message(decision: PolicyDecision) -> str:
    if decision.layer == "authz-scope":
        return "the caller scope does not permit this mutation class"
    if decision.layer == "deployment-class":
        return "this mutation class is disabled for the deployment"
    if decision.layer == "operation-allowlist":
        return "the operation is not in the deployment allowlist"
    if decision.layer == "target-allowlist":
        return "the target is not in the deployment allowlist"
    if decision.layer == "capability":
        return "the target Gateway does not expose the required capability"
    return "the mutation was denied by policy"
