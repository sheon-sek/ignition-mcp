"""D15 Perspective read use cases on the REST plane.

Each Gateway-backed read exports the Project through the same bounded capture the
D16 transaction service uses, reads the documents it needs from the private
staging copy, and then aborts the staging writer. A read publishes no artifact,
never returns the archive, and does not change anything.

Reads return Local resources only. Gateway export is the local-project boundary
(D15), so a View that a Project inherits from an ancestor is not in this archive
and answers ``not_found`` rather than being resolved through the inheritance
chain. ``perspective_view_validate`` is the one read that touches neither the
Gateway nor an archive: it validates the document the caller sent.
"""

from __future__ import annotations

import asyncio

from ignition_rest_mcp.artifacts.local import LocalArtifactStore
from ignition_rest_mcp.capabilities.registry import CapabilityRegistry
from ignition_rest_mcp.client.gateway import GatewayClient
from ignition_rest_mcp.errors import GatewayError
from ignition_rest_mcp.models import (
    PageMetadata,
    PerspectivePageConfigGetResult,
    PerspectiveSessionPropsGetResult,
    PerspectiveViewGetResult,
    PerspectiveViewListResult,
    PerspectiveViewValidateResult,
)
from ignition_rest_mcp.operation import OperationContext
from ignition_rest_mcp.projects import perspective
from ignition_rest_mcp.projects.capture import staged_project
from ignition_rest_mcp.services.readonly import bounded_page_request, require_capability

#: The capability every Perspective read is gated on. The Project export route is
#: how these Tools reach a Project's Local resources (D15/D04), so a Gateway
#: without it exposes no Perspective read at all.
PROJECT_EXPORT_CAPABILITY = "project_export"


async def perspective_view_list(
    client: GatewayClient,
    registry: CapabilityRegistry,
    store: LocalArtifactStore,
    context: OperationContext,
    *,
    project_name: str,
    limit: int,
    offset: int,
    deadline_seconds: float,
) -> PerspectiveViewListResult:
    """One bounded page of the Project's Local View paths."""

    require_capability(registry, PROJECT_EXPORT_CAPABILITY)
    limit, offset = bounded_page_request(limit, offset)
    async with staged_project(
        client, store, context, project_name=project_name, deadline_seconds=deadline_seconds,
    ) as staged:
        paths = await asyncio.to_thread(perspective.list_view_paths, staged.path)
        return PerspectiveViewListResult(
            correlationId=context.correlation_id,
            projectName=staged.name,
            items=paths[offset : offset + limit],
            page=PageMetadata(total=len(paths), matching=len(paths), limit=limit, offset=offset),
        )


async def perspective_view_get(
    client: GatewayClient,
    registry: CapabilityRegistry,
    store: LocalArtifactStore,
    context: OperationContext,
    *,
    project_name: str,
    path: str,
    deadline_seconds: float,
) -> PerspectiveViewGetResult:
    """One View document, with the fingerprint of the export it was read from."""

    path = perspective.validate_logical_resource_path(path)
    require_capability(registry, PROJECT_EXPORT_CAPABILITY)
    async with staged_project(
        client, store, context, project_name=project_name, deadline_seconds=deadline_seconds,
    ) as staged:
        document = await asyncio.to_thread(perspective.read_view_document, staged.path, path)
        return PerspectiveViewGetResult(
            correlationId=context.correlation_id,
            projectName=staged.name,
            path=path,
            view=document,
            fingerprint=staged.fingerprint,
        )


def perspective_view_validate(
    context: OperationContext, *, view: str,
) -> PerspectiveViewValidateResult:
    """Validate a caller-supplied View document offline (D15). No Gateway call."""

    validation = perspective.validate_view_document(view)
    return PerspectiveViewValidateResult(
        correlationId=context.correlation_id,
        valid=True,
        bytes=validation.bytes,
        depth=validation.depth,
    )


async def perspective_page_config_get(
    client: GatewayClient,
    registry: CapabilityRegistry,
    store: LocalArtifactStore,
    context: OperationContext,
    *,
    project_name: str,
    deadline_seconds: float,
) -> PerspectivePageConfigGetResult:
    """The Project's Page configuration document, or ``not_found`` when it has none."""

    require_capability(registry, PROJECT_EXPORT_CAPABILITY)
    async with staged_project(
        client, store, context, project_name=project_name, deadline_seconds=deadline_seconds,
    ) as staged:
        config = await asyncio.to_thread(perspective.read_page_config, staged.path)
        if config is None:
            raise GatewayError(
                "not_found", f"Project {staged.name!r} has no Local Page configuration document",
            )
        return PerspectivePageConfigGetResult(
            correlationId=context.correlation_id,
            projectName=staged.name,
            config=config,
            fingerprint=staged.fingerprint,
        )


async def perspective_session_props_get(
    client: GatewayClient,
    registry: CapabilityRegistry,
    store: LocalArtifactStore,
    context: OperationContext,
    *,
    project_name: str,
    deadline_seconds: float,
) -> PerspectiveSessionPropsGetResult:
    """The Project's Session properties document, or ``not_found`` when it has none."""

    require_capability(registry, PROJECT_EXPORT_CAPABILITY)
    async with staged_project(
        client, store, context, project_name=project_name, deadline_seconds=deadline_seconds,
    ) as staged:
        props = await asyncio.to_thread(perspective.read_session_props, staged.path)
        if props is None:
            raise GatewayError(
                "not_found", f"Project {staged.name!r} has no Local Session properties document",
            )
        return PerspectiveSessionPropsGetResult(
            correlationId=context.correlation_id,
            projectName=staged.name,
            props=props,
            fingerprint=staged.fingerprint,
        )
