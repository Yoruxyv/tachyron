# Copyright (c) 2026 Hans Valerie
"""Benchmark export schemas, round trips and exclusive publication."""

import csv
import json
from dataclasses import replace
from pathlib import Path

import pytest

from tachyron.benchmarking import exports
from tachyron.benchmarking.exports import publish_exports, read_experiment
from tachyron.benchmarking.models import BenchmarkError
from tests.benchmark_helpers import example_experiment


def test_json_roundtrip_csv_units_and_exclusive_publication(tmp_path: Path) -> None:
    experiment = example_experiment()
    publish_exports(experiment, tmp_path)
    assert read_experiment(tmp_path / "runs.json") == experiment
    manifest = json.loads((tmp_path / "experiment.json").read_text())
    assert manifest["status"] == "success"
    with (tmp_path / "runs.csv").open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 12
    assert rows[0]["cpu_mean_core_percent"] == ""
    assert rows[0]["actual_workers"] == ""
    assert rows[0]["context_id"] == experiment.context.digest
    assert "context_json" in rows[0]
    before = (tmp_path / "experiment.json").read_bytes()
    with pytest.raises(FileExistsError):
        publish_exports(experiment, tmp_path)
    assert (tmp_path / "experiment.json").read_bytes() == before


@pytest.mark.parametrize(
    "changed",
    ["transform", "environment", "context_id", "duplicate", "schema", "throughput"],
)
def test_import_rejects_incompatible_or_dishonest_evidence(
    tmp_path: Path, changed: str
) -> None:
    publish_exports(example_experiment(), tmp_path)
    path = tmp_path / "runs.json"
    data = json.loads(path.read_text())
    if changed == "transform":
        data["context"]["transform"]["width"] = 3
    elif changed == "environment":
        data["context"]["environment"]["os_name"] = "different"
    elif changed == "context_id":
        data["records"][0]["context_id"] = "f" * 64
    elif changed == "duplicate":
        data["records"][1] = data["records"][0]
    elif changed == "schema":
        data["schema_version"] = 100
    else:
        data["records"][0]["throughput_images_per_second"] = 999
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(BenchmarkError):
        read_experiment(path)


def test_failed_export_never_publishes_a_completion_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    publish = exports.publish_bytes

    def fail(directory: Path, name: str, data: bytes) -> None:
        if name == "workers.csv":
            raise OSError("private write failure")
        publish(directory, name, data)

    monkeypatch.setattr(exports, "publish_bytes", fail)
    with pytest.raises(OSError, match="private write failure"):
        publish_exports(example_experiment(), tmp_path)
    assert not (tmp_path / "experiment.json").exists()


def test_empty_failed_evidence_produces_headers_without_a_fake_raw_run(
    tmp_path: Path,
) -> None:
    publish_exports(
        replace(example_experiment(), records=(), status="failed"), tmp_path
    )
    with (tmp_path / "runs.csv").open(newline="", encoding="utf-8") as stream:
        rows = csv.DictReader(stream)
        assert rows.fieldnames is not None
        assert "elapsed_seconds" in rows.fieldnames
        assert list(rows) == []


def test_integer_numeric_metadata_roundtrips_without_hash_drift(tmp_path: Path) -> None:
    original = example_experiment()
    context = replace(
        original.context,
        sampling_interval=1,
        environment=replace(original.context.environment, total_memory_mib=100),
    )
    experiment = replace(
        original,
        context=context,
        config=replace(original.config, sampling_interval=1),
        records=tuple(
            replace(record, context_id=context.digest) for record in original.records
        ),
    )
    publish_exports(experiment, tmp_path)
    assert read_experiment(tmp_path / "runs.json") == experiment
