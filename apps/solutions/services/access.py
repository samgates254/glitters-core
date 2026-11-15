"""Staff desk access and partner organization access.

Partner operators are owners, admins, and managers of the assigned
organization. Those roles already carry property.publish. leads.view and
leads.manage stay reserved. Members and viewers can read the organization
and cannot open this desk.
"""

from __future__ import annotations

from apps.marketplace.errors import MarketplaceError
from apps.marketplace.services.access import ensure_moderator
from apps.organizations.domain import (
    MembershipStatus,
    OrganizationStatus,
    Permission,
    permissions_for,
)
from apps.organizations.errors import OrganizationError
from apps.organizations.models import OrganizationMembership
from apps.solutions.domain import PERMISSION_DENIED
from apps.solutions.errors import SolutionsError


def ensure_staff(user) -> None:
    try:
        ensure_moderator(user)
    except (MarketplaceError, OrganizationError) as exc:
        raise SolutionsError(exc.detail, status_code=exc.status_code) from exc


def operating_organization_ids(user) -> set:
    rows = OrganizationMembership.objects.filter(
        user=user,
        status=MembershipStatus.ACTIVE,
        organization__status=OrganizationStatus.ACTIVE,
    ).select_related("organization")
    allowed = set()
    for membership in rows:
        if Permission.PROPERTY_PUBLISH in permissions_for(membership.role):
            allowed.add(membership.organization_id)
    return allowed


def require_operator(user, organization_id) -> None:
    if organization_id is None or organization_id not in operating_organization_ids(user):
        raise SolutionsError(PERMISSION_DENIED, status_code=403)
