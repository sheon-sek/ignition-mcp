"""Installed-wheel proof for ``ignition-mcp setup|status|start|connect|reset`` (issue #77).

The console script is built with ``uv build``, installed into a throwaway virtual
environment outside the checkout, and run with a working directory that is not the
repository and an environment that carries nothing from it. A real ``status`` run
then talks to an in-test localhost Gateway, so the run proves the shipped wheel
needs nothing from the monorepo: no ``contracts/``, no
``packages/ignition-runtime-bundle/``, no ``tooling`` import.
"""

from __future__ import annotations

import http.server
import json
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any, Iterator

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLE_VERSION = "0.2.0"
GATEWAY_KEY = "GWSENTINELwheel0001112223334"
GATEWAY_TOKEN = f"setup:{GATEWAY_KEY}"
TIMEOUT_SECONDS = 180

UV = shutil.which("uv")
pytestmark = pytest.mark.skipif(UV is None, reason="uv is required to build and install the wheel")

#: Every ``(method, path)`` the fake Gateway answered, in order.
REQUESTS: list[tuple[str, str]] = []


class FakeGatewayHandler(http.server.BaseHTTPRequestHandler):
    """The read-only Gateway routes ``status`` needs, served over real localhost HTTP."""

    server_version = "IgnitionTestFake/8.3.8"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - handler signature
        return

    def _send(self, status: int, payload: bytes) -> None:
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, status: int, document: Any) -> None:
        self._send(status, json.dumps(document).encode("utf-8"))

    def do_GET(self) -> None:  # noqa: N802 - handler signature
        path = self.path.split("?")[0]
        REQUESTS.append(("GET", path))
        if self.headers.get("X-Ignition-API-Token") != GATEWAY_TOKEN:
            self._json(403, {"message": "no token"})
            return
        if path == "/data/api/v1/gateway-info":
            self._json(200, {"name": "wheel-fake", "ignitionVersion": "8.3.8 (b2026071409)"})
        elif path == "/data/api/v1/modules/healthy":
            self._json(200, {"total": 1, "items": [{"id": "com.inductiveautomation.mcp",
                                                    "version": "1.3.5.2026021307-SNAPSHOT"}]})
        elif path == "/data/api/v1/projects/find/ignition_runtime":
            self._json(200, {"name": "ignition_runtime", "inheritable": False,
                             "description": "Runtime MCP Bundle.\n"
                                            "ignition-mcp-managed: product=ignition-runtime-bundle; "
                                            f"bundle={BUNDLE_VERSION}"})
        else:
            self._json(404, {"message": f"no route {path}"})


@pytest.fixture(scope="module")
def sandbox(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build the wheel, install it into a venv outside the checkout, return the sandbox."""

    assert UV is not None
    workspace = tmp_path_factory.mktemp("wheel")
    dist = workspace / "dist"
    built = subprocess.run(
        [UV, "build", "--package", "ignition-rest-mcp", "--out-dir", str(dist)],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=TIMEOUT_SECONDS, check=False,
    )
    assert built.returncode == 0, built.stderr[-2000:]
    wheels = sorted(dist.glob("ignition_rest_mcp-*.whl"))
    assert wheels, list(dist.iterdir())

    venv = workspace / "venv"
    for argv in ([UV, "venv", str(venv)],
                 [UV, "pip", "install", "--python", str(venv / "bin" / "python"), str(wheels[0])]):
        installed = subprocess.run(argv, cwd=str(workspace), capture_output=True, text=True,
                                  timeout=TIMEOUT_SECONDS, check=False)
        assert installed.returncode == 0, f"{argv[1]}: {installed.stderr[-2000:]}"
    assert (venv / "bin" / "ignition-mcp").is_file(), "the wheel must ship the ignition-mcp console script"
    return workspace


@pytest.fixture
def live_gateway() -> Iterator[str]:
    """Start the fake Gateway, and keep the requests it answered for the assertions."""

    REQUESTS.clear()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeGatewayHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def token_file(workspace: Path) -> Path:
    """Write the deployment's Gateway credential file outside the repository."""

    path = workspace / "gateway.token"
    path.write_text(GATEWAY_TOKEN + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def execute(workspace: Path, arguments: list[str], gateway_url: str = "http://127.0.0.1:1") -> subprocess.CompletedProcess[str]:
    """Run the installed console script with a minimal environment, outside the repo."""

    environment = {
        "PATH": f"{workspace / 'venv' / 'bin'}:/usr/bin:/bin",
        "HOME": str(workspace),
        "COLUMNS": "200",
        "IGNITION_MCP_SETUP_GATEWAY_URL": gateway_url,
    }
    return subprocess.run(
        [str(workspace / "venv" / "bin" / "ignition-mcp"), *arguments],
        cwd=str(workspace), env=environment, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=TIMEOUT_SECONDS, check=False,
    )


def test_installed_help_documents_exit_codes_and_every_command(sandbox: Path) -> None:
    run = execute(sandbox, ["--help"])
    assert run.returncode == 0, run.stderr
    assert "Exit codes: 0 success, 1 a step failed, 2 a problem with the flags or answers." in run.stdout
    assert "{setup,status,start,connect,reset}" in run.stdout


def test_installed_status_reads_the_gateway_and_prints_a_json_document(
    sandbox: Path, live_gateway: str
) -> None:
    run = execute(
        sandbox,
        ["status", "--gateway-url", live_gateway, "--gateway-token-file", str(token_file(sandbox)), "--json"],
        live_gateway,
    )
    # No deployment exists in the sandbox, so status reports exit 1, while the Gateway
    # read itself still succeeded over real localhost HTTP.
    assert run.returncode == 1, run.stdout + run.stderr
    document = json.loads(run.stdout)
    steps = {step["step"]: step for step in document["steps"]}
    assert steps["gateway"]["status"] == "OK"
    assert "8.3.8" in steps["gateway"]["reason"]
    assert ("GET", "/data/api/v1/gateway-info") in REQUESTS
    assert steps["deployment"]["status"] == "FAILED"
    assert GATEWAY_KEY not in run.stdout and GATEWAY_KEY not in run.stderr


def test_installed_command_missing_a_value_fails_in_a_non_tty_run(sandbox: Path) -> None:
    run = execute(sandbox, ["status"])
    assert run.returncode == 2
    assert "stdin is not a terminal" in run.stdout + run.stderr
    assert "--gateway-url" in run.stdout + run.stderr


def test_installed_cli_resolves_nothing_inside_the_repository(sandbox: Path) -> None:
    """A repo-internal path is just a path: it is not looked up relative to anything."""

    missing = REPO_ROOT / "contracts" / "nope.json"
    run = execute(sandbox, ["status", "--gateway-url", "http://127.0.0.1:1",
                            "--gateway-token-file", str(missing)])
    assert run.returncode == 2
    assert str(missing) in run.stdout + run.stderr
