"""Shared bounded SQLite foundation: WAL, busy timeout, forward-only migrations,
integrity probes. All filesystem and SQL work runs off the event loop."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
import logging
from pathlib import Path
import sqlite3
import threading
from typing import Any, Callable, Iterator, TypeVar

from ignition_rest_mcp.storage.paths import ensure_private_dir, make_private_file

LOGGER = logging.getLogger("ignition_rest_mcp.storage")

T = TypeVar("T")

BUSY_TIMEOUT_MS = 5_000


class StorageUnavailable(RuntimeError):
    """The named storage subsystem cannot currently serve requests."""

    def __init__(self, database: str) -> None:
        super().__init__(f"storage subsystem '{database}' is unavailable")
        self.database = database


class MigrationError(RuntimeError):
    """A stored schema version is ahead of this build; forward-only policy."""


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[None]:
    """One explicit bounded write transaction (the connection is autocommit)."""

    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except Exception:
        conn.rollback()
        raise
    conn.execute("COMMIT")


class Database:
    """A single SQLite file behind an async, serialized, health-tracked gateway."""

    def __init__(
        self,
        name: str,
        path: Path,
        migrations: list[str],
        *,
        synchronous: str = "NORMAL",
    ) -> None:
        self.name = name
        self.path = path
        self._migrations = migrations
        self._synchronous = synchronous
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._healthy = False
        self._detail = ""

    @property
    def healthy(self) -> bool:
        return self._healthy

    @property
    def detail(self) -> str:
        return self._detail

    async def open(self) -> None:
        await asyncio.to_thread(self._open_sync)

    def _open_sync(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._healthy = True
                return
            try:
                ensure_private_dir(self.path.parent)
                conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
                try:
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
                    conn.execute(f"PRAGMA synchronous={self._synchronous}")
                    conn.execute("PRAGMA foreign_keys=ON")
                    self._migrate(conn)
                    if str(conn.execute("PRAGMA quick_check").fetchone()[0]) != "ok":
                        raise MigrationError(f"{self.name}: integrity probe failed")
                except Exception:
                    conn.close()
                    raise
                self._conn = conn
                self._healthy = True
                self._detail = ""
                self._tighten_file_modes()
            except Exception as error:
                self._healthy = False
                self._detail = type(error).__name__
                LOGGER.error(
                    "Storage subsystem open failed",
                    extra={"event": "storage_open", "outcome": "error", "errorCode": type(error).__name__},
                )
                raise StorageUnavailable(self.name) from error

    def _migrate(self, conn: sqlite3.Connection) -> None:
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if version > len(self._migrations):
            raise MigrationError(
                f"{self.name}: stored schema version {version} is newer than this build "
                f"({len(self._migrations)} migrations); refusing to downgrade"
            )
        for index in range(version, len(self._migrations)):
            conn.executescript(self._migrations[index])
            conn.execute(f"PRAGMA user_version={index + 1}")

    @property
    def migration_count(self) -> int:
        return len(self._migrations)

    async def run(self, operation: Callable[[sqlite3.Connection], T]) -> T:
        if not self._healthy:
            raise StorageUnavailable(self.name)
        return await asyncio.to_thread(self._run_sync, operation)

    def _run_sync(self, operation: Callable[[sqlite3.Connection], T]) -> T:
        with self._lock:
            conn = self._conn
            if conn is None or not self._healthy:
                raise StorageUnavailable(self.name)
            try:
                result = operation(conn)
                self._tighten_file_modes()
                return result
            except sqlite3.Error as error:
                self._healthy = False
                self._detail = type(error).__name__
                LOGGER.error(
                    "Storage subsystem operation failed; marking unhealthy until the next probe",
                    extra={"event": "storage_failure", "outcome": "error", "errorCode": type(error).__name__},
                )
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
                raise StorageUnavailable(self.name) from error

    async def probe(self) -> bool:
        """Re-check integrity; readiness recovers automatically when this passes."""

        def _probe(conn: sqlite3.Connection) -> bool:
            return str(conn.execute("PRAGMA quick_check").fetchone()[0]) == "ok"

        if self._conn is None:
            try:
                await self.open()
                return True
            except StorageUnavailable:
                return False
        try:
            ok = await asyncio.to_thread(self._probe_locked, _probe)
        except sqlite3.Error:
            ok = False
        self._healthy = ok
        if not ok:
            self._detail = "probe-failed"
        return ok

    def _probe_locked(self, check: Callable[[sqlite3.Connection], bool]) -> bool:
        with self._lock:
            if self._conn is None:
                return False
            try:
                result = check(self._conn)
            except sqlite3.Error:
                result = False
            if result:
                self._tighten_file_modes()
            return result

    def _tighten_file_modes(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(str(self.path) + suffix)
            if candidate.exists():
                try:
                    make_private_file(candidate)
                except OSError:
                    pass

    async def close(self) -> None:
        with self._lock:
            conn, self._conn = self._conn, None
            self._healthy = False
        if conn is not None:
            await asyncio.to_thread(conn.close)


class Storage:
    """Owns every SQLite subsystem below the data directory."""

    def __init__(self, data_dir: Path, audit_synchronous: str = "FULL") -> None:
        from ignition_rest_mcp.storage.schema import AUDIT_DDL, STATE_DDL

        self.data_dir = data_dir
        self.state = Database("state", data_dir / "state.db", STATE_DDL)
        self.audit = Database("audit", data_dir / "audit.db", AUDIT_DDL, synchronous=audit_synchronous)
        self.databases: tuple[Database, ...] = (self.state, self.audit)

    async def open(self) -> None:
        for database in self.databases:
            await database.open()

    async def close(self) -> None:
        for database in self.databases:
            await database.close()

    async def probe_all(self) -> dict[str, bool]:
        results: dict[str, bool] = {}
        for database in self.databases:
            results[database.name] = await database.probe()
        return results

    def health(self) -> list[dict[str, Any]]:
        return [
            {"name": database.name, "healthy": database.healthy, "detail": database.detail}
            for database in self.databases
        ]
