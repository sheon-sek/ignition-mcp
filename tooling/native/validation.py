"""Validation error type and small assertion helper."""

from __future__ import annotations


class ValidationError(ValueError):
    """Project source violates the verified Designer MCP resource profile."""


# D31 amendment 1: the bundle tooling reads a CRLF file as LF, so a Windows checkout
# builds the same bytes as a Linux one. A lone CR is not a line ending and still fails.
LONE_CR_HINT = "contains a carriage return that is not part of a CRLF line ending"


def to_lf(data: bytes, location: object) -> bytes:
    """Return ``data`` with CRLF line endings turned into LF; refuse a lone CR."""
    data = data.replace(b"\r\n", b"\n")
    require(b"\r" not in data, location, LONE_CR_HINT)
    return data


def require(condition: bool, location: object, message: str) -> None:
    if not condition:
        raise ValidationError(f"{location}: {message}")
