"""D15 typed Perspective adapter over a validated Project export archive.

Decision links (``docs/decisions/``):

- D15: Perspective resources are project resources addressed by a *Logical
  resource path* such as ``Pages/Overview``, never by an archive path. The
  server owns the mapping, so a caller can neither traverse the archive nor
  reach a resource it did not name.
- D10: a Logical resource path and a View document are bounded input. An
  over-budget value fails with ``limit_exceeded`` and is never truncated.

This module is the only place that maps a Logical resource path to archive
entries. The D15 mutation path (whole-document replace, single-View delete)
reuses :func:`view_directory`, :func:`view_document_entry`,
:func:`view_resource_entry` and :func:`content_entries` instead of repeating the
mapping.

The write half is :class:`ResourcePatch` plus :func:`apply_patch`: one typed
change to exactly one Perspective resource, written over a copy of the baseline
archive with every other entry copied byte-identically (D15: never rebuild a
Project from the resource types this server understands). A delete removes the
View's own entries, and the folder's marker entries only when the View was all
that folder held, so a folder that still holds other Views keeps them.

Every caller-facing message names the Logical resource, never the archive entry:
the layout stays server-side, and a message that echoed an entry would hand the
caller the path the public API refuses to accept. Every read that needs to say
which document a refusal is about passes a ``label`` into :func:`read_document`.

Archive layout, as Ignition 8.3 exports the Perspective module:

.. code-block:: text

    com.inductiveautomation.perspective/views/<Logical path>/view.json
    com.inductiveautomation.perspective/views/<Logical path>/resource.json
    com.inductiveautomation.perspective/page-config/config.json
    com.inductiveautomation.perspective/session-props/props.json

A directory under ``views/`` is a View when it holds a ``view.json`` entry, so a
View folder, which holds other Views instead, is not reported as one. The
archive must already have passed
:mod:`ignition_rest_mcp.projects.zip_safety`: entry names are read as validated,
and the declared size of an entry is trusted only because
:func:`ignition_rest_mcp.projects.fingerprint.project_fingerprint` has already
streamed it against that declaration.

The functions here read files, so callers run them off the event loop
(``asyncio.to_thread``). ``perspective_view_validate`` is the offline half: it
never touches an archive or a Gateway.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
import json
import tempfile
from typing import Any, cast, Protocol
import zipfile

from ignition_rest_mcp.artifacts.model import ArtifactReader, ArtifactWriter
from ignition_rest_mcp.errors import GatewayError
from ignition_rest_mcp.projects.transactions import CandidateBuilder
from ignition_rest_mcp.projects.zip_safety import is_directory_entry

#: The Perspective module directory inside a Project export.
PERSPECTIVE_MODULE = "com.inductiveautomation.perspective"

#: The Views resource directory: one directory per View, named by its Logical path.
VIEWS_DIRECTORY = f"{PERSPECTIVE_MODULE}/views"

#: The View document inside a View directory.
VIEW_DOCUMENT_NAME = "view.json"

#: The Designer resource metadata beside it.
VIEW_RESOURCE_NAME = "resource.json"

#: The entry that marks a View directory as a View *folder*, so deleting the View a
#: folder is named after can leave the folder and the Views inside it alone.
VIEW_FOLDER_NAME = "folder.json"

#: The Project's single Page configuration document.
PAGE_CONFIG_ENTRY = f"{PERSPECTIVE_MODULE}/page-config/config.json"

#: The Project's single Session properties document.
SESSION_PROPS_ENTRY = f"{PERSPECTIVE_MODULE}/session-props/props.json"

#: Longest Logical resource path accepted (D10: caller input is bounded).
MAX_LOGICAL_PATH_BYTES = 512

#: Characters Ignition refuses in a project resource name on every platform.
RESERVED_NAME_CHARACTERS = frozenset(':*?"<>|')


@dataclass(frozen=True, slots=True)
class ViewBudget:
    """Finite D10 ceilings on one Perspective JSON document.

    ``max_bytes`` is measured on the serialized document, so the same ceiling
    applies whether the caller sent JSON text or an already-parsed value, and a
    document read out of an archive is refused before it is buffered past it.
    ``max_depth`` counts the JSON values on the longest path from the document to
    a leaf: a scalar is 1, ``{"a": {}}`` is 2, ``{"a": {"b": []}}`` is 3.
    """

    max_bytes: int = 1_048_576
    max_depth: int = 64


#: The shared ceilings: :class:`ViewBudget` is frozen, so one instance can be
#: resolved by every call that omits ``budget``.
DEFAULT_VIEW_BUDGET = ViewBudget()


@dataclass(frozen=True, slots=True)
class ViewValidation:
    """What an accepted View document measured (the D15 offline validation result)."""

    document: dict[str, Any]
    bytes: int
    depth: int


def validate_logical_resource_path(value: str) -> str:
    """Return a Logical resource path unchanged, or refuse it (D15).

    Refused with ``invalid_argument``: a non-string or empty value, text that
    cannot be UTF-8 encoded (a lone surrogate), a leading ``/``, a backslash, any
    empty, ``.`` or ``..`` segment, a control character, and a character Ignition
    refuses in a resource name. Refused with ``limit_exceeded``: a path over
    :data:`MAX_LOGICAL_PATH_BYTES`.
    """

    if not isinstance(value, str) or not value:
        raise GatewayError("invalid_argument", "path must be a non-empty Logical resource path")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as error:
        # A lone surrogate cannot address a resource: it survives neither JSON
        # transport nor the archive's own name encoding.
        raise GatewayError(
            "invalid_argument",
            "path must be text that can be encoded as UTF-8; it contains an unpaired surrogate",
        ) from error
    if len(encoded) > MAX_LOGICAL_PATH_BYTES:
        raise GatewayError(
            "limit_exceeded",
            f"path is {len(encoded)} bytes, over the {MAX_LOGICAL_PATH_BYTES}-byte limit; "
            "name the View directly instead of qualifying it further",
        )
    if value.startswith("/"):
        raise GatewayError(
            "invalid_argument",
            f"path {value!r} starts with '/'; a Logical resource path is relative to the Views root",
        )
    if "\\" in value:
        raise GatewayError(
            "invalid_argument", f"path {value!r} contains a backslash; segments are separated by '/' only",
        )
    for segment in value.split("/"):
        if not segment:
            raise GatewayError("invalid_argument", f"path {value!r} has an empty segment")
        if segment in (".", ".."):
            raise GatewayError(
                "invalid_argument",
                f"path {value!r} has a {segment!r} segment, which would escape the Views root",
            )
        for character in segment:
            if ord(character) < 0x20 or ord(character) == 0x7F:
                raise GatewayError("invalid_argument", f"path {value!r} contains a control character")
            if character in RESERVED_NAME_CHARACTERS:
                raise GatewayError(
                    "invalid_argument",
                    f"path {value!r} contains {character!r}, which Ignition refuses in a resource name",
                )
    return value


def view_directory(logical_path: str) -> str:
    """The archive directory holding one View's entries."""

    return f"{VIEWS_DIRECTORY}/{validate_logical_resource_path(logical_path)}"


def view_document_entry(logical_path: str) -> str:
    """The archive entry holding the View document."""

    return f"{view_directory(logical_path)}/{VIEW_DOCUMENT_NAME}"


def view_resource_entry(logical_path: str) -> str:
    """The archive entry holding the View's Designer resource metadata."""

    return f"{view_directory(logical_path)}/{VIEW_RESOURCE_NAME}"


def logical_path_from_view_document(entry: str) -> str | None:
    """Invert :func:`view_document_entry`, or ``None`` for any other entry.

    An entry that is not a View document, or whose derived path is not a valid
    Logical resource path, is not a View: the caller sees the listing, not a
    refusal, because a Project archive holds entries this adapter does not own.
    """

    prefix = f"{VIEWS_DIRECTORY}/"
    suffix = f"/{VIEW_DOCUMENT_NAME}"
    if not isinstance(entry, str) or not entry.startswith(prefix) or not entry.endswith(suffix):
        return None
    candidate = entry[len(prefix) : -len(suffix)]
    try:
        return validate_logical_resource_path(candidate)
    except GatewayError:
        return None


def content_entries(archive_path: str) -> list[str]:
    """Every non-directory entry name, in central-directory order.

    Directory markers (names ending ``/``) are dropped: the D16 fingerprint
    ignores them and so does every read and write here.
    """

    try:
        with zipfile.ZipFile(archive_path) as archive:
            return [info.filename for info in archive.infolist() if not is_directory_entry(info.filename)]
    except (zipfile.BadZipFile, OSError, ValueError) as error:
        raise GatewayError(
            "invalid_argument", "the Project export is not a readable ZIP archive",
        ) from error


def list_view_paths(archive_path: str) -> list[str]:
    """Sorted Logical paths of every Local View in the archive (D15)."""

    paths: list[str] = []
    for entry in content_entries(archive_path):
        logical = logical_path_from_view_document(entry)
        if logical is not None:
            paths.append(logical)
    return sorted(paths)


def read_document(
    archive_path: str, entry: str, *, label: str, budget: ViewBudget | None = None,
) -> dict[str, Any] | None:
    """The JSON object at ``entry``, or ``None`` when the archive has no such entry.

    ``label`` is what a caller-facing message names: the Logical resource path
    (``Local View 'Pages/Overview'``) or the Project-level document's name. The
    archive entry itself never reaches a message, because D15 keeps the archive
    layout server-side and a message that echoed an entry would hand the caller
    the very path the public API refuses to accept.

    ``limit_exceeded`` when the entry declares more than ``budget.max_bytes``,
    ``schema_mismatch`` when its bytes are not a UTF-8 JSON object: the document
    came from the Gateway, so an unreadable one is a Gateway response problem,
    not a caller mistake.
    """

    limits = budget if budget is not None else DEFAULT_VIEW_BUDGET
    try:
        with zipfile.ZipFile(archive_path) as archive:
            info = archive.getinfo(entry)
            if info.file_size > limits.max_bytes:
                raise GatewayError(
                    "limit_exceeded",
                    f"{label} is {info.file_size} bytes, over the {limits.max_bytes}-byte "
                    "document limit; this server cannot return it whole",
                )
            payload = archive.read(info)
    except KeyError:
        return None
    except (zipfile.BadZipFile, OSError, ValueError) as error:
        raise GatewayError(
            "invalid_argument", "the Project export is not a readable ZIP archive",
        ) from error
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise GatewayError(
            "schema_mismatch", f"{label} in the Project export is not UTF-8 JSON this server can read",
        ) from error
    if not isinstance(document, dict):
        raise GatewayError("schema_mismatch", f"{label} in the Project export is not a JSON object")
    return document


def read_view_document(
    archive_path: str, logical_path: str, *, budget: ViewBudget | None = None,
) -> dict[str, Any]:
    """The View document at one Logical resource path, or ``not_found``."""

    path = validate_logical_resource_path(logical_path)
    document = read_document(
        archive_path, view_document_entry(path), label=f"Local View {path!r}", budget=budget,
    )
    if document is None:
        raise GatewayError("not_found", f"this Project has no Local View at {path!r}")
    return document


def read_page_config(archive_path: str) -> dict[str, Any] | None:
    """The Project's Page configuration document, or ``None`` when it has none locally."""

    return read_document(
        archive_path, PAGE_CONFIG_ENTRY, label="the Project's Page configuration document",
    )


def read_session_props(archive_path: str) -> dict[str, Any] | None:
    """The Project's Session properties document, or ``None`` when it has none locally."""

    return read_document(
        archive_path, SESSION_PROPS_ENTRY, label="the Project's Session properties document",
    )


def validate_view_document(document: Any, *, budget: ViewBudget | None = None) -> ViewValidation:
    """Validate one View document offline (D15), without a Gateway or an archive.

    ``document`` is an already-parsed JSON value, which is what a Tool receives:
    the MCP layer parses the caller's request, so this function measures and
    checks instead of parsing text.

    Refused with ``invalid_argument``: a value that is not a JSON object, a value
    JSON cannot represent, a missing or non-object ``root``, and a ``root.type``
    that is not a string. Refused with ``limit_exceeded``: a document nested
    deeper than the depth ceiling, and one over the byte ceiling.

    The byte ceiling is measured on the compact re-serialization
    (``separators=(",", ":")``, ``ensure_ascii=False``), so it does not depend on
    how the caller's client spaced its request, and the depth ceiling is checked
    before that measurement, so no document reaches the serializer deep enough to
    exhaust its recursion.

    Unknown component types are accepted. This checks structure and budget, never
    the component inventory of a particular Ignition patch, and it cannot promise
    that an import will accept what it accepts.
    """

    limits = budget if budget is not None else DEFAULT_VIEW_BUDGET
    if not isinstance(document, dict):
        raise GatewayError("invalid_argument", "a View document must be a JSON object")
    root = document.get("root")
    if not isinstance(root, dict):
        raise GatewayError("invalid_argument", "a View document must have a 'root' object")
    if not isinstance(root.get("type"), str):
        raise GatewayError("invalid_argument", "a View document must have a string 'root.type'")
    return _measured(document, limits)


def validate_document(document: Any, *, budget: ViewBudget | None = None) -> ViewValidation:
    """Validate a whole-document replacement that has no required structure (D15).

    The Page configuration and the Session properties are whole-document
    replacements whose content this server does not interpret, so they take the
    same D10 ceilings as a View document and none of its ``root`` requirement.
    Refused with ``invalid_argument``: a value that is not a JSON object, or a
    value JSON cannot represent. Refused with ``limit_exceeded``: one over the
    byte or depth ceiling.
    """

    if not isinstance(document, dict):
        raise GatewayError("invalid_argument", "the document must be a JSON object")
    return _measured(document, budget if budget is not None else DEFAULT_VIEW_BUDGET)


def _measured(document: dict[str, Any], limits: ViewBudget) -> ViewValidation:
    """The D10 measurements of one JSON object: depth first, then compact bytes.

    The depth ceiling is checked before the serialization, so no document reaches
    the serializer deep enough to exhaust its recursion.
    """

    depth = _container_depth(document)
    if depth > limits.max_depth:
        raise GatewayError("limit_exceeded", _depth_message(depth, limits.max_depth))
    try:
        measured = len(document_bytes(document))
    except (TypeError, ValueError) as error:
        raise GatewayError(
            "invalid_argument", "the document must be JSON that JSON can represent",
        ) from error
    if measured > limits.max_bytes:
        raise GatewayError("limit_exceeded", _bytes_message(measured, limits.max_bytes))
    return ViewValidation(document=document, bytes=measured, depth=depth)


def _bytes_message(requested: int, limit: int) -> str:
    return (
        f"the View document is {requested} bytes, over the {limit}-byte limit; "
        "split it into smaller Views or remove unused components"
    )


def _depth_message(requested: int, limit: int) -> str:
    return (
        f"the View document nests {requested} levels deep, over the {limit}-level limit; "
        "flatten the component tree or split it into smaller Views"
    )


def _container_depth(value: Any) -> int:
    """JSON values on the longest path from ``value`` to a leaf.

    Counted breadth-first, so a document nested past the interpreter's recursion
    limit is measured rather than crashed on. A scalar is 1, ``{"a": {}}`` is 2.
    """

    levels = 0
    current: list[Any] = [value]
    while current:
        levels += 1
        following: list[Any] = []
        for item in current:
            if isinstance(item, dict):
                following.extend(item.values())
            elif isinstance(item, list):
                following.extend(item)
        current = following
    return levels


# --------------------------------------------------------------------------- the patch half


class PatchKind(str, Enum):
    """Which Perspective resource one :class:`ResourcePatch` changes (D15)."""

    VIEW_REPLACE = "view_replace"
    VIEW_DELETE = "view_delete"
    PAGE_CONFIG_REPLACE = "page_config_replace"
    SESSION_PROPS_REPLACE = "session_props_replace"


class SeekableBytes(Protocol):
    """A byte container a ZIP archive is read from or written to.

    Both directions of a patch need to seek, so the two spooled files the candidate
    builder threads are stated as this rather than as a bare file object. An
    ``io.BytesIO`` and a ``tempfile.SpooledTemporaryFile`` both qualify.
    """

    def read(self, size: int = -1) -> bytes: ...

    def write(self, data: bytes) -> int: ...

    def seek(self, offset: int, whence: int = 0) -> int: ...

    def tell(self) -> int: ...

    def flush(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ResourcePatch:
    """One typed change to exactly one Perspective resource (D15).

    ``document`` is the whole replacement document, and is ``None`` only for a
    delete. ``logical_path`` names the View for the two View kinds and is unused
    by the two Project-wide documents.
    """

    kind: PatchKind
    logical_path: str = ""
    document: dict[str, Any] | None = None

    def target_entry(self) -> str:
        """The archive entry whose presence means a Project defines this target.

        This is what the inheritance Preflight reads: a Project defines a
        Perspective resource locally exactly when its own export holds the entry
        the patch would replace or remove (D15).
        """

        if self.kind is PatchKind.PAGE_CONFIG_REPLACE:
            return PAGE_CONFIG_ENTRY
        if self.kind is PatchKind.SESSION_PROPS_REPLACE:
            return SESSION_PROPS_ENTRY
        return view_document_entry(self.logical_path)


def document_bytes(document: dict[str, Any]) -> bytes:
    """The exact bytes one replacement document is written as.

    Compact JSON, which is the serialization the D10 byte ceiling is measured on
    and what the D16 fingerprint hashes, so the ceiling and the archive agree.
    """

    return json.dumps(document, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def defines_target(archive_path: str, patch: ResourcePatch) -> bool:
    """Whether one Project archive defines the resource ``patch`` targets (D15)."""

    return patch.target_entry() in content_entries(archive_path)


def apply_patch(source: "SeekableBytes", patch: ResourcePatch, target: "SeekableBytes") -> None:
    """Write the baseline archive with exactly one Perspective resource changed.

    Every entry the patch does not name is copied byte-identically, in central
    directory order, keeping its own ``ZipInfo``: the D16 fingerprint hashes the
    uncompressed content of every entry, so nothing unrelated may move. The
    replaced entry keeps its place and its header when the archive already has it,
    and is appended with a fresh header when the patch creates it.

    ``source`` is an already D15-validated baseline, so entry names are read as
    validated. Both streams are seekable ZIP containers.
    """

    payload = None if patch.document is None else document_bytes(patch.document)
    replacement = {} if payload is None else {patch.target_entry(): payload}
    # zipfile's stubs only accept their own file protocols; a seekable byte container is
    # what it really needs, and that is what SeekableBytes states.
    with zipfile.ZipFile(cast(Any, source)) as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        dropped = _patch_entries(names, patch) - set(replacement)
        with zipfile.ZipFile(cast(Any, target), "w", zipfile.ZIP_DEFLATED) as out:
            for info in infos:
                if info.filename in dropped:
                    continue
                body = replacement.get(info.filename, archive.read(info))
                out.writestr(info, body)
            for name, body in replacement.items():
                if name not in names:
                    out.writestr(_new_entry(name), body)


def _patch_entries(names: list[str], patch: ResourcePatch) -> set[str]:
    """The entries one patch removes from the baseline archive."""

    if patch.kind is PatchKind.VIEW_DELETE:
        return _view_delete_entries(names, patch.logical_path)
    if patch.kind is PatchKind.PAGE_CONFIG_REPLACE:
        return {PAGE_CONFIG_ENTRY}
    if patch.kind is PatchKind.SESSION_PROPS_REPLACE:
        return {SESSION_PROPS_ENTRY}
    return {view_document_entry(patch.logical_path)}


def _view_delete_entries(names: list[str], logical_path: str) -> set[str]:
    """The entries deleting one View removes (D15, D26: one View, never a folder).

    The View's own entries go, which are its View document, its Designer resource
    metadata and the folder's directory marker. The folder's other entries, the
    ``folder.json`` that marks the path as a View folder and any nested View inside
    it, go only when the View was all that folder held, so a folder that also holds
    other Views keeps every one of them.
    """

    prefix = f"{view_directory(logical_path)}/"
    inside = {name for name in names if name.startswith(prefix)}
    own = {view_document_entry(logical_path), view_resource_entry(logical_path), prefix}
    markers = {prefix, f"{prefix}{VIEW_FOLDER_NAME}"}
    return inside if inside - markers <= own else own


def _new_entry(name: str) -> zipfile.ZipInfo:
    """A fresh header for an entry a patch creates."""

    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    return info


#: Bytes of a baseline archive one builder threads in memory before the spool
#: spills to a transaction-scoped temporary file (D10/D15 bound the scratch, not
#: the caller's patience).
SPOOL_MEMORY_BYTES = 4_194_304

#: Bytes per chunk streamed out of the spooled candidate.
PATCH_CHUNK_BYTES = 1_048_576


class PerspectiveCandidateBuilder(CandidateBuilder):
    """Candidate B: the baseline archive with one Perspective resource patched (D15).

    It reads the baseline only as a bounded stream and writes the candidate only
    through the transaction's own writer. The ZIP rewrite needs a seekable
    container in both directions, so the baseline is spooled to a temporary file
    that is scoped to this call and deleted with it, never to a Project artifact
    (D15: temporary workspace resources are transaction-scoped and cleaned on
    every exit path).
    """

    def __init__(self, patch: ResourcePatch) -> None:
        self._patch = patch

    async def build(self, baseline: ArtifactReader, out: ArtifactWriter) -> None:
        with tempfile.SpooledTemporaryFile(max_size=SPOOL_MEMORY_BYTES) as source:
            while True:
                chunk = await baseline.read_chunk()
                if chunk is None:
                    break
                source.write(chunk)
            source.seek(0)
            with tempfile.SpooledTemporaryFile(max_size=SPOOL_MEMORY_BYTES) as patched:
                await asyncio.to_thread(apply_patch, source, self._patch, patched)
                patched.seek(0)
                while True:
                    chunk = await asyncio.to_thread(patched.read, PATCH_CHUNK_BYTES)
                    if not chunk:
                        return
                    await out.write(chunk)
