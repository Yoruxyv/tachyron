# Copyright (c) 2026 Hans Valerie
"""Characterize adapter defaults and operator boundaries before ownership changes."""

import hashlib
from dataclasses import asdict
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from tachyron import benchmark_cli, cli
from tachyron.api import create_app
from tachyron.benchmarking.exports import encode_document, experiment_document
from tachyron.benchmarking.models import BenchmarkConfig
from tachyron.imports.models import METADATA_BYTES, ImportLimits
from tachyron.jobs.coordinator import Settings
from tachyron.jobs.models import RunRequest
from tachyron.processing import TransformConfig
from tachyron.settings import Settings as OperatorSettings
from tests.benchmark_helpers import example_experiment, make_manifest


def test_settings_import_compatibility_and_independent_defaults() -> None:
    assert Settings is OperatorSettings
    first, second = Settings(), OperatorSettings()
    first.datasets["tiny"] = Path("input")
    assert second.datasets == {}


def test_default_request_and_controlled_evidence_remain_identical() -> None:
    request = RunRequest(dataset_id="tiny")
    assert request.model_dump_json() == (
        '{"dataset_id":"tiny","operation":"resize-grayscale-v1","width":256,'
        '"height":256,"repetitions":5,"seed":2026,"sampling_interval":0.1,"tiny":false}'
    )
    assert request.benchmark_config() == BenchmarkConfig()
    assert request.transform() == TransformConfig()
    # Frozen before the refactor; the fixture supplies controlled provenance.
    evidence = encode_document(experiment_document(example_experiment()))
    assert hashlib.sha256(evidence).hexdigest() == (
        "c048090fd551230bf53c2c900722a3d27620c8f33ec23cc512597919c9eff00f"
    )


@pytest.mark.parametrize(("tiny", "repetitions"), [(False, 5), (True, 1)])
def test_benchmark_cli_defaults_preserve_explicit_tiny_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tiny: bool, repetitions: int
) -> None:
    execute = Mock(return_value=example_experiment())
    monkeypatch.setattr(benchmark_cli, "run_experiment", execute)
    monkeypatch.setattr(benchmark_cli, "build_manifest", Mock())
    arguments = benchmark_cli._arguments(
        ["run", "input", "--output", str(tmp_path), *(["--tiny"] if tiny else [])]
    )
    benchmark_cli._run(arguments)
    assert execute.call_args.args[2] == TransformConfig()
    assert asdict(execute.call_args.args[3]) == {
        "repetitions": repetitions,
        "seed": 2026,
        "sampling_interval": 0.1,
        "tiny": tiny,
    }


def test_processing_cli_uses_transform_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = make_manifest(tmp_path / "input")
    execute = Mock()
    execute.return_value.to_dict.return_value = {}
    execute.return_value.failed = 0
    monkeypatch.setattr(cli, "run_sequential", execute)
    assert cli.main([str(manifest.root), "--output", str(tmp_path / "output")]) == 0
    assert execute.call_args.args[2] == TransformConfig(256, 256)
    assert capsys.readouterr().out == "{}\n"


def test_environment_fallbacks_and_explicit_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("TACHYRON_DATASETS", "TACHYRON_DATA_DIR", "TACHYRON_DATASET_DIR"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings.from_environment()
    assert settings.datasets == {}
    assert settings.data_dir == Path("results/api")
    assert settings.dataset_dir == Path("datasets/imported")
    assert settings.queue_capacity == 2
    assert Settings().dataset_dir is None
    monkeypatch.setenv("TACHYRON_DATA_DIR", "custom/history")
    monkeypatch.setenv("TACHYRON_DATASET_DIR", "custom/images")
    assert Settings.from_environment().data_dir == Path("custom/history")
    assert Settings.from_environment().dataset_dir == Path("custom/images")


@pytest.mark.parametrize("capacity", [0, 17, True, 1.0])
def test_queue_capacity_rejects_invalid_types_and_bounds(capacity: int) -> None:
    with pytest.raises(
        ValueError, match="Waiting queue capacity must be between 1 and 16"
    ):
        Settings(queue_capacity=capacity)


def test_injected_allowlists_and_policy_remain_authoritative(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path / "history",
        allowed_hosts=("operator.test",),
        allowed_origins=("http://operator.test:5173",),
    )
    with TestClient(create_app(settings), base_url="http://operator.test") as client:
        response = client.get("/api/dataset-imports/policy")
        assert response.status_code == 200
        assert response.json() == {
            "schema_version": 1,
            **asdict(ImportLimits()),
            "metadata_bytes": METADATA_BYTES,
            "formats": ["PNG", "JPEG"],
        }
        assert client.get("/health", headers={"Host": "127.0.0.1"}).status_code == 403
        for origin, status in (("http://operator.test:5173", 404), ("null", 403)):
            response = client.post(
                "/api/dataset-imports/00000000-0000-4000-8000-000000000001/cancel",
                headers={"X-Tachyron-Request": "1", "Origin": origin},
            )
            assert response.status_code == status
