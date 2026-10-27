# Copyright (c) 2026 Hans Valerie
"""Versioned SQLite job history with one exclusive coordinator owner."""

import sqlite3
from pathlib import Path
from threading import RLock

from tachyron.jobs.models import Job, Metrics
from tachyron.processing.dataset import reject_link


class HistoryStore:
    """Serialize short transactions on one connection, held until shutdown.

    Exclusive SQLite locking survives transaction boundaries and prevents another
    app from recovering this owner's active jobs. OS locks release after crashes.
    """

    def __init__(self, path: Path) -> None:
        if path.exists() or path.is_symlink():
            reject_link(path.lstat())
        self._lock = RLock()
        self._connection = sqlite3.connect(path, timeout=0.25, check_same_thread=False)
        try:
            self._connection.execute("PRAGMA locking_mode=EXCLUSIVE")
            self._connection.execute("BEGIN EXCLUSIVE")
            self._migrate()
            self._connection.commit()
        except BaseException:
            self._connection.close()
            raise

    def _migrate(self) -> None:
        """Upgrade an empty v0 database atomically; never adopt unknown history."""
        version = self._connection.execute("PRAGMA user_version").fetchone()[0]
        if version == 1:
            self._connection.execute(
                "SELECT id, state, created_at, document, result, key FROM jobs LIMIT 0"
            )
            return
        if (
            version != 0
            or self._connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchone()
        ):
            raise ValueError("Unsupported history schema; migration is required")
        self._connection.execute(
            "CREATE TABLE jobs (id TEXT PRIMARY KEY, state TEXT NOT NULL, "
            "created_at TEXT NOT NULL, document TEXT NOT NULL, result TEXT, "
            "key TEXT UNIQUE)"
        )
        self._connection.execute(
            "CREATE INDEX jobs_history ON jobs(state, created_at, id)"
        )
        self._connection.execute("PRAGMA user_version=1")

    def close(self) -> None:
        """Release the connection and its exclusive ownership lock."""
        with self._lock:
            self._connection.close()

    def save(
        self, job: Job, result: Metrics | None = None, *, key: str | None = None
    ) -> None:
        """Commit state and terminal evidence together; retain earlier evidence."""
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO jobs (id, state, created_at, document, result, key) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET state=excluded.state, "
                "document=excluded.document, result=COALESCE(excluded.result, jobs.result)",
                (
                    job.id,
                    job.state,
                    job.created_at,
                    job.model_dump_json(),
                    result.model_dump_json() if result is not None else None,
                    key,
                ),
            )

    def get(self, identifier: str) -> Job | None:
        """Read a snapshot without loading the full raw result."""
        with self._lock:
            row = self._connection.execute(
                "SELECT document FROM jobs WHERE id=?", (identifier,)
            ).fetchone()
            return Job.model_validate_json(row[0]) if row else None

    def by_key(self, key: str) -> Job | None:
        """Find an accepted submission across restarts without resubmitting it."""
        with self._lock:
            row = self._connection.execute(
                "SELECT document FROM jobs WHERE key=?", (key,)
            ).fetchone()
            return Job.model_validate_json(row[0]) if row else None

    def result(self, identifier: str) -> Metrics | None:
        """Read retained raw evidence, including incomplete experiments."""
        with self._lock:
            row = self._connection.execute(
                "SELECT result FROM jobs WHERE id=?", (identifier,)
            ).fetchone()
            return Metrics.model_validate_json(row[0]) if row and row[0] else None

    def history(self, limit: int, offset: int) -> list[Job]:
        """Return bounded, stable terminal history, newest first."""
        with self._lock:
            rows = self._connection.execute(
                "SELECT document FROM jobs WHERE state IN ('COMPLETED','FAILED','CANCELLED') "
                "ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            return [Job.model_validate_json(row[0]) for row in rows]

    def recover(self, ended_at: str) -> None:
        """Fail interrupted jobs, preserving their last committed progress."""
        with self._lock, self._connection:
            rows = self._connection.execute(
                "SELECT document FROM jobs WHERE state IN ('QUEUED','RUNNING')"
            ).fetchall()
            for row in rows:
                job = Job.model_validate_json(row[0]).model_copy(
                    update={
                        "state": "FAILED",
                        "ended_at": ended_at,
                        "error_code": "server_interrupted",
                    }
                )
                self._connection.execute(
                    "UPDATE jobs SET state=?, document=? WHERE id=?",
                    (job.state, job.model_dump_json(), job.id),
                )
