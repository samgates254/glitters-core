"""Listing mandates. A mandate is marketplace authority, not legal title."""

from __future__ import annotations

from datetime import date

from django.contrib.auth import get_user_model
from django.db import OperationalError, transaction
from django.utils import timezone

from apps.identity.domain import AccountStatus
from apps.marketplace.domain import (
    ACCOUNT_NOT_FOUND,
    CONFLICT,
    EVIDENCE_MAX,
    MANDATE_EXPIRED,
    MANDATE_INVALID,
    NOT_FOUND,
    ORG_NOT_ACTIVE,
    ORGANIZATION_NOT_FOUND,
    PERMISSION_DENIED,
    PROPERTY_ARCHIVED,
    EventAction,
    ListingScope,
    MandateStatus,
)
from apps.marketplace.errors import MarketplaceError
from apps.marketplace.models import ListingMandate
from apps.marketplace.services.access import (
    actor_is_publisher,
    ensure_actor,
    ensure_verified_email,
    require_representative,
)
from apps.marketplace.services.audit import record_event
from apps.organizations.domain import OrganizationStatus
from apps.organizations.models import Organization
from apps.properties.domain import ManagementContext, PropertyStatus
from apps.properties.errors import PropertyError
from apps.properties.services.authorization import decide
from apps.properties.services.locking import is_deadlock, lock_properties

User = get_user_model()


def _text(value, *, maximum: int, field: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str) or len(value.strip()) > maximum or "\n" in value or "\r" in value:
        raise MarketplaceError(f"This {field} is not valid.", status_code=400)
    return value.strip()


def _date(value, *, default: date | None = None) -> date:
    if value in (None, ""):
        if default is None:
            raise MarketplaceError(MANDATE_INVALID, status_code=400)
        return default
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise MarketplaceError(MANDATE_INVALID, status_code=400)
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise MarketplaceError(MANDATE_INVALID, status_code=400) from exc


def _active_user(user_id):
    try:
        user = User.objects.get(pk=user_id)
    except (User.DoesNotExist, ValueError, TypeError) as exc:
        raise MarketplaceError(ACCOUNT_NOT_FOUND, status_code=400) from exc
    if user.status != AccountStatus.ACTIVE:
        raise MarketplaceError("This account cannot sign in.", status_code=403)
    return user


def _active_organization(organization_id):
    try:
        organization = Organization.objects.get(pk=organization_id)
    except (Organization.DoesNotExist, ValueError, TypeError) as exc:
        raise MarketplaceError(ORGANIZATION_NOT_FOUND, status_code=400) from exc
    if organization.status != OrganizationStatus.ACTIVE:
        raise MarketplaceError(ORG_NOT_ACTIVE, status_code=403)
    return organization


def _can_speak_for_organization(actor, record) -> bool:
    if record.management_context != ManagementContext.ORGANIZATION:
        return False
    return actor_is_publisher(actor, record.managing_organization_id)


def _lock(mandate_id) -> ListingMandate:
    try:
        return (
            ListingMandate.objects.select_for_update(of=("self",))
            .select_related(
                "property",
                "principal_user",
                "principal_organization",
                "representative_user",
                "representative_organization",
                "granting_user",
            )
            .get(pk=mandate_id)
        )
    except (ListingMandate.DoesNotExist, ValueError, TypeError) as exc:
        raise MarketplaceError(NOT_FOUND, status_code=404) from exc


def _event_organization(mandate):
    return mandate.representative_organization_id or mandate.principal_organization_id


def create_mandate(*, actor, data: dict, request=None) -> ListingMandate:
    ensure_actor(actor)
    ensure_verified_email(actor)
    try:
        decision = decide(actor, data.get("property_id"))
    except PropertyError:
        raise
    if not decision.can_update:
        raise MarketplaceError(PERMISSION_DENIED, status_code=403)
    try:
        with transaction.atomic():
            record = lock_properties(decision.record.id)[decision.record.id]
            return _create_locked(actor, record, data, request)
    except OperationalError as exc:
        if is_deadlock(exc):
            raise MarketplaceError(CONFLICT, status_code=409) from exc
        raise


def _create_locked(actor, record, data, request) -> ListingMandate:
    if record.status == PropertyStatus.ARCHIVED:
        raise MarketplaceError(PROPERTY_ARCHIVED, status_code=409)
    scope = data.get("listing_scope")
    if scope not in ListingScope.VALUES:
        raise MarketplaceError(MANDATE_INVALID, status_code=400)
    representative_user_id = data.get("representative_user_id") or None
    representative_organization_id = data.get("representative_organization_id") or None
    if bool(representative_user_id) == bool(representative_organization_id):
        raise MarketplaceError("Choose a person or an organization to authorize.", status_code=400)
    principal = data.get("principal") or (
        "organization" if _can_speak_for_organization(actor, record) else "self"
    )
    if principal == "organization":
        if not _can_speak_for_organization(actor, record):
            raise MarketplaceError(
                "You cannot grant organization authority for this property.",
                status_code=403,
            )
        principal_user = None
        principal_organization = record.managing_organization
    elif principal == "self":
        principal_user = actor
        principal_organization = None
    else:
        raise MarketplaceError(MANDATE_INVALID, status_code=400)
    if representative_user_id:
        representative_user = _active_user(representative_user_id)
        representative_organization = None
    else:
        representative_user = None
        representative_organization = _active_organization(representative_organization_id)
    self_declared = _same_party(
        principal_user,
        principal_organization,
        representative_user,
        representative_organization,
    )
    valid_from = _date(data.get("valid_from"), default=timezone.localdate())
    valid_until = _date(data.get("valid_until"), default=None) if data.get("valid_until") else None
    if valid_until is not None and valid_until < valid_from:
        raise MarketplaceError(MANDATE_INVALID, status_code=400)
    if valid_until is not None and valid_until < timezone.localdate():
        raise MarketplaceError(MANDATE_EXPIRED, status_code=400)
    evidence = _text(
        data.get("evidence_reference"),
        maximum=EVIDENCE_MAX,
        field="evidence reference",
    )
    now = timezone.now()
    mandate = ListingMandate.objects.create(
        property=record,
        principal_user=principal_user,
        principal_organization=principal_organization,
        representative_user=representative_user,
        representative_organization=representative_organization,
        granting_user=actor,
        accepted_by=actor if self_declared else None,
        listing_scope=scope,
        valid_from=valid_from,
        valid_until=valid_until,
        evidence_reference=evidence,
        status=MandateStatus.ACTIVE if self_declared else MandateStatus.PENDING_ACCEPTANCE,
        self_declared=self_declared,
        accepted_at=now if self_declared else None,
    )
    record_event(
        actor=actor,
        property_record=record,
        action=EventAction.MANDATE_CREATED,
        object_type="listing_mandate",
        object_id=mandate.id,
        request=request,
        organization_id=_event_organization(mandate),
        metadata={
            "listing_scope": scope,
            "status": mandate.status,
            "self_declared": self_declared,
            "representative": "person" if representative_user else "organization",
        },
    )
    return mandate


def _same_party(
    principal_user,
    principal_organization,
    representative_user,
    representative_organization,
):
    if principal_user is not None and representative_user is not None:
        return principal_user.id == representative_user.id
    if principal_organization is not None and representative_organization is not None:
        return principal_organization.id == representative_organization.id
    return False


def list_mandates(user):
    ensure_actor(user)
    from django.db.models import Q

    from apps.marketplace.services.access import publish_roles

    publisher_orgs = OrganizationMembershipIds(user, publish_roles())
    return (
        ListingMandate.objects.filter(
            Q(principal_user=user)
            | Q(representative_user=user)
            | Q(granting_user=user)
            | Q(principal_organization_id__in=publisher_orgs)
            | Q(representative_organization_id__in=publisher_orgs)
        )
        .select_related(
            "property",
            "principal_user",
            "principal_organization",
            "representative_user",
            "representative_organization",
        )
        .order_by("-created_at")
    )


def OrganizationMembershipIds(user, roles):
    from apps.organizations.models import OrganizationMembership

    return OrganizationMembership.objects.filter(
        user=user,
        status="active",
        organization__status=OrganizationStatus.ACTIVE,
        role__in=roles,
    ).values("organization_id")


def accept_mandate(*, actor, mandate_id, request=None) -> ListingMandate:
    return _decide_pending(actor, mandate_id, request, accept=True)


def reject_mandate(*, actor, mandate_id, request=None) -> ListingMandate:
    return _decide_pending(actor, mandate_id, request, accept=False)


def _decide_pending(actor, mandate_id, request, *, accept: bool) -> ListingMandate:
    ensure_actor(actor)
    try:
        with transaction.atomic():
            mandate = _lock(mandate_id)
            _expire_if_due(mandate, actor, request)
            if mandate.status != MandateStatus.PENDING_ACCEPTANCE:
                raise MarketplaceError(MANDATE_INVALID, status_code=409)
            require_representative(actor, mandate)
            now = timezone.now()
            if accept:
                mandate.status = MandateStatus.ACTIVE
                mandate.accepted_at = now
                mandate.accepted_by = actor
                action = EventAction.MANDATE_ACCEPTED
            else:
                mandate.status = MandateStatus.REJECTED
                mandate.rejected_at = now
                action = EventAction.MANDATE_REJECTED
            mandate.save()
            record_event(
                actor=actor,
                property_record=mandate.property,
                action=action,
                object_type="listing_mandate",
                object_id=mandate.id,
                request=request,
                organization_id=_event_organization(mandate),
                metadata={"status": mandate.status, "self_declared": mandate.self_declared},
            )
            return mandate
    except OperationalError as exc:
        if is_deadlock(exc):
            raise MarketplaceError(CONFLICT, status_code=409) from exc
        raise


def revoke_mandate(*, actor, mandate_id, request=None) -> ListingMandate:
    ensure_actor(actor)
    try:
        with transaction.atomic():
            mandate = _lock(mandate_id)
            if not _may_revoke(actor, mandate):
                raise MarketplaceError(NOT_FOUND, status_code=404)
            if mandate.status not in {MandateStatus.ACTIVE, MandateStatus.PENDING_ACCEPTANCE}:
                raise MarketplaceError(MANDATE_INVALID, status_code=409)
            now = timezone.now()
            mandate.status = MandateStatus.REVOKED
            mandate.revoked_at = now
            mandate.save(update_fields=["status", "revoked_at", "updated_at"])
            record_event(
                actor=actor,
                property_record=mandate.property,
                action=EventAction.MANDATE_REVOKED,
                object_type="listing_mandate",
                object_id=mandate.id,
                request=request,
                organization_id=_event_organization(mandate),
                metadata={"status": mandate.status},
            )
            from apps.marketplace.services.listings import withdraw_for_mandate

            withdraw_for_mandate(mandate, actor, request, reason="mandate_revoked")
            return mandate
    except OperationalError as exc:
        if is_deadlock(exc):
            raise MarketplaceError(CONFLICT, status_code=409) from exc
        raise


def _may_revoke(actor, mandate) -> bool:
    if actor.id in {
        mandate.granting_user_id,
        mandate.principal_user_id,
        mandate.representative_user_id,
    }:
        return True
    if actor_is_publisher(actor, mandate.representative_organization_id):
        return True
    if actor_is_publisher(actor, mandate.principal_organization_id):
        return True
    try:
        decision = decide(actor, mandate.property_id)
    except PropertyError:
        return False
    return decision.can_update


def expire_mandate_if_due(mandate, actor, request) -> ListingMandate:
    _expire_if_due(mandate, actor, request)
    return mandate


def _expire_if_due(mandate, actor, request) -> None:
    today = timezone.localdate()
    if mandate.status != MandateStatus.ACTIVE or mandate.valid_until is None:
        return
    if mandate.valid_until >= today:
        return
    mandate.status = MandateStatus.EXPIRED
    mandate.save(update_fields=["status", "updated_at"])
    record_event(
        actor=actor,
        property_record=mandate.property,
        action=EventAction.MANDATE_EXPIRED,
        object_type="listing_mandate",
        object_id=mandate.id,
        request=request,
        organization_id=_event_organization(mandate),
        metadata={"status": mandate.status},
    )
    from apps.marketplace.services.listings import withdraw_for_mandate

    withdraw_for_mandate(mandate, actor, request, reason="mandate_expired")


def get_mandate_for_actor(*, actor, mandate_id) -> ListingMandate:
    ensure_actor(actor)
    if not list_mandates(actor).filter(pk=mandate_id).exists():
        raise MarketplaceError(NOT_FOUND, status_code=404)
    return ListingMandate.objects.select_related(
        "property",
        "principal_user",
        "principal_organization",
        "representative_user",
        "representative_organization",
    ).get(pk=mandate_id)
