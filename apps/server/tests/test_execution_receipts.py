# Copyright (c) 2026 Hans Valerie
"""Fail closed on damaged operational admission and partial acknowledgements."""

import json
from pathlib import Path
from typing import cast

import pytest

from tachyron.execution import inspect_execution, run_local
from tachyron.processing import ProcessingError, TransformConfig
from tests.benchmark_helpers import make_manifest


@pytest.mark.parametrize(
    "corruption", ["pipeline", "config", "policy", "timestamp", "extra", "duplicate"]
)
def test_invalid_admission_is_not_trusted(tmp_path: Path, corruption: str) -> None:
    manifest = make_manifest(tmp_path / "input")
    output = tmp_path / "output"
    run_local(manifest.root, output, TransformConfig(2, 2), 1, cancelled=lambda: True)
    path = output / "admission.json"
    data = cast("dict[str, object]", json.loads(path.read_text()))
    if corruption == "pipeline":
        data["pipeline"] = "unknown"
    elif corruption == "config":
        data["config"] = {"width": True, "height": 2, "output_format": "PNG"}
    elif corruption == "policy":
        policy = cast("dict[str, object]", data["limits"])
        policy["max_files"] = 10001
    elif corruption == "timestamp":
        data["created_at"] = "2026-10-29T00:00:00"
    elif corruption == "extra":
        data["extra"] = 1
    text = json.dumps(data)
    if corruption == "duplicate":
        text = text.replace(
            '"schema_version": 1', '"schema_version": 1, "schema_version": 1'
        )
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ProcessingError):
        inspect_execution(output)


def test_unacknowledged_files_never_imply_success(tmp_path: Path) -> None:
    manifest = make_manifest(tmp_path / "input")
    output = tmp_path / "output"
    run_local(manifest.root, output, TransformConfig(2, 2), 1, cancelled=lambda: True)
    (output / "execution.json").unlink()
    (output / "unrelated.png").write_bytes(b"not evidence")
    result = inspect_execution(output)
    assert result.state == "UNCONFIRMED"
    assert result.counts == {
        "succeeded": 0,
        "failed": 0,
        "unknown": 3,
        "not_started": 0,
    }
    assert result.cancel_requested is None


@pytest.mark.parametrize("corruption", ["owner", "index", "mapping", "oversize"])
def test_invalid_partial_acknowledgement_is_rejected(
    tmp_path: Path, corruption: str
) -> None:
    manifest = make_manifest(tmp_path / "input")
    output = tmp_path / "output"
    result = run_local(
        manifest.root, output, TransformConfig(2, 2), 1, cancelled=lambda: True
    )
    (output / "execution.json").unlink()
    data: dict[str, object] = {
        "execution_id": result.execution_id,
        "index": 0,
        "outcome": {
            "relative_path": "0.png",
            "output_name": None,
            "error_code": "invalid_image",
            "input_size": None,
        },
    }
    if corruption == "owner":
        data["execution_id"] = "foreign"
    elif corruption == "index":
        data["index"] = True
    elif corruption == "mapping":
        outcome = cast("dict[str, object]", data["outcome"])
        outcome["relative_path"] = "../outside.png"
    else:
        data["oversize"] = "x" * 4096
    (output / "records" / "00000.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ProcessingError):
        inspect_execution(output)
