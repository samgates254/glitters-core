"""Email and phone verification."""

from __future__ import annotations

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.identity.domain import (
    CODE_INVALID,
    GENERIC_RESEND,
    PHONE_UNAVAILABLE,
    AccountStatus,
    normalize_email,
    normalize_phone,
)
from apps.identity.errors import IdentityError
from apps.identity.models import TokenPurpose, User
from apps.identity.services.audit import record_event
from apps.identity.services.delivery import send_email_verification, send_phone_code
from apps.identity.services.lifecycle import change_status
from apps.identity.services.secrets import EMAIL_TOKEN_TTL, PHONE_CODE_ATTEMPTS, PHONE_CODE_TTL
from apps.identity.services.tokens import (
    consume_numeric_code,
    consume_token,
    issue_token,
    register_numeric_failure,
)

RESEND_MESSAGE = GENERIC_RESEND


def resend_email_verification(email: str, request=None) -> None:
    user = User.objects.filter(email=normalize_email(email)).first()
    if user is None or user.email_verified_at is not None:
        return
    if user.status != AccountStatus.PENDING_VERIFICATION:
        return
    raw = issue_token(user, TokenPurpose.EMAIL_VERIFICATION, EMAIL_TOKEN_TTL)
    send_email_verification(user, raw)
    record_event(user, "email_verification_resent", request)


def verify_email(token: str, request=None) -> User:
    identity_token = consume_token(token, TokenPurpose.EMAIL_VERIFICATION)
    user = identity_token.user
    user.email_verified_at = timezone.now()
    if user.status == AccountStatus.PENDING_VERIFICATION:
        change_status(user, AccountStatus.ACTIVE)
    else:
        user.save()
    record_event(user, "email_verified", request)
    return user


def set_phone(user, phone: str, request=None) -> None:
    if not str(phone or "").strip():
        user.phone_e164 = ""
        user.phone_verified_at = None
        user.save()
        record_event(user, "phone_cleared", request)
        return
    try:
        normalized = normalize_phone(phone)
    except ValueError as exc:
        raise IdentityError(str(exc), status_code=400) from exc
    if user.phone_e164 == normalized and user.phone_verified_at is not None:
        return
    user.phone_e164 = normalized
    user.phone_verified_at = None
    user.save()
    raw = issue_token(
        user,
        TokenPurpose.PHONE_VERIFICATION,
        PHONE_CODE_TTL,
        subject=normalized,
        numeric=True,
    )
    send_phone_code(user, normalized, raw)
    record_event(user, "phone_verification_requested", request)


def verify_phone(user, code: str, request=None) -> None:
    try:
        identity_token = consume_numeric_code(user, code, TokenPurpose.PHONE_VERIFICATION)
    except IdentityError:
        register_numeric_failure(
            user,
            TokenPurpose.PHONE_VERIFICATION,
            limit=PHONE_CODE_ATTEMPTS,
        )
        raise IdentityError(CODE_INVALID, status_code=400) from None
    if identity_token.subject != user.phone_e164:
        raise IdentityError(CODE_INVALID, status_code=400)
    if (
        User.objects.filter(phone_e164=user.phone_e164, phone_verified_at__isnull=False)
        .exclude(pk=user.pk)
        .exists()
    ):
        raise IdentityError(PHONE_UNAVAILABLE, status_code=400)
    user.phone_verified_at = timezone.now()
    try:
        with transaction.atomic():
            user.save()
            User.objects.filter(
                phone_e164=user.phone_e164,
                phone_verified_at__isnull=True,
            ).exclude(pk=user.pk).update(phone_e164="")
    except IntegrityError as exc:
        raise IdentityError(PHONE_UNAVAILABLE, status_code=400) from exc
    record_event(user, "phone_verified", request)
