"""Reject authenticated API calls from accounts that can no longer sign in."""

from __future__ import annotations

from django.contrib.auth import logout
from django.http import JsonResponse

from apps.identity.domain import ACCOUNT_BLOCKED, AccountStatus


class ActiveAccountMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith("/api/v1/health/"):
            return self.get_response(request)
        user = getattr(request, "user", None)
        if not getattr(user, "is_authenticated", False):
            return self.get_response(request)
        reason = self._block_reason(request, user)
        if reason is None:
            return self.get_response(request)
        logout(request)
        if request.path.startswith("/api/"):
            return JsonResponse(
                {
                    "errors": {"detail": reason},
                    "request_id": getattr(request, "request_id", None),
                },
                status=403,
            )
        return self.get_response(request)

    def _block_reason(self, request, user) -> str | None:
        if getattr(user, "status", AccountStatus.ACTIVE) != AccountStatus.ACTIVE:
            return ACCOUNT_BLOCKED
        if request.path.startswith("/api/") and (user.is_staff or user.is_superuser):
            if not user.has_confirmed_mfa():
                return "Multi-factor authentication is required."
        return None
