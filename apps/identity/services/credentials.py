"""Browser sessions and mobile credentials."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from django.contrib.auth import login
from django.contrib.sessions.models import Session
from django.db import transaction
from django.utils import timezone

from apps.identity.domain import TOKEN_INVALID
from apps.identity.errors import IdentityError
from apps.identity.models import ApiCredential, AuthSessionLink
from apps.identity.services.audit import record_event
from apps.identity.services.secrets import (
    ACCESS_TOKEN_TTL,
    REFRESH_TOKEN_TTL,
    expiry,
    hash_secret,
    new_token,
)


@dataclass(frozen=True)
class IssuedCredential:
    access_token: str
    refresh_token: str
    expires_in: int


def start_browser_session(request, user) -> None:
    login(request, user)
    request.session.cycle_key()
    AuthSessionLink.objects.filter(session_key=request.session.session_key).delete()
    AuthSessionLink.objects.create(user=user, session_key=request.session.session_key)


def issue_mobile_credential(user, *, family_id: uuid.UUID | None = None) -> IssuedCredential:
    access = new_token()
    refresh = new_token()
    ApiCredential.objects.create(
        user=user,
        family_id=family_id or uuid.uuid4(),
        access_hash=hash_secret(access),
        refresh_hash=hash_secret(refresh),
        access_expires_at=expiry(ACCESS_TOKEN_TTL),
        refresh_expires_at=expiry(REFRESH_TOKEN_TTL),
        client_label="mobile",
    )
    return IssuedCredential(
        access_token=access,
        refresh_token=refresh,
        expires_in=int(ACCESS_TOKEN_TTL.total_seconds()),
    )


def authenticate_access_token(raw: str):
    now = timezone.now()
    credential = (
        ApiCredential.objects.select_related("user")
        .filter(access_hash=hash_secret(raw), revoked_at__isnull=True, access_expires_at__gt=now)
        .first()
    )
    if credential is None:
        raise IdentityError(TOKEN_INVALID, status_code=401)
    return credential.user


def rotate_refresh_token(raw: str, request=None) -> IssuedCredential:
    now = timezone.now()
    digest = hash_secret(raw)
    with transaction.atomic():
        credential = (
            ApiCredential.objects.select_for_update()
            .select_related("user")
            .filter(refresh_hash=digest)
            .first()
        )
        if credential is None:
            outcome = "missing"
            user = None
            issued = None
        elif credential.revoked_at is not None or credential.refresh_expires_at <= now:
            # A reused refresh token revokes every credential in its family.
            # The rejection is raised after this transaction commits.
            outcome = "replay" if credential.revoked_at is not None else "expired"
            user = credential.user
            issued = None
            if outcome == "replay":
                ApiCredential.objects.filter(
                    family_id=credential.family_id,
                    revoked_at__isnull=True,
                ).update(revoked_at=now)
        else:
            outcome = "rotated"
            credential.revoked_at = now
            credential.save(update_fields=["revoked_at"])
            user = credential.user
            issued = issue_mobile_credential(user, family_id=credential.family_id)
    if outcome != "rotated":
        if user is not None:
            record_event(user, "credential_replayed", request)
        raise IdentityError(TOKEN_INVALID, status_code=401)
    record_event(user, "credential_rotated", request)
    return issued


def revoke_mobile_credentials(user, *, family_id=None) -> None:
    now = timezone.now()
    queryset = ApiCredential.objects.filter(user=user, revoked_at__isnull=True)
    if family_id is not None:
        queryset = queryset.filter(family_id=family_id)
    queryset.update(revoked_at=now)


def revoke_access_token(raw: str) -> None:
    now = timezone.now()
    ApiCredential.objects.filter(access_hash=hash_secret(raw), revoked_at__isnull=True).update(
        revoked_at=now
    )


def revoke_browser_sessions(user, *, keep_session_key: str | None = None) -> None:
    links = AuthSessionLink.objects.filter(user=user)
    if keep_session_key:
        links = links.exclude(session_key=keep_session_key)
    keys = list(links.values_list("session_key", flat=True))
    if keys:
        Session.objects.filter(session_key__in=keys).delete()
        AuthSessionLink.objects.filter(session_key__in=keys).delete()


def end_browser_session(request) -> None:
    key = request.session.session_key
    user = request.user if getattr(request.user, "is_authenticated", False) else None
    if key:
        AuthSessionLink.objects.filter(session_key=key).delete()
    from django.contrib.auth import logout

    logout(request)
    if user is not None:
        record_event(user, "logout", request)
