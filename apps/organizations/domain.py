"""Organization rules, roles, and the central permission registry.

A permission listed in FUTURE_PERMISSIONS is reserved for a later module.
It is granted to no role until that module assigns it here. M4 assigns
property.create, property.update, and property.archive. M5 assigns
property.publish. M6-B.3 assigns leads.view and leads.manage to owner,
admin, and manager. listing.manage stays reserved. property.publish does
not grant lead access.
"""

from __future__ import annotations

import re
from datetime import timedelta

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
COUNTRY_RE = re.compile(r"^[A-Z]{2}$")

INVITATION_TTL = timedelta(days=7)

NOT_FOUND = "Not found."
NO_ACCESS = "You do not have access to this organization."
ORG_NOT_ACTIVE = "This organization is not active."
MEMBERSHIP_NOT_ACTIVE = "This membership is not active."
PERMISSION_DENIED = "You do not have permission for this action."
INVITATION_INVALID = "This invitation is invalid or expired."  # noqa: S105
INVITATION_MISMATCH = "This invitation does not match the signed-in account."
VERIFY_EMAIL_FIRST = "Verify your email before continuing."
ALREADY_MEMBER = "This account is already a member of the organization."
MEMBERSHIP_NEEDS_INVITATION = "This membership needs a new invitation before it can be restored."
DUPLICATE_INVITATION = "An invitation is already pending for this email."
ALREADY_HAS_MEMBERSHIP = "This person already has a membership."
ROLE_NOT_ASSIGNABLE = "This role cannot be assigned."
OWNERSHIP_TRANSFER_REQUIRED = "Ownership changes use the transfer operation."
CANNOT_CHANGE_MEMBERSHIP = "You cannot change this membership."
LAST_OWNER = "The organization must keep an active owner."
NOT_THE_OWNER = "Only the active owner can transfer ownership."
STATUS_NOT_ALLOWED = "This status change is not allowed."
CONFLICT = "This change conflicts with the current organization state."
NO_CHANGES = "No changes were submitted."
INVITATION_SENT = "Invitation sent."


class OrganizationType:
    AGENCY = "agency"
    DEVELOPER = "developer"
    PROPERTY_MANAGER = "property_manager"
    LANDLORD = "landlord"
    HOSPITALITY = "hospitality"
    PROFESSIONAL_SERVICE = "professional_service"
    CONTRACTOR = "contractor"

    CHOICES = (
        (AGENCY, "Real estate agency"),
        (DEVELOPER, "Property developer"),
        (PROPERTY_MANAGER, "Property management company"),
        (LANDLORD, "Landlord business"),
        (HOSPITALITY, "Hospitality operator"),
        (PROFESSIONAL_SERVICE, "Professional service provider"),
        (CONTRACTOR, "Contractor"),
    )
    VALUES = frozenset(value for value, _label in CHOICES)


class OrganizationStatus:
    PENDING_VERIFICATION = "pending_verification"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DEACTIVATED = "deactivated"
    REJECTED = "rejected"

    CHOICES = (
        (PENDING_VERIFICATION, "Pending verification"),
        (ACTIVE, "Active"),
        (SUSPENDED, "Suspended"),
        (DEACTIVATED, "Deactivated"),
        (REJECTED, "Rejected"),
    )
    VALUES = frozenset(value for value, _label in CHOICES)


# Platform staff may apply these. Rejected is terminal.
TRANSITIONS: dict[str, frozenset[str]] = {
    OrganizationStatus.PENDING_VERIFICATION: frozenset(
        {OrganizationStatus.ACTIVE, OrganizationStatus.REJECTED}
    ),
    OrganizationStatus.ACTIVE: frozenset(
        {
            OrganizationStatus.SUSPENDED,
            OrganizationStatus.DEACTIVATED,
            OrganizationStatus.PENDING_VERIFICATION,
        }
    ),
    OrganizationStatus.SUSPENDED: frozenset(
        {OrganizationStatus.ACTIVE, OrganizationStatus.DEACTIVATED}
    ),
    OrganizationStatus.DEACTIVATED: frozenset({OrganizationStatus.ACTIVE}),
    OrganizationStatus.REJECTED: frozenset(),
}

# An owner may wind an active organization down. Reactivation is a staff action.
MEMBER_STATUS_TRANSITIONS: dict[str, frozenset[str]] = {
    OrganizationStatus.ACTIVE: frozenset({OrganizationStatus.DEACTIVATED}),
}


class MembershipStatus:
    INVITED = "invited"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    REMOVED = "removed"

    CHOICES = (
        (INVITED, "Invited"),
        (ACTIVE, "Active"),
        (SUSPENDED, "Suspended"),
        (REMOVED, "Removed"),
    )
    VALUES = frozenset(value for value, _label in CHOICES)


class MembershipSource:
    CREATOR = "creator"
    INVITATION = "invitation"
    PLATFORM = "platform"

    CHOICES = (
        (CREATOR, "Creator"),
        (INVITATION, "Invitation"),
        (PLATFORM, "Platform"),
    )
    VALUES = frozenset(value for value, _label in CHOICES)


class InvitationStatus:
    PENDING = "pending"
    ACCEPTED = "accepted"
    REVOKED = "revoked"
    EXPIRED = "expired"

    CHOICES = (
        (PENDING, "Pending"),
        (ACCEPTED, "Accepted"),
        (REVOKED, "Revoked"),
        (EXPIRED, "Expired"),
    )
    VALUES = frozenset(value for value, _label in CHOICES)


class Role:
    OWNER = "owner"
    ADMIN = "admin"
    MANAGER = "manager"
    MEMBER = "member"
    VIEWER = "viewer"

    CHOICES = (
        (OWNER, "Owner"),
        (ADMIN, "Admin"),
        (MANAGER, "Manager"),
        (MEMBER, "Member"),
        (VIEWER, "Viewer"),
    )
    VALUES = frozenset(value for value, _label in CHOICES)
    # Catalogue order is the privilege order.
    ORDER = (OWNER, ADMIN, MANAGER, MEMBER, VIEWER)


ROLE_RANK = {
    Role.VIEWER: 10,
    Role.MEMBER: 20,
    Role.MANAGER: 30,
    Role.ADMIN: 40,
    Role.OWNER: 50,
}


class Permission:
    ORGANIZATION_VIEW = "organization.view"
    ORGANIZATION_UPDATE = "organization.update"
    ORGANIZATION_MANAGE_SETTINGS = "organization.manage_settings"
    ORGANIZATION_MANAGE_MEMBERS = "organization.manage_members"
    ORGANIZATION_INVITE_MEMBERS = "organization.invite_members"
    ORGANIZATION_REMOVE_MEMBERS = "organization.remove_members"
    ORGANIZATION_MANAGE_ROLES = "organization.manage_roles"
    ORGANIZATION_VIEW_AUDIT = "organization.view_audit"
    ORGANIZATION_MANAGE_BILLING = "organization.manage_billing"
    ORGANIZATION_TRANSFER_OWNERSHIP = "organization.transfer_ownership"
    ORGANIZATION_MANAGE_STATUS = "organization.manage_status"

    PROPERTY_CREATE = "property.create"
    PROPERTY_UPDATE = "property.update"
    PROPERTY_PUBLISH = "property.publish"
    PROPERTY_ARCHIVE = "property.archive"
    LISTING_MANAGE = "listing.manage"
    LEADS_VIEW = "leads.view"
    LEADS_MANAGE = "leads.manage"
    OPERATIONS_VIEW = "operations.view"
    OPERATIONS_MANAGE = "operations.manage"
    LEASES_MANAGE = "leases.manage"
    FINANCE_VIEW = "finance.view"
    FINANCE_MANAGE = "finance.manage"
    HOSPITALITY_MANAGE = "hospitality.manage"
    SERVICE_REQUESTS_MANAGE = "service_requests.manage"


# Owners, admins, and managers operate property records and can submit
# organization listings. Members and viewers cannot. property.publish does
# not skip moderation, a listing mandate, or account checks, and it does
# not grant enquiry access.
_PROPERTY_RECORD_PERMISSIONS = frozenset(
    {
        Permission.PROPERTY_CREATE,
        Permission.PROPERTY_UPDATE,
        Permission.PROPERTY_ARCHIVE,
        Permission.PROPERTY_PUBLISH,
    }
)

# Separate from property.publish. A role can publish and still be unable
# to read enquiries if these two names are absent.
_LEAD_PERMISSIONS = frozenset(
    {
        Permission.LEADS_VIEW,
        Permission.LEADS_MANAGE,
    }
)

_OPERATIONS_PERMISSIONS = frozenset(
    {
        Permission.OPERATIONS_VIEW,
        Permission.OPERATIONS_MANAGE,
    }
)

_OWNER_PERMISSIONS = (
    frozenset(
        {
            Permission.ORGANIZATION_VIEW,
            Permission.ORGANIZATION_UPDATE,
            Permission.ORGANIZATION_MANAGE_SETTINGS,
            Permission.ORGANIZATION_MANAGE_MEMBERS,
            Permission.ORGANIZATION_INVITE_MEMBERS,
            Permission.ORGANIZATION_REMOVE_MEMBERS,
            Permission.ORGANIZATION_MANAGE_ROLES,
            Permission.ORGANIZATION_VIEW_AUDIT,
            Permission.ORGANIZATION_MANAGE_BILLING,
            Permission.ORGANIZATION_TRANSFER_OWNERSHIP,
            Permission.ORGANIZATION_MANAGE_STATUS,
        }
    )
    | _PROPERTY_RECORD_PERMISSIONS
    | _LEAD_PERMISSIONS
    | _OPERATIONS_PERMISSIONS
)

_ADMIN_PERMISSIONS = _OWNER_PERMISSIONS - {
    Permission.ORGANIZATION_TRANSFER_OWNERSHIP,
    Permission.ORGANIZATION_MANAGE_STATUS,
}

_MANAGER_PERMISSIONS = (
    frozenset(
        {
            Permission.ORGANIZATION_VIEW,
            Permission.ORGANIZATION_UPDATE,
            Permission.ORGANIZATION_INVITE_MEMBERS,
            Permission.ORGANIZATION_MANAGE_MEMBERS,
            Permission.ORGANIZATION_VIEW_AUDIT,
        }
    )
    | _PROPERTY_RECORD_PERMISSIONS
    | _LEAD_PERMISSIONS
    | _OPERATIONS_PERMISSIONS
)

_READ_PERMISSIONS = frozenset({Permission.ORGANIZATION_VIEW})

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    Role.OWNER: _OWNER_PERMISSIONS,
    Role.ADMIN: _ADMIN_PERMISSIONS,
    Role.MANAGER: _MANAGER_PERMISSIONS,
    Role.MEMBER: _READ_PERMISSIONS,
    Role.VIEWER: _READ_PERMISSIONS,
}

# Still ungranted. property.publish moved into ROLE_PERMISSIONS in M5.
# leads.view and leads.manage moved in M6-B.3. listing.manage stays
# reserved so it is not a second name for publication.
FUTURE_PERMISSIONS = (
    Permission.LISTING_MANAGE,
    Permission.LEASES_MANAGE,
    Permission.FINANCE_VIEW,
    Permission.FINANCE_MANAGE,
    Permission.HOSPITALITY_MANAGE,
    Permission.SERVICE_REQUESTS_MANAGE,
)

PROFILE_FIELDS = frozenset(
    {
        "display_name",
        "contact_email",
        "contact_phone",
        "website",
        "region",
        "address_line",
    }
)
SETTINGS_FIELDS = frozenset(
    {
        "legal_name",
        "organization_type",
        "registration_reference",
        "slug",
        "country",
    }
)

FORBIDDEN_METADATA_KEYS = frozenset(
    {
        "token",
        "raw_token",
        "token_hash",
        "password",
        "secret",
        "authorization",
        "cookie",
    }
)


class EventAction:
    ORGANIZATION_CREATED = "organization.created"
    ORGANIZATION_UPDATED = "organization.updated"
    ORGANIZATION_STATUS_CHANGED = "organization.status_changed"
    INVITATION_CREATED = "invitation.created"
    INVITATION_RESENT = "invitation.resent"
    INVITATION_ACCEPTED = "invitation.accepted"
    INVITATION_REVOKED = "invitation.revoked"
    INVITATION_EXPIRED = "invitation.expired"
    MEMBERSHIP_ACTIVATED = "membership.activated"
    MEMBERSHIP_SUSPENDED = "membership.suspended"
    MEMBERSHIP_REACTIVATED = "membership.reactivated"
    MEMBERSHIP_REMOVED = "membership.removed"
    MEMBERSHIP_ROLE_CHANGED = "membership.role_changed"
    OWNERSHIP_TRANSFERRED = "ownership.transferred"

    CHOICES = (
        (ORGANIZATION_CREATED, "Organization created"),
        (ORGANIZATION_UPDATED, "Organization updated"),
        (ORGANIZATION_STATUS_CHANGED, "Organization status changed"),
        (INVITATION_CREATED, "Invitation created"),
        (INVITATION_RESENT, "Invitation resent"),
        (INVITATION_ACCEPTED, "Invitation accepted"),
        (INVITATION_REVOKED, "Invitation revoked"),
        (INVITATION_EXPIRED, "Invitation expired"),
        (MEMBERSHIP_ACTIVATED, "Membership activated"),
        (MEMBERSHIP_SUSPENDED, "Membership suspended"),
        (MEMBERSHIP_REACTIVATED, "Membership reactivated"),
        (MEMBERSHIP_REMOVED, "Membership removed"),
        (MEMBERSHIP_ROLE_CHANGED, "Membership role changed"),
        (OWNERSHIP_TRANSFERRED, "Ownership transferred"),
    )
    VALUES = frozenset(value for value, _label in CHOICES)


def permissions_for(role: str) -> frozenset[str]:
    return ROLE_PERMISSIONS.get(role, frozenset())


def permission_names(role: str) -> list[str]:
    return sorted(permissions_for(role))


def reserved_permissions() -> list[str]:
    granted: set[str] = set()
    for permissions in ROLE_PERMISSIONS.values():
        granted.update(permissions)
    return sorted(set(FUTURE_PERMISSIONS) - granted)


def role_catalogue() -> list[dict[str, object]]:
    return [{"role": role, "permissions": permission_names(role)} for role in Role.ORDER]


def rank_exceeds(actor_role: str, target_role: str) -> bool:
    """True when the actor outranks the target. Equal ranks do not qualify."""

    actor_rank = ROLE_RANK.get(actor_role)
    target_rank = ROLE_RANK.get(target_role)
    if actor_rank is None or target_rank is None:
        return False
    return actor_rank > target_rank


def transition_allowed(current: str, new: str) -> bool:
    return new in TRANSITIONS.get(current, frozenset())


def member_transition_allowed(current: str, new: str) -> bool:
    return new in MEMBER_STATUS_TRANSITIONS.get(current, frozenset())


def normalize_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower())
    slug = slug.strip("-")[:80].strip("-")
    return slug


def cache_key(organization_id, suffix: str) -> str:
    """Cache keys for organization data start with the organization id.

    A key without that prefix is not an organization-scoped key. Callers still
    have to authorize the caller before reading or writing the cached value.
    """

    tail = str(suffix or "").strip().strip(":")
    if not tail:
        raise ValueError("A cache suffix is required.")
    return f"org:{organization_id}:{tail}"
