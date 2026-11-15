"""Object authorization for one property.

An id in the URL is a lookup key. Personal rows are visible only through
property_access. Organization rows also require an active membership, unless
the caller holds an explicit grant and that organization is active.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.utils import timezone

from apps.organizations.domain import (
    MEMBERSHIP_NOT_ACTIVE,
    ORG_NOT_ACTIVE,
    MembershipStatus,
    OrganizationStatus,
    Permission,
    permissions_for,
)
from apps.organizations.models import OrganizationMembership
from apps.organizations.services.authorization import ensure_active_account
from apps.properties.domain import (
    NOT_FOUND,
    PERMISSION_DENIED,
    AccessCapability,
    AccessOrigin,
    AccessStatus,
    ManagementContext,
)
from apps.properties.errors import PropertyError
from apps.properties.models import Property, PropertyAccess


@dataclass(frozen=True)
class PropertyDecision:
    record: Property
    can_update: bool
    can_archive: bool

    @property
    def can_read_coordinates(self) -> bool:
        return self.can_update

    @property
    def can_read_audit(self) -> bool:
        return self.can_update

    @property
    def can_manage_parties(self) -> bool:
        return self.can_update


def decide(user, property_id) -> PropertyDecision:
    ensure_active_account(user)
    try:
        record = Property.objects.select_related(
            "location__address__country",
            "location__address__administrative_area",
            "location__address__locality",
            "managing_organization",
            "parent",
            "created_by",
        ).get(pk=property_id)
    except Property.DoesNotExist as exc:
        raise PropertyError(NOT_FOUND, status_code=404) from exc

    grant = (
        PropertyAccess.objects.filter(property=record, user=user, status=AccessStatus.ACTIVE)
        .order_by("-created_at")
        .first()
    )
    if grant is not None and grant.origin == AccessOrigin.INHERITED:
        delegation = (
            grant.operational_delegations.filter(status=AccessStatus.ACTIVE)
            .select_related("agreement")
            .first()
        )
        if delegation is not None:
            agr = delegation.agreement
            if agr.status != "active" or (
                agr.valid_until is not None and timezone.localdate() > agr.valid_until
            ):
                grant = None

    if record.management_context == ManagementContext.PERSONAL:
        if grant is None:
            raise PropertyError(NOT_FOUND, status_code=404)
        manage = grant.capability == AccessCapability.MANAGE
        return PropertyDecision(record=record, can_update=manage, can_archive=manage)

    membership = OrganizationMembership.objects.filter(
        organization_id=record.managing_organization_id,
        user=user,
    ).first()
    if membership is None and grant is None:
        raise PropertyError(NOT_FOUND, status_code=404)
    if record.managing_organization.status != OrganizationStatus.ACTIVE:
        raise PropertyError(ORG_NOT_ACTIVE, status_code=403)
    if membership is not None and membership.status != MembershipStatus.ACTIVE:
        raise PropertyError(MEMBERSHIP_NOT_ACTIVE, status_code=403)

    org_update = False
    org_archive = False
    if membership is not None and membership.status == MembershipStatus.ACTIVE:
        granted = permissions_for(membership.role)
        org_update = Permission.PROPERTY_UPDATE in granted
        org_archive = Permission.PROPERTY_ARCHIVE in granted
    grant_manage = grant is not None and grant.capability == AccessCapability.MANAGE
    return PropertyDecision(
        record=record,
        can_update=org_update or grant_manage,
        can_archive=org_archive or grant_manage,
    )


def require_update(decision: PropertyDecision) -> None:
    if not decision.can_update:
        raise PropertyError(PERMISSION_DENIED, status_code=403)


def require_archive(decision: PropertyDecision) -> None:
    if not decision.can_archive:
        raise PropertyError(PERMISSION_DENIED, status_code=403)
