"""Entry point for ``ignition-mcp setup|status|start|connect|reset`` (D32 section 2).

Every command runs the same way:

1. parse the flags,
2. open the deployment named by ``--deployment``,
3. resolve the command's inputs (flag, saved deployment, prompt),
4. call the command's handler with a :class:`Context`,
5. print the equivalent one-line command when the wizard asked anything,
6. let the reporter print the end of the run and return the exit code.

A handler reports through ``ctx.reporter``, asks for Explicit acceptance through
:meth:`Context.accept` before its first write, and raises
:class:`~ignition_rest_mcp.cli.engine.errors.CliError` to stop. The handlers of
this ticket are placeholders. ``setup`` runs the functions in :data:`SETUP_STAGES`
in order, so the Runtime and REST tickets each add their own stage.

``ignition-mcp setup-native ...`` still runs the old commands until ticket P7-6
deletes them.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ignition_rest_mcp.cli.engine.deployment import Deployment, open_deployment, save_deployment
from ignition_rest_mcp.cli.engine.errors import CliError, ErrorCode
from ignition_rest_mcp.cli.engine.prompter import Prompter, default_prompter, is_terminal
from ignition_rest_mcp.cli.engine.report import JsonReporter, Reporter, RichReporter
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


@dataclass(slots=True)
class Context:
    """What a handler gets. Everything a handler prints goes through ``reporter``."""

    command: str
    args: argparse.Namespace
    specs: list[InputSpec]
    resolved: Resolved
    prompter: Prompter | None
    reporter: Reporter
    accept_flags: AcceptFlags
    accepted: list[Accepted] = field(default_factory=list)

    @property
    def deployment(self) -> Deployment:
        return self.resolved.deployment

    @property
    def dry_run(self) -> bool:
        return bool(getattr(self.args, "dry_run", False))

    def accept(self, needed: Sequence[Needed]) -> list[Accepted]:
        """Accept every item or raise ``acceptance_required``. Call it before any write."""

        items = accept(needed, self.accept_flags, self.prompter)
        self.accepted.extend(items)
        self.reporter.accepted(items)
        return items

    def save(self) -> Deployment:
        """Write the resolved non-secret values into ``deployment.toml``."""

        deployment = save_deployment(self.deployment, self.resolved.saveable(self.specs))
        self.resolved.deployment = deployment
        return deployment


Handler = Callable[[Context], None]


def _not_implemented(ctx: Context) -> None:
    raise CliError(ErrorCode.NOT_IMPLEMENTED, f"{ctx.command} is not implemented yet")


#: The stages ``setup`` runs in order. Each Phase 7 ticket appends its own.
SETUP_STAGES: list[Handler] = []


def _setup(ctx: Context) -> None:
    if not SETUP_STAGES:
        _not_implemented(ctx)
    for stage in SETUP_STAGES:
        stage(ctx)


@dataclass(slots=True)
class Command:
    name: str
    summary: str
    #: Names from :func:`standard_inputs`, in the order the wizard asks them.
    inputs: tuple[str, ...]
    handler: Handler = _not_implemented
    #: Adds the command's own flags.
    configure: Callable[[argparse.ArgumentParser], None] | None = None


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
            handler=_setup,
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
        for name in command.inputs:
            spec = specs[name]
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
    command_specs = [specs[name] for name in command.inputs]
    ctx: Context | None = None
    try:
        deployment = open_deployment(args.deployment, root)
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
            equivalent_command([command.name, *extra[0]], ctx.specs, ctx.resolved, ctx.accepted, extra[1])
        )
    return reporter.finish()


def _wizard_asked(ctx: Context) -> bool:
    return Source.PROMPT in ctx.resolved.sources.values() or any(item.how == "prompt" for item in ctx.accepted)


def _command_words(ctx: Context) -> tuple[list[str], list[str]]:
    """Positional words and extra flags a command adds to its equivalent command."""

    positional = [ctx.args.role] if ctx.command == "connect" else []
    extra = ["--dry-run"] if ctx.dry_run else []
    return positional, extra


def main(argv: Sequence[str] | None = None) -> int:
    """Console-script entry point for ``ignition-mcp``."""

    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["setup-native"]:
        from ignition_rest_mcp.cli.setup_native import main as setup_native

        return setup_native.main(args)
    return run(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
