"""Entry point for ``ignition-mcp setup-native doctor|plan|apply|verify|install-module`` (D20, D25).

``doctor``, ``plan`` and ``verify`` are read-only; ``apply`` writes the planned
bundle project, Server Config and Runtime Target Policy through the curated
guarded path and then verifies.  ``install-module`` writes the Gateway's own
Module plane through that same path: it hash-checks a trusted local ``.modl``,
uploads it, accepts its certificate and EULA only under their flags, installs it,
and takes the Gateway down only when ``--restart`` says so.  Usage problems exit 2,
an interrupted run exits 2, and an unexpected crash exits 1 reporting the exception
type only, so a credential can never leak through a traceback.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Callable, Coroutine, Sequence
from dataclasses import dataclass
from typing import Any

from ignition_rest_mcp.cli.setup_native import apply, doctor, install_module, plan, verify
from ignition_rest_mcp.cli.setup_native.inputs import (
    COMMANDS,
    ENV_DOC,
    EXIT_CODE_DOC,
    INSTALL_MODULE,
    Inputs,
    ModuleInputs,
    UsageError,
    UsageParser,
    build_base_parser,
    load_inputs,
    load_module_inputs,
    parse_command,
)

PROG = "ignition-mcp"
GROUP = "setup-native"

GROUP_DESCRIPTION = (
    "Detect, plan, apply, verify and install the Module behind an ignition-runtime-bundle "
    "deployment across its documented REST and MCP endpoints."
)

#: The four manifest-driven commands. ``install-module`` names a ``.modl`` instead,
#: so it loads and runs through its own branch in :func:`main`.
_COMMANDS: dict[str, Callable[[Inputs], Coroutine[Any, Any, int]]] = {
    "doctor": doctor.run,
    "plan": plan.run,
    "verify": verify.run,
    "apply": apply.run,
}


@dataclass(frozen=True, slots=True)
class ParserTree:
    """The root and group parsers, sharing one flag definition source."""

    root: UsageParser
    group: UsageParser


def build_parser(prog: str = PROG) -> ParserTree:
    """Build ``ignition-mcp`` → ``setup-native`` → one subcommand per entry of COMMANDS."""

    epilog = EXIT_CODE_DOC + ENV_DOC
    root = UsageParser(
        prog=prog,
        description=GROUP_DESCRIPTION,
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    groups = root.add_subparsers(dest="group", metavar="{" + GROUP + "}")
    group = groups.add_parser(
        GROUP,
        prog=f"{prog} {GROUP}",
        description=GROUP_DESCRIPTION,
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    commands = group.add_subparsers(dest="command", metavar="{" + ",".join(COMMANDS) + "}")
    for command in COMMANDS:
        commands.add_parser(
            command,
            parents=[build_base_parser(module_mode=command == INSTALL_MODULE)],
            help=_COMMAND_SUMMARY[command],
            description=_COMMAND_SUMMARY[command],
        )
    return ParserTree(root=root, group=group)


_COMMAND_SUMMARY = {
    "doctor": "ordered read-only diagnosis of a deployment (never mutates anything)",
    "plan": "report CREATE / UPDATE / NO CHANGE / BLOCKED intentions without applying them",
    "verify": "verify a provisioned deployment: exact inventories, resource/prompt smokes, bundle_info",
    "apply": (
        "apply the planned bundle project, Server Config and Runtime Target Policy, then verify"
    ),
    INSTALL_MODULE: (
        "install a trusted local .modl: hash check, upload, certificate and EULA acceptance "
        "under their flags, install, optional restart"
    ),
}


def _usage_error(message: str, parser: UsageParser) -> int:
    print(f"{PROG}: error: {message}", file=sys.stderr)
    print(parser.format_usage(), file=sys.stderr)
    return 2


async def _dispatch(command: str, inputs: Inputs | ModuleInputs) -> int:
    """Run the loaded inputs through the one command that owns that shape."""

    if command == INSTALL_MODULE:
        if not isinstance(inputs, ModuleInputs):  # pragma: no cover - main loads by command
            raise UsageError(f"{command} needs the --file and --sha256 inputs")
        return await install_module.run(inputs)
    if not isinstance(inputs, Inputs):  # pragma: no cover - main loads by command
        raise UsageError(f"{command} needs a bundle manifest")
    return await _COMMANDS[command](inputs)


def main(argv: Sequence[str] | None = None) -> int:
    """Console-script entry point; returns the process exit code."""

    args = [str(item) for item in (sys.argv[1:] if argv is None else argv)]
    tree = build_parser()
    if not args:
        return _usage_error("a command is required", tree.root)
    if args[0] in ("-h", "--help"):
        print(tree.root.format_help())
        return 0
    if args[0] != GROUP:
        return _usage_error(
            f"unknown command group {args[0]!r}; only {GROUP!r} is implemented",
            tree.root,
        )
    rest = args[1:]
    if not rest:
        return _usage_error(f"{GROUP} needs one of {', '.join(COMMANDS)}", tree.group)
    if rest[0] in ("-h", "--help"):
        print(tree.group.format_help())
        return 0
    try:
        command, flags = parse_command(rest)
        inputs = load_module_inputs(flags) if command == INSTALL_MODULE else load_inputs(flags, command)
    except UsageError as error:
        return _usage_error(str(error), tree.group)
    except SystemExit as exit_signal:  # argparse handled --help on the subcommand parser
        return _exit_code(exit_signal)
    except KeyboardInterrupt:
        return 2
    try:
        return asyncio.run(_dispatch(command, inputs))
    except UsageError as error:
        return _usage_error(str(error), tree.group)
    except KeyboardInterrupt:
        return 2
    except Exception as error:  # never echo a message that could carry a credential
        print(f"{PROG}: unexpected {type(error).__name__}", file=sys.stderr)
        return 1


def _exit_code(exit_signal: SystemExit) -> int:
    code = exit_signal.code
    if code is None:
        return 0
    return code if isinstance(code, int) else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
