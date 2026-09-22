"""The pinned-interpreter fetch must survive a transient artifact-CDN failure.

Maven Central's CDN answered a transient `HTTP 404` for the pinned jython-standalone
2.7.4 JAR, which failed every recorded-Jython test in a live CI job before the
handler under test was ever executed. The fetch now retries a transport failure, a
truncated body and a digest mismatch with bounded backoff, while the pinned size and
digest still decide what is accepted, and refuses an oversized body without a retry.

These tests drive that policy with a small payload rather than the real 50 MB
artifact.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.error import HTTPError

import pytest

from tooling.native.jython_runner import runner

PAYLOAD = b"pinned-artifact-body"


class _Response:
    """The context-managed, chunk-reading response `urlopen` returns."""

    def __init__(self, body: bytes) -> None:
        self._body = body
        self._offset = 0

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False

    def read(self, size: int) -> bytes:
        chunk = self._body[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk


def _pin(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, size: int | None = None, digest: str | None = None) -> Path:
    """Pin the fetch to `PAYLOAD`, keep its retries fast, and cache under `tmp_path`."""
    monkeypatch.setattr(runner, "JYTHON_URL", "https://example.invalid/jython.jar")
    monkeypatch.setattr(runner, "JYTHON_SIZE", len(PAYLOAD) if size is None else size)
    monkeypatch.setattr(runner, "JYTHON_SHA256", hashlib.sha256(PAYLOAD).hexdigest() if digest is None else digest)
    monkeypatch.setattr(runner, "DOWNLOAD_ATTEMPTS", 3)
    monkeypatch.setattr(runner, "DOWNLOAD_BACKOFF_SECONDS", 0.0)
    monkeypatch.setenv("IGNITION_MCP_JYTHON_CACHE", str(tmp_path))
    return tmp_path / f"jython-standalone-{runner.JYTHON_VERSION}.jar"


def _counting_urlopen(monkeypatch: pytest.MonkeyPatch, failures: int = 0) -> list[str]:
    """A `urlopen` that fails `failures` times and then serves the payload."""
    attempts: list[str] = []

    def fake_urlopen(url: str, timeout: int) -> _Response:
        attempts.append(url)
        if len(attempts) <= failures:
            raise HTTPError(url, 404, "Not Found", {}, None)
        return _Response(PAYLOAD)

    monkeypatch.setattr(runner, "urlopen", fake_urlopen)
    return attempts


def test_a_transient_fetch_failure_is_retried(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    jar = _pin(monkeypatch, tmp_path)
    attempts = _counting_urlopen(monkeypatch, failures=1)

    assert runner._ensure_jython_jar() == jar
    assert len(attempts) == 2
    assert jar.read_bytes() == PAYLOAD


def test_a_body_whose_digest_is_not_pinned_is_retried_then_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    jar = _pin(monkeypatch, tmp_path, digest="0" * 64)
    attempts = _counting_urlopen(monkeypatch)

    with pytest.raises(runner.JythonRunnerError) as error:
        runner._ensure_jython_jar()

    assert len(attempts) == 3
    assert "3 attempts" in str(error.value)
    # A body the pin rejects is never left behind as the cached interpreter.
    assert not jar.exists()
    assert list(tmp_path.iterdir()) == []


def test_an_oversized_body_is_refused_without_a_retry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _pin(monkeypatch, tmp_path, size=4)
    attempts = _counting_urlopen(monkeypatch)

    with pytest.raises(runner.JythonRunnerError, match="exceeded 4 bytes"):
        runner._ensure_jython_jar()

    assert len(attempts) == 1
