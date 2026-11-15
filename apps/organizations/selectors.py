"""Read models for organization collections."""

from __future__ import annotations

from django.utils import timezone

from apps.organizations.domain import InvitationStatus, MembershipStatus, OrganizationStatus
from apps.organizations.models import (
    Organization,
    OrganizationEvent,
    OrganizationInvitation,
    OrganizationMembership,
)


def accessible_organizations(user):
    """Organizations the user can operate: active membership and active organization."""

    return (
        Organization.objects.filter(
            memberships__user=user,
            memberships__status=MembershipStatus.ACTIVE,
            status=OrganizationStatus.ACTIVE,
        )
        .distinct()
        .order_by("display_name", "id")
    )


def memberships_for_user(user):
    return (
        OrganizationMembership.objects.select_related("organization")
        .filter(user=user)
        .order_by("-created_at", "id")
    )


def members_of(organization: Organization):
    return (
        OrganizationMembership.objects.select_related("user")
        .filter(organization=organization)
        .exclude(status=MembershipStatus.REMOVED)
        .order_by("created_at", "id")
    )


def pending_invitations(organization: Organization):
    return OrganizationInvitation.objects.filter(
        organization=organization,
        status=InvitationStatus.PENDING,
        expires_at__gt=timezone.now(),
    ).order_by("created_at", "id")


def audit_events(organization: Organization):
    return OrganizationEvent.objects.filter(organization=organization).order_by("-created_at", "id")
