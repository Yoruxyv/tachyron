# Copyright (c) 2026 Hans Valerie
"""API lifecycle, queue pressure and failures over real tiny image processing."""

import json
import multiprocessing
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event
from typing import cast
from unittest.mock import Mock

import psutil
import pytest
from fastapi.testclient import TestClient

from tachyron.api import create_app
from tachyron.benchmarking import runner
from tachyron.benchmarking.exports import read_experiment
from tachyron.jobs.coordinator import Coordinator, Settings
from tachyron.jobs.models import Job, Metrics
from tachyron.processing import (
    BatchResult,
    DatasetManifest,
    TransformConfig,
)
from tachyron.workers import WorkerExecutionError
from tests.benchmark_helpers import make_manifest, sequential_pool
from tests.job_helpers import BODY, terminal


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    manifest = make_manifest(tmp_path / "input")
    return Settings({"tiny": manifest.root}, tmp_path / "history", queue_capacity=1)


@pytest.fixture
def service(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[tuple[TestClient, Coordinator]]:
    monkeypatch.setattr(runner, "run_parallel", sequential_pool)
    with TestClient(create_app(settings)) as client:
        yield client, cast("Coordinator", client.app_state["coordinator"])


def test_completed_job_matches_cli_core_and_survives_restart(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner, "run_parallel", sequential_pool)
    with TestClient(create_app(settings)) as client:
        completed, metrics = _assert_completed(client)
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get(
            f"/api/jobs/{completed.id}"
        ).json() == completed.model_dump(mode="json")
        assert restarted.get(f"/api/jobs/{completed.id}/metrics").json() == metrics
        replay = restarted.post(
            "/api/jobs", json=BODY, headers={"Idempotency-Key": "same-run"}
        )
        assert replay.status_code == 202
        assert replay.json()["id"] == completed.id
        assert len(restarted.get("/api/benchmarks").json()) == 1


def _assert_completed(client: TestClient) -> tuple[Job, object]:
    manager = cast("Coordinator", client.app_state["coordinator"])
    response = client.post(
        "/api/jobs", json=BODY, headers={"Idempotency-Key": "same-run"}
    )
    assert response.status_code == 202
    accepted = Job.model_validate(response.json())
    assert accepted.state == "QUEUED"
    assert response.headers["location"] == f"/api/jobs/{accepted.id}"
    completed = terminal(client, accepted.id)
    assert completed.state == "COMPLETED"
    assert (completed.completed_batches, completed.total_batches) == (8, 8)
    assert (completed.succeeded_images, completed.failed_images) == (24, 0)
    assert completed.started_at is not None
    assert completed.ended_at is not None
    metrics = client.get(f"/api/jobs/{completed.id}/metrics")
    assert metrics.status_code == 200
    evidence = read_experiment(
        manager.directory / completed.id / "experiment/runs.json"
    )
    assert Metrics.model_validate(metrics.json()) == Metrics.from_experiment(evidence)
    assert len(client.get("/api/benchmarks").json()) == 1
    assert client.get("/api/benchmarks?offset=1").json() == []
    assert (
        client.get(f"/api/benchmarks/{completed.id}").json()["result"] == metrics.json()
    )

    return completed, metrics.json()


def test_bounded_admission_status_responsiveness_idempotency_and_cancellation(
    service: tuple[TestClient, Coordinator],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, manager = service
    entered, release = Event(), Event()

    def blocked(
        manifest: DatasetManifest,
        directory: Path,
        transform: TransformConfig,
        workers: int,
    ) -> BatchResult:
        entered.set()
        if not release.wait(10):
            raise WorkerExecutionError("test release timed out")
        return sequential_pool(manifest, directory, transform, workers)

    monkeypatch.setattr(runner, "run_parallel", blocked)
    try:
        active = client.post("/api/jobs", json=BODY).json()["id"]
        assert entered.wait(5)
        waiting = client.post(
            "/api/jobs", json=BODY, headers={"Idempotency-Key": "queued"}
        )
        assert waiting.status_code == 202
        queued = waiting.json()["id"]
        assert client.get(f"/api/jobs/{queued}").json()["state"] == "QUEUED"
        overloaded = client.post("/api/jobs", json=BODY)
        assert overloaded.status_code == 429
        assert overloaded.json() == {"detail": "queue_full"}
        assert overloaded.headers["retry-after"] == "1"
        replay = client.post(
            "/api/jobs", json=BODY, headers={"Idempotency-Key": "queued"}
        )
        assert replay.json()["id"] == queued
        conflict = client.post(
            "/api/jobs",
            json={**BODY, "width": 3},
            headers={"Idempotency-Key": "queued"},
        )
        assert conflict.status_code == 409
        for _ in range(3):
            assert client.get("/health").json() == {"status": "ok"}
            assert client.get(f"/api/jobs/{active}").json()["state"] == "RUNNING"
            assert client.get(f"/api/jobs/{active}/metrics").status_code == 409
        assert client.get("/api/benchmarks").json() == []
        assert client.get(f"/api/benchmarks/{active}").status_code == 409
        assert client.post(f"/api/jobs/{queued}/cancel").json()["state"] == "CANCELLED"
        assert not (manager.directory / queued).exists()
        cancelled = client.post(f"/api/jobs/{active}/cancel")
        assert cancelled.status_code == 202
        assert cancelled.json()["state"] == "RUNNING"
        assert cancelled.json()["cancel_requested"] is True
        assert not release.is_set()
    finally:
        release.set()
    stopped = terminal(client, active)
    assert stopped.state == "CANCELLED"
    assert stopped.completed_batches == 1
    assert stopped.succeeded_images == 3
    assert client.get(f"/api/jobs/{active}/metrics").status_code == 409
    partial = client.get(f"/api/benchmarks/{active}").json()["result"]
    assert partial["experiment"]["status"] == "interrupted"
    assert all(summary["speedup"] is None for summary in partial["summaries"])
    assert client.post(f"/api/jobs/{active}/cancel").json() == stopped.model_dump(
        mode="json"
    )
    assert manager._signals == {}


@pytest.mark.parametrize("failure", ["image", "pool", "provenance", "publication"])
def test_failures_keep_known_partial_counts_and_never_publish_success_metrics(
    service: tuple[TestClient, Coordinator],
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    client, manager = service
    if failure == "image":
        (manager.settings.datasets["tiny"] / "0.png").write_bytes(b"changed input")
    elif failure == "pool":
        monkeypatch.setattr(
            runner,
            "run_parallel",
            Mock(side_effect=WorkerExecutionError("secret-local-path")),
        )
    elif failure == "provenance":
        monkeypatch.setattr(
            runner,
            "_check_environment",
            Mock(side_effect=ValueError("secret-local-path")),
        )
    else:
        monkeypatch.setattr(
            runner, "publish_exports", Mock(side_effect=OSError("secret-local-path"))
        )
    response = client.post("/api/jobs", json=BODY)
    failed = terminal(client, response.json()["id"])
    assert failed.state == "FAILED"
    assert failed.error_code in ("image_failure", "execution_failure")
    assert client.get(f"/api/jobs/{failed.id}/metrics").status_code == 409
    assert "secret-local-path" not in client.get(f"/api/benchmarks/{failed.id}").text
    if failure == "image":
        assert (
            failed.completed_batches,
            failed.succeeded_images,
            failed.failed_images,
        ) == (1, 2, 1)
    elif failure in ("pool", "provenance"):
        assert failed.unknown_batches == 1
    else:
        assert failed.succeeded_images == 24
        assert failed.completed_batches == 8
    assert client.get("/health").status_code == 200


@pytest.mark.parametrize(
    "updates",
    [
        {"dataset_id": "../private"},
        {"dataset_id": "unknown"},
        {"workers": 3},
        {"width": True},
        {"width": "2"},
        {"width": 0},
        {"height": 4097},
        {"repetitions": 21},
        {"tiny": False},
        {"sampling_interval": 0},
        {"operation": "shell"},
        {"output": "secret-local-path"},
    ],
)
def test_request_validation_does_not_leak_inputs_or_create_jobs(
    service: tuple[TestClient, Coordinator],
    updates: dict[str, object],
) -> None:
    client, manager = service
    response = client.post("/api/jobs", json={**BODY, **updates})
    assert response.status_code == (
        404 if updates.get("dataset_id") == "unknown" else 422
    )
    assert "secret-local-path" not in response.text
    assert manager._signals == {}
    assert not list(manager.directory.glob("*/plan.json"))


def test_missing_jobs_and_bounded_query_validation(
    service: tuple[TestClient, Coordinator],
) -> None:
    client, _manager = service
    missing = "00000000-0000-4000-8000-000000000001"
    for suffix in ("", "/metrics"):
        assert client.get(f"/api/jobs/{missing}{suffix}").status_code == 404
    assert client.post(f"/api/jobs/{missing}/cancel").status_code == 404
    assert client.get(f"/api/benchmarks/{missing}").status_code == 404
    for query in ("limit=0", "limit=51", "offset=-1", "offset=100001"):
        assert client.get(f"/api/benchmarks?{query}").status_code == 422
    assert client.get("/api/jobs/not-a-uuid").status_code == 422
    assert (
        client.post(
            "/api/jobs", json=BODY, headers={"Idempotency-Key": "x" * 65}
        ).status_code
        == 422
    )


@pytest.mark.spawn
def test_api_real_spawn_comparison_and_pool_cleanup(settings: Settings) -> None:
    # Start POSIX's interpreter-owned resource tracker before measuring pool cleanup.
    with multiprocessing.get_context("spawn").Lock():
        before = {child.pid for child in psutil.Process().children(recursive=True)}
    with TestClient(create_app(settings)) as client:
        response = client.post("/api/jobs", json=BODY)
        assert response.status_code == 202
        job = terminal(client, response.json()["id"])
        assert job.state == "COMPLETED"
        metrics = client.get(f"/api/jobs/{job.id}/metrics").json()
        assert {
            record["step"]["workers"] for record in metrics["experiment"]["records"]
        } == {1, 2, 4, 8}
        assert all(
            record["elapsed_seconds"] > 0 for record in metrics["experiment"]["records"]
        )
        assert metrics["summaries"][0]["speedup"] == pytest.approx(1)
    assert (
        not {child.pid for child in psutil.Process().children(recursive=True)} - before
    )


def test_openapi_documents_typed_contracts_without_starting_workers(
    settings: Settings,
) -> None:
    app = create_app(settings)
    schema = app.openapi()
    assert not settings.data_dir.exists()
    assert "202" in schema["paths"]["/api/jobs"]["post"]["responses"]
    assert "429" in schema["paths"]["/api/jobs"]["post"]["responses"]
    for name in (
        "RunRequest",
        "Job",
        "Metrics",
        "HistoryDetail",
        "WorkerSummary",
        "ErrorResponse",
    ):
        assert name in schema["components"]["schemas"]


def test_concurrent_retries_admit_exactly_one_job(
    service: tuple[TestClient, Coordinator],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, manager = service
    entered, release = Event(), Event()

    def blocked(
        manifest: DatasetManifest,
        directory: Path,
        transform: TransformConfig,
        workers: int,
    ) -> BatchResult:
        entered.set()
        if not release.wait(10):
            raise WorkerExecutionError("test release timed out")
        return sequential_pool(manifest, directory, transform, workers)

    monkeypatch.setattr(runner, "run_parallel", blocked)
    barrier = Barrier(4)

    def submit(_index: int) -> str:
        barrier.wait(timeout=5)
        response = client.post(
            "/api/jobs", json=BODY, headers={"Idempotency-Key": "concurrent"}
        )
        assert response.status_code == 202
        return response.json()["id"]

    try:
        active = client.post("/api/jobs", json=BODY).json()["id"]
        assert entered.wait(5)
        with ThreadPoolExecutor(max_workers=4) as executor:
            identifiers = list(executor.map(submit, range(4)))
        assert len(set(identifiers)) == 1
        assert list(manager._queue) == [identifiers[0]]
        assert client.post("/api/jobs", json=BODY).status_code == 429
        client.post(f"/api/jobs/{active}/cancel")
        client.post(f"/api/jobs/{identifiers[0]}/cancel")
    finally:
        release.set()
    assert terminal(client, active).state == "CANCELLED"


def test_inconsistent_stored_metrics_fail_closed(
    service: tuple[TestClient, Coordinator],
) -> None:
    client, manager = service
    accepted = client.post("/api/jobs", json=BODY)
    job = terminal(client, accepted.json()["id"])
    result = client.get(f"/api/jobs/{job.id}/metrics").json()
    result["summaries"][0]["speedup"] = 999
    corrupted = json.dumps(result)
    with manager.store._lock, manager.store._connection:
        manager.store._connection.execute(
            "UPDATE jobs SET result=? WHERE id=?", (corrupted, job.id)
        )
    response = client.get(f"/api/jobs/{job.id}/metrics")
    assert response.status_code == 503
    assert response.json() == {"detail": "history_unavailable"}
