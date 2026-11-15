"""API presentation helpers shared by every version."""

from __future__ import annotations

from rest_framework.views import exception_handler


def glitters_exception_handler(exc, context):
    """Return a stable error envelope and the request id."""

    response = exception_handler(exc, context)
    if response is None:
        return None
    request = context.get("request")
    response.data = {
        "errors": response.data,
        "request_id": getattr(request, "request_id", None),
    }
    return response
