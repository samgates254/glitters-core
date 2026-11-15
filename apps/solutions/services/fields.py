"""Shared request cleaning for the solutions services."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date, datetime

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator, validate_email
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from apps.identity.domain import E164_RE, normalize_email
from apps.organizations.models import Organization
from apps.solutions.domain import INVALID, SLUG_RE
from apps.solutions.errors import SolutionsError

_SEPARATORS = re.compile(r"[\s().-]")
_URL = URLValidator(schemes=["https"])


def require_mapping(fields: object) -> Mapping:
    if not isinstance(fields, Mapping):
        raise SolutionsError(INVALID, status_code=400)
    return fields


def reject_unknown(fields: Mapping, allowed: frozenset[str]) -> None:
    if set(fields) - allowed:
        raise SolutionsError("This field is not accepted.", status_code=400)


def text(value: object, *, maximum: int, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str) or "\x00" in value:
        raise SolutionsError(INVALID, status_code=400)
    cleaned = value.strip()
    if required and cleaned == "":
        raise SolutionsError(INVALID, status_code=400)
    if len(cleaned) > maximum:
        raise SolutionsError(INVALID, status_code=400)
    return cleaned


def slug(value: object) -> str:
    cleaned = text(value, maximum=80, required=True)
    if SLUG_RE.fullmatch(cleaned) is None:
        raise SolutionsError(INVALID, status_code=400)
    return cleaned


def optional_email(value: object) -> str:
    cleaned = text(value, maximum=254)
    if cleaned == "":
        return ""
    normalized = normalize_email(cleaned)
    try:
        validate_email(normalized)
    except ValidationError:
        raise SolutionsError(INVALID, status_code=400) from None
    return normalized


def optional_phone(value: object) -> str:
    cleaned = text(value, maximum=32)
    if cleaned == "":
        return ""
    compact = _SEPARATORS.sub("", cleaned)
    if compact.startswith("00"):
        compact = f"+{compact[2:]}"
    if E164_RE.fullmatch(compact) is None:
        raise SolutionsError(INVALID, status_code=400)
    return compact


def optional_money(value: object) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SolutionsError(INVALID, status_code=400)
    return value


def flag(value: object, *, default: bool = False) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise SolutionsError(INVALID, status_code=400)
    return value


def optional_datetime(value: object) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = parse_datetime(value)
        if parsed is None:
            raise SolutionsError(INVALID, status_code=400)
    else:
        raise SolutionsError(INVALID, status_code=400)
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def optional_date(value: object) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise SolutionsError(INVALID, status_code=400)
    parsed = parse_date(value)
    if parsed is None:
        raise SolutionsError(INVALID, status_code=400)
    return parsed


def optional_https(value: object) -> str:
    cleaned = text(value, maximum=200)
    if cleaned == "":
        return ""
    try:
        _URL(cleaned)
    except ValidationError:
        raise SolutionsError(INVALID, status_code=400) from None
    return cleaned


def choice(value: object, allowed: frozenset[str]) -> str:
    cleaned = text(value, maximum=64, required=True)
    if cleaned not in allowed:
        raise SolutionsError(INVALID, status_code=400)
    return cleaned


def active_organization(value: object):
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise SolutionsError(INVALID, status_code=400)
    organization = Organization.objects.filter(pk=value, status="active").first()
    if organization is None:
        raise SolutionsError(INVALID, status_code=400)
    return organization
