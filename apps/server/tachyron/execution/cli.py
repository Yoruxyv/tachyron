# Copyright (c) 2026 Hans Valerie
"""Trusted local execution/inspection CLI; no HTTP path admission or API dependency."""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from tachyron.benchmarking.models import BenchmarkError
from tachyron.execution import (
    CalibrationPolicy,
    inspect_adaptive,
    inspect_execution,
    run_adaptive,
    run_local,
)
from tachyron.processing import ProcessingError, TransformConfig
from tachyron.processing.models import DEFAULT_TRANSFORM
from tachyron.workers import WorkerExecutionError


def main(argv: Sequence[str] | None = None) -> int:
    """Run once or inspect receipts; return 0/1/2/130 for success/failure/setup/cancel."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser(
        "run", help="Explicitly authorize and process a local PNG/JPEG root once"
    )
    adaptive = commands.add_parser(
        "adaptive", help="Calibrate bounded local samples, then execute once"
    )
    for operation in (run, adaptive):
        operation.add_argument("dataset", type=Path)
        operation.add_argument("--output", type=Path, required=True)
        operation.add_argument("--width", type=int, default=DEFAULT_TRANSFORM.width)
        operation.add_argument("--height", type=int, default=DEFAULT_TRANSFORM.height)
    run.add_argument("--workers", type=int, choices=(1, 2, 4, 8), required=True)
    adaptive.add_argument(
        "--fallback-workers", type=int, choices=(1, 2, 4, 8), default=1
    )
    adaptive.add_argument("--sample-files", type=int, default=32)
    adaptive.add_argument("--repetitions", type=int, default=3)
    adaptive.add_argument("--budget-seconds", type=float, default=60)
    adaptive.add_argument("--seed", type=int, default=2026)
    adaptive.add_argument("--budget-images", type=int, default=576)
    inspect = commands.add_parser(
        "inspect", help="Read committed evidence; never resume or infer from PNGs"
    )
    inspect.add_argument("directory", type=Path)
    commands.add_parser(
        "inspect-adaptive", help="Validate adaptive and linked execution receipts"
    ).add_argument("directory", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "inspect-adaptive":
            summary = inspect_adaptive(args.directory)
            state = summary["state"]
        else:
            if args.command == "inspect":
                result = inspect_execution(args.directory)
            elif args.command == "adaptive":
                result = run_adaptive(
                    args.dataset,
                    args.output,
                    TransformConfig(args.width, args.height),
                    policy=CalibrationPolicy(
                        args.sample_files,
                        args.repetitions,
                        args.budget_seconds,
                        args.seed,
                        args.fallback_workers,
                        args.budget_images,
                    ),
                )
            else:
                result = run_local(
                    args.dataset,
                    args.output,
                    TransformConfig(args.width, args.height),
                    args.workers,
                )
            summary = result.to_dict()
            summary.pop("items", None)
            state = result.state
    except KeyboardInterrupt:
        sys.stderr.write(
            "error: interrupted; no terminal completion is confirmed; inspect retained receipts\n"
        )
        return 130
    except ProcessingError as error:
        sys.stderr.write(f"error: {error.code}: {error}\n")
        return 2
    except BenchmarkError:
        sys.stderr.write(
            "error: workload_busy; another cooperating workload owns the host lock\n"
        )
        return 2
    except (ValueError, OSError, WorkerExecutionError):
        sys.stderr.write(
            "error: execution_unconfirmed; inspect retained receipts; no completion is promised\n"
        )
        return 2
    sys.stdout.write(json.dumps(summary, sort_keys=True, allow_nan=False) + "\n")
    if state == "CANCELLED":
        return 130
    return 0 if state == "COMPLETED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
