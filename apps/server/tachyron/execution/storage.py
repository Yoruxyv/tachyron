# Copyright (c) 2026 Hans Valerie
"""Exclusive operational receipts and conservative inspection after interruption."""

import hashlib
import json
from dataclasses import asdict, fields
from datetime import datetime, timedelta
from pathlib import Path
from typing import cast, get_args
from uuid import UUID

from tachyron.execution.models import (
    MAX_PATH_BYTES,
    RECEIPT_BYTES,
    ExecutionResult,
    ExecutionState,
    ItemResult,
    ItemState,
    validate_limits,
    validate_names,
)
from tachyron.processing import (
    DatasetManifest,
    ImageOutcome,
    ManifestEntry,
    ProcessingError,
    ResourceLimits,
    TransformConfig,
)
from tachyron.processing.dataset import (
    dataset_root,
    read_input,
    validate_manifest,
    validate_relative,
)
from tachyron.processing.models import ErrorCode
from tachyron.processing.processor import publish_bytes


def encode(value: object) -> bytes:
    """Serialize the separate operational contract, rejecting nonfinite values."""
    return (
        json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
    ).encode()


def publish_outcome(
    directory: Path, execution_id: str, index: int, outcome: ImageOutcome
) -> None:
    """Publish an immutable acknowledgement only after the parent receives it."""
    publish_bytes(
        directory / "records",
        f"{index:05d}.json",
        encode(
            {"execution_id": execution_id, "index": index, "outcome": asdict(outcome)}
        ),
    )


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TypeError("Expected a receipt object")
    return cast("dict[str, object]", value)


def _array(value: object) -> list[object]:
    if not isinstance(value, list):
        raise TypeError("Expected a receipt array")
    return cast("list[object]", value)


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("Expected receipt text")
    return value


def _integer(value: object) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("Expected a nonnegative receipt integer")
    return value


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("Duplicate receipt member")
        result[name] = value
    return result


def _read(
    directory: Path, name: str, maximum: int = RECEIPT_BYTES
) -> dict[str, object]:
    data = read_input(directory, name, ResourceLimits(max_file_bytes=maximum))
    value: object = json.loads(data, object_pairs_hook=_unique)
    return _object(value)


def _manifest(admission: dict[str, object]) -> DatasetManifest:
    data = _object(admission["manifest"])
    if set(data) != {"sha256", "total_bytes", "entries"}:
        raise ValueError("Invalid manifest fields")
    limits = _object(admission["limits"])
    if set(limits) != {field.name for field in fields(ResourceLimits)}:
        raise ValueError("Invalid local resource policy")
    policy = ResourceLimits(**{name: _integer(value) for name, value in limits.items()})
    validate_limits(policy)
    entries: list[ManifestEntry] = []
    values = _array(data["entries"])
    if len(values) > policy.max_files:
        raise ValueError("Excessive manifest size")
    for item in values:
        entry = _object(item)
        if set(entry) != {"relative_path", "size_bytes", "sha256"}:
            raise ValueError("Invalid manifest entry")
        name, checksum = _string(entry["relative_path"]), _string(entry["sha256"])
        validate_relative(name)
        if (
            len(name.encode()) > MAX_PATH_BYTES
            or len(checksum) != 64
            or any(c not in "0123456789abcdef" for c in checksum)
        ):
            raise ValueError("Invalid manifest identity")
        entries.append(ManifestEntry(name, _integer(entry["size_bytes"]), checksum))
    manifest = DatasetManifest(Path(), tuple(entries), _string(data["sha256"]), policy)
    validate_manifest(manifest)
    validate_names(manifest)
    if manifest.total_bytes != _integer(data["total_bytes"]):
        raise ValueError("Invalid manifest byte accounting")
    return manifest


def _admission(data: dict[str, object]) -> DatasetManifest:
    if set(data) != {
        "schema_version",
        "mode",
        "execution_id",
        "created_at",
        "workers",
        "pipeline",
        "config",
        "limits",
        "manifest",
        "environment",
    }:
        raise ValueError("Invalid admission fields")
    identifier = _string(data["execution_id"])
    created = datetime.fromisoformat(_string(data["created_at"]))
    if (
        str(UUID(identifier)) != identifier
        or _integer(data["schema_version"]) != 1
        or data["mode"] != "local-execution-v1"
        or _integer(data["workers"]) not in {1, 2, 4, 8}
        or data["pipeline"] != "resize-grayscale-v1"
        or created.utcoffset() != timedelta(0)
    ):
        raise ValueError("Invalid operational admission")
    config = _object(data["config"])
    if (
        set(config) != {"width", "height", "output_format"}
        or config["output_format"] != "PNG"
    ):
        raise ValueError("Invalid image configuration")
    transform = TransformConfig(_integer(config["width"]), _integer(config["height"]))
    _object(data["environment"])
    manifest = _manifest(data)
    manifest.limits.check_output(transform, len(manifest.entries))
    return manifest


def _outcome(value: object, expected: str) -> ImageOutcome:
    data = _object(value)
    if set(data) != {"relative_path", "output_name", "error_code", "input_size"}:
        raise ValueError("Invalid outcome fields")
    name, code = data["output_name"], data["error_code"]
    expected_name = hashlib.sha256(expected.encode()).hexdigest() + ".png"
    if data["relative_path"] != expected or (name is None) == (code is None):
        raise ValueError("Invalid outcome identity")
    if name is not None and name != expected_name:
        raise ValueError("Invalid output mapping")
    if code is not None and code not in get_args(ErrorCode):
        raise ValueError("Invalid image error code")
    dimensions = data["input_size"]
    size = None
    if dimensions is not None:
        values = _array(dimensions)
        if len(values) != 2 or any(_integer(value) == 0 for value in values):
            raise ValueError("Invalid image dimensions")
        size = (_integer(values[0]), _integer(values[1]))
    return ImageOutcome(
        expected, cast("str | None", name), cast("ErrorCode | None", code), size
    )


def _items(value: object, manifest: DatasetManifest) -> tuple[ItemResult, ...]:
    values = _array(value)
    if len(values) != len(manifest.entries):
        raise ValueError("Incomplete terminal item accounting")
    result: list[ItemResult] = []
    for item, entry in zip(values, manifest.entries, strict=True):
        data = _object(item)
        state = data["state"]
        if (
            set(data) != {"relative_path", "state", "outcome"}
            or data["relative_path"] != entry.relative_path
            or state not in get_args(ItemState)
        ):
            raise ValueError("Invalid terminal item")
        outcome = (
            None
            if data["outcome"] is None
            else _outcome(data["outcome"], entry.relative_path)
        )
        expected = "SUCCEEDED" if outcome is not None and outcome.success else "FAILED"
        if (outcome is not None and state != expected) or (
            outcome is None and state not in {"UNKNOWN", "NOT_STARTED"}
        ):
            raise ValueError("Item state contradicts its outcome")
        result.append(
            ItemResult(entry.relative_path, cast("ItemState", state), outcome)
        )
    return tuple(result)


def _terminal(
    data: dict[str, object], admission: dict[str, object], manifest: DatasetManifest
) -> ExecutionResult:
    state = data["state"]
    expected_fields = {field.name for field in fields(ExecutionResult)} | {
        "schema_version",
        "mode",
        "input_count",
        "counts",
    }
    if (
        set(data) != expected_fields
        or state not in {"COMPLETED", "FAILED", "CANCELLED"}
        or _integer(data["schema_version"]) != 1
        or data["mode"] != "local-execution-v1"
    ):
        raise ValueError("Invalid execution receipt version or state")
    for name in ("execution_id", "workers"):
        if data[name] != admission[name]:
            raise ValueError("Receipt owner differs from admission")
    _integer(data["workers"])
    seconds = data["processing_seconds"]
    if (
        isinstance(seconds, bool)
        or not isinstance(seconds, (float, int))
        or not 0 <= seconds < float("inf")
    ):
        raise ValueError("Invalid operational duration")
    cancellation = data["cancel_requested"]
    error = data["error_code"]
    if not isinstance(cancellation, bool) or error not in {
        None,
        "worker_failure",
        "receipt_failure",
        "image_failure",
    }:
        raise ValueError("Invalid execution failure or cancellation")
    result = ExecutionResult(
        _string(admission["execution_id"]),
        manifest.sha256,
        _integer(admission["workers"]),
        cast("ExecutionState", state),
        _items(data["items"], manifest),
        cancellation,
        cast("str | None", error),
        seconds,
    )
    counts = {name: _integer(value) for name, value in _object(data["counts"]).items()}
    if (
        data["manifest_sha256"] != manifest.sha256
        or _integer(data["input_count"]) != len(manifest.entries)
        or counts != result.counts
    ):
        raise ValueError("Execution totals contradict its evidence")
    counts = result.counts
    if state == "COMPLETED" and (
        error is not None or counts["succeeded"] != len(result.items)
    ):
        raise ValueError("Incomplete execution cannot be completed")
    if state == "CANCELLED" and (
        not cancellation or error is not None or counts["failed"]
    ):
        raise ValueError("Invalid cancelled execution")
    if state == "FAILED" and error is None:
        raise ValueError("Failed execution requires an error code")
    return result


def inspect_execution(directory: Path) -> ExecutionResult:
    """Read terminal evidence or conservatively reconcile acknowledged partial work.

    No final receipt means UNCONFIRMED: the owner might still be running, or may
    have exited. Unacknowledged inputs remain unknown; PNG existence proves nothing.
    This does not resume work or claim exactly-once recovery.
    """
    try:
        directory = dataset_root(directory)
        admission = _read(directory, "admission.json")
        manifest = _admission(admission)
        identifier = _string(admission["execution_id"])
        terminal = directory / "execution.json"
        if terminal.exists() or terminal.is_symlink():
            return _terminal(_read(directory, "execution.json"), admission, manifest)
        dataset_root(directory / "records")
        items: list[ItemResult] = []
        for index, entry in enumerate(manifest.entries):
            path = directory / "records" / f"{index:05d}.json"
            outcome = None
            if path.exists() or path.is_symlink():
                record = _read(directory, f"records/{index:05d}.json", 4096)
                if (
                    set(record) != {"execution_id", "index", "outcome"}
                    or record["execution_id"] != identifier
                    or _integer(record["index"]) != index
                ):
                    raise ValueError("Invalid acknowledgement owner")
                outcome = _outcome(record["outcome"], entry.relative_path)
            state: ItemState = (
                "UNKNOWN"
                if outcome is None
                else ("SUCCEEDED" if outcome.success else "FAILED")
            )
            items.append(ItemResult(entry.relative_path, state, outcome))
        return ExecutionResult(
            identifier,
            manifest.sha256,
            _integer(admission["workers"]),
            "UNCONFIRMED",
            tuple(items),
            None,
            None,
            None,
        )
    except (ValueError, TypeError, KeyError, OSError, RecursionError) as error:
        raise ProcessingError(
            "invalid_path", "Operational receipts are invalid or unavailable"
        ) from error
