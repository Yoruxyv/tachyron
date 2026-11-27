# Copyright (c) 2026 Hans Valerie
"""Explicit local operations and stateless bounded telemetry polling."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response

from tachyron.execution.live import LiveSnapshot
from tachyron.jobs.models import ErrorResponse
from tachyron.jobs.operations import OperationRequest
from tachyron.jobs.routes import Manager

router = APIRouter(
    prefix="/api/operations",
    responses={
        status: {"model": ErrorResponse} for status in (403, 404, 409, 422, 503)
    },
)


@router.post("", status_code=202)
def start_operation(
    body: OperationRequest, manager: Manager, response: Response
) -> LiveSnapshot:
    """Accept one registered operational workload into the coordinator."""
    snapshot = manager.submit_operation(body)
    response.headers["Location"] = f"/api/operations/{snapshot.run_id}"
    response.headers["Cache-Control"] = "no-store"
    return snapshot


@router.get("/{run_id}")
def operation_status(
    run_id: UUID,
    manager: Manager,
    response: Response,
    after: Annotated[int | None, Query(ge=0, le=2**63 - 1)] = None,
) -> LiveSnapshot:
    """Read a current snapshot and a bounded suffix after the supplied cursor."""
    response.headers["Cache-Control"] = "no-store"
    return manager.operation(str(run_id), after)


@router.post("/{run_id}/cancel", status_code=202)
def cancel_operation(
    run_id: UUID, manager: Manager, response: Response
) -> LiveSnapshot:
    """Request a cooperative stop and return the latest observed state."""
    response.headers["Cache-Control"] = "no-store"
    return manager.cancel_operation(str(run_id))
