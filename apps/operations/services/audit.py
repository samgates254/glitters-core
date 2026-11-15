"""Append-only operational audit event recording."""

from __future__ import annotations

import uuid
from typing import Any

from django.db import IntegrityError, transaction

from config.context import request_id_var
from apps.operations.domain import clean_metadata, compute_idempotency_key
from apps.operations.models import OperationsEvent


def request_correlation_id(request=None) -> str:
    if request is not None:
        value = str(getattr(request, "request_id", "") or "")
        if value:
            return value
    value = str(request_id_var.get() or "")
    return value if value else ""


def record_operations_event(
    *,
    actor,
    property_record,
    action: str,
    object_type: str,
    object_id: str | uuid.UUID,
    organization=None,
    request=None,
    metadata: dict[str, Any] | None = None,
) -> OperationsEvent:
    req_id = request_correlation_id(request)
    str_object_id = str(object_id)
    key = compute_idempotency_key(action, object_type, str_object_id, req_id)
    safe_metadata = clean_metadata(metadata)

    try:
        with transaction.atomic():
            return OperationsEvent.objects.create(
                idempotency_key=key,
                actor=actor,
                property=property_record,
                organization=organization,
                action=action,
                object_type=object_type,
                object_id=str_object_id,
                request_id=req_id,
                metadata=safe_metadata,
            )
    except IntegrityError:
        # Idempotent replay: return existing event
        existing = OperationsEvent.objects.filter(idempotency_key=key).first()
        if existing is not None:
            return existing
        raise
