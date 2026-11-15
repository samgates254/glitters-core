"""Secret helpers. Raw tokens are returned to the caller once and are not logged."""

from __future__ import annotations

import hashlib
import ipaddress
import secrets
from datetime import timedelta

from django.utils import timezone

ACCESS_TOKEN_TTL = timedelta(minutes=15)
REFRESH_TOKEN_TTL = timedelta(days=30)
EMAIL_TOKEN_TTL = timedelta(hours=24)
PASSWORD_RESET_TTL = timedelta(hours=1)
PHONE_CODE_TTL = timedelta(minutes=10)
MFA_CHALLENGE_TTL = timedelta(minutes=5)
MFA_ENROLLMENT_TTL = timedelta(minutes=10)
PHONE_CODE_ATTEMPTS = 5
MFA_CHALLENGE_ATTEMPTS = 5


def new_token() -> str:
    return secrets.token_urlsafe(32)


def new_numeric_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8"), usedforsecurity=False).hexdigest()


def client_ip(request) -> str | None:
    if request is None:
        return None
    raw = str(request.META.get("REMOTE_ADDR") or "")
    try:
        ipaddress.ip_address(raw)
    except ValueError:
        return None
    return raw


def user_agent(request) -> str:
    if request is None:
        return ""
    return str(request.META.get("HTTP_USER_AGENT") or "")[:200]


def expiry(delta: timedelta):
    return timezone.now() + delta
