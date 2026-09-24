"""Entry point for ``ignition-mcp setup|status|start|connect|reset`` (D32 section 2).

Every command runs the same way:

1. parse the flags,
2. open the deployment named by ``--deployment``,
3. resolve the command's inputs (flag, saved deployment, prompt),
4. call the command's handler with a :class:`Context`,
5. print the equivalent one-line command when the wizard asked anything,
6. let the reporter print the end of the run and return the exit code.

A handler reports each step through ``ctx.reporter.step``, and raises
:class:`~ignition_rest_mcp.cli.engine.errors.CliError` to stop. The handlers of
this ticket are placeholders. ``setup``, and any command a stage is registered
on, runs the stages added with :func:`register_stage`: every stage's plan first
with a :class:`Context` that cannot write, then the plan display, every acceptance
and the confirmation (``--yes`` in one-line mode), and only then each stage's
``apply`` with an :class:`ApplyContext`. Its writes, including Gateway writes
through :meth:`ApplyContext.gateway_writer`, check the engine's gate themselves. A
stage brings its own inputs, so the Runtime and REST tickets never edit the same
lines here.

``ignition-mcp setup-native ...`` still runs the old commands until ticket P7-6
deletes them.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from ignition_rest_mcp.cli.engine.deployment import Deployment, open_deployment, save_deployment, write_secret
from ignition_rest_mcp.cli.engine.errors import CliError, ErrorCode
from ignition_rest_mcp.cli.engine.prompter import Prompter, default_prompter, is_terminal
from ignition_rest_mcp.cli.engine.report import JsonReporter, PlannedChange, Reporter, RichReporter, Status
from ignition_rest_mcp.cli.engine.resolve import (
    PROG,
    AcceptFlags,
    Accepted,
    InputSpec,
    Kind,
    Needed,
    Resolved,
    Source,
    TokenProbe,
    accept,
    check_gateway_url,
    equivalent_command,
    gateway_token_check,
    resolve,
)
from ignition_rest_mcp.cli.setup_native.gateway import GatewayRest
from ignition_rest_mcp.cli.setup_native.inputs import Endpoint
from ignition_rest_mcp.cli.setup_native.writer import GatewayWriter

DEFAULT_DEPLOYMENT = "default"
ENVIRONMENTS = ("dev", "prod")
ROLES = ("analysis", "engineer")


def _default_roles(values: Mapping[str, str]) -> str:
    # D32 section 5: dev deploys both roles, prod the Analysis Assistant only.
    return "analysis" if values.get("environment") == "prod" else "analysis,engineer"


def standard_inputs(token_probe: TokenProbe | None = None) -> dict[str, InputSpec]:
    """The inputs the commands share, by name. ``token_probe`` replaces the Gateway call."""

    specs = [
        InputSpec(
            name="gateway_url",
            flag="--gateway-url",
            question="Gateway URL",
            check=check_gateway_url,
        ),
        InputSpec(
            name="environment",
            flag="--environment",
            question="Deployment environment",
            kind=Kind.CHOICE,
            choices=ENVIRONMENTS,
            default="dev",
        ),
        InputSpec(
            name="roles",
            flag="--roles",
            question="Assistant roles to deploy",
            kind=Kind.LIST,
            choices=ROLES,
            default=_default_roles,
        ),
        InputSpec(
            name="gateway_token",
            flag="--gateway-token-file",
            question=(
                "Paste a new Gateway API key (<name>:<key>) whose Security Level is ticked "
                "under every permission in Security > General Settings"
            ),
            kind=Kind.SECRET,
            secret_name="gateway-token",
            check=gateway_token_check(token_probe),
            failure_code=ErrorCode.GATEWAY_TOKEN_REJECTED,
        ),
    ]
    return {spec.name: spec for spec in specs}


class WriteBeforeAcceptance(RuntimeError):
    """A write was attempted before the plan was shown, accepted and confirmed.

    This is a bug in a stage, never an operator error, so it is not a ``CliError``.
    """


class _WriteGate:
    """The engine's switch for writes. Only :func:`_run_stages` opens it."""

    __slots__ = ("_open",)

    def __init__(self) -> None:
        self._open = False

    def check(self, what: str) -> None:
        if not self._open:
            raise WriteBeforeAcceptance(f"{what} was attempted before the plan was shown, accepted and confirmed")


def _open_gate(gate: _WriteGate) -> None:
    gate._open = True


@dataclass(slots=True)
class Context:
    """What a handler or a stage's ``plan`` gets. It has no way to write.

    Everything it prints goes through ``reporter``. It can read the Gateway through
    :meth:`gateway_reader`, which only sends GET requests.
    """

    command: str
    args: argparse.Namespace
    specs: list[InputSpec]
    resolved: Resolved
    prompter: Prompter | None
    reporter: Reporter
    accept_flags: AcceptFlags
    accepted: list[Accepted] = field(default_factory=list)
    #: Whether the plan was confirmed, so the equivalent command carries ``--yes``.
    confirmed: bool = False

    @property
    def deployment(self) -> Deployment:
        return self.resolved.deployment

    @property
    def dry_run(self) -> bool:
        return bool(getattr(self.args, "dry_run", False))

    def gateway_reader(self, transport: httpx.AsyncBaseTransport | None = None) -> GatewayRest:
        """A GET-only Gateway client that authenticates with the setup token."""

        return GatewayRest(_endpoint(self), self._setup_token(), transport=transport)

    def _setup_token(self) -> str:
        secret = self.resolved.secrets.get("gateway_token")
        if secret is None:
            raise CliError(ErrorCode.MISSING_INPUT, f"{self.command} has no Gateway token")
        return secret.reveal()


@dataclass(slots=True)
class ApplyContext(Context):
    """What a stage's ``apply`` gets: a :class:`Context` that can also write.

    Every write checks the engine's gate itself, so a write made before the plan was
    shown, accepted and confirmed raises :class:`WriteBeforeAcceptance`.
    """

    _gate: _WriteGate = field(default_factory=_WriteGate)

    def save(self) -> Deployment:
        """Write the resolved non-secret values into ``deployment.toml``."""

        self._gate.check("saving deployment.toml")
        deployment = save_deployment(self.deployment, self.resolved.saveable(self.specs))
        self.resolved.deployment = deployment
        return deployment

    def write_secret(self, secret: str, value: str) -> Path:
        """Create one ``0600`` secret file in the deployment directory."""

        self._gate.check(f"writing the {secret} secret file")
        return write_secret(self.deployment, secret, value)

    def gateway_writer(self, transport: httpx.AsyncBaseTransport | None = None) -> GatewayWriter:
        """The curated Gateway write path, with every write checked against the gate."""

        return _GatedGatewayWriter(self._gate, _endpoint(self), self._setup_token(), transport=transport)


class _GatedGatewayWriter(GatewayWriter):
    """``setup_native``'s writer, whose single write method checks the gate first."""

    def __init__(
        self,
        gate: _WriteGate,
        endpoint: Endpoint,
        api_token: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(endpoint, api_token, transport=transport)
        self._gate = gate

    async def _write(
        self,
        method: str,
        path: str,
        *,
        body: bytes,
        content_type: str,
        action: str,
        params: dict[str, str] | None = None,
    ) -> Any:
        self._gate.check(action)
        return await super()._write(
            method, path, body=body, content_type=content_type, action=action, params=params
        )


def _endpoint(ctx: Context) -> Endpoint:
    url = ctx.resolved.values.get("gateway_url")
    if not url:
        raise CliError(ErrorCode.MISSING_INPUT, f"{ctx.command} has no Gateway URL")
    parts = urlsplit(url)
    return Endpoint(
        url=url.rstrip("/"),
        scheme=parts.scheme,
        host=str(parts.hostname),
        port=parts.port or (443 if parts.scheme == "https" else 80),
    )


Handler = Callable[[Context], None]


def _not_implemented(ctx: Context) -> None:
    raise CliError(ErrorCode.NOT_IMPLEMENTED, f"{ctx.command} is not implemented yet")


@dataclass(slots=True)
class Plan:
    """What one stage will do, found without writing anything.

    ``changes`` are the lines the operator sees before confirming. ``needed`` are
    the Explicit acceptance items the stage's writes depend on. ``data`` is the
    stage's own state, handed back to its ``apply`` unchanged.
    """

    changes: list[str] = field(default_factory=list)
    needed: list[Needed] = field(default_factory=list)
    data: Any = None


@dataclass(frozen=True, slots=True)
class Stage:
    """One part of a command: a read-only ``plan`` and the ``apply`` that writes it.

    ``plan`` gets a :class:`Context`, which cannot write. ``apply`` gets an
    :class:`ApplyContext`. ``inputs`` are the stage's own inputs. They are asked
    after the command's shared inputs, in the order the stages were registered.
    """

    name: str
    plan: Callable[[Context], Plan]
    apply: Callable[[ApplyContext, Plan], None]
    inputs: tuple[InputSpec, ...] = ()


def _run_stages(ctx: Context) -> None:
    """Plan every stage, show the plan, accept and confirm, and only then apply.

    One-line mode confirms a plan with changes through ``--yes``; the wizard asks.
    A missing or declined acceptance or confirmation fails the run before the gate
    opens, so the Gateway and the deployment directory stay untouched.
    """

    stages = COMMANDS[ctx.command].stages
    if not stages:
        _not_implemented(ctx)
    plans: list[tuple[Stage, Plan]] = []
    for stage in stages:
        with ctx.reporter.step(f"plan {stage.name}", "reading the current state") as end:
            plan = stage.plan(ctx)
            end.set(Status.OK, f"{len(plan.changes)} change(s) planned")
        plans.append((stage, plan))
    ctx.reporter.plan([PlannedChange(stage.name, change) for stage, plan in plans for change in plan.changes])
    items = accept([item for _, plan in plans for item in plan.needed], ctx.accept_flags, ctx.prompter)
    ctx.accepted.extend(items)
    ctx.reporter.accepted(items)
    if ctx.dry_run:
        return
    if any(plan.changes for _, plan in plans) and not ctx.accept_flags.yes:
        if ctx.prompter is None:
            positional, extra = _command_words(ctx)
            raise CliError(
                ErrorCode.NOT_CONFIRMED,
                "the plan above has changes and one-line mode confirms them with --yes. Nothing was written",
                next_action=equivalent_command(
                    [ctx.command, *positional], ctx.specs, ctx.resolved, ctx.accepted, extra, yes=True
                ),
            )
        if not ctx.prompter.confirm("Apply the changes listed above?"):
            raise CliError(ErrorCode.NOT_CONFIRMED, "the plan was not confirmed. Nothing was written")
    ctx.confirmed = True
    gate = _WriteGate()
    apply_ctx = ApplyContext(
        command=ctx.command,
        args=ctx.args,
        specs=ctx.specs,
        resolved=ctx.resolved,
        prompter=ctx.prompter,
        reporter=ctx.reporter,
        accept_flags=ctx.accept_flags,
        accepted=ctx.accepted,
        confirmed=ctx.confirmed,
        _gate=gate,
    )
    _open_gate(gate)
    for stage, plan in plans:
        stage.apply(apply_ctx, plan)


@dataclass(slots=True)
class Command:
    name: str
    summary: str
    #: Names from :func:`standard_inputs`, in the order the wizard asks them.
    inputs: tuple[str, ...]
    handler: Handler = _not_implemented
    #: Adds the command's own flags.
    configure: Callable[[argparse.ArgumentParser], None] | None = None
    #: Inputs the command's own ticket adds, asked after ``inputs``.
    extra_inputs: list[InputSpec] = field(default_factory=list)
    #: The stages :func:`_run_stages` runs when ``handler`` is ``_run_stages``.
    stages: list[Stage] = field(default_factory=list)
    #: Extra words the command adds to its equivalent command, such as ``--bind``.
    words: Callable[[Context], list[str]] | None = None

    def specs(self, standard: Mapping[str, InputSpec]) -> list[InputSpec]:
        specs = [standard[name] for name in self.inputs] + list(self.extra_inputs)
        specs += [spec for stage in self.stages for spec in stage.inputs]
        seen: set[str] = set()
        unique: list[InputSpec] = []
        for spec in specs:
            if spec.name not in seen:
                seen.add(spec.name)
                unique.append(spec)
        return unique


def register_stage(stage: Stage, command: str = "setup") -> Stage:
    """Add a stage and its inputs to a command. Each Phase 7 ticket calls this.

    The command runs its stages through :func:`_run_stages`, so a command that had a
    placeholder body gets the plan, acceptance and confirmation sequence.
    """

    target = COMMANDS[command]
    if any(existing.name == stage.name for existing in target.stages):
        raise ValueError(f"{command} stage {stage.name!r} is already registered")
    target.stages.append(stage)
    target.handler = _run_stages
    return stage


def _setup_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dry-run", action="store_true", help="show the plan and stop before any write")


def _connect_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("role", choices=ROLES, help="the Assistant role to register")


COMMANDS: dict[str, Command] = {
    command.name: command
    for command in (
        Command(
            "setup",
            "deploy both planes for the chosen Assistant roles; shows the plan and asks before writing",
            ("gateway_url", "environment", "roles", "gateway_token"),
            handler=_run_stages,
            configure=_setup_flags,
        ),
        Command("status", "read-only: report every check one line at a time", ("gateway_url",)),
        Command("start", "run the REST server in the foreground until Ctrl+C", ()),
        Command(
            "connect",
            "register one Assistant role's endpoints with Claude Code or Codex",
            (),
            configure=_connect_flags,
        ),
        Command("reset", "remove what setup created; refused outside dev", ("gateway_url", "gateway_token")),
    )
}


#: The stages ``setup`` runs, in order. Add one with :func:`register_stage`.
SETUP_STAGES: list[Stage] = COMMANDS["setup"].stages


def build_parser(specs: Mapping[str, InputSpec] | None = None) -> argparse.ArgumentParser:
    specs = specs or standard_inputs()
    root = argparse.ArgumentParser(
        prog=PROG,
        description="Set up, check, run and connect an Ignition MCP deployment.",
        epilog="Exit codes: 0 success, 1 a step failed, 2 a problem with the flags or answers.",
    )
    commands = root.add_subparsers(dest="command", metavar="{" + ",".join(COMMANDS) + "}")
    for command in COMMANDS.values():
        parser = commands.add_parser(command.name, help=command.summary, description=command.summary)
        parser.add_argument(
            "--deployment",
            default=DEFAULT_DEPLOYMENT,
            help="deployment name under ~/.config/ignition-mcp/deployments (default: %(default)s)",
        )
        for spec in command.specs(specs):
            help_text = spec.question
            if spec.kind is Kind.SECRET:
                help_text = "file that holds the " + spec.name.replace("_", " ") + " (one <name>:<key> line)"
            elif spec.choices:
                help_text += f" ({', '.join(spec.choices)})"
            parser.add_argument(spec.flag, dest=spec.name, default=None, metavar="VALUE", help=help_text)
        parser.add_argument("--json", action="store_true", help="print the steps as one JSON document")
        parser.add_argument(
            "--yes", action="store_true", help="accept every named risk except the certificate and the EULA"
        )
        parser.add_argument("--accept-certificate", action="store_true", help="trust the Module certificate")
        parser.add_argument("--accept-eula", action="store_true", help="accept the Module EULA")
        if command.configure is not None:
            command.configure(parser)
    return root


def run(
    argv: Sequence[str],
    *,
    root: Path | None = None,
    prompter: Prompter | None = None,
    interactive: bool | None = None,
    token_probe: TokenProbe | None = None,
    reporter: Reporter | None = None,
) -> int:
    """Run one command. The keyword arguments exist for tests.

    ``interactive`` defaults to whether stdin is a terminal. ``--json`` always turns
    prompting off, because its reader is a program.
    """

    specs = standard_inputs(token_probe)
    parser = build_parser(specs)
    args = parser.parse_args(list(argv))
    if args.command is None:
        parser.print_usage(sys.stderr)
        return 2
    command = COMMANDS[args.command]
    if reporter is None:
        reporter = JsonReporter(command.name) if args.json else RichReporter(command.name)
    if args.json or not (is_terminal() if interactive is None else interactive):
        prompter = None
    elif prompter is None:
        prompter = default_prompter()
    command_specs = command.specs(specs)
    ctx: Context | None = None
    try:
        deployment = open_deployment(args.deployment, root)
        reporter.default_next_action = f"{PROG} status --deployment {deployment.name}"
        flags = {spec.name: getattr(args, spec.name) for spec in command_specs}
        resolved = resolve(command_specs, flags, deployment, prompter)
        reporter.hide(*(secret.reveal() for secret in resolved.secrets.values()))
        ctx = Context(
            command=command.name,
            args=args,
            specs=command_specs,
            resolved=resolved,
            prompter=prompter,
            reporter=reporter,
            accept_flags=AcceptFlags(args.yes, args.accept_certificate, args.accept_eula),
        )
        command.handler(ctx)
    except CliError as error:
        if error.code is ErrorCode.MISSING_INPUT and not error.next_action:
            error.next_action = f"{PROG} {command.name} --help"
        reporter.fail(error)
    except KeyboardInterrupt:
        reporter.fail(CliError(ErrorCode.INTERRUPTED, "interrupted; steps that ended above are kept"))
    except Exception as error:  # the message could carry a secret; report the type only
        reporter.fail(CliError(ErrorCode.UNEXPECTED_ERROR, f"unexpected {type(error).__name__}"))
    stopped = reporter.error is not None and reporter.error.code in (
        ErrorCode.ACCEPTANCE_REQUIRED,
        ErrorCode.INTERRUPTED,
    )
    if ctx is not None and not stopped and _wizard_asked(ctx):
        extra = _command_words(ctx)
        reporter.equivalent_command(
            equivalent_command(
                [command.name, *extra[0]], ctx.specs, ctx.resolved, ctx.accepted, extra[1], yes=ctx.confirmed
            )
        )
    return reporter.finish()


def _wizard_asked(ctx: Context) -> bool:
    return Source.PROMPT in ctx.resolved.sources.values() or any(item.how == "prompt" for item in ctx.accepted)


def _command_words(ctx: Context) -> tuple[list[str], list[str]]:
    """Positional words and extra flags a command adds to its equivalent command."""

    positional = [ctx.args.role] if ctx.command == "connect" else []
    extra = ["--dry-run"] if ctx.dry_run else []
    words = COMMANDS[ctx.command].words
    if words is not None:
        extra += words(ctx)
    return positional, extra


def main(argv: Sequence[str] | None = None) -> int:
    """Console-script entry point for ``ignition-mcp``."""

    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["setup-native"]:
        from ignition_rest_mcp.cli.setup_native import main as setup_native

        return setup_native.main(args)
    from ignition_rest_mcp.cli.setup import rest as rest_setup
    from ignition_rest_mcp.cli.setup import start as rest_start

    rest_setup.register()
    rest_start.register()
    return run(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
