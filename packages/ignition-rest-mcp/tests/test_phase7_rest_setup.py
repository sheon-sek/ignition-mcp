"""``setup`` on the REST plane and ``start`` (issue #75, D32 sections 5, 6, 8 and 9).

``setup`` runs against the recorded Gateway. ``start`` runs with a fake ``SERVE``
that drives the real server through a test client, so no test binds a port or sleeps.
"""

from __future__ import annotations

import argparse
import dataclasses
import io
import json
import logging
import os
import stat
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from ignition_rest_mcp.cli.engine import main as engine
from ignition_rest_mcp.cli.engine.deployment import open_deployment, save_deployment
from ignition_rest_mcp.cli.engine.report import JsonReporter
from ignition_rest_mcp.cli.setup import rest, start
from ignition_rest_mcp.config import Settings
from ignition_rest_mcp.server import create_server
from phase4_fixtures import Session, envelope, structured, write_requests

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tests/harness"))

from recorded_gateway import API_TOKEN, RecordedGateway  # noqa: E402

PIPELINE = "project:MCP_CI_ALARM:/pipeline:Notify"
EVENT = "6f1c8e2a-0f3f-4a44-9d5f-1c2b3a4d5e6f"
SETUP_GRANT = [{"name": "Authenticated", "children": [{"name": "Administrator", "children": []}]}]


@pytest.fixture
def cli() -> Iterator[None]:
    """Registers the REST stage and ``start``, and restores the engine afterwards.

    ``--recreate-tokens`` belongs to the Runtime stage (#74); this fixture adds it to
    the parser so the REST stage can be tested on its own.
    """

    setup = engine.COMMANDS["setup"]
    start_command = engine.COMMANDS["start"]
    saved = (setup.configure, list(setup.stages), setup.handler)
    saved_start = (start_command.handler, start_command.configure, start_command.words)
    original = setup.configure

    def configure(parser: argparse.ArgumentParser) -> None:
        if original is not None:
            original(parser)
        if "--recreate-tokens" not in parser._option_string_actions:
            parser.add_argument("--recreate-tokens", action="store_true")

    setup.configure = configure
    rest.register()
    start.register()
    try:
        yield
    finally:
        setup.configure, setup.stages[:], setup.handler = saved
        start_command.handler, start_command.configure, start_command.words = saved_start


@pytest.fixture
def gateway() -> Iterator[RecordedGateway]:
    with RecordedGateway() as recorded:
        recorded.seed_resource(
            "ignition/api-token",
            API_TOKEN.partition(":")[0],
            config={
                "profile": {"type": "basic-token", "secureChannelRequired": False, "securityLevels": SETUP_GRANT},
                "settings": {"tokenHash": "unused"},
            },
        )
        yield recorded


def run_json(argv: list[str], root: Path) -> tuple[int, dict[str, Any], str]:
    stream = io.StringIO()
    code = engine.run([*argv, "--json"], root=root, reporter=JsonReporter(argv[0], stream), interactive=False)
    return code, json.loads(stream.getvalue()), stream.getvalue()


def setup_argv(tmp_path: Path, gateway: RecordedGateway, *extra: str) -> list[str]:
    token = tmp_path / "setup-key.txt"
    if not token.exists():
        token.write_text(API_TOKEN + "\n", encoding="utf-8")
        token.chmod(0o600)
    return ["setup", "--gateway-url", gateway.base_url, "--gateway-token-file", str(token), *extra]


def statuses(document: dict[str, Any]) -> dict[str, str]:
    return {step["step"]: step["status"] for step in document["steps"]}


def test_setup_then_start_gives_each_role_its_own_scopes(
    tmp_path: Path, gateway: RecordedGateway, cli: None, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    root = tmp_path / "deployments"
    code, document, _ = run_json(setup_argv(tmp_path, gateway, "--yes"), root)

    assert code == 0, document
    assert statuses(document) == {
        "plan rest": "OK",
        "rest token ignition-mcp-rest": "CHANGED",
        "rest static token ignition-mcp-analysis": "CHANGED",
        "rest static token ignition-mcp-engineer": "CHANGED",
        "rest settings": "CHANGED",
    }
    assert [item["item"] for item in document["accepted"]] == ["wildcard_target_allowlist"]
    assert "Authenticated/Administrator" in document["steps"][1]["reason"]
    created = [json.loads(request["body"]) for request in write_requests(gateway, "POST")
               if request["path"] == "/data/api/v1/resources/ignition/api-token"]
    assert created[0][0]["name"] == "ignition-mcp-rest"
    assert created[0][0]["config"]["profile"]["securityLevels"] == SETUP_GRANT

    deployment = open_deployment("default", root)
    assert deployment.values["rest_mutation_classes"] == "config,control"
    assert deployment.values["rest_target_allowlist"] == "*"
    assert deployment.values["rest_project_writer"] == "on"
    secrets = [rest.static_token_value(deployment, role) for role in ("analysis", "engineer")]
    rest_key = (deployment.directory / "rest-gateway-token.secret").read_text(encoding="utf-8").strip()
    for name in ("rest-gateway-token", "rest-analysis-token", "rest-engineer-token"):
        assert stat.S_IMODE(deployment.secret_path(name).stat().st_mode) == 0o600

    rerun_code, rerun, _ = run_json(setup_argv(tmp_path, gateway), root)
    assert rerun_code == 0 and rerun["plan"] == [], rerun
    assert set(statuses(rerun).values()) == {"OK"}

    served: list[Settings] = []

    def fake_serve(settings: Settings, report: start.HealthReport) -> None:
        served.append(settings)
        # The recorded Gateway knows one key only, so the server under test uses it
        # in place of the ignition-mcp-rest key it was given.
        recorded = dataclasses.replace(settings, gateway_api_token=API_TOKEN, watcher_interval_seconds=3600.0)
        gateway.seed_pipeline(PIPELINE, [{"alarmEventId": EVENT, "status": "Running", "millis": 1200}])
        with TestClient(create_server(recorded).http_app()) as http:
            health = http.get("/health/ready")
            report(health.status_code, health.json())
            arguments = {"path": PIPELINE, "alarmEventId": EVENT}
            refused = Session(http, secrets[0]).call("alarm_pipeline_cancel", arguments)
            assert envelope(refused)["code"] == "permission_denied"
            allowed = Session(http, secrets[1]).call("alarm_pipeline_cancel", arguments)
            assert structured(allowed)["alarmEventId"] == EVENT

    monkeypatch.setattr(start, "SERVE", fake_serve)
    monkeypatch.setenv("IGNITION_MCP_AUTH_MODE", "none")
    caplog.set_level(logging.DEBUG)
    code, started, output = run_json(["start"], root)

    assert code == 0, started
    assert statuses(started) == {
        "deployment": "OK", "risks": "OK", "health": "OK", "endpoint analysis": "OK",
        "endpoint engineer": "OK", "serve": "OK", "stop": "OK",
    }
    endpoints = {step["step"]: step["reason"] for step in started["steps"]}
    assert endpoints["risks"] == (
        "active: wildcard_target_allowlist:config,control (accepted by setup, recorded as rest_accepted)"
    )
    assert started["accepted"] == []
    assert "http://127.0.0.1:8000/mcp" in endpoints["endpoint analysis"]
    assert f"{gateway.base_url}/data/mcp/engineer" in endpoints["endpoint engineer"]
    settings = served[0]
    assert (settings.auth_mode, settings.bind_host, settings.bind_port) == ("static-token", "127.0.0.1", 8000)
    assert {token.name: token.scopes for token in settings.static_tokens} == {
        "ignition-mcp-analysis": ("ignition.read",),
        "ignition-mcp-engineer": ("ignition.read", "ignition.config", "ignition.control"),
    }
    assert settings.gateway_api_token == rest_key
    assert (settings.config_mutation_enabled, settings.control_mutation_enabled) == (True, True)
    assert settings.admin_mutation_enabled is False
    assert settings.mutation_targets["alarm_pipeline_cancel"] == ("*",)
    assert settings.project_writer_enabled and settings.gateway_id == "default"
    assert "IGNITION_MCP_GATEWAY_API_TOKEN" not in os.environ
    assert os.environ["IGNITION_MCP_AUTH_MODE"] == "none"
    for secret in (*secrets, rest_key, rest_key.partition(":")[2], API_TOKEN):
        assert secret not in output
        assert secret not in caplog.text


def test_the_admin_class_needs_its_own_acceptance(tmp_path: Path, gateway: RecordedGateway, cli: None) -> None:
    root = tmp_path / "deployments"
    argv = setup_argv(
        tmp_path, gateway, "--rest-mutation-classes", "config,control,admin", "--rest-target-allowlist", "none",
    )
    code, document, _ = run_json(argv, root)

    assert code == 2
    assert document["error"]["code"] == "acceptance_required"
    assert "admin_mutation_class" in document["error"]["message"]
    assert not (root / "default").exists()
    assert write_requests(gateway, "POST") == []

    code, document, _ = run_json([*argv, "--yes"], root)
    assert code == 0, document
    assert [item["item"] for item in document["accepted"]] == ["admin_mutation_class"]


def test_prod_turns_every_mutation_class_off(tmp_path: Path, gateway: RecordedGateway, cli: None) -> None:
    root = tmp_path / "deployments"
    code, document, _ = run_json(setup_argv(tmp_path, gateway, "--environment", "prod", "--yes"), root)

    assert code == 0, document
    assert document["accepted"] == []
    deployment = open_deployment("default", root)
    assert deployment.values["roles"] == ["analysis"]
    assert not deployment.secret_path("rest-engineer-token").exists()
    environment = start.server_environment(deployment, "127.0.0.1", 8000)
    assert environment["IGNITION_MCP_CONFIG_MUTATION_ENABLED"] == "false"
    assert environment["IGNITION_MCP_CONTROL_MUTATION_ENABLED"] == "false"
    assert environment["IGNITION_MCP_PROJECT_WRITER_ENABLED"] == "false"
    assert "IGNITION_MCP_MUTATION_TARGETS" not in environment
    assert list(json.loads(environment["IGNITION_MCP_STATIC_TOKENS"])) == ["ignition-mcp-analysis"]


def test_a_non_loopback_bind_needs_acceptance(
    tmp_path: Path, gateway: RecordedGateway, cli: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "deployments"
    assert run_json(setup_argv(tmp_path, gateway, "--yes"), root)[0] == 0
    calls: list[Settings] = []
    monkeypatch.setattr(start, "SERVE", lambda settings, report: calls.append(settings))

    code, document, _ = run_json(["start", "--bind", "0.0.0.0:8000"], root)

    assert code == 2
    assert document["error"]["code"] == "acceptance_required"
    assert "non_loopback_bind" in document["error"]["message"]
    assert calls == []
    environment = start.server_environment(open_deployment("default", root), "0.0.0.0", 8000)
    assert environment["IGNITION_MCP_DEPLOYMENT_PROFILE"] == "trusted-internal"


def test_start_uses_the_bind_the_deployment_saved(
    tmp_path: Path, gateway: RecordedGateway, cli: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``bind`` saved in ``deployment.toml`` decides where ``start`` listens, like ``--bind``."""

    root = tmp_path / "deployments"
    assert run_json(setup_argv(tmp_path, gateway, "--yes"), root)[0] == 0
    save_deployment(open_deployment("default", root), {"bind": "127.0.0.1:8123"})
    served: list[Settings] = []

    def fake_serve(settings: Settings, report: start.HealthReport) -> None:
        served.append(settings)
        report(200, {"registryState": "OK", "storageReady": True})

    monkeypatch.setattr(start, "SERVE", fake_serve)

    code, document, _ = run_json(["start"], root)

    assert code == 0, document
    assert (served[0].bind_host, served[0].bind_port) == ("127.0.0.1", 8123)
    endpoints = {step["step"]: step["reason"] for step in document["steps"]}
    assert "http://127.0.0.1:8123/mcp" in endpoints["endpoint analysis"]


def test_start_before_setup_names_setup(tmp_path: Path, cli: None) -> None:
    code, document, _ = run_json(["start"], tmp_path / "deployments")

    assert code == 2
    assert document["error"]["code"] == "deployment_unreadable"
    assert document["error"]["next_action"] == "ignition-mcp setup --deployment default"


def test_a_lost_rest_key_is_recreated_only_with_recreate_tokens(
    tmp_path: Path, gateway: RecordedGateway, cli: None,
) -> None:
    root = tmp_path / "deployments"
    assert run_json(setup_argv(tmp_path, gateway, "--yes"), root)[0] == 0
    lost = open_deployment("default", root).secret_path("rest-gateway-token")
    lost.unlink()

    code, document, _ = run_json(setup_argv(tmp_path, gateway, "--yes"), root)
    assert code == 2
    assert document["error"]["code"] == "missing_input"
    assert "--recreate-tokens" in document["error"]["next_action"]
    assert not lost.exists() and write_requests(gateway, "DELETE") == []

    code, document, _ = run_json(setup_argv(tmp_path, gateway, "--recreate-tokens", "--yes"), root)
    assert code == 0, document
    assert statuses(document)["rest token ignition-mcp-rest"] == "CHANGED"
    deletes = write_requests(gateway, "DELETE")
    assert len(deletes) == 1 and "/api-token/ignition-mcp-rest/" in deletes[0]["path"]
    assert lost.exists()


def test_the_default_wildcard_allowlist_needs_acceptance(tmp_path: Path, gateway: RecordedGateway, cli: None) -> None:
    root = tmp_path / "deployments"
    code, document, _ = run_json(setup_argv(tmp_path, gateway), root)

    assert code == 2
    assert document["error"]["code"] == "acceptance_required"
    assert "wildcard_target_allowlist" in document["error"]["message"]
    assert not (root / "default").exists()
    assert write_requests(gateway, "POST") == []


def test_an_environment_change_resets_the_saved_rest_settings(
    tmp_path: Path, gateway: RecordedGateway, cli: None,
) -> None:
    root = tmp_path / "deployments"
    assert run_json(setup_argv(tmp_path, gateway, "--yes"), root)[0] == 0

    code, document, _ = run_json(setup_argv(tmp_path, gateway, "--environment", "prod", "--yes"), root)

    assert code == 0, document
    assert [entry["change"] for entry in document["plan"]] == [
        "narrow rest-mutation-classes from config,control to none (environment dev to prod)",
        "narrow rest-target-allowlist from * to none (environment dev to prod)",
        "narrow rest-project-writer from on to off (environment dev to prod)",
        "record the accepted REST risks: none",
    ]
    deployment = open_deployment("default", root)
    assert (deployment.values["rest_mutation_classes"], deployment.values["rest_target_allowlist"]) == ("none", "none")
    assert (deployment.values["rest_project_writer"], deployment.values["rest_accepted"]) == ("off", [])

    # Widening back into dev asks for the '*' allowlist again.
    code, document, _ = run_json(setup_argv(tmp_path, gateway, "--environment", "dev"), root)
    assert code == 2
    assert "wildcard_target_allowlist" in document["error"]["message"]


def test_a_hand_edited_rest_token_is_restored_after_acceptance(
    tmp_path: Path, gateway: RecordedGateway, cli: None,
) -> None:
    root = tmp_path / "deployments"
    assert run_json(setup_argv(tmp_path, gateway, "--yes"), root)[0] == 0
    served = gateway.resource("ignition/api-token", "ignition-mcp-rest")
    widened = [{"name": "Authenticated", "children": [{"name": "Everything", "children": []}]}]
    config = {**served["config"], "profile": {**served["config"]["profile"], "securityLevels": widened}}
    gateway.change_resource_out_of_band("ignition/api-token", "ignition-mcp-rest", config=config)

    code, document, _ = run_json(setup_argv(tmp_path, gateway), root)
    assert code == 2
    assert "overwrite_hand_edit" in document["error"]["message"]
    assert write_requests(gateway, "PUT") == []

    code, document, _ = run_json(setup_argv(tmp_path, gateway, "--yes"), root)
    assert code == 0, document
    assert statuses(document)["rest token ignition-mcp-rest"] == "CHANGED"
    assert [item["item"] for item in document["accepted"]] == ["overwrite_hand_edit"]
    restored = gateway.resource("ignition/api-token", "ignition-mcp-rest")["config"]
    assert restored["profile"]["securityLevels"] == SETUP_GRANT
    assert restored["settings"] == served["config"]["settings"], "the key must not change"


def test_start_asks_for_a_risk_that_setup_never_recorded(
    tmp_path: Path, gateway: RecordedGateway, cli: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "deployments"
    assert run_json(setup_argv(tmp_path, gateway, "--yes"), root)[0] == 0
    save_deployment(open_deployment("default", root), {"rest_mutation_classes": "config,control,admin"})
    calls: list[Settings] = []
    monkeypatch.setattr(start, "SERVE", lambda settings, report: calls.append(settings))

    code, document, _ = run_json(["start"], root)
    assert code == 2
    assert document["error"]["code"] == "acceptance_required"
    assert "admin_mutation_class" in document["error"]["message"]
    assert "wildcard_target_allowlist" in document["error"]["message"]
    assert calls == []

    code, document, _ = run_json(["start", "--yes"], root)
    assert code == 0, document
    assert [item["item"] for item in document["accepted"]] == ["wildcard_target_allowlist", "admin_mutation_class"]
    assert calls[0].admin_mutation_enabled is True
    risks = {step["step"]: step["reason"] for step in document["steps"]}["risks"]
    assert "admin_mutation_class (accepted in this run)" in risks
