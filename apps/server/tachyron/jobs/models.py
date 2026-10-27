# Copyright (c) 2026 Hans Valerie
"""Typed API and durable job documents, with bounded benchmark inputs."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tachyron.benchmarking.models import BenchmarkConfig, Experiment
from tachyron.benchmarking.stats import WorkerSummary, summarize
from tachyron.processing import TransformConfig

DatasetId = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")]
JobState = Literal["QUEUED", "RUNNING", "COMPLETED", "FAILED", "CANCELLED"]
TERMINAL = ("COMPLETED", "FAILED", "CANCELLED")


class RunRequest(BaseModel):
    """One full 1/2/4/8-worker comparison using the CLI's fixed pipeline."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    dataset_id: DatasetId
    operation: Literal["resize-grayscale-v1"] = "resize-grayscale-v1"
    width: int = Field(default=256, ge=1, le=4096)
    height: int = Field(default=256, ge=1, le=4096)
    repetitions: int = Field(default=5, ge=1, le=20)
    seed: int = Field(default=2026, ge=0, le=2**32 - 1)
    sampling_interval: float = Field(default=0.1, ge=0.01, le=10)
    tiny: bool = False

    @model_validator(mode="after")
    def validate_schedule(self) -> "RunRequest":
        """Apply the same production/tiny repetition policy as the CLI."""
        self.benchmark_config()
        return self

    def benchmark_config(self) -> BenchmarkConfig:
        """Return the shared core configuration without changing its defaults."""
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

    schema_version: Literal[1] = 1
    context_id: str
    experiment: Experiment
    summaries: tuple[WorkerSummary, ...]

    @model_validator(mode="after")
    def validate_evidence(self) -> "Metrics":
        """Reject inconsistent stored contexts/statistics rather than serving them."""
        if (
            self.context_id != self.experiment.context.digest
            or self.summaries != summarize(self.experiment, require_complete=False)
        ):
            raise ValueError("Stored metrics contradict their raw evidence")
        return self

    @classmethod
    def from_experiment(cls, experiment: Experiment) -> "Metrics":
        """Preserve incomplete evidence without promoting it to a comparison."""
        return cls(
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
