"""Append-only authentication events."""

from __future__ import annotations

from apps.identity.models import IdentityEvent
from apps.identity.services.secrets import client_ip, user_agent


def record_event(user, event_type: str, request=None) -> None:
    IdentityEvent.objects.create(
        user=user,
        event_type=event_type,
        ip_address=client_ip(request),
        user_agent=user_agent(request),
    )
