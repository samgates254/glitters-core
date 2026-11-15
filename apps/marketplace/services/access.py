"""Who may advertise. Organization membership is not a listing mandate."""

from __future__ import annotations

from django.utils import timezone

from apps.identity.domain import AccountStatus
from apps.marketplace.domain import (
    MEMBERSHIP_NOT_ACTIVE,
    NOT_FOUND,
    ORG_NOT_ACTIVE,
    PERMISSION_DENIED,
    VERIFY_EMAIL_FIRST,
    MandateStatus,
)
from apps.marketplace.errors import MarketplaceError
from apps.organizations.domain import (
    MembershipStatus,
    OrganizationStatus,
    Permission,
    permissions_for,
)
from apps.organizations.models import OrganizationMembership
from apps.organizations.services.authorization import ensure_active_account


def ensure_actor(user) -> None:
    ensure_active_account(user)


def ensure_verified_email(user) -> None:
    if user.email_verified_at is None:
        raise MarketplaceError(VERIFY_EMAIL_FIRST, status_code=403)


def ensure_moderator(user) -> None:
    ensure_actor(user)
    if not (user.is_staff or user.is_superuser):
        raise MarketplaceError(PERMISSION_DENIED, status_code=403)
    if not user.has_confirmed_mfa():
        raise MarketplaceError("Multi-factor authentication is required.", status_code=403)


def publish_roles() -> list[str]:
    from apps.organizations.domain import ROLE_PERMISSIONS

    return sorted(
        role
        for role, permissions in ROLE_PERMISSIONS.items()
        if Permission.PROPERTY_PUBLISH in permissions
    )


def lead_reader_roles() -> list[str]:
    """Roles that may read organization enquiries.

    The grant is leads.view or leads.manage on the membership role.
    property.publish is not consulted. The registry is read on each call
    so a role list is never copied beside ROLE_PERMISSIONS.
    """

    from apps.organizations.domain import ROLE_PERMISSIONS

    return sorted(
        role
        for role, permissions in ROLE_PERMISSIONS.items()
        if Permission.LEADS_VIEW in permissions or Permission.LEADS_MANAGE in permissions
    )


def lead_manager_roles() -> list[str]:
    """Roles that may manage organization enquiries.

    The grant is leads.manage on the membership role.
    The registry is read on each call so a role list is never copied beside ROLE_PERMISSIONS.
    """

    from apps.organizations.domain import ROLE_PERMISSIONS

    return sorted(
        role
        for role, permissions in ROLE_PERMISSIONS.items()
        if Permission.LEADS_MANAGE in permissions
    )


def require_lead_manager(user, enquiry) -> OrganizationMembership | None:
    """404 for strangers and unrelated accounts. 403 when related but cannot manage.

    Personal enquiries require the caller to be the recorded advertiser.
    Organization enquiries require active membership in an active organization
    whose role grants leads.manage. Staff receives no implicit bypass.
    """

    ensure_actor(user)
    ensure_verified_email(user)
    if enquiry.organization_id is None:
        if enquiry.advertiser_id != user.pk:
            raise MarketplaceError(NOT_FOUND, status_code=404)
        return None
    membership = (
        OrganizationMembership.objects.select_related("organization")
        .filter(organization_id=enquiry.organization_id, user_id=user.pk)
        .first()
    )
    if membership is None:
        raise MarketplaceError(NOT_FOUND, status_code=404)
    if membership.organization.status != OrganizationStatus.ACTIVE:
        raise MarketplaceError(ORG_NOT_ACTIVE, status_code=403)
    if membership.status != MembershipStatus.ACTIVE:
        raise MarketplaceError(MEMBERSHIP_NOT_ACTIVE, status_code=403)
    if Permission.LEADS_MANAGE not in permissions_for(membership.role):
        raise MarketplaceError(PERMISSION_DENIED, status_code=403)
    return membership


def publisher_membership(user, organization_id):
    if organization_id is None:
        return None
    return (
        OrganizationMembership.objects.select_related("organization", "user")
        .filter(organization_id=organization_id, user=user)
        .first()
    )


def require_listing_manager(user, listing) -> None:
    """404 for strangers. 403 when the caller is related but cannot publish."""

    ensure_actor(user)
    if listing.advertising_organization_id is None:
        if listing.advertiser_id != user.id:
            raise MarketplaceError(NOT_FOUND, status_code=404)
        ensure_verified_email(user)
        return
    membership = publisher_membership(user, listing.advertising_organization_id)
    if membership is None:
        raise MarketplaceError(NOT_FOUND, status_code=404)
    if membership.organization.status != OrganizationStatus.ACTIVE:
        raise MarketplaceError(ORG_NOT_ACTIVE, status_code=403)
    if membership.status != MembershipStatus.ACTIVE:
        raise MarketplaceError(MEMBERSHIP_NOT_ACTIVE, status_code=403)
    if Permission.PROPERTY_PUBLISH not in permissions_for(membership.role):
        raise MarketplaceError(PERMISSION_DENIED, status_code=403)
    ensure_verified_email(user)


def require_representative(user, mandate) -> None:
    ensure_actor(user)
    if mandate.representative_user_id:
        if mandate.representative_user_id != user.id:
            raise MarketplaceError(NOT_FOUND, status_code=404)
        if mandate.representative_user.status != AccountStatus.ACTIVE:
            raise MarketplaceError("This account cannot sign in.", status_code=403)
        ensure_verified_email(user)
        return
    organization = mandate.representative_organization
    membership = publisher_membership(user, organization.id)
    if membership is None:
        raise MarketplaceError(NOT_FOUND, status_code=404)
    if organization.status != OrganizationStatus.ACTIVE:
        raise MarketplaceError(ORG_NOT_ACTIVE, status_code=403)
    if membership.status != MembershipStatus.ACTIVE:
        raise MarketplaceError(MEMBERSHIP_NOT_ACTIVE, status_code=403)
    if Permission.PROPERTY_PUBLISH not in permissions_for(membership.role):
        raise MarketplaceError(PERMISSION_DENIED, status_code=403)
    ensure_verified_email(user)


def mandate_in_force(mandate, today=None) -> bool:
    today = today or timezone.localdate()
    if mandate.status != MandateStatus.ACTIVE:
        return False
    if mandate.valid_from > today:
        return False
    return mandate.valid_until is None or mandate.valid_until >= today


def actor_is_publisher(user, organization_id) -> bool:
    membership = publisher_membership(user, organization_id)
    if membership is None:
        return False
    if membership.organization.status != OrganizationStatus.ACTIVE:
        return False
    if membership.status != MembershipStatus.ACTIVE:
        return False
    return Permission.PROPERTY_PUBLISH in permissions_for(membership.role)
