# Copyright (c) 2026 Hans Valerie
"""One import helper owns writes, Pillow, and the account-wide workload lock."""

import hashlib
import json
import os
from contextlib import suppress
from pathlib import Path
from typing import BinaryIO, Protocol

import psutil
from pydantic import ValidationError
from python_multipart import MultipartParser
from python_multipart.exceptions import MultipartParseError
from python_multipart.multipart import parse_options_header

from tachyron.benchmarking.models import BenchmarkError
from tachyron.benchmarking.ownership import experiment_lock
from tachyron.imports.models import (
    FRAME_BYTES,
    HEADER_BYTES,
    METADATA_BYTES,
    DatasetImportError,
    ImportLimits,
    Metadata,
    metadata_document,
)
from tachyron.imports.storage import managed_root, manifest_document
from tachyron.processing.dataset import manifest_digest, read_input
from tachyron.processing.models import (
    DEFAULT_LIMITS,
    DatasetManifest,
    ManifestEntry,
    ProcessingError,
)
from tachyron.processing.processor import validate_image


class Channel(Protocol):
    """The common bytes-only interface of Windows and POSIX multiprocessing pipes."""

    def send_bytes(self, buf: bytes) -> None: ...
    def recv_bytes(self, maxlength: int) -> bytes: ...
    def poll(self, timeout: float) -> bool: ...
    def close(self) -> None: ...


def send(connection: Channel, value: dict[str, object]) -> None:
    """Use bounded JSON rather than unpickling data from an image-handling child."""
    data = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    if len(data) > FRAME_BYTES:
        raise DatasetImportError("worker_protocol", 503)
    connection.send_bytes(data)


class Upload:
    """A strict multipart sink for a single already-owned private directory."""

    def __init__(
        self,
        connection: Channel,
        root: Path,
        digest: str,
        boundary: str,
        limits: ImportLimits,
    ) -> None:
        self.connection = connection
        self.root = root
        self.digest = digest
        self.limits = limits
        self.metadata: Metadata | None = None
        self.index = -1
        self.header_name = bytearray()
        self.header_value = bytearray()
        self.headers: dict[bytes, bytes] = {}
        self.header_bytes = 0
        self.metadata_bytes = bytearray()
        self.file: BinaryIO | None = None
        self.hasher = hashlib.sha256()
        self.file_bytes = self.total_bytes = 0
        self.complete = False
        self.entries: list[ManifestEntry] = []
        self.parser = MultipartParser(
            boundary,
            {
                "on_part_begin": self.begin,
                "on_header_field": self.header_field,
                "on_header_value": self.header_data,
                "on_header_end": self.header_end,
                "on_headers_finished": self.headers_finished,
                "on_part_data": self.data,
                "on_part_end": self.end,
                "on_end": self.ended,
            },
            max_header_count=8,
            max_header_size=HEADER_BYTES,
        )

    def begin(self) -> None:
        self.index += 1
        if self.index > self.limits.max_files:
            raise DatasetImportError("upload_limit_exceeded", 413)
        self.headers = {}
        self.header_bytes = 0

    def _header(self, target: bytearray, data: bytes, start: int, end: int) -> None:
        self.header_bytes += end - start
        if self.header_bytes > HEADER_BYTES:
            raise DatasetImportError("upload_limit_exceeded", 413)
        target.extend(data[start:end])

    def header_field(self, data: bytes, start: int, end: int) -> None:
        self._header(self.header_name, data, start, end)

    def header_data(self, data: bytes, start: int, end: int) -> None:
        self._header(self.header_value, data, start, end)

    def header_end(self) -> None:
        key = bytes(self.header_name).lower()
        if key in self.headers or key not in {b"content-disposition", b"content-type"}:
            raise DatasetImportError("invalid_multipart", 400)
        self.headers[key] = bytes(self.header_value)
        self.header_name.clear()
        self.header_value.clear()

    def headers_finished(self) -> None:
        kind, options = parse_options_header(
            self.headers.get(b"content-disposition", b"")
        )
        if kind != b"form-data":
            raise DatasetImportError("invalid_multipart", 400)
        if self.index == 0:
            if options != {b"name": b"metadata"}:
                raise DatasetImportError("invalid_multipart", 400)
            return
        metadata = self.metadata
        if metadata is None or self.index > len(metadata.files):
            raise DatasetImportError("invalid_multipart", 400)
        item = metadata.files[self.index - 1]
        if options.get(b"name") != item.part.encode() or set(options) - {
            b"name",
            b"filename",
        }:
            raise DatasetImportError("invalid_multipart", 400)
        path = self.root.joinpath(*item.relative_path.split("/"))
        if len(str(path).encode("utf-16-le")) // 2 > 240:
            raise DatasetImportError("invalid_path", index=self.index - 1)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.file = path.open("xb")
        self.file_bytes = 0
        self.hasher = hashlib.sha256()

    def data(self, data: bytes, start: int, end: int) -> None:
        part = data[start:end]
        if self.index == 0:
            if len(self.metadata_bytes) + len(part) > METADATA_BYTES:
                raise DatasetImportError("upload_limit_exceeded", 413)
            self.metadata_bytes.extend(part)
            return
        if self.metadata is None or self.file is None:
            raise DatasetImportError("invalid_multipart", 400)
        self.file_bytes += len(part)
        self.total_bytes += len(part)
        if (
            self.file_bytes > self.metadata.files[self.index - 1].size_bytes
            or self.total_bytes > self.limits.max_total_bytes
        ):
            raise DatasetImportError("upload_limit_exceeded", 413)
        self.file.write(part)
        self.hasher.update(part)

    def end(self) -> None:
        if self.index == 0:
            raw = bytes(self.metadata_bytes)
            if hashlib.sha256(raw).hexdigest() != self.digest:
                raise DatasetImportError("metadata_digest_mismatch")
            self.metadata = metadata_document(raw, self.limits)
            send(
                self.connection,
                {"kind": "metadata", "value": self.metadata.model_dump(mode="json")},
            )
            if self.connection.recv_bytes(FRAME_BYTES) != b"continue":
                raise DatasetImportError("import_cancelled", 409)
            return
        if self.metadata is None or self.file is None:
            raise DatasetImportError("invalid_multipart", 400)
        self.file.flush()
        os.fsync(self.file.fileno())
        self.file.close()
        self.file = None
        item = self.metadata.files[self.index - 1]
        digest = self.hasher.hexdigest()
        if self.file_bytes != item.size_bytes or digest != item.sha256:
            raise DatasetImportError("file_digest_mismatch", index=self.index - 1)
        self.entries.append(ManifestEntry(item.relative_path, self.file_bytes, digest))

    def ended(self) -> None:
        self.complete = True

    def finish(self) -> DatasetManifest:
        """Require complete framing, then validate exactly the received snapshot."""
        self.parser.finalize()
        if (
            not self.complete
            or self.metadata is None
            or len(self.entries) != len(self.metadata.files)
        ):
            raise DatasetImportError("invalid_multipart", 400)
        ordered = tuple(sorted(self.entries, key=lambda item: item.relative_path))
        manifest = DatasetManifest(
            self.root, ordered, manifest_digest(ordered), DEFAULT_LIMITS
        )
        for index, entry in enumerate(self.entries):
            send(self.connection, {"kind": "validating", "index": index})
            data = read_input(self.root, entry.relative_path, DEFAULT_LIMITS)
            if hashlib.sha256(data).hexdigest() != entry.sha256:
                raise DatasetImportError("input_changed", index=index)
            try:
                validate_image(data, DEFAULT_LIMITS)
            except ProcessingError as error:
                raise DatasetImportError(error.code, index=index) from error
            send(self.connection, {"kind": "validated", "count": index + 1})
        if os.name != "nt":
            for directory, _, _ in os.walk(self.root, topdown=False):
                descriptor = os.open(directory, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        return manifest

    def close(self) -> None:
        if self.file is not None:
            self.file.close()


def run(
    connection: Channel,
    directory: Path,
    nonce: str,
    digest: str,
    boundary: str,
    limits: ImportLimits,
) -> None:
    """Own all mutable files until validation ends; retain the lock until release.

    EOF never deletes files: the parent may already have committed READY. The
    helper has no database connection and receives no Python pickled payloads.
    """
    upload: Upload | None = None
    with suppress(BrokenPipeError, EOFError):
        try:
            if connection.recv_bytes(FRAME_BYTES) != b"go":
                return
            with experiment_lock():
                managed_root(directory.parent)
                directory.mkdir(mode=0o700)
                with (directory / ".owner").open("x", encoding="ascii") as stream:
                    stream.write(
                        json.dumps(
                            {
                                "nonce": nonce,
                                "operation_id": directory.name,
                                "pid": os.getpid(),
                                "born": psutil.Process().create_time(),
                            }
                        )
                    )
                    stream.flush()
                    os.fsync(stream.fileno())
                root = directory / "files"
                root.mkdir(mode=0o700)
                upload = Upload(connection, root, digest, boundary, limits)
                send(connection, {"kind": "locked"})
                while True:
                    command = connection.recv_bytes(FRAME_BYTES)
                    if command.startswith(b"data:"):
                        upload.parser.write(command[5:])
                        send(
                            connection,
                            {"kind": "received", "count": len(upload.entries)},
                        )
                    elif command == b"finish":
                        manifest = upload.finish()
                        send(
                            connection,
                            {
                                "kind": "complete",
                                "manifest": manifest_document(manifest),
                            },
                        )
                        # From this boundary onward, the helper performs no file mutations.
                        connection.recv_bytes(FRAME_BYTES)
                        return
                    else:
                        raise DatasetImportError("worker_protocol", 503)
        except DatasetImportError as error:
            send(
                connection,
                {
                    "kind": "failure",
                    "code": error.code,
                    "status": error.status,
                    "index": error.index,
                },
            )
        except BenchmarkError:
            send(
                connection, {"kind": "failure", "code": "workload_busy", "status": 409}
            )
        except (
            MultipartParseError,
            ValidationError,
            UnicodeError,
            json.JSONDecodeError,
            KeyError,
            ValueError,
        ):
            send(
                connection,
                {"kind": "failure", "code": "invalid_multipart", "status": 400},
            )
        except (OSError, ProcessingError):
            send(
                connection,
                {"kind": "failure", "code": "import_io_error", "status": 503},
            )
        finally:
            if upload is not None:
                upload.close()
            connection.close()
