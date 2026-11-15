"""Issue and consume one-time identity tokens."""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from apps.identity.domain import CODE_INVALID, TOKEN_INVALID
from apps.identity.errors import IdentityError
from apps.identity.models import IdentityToken
from apps.identity.services.secrets import expiry, hash_secret, new_numeric_code, new_token


def issue_token(user, purpose: str, ttl, *, subject: str = "", numeric: bool = False) -> str:
    raw = new_numeric_code() if numeric else new_token()
    digest = hash_secret(f"{user.pk}:{purpose}:{raw}" if numeric else raw)
    now = timezone.now()
    with transaction.atomic():
        IdentityToken.objects.filter(user=user, purpose=purpose, used_at__isnull=True).update(
            used_at=now
        )
        IdentityToken.objects.create(
            user=user,
            purpose=purpose,
            token_hash=digest,
            subject=subject,
            expires_at=expiry(ttl),
        )
    return raw


def consume_token(raw: str, purpose: str):
    return _consume(hash_secret(raw), purpose, TOKEN_INVALID)


def consume_numeric_code(user, raw: str, purpose: str):
    digest = hash_secret(f"{user.pk}:{purpose}:{str(raw).strip()}")
    token = _consume(digest, purpose, CODE_INVALID)
    if token.user_id != user.pk:
        raise IdentityError(CODE_INVALID, status_code=400)
    return token


def find_live_token(raw: str, purpose: str):
    now = timezone.now()
    token = IdentityToken.objects.filter(
        token_hash=hash_secret(raw),
        purpose=purpose,
    ).first()
    if token is None or token.used_at is not None or token.expires_at <= now:
        raise IdentityError(TOKEN_INVALID, status_code=400)
    return token


def load_live_token(raw: str, purpose: str):
    """Lock a still-valid token. Caller must run inside transaction.atomic."""

    now = timezone.now()
    token = (
        IdentityToken.objects.select_for_update()
        .filter(token_hash=hash_secret(raw), purpose=purpose)
        .first()
    )
    if token is None or token.used_at is not None or token.expires_at <= now:
        raise IdentityError(TOKEN_INVALID, status_code=400)
    return token


def burn_token(token) -> None:
    token.used_at = timezone.now()
    token.save(update_fields=["used_at"])


def register_numeric_failure(user, purpose: str, *, limit: int) -> None:
    now = timezone.now()
    with transaction.atomic():
        token = (
            IdentityToken.objects.select_for_update()
            .filter(user=user, purpose=purpose, used_at__isnull=True, expires_at__gt=now)
            .order_by("-created_at")
            .first()
        )
        if token is None:
            return
        token.attempt_count += 1
        if token.attempt_count >= limit:
            token.used_at = now
        token.save(update_fields=["attempt_count", "used_at"])


def _consume(digest: str, purpose: str, message: str):
    now = timezone.now()
    with transaction.atomic():
        token = (
            IdentityToken.objects.select_for_update()
            .filter(token_hash=digest, purpose=purpose)
            .first()
        )
        if token is None or token.used_at is not None or token.expires_at <= now:
            blocked = False
            token = None
        else:
            blocked = token.attempt_count >= 5 and purpose.endswith("verification")
            token.used_at = now
            token.save(update_fields=["used_at"])
    if token is None or blocked:
        raise IdentityError(message, status_code=400)
    return token
