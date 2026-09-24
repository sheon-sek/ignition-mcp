#!/usr/bin/env python3
"""Rehearse the G7 stage (issue #78) on a local docker Gateway before a live CI run.

The stage drives the real CLI against a real Gateway, so a recorded fake would prove
little. This script repeats the ``phase4-live-apply`` workflow's steps on this
machine instead: a fresh Gateway from ``docker-compose.yml`` plus
``docker-compose-install.yml`` (no MCP Module), the wait for commissioning, the CI
security bootstrap, the wait for authenticated Native REST, then
``setup_stage.py``. The compose project is ``p7-g7-rehearsal`` and is removed with
its volume at the end, whatever the outcome.

    uv run --no-sync python tests/harness/phase4-live/rehearse_setup.py [--keep]

``--keep`` leaves the Gateway running after the stage, for a look at a failure.
It needs docker, the ``inductiveautomation/ignition:8.3.8`` image and host port
8093. Exit 0 means the stage passed. A rehearsal is never evidence: the stage's
document goes to a temporary directory, which the script names at the end.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import driver  # noqa: E402

PROJECT = "p7-g7-rehearsal"
IMAGE = os.environ.get("GATEWAY_IMAGE", "inductiveautomation/ignition:8.3.8")
BASE_URL = "http://127.0.0.1:8093"
DATA = "/usr/local/bin/ignition/data/config/resources/core/ignition"
COMMISSION_SECONDS = 480.0
REST_SECONDS = 300.0
POLL_SECONDS = 3.0

ENV = {
    **os.environ,
    "COMPOSE_PROJECT_NAME": PROJECT,
    "COMPOSE_FILE": f"{HERE / 'docker-compose.yml'}{os.pathsep}{HERE / 'docker-compose-install.yml'}",
    "GATEWAY_IMAGE": IMAGE,
}


def sh(*words: str, check: bool = True) -> str:
    completed = subprocess.run(list(words), cwd=str(ROOT), env=ENV, capture_output=True, text=True, check=False)
    if check and completed.returncode != 0:
        raise SystemExit(f"{' '.join(words)} failed: {completed.stderr[-1200:]}")
    return completed.stdout


def wait(what: str, seconds: float, ready: Callable[[], bool]) -> None:
    deadline = driver.CLOCK.now() + seconds
    while driver.CLOCK.now() < deadline:
        if ready():
            return
        driver.CLOCK.wait(POLL_SECONDS)
    raise SystemExit(f"{what}: deadline of {seconds:g} s exceeded")


def commissioned() -> bool:
    log = sh("docker", "compose", "logs", "--no-color", "gateway", check=False)
    if "ContextState = FAULTED" in log:
        raise SystemExit("the Gateway FAULTED during commissioning")
    return "ContextState = RUNNING" in log and _status(BASE_URL + "/StatusPing") == 200


def _status(url: str, token: str | None = None) -> int:
    request = urllib.request.Request(url)
    if token:
        request.add_header("X-Ignition-API-Token", token)
    try:
        with urllib.request.urlopen(request, timeout=5.0) as response:
            return int(response.status)
    except urllib.error.HTTPError as error:
        return int(error.code)
    except (urllib.error.URLError, OSError):
        return 0


def bootstrap_security(work: Path) -> str:
    """The workflow's two security steps: prepare the CI level and key, install them."""

    cid = sh("docker", "compose", "ps", "-q", "gateway").strip()
    boot = work / "security-bootstrap"
    boot.mkdir()
    for name in ("security-levels", "security-properties"):
        sh("docker", "cp", f"{cid}:{DATA}/{name}", str(boot / name))
    github_env = work / "github-env"
    sh(
        sys.executable, "tests/harness/runtime-binding/prepare_ci_security.py",
        "--root", str(boot), "--evidence", str(work / "ci-security-bootstrap.json"), "--github-env", str(github_env),
    )
    token = next(
        line.partition("=")[2] for line in github_env.read_text("utf-8").splitlines() if line.startswith("CI_API_TOKEN=")
    )
    owner = sh("docker", "exec", cid, "stat", "-c", "%u:%g", "/usr/local/bin/ignition/data").strip()
    sh("docker", "exec", cid, "mkdir", "-p", f"{DATA}/api-token")
    sh("docker", "compose", "stop", "-t", "30", "gateway")
    sh("docker", "cp", f"{boot}/security-levels/.", f"{cid}:{DATA}/security-levels/")
    sh("docker", "cp", f"{boot}/security-properties/.", f"{cid}:{DATA}/security-properties/")
    sh("docker", "cp", f"{boot}/api-token/ignition-mcp-ci", f"{cid}:{DATA}/api-token/ignition-mcp-ci")
    sh(
        "docker", "run", "--rm", "--volumes-from", cid, "--entrypoint", "/bin/sh", "--user", "0:0", IMAGE, "-c",
        f"chown -R '{owner}' {DATA}/security-levels {DATA}/security-properties {DATA}/api-token/ignition-mcp-ci",
    )
    sh("docker", "compose", "start", "gateway")
    return token


def main() -> int:
    parser = argparse.ArgumentParser(description="Rehearse the G7 stage on a local docker Gateway")
    parser.add_argument("--keep", action="store_true", help="leave the Gateway running afterwards")
    keep = parser.parse_args().keep
    work = Path(tempfile.mkdtemp(prefix="g7-rehearsal-"))
    sh("docker", "compose", "down", "-v", "--remove-orphans", check=False)
    try:
        sh("docker", "compose", "up", "-d", "--no-build")
        wait("commissioning", COMMISSION_SECONDS, commissioned)
        print("gateway commissioned", flush=True)
        token = bootstrap_security(work)
        url = BASE_URL + "/data/api/v1/gateway-info"
        wait("authenticated Native REST", REST_SECONDS, lambda: _status(url, token) == 200)
        print("authenticated Native REST is ready", flush=True)
        evidence = work / "evidence"
        stage = subprocess.run(
            [
                sys.executable, str(HERE / "setup_stage.py"),
                "--base-url", BASE_URL, "--api-token", token, "--evidence-dir", str(evidence),
            ],
            cwd=str(ROOT), env=ENV, check=False,
        )
        print(f"stage exit {stage.returncode}; evidence in {evidence}", flush=True)
        return stage.returncode
    finally:
        (work / "gateway.log").write_text(sh("docker", "compose", "logs", "--no-color", "gateway", check=False), "utf-8")
        if not keep:
            sh("docker", "compose", "down", "-v", "--remove-orphans", check=False)


if __name__ == "__main__":
    raise SystemExit(main())
