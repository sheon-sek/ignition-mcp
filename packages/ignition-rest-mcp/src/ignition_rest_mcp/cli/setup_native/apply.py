"""``setup-native apply``: write the planned desired state, then verify it (D20).

``apply`` is plan-driven: it observes the Gateway exactly as ``plan`` does, refuses
to write anything while a single line is ``BLOCKED``, and then executes the
remaining intentions in the order ``plan`` printed them:

1. the bundle Project, for ``CREATE`` or for ``UPDATE`` of a **managed** project
   (takeover of an unmanaged project is ``BLOCKED``, never a write);
2. the MCP Server Config for the selected profile, with the profile's explicit
   Tool list — never ``*`` (D09/D20);
3. the Runtime Target Policy in the reserved provider ``IgnitionMCPPolicy``, with
   its declared-length companion and the enforced 32 KiB cap (D30 §1).

Every write goes through :class:`~ignition_rest_mcp.cli.setup_native.writer.GatewayWriter`
and is confirmed by a read-back before the next one starts. There is no rollback
(D20 states setup is not atomic); a failed write stops the sequence and is
reported with everything written before it. The run always ends with the ``verify``
sequence (D20), whose report is embedded in this command's own report.

The Module resolves a Server Config's Tool list against its provider registry at the
moment the resource is written, and it registers a Project's provider on the Project
collection's own notification thread — which can land after that write. Such a server
serves ``initialize`` but no primitives at all, and only a *resource update* makes the
Module build it again (ticket #21, live run 35714215320). So a run that wrote a Server
Config and can see no Tool at its endpoint re-announces the same document, bounded,
until the endpoint serves the profile's inventory.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import httpx

from ignition_rest_mcp.cli.setup_native import documents as docs
from ignition_rest_mcp.cli.setup_native import gateway as gw
from ignition_rest_mcp.cli.setup_native import plan, security, verify
from ignition_rest_mcp.cli.setup_native.action import (
    BLOCKED,
    CREATE,
    UPDATE,
    Action,
    needs_acknowledgement,
    upgrade_class,
)
from ignition_rest_mcp.cli.setup_native.doctor import (
    GatewayObservation,
    emit,
    make_gateway,
    make_mcp,
    probe_gateway,
)
from ignition_rest_mcp.cli.setup_native.inputs import API_TOKEN_TYPE, SECURITY_LEVELS_TYPE, Inputs
from ignition_rest_mcp.cli.setup_native.mcp_http import McpProbeError
from ignition_rest_mcp.cli.setup_native.writer import GatewayWriter, WriteError

#: How many times a run may re-announce the Server Config it wrote while the endpoint it
#: built serves no Tool at all, and how long to give the Module's own thread to register
#: the Project's provider before each re-announcement. Bounded (D10): at most that many
#: idempotent updates of the document the plan already approved, and no other write.
SERVER_CONFIG_REFRESH_ATTEMPTS = 3
SERVER_CONFIG_REFRESH_WAIT_SECONDS = 2.0


@dataclass(frozen=True, slots=True)
class Write:
    """One write attempt, or one line apply deliberately did not write."""

    kind: str
    name: str
    action: str
    detail: str
    ok: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "action": self.action,
            "detail": self.detail,
            "ok": self.ok,
        }

    def as_line(self) -> str:
        marker = "WRITE" if self.ok else "FAILED"
        return f"{marker} {self.kind} {self.name} [{self.action}]: {self.detail}"


def apply_report(
    inputs: Inputs,
    actions: Sequence[Action],
    writes: Sequence[Write],
    verify_report: dict[str, Any] | None,
    *,
    exit_code: int,
    error: str | None,
    refreshes: Sequence[dict[str, Any]] = (),
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "command": "apply",
        "manifest": str(inputs.manifest_path),
        "bundleVersion": inputs.bundle_version,
        "profile": inputs.profile,
        "bundleProject": inputs.bundle_project,
        "gatewayUrl": inputs.gateway_url.url,
        "serverConfig": inputs.server_config_name,
        "plan": [action.as_dict() for action in actions],
        "writes": [write.as_dict() for write in writes],
        "applied": any(write.ok and write.action in (CREATE, UPDATE) for write in writes),
        "verify": verify_report,
        "exitCode": exit_code,
    }
    if refreshes:
        # Every re-announcement of the Server Config document the plan already approved,
        # so a run that had to wait for the Module to serve its Tools says so.
        report["refreshes"] = [dict(refresh) for refresh in refreshes]
    if error is not None:
        report["error"] = error
    if inputs.policy_file is not None:
        report["policyFile"] = str(inputs.policy_file)
    if inputs.provisions_security:
        report["securityLevel"] = inputs.security_level_path
    if inputs.create_runtime_token:
        # The credential's *location* and mode, never the credential (D20).
        report["runtimeToken"] = {
            "name": inputs.runtime_token,
            "secretFile": str(inputs.runtime_token_file),
            "secretFileMode": "0600",
            "secureChannelRequired": not inputs.runtime_token_insecure_channel,
        }
    return report


def _emit(
    inputs: Inputs,
    actions: Sequence[Action],
    writes: Sequence[Write],
    verify_lines: Sequence[str],
    *,
    exit_code: int,
    error: str | None,
    verify_report: dict[str, Any] | None,
    refreshes: Sequence[dict[str, Any]] = (),
) -> int:
    """Report one apply run, ending with the same promise ``plan`` makes."""

    report = apply_report(
        inputs, actions, writes, verify_report, exit_code=exit_code, error=error, refreshes=refreshes,
    )
    if inputs.as_json:
        emit(inputs, report, [])
    else:
        for action in actions:
            print(action.as_line())
        for write in writes:
            print(write.as_line())
        for refresh in refreshes:
            print(f"REFRESH server-config {refresh.get('name')}: {refresh.get('detail')}")
        if error is not None:
            print(f"apply could not proceed: {error}")
        if verify_lines:
            print("")
            for line in verify_lines:
                print(line)
        counted = "" if not refreshes else f" refreshes={len(refreshes)}"
        print(
            f"apply: wrote={sum(1 for w in writes if w.ok and w.action in (CREATE, UPDATE))} "
            f"skipped={sum(1 for w in writes if w.action not in (CREATE, UPDATE))} "
            f"failed={sum(1 for w in writes if not w.ok)}{counted} => exit {exit_code}"
        )
    if not any(write.action in (CREATE, UPDATE) for write in writes):
        print(plan.PLAN_SENTINEL)
    return exit_code


def _action_of(actions: Sequence[Action], kind: str) -> Action:
    for action in actions:
        if action.kind == kind:
            return action
    raise WriteError(f"the plan carries no {kind} line")  # pragma: no cover - build_actions always emits one


def _unacknowledged(inputs: Inputs, observation: GatewayObservation, actions: Sequence[Action]) -> str:
    """D20: a MAJOR or downgrade bundle change needs the operator's explicit go-ahead."""

    action = _action_of(actions, "bundle-project")
    if action.action != UPDATE:
        return ""
    installed = observation.project.bundle_version if observation.project else None
    change = upgrade_class(installed, inputs.bundle_version)
    if not needs_acknowledgement(change) or inputs.acknowledge_upgrade:
        return ""
    return (
        f"the bundle-project change is a {change} ({installed} -> {inputs.bundle_version}) and "
        "--acknowledge-upgrade was not passed; nothing was written"
    )


def _bundle_archive(inputs: Inputs) -> bytes:
    """The operator's bundle ZIP, already SHA-256-verified against the manifest."""

    if inputs.bundle_zip is None:  # pragma: no cover - load_inputs requires it for apply
        raise WriteError("apply needs --bundle-zip")
    try:
        archive = inputs.bundle_zip.read_bytes()
    except OSError as error:
        raise WriteError(f"--bundle-zip: cannot read {inputs.bundle_zip}: {type(error).__name__}") from error
    declared = inputs.manifest["artifact"]["sizeBytes"]
    if len(archive) != declared:
        raise WriteError(
            f"--bundle-zip: {inputs.bundle_zip} is {len(archive)} bytes; the manifest declares {declared}"
        )
    return archive


async def _write_security_level(
    inputs: Inputs, observed: security.Observation, actions: Sequence[Action], writer: GatewayWriter
) -> Write:
    """Create the dedicated Runtime Security Level, preserving the tree (D20)."""

    action = _action_of(actions, "security-level")
    if action.action not in (CREATE, UPDATE):
        return Write(action.kind, action.name, action.action, "nothing to write")
    before = observed.levels or []
    tree, error = security.with_managed_level(before, inputs)
    if tree is None:  # pragma: no cover - the plan line is BLOCKED before any write
        raise WriteError(f"the dedicated Runtime level cannot be placed in the served tree: {error}")
    await writer.update_security_levels(tree, signature=observed.signature, collection=observed.collection)
    problem = security.verify_readback(
        await writer.reads.singleton_document(SECURITY_LEVELS_TYPE), before, inputs,
    )
    if problem:
        raise WriteError(f"the security-levels write was accepted but {problem}")
    return Write(
        action.kind, action.name, action.action,
        f"added {inputs.security_level_path}, preserving the other {len(before)} top-level level(s); "
        "read back and structurally verified",
    )


async def _write_runtime_token(
    inputs: Inputs,
    observed: security.Observation,
    creating_level: bool,
    actions: Sequence[Action],
    writer: GatewayWriter,
) -> Write:
    """Create the Runtime API token and store its secret in the operator's 0600 file."""

    action = _action_of(actions, "runtime-token")
    if action.action not in (CREATE, UPDATE):
        return Write(action.kind, action.name, action.action, "nothing to write")
    token_file = inputs.runtime_token_file
    assert token_file is not None  # pragma: no cover - load_inputs requires it with the flag
    try:
        key, declared = security.credential(await writer.generate_api_token())
    except security.CredentialError as error:
        raise WriteError(str(error)) from error
    grant = security.token_grant(inputs, observed.levels or [], creating=creating_level)
    if grant is None:  # pragma: no cover - the plan line is BLOCKED before any write
        raise WriteError(
            f"{inputs.security_level_path} is not in the Gateway's security tree, so the Runtime API "
            "token has no level to be granted; nothing was written"
        )
    config = security.token_config(
        inputs, grant, token_hash=declared, timestamp_ms=int(time.time() * 1000),
    )
    await writer.create_api_token(
        inputs.runtime_token, config, description=security.token_description(inputs),
    )
    served = await writer.reads.resource_document(API_TOKEN_TYPE, inputs.runtime_token)
    if security.stored_token_hash(served) != declared:
        raise WriteError(
            f"the API token {inputs.runtime_token} was created but reads back a different token hash, "
            f"so the secret for {token_file} would not authenticate"
        )
    try:
        security.write_secret_file(token_file, security.token_secret(inputs.runtime_token, key))
    except security.FileError as error:
        raise WriteError(
            f"{error}; the token exists on the Gateway with a secret that was not stored — delete it "
            "(or pass another --runtime-token-file) before re-running"
        ) from error
    return Write(
        action.kind, action.name, action.action,
        f"created the API token granted {inputs.security_level_path}; its secret was written to "
        f"{token_file} with mode 0600 and is never reported",
    )


async def _write_project(inputs: Inputs, actions: Sequence[Action], writer: GatewayWriter) -> Write:
    action = _action_of(actions, "bundle-project")
    if action.action not in (CREATE, UPDATE):
        return Write(action.kind, action.name, action.action, "nothing to write")
    archive = _bundle_archive(inputs)
    backup = ""
    if action.action == UPDATE and inputs.backup_dir is not None:
        backup = await _backup_project(inputs, writer)
    await writer.import_project(action.name, archive, overwrite=action.action == UPDATE)
    state = await writer.reads.find_project(inputs.bundle_project)
    if state.classification != gw.MANAGED:
        raise WriteError(
            f"the import was accepted but {action.name} reads back as {state.classification}; "
            "the deployed project carries no valid ownership marker"
        )
    if state.bundle_version != inputs.bundle_version:
        raise WriteError(
            f"the import was accepted but {action.name} reads back bundle {state.bundle_version}, "
            f"not {inputs.bundle_version}"
        )
    if state.inheritable is True:
        raise WriteError(f"the import was accepted but {action.name} reads back as inheritable")
    detail = f"{action.action} {len(archive)} bytes; read back managed bundle {state.bundle_version}{backup}"
    return Write(action.kind, action.name, action.action, detail)


async def _backup_project(inputs: Inputs, writer: GatewayWriter) -> str:
    """D20's snapshot before a replace: keep the deployed archive the operator asked for."""

    assert inputs.backup_dir is not None
    try:
        archive = await writer.project_export(inputs.bundle_project)
    except WriteError as error:
        raise WriteError(f"--backup-dir: the pre-replace export failed ({error})") from error
    target = Path(inputs.backup_dir).expanduser()
    name = inputs.bundle_project
    try:
        target.mkdir(parents=True, exist_ok=True)
        path = target / f"{name}.zip"
        path.write_bytes(archive)
    except OSError as error:
        raise WriteError(f"--backup-dir: cannot write {target}: {type(error).__name__}") from error
    return f"; backed up {len(archive)} bytes to {path}"


def _desired_server_config(
    inputs: Inputs, observation: GatewayObservation, documents: docs.Documents
) -> tuple[dict[str, Any], list[str]]:
    """The Server Config document this run owns, and the explicit Tool list it carries."""

    observed = observation.server_config or {}
    held = observed.get("config") if isinstance(observed.get("config"), dict) else None
    permissions = documents.permissions
    if permissions is None and isinstance(held, dict):
        candidate = held.get("permissions")
        permissions = candidate if isinstance(candidate, dict) else None
    if permissions is None:  # pragma: no cover - plan_actions BLOCKs this case first
        raise WriteError("no permissions tree is available for the Server Config")
    config = docs.desired_server_config(inputs, held, permissions)
    return config, list(config["tools"][f"project/{inputs.bundle_project}"])


async def _update_server_config(
    writer: GatewayWriter,
    inputs: Inputs,
    name: str,
    config: dict[str, Any],
    desired: Sequence[str],
    *,
    signature: str | None = None,
    enabled: bool | None = None,
) -> dict[str, Any]:
    """Write ``config`` over the Server Config's current signature and read it back.

    The signature read here is what makes the write a precondition check rather than a
    blind overwrite (D30): a document that changed under apply is refused, not clobbered.
    """

    current = await writer.reads.server_config_document(name)
    if current is None:
        raise WriteError(f"server config {name} reads back absent")
    held_enabled = current.get("enabled")
    if signature is None:
        signature = str(current.get("signature") or "")
    if enabled is None:
        enabled = True if not isinstance(held_enabled, bool) else held_enabled
    await writer.update_server_config(name, config, signature=signature, enabled=enabled)
    return await _require_server_config(writer, inputs, name, desired)


async def _write_server_config(
    inputs: Inputs,
    observation: GatewayObservation,
    documents: docs.Documents,
    actions: Sequence[Action],
    writer: GatewayWriter,
) -> Write:
    action = _action_of(actions, "server-config")
    if action.action not in (CREATE, UPDATE):
        return Write(action.kind, action.name, action.action, "nothing to write")
    config, desired = _desired_server_config(inputs, observation, documents)
    if action.action == CREATE:
        await writer.create_server_config(action.name, config, enabled=False)
        created = await _require_server_config(writer, inputs, action.name, desired)
        document = await _update_server_config(
            writer, inputs, action.name, config, desired,
            signature=str(created.get("signature") or ""), enabled=True,
        )
        state = "enabled" if document.get("enabled") is not False else "still disabled"
        detail = f"created {len(desired)} Tools for project/{inputs.bundle_project}, {state}"
    else:
        document = await _update_server_config(writer, inputs, action.name, config, desired)
        note = "" if document.get("enabled") is not False else "; left disabled as observed"
        detail = f"reconciled {len(desired)} Tools for project/{inputs.bundle_project}{note}"
    return Write(action.kind, action.name, action.action, detail)


def _server_config_written(writes: Sequence[Write]) -> bool:
    """Whether this run announced a Server Config the Module should be serving."""

    return any(
        write.kind == "server-config" and write.ok and write.action in (CREATE, UPDATE)
        for write in writes
    )


async def _serves_tool_inventory(
    inputs: Inputs, mcp_transport: httpx.AsyncBaseTransport | None
) -> bool:
    """Whether the endpoint advertises a Tool at all, i.e. the Module resolved the list.

    An endpoint the Module built before the Project's provider was registered answers
    ``initialize`` and then advertises no Tool capability, with every list answering
    ``-32600`` — the shape live run 35714215320 recorded for seven read-only attempts.
    A probe that cannot be made at all (an unreachable endpoint, a refused credential) is
    reported as ``True``: that is ``verify``'s business, never a rebuild trigger.
    """

    if not inputs.profile_inventory("tools") or inputs.runtime_endpoint() is None:
        return True
    try:
        client = make_mcp(inputs, mcp_transport)
        async with client:
            await client.initialize()
            if not client.advertises("tools"):
                return False
            return len(await client.tools_list()) > 0
    except (McpProbeError, httpx.HTTPError):
        return True


async def _refresh_server_config(
    inputs: Inputs,
    observation: GatewayObservation,
    documents: docs.Documents,
    actions: Sequence[Action],
    writer: GatewayWriter,
    mcp_transport: httpx.AsyncBaseTransport | None,
) -> list[dict[str, Any]]:
    """Re-announce the Server Config while the endpoint it built serves no Tool (bounded).

    The Module resolves a Server Config's Tool list from its provider registry when the
    resource is written, and it registers the Project's provider on the Project
    collection's notification thread (an ``ExecutionQueue`` turn that can land after the
    write). A provider that registers afterwards does not refresh a server that already
    exists, while a resource *update* makes the Module build the server again — so
    re-writing the document the plan already approved is what makes the endpoint serve
    the profile's Tools. Nothing but that update is written, and a plan after it is still
    a NO CHANGE run.
    """

    action = _action_of(actions, "server-config")
    config, desired = _desired_server_config(inputs, observation, documents)
    refreshes: list[dict[str, Any]] = []
    for attempt in range(1, SERVER_CONFIG_REFRESH_ATTEMPTS + 1):
        if await _serves_tool_inventory(inputs, mcp_transport):
            break
        # Give the Module's own thread its turn before announcing the document again.
        await asyncio.sleep(SERVER_CONFIG_REFRESH_WAIT_SECONDS)
        await _update_server_config(writer, inputs, action.name, config, desired)
        refreshes.append({
            "name": action.name,
            "attempt": attempt,
            "detail": (
                f"the endpoint served no Tools after the write; re-announced the same "
                f"document (attempt {attempt} of {SERVER_CONFIG_REFRESH_ATTEMPTS})"
            ),
        })
    return refreshes


async def _require_server_config(
    writer: GatewayWriter, inputs: Inputs, name: str, desired: Sequence[str]
) -> dict[str, Any]:
    document = await writer.reads.server_config_document(name)
    if document is None:
        raise WriteError(f"server config {name} reads back absent after the write")
    observed, note = docs.observed_tools(document, inputs.bundle_project)
    if observed is None or sorted(observed) != sorted(desired):
        raise WriteError(
            f"server config {name} reads back {note or 'a different Tool list'}; "
            f"the profile {inputs.profile} inventory was not written"
        )
    return document


async def _write_policy(
    inputs: Inputs,
    documents: docs.Documents,
    observed: docs.PolicyObservation | None,
    actions: Sequence[Action],
    writer: GatewayWriter,
) -> Write:
    action = _action_of(actions, "runtime-policy")
    if action.action not in (CREATE, UPDATE):
        return Write(action.kind, action.name, action.action, "nothing to write")
    if documents.policy_text is None:  # pragma: no cover - apply requires --policy-file
        raise WriteError("apply needs --policy-file")
    creating = action.action == CREATE or observed is None or not observed.provider_present
    created = False
    if creating:
        created = await writer.ensure_policy_provider()
        await writer.await_policy_provider()
    outcome = await writer.import_policy(
        documents.policy_text, first_policy="Abort" if created else "MergeOverwrite",
    )
    repaired, provenance = await _confirm_policy(writer, documents)
    repair = "; repaired by one idempotent re-import" if repaired else ""
    detail = (
        f"wrote {docs.byte_length(documents.policy_text)} bytes "
        f"(sha256 {docs.sha256_text(documents.policy_text)[:16]}...) into {docs.PROVIDER}, "
        f"{outcome.attempt_count} import attempt(s); declared length reads back {provenance}{repair}"
    )
    if created:
        detail = f"created provider {docs.PROVIDER}; " + detail
    return Write(action.kind, action.name, action.action, detail)


async def _confirm_policy(writer: GatewayWriter, documents: docs.Documents) -> tuple[bool, str]:
    """Read the served policy back; repair once with an idempotent re-import if it disagrees."""

    assert documents.policy_text is not None
    want = documents.policy_text
    mismatch = _policy_mismatch(await writer.export_policy(), want)
    if mismatch == "":
        return False, str(docs.byte_length(want))
    await writer.import_policy(want, first_policy="MergeOverwrite")
    document = await writer.export_policy()
    mismatch = _policy_mismatch(document, want)
    if mismatch != "":
        raise WriteError(f"the served Runtime Target Policy does not match the document apply wrote ({mismatch})")
    return True, str(docs.byte_length(want))


def _policy_mismatch(document: dict[str, Any], want: str) -> str:
    """How the served policy differs from the desired one; ``""`` when it matches."""

    tag = docs.find_tag(document, docs.TAG_NAME)
    value = tag.get("value") if isinstance(tag, dict) else None
    if not isinstance(value, str):
        return "the provider serves no RuntimeTargetPolicy Tag value"
    if value != want:
        return (
            f"served {docs.byte_length(value)} bytes (sha256 {docs.sha256_text(value)[:16]}...), "
            f"wanted {docs.byte_length(want)} bytes (sha256 {docs.sha256_text(want)[:16]}...)"
        )
    length = docs.find_tag(document, docs.LENGTH_TAG_NAME)
    declared = length.get("value") if isinstance(length, dict) else None
    expected = docs.byte_length(want)
    if declared != expected:
        return f"the declared length Tag reads back {declared!r}, not {expected}"
    return ""


async def _execute(
    inputs: Inputs,
    documents: docs.Documents,
    observation: GatewayObservation,
    policy: docs.PolicyObservation | None,
    observed: security.Observation,
    actions: Sequence[Action],
    writer: GatewayWriter,
) -> list[Write]:
    """Run the writes in plan order; a failure stops the sequence (no rollback)."""

    writes: list[Write] = []

    async def security_level() -> Write:
        return await _write_security_level(inputs, observed, actions, writer)

    async def token_step() -> Write:
        return await _write_runtime_token(inputs, observed, level_creating, actions, writer)

    async def project() -> Write:
        return await _write_project(inputs, actions, writer)

    async def server_config() -> Write:
        return await _write_server_config(inputs, observation, documents, actions, writer)

    async def policy_tags() -> Write:
        return await _write_policy(inputs, documents, policy, actions, writer)

    # D20's transaction order: the Security Level and the credential are provisioned
    # before the Project and the Server Config that name them. A run that did not opt
    # into them does not touch those planes at all, so its report keeps exactly the
    # three deployment lines it had before the flags existed.
    steps: list[tuple[str, Any]] = []
    level_creating = False
    if inputs.provision_security_levels:
        level_creating = _action_of(actions, "security-level").action == CREATE
        steps.append(("security-level", security_level))
    if inputs.create_runtime_token:
        steps.append(("runtime-token", token_step))
    steps.extend((
        ("bundle-project", project),
        ("server-config", server_config),
        ("runtime-policy", policy_tags),
    ))
    for kind, step in steps:
        try:
            writes.append(await step())
        except WriteError as error:
            action = _action_of(actions, kind)
            writes.append(Write(action.kind, action.name, action.action, str(error), ok=False))
            return writes
    return writes


async def run(
    inputs: Inputs,
    *,
    gateway_transport: httpx.AsyncBaseTransport | None = None,
    mcp_transport: httpx.AsyncBaseTransport | None = None,
) -> int:
    """Plan, refuse if blocked, write, then verify."""

    documents = docs.load(inputs)
    policy: docs.PolicyObservation | None = None
    observed = security.Observation()
    async with make_gateway(inputs, gateway_transport) as client:
        _, observation = await probe_gateway(client, inputs)
        if observation.reachable:
            policy = await docs.observe_policy(client)
        if observation.reachable and inputs.provisions_security:
            observed = await security.observe(client, inputs)
    if not observation.reachable:
        error = observation.error or "Gateway unreachable"
        return _emit(inputs, [], [], [], exit_code=1, error=error, verify_report=None)

    actions = plan.build_actions(inputs, observation, documents, policy, observed)
    blocked = [action for action in actions if action.action == BLOCKED]
    if blocked:
        error = (
            f"{len(blocked)} BLOCKED plan line(s); apply writes nothing until they are resolved"
        )
        return _emit(inputs, actions, [], [], exit_code=3, error=error, verify_report=None)
    unacknowledged = _unacknowledged(inputs, observation, actions)
    if unacknowledged:
        return _emit(inputs, actions, [], [], exit_code=3, error=unacknowledged, verify_report=None)

    # `make_gateway` above already applied the plain-HTTP/loopback rule to this
    # endpoint, so the writer cannot carry the token anywhere the read client would not.
    refreshes: list[dict[str, Any]] = []
    async with GatewayWriter(
        inputs.gateway_url, inputs.gateway_token, timeout_seconds=inputs.timeout_seconds,
        transport=gateway_transport,
    ) as writer:
        writes = await _execute(inputs, documents, observation, policy, observed, actions, writer)
        # A Server Config this run announced is only deployed when the endpoint it
        # creates really serves the profile's Tools; a Module that built the server
        # before the Project's provider registered needs the document again.
        if _server_config_written(writes):
            refreshes = await _refresh_server_config(
                inputs, observation, documents, actions, writer, mcp_transport,
            )

    verify_report, verify_lines, verify_code = await verify.collect(inputs, mcp_transport=mcp_transport)
    failed = any(not write.ok for write in writes) or verify_code != 0
    return _emit(
        inputs, actions, writes, verify_lines,
        exit_code=1 if failed else 0, error=None, verify_report=verify_report, refreshes=refreshes,
    )
