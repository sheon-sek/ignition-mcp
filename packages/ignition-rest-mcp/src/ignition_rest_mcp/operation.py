"""D18 correlation context: sortable UUIDv7 IDs and per-operation state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import secrets
import threading
import time
from typing import TYPE_CHECKING
import uuid

if TYPE_CHECKING:
    from ignition_rest_mcp.audit.sink import Auditor

_RANDOM_BITS = 74
_MAX_RANDOM = 1 << _RANDOM_BITS
_NEW_COUNTER_SPAN = 1 << 48
_RANDOM_INCREMENT_CAP = 1 << 12


class MonotonicUuid7:
    """RFC 9562 UUIDv7 via the random-increment scheme; process-monotonic, stdlib only."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_ms = 0
        self._counter = 0

    def generate(self) -> uuid.UUID:
        with self._lock:
            now_ms = time.time_ns() // 1_000_000
            if now_ms > self._last_ms:
                self._last_ms = now_ms
                self._counter = secrets.randbelow(_NEW_COUNTER_SPAN)
            else:
                self._counter += secrets.randbelow(_RANDOM_INCREMENT_CAP) + 1
                if self._counter >= _MAX_RANDOM:
                    self._last_ms += 1
                    self._counter = secrets.randbelow(_NEW_COUNTER_SPAN)
            value = (
                (self._last_ms << 80)
                | (0x7 << 76)
                | ((self._counter >> 62) << 64)
                | (0b10 << 62)
                | (self._counter & ((1 << 62) - 1))
            )
        return uuid.UUID(int=value)


_UUID7 = MonotonicUuid7()


def uuid7() -> str:
    """One server-generated sortable correlation/record identifier (D18)."""

    value = _UUID7.generate()
    if value.version != 7:  # pragma: no cover - construction guarantees version 7
        raise AssertionError("UUIDv7 construction invariant violated")
    return str(value)


def is_uuid7(value: str) -> bool:
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False
    return parsed.version == 7 and value == str(parsed)


@dataclass(slots=True)
class OperationContext:
    correlation_id: str
    server: str
    tool: str
    actor: str
    permission_class: str
    budget_class: str
    destructive: bool
    started_at: datetime
    client_request_id: str | None = None
    transaction_id: str | None = None
    auditor: "Auditor | None" = None

    @classmethod
    def start(
        cls,
        tool: str,
        actor: str,
        budget_class: str,
        permission_class: str = "READ",
        destructive: bool = False,
        server: str = "ignition-rest",
    ) -> "OperationContext":
        return cls(
            correlation_id=uuid7(),
            server=server,
            tool=tool,
            actor=actor,
            permission_class=permission_class,
            budget_class=budget_class,
            destructive=destructive,
            started_at=datetime.now(timezone.utc),
        )

    def elapsed_ms(self) -> float:
        return max(0.0, (datetime.now(timezone.utc) - self.started_at).total_seconds() * 1000.0)

    def iso_started_at(self) -> str:
        return self.started_at.isoformat()
