"""Invitation email. The raw token is placed in the message body and nowhere else."""

from __future__ import annotations

from django.conf import settings
from django.core.mail import send_mail


def send_organization_invitation(*, organization, email: str, role: str, raw_token: str) -> None:
    display_name = " ".join(organization.display_name.replace("\r", " ").replace("\n", " ").split())
    send_mail(
        f"Invitation to {display_name[:200]}",
        (
            f"You have been invited to {display_name} on GLITTERS.\n"
            f"role: {role}\n"
            f"token: {raw_token}\n"
            "The invitation expires in 7 days and works once.\n"
        ),
        settings.DEFAULT_FROM_EMAIL,
        [email],
        fail_silently=False,
    )
