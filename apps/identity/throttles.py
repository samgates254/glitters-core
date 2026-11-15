"""Request throttles for authentication endpoints."""

from __future__ import annotations

import hashlib

from django.conf import settings
from rest_framework.throttling import SimpleRateThrottle

from apps.identity.domain import normalize_email


class IdentityThrottle(SimpleRateThrottle):
    def allow_request(self, request, view):
        if not getattr(settings, "IDENTITY_THROTTLE", True):
            return True
        return super().allow_request(request, view)

    def get_cache_key(self, request, view):
        ident = self.get_ident(request) or "unknown"
        return self.cache_format % {"scope": self.scope, "ident": ident}


class LoginThrottle(IdentityThrottle):
    scope = "auth_login"

    def get_cache_key(self, request, view):
        ident = self.get_ident(request) or "unknown"
        email = ""
        data = request.data if isinstance(getattr(request, "data", None), dict) else {}
        email = normalize_email(str(data.get("email", "")))
        digest = hashlib.sha256(email.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]
        return f"throttle_{self.scope}_{ident}_{digest}"


class RegisterThrottle(IdentityThrottle):
    scope = "auth_register"


class PasswordResetThrottle(IdentityThrottle):
    scope = "auth_password_reset"


class VerifyThrottle(IdentityThrottle):
    scope = "auth_verify"


class MfaThrottle(IdentityThrottle):
    scope = "auth_mfa"


class PhoneThrottle(IdentityThrottle):
    scope = "auth_phone"
