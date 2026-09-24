"""``ignition-mcp setup`` on the Runtime plane (issue #74, D32 sections 5 to 10).

A stateful fake Gateway answers through ``httpx.MockTransport``: the Module flow,
projects, Security Levels, API tokens, Server Configs, the policy provider and each
role's MCP endpoint, which checks the role's token the way the Gateway does. No test
opens a socket or sleeps.
"""

from __future__ import annotations

import base64
import hashlib
import io
import itertools
import json
import secrets
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

import httpx
import pytest

from ignition_rest_mcp.cli.engine import main as engine
from ignition_rest_mcp.cli.engine.report import JsonReporter
from ignition_rest_mcp.cli.setup import runtime
from ignition_rest_mcp.cli.setup_native import security

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
            return ok
        if path.startswith("/data/api/v1/projects/import/"):
            name = path.rsplit("/", 1)[-1]
            if name in self.projects and query.get("overwrite") != "true":
                return httpx.Response(409, json={"message": "exists"})
            self.projects[name] = body
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
        if message["method"] == "initialize":
            result: dict[str, Any] = {
                "protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": name},
            }
        elif message["method"] == "tools/list":
            tools = config["config"]["tools"][f"project/{runtime.PROJECT}"]
            result = {"tools": [{"name": tool} for tool in tools]}
        else:
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32601}})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": message["id"], "result": result})


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


def setup(tmp_path: Path, *extra: str, environment: str = "dev") -> tuple[int, dict[str, Any], str]:
    stream = io.StringIO()
    argv = [
        "setup", "--gateway-url", URL, "--environment", environment,
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
