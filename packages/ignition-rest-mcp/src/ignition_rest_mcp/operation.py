"""Per-operation correlation context."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import uuid


@dataclass(frozen=True, slots=True)
class OperationContext:
    correlation_id: str
    server: str
    tool: str
    actor: str
    permission_class: str
    destructive: bool
    started_at: datetime

    @classmethod
    def read(cls, tool: str, actor: str) -> "OperationContext":
        return cls(
            correlation_id=str(uuid.uuid4()),
            server="ignition-rest",
            tool=tool,
            actor=actor,
            permission_class="READ",
            destructive=False,
            started_at=datetime.now(timezone.utc),
        )
