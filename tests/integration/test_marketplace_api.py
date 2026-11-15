"""Listing lifecycle, commercial terms, media, moderation, and discovery."""

from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient
from tests.identity_support import error_detail
from tests.marketplace_support import (
    LISTINGS,
    MODERATION,
    PUBLISHER,
    active_property,
    add_document,
    add_photo,
    attach,
    draft_listing,
    expect,
    publish_personal,
    rent_price,
    sale_price,
    self_mandate,
    staff_user,
)
from tests.organizations_support import client_for, verified_user

from apps.marketplace.models import Listing, ListingEvent, ListingPrice

pytestmark = pytest.mark.django_db


def test_listing_lifecycle_and_invalid_transitions(password) -> None:
    owner = verified_user("ada@example.com", password, first_name="Ada", last_name="Okello")
    staff = staff_user("staff@example.com", password)
    owner_client = client_for(owner)
    staff_client = client_for(staff)
    record = active_property(owner_client, name="Lifecycle flat")
    mandate = self_mandate(owner_client, owner, record["id"])
    media = add_photo(owner_client, record["id"])
    created = draft_listing(
        owner_client,
        property_id=record["id"],
        mandate_id=mandate["id"],
        description="",
    )
    assert created["status"] == "draft"
    assert created["reference"].startswith("LST-")
    assert created["property_reference"].startswith("GLT-")
    assert "legal_title_verified" not in created
    edited = expect(
        owner_client.patch(
            f"{PUBLISHER}{created['id']}/",
            {"description": "A bright apartment with a private balcony.", "title": "Edited title"},
            format="json",
        ),
        200,
    )
    assert edited["title"] == "Edited title"
    assert edited["description"].startswith("A bright")
    bare = owner_client.post(f"{PUBLISHER}{created['id']}/submit/")
    assert bare.status_code == 400
    assert error_detail(bare) == "A stored photograph is required before submission."
    attach(owner_client, created["id"], media["id"])
    submitted = expect(owner_client.post(f"{PUBLISHER}{created['id']}/submit/"), 200)
    assert submitted["status"] == "pending_review"
    self_publish = owner_client.post(f"{MODERATION}{created['id']}/approve/")
    assert self_publish.status_code == 403
    rejected = expect(
        staff_client.post(
            f"{MODERATION}{created['id']}/reject/",
            {"reason": "The description names a private gate code."},
            format="json",
        ),
        200,
    )
    assert rejected["status"] == "rejected"
    assert rejected["rejection_reason"].startswith("The description")
    assert "does not verify legal title" in rejected["moderation_notice"]
    revised = expect(owner_client.post(f"{PUBLISHER}{created['id']}/revise/"), 200)
    assert revised["status"] == "draft"
    expect(owner_client.post(f"{PUBLISHER}{created['id']}/submit/"), 200)
    approved = expect(staff_client.post(f"{MODERATION}{created['id']}/approve/"), 200)
    assert approved["status"] == "published"
    assert approved["published_at"]
    assert approved["expires_at"]
    paused = expect(owner_client.post(f"{PUBLISHER}{created['id']}/pause/"), 200)
    assert paused["status"] == "paused"
    resumed = expect(owner_client.post(f"{PUBLISHER}{created['id']}/resume/"), 200)
    assert resumed["status"] == "published"
    invalid = owner_client.post(f"{PUBLISHER}{created['id']}/revise/")
    assert invalid.status_code == 409
    assert error_detail(invalid) == "This listing status change is not allowed."
    early = owner_client.post(f"{PUBLISHER}{created['id']}/expire/")
    assert early.status_code == 409
    assert error_detail(early) == "This listing has not reached its expiry time."
    held = expect(
        staff_client.post(
            f"{MODERATION}{created['id']}/suspend/",
            {"reason": "Held for a content check."},
            format="json",
        ),
        200,
    )
    assert held["status"] == "paused"
    assert held["moderation_hold"] is True
    blocked = owner_client.post(f"{PUBLISHER}{created['id']}/resume/")
    assert blocked.status_code == 409
    assert error_detail(blocked) == "This listing is held for moderation."
    reinstated = expect(staff_client.post(f"{MODERATION}{created['id']}/reinstate/"), 200)
    assert reinstated["status"] == "pending_review"
    assert reinstated["moderation_hold"] is False
    expect(staff_client.post(f"{MODERATION}{created['id']}/approve/"), 200)
    completed = expect(owner_client.post(f"{PUBLISHER}{created['id']}/complete/"), 200)
    assert completed["status"] == "completed"
    reopened = owner_client.post(f"{PUBLISHER}{created['id']}/resume/")
    assert reopened.status_code == 409
    actions = set(
        ListingEvent.objects.filter(listing_id=created["id"]).values_list("action", flat=True)
    )
    assert {
        "listing.created",
        "listing.updated",
        "listing.submitted",
        "listing.rejected",
        "listing.approved",
        "listing.published",
        "listing.paused",
        "listing.resumed",
        "listing.completed",
    } <= actions
    event = ListingEvent.objects.filter(listing_id=created["id"]).first()
    with pytest.raises(ValueError, match="append-only"):
        event.save()
    with pytest.raises(ValueError, match="append-only"):
        ListingEvent.objects.filter(pk=event.pk).update(action="listing.created")


def test_withdrawal_expiry_and_renewal(password) -> None:
    owner = verified_user("ada@example.com", password)
    staff = staff_user("staff@example.com", password)
    owner_client = client_for(owner)
    published = publish_personal(owner, owner_client, client_for(staff), title="Renew me")
    listing_id = published["listing"]["id"]
    row = Listing.objects.get(pk=listing_id)
    row.expires_at = timezone.now() - timedelta(minutes=5)
    row.save(update_fields=["expires_at", "updated_at"])
    overdue = owner_client.post(f"{PUBLISHER}{listing_id}/withdraw/")
    assert overdue.status_code == 409
    assert error_detail(overdue) == "This listing has expired."
    row.refresh_from_db()
    assert row.status == "expired"
    renewed = expect(owner_client.post(f"{PUBLISHER}{listing_id}/renew/"), 201)
    assert renewed["status"] == "draft"
    assert renewed["reference"] != published["listing"]["reference"]
    assert renewed["title"] == "Renew me"
    assert renewed["mandate_id"] == published["mandate"]["id"]
    row.refresh_from_db()
    assert row.status == "expired"
    public = client_for(staff).get(f"{LISTINGS}{row.reference}/")
    assert public.status_code == 404
    fresh = publish_personal(
        owner,
        owner_client,
        client_for(staff),
        name="Second flat",
        title="Leave it",
    )
    withdrawn = expect(owner_client.post(f"{PUBLISHER}{fresh['listing']['id']}/withdraw/"), 200)
    assert withdrawn["status"] == "withdrawn"
    again = owner_client.post(f"{PUBLISHER}{fresh['listing']['id']}/submit/")
    assert again.status_code == 409


def test_price_rules_and_history(password) -> None:
    owner = verified_user("ada@example.com", password)
    client = client_for(owner)
    record = active_property(client, name="Priced flat")
    mandate = self_mandate(client, owner, record["id"], "long_term_rental")
    wrong_period = client.post(
        PUBLISHER,
        {
            "property_id": record["id"],
            "mandate_id": mandate["id"],
            "listing_type": "long_term_rental",
            "title": "Monthly let",
            "description": "A twelve month let.",
            "price": sale_price(),
        },
        format="json",
    )
    assert wrong_period.status_code == 400
    assert error_detail(wrong_period) == "This billing period is not valid for the listing type."
    created = draft_listing(
        client,
        property_id=record["id"],
        mandate_id=mandate["id"],
        listing_type="long_term_rental",
        title="Monthly let",
        description="A twelve month let.",
        price=rent_price(),
    )
    assert created["price"]["amount_minor"] == 8_500_000
    assert created["price"]["billing_period"] == "month"
    assert created["price"]["currency"] == "KES"
    negative = client.patch(
        f"{PUBLISHER}{created['id']}/",
        {"price": rent_price(amount=-1)},
        format="json",
    )
    assert negative.status_code == 400
    floated = client.patch(
        f"{PUBLISHER}{created['id']}/",
        {"price": rent_price(amount=1.5)},
        format="json",
    )
    assert floated.status_code == 400
    flagged = client.patch(
        f"{PUBLISHER}{created['id']}/",
        {"price": rent_price(amount=True)},
        format="json",
    )
    assert flagged.status_code == 400
    currency = client.patch(
        f"{PUBLISHER}{created['id']}/",
        {"price": rent_price(currency="eur")},
        format="json",
    )
    assert currency.status_code == 400
    assert error_detail(currency) == "This currency is not supported."
    changed = expect(
        client.patch(
            f"{PUBLISHER}{created['id']}/",
            {"price": rent_price(amount=9_000_000)},
            format="json",
        ),
        200,
    )
    assert changed["price"]["amount_minor"] == 9_000_000
    assert len(changed["price_history"]) == 2
    previous = ListingPrice.objects.filter(listing_id=created["id"], superseded_at__isnull=False)
    assert previous.count() == 1
    bare_property = active_property(client, name="Bare price")
    bare_mandate = self_mandate(client, owner, bare_property["id"])
    bare = expect(
        client.post(
            PUBLISHER,
            {
                "property_id": bare_property["id"],
                "mandate_id": bare_mandate["id"],
                "listing_type": "sale",
                "title": "Ask later",
                "description": "Price follows.",
            },
            format="json",
        ),
        201,
    )
    photo = add_photo(client, bare_property["id"])
    attach(client, bare["id"], photo["id"])
    refused = client.post(f"{PUBLISHER}{bare['id']}/submit/")
    assert refused.status_code == 400
    assert error_detail(refused) == "A price is required before submission."


def test_one_open_listing_per_type_and_type_eligibility(password) -> None:
    owner = verified_user("ada@example.com", password)
    client = client_for(owner)
    record = active_property(client, name="Dual offer")
    sale = self_mandate(client, owner, record["id"], "sale")
    rental = self_mandate(client, owner, record["id"], "long_term_rental")
    first = draft_listing(client, property_id=record["id"], mandate_id=sale["id"])
    duplicate = client.post(
        PUBLISHER,
        {
            "property_id": record["id"],
            "mandate_id": sale["id"],
            "listing_type": "sale",
            "title": "Second sale",
            "description": "Should not open.",
            "price": sale_price(),
        },
        format="json",
    )
    assert duplicate.status_code == 409
    assert error_detail(duplicate) == "This property already has an open listing of this type."
    other = draft_listing(
        client,
        property_id=record["id"],
        mandate_id=rental["id"],
        listing_type="long_term_rental",
        title="Same home to let",
        price=rent_price(),
    )
    assert other["id"] != first["id"]
    land_mandate = self_mandate(client, owner, record["id"], "land_sale")
    refused = client.post(
        PUBLISHER,
        {
            "property_id": record["id"],
            "mandate_id": land_mandate["id"],
            "listing_type": "land_sale",
            "title": "Not land",
            "description": "An apartment is not a land sale.",
            "price": sale_price(),
        },
        format="json",
    )
    assert refused.status_code == 400
    assert error_detail(refused) == "This listing type is not available for the property type."


def test_media_validation_and_private_documents(password) -> None:
    owner = verified_user("ada@example.com", password)
    stranger = verified_user("stranger@example.com", password)
    client = client_for(owner)
    record = active_property(client, name="Photographed")
    sessions = f"/api/v1/marketplace/properties/{record['id']}/media/upload-sessions/"
    gif = client.post(
        sessions,
        {"kind": "photograph", "mime_type": "image/gif", "size_bytes": 32},
        format="json",
    )
    assert gif.status_code == 400
    claimed = client.post(
        sessions,
        {
            "kind": "photograph",
            "mime_type": "image/jpeg",
            "size_bytes": 32,
            "storage_reference": "https://cdn.example.com/photos/front.jpg?token=secret",
            "upload_status": "stored",
        },
        format="json",
    )
    assert claimed.status_code == 400
    assert error_detail(claimed) == "This media reference is not valid."
    direct = client.post(
        f"/api/v1/marketplace/properties/{record['id']}/media/",
        {
            "kind": "photograph",
            "mime_type": "image/jpeg",
            "storage_reference": "https://cdn.example.com/photos/front.jpg",
            "upload_status": "stored",
        },
        format="json",
    )
    assert direct.status_code == 405
    document = add_document(client, record["id"])
    assert document["visibility"] == "private"
    assert document["upload_status"] == "stored"
    photo = add_photo(client, record["id"])
    mandate = self_mandate(client, owner, record["id"])
    listing = draft_listing(client, property_id=record["id"], mandate_id=mandate["id"])
    attached_doc = client.post(
        f"{PUBLISHER}{listing['id']}/media/",
        {"property_media_id": document["id"]},
        format="json",
    )
    assert attached_doc.status_code == 400
    assert error_detail(attached_doc) == "A private document cannot be attached to a listing."
    attach(client, listing["id"], photo["id"])
    hidden = client_for(stranger).get(f"/api/v1/marketplace/properties/{record['id']}/media/")
    assert hidden.status_code == 404
    removed = expect(
        client.post(
            f"/api/v1/marketplace/properties/{record['id']}/media/{document['id']}/remove/"
        ),
        200,
    )
    assert removed["upload_status"] == "rejected"


def test_public_discovery_filters_sort_and_pagination(password) -> None:
    owner = verified_user("ada@example.com", password, first_name="Ada", last_name="Okello")
    staff = staff_user("staff@example.com", password)
    owner_client = client_for(owner)
    staff_client = client_for(staff)
    first = publish_personal(
        owner,
        owner_client,
        staff_client,
        name="Search one",
        title="Jacaranda apartment",
        with_locality=True,
    )
    second = publish_personal(
        owner,
        owner_client,
        staff_client,
        name="Search two",
        title="Quiet studio",
        price=sale_price(amount=5_000_000_00),
    )
    poa_property = active_property(owner_client, name="POA home")
    poa_mandate = self_mandate(owner_client, owner, poa_property["id"])
    poa_media = add_photo(owner_client, poa_property["id"])
    poa = draft_listing(
        owner_client,
        property_id=poa_property["id"],
        mandate_id=poa_mandate["id"],
        title="Price on request",
        price=sale_price(amount=None, presentation="price_on_application", negotiable=False),
    )
    attach(owner_client, poa["id"], poa_media["id"])
    expect(owner_client.post(f"{PUBLISHER}{poa['id']}/submit/"), 200)
    expect(staff_client.post(f"{MODERATION}{poa['id']}/approve/"), 200)
    anonymous = client_for(verified_user("nobody@example.com", password))
    anonymous.logout()
    listed = expect(anonymous.get(LISTINGS), 200)
    references = {row["reference"] for row in listed["results"]}
    assert first["listing"]["reference"] in references
    assert second["listing"]["reference"] in references
    assert poa["reference"] in references
    typed = expect(
        anonymous.get(LISTINGS, {"listing_type": "sale", "property_type": "residential"}),
        200,
    )
    assert typed["count"] >= 3
    county = expect(
        anonymous.get(LISTINGS, {"administrative_area_id": first["property"]["county_id"]}),
        200,
    )
    assert first["listing"]["reference"] in {row["reference"] for row in county["results"]}
    bedrooms = expect(anonymous.get(LISTINGS, {"bedrooms": 3, "bathrooms": 2}), 200)
    assert first["listing"]["reference"] in {row["reference"] for row in bedrooms["results"]}
    floor = expect(anonymous.get(LISTINGS, {"floor_area_min": "80", "land_area_min": "300"}), 200)
    assert first["listing"]["reference"] in {row["reference"] for row in floor["results"]}
    text = expect(anonymous.get(LISTINGS, {"q": "Jacaranda"}), 200)
    assert {row["reference"] for row in text["results"]} == {first["listing"]["reference"]}
    dear = expect(anonymous.get(LISTINGS, {"price_min": 10_000_000_00, "currency": "KES"}), 200)
    dear_refs = {row["reference"] for row in dear["results"]}
    assert first["listing"]["reference"] in dear_refs
    assert second["listing"]["reference"] not in dear_refs
    assert poa["reference"] not in dear_refs
    kes = expect(anonymous.get(LISTINGS, {"currency": "KES"}), 200)
    assert poa["reference"] in {row["reference"] for row in kes["results"]}
    ordered = expect(anonymous.get(LISTINGS, {"sort": "price"}), 200)
    amounts = [
        row["price"]["amount_minor"] for row in ordered["results"] if row["price"]["amount_minor"]
    ]
    assert amounts == sorted(amounts)
    page = expect(anonymous.get(LISTINGS, {"page_size": 1, "sort": "published_at"}), 200)
    assert page["count"] >= 3
    assert len(page["results"]) == 1
    assert page["next"]
    follow = expect(anonymous.get(page["next"]), 200)
    assert follow["results"][0]["reference"] != page["results"][0]["reference"]
    rejected = anonymous.get(LISTINGS, {"near": "1,1"})
    assert rejected.status_code == 400
    short = anonymous.get(LISTINGS, {"q": "a"})
    assert short.status_code == 400
    hidden = active_property(owner_client, name="Hidden draft")
    draft = draft_listing(
        owner_client,
        property_id=hidden["id"],
        mandate_id=self_mandate(owner_client, owner, hidden["id"])["id"],
        title="Secret draft",
    )
    public_refs = {row["reference"] for row in expect(anonymous.get(LISTINGS), 200)["results"]}
    assert draft["reference"] not in public_refs
    assert anonymous.get(f"{LISTINGS}{draft['reference']}/").status_code == 404


def test_assisted_description_cannot_publish_itself(password) -> None:
    owner = verified_user("ada@example.com", password)
    staff = staff_user("staff@example.com", password)
    client = client_for(owner)
    record = active_property(client, name="Assisted copy")
    mandate = self_mandate(client, owner, record["id"])
    media = add_photo(client, record["id"])
    created = draft_listing(
        client,
        property_id=record["id"],
        mandate_id=mandate["id"],
        description="Drafted with assistance.",
        description_origin="ai_assisted",
    )
    attach(client, created["id"], media["id"])
    blocked = client.post(f"{PUBLISHER}{created['id']}/submit/")
    assert blocked.status_code == 400
    assert error_detail(blocked) == "An assisted description must be accepted before submission."
    accepted = expect(client.post(f"{PUBLISHER}{created['id']}/accept-description/"), 200)
    assert accepted["description_origin"] == "ai_assisted"
    assert accepted["ai_approved_at"]
    edited = expect(
        client.patch(
            f"{PUBLISHER}{created['id']}/",
            {"description": "Drafted with assistance and then edited."},
            format="json",
        ),
        200,
    )
    assert edited["ai_approved_at"] is None
    blocked_again = client.post(f"{PUBLISHER}{created['id']}/submit/")
    assert blocked_again.status_code == 400
    expect(client.post(f"{PUBLISHER}{created['id']}/accept-description/"), 200)
    expect(client.post(f"{PUBLISHER}{created['id']}/submit/"), 200)
    approved = expect(client_for(staff).post(f"{MODERATION}{created['id']}/approve/"), 200)
    assert approved["description_origin"] == "ai_assisted"
    public = expect(client.get(f"{LISTINGS}{approved['reference']}/"), 200)
    assert public["description_origin"] == "ai_assisted"


def test_published_price_is_locked_and_writes_require_csrf(password) -> None:
    owner = verified_user("ada@example.com", password)
    staff = staff_user("staff@example.com", password)
    client = client_for(owner)
    published = publish_personal(owner, client, client_for(staff), title="Locked price")
    locked = client.patch(
        f"{PUBLISHER}{published['listing']['id']}/",
        {"price": sale_price(amount=1)},
        format="json",
    )
    assert locked.status_code == 409
    assert error_detail(locked) == "A price can be changed while the listing is a draft."
    browser = APIClient(enforce_csrf_checks=True)
    browser.force_login(owner)
    denied = browser.post(f"{PUBLISHER}{published['listing']['id']}/pause/", format="json")
    assert denied.status_code == 403
    assert error_detail(denied) == "CSRF verification failed."
    upload = browser.post(
        f"/api/v1/marketplace/properties/{published['property']['id']}/media/upload-sessions/",
        {"kind": "photograph", "mime_type": "image/jpeg", "size_bytes": 32},
        format="json",
    )
    assert upload.status_code == 403
    assert error_detail(upload) == "CSRF verification failed."
