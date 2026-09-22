"""D15/D16 Perspective write use cases on the REST plane (Phase 5).

The four Perspective writes author nothing themselves: each one builds a typed
:class:`~ignition_rest_mcp.projects.perspective.ResourcePatch` and hands it to the
same ``ProjectTransactionService`` that G3, G4 and ``project_import`` already
proved, so D16's protocol, states, backup and restart reconciliation are reused
rather than re-derived.

Where each rule lands:

- **D08/D30 §3 Preflight.** The whole chain runs before the inheritance walk, before
  the writer lock and before anything is staged: verified principal, authorization
  scope, deployment class, operation allowlist, Target allowlist and capability. A
  Project the deployment did not name is refused without being exported and without
  leaving a transaction row.
- **D15 inheritance.** A write never creates a silent local override. After the
  D08 chain, the Project's own export is read to decide whether the target is
  Local; only when it is not does the server walk the ancestor chain, bounded by
  :data:`MAX_INHERITANCE_DEPTH`, exporting each ancestor and refusing with
  ``invalid_argument`` and reason ``inherited_resource`` if one defines the target.
  A target no Project in the chain defines is a create, which D15 allows.
- **D15/D17 redaction.** A document that carries the redaction placeholder is
  refused before the transaction starts: it is what a read of a secret-named field
  returns, and writing it back would store the placeholder as the value.
- **D30 §2 Precondition.** ``expectedFingerprint`` is the ``pcf1`` token the
  matching get Tool reported. The transaction compares it with baseline A and
  refuses with ``conflict`` before a candidate is staged or anything is dispatched.
- **D16 terminal states.** A satisfied outcome (``COMMITTED``, ``NO_CHANGE``) is
  returned as data; every other terminal state raises the D30 §7 code, exactly as
  ``project_import`` reports them, through the same helpers.

The archive itself is untouched by this module: the patch and the candidate
builder live in :mod:`ignition_rest_mcp.projects.perspective`, which owns the
Logical resource path mapping.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ignition_rest_mcp.artifacts.local import LocalArtifactStore
from ignition_rest_mcp.auth import VerifiedPrincipal
from ignition_rest_mcp.capabilities.registry import CapabilityRegistry
from ignition_rest_mcp.client.gateway import GatewayClient
from ignition_rest_mcp.config import Settings
from ignition_rest_mcp.errors import GatewayError
from ignition_rest_mcp.models import (
    PerspectiveWriteResult,
    PerspectiveViewWriteResult,
)
from ignition_rest_mcp.operation import OperationContext
from ignition_rest_mcp.projects import perspective
from ignition_rest_mcp.projects.capture import staged_project, validate_project_name
from ignition_rest_mcp.projects.perspective import PatchKind, PerspectiveCandidateBuilder, ResourcePatch
from ignition_rest_mcp.projects.transactions import (
    ProjectTransactionService,
    TransactionResult,
)
from ignition_rest_mcp.safety.executor import MutationPreflight, preflight_mutation
from ignition_rest_mcp.safety.policy import CONFIG_MUTATION, MutationOperation
from ignition_rest_mcp.services.config_resources import REDACTED_PLACEHOLDER
from ignition_rest_mcp.services.project_import import (
    expected_project_fingerprint,
    link_transaction,
    terminal_failure,
)
from ignition_rest_mcp.storage.records import OperationRecordStore

#: D04: the route every Perspective write needs is the Project import the D16
#: transaction dispatches. It also exports the Project (D15's local boundary) and
#: reads the Project listing for the ancestor walk, so a Gateway without the import
#: route exposes no Perspective write at all.
WRITE_CAPABILITY = "project_import"

#: Longest inheritance chain the Preflight walk follows before it fails closed
#: (D10: the walk is a bounded number of exports, and an unverifiable chain may
#: never be read as "no ancestor defines this").
MAX_INHERITANCE_DEPTH = 16

#: Bounded pages of the Gateway Project listing one walk may read.
MAX_PROJECT_LIST_PAGES = 20

#: The page size the Project listing is read with, matching the D16 transaction's
#: own canonicalization read.
PROJECT_LIST_PAGE = 500

VIEW_UPSERT = MutationOperation(
    op_id="perspective_view_upsert",
    mutation_class=CONFIG_MUTATION,
    capability=WRITE_CAPABILITY,
    #: An upsert replaces one document, like `config_resource_update`; removing state is
    #: the delete Tool's job (D08).
    destructive=False,
    target_denial_code="permission_denied",
    rejection_is_final=True,
)

VIEW_DELETE = MutationOperation(
    op_id="perspective_view_delete",
    mutation_class=CONFIG_MUTATION,
    capability=WRITE_CAPABILITY,
    destructive=True,
    target_denial_code="permission_denied",
    rejection_is_final=True,
)

PAGE_CONFIG_UPDATE = MutationOperation(
    op_id="perspective_page_config_update",
    mutation_class=CONFIG_MUTATION,
    capability=WRITE_CAPABILITY,
    destructive=False,
    target_denial_code="permission_denied",
    rejection_is_final=True,
)

SESSION_PROPS_UPDATE = MutationOperation(
    op_id="perspective_session_props_update",
    mutation_class=CONFIG_MUTATION,
    capability=WRITE_CAPABILITY,
    destructive=False,
    target_denial_code="permission_denied",
    rejection_is_final=True,
)


def refuse_redacted_value(document: Any, *, parameter: str) -> None:
    """Refuse a document that carries the redaction placeholder anywhere (D15/D17).

    ``perspective_view_get`` and the other document reads redact secret-named fields
    to :data:`~ignition_rest_mcp.services.config_resources.REDACTED_PLACEHOLDER`. A
    write built from such a read would store the placeholder as if it were the
    secret, so the exact value is refused wherever it appears, as a value or as a
    key, with ``invalid_argument`` and reason ``redacted_value``.
    """

    if _carries_placeholder(document):
        raise GatewayError(
            "invalid_argument",
            f"{parameter} contains the exact value {REDACTED_PLACEHOLDER!r}, which is what a read "
            "returns in place of a secret; it can never be written back as the value "
            "(reason: redacted_value). Read the resource again and keep that field as it is.",
        )


def _carries_placeholder(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if key == REDACTED_PLACEHOLDER or _carries_placeholder(child):
                return True
        return False
    if isinstance(value, list):
        return any(_carries_placeholder(child) for child in value)
    return bool(value == REDACTED_PLACEHOLDER)


async def perspective_view_upsert(
    client: GatewayClient,
    transactions: ProjectTransactionService,
    store: LocalArtifactStore,
    registry: CapabilityRegistry,
    settings: Settings,
    records: OperationRecordStore,
    context: OperationContext,
    *,
    principal: VerifiedPrincipal,
    project_name: str,
    path: str,
    view: dict[str, Any],
    expected_fingerprint: str,
    metrics: Any = None,
) -> PerspectiveViewWriteResult:
    """Replace (or create) one Local View document."""

    name = validate_project_name(project_name)
    logical = perspective.validate_logical_resource_path(path)
    perspective.validate_view_document(view)
    refuse_redacted_value(view, parameter="view")
    result = await _write(
        client=client, transactions=transactions, store=store, registry=registry,
        settings=settings, records=records, context=context, principal=principal,
        project_name=name, operation=VIEW_UPSERT, expected_fingerprint=expected_fingerprint,
        patch=ResourcePatch(
            kind=PatchKind.VIEW_REPLACE, logical_path=logical, document=view,
        ),
        require_local=False, metrics=metrics,
    )
    return PerspectiveViewWriteResult(
        path=logical, **_result_fields(context, result),
    )


async def perspective_view_delete(
    client: GatewayClient,
    transactions: ProjectTransactionService,
    store: LocalArtifactStore,
    registry: CapabilityRegistry,
    settings: Settings,
    records: OperationRecordStore,
    context: OperationContext,
    *,
    principal: VerifiedPrincipal,
    project_name: str,
    path: str,
    expected_fingerprint: str,
    metrics: Any = None,
) -> PerspectiveViewWriteResult:
    """Remove one Local View. D26: one View per call, never a folder."""

    name = validate_project_name(project_name)
    logical = perspective.validate_logical_resource_path(path)
    result = await _write(
        client=client, transactions=transactions, store=store, registry=registry,
        settings=settings, records=records, context=context, principal=principal,
        project_name=name, operation=VIEW_DELETE, expected_fingerprint=expected_fingerprint,
        patch=ResourcePatch(kind=PatchKind.VIEW_DELETE, logical_path=logical),
        require_local=True, metrics=metrics,
    )
    return PerspectiveViewWriteResult(path=logical, **_result_fields(context, result))


async def perspective_page_config_update(
    client: GatewayClient,
    transactions: ProjectTransactionService,
    store: LocalArtifactStore,
    registry: CapabilityRegistry,
    settings: Settings,
    records: OperationRecordStore,
    context: OperationContext,
    *,
    principal: VerifiedPrincipal,
    project_name: str,
    config: dict[str, Any],
    expected_fingerprint: str,
    metrics: Any = None,
) -> PerspectiveWriteResult:
    """Replace (or create) the Project's Page configuration document."""

    name = validate_project_name(project_name)
    perspective.validate_document(config)
    refuse_redacted_value(config, parameter="config")
    result = await _write(
        client=client, transactions=transactions, store=store, registry=registry,
        settings=settings, records=records, context=context, principal=principal,
        project_name=name, operation=PAGE_CONFIG_UPDATE, expected_fingerprint=expected_fingerprint,
        patch=ResourcePatch(kind=PatchKind.PAGE_CONFIG_REPLACE, document=config),
        require_local=False, metrics=metrics,
    )
    return PerspectiveWriteResult(**_result_fields(context, result))


async def perspective_session_props_update(
    client: GatewayClient,
    transactions: ProjectTransactionService,
    store: LocalArtifactStore,
    registry: CapabilityRegistry,
    settings: Settings,
    records: OperationRecordStore,
    context: OperationContext,
    *,
    principal: VerifiedPrincipal,
    project_name: str,
    props: dict[str, Any],
    expected_fingerprint: str,
    metrics: Any = None,
) -> PerspectiveWriteResult:
    """Replace (or create) the Project's Session properties document."""

    name = validate_project_name(project_name)
    perspective.validate_document(props)
    refuse_redacted_value(props, parameter="props")
    result = await _write(
        client=client, transactions=transactions, store=store, registry=registry,
        settings=settings, records=records, context=context, principal=principal,
        project_name=name, operation=SESSION_PROPS_UPDATE,
        expected_fingerprint=expected_fingerprint,
        patch=ResourcePatch(kind=PatchKind.SESSION_PROPS_REPLACE, document=props),
        require_local=False, metrics=metrics,
    )
    return PerspectiveWriteResult(**_result_fields(context, result))


async def _write(
    *,
    client: GatewayClient,
    transactions: ProjectTransactionService,
    store: LocalArtifactStore,
    registry: CapabilityRegistry,
    settings: Settings,
    records: OperationRecordStore,
    context: OperationContext,
    principal: VerifiedPrincipal,
    project_name: str,
    operation: MutationOperation,
    patch: ResourcePatch,
    expected_fingerprint: str,
    require_local: bool,
    metrics: Any,
) -> TransactionResult:
    """The shared write path: D08 chain, inheritance walk, D16 transaction."""

    fingerprint = expected_project_fingerprint(expected_fingerprint)
    await preflight_mutation(
        registry=registry, settings=settings, context=context,
        preflight=MutationPreflight(
            operation=operation, principal=principal, target_id=project_name,
            target_type="project", audit_fields={"projectName": project_name},
        ),
    )
    deadline = settings.budget_deadline_seconds("ARTIFACT")
    local = await _defines_locally(
        client, store, context, project_name=project_name, patch=patch, deadline_seconds=deadline,
    )
    if not local and require_local:
        raise GatewayError(
            "not_found",
            f"Project {project_name!r} has no Local {_target_label(patch)} and no ancestor defines one",
        )
    result = await transactions.execute(
        project_name=project_name, builder=PerspectiveCandidateBuilder(patch), context=context,
        principal=principal, operation=operation, expected_fingerprint=fingerprint,
    )
    await link_transaction(records, context, result, metrics)
    failure = terminal_failure(result)
    if failure is not None:
        raise failure
    return result


async def _defines_locally(
    client: GatewayClient,
    store: LocalArtifactStore,
    context: OperationContext,
    *,
    project_name: str,
    patch: ResourcePatch,
    deadline_seconds: float,
) -> bool:
    """Whether the Project defines the target, refusing an Inherited one (D15).

    Gateway export is the local-Project boundary, so the Project's own export decides
    "Local". Only when it does not define the target does the ancestor chain get read:
    if an ancestor defines it, this Project gets the resource by inheritance, and a
    write would create the local override D15 forbids.
    """

    if await _defines(
        client, store, context, project_name=project_name, patch=patch,
        deadline_seconds=deadline_seconds,
    ):
        return True
    for ancestor in await _ancestors(client, context, project_name):
        if await _defines(
            client, store, context, project_name=ancestor, patch=patch,
            deadline_seconds=deadline_seconds,
        ):
            raise GatewayError(
                "invalid_argument",
                f"{_target_label(patch)} is an Inherited resource of Project {project_name!r}: the "
                f"Project does not define it locally and ancestor Project {ancestor!r} does, so this "
                "write would create a local override (reason: inherited_resource). A future explicit "
                "override needs its own deliberate API (D15).",
            )
    return False


async def _defines(
    client: GatewayClient,
    store: LocalArtifactStore,
    context: OperationContext,
    *,
    project_name: str,
    patch: ResourcePatch,
    deadline_seconds: float,
) -> bool:
    """One bounded export, read for the single entry the patch targets."""

    async with staged_project(
        client, store, context, project_name=project_name, deadline_seconds=deadline_seconds,
    ) as staged:
        return await asyncio.to_thread(perspective.defines_target, staged.path, patch)


async def _ancestors(client: GatewayClient, context: OperationContext, project_name: str) -> list[str]:
    """The ancestor chain above one Project, nearest first, bounded and cycle-safe.

    The parent is the ``parent`` of the Project's own entry in the Gateway Project
    listing. A chain deeper than :data:`MAX_INHERITANCE_DEPTH`, or one that loops, is
    refused rather than truncated: the walk must never end early and be read as "no
    ancestor defines this".
    """

    parents = await _project_parents(client, context)
    chain: list[str] = []
    seen = {project_name}
    current = parents.get(project_name, "")
    while current:
        if current in seen:
            raise GatewayError(
                "limit_exceeded",
                f"the Project inheritance chain above {project_name!r} loops through {current!r}; "
                "the ancestor check cannot be completed",
            )
        if len(chain) >= MAX_INHERITANCE_DEPTH:
            raise GatewayError(
                "limit_exceeded",
                f"the Project inheritance chain above {project_name!r} is deeper than "
                f"{MAX_INHERITANCE_DEPTH} Projects; the ancestor check cannot be completed",
            )
        seen.add(current)
        chain.append(current)
        current = parents.get(current, "")
    return chain


async def _project_parents(client: GatewayClient, context: OperationContext) -> dict[str, str]:
    """Every Project's parent, from a bounded read of the Project listing.

    The listing carries the inheritance ``parent`` per Project, so one bounded read
    answers the whole chain. A Project with no parent, or one whose parent has no
    entry of its own, ends its chain.
    """

    parents: dict[str, str] = {}
    offset = 0
    for _ in range(MAX_PROJECT_LIST_PAGES):
        payload = await client.get_json(
            "/data/api/v1/projects/list",
            params={"limit": PROJECT_LIST_PAGE, "offset": offset},
            context=context,
        )
        items = payload.get("items")
        metadata = payload.get("metadata")
        if not isinstance(items, list) or not isinstance(metadata, dict):
            raise GatewayError("schema_mismatch", "project listing has an unknown shape")
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                continue
            parent = item.get("parent")
            parents[item["name"]] = parent if isinstance(parent, str) else ""
        matching = metadata.get("matching")
        if isinstance(matching, bool) or not isinstance(matching, (int, float)) or int(matching) != matching:
            raise GatewayError("schema_mismatch", "project listing metadata is invalid")
        matching = int(matching)
        offset += PROJECT_LIST_PAGE
        if offset >= matching or not items:
            return parents
    raise GatewayError(
        "limit_exceeded",
        f"the Gateway lists more Projects than the {MAX_PROJECT_LIST_PAGES}-page bound the ancestor "
        "check reads; the check cannot be completed",
    )


def _target_label(patch: ResourcePatch) -> str:
    """How a refusal names the resource one patch targets."""

    if patch.kind is PatchKind.PAGE_CONFIG_REPLACE:
        return "Page configuration document"
    if patch.kind is PatchKind.SESSION_PROPS_REPLACE:
        return "Session properties document"
    return f"View {patch.logical_path!r}"


def _result_fields(context: OperationContext, result: TransactionResult) -> dict[str, Any]:
    """The ``PerspectiveWriteResult`` fields one satisfied transaction reports.

    A satisfied terminal state always captured both fingerprints; ``resultFingerprint``
    stays ``None`` for ``NO_CHANGE``, where nothing was imported.
    """

    return {
        "correlationId": context.correlation_id,
        "projectName": result.project_name,
        "transactionId": result.transaction_id,
        "state": result.state.value,
        "baselineFingerprint": _fingerprint(result.baseline_fingerprint, result, "baseline"),
        "candidateFingerprint": _fingerprint(result.candidate_fingerprint, result, "candidate"),
        "resultFingerprint": result.result_fingerprint,
        "importDispatched": result.import_dispatched,
        "designerWarning": result.designer_warning,
    }


def _fingerprint(value: str | None, result: TransactionResult, label: str) -> str:
    if value is None:  # pragma: no cover - a satisfied state always captured both
        raise GatewayError(
            "internal_error",
            f"project {result.project_name!r} reached {result.state.value} without a {label} fingerprint",
        )
    return value
