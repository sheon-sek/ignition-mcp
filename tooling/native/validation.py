"""Validation error type and small assertion helper."""

from __future__ import annotations


class ValidationError(ValueError):
    """Project source violates the verified Designer MCP resource profile."""


def require(condition: bool, location: object, message: str) -> None:
    if not condition:
        raise ValidationError(f"{location}: {message}")
