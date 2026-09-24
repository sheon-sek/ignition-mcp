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
from ignition_rest_mcp.cli.setup import connect, reset, rest, runtime, status
from ignition_rest_mcp.cli.setup_native import documents as docs
from ignition_rest_mcp.cli.setup_native import security
from test_phase7_setup_runtime import (
    ACCEPT,
    MODULES,
    ROOT,
    SETUP_KEY,
    SETUP_LEVEL,
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
    # A name that is already registered is removed before the add.
    assert [call[3] for call in client_binaries.calls if call[1:3] == ["mcp", "remove"]] == [
        "ignition-runtime-analysis",
        "ignition-rest-analysis",
    ]
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
