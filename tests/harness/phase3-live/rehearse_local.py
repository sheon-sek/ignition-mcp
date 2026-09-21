#!/usr/bin/env python3
"""Full local rehearsal of the phase3-live-g3 job without Docker.

Runs the real ``driver.py``, the real ``ignition-rest-mcp`` server and the real
``inventory_gate_off.py`` against two fakes that serve the wire shapes observed
on live Gateways in G3 runs 35588029382-35589924939:

- fake Ignition Gateway: stateful project store; exports re-canonicalize
  project.json compactly and idempotently (the live behavior g3diag R1 proved);
  import accepts chunked bodies; the module list serves the display version
  ``1.3.5-SNAPSHOT (b2026021307)``; designer/list metadata use float counts;
  every route requires the API-token header;
- fake Runtime Bundle MCP endpoint: API-token header auth (bearer-only gets
  403), 415 on the GET probe, exactly the 13 tools with no published
  outputSchema, resources present, prompts/list answered with -32600,
  tag_read failures with isError, bundle_info with the stamped revision.

Usage (from the repo root):
    uv run --no-sync python tests/harness/phase3-live/rehearse_local.py

Exit 0 means the whole G3 pipeline passed locally (gate-on driver run, gate-off
probe, evidence rows for both tuple shapes, merged-set validation). Listeners
use ephemeral ports only; 8088 (the user's Gateway) is never touched.
"""
from __future__ import annotations

import contextlib
import hashlib
import http.server
import io
import json
import os
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
BUNDLE_VERSION = (ROOT / "packages/ignition-runtime-bundle/BUNDLE_VERSION").read_text(encoding="utf-8").strip()
JWT_ISSUER = "https://rehearsal.ignition-mcp.invalid"
JWT_AUDIENCE = "ignition-rest"
API_TOKEN = "rehearse-token:" + "Zk9Zk9Zk9Zk9Zk9Zk9Zk9Zk9Zk9"
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


def _canonical_export(project_zip: bytes) -> bytes:
    """Mimic the live Gateway: export rewrites project.json compactly; idempotent."""
    source = zipfile.ZipFile(io.BytesIO(project_zip))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for info in source.infolist():
            data = source.read(info.filename)
            if info.filename == "project.json":
                data = json.dumps(json.loads(data), sort_keys=True, separators=(",", ":")).encode("utf-8")
            archive.writestr(info.filename, data)
    return out.getvalue()


OPENAPI_PATHS: dict[str, dict[str, dict[str, str]]] = {}
for _method, _path in [
    ("get", "/data/api/v1/gateway-info"),
    ("get", "/data/api/v1/projects/list"),
    ("get", "/data/api/v1/projects/export/{name}"),
    ("get", "/data/api/v1/projects/find/{name}"),
    ("post", "/data/api/v1/projects/import/{name}"),
    ("get", "/data/api/v1/designers"),
    ("get", "/data/api/v1/tags/export"),
    ("get", "/data/api/v1/audit/log/{name}"),
    ("get", "/data/alarm-notification/api/v1/pipelines"),
    ("get", "/data/alarm-notification/api/v1/pipeline"),
    ("get", "/data/api/v1/resources/find/com.inductiveautomation.mcp/server-config/{name}"),
    ("get", "/data/api/v1/resources/type/ignition.gateway/idp-links"),
    ("get", "/data/api/v1/resources/names/ignition.gateway/idp-links"),
    ("get", "/data/api/v1/resources/list/ignition.gateway/idp-links"),
    ("get", "/data/api/v1/resources/find/ignition.gateway/idp-links/{name}"),
    ("get", "/data/api/v1/resources/singleton/ignition/security-levels"),
    ("post", "/data/api/v1/resources/ignition/api-token"),
]:
    OPENAPI_PATHS.setdefault(_path, {})[_method] = {"summary": "rehearsal"}


class _Base(http.server.BaseHTTPRequestHandler):
    timeout = 30
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args: object) -> None:
        pass

    def _send(self, status: int, body: bytes, content_type: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, value: object) -> None:
        self._send(status, json.dumps(value).encode())


class GatewayHandler(_Base):
    """Stateful fake 8.3.x Gateway; wire shapes pinned by the G3 live runs."""

    def _authed(self) -> bool:
        return self.headers.get("X-Ignition-API-Token") == API_TOKEN

    def _read_body(self) -> bytes:
        if (self.headers.get("Transfer-Encoding") or "").lower() == "chunked":
            chunks = bytearray()
            while True:
                size = int(self.rfile.readline().strip().split(b";")[0], 16)
                if size == 0:
                    self.rfile.readline()  # final CRLF after the terminating chunk
                    break
                chunks.extend(self.rfile.read(size))
                self.rfile.readline()  # CRLF after each chunk
            return bytes(chunks)
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def do_GET(self) -> None:  # noqa: N802
        if not self._authed():
            self._json(403, {"message": "Forbidden", "status": "403"})
            return
        path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
        server: Any = self.server
        if path == "/data/api/v1/gateway-info":
            self._json(200, {"name": "rehearsal-gateway", "edition": "standard",
                             "ignitionVersion": "8.3.8 (b2026071409)"})
        elif path == "/openapi.json":
            self._json(200, server.openapi_document)
        elif path == "/data/api/v1/modules/healthy":
            self._json(200, {"total": 1, "items": [
                {"id": "com.inductiveautomation.mcp", "version": "1.3.5-SNAPSHOT (b2026021307)",
                 "installed": True, "healthy": True},
            ]})
        elif path == "/data/api/v1/projects/list":
            names = sorted(server.projects)
            self._json(200, {"items": [{"name": n} for n in names],
                             "metadata": {"total": float(len(names)), "matching": float(len(names)),
                                          "limit": 500, "offset": 0}})
        elif path.startswith("/data/api/v1/projects/find/"):
            name = path.rsplit("/", 1)[-1]
            if name not in server.projects:
                self._json(404, {"message": "not found"})
                return
            document = json.loads(zipfile.ZipFile(io.BytesIO(server.projects[name])).read("project.json"))
            self._json(200, {"name": name, "title": document.get("title", name),
                             "description": document.get("description", ""),
                             "inheritable": bool(document.get("inheritable", False))})
        elif path.startswith("/data/api/v1/resources/find/com.inductiveautomation.mcp/server-config/"):
            self._json(200, {"name": "phase3-runtime"})
        elif path.startswith("/data/api/v1/projects/export/"):
            name = path.rsplit("/", 1)[-1]
            if name not in server.projects:
                self._json(404, {"message": "no project"})
                return
            self._send(200, _canonical_export(server.projects[name]), "application/zip")
        elif path == "/data/api/v1/tags/export":
            self._json(200, {"path": "", "tags": [{"name": "Status", "path": "mcp_rehearse/Status",
                                                   "tagType": "Boolean"}]})
        elif path == "/data/api/v1/designers":
            self._json(200, {"items": [], "metadata": {"total": 0.0, "matching": 0.0,
                                                       "limit": 100, "offset": 0}})
        else:
            self._json(404, {"message": f"unexpected GET {path}"})

    def do_POST(self) -> None:  # noqa: N802
        if not self._authed():
            self._json(403, {"message": "Forbidden", "status": "403"})
            return
        path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
        server: Any = self.server
        if path.startswith("/data/api/v1/projects/import/"):
            name = path.rsplit("/", 1)[-1]
            body = self._read_body()
            server.imports.append({"project": name, "size": len(body)})
            server.projects[name] = body
            self._json(200, {"message": f"Project {name} imported"})
        else:
            self._json(404, {"message": f"unexpected POST {path}"})


class RuntimeMcpHandler(_Base):
    """The module-served Runtime Bundle endpoint, exact live refusals included."""

    def do_GET(self) -> None:  # noqa: N802 - the CLI reachability probe hits GET
        # The live module answers 415 Unsupported Media Type here (run 35589924939).
        self.send_response(415)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        if self.headers.get("X-Ignition-API-Token") != API_TOKEN:
            # Live: bearer-only API tokens are refused with 403 (run 35588754132).
            self._json(403, {"message": "Forbidden", "status": "403"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length))
        method = str(payload.get("method"))
        request_id = payload.get("id")
        server: Any = self.server
        result: Any = None
        error: Any = None
        if method == "initialize":
            result = {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {"listChanged": False}, "resources": {"listChanged": False}},
                "serverInfo": {"name": "phase3-runtime", "title": "Phase 3 Runtime Readonly",
                               "version": BUNDLE_VERSION},
            }
        elif method == "notifications/initialized":
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        elif method == "tools/list":
            result = {"tools": [{"name": name, "description": f"{name} rehearsal"} for name in server.tools]}
        elif method == "resources/list":
            result = {"resources": [{"uri": uri, "name": uri.rsplit("/", 1)[-1]} for uri in server.resources]}
        elif method == "resources/read":
            uri = str((payload.get("params") or {}).get("uri"))
            result = {"contents": [{"uri": uri, "mimeType": "application/json",
                                    "text": json.dumps({"schemaVersion": 1, "rehearsal": True})}]}
        elif method == "prompts/list":
            # Live: the module refuses the unimplemented capability with -32600.
            error = {"code": -32600, "message": "Invalid Request: prompts not offered"}
        elif method == "tools/call":
            name = str((payload.get("params") or {}).get("name"))
            if name == "bundle_info":
                result = {"content": [{"type": "text", "text": "ok"}], "isError": False,
                          "structuredContent": {
                              "bundleVersion": BUNDLE_VERSION,
                              "bundleSourceRevision": server.source_revision,
                              "gatewayVersion": "8.3.8 (b2026071409)",
                              "mcpModuleVersion": "1.3.5-SNAPSHOT",
                              "compatibilityStatus": "UNKNOWN",
                          }}
            elif name == "tag_read":
                result = {"content": [{"type": "text", "text": "tag path is not valid"}], "isError": True}
            else:
                error = {"code": -32602, "message": f"unknown tool {name}"}
        else:
            error = {"code": -32601, "message": f"method not found: {method}"}
        response: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id}
        if error is not None:
            response["error"] = error
        else:
            response["result"] = result
        body = json.dumps(response).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Mcp-Session-Id", "rehearsal-session")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ThreadingServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class GatewayServer(ThreadingServer):
    def __init__(self, port: int, projects: dict[str, bytes]) -> None:
        super().__init__(("127.0.0.1", port), GatewayHandler)
        self.projects = projects
        self.imports: list[dict[str, Any]] = []
        self.openapi_document = {"openapi": "3.0.1", "paths": OPENAPI_PATHS}


class RuntimeMcpServer(ThreadingServer):
    def __init__(self, port: int, tools: list[str], resources: list[str]) -> None:
        super().__init__(("127.0.0.1", port), RuntimeMcpHandler)
        self.tools = tools
        self.resources = resources
        self.source_revision = SOURCE_REVISION


def run(command: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(command[:7]), "…")
    return subprocess.run(command, capture_output=True, text=True, cwd=str(ROOT), env=env)


def start_rest_server(env: dict[str, str], port: int, log: Path, procs: list[subprocess.Popen[str]]) -> None:
    handle = subprocess.Popen(
        ["uv", "run", "--locked", "--package", "ignition-rest-mcp", "ignition-rest-mcp"],
        env=env, stdout=log.open("w"), stderr=subprocess.STDOUT, cwd=str(ROOT), text=True,
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


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="g3-rehearsal-"))
    print(f"workspace: {work}")

    release = run(["uv", "run", "--no-sync", "python", "-m", "tooling.native.cli", "release",
                   "--project-dir", "packages/ignition-runtime-bundle/project",
                   "--out-dir", str(work / "release"), "--source-revision", SOURCE_REVISION,
                   "--evidence-dir", "tests/compatibility/evidence"], dict(os.environ))
    if release.returncode != 0:
        print(release.stdout[-4000:], release.stderr[-4000:])
        return 1
    manifest_path = work / "release" / f"ignition-runtime-bundle-{BUNDLE_VERSION}.manifest.json"
    zip_path = work / "release" / f"ignition-runtime-bundle-{BUNDLE_VERSION}.zip"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    jwt = run(["uv", "run", "--locked", "--package", "ignition-rest-mcp", "python", "-c",
               "import sys; sys.path.insert(0, 'tests/harness/phase3-live')\n"
               "from driver import mint_pair\n"
               "private, public = mint_pair()\n"
               "open(sys.argv[1], 'w').write(private)\n"
               "open(sys.argv[2], 'w').write(public)\n",
               str(work / "jwt-private.pem"), str(work / "jwt-public.pem")], dict(os.environ))
    if jwt.returncode != 0:
        print(jwt.stdout[-2000:], jwt.stderr[-2000:])
        return 1
    os.chmod(work / "jwt-private.pem", 0o600)

    gateway_port, runtime_port, rest_port = _free_port(), _free_port(), _free_port()
    gateway = GatewayServer(gateway_port, {
        "ignition_runtime": _zip_of(ROOT / "packages/ignition-runtime-bundle/project"),
        DISPOSABLE: _zip_of(ROOT / "tests/harness/phase3-live/fixture-project"),
    })
    threading.Thread(target=gateway.serve_forever, daemon=True).start()
    readonly = manifest["profileInventories"]["readonly"]
    runtime = RuntimeMcpServer(runtime_port, sorted(readonly["tools"]), list(readonly["resources"]))
    threading.Thread(target=runtime.serve_forever, daemon=True).start()
    print(f"fake gateway : http://127.0.0.1:{gateway_port}")
    print(f"fake runtime : http://127.0.0.1:{runtime_port}/data/mcp/phase3-runtime")
    print(f"ignition-rest: http://127.0.0.1:{rest_port}")

    (work / "data").mkdir(parents=True, exist_ok=True)
    (work / "ci-marker.json").write_text(json.dumps({
        "marker": "ignition-mcp-phase3-live", "environment": "phase3-live", "runId": RUN_ID,
        "gatewayVersion": "8.3.8", "gatewayBuild": "2026071409", "disposableProject": DISPOSABLE,
        "gatewayId": "rehearsal-local", "trustedRepo": "sheon-sek/ignition-mcp",
    }), encoding="utf-8")

    base_env = {
        **os.environ,
        "IGNITION_MCP_DATA_DIR": str(work / "data"),
        "IGNITION_MCP_GATEWAY_URL": f"http://127.0.0.1:{gateway_port}",
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
        start_rest_server({**base_env, "IGNITION_MCP_SENSITIVE_EXPORTS_ENABLED": "true"},
                          rest_port, work / "rest-gate-on.log", procs)
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
            ["uv", "run", "--locked", "--package", "ignition-rest-mcp", "python",
             "tests/harness/phase3-live/driver.py",
             "--ci-marker", str(work / "ci-marker.json"),
             "--release-manifest", str(manifest_path),
             "--release-zip", str(zip_path),
             "--jwt-private-key", str(work / "jwt-private.pem"),
             "--rest-url", f"http://127.0.0.1:{rest_port}",
             "--runtime-mcp-url", f"http://127.0.0.1:{runtime_port}/data/mcp/phase3-runtime",
             "--observations", str(work / "observations.json"),
             "--raw-dir", str(work / "raw"),
             "--expect-d27-tuple"],
            capture_output=True, text=True, cwd=str(ROOT), env=driver_env,
        )
        print(driver.stdout[-12000:])
        if driver.stderr.strip():
            print("--- driver stderr ---\n", driver.stderr[-4000:])
        if driver.returncode != 0:
            print(f"driver rc={driver.returncode}; server log tail:\n{(work / 'rest-gate-on.log').read_text()[-4000:]}")
            return 1

        for proc in procs:
            proc.terminate()
        for proc in procs:
            with contextlib.suppress(Exception):
                proc.wait(timeout=20)
        procs.clear()
        start_rest_server({**base_env, "IGNITION_MCP_SENSITIVE_EXPORTS_ENABLED": "false"},
                          rest_port, work / "rest-gate-off.log", procs)
        gateoff = run(["uv", "run", "--locked", "--package", "ignition-rest-mcp", "python",
                       "tests/harness/phase3-live/inventory_gate_off.py",
                       "--rest-url", f"http://127.0.0.1:{rest_port}",
                       "--jwt-private-key", str(work / "jwt-private.pem"),
                       "--jwt-issuer", JWT_ISSUER, "--jwt-audience", JWT_AUDIENCE,
                       "--output", str(work / "gate-off-inventory.json")], base_env)
        print(gateoff.stdout, gateoff.stderr)
        if gateoff.returncode != 0:
            return 1

        sha256 = hashlib.sha256(zip_path.read_bytes()).hexdigest()
        for version, build in (("8.3.8", "2026071409"), ("8.3.9", "2026082511")):
            identity = {
                "gatewayVersion": version, "gatewayBuild": build,
                "gatewayImage": f"inductiveautomation/ignition:{version}",
                "gatewayImageDigest": "sha256:" + "0" * 64,
                "mcpModuleVersion": "1.3.5-SNAPSHOT", "mcpModuleArtifactVersion": "1.3.5.2026021307-SNAPSHOT",
                "mcpModuleBuild": "2026021307",
                "mcpModuleSha256": "b1142a5796f2fd834555f13f03de706599d745f7172a68e54f2f7908b67fe365",
                "bundleVersion": BUNDLE_VERSION, "bundleSha256": sha256, "deployedBundleSha256": sha256,
                "sourceRevision": SOURCE_REVISION, "runId": RUN_ID,
            }
            (work / f"identity-{version}.json").write_text(json.dumps(identity), encoding="utf-8")
            generated = run(["uv", "run", "--no-sync", "python", "-m", "tooling.compat", "generate",
                             "--observations", str(work / "observations.json"),
                             "--identity", str(work / f"identity-{version}.json"),
                             "--gate-off-report", str(work / "gate-off-inventory.json"),
                             "--out-dir", str(work / f"evidence-{version}")], base_env)
            print(generated.stdout, generated.stderr)
            if generated.returncode != 0:
                return 1

        merged = work / "evidence-merged"
        merged.mkdir()
        with contextlib.suppress(FileExistsError):
            for version in ("8.3.8", "8.3.9"):
                for row_dir in (work / f"evidence-{version}").iterdir():
                    row_dir.rename(merged / row_dir.name)
        validated = run(["uv", "run", "--no-sync", "python", "-m", "tooling.compat", "validate",
                         "--evidence-dir", str(merged)], base_env)
        print(validated.stdout, validated.stderr)
        if validated.returncode != 0:
            return 1

        print(f"\nREHEARSAL PASSED; gateway imports observed: {gateway.imports}")
        print(f"workspace kept at {work}")
        return 0
    finally:
        for proc in procs:
            proc.terminate()
        for proc in procs:
            with contextlib.suppress(Exception):
                proc.wait(timeout=10)
        gateway.shutdown()
        runtime.shutdown()


if __name__ == "__main__":
    sys.exit(main())
