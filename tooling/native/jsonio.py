"""Strict JSON loading helpers for repository artifacts."""

from __future__ import annotations

import json
from typing import Any, cast

from .validation import ValidationError


def load_json_object(data: bytes, location: str) -> dict[str, Any]:
    """Decode one strict UTF-8 JSON object, rejecting duplicate/non-JSON values."""

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate JSON key {key!r}")
            result[key] = value
        return result

    def constant(value: str) -> None:
        raise ValueError(f"non-JSON constant {value}")

    try:
        value = json.loads(
            data.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant
        )
    except (UnicodeError, ValueError) as error:
        raise ValidationError(f"{location}: invalid JSON: {error}") from error
    if type(value) is not dict:
        raise ValidationError(f"{location}: must be a JSON object")
    return cast(dict[str, Any], value)
