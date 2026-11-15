"""Append-only listing audit. Secrets and coordinates are dropped."""

from __future__ import annotations

from apps.identity.services.secrets import client_ip
from apps.marketplace.domain import clean_metadata
from apps.marketplace.models import ListingEvent
from apps.properties.services.audit import request_correlation_id


def record_event(
    *,
    actor,
    property_record,
    action: str,
    object_type: str,
    object_id,
    request=None,
    listing=None,
    organization_id=None,
    metadata: dict | None = None,
) -> ListingEvent:
    authenticated = actor if getattr(actor, "is_authenticated", False) else None
    return ListingEvent.objects.create(
        actor=authenticated,
        listing=listing,
        property=property_record,
        organization_id=organization_id,
        action=action,
        object_type=object_type,
        object_id=str(object_id),
        request_id=request_correlation_id(request),
        ip_address=client_ip(request),
        metadata=clean_metadata(metadata),
    )
