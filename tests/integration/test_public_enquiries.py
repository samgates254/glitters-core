"""Public creation of an enquiry against an eligible listing."""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta

import pytest
from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APIClient
from tests.identity_support import error_detail
from tests.marketplace_support import (
    LISTINGS,
    MANDATES,
    MODERATION,
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
from tests.organizations_support import (
    ACCEPT,
    ORGANIZATIONS,
    client_for,
    organization_body,
    verified_user,
)

from apps.identity.models import User
from apps.marketplace.domain import (
    ENQUIRY_CHANNEL_WEB,
    ENQUIRY_CONSENT_REQUIRED,
    ENQUIRY_CONSENT_VERSION,
    ENQUIRY_EMAIL_INVALID,
    ENQUIRY_FIELD_REJECTED,
    ENQUIRY_INVALID,
    ENQUIRY_MESSAGE_MAX,
    ENQUIRY_MESSAGE_REQUIRED,
    ENQUIRY_MESSAGE_TOO_LONG,
    ENQUIRY_NAME_REQUIRED,
    ENQUIRY_PHONE_INVALID,
    NOT_FOUND,
    EnquiryEventAction,
    EnquiryStatus,
    MandateStatus,
)
from apps.marketplace.models import (
    Enquiry,
    EnquiryEvent,
    Listing,
    ListingMandate,
    ListingMedia,
    ListingPrice,
)
from apps.marketplace.throttles import EnquiryAddressThrottle, EnquiryCreationThrottle
from apps.organizations.domain import OrganizationStatus
from apps.organizations.models import Organization
from apps.properties.domain import PropertyStatus
from apps.properties.models import Property

pytestmark = pytest.mark.django_db
MISSING = "LST-MISSING1"
_FORBIDDEN = (
    "contact_name",
    "contact_email",
    "contact_phone",
    "message",
    "organization",
    "organization_id",
    "advertiser",
    "advertiser_id",
    "assigned_to",
    "assigned_to_id",
    "consent_at",
    "consent_version",
    "metadata",
    "request_id",
)


def _fields(**overrides) -> dict:
    body = {
        "contact_name": "Ada Lovelace",
        "contact_email": "seeker@example.com",
        "contact_phone": "+254 712 345 678",
        "message": "Is the apartment still available?",
        "consent": True,
    }
    body.update(overrides)
    return body


def _post(client, reference: str, payload: dict | None = None, **extra):
    return client.post(
        f"{LISTINGS}{reference}/enquiries/",
        _fields() if payload is None else payload,
        format="json",
        **extra,
    )


def _publish(password: str, suffix: str):
    owner = verified_user(f"owner-{suffix}@example.com", password)
    staff = staff_user(f"staff-{suffix}@example.com", password)
    published = publish_personal(
        owner,
        client_for(owner),
        client_for(staff),
        name=f"Home {suffix}",
        title=f"Title {suffix}",
    )
    return owner, staff, published


def _invitation_token() -> str:
    from django.core import mail

    for line in mail.outbox[-1].body.splitlines():
        if line.startswith("token:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError("missing token")


def _publish_organization(password: str, suffix: str):
    owner = verified_user(f"owner-{suffix}@example.com", password)
    agent = verified_user(f"agent-{suffix}@example.com", password)
    staff = staff_user(f"staff-{suffix}@example.com", password)
    owner_client = client_for(owner)
    record = active_property(owner_client, name=f"Agency home {suffix}")
    agency = expect(
        owner_client.post(
            ORGANIZATIONS,
            organization_body(
                display_name=f"Agency {suffix}",
                legal_name=f"Agency {suffix} Ltd",
            ),
            format="json",
        ),
        201,
    )
    invited = owner_client.post(
        f"{ORGANIZATIONS}{agency['id']}/invitations/",
        {"email": agent.email, "role": "manager"},
        format="json",
    )
    assert invited.status_code == 201
    accepted_invite = client_for(agent).post(
        ACCEPT,
        {"token": _invitation_token()},
        format="json",
    )
    assert accepted_invite.status_code == 200
    mandate = expect(
        owner_client.post(
            MANDATES,
            {
                "property_id": record["id"],
                "listing_scope": "sale",
                "representative_organization_id": agency["id"],
                "principal": "self",
            },
            format="json",
        ),
        201,
    )
    accepted = expect(client_for(agent).post(f"{MANDATES}{mandate['id']}/accept/"), 200)
    assert accepted["status"] == "active"
    photo = add_photo(client_for(agent), record["id"])
    listing = draft_listing(
        client_for(agent),
        property_id=record["id"],
        mandate_id=accepted["id"],
        title=f"Agency title {suffix}",
    )
    attach(client_for(agent), listing["id"], photo["id"])
    expect(client_for(agent).post(f"{PUBLISHER}{listing['id']}/submit/"), 200)
    approved = expect(client_for(staff).post(f"{MODERATION}{listing['id']}/approve/"), 200)
    return agent, agency, approved


def _assert_receipt(response, secret: str) -> dict:
    assert response.status_code == 201, response.content
    body = response.json()
    assert set(body) == {"id", "status"}
    assert body["status"] == EnquiryStatus.NEW
    encoded = response.content.decode()
    assert secret not in encoded
    for field in _FORBIDDEN:
        assert field not in body
    return body


def _assert_hidden(client, reference: str, *secrets: str) -> None:
    hidden = _post(client, reference)
    missing = _post(client, MISSING)
    assert hidden.status_code == missing.status_code == 404
    assert hidden.json()["errors"] == missing.json()["errors"] == {"detail": NOT_FOUND}
    encoded = hidden.content.decode()
    for secret in secrets:
        assert secret not in encoded
    assert Enquiry.objects.filter(listing__reference=reference).count() == 0


def _stored(enquiry_id: str) -> Enquiry:
    return Enquiry.objects.select_related("listing", "advertiser", "organization").get(
        pk=enquiry_id
    )


def test_personal_listing_accepts_a_public_enquiry(password) -> None:
    suffix = uuid.uuid4().hex[:8]
    owner, _staff, published = _publish(password, suffix)
    reference = published["listing"]["reference"]
    response = _post(APIClient(), reference, _fields(contact_email=f"Seeker-{suffix}@Example.com"))
    body = _assert_receipt(response, f"Seeker-{suffix}@Example.com")
    stored = _stored(body["id"])
    assert stored.listing_id == uuid.UUID(published["listing"]["id"])
    assert stored.advertiser_id == owner.id
    assert stored.organization_id is None
    assert stored.status == EnquiryStatus.NEW
    assert stored.assigned_to_id is None
    assert stored.contact_name == "Ada Lovelace"
    assert stored.contact_email == f"seeker-{suffix}@example.com"
    assert stored.contact_phone == "+254712345678"
    assert stored.message == "Is the apartment still available?"
    assert stored.consent_version == ENQUIRY_CONSENT_VERSION == "2026-11"
    assert stored.consent_at is not None
    assert timezone.now() - stored.consent_at < timedelta(minutes=5)
    assert stored.closed_at is None
    assert stored.anonymized_at is None
    event = EnquiryEvent.objects.get(enquiry=stored)
    assert event.action == EnquiryEventAction.CREATED
    assert event.actor_id is None
    assert event.request_id
    assert event.metadata == {
        "channel": ENQUIRY_CHANNEL_WEB,
        "consent_version": ENQUIRY_CONSENT_VERSION,
    }
    public = APIClient().get(f"{LISTINGS}{reference}/")
    assert public.status_code == 200
    public_body = public.content.decode().lower()
    assert f"seeker-{suffix}@example.com" not in public_body
    assert "+254712345678" not in public_body
    assert "ada lovelace" not in public_body
    assert "still available" not in public_body


def test_organization_listing_derives_advertiser_and_organization(password, locmem_email) -> None:
    suffix = uuid.uuid4().hex[:8]
    agent, agency, approved = _publish_organization(password, suffix)
    response = _post(APIClient(), approved["reference"])
    body = _assert_receipt(response, agency["id"])
    stored = _stored(body["id"])
    assert stored.advertiser_id == agent.id
    assert stored.organization_id == uuid.UUID(agency["id"])
    assert stored.status == EnquiryStatus.NEW
    assert stored.consent_version == ENQUIRY_CONSENT_VERSION
    assert (
        EnquiryEvent.objects.filter(
            enquiry=stored,
            action=EnquiryEventAction.CREATED,
        ).count()
        == 1
    )


@pytest.mark.parametrize(
    ("overrides", "detail"),
    [
        ({"contact_name": "   "}, ENQUIRY_NAME_REQUIRED),
        ({"contact_email": "not-an-email"}, ENQUIRY_EMAIL_INVALID),
        ({"message": " \n "}, ENQUIRY_MESSAGE_REQUIRED),
        ({"message": "x" * (ENQUIRY_MESSAGE_MAX + 1)}, ENQUIRY_MESSAGE_TOO_LONG),
        ({"consent": False}, ENQUIRY_CONSENT_REQUIRED),
        ({"contact_phone": "0712345678"}, ENQUIRY_PHONE_INVALID),
        ({"contact_phone": "not-a-phone"}, ENQUIRY_PHONE_INVALID),
    ],
)
def test_invalid_public_enquiry_input_is_rejected(overrides, detail) -> None:
    response = _post(APIClient(), MISSING, _fields(**overrides))
    assert response.status_code == 400
    assert error_detail(response) == detail
    assert Enquiry.objects.count() == 0


def test_missing_name_and_missing_consent_are_rejected() -> None:
    nameless = _fields()
    del nameless["contact_name"]
    nameless_response = _post(APIClient(), MISSING, nameless)
    assert nameless_response.status_code == 400
    assert error_detail(nameless_response) == ENQUIRY_NAME_REQUIRED
    unconsented = _fields()
    del unconsented["consent"]
    unconsented_response = _post(APIClient(), MISSING, unconsented)
    assert unconsented_response.status_code == 400
    assert error_detail(unconsented_response) == ENQUIRY_CONSENT_REQUIRED
    assert Enquiry.objects.count() == 0


def test_client_cannot_choose_advertiser_organization_or_assignment(password) -> None:
    suffix = uuid.uuid4().hex[:8]
    owner, _staff, published = _publish(password, suffix)
    stranger = verified_user(f"stranger-{suffix}@example.com", password)
    reference = published["listing"]["reference"]
    extras = (
        {"advertiser_id": str(stranger.id)},
        {"advertiser": str(stranger.id)},
        {"organization_id": str(uuid.uuid4())},
        {"organization": str(uuid.uuid4())},
        {"advertising_organization_id": str(uuid.uuid4())},
        {"assigned_to": str(stranger.id)},
        {"assigned_to_id": str(stranger.id)},
        {"listing_owner": str(owner.id)},
        {"user_id": str(stranger.id)},
        {"status": EnquiryStatus.CLOSED},
        {"consent_version": "1999-01"},
    )
    client = client_for(stranger)
    for extra in extras:
        response = _post(client, reference, _fields(**extra))
        assert response.status_code == 400
        assert error_detail(response) == ENQUIRY_FIELD_REJECTED
    assert Enquiry.objects.filter(listing__reference=reference).count() == 0
    stored_response = _post(client, reference)
    body = _assert_receipt(stored_response, stranger.email)
    stored = _stored(body["id"])
    assert stored.advertiser_id == owner.id
    assert stored.organization_id is None
    assert stored.assigned_to_id is None


def test_unpublished_listing_is_indistinguishable_from_a_missing_one(password) -> None:
    suffix = uuid.uuid4().hex[:8]
    owner = verified_user(f"owner-{suffix}@example.com", password)
    client = client_for(owner)
    record = active_property(client, name=f"Draft {suffix}")
    mandate = self_mandate(client, owner, record["id"])
    title = f"Hidden title {suffix}"
    draft = draft_listing(client, property_id=record["id"], mandate_id=mandate["id"], title=title)
    _assert_hidden(APIClient(), draft["reference"], title, owner.email)


def test_expired_listing_receives_no_enquiry(password) -> None:
    suffix = uuid.uuid4().hex[:8]
    _owner, _staff, published = _publish(password, suffix)
    listing = Listing.objects.get(pk=published["listing"]["id"])
    listing.expires_at = timezone.now() - timedelta(minutes=5)
    listing.save(update_fields=["expires_at", "updated_at"])
    _assert_hidden(APIClient(), listing.reference, listing.title)
    listing.refresh_from_db()
    assert listing.status == "published"


def test_moderation_hold_receives_no_enquiry(password) -> None:
    suffix = uuid.uuid4().hex[:8]
    _owner, staff, published = _publish(password, suffix)
    listing_id = published["listing"]["id"]
    held = expect(
        client_for(staff).post(
            f"{MODERATION}{listing_id}/suspend/",
            {"reason": "Held for a content check."},
            format="json",
        ),
        200,
    )
    assert held["moderation_hold"] is True
    _assert_hidden(APIClient(), published["listing"]["reference"], published["listing"]["title"])


def test_inactive_property_receives_no_enquiry(password) -> None:
    suffix = uuid.uuid4().hex[:8]
    _owner, _staff, published = _publish(password, suffix)
    Property.objects.filter(pk=published["property"]["id"]).update(
        status=PropertyStatus.ARCHIVED,
        archived_at=timezone.now(),
    )
    _assert_hidden(APIClient(), published["listing"]["reference"], published["listing"]["title"])


def test_invalid_mandate_receives_no_enquiry(password) -> None:
    suffix = uuid.uuid4().hex[:8]
    _owner, _staff, published = _publish(password, suffix)
    ListingMandate.objects.filter(pk=published["mandate"]["id"]).update(
        status=MandateStatus.REVOKED,
        revoked_at=timezone.now(),
    )
    _assert_hidden(APIClient(), published["listing"]["reference"], published["listing"]["title"])


def test_unverified_advertiser_receives_no_enquiry(password) -> None:
    suffix = uuid.uuid4().hex[:8]
    owner, _staff, published = _publish(password, suffix)
    User.objects.filter(pk=owner.id).update(email_verified_at=None)
    _assert_hidden(APIClient(), published["listing"]["reference"], owner.email)


def test_inactive_organization_receives_no_enquiry(password, locmem_email) -> None:
    suffix = uuid.uuid4().hex[:8]
    _agent, agency, approved = _publish_organization(password, suffix)
    Organization.objects.filter(pk=agency["id"]).update(status=OrganizationStatus.SUSPENDED)
    _assert_hidden(APIClient(), approved["reference"], agency["id"], agency["display_name"])


def test_listing_without_a_verified_photograph_receives_no_enquiry(password) -> None:
    suffix = uuid.uuid4().hex[:8]
    _owner, _staff, published = _publish(password, suffix)
    assert ListingMedia.objects.filter(listing_id=published["listing"]["id"]).delete()[0] == 1
    _assert_hidden(APIClient(), published["listing"]["reference"], published["listing"]["title"])


def test_invalid_price_receives_no_enquiry(password) -> None:
    suffix = uuid.uuid4().hex[:8]
    _owner, _staff, published = _publish(password, suffix)
    updated = ListingPrice.objects.filter(
        listing_id=published["listing"]["id"],
        superseded_at__isnull=True,
    ).update(billing_period="month")
    assert updated == 1
    _assert_hidden(APIClient(), published["listing"]["reference"], published["listing"]["title"])


def test_event_failure_rolls_back_the_enquiry(password, monkeypatch, caplog) -> None:
    suffix = uuid.uuid4().hex[:8]
    _owner, _staff, published = _publish(password, suffix)
    email = f"rollback-{suffix}@example.com"

    def _fail(self, *args, **kwargs):
        raise ValueError("enquiry event failed")

    monkeypatch.setattr(EnquiryEvent, "save", _fail)
    caplog.set_level(logging.INFO, logger="glitters.marketplace")
    response = _post(APIClient(), published["listing"]["reference"], _fields(contact_email=email))
    assert response.status_code == 400
    assert error_detail(response) == ENQUIRY_INVALID
    encoded = response.content.decode()
    assert email not in encoded
    assert "enquiry event failed" not in encoded
    assert email not in caplog.text
    assert "enquiry event failed" not in caplog.text
    assert Enquiry.objects.filter(listing__reference=published["listing"]["reference"]).count() == 0
    assert EnquiryEvent.objects.count() == 0


def test_public_enquiry_is_throttled_by_address_and_email(password, settings, monkeypatch) -> None:
    settings.ENQUIRY_THROTTLE = True
    monkeypatch.setattr(EnquiryCreationThrottle, "get_rate", lambda self: "2/min")
    monkeypatch.setattr(EnquiryAddressThrottle, "get_rate", lambda self: "30/hour")
    cache.clear()
    suffix = uuid.uuid4().hex[:8]
    _owner, _staff, published = _publish(password, suffix)
    reference = published["listing"]["reference"]
    client = APIClient()
    email = f"throttle-{suffix}@example.com"
    body = _fields(contact_email=email)
    statuses = [
        _post(client, reference, body).status_code,
        _post(client, reference, body).status_code,
        _post(client, reference, body).status_code,
    ]
    assert statuses == [201, 201, 429]
    other = _post(client, reference, _fields(contact_email=f"other-{suffix}@example.com"))
    assert other.status_code == 201
    moved = _post(
        client,
        reference,
        body,
        REMOTE_ADDR="203.0.113.20",
    )
    assert moved.status_code == 201
    assert Enquiry.objects.filter(listing__reference=reference).count() == 4


def test_rotating_emails_from_one_address_are_throttled(password, settings, monkeypatch) -> None:
    settings.ENQUIRY_THROTTLE = True
    monkeypatch.setattr(EnquiryCreationThrottle, "get_rate", lambda self: "100/hour")
    monkeypatch.setattr(EnquiryAddressThrottle, "get_rate", lambda self: "2/min")
    cache.clear()
    suffix = uuid.uuid4().hex[:8]
    _owner, _staff, published = _publish(password, suffix)
    reference = published["listing"]["reference"]
    client = APIClient()
    first = _post(client, reference, _fields(contact_email=f"a-{suffix}@example.com"))
    second = _post(client, reference, _fields(contact_email=f"b-{suffix}@example.com"))
    third = _post(client, reference, _fields(contact_email=f"c-{suffix}@example.com"))
    assert [first.status_code, second.status_code, third.status_code] == [201, 201, 429]
    assert f"c-{suffix}@example.com" not in third.content.decode()


def test_browser_enquiry_requires_a_csrf_token(password) -> None:
    suffix = uuid.uuid4().hex[:8]
    _owner, _staff, published = _publish(password, suffix)
    reference = published["listing"]["reference"]
    email = f"csrf-{suffix}@example.com"
    client = APIClient(enforce_csrf_checks=True)
    missing = _post(client, reference, _fields(contact_email=email))
    assert missing.status_code == 403
    assert error_detail(missing) == "CSRF verification failed."
    assert email not in missing.content.decode()
    issued = client.get("/api/v1/auth/csrf/")
    assert issued.status_code == 200
    token = client.cookies["csrftoken"].value
    accepted = _post(
        client,
        reference,
        _fields(contact_email=email),
        HTTP_X_CSRFTOKEN=token,
    )
    assert accepted.status_code == 201


def test_contact_details_are_not_written_to_application_logs(password, caplog) -> None:
    suffix = uuid.uuid4().hex[:8]
    _owner, _staff, published = _publish(password, suffix)
    email = f"logged-{suffix}@example.com"
    message = f"Gate code {suffix} stays private."
    caplog.set_level(logging.INFO)
    response = _post(
        APIClient(),
        published["listing"]["reference"],
        _fields(
            contact_name=f"Private Name {suffix}",
            contact_email=email,
            contact_phone="+14155552671",
            message=message,
        ),
    )
    assert response.status_code == 201
    text = caplog.text
    assert email not in text
    assert f"Private Name {suffix}" not in text
    assert "+14155552671" not in text
    assert message not in text
    assert "Public enquiry stored." in text
