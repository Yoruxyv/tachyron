# Copyright (c) 2026 Hans Valerie
"""Registered operational workloads owned by the existing coordinator."""

from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

import psutil
from pydantic import BaseModel, ConfigDict, Field, model_validator

from tachyron.benchmarking.models import WORKERS, BenchmarkError
from tachyron.benchmarking.ownership import experiment_lock
from tachyron.execution.adaptive import _run_adaptive_manifest
from tachyron.execution.live import LiveTelemetry, Mode
from tachyron.execution.models import ExecutionState, validate_limits
from tachyron.execution.runner import _run_manifest
from tachyron.jobs.models import DatasetId
from tachyron.processing import DatasetManifest, TransformConfig
from tachyron.processing.dataset import dataset_root
from tachyron.processing.models import DEFAULT_TRANSFORM
from tachyron.workers import WorkerExecutionError

RETAINED_OPERATIONS = 8


class OperationRequest(BaseModel):
    """An explicit fixed-worker or experimental adaptive local execution."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    dataset_id: DatasetId
    mode: Mode
    workers: int | None = None
    width: int = Field(default=DEFAULT_TRANSFORM.width, ge=1, le=4096)
    height: int = Field(default=DEFAULT_TRANSFORM.height, ge=1, le=4096)

    @model_validator(mode="after")
    def validate_workers(self) -> "OperationRequest":
        """Only fixed mode accepts a supported explicit worker cap."""
        if self.mode == "manual" and self.workers not in WORKERS:
            raise ValueError("Manual mode requires 1, 2, 4 or 8 workers")
        if self.mode == "adaptive" and self.workers is not None:
            raise ValueError("Adaptive mode selects its own worker cap")
        return self

    def transform(self) -> TransformConfig:
        """Use the existing image transform with validated dimensions."""
        return TransformConfig(self.width, self.height)


@dataclass(frozen=True, slots=True)
class OperationalJob:
    """One frozen registered input and a bounded, owner-local snapshot."""

    request: OperationRequest
    manifest: DatasetManifest
    telemetry: LiveTelemetry

    def execute(self, data_dir: Path) -> tuple[ExecutionState, str | None]:
        """Run inside the reserved slot and reconcile the actual receipt."""
        started = perf_counter()
        try:
            with experiment_lock():
                validate_limits(self.manifest.limits)
                parent = data_dir / "operations"
                parent.mkdir(mode=0o700, exist_ok=True)
                directory = dataset_root(parent) / self.telemetry.run_id
                return self._execute_manifest(directory, started)
        except BenchmarkError:
            return "FAILED", "workload_busy"
        except WorkerExecutionError:
            return "UNCONFIRMED", "execution_unconfirmed"
        except (OSError, ValueError, psutil.Error):
            return "FAILED", "operation_failure"

    def _execute_manifest(
        self, directory: Path, started: float
    ) -> tuple[ExecutionState, str | None]:
        config = self.request.transform()
        live = self.telemetry
        if self.request.workers is not None:
            result = _run_manifest(
                self.manifest,
                directory,
                config,
                self.request.workers,
                cancelled=live.cancelled,
                live=live,
            )
            return result.state, result.error_code
        adaptive = _run_adaptive_manifest(
            self.manifest,
            directory,
            config,
            cancelled=live.cancelled,
            start=started,
            live=live,
        )
        return adaptive.observed_terminal()
