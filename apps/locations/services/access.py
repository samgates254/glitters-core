"""Account gate for location reads. This app does not import organizations."""

from __future__ import annotations

from django.contrib.auth.models import AbstractBaseUser

from apps.identity.domain import ACCOUNT_BLOCKED, AUTHENTICATION_REQUIRED, AccountStatus
from apps.locations.errors import LocationError


def ensure_active_account(user) -> None:
    if not isinstance(user, AbstractBaseUser) or not getattr(user, "is_authenticated", False):
        raise LocationError(AUTHENTICATION_REQUIRED, status_code=401)
    if getattr(user, "status", None) != AccountStatus.ACTIVE:
        raise LocationError(ACCOUNT_BLOCKED, status_code=403)
