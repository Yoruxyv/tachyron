# Copyright (c) 2026 Hans Valerie
"""Versioned, exclusive portable exports and strict bounded JSON re-comparison."""

import csv
import hashlib
import io
import json
import math
from dataclasses import asdict, fields
from pathlib import Path
from typing import cast

from tachyron.benchmarking.models import (
    BenchmarkConfig,
    BenchmarkError,
    Context,
    Environment,
    Experiment,
    RunRecord,
    Step,
    Telemetry,
    finite,
    integer,
)
from tachyron.benchmarking.stats import WorkerSummary, summarize, throughput, validate
from tachyron.processing import ResourceLimits, TransformConfig
from tachyron.processing.dataset import read_input
from tachyron.processing.processor import publish_bytes


def encode_document(value: object) -> bytes:
    """Encode stable UTF-8 JSON, rejecting nonfinite numbers instead of emitting NaN."""
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode(
        "utf-8"
    )


def record_document(record: RunRecord) -> dict[str, object]:
    """Include independently checkable throughput beside raw counts and duration."""
    return {
        **asdict(record),
        "throughput_images_per_second": throughput(
            record.succeeded, record.elapsed_seconds
        )
        if record.succeeded is not None
        else None,
    }


def experiment_document(experiment: Experiment) -> dict[str, object]:
    """Serialize one fixed context with all raw repetitions, including warm-ups."""
    return {
        **asdict(experiment),
        "schema_version": 1,
        "context_id": experiment.context.digest,
        "records": [record_document(record) for record in experiment.records],
    }


def _csv(rows: list[dict[str, object]], *, columns: list[str] | None = None) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(
        stream,
        fieldnames=columns if columns is not None else list(rows[0]),
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def _run_row(experiment: Experiment, record: RunRecord) -> dict[str, object]:
    document = record_document(record)
    document.pop("step")
    document.pop("telemetry")
    return {
        "schema_version": 1,
        "experiment_id": experiment.experiment_id,
        "context_json": json.dumps(
            asdict(experiment.context), sort_keys=True, allow_nan=False
        ),
        **asdict(record.step),
        **document,
        **asdict(record.telemetry),
    }


def _summary_row(experiment: Experiment, summary: WorkerSummary) -> dict[str, object]:
    document = asdict(summary)
    document.pop("elapsed")
    elapsed = summary.elapsed
    return {
        "schema_version": 1,
        "experiment_id": experiment.experiment_id,
        "context_id": experiment.context.digest,
        "context_json": json.dumps(
            asdict(experiment.context), sort_keys=True, allow_nan=False
        ),
        **document,
        "elapsed_mean_seconds": elapsed.mean if elapsed else None,
        "elapsed_median_seconds": elapsed.median if elapsed else None,
        "elapsed_minimum_seconds": elapsed.minimum if elapsed else None,
        "elapsed_maximum_seconds": elapsed.maximum if elapsed else None,
        "elapsed_sample_stddev_seconds": elapsed.sample_stddev if elapsed else None,
    }


def publish_exports(experiment: Experiment, directory: Path) -> None:
    """Exclusively publish raw/summary JSON/CSV, then the final completion manifest.

    Args:
        experiment: Valid evidence; failed comparisons yield null summary metrics.
        directory: Existing exclusively owned experiment or comparison directory.

    Raises:
        BenchmarkError: Evidence is incompatible or dishonest.
        OSError: Publication failed or an existing pathname would be overwritten.
    """
    validate(experiment)
    summaries = summarize(experiment, require_complete=False)
    exports = {
        "runs.json": encode_document(experiment_document(experiment)),
        "runs.csv": _csv(
            [_run_row(experiment, record) for record in experiment.records],
            columns=[
                "schema_version",
                "experiment_id",
                "context_json",
                *(field.name for field in fields(Step)),
                *(
                    field.name
                    for field in fields(RunRecord)
                    if field.name not in ("step", "telemetry")
                ),
                "throughput_images_per_second",
                *(field.name for field in fields(Telemetry)),
            ],
        ),
        "workers.json": encode_document(
            {
                "schema_version": 1,
                "experiment_id": experiment.experiment_id,
                "context_id": experiment.context.digest,
                "context": asdict(experiment.context),
                "status": experiment.status,
                "summaries": [asdict(summary) for summary in summaries],
                "units": {
                    "elapsed": "seconds",
                    "mean_throughput": "successful_images_per_second",
                    "cpu_mean_core_percent": "logical_core_percent",
                    "rss_peak_mib": "MiB",
                    "efficiency_percent": "percent",
                    "speedup": "ratio",
                },
            }
        ),
        "workers.csv": _csv(
            [_summary_row(experiment, summary) for summary in summaries]
        ),
    }
    for name, content in exports.items():
        publish_bytes(directory, name, content)
    publish_bytes(
        directory,
        "experiment.json",
        encode_document(
            {
                "schema_version": 1,
                "experiment_id": experiment.experiment_id,
                "status": experiment.status,
                "planned_runs": len(experiment.plan),
                "recorded_runs": len(experiment.records),
                "exports_sha256": {
                    name: hashlib.sha256(content).hexdigest()
                    for name, content in exports.items()
                },
            }
        ),
    )


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise BenchmarkError("Expected a JSON object")
    # json.loads guarantees string keys; field values remain untrusted objects.
    return cast("dict[str, object]", value)


def _exact(value: object, names: set[str]) -> dict[str, object]:
    result = _object(value)
    if result.keys() != names:
        raise BenchmarkError("Unsupported or missing export fields")
    return result


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise BenchmarkError("Expected a string")
    return value


def _integer(value: object) -> int:
    if not integer(value):
        raise BenchmarkError("Expected a nonnegative integer")
    return cast("int", value)  # integer() rejects bool and noninteger JSON values.


def _number(value: object) -> float:
    if not finite(value):
        raise BenchmarkError("Expected a finite nonnegative number")
    return cast(
        "float", value
    )  # Preserve JSON integer/float identity after finite() validation.


def _boolean(value: object) -> bool:
    if not isinstance(value, bool):
        raise BenchmarkError("Expected a boolean")
    return value


def _list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise BenchmarkError("Expected an array")
    return cast("list[object]", value)  # JSON array elements remain untrusted.


def _environment(value: object) -> Environment:
    data = _exact(value, {field.name for field in fields(Environment)})
    affinity = data["cpu_affinity"]
    return Environment(
        python_version=_string(data["python_version"]),
        pillow_version=_string(data["pillow_version"]),
        psutil_version=_string(data["psutil_version"]),
        tachyron_version=_string(data["tachyron_version"]),
        os_name=_string(data["os_name"]),
        os_release=_string(data["os_release"]),
        architecture=_string(data["architecture"]),
        host_id=_string(data["host_id"]),
        source_hash=_string(data["source_hash"]),
        cpu_model=None if data["cpu_model"] is None else _string(data["cpu_model"]),
        logical_cpus=None
        if data["logical_cpus"] is None
        else _integer(data["logical_cpus"]),
        physical_cpus=None
        if data["physical_cpus"] is None
        else _integer(data["physical_cpus"]),
        cpu_affinity=None
        if affinity is None
        else tuple(_integer(cpu) for cpu in _list(affinity)),
        total_memory_mib=None
        if data["total_memory_mib"] is None
        else _number(data["total_memory_mib"]),
        available_memory_mib=None
        if data["available_memory_mib"] is None
        else _number(data["available_memory_mib"]),
        git_commit=None if data["git_commit"] is None else _string(data["git_commit"]),
        git_dirty=None if data["git_dirty"] is None else _boolean(data["git_dirty"]),
    )


def _context(value: object) -> Context:
    data = _exact(value, {field.name for field in fields(Context)})
    transform = _exact(
        data["transform"], {field.name for field in fields(TransformConfig)}
    )
    if transform["output_format"] != "PNG":
        raise BenchmarkError("Unsupported image transform")
    limits = _exact(data["limits"], {field.name for field in fields(ResourceLimits)})
    return Context(
        _string(data["manifest_sha256"]),
        _integer(data["input_count"]),
        _integer(data["input_bytes"]),
        TransformConfig(_integer(transform["width"]), _integer(transform["height"])),
        ResourceLimits(**{key: _integer(value) for key, value in limits.items()}),
        _environment(data["environment"]),
        _boolean(data["tiny"]),
        _number(data["sampling_interval"]),
        pipeline=_string(data["pipeline"]),
        execution_mode=_string(data["execution_mode"]),
        timing_policy=_string(data["timing_policy"]),
        cpu_policy=_string(data["cpu_policy"]),
        cache_policy=_string(data["cache_policy"]),
    )


def _step(value: object) -> Step:
    data = _exact(value, {field.name for field in fields(Step)})
    return Step(
        _string(data["run_id"]),
        _integer(data["workers"]),
        _boolean(data["warmup"]),
        _integer(data["repetition"]),
        _integer(data["order"]),
    )


def _telemetry(value: object) -> Telemetry:
    data = _exact(value, {field.name for field in fields(Telemetry)})
    return Telemetry(
        observations=_integer(data["observations"]),
        cpu_samples=_integer(data["cpu_samples"]),
        memory_samples=_integer(data["memory_samples"]),
        missed_observations=_integer(data["missed_observations"]),
        cpu_observed_seconds=_number(data["cpu_observed_seconds"]),
        cpu_mean_core_percent=None
        if data["cpu_mean_core_percent"] is None
        else _number(data["cpu_mean_core_percent"]),
        cpu_peak_core_percent=None
        if data["cpu_peak_core_percent"] is None
        else _number(data["cpu_peak_core_percent"]),
        rss_peak_mib=None
        if data["rss_peak_mib"] is None
        else _number(data["rss_peak_mib"]),
        observed_descendants_peak=None
        if data["observed_descendants_peak"] is None
        else _integer(data["observed_descendants_peak"]),
        truncated=_boolean(data["truncated"]),
        unavailable_reason=None
        if data["unavailable_reason"] is None
        else _string(data["unavailable_reason"]),
    )


def _record(value: object) -> RunRecord:
    data = _exact(
        value,
        {field.name for field in fields(RunRecord)} | {"throughput_images_per_second"},
    )
    record = RunRecord(
        step=_step(data["step"]),
        context_id=_string(data["context_id"]),
        started_at=_string(data["started_at"]),
        ended_at=_string(data["ended_at"]),
        elapsed_seconds=None
        if data["elapsed_seconds"] is None
        else _number(data["elapsed_seconds"]),
        succeeded=None if data["succeeded"] is None else _integer(data["succeeded"]),
        failed=None if data["failed"] is None else _integer(data["failed"]),
        status=_string(data["status"]),
        error_code=None if data["error_code"] is None else _string(data["error_code"]),
        telemetry=_telemetry(data["telemetry"]),
        actual_workers=None
        if data["actual_workers"] is None
        else _integer(data["actual_workers"]),
    )
    expected = (
        throughput(record.succeeded, record.elapsed_seconds)
        if record.succeeded is not None
        else None
    )
    reported = data["throughput_images_per_second"]
    if (expected is None) != (reported is None) or (
        expected is not None
        and not math.isclose(expected, _number(reported), rel_tol=0, abs_tol=0)
    ):
        raise BenchmarkError("Exported throughput contradicts raw counts/duration")
    return record


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise BenchmarkError("Duplicate JSON object field")
        result[key] = value
    return result


def read_experiment(path: Path) -> Experiment:
    """Load bounded regular-file JSON and reject mixed/inconsistent evidence.

    Args:
        path: A portable runs.json produced by this schema version.

    Returns:
        Valid immutable raw evidence; unsuccessful evidence stays unsuccessful.

    Raises:
        BenchmarkError: Malformed, excessive, incompatible or inconsistent input.
    """
    try:
        raw: object = json.loads(
            read_input(
                path.parent, path.name, ResourceLimits(max_file_bytes=4 * 1024**2)
            ),
            object_pairs_hook=_unique,
        )
        data = _exact(
            raw,
            {field.name for field in fields(Experiment)}
            | {"schema_version", "context_id"},
        )
        if _integer(data["schema_version"]) != 1:
            raise BenchmarkError("Unsupported benchmark schema")
        config = _exact(
            data["config"], {field.name for field in fields(BenchmarkConfig)}
        )
        experiment = Experiment(
            _string(data["experiment_id"]),
            _context(data["context"]),
            BenchmarkConfig(
                _integer(config["repetitions"]),
                _integer(config["seed"]),
                _number(config["sampling_interval"]),
                _boolean(config["tiny"]),
            ),
            tuple(_step(value) for value in _list(data["plan"])),
            tuple(_record(value) for value in _list(data["records"])),
            _string(data["status"]),
        )
        if experiment.context.digest != data["context_id"]:
            raise BenchmarkError("Comparison context digest is inconsistent")
        validate(experiment)
        return experiment
    except (
        ValueError,
        TypeError,
        KeyError,
        OverflowError,
        OSError,
        RecursionError,
    ) as error:
        raise BenchmarkError("Invalid benchmark export") from error
