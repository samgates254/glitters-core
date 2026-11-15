"""Helpers for marketplace tests. This module is not a test module."""

from __future__ import annotations

from tests.identity_support import enable_confirmed_mfa
from tests.organizations_support import verified_user
from tests.properties_support import (
    PROPERTIES,
    address_for,
    kenya_chain,
    locality_in,
    property_body,
)

LISTINGS = "/api/v1/marketplace/listings/"
PUBLISHER = "/api/v1/marketplace/publisher/listings/"
MANDATES = "/api/v1/marketplace/mandates/"
MODERATION = "/api/v1/marketplace/moderation/listings/"

_AREA = 0


def expect(response, status: int):
    assert response.status_code == status, response.content
    if status == 204:
        return None
    return response.json()


def sale_price(**overrides) -> dict:
    body = {
        "amount": 12_500_000_00,
        "currency": "KES",
        "presentation": "asking",
        "negotiable": True,
        "billing_period": "sale",
    }
    body.update(overrides)
    return body


def rent_price(**overrides) -> dict:
    body = sale_price(
        amount=85_000_00,
        billing_period="month",
        negotiable=False,
    )
    body.update(overrides)
    return body


def staff_user(email: str, password: str):
    user = verified_user(email, password, is_staff=True)
    enable_confirmed_mfa(user)
    return user


def active_property(client, *, name: str, with_locality: bool = False, **overrides) -> dict:
    global _AREA
    _AREA += 1
    _county, _sub, ward = kenya_chain(name=f"Mkt{_AREA}")
    created = expect(
        client.post(PROPERTIES, property_body(name=name, **overrides), format="json"),
        201,
    )
    address = address_for(ward)
    if with_locality:
        locality = locality_in(ward, name=f"Place{_AREA}")
        address["locality_id"] = str(locality.id)
    activated = expect(
        client.patch(
            f"{PROPERTIES}{created['id']}/",
            {
                "status": "active",
                "address": address,
                "coordinates": {"latitude": -1.2921, "longitude": 36.8219},
                "characteristics": [
                    {"code": "bedrooms", "value": 3},
                    {"code": "bathrooms", "value": 2},
                    {"code": "floor_area_sqm", "value": "85.50"},
                    {"code": "plot_area_sqm", "value": "400.00"},
                ],
            },
            format="json",
        ),
        200,
    )
    activated["ward_id"] = str(ward.id)
    activated["county_id"] = str(_county.id)
    return activated


def self_mandate(client, user, property_id: str, scope: str = "sale") -> dict:
    return expect(
        client.post(
            MANDATES,
            {
                "property_id": property_id,
                "listing_scope": scope,
                "representative_user_id": str(user.id),
                "principal": "self",
                "evidence_reference": "Owner letter 2026",
            },
            format="json",
        ),
        201,
    )


def add_photo(client, property_id: str) -> dict:
    from tests.media_samples import jpeg_bytes, store_upload

    data = jpeg_bytes()
    opened = expect(
        client.post(
            f"/api/v1/marketplace/properties/{property_id}/media/upload-sessions/",
            {
                "kind": "photograph",
                "mime_type": "image/jpeg",
                "size_bytes": len(data),
                "caption": "Front elevation",
                "alt_text": "Front of the building",
            },
            format="json",
        ),
        201,
    )
    store_upload(opened, data)
    return expect(
        client.post(
            f"/api/v1/marketplace/media/upload-sessions/{opened['id']}/complete/",
            format="json",
        ),
        200,
    )


def add_document(client, property_id: str) -> dict:
    from tests.media_samples import pdf_bytes, store_upload

    data = pdf_bytes()
    opened = expect(
        client.post(
            f"/api/v1/marketplace/properties/{property_id}/media/upload-sessions/",
            {
                "kind": "document",
                "mime_type": "application/pdf",
                "size_bytes": len(data),
            },
            format="json",
        ),
        201,
    )
    store_upload(opened, data)
    return expect(
        client.post(
            f"/api/v1/marketplace/media/upload-sessions/{opened['id']}/complete/",
            format="json",
        ),
        200,
    )


def draft_listing(
    client,
    *,
    property_id: str,
    mandate_id: str,
    listing_type: str = "sale",
    title: str = "Kilimani apartment",
    description: str = "A bright apartment with a private balcony.",
    price: dict | None = None,
    **extra,
) -> dict:
    body = {
        "property_id": property_id,
        "mandate_id": mandate_id,
        "listing_type": listing_type,
        "title": title,
        "description": description,
        "highlights": "Secure parking",
        "price": sale_price() if price is None else price,
    }
    body.update(extra)
    return expect(client.post(PUBLISHER, body, format="json"), 201)


def attach(client, listing_id: str, media_id: str, *, featured: bool = True) -> dict:
    return expect(
        client.post(
            f"{PUBLISHER}{listing_id}/media/",
            {"property_media_id": media_id, "sort_order": 0, "featured": featured},
            format="json",
        ),
        201,
    )


def publish_personal(
    owner,
    owner_client,
    staff_client,
    *,
    name: str = "Market apartment",
    title: str = "Jacaranda apartment",
    listing_type: str = "sale",
    price: dict | None = None,
    with_locality: bool = False,
):
    record = active_property(owner_client, name=name, with_locality=with_locality)
    mandate = self_mandate(owner_client, owner, record["id"], listing_type)
    media = add_photo(owner_client, record["id"])
    listing = draft_listing(
        owner_client,
        property_id=record["id"],
        mandate_id=mandate["id"],
        listing_type=listing_type,
        title=title,
        price=price,
    )
    attach(owner_client, listing["id"], media["id"])
    expect(owner_client.post(f"{PUBLISHER}{listing['id']}/submit/"), 200)
    approved = expect(staff_client.post(f"{MODERATION}{listing['id']}/approve/"), 200)
    return {
        "property": record,
        "mandate": mandate,
        "media": media,
        "listing": approved,
    }
