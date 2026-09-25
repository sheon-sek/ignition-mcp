"""The ``ignition-mcp`` command engine (issue #73, D32 sections 3, 4, 6 and 9).

A scripted prompter stands in for the terminal and a fake probe for the Gateway, so
no test needs either.
"""

from __future__ import annotations

import asyncio
import io
import json
import shlex
import stat
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from rich.console import Console

from ignition_rest_mcp.cli.engine import main as engine
from ignition_rest_mcp.cli.engine.deployment import open_deployment, save_deployment, write_secret
from ignition_rest_mcp.cli.engine.errors import CliError, ErrorCode
from ignition_rest_mcp.cli.engine.prompter import PlainPrompter
from ignition_rest_mcp.cli.engine.report import JsonReporter, RichReporter, Status
from ignition_rest_mcp.cli.engine.resolve import InputSpec, Kind, Needed, Risk, probe_gateway_token, resolve

GOOD_TOKEN = "setup:R29vZEtleUdvb2RLZXk"
BAD_TOKEN = "setup:QmFkS2V5QmFkS2V5"


class ScriptedPrompter:
    """Answers each question with the next scripted answer, in order."""

    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.asked: list[str] = []
        self.said: list[str] = []

    def _next(self, question: str) -> Any:
        self.asked.append(question)
        assert self.answers, f"no scripted answer left for {question!r}"
        return self.answers.pop(0)

    def say(self, message: str) -> None:
        self.said.append(message)

    def text(self, question: str, default: str | None = None) -> str:
        return str(self._next(question)) or (default or "")

    def secret(self, question: str) -> str:
        return str(self._next(question))

    def select(self, question: str, choices: Any, default: str | None = None) -> str:
        answer = str(self._next(question))
        assert answer in choices
        return answer

    def confirm(self, question: str) -> bool:
        return bool(self._next(question))


def probe(url: str, token: str) -> str:
    return "" if token == GOOD_TOKEN else "the Gateway does not know this key"


class Seen(list[engine.Context]):
    """The contexts the test ``setup`` stage saw, and the items it asks to accept."""

    needed: list[Needed]


@pytest.fixture
def seen() -> Iterator[Seen]:
    """Registers a ``setup`` stage that records its context, accepts, then saves."""

    contexts = Seen()
    contexts.needed = []

    def plan(ctx: engine.Context) -> engine.Plan:
        contexts.append(ctx)
        return engine.Plan(changes=["save deployment.toml"], needed=list(contexts.needed))

    def apply(ctx: engine.ApplyContext, plan: engine.Plan) -> None:
        with ctx.reporter.step("deployment", "saving deployment.toml") as end:
            ctx.save()
            end.set(Status.CHANGED, "saved deployment.toml")

    stage = engine.register_stage(engine.Stage("deployment", plan, apply))
    try:
        yield contexts
    finally:
        engine.SETUP_STAGES.remove(stage)


def token_file(tmp_path: Path, secret: str = GOOD_TOKEN) -> Path:
    path = tmp_path / "token.txt"
    path.write_text(secret + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def run_json(argv: list[str], root: Path, **kwargs: Any) -> tuple[int, dict[str, Any], str]:
    stream = io.StringIO()
    code = engine.run([*argv, "--json"], root=root, reporter=JsonReporter(argv[0], stream), **kwargs)
    return code, json.loads(stream.getvalue()), stream.getvalue()


def rich_reporter(command: str) -> tuple[RichReporter, io.StringIO]:
    buffer = io.StringIO()
    return RichReporter(command, Console(file=buffer, width=200, color_system=None)), buffer


# ----------------------------------------------------------------- parity


def test_one_line_and_wizard_resolve_the_same_inputs(tmp_path: Path, seen: Seen) -> None:
    one_line_root = tmp_path / "one-line"
    code, document, raw = run_json(
        [
            "setup",
            "--gateway-url", "http://gw:8088",
            "--environment", "dev",
            "--roles", "analysis,engineer",
            "--gateway-token-file", str(token_file(tmp_path)),
            "--yes",
        ],
        one_line_root,
        interactive=False,
        token_probe=probe,
    )
    assert code == 0, document
    assert [step["status"] for step in document["steps"]] == ["OK", "CHANGED", "CHANGED"]
    assert document["plan"] == [{"stage": "deployment", "change": "save deployment.toml"}]
    assert GOOD_TOKEN not in raw

    wizard_root = tmp_path / "wizard"
    reporter, buffer = rich_reporter("setup")
    prompter = ScriptedPrompter("http://gw:8088", "dev", "analysis,engineer", GOOD_TOKEN, True)
    code = engine.run(
        ["setup"], root=wizard_root, prompter=prompter, interactive=True, token_probe=probe, reporter=reporter
    )
    assert code == 0
    assert prompter.answers == [] and prompter.said == []
    one_line, wizard = seen
    assert one_line.resolved.values == wizard.resolved.values == {
        "gateway_url": "http://gw:8088",
        "environment": "dev",
        "roles": "analysis,engineer",
    }
    assert one_line.resolved.secrets["gateway_token"].reveal() == wizard.resolved.secrets["gateway_token"].reveal()
    saved = [(one_line_root / "default" / "deployment.toml"), (wizard_root / "default" / "deployment.toml")]
    assert saved[0].read_text() == saved[1].read_text()
    assert "roles = [\"analysis\", \"engineer\"]" in saved[0].read_text()

    # The wizard prints the one-line command with the same values and no secret.
    output = buffer.getvalue()
    assert GOOD_TOKEN not in output and GOOD_TOKEN.split(":")[1] not in output
    words = shlex.split(reporter.equivalent)
    assert words[:2] == ["ignition-mcp", "setup"]
    assert words[words.index("--gateway-url") + 1] == "http://gw:8088"
    assert words[words.index("--roles") + 1] == "analysis,engineer"
    assert words[words.index("--gateway-token-file") + 1] == str(wizard_root / "default" / "gateway-token.secret")


def test_equivalent_command_reruns_to_the_same_result(tmp_path: Path, seen: Seen) -> None:
    reporter, _ = rich_reporter("setup")
    prompter = ScriptedPrompter("prod", "analysis", GOOD_TOKEN, True)
    code = engine.run(
        ["setup", "--gateway-url", "https://gw:8043"],
        root=tmp_path / "a",
        prompter=prompter,
        interactive=True,
        token_probe=probe,
        reporter=reporter,
    )
    assert code == 0
    assert prompter.answers == [] and prompter.said == []
    words = shlex.split(reporter.equivalent.replace("FILE", str(token_file(tmp_path))))
    code, document, _ = run_json(words[1:], tmp_path / "b", interactive=False, token_probe=probe)
    assert code == 0, document
    assert seen[0].resolved.values == seen[1].resolved.values
    assert seen[1].resolved.values["environment"] == "prod"


def test_a_saved_deployment_answers_instead_of_asking(tmp_path: Path, seen: Seen) -> None:
    deployment = save_deployment(
        open_deployment("lab", tmp_path),
        {"gateway_url": "http://lab:8088", "environment": "prod", "roles": ["analysis"]},
    )
    write_secret(deployment, "gateway-token", GOOD_TOKEN)
    code, document, raw = run_json(["setup", "--deployment", "lab", "--yes"], tmp_path, interactive=False, token_probe=probe)
    assert code == 0, document
    assert seen[0].resolved.values["roles"] == "analysis"
    assert GOOD_TOKEN not in raw
    if sys.platform != "win32":
        assert stat.S_IMODE(deployment.secret_path("gateway-token").stat().st_mode) == 0o600


def test_setup_saves_a_pasted_key_for_later_commands(tmp_path: Path, seen: Seen) -> None:
    prompter = ScriptedPrompter("dev", "analysis", GOOD_TOKEN, True)
    reporter, _ = rich_reporter("setup")
    code = engine.run(
        ["setup", "--gateway-url", "http://gw:8088"],
        root=tmp_path,
        prompter=prompter,
        interactive=True,
        token_probe=probe,
        reporter=reporter,
    )
    assert code == 0
    path = open_deployment("default", tmp_path).secret_path("gateway-token")
    assert path.read_text(encoding="utf-8").strip() == GOOD_TOKEN
    assert str(path) in reporter.equivalent and "FILE" not in reporter.equivalent
    # A re-run finds the saved key and asks for nothing.
    code, document, _ = run_json(["setup", "--yes"], tmp_path, interactive=False, token_probe=probe)
    assert code == 0, document
    assert seen[1].resolved.secrets["gateway_token"].reveal() == GOOD_TOKEN


def test_setup_replaces_a_saved_key_with_the_one_it_was_given(tmp_path: Path, seen: Seen) -> None:
    deployment = open_deployment("default", tmp_path)
    write_secret(deployment, "gateway-token", "old:T2xkS2V5T2xkS2V5T2xk")
    words = ["setup", "--gateway-url", "http://gw:8088", "--gateway-token-file", str(token_file(tmp_path)), "--yes"]
    code, document, _ = run_json(words, tmp_path, interactive=False, token_probe=probe)
    assert code == 0, document
    assert deployment.secret_path("gateway-token").read_text(encoding="utf-8").strip() == GOOD_TOKEN
    if sys.platform != "win32":
        assert stat.S_IMODE(deployment.secret_path("gateway-token").stat().st_mode) == 0o600


def test_a_dry_run_saves_no_key(tmp_path: Path, seen: Seen) -> None:
    words = ["setup", "--gateway-url", "http://gw:8088", "--gateway-token-file", str(token_file(tmp_path)), "--dry-run"]
    code, document, _ = run_json(words, tmp_path, interactive=False, token_probe=probe)
    assert code == 0, document
    assert not open_deployment("default", tmp_path).secret_path("gateway-token").exists()


# ------------------------------------------------------------ refusals


def test_non_tty_missing_values_fail_before_any_write(tmp_path: Path, seen: Seen) -> None:
    code, document, _ = run_json(["setup", "--roles", "analysis"], tmp_path, interactive=False, token_probe=probe)
    assert code == 2
    assert document["error"]["code"] == "missing_input"
    assert "--gateway-url" in document["error"]["message"]
    assert "--gateway-token-file" in document["error"]["message"]
    assert document["error"]["next_action"] == "ignition-mcp setup --help"
    assert seen == []
    assert not tmp_path.exists() or list(tmp_path.iterdir()) == []


def test_non_tty_rejected_token_fails_with_its_code(tmp_path: Path, seen: Seen) -> None:
    code, document, raw = run_json(
        ["setup", "--gateway-url", "http://gw:8088", "--gateway-token-file", str(token_file(tmp_path, BAD_TOKEN))],
        tmp_path / "root",
        interactive=False,
        token_probe=probe,
    )
    assert code == 2
    assert document["error"]["code"] == "gateway_token_rejected"
    assert BAD_TOKEN not in raw
    assert seen == []


@pytest.mark.parametrize(
    ("flags", "missing"),
    [
        (["--yes"], "--accept-certificate"),
        (["--accept-certificate"], "--yes"),
    ],
)
def test_missing_acceptance_in_one_line_fails_before_any_write(
    tmp_path: Path, seen: Seen, flags: list[str], missing: str
) -> None:
    seen.needed = [Needed(Risk.CERTIFICATE), Needed(Risk.WILDCARD_ALLOWLIST)]
    root = tmp_path / "root"
    code, document, _ = run_json(
        ["setup", "--gateway-url", "http://gw:8088", "--gateway-token-file", str(token_file(tmp_path)), *flags],
        root,
        interactive=False,
        token_probe=probe,
    )
    assert code == 2
    assert document["error"]["code"] == "acceptance_required"
    assert missing in document["error"]["message"]
    assert not (root / "default" / "deployment.toml").exists()


def test_accepted_items_appear_in_the_json_report(
    tmp_path: Path, seen: Seen
) -> None:
    seen.needed = [Needed(Risk.EULA, "module 5.3.0"), Needed(Risk.RESTART)]
    code, document, _ = run_json(
        [
            "setup", "--gateway-url", "http://gw:8088", "--gateway-token-file", str(token_file(tmp_path)),
            "--accept-eula", "--yes",
        ],
        tmp_path / "root",
        interactive=False,
        token_probe=probe,
    )
    assert code == 0, document
    assert document["accepted"] == [
        {"item": "module_eula", "detail": "module 5.3.0", "how": "flag"},
        {"item": "gateway_restart", "detail": "", "how": "flag"},
    ]


def test_wizard_declining_an_item_stops_before_any_write(
    tmp_path: Path, seen: Seen
) -> None:
    seen.needed = [Needed(Risk.ADMIN_CLASS)]
    reporter, buffer = rich_reporter("setup")
    prompter = ScriptedPrompter("http://gw:8088", "dev", "analysis,engineer", GOOD_TOKEN, False)
    code = engine.run(
        ["setup"], root=tmp_path, prompter=prompter, interactive=True, token_probe=probe, reporter=reporter
    )
    assert code == 2
    assert "acceptance_required" in buffer.getvalue()
    assert not (tmp_path / "default" / "deployment.toml").exists()


# ------------------------------------------------------------ ask again


def test_rejected_token_is_asked_again_and_the_run_continues(
    tmp_path: Path, seen: Seen
) -> None:
    reporter, _ = rich_reporter("setup")
    prompter = ScriptedPrompter("not a url", "http://gw:8088", "dev", "analysis", "no-colon", BAD_TOKEN, GOOD_TOKEN, True)
    code = engine.run(
        ["setup"], root=tmp_path, prompter=prompter, interactive=True, token_probe=probe, reporter=reporter
    )
    assert code == 0
    assert prompter.answers == []
    assert any("absolute http or https URL" in line for line in prompter.said)
    assert any("<name>:<key>" in line for line in prompter.said)
    assert any("does not know this key" in line for line in prompter.said)
    assert seen[0].resolved.secrets["gateway_token"].reveal() == GOOD_TOKEN


def test_wrong_path_is_asked_again(tmp_path: Path) -> None:
    module = tmp_path / "module.modl"
    module.write_bytes(b"PK")
    spec = InputSpec("module_file", "--module-file", "Module file", kind=Kind.PATH)
    prompter = ScriptedPrompter(str(module))
    resolved = resolve(
        [spec], {"module_file": str(tmp_path / "missing.modl")}, open_deployment("x", tmp_path), prompter
    )
    assert resolved.values["module_file"] == str(module)
    assert "does not exist" in prompter.said[0]


# ------------------------------------------------------------ reporter


def test_json_steps_carry_codes_and_never_a_secret() -> None:
    stream = io.StringIO()
    reporter = JsonReporter("status", stream)
    reporter.hide(GOOD_TOKEN)
    reporter.start("gateway", "reading gateway-info")
    reporter.end("gateway", Status.OK, "reachable")
    reporter.end(
        "token",
        Status.FAILED,
        f"the Gateway refused {GOOD_TOKEN}",
        next_action="ignition-mcp setup --deployment default",
        code=ErrorCode.STEP_FAILED.value,
    )
    reporter.fail(CliError(ErrorCode.STEP_FAILED, "one step failed"))
    assert reporter.finish() == 1
    document = json.loads(stream.getvalue())
    assert [step["status"] for step in document["steps"]] == ["OK", "FAILED"]
    assert document["steps"][1]["code"] == "step_failed"
    assert document["steps"][1]["reason"] == "the Gateway refused <secret>"
    assert document["error"]["code"] == "step_failed"
    assert GOOD_TOKEN not in stream.getvalue()
    with pytest.raises(ValueError, match="next action"):
        reporter.end("other", Status.FAILED, "no next action")


def test_placeholder_commands_report_not_implemented(tmp_path: Path) -> None:
    code, document, _ = run_json(["start"], tmp_path, interactive=False)
    assert code == 1
    assert document["error"]["code"] == "not_implemented"


def test_plain_prompter_asks_again_on_bad_answers() -> None:
    lines = iter(["9\n", "2\n", "maybe\n", "y\n"])
    output = io.StringIO()
    prompter = PlainPrompter(read_line=lambda: next(lines), output=output)
    assert prompter.select("Environment", ["dev", "prod"], default="dev") == "prod"
    assert prompter.confirm("Allow it?") is True
    assert "Enter a number from 1 to 2." in output.getvalue()
    assert "Answer y or n." in output.getvalue()


# ------------------------------------------------- review round 1 fixes


@pytest.fixture
def stages() -> Iterator[list[engine.Stage]]:
    """Stages a test registers; removed again afterwards."""

    added: list[engine.Stage] = []
    yield added
    for stage in added:
        engine.SETUP_STAGES.remove(stage)


def one_line(tmp_path: Path, *extra: str) -> list[str]:
    return ["setup", "--gateway-url", "http://gw:8088", "--gateway-token-file", str(token_file(tmp_path)), *extra]


def test_a_later_stage_acceptance_stops_the_run_before_an_earlier_stage_writes(
    tmp_path: Path, stages: list[engine.Stage]
) -> None:
    applied: list[str] = []

    def first_apply(ctx: engine.ApplyContext, plan: engine.Plan) -> None:
        applied.append("first")
        ctx.save()

    stages.append(engine.register_stage(engine.Stage("first", lambda ctx: engine.Plan(["write"]), first_apply)))
    stages.append(
        engine.register_stage(
            engine.Stage(
                "second",
                lambda ctx: engine.Plan(["install module"], [Needed(Risk.CERTIFICATE)]),
                lambda ctx, plan: applied.append("second"),
            )
        )
    )
    root = tmp_path / "root"
    code, document, _ = run_json(one_line(tmp_path, "--yes"), root, interactive=False, token_probe=probe)
    assert code == 2
    assert document["error"]["code"] == "acceptance_required"
    assert "--accept-certificate" in document["error"]["message"]
    assert [entry["stage"] for entry in document["plan"]] == ["first", "second"]
    assert applied == []
    assert not (root / "default" / "deployment.toml").exists()

    # --dry-run shows the plan and stops, even when everything is accepted.
    code, document, _ = run_json(
        one_line(tmp_path, "--yes", "--accept-certificate", "--dry-run"), root, interactive=False, token_probe=probe
    )
    assert code == 0 and applied == [] and len(document["plan"]) == 2

    # A wizard that does not confirm the plan writes nothing either.
    reporter, buffer = rich_reporter("setup")
    prompter = ScriptedPrompter(True, False)
    code = engine.run(
        one_line(tmp_path, "--environment", "dev", "--roles", "analysis"),
        root=root,
        prompter=prompter,
        interactive=True,
        token_probe=probe,
        reporter=reporter,
    )
    assert prompter.answers == []
    assert code == 2 and "not_confirmed" in buffer.getvalue() and applied == []


def test_a_plan_step_has_no_write_capability(tmp_path: Path, stages: list[engine.Stage]) -> None:
    received: list[engine.Context] = []

    def plan(ctx: engine.Context) -> engine.Plan:
        received.append(ctx)
        ctx.save()  # type: ignore[attr-defined]
        return engine.Plan()

    stages.append(engine.register_stage(engine.Stage("eager", plan, lambda ctx, plan: None)))
    root = tmp_path / "root"
    code, document, _ = run_json(one_line(tmp_path, "--yes"), root, interactive=False, token_probe=probe)
    assert code == 1
    assert document["error"]["code"] == "unexpected_error"
    assert document["steps"][0]["status"] == "FAILED"
    assert not (root / "default" / "deployment.toml").exists()
    ctx = received[0]
    assert not isinstance(ctx, engine.ApplyContext)
    for name in ("save", "write_secret", "gateway_writer", "allow_writes", "_gate"):
        assert not hasattr(ctx, name), name


def test_a_gateway_write_before_the_gate_opens_is_refused(tmp_path: Path, stages: list[engine.Stage]) -> None:
    captured: list[engine.ApplyContext] = []
    stages.append(
        engine.register_stage(
            engine.Stage("capture", lambda ctx: engine.Plan(), lambda ctx, plan: captured.append(ctx))
        )
    )
    code, document, _ = run_json(one_line(tmp_path), tmp_path / "root", interactive=False, token_probe=probe)
    assert code == 0, document
    ctx = captured[0]
    sent: list[httpx.Request] = []

    def gateway(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={})

    closed = engine.ApplyContext(
        command=ctx.command,
        args=ctx.args,
        specs=ctx.specs,
        resolved=ctx.resolved,
        prompter=None,
        reporter=ctx.reporter,
        accept_flags=ctx.accept_flags,
    )

    async def restart(apply_ctx: engine.ApplyContext) -> None:
        async with apply_ctx.gateway_writer(transport=httpx.MockTransport(gateway)) as writer:
            await writer.restart_gateway()

    with pytest.raises(engine.WriteBeforeAcceptance):
        asyncio.run(restart(closed))
    with pytest.raises(engine.WriteBeforeAcceptance):
        closed.save()
    assert sent == []
    assert not (tmp_path / "root" / "default" / "deployment.toml").exists()

    # The context the engine handed to apply has its gate open.
    asyncio.run(restart(ctx))
    assert [request.method for request in sent] == ["POST"]


def test_one_line_changes_need_yes(tmp_path: Path, seen: Seen) -> None:
    root = tmp_path / "root"
    code, document, raw = run_json(one_line(tmp_path), root, interactive=False, token_probe=probe)
    assert code == 2
    assert document["error"]["code"] == "not_confirmed"
    assert "--yes" in document["error"]["message"]
    assert shlex.split(document["error"]["next_action"])[-1] == "--yes"
    assert GOOD_TOKEN not in raw
    assert not (root / "default" / "deployment.toml").exists()


def test_an_error_inside_a_step_ends_that_step_as_failed(tmp_path: Path, stages: list[engine.Stage]) -> None:
    def apply(ctx: engine.Context, plan: engine.Plan) -> None:
        with ctx.reporter.step("gateway", "probing"):
            raise CliError(ErrorCode.STEP_FAILED, "connection refused")

    stages.append(engine.register_stage(engine.Stage("probe", lambda ctx: engine.Plan(), apply)))
    code, document, _ = run_json(one_line(tmp_path), tmp_path / "root", interactive=False, token_probe=probe)
    assert code == 1
    failed = document["steps"][-1]
    assert failed["step"] == "gateway" and failed["status"] == "FAILED"
    assert failed["reason"] == "connection refused" and failed["code"] == "step_failed"
    assert failed["next_action"] == "ignition-mcp status --deployment default"

    # A step left open by start() is closed by fail() in the rich report too.
    reporter, buffer = rich_reporter("status")
    reporter.start("gateway", "probing")
    reporter.fail(CliError(ErrorCode.STEP_FAILED, "connection refused", "ignition-mcp status"))
    assert reporter.finish() == 1
    lines = buffer.getvalue().splitlines()
    assert lines[1].startswith("FAILED  gateway  connection refused")
    assert lines[2].strip() == "next: ignition-mcp status"


def test_an_unreachable_gateway_has_its_own_code(tmp_path: Path) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    transport = httpx.MockTransport(refuse)
    with pytest.raises(CliError) as caught:
        probe_gateway_token("http://gw:8088", GOOD_TOKEN, transport=transport)
    assert caught.value.code is ErrorCode.GATEWAY_UNREACHABLE
    assert caught.value.next_action == "curl -sSI http://gw:8088/data/api/v1/gateway-info"
    assert probe_gateway_token(
        "http://gw:8088", BAD_TOKEN, transport=httpx.MockTransport(lambda request: httpx.Response(401))
    ).startswith("the Gateway does not know this key")

    # In the wizard an unreachable Gateway ends the run instead of asking for another key.
    reporter, buffer = rich_reporter("setup")
    code = engine.run(
        ["setup", "--gateway-url", "http://gw:8088", "--environment", "dev", "--roles", "analysis"],
        root=tmp_path,
        prompter=ScriptedPrompter(GOOD_TOKEN),
        interactive=True,
        token_probe=lambda url, token: probe_gateway_token(url, token, transport=transport),
        reporter=reporter,
    )
    assert code == 1 and "gateway_unreachable" in buffer.getvalue()
    assert GOOD_TOKEN not in buffer.getvalue()


def test_a_stage_brings_its_own_inputs(tmp_path: Path, stages: list[engine.Stage]) -> None:
    module = tmp_path / "module.modl"
    module.write_bytes(b"PK")
    found: list[str] = []

    def plan(ctx: engine.Context) -> engine.Plan:
        found.append(ctx.resolved.values["module_file"])
        return engine.Plan()

    spec = InputSpec("module_file", "--module-file", "Module file", kind=Kind.PATH)
    stages.append(engine.register_stage(engine.Stage("module", plan, lambda ctx, plan: None, (spec,))))
    code, document, _ = run_json(one_line(tmp_path), tmp_path / "root", interactive=False, token_probe=probe)
    assert code == 2 and "--module-file" in document["error"]["message"]
    code, document, _ = run_json(
        one_line(tmp_path, "--module-file", str(module)), tmp_path / "root", interactive=False, token_probe=probe
    )
    assert code == 0, document
    assert found == [str(module)]
