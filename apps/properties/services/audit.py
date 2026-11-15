"""Append-only property audit. Coordinate values are dropped."""

from __future__ import annotations

from apps.identity.services.secrets import client_ip
from apps.properties.domain import clean_metadata
from apps.properties.models import PropertyEvent
from config.context import request_id_var


def request_correlation_id(request) -> str:
    if request is not None:
        value = str(getattr(request, "request_id", "") or "")
    else:
        value = str(request_id_var.get() or "")
    if value in {"", "-"} or len(value) > 128:
        return ""
    return value


def record_event(
    *,
    actor,
    property_record,
    action: str,
    object_type: str,
    object_id,
    request=None,
    metadata: dict | None = None,
) -> PropertyEvent:
    return PropertyEvent.objects.create(
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        property=property_record,
        organization_id=property_record.managing_organization_id,
        action=action,
        object_type=object_type,
        object_id=str(object_id),
        request_id=request_correlation_id(request),
        ip_address=client_ip(request),
        metadata=clean_metadata(metadata),
    )
