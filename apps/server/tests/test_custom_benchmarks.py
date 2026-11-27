# Copyright (c) 2026 Hans Valerie
"""Custom scientific schedules keep strict admission and portable evidence."""

from dataclasses import replace
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tachyron.api import create_app
from tachyron.benchmarking import capacity, runner
from tachyron.benchmarking.exports import publish_exports, read_experiment
from tachyron.benchmarking.models import (
    CustomBenchmarkConfig,
    Experiment,
    RunRecord,
    make_plan,
)
from tachyron.benchmarking.stats import summarize
from tachyron.jobs.coordinator import Settings
from tachyron.processing import BatchResult, DatasetManifest, TransformConfig
from tests.benchmark_helpers import example_experiment, make_manifest, sequential_pool
from tests.job_helpers import BODY, local_client, terminal


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    manifest = make_manifest(tmp_path / "input")
    return Settings({"tiny": manifest.root}, tmp_path / "history")


def _simulate_pool(
    manifest: DatasetManifest,
    directory: Path,
    transform: TransformConfig,
    workers: int,
    *,
    custom_comparison: bool,
) -> BatchResult:
    assert custom_comparison
    return sequential_pool(manifest, directory, transform, workers)


@pytest.mark.parametrize("counts", [[], [0, 3], [1, 17], [3, 3], [True], [3.0], ["9"]])
def test_custom_job_rejects_invalid_counts(
    settings: Settings, counts: list[object]
) -> None:
    with local_client(create_app(settings)) as client:
        response = client.post("/api/jobs", json={**BODY, "worker_counts": counts})
        assert response.status_code == 422
        assert client.get("/api/benchmarks").json() == []


def test_custom_job_bounds_output_work(settings: Settings) -> None:
    dataset = settings.datasets["tiny"]
    (dataset / "extra.png").write_bytes((dataset / "0.png").read_bytes())
    with local_client(create_app(settings)) as client:
        response = client.post(
            "/api/jobs",
            json={
                **BODY,
                "width": 4096,
                "height": 4096,
                "repetitions": 11,
                "worker_counts": list(range(1, 11)),
            },
        )
        assert response.status_code == 422
        assert "comparison_budget_exceeded" in response.text


def test_custom_capacity_refusal_has_no_pool_or_false_measurements(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        capacity,
        "observe_resources",
        Mock(return_value=SimpleNamespace(candidates=(1,))),
    )
    pool = Mock(
        side_effect=AssertionError("capacity refusal must precede pool startup")
    )
    monkeypatch.setattr(runner, "run_parallel", pool)
    with local_client(create_app(settings)) as client:
        accepted = client.post("/api/jobs", json={**BODY, "worker_counts": [1, 9]})
        assert accepted.status_code == 202
        job = terminal(client, accepted.json()["id"])
        assert job.state == "FAILED"
        assert job.error_code == "worker_capacity_unavailable"
        assert job.completed_batches == job.unknown_batches == job.succeeded_images == 0
        history = client.get(f"/api/benchmarks/{job.id}")
        assert history.status_code == 200
        assert history.json()["result"] is None
    pool.assert_not_called()


def test_custom_job_canonicalizes_retries_and_restores_versioned_history(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        capacity,
        "observe_resources",
        Mock(return_value=SimpleNamespace(candidates=(3, 9))),
    )

    pool = Mock(side_effect=_simulate_pool)
    monkeypatch.setattr(runner, "run_parallel", pool)
    headers = {"Idempotency-Key": "custom-order"}
    with local_client(create_app(settings)) as client:
        accepted = client.post(
            "/api/jobs", json={**BODY, "worker_counts": [9, 3]}, headers=headers
        )
        assert accepted.status_code == 202
        assert accepted.json()["request"]["worker_counts"] == [3, 9]
        job = terminal(client, accepted.json()["id"])
        assert job.state == "COMPLETED"
        assert (job.completed_batches, job.succeeded_images) == (4, 12)
        retry = client.post(
            "/api/jobs", json={**BODY, "worker_counts": [3, 9]}, headers=headers
        )
        assert retry.status_code == 202
        assert retry.json()["id"] == job.id
        metrics = client.get(f"/api/jobs/{job.id}/metrics").json()
        assert metrics["schema_version"] == 2
        assert metrics["experiment"]["config"]["worker_counts"] == [3, 9]
        assert [row["workers"] for row in metrics["summaries"]] == [3, 9]
        assert all(
            row["speedup"] is None and row["efficiency_percent"] is None
            for row in metrics["summaries"]
        )
    assert pool.call_count == 4
    with local_client(create_app(settings)) as restarted:
        detail = restarted.get(f"/api/benchmarks/{job.id}").json()
        assert detail["job"]["request"]["worker_counts"] == [3, 9]
        assert detail["result"] == metrics


def test_custom_cancellation_stops_before_starting_the_next_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        capacity,
        "observe_resources",
        Mock(return_value=SimpleNamespace(candidates=(1, 3))),
    )

    pool = Mock(side_effect=_simulate_pool)
    monkeypatch.setattr(runner, "run_parallel", pool)
    stopped = Event()

    def stop_after_measurement(record: RunRecord) -> None:
        if not record.step.warmup:
            stopped.set()

    comparison = runner.run_experiment(
        make_manifest(tmp_path / "cancel-input"),
        tmp_path / "cancelled",
        TransformConfig(2, 2),
        CustomBenchmarkConfig(worker_counts=(1, 3), repetitions=2, tiny=True),
        cancelled=stopped.is_set,
        on_record=stop_after_measurement,
    )
    assert comparison.status == "interrupted"
    assert pool.call_count == len(comparison.records) == 3
    assert read_experiment(tmp_path / "cancelled/runs.json") == comparison
    assert (
        sum(row.measured_count for row in summarize(comparison, require_complete=False))
        == 1
    )


def _comparison() -> Experiment:
    template = example_experiment()
    config = CustomBenchmarkConfig(
        worker_counts=(1, 3, 9), repetitions=2, seed=template.config.seed, tiny=True
    )
    plan = make_plan(config, template.experiment_id)
    records = tuple(
        replace(template.records[0], step=step, elapsed_seconds=12.0 / step.workers)
        for step in plan
    )
    return replace(template, config=config, plan=plan, records=records)


def test_custom_exports_round_trip_and_use_measured_one_worker_baseline(
    tmp_path: Path,
) -> None:
    experiment = _comparison()
    publish_exports(experiment, tmp_path)
    restored = read_experiment(tmp_path / "runs.json")
    assert restored == experiment
    results = summarize(restored)
    assert [row.workers for row in results] == [1, 3, 9]
    assert [row.speedup for row in results] == [1.0, 3.0, 9.0]
    assert [row.efficiency_percent for row in results] == [100.0, 100.0, 100.0]


def test_partial_custom_retains_measured_one_worker_evidence() -> None:
    comparison = _comparison()
    partial = replace(comparison, records=comparison.records[:6], status="interrupted")
    baseline = summarize(partial, require_complete=False)[0]
    assert baseline.workers == baseline.measured_count == 1
    assert baseline.elapsed is not None
    assert baseline.elapsed.mean == pytest.approx(12.0)
    assert baseline.speedup is None
    assert baseline.efficiency_percent is None


def test_partial_custom_preserves_only_measured_observations() -> None:
    comparison = _comparison()
    partial = replace(comparison, records=comparison.records[:4], status="interrupted")
    rows = summarize(partial, require_complete=False)
    observed = partial.records[-1].step.workers
    for row in rows:
        assert row.measured_count == int(row.workers == observed)
        assert (row.elapsed is not None) == (row.workers == observed)
        assert not row.complete
        assert row.speedup is None
        assert row.efficiency_percent is None
