"""Slice 3 (Phase 3 / G3): D15 ZIP safety validator — every rejection class.

Fixtures are written to real files under ``tmp_path`` (the validator takes a
path, exactly like the D17 artifact-validator contract) and each archive
carries exactly one violation, so a refusal always names the rule under test.
"""

from __future__ import annotations

import stat
import struct
from pathlib import Path
import warnings
import zipfile

import pytest

from ignition_rest_mcp.errors import GatewayError
from ignition_rest_mcp.projects.zip_safety import (
    ENTRY_NAME_ECHO_LIMIT,
    UnsafeArchiveError,
    ZipSafetyConfig,
    ZipSummary,
    validate_zip_file,
)

FIXED_TIME = (2024, 5, 17, 12, 0, 0)
BENIGN_BODY = b'{"project":"demo","version":"8.1.43"}\n'
TAGSET_BODY = b'{"name":"default","tags":[{"name":"flow","value":12}]}\n'
CENTRAL_SIGNATURE = b"PK\x01\x02"
UTF8_NAME_FLAG = 0x800
ENCRYPTED_FLAG = 0x01


class CraftedInfo(zipfile.ZipInfo):
    """A header whose stored filename bytes and general-purpose flags are fixed.

    ``zipfile`` recomputes both while opening an entry for writing — it clears the
    UTF-8 name flag and the encryption bit and encodes the name with its own
    ASCII-then-UTF-8 rule — so hostile fixtures that Python would never write are
    supplied through ``_encodeFilenameFlags`` instead. The writer asks each
    instance that one question for the local header and for the central-directory
    record alike, which keeps the two records byte-identical (the stdlib reader
    refuses an archive whose pair disagrees).
    """

    stored_name: bytes | None = None
    stored_flags: int | None = None

    def _encodeFilenameFlags(self) -> tuple[bytes, int]:
        if self.stored_name is None and self.stored_flags is None:
            return super()._encodeFilenameFlags()
        if self.stored_name is None:
            name_bytes = super()._encodeFilenameFlags()[0]
        else:
            name_bytes = self.stored_name
        flags = self.flag_bits if self.stored_flags is None else self.stored_flags
        return name_bytes, flags


def entry(
    name: str,
    *,
    compress_type: int = zipfile.ZIP_STORED,
    external_attr: int | None = None,
    create_system: int = 0,
    name_bytes: bytes | None = None,
    flags: int | None = None,
) -> zipfile.ZipInfo:
    """Build one entry header; payload is supplied by :func:`build`/:func:`one`."""

    info = CraftedInfo(name, date_time=FIXED_TIME)
    info.compress_type = compress_type
    info.create_system = create_system
    info.stored_name = name_bytes
    info.stored_flags = flags
    if external_attr is not None:
        info.external_attr = external_attr
    return info


def build(tmp_path: Path, entries: list[tuple[zipfile.ZipInfo, bytes]], name: str = "archive.zip") -> Path:
    path = tmp_path / name
    with warnings.catch_warnings():
        # ``zipfile`` warns when a name repeats; the archive is still written,
        # which is exactly the hostile shape under test.
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(path, "w") as output:
            for info, payload in entries:
                output.writestr(info, payload)
    return path


def one(tmp_path: Path, info: zipfile.ZipInfo, payload: bytes = b"payload") -> Path:
    return build(tmp_path, [(info, payload)])


def refusal(path: Path, config: ZipSafetyConfig | None = None) -> str:
    """The message of the required refusal, or a hard test failure."""

    with pytest.raises(UnsafeArchiveError) as captured:
        validate_zip_file(str(path), config)
    return str(captured.value)


def patch_central_sizes(
    path: Path,
    name: str,
    *,
    file_size: int | None = None,
    compress_size: int | None = None,
) -> None:
    """Rewrite one central-directory size field, leaving the stored body intact.

    This is how real bombs and lying exports look: the header metadata disagrees
    with the entry data that follows it.
    """

    buffer = bytearray(path.read_bytes())
    needle = name.encode("utf-8")
    cursor = 0
    while True:
        start = buffer.find(CENTRAL_SIGNATURE, cursor)
        assert start >= 0, "central directory record not found"
        name_len, extra_len, comment_len = struct.unpack_from("<HHH", buffer, start + 28)
        if bytes(buffer[start + 46 : start + 46 + name_len]) == needle:
            if compress_size is not None:
                struct.pack_into("<I", buffer, start + 20, compress_size)
            if file_size is not None:
                struct.pack_into("<I", buffer, start + 24, file_size)
            path.write_bytes(bytes(buffer))
            return
        cursor = start + 46 + name_len + extra_len + comment_len


# --- acceptance ------------------------------------------------------------


def test_benign_project_archive_is_accepted_with_central_directory_totals(tmp_path: Path) -> None:
    path = build(
        tmp_path,
        [
            (entry("project.json"), BENIGN_BODY),
            (entry("tags/"), b""),
            (entry("tags/default/tagset.json"), TAGSET_BODY),
        ],
    )

    summary = validate_zip_file(str(path))

    # Directory markers are counted and contribute zero bytes; stored entries
    # compress to nothing, so both totals are the sum of the payloads.
    assert summary == ZipSummary(
        entries=3,
        total_uncompressed=len(BENIGN_BODY) + len(TAGSET_BODY),
        total_compressed=len(BENIGN_BODY) + len(TAGSET_BODY),
    )


def test_caller_supplied_ceilings_are_honoured(tmp_path: Path) -> None:
    path = build(tmp_path, [(entry("project.json"), BENIGN_BODY)])

    assert validate_zip_file(str(path), ZipSafetyConfig(max_entries=1)).entries == 1


# --- rule 1: not a readable ZIP --------------------------------------------


def test_non_archive_bytes_are_refused(tmp_path: Path) -> None:
    path = tmp_path / "not-a-zip.bin"
    path.write_bytes(b"PK\x03\x04 this is not a central directory")

    assert "readable-zip" in refusal(path)


def test_empty_central_directory_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "empty.zip"
    zipfile.ZipFile(path, "w").close()

    assert "non-empty-central-directory" in refusal(path)


def test_missing_archive_file_is_refused(tmp_path: Path) -> None:
    assert "readable-zip" in refusal(tmp_path / "absent.zip")


# --- rule 2: entry name shape ----------------------------------------------


def test_absolute_entry_name_is_refused(tmp_path: Path) -> None:
    message = refusal(one(tmp_path, entry("/etc/passwd")))

    assert "absolute-entry-name" in message
    assert "/etc/passwd" in message


def test_root_only_directory_marker_is_refused(tmp_path: Path) -> None:
    assert "absolute-entry-name" in refusal(one(tmp_path, entry("/")))


def test_parent_directory_entry_name_is_refused(tmp_path: Path) -> None:
    message = refusal(one(tmp_path, entry("tags/../../etc/passwd")))

    assert "path-traversal-entry-name" in message
    assert "path segment '..'" in message


def test_dot_segment_entry_name_is_refused(tmp_path: Path) -> None:
    message = refusal(one(tmp_path, entry("./project.json")))

    assert "path-traversal-entry-name" in message
    assert "segment '.'" in message


def test_empty_path_segment_is_refused(tmp_path: Path) -> None:
    message = refusal(one(tmp_path, entry("tags//tagset.json")))

    assert "path-traversal-entry-name" in message
    assert "path segment ''" in message


def test_double_trailing_slash_is_refused(tmp_path: Path) -> None:
    assert "path-traversal-entry-name" in refusal(one(tmp_path, entry("tags//")))


def test_backslash_entry_name_is_refused(tmp_path: Path) -> None:
    assert "backslash-entry-name" in refusal(one(tmp_path, entry("tags\\..\\windows\\win.ini")))


def test_drive_letter_entry_name_is_refused(tmp_path: Path) -> None:
    assert "drive-letter-entry-name" in refusal(one(tmp_path, entry("C:/Windows/win.ini")))


def test_zero_length_entry_name_is_refused(tmp_path: Path) -> None:
    assert "empty-entry-name" in refusal(one(tmp_path, entry("")))


def test_high_byte_entry_name_without_utf8_flag_is_refused(tmp_path: Path) -> None:
    path = one(tmp_path, entry("caf\u00e9-note.txt", name_bytes=b"caf\xe9-note.txt", flags=0))

    assert "ascii-entry-name-without-utf8-flag" in refusal(path)


def test_invalid_utf8_entry_name_with_utf8_flag_is_refused(tmp_path: Path) -> None:
    path = one(tmp_path, entry("bad.txt", name_bytes=b"\xff\xfebad.txt", flags=UTF8_NAME_FLAG))

    assert "utf8-entry-name" in refusal(path)


def test_valid_utf8_entry_name_is_accepted_whether_flagged_or_ascii(tmp_path: Path) -> None:
    accented = one(tmp_path, entry("café-note.txt", name_bytes="café-note.txt".encode("utf-8"), flags=UTF8_NAME_FLAG))
    plain = one(tmp_path, entry("note.txt"))

    assert validate_zip_file(str(accented)).entries == 1
    assert validate_zip_file(str(plain)).entries == 1


# --- rule 3: non-regular entry types ---------------------------------------


def test_symlink_entry_is_refused(tmp_path: Path) -> None:
    path = one(
        tmp_path,
        entry("link.txt", create_system=3, external_attr=(stat.S_IFLNK | 0o777) << 16),
        payload=b"/etc/passwd",
    )

    message = refusal(path)

    assert "regular-file-entry" in message
    assert "symbolic link" in message


@pytest.mark.parametrize(
    ("fragment", "type_bits"),
    [
        ("device", stat.S_IFCHR),
        ("device", stat.S_IFBLK),
        ("FIFO", stat.S_IFIFO),
        ("socket", stat.S_IFSOCK),
    ],
)
def test_non_regular_entry_type_is_refused(tmp_path: Path, fragment: str, type_bits: int) -> None:
    path = one(
        tmp_path,
        entry("node", create_system=3, external_attr=(type_bits | 0o600) << 16),
        payload=b"0",
    )

    message = refusal(path)

    assert "regular-file-entry" in message
    assert fragment in message


def test_directory_entry_carrying_data_is_refused(tmp_path: Path) -> None:
    message = refusal(one(tmp_path, entry("tags/"), payload=TAGSET_BODY))

    assert "directory-entry-without-data" in message
    assert "tags/" in message


# --- rule 4: duplicate names -----------------------------------------------


def test_exact_duplicate_entry_name_is_refused(tmp_path: Path) -> None:
    path = build(
        tmp_path,
        [(entry("project.json"), BENIGN_BODY), (entry("project.json"), b'{"project":"override"}\n')],
    )

    message = refusal(path)

    assert "unique-entry-name" in message
    assert "identical name" in message


def test_nfc_nfd_duplicate_entry_names_are_refused(tmp_path: Path) -> None:
    composed = entry("tags/caf\u00e9.json")
    decomposed = entry("tags/cafe\u0301.json")
    assert composed.filename != decomposed.filename

    message = refusal(build(tmp_path, [(composed, b"{}"), (decomposed, b"{}")]))

    assert "nfc-unique-entry-name" in message


def test_case_folded_duplicate_entry_names_are_refused(tmp_path: Path) -> None:
    path = build(tmp_path, [(entry("Readme.txt"), b"a"), (entry("README.TXT"), b"b")])

    assert "casefold-unique-entry-name" in refusal(path)


# --- rule 5: encryption -----------------------------------------------------


def test_encrypted_entry_is_refused(tmp_path: Path) -> None:
    path = one(tmp_path, entry("secret.json", flags=ENCRYPTED_FLAG))

    assert "unencrypted-entry" in refusal(path)


def test_stored_general_purpose_flags_without_the_encryption_bit_are_accepted(tmp_path: Path) -> None:
    assert validate_zip_file(str(one(tmp_path, entry("note.txt", flags=0x04)))).entries == 1


# --- rules 6-8: resource ceilings ------------------------------------------


def test_entry_count_above_ceiling_is_refused(tmp_path: Path) -> None:
    path = build(tmp_path, [(entry("a.json"), b"1"), (entry("b.json"), b"2"), (entry("c.json"), b"3")])

    message = refusal(path, ZipSafetyConfig(max_entries=2))

    assert "entry-count" in message
    assert "3 entries" in message


def test_entry_larger_than_per_entry_ceiling_is_refused(tmp_path: Path) -> None:
    path = one(tmp_path, entry("big.json"), payload=b"0" * 64)

    message = refusal(path, ZipSafetyConfig(max_entry_uncompressed_bytes=32))

    assert "entry-uncompressed-size-limit" in message
    assert "exceed the limit of 32" in message


def test_cumulative_expanded_size_above_ceiling_is_refused(tmp_path: Path) -> None:
    path = build(tmp_path, [(entry("a.json"), b"0" * 40), (entry("b.json"), b"1" * 40)])

    message = refusal(path, ZipSafetyConfig(max_total_uncompressed_bytes=64))

    assert "total-uncompressed-size-limit" in message
    assert "b.json" in message


def test_deflated_compression_bomb_is_refused(tmp_path: Path) -> None:
    # 300 kB of one repeated byte deflates to a few hundred bytes: far past the
    # 100x per-entry expansion ceiling while staying inside every size ceiling.
    path = one(tmp_path, entry("bomb.txt", compress_type=zipfile.ZIP_DEFLATED), payload=b"a" * 300_000)

    message = refusal(path)

    assert "entry-compression-ratio-limit" in message
    assert "expands" in message


def test_stored_entries_are_exempt_from_the_expansion_ceiling(tmp_path: Path) -> None:
    path = one(tmp_path, entry("plain.txt"), payload=b"a" * 300_000)

    summary = validate_zip_file(str(path))

    assert summary.total_uncompressed == 300_000
    assert summary.total_compressed == 300_000


def test_zero_length_deflated_entry_is_accepted(tmp_path: Path) -> None:
    # A deflated empty file declares no uncompressed bytes while still carrying a
    # couple of compressed ones: the expansion ceiling divides by the uncompressed
    # size, so that shape has to be skipped rather than raise.
    path = one(tmp_path, entry("empty.json", compress_type=zipfile.ZIP_DEFLATED), payload=b"")

    summary = validate_zip_file(str(path))

    assert summary.entries == 1
    assert summary.total_uncompressed == 0
    assert summary.total_compressed > 0


def test_archive_wide_expansion_ceiling_is_refused(tmp_path: Path) -> None:
    path = one(tmp_path, entry("bomb.txt", compress_type=zipfile.ZIP_DEFLATED), payload=b"a" * 300_000)

    message = refusal(path, ZipSafetyConfig(min_compression_ratio=0.0, max_ratio_total=2.0))

    assert "total-compression-ratio-limit" in message


def test_deflated_entry_declaring_no_compressed_size_is_refused(tmp_path: Path) -> None:
    path = one(tmp_path, entry("tricky.txt", compress_type=zipfile.ZIP_DEFLATED), payload=b"abcdefgh" * 16)
    patch_central_sizes(path, "tricky.txt", compress_size=0)

    assert "compressed-size-declared" in refusal(path)


# --- refusal contract -------------------------------------------------------


def test_refusals_are_invalid_argument_gateway_errors(tmp_path: Path) -> None:
    error = UnsafeArchiveError("boom")

    assert isinstance(error, GatewayError)
    assert error.code == "invalid_argument"
    assert str(error) == "invalid_argument: boom"

    with pytest.raises(GatewayError) as captured:
        validate_zip_file(str(one(tmp_path, entry("/etc/passwd"))))
    assert captured.value.code == "invalid_argument"


def test_hostile_entry_name_is_truncated_in_the_refusal_message(tmp_path: Path) -> None:
    hostile = "/" + ("payload/" + "x" * 60) * 12 + "shadow"
    path = one(tmp_path, entry(hostile))

    message = refusal(path)

    assert hostile[: ENTRY_NAME_ECHO_LIMIT] in message
    assert "…" in message
    assert "shadow" not in message
