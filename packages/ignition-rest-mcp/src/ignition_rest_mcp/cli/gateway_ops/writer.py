"""The curated, guarded write path the CLI changes a Gateway through (D20, D30).

The CLI is the only part of this repo that writes a Gateway outside the D08 mutation
chain, so this write path is deliberately small and named:

* **Only these routes.** Each operation has one method here, and every path is built
  from a module constant: import a Project archive, create or modify the MCP Server
  Config resource, create the reserved policy Tag provider and import its Tags,
  upload a Module, accept its certificate, accept its EULA, install it, restart the
  Gateway. No caller can address another route, and no request body is a
  pass-through of operator input.
* **Guards before dispatch.** A name must match the CLI's name grammar, a Tool
  list must be explicit (``*`` is refused, D09/D20), the reserved provider name
  is the module constant, and an upload body must already sit inside the size bound.
* **Bounded and redacted.** Every response is size-bounded and every error message
  is scrubbed of the API token before it can reach a report.
* **No blind retry.** The one retry loop is the policy Tag import: it re-sends the
  *same* document idempotently under a deadline, because ticket #6 recorded a
  freshly created Tag provider answering the first import with ``Bad 776`` while it
  starts, and because an accepted import is not proof that the provider serves the
  Tags. Every write's effect is confirmed by a read-back the caller performs.

Reads go through :class:`~ignition_rest_mcp.cli.gateway_ops.gateway.GatewayRest`,
which stays GET-only; this module owns the POST/PUT half.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import httpx

from ignition_rest_mcp.cli.gateway_ops import documents
from ignition_rest_mcp.cli.gateway_ops.gateway import (
    GatewayProbeError,
    GatewayRest,
    _reason,
    _redact,
    _snippet,
)
from ignition_rest_mcp.cli.gateway_ops.inputs import (
    API_TOKEN_TYPE,
    CONFIG_COLLECTION,
    MAX_MODULE_BYTES,
    MODULE_NAME_TOKEN,
    SECURITY_LEVELS_TYPE,
    SERVER_CONFIG_TYPE,
    NAME_TOKEN,
    Endpoint,
)

#: Write routes, one per operation. Nothing else is reachable from this module.
PROJECT_IMPORT_PATH = "/data/api/v1/projects/import/{name}"
PROJECT_EXPORT_PATH = "/data/api/v1/projects/export/{name}"
RESOURCE_COLLECTION_PATH = "/data/api/v1/resources/{resource_type}"
TAG_IMPORT_PATH = "/data/api/v1/tags/import"
#: The Gateway's own key/hash generator for a new API token (ticket #22). It persists
#: nothing: the pair it answers with is what the token create below stores.
API_TOKEN_GENERATE_PATH = "/data/api/v1/api-token/generate"
#: The Module flow of ``install-module`` (D20), one route per step of the documented
#: sequence: upload the archive, accept what it carries, install it, restart.
MODULE_UPLOAD_PATH = "/data/api/v1/modules/upload"
MODULE_CERTIFICATE_ACCEPT_PATH = "/data/api/v1/modules/certificate"
MODULE_EULA_ACCEPT_PATH = "/data/api/v1/modules/eula"
MODULE_INSTALL_PATH = "/data/api/v1/modules/install"
GATEWAY_RESTART_PATH = "/data/api/v1/restart-tasks/restart"
#: The 8.3.8 OpenAPI describes the upload body as "a binary stream" and names no media
#: type. ``application/octet-stream`` is what the repo sends for any other raw body
#: (the Tag import); T3's live run confirms it against a real Gateway.
MODULE_UPLOAD_CONTENT_TYPE = "application/octet-stream"
#: Both accept routes answer 409 when the certificate or EULA is already accepted,
#: which is the state this command wanted anyway.
ALREADY_ACCEPTED_STATUS = 409
#: What one acceptance step reported: this run accepted it, or the Gateway already had.
ACCEPTED = "ACCEPTED"
ALREADY_ACCEPTED = "ALREADY_ACCEPTED"

MAX_WRITE_RESPONSE_BYTES = 1_048_576
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
#: The policy import deadline and poll interval (ticket #6: the provider must be
#: given time to start serving the Tags it was asked to hold).
POLICY_DEADLINE_SECONDS = 120.0
PROVIDER_READY_SECONDS = 60.0
RETRY_INTERVAL_SECONDS = 3.0


class WriteError(RuntimeError):
    """A guarded write failed; the message is safe to report.

    ``status`` carries the HTTP status when the Gateway answered one, so a caller can
    tell a startup state it may wait out from a refusal it must not retry.
    """

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


class WriteRefused(WriteError):
    """The Gateway answered 2xx and refused the write inside the body.

    D30 §2 makes an explicit refusal final for the caller, so the one bounded retry
    loop this module has never re-sends a refused write.
    """

    def __init__(self, message: str, status: int = 200) -> None:
        super().__init__(message)
        self.status = status


@dataclass(slots=True)
class PolicyImport:
    """The outcome of the bounded policy import loop."""

    attempts: list[dict[str, Any]] = field(default_factory=list)

    @property
    def attempt_count(self) -> int:
        return len(self.attempts)

    @property
    def last(self) -> dict[str, Any]:
        return self.attempts[-1] if self.attempts else {}


def refusal(payload: Any) -> str:
    """The Gateway's own refusal inside a 2xx body, or ``""`` when it claimed success.

    The documented write shape is ``{success, changes, problem}``; a ``problem``
    object or ``success: false`` is the Gateway refusing the write, and D30 §2 makes
    an explicit refusal final for the caller.
    """

    if not isinstance(payload, dict):
        return ""
    if payload.get("success") is False:
        problem = payload.get("problem")
        message = problem.get("message") if isinstance(problem, dict) else None
        return str(message or "the Gateway reported success=false")
    problem = payload.get("problem")
    if isinstance(problem, dict) and problem:
        return str(problem.get("message") or "the Gateway reported a problem")
    return ""


def import_failures(payload: Any) -> list[Any] | None:
    """Normalize the two observed Tag import wire shapes; ``None`` means no failure.

    Committed 8.3.8 describes a list of non-Good QualityCodes; live 8.3.8/8.3.9
    answer a summary object. Anything unrecognized counts as a failure, so the
    caller fails closed instead of reading an uninterpretable body as success.
    """

    if payload is None or payload == []:
        return None
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        failures = payload.get("failures")
        count = payload.get("failureCount")
        if count == 0 and (failures is None or failures == []):
            return None
        return failures if isinstance(failures, list) and failures else [payload]
    return [payload]


def _retryable(error: WriteError) -> bool:
    """Whether a failed policy import may be re-sent: a transport error, a 404 (the
    provider is not serving yet) or a server-side status. Any other 4xx is final."""

    return error.status == 0 or error.status == 404 or error.status >= 500


def guard_name(value: str, what: str) -> str:
    """Validate a name this CLI is willing to address; never a path fragment."""

    name = str(value).strip()
    if NAME_TOKEN.fullmatch(name) is None:
        raise WriteError(f"{what} {value!r} must match [A-Za-z0-9][A-Za-z0-9._-]{{0,63}}")
    return name


def guard_module_token(value: str, what: str) -> str:
    """Validate a module id or an upload file name: one path-free token, never a URL fragment."""

    token = str(value).strip()
    if MODULE_NAME_TOKEN.fullmatch(token) is None:
        raise WriteError(f"{what} {value!r} must match [A-Za-z0-9][A-Za-z0-9._-]{{0,127}}")
    return token


def guard_tool_list(tools: Any, what: str) -> list[str]:
    """D09/D20: the written Tool inventory is explicit and never a wildcard."""

    if not isinstance(tools, (list, tuple)) or not tools:
        raise WriteError(f"{what} must be a non-empty explicit Tool list")
    names: list[str] = []
    for tool in tools:
        if not isinstance(tool, str) or tool.strip() == "*":
            raise WriteError(f"{what} must not contain a wildcard; got {tool!r}")
        if NAME_TOKEN.fullmatch(tool.strip()) is None:
            raise WriteError(f"{what}: {tool!r} is not a Tool name")
        names.append(tool.strip())
    return names


class GatewayWriter:
    """The curated write half, guarded and bounded."""

    def __init__(
        self,
        endpoint: Endpoint,
        api_token: str,
        *,
        timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._token = api_token
        self._endpoint = endpoint
        self._reads = GatewayRest(endpoint, api_token, timeout_seconds=timeout_seconds, transport=transport)
        self._client = httpx.AsyncClient(
            base_url=endpoint.url,
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "X-Ignition-API-Token": api_token,
                "User-Agent": "ignition-mcp",
            },
            follow_redirects=False,
            timeout=httpx.Timeout(timeout_seconds),
            transport=transport,
        )

    @property
    def reads(self) -> GatewayRest:
        """The read-only client over the same origin (GET routes only)."""

        return self._reads

    async def __aenter__(self) -> GatewayWriter:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()
        await self._reads.aclose()

    # ------------------------------------------------------------------ projects

    async def project_export(self, name: str) -> bytes:
        """The Project archive the Gateway currently serves (bounded)."""

        target = guard_name(name, "project name")
        return await self._archive(
            PROJECT_EXPORT_PATH.format(name=quote(target, safe="")), "project export",
        )

    async def import_project(self, name: str, archive: bytes, *, overwrite: bool) -> dict[str, Any]:
        """Import one Project archive; D20 forbids a blind overwrite."""

        target = guard_name(name, "project name")
        if not archive.startswith(b"PK"):
            raise WriteError("the bundle archive is not a ZIP file")
        params = {"overwrite": "true"} if overwrite else None
        return _as_object(await self._write(
            "POST",
            PROJECT_IMPORT_PATH.format(name=quote(target, safe="")),
            body=archive,
            content_type="application/zip",
            params=params,
            action="import project",
        ), "import project")

    # ------------------------------------------------------------- server config

    async def create_server_config(
        self, name: str, config: dict[str, Any], *, enabled: bool
    ) -> dict[str, Any]:
        """Create the MCP Server Config resource (one explicit Tool list)."""

        item = self._server_config_change(name, config, enabled=enabled)
        return _as_object(await self._write(
            "POST",
            RESOURCE_COLLECTION_PATH.format(resource_type=SERVER_CONFIG_TYPE),
            body=json.dumps([item], separators=(",", ":")).encode("utf-8"),
            content_type="application/json",
            action="create server config",
        ), "create server config")

    async def update_server_config(
        self, name: str, config: dict[str, Any], *, signature: str, enabled: bool
    ) -> dict[str, Any]:
        """Modify the MCP Server Config resource, carrying the Resource signature."""

        item = self._server_config_change(name, config, enabled=enabled)
        if not isinstance(signature, str) or not signature.strip():
            raise WriteError(
                f"server config {name!r} read back no signature; refusing to modify it blind"
            )
        item["signature"] = signature
        return _as_object(await self._write(
            "PUT",
            RESOURCE_COLLECTION_PATH.format(resource_type=SERVER_CONFIG_TYPE),
            body=json.dumps([item], separators=(",", ":")).encode("utf-8"),
            content_type="application/json",
            action="modify server config",
        ), "modify server config")

    def _server_config_change(self, name: str, config: dict[str, Any], *, enabled: bool) -> dict[str, Any]:
        target = guard_name(name, "server config name")
        if not isinstance(config, dict):
            raise WriteError("the desired Server Config must be an object")
        tools = config.get("tools")
        if not isinstance(tools, dict) or not tools:
            raise WriteError("the desired Server Config must map at least one project's Tools")
        # Every entry this CLI writes is explicit. An entry for another project is the
        # operator's and travels verbatim, so it is not re-validated here.
        for entry in tools.values():
            guard_tool_list(entry, f"the Server Config {target!r} Tool list")
        permissions = config.get("permissions")
        if not isinstance(permissions, dict) or not permissions:
            raise WriteError(
                f"refusing to write server config {target!r} without a permissions tree: "
                "an unauthenticated MCP endpoint is not a writable desired state"
            )
        return {
            "name": target,
            "collection": CONFIG_COLLECTION,
            "enabled": bool(enabled),
            "description": "",
            "config": config,
        }

    # ------------------------------------------------------- security planes

    async def update_security_levels(
        self, tree: list[dict[str, Any]], *, signature: str, collection: str
    ) -> dict[str, Any]:
        """Modify the Security Levels singleton, carrying the Resource signature (D20).

        The change item carries only what this CLI owns — the security tree — so the
        singleton's own description and enabled flag travel untouched, and D20's
        optimistic precondition is the signature read moments earlier.
        """

        if not isinstance(signature, str) or not signature.strip():
            raise WriteError("the security-levels singleton read back no signature; refusing to modify it blind")
        if not isinstance(tree, list) or not tree:
            raise WriteError("the desired security tree must be a non-empty list of levels")
        item = {
            "collection": guard_name(collection, "collection"),
            "signature": signature,
            "config": {"securityLevels": tree},
        }
        action = "modify security levels"
        return _as_object(await self._write(
            "PUT",
            RESOURCE_COLLECTION_PATH.format(resource_type=SECURITY_LEVELS_TYPE),
            body=json.dumps([item], separators=(",", ":")).encode("utf-8"),
            content_type="application/json",
            action=action,
        ), action)

    async def generate_api_token(self) -> dict[str, Any]:
        """One Gateway-generated ``{key, hash}`` pair; the key never leaves this process."""

        action = "generate API token key"
        return _as_object(await self._write(
            "POST",
            API_TOKEN_GENERATE_PATH,
            body=b"",
            content_type="application/json",
            action=action,
        ), action)

    async def create_api_token(
        self, name: str, config: dict[str, Any], *, description: str
    ) -> dict[str, Any]:
        """Create the Runtime API token resource (a create can never overwrite one)."""

        item = self._api_token_change(name, config, description)
        action = "create API token"
        return _as_object(await self._write(
            "POST",
            RESOURCE_COLLECTION_PATH.format(resource_type=API_TOKEN_TYPE),
            body=json.dumps([item], separators=(",", ":")).encode("utf-8"),
            content_type="application/json",
            action=action,
        ), action)

    def _api_token_change(
        self, name: str, config: dict[str, Any], description: str
    ) -> dict[str, Any]:
        """The one API-token create item this CLI is willing to send.

        Every field is checked rather than passed through: one ``basic-token`` profile,
        an explicit non-empty granted-levels tree, and the credential hash the Gateway
        itself produced.
        """

        target = guard_name(name, "API token name")
        if not isinstance(config, dict):
            raise WriteError("the desired API token config must be an object")
        profile = config.get("profile")
        if not isinstance(profile, dict) or profile.get("type") != "basic-token":
            raise WriteError(f"API token {target!r} must carry a basic-token profile")
        if not isinstance(profile.get("secureChannelRequired"), bool):
            raise WriteError(f"API token {target!r} must state secureChannelRequired as a boolean")
        grant = profile.get("securityLevels")
        if not isinstance(grant, list) or not grant:
            raise WriteError(f"API token {target!r} must be granted an explicit security level")
        for node in grant:
            if not isinstance(node, dict) or not isinstance(node.get("name"), str) or not node["name"]:
                raise WriteError(f"API token {target!r} carries an unusable security level grant")
        if not isinstance(profile.get("timestamp"), int):
            raise WriteError(f"API token {target!r} must carry the creation timestamp as a number")
        settings = config.get("settings")
        if not isinstance(settings, dict) or not isinstance(settings.get("tokenHash"), str):
            raise WriteError(f"API token {target!r} must carry the tokenHash the Gateway generated")
        if not settings["tokenHash"]:
            raise WriteError(f"API token {target!r} carries an empty tokenHash")
        return {
            "name": target,
            "collection": guard_name(config.get("collection", CONFIG_COLLECTION), "collection"),
            "enabled": True,
            "description": str(description),
            "config": {"profile": profile, "settings": settings},
        }

    # -------------------------------------------------------------- policy Tags

    async def ensure_policy_provider(self) -> bool:
        """Create the reserved policy Tag provider when it is absent; ``True`` when created."""

        existing = await self._reads.resource_document(documents.TAG_PROVIDER_TYPE, documents.PROVIDER)
        if existing is not None:
            return False
        await self._write(
            "POST",
            RESOURCE_COLLECTION_PATH.format(resource_type=documents.TAG_PROVIDER_TYPE),
            body=json.dumps([documents.provider_resource()], separators=(",", ":")).encode("utf-8"),
            content_type="application/json",
            action="create policy Tag provider",
        )
        return True

    async def await_policy_provider(self, *, deadline_seconds: float = PROVIDER_READY_SECONDS) -> dict[str, Any]:
        """Poll until the provider is readable *and* servable, under a deadline."""

        started = time.monotonic()
        attempts = 0
        find_status = ""
        export_status = ""
        while time.monotonic() - started < deadline_seconds:
            attempts += 1
            try:
                document = await self._reads.resource_document(documents.TAG_PROVIDER_TYPE, documents.PROVIDER)
                find_status = "present" if document is not None else "absent"
            except GatewayProbeError as error:
                find_status = str(error)
            try:
                await self._reads.export_tags(documents.PROVIDER)
                export_status = "readable"
            except GatewayProbeError as error:
                export_status = str(error)
            if find_status == "present" and export_status == "readable":
                return {"ready": True, "attempts": attempts, "find": find_status, "export": export_status}
            time.sleep(RETRY_INTERVAL_SECONDS)
        raise WriteError(
            f"the reserved policy provider did not become readable within {deadline_seconds:g}s "
            f"({attempts} attempt(s); resource={find_status}, export={export_status})"
        )

    async def import_policy(
        self, text: str, *, first_policy: str, deadline_seconds: float = POLICY_DEADLINE_SECONDS
    ) -> PolicyImport:
        """Import the policy Tag and its declared-length companion, bounded and idempotent.

        The first attempt carries the caller's collision policy (``Abort`` when the
        provider was just created, so a create can never clobber something else);
        every retry is an idempotent ``MergeOverwrite`` of the *same* document, so a
        partially applied import cannot wedge the write.
        """

        document = documents.tag_document(text)
        started = time.monotonic()
        outcome = PolicyImport()
        while True:
            policy = first_policy if not outcome.attempts else "MergeOverwrite"
            attempt: dict[str, Any] = {"collisionPolicy": policy}
            try:
                payload = await self._write(
                    "POST",
                    TAG_IMPORT_PATH,
                    body=document,
                    content_type="application/octet-stream",
                    params={
                        "provider": documents.PROVIDER,
                        "path": "",
                        "type": "json",
                        "collisionPolicy": policy,
                    },
                    action="import policy Tags",
                )
                failures = import_failures(payload)
                attempt["failures"] = failures
                if failures is None:
                    attempt["ok"] = True
                    outcome.attempts.append(attempt)
                    return outcome
            except WriteRefused as error:
                # An explicit refusal is final (D30 §2): report it, do not re-send it.
                attempt["error"] = str(error)
                attempt["ok"] = False
                outcome.attempts.append(attempt)
                raise WriteError(
                    f"importing the Runtime Target Policy was refused: {error} "
                    f"({outcome.attempt_count} attempt(s))"
                ) from error
            except WriteError as error:
                attempt["error"] = str(error)
                if not _retryable(error):
                    attempt["ok"] = False
                    attempt["elapsedMs"] = int((time.monotonic() - started) * 1000)
                    outcome.attempts.append(attempt)
                    raise WriteError(
                        f"importing the Runtime Target Policy failed with {error}: "
                        f"{outcome.attempt_count} attempt(s)"
                    ) from error
            attempt["ok"] = False
            attempt["elapsedMs"] = int((time.monotonic() - started) * 1000)
            outcome.attempts.append(attempt)
            if time.monotonic() - started >= deadline_seconds:
                raise WriteError(
                    f"importing the Runtime Target Policy failed after {outcome.attempt_count} "
                    f"attempt(s): {json.dumps(outcome.last)[:400]}"
                )
            time.sleep(RETRY_INTERVAL_SECONDS)

    async def export_policy(self) -> dict[str, Any]:
        return await self._reads.export_tags(documents.PROVIDER)

    # ------------------------------------------------------------------- modules

    async def upload_module(self, file_name: str, archive: bytes) -> dict[str, Any]:
        """Upload one ``.modl`` for installation; the bytes travel raw.

        D20 accepts a trusted local artifact only, and the caller has already proven
        its SHA-256. The Gateway stores the file under ``fileName`` in its module root,
        so the name is checked to be one path-free name and nothing else.
        """

        target = guard_module_token(file_name, "module file name")
        if len(archive) > MAX_MODULE_BYTES:
            raise WriteError(
                f"the module archive is {len(archive)} bytes; the bound is {MAX_MODULE_BYTES} bytes"
            )
        if not archive:
            raise WriteError("the module archive is empty")
        action = "upload module"
        return _as_object(await self._write(
            "POST",
            MODULE_UPLOAD_PATH,
            body=archive,
            content_type=MODULE_UPLOAD_CONTENT_TYPE,
            params={"fileName": target},
            action=action,
        ), action)

    async def accept_module_certificate(self, module_id: str) -> str:
        """Accept the module's signing certificate; 409 means it already was."""

        return await self._accept(MODULE_CERTIFICATE_ACCEPT_PATH, module_id, "certificate")

    async def accept_module_eula(self, module_id: str) -> str:
        """Accept the module's EULA; 409 means it already was."""

        return await self._accept(MODULE_EULA_ACCEPT_PATH, module_id, "EULA")

    async def _accept(self, path: str, module_id: str, what: str) -> str:
        target = guard_module_token(module_id, "module id")
        action = f"accept {what}"
        try:
            _as_object(await self._write(
                "POST", path, body=b"", content_type="application/json",
                params={"moduleId": target}, action=action,
            ), action)
        except WriteError as error:
            # The route documents 409 for an acceptance the Gateway already holds,
            # which is the state this command asked for.
            if error.status == ALREADY_ACCEPTED_STATUS:
                return ALREADY_ACCEPTED
            raise
        return ACCEPTED

    async def install_module(self, module_id: str) -> dict[str, Any]:
        """Complete the installation of a module this Gateway already holds uploaded."""

        target = guard_module_token(module_id, "module id")
        action = "install module"
        return _as_object(await self._write(
            "POST",
            MODULE_INSTALL_PATH,
            body=b"",
            content_type="application/json",
            params={"moduleId": target},
            action=action,
        ), action)

    async def restart_gateway(self) -> dict[str, Any]:
        """Restart the Gateway, confirming the call the documented way (D20).

        The restart is disruptive by design, and a Gateway can drop the connection on
        the way down: a transport failure carries ``status == 0``, which the caller
        reports alongside its own wait rather than retrying here.
        """

        action = "restart gateway"
        return _as_object(await self._write(
            "POST",
            GATEWAY_RESTART_PATH,
            body=b"",
            content_type="application/json",
            params={"confirm": "true"},
            action=action,
        ), action)

    # --------------------------------------------------------------- transport

    async def _archive(self, path: str, what: str) -> bytes:
        try:
            async with self._client.stream("GET", path) as response:
                declared = response.headers.get("content-length")
                if declared is not None and declared.isdigit() and int(declared) > MAX_ARCHIVE_BYTES:
                    raise WriteError(f"{what} announces {declared} bytes; the bound is {MAX_ARCHIVE_BYTES}")
                if not response.is_success:
                    await response.aread()
                    raise WriteError(f"{what} returned HTTP {response.status_code}")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(body) + len(chunk) > MAX_ARCHIVE_BYTES:
                        raise WriteError(f"{what} exceeded {MAX_ARCHIVE_BYTES} bytes")
                    body.extend(chunk)
                return bytes(body)
        except httpx.HTTPError as error:
            raise WriteError(f"{what} failed: {_redact(_reason(error), self._token)}") from error

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
        """The single write chokepoint: bounded, redacted, refusal-aware."""

        try:
            async with self._client.stream(
                method, path, params=params, content=body, headers={"Content-Type": content_type},
            ) as response:
                payload = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(payload) + len(chunk) > MAX_WRITE_RESPONSE_BYTES:
                        raise WriteError(f"{action} exceeded {MAX_WRITE_RESPONSE_BYTES} bytes of response")
                    payload.extend(chunk)
                status = response.status_code
        except httpx.HTTPError as error:
            raise WriteError(f"{action} failed: {_redact(_reason(error), self._token)}") from error
        if not 200 <= status < 300:
            snippet = _redact(_snippet(bytes(payload)), self._token)
            raise WriteError(
                f"{action} returned HTTP {status}" + (f": {snippet}" if snippet else ""),
                status,
            )
        document = _decode(payload, status)
        problem = refusal(document)
        if problem:
            raise WriteRefused(f"{action} was refused by the Gateway: {_redact(problem, self._token)}")
        return document


def _as_object(payload: Any, action: str) -> dict[str, Any]:
    """A write's JSON object body; an empty body is an acknowledgement with no claim."""

    if payload is None:
        return {}
    if isinstance(payload, dict):
        return payload
    raise WriteError(f"{action} answered with a body that is not a JSON object")


def _decode(payload: bytes | bytearray, status: int) -> Any:
    if not payload.strip():
        return None
    try:
        return json.loads(bytes(payload))
    except ValueError as error:
        raise WriteError(
            f"the Gateway answered HTTP {status} with a body that is not JSON: {_reason(error)}",
            status,
        ) from error
