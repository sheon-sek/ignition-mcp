"""Shared bounded Project ZIP capture (Native REST export -> ArtifactStore).

Used by the sensitive ``project_export`` Tool (with the slice-1 audit chain in
``services/exports.py``) and by the D16 transaction machinery (baseline A,
re-check A' and post-import C captures, which are internal steps of an
audited mutation rather than separately audited exports).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from ignition_rest_mcp.artifacts.local import LocalArtifactStore, sanitize_display_filename
from ignition_rest_mcp.artifacts.model import Artifact
from ignition_rest_mcp.client.gateway import GatewayClient
from ignition_rest_mcp.errors import GatewayError
from ignition_rest_mcp.operation import OperationContext
from ignition_rest_mcp.projects.fingerprint import project_fingerprint

MAX_PROJECT_NAME_BYTES = 256


@dataclass(frozen=True, slots=True)
class CapturedProject:
    artifact: Artifact
    fingerprint: str


def validate_project_name(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GatewayError("invalid_argument", "projectName must be non-empty")
    if len(value.encode("utf-8")) > MAX_PROJECT_NAME_BYTES:
        raise GatewayError(
            "limit_exceeded", f"projectName exceeds the {MAX_PROJECT_NAME_BYTES}-byte limit",
        )
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise GatewayError("invalid_argument", "projectName must not contain control characters")
    return value


async def capture_project(
    client: GatewayClient,
    store: LocalArtifactStore,
    context: OperationContext,
    *,
    project_name: str,
    gateway_id: str,
    retention_class: str,
    deadline_seconds: float,
) -> CapturedProject:
    """Stream ``GET /data/api/v1/projects/export/{name}`` into a validated,
    fingerprinted artifact. Any failure aborts staging so a partial capture can
    never be observed as READY (D17)."""

    name = validate_project_name(project_name)
    writer: Any = None
    try:
        writer = await store.create(
            kind="project_export", sensitivity="CONFIDENTIAL", retention_class=retention_class,
            owner=context.actor,
            filename=sanitize_display_filename(f"{name}.zip", fallback="project-export.zip"),
            media_type="application/zip", correlation_id=context.correlation_id,
            gateway_id=gateway_id, project_name=name,
        )
        path = f"/data/api/v1/projects/export/{quote(name, safe='')}"
        await client.stream_get_to(
            path, sink=writer, limit_bytes=store.quotas.max_bytes,
            deadline_seconds=deadline_seconds, context=context,
        )
        fingerprint = await asyncio.to_thread(project_fingerprint, store.staged_path(writer))
    except BaseException:
        if writer is not None:
            await writer.abort()
        raise
    artifact = await store.publish(writer)
    return CapturedProject(artifact=artifact, fingerprint=fingerprint)
