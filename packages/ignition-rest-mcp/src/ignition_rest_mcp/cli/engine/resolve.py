"""Input resolution, Explicit acceptance and the equivalent command (D32 sections 3 and 6).

Each input is an :class:`InputSpec`. :func:`resolve` takes its value from the flag,
then from the saved deployment, then from the prompter. Without a prompter (stdin is
not a terminal, or ``--json``) a value that is still missing fails the run with
every missing flag listed, before any check runs and before any write.

A value that fails its check is asked again with the reason when there is a
prompter. Without one it fails the run.

:func:`accept` does the same for the named risks and legal terms of section 6, and
:func:`equivalent_command` prints the one-line command a wizard run amounts to.
"""

from __future__ import annotations

import asyncio
import re
import shlex
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from ignition_rest_mcp.cli.engine.deployment import Deployment, Value, read_secret
from ignition_rest_mcp.cli.engine.errors import CliError, ErrorCode
from ignition_rest_mcp.cli.engine.prompter import Prompter

PROG = "ignition-mcp"

#: A check returns ``""`` when the value is good, else the reason shown to the
#: operator. It gets the values resolved before it, keyed by input name.
Check = Callable[[str, Mapping[str, str]], str]


class Kind(StrEnum):
    TEXT = "text"
    #: One of ``choices``.
    CHOICE = "choice"
    #: A comma-separated subset of ``choices``, stored as a TOML list.
    LIST = "list"
    #: A path that must exist.
    PATH = "path"
    #: A secret. The flag names a file that holds it, the saved deployment keeps it
    #: in ``<secret_name>.secret``, and the wizard asks for it to be pasted.
    SECRET = "secret"


class Source(StrEnum):
    FLAG = "flag"
    SAVED = "saved"
    DEFAULT = "default"
    PROMPT = "prompt"


@dataclass(frozen=True, slots=True, repr=False)
class Secret:
    """A secret value that prints as ``<secret>`` everywhere except :meth:`reveal`."""

    _value: str

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "<secret>"

    __str__ = __repr__


@dataclass(frozen=True, slots=True)
class InputSpec:
    """One input a command needs.

    ``name`` is the key in ``deployment.toml`` and in :attr:`Resolved.values`.
    ``default`` may depend on the values resolved before this one, such as the roles
    that depend on the Deployment environment. A default is a value, never an
    acceptance.
    """

    name: str
    flag: str
    question: str
    kind: Kind = Kind.TEXT
    choices: tuple[str, ...] = ()
    default: str | Callable[[Mapping[str, str]], str | None] | None = None
    check: Check | None = None
    #: The error code when the check fails and nobody can be asked again.
    failure_code: ErrorCode = ErrorCode.INVALID_INPUT
    #: For :attr:`Kind.SECRET`: the secret file name inside the deployment.
    secret_name: str = ""

    def default_for(self, values: Mapping[str, str]) -> str | None:
        return self.default(values) if callable(self.default) else self.default


@dataclass(slots=True)
class Resolved:
    """What :func:`resolve` found, and where each value came from."""

    deployment: Deployment
    values: dict[str, str] = field(default_factory=dict)
    secrets: dict[str, Secret] = field(default_factory=dict)
    sources: dict[str, Source] = field(default_factory=dict)
    #: The file each secret was read from. A command that saves a prompted secret
    #: records the new path here so the equivalent command can name it.
    secret_files: dict[str, Path] = field(default_factory=dict)

    def list(self, name: str) -> list[str]:
        return [item for item in self.values[name].split(",") if item]

    def saveable(self, specs: Sequence[InputSpec]) -> dict[str, Value]:
        """The non-secret values in the shape ``save_deployment`` writes."""

        saved: dict[str, Value] = {}
        for spec in specs:
            if spec.kind is Kind.SECRET or spec.name not in self.values:
                continue
            saved[spec.name] = self.list(spec.name) if spec.kind is Kind.LIST else self.values[spec.name]
        return saved


# ----------------------------------------------------------------- resolution


def resolve(
    specs: Sequence[InputSpec],
    flags: Mapping[str, str | None],
    deployment: Deployment,
    prompter: Prompter | None,
) -> Resolved:
    """Resolve every input in order. Nothing here writes a file.

    ``flags`` maps an input name to its flag value, ``None`` when the flag was not
    given. ``prompter`` is ``None`` when nobody can answer.
    """

    resolved = Resolved(deployment)
    if prompter is None:
        missing = [spec.flag for spec in specs if _candidate(spec, flags, deployment, {}, True) is None]
        # A default that depends on a missing value cannot be known yet; the
        # missing value is already listed, so that is enough to fail on.
        if missing:
            raise CliError(
                ErrorCode.MISSING_INPUT,
                "stdin is not a terminal, so nothing can be asked; pass " + ", ".join(missing),
            )
    for spec in specs:
        # The wizard asks for a value that only has a default, showing the default.
        candidate = _candidate(spec, flags, deployment, resolved.values, prompter is None)
        if candidate is not None:
            raw, source = candidate
            reason = _take(spec, raw, source, resolved)
            if not reason:
                continue
            origin = spec.flag if source is Source.FLAG else f"the saved {spec.name}"
            if prompter is None:
                raise CliError(spec.failure_code, f"{origin}: {reason}")
            prompter.say(f"{origin}: {reason}")
        elif prompter is None:  # pragma: no cover - the missing pass above raised
            raise CliError(ErrorCode.MISSING_INPUT, f"pass {spec.flag}")
        _ask_until_valid(spec, prompter, resolved)
    return resolved


def _candidate(
    spec: InputSpec,
    flags: Mapping[str, str | None],
    deployment: Deployment,
    values: Mapping[str, str],
    use_default: bool,
) -> tuple[str, Source] | None:
    flag_value = flags.get(spec.name)
    if flag_value is not None:
        return flag_value, Source.FLAG
    if spec.kind is Kind.SECRET:
        path = deployment.secret_path(spec.secret_name)
        if path.exists() or path.is_symlink():
            return str(path), Source.SAVED
    else:
        saved = deployment.values.get(spec.name)
        if isinstance(saved, list):
            return ",".join(saved), Source.SAVED
        if isinstance(saved, str):
            return saved, Source.SAVED
    default = spec.default_for(values) if use_default else None
    if default is not None:
        return default, Source.DEFAULT
    return None


def _ask_until_valid(spec: InputSpec, prompter: Prompter, resolved: Resolved) -> None:
    default = spec.default_for(resolved.values)
    while True:
        if spec.kind is Kind.SECRET:
            answer = prompter.secret(spec.question)
        elif spec.kind is Kind.CHOICE:
            answer = prompter.select(spec.question, spec.choices, default)
        elif spec.kind is Kind.LIST:
            options = _list_options(spec.choices)
            answer = prompter.select(spec.question, options, default if default in options else None)
        else:
            answer = prompter.text(spec.question, default)
        reason = _take(spec, answer, Source.PROMPT, resolved)
        if not reason:
            return
        prompter.say(f"{reason}. Try again.")


def _list_options(choices: tuple[str, ...]) -> list[str]:
    """Every non-empty subset of a short choice list, the largest first."""

    options: list[str] = []
    for mask in range((1 << len(choices)) - 1, 0, -1):
        options.append(",".join(choice for index, choice in enumerate(choices) if mask & (1 << index)))
    return sorted(options, key=lambda option: -option.count(","))


def _take(spec: InputSpec, raw: str, source: Source, resolved: Resolved) -> str:
    """Normalize and check one value; store it and return ``""``, or return the reason."""

    value = raw.strip()
    if not value:
        return "a value is required"
    secret_file: Path | None = None
    if spec.kind is Kind.SECRET and source is not Source.PROMPT:
        secret_file = Path(value).expanduser()
        try:
            value = read_secret(secret_file)
        except CliError as error:
            return error.message
    elif spec.kind is Kind.SECRET and not re.fullmatch(r"[^:\s]+:\S+", value):
        return "a Gateway API key has the form <name>:<key>"
    elif spec.kind is Kind.CHOICE and value not in spec.choices:
        return f"{value!r} is not one of {', '.join(spec.choices)}"
    elif spec.kind is Kind.LIST:
        items = [item.strip() for item in value.split(",") if item.strip()]
        unknown = [item for item in items if item not in spec.choices]
        if unknown or not items:
            return f"{value!r} must be a comma-separated list of {', '.join(spec.choices)}"
        value = ",".join(choice for choice in spec.choices if choice in items)
    elif spec.kind is Kind.PATH:
        path = Path(value).expanduser()
        if not path.exists():
            return f"{path} does not exist"
        value = str(path)
    if spec.check is not None:
        reason = spec.check(value, resolved.values)
        if reason:
            return reason
    if spec.kind is Kind.SECRET:
        resolved.secrets[spec.name] = Secret(value)
        if secret_file is not None:
            resolved.secret_files[spec.name] = secret_file
    else:
        resolved.values[spec.name] = value
    resolved.sources[spec.name] = source
    return ""


# ------------------------------------------------------------- common checks


def check_readable_file(value: str, _values: Mapping[str, str]) -> str:
    """A check for :attr:`Kind.PATH` inputs that must be readable regular files."""

    path = Path(value)
    if not path.is_file():
        return f"{path} is not a file"
    try:
        with path.open("rb") as handle:
            handle.read(1)
    except OSError as error:
        return f"{path} cannot be read ({type(error).__name__})"
    return ""


def check_gateway_url(value: str, _values: Mapping[str, str]) -> str:
    try:
        parts = urlsplit(value)
        _ = parts.port  # raises ValueError for a port that is not a number
    except ValueError as error:
        return f"{value!r} is not a valid URL ({error.args[0]})"
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return f"{value!r} must be an absolute http or https URL, such as http://localhost:8088"
    if parts.username or parts.password:
        return "the URL must not carry a user name or password"
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        return "give the Gateway's base URL only, such as http://localhost:8088"
    return ""


#: Checks a Gateway API key against a Gateway URL; ``""`` when the Gateway accepts it.
TokenProbe = Callable[[str, str], str]


def gateway_token_check(probe: TokenProbe | None = None) -> Check:
    """A check that asks the Gateway at ``gateway_url`` whether it accepts the key.

    The resolver asks again when the check fails, so a rejected key never ends the
    run while someone can answer. A check that raises ``CliError`` ends the run at
    once, as an unreachable Gateway does. Tests pass a fake ``probe``.
    """

    use = probe or probe_gateway_token

    def check(value: str, values: Mapping[str, str]) -> str:
        url = values.get("gateway_url")
        if not url:
            return ""
        return use(url.rstrip("/"), value)

    return check


def probe_gateway_token(url: str, token: str, transport: httpx.AsyncBaseTransport | None = None) -> str:
    """Read gateway-info with ``token``; ``""`` on success, else a reason in words.

    A Gateway that never answers (DNS, TCP, TLS or a timeout) raises
    ``gateway_unreachable`` at once, because asking for another key cannot fix it.
    The reason never carries the token, and never a bare HTTP status (D32 section 4).
    """

    from ignition_rest_mcp.cli.setup_native import gateway as gw
    from ignition_rest_mcp.cli.setup_native.inputs import Endpoint

    parts = urlsplit(url)
    endpoint = Endpoint(
        url=url,
        scheme=parts.scheme,
        host=str(parts.hostname),
        port=parts.port or (443 if parts.scheme == "https" else 80),
    )

    async def read() -> None:
        async with gw.GatewayRest(endpoint, token, transport=transport) as client:
            await client.gateway_info()

    try:
        asyncio.run(read())
    except gw.GatewayProbeError as error:
        if isinstance(error.__cause__, httpx.TransportError):
            raise CliError(
                ErrorCode.GATEWAY_UNREACHABLE,
                f"cannot reach the Gateway at {url} ({type(error.__cause__).__name__})",
                next_action=f"curl -sSI {url}{gw.GATEWAY_INFO_PATH}",
            ) from None
        status = re.search(r"returned HTTP (\d{3})", str(error))
        if status is None:
            return f"the Gateway's answer to the key check could not be read ({error})"
        code = int(status.group(1))
        if code == 401:
            return "the Gateway does not know this key; copy the whole <name>:<key> line of a new API key"
        if code == 403:
            return (
                "the key's Security Level may not read the Gateway; tick it under every permission "
                "in Security > General Settings"
            )
        return f"the Gateway refused the key check with HTTP {code}"
    return ""


# ----------------------------------------------------------- Explicit acceptance


class Risk(StrEnum):
    """The named items of D32 section 6. The values are stable ``--json`` names."""

    CERTIFICATE = "module_certificate"
    EULA = "module_eula"
    RESTART = "gateway_restart"
    UNENCRYPTED_CHANNEL = "unencrypted_token_channel"
    WILDCARD_ALLOWLIST = "wildcard_target_allowlist"
    ADMIN_CLASS = "admin_mutation_class"
    NON_LOOPBACK_BIND = "non_loopback_bind"
    OVERWRITE_HAND_EDIT = "overwrite_hand_edit"


#: What accepting each item allows, as the wizard's yes or no question states it.
RISK_QUESTIONS: dict[Risk, str] = {
    Risk.CERTIFICATE: "Trust the MCP Module's signing certificate on this Gateway?",
    Risk.EULA: "Accept the MCP Module's EULA on this Gateway's behalf?",
    Risk.RESTART: "Allow a Gateway restart? Every session and client connection on it drops.",
    Risk.UNENCRYPTED_CHANNEL: "Allow Runtime tokens to travel over plain http to this Gateway?",
    Risk.WILDCARD_ALLOWLIST: "Allow '*' Target allowlists, so Mutations may touch every target D30 does not refuse?",
    Risk.ADMIN_CLASS: "Turn on ADMIN Mutations, which change the Gateway's own configuration?",
    Risk.NON_LOOPBACK_BIND: "Bind the REST server to an address other hosts can reach?",
    Risk.OVERWRITE_HAND_EDIT: "Overwrite a change someone made on the Gateway by hand?",
}

#: The items ``--yes`` never covers.
NAMED_FLAGS: dict[Risk, str] = {
    Risk.CERTIFICATE: "--accept-certificate",
    Risk.EULA: "--accept-eula",
}


def acceptance_flag(risk: Risk) -> str:
    return NAMED_FLAGS.get(risk, "--yes")


@dataclass(frozen=True, slots=True)
class Needed:
    """One item a run needs accepted. ``detail`` says what exactly, without secrets."""

    risk: Risk
    detail: str = ""


@dataclass(frozen=True, slots=True)
class AcceptFlags:
    yes: bool = False
    certificate: bool = False
    eula: bool = False

    def covers(self, risk: Risk) -> bool:
        if risk is Risk.CERTIFICATE:
            return self.certificate
        if risk is Risk.EULA:
            return self.eula
        return self.yes


@dataclass(frozen=True, slots=True)
class Accepted:
    risk: Risk
    detail: str
    #: ``flag`` or ``prompt``.
    how: str


def accept(needed: Sequence[Needed], flags: AcceptFlags, prompter: Prompter | None) -> list[Accepted]:
    """Accept every needed item, or fail before any write.

    Without a prompter every item the flags do not cover is listed with its flag. In
    the wizard each item is a yes or no question, and a no ends the run.
    """

    uncovered = [item for item in needed if not flags.covers(item.risk)]
    if uncovered and prompter is None:
        wanted = sorted({acceptance_flag(item.risk) for item in uncovered})
        names = ", ".join(item.risk.value for item in uncovered)
        raise CliError(
            ErrorCode.ACCEPTANCE_REQUIRED,
            f"this run needs acceptance of {names}; pass {' '.join(wanted)}. Nothing was written",
        )
    accepted = [Accepted(item.risk, item.detail, "flag") for item in needed if flags.covers(item.risk)]
    for item in uncovered:
        assert prompter is not None
        question = RISK_QUESTIONS[item.risk] + (f" ({item.detail})" if item.detail else "")
        if not prompter.confirm(question):
            raise CliError(
                ErrorCode.ACCEPTANCE_REQUIRED,
                f"{item.risk.value} was declined, so the run stopped. Nothing was written",
            )
        accepted.append(Accepted(item.risk, item.detail, "prompt"))
    return accepted


# ------------------------------------------------------------ equivalent command


SECRET_FILE_PLACEHOLDER = "FILE"


def equivalent_command(
    command: Sequence[str],
    specs: Sequence[InputSpec],
    resolved: Resolved,
    accepted: Sequence[Accepted] = (),
    extra: Sequence[str] = (),
) -> str:
    """The one-line command with the same values as a wizard run, and no secret.

    A secret appears as the path of the file it came from. A pasted secret that no
    command saved appears as ``FILE``, for the operator to replace with a file that
    holds it. ``command`` is the words after ``ignition-mcp``, such as ``["setup"]``.
    """

    words = [PROG, *command, "--deployment", resolved.deployment.name]
    for spec in specs:
        if spec.kind is Kind.SECRET:
            if spec.name in resolved.secrets:
                path = resolved.secret_files.get(spec.name)
                words += [spec.flag, str(path) if path is not None else SECRET_FILE_PLACEHOLDER]
        elif spec.name in resolved.values:
            words += [spec.flag, resolved.values[spec.name]]
    words += extra
    risks = {item.risk for item in accepted}
    words += [flag for risk, flag in NAMED_FLAGS.items() if risk in risks]
    if risks - set(NAMED_FLAGS):
        words.append("--yes")
    return shlex.join(words)
