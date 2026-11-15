"""DRF permission classes. Services repeat these checks."""

from __future__ import annotations

from rest_framework.permissions import BasePermission

from apps.organizations.domain import PERMISSION_DENIED
from apps.organizations.errors import OrganizationError, raise_for_organization
from apps.organizations.services.authorization import authorize


class HasOrganizationPermission(BasePermission):
    """Authorize the URL organization id. A client-supplied id is only a key."""

    message = PERMISSION_DENIED

    def has_permission(self, request, view) -> bool:
        permission = getattr(view, "required_permission", None)
        organization_id = view.kwargs.get("organization_id")
        if permission is None or organization_id is None:
            return False
        try:
            membership = authorize(request.user, organization_id, permission)
        except OrganizationError as exc:
            raise_for_organization(exc)
        request.glitters_membership = membership
        request.glitters_organization = membership.organization
        return True
