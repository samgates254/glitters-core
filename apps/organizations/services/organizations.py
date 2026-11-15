"""Create and maintain organization records."""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.identity.domain import normalize_email, normalize_phone
from apps.organizations.domain import (
    COUNTRY_RE,
    NO_CHANGES,
    PERMISSION_DENIED,
    PROFILE_FIELDS,
    SETTINGS_FIELDS,
    SLUG_RE,
    STATUS_NOT_ALLOWED,
    VERIFY_EMAIL_FIRST,
    EventAction,
    MembershipSource,
    MembershipStatus,
    OrganizationStatus,
    OrganizationType,
    Permission,
    Role,
    member_transition_allowed,
    normalize_slug,
    permissions_for,
    transition_allowed,
)
from apps.organizations.errors import OrganizationError
from apps.organizations.models import Organization, OrganizationMembership
from apps.organizations.services.audit import record_event
from apps.organizations.services.authorization import (
    authorize,
    ensure_active_account,
    load_organization,
    lock_active_membership,
)

_WEBSITE = URLValidator(schemes=["http", "https"])


def _clean_name(value: str, label: str) -> str:
    name = " ".join(str(value or "").split())
    if not name or len(name) > 200:
        raise OrganizationError(f"Enter a {label}.", status_code=400)
    return name


def _clean_country(value: str) -> str:
    country = str(value or "").strip().upper()
    if not COUNTRY_RE.fullmatch(country):
        raise OrganizationError("Country must be a two-letter code.", status_code=400)
    return country


def _clean_phone(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return normalize_phone(raw)
    except ValueError as exc:
        raise OrganizationError("Enter a valid phone number.", status_code=400) from exc


def _clean_website(value: str) -> str:
    website = str(value or "").strip()
    if not website:
        return ""
    try:
        _WEBSITE(website)
    except ValidationError as exc:
        raise OrganizationError("Enter a valid website.", status_code=400) from exc
    return website


def _clean_slug(value: str) -> str:
    slug = normalize_slug(value)
    if not slug or not SLUG_RE.fullmatch(slug):
        raise OrganizationError("Enter a valid slug.", status_code=400)
    return slug


def _clean_reference(value: str) -> str:
    reference = " ".join(str(value or "").split())
    if len(reference) > 64:
        raise OrganizationError("Registration reference is too long.", status_code=400)
    return reference


def _apply_profile(organization: Organization, fields: dict) -> list[str]:
    changed: list[str] = []
    if "display_name" in fields:
        organization.display_name = _clean_name(fields["display_name"], "display name")
        changed.append("display_name")
    if "contact_email" in fields:
        organization.contact_email = normalize_email(fields["contact_email"])
        changed.append("contact_email")
    if "contact_phone" in fields:
        organization.contact_phone = _clean_phone(fields["contact_phone"])
        changed.append("contact_phone")
    if "website" in fields:
        organization.website = _clean_website(fields["website"])
        changed.append("website")
    if "region" in fields:
        organization.region = " ".join(str(fields["region"] or "").split())[:80]
        changed.append("region")
    if "address_line" in fields:
        organization.address_line = " ".join(str(fields["address_line"] or "").split())[:255]
        changed.append("address_line")
    return changed


def _apply_settings(organization: Organization, fields: dict) -> list[str]:
    changed: list[str] = []
    if "legal_name" in fields:
        organization.legal_name = _clean_name(fields["legal_name"], "legal name")
        changed.append("legal_name")
    if "organization_type" in fields:
        kind = str(fields["organization_type"] or "")
        if kind not in OrganizationType.VALUES:
            raise OrganizationError("Choose a valid organization type.", status_code=400)
        organization.organization_type = kind
        changed.append("organization_type")
    if "registration_reference" in fields:
        organization.registration_reference = _clean_reference(fields["registration_reference"])
        changed.append("registration_reference")
    if "slug" in fields:
        organization.slug = _clean_slug(fields["slug"])
        changed.append("slug")
    if "country" in fields:
        organization.country = _clean_country(fields["country"])
        changed.append("country")
    return changed


def create_organization(*, user, fields: dict, request=None) -> Organization:
    ensure_active_account(user)
    if user.email_verified_at is None:
        raise OrganizationError(VERIFY_EMAIL_FIRST, status_code=403)
    legal_name = _clean_name(fields.get("legal_name", ""), "legal name")
    display_name = _clean_name(fields.get("display_name", ""), "display name")
    kind = str(fields.get("organization_type") or "")
    if kind not in OrganizationType.VALUES:
        raise OrganizationError("Choose a valid organization type.", status_code=400)
    slug_source = fields["slug"] if fields.get("slug") else display_name
    slug = _clean_slug(slug_source)
    now = timezone.now()
    try:
        with transaction.atomic():
            organization = Organization.objects.create(
                legal_name=legal_name,
                display_name=display_name,
                slug=slug,
                organization_type=kind,
                registration_reference=_clean_reference(fields.get("registration_reference", "")),
                contact_email=normalize_email(fields.get("contact_email", "")),
                contact_phone=_clean_phone(fields.get("contact_phone", "")),
                website=_clean_website(fields.get("website", "")),
                country=_clean_country(fields.get("country") or "KE"),
                region=" ".join(str(fields.get("region") or "").split())[:80],
                address_line=" ".join(str(fields.get("address_line") or "").split())[:255],
            )
            membership = OrganizationMembership.objects.create(
                organization=organization,
                user=user,
                role=Role.OWNER,
                status=MembershipStatus.ACTIVE,
                source=MembershipSource.CREATOR,
                joined_at=now,
            )
            record_event(
                actor=user,
                organization=organization,
                action=EventAction.ORGANIZATION_CREATED,
                object_type="organization",
                object_id=organization.id,
                request=request,
                metadata={"slug": slug, "organization_type": kind},
            )
            record_event(
                actor=user,
                organization=organization,
                action=EventAction.MEMBERSHIP_ACTIVATED,
                object_type="organization_membership",
                object_id=membership.id,
                request=request,
                metadata={"role": Role.OWNER, "source": MembershipSource.CREATOR},
            )
    except IntegrityError as exc:
        raise OrganizationError("This slug is already in use.", status_code=409) from exc
    return organization


def update_organization(*, user, organization_id, fields: dict, request=None) -> Organization:
    profile = {key: value for key, value in fields.items() if key in PROFILE_FIELDS}
    settings_fields = {key: value for key, value in fields.items() if key in SETTINGS_FIELDS}
    if not profile and not settings_fields:
        raise OrganizationError(NO_CHANGES, status_code=400)
    try:
        with transaction.atomic():
            organization = load_organization(organization_id, lock=True)
            membership = lock_active_membership(
                organization,
                user,
                Permission.ORGANIZATION_UPDATE
                if profile
                else Permission.ORGANIZATION_MANAGE_SETTINGS,
            )
            # A profile edit and a settings edit are separate permissions.
            granted = permissions_for(membership.role)
            if profile and Permission.ORGANIZATION_UPDATE not in granted:
                raise OrganizationError(PERMISSION_DENIED, status_code=403)
            if settings_fields and Permission.ORGANIZATION_MANAGE_SETTINGS not in granted:
                raise OrganizationError(PERMISSION_DENIED, status_code=403)
            changed = _apply_profile(organization, profile)
            changed.extend(_apply_settings(organization, settings_fields))
            organization.save()
            record_event(
                actor=user,
                organization=organization,
                action=EventAction.ORGANIZATION_UPDATED,
                object_type="organization",
                object_id=organization.id,
                request=request,
                metadata={"fields": changed},
            )
    except IntegrityError as exc:
        raise OrganizationError("This slug is already in use.", status_code=409) from exc
    return organization


def change_organization_status(
    *,
    actor,
    organization_id,
    status: str,
    request=None,
) -> Organization:
    ensure_active_account(actor)
    if status not in OrganizationStatus.VALUES:
        raise OrganizationError(STATUS_NOT_ALLOWED, status_code=400)
    with transaction.atomic():
        organization = load_organization(organization_id, lock=True)
        previous = organization.status
        if previous == status:
            raise OrganizationError(STATUS_NOT_ALLOWED, status_code=409)
        if actor.is_staff or actor.is_superuser:
            if not transition_allowed(previous, status):
                raise OrganizationError(STATUS_NOT_ALLOWED, status_code=409)
        else:
            lock_active_membership(organization, actor, Permission.ORGANIZATION_MANAGE_STATUS)
            if not member_transition_allowed(previous, status):
                raise OrganizationError(STATUS_NOT_ALLOWED, status_code=409)
        organization.status = status
        organization.save(update_fields=["status", "updated_at"])
        record_event(
            actor=actor,
            organization=organization,
            action=EventAction.ORGANIZATION_STATUS_CHANGED,
            object_type="organization",
            object_id=organization.id,
            request=request,
            metadata={"from_status": previous, "to_status": status},
        )
    return organization


def get_organization(*, user, organization_id) -> Organization:
    membership = authorize(user, organization_id, Permission.ORGANIZATION_VIEW)
    return membership.organization
