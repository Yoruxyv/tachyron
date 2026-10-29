# Copyright (c) 2026 Hans Valerie
"""Bounded import documents and portable names, without HTTP dependencies."""

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tachyron.jobs.models import DatasetInfo
from tachyron.processing.dataset import validate_relative
from tachyron.processing.models import DEFAULT_LIMITS, ProcessingError

CHUNK = 64 * 1024
METADATA_BYTES = 512 * 1024
FRAME_BYTES = 1024 * 1024
HEADER_BYTES = 8192
TERMINAL = ("READY", "FAILED", "CANCELLED")
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


def stamp() -> str:
    """Return a portable UTC lifecycle timestamp."""
    return datetime.now(UTC).isoformat()


class DatasetImportError(ValueError):
    """A stable public code; decoder and operating-system details stay private."""

    def __init__(self, code: str, status: int = 422, index: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.index = index


@dataclass(frozen=True)
class ImportLimits:
    """Operational budgets, independently bounded from image acceptance."""

    max_files: int = DEFAULT_LIMITS.max_files
    max_file_bytes: int = DEFAULT_LIMITS.max_file_bytes
    max_total_bytes: int = DEFAULT_LIMITS.max_total_bytes
    max_body_bytes: int = 521 * 1024 * 1024
    registrations: int = 32
    storage_bytes: int = 4 * 1024 * 1024 * 1024
    catalog_bytes: int = 64 * 1024 * 1024
    inactivity_seconds: float = 30.0
    receive_seconds: float = 600.0
    image_seconds: float = 60.0


class Document(BaseModel):
    """Strict, immutable boundary documents."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class FileSpec(Document):
    """One exact compressed input; multipart filenames are ignored."""

    part: str
    relative_path: str = Field(min_length=1, max_length=240)
    size_bytes: int = Field(ge=1, le=DEFAULT_LIMITS.max_file_bytes)
    sha256: Digest


class Selection(Document):
    """Browser-declared selection intent, never a verified source inventory."""

    mode: Literal["whole-folder", "explicit-subset"] = "whole-folder"
    excluded_count: int = Field(default=0, ge=0, le=10_000)

    @model_validator(mode="after")
    def consistent(self) -> "Selection":
        """Require explicit subset intent when exclusions are declared."""
        if (self.mode == "whole-folder") != (self.excluded_count == 0):
            raise ValueError("Inconsistent selection")
        return self


class Metadata(Document):
    """An exact selected workload, bound to the operation by its raw digest."""

    schema_version: Literal[1]
    display_name: str = Field(min_length=1, max_length=80)
    selection: Selection
    files: tuple[FileSpec, ...] = Field(
        min_length=1, max_length=DEFAULT_LIMITS.max_files
    )

    def check(self, limits: ImportLimits) -> None:
        """Reject excessive or ambiguous trees before writing any file."""
        if any(unicodedata.category(c).startswith("C") for c in self.display_name):
            raise DatasetImportError("invalid_request")
        if (
            len(self.files) > limits.max_files
            or sum(item.size_bytes for item in self.files) > limits.max_total_bytes
        ):
            raise DatasetImportError("upload_limit_exceeded", 413)
        nodes: dict[str, tuple[str, bool]] = {}
        for index, item in enumerate(self.files):
            if item.part != f"file-{index}" or item.size_bytes > limits.max_file_bytes:
                raise DatasetImportError("invalid_request", index=index)
            _register_path(nodes, item.relative_path, index)
        if len(nodes) > DEFAULT_LIMITS.max_entries:
            raise DatasetImportError("upload_limit_exceeded", 413)


def _register_path(
    nodes: dict[str, tuple[str, bool]], relative_path: str, index: int
) -> None:
    """Reject file/directory and portable-name collisions across every prefix."""
    parts = portable_parts(relative_path)
    if len(parts) - 1 > DEFAULT_LIMITS.max_depth:
        raise DatasetImportError("invalid_path", index=index)
    for depth in range(1, len(parts) + 1):
        name = "/".join(parts[:depth])
        key = unicodedata.normalize("NFC", name).casefold()
        node = (name, depth == len(parts))
        previous = nodes.get(key)
        if previous is not None and (previous != node or node[1]):
            raise DatasetImportError("path_conflict", index=index)
        nodes[key] = node


def portable_parts(name: str) -> tuple[str, ...]:
    """Preserve accepted names while rejecting Windows/Unicode ambiguities."""
    try:
        parts = validate_relative(name)
    except ProcessingError as error:
        raise DatasetImportError("invalid_path") from error
    for part in parts:
        device = part.split(".")[0].rstrip(" ").upper()
        if (
            len(part.encode("utf-8")) > 240
            or part.endswith((".", " "))
            or any(
                c in '<>"|?*' or unicodedata.category(c).startswith("C") for c in part
            )
            or device in {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
            or re.fullmatch(r"(?:COM|LPT)[1-9¹²³]", device)
        ):
            raise DatasetImportError("invalid_path")
    return parts


class Progress(Document):
    """Known counts; null denotes lost/unconfirmed progress."""

    total_files: int | None = None
    received_files: int | None = None
    validated_files: int | None = None


class Operation(Document):
    """Durable operation receipt; terminal documents never change."""

    schema_version: Literal[1] = 1
    operation_id: str
    state: Literal["RECEIVING", "VALIDATING", "READY", "FAILED", "CANCELLED"] = (
        "RECEIVING"
    )
    created_at: str
    ended_at: str | None = None
    cancel_requested: bool = False
    progress: Progress = Progress()
    dataset: DatasetInfo | None = None
    error_code: str | None = None
    file_index: int | None = None


def unique_json(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject duplicate JSON members rather than accepting parser-dependent values."""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise DatasetImportError("invalid_request")
        result[key] = value
    return result


def metadata_document(data: bytes, limits: ImportLimits) -> Metadata:
    """Decode strict UTF-8 and reject duplicate fields before typed validation."""
    json.loads(data.decode("utf-8"), object_pairs_hook=unique_json)
    metadata = Metadata.model_validate_json(data)
    metadata.check(limits)
    return metadata
