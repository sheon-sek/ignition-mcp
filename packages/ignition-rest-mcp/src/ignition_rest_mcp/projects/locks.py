"""D16 writer concurrency: process-level single-writer guard (flock), the
per-(Gateway ID, project) async lock registry with bounded acquisition, and the
per-(Gateway ID, project) lock file that ``ignition-mcp setup`` shares with the
REST server (issue #80)."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import errno
import hashlib
import logging
import os
from pathlib import Path
import sys
from typing import AsyncIterator

# D16's guarantee is one active project writer per data directory. The OS-level
# primitive differs: flock(2) on POSIX, msvcrt.locking (a mandatory region lock)
# on Windows. Guarded on sys.platform, not os.name, because only sys.platform
# lets strict mypy narrow the platform-specific members.
if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

from ignition_rest_mcp.errors import GatewayError
from ignition_rest_mcp.storage.paths import ensure_private_dir

LOGGER = logging.getLogger("ignition_rest_mcp.projects")

LOCK_FILENAME = "project-writer.lock"
#: Holds one lock file per (Gateway ID, project) under the data directory.
PROJECT_LOCK_DIRNAME = "project-locks"
#: How often a registry acquisition retries a project lock file another process holds.
PROJECT_LOCK_POLL_SECONDS = 0.05

# The flock is process-local. It prevents two ignition-rest processes sharing
# one data directory; cross-host exclusivity remains an operator obligation
# (D16: one active Project writer per Gateway) and is surfaced as a limitation
# by gateway_diagnose / ignition-mcp status.
SINGLE_WRITER_LIMITATION = (
    "the single-writer guard is process-local; the operator must run at most one "
    "project-writer-enabled replica per Gateway"
)


class ProcessWriterGuard:
    """Exclusive non-blocking OS lock (flock on POSIX, msvcrt region lock on
    Windows) held while the writer is enabled."""

    def __init__(self, data_dir: Path) -> None:
        self._path = data_dir / LOCK_FILENAME
        self._fd: int | None = None

    @property
    def held(self) -> bool:
        return self._fd is not None

    def acquire_sync(self) -> None:
        if self._fd is not None:
            return
        fd = _try_lock_file(self._path)
        if fd is None:
            raise GatewayError(
                "conflict",
                "another ignition-rest process already holds the project writer lock "
                f"for this data directory ({SINGLE_WRITER_LIMITATION})",
            )
        self._fd = fd

    def release_sync(self) -> None:
        fd, self._fd = self._fd, None
        if fd is not None:
            _unlock_file(fd)

    async def acquire(self) -> None:
        await asyncio.to_thread(self.acquire_sync)

    async def release(self) -> None:
        await asyncio.to_thread(self.release_sync)


class _Entry:
    __slots__ = ("lock", "holders")

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.holders = 0


def _try_lock_file(path: Path) -> int | None:
    """Open ``path`` and take its exclusive non-blocking OS lock.

    Returns the open descriptor, or ``None`` when another descriptor holds the lock.
    """

    ensure_private_dir(path.parent)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if sys.platform == "win32":
            # Lock 1 byte at offset 0; the position stays 0 because nothing
            # reads or writes on this fd. LK_NBLCK fails with EACCES when the
            # region is already locked (EDEADLOCK comes only from LK_LOCK).
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        os.close(fd)
        if error.errno in {errno.EACCES, errno.EAGAIN}:
            return None
        raise
    try:
        os.chmod(path, 0o600)
    except OSError:  # pragma: no cover
        pass
    return fd


def _unlock_file(fd: int) -> None:
    try:
        if sys.platform == "win32":
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def project_lock_path(data_dir: Path, gateway_id: str, project: str) -> Path:
    """The lock file of one (Gateway ID, project) pair under a REST data directory.

    The name is a digest, so any Gateway ID and project name map to a safe file name.
    """

    digest = hashlib.sha256(f"{gateway_id}\0{project}".encode("utf-8")).hexdigest()[:32]
    return data_dir / PROJECT_LOCK_DIRNAME / f"{digest}.lock"


class ProjectFileLock:
    """The cross-process half of the D16 writer lock for one (Gateway ID, project).

    The REST server takes it inside :class:`ProjectLockRegistry` for each project
    Mutation, and ``ignition-mcp setup`` takes it while it replaces the managed
    project. It is held for one Mutation only, unlike :class:`ProcessWriterGuard`,
    so a running REST server does not keep ``setup`` out. It is process-local in the
    sense D16 names: it serializes writers on this machine that share the data
    directory, not writers on another host or in the Designer.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fd: int | None = None

    @property
    def held(self) -> bool:
        return self._fd is not None

    def try_acquire(self) -> bool:
        """Take the lock without waiting; ``False`` when another process holds it."""

        if self._fd is None:
            self._fd = _try_lock_file(self.path)
        return self._fd is not None

    def release(self) -> None:
        fd, self._fd = self._fd, None
        if fd is not None:
            _unlock_file(fd)


class ProjectLockRegistry:
    """Bounded per-(gateway, project) exclusive locks with idle removal.

    With ``data_dir`` set, an acquisition also takes the pair's
    :class:`ProjectFileLock` under it, so ``ignition-mcp setup`` and this server never
    write the same project at the same time.
    """

    def __init__(
        self, *, timeout_seconds: float, max_entries: int, data_dir: Path | None = None,
        poll_seconds: float = PROJECT_LOCK_POLL_SECONDS,
    ) -> None:
        self._timeout = timeout_seconds
        self._max = max_entries
        self._data_dir = data_dir
        self._poll = poll_seconds
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
                file_lock = await self._file_lock(gateway_id, project)
                try:
                    yield
                finally:
                    if file_lock is not None:
                        await asyncio.to_thread(file_lock.release)
            finally:
                entry.lock.release()
        finally:
            entry.holders -= 1
            if entry.holders == 0 and not entry.lock.locked():
                self._entries.pop(key, None)  # idle removal

    async def _file_lock(self, gateway_id: str, project: str) -> ProjectFileLock | None:
        """Take the pair's lock file within the timeout, or ``None`` without a data directory."""

        if self._data_dir is None:
            return None
        file_lock = ProjectFileLock(project_lock_path(self._data_dir, gateway_id, project))
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._timeout
        while not await asyncio.to_thread(file_lock.try_acquire):
            if loop.time() >= deadline:
                raise GatewayError(
                    "conflict",
                    f"another process, such as ignition-mcp setup, holds the project lock for {project!r}; "
                    "the operation was not attempted",
                )
            await asyncio.sleep(self._poll)
        return file_lock
