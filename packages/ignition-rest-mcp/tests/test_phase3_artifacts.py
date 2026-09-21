"""Slice 2 (Phase 3 / G3): LocalArtifactStore durable-create, crash recovery,
quotas, retention, reconciliation and bounded cleanup."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from ignition_rest_mcp.artifacts.local import (
    LocalArtifactStore,
    QuotaConfig,
    quotas_from_settings,
    sanitize_display_filename,
    validate_artifact_id,
)
from ignition_rest_mcp.artifacts.model import ArtifactState
from ignition_rest_mcp.errors import GatewayError
from ignition_rest_mcp.operation import uuid7
from ignition_rest_mcp.storage.database import Database, StorageUnavailable
from ignition_rest_mcp.storage.schema import STATE_DDL
from test_config import _settings

ZIP_BYTES = b"PK\x03\x04" + b"zip-shaped-payload" * 10


class _Validator:
    name = "test-validator"

    def __init__(self, reject: bool = False) -> None:
        self.reject = reject
        self.seen: list[str] = []

    async def validate(self, path: str) -> None:
        self.seen.append(path)
        if self.reject:
            raise ValueError("validator refused")


class _Store:
    def __init__(self, tmp: Path, **quota_overrides: Any) -> None:
        self.db = Database("state", tmp / "state.db", STATE_DDL)
        quotas = QuotaConfig(**{
            "max_bytes": 4096,
            "max_total_bytes": 12 * 1024 * 1024,
            "max_count": 20,
            "min_free_bytes": 1,
            "min_free_ratio": 0.0,
            "export_ttl_hours": 24,
            "recovery_ttl_days": 7,
            "staging_deadline_seconds": 900.0,
            "cleanup_interval_seconds": 300.0,
            "cleanup_batch": 5,
            **quota_overrides,
        })
        self.store = LocalArtifactStore(self.db, tmp, quotas)

    async def open(self) -> None:
        await self.db.open()
        self.store.prepare()

    async def close(self) -> None:
        await self.db.close()


def _run(coro: Any) -> Any:
    async def main() -> Any:
        return await coro

    return asyncio.run(main())


def _create_store(tmp: Path, **overrides: Any) -> _Store:
    holder = _Store(tmp, **overrides)
    _run(holder.open())
    return holder


async def _write_all(writer: Any, chunks: list[bytes]) -> None:
    for chunk in chunks:
        await writer.write(chunk)


# --------------------------------------------------------------------------- helpers


def test_artifact_id_syntax_validated_before_storage_access() -> None:
    assert validate_artifact_id(uuid7())
    for bad in ("", "../etc/passwd", "a" * 129, "/absolute", "with space", "trav/eral", "nul\x00"):
        with pytest.raises(GatewayError) as captured:
            validate_artifact_id(bad)
        assert captured.value.code == "invalid_argument"


def test_display_filename_sanitized_never_path_derived() -> None:
    assert "/" not in sanitize_display_filename("../../evil/name.zip", fallback="f")
    assert ".." not in sanitize_display_filename("..\\..\\x", fallback="f")
    assert sanitize_display_filename("   ", fallback="fallback.bin") == "fallback.bin"
    assert len(sanitize_display_filename("x" * 500, fallback="f")) <= 200


def test_quota_config_rejects_unlimited_postures() -> None:
    base: dict[str, Any] = dict(
        max_bytes=10, max_total_bytes=100, max_count=5, min_free_bytes=1, min_free_ratio=0.0,
        export_ttl_hours=24, recovery_ttl_days=7, staging_deadline_seconds=60,
        cleanup_interval_seconds=60, cleanup_batch=5,
    )
    for key in ("max_bytes", "max_total_bytes", "max_count", "min_free_bytes", "export_ttl_hours",
                "recovery_ttl_days", "cleanup_batch"):
        bad = dict(base)
        bad[key] = 0
        with pytest.raises(ValueError):
            QuotaConfig(**bad)  # type: ignore[arg-type]
        bad[key] = -1
        with pytest.raises(ValueError):
            QuotaConfig(**bad)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        QuotaConfig(**{**base, "min_free_ratio": 1.0})  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        QuotaConfig(**{**base, "max_bytes": 101, "max_total_bytes": 100})  # type: ignore[arg-type]


def test_settings_supply_finite_quota_defaults() -> None:
    quotas = quotas_from_settings(_settings())
    assert quotas.max_bytes > 0 and quotas.max_total_bytes >= quotas.max_bytes
    assert quotas.export_ttl_hours == 24 and quotas.recovery_ttl_days == 7


def test_layout_is_ids_only_with_private_modes(tmp_path: Path) -> None:
    holder = _create_store(tmp_path)
    try:
        async def scenario() -> None:
            writer = await holder.store.create(
                kind="project_export", sensitivity="CONFIDENTIAL", retention_class="EXPORT",
                owner="none:test", filename="My Project.zip", media_type="application/zip",
                correlation_id=uuid7(),
            )
            await _write_all(writer, [ZIP_BYTES])
            artifact = await holder.store.publish(writer)
            object_path = tmp_path / "artifacts" / "objects" / artifact.artifact_id[:2] / artifact.artifact_id
            assert object_path.exists()
            assert "My Project" not in str(object_path)
            assert oct(object_path.stat().st_mode & 0o777) == "0o600"
            assert oct((tmp_path / "artifacts").stat().st_mode & 0o777) == "0o700"

        _run(scenario())
    finally:
        _run(holder.close())


# --------------------------------------------------------------------------- create/publish


def test_create_publish_round_trip_and_ref_shape(tmp_path: Path) -> None:
    holder = _create_store(tmp_path)
    try:
        async def scenario() -> None:
            writer = await holder.store.create(
                kind="project_export", sensitivity="CONFIDENTIAL", retention_class="EXPORT",
                owner="none:test", filename="Demo.zip", media_type="application/zip",
                correlation_id="corr-1", project_name="Demo",
            )
            await _write_all(writer, [ZIP_BYTES[:7], ZIP_BYTES[7:]])
            artifact = await holder.store.publish(writer)
            assert artifact.state is ArtifactState.READY
            assert artifact.size_bytes == len(ZIP_BYTES)
            ref = artifact.to_ref()
            assert ref["kind"] == "project_export"
            assert ref["sensitivity"] == "CONFIDENTIAL"
            assert ref["retentionClass"] == "EXPORT"
            assert ref["expiresAt"] is not None  # EXPORT TTL started at creation
            assert ref["download"] == {"path": f"/artifacts/{artifact.artifact_id}"}
            assert artifact.owner == "none:test"  # internal metadata, not in the ref
            assert "owner" not in ref and "principal" not in ref

        _run(scenario())
    finally:
        _run(holder.close())


def test_validation_failure_publishes_nothing(tmp_path: Path) -> None:
    holder = _create_store(tmp_path)
    try:
        async def scenario() -> None:
            validator = _Validator(reject=True)
            writer = await holder.store.create(
                kind="project_archive", sensitivity="CONFIDENTIAL", retention_class="EXPORT",
                owner="none:test", filename="bad.zip", media_type="application/zip",
                correlation_id="c", validator=validator,
            )
            await _write_all(writer, [ZIP_BYTES])
            with pytest.raises(GatewayError) as captured:
                await holder.store.publish(writer)
            assert captured.value.code == "invalid_argument"
            assert validator.seen and Path(validator.seen[0]).exists() is False
            assert await holder.store.count_ready() == 0

        _run(scenario())
    finally:
        _run(holder.close())


def test_zero_byte_artifact_refused(tmp_path: Path) -> None:
    holder = _create_store(tmp_path)
    try:
        async def scenario() -> None:
            writer = await holder.store.create(
                kind="tag_config_export", sensitivity="CONFIDENTIAL", retention_class="EXPORT",
                owner="none:test", filename="empty.json", media_type="application/json",
                correlation_id="c",
            )
            with pytest.raises(GatewayError, match="zero-byte"):
                await holder.store.publish(writer)

        _run(scenario())
    finally:
        _run(holder.close())


def test_size_cap_enforced_midstream_never_published(tmp_path: Path) -> None:
    holder = _create_store(tmp_path, max_bytes=100)
    try:
        async def scenario() -> None:
            writer = await holder.store.create(
                kind="project_export", sensitivity="CONFIDENTIAL", retention_class="EXPORT",
                owner="none:test", filename="big.zip", media_type="application/zip",
                correlation_id="c",
            )
            await writer.write(b"x" * 90)
            with pytest.raises(GatewayError) as captured:
                await writer.write(b"y" * 20)
            assert captured.value.code == "limit_exceeded"
            assert list((tmp_path / "artifacts" / "staging").iterdir()) == []
            assert await holder.store.count_ready() == 0
            # the writer is dead; further use fails explicitly, not silently
            with pytest.raises(GatewayError):
                await writer.write(b"z")

        _run(scenario())
    finally:
        _run(holder.close())


def test_declared_size_must_fit_single_artifact_quota(tmp_path: Path) -> None:
    holder = _create_store(tmp_path, max_bytes=100)
    try:
        async def scenario() -> None:
            with pytest.raises(GatewayError) as captured:
                await holder.store.create(
                    kind="project_export", sensitivity="CONFIDENTIAL", retention_class="EXPORT",
                    owner="none:test", filename="x.zip", media_type="application/zip",
                    correlation_id="c", declared_size=500,
                )
            assert captured.value.code == "limit_exceeded"

        _run(scenario())
    finally:
        _run(holder.close())


def test_total_quota_concurrent_creates_do_not_oversubscribe(tmp_path: Path) -> None:
    holder = _create_store(tmp_path, max_bytes=1_000_000, max_total_bytes=2_500_000)
    try:
        async def scenario() -> None:
            outcomes = await asyncio.gather(*[
                holder.store.create(
                    kind="project_export", sensitivity="CONFIDENTIAL", retention_class="EXPORT",
                    owner="none:test", filename=f"c{i}.zip", media_type="application/zip",
                    correlation_id="c",
                )
                for i in range(5)
            ], return_exceptions=True)
            # each writer reserves max_bytes (1 MB); total 2.5 MB => at most 2 reservations fit,
            # the rest fail closed — the quota is never oversubscribed.
            ok = [o for o in outcomes if not isinstance(o, BaseException)]
            refused = [o for o in outcomes if isinstance(o, GatewayError) and o.code == "limit_exceeded"]
            assert len(ok) == 2 and len(refused) == 3

        _run(scenario())
    finally:
        _run(holder.close())


def test_max_count_quota_fails_closed(tmp_path: Path) -> None:
    holder = _create_store(tmp_path, max_count=2)
    try:
        async def scenario() -> None:
            for index in range(2):
                writer = await holder.store.create(
                    kind="project_export", sensitivity="INTERNAL", retention_class="EPHEMERAL",
                    owner="none:test", filename=f"n{index}.zip", media_type="application/zip",
                    correlation_id="c",
                )
                await _write_all(writer, [b"payload"])
                await holder.store.publish(writer)
            with pytest.raises(GatewayError) as captured:
                await holder.store.create(
                    kind="project_export", sensitivity="INTERNAL", retention_class="EPHEMERAL",
                    owner="none:test", filename="n3.zip", media_type="application/zip",
                    correlation_id="c",
                )
            assert captured.value.code == "limit_exceeded"

        _run(scenario())
    finally:
        _run(holder.close())


# --------------------------------------------------------------------------- crash recovery


def _set_failure_hook(store: LocalArtifactStore, point: str) -> None:
    def hook(current: str) -> None:
        if current == point:
            raise _Crash(point)

    store._fail_hook = hook


class _Crash(RuntimeError):
    pass


@pytest.mark.parametrize("point", ["staged", "sealed", "validated", "publishing", "replaced"])
def test_crash_at_every_split_point_recovers(tmp_path: Path, point: str) -> None:
    holder = _create_store(tmp_path)
    try:
        async def scenario() -> None:
            _set_failure_hook(holder.store, point)
            with pytest.raises(_Crash):
                writer = await holder.store.create(
                    kind="project_export", sensitivity="CONFIDENTIAL", retention_class="EXPORT",
                    owner="none:test", filename="crash.zip", media_type="application/zip",
                    correlation_id="c",
                )
                await _write_all(writer, [ZIP_BYTES])
                await holder.store.publish(writer)
            _set_failure_hook(holder.store, "__never__")

        _run(scenario())

        # "staged"/"sealed"/"validated" crash before the row leaves STAGING: reconcile
        # must expire the staging row (deadline forced) and remove bytes.
        # "publishing" crashed after PUBLISHING without the object -> discard.
        # "replaced" crashed after os.replace but before READY -> bounded rehash -> READY.
        stats = None

        async def crash_like_restart() -> Any:
            # force the staging deadline into the past exactly like a restart would wait for
            await holder.store._db.run(
                lambda conn: conn.execute(
                    "UPDATE artifacts SET staging_deadline_at = '2000-01-01T00:00:00.000000Z'"
                    " WHERE state = 'STAGING'"
                )
            )
            return await holder.store.reconcile(batch=50, deadline_seconds=30)

        stats = _run(crash_like_restart())

        async def verify() -> None:
            rows = await holder.store._db.run(
                lambda conn: conn.execute("SELECT state FROM artifacts").fetchall()
            )
            states = [row[0] for row in rows]
            if point == "replaced":
                assert states == ["READY"]
                assert stats["recovered_ready"] == 1
            else:
                assert states == []  # row removed
            staging_files = list((tmp_path / "artifacts" / "staging").iterdir())
            assert staging_files == []
            objects = list((tmp_path / "artifacts" / "objects").rglob("*"))
            present = [entry for entry in objects if entry.is_file()]
            assert len(present) == (1 if point == "replaced" else 0)

        _run(verify())
    finally:
        _run(holder.close())


def test_publishing_object_with_wrong_hash_is_discarded(tmp_path: Path) -> None:
    holder = _create_store(tmp_path)
    try:
        async def scenario() -> None:
            writer = await holder.store.create(
                kind="project_export", sensitivity="CONFIDENTIAL", retention_class="EXPORT",
                owner="none:test", filename="tamper.zip", media_type="application/zip",
                correlation_id="c",
            )
            await _write_all(writer, [ZIP_BYTES])
            _set_failure_hook(holder.store, "publishing")
            with pytest.raises(_Crash):
                await holder.store.publish(writer)
            _set_failure_hook(holder.store, "__never__")
            # simulate corrupt/foreign object at the target path
            row = await holder.store._db.run(
                lambda conn: conn.execute(
                    "SELECT artifact_id, size_bytes, sha256 FROM artifacts WHERE state = 'PUBLISHING'"
                ).fetchall()
            )
            artifact_id, _, _ = row[0]
            object_path = holder.store._object_path(artifact_id)
            object_path.parent.mkdir(parents=True, exist_ok=True)
            object_path.write_bytes(b"corrupted")
            stats = await holder.store.reconcile(batch=10, deadline_seconds=30)
            assert stats["discarded"] == 1
            assert not object_path.exists()
            assert await holder.store._db.run(
                lambda conn: conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0]
            ) == 0

        _run(scenario())
    finally:
        _run(holder.close())


def test_tamper_between_seal_and_replace_is_refused(tmp_path: Path) -> None:
    holder = _create_store(tmp_path)
    try:
        async def scenario() -> None:
            writer = await holder.store.create(
                kind="project_export", sensitivity="CONFIDENTIAL", retention_class="EXPORT",
                owner="none:test", filename="race.zip", media_type="application/zip",
                correlation_id="c",
            )
            await _write_all(writer, [ZIP_BYTES])
            sha_before, size_before = await writer._seal()
            del sha_before, size_before
            staging = holder.store._staging_path(writer.artifact_id)
            staging.write_bytes(b"tampered after seal")
            with pytest.raises(GatewayError, match="staged bytes changed"):
                await holder.store.publish(writer)

        _run(scenario())
    finally:
        _run(holder.close())


def test_orphan_files_and_lost_rows_reconciled(tmp_path: Path) -> None:
    holder = _create_store(tmp_path)
    try:
        async def scenario() -> None:
            writer = await holder.store.create(
                kind="project_export", sensitivity="CONFIDENTIAL", retention_class="EXPORT",
                owner="none:test", filename="ok.zip", media_type="application/zip", correlation_id="c",
            )
            await _write_all(writer, [ZIP_BYTES])
            artifact = await holder.store.publish(writer)
            # orphan object + orphan staging file
            orphan = holder.store._object_path("ffffffff-ffff-7fff-bfff-ffffffffffff")
            orphan.parent.mkdir(parents=True, exist_ok=True)
            orphan.write_bytes(b"ghost")
            (tmp_path / "artifacts" / "staging" / "ghost.part").write_bytes(b"ghost")
            # READY row whose object vanished -> LOST, never listed
            holder.store._object_path(artifact.artifact_id).unlink()
            stats = await holder.store.reconcile(batch=100, deadline_seconds=30)
            assert stats["lost"] == 1
            assert stats["orphan_files"] == 2
            listing, total = await holder.store.list(
                principal="none:test", allow_admin=False, kind=None, limit=10, offset=0,
            )
            assert listing == [] and total == 0
            with pytest.raises(GatewayError) as captured:
                await holder.store.stat(artifact.artifact_id)
            assert captured.value.code == "not_found"

        _run(scenario())
    finally:
        _run(holder.close())


def test_reconcile_is_bounded_by_batch_and_deadline(tmp_path: Path) -> None:
    holder = _create_store(tmp_path)
    try:
        async def scenario() -> None:
            for index in range(6):
                writer = await holder.store.create(
                    kind="project_export", sensitivity="INTERNAL", retention_class="EPHEMERAL",
                    owner="none:test", filename=f"s{index}.zip", media_type="application/zip",
                    correlation_id="c",
                )
                del writer  # abandon in STAGING
            await holder.store._db.run(
                lambda conn: conn.execute(
                    "UPDATE artifacts SET staging_deadline_at = '2000-01-01T00:00:00.000000Z'"
                )
            )
            stats = await holder.store.reconcile(batch=2, deadline_seconds=30)
            assert stats["expired_staging"] == 2  # exactly one bounded batch
            remaining = await holder.store._db.run(
                lambda conn: conn.execute("SELECT COUNT(*) FROM artifacts WHERE state = 'STAGING'").fetchone()
            )
            assert remaining[0] == 4
            stats2 = await holder.store.reconcile(batch=2, deadline_seconds=0.000001)
            assert stats2["expired_staging"] <= 2  # deadline honored without unbounded work

        _run(scenario())
    finally:
        _run(holder.close())


# --------------------------------------------------------------------------- reads, cleanup, retention


def test_open_read_streams_and_gone_after_state_change(tmp_path: Path) -> None:
    holder = _create_store(tmp_path)
    try:
        async def scenario() -> None:
            writer = await holder.store.create(
                kind="project_export", sensitivity="CONFIDENTIAL", retention_class="EXPORT",
                owner="none:test", filename="stream.zip", media_type="application/zip", correlation_id="c",
            )
            await _write_all(writer, [ZIP_BYTES])
            artifact = await holder.store.publish(writer)

            reader = await holder.store.open_read(artifact.artifact_id)
            # delete races the open fd: POSIX keeps the fd readable (cleanup-safe)
            await holder.store.delete_internal(artifact.artifact_id)
            chunks = []
            while True:
                chunk = await reader.read_chunk()
                if chunk is None:
                    break
                chunks.append(chunk)
            assert b"".join(chunks) == ZIP_BYTES
            with pytest.raises(GatewayError) as captured:
                await holder.store.open_read(artifact.artifact_id)
            assert captured.value.code == "not_found"

        _run(scenario())
    finally:
        _run(holder.close())


def test_cleanup_expired_only_touches_expired_unlocked(tmp_path: Path) -> None:
    holder = _create_store(tmp_path)
    try:
        async def scenario() -> None:
            writer = await holder.store.create(
                kind="project_export", sensitivity="CONFIDENTIAL", retention_class="EXPORT",
                owner="none:test", filename="ttl.zip", media_type="application/zip", correlation_id="c",
            )
            await _write_all(writer, [ZIP_BYTES])
            expiring = await holder.store.publish(writer)
            await holder.store._db.run(
                lambda conn: conn.execute(
                    "UPDATE artifacts SET expires_at = '2000-01-01T00:00:00.000000Z' WHERE artifact_id = ?",
                    (expiring.artifact_id,),
                )
            )
            writer2 = await holder.store.create(
                kind="project_export", sensitivity="CONFIDENTIAL", retention_class="EXPORT",
                owner="none:test", filename="fresh.zip", media_type="application/zip", correlation_id="c",
            )
            await _write_all(writer2, [ZIP_BYTES])
            fresh = await holder.store.publish(writer2)

            removed = await holder.store.cleanup_expired()
            assert removed == 1
            assert await holder.store.exists(expiring.artifact_id) is False
            assert await holder.store.exists(fresh.artifact_id) is True

        _run(scenario())
    finally:
        _run(holder.close())


def test_retention_lock_refuses_delete_and_cleanup(tmp_path: Path) -> None:
    holder = _create_store(tmp_path)
    try:
        async def scenario() -> None:
            writer = await holder.store.create(
                kind="project_export", sensitivity="CONFIDENTIAL", retention_class="EPHEMERAL",
                owner="none:test", filename="baseline.zip", media_type="application/zip",
                correlation_id="c", transaction_id="",
            )
            await _write_all(writer, [ZIP_BYTES])
            baseline = await holder.store.publish(writer)
            promoted = await holder.store.promote_recovery(baseline.artifact_id, "txn-1")
            assert promoted.retention_class == "RECOVERY"
            assert promoted.retention_lock is True
            assert promoted.expires_at is None  # clock waits for terminal state
            with pytest.raises(GatewayError) as captured:
                await holder.store.delete_internal(baseline.artifact_id)
            assert captured.value.code == "conflict"
            assert await holder.store.cleanup_expired() == 0

            await holder.store.release_retention(baseline.artifact_id)
            released = await holder.store.stat(baseline.artifact_id)
            assert released.retention_lock is False
            assert released.expires_at is not None
            assert await holder.store.cleanup_expired() == 0  # TTL not reached yet
            await holder.store._db.run(
                lambda conn: conn.execute(
                    "UPDATE artifacts SET expires_at = '2000-01-01T00:00:00.000000Z' WHERE artifact_id = ?",
                    (baseline.artifact_id,),
                )
            )
            assert await holder.store.cleanup_expired() == 1

        _run(scenario())
    finally:
        _run(holder.close())


def test_list_principal_scoped_and_pagination(tmp_path: Path) -> None:
    holder = _create_store(tmp_path)
    try:
        async def scenario() -> None:
            for owner in ("none:alice", "none:alice", "none:bob"):
                writer = await holder.store.create(
                    kind="project_export", sensitivity="CONFIDENTIAL", retention_class="EXPORT",
                    owner=owner, filename="p.zip", media_type="application/zip", correlation_id="c",
                )
                await _write_all(writer, [ZIP_BYTES])
                await holder.store.publish(writer)
            mine, total = await holder.store.list(
                principal="none:alice", allow_admin=False, kind=None, limit=1, offset=0,
            )
            assert total == 2 and len(mine) == 1
            zipped, kind_total = await holder.store.list(
                principal="none:alice", allow_admin=False, kind="tag_config_export", limit=10, offset=0,
            )
            assert zipped == [] and kind_total == 0
            admin_all, admin_total = await holder.store.list(
                principal="none:admin", allow_admin=True, kind=None, limit=10, offset=0,
            )
            assert admin_total == 3 and len(admin_all) == 3
            with pytest.raises(GatewayError):
                await holder.store.list(principal="x", allow_admin=False, kind=None, limit=501, offset=0)
            with pytest.raises(GatewayError):
                await holder.store.list(principal="x", allow_admin=False, kind=None, limit=10, offset=2_000_000)

        _run(scenario())
    finally:
        _run(holder.close())


def test_storage_unavailable_fails_artifact_calls_loud(tmp_path: Path) -> None:
    holder = _create_store(tmp_path)
    try:
        async def scenario() -> None:
            holder.db._healthy = False
            with pytest.raises(StorageUnavailable):
                await holder.store.create(
                    kind="project_export", sensitivity="CONFIDENTIAL", retention_class="EXPORT",
                    owner="none:test", filename="x.zip", media_type="application/zip", correlation_id="c",
                )

        _run(scenario())
    finally:
        _run(holder.close())


# --------------------------------------------------------------------------- cancellation


@pytest.mark.parametrize("cancel_at", ["before-first-write", "mid-stream", "before-publish"])
def test_cancellation_never_publishes_and_removes_staging(tmp_path: Path, cancel_at: str) -> None:
    holder = _create_store(tmp_path)
    try:
        async def scenario() -> None:
            writer = await holder.store.create(
                kind="project_export", sensitivity="CONFIDENTIAL", retention_class="EXPORT",
                owner="none:test", filename="cancel.zip", media_type="application/zip", correlation_id="c",
            )

            async def work() -> None:
                if cancel_at == "before-first-write":
                    raise asyncio.CancelledError
                await _write_all(writer, [ZIP_BYTES[:8]])
                if cancel_at == "mid-stream":
                    raise asyncio.CancelledError
                await _write_all(writer, [ZIP_BYTES[8:]])
                if cancel_at == "before-publish":
                    raise asyncio.CancelledError
                await holder.store.publish(writer)

            task = asyncio.create_task(work())
            with pytest.raises(asyncio.CancelledError):
                await task
            await writer.abort()  # caller cleanup contract (lifecycle/executor does this)
            assert list((tmp_path / "artifacts" / "staging").iterdir()) == []
            assert await holder.store.count_ready() == 0

        _run(scenario())
    finally:
        _run(holder.close())
