"""``ignition-mcp start`` (D32 sections 2, 5 and 8, issue #75).

``start`` reads the deployment directory that ``setup`` wrote and derives every
``IGNITION_MCP_*`` value from it, so the operator never sets one:

* the Gateway URL and the ``ignition-mcp-rest`` key,
* ``static-token`` authentication with one Named static token per deployed role,
* the REST Mutation settings saved by the REST stage,
* the bind address, ``127.0.0.1:8000`` unless ``--bind`` names another,
* a data directory inside the deployment directory.

An ``IGNITION_MCP_*`` variable already in the environment is ignored. The server
then runs in the foreground. Once it answers ``/health/ready``, ``start`` reports
the result and each role's endpoints, and it keeps serving until Ctrl+C.

A bind address other hosts can reach needs Explicit acceptance (D32 section 6).
The server then runs with the ``trusted-internal`` profile, because the
``development`` profile refuses such an address.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from ignition_rest_mcp.cli.engine import main as engine
from ignition_rest_mcp.cli.engine.deployment import DIRECTORY_MODE, Deployment
from ignition_rest_mcp.cli.engine.errors import CliError, ErrorCode
from ignition_rest_mcp.cli.engine.report import Status
from ignition_rest_mcp.cli.engine.resolve import PROG, Needed, Risk, accept
from ignition_rest_mcp.cli.setup.rest import (
    REST_TOKEN_SECRET,
    ROLE_SCOPES,
    ROLE_TOKEN_NAMES,
    RestSettings,
    static_token_secret,
    static_token_value,
)
from ignition_rest_mcp.cli.engine.deployment import read_secret
from ignition_rest_mcp.config import ConfigurationError, Settings, _is_loopback
from ignition_rest_mcp.projects.transactions import PROJECT_IMPORT_OPERATION
from ignition_rest_mcp.safety.policy import ADMIN_MUTATION, CONFIG_MUTATION, CONTROL_MUTATION, MutationOperation
from ignition_rest_mcp.services.alarm_pipeline_cancel import ALARM_PIPELINE_CANCEL
from ignition_rest_mcp.services.artifact_delete import ARTIFACT_DELETE
from ignition_rest_mcp.services.config_mutation import (
    CONFIG_RESOURCE_CREATE,
    CONFIG_RESOURCE_DELETE,
    CONFIG_RESOURCE_RENAME,
    CONFIG_RESOURCE_UPDATE,
)
from ignition_rest_mcp.services.perspective_write import (
    PAGE_CONFIG_UPDATE,
    SESSION_PROPS_UPDATE,
    VIEW_DELETE,
    VIEW_UPSERT,
)
from ignition_rest_mcp.services.tag_config_import import TAG_CONFIG_IMPORT

DEFAULT_BIND = "127.0.0.1:8000"
MCP_PATH = "/mcp"
DATA_DIRECTORY = "rest-data"
ENV_PREFIX = "IGNITION_MCP_"
HEALTH_DEADLINE_SECONDS = 30.0
HEALTH_POLL_SECONDS = 0.25

#: Every REST Mutation operation, so an enabled class can allowlist its operations.
OPERATIONS: tuple[MutationOperation, ...] = (
    PROJECT_IMPORT_OPERATION,
    VIEW_UPSERT,
    VIEW_DELETE,
    PAGE_CONFIG_UPDATE,
    SESSION_PROPS_UPDATE,
    TAG_CONFIG_IMPORT,
    CONFIG_RESOURCE_UPDATE,
    CONFIG_RESOURCE_CREATE,
    CONFIG_RESOURCE_DELETE,
    CONFIG_RESOURCE_RENAME,
    ARTIFACT_DELETE,
    ALARM_PIPELINE_CANCEL,
)
CLASS_NAMES = {"config": CONFIG_MUTATION, "control": CONTROL_MUTATION, "admin": ADMIN_MUTATION}

#: Called once the server answers ``/health/ready``: the HTTP status (``0`` when it
#: never answered) and the decoded body.
HealthReport = Callable[[int, dict[str, Any]], None]
#: Runs the server in the foreground and calls the report once it is up.
Serve = Callable[[Settings, HealthReport], None]


# ------------------------------------------------------------------ bind address


def parse_bind(value: str) -> tuple[str, int]:
    """``HOST:PORT`` or ``[IPv6]:PORT``."""

    try:
        parts = urlsplit("//" + value)
        port = parts.port
    except ValueError:
        port = None
        parts = None
    if parts is None or not parts.hostname or port is None or parts.path or parts.username or parts.query:
        raise CliError(
            ErrorCode.INVALID_INPUT,
            f"--bind {value!r} must be HOST:PORT, such as {DEFAULT_BIND}",
            next_action=f"{PROG} start --bind {DEFAULT_BIND}",
        )
    return parts.hostname, port


def _url_host(host: str) -> str:
    return f"[{host}]" if ":" in host else host


def _connect_host(host: str) -> str:
    """The address a local client uses to reach a server bound to ``host``."""

    return {"0.0.0.0": "127.0.0.1", "::": "::1"}.get(host, host)


# --------------------------------------------------------------- server settings


def server_environment(deployment: Deployment, host: str, port: int) -> dict[str, str]:
    """Every ``IGNITION_MCP_*`` value the REST server needs, from the deployment."""

    values = deployment.values
    url = values.get("gateway_url")
    roles = values.get("roles")
    if not isinstance(url, str) or not isinstance(roles, list) or not roles:
        raise CliError(
            ErrorCode.DEPLOYMENT_UNREADABLE,
            f"{deployment.directory} has no Gateway URL or roles; setup has not finished",
            next_action=f"{PROG} setup --deployment {deployment.name}",
        )
    settings = RestSettings.from_values(values)
    tokens = {
        ROLE_TOKEN_NAMES[role]: {"token": static_token_value(deployment, role), "scopes": list(ROLE_SCOPES[role])}
        for role in roles
    }
    enabled = {CLASS_NAMES[name] for name in settings.classes}
    operations = [operation.op_id for operation in OPERATIONS if operation.mutation_class in enabled]
    environment = {
        "IGNITION_MCP_GATEWAY_URL": url,
        "IGNITION_MCP_GATEWAY_API_TOKEN": read_secret(deployment.secret_path(REST_TOKEN_SECRET)),
        "IGNITION_MCP_HOST": host,
        "IGNITION_MCP_PORT": str(port),
        "IGNITION_MCP_PATH": MCP_PATH,
        "IGNITION_MCP_DEPLOYMENT_PROFILE": "development" if _is_loopback(host) else "trusted-internal",
        "IGNITION_MCP_AUTH_MODE": "static-token",
        "IGNITION_MCP_STATIC_TOKENS": json.dumps(tokens),
        "IGNITION_MCP_DATA_DIR": str(deployment.directory / DATA_DIRECTORY),
        "IGNITION_MCP_CONFIG_MUTATION_ENABLED": _bool(CONFIG_MUTATION in enabled),
        "IGNITION_MCP_CONTROL_MUTATION_ENABLED": _bool(CONTROL_MUTATION in enabled),
        "IGNITION_MCP_ADMIN_MUTATION_ENABLED": _bool(ADMIN_MUTATION in enabled),
        "IGNITION_MCP_MUTATION_OPERATIONS": ",".join(operations),
        "IGNITION_MCP_PROJECT_WRITER_ENABLED": _bool(settings.project_writer),
    }
    if settings.wildcard_targets and operations:
        environment["IGNITION_MCP_MUTATION_TARGETS"] = json.dumps({op_id: ["*"] for op_id in operations})
    if settings.project_writer:
        # One stable ID per Gateway; a deployment is one Gateway (D32 section 9).
        environment["IGNITION_MCP_GATEWAY_ID"] = deployment.name
    return environment


def _bool(value: bool) -> str:
    return "true" if value else "false"


@contextmanager
def _only(environment: Mapping[str, str]) -> Iterator[None]:
    """Replace every ``IGNITION_MCP_*`` variable with ``environment`` for the block."""

    saved = dict(os.environ)
    try:
        for name in [name for name in os.environ if name.startswith(ENV_PREFIX)]:
            del os.environ[name]
        os.environ.update(environment)
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


def settings_from(environment: Mapping[str, str]) -> Settings:
    """Validate the derived values exactly as the server does at startup."""

    with _only(environment):
        try:
            return Settings.from_env()
        except (ConfigurationError, ValueError) as error:
            raise CliError(ErrorCode.STEP_FAILED, f"the REST server refused the derived settings: {error}") from None


# ------------------------------------------------------------------------ serving


def serve_foreground(settings: Settings, report: HealthReport) -> None:
    """Run the REST server until Ctrl+C, and report its health once it answers."""

    from ignition_rest_mcp.observability.logging import configure_logging
    from ignition_rest_mcp.server import create_server

    configure_logging(log_format=settings.resolved_log_format, level="INFO")
    # The health poll below is start's own traffic, not the server's.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    mcp = create_server(settings)

    async def run() -> None:
        server = asyncio.create_task(
            mcp.run_http_async(
                transport="streamable-http",
                host=settings.bind_host,
                port=settings.bind_port,
                path=settings.mcp_path,
                show_banner=False,
            )
        )
        base = f"http://{_url_host(_connect_host(settings.bind_host))}:{settings.bind_port}"
        report(*await _await_health(base, server))
        await server

    try:
        asyncio.run(run())
    except SystemExit:
        # uvicorn exits this way when it cannot listen, such as a port in use.
        raise CliError(
            ErrorCode.STEP_FAILED,
            f"the REST server could not listen on {_url_host(settings.bind_host)}:{settings.bind_port}",
            next_action=f"{PROG} start --bind 127.0.0.1:8001",
        ) from None


async def _await_health(base: str, server: asyncio.Task[None]) -> tuple[int, dict[str, Any]]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + HEALTH_DEADLINE_SECONDS
    last: tuple[int, dict[str, Any]] = (0, {})
    async with httpx.AsyncClient(timeout=5.0) as client:
        while not server.done() and loop.time() < deadline:
            try:
                response = await client.get(base + "/health/ready")
                body = response.json()
                last = (response.status_code, body if isinstance(body, dict) else {})
                if response.status_code == 200:
                    return last
            except (httpx.HTTPError, ValueError):
                pass
            await asyncio.sleep(HEALTH_POLL_SECONDS)
    return last


#: Replaced in tests, so no test binds a port.
SERVE: Serve = serve_foreground


# ------------------------------------------------------------------------ command


def start(ctx: engine.Context) -> None:
    host, port = parse_bind(getattr(ctx.args, "bind", None) or DEFAULT_BIND)
    if not _is_loopback(host):
        items = accept([Needed(Risk.NON_LOOPBACK_BIND, f"{_url_host(host)}:{port}")], ctx.accept_flags, ctx.prompter)
        ctx.accepted.extend(items)
        ctx.reporter.accepted(items)
    deployment = ctx.deployment
    with ctx.reporter.step("deployment", f"reading {deployment.directory}") as end:
        if not deployment.exists:
            raise CliError(
                ErrorCode.DEPLOYMENT_UNREADABLE,
                f"{deployment.directory} has no deployment.toml",
                next_action=f"{PROG} setup --deployment {deployment.name}",
            )
        environment = server_environment(deployment, host, port)
        tokens: dict[str, dict[str, Any]] = json.loads(environment["IGNITION_MCP_STATIC_TOKENS"])
        roles = [role for role, name in ROLE_TOKEN_NAMES.items() if name in tokens]
        ctx.reporter.hide(
            environment["IGNITION_MCP_GATEWAY_API_TOKEN"], *(str(entry["token"]) for entry in tokens.values())
        )
        settings = settings_from(environment)
        _make_data_directory(Path(settings.data_dir))
        end.set(
            Status.OK,
            f"{settings.deployment_profile} profile, static-token auth for {', '.join(roles)}, "
            f"{RestSettings.from_values(deployment.values).describe()}",
        )

    rest_url = f"http://{_url_host(host)}:{port}{MCP_PATH}"
    gateway_url = str(deployment.values["gateway_url"]).rstrip("/")

    def report(status: int, body: dict[str, Any]) -> None:
        with ctx.reporter.step("health", f"GET {rest_url.removesuffix(MCP_PATH)}/health/ready") as end:
            if status == 200:
                end.set(Status.OK, f"ready: registry {body.get('registryState')}, storage ready")
            else:
                reason = (
                    f"not ready after {HEALTH_DEADLINE_SECONDS:.0f} s: registry {body.get('registryState')}, "
                    f"storage ready {body.get('storageReady')}"
                    if status
                    else f"the server did not answer within {HEALTH_DEADLINE_SECONDS:.0f} s"
                )
                end.set(
                    Status.FAILED,
                    reason + "; the server keeps running",
                    next_action=f"{PROG} status --deployment {deployment.name}",
                    code=ErrorCode.STEP_FAILED.value,
                )
        for role in roles:
            with ctx.reporter.step(f"endpoint {role}", f"the {role} Assistant role") as end:
                token = deployment.secret_path(static_token_secret(role))
                end.set(
                    Status.OK,
                    f"REST {rest_url} with the Named static token {ROLE_TOKEN_NAMES[role]} in {token}; "
                    f"Runtime {gateway_url}/data/mcp/{role}",
                )
        with ctx.reporter.step("serve", "the REST server runs in the foreground") as end:
            end.set(Status.OK, "serving until Ctrl+C")

    try:
        SERVE(settings, report)
    except KeyboardInterrupt:
        pass
    with ctx.reporter.step("stop", "the REST server") as end:
        end.set(Status.OK, "stopped")


def _make_data_directory(path: Path) -> None:
    path.mkdir(mode=DIRECTORY_MODE, parents=True, exist_ok=True)


def _flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--bind",
        default=None,
        metavar="HOST:PORT",
        help=f"address the REST server listens on (default: {DEFAULT_BIND}); another host needs --yes",
    )


def _words(ctx: engine.Context) -> list[str]:
    bind = getattr(ctx.args, "bind", None)
    return ["--bind", bind] if bind else []


def register() -> None:
    """Give ``start`` its body and its ``--bind`` flag."""

    command = engine.COMMANDS["start"]
    command.handler = start
    command.configure = _flags
    command.words = _words
