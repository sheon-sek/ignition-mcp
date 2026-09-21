"""Slice 3 (Phase 3 / G3): D16 ``project-content-v1`` logical fingerprint.

Covers both halves of the contract: invariance under every byte-level incidental
(entry order, compression method/level, timestamps, extra fields, comments,
directory markers) and sensitivity to every logical change (a content byte, a
path, a real empty file), plus the D15 gate, the header/stream cross-check and
the committed golden vector.
"""

from __future__ import annotations

import hashlib
import re
import stat
import struct
from pathlib import Path
import zipfile

import pytest

from ignition_rest_mcp.projects.fingerprint import (
    PROJECT_CONTENT_ALGORITHM,
    RULE_SIZE_MISMATCH,
    project_fingerprint,
)
from ignition_rest_mcp.projects.zip_safety import UnsafeArchiveError, ZipSafetyConfig

GOLDEN_TIME = (1980, 1, 1, 0, 0, 0)
GOLDEN_ENTRIES: tuple[tuple[str, bytes], ...] = (
    ("project.json", b'{"name":"golden","version":"8.1.43"}\n'),
    ("pages/index.html", b"<html><body>golden</body></html>\n"),
    ("tags/default/tagset.json", b'{"name":"default","tags":[{"name":"flow","value":12}]}\n'),
)

#: Reproducibility anchor for ``project-content-v1``: built from GOLDEN_ENTRIES
#: only, ZIP_DEFLATED level 9, fixed 1980-01-01 timestamps, no entry or archive
#: comment, no extra fields. The algorithm covers canonical paths, uncompressed
#: lengths and uncompressed bytes, so the literal is independent of the deflate
#: level and of any metadata choice; it was produced by this implementation and is
#: asserted verbatim (and re-derived independently in
#: ``test_digest_matches_the_specified_byte_framing``).
GOLDEN_PCF1 = "pcf1:1fd1aaa11b4af5ac851fb18ffd52ca42dbef01a4ac27dd1337aa803610fd6f86"

PCF1_PATTERN = re.compile(r"^pcf1:[0-9a-f]{64}$")
CENTRAL_SIGNATURE = b"PK\x01\x02"
CHUNK_SPAN = 1024 * 1024 + 7


def build(
    tmp_path: Path,
    entries: list[tuple[str, bytes]],
    *,
    name: str = "project.zip",
    compress_type: int = zipfile.ZIP_DEFLATED,
    compresslevel: int | None = None,
    date_time: tuple[int, int, int, int, int, int] = GOLDEN_TIME,
    extra: bytes = b"",
    entry_comment: bytes = b"",
    archive_comment: bytes = b"",
    directories: tuple[str, ...] = (),
    reverse: bool = False,
) -> Path:
    """Write one candidate archive; every knob here must be digest-incidental."""

    path = tmp_path / name
    pairs = list(reversed(entries)) if reverse else list(entries)
    with zipfile.ZipFile(path, "w", compression=compress_type, compresslevel=compresslevel) as output:
        for entry_name, payload in pairs:
            info = zipfile.ZipInfo(entry_name, date_time=date_time)
            info.compress_type = compress_type
            info.extra = extra
            info.comment = entry_comment
            output.writestr(info, payload)
        for directory in directories:
            marker = zipfile.ZipInfo(directory, date_time=date_time)
            marker.compress_type = zipfile.ZIP_STORED
            output.writestr(marker, b"")
        if archive_comment:
            output.comment = archive_comment
    return path


def reference_digest(entries: list[tuple[str, bytes]]) -> str:
    """Re-derive the digest straight from the specified framing, independently."""

    digest = hashlib.sha256()
    digest.update(PROJECT_CONTENT_ALGORITHM.encode("ascii") + b"\n")
    for entry_name, payload in sorted(entries, key=lambda item: item[0].encode("utf-8")):
        path_bytes = entry_name.encode("utf-8")
        digest.update(struct.pack(">Q", len(path_bytes)))
        digest.update(path_bytes)
        digest.update(struct.pack(">Q", len(payload)))
        digest.update(payload)
    return f"pcf1:{digest.hexdigest()}"


def patch_central_file_size(path: Path, entry_name: str, file_size: int) -> None:
    """Lie about one entry's uncompressed size in the central directory only."""

    buffer = bytearray(path.read_bytes())
    needle = entry_name.encode("utf-8")
    cursor = 0
    while True:
        start = buffer.find(CENTRAL_SIGNATURE, cursor)
        assert start >= 0, "central directory record not found"
        name_len, extra_len, comment_len = struct.unpack_from("<HHH", buffer, start + 28)
        if bytes(buffer[start + 46 : start + 46 + name_len]) == needle:
            struct.pack_into("<I", buffer, start + 24, file_size)
            path.write_bytes(bytes(buffer))
            return
        cursor = start + 46 + name_len + extra_len + comment_len


# --- the committed vector ---------------------------------------------------


def test_golden_vector_matches_committed_digest(tmp_path: Path) -> None:
    path = build(tmp_path, list(GOLDEN_ENTRIES), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)

    digest = project_fingerprint(str(path))

    assert digest == GOLDEN_PCF1
    assert PCF1_PATTERN.match(digest)


def test_digest_matches_the_specified_byte_framing(tmp_path: Path) -> None:
    entries = [*GOLDEN_ENTRIES, ("tags/large.json", b"[" * CHUNK_SPAN)]

    path = build(tmp_path, entries, compress_type=zipfile.ZIP_STORED)

    assert project_fingerprint(str(path)) == reference_digest(entries)


# --- invariance -------------------------------------------------------------


def test_digest_ignores_entry_order(tmp_path: Path) -> None:
    forward = build(tmp_path, list(GOLDEN_ENTRIES), name="forward.zip")
    backward = build(tmp_path, list(GOLDEN_ENTRIES), name="backward.zip", reverse=True)

    assert project_fingerprint(str(forward)) == project_fingerprint(str(backward)) == GOLDEN_PCF1


def test_digest_ignores_compression_method_and_level(tmp_path: Path) -> None:
    stored = build(tmp_path, list(GOLDEN_ENTRIES), name="stored.zip", compress_type=zipfile.ZIP_STORED)
    fast = build(tmp_path, list(GOLDEN_ENTRIES), name="fast.zip", compresslevel=1)
    maximum = build(tmp_path, list(GOLDEN_ENTRIES), name="maximum.zip", compresslevel=9)

    assert project_fingerprint(str(stored)) == GOLDEN_PCF1
    assert project_fingerprint(str(fast)) == project_fingerprint(str(maximum)) == GOLDEN_PCF1


def test_digest_ignores_timestamps_extra_fields_and_comments(tmp_path: Path) -> None:
    plain = build(tmp_path, list(GOLDEN_ENTRIES), name="plain.zip")
    retagged = build(
        tmp_path,
        list(GOLDEN_ENTRIES),
        name="retagged.zip",
        date_time=(2026, 9, 21, 3, 4, 6),
        extra=b"XX\x04\x00abcd",
        entry_comment=b"written by a different exporter",
        archive_comment=b"gateway 8.1.43 build 20260921",
    )

    assert project_fingerprint(str(retagged)) == project_fingerprint(str(plain)) == GOLDEN_PCF1


def test_digest_ignores_empty_directory_entries(tmp_path: Path) -> None:
    files = build(tmp_path, list(GOLDEN_ENTRIES), name="files-only.zip")
    with_markers = build(
        tmp_path,
        list(GOLDEN_ENTRIES),
        name="with-markers.zip",
        directories=("tags/", "tags/default/", "pages/"),
    )

    assert project_fingerprint(str(with_markers)) == project_fingerprint(str(files)) == GOLDEN_PCF1


# --- sensitivity ------------------------------------------------------------


def test_digest_changes_when_a_single_content_byte_is_flipped(tmp_path: Path) -> None:
    flipped = list(GOLDEN_ENTRIES)
    name, payload = flipped[1]
    body = bytearray(payload)
    body[0] ^= 0x01
    flipped[1] = (name, bytes(body))

    original = build(tmp_path, list(GOLDEN_ENTRIES), name="original.zip")
    changed = build(tmp_path, flipped, name="changed.zip")

    assert project_fingerprint(str(original)) == GOLDEN_PCF1
    assert project_fingerprint(str(changed)) != GOLDEN_PCF1


def test_digest_changes_when_a_path_is_renamed(tmp_path: Path) -> None:
    renamed = list(GOLDEN_ENTRIES)
    renamed[0] = ("project.cfg", renamed[0][1])

    original = build(tmp_path, list(GOLDEN_ENTRIES), name="original.zip")
    moved = build(tmp_path, renamed, name="renamed.zip")

    assert project_fingerprint(str(moved)) != project_fingerprint(str(original))


def test_digest_changes_when_an_empty_file_is_added_or_removed(tmp_path: Path) -> None:
    baseline = build(tmp_path, list(GOLDEN_ENTRIES), name="baseline.zip")
    emptied = build(tmp_path, [*GOLDEN_ENTRIES, ("notes/empty.txt", b"")], name="emptied.zip")
    restored = build(tmp_path, list(GOLDEN_ENTRIES), name="restored.zip")

    assert project_fingerprint(str(baseline)) == GOLDEN_PCF1
    assert project_fingerprint(str(emptied)) != GOLDEN_PCF1
    assert project_fingerprint(str(restored)) == GOLDEN_PCF1


def test_digest_distinguishes_an_empty_file_from_a_directory_of_the_same_name(tmp_path: Path) -> None:
    file_entry = build(tmp_path, [*GOLDEN_ENTRIES, ("tags/note", b"")], name="file.zip")
    directory_entry = build(tmp_path, list(GOLDEN_ENTRIES), name="directory.zip", directories=("tags/note/",))

    assert project_fingerprint(str(file_entry)) != GOLDEN_PCF1
    assert project_fingerprint(str(directory_entry)) == GOLDEN_PCF1


# --- the D15 gate and the header cross-check --------------------------------


def test_digest_refuses_an_archive_failing_zip_safety(tmp_path: Path) -> None:
    path = tmp_path / "symlink.zip"
    with zipfile.ZipFile(path, "w") as output:
        for entry_name, payload in GOLDEN_ENTRIES:
            output.writestr(entry_name, payload)
        link = zipfile.ZipInfo("escape", date_time=GOLDEN_TIME)
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        output.writestr(link, b"../../etc/passwd")

    with pytest.raises(UnsafeArchiveError) as captured:
        project_fingerprint(str(path))

    assert "regular-file-entry" in str(captured.value)


def test_digest_applies_the_callers_ceilings_before_reading_any_content(tmp_path: Path) -> None:
    path = build(tmp_path, list(GOLDEN_ENTRIES))

    with pytest.raises(UnsafeArchiveError) as captured:
        project_fingerprint(str(path), config=ZipSafetyConfig(max_entries=2))

    assert "entry-count" in str(captured.value)
    assert project_fingerprint(str(path)) == GOLDEN_PCF1


def test_digest_refuses_a_header_that_overstates_the_content(tmp_path: Path) -> None:
    path = build(tmp_path, list(GOLDEN_ENTRIES), compress_type=zipfile.ZIP_STORED)
    patch_central_file_size(path, "pages/index.html", len(GOLDEN_ENTRIES[1][1]) * 3)

    with pytest.raises(UnsafeArchiveError) as captured:
        project_fingerprint(str(path))

    message = str(captured.value)
    assert RULE_SIZE_MISMATCH in message
    assert "mismatch" in message
    assert "pages/index.html" in message


def test_understated_header_cannot_stream_past_the_configured_ceilings(tmp_path: Path) -> None:
    # The header claims 64 bytes for a 256 kB stored body. Work is bounded either
    # by the D15 ceilings applied to the bytes actually streamed or by the
    # content-versus-header cross-check, and never yields a fingerprint.
    body = bytes(range(256)) * 1024
    path = build(tmp_path, [("blob.bin", body)], compress_type=zipfile.ZIP_STORED)
    patch_central_file_size(path, "blob.bin", 64)

    with pytest.raises(UnsafeArchiveError) as captured:
        project_fingerprint(str(path), config=ZipSafetyConfig(max_entry_uncompressed_bytes=4096))

    message = str(captured.value)
    assert "entry-uncompressed-size-limit" in message or RULE_SIZE_MISMATCH in message
