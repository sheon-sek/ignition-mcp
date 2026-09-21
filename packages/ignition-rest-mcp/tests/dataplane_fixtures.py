"""Shared helpers for Phase 3 data-plane tests."""

from __future__ import annotations

import io
import zipfile


def make_zip_bytes(entries: dict[str, bytes] | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, body in (entries or {"project.json": b'{"title": "Demo"}'}).items():
            archive.writestr(name, body)
    return buffer.getvalue()
