"""Phase 3 Slice 10: ``ignition-mcp setup-native doctor|plan|verify`` (D20, D21).

Deterministic by construction: both planes are ``httpx.MockTransport`` fakes, so
nothing here opens a socket.  The "no monorepo path dependency" proof against an
installed wheel lives in ``test_phase3_setup_native_wheel.py``.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import hashlib
import io
import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest

from ignition_rest_mcp.cli.setup_native import gateway, plan, verify
from ignition_rest_mcp.cli.setup_native import doctor
from ignition_rest_mcp.cli.setup_native import main as cli_main
from ignition_rest_mcp.cli.setup_native.inputs import Endpoint, Inputs, UsageError, load_inputs, validate_manifest
from ignition_rest_mcp.cli.setup_native.mcp_http import McpHttpClient, McpProbeError

BUNDLE_VERSION = "0.2.0"
SHA_A = "a" * 40
GATEWAY_TOKEN = "GWSENTINELc0ffee000111222333"
MCP_TOKEN = "MCPSENTINELdeadbeef444555666777"
TOOLS = ["alpha", "beta", "bundle_info"]
RESOURCE_URIS = ["ignition://?contracts/alpha-output"]
GATEWAY_ENDPOINT = Endpoint(url="http://127.0.0.1:8088", scheme="http", host="127.0.0.1", port=8088)
MCP_ENDPOINT = Endpoint(url="http://127.0.0.1:8000/mcp", scheme="http", host="127.0.0.1", port=8000)
DEAD_ENDPOINT = Endpoint(url="http://127.0.0.1:1", scheme="http", host="127.0.0.1", port=1)

OPENAPI_OPERATIONS: dict[str, tuple[str, str]] = {
    "server-config": ("get", gateway.SERVER_CONFIG_FIND_PATH),
    "project-import": ("post", gateway.PROJECT_IMPORT_PATH),
    "security-levels": ("get", gateway.SECURITY_LEVELS_PATH),
    "api-token": ("post", gateway.API_TOKEN_PATH),
    "designers": ("get", gateway.DESIGNERS_PATH),
    "projects-find": ("get", gateway.PROJECT_FIND_PATH),
}
ALL_CAPABILITIES = tuple(OPENAPI_OPERATIONS)


# ----------------------------------------------------------------------------- manifest


def make_manifest(**overrides: Any) -> dict[str, Any]:
    """A structurally valid bundle manifest with a three-Tool readonly inventory."""

    def profile(permissions: list[str], resources: list[str], prompts: list[str]) -> dict[str, Any]:
        return {"permissions": permissions, "tools": list(TOOLS), "resources": resources, "prompts": prompts}

    manifest: dict[str, Any] = {
        "schemaVersion": 1,
        "bundleVersion": BUNDLE_VERSION,
        "sourceRevision": SHA_A,
        "resourceSchemaVersion": 1,
        "nativeResponseBindingStatus": "VERIFIED_WITH_LIMITATION",
        "artifact": {
            "filename": f"ignition-runtime-bundle-{BUNDLE_VERSION}.zip",
            "sha256": hashlib.sha256(b"bundle-bytes").hexdigest(),
            "sizeBytes": 12,
        },
        "tools": list(TOOLS),
        "resources": list(RESOURCE_URIS),
        "prompts": [],
        "toolRequirements": {
            name: {"budgetClass": "FAST", "permissionClass": "READ",
                   "nativeRequirements": ["system.util.getVersion"]}
            for name in TOOLS
        },
        "profileInventories": {
            "readonly": profile(["READ"], list(RESOURCE_URIS), []),
            "operator": profile(["CONTROL", "READ"], [], []),
            "configurator": profile(["CONFIG", "READ"], [], []),
            "full": profile(["CONFIG", "CONTROL", "READ"], [], []),
        },
        "testedTuples": [],
    }
    manifest.update(overrides)
    return manifest


def with_profile(manifest: dict[str, Any], name: str, **inventory: Any) -> dict[str, Any]:
    entry = dict(manifest["profileInventories"][name])
    entry.update(inventory)
    profiles = dict(manifest["profileInventories"])
    profiles[name] = entry
    manifest["profileInventories"] = profiles
    return manifest


def evidence_row(**overrides: Any) -> dict[str, Any]:
    row = {
        "gate": "G3",
        "gatewayVersion": "8.3.8",
        "gatewayBuild": "2026071409",
        "mcpModuleVersion": "1.3.5-SNAPSHOT",
        "mcpModuleBuild": "2026021307",
        "mcpModuleSha256": "b" * 64,
        "bundleVersion": BUNDLE_VERSION,
        "compatibilityStatus": "UNTESTED",
        "nativeResponseBinding": "VERIFIED_WITH_LIMITATION",
    }
    row.update(overrides)
    return row


# --------------------------------------------------------------------------- fake planes


class _NoMethod(Exception):
    def __init__(self, method: str) -> None:
        super().__init__(method)
        self.method = method


class FakeGateway:
    """Read-only Gateway REST fake; every behaviour is an explicit constructor knob."""

    def __init__(
        self,
        *,
        project: str = "managed",
        marker_version: str = BUNDLE_VERSION,
        server_config: str = "present",
        module: str = "installed",
        capabilities: tuple[str, ...] = ALL_CAPABILITIES,
        ignition_version: str = "8.3.8 (b2026071409)",
        offline: bool = False,
    ) -> None:
        self.project = project
        self.marker_version = marker_version
        self.server_config = server_config
        self.module = module
        self.capabilities = capabilities
        self.ignition_version = ignition_version
        self.offline = offline
        self.requests: list[httpx.Request] = []

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def paths(self) -> list[str]:
        return [request.url.path for request in self.requests]

    def methods(self) -> list[str]:
        return [request.method for request in self.requests]

    def openapi(self) -> bytes:
        paths: dict[str, Any] = {}
        for capability in self.capabilities:
            method, template = OPENAPI_OPERATIONS[capability]
            paths.setdefault(template, {})[method] = {"summary": capability}
        paths.setdefault("/data/api/v1/ping", {})["get"] = {"summary": "unrelated operation"}
        return json.dumps({"openapi": "3.0.1", "paths": paths}, sort_keys=True).encode()

    def _project_body(self) -> bytes:
        marker = gateway.MANAGED_MARKER_PREFIX
        version = self.marker_version
        descriptions = {
            "managed": f"Runtime MCP Bundle.\n{marker}product=ignition-runtime-bundle; bundle={version}",
            "inheritable": f"Runtime MCP Bundle.\n{marker}product=ignition-runtime-bundle; bundle={version}",
            "foreign_product": f"Runtime MCP Bundle.\n{marker}product=some-other-bundle; bundle={version}",
            "malformed": f"Runtime MCP Bundle.\n{marker}product=ignition-runtime-bundle bundle={version}",
            "unmanaged": "Someone's hand-built project.",
            "empty_description": "",
        }
        body: dict[str, Any] = {
            "name": "ignition_runtime",
            "title": "Runtime MCP Bundle",
            "description": descriptions[self.project],
            "inheritable": self.project == "inheritable",
        }
        return json.dumps(body).encode()

    def _modules_body(self) -> bytes:
        items = {
            "installed": [{"id": gateway.MCP_MODULE_ID, "version": "1.3.5.2026021307-SNAPSHOT"}],
            "missing": [{"id": "com.other.module", "version": "1.0.0.2026021307"}],
            "unparseable": [{"id": gateway.MCP_MODULE_ID, "version": "1.3.5"}],
            "absent": [],
        }[self.module]
        return json.dumps({"total": len(items), "items": items}).encode()

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.offline:
            raise httpx.ConnectError("connection refused", request=request)
        assert request.headers["x-ignition-api-token"] == GATEWAY_TOKEN
        path = request.url.path
        if path == gateway.GATEWAY_INFO_PATH:
            return httpx.Response(200, json={"name": "ci-gateway", "edition": "standard",
                                             "ignitionVersion": self.ignition_version})
        if path == "/openapi.json":
            return httpx.Response(200, content=self.openapi(), headers={"content-type": "application/json"})
        if path == gateway.MODULES_PATH:
            assert request.url.params["limit"] == "500" and request.url.params["offset"] == "0"
            if self.module == "error":
                leaked = request.headers["x-ignition-api-token"]
                return httpx.Response(401, content=f"unauthorized for token {leaked}".encode())
            return httpx.Response(200, content=self._modules_body())
        if path.startswith("/data/api/v1/projects/find/"):
            if self.project == "absent":
                return httpx.Response(404, json={"message": "not found"})
            return httpx.Response(200, content=self._project_body())
        if path.startswith("/data/api/v1/resources/find/com.inductiveautomation.mcp/server-config/"):
            if self.server_config == "absent":
                return httpx.Response(404, json={"message": "not found"})
            if self.server_config == "error":
                return httpx.Response(500, content=b"backend failure while reading the configuration")
            return httpx.Response(200, json={"name": "production", "attributes": {"tools": TOOLS}})
        return httpx.Response(404, json={"message": f"unexpected probe path {path}"})


class FakeMcp:
    """MCP Streamable HTTP fake with a tunable inventory and JSON or SSE response forms."""

    def __init__(
        self,
        *,
        tools: list[str] | None = None,
        resources: list[str] | None = None,
        prompts: list[str] | None = None,
        advertise: tuple[str, ...] = ("tools", "resources"),
        bundle: dict[str, Any] | None = None,
        sse: bool = True,
        fail_tools_call: bool = False,
        empty_resource: bool = False,
        offline: bool = False,
        deny: bool = False,
        reject_initialize: bool = False,
        invalid_refusals: bool = False,
    ) -> None:
        self.tools = list(TOOLS) if tools is None else tools
        self.resources = list(RESOURCE_URIS) if resources is None else resources
        self.prompts = [] if prompts is None else prompts
        self.advertise = advertise
        self.bundle = {
            "bundleVersion": BUNDLE_VERSION,
            "bundleSourceRevision": SHA_A,
            "gatewayVersion": "8.3.8 (b2026071409)",
            "mcpModuleVersion": "1.3.5-SNAPSHOT",
            "mcpModuleBuild": "2026021307",
            "compatibilityStatus": "UNKNOWN",
        } if bundle is None else bundle
        self.sse = sse
        self.fail_tools_call = fail_tools_call
        self.empty_resource = empty_resource
        self.offline = offline
        self.deny = deny
        self.reject_initialize = reject_initialize
        # Live shape (G3 run 35588754132): the module refuses unimplemented list
        # capabilities with -32600 Invalid Request, not -32601.
        self.invalid_refusals = invalid_refusals
        self.session_id = "session-77"
        self.requests: list[httpx.Request] = []
        self.methods: list[str] = []

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def result(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "initialize":
            if self.reject_initialize:
                raise _NoMethod("initialize")
            return {
                "protocolVersion": "2025-06-18",
                "capabilities": {kind: {"listChanged": False} for kind in self.advertise},
                "serverInfo": {"name": "ignition-rest-mcp", "version": "0.1.0a0"},
            }
        if method == "tools/list":
            self._require("tools")
            return {"tools": [{"name": name, "description": f"{name} description"} for name in self.tools]}
        if method == "resources/list":
            self._require("resources")
            return {"resources": [{"uri": uri, "name": uri.rsplit("/", 1)[-1]} for uri in self.resources]}
        if method == "prompts/list":
            self._require("prompts")
            return {"prompts": [{"name": name, "description": name} for name in self.prompts]}
        if method == "resources/read":
            uri = params.get("uri")
            if "resources" not in self.advertise:
                raise _NoMethod("resources/read")
            if self.empty_resource or uri not in self.resources:
                return {"contents": []}
            return {"contents": [{"uri": uri, "mimeType": "application/json", "text": '{"schemaVersion":1}'}]}
        if method == "prompts/get":
            name = params.get("name")
            if "prompts" not in self.advertise or name not in self.prompts:
                raise _NoMethod("prompts/get")
            return {"description": "smoke",
                    "messages": [{"role": "user", "content": {"type": "text", "text": f"prompt {name}"}}]}
        if method == "tools/call":
            called = params.get("name")
            if called not in self.tools:
                raise _NoMethod(f"tools/call {called}")
            if self.fail_tools_call:
                return {"content": [{"type": "text", "text": "bundle is not deployed"}], "isError": True}
            return {"content": [{"type": "text", "text": "ok"}], "structuredContent": dict(self.bundle),
                    "isError": False}
        raise _NoMethod(method)

    def _require(self, capability: str) -> None:
        if capability not in self.advertise:
            raise _NoMethod(f"{capability}/list")

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.offline:
            raise httpx.ConnectError("connection refused", request=request)
        if self.deny:
            return httpx.Response(401, content=f"invalid credential {request.headers.get('authorization')}".encode())
        if request.method == "GET":
            return httpx.Response(405, content=b"method not allowed for reachability probes")
        assert "application/json" in request.headers["accept"]
        assert "text/event-stream" in request.headers["accept"]
        payload = json.loads(request.content)
        method = str(payload.get("method"))
        self.methods.append(method)
        headers = {"mcp-session-id": self.session_id}
        if method == "initialize":
            assert "mcp-session-id" not in request.headers
        elif request.headers.get("mcp-session-id") != self.session_id:
            return httpx.Response(400, content=b"unknown or missing Mcp-Session-Id", headers=headers)
        if method == "notifications/initialized":
            return httpx.Response(202, content=b"", headers=headers)
        try:
            body: dict[str, Any] = {"jsonrpc": "2.0", "id": payload.get("id"),
                                    "result": self.result(method, payload.get("params") or {})}
        except _NoMethod as error:
            code = -32600 if (self.invalid_refusals and error.method.endswith("/list")) else -32601
            body = {"jsonrpc": "2.0", "id": payload.get("id"),
                    "error": {"code": code, "message": f"refused: {error.method}"}}
        text = json.dumps(body)
        if self.sse:
            return httpx.Response(200, content=f"event: message\ndata: {text}\n\n".encode(),
                                  headers={**headers, "content-type": "text/event-stream"})
        return httpx.Response(200, content=text.encode(), headers={**headers, "content-type": "application/json"})


# ------------------------------------------------------------------------------ harness


def write_manifest(tmp_path: Path, manifest: dict[str, Any]) -> Path:
    path = tmp_path / "bundle.manifest.json"
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return path


def make_inputs(
    tmp_path: Path,
    manifest: dict[str, Any],
    *,
    command: str = "doctor",
    profile: str = "readonly",
    server_config_name: str | None = "production",
    as_json: bool = True,
    bundle_zip: Path | None = None,
) -> Inputs:
    return Inputs(
        command=command,
        manifest_path=write_manifest(tmp_path, manifest),
        manifest=manifest,
        gateway_url=GATEWAY_ENDPOINT,
        mcp_url=MCP_ENDPOINT,
        bundle_zip=bundle_zip,
        profile=profile,
        bundle_project="ignition_runtime",
        server_config_name=server_config_name,
        gateway_token=GATEWAY_TOKEN,
        mcp_token=MCP_TOKEN,
        timeout_seconds=5.0,
        allow_insecure_authorize=False,
        as_json=as_json,
    )


def checks_of(payload: dict[str, Any]) -> dict[str, dict[str, str]]:
    raw = payload.get("checks", payload.get("actions"))
    assert isinstance(raw, list)
    return {item["name"]: item for item in raw}


def actions_of(payload: dict[str, Any]) -> list[dict[str, str]]:
    raw = payload["actions"]
    assert isinstance(raw, list)
    return raw


class Runner:
    """Run one command against the fake planes; return its exit code plus report."""

    def __init__(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        self.tmp_path = tmp_path
        self.capsys = capsys

    def __call__(
        self,
        command: str,
        manifest: dict[str, Any],
        *,
        gateway_fake: FakeGateway | None = None,
        mcp_fake: FakeMcp | None = None,
        as_json: bool = True,
        **input_kwargs: Any,
    ) -> tuple[int, dict[str, Any], str]:
        inputs = make_inputs(self.tmp_path, manifest, command=command, as_json=as_json, **input_kwargs)
        function: Callable[..., Any] = {"doctor": doctor.run, "plan": plan.run, "verify": verify.run}[command]
        kwargs: dict[str, Any] = {}
        if gateway_fake is not None and command != "verify":
            kwargs["gateway_transport"] = gateway_fake.transport
        if mcp_fake is not None and command != "plan":
            kwargs["mcp_transport"] = mcp_fake.transport
        code = asyncio.run(function(inputs, **kwargs))
        captured = self.capsys.readouterr()
        text = captured.out
        payload = parse_report(text) if as_json else {}
        return code, payload, text


@pytest.fixture
def runner(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> Runner:
    capsys.readouterr()
    return Runner(tmp_path, capsys)


def parse_report(text: str) -> dict[str, Any]:
    """Parse the JSON report, tolerating the plan promise line that follows it."""

    lines = [line for line in text.splitlines() if not line.startswith(plan.PLAN_SENTINEL)]
    payload = json.loads("\n".join(lines))
    assert isinstance(payload, dict)
    return payload


def run_main(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli_main.main(argv)
    return code, out.getvalue(), err.getvalue()


def token_file(tmp_path: Path, name: str, *, mode: int = 0o600, body: str | None = None) -> Path:
    path = tmp_path / f"{name}.token"
    path.write_text(body if body is not None else f"{GATEWAY_TOKEN}\n", encoding="utf-8")
    path.chmod(mode)
    return path


# -------------------------------------------------------------------------------- doctor


def test_doctor_reports_every_check_in_order(runner: Runner) -> None:
    code, payload, _ = runner("doctor", make_manifest(), gateway_fake=FakeGateway(), mcp_fake=FakeMcp())
    checks = payload["checks"]
    assert code == 0
    assert [check["name"] for check in checks] == [
        "gateway-info",
        "openapi-sha256",
        "module-installed",
        "capabilities.server-config",
        "capabilities.project-import",
        "capabilities.security-levels",
        "capabilities.api-token",
        "capabilities.designers",
        "bundle-project",
        "server-config-presence",
        "mcp-initialize",
        "inventory-tools",
        "inventory-resources",
        "inventory-prompts",
        "bundle-info",
        "compatibility",
    ]
    statuses = checks_of(payload)
    assert statuses["inventory-prompts"]["status"] == "NOT_APPLICABLE"
    # Phase 3 manifests carry no SUPPORTED row, so a clean deployment is still UNTESTED.
    assert statuses["compatibility"]["status"] == "UNKNOWN"
    assert all(check["status"] in ("PASS", "NOT_APPLICABLE", "UNKNOWN") for check in checks)
    assert payload["exitCode"] == 0


def test_doctor_text_lines_are_aligned_and_summarised(runner: Runner) -> None:
    code, _, text = runner("doctor", make_manifest(), gateway_fake=FakeGateway(), mcp_fake=FakeMcp(), as_json=False)
    lines = text.splitlines()
    assert code == 0
    assert lines[0] == "PASS           gateway-info: ignitionVersion=8.3.8 (b2026071409) gateway=ci-gateway"
    assert any(line.startswith("NOT_APPLICABLE inventory-prompts:") for line in lines)
    assert lines[-1].startswith("doctor: 16 check(s)") and lines[-1].endswith("=> exit 0")


def test_doctor_only_issues_read_only_gateway_requests(runner: Runner) -> None:
    fake = FakeGateway()
    code, _, _ = runner("doctor", make_manifest(), gateway_fake=fake, mcp_fake=FakeMcp())
    assert code == 0
    assert set(fake.methods()) == {"GET"}
    assert fake.paths().count("/openapi.json") == 1
    assert not any("/import" in path or "/api-token" in path for path in fake.paths())


@pytest.mark.parametrize(
    ("project", "marker_version", "expected_status", "fragment"),
    [
        ("managed", BUNDLE_VERSION, "PASS", "MANAGED bundle=0.2.0; standalone"),
        ("managed", "0.1.0", "PASS", "MANAGED bundle=0.1.0 != manifest 0.2.0"),
        ("absent", BUNDLE_VERSION, "PASS", "ABSENT"),
        ("unmanaged", BUNDLE_VERSION, "FAIL", "UNMANAGED_SAME_NAME"),
        ("empty_description", BUNDLE_VERSION, "FAIL", "UNMANAGED_SAME_NAME"),
        ("malformed", BUNDLE_VERSION, "FAIL", "MARKER_INVALID"),
        ("foreign_product", BUNDLE_VERSION, "FAIL", "MARKER_INVALID"),
        ("inheritable", BUNDLE_VERSION, "FAIL", "NOT standalone"),
    ],
)
def test_doctor_classifies_every_project_branch(
    runner: Runner, project: str, marker_version: str, expected_status: str, fragment: str
) -> None:
    fake = FakeGateway(project=project, marker_version=marker_version)
    code, payload, _ = runner("doctor", make_manifest(), gateway_fake=fake, mcp_fake=FakeMcp())
    check = checks_of(payload)["bundle-project"]
    assert check["status"] == expected_status, check
    assert fragment in check["detail"]
    assert code == (0 if expected_status == "PASS" else 1)


def test_doctor_skips_project_when_the_find_route_is_absent(runner: Runner) -> None:
    caps = tuple(name for name in ALL_CAPABILITIES if name != "projects-find")
    _, payload, _ = runner("doctor", make_manifest(), gateway_fake=FakeGateway(capabilities=caps),
                           mcp_fake=FakeMcp())
    check = checks_of(payload)["bundle-project"]
    assert check["status"] == "SKIP"
    assert "/data/api/v1/projects/find/{name}" in check["detail"]


def test_doctor_server_config_presence_states(runner: Runner) -> None:
    for state, expected in (("present", "PASS"), ("absent", "FAIL"), ("error", "UNKNOWN")):
        _, payload, _ = runner("doctor", make_manifest(), gateway_fake=FakeGateway(server_config=state),
                               mcp_fake=FakeMcp())
        assert checks_of(payload)["server-config-presence"]["status"] == expected, state
    _, payload, _ = runner("doctor", make_manifest(), gateway_fake=FakeGateway(), mcp_fake=FakeMcp(),
                           server_config_name=None)
    assert checks_of(payload)["server-config-presence"]["status"] == "SKIP"
    caps = tuple(name for name in ALL_CAPABILITIES if name != "server-config")
    _, payload, _ = runner("doctor", make_manifest(), gateway_fake=FakeGateway(capabilities=caps),
                           mcp_fake=FakeMcp())
    assert checks_of(payload)["server-config-presence"]["status"] == "SKIP"


@pytest.mark.parametrize(
    "tools",
    [
        pytest.param(["alpha", "beta", "bundle_info", "sneaky"], id="superset"),
        pytest.param(["alpha", "bundle_info"], id="subset"),
        pytest.param(["beta", "bundle_info", "alpha"], id="reordered-exact"),
    ],
)
def test_doctor_tool_inventory_must_be_exact(runner: Runner, tools: list[str]) -> None:
    code, payload, _ = runner("doctor", make_manifest(), gateway_fake=FakeGateway(), mcp_fake=FakeMcp(tools=tools))
    check = checks_of(payload)["inventory-tools"]
    if sorted(tools) == sorted(TOOLS):
        assert check["status"] == "PASS" and code == 0
    else:
        assert check["status"] == "FAIL" and code == 1
        assert ("extra=" in check["detail"]) != ("missing=" in check["detail"]), check["detail"]


def test_doctor_inventory_drift_detail_stays_bounded(runner: Runner) -> None:
    extra = [f"extra_{index}" for index in range(12)]
    _, payload, _ = runner("doctor", make_manifest(), gateway_fake=FakeGateway(),
                           mcp_fake=FakeMcp(tools=[*TOOLS, *extra]))
    detail = checks_of(payload)["inventory-tools"]["detail"]
    assert "+4 more" in detail and len(detail) < 200


def test_doctor_missing_module_fails(runner: Runner) -> None:
    for module in ("missing", "absent"):
        code, payload, _ = runner("doctor", make_manifest(), gateway_fake=FakeGateway(module=module),
                                  mcp_fake=FakeMcp())
        check = checks_of(payload)["module-installed"]
        assert check["status"] == "FAIL", module
        assert "com.inductiveautomation.mcp" in check["detail"]
        assert code == 1


def test_doctor_survives_an_offline_gateway(runner: Runner) -> None:
    code, payload, _ = runner("doctor", make_manifest(), gateway_fake=FakeGateway(offline=True),
                              mcp_fake=FakeMcp())
    checks = checks_of(payload)
    assert code == 1
    assert checks["gateway-info"]["status"] == "FAIL"
    for name in ("openapi-sha256", "module-installed", "bundle-project", "server-config-presence",
                 "compatibility", "capabilities.api-token"):
        assert checks[name]["status"] == "SKIP", name
    # The other plane is independent: it is still probed.
    assert checks["mcp-initialize"]["status"] == "PASS"


def test_doctor_absent_capability_is_not_a_failure(runner: Runner) -> None:
    caps = tuple(name for name in ALL_CAPABILITIES if name != "project-import")
    code, payload, _ = runner("doctor", make_manifest(), gateway_fake=FakeGateway(capabilities=caps),
                              mcp_fake=FakeMcp())
    checks = checks_of(payload)
    assert checks["capabilities.project-import"]["status"] == "NOT_APPLICABLE"
    assert checks["capabilities.designers"]["status"] == "PASS"
    assert code == 0


def test_doctor_openapi_sha256_is_the_hash_of_the_served_document(runner: Runner) -> None:
    fake = FakeGateway()
    _, payload, _ = runner("doctor", make_manifest(), gateway_fake=fake, mcp_fake=FakeMcp())
    detail = checks_of(payload)["openapi-sha256"]["detail"]
    assert detail.startswith(hashlib.sha256(fake.openapi()).hexdigest())
    assert "documented operations" in detail


def test_doctor_unparseable_gateway_version_keeps_compatibility_unknown(runner: Runner) -> None:
    code, payload, _ = runner("doctor", make_manifest(), gateway_fake=FakeGateway(ignition_version="8.3.8"),
                              mcp_fake=FakeMcp())
    checks = checks_of(payload)
    assert checks["gateway-info"]["status"] == "PASS"
    assert "compatibility stays UNKNOWN" in checks["gateway-info"]["detail"]
    assert checks["compatibility"]["status"] == "UNKNOWN"
    assert "gatewayVersion" in checks["compatibility"]["detail"]
    assert code == 0


def test_doctor_compatibility_is_unknown_for_an_incomplete_identity(runner: Runner) -> None:
    _, payload, _ = runner("doctor", make_manifest(testedTuples=[evidence_row()]),
                           gateway_fake=FakeGateway(module="unparseable"), mcp_fake=FakeMcp())
    check = checks_of(payload)["compatibility"]
    assert check["status"] == "UNKNOWN"
    assert "mcpModuleBuild" in check["detail"]


def test_doctor_compatibility_is_untested_for_a_complete_unmatched_tuple(runner: Runner) -> None:
    _, payload, _ = runner("doctor", make_manifest(testedTuples=[evidence_row(gatewayVersion="8.1.0")]),
                           gateway_fake=FakeGateway(), mcp_fake=FakeMcp())
    check = checks_of(payload)["compatibility"]
    assert check["status"] == "UNKNOWN"
    assert "no testedTuples row" in check["detail"] and "=> UNTESTED" in check["detail"]


@pytest.mark.parametrize(
    ("recorded", "expected_status", "expected_code"),
    [("UNTESTED", "UNKNOWN", 0), ("INCOMPATIBLE", "FAIL", 1), ("SUPPORTED", "PASS", 0)],
)
def test_doctor_never_upgrades_or_downgrades_a_tested_tuple(
    runner: Runner, recorded: str, expected_status: str, expected_code: int
) -> None:
    manifest = make_manifest(testedTuples=[evidence_row(compatibilityStatus=recorded)])
    code, payload, _ = runner("doctor", manifest, gateway_fake=FakeGateway(), mcp_fake=FakeMcp())
    check = checks_of(payload)["compatibility"]
    assert check["status"] == expected_status
    assert f"=> {recorded}" in check["detail"]
    assert code == expected_code


def test_doctor_method_not_found_with_a_non_empty_expected_inventory_fails(runner: Runner) -> None:
    manifest = with_profile(make_manifest(), "operator", prompts=["standup"], resources=list(RESOURCE_URIS))
    code, payload, _ = runner("doctor", manifest, profile="operator", gateway_fake=FakeGateway(),
                              mcp_fake=FakeMcp(advertise=("tools", "resources")))
    checks = checks_of(payload)
    assert checks["inventory-prompts"]["status"] == "FAIL"
    assert "expects 1" in checks["inventory-prompts"]["detail"]
    assert checks["inventory-resources"]["status"] == "PASS"
    assert code == 1


def test_doctor_unadvertised_resources_capability_with_expected_resources_fails(runner: Runner) -> None:
    code, payload, _ = runner("doctor", make_manifest(), gateway_fake=FakeGateway(),
                              mcp_fake=FakeMcp(advertise=("tools",)))
    checks = checks_of(payload)
    assert checks["inventory-resources"]["status"] == "FAIL"
    assert checks["inventory-prompts"]["status"] == "NOT_APPLICABLE"
    assert code == 1


def test_doctor_bundle_info_must_match_the_manifest(runner: Runner) -> None:
    agreed = dict(FakeMcp().bundle)
    cases = [
        (agreed, make_manifest(), "PASS", "matches"),
        ({**agreed, "bundleVersion": "0.1.0"}, make_manifest(), "FAIL", "manifest declares"),
        ({**agreed, "bundleSourceRevision": "c" * 40}, make_manifest(), "FAIL", "!= manifest"),
        ({k: v for k, v in agreed.items() if k != "bundleSourceRevision"}, make_manifest(), "PASS", "legacy bundle"),
        ({k: v for k, v in agreed.items() if k != "bundleSourceRevision"}, make_manifest(sourceRevision="UNSTAMPED"),
         "PASS", "manifest is UNSTAMPED"),
    ]
    for bundle, manifest, expected, fragment in cases:
        code, payload, _ = runner("doctor", manifest, gateway_fake=FakeGateway(), mcp_fake=FakeMcp(bundle=bundle))
        check = checks_of(payload)["bundle-info"]
        assert check["status"] == expected, (bundle, manifest["sourceRevision"], check)
        assert fragment in check["detail"], check
        assert code == (0 if expected == "PASS" else 1)


def test_doctor_bundle_info_error_result_fails(runner: Runner) -> None:
    code, payload, _ = runner("doctor", make_manifest(), gateway_fake=FakeGateway(),
                              mcp_fake=FakeMcp(fail_tools_call=True))
    check = checks_of(payload)["bundle-info"]
    assert code == 1 and check["status"] == "FAIL"
    assert "error result" in check["detail"]


def test_doctor_mcp_session_failure_skips_mcp_checks(runner: Runner) -> None:
    code, payload, _ = runner("doctor", make_manifest(), gateway_fake=FakeGateway(),
                              mcp_fake=FakeMcp(reject_initialize=True))
    checks = checks_of(payload)
    assert code == 1
    assert checks["mcp-initialize"]["status"] == "FAIL"
    for name in ("inventory-tools", "inventory-resources", "inventory-prompts", "bundle-info"):
        assert checks[name]["status"] == "SKIP", name
    assert checks["compatibility"]["status"] == "UNKNOWN"


# --------------------------------------------------------------------------------- plan


def test_plan_creates_when_the_project_is_absent(runner: Runner) -> None:
    code, _, text = runner("plan", make_manifest(), gateway_fake=FakeGateway(project="absent"),
                           server_config_name=None, as_json=False)
    assert code == 0
    assert text.splitlines()[0] == (
        "NO CHANGE mcp-module com.inductiveautomation.mcp: detected version=1.3.5-SNAPSHOT build=2026021307"
    )
    assert "CREATE bundle-project ignition_runtime: deploy managed bundle 0.2.0" in text
    assert "SKIP server-config com.inductiveautomation.mcp: no --server-config-name supplied" in text
    assert "NO CHANGE security-level Gateway security level: detect-only, provisioning is apply-phase" in text
    assert "NO CHANGE runtime-token" in text
    assert "BLOCKED" not in text
    assert text.endswith(f"{plan.PLAN_SENTINEL}\n")


def test_plan_no_change_for_a_matching_managed_project(runner: Runner) -> None:
    code, _, text = runner("plan", make_manifest(), gateway_fake=FakeGateway(project="managed"), as_json=False)
    assert code == 0
    assert "NO CHANGE bundle-project ignition_runtime: managed bundle 0.2.0 already deployed" in text
    assert "NO CHANGE server-config production: explicit Tool inventory managed by apply" in text


@pytest.mark.parametrize("project", ["unmanaged", "malformed", "foreign_product"])
def test_plan_refuses_takeover_of_an_unmanaged_project(runner: Runner, project: str) -> None:
    code, _, text = runner("plan", make_manifest(), gateway_fake=FakeGateway(project=project), as_json=False)
    assert code == 3
    assert "BLOCKED bundle-project ignition_runtime: refuse takeover of unmanaged project ignition_runtime" in text
    assert text.endswith(f"{plan.PLAN_SENTINEL}\n")


def test_plan_blocks_an_inheritable_managed_project(runner: Runner) -> None:
    code, payload, _ = runner("plan", make_manifest(), gateway_fake=FakeGateway(project="inheritable"))
    assert code == 3
    action = next(item for item in actions_of(payload) if item["kind"] == "bundle-project")
    assert action["action"] == "BLOCKED" and "standalone" in action["reason"]


@pytest.mark.parametrize(
    ("installed", "target", "expected"),
    [
        ("0.1.0", "0.2.0", "minor"),
        ("0.2.0", "0.2.1", "patch"),
        ("0.1.9", "0.2.0", "minor"),
        ("0.2.0", "1.0.0", "major"),
        ("0.2.1", "0.2.0", "downgrade"),
        ("1.0.0", "0.9.9", "downgrade"),
        ("not-a-version", "0.2.0", "major"),
        (None, "0.2.0", "major"),
    ],
)
def test_plan_upgrade_classes(installed: str | None, target: str, expected: str) -> None:
    assert plan.upgrade_class(installed, target) == expected
    assert plan.needs_acknowledgement(expected) == (expected in ("major", "downgrade"))


def test_plan_update_line_carries_the_change_class(runner: Runner) -> None:
    patch_case = make_manifest(bundleVersion="0.2.1")
    _, _, text = runner("plan", patch_case, gateway_fake=FakeGateway(marker_version="0.2.0"), as_json=False)
    assert "UPDATE bundle-project ignition_runtime: redeploy managed bundle 0.2.0 -> 0.2.1 (patch)" in text

    major = make_manifest(bundleVersion="1.0.0")
    _, _, major_text = runner("plan", major, gateway_fake=FakeGateway(marker_version="0.2.0"), as_json=False)
    assert "redeploy managed bundle 0.2.0 -> 1.0.0 (major; requires explicit acknowledgement in apply)" in major_text
    assert major_text.endswith(f"{plan.PLAN_SENTINEL}\n")

    down = make_manifest(bundleVersion="0.1.0")
    _, _, down_text = runner("plan", down, gateway_fake=FakeGateway(marker_version="0.2.0"), as_json=False)
    assert "(downgrade; requires explicit acknowledgement in apply)" in down_text


def test_plan_blocked_on_missing_server_config_capability(runner: Runner) -> None:
    caps = tuple(name for name in ALL_CAPABILITIES if name != "server-config")
    code, _, text = runner("plan", make_manifest(), gateway_fake=FakeGateway(capabilities=caps), as_json=False)
    assert code == 3
    assert "BLOCKED server-config production: Gateway does not document the server-config resource" in text

    thin = tuple(name for name in ALL_CAPABILITIES if name in ("server-config", "projects-find"))
    code, _, text = runner("plan", make_manifest(), gateway_fake=FakeGateway(capabilities=thin), as_json=False)
    assert code == 0
    assert "SKIP security-level Gateway security level: Gateway does not document" in text
    assert "SKIP runtime-token" in text


def test_plan_creates_an_absent_server_config(runner: Runner) -> None:
    code, _, text = runner("plan", make_manifest(), gateway_fake=FakeGateway(server_config="absent"), as_json=False)
    assert code == 0
    assert "CREATE server-config production: explicit Tool inventory (never *)" in text


def test_plan_blocked_when_module_state_is_unreadable(runner: Runner) -> None:
    code, _, text = runner("plan", make_manifest(), gateway_fake=FakeGateway(module="error"), as_json=False)
    assert code == 3
    assert "BLOCKED mcp-module com.inductiveautomation.mcp: module state unknown" in text
    assert GATEWAY_TOKEN not in text


def test_plan_blocked_when_server_config_presence_is_unknown(runner: Runner) -> None:
    code, payload, _ = runner("plan", make_manifest(), gateway_fake=FakeGateway(server_config="error"))
    assert code == 3
    blocked = [action for action in actions_of(payload) if action["action"] == "BLOCKED"]
    assert [action["kind"] for action in blocked] == ["server-config"]
    assert "presence unknown" in blocked[0]["reason"]


def test_plan_blocked_when_the_project_state_is_unreadable(runner: Runner) -> None:
    caps = tuple(name for name in ALL_CAPABILITIES if name != "projects-find")
    code, payload, _ = runner("plan", make_manifest(), gateway_fake=FakeGateway(capabilities=caps))
    assert code == 3
    action = next(item for item in actions_of(payload) if item["kind"] == "bundle-project")
    assert action["action"] == "BLOCKED" and "ownership cannot be proven" in action["reason"]


def test_plan_transport_error_exits_one_and_still_promises_nothing(runner: Runner) -> None:
    code, _, text = runner("plan", make_manifest(), gateway_fake=FakeGateway(offline=True), as_json=False)
    assert code == 1
    assert "plan could not observe the Gateway" in text
    assert text.endswith(f"{plan.PLAN_SENTINEL}\n")


def test_plan_json_reports_applied_false(runner: Runner) -> None:
    code, payload, text = runner("plan", make_manifest(), gateway_fake=FakeGateway(project="unmanaged"))
    assert code == 3
    assert payload["applied"] is False
    assert payload["exitCode"] == 3
    assert {action["kind"] for action in actions_of(payload)} == {
        "mcp-module", "bundle-project", "server-config", "security-level", "runtime-token",
    }
    assert text.rstrip().endswith(plan.PLAN_SENTINEL)


def test_plan_json_carries_the_transport_error(runner: Runner) -> None:
    code, payload, _ = runner("plan", make_manifest(), gateway_fake=FakeGateway(offline=True))
    assert code == 1
    assert payload["actions"] == []
    assert payload["applied"] is False
    assert "connection refused" in payload["error"]


# ------------------------------------------------------------------------------- verify


def test_verify_green_sequence(runner: Runner) -> None:
    code, payload, _ = runner("verify", make_manifest(), mcp_fake=FakeMcp(advertise=("tools", "resources")))
    names = [check["name"] for check in payload["checks"]]
    assert code == 0 and payload["verified"] is True
    assert names == [
        "endpoint-reachable",
        "mcp-initialize",
        "inventory-tools",
        "inventory-resources",
        "inventory-prompts",
        f"resources-read {RESOURCE_URIS[0]}",
        "prompts-get",
        "bundle-info",
    ]


def test_verify_inventory_superset_and_subset_fail(runner: Runner) -> None:
    for tools in ([*TOOLS, "sneaky"], ["alpha"]):
        code, payload, _ = runner("verify", make_manifest(), mcp_fake=FakeMcp(tools=tools))
        check = checks_of(payload)["inventory-tools"]
        assert code == 1 and payload["verified"] is False
        assert check["status"] == "FAIL"
        assert ("extra=" in check["detail"]) != ("missing=" in check["detail"])


def test_verify_resource_read_smoke(runner: Runner) -> None:
    code, payload, _ = runner("verify", make_manifest(), mcp_fake=FakeMcp(empty_resource=True))
    check = checks_of(payload)[f"resources-read {RESOURCE_URIS[0]}"]
    assert code == 1 and check["status"] == "FAIL"
    assert "no content" in check["detail"]

    code, payload, _ = runner("verify", make_manifest(), mcp_fake=FakeMcp(resources=[]))
    checks = checks_of(payload)
    assert code == 1
    assert checks["inventory-resources"]["status"] == "FAIL"
    assert checks[f"resources-read {RESOURCE_URIS[0]}"]["status"] == "FAIL"


def test_verify_prompts_smoke_and_the_d28_lesson(runner: Runner) -> None:
    manifest = with_profile(make_manifest(), "full", resources=[], prompts=["standup"])
    code, payload, _ = runner("verify", manifest, profile="full",
                              mcp_fake=FakeMcp(resources=[], prompts=["standup"],
                                               advertise=("tools", "resources", "prompts")))
    checks = checks_of(payload)
    assert code == 0 and payload["verified"] is True
    assert checks["prompts-get standup"]["status"] == "PASS"
    assert checks["resources-read"]["status"] == "NOT_APPLICABLE"

    # An endpoint that advertises prompts while the profile declares none must not
    # be waved through as "not applicable" (the D28 lesson).
    code, payload, _ = runner("verify", make_manifest(),
                              mcp_fake=FakeMcp(advertise=("tools", "resources", "prompts")))
    check = checks_of(payload)["prompts-get"]
    assert code == 1 and check["status"] == "FAIL"
    assert "advertises prompts" in check["detail"]

    code, payload, _ = runner("verify", make_manifest(), mcp_fake=FakeMcp(advertise=("tools", "resources")))
    check = checks_of(payload)["prompts-get"]
    assert code == 0 and check["status"] == "NOT_APPLICABLE"


def test_verify_unreachable_endpoint_skips_everything(runner: Runner) -> None:
    code, payload, _ = runner("verify", make_manifest(), mcp_fake=FakeMcp(offline=True))
    checks = checks_of(payload)
    assert code == 1 and payload["verified"] is False
    assert checks["endpoint-reachable"]["status"] == "FAIL"
    assert all(check["status"] == "SKIP" for name, check in checks.items() if name != "endpoint-reachable")


def test_verify_denied_endpoint_fails_without_leaking_the_bearer(runner: Runner) -> None:
    code, payload, text = runner("verify", make_manifest(), mcp_fake=FakeMcp(deny=True))
    checks = checks_of(payload)
    assert code == 1
    assert checks["endpoint-reachable"]["status"] == "FAIL"
    assert checks["mcp-initialize"]["status"] == "SKIP"
    assert MCP_TOKEN not in text and "[redacted]" in text


def test_verify_initialize_failure_skips_the_rest(runner: Runner) -> None:
    code, payload, _ = runner("verify", make_manifest(), mcp_fake=FakeMcp(reject_initialize=True))
    checks = checks_of(payload)
    assert code == 1
    assert checks["endpoint-reachable"]["status"] == "PASS"
    assert checks["mcp-initialize"]["status"] == "FAIL"
    assert checks["bundle-info"]["status"] == "SKIP"


def test_verify_bundle_version_drift_fails(runner: Runner) -> None:
    code, payload, _ = runner("verify", make_manifest(), mcp_fake=FakeMcp(bundle={"bundleVersion": "0.0.9"}))
    assert code == 1
    assert checks_of(payload)["bundle-info"]["status"] == "FAIL"


# ------------------------------------------------------------------------ wire behaviours


def test_mcp_client_echoes_the_session_and_sends_the_bearer() -> None:
    fake = FakeMcp()

    async def exercise() -> list[str]:
        async with McpHttpClient(endpoint=MCP_ENDPOINT, token=MCP_TOKEN, timeout_seconds=5.0,
                                 transport=fake.transport) as client:
            await client.initialize()
            return await client.tools_list()

    assert asyncio.run(exercise()) == sorted(TOOLS)
    assert fake.requests[2].headers["mcp-session-id"] == fake.session_id
    assert fake.requests[2].headers["authorization"] == f"Bearer {MCP_TOKEN}"
    assert fake.methods[:2] == ["initialize", "notifications/initialized"]


API_TOKEN = "ignition-mcp-ci:zG48znDwfapnZCJA_d7THMrQJpejwONfXMFZ5oBYn0I"
JWT_SHAPE = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJnMyJ9.9qY3pk-from-another-key"


@pytest.mark.parametrize(
    ("token", "api_header_expected"),
    [(API_TOKEN, True), (JWT_SHAPE, False), (MCP_TOKEN, False)],
    ids=["ignition-api-token", "jwt", "opaque-mcp-token"],
)
def test_mcp_client_adds_the_gateway_api_token_header_only_for_api_token_shapes(
    token: str, api_header_expected: bool
) -> None:
    """The live Gateway module endpoint authenticates API tokens via
    X-Ignition-API-Token (G3 run 35588029382: 403 on bearer-only); JWTs and
    opaque tokens must stay pure bearer."""
    fake = FakeMcp()

    async def exercise() -> None:
        async with McpHttpClient(endpoint=MCP_ENDPOINT, token=token, timeout_seconds=5.0,
                                 transport=fake.transport) as client:
            await client.initialize()

    asyncio.run(exercise())
    request = fake.requests[0]
    assert request.headers["authorization"] == f"Bearer {token}"
    assert (("x-ignition-api-token" in request.headers) is api_header_expected)
    if api_header_expected:
        assert request.headers["x-ignition-api-token"] == token

@pytest.mark.parametrize("sse", [True, False], ids=["sse", "json"])
def test_mcp_client_accepts_both_response_forms(sse: bool) -> None:
    fake = FakeMcp(prompts=["standup"], advertise=("tools", "resources", "prompts"), sse=sse)

    async def exercise() -> tuple[dict[str, Any], str, dict[str, Any]]:
        async with McpHttpClient(endpoint=MCP_ENDPOINT, transport=fake.transport, timeout_seconds=5.0) as client:
            initialized = await client.initialize()
            text = await client.resource_read(RESOURCE_URIS[0])
            prompt = await client.prompt_get("standup")
            return initialized, text, prompt

    initialized, text, prompt = asyncio.run(exercise())
    assert initialized["protocolVersion"] == "2025-06-18"
    assert json.loads(text) == {"schemaVersion": 1}
    assert prompt["messages"][0]["role"] == "user"
    assert fake.advertise == ("tools", "resources", "prompts")


def test_mcp_client_bounds_the_response_body() -> None:
    async def exercise() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            body = b'{"jsonrpc":"2.0","id":1,"result":{"tools":[' + b"x" * 2_000_000 + b"]}}"
            return httpx.Response(200, content=body, headers={"content-type": "application/json"})

        async with McpHttpClient(endpoint=MCP_ENDPOINT, transport=httpx.MockTransport(handler),
                                 timeout_seconds=5.0) as client:
            with pytest.raises(McpProbeError, match="1048576"):
                await client.tools_list()

    asyncio.run(exercise())


def test_doctor_treats_invalid_request_list_refusal_as_absent_capability(runner: Runner) -> None:
    """Live module shape: unadvertised prompts/list answers -32600. With the
    capability unadvertised and the profile expecting none this is
    NOT_APPLICABLE (the G1 lesson), not a failure."""
    code, payload, _ = runner("doctor", make_manifest(), gateway_fake=FakeGateway(),
                              mcp_fake=FakeMcp(invalid_refusals=True))
    check = checks_of(payload)["inventory-prompts"]
    assert code == 0 and check["status"] == "NOT_APPLICABLE", check


def test_advertised_prompts_refused_with_invalid_request_still_fails(runner: Runner) -> None:
    """The -32600 leniency is scoped: an endpoint that advertises prompts but
    whose prompts/list is refused while the profile expects prompts must FAIL."""
    manifest = with_profile(make_manifest(), "operator", prompts=["standup"], resources=list(RESOURCE_URIS))
    code, payload, _ = runner("doctor", manifest, profile="operator", gateway_fake=FakeGateway(),
                              mcp_fake=FakeMcp(prompts=["standup"], advertise=("tools", "resources", "prompts"),
                                               invalid_refusals=False))
    assert code == 0 and checks_of(payload)["inventory-prompts"]["status"] == "PASS"
    # Now advertise prompts but refuse the list method with -32600 (invalid shape).
    class _AdvertisedButRefusing(FakeMcp):
        def result(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
            if method == "prompts/list":
                raise _NoMethod("prompts/list")
            return super().result(method, params)

    refusing = _AdvertisedButRefusing(prompts=["standup"], advertise=("tools", "resources", "prompts"),
                                      invalid_refusals=True)
    code, payload, _ = runner("doctor", manifest, profile="operator", gateway_fake=FakeGateway(),
                              mcp_fake=refusing)
    check = checks_of(payload)["inventory-prompts"]
    assert code == 1 and check["status"] == "FAIL", check


def test_mcp_client_separates_method_not_found_from_other_failures() -> None:
    fake = FakeMcp(advertise=("tools",))

    async def exercise() -> tuple[str, str]:
        async with McpHttpClient(endpoint=MCP_ENDPOINT, transport=fake.transport, timeout_seconds=5.0) as client:
            await client.initialize()
            try:
                await client.prompts_list()
            except McpProbeError as error:
                return type(error).__name__, str(error)
            return "no-error", ""

    name, message = asyncio.run(exercise())
    assert name == "McpMethodNotFound" and "prompts/list" in message


def test_mcp_client_follows_pagination_cursors() -> None:
    pages = [
        {"tools": [{"name": "alpha"}, {"name": "beta"}], "nextCursor": "page-2"},
        {"tools": [{"name": "bundle_info"}]},
    ]
    seen: list[Any] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        params = payload.get("params") or {}
        seen.append(params.get("cursor"))
        result = pages[0] if params.get("cursor") is None else pages[1]
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "result": result})

    async def exercise() -> list[str]:
        async with McpHttpClient(endpoint=MCP_ENDPOINT, transport=httpx.MockTransport(handler),
                                 timeout_seconds=5.0) as client:
            return await client.tools_list()

    assert asyncio.run(exercise()) == sorted(TOOLS)
    assert seen == [None, "page-2"]


def test_gateway_probe_error_redacts_the_service_token() -> None:
    fake = FakeGateway(module="error")

    async def exercise() -> str:
        async with gateway.GatewayRest(GATEWAY_ENDPOINT, GATEWAY_TOKEN, transport=fake.transport) as client:
            with pytest.raises(gateway.GatewayProbeError) as caught:
                await client.mcp_module()
            return str(caught.value)

    message = asyncio.run(exercise())
    assert GATEWAY_TOKEN not in message and "[redacted]" in message


def test_gateway_client_never_follows_redirects() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == gateway.GATEWAY_INFO_PATH:
            return httpx.Response(302, headers={"location": "http://elsewhere.test/gateway-info"})
        return httpx.Response(200, json={})

    async def exercise() -> str:
        async with gateway.GatewayRest(GATEWAY_ENDPOINT, GATEWAY_TOKEN,
                                       transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(gateway.GatewayProbeError) as caught:
                await client.gateway_info()
            return str(caught.value)

    assert "HTTP 302" in asyncio.run(exercise())


def test_gateway_client_bounds_json_responses() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b'{"paths":' + b"x" * 2_000_000 + b"}")

    async def exercise() -> str:
        async with gateway.GatewayRest(GATEWAY_ENDPOINT, GATEWAY_TOKEN,
                                       transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(gateway.GatewayProbeError) as caught:
                await client.gateway_info()
            return str(caught.value)

    assert "1048576" in asyncio.run(exercise())


# --------------------------------------------------------------------- inputs and CLI glue


def test_console_script_help_documents_the_exit_codes() -> None:
    code, out, err = run_main(["setup-native", "doctor", "--help"])
    assert code == 0 and err == ""
    assert "exit codes:" in out
    assert "0  command completed with no FAIL" in out
    assert "3  plan reports at least one BLOCKED action" in out
    assert "--bundle-manifest" in out and "--server-config-name" in out
    assert "IGNITION_MCP_SETUP_GATEWAY_TOKEN" in out


def test_group_help_offers_exactly_three_subcommands() -> None:
    code, out, _ = run_main(["setup-native", "--help"])
    assert code == 0
    assert "{doctor,plan,verify}" in out
    assert "\n  apply" not in out
    assert "install-module" in out and "(Phase 6)" in out


def test_bare_invocation_and_unknown_group_are_usage_errors() -> None:
    code, _, err = run_main([])
    assert code == 2 and "a command is required" in err
    code, _, err = run_main(["other-group", "--bundle-manifest", "x"])
    assert code == 2 and "unknown command group" in err and "deliberately absent" in err


@pytest.mark.parametrize("command", ["apply", "install-module"])
def test_mutation_commands_are_absent_with_an_explained_refusal(command: str) -> None:
    code, _, err = run_main(["setup-native", command, "--bundle-manifest", "x"])
    assert code == 2
    assert "not implemented" in err and "Phase 4" in err


def test_unknown_subcommand_is_a_usage_error(tmp_path: Path) -> None:
    code, _, err = run_main(["setup-native", "detect", "--bundle-manifest",
                             str(write_manifest(tmp_path, make_manifest()))])
    assert code == 2 and "expected one of doctor, plan, verify" in err


def test_missing_manifest_file_is_a_usage_error(tmp_path: Path) -> None:
    code, _, err = run_main(["setup-native", "doctor", "--bundle-manifest", str(tmp_path / "none.json")])
    assert code == 2 and "--bundle-manifest" in err


def test_broken_manifest_is_a_usage_error(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text('{"schemaVersion": 1}', encoding="utf-8")
    code, _, err = run_main(["setup-native", "plan", "--bundle-manifest", str(path),
                             "--gateway-url", "http://127.0.0.1:8088",
                             "--gateway-token-file", str(token_file(tmp_path, "gw"))])
    assert code == 2 and "manifest rejected" in err


def test_manifest_that_is_not_json_is_a_usage_error(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("not json at all", encoding="utf-8")
    code, _, err = run_main(["setup-native", "plan", "--bundle-manifest", str(path),
                             "--gateway-url", "http://127.0.0.1:8088",
                             "--gateway-token-file", str(token_file(tmp_path, "gw"))])
    assert code == 2 and "not valid JSON" in err


@pytest.mark.parametrize(
    "mutate",
    [
        lambda m: m.pop("testedTuples"),
        lambda m: m.update(bundleVersion="0.2"),
        lambda m: m.update(sourceRevision="deadbeef"),
        lambda m: m["artifact"].update(sha256="nope"),
        lambda m: m.update(tools=["beta", "alpha", "bundle_info"]),
        lambda m: m["profileInventories"]["readonly"].pop("prompts"),
        lambda m: m["profileInventories"]["full"].update(tools=["nope"]),
        lambda m: m.update(testedTuples=[{"gate": "G3"}]),
        lambda m: m.update(testedTuples=[evidence_row(compatibilityStatus="MAYBE")]),
        lambda m: m["toolRequirements"].pop("alpha"),
    ],
)
def test_manifest_rejections(mutate: Callable[[dict[str, Any]], Any]) -> None:
    manifest = make_manifest()
    mutate(manifest)
    with pytest.raises(UsageError):
        validate_manifest(manifest)


def test_a_valid_manifest_survives_validation() -> None:
    assert validate_manifest(make_manifest(testedTuples=[evidence_row()]))["bundleVersion"] == BUNDLE_VERSION


def test_token_file_mode_is_enforced(tmp_path: Path) -> None:
    loose = token_file(tmp_path, "loose", mode=0o640)
    code, _, err = run_main(["setup-native", "plan", "--bundle-manifest",
                             str(write_manifest(tmp_path, make_manifest())),
                             "--gateway-url", "http://127.0.0.1:8088", "--gateway-token-file", str(loose)])
    assert code == 2
    assert "group or others" in err and "0600" in err and GATEWAY_TOKEN not in err


def test_token_file_rejects_extra_lines_and_symlinks(tmp_path: Path) -> None:
    base = ["--bundle-manifest", str(write_manifest(tmp_path, make_manifest())),
            "--gateway-url", "http://127.0.0.1:8088"]
    two_lines = token_file(tmp_path, "two", body=f"{GATEWAY_TOKEN}\nsecond line\n")
    code, _, err = run_main(["setup-native", "plan", *base, "--gateway-token-file", str(two_lines)])
    assert code == 2 and "exactly one non-empty line" in err

    link = tmp_path / "link.token"
    link.symlink_to(two_lines)
    code, _, err = run_main(["setup-native", "plan", *base, "--gateway-token-file", str(link)])
    assert code == 2 and "symlink" in err


def test_a_blank_token_file_is_rejected(tmp_path: Path) -> None:
    empty = token_file(tmp_path, "empty", body="\n\n")
    with pytest.raises(UsageError, match="exactly one non-empty line"):
        load_inputs(["--bundle-manifest", str(write_manifest(tmp_path, make_manifest())),
                     "--gateway-url", "http://127.0.0.1:8088", "--gateway-token-file", str(empty)], "plan")


def test_bundle_zip_hash_must_match_the_manifest(tmp_path: Path) -> None:
    artifact = tmp_path / "ignition-runtime-bundle-0.2.0.zip"
    artifact.write_bytes(b"bundle-bytes")
    base = ["--bundle-manifest", str(write_manifest(tmp_path, make_manifest())),
            "--bundle-zip", str(artifact), "--gateway-url", "http://127.0.0.1:1",
            "--gateway-token-file", str(token_file(tmp_path, "gw"))]
    code, _, err = run_main(["setup-native", "plan", *base])
    assert code in (0, 1, 3) and "SHA-256" not in err

    artifact.write_bytes(b"tampered bundle bytes")
    code, _, err = run_main(["setup-native", "plan", *base])
    assert code == 2 and "SHA-256 mismatch" in err
    assert hashlib.sha256(b"tampered bundle bytes").hexdigest() in err
    assert hashlib.sha256(b"bundle-bytes").hexdigest() in err


def test_urls_and_tokens_come_from_the_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IGNITION_MCP_SETUP_GATEWAY_URL", "http://127.0.0.1:18088/")
    monkeypatch.setenv("IGNITION_MCP_SETUP_MCP_URL", "http://127.0.0.1:18000/mcp")
    monkeypatch.setenv("IGNITION_MCP_SETUP_GATEWAY_TOKEN", GATEWAY_TOKEN)
    monkeypatch.setenv("IGNITION_MCP_SETUP_MCP_TOKEN", MCP_TOKEN)
    inputs = load_inputs(["--bundle-manifest", str(write_manifest(tmp_path, make_manifest()))], "plan")
    assert inputs.gateway_url.url == "http://127.0.0.1:18088"
    assert inputs.mcp_url is not None and inputs.mcp_url.url == "http://127.0.0.1:18000/mcp"
    assert inputs.gateway_token == GATEWAY_TOKEN and inputs.mcp_token == MCP_TOKEN
    assert inputs.profile == "readonly" and inputs.bundle_project == "ignition_runtime"
    assert inputs.bundle_zip is None and inputs.server_config_name is None


def test_doctor_and_verify_require_the_mcp_url_but_plan_does_not(tmp_path: Path,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("IGNITION_MCP_SETUP_MCP_URL", raising=False)
    base = ["--bundle-manifest", str(write_manifest(tmp_path, make_manifest())),
            "--gateway-url", "http://127.0.0.1:1", "--gateway-token-file", str(token_file(tmp_path, "gw"))]
    assert run_main(["setup-native", "doctor", *base])[0] == 2
    assert run_main(["setup-native", "verify", *base])[0] == 2
    assert run_main(["setup-native", "plan", *base])[0] == 1


def test_urls_may_not_carry_credentials(tmp_path: Path) -> None:
    with pytest.raises(UsageError, match="credentials in the URL"):
        load_inputs(["--bundle-manifest", str(write_manifest(tmp_path, make_manifest())),
                     "--gateway-url", "http://user:pass@127.0.0.1:8088",
                     "--gateway-token-file", str(token_file(tmp_path, "gw"))], "plan")


def test_a_gateway_url_is_always_required(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("IGNITION_MCP_SETUP_GATEWAY_URL", raising=False)
    with pytest.raises(UsageError, match="gateway-url"):
        load_inputs(["--bundle-manifest", str(write_manifest(tmp_path, make_manifest())),
                     "--mcp-url", "http://127.0.0.1:8000/mcp", "--gateway-token-file",
                     str(token_file(tmp_path, "gw"))], "plan")


def test_profile_must_be_a_known_one(tmp_path: Path) -> None:
    with pytest.raises(UsageError):
        load_inputs(["--bundle-manifest", str(write_manifest(tmp_path, make_manifest())),
                     "--gateway-url", "http://127.0.0.1:8088", "--profile", "sudo",
                     "--gateway-token-file", str(token_file(tmp_path, "gw"))], "plan")


def test_names_are_bounded(tmp_path: Path) -> None:
    base = ["--bundle-manifest", str(write_manifest(tmp_path, make_manifest())),
            "--gateway-url", "http://127.0.0.1:8088",
            "--gateway-token-file", str(token_file(tmp_path, "gw"))]
    with pytest.raises(UsageError, match="bundle-project"):
        load_inputs([*base, "--bundle-project", "../etc/passwd"], "plan")
    with pytest.raises(UsageError, match="server-config-name"):
        load_inputs([*base, "--server-config-name", "has space"], "plan")


def test_timeout_is_bounded(tmp_path: Path) -> None:
    base = ["--bundle-manifest", str(write_manifest(tmp_path, make_manifest())),
            "--gateway-url", "http://127.0.0.1:8088",
            "--gateway-token-file", str(token_file(tmp_path, "gw"))]
    with pytest.raises(UsageError, match="timeout-seconds"):
        load_inputs([*base, "--timeout-seconds", "0"], "plan")
    with pytest.raises(UsageError, match="timeout-seconds"):
        load_inputs([*base, "--timeout-seconds", "600"], "plan")


def test_plaintext_token_to_a_remote_gateway_is_refused(tmp_path: Path) -> None:
    inputs = make_inputs(tmp_path, make_manifest())
    remote = replace(inputs, gateway_url=Endpoint(url="http://10.0.0.9:8088", scheme="http", host="10.0.0.9",
                                                  port=8088))
    with pytest.raises(UsageError, match="allow-insecure-authorize"):
        doctor.make_gateway(remote)
    allowed = replace(remote, allow_insecure_authorize=True)
    assert doctor.make_gateway(allowed).endpoint == DEAD_ENDPOINT.__class__(
        url="http://10.0.0.9:8088", scheme="http", host="10.0.0.9", port=8088)


@pytest.mark.parametrize("command", ["doctor", "plan", "verify"])
def test_secrets_never_reach_a_report(runner: Runner, command: str) -> None:
    """Both credential planes fail loudly, but never verbatim (D20 reporting rule)."""

    code, _, text = runner(command, make_manifest(), gateway_fake=FakeGateway(module="error"),
                           mcp_fake=FakeMcp(deny=True), server_config_name=None)
    assert code in (1, 3), command
    assert GATEWAY_TOKEN not in text, command
    assert MCP_TOKEN not in text, command
    assert "[redacted]" in text, command


def test_unexpected_failure_reports_only_the_exception_type(tmp_path: Path,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    async def explode(inputs: Inputs) -> int:
        raise RuntimeError(f"gateway said {GATEWAY_TOKEN}")

    monkeypatch.setitem(cli_main._COMMANDS, "doctor", explode)
    code, out, err = run_main(["setup-native", "doctor",
                               "--bundle-manifest", str(write_manifest(tmp_path, make_manifest())),
                               "--gateway-url", "http://127.0.0.1:8088", "--mcp-url", "http://127.0.0.1:8000/mcp",
                               "--gateway-token-file", str(token_file(tmp_path, "gw"))])
    assert code == 1 and out == ""
    assert "RuntimeError" in err and GATEWAY_TOKEN not in err


def test_keyboard_interrupt_exits_two(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def interrupted(argv: Any, command: str) -> Inputs:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_main, "load_inputs", interrupted)
    code, _, _ = run_main(["setup-native", "doctor", "--bundle-manifest",
                           str(write_manifest(tmp_path, make_manifest()))])
    assert code == 2


ALLOWED_IMPORT_ROOTS = frozenset(
    {
        "__future__", "argparse", "asyncio", "collections", "dataclasses", "hashlib", "httpx",
        "ipaddress", "json", "os", "pathlib", "re", "stat", "sys", "typing", "urllib",
    }
)


@pytest.mark.parametrize("module", sorted(Path(doctor.__file__).parent.glob("*.py")))
def test_the_cli_package_stays_code_separated(module: Path) -> None:
    """D25 plus the no-repo-path rule: only the CLI package, httpx and the stdlib."""

    tree = ast.parse(module.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(str(alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(str(node.module))
    foreign = {name for name in imported if name.split(".")[0] not in ALLOWED_IMPORT_ROOTS}
    own_package = {name for name in foreign if name.startswith("ignition_rest_mcp.cli")}
    assert foreign == own_package, (module.name, sorted(foreign - own_package))
    assert "subprocess" not in imported, module
    assert not any(name.startswith(("tooling", "tests", "ignition_rest_mcp.server")) for name in imported)
