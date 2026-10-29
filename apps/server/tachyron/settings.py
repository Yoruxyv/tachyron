# Copyright (c) 2026 Hans Valerie
"""Operator-owned API settings, independent of coordinator lifecycle."""

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from tachyron.imports.models import ImportLimits


@dataclass(frozen=True)
class Settings:
    """Operator-owned configuration; HTTP clients cannot select filesystem paths."""

    datasets: dict[str, Path] = field(default_factory=dict[str, Path])
    data_dir: Path = Path("results/api")
    queue_capacity: int = 2
    dataset_dir: Path | None = None
    import_limits: ImportLimits = field(default_factory=ImportLimits)
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost", "::1")
    allowed_origins: tuple[str, ...] = (
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:8000",
        "http://127.0.0.1:4174",
        "http://127.0.0.1:4173",
    )

    def __post_init__(self) -> None:
        if len(self.datasets) > 32 or any(
            re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", name) is None
            for name in self.datasets
        ):
            raise ValueError("Allow at most 32 portable dataset identifiers")
        if type(self.queue_capacity) is not int or not 1 <= self.queue_capacity <= 16:
            raise ValueError("Waiting queue capacity must be between 1 and 16")

    @classmethod
    def from_environment(cls) -> "Settings":
        """Load a small explicit allowlist and an operator-owned history directory."""
        raw = os.environ.get("TACHYRON_DATASETS", "{}")
        if len(raw) > 16_384:
            raise ValueError("Dataset configuration is too large")
        configured: object = json.loads(raw)
        if not isinstance(configured, dict):
            raise TypeError("Dataset configuration must be an object")
        datasets: dict[str, Path] = {}
        for name, path in cast("dict[str, object]", configured).items():
            if not isinstance(name, str) or not isinstance(path, str):
                raise TypeError("Dataset identifiers and roots must be strings")
            datasets[name] = Path(path)
        return cls(
            datasets,
            Path(os.environ.get("TACHYRON_DATA_DIR", "results/api")),
            dataset_dir=Path(
                os.environ.get("TACHYRON_DATASET_DIR", "datasets/imported")
            ),
        )
