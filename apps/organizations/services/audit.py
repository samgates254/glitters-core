"""Append-only organization audit events."""

from __future__ import annotations

from apps.identity.services.secrets import client_ip
from apps.organizations.domain import FORBIDDEN_METADATA_KEYS
from apps.organizations.models import OrganizationEvent
from config.context import request_id_var


def request_correlation_id(request) -> str:
    if request is not None:
        value = str(getattr(request, "request_id", "") or "")
    else:
        value = str(request_id_var.get() or "")
    if value in {"", "-"} or len(value) > 128:
        return ""
    return value


def clean_metadata(metadata: dict | None) -> dict:
    cleaned: dict = {}
    for key, value in (metadata or {}).items():
        if str(key).lower() in FORBIDDEN_METADATA_KEYS:
            continue
        cleaned[str(key)] = value
    return cleaned


def record_event(
    *,
    actor,
    organization,
    action: str,
    object_type: str,
    object_id,
    request=None,
    metadata: dict | None = None,
) -> OrganizationEvent:
    return OrganizationEvent.objects.create(
        actor=actor,
        organization=organization,
        action=action,
        object_type=object_type,
        object_id=str(object_id),
        request_id=request_correlation_id(request),
        ip_address=client_ip(request),
        metadata=clean_metadata(metadata),
    )
