# Copyright (c) 2026 Hans Valerie
"""Check truthful CLI counts, sanitized errors, and subprocess exit behavior."""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
from PIL import Image

from tachyron import cli
from tachyron.cli import main
from tachyron.workers import WorkerExecutionError


def test_cli_workers_are_opt_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    dataset = tmp_path / "input"
    dataset.mkdir()
    with Image.new("RGB", (2, 2), "red") as image:
        image.save(dataset / "a.png")
    parallel = Mock(side_effect=AssertionError("default command must stay sequential"))
    monkeypatch.setattr(cli, "run_parallel", parallel)
    assert main([str(dataset), "--output", str(tmp_path / "sequential")]) == 0
    parallel.assert_not_called()
    capsys.readouterr()


def test_cli_pool_failure_is_sanitized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    dataset = tmp_path / "input"
    dataset.mkdir()
    with Image.new("RGB", (2, 2), "red") as image:
        image.save(dataset / "a.png")
    monkeypatch.setattr(
        cli,
        "run_parallel",
        Mock(side_effect=WorkerExecutionError("private worker trace")),
    )
    assert (
        main([str(dataset), "--output", str(tmp_path / "run"), "--workers", "2"]) == 2
    )
    captured = capsys.readouterr()
    assert "Parallel execution failed" in captured.err
    assert "private" not in captured.err
    assert not captured.out


@pytest.mark.parametrize(
    ("valid", "invalid", "status"), [(2, 0, 0), (1, 1, 1), (0, 1, 1)]
)
def test_cli_counts_success_partial_and_total_failure(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    valid: int,
    invalid: int,
    status: int,
) -> None:
    dataset = tmp_path / "input"
    dataset.mkdir()
    for index in range(valid):
        with Image.new("RGB", (2, 2), "red") as image:
            image.save(dataset / f"good-{index}.png")
    for index in range(invalid):
        (dataset / f"bad-{index}.png").write_bytes(b"invalid")
    args = [
        str(dataset),
        "--output",
        str(tmp_path / "run"),
        "--width",
        "2",
        "--height",
        "3",
    ]
    assert main(args) == status
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["succeeded"] == valid
    assert report["failed"] == invalid
    assert not captured.err


def test_cli_validation_has_no_output_side_effects(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "run"
    assert main([str(tmp_path / "missing"), "--output", str(output)]) == 2
    captured = capsys.readouterr()
    assert "error:" in captured.err
    assert str(tmp_path) not in captured.err
    assert not output.exists()


def test_module_invocation_and_bad_arguments(tmp_path: Path) -> None:
    result = subprocess.run(  # noqa: S603 -- Fixed installed module, argument list, no shell.
        [
            sys.executable,
            "-m",
            "tachyron.cli",
            str(tmp_path),
            "--output",
            str(tmp_path / "run"),
            "--width",
            "0",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 2
    assert "error:" in result.stderr
    assert "Traceback" not in result.stderr
    assert not (tmp_path / "run").exists()


def test_cli_interrupt_has_explicit_exit_and_no_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "build_manifest", Mock(side_effect=KeyboardInterrupt))
    assert main([str(tmp_path), "--output", str(tmp_path / "run")]) == 130
    assert "interrupted" in capsys.readouterr().err
    assert not (tmp_path / "run").exists()


@pytest.mark.parametrize(
    ("failure", "expected"), [(KeyboardInterrupt, 130), (BrokenPipeError, 2)]
)
def test_cli_output_failure_keeps_report_and_returns_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: type[BaseException],
    expected: int,
) -> None:
    dataset = tmp_path / "input"
    dataset.mkdir()
    with Image.new("RGB", (2, 2), "red") as image:
        image.save(dataset / "a.png")
    output = tmp_path / "run"
    with monkeypatch.context() as patch:
        # Keep the stream interface used by Python 3.14 argparse color detection.
        patch.setattr(cli.sys.stdout, "write", Mock(side_effect=failure))
        try:
            code = main(
                [str(dataset), "--output", str(output), "--width", "1", "--height", "1"]
            )
        except KeyboardInterrupt:
            pytest.fail("CLI output interruption must return 130")
    assert code == expected
    captured = capsys.readouterr()
    assert "error:" in captured.err
    assert "Traceback" not in captured.err
    assert str(tmp_path) not in captured.err
    report = json.loads((output / "run.json").read_text(encoding="utf-8"))
    assert report["succeeded"] == 1
    assert report["failed"] == 0
