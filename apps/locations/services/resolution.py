"""Validate an address chain and, when asked, store it for one property."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from apps.locations.domain import (
    ADDRESS_FIELD_INVALID,
    ADDRESS_INCOMPLETE,
    AREA_COUNTRY,
    AREA_INVALID,
    COUNTRY_UNSUPPORTED,
    LOCALITY_COUNTRY,
    LOCALITY_OUTSIDE,
    LOCATION_NOT_FOUND,
    MAX_CHAIN_DEPTH,
    clean_text,
    normalize_country,
)
from apps.locations.errors import LocationError
from apps.locations.models import Address, AdministrativeArea, Country, Locality


@dataclass(frozen=True)
class ResolvedAddress:
    country_code: str
    country_name: str
    administrative_area_id: UUID | None
    administrative_area_name: str
    administrative_path: tuple[str, ...]
    locality_id: UUID | None
    locality_name: str
    street_line: str
    building_name: str
    unit_label: str
    postal_code: str


def resolve_address(payload: dict) -> ResolvedAddress:
    if not isinstance(payload, dict):
        raise LocationError(ADDRESS_FIELD_INVALID, status_code=400)
    try:
        country_code = normalize_country(payload.get("country"))
        street_line = clean_text(payload.get("street_line"), max_length=200)
        building_name = clean_text(payload.get("building_name"), max_length=120)
        unit_label = clean_text(payload.get("unit_label"), max_length=40)
        postal_code = clean_text(payload.get("postal_code"), max_length=16)
    except ValueError as exc:
        raise LocationError(str(exc), status_code=400) from exc

    try:
        country = Country.objects.get(pk=country_code, active=True)
    except Country.DoesNotExist as exc:
        raise LocationError(COUNTRY_UNSUPPORTED, status_code=400) from exc

    area = _load_area(payload.get("administrative_area_id"))
    if area is not None:
        assert_area_chain(area)
        if area.country_id != country.pk:
            raise LocationError(AREA_COUNTRY, status_code=400)

    locality = _load_locality(payload.get("locality_id"))
    if locality is not None:
        if locality.country_id != country.pk:
            raise LocationError(LOCALITY_COUNTRY, status_code=400)
        if locality.administrative_area_id is not None:
            assert_area_chain(locality.administrative_area)
            if locality.administrative_area.country_id != country.pk:
                raise LocationError(LOCALITY_COUNTRY, status_code=400)
            if area is None:
                area = locality.administrative_area
            elif not area_covers(area, locality.administrative_area):
                raise LocationError(LOCALITY_OUTSIDE, status_code=400)

    has_place = area is not None or locality is not None
    has_text = any((street_line, building_name, unit_label, postal_code))
    if not has_place and not has_text:
        raise LocationError(ADDRESS_INCOMPLETE, status_code=400)

    path = _path_names(area) if area is not None else ()
    return ResolvedAddress(
        country_code=country.pk,
        country_name=country.name,
        administrative_area_id=None if area is None else area.id,
        administrative_area_name="" if area is None else area.name,
        administrative_path=path,
        locality_id=None if locality is None else locality.id,
        locality_name="" if locality is None else locality.name,
        street_line=street_line,
        building_name=building_name,
        unit_label=unit_label,
        postal_code=postal_code,
    )


def persist_address(resolved: ResolvedAddress) -> Address:
    return Address.objects.create(
        country_id=resolved.country_code,
        administrative_area_id=resolved.administrative_area_id,
        locality_id=resolved.locality_id,
        street_line=resolved.street_line,
        building_name=resolved.building_name,
        unit_label=resolved.unit_label,
        postal_code=resolved.postal_code,
    )


def assert_area_chain(area: AdministrativeArea) -> None:
    current = area
    seen: set[UUID] = set()
    while current is not None:
        if current.id in seen or len(seen) > MAX_CHAIN_DEPTH:
            raise LocationError(AREA_INVALID, status_code=400)
        seen.add(current.id)
        if current.level.country_id != current.country_id:
            raise LocationError(AREA_INVALID, status_code=400)
        parent = current.parent
        if parent is None:
            if current.level.depth != 1:
                raise LocationError(AREA_INVALID, status_code=400)
            break
        if parent.country_id != current.country_id or parent.level.depth != current.level.depth - 1:
            raise LocationError(AREA_INVALID, status_code=400)
        current = parent


def area_covers(ancestor: AdministrativeArea, area: AdministrativeArea) -> bool:
    """True when `area` is `ancestor` or sits under it."""

    current = area
    seen: set[UUID] = set()
    while current is not None and current.id not in seen and len(seen) <= MAX_CHAIN_DEPTH:
        if current.id == ancestor.id:
            return True
        seen.add(current.id)
        current = current.parent
    return False


def _load_area(value) -> AdministrativeArea | None:
    if value in (None, ""):
        return None
    try:
        area_id = value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise LocationError(LOCATION_NOT_FOUND, status_code=400) from exc
    try:
        return AdministrativeArea.objects.select_related("level", "country", "parent__level").get(
            pk=area_id
        )
    except AdministrativeArea.DoesNotExist as exc:
        raise LocationError(LOCATION_NOT_FOUND, status_code=400) from exc


def _load_locality(value) -> Locality | None:
    if value in (None, ""):
        return None
    try:
        locality_id = value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise LocationError(LOCATION_NOT_FOUND, status_code=400) from exc
    try:
        return Locality.objects.select_related(
            "country",
            "administrative_area__level",
            "administrative_area__parent",
            "administrative_area__country",
        ).get(pk=locality_id)
    except Locality.DoesNotExist as exc:
        raise LocationError(LOCATION_NOT_FOUND, status_code=400) from exc


def _path_names(area: AdministrativeArea) -> tuple[str, ...]:
    names: list[str] = []
    current: AdministrativeArea | None = area
    seen: set[UUID] = set()
    while current is not None and current.id not in seen and len(seen) <= MAX_CHAIN_DEPTH:
        seen.add(current.id)
        names.append(current.name)
        current = current.parent
    names.reverse()
    return tuple(names)
