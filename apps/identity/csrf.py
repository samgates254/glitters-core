"""JSON response for CSRF failures raised before a DRF view runs."""

from __future__ import annotations

from django.http import JsonResponse


def csrf_failure(request, reason=""):
    del reason
    return JsonResponse(
        {
            "errors": {"detail": "CSRF verification failed."},
            "request_id": getattr(request, "request_id", None),
        },
        status=403,
    )
