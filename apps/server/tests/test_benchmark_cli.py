# Copyright (c) 2026 Hans Valerie
"""Benchmark CLI validation, sanitization and guarded real-process execution."""

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import psutil
import pytest

from tachyron import benchmark_cli
from tachyron.benchmarking.exports import publish_exports
from tests.benchmark_helpers import example_experiment, make_manifest


def test_compare_cli_recomputes_from_raw_evidence_and_rejects_failed_input(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    publish_exports(example_experiment(), source)
    assert (
        benchmark_cli.main(
            [
                "compare",
                str(source / "runs.json"),
                "--output",
                str(tmp_path / "comparison"),
            ]
        )
        == 0
    )
    response = json.loads(capsys.readouterr().out)
    assert response["status"] == "success"
    assert response["summaries"][0]["speedup"] == pytest.approx(1)
    failed = replace(example_experiment(), records=(), status="failed")
    failure_dir = tmp_path / "failed"
    failure_dir.mkdir()
    publish_exports(failed, failure_dir)
    assert (
        benchmark_cli.main(
            [
                "compare",
                str(failure_dir / "runs.json"),
                "--output",
                str(tmp_path / "invalid"),
            ]
        )
        == 2
    )
    assert not (tmp_path / "invalid").exists()


def test_benchmark_cli_validates_before_discovery_and_sanitizes_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        benchmark_cli.main(
            [
                "run",
                str(tmp_path / "missing"),
                "--output",
                str(tmp_path / "experiment"),
                "--repetitions",
                "1",
            ]
        )
        == 2
    )
    output = capsys.readouterr()
    assert not output.out
    assert str(tmp_path) not in output.err
    assert not (tmp_path / "experiment").exists()


@pytest.mark.spawn
def test_guarded_benchmark_cli_real_run_and_compare_without_http(
    tmp_path: Path,
) -> None:
    dataset = tmp_path / "input"
    make_manifest(dataset)
    command = [sys.executable, "-I", "-m", "tachyron.benchmark_cli"]
    run = subprocess.run(  # noqa: S603 -- Fixed installed module with generated paths, no shell.
        [
            *command,
            "run",
            str(dataset),
            "--output",
            str(tmp_path / "experiment"),
            "--tiny",
            "--width",
            "2",
            "--height",
            "2",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=90,
    )
    assert run.returncode == 0, run.stderr
    response = json.loads(run.stdout)
    assert response["status"] == "success"
    assert response["tiny"] is True
    assert len(response["summaries"]) == 4
    compared = subprocess.run(  # noqa: S603 -- Same trusted module and owned input/output paths.
        [
            *command,
            "compare",
            str(tmp_path / "experiment/runs.json"),
            "--output",
            str(tmp_path / "comparison"),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert compared.returncode == 0, compared.stderr
    assert json.loads(compared.stdout)["summaries"] == response["summaries"]


def test_benchmark_cli_sanitizes_denied_process_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = make_manifest(tmp_path / "input")
    monkeypatch.setattr(
        benchmark_cli,
        "run_experiment",
        Mock(side_effect=psutil.AccessDenied(123, name="private process detail")),
    )
    assert (
        benchmark_cli.main(
            [
                "run",
                str(manifest.root),
                "--output",
                str(tmp_path / "experiment"),
                "--tiny",
            ]
        )
        == 2
    )
    captured = capsys.readouterr()
    assert not captured.out
    assert "private process detail" not in captured.err
    assert str(tmp_path) not in captured.err
