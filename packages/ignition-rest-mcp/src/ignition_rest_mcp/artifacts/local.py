"""D17 LocalArtifactStore: staging -> validate -> atomic publish with durable
crash-recoverable state, finite quotas, retention classes and bounded cleanup.

Layout under ``<data>/artifacts/``:

- ``staging/<id>.part``   in-flight bytes (O_EXCL, 0600)
- ``objects/<2-char shard>/<id>``  published objects

Metadata lives in the shared ``state`` SQLite database. Storage keys are
generated opaque IDs; user/Gateway filenames are display metadata only (D17).

Durable create sequence (each split point is crash-recoverable by ``reconcile``):

1. INSERT row STAGING with quota reservation + staging deadline (commit)
2. stream ``staging/<id>.part`` with incremental size/SHA-256 and in-flight caps
3. run the declared validator against the staged file (failure -> discard)
4. UPDATE row PUBLISHING with final size + sha256 (commit)
5. os.replace staging -> object; fsync object dir + staging dir
6. UPDATE row READY, reservation converts into used bytes
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
import errno
import hashlib
import os
from pathlib import Path
import re
import shutil
import time
from typing import Any, Callable

from ignition_rest_mcp.artifacts.model import (
    ARTIFACT_KINDS,
    RETENTION_CLASSES,
    SENSITIVITY_CLASSES,
    Artifact,
    ArtifactReader,
    ArtifactState,
    ArtifactValidator,
    DOWNLOAD_PATH_PREFIX,  # noqa: F401  (re-exported for consumers)
)
from ignition_rest_mcp.errors import GatewayError
from ignition_rest_mcp.operation import uuid7
from ignition_rest_mcp.storage.database import Database, transaction
from ignition_rest_mcp.storage.records import iso_utc, utc_now

MAX_LIST_LIMIT = 500
MAX_ID_LENGTH = 128
ID_PATTERN = r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$"
FILENAME_LIMIT = 200
CHUNK_SIZE = 1024 * 1024
FREE_DISK_CHECK_INTERVAL_SECONDS = 1.0

_COLUMNS = (
    "artifact_id", "kind", "state", "filename", "media_type", "size_bytes", "sha256", "sensitivity",
    "retention_class", "created_at", "expires_at", "owner_principal", "gateway_id", "project_name",
    "correlation_id", "transaction_id", "retention_lock", "reserved_bytes", "staging_deadline_at",
)
_SELECT_COLUMNS = ", ".join(_COLUMNS)
_ID_RE = re.compile(ID_PATTERN)


def validate_artifact_id(value: str) -> str:
    if (
        not isinstance(value, str) or not 1 <= len(value) <= MAX_ID_LENGTH
        or _ID_RE.fullmatch(value) is None
    ):
        raise GatewayError("invalid_argument", "artifactId syntax is invalid")
    return value


def sanitize_display_filename(value: str, fallback: str) -> str:
    """Display metadata only; never used to derive storage paths (D17)."""

    name = "".join(
        character for character in str(value)
        if character not in "/\\\r\n\x00" and ord(character) >= 0x20
    ).strip()
    while ".." in name:
        name = name.replace("..", ".")
    name = name[:FILENAME_LIMIT]
    return name or fallback


class QuotaConfig:
    """Finite, validated artifact quotas (D17: 'unlimited' is not a valid posture)."""

    def __init__(
        self, *, max_bytes: int, max_total_bytes: int, max_count: int,
        min_free_bytes: int, min_free_ratio: float,
        export_ttl_hours: int, recovery_ttl_days: int, staging_deadline_seconds: float,
        cleanup_interval_seconds: float, cleanup_batch: int,
    ) -> None:
        for name, value in (
            ("max_bytes", max_bytes), ("max_total_bytes", max_total_bytes), ("max_count", max_count),
            ("min_free_bytes", min_free_bytes), ("export_ttl_hours", export_ttl_hours),
            ("recovery_ttl_days", recovery_ttl_days), ("cleanup_batch", cleanup_batch),
        ):
            if int(value) <= 0:
                raise ValueError(f"quota {name} must be a finite positive value (0/negative rejected)")
        if max_bytes > max_total_bytes:
            raise ValueError("quota max_bytes must not exceed max_total_bytes")
        if not 0.0 <= min_free_ratio < 1.0:
            raise ValueError("min_free_ratio must be in [0, 1)")
        if staging_deadline_seconds <= 0 or cleanup_interval_seconds <= 0:
            raise ValueError("staging/cleanup intervals must be positive")
        self.max_bytes = int(max_bytes)
        self.max_total_bytes = int(max_total_bytes)
        self.max_count = int(max_count)
        self.min_free_bytes = int(min_free_bytes)
        self.min_free_ratio = float(min_free_ratio)
        self.export_ttl_hours = int(export_ttl_hours)
        self.recovery_ttl_days = int(recovery_ttl_days)
        self.staging_deadline_seconds = float(staging_deadline_seconds)
        self.cleanup_interval_seconds = float(cleanup_interval_seconds)
        self.cleanup_batch = int(cleanup_batch)


class LocalWriter:
    """Streaming staging sink: O_EXCL 0600 part-file, incremental size + SHA-256,
    in-flight cap and periodic free-disk rechecks as bytes arrive."""

    def __init__(
        self, store: "LocalArtifactStore", artifact_id: str, limit_bytes: int,
        validator: ArtifactValidator | None,
    ) -> None:
        self.artifact_id = artifact_id
        self.bytes_written = 0
        self._store = store
        self._limit = limit_bytes
        self._validator = validator
        self._digest = hashlib.sha256()
        self._fd: int | None = None
        self._closed = False

    async def write(self, chunk: bytes) -> None:
        if self._closed:
            raise GatewayError("internal_error", "artifact writer already closed")
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            raise GatewayError("internal_error", "artifact chunks must be bytes")
        data = bytes(chunk)
        projected = self.bytes_written + len(data)
        if projected > self._limit:
            await self.abort()
            raise GatewayError(
                "limit_exceeded",
                f"artifact exceeds the bound of {self._limit} bytes at {projected} bytes; "
                "the partial upload was discarded and nothing was published",
            )
        await self._store._free_disk_guard(force=False)
        if self._fd is None:
            self._fd = await asyncio.to_thread(self._store._open_staging, self.artifact_id)
        await asyncio.to_thread(self._store._write_chunk, self._fd, data)
        self._digest.update(data)
        self.bytes_written = projected

    async def abort(self) -> None:
        if self._closed:
            return
        self._closed = True
        fd, self._fd = self._fd, None
        await self._store._discard(self.artifact_id, fd)

    async def _seal(self) -> tuple[str, int]:
        if self._fd is None:
            self._fd = await asyncio.to_thread(self._store._open_staging, self.artifact_id)
            await asyncio.to_thread(os.fsync, self._fd)
        else:
            await asyncio.to_thread(os.fsync, self._fd)
        return self._digest.hexdigest(), self.bytes_written


class _StreamReader:
    def __init__(self, artifact: Artifact, fd: int) -> None:
        self.artifact = artifact
        self._fd: int | None = fd

    async def read_chunk(self) -> bytes | None:
        fd = self._fd
        if fd is None:
            return None
        chunk = await asyncio.to_thread(os.read, fd, CHUNK_SIZE)
        if not chunk:
            await self.close()
            return None
        return chunk

    async def close(self) -> None:
        fd, self._fd = self._fd, None
        if fd is not None:
            await asyncio.to_thread(os.close, fd)


class LocalArtifactStore:
    def __init__(self, db: Database, data_dir: Path, quotas: QuotaConfig) -> None:
        self._db = db
        self._root = data_dir / "artifacts"
        self._staging = self._root / "staging"
        self._objects = self._root / "objects"
        self._quotas = quotas
        self._last_free_check = 0.0
        self._fail_hook: Callable[[str], None] = lambda point: None

    @property
    def quotas(self) -> QuotaConfig:
        return self._quotas

    # ------------------------------------------------------------------ layout

    def prepare(self) -> None:
        for directory in (self._root, self._staging, self._objects):
            directory.mkdir(mode=0o700, exist_ok=True)
            os.chmod(directory, 0o700)

    def _object_path(self, artifact_id: str) -> Path:
        return self._objects / artifact_id[:2] / artifact_id

    def _staging_path(self, artifact_id: str) -> Path:
        return self._staging / f"{artifact_id}.part"

    def _open_staging(self, artifact_id: str) -> int:
        self._staging.mkdir(parents=True, mode=0o700, exist_ok=True)
        return os.open(self._staging_path(artifact_id), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)

    def _write_chunk(self, fd: int, data: bytes) -> None:
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            view = view[written:]

    # ------------------------------------------------------------------ quota

    async def _free_disk_guard(self, *, force: bool) -> None:
        now = time.monotonic()
        if not force and now - self._last_free_check < FREE_DISK_CHECK_INTERVAL_SECONDS:
            return
        self._last_free_check = now
        usage = await asyncio.to_thread(shutil.disk_usage, str(self._root))
        quota = self._quotas
        if usage.free < quota.min_free_bytes or (
            quota.min_free_ratio > 0 and usage.free < usage.total * quota.min_free_ratio
        ):
            raise GatewayError(
                "limit_exceeded",
                "free disk fell below the configured minimum; the operation was refused",
            )

    def _quota_guard(self, conn: Any, reserve_bytes: int) -> None:
        totals = conn.execute(
            "SELECT COALESCE(SUM(size_bytes), 0), COALESCE(SUM(reserved_bytes), 0), COUNT(*)"
            " FROM artifacts WHERE state IN ('STAGING','PUBLISHING','READY','DELETING')"
        ).fetchone()
        used, reserved, active_count = int(totals[0]), int(totals[1]), int(totals[2])
        if active_count + 1 > self._quotas.max_count:
            raise GatewayError(
                "limit_exceeded",
                f"artifact count would exceed the configured maximum of {self._quotas.max_count}",
            )
        if reserve_bytes > self._quotas.max_bytes:
            raise GatewayError(
                "limit_exceeded",
                f"declared artifact size {reserve_bytes} exceeds the single-artifact maximum "
                f"of {self._quotas.max_bytes} bytes",
            )
        if used + reserved + reserve_bytes > self._quotas.max_total_bytes:
            raise GatewayError(
                "limit_exceeded",
                f"artifact bytes used+reserved ({used + reserved}) plus reservation ({reserve_bytes}) "
                f"would exceed the configured total of {self._quotas.max_total_bytes}",
            )

    # ------------------------------------------------------------------ create

    async def create(
        self, *, kind: str, sensitivity: str, retention_class: str, owner: str,
        filename: str, media_type: str, correlation_id: str, gateway_id: str = "",
        project_name: str = "", transaction_id: str = "", declared_size: int | None = None,
        validator: ArtifactValidator | None = None,
    ) -> LocalWriter:
        if kind not in ARTIFACT_KINDS:
            raise GatewayError("invalid_argument", "unknown artifact kind")
        if sensitivity not in SENSITIVITY_CLASSES:
            raise GatewayError("invalid_argument", "unknown sensitivity class")
        if retention_class not in RETENTION_CLASSES:
            raise GatewayError("invalid_argument", "unknown retention class")
        if validator is not None and not callable(getattr(validator, "validate", None)):
            raise GatewayError("internal_error", "artifact validator is not callable")
        owner_key = str(owner)
        if not owner_key:
            raise GatewayError("internal_error", "artifacts require a verified owning principal")
        display = sanitize_display_filename(filename, fallback=f"{kind}.bin")
        reservation = min(int(declared_size), self._quotas.max_bytes) if declared_size is not None \
            else self._quotas.max_bytes
        if declared_size is not None and declared_size > self._quotas.max_bytes:
            raise GatewayError(
                "limit_exceeded",
                f"declared artifact size {declared_size} exceeds the single-artifact maximum "
                f"of {self._quotas.max_bytes} bytes",
            )
        await self._free_disk_guard(force=True)
        artifact_id = uuid7()
        created = utc_now()
        expires = (
            iso_utc(created + timedelta(hours=self._quotas.export_ttl_hours))
            if retention_class == "EXPORT" else None
        )
        deadline = iso_utc(created + timedelta(seconds=self._quotas.staging_deadline_seconds))

        def _insert(conn: Any) -> None:
            with transaction(conn):
                self._quota_guard(conn, reservation)
                conn.execute(
                    f"INSERT INTO artifacts ({_SELECT_COLUMNS}) VALUES ({', '.join(['?'] * len(_COLUMNS))})",
                    (
                        artifact_id, kind, "STAGING", display, media_type, 0, "", sensitivity,
                        retention_class, iso_utc(created), expires, owner_key, gateway_id, project_name,
                        correlation_id, transaction_id, 0, reservation, deadline,
                    ),
                )

        await self._db.run(_insert)
        self._fail_hook("staged")
        return LocalWriter(self, artifact_id, reservation, validator)

    async def publish(self, writer: LocalWriter) -> Artifact:
        sha256, size = await writer._seal()
        self._fail_hook("sealed")
        row = await self._fetch_row(writer.artifact_id)
        if row is None or row.state is not ArtifactState.STAGING:
            await writer.abort()
            raise GatewayError("internal_error", "artifact staging row disappeared before validation")
        if size == 0:
            await self._discard(writer.artifact_id, writer._fd)
            raise GatewayError("invalid_argument", "refusing to publish a zero-byte artifact")
        if writer._validator is not None:
            try:
                await writer._validator.validate(str(self._staging_path(writer.artifact_id)))
            except GatewayError:
                await self._discard(writer.artifact_id, writer._fd)
                raise
            except ValueError as error:
                await self._discard(writer.artifact_id, writer._fd)
                raise GatewayError(
                    "invalid_argument", f"artifact failed validation and was discarded: {error}",
                ) from error
        self._fail_hook("validated")

        def _mark_publishing(conn: Any) -> None:
            with transaction(conn):
                current = conn.execute(
                    "SELECT state FROM artifacts WHERE artifact_id = ?", (writer.artifact_id,),
                ).fetchone()
                if current is None or current[0] != "STAGING":
                    raise GatewayError("internal_error", "artifact left STAGING before publication")
                conn.execute(
                    "UPDATE artifacts SET state = 'PUBLISHING', size_bytes = ?, sha256 = ?"
                    " WHERE artifact_id = ?",
                    (size, sha256, writer.artifact_id),
                )

        await self._db.run(_mark_publishing)
        self._fail_hook("publishing")
        fd, writer._fd, writer._closed = writer._fd, None, True  # fd ownership -> replace step
        try:
            await asyncio.to_thread(self._replace_object, writer.artifact_id, size, sha256)
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
        self._fail_hook("replaced")

        def _ready(conn: Any) -> None:
            with transaction(conn):
                conn.execute(
                    "UPDATE artifacts SET state = 'READY', reserved_bytes = 0 WHERE artifact_id = ?",
                    (writer.artifact_id,),
                )

        await self._db.run(_ready)
        artifact = await self.stat(writer.artifact_id)
        self._fail_hook("ready")
        return artifact

    def _replace_object(self, artifact_id: str, size: int, sha256: str) -> None:
        source = self._staging_path(artifact_id)
        target = self._object_path(artifact_id)
        if not source.exists():
            raise GatewayError("internal_error", "staging object vanished before publication")
        digest = hashlib.sha256()
        read_size = 0
        with source.open("rb") as stream:
            for block in iter(lambda: stream.read(CHUNK_SIZE), b""):
                read_size += len(block)
                digest.update(block)
        if read_size != size or digest.hexdigest() != sha256:
            raise GatewayError("internal_error", "staged bytes changed before publication")
        target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        os.chmod(target.parent, 0o700)
        os.replace(source, target)
        _fsync_dir(target.parent)
        _fsync_dir(self._staging)
        os.chmod(target, 0o600)

    # ------------------------------------------------------------------ read

    async def _fetch_row(self, artifact_id: str) -> Artifact | None:
        def _read(conn: Any) -> Artifact | None:
            row = conn.execute(
                f"SELECT {_SELECT_COLUMNS} FROM artifacts WHERE artifact_id = ?", (artifact_id,),
            ).fetchone()
            return _to_artifact(row) if row is not None else None

        return await self._db.run(_read)

    async def stat(self, artifact_id: str) -> Artifact:
        validate_artifact_id(artifact_id)
        artifact = await self._fetch_row(artifact_id)
        if artifact is None or artifact.state is not ArtifactState.READY:
            raise GatewayError("not_found", "artifact not found")
        return artifact

    async def count_ready(self) -> int:
        """GAUGE source for metrics and quota diagnostics (low-cardinality use only)."""

        def _read(conn: Any) -> int:
            return int(conn.execute("SELECT COUNT(*) FROM artifacts WHERE state = 'READY'").fetchone()[0])

        return await self._db.run(_read)

    async def exists(self, artifact_id: str) -> bool:
        if not isinstance(artifact_id, str) or _ID_RE.fullmatch(artifact_id) is None:
            return False
        artifact = await self._fetch_row(artifact_id)
        return artifact is not None and artifact.state is ArtifactState.READY

    async def open_read(self, artifact_id: str) -> ArtifactReader:
        """READY check and fd open in one guarded step; an already-open fd keeps
        streaming after unlink, and non-READY states answer not_found."""

        validate_artifact_id(artifact_id)

        def _open(conn: Any) -> tuple[Artifact | None, int | None]:
            row = conn.execute(
                f"SELECT {_SELECT_COLUMNS} FROM artifacts WHERE artifact_id = ?", (artifact_id,),
            ).fetchone()
            if row is None or row[2] != "READY":
                return None, None
            try:
                fd = os.open(self._object_path(artifact_id), os.O_RDONLY)
            except OSError as error:
                if error.errno == errno.ENOENT:
                    conn.execute(
                        "UPDATE artifacts SET state = 'LOST' WHERE artifact_id = ? AND state = 'READY'",
                        (artifact_id,),
                    )
                    return None, None
                raise
            return _to_artifact(row), fd

        artifact, fd = await self._db.run(_open)
        if artifact is None or fd is None:
            raise GatewayError("not_found", "artifact not found")
        return _StreamReader(artifact, fd)

    # ------------------------------------------------------------------ list

    async def list(
        self, *, principal: str, allow_admin: bool, kind: str | None, limit: int, offset: int,
    ) -> tuple[list[Artifact], int]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIST_LIMIT:
            raise GatewayError("invalid_argument", f"limit must be between 1 and {MAX_LIST_LIMIT}")
        if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 1_000_000:
            raise GatewayError("invalid_argument", "offset out of bounds")
        if kind is not None and kind not in ARTIFACT_KINDS:
            raise GatewayError("invalid_argument", "unknown artifact kind")

        def _read(conn: Any) -> tuple[list[Artifact], int]:
            where = "state = 'READY'"
            parameters: list[Any] = []
            if not allow_admin:
                where += " AND owner_principal = ?"
                parameters.append(principal)
            if kind is not None:
                where += " AND kind = ?"
                parameters.append(kind)
            total = int(conn.execute(f"SELECT COUNT(*) FROM artifacts WHERE {where}", parameters).fetchone()[0])
            rows = conn.execute(
                f"SELECT {_SELECT_COLUMNS} FROM artifacts WHERE {where}"
                " ORDER BY created_at DESC, artifact_id DESC LIMIT ? OFFSET ?",
                (*parameters, limit, offset),
            ).fetchall()
            return [_to_artifact(row) for row in rows], total

        return await self._db.run(_read)

    # ------------------------------------------------------------------ lifecycle

    async def _discard(self, artifact_id: str, fd: int | None) -> None:
        """Delete staging file + row (idempotent); reservation dies with the row."""

        def _cleanup(conn: Any) -> None:
            with transaction(conn):
                conn.execute("DELETE FROM artifacts WHERE artifact_id = ?", (artifact_id,))

        await self._db.run(_cleanup)
        await asyncio.to_thread(self._remove_staging, artifact_id, fd)

    def _unlink_object(self, artifact_id: str) -> None:
        path = self._object_path(artifact_id)
        try:
            if path.exists():
                path.unlink()
                _fsync_dir(path.parent)
        except OSError:
            pass
        try:
            self._staging_path(artifact_id).unlink(missing_ok=True)
        except OSError:
            pass

    def _remove_staging(self, artifact_id: str, fd: int | None) -> None:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            self._staging_path(artifact_id).unlink(missing_ok=True)
        except OSError:
            pass

    async def delete_internal(self, artifact_id: str) -> None:
        """Internal deletion; retention-locked artifacts are refused (D17)."""

        validate_artifact_id(artifact_id)

        def _start(conn: Any) -> bool:
            with transaction(conn):
                row = conn.execute(
                    "SELECT state, retention_lock FROM artifacts WHERE artifact_id = ?", (artifact_id,),
                ).fetchone()
                if row is None:
                    return False
                if int(row[1]) == 1:
                    raise GatewayError("conflict", "artifact is retention-locked and cannot be deleted")
                conn.execute("UPDATE artifacts SET state = 'DELETING' WHERE artifact_id = ?", (artifact_id,))
                return True

        started = await self._db.run(_start)
        if not started:
            return
        await asyncio.to_thread(self._unlink_object, artifact_id)

        def _finish(conn: Any) -> None:
            with transaction(conn):
                conn.execute("DELETE FROM artifacts WHERE artifact_id = ?", (artifact_id,))

        await self._db.run(_finish)

    async def cleanup_expired(self) -> int:
        """TTL cleanup in bounded batches; locked and in-flight rows are skipped."""

        now = iso_utc(utc_now())

        def _candidates(conn: Any) -> list[str]:
            rows = conn.execute(
                "SELECT artifact_id FROM artifacts WHERE state = 'READY' AND retention_lock = 0"
                " AND expires_at IS NOT NULL AND expires_at <= ? ORDER BY expires_at ASC LIMIT ?",
                (now, self._quotas.cleanup_batch),
            ).fetchall()
            return [str(row[0]) for row in rows]

        removed = 0
        while True:
            batch = await self._db.run(_candidates)
            if not batch:
                return removed
            for artifact_id in batch:
                await self.delete_internal(artifact_id)
                removed += 1
            if len(batch) < self._quotas.cleanup_batch:
                return removed

    # ------------------------------------------------------------------ retention

    async def promote_recovery(self, artifact_id: str, transaction_id: str) -> Artifact:
        """D16 baseline promotion: RECOVERY + retention lock; the 7-day clock starts
        only when the owning transaction reaches a lock-releasing terminal state."""

        validate_artifact_id(artifact_id)

        def _promote(conn: Any) -> None:
            with transaction(conn):
                cursor = conn.execute(
                    "UPDATE artifacts SET retention_class = 'RECOVERY', retention_lock = 1,"
                    " expires_at = NULL, transaction_id = ?"
                    " WHERE artifact_id = ? AND state = 'READY'",
                    (transaction_id, artifact_id),
                )
                if cursor.rowcount == 0:
                    raise GatewayError("not_found", "artifact not found for recovery promotion")

        await self._db.run(_promote)
        return await self.stat(artifact_id)

    async def release_retention(self, *artifact_ids: str) -> None:
        """Unlocks RECOVERY artifacts and starts their TTL clock (slice 7 release set)."""

        expires = iso_utc(utc_now() + timedelta(days=self._quotas.recovery_ttl_days))

        def _release(conn: Any) -> None:
            with transaction(conn):
                for artifact_id in artifact_ids:
                    conn.execute(
                        "UPDATE artifacts SET retention_lock = 0, expires_at = ?"
                        " WHERE artifact_id = ? AND retention_class = 'RECOVERY' AND state = 'READY'",
                        (expires, artifact_id),
                    )

        await self._db.run(_release)

    # ------------------------------------------------------------------ reconcile

    async def reconcile(self, *, batch: int, deadline_seconds: float) -> dict[str, int]:
        """Crash recovery at every durable split point + orphan sweep, bounded in
        batches and elapsed deadline."""

        started = time.monotonic()
        stats = {
            "expired_staging": 0, "recovered_ready": 0, "discarded": 0,
            "finished_delete": 0, "lost": 0, "orphan_files": 0,
        }
        now = iso_utc(utc_now())

        def _stale_staging(conn: Any) -> list[str]:
            rows = conn.execute(
                "SELECT artifact_id FROM artifacts WHERE state = 'STAGING' AND staging_deadline_at < ? LIMIT ?",
                (now, batch),
            ).fetchall()
            return [str(row[0]) for row in rows]

        for artifact_id in await self._db.run(_stale_staging):
            await self._discard(artifact_id, None)
            stats["expired_staging"] += 1
            if time.monotonic() - started >= deadline_seconds:
                return stats

        def _publishing(conn: Any) -> list[tuple[str, int, str]]:
            rows = conn.execute(
                "SELECT artifact_id, size_bytes, sha256 FROM artifacts WHERE state = 'PUBLISHING' LIMIT ?",
                (batch,),
            ).fetchall()
            return [(str(r[0]), int(r[1]), str(r[2])) for r in rows]

        for artifact_id, size, sha256 in await self._db.run(_publishing):
            matches = await asyncio.to_thread(self._object_matches, artifact_id, size, sha256)
            if matches:
                def _complete(conn: Any, aid: str = artifact_id) -> None:
                    with transaction(conn):
                        conn.execute(
                            "UPDATE artifacts SET state = 'READY', reserved_bytes = 0 WHERE artifact_id = ?",
                            (aid,),
                        )

                await self._db.run(_complete)
                stats["recovered_ready"] += 1
            else:
                await self._discard(artifact_id, None)
                stats["discarded"] += 1
            if time.monotonic() - started >= deadline_seconds:
                return stats

        def _deleting(conn: Any) -> list[str]:
            rows = conn.execute(
                "SELECT artifact_id FROM artifacts WHERE state = 'DELETING' LIMIT ?", (batch,),
            ).fetchall()
            return [str(row[0]) for row in rows]

        for artifact_id in await self._db.run(_deleting):
            await asyncio.to_thread(self._unlink_object, artifact_id)

            def _row_gone(conn: Any, aid: str = artifact_id) -> None:
                with transaction(conn):
                    conn.execute("DELETE FROM artifacts WHERE artifact_id = ?", (aid,))

            await self._db.run(_row_gone)
            stats["finished_delete"] += 1
            if time.monotonic() - started >= deadline_seconds:
                return stats

        def _missing_objects(conn: Any) -> list[str]:
            rows = conn.execute(
                "SELECT artifact_id FROM artifacts WHERE state = 'READY' ORDER BY artifact_id LIMIT ?",
                (batch,),
            ).fetchall()
            return [str(row[0]) for row in rows if not self._object_path(str(row[0])).exists()]

        for artifact_id in await self._db.run(_missing_objects):
            def _mark(conn: Any, aid: str = artifact_id) -> None:
                with transaction(conn):
                    conn.execute("UPDATE artifacts SET state = 'LOST' WHERE artifact_id = ?", (aid,))

            await self._db.run(_mark)
            stats["lost"] += 1

        if time.monotonic() - started < deadline_seconds:
            stats["orphan_files"] = await self._sweep_orphans(batch, started, deadline_seconds)
        return stats

    def _object_matches(self, artifact_id: str, size: int, sha256: str) -> bool:
        path = self._object_path(artifact_id)
        try:
            if not path.exists() or path.stat().st_size != size:
                return False
        except OSError:
            return False
        digest = hashlib.sha256()
        try:
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(CHUNK_SIZE), b""):
                    digest.update(block)
        except OSError:
            return False
        return digest.hexdigest() == sha256

    async def _sweep_orphans(self, batch: int, started: float, deadline: float) -> int:
        def _live_ids(conn: Any) -> set[str]:
            rows = conn.execute("SELECT artifact_id FROM artifacts").fetchall()
            return {str(row[0]) for row in rows}

        live = await self._db.run(_live_ids)
        removed = 0

        def _sweep() -> int:
            count = 0
            if self._staging.exists():
                for entry in self._staging.iterdir():
                    if count >= batch or time.monotonic() - started >= deadline:
                        return count
                    candidate = entry.name[: -len(".part")] if entry.name.endswith(".part") else entry.name
                    if candidate not in live or not _ID_RE.fullmatch(candidate):
                        entry.unlink(missing_ok=True)
                        count += 1
            if self._objects.exists():
                for shard in sorted(self._objects.iterdir()):
                    if not shard.is_dir():
                        continue
                    for entry in sorted(shard.iterdir()):
                        if count >= batch or time.monotonic() - started >= deadline:
                            return count
                        if entry.name not in live or not _ID_RE.fullmatch(entry.name):
                            entry.unlink(missing_ok=True)
                            count += 1
            return count

        removed = await asyncio.to_thread(_sweep)
        return removed


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _to_artifact(row: tuple[Any, ...]) -> Artifact:
    values = dict(zip(_COLUMNS, row, strict=True))
    return Artifact(
        artifact_id=str(values["artifact_id"]),
        kind=str(values["kind"]),
        state=ArtifactState(str(values["state"])),
        filename=str(values["filename"]),
        media_type=str(values["media_type"]),
        size_bytes=int(values["size_bytes"]),
        sha256=str(values["sha256"]),
        sensitivity=str(values["sensitivity"]),
        retention_class=str(values["retention_class"]),
        created_at=str(values["created_at"]),
        expires_at=values["expires_at"],
        owner=str(values["owner_principal"]),
        gateway_id=str(values["gateway_id"]),
        project_name=str(values["project_name"]),
        correlation_id=str(values["correlation_id"]),
        transaction_id=str(values["transaction_id"]),
        retention_lock=bool(values["retention_lock"]),
    )


def quotas_from_settings(settings: Any) -> QuotaConfig:
    return QuotaConfig(
        max_bytes=settings.artifact_max_bytes,
        max_total_bytes=settings.artifact_total_bytes,
        max_count=settings.artifact_max_count,
        min_free_bytes=settings.artifact_min_free_bytes,
        min_free_ratio=settings.artifact_min_free_ratio,
        export_ttl_hours=settings.artifact_export_ttl_hours,
        recovery_ttl_days=settings.artifact_recovery_ttl_days,
        staging_deadline_seconds=settings.artifact_staging_deadline_seconds,
        cleanup_interval_seconds=settings.artifact_cleanup_interval_seconds,
        cleanup_batch=settings.artifact_cleanup_batch,
    )


__all__ = [
    "LocalArtifactStore", "LocalWriter", "QuotaConfig", "quotas_from_settings",
    "sanitize_display_filename", "validate_artifact_id", "MAX_LIST_LIMIT", "DOWNLOAD_PATH_PREFIX",
]
