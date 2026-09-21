"""D17/D08 sensitive export use cases (Phase 3): project_export and
tag_config_export. Both are READ operations on the Native REST plane (D02/D11)
that stage the binary body into the ArtifactStore (D17), require the
deployment sensitive-export gate (D08 deny-by-default), and audit
decision -> attempt -> result around the Gateway call (slice 1 ordering).

Nothing is ever published from a failed or cancelled transfer: the staging
writer is aborted, so no READY artifact exists for a broken stream.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from ignition_rest_mcp.artifacts.local import LocalArtifactStore, sanitize_display_filename
from ignition_rest_mcp.artifacts.model import Artifact
from ignition_rest_mcp.capabilities.registry import CapabilityRegistry
from ignition_rest_mcp.client.gateway import GatewayClient
from ignition_rest_mcp.errors import GatewayError
from ignition_rest_mcp.models import ArtifactRefModel, ProjectExportResult, TagConfigExportResult
from ignition_rest_mcp.operation import OperationContext
from ignition_rest_mcp.projects.capture import capture_project, validate_project_name

MAX_PROVIDER_LENGTH = 256
MAX_TAG_PATH_LENGTH = 1024
# Documented bound (slice 5 step 4): tag config JSON is validated with a full
# stdlib parse while the payload fits this ceiling; larger payloads fail
# limit_exceeded instead of an unbounded or partial validation.
TAG_JSON_FULL_PARSE_LIMIT_BYTES = 8 * 1024 * 1024


def _require_gate(settings_gate_on: bool, capability: str, registry: CapabilityRegistry) -> None:
    if not settings_gate_on:
        raise GatewayError(
            "operation_disabled",
            "sensitive exports are disabled for this deployment "
            "(set IGNITION_MCP_SENSITIVE_EXPORTS_ENABLED=true to enable)",
        )
    if not registry.supports(capability):
        raise GatewayError(
            "unsupported_capability",
            f"{capability} is not present in the current Gateway capability snapshot",
        )


def _bounded_export_text(value: str, name: str, *, maximum_bytes: int, allow_empty: bool) -> str:
    if not isinstance(value, str):
        raise GatewayError("invalid_argument", f"{name} must be a string")
    if not allow_empty and not value.strip():
        raise GatewayError("invalid_argument", f"{name} must be non-empty")
    if len(value) > maximum_bytes:  # cheap character bound first
        raise GatewayError("limit_exceeded", f"{name} exceeds the {maximum_bytes}-byte limit")
    if len(value.encode("utf-8")) > maximum_bytes:
        raise GatewayError("limit_exceeded", f"{name} exceeds the {maximum_bytes}-byte limit")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise GatewayError("invalid_argument", f"{name} must not contain control characters")
    return value


async def _audited_denial(context: OperationContext, reason: str) -> None:
    auditor = context.auditor
    if auditor is not None:
        await auditor.decision(allowed=False, reason=reason)


async def _audited_gate(
    context: OperationContext, settings_gate_on: bool, capability: str, registry: CapabilityRegistry,
) -> None:
    """Gate/capability denial must still produce its audit row (D18 coverage)."""

    try:
        _require_gate(settings_gate_on, capability, registry)
    except GatewayError as error:
        await _audited_denial(context, error.code)
        raise


async def project_export(
    client: GatewayClient,
    registry: CapabilityRegistry,
    store: LocalArtifactStore,
    context: OperationContext,
    *,
    settings_gate_on: bool,
    project_name: str,
    gateway_id: str,
    deadline_seconds: float,
) -> ProjectExportResult:
    await _audited_gate(context, settings_gate_on, "project_export", registry)
    name = validate_project_name(project_name)

    auditor = context.auditor
    if auditor is not None:
        await auditor.decision(
            allowed=True, target_type="project", target_id=name, safe_fields={"projectName": name},
        )
        await auditor.attempt(target_type="project", target_id=name, safe_fields={"projectName": name})
    try:
        captured = await capture_project(
            client, store, context, project_name=name, gateway_id=gateway_id,
            retention_class="EXPORT", deadline_seconds=deadline_seconds,
        )
    except (Exception, asyncio.CancelledError) as error:
        if auditor is not None and auditor.attempt_recorded and not isinstance(error, asyncio.CancelledError):
            code = error.code if isinstance(error, GatewayError) else "internal_error"
            await auditor.result("failed", error_code=code, safe_fields={"projectName": name})
        raise
    if auditor is not None:
        await auditor.result(
            "completed", duration_ms=context.elapsed_ms(),
            target_type="project", target_id=name, safe_fields={"projectName": name},
        )
    return ProjectExportResult(
        correlationId=context.correlation_id,
        projectName=name,
        fingerprint=captured.fingerprint,
        artifact=_ref(captured.artifact),
    )


async def tag_config_export(
    client: GatewayClient,
    registry: CapabilityRegistry,
    store: LocalArtifactStore,
    context: OperationContext,
    *,
    settings_gate_on: bool,
    provider: str,
    path: str,
    recursive: bool,
    include_udts: bool,
    gateway_id: str,
    deadline_seconds: float,
) -> TagConfigExportResult:
    await _audited_gate(context, settings_gate_on, "tag_config_export", registry)
    provider = _bounded_export_text(provider, "provider", maximum_bytes=MAX_PROVIDER_LENGTH, allow_empty=False)
    path = _bounded_export_text(path, "path", maximum_bytes=MAX_TAG_PATH_LENGTH, allow_empty=True)
    if not isinstance(recursive, bool) or not isinstance(include_udts, bool):
        raise GatewayError("invalid_argument", "recursive and includeUdts must be booleans")

    writer: Any = None
    safe = {"provider": provider, "path": path, "recursive": recursive, "includeUdts": include_udts}
    auditor = context.auditor
    try:
        if auditor is not None:
            await auditor.decision(allowed=True, target_type="tag_provider", target_id=provider, safe_fields=safe)
        writer = await store.create(
            kind="tag_config_export", sensitivity="CONFIDENTIAL", retention_class="EXPORT",
            owner=context.actor,
            filename=sanitize_display_filename(f"tag-config-{provider}.json", fallback="tag-config-export.json"),
            media_type="application/json", correlation_id=context.correlation_id, gateway_id=gateway_id,
        )
        if auditor is not None:
            await auditor.attempt(target_type="tag_provider", target_id=provider, safe_fields=safe)
        params: dict[str, Any] = {
            "type": "json",  # locked; XML is not selectable (slice 5 step 4)
            "provider": provider,
            "recursive": recursive,
            "includeUdts": include_udts,
        }
        if path:
            params["path"] = path
        delivered = await client.stream_get_to(
            "/data/api/v1/tags/export", params=params, sink=writer,
            limit_bytes=min(store.quotas.max_bytes, TAG_JSON_FULL_PARSE_LIMIT_BYTES),
            deadline_seconds=deadline_seconds, context=context,
        )
        await _validate_staged_json(store, writer, delivered)
    except (Exception, asyncio.CancelledError) as error:
        if writer is not None:
            await writer.abort()
        if auditor is not None and auditor.attempt_recorded and not isinstance(error, asyncio.CancelledError):
            code = error.code if isinstance(error, GatewayError) else "internal_error"
            await auditor.result("failed", error_code=code, safe_fields=safe)
        raise
    artifact = await store.publish(writer)
    if auditor is not None:
        await auditor.result(
            "completed", duration_ms=context.elapsed_ms(),
            target_type="tag_provider", target_id=provider, safe_fields=safe,
        )
    return TagConfigExportResult(
        correlationId=context.correlation_id, provider=provider, path=path, artifact=_ref(artifact),
    )


async def _validate_staged_json(store: LocalArtifactStore, writer: Any, delivered: int) -> None:
    if delivered > TAG_JSON_FULL_PARSE_LIMIT_BYTES:  # unreachable via cap; explicit anyway
        raise GatewayError(
            "limit_exceeded",
            f"tag config export body of {delivered} bytes exceeds the {TAG_JSON_FULL_PARSE_LIMIT_BYTES}"
            "-byte validation ceiling",
        )
    payload = await asyncio.to_thread(_read_staged_bytes, store, writer)
    try:
        json.loads(payload)
    except (ValueError, UnicodeDecodeError) as error:
        raise GatewayError(
            "invalid_argument", "Gateway returned a tag config body that is not valid JSON",
        ) from error


def _read_staged_bytes(store: LocalArtifactStore, writer: Any) -> bytes:
    with open(store.staged_path(writer), "rb") as stream:
        return stream.read()


def _ref(artifact: Artifact) -> ArtifactRefModel:
    return ArtifactRefModel(**artifact.to_ref())
