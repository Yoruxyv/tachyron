# Copyright (c) 2026 Hans Valerie
"""Small benchmark run/compare adapter; existing processing commands are unchanged."""

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

import psutil

from tachyron.benchmarking.exports import publish_exports, read_experiment
from tachyron.benchmarking.models import BenchmarkConfig, Experiment
from tachyron.benchmarking.runner import run_experiment
from tachyron.benchmarking.stats import summarize
from tachyron.processing import TransformConfig, build_manifest
from tachyron.workers import WorkerExecutionError


class Arguments(argparse.Namespace):
    """Typed adapter boundary for argparse's two focused commands."""

    command: str
    dataset: Path
    input: Path
    output: Path
    width: int
    height: int
    repetitions: int | None
    seed: int
    sampling_interval: float
    tiny: bool


def _arguments(argv: Sequence[str] | None) -> Arguments:
    parser = argparse.ArgumentParser(
        description="Measure reproducible spawn-pool batch scaling; no HTTP server required."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser(
        "run", help="Run one warm-up and five measured repetitions per worker count"
    )
    run.add_argument("dataset", type=Path)
    run.add_argument(
        "--output",
        required=True,
        type=Path,
        help="New experiment directory outside the dataset",
    )
    run.add_argument("--width", type=int, default=256)
    run.add_argument("--height", type=int, default=256)
    run.add_argument("--repetitions", type=int, help="5-20 normally; 1-20 with --tiny")
    run.add_argument("--seed", type=int, default=2026)
    run.add_argument("--sampling-interval", type=float, default=0.1)
    run.add_argument(
        "--tiny",
        action="store_true",
        help="Explicitly labeled tiny test workload; default one measured repetition",
    )
    compare = commands.add_parser(
        "compare", help="Validate raw runs.json and recompute a complete comparison"
    )
    compare.add_argument("input", type=Path)
    compare.add_argument(
        "--output",
        required=True,
        type=Path,
        help="New comparison export directory; parent must exist",
    )
    arguments = Arguments()
    parser.parse_args(argv, namespace=arguments)
    return arguments


def _run(arguments: Arguments) -> Experiment:
    repetitions = arguments.repetitions
    config = BenchmarkConfig(
        repetitions=repetitions
        if repetitions is not None
        else (1 if arguments.tiny else 5),
        seed=arguments.seed,
        sampling_interval=arguments.sampling_interval,
        tiny=arguments.tiny,
    )
    transform = TransformConfig(arguments.width, arguments.height)
    manifest = build_manifest(arguments.dataset)
    return run_experiment(manifest, arguments.output, transform, config)


def _compare(arguments: Arguments) -> Experiment:
    experiment = read_experiment(arguments.input)
    summarize(experiment)
    parent = arguments.output.parent.resolve(strict=True)
    destination = parent / arguments.output.name
    destination.mkdir(mode=0o700)
    publish_exports(experiment, destination)
    return experiment


def main(argv: Sequence[str] | None = None) -> int:
    """Run benchmark workflows, writing machine-readable status and truthful exits.

    Args:
        argv: Arguments, or process arguments when omitted.

    Returns:
        0 for a complete comparison, 1 for image failures, 2 for setup/execution/
        export failure, and 130 for interruption. Argparse syntax errors exit 2.
    """
    arguments = _arguments(argv)
    try:
        experiment = (
            _run(arguments) if arguments.command == "run" else _compare(arguments)
        )
        response: dict[str, object] = {
            "schema_version": 1,
            "experiment_id": experiment.experiment_id,
            "context_id": experiment.context.digest,
            "status": experiment.status,
            "tiny": experiment.config.tiny,
            "summaries": [
                asdict(row) for row in summarize(experiment, require_complete=False)
            ],
        }
        sys.stdout.write(json.dumps(response, sort_keys=True, allow_nan=False) + "\n")
    except KeyboardInterrupt:
        sys.stderr.write(
            "error: benchmark interrupted; retained evidence is incomplete\n"
        )
        return 130
    except (ValueError, WorkerExecutionError, OSError, psutil.Error):
        sys.stderr.write(
            "error: benchmark setup, execution, comparison or export failed; retained evidence may be incomplete\n"
        )
        return 2
    return 0 if experiment.status == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
