"""Installed-wheel proof for ``ignition-mcp setup-native`` (Slice 10, D20/D25).

The console script is built with ``uv build``, installed into a throwaway virtual
environment outside the checkout, and run with a working directory that is not the
repository and an environment that carries nothing from it.  A real ``doctor`` and
``plan`` probe then talks to an in-test localhost Gateway, so the run proves the
shipped wheel needs nothing from the monorepo: no ``contracts/``, no
``packages/ignition-runtime-bundle/``, no ``tooling`` import.
"""

from __future__ import annotations

import hashlib
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
TOOLS = ["alpha", "beta", "bundle_info"]
RESOURCE_URIS = ["ignition://?contracts/alpha-output"]
SOURCE_REVISION = "a" * 40
GATEWAY_TOKEN = "GWSENTINELwheel0001112223334"
TIMEOUT_SECONDS = 180

UV = shutil.which("uv")
pytestmark = pytest.mark.skipif(UV is None, reason="uv is required to build and install the wheel")


class FakeGatewayHandler(http.server.BaseHTTPRequestHandler):
    """The documented read-only Gateway routes, served over real HTTP on localhost."""

    server_version = "IgnitionTestFake/8.3.8"
    project_state = "managed"

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
        if self.headers.get("X-Ignition-API-Token") != GATEWAY_TOKEN:
            self._json(401, {"message": "no token"})
            return
        path = self.path.split("?")[0]
        if path == "/data/api/v1/gateway-info":
            self._json(200, {"name": "wheel-fake", "ignitionVersion": "8.3.8 (b2026071409)"})
        elif path == "/openapi.json":
            self._send(200, _openapi_document())
        elif path == "/data/api/v1/modules/healthy":
            self._json(200, {"total": 1, "items": [{"id": "com.inductiveautomation.mcp",
                                                    "version": "1.3.5.2026021307-SNAPSHOT"}]})
        elif path == "/data/api/v1/projects/find/ignition_runtime":
            if self.project_state == "absent":
                self._json(404, {"message": "not found"})
            else:
                self._json(200, {"name": "ignition_runtime", "inheritable": False,
                                 "description": "Runtime MCP Bundle.\n"
                                                "ignition-mcp-managed: product=ignition-runtime-bundle; "
                                                f"bundle={BUNDLE_VERSION}"})
        elif path.startswith("/data/api/v1/resources/find/com.inductiveautomation.mcp/server-config/"):
            self._json(200, {"name": "wheel", "attributes": {"tools": TOOLS}})
        else:
            self._json(404, {"message": f"no route {path}"})


def _openapi_document() -> bytes:
    paths = {
        "/data/api/v1/gateway-info": {"get": {}},
        "/data/api/v1/projects/find/{name}": {"get": {}},
        "/data/api/v1/projects/import/{name}": {"post": {}},
        "/data/api/v1/resources/find/com.inductiveautomation.mcp/server-config/{name}": {"get": {}},
        "/data/api/v1/resources/singleton/ignition/security-levels": {"get": {}},
        "/data/api/v1/resources/ignition/api-token": {"post": {}},
        "/data/api/v1/designers": {"get": {}},
    }
    return json.dumps({"openapi": "3.0.1", "paths": paths}).encode("utf-8")


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
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeGatewayHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def manifest_document() -> dict[str, Any]:
    def profile(permissions: list[str], resources: list[str]) -> dict[str, Any]:
        return {"permissions": permissions, "tools": list(TOOLS), "resources": resources, "prompts": []}

    return {
        "schemaVersion": 1,
        "bundleVersion": BUNDLE_VERSION,
        "sourceRevision": SOURCE_REVISION,
        "resourceSchemaVersion": 1,
        "nativeResponseBindingStatus": "VERIFIED_WITH_LIMITATION",
        "artifact": {
            "filename": f"ignition-runtime-bundle-{BUNDLE_VERSION}.zip",
            "sha256": hashlib.sha256(b"wheel bundle artifact").hexdigest(),
            "sizeBytes": 19,
        },
        "tools": list(TOOLS),
        "resources": list(RESOURCE_URIS),
        "prompts": [],
        "toolRequirements": {name: {"permissionClass": "READ"} for name in TOOLS},
        "profileInventories": {
            "readonly": profile(["READ"], list(RESOURCE_URIS)),
            "operator": profile(["CONTROL", "READ"], []),
            "configurator": profile(["CONFIG", "READ"], []),
            "full": profile(["CONFIG", "CONTROL", "READ"], []),
        },
        "testedTuples": [],
    }


def prepare_deployment(workspace: Path) -> list[str]:
    """Write the manifest, its ZIP and a 0600 token file outside the repository."""

    manifest = workspace / "bundle.manifest.json"
    manifest.write_text(json.dumps(manifest_document(), indent=2, sort_keys=True), encoding="utf-8")
    artifact = workspace / f"ignition-runtime-bundle-{BUNDLE_VERSION}.zip"
    artifact.write_bytes(b"wheel bundle artifact")
    token = workspace / "gateway.token"
    token.write_text(f"{GATEWAY_TOKEN}\n", encoding="utf-8")
    token.chmod(0o600)
    return ["--bundle-manifest", str(manifest), "--bundle-zip", str(artifact),
            "--gateway-token-file", str(token), "--bundle-project", "ignition_runtime"]


def execute(workspace: Path, arguments: list[str], gateway_url: str) -> subprocess.CompletedProcess[str]:
    """Run the installed console script with a minimal environment, outside the repo."""

    environment = {
        "PATH": f"{workspace / 'venv' / 'bin'}:/usr/bin:/bin",
        "HOME": str(workspace),
        "IGNITION_MCP_SETUP_GATEWAY_URL": gateway_url,
        "IGNITION_MCP_SETUP_MCP_URL": "http://127.0.0.1:1/mcp",
    }
    return subprocess.run(
        [str(workspace / "venv" / "bin" / "ignition-mcp"), "setup-native", *arguments],
        cwd=str(workspace), env=environment, capture_output=True, text=True,
        timeout=TIMEOUT_SECONDS, check=False,
    )


def test_installed_help_documents_exit_codes_and_every_command(sandbox: Path) -> None:
    run = execute(sandbox, ["doctor", "--help"], "http://127.0.0.1:1")
    assert run.returncode == 0, run.stderr
    assert "exit codes:" in run.stdout
    assert "0  command completed with no FAIL" in run.stdout
    assert "3  wrote nothing because an explicit operator acknowledgement is missing" in run.stdout

    group = execute(sandbox, ["--help"], "http://127.0.0.1:1")
    assert group.returncode == 0, group.stderr
    assert "{doctor,plan,verify,apply,install-module}" in group.stdout

    module = execute(sandbox, ["install-module", "--help"], "http://127.0.0.1:1")
    assert module.returncode == 0, module.stderr
    assert "--file" in module.stdout and "--sha256" in module.stdout and "--restart" in module.stdout
    assert "--bundle-manifest" not in module.stdout


def test_installed_doctor_uses_only_the_environment_and_the_manifest(sandbox: Path,
                                                                    live_gateway: str) -> None:
    arguments = prepare_deployment(sandbox)
    run = execute(sandbox, ["doctor", *arguments, "--json"], live_gateway)
    # The Runtime MCP endpoint is deliberately dead, so doctor must report exit 1,
    # while every Gateway-side observation still succeeded over real localhost HTTP.
    assert run.returncode == 1, run.stdout + run.stderr
    report = json.loads(run.stdout)
    checks = {check["name"]: check for check in report["checks"]}
    assert checks["gateway-info"]["status"] == "PASS"
    assert checks["openapi-sha256"]["status"] == "PASS"
    assert checks["module-installed"]["status"] == "PASS"
    assert checks["bundle-project"]["status"] == "PASS"
    assert "MANAGED bundle=0.2.0" in checks["bundle-project"]["detail"]
    assert checks["mcp-initialize"]["status"] == "FAIL"
    assert checks["inventory-tools"]["status"] == "SKIP"
    assert GATEWAY_TOKEN not in run.stdout and GATEWAY_TOKEN not in run.stderr


def test_installed_plan_needs_no_mcp_plane(sandbox: Path, live_gateway: str) -> None:
    arguments = prepare_deployment(sandbox)
    run = execute(sandbox, ["plan", *arguments], live_gateway)
    assert run.returncode == 0, run.stdout + run.stderr
    lines = run.stdout.splitlines()
    assert lines[-1] == "No changes have been applied."
    assert any(line.startswith("NO CHANGE bundle-project ignition_runtime") for line in lines)
    assert any(line.startswith("NO CHANGE mcp-module") for line in lines)
    assert not any(line.startswith("BLOCKED") for line in lines)


def test_installed_cli_resolves_nothing_inside_the_repository(sandbox: Path) -> None:
    """A repo-internal manifest path is just a path: it is not looked up relative to anything."""

    argv = prepare_deployment(sandbox)
    missing = [argv[0], str(REPO_ROOT / "contracts" / "nope.json"), *argv[2:]]
    run = execute(sandbox, ["plan", *missing], "http://127.0.0.1:1")
    assert run.returncode == 2
    assert "--bundle-manifest" in run.stderr
    assert "cannot read" in run.stderr
