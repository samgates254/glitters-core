"""Pure organization rules and database constraints."""

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone
from tests.organizations_support import verified_user

from apps.organizations.domain import (
    FUTURE_PERMISSIONS,
    ROLE_PERMISSIONS,
    EventAction,
    OrganizationStatus,
    Permission,
    Role,
    cache_key,
    member_transition_allowed,
    normalize_slug,
    permission_names,
    permissions_for,
    rank_exceeds,
    reserved_permissions,
    transition_allowed,
)
from apps.organizations.models import (
    Organization,
    OrganizationEvent,
    OrganizationInvitation,
    OrganizationMembership,
)
from apps.organizations.services.audit import clean_metadata


def test_role_matrix_and_reserved_permissions() -> None:
    owner = permissions_for(Role.OWNER)
    admin = permissions_for(Role.ADMIN)
    assert Permission.ORGANIZATION_TRANSFER_OWNERSHIP in owner
    assert Permission.ORGANIZATION_MANAGE_STATUS in owner
    assert Permission.ORGANIZATION_MANAGE_BILLING in owner
    assert Permission.ORGANIZATION_TRANSFER_OWNERSHIP not in admin
    assert Permission.ORGANIZATION_MANAGE_STATUS not in admin
    assert Permission.ORGANIZATION_MANAGE_BILLING in admin
    assert Permission.ORGANIZATION_REMOVE_MEMBERS not in permissions_for(Role.MANAGER)
    assert Permission.ORGANIZATION_INVITE_MEMBERS in permissions_for(Role.MANAGER)
    assert permissions_for(Role.MEMBER) == frozenset({Permission.ORGANIZATION_VIEW})
    assert permissions_for(Role.VIEWER) == permissions_for(Role.MEMBER)
    assert permissions_for("missing") == frozenset()
    assert permission_names(Role.VIEWER) == ["organization.view"]
    for role in (Role.OWNER, Role.ADMIN, Role.MANAGER):
        assert Permission.PROPERTY_CREATE in permissions_for(role)
        assert Permission.PROPERTY_UPDATE in permissions_for(role)
        assert Permission.PROPERTY_ARCHIVE in permissions_for(role)
        assert Permission.PROPERTY_PUBLISH in permissions_for(role)
        assert Permission.LEADS_VIEW in permissions_for(role)
        assert Permission.LEADS_MANAGE in permissions_for(role)
    assert Permission.PROPERTY_PUBLISH not in permissions_for(Role.MEMBER)
    assert Permission.PROPERTY_PUBLISH not in permissions_for(Role.VIEWER)
    assert Permission.LEADS_VIEW not in permissions_for(Role.MEMBER)
    assert Permission.LEADS_MANAGE not in permissions_for(Role.MEMBER)
    assert Permission.LEADS_VIEW not in permissions_for(Role.VIEWER)
    assert Permission.LEADS_MANAGE not in permissions_for(Role.VIEWER)
    for role in (Role.OWNER, Role.ADMIN, Role.MANAGER):
        assert Permission.LEASES_VIEW in permissions_for(role)
        assert Permission.LEASES_PREPARE in permissions_for(role)
        assert Permission.LEASES_APPROVE in permissions_for(role)
        assert Permission.TENANTS_VET in permissions_for(role)
    for role in (Role.OWNER, Role.ADMIN):
        assert Permission.LEASES_EXECUTE in permissions_for(role)
        assert Permission.LEASES_TERMINATE in permissions_for(role)
    assert Permission.LEASES_EXECUTE not in permissions_for(Role.MANAGER)
    assert Permission.LEASES_TERMINATE not in permissions_for(Role.MANAGER)
    assert Permission.LEASES_EXECUTE not in permissions_for(Role.MEMBER)
    assert Permission.TENANTS_VET not in permissions_for(Role.MEMBER)
    assert Permission.LEASES_VIEW not in permissions_for(Role.MEMBER)
    assert Permission.LEASES_PREPARE not in permissions_for(Role.MEMBER)
    assert Permission.LEASES_APPROVE not in permissions_for(Role.VIEWER)
    assert Permission.LEASES_MANAGE not in permissions_for(Role.OWNER)
    assert Permission.PROPERTY_CREATE not in permissions_for(Role.MEMBER)
    assert Permission.PROPERTY_ARCHIVE not in permissions_for(Role.VIEWER)
    granted = set().union(*ROLE_PERMISSIONS.values())
    assert granted.isdisjoint(FUTURE_PERMISSIONS)
    assert "property.create" not in reserved_permissions()
    assert "property.publish" not in reserved_permissions()
    assert "leads.view" not in reserved_permissions()
    assert "leads.manage" not in reserved_permissions()
    assert "leases.manage" in reserved_permissions()
    assert "leases.view" not in reserved_permissions()
    assert "leases.execute" not in reserved_permissions()
    assert "leases.terminate" not in reserved_permissions()
    assert "tenants.vet" not in reserved_permissions()
    assert "listing.manage" in reserved_permissions()
    assert "service_requests.manage" in reserved_permissions()
    assert "organization.view" not in reserved_permissions()


def test_status_transitions_and_slug_normalization() -> None:
    assert transition_allowed(OrganizationStatus.PENDING_VERIFICATION, OrganizationStatus.ACTIVE)
    assert transition_allowed(OrganizationStatus.PENDING_VERIFICATION, OrganizationStatus.REJECTED)
    assert not transition_allowed(OrganizationStatus.REJECTED, OrganizationStatus.ACTIVE)
    assert member_transition_allowed(OrganizationStatus.ACTIVE, OrganizationStatus.DEACTIVATED)
    assert not member_transition_allowed(OrganizationStatus.ACTIVE, OrganizationStatus.SUSPENDED)
    assert not member_transition_allowed(OrganizationStatus.SUSPENDED, OrganizationStatus.ACTIVE)
    assert normalize_slug("  Acme Properties!! ") == "acme-properties"
    assert normalize_slug("---") == ""
    assert rank_exceeds("owner", "missing") is False
    assert cache_key("abc", "members") == "org:abc:members"
    with pytest.raises(ValueError):
        cache_key("abc", " ")


def test_metadata_drops_secrets() -> None:
    cleaned = clean_metadata({"email": "a@example.com", "token": "raw", "Password": "no"})
    assert cleaned == {"email": "a@example.com"}


@pytest.mark.django_db
def test_database_invariants(password) -> None:
    owner = verified_user("owner@example.com", password)
    other = verified_user("other@example.com", password)
    organization = Organization.objects.create(
        legal_name="Acme Properties Limited",
        display_name="Acme",
        slug="acme",
        organization_type="agency",
        country="KE",
    )
    OrganizationMembership.objects.create(
        organization=organization,
        user=owner,
        role=Role.OWNER,
        status="active",
        source="creator",
        joined_at=timezone.now(),
    )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            OrganizationMembership.objects.create(
                organization=organization,
                user=other,
                role=Role.OWNER,
                status="active",
                source="invitation",
                joined_at=timezone.now(),
            )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            OrganizationMembership.objects.create(
                organization=organization,
                user=owner,
                role=Role.ADMIN,
                status="active",
                source="invitation",
                joined_at=timezone.now(),
            )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            OrganizationMembership.objects.create(
                organization=organization,
                user=other,
                role=Role.MEMBER,
                status="active",
                source="invitation",
            )
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            OrganizationInvitation.objects.create(
                organization=organization,
                email="guest@example.com",
                role=Role.OWNER,
                token_hash="a" * 64,
                expires_at=timezone.now(),
            )
    event = OrganizationEvent.objects.create(
        actor=owner,
        organization=organization,
        action=EventAction.ORGANIZATION_CREATED,
        object_type="organization",
        object_id=str(organization.id),
    )
    with pytest.raises(ValueError):
        event.save()
    with pytest.raises(ValueError):
        event.delete()
    with pytest.raises(ValueError):
        OrganizationEvent.objects.filter(pk=event.pk).update(
            action=EventAction.ORGANIZATION_UPDATED,
        )
