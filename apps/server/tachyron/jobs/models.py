# Copyright (c) 2026 Hans Valerie
"""Typed API and durable job documents, with bounded benchmark inputs."""

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
    model_validator,
)

from tachyron.benchmarking.models import (
    DEFAULT_CONFIG,
    MAX_CUSTOM_COUNTS,
    MAX_CUSTOM_WORKERS,
    BenchmarkConfig,
    BenchmarkSchedule,
    CustomBenchmarkConfig,
    Experiment,
)
from tachyron.benchmarking.stats import WorkerSummary, summarize
from tachyron.processing import TransformConfig
from tachyron.processing.models import DEFAULT_TRANSFORM

DatasetId = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")]
JobState = Literal["QUEUED", "RUNNING", "COMPLETED", "FAILED", "CANCELLED"]
TERMINAL = ("COMPLETED", "FAILED", "CANCELLED")


CustomCount = Annotated[int, Field(strict=True, ge=1, le=MAX_CUSTOM_WORKERS)]
CustomCounts = Annotated[
    tuple[CustomCount, ...],
    Field(strict=False, min_length=1, max_length=MAX_CUSTOM_COUNTS),
]


class RunRequest(BaseModel):
    """The fixed comparison or a bounded custom schedule over the same pipeline."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    dataset_id: DatasetId
    operation: Literal["resize-grayscale-v1"] = "resize-grayscale-v1"
    width: int = Field(default=DEFAULT_TRANSFORM.width, ge=1, le=4096)
    height: int = Field(default=DEFAULT_TRANSFORM.height, ge=1, le=4096)
    repetitions: int = Field(default=DEFAULT_CONFIG.repetitions, ge=1, le=20)
    seed: int = Field(default=DEFAULT_CONFIG.seed, ge=0, le=2**32 - 1)
    sampling_interval: float = Field(
        default=DEFAULT_CONFIG.sampling_interval, ge=0.01, le=10
    )
    tiny: bool = DEFAULT_CONFIG.tiny
    worker_counts: CustomCounts | None = None

    @field_validator("worker_counts")
    @classmethod
    def order_counts(cls, value: tuple[int, ...] | None) -> tuple[int, ...] | None:
        """Canonicalize order while preserving duplicates for rejection."""
        return tuple(sorted(value)) if value is not None else None

    # Infer the return type to retain the typed RunRequest schema for input and output.
    @model_serializer(mode="wrap")
    def _serialize(self, serialize: SerializerFunctionWrapHandler):
        """Keep the historical request wire shape when using the fixed schedule."""
        data: dict[str, object] = serialize(self)
        if self.worker_counts is None:
            data.pop("worker_counts", None)
        return data

    @model_validator(mode="after")
    def validate_schedule(self) -> "RunRequest":
        """Apply the same production/tiny repetition policy as the CLI."""
        self.benchmark_config()
        return self

    def benchmark_config(self) -> BenchmarkSchedule:
        """Return the shared core configuration without changing its defaults."""
        if self.worker_counts is not None:
            return CustomBenchmarkConfig(
                worker_counts=self.worker_counts,
                repetitions=self.repetitions,
                seed=self.seed,
                sampling_interval=self.sampling_interval,
                tiny=self.tiny,
            )
        return BenchmarkConfig(
            self.repetitions, self.seed, self.sampling_interval, self.tiny
        )

    def transform(self) -> TransformConfig:
        """Return the established lossless image pipeline configuration."""
        return TransformConfig(self.width, self.height)


class DatasetInfo(BaseModel):
    """Public discovery metadata; filesystem roots are never returned."""

    dataset_id: str
    manifest_sha256: str | None = None
    input_count: int | None = None
    total_bytes: int | None = None
    error_code: str | None = None
    display_name: str | None = None
    source: str = "environment"
    selection_mode: str | None = None
    excluded_count: int = 0


class Job(BaseModel):
    """Durable state; image counts sum known attempts, including warm-ups."""

    model_config = ConfigDict(frozen=True)

    id: str
    request: RunRequest
    manifest_sha256: str
    input_count: int
    state: JobState = "QUEUED"
    created_at: str
    started_at: str | None = None
    ended_at: str | None = None
    cancel_requested: bool = False
    completed_batches: int = 0
    total_batches: int
    succeeded_images: int = 0
    failed_images: int = 0
    unknown_batches: int = 0
    error_code: str | None = None


class Metrics(BaseModel):
    """Raw core evidence and authoritative statistics, with unchanged units."""

    schema_version: Literal[1, 2] = 1
    context_id: str
    experiment: Experiment
    summaries: tuple[WorkerSummary, ...]

    @model_validator(mode="after")
    def validate_evidence(self) -> "Metrics":
        """Reject inconsistent stored contexts/statistics rather than serving them."""
        if (
            self.schema_version != self.experiment.config.schema_version
            or self.context_id != self.experiment.context.digest
            or self.summaries != summarize(self.experiment, require_complete=False)
        ):
            raise ValueError("Stored metrics contradict their raw evidence")
        return self

    @classmethod
    def from_experiment(cls, experiment: Experiment) -> "Metrics":
        """Preserve incomplete evidence without promoting it to a comparison."""
        return cls(
            schema_version=experiment.config.schema_version,
            context_id=experiment.context.digest,
            experiment=experiment,
            summaries=summarize(experiment, require_complete=False),
        )


class HistoryItem(BaseModel):
    """One terminal job and its available comparison statistics."""

    job: Job
    summaries: tuple[WorkerSummary, ...] | None


class HistoryDetail(BaseModel):
    """Terminal history, including partial raw evidence when available."""

    job: Job
    result: Metrics | None


class ErrorResponse(BaseModel):
    """Sanitized transport error with a stable machine-readable code."""

    detail: str
