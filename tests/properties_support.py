"""Helpers for property and location tests. This module is not a test module."""

from __future__ import annotations

from apps.locations.models import AdministrativeArea, AdministrativeLevel, Country, Locality

PROPERTIES = "/api/v1/properties/"
COUNTRIES = "/api/v1/locations/countries/"
LOCALITIES = "/api/v1/locations/localities/"
LOCATION_SEARCH = "/api/v1/locations/search/"
RESOLVE = "/api/v1/locations/addresses/resolve/"


def property_body(**overrides) -> dict:
    body = {
        "management_context": "personal",
        "property_type": "residential",
        "property_subtype": "apartment",
        "name": "Kilimani Apartment",
        "hierarchy_role": "standalone",
    }
    body.update(overrides)
    return body


def kenya_chain(
    name: str = "Parklands",
) -> tuple[AdministrativeArea, AdministrativeArea, AdministrativeArea]:
    country = Country.objects.get(pk="KE")
    levels = {level.code: level for level in AdministrativeLevel.objects.filter(country=country)}
    county = AdministrativeArea.objects.create(
        country=country,
        level=levels["county"],
        name=f"Nairobi {name}",
        code=f"47-{name[:8]}",
    )
    sub_county = AdministrativeArea.objects.create(
        country=country,
        parent=county,
        level=levels["sub_county"],
        name=f"Westlands {name}",
    )
    ward = AdministrativeArea.objects.create(
        country=country,
        parent=sub_county,
        level=levels["ward"],
        name=name,
    )
    return county, sub_county, ward


def locality_in(area: AdministrativeArea, name: str = "Kilimani") -> Locality:
    return Locality.objects.create(
        country=area.country,
        administrative_area=area,
        name=name,
        kind="neighbourhood",
    )


def address_for(area: AdministrativeArea, **overrides) -> dict:
    body = {
        "country": area.country_id,
        "administrative_area_id": str(area.id),
        "street_line": "Mpaka Road",
    }
    body.update(overrides)
    return body
