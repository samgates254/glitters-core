"""Replace the structured characteristics of one property."""

from __future__ import annotations

from apps.properties.domain import (
    CHARACTERISTIC_ONCE,
    CHARACTERISTIC_TYPE,
    CHARACTERISTIC_VALUE,
    EventAction,
    attribute_api_value,
    normalize_attribute,
)
from apps.properties.errors import PropertyError
from apps.properties.models import PropertyAttribute
from apps.properties.services.audit import record_event


def replace_characteristics(record, items, *, actor, request=None) -> list[PropertyAttribute]:
    if not isinstance(items, list):
        raise PropertyError(CHARACTERISTIC_VALUE, status_code=400)
    seen: list[str] = []
    prepared = []
    for item in items:
        if not isinstance(item, dict) or "code" not in item or "value" not in item:
            raise PropertyError(CHARACTERISTIC_VALUE, status_code=400)
        code = str(item["code"]).strip()
        if code in seen:
            raise PropertyError(CHARACTERISTIC_ONCE, status_code=400)
        seen.append(code)
        try:
            columns = normalize_attribute(record.property_type, code, item["value"])
        except ValueError as exc:
            message = str(exc)
            status = 409 if message == CHARACTERISTIC_TYPE else 400
            raise PropertyError(message, status_code=status) from exc
        prepared.append((code, columns))

    record.attributes.all().delete()
    stored = [
        PropertyAttribute.objects.create(property=record, code=code, **columns)
        for code, columns in prepared
    ]
    record_event(
        actor=actor,
        property_record=record,
        action=EventAction.CHARACTERISTICS_UPDATED,
        object_type="property",
        object_id=record.id,
        request=request,
        metadata={
            "codes": [
                {"code": code, "value": _json_value(columns), "unit": columns["unit"]}
                for code, columns in prepared
            ]
        },
    )
    return stored


def _json_value(columns: dict):
    value = attribute_api_value(columns)
    if hasattr(value, "quantize"):
        return str(value)
    return value
