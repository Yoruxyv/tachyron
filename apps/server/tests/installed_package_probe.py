# Copyright (c) 2026 Hans Valerie
"""Smoke a built distribution with isolated Python, outside the repository.

Run directly, without pytest or the dev extra. CI installs the wheel and sdist
into separate environments, checks each base install, then adds its API extra.
"""

import asyncio
import importlib.util
import json
import subprocess
import sys
import sysconfig
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

import tachyron
from tachyron.benchmarking.exports import read_experiment
from tachyron.benchmarking.stats import summarize

assert sys.flags.isolated
assert tachyron.__file__ is not None
assert Path(tachyron.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())

if sys.argv[1:] == ["--api"]:
    from tachyron.api import create_app, health

    assert asyncio.run(health()) == {"status": "ok"}
    assert "/api/jobs" in create_app().openapi()["paths"]
    raise SystemExit(0)


def command(name: str, *arguments: str, cwd: Path) -> dict[str, object]:
    """Exercise the installed console entry point with bounded execution."""
    suffix = ".exe" if sys.platform == "win32" else ""
    executable = Path(sysconfig.get_path("scripts")) / (name + suffix)
    result = subprocess.run(  # noqa: S603 -- Installed entry point, fixed arguments, no shell.
        [str(executable), *arguments],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    document: dict[str, object] = json.loads(result.stdout)
    return document


def check_base(root: Path) -> None:
    """Verify optional isolation, real spawn, and deterministic installed exports."""
    for name in ("fastapi", "pydantic", "uvicorn"):
        assert importlib.util.find_spec(name) is None, name
    dataset = root / "input"
    dataset.mkdir()
    with Image.new("RGB", (3, 2), "red") as image:
        image.save(dataset / "image.png")
    reports: list[dict[str, object]] = []
    for mode, arguments in (("sequential", ()), ("parallel", ("--workers", "2"))):
        output = root / mode
        report = command(
            "tachyron-process",
            str(dataset),
            "--output",
            str(output),
            "--width",
            "2",
            "--height",
            "2",
            *arguments,
            cwd=root,
        )
        assert (report["succeeded"], report["failed"]) == (1, 0)
        reports.append(report)
        with Image.open(next(output.glob("*.png"))) as processed:
            assert processed.size == (2, 2)
            assert processed.mode == "L"
    reports[1].pop("execution")
    assert reports[0] == reports[1]
    experiment = root / "experiment"
    measured = command(
        "tachyron-benchmark",
        "run",
        str(dataset),
        "--output",
        str(experiment),
        "--tiny",
        "--width",
        "2",
        "--height",
        "2",
        cwd=root,
    )
    assert measured["status"] == "success"
    evidence = read_experiment(experiment / "runs.json")
    assert len(evidence.records) == 8
    assert [row.workers for row in summarize(evidence)] == [1, 2, 4, 8]
    comparison = root / "comparison"
    recomputed = command(
        "tachyron-benchmark",
        "compare",
        str(experiment / "runs.json"),
        "--output",
        str(comparison),
        cwd=root,
    )
    assert measured == recomputed
    for name in ("runs.json", "runs.csv", "workers.csv", "experiment.json"):
        assert (experiment / name).read_bytes() == (comparison / name).read_bytes()


def main() -> None:
    """Fail if imports escape the install or a built-package contract breaks."""
    assert not sys.argv[1:]
    with TemporaryDirectory(prefix="tachyron-installed-") as directory:
        root = Path(directory)
        assert not root.is_relative_to(Path(__file__).resolve().parents[3])
        check_base(root)


if __name__ == "__main__":
    main()
