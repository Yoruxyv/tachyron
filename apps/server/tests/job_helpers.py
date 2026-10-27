# Copyright (c) 2026 Hans Valerie
"""Shared API submission and bounded polling for job/history integration tests."""

from time import monotonic, sleep

import pytest
from fastapi.testclient import TestClient

from tachyron.jobs.models import Job

BODY: dict[str, object] = {
    "dataset_id": "tiny",
    "width": 2,
    "height": 2,
    "tiny": True,
    "repetitions": 1,
}


def terminal(client: TestClient, identifier: str) -> Job:
    """Bound test polling without imposing a hardware performance threshold."""
    deadline = monotonic() + 20
    while monotonic() < deadline:
        response = client.get(f"/api/jobs/{identifier}")
        assert response.status_code == 200, response.text
        job = Job.model_validate(response.json())
        if job.state in ("COMPLETED", "FAILED", "CANCELLED"):
            return job
        sleep(0.01)
    pytest.fail("Job did not reach a terminal state")
