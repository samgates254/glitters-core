"""Signing, activation, and termination after the canonical digest exists."""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from tests.organizations_support import verified_user
from tests.properties_support import property_body

from apps.operations.domain import PhysicalState
from apps.operations.models import UnitPhysicalProfile
from apps.operations.services.agreements import (
    accept_agreement,
    activate_agreement,
    propose_agreement,
)
from apps.operations.services.physical_profiles import update_physical_profile
from apps.organizations.models import Organization, OrganizationMembership
from apps.properties.services.records import create_property
from apps.tenancy.domain import (
    ALREADY_SIGNED,
    NOT_READY_FOR_MOVE_IN,
    SIGNATURE_DEADLINE_EXPIRED,
    TERMS_HASH_MISMATCH,
    UNAUTHORIZED_SIGNATORY,
    HandoverStatus,
    HandoverType,
    LeasePartyRole,
    LeaseStatus,
    ScheduleStatus,
    SignatureMethod,
    TenancyEventAction,
)
from apps.tenancy.errors import TenancyError
from apps.tenancy.models import HandoverProtocol, Lease, LeaseSignatureEvidence, RentScheduleEntry
from apps.tenancy.services.leases import (
    activate_lease,
    approve_lease_terms,
    create_draft_lease,
    sign_lease,
    terminate_lease,
)

pytestmark = pytest.mark.django_db

PASSWORD = "Password-123!"
DOCUMENT = b"%PDF-1.7\nGLITTERS executed lease\n"
START = date(2026, 9, 1)
TERM_END = date(2027, 8, 31)
TERMINATION = date(2026, 11, 15)


def _user(prefix: str):
    return verified_user(f"{prefix}-{uuid.uuid4().hex[:8]}@example.com", PASSWORD)


def _unit(owner):
    return create_property(
        actor=owner,
        data=property_body(name=f"Unit {uuid.uuid4().hex[:8]}"),
    )


def _organization() -> Organization:
    slug = uuid.uuid4().hex[:6]
    return Organization.objects.create(
        legal_name=f"Prime PM {slug} Ltd",
        display_name=f"Prime PM {slug}",
        slug=f"prime-pm-{slug}",
        organization_type="agency",
        status="active",
        country="KE",
    )


def _member(user, organization) -> OrganizationMembership:
    return OrganizationMembership.objects.create(
        organization=organization,
        user=user,
        role="manager",
        status="active",
        source="creator",
        joined_at=timezone.now(),
    )


def _approved(owner, tenant, occupant=None, *, now=None):
    parties = [
        {
            "user_id": tenant.id,
            "party_role": LeasePartyRole.PRIMARY_TENANT,
            "has_signature_authority": True,
        }
    ]
    if occupant is not None:
        parties.append(
            {
                "user_id": occupant.id,
                "party_role": LeasePartyRole.AUTHORIZED_OCCUPANT,
                "has_signature_authority": False,
            }
        )
    prop = _unit(owner)
    draft = create_draft_lease(
        actor=owner,
        property_id=prop.id,
        payload={
            "lease_type": "residential_fixed",
            "start_date": START,
            "term_end_date": TERM_END,
            "rent_amount_minor": 4_500_000,
            "parties": parties,
        },
    )
    approved = approve_lease_terms(
        actor=owner,
        lease_id=draft.id,
        rendered_document=DOCUMENT,
        now=now,
    )
    return approved, prop


def _sign(actor, lease, rendered=DOCUMENT, **kwargs):
    party = lease.parties.get(user=actor)
    return sign_lease(
        actor=actor,
        lease_id=lease.id,
        party_id=party.id,
        rendered_document=rendered,
        signature_method=SignatureMethod.CLICK_TO_SIGN,
        **kwargs,
    )


def _executed(owner, tenant):
    lease, prop = _approved(owner, tenant)
    _sign(owner, lease)
    _sign(tenant, lease)
    lease.refresh_from_db()
    return lease, prop


def _ready_for_move_in(owner, lease) -> None:
    HandoverProtocol.objects.create(
        lease=lease,
        protocol_type=HandoverType.MOVE_IN,
        handover_date=START,
        physical_condition_summary="Walls intact. Keys received.",
        status=HandoverStatus.EXECUTED,
        created_by=owner,
    )
    update_physical_profile(
        actor=owner,
        property_id=lease.property_id,
        new_state=PhysicalState.READY_INSPECTED,
    )


def _active(owner, tenant):
    lease, prop = _executed(owner, tenant)
    _ready_for_move_in(owner, lease)
    activated = activate_lease(actor=owner, lease_id=lease.id)
    return activated, prop


def test_both_parties_sign_the_approved_document_without_starting_occupancy() -> None:
    owner = _user("owner")
    tenant = _user("tenant")
    lease, prop = _approved(owner, tenant)
    approved_hash = lease.contract_data_hash
    rendered_hash = lease.rendered_document_hash

    owner_evidence = _sign(owner, lease, idempotency_key="owner-sign")
    lease.refresh_from_db()
    assert lease.status == LeaseStatus.PENDING_SIGNATURE
    assert lease.activated_at is None
    assert owner_evidence.contract_data_hash == approved_hash
    assert owner_evidence.rendered_document_hash == rendered_hash

    replay = _sign(owner, lease, idempotency_key="owner-sign")
    assert replay.id == owner_evidence.id
    assert (
        LeaseSignatureEvidence.objects.filter(lease=lease, party=owner_evidence.party).count() == 1
    )

    tenant_evidence = _sign(tenant, lease)
    lease.refresh_from_db()
    assert lease.status == LeaseStatus.FULLY_EXECUTED
    assert lease.signed_at is not None
    assert lease.activated_at is None
    assert lease.contract_data_hash == approved_hash
    assert lease.rendered_document_hash == rendered_hash
    assert lease.rent_amount_minor == 4_500_000
    assert tenant_evidence.signatory_user_id == tenant.id
    assert not UnitPhysicalProfile.objects.filter(property=prop).exists()
    actions = set(lease.events.values_list("action", flat=True))
    assert TenancyEventAction.LEASE_PARTY_SIGNED in actions
    assert TenancyEventAction.LEASE_FULLY_EXECUTED in actions
    assert TenancyEventAction.LEASE_ACTIVATED not in actions


def test_a_second_distinct_signature_is_rejected() -> None:
    owner = _user("owner")
    tenant = _user("tenant")
    lease, _prop = _approved(owner, tenant)
    _sign(owner, lease, idempotency_key="owner-once")

    with pytest.raises(TenancyError, match=ALREADY_SIGNED) as captured:
        _sign(owner, lease, idempotency_key="owner-again")

    assert captured.value.status_code == 409
    assert LeaseSignatureEvidence.objects.filter(lease=lease).count() == 1
    lease.refresh_from_db()
    assert lease.status == LeaseStatus.PENDING_SIGNATURE


def test_different_rendered_bytes_do_not_record_a_signature() -> None:
    owner = _user("owner")
    tenant = _user("tenant")
    lease, _prop = _approved(owner, tenant)

    with pytest.raises(TenancyError, match=TERMS_HASH_MISMATCH) as captured:
        _sign(owner, lease, rendered=b"%PDF-1.7\nrewritten terms\n")

    assert captured.value.status_code == 409
    assert LeaseSignatureEvidence.objects.filter(lease=lease).count() == 0
    lease.refresh_from_db()
    assert lease.status == LeaseStatus.PENDING_SIGNATURE
    assert lease.contract_data_hash != ""


def test_stranger_and_occupant_cannot_sign() -> None:
    owner = _user("owner")
    tenant = _user("tenant")
    occupant = _user("occupant")
    stranger = _user("stranger")
    lease, _prop = _approved(owner, tenant, occupant)
    tenant_party = lease.parties.get(user=tenant)

    with pytest.raises(TenancyError, match=UNAUTHORIZED_SIGNATORY) as stranger_error:
        sign_lease(
            actor=stranger,
            lease_id=lease.id,
            party_id=tenant_party.id,
            rendered_document=DOCUMENT,
            signature_method=SignatureMethod.CLICK_TO_SIGN,
        )
    assert stranger_error.value.status_code == 403

    with pytest.raises(TenancyError, match=UNAUTHORIZED_SIGNATORY) as occupant_error:
        _sign(occupant, lease)
    assert occupant_error.value.status_code == 403
    assert LeaseSignatureEvidence.objects.filter(lease=lease).count() == 0


def test_signing_at_the_deadline_does_not_expire_or_record_evidence() -> None:
    owner = _user("owner")
    tenant = _user("tenant")
    opened = timezone.now()
    lease, _prop = _approved(owner, tenant, now=opened)
    deadline = lease.signature_deadline
    assert deadline is not None

    with pytest.raises(TenancyError, match=SIGNATURE_DEADLINE_EXPIRED) as captured:
        _sign(owner, lease, now=deadline)

    assert captured.value.status_code == 409
    lease.refresh_from_db()
    assert lease.status == LeaseStatus.PENDING_SIGNATURE
    assert LeaseSignatureEvidence.objects.filter(lease=lease).count() == 0
    assert not lease.events.filter(action=TenancyEventAction.LEASE_RESERVATION_EXPIRED).exists()


def test_signed_contract_and_signature_rows_cannot_be_rewritten() -> None:
    owner = _user("owner")
    tenant = _user("tenant")
    lease, _prop = _executed(owner, tenant)
    original_hash = lease.contract_data_hash

    lease.rent_amount_minor = 9_000_000
    with pytest.raises(ValidationError, match="immutable on executed lease"):
        lease.save()
    lease.refresh_from_db()
    assert lease.rent_amount_minor == 4_500_000
    assert lease.contract_data_hash == original_hash

    evidence = LeaseSignatureEvidence.objects.get(lease=lease, signatory_user=owner)
    with pytest.raises(ValidationError, match="append-only"):
        LeaseSignatureEvidence.objects.filter(pk=evidence.pk).update(user_agent="rewritten")
    with pytest.raises(ValidationError, match="append-only"):
        LeaseSignatureEvidence.objects.bulk_update([evidence], ["user_agent"])
    with pytest.raises(ValidationError, match="cannot be deleted"):
        LeaseSignatureEvidence.objects.filter(pk=evidence.pk).delete()
    evidence.refresh_from_db()
    assert evidence.user_agent == ""
    assert evidence.contract_data_hash == original_hash


def test_direct_signature_create_still_works() -> None:
    owner = _user("owner")
    tenant = _user("tenant")
    lease, _prop = _approved(owner, tenant)
    party = lease.parties.get(user=tenant)
    created = LeaseSignatureEvidence.objects.create(
        idempotency_key=f"direct-{uuid.uuid4().hex}",
        lease=lease,
        party=party,
        signatory_user=tenant,
        signatory_role=party.party_role,
        document_version=lease.document_version,
        contract_data_hash=lease.contract_data_hash,
        rendered_document_hash=lease.rendered_document_hash,
        signature_method=SignatureMethod.CLICK_TO_SIGN,
    )
    assert LeaseSignatureEvidence.objects.filter(pk=created.pk).exists()


def test_activation_requires_an_executed_move_in_and_a_ready_unit() -> None:
    owner = _user("owner")
    tenant = _user("tenant")
    lease, _prop = _executed(owner, tenant)

    with pytest.raises(TenancyError, match=NOT_READY_FOR_MOVE_IN) as captured:
        activate_lease(actor=owner, lease_id=lease.id)

    assert captured.value.status_code == 409
    lease.refresh_from_db()
    assert lease.status == LeaseStatus.FULLY_EXECUTED
    assert lease.activated_at is None


def test_activation_starts_occupancy_without_changing_the_digest() -> None:
    owner = _user("owner")
    tenant = _user("tenant")
    lease, prop = _executed(owner, tenant)
    approved_hash = lease.contract_data_hash
    _ready_for_move_in(owner, lease)

    activated = activate_lease(actor=owner, lease_id=lease.id)

    assert activated.status == LeaseStatus.ACTIVE
    assert activated.activated_at is not None
    assert activated.contract_data_hash == approved_hash
    assert activated.rent_amount_minor == 4_500_000
    profile = UnitPhysicalProfile.objects.get(property=prop)
    assert profile.physical_state == PhysicalState.OCCUPIED_NORMAL
    assert lease.events.filter(action=TenancyEventAction.LEASE_ACTIVATED).count() == 1


def test_tenant_and_unempowered_agent_cannot_terminate() -> None:
    owner = _user("owner")
    tenant = _user("tenant")
    manager = _user("manager")
    lease, prop = _active(owner, tenant)
    organization = _organization()
    _member(manager, organization)
    proposed = propose_agreement(
        actor=owner,
        property_id=prop.id,
        managing_org_id=organization.id,
        can_execute_leases=False,
        valid_from=timezone.localdate(),
    )
    accepted = accept_agreement(actor=manager, agreement_id=proposed.id)
    activate_agreement(actor=owner, agreement_id=accepted.id)

    with pytest.raises(TenancyError) as tenant_error:
        terminate_lease(
            actor=tenant,
            lease_id=lease.id,
            termination_date=TERMINATION,
            reason="Tenant cannot end this lease.",
        )
    assert tenant_error.value.status_code == 403

    with pytest.raises(TenancyError, match="can_execute_leases=False") as manager_error:
        terminate_lease(
            actor=manager,
            lease_id=lease.id,
            termination_date=TERMINATION,
            reason="Agent lacks execution authority.",
        )
    assert manager_error.value.status_code == 403
    lease.refresh_from_db()
    assert lease.status == LeaseStatus.ACTIVE
    assert lease.actual_termination_date is None
    assert lease.events.filter(action=TenancyEventAction.LEASE_TERMINATED).count() == 0


def test_early_termination_preserves_the_signed_contract_and_releases_the_tail() -> None:
    owner = _user("owner")
    tenant = _user("tenant")
    lease, prop = _active(owner, tenant)
    approved_hash = lease.contract_data_hash
    RentScheduleEntry.objects.create(
        lease=lease,
        sequence_number=1,
        period_start=START,
        period_end=date(2026, 9, 30),
        due_date=START,
        base_rent_minor=4_500_000,
        service_charge_minor=0,
        total_due_minor=4_500_000,
        currency="KES",
    )
    RentScheduleEntry.objects.create(
        lease=lease,
        sequence_number=2,
        period_start=date(2026, 12, 1),
        period_end=date(2026, 12, 31),
        due_date=date(2026, 12, 1),
        base_rent_minor=4_500_000,
        service_charge_minor=0,
        total_due_minor=4_500_000,
        currency="KES",
    )

    terminated = terminate_lease(
        actor=owner,
        lease_id=lease.id,
        termination_date=TERMINATION,
        reason="Mutual early exit after notice.",
    )

    assert terminated.status == LeaseStatus.TERMINATED_EARLY
    assert terminated.actual_termination_date == TERMINATION
    assert terminated.effective_end_date == TERMINATION + timedelta(days=1)
    assert terminated.term_end_date == TERM_END
    assert terminated.contract_data_hash == approved_hash
    assert terminated.rent_amount_minor == 4_500_000
    kept = terminated.rent_schedules.get(sequence_number=1)
    cancelled = terminated.rent_schedules.get(sequence_number=2)
    assert kept.status == ScheduleStatus.SCHEDULED
    assert kept.base_rent_minor == kept.total_due_minor == 4_500_000
    assert cancelled.status == ScheduleStatus.CANCELLED
    assert cancelled.base_rent_minor == cancelled.total_due_minor == 4_500_000
    profile = UnitPhysicalProfile.objects.get(property=prop)
    assert profile.physical_state == PhysicalState.TURNOVER_PREPARATION
    assert terminated.events.filter(action=TenancyEventAction.LEASE_TERMINATED).count() == 1
    assert terminated.events.filter(action=TenancyEventAction.RENT_SCHEDULE_CANCELLED).count() == 1

    with pytest.raises(
        IntegrityError, match="tenancy_lease_exclude_overlapping_contractual_leases"
    ):
        with transaction.atomic():
            Lease.objects.create(
                property=prop,
                property_owner=owner,
                start_date=TERMINATION,
                term_end_date=date(2026, 12, 14),
                effective_end_date=date(2026, 12, 15),
                rent_amount_minor=4_500_000,
                status=LeaseStatus.PENDING_SIGNATURE,
                created_by=owner,
            )

    following = Lease.objects.create(
        property=prop,
        property_owner=owner,
        start_date=TERMINATION + timedelta(days=1),
        term_end_date=date(2027, 8, 31),
        effective_end_date=date(2027, 9, 1),
        rent_amount_minor=4_500_000,
        status=LeaseStatus.PENDING_SIGNATURE,
        created_by=owner,
    )
    assert following.start_date == date(2026, 11, 16)

    with pytest.raises(TenancyError, match="not allowed") as second:
        terminate_lease(
            actor=owner,
            lease_id=lease.id,
            termination_date=TERMINATION,
            reason="Already ended.",
        )
    assert second.value.status_code == 409
    assert terminated.events.filter(action=TenancyEventAction.LEASE_TERMINATED).count() == 1
