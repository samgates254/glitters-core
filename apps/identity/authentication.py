"""Bearer access tokens for native clients. Browser clients use the session."""

from __future__ import annotations

from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from apps.identity.domain import ACCOUNT_BLOCKED, TOKEN_INVALID, AccountStatus
from apps.identity.errors import IdentityError
from apps.identity.services.credentials import authenticate_access_token


class BearerCredentialAuthentication(BaseAuthentication):
    keyword = "Bearer"

    def authenticate_header(self, request):
        return self.keyword

    def authenticate(self, request):
        header = request.headers.get("Authorization", "")
        if not header:
            return None
        prefix, _, raw = header.partition(" ")
        if prefix != self.keyword:
            return None
        if not raw.strip():
            raise AuthenticationFailed(TOKEN_INVALID)
        try:
            user = authenticate_access_token(raw.strip())
        except IdentityError as exc:
            raise AuthenticationFailed(TOKEN_INVALID) from exc
        if user.status != AccountStatus.ACTIVE:
            raise AuthenticationFailed(ACCOUNT_BLOCKED)
        if (user.is_staff or user.is_superuser) and not user.has_confirmed_mfa():
            raise AuthenticationFailed("Multi-factor authentication is required.")
        return (user, raw.strip())
