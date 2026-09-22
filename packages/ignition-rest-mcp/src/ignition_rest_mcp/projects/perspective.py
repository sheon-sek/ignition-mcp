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

import json
import zipfile
from dataclasses import dataclass
from typing import Any

from ignition_rest_mcp.errors import GatewayError
from ignition_rest_mcp.projects.zip_safety import is_directory_entry

#: The Perspective module directory inside a Project export.
PERSPECTIVE_MODULE = "com.inductiveautomation.perspective"

#: The Views resource directory: one directory per View, named by its Logical path.
VIEWS_DIRECTORY = f"{PERSPECTIVE_MODULE}/views"

#: The View document inside a View directory.
VIEW_DOCUMENT_NAME = "view.json"

#: The Designer resource metadata beside it.
VIEW_RESOURCE_NAME = "resource.json"

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

    Refused with ``invalid_argument``: a non-string or empty value, a leading
    ``/``, a backslash, any empty, ``.`` or ``..`` segment, a control character,
    and a character Ignition refuses in a resource name. Refused with
    ``limit_exceeded``: a path over :data:`MAX_LOGICAL_PATH_BYTES`.
    """

    if not isinstance(value, str) or not value:
        raise GatewayError("invalid_argument", "path must be a non-empty Logical resource path")
    if len(value.encode("utf-8")) > MAX_LOGICAL_PATH_BYTES:
        raise GatewayError(
            "limit_exceeded",
            f"path is {len(value.encode('utf-8'))} bytes, over the {MAX_LOGICAL_PATH_BYTES}-byte limit; "
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
    archive_path: str, entry: str, *, budget: ViewBudget | None = None,
) -> dict[str, Any] | None:
    """The JSON object at ``entry``, or ``None`` when the archive has no such entry.

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
                    f"{entry} declares {info.file_size} bytes, over the {limits.max_bytes}-byte "
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
            "schema_mismatch", f"the Project export entry {entry} is not UTF-8 JSON this server can read",
        ) from error
    if not isinstance(document, dict):
        raise GatewayError("schema_mismatch", f"the Project export entry {entry} is not a JSON object")
    return document


def read_view_document(archive_path: str, logical_path: str) -> dict[str, Any]:
    """The View document at one Logical resource path, or ``not_found``."""

    path = validate_logical_resource_path(logical_path)
    document = read_document(archive_path, view_document_entry(path))
    if document is None:
        raise GatewayError("not_found", f"this Project has no Local View at {path!r}")
    return document


def read_page_config(archive_path: str) -> dict[str, Any] | None:
    """The Project's Page configuration document, or ``None`` when it has none locally."""

    return read_document(archive_path, PAGE_CONFIG_ENTRY)


def read_session_props(archive_path: str) -> dict[str, Any] | None:
    """The Project's Session properties document, or ``None`` when it has none locally."""

    return read_document(archive_path, SESSION_PROPS_ENTRY)


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
    depth = _container_depth(document)
    if depth > limits.max_depth:
        raise GatewayError("limit_exceeded", _depth_message(depth, limits.max_depth))
    try:
        measured = len(
            json.dumps(document, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
        )
    except (TypeError, ValueError) as error:
        raise GatewayError(
            "invalid_argument", "view must be a JSON object that JSON can represent",
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
