"""D16 Project logical fingerprint: algorithm ``project-content-v1`` -> ``pcf1:<64 hex>``.

Decision links (``docs/decisions/``):

- D16 — logical Project identity is the *content* fingerprint, used for no-op
  detection, the pre-import concurrency check and post-import reconciliation.
  It is deliberately distinct from the D17 exact-byte artifact SHA-256, which
  proves transport/storage integrity of one particular byte string.
- D15/D17 — an archive must pass the :mod:`ignition_rest_mcp.projects.zip_safety`
  gate before it is digested at all, and the canonical-path rules are re-checked
  here independently (defense in depth): no caller can reach the digest by
  skipping the validator.

What the digest covers (D16: nothing excluded, no content normalization):

* every non-directory entry (names ending ``/`` are directory markers);
* the validated entry name exactly as stored — no Unicode normalization and no
  case folding at digest level (those transformations exist only for duplicate
  detection in ``zip_safety``);
* the uncompressed length and the uncompressed bytes.

What it ignores, so semantically identical exports fingerprint the same:
entry order, timestamps, comments, extra fields and the compression method or
level. Entries are digested in ascending UTF-8 byte order of the canonical path,
and the framing makes every boundary unambiguous:

``SHA-256( b"project-content-v1\\n" || for each entry:
    u64be(len(path)) || path || u64be(uncompressed_len) || uncompressed_bytes )``

The whole archive is never held in memory: each entry is streamed into the
running digest in :data:`DIGEST_CHUNK_BYTES` chunks and the D15 size ceilings are
enforced against the bytes *actually* streamed, so a header that lies about its
size cannot blow memory or time (D10: bounded input, definite refusal, never a
partial result). ``extractall`` is NEVER used.
"""

from __future__ import annotations

import hashlib
import stat
import struct
import zipfile
from typing import Never

from ignition_rest_mcp.projects.zip_safety import (
    DEFAULT_ZIP_SAFETY_CONFIG,
    ENTRY_NAME_ECHO_LIMIT,
    RULE_ENTRY_SIZE,
    RULE_ENTRY_TYPE,
    RULE_TOTAL_SIZE,
    UnsafeArchiveError,
    ZipSafetyConfig,
    canonical_entry_name,
    entry_label,
    is_directory_entry,
    validate_zip_file,
)

#: D16 algorithm label; also the digest's domain-separation prefix.
PROJECT_CONTENT_ALGORITHM = "project-content-v1"

#: Prefix of the rendered fingerprint (``pcf1:<64 lowercase hex>``).
FINGERPRINT_PREFIX = "pcf1"

#: Maximum bytes buffered while streaming one entry into the digest.
DIGEST_CHUNK_BYTES = 1024 * 1024

#: The readable content of an entry does not agree with its declared header.
RULE_SIZE_MISMATCH = "uncompressed-size-mismatch"

_ALGORITHM_HEADER = PROJECT_CONTENT_ALGORITHM.encode("ascii") + b"\n"
_U64BE = struct.Struct(">Q")


def project_fingerprint(path: str, *, config: ZipSafetyConfig | None = None) -> str:
    """Return the D16 ``pcf1:<64 hex>`` fingerprint of the Project archive at ``path``.

    The archive is validated against ``config`` (defaulting to
    :data:`~ignition_rest_mcp.projects.zip_safety.DEFAULT_ZIP_SAFETY_CONFIG`)
    before any content is read, then every non-directory entry is digested in
    canonical-path order. Raises
    :class:`~ignition_rest_mcp.projects.zip_safety.UnsafeArchiveError` for a
    refused archive or an entry whose streamed bytes disagree with its declared
    uncompressed size.
    """

    limits = config if config is not None else DEFAULT_ZIP_SAFETY_CONFIG
    validate_zip_file(path, limits)

    digest = hashlib.sha256()
    digest.update(_ALGORITHM_HEADER)
    streamed_total = 0
    with zipfile.ZipFile(path) as archive:
        for path_bytes, name, info in _content_entries(archive):
            streamed_total += _digest_content_entry(archive, info, path_bytes, name, digest, limits, streamed_total)
    return f"{FINGERPRINT_PREFIX}:{digest.hexdigest()}"


def _content_entries(archive: zipfile.ZipFile) -> list[tuple[bytes, str, zipfile.ZipInfo]]:
    """Return ``(path bytes, canonical name, header)`` sorted by path bytes ascending.

    Directory markers are skipped (D16), every name is re-validated here, and an
    entry that declares a non-regular file type still cannot reach the digest.
    Only the *declared* type bits are trusted: ZIP writers routinely store
    permissions without any type bits (CPython writes ``0o600 << 16``, Java
    writes ``0``), so an absent type claim is not evidence of a symlink.
    """

    entries: list[tuple[bytes, str, zipfile.ZipInfo]] = []
    for info in archive.infolist():
        name = canonical_entry_name(info)
        if is_directory_entry(name):
            continue
        file_type = stat.S_IFMT(info.external_attr >> 16)
        if file_type and file_type != stat.S_IFREG:
            raise UnsafeArchiveError(
                f"entry {entry_label(name)} violates the {RULE_ENTRY_TYPE} rule: only regular files carry content",
            )
        entries.append((name.encode("utf-8"), name, info))
    entries.sort(key=lambda item: item[0])
    return entries


def _digest_content_entry(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    path_bytes: bytes,
    name: str,
    digest: hashlib._Hash,
    limits: ZipSafetyConfig,
    already_streamed: int,
) -> int:
    """Frame one entry and stream its uncompressed bytes into ``digest``.

    The declared length is framed before the body and then cross-checked against
    the bytes actually read, so one pass suffices while every accepted archive is
    framed exactly as ``u64be(uncompressed_len) || uncompressed_bytes``.
    """

    declared = info.file_size
    digest.update(_U64BE.pack(len(path_bytes)))
    digest.update(path_bytes)
    digest.update(_U64BE.pack(declared))

    read = 0
    try:
        with archive.open(info, "r") as stream:
            while True:
                chunk = stream.read(DIGEST_CHUNK_BYTES)
                if not chunk:
                    break
                read += len(chunk)
                if read > declared:
                    _refuse_size(name, declared, read)
                if read > limits.max_entry_uncompressed_bytes:
                    raise UnsafeArchiveError(
                        f"entry {entry_label(name)} violates the {RULE_ENTRY_SIZE} rule: more than "
                        f"{limits.max_entry_uncompressed_bytes} bytes were streamed",
                    )
                if already_streamed + read > limits.max_total_uncompressed_bytes:
                    raise UnsafeArchiveError(
                        f"entry {entry_label(name)} violates the {RULE_TOTAL_SIZE} rule: more than "
                        f"{limits.max_total_uncompressed_bytes} bytes were streamed in total",
                    )
                digest.update(chunk)
    except (zipfile.BadZipFile, EOFError) as error:
        detail = " ".join(str(error).split())[:ENTRY_NAME_ECHO_LIMIT]
        raise UnsafeArchiveError(
            f"entry {entry_label(name)} violates the {RULE_SIZE_MISMATCH} rule: the stored body does not yield "
            f"the declared {declared} uncompressed bytes ({detail})",
        ) from error

    if read != declared:
        _refuse_size(name, declared, read)
    return read


def _refuse_size(name: str, declared: int, read: int) -> Never:
    raise UnsafeArchiveError(
        f"entry {entry_label(name)} violates the {RULE_SIZE_MISMATCH} rule: the central directory declares "
        f"{declared} uncompressed bytes but {read} were streamed",
    )
