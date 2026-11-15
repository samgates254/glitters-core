"""Unit tests for operations domain rules, transitions, and audit event immutability."""

from __future__ import annotations

import pytest
from django.utils import timezone
from tests.organizations_support import verified_user
from tests.properties_support import property_body

from apps.operations.domain import (
    AgreementStatus,
    OperationsEventAction,
    PhysicalState,
    agreement_transition_allowed,
    clean_metadata,
    compute_idempotency_key,
    generate_agreement_reference,
    physical_state_transition_allowed,
)
from apps.operations.models import OperationsEvent
from apps.properties.services.records import create_property

pytestmark = pytest.mark.django_db


def test_agreement_reference_format() -> None:
    ref = generate_agreement_reference()
    assert ref.startswith("PMA-")
    assert len(ref) == 12
    # Ensure distinct random references
    ref2 = generate_agreement_reference()
    assert ref != ref2


def test_agreement_status_transitions() -> None:
    # Draft transitions
    assert agreement_transition_allowed(AgreementStatus.DRAFT, AgreementStatus.PENDING_OWNER_ACCEPTANCE)
    assert agreement_transition_allowed(AgreementStatus.DRAFT, AgreementStatus.PENDING_AGENT_ACCEPTANCE)
    assert agreement_transition_allowed(AgreementStatus.DRAFT, AgreementStatus.REVOKED)
    assert not agreement_transition_allowed(AgreementStatus.DRAFT, AgreementStatus.ACTIVE)

    # Pending transitions
    assert agreement_transition_allowed(AgreementStatus.PENDING_OWNER_ACCEPTANCE, AgreementStatus.ACCEPTED)
    assert agreement_transition_allowed(AgreementStatus.PENDING_OWNER_ACCEPTANCE, AgreementStatus.ACTIVE)
    assert agreement_transition_allowed(AgreementStatus.PENDING_OWNER_ACCEPTANCE, AgreementStatus.REJECTED)
    assert agreement_transition_allowed(AgreementStatus.PENDING_OWNER_ACCEPTANCE, AgreementStatus.REVOKED)

    # Accepted transitions
    assert agreement_transition_allowed(AgreementStatus.ACCEPTED, AgreementStatus.ACTIVE)
    assert agreement_transition_allowed(AgreementStatus.ACCEPTED, AgreementStatus.TERMINATED)

    # Active transitions
    assert agreement_transition_allowed(AgreementStatus.ACTIVE, AgreementStatus.TERMINATED)
    assert agreement_transition_allowed(AgreementStatus.ACTIVE, AgreementStatus.EXPIRED)
    assert not agreement_transition_allowed(AgreementStatus.ACTIVE, AgreementStatus.DRAFT)
    assert not agreement_transition_allowed(AgreementStatus.ACTIVE, AgreementStatus.REJECTED)

    # Terminal states
    assert not agreement_transition_allowed(AgreementStatus.TERMINATED, AgreementStatus.ACTIVE)
    assert not agreement_transition_allowed(AgreementStatus.REJECTED, AgreementStatus.ACTIVE)
    assert not agreement_transition_allowed(AgreementStatus.REVOKED, AgreementStatus.ACTIVE)


def test_physical_state_transitions() -> None:
    # Turnover preparation transitions
    assert physical_state_transition_allowed(PhysicalState.TURNOVER_PREPARATION, PhysicalState.READY_INSPECTED)
    assert physical_state_transition_allowed(PhysicalState.TURNOVER_PREPARATION, PhysicalState.REPAIR_HOLD)
    assert physical_state_transition_allowed(PhysicalState.TURNOVER_PREPARATION, PhysicalState.STRUCTURAL_OFFLINE)
    assert physical_state_transition_allowed(PhysicalState.TURNOVER_PREPARATION, PhysicalState.OWNER_RESERVED)

    # Ready inspected transitions
    assert physical_state_transition_allowed(PhysicalState.READY_INSPECTED, PhysicalState.TURNOVER_PREPARATION)
    assert physical_state_transition_allowed(PhysicalState.READY_INSPECTED, PhysicalState.REPAIR_HOLD)

    # Structural offline requires repair hold or turnover first
    assert physical_state_transition_allowed(PhysicalState.STRUCTURAL_OFFLINE, PhysicalState.REPAIR_HOLD)
    assert not physical_state_transition_allowed(PhysicalState.STRUCTURAL_OFFLINE, PhysicalState.READY_INSPECTED)

    # Same state is always allowed (no-op)
    assert physical_state_transition_allowed(PhysicalState.READY_INSPECTED, PhysicalState.READY_INSPECTED)


def test_idempotency_key_deterministic_and_unique() -> None:
    key1 = compute_idempotency_key("agreement.accepted", "agreement", "123", "req-1")
    key2 = compute_idempotency_key("agreement.accepted", "agreement", "123", "req-1")
    key3 = compute_idempotency_key("agreement.accepted", "agreement", "123", "req-2")

    assert key1 == key2
    assert key1 != key3
    assert len(key1) == 64


def test_metadata_drops_sensitive_keys() -> None:
    data = {
        "reference": "PMA-001",
        "notes": "Standard terms",
        "token": "secret_token_123",
        "auth_password": "my_password",
        "nested": {
            "key": "value",
            "secret": "hidden",
        },
    }
    cleaned = clean_metadata(data)
    assert cleaned["reference"] == "PMA-001"
    assert cleaned["notes"] == "Standard terms"
    assert "token" not in cleaned
    assert "auth_password" not in cleaned
    assert "secret" not in cleaned["nested"]
    assert cleaned["nested"]["key"] == "value"


def test_operations_event_is_append_only() -> None:
    user = verified_user("owner.audit@example.com", "Password-123!")
    prop = create_property(actor=user, data=property_body(name="Audit Property"))

    event = OperationsEvent.objects.create(
        idempotency_key="key-001",
        actor=user,
        property=prop,
        action=OperationsEventAction.AGREEMENT_DRAFTED,
        object_type="management_agreement",
        object_id="obj-001",
        metadata={"test": "data"},
    )
    assert event.id is not None

    # Mutation via save() on existing record must fail
    event.action = OperationsEventAction.AGREEMENT_ACTIVATED
    with pytest.raises(ValueError, match="Operations events are append-only"):
        event.save()

    # Mutation via QuerySet.update() must fail
    with pytest.raises(ValueError, match="Operations events are append-only"):
        OperationsEvent.objects.filter(id=event.id).update(action="hacked")

    # Deletion via delete() must fail
    with pytest.raises(ValueError, match="Operations events are append-only"):
        event.delete()

    # Deletion via QuerySet.delete() must fail
    with pytest.raises(ValueError, match="Operations events are append-only"):
        OperationsEvent.objects.filter(id=event.id).delete()
