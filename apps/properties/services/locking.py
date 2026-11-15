"""Row locks for property writes. Callers already hold a transaction."""

from __future__ import annotations

from django.db import OperationalError

from apps.properties.models import Property


def is_deadlock(exc: OperationalError) -> bool:
    cause = getattr(exc, "__cause__", None)
    code = getattr(cause, "sqlstate", None) or getattr(cause, "pgcode", None)
    return code == "40P01" or "deadlock detected" in str(exc).lower()


def lock_properties(*ids) -> dict:
    locked = {}
    for pk in sorted({value for value in ids if value is not None}, key=str):
        locked[pk] = Property.objects.select_for_update().get(pk=pk)
    return locked
