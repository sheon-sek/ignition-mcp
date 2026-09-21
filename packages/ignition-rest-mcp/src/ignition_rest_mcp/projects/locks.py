"""D16 writer concurrency: process-level single-writer guard (flock) and the
per-(Gateway ID, project) async lock registry with bounded acquisition."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import errno
import fcntl
import logging
import os
from pathlib import Path
from typing import AsyncIterator

from ignition_rest_mcp.errors import GatewayError
from ignition_rest_mcp.storage.paths import ensure_private_dir

LOGGER = logging.getLogger("ignition_rest_mcp.projects")

LOCK_FILENAME = "project-writer.lock"

# The flock is process-local. It prevents two ignition-rest processes sharing
# one data directory; cross-host exclusivity remains an operator obligation
# (D16: one active Project writer per Gateway) and is surfaced as a limitation
# by gateway_diagnose / setup-native doctor.
SINGLE_WRITER_LIMITATION = (
    "the single-writer guard is process-local; the operator must run at most one "
    "project-writer-enabled replica per Gateway"
)


class ProcessWriterGuard:
    """Exclusive non-blocking flock held while the writer is enabled."""

    def __init__(self, data_dir: Path) -> None:
        self._path = data_dir / LOCK_FILENAME
        self._fd: int | None = None

    @property
    def held(self) -> bool:
        return self._fd is not None

    def acquire_sync(self) -> None:
        if self._fd is not None:
            return
        ensure_private_dir(self._path.parent)
        fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            os.close(fd)
            if error.errno in {errno.EACCES, errno.EAGAIN}:
                raise GatewayError(
                    "conflict",
                    "another ignition-rest process already holds the project writer lock "
                    f"for this data directory ({SINGLE_WRITER_LIMITATION})",
                ) from error
            raise
        self._fd = fd
        try:
            os.chmod(self._path, 0o600)
        except OSError:  # pragma: no cover
            pass

    def release_sync(self) -> None:
        fd, self._fd = self._fd, None
        if fd is not None:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    async def acquire(self) -> None:
        await asyncio.to_thread(self.acquire_sync)

    async def release(self) -> None:
        await asyncio.to_thread(self.release_sync)


class _Entry:
    __slots__ = ("lock", "holders")

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.holders = 0


class ProjectLockRegistry:
    """Bounded per-(gateway, project) exclusive locks with idle removal."""

    def __init__(self, *, timeout_seconds: float, max_entries: int) -> None:
        self._timeout = timeout_seconds
        self._max = max_entries
        self._entries: dict[tuple[str, str], _Entry] = {}

    def __len__(self) -> int:
        return len(self._entries)

    @asynccontextmanager
    async def acquire(self, gateway_id: str, project: str) -> AsyncIterator[None]:
        key = (gateway_id, project)
        entry = self._entries.get(key)
        if entry is None:
            if len(self._entries) >= self._max:
                raise GatewayError(
                    "limit_exceeded",
                    f"the project writer lock registry is full (max {self._max} concurrently locked projects)",
                )
            entry = _Entry()
            self._entries[key] = entry
        entry.holders += 1
        try:
            try:
                await asyncio.wait_for(entry.lock.acquire(), self._timeout)
            except TimeoutError as error:
                raise GatewayError(
                    "conflict",
                    f"another writer currently holds the project lock for {project!r}; "
                    "the operation was not attempted",
                ) from error
            try:
                yield
            finally:
                entry.lock.release()
        finally:
            entry.holders -= 1
            if entry.holders == 0 and not entry.lock.locked():
                self._entries.pop(key, None)  # idle removal
