"""Unit tests for tenancy domain choices, reference generators, and physical state transitions."""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone
from tests.organizations_support import verified_user
from tests.properties_support import property_body

from apps.operations.domain import (
    PhysicalState,
    physical_state_transition_allowed,
)
from apps.operations.models import ManagementAgreement, UnitPhysicalProfile
from apps.organizations.models import Organization
from apps.properties.domain import HierarchyRole
from apps.properties.services.records import create_property
from apps.tenancy.domain import (
    INVALID_UNIT,
    AmendmentStatus,
    AmendmentType,
    ApplicationStatus,
    DeductionDisputeStatus,
    DeductionType,
    DepositStatus,
    EmploymentStatus,
    HandoverStatus,
    HandoverType,
    IdDocumentType,
    LeasePartyRole,
    LeaseStatus,
    LeaseType,
    PartySignatureStatus,
    ScheduleStatus,
    SignatureMethod,
    TenancyEventAction,
    generate_amendment_reference,
    generate_application_reference,
    generate_lease_reference,
)
from apps.tenancy.models import (
    Lease,
    LeaseParty,
    RentalApplication,
    TenancyEvent,
)

pytestmark = pytest.mark.django_db


def test_reference_generators() -> None:
    lease_ref = generate_lease_reference()
    assert lease_ref.startswith("LSE-")
    assert len(lease_ref) == 16
    assert lease_ref != generate_lease_reference()

    app_ref = generate_application_reference()
    assert app_ref.startswith("APP-")
    assert len(app_ref) == 16
    assert app_ref != generate_application_reference()

    amd_ref = generate_amendment_reference()
    assert amd_ref.startswith("AMD-")
    assert len(amd_ref) == 16
    assert amd_ref != generate_amendment_reference()


def test_domain_constants_and_subsets() -> None:
    assert LeaseStatus.PENDING_SIGNATURE in LeaseStatus.OCCUPANCY_RESERVING
    assert LeaseStatus.FULLY_EXECUTED in LeaseStatus.OCCUPANCY_RESERVING
    assert LeaseStatus.ACTIVE in LeaseStatus.OCCUPANCY_RESERVING
    assert LeaseStatus.UNDER_RENEWAL in LeaseStatus.OCCUPANCY_RESERVING
    assert LeaseStatus.DRAFT not in LeaseStatus.OCCUPANCY_RESERVING
    assert LeaseStatus.CANCELLED not in LeaseStatus.OCCUPANCY_RESERVING
    assert LeaseStatus.EXPIRED_UNSIGNED not in LeaseStatus.OCCUPANCY_RESERVING
    assert LeaseStatus.TERMINATED_NORMAL in LeaseStatus.OCCUPANCY_RESERVING
    assert LeaseStatus.TERMINATED_EARLY in LeaseStatus.OCCUPANCY_RESERVING

    assert LeasePartyRole.PRIMARY_TENANT in LeasePartyRole.SIGNATORY_ROLES
    assert LeasePartyRole.CO_TENANT in LeasePartyRole.SIGNATORY_ROLES
    assert LeasePartyRole.GUARANTOR in LeasePartyRole.SIGNATORY_ROLES
    assert LeasePartyRole.LANDLORD in LeasePartyRole.SIGNATORY_ROLES
    assert LeasePartyRole.MANAGING_AGENT in LeasePartyRole.SIGNATORY_ROLES
    assert LeasePartyRole.AUTHORIZED_OCCUPANT not in LeasePartyRole.SIGNATORY_ROLES


def test_physical_occupancy_state_transitions() -> None:
    # Ready / Inspected -> Occupied (Normal) upon Move-in Handover
    assert physical_state_transition_allowed(PhysicalState.READY_INSPECTED, PhysicalState.OCCUPIED_NORMAL)
    assert physical_state_transition_allowed(PhysicalState.READY_INSPECTED, PhysicalState.TURNOVER_PREPARATION)
    assert physical_state_transition_allowed(PhysicalState.READY_INSPECTED, PhysicalState.REPAIR_HOLD)

    # Occupied (Normal) -> Turnover Preparation (Move-out) or Occupied (Holdover)
    assert physical_state_transition_allowed(PhysicalState.OCCUPIED_NORMAL, PhysicalState.TURNOVER_PREPARATION)
    assert physical_state_transition_allowed(PhysicalState.OCCUPIED_NORMAL, PhysicalState.OCCUPIED_HOLDOVER)
    assert physical_state_transition_allowed(PhysicalState.OCCUPIED_NORMAL, PhysicalState.REPAIR_HOLD)

    # Occupied (Holdover) -> Turnover Preparation (eviction/vacated) or Repair Hold
    assert physical_state_transition_allowed(PhysicalState.OCCUPIED_HOLDOVER, PhysicalState.TURNOVER_PREPARATION)
    assert physical_state_transition_allowed(PhysicalState.OCCUPIED_HOLDOVER, PhysicalState.REPAIR_HOLD)
    assert physical_state_transition_allowed(PhysicalState.OCCUPIED_HOLDOVER, PhysicalState.OCCUPIED_NORMAL)

    # Invalid transitions
    assert not physical_state_transition_allowed(PhysicalState.OCCUPIED_HOLDOVER, PhysicalState.READY_INSPECTED)
    assert not physical_state_transition_allowed(PhysicalState.STRUCTURAL_OFFLINE, PhysicalState.OCCUPIED_NORMAL)


def test_management_agreement_can_execute_leases_field() -> None:
    user = verified_user("mgt-user@example.com", "Password-123-Safe")
    org = Organization.objects.create(
        legal_name="Apex PM Firm Limited",
        display_name="Apex PM Firm",
        slug="apex-pm",
        organization_type="agency",
    )
    prop = create_property(actor=user, data=property_body(name="Villa Rosa", hierarchy_role="standalone"))

    agreement = ManagementAgreement.objects.create(
        property=prop,
        managing_organization=org,
        granting_user=user,
        valid_from=timezone.localdate(),
        created_by=user,
    )
    # Default is False
    assert agreement.can_execute_leases is False

    agreement.can_execute_leases = True
    agreement.save(update_fields=["can_execute_leases"])
    agreement.refresh_from_db()
    assert agreement.can_execute_leases is True


def test_lease_property_unit_validation() -> None:
    user = verified_user("owner-user@example.com", "Password-123-Safe")
    building = create_property(actor=user, data=property_body(name="Kilimani Tower", hierarchy_role="building"))
    unit = create_property(actor=user, data=property_body(name="Unit 1A", hierarchy_role="unit", parent_id=str(building.id)))

    # Building is not a leasable unit
    invalid_lease = Lease(
        property=building,
        property_owner=user,
        start_date=timezone.localdate(),
        term_end_date=timezone.localdate() + timezone.timedelta(days=365),
        effective_end_date=timezone.localdate() + timezone.timedelta(days=366),
        rent_amount_minor=50_000_00,
        created_by=user,
    )
    with pytest.raises(ValidationError) as exc:
        invalid_lease.clean()
    assert "property" in exc.value.message_dict
    assert INVALID_UNIT in str(exc.value.message_dict["property"])

    # Unit is valid
    valid_lease = Lease(
        property=unit,
        property_owner=user,
        start_date=timezone.localdate(),
        term_end_date=timezone.localdate() + timezone.timedelta(days=365),
        effective_end_date=timezone.localdate() + timezone.timedelta(days=366),
        rent_amount_minor=50_000_00,
        created_by=user,
    )
    # clean() should not raise
    valid_lease.clean()


def test_tenancy_event_append_only_immutability() -> None:
    user = verified_user("audit-user@example.com", "Password-123-Safe")
    event = TenancyEvent.objects.create(
        idempotency_key="idemp-test-001",
        actor=user,
        action=TenancyEventAction.LEASE_DRAFTED,
        object_type="lease",
        object_id=str(user.id),
        metadata={"step": "drafting"},
    )
    assert event.id is not None

    # .save() on existing instance raises ValueError
    event.metadata = {"step": "modified"}
    with pytest.raises(ValueError, match="Tenancy events are append-only"):
        event.save()

    # .delete() on instance raises ValueError
    with pytest.raises(ValueError, match="Tenancy events are append-only"):
        event.delete()

    # QuerySet .update() raises ValueError
    with pytest.raises(ValueError, match="Tenancy events are append-only"):
        TenancyEvent.objects.filter(id=event.id).update(action=TenancyEventAction.LEASE_ACTIVATED)

    # QuerySet .bulk_update() raises ValueError
    with pytest.raises(ValueError, match="Tenancy events are append-only"):
        TenancyEvent.objects.bulk_update([event], ["metadata"])

    # QuerySet .delete() raises ValueError
    with pytest.raises(ValueError, match="Tenancy events are append-only"):
        TenancyEvent.objects.filter(id=event.id).delete()
