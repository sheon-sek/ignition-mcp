"""``ignition-mcp status``, ``connect`` and ``reset`` (issue #76, D32 sections 2, 4, 8, 10).

The Runtime plane's stateful fake Gateway, extended with the routes these three
commands also read and write: the reserved Tag provider's document with its
signature, the project delete, the Module uninstall and ``database_query_list``. A
deployment is built by running the real ``setup`` Runtime stage, then finished with
the REST plane's state the REST stage would leave, because that stage reads the
Gateway through its own client rather than an injected transport. No test opens a
socket, sleeps, or runs the real ``claude`` or ``codex`` binary.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import secrets
import stat
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import httpx
import pytest
from rich.console import Console

from ignition_rest_mcp.cli.engine import main as engine
from ignition_rest_mcp.cli.engine.deployment import Deployment, open_deployment, save_deployment, write_secret
from ignition_rest_mcp.cli.engine.report import JsonReporter, RichReporter
from ignition_rest_mcp.cli.engine.resolve import Secret as EngineSecret
from ignition_rest_mcp.cli.setup import connect, reset, rest, runtime, status
from ignition_rest_mcp.cli.gateway_ops import documents as docs
from ignition_rest_mcp.cli.gateway_ops import gateway as gw
from ignition_rest_mcp.cli.gateway_ops import security
from test_phase7_setup_runtime import (
    ACCEPT,
    MODULES,
    ROOT,
    SETUP_KEY,
    SETUP_LEVEL,
    SETUP_TOKEN,
    URL,
    FakeGateway,
    _no_sleep,
    _probe,
    _token_file,
    setup,
)

MODULE_UNINSTALL = "/data/api/v1/modules/uninstall"
PROJECT_DELETE = f"/data/api/v1/projects/{runtime.PROJECT}"
PROVIDER_FIND = f"/data/api/v1/resources/find/{docs.TAG_PROVIDER_TYPE}/{docs.PROVIDER}"
ROLE_SECRETS = {
    "analysis": ("ignition-mcp-analysis", "static-analysis-key-0000000000000000"),
    "engineer": ("ignition-mcp-engineer", "static-engineer-key-0000000000000000"),
}
REST_KEY = "restkey0000000000000000000000000000000"


def _key() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()


class CliGateway(FakeGateway):
    """``setup``\"s fake, plus what ``status``, ``reset`` and the policy provider need."""

    def __init__(self) -> None:
        super().__init__()
        self.policy_provider: dict[str, Any] | None = None
        #: What ``database_query_list`` answers with.
        self.queries: list[dict[str, Any]] = [{"alias": "recent_alarms"}]
        self.uninstalled: list[dict[str, Any]] = []

    # -------------------------------------------------------------- interception

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE" and unquote(request.url.path) == MODULE_UNINSTALL:
            self.uninstalled.append(json.loads(request.content or b"{}"))
        return super().handle(request)

    def _get(self, path: str, query: dict[str, str]) -> httpx.Response:
        if path == PROVIDER_FIND:
            if self.policy_provider is None:
                return httpx.Response(404, json={})
            return httpx.Response(200, json=self.policy_provider)
        return super()._get(path, query)

    def _delete(self, path: str) -> httpx.Response:
        if path == PROJECT_DELETE:
            if runtime.PROJECT not in self.projects:
                return httpx.Response(404, json={})
            del self.projects[runtime.PROJECT]
            return httpx.Response(200, json={"success": True})
        if path == MODULE_UNINSTALL:
            self.module_build = None
            return httpx.Response(200, json={"success": True})
        prefix = f"/data/api/v1/resources/{docs.TAG_PROVIDER_TYPE}/"
        if path.startswith(prefix):
            signature = path[len(prefix) :].partition("/")[2]
            if self.policy_provider is None or self.policy_provider["signature"] != signature:
                return httpx.Response(409, json={"message": "signature mismatch"})
            self.policy_provider = None
            self.provider = False
            self.tags.clear()
            return httpx.Response(200, json={"success": True})
        return super()._delete(path)

    def _write(self, method: str, path: str, query: dict[str, str], body: bytes) -> httpx.Response:
        if path == f"/data/api/v1/resources/{docs.TAG_PROVIDER_TYPE}" and method == "POST":
            self.policy_provider = {
                "name": docs.PROVIDER,
                "collection": "core",
                "signature": self._sign(),
                "config": {},
            }
        return super()._write(method, path, query, body)

    def _mcp(self, request: httpx.Request, name: str) -> httpx.Response:
        if request.content:
            message = json.loads(request.content)
            params = message.get("params")
            if (
                message.get("method") == "tools/call"
                and isinstance(params, dict)
                and params.get("name") == status.QUERY_LIST_TOOL
            ):
                if self._token_for(request.headers.get("X-Ignition-API-Token")) is None:
                    return httpx.Response(403, json={"message": "Forbidden"})
                result = {
                    "entries": self.queries,
                    "summary": {"approved": len(self.queries)},
                    "meta": {"correlationId": "test"},
                }
                return httpx.Response(
                    200,
                    json={"jsonrpc": "2.0", "id": message["id"], "result": {"structuredContent": result, "content": []}},
                )
        return super()._mcp(request, name)


# ------------------------------------------------------------------- the fixtures


@pytest.fixture
def gateway(monkeypatch: pytest.MonkeyPatch) -> Iterator[CliGateway]:
    """The fake Gateway and every command this file exercises, registered as ``main`` does."""

    fake = CliGateway()
    monkeypatch.setattr(
        runtime,
        "SETTINGS",
        runtime.Settings(
            transport=httpx.MockTransport(fake.handle), sleep=_no_sleep, module_dirs=[MODULES], checkout=ROOT
        ),
    )
    saved = {name: (command.handler, command.inputs, command.extra_inputs, command.configure)
             for name, command in engine.COMMANDS.items()}
    stages = list(engine.COMMANDS["reset"].stages)
    runtime.register()
    status.register()
    reset.register()
    connect.register()
    try:
        yield fake
    finally:
        runtime.unregister()
        for name, (handler, inputs, extra_inputs, configure) in saved.items():
            command = engine.COMMANDS[name]
            command.handler = handler
            command.inputs = inputs
            command.extra_inputs = extra_inputs
            command.configure = configure
        engine.COMMANDS["reset"].stages[:] = stages


def deployment(tmp_path: Path) -> Deployment:
    """The saved deployment, with the values ``deployment.toml`` holds."""

    return open_deployment("default", tmp_path / "deployments")


def record(tmp_path: Path, *resources: str) -> None:
    """Add to the deployment's ``created`` record the way a stage does."""

    served = deployment(tmp_path)
    saved = served.values.get(engine.CREATED_KEY)
    known = [str(item) for item in saved] if isinstance(saved, list) else []
    save_deployment(served, {engine.CREATED_KEY: list(dict.fromkeys([*known, *resources]))})


def _seed_rest(tmp_path: Path, gateway: CliGateway, roles: Sequence[str]) -> None:
    """Add the REST plane state the REST stage writes: the token, the secrets, the settings."""

    served = deployment(tmp_path)
    key = _key()
    gateway._add_token(rest.REST_TOKEN_NAME, key, SETUP_LEVEL, secure=False)
    write_secret(served, rest.REST_TOKEN_SECRET, security.token_secret(rest.REST_TOKEN_NAME, key))
    for role in roles:
        name, value = ROLE_SECRETS[role]
        write_secret(served, rest.static_token_secret(role), f"{name}:{value}")
    save_deployment(
        served,
        {
            "rest_mutation_classes": "config,control",
            "rest_target_allowlist": "*",
            "rest_project_writer": "on",
            rest.RECORD_KEY: ["wildcard_target_allowlist:config,control"],
        },
    )
    record(tmp_path, rest.REST_TOKEN_RECORD)


def healthy(tmp_path: Path, gateway: CliGateway, *, roles: Sequence[str] = ("analysis", "engineer")) -> Deployment:
    """A deployment ``setup`` finished on both planes."""

    code, document, _ = setup(tmp_path, *ACCEPT)
    assert code == 0, document
    _seed_rest(tmp_path, gateway, roles)
    return deployment(tmp_path)


def run_json(argv: list[str], root: Path, **kwargs: Any) -> tuple[int, dict[str, Any], str]:
    kwargs.setdefault("interactive", False)
    stream = io.StringIO()
    code = engine.run([*argv, "--json"], root=root, reporter=JsonReporter(argv[0], stream), **kwargs)
    raw = stream.getvalue()
    return code, json.loads(raw), raw


def status_argv(tmp_path: Path, *extra: str) -> list[str]:
    return ["status", "--deployment", "default", "--gateway-token-file", str(_token_file(tmp_path)), *extra]


def steps(document: dict[str, Any]) -> dict[str, str]:
    return {step["step"]: step["status"] for step in document["steps"]}


def reasons(document: dict[str, Any]) -> dict[str, str]:
    return {step["step"]: step["reason"] for step in document["steps"]}


def next_actions(document: dict[str, Any]) -> dict[str, str]:
    return {step["step"]: step["next_action"] for step in document["steps"]}


# ------------------------------------------------------------------------ status


def test_status_on_a_healthy_deployment_reports_every_check_ok(tmp_path: Path, gateway: CliGateway) -> None:
    healthy(tmp_path, gateway)

    code, document, raw = run_json(status_argv(tmp_path), tmp_path / "deployments", token_probe=_probe)

    assert code == 0, document
    assert document["error"] is None
    assert set(steps(document).values()) == {"OK"}, document
    assert "gateway" in steps(document) and "module" in steps(document) and "bundle" in steps(document)
    for role in ("analysis", "engineer"):
        assert f"token {role}" in steps(document)
        assert f"server config {role}" in steps(document)
        assert f"endpoint {role}" in steps(document)
    assert steps(document)["rest token"] == "OK" and steps(document)["rest settings"] == "OK"
    assert reasons(document)["module"].startswith(f"build {runtime.PINNED_MODULE_BUILD}")
    assert reasons(document)["runtime policy"].startswith("the served document is the generated dev one")
    assert "lists 1 approved alias" in reasons(document)["named-query registry"]
    assert reasons(document)["leftover files"] == "no file is left for a role the deployment does not serve"
    # No secret of either plane reaches the report.
    for name in ("runtime-analysis.secret", "rest-gateway-token.secret", "rest-analysis-token.secret"):
        secret = (deployment(tmp_path).directory / name).read_text(encoding="utf-8").strip()
        assert secret not in raw
        assert secret.partition(":")[2] not in raw
    assert SETUP_KEY not in raw


def test_status_on_a_lost_secret_names_the_cause_and_the_next_action(
    tmp_path: Path, gateway: CliGateway
) -> None:
    healthy(tmp_path, gateway)
    (deployment(tmp_path).directory / "runtime-analysis.secret").unlink()

    code, document, _ = run_json(status_argv(tmp_path), tmp_path / "deployments", token_probe=_probe)

    assert code == 1
    assert steps(document)["token analysis"] == "FAILED"
    assert "its secret file" in reasons(document)["token analysis"]
    assert "--recreate-tokens" in next_actions(document)["token analysis"]
    # Reporting does not repair: the Gateway token is still there.
    assert "ignition-mcp-analysis" in gateway.tokens
    assert steps(document)["endpoint analysis"] == "SKIPPED"
    assert steps(document)["token engineer"] == "OK"
    assert document["error"] is None


def test_status_on_a_hand_edited_server_config_reports_it(tmp_path: Path, gateway: CliGateway) -> None:
    healthy(tmp_path, gateway)
    gateway.configs["analysis"]["config"]["permissions"] = {
        "type": "AllOf",
        "securityLevels": [{"name": "Authenticated", "children": [{"name": "Setup", "children": []}]}],
    }

    code, document, _ = run_json(status_argv(tmp_path), tmp_path / "deployments", token_probe=_probe)

    assert code == 1
    assert steps(document)["server config analysis"] == "FAILED"
    assert "changed by hand" in reasons(document)["server config analysis"]
    assert "--yes" in next_actions(document)["server config analysis"]
    assert steps(document)["server config engineer"] == "OK"


def test_status_reports_a_leftover_secret_for_a_role_the_deployment_dropped(
    tmp_path: Path, gateway: CliGateway
) -> None:
    healthy(tmp_path, gateway)
    directory = deployment(tmp_path).directory
    (directory / "runtime-engineer.secret").write_text("old:key\n", encoding="utf-8")
    os.chmod(directory / "runtime-engineer.secret", 0o600)
    save_deployment(deployment(tmp_path), {"roles": ["analysis"]})

    code, document, _ = run_json(status_argv(tmp_path), tmp_path / "deployments", token_probe=_probe)

    assert code == 1
    assert "runtime-engineer.secret" in reasons(document)["leftover files"]
    assert next_actions(document)["leftover files"].startswith("rm ")
    assert "engineer" not in reasons(document)["deployment"]


def test_status_names_one_cause_and_skips_the_checks_that_need_it(
    tmp_path: Path, gateway: CliGateway
) -> None:
    healthy(tmp_path, gateway)

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to the Gateway", request=request)

    runtime.SETTINGS.transport = httpx.MockTransport(refuse)
    code, document, _ = run_json(status_argv(tmp_path), tmp_path / "deployments", token_probe=_probe)

    assert code == 1
    assert steps(document)["deployment"] == "OK"
    assert steps(document)["gateway"] == "FAILED"
    assert next_actions(document)["gateway"]
    assert steps(document)["module"] == "SKIPPED"
    assert reasons(document)["module"] == reasons(document)["bundle"]
    assert steps(document)["rest static tokens"] == "OK"
    assert steps(document)["leftover files"] == "OK"


# ----------------------------------------------------------------------- connect


class FakeRunner:
    """Records the client commands and answers them with a fixed exit code."""

    def __init__(self, code: int = 0) -> None:
        self.code = code
        self.calls: list[list[str]] = []

    def __call__(self, argv: Sequence[str]) -> connect.RunResult:
        self.calls.append(list(argv))
        return connect.RunResult(code=self.code)


@pytest.fixture
def client_binaries(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[FakeRunner]:
    runner = FakeRunner()
    monkeypatch.setattr(connect, "SETTINGS", connect.Settings(which=lambda name: f"/usr/bin/{name}", run=runner, home=tmp_path))
    yield runner


def test_connect_registers_both_endpoints_with_claude_and_never_prints_a_token(
    tmp_path: Path, gateway: CliGateway, client_binaries: FakeRunner
) -> None:
    healthy(tmp_path, gateway)

    code, document, raw = run_json(
        ["connect", "analysis", "--client", "claude", "--deployment", "default"], tmp_path / "deployments"
    )

    assert code == 0, document
    adds = [call for call in client_binaries.calls if call[1:3] == ["mcp", "add"]]
    assert [call[3:7] for call in adds] == [
        ["--transport", "http", "ignition-runtime-analysis", f"{URL}/data/mcp/analysis"],
        ["--transport", "http", "ignition-rest-analysis", "http://127.0.0.1:8000/mcp"],
    ]
    assert all("--scope" in call and "user" in call for call in adds)
    runtime_header = adds[0][adds[0].index("--header") + 1]
    rest_header = adds[1][adds[1].index("--header") + 1]
    token = (deployment(tmp_path).directory / "runtime-analysis.secret").read_text(encoding="utf-8").strip()
    assert runtime_header == f"{connect.RUNTIME_HEADER}: {token}"
    assert rest_header == f"Authorization: Bearer ignition-mcp-analysis:{ROLE_SECRETS['analysis'][1]}"
    # Nothing is removed for a name this client does not hold.
    assert [call[1:3] for call in client_binaries.calls if len(call) > 2 and call[2] == "remove"] == []
    for secret in (token, token.partition(":")[2], ROLE_SECRETS["analysis"][1], REST_KEY):
        assert secret not in raw
    assert "Claude Code" in reasons(document)["client"]


def test_connect_offers_a_client_that_is_not_installed_but_refuses_it(
    tmp_path: Path, gateway: CliGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    healthy(tmp_path, gateway)
    runner = FakeRunner()
    monkeypatch.setattr(
        connect,
        "SETTINGS",
        connect.Settings(
            which=lambda name: "/usr/bin/claude" if name == "claude" else None, run=runner, home=tmp_path
        ),
    )

    code, document, _ = run_json(
        ["connect", "analysis", "--client", "codex", "--deployment", "default"], tmp_path / "deployments"
    )

    assert code == 2
    assert document["error"]["code"] == "invalid_input"
    assert "Codex is not installed" in document["error"]["message"]
    assert runner.calls == []
    # The wizard shows every client as a choice, refuses the one that is not installed
    # with its reason, and asks again. ``--json`` never prompts (D32 section 3), so this
    # half reads the wizard's own output.
    prompter = ScriptedPrompter(["codex", "none"])
    buffer = io.StringIO()
    code = engine.run(
        ["connect", "analysis", "--deployment", "default"],
        root=tmp_path / "deployments",
        reporter=RichReporter("connect", Console(file=buffer, width=200, color_system=None)),
        interactive=True,
        prompter=prompter,
    )
    printed = buffer.getvalue()
    assert code == 0, printed
    assert prompter.choices[0] == ["claude", "codex", "none"]
    assert any("Codex is not installed" in said for said in prompter.said)
    assert "SKIPPED" in printed and "--client none" in printed
    assert runner.calls == []


class ScriptedPrompter:
    """Answers each question with the next scripted answer, in order."""

    def __init__(self, answers: Sequence[str]) -> None:
        self._answers = list(answers)
        self.choices: list[list[str]] = []
        self.said: list[str] = []

    def _next(self) -> str:
        return self._answers.pop(0)

    def say(self, message: str) -> None:
        self.said.append(message)

    def text(self, question: str, default: str | None = None) -> str:
        return self._next()

    def secret(self, question: str) -> str:
        return self._next()

    def select(self, question: str, choices: Sequence[str], default: str | None = None) -> str:
        self.choices.append(list(choices))
        return self._next()

    def confirm(self, question: str) -> bool:
        return self._next() == "y"


def test_connect_writes_the_codex_configuration_with_the_headers(
    tmp_path: Path, gateway: CliGateway, client_binaries: FakeRunner
) -> None:
    healthy(tmp_path, gateway)

    code, document, raw = run_json(
        ["connect", "engineer", "--client", "codex", "--deployment", "default"], tmp_path / "deployments"
    )

    assert code == 0, document
    path = tmp_path / ".codex" / "config.toml"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    text = path.read_text(encoding="utf-8")
    token = (deployment(tmp_path).directory / "runtime-engineer.secret").read_text(encoding="utf-8").strip()
    assert f'[mcp_servers.ignition-runtime-engineer]\nurl = "{URL}/data/mcp/engineer"' in text
    assert f'[mcp_servers.ignition-runtime-engineer.http_headers]\n"X-Ignition-API-Token" = "{token}"' in text
    assert "[mcp_servers.ignition-rest-engineer.http_headers]" in text
    bearer = f"Bearer ignition-mcp-engineer:{ROLE_SECRETS['engineer'][1]}"
    assert f'"Authorization" = {json.dumps(bearer)}' in text
    assert client_binaries.calls == []
    for secret in (token, token.partition(":")[2], ROLE_SECRETS["engineer"][1]):
        assert secret not in raw
    assert str(path) in reasons(document)["client"]
    assert str(path) in reasons(document)["codex ignition-runtime-engineer"]
    # A second run finds the entries in place and reports no change.
    code, document, _ = run_json(
        ["connect", "engineer", "--client", "codex", "--deployment", "default"], tmp_path / "deployments"
    )
    assert code == 0, document
    assert [step["status"] for step in document["steps"] if step["step"].startswith("codex")] == ["OK", "OK"]


def test_connect_with_none_registers_nothing(
    tmp_path: Path, gateway: CliGateway, client_binaries: FakeRunner
) -> None:
    healthy(tmp_path, gateway)

    code, document, _ = run_json(
        ["connect", "analysis", "--client", "none", "--deployment", "default"], tmp_path / "deployments"
    )

    assert code == 0, document
    assert steps(document)["client"] == "SKIPPED"
    assert client_binaries.calls == []
    assert not (tmp_path / ".codex").exists()


def test_connect_refuses_a_role_the_deployment_does_not_serve(
    tmp_path: Path, gateway: CliGateway, client_binaries: FakeRunner
) -> None:
    healthy(tmp_path, gateway, roles=("analysis",))
    save_deployment(deployment(tmp_path), {"roles": ["analysis"]})

    code, document, _ = run_json(
        ["connect", "engineer", "--client", "claude", "--deployment", "default"], tmp_path / "deployments"
    )

    assert code == 2
    assert document["error"]["code"] == "deployment_unreadable"
    assert client_binaries.calls == []


# ------------------------------------------------------------------------- reset


def test_reset_leaves_nothing_behind(tmp_path: Path, gateway: CliGateway) -> None:
    directory = healthy(tmp_path, gateway).directory

    code, document, raw = run_json(
        ["reset", "--yes", "--gateway-token-file", str(_token_file(tmp_path)), "--deployment", "default"],
        tmp_path / "deployments",
        token_probe=_probe,
    )

    assert code == 0, document
    assert "gateway_restart" in {item["item"] for item in document["accepted"]}
    assert steps(document)["plan reset"] == "OK"
    reset_steps = {step["status"] for step in document["steps"] if step["step"].startswith("reset ")}
    assert reset_steps == {"CHANGED"}, document
    assert gateway.tokens.keys() == {"setup"}
    assert gateway.configs == {}
    assert gateway.projects == {}
    assert gateway.policy_provider is None and gateway.provider is False and gateway.tags == {}
    assert gateway.module_build is None
    assert gateway.uninstalled == [{"uninstall": ["com.inductiveautomation.mcp"]}]
    authenticated = next(node for node in gateway.levels if node["name"] == "Authenticated")
    assert [child["name"] for child in authenticated["children"]] == ["Setup"]
    assert not directory.exists()
    assert SETUP_KEY not in raw


def test_reset_needs_the_acceptance_and_the_confirmation_before_any_write(
    tmp_path: Path, gateway: CliGateway
) -> None:
    directory = healthy(tmp_path, gateway).directory
    gateway.requests.clear()

    code, document, _ = run_json(
        ["reset", "--gateway-token-file", str(_token_file(tmp_path)), "--deployment", "default"],
        tmp_path / "deployments",
        token_probe=_probe,
    )

    assert code == 2
    assert document["error"]["code"] == "acceptance_required"
    assert "gateway_restart" in document["error"]["message"]
    assert "--yes" in document["error"]["message"]
    assert len(document["plan"]) > 4
    assert gateway.writes == []
    assert "ignition-mcp-analysis" in gateway.tokens
    assert directory.exists()


def test_reset_is_refused_in_prod(tmp_path: Path, gateway: CliGateway) -> None:
    directory = healthy(tmp_path, gateway).directory
    save_deployment(deployment(tmp_path), {"environment": "prod"})
    gateway.requests.clear()

    code, document, _ = run_json(
        ["reset", "--yes", "--gateway-token-file", str(_token_file(tmp_path)), "--deployment", "default"],
        tmp_path / "deployments",
        token_probe=_probe,
    )

    assert code == 2
    assert document["error"]["code"] == "invalid_input"
    assert "refused outside dev" in document["error"]["message"]
    assert gateway.requests == []
    assert directory.exists()


def test_reset_leaves_a_project_setup_did_not_deploy_alone(tmp_path: Path, gateway: CliGateway) -> None:
    healthy(tmp_path, gateway)
    gateway.projects[runtime.PROJECT] = _unmanaged_project()
    gateway.requests.clear()

    code, document, _ = run_json(
        ["reset", "--yes", "--gateway-token-file", str(_token_file(tmp_path)), "--deployment", "default"],
        tmp_path / "deployments",
        token_probe=_probe,
    )

    assert code == 0, document
    assert any("leave the project" in change["change"] for change in document["plan"])
    assert steps(document)["reset bundle project"] == "SKIPPED"
    assert runtime.PROJECT in gateway.projects
    assert "ignition-mcp-analysis" not in gateway.tokens


def _unmanaged_project() -> bytes:
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("project.json", json.dumps({"title": "mine", "description": "hand made"}))
    return buffer.getvalue()


# --------------------------------------------- ownership: only what setup created


def test_reset_keeps_a_pre_existing_security_level_and_module(
    tmp_path: Path, gateway: CliGateway
) -> None:
    """The Module and the analysis level are there before setup, so setup creates neither."""

    gateway.module_build = runtime.PINNED_MODULE_BUILD
    authenticated = next(node for node in gateway.levels if node["name"] == "Authenticated")
    authenticated["children"].append({"name": "IgnitionMcpAnalysis", "children": []})

    code, document, _ = setup(tmp_path, *ACCEPT)
    assert code == 0, document
    _seed_rest(tmp_path, gateway, ("analysis", "engineer"))
    created = deployment(tmp_path).values[engine.CREATED_KEY]
    assert isinstance(created, list)
    assert runtime.MODULE_RECORD not in created
    assert "level:Authenticated/IgnitionMcpAnalysis" not in created
    assert "level:Authenticated/IgnitionMcpEngineer" in created
    gateway.requests.clear()

    code, document, _ = run_json(
        ["reset", "--yes", "--gateway-token-file", str(_token_file(tmp_path)), "--deployment", "default"],
        tmp_path / "deployments",
        token_probe=_probe,
    )

    assert code == 0, document
    changes = " | ".join(change["change"] for change in document["plan"])
    assert "leave the Security Level Authenticated/IgnitionMcpAnalysis in place" in changes
    assert f"leave MCP Module build {runtime.PINNED_MODULE_BUILD} in place" in changes
    assert steps(document)["reset module"] == "OK"
    assert steps(document)["reset analysis"] == "CHANGED"
    assert steps(document)["reset security levels"] == "CHANGED"
    # The Gateway keeps what it had: the Module is not uninstalled and nothing restarts.
    assert gateway.module_build == runtime.PINNED_MODULE_BUILD
    assert gateway.uninstalled == []
    assert not any(path.endswith("/restart-tasks/restart") for _, path in gateway.requests)
    authenticated = next(node for node in gateway.levels if node["name"] == "Authenticated")
    assert [child["name"] for child in authenticated["children"]] == ["Setup", "IgnitionMcpAnalysis"]
    # What setup did create goes.
    assert "ignition-mcp-analysis" not in gateway.tokens
    assert gateway.projects == {} and gateway.policy_provider is None


def test_reset_keeps_a_server_config_setup_left_alone(tmp_path: Path, gateway: CliGateway) -> None:
    """A matching Server Config survives setup, so it is not recorded and not deleted."""

    # The Module is there first, so the Server Config resource type is observable.
    gateway.module_build = runtime.PINNED_MODULE_BUILD
    gateway.configs["analysis"] = _pre_existing_server_config("analysis")

    code, document, _ = setup(tmp_path, *ACCEPT)
    assert code == 0, document
    _seed_rest(tmp_path, gateway, ("analysis", "engineer"))
    created = deployment(tmp_path).values[engine.CREATED_KEY]
    assert isinstance(created, list)
    assert runtime.ROLES["analysis"].config_record not in created
    assert runtime.ROLES["engineer"].config_record in created
    gateway.requests.clear()

    code, document, _ = run_json(
        ["reset", "--yes", "--gateway-token-file", str(_token_file(tmp_path)), "--deployment", "default"],
        tmp_path / "deployments",
        token_probe=_probe,
    )

    assert code == 0, document
    assert any(
        "leave the Server Config analysis in place" in change["change"] for change in document["plan"]
    )
    assert "analysis" in gateway.configs and "engineer" not in gateway.configs
    assert "ignition-mcp-analysis" not in gateway.tokens
    assert reasons(document)["reset analysis"] == "removed API token ignition-mcp-analysis"


def _pre_existing_server_config(role_name: str) -> dict[str, Any]:
    """A Server Config exactly as ``setup`` would write it, so setup leaves it alone."""

    targets = runtime.RuntimeTargets(
        bundle=runtime.read_bundle(ROOT), endpoint=runtime._endpoint_of(URL), insecure_channel=True
    )
    role = runtime.ROLES[role_name]
    inputs = runtime.role_inputs(targets, role)
    return {
        "name": role.server_config,
        "collection": "core",
        "enabled": True,
        "signature": "pre-existing-1",
        "config": docs.desired_server_config(inputs, None, role.permissions()),
    }


# --------------------------------------------------------- the write gate


def test_the_deployment_directory_is_removed_through_the_gate(tmp_path: Path, gateway: CliGateway) -> None:
    directory = tmp_path / "deployments" / "default"
    save_deployment(
        Deployment("default", directory), {"environment": "dev", "gateway_url": URL, "roles": ["analysis"]}
    )
    (directory / "notes.txt").write_text("keep me\n", encoding="utf-8")
    gateway.module_build = None
    ctx = _context(tmp_path)

    plan = reset.plan(ctx)

    assert plan.changes == [f"delete the deployment directory {directory} (2 entries)"]
    with pytest.raises(engine.WriteBeforeAcceptance):
        reset.apply(_apply_context(tmp_path), plan)
    assert (directory / "notes.txt").is_file()


def _resolved(tmp_path: Path) -> Any:
    resolved = engine.Resolved(deployment(tmp_path))
    resolved.values["gateway_url"] = URL
    resolved.secrets["gateway_token"] = EngineSecret(SETUP_TOKEN)
    return resolved


def _context(tmp_path: Path) -> engine.Context:
    return engine.Context(
        command="reset",
        args=argparse.Namespace(),
        specs=[],
        resolved=_resolved(tmp_path),
        prompter=None,
        reporter=JsonReporter("reset", io.StringIO()),
        accept_flags=engine.AcceptFlags(True, False, False),
    )


def _apply_context(tmp_path: Path) -> engine.ApplyContext:
    ctx = _context(tmp_path)
    return engine.ApplyContext(
        command=ctx.command,
        args=ctx.args,
        specs=ctx.specs,
        resolved=ctx.resolved,
        prompter=ctx.prompter,
        reporter=ctx.reporter,
        accept_flags=ctx.accept_flags,
    )


# ------------------------------------------------------------- the environment


def test_reset_refuses_anything_but_a_dev_environment(tmp_path: Path, gateway: CliGateway) -> None:
    directory = healthy(tmp_path, gateway).directory
    argv = ["reset", "--yes", "--gateway-token-file", str(_token_file(tmp_path)), "--deployment", "default"]

    for value in ("production", "prod", ""):
        save_deployment(deployment(tmp_path), {"environment": value})
        gateway.requests.clear()
        code, document, _ = run_json(argv, tmp_path / "deployments", token_probe=_probe)
        assert code == 2, document
        assert document["error"]["code"] == "invalid_input"
        assert "refused outside dev" in document["error"]["message"]
        assert gateway.requests == []
        assert directory.exists()

    path = directory / "deployment.toml"
    path.write_text(
        "".join(line for line in path.read_text(encoding="utf-8").splitlines(keepends=True)
                if not line.startswith("environment")),
        encoding="utf-8",
    )
    code, document, _ = run_json(argv, tmp_path / "deployments", token_probe=_probe)
    assert code == 2, document
    assert "no environment at all" in document["error"]["message"]
    assert gateway.requests == []


# --------------------------------------------------------- client collisions


def _claude_entries(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))["mcpServers"]


def test_connect_keeps_a_different_claude_entry_without_acceptance(
    tmp_path: Path, gateway: CliGateway, client_binaries: FakeRunner
) -> None:
    healthy(tmp_path, gateway)
    path = tmp_path / ".claude.json"
    other = {
        "type": "http",
        "url": "http://another-gateway:9000/data/mcp/analysis",
        "headers": {"X-Ignition-API-Token": "another:key"},
    }
    path.write_text(
        json.dumps({"mcpServers": {"ignition-runtime-analysis": other, "someone-else": {"url": "http://x/mcp"}}}),
        encoding="utf-8",
    )

    code, document, raw = run_json(
        ["connect", "analysis", "--client", "claude", "--deployment", "default"], tmp_path / "deployments"
    )

    assert code == 2
    assert document["error"]["code"] == "acceptance_required"
    assert "overwrite_hand_edit" in document["error"]["message"]
    assert "ignition-runtime-analysis" in document["error"]["message"]
    assert "its url" in document["error"]["message"]
    assert client_binaries.calls == []
    assert _claude_entries(path)["ignition-runtime-analysis"] == other
    assert other["headers"]["X-Ignition-API-Token"] not in raw


def test_connect_restores_the_claude_entry_when_the_registration_fails(
    tmp_path: Path, gateway: CliGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    healthy(tmp_path, gateway)
    path = tmp_path / ".claude.json"
    other = {
        "type": "http",
        "url": "http://another-gateway:9000/data/mcp/analysis",
        "headers": {"X-Ignition-API-Token": "another:key"},
    }
    path.write_text(json.dumps({"mcpServers": {"ignition-runtime-analysis": other}}), encoding="utf-8")
    calls: list[list[str]] = []
    state = {"fail": True}

    def run(argv: Sequence[str]) -> connect.RunResult:
        calls.append(list(argv))
        if list(argv[2:3]) == ["add"] and argv[5] == "ignition-runtime-analysis" and state["fail"]:
            state["fail"] = False
            return connect.RunResult(code=1, stderr="the name is already in use")
        return connect.RunResult(code=0)

    monkeypatch.setattr(
        connect,
        "SETTINGS",
        connect.Settings(which=lambda name: f"/usr/bin/{name}", run=run, home=tmp_path),
    )

    code, document, raw = run_json(
        ["connect", "analysis", "--client", "claude", "--yes", "--deployment", "default"],
        tmp_path / "deployments",
    )

    assert code == 1
    assert steps(document)["claude ignition-runtime-analysis"] == "FAILED"
    assert "was restored" in reasons(document)["claude ignition-runtime-analysis"]
    assert [item["item"] for item in document["accepted"]] == ["overwrite_hand_edit"]
    added = [call[5] for call in calls if call[2:3] == ["add"]]
    # The new content is registered under a temporary name first, then under the real name.
    assert added == ["ignition-runtime-analysis-ignition-mcp-tmp", "ignition-runtime-analysis"]
    restore = [call for call in calls if call[2:3] == ["add-json"]]
    assert len(restore) == 1
    assert restore[0][3] == "ignition-runtime-analysis"
    assert json.loads(restore[0][4]) == other
    assert other["headers"]["X-Ignition-API-Token"] not in raw


def test_connect_replaces_a_different_codex_entry_only_with_acceptance(
    tmp_path: Path, gateway: CliGateway, client_binaries: FakeRunner
) -> None:
    healthy(tmp_path, gateway)
    path = tmp_path / ".codex" / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    kept = '# a comment someone wrote\n[mcp_servers.other]\nurl = "http://other/mcp"\n\n'
    other = (
        "[mcp_servers.ignition-runtime-analysis]\n"
        'url = "http://another-gateway:9000/data/mcp/analysis"\n\n'
        "[mcp_servers.ignition-runtime-analysis.http_headers]\n"
        '"X-Ignition-API-Token" = "another:key"\n'
    )
    path.write_text(kept + other, encoding="utf-8")
    argv = ["connect", "analysis", "--client", "codex", "--deployment", "default"]

    code, document, _ = run_json(argv, tmp_path / "deployments")

    assert code == 2 and document["error"]["code"] == "acceptance_required"
    assert path.read_text(encoding="utf-8") == kept + other

    code, document, raw = run_json([*argv, "--yes"], tmp_path / "deployments")

    assert code == 0, document
    assert [item["item"] for item in document["accepted"]] == ["overwrite_hand_edit"]
    text = path.read_text(encoding="utf-8")
    assert text.startswith(kept)
    assert "http://another-gateway:9000/data/mcp/analysis" not in text
    assert "another:key" not in raw
    token = (deployment(tmp_path).directory / "runtime-analysis.secret").read_text(encoding="utf-8").strip()
    assert f'"X-Ignition-API-Token" = {json.dumps(token)}' in text
    assert "replaced a different entry of the same name" in reasons(document)["codex ignition-runtime-analysis"]


# ------------------------------------------------------- status next actions


def test_status_gives_the_unreadable_checks_a_specific_next_action(
    tmp_path: Path, gateway: CliGateway
) -> None:
    healthy(tmp_path, gateway)
    gateway.levels = "not a tree"
    gateway.tokens["setup"]["config"]["profile"].pop("securityLevels")

    code, document, _ = run_json(status_argv(tmp_path), tmp_path / "deployments", token_probe=_probe)

    assert code == 1
    assert steps(document)["level analysis"] == "FAILED"
    assert next_actions(document)["level analysis"] == f"curl -sS {URL}{gw.SECURITY_LEVELS_PATH}"
    assert steps(document)["rest token"] == "FAILED"
    key = next_actions(document)["rest token"]
    assert key.startswith("curl -sS ") and key.endswith("/data/api/v1/resources/find/ignition/api-token/setup")
