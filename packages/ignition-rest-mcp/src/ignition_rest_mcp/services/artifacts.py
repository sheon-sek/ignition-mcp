"""Slice 8 (Phase 3) storage-backed Tool use cases: artifact_list,
artifact_info and operation_diagnose (D17/D19).

All three are principal-scoped: records of the same verified principal are
visible to everyone else only via ``ignition.admin`` (mirroring D19);
non-visible records answer ``not_found`` — never an existence oracle.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ignition_rest_mcp.artifacts.local import LocalArtifactStore
from ignition_rest_mcp.artifacts.model import Artifact
from ignition_rest_mcp.audit.sink import AuditRow, AuditWriteError, SqliteAuditSink
from ignition_rest_mcp.auth import Principal
from ignition_rest_mcp.config import ADMIN_SCOPE
from ignition_rest_mcp.errors import GatewayError
from ignition_rest_mcp.models import (
    ArtifactInfoResult,
    ArtifactListResult,
    ArtifactPage,
    ArtifactRefModel,
    OperationDiagnoseResult,
    OperationPhase,
)
from ignition_rest_mcp.operation import OperationContext, is_uuid7
from ignition_rest_mcp.storage.records import OperationRecord, OperationRecordStore, iso_utc

RESTRICTED = "RESTRICTED"


def artifact_visible(principal: Principal, owner_key: str) -> bool:
    """D17/D19 visibility for one artifact: its owning principal, or any holder of
    ``ignition.admin``. A caller that cannot see an artifact is answered exactly as if
    it did not exist (no existence oracle), which is why the ``project_import`` Tool
    resolves its input artifact through this rule too."""

    return owner_key == principal.key or principal.has_scope(ADMIN_SCOPE)


def _ref(artifact: Artifact) -> ArtifactRefModel:
    return ArtifactRefModel(**artifact.to_ref())


async def artifact_list(
    store: LocalArtifactStore, context: OperationContext, *, principal: Principal,
    kind: str, limit: int, offset: int,
) -> ArtifactListResult:
    # store.list validates the kind enum; the empty string means "no filter".
    kind_filter = kind.strip() or None
    items, matching = await store.list(
        principal=principal.key, allow_admin=principal.has_scope(ADMIN_SCOPE),
        kind=kind_filter, limit=limit, offset=offset,
    )
    next_offset = offset + len(items) if offset + len(items) < matching else None
    return ArtifactListResult(
        correlationId=context.correlation_id,
        items=[_ref(item) for item in items],
        page=ArtifactPage(
            total=matching, matching=matching, limit=limit, offset=offset, nextOffset=next_offset,
        ),
    )


async def artifact_info(
    store: LocalArtifactStore, audit_sink: SqliteAuditSink | None, context: OperationContext,
    *, principal: Principal, artifact_id: str, metrics: object | None = None,
) -> ArtifactInfoResult:
    try:
        artifact = await store.stat(artifact_id)
    except GatewayError:
        raise
    if not artifact_visible(principal, artifact.owner):
        # Not visible behaves exactly like non-existent (no existence oracle).
        raise GatewayError("not_found", "Artifact not found")
    if artifact.sensitivity == RESTRICTED:
        # Restricted metadata access is audited, fail-closed before returning (D18).
        if audit_sink is None:
            raise GatewayError("internal_error", "The audit subsystem is unavailable")
        try:
            await audit_sink.write(AuditRow(
                timestamp=iso_utc(datetime.now(timezone.utc)),
                correlation_id=context.correlation_id, server="ignition-rest",
                tool="artifact_info_restricted", actor=principal.key,
                operation_class="READ", destructive=False, phase="attempt", outcome="attempted",
                target_type="artifact", target_id=artifact.artifact_id,
                safe_fields={"artifactId": artifact.artifact_id, "kind": artifact.kind},
            ))
        except AuditWriteError as error:
            if metrics is not None:
                metrics.record_audit_write_failure("attempt")  # type: ignore[attr-defined]
            raise GatewayError(
                "internal_error", "The audit subsystem could not record restricted metadata access",
            ) from error
    return ArtifactInfoResult(correlationId=context.correlation_id, artifact=_ref(artifact))


def _diagnose_projection(record: OperationRecord) -> OperationDiagnoseResult:
    outcome = record.outcome if record.outcome is not None else "in_progress"
    return OperationDiagnoseResult(
        correlationId=record.correlation_id,
        tool=record.tool,
        outcome=outcome,
        errorCode=record.error_code,
        startedAt=record.started_at,
        finishedAt=record.finished_at,
        phases=[OperationPhase(name=str(phase["name"]), at=str(phase["at"])) for phase in record.phases],
        phasesTruncated=record.phases_truncated,
        transactionId=record.transaction_id,
        downstreamCorrelationId=record.downstream_correlation_id,
        auditResultMissing=record.audit_result_missing,
    )


async def operation_diagnose(
    records: OperationRecordStore, context: OperationContext, *, principal: Principal, correlation_id: str,
) -> OperationDiagnoseResult:
    if not is_uuid7(correlation_id):
        raise GatewayError("invalid_argument", "correlationId must be an exact UUIDv7 identifier")
    record = await records.fetch(correlation_id)
    if record is None or not artifact_visible(principal, record.principal_key):
        raise GatewayError("not_found", "No operation record exists for that correlationId")
    return _diagnose_projection(record)
