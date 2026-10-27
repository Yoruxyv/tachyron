# Copyright (c) 2026 Hans Valerie
"""Controlled benchmark evidence and tiny image fixtures for backend tests."""

from pathlib import Path

from PIL import Image

from tachyron.benchmarking.models import (
    BenchmarkConfig,
    Context,
    Environment,
    Experiment,
    RunRecord,
    Telemetry,
    make_plan,
)
from tachyron.processing import (
    DatasetManifest,
    ResourceLimits,
    TransformConfig,
    build_manifest,
)


def example_experiment(repetitions: int = 2) -> Experiment:
    """Controlled arithmetic evidence only; never used by production reporting."""
    config = BenchmarkConfig(repetitions=repetitions, tiny=True, seed=19)
    environment = Environment(
        python_version="test",
        pillow_version="test",
        psutil_version="test",
        tachyron_version="test",
        os_name="test",
        os_release="test",
        architecture="test",
        host_id="b" * 64,
        source_hash="c" * 64,
    )
    context = Context(
        "a" * 64,
        10,
        100,
        TransformConfig(2, 2),
        ResourceLimits(),
        environment,
        True,
        0.1,
    )
    plan = make_plan(config, "00000000-0000-4000-8000-000000000001")
    durations = {1: (10.0, 14.0), 2: (4.0, 8.0), 4: (2.0, 4.0), 8: (2.0, 2.0)}
    records = tuple(
        RunRecord(
            step=step,
            context_id=context.digest,
            started_at="2026-01-01T00:00:00+00:00",
            ended_at="2026-01-01T00:00:01+00:00",
            elapsed_seconds=999.0
            if step.warmup
            else durations[step.workers][(step.repetition - 1) % 2],
            succeeded=10,
            failed=0,
            status="success",
            error_code=None,
            telemetry=Telemetry(),
        )
        for step in plan
    )
    return Experiment(
        "00000000-0000-4000-8000-000000000001",
        context,
        config,
        plan,
        records,
        "success",
    )


def make_manifest(root: Path, *, corrupt: bool = False) -> DatasetManifest:
    root.mkdir()
    for index in range(3):
        with Image.new("RGB", (4, 3), (index * 55, 60, 70)) as image:
            image.save(root / f"{index}.png")
    if corrupt:
        (root / "bad.png").write_bytes(b"invalid image")
    return build_manifest(root)
