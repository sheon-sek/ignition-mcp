"""The D21 change classes a bundle or Module build change is named by (D20, D21)."""

from __future__ import annotations


def parse_semver(value: str | None) -> tuple[int, int, int] | None:
    """Strict ``MAJOR.MINOR.PATCH`` parse; anything else is unparseable (``None``)."""

    if not isinstance(value, str):
        return None
    parts = value.strip().split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        return None
    if any(len(part) > 1 and part.startswith("0") for part in parts):
        return None
    return int(parts[0]), int(parts[1]), int(parts[2])


def upgrade_class(installed: str | None, target: str) -> str:
    """D21 change class: ``patch``, ``minor``, ``major`` or ``downgrade``."""

    current = parse_semver(installed)
    wanted = parse_semver(target)
    if current is None or wanted is None:
        return "major"
    if wanted < current:
        return "downgrade"
    if wanted[0] > current[0]:
        return "major"
    if wanted[1] > current[1]:
        return "minor"
    return "patch"


def needs_acknowledgement(change_class: str) -> bool:
    return change_class in ("major", "downgrade")
