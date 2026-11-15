"""Location reference API."""

import pytest
from tests.identity_support import error_detail
from tests.organizations_support import client_for, verified_user
from tests.properties_support import COUNTRIES, LOCALITIES, LOCATION_SEARCH, RESOLVE

from apps.locations.models import AdministrativeArea, AdministrativeLevel, Country, Locality
from apps.locations.selectors import level_codes


@pytest.mark.django_db
def test_country_ladders_are_not_kenya_only(password) -> None:
    user = verified_user("ada@example.com", password)
    client = client_for(user)
    listed = client.get(COUNTRIES)
    assert listed.status_code == 200
    codes = {row["iso_alpha2"] for row in listed.json()["results"]}
    assert {"KE", "UG", "TZ", "RW"} <= codes
    assert level_codes(Country.objects.get(pk="KE")) == ["county", "sub_county", "ward"]
    assert level_codes(Country.objects.get(pk="UG"))[0] == "region"
    assert "district" in level_codes(Country.objects.get(pk="UG"))
    areas = client.get(f"{COUNTRIES}KE/areas/")
    assert areas.status_code == 200
    assert areas.json()["count"] == 0


@pytest.mark.django_db
def test_browse_search_and_resolve_a_valid_chain(password) -> None:
    user = verified_user("ada@example.com", password)
    client = client_for(user)
    country = Country.objects.get(pk="KE")
    levels = {level.code: level for level in AdministrativeLevel.objects.filter(country=country)}
    county = AdministrativeArea.objects.create(
        country=country,
        level=levels["county"],
        name="Nairobi",
        code="47",
    )
    sub_county = AdministrativeArea.objects.create(
        country=country,
        parent=county,
        level=levels["sub_county"],
        name="Westlands",
    )
    ward = AdministrativeArea.objects.create(
        country=country,
        parent=sub_county,
        level=levels["ward"],
        name="Parklands",
    )
    Locality.objects.create(
        country=country,
        administrative_area=ward,
        name="Parklands Estate",
        kind="neighbourhood",
    )
    roots = client.get(f"{COUNTRIES}KE/areas/")
    assert roots.status_code == 200
    assert roots.json()["results"][0]["name"] == "Nairobi"
    assert roots.json()["results"][0]["level_code"] == "county"
    children = client.get(f"{COUNTRIES}KE/areas/?parent_id={county.id}")
    assert children.json()["results"][0]["name"] == "Westlands"
    hierarchy = client.get(f"/api/v1/locations/areas/{ward.id}/")
    assert hierarchy.status_code == 200
    assert [row["name"] for row in hierarchy.json()["ancestors"]] == ["Nairobi", "Westlands"]
    assert [row["code"] for row in hierarchy.json()["levels"]] == ["county", "sub_county", "ward"]
    localities = client.get(f"{LOCALITIES}?q=park&country=KE&administrative_area_id={county.id}")
    assert localities.status_code == 200
    assert localities.json()["results"][0]["name"] == "Parklands Estate"
    found = client.get(f"{LOCATION_SEARCH}?q=west&country=ke")
    assert found.status_code == 200
    assert any(row["name"] == "Westlands" for row in found.json()["results"])
    assert "coordinates" not in found.json()["results"][0]
    resolved = client.post(
        RESOLVE,
        {
            "country": "ke",
            "administrative_area_id": str(ward.id),
            "street_line": "Mpaka Road",
            "postal_code": "00606",
        },
        format="json",
    )
    assert resolved.status_code == 200
    body = resolved.json()
    assert body["country"] == "KE"
    assert body["administrative_path"] == ["Nairobi", "Westlands", "Parklands"]
    assert body["street_line"] == "Mpaka Road"
    assert "latitude" not in body


@pytest.mark.django_db
def test_invalid_administrative_relationships_are_rejected(password) -> None:
    user = verified_user("ada@example.com", password)
    client = client_for(user)
    kenya = Country.objects.get(pk="KE")
    uganda = Country.objects.get(pk="UG")
    kenya_levels = {
        level.code: level for level in AdministrativeLevel.objects.filter(country=kenya)
    }
    uganda_levels = {
        level.code: level for level in AdministrativeLevel.objects.filter(country=uganda)
    }
    nairobi = AdministrativeArea.objects.create(
        country=kenya,
        level=kenya_levels["county"],
        name="Nairobi",
        code="47",
    )
    skipped = AdministrativeArea.objects.create(
        country=kenya,
        parent=nairobi,
        level=kenya_levels["ward"],
        name="Skipped Ward",
    )
    kampala = AdministrativeArea.objects.create(
        country=uganda,
        level=uganda_levels["region"],
        name="Central",
    )
    district = AdministrativeArea.objects.create(
        country=uganda,
        parent=kampala,
        level=uganda_levels["district"],
        name="Kampala",
    )
    locality = Locality.objects.create(
        country=uganda,
        administrative_area=district,
        name="Kololo",
        kind="neighbourhood",
    )
    skipped_response = client.post(
        RESOLVE,
        {"country": "KE", "administrative_area_id": str(skipped.id), "street_line": "Road"},
        format="json",
    )
    assert skipped_response.status_code == 400
    assert error_detail(skipped_response) == "This administrative area is not valid."
    crossed = client.post(
        RESOLVE,
        {"country": "KE", "administrative_area_id": str(district.id), "street_line": "Road"},
        format="json",
    )
    assert crossed.status_code == 400
    assert "does not belong" in error_detail(crossed)
    outside = client.post(
        RESOLVE,
        {
            "country": "UG",
            "administrative_area_id": str(kampala.id),
            "locality_id": str(locality.id),
            "street_line": "Road",
        },
        format="json",
    )
    assert outside.status_code == 200
    mismatched = AdministrativeArea.objects.create(
        country=kenya,
        level=kenya_levels["county"],
        name="Other County",
        code="01",
    )
    wrong_locality = client.post(
        RESOLVE,
        {
            "country": "UG",
            "administrative_area_id": str(mismatched.id),
            "locality_id": str(locality.id),
        },
        format="json",
    )
    assert wrong_locality.status_code == 400
    short = client.get(f"{LOCALITIES}?q=a")
    assert short.status_code == 400
    assert error_detail(short) == "Enter at least two characters."
    assert "request_id" in short.json()


@pytest.mark.django_db
def test_location_search_is_paginated(password) -> None:
    user = verified_user("ada@example.com", password)
    client = client_for(user)
    country = Country.objects.get(pk="KE")
    level = AdministrativeLevel.objects.get(country=country, code="county")
    for index in range(30):
        AdministrativeArea.objects.create(
            country=country,
            level=level,
            name=f"Searchtown {index:02d}",
            code=f"s{index:02d}",
        )
    page = client.get(f"{LOCATION_SEARCH}?q=Searchtown&page_size=25")
    assert page.status_code == 200
    body = page.json()
    assert body["count"] == 30
    assert len(body["results"]) == 25
    assert body["next"]
    second = client.get(body["next"])
    assert len(second.json()["results"]) == 5
