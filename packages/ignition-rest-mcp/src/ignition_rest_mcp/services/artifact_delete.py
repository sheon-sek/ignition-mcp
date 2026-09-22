"""D17/D30 artifact removal on the Native REST plane (Phase 4, 4c).

``artifact_delete`` removes one artifact the server holds. D30 is explicit about the
shape of this Tool, and each clause lands somewhere specific:

- "The Phase 3 runbook's planned ``DELETE /artifacts/{id}`` data-plane route is
  **dropped**; the Tool is the only delete path." The Tool therefore makes **no Gateway
  call at all**: it talks only to the D17 ``ArtifactStore``. That is why the operation
  declares ``gateway_backed=False`` — D08's capability layer has no route to check, and
  its answer is the local subsystem the invocation resolved before the chain ran.
- "Only the owning principal can delete an artifact, or ``ignition.admin``; artifacts
  the caller cannot see answer ``not_found``." The visibility check is the D08
  Precondition hook, so it runs after the Target allowlist and before the audit
  decision, the attempt and the removal, and an invisible artifact is answered exactly
  as an unknown identifier is (no existence oracle).
- "A retention-locked artifact fails with ``conflict``." The check is the store's own,
  inside the same transaction that moves the artifact into ``DELETING``, so a
  concurrent lock release cannot slip between a check and a removal.
- "Audited through the Phase 3 ordering." ``execute_local_mutation`` runs the same
  chain and writes the same ``decision`` → ``attempt`` → ``result`` rows as
  ``execute_mutation`` does for a dispatched Mutation.

Verification (D30 §6): a bounded read-back of the same identifier through the store.
A removal has no post-state to report other than absence, which is exactly what the
result publishes as Observed state. An error is never turned into a success by that
read-back: TTL cleanup, the D16 transaction and any ``ignition.admin`` remove artifacts
too, so absence after a failure cannot be attributed to this call.

Crash safety (D17): the store commits ``DELETING`` first, unlinks the object, fsyncs
the directory and finally deletes the row. Every split point is recoverable by
``reconcile``, and a ``DELETING`` artifact is never visible to a consumer in the
meantime.
"""

from __future__ import annotations

from typing import Any

from ignition_rest_mcp.artifacts.local import LocalArtifactStore, validate_artifact_id
from ignition_rest_mcp.artifacts.model import Artifact
from ignition_rest_mcp.auth import VerifiedPrincipal
from ignition_rest_mcp.config import Settings
from ignition_rest_mcp.errors import GatewayError
from ignition_rest_mcp.models import ArtifactDeleteResult
from ignition_rest_mcp.operation import OperationContext
from ignition_rest_mcp.safety.executor import (
    LocalMutationRequest,
    MutationResult,
    VerificationOutcome,
    execute_local_mutation,
    mutation_failure,
)
from ignition_rest_mcp.safety.policy import CONFIG_MUTATION, MutationOperation
from ignition_rest_mcp.services.artifacts import artifact_visible

#: D30 §6/D30 §7: a Phase 4 Mutation answers `permission_denied` for a Target outside
#: its Target allowlist, and an explicit refusal is the result of the attempt.
ARTIFACT_DELETE = MutationOperation(
    op_id="artifact_delete",
    mutation_class=CONFIG_MUTATION,
    #: D30 drops the artifact DELETE route, so the operation's capability is the local
    #: store rather than a D04 OpenAPI capability.
    capability="artifact_store",
    destructive=True,
    target_denial_code="permission_denied",
    rejection_is_final=True,
    gateway_backed=False,
)


async def artifact_delete(
    store: LocalArtifactStore,
    settings: Settings,
    context: OperationContext,
    *,
    principal: VerifiedPrincipal,
    artifact_id: str,
) -> ArtifactDeleteResult:
    """Remove one artifact the calling principal may see (D17/D30)."""

    # D10 input validation, before the chain: the identifier is the store's own key,
    # bounded by the same rule the store applies, so nothing else can reach it.
    validate_artifact_id(artifact_id)

    #: The Target identity and the audit fields are known only once the pre-state is
    #: read, so the Precondition hook fills them; `execute_local_mutation` reads both
    #: after that hook and before it writes the decision and attempt rows.
    audit_fields: dict[str, Any] = {}
    pre_state: dict[str, Artifact] = {}
    observed: dict[str, bool] = {}

    async def resolve() -> None:
        """The pre-state: a READY artifact this principal may see (D30 §6).

        The Target allowlist has already been checked against the caller's identifier,
        so a Target the deployment does not name is refused without reading the store.
        An unknown identifier and an artifact owned by another principal are answered
        identically — ``not_found``, with the denial audited — because D30 §6 forbids
        the Tool from being an existence oracle.
        """

        try:
            artifact = await store.stat(artifact_id)
        except GatewayError as error:
            if error.code != "not_found":
                raise
            raise GatewayError("not_found", "Artifact not found; nothing was removed") from error
        if not artifact_visible(principal, artifact.owner):
            raise GatewayError("not_found", "Artifact not found; nothing was removed")
        pre_state["artifact"] = artifact
        audit_fields.update({
            "kind": artifact.kind,
            "sensitivity": artifact.sensitivity,
            "retentionClass": artifact.retention_class,
        })

    async def effect() -> None:
        # D17: the retention-lock check, the DELETING transition, the unlink and the row
        # removal are the store's, so this Tool cannot invent a second delete path.
        await store.delete_internal(artifact_id)

    async def verify() -> VerificationOutcome:
        """D30 §6: a bounded read-back of the same identifier."""

        present = await store.exists(artifact_id)
        observed["present"] = present
        return VerificationOutcome.CONFIRMED if not present else VerificationOutcome.UNCHANGED

    mutation = await execute_local_mutation(
        settings=settings,
        context=context,
        request=LocalMutationRequest(
            operation=ARTIFACT_DELETE,
            principal=principal,
            #: The Target is the artifact's own storage identifier, matched exactly.
            target_id=artifact_id,
            effect=effect,
            verify=verify,
            precondition=resolve,
            refusal=_retention_refusal,
            audit_fields=audit_fields,
            target_type="artifact",
        ),
    )
    failure = _failure(mutation, observed)
    if failure is not None:
        raise failure
    artifact = pre_state["artifact"]
    if observed["present"]:  # pragma: no cover - the executor maps this to a failure
        # A last guard: the executor's own mapping already turns a read-back that
        # contradicts the removal into an error, so a result can never claim an
        # absence the read-back did not establish.
        raise GatewayError(
            "outcome_unknown",
            "the bounded read-back still reports the artifact; nothing was replayed",
        )
    return ArtifactDeleteResult(
        correlationId=context.correlation_id,
        artifactId=artifact.artifact_id,
        kind=artifact.kind,
        #: The Observed state a removal leaves: the read-back found no READY artifact.
        present=False,
    )


def _retention_refusal(error: GatewayError) -> GatewayError | None:
    """The store's own refusal of one removal (D17/D30).

    ``delete_internal`` raises ``conflict`` inside the transaction that checks the
    retention lock, so the artifact is untouched and D30 §7's mapping applies as
    written: ``conflict``. Any other error is not a refusal the operation recognises —
    it is the caller's error, and the executor reports it as such.
    """

    if error.code != "conflict":
        return None
    return GatewayError(
        "conflict",
        "the artifact is retention-locked and cannot be removed: only the owning D16 "
        "transaction releases a RECOVERY artifact's lock, and no read-back turns the "
        "refusal into a removal",
        error.status_code,
    )


def _failure(result: MutationResult, observed: dict[str, bool]) -> GatewayError | None:
    """The error a caller must see when the removal did not cleanly apply.

    What the bounded read-back observed is named when the call got that far: a caller
    deciding what to do next needs to know whether the artifact was still there when
    the Tool last looked.
    """

    failure = mutation_failure(result)
    if failure is None:
        return None
    if "present" not in observed:
        return failure
    seen = "still reports a READY artifact" if observed["present"] else "no longer reports one"
    return GatewayError(
        failure.code,
        f"{failure.message} (the bounded read-back of the same identifier {seen})",
        failure.status_code,
    )
