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


class DispatchBoundary(str, Enum):
    """What a *durable* record may conclude about one dispatch attempt.

    The guarded executor classifies every answer once, as soon as it exists and before
    any verification or read-back, and hands it to ``MutationRequest.on_dispatch_boundary``.
    A caller that keeps durable state (the D16 Project transaction) persists it on its
    row, so a process that dies in this window leaves a record the restart can read
    instead of an ambiguous boundary it has to reinterpret.

    The two values the executor never produces describe a row whose answer was not
    recorded yet: the transaction writes one *before* it dispatches, because that is the
    only state that is true while the answer is unknown.
    """

    #: Nothing left the process (a connect-stage failure): a known non-attempt.
    NOT_SENT = "not_sent"
    #: The Gateway answered and refused, and ``rejection_is_final`` makes that answer the
    #: result: nothing may attribute a success to the attempt (D30 §2), because a target
    #: that happens to show the requested state may have been changed by another writer.
    REFUSED = "refused"
    #: The Gateway answered without refusing: a claim that only a matching read-back
    #: confirms.
    CLAIMED = "claimed"
    #: No definite answer (partial send, no response, 5xx), or a refusal this operation
    #: does not make final. D16's attribution applies: a read-back equal to the staged
    #: candidate B is a recovered success.
    ATTRIBUTABLE = "attributable"
    #: Possibly dispatched, while an unrecorded refusal would be final for this
    #: operation. An unrecorded refusal is indistinguishable from an ambiguous boundary,
    #: so nothing may attribute a success: D30 §2 wins over D16's recovered success here,
    #: and the transaction reports OUTCOME_UNKNOWN with its recovery snapshot kept.
    UNATTRIBUTABLE = "unattributable"


@dataclass(frozen=True, slots=True)
class DispatchClassification:
    """One dispatch's answer, classified once, before any verification or read-back.

    ``boundary`` is the part a caller may persist and act on later; ``rejection`` is the
    Gateway's own refusal when it gave one (D30 §2/§7), and ``error`` is the error the
    boundary itself carries — a refusal, or the transport error of a known non-attempt.
    """

    boundary: DispatchBoundary
    dispatch: WriteDispatchResult
    rejection: GatewayError | None = None
    error: GatewayError | None = None


class VerificationOutcome(str, Enum):
    CONFIRMED = "confirmed"          # target now matches the intended post-state
    UNCHANGED = "unchanged"          # target still matches the pre-state
    MISMATCH = "mismatch"            # observable, but matches neither pre- nor post-state
    INDETERMINATE = "indeterminate"  # could not be established within bounds


@dataclass(frozen=True, slots=True)
class MutationPreflight:
    """Everything the D08 chain decides on before a dispatch: the operation, the
    caller and every Target the call changes.

    ``MutationRequest`` projects itself onto this, and a Tool with expensive work to do
    before it dispatches (the D16 Project transaction exports, stages and fingerprints
    an archive first) runs the same chain on its own, so a denial refuses the call
    before that work and both callers share one implementation.
    """

    operation: MutationOperation
    principal: VerifiedPrincipal
    target_id: str
    target_type: str = ""
    additional_target_ids: tuple[str, ...] = ()
    target_policy: Callable[[], PolicyDecision] | None = None
    precondition: Callable[[], Awaitable[None]] | None = None
    audit_fields: dict[str, Any] = field(default_factory=dict)


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
    #: Further Targets the same call changes (D30 §3 Preflight). A rename mutates its
    #: source *and* produces a resource at its destination, so both must be
    #: allowlisted before anything executes; an empty tuple means the one Target.
    additional_target_ids: tuple[str, ...] = ()
    dispatch_deadline_seconds: float = 120.0
    verification_deadline_seconds: float = 30.0
    verify: Callable[[WriteDispatchResult], Awaitable[VerificationOutcome]] | None = None
    precondition: Callable[[], Awaitable[None]] | None = None
    audit_fields: dict[str, Any] = field(default_factory=dict)
    target_type: str = ""
    #: A Target-class policy rule the operation itself declares (D30 §5): it is
    #: evaluated between the operation allowlist and the Target allowlist, inside
    #: the deployment policy, and its denial is audited like every other layer.
    target_policy: Callable[[], PolicyDecision] | None = None
    #: A Gateway can report a refused change inside a 2xx response (Ignition's
    #: resource PUT answers ``success=false`` with a ``problem``). Such a response
    #: is a known rejection, never a claimed success: the classifier turns it into
    #: the D06 error the caller must see.
    rejection: Callable[[WriteDispatchResult], GatewayError | None] | None = None
    #: Called once, as soon as the dispatch boundary is classified and before any
    #: verification or read-back, so a caller that keeps durable state writes down what
    #: the Gateway answered. The D16 Project transaction uses it to keep an explicit
    #: rejection final across a restart (D30 §2) and to leave a row whose answer was
    #: never recorded indistinguishable from an ambiguous boundary rather than from a
    #: success.
    on_dispatch_boundary: Callable[[DispatchClassification], Awaitable[None]] | None = None

    @property
    def preflight(self) -> MutationPreflight:
        """The part of this request the D08 chain decides on before a dispatch."""

        return MutationPreflight(
            operation=self.operation, principal=self.principal, target_id=self.target_id,
            target_type=self.target_type, additional_target_ids=self.additional_target_ids,
            target_policy=self.target_policy, precondition=self.precondition,
            audit_fields=dict(self.audit_fields),
        )


@dataclass(frozen=True, slots=True)
class MutationResult:
    state: MutationState
    dispatch: WriteDispatchResult | None
    error: GatewayError | None = None

    @property
    def possibly_dispatched(self) -> bool:
        return self.state not in {MutationState.NOT_SENT, MutationState.REJECTED}


async def preflight_mutation(
    *,
    registry: CapabilityRegistry,
    settings: Settings,
    context: OperationContext,
    preflight: MutationPreflight,
) -> None:
    """The D08 chain up to and including the Precondition hook, exactly once.

    Authentication, authorization scope, deployment class, operation allowlist, the
    operation's Target-class rule (D30 §5), every Target allowlist and the Precondition
    hook run in order, every denial writes its ``decision`` row, and nothing outside
    this function decides whether a mutation may be attempted. It is callable on its
    own so a Tool that has expensive work to do before dispatch — the D16 Project
    transaction exports, stages and fingerprints an archive first — refuses the call
    before that work, and so the audited reason and the caller's error code cannot
    drift between the two callers.
    """

    auditor = context.auditor
    if auditor is None:
        raise GatewayError("internal_error", "mutation dispatch requires an audited invocation context")
    target_type = preflight.target_type or "mutation-target"

    if not is_verified_principal(preflight.principal):
        await auditor.decision(
            allowed=False, reason="unauthenticated-principal",
            target_type=target_type, target_id=preflight.target_id,
        )
        raise GatewayError("permission_denied", "mutations require a verified principal")

    scope = authorize_scope(preflight.principal, preflight.operation)
    if not scope.allowed:
        await auditor.decision(
            allowed=False, reason=f"{scope.layer}:{scope.reason}",
            target_type=target_type, target_id=preflight.target_id,
        )
        assert scope.error_code is not None
        raise GatewayError(scope.error_code, _layer_message(scope))

    capability_present = registry.supports(preflight.operation.capability)
    # D30 §5: the operation's own Target-class rule (Refused resource types) is
    # evaluated before the Target allowlist, so it also covers the fully allowlisted
    # deployment where only it can refuse.
    target_class = preflight.target_policy() if preflight.target_policy is not None else None
    # D30 §3 Preflight: every Target this call changes is checked before anything
    # executes, and a denial names the Target that was refused.
    for target_id in (preflight.target_id, *preflight.additional_target_ids):
        decision = evaluate_deployment_policy(
            settings, preflight.operation, target_id, capability_present, target_class=target_class,
        )
        if decision.allowed:
            continue
        await auditor.decision(
            allowed=False, reason=f"{decision.layer}:{decision.reason}",
            target_type=target_type, target_id=target_id,
        )
        assert decision.error_code is not None
        raise GatewayError(decision.error_code, _layer_message(decision))

    if preflight.precondition is not None:
        try:
            await preflight.precondition()
        except GatewayError as error:
            await auditor.decision(
                allowed=False, reason=f"precondition:{error.code}",
                target_type=target_type, target_id=preflight.target_id,
            )
            raise


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
    await preflight_mutation(
        registry=registry, settings=settings, context=context, preflight=request.preflight,
    )

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

    # A Gateway can carry its own refusal inside a 2xx response. That is a known
    # rejection (the Gateway answered and refused), so it follows the same path as
    # a 4xx: no claimed success, and no invented outcome_unknown.
    rejection = (
        request.rejection(dispatch)
        if request.rejection is not None and dispatch.outcome is DispatchOutcome.RESPONDED
        else None
    )

    # The answer is classified once, as soon as it exists and before any read-back (and
    # before any early return, so a known non-attempt is recorded too), and the caller is
    # handed the classification at that instant: a caller that keeps durable state
    # records what the Gateway answered, so a process that dies here leaves a record a
    # restart can read instead of a boundary it has to reinterpret (D30 §2, D16).
    if request.on_dispatch_boundary is not None:
        await request.on_dispatch_boundary(DispatchClassification(
            boundary=_classify_boundary(request.operation, dispatch, rejection),
            dispatch=dispatch, rejection=rejection, error=_boundary_error(dispatch, rejection),
        ))

    if dispatch.outcome is DispatchOutcome.NOT_SENT:
        # Known non-attempt: the ordinary transport error is returned, and the
        # failure is never dressed up as outcome_unknown.
        error = dispatch.transport_error or GatewayError("gateway_unavailable", "Gateway is unavailable")
        await auditor.result("not_sent", error_code=error.code, target_type=target_type,
                             target_id=request.target_id)
        return MutationResult(MutationState.NOT_SENT, dispatch, error)

    if dispatch.outcome is DispatchOutcome.RESPONDED and rejection is None and 200 <= (dispatch.status or 0) < 300:
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

    if dispatch.outcome is DispatchOutcome.RESPONDED and (
        rejection is not None or (dispatch.status is not None and 400 <= dispatch.status < 500)
    ):
        if request.operation.rejection_is_final:
            # D30 §2: the Gateway answered and refused. That answer is the result —
            # no read-back may turn it into a success, because a resource that
            # happens to show the requested values may have been changed by another
            # writer, and claiming one is exactly the false success this forbids.
            error = rejection or _map_status(dispatch.status or 500)
            await auditor.result("rejected", error_code=error.code, target_type=target_type,
                                 target_id=request.target_id)
            return MutationResult(MutationState.REJECTED, dispatch, error)
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
        error = rejection or _map_status(dispatch.status or 500)
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


def _classify_boundary(
    operation: MutationOperation, dispatch: WriteDispatchResult, rejection: GatewayError | None,
) -> DispatchBoundary:
    """The durable class of one dispatch answer (the branch ``_interpret`` then takes).

    ``REFUSED`` is only ever returned for an operation whose refusal is final
    (``rejection_is_final``): for any other operation the refusal is the input to a
    read-back, exactly like an ambiguous boundary, so the answer is attributable.
    """

    status = dispatch.status or 0
    if dispatch.outcome is DispatchOutcome.NOT_SENT:
        return DispatchBoundary.NOT_SENT
    if dispatch.outcome is DispatchOutcome.RESPONDED and (rejection is not None or 400 <= status < 500):
        return DispatchBoundary.REFUSED if operation.rejection_is_final else DispatchBoundary.ATTRIBUTABLE
    if dispatch.outcome is DispatchOutcome.RESPONDED and 200 <= status < 300:
        return DispatchBoundary.CLAIMED
    return DispatchBoundary.ATTRIBUTABLE


def _boundary_error(dispatch: WriteDispatchResult, rejection: GatewayError | None) -> GatewayError | None:
    """The error a boundary carries by itself: a refusal, or a known non-attempt."""

    if dispatch.outcome is DispatchOutcome.NOT_SENT:
        return dispatch.transport_error or GatewayError("gateway_unavailable", "Gateway is unavailable")
    if rejection is not None:
        return rejection
    if dispatch.outcome is DispatchOutcome.RESPONDED and dispatch.status is not None and 400 <= dispatch.status < 500:
        return _map_status(dispatch.status)
    return None


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
    if decision.layer == "target-class":
        return (
            "this resource type is refused for generic configuration mutation; "
            "administer it through its own curated path"
        )
    if decision.layer == "capability":
        return "the target Gateway does not expose the required capability"
    return "the mutation was denied by policy"


def mutation_failure(result: MutationResult) -> GatewayError | None:
    """The error a caller must see when a mutation did not cleanly apply.

    A verified success (including a recovered one) has none. Every other state is
    a refusal or an unresolved outcome, and an unresolved outcome is always
    ``outcome_unknown`` — never a silent success and never a replay licence.
    """

    if result.state in {MutationState.SUCCEEDED, MutationState.RECOVERED_SUCCESS}:
        return None
    if result.error is not None:
        return result.error
    return GatewayError(
        "outcome_unknown",
        "the mutation did not apply cleanly and its final state is unknown; it was not replayed",
    )
