# Copyright (c) 2026 Hans Valerie
"""Bounded observational process-tree sampling; no worker lifecycle ownership."""

import math
import os
import threading
import time
from dataclasses import dataclass

import psutil

from tachyron.benchmarking.models import BenchmarkError, Telemetry

MAX_OBSERVATIONS = 10_000


@dataclass(frozen=True, slots=True)
class ProcessSample:
    """One process identity, CPU counter in seconds, and RSS in bytes."""

    pid: int
    created: float
    cpu_seconds: float
    rss_bytes: int


@dataclass(frozen=True, slots=True)
class Snapshot:
    """A sequential process-tree observation, not an atomic physical-memory view."""

    at: float
    processes: tuple[ProcessSample, ...]
    complete: bool


def summarize_samples(
    snapshots: tuple[Snapshot, ...],
    start: float,
    stop: float,
    *,
    truncated: bool = False,
    reason: str | None = None,
) -> Telemetry:
    """Use only in-window samples and stable, fully readable process sets.

    CPU intervals crossing a birth, exit, inaccessible observation or PID reuse
    remain unavailable. Means weight valid intervals by their observed duration.
    """
    samples = tuple(sample for sample in snapshots if start <= sample.at <= stop)
    cpu: list[tuple[float, float]] = []
    memory: list[float] = []
    descendants: list[int] = []
    missed = 0
    previous: Snapshot | None = None
    for sample in samples:
        if not sample.complete or not sample.processes:
            previous = None
            missed += 1
            continue
        memory.append(sum(process.rss_bytes for process in sample.processes) / 1024**2)
        descendants.append(len(sample.processes) - 1)
        if previous is not None:
            old = {
                (process.pid, process.created): process.cpu_seconds
                for process in previous.processes
            }
            current = {
                (process.pid, process.created): process.cpu_seconds
                for process in sample.processes
            }
            duration = sample.at - previous.at
            deltas = tuple(
                value - old[key] for key, value in current.items() if key in old
            )
            if (
                old.keys() == current.keys()
                and duration > 0
                and all(value >= 0 and math.isfinite(value) for value in deltas)
            ):
                percent = sum(deltas) / duration * 100
                if math.isfinite(percent):
                    cpu.append((percent, duration))
                else:
                    missed += 1
            else:
                missed += 1
        previous = sample
    observed_seconds = sum(duration for _, duration in cpu)
    return Telemetry(
        observations=len(samples),
        cpu_samples=len(cpu),
        memory_samples=len(memory),
        missed_observations=missed,
        cpu_observed_seconds=observed_seconds,
        cpu_mean_core_percent=sum(value * duration for value, duration in cpu)
        / observed_seconds
        if cpu
        else None,
        cpu_peak_core_percent=max(value for value, _ in cpu) if cpu else None,
        rss_peak_mib=max(memory) if memory else None,
        observed_descendants_peak=max(descendants) if descendants else None,
        truncated=truncated,
        unavailable_reason=reason,
    )


class Sampler:
    """A run-scoped sampling thread with bounded storage and explicit availability."""

    def __init__(self, interval: float) -> None:
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._parent: psutil.Process | None = None
        self._excluded: set[tuple[int, float]] = set()
        self._snapshots: list[Snapshot] = []
        self._reason: str | None = None
        self._truncated = False

    def start(self) -> None:
        """Prime the observation scope before timing; setup failure is unavailable."""
        try:
            self._parent = psutil.Process(os.getpid())
            self._excluded = {
                (child.pid, child.create_time())
                for child in self._parent.children(recursive=True)
            }
            self._thread = threading.Thread(
                target=self._run, daemon=True, name="tachyron-resource-sampler"
            )
            self._thread.start()
        except (psutil.Error, OSError, RuntimeError):
            self._stop.set()
            self._reason = "setup_failed"

    def _snapshot(self) -> Snapshot:
        parent = self._parent
        if parent is None:
            raise BenchmarkError("Sampler has no process owner")
        samples: list[ProcessSample] = []
        complete = True
        try:
            processes = [parent, *parent.children(recursive=True)]
        except psutil.Error:
            return Snapshot(time.perf_counter(), (), False)
        if len(processes) > 64:
            raise BenchmarkError(
                "Benchmark process tree exceeds the sampling safety bound"
            )
        for process in processes:
            try:
                created = process.create_time()
                if (process.pid, created) in self._excluded:
                    continue
                times = process.cpu_times()
                rss = process.memory_info().rss
                if not process.is_running():
                    complete = False
                    continue
                samples.append(
                    ProcessSample(process.pid, created, times.user + times.system, rss)
                )
            except psutil.Error:
                complete = False
        return Snapshot(time.perf_counter(), tuple(samples), complete)

    def _run(self) -> None:
        try:
            while not self._stop.wait(self.interval):
                if len(self._snapshots) == MAX_OBSERVATIONS:
                    self._truncated = True
                    self._reason = "sample_limit"
                    return
                self._snapshots.append(self._snapshot())
        except (psutil.Error, OSError, RuntimeError, ValueError):
            # Observational failure cannot manufacture resource values or a run failure.
            self._reason = "sampling_failed"

    def stop(self, start: float, stop: float) -> Telemetry:
        """Stop/join before reading evidence; a stuck sampler aborts the experiment."""
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.ident is not None:
            thread.join(timeout=3)
            if thread.is_alive():
                raise BenchmarkError("Resource sampler could not stop")
        return summarize_samples(
            tuple(self._snapshots),
            start,
            stop,
            truncated=self._truncated,
            reason=self._reason,
        )
