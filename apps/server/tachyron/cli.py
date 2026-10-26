# Copyright (c) 2026 Hans Valerie
"""Minimal sequential-processing adapter, with no benchmark or HTTP dependency."""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from tachyron.processing import (
    ProcessingError,
    TransformConfig,
    build_manifest,
    run_sequential,
)


def main(argv: Sequence[str] | None = None) -> int:
    """Run real processing; return 0 for success, 1 for image failures, 2 for setup.

    Args:
        argv: CLI arguments, or the process arguments when omitted.

    Returns:
        An exit status reflecting the complete batch outcome. Interruptions return
        130 and preserve completed outputs; argument syntax errors exit with 2.
    """
    parser = argparse.ArgumentParser(
        description="Sequentially resize and grayscale PNG/JPEG images."
    )
    parser.add_argument("dataset", type=Path, help="Explicit input dataset directory")
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="New output directory outside the dataset (parent must exist)",
    )
    parser.add_argument("--width", type=int, default=256)
    parser.add_argument("--height", type=int, default=256)
    args = parser.parse_args(argv)
    try:
        config = TransformConfig(width=args.width, height=args.height)
        manifest = build_manifest(args.dataset)
        result = run_sequential(manifest, args.output, config)
        sys.stdout.write(json.dumps(result.to_dict(), sort_keys=True) + "\n")
    except ProcessingError as error:
        sys.stderr.write(f"error: {error.code}: {error}\n")
        return 2
    except ValueError as error:
        sys.stderr.write(f"error: {error}\n")
        return 2
    except KeyboardInterrupt:
        sys.stderr.write("error: interrupted; completed outputs have been retained\n")
        return 130
    except OSError:
        sys.stderr.write(
            "error: report output cannot be written; completed outputs have been retained\n"
        )
        return 2
    return 1 if result.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
