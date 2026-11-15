"""Credential checks and session or mobile credential establishment."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from django.contrib.auth.hashers import check_password, make_password

from apps.identity.domain import (
    ACCOUNT_BLOCKED,
    LOGIN_FAILED,
    VERIFY_EMAIL,
    AccountStatus,
    normalize_email,
)
from apps.identity.errors import IdentityError
from apps.identity.models import TokenPurpose, User
from apps.identity.services.audit import record_event
from apps.identity.services.credentials import (
    IssuedCredential,
    issue_mobile_credential,
    start_browser_session,
)
from apps.identity.services.mfa import confirm_enrollment_token, verify_challenge
from apps.identity.services.secrets import MFA_CHALLENGE_TTL, MFA_ENROLLMENT_TTL
from apps.identity.services.tokens import issue_token


@dataclass
class LoginResult:
    kind: str
    user: User | None = None
    challenge_token: str = ""
    enrollment_token: str = ""
    credential: IssuedCredential | None = None
    recovery_codes: list[str] = field(default_factory=list)


def login_user(*, email: str, password: str, client: str, request) -> LoginResult:
    user = _verified_password(email, password, request)
    _ensure_can_sign_in(user)
    if user.has_confirmed_mfa():
        token = issue_token(user, TokenPurpose.MFA_CHALLENGE, MFA_CHALLENGE_TTL, subject=client)
        record_event(user, "mfa_challenged", request)
        return LoginResult(kind="mfa", challenge_token=token)
    if user.is_staff or user.is_superuser:
        token = issue_token(
            user,
            TokenPurpose.MFA_ENROLLMENT,
            MFA_ENROLLMENT_TTL,
            subject=client,
        )
        record_event(user, "mfa_enrollment_required", request)
        return LoginResult(kind="enroll", enrollment_token=token)
    return _establish(user, client, request)


def complete_mfa(*, challenge_token: str, code: str, request) -> LoginResult:
    user, client = verify_challenge(challenge_token, code)
    _ensure_can_sign_in(user)
    record_event(user, "mfa_verified", request)
    return _establish(user, client, request)


def complete_enrollment(*, enrollment_token: str, code: str, request) -> LoginResult:
    user, client, codes = confirm_enrollment_token(enrollment_token, code)
    _ensure_can_sign_in(user)
    record_event(user, "mfa_confirmed", request)
    return _establish(user, client, request, recovery_codes=codes)


def _verified_password(email: str, password: str, request) -> User:
    user = User.objects.filter(email=normalize_email(email)).first()
    if user is None:
        check_password(password, _dummy_password_hash())
        record_event(None, "login_failed", request)
        raise IdentityError(LOGIN_FAILED, status_code=401)
    if not user.check_password(password):
        record_event(user, "login_failed", request)
        raise IdentityError(LOGIN_FAILED, status_code=401)
    return user


def _ensure_can_sign_in(user: User) -> None:
    if user.status == AccountStatus.PENDING_VERIFICATION:
        raise IdentityError(VERIFY_EMAIL, status_code=403)
    if user.status != AccountStatus.ACTIVE:
        raise IdentityError(ACCOUNT_BLOCKED, status_code=403)


def _establish(user, client: str, request, recovery_codes: list[str] | None = None) -> LoginResult:
    if client == "mobile":
        credential = issue_mobile_credential(user)
        record_event(user, "login_succeeded", request)
        return LoginResult(
            kind="mobile",
            user=user,
            credential=credential,
            recovery_codes=recovery_codes or [],
        )
    start_browser_session(request, user)
    record_event(user, "login_succeeded", request)
    return LoginResult(
        kind="session",
        user=user,
        recovery_codes=recovery_codes or [],
    )


@lru_cache(maxsize=1)
def _dummy_password_hash() -> str:
    return make_password("glitters-dummy-password-not-a-user")
