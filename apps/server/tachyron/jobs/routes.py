# Copyright (c) 2026 Hans Valerie
"""HTTP contracts over the lifespan-owned coordinator and durable history."""

from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response

from tachyron.jobs.coordinator import AdmissionError, Coordinator
from tachyron.jobs.models import (
    TERMINAL,
    DatasetInfo,
    ErrorResponse,
    HistoryDetail,
    HistoryItem,
    Job,
    Metrics,
    RunRequest,
)

router = APIRouter(
    prefix="/api",
    responses={
        status: {"model": ErrorResponse} for status in (404, 409, 422, 429, 503)
    },
)


def coordinator(request: Request) -> Coordinator:
    """Resolve only the coordinator created for this ASGI lifespan."""
    return cast("Coordinator", request.state.coordinator)


Manager = Annotated[Coordinator, Depends(coordinator)]
SubmissionKey = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9._-]+$",
    ),
]


@router.get("/datasets")
def datasets(manager: Manager) -> list[DatasetInfo]:
    """List frozen allowlisted workloads and safe discovery failures."""
    return manager.datasets


@router.post("/jobs", status_code=202)
def start_job(
    body: RunRequest,
    manager: Manager,
    response: Response,
    idempotency_key: SubmissionKey = None,
) -> Job:
    """Accept a bounded queued comparison; 202 never promises completion."""
    job = manager.submit(body, idempotency_key)
    response.headers["Location"] = f"/api/jobs/{job.id}"
    return job


@router.get("/jobs/{job_id}")
def job_status(job_id: UUID, manager: Manager) -> Job:
    """Poll durable state and known batch accounting without starting work."""
    return manager.get(str(job_id))


@router.post("/jobs/{job_id}/cancel", status_code=202)
def cancel_job(job_id: UUID, manager: Manager) -> Job:
    """Request cooperative cancellation; active batches drain before stopping."""
    return manager.cancel(str(job_id))


@router.get("/jobs/{job_id}/metrics")
def job_metrics(job_id: UUID, manager: Manager) -> Metrics:
    """Return successful core metrics; incomplete/unsuccessful jobs return 409."""
    job = manager.get(str(job_id))
    if job.state != "COMPLETED":
        raise AdmissionError("metrics_unavailable", 409)
    result = manager.store.result(job.id)
    if result is None or result.experiment.status != "success":
        raise AdmissionError("history_unavailable", 503)
    return result


@router.get("/benchmarks")
def history(
    manager: Manager,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> list[HistoryItem]:
    """List bounded terminal history without mixing independent experiments."""
    items: list[HistoryItem] = []
    for job in manager.store.history(limit, offset):
        result = manager.store.result(job.id)
        items.append(
            HistoryItem(job=job, summaries=result.summaries if result else None)
        )
    return items


@router.get("/benchmarks/{job_id}")
def history_detail(job_id: UUID, manager: Manager) -> HistoryDetail:
    """Read a saved comparison or explicit partial failure/cancellation evidence."""
    job = manager.get(str(job_id))
    if job.state not in TERMINAL:
        raise AdmissionError("history_not_terminal", 409)
    return HistoryDetail(job=job, result=manager.store.result(job.id))
