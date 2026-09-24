"""Validation error type and small assertion helper."""

from __future__ import annotations


class ValidationError(ValueError):
    """Project source violates the verified Designer MCP resource profile."""


# D31 section 5: a checkout made before .gitattributes keeps CRLF files, and the bundle
# tooling fails on them instead of normalizing. Name the cause and the fix in the message.
CRLF_LINE_ENDING_HINT = (
    "must use LF line endings; this checkout has CRLF files, "
    "see D31 section 5 (git rm --cached -rq . && git reset --hard)"
)


def require(condition: bool, location: object, message: str) -> None:
    if not condition:
        raise ValidationError(f"{location}: {message}")
