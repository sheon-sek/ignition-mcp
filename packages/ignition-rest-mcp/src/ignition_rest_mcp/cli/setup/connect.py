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

Before anything is written, the entry a name already holds is read from that client's
own configuration. An identical entry is a no change; a different one is named with
the fields that differ, never their values, and is replaced only after the D32
section 6 overwrite acceptance, which ``--yes`` covers in one-line mode. Claude Code's
different entry keeps its place until the new content is registered under a temporary
name, and is put back if the registration under the real name fails.

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
from ignition_rest_mcp.cli.engine.resolve import PROG, InputSpec, Kind, Needed, Risk, accept
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
#: The name a replacement is registered under first, so the new content is known to
#: work before the existing entry under the real name is touched.
TEMP_SUFFIX = "-ignition-mcp-tmp"

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


def claude_config_path() -> Path:
    """Claude Code's user-scope state file, which holds the entries ``--scope user`` writes."""

    if SETTINGS.home is not None:
        return SETTINGS.home / ".claude.json"
    directory = os.environ.get("CLAUDE_CONFIG_DIR")
    if directory:
        return Path(directory).expanduser() / ".claude.json"
    return Path.home() / ".claude.json"


def _read_json_object(path: Path) -> dict[str, Any]:
    """The JSON object at ``path``; an absent file is ``{}`` and an unreadable one is refused."""

    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError) as error:
        raise CliError(
            ErrorCode.INVALID_INPUT,
            f"{path} cannot be read ({type(error).__name__}); fix it before connecting",
            next_action=f"{PROG} connect --help",
        ) from error
    try:
        document = json.loads(text)
    except ValueError as error:
        raise CliError(
            ErrorCode.INVALID_INPUT,
            f"{path} is not valid JSON ({error}); fix it before connecting",
            next_action=f"{PROG} connect --help",
        ) from error
    return document if isinstance(document, dict) else {}


def _claude_entries() -> dict[str, Any]:
    """Every MCP entry Claude Code holds at the user scope, by name.

    Reading its own state file is how this CLI learns whether a name is already taken;
    the writes still go through ``claude mcp add``.
    """

    servers = _read_json_object(claude_config_path()).get("mcpServers")
    return servers if isinstance(servers, dict) else {}


def _diff(client: str, entry: dict[str, Any], item: Registration) -> list[str]:
    """What differs between an existing entry and ours, naming fields and never values."""

    drift: list[str] = []
    if entry.get("url") != item.url:
        drift.append("its url")
    headers = entry.get("headers") if client == "claude" else entry.get("http_headers")
    if headers != {item.header: item.value}:
        drift.append(f"its {item.header} header")
    return drift


def existing_entries(client: str) -> dict[str, Any]:
    """Every entry the client already holds under our names, as one snapshot."""

    if client == "claude":
        return _claude_entries()
    return _codex_servers(_read_codex())


def _collisions(client: str, entries: dict[str, Any], items: Sequence[Registration]) -> list[Needed]:
    """The acceptance items the existing entries of this client need, if any."""

    needed: list[Needed] = []
    for item in items:
        entry = entries.get(item.name)
        if not isinstance(entry, dict):
            continue
        drift = _diff(client, entry, item)
        if drift:
            needed.append(
                Needed(Risk.OVERWRITE_HAND_EDIT, f"the {LABELS[client]} entry {item.name}: {', '.join(drift)}")
            )
    return needed


def _connect_next(ctx: engine.Context) -> str:
    return f"{PROG} connect {getattr(ctx.args, 'role', '<role>')} --deployment {ctx.deployment.name}"


def _client(ctx: engine.Context, argv: Sequence[str], what: str) -> RunResult:
    """Run one client command, and stop the run when the client refuses it."""

    result = SETTINGS.run(argv)
    if result.code != 0:
        raise CliError(
            ErrorCode.STEP_FAILED,
            f"{what} (exit {result.code}){_snippet(result)}",
            next_action=_connect_next(ctx),
        )
    return result


def _add_argv(binary: str, name: str, item: Registration) -> list[str]:
    return [
        binary,
        "mcp",
        "add",
        "--transport",
        "http",
        name,
        item.url,
        "--scope",
        CLAUDE_SCOPE,
        "--header",
        item.line,
    ]


def _remove_argv(binary: str, name: str) -> list[str]:
    return [binary, "mcp", "remove", name, "--scope", CLAUDE_SCOPE]


def _restore_claude(ctx: engine.Context, item: Registration, old: dict[str, Any], temp: str) -> str:
    """Put the previous entry back with the client's own JSON form; returns what happened."""

    binary = BINARIES["claude"]
    SETTINGS.run(_remove_argv(binary, temp))
    result = SETTINGS.run([binary, "mcp", "add-json", item.name, json.dumps(old), "--scope", CLAUDE_SCOPE])
    if result.code != 0:
        raise CliError(
            ErrorCode.STEP_FAILED,
            f"the previous entry {item.name} was not restored (exit {result.code}){_snippet(result)}",
            next_action=_connect_next(ctx),
        )
    return f"the previous entry {item.name} was restored"


def _register_claude(
    ctx: engine.Context, items: Sequence[Registration], entries: dict[str, Any]
) -> tuple[list[str], str]:
    """Register through ``claude mcp add``, its own configuration mechanism."""

    binary = BINARIES["claude"]
    registered: list[str] = []
    for item in items:
        with ctx.reporter.step(f"claude {item.name}", item.url) as end:
            old = entries.get(item.name)
            if isinstance(old, dict) and not _diff("claude", old, item):
                end.set(Status.OK, f"already registered in {claude_config_path()} as this deployment wants it")
                registered.append(item.name)
                continue
            if not isinstance(old, dict):
                _client(ctx, _add_argv(binary, item.name, item), f"Claude Code refused the registration of {item.name}")
                end.set(Status.CHANGED, f"registered with Claude Code, scope {CLAUDE_SCOPE}")
                registered.append(item.name)
                continue
            # A different entry owns this name and someone else may depend on it. The new
            # content is registered under a temporary name first, so the old entry is only
            # removed once the new one is known to work, and is restored if the rename fails.
            temp = f"{item.name}{TEMP_SUFFIX}"
            _client(ctx, _add_argv(binary, temp, item), f"Claude Code refused the registration of {temp}")
            try:
                _client(ctx, _remove_argv(binary, item.name), f"Claude Code would not replace {item.name}")
            except CliError:
                # The existing entry is still there, so only the temporary one goes.
                SETTINGS.run(_remove_argv(binary, temp))
                raise
            try:
                _client(
                    ctx, _add_argv(binary, item.name, item), f"Claude Code refused the registration of {item.name}"
                )
            except CliError as error:
                note = _restore_claude(ctx, item, old, temp)
                raise CliError(error.code, f"{error.message}; {note}", error.next_action) from error
            _client(ctx, _remove_argv(binary, temp), f"Claude Code would not remove the temporary entry {temp}")
            end.set(
                Status.CHANGED,
                f"replaced a different entry of the same name in {claude_config_path()}, which was restored "
                "first if the registration had failed",
            )
            registered.append(item.name)
    return registered, f"Claude Code's own configuration, scope {CLAUDE_SCOPE}"


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
    own = f"[mcp_servers.{item.name}."
    end = start + 1
    while end < len(lines):
        stripped = lines[end].strip()
        if stripped.startswith("[") and not stripped.startswith(own):
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


def _read_codex() -> str:
    """Codex's configuration as text; an absent file is empty."""

    path = codex_config_path()
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    except (OSError, UnicodeDecodeError) as error:
        raise CliError(
            ErrorCode.INVALID_INPUT,
            f"{path} cannot be read ({type(error).__name__})",
            next_action=_codex_next(),
        ) from error


def _write_codex(path: Path, text: str) -> None:
    """Write the configuration atomically, with mode ``0600`` because it holds a token."""

    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    staging = path.with_name(path.name + ".tmp")
    staging.write_text(text, encoding="utf-8", newline="\n")
    os.chmod(staging, 0o600)
    os.replace(staging, path)


def _register_codex(
    ctx: engine.Context, items: Sequence[Registration], entries: dict[str, Any]
) -> tuple[list[str], str]:
    """Register in Codex's own configuration file, leaving every other table alone."""

    path = codex_config_path()
    registered: list[str] = []
    for item in items:
        with ctx.reporter.step(f"codex {item.name}", item.url) as end:
            replaced = isinstance(entries.get(item.name), dict) and _diff("codex", entries[item.name], item)
            updated = codex_config_text(_read_codex(), [item])
            if updated is None:
                end.set(Status.OK, f"{item.name} is already registered in {path} as this deployment wants it")
            else:
                _write_codex(path, updated)
                what = "replaced a different entry of the same name" if replaced else "registered"
                end.set(Status.CHANGED, f"{what} in {path}, mode 0600, header {item.header}")
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
    entries = existing_entries(client)
    # A name this client already holds with different content is someone else's entry
    # until the operator says otherwise, so the acceptance is taken before any write.
    accepted = accept(_collisions(client, entries, items), ctx.accept_flags, ctx.prompter)
    ctx.accepted.extend(accepted)
    ctx.reporter.accepted(accepted)
    if client == "claude":
        registered, location = _register_claude(ctx, items, entries)
    else:
        registered, location = _register_codex(ctx, items, entries)
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
