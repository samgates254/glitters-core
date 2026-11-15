"""Outbound identity messages.

Email uses Django's mail backend. Phone codes use that same backend until an
SMS provider exists. Delivery proves the adapter was invoked, not that an SMS
network accepted the message.
"""

from __future__ import annotations

from django.conf import settings
from django.core.mail import send_mail


def send_identity_email(*, to: str, subject: str, body: str) -> None:
    send_mail(
        subject,
        body,
        settings.DEFAULT_FROM_EMAIL,
        [to],
        fail_silently=False,
    )


def send_email_verification(user, raw_token: str) -> None:
    send_identity_email(
        to=user.email,
        subject="Verify your GLITTERS email",
        body=(
            "Use this token to verify your email.\n"
            f"token: {raw_token}\n"
            "The token expires in 24 hours and works once.\n"
        ),
    )


def send_existing_account_notice(user) -> None:
    send_identity_email(
        to=user.email,
        subject="GLITTERS account notice",
        body=(
            "An account with this email already exists.\n"
            "If you requested this, reset your password.\n"
        ),
    )


def send_password_reset(user, raw_token: str) -> None:
    send_identity_email(
        to=user.email,
        subject="Reset your GLITTERS password",
        body=(
            "Use this token to choose a new password.\n"
            f"token: {raw_token}\n"
            "The token expires in 1 hour and works once.\n"
        ),
    )


def send_phone_code(user, phone_e164: str, raw_code: str) -> None:
    send_identity_email(
        to=user.email,
        subject="GLITTERS phone verification code",
        body=(
            "A phone verification code was requested for this account.\n"
            f"phone: {phone_e164}\n"
            f"code: {raw_code}\n"
            "The code expires in 10 minutes.\n"
            "This message is the current delivery adapter. It is not an SMS receipt.\n"
        ),
    )
