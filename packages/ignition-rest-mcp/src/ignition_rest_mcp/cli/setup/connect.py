"""``ignition-mcp connect``: register a role's endpoints with an agent client (issue #76).

``connect <role>`` registers two MCP servers with one client, using that role's own
tokens (D32 section 8):

* ``ignition-runtime-<role>``, the Gateway endpoint ``<gateway-url>/data/mcp/<role>``,
  authenticated with the ``X-Ignition-API-Token`` header the Module reads;
* ``ignition-rest-<role>``, the REST server's default address, authenticated with
  ``Authorization: Bearer`` and the role's Named static token.

Which client is decided by the ``client`` input, so one engine resolves it and the
wizard's equivalent command carries the answer:

* Claude Code is registered through its own CLI, ``claude mcp add``, which writes its
  own configuration;
* Codex is registered in its own configuration file, ``[mcp_servers.<name>]`` with an
  ``http_headers`` table. Its CLI cannot express that: ``codex mcp add`` offers only
  ``--bearer-token-env-var``, and the bearer scheme is the one the Module does not read
  for a Gateway API token;
* ``none`` registers nothing.

A token never appears in this command's output, its report or its equivalent command.
It is passed to the client through the client's own configuration and the report names
where it went. Tests replace :data:`SETTINGS`, so no test runs the real ``claude`` or
``codex`` binary.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ignition_rest_mcp.cli.engine import main as engine
from ignition_rest_mcp.cli.engine.deployment import Deployment, read_secret
from ignition_rest_mcp.cli.engine.errors import CliError, ErrorCode
from ignition_rest_mcp.cli.engine.report import Status
from ignition_rest_mcp.cli.engine.resolve import PROG, InputSpec, Kind
from ignition_rest_mcp.cli.setup import rest, runtime, start

NONE = "none"
CLIENTS = ("claude", "codex", NONE)
#: The display name of each client, and the binary its detection looks for on PATH.
LABELS = {"claude": "Claude Code", "codex": "Codex"}
BINARIES = {"claude": "claude", "codex": "codex"}
#: Where ``claude mcp add`` puts a registration: ``user`` reaches every project.
CLAUDE_SCOPE = "user"
#: How long one client command may take before it is treated as not answering.
COMMAND_TIMEOUT_SECONDS = 60.0
#: How much of a client's own failure output is quoted back, in characters.
SNIPPET_LENGTH = 200

#: The credential header the MCP Module reads (D32 section 8). The bearer scheme is
#: not accepted for a Gateway API token, which is why Codex needs ``http_headers``.
RUNTIME_HEADER = "X-Ignition-API-Token"
REST_HEADER = "Authorization"


@dataclass(frozen=True, slots=True)
class RunResult:
    """One finished client command. Its output is quoted only through :func:`_snippet`."""

    code: int
    stdout: str = ""
    stderr: str = ""


def _run_process(argv: Sequence[str]) -> RunResult:
    try:
        answer = subprocess.run(
            list(argv), capture_output=True, text=True, check=False, timeout=COMMAND_TIMEOUT_SECONDS
        )
    except (OSError, subprocess.SubprocessError) as error:
        return RunResult(code=-1, stderr=f"{type(error).__name__}")
    return RunResult(code=answer.returncode, stdout=answer.stdout, stderr=answer.stderr)


@dataclass(slots=True)
class Settings:
    """What tests replace. ``which`` is the PATH lookup, ``run`` the command runner."""

    which: Callable[[str], str | None] = shutil.which
    run: Callable[[Sequence[str]], RunResult] = _run_process
    #: A replacement home for the Codex configuration; ``None`` means the real one.
    home: Path | None = None


SETTINGS = Settings()


@dataclass(frozen=True, slots=True)
class Registration:
    """One MCP server entry to register with a client."""

    name: str
    url: str
    header: str
    #: The header value, a token. It is never printed.
    value: str

    @property
    def line(self) -> str:
        """The ``Header: value`` argument ``claude mcp add --header`` takes."""

        return f"{self.header}: {self.value}"


# ------------------------------------------------------------------ detection


def installed(client: str) -> bool:
    """Whether the client's binary is on PATH."""

    if client == NONE:
        return True
    return SETTINGS.which(BINARIES[client]) is not None


def check_client(value: str, _values: Mapping[str, str]) -> str:
    """``""`` when the client can be used, else the reason the wizard asks again with."""

    if installed(value):
        return ""
    return (
        f"{LABELS[value]} is not installed ({BINARIES[value]} is not on PATH), so it cannot be chosen; "
        f"install it, or answer {NONE}"
    )


CLIENT_INPUT = InputSpec(
    name="client",
    flag="--client",
    question=(
        "MCP client to register this role with (claude for Claude Code, codex for Codex, "
        "none to register nothing)"
    ),
    kind=Kind.CHOICE,
    choices=CLIENTS,
    check=check_client,
)


# ------------------------------------------------------------------ what to register


def registrations(ctx: engine.Context, role: str, deployment: Deployment) -> list[Registration]:
    """The two MCP servers one role registers, with the secrets each one needs."""

    token = read_secret(deployment.secret_path(runtime.ROLES[role].secret))
    static = rest.static_token_value(deployment, role)
    ctx.reporter.hide(token, static)
    url = deployment.values.get("gateway_url")
    if not isinstance(url, str) or not url:
        raise CliError(
            ErrorCode.DEPLOYMENT_UNREADABLE,
            f"{deployment.directory} has no Gateway URL; setup has not finished here",
            next_action=f"{PROG} setup --deployment {deployment.name}",
        )
    return [
        Registration(
            f"ignition-runtime-{role}",
            f"{url.rstrip('/')}/data/mcp/{role}",
            RUNTIME_HEADER,
            token,
        ),
        Registration(
            f"ignition-rest-{role}",
            f"http://{start.DEFAULT_BIND}{start.MCP_PATH}",
            REST_HEADER,
            f"Bearer {static}",
        ),
    ]


# ---------------------------------------------------------------------- Claude Code


def _snippet(result: RunResult) -> str:
    """The client's own last line of output, bounded, for the step's reason."""

    for stream in (result.stderr, result.stdout):
        lines = [line.strip() for line in stream.splitlines() if line.strip()]
        if lines:
            return f": {lines[-1][:SNIPPET_LENGTH]}"
    return ""


def _register_claude(ctx: engine.Context, items: Sequence[Registration]) -> tuple[list[str], str]:
    """Register through ``claude mcp add``, its own configuration mechanism."""

    binary = BINARIES["claude"]
    registered: list[str] = []
    for item in items:
        with ctx.reporter.step(f"claude {item.name}", item.url) as end:
            # claude mcp add refuses a name that is already registered, so a previous
            # registration goes first. A remove that finds nothing is not a failure.
            SETTINGS.run([binary, "mcp", "remove", item.name, "--scope", CLAUDE_SCOPE])
            result = SETTINGS.run(
                [
                    binary,
                    "mcp",
                    "add",
                    "--transport",
                    "http",
                    item.name,
                    item.url,
                    "--scope",
                    CLAUDE_SCOPE,
                    "--header",
                    item.line,
                ]
            )
            if result.code != 0:
                raise CliError(
                    ErrorCode.STEP_FAILED,
                    f"Claude Code refused the registration (exit {result.code}){_snippet(result)}",
                    next_action=_connect_next(ctx),
                )
            end.set(Status.CHANGED, f"registered with Claude Code, scope {CLAUDE_SCOPE}")
            registered.append(item.name)
    return registered, f"Claude Code's own configuration, scope {CLAUDE_SCOPE}"


def _connect_next(ctx: engine.Context) -> str:
    return f"{PROG} connect {getattr(ctx.args, 'role', '<role>')} --deployment {ctx.deployment.name}"


# --------------------------------------------------------------------------- Codex


def codex_config_path() -> Path:
    """Codex's own configuration file: ``$CODEX_HOME/config.toml``, else ``~/.codex``."""

    if SETTINGS.home is not None:
        return SETTINGS.home / ".codex" / "config.toml"
    home = os.environ.get("CODEX_HOME")
    if home:
        return Path(home).expanduser() / "config.toml"
    return Path.home() / ".codex" / "config.toml"


def _codex_block(item: Registration) -> str:
    """One ``[mcp_servers.<name>]`` table, with its literal header in ``http_headers``."""

    return "\n".join(
        [
            f"[mcp_servers.{item.name}]",
            f"url = {json.dumps(item.url)}",
            "",
            f"[mcp_servers.{item.name}.http_headers]",
            f"{json.dumps(item.header)} = {json.dumps(item.value)}",
        ]
    )


def _codex_servers(text: str) -> dict[str, Any]:
    """The ``mcp_servers`` table of the existing configuration, or ``{}``."""

    if not text.strip():
        return {}
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise CliError(
            ErrorCode.INVALID_INPUT,
            f"the Codex configuration is not valid TOML ({error}); fix it before connecting",
            next_action=f"{PROG} connect --help",
        ) from error
    servers = document.get("mcp_servers")
    return servers if isinstance(servers, dict) else {}


def _codex_matches(servers: dict[str, Any], item: Registration) -> bool:
    entry = servers.get(item.name)
    if not isinstance(entry, dict):
        return False
    headers = entry.get("http_headers")
    return entry.get("url") == item.url and isinstance(headers, dict) and headers.get(item.header) == item.value


def _replace_table(text: str, item: Registration) -> str:
    """``text`` with the ``[mcp_servers.<name>]`` table, and its sub-tables, replaced."""

    block = _codex_block(item).splitlines()
    header = f"[mcp_servers.{item.name}]"
    lines = text.splitlines()
    start = next((index for index, line in enumerate(lines) if line.strip() == header), None)
    if start is None:
        body = "\n".join(lines)
        if body and not body.endswith("\n"):
            body += "\n"
        separator = "\n" if body.strip() else ""
        return body + separator + "\n".join(block) + "\n"
    end = start + 1
    while end < len(lines):
        stripped = lines[end].strip()
        if stripped.startswith("[") and not stripped.startswith(f"{header}."):
            break
        end += 1
    return "\n".join([*lines[:start], *block, *lines[end:]]) + "\n"


def codex_config_text(text: str, items: Sequence[Registration]) -> str | None:
    """The new configuration text, or ``None`` when every registration already matches."""

    servers = _codex_servers(text)
    wanted = [item for item in items if not _codex_matches(servers, item)]
    if not wanted:
        return None
    updated = text
    for item in wanted:
        updated = _replace_table(updated, item)
    written = _codex_servers(updated)
    for item in items:
        if not _codex_matches(written, item):
            raise CliError(
                ErrorCode.STEP_FAILED,
                f"the Codex configuration would not read back with {item.name}; nothing was written",
                next_action=_codex_next(),
            )
    return updated


def _codex_next() -> str:
    return f"{PROG} connect --help"


def _write_codex(path: Path, text: str) -> None:
    """Write the configuration atomically, with mode ``0600`` because it holds a token."""

    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    staging = path.with_name(path.name + ".tmp")
    staging.write_text(text, encoding="utf-8", newline="\n")
    os.chmod(staging, 0o600)
    os.replace(staging, path)


def _register_codex(ctx: engine.Context, items: Sequence[Registration]) -> tuple[list[str], str]:
    """Register in Codex's own configuration file."""

    path = codex_config_path()
    registered: list[str] = []
    for item in items:
        with ctx.reporter.step(f"codex {item.name}", item.url) as end:
            try:
                text = path.read_text(encoding="utf-8")
            except FileNotFoundError:
                text = ""
            except (OSError, UnicodeDecodeError) as error:
                raise CliError(
                    ErrorCode.INVALID_INPUT,
                    f"{path} cannot be read ({type(error).__name__})",
                    next_action=_codex_next(),
                ) from error
            updated = codex_config_text(text, [item])
            if updated is None:
                end.set(Status.OK, f"{item.name} is already registered in {path}")
            else:
                _write_codex(path, updated)
                end.set(Status.CHANGED, f"registered in {path}, mode 0600, header {item.header}")
            registered.append(item.name)
    return registered, str(path)


# ------------------------------------------------------------------------ command


def connect(ctx: engine.Context) -> None:
    """Register the role's Runtime and REST endpoints with the chosen client."""

    role = str(ctx.args.role)
    client = ctx.resolved.values["client"]
    deployment = ctx.deployment
    with ctx.reporter.step("deployment", str(deployment.directory)) as end:
        if not deployment.exists:
            raise CliError(
                ErrorCode.DEPLOYMENT_UNREADABLE,
                f"{deployment.directory} has no deployment.toml",
                next_action=f"{PROG} setup --deployment {deployment.name}",
            )
        roles = deployment.values.get("roles")
        served = [str(name) for name in roles] if isinstance(roles, list) else []
        if role not in served:
            raise CliError(
                ErrorCode.DEPLOYMENT_UNREADABLE,
                f"the deployment {deployment.name} does not serve the {role} role"
                + (f"; it serves {', '.join(served)}" if served else ""),
                next_action=f"{PROG} setup --deployment {deployment.name} --roles {role}",
            )
        end.set(Status.OK, f"serves the {role} role")
    if client == NONE:
        ctx.reporter.end("client", Status.SKIPPED, "none was chosen, so nothing was registered")
        return
    items = registrations(ctx, role, deployment)
    registered, location = _register_claude(ctx, items) if client == "claude" else _register_codex(ctx, items)
    ctx.reporter.end(
        "client",
        Status.OK,
        f"{LABELS[client]} now has {', '.join(registered)} for the {role} role; the tokens went into {location}",
    )


def register() -> None:
    """Give ``connect`` its body and the ``client`` input its wizard asks for."""

    command = engine.COMMANDS["connect"]
    command.handler = connect
    command.extra_inputs = [CLIENT_INPUT]
