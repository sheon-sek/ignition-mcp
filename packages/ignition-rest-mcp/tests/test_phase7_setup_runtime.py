"""``ignition-mcp setup`` on the Runtime plane (issue #74, D32 sections 5 to 10).

A stateful fake Gateway answers through ``httpx.MockTransport``: the Module flow,
projects, Security Levels, API tokens, Server Configs, the policy provider and each
role's MCP endpoint, which checks the role's token the way the Gateway does. No test
opens a socket or sleeps.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import itertools
import json
import re
import secrets
import zipfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

import httpx
import pytest

from ignition_rest_mcp.cli.engine import main as engine
from ignition_rest_mcp.cli.engine.report import JsonReporter
from ignition_rest_mcp.cli.setup import runtime
from ignition_rest_mcp.cli.gateway_ops import security
from ignition_rest_mcp.cli.gateway_ops.mcp_http import McpHttpClient, McpProbeError
from ignition_rest_mcp.errors import GatewayError
from ignition_rest_mcp.projects.locks import ProjectFileLock, ProjectLockRegistry, project_lock_path

ROOT = Path(__file__).resolve().parents[3]
MODULES = ROOT / "tests/fixtures/modules"
URL = "http://gw.test:8088"
SETUP_KEY = base64.urlsafe_b64encode(b"s" * 32).rstrip(b"=").decode()
SETUP_TOKEN = f"setup:{SETUP_KEY}"
SETUP_LEVEL = [{"name": "Authenticated", "children": [{"name": "Setup", "children": []}]}]
MCP_TYPE = "com.inductiveautomation.mcp/server-config"


def _grant(permission: str = "AnyOf") -> dict[str, Any]:
    return {"type": permission, "securityLevels": json.loads(json.dumps(SETUP_LEVEL))}


class FakeGateway:
    """Just enough Gateway for the Runtime plane of ``setup``."""

    def __init__(self) -> None:
        self.signatures = itertools.count(1)
        self.requests: list[tuple[str, str]] = []
        self.module_build: str | None = None
        self.uploaded = False
        self.levels: list[dict[str, Any]] = [
            {"name": "Authenticated", "description": "Represents a user who has been authenticated by the system.",
             "children": [{"name": "Setup", "children": []}]},
            {"name": "Public", "children": []},
        ]
        self.levels_signature = self._sign()
        self.properties = {key: _grant() for key, _ in runtime.GATEWAY_PERMISSIONS}
        self.tokens: dict[str, dict[str, Any]] = {}
        self._add_token("setup", SETUP_KEY, SETUP_LEVEL, secure=False)
        self.projects: dict[str, bytes] = {}
        self.configs: dict[str, dict[str, Any]] = {}
        self.provider = False
        self.tags: dict[str, Any] = {}
        #: An endpoint that answers but serves no Tool, as a Module does before the
        #: project's provider registers.
        self.serve_no_tools = False
        #: What ``bundle_info`` reports, when it should differ from the deployed marker.
        self.bundle_info_version = ""
        self.bundle_resources = runtime.read_bundle(ROOT).inventories["readonly"]["resources"]
        #: Levels someone adds while the Gateway restarts after the Module install.
        self.levels_added_on_restart: list[dict[str, Any]] = []
        #: Called before each project export is answered, as a Designer save would land.
        self.on_export: Callable[[int], None] | None = None
        self.exports = 0
        #: Store an import the way 8.3.8 does: ``resource.json`` re-serialized and a
        #: ``parent`` key in ``project.json``.
        self.reserialize_on_import = False

    # ---------------------------------------------------------------- helpers

    def _sign(self) -> str:
        return f"sig-{next(self.signatures)}"

    def _add_token(self, name: str, key: str, levels: list[dict[str, Any]], *, secure: bool) -> None:
        self.tokens[name] = {
            "name": name, "collection": "core", "enabled": True, "signature": self._sign(),
            "config": {
                "profile": {"type": "basic-token", "secureChannelRequired": secure, "securityLevels": levels},
                "settings": {"tokenHash": security.token_hash(key)},
            },
        }

    def _token_for(self, header: str | None) -> dict[str, Any] | None:
        name, _, key = (header or "").partition(":")
        document = self.tokens.get(name)
        if document is None or document["config"]["settings"]["tokenHash"] != security.token_hash(key):
            return None
        return document

    @property
    def writes(self) -> list[tuple[str, str]]:
        return [(method, path) for method, path in self.requests if method != "GET" and "/data/mcp/" not in path]

    def project_json(self, name: str) -> dict[str, Any] | None:
        archive = self.projects.get(name)
        if archive is None:
            return None
        with zipfile.ZipFile(io.BytesIO(archive)) as handle:
            document: dict[str, Any] = json.loads(handle.read("project.json"))
        return document

    # ---------------------------------------------------------------- routing

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = unquote(request.url.path)
        query = {key: values[0] for key, values in parse_qs(urlsplit(str(request.url)).query).items()}
        self.requests.append((request.method, path))
        if path.startswith("/data/mcp/"):
            if request.method == "GET":
                return httpx.Response(415, json={"message": "Unsupported Media Type"})
            return self._mcp(request, path.rsplit("/", 1)[-1])
        if self._token_for(request.headers.get("X-Ignition-API-Token")) is None:
            return httpx.Response(403, json={"message": "Forbidden"})
        body = request.content
        if request.method == "GET":
            return self._get(path, query)
        if request.method == "DELETE":
            return self._delete(path)
        return self._write(request.method, path, query, body)

    def _get(self, path: str, query: dict[str, str]) -> httpx.Response:
        if path == "/data/api/v1/gateway-info":
            return httpx.Response(200, json={"ignitionVersion": "8.3.8 (b2026071409)"})
        if path == "/data/api/v1/modules/healthy":
            items = []
            if self.module_build is not None:
                items.append({"id": "com.inductiveautomation.mcp", "version": f"1.3.5-SNAPSHOT (b{self.module_build})"})
            return httpx.Response(200, json={"items": items, "metadata": {"total": len(items)}})
        if path in ("/data/api/v1/modules/certificate", "/data/api/v1/modules/eula"):
            if not self.uploaded:
                return httpx.Response(404, json={})
            if path.endswith("eula"):
                return httpx.Response(200, content=b"<html>EULA</html>")
            return httpx.Response(200, json={"subjectName": "Inductive Automation"})
        if path.startswith("/data/api/v1/projects/find/"):
            document = self.project_json(path.rsplit("/", 1)[-1])
            return httpx.Response(200, json=document) if document is not None else httpx.Response(404, json={})
        if path.startswith("/data/api/v1/projects/export/"):
            self.exports += 1
            if self.on_export is not None:
                self.on_export(self.exports)
            archive = self.projects.get(path.rsplit("/", 1)[-1])
            return httpx.Response(200, content=archive) if archive else httpx.Response(404, json={})
        if path == "/data/api/v1/resources/singleton/ignition/security-levels":
            return httpx.Response(200, json={
                "name": "security-levels", "collection": "core", "signature": self.levels_signature,
                "config": {"securityLevels": self.levels},
            })
        if path == "/data/api/v1/resources/singleton/ignition/security-properties":
            return httpx.Response(200, json={"config": self.properties})
        if path.startswith("/data/api/v1/resources/find/ignition/api-token/"):
            token = self.tokens.get(path.rsplit("/", 1)[-1])
            return httpx.Response(200, json=token) if token else httpx.Response(404, json={})
        if path.startswith(f"/data/api/v1/resources/find/{MCP_TYPE}/"):
            if self.module_build is None:
                return httpx.Response(400, json={"message": "unknown resource type"})
            config = self.configs.get(path.rsplit("/", 1)[-1])
            return httpx.Response(200, json=config) if config else httpx.Response(404, json={})
        if path == "/data/api/v1/resources/find/ignition/tag-provider/IgnitionMCPPolicy":
            return httpx.Response(200, json={"name": "IgnitionMCPPolicy"}) if self.provider else httpx.Response(404)
        if path == "/data/api/v1/tags/export":
            tags = [{"name": name, "value": value} for name, value in self.tags.items()]
            return httpx.Response(200, json={"name": "", "tags": tags})
        return httpx.Response(404, json={"message": f"no route {path}"})

    def _delete(self, path: str) -> httpx.Response:
        prefix = f"/data/api/v1/resources/{MCP_TYPE}/"
        if path.startswith(prefix):
            name, signature = path[len(prefix):].split("/")
            if self.configs.get(name, {}).get("signature") != signature:
                return httpx.Response(409, json={"message": "signature mismatch"})
            del self.configs[name]
            return httpx.Response(200, json={"success": True})
        prefix = "/data/api/v1/resources/ignition/api-token/"
        if path.startswith(prefix):
            name, signature = path[len(prefix):].split("/")
            if self.tokens.get(name, {}).get("signature") != signature:
                return httpx.Response(409, json={"message": "signature mismatch"})
            del self.tokens[name]
            return httpx.Response(200, json={"success": True})
        return httpx.Response(404, json={})

    def _write(self, method: str, path: str, query: dict[str, str], body: bytes) -> httpx.Response:
        ok = httpx.Response(200, json={"success": True})
        if path == "/data/api/v1/modules/upload":
            self.uploaded = True
            return httpx.Response(200, json={"moduleId": "com.inductiveautomation.mcp"})
        if path in ("/data/api/v1/modules/certificate", "/data/api/v1/modules/eula"):
            return ok
        if path == "/data/api/v1/modules/install":
            self.module_build = runtime.PINNED_MODULE_BUILD
            return ok
        if path == "/data/api/v1/restart-tasks/restart":
            # A live Gateway serves the Security Level singleton with a new signature
            # after a restart (issue #78), and a hand edit may land in the same window.
            self.levels_signature = self._sign()
            self.levels += self.levels_added_on_restart
            return ok
        if path.startswith("/data/api/v1/projects/import/"):
            name = path.rsplit("/", 1)[-1]
            if name in self.projects and query.get("overwrite") != "true":
                return httpx.Response(409, json={"message": "exists"})
            self.projects[name] = _reserialize(body) if self.reserialize_on_import else body
            return ok
        if path == "/data/api/v1/api-token/generate":
            key = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
            return httpx.Response(200, json={"key": key, "hash": security.token_hash(key)})
        items: list[dict[str, Any]] = json.loads(body) if body else []
        if path == "/data/api/v1/resources/ignition/security-levels":
            if items[0]["signature"] != self.levels_signature:
                return httpx.Response(409, json={"message": "signature mismatch"})
            self.levels = items[0]["config"]["securityLevels"]
            self.levels_signature = self._sign()
            return ok
        if path == "/data/api/v1/resources/ignition/api-token":
            item = items[0]
            if item["name"] in self.tokens:
                return httpx.Response(409, json={"message": "exists"})
            self.tokens[item["name"]] = {**item, "signature": self._sign()}
            return ok
        if path == f"/data/api/v1/resources/{MCP_TYPE}":
            item = items[0]
            if method == "PUT":
                current = self.configs.get(item["name"])
                if current is None or current["signature"] != item["signature"]:
                    return httpx.Response(409, json={"message": "signature mismatch"})
            self.configs[item["name"]] = {**item, "signature": self._sign()}
            return ok
        if path == "/data/api/v1/resources/ignition/tag-provider":
            self.provider = True
            return ok
        if path == "/data/api/v1/tags/import":
            for tag in json.loads(body)["tags"]:
                self.tags[tag["name"]] = tag["value"]
            return httpx.Response(200, json={"failureCount": 0, "failures": [], "successCount": 2})
        return httpx.Response(404, json={"message": f"no route {path}"})

    def _mcp(self, request: httpx.Request, name: str) -> httpx.Response:
        token = self._token_for(request.headers.get("X-Ignition-API-Token"))
        config = self.configs.get(name)
        if config is None or self.module_build is None:
            return httpx.Response(404, json={})
        if token is None:
            return httpx.Response(403, json={"message": "Forbidden"})
        profile = token["config"]["profile"]
        if profile["secureChannelRequired"] and request.url.scheme == "http":
            return httpx.Response(403, json={"message": "Forbidden"})
        if not runtime.satisfies(runtime.token_levels(token), config["config"]["permissions"]):
            return httpx.Response(403, json={"message": "Forbidden"})
        message = json.loads(request.content)
        if "id" not in message:
            return httpx.Response(202)
        method = message["method"]
        if method == "initialize":
            capabilities: dict[str, Any] = {"resources": {}} if self.serve_no_tools else {"tools": {}, "resources": {}}
            result: dict[str, Any] = {
                "protocolVersion": "2025-06-18", "capabilities": capabilities, "serverInfo": {"name": name},
            }
        elif method == "tools/list" and self.serve_no_tools:
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32600}})
        elif method == "tools/list":
            tools = config["config"]["tools"][f"project/{runtime.PROJECT}"]
            result = {"tools": [{"name": tool} for tool in tools]}
        elif method == "resources/list":
            result = {"resources": [{"uri": uri} for uri in self.bundle_resources]}
        elif method == "resources/read":
            result = {"contents": [{"uri": message["params"]["uri"], "text": "{}"}]}
        elif method == "tools/call" and message["params"]["name"] == "bundle_info":
            marker = (self.project_json(runtime.PROJECT) or {}).get("description", "").splitlines()[-1]
            version = self.bundle_info_version or marker.rsplit("=", 1)[-1]
            result = {"structuredContent": {"bundleVersion": version}, "content": []}
        else:
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32601}})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": message["id"], "result": result})


def _rewrite(archive: bytes, change: Callable[[str, bytes], bytes | None]) -> bytes:
    """``archive`` with each entry passed through ``change``; ``None`` drops the entry."""

    buffer = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(archive)) as source, zipfile.ZipFile(buffer, "w") as target:
        for name in source.namelist():
            data = change(name, source.read(name))
            if data is not None:
                target.writestr(name, data)
    return buffer.getvalue()


def _reserialize(archive: bytes) -> bytes:
    def change(name: str, data: bytes) -> bytes:
        if name.endswith("resource.json"):
            document = json.loads(data)
            document["attributes"] = dict(reversed(list(document["attributes"].items())))
            return json.dumps(document, indent=2).encode()
        if name == "project.json":
            return json.dumps({**json.loads(data), "parent": ""}, indent=2).encode()
        return data

    return _rewrite(archive, change)


async def _no_sleep(_seconds: float) -> None:
    return None


@pytest.fixture
def gateway(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeGateway]:
    fake = FakeGateway()
    monkeypatch.setattr(
        runtime,
        "SETTINGS",
        runtime.Settings(
            transport=httpx.MockTransport(fake.handle), sleep=_no_sleep, module_dirs=[MODULES], checkout=ROOT,
        ),
    )
    runtime.register()
    try:
        yield fake
    finally:
        runtime.unregister()


def _token_file(tmp_path: Path) -> Path:
    path = tmp_path / "setup-token.txt"
    path.write_text(SETUP_TOKEN + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def _probe(_url: str, token: str) -> str:
    return "" if token == SETUP_TOKEN else "the Gateway does not know this key"


def setup(
    tmp_path: Path, *extra: str, environment: str = "dev", url: str = URL
) -> tuple[int, dict[str, Any], str]:
    stream = io.StringIO()
    argv = [
        "setup", "--gateway-url", url, "--environment", environment,
        "--gateway-token-file", str(_token_file(tmp_path)), "--json", *extra,
    ]
    code = engine.run(
        argv, root=tmp_path / "deployments", interactive=False, token_probe=_probe,
        reporter=JsonReporter("setup", stream),
    )
    raw = stream.getvalue()
    return code, json.loads(raw), raw


ACCEPT = ("--yes", "--accept-certificate", "--accept-eula")


def steps(document: dict[str, Any]) -> dict[str, str]:
    return {step["step"]: step["status"] for step in document["steps"]}


def deployment(tmp_path: Path) -> Path:
    return tmp_path / "deployments" / "default"


# ------------------------------------------------------------------ happy path


def test_dev_setup_takes_an_empty_gateway_to_both_roles_and_a_rerun_changes_nothing(
    tmp_path: Path, gateway: FakeGateway
) -> None:
    code, document, raw = setup(tmp_path, *ACCEPT)

    assert code == 0, document
    status = steps(document)
    for step in (
        "runtime module", "runtime bundle", "runtime security levels", "runtime token analysis",
        "runtime token engineer", "runtime server config analysis", "runtime server config engineer",
        "runtime policy",
    ):
        assert status[step] == "CHANGED", (step, document)
    assert status["runtime check analysis"] == status["runtime check engineer"] == "OK"
    assert {item["item"] for item in document["accepted"]} >= {
        "module_certificate", "module_eula", "gateway_restart", "unencrypted_token_channel",
        "wildcard_target_allowlist",
    }
    assert any(str(MODULES) in change["change"] for change in document["plan"])
    # Each role has its own token, level and permissions tree.
    analysis = gateway.configs["analysis"]["config"]
    engineer = gateway.configs["engineer"]["config"]
    assert analysis["permissions"]["securityLevels"][0]["children"][0]["name"] == "IgnitionMcpAnalysis"
    assert engineer["permissions"]["securityLevels"][0]["children"][0]["name"] == "IgnitionMcpEngineer"
    assert "tag_write" in engineer["tools"]["project/ignition_runtime"]
    assert "tag_write" not in analysis["tools"]["project/ignition_runtime"]
    assert gateway.project_json("ignition_runtime") is not None
    policy = json.loads(gateway.tags["RuntimeTargetPolicy"])
    assert policy["allowlists"]["tag_write"] == ["*"] and policy["alarmShelveMaxSeconds"] == 3600
    secret = (deployment(tmp_path) / "runtime-engineer.secret").read_text().strip()
    assert (deployment(tmp_path) / "runtime-engineer.secret").stat().st_mode & 0o077 == 0
    assert secret.partition(":")[2] not in raw and SETUP_KEY not in raw
    assert (deployment(tmp_path) / "runtime-policy.json").is_file()

    gateway.requests.clear()
    code, document, _ = setup(tmp_path)

    assert code == 0, document
    assert document["plan"] == []
    assert set(steps(document).values()) == {"OK"}, document
    assert gateway.writes == []


def test_the_level_edit_after_the_module_restart_uses_the_signature_read_then(
    tmp_path: Path, gateway: FakeGateway
) -> None:
    code, document, _ = setup(tmp_path, *ACCEPT)

    assert code == 0, document
    assert steps(document)["runtime security levels"] == "CHANGED"


def test_a_level_tree_changed_during_the_module_restart_is_not_overwritten(
    tmp_path: Path, gateway: FakeGateway
) -> None:
    gateway.levels_added_on_restart = [{"name": "HandMade", "children": []}]

    code, document, _ = setup(tmp_path, *ACCEPT)

    assert code == 1
    assert steps(document)["runtime security levels"] == "FAILED"
    assert "changed on the Gateway after the plan read it" in document["error"]["message"]
    assert not any(level["name"] == "IgnitionMcpAnalysis" for level in gateway.levels[0]["children"])


def test_dry_run_shows_the_plan_and_writes_nothing(tmp_path: Path, gateway: FakeGateway) -> None:
    code, document, _ = setup(tmp_path, *ACCEPT, "--dry-run")

    assert code == 0, document
    assert len(document["plan"]) > 5
    assert gateway.writes == []
    assert not deployment(tmp_path).exists()


# ------------------------------------------------------------- safety refusals


def test_a_missing_acceptance_stops_before_any_write(tmp_path: Path, gateway: FakeGateway) -> None:
    code, document, _ = setup(tmp_path, "--yes", "--accept-eula")

    assert code == 2
    assert document["error"]["code"] == "acceptance_required"
    assert "--accept-certificate" in document["error"]["message"]
    assert gateway.writes == []


def test_a_lower_module_build_is_refused(tmp_path: Path, gateway: FakeGateway) -> None:
    gateway.module_build = "2099010100"

    code, document, _ = setup(tmp_path, *ACCEPT)

    assert code == 1
    assert "a lower build is never installed" in document["error"]["message"]
    assert gateway.writes == []


def test_a_module_file_with_another_hash_is_refused(tmp_path: Path, gateway: FakeGateway) -> None:
    other = tmp_path / "other.modl"
    other.write_bytes(b"PK not the pinned module")

    code, document, _ = setup(tmp_path, *ACCEPT, "--module-file", str(other))

    assert code == 2
    assert document["error"]["code"] == "invalid_input"
    assert "not the pinned MCP Module build" in document["error"]["message"]
    assert gateway.writes == []


def test_an_unmanaged_project_with_the_bundle_name_is_never_taken_over(
    tmp_path: Path, gateway: FakeGateway
) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("project.json", json.dumps({"title": "mine", "description": "hand made"}))
    gateway.projects["ignition_runtime"] = buffer.getvalue()

    code, document, _ = setup(tmp_path, *ACCEPT)

    assert code == 1
    assert "never takes over an unmanaged project" in document["error"]["message"]
    assert gateway.writes == []


def test_a_setup_key_not_ticked_under_every_permission_is_rejected(tmp_path: Path, gateway: FakeGateway) -> None:
    gateway.properties["writePermissions"] = {
        "type": "AnyOf", "securityLevels": [{"name": "Authenticated", "children": [{"name": "Admins"}]}],
    }

    code, document, _ = setup(tmp_path, *ACCEPT)

    assert code == 2
    assert document["error"]["code"] == "gateway_token_rejected"
    assert "not ticked under Gateway Write" in document["error"]["message"]


def test_a_managed_project_is_backed_up_before_it_is_replaced(tmp_path: Path, gateway: FakeGateway) -> None:
    assert setup(tmp_path, *ACCEPT)[0] == 0
    old = gateway.projects["ignition_runtime"]
    buffer = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(old)) as source, zipfile.ZipFile(buffer, "w") as target:
        for name in source.namelist():
            data = source.read(name)
            if name == "project.json":
                project = json.loads(data)
                project["description"] = project["description"].replace("bundle=", "bundle=0.0.1#").split("#")[0]
                data = json.dumps(project).encode()
            target.writestr(name, data)
    gateway.projects["ignition_runtime"] = buffer.getvalue()

    code, document, _ = setup(tmp_path, "--yes")

    assert code == 0, document
    assert steps(document)["runtime bundle"] == "CHANGED"
    assert (deployment(tmp_path) / "backups" / "ignition_runtime-0.0.1.zip").read_bytes() == buffer.getvalue()


# ------------------------------------------------------------------ re-run rules


def test_a_lost_secret_is_reported_and_recreated_only_with_recreate_tokens(
    tmp_path: Path, gateway: FakeGateway
) -> None:
    assert setup(tmp_path, *ACCEPT)[0] == 0
    (deployment(tmp_path) / "runtime-analysis.secret").unlink()
    old = gateway.tokens["ignition-mcp-analysis"]["config"]["settings"]["tokenHash"]
    gateway.requests.clear()

    code, document, _ = setup(tmp_path, "--yes")

    assert code == 2
    assert document["error"]["code"] == "acceptance_required"
    assert "--recreate-tokens" in document["error"]["message"]
    assert gateway.writes == []

    code, document, _ = setup(tmp_path, "--yes", "--recreate-tokens")

    assert code == 0, document
    assert steps(document)["runtime token analysis"] == "CHANGED"
    assert steps(document)["runtime check analysis"] == "OK"
    assert gateway.tokens["ignition-mcp-analysis"]["config"]["settings"]["tokenHash"] != old
    assert (deployment(tmp_path) / "runtime-analysis.secret").is_file()


def test_a_hand_edited_policy_is_reported_and_restored_after_acceptance(
    tmp_path: Path, gateway: FakeGateway
) -> None:
    assert setup(tmp_path, *ACCEPT)[0] == 0
    gateway.tags["RuntimeTargetPolicy"] = gateway.tags["RuntimeTargetPolicy"].replace('"*"', '"[default]Plant"')
    gateway.tags["RuntimeTargetPolicyLength"] = len(gateway.tags["RuntimeTargetPolicy"].encode())

    code, document, _ = setup(tmp_path)

    assert code == 2
    assert document["error"]["code"] == "acceptance_required"
    assert "overwrite_hand_edit" in document["error"]["message"]
    assert any("changed it by hand" in change["change"] for change in document["plan"])

    code, document, _ = setup(tmp_path, "--yes")

    assert code == 0, document
    assert steps(document)["runtime policy"] == "CHANGED"
    assert "overwrite_hand_edit" in {item["item"] for item in document["accepted"]}
    assert json.loads(gateway.tags["RuntimeTargetPolicy"])["allowlists"]["tag_write"] == ["*"]


def test_permissions_drift_on_a_server_config_is_a_change(tmp_path: Path, gateway: FakeGateway) -> None:
    assert setup(tmp_path, *ACCEPT)[0] == 0
    gateway.configs["analysis"]["config"]["permissions"] = {
        "type": "AllOf",
        "securityLevels": [{"name": "Authenticated", "children": [{"name": "IgnitionMcpRuntimeReadonly"}]}],
    }

    code, document, _ = setup(tmp_path, "--yes")

    assert code == 0, document
    assert steps(document)["runtime server config analysis"] == "CHANGED"
    assert any("permissions tree differs" in change["change"] for change in document["plan"])
    assert gateway.configs["analysis"]["config"]["permissions"] == runtime.ROLES["analysis"].permissions()


# ----------------------------------------------------------------- 403 causes


def test_a_403_at_the_closing_check_names_its_cause(tmp_path: Path, gateway: FakeGateway) -> None:
    code, document, _ = setup(tmp_path, *ACCEPT, "--provision-security-levels", environment="prod")

    assert code == 1
    assert document["error"]["code"] == "step_failed"
    assert "requires a secure channel and the Gateway URL is http" in document["error"]["message"]
    assert steps(document)["runtime check analysis"] == "FAILED"


def test_the_setup_key_check_reads_levels_below_a_required_level() -> None:
    held = [("Authenticated", "Roles", "Admin")]

    assert runtime.satisfies(held, {"type": "AnyOf", "securityLevels": [{"name": "Authenticated"}]})
    assert not runtime.satisfies(held, {"type": "AllOf", "securityLevels": [
        {"name": "Authenticated", "children": [{"name": "Roles"}, {"name": "Other"}]},
    ]})
    assert runtime.satisfies([], {"type": "AnyOf", "securityLevels": []})
    assert hashlib.sha256((MODULES / next(MODULES.glob("*.modl")).name).read_bytes()).hexdigest() == (
        runtime.PINNED_MODULE_SHA256
    )


# ------------------------------------------------------------- review round 1


def _set_marker(gateway: FakeGateway, version: str) -> None:
    old = gateway.projects["ignition_runtime"]
    buffer = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(old)) as source, zipfile.ZipFile(buffer, "w") as target:
        for name in source.namelist():
            data = source.read(name)
            if name == "project.json":
                project = json.loads(data)
                lines = project["description"].splitlines()
                lines[-1] = lines[-1].rsplit("=", 1)[0] + "=" + version
                project["description"] = "\n".join(lines)
                data = json.dumps(project).encode()
            target.writestr(name, data)
    gateway.projects["ignition_runtime"] = buffer.getvalue()


def test_a_module_upgrade_needs_its_own_acceptance(tmp_path: Path, gateway: FakeGateway) -> None:
    gateway.module_build = "2020010100"

    code, document, _ = setup(tmp_path, "--accept-certificate", "--accept-eula")

    assert code == 2
    assert "module_upgrade" in document["error"]["message"]
    assert gateway.writes == []

    code, document, _ = setup(tmp_path, *ACCEPT)

    assert code == 0, document
    assert "module_upgrade" in {item["item"] for item in document["accepted"]}
    assert gateway.module_build == runtime.PINNED_MODULE_BUILD


def test_a_bundle_downgrade_needs_its_own_acceptance(tmp_path: Path, gateway: FakeGateway) -> None:
    assert setup(tmp_path, *ACCEPT)[0] == 0
    _set_marker(gateway, "9.0.0")

    code, document, _ = setup(tmp_path, "--accept-certificate")

    assert code == 2
    assert "bundle_upgrade" in document["error"]["message"]
    assert any("(downgrade)" in change["change"] for change in document["plan"])

    code, document, _ = setup(tmp_path, "--yes")

    assert code == 0, document
    assert "bundle_upgrade" in {item["item"] for item in document["accepted"]}
    assert (deployment(tmp_path) / "backups" / "ignition_runtime-9.0.0.zip").is_file()


def test_moving_to_prod_narrows_the_roles_and_the_policy(tmp_path: Path, gateway: FakeGateway) -> None:
    secure = "https://gw.test:8043"
    assert setup(tmp_path, *ACCEPT, url=secure)[0] == 0
    assert "engineer" in gateway.configs

    code, document, _ = setup(tmp_path, "--yes", environment="prod", url=secure)

    assert code == 0, document
    changes = " | ".join(change["change"] for change in document["plan"])
    assert "remove the engineer role" in changes and "narrow the Runtime Target Policy" in changes
    assert steps(document)["runtime remove engineer"] == "CHANGED"
    assert "engineer" not in gateway.configs and "ignition-mcp-engineer" not in gateway.tokens
    authenticated = next(node for node in gateway.levels if node["name"] == "Authenticated")
    assert [child["name"] for child in authenticated["children"]] == ["Setup", "IgnitionMcpAnalysis"]
    assert not (deployment(tmp_path) / "runtime-engineer.secret").exists()
    assert not (deployment(tmp_path) / "permissions-engineer.json").exists()
    assert json.loads(gateway.tags["RuntimeTargetPolicy"])["allowlists"] == {}
    assert 'roles = ["analysis"]' in (deployment(tmp_path) / "deployment.toml").read_text()
    assert steps(document)["runtime check analysis"] == "OK"


def test_the_closing_check_never_writes_to_a_config_it_did_not_plan(tmp_path: Path, gateway: FakeGateway) -> None:
    assert setup(tmp_path, *ACCEPT)[0] == 0
    gateway.serve_no_tools = True
    gateway.requests.clear()

    code, document, _ = setup(tmp_path)

    assert code == 1
    assert steps(document)["runtime check analysis"] == "FAILED"
    assert "inventory-tools" in document["error"]["message"]
    assert document["error"]["next_action"]
    assert gateway.writes == []


def test_the_closing_check_runs_the_whole_verify_sequence(tmp_path: Path, gateway: FakeGateway) -> None:
    assert setup(tmp_path, *ACCEPT)[0] == 0
    gateway.bundle_resources = gateway.bundle_resources[:-1]

    code, document, _ = setup(tmp_path)

    assert code == 1
    assert "inventory-resources" in document["error"]["message"]

    gateway.bundle_resources = runtime.read_bundle(ROOT).inventories["readonly"]["resources"]
    gateway.bundle_info_version = "0.0.9"
    code, document, _ = setup(tmp_path)

    assert code == 1
    assert "bundle-info" in document["error"]["message"]
    assert any(method == "POST" and "/data/mcp/" in path for method, path in gateway.requests)


def test_each_role_token_is_refused_on_the_other_role_endpoint(tmp_path: Path, gateway: FakeGateway) -> None:
    assert setup(tmp_path, *ACCEPT)[0] == 0
    transport = runtime.SETTINGS.transport

    async def initialize(role: str, token_of: str) -> str:
        token = (deployment(tmp_path) / f"runtime-{token_of}.secret").read_text().strip()
        endpoint = runtime._endpoint_of(URL, f"/data/mcp/{role}")
        try:
            async with McpHttpClient(endpoint, token, transport=transport) as client:
                await client.initialize()
        except McpProbeError as error:
            return str(error)
        return "OK"

    assert asyncio.run(initialize("analysis", "analysis")) == "OK"
    assert asyncio.run(initialize("engineer", "engineer")) == "OK"
    assert "HTTP 403" in asyncio.run(initialize("engineer", "analysis"))
    assert "HTTP 403" in asyncio.run(initialize("analysis", "engineer"))


# ------------------------------------------- issue #80: D16, content drift, ownership


TOOL_SCRIPT = "com.inductiveautomation.mcp/tools/tag_read/onToolCalled.py"
STAMPED_SCRIPT = "com.inductiveautomation.mcp/tools/bundle_info/onToolCalled.py"


def _edit_tool(gateway: FakeGateway, line: bytes = b"# hand edit\n") -> bytes:
    """Append ``line`` to one Tool's script inside the managed project, as a Designer save would."""

    edited = _rewrite(
        gateway.projects[runtime.PROJECT], lambda name, data: data + line if name == TOOL_SCRIPT else data
    )
    gateway.projects[runtime.PROJECT] = edited
    return edited


def _imports(gateway: FakeGateway) -> list[str]:
    return [path for _, path in gateway.writes if path.startswith("/data/api/v1/projects/import/")]


def test_a_rerun_after_the_gateway_reserialized_the_import_changes_nothing(
    tmp_path: Path, gateway: FakeGateway
) -> None:
    gateway.reserialize_on_import = True
    assert setup(tmp_path, *ACCEPT)[0] == 0
    # A checkout on a later commit stamps another revision into bundle_info.
    gateway.projects[runtime.PROJECT] = _rewrite(
        gateway.projects[runtime.PROJECT],
        lambda name, data: re.sub(rb'"[0-9a-f]{40}"', b'"' + b"a" * 40 + b'"', data) if name == STAMPED_SCRIPT else data,
    )
    gateway.requests.clear()

    code, document, _ = setup(tmp_path)

    assert code == 0, document
    assert document["plan"] == []
    assert steps(document)["runtime bundle"] == "OK"
    assert gateway.writes == []


def test_a_hand_edited_tool_is_reported_and_restored_only_after_acceptance(
    tmp_path: Path, gateway: FakeGateway
) -> None:
    assert setup(tmp_path, *ACCEPT)[0] == 0
    deployed = gateway.projects[runtime.PROJECT]
    edited = _edit_tool(gateway)

    code, document, _ = setup(tmp_path)

    assert code == 2
    assert document["error"]["code"] == "acceptance_required"
    assert "overwrite_hand_edit" in document["error"]["message"]
    change = next(item["change"] for item in document["plan"] if "restore the managed project" in item["change"])
    assert "changed it by hand: tools/tag_read (onToolCalled.py)" in change
    assert gateway.projects[runtime.PROJECT] == edited

    code, document, _ = setup(tmp_path, "--yes")

    assert code == 0, document
    assert steps(document)["runtime bundle"] == "CHANGED"
    assert "overwrite_hand_edit" in {item["item"] for item in document["accepted"]}
    assert gateway.projects[runtime.PROJECT] == deployed
    version = runtime.read_bundle(ROOT).version
    assert (deployment(tmp_path) / "backups" / f"ignition_runtime-{version}.zip").read_bytes() == edited


def test_a_change_between_the_baseline_and_the_import_is_refused_and_nothing_is_imported(
    tmp_path: Path, gateway: FakeGateway
) -> None:
    assert setup(tmp_path, *ACCEPT)[0] == 0
    _edit_tool(gateway)
    before = gateway.exports

    def designer_save(count: int) -> None:
        # The plan's export, the baseline, then the re-check just before the import.
        if count == before + 3:
            _edit_tool(gateway, b"# saved in the Designer\n")

    gateway.on_export = designer_save
    gateway.requests.clear()

    code, document, _ = setup(tmp_path, "--yes")

    assert code == 1
    assert document["error"]["code"] == "conflict"
    message = document["error"]["message"]
    assert "between the baseline export and the import" in message and "Designer save" in message
    assert "nothing was imported" in message
    assert steps(document)["runtime bundle"] == "FAILED"
    assert _imports(gateway) == []
    assert b"# saved in the Designer" in gateway.projects[runtime.PROJECT]


def test_a_held_project_lock_refuses_the_replace(tmp_path: Path, gateway: FakeGateway) -> None:
    assert setup(tmp_path, *ACCEPT)[0] == 0
    _edit_tool(gateway)
    lock = ProjectFileLock(project_lock_path(deployment(tmp_path) / "rest-data", "default", runtime.PROJECT))
    assert lock.try_acquire()
    gateway.requests.clear()
    try:
        code, document, _ = setup(tmp_path, "--yes")
    finally:
        lock.release()

    assert code == 1
    assert document["error"]["code"] == "conflict"
    assert "this deployment's REST server is writing the project" in document["error"]["message"]
    assert _imports(gateway) == []


def test_the_rest_project_lock_refuses_while_setup_holds_the_lock_file(tmp_path: Path) -> None:
    held = ProjectFileLock(project_lock_path(tmp_path, "default", runtime.PROJECT))
    assert held.try_acquire()
    # A 1 ms deadline and no poll interval: the refusal comes without a sleep.
    registry = ProjectLockRegistry(timeout_seconds=0.001, max_entries=4, data_dir=tmp_path, poll_seconds=0.0)

    async def mutate() -> str:
        async with registry.acquire("default", runtime.PROJECT):
            return "written"

    try:
        with pytest.raises(GatewayError) as refused:
            asyncio.run(mutate())
    finally:
        held.release()
    assert refused.value.code == "conflict"
    assert asyncio.run(mutate()) == "written"


def test_a_role_resource_setup_did_not_create_is_left_in_place(tmp_path: Path, gateway: FakeGateway) -> None:
    secure = "https://gw.test:8043"
    assert setup(tmp_path, *ACCEPT, url=secure)[0] == 0
    toml = deployment(tmp_path) / "deployment.toml"
    text = toml.read_text()
    for entry in ('"server-config:engineer"', '"runtime-token:ignition-mcp-engineer"',
                  '"level:Authenticated/IgnitionMcpEngineer"'):
        assert entry in text
        text = re.sub(rf"\s*{re.escape(entry)},?", "", text)
    toml.write_text(text)

    code, document, _ = setup(tmp_path, "--yes", environment="prod", url=secure)

    assert code == 0, document
    assert steps(document)["runtime keep engineer"] == "SKIPPED"
    changes = " | ".join(change["change"] for change in document["plan"])
    assert "leave the Server Config engineer in place: the deployment does not record" in changes
    assert "engineer" in gateway.configs and "ignition-mcp-engineer" in gateway.tokens
    authenticated = next(node for node in gateway.levels if node["name"] == "Authenticated")
    assert "IgnitionMcpEngineer" in [child["name"] for child in authenticated["children"]]
    assert (deployment(tmp_path) / "runtime-engineer.secret").exists()
    assert not (deployment(tmp_path) / "permissions-engineer.json").exists()
