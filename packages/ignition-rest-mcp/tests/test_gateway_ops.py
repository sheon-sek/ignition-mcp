"""Unit tests for the ``ignition_rest_mcp.cli.gateway_ops`` helpers (issue #77).

These are the unit-level pieces the CLI's Gateway half is built on:
the Gateway REST probes, the Runtime MCP client, the security helpers, the
``.modl`` reader and the guarded writer. Nothing here drives a command or reads a
manifest; the surviving CLI commands are covered by the ``test_phase7_*`` files.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import sys
import zipfile
from pathlib import Path
from typing import Any

import httpx
import pytest

from ignition_rest_mcp.cli.gateway_ops import documents, gateway, install_module, policy, security, writer
from ignition_rest_mcp.cli.gateway_ops.action import needs_acknowledgement, upgrade_class
from ignition_rest_mcp.cli.gateway_ops.inputs import Endpoint, Inputs, ModuleInputs, UsageError
from ignition_rest_mcp.cli.gateway_ops.mcp_http import McpHttpClient, McpProbeError

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tests/harness"))

from recorded_gateway import GENERATED_API_TOKEN_HASH, GENERATED_API_TOKEN_KEY  # noqa: E402

BUNDLE_VERSION = "0.2.0"
GATEWAY_TOKEN = "GWSENTINELc0ffee000111222333"
MCP_TOKEN = "MCPSENTINELdeadbeef444555666777"
TOOLS = ["alpha", "beta", "bundle_info"]
RESOURCE_URIS = ["ignition://?contracts/alpha-output"]
GATEWAY_ENDPOINT = Endpoint(url="http://127.0.0.1:8088", scheme="http", host="127.0.0.1", port=8088)
MCP_ENDPOINT = Endpoint(url="http://127.0.0.1:8000/mcp", scheme="http", host="127.0.0.1", port=8000)
DEAD_ENDPOINT = Endpoint(url="http://127.0.0.1:1", scheme="http", host="127.0.0.1", port=1)
PROJECT = "ignition_runtime"
SERVER_CONFIG = "phase4-credentials"
PROFILE = "readonly"
LEVEL = "IgnitionMcpRuntimeReadonly"
SECRET = f"{SERVER_CONFIG}:{GENERATED_API_TOKEN_KEY}"
PERMISSIONS = {
    "type": "AllOf",
    "securityLevels": [{"name": "Authenticated", "children": [{"name": "IgnitionMcpCi", "children": []}]}],
}
API_TOKEN = "ignition-mcp-ci:zG48znDwfapnZCJA_d7THMrQJpejwONfXMFZ5oBYn0I"
JWT_SHAPE = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJnMyJ9.9qY3pk-from-another-key"
MODULE_ID = gateway.MCP_MODULE_ID
FILE_VERSION = "1.3.5.2026021307-SNAPSHOT"
FILE_BUILD = "2026021307"
OLDER_BUILD = "2025010101"
NEWER_BUILD = "2027010101"


# --------------------------------------------------------------------------- fakes


class _NoMethod(Exception):
    def __init__(self, method: str) -> None:
        super().__init__(method)
        self.method = method


class FakeMcp:
    """MCP Streamable HTTP fake: a tunable inventory and JSON or SSE response forms."""

    def __init__(
        self,
        *,
        prompts: list[str] | None = None,
        advertise: tuple[str, ...] = ("tools", "resources"),
        sse: bool = True,
    ) -> None:
        self.tools = list(TOOLS)
        self.resources = list(RESOURCE_URIS)
        self.prompts = [] if prompts is None else prompts
        self.advertise = advertise
        self.sse = sse
        self.session_id = "session-77"
        self.requests: list[httpx.Request] = []
        self.methods: list[str] = []

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def result(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "initialize":
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
            if "resources" not in self.advertise or uri not in self.resources:
                raise _NoMethod("resources/read")
            return {"contents": [{"uri": uri, "mimeType": "application/json", "text": '{"schemaVersion":1}'}]}
        if method == "prompts/get":
            name = params.get("name")
            if "prompts" not in self.advertise or name not in self.prompts:
                raise _NoMethod("prompts/get")
            return {"description": "smoke",
                    "messages": [{"role": "user", "content": {"type": "text", "text": f"prompt {name}"}}]}
        raise _NoMethod(method)

    def _require(self, capability: str) -> None:
        if capability not in self.advertise:
            raise _NoMethod(f"{capability}/list")

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
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
            body = {"jsonrpc": "2.0", "id": payload.get("id"),
                    "error": {"code": -32601, "message": f"refused: {error.method}"}}
        text = json.dumps(body)
        if self.sse:
            return httpx.Response(200, content=f"event: message\ndata: {text}\n\n".encode(),
                                  headers={**headers, "content-type": "text/event-stream"})
        return httpx.Response(200, content=text.encode(), headers={**headers, "content-type": "application/json"})


# ------------------------------------------------------------------------ helpers


def make_inputs(**overrides: Any) -> Inputs:
    values: dict[str, Any] = {
        "command": "status",
        "manifest_path": Path("bundle.manifest.json"),
        "manifest": {"bundleVersion": BUNDLE_VERSION},
        "gateway_url": GATEWAY_ENDPOINT,
        "mcp_url": MCP_ENDPOINT,
        "bundle_zip": None,
        "profile": PROFILE,
        "bundle_project": PROJECT,
        "server_config_name": SERVER_CONFIG,
        "gateway_token": GATEWAY_TOKEN,
        "mcp_token": MCP_TOKEN,
        "timeout_seconds": 5.0,
        "allow_insecure_authorize": False,
        "as_json": True,
    }
    values.update(overrides)
    return Inputs(**values)


def modl(version: str = FILE_VERSION, module_id: str = MODULE_ID) -> bytes:
    """One ``.modl`` shaped like the real artifact: a ZIP holding ``module.xml``."""

    document = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
        f"<modules><module><name>MCP</name><id>{module_id}</id><version>{version}</version>"
        "</module></modules>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("module.xml", document)
        archive.writestr("mcp-gateway.jar", b"jar bytes")
    return buffer.getvalue()


def module_inputs(tmp_path: Path, payload: bytes | None = None, **overrides: Any) -> ModuleInputs:
    """One valid ``ModuleInputs`` over a written ``.modl``."""

    data = modl() if payload is None else payload
    path = tmp_path / "mcp-module.modl"
    path.write_bytes(data)
    values: dict[str, Any] = {
        "command": "setup",
        "module_file": path,
        "upload_name": "mcp-module.modl",
        "sha256": hashlib.sha256(data).hexdigest(),
        "gateway_url": GATEWAY_ENDPOINT,
        "gateway_token": GATEWAY_TOKEN,
        "timeout_seconds": 5.0,
        "allow_insecure_authorize": False,
        "as_json": True,
        "accept_certificate": False,
        "accept_eula": False,
        "acknowledge_upgrade": False,
        "restart": False,
    }
    values.update(overrides)
    return ModuleInputs(**values)


# ------------------------------------------------------------------- mcp client


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


@pytest.mark.parametrize(("status", "reachable"), [(415, True), (405, True), (500, False)])
def test_mcp_client_reachability_accepts_content_negotiation_refusals(status: int, reachable: bool) -> None:
    """The live module answers the GET probe with 415 Unsupported Media Type;
    negotiation/method refusals prove a listener, a server error does not."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=b"nope")

    async def exercise() -> str:
        async with McpHttpClient(endpoint=MCP_ENDPOINT, transport=httpx.MockTransport(handler),
                                 timeout_seconds=5.0) as client:
            return await client.reachability()

    if reachable:
        assert asyncio.run(exercise()) == f"HTTP {status}"
    else:
        with pytest.raises(McpProbeError, match=str(status)):
            asyncio.run(exercise())


# --------------------------------------------------------------- gateway probes


def test_gateway_probe_error_redacts_the_service_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        leaked = request.headers["x-ignition-api-token"]
        return httpx.Response(401, content=f"unauthorized for token {leaked}".encode())

    async def exercise() -> str:
        async with gateway.GatewayRest(GATEWAY_ENDPOINT, GATEWAY_TOKEN,
                                       transport=httpx.MockTransport(handler)) as client:
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


# ------------------------------------------------------------- install_module


def test_read_artifact_refuses_a_hash_mismatch(tmp_path: Path) -> None:
    with pytest.raises(UsageError, match="SHA-256 mismatch"):
        install_module.read_artifact(module_inputs(tmp_path, sha256="0" * 64))


def test_read_artifact_refuses_a_foreign_module_id(tmp_path: Path) -> None:
    with pytest.raises(UsageError, match="installs the"):
        install_module.read_artifact(module_inputs(tmp_path, payload=modl(module_id="com.example.other")))


def test_read_artifact_refuses_a_file_past_the_read_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bound is enforced by the read itself, not by a size check that came before it."""

    monkeypatch.setattr(install_module, "MAX_MODULE_BYTES", 2 * install_module.MODULE_READ_BLOCK_BYTES)
    data = modl() + b"x" * (4 * install_module.MODULE_READ_BLOCK_BYTES)

    with pytest.raises(UsageError, match="passes the .* byte bound"):
        install_module.read_artifact(module_inputs(tmp_path, payload=data))


@pytest.mark.parametrize(
    ("installed_build", "expected"),
    [
        (None, install_module.INSTALL),
        (FILE_BUILD, install_module.NO_CHANGE),
        (OLDER_BUILD, install_module.UPGRADE),
        (NEWER_BUILD, install_module.REFUSED),
    ],
    ids=["absent", "same", "higher", "lower"],
)
def test_classify_build_names_every_build_relation(
    tmp_path: Path, installed_build: str | None, expected: str
) -> None:
    artifact = install_module.read_artifact(module_inputs(tmp_path))
    installed = None if installed_build is None else gateway.ModuleIdentity(
        raw_version=FILE_VERSION, version="1.3.5-SNAPSHOT", build=installed_build,
    )
    assert install_module.classify_build(installed, artifact) == expected


@pytest.mark.parametrize(
    ("state", "on_startup", "fault_cause", "expected"),
    [
        (None, None, None, ""),
        ("ACTIVE", "true", None, ""),
        ("inactive", None, None, "state inactive"),
        ("INACTIVE", "disabled", None, "state INACTIVE, onStartup disabled"),
        ("FAULTED", None, "MissingDependency", "state FAULTED, fault cause MissingDependency"),
    ],
    ids=["no-state", "active", "inactive", "inactive-disabled-startup", "faulted"],
)
def test_the_module_state_problem_names_what_the_listing_reports(
    state: str | None, on_startup: str | None, fault_cause: str | None, expected: str
) -> None:
    """Issue #81: only an ACTIVE Module serves the routes setup writes through."""

    identity = gateway.ModuleIdentity(
        raw_version=FILE_VERSION, version="1.3.5-SNAPSHOT", build=FILE_BUILD,
        state=state, on_startup=on_startup, fault_cause=fault_cause,
    )
    assert install_module.state_problem(identity) == expected


def test_the_module_read_keeps_the_state_the_listing_reports() -> None:
    """The entry carries the state beside the version, and the read keeps it."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "items": [{
                "id": MODULE_ID,
                "version": "1.3.5-SNAPSHOT (b2026021307)",
                "state": "INACTIVE",
                "onStartup": "disabled",
                "faultCause": "  ",
            }],
            "metadata": {"total": 1},
        })

    async def exercise() -> gateway.ModuleIdentity | None:
        async with gateway.GatewayRest(GATEWAY_ENDPOINT, GATEWAY_TOKEN,
                                       transport=httpx.MockTransport(handler)) as client:
            return await client.mcp_module()

    identity = asyncio.run(exercise())
    assert identity is not None
    assert (identity.build, identity.state, identity.on_startup) == (FILE_BUILD, "INACTIVE", "disabled")
    assert identity.fault_cause is None


# ------------------------------------------------------------------- upgrade class


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
def test_upgrade_class_names_the_change(installed: str | None, target: str, expected: str) -> None:
    assert upgrade_class(installed, target) == expected
    assert needs_acknowledgement(expected) == (expected in ("major", "downgrade"))


# ------------------------------------------------------------------ security


def test_the_managed_level_shape_and_the_grant_are_minimal() -> None:
    """The pure pieces D20 leans on: insertion, path finding, the granted tree."""

    inputs = make_inputs()
    level = security.desired_level(inputs)
    assert level == {"name": LEVEL, "description": security.LEVEL_DESCRIPTION.format(profile=PROFILE),
                     "children": []}
    tree: list[dict[str, Any]] = [{
        "name": "Authenticated",
        "description": "authenticated",
        "children": [{"name": "Roles", "children": []}],
    }]
    merged, error = security.with_managed_level(tree, inputs)
    assert error == "" and merged is not None
    assert merged[0]["children"][-1] == level
    # The observed tree is never mutated in place.
    assert len(tree[0]["children"]) == 1

    grant = security.token_grant(inputs, tree, creating=True)
    assert grant == [{
        "name": "Authenticated",
        "description": "authenticated",
        "children": [level],
    }]

    # A tree without the parent, and one with the parent twice, are both refusals.
    assert security.with_managed_level([{"name": "Public"}], inputs)[1].startswith(
        "the Gateway's security tree has no Authenticated level"
    )
    doubled = [*tree, {"name": "Authenticated", "children": []}]
    assert "2 top-level Authenticated levels" in security.with_managed_level(doubled, inputs)[1]


def test_the_token_hash_derivation_matches_the_live_rule() -> None:
    """G0's recorded pair: 32 key bytes, unpadded Base64URL SHA-256 of the decoded key."""

    assert security.token_hash(GENERATED_API_TOKEN_KEY) == GENERATED_API_TOKEN_HASH
    assert security.token_hash("not base64url!") == ""
    assert security.token_secret("svc", GENERATED_API_TOKEN_KEY) == f"svc:{GENERATED_API_TOKEN_KEY}"
    with pytest.raises(security.CredentialError):
        security.credential({"key": GENERATED_API_TOKEN_KEY, "hash": "wrong"})
    with pytest.raises(security.CredentialError):
        security.credential({})


def test_the_secret_file_is_created_with_0600_and_never_overwritten(tmp_path: Path) -> None:
    path = tmp_path / "credential.token"
    old_umask = os.umask(0o777)
    try:
        security.write_secret_file(path, SECRET)
    finally:
        os.umask(old_umask)
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert path.read_text(encoding="utf-8") == SECRET + "\n"
    with pytest.raises(security.FileError, match="refusing to overwrite"):
        security.write_secret_file(path, "other:value")
    assert path.read_text(encoding="utf-8") == SECRET + "\n"
    assert security.check_secret_file_target(path).endswith("refusing to overwrite a credential file")


# ---------------------------------------------------------------------- writer


def test_a_wildcard_tool_list_can_never_be_written() -> None:
    """D20: the Server Config Tool mapping is explicit, and a create needs permissions."""

    config = {
        "title": SERVER_CONFIG,
        "permissions": PERMISSIONS,
        "tools": {f"project/{PROJECT}": ["*"]},
    }

    async def attempt(document: dict[str, Any]) -> None:
        async with writer.GatewayWriter(DEAD_ENDPOINT, "token") as client:
            await client.create_server_config(SERVER_CONFIG, document, enabled=True)

    with pytest.raises(writer.WriteError, match="must not contain a wildcard"):
        asyncio.run(attempt(config))
    config["tools"] = {f"project/{PROJECT}": TOOLS}
    config["permissions"] = {}
    with pytest.raises(writer.WriteError, match="permissions tree"):
        asyncio.run(attempt(config))


def test_the_writer_refuses_an_unusable_token_document() -> None:
    good: dict[str, Any] = {
        "profile": {
            "type": "basic-token", "secureChannelRequired": True,
            "securityLevels": [{"name": "Authenticated", "children": []}], "timestamp": 1,
        },
        "settings": {"tokenHash": GENERATED_API_TOKEN_HASH},
    }

    async def exercise() -> None:
        async with writer.GatewayWriter(DEAD_ENDPOINT, "token") as client:
            item = client._api_token_change("svc", good, "a description")
            assert item["name"] == "svc" and item["description"] == "a description"
            assert item["config"]["profile"]["type"] == "basic-token"
            for mutate, expected in (
                (lambda doc: doc["profile"].update({"type": "jwt"}), "basic-token profile"),
                (lambda doc: doc["profile"].update({"securityLevels": []}),
                 "granted an explicit security level"),
                (lambda doc: doc.update({"settings": {}}), "tokenHash"),
                (lambda doc: doc["profile"].pop("secureChannelRequired"), "secureChannelRequired"),
                (lambda doc: doc["profile"].pop("timestamp"), "timestamp"),
            ):
                document = json.loads(json.dumps(good))
                mutate(document)
                with pytest.raises(writer.WriteError, match=expected):
                    client._api_token_change("svc", document, "a description")

    asyncio.run(exercise())


# --------------------------------------------------------------------------- policy

POLICY = json.dumps(
    {"schemaVersion": 1, "allowlists": {}, "serviceIdentity": "ignition-mcp-service", "auditMode": "best_effort"}
)


def _served_document(text: str) -> dict[str, Any]:
    return {"tags": documents.policy_tags(text)}


class _ScriptedWriter:
    """A writer whose served policy is scripted, so the repair path is testable."""

    def __init__(self, served: list[str]) -> None:
        self._served = list(served)
        self.imports: list[tuple[str, str]] = []

    async def export_policy(self) -> dict[str, Any]:
        text = self._served[0] if len(self._served) == 1 else self._served.pop(0)
        return _served_document(text)

    async def import_policy(self, text: str, *, first_policy: str) -> None:
        self.imports.append((text, first_policy))


def test_confirm_policy_accepts_a_read_back_that_matches() -> None:
    fake = _ScriptedWriter([POLICY])
    repaired, declared = asyncio.run(policy.confirm_policy(fake, documents.Documents(policy_text=POLICY)))
    assert (repaired, declared) == (False, str(documents.byte_length(POLICY)))
    assert fake.imports == []


def test_confirm_policy_repairs_once_when_the_provider_serves_another_document() -> None:
    fake = _ScriptedWriter([json.dumps({"tags": []}), POLICY])
    repaired, declared = asyncio.run(policy.confirm_policy(fake, documents.Documents(policy_text=POLICY)))
    assert (repaired, declared) == (True, str(documents.byte_length(POLICY)))
    assert fake.imports == [(POLICY, "MergeOverwrite")]


def test_confirm_policy_refuses_a_provider_that_still_differs_after_the_repair() -> None:
    fake = _ScriptedWriter([json.dumps({"tags": []})])
    with pytest.raises(writer.WriteError, match="does not match the document this run wrote"):
        asyncio.run(policy.confirm_policy(fake, documents.Documents(policy_text=POLICY)))
    assert len(fake.imports) == 1
