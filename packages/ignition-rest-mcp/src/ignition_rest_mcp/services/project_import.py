"""D30/D16 Project import on the Native REST plane (Phase 4, milestone 4c).

``project_import`` is the Phase 4 Tool over the Phase 3 D16 transaction machinery. It
adds no import path of its own: it resolves and validates the caller's archive, runs
the D08 chain *before* the transaction does any work, and then hands the archive to
``ProjectTransactionService`` as candidate B.

Where each rule lands:

- **D08/D30 §3 Preflight.** The whole chain — verified principal, authorization scope,
  deployment class, operation allowlist, Target allowlist and capability — runs before
  the writer lock, before the baseline export and before anything is staged, and it
  runs through the same ``preflight_mutation`` the guarded executor uses, so the
  audited reason and the caller's error code cannot drift between the two. A denied
  Target therefore answers ``permission_denied`` (D30 §7) without exporting a Project
  the deployment said not to touch and without leaving a transaction row.
- **D30 §2 Precondition.** ``expectedFingerprint`` is the caller's Project fingerprint,
  the ``pcf1`` token ``project_export`` reports. The transaction compares it with
  baseline A and refuses with ``conflict`` before it stages a candidate, backs anything
  up or dispatches. The residual race window between that comparison and the pre-import
  re-export is D16's documented final import race, narrowed by that re-export and not
  claimed to be closed.
- **D16 reconcile.** The tool reports the transaction's terminal state as data for a
  satisfied outcome (``COMMITTED``, ``NO_CHANGE``) and raises the D06 taxonomy error for
  every other terminal state, because D06 routes execution failure through Tool error
  semantics. ``recovered_success`` is reachable only the way D16 says it is: an
  ambiguous dispatch (possibly sent, no response, or 5xx) whose post-import export C
  equals the staged candidate B, which proves the import landed. An *explicit* Gateway
  rejection is final for this Tool (``PROJECT_IMPORT_TOOL_OPERATION.rejection_is_final``,
  D30 §2), so a rejected dispatch can never be reconciled into a success even if the
  Project happens to show B — another writer could have produced it.
- **D17/D30 §6 artifact input.** The archive is a READY artifact visible to the same
  Mutation principal; anything else answers ``not_found`` and a non-archive kind is
  ``invalid_argument``. D30 §6 names a ``project_archive``; D17 names server-produced
  exports as a legitimate binary ingress source, so both Project-archive kinds are
  accepted, and both have passed the D15 ZIP gate before they became READY. The bytes
  are re-validated (D15) and fingerprinted as candidate B inside the transaction, so an
  unsafe archive fails with ``invalid_argument`` before any dispatch.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from ignition_rest_mcp.artifacts.local import LocalArtifactStore, validate_artifact_id
from ignition_rest_mcp.artifacts.model import Artifact
from ignition_rest_mcp.auth import VerifiedPrincipal
from ignition_rest_mcp.capabilities.registry import CapabilityRegistry
from ignition_rest_mcp.config import Settings
from ignition_rest_mcp.errors import GatewayError
from ignition_rest_mcp.models import ProjectImportResult
from ignition_rest_mcp.operation import OperationContext
from ignition_rest_mcp.projects.capture import validate_project_name
from ignition_rest_mcp.projects.transactions import (
    PROJECT_IMPORT_TOOL_OPERATION,
    ArtifactCandidateBuilder,
    ProjectTransactionService,
    TransactionResult,
    TransactionState,
)
from ignition_rest_mcp.safety.executor import MutationPreflight, preflight_mutation
from ignition_rest_mcp.services.artifacts import artifact_visible
from ignition_rest_mcp.storage.database import StorageUnavailable
from ignition_rest_mcp.storage.records import OperationRecordStore

LOGGER = logging.getLogger("ignition_rest_mcp.project_import")

#: The D16 Project fingerprint shape (``project-content-v1`` -> ``pcf1:<64 hex>``).
FINGERPRINT_RE = re.compile(r"^pcf1:[0-9a-f]{64}$")

#: Project-archive artifact kinds this Tool consumes (see the module docstring).
IMPORT_ARTIFACT_KINDS = frozenset({"project_archive", "project_export"})

#: The terminal states a satisfied call reports as data; every other D16 terminal state
#: is a Tool error (D06) carrying the D30 §7 code.
SATISFIED_STATES = frozenset({TransactionState.COMMITTED, TransactionState.NO_CHANGE})


async def project_import(
    transactions: ProjectTransactionService,
    store: LocalArtifactStore,
    registry: CapabilityRegistry,
    settings: Settings,
    records: OperationRecordStore,
    context: OperationContext,
    *,
    principal: VerifiedPrincipal,
    project_name: str,
    artifact_id: str,
    expected_fingerprint: str,
    metrics: Any = None,
) -> ProjectImportResult:
    """Import one caller-supplied Project archive into an existing Project."""

    name = validate_project_name(project_name)
    validate_artifact_id(artifact_id)
    fingerprint = _expected_fingerprint(expected_fingerprint)
    await preflight_mutation(
        registry=registry, settings=settings, context=context,
        preflight=MutationPreflight(
            operation=PROJECT_IMPORT_TOOL_OPERATION, principal=principal, target_id=name,
            target_type="project", audit_fields={"projectName": name},
        ),
    )
    source = await _import_archive(store, principal, artifact_id)
    result = await transactions.execute(
        project_name=name, builder=ArtifactCandidateBuilder(store, source.artifact_id),
        context=context, principal=principal, operation=PROJECT_IMPORT_TOOL_OPERATION,
        expected_fingerprint=fingerprint,
    )
    await _link_transaction(records, context, result, metrics)
    failure = _failure(result)
    if failure is not None:
        raise failure
    return ProjectImportResult(
        correlationId=context.correlation_id,
        projectName=result.project_name,
        transactionId=result.transaction_id,
        state=result.state.value,
        baselineFingerprint=_fingerprint(result.baseline_fingerprint, name, "baseline"),
        candidateFingerprint=_fingerprint(result.candidate_fingerprint, name, "candidate"),
        resultFingerprint=result.result_fingerprint,
        importDispatched=result.import_dispatched,
        designerWarning=result.designer_warning,
    )


def _expected_fingerprint(value: str) -> str:
    """The caller's Precondition token, checked for shape before any work.

    A malformed token cannot match any Project, so it is an input error rather than a
    transaction that is created only to end ``CONFLICTED``.
    """

    if not isinstance(value, str) or FINGERPRINT_RE.fullmatch(value) is None:
        raise GatewayError(
            "invalid_argument",
            "expectedFingerprint must be the pcf1 fingerprint of the Project's current "
            "content, exactly as project_export reported it",
        )
    return value


async def _import_archive(
    store: LocalArtifactStore, principal: VerifiedPrincipal, artifact_id: str,
) -> Artifact:
    """The READY Project archive the caller may consume (D30 §6/D17).

    Visibility is checked before the kind, so an artifact the caller cannot see answers
    exactly as a missing one does (no existence oracle), and a visible artifact that is
    not a Project archive is an input error.
    """

    artifact = await store.stat(artifact_id)
    if not artifact_visible(principal, artifact.owner):
        raise GatewayError("not_found", "Artifact not found")
    if artifact.kind not in IMPORT_ARTIFACT_KINDS:
        raise GatewayError(
            "invalid_argument",
            f"artifactId must name a Project archive artifact; {artifact.artifact_id} is a "
            f"{artifact.kind}",
        )
    return artifact


async def _link_transaction(
    records: OperationRecordStore, context: OperationContext, result: TransactionResult,
    metrics: Any,
) -> None:
    """D19: name the Project transaction on the caller's operation record.

    The transaction outlives the call, so a caller that received an error still has a
    way back to it through ``operation_diagnose``. The record is diagnostics: a local
    storage failure must never turn an import that ran into a failed call.
    """

    try:
        await records.set_transaction(context.correlation_id, result.transaction_id)
    except StorageUnavailable:
        if metrics is not None:
            metrics.record_operation_record_write_failure("transaction")
        LOGGER.error(
            "Operation record could not be linked to its Project transaction",
            extra={
                "event": "operation_record_failure", "correlationId": context.correlation_id,
                "tool": context.tool, "outcome": "error", "errorCode": "storage_unavailable",
            },
        )


def _failure(result: TransactionResult) -> GatewayError | None:
    """The error a caller must see for every terminal state but a satisfied one.

    The transaction id and the terminal state are named in the message so the caller can
    follow the transaction it produced; the code is the D30 §7 one the state maps to.
    """

    if result.state in SATISFIED_STATES:
        return None
    error = result.error or GatewayError(
        "outcome_unknown",
        "the Project import did not apply cleanly and its final state is unknown; "
        "it was not and must not be replayed",
    )
    return GatewayError(
        error.code,
        f"{error.message} (project transaction {result.transaction_id} ended {result.state.value})",
        error.status_code,
    )


def _fingerprint(value: str | None, name: str, label: str) -> str:
    if value is None:  # pragma: no cover - a satisfied state always captured both
        raise GatewayError(
            "internal_error",
            f"project {name!r} reached a satisfied terminal state without a {label} fingerprint",
        )
    return value
