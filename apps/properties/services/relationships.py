"""Claimed parties and explicit access grants.

A party role does not verify legal title. An organization party does not
give that organization's members access to the property.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db import IntegrityError, OperationalError, transaction
from django.utils import timezone

from apps.identity.domain import AccountStatus
from apps.organizations.models import Organization
from apps.properties.domain import (
    ACCOUNT_CANNOT_BE_ADDED,
    ACCOUNT_NOT_FOUND,
    CONFLICT,
    LAST_MANAGER,
    NOT_FOUND,
    ORGANIZATION_NOT_FOUND,
    ORGANIZATION_PARTY_NO_ACCESS,
    PARTY_CLOSED,
    PARTY_EXISTS,
    PERMISSION_DENIED,
    STATUS_INVALID,
    VERIFICATION_UNAVAILABLE,
    AccessCapability,
    AccessOrigin,
    AccessStatus,
    EventAction,
    ManagementContext,
    PartyRole,
    PartyStatus,
    PartySubject,
)
from apps.properties.errors import PropertyError
from apps.properties.models import PropertyAccess, PropertyParty
from apps.properties.services.audit import record_event
from apps.properties.services.authorization import decide, require_update
from apps.properties.services.locking import is_deadlock, lock_properties

ACCESS_ALREADY_REVOKED = "This access is already revoked."
PARTY_ALREADY_REVOKED = "This relationship is already revoked."
ARCHIVED = "Archived properties cannot be changed."


def list_relationships(user, property_id) -> dict:
    decision = decide(user, property_id)
    parties = PropertyParty.objects.filter(property_id=decision.record.id).select_related(
        "user",
        "organization",
    )
    if not decision.can_manage_parties:
        parties = parties.filter(
            status__in=[
                PartyStatus.CLAIMED,
                PartyStatus.PENDING_VERIFICATION,
                PartyStatus.VERIFIED,
            ]
        )
    payload = {"parties": parties.order_by("created_at")}
    if decision.can_manage_parties:
        payload["access"] = PropertyAccess.objects.filter(property_id=decision.record.id).order_by(
            "created_at"
        )
    return payload


def add_party(*, actor, property_id, data: dict, request=None) -> PropertyParty:
    decision = decide(actor, property_id)
    require_update(decision)
    try:
        with transaction.atomic():
            record = lock_properties(property_id)[property_id]
            _reject_archived(record)
            party = _insert_party(actor, record, data, request)
            return party
    except OperationalError as exc:
        if is_deadlock(exc):
            raise PropertyError(CONFLICT, status_code=409) from exc
        raise


def update_party(*, actor, property_id, party_id, data: dict, request=None) -> PropertyParty:
    decision = decide(actor, property_id)
    require_update(decision)
    try:
        with transaction.atomic():
            lock_properties(property_id)
            party = _get_party(property_id, party_id)
            if party.status not in {PartyStatus.CLAIMED, PartyStatus.PENDING_VERIFICATION}:
                raise PropertyError(PARTY_CLOSED, status_code=409)
            changed = False
            if "role" in data and data["role"] != party.role:
                if data["role"] not in PartyRole.VALUES:
                    raise PropertyError(STATUS_INVALID, status_code=400)
                party.role = data["role"]
                changed = True
            if "note" in data:
                note = data["note"] if isinstance(data["note"], str) else None
                if note is None or len(note) > 500:
                    raise PropertyError(STATUS_INVALID, status_code=400)
                party.note = note.strip()
                changed = True
            if "status" in data and data["status"] != party.status:
                _apply_party_status(party, data["status"])
                changed = True
            if not changed:
                raise PropertyError("No changes were submitted.", status_code=400)
            try:
                party.save()
            except IntegrityError as exc:
                raise PropertyError(PARTY_EXISTS, status_code=409) from exc
            record_event(
                actor=actor,
                property_record=party.property,
                action=EventAction.PARTICIPANT_UPDATED,
                object_type="property_party",
                object_id=party.id,
                request=request,
                metadata={"role": party.role, "status": party.status},
            )
            return party
    except OperationalError as exc:
        if is_deadlock(exc):
            raise PropertyError(CONFLICT, status_code=409) from exc
        raise


def revoke_party(
    *,
    actor,
    property_id,
    party_id,
    revoke_access: bool,
    request=None,
) -> PropertyParty:
    decision = decide(actor, property_id)
    require_update(decision)
    try:
        with transaction.atomic():
            record = lock_properties(property_id)[property_id]
            party = _get_party(property_id, party_id)
            if party.status == PartyStatus.REVOKED:
                raise PropertyError(PARTY_ALREADY_REVOKED, status_code=409)
            if party.status in {PartyStatus.REJECTED, PartyStatus.VERIFIED}:
                raise PropertyError(PARTY_CLOSED, status_code=409)
            party.status = PartyStatus.REVOKED
            party.revoked_at = timezone.now()
            party.save(update_fields=["status", "revoked_at", "updated_at"])
            record_event(
                actor=actor,
                property_record=record,
                action=EventAction.PARTICIPANT_REVOKED,
                object_type="property_party",
                object_id=party.id,
                request=request,
                metadata={"role": party.role, "status": party.status},
            )
            if revoke_access and party.user_id is not None:
                grant = PropertyAccess.objects.filter(
                    property=record,
                    user_id=party.user_id,
                    status=AccessStatus.ACTIVE,
                ).first()
                if grant is not None:
                    _revoke_grant(actor, record, grant, request)
            return party
    except OperationalError as exc:
        if is_deadlock(exc):
            raise PropertyError(CONFLICT, status_code=409) from exc
        raise


def revoke_access(*, actor, property_id, access_id, request=None) -> PropertyAccess:
    decision = decide(actor, property_id)
    require_update(decision)
    try:
        with transaction.atomic():
            record = lock_properties(property_id)[property_id]
            try:
                grant = PropertyAccess.objects.select_for_update().get(
                    pk=access_id,
                    property=record,
                )
            except PropertyAccess.DoesNotExist as exc:
                raise PropertyError(NOT_FOUND, status_code=404) from exc
            return _revoke_grant(actor, record, grant, request)
    except OperationalError as exc:
        if is_deadlock(exc):
            raise PropertyError(CONFLICT, status_code=409) from exc
        raise


def _insert_party(actor, record, data, request) -> PropertyParty:
    subject_kind = data.get("subject_kind")
    role = data.get("role")
    if subject_kind not in PartySubject.VALUES or role not in PartyRole.VALUES:
        raise PropertyError(STATUS_INVALID, status_code=400)
    note = data.get("note") or ""
    if not isinstance(note, str) or len(note) > 500:
        raise PropertyError(STATUS_INVALID, status_code=400)
    user = None
    organization = None
    if subject_kind == PartySubject.PERSON:
        user = _active_user(data.get("user_id"))
        if data.get("organization_id"):
            raise PropertyError(STATUS_INVALID, status_code=400)
    else:
        organization = _organization(data.get("organization_id"))
        if data.get("user_id") or data.get("access_capability"):
            raise PropertyError(ORGANIZATION_PARTY_NO_ACCESS, status_code=400)
    if _open_party_exists(record, role, user, organization):
        raise PropertyError(PARTY_EXISTS, status_code=409)
    party = PropertyParty.objects.create(
        property=record,
        subject_kind=subject_kind,
        user=user,
        organization=organization,
        role=role,
        status=PartyStatus.CLAIMED,
        note=note.strip(),
        claimed_by=actor,
    )
    record_event(
        actor=actor,
        property_record=record,
        action=EventAction.PARTICIPANT_ADDED,
        object_type="property_party",
        object_id=party.id,
        request=request,
        metadata={
            "role": role,
            "status": PartyStatus.CLAIMED,
            "subject_kind": subject_kind,
            "legal_title_verified": False,
        },
    )
    capability = data.get("access_capability")
    if capability:
        if capability not in AccessCapability.VALUES or user is None:
            raise PropertyError(ORGANIZATION_PARTY_NO_ACCESS, status_code=400)
        _grant_access(actor, record, user, capability, request)
    return party


def _grant_access(actor, record, user, capability, request) -> PropertyAccess:
    existing = PropertyAccess.objects.filter(
        property=record,
        user=user,
        status=AccessStatus.ACTIVE,
    ).first()
    if existing is not None and existing.capability == capability:
        return existing
    if existing is not None:
        _revoke_grant(actor, record, existing, request)
    grant = PropertyAccess.objects.create(
        property=record,
        user=user,
        capability=capability,
        status=AccessStatus.ACTIVE,
        origin=AccessOrigin.DIRECT,
        inherited_from=None,
        granted_by=actor,
    )
    record_event(
        actor=actor,
        property_record=record,
        action=EventAction.ACCESS_GRANTED,
        object_type="property_access",
        object_id=grant.id,
        request=request,
        metadata={"user_id": str(user.id), "capability": capability, "origin": grant.origin},
    )
    return grant


def _revoke_grant(actor, record, grant, request, *, cascade: bool = False) -> PropertyAccess:
    """Revoke one grant. Inherited copies are revoked with the source.

    A direct revoke of the last personal manage grant is refused. Cascade
    skips that guard so a parent revocation is not blocked by the copy on a
    child. The child can be left with no manage grant. Direct grants on a
    descendant are not revoked.
    """

    if grant.status != AccessStatus.ACTIVE:
        raise PropertyError(ACCESS_ALREADY_REVOKED, status_code=409)
    if (
        not cascade
        and record.management_context == ManagementContext.PERSONAL
        and grant.capability == AccessCapability.MANAGE
    ):
        remaining = (
            PropertyAccess.objects.filter(
                property=record,
                status=AccessStatus.ACTIVE,
                capability=AccessCapability.MANAGE,
            )
            .exclude(pk=grant.pk)
            .count()
        )
        if remaining < 1:
            raise PropertyError(LAST_MANAGER, status_code=409)
    grant.status = AccessStatus.REVOKED
    grant.revoked_at = timezone.now()
    grant.save(update_fields=["status", "revoked_at"])
    record_event(
        actor=actor,
        property_record=record,
        action=EventAction.ACCESS_REVOKED,
        object_type="property_access",
        object_id=grant.id,
        request=request,
        metadata={
            "user_id": str(grant.user_id),
            "capability": grant.capability,
            "origin": grant.origin,
            "cascade": cascade,
        },
    )
    _revoke_inherited(actor, grant, request)
    return grant


def _revoke_inherited(actor, grant, request) -> None:
    derived_rows = list(
        PropertyAccess.objects.filter(
            inherited_from=grant,
            status=AccessStatus.ACTIVE,
        )
        .order_by("property_id", "id")
        .values_list("id", "property_id")
    )
    if not derived_rows:
        return
    lock_properties(*(property_id for _grant_id, property_id in derived_rows))
    derived = list(
        PropertyAccess.objects.select_for_update(of=("self",))
        .filter(inherited_from=grant, status=AccessStatus.ACTIVE)
        .select_related("property")
        .order_by("property_id", "id")
    )
    for child in derived:
        _revoke_grant(actor, child.property, child, request, cascade=True)


def _apply_party_status(party, new_status: str) -> None:
    if new_status == PartyStatus.VERIFIED:
        raise PropertyError(VERIFICATION_UNAVAILABLE, status_code=400)
    if new_status == PartyStatus.PENDING_VERIFICATION and party.status == PartyStatus.CLAIMED:
        party.status = PartyStatus.PENDING_VERIFICATION
        return
    if new_status == PartyStatus.REJECTED and party.status in {
        PartyStatus.CLAIMED,
        PartyStatus.PENDING_VERIFICATION,
    }:
        party.status = PartyStatus.REJECTED
        return
    raise PropertyError(STATUS_INVALID, status_code=400)


def _open_party_exists(record, role, user, organization) -> bool:
    queryset = PropertyParty.objects.filter(
        property=record,
        role=role,
        status__in=sorted(PartyStatus.OPEN),
    )
    if user is not None:
        queryset = queryset.filter(user=user)
    else:
        queryset = queryset.filter(organization=organization)
    return queryset.exists()


def _active_user(user_id):
    if not user_id:
        raise PropertyError(ACCOUNT_NOT_FOUND, status_code=400)
    user_model = get_user_model()
    try:
        user = user_model.objects.get(pk=user_id)
    except user_model.DoesNotExist as exc:
        raise PropertyError(ACCOUNT_NOT_FOUND, status_code=400) from exc
    if user.status != AccountStatus.ACTIVE:
        raise PropertyError(ACCOUNT_CANNOT_BE_ADDED, status_code=400)
    return user


def _organization(organization_id):
    if not organization_id:
        raise PropertyError(ORGANIZATION_NOT_FOUND, status_code=400)
    try:
        return Organization.objects.get(pk=organization_id)
    except Organization.DoesNotExist as exc:
        raise PropertyError(ORGANIZATION_NOT_FOUND, status_code=400) from exc


def _get_party(property_id, party_id) -> PropertyParty:
    try:
        return (
            PropertyParty.objects.select_for_update(of=("self",))
            .select_related(
                "property",
                "user",
                "organization",
            )
            .get(pk=party_id, property_id=property_id)
        )
    except PropertyParty.DoesNotExist as exc:
        raise PropertyError(NOT_FOUND, status_code=404) from exc


def _reject_archived(record) -> None:
    if record.status == "archived":
        raise PropertyError(ARCHIVED, status_code=409)


def assert_can_manage(decision) -> None:
    if not decision.can_manage_parties:
        raise PropertyError(PERMISSION_DENIED, status_code=403)
