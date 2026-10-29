# Copyright (c) 2026 Hans Valerie
"""HTTP transport for Tachyron; benchmark behavior belongs outside this module."""

import asyncio
import sqlite3
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Literal, cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from tachyron.imports.models import DatasetImportError
from tachyron.imports.routes import router as import_router
from tachyron.jobs.coordinator import AdmissionError, Coordinator, Settings
from tachyron.jobs.routes import router
from tachyron.processing import ProcessingError
from tachyron.request_security import LocalRequests


async def health() -> dict[str, Literal["ok"]]:
    """Report process liveness without starting benchmark work."""
    return {"status": "ok"}


async def admission_error(_request: Request, error: Exception) -> JSONResponse:
    """Expose stable rejection codes without local paths or exception details."""
    rejection = cast("AdmissionError", error)  # Registered only for AdmissionError.
    return JSONResponse(
        {"detail": rejection.code},
        status_code=rejection.status,
        headers={"Retry-After": "1"} if rejection.status in (429, 503) else None,
    )


async def validation_error(_request: Request, _error: Exception) -> JSONResponse:
    """Do not reflect invalid request values, paths or credentials into errors."""
    return JSONResponse({"detail": "invalid_request"}, status_code=422)


async def processing_error(_request: Request, error: Exception) -> JSONResponse:
    """Preserve the processing core's safe validation codes."""
    rejection = cast("ProcessingError", error)  # Registered only for ProcessingError.
    return JSONResponse({"detail": rejection.code}, status_code=422)


async def storage_error(_request: Request, _error: Exception) -> JSONResponse:
    """Fail closed when durable acceptance or history cannot be guaranteed."""
    return JSONResponse(
        {"detail": "history_unavailable"}, status_code=503, headers={"Retry-After": "1"}
    )


async def import_error(_request: Request, error: Exception) -> JSONResponse:
    """Return allowlisted import codes without reflected input or decoder details."""
    rejection = cast("DatasetImportError", error)
    body: dict[str, object] = {"detail": rejection.code}
    if rejection.index is not None:
        body["file_index"] = rejection.index
    return JSONResponse(
        body,
        status_code=rejection.status,
        headers={"Retry-After": "1"} if rejection.status in (429, 503) else None,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create transport only; history, threads and pools belong to the lifespan.

    Args:
        settings: Operator allowlist/storage, or environment settings at startup.

    Returns:
        An app supporting one coordinator per history directory.
    """

    @asynccontextmanager
    async def lifespan(_application: FastAPI) -> AsyncGenerator[dict[str, Coordinator]]:
        manager = Coordinator(
            settings if settings is not None else Settings.from_environment()
        )
        # Startup discovery finishes before serving; no cancellable partial owner.
        manager.start()
        try:
            yield {"coordinator": manager}
        finally:
            shutdown = asyncio.create_task(asyncio.to_thread(manager.close))
            try:
                await asyncio.shield(shutdown)
            except asyncio.CancelledError:
                await shutdown
                raise

    application = FastAPI(title="Tachyron", version="0.1.0", lifespan=lifespan)
    application.add_api_route("/health", health, methods=["GET"])
    application.include_router(router)
    application.include_router(import_router)
    application.add_middleware(LocalRequests)
    application.add_exception_handler(DatasetImportError, import_error)
    application.add_exception_handler(AdmissionError, admission_error)
    application.add_exception_handler(RequestValidationError, validation_error)
    application.add_exception_handler(ProcessingError, processing_error)
    application.add_exception_handler(sqlite3.Error, storage_error)
    application.add_exception_handler(ValidationError, storage_error)
    return application


app = create_app()
