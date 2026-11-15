"""Spatial multi-tenant authorization service and organization access rules."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from django.core.exceptions import PermissionDenied

from apps.organizations.domain import MembershipStatus, Role
from apps.spatial.domain import CROSS_ORG_ACCESS_DENIED, SURVEYOR_CREDENTIAL_REQUIRED

if TYPE_CHECKING:
    from apps.identity.models import User
    from apps.organizations.models import Organization
    from apps.spatial.models import SpatialEntity


class SpatialAccessService:
    @staticmethod
    def can_view_entity(
        entity: SpatialEntity,
        user: User | None,
        organization: Organization | None = None,
    ) -> bool:
        """Determines if a user/client can view a spatial entity."""
        # Public data (organization is None) is readable by all
        if entity.organization_id is None:
            return True

        # Private data requires an authenticated user and matching active organization
        if not user or not user.is_authenticated:
            return False

        if not organization:
            return False

        if entity.organization_id != organization.id:
            return False

        return True

    @staticmethod
    def assert_can_view(
        entity: SpatialEntity,
        user: User | None,
        organization: Organization | None = None,
    ) -> None:
        """Asserts view permission; raises PermissionDenied if unauthorized."""
        if not SpatialAccessService.can_view_entity(entity, user, organization):
            raise PermissionDenied(CROSS_ORG_ACCESS_DENIED)

    @staticmethod
    def assert_can_manage(
        entity: SpatialEntity,
        user: User,
        organization: Organization,
    ) -> None:
        """Asserts manage/edit permission on an organization's spatial asset."""
        if not user or not user.is_authenticated:
            raise PermissionDenied(CROSS_ORG_ACCESS_DENIED)

        if entity.organization_id is None:
            # Platform administrators only for public foundation data
            if not user.is_staff:
                raise PermissionDenied("Only platform administrators can modify public spatial data.")
            return

        if entity.organization_id != organization.id:
            raise PermissionDenied(CROSS_ORG_ACCESS_DENIED)

        # Check membership role in organization
        membership = user.organization_memberships.filter(
            organization=organization,
            status=MembershipStatus.ACTIVE,
        ).first()
        if not membership or membership.role not in (Role.OWNER, Role.ADMIN, Role.MANAGER):
            raise PermissionDenied("You do not have administrative permissions for this spatial asset.")

    @staticmethod
    def assert_surveyor_credentials(user: User, license_number: str) -> None:
        """Validates that a user holds valid surveyor credentials for restricted survey data."""
        if not user or not user.is_authenticated:
            raise PermissionDenied(SURVEYOR_CREDENTIAL_REQUIRED)
        if not license_number or not license_number.strip():
            raise PermissionDenied(SURVEYOR_CREDENTIAL_REQUIRED)
