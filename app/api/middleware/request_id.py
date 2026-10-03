"""Request-ID middleware (ARCHITECT §11.4, P-15).

Generates a UUID4 ``request_id`` for every incoming HTTP request, stores it
in ``contextvars`` so every log entry within the request carries it, and
returns it as the ``X-Request-ID`` response header.
"""

from __future__ import annotations

import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.observability.logging import request_id_ctx, trace_id_ctx


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Attach a unique ``request_id`` to every HTTP request/response cycle."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = str(uuid.uuid4())
        rid_token = request_id_ctx.set(request_id)
        tid_token = trace_id_ctx.set(request_id)  # placeholder until OTel (§11.2)
        try:
            response = await call_next(request)
        finally:
            trace_id_ctx.reset(tid_token)
            request_id_ctx.reset(rid_token)
        response.headers["X-Request-ID"] = request_id
        return response
