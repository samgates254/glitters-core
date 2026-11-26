"""Append-only tenancy audit events."""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from django.db import IntegrityError, transaction

from apps.tenancy.models import TenancyEvent

_SENSITIVE_KEYS = ("token", "password", "secret", "authorization", "cookie")


def tenancy_idempotency_key(*parts: object) -> str:
    """Stable 64-character key. Repeating the same parts is a no-op insert."""

    seed = ":".join(str(part) for part in parts)
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def clean_tenancy_metadata(data: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    cleaned: dict[str, Any] = {}
    for key, value in data.items():
        if not isinstance(key, str):
            continue
        lower_key = key.lower()
        if any(secret in lower_key for secret in _SENSITIVE_KEYS):
            continue
        if isinstance(value, (str, int, bool)) or value is None:
            cleaned[key] = value
        elif isinstance(value, dict):
            cleaned[key] = clean_tenancy_metadata(value)
        elif isinstance(value, (list, tuple)):
            cleaned[key] = [
                item if isinstance(item, (str, int, bool)) or item is None else str(item)
                for item in value
            ]
        else:
            cleaned[key] = str(value)
    return cleaned


def record_tenancy_event(
    *,
    actor,
    action: str,
    object_type: str,
    object_id: str | uuid.UUID,
    lease=None,
    application=None,
    metadata: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
    request_id: str = "",
) -> TenancyEvent:
    """Insert one audit row. A repeated key returns the existing row."""

    key = idempotency_key or tenancy_idempotency_key(
        action, object_type, object_id, request_id or uuid.uuid4().hex
    )
    try:
        with transaction.atomic():
            return TenancyEvent.objects.create(
                idempotency_key=key,
                actor=actor,
                lease=lease,
                application=application,
                action=action,
                object_type=object_type,
                object_id=str(object_id),
                request_id=request_id,
                metadata=clean_tenancy_metadata(metadata),
            )
    except IntegrityError:
        existing = TenancyEvent.objects.filter(idempotency_key=key).first()
        if existing is not None:
            return existing
        raise
