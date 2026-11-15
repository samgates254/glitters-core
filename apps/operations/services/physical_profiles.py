"""Domain service for physical asset readiness and turnover management."""

from __future__ import annotations

import uuid
from typing import Any

from django.db import OperationalError, transaction
from django.utils import timezone

from apps.organizations.services.authorization import ensure_active_account
from apps.operations.domain import (
    CONFLICT,
    NOT_FOUND,
    PERMISSION_DENIED,
    PHYSICAL_STATE_INVALID,
    OperationsEventAction,
    PhysicalState,
    physical_state_transition_allowed,
)
from apps.operations.errors import OperationsError
from apps.operations.models import ManagementAgreement, UnitPhysicalProfile
from apps.operations.services.agreements import (
    is_agreement_expired,
    sync_agreement_expiry,
    verify_agent_authority,
    verify_owner_authority,
)
from apps.operations.services.audit import record_operations_event
from apps.properties.models import Property
from apps.properties.services.locking import is_deadlock, lock_properties


def get_or_create_physical_profile(property_record: Property) -> UnitPhysicalProfile:
    profile, _ = UnitPhysicalProfile.objects.get_or_create(
        property=property_record,
        defaults={
            "physical_state": PhysicalState.TURNOVER_PREPARATION,
            "target_currency": "KES",
        },
    )
    return profile


def check_operations_authority(actor, property_record: Property) -> None:
    """Verify actor has authority to view or update physical asset operations."""
    ensure_active_account(actor)

    # 1. Check if owner
    try:
        verify_owner_authority(actor, property_record)
        return
    except OperationsError:
        pass

    # 2. Check if managing organization agent via active ManagementAgreement
    active_agreement = ManagementAgreement.objects.filter(
        property=property_record,
        status="active",
    ).select_related("managing_organization").first()

    if active_agreement is not None:
        if is_agreement_expired(active_agreement):
            sync_agreement_expiry(active_agreement)
            active_agreement = None
        else:
            try:
                verify_agent_authority(actor, active_agreement.managing_organization)
                return
            except OperationsError:
                pass

    # 3. Check if property is direct organization-managed
    if property_record.managing_organization is not None:
        try:
            verify_agent_authority(actor, property_record.managing_organization)
            return
        except OperationsError:
            pass

    raise OperationsError(PERMISSION_DENIED, status_code=403)


def update_physical_profile(
    *,
    actor,
    property_id: uuid.UUID | str,
    new_state: str | None = None,
    target_rent_minor: int | None = None,
    target_currency: str | None = None,
    notes: str | None = None,
    request=None,
) -> UnitPhysicalProfile:
    """Update unit physical readiness and turnover state under row-level locking."""
    ensure_active_account(actor)

    try:
        with transaction.atomic():
            locked = lock_properties(property_id)
            property_record = locked.get(property_id)
            if property_record is None:
                try:
                    property_record = Property.objects.select_for_update().get(pk=property_id)
                except Property.DoesNotExist as exc:
                    raise OperationsError(NOT_FOUND, status_code=404) from exc

            check_operations_authority(actor, property_record)

            profile, _ = UnitPhysicalProfile.objects.select_for_update().get_or_create(
                property=property_record,
                defaults={
                    "physical_state": PhysicalState.TURNOVER_PREPARATION,
                    "target_currency": "KES",
                },
            )

            previous_state = profile.physical_state
            changed = False

            if new_state is not None and new_state != previous_state:
                if not physical_state_transition_allowed(previous_state, new_state):
                    raise OperationsError(
                        f"{PHYSICAL_STATE_INVALID} Cannot transition from {previous_state} to {new_state}.",
                        status_code=400,
                    )
                profile.physical_state = new_state
                if new_state == PhysicalState.READY_INSPECTED:
                    profile.ready_at = timezone.now()
                else:
                    profile.ready_at = None
                changed = True

            if target_rent_minor is not None:
                if target_rent_minor < 0:
                    raise OperationsError("Target rent cannot be negative.", status_code=400)
                profile.target_rent_minor = target_rent_minor
                changed = True

            if target_currency is not None:
                cleaned_currency = target_currency.strip().upper()
                if len(cleaned_currency) != 3:
                    raise OperationsError("Target currency must be a 3-letter code.", status_code=400)
                profile.target_currency = cleaned_currency
                changed = True

            if notes is not None:
                profile.notes = notes.strip()
                changed = True

            if changed:
                profile.updated_by = actor
                profile.save()

                record_operations_event(
                    actor=actor,
                    property_record=property_record,
                    action=OperationsEventAction.PHYSICAL_STATE_UPDATED,
                    object_type="unit_physical_profile",
                    object_id=profile.id,
                    request=request,
                    metadata={
                        "previous_state": previous_state,
                        "new_state": profile.physical_state,
                        "target_rent_minor": profile.target_rent_minor,
                        "target_currency": profile.target_currency,
                    },
                )

            return profile
    except OperationalError as exc:
        if is_deadlock(exc):
            raise OperationsError(CONFLICT, status_code=409) from exc
        raise
