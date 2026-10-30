# Copyright (c) 2026 Hans Valerie
"""Operational admission, receipt integrity, real spawn and legacy isolation."""

import json
import multiprocessing
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from threading import Event
from typing import NoReturn, cast
from unittest.mock import Mock

import pytest
from PIL import Image

from tachyron.benchmarking.models import BenchmarkError
from tachyron.benchmarking.ownership import experiment_lock
from tachyron.execution import LOCAL_LIMITS, cli, inspect_execution, run_local, runner
from tachyron.processing import (
    ImageOutcome,
    ManifestEntry,
    ProcessingError,
    ResourceLimits,
    TransformConfig,
    build_manifest,
    run_sequential,
)
from tachyron.workers import operational
from tests.benchmark_helpers import make_manifest
from tests.test_workers import crash_in_worker, exit_in_worker, raise_in_worker


def interrupt_in_worker(
    root: Path,
    entry: ManifestEntry,
    output: Path,
    config: TransformConfig,
    limits: ResourceLimits,
) -> NoReturn:
    """Raise in the child without sending a cancellation signal to its parent."""
    raise KeyboardInterrupt("private child interrupt")


def test_local_policy_is_separate_and_only_narrowable(tmp_path: Path) -> None:
    manifest = make_manifest(tmp_path / "inputs")
    assert (ResourceLimits().max_files, LOCAL_LIMITS.max_files) == (1000, 10_000)
    assert ResourceLimits().max_total_bytes == 512 * 1024**2
    for limits in (
        replace(LOCAL_LIMITS, max_files=10_001),
        replace(LOCAL_LIMITS, max_pixels=LOCAL_LIMITS.max_pixels + 1),
    ):
        with pytest.raises(ProcessingError, match="narrowed"):
            run_local(
                manifest.root,
                tmp_path / "outputs",
                TransformConfig(2, 2),
                1,
                limits=limits,
            )
    with pytest.raises(ProcessingError, match="file or byte"):
        run_local(
            manifest.root,
            tmp_path / "outputs",
            TransformConfig(2, 2),
            1,
            limits=replace(LOCAL_LIMITS, max_files=2),
        )
    assert not (tmp_path / "outputs").exists()


@pytest.mark.spawn
@pytest.mark.parametrize("workers", [1, 2, 4, 8])
def test_operational_pool_matches_sequential_and_releases_children(
    tmp_path: Path, workers: int
) -> None:
    manifest = make_manifest(tmp_path / "inputs")
    with Image.open(manifest.root / "0.png") as source:
        source.save(manifest.root / "source.jpg", format="JPEG")
    manifest = build_manifest(manifest.root)
    config = TransformConfig(2, 2)
    reference = run_sequential(manifest, tmp_path / "reference", config)
    before = {child.pid for child in multiprocessing.active_children()}
    result = run_local(manifest.root, tmp_path / "output", config, workers)
    assert result.state == "COMPLETED"
    assert result.counts == {
        "succeeded": 4,
        "failed": 0,
        "unknown": 0,
        "not_started": 0,
    }
    assert inspect_execution(tmp_path / "output") == result
    assert result.manifest_sha256 == manifest.sha256
    assert result.processing_seconds is not None
    assert result.processing_seconds > 0
    assert len(list((tmp_path / "output" / "records").glob("*.json"))) == 4
    assert {child.pid for child in multiprocessing.active_children()} <= before
    for outcome in reference.outcomes:
        assert outcome.output_name is not None
        assert (tmp_path / "output" / outcome.output_name).read_bytes() == (
            reference.output_dir / outcome.output_name
        ).read_bytes()
    assert not (tmp_path / "output" / "run.json").exists()
    assert not (tmp_path / "output" / "runs.json").exists()


@pytest.mark.spawn
def test_corrupt_input_is_failed_and_other_items_still_complete(tmp_path: Path) -> None:
    manifest = make_manifest(tmp_path / "inputs", corrupt=True)
    result = run_local(manifest.root, tmp_path / "output", TransformConfig(2, 2), 2)
    assert result.state == "FAILED"
    assert result.error_code == "image_failure"
    assert result.counts == {
        "succeeded": 3,
        "failed": 1,
        "unknown": 0,
        "not_started": 0,
    }
    assert inspect_execution(tmp_path / "output") == result


def test_cancel_before_pool_proves_every_item_unstarted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = make_manifest(tmp_path / "inputs")
    factory = Mock(side_effect=AssertionError("No pool should start"))
    monkeypatch.setattr(operational, "ProcessPoolExecutor", factory)
    result = run_local(
        manifest.root,
        tmp_path / "output",
        TransformConfig(2, 2),
        8,
        cancelled=lambda: True,
    )
    assert result.state == "CANCELLED"
    assert result.cancel_requested
    assert result.counts == {
        "succeeded": 0,
        "failed": 0,
        "unknown": 0,
        "not_started": 3,
    }
    factory.assert_not_called()
    assert inspect_execution(tmp_path / "output") == result


@pytest.mark.parametrize("worker", [0, 3, 16, True])
def test_unsupported_worker_count_rejects_before_discovery(
    tmp_path: Path, worker: int
) -> None:
    with pytest.raises(ValueError, match="Worker count"):
        run_local(
            tmp_path / "missing", tmp_path / "output", TransformConfig(2, 2), worker
        )
    assert not (tmp_path / "output").exists()


def test_admission_rejects_output_overlap_low_disk_and_existing_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = make_manifest(tmp_path / "inputs")
    existing = tmp_path / "existing"
    existing.mkdir()
    sentinel = existing / "sentinel"
    sentinel.write_bytes(b"retain")
    for output in (manifest.root / "nested", existing):
        with pytest.raises(ProcessingError):
            run_local(manifest.root, output, TransformConfig(2, 2), 1)
    assert sentinel.read_bytes() == b"retain"
    monkeypatch.setattr(runner.shutil, "disk_usage", Mock(return_value=Mock(free=0)))
    with pytest.raises(ProcessingError, match="disk headroom"):
        run_local(manifest.root, tmp_path / "low-disk", TransformConfig(2, 2), 1)
    assert not (tmp_path / "low-disk").exists()


def test_output_pixel_budget_and_ambiguous_names_reject_before_workers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = make_manifest(tmp_path / "inputs")
    with pytest.raises(ProcessingError, match="pixel"):
        run_local(
            manifest.root,
            tmp_path / "pixels",
            TransformConfig(2, 2),
            1,
            limits=replace(LOCAL_LIMITS, max_output_pixels=4),
        )
    entry = replace(
        manifest.entries[1], relative_path=manifest.entries[0].relative_path.upper()
    )
    ambiguous = replace(
        manifest, entries=(manifest.entries[0], entry, manifest.entries[2])
    )
    monkeypatch.setattr(runner, "build_manifest", Mock(return_value=ambiguous))
    with pytest.raises(ProcessingError, match="ambiguous"):
        run_local(manifest.root, tmp_path / "names", TransformConfig(2, 2), 1)
    assert not (tmp_path / "names").exists()


def test_host_workload_lock_excludes_operational_admission(tmp_path: Path) -> None:
    manifest = make_manifest(tmp_path / "inputs")
    with experiment_lock(), pytest.raises(BenchmarkError, match="Another benchmark"):
        run_local(manifest.root, tmp_path / "output", TransformConfig(2, 2), 1)
    assert not (tmp_path / "output").exists()


@pytest.mark.spawn
@pytest.mark.parametrize(
    "task", [raise_in_worker, exit_in_worker, interrupt_in_worker, crash_in_worker]
)
def test_catastrophic_worker_errors_leave_unknown_work_and_no_children(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, task: object
) -> None:
    manifest = make_manifest(tmp_path / "inputs")
    monkeypatch.setattr(operational, "process_image", task)
    before = {child.pid for child in multiprocessing.active_children()}
    result = run_local(manifest.root, tmp_path / "output", TransformConfig(2, 2), 2)
    assert result.state == "FAILED"
    assert result.error_code == "worker_failure"
    assert not result.cancel_requested
    assert result.counts["unknown"] > 0
    assert sum(result.counts.values()) == 3
    assert inspect_execution(tmp_path / "output") == result
    assert {child.pid for child in multiprocessing.active_children()} <= before
    assert not list((tmp_path / "output").glob(".tachyron-*.tmp"))
    assert "private" not in json.dumps(result.to_dict())


@pytest.mark.spawn
def test_terminal_publication_failure_retains_acknowledgements_across_process_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = make_manifest(tmp_path / "inputs")
    publish = runner.publish_bytes

    def fail_terminal(directory: Path, name: str, data: bytes) -> None:
        if name == "execution.json":
            raise OSError("secret storage detail")
        publish(directory, name, data)

    monkeypatch.setattr(runner, "publish_bytes", fail_terminal)
    with pytest.raises(OSError, match="secret storage detail"):
        run_local(manifest.root, tmp_path / "output", TransformConfig(2, 2), 2)
    partial = inspect_execution(tmp_path / "output")
    assert partial.state == "UNCONFIRMED"
    assert partial.cancel_requested is None
    assert partial.processing_seconds is None
    assert partial.counts["succeeded"] == 3
    completed = subprocess.run(  # noqa: S603 -- Fixed module and isolated fixture directory.
        [
            sys.executable,
            "-B",
            "-m",
            "tachyron.execution.cli",
            "inspect",
            str(tmp_path / "output"),
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert completed.returncode == 1
    assert json.loads(completed.stdout)["state"] == "UNCONFIRMED"


@pytest.mark.spawn
def test_receipt_write_failure_never_claims_complete_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = make_manifest(tmp_path / "inputs")
    monkeypatch.setattr(
        runner, "publish_outcome", Mock(side_effect=OSError("private storage failure"))
    )
    result = run_local(manifest.root, tmp_path / "output", TransformConfig(2, 2), 1)
    assert result.state == "FAILED"
    assert result.error_code == "receipt_failure"
    assert sum(result.counts.values()) == len(manifest.entries)
    assert inspect_execution(tmp_path / "output") == result


@pytest.mark.spawn
@pytest.mark.parametrize(
    "corruption", ["state", "counts", "boolean", "owner", "path", "nan", "extra"]
)
def test_receipts_fail_closed_on_corrupt_terminal_evidence(
    tmp_path: Path, corruption: str
) -> None:
    manifest = make_manifest(tmp_path / "inputs")
    run_local(manifest.root, tmp_path / "output", TransformConfig(2, 2), 1)
    path = tmp_path / "output" / "execution.json"
    data = cast("dict[str, object]", json.loads(path.read_text()))
    if corruption == "state":
        data["state"] = "QUEUED"
    elif corruption == "counts":
        data["counts"] = {"succeeded": 2}
    elif corruption == "boolean":
        data["workers"] = True
    elif corruption == "owner":
        data["execution_id"] = "00000000-0000-4000-8000-000000000000"
    elif corruption == "path":
        items = cast("list[dict[str, object]]", data["items"])
        items[0]["relative_path"] = "../private"
    elif corruption == "nan":
        data["processing_seconds"] = float("nan")
    else:
        data["unexpected"] = "private"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ProcessingError, match="invalid or unavailable"):
        inspect_execution(tmp_path / "output")


def test_cli_sanitizes_unavailable_and_invalid_inputs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        cli.main(
            [
                "run",
                str(tmp_path / "private"),
                "--output",
                str(tmp_path / "out"),
                "--workers",
                "1",
            ]
        )
        == 2
    )
    assert "private" not in capsys.readouterr().err
    assert cli.main(["inspect", str(tmp_path / "private")]) == 2
    assert "private" not in capsys.readouterr().err


@pytest.mark.spawn
def test_decoded_image_budget_is_enforced_per_item(tmp_path: Path) -> None:
    manifest = make_manifest(tmp_path / "inputs")
    result = run_local(
        manifest.root,
        tmp_path / "output",
        TransformConfig(2, 2),
        1,
        limits=replace(LOCAL_LIMITS, max_pixels=1),
    )
    assert result.state == "FAILED"
    assert result.counts["failed"] == 3
    assert all(
        item.outcome is not None and item.outcome.error_code == "resource_limit"
        for item in result.items
    )


@pytest.mark.spawn
def test_real_pool_cancellation_after_acknowledgement_drains_and_reaps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = make_manifest(tmp_path / "inputs")
    for index in range(3, 24):
        (manifest.root / f"{index}.png").write_bytes(
            (manifest.root / "0.png").read_bytes()
        )
    cancelled = Event()
    publish = runner.publish_outcome

    def acknowledge(
        directory: Path, identifier: str, index: int, outcome: ImageOutcome
    ) -> None:
        publish(directory, identifier, index, outcome)
        cancelled.set()

    monkeypatch.setattr(runner, "publish_outcome", acknowledge)
    before = {child.pid for child in multiprocessing.active_children()}
    result = run_local(
        manifest.root,
        tmp_path / "output",
        TransformConfig(2, 2),
        2,
        cancelled=cancelled.is_set,
    )
    assert result.state == "CANCELLED"
    assert 1 <= result.counts["succeeded"] <= 4
    assert result.counts["failed"] == result.counts["unknown"] == 0
    assert result.counts["not_started"] >= 20
    assert sum(result.counts.values()) == 24
    assert inspect_execution(tmp_path / "output") == result
    assert {child.pid for child in multiprocessing.active_children()} <= before


@pytest.mark.parametrize(
    ("first_state", "cancel_requested", "error_code", "state", "error"),
    [
        ("SUCCEEDED", False, None, "COMPLETED", None),
        ("SUCCEEDED", True, None, "COMPLETED", None),
        ("SUCCEEDED", False, "worker_failure", "FAILED", "worker_failure"),
        ("SUCCEEDED", True, "receipt_failure", "FAILED", "receipt_failure"),
        ("FAILED", False, None, "FAILED", "image_failure"),
        ("FAILED", True, None, "FAILED", "image_failure"),
        ("FAILED", True, "worker_failure", "FAILED", "worker_failure"),
        ("FAILED", True, "receipt_failure", "FAILED", "receipt_failure"),
        ("UNKNOWN", False, None, "FAILED", "worker_failure"),
        ("UNKNOWN", True, None, "CANCELLED", None),
        ("NOT_STARTED", False, None, "FAILED", "worker_failure"),
        ("NOT_STARTED", True, None, "CANCELLED", None),
        ("UNKNOWN", True, "worker_failure", "FAILED", "worker_failure"),
        ("NOT_STARTED", True, "receipt_failure", "FAILED", "receipt_failure"),
    ],
)
def test_terminal_classification_preserves_failure_precedence(
    tmp_path: Path,
    first_state: str,
    cancel_requested: bool,
    error_code: str | None,
    state: str,
    error: str | None,
) -> None:
    manifest = make_manifest(tmp_path / "inputs")
    reference = run_sequential(manifest, tmp_path / "reference", TransformConfig(2, 2))
    outcomes: list[ImageOutcome | None] = list(reference.outcomes)
    outcomes[0] = {
        "SUCCEEDED": outcomes[0],
        "FAILED": ImageOutcome(
            manifest.entries[0].relative_path, None, "invalid_image"
        ),
        "UNKNOWN": None,
        "NOT_STARTED": None,
    }[first_state]
    accounting = operational.PoolAccounting(
        tuple(outcomes),
        frozenset({0}) if first_state == "NOT_STARTED" else frozenset(),
        cancel_requested,
        error_code,
    )
    result = runner._result("execution", manifest, 2, accounting, 0.25)
    assert (result.state, result.error_code) == (state, error)
    assert result.cancel_requested is cancel_requested
    assert result.items[0].state == first_state
    assert [item.state for item in result.items[1:]] == ["SUCCEEDED", "SUCCEEDED"]
    assert sum(result.counts.values()) == len(manifest.entries)
    assert result.processing_seconds == pytest.approx(0.25, rel=0, abs=0)
