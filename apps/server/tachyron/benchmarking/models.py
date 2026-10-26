# Copyright (c) 2026 Hans Valerie
"""Immutable benchmark records with explicit units, availability and provenance."""

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime
from uuid import UUID, uuid5

from tachyron.processing import ResourceLimits, TransformConfig

WORKERS = (1, 2, 4, 8)
TIMING_POLICY = "spawn-batch-e2e-v1"
CPU_POLICY = "observed-tree-logical-core-percent-v1"


class BenchmarkError(ValueError):
    """Benchmark configuration, evidence or comparison is invalid."""


def integer(value: object, minimum: int = 0) -> bool:
    """Check an integer trust boundary without accepting boolean values."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def finite(value: object, minimum: float = 0) -> bool:
    """Check finite nonnegative observations without accepting booleans."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= minimum
    )


def digest(value: str, length: int = 64) -> bool:
    """Recognize the lowercase hashes emitted by the local provenance collector."""
    return (
        isinstance(value, str)
        and len(value) == length
        and all(c in "0123456789abcdef" for c in value)
    )


@dataclass(frozen=True, slots=True)
class BenchmarkConfig:
    """Production schedule; tiny workloads explicitly permit fewer repetitions."""

    repetitions: int = 5
    seed: int = 2026
    sampling_interval: float = 0.1
    tiny: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.tiny, bool):
            raise BenchmarkError("Tiny-workload selection must be boolean")
        if (
            not integer(self.repetitions, 1 if self.tiny else 5)
            or self.repetitions > 20
        ):
            raise BenchmarkError(
                "Repetitions must be 5-20, or 1-20 for an explicit tiny test"
            )
        if not integer(self.seed) or self.seed > 2**32 - 1:
            raise BenchmarkError("Seed must be an integer between 0 and 2^32-1")
        if not finite(self.sampling_interval, 0.01) or self.sampling_interval > 10:
            raise BenchmarkError(
                "Sampling interval must be between 0.01 and 10 seconds"
            )


@dataclass(frozen=True, slots=True)
class Environment:
    """Loaded code/runtime and hardware snapshot; unavailable information is null."""

    python_version: str
    pillow_version: str
    psutil_version: str
    tachyron_version: str
    os_name: str
    os_release: str
    architecture: str
    host_id: str
    source_hash: str
    cpu_model: str | None = None
    logical_cpus: int | None = None
    physical_cpus: int | None = None
    cpu_affinity: tuple[int, ...] | None = None
    total_memory_mib: float | None = None
    available_memory_mib: float | None = None
    git_commit: str | None = None
    git_dirty: bool | None = None

    def __post_init__(self) -> None:
        names = (
            self.python_version,
            self.pillow_version,
            self.psutil_version,
            self.tachyron_version,
            self.os_name,
            self.os_release,
            self.architecture,
        )
        if any(not isinstance(name, str) or not name for name in names):
            raise BenchmarkError("Runtime provenance must contain nonempty strings")
        if not digest(self.host_id) or not digest(self.source_hash):
            raise BenchmarkError("Invalid host or loaded-source identity")
        if any(
            value is not None and not integer(value, 1)
            for value in (self.logical_cpus, self.physical_cpus)
        ):
            raise BenchmarkError("CPU counts must be positive or unavailable")
        if any(
            value is not None and not finite(value)
            for value in (self.total_memory_mib, self.available_memory_mib)
        ):
            raise BenchmarkError("Memory provenance must be finite or unavailable")
        if self.cpu_affinity is not None and any(
            not integer(cpu) for cpu in self.cpu_affinity
        ):
            raise BenchmarkError("Invalid CPU affinity")
        if self.git_commit is not None and not digest(self.git_commit, 40):
            raise BenchmarkError("Invalid Git commit identity")
        if self.git_dirty is not None and not isinstance(self.git_dirty, bool):
            raise BenchmarkError(
                "Git working-tree state must be boolean or unavailable"
            )
        if self.cpu_model is not None and not isinstance(self.cpu_model, str):
            raise BenchmarkError("Invalid CPU description")


@dataclass(frozen=True, slots=True)
class Context:
    """One fixed workload, transform, measurement policy and machine environment."""

    manifest_sha256: str
    input_count: int
    input_bytes: int
    transform: TransformConfig
    limits: ResourceLimits
    environment: Environment
    tiny: bool
    sampling_interval: float
    pipeline: str = "resize-grayscale-v1"
    execution_mode: str = "process_pool"
    timing_policy: str = TIMING_POLICY
    cpu_policy: str = CPU_POLICY
    cache_policy: str = "uncontrolled-os-cache-one-warmup"

    def __post_init__(self) -> None:
        if (
            not digest(self.manifest_sha256)
            or not integer(self.input_count, 1)
            or not integer(self.input_bytes)
        ):
            raise BenchmarkError("Invalid workload identity or accounting")
        if (
            self.input_count > self.limits.max_files
            or self.input_bytes > self.limits.max_total_bytes
        ):
            raise BenchmarkError("Workload exceeds its resource policy")
        if (
            self.pipeline,
            self.execution_mode,
            self.timing_policy,
            self.cpu_policy,
            self.cache_policy,
        ) != (
            "resize-grayscale-v1",
            "process_pool",
            TIMING_POLICY,
            CPU_POLICY,
            "uncontrolled-os-cache-one-warmup",
        ):
            raise BenchmarkError("Unsupported comparison policy or execution mode")
        BenchmarkConfig(tiny=self.tiny, sampling_interval=self.sampling_interval)
        self.limits.check_output(self.transform, self.input_count)

    @property
    def digest(self) -> str:
        """Identify the complete portable comparison context canonically."""
        return hashlib.sha256(
            json.dumps(
                asdict(self), sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class Step:
    """One uniquely named execution at a reproducible position in the schedule."""

    run_id: str
    workers: int
    warmup: bool
    repetition: int
    order: int

    def __post_init__(self) -> None:
        UUID(self.run_id)
        if not integer(self.workers, 1) or self.workers not in WORKERS:
            raise BenchmarkError("Worker count must be 1, 2, 4 or 8")
        if (
            not isinstance(self.warmup, bool)
            or not integer(self.order)
            or not integer(self.repetition)
        ):
            raise BenchmarkError("Invalid run order or warm-up flag")
        if self.warmup != (self.repetition == 0):
            raise BenchmarkError(
                "Warm-ups use repetition zero; measurements start at one"
            )


def make_plan(config: BenchmarkConfig, experiment_id: str) -> tuple[Step, ...]:
    """Seed each round's interleaving without modifying global random state."""
    namespace = UUID(experiment_id)
    plan: list[Step] = []
    for repetition in range(config.repetitions + 1):
        order = sorted(
            WORKERS,
            key=lambda count: hashlib.sha256(
                f"{config.seed}:{repetition}:{count}".encode()
            ).digest(),
        )
        for workers in order:
            index = len(plan)
            plan.append(
                Step(
                    str(uuid5(namespace, str(index))),
                    workers,
                    repetition == 0,
                    repetition,
                    index,
                )
            )
    return tuple(plan)


@dataclass(frozen=True, slots=True)
class Telemetry:
    """Sampled resource evidence, never an invented complete CPU/memory trace."""

    observations: int = 0
    cpu_samples: int = 0
    memory_samples: int = 0
    missed_observations: int = 0
    cpu_observed_seconds: float = 0.0
    cpu_mean_core_percent: float | None = None
    cpu_peak_core_percent: float | None = None
    rss_peak_mib: float | None = None
    observed_descendants_peak: int | None = None
    truncated: bool = False
    unavailable_reason: str | None = None

    def __post_init__(self) -> None:
        if any(
            not integer(value)
            for value in (
                self.observations,
                self.cpu_samples,
                self.memory_samples,
                self.missed_observations,
            )
        ):
            raise BenchmarkError("Invalid sample accounting")
        if (
            self.observations > 10_000
            or self.cpu_samples > self.observations
            or self.memory_samples > self.observations
            or self.missed_observations > self.observations
        ):
            raise BenchmarkError("Sample counts exceed observation bounds")
        values = (
            self.cpu_observed_seconds,
            self.cpu_mean_core_percent,
            self.cpu_peak_core_percent,
            self.rss_peak_mib,
        )
        if any(value is not None and not finite(value) for value in values):
            raise BenchmarkError("Invalid resource observation")
        if self.observed_descendants_peak is not None and not integer(
            self.observed_descendants_peak
        ):
            raise BenchmarkError("Invalid descendant observation")
        if not isinstance(self.truncated, bool):
            raise BenchmarkError("Invalid truncation flag")
        if self.unavailable_reason not in (
            None,
            "setup_failed",
            "sampling_failed",
            "sample_limit",
        ):
            raise BenchmarkError("Unsupported monitoring availability code")
        if (self.cpu_samples == 0) != (self.cpu_mean_core_percent is None) or (
            self.cpu_samples == 0
        ) != (self.cpu_peak_core_percent is None):
            raise BenchmarkError("Missing CPU observations must remain unavailable")
        if (self.memory_samples == 0) != (self.rss_peak_mib is None):
            raise BenchmarkError("Missing memory observations must remain unavailable")
        if (self.cpu_samples > 0) != (self.cpu_observed_seconds > 0):
            raise BenchmarkError("CPU samples require positive observed time")


@dataclass(frozen=True, slots=True)
class RunRecord:
    """Completed or explicitly unsuccessful attempt, retaining its schedule slot."""

    step: Step
    context_id: str
    started_at: str
    ended_at: str
    elapsed_seconds: float | None
    succeeded: int | None
    failed: int | None
    status: str
    error_code: str | None
    telemetry: Telemetry
    actual_workers: int | None = None

    def __post_init__(self) -> None:
        if not digest(self.context_id):
            raise BenchmarkError("Invalid run context identity")
        for stamp in (self.started_at, self.ended_at):
            if datetime.fromisoformat(stamp).utcoffset() is None:
                raise BenchmarkError("Run timestamps must carry a timezone")
        if self.elapsed_seconds is not None and not finite(self.elapsed_seconds):
            raise BenchmarkError("Invalid elapsed duration")
        if (self.succeeded is None) != (self.failed is None) or any(
            value is not None and not integer(value)
            for value in (self.succeeded, self.failed)
        ):
            raise BenchmarkError("Invalid image accounting")
        if self.status not in ("success", "failed", "interrupted"):
            raise BenchmarkError("Invalid run status")
        if self.error_code not in (
            None,
            "image_failure",
            "execution_failure",
            "invalid_duration",
            "cleanup_failure",
            "provenance_changed",
        ):
            raise BenchmarkError("Unsupported failure code")
        if (self.status == "success") != (self.error_code is None):
            raise BenchmarkError("Run status contradicts its error code")
        if self.actual_workers is not None:
            raise BenchmarkError(
                "This sampler cannot determine actual worker utilization"
            )


@dataclass(frozen=True, slots=True)
class Experiment:
    """Raw evidence; incomplete experiments remain distinct from valid comparisons."""

    experiment_id: str
    context: Context
    config: BenchmarkConfig
    plan: tuple[Step, ...]
    records: tuple[RunRecord, ...]
    status: str
