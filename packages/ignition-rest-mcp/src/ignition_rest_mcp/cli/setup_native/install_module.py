"""``setup-native install-module``: put a trusted local MCP Module on a Gateway (D20, D26 Phase 6).

The command drives the Gateway's own module REST flow, in this order, and every
step is refused before it happens rather than undone after it:

1. read the local ``.modl`` and compare its SHA-256 with the hash the operator
   named. A mismatch is a usage error, and nothing has been uploaded yet;
2. read ``modules/healthy``. The same build already installed is ``NO CHANGE``;
   a newer build on the Gateway is always refused; a higher build in the file
   needs ``--acknowledge-upgrade``;
3. upload the archive;
4. read the certificate and the EULA the module carries. Without
   ``--accept-certificate`` / ``--accept-eula`` the run prints the certificate and
   says where the EULA is read, then stops before installing (D20 forbids silent
   acceptance). With the flags it posts each acceptance, and a 409 means the
   Gateway already holds one;
5. install it;
6. without ``--restart`` report the pending restart and hand off to the operator.
   With it, confirm the restart, wait for the Gateway to answer again, and prove
   the module id and build are served.

The compatibility matrix is ``doctor``'s report, not this command's precondition,
and nothing here downloads anything: the only artifact is the file named on the
command line. The restart wait takes an injectable sleeper, so a test never sleeps.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence
from xml.etree import ElementTree

import httpx

from ignition_rest_mcp.cli.setup_native import gateway as gw
from ignition_rest_mcp.cli.setup_native.doctor import emit, make_writer
from ignition_rest_mcp.cli.setup_native.inputs import MAX_MODULE_BYTES, ModuleInputs, UsageError
from ignition_rest_mcp.cli.setup_native.writer import ALREADY_ACCEPTED, GatewayWriter, WriteError

#: What the run decided to do, and the word the report carries.
INSTALL = "INSTALL"
UPGRADE = "UPGRADE"
NO_CHANGE = "NO CHANGE"
REFUSED = "REFUSED"
FAILED = "FAILED"

#: The restart wait: how long to give the Gateway to come back, and how often to ask.
#: Bounded (D10), and every poll is one read of ``modules/healthy``.
RESTART_READY_SECONDS = 600.0
RESTART_POLL_SECONDS = 5.0

#: A ``.modl`` is a ZIP, and ``module.xml`` is its identity document.
MODULE_XML_NAME = "module.xml"
MODULE_XML_LIMIT_BYTES = 1_048_576

#: The certificate fields this command prints for the operator's decision.
CERTIFICATE_FIELDS = ("subjectName", "issuerName", "notValidBefore", "notValidAfter", "selfSigned")


@dataclass(frozen=True, slots=True)
class Artifact:
    """The local ``.modl``, read once and proven against the named hash."""

    module_id: str
    version: str
    build: str
    size_bytes: int
    sha256: str
    payload: bytes

    def describe(self) -> str:
        return f"id={self.module_id} version={self.version} build={self.build} size={self.size_bytes}"


@dataclass(frozen=True, slots=True)
class Step:
    """One step of the sequence, as the report and the text lines carry it."""

    name: str
    action: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"name": self.name, "action": self.action, "detail": self.detail}

    def as_line(self) -> str:
        return f"{self.action:<8}{self.name}: {self.detail}"


DONE = "DONE"
SKIPPED = "SKIPPED"
NEEDS = "NEEDS-ACK"


def read_artifact(inputs: ModuleInputs) -> Artifact:
    """Read, hash and open the operator's ``.modl``; every failure is a usage error.

    Nothing has been sent anywhere when this raises, which is what makes the hash
    check a precondition rather than a post-condition (D20).
    """

    module_file = inputs.module_file
    try:
        # Bound the buffer before reading it (D10); a file that changed underneath this
        # check is caught by the hash comparison below, which refuses the run anyway.
        if module_file.stat().st_size > MAX_MODULE_BYTES:
            raise UsageError(
                f"--file: {module_file} is over the {MAX_MODULE_BYTES} byte bound for a .modl"
            )
        payload = module_file.read_bytes()
    except OSError as error:
        raise UsageError(f"--file: cannot read {module_file}: {type(error).__name__}") from error
    digest = hashlib.sha256(payload).hexdigest()
    if digest != inputs.sha256:
        raise UsageError(
            f"--file: SHA-256 mismatch (file {digest} != --sha256 {inputs.sha256}); nothing was uploaded"
        )
    module_id, raw_version = _read_module_xml(payload, module_file)
    identity = gw.parse_module_identity(raw_version)
    if identity is None or identity.build is None or identity.version is None:
        raise UsageError(
            f"--file: {module_file} declares version {raw_version!r}, which carries no 10-digit build; "
            "the installed build cannot be compared without one"
        )
    return Artifact(
        module_id=module_id,
        version=identity.version,
        build=identity.build,
        size_bytes=len(payload),
        sha256=digest,
        payload=payload,
    )


def _read_module_xml(payload: bytes, source: Path) -> tuple[str, str]:
    """The module id and version from the archive's own ``module.xml``."""

    if not payload.startswith(b"PK"):
        raise UsageError(f"--file: {source} is not a ZIP archive, so it is not a .modl")
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            info = archive.getinfo(MODULE_XML_NAME)
            if info.file_size > MODULE_XML_LIMIT_BYTES:
                raise UsageError(
                    f"--file: {MODULE_XML_NAME} is {info.file_size} bytes; the bound is {MODULE_XML_LIMIT_BYTES}"
                )
            document = archive.read(MODULE_XML_NAME)
    except KeyError as error:
        raise UsageError(f"--file: {source} holds no {MODULE_XML_NAME}") from error
    except (zipfile.BadZipFile, OSError) as error:
        raise UsageError(f"--file: {source} could not be opened as a .modl ({type(error).__name__})") from error
    try:
        root = ElementTree.fromstring(document.decode("utf-8"))
    except (ElementTree.ParseError, UnicodeDecodeError) as error:
        raise UsageError(f"--file: {MODULE_XML_NAME} is not well-formed XML ({type(error).__name__})") from error
    node = root if root.tag == "module" else root.find("module")
    if node is None:
        raise UsageError(f"--file: {MODULE_XML_NAME} declares no <module>")
    module_id = _element_text(node, "id")
    version = _element_text(node, "version")
    if module_id is None:
        raise UsageError(f"--file: {MODULE_XML_NAME} carries no <id>")
    if version is None:
        raise UsageError(f"--file: {MODULE_XML_NAME} carries no <version>")
    return module_id, version


def _element_text(node: ElementTree.Element, tag: str) -> str | None:
    child = node.find(tag)
    if child is None or child.text is None or not child.text.strip():
        return None
    return child.text.strip()


def classify_build(installed: gw.ModuleIdentity | None, artifact: Artifact) -> str:
    """D20's Module rules: same build is nothing to do, a newer installed build never loses."""

    if installed is None:
        return INSTALL
    if installed.build is None:
        # No comparable build on the Gateway: a blind replace is not an option (D20).
        return REFUSED
    if installed.build == artifact.build:
        return NO_CHANGE
    return UPGRADE if installed.build < artifact.build else REFUSED


def install_report(
    inputs: ModuleInputs,
    artifact: Artifact,
    steps: Sequence[Step],
    *,
    outcome: str,
    exit_code: int,
    error: str | None,
    installed: gw.ModuleIdentity | None,
    certificate: dict[str, Any] | None = None,
    eula: dict[str, Any] | None = None,
    restart: dict[str, Any] | None = None,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "command": "install-module",
        "moduleFile": str(inputs.module_file),
        "moduleSha256": artifact.sha256,
        "requestedSha256": inputs.sha256,
        "gatewayUrl": inputs.gateway_url.url,
        "moduleId": artifact.module_id,
        "moduleVersion": artifact.version,
        "moduleBuild": artifact.build,
        "installedBefore": None if installed is None else _identity_detail(installed),
        "outcome": outcome,
        "restartRequested": inputs.restart,
        "steps": [step.as_dict() for step in steps],
        "exitCode": exit_code,
    }
    if certificate is not None:
        report["certificate"] = certificate
    if eula is not None:
        report["eula"] = eula
    if restart is not None:
        report["restart"] = restart
    if error is not None:
        report["error"] = error
    return report


def _identity_detail(identity: gw.ModuleIdentity) -> str:
    return f"version={identity.version or '?'} build={identity.build or '?'} reported={identity.raw_version}"


def _certificate_view(document: dict[str, Any]) -> dict[str, Any]:
    """Only the named certificate fields, bounded: the report never carries a blob."""

    return {key: document[key] for key in CERTIFICATE_FIELDS if key in document}


def _eula_view(inputs: ModuleInputs, module_id: str, size_bytes: int) -> dict[str, Any]:
    return {
        "sizeBytes": size_bytes,
        "readAt": f"{inputs.gateway_url.url}{gw.MODULE_EULA_PATH}?moduleId={module_id}",
    }


def _finish(
    inputs: ModuleInputs,
    artifact: Artifact,
    steps: Sequence[Step],
    *,
    outcome: str,
    exit_code: int,
    error: str | None,
    installed: gw.ModuleIdentity | None,
    certificate: dict[str, Any] | None = None,
    eula: dict[str, Any] | None = None,
    restart: dict[str, Any] | None = None,
) -> int:
    report = install_report(
        inputs, artifact, steps,
        outcome=outcome, exit_code=exit_code, error=error, installed=installed,
        certificate=certificate, eula=eula, restart=restart,
    )
    lines = [step.as_line() for step in steps]
    if error is not None:
        lines.append(f"install-module: {error}")
    lines.append(f"install-module: {outcome} {artifact.module_id} build={artifact.build} => exit {exit_code}")
    emit(inputs, report, lines)
    return exit_code


async def _observe_installed(
    inputs: ModuleInputs, writer: GatewayWriter, artifact: Artifact, steps: list[Step]
) -> tuple[gw.ModuleIdentity | None, int | None]:
    """The Gateway's identity for this module id, plus a terminal exit code when there is one.

    Every decision here happens before an upload, so a refusal leaves the Gateway
    exactly as it found it.
    """

    try:
        installed = await writer.reads.module_identity(artifact.module_id)
    except gw.GatewayProbeError as error:
        steps.append(Step("installed-build", FAILED, str(error)))
        return None, _refuse(
            inputs, artifact, steps,
            reason=f"the installed module build could not be read; refusing to upload over an unknown state: {error}",
            exit_code=1,
        )
    if installed is None:
        steps.append(Step("installed-build", DONE, "not installed"))
        return None, None
    verdict = classify_build(installed, artifact)
    detail = f"{_identity_detail(installed)} vs file build={artifact.build}"
    if verdict == NO_CHANGE:
        steps.append(Step("installed-build", DONE, f"already installed; {detail}"))
        return installed, _finish(
            inputs, artifact, steps, outcome=NO_CHANGE, exit_code=0, error=None, installed=installed,
        )
    if verdict == REFUSED:
        steps.append(Step("installed-build", REFUSED, detail))
        return installed, _finish(
            inputs, artifact, steps, outcome=REFUSED, exit_code=1,
            error=(
                f"the installed build ({_build_word(installed)}) is not older than the file's "
                f"build={artifact.build}; a lower build is never installed"
            ),
            installed=installed,
        )
    if verdict == UPGRADE and not inputs.acknowledge_upgrade:
        steps.append(Step("installed-build", NEEDS, f"upgrade needs acknowledgement; {detail}"))
        return installed, _finish(
            inputs, artifact, steps, outcome=REFUSED, exit_code=3,
            error=(
                f"the file's build={artifact.build} is higher than the installed build={installed.build}; "
                "pass --acknowledge-upgrade to install it"
            ),
            installed=installed,
        )
    steps.append(Step("installed-build", DONE, detail))
    return installed, None


def _build_word(identity: gw.ModuleIdentity) -> str:
    return identity.build if identity.build is not None else "unreadable"


def _refuse(
    inputs: ModuleInputs, artifact: Artifact, steps: Sequence[Step], *, reason: str, exit_code: int
) -> int:
    return _finish(
        inputs, artifact, steps, outcome=REFUSED, exit_code=exit_code,
        error=reason, installed=None,
    )


async def _acceptances(
    inputs: ModuleInputs, writer: GatewayWriter, artifact: Artifact, steps: list[Step]
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, int | None]:
    """Read what the module carries and accept it only under its own flag.

    D20 forbids a silent certificate or EULA acceptance: the run shows the operator
    what they are being asked to accept, then stops. Only the flags let it post the
    acceptance, and a 409 is the Gateway saying it already holds one.
    """

    try:
        certificate = await writer.reads.module_certificate(artifact.module_id)
        eula_size = await writer.reads.module_eula_size(artifact.module_id)
    except gw.GatewayProbeError as error:
        steps.append(Step("inspect-acceptances", FAILED, str(error)))
        return None, None, _refuse(
            inputs, artifact, steps,
            reason=f"the uploaded module's certificate or EULA could not be read: {error}", exit_code=1,
        )
    certificate_view = None if certificate is None else _certificate_view(certificate)
    eula_view = None if eula_size is None else _eula_view(inputs, artifact.module_id, eula_size)

    missing: list[str] = []
    if certificate_view is not None and not inputs.accept_certificate:
        missing.append("--accept-certificate")
    if eula_view is not None and not inputs.accept_eula:
        missing.append("--accept-eula")
    if missing:
        steps.append(
            Step("certificate", NEEDS if certificate_view is not None else SKIPPED,
                 _certificate_line(certificate_view) if certificate_view is not None
                 else "the module carries no certificate")
        )
        steps.append(
            Step("eula", NEEDS if eula_view is not None else SKIPPED,
                 f"read it at {eula_view['readAt']}" if eula_view is not None
                 else "the module carries no EULA")
        )
        return certificate_view, eula_view, _finish(
            inputs, artifact, steps, outcome=REFUSED, exit_code=3,
            error=f"{', '.join(missing)} not passed; the module was uploaded but nothing was installed",
            installed=None, certificate=certificate_view, eula=eula_view,
        )

    try:
        if certificate_view is not None:
            outcome = await writer.accept_module_certificate(artifact.module_id)
            steps.append(Step("certificate", DONE, _acceptance_detail(outcome)))
        else:
            steps.append(Step("certificate", SKIPPED, "the module carries no certificate"))
        if eula_view is not None:
            outcome = await writer.accept_module_eula(artifact.module_id)
            steps.append(Step("eula", DONE, _acceptance_detail(outcome)))
        else:
            steps.append(Step("eula", SKIPPED, "the module carries no EULA"))
    except WriteError as error:
        steps.append(Step("accept", FAILED, str(error)))
        return certificate_view, eula_view, _refuse(
            inputs, artifact, steps,
            reason=f"the acceptance the operator authorised was refused: {error}", exit_code=1,
        )
    return certificate_view, eula_view, None


def _certificate_line(certificate: dict[str, Any]) -> str:
    subject = certificate.get("subjectName", "?")
    issuer = certificate.get("issuerName", "?")
    valid_from = certificate.get("notValidBefore", "?")
    valid_to = certificate.get("notValidAfter", "?")
    return f"subject={subject} issuer={issuer} valid={valid_from}..{valid_to}"


def _acceptance_detail(marker: str) -> str:
    """The acceptance outcome as a report word, not a constant name."""

    return "already accepted by the Gateway" if marker == ALREADY_ACCEPTED else "accepted"


async def _await_module_back(
    writer: GatewayWriter,
    artifact: Artifact,
    *,
    sleeper: Callable[[float], Awaitable[None]],
) -> tuple[Step, bool]:
    """Poll ``modules/healthy`` until the installed build is served, under a bound."""

    polls = int(RESTART_READY_SECONDS // RESTART_POLL_SECONDS)
    last = "the Gateway has not answered yet"
    for attempt in range(1, polls + 1):
        try:
            identity = await writer.reads.module_identity(artifact.module_id)
        except gw.GatewayProbeError as error:
            identity = None
            last = str(error)
        if identity is not None:
            if identity.build == artifact.build:
                return Step("wait-ready", DONE, f"poll {attempt}: {_identity_detail(identity)}"), True
            last = f"the Gateway serves {_identity_detail(identity)}, not build={artifact.build}"
        if attempt < polls:
            await sleeper(RESTART_POLL_SECONDS)
    return Step("wait-ready", FAILED, f"{polls} poll(s); {last}"), False


async def _restart(
    inputs: ModuleInputs,
    writer: GatewayWriter,
    artifact: Artifact,
    steps: list[Step],
    *,
    sleeper: Callable[[float], Awaitable[None]],
) -> int:
    """Confirm the restart, wait for the Gateway, and prove the module came back."""

    try:
        await writer.restart_gateway()
    except WriteError as error:
        if error.status != 0:
            steps.append(Step("restart", FAILED, str(error)))
            return _finish(
                inputs, artifact, steps, outcome=FAILED, exit_code=1,
                error=f"the restart request was refused: {error}", installed=None,
            )
        # A Gateway restarting can drop the response; the readiness wait decides.
        steps.append(Step("restart", DONE, f"no clean answer ({error}); waiting for the Gateway"))
    else:
        steps.append(Step("restart", DONE, "confirmed with confirm=true"))
    step, came_back = await _await_module_back(writer, artifact, sleeper=sleeper)
    steps.append(step)
    if not came_back:
        return _finish(
            inputs, artifact, steps, outcome=FAILED, exit_code=1,
            error=(
                f"the module did not come back as build={artifact.build} within "
                f"{RESTART_READY_SECONDS:g}s; the install is still waiting on a restart"
            ),
            installed=None, restart={"requested": True, "ready": False},
        )
    return _finish(
        inputs, artifact, steps, outcome=INSTALL, exit_code=0, error=None, installed=None,
        restart={"requested": True, "ready": True, "build": artifact.build, "readback": step.detail},
    )


async def run(
    inputs: ModuleInputs,
    *,
    gateway_transport: httpx.AsyncBaseTransport | None = None,
    sleeper: Callable[[float], Awaitable[None]] | None = None,
) -> int:
    """Install the ``.modl`` the operator named, or refuse before writing anything."""

    artifact = read_artifact(inputs)
    wait = asyncio.sleep if sleeper is None else sleeper
    steps: list[Step] = [Step("local-artifact", DONE, artifact.describe())]
    async with make_writer(inputs, gateway_transport) as writer:
        installed, decided = await _observe_installed(inputs, writer, artifact, steps)
        if decided is not None:
            return decided

        try:
            upload = await writer.upload_module(inputs.upload_name, artifact.payload)
        except WriteError as error:
            steps.append(Step("upload", FAILED, str(error)))
            return _finish(
                inputs, artifact, steps, outcome=FAILED, exit_code=1,
                error=f"the upload failed: {error}", installed=installed,
            )
        served_id = upload.get("moduleId")
        if isinstance(served_id, str) and served_id and served_id != artifact.module_id:
            steps.append(Step("upload", REFUSED, f"the Gateway stored it as {served_id!r}"))
            return _finish(
                inputs, artifact, steps, outcome=REFUSED, exit_code=1,
                error=(
                    f"the Gateway answered the upload with moduleId {served_id!r}, not the "
                    f"{artifact.module_id!r} the file declares; nothing was installed"
                ),
                installed=installed,
            )
        steps.append(Step("upload", DONE, f"{inputs.upload_name} ({artifact.size_bytes} bytes)"))

        certificate, eula, refused = await _acceptances(inputs, writer, artifact, steps)
        if refused is not None:
            return refused

        try:
            await writer.install_module(artifact.module_id)
        except WriteError as error:
            steps.append(Step("install", FAILED, str(error)))
            return _finish(
                inputs, artifact, steps, outcome=FAILED, exit_code=1,
                error=f"the install was refused: {error}", installed=installed,
                certificate=certificate, eula=eula,
            )
        steps.append(Step("install", DONE, f"{artifact.module_id} installed"))

        if not inputs.restart:
            # The install is done; the Module only wakes on a restart, and this run was
            # not told to take the Gateway down. Report what is left instead.
            steps.append(
                Step(
                    "restart", NEEDS,
                    "pending: restart the Gateway, then run setup-native verify to confirm the deployment",
                )
            )
            return _finish(
                inputs, artifact, steps,
                outcome=UPGRADE if installed is not None else INSTALL, exit_code=0, error=None,
                installed=installed, certificate=certificate, eula=eula,
                restart={"requested": False, "pending": True, "ready": False},
            )
        return await _restart(inputs, writer, artifact, steps, sleeper=wait)


