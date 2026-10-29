# Copyright (c) 2026 Hans Valerie
"""Bounded multipart transport; complete bodies finish independently of the client."""

import asyncio
import re
from collections.abc import AsyncIterator, Callable
from dataclasses import asdict
from time import monotonic
from typing import Annotated, ParamSpec, TypeVar
from uuid import UUID

from fastapi import APIRouter, Header, Request, Response
from python_multipart.multipart import parse_options_header
from starlette.requests import ClientDisconnect

from tachyron.imports.models import DatasetImportError, Operation
from tachyron.imports.service import Session
from tachyron.jobs.routes import Manager

router = APIRouter(prefix="/api/dataset-imports")
P = ParamSpec("P")
T = TypeVar("T")


@router.get("/policy")
def policy(manager: Manager) -> dict[str, object]:
    """Expose operational limits without promising hardware performance."""
    return {
        "schema_version": 1,
        **asdict(manager.catalog.limits),
        "metadata_bytes": 512 * 1024,
        "formats": ["PNG", "JPEG"],
    }


async def owned_thread(
    function: Callable[P, T], *args: P.args, **kwargs: P.kwargs
) -> T:
    """Do not abandon a mutating thread when its ASGI owner is cancelled."""
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    interrupted = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            interrupted = True
    result = task.result()
    if interrupted:
        raise asyncio.CancelledError
    return result


@router.post("")
async def upload(
    request: Request,
    response: Response,
    manager: Manager,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
    metadata_digest: Annotated[str, Header(alias="X-Tachyron-Metadata-SHA256")],
) -> Operation:
    """Receive one exact workload; disconnect before EOF fails, after EOF continues."""
    identifier, boundary = upload_identity(request, idempotency_key, metadata_digest)
    response.headers["Location"] = f"/api/dataset-imports/{identifier}"
    previous = manager.catalog.replay(identifier, metadata_digest)
    if previous is not None:
        return previous
    return await receive_import(
        request, response, manager, identifier, metadata_digest, boundary
    )


def upload_identity(request: Request, key: str, digest: str) -> tuple[str, str]:
    """Validate framing and identity before creating a durable operation."""
    try:
        identifier = str(UUID(key))
    except ValueError as error:
        raise DatasetImportError("invalid_request") from error
    if identifier != key or re.fullmatch(r"[a-f0-9]{64}", digest) is None:
        raise DatasetImportError("invalid_request")
    content_type, parameters = parse_options_header(
        request.headers.get("content-type", "")
    )
    boundary = parameters.get(b"boundary", b"")
    if (
        content_type != b"multipart/form-data"
        or not 1 <= len(boundary) <= 70
        or any(value < 32 or value > 126 for value in boundary)
    ):
        raise DatasetImportError("invalid_multipart", 400)
    if request.headers.get("content-encoding", "identity") != "identity":
        raise DatasetImportError("invalid_multipart", 400)
    return identifier, boundary.decode("ascii")


async def receive_import(
    request: Request,
    response: Response,
    manager: Manager,
    identifier: str,
    metadata_digest: str,
    boundary: str,
) -> Operation:
    """Keep one request's owner through allocation, terminal transition and cleanup."""
    session: Session | None = None

    def allocate() -> Session | Operation:
        nonlocal session
        owner = manager.begin_import(identifier, metadata_digest, boundary)
        if isinstance(owner, Session):
            session = owner
        return owner

    try:
        owner = await owned_thread(allocate)
        if isinstance(owner, Operation):
            return owner
        session = owner
        await owned_thread(session.start)
        await receive_body(request, session)
        # No disconnect listener after EOF: lost response must not roll back a commit.
        document = await owned_thread(session.validate)
        result = await owned_thread(session.publish, document)
        response.status_code = 201
        return result
    except (ClientDisconnect, TimeoutError) as error:
        failure = DatasetImportError(
            "upload_interrupted"
            if isinstance(error, ClientDisconnect)
            else "upload_timeout",
            408,
        )
        if session is not None:
            manager.catalog.finish(identifier, failure.code)
        raise failure from error
    except DatasetImportError as error:
        if session is not None:
            manager.catalog.finish(identifier, error.code, error.index)
        raise
    except (EOFError, OSError, ValueError) as error:
        if session is not None:
            manager.catalog.finish(identifier, "worker_unavailable")
        raise DatasetImportError("worker_unavailable", 503) from error
    except asyncio.CancelledError:
        if session is not None:
            manager.catalog.finish(identifier, "server_interrupted")
        raise
    finally:
        if session is not None:
            try:
                await owned_thread(session.close)
            finally:
                manager.release_import(session)


@router.get("/{operation_id}")
def status(operation_id: UUID, manager: Manager) -> Operation:
    """Read durable state and optional current progress from its sole owner."""
    operation = manager.catalog.get(str(operation_id))
    session = manager.import_session
    if (
        session is not None
        and session.identifier == str(operation_id)
        and operation.state not in ("READY", "FAILED", "CANCELLED")
    ):
        return operation.model_copy(update={"progress": session.progress})
    return operation


@router.post("/{operation_id}/cancel", status_code=202)
def cancel(operation_id: UUID, manager: Manager, response: Response) -> Operation:
    """Cancellation intent is distinct from resource cleanup and terminal state."""
    operation = manager.cancel_import(str(operation_id))
    if operation.state in ("READY", "FAILED", "CANCELLED"):
        response.status_code = 200
    return operation


async def next_data(
    stream: AsyncIterator[bytes], session: Session, budget: float
) -> bytes:
    """Observe cancellation while an otherwise idle client still owns its socket."""
    task = asyncio.ensure_future(anext(stream))
    deadline = monotonic() + budget
    try:
        while True:
            if session.cancelled.is_set():
                raise DatasetImportError("import_cancelled", 409)
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise TimeoutError
            finished, _ = await asyncio.wait((task,), timeout=min(0.05, remaining))
            if finished:
                return task.result()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def receive_body(request: Request, session: Session) -> None:
    """Bound actual body bytes and client inactivity independently of pipe I/O."""
    limit = session.catalog.limits
    declared = request.headers.get("content-length")
    if declared is not None and (
        not declared.isdecimal() or int(declared) > limit.max_body_bytes
    ):
        raise DatasetImportError("upload_limit_exceeded", 413)
    stream = request.stream().__aiter__()
    total = 0
    deadline = monotonic() + limit.receive_seconds
    while True:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise DatasetImportError("upload_timeout", 408)
        try:
            data = await next_data(
                stream, session, min(limit.inactivity_seconds, remaining)
            )
        except StopAsyncIteration:
            return
        total += len(data)
        if total > limit.max_body_bytes:
            raise DatasetImportError("upload_limit_exceeded", 413)
        await owned_thread(session.feed, data)
