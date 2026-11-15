"""Helpers for identity tests. This module is not a test module."""

from __future__ import annotations

import secrets

from django.core import mail, signing
from django.utils import timezone

from apps.identity.totp import encode_secret


def enable_confirmed_mfa(user):
    from apps.identity.models import MfaDevice

    MfaDevice.objects.create(
        user=user,
        secret_signed=signing.dumps(
            encode_secret(secrets.token_bytes(20)),
            salt="glitters.mfa.v1",
        ),
        confirmed_at=timezone.now(),
    )


def error_detail(response) -> str:
    payload = response.json()["errors"]["detail"]
    if isinstance(payload, list):
        return str(payload[0])
    return str(payload)


def line_value(body: str, prefix: str) -> str:
    for line in body.splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
    raise AssertionError(f"missing {prefix!r}")


def latest_mail_value(prefix: str) -> str:
    return line_value(mail.outbox[-1].body, prefix)
