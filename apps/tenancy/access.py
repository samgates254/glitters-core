"""Which leases an actor may discover.

Membership is not title. A hidden lease answers as not found.
"""

from __future__ import annotations

from django.db.models import Q, QuerySet

from apps.organizations.domain import (
    MembershipStatus,
    OrganizationStatus,
    Permission,
    permissions_for,
)
from apps.organizations.models import OrganizationMembership
from apps.properties.domain import AccessCapability, AccessStatus
from apps.properties.models import PropertyAccess
from apps.tenancy.models import Lease, LeaseParty


def leases_visible_to(user) -> QuerySet[Lease]:
    """Leases the actor may list or retrieve. Unauthenticated actors see none."""

    if not getattr(user, "is_authenticated", False):
        return Lease.objects.none()

    memberships = OrganizationMembership.objects.filter(
        user=user,
        status=MembershipStatus.ACTIVE,
        organization__status=OrganizationStatus.ACTIVE,
    ).only("organization_id", "role")
    organization_ids = [
        membership.organization_id
        for membership in memberships
        if Permission.LEASES_VIEW in permissions_for(membership.role)
    ]
    managed_properties = PropertyAccess.objects.filter(
        user=user,
        status=AccessStatus.ACTIVE,
        capability=AccessCapability.MANAGE,
    ).values("property_id")
    party_leases = LeaseParty.objects.filter(user=user).values("lease_id")
    return Lease.objects.filter(
        Q(property_owner=user)
        | Q(created_by=user)
        | Q(property_id__in=managed_properties)
        | Q(managing_organization_id__in=organization_ids)
        | Q(pk__in=party_leases)
    ).distinct()


def visible_lease(user, lease_id) -> Lease | None:
    return leases_visible_to(user).filter(pk=lease_id).first()
