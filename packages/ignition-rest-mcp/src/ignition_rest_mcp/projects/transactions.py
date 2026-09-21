"""D16 Project transaction machinery (internal; no public surface in Phase 3).

Protocol (canonical order, D16 / plan slice 7):

    writer lock (per Gateway ID + project) -> Designer-session policy
    -> export baseline A (EPHEMERAL, pcf1 fingerprint)
    -> build candidate B (EPHEMERAL, streamed through the ArtifactStore)
    -> fingerprint(B) == fingerprint(A)  =>  NO_CHANGE (no backup, no import)
    -> validate/promote A to locked RECOVERY (failure => FAILED_PRE_IMPORT)
    -> fresh export A' ; fingerprint(A') != fingerprint(A)  =>  CONFLICTED
       (importAttempted = false)
    -> import B exactly once through the GUARDED EXECUTOR (never replayed)
    -> fresh export C ; reconcile: C==B => COMMITTED (ambiguous dispatches that
       reconcile to the candidate are recorded as recovered success);
       C==A => NOT_APPLIED;  claimed-but-wrong => RECOVERY_REQUIRED;
       ambiguous-mismatch/unobtainable => OUTCOME_UNKNOWN
    -> recovery-lock release set: COMMITTED / NOT_APPLIED / CONFLICTED /
       FAILED_PRE_IMPORT release (7-day TTL starts); OUTCOME_UNKNOWN and
       RECOVERY_REQUIRED stay locked and additionally preserve B and C as
       locked RECOVERY artifacts.

There is no automatic rollback, merge or replay. Restart reconciliation never
re-imports: rows in IMPORT_SENT/VERIFYING are resolved by a bounded read-only
re-export comparison. Every row transition is persisted with timestamps.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
import logging
from typing import Any, AsyncIterator
from urllib.parse import quote

from ignition_rest_mcp.artifacts.local import LocalArtifactStore
from ignition_rest_mcp.artifacts.model import ArtifactReader, ArtifactWriter
from ignition_rest_mcp.auth import VerifiedPrincipal
from ignition_rest_mcp.capabilities.registry import CapabilityRegistry
from ignition_rest_mcp.client.gateway import GatewayClient, WriteDispatchResult
from ignition_rest_mcp.config import Settings
from ignition_rest_mcp.errors import GatewayError
from ignition_rest_mcp.operation import OperationContext, uuid7
from ignition_rest_mcp.projects.capture import CapturedProject, capture_project, validate_project_name
from ignition_rest_mcp.projects.designers import active_sessions_for_project, enforce_policy
from ignition_rest_mcp.projects.identity import GatewayIdentity
from ignition_rest_mcp.projects.locks import ProjectLockRegistry
from ignition_rest_mcp.safety.executor import (
    MutationRequest,
    MutationResult,
    MutationState,
    VerificationOutcome,
    execute_mutation,
)
from ignition_rest_mcp.safety.policy import CONFIG_MUTATION, MutationOperation
from ignition_rest_mcp.storage.database import Database, transaction
from ignition_rest_mcp.storage.records import iso_utc, utc_now

LOGGER = logging.getLogger("ignition_rest_mcp.projects")

PROJECT_IMPORT_OPERATION = MutationOperation(
    op_id="project_import",
    mutation_class=CONFIG_MUTATION,
    capability="project_import",
    destructive=True,
)


class TransactionState(str, Enum):
    PREPARING = "PREPARING"
    BASELINE_CAPTURED = "BASELINE_CAPTURED"
    CANDIDATE_VALIDATED = "CANDIDATE_VALIDATED"
    BACKUP_PERSISTED = "BACKUP_PERSISTED"
    CONCURRENCY_VERIFIED = "CONCURRENCY_VERIFIED"
    IMPORT_SENT = "IMPORT_SENT"
    VERIFYING = "VERIFYING"
    COMMITTED = "COMMITTED"
    NO_CHANGE = "NO_CHANGE"
    CONFLICTED = "CONFLICTED"
    FAILED_PRE_IMPORT = "FAILED_PRE_IMPORT"
    NOT_APPLIED = "NOT_APPLIED"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


NON_TERMINAL = frozenset({
    TransactionState.PREPARING, TransactionState.BASELINE_CAPTURED,
    TransactionState.CANDIDATE_VALIDATED, TransactionState.BACKUP_PERSISTED,
    TransactionState.CONCURRENCY_VERIFIED, TransactionState.IMPORT_SENT,
    TransactionState.VERIFYING,
})
PRE_IMPORT_STATES = NON_TERMINAL - {TransactionState.IMPORT_SENT, TransactionState.VERIFYING}
LOCK_RELEASED_ON = frozenset({
    TransactionState.COMMITTED, TransactionState.NOT_APPLIED,
    TransactionState.CONFLICTED, TransactionState.FAILED_PRE_IMPORT,
})
LOCK_HELD_ON = frozenset({TransactionState.OUTCOME_UNKNOWN, TransactionState.RECOVERY_REQUIRED})


@dataclass(frozen=True, slots=True)
class TransactionResult:
    transaction_id: str
    state: TransactionState
    project_name: str
    baseline_artifact_id: str | None
    baseline_fingerprint: str | None
    candidate_artifact_id: str | None
    candidate_fingerprint: str | None
    result_fingerprint: str | None
    import_dispatched: bool
    external_drift_detected: bool
    designer_warning: bool
    error: GatewayError | None


class CandidateBuilder:
    """D15 contract: reads baseline A only as a bounded stream and writes B only
    through a store-managed EPHEMERAL writer; can never hold whole archives.
    Phase 3 ships no production builder (tests/harness only)."""

    async def build(self, baseline: ArtifactReader, out: ArtifactWriter) -> None:  # pragma: no cover
        raise NotImplementedError


class ProjectTransactionService:
    def __init__(
        self, *, client: GatewayClient, registry: CapabilityRegistry, store: LocalArtifactStore,
        settings: Settings, locks: ProjectLockRegistry, identity: GatewayIdentity, db: Database,
    ) -> None:
        self._client = client
        self._registry = registry
        self._store = store
        self._settings = settings
        self._locks = locks
        self._identity = identity
        self._db = db

    # ------------------------------------------------------------------ rows

    async def _insert(self, txn_id: str, context: OperationContext, project_name: str) -> None:
        now = iso_utc(utc_now())

        def _write(conn: Any) -> None:
            with transaction(conn):
                conn.execute(
                    "INSERT INTO project_transactions (transaction_id, gateway_id, gateway_id_derived,"
                    " project_name, state, correlation_id, principal_key, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?)",
                    (txn_id, self._identity.key, 1 if self._identity.derived else 0, project_name,
                     TransactionState.PREPARING.value, context.correlation_id, context.actor, now, now),
                )

        await self._db.run(_write)

    async def _update(self, txn_id: str, state: TransactionState, **fields: Any) -> None:
        columns = ["state"]
        values: list[Any] = [state.value]
        mapping = {
            "baseline_artifact": "baseline_artifact_id", "baseline_fingerprint": "baseline_fingerprint",
            "candidate_artifact": "candidate_artifact_id", "candidate_fingerprint": "candidate_fingerprint",
            "precheck_artifact": "precheck_artifact_id", "result_artifact": "result_artifact_id",
            "result_fingerprint": "result_fingerprint", "import_dispatched": "import_dispatched",
            "import_outcome": "import_outcome", "import_status": "import_status",
            "external_drift": "external_drift_detected", "designer_warning": "designer_warning",
            "error_code": "error_code",
        }
        for key, column in mapping.items():
            if key in fields:
                columns.append(column)
                value = fields[key]
                values.append(1 if value is True else 0 if value is False else value)
        columns.append("updated_at")
        values.append(iso_utc(utc_now()))
        values.append(txn_id)

        def _write(conn: Any) -> None:
            with transaction(conn):
                conn.execute(
                    f"UPDATE project_transactions SET {', '.join(c + ' = ?' for c in columns)}"
                    " WHERE transaction_id = ?",
                    values,
                )

        await self._db.run(_write)

    # ------------------------------------------------------------------ execute

    async def execute(
        self, *, project_name: str, builder: CandidateBuilder, context: OperationContext,
        principal: VerifiedPrincipal,
    ) -> TransactionResult:
        if not self._settings.project_writer_enabled:
            raise GatewayError(
                "operation_disabled",
                "the Project writer is disabled for this deployment "
                "(IGNITION_MCP_PROJECT_WRITER_ENABLED=false)",
            )
        name = await self._canonicalize_name(validate_project_name(project_name), context)
        lock_key = self._identity.key  # writer-enabled config validation guarantees explicit ID

        async with self._locks.acquire(lock_key, name):
            txn_id = uuid7()
            await self._insert(txn_id, context, name)
            return await self._run_locked(txn_id, name, builder, context, principal)

    async def _run_locked(
        self, txn_id: str, name: str, builder: CandidateBuilder,
        context: OperationContext, principal: VerifiedPrincipal,
    ) -> TransactionResult:
        baseline: CapturedProject | None = None
        candidate: CapturedProject | None = None
        precheck: CapturedProject | None = None
        result_capture: CapturedProject | None = None
        deadline = self._settings.budget_deadline_seconds("ARTIFACT")

        async def cleanup_ephemerals(*ids: str | None) -> None:
            for artifact_id in ids:
                if artifact_id:
                    try:
                        await self._store.delete_internal(artifact_id)
                    except GatewayError:
                        pass  # janitor/reconcile converges leftovers

        try:
            # Designer-session policy (config-only, never caller-overridable).
            designer_warning = False
            policy = self._settings.project_designer_policy
            if policy != "ignore":
                sessions = await active_sessions_for_project(self._client, self._registry, context, name)
                if sessions:
                    if policy == "deny":
                        enforce_policy(policy, sessions, name)  # raises conflict
                    designer_warning = True
                    LOGGER.warning(
                        "Active Designer sessions on project during guarded transaction",
                        extra={"event": "designer_sessions", "outcome": "warn", "correlationId": context.correlation_id},
                    )
            await self._update(txn_id, TransactionState.PREPARING, designer_warning=designer_warning)

            # Baseline A (EPHEMERAL until promoted to locked RECOVERY).
            baseline = await capture_project(
                self._client, self._store, context, project_name=name,
                gateway_id=self._identity.key, retention_class="EPHEMERAL", deadline_seconds=deadline,
            )
            await self._update(
                txn_id, TransactionState.BASELINE_CAPTURED,
                baseline_artifact=baseline.artifact.artifact_id, baseline_fingerprint=baseline.fingerprint,
            )

            # Candidate B through the bounded builder contract only.
            candidate_writer = await self._store.create(
                kind="project_archive", sensitivity="CONFIDENTIAL", retention_class="EPHEMERAL",
                owner=context.actor, filename=f"{name}-candidate.zip", media_type="application/zip",
                correlation_id=context.correlation_id, gateway_id=self._identity.key,
                project_name=name, transaction_id=txn_id,
            )
            candidate_published = False
            try:
                reader = await self._store.open_read(baseline.artifact.artifact_id)
                try:
                    await builder.build(reader, candidate_writer)
                finally:
                    await reader.close()
                candidate_fingerprint = await self._fingerprint_staged(candidate_writer)
                candidate_artifact = await self._store.publish(candidate_writer)
                candidate_published = True
            finally:
                if not candidate_published:
                    # A cancelled or failed build must never leave staged bytes.
                    try:
                        await asyncio.shield(candidate_writer.abort())
                    except Exception:  # pragma: no cover - cancellation hygiene
                        pass
            candidate = CapturedProject(artifact=candidate_artifact, fingerprint=candidate_fingerprint)
            await self._update(
                txn_id, TransactionState.CANDIDATE_VALIDATED,
                candidate_artifact=candidate_artifact.artifact_id, candidate_fingerprint=candidate_fingerprint,
            )

            # D16 no-op idempotency: semantic equality short-circuits everything.
            if candidate_fingerprint == baseline.fingerprint:
                await cleanup_ephemerals(baseline.artifact.artifact_id, candidate_artifact.artifact_id)
                await self._update(txn_id, TransactionState.NO_CHANGE, error_code=None)
                return self._result(txn_id, TransactionState.NO_CHANGE, name, baseline, candidate,
                                    None, designer_warning=designer_warning)

            # Durable recovery snapshot BEFORE any mutation.
            try:
                await self._store.promote_recovery(baseline.artifact.artifact_id, txn_id)
            except GatewayError as error:
                await cleanup_ephemerals(candidate_artifact.artifact_id, baseline.artifact.artifact_id)
                await self._update(txn_id, TransactionState.FAILED_PRE_IMPORT, error_code=error.code)
                return self._result(txn_id, TransactionState.FAILED_PRE_IMPORT, name, baseline, candidate,
                                    None, error=error, designer_warning=designer_warning)
            await self._update(txn_id, TransactionState.BACKUP_PERSISTED)

            # Mandatory pre-import re-export comparison.
            precheck = await capture_project(
                self._client, self._store, context, project_name=name,
                gateway_id=self._identity.key, retention_class="EPHEMERAL", deadline_seconds=deadline,
            )
            if precheck.fingerprint != baseline.fingerprint:
                await self._store.release_retention(baseline.artifact.artifact_id)
                await cleanup_ephemerals(precheck.artifact.artifact_id, candidate_artifact.artifact_id)
                conflict_error = GatewayError(
                    "conflict",
                    "the project changed after the baseline export; the import was not attempted "
                    f"(baseline {baseline.fingerprint} vs current {precheck.fingerprint})",
                )
                await self._update(txn_id, TransactionState.CONFLICTED,
                                   error_code="conflict", import_dispatched=False,
                                   precheck_artifact=precheck.artifact.artifact_id)
                return self._result(txn_id, TransactionState.CONFLICTED, name, baseline, candidate,
                                    None, error=conflict_error, import_dispatched=False,
                                    designer_warning=designer_warning)
            await self._update(txn_id, TransactionState.CONCURRENCY_VERIFIED,
                               precheck_artifact=precheck.artifact.artifact_id)

            # Exactly-once import through the guarded executor.
            await self._update(txn_id, TransactionState.IMPORT_SENT, import_dispatched=True)
            verify_state: dict[str, Any] = {}

            async def verify(_dispatch: WriteDispatchResult) -> VerificationOutcome:
                await self._update(txn_id, TransactionState.VERIFYING)
                try:
                    captured = await capture_project(
                        self._client, self._store, context, project_name=name,
                        gateway_id=self._identity.key, retention_class="EPHEMERAL",
                        deadline_seconds=self._settings.project_verification_timeout_seconds,
                    )
                except (GatewayError, asyncio.CancelledError):
                    raise
                except Exception:
                    return VerificationOutcome.INDETERMINATE
                verify_state["captured"] = captured
                if captured.fingerprint == (candidate_fingerprint):
                    return VerificationOutcome.CONFIRMED
                if captured.fingerprint == baseline.fingerprint:
                    return VerificationOutcome.UNCHANGED
                return VerificationOutcome.MISMATCH

            mutation: MutationResult = await execute_mutation(
                client=self._client, registry=self._registry, settings=self._settings, context=context,
                request=MutationRequest(
                    operation=PROJECT_IMPORT_OPERATION, principal=principal, target_id=name,
                    request_path=f"/data/api/v1/projects/import/{quote(name, safe='')}",
                    params={"overwrite": "true"},
                    body_chunks=_artifact_chunks(self._store, candidate_artifact.artifact_id),
                    content_type="application/zip",
                    dispatch_deadline_seconds=deadline,
                    verification_deadline_seconds=self._settings.project_verification_timeout_seconds,
                    verify=verify, precondition=None,
                    audit_fields={"projectName": name}, target_type="project",
                ),
            )
            dispatch = mutation.dispatch
            result_capture = verify_state.get("captured")
            if isinstance(result_capture, CapturedProject):
                await self._update(txn_id, TransactionState.VERIFYING,
                                   result_artifact=result_capture.artifact.artifact_id,
                                   result_fingerprint=result_capture.fingerprint)
            final, mapped_error = self._map_mutation(mutation)
            external_drift = False
            import_dispatched = True
            if final is TransactionState.NOT_APPLIED and mutation.state is MutationState.NOT_SENT:
                # Known non-attempt: a diagnostic re-export must not hide drift.
                import_dispatched = False
                external_drift = await self._diagnose_drift(name, baseline, context)
                await self._store.release_retention(baseline.artifact.artifact_id)
                await cleanup_ephemerals(
                    precheck.artifact.artifact_id if precheck else None,
                    candidate_artifact.artifact_id,
                    result_capture.artifact.artifact_id if result_capture else None,
                )
            elif final in LOCK_RELEASED_ON:
                await self._store.release_retention(baseline.artifact.artifact_id)
                await cleanup_ephemerals(
                    precheck.artifact.artifact_id if precheck else None,
                    candidate_artifact.artifact_id,
                    result_capture.artifact.artifact_id if result_capture else None,
                )
            else:  # LOCK_HELD_ON: preserve baseline + candidate + result as locked RECOVERY
                preserved = [candidate_artifact.artifact_id]
                if result_capture is not None:
                    preserved.append(result_capture.artifact.artifact_id)
                await self._preserve_for_recovery(txn_id, *preserved)
                await cleanup_ephemerals(precheck.artifact.artifact_id if precheck else None)
            await self._update(
                txn_id, final, error_code=mapped_error.code if mapped_error else None,
                import_dispatched=import_dispatched, external_drift=external_drift,
                import_outcome=dispatch.outcome.value if dispatch else None,
                import_status=dispatch.status if dispatch else None,
            )
            return self._result(txn_id, final, name, baseline, candidate, result_capture,
                                error=mapped_error, import_dispatched=import_dispatched,
                                external_drift=external_drift, designer_warning=designer_warning)
        except asyncio.CancelledError:
            # Persisted state decides on restart; never replay, never fake-clean.
            await cleanup_ephemerals(precheck.artifact.artifact_id if precheck else None)
            raise
        except GatewayError as error:
            # Executor policy denials and audit fail-closed raises happen BEFORE
            # any byte of the import left this process, so a row still sitting in
            # IMPORT_SENT after a raise is a known non-attempt: finalize it as
            # FAILED_PRE_IMPORT (a release-set state) and propagate. A VERIFYING
            # row means dispatch already returned and verification died — leave
            # the row for restart reconciliation and propagate unchanged.
            row_state = await self._row_state(txn_id)
            if row_state is TransactionState.VERIFYING:
                raise
            if row_state is TransactionState.IMPORT_SENT:
                await self._update(txn_id, TransactionState.FAILED_PRE_IMPORT,
                                   error_code=error.code, import_dispatched=False)
                if baseline is not None:
                    try:
                        await self._store.release_retention(baseline.artifact.artifact_id)
                    except GatewayError:
                        pass
                await cleanup_ephemerals(candidate.artifact.artifact_id if candidate else None,
                                         precheck.artifact.artifact_id if precheck else None)
                raise
            if baseline is not None and row_state not in {TransactionState.PREPARING, None}:
                try:
                    await self._store.release_retention(baseline.artifact.artifact_id)
                except GatewayError:
                    pass
            await cleanup_ephemerals(
                baseline.artifact.artifact_id if baseline and not self._promoted(row_state) else None,
                candidate.artifact.artifact_id if candidate else None,
                precheck.artifact.artifact_id if precheck else None,
            )
            await self._update(txn_id, TransactionState.FAILED_PRE_IMPORT,
                               error_code=error.code, import_dispatched=False)
            return self._result(txn_id, TransactionState.FAILED_PRE_IMPORT, name, baseline, candidate,
                                None, error=error)

    # ------------------------------------------------------------------ helpers

    def _promoted(self, state: TransactionState | None) -> bool:
        return state in {
            TransactionState.BACKUP_PERSISTED, TransactionState.CONCURRENCY_VERIFIED,
            TransactionState.IMPORT_SENT, TransactionState.VERIFYING,
        }

    async def _row_state(self, txn_id: str) -> TransactionState | None:
        def _read(conn: Any) -> str | None:
            row = conn.execute(
                "SELECT state FROM project_transactions WHERE transaction_id = ?", (txn_id,),
            ).fetchone()
            return str(row[0]) if row else None

        value = await self._db.run(_read)
        return TransactionState(value) if value else None

    async def _canonicalize_name(self, name: str, context: OperationContext) -> str:
        """Exact (case-sensitive) match against a bounded project_list lookup."""
        offset = 0
        while offset <= 1_000_000:
            payload = await self._client.get_json(
                "/data/api/v1/projects/list", params={"limit": 500, "offset": offset}, context=context,
            )
            items = payload.get("items")
            metadata = payload.get("metadata")
            if not isinstance(items, list) or not isinstance(metadata, dict):
                raise GatewayError("schema_mismatch", "project listing has an unknown shape")
            for item in items:
                if isinstance(item, dict) and item.get("name") == name:
                    return name
            matching = metadata.get("matching")
            if not isinstance(matching, int) or isinstance(matching, bool):
                raise GatewayError("schema_mismatch", "project listing metadata is invalid")
            offset += 500
            if offset >= matching or not items:
                break
        raise GatewayError("not_found", f"project {name!r} does not exist on the Gateway (exact-name match)")

    async def _fingerprint_staged(self, writer: Any) -> str:
        from ignition_rest_mcp.projects.fingerprint import project_fingerprint

        return await asyncio.to_thread(project_fingerprint, self._store.staged_path(writer))

    def _map_mutation(self, mutation: MutationResult) -> tuple[TransactionState, GatewayError | None]:
        state = mutation.state
        if state is MutationState.SUCCEEDED:
            return TransactionState.COMMITTED, None
        if state is MutationState.RECOVERED_SUCCESS:
            return TransactionState.COMMITTED, None
        if state is MutationState.NOT_APPLIED:
            return TransactionState.NOT_APPLIED, mutation.error
        if state is MutationState.REJECTED:
            return TransactionState.NOT_APPLIED, mutation.error
        if state is MutationState.NOT_SENT:
            return TransactionState.NOT_APPLIED, mutation.error
        if state is MutationState.OUTCOME_UNKNOWN:
            return TransactionState.OUTCOME_UNKNOWN, mutation.error
        return TransactionState.RECOVERY_REQUIRED, mutation.error

    async def _diagnose_drift(self, name: str, baseline: CapturedProject, context: OperationContext) -> bool:
        try:
            current = await capture_project(
                self._client, self._store, context, project_name=name,
                gateway_id=self._identity.key, retention_class="EPHEMERAL",
                deadline_seconds=self._settings.project_verification_timeout_seconds,
            )
        except (GatewayError, asyncio.CancelledError):
            raise
        except Exception:
            return False
        try:
            await self._store.delete_internal(current.artifact.artifact_id)
        except GatewayError:
            pass
        return current.fingerprint != baseline.fingerprint

    async def _preserve_for_recovery(self, txn_id: str, *artifact_ids: str) -> None:
        for artifact_id in artifact_ids:
            try:
                await self._store.promote_recovery(artifact_id, txn_id)
            except GatewayError as error:
                LOGGER.error(
                    "Could not promote transaction artifact for recovery",
                    extra={"event": "recovery_promotion", "outcome": "error", "errorCode": error.code},
                )

    def _result(
        self, txn_id: str, state: TransactionState, name: str,
        baseline: CapturedProject | None, candidate: CapturedProject | None,
        result_capture: CapturedProject | None, *, error: GatewayError | None = None,
        import_dispatched: bool = True, external_drift: bool = False, designer_warning: bool = False,
    ) -> TransactionResult:
        return TransactionResult(
            transaction_id=txn_id, state=state, project_name=name,
            baseline_artifact_id=baseline.artifact.artifact_id if baseline else None,
            baseline_fingerprint=baseline.fingerprint if baseline else None,
            candidate_artifact_id=candidate.artifact.artifact_id if candidate else None,
            candidate_fingerprint=candidate.fingerprint if candidate else None,
            result_fingerprint=result_capture.fingerprint if result_capture else None,
            import_dispatched=import_dispatched, external_drift_detected=external_drift,
            designer_warning=designer_warning, error=error,
        )

    # ------------------------------------------------------------------ restart

    async def reconcile_interrupted(self, *, batch: int, per_txn_seconds: float) -> int:
        """Bounded read-only reconciliation; never replays an import."""

        def _rows(conn: Any) -> list[dict[str, Any]]:
            placeholders = ",".join("?" for _ in NON_TERMINAL)
            curs = conn.execute(
                f"SELECT transaction_id, gateway_id, project_name, state, principal_key,"
                f" baseline_artifact_id, baseline_fingerprint, candidate_artifact_id, candidate_fingerprint"
                f" FROM project_transactions WHERE state IN ({placeholders}) ORDER BY updated_at LIMIT ?",
                (*[s.value for s in sorted(NON_TERMINAL, key=lambda item: item.value)], batch),
            )
            keys = ("transaction_id", "gateway_id", "project_name", "state", "principal_key",
                    "baseline_artifact_id", "baseline_fingerprint", "candidate_artifact_id", "candidate_fingerprint")
            return [dict(zip(keys, row, strict=True)) for row in curs.fetchall()]

        rows = await self._db.run(_rows)
        processed = 0
        for row in rows:
            processed += 1
            try:
                await asyncio.wait_for(self._reconcile_row(row), per_txn_seconds)
            except asyncio.TimeoutError:
                LOGGER.warning(
                    "Transaction reconciliation deadline exceeded; retrying next pass",
                    extra={"event": "txn_reconcile", "outcome": "timeout"},
                )
            except GatewayError as error:
                LOGGER.warning(
                    "Transaction reconciliation attempt deferred",
                    extra={"event": "txn_reconcile", "outcome": "deferred", "errorCode": error.code},
                )
        return processed

    async def _reconcile_row(self, row: dict[str, Any]) -> None:
        txn_id = str(row["transaction_id"])
        state = TransactionState(str(row["state"]))
        name = str(row["project_name"])
        baseline_id = row["baseline_artifact_id"]
        baseline_fp = row["baseline_fingerprint"]
        candidate_id = row["candidate_artifact_id"]
        candidate_fp = row["candidate_fingerprint"]

        if state in PRE_IMPORT_STATES:
            if baseline_id:
                # release_retention only affects RECOVERY rows; harmless otherwise.
                await self._store.release_retention(str(baseline_id))
            for artifact_id in (str(baseline_id) if baseline_id else None,
                                str(candidate_id) if candidate_id else None):
                if artifact_id:
                    try:
                        await self._store.delete_internal(artifact_id)
                    except GatewayError:
                        pass
            await self._update(txn_id, TransactionState.FAILED_PRE_IMPORT,
                               error_code="interrupted", import_dispatched=False)
            return

        # IMPORT_SENT / VERIFYING: possibly dispatched -> bounded read-only compare.
        lock_key = self._identity.key
        async with self._locks.acquire(lock_key, name):
            context = OperationContext.start("project_import", str(row["principal_key"]), "ARTIFACT")
            context.transaction_id = txn_id
            try:
                current = await capture_project(
                    self._client, self._store, context, project_name=name,
                    gateway_id=lock_key, retention_class="EPHEMERAL",
                    deadline_seconds=self._settings.project_verification_timeout_seconds,
                )
            except GatewayError:
                await self._preserve_unknown(txn_id, state, baseline_id, candidate_id)
                return
            if candidate_fp and current.fingerprint == candidate_fp:
                if baseline_id:
                    await self._store.release_retention(str(baseline_id))
                for artifact_id in (str(candidate_id) if candidate_id else None, current.artifact.artifact_id):
                    if artifact_id:
                        try:
                            await self._store.delete_internal(artifact_id)
                        except GatewayError:
                            pass
                await self._update(txn_id, TransactionState.COMMITTED, error_code=None,
                                   import_dispatched=True,
                                   result_artifact=current.artifact.artifact_id,
                                   result_fingerprint=current.fingerprint)
                LOGGER.warning(
                    "Recovered committed transaction from an interrupted process (never replayed)",
                    extra={"event": "txn_reconcile", "outcome": "recovered_committed"},
                )
            elif baseline_fp and current.fingerprint == baseline_fp:
                if baseline_id:
                    await self._store.release_retention(str(baseline_id))
                for artifact_id in (str(candidate_id) if candidate_id else None, current.artifact.artifact_id):
                    if artifact_id:
                        try:
                            await self._store.delete_internal(artifact_id)
                        except GatewayError:
                            pass
                await self._update(txn_id, TransactionState.NOT_APPLIED, error_code="conflict",
                                   result_artifact=current.artifact.artifact_id,
                                   result_fingerprint=current.fingerprint)
            else:
                await self._store.release_retention(current.artifact.artifact_id)
                await self._preserve_unknown(txn_id, state, baseline_id, candidate_id,
                                             extra_artifact=current.artifact.artifact_id,
                                             extra_fingerprint=current.fingerprint)

    async def _preserve_unknown(
        self, txn_id: str, state: TransactionState, baseline_id: Any, candidate_id: Any,
        extra_artifact: str | None = None, extra_fingerprint: str | None = None,
    ) -> None:
        await self._preserve_for_recovery(txn_id, *[
            str(item) for item in (candidate_id,) if item
        ])
        target = (
            TransactionState.RECOVERY_REQUIRED if state is TransactionState.VERIFYING
            else TransactionState.OUTCOME_UNKNOWN
        )
        await self._update(txn_id, target, error_code="outcome_unknown",
                           result_artifact=extra_artifact, result_fingerprint=extra_fingerprint)


async def _artifact_chunks(store: LocalArtifactStore, artifact_id: str) -> AsyncIterator[bytes]:
    reader = await store.open_read(artifact_id)
    try:
        while True:
            chunk = await reader.read_chunk()
            if chunk is None:
                return
            yield chunk
    finally:
        await reader.close()
