# Copyright (c) 2026 Hans Valerie
"""Characterize cooperative stops without changing measurement or CLI evidence."""

from pathlib import Path
from threading import Event
from unittest.mock import Mock

import pytest

from tachyron.benchmarking import runner
from tachyron.benchmarking.exports import read_experiment
from tachyron.benchmarking.models import BenchmarkConfig, RunRecord
from tachyron.benchmarking.stats import summarize
from tachyron.processing import TransformConfig
from tests.benchmark_helpers import make_manifest, sequential_pool


@pytest.mark.parametrize("stop_after", [0, 1, 7, 8])
def test_cancellation_preserves_completed_batches_and_never_truncates_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stop_after: int,
) -> None:
    manifest = make_manifest(tmp_path / "input")
    signal = Event()
    observed: list[RunRecord] = []
    execute = Mock(side_effect=sequential_pool)
    monkeypatch.setattr(runner, "run_parallel", execute)
    if stop_after == 0:
        signal.set()

    def progress(record: RunRecord) -> None:
        assert (
            len(list((tmp_path / "output/records").glob("*.json"))) == len(observed) + 1
        )
        observed.append(record)
        if len(observed) == stop_after:
            signal.set()

    experiment = runner.run_experiment(
        manifest,
        tmp_path / "output",
        TransformConfig(2, 2),
        BenchmarkConfig(repetitions=1, tiny=True),
        cancelled=signal.is_set,
        on_record=progress,
    )
    assert experiment.status == ("success" if stop_after == 8 else "interrupted")
    assert len(experiment.records) == execute.call_count == stop_after
    assert experiment.records == tuple(observed)
    assert all((record.succeeded, record.failed) == (3, 0) for record in observed)
    assert read_experiment(tmp_path / "output/runs.json") == experiment
    assert all(
        row.complete == (stop_after == 8)
        for row in summarize(experiment, require_complete=False)
    )


def test_image_failure_wins_over_simultaneous_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = make_manifest(tmp_path / "input", corrupt=True)
    signal = Event()
    monkeypatch.setattr(runner, "run_parallel", sequential_pool)
    experiment = runner.run_experiment(
        manifest,
        tmp_path / "output",
        TransformConfig(2, 2),
        BenchmarkConfig(repetitions=1, tiny=True),
        cancelled=signal.is_set,
        on_record=lambda _record: signal.set(),
    )
    assert experiment.status == "failed"
    assert experiment.records[0].error_code == "image_failure"
    assert (experiment.records[0].succeeded, experiment.records[0].failed) == (3, 1)


def test_progress_observer_failure_stops_work_and_is_not_reported_as_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = make_manifest(tmp_path / "input")
    execute = Mock(side_effect=sequential_pool)
    monkeypatch.setattr(runner, "run_parallel", execute)
    with pytest.raises(OSError, match="history unavailable"):
        runner.run_experiment(
            manifest,
            tmp_path / "output",
            TransformConfig(2, 2),
            BenchmarkConfig(repetitions=1, tiny=True),
            on_record=Mock(side_effect=OSError("history unavailable")),
        )
    assert execute.call_count == 1
    assert len(list((tmp_path / "output/records").glob("*.json"))) == 1
    assert not (tmp_path / "output/experiment.json").exists()
