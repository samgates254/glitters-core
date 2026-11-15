"""Registration, profile edits, and self-service deactivation."""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.identity.domain import (
    CURRENT_PASSWORD_FAILED,
    GENERIC_REGISTER,
    AccountStatus,
    normalize_email,
    normalize_phone,
)
from apps.identity.errors import IdentityError
from apps.identity.models import TokenPurpose, User
from apps.identity.services.audit import record_event
from apps.identity.services.credentials import revoke_browser_sessions, revoke_mobile_credentials
from apps.identity.services.delivery import send_email_verification, send_existing_account_notice
from apps.identity.services.lifecycle import change_status
from apps.identity.services.secrets import EMAIL_TOKEN_TTL
from apps.identity.services.tokens import issue_token

REGISTERED_MESSAGE = GENERIC_REGISTER


def register_account(
    *,
    email: str,
    password: str,
    first_name: str = "",
    last_name: str = "",
    phone: str = "",
    request=None,
) -> None:
    normalized = normalize_email(email)
    if not normalized:
        raise IdentityError("An email address is required.", status_code=400)
    existing = User.objects.filter(email=normalized).first()
    if existing is not None:
        _answer_existing(existing, request)
        return
    phone_e164 = _optional_phone(phone)
    try:
        with transaction.atomic():
            user = User.objects.create_user(
                email=normalized,
                password=password,
                first_name=first_name.strip(),
                last_name=last_name.strip(),
                status=AccountStatus.PENDING_VERIFICATION,
                phone_e164=phone_e164,
            )
            raw = issue_token(user, TokenPurpose.EMAIL_VERIFICATION, EMAIL_TOKEN_TTL)
    except IntegrityError:
        return
    except ValidationError as exc:
        if _is_duplicate_email(exc):
            return
        raise IdentityError(_first_message(exc), status_code=400) from exc
    send_email_verification(user, raw)
    record_event(user, "registration_completed", request)


def update_profile(user, *, first_name: str, last_name: str) -> User:
    user.first_name = first_name.strip()
    user.last_name = last_name.strip()
    user.save()
    return user


def deactivate_account(user, password: str, request=None) -> None:
    if not user.check_password(password):
        raise IdentityError(CURRENT_PASSWORD_FAILED, status_code=400)
    change_status(user, AccountStatus.DEACTIVATED)
    revoke_mobile_credentials(user)
    revoke_browser_sessions(user)
    record_event(user, "account_deactivated", request)


def _answer_existing(user, request) -> None:
    if user.status == AccountStatus.PENDING_VERIFICATION and user.email_verified_at is None:
        raw = issue_token(user, TokenPurpose.EMAIL_VERIFICATION, EMAIL_TOKEN_TTL)
        send_email_verification(user, raw)
    else:
        send_existing_account_notice(user)
    record_event(user, "registration_duplicate", request)


def _optional_phone(phone: str) -> str:
    if not str(phone or "").strip():
        return ""
    try:
        return normalize_phone(phone)
    except ValueError as exc:
        raise IdentityError(str(exc), status_code=400) from exc


def _is_duplicate_email(exc: ValidationError) -> bool:
    error_dict = getattr(exc, "error_dict", {})
    for errors in error_dict.values():
        for error in errors:
            if getattr(error, "code", "") == "unique":
                return True
    return False


def _first_message(exc: ValidationError) -> str:
    messages = getattr(exc, "messages", None)
    if messages:
        return str(messages[0])
    return "The request is invalid."
