"""Time-based one-time passwords and recovery codes."""

from __future__ import annotations

import secrets

from django.core import signing
from django.db import transaction
from django.utils import timezone

from apps.identity.domain import CODE_INVALID, MFA_REQUIRED_FOR_STAFF
from apps.identity.errors import IdentityError
from apps.identity.models import MfaDevice, RecoveryCode, TokenPurpose
from apps.identity.services.secrets import MFA_CHALLENGE_ATTEMPTS, hash_secret
from apps.identity.services.tokens import burn_token, load_live_token
from apps.identity.totp import decode_secret, encode_secret, otpauth_uri, random_secret, verify_totp

_SIGNING_SALT = "glitters.mfa.v1"


def begin_enrollment(user) -> dict[str, str]:
    secret = random_secret()
    now = timezone.now()
    MfaDevice.objects.filter(
        user=user,
        confirmed_at__isnull=True,
        disabled_at__isnull=True,
    ).update(disabled_at=now)
    MfaDevice.objects.create(user=user, secret_signed=_seal(secret))
    return {
        "secret": encode_secret(secret),
        "otpauth_uri": otpauth_uri(secret=secret, account=user.email),
    }


def confirm_device(user, code: str) -> list[str]:
    device = (
        MfaDevice.objects.filter(user=user, confirmed_at__isnull=True, disabled_at__isnull=True)
        .order_by("-created_at")
        .first()
    )
    if device is None or not verify_totp(_open(device.secret_signed), code):
        raise IdentityError(CODE_INVALID, status_code=400)
    now = timezone.now()
    device.confirmed_at = now
    device.save(update_fields=["confirmed_at"])
    MfaDevice.objects.filter(user=user, disabled_at__isnull=True).exclude(pk=device.pk).update(
        disabled_at=now
    )
    return _replace_recovery_codes(user)


def disable_mfa(user, *, password: str, code: str) -> None:
    if user.is_staff or user.is_superuser:
        raise IdentityError(MFA_REQUIRED_FOR_STAFF, status_code=403)
    if not user.check_password(password):
        raise IdentityError("Current password is not correct.", status_code=400)
    if not _code_matches(user, code):
        raise IdentityError(CODE_INVALID, status_code=400)
    now = timezone.now()
    user.mfa_devices.filter(disabled_at__isnull=True).update(disabled_at=now)
    user.recovery_codes.filter(used_at__isnull=True).update(used_at=now)


def verify_challenge(raw_token: str, code: str):
    with transaction.atomic():
        token = load_live_token(raw_token, TokenPurpose.MFA_CHALLENGE)
        user = token.user
        if token.attempt_count >= MFA_CHALLENGE_ATTEMPTS:
            burn_token(token)
            failed = True
            client = ""
        elif not _code_matches(user, code):
            token.attempt_count += 1
            if token.attempt_count >= MFA_CHALLENGE_ATTEMPTS:
                token.used_at = timezone.now()
            token.save(update_fields=["attempt_count", "used_at"])
            failed = True
            client = ""
        else:
            client = token.subject or "web"
            burn_token(token)
            failed = False
    # Commit the attempt or burn before telling the client the code was rejected.
    if failed:
        raise IdentityError(CODE_INVALID, status_code=400)
    return user, client


def confirm_enrollment_token(raw_token: str, code: str):
    with transaction.atomic():
        token = load_live_token(raw_token, TokenPurpose.MFA_ENROLLMENT)
        codes = confirm_device(token.user, code)
        client = token.subject or "web"
        burn_token(token)
        return token.user, client, codes


def _code_matches(user, code: str) -> bool:
    device = (
        user.mfa_devices.filter(confirmed_at__isnull=False, disabled_at__isnull=True)
        .order_by("-confirmed_at")
        .first()
    )
    if device is not None and verify_totp(_open(device.secret_signed), code):
        return True
    compact = str(code or "").strip().lower().replace("-", "")
    if not compact:
        return False
    now = timezone.now()
    updated = RecoveryCode.objects.filter(
        user=user,
        code_hash=hash_secret(f"{user.pk}:{compact}"),
        used_at__isnull=True,
    ).update(used_at=now)
    return updated == 1


def _replace_recovery_codes(user) -> list[str]:
    now = timezone.now()
    RecoveryCode.objects.filter(user=user, used_at__isnull=True).update(used_at=now)
    displayed: list[str] = []
    for _ in range(8):
        raw = secrets.token_hex(8)
        RecoveryCode.objects.create(
            user=user,
            code_hash=hash_secret(f"{user.pk}:{raw}"),
        )
        displayed.append(f"{raw[:4]}-{raw[4:8]}-{raw[8:12]}-{raw[12:16]}")
    return displayed


def _seal(secret: bytes) -> str:
    return signing.dumps(encode_secret(secret), salt=_SIGNING_SALT)


def _open(signed: str) -> bytes:
    encoded = signing.loads(signed, salt=_SIGNING_SALT)
    return decode_secret(encoded)
