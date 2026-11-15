"""Queryset scope for enquiry reads. Filters cannot widen it."""

from __future__ import annotations

import uuid

import pytest
from django.utils import timezone
from tests.marketplace_support import (
    MANDATES,
    active_property,
    draft_listing,
    expect,
    self_mandate,
)
from tests.organizations_support import ORGANIZATIONS, client_for, organization_body, verified_user

from apps.marketplace.filters import EnquiryFilters
from apps.marketplace.models import Enquiry, Listing
from apps.marketplace.services.access import lead_manager_roles, lead_reader_roles
from apps.marketplace.services.enquiries import authorized_enquiries, list_enquiries
from apps.organizations.domain import (
    MembershipSource,
    MembershipStatus,
    Permission,
    Role,
    permissions_for,
)
from apps.organizations.models import OrganizationMembership

pytestmark = pytest.mark.django_db


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
    owner = verified_user(f"scope-{suffix}@example.com", password)
    client = client_for(owner)
    record = active_property(client, name=f"Scope {suffix}")
    mandate = self_mandate(client, owner, record["id"])
    created = draft_listing(
        client,
        property_id=record["id"],
        mandate_id=mandate["id"],
        title=f"Scope title {suffix}",
    )
    return owner, Listing.objects.get(pk=created["id"])


def _organization_listing(password: str, suffix: str, reader_role: str):
    owner = verified_user(f"scope-owner-{suffix}@example.com", password)
    reader = verified_user(f"scope-{reader_role}-{suffix}@example.com", password)
    client = client_for(owner)
    agency = expect(
        client.post(
            ORGANIZATIONS,
            organization_body(
                display_name=f"Scope {suffix}",
                legal_name=f"Scope {suffix} Limited",
            ),
            format="json",
        ),
        201,
    )
    OrganizationMembership.objects.create(
        organization_id=agency["id"],
        user=reader,
        role=reader_role,
        status=MembershipStatus.ACTIVE,
        source=MembershipSource.INVITATION,
        joined_at=timezone.now(),
    )
    record = active_property(client, name=f"Scope home {suffix}")
    mandate = expect(
        client.post(
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
        mandate = expect(client.post(f"{MANDATES}{mandate['id']}/accept/"), 200)
    created = draft_listing(
        client,
        property_id=record["id"],
        mandate_id=mandate["id"],
        title=f"Scope agency {suffix}",
    )
    listing = Listing.objects.get(pk=created["id"])
    return owner, reader, agency, listing


def test_authorized_queryset_rejects_uuid_and_reference_changes(password) -> None:
    suffix = uuid.uuid4().hex[:8]
    owner, listing = _personal(password, suffix)
    own = _store(listing, contact_email=f"own-{suffix}@example.com")
    stranger, other_listing = _personal(password, f"stranger-{suffix}")
    foreign = _store(other_listing, contact_email=f"foreign-{suffix}@example.com")
    scoped = authorized_enquiries(owner)
    assert set(scoped.values_list("id", flat=True)) == {own.id}
    assert not scoped.filter(pk=foreign.id).exists()
    assert not scoped.filter(listing__reference=other_listing.reference).exists()
    assert not scoped.filter(advertiser_id=stranger.id).exists()
    narrowed = list_enquiries(
        owner,
        EnquiryFilters(listing_reference=other_listing.reference),
    )
    assert not narrowed.exists()
    where = str(scoped.query).split("WHERE", 1)[1]
    assert "assigned_to" not in where
    assert "organization_membership" in str(scoped.query)


def test_assignment_and_advertiser_id_do_not_widen_organization_scope(password) -> None:
    suffix = uuid.uuid4().hex[:8]
    _owner, reader, agency, listing = _organization_listing(password, suffix, Role.MEMBER)
    enquiry = _store(listing, contact_email=f"org-{suffix}@example.com")
    enquiry.assigned_to = reader
    enquiry.save(update_fields=["assigned_to", "updated_at"])
    scoped = authorized_enquiries(reader)
    assert not scoped.filter(pk=enquiry.id).exists()
    assert not scoped.filter(organization_id=agency["id"]).exists()
    assert Permission.PROPERTY_PUBLISH not in permissions_for(Role.MEMBER)
    assert Role.MEMBER not in lead_reader_roles()
    # The recorded advertiser still needs the lead permission when the row
    # belongs to an organization. Personal matching is not a second path.
    advertiser = listing.advertiser
    OrganizationMembership.objects.filter(organization_id=agency["id"], user=advertiser).update(
        role=Role.MEMBER
    )
    assert not authorized_enquiries(advertiser).filter(pk=enquiry.id).exists()


def test_property_publish_does_not_grant_lead_access(password, monkeypatch) -> None:
    suffix = uuid.uuid4().hex[:8]
    _owner, manager, _agency, listing = _organization_listing(password, suffix, Role.MANAGER)
    enquiry = _store(listing)
    assert authorized_enquiries(manager).filter(pk=enquiry.id).exists()
    from apps.organizations import domain

    patched = {role: frozenset(perms) for role, perms in domain.ROLE_PERMISSIONS.items()}
    patched[Role.MANAGER] = frozenset({Permission.PROPERTY_PUBLISH, Permission.ORGANIZATION_VIEW})
    monkeypatch.setattr(domain, "ROLE_PERMISSIONS", patched)
    assert Permission.PROPERTY_PUBLISH in patched[Role.MANAGER]
    assert Permission.LEADS_VIEW not in patched[Role.MANAGER]
    assert Role.MANAGER not in lead_reader_roles()
    assert not authorized_enquiries(manager).filter(pk=enquiry.id).exists()
    patched[Role.MANAGER] = frozenset({Permission.LEADS_VIEW})
    assert Permission.PROPERTY_PUBLISH not in patched[Role.MANAGER]
    assert authorized_enquiries(manager).filter(pk=enquiry.id).exists()
    patched[Role.MANAGER] = frozenset({Permission.LEADS_MANAGE})
    assert authorized_enquiries(manager).filter(pk=enquiry.id).exists()


def test_lead_manager_roles_derived_from_registry() -> None:
    assert lead_manager_roles() == [Role.ADMIN, Role.MANAGER, Role.OWNER]
    assert Role.MEMBER not in lead_manager_roles()
    assert Role.VIEWER not in lead_manager_roles()
