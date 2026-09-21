"""D17 artifact HTTP data plane: GET/HEAD/POST /artifacts.

FastMCP custom routes are NOT wrapped by the MCP ``RequireAuthMiddleware``, so
these routes authenticate explicitly with the configured verifier and then
enforce the principal-scoped authorization rule. They also implement the
Slice-1 audit phase ordering themselves (they live outside the Tool invocation
lifecycle): the access ``attempt`` row is durably committed before any status
line, sensitive header or body byte; denials write only a ``decision`` row; a
failed ``result`` row never alters the already-sent response.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import TYPE_CHECKING, Any, AsyncGenerator, Callable

from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse

from ignition_rest_mcp.artifacts.local import LocalArtifactStore, validate_artifact_id
from ignition_rest_mcp.artifacts.model import Artifact
from ignition_rest_mcp.audit.sink import AuditRow, SqliteAuditSink
from ignition_rest_mcp.auth import ADMIN_SCOPE, Principal, build_auth, principal_from_token
from ignition_rest_mcp.config import Settings
from ignition_rest_mcp.errors import GatewayError
from ignition_rest_mcp.operation import uuid7
from ignition_rest_mcp.projects.zip_safety import ZipSafetyConfig, validate_zip_file
from ignition_rest_mcp.storage.database import StorageUnavailable
from ignition_rest_mcp.storage.records import iso_utc, utc_now

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from ignition_rest_mcp.runtime import RuntimeState

LOGGER = logging.getLogger("ignition_rest_mcp.artifacts")

UPLOADABLE_KINDS = frozenset({"project_archive"})
UPLOAD_MEDIA_TYPE = "application/zip"
SENSITIVE_CLASSES = frozenset({"CONFIDENTIAL", "RESTRICTED"})
STATUS_BY_CODE = {
    "invalid_argument": 400,
    "permission_denied": 401,
    "not_found": 404,
    "operation_disabled": 404,
    "conflict": 409,
    "limit_exceeded": 413,
    "internal_error": 503,
}


class ZipSafetyValidator:
    """ArtifactStore validator hook wrapping the D15 streamed check."""

    name = "zip-safety"

    def __init__(self, config: ZipSafetyConfig | None = None) -> None:
        self._config = config

    async def validate(self, path: str) -> None:
        await asyncio.to_thread(validate_zip_file, path, self._config)


def _error(correlation_id: str, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        {"code": code, "message": message, "correlationId": correlation_id},
        status_code=STATUS_BY_CODE.get(code, 400),
    )


def _content_disposition(filename: str) -> str:
    ascii_fallback = filename.encode("ascii", "replace").decode("ascii")
    ascii_fallback = "".join(char for char in ascii_fallback if char not in '"\\' and ord(char) >= 0x20)
    extended = quote_rfc6266(filename)
    return f"attachment; filename=\"{ascii_fallback or 'artifact'}\"; filename*=UTF-8''{extended}"


def quote_rfc6266(value: str) -> str:
    safe = "!#$%&'*+.^_`|~0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-"
    return "".join(
        char if char in safe else "".join("%%%02X" % byte for byte in char.encode("utf-8"))
        for char in value
    )


def _representation_headers(artifact: Artifact) -> dict[str, str]:
    raw_digest = base64.b64encode(bytes.fromhex(artifact.sha256)).decode("ascii")
    return {
        "Content-Type": artifact.media_type,
        "Content-Length": str(artifact.size_bytes),
        "ETag": f'"{artifact.sha256}"',
        "Repr-Digest": f"sha-256=:{raw_digest}:",
        "Content-Disposition": _content_disposition(artifact.filename),
        "Cache-Control": "no-store",
    }


class ArtifactDataPlane:
    def __init__(self, state: "RuntimeState", settings: Settings) -> None:
        self._state = state
        self._settings = settings
        self._verifier = build_auth(settings)

    # ------------------------------------------------------------------ authn/authz

    async def authenticate(self, request: Request) -> Principal:
        if self._settings.auth_mode == "none":
            return principal_from_token(self._settings, None)
        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer ") or len(header) <= 7:
            return Principal(key="unauthenticated", scopes=frozenset(), auth_mode="anonymous")
        token_value = header[7:]
        assert self._verifier is not None
        access = await self._verifier.verify_token(token_value)
        if access is None:
            return Principal(key="unauthenticated", scopes=frozenset(), auth_mode="invalid-credential")
        required = set(self._verifier.required_scopes or [])
        if not required.issubset(set(access.scopes)):
            return Principal(key="unauthenticated", scopes=frozenset(), auth_mode="insufficient-scope")
        return principal_from_token(self._settings, access)

    @staticmethod
    def _visible(principal: Principal, artifact: Artifact) -> bool:
        # D17/D19: same verified principal, or ignition.admin for foreign records.
        return artifact.owner == principal.key or principal.has_scope(ADMIN_SCOPE)

    # ------------------------------------------------------------------ audit

    async def _row(
        self, *, phase: str, outcome: str, correlation_id: str, principal: Principal,
        artifact: Artifact | None, method: str, artifact_id: str,
        target_id: str | None = None, duration_ms: float | None = None, bytes_sent: int | None = None,
    ) -> None:
        row = AuditRow(
            timestamp=iso_utc(utc_now()),
            correlation_id=correlation_id,
            server="ignition-rest",
            tool="artifact_access",
            actor=principal.key,
            operation_class="READ",
            destructive=False,
            phase=phase,
            outcome=outcome,
            target_type="artifact" if artifact is not None or artifact_id else None,
            target_id=target_id if target_id is not None else (artifact.artifact_id if artifact else artifact_id),
            duration_ms=duration_ms,
            safe_fields={
                "artifactId": artifact_id[:128],
                "kind": artifact.kind if artifact is not None else "",
                "sensitivity": artifact.sensitivity if artifact is not None else "",
                "method": method,
                **({"bytesSent": int(bytes_sent)} if bytes_sent is not None else {}),
            },
        )
        sink: SqliteAuditSink | None = self._state.audit_sink
        if sink is None:
            raise StorageUnavailable("audit")
        await sink.write(row)

    async def _decision_quietly(
        self, correlation_id: str, principal: Principal, artifact: Artifact | None, method: str,
        artifact_id: str, reason: str,
    ) -> None:
        try:
            await self._row(
                phase="decision", outcome=f"denied:{reason}", correlation_id=correlation_id,
                principal=principal, artifact=artifact, method=method, artifact_id=artifact_id,
            )
        except Exception as error:
            # A failed denial audit must not change the denial itself (slice 1 rule).
            LOGGER.error(
                "Data-plane denial decision row could not be written",
                extra={
                    "event": "audit_write_failure", "correlationId": correlation_id,
                    "tool": "artifact_access", "outcome": "error", "errorCode": type(error).__name__,
                },
            )

    # ------------------------------------------------------------------ GET / HEAD

    async def fetch(self, request: Request) -> Response:
        correlation_id = uuid7()
        artifact_id = request.path_params.get("artifactId", "")
        method = request.method or "GET"
        try:
            validate_artifact_id(artifact_id)
        except GatewayError as error:
            return _error(correlation_id, error.code, error.message)

        principal = await self.authenticate(request)
        if not principal.has_scope("ignition.read"):
            await self._decision_quietly(
                correlation_id, principal, None, method, artifact_id, "authentication",
            )
            return _error(correlation_id, "permission_denied", "A valid ignition.read credential is required")

        store: LocalArtifactStore | None = self._state.artifacts
        if store is None:
            return _error(correlation_id, "internal_error", "The artifact store is unavailable")
        try:
            artifact = await store.stat(artifact_id)
        except GatewayError:
            return _error(correlation_id, "not_found", "Artifact not found")
        except StorageUnavailable:
            return _error(correlation_id, "internal_error", "The artifact store is unavailable")

        if not self._visible(principal, artifact):
            # Non-visible records answer not_found (no existence oracle) after a
            # decision row for the denied attempt.
            await self._decision_quietly(
                correlation_id, principal, artifact, method, artifact_id, "cross-principal",
            )
            return _error(correlation_id, "not_found", "Artifact not found")

        audited = artifact.sensitivity in SENSITIVE_CLASSES
        if audited:
            try:
                await self._row(
                    phase="attempt", outcome="attempted", correlation_id=correlation_id,
                    principal=principal, artifact=artifact, method=method, artifact_id=artifact_id,
                )
            except Exception as error:
                LOGGER.error(
                    "Data-plane attempt row failed; failing closed with no metadata or body",
                    extra={
                        "event": "audit_write_failure", "correlationId": correlation_id,
                        "tool": "artifact_access", "outcome": "error", "errorCode": type(error).__name__,
                    },
                )
                metrics = self._state.metrics
                if metrics is not None:
                    metrics.record_audit_write_failure("attempt")
                return _error(
                    correlation_id, "internal_error",
                    "The audit subsystem could not record the access attempt; no artifact data was sent",
                )

        headers = _representation_headers(artifact)
        try:
            reader = await store.open_read(artifact_id)
        except GatewayError:
            if audited:
                await self._result_quietly(correlation_id, principal, artifact, method, "failed", 0)
            return _error(correlation_id, "not_found", "Artifact not found")
        except StorageUnavailable:
            if audited:
                await self._result_quietly(correlation_id, principal, artifact, method, "failed", 0)
            return _error(correlation_id, "internal_error", "The artifact store is unavailable")

        if method == "HEAD" or request.method == "HEAD":
            await reader.close()
            if audited:
                await self._result_quietly(correlation_id, principal, artifact, "HEAD", "completed", 0)
            return Response(status_code=200, headers=headers)

        async def finish(outcome: str, sent: int) -> None:
            if not audited:
                return
            await self._result_quietly(correlation_id, principal, artifact, "GET", outcome, sent)

        return StreamingResponse(
            _stream(reader.read_chunk, request.is_disconnected, finish),
            status_code=200,
            headers=headers,
            media_type=None,
        )

    async def _result_quietly(
        self, correlation_id: str, principal: Principal, artifact: Artifact, method: str,
        outcome: str, bytes_sent: int,
    ) -> None:
        try:
            await self._row(
                phase="result", outcome=outcome,
                correlation_id=correlation_id, principal=principal,
                artifact=artifact, method=method, artifact_id=artifact.artifact_id,
                target_id=artifact.artifact_id, bytes_sent=bytes_sent,
            )
        except Exception as error:
            metrics = self._state.metrics
            if metrics is not None:
                metrics.record_audit_write_failure("result")
            LOGGER.error(
                "Data-plane result row failed; the already-sent response is unchanged",
                extra={
                    "event": "audit_write_failure", "correlationId": correlation_id,
                    "tool": "artifact_access", "outcome": "error", "errorCode": type(error).__name__,
                },
            )

    # ------------------------------------------------------------------ POST

    async def upload(self, request: Request) -> Response:
        correlation_id = uuid7()
        if not self._settings.artifact_upload_enabled:
            # deployment-disabled by default; 404/disabled posture
            return _error(correlation_id, "operation_disabled", "Artifact upload is not enabled")
        principal = await self.authenticate(request)
        if not principal.has_scope("ignition.read"):
            return _error(correlation_id, "permission_denied", "A valid ignition.read credential is required")
        kind = request.query_params.get("kind", "")
        if kind not in UPLOADABLE_KINDS:
            return _error(correlation_id, "invalid_argument", "kind must be one of: project_archive")
        content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
        if content_type != UPLOAD_MEDIA_TYPE:
            return _error(correlation_id, "invalid_argument", "Content-Type must be application/zip")
        raw_length = request.headers.get("content-length")
        if raw_length is None or not raw_length.isdigit():
            return _error(correlation_id, "invalid_argument", "A finite Content-Length is required")
        declared = int(raw_length)
        store = self._state.artifacts
        if store is None:
            return _error(correlation_id, "internal_error", "The artifact store is unavailable")
        quotas = store.quotas
        if declared > quotas.max_bytes:
            return _error(
                correlation_id, "limit_exceeded",
                f"declared size {declared} exceeds the single-artifact maximum {quotas.max_bytes}",
            )

        writer: Any = None
        uploaded_bytes = 0
        try:
            writer = await store.create(
                kind=kind, sensitivity="CONFIDENTIAL", retention_class="EXPORT",
                owner=principal.key, filename=f"{kind}-{correlation_id[:8]}.zip",
                media_type=UPLOAD_MEDIA_TYPE, correlation_id=correlation_id,
                declared_size=declared, validator=ZipSafetyValidator(),
            )
            async for chunk in request.stream():
                await writer.write(chunk)
            uploaded_bytes = writer.bytes_written
            if uploaded_bytes != declared:
                await writer.abort()
                writer = None
                return _error(
                    correlation_id, "invalid_argument",
                    f"Content-Length declared {declared} bytes but {uploaded_bytes} were sent",
                )
            artifact = await store.publish(writer)
            writer = None
        except GatewayError as error:
            if writer is not None:
                await writer.abort()
            return _error(correlation_id, error.code, error.message)
        except StorageUnavailable:
            if writer is not None:
                await writer.abort()
            return _error(correlation_id, "internal_error", "The artifact store is unavailable")
        except Exception:
            if writer is not None:
                await writer.abort()
            LOGGER.exception("Artifact upload failed")
            return _error(correlation_id, "internal_error", "Artifact upload failed")

        return JSONResponse(artifact.to_ref(), status_code=201)


async def _stream(
    read_chunk: "Callable[[], Any]",
    is_disconnected: "Callable[[], Any]",
    finish: "Callable[[str, int], Any]",
) -> AsyncGenerator[bytes, None]:
    """Bounded chunk egress; disconnect stops the stream; exactly one result row."""

    sent = 0
    outcome = "completed"
    try:
        while True:
            if await is_disconnected():
                outcome = "client_disconnected"
                break
            chunk = await read_chunk()
            if chunk is None:
                break
            sent += len(chunk)
            yield chunk
    except BaseException:
        outcome = "client_disconnected"
        raise
    finally:
        try:
            await asyncio.shield(finish(outcome, sent))
        except asyncio.CancelledError:
            pass


def register_artifact_routes(mcp: "FastMCP", state: "RuntimeState", settings: Settings) -> None:
    """Register GET/HEAD /artifacts/{artifactId} and POST /artifacts next to /health/*."""

    plane = ArtifactDataPlane(state, settings)

    @mcp.custom_route("/artifacts/{artifactId}", methods=["GET", "HEAD"], include_in_schema=False)
    async def artifact_fetch(request: Request) -> Response:
        return await plane.fetch(request)

    @mcp.custom_route("/artifacts", methods=["POST"], include_in_schema=False)
    async def artifact_upload(request: Request) -> Response:
        return await plane.upload(request)
