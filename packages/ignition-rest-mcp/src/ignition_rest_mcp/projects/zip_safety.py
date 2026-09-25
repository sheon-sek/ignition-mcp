"""D15 ZIP safety validator: streamed central-directory + entry checks.

Decision links (``docs/decisions/``):

- D15 — every Project archive that reaches the typed ZIP resource adapter or an
  upload must pass the ZIP safety gate first: traversal, absolute paths,
  symlinks / non-regular entries, duplicate names, entry count, expanded size
  and compression-bomb controls. No generic project-resource path is validated
  more loosely than this.
- D16 — validation is metadata-only over the central directory. The archive is
  never extracted, nothing is written, ``extractall`` is NEVER called and no
  whole-archive buffer exists. Entry content is streamed later, in bounded
- D17 — this module is the validator body for ``project_archive`` /
  ``project_export`` artifacts, so every refusal is reported through the
  artifact-validator contract: :class:`UnsafeArchiveError` carrying the D06
  ``invalid_argument`` code plus a message that names the violated rule.
- D10 — an archive is *input*, so nothing about it may be unbounded: entry
  count, per-entry and total expanded size and expansion ratio all carry finite
  ceilings in :class:`ZipSafetyConfig`, and a violation is a definite refusal
  rather than a silently bounded or partial read.
  ``invalid_argument`` code plus a message that names the violated rule.

Entry names are the attack surface, so they are never echoed back unbound:
:func:`entry_label` truncates the offending name to
:data:`ENTRY_NAME_ECHO_LIMIT` characters.
"""

from __future__ import annotations

import re
import stat
import unicodedata
import zipfile
from dataclasses import dataclass
from typing import Never, Sequence

from ignition_rest_mcp.errors import GatewayError

#: Longest offending entry name reproduced inside a refusal message.
ENTRY_NAME_ECHO_LIMIT = 120

#: Central-directory rules implemented by :func:`validate_zip_file`. Each
#: refusal message names exactly one of these so a caller can map a rejection
#: back to the violated D15 rule without parsing prose.
RULE_READABLE_ZIP = "readable-zip"
RULE_NON_EMPTY_CENTRAL_DIRECTORY = "non-empty-central-directory"
RULE_ENTRY_COUNT = "entry-count"
RULE_UTF8_ENTRY_NAME = "utf8-entry-name"
RULE_ASCII_ENTRY_NAME = "ascii-entry-name-without-utf8-flag"
RULE_ABSOLUTE_ENTRY_NAME = "absolute-entry-name"
RULE_DRIVE_LETTER_ENTRY_NAME = "drive-letter-entry-name"
RULE_BACKSLASH_ENTRY_NAME = "backslash-entry-name"
RULE_EMPTY_ENTRY_NAME = "empty-entry-name"
RULE_TRAVERSAL_ENTRY_NAME = "path-traversal-entry-name"
RULE_ENTRY_TYPE = "regular-file-entry"
RULE_DIRECTORY_ENTRY_DATA = "directory-entry-without-data"
RULE_ENCRYPTED_ENTRY = "unencrypted-entry"
RULE_DUPLICATE_ENTRY_NAME = "unique-entry-name"
RULE_DUPLICATE_NORMALIZED_ENTRY_NAME = "nfc-unique-entry-name"
RULE_DUPLICATE_CASED_ENTRY_NAME = "casefold-unique-entry-name"
RULE_ENTRY_SIZE = "entry-uncompressed-size-limit"
RULE_TOTAL_SIZE = "total-uncompressed-size-limit"
RULE_MISSING_COMPRESSED_SIZE = "compressed-size-declared"
RULE_ENTRY_RATIO = "entry-compression-ratio-limit"
RULE_TOTAL_RATIO = "total-compression-ratio-limit"

_UTF8_NAME_FLAG = 0x800
_ENCRYPTED_FLAG = 0x01
_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")


@dataclass(frozen=True, slots=True)
class ZipSafetyConfig:
    """Finite D15 ceilings. Frozen, immutable and safe to share across calls.

    ``min_compression_ratio`` is the smallest tolerated
    ``compress_size / file_size`` for one non-stored entry, i.e. the maximum
    expansion accepted (``0.01`` = at most 100x). ``max_ratio_total`` is the
    opposite direction: the largest tolerated
    ``total uncompressed / total compressed`` for the whole archive. A
    non-positive value disables the corresponding ratio check.
    """

    max_entries: int = 4096
    max_entry_uncompressed_bytes: int = 268_435_456
    max_total_uncompressed_bytes: int = 1_073_741_824
    min_compression_ratio: float = 0.01
    max_ratio_total: float = 1000.0


#: The shared default ceilings: :class:`ZipSafetyConfig` is frozen, so one
#: immutable instance can be resolved by every call that omits ``config``.
DEFAULT_ZIP_SAFETY_CONFIG = ZipSafetyConfig()


@dataclass(frozen=True, slots=True)
class ZipSummary:
    """Central-directory totals observed by a passing validation run."""

    entries: int
    total_uncompressed: int
    total_compressed: int


class UnsafeArchiveError(GatewayError):
    """A D15 safety refusal; always mapped to the D06 ``invalid_argument`` code."""

    def __init__(self, message: str) -> None:
        super().__init__("invalid_argument", message)


def entry_label(name: str) -> str:
    """Render an entry name for a refusal message, bounded and escaped."""

    clipped = name if len(name) <= ENTRY_NAME_ECHO_LIMIT else name[:ENTRY_NAME_ECHO_LIMIT] + "\u2026"
    return repr(clipped)


def _refuse(rule: str, name: str, detail: str = "") -> Never:
    """Fail closed on the first violated rule, naming the rule and the entry."""

    suffix = f": {detail}" if detail else ""
    raise UnsafeArchiveError(f"entry {entry_label(name)} violates the {rule} rule{suffix}")


def _refuse_archive(rule: str, detail: str) -> Never:
    raise UnsafeArchiveError(f"archive violates the {rule} rule: {detail}")


def is_directory_entry(name: str) -> bool:
    """D16: a trailing ``/`` marks a directory entry, never a content entry."""

    return name.endswith("/")


def canonical_entry_name(info: zipfile.ZipInfo) -> str:
    """Return the validated entry name exactly as stored (the D16 canonical path).

    Raises :class:`UnsafeArchiveError` on any D15 name violation: invalid
    encoding (a name without the UTF-8 flag must be pure ASCII; a name with the
    flag must have survived UTF-8 decoding), absolute paths, drive letters,
    backslashes and empty / ``.`` / ``..`` segments. No Unicode normalization or
    case folding is applied to the returned value — those transformations exist
    only for duplicate detection.
    """

    name = info.filename
    if info.flag_bits & _UTF8_NAME_FLAG:
        try:
            name.encode("utf-8")
        except UnicodeEncodeError:
            _refuse(RULE_UTF8_ENTRY_NAME, name, "the UTF-8 name flag is set but the stored bytes are not UTF-8")
    elif not name.isascii():
        _refuse(
            RULE_ASCII_ENTRY_NAME,
            name,
            "the UTF-8 name flag is clear, so the stored filename bytes must be ASCII",
        )

    if not name:
        _refuse(RULE_EMPTY_ENTRY_NAME, name, "the stored filename is zero length")
    if _DRIVE_PREFIX.match(name):
        _refuse(RULE_DRIVE_LETTER_ENTRY_NAME, name, "Windows drive-qualified paths are refused")
    if "\\" in name:
        _refuse(RULE_BACKSLASH_ENTRY_NAME, name, "backslash separators are refused")

    target = name[:-1] if is_directory_entry(name) else name
    if not target:
        _refuse(RULE_ABSOLUTE_ENTRY_NAME, name, "the root directory alone is not a relative path")
    if target.startswith("/"):
        _refuse(RULE_ABSOLUTE_ENTRY_NAME, name, "a leading '/' is an absolute path")
    for segment in target.split("/"):
        if segment in ("", ".", ".."):
            _refuse(RULE_TRAVERSAL_ENTRY_NAME, name, f"path segment {segment!r} escapes or aliases the archive root")
    return name


def _short(detail: str) -> str:
    """Bound a stdlib message: it can quote a stored (attacker-controlled) name."""

    collapsed = " ".join(detail.split())
    return collapsed if len(collapsed) <= ENTRY_NAME_ECHO_LIMIT else collapsed[:ENTRY_NAME_ECHO_LIMIT] + "\u2026"


def _read_central_directory(path: str) -> Sequence[zipfile.ZipInfo]:
    """Parse only the central directory; the archive body is never touched."""

    infos: list[zipfile.ZipInfo]
    try:
        with zipfile.ZipFile(path) as archive:
            infos = list(archive.infolist())
    except zipfile.BadZipFile as error:
        _refuse_archive(RULE_READABLE_ZIP, f"the central directory is malformed ({_short(str(error))})")
    except UnicodeDecodeError as error:
        _refuse_archive(RULE_UTF8_ENTRY_NAME, f"a stored filename is not valid UTF-8 ({_short(str(error))})")
    except NotImplementedError as error:
        _refuse_archive(RULE_READABLE_ZIP, f"the archive uses an unsupported zip feature ({_short(str(error))})")
    except ValueError as error:
        _refuse_archive(RULE_READABLE_ZIP, f"the object is not a readable ZIP archive ({_short(str(error))})")
    except OSError as error:
        _refuse_archive(RULE_READABLE_ZIP, f"the archive could not be read ({type(error).__name__})")
    return infos


def _check_entry_type(info: zipfile.ZipInfo, name: str) -> None:
    """Reject anything that is not a regular file or an empty directory marker."""

    mode = info.external_attr >> 16
    if stat.S_ISLNK(mode):
        _refuse(RULE_ENTRY_TYPE, name, "symbolic links are refused")
    if stat.S_ISCHR(mode) or stat.S_ISBLK(mode):
        _refuse(RULE_ENTRY_TYPE, name, "device entries are refused")
    if stat.S_ISFIFO(mode):
        _refuse(RULE_ENTRY_TYPE, name, "FIFO entries are refused")
    if stat.S_ISSOCK(mode):
        _refuse(RULE_ENTRY_TYPE, name, "socket entries are refused")
    if is_directory_entry(name) and info.file_size > 0:
        _refuse(
            RULE_DIRECTORY_ENTRY_DATA,
            name,
            f"a directory marker declares {info.file_size} bytes of content",
        )


def _check_not_encrypted(info: zipfile.ZipInfo, name: str) -> None:
    if info.flag_bits & _ENCRYPTED_FLAG:
        _refuse(RULE_ENCRYPTED_ENTRY, name, "encrypted entries cannot be validated and are refused")


def _check_declared_size(info: zipfile.ZipInfo, name: str, config: ZipSafetyConfig) -> None:
    if info.file_size > config.max_entry_uncompressed_bytes:
        _refuse(
            RULE_ENTRY_SIZE,
            name,
            f"{info.file_size} declared uncompressed bytes exceed the limit of "
            f"{config.max_entry_uncompressed_bytes}",
        )


def _check_entry_ratio(info: zipfile.ZipInfo, name: str, config: ZipSafetyConfig) -> None:
    """Refuse per-entry expansion beyond ``1 / min_compression_ratio``.

    Stored entries are exempt: by definition they expand nothing. A non-stored
    entry that declares content but no compressed size cannot be bounded at all,
    so it is refused rather than trusted.
    """

    if info.compress_type == zipfile.ZIP_STORED or config.min_compression_ratio <= 0.0:
        return
    if info.compress_size == 0:
        if info.file_size > 0:
            _refuse(
                RULE_MISSING_COMPRESSED_SIZE,
                name,
                f"compression method {info.compress_type} declares {info.file_size} "
                "uncompressed bytes and no compressed size",
            )
        return
    if info.file_size == 0:
        return
    observed = info.compress_size / info.file_size
    if observed < config.min_compression_ratio:
        _refuse(
            RULE_ENTRY_RATIO,
            name,
            f"expands {info.file_size} bytes out of {info.compress_size} compressed bytes "
            f"(ratio {observed:.6g} below the minimum {config.min_compression_ratio:g})",
        )


def _check_unique_name(
    name: str,
    seen_exact: set[str],
    seen_nfc: set[str],
    seen_folded: set[str],
) -> None:
    """Reject repeated names, including names colliding after NFC or case fold.

    One archive cannot hold the same logical path twice: on extraction the later
    entry silently replaces the earlier one, which is a write-what-where
    primitive. The NFC and case-fold passes close the macOS (``NFD`` vs ``NFC``)
    and Windows/zip-swing (case-insensitive) collision tricks.
    """

    if name in seen_exact:
        _refuse(RULE_DUPLICATE_ENTRY_NAME, name, "the identical name appears more than once")
    normalized = unicodedata.normalize("NFC", name)
    if normalized in seen_nfc:
        _refuse(RULE_DUPLICATE_NORMALIZED_ENTRY_NAME, name, "the NFC-normalized name appears more than once")
    folded = normalized.casefold()
    if folded in seen_folded:
        _refuse(RULE_DUPLICATE_CASED_ENTRY_NAME, name, "the case-folded normalized name appears more than once")
    seen_exact.add(name)
    seen_nfc.add(normalized)
    seen_folded.add(folded)


def validate_zip_file(path: str, config: ZipSafetyConfig | None = None) -> ZipSummary:
    """Apply every D15 central-directory safety rule to the archive at ``path``.

    Metadata-only: the central directory is parsed and each entry header is
    checked in stored order, raising :class:`UnsafeArchiveError` on the first
    violation. Directory markers count toward the entry cap and contribute zero
    bytes. ``config`` defaults to :data:`DEFAULT_ZIP_SAFETY_CONFIG`.

    Returns the observed entry count and uncompressed/compressed totals.
    """

    limits = config if config is not None else DEFAULT_ZIP_SAFETY_CONFIG
    infos = _read_central_directory(path)
    if not infos:
        _refuse_archive(RULE_NON_EMPTY_CENTRAL_DIRECTORY, "the central directory holds no entries")
    if len(infos) > limits.max_entries:
        _refuse_archive(RULE_ENTRY_COUNT, f"{len(infos)} entries exceed the limit of {limits.max_entries}")

    seen_exact: set[str] = set()
    seen_nfc: set[str] = set()
    seen_folded: set[str] = set()
    total_uncompressed = 0
    total_compressed = 0

    for info in infos:
        name = canonical_entry_name(info)
        _check_entry_type(info, name)
        _check_not_encrypted(info, name)
        _check_declared_size(info, name, limits)
        _check_entry_ratio(info, name, limits)
        _check_unique_name(name, seen_exact, seen_nfc, seen_folded)
        total_uncompressed += info.file_size
        total_compressed += info.compress_size
        if total_uncompressed > limits.max_total_uncompressed_bytes:
            _refuse(
                RULE_TOTAL_SIZE,
                name,
                f"cumulative {total_uncompressed} uncompressed bytes exceed the limit of "
                f"{limits.max_total_uncompressed_bytes}",
            )

    if (
        limits.max_ratio_total > 0.0
        and total_compressed > 0
        and total_uncompressed / total_compressed > limits.max_ratio_total
    ):
        _refuse_archive(
            RULE_TOTAL_RATIO,
            f"{total_uncompressed} uncompressed bytes over {total_compressed} compressed bytes exceed "
            f"the total ratio of {limits.max_ratio_total:g}",
        )

    return ZipSummary(entries=len(infos), total_uncompressed=total_uncompressed, total_compressed=total_compressed)
