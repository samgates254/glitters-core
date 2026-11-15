"""HTTP middleware for GLITTERS CORE."""

from __future__ import annotations

import re
import uuid

from config.context import request_id_var

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def resolve_request_id(header_value: str | None) -> str:
    """Accept a caller id only when it cannot break a log line."""

    if header_value and _REQUEST_ID_RE.fullmatch(header_value):
        return header_value
    return uuid.uuid4().hex


class RequestIdMiddleware:
    """Attach one request id to the request, the response, and the log context."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = resolve_request_id(request.headers.get("X-Request-ID"))
        request.request_id = request_id
        token = request_id_var.set(request_id)
        try:
            response = self.get_response(request)
        finally:
            request_id_var.reset(token)
        response["X-Request-ID"] = request_id
        return response
