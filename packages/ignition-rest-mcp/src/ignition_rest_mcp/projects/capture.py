"""Shared bounded Project ZIP capture (Native REST export -> ArtifactStore).

Used by the sensitive ``project_export`` Tool (with the slice-1 audit chain in
``services/exports.py``), by the D16 transaction machinery (baseline A,
re-check A' and post-import C captures, which are internal steps of an
audited mutation rather than separately audited exports), and by the D15
Perspective reads, which stage one export without publishing it.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator
from urllib.parse import quote

from ignition_rest_mcp.artifacts.local import LocalArtifactStore, LocalWriter, sanitize_display_filename
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


@dataclass(frozen=True, slots=True)
class StagedProject:
    """A Project export held in private staging for one internal read (D15).

    ``path`` is the staged archive. It never becomes a READY artifact and never
    leaves the server; ``fingerprint`` is its D16 ``pcf1`` content digest.
    """

    name: str
    path: str
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
    writer, fingerprint = await _stage_project(
        client, store, context, name=name, gateway_id=gateway_id,
        retention_class=retention_class, deadline_seconds=deadline_seconds,
    )
    artifact = await store.publish(writer)
    return CapturedProject(artifact=artifact, fingerprint=fingerprint)


@asynccontextmanager
async def staged_project(
    client: GatewayClient,
    store: LocalArtifactStore,
    context: OperationContext,
    *,
    project_name: str,
    gateway_id: str = "",
    deadline_seconds: float,
) -> AsyncIterator[StagedProject]:
    """Stage one Project export for an internal read, and never publish it.

    The staged archive has passed the D15 ZIP safety gate inside the D16
    fingerprint pass, and the staging writer is aborted on every exit path, so a
    read leaves no artifact behind and no partial export is observable as READY
    (D17). ``gateway_id`` is metadata of the staging row that is discarded here.
    """

    name = validate_project_name(project_name)
    writer, fingerprint = await _stage_project(
        client, store, context, name=name, gateway_id=gateway_id,
        retention_class="EPHEMERAL", deadline_seconds=deadline_seconds,
    )
    try:
        yield StagedProject(name=name, path=store.staged_path(writer), fingerprint=fingerprint)
    finally:
        await writer.abort()


async def _stage_project(
    client: GatewayClient,
    store: LocalArtifactStore,
    context: OperationContext,
    *,
    name: str,
    gateway_id: str,
    retention_class: str,
    deadline_seconds: float,
) -> tuple[LocalWriter, str]:
    """Stream one validated Project export into staging and fingerprint it."""

    writer = await store.create(
        kind="project_export", sensitivity="CONFIDENTIAL", retention_class=retention_class,
        owner=context.actor,
        filename=sanitize_display_filename(f"{name}.zip", fallback="project-export.zip"),
        media_type="application/zip", correlation_id=context.correlation_id,
        gateway_id=gateway_id, project_name=name,
    )
    try:
        path = f"/data/api/v1/projects/export/{quote(name, safe='')}"
        await client.stream_get_to(
            path, sink=writer, limit_bytes=store.quotas.max_bytes,
            deadline_seconds=deadline_seconds, context=context,
        )
        fingerprint = await asyncio.to_thread(project_fingerprint, store.staged_path(writer))
    except BaseException:
        await writer.abort()
        raise
    return writer, fingerprint
