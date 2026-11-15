"""Query parsing for public discovery and the enquiry inbox."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from apps.marketplace.domain import (
    CURRENCIES,
    FILTER_INVALID,
    GEOSPATIAL_PARAMS,
    SORTS,
    EnquiryStatus,
    ListingType,
    parse_area,
    parse_minor_amount,
)
from apps.marketplace.errors import MarketplaceError
from apps.properties.domain import PropertyType


@dataclass(frozen=True)
class ListingFilters:
    listing_type: str | None = None
    property_type: str | None = None
    country: str | None = None
    administrative_area_id: UUID | None = None
    locality_id: UUID | None = None
    price_min: int | None = None
    price_max: int | None = None
    currency: str | None = None
    bedrooms: int | None = None
    bathrooms: int | None = None
    floor_area_min: object | None = None
    land_area_min: object | None = None
    published_after: datetime | None = None
    published_before: datetime | None = None
    query: str | None = None
    sort: str = "-published_at"


@dataclass(frozen=True)
class EnquiryFilters:
    status: str | None = None
    listing_reference: str | None = None
    created_after: datetime | None = None
    created_before: datetime | None = None


_ENQUIRY_PARAMS = frozenset(
    {
        "status",
        "listing_reference",
        "created_after",
        "created_before",
        "page",
        "page_size",
    }
)
_LISTING_REFERENCE = re.compile(r"^[A-Za-z0-9-]{1,20}$")


def parse_enquiry_filters(params) -> EnquiryFilters:
    """Accept inbox filters. Organization, advertiser, and permission keys are refused."""

    if not set(params.keys()) <= _ENQUIRY_PARAMS:
        raise MarketplaceError(FILTER_INVALID, status_code=400)
    created_after = _moment(params.get("created_after"))
    created_before = _moment(params.get("created_before"))
    if created_after is not None and created_before is not None and created_after > created_before:
        raise MarketplaceError(FILTER_INVALID, status_code=400)
    return EnquiryFilters(
        status=_choice(params, "status", EnquiryStatus.VALUES),
        listing_reference=_listing_reference(params.get("listing_reference")),
        created_after=created_after,
        created_before=created_before,
    )


def parse_listing_filters(params) -> ListingFilters:
    names = set(params.keys())
    if names & GEOSPATIAL_PARAMS:
        raise MarketplaceError(FILTER_INVALID, status_code=400)
    listing_type = _choice(params, "listing_type", ListingType.VALUES)
    property_type = _choice(params, "property_type", PropertyType.VALUES)
    country = _country(params.get("country"))
    price_min = _money(params, "price_min")
    price_max = _money(params, "price_max")
    if price_min is not None and price_max is not None and price_min > price_max:
        raise MarketplaceError(FILTER_INVALID, status_code=400)
    currency = _choice(params, "currency", CURRENCIES)
    query = _query(params.get("q"))
    sort = params.get("sort") or "-published_at"
    if sort not in SORTS:
        raise MarketplaceError(FILTER_INVALID, status_code=400)
    return ListingFilters(
        listing_type=listing_type,
        property_type=property_type,
        country=country,
        administrative_area_id=_uuid(params, "administrative_area_id"),
        locality_id=_uuid(params, "locality_id"),
        price_min=price_min,
        price_max=price_max,
        currency=currency,
        bedrooms=_count(params, "bedrooms"),
        bathrooms=_count(params, "bathrooms"),
        floor_area_min=_area(params, "floor_area_min"),
        land_area_min=_area(params, "land_area_min"),
        published_after=_moment(params.get("published_after")),
        published_before=_moment(params.get("published_before")),
        query=query,
        sort=sort,
    )


def _choice(params, name, allowed):
    value = params.get(name)
    if value in (None, ""):
        return None
    if value not in allowed:
        raise MarketplaceError(FILTER_INVALID, status_code=400)
    return value


def _country(value):
    if value in (None, ""):
        return None
    invalid = (
        not isinstance(value, str)
        or len(value) != 2
        or not value.isalpha()
        or value != value.upper()
    )
    if invalid:
        raise MarketplaceError(FILTER_INVALID, status_code=400)
    from apps.locations.models import Country

    if not Country.objects.filter(pk=value).exists():
        raise MarketplaceError(FILTER_INVALID, status_code=400)
    return value


def _uuid(params, name):
    value = params.get(name)
    if value in (None, ""):
        return None
    try:
        return UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise MarketplaceError(FILTER_INVALID, status_code=400) from exc


def _count(params, name):
    value = params.get(name)
    if value in (None, ""):
        return None
    if isinstance(value, bool) or not str(value).isdigit():
        raise MarketplaceError(FILTER_INVALID, status_code=400)
    return int(str(value))


def _money(params, name):
    value = params.get(name)
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise MarketplaceError(FILTER_INVALID, status_code=400)
    text = str(value)
    if not text.isdigit():
        raise MarketplaceError(FILTER_INVALID, status_code=400)
    try:
        return parse_minor_amount(int(text), allow_null=False)
    except ValueError as exc:
        raise MarketplaceError(str(exc), status_code=400) from exc


def _area(params, name):
    value = params.get(name)
    if value in (None, ""):
        return None
    try:
        return parse_area(str(value))
    except ValueError as exc:
        raise MarketplaceError(FILTER_INVALID, status_code=400) from exc


def _listing_reference(value):
    if value in (None, ""):
        return None
    if not isinstance(value, str) or _LISTING_REFERENCE.fullmatch(value) is None:
        raise MarketplaceError(FILTER_INVALID, status_code=400)
    return value


def _query(value):
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise MarketplaceError(FILTER_INVALID, status_code=400)
    text = value.strip()
    if len(text) < 2 or len(text) > 80:
        raise MarketplaceError(FILTER_INVALID, status_code=400)
    return text


def _moment(value):
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise MarketplaceError(FILTER_INVALID, status_code=400)
    parsed = parse_datetime(value)
    if parsed is None:
        parsed_date = parse_date(value)
        if parsed_date is None:
            raise MarketplaceError(FILTER_INVALID, status_code=400)
        parsed = datetime.combine(
            parsed_date,
            datetime.min.time(),
            tzinfo=timezone.get_current_timezone(),
        )
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed
