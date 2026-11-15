"""Query parsing for property discovery. This is not a public marketplace search."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from django.contrib.gis.geos import Polygon

from apps.properties.domain import (
    BBOX_INVALID,
    BBOX_TOO_LARGE,
    FILTER_INVALID,
    MAX_BBOX_DEGREES,
    MAX_RADIUS_M,
    NEAR_REQUIRED,
    RADIUS_RANGE,
    RADIUS_REQUIRED,
    ManagementContext,
    PropertyStatus,
    PropertyType,
    parse_coordinates,
)
from apps.properties.errors import PropertyError


@dataclass
class PropertyFilters:
    status: str | None = None
    property_type: str | None = None
    management_context: str | None = None
    organization_id: UUID | None = None
    parent_id: UUID | None = None
    administrative_area_id: UUID | None = None
    country: str | None = None
    near: object | None = None
    radius_m: int | None = None
    bbox: Polygon | None = None


def parse_property_filters(params) -> PropertyFilters:
    filters = PropertyFilters(
        status=_choice(params.get("status"), PropertyStatus.VALUES),
        property_type=_choice(params.get("property_type"), PropertyType.VALUES),
        management_context=_choice(params.get("management_context"), ManagementContext.VALUES),
        organization_id=_uuid(params.get("organization_id")),
        parent_id=_uuid(params.get("parent_id")),
        administrative_area_id=_uuid(params.get("administrative_area_id")),
        country=_country(params.get("country")),
    )
    near_text = params.get("near")
    radius_text = params.get("radius_m")
    bbox_text = params.get("bbox")
    if near_text and not radius_text:
        raise PropertyError(RADIUS_REQUIRED, status_code=400)
    if radius_text and not near_text:
        raise PropertyError(NEAR_REQUIRED, status_code=400)
    if near_text:
        filters.near = _near(near_text)
        filters.radius_m = _radius(radius_text)
    if bbox_text:
        filters.bbox = _bbox(bbox_text)
    return filters


def _choice(value, allowed: frozenset[str]) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if text not in allowed:
        raise PropertyError(FILTER_INVALID, status_code=400)
    return text


def _uuid(value) -> UUID | None:
    if value in (None, ""):
        return None
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise PropertyError(FILTER_INVALID, status_code=400) from exc


def _country(value) -> str | None:
    if value in (None, ""):
        return None
    code = str(value).strip().upper()
    if len(code) != 2 or not code.isalpha():
        raise PropertyError(FILTER_INVALID, status_code=400)
    return code


def _near(value):
    parts = str(value).split(",")
    if len(parts) != 2:
        raise PropertyError(FILTER_INVALID, status_code=400)
    try:
        latitude = float(parts[0].strip())
        longitude = float(parts[1].strip())
    except ValueError as exc:
        raise PropertyError(FILTER_INVALID, status_code=400) from exc
    try:
        return parse_coordinates({"latitude": latitude, "longitude": longitude})
    except ValueError as exc:
        raise PropertyError(str(exc), status_code=400) from exc


def _radius(value) -> int:
    try:
        radius = int(str(value))
    except (TypeError, ValueError) as exc:
        raise PropertyError(RADIUS_RANGE, status_code=400) from exc
    if isinstance(value, str) and value.strip() != str(radius):
        raise PropertyError(RADIUS_RANGE, status_code=400)
    if radius < 1 or radius > MAX_RADIUS_M:
        raise PropertyError(RADIUS_RANGE, status_code=400)
    return radius


def _bbox(value) -> Polygon:
    parts = str(value).split(",")
    if len(parts) != 4:
        raise PropertyError(BBOX_INVALID, status_code=400)
    try:
        min_lon, min_lat, max_lon, max_lat = (float(part.strip()) for part in parts)
    except ValueError as exc:
        raise PropertyError(BBOX_INVALID, status_code=400) from exc
    try:
        parse_coordinates({"latitude": min_lat, "longitude": min_lon})
        parse_coordinates({"latitude": max_lat, "longitude": max_lon})
    except ValueError as exc:
        raise PropertyError(BBOX_INVALID, status_code=400) from exc
    if min_lon >= max_lon or min_lat >= max_lat:
        raise PropertyError(BBOX_INVALID, status_code=400)
    if max_lon - min_lon > MAX_BBOX_DEGREES or max_lat - min_lat > MAX_BBOX_DEGREES:
        raise PropertyError(BBOX_TOO_LARGE, status_code=400)
    polygon = Polygon.from_bbox((min_lon, min_lat, max_lon, max_lat))
    polygon.srid = 4326
    return polygon
