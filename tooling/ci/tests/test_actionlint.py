"""The actionlint side of the pre-push check: pinned, verified, and parsed.

The recorded output fixture in ``fixtures/actionlint-runner-temp-output.txt`` is
verbatim stdout of the pinned binary (1.7.12) on
``fixtures/reconstructed-runner-temp-env.workflow.yml`` — the historical
breakage that failed run 35586649945 before GitHub ran a single job.
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest import mock
import urllib.error

from tooling.ci.actionlint import (
    ActionlintError,
    Asset,
    CHUNK_BYTES,
    DOWNLOAD_ATTEMPTS,
    OVERRIDE_ENV,
    VERSION,
    asset_for,
    ensure_binary,
    parse_diagnostics,
    resolve_binary,
    run_actionlint,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
RECORDED = FIXTURES / "actionlint-runner-temp-output.txt"
RECONSTRUCTED = "tooling/ci/tests/fixtures/reconstructed-runner-temp-env.workflow.yml"

#: The real constructor, kept out of reach of the digest double below.
_REAL_SHA256 = hashlib.sha256


def _tarball(payload: bytes = b"#!/bin/sh\necho stub actionlint\n") -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        info = tarfile.TarInfo("actionlint")
        info.size = len(payload)
        info.mode = 0o755
        archive.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


def _asset(payload: bytes) -> Asset:
    """A pinned-asset stand-in whose size pin matches ``payload``."""
    return Asset(
        name="actionlint_1.7.12_test.tar.gz",
        url="https://example.invalid/a.tar.gz",
        sha256=_REAL_SHA256(payload).hexdigest(),
        size=len(payload),
    )


def _refuse_fetch(url: str, limit: int) -> bytes:
    raise AssertionError(f"nothing must be fetched: {url} (limit {limit})")


class _Response:
    """A release response that only answers bounded reads, and counts them."""

    def __init__(self, payload: bytes) -> None:
        self._payload = io.BytesIO(payload)
        self.reads: list[int] = []

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            raise AssertionError("unbounded read requested")
        self.reads.append(size)
        return self._payload.read(size)

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exception: object) -> bool:
        return False


class _CountingDigest:
    """A sha256 double that records how many bytes reach the digest."""

    def __init__(self) -> None:
        self.seen = 0
        self._real = _REAL_SHA256()

    def update(self, chunk: bytes) -> None:
        self.seen += len(chunk)
        self._real.update(chunk)

    def hexdigest(self) -> str:
        return self._real.hexdigest()


class PinnedAssetTest(unittest.TestCase):
    def test_every_supported_platform_has_the_published_checksum(self) -> None:
        # Sha256 values transcribed from actionlint_1.7.12_checksums.txt.
        expected = {
            ("linux", "x86_64"): "8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8",
            ("linux", "aarch64"): "325e971b6ba9bfa504672e29be93c24981eeb1c07576d730e9f7c8805afff0c6",
            ("darwin", "x86_64"): "5b44c3bc2255115c9b69e30efc0fecdf498fdb63c5d58e17084fd5f16324c644",
            ("darwin", "arm64"): "aba9ced2dee8d27fecca3dc7feb1a7f9a52caefa1eb46f3271ea66b6e0e6953f",
        }
        for platform_key, sha256 in expected.items():
            asset = asset_for(*platform_key)
            self.assertEqual(asset.sha256, sha256, platform_key)
            self.assertIn(VERSION, asset.name)
            self.assertTrue(asset.url.startswith("https://github.com/rhysd/actionlint/releases/download/"))

    def test_every_supported_platform_pins_the_published_byte_size(self) -> None:
        # Byte sizes from the release's own asset list (v1.7.12), which the
        # download and the cache read are bounded by.
        expected = {
            ("linux", "x86_64"): 2353908,
            ("linux", "aarch64"): 2111482,
            ("darwin", "x86_64"): 2355828,
            ("darwin", "arm64"): 2164202,
        }
        for platform_key, size in expected.items():
            self.assertEqual(asset_for(*platform_key).size, size, platform_key)

    def test_platform_machine_names_are_normalised(self) -> None:
        self.assertEqual(asset_for("linux", "AMD64"), asset_for("linux", "x86_64"))
        self.assertEqual(asset_for("linux", "aarch64").name, f"actionlint_{VERSION}_linux_arm64.tar.gz")
        self.assertEqual(asset_for("darwin", "arm64").name, f"actionlint_{VERSION}_darwin_arm64.tar.gz")

    def test_unsupported_platform_fails_loudly(self) -> None:
        with self.assertRaises(ActionlintError) as caught:
            asset_for("windows", "AMD64")
        self.assertIn("windows", str(caught.exception))


class OutputParsingTest(unittest.TestCase):
    def test_recorded_runner_temp_diagnostic_is_parsed(self) -> None:
        diagnostics = parse_diagnostics(RECORDED.read_text(encoding="utf-8"))
        self.assertEqual(len(diagnostics), 1)
        diagnostic = diagnostics[0]
        self.assertEqual(diagnostic.path, RECONSTRUCTED)
        self.assertEqual((diagnostic.line, diagnostic.column), (10, 21))
        self.assertIn('context "runner" is not allowed here', diagnostic.message)
        self.assertTrue(diagnostic.message.endswith("[expression]"))

    def test_clean_run_has_no_diagnostics(self) -> None:
        self.assertEqual(parse_diagnostics(""), [])


class FetchTest(unittest.TestCase):
    def test_downloads_verifies_and_reuses_the_cached_binary(self) -> None:
        payload = _tarball()
        asset = _asset(payload)
        fetched: list[tuple[str, int]] = []

        def fetch(url: str, limit: int) -> bytes:
            fetched.append((url, limit))
            return payload

        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            binary = ensure_binary(asset, cache_dir=cache, fetch=fetch)
            self.assertEqual(binary.read_bytes(), b"#!/bin/sh\necho stub actionlint\n")
            self.assertTrue(binary.stat().st_mode & 0o111)

            self.assertEqual(ensure_binary(asset, cache_dir=cache, fetch=_refuse_fetch), binary)
            # The pinned size travels with the fetch, so the seam is bounded too.
            self.assertEqual(fetched, [(asset.url, asset.size)])

    def test_checksum_mismatch_fails_loudly(self) -> None:
        payload = _tarball()
        asset = Asset(
            name="actionlint_1.7.12_test.tar.gz",
            url="https://example.invalid/a.tar.gz",
            sha256="0" * 64,
            size=len(payload),
        )
        attempts: list[float] = []
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ActionlintError) as caught:
                ensure_binary(
                    asset, cache_dir=Path(temporary),
                    fetch=lambda url, limit: payload, sleep=attempts.append,
                )
        self.assertIn("0" * 64, str(caught.exception))
        # Bounded work: the digest fails on every attempt, and the retries are
        # spaced by the documented backoff rather than run in a tight loop.
        self.assertEqual(attempts, [1.0, 2.0, 4.0, 8.0])
        self.assertIn(f"in {DOWNLOAD_ATTEMPTS} attempt(s)", str(caught.exception))

    def test_a_transient_release_failure_is_retried_with_backoff(self) -> None:
        """The release CDN answered HTTP 500 in run 35669308034 and failed an
        otherwise clean push; the pinned download rides that out."""
        payload = _tarball()
        asset = _asset(payload)
        responses: list[object] = [
            urllib.error.HTTPError(asset.url, 500, "Internal Server Error", {}, None),
            urllib.error.HTTPError(asset.url, 503, "Service Unavailable", {}, None),
            _Response(payload),
        ]
        attempts: list[float] = []

        def urlopen(url: str, timeout: float | None = None) -> object:
            self.assertEqual(url, asset.url)
            response = responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch("tooling.ci.actionlint.urllib.request.urlopen", side_effect=urlopen):
                binary = ensure_binary(asset, cache_dir=Path(temporary), sleep=attempts.append)
            self.assertEqual(binary.read_bytes(), b"#!/bin/sh\necho stub actionlint\n")
        self.assertEqual(responses, [])
        self.assertEqual(attempts, [1.0, 2.0])

    def test_a_truncated_download_is_fetched_again(self) -> None:
        """A body that does not match the pin is retried, and the checksum still
        decides: a download that never matches aborts instead of being accepted."""
        payload = _tarball()
        asset = _asset(payload)
        served: list[int] = []

        def fetch(url: str, limit: int) -> bytes:
            served.append(limit)
            return payload[: len(payload) // 2] if len(served) == 1 else payload

        with tempfile.TemporaryDirectory() as temporary:
            binary = ensure_binary(asset, cache_dir=Path(temporary), fetch=fetch, sleep=lambda _: None)
            delivered = binary.read_bytes()
        self.assertEqual(len(served), 2)
        self.assertEqual(delivered, b"#!/bin/sh\necho stub actionlint\n")

    def test_an_over_pin_response_is_not_retried(self) -> None:
        """A body over the pin is a response the pin rejects, not a transient
        network condition, so the loop spends one attempt on it."""
        asset = asset_for("linux", "x86_64")
        response = _Response(b"\0" * (asset.size * 4))
        attempts: list[float] = []
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch("tooling.ci.actionlint.urllib.request.urlopen", return_value=response):
                with self.assertRaises(ActionlintError):
                    ensure_binary(asset, cache_dir=Path(temporary), sleep=attempts.append)
        self.assertEqual(attempts, [])

    def test_corrupted_cache_is_never_trusted(self) -> None:
        asset = asset_for("linux", "x86_64")
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            (cache / asset.name).write_bytes(b"not the pinned archive")
            with self.assertRaises(ActionlintError):
                ensure_binary(asset, cache_dir=cache, fetch=_refuse_fetch)

    def test_replaced_cached_executable_is_rebuilt_from_the_verified_archive(self) -> None:
        # A cached executable that no longer matches the verified archive (a
        # corrupt disk, a stale build, a hand-placed no-op) must not be the
        # binary that lints the workflows.
        payload = _tarball()
        asset = _asset(payload)
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            ensure_binary(asset, cache_dir=cache, fetch=lambda url, limit: payload)
            replaced = cache / "actionlint"
            replaced.write_bytes(b"#!/bin/sh\nexit 0\n")
            binary = ensure_binary(asset, cache_dir=cache, fetch=lambda url, limit: payload)
            self.assertEqual(binary.read_bytes(), b"#!/bin/sh\necho stub actionlint\n")
            self.assertTrue(binary.stat().st_mode & 0o111)


class BoundedAcquisitionTest(unittest.TestCase):
    """D10: the archive is read in bounded chunks, and overflow is rejected before
    it is retained — neither a hostile response nor a corrupted cache can make the
    process hold an unbounded body ahead of the sha256 check."""

    def test_oversize_download_is_rejected_before_it_is_retained(self) -> None:
        asset = asset_for("linux", "x86_64")
        response = _Response(b"\0" * (asset.size * 4))
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            with mock.patch("tooling.ci.actionlint.urllib.request.urlopen", return_value=response):
                with self.assertRaises(ActionlintError) as caught:
                    ensure_binary(asset, cache_dir=cache)
            self.assertIn(f"larger than the pinned {asset.size} bytes", str(caught.exception))
            self.assertFalse((cache / asset.name).exists())
        # The body was abandoned at the pin instead of being drained: four
        # archives' worth of response costs one archive's worth of reads.
        self.assertTrue(all(size > 0 for size in response.reads))
        self.assertLessEqual(len(response.reads), asset.size // CHUNK_BYTES + 2)

    def test_oversize_cached_archive_is_rejected_before_it_is_hashed(self) -> None:
        asset = asset_for("linux", "x86_64")
        digests: list[_CountingDigest] = []

        def sha256() -> _CountingDigest:
            digest = _CountingDigest()
            digests.append(digest)
            return digest

        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            (cache / asset.name).write_bytes(b"\0" * (asset.size * 4))
            with mock.patch("tooling.ci.actionlint.hashlib.sha256", sha256):
                with self.assertRaises(ActionlintError) as caught:
                    ensure_binary(asset, cache_dir=cache, fetch=_refuse_fetch)
            self.assertIn(f"larger than the pinned {asset.size} bytes", str(caught.exception))
            self.assertFalse((cache / "actionlint").exists())
        # The digest stops at the pin: a cached archive four times the pinned
        # size never reaches the hash in full.
        self.assertEqual(len(digests), 1)
        self.assertLessEqual(digests[0].seen, asset.size)


class OverrideBinaryTest(unittest.TestCase):
    """``--actionlint`` is a test seam, not a way to check nothing."""

    def _stub(self, directory: Path, version: str) -> Path:
        path = directory / "actionlint"
        path.write_text(f"#!/bin/sh\necho {version}\n", encoding="utf-8")
        path.chmod(0o755)
        return path

    def _resolve(self, override: Path) -> Path:
        with mock.patch.dict("os.environ", {OVERRIDE_ENV: "1"}):
            return resolve_binary(override)

    def test_override_needs_the_explicit_test_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            stub = self._stub(Path(temporary), VERSION)
            with mock.patch.dict("os.environ", {}, clear=True):
                with self.assertRaises(ActionlintError) as caught:
                    resolve_binary(stub)
        self.assertIn(OVERRIDE_ENV, str(caught.exception))

    def test_no_op_override_binary_is_rejected(self) -> None:
        with self.assertRaises(ActionlintError) as caught:
            self._resolve(Path("/bin/true"))
        self.assertIn("1.7.12", str(caught.exception))

    def test_failing_override_binary_is_rejected(self) -> None:
        with self.assertRaises(ActionlintError):
            self._resolve(Path("/bin/false"))

    def test_override_that_reports_another_version_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ActionlintError) as caught:
                self._resolve(self._stub(Path(temporary), "1.9.9"))
        self.assertIn("1.9.9", str(caught.exception))

    def test_override_that_reports_the_pinned_version_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            stub = self._stub(Path(temporary), VERSION)
            self.assertEqual(self._resolve(stub), stub)

    def test_absent_override_binary_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ActionlintError) as caught:
                self._resolve(Path(temporary) / "absent")
        self.assertIn("not a file", str(caught.exception))


class RunTest(unittest.TestCase):
    def _stub(self, cache: Path, body: str) -> Path:
        path = cache / "actionlint"
        path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        path.chmod(0o755)
        return path

    def test_diagnostics_are_returned_for_exit_one(self) -> None:
        recorded = RECORDED.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as temporary:
            stub = self._stub(Path(temporary), f"cat <<'RECORDED_OUTPUT'\n{recorded}RECORDED_OUTPUT\nexit 1")
            diagnostics = run_actionlint(stub, [Path(RECONSTRUCTED)])
        self.assertEqual(len(diagnostics), 1)
        self.assertEqual(diagnostics[0].line, 10)

    def test_external_integrations_are_disabled_on_every_invocation(self) -> None:
        # actionlint runs shellcheck and pyflakes itself when it finds them on
        # PATH, so the same command reported SC2046 in GitHub CI (run
        # 35626729044) and passed on a machine without shellcheck. Disabling
        # both makes the check a function of the repository, not the machine.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            recorded = root / "argv.txt"
            stub = self._stub(root, f'printf "%s\\n" "$@" > {recorded}')
            run_actionlint(stub, [Path(RECONSTRUCTED)])
            argv = recorded.read_text(encoding="utf-8").split()
        self.assertIn("-shellcheck=", argv)
        self.assertIn("-pyflakes=", argv)

    def test_actionlint_invocation_failure_is_loud(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            stub = self._stub(Path(temporary), "echo 'config error' >&2\nexit 3")
            with self.assertRaises(ActionlintError) as caught:
                run_actionlint(stub, [Path(RECONSTRUCTED)])
        self.assertIn("3", str(caught.exception))

    def test_exit_one_without_a_diagnostic_is_a_could_not_run_error(self) -> None:
        # An empty exit 1 is not "no findings": it is a binary that failed to
        # lint (a no-op stub, or a changed output format). Failing closed keeps
        # the check honest.
        with tempfile.TemporaryDirectory() as temporary:
            stub = self._stub(Path(temporary), "exit 1")
            with self.assertRaises(ActionlintError) as caught:
                run_actionlint(stub, [Path(RECONSTRUCTED)])
        self.assertIn("exited 1", str(caught.exception))

    def test_exit_one_with_unparseable_output_is_a_could_not_run_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            stub = self._stub(Path(temporary), "echo 'not a diagnostic line'\nexit 1")
            with self.assertRaises(ActionlintError) as caught:
                run_actionlint(stub, [Path(RECONSTRUCTED)])
        self.assertIn("not a diagnostic line", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
