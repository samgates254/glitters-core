"""Authenticated enquiry inbox. Authorization is the queryset, not the page."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone
from tests.identity_support import error_detail
from tests.marketplace_support import (
    MANDATES,
    active_property,
    draft_listing,
    expect,
    self_mandate,
    staff_user,
)
from tests.organizations_support import ORGANIZATIONS, client_for, organization_body, verified_user

from apps.identity.domain import AccountStatus
from apps.marketplace.domain import (
    MEMBERSHIP_NOT_ACTIVE,
    NOT_FOUND,
    ORG_NOT_ACTIVE,
    PERMISSION_DENIED,
    EnquiryStatus,
)
from apps.marketplace.models import Enquiry, Listing
from apps.organizations.domain import MembershipSource, MembershipStatus, OrganizationStatus
from apps.organizations.models import Organization, OrganizationMembership

pytestmark = pytest.mark.django_db

ENQUIRIES = "/api/v1/marketplace/enquiries/"
INBOX_FIELDS = {
    "id",
    "listing_reference",
    "contact_name",
    "contact_email",
    "contact_phone",
    "message",
    "status",
    "consent_at",
    "consent_version",
    "created_at",
    "assignment",
}


def _store(listing: Listing, **overrides) -> Enquiry:
    payload = {
        "listing": listing,
        "advertiser_id": listing.advertiser_id,
        "organization_id": listing.advertising_organization_id,
        "contact_name": "Ada Lovelace",
        "contact_email": "seeker@example.com",
        "contact_phone": "+254712345678",
        "message": "Is the apartment still available?",
        "consent_at": timezone.now(),
        "consent_version": "2026-11",
    }
    payload.update(overrides)
    return Enquiry.objects.create(**payload)


def _personal(password: str, suffix: str):
    owner = verified_user(f"ada-{suffix}@example.com", password)
    client = client_for(owner)
    record = active_property(client, name=f"Home {suffix}")
    mandate = self_mandate(client, owner, record["id"])
    created = draft_listing(
        client,
        property_id=record["id"],
        mandate_id=mandate["id"],
        title=f"Title {suffix}",
    )
    return owner, client, Listing.objects.get(pk=created["id"])


def _join(organization_id, user, role: str) -> OrganizationMembership:
    return OrganizationMembership.objects.create(
        organization_id=organization_id,
        user=user,
        role=role,
        status=MembershipStatus.ACTIVE,
        source=MembershipSource.INVITATION,
        joined_at=timezone.now(),
    )


def _organization_enquiry(password: str, suffix: str, roles: tuple[str, ...] = ()):
    owner = verified_user(f"owner-{suffix}@example.com", password)
    owner_client = client_for(owner)
    agency = expect(
        owner_client.post(
            ORGANIZATIONS,
            organization_body(
                display_name=f"Agency {suffix}",
                legal_name=f"Agency {suffix} Limited",
            ),
            format="json",
        ),
        201,
    )
    people = {"owner": owner}
    for role in roles:
        person = verified_user(f"{role}-{suffix}@example.com", password)
        _join(agency["id"], person, role)
        people[role] = person
    record = active_property(owner_client, name=f"Agency home {suffix}")
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
    if mandate["status"] != "active":
        mandate = expect(owner_client.post(f"{MANDATES}{mandate['id']}/accept/"), 200)
    created = draft_listing(
        owner_client,
        property_id=record["id"],
        mandate_id=mandate["id"],
        title=f"Agency title {suffix}",
    )
    listing = Listing.objects.get(pk=created["id"])
    enquiry = _store(
        listing,
        contact_email=f"seeker-{suffix}@example.com",
        message=f"Message {suffix}",
    )
    return people, agency, listing, enquiry


def _detail(client, enquiry_id):
    return client.get(f"{ENQUIRIES}{enquiry_id}/")


def test_personal_advertiser_reads_only_necessary_fields(password) -> None:
    suffix = uuid.uuid4().hex[:8]
    owner, client, listing = _personal(password, suffix)
    enquiry = _store(listing, contact_email=f"seeker-{suffix}@example.com")
    listed = expect(client.get(ENQUIRIES), 200)
    assert listed["count"] == 1
    row = listed["results"][0]
    assert set(row) == INBOX_FIELDS
    assert row["id"] == str(enquiry.id)
    assert row["listing_reference"] == listing.reference
    assert row["contact_name"] == "Ada Lovelace"
    assert row["contact_email"] == enquiry.contact_email
    assert row["contact_phone"] == "+254712345678"
    assert row["message"] == enquiry.message
    assert row["status"] == EnquiryStatus.NEW
    assert row["consent_version"] == "2026-11"
    assert row["consent_at"]
    assert row["created_at"]
    assert row["assignment"] is None
    detail = expect(_detail(client, enquiry.id), 200)
    assert detail == row
    encoded = client.get(ENQUIRIES).content.decode()
    assert owner.email not in encoded
    assert "organization" not in row
    assert client.post(ENQUIRIES, {}, format="json").status_code == 405
    assert client.post(f"{ENQUIRIES}{enquiry.id}/", {}, format="json").status_code == 405


def test_unrelated_personal_advertiser_is_denied(password) -> None:
    suffix = uuid.uuid4().hex[:8]
    _owner, _client, listing = _personal(password, suffix)
    enquiry = _store(listing, contact_email=f"secret-{suffix}@example.com", message="Private note")
    stranger, stranger_client, _other = _personal(password, f"other-{suffix}")
    _store(
        _other,
        contact_email=f"own-{suffix}@example.com",
        message="Own note",
    )
    listed = expect(stranger_client.get(ENQUIRIES), 200)
    assert listed["count"] == 1
    assert listed["results"][0]["contact_email"] == f"own-{suffix}@example.com"
    denied = _detail(stranger_client, enquiry.id)
    assert denied.status_code == 404
    assert error_detail(denied) == NOT_FOUND
    body = denied.content.decode()
    assert enquiry.contact_email not in body
    assert "Private note" not in body
    assert stranger.id != listing.advertiser_id


def test_owner_admin_and_manager_read_organization_enquiries(password) -> None:
    suffix = uuid.uuid4().hex[:8]
    people, agency, listing, enquiry = _organization_enquiry(
        password,
        suffix,
        ("admin", "manager"),
    )
    assert listing.advertising_organization_id is not None
    assert listing.advertiser_id == people["owner"].id
    for role in ("owner", "admin", "manager"):
        client = client_for(people[role])
        permissions = expect(
            client.get(f"{ORGANIZATIONS}{agency['id']}/permissions/"),
            200,
        )
        assert "leads.view" in permissions["permissions"]
        assert "leads.manage" in permissions["permissions"]
        listed = expect(client.get(ENQUIRIES), 200)
        assert [row["id"] for row in listed["results"]] == [str(enquiry.id)]
        detail = expect(_detail(client, enquiry.id), 200)
        assert detail["listing_reference"] == listing.reference
        assert detail["contact_email"] == enquiry.contact_email
        assert "display_name" not in detail


def test_member_and_viewer_cannot_read_organization_enquiries(password) -> None:
    suffix = uuid.uuid4().hex[:8]
    people, agency, _listing, enquiry = _organization_enquiry(
        password,
        suffix,
        ("member", "viewer"),
    )
    member = people["member"]
    member_client = client_for(member)
    record = active_property(member_client, name=f"Member home {suffix}")
    mandate = self_mandate(member_client, member, record["id"])
    created = draft_listing(
        member_client,
        property_id=record["id"],
        mandate_id=mandate["id"],
        title=f"Member title {suffix}",
    )
    own = _store(
        Listing.objects.get(pk=created["id"]),
        contact_email=f"member-own-{suffix}@example.com",
    )
    permissions = expect(member_client.get(f"{ORGANIZATIONS}{agency['id']}/permissions/"), 200)
    assert permissions["permissions"] == ["organization.view"]
    listed = expect(member_client.get(ENQUIRIES), 200)
    assert [row["id"] for row in listed["results"]] == [str(own.id)]
    denied = _detail(member_client, enquiry.id)
    assert denied.status_code == 403
    assert error_detail(denied) == PERMISSION_DENIED
    assert enquiry.contact_email not in denied.content.decode()
    viewer = client_for(people["viewer"])
    viewer_permissions = expect(
        viewer.get(f"{ORGANIZATIONS}{agency['id']}/permissions/"),
        200,
    )
    assert "leads.view" not in viewer_permissions["permissions"]
    assert "leads.manage" not in viewer_permissions["permissions"]
    assert expect(viewer.get(ENQUIRIES), 200)["count"] == 0
    viewer_denied = _detail(viewer, enquiry.id)
    assert viewer_denied.status_code == 403
    assert error_detail(viewer_denied) == PERMISSION_DENIED


def test_cross_organization_isolation(password) -> None:
    suffix = uuid.uuid4().hex[:8]
    first_people, _first_agency, _first_listing, first = _organization_enquiry(
        password,
        f"a-{suffix}",
    )
    second_people, _second_agency, _second_listing, second = _organization_enquiry(
        password,
        f"b-{suffix}",
    )
    listed = expect(client_for(first_people["owner"]).get(ENQUIRIES), 200)
    assert [row["id"] for row in listed["results"]] == [str(first.id)]
    encoded = client_for(first_people["owner"]).get(ENQUIRIES).content.decode()
    assert second.contact_email not in encoded
    hidden = _detail(client_for(first_people["owner"]), second.id)
    assert hidden.status_code == 404
    assert error_detail(hidden) == NOT_FOUND
    assert second.message not in hidden.content.decode()
    other = expect(client_for(second_people["owner"]).get(ENQUIRIES), 200)
    assert [row["id"] for row in other["results"]] == [str(second.id)]


def test_unauthorized_detail_lookup(password) -> None:
    suffix = uuid.uuid4().hex[:8]
    owner, client, listing = _personal(password, suffix)
    enquiry = _store(
        listing,
        message=f"Hidden {suffix}",
        contact_email=f"hidden-{suffix}@example.com",
    )
    missing = _detail(client, uuid.uuid4())
    assert missing.status_code == 404
    assert error_detail(missing) == NOT_FOUND
    assert client.get(f"{ENQUIRIES}not-a-uuid/").status_code == 404
    stranger = verified_user(f"stranger-{suffix}@example.com", password)
    denied = _detail(client_for(stranger), enquiry.id)
    assert denied.status_code == 404
    assert enquiry.contact_email not in denied.content.decode()
    staff = staff_user(f"staff-{suffix}@example.com", password)
    staff_denied = _detail(client_for(staff), enquiry.id)
    assert staff_denied.status_code == 404
    assert expect(client_for(staff).get(ENQUIRIES), 200)["count"] == 0
    assert owner.id == listing.advertiser_id


def test_pagination_and_deterministic_order(password) -> None:
    suffix = uuid.uuid4().hex[:8]
    owner, client, listing = _personal(password, suffix)
    stamp = timezone.now() - timedelta(days=1)
    created = [
        _store(listing, contact_email=f"seeker-{suffix}-{index}@example.com") for index in range(3)
    ]
    Enquiry.objects.filter(pk__in=[row.pk for row in created]).update(created_at=stamp)
    _other, _other_client, other_listing = _personal(password, f"page-{suffix}")
    _store(other_listing, contact_email=f"foreign-{suffix}@example.com", message="Foreign page")
    expected = [
        str(item)
        for item in Enquiry.objects.filter(advertiser=owner, organization__isnull=True)
        .order_by("-created_at", "-id")
        .values_list("id", flat=True)
    ]
    first = expect(client.get(ENQUIRIES, {"page_size": 2}), 200)
    second = expect(client.get(ENQUIRIES, {"page_size": 2, "page": 2}), 200)
    assert first["count"] == 3
    assert [row["id"] for row in first["results"]] == expected[:2]
    assert [row["id"] for row in second["results"]] == expected[2:]
    assert first["next"]
    assert first["previous"] is None
    assert second["previous"]
    assert second["next"] is None
    assert "Foreign page" not in client.get(ENQUIRIES, {"page_size": 2}).content.decode()


def test_status_listing_and_created_filters(password) -> None:
    suffix = uuid.uuid4().hex[:8]
    _owner, client, listing = _personal(password, suffix)
    older = timezone.now() - timedelta(days=3)
    newer = timezone.now() - timedelta(hours=2)
    first = _store(listing, contact_email=f"old-{suffix}@example.com", message="Older note")
    second = _store(listing, contact_email=f"new-{suffix}@example.com", message="Newer note")
    Enquiry.objects.filter(pk=first.pk).update(created_at=older, status=EnquiryStatus.CONTACTED)
    Enquiry.objects.filter(pk=second.pk).update(created_at=newer, status=EnquiryStatus.NEW)
    _stranger, _stranger_client, other_listing = _personal(password, f"filter-{suffix}")
    foreign = _store(
        other_listing,
        contact_email=f"foreign-{suffix}@example.com",
        message="Foreign filter",
    )
    by_status = expect(client.get(ENQUIRIES, {"status": EnquiryStatus.CONTACTED}), 200)
    assert [row["id"] for row in by_status["results"]] == [str(first.id)]
    by_listing = expect(client.get(ENQUIRIES, {"listing_reference": listing.reference}), 200)
    assert {row["id"] for row in by_listing["results"]} == {str(first.id), str(second.id)}
    crossed = expect(client.get(ENQUIRIES, {"listing_reference": other_listing.reference}), 200)
    assert crossed["count"] == 0
    assert foreign.contact_email not in client.get(ENQUIRIES).content.decode()
    midpoint = older + timedelta(days=1)
    after = expect(client.get(ENQUIRIES, {"created_after": midpoint.isoformat()}), 200)
    assert [row["id"] for row in after["results"]] == [str(second.id)]
    before = expect(client.get(ENQUIRIES, {"created_before": midpoint.isoformat()}), 200)
    assert [row["id"] for row in before["results"]] == [str(first.id)]
    invalid_range = client.get(
        ENQUIRIES,
        {"created_after": newer.isoformat(), "created_before": older.isoformat()},
    )
    assert invalid_range.status_code == 400
    assert client.get(ENQUIRIES, {"status": "missing"}).status_code == 400
    dated = expect(client.get(ENQUIRIES, {"created_after": "2020-01-01"}), 200)
    assert dated["count"] == 2
    assert client.get(ENQUIRIES, {"listing_reference": "not a reference"}).status_code == 400
    assert client.get(ENQUIRIES, {"created_after": "yesterday"}).status_code == 400
    claimed = client.get(
        ENQUIRIES,
        {
            "organization_id": str(uuid.uuid4()),
            "advertiser_id": str(uuid.uuid4()),
            "permission": "leads.view",
            "assigned_to": str(uuid.uuid4()),
        },
    )
    assert claimed.status_code == 400
    assert error_detail(claimed) == "This filter is not valid."
    assert foreign.message not in claimed.content.decode()


def test_empty_inbox(password) -> None:
    user = verified_user(f"empty-{uuid.uuid4().hex[:8]}@example.com", password)
    body = expect(client_for(user).get(ENQUIRIES), 200)
    assert body["count"] == 0
    assert body["results"] == []
    assert body["next"] is None
    assert body["previous"] is None


def test_authentication_and_account_requirements(password, api_client) -> None:
    suffix = uuid.uuid4().hex[:8]
    owner, client, listing = _personal(password, suffix)
    enquiry = _store(listing, contact_email=f"auth-{suffix}@example.com", message="Auth note")
    anonymous = api_client.get(ENQUIRIES)
    assert anonymous.status_code == 401
    anonymous_detail = api_client.get(f"{ENQUIRIES}{enquiry.id}/")
    assert anonymous_detail.status_code == 401
    assert enquiry.contact_email not in anonymous_detail.content.decode()
    assert "Auth note" not in anonymous.content.decode()
    owner.email_verified_at = None
    owner.save(update_fields=["email_verified_at", "updated_at"])
    unverified = client.get(ENQUIRIES)
    assert unverified.status_code == 403
    assert error_detail(unverified) == "Verify your email before continuing."
    owner.email_verified_at = timezone.now()
    owner.status = AccountStatus.SUSPENDED
    owner.save(update_fields=["email_verified_at", "status", "updated_at"])
    blocked = _detail(client, enquiry.id)
    assert blocked.status_code == 403
    assert error_detail(blocked) == "This account cannot sign in."
    assert enquiry.message not in blocked.content.decode()


def test_assignment_does_not_grant_access(password) -> None:
    suffix = uuid.uuid4().hex[:8]
    people, _agency, _listing, enquiry = _organization_enquiry(password, suffix, ("member",))
    member = people["member"]
    stranger = verified_user(f"assignee-{suffix}@example.com", password)
    enquiry.assigned_to = member
    enquiry.save(update_fields=["assigned_to", "updated_at"])
    member_denied = _detail(client_for(member), enquiry.id)
    assert member_denied.status_code == 403
    assert error_detail(member_denied) == PERMISSION_DENIED
    assert expect(client_for(member).get(ENQUIRIES), 200)["count"] == 0
    enquiry.assigned_to = stranger
    enquiry.save(update_fields=["assigned_to", "updated_at"])
    stranger_denied = _detail(client_for(stranger), enquiry.id)
    assert stranger_denied.status_code == 404
    assert expect(client_for(stranger).get(ENQUIRIES), 200)["count"] == 0
    visible = expect(_detail(client_for(people["owner"]), enquiry.id), 200)
    assert visible["assignment"] == {"user_id": str(stranger.id)}
    assert stranger.email not in client_for(people["owner"]).get(ENQUIRIES).content.decode()
    assert member.email not in client_for(people["owner"]).get(ENQUIRIES).content.decode()


def test_membership_is_checked_on_every_request(password) -> None:
    suffix = uuid.uuid4().hex[:8]
    people, agency, _listing, enquiry = _organization_enquiry(
        password,
        suffix,
        ("admin", "manager"),
    )
    admin = people["admin"]
    manager = people["manager"]
    owner = people["owner"]
    assert expect(_detail(client_for(admin), enquiry.id), 200)["id"] == str(enquiry.id)
    membership = OrganizationMembership.objects.get(organization_id=agency["id"], user=admin)
    membership.role = "member"
    membership.save(update_fields=["role", "updated_at"])
    demoted = _detail(client_for(admin), enquiry.id)
    assert demoted.status_code == 403
    assert error_detail(demoted) == PERMISSION_DENIED
    assert expect(client_for(admin).get(ENQUIRIES), 200)["count"] == 0
    membership.role = "admin"
    membership.save(update_fields=["role", "updated_at"])
    assert expect(_detail(client_for(admin), enquiry.id), 200)["id"] == str(enquiry.id)
    manager_membership = OrganizationMembership.objects.get(
        organization_id=agency["id"],
        user=manager,
    )
    manager_membership.status = MembershipStatus.SUSPENDED
    manager_membership.save(update_fields=["status", "updated_at"])
    suspended = _detail(client_for(manager), enquiry.id)
    assert suspended.status_code == 403
    assert error_detail(suspended) == MEMBERSHIP_NOT_ACTIVE
    assert enquiry.contact_email not in client_for(manager).get(ENQUIRIES).content.decode()
    manager_membership.status = MembershipStatus.ACTIVE
    manager_membership.save(update_fields=["status", "updated_at"])
    assert expect(_detail(client_for(manager), enquiry.id), 200)["id"] == str(enquiry.id)
    Organization.objects.filter(pk=agency["id"]).update(status=OrganizationStatus.DEACTIVATED)
    blocked = _detail(client_for(owner), enquiry.id)
    assert blocked.status_code == 403
    assert error_detail(blocked) == ORG_NOT_ACTIVE
    assert expect(client_for(owner).get(ENQUIRIES), 200)["count"] == 0
