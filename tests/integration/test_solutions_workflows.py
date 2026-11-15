"""Workflows for auctions, insurance, projects, investments, and services."""

from __future__ import annotations

import uuid

import pytest
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework.test import APIClient
from tests.identity_support import enable_confirmed_mfa
from tests.marketplace_support import staff_user
from tests.organizations_support import verified_user

from apps.organizations.models import Organization, OrganizationMembership
from apps.solutions.domain import INSURANCE_QUOTE_ADAPTERS, SERVICE_CATALOGUE
from apps.solutions.models import CommercialEnquiryEvent

pytestmark = pytest.mark.django_db

AUCTIONS = "/api/v1/solutions/auctions/"
PARTNER_AUCTIONS = "/api/v1/solutions/partner/auctions/"
STAFF_AUCTIONS = "/api/v1/solutions/staff/auctions/"
PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF"


def _org(name: str) -> Organization:
    return Organization.objects.create(
        legal_name=f"{name} Limited",
        display_name=name,
        slug=f"{name.lower().replace(' ', '-')}-{uuid.uuid4().hex[:6]}",
        organization_type="professional_service",
        status="active",
        country="KE",
    )


def _join(user, organization, role="owner"):
    OrganizationMembership.objects.create(
        organization=organization,
        user=user,
        role=role,
        status="active",
        source="creator",
        joined_at=timezone.now(),
    )


def _client(user) -> APIClient:
    client = APIClient()
    client.force_login(user)
    return client


def _auction(organization_id: str, **overrides) -> dict:
    body = {
        "organization_id": organization_id,
        "title": "Industrial plot, Machakos",
        "category": "land",
        "description": "A parcel offered by a partner institution.",
        "county": "Machakos",
        "town": "Athi River",
        "location_label": "Athi River",
        "size_label": "0.5 acres",
        "tenure": "freehold",
        "guide_price_minor": 800000000,
        "reserve_price_minor": 700000000,
        "institution_name": "Harbour Bank",
        "auctioneer_name": "Plain Auctioneers",
        "auctioneer_licence_reference": "SUPPLIED-1",
        "contact_email": "desk@harbour.example",
        "terms": "Sold as inspected by the auctioneer.",
        "deposit_requirements": "The auctioneer states the deposit.",
        "payment_instructions": "Pay the auctioneer as instructed.",
    }
    body.update(overrides)
    return body


def _post_status(client, reference, status, expected):
    return _expect(
        client.post(
            f"{STAFF_AUCTIONS}{reference}/status/",
            {"status": status},
            format="json",
        ),
        expected,
    )


def _expect(response, status: int):
    assert response.status_code == status, response.content
    if status == 204:
        return None
    if "text/csv" in response["Content-Type"]:
        return response.content.decode()
    return response.json()


def test_auction_stays_private_until_staff_publish(password):
    bank = _org("Harbour Bank")
    partner = verified_user("partner@harbour.example", password)
    _join(partner, bank)
    stranger = verified_user("other@example.com", password)
    _join(stranger, _org("Other House"))
    member = verified_user("member@harbour.example", password)
    _join(member, bank, role="member")
    staff = staff_user("staff-auction@glitters.example", password)
    partner_client = _client(partner)
    created = _expect(
        partner_client.post(PARTNER_AUCTIONS, _auction(str(bank.id)), format="json"),
        201,
    )
    reference = created["reference"]
    assert created["status"] == "draft"
    assert created["reserve_price"]["amount_minor"] == 700000000
    assert _expect(APIClient().get(f"{AUCTIONS}{reference}/"), 404)
    submitted = _expect(partner_client.post(f"{PARTNER_AUCTIONS}{reference}/submit/"), 200)
    assert submitted["status"] == "pending_verification"
    denied = _post_status(partner_client, reference, "published", 403)
    assert denied["errors"]["detail"]
    _expect(_client(member).post(PARTNER_AUCTIONS, _auction(str(bank.id)), format="json"), 403)
    _post_status(_client(staff), reference, "published", 400)
    _post_status(_client(staff), reference, "approved", 200)
    published = _post_status(_client(staff), reference, "published", 200)
    assert published["status"] == "published"
    public = _expect(APIClient().get(f"{AUCTIONS}{reference}/"), 200)
    assert public["reserve_price"] is None
    assert public["reserve_publication"] == "withheld"
    assert public["auctioneer_verification"] == "not_checked"
    assert "not been checked" in public["auctioneer_licence_statement"]
    assert _expect(_client(stranger).get("/api/v1/solutions/partner/auctions/"), 200)["count"] == 0
    owned = _expect(partner_client.get(PARTNER_AUCTIONS), 200)
    assert owned["count"] == 1


def test_private_auction_documents_are_not_public(password, tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    bank = _org("Document Bank")
    partner = verified_user("docs@bank.example", password)
    _join(partner, bank)
    other = verified_user("docs-other@example.com", password)
    _join(other, _org("Elsewhere"))
    staff = staff_user("docs-staff@glitters.example", password)
    partner_client = _client(partner)
    created = _expect(
        partner_client.post(PARTNER_AUCTIONS, _auction(str(bank.id)), format="json"),
        201,
    )
    reference = created["reference"]
    upload = SimpleUploadedFile("notice.pdf", PDF, content_type="application/pdf")
    stored = _expect(
        partner_client.post(
            f"{PARTNER_AUCTIONS}{reference}/documents/",
            {"upload": upload, "kind": "document", "content_type": "application/pdf"},
            format="multipart",
        ),
        201,
    )
    assert stored["visibility"] == "private"
    assert APIClient().get(f"/api/v1/solutions/documents/{stored['id']}/").status_code == 404
    _post_status(_client(staff), reference, "approved", 200)
    _post_status(_client(staff), reference, "published", 200)
    assert APIClient().get(f"/api/v1/solutions/documents/{stored['id']}/").status_code == 404
    private = f"/api/v1/solutions/documents/{stored['id']}/private/"
    assert _client(other).get(private).status_code == 404
    assert partner_client.get(private).status_code == 200
    public_upload = SimpleUploadedFile("plan.pdf", PDF, content_type="application/pdf")
    public_doc = _expect(
        _client(staff).post(
            f"{STAFF_AUCTIONS}{reference}/documents/",
            {
                "upload": public_upload,
                "kind": "document",
                "content_type": "application/pdf",
                "visibility": "public",
            },
            format="multipart",
        ),
        201,
    )
    assert APIClient().get(f"/api/v1/solutions/documents/{public_doc['id']}/").status_code == 200
    listing = _expect(APIClient().get(f"{AUCTIONS}{reference}/"), 200)
    assert stored["id"] not in {item["id"] for item in listing["documents"]}
    assert public_doc["id"] in {item["id"] for item in listing["documents"]}


def test_insurance_quotes_follow_the_configured_path(
    password,
    locmem_email,
    django_capture_on_commit_callbacks,
):
    staff = staff_user("insurance-staff@glitters.example", password)
    client = _client(staff)
    _expect(
        client.post(
            "/api/v1/solutions/staff/insurance/products/",
            {
                "slug": "motor-request",
                "name": "Motor request",
                "line": "general",
                "category": "motor",
                "summary": "A partner motor product. No premium is calculated here.",
                "quotation_mode": "request",
                "partner_name": "Plain Insurer",
                "status": "published",
            },
            format="json",
        ),
        201,
    )
    body = {
        "contact_name": "Amina Otieno",
        "contact_email": "amina@example.com",
        "message": "Please ask the insurer to contact me about private motor cover.",
        "consent": True,
        "preferred_contact": "email",
        "intent": "quote",
        "detail": "Private car",
    }
    with django_capture_on_commit_callbacks(execute=True):
        recorded = _expect(
            APIClient().post(
                "/api/v1/solutions/insurance/products/motor-request/enquiries/",
                body,
                format="json",
            ),
            201,
        )
    assert recorded["quotation"]["state"] == "request_recorded"
    assert "premium_minor" not in recorded["quotation"]
    assert "contact_email" not in recorded
    assert len(mail.outbox) == 1
    assert "not an insurer quotation" in mail.outbox[0].body
    assert "KES" not in mail.outbox[0].body
    _expect(
        client.post(
            "/api/v1/solutions/staff/insurance/products/",
            {
                "slug": "motor-api",
                "name": "Motor API",
                "line": "general",
                "category": "motor",
                "summary": "Waiting for the insurer API.",
                "quotation_mode": "api",
                "api_configured": True,
                "partner_name": "Plain Insurer",
                "status": "published",
            },
            format="json",
        ),
        201,
    )
    waiting = _expect(
        APIClient().post(
            "/api/v1/solutions/insurance/products/motor-api/enquiries/",
            body,
            format="json",
        ),
        201,
    )
    assert waiting["quotation"]["state"] == "awaiting_authorised_partner"
    assert "premium_minor" not in waiting["quotation"]
    INSURANCE_QUOTE_ADAPTERS["motor-api"] = lambda product: {
        "partner_reference": "Q-1",
        "premium_minor": 250000,
        "summary": "Partner premium",
    }
    try:
        quoted = _expect(
            APIClient().post(
                "/api/v1/solutions/insurance/products/motor-api/enquiries/",
                {**body, "contact_email": "amina2@example.com"},
                format="json",
            ),
            201,
        )
    finally:
        INSURANCE_QUOTE_ADAPTERS.clear()
    assert quoted["quotation"]["premium_source"] == "authorised_partner_api"
    assert quoted["quotation"]["premium_minor"] == 250000


def test_life_enquiries_reject_medical_detail(password):
    staff = staff_user("life-staff@glitters.example", password)
    _expect(
        _client(staff).post(
            "/api/v1/solutions/staff/insurance/products/",
            {
                "slug": "family-cover",
                "name": "Family protection",
                "line": "life",
                "category": "family_protection",
                "summary": "The insurer collects medical information on its own form.",
                "quotation_mode": "request",
                "partner_name": "Plain Life",
                "status": "published",
            },
            format="json",
        ),
        201,
    )
    base = {
        "contact_name": "John Kariuki",
        "contact_email": "john@example.com",
        "message": "Please ask the insurer to send the secure application.",
        "consent": True,
        "preferred_contact": "phone",
        "contact_phone": "+254712345678",
        "intent": "quote",
    }
    refused = APIClient().post(
        "/api/v1/solutions/insurance/products/family-cover/enquiries/",
        {**base, "detail": "individual", "medical_details": "blood pressure"},
        format="json",
    )
    assert refused.status_code == 400
    accepted = _expect(
        APIClient().post(
            "/api/v1/solutions/insurance/products/family-cover/enquiries/",
            {**base, "detail": "family"},
            format="json",
        ),
        201,
    )
    assert accepted["status"] == "new"


def test_projects_investments_and_services_are_staff_managed(password):
    staff = staff_user("catalogue-staff@glitters.example", password)
    client = _client(staff)
    _expect(
        client.post(
            "/api/v1/solutions/staff/projects/",
            {
                "slug": "riverside",
                "name": "Riverside",
                "location_label": "Nairobi",
                "description": "A residential development.",
                "category": "residential",
                "status": "draft",
            },
            format="json",
        ),
        201,
    )
    assert APIClient().get("/api/v1/solutions/projects/riverside/").status_code == 404
    opened = _expect(
        client.patch(
            "/api/v1/solutions/staff/projects/riverside/",
            {
                "slug": "riverside",
                "name": "Riverside",
                "developer_name": "Riverside Developer",
                "location_label": "Nairobi",
                "description": "A residential development.",
                "category": "residential",
                "status": "upcoming",
                "contact_email": "sales@riverside.example",
            },
            format="json",
        ),
        200,
    )
    assert opened["status"] == "upcoming"
    hidden = _expect(
        client.post(
            "/api/v1/solutions/staff/investments/",
            {
                "name": "Sample equity",
                "symbol": "SMPL",
                "category": "equity",
                "summary": "A partner-listed equity. No order can be placed here.",
                "risk_disclosure": "Capital is at risk. Past prices are not a forecast.",
                "price_minor": 12300,
                "partner_name": "Plain Securities",
                "status": "published",
            },
            format="json",
        ),
        201,
    )
    public = _expect(APIClient().get(f"/api/v1/solutions/investments/{hidden['reference']}/"), 200)
    assert public["price"] is None
    assert "guaranteed_return" not in public
    shown = _expect(
        client.patch(
            f"/api/v1/solutions/staff/investments/{hidden['reference']}/",
            {
                "name": "Sample equity",
                "symbol": "SMPL",
                "category": "equity",
                "summary": "A partner-listed equity. No order can be placed here.",
                "risk_disclosure": "Capital is at risk. Past prices are not a forecast.",
                "price_minor": 12300,
                "data_source": "Plain Securities delayed feed",
                "price_as_of": "2026-11-11T09:00:00Z",
                "partner_name": "Plain Securities",
                "status": "published",
            },
            format="json",
        ),
        200,
    )
    assert shown["price"]["amount_minor"] == 12300
    assert shown["price"]["data_source"] == "Plain Securities delayed feed"
    categories = _expect(APIClient().get("/api/v1/solutions/services/categories/"), 200)
    slugs = {row["slug"] for row in categories["results"]}
    assert slugs == {item[1] for item in SERVICE_CATALOGUE}
    _expect(
        client.post(
            "/api/v1/solutions/staff/providers/",
            {
                "category_slug": "legal-services",
                "name": "A licensed advocate",
                "summary": "Property work.",
                "status": "published",
            },
            format="json",
        ),
        400,
    )
    provider = _expect(
        client.post(
            "/api/v1/solutions/staff/providers/",
            {
                "category_slug": "legal-services",
                "name": "Harbour Chambers",
                "coverage": "Nairobi",
                "summary": "Property-related introductions.",
                "credentials_note": "Advocate of the High Court",
                "status": "published",
            },
            format="json",
        ),
        201,
    )
    assert provider["credentials_checked"] is False
    assert provider["credentials_note"] == ""
    assert "not been checked" in provider["credentials_statement"]


def test_enquiries_are_restricted_to_the_assigned_partner(password):
    bank = _org("Assigned Bank")
    other = _org("Unassigned Bank")
    owner = verified_user("owner@assigned.example", password)
    outsider = verified_user("owner@unassigned.example", password)
    _join(owner, bank)
    _join(outsider, other)
    staff = staff_user("enquiry-staff@glitters.example", password)
    staff_client = _client(staff)
    created = _expect(
        _client(owner).post(PARTNER_AUCTIONS, _auction(str(bank.id)), format="json"),
        201,
    )
    reference = created["reference"]
    _post_status(staff_client, reference, "approved", 200)
    _post_status(staff_client, reference, "published", 200)
    enquiry = _expect(
        APIClient().post(
            f"{AUCTIONS}{reference}/enquiries/",
            {
                "contact_name": "Wanjiku",
                "contact_email": "wanjiku@example.com",
                "message": "Please send the viewing dates.",
                "consent": True,
                "preferred_contact": "email",
                "intent": "viewing",
            },
            format="json",
        ),
        201,
    )
    assert APIClient().get("/api/v1/solutions/staff/enquiries/export/").status_code in {401, 403}
    assert _expect(_client(outsider).get("/api/v1/solutions/partner/enquiries/"), 200)["count"] == 0
    outsider_url = f"/api/v1/solutions/partner/enquiries/{enquiry['reference']}/"
    assert _client(outsider).get(outsider_url).status_code == 404
    visible = _expect(_client(owner).get(outsider_url), 200)
    assert visible["contact_email"] == "wanjiku@example.com"
    assert visible["internal_notes"] == ""
    noted = _expect(
        staff_client.patch(
            f"/api/v1/solutions/staff/enquiries/{enquiry['reference']}/",
            {"note": "Call the customer tomorrow."},
            format="json",
        ),
        200,
    )
    assert noted["internal_notes"] == "Call the customer tomorrow."
    again = _expect(_client(owner).get(outsider_url), 200)
    assert again["internal_notes"] == ""
    exported = _expect(
        staff_client.get("/api/v1/solutions/staff/enquiries/export/?category=auction"),
        200,
    )
    assert "wanjiku@example.com" in exported
    closed = _expect(
        staff_client.patch(
            f"/api/v1/solutions/staff/enquiries/{enquiry['reference']}/",
            {"status": "closed"},
            format="json",
        ),
        200,
    )
    assert closed["status"] == "closed"
    anonymized = _expect(
        staff_client.patch(
            f"/api/v1/solutions/staff/enquiries/{enquiry['reference']}/",
            {"anonymize": True},
            format="json",
        ),
        200,
    )
    assert anonymized["contact_email"] is None
    assert anonymized["message"] is None
    event = CommercialEnquiryEvent.objects.filter(enquiry__reference=enquiry["reference"]).first()
    assert "wanjiku@example.com" not in str(event.metadata)


def test_staff_without_mfa_cannot_publish(password):
    user = verified_user("nofa@glitters.example", password, is_staff=True)
    response = _client(user).post(
        "/api/v1/solutions/staff/projects/",
        {
            "slug": "blocked",
            "name": "Blocked",
            "location_label": "Nairobi",
            "description": "Should not be created.",
            "category": "land",
            "status": "draft",
        },
        format="json",
    )
    assert response.status_code == 403
    enable_confirmed_mfa(user)
    assert (
        _client(user)
        .post(
            "/api/v1/solutions/staff/projects/",
            {
                "slug": "blocked",
                "name": "Blocked",
                "location_label": "Nairobi",
                "description": "Created after MFA.",
                "category": "land",
                "status": "draft",
            },
            format="json",
        )
        .status_code
        == 201
    )
