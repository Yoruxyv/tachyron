# Copyright (c) 2026 Hans Valerie
"""Trusted local execution/inspection CLI; no HTTP path admission or API dependency."""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from tachyron.benchmarking.models import BenchmarkError
from tachyron.execution import inspect_execution, run_local
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
    run.add_argument("dataset", type=Path)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--workers", type=int, choices=(1, 2, 4, 8), required=True)
    run.add_argument("--width", type=int, default=DEFAULT_TRANSFORM.width)
    run.add_argument("--height", type=int, default=DEFAULT_TRANSFORM.height)
    inspect = commands.add_parser(
        "inspect", help="Read committed evidence; never resume or infer from PNGs"
    )
    inspect.add_argument("directory", type=Path)
    args = parser.parse_args(argv)
    try:
        result = (
            inspect_execution(args.directory)
            if args.command == "inspect"
            else run_local(
                args.dataset,
                args.output,
                TransformConfig(args.width, args.height),
                args.workers,
            )
        )
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
    summary = result.to_dict()
    del summary["items"]
    sys.stdout.write(json.dumps(summary, sort_keys=True, allow_nan=False) + "\n")
    if result.state == "CANCELLED":
        return 130
    return 0 if result.state == "COMPLETED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
