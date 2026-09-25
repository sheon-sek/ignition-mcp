"""Reading a trusted local ``.modl`` and classifying it against the installed build (D20).

Two decisions live here, and both are made before anything reaches the Gateway:

1. :func:`read_artifact` reads the operator's file once, hashes it while it reads
   and refuses a mismatch, a foreign module id, a version without a build, or a file
   past the D10 bound. Nothing has been uploaded when it raises;
2. :func:`classify_build` compares the file's build with the build the Gateway
   serves. The same build is ``NO CHANGE``, a newer file build is an ``UPGRADE`` and
   anything else is ``REFUSED``, because a lower build never replaces a higher one.

:func:`state_problem` adds the other half of "installed": only a Module the listing
reports as ``ACTIVE`` hosts its routes, so ``setup`` fails on anything else, an entry
that reports no state included, instead of carrying its own 404 further along
(issue #81).

The Gateway's own module REST flow, the certificate and EULA acceptance and the
restart live in :mod:`ignition_rest_mcp.cli.gateway_ops.writer`. Nothing here
downloads anything: the only artifact is the file the operator named.
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

from ignition_rest_mcp.cli.gateway_ops import gateway as gw
from ignition_rest_mcp.cli.gateway_ops.inputs import MAX_MODULE_BYTES, ModuleInputs, UsageError

#: What the comparison decided to do, and the word the caller's report carries.
INSTALL = "INSTALL"
UPGRADE = "UPGRADE"
NO_CHANGE = "NO CHANGE"
REFUSED = "REFUSED"

#: A ``.modl`` is a ZIP, and ``module.xml`` is its identity document. Reading and hashing
#: the archive share one pass, so this is the block the file bound is checked on.
MODULE_READ_BLOCK_BYTES = 1024 * 1024
MODULE_XML_NAME = "module.xml"
MODULE_XML_LIMIT_BYTES = 1_048_576

#: The ``modules/healthy`` state of a Module the Gateway has loaded and is serving.
#: Anything else means the routes the Module hosts do not exist yet, so ``setup``
#: treats it as not installed (issue #81).
MODULE_ACTIVE = "ACTIVE"


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


def read_artifact(inputs: ModuleInputs) -> Artifact:
    """Read, hash and open the operator's ``.modl``; every failure is a usage error.

    Nothing has been sent anywhere when this raises, which is what makes the hash
    check a precondition rather than a post-condition (D20).
    """

    payload, digest = _read_bounded(inputs.module_file)
    if digest != inputs.sha256:
        raise UsageError(
            f"--file: SHA-256 mismatch (file {digest} != --sha256 {inputs.sha256}); nothing was uploaded"
        )
    module_id, raw_version = _read_module_xml(payload, inputs.module_file)
    if module_id != gw.MCP_MODULE_ID:
        raise UsageError(
            f"--file: {inputs.module_file} is module {module_id!r}, and this command installs "
            f"the {gw.MCP_MODULE_ID!r} module and nothing else"
        )
    identity = gw.parse_module_identity(raw_version)
    if identity is None or identity.build is None or identity.version is None:
        raise UsageError(
            f"--file: {inputs.module_file} declares version {raw_version!r}, which carries no 10-digit build; "
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


def _read_bounded(module_file: Path) -> tuple[bytes, str]:
    """One open, one pass, and no more than the bound: D10 says a big file is refused, not buffered.

    Reading and hashing share the loop so a file that grows while it is read cannot push
    more than ``MAX_MODULE_BYTES`` into memory before the refusal.
    """

    digest = hashlib.sha256()
    buffer = bytearray()
    try:
        with module_file.open("rb") as stream:
            while True:
                block = stream.read(MODULE_READ_BLOCK_BYTES)
                if not block:
                    return bytes(buffer), digest.hexdigest()
                if len(buffer) + len(block) > MAX_MODULE_BYTES:
                    raise UsageError(
                        f"--file: {module_file} passes the {MAX_MODULE_BYTES} byte bound for a .modl "
                        "while it was being read; it is not installed"
                    )
                digest.update(block)
                buffer.extend(block)
    except OSError as error:
        raise UsageError(f"--file: cannot read {module_file}: {type(error).__name__}") from error


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


def state_problem(identity: gw.ModuleIdentity) -> str:
    """Why the listing does not show an ACTIVE Module, as the entry words it.

    The caller fails on any non-empty answer, so the state has to be evidenced, not
    merely uncontradicted: a Module the Gateway installed but did not start, one it
    faulted, and one whose entry says nothing about its state are all not ACTIVE.
    ``modules/healthy`` documents ``state`` as available on fully loaded modules only,
    which is why an entry without one reads as the Gateway reporting no state rather
    than as a pass.
    """

    state = identity.state
    if state is not None and state.upper() == MODULE_ACTIVE:
        return ""
    detail = ["the Gateway reports no state" if state is None else f"the Gateway reports state {state}"]
    if identity.on_startup is not None:
        detail.append(f"onStartup {identity.on_startup}")
    if identity.fault_cause is not None:
        detail.append(f"fault cause {identity.fault_cause}")
    return ", ".join(detail)
