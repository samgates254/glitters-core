"""Who may advertise. A claim or a membership is not a mandate."""

from datetime import timedelta

import pytest
from django.utils import timezone
from tests.identity_support import error_detail
from tests.marketplace_support import (
    LISTINGS,
    MANDATES,
    MODERATION,
    PUBLISHER,
    active_property,
    add_document,
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
from tests.properties_support import PROPERTIES

from apps.identity.services.secrets import hash_secret
from apps.marketplace.models import Listing, ListingMandate
from apps.organizations.models import OrganizationInvitation, OrganizationMembership

pytestmark = pytest.mark.django_db


def _org(client, **overrides):
    body = organization_body(**overrides)
    return expect(client.post(ORGANIZATIONS, body, format="json"), 201)


def _invite(client, organization_id, email, role="manager"):
    return client.post(
        f"{ORGANIZATIONS}{organization_id}/invitations/",
        {"email": email, "role": role},
        format="json",
    )


def test_personal_self_declaration_is_not_a_title(password) -> None:
    owner = verified_user("ada@example.com", password)
    client = client_for(owner)
    record = active_property(client, name="Personal home")
    mandate = self_mandate(client, owner, record["id"])
    assert mandate["status"] == "active"
    assert mandate["self_declared"] is True
    assert mandate["authority"] == "self_declaration"
    assert "has not verified legal title" in mandate["authority_notice"]
    assert mandate["evidence_reference"] == "Owner letter 2026"
    listing = draft_listing(client, property_id=record["id"], mandate_id=mandate["id"])
    assert listing["advertising_organization_id"] is None
    assert listing["advertiser_id"] == str(owner.id)


def test_agency_representation_acceptance_and_rejection(password, locmem_email) -> None:
    owner = verified_user("owner@example.com", password)
    agent = verified_user("agent@example.com", password)
    viewer = verified_user("viewer@example.com", password)
    outsider = verified_user("outsider@example.com", password)
    owner_client = client_for(owner)
    record = active_property(owner_client, name="Agency brief")
    agency = _org(owner_client, display_name="North Agency", legal_name="North Agency Ltd")
    assert _invite(owner_client, agency["id"], agent.email, "manager").status_code == 201
    token = _token()
    assert client_for(agent).post(ACCEPT, {"token": token}, format="json").status_code == 200
    assert _invite(owner_client, agency["id"], viewer.email, "viewer").status_code == 201
    assert client_for(viewer).post(ACCEPT, {"token": _token()}, format="json").status_code == 200
    pending = expect(
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
    assert pending["status"] == "pending_acceptance"
    assert pending["authority"] == "representation"
    assert "not proof of legal ownership" in pending["authority_notice"]
    viewer_accept = client_for(viewer).post(f"{MANDATES}{pending['id']}/accept/")
    assert viewer_accept.status_code == 403
    stranger = client_for(outsider).get(f"{MANDATES}{pending['id']}/")
    assert stranger.status_code == 404
    rejected = expect(client_for(agent).post(f"{MANDATES}{pending['id']}/reject/"), 200)
    assert rejected["status"] == "rejected"
    again = expect(
        owner_client.post(
            MANDATES,
            {
                "property_id": record["id"],
                "listing_scope": "any",
                "representative_organization_id": agency["id"],
                "principal": "self",
            },
            format="json",
        ),
        201,
    )
    accepted = expect(client_for(agent).post(f"{MANDATES}{again['id']}/accept/"), 200)
    assert accepted["status"] == "active"
    assert accepted["self_declared"] is False
    photo = add_photo(client_for(agent), record["id"])
    document = client_for(agent).post(
        f"/api/v1/marketplace/properties/{record['id']}/media/upload-sessions/",
        {"kind": "document", "mime_type": "application/pdf", "size_bytes": 64},
        format="json",
    )
    assert document.status_code == 403
    listing = draft_listing(
        client_for(agent),
        property_id=record["id"],
        mandate_id=again["id"],
        title="Agency instruction",
    )
    assert listing["advertising_organization_id"] == agency["id"]
    attach(client_for(agent), listing["id"], photo["id"])
    media_rows = client_for(agent).get(f"/api/v1/marketplace/properties/{record['id']}/media/")
    assert media_rows.status_code == 200
    assert all(row["kind"] == "photograph" for row in media_rows.json()["results"])
    owner_document = add_document(owner_client, record["id"])
    agent_rows = client_for(agent).get(
        f"/api/v1/marketplace/properties/{record['id']}/media/",
    )
    assert agent_rows.status_code == 200
    visible = {row["id"] for row in agent_rows.json()["results"]}
    assert owner_document["id"] not in visible


def test_mandate_scope_property_expiry_and_revocation(password) -> None:
    owner = verified_user("ada@example.com", password)
    staff = staff_user("staff@example.com", password)
    client = client_for(owner)
    first = active_property(client, name="Mandate one")
    second = active_property(client, name="Mandate two")
    sale = self_mandate(client, owner, first["id"], "sale")
    rental = self_mandate(client, owner, first["id"], "long_term_rental")
    mismatched = client.post(
        PUBLISHER,
        {
            "property_id": second["id"],
            "mandate_id": sale["id"],
            "listing_type": "sale",
            "title": "Wrong home",
            "description": "This mandate is for another property.",
            "price": {
                "amount": 100,
                "currency": "KES",
                "presentation": "asking",
                "negotiable": False,
                "billing_period": "sale",
            },
        },
        format="json",
    )
    assert mismatched.status_code == 400
    assert error_detail(mismatched) == "This authorization does not cover the listing."
    scoped = client.post(
        PUBLISHER,
        {
            "property_id": first["id"],
            "mandate_id": rental["id"],
            "listing_type": "sale",
            "title": "Wrong scope",
            "description": "The mandate is for a rental.",
            "price": {
                "amount": 100,
                "currency": "KES",
                "presentation": "asking",
                "negotiable": False,
                "billing_period": "sale",
            },
        },
        format="json",
    )
    assert scoped.status_code == 400
    assert error_detail(scoped) == "This authorization does not cover this listing type."
    published = publish_personal(
        owner,
        client,
        client_for(staff),
        name="Revoke me",
        title="Live offer",
    )
    mandate = ListingMandate.objects.get(pk=published["mandate"]["id"])
    mandate.valid_from = timezone.localdate() - timedelta(days=2)
    mandate.valid_until = timezone.localdate() - timedelta(days=1)
    mandate.save(update_fields=["valid_from", "valid_until", "updated_at"])
    expired = client.post(f"{PUBLISHER}{published['listing']['id']}/pause/")
    assert expired.status_code == 409
    mandate.refresh_from_db()
    assert mandate.status == "expired"
    assert Listing.objects.get(pk=published["listing"]["id"]).status == "withdrawn"
    live = publish_personal(owner, client, client_for(staff), name="Still live", title="Still live")
    revoked = expect(client.post(f"{MANDATES}{live['mandate']['id']}/revoke/"), 200)
    assert revoked["status"] == "revoked"
    listing = Listing.objects.get(pk=live["listing"]["id"])
    assert listing.status == "withdrawn"
    hidden = client.get(f"{LISTINGS}{listing.reference}/")
    assert hidden.status_code == 404
    draft_row = draft_listing(
        client,
        property_id=second["id"],
        mandate_id=self_mandate(client, owner, second["id"])["id"],
        title="Stays a draft",
    )
    expect(client.post(f"{MANDATES}{draft_row['mandate_id']}/revoke/"), 200)
    assert Listing.objects.get(pk=draft_row["id"]).status == "draft"


def test_organization_publisher_permission_and_inactive_participants(
    password,
    locmem_email,
) -> None:
    owner = verified_user("owner@example.com", password)
    manager = verified_user("manager@example.com", password)
    member = verified_user("member@example.com", password)
    staff = staff_user("staff@example.com", password)
    owner_client = client_for(owner)
    agency = _org(owner_client, display_name="Other Agency", legal_name="Other Agency Ltd")
    assert _invite(owner_client, agency["id"], manager.email, "manager").status_code == 201
    assert client_for(manager).post(ACCEPT, {"token": _token()}, format="json").status_code == 200
    assert _invite(owner_client, agency["id"], member.email, "member").status_code == 201
    assert client_for(member).post(ACCEPT, {"token": _token()}, format="json").status_code == 200
    record = expect(
        owner_client.post(
            PROPERTIES,
            {
                "management_context": "organization",
                "organization_id": agency["id"],
                "property_type": "residential",
                "property_subtype": "apartment",
                "name": "Managed flat",
                "hierarchy_role": "standalone",
            },
            format="json",
        ),
        201,
    )
    from tests.properties_support import address_for, kenya_chain

    _county, _sub, ward = kenya_chain(name="OrgWard")
    expect(
        owner_client.patch(
            f"{PROPERTIES}{record['id']}/",
            {"status": "active", "address": address_for(ward)},
            format="json",
        ),
        200,
    )
    refused = client_for(member).post(
        MANDATES,
        {
            "property_id": record["id"],
            "listing_scope": "sale",
            "representative_organization_id": agency["id"],
            "principal": "organization",
        },
        format="json",
    )
    assert refused.status_code == 403
    mandate = expect(
        client_for(manager).post(
            MANDATES,
            {
                "property_id": record["id"],
                "listing_scope": "sale",
                "representative_organization_id": agency["id"],
                "principal": "organization",
            },
            format="json",
        ),
        201,
    )
    assert mandate["self_declared"] is True
    assert mandate["status"] == "active"
    photo = add_photo(client_for(manager), record["id"])
    listing = draft_listing(
        client_for(manager),
        property_id=record["id"],
        mandate_id=mandate["id"],
        title="Organization offer",
        description="Managed by the agency.",
    )
    attach(client_for(manager), listing["id"], photo["id"])
    member_edit = client_for(member).patch(
        f"{PUBLISHER}{listing['id']}/",
        {"title": "Taken"},
        format="json",
    )
    assert member_edit.status_code == 403
    expect(client_for(manager).post(f"{PUBLISHER}{listing['id']}/submit/"), 200)
    approved = expect(
        client_for(staff).post(f"{MODERATION}{listing['id']}/approve/"),
        200,
    )
    assert approved["advertiser"]["organization_name"] == "Other Agency"
    public = expect(client_for(owner).get(LISTINGS), 200)
    assert listing["reference"] in {row["reference"] for row in public["results"]}
    membership = OrganizationMembership.objects.get(organization_id=agency["id"], user=manager)
    suspended = owner_client.post(
        f"{ORGANIZATIONS}{agency['id']}/members/{membership.id}/suspend/",
    )
    assert suspended.status_code == 200
    hidden = client_for(owner).get(LISTINGS)
    assert listing["reference"] not in {row["reference"] for row in hidden.json()["results"]}
    resume = client_for(owner).post(f"{PUBLISHER}{listing['id']}/resume/")
    assert resume.status_code == 403
    assert error_detail(resume) == "This membership is not active."
    deactivated = owner_client.post(
        f"{ORGANIZATIONS}{agency['id']}/status/",
        {"status": "deactivated"},
        format="json",
    )
    assert deactivated.status_code == 200
    blocked = owner_client.get(f"{PUBLISHER}{listing['id']}/")
    assert blocked.status_code == 403
    assert error_detail(blocked) == "This organization is not active."


def test_unverified_email_cannot_advertise(password) -> None:
    owner = verified_user("ada@example.com", password)
    owner.email_verified_at = None
    owner.save(update_fields=["email_verified_at", "updated_at"])
    response = client_for(owner).post(
        MANDATES,
        {
            "property_id": "00000000-0000-0000-0000-000000000000",
            "listing_scope": "sale",
            "representative_user_id": str(owner.id),
        },
        format="json",
    )
    assert response.status_code == 403
    assert error_detail(response) == "Verify your email before continuing."


def test_future_mandate_cannot_publish_yet(password) -> None:
    owner = verified_user("ada@example.com", password)
    client = client_for(owner)
    record = active_property(client, name="Future brief")
    tomorrow = timezone.localdate() + timedelta(days=2)
    mandate = expect(
        client.post(
            MANDATES,
            {
                "property_id": record["id"],
                "listing_scope": "sale",
                "representative_user_id": str(owner.id),
                "principal": "self",
                "valid_from": tomorrow.isoformat(),
            },
            format="json",
        ),
        201,
    )
    assert mandate["status"] == "active"
    photo = add_photo(client, record["id"])
    listing = draft_listing(client, property_id=record["id"], mandate_id=mandate["id"])
    attach(client, listing["id"], photo["id"])
    refused = client.post(f"{PUBLISHER}{listing['id']}/submit/")
    assert refused.status_code == 409
    assert error_detail(refused) == "This authorization does not cover the listing."


def _token() -> str:
    from django.core import mail

    for line in mail.outbox[-1].body.splitlines():
        if line.startswith("token:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError("missing token")


def test_revoked_invitation_token_is_not_reused_as_a_mandate_actor(password, locmem_email) -> None:
    """A removed member cannot keep using an old invitation as if it were access."""

    owner = verified_user("owner@example.com", password)
    guest = verified_user("guest@example.com", password)
    owner_client = client_for(owner)
    agency = _org(owner_client)
    assert _invite(owner_client, agency["id"], guest.email, "manager").status_code == 201
    stored = OrganizationInvitation.objects.get(email=guest.email)
    assert stored.token_hash == hash_secret(_token())
    assert client_for(guest).post(ACCEPT, {"token": _token()}, format="json").status_code == 200
    membership = OrganizationMembership.objects.get(user=guest)
    removed = owner_client.post(f"{ORGANIZATIONS}{agency['id']}/members/{membership.id}/remove/")
    assert removed.status_code == 200
    replay = client_for(guest).post(ACCEPT, {"token": _token()}, format="json")
    assert replay.status_code == 400
    assert OrganizationMembership.objects.get(user=guest).status == "removed"
