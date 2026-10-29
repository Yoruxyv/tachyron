# Copyright (c) 2026 Hans Valerie
"""Supervised import IPC; coordinator owns admission, catalog owns publication."""

import json
import logging
import multiprocessing
from contextlib import suppress
from multiprocessing.context import SpawnProcess
from threading import Event, Lock
from time import monotonic
from typing import cast

import psutil

from tachyron.benchmarking.models import BenchmarkError
from tachyron.imports.models import (
    CHUNK,
    FRAME_BYTES,
    TERMINAL,
    DatasetImportError,
    Metadata,
    Operation,
    Progress,
)
from tachyron.imports.storage import Catalog, load_manifest
from tachyron.imports.worker import Channel, run

logger = logging.getLogger(__name__)


class Session:
    """One request's helper and its cancellation signal; never a background queue."""

    def __init__(
        self, catalog: Catalog, identifier: str, digest: str, boundary: str
    ) -> None:
        self.catalog = catalog
        self.identifier = identifier
        self.cancelled = Event()
        self.done = Event()
        self.metadata: Metadata | None = None
        self.progress = Progress()
        self.mutex = Lock()
        self.connection: Channel | None = None
        self.process: SpawnProcess | None = None
        self._digest = digest
        self._boundary = boundary
        self._validated = False
        self.nonce = catalog.create(identifier, digest)

    def start(self) -> None:
        """Start only after the coordinator has exposed this cancellable owner."""
        if self.cancelled.is_set():
            raise DatasetImportError("import_cancelled", 409)
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe()
        self.connection = parent
        process = context.Process(
            target=run,
            args=(
                child,
                self.catalog.objects / self.identifier,
                self.nonce,
                self._digest,
                self._boundary,
                self.catalog.limits,
            ),
            name="tachyron-import",
        )
        self.process = process
        try:
            process.start()
            child.close()
            if process.pid is None:
                raise DatasetImportError("worker_unavailable", 503)
            self.catalog.helper(
                self.identifier, process.pid, psutil.Process(process.pid).create_time()
            )
            if self.cancelled.is_set():
                raise DatasetImportError("import_cancelled", 409)
            self.exchange(b"go", "locked")
        except (OSError, RuntimeError, psutil.Error) as error:
            child.close()
            raise DatasetImportError("worker_unavailable", 503) from error
        except BaseException:
            child.close()
            raise

    def exchange(self, command: bytes, expected: str) -> dict[str, object]:
        """Keep one bounded frame in flight; only validated-file progress resets time."""
        connection = self.connection
        if connection is None:
            raise DatasetImportError("worker_unavailable", 503)
        if self.cancelled.is_set():
            raise DatasetImportError("import_cancelled", 409)
        connection.send_bytes(command)
        deadline = monotonic() + self.catalog.limits.image_seconds
        while True:
            if self.cancelled.is_set():
                raise DatasetImportError("import_cancelled", 409)
            if monotonic() > deadline:
                raise DatasetImportError(
                    "validation_timeout"
                    if expected == "complete"
                    else "worker_timeout",
                    503,
                )
            if not connection.poll(0.05):
                continue
            raw = cast(
                "dict[str, object]", json.loads(connection.recv_bytes(FRAME_BYTES))
            )
            kind = raw.get("kind")
            if kind == "validated":
                self._record_validation(int(cast("int", raw["count"])))
                deadline = monotonic() + self.catalog.limits.image_seconds
            elif self._accept_reply(raw, expected, connection):
                return raw

    def _record_validation(self, count: int) -> None:
        """Only sequential acknowledgements within the workload advance validation."""
        if (
            self.metadata is None
            or count != (self.progress.validated_files or 0) + 1
            or count > len(self.metadata.files)
        ):
            raise DatasetImportError("worker_protocol", 503)
        self.progress = self.progress.model_copy(update={"validated_files": count})

    def _accept_reply(
        self, raw: dict[str, object], expected: str, connection: Channel
    ) -> bool:
        """Reserve metadata before authorizing writes; accept only the pending reply."""
        kind = raw.get("kind")
        if kind == "failure":
            raise DatasetImportError(
                str(raw["code"]),
                int(cast("int", raw["status"])),
                cast("int | None", raw.get("index")),
            )
        if kind == "metadata":
            metadata = Metadata.model_validate_json(json.dumps(raw["value"]))
            self.catalog.reserve(self.identifier, metadata)
            self.metadata = metadata
            self.progress = Progress(
                total_files=len(metadata.files), received_files=0, validated_files=0
            )
            connection.send_bytes(b"continue")
            return False
        if kind == expected:
            if kind == "complete":
                self._validated = True
            if kind == "received":
                self.progress = self.progress.model_copy(
                    update={"received_files": int(cast("int", raw["count"]))}
                )
            return True
        if kind != "validating":
            raise DatasetImportError("worker_protocol", 503)
        return False

    def feed(self, data: bytes) -> None:
        """Slice ASGI chunks so pipe writes and parser buffering stay bounded."""
        for offset in range(0, len(data), CHUNK):
            self.exchange(b"data:" + data[offset : offset + CHUNK], "received")

    def validate(self) -> str:
        """After complete-body receipt, validation survives client disconnection."""
        with self.mutex:
            operation = self.catalog.get(self.identifier)
            if operation.cancel_requested:
                raise DatasetImportError("import_cancelled", 409)
            self.catalog.update(operation.model_copy(update={"state": "VALIDATING"}))
        response = self.exchange(b"finish", "complete")
        return str(response["manifest"])

    def publish(self, document: str) -> Operation:
        """Serialize the cancellation boundary with registration publication."""
        if self.metadata is None:
            raise DatasetImportError("invalid_multipart", 400)
        manifest = load_manifest(
            document, self.catalog.objects / self.identifier / "files"
        )
        with self.mutex:
            if self.cancelled.is_set():
                raise DatasetImportError("import_cancelled", 409)
            operation = self.catalog.get(self.identifier).model_copy(
                update={"progress": self.progress}
            )
            return self.catalog.publish(operation, self.metadata, manifest)

    def cancel(self) -> Operation:
        """Persist intent; terminal receipts are always returned unchanged."""
        with self.mutex:
            try:
                operation = self.catalog.get(self.identifier)
            except DatasetImportError:
                self.cancelled.set()
                raise
            if operation.state in TERMINAL:
                return operation
            try:
                operation = self.catalog.update(
                    operation.model_copy(update={"cancel_requested": True})
                )
            finally:
                self.cancelled.set()
            return operation

    def stop(self) -> None:
        """Join only the process object this session created; never target a raw PID."""
        process = self.process
        if self.connection is not None:
            if self._validated:
                with suppress(OSError):
                    self.connection.send_bytes(b"release")
            self.connection.close()
            self.connection = None
        if process is not None and process.pid is not None:
            process.join(timeout=0.2)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
            if process.is_alive():
                process.kill()
                process.join(timeout=5)
            if process.is_alive():
                raise DatasetImportError("worker_unavailable", 503)
            process.close()
            self.process = None
        elif process is not None:
            process.close()
            self.process = None

    def close(self) -> None:
        """Stop mutation before namespace cleanup and release quota only on success."""
        try:
            self.stop()
            try:
                self.catalog.cleanup(self.identifier)
            except (OSError, DatasetImportError, BenchmarkError):
                logger.warning(
                    "Owned dataset staging retained; cleanup will retry on restart"
                )
        finally:
            self.done.set()
