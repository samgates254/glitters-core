"""Integration tests verifying PostgreSQL GiST exclusion constraints, model constraints, and relationships in apps.tenancy."""

from __future__ import annotations

import datetime
import pytest
from django.db import IntegrityError, transaction
from tests.organizations_support import verified_user
from tests.properties_support import property_body

from apps.organizations.models import Organization
from apps.properties.services.records import create_property
from apps.tenancy.domain import (
    AmendmentType,
    DeductionType,
    HandoverType,
    IdDocumentType,
    LeasePartyRole,
    LeaseStatus,
    LeaseType,
    SignatureMethod,
)
from apps.tenancy.models import (
    DepositDeduction,
    DepositObligation,
    HandoverProtocol,
    Lease,
    LeaseAmendment,
    LeaseParty,
    LeaseSignatureEvidence,
    RentalApplication,
    RentScheduleEntry,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def test_setup():
    pw = "Secret-P@ssword-42!"
    landlord = verified_user("landlord@example.com", pw)
    tenant1 = verified_user("tenant1@example.com", pw)
    tenant2 = verified_user("tenant2@example.com", pw)
    agent = verified_user("agent@example.com", pw)
    org = Organization.objects.create(
        legal_name="Prime Properties Limited",
        display_name="Prime Properties",
        slug="prime-pm",
        organization_type="agency",
    )

    building = create_property(actor=landlord, data=property_body(name="Sunrise Court", hierarchy_role="building"))
    unit101 = create_property(actor=landlord, data=property_body(name="Unit 101", hierarchy_role="unit", parent_id=str(building.id)))
    unit102 = create_property(actor=landlord, data=property_body(name="Unit 102", hierarchy_role="unit", parent_id=str(building.id)))

    return {
        "landlord": landlord,
        "tenant1": tenant1,
        "tenant2": tenant2,
        "agent": agent,
        "org": org,
        "unit101": unit101,
        "unit102": unit102,
    }


def test_gist_exclusion_same_day_turnover_success(test_setup) -> None:
    """Verifies that [2026-01-01, 2026-01-31) and [2026-01-31, 2026-02-28) on same unit do NOT conflict."""
    unit = test_setup["unit101"]
    landlord = test_setup["landlord"]

    lease1 = Lease.objects.create(
        property=unit,
        property_owner=landlord,
        start_date=datetime.date(2026, 1, 1),
        term_end_date=datetime.date(2026, 1, 30),
        effective_end_date=datetime.date(2026, 1, 31),
        rent_amount_minor=50_000_00,
        status=LeaseStatus.ACTIVE,
        created_by=landlord,
    )
    assert lease1.id is not None

    # Same-day turnover lease starting Jan 31
    lease2 = Lease.objects.create(
        property=unit,
        property_owner=landlord,
        start_date=datetime.date(2026, 1, 31),
        term_end_date=datetime.date(2026, 2, 27),
        effective_end_date=datetime.date(2026, 2, 28),
        rent_amount_minor=52_000_00,
        status=LeaseStatus.PENDING_SIGNATURE,
        created_by=landlord,
    )
    assert lease2.id is not None


def test_gist_exclusion_overlap_rejection(test_setup) -> None:
    """Verifies that overlapping occupancy-reserving date ranges on the same unit throw IntegrityError."""
    unit = test_setup["unit101"]
    landlord = test_setup["landlord"]

    Lease.objects.create(
        property=unit,
        property_owner=landlord,
        start_date=datetime.date(2026, 1, 1),
        term_end_date=datetime.date(2026, 1, 31),
        effective_end_date=datetime.date(2026, 2, 1),
        rent_amount_minor=50_000_00,
        status=LeaseStatus.ACTIVE,
        created_by=landlord,
    )

    # Attempt overlapping lease [2026-01-15, 2026-02-15) on same unit
    with pytest.raises(IntegrityError, match="tenancy_lease_exclude_overlapping_contractual_leases"):
        with transaction.atomic():
            Lease.objects.create(
                property=unit,
                property_owner=landlord,
                start_date=datetime.date(2026, 1, 15),
                term_end_date=datetime.date(2026, 2, 14),
                effective_end_date=datetime.date(2026, 2, 15),
                rent_amount_minor=55_000_00,
                status=LeaseStatus.PENDING_SIGNATURE,
                created_by=landlord,
            )


def test_gist_exclusion_different_properties_do_not_conflict(test_setup) -> None:
    """Verifies that leases with identical dates on different units do NOT conflict."""
    unit101 = test_setup["unit101"]
    unit102 = test_setup["unit102"]
    landlord = test_setup["landlord"]

    lease1 = Lease.objects.create(
        property=unit101,
        property_owner=landlord,
        start_date=datetime.date(2026, 1, 1),
        term_end_date=datetime.date(2026, 12, 31),
        effective_end_date=datetime.date(2027, 1, 1),
        rent_amount_minor=50_000_00,
        status=LeaseStatus.ACTIVE,
        created_by=landlord,
    )
    lease2 = Lease.objects.create(
        property=unit102,
        property_owner=landlord,
        start_date=datetime.date(2026, 1, 1),
        term_end_date=datetime.date(2026, 12, 31),
        effective_end_date=datetime.date(2027, 1, 1),
        rent_amount_minor=50_000_00,
        status=LeaseStatus.ACTIVE,
        created_by=landlord,
    )
    assert lease1.id != lease2.id


def test_gist_exclusion_open_ended_periodic_lease(test_setup) -> None:
    """Verifies that an open-ended periodic lease [start, NULL) blocks future overlapping leases."""
    unit = test_setup["unit101"]
    landlord = test_setup["landlord"]

    # Open-ended lease: effective_end_date is None -> [2026-01-01, infinity)
    Lease.objects.create(
        property=unit,
        property_owner=landlord,
        lease_type=LeaseType.RESIDENTIAL_PERIODIC,
        start_date=datetime.date(2026, 1, 1),
        term_end_date=None,
        effective_end_date=None,
        rent_amount_minor=45_000_00,
        status=LeaseStatus.ACTIVE,
        created_by=landlord,
    )

    # Attempting to pre-lease starting June 2026 must be blocked
    with pytest.raises(IntegrityError, match="tenancy_lease_exclude_overlapping_contractual_leases"):
        with transaction.atomic():
            Lease.objects.create(
                property=unit,
                property_owner=landlord,
                start_date=datetime.date(2026, 6, 1),
                term_end_date=datetime.date(2027, 5, 31),
                effective_end_date=datetime.date(2027, 6, 1),
                rent_amount_minor=50_000_00,
                status=LeaseStatus.PENDING_SIGNATURE,
                created_by=landlord,
            )


def test_gist_exclusion_consecutive_renewal_replacement_leases(test_setup) -> None:
    """Verifies that an incumbent lease in UNDER_RENEWAL and a replacement lease in PENDING_SIGNATURE coexist."""
    unit = test_setup["unit101"]
    landlord = test_setup["landlord"]

    lease_a = Lease.objects.create(
        property=unit,
        property_owner=landlord,
        start_date=datetime.date(2026, 1, 1),
        term_end_date=datetime.date(2026, 12, 31),
        effective_end_date=datetime.date(2027, 1, 1),
        rent_amount_minor=50_000_00,
        status=LeaseStatus.UNDER_RENEWAL,
        created_by=landlord,
    )

    # Consecutive replacement lease B starting on Jan 1 2027
    lease_b = Lease.objects.create(
        property=unit,
        property_owner=landlord,
        supersedes_lease=lease_a,
        start_date=datetime.date(2027, 1, 1),
        term_end_date=datetime.date(2027, 12, 31),
        effective_end_date=datetime.date(2028, 1, 1),
        rent_amount_minor=55_000_00,
        status=LeaseStatus.PENDING_SIGNATURE,
        created_by=landlord,
    )
    assert lease_b.id is not None
    assert lease_b.supersedes_lease_id == lease_a.id


def test_gist_exclusion_non_reserving_statuses_do_not_conflict(test_setup) -> None:
    """Verifies that DRAFT, CANCELLED, and TERMINATED leases do NOT lock the GiST exclusion constraint."""
    unit = test_setup["unit101"]
    landlord = test_setup["landlord"]

    # Active lease
    Lease.objects.create(
        property=unit,
        property_owner=landlord,
        start_date=datetime.date(2026, 1, 1),
        term_end_date=datetime.date(2026, 12, 31),
        effective_end_date=datetime.date(2027, 1, 1),
        rent_amount_minor=50_000_00,
        status=LeaseStatus.ACTIVE,
        created_by=landlord,
    )

    # Draft lease overlapping the same dates is permitted (not reserving)
    draft_lease = Lease.objects.create(
        property=unit,
        property_owner=landlord,
        start_date=datetime.date(2026, 1, 1),
        term_end_date=datetime.date(2026, 12, 31),
        effective_end_date=datetime.date(2027, 1, 1),
        rent_amount_minor=55_000_00,
        status=LeaseStatus.DRAFT,
        created_by=landlord,
    )
    assert draft_lease.id is not None

    # Cancelled lease overlapping is permitted
    cancelled_lease = Lease.objects.create(
        property=unit,
        property_owner=landlord,
        start_date=datetime.date(2026, 6, 1),
        term_end_date=datetime.date(2026, 12, 31),
        effective_end_date=datetime.date(2027, 1, 1),
        rent_amount_minor=52_000_00,
        status=LeaseStatus.CANCELLED,
        created_by=landlord,
    )
    assert cancelled_lease.id is not None


def test_lease_check_constraints(test_setup) -> None:
    unit = test_setup["unit101"]
    landlord = test_setup["landlord"]

    # 1. effective_end_date <= start_date
    with pytest.raises(IntegrityError, match="tenancy_lease_effective_end_after_start"):
        with transaction.atomic():
            Lease.objects.create(
                property=unit,
                property_owner=landlord,
                start_date=datetime.date(2026, 5, 1),
                effective_end_date=datetime.date(2026, 5, 1),
                rent_amount_minor=50_000_00,
                created_by=landlord,
            )

    # 2. rent_amount_minor <= 0
    with pytest.raises(IntegrityError, match="tenancy_lease_rent_positive"):
        with transaction.atomic():
            Lease.objects.create(
                property=unit,
                property_owner=landlord,
                start_date=datetime.date(2026, 5, 1),
                effective_end_date=datetime.date(2026, 6, 1),
                rent_amount_minor=0,
                created_by=landlord,
            )

    # 3. payment_due_day out of range (1-28)
    with pytest.raises(IntegrityError, match="tenancy_lease_payment_due_day_range"):
        with transaction.atomic():
            Lease.objects.create(
                property=unit,
                property_owner=landlord,
                start_date=datetime.date(2026, 5, 1),
                effective_end_date=datetime.date(2026, 6, 1),
                rent_amount_minor=50_000_00,
                payment_due_day=29,
                created_by=landlord,
            )

    # 4. grace_period_days > 30
    with pytest.raises(IntegrityError, match="tenancy_lease_grace_period_range"):
        with transaction.atomic():
            Lease.objects.create(
                property=unit,
                property_owner=landlord,
                start_date=datetime.date(2026, 5, 1),
                effective_end_date=datetime.date(2026, 6, 1),
                rent_amount_minor=50_000_00,
                grace_period_days=31,
                created_by=landlord,
            )


def test_lease_party_constraints(test_setup) -> None:
    unit = test_setup["unit101"]
    landlord = test_setup["landlord"]
    tenant = test_setup["tenant1"]

    lease = Lease.objects.create(
        property=unit,
        property_owner=landlord,
        start_date=datetime.date(2026, 1, 1),
        effective_end_date=datetime.date(2027, 1, 1),
        rent_amount_minor=50_000_00,
        created_by=landlord,
    )

    # Authorized occupant CANNOT have signature authority
    with pytest.raises(IntegrityError, match="tenancy_party_occupant_no_sig_auth"):
        with transaction.atomic():
            LeaseParty.objects.create(
                lease=lease,
                user=tenant,
                party_role=LeasePartyRole.AUTHORIZED_OCCUPANT,
                has_signature_authority=True,
            )

    # Authorized occupant with has_signature_authority=False succeeds
    occupant = LeaseParty.objects.create(
        lease=lease,
        user=tenant,
        party_role=LeasePartyRole.AUTHORIZED_OCCUPANT,
        has_signature_authority=False,
    )
    assert occupant.id is not None

    # Duplicate party role for same user on same lease fails unique constraint
    with pytest.raises(IntegrityError, match="tenancy_lease_party_unique_role"):
        with transaction.atomic():
            LeaseParty.objects.create(
                lease=lease,
                user=tenant,
                party_role=LeasePartyRole.AUTHORIZED_OCCUPANT,
                has_signature_authority=False,
            )


def test_lease_signature_evidence_dual_hashing(test_setup) -> None:
    unit = test_setup["unit101"]
    landlord = test_setup["landlord"]
    tenant = test_setup["tenant1"]

    lease = Lease.objects.create(
        property=unit,
        property_owner=landlord,
        start_date=datetime.date(2026, 1, 1),
        effective_end_date=datetime.date(2027, 1, 1),
        rent_amount_minor=50_000_00,
        contract_data_hash="a" * 64,
        rendered_document_hash="b" * 64,
        rendered_document_uri="docs/contracts/2026/LSE-001.pdf",
        created_by=landlord,
    )
    party = LeaseParty.objects.create(
        lease=lease,
        user=tenant,
        party_role=LeasePartyRole.PRIMARY_TENANT,
        has_signature_authority=True,
    )

    evidence = LeaseSignatureEvidence.objects.create(
        idempotency_key="sig-evidence-key-001",
        lease=lease,
        party=party,
        signatory_user=tenant,
        signatory_role=LeasePartyRole.PRIMARY_TENANT,
        document_version=1,
        contract_data_hash=lease.contract_data_hash,
        rendered_document_hash=lease.rendered_document_hash,
        rendered_document_uri=lease.rendered_document_uri,
        signature_method=SignatureMethod.OTP_SMS_CONFIRMED,
        ip_address="197.232.1.42",
        user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)",
    )
    assert evidence.id is not None
    assert evidence.contract_data_hash == "a" * 64
    assert evidence.rendered_document_hash == "b" * 64


def test_rent_schedule_entry_constraints(test_setup) -> None:
    unit = test_setup["unit101"]
    landlord = test_setup["landlord"]

    lease = Lease.objects.create(
        property=unit,
        property_owner=landlord,
        start_date=datetime.date(2026, 1, 1),
        effective_end_date=datetime.date(2027, 1, 1),
        rent_amount_minor=50_000_00,
        service_charge_minor=5_000_00,
        created_by=landlord,
    )

    # Valid schedule entry: total = base (50k) + service (5k) = 55k
    sched1 = RentScheduleEntry.objects.create(
        lease=lease,
        sequence_number=1,
        period_start=datetime.date(2026, 1, 1),
        period_end=datetime.date(2026, 1, 31),
        due_date=datetime.date(2026, 1, 1),
        base_rent_minor=50_000_00,
        service_charge_minor=5_000_00,
        total_due_minor=55_000_00,
    )
    assert sched1.id is not None

    # Total due minor does not match sum
    with pytest.raises(IntegrityError, match="tenancy_rent_schedule_total_matches"):
        with transaction.atomic():
            RentScheduleEntry.objects.create(
                lease=lease,
                sequence_number=2,
                period_start=datetime.date(2026, 2, 1),
                period_end=datetime.date(2026, 2, 28),
                due_date=datetime.date(2026, 2, 1),
                base_rent_minor=50_000_00,
                service_charge_minor=5_000_00,
                total_due_minor=60_000_00,  # Incorrect
            )

    # Period end before period start
    with pytest.raises(IntegrityError, match="tenancy_rent_schedule_period_valid"):
        with transaction.atomic():
            RentScheduleEntry.objects.create(
                lease=lease,
                sequence_number=2,
                period_start=datetime.date(2026, 2, 15),
                period_end=datetime.date(2026, 2, 10),
                due_date=datetime.date(2026, 2, 1),
                base_rent_minor=50_000_00,
                service_charge_minor=5_000_00,
                total_due_minor=55_000_00,
            )


def test_deposit_obligation_and_deductions(test_setup) -> None:
    unit = test_setup["unit101"]
    landlord = test_setup["landlord"]
    tenant = test_setup["tenant1"]

    lease = Lease.objects.create(
        property=unit,
        property_owner=landlord,
        start_date=datetime.date(2026, 1, 1),
        effective_end_date=datetime.date(2027, 1, 1),
        rent_amount_minor=50_000_00,
        deposit_amount_minor=50_000_00,
        created_by=landlord,
    )

    deposit = DepositObligation.objects.create(
        lease=lease,
        tenant_user=tenant,
        required_amount_minor=50_000_00,
        due_date=datetime.date(2026, 1, 1),
    )
    assert deposit.id is not None

    deduction = DepositDeduction.objects.create(
        deposit_obligation=deposit,
        deduction_type=DeductionType.DAMAGE_REPAIR,
        description="Drywall repair and repaint in living room.",
        amount_minor=12_000_00,
        created_by=landlord,
    )
    assert deduction.id is not None
    assert deduction.amount_minor == 12_000_00


def test_handover_protocol_uniqueness(test_setup) -> None:
    unit = test_setup["unit101"]
    landlord = test_setup["landlord"]

    lease = Lease.objects.create(
        property=unit,
        property_owner=landlord,
        start_date=datetime.date(2026, 1, 1),
        effective_end_date=datetime.date(2027, 1, 1),
        rent_amount_minor=50_000_00,
        created_by=landlord,
    )

    p1 = HandoverProtocol.objects.create(
        lease=lease,
        protocol_type=HandoverType.MOVE_IN,
        handover_date=datetime.date(2026, 1, 1),
        keys_handed_over_count=3,
        physical_condition_summary="Turnover inspection certified. Pristine condition.",
        meter_readings_snapshot={"water_m3": 412.5, "electricity_kwh": 1050.2},
        created_by=landlord,
    )
    assert p1.id is not None

    # Cannot have second MOVE_IN handover protocol for same lease
    with pytest.raises(IntegrityError, match="tenancy_handover_one_per_type"):
        with transaction.atomic():
            HandoverProtocol.objects.create(
                lease=lease,
                protocol_type=HandoverType.MOVE_IN,
                handover_date=datetime.date(2026, 1, 2),
                physical_condition_summary="Duplicate attempt",
                created_by=landlord,
            )


def test_rental_application_and_amendment(test_setup) -> None:
    unit = test_setup["unit101"]
    landlord = test_setup["landlord"]
    tenant = test_setup["tenant1"]

    app = RentalApplication.objects.create(
        property=unit,
        applicant=tenant,
        proposed_start_date=datetime.date(2026, 2, 1),
        proposed_term_months=12,
        monthly_income_minor=180_000_00,
        employment_status="employed",
        employer_name="Safaricom PLC",
        id_document_type=IdDocumentType.NATIONAL_ID,
        id_document_number="29182736",
    )
    assert app.id is not None
    assert app.reference.startswith("APP-")

    lease = Lease.objects.create(
        property=unit,
        property_owner=landlord,
        application=app,
        start_date=datetime.date(2026, 2, 1),
        effective_end_date=datetime.date(2027, 2, 1),
        rent_amount_minor=50_000_00,
        created_by=landlord,
    )

    amendment = LeaseAmendment.objects.create(
        lease=lease,
        amendment_type=AmendmentType.RENT_ADJUSTMENT,
        effective_date=datetime.date(2026, 8, 1),
        old_rent_amount_minor=50_000_00,
        new_rent_amount_minor=55_000_00,
        reason="Annual 10% rent escalation clause.",
        created_by=landlord,
    )
    assert amendment.id is not None
    assert amendment.reference.startswith("AMD-")
