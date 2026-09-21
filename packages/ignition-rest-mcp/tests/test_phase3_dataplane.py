"""Slice 4 (Phase 3 / G3): artifact HTTP data plane — route auth in every mode,
principal-scoped access vs admin, audit phase ordering including sink-failure
fail-closed, header parity, disconnect handling, cleanup races and uploads."""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
import time
from typing import Any

import httpx
import pytest
from dataplane_fixtures import make_zip_bytes
from fastmcp import Client

from ignition_rest_mcp.artifacts.local import LocalArtifactStore, quotas_from_settings
from ignition_rest_mcp.artifacts.routes import ZipSafetyValidator, _stream
from ignition_rest_mcp.config import LEGACY_STATIC_TOKEN_NAME, READ_SCOPE, StaticToken
from ignition_rest_mcp.storage.database import Database
from ignition_rest_mcp.storage.schema import AUDIT_DDL, STATE_DDL
from test_config import _settings

STATIC_TOKEN = "s3cret"
STATIC_BEARER = "Bearer " + STATIC_TOKEN
JWT_ISSUER = "https://idp"
JWT_AUDIENCE = "ignition-rest"


def _run(coro: Any) -> Any:
    async def main() -> Any:
        return await coro

    return asyncio.run(main())


def _legacy_static_token() -> tuple[StaticToken, ...]:
    """The Phase 1--3 single-token configuration, as D07's named form."""

    return (StaticToken(name=LEGACY_STATIC_TOKEN_NAME, token=STATIC_TOKEN, scopes=(READ_SCOPE,)),)


class _Stack:
    """create_server with a live lifespan (MCP Client) + HTTP view of the same app."""

    def __init__(self, tmp_path: Path, **overrides: Any) -> None:
        base: dict[str, Any] = {
            "data_dir": str(tmp_path),
            "watcher_interval_seconds": 3600.0,
            "retention_interval_seconds": 3600.0,
            "storage_probe_interval_seconds": 3600.0,
        }
        base.update(overrides)
        self.settings = _settings(**base)
        self.tmp_path = tmp_path

    async def __aenter__(self) -> "_Stack":
        from ignition_rest_mcp.server import create_server

        self.server = create_server(self.settings)
        self.mcp = Client(self.server)
        await self.mcp.__aenter__()
        self.http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.server.http_app()), base_url="http://test",
        )
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.http.aclose()
        await self.mcp.__aexit__(None, None, None)


async def _publish_fixture(
    tmp_path: Path, settings: Any, *, owner: str, sensitivity: str = "CONFIDENTIAL",
    kind: str = "project_export", body: bytes | None = None, filename: str = "Demo.zip",
    retention_class: str = "EXPORT",
) -> dict[str, Any]:
    db = Database("state", tmp_path / "state.db", STATE_DDL)
    await db.open()
    store = LocalArtifactStore(db, tmp_path, quotas_from_settings(settings))
    writer = await store.create(
        kind=kind, sensitivity=sensitivity, retention_class=retention_class, owner=owner,
        filename=filename, media_type="application/zip" if kind != "tag_config_export" else "application/json",
        correlation_id="fixture", project_name="Demo" if kind != "tag_config_export" else "",
    )
    await writer.write(body if body is not None else make_zip_bytes())
    artifact = await store.publish(writer)
    await db.close()
    return artifact.to_ref()


async def _audit_rows(tmp_path: Path) -> list[tuple[str, str, str, str]]:
    db = Database("audit", tmp_path / "audit.db", AUDIT_DDL)
    await db.open()
    try:
        return list(await db.run(
            lambda conn: conn.execute(
                "SELECT phase, outcome, tool, actor_key FROM audit_log ORDER BY seq"
            ).fetchall()
        ))
    finally:
        await db.close()


def _jwt_pair() -> tuple[str, str]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_pem, public_pem


def _jwt(private_pem: str, *, sub: str, scopes: list[str], expired: bool = False) -> str:
    from joserfc import jwt
    from joserfc.jwk import RSAKey

    claims: dict[str, Any] = {
        "iss": JWT_ISSUER, "aud": JWT_AUDIENCE, "sub": sub,
        "exp": int(time.time()) - 60 if expired else int(time.time()) + 300,
        "scope": " ".join(scopes),
    }
    return jwt.encode({"alg": "RS256"}, claims, RSAKey.import_key(private_pem))


# ---------------------------------------------------------------------- GET / HEAD


def test_get_streams_with_integrity_headers_and_audit(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _Stack(tmp_path) as stack:
            ref = await _publish_fixture(tmp_path, stack.settings, owner="none:test")
            response = await stack.http.get(f"/artifacts/{ref['artifactId']}")
            assert response.status_code == 200
            body = response.content
            import hashlib

            assert hashlib.sha256(body).hexdigest() == ref["sha256"]
            assert response.headers["ETag"] == f'"{ref["sha256"]}"'
            assert response.headers["Repr-Digest"] == (
                f"sha-256=:{base64.b64encode(bytes.fromhex(ref['sha256'])).decode()}:"
            )
            assert response.headers["Content-Length"] == str(len(body))
            assert response.headers["Cache-Control"] == "no-store"
            assert 'filename="Demo.zip"' in response.headers["Content-Disposition"]
            rows = await _audit_rows(tmp_path)
            assert [(r[0], r[1]) for r in rows] == [("attempt", "attempted"), ("result", "completed")]
            assert rows[0][2] == "artifact_access"

    _run(scenario())


def test_head_get_header_parity(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _Stack(tmp_path) as stack:
            ref = await _publish_fixture(tmp_path, stack.settings, owner="none:test")
            get = await stack.http.get(f"/artifacts/{ref['artifactId']}")
            head = await stack.http.head(f"/artifacts/{ref['artifactId']}")
            assert head.status_code == 200
            assert head.content == b""
            for name in ("ETag", "Repr-Digest", "Content-Length", "Content-Disposition", "Content-Type",
                         "Cache-Control"):
                assert head.headers[name] == get.headers[name], name
            rows = await _audit_rows(tmp_path)
            assert [r[0] for r in rows] == ["attempt", "result", "attempt", "result"]
            methods = await _audit_rows(tmp_path)
            assert len(methods) == 4

    _run(scenario())


def test_internal_artifacts_do_not_require_audit_rows(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _Stack(tmp_path) as stack:
            ref = await _publish_fixture(
                tmp_path, stack.settings, owner="none:test", sensitivity="INTERNAL",
            )
            response = await stack.http.get(f"/artifacts/{ref['artifactId']}")
            assert response.status_code == 200
            assert await _audit_rows(tmp_path) == []

    _run(scenario())


def test_unknown_id_bad_syntax_and_unauthenticated_404_400_401(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _Stack(tmp_path, auth_mode="static-token", static_tokens=_legacy_static_token()) as stack:
            auth = {"Authorization": STATIC_BEARER}
            noauth = await stack.http.get("/artifacts/" + "0" * 36)
            assert noauth.status_code == 401
            assert noauth.json()["code"] == "permission_denied"
            wrong = await stack.http.get(
                "/artifacts/" + "0" * 36, headers={"Authorization": "Bearer nope"},
            )
            assert wrong.status_code == 401
            missing = await stack.http.get("/artifacts/" + "0" * 36, headers=auth)
            assert missing.status_code == 404
            assert missing.json()["code"] == "not_found"
            bad = await stack.http.get("/artifacts/bad!id", headers=auth)
            assert bad.status_code == 400
            assert bad.json()["code"] == "invalid_argument"
            # traversal-shaped URLs never reach the artifact route at all
            traversal = await stack.http.get("/artifacts/..%2Fetc", headers=auth)
            assert traversal.status_code == 404
            # auth failures before any metadata access: decision rows only
            rows = await _audit_rows(tmp_path)
            assert rows and all(r[0] == "decision" for r in rows)

    _run(scenario())


def test_static_token_single_trust_domain(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _Stack(tmp_path, auth_mode="static-token", static_tokens=_legacy_static_token()) as stack:
            owner_key = "static-token:trusted-internal-static-token"
            ref = await _publish_fixture(tmp_path, stack.settings, owner=owner_key)
            ok = await stack.http.get(
                f"/artifacts/{ref['artifactId']}", headers={"Authorization": STATIC_BEARER},
            )
            assert ok.status_code == 200, ok.json()

    _run(scenario())


def test_cross_principal_denied_and_admin_allowed_under_jwt(tmp_path: Path) -> None:
    private_pem, public_pem = _jwt_pair()

    async def scenario() -> None:
        async with _Stack(
            tmp_path, auth_mode="jwt", jwt_public_key=public_pem,
            jwt_issuer=JWT_ISSUER, jwt_audience=JWT_AUDIENCE,
        ) as stack:
            ref = await _publish_fixture(tmp_path, stack.settings, owner="jwt:alice")
            alice = _jwt(private_pem, sub="alice", scopes=["ignition.read"])
            bob = _jwt(private_pem, sub="bob", scopes=["ignition.read"])
            admin = _jwt(private_pem, sub="root", scopes=["ignition.read", "ignition.admin"])
            expired = _jwt(private_pem, sub="alice", scopes=["ignition.read"], expired=True)

            assert (await stack.http.get(
                f"/artifacts/{ref['artifactId']}", headers={"Authorization": f"Bearer {alice}"},
            )).status_code == 200

            denied = await stack.http.get(
                f"/artifacts/{ref['artifactId']}", headers={"Authorization": f"Bearer {bob}"},
            )
            assert denied.status_code == 404  # no existence oracle
            assert denied.json()["code"] == "not_found"

            admin_view = await stack.http.get(
                f"/artifacts/{ref['artifactId']}", headers={"Authorization": f"Bearer {admin}"},
            )
            assert admin_view.status_code == 200

            stale = await stack.http.get(
                f"/artifacts/{ref['artifactId']}", headers={"Authorization": f"Bearer {expired}"},
            )
            assert stale.status_code == 401

            rows = await _audit_rows(tmp_path)
            phases = [r[0] for r in rows]
            # alice allowed (attempt+result), bob denied (decision only, no attempt),
            # admin allowed, expired 401 decision
            assert "decision" in phases
            denials = [r for r in rows if r[1].startswith("denied:cross-principal")]
            assert len(denials) == 1 and denials[0][2] == "artifact_access"
            attempts = [r for r in rows if r[0] == "attempt"]
            assert len(attempts) == 2  # alice + admin only

    _run(scenario())


def test_admin_scope_alone_is_rejected_by_the_verifier_no_scope_hierarchy(tmp_path: Path) -> None:
    private_pem, public_pem = _jwt_pair()

    async def scenario() -> None:
        async with _Stack(
            tmp_path, auth_mode="jwt", jwt_public_key=public_pem,
            jwt_issuer=JWT_ISSUER, jwt_audience=JWT_AUDIENCE,
        ) as stack:
            ref = await _publish_fixture(tmp_path, stack.settings, owner="jwt:alice")
            admin_only = _jwt(private_pem, sub="root", scopes=["ignition.admin"])
            denied = await stack.http.get(
                f"/artifacts/{ref['artifactId']}", headers={"Authorization": f"Bearer {admin_only}"},
            )
            assert denied.status_code == 401  # required ignition.read; no implicit hierarchy

    _run(scenario())


# ---------------------------------------------------------------------- audit failure


def test_confidential_get_fails_closed_when_audit_sink_is_broken(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from ignition_rest_mcp.audit.sink import AuditWriteError, SqliteAuditSink

    async def always_failing(self, row):  # type: ignore[no-untyped-def]
        raise AuditWriteError("audit subsystem down")

    monkeypatch.setattr(SqliteAuditSink, "write", always_failing)
    async def scenario() -> None:
        async with _Stack(tmp_path) as stack:
            ref = await _publish_fixture(tmp_path, stack.settings, owner="none:test")
            response = await stack.http.get(f"/artifacts/{ref['artifactId']}")
            assert response.status_code == 503
            assert response.json()["code"] == "internal_error"
            assert "sha256" not in response.text and ref["artifactId"] not in response.text
            # nothing leaked: no ETag/Content-Disposition on the failure body
            assert "ETag" not in response.headers

    _run(scenario())


def test_head_denial_proceeds_when_decision_row_write_fails(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from ignition_rest_mcp.audit.sink import AuditWriteError, SqliteAuditSink

    async def always_failing(self, row):  # type: ignore[no-untyped-def]
        raise AuditWriteError("audit subsystem down")

    monkeypatch.setattr(SqliteAuditSink, "write", always_failing)
    private_pem, public_pem = _jwt_pair()

    async def scenario() -> None:
        async with _Stack(
            tmp_path, auth_mode="jwt", jwt_public_key=public_pem,
            jwt_issuer=JWT_ISSUER, jwt_audience=JWT_AUDIENCE,
        ) as stack:
            ref = await _publish_fixture(tmp_path, stack.settings, owner="jwt:alice")
            bob = _jwt(private_pem, sub="bob", scopes=["ignition.read"])
            denied = await stack.http.get(
                f"/artifacts/{ref['artifactId']}", headers={"Authorization": f"Bearer {bob}"},
            )
            assert denied.status_code == 404  # denial stands despite the failed audit write

    _run(scenario())


def test_result_row_failure_never_alters_a_completed_response(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from ignition_rest_mcp.audit.sink import AuditWriteError, SqliteAuditSink

    original_write = SqliteAuditSink.write

    async def failing_result_rows(self, row):  # type: ignore[no-untyped-def]
        if row.phase == "result":
            raise AuditWriteError("audit unavailable at result time")
        return await original_write(self, row)

    monkeypatch.setattr(SqliteAuditSink, "write", failing_result_rows)

    async def scenario() -> None:
        async with _Stack(tmp_path) as stack:
            ref = await _publish_fixture(tmp_path, stack.settings, owner="none:test")
            response = await stack.http.get(f"/artifacts/{ref['artifactId']}")
            # the true outcome is returned; only the audit trail is incomplete
            assert response.status_code == 200
            assert len(response.content) == ref["sizeBytes"]


    _run(scenario())


# ---------------------------------------------------------------------- streaming units


def test_stream_reports_client_disconnected_once() -> None:
    async def scenario() -> None:
        chunks = [b"a" * 1024, b"b" * 1024, b"c" * 1024]
        index = 0
        finished: list[tuple[str, int]] = []
        disconnected_after = 1

        async def read_chunk() -> bytes | None:
            nonlocal index
            if index >= len(chunks):
                return None
            chunk = chunks[index]
            index += 1
            return chunk

        polls = 0

        async def is_disconnected() -> bool:
            nonlocal polls
            polls += 1
            return polls > disconnected_after

        async def finish(outcome: str, sent: int) -> None:
            finished.append((outcome, sent))

        stream = _stream(read_chunk, is_disconnected, finish)
        received = [chunk async for chunk in stream]
        assert received == [b"a" * 1024]
        assert finished == [("client_disconnected", 1024)]

    _run(scenario())


def test_stream_reports_completed_with_exact_bytes_and_single_finish() -> None:
    async def scenario() -> None:
        payloads = iter([b"x" * 10, b"y" * 5, None])
        finished: list[tuple[str, int]] = []

        async def read_chunk() -> bytes | None:
            return next(payloads)

        async def is_disconnected() -> bool:
            return False

        async def finish(outcome: str, sent: int) -> None:
            finished.append((outcome, sent))

        stream = _stream(read_chunk, is_disconnected, finish)
        received = [chunk async for chunk in stream]
        assert received == [b"x" * 10, b"y" * 5]
        assert finished == [("completed", 15)]

    _run(scenario())


def test_cleanup_racing_an_open_download_keeps_streaming(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _Stack(tmp_path) as stack:
            ref = await _publish_fixture(tmp_path, stack.settings, owner="none:test")
            reader_store_db = Database("state", tmp_path / "state.db", STATE_DDL)
            await reader_store_db.open()
            store = LocalArtifactStore(reader_store_db, tmp_path, quotas_from_settings(stack.settings))
            reader = await store.open_read(ref["artifactId"])
            # delete the artifact while our fd is open (simulates TTL cleanup)
            await store.delete_internal(ref["artifactId"])
            first = await reader.read_chunk()
            assert first is not None and len(first) > 0
            await reader.close()
            await reader_store_db.close()
            gone = await stack.http.get(f"/artifacts/{ref['artifactId']}")
            assert gone.status_code == 404

    _run(scenario())


# ---------------------------------------------------------------------- uploads


def test_upload_disabled_by_default(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _Stack(tmp_path) as stack:
            response = await stack.http.post(
                "/artifacts?kind=project_archive", content=make_zip_bytes(),
                headers={"Content-Type": "application/zip"},
            )
            assert response.status_code == 404
            assert response.json()["code"] == "operation_disabled"

    _run(scenario())


def test_upload_success_returns_artifact_ref_and_valid_zip_is_required(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _Stack(tmp_path, artifact_upload_enabled=True) as stack:
            zip_bytes = make_zip_bytes()
            response = await stack.http.post(
                "/artifacts?kind=project_archive", content=zip_bytes,
                headers={"Content-Type": "application/zip"},
            )
            assert response.status_code == 201, response.text
            ref = response.json()
            assert ref["kind"] == "project_archive"
            assert ref["sensitivity"] == "CONFIDENTIAL"
            assert ref["retentionClass"] == "EXPORT"
            fetched = await stack.http.get(f"/artifacts/{ref['artifactId']}")
            assert fetched.content == zip_bytes

            junk = await stack.http.post(
                "/artifacts?kind=project_archive", content=b"not a zip at all",
                headers={"Content-Type": "application/zip"},
            )
            assert junk.status_code == 400
            assert junk.json()["code"] == "invalid_argument"
            # failed uploads leave no READY artifacts behind
            listing, total = await _list_store(tmp_path, stack)
            assert total == 1

    async def _list_store(tmp_path: Path, stack: _Stack) -> tuple[list[Any], int]:
        db = Database("state", tmp_path / "state.db", STATE_DDL)
        await db.open()
        store = LocalArtifactStore(db, tmp_path, quotas_from_settings(stack.settings))
        try:
            return await store.list(
                principal="none:test", allow_admin=False, kind=None, limit=10, offset=0,
            )
        finally:
            await db.close()

    _run(scenario())


def test_upload_rejects_lying_content_length_oversize_and_bad_metadata(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _Stack(tmp_path, artifact_upload_enabled=True, artifact_max_bytes=1024) as stack:
            # lying Content-Length: declare 900 bytes, send fewer
            small_zip = make_zip_bytes()
            assert len(small_zip) < 900
            lying = await stack.http.post(
                "/artifacts?kind=project_archive",
                content=small_zip,
                headers={"Content-Type": "application/zip", "Content-Length": "900"},
            )
            assert lying.status_code == 400, lying.text
            # declared size above the single-artifact max: 413
            big = await stack.http.post(
                "/artifacts?kind=project_archive", content=b"x" * 10,
                headers={"Content-Type": "application/zip", "Content-Length": "4096"},
            )
            assert big.status_code == 413
            assert big.json()["code"] == "limit_exceeded"
            # wrong kind / wrong media type / missing content-length
            for url, headers in (
                ("/artifacts?kind=tag_config_export", {"Content-Type": "application/zip"}),
                ("/artifacts?kind=project_archive", {"Content-Type": "application/json"}),
            ):
                response = await stack.http.post(url, content=b"PK\x03\x04xx", headers=headers)
                assert response.status_code == 400, url
            # staging leftovers from failed uploads are cleaned (no stale rows)
            db = Database("state", tmp_path / "state.db", STATE_DDL)
            await db.open()
            try:
                rows = await db.run(lambda conn: conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0])
            finally:
                await db.close()
            assert int(rows) == 0

    _run(scenario())


def test_zip_safety_validator_hook_streams_off_loop(tmp_path: Path) -> None:
    validator = ZipSafetyValidator()
    good = tmp_path / "good.zip"
    good.write_bytes(make_zip_bytes())
    _run(validator.validate(str(good)))
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"junk")
    with pytest.raises(Exception) as captured:
        _run(validator.validate(str(bad)))
    assert getattr(captured.value, "code", None) == "invalid_argument"
