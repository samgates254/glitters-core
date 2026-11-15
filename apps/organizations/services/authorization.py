"""Service-level organization authorization.

An organization id from a URL or body is a lookup key. It is not proof that
the caller may act. `is_staff` is not an organization role.
"""

from __future__ import annotations

from django.contrib.auth.models import AbstractBaseUser

from apps.identity.domain import ACCOUNT_BLOCKED, AUTHENTICATION_REQUIRED, AccountStatus
from apps.organizations.domain import (
    MEMBERSHIP_NOT_ACTIVE,
    NO_ACCESS,
    NOT_FOUND,
    ORG_NOT_ACTIVE,
    PERMISSION_DENIED,
    MembershipStatus,
    OrganizationStatus,
    permissions_for,
)
from apps.organizations.errors import OrganizationError
from apps.organizations.models import Organization, OrganizationMembership


def ensure_active_account(user) -> None:
    if not isinstance(user, AbstractBaseUser) or not getattr(user, "is_authenticated", False):
        raise OrganizationError(AUTHENTICATION_REQUIRED, status_code=401)
    if getattr(user, "status", None) != AccountStatus.ACTIVE:
        raise OrganizationError(ACCOUNT_BLOCKED, status_code=403)


def load_organization(organization_id, *, lock: bool = False) -> Organization:
    queryset = Organization.objects.all()
    if lock:
        queryset = queryset.select_for_update()
    try:
        return queryset.get(pk=organization_id)
    except Organization.DoesNotExist as exc:
        raise OrganizationError(NOT_FOUND, status_code=404) from exc


def authorize(user, organization_id, permission: str) -> OrganizationMembership:
    """Return the caller's active membership when it grants `permission`."""

    ensure_active_account(user)
    organization = load_organization(organization_id)
    try:
        membership = OrganizationMembership.objects.select_related("organization", "user").get(
            organization=organization,
            user=user,
        )
    except OrganizationMembership.DoesNotExist as exc:
        raise OrganizationError(NO_ACCESS, status_code=403) from exc
    if organization.status != OrganizationStatus.ACTIVE:
        raise OrganizationError(ORG_NOT_ACTIVE, status_code=403)
    if membership.status != MembershipStatus.ACTIVE:
        raise OrganizationError(MEMBERSHIP_NOT_ACTIVE, status_code=403)
    if permission not in permissions_for(membership.role):
        raise OrganizationError(PERMISSION_DENIED, status_code=403)
    return membership


def lock_active_membership(
    organization: Organization,
    user,
    permission: str,
) -> OrganizationMembership:
    """Re-read the caller under the organization row lock."""

    ensure_active_account(user)
    if organization.status != OrganizationStatus.ACTIVE:
        raise OrganizationError(ORG_NOT_ACTIVE, status_code=403)
    try:
        membership = OrganizationMembership.objects.select_for_update(of=("self",)).get(
            organization=organization,
            user=user,
        )
    except OrganizationMembership.DoesNotExist as exc:
        raise OrganizationError(NO_ACCESS, status_code=403) from exc
    if membership.status != MembershipStatus.ACTIVE:
        raise OrganizationError(MEMBERSHIP_NOT_ACTIVE, status_code=403)
    if permission not in permissions_for(membership.role):
        raise OrganizationError(PERMISSION_DENIED, status_code=403)
    return membership
