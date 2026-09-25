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
from ignition_rest_mcp.cli.gateway_ops import security
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
PROPERTIES = "ignition/security-properties"
#: The General Settings the recorded Gateway serves: the setup key's level under
#: every entry, as D32 section 9 asks, and one entry that lets everyone through.
GENERAL_SETTINGS = {
    **{
        entry: {"type": "AnyOf", "securityLevels": SETUP_GRANT}
        for entry in ("accessPermissions", "readPermissions", "writePermissions", "designerPermissions")
    },
    "createProjectPermissions": {"type": "AllOf", "securityLevels": []},
    "userInactivityTimeout": 10,
}
REST_LEAF = ("Authenticated", "IgnitionMcpRest")


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
        for flag in ("--recreate-tokens", "--provision-security-levels"):
            if flag not in parser._option_string_actions:
                parser.add_argument(flag, action="store_true")

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
        recorded.seed_resource(PROPERTIES, "security-properties", config=json.loads(json.dumps(GENERAL_SETTINGS)))
        yield recorded


def general_settings(gateway: RecordedGateway) -> dict[str, Any]:
    config: dict[str, Any] = gateway.resource(PROPERTIES, "security-properties")["config"]
    return config


def holds_rest_level(permission: dict[str, Any]) -> bool:
    return any(
        node["name"] == REST_LEAF[0] and any(child["name"] == REST_LEAF[1] for child in node.get("children", []))
        for node in permission["securityLevels"]
    )


def token_levels(gateway: RecordedGateway) -> list[tuple[str, ...]]:
    grant = gateway.resource("ignition/api-token", "ignition-mcp-rest")["config"]["profile"]["securityLevels"]
    return [(node["name"], child["name"]) for node in grant for child in node["children"]]


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


def statuses_reason(document: dict[str, Any], name: str) -> str:
    return str(next(step["reason"] for step in document["steps"] if step["step"] == name))


def test_setup_then_start_gives_each_role_its_own_scopes(
    tmp_path: Path, gateway: RecordedGateway, cli: None, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    root = tmp_path / "deployments"
    code, document, _ = run_json(setup_argv(tmp_path, gateway, "--yes"), root)

    assert code == 0, document
    assert statuses(document) == {
        "plan rest": "OK",
        "setup key": "CHANGED",
        "rest level": "CHANGED",
        "rest general settings": "CHANGED",
        "rest token ignition-mcp-rest": "CHANGED",
        "rest static token ignition-mcp-analysis": "CHANGED",
        "rest static token ignition-mcp-engineer": "CHANGED",
        "rest settings": "CHANGED",
    }
    assert [item["item"] for item in document["accepted"]] == ["wildcard_target_allowlist"]
    assert "with Authenticated/IgnitionMcpRest as its only level" in statuses_reason(document, "rest token ignition-mcp-rest")
    assert token_levels(gateway) == [REST_LEAF]
    authenticated = next(node for node in gateway.security_levels() or [] if node["name"] == "Authenticated")
    assert [child["name"] for child in authenticated["children"]] == ["Roles", "IgnitionMcpRest"]
    # dev: read and write. Access, Designer and every other setting stay as they were.
    settings = general_settings(gateway)
    assert holds_rest_level(settings["readPermissions"]) and holds_rest_level(settings["writePermissions"])
    for key, value in GENERAL_SETTINGS.items():
        if key not in ("readPermissions", "writePermissions"):
            assert settings[key] == value, key

    deployment = open_deployment("default", root)
    assert deployment.values["created"] == [
        "rest-level:Authenticated/IgnitionMcpRest",
        "rest-permission:readPermissions",
        "rest-permission:writePermissions",
        "rest-token:ignition-mcp-rest",
    ]
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
    assert code == 2
    assert document["error"]["code"] == "acceptance_required"
    assert document["error"]["next_action"].endswith("--provision-security-levels")
    assert write_requests(gateway, "POST") == [] and write_requests(gateway, "PUT") == []

    argv = setup_argv(tmp_path, gateway, "--environment", "prod", "--provision-security-levels", "--yes")
    code, document, _ = run_json(argv, root)

    assert code == 0, document
    assert document["accepted"] == []
    settings = general_settings(gateway)
    assert holds_rest_level(settings["readPermissions"])
    assert settings["writePermissions"] == GENERAL_SETTINGS["writePermissions"]
    assert token_levels(gateway) == [REST_LEAF]
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
        "remove Authenticated/IgnitionMcpRest from the General Settings entries writePermissions "
        "(environment prod: read access only)",
        "narrow rest-mutation-classes from config,control to none (environment dev to prod)",
        "narrow rest-target-allowlist from * to none (environment dev to prod)",
        "narrow rest-project-writer from on to off (environment dev to prod)",
        "record the accepted REST risks: none",
    ]
    deployment = open_deployment("default", root)
    assert (deployment.values["rest_mutation_classes"], deployment.values["rest_target_allowlist"]) == ("none", "none")
    assert (deployment.values["rest_project_writer"], deployment.values["rest_accepted"]) == ("off", [])
    assert statuses(document)["rest general settings"] == "CHANGED"
    assert not holds_rest_level(general_settings(gateway)["writePermissions"])
    assert holds_rest_level(general_settings(gateway)["readPermissions"])
    assert "rest-permission:writePermissions" not in deployment.values["created"]

    # Widening back into dev asks for the '*' allowlist again, and adds the write grant.
    code, document, _ = run_json(setup_argv(tmp_path, gateway, "--environment", "dev"), root)
    assert code == 2
    assert "wildcard_target_allowlist" in document["error"]["message"]
    code, document, _ = run_json(setup_argv(tmp_path, gateway, "--environment", "dev", "--yes"), root)
    assert code == 0, document
    assert statuses(document)["rest general settings"] == "CHANGED"
    assert holds_rest_level(general_settings(gateway)["writePermissions"])
    assert "rest-permission:writePermissions" in open_deployment("default", root).values["created"]


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
    assert not any("/api-token" in request["path"] for request in write_requests(gateway, "PUT"))

    code, document, _ = run_json(setup_argv(tmp_path, gateway, "--yes"), root)
    assert code == 0, document
    assert statuses(document)["rest token ignition-mcp-rest"] == "CHANGED"
    assert [item["item"] for item in document["accepted"]] == ["overwrite_hand_edit"]
    restored = gateway.resource("ignition/api-token", "ignition-mcp-rest")["config"]
    assert token_levels(gateway) == [REST_LEAF]
    assert restored["settings"] == served["config"]["settings"], "the key must not change"


def test_a_token_on_the_setup_key_level_moves_to_the_dedicated_level(
    tmp_path: Path, gateway: RecordedGateway, cli: None,
) -> None:
    """A Phase 7 deployment's token copied the setup key's level; the next run moves it."""

    root = tmp_path / "deployments"
    assert run_json(setup_argv(tmp_path, gateway, "--yes"), root)[0] == 0
    served = gateway.resource("ignition/api-token", "ignition-mcp-rest")
    config = {**served["config"], "profile": {**served["config"]["profile"], "securityLevels": SETUP_GRANT}}
    gateway.change_resource_out_of_band("ignition/api-token", "ignition-mcp-rest", config=config)

    code, document, _ = run_json(setup_argv(tmp_path, gateway, "--yes"), root)

    assert code == 0, document
    assert document["accepted"] == []
    assert statuses(document)["rest token ignition-mcp-rest"] == "CHANGED"
    reason = {step["step"]: step["reason"] for step in document["steps"]}["rest token ignition-mcp-rest"]
    assert reason == (
        "moved from the setup key's level Authenticated/Administrator to Authenticated/IgnitionMcpRest; "
        "the key is unchanged"
    )
    assert token_levels(gateway) == [REST_LEAF]


def test_an_all_of_entry_is_refused_before_any_write(tmp_path: Path, gateway: RecordedGateway, cli: None) -> None:
    """Adding a level to an AllOf entry would lock out everyone who holds only the others."""

    settings = general_settings(gateway)
    settings["writePermissions"] = {"type": "AllOf", "securityLevels": SETUP_GRANT}

    code, document, _ = run_json(setup_argv(tmp_path, gateway, "--yes"), tmp_path / "deployments")

    assert code == 1, document
    assert document["error"]["code"] == "step_failed"
    assert "writePermissions is AllOf" in document["error"]["message"]
    assert document["error"]["next_action"].endswith("/data/api/v1/resources/singleton/ignition/security-properties")
    assert write_requests(gateway, "POST") == [] and write_requests(gateway, "PUT") == []


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


def test_the_general_settings_edit_never_narrows_anyone_else() -> None:
    everyone = {"type": "AnyOf", "securityLevels": []}
    assert security.permission_with_level(everyone, "readPermissions", REST_LEAF) == (None, "")

    only_rest = {"type": "AnyOf", "securityLevels": [{"name": "Authenticated", "children": [
        {"name": "IgnitionMcpRest", "children": []}]}]}
    changed, reason = security.permission_without_level(only_rest, "readPermissions", REST_LEAF)
    assert changed is None and "lets every token through" in reason

    shared = {"type": "AnyOf", "securityLevels": [*SETUP_GRANT, {"name": "Public", "children": []}]}
    added, _ = security.permission_with_level(shared, "writePermissions", REST_LEAF)
    assert added is not None and security.permission_holds(added, REST_LEAF)
    # The removal drops the level and, when that leaves it childless, its parent, so a
    # bare Authenticated never ends up granting every signed-in user.
    removed, _ = security.permission_without_level(
        {"type": "AnyOf", "securityLevels": [only_rest["securityLevels"][0], {"name": "Public", "children": []}]},
        "writePermissions", REST_LEAF,
    )
    assert removed == {"type": "AnyOf", "securityLevels": [{"name": "Public", "children": []}]}
    assert security.permission_without_level(added, "writePermissions", REST_LEAF)[0] == shared


def test_an_operator_grant_made_after_the_plan_is_not_recorded(
    tmp_path: Path, gateway: RecordedGateway, cli: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An operator adds the level to writePermissions between the plan and the edit."""

    create_level = rest.add_rest_level

    async def then_operator_grants(ctx: engine.Context, writer: Any) -> None:
        await create_level(ctx, writer)
        config = json.loads(json.dumps(general_settings(gateway)))
        config["writePermissions"]["securityLevels"][0]["children"].append({"name": REST_LEAF[1], "children": []})
        gateway.change_resource_out_of_band(PROPERTIES, "security-properties", config=config)

    monkeypatch.setattr(rest, "add_rest_level", then_operator_grants)
    code, document, _ = run_json(setup_argv(tmp_path, gateway, "--yes"), tmp_path / "deployments")

    assert code == 0, document
    created = open_deployment("default", tmp_path / "deployments").values["created"]
    assert "rest-permission:readPermissions" in created
    assert "rest-permission:writePermissions" not in created
    assert holds_rest_level(general_settings(gateway)["writePermissions"])
