#!/usr/bin/env python3
"""Run the unchanged G3 driver against the shared recorded Gateway fake.

The rehearsal starts the real ``ignition-rest-mcp`` server, runs ``driver.py``,
runs the gate-off inventory probe, and validates generated compatibility rows.
The fake replays recorded Native REST and Runtime MCP wire behavior on one
ephemeral listener. Port 8088 is never touched.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import zipfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tests/harness"))

from recorded_gateway import API_TOKEN, RecordedGateway  # noqa: E402

BUNDLE_VERSION = (ROOT / "packages/ignition-runtime-bundle/BUNDLE_VERSION").read_text(encoding="utf-8").strip()
JWT_ISSUER = "https://rehearsal.ignition-mcp.invalid"
JWT_AUDIENCE = "ignition-rest"
DISPOSABLE = "mcp_g3_0_local"
RUN_ID = "0"
SOURCE_REVISION = "a" * 40


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _zip_of(directory: Path) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(directory).as_posix())
    return buffer.getvalue()


def run(command: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(command[:7]), "...")
    return subprocess.run(command, capture_output=True, text=True, cwd=str(ROOT), env=env)


def start_rest_server(env: dict[str, str], port: int, log: Path, procs: list[subprocess.Popen[str]]) -> None:
    handle = subprocess.Popen(
        ["uv", "run", "--locked", "--package", "ignition-rest-mcp", "ignition-rest-mcp"],
        env=env,
        stdout=log.open("w"),
        stderr=subprocess.STDOUT,
        cwd=str(ROOT),
        text=True,
    )
    procs.append(handle)
    deadline = time.monotonic() + 90
    import http.client

    while time.monotonic() < deadline:
        if handle.poll() is not None:
            raise RuntimeError(f"ignition-rest died:\n{log.read_text()[-4000:]}")
        try:
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            connection.request("GET", "/health/ready")
            ready = connection.getresponse().status == 200
            connection.close()
            if ready:
                return
        except OSError:
            pass
        time.sleep(0.5)
    raise RuntimeError(f"ignition-rest did not become ready; see {log}")


def _build_release(work: Path) -> tuple[Path, Path, dict[str, object]] | None:
    release = run(
        [
            "uv", "run", "--no-sync", "python", "-m", "tooling.native.cli", "release",
            "--project-dir", "packages/ignition-runtime-bundle/project",
            "--out-dir", str(work / "release"),
            "--source-revision", SOURCE_REVISION,
            "--evidence-dir", "tests/compatibility/evidence",
        ],
        dict(os.environ),
    )
    if release.returncode != 0:
        print(release.stdout[-4000:], release.stderr[-4000:])
        return None
    manifest_path = work / "release" / f"ignition-runtime-bundle-{BUNDLE_VERSION}.manifest.json"
    zip_path = work / "release" / f"ignition-runtime-bundle-{BUNDLE_VERSION}.zip"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return manifest_path, zip_path, manifest


def _write_jwt_pair(work: Path) -> bool:
    jwt = run(
        [
            "uv", "run", "--locked", "--package", "ignition-rest-mcp", "python", "-c",
            "import sys; sys.path.insert(0, 'tests/harness/phase3-live')\n"
            "from driver import mint_pair\n"
            "private, public = mint_pair()\n"
            "open(sys.argv[1], 'w').write(private)\n"
            "open(sys.argv[2], 'w').write(public)\n",
            str(work / "jwt-private.pem"),
            str(work / "jwt-public.pem"),
        ],
        dict(os.environ),
    )
    if jwt.returncode != 0:
        print(jwt.stdout[-2000:], jwt.stderr[-2000:])
        return False
    os.chmod(work / "jwt-private.pem", 0o600)
    return True


def _write_marker(work: Path) -> None:
    (work / "ci-marker.json").write_text(
        json.dumps({
            "marker": "ignition-mcp-phase3-live",
            "environment": "phase3-live",
            "runId": RUN_ID,
            "gatewayVersion": "8.3.8",
            "gatewayBuild": "2026071409",
            "disposableProject": DISPOSABLE,
            "gatewayId": "rehearsal-local",
            "trustedRepo": "sheon-sek/ignition-mcp",
        }),
        encoding="utf-8",
    )


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="g3-rehearsal-"))
    print(f"workspace: {work}")
    release = _build_release(work)
    if release is None or not _write_jwt_pair(work):
        return 1
    manifest_path, zip_path, manifest = release
    readonly = manifest["profileInventories"]["readonly"]
    assert isinstance(readonly, dict)
    tools = readonly["tools"]
    resources = readonly["resources"]
    assert isinstance(tools, list) and isinstance(resources, list)

    rest_port = _free_port()
    projects = {
        "ignition_runtime": _zip_of(ROOT / "packages/ignition-runtime-bundle/project"),
        DISPOSABLE: _zip_of(ROOT / "tests/harness/phase3-live/fixture-project"),
    }
    with RecordedGateway(
        projects=projects,
        runtime_tools=tuple(sorted(str(item) for item in tools)),
        runtime_resources=tuple(str(item) for item in resources),
        source_revision=SOURCE_REVISION,
        bundle_version=BUNDLE_VERSION,
    ) as gateway:
        print(f"recorded gateway: {gateway.base_url}")
        print(f"runtime MCP     : {gateway.mcp_url}")
        print(f"ignition-rest   : http://127.0.0.1:{rest_port}")

        (work / "data").mkdir(parents=True, exist_ok=True)
        _write_marker(work)
        base_env = {
            **os.environ,
            "IGNITION_MCP_DATA_DIR": str(work / "data"),
            "IGNITION_MCP_GATEWAY_URL": gateway.base_url,
            "IGNITION_MCP_GATEWAY_API_TOKEN": API_TOKEN,
            "IGNITION_MCP_DEPLOYMENT_PROFILE": "development",
            "IGNITION_MCP_AUTH_MODE": "jwt",
            "IGNITION_MCP_JWT_PUBLIC_KEY": (work / "jwt-public.pem").read_text(encoding="utf-8"),
            "IGNITION_MCP_JWT_ISSUER": JWT_ISSUER,
            "IGNITION_MCP_JWT_AUDIENCE": JWT_AUDIENCE,
            "IGNITION_MCP_HOST": "127.0.0.1",
            "IGNITION_MCP_PORT": str(rest_port),
            "IGNITION_MCP_PATH": "/mcp",
            "IGNITION_MCP_WATCHER_INTERVAL_SECONDS": "1",
        }
        procs: list[subprocess.Popen[str]] = []
        try:
            start_rest_server(
                {**base_env, "IGNITION_MCP_SENSITIVE_EXPORTS_ENABLED": "true"},
                rest_port,
                work / "rest-gate-on.log",
                procs,
            )
            driver_env = {
                **base_env,
                "IGNITION_MCP_SENSITIVE_EXPORTS_ENABLED": "true",
                "IGNITION_MCP_PROJECT_WRITER_ENABLED": "true",
                "IGNITION_MCP_GATEWAY_ID": "rehearsal-local",
                "IGNITION_MCP_CONFIG_MUTATION_ENABLED": "true",
                "IGNITION_MCP_MUTATION_OPERATIONS": "project_import",
                "IGNITION_MCP_MUTATION_TARGETS": json.dumps({"project_import": [DISPOSABLE]}),
            }
            driver = subprocess.run(
                [
                    "uv", "run", "--locked", "--package", "ignition-rest-mcp", "python",
                    "tests/harness/phase3-live/driver.py",
                    "--ci-marker", str(work / "ci-marker.json"),
                    "--release-manifest", str(manifest_path),
                    "--release-zip", str(zip_path),
                    "--jwt-private-key", str(work / "jwt-private.pem"),
                    "--rest-url", f"http://127.0.0.1:{rest_port}",
                    "--runtime-mcp-url", gateway.mcp_url,
                    "--observations", str(work / "observations.json"),
                    "--raw-dir", str(work / "raw"),
                    "--expect-d27-tuple",
                ],
                capture_output=True,
                text=True,
                cwd=str(ROOT),
                env=driver_env,
            )
            print(driver.stdout[-12000:])
            if driver.stderr.strip():
                print("driver stderr:\n", driver.stderr[-4000:])
            if driver.returncode != 0:
                print(
                    f"driver rc={driver.returncode}; server log tail:\n"
                    f"{(work / 'rest-gate-on.log').read_text()[-4000:]}"
                )
                return 1

            for proc in procs:
                proc.terminate()
            for proc in procs:
                with contextlib.suppress(Exception):
                    proc.wait(timeout=20)
            procs.clear()
            start_rest_server(
                {**base_env, "IGNITION_MCP_SENSITIVE_EXPORTS_ENABLED": "false"},
                rest_port,
                work / "rest-gate-off.log",
                procs,
            )
            gateoff = run(
                [
                    "uv", "run", "--locked", "--package", "ignition-rest-mcp", "python",
                    "tests/harness/phase3-live/inventory_gate_off.py",
                    "--rest-url", f"http://127.0.0.1:{rest_port}",
                    "--jwt-private-key", str(work / "jwt-private.pem"),
                    "--jwt-issuer", JWT_ISSUER,
                    "--jwt-audience", JWT_AUDIENCE,
                    "--output", str(work / "gate-off-inventory.json"),
                ],
                base_env,
            )
            print(gateoff.stdout, gateoff.stderr)
            if gateoff.returncode != 0:
                return 1

            sha256 = hashlib.sha256(zip_path.read_bytes()).hexdigest()
            for version, build in (("8.3.8", "2026071409"), ("8.3.9", "2026082511")):
                identity = {
                    "gatewayVersion": version,
                    "gatewayBuild": build,
                    "gatewayImage": f"inductiveautomation/ignition:{version}",
                    "gatewayImageDigest": "sha256:" + "0" * 64,
                    "mcpModuleVersion": "1.3.5-SNAPSHOT",
                    "mcpModuleArtifactVersion": "1.3.5.2026021307-SNAPSHOT",
                    "mcpModuleBuild": "2026021307",
                    "mcpModuleSha256": "b1142a5796f2fd834555f13f03de706599d745f7172a68e54f2f7908b67fe365",
                    "bundleVersion": BUNDLE_VERSION,
                    "bundleSha256": sha256,
                    "deployedBundleSha256": sha256,
                    "sourceRevision": SOURCE_REVISION,
                    "runId": RUN_ID,
                }
                identity_path = work / f"identity-{version}.json"
                identity_path.write_text(json.dumps(identity), encoding="utf-8")
                generated = run(
                    [
                        "uv", "run", "--no-sync", "python", "-m", "tooling.compat", "generate",
                        "--observations", str(work / "observations.json"),
                        "--identity", str(identity_path),
                        "--gate-off-report", str(work / "gate-off-inventory.json"),
                        "--out-dir", str(work / f"evidence-{version}"),
                    ],
                    base_env,
                )
                print(generated.stdout, generated.stderr)
                if generated.returncode != 0:
                    return 1

            merged = work / "evidence-merged"
            merged.mkdir()
            for version in ("8.3.8", "8.3.9"):
                for row_dir in (work / f"evidence-{version}").iterdir():
                    row_dir.rename(merged / row_dir.name)
            validated = run(
                [
                    "uv", "run", "--no-sync", "python", "-m", "tooling.compat", "validate",
                    "--evidence-dir", str(merged),
                ],
                base_env,
            )
            print(validated.stdout, validated.stderr)
            if validated.returncode != 0:
                return 1

            print(f"REHEARSAL PASSED; gateway imports observed: {gateway.imports}")
            print(f"workspace kept at {work}")
            return 0
        finally:
            for proc in procs:
                proc.terminate()
            for proc in procs:
                with contextlib.suppress(Exception):
                    proc.wait(timeout=10)


if __name__ == "__main__":
    sys.exit(main())
