# Copyright (c) 2026 Hans Valerie
"""HTTP transport for Tachyron; benchmark behavior belongs outside this module."""

from typing import Literal

from fastapi import FastAPI

app = FastAPI(title="Tachyron")


@app.get("/health")
async def health() -> dict[str, Literal["ok"]]:
    """Report process liveness without starting benchmark work."""
    return {"status": "ok"}
