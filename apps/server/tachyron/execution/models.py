# Copyright (c) 2026 Hans Valerie
"""Independent operational receipts and local policy; benchmark-v1 stays unchanged."""

import unicodedata
from dataclasses import asdict, dataclass, fields
from typing import Literal

from tachyron.processing import (
    DatasetManifest,
    ImageOutcome,
    ProcessingError,
    ResourceLimits,
)

LOCAL_LIMITS = ResourceLimits(
    max_files=10_000,
    max_total_bytes=8 * 1024**3,
    max_output_pixels=1_073_741_824,
    max_entries=50_000,
)
MAX_PATH_BYTES = 1024
RECEIPT_BYTES = 64 * 1024**2
ExecutionState = Literal["COMPLETED", "FAILED", "CANCELLED", "UNCONFIRMED"]
ItemState = Literal["SUCCEEDED", "FAILED", "UNKNOWN", "NOT_STARTED"]


def validate_limits(limits: ResourceLimits) -> None:
    """Allow narrower local policies without expanding the supported safety bounds."""
    if any(
        getattr(limits, field.name) > getattr(LOCAL_LIMITS, field.name)
        for field in fields(ResourceLimits)
    ):
        raise ProcessingError(
            "resource_limit", "Local execution policy can only be narrowed"
        )


def validate_names(manifest: DatasetManifest) -> None:
    """Bound receipt names and reject ambiguous portable input identities."""
    names: set[str] = set()
    for entry in manifest.entries:
        name = entry.relative_path
        key = unicodedata.normalize("NFC", name).casefold()
        if (
            len(name.encode()) > MAX_PATH_BYTES
            or any(unicodedata.category(c).startswith("C") for c in name)
            or key in names
        ):
            raise ProcessingError(
                "invalid_path", "Local manifest names are excessive or ambiguous"
            )
        names.add(key)


@dataclass(frozen=True)
class ItemResult:
    """One fixed manifest item; unknown work is never promoted by file existence."""

    relative_path: str
    state: ItemState
    outcome: ImageOutcome | None = None


@dataclass(frozen=True)
class ExecutionResult:
    """Durable operational outcome, deliberately distinct from benchmark evidence."""

    execution_id: str
    manifest_sha256: str
    workers: int
    state: ExecutionState
    items: tuple[ItemResult, ...]
    cancel_requested: bool | None
    error_code: str | None
    processing_seconds: float | None

    @property
    def counts(self) -> dict[str, int]:
        """Account for every input, including definitely unstarted and unknown work."""
        return {
            state.lower(): sum(item.state == state for item in self.items)
            for state in ("SUCCEEDED", "FAILED", "UNKNOWN", "NOT_STARTED")
        }

    def to_dict(self) -> dict[str, object]:
        """Produce a portable receipt without absolute filesystem roots."""
        return {
            "schema_version": 1,
            "mode": "local-execution-v1",
            **asdict(self),
            "input_count": len(self.items),
            "counts": self.counts,
        }
