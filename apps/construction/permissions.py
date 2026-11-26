from __future__ import annotations

from apps.construction.domain import ProjectRole
from apps.organizations.domain import MembershipStatus, Role
from apps.organizations.models import OrganizationMembership

PROJECT_MANAGEMENT_ROLES = {
    ProjectRole.PROJECT_ADMIN,
    ProjectRole.PROJECT_MANAGER,
}


def has_org_access(user, organization) -> bool:
    if not user or not user.is_authenticated:
        return False
    if getattr(user, "is_staff", False):
        return True
    return OrganizationMembership.objects.filter(
        organization=organization,
        user=user,
        status=MembershipStatus.ACTIVE,
        role__in=(Role.OWNER, Role.ADMIN, Role.MANAGER, Role.MEMBER),
    ).exists()


def has_project_role(user, project, roles) -> bool:
    if not user or not user.is_authenticated or not has_org_access(user, project.organization):
        return False
    if getattr(user, "is_staff", False):
        return True
    if OrganizationMembership.objects.filter(
        organization=project.organization,
        user=user,
        status=MembershipStatus.ACTIVE,
        role__in=(Role.OWNER, Role.ADMIN, Role.MANAGER),
    ).exists():
        return True
    return project.members.filter(user=user, role__in=roles).exists()
