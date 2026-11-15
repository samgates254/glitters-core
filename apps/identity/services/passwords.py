"""Password change and reset."""

from __future__ import annotations

from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.identity.domain import (
    CURRENT_PASSWORD_FAILED,
    GENERIC_RESET,
    RECOVERABLE_STATUSES,
    TOKEN_INVALID,
    AccountStatus,
    normalize_email,
)
from apps.identity.errors import IdentityError
from apps.identity.models import TokenPurpose, User
from apps.identity.services.audit import record_event
from apps.identity.services.credentials import revoke_browser_sessions, revoke_mobile_credentials
from apps.identity.services.delivery import send_password_reset
from apps.identity.services.secrets import PASSWORD_RESET_TTL
from apps.identity.services.tokens import burn_token, issue_token, load_live_token

RESET_MESSAGE = GENERIC_RESET


def request_password_reset(email: str, request=None) -> None:
    user = User.objects.filter(email=normalize_email(email)).first()
    if user is None or user.status not in RECOVERABLE_STATUSES:
        record_event(user if user is not None else None, "password_reset_ignored", request)
        return
    raw = issue_token(user, TokenPurpose.PASSWORD_RESET, PASSWORD_RESET_TTL)
    send_password_reset(user, raw)
    record_event(user, "password_reset_requested", request)


def confirm_password_reset(*, token: str, password: str, request=None) -> None:
    with transaction.atomic():
        identity_token = load_live_token(token, TokenPurpose.PASSWORD_RESET)
        user = User.objects.select_for_update().get(pk=identity_token.user_id)
        if user.status == AccountStatus.SUSPENDED:
            raise IdentityError(TOKEN_INVALID, status_code=400)
        _validate_new_password(password, user)
        user.set_password(password)
        if user.status in {AccountStatus.DEACTIVATED, AccountStatus.SECURITY_RESTRICTED}:
            user.status = AccountStatus.ACTIVE
        user.save()
        burn_token(identity_token)
    revoke_mobile_credentials(user)
    revoke_browser_sessions(user)
    record_event(user, "password_reset_completed", request)


def change_password(user, *, current_password: str, new_password: str, request=None) -> None:
    if not user.check_password(current_password):
        raise IdentityError(CURRENT_PASSWORD_FAILED, status_code=400)
    _validate_new_password(new_password, user)
    user.set_password(new_password)
    user.save()
    keep = None
    if request is not None and request.session.get("_auth_user_id"):
        update_session_auth_hash(request, user)
        keep = request.session.session_key
    revoke_browser_sessions(user, keep_session_key=keep)
    revoke_mobile_credentials(user)
    record_event(user, "password_changed", request)


def _validate_new_password(password: str, user) -> None:
    try:
        validate_password(password, user)
    except ValidationError as exc:
        message = exc.messages[0] if getattr(exc, "messages", None) else "Password is too weak."
        raise IdentityError(str(message), status_code=400) from exc
