"""One planned intention, shared by ``plan`` and ``apply`` (D20).

``plan`` derives a list of these from the read-only observations and prints them;
``apply`` consumes the same list, refuses to write anything while a line is
``BLOCKED``, and executes the remaining lines in the order they appear.
"""

from __future__ import annotations

from dataclasses import dataclass

CREATE = "CREATE"
UPDATE = "UPDATE"
NO_CHANGE = "NO CHANGE"
BLOCKED = "BLOCKED"
#: Detect-only lines carry no intention; ``SKIP`` marks them as out of scope here.
SKIPPED = "SKIP"

ACKNOWLEDGEMENT = "requires explicit acknowledgement in apply"


@dataclass(frozen=True, slots=True)
class Action:
    """One planned intention."""

    action: str
    kind: str
    name: str
    reason: str

    def as_dict(self) -> dict[str, str]:
        return {"action": self.action, "kind": self.kind, "name": self.name, "reason": self.reason}

    def as_line(self) -> str:
        return f"{self.action} {self.kind} {self.name}: {self.reason}"


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
