# Copyright (c) 2026 Hans Valerie
"""Exercise the public liveness contract without starting a network server."""

import pytest
from fastapi.testclient import TestClient

from tachyron.api import app


def test_health_reports_process_liveness() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize(
    ("method", "path", "status"),
    [("POST", "/health", 405), ("GET", "/api/jobs", 404)],
)
def test_unsupported_requests_are_not_reported_healthy(
    method: str, path: str, status: int
) -> None:
    with TestClient(app) as client:
        response = client.request(method, path)

    assert response.status_code == status
