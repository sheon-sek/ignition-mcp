"""Process boundary for recorded Runtime Tool execution under Jython 2.7."""

from __future__ import annotations

import functools
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from typing import Any
from urllib.request import urlopen

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[3]
JYTHON_VERSION = "2.7.4"
JYTHON_URL = (
    "https://repo.maven.apache.org/maven2/org/python/jython-standalone/2.7.4/"
    "jython-standalone-2.7.4.jar"
)
JYTHON_SHA256 = "1fba1769effcc8b19f5e10436bc8274a158ce988559f257927c24c73bb137f3c"
JYTHON_SIZE = 50_453_449
DEFAULT_CACHE = Path(__file__).resolve().parent / "build/cache"
#: D29 allows the Jython 2.7.4 process to read only repository-controlled
#: fixtures, so a fixture path must resolve inside this committed directory.
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
#: D10's default structured-output ceiling, applied to the recorded native result
#: that crosses into the Jython process.
MAX_FIXTURE_BYTES = 256 * 1024
DOWNLOAD_CHUNK_SIZE = 1024 * 1024
#: How many times one pinned artifact fetch is attempted. Maven Central's CDN has
#: answered a transient `HTTP 404` for the pinned 50 MB JAR, which without a retry
#: fails every recorded-Jython test in the job; the digest below still decides what
#: is accepted, so a retry can never substitute another artifact.
DOWNLOAD_ATTEMPTS = 5
DOWNLOAD_BACKOFF_SECONDS = 1.0


class JythonRunnerError(RuntimeError):
    """The interpreter, recorded call, handler, or output contract failed."""


class _OversizeArtifact(JythonRunnerError):
    """A body larger than the pinned size: refetching it cannot help."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(DOWNLOAD_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_jar(path: Path) -> None:
    if path.stat().st_size != JYTHON_SIZE:
        raise JythonRunnerError(
            f"Jython {JYTHON_VERSION} size mismatch: {path.stat().st_size} != {JYTHON_SIZE}"
        )
    actual = _sha256(path)
    if actual != JYTHON_SHA256:
        raise JythonRunnerError(
            f"Jython {JYTHON_VERSION} SHA-256 mismatch: {actual} != {JYTHON_SHA256}"
        )


def _fetch_pinned_jar(url: str, destination: Path) -> None:
    """One attempt: stream the pinned artifact into `destination`.

    A body larger than the pinned size is refused here without a retry, because
    refetching cannot turn it into the pinned artifact.
    """

    with urlopen(url, timeout=30) as response:  # noqa: S310 - the caller passes a fixed HTTPS URL
        total = 0
        with destination.open("wb") as stream:
            while chunk := response.read(DOWNLOAD_CHUNK_SIZE):
                total += len(chunk)
                if total > JYTHON_SIZE:
                    raise _OversizeArtifact(f"Jython download exceeded {JYTHON_SIZE} bytes")
                stream.write(chunk)


def _acquire_pinned_jar(url: str, jar: Path, cache: Path) -> None:
    """Fetch the pinned interpreter, retrying a transient failure with backoff.

    A transport failure, a truncated body and a digest mismatch are worth retrying;
    the pinned size and digest still decide what is accepted, so a retry cannot
    substitute another artifact, and an oversized body is refused at once.
    """

    last: BaseException | None = None
    for attempt in range(DOWNLOAD_ATTEMPTS):
        if attempt:
            time.sleep(DOWNLOAD_BACKOFF_SECONDS * (2 ** (attempt - 1)))
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(prefix="jython-", suffix=".jar", dir=cache, delete=False) as stream:
                temporary = Path(stream.name)
            _fetch_pinned_jar(url, temporary)
            _verify_jar(temporary)
            temporary.replace(jar)
            return
        except _OversizeArtifact:
            raise
        except (JythonRunnerError, OSError, ValueError) as error:
            last = error
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
    raise JythonRunnerError(
        f"Could not acquire pinned Jython artifact from {url} in {DOWNLOAD_ATTEMPTS} attempts: {last}"
    ) from last


def _ensure_jython_jar() -> Path:
    configured = os.environ.get("IGNITION_MCP_JYTHON_CACHE")
    cache = Path(configured).expanduser() if configured else DEFAULT_CACHE
    cache.mkdir(parents=True, exist_ok=True)
    jar = cache / f"jython-standalone-{JYTHON_VERSION}.jar"
    if jar.is_file():
        _verify_jar(jar)
        return jar

    _acquire_pinned_jar(JYTHON_URL, jar, cache)
    return jar


# Each recorded call is a short-lived JVM that runs one sub-second handler. The
# C1-only compiler and the serial collector cut its CPU cost by about 84% without
# changing Java or Jython semantics; stack and heap stay at their defaults so the
# depth- and size-bound fixtures behave exactly as before.
_JVM_FLAGS = ("-XX:TieredStopAtLevel=1", "-XX:+UseSerialGC", "-XX:CICompilerCount=1")


def _java_executable() -> str:
    configured = os.environ.get("JYTHON_RUNNER_JAVA")
    if configured:
        configured_path = Path(configured).expanduser()
        if not configured_path.is_file():
            raise JythonRunnerError(f"JYTHON_RUNNER_JAVA is not a file: {configured_path}")
        return str(configured_path)

    java_home_11 = os.environ.get("JAVA_HOME_11_X64")
    if java_home_11:
        java_home_path = Path(java_home_11) / "bin/java"
        if java_home_path.is_file():
            return str(java_home_path)

    resolved = shutil.which("java")
    if resolved is None:
        raise JythonRunnerError(
            "Java 11 was not found. Set JYTHON_RUNNER_JAVA to the Java 11 executable."
        )
    return resolved


@functools.lru_cache(maxsize=None)
def _require_java_11(java: str) -> None:
    """Check one resolved Java executable once per process.

    Every recorded call would otherwise start a JVM of its own just to read
    ``java -version``, which is the same executable answering the same question.
    The executable path is the key, so a caller that re-resolves it — including one
    whose environment moved ``JYTHON_RUNNER_JAVA`` — is checked again. A rejected
    executable raises, and an exception leaves no cache entry: the next call runs
    the check again.
    """
    completed = subprocess.run(
        [java, "-version"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    version_output = completed.stderr + completed.stdout
    if completed.returncode != 0 or not any(
        marker in version_output for marker in ('version "11.', 'openjdk 11.')
    ):
        raise JythonRunnerError(
            "The recorded Jython runner requires Java 11; java -version returned: "
            + version_output.strip()
        )


def _tool_paths(tool_name: str) -> tuple[Path, Path]:
    if not tool_name or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_" for character in tool_name):
        raise JythonRunnerError(f"Invalid Runtime Tool name: {tool_name!r}")
    handler = (
        ROOT
        / "packages/ignition-runtime-bundle/project/com.inductiveautomation.mcp/tools"
        / tool_name
        / "onToolCalled.py"
    )
    contract = ROOT / "contracts/tools/runtime" / f"{tool_name}.contract.json"
    if not handler.is_file() or not contract.is_file():
        raise JythonRunnerError(f"Runtime Tool implementation or contract is missing: {tool_name}")
    return handler, contract


def _validate_structured_content(contract_path: Path, result: dict[str, Any]) -> None:
    structured = result.get("structuredContent")
    if not isinstance(structured, dict):
        raise JythonRunnerError("Handler did not return object-valued structuredContent")
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    schema_path = ROOT / contract["outputSchema"]
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(structured)


def _error_codes() -> set[str]:
    document = json.loads(
        (ROOT / "contracts/shared/error-codes.json").read_text(encoding="utf-8")
    )
    return {str(code) for code in document["codes"]}


def _canonical_error(result: dict[str, Any]) -> dict[str, Any]:
    """D06: a Tool error is `content` text carrying the canonical error object.

    A handler returns ``content`` as one text part object (the Module wraps it
    into the protocol's single-element array), so both shapes are accepted here.
    """

    content = result.get("content")
    if isinstance(content, dict):
        content = [content]
    if not isinstance(content, list) or len(content) != 1 or not isinstance(content[0], dict):
        raise JythonRunnerError(f"Tool Error must contain exactly one content part: {result}")
    if content[0].get("type") != "text" or not isinstance(content[0].get("text"), str):
        raise JythonRunnerError(f"Tool Error must contain canonical JSON text: {result}")
    try:
        error = json.loads(content[0]["text"])
    except json.JSONDecodeError as error_or:  # pragma: no cover - defensive
        raise JythonRunnerError(f"Tool Error text is not JSON: {content[0]['text']!r}") from error_or
    if not isinstance(error, dict):
        raise JythonRunnerError("Tool Error text must be a JSON object")
    if not {"code", "message", "correlationId"} <= set(error) <= {"code", "message", "correlationId", "details"}:
        raise JythonRunnerError(f"Tool Error keys drifted from the canonical shape: {sorted(error)}")
    if error["code"] not in _error_codes():
        raise JythonRunnerError(f"Tool Error used an unknown code: {error['code']!r}")
    if not isinstance(error["message"], str) or not error["message"]:
        raise JythonRunnerError("Tool Error message must be a non-empty string")
    if not isinstance(error["correlationId"], str) or not error["correlationId"]:
        raise JythonRunnerError("Tool Error correlationId must be a non-empty string")
    if "details" in error and not isinstance(error["details"], dict):
        raise JythonRunnerError("Tool Error details must be an object")
    return error


def _run(tool_name: str, fixture_path: Path) -> tuple[dict[str, Any], Path]:
    """Run one unchanged Runtime Tool handler over one recorded fixture.

    Returns the raw handler result and the Tool's contract path, so each public
    wrapper can validate the shape it expects.
    """

    handler, contract = _tool_paths(tool_name)
    fixture = _fixture_path(fixture_path)

    java = _java_executable()
    _require_java_11(java)
    jar = _ensure_jython_jar()
    launcher = Path(__file__).with_name("jython_launcher.py")
    try:
        completed = subprocess.run(
            [java, *_JVM_FLAGS, "-jar", str(jar), str(launcher), str(handler), str(fixture)],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise JythonRunnerError(f"Jython process failed: {error}") from error
    if completed.returncode != 0:
        raise JythonRunnerError(
            f"Jython process exited {completed.returncode}: {completed.stderr.strip()}"
        )
    try:
        raw_result = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise JythonRunnerError(f"Jython emitted invalid JSON: {completed.stdout!r}") from error
    if not isinstance(raw_result, dict):
        raise JythonRunnerError("Jython handler result must be an object")
    return dict(raw_result), contract


def run_recorded_tool(tool_name: str, fixture_path: Path) -> dict[str, Any]:
    """Run one unchanged Runtime Tool handler with a recorded native result fixture."""
    result, contract = _run(tool_name, fixture_path)
    if result.get("isError") is True:
        raise JythonRunnerError(f"Runtime Tool returned an error: {result}")
    _validate_structured_content(contract, result)
    return result


def run_recorded_tool_error(
    tool_name: str, fixture_path: Path, *, expected_code: str | None = None
) -> dict[str, Any]:
    """Run a fixture whose recorded conditions must produce a canonical Tool error.

    The returned object is the Tool Error (`code`, `message`, `correlationId` and
    the Tool's optional `details`), so a test can assert *which* refusal a batch
    produced — for example a reserved-provider refusal rather than an allowlist one.
    """
    result, _contract = _run(tool_name, fixture_path)
    if result.get("isError") is not True:
        raise JythonRunnerError(f"Runtime Tool did not fail as recorded: {result}")
    if "structuredContent" in result:
        raise JythonRunnerError("A Tool Error must not carry structuredContent")
    error = _canonical_error(result)
    if expected_code is not None and error["code"] != expected_code:
        raise JythonRunnerError(f"Expected {expected_code}, got {error['code']}: {error}")
    return error


def _fixture_path(fixture_path: Path) -> Path:
    """The recorded fixture to replay, confined to the committed fixture directory.

    D29 restricts the vulnerable Jython 2.7.4 process to repository-controlled
    handlers and fixtures: a path that resolves outside ``fixtures/`` — including
    through a symlink — is rejected, as is one over :data:`MAX_FIXTURE_BYTES`.
    Both checks run before Java starts.
    """
    fixture = fixture_path.expanduser().resolve()
    if not fixture.is_relative_to(FIXTURE_DIR):
        raise JythonRunnerError(
            f"Recorded fixtures must come from {FIXTURE_DIR} (D29); "
            f"{fixture_path} resolves to {fixture}"
        )
    if not fixture.is_file():
        raise JythonRunnerError(f"Recorded fixture is missing: {fixture}")
    size = fixture.stat().st_size
    if size > MAX_FIXTURE_BYTES:
        raise JythonRunnerError(
            f"Recorded fixture is {size} bytes, over the {MAX_FIXTURE_BYTES}-byte limit: {fixture}"
        )
    return fixture

