"""Pinned, checksum-verified actionlint for the pre-push workflow check.

actionlint is not installed on this workstation, so the check fetches the exact
release named in :data:`ASSETS`, verifies its published sha256, and re-extracts
the binary from that verified archive on every use. A missing platform, a
mismatching checksum, a replaced cache entry or a failing invocation raises,
because a silently skipped lint would turn into a green check that proves
nothing.

Every archive read is bounded by the byte size pinned for that platform (D10): a
redirected or stalled upstream, or a corrupted local cache, cannot make the
process read an unbounded body before the sha256 check rejects it.

Each invocation disables actionlint's shellcheck and pyflakes integrations
(:data:`DISABLED_INTEGRATIONS`). actionlint runs them whenever it finds them on
``PATH``, so leaving them on makes the same command report different results on
different machines — the reason CI failed with ``SC2046`` (run 35626729044)
while the identical command passed locally. Shell syntax is already checked by
``bash -n`` in :mod:`tooling.ci.check_workflows`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
import hashlib
import http.client
import os
from pathlib import Path
import platform
import re
import subprocess
import tarfile
import time
from typing import BinaryIO
import urllib.error
import urllib.request

VERSION = "1.7.12"
BASE_URL = f"https://github.com/rhysd/actionlint/releases/download/v{VERSION}"

#: Bytes per archive read. Small enough that an oversized body is abandoned
#: after a bounded amount of work, large enough not to syscall per byte.
CHUNK_BYTES = 64 * 1024

#: How many times the pinned archive is fetched before the check gives up, and
#: the first backoff step (doubled per attempt: 1 s, 2 s, 4 s, 8 s). The release
#: CDN answered HTTP 500 in run 35669308034, which failed an otherwise clean push
#: before a single live job ran; a bounded retry rides that out while the sha256
#: still decides what is accepted.
DOWNLOAD_ATTEMPTS = 5
DOWNLOAD_BACKOFF_SECONDS = 1.0
DOWNLOAD_TIMEOUT_SECONDS = 60.0

#: Opt-in for the test-only ``--actionlint`` override. Production and CI never
#: set it, so the documented command cannot be pointed at another executable;
#: tests that need to script actionlint's output set it explicitly.
OVERRIDE_ENV = "IGNITION_MCP_ACTIONLINT_ALLOW_OVERRIDE"

#: actionlint runs shellcheck and pyflakes itself when it finds them on ``PATH``,
#: so the same command reported a shellcheck finding in GitHub CI (run
#: 35626729044, ``SC2046`` in ``phase3-live-g3.yml``) and passed on a machine
#: without shellcheck. Both integrations are disabled on every invocation, which
#: makes the check a function of the repository rather than of the machine. An
#: empty value is actionlint's documented "off"; shell syntax is covered by
#: ``bash -n`` in :mod:`tooling.ci.check_workflows`.
DISABLED_INTEGRATIONS = ("-shellcheck=", "-pyflakes=")

#: ``path:line:column: message``. The caret illustration actionlint prints after
#: each diagnostic does not match, so it is skipped.
DIAGNOSTIC = re.compile(r"^(?P<path>.+?):(?P<line>\d+):(?P<column>\d+): (?P<message>.*)$")
MACHINE_ALIASES = {"amd64": "x86_64", "x86_64": "x86_64", "aarch64": "aarch64", "arm64": "arm64"}


class ActionlintError(RuntimeError):
    """actionlint could not be fetched, verified, or run."""


class OversizeResponseError(ActionlintError):
    """A response (or a cached archive) was larger than the pin.

    Retrying cannot help — the pin rejects the body outright — so the fetch loop
    surfaces it immediately instead of spending its attempts on it.
    """


@dataclass(frozen=True)
class Asset:
    """One pinned release archive: its sha256, and the size that bounds every read."""

    name: str
    url: str
    sha256: str
    size: int


@dataclass(frozen=True)
class Diagnostic:
    """One actionlint finding, as actionlint itself reported it."""

    path: str
    line: int
    column: int
    message: str


def _asset(platform_name: str, sha256: str, size: int) -> Asset:
    name = f"actionlint_{VERSION}_{platform_name}.tar.gz"
    return Asset(name, f"{BASE_URL}/{name}", sha256, size)


#: Sha256 values from actionlint_1.7.12_checksums.txt in the pinned release, and
#: the byte sizes from that release's asset list.
ASSETS: dict[tuple[str, str], Asset] = {
    ("linux", "x86_64"): _asset("linux_amd64", "8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8", 2353908),
    ("linux", "aarch64"): _asset("linux_arm64", "325e971b6ba9bfa504672e29be93c24981eeb1c07576d730e9f7c8805afff0c6", 2111482),
    ("darwin", "x86_64"): _asset("darwin_amd64", "5b44c3bc2255115c9b69e30efc0fecdf498fdb63c5d58e17084fd5f16324c644", 2355828),
    ("darwin", "arm64"): _asset("darwin_arm64", "aba9ced2dee8d27fecca3dc7feb1a7f9a52caefa1eb46f3271ea66b6e0e6953f", 2164202),
}


def asset_for(system: str, machine: str) -> Asset:
    """The pinned archive for one platform, or a loud failure."""
    key = (system.lower(), MACHINE_ALIASES.get(machine.lower(), machine.lower()))
    asset = ASSETS.get(key)
    if asset is None:
        supported = ", ".join(f"{name}/{arch}" for name, arch in sorted(ASSETS))
        raise ActionlintError(f"no pinned actionlint {VERSION} for {system}/{machine}; supported: {supported}")
    return asset


def default_cache_dir() -> Path:
    """Where the verified binary is cached between runs."""
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "ignition-mcp-ci" / "actionlint" / VERSION


def _chunks(stream: BinaryIO, limit: int, *, source: str) -> Iterator[bytes]:
    """``stream`` in bounded chunks; the first byte past ``limit`` raises.

    The bound is on retention, not only on the final digest: an oversized body is
    abandoned where it crosses the pin instead of being read to the end and
    rejected afterwards. The already-read chunk is dropped, never yielded. The
    failure is an :class:`OversizeResponseError`, which the fetch loop does not
    retry: a body over the pin is not a transient network condition.
    """
    total = 0
    while True:
        chunk = stream.read(CHUNK_BYTES)
        if not chunk:
            return
        total += len(chunk)
        if total > limit:
            raise OversizeResponseError(
                f"{source} is larger than the pinned {limit} bytes of actionlint {VERSION}; "
                f"refusing to read past {total} bytes"
            )
        yield chunk


def _download(url: str, limit: int) -> bytes:
    """One bounded attempt at the pinned archive over HTTP.

    A transport failure is reported as an :class:`ActionlintError` so the fetch
    loop can retry it; an over-pin body is not, because retrying cannot change a
    response the pin rejects.
    """
    try:
        with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:  # noqa: S310 - pinned https release URL
            return b"".join(_chunks(response, limit, source=url))
    except (urllib.error.HTTPError, urllib.error.URLError, OSError, http.client.HTTPException) as error:
        raise ActionlintError(f"fetching {url} failed: {type(error).__name__}: {error}") from error


def _fetch_pinned(
    asset: Asset,
    *,
    fetch: Callable[[str, int], bytes],
    sleep: Callable[[float], None],
    attempts: int = DOWNLOAD_ATTEMPTS,
) -> bytes:
    """The pinned archive's bytes, retried while the failure can be transient.

    The release CDN is a single point of failure for the whole check: it answered
    HTTP 500 in run 35669308034 and failed an otherwise clean push before a single
    live job ran. A transport failure, and a body whose digest is not the pinned
    one (a truncated download), are retried with exponential backoff. The sha256
    still decides: bytes that never match abort after :data:`DOWNLOAD_ATTEMPTS`,
    and an over-pin body aborts at once.
    """
    last = "no attempt was made"
    for attempt in range(attempts):
        try:
            payload = fetch(asset.url, asset.size)
        except OversizeResponseError:
            raise
        except ActionlintError as error:
            last = str(error)
        else:
            digest = hashlib.sha256(payload).hexdigest()
            if digest == asset.sha256:
                return payload
            last = f"the download's sha256 is {digest}, not the pinned {asset.sha256}"
        if attempt + 1 < attempts:
            sleep(DOWNLOAD_BACKOFF_SECONDS * (2 ** attempt))
    raise ActionlintError(
        f"could not obtain the pinned actionlint {VERSION} archive in {attempts} attempt(s): {last}"
    )


def _sha256(path: Path, limit: int) -> str:
    """``path``'s sha256, hashed in bounded chunks that stop at ``limit``."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in _chunks(handle, limit, source=str(path)):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_binary(
    asset: Asset | None = None,
    *,
    cache_dir: Path | None = None,
    fetch: Callable[[str, int], bytes] = _download,
    sleep: Callable[[float], None] = time.sleep,
) -> Path:
    """Path to the pinned actionlint binary, downloaded, verified, and extracted.

    The archive's sha256 is re-checked on every call, including cache hits, and
    the executable is rebuilt from that verified archive every time: a replaced
    or corrupted cached binary never lints, it is overwritten. The download is
    retried with backoff when the failure can be transient and the cache read is
    capped by the asset's pinned size, so neither can be talked into an unbounded
    read before that check — and the sha256, not the retry, is what accepts the
    bytes.
    """
    if asset is None:
        asset = asset_for(platform.system(), platform.machine())
    directory = cache_dir or default_cache_dir()
    directory.mkdir(parents=True, exist_ok=True)
    tarball = directory / asset.name
    if not tarball.is_file():
        _write_atomically(tarball, _fetch_pinned(asset, fetch=fetch, sleep=sleep))
    digest = _sha256(tarball, asset.size)
    if digest != asset.sha256:
        raise ActionlintError(
            f"{tarball} does not match the pinned sha256 {asset.sha256} (got {digest}); delete it and re-run"
        )
    binary = directory / "actionlint"
    _write_atomically(binary, _extract_actionlint(tarball))
    binary.chmod(0o755)
    return binary


def resolve_binary(override: Path | None = None) -> Path:
    """The actionlint to run: the pinned binary, or a verified test override.

    The override exists so tests can script actionlint output. It requires
    :data:`OVERRIDE_ENV` and must identify itself as the pinned version, so a
    no-op or stale executable cannot make the workflow check pass.
    """
    if override is None:
        return ensure_binary()
    if os.environ.get(OVERRIDE_ENV) != "1":
        raise ActionlintError(
            f"--actionlint is a test-only override and is disabled; set {OVERRIDE_ENV}=1 to use "
            f"{override}, or omit it to run the pinned, checksum-verified actionlint {VERSION}"
        )
    if not override.is_file():
        raise ActionlintError(f"actionlint override is not a file: {override}")
    if not os.access(override, os.X_OK):
        raise ActionlintError(f"actionlint override is not executable: {override}")
    completed = subprocess.run([str(override), "--version"], capture_output=True, text=True, check=False)
    reported = (completed.stdout + completed.stderr).strip()
    if completed.returncode != 0 or VERSION not in reported:
        raise ActionlintError(
            f"actionlint override {override} does not identify as actionlint {VERSION}: "
            f"exited {completed.returncode} with {reported[:200]!r}"
        )
    return override


def _write_atomically(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def _extract_actionlint(tarball: Path) -> bytes:
    with tarfile.open(tarball) as archive:
        member = archive.extractfile("actionlint")
        if member is None:
            raise ActionlintError(f"{tarball} does not contain the actionlint binary")
        return member.read()


def run_actionlint(binary: Path, files: Sequence[Path], *, cwd: Path | None = None) -> list[Diagnostic]:
    """Run actionlint over ``files``; exit 0 (clean) and 1 (findings) are results.

    Exit 1 with nothing parseable on stdout is not "no findings": it is a binary
    that failed to lint, or an output format this parser no longer understands.
    It raises so the check fails closed instead of reporting a clean run.

    Every invocation carries :data:`DISABLED_INTEGRATIONS`, so actionlint never
    shells out to a shellcheck or pyflakes that happens to be installed.
    """
    result = subprocess.run(
        [str(binary), *DISABLED_INTEGRATIONS, "-color=false", *[str(path) for path in files]],
        capture_output=True,
        text=True,
        check=False,
        cwd=cwd,
    )
    if result.returncode not in (0, 1):
        detail = " ".join((result.stderr or result.stdout).split())[:400]
        raise ActionlintError(f"actionlint exited {result.returncode}: {detail}")
    diagnostics = parse_diagnostics(result.stdout)
    if result.returncode == 1 and not diagnostics:
        detail = " ".join((result.stderr or result.stdout).split())[:400]
        raise ActionlintError(f"actionlint exited 1 without a parseable diagnostic: {detail}")
    return diagnostics


def parse_diagnostics(output: str) -> list[Diagnostic]:
    """One :class:`Diagnostic` per reported line, ignoring illustration lines."""
    diagnostics: list[Diagnostic] = []
    for line in output.splitlines():
        match = DIAGNOSTIC.match(line)
        if match is None:
            continue
        diagnostics.append(
            Diagnostic(
                match.group("path"),
                int(match.group("line")),
                int(match.group("column")),
                match.group("message"),
            )
        )
    return diagnostics
