# Copyright (c) 2026 Hans Valerie
"""Local browser request boundaries; native local clients use the explicit header."""

from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class LocalRequests:
    """Reject untrusted hosts and browser mutations before consuming request bodies."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        coordinator = scope.get("state", {}).get("coordinator")
        if coordinator is None:
            await self.app(scope, receive, send)
            return
        settings = coordinator.settings
        try:
            authority = urlsplit("//" + request.headers.get("host", ""))
            host = authority.hostname
            if (
                len(request.headers.getlist("host")) != 1
                or authority.username is not None
                or authority.password is not None
                or authority.path
                or authority.query
                or authority.fragment
                or authority.port == 0
            ):
                host = None
        except ValueError:
            host = None
        code = ""
        if host not in settings.allowed_hosts:
            code = "invalid_host"
        elif request.method in {
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
        } and scope["path"].startswith("/api/"):
            origin = request.headers.get("origin")
            if (
                request.headers.get("x-tachyron-request") != "1"
                or len(request.headers.getlist("x-tachyron-request")) != 1
                or len(request.headers.getlist("origin")) > 1
                or (origin is not None and origin not in settings.allowed_origins)
                or request.headers.get("sec-fetch-site") == "cross-site"
            ):
                code = "request_forbidden"
        if code:
            response = JSONResponse(
                {"detail": code}, status_code=403, headers={"Cache-Control": "no-store"}
            )
            await response(scope, receive, send)
        else:
            await self.app(scope, receive, send)
