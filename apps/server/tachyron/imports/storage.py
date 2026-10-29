# Copyright (c) 2026 Hans Valerie
"""Exclusive catalog ownership, immutable receipts, and transactional publication."""

import json
import os
import shutil
import sqlite3
import stat
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from threading import RLock
from time import monotonic, sleep
from typing import cast
from uuid import uuid4

import psutil
from pydantic import TypeAdapter

from tachyron.benchmarking.ownership import experiment_lock
from tachyron.imports.models import (
    METADATA_BYTES,
    TERMINAL,
    DatasetImportError,
    ImportLimits,
    Metadata,
    Operation,
    stamp,
)
from tachyron.jobs.models import DatasetInfo
from tachyron.processing.dataset import dataset_root, reject_link, validate_manifest
from tachyron.processing.models import DatasetManifest

RECEIPT_BYTES = 8192
RESERVE_BYTES = 2 * 1024 * 1024


def managed_root(path: Path) -> Path:
    """Check every existing ancestor before creating a private storage root."""
    for parent in reversed((path.absolute(), *path.absolute().parents)):
        if parent.exists() or parent.is_symlink():
            reject_link(parent.lstat())
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return dataset_root(path)


def manifest_document(manifest: DatasetManifest) -> str:
    """Encode a root-independent snapshot using the existing manifest fields."""
    return json.dumps(
        {
            "entries": [asdict(item) for item in manifest.entries],
            "sha256": manifest.sha256,
            "limits": asdict(manifest.limits),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def load_manifest(document: str, root: Path) -> DatasetManifest:
    """Validate stored evidence without rediscovering or refreshing its identity."""
    try:
        raw = cast("dict[str, object]", json.loads(document))
        manifest = TypeAdapter(DatasetManifest).validate_json(
            json.dumps({**raw, "root": str(root)}), strict=True
        )
        validate_manifest(manifest)
        return manifest
    except (ValueError, TypeError) as error:
        raise DatasetImportError("catalog_unavailable", 503) from error


def owner_document(directory: Path, nonce: str) -> dict[str, object]:
    """Read only a bounded regular ownership marker; old nonce markers allow cleanup."""
    marker = directory / ".owner"
    info = marker.lstat()
    reject_link(info)
    if not stat.S_ISREG(info.st_mode) or info.st_size > 1024:
        raise DatasetImportError("ownership_unconfirmed", 503)
    raw = marker.read_text(encoding="ascii")
    if raw == nonce:
        return {"nonce": nonce}
    try:
        result = cast("dict[str, object]", json.loads(raw))
        if not isinstance(result, dict) or result.get("nonce") != nonce:
            raise DatasetImportError("ownership_unconfirmed", 503)
        return result
    except (ValueError, TypeError) as error:
        raise DatasetImportError("ownership_unconfirmed", 503) from error


class Catalog:
    """Own one serialized SQLite connection; keep terminal-transition headroom.

    Every admission allocates a two-MiB work blob. Finalization frees that blob
    inside the same transaction before inserting a bounded manifest and receipt.
    Receipts occupy a fixed-size blob, so failure transitions need no larger row.
    """

    def __init__(self, directory: Path, limits: ImportLimits) -> None:
        self.root = managed_root(directory)
        self.limits = limits
        path = self.root / "catalog.sqlite3"
        if path.exists() or path.is_symlink():
            reject_link(path.lstat())
        if not path.exists() and any(self.root.iterdir()):
            raise ValueError("Missing dataset catalog; restore it before startup")
        self.lock = RLock()
        self.connection = sqlite3.connect(path, timeout=0.25, check_same_thread=False)
        try:
            self.connection.execute("PRAGMA foreign_keys=ON")
            self.connection.execute("PRAGMA synchronous=FULL")
            self.connection.execute("PRAGMA locking_mode=EXCLUSIVE")
            self.connection.execute("BEGIN EXCLUSIVE")
            self._migrate()
            self.connection.commit()
            self.objects = self.root / "objects"
            self.objects.mkdir(mode=0o700, exist_ok=True)
            dataset_root(self.objects)
        except BaseException:
            self.connection.close()
            raise

    def _migrate(self) -> None:
        version = self.connection.execute("PRAGMA user_version").fetchone()[0]
        if version == 1:
            self.connection.execute(
                "SELECT id, digest, state, document, nonce FROM operations LIMIT 0"
            )
            self.connection.execute(
                "SELECT id, operation, info, manifest FROM datasets LIMIT 0"
            )
            self.connection.execute(
                "SELECT operation, reserved, pid, born, headroom FROM work LIMIT 0"
            )
            return
        if (
            version != 0
            or self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchone()
        ):
            raise ValueError("Unsupported dataset catalog schema")
        self.connection.executescript(
            "BEGIN EXCLUSIVE;CREATE TABLE operations (id TEXT PRIMARY KEY, digest TEXT NOT NULL, state TEXT NOT NULL, "
            "document BLOB NOT NULL CHECK(length(document)=8192), nonce TEXT NOT NULL);"
            "CREATE UNIQUE INDEX one_import ON operations((1)) WHERE state IN ('RECEIVING','VALIDATING');"
            "CREATE TABLE datasets (id TEXT PRIMARY KEY, operation TEXT NOT NULL UNIQUE REFERENCES operations(id), "
            "info TEXT NOT NULL, manifest TEXT NOT NULL);"
            "CREATE TABLE work (operation TEXT PRIMARY KEY REFERENCES operations(id), reserved INTEGER NOT NULL, "
            "pid INTEGER, born REAL, headroom BLOB NOT NULL);"
            "CREATE TRIGGER immutable_receipt BEFORE UPDATE ON operations WHEN OLD.state IN ('READY','FAILED','CANCELLED') "
            "BEGIN SELECT RAISE(ABORT,'immutable receipt'); END;"
            "CREATE TRIGGER immutable_dataset BEFORE UPDATE ON datasets BEGIN SELECT RAISE(ABORT,'immutable dataset'); END;"
            "PRAGMA user_version=1;COMMIT;"
        )

    @contextmanager
    def transaction(self) -> Generator[None]:
        """Translate catalog faults without misreporting them as history failures."""
        with self.lock:
            outer = not self.connection.in_transaction
            try:
                if outer:
                    self.connection.execute("BEGIN")
                yield
                if outer:
                    self.connection.commit()
            except sqlite3.Error as error:
                if outer:
                    self.connection.rollback()
                raise DatasetImportError("catalog_unavailable", 503) from error
            except DatasetImportError:
                if outer:
                    self.connection.rollback()
                raise
            except (ValueError, TypeError, KeyError) as error:
                if outer:
                    self.connection.rollback()
                raise DatasetImportError("catalog_unavailable", 503) from error
            except BaseException:
                if outer:
                    self.connection.rollback()
                raise

    def _save(self, operation: Operation) -> None:
        data = operation.model_dump_json().encode()
        if len(data) > RECEIPT_BYTES:
            raise DatasetImportError("catalog_unavailable", 503)
        self.connection.execute(
            "UPDATE operations SET state=?,document=? WHERE id=?",
            (operation.state, data.ljust(RECEIPT_BYTES, b" "), operation.operation_id),
        )

    def get(self, identifier: str) -> Operation:
        """Read authoritative state; a read fault never becomes a missing dataset."""
        with self.transaction():
            row = self.connection.execute(
                "SELECT document FROM operations WHERE id=?", (identifier,)
            ).fetchone()
            if row is None:
                raise DatasetImportError("import_not_found", 404)
            return Operation.model_validate_json(row[0])

    def replay(self, identifier: str, digest: str) -> Operation | None:
        """Bind every replay to its original metadata, including terminal failures."""
        with self.transaction():
            row = self.connection.execute(
                "SELECT digest,document FROM operations WHERE id=?", (identifier,)
            ).fetchone()
            if row is None:
                return None
            if row[0] != digest:
                raise DatasetImportError("idempotency_conflict", 409)
            return Operation.model_validate_json(row[1])

    def create(self, identifier: str, digest: str) -> str:
        """Reserve permanent receipt and finalization capacity before accepting bytes."""
        with self.transaction():
            size = self.connection.execute("PRAGMA page_size").fetchone()[0]
            used = (
                self.connection.execute("PRAGMA page_count").fetchone()[0]
                - self.connection.execute("PRAGMA freelist_count").fetchone()[0]
            ) * size
            if used + RESERVE_BYTES + 1024 * 1024 > self.limits.catalog_bytes:
                raise DatasetImportError("catalog_capacity", 507)
            self.connection.execute(
                f"PRAGMA max_page_count={self.limits.catalog_bytes // size}"
            )
            operation = Operation(operation_id=identifier, created_at=stamp())
            nonce = uuid4().hex
            self.connection.execute(
                "INSERT INTO operations VALUES (?,?,?,?,?)",
                (
                    identifier,
                    digest,
                    operation.state,
                    operation.model_dump_json().encode().ljust(RECEIPT_BYTES, b" "),
                    nonce,
                ),
            )
            self.connection.execute(
                "INSERT INTO work VALUES (?,0,NULL,NULL,zeroblob(?))",
                (identifier, RESERVE_BYTES),
            )
            return nonce

    def helper(self, identifier: str, pid: int, born: float) -> None:
        """Persist helper identity before authorizing any native or filesystem work."""
        with self.transaction():
            self.connection.execute(
                "UPDATE work SET pid=?,born=? WHERE operation=?",
                (pid, born, identifier),
            )

    def reserve(self, identifier: str, metadata: Metadata) -> None:
        """Charge unfinished files and enforce capacity without evicting registrations."""
        with self.transaction():
            count, total = self.connection.execute(
                "SELECT count(*),coalesce(sum(json_extract(info,'$.total_bytes')),0) FROM datasets"
            ).fetchone()
            reserved = self.connection.execute(
                "SELECT coalesce(sum(reserved),0) FROM work"
            ).fetchone()[0]
            amount = sum(item.size_bytes for item in metadata.files)
            if (
                count >= self.limits.registrations
                or total + reserved + amount > self.limits.storage_bytes
            ):
                raise DatasetImportError("dataset_capacity", 507)
            if shutil.disk_usage(self.root).free < amount + RESERVE_BYTES * 2:
                raise DatasetImportError("insufficient_storage", 507)
            self.connection.execute(
                "UPDATE work SET reserved=? WHERE operation=?", (amount, identifier)
            )

    def update(self, operation: Operation) -> Operation:
        """Update active state only; callers serialize cancellation and publication."""
        with self.transaction():
            current = self.get(operation.operation_id)
            if current.state in TERMINAL:
                return current
            self._save(operation)
            return operation

    def publish(
        self, operation: Operation, metadata: Metadata, manifest: DatasetManifest
    ) -> Operation:
        """Commit the registration and READY receipt atomically."""
        document = manifest_document(manifest)
        if len(document.encode()) > METADATA_BYTES:
            raise DatasetImportError("upload_limit_exceeded", 413)
        info = DatasetInfo(
            dataset_id="import-" + operation.operation_id.replace("-", ""),
            manifest_sha256=manifest.sha256,
            input_count=len(manifest.entries),
            total_bytes=manifest.total_bytes,
            display_name=metadata.display_name,
            source="import",
            selection_mode=metadata.selection.mode,
            excluded_count=metadata.selection.excluded_count,
        )
        with self.transaction():
            current = self.get(operation.operation_id)
            if current.state in TERMINAL or current.cancel_requested:
                raise DatasetImportError("import_cancelled", 409)
            self.connection.execute(
                "UPDATE work SET headroom=zeroblob(0),reserved=0 WHERE operation=?",
                (operation.operation_id,),
            )
            self.connection.execute(
                "INSERT INTO datasets VALUES (?,?,?,?)",
                (
                    info.dataset_id,
                    operation.operation_id,
                    info.model_dump_json(),
                    document,
                ),
            )
            ready = operation.model_copy(
                update={"state": "READY", "ended_at": stamp(), "dataset": info}
            )
            self._save(ready)
            return ready

    def finish(
        self, identifier: str, code: str, index: int | None = None
    ) -> Operation | None:
        """Freeze a failure/cancellation without ever demoting a committed dataset."""
        with self.transaction():
            if (
                self.connection.execute(
                    "SELECT id FROM operations WHERE id=?", (identifier,)
                ).fetchone()
                is None
            ):
                return None
            current = self.get(identifier)
            if current.state in TERMINAL:
                return current
            self.connection.execute(
                "UPDATE work SET headroom=zeroblob(0) WHERE operation=?", (identifier,)
            )
            final = current.model_copy(
                update={
                    "state": "CANCELLED" if current.cancel_requested else "FAILED",
                    "ended_at": stamp(),
                    "error_code": code,
                    "file_index": index,
                }
            )
            self._save(final)
            return final

    def datasets(self) -> list[DatasetInfo]:
        """Read registrations directly; no managed registry cache is authoritative."""
        with self.transaction():
            return [
                DatasetInfo.model_validate_json(row[0])
                for row in self.connection.execute(
                    "SELECT info FROM datasets ORDER BY id"
                )
            ]

    def manifest(self, identifier: str) -> DatasetManifest | None:
        """Resolve the saved snapshot, never a fresh discovery of changed files."""
        with self.transaction():
            row = self.connection.execute(
                "SELECT operation,manifest FROM datasets WHERE id=?", (identifier,)
            ).fetchone()
            if row is None:
                return None
            return load_manifest(row[1], self.objects / row[0] / "files")

    def cleanup(self, identifier: str) -> None:
        """Remove only a failed operation's proven namespace, under workload exclusion."""
        with self.transaction():
            operation = self.get(identifier)
            nonce = self.connection.execute(
                "SELECT nonce FROM operations WHERE id=?", (identifier,)
            ).fetchone()[0]
        if operation.state == "READY":
            with self.transaction():
                self.connection.execute(
                    "DELETE FROM work WHERE operation=?", (identifier,)
                )
            return
        if operation.state not in TERMINAL:
            raise DatasetImportError("ownership_unconfirmed", 503)
        with experiment_lock():
            dataset_root(self.root)
            dataset_root(self.objects)
            directory = self.objects / identifier
            if directory.exists() or directory.is_symlink():
                dataset_root(directory)
                owner_document(directory, nonce)
                # Validate every descendant before recursive deletion, including junctions.
                for parent, directories, files in os.walk(directory, followlinks=False):
                    for name in (*directories, *files):
                        reject_link((Path(parent) / name).lstat())
                shutil.rmtree(directory)
        with self.transaction():
            self.connection.execute("DELETE FROM work WHERE operation=?", (identifier,))

    def recover(self) -> None:
        """Reap verified helpers before failing unfinished operations and cleaning."""
        with self.transaction():
            rows = self.connection.execute(
                "SELECT operation,pid,born,nonce FROM work JOIN operations ON operations.id=work.operation"
            ).fetchall()
        for identifier, pid, born, nonce in rows:
            if pid is not None:
                try:
                    process = psutil.Process(pid)
                    if process.create_time() == born:
                        self._reap(process, identifier, nonce, born)
                except psutil.NoSuchProcess:
                    pass
            self.finish(identifier, "server_interrupted")
            self.cleanup(identifier)

    def _reap(
        self, process: psutil.Process, identifier: str, nonce: str, born: float
    ) -> None:
        """Kill only a helper corroborated by its private marker and durable identity."""
        directory = self.objects / identifier
        try:
            dataset_root(directory)
            owner = owner_document(directory, nonce)
        except FileNotFoundError:
            # Before `go` the child has no marker and exits on the dead parent's pipe.
            owner = {}
        try:
            if (
                owner.get("operation_id") == identifier
                and owner.get("pid") == process.pid
                and owner.get("born") == born
                and "--multiprocessing-fork" in process.cmdline()
            ):
                process.kill()
            deadline = monotonic() + 5
            # Observe exit without stealing multiprocessing's POSIX wait status.
            while process.is_running() and process.status() != psutil.STATUS_ZOMBIE:
                if monotonic() > deadline:
                    raise DatasetImportError("ownership_unconfirmed", 503)
                sleep(0.05)
        except psutil.AccessDenied as error:
            raise DatasetImportError("ownership_unconfirmed", 503) from error

    def close(self) -> None:
        """Release catalog ownership after every helper has stopped."""
        with self.lock:
            self.connection.close()
