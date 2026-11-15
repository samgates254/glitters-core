"""Public listings must not leak coordinates, parties, or unpublished offers."""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient
from tests.marketplace_support import (
    LISTINGS,
    PUBLISHER,
    active_property,
    add_photo,
    attach,
    draft_listing,
    expect,
    publish_personal,
    self_mandate,
    staff_user,
)
from tests.organizations_support import ORGANIZATIONS, client_for, organization_body, verified_user
from tests.properties_support import PROPERTIES

pytestmark = pytest.mark.django_db

PUBLIC_KEYS = {
    "reference",
    "listing_type",
    "title",
    "description",
    "highlights",
    "description_origin",
    "published_at",
    "expires_at",
    "property",
    "location",
    "price",
    "advertiser",
    "media",
}
PROPERTY_KEYS = {
    "property_type",
    "property_subtype",
    "bedrooms",
    "bathrooms",
    "floor_area_sqm",
    "land_area_sqm",
}
LOCATION_KEYS = {"country", "administrative_area", "locality"}
PRICE_KEYS = {"amount_minor", "currency", "presentation", "negotiable", "billing_period"}
MEDIA_KEYS = {"mime_type", "asset_reference", "caption", "alt_text", "sort_order", "featured"}
FORBIDDEN = {
    "coordinates",
    "latitude",
    "longitude",
    "street",
    "street_line",
    "building",
    "unit",
    "postal_code",
    "parties",
    "moderation_note",
    "moderation_hold",
    "evidence_reference",
    "rejection_reason",
    "property_id",
    "property_reference",
    "distance",
    "has_coordinates",
    "email",
    "mandate_id",
    "id",
    "geom",
    "geojson",
}


def _keys(value, found: set[str]) -> None:
    if isinstance(value, dict):
        found.update(value)
        for item in value.values():
            _keys(item, found)
    elif isinstance(value, list):
        for item in value:
            _keys(item, found)


def test_public_payload_hides_exact_location_and_private_records(password) -> None:
    owner = verified_user("ada@example.com", password, first_name="Ada", last_name="Okello")
    staff = staff_user("staff@example.com", password)
    owner_client = client_for(owner)
    published = publish_personal(
        owner,
        owner_client,
        client_for(staff),
        title="Public flat",
        with_locality=True,
    )
    reference = published["listing"]["reference"]
    property_id = published["property"]["id"]
    anonymous = APIClient()
    with CaptureQueriesContext(connection) as captured:
        detail = expect(anonymous.get(f"{LISTINGS}{reference}/"), 200)
        collection = expect(anonymous.get(LISTINGS), 200)
    sql = " ".join(query["sql"] for query in captured.captured_queries)
    assert "ST_DWithin" not in sql
    assert "ST_Distance" not in sql
    assert "ST_Contains" not in sql
    assert '"property_location"."coordinates"' not in sql
    assert set(detail) == PUBLIC_KEYS
    assert set(detail["property"]) == PROPERTY_KEYS
    assert set(detail["location"]) == LOCATION_KEYS
    assert set(detail["price"]) == PRICE_KEYS
    assert set(detail["advertiser"]) == {"display_name", "organization_name"}
    assert set(detail["media"][0]) == MEDIA_KEYS
    assert detail["advertiser"]["display_name"] == "Ada Okello"
    assert detail["advertiser"]["organization_name"] is None
    assert detail["location"]["country"] == "KE"
    assert detail["location"]["locality"]
    assert detail["description_origin"] == "human"
    blob = str(detail) + str(collection)
    found: set[str] = set()
    _keys(detail, found)
    _keys(collection, found)
    assert found.isdisjoint(FORBIDDEN)
    assert "Mpaka Road" not in blob
    assert "-1.2921" not in blob
    assert "36.8219" not in blob
    assert published["property"]["reference"] not in blob
    assert str(property_id) not in blob
    assert "Owner letter" not in blob
    property_body = owner_client.get(f"{PROPERTIES}{property_id}/").json()
    assert "LST-" not in str(property_body)
    assert anonymous.get(f"{LISTINGS}{reference}/").json()["reference"] == reference
    blocked = (
        "near",
        "bbox",
        "lat",
        "lon",
        "latitude",
        "longitude",
        "radius_m",
        "distance",
        "radius",
    )
    for parameter in blocked:
        refused = anonymous.get(LISTINGS, {parameter: "1"})
        assert refused.status_code == 400, parameter
    draft_property = active_property(owner_client, name="Unlisted asset")
    assert draft_property["reference"] not in blob
    missed = anonymous.get(LISTINGS, {"q": "Unlisted"})
    assert missed.status_code == 200
    assert missed.json()["count"] == 0 or draft_property["reference"] not in str(missed.json())


def test_unpublished_and_other_organizations_stay_hidden(password) -> None:
    owner = verified_user("ada@example.com", password)
    other = verified_user("ben@example.com", password)
    staff = staff_user("staff@example.com", password)
    owner_client = client_for(owner)
    published = publish_personal(owner, owner_client, client_for(staff), title="Visible offer")
    reference = published["listing"]["reference"]
    stranger = client_for(other)
    assert stranger.get(f"{PUBLISHER}{published['listing']['id']}/").status_code == 404
    assert stranger.get(PUBLISHER).json()["count"] == 0
    paused = expect(owner_client.post(f"{PUBLISHER}{published['listing']['id']}/pause/"), 200)
    assert paused["status"] == "paused"
    anonymous = APIClient()
    assert anonymous.get(f"{LISTINGS}{reference}/").status_code == 404
    assert reference not in str(anonymous.get(LISTINGS).json())
    organization = expect(
        owner_client.post(
            ORGANIZATIONS,
            organization_body(display_name="Other Agency"),
            format="json",
        ),
        201,
    )
    assert organization["id"]
    # A known organization id without membership is 403. Missing organizations are 404.
    assert stranger.get(f"{ORGANIZATIONS}{organization['id']}/").status_code == 403


def test_public_queries_do_not_grow_per_listing_row(password) -> None:
    owner = verified_user("ada@example.com", password)
    staff_client = client_for(staff_user("staff@example.com", password))
    owner_client = client_for(owner)
    publish_personal(owner, owner_client, staff_client, name="Query one", title="Query one")
    anonymous = APIClient()
    with CaptureQueriesContext(connection) as first:
        expect(anonymous.get(LISTINGS), 200)
    publish_personal(owner, owner_client, staff_client, name="Query two", title="Query two")
    with CaptureQueriesContext(connection) as second:
        body = expect(anonymous.get(LISTINGS), 200)
    assert body["count"] >= 2
    assert len(second.captured_queries) <= len(first.captured_queries) + 2


def test_moderation_metadata_is_not_public(password) -> None:
    owner = verified_user("ada@example.com", password)
    staff = staff_user("staff@example.com", password)
    owner_client = client_for(owner)
    staff_client = client_for(staff)
    record = active_property(owner_client, name="Reviewed")
    mandate = self_mandate(owner_client, owner, record["id"])
    media = add_photo(owner_client, record["id"])
    listing = draft_listing(
        owner_client,
        property_id=record["id"],
        mandate_id=mandate["id"],
        description="Needs a correction before the public can see it.",
    )
    attach(owner_client, listing["id"], media["id"])
    expect(owner_client.post(f"{PUBLISHER}{listing['id']}/submit/"), 200)
    rejected = expect(
        staff_client.post(
            f"/api/v1/marketplace/moderation/listings/{listing['id']}/reject/",
            {"reason": "Private gate code in the highlights."},
            format="json",
        ),
        200,
    )
    assert rejected["moderation_note"] == "" or "gate" not in rejected["moderation_note"]
    anonymous = APIClient()
    assert anonymous.get(f"{LISTINGS}{listing['reference']}/").status_code == 404
    plain = staff_client.get(f"{LISTINGS}{listing['reference']}/")
    assert plain.status_code == 404
    detail = expect(
        staff_client.get(f"/api/v1/marketplace/moderation/listings/{listing['id']}/"),
        200,
    )
    assert "does not verify legal title" in detail["moderation_notice"]
