"""Comprehensive regression tests for M7-B Phase 2A Architectural Integrity Remediation.

Covers:
1. Management agreement authority invariant and explicit owner revocation.
2. Canonical rentable-unit validation and ambiguous occupancy prevention.
3. Executed lease and party immutability (instance save, update, bulk_update, delete).
4. Signature evidence uniqueness constraint and occupant prohibition.
"""

from __future__ import annotations

import datetime
import uuid

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from tests.organizations_support import verified_user
from tests.properties_support import property_body

from apps.operations.domain import AgreementStatus
from apps.operations.errors import OperationsError
from apps.operations.models import DelegatedPropertyAccess, ManagementAgreement
from apps.operations.services.agreements import (
    accept_agreement,
    activate_agreement,
    propose_agreement,
    revoke_lease_execution_authority,
    terminate_agreement,
    verify_lease_execution_authority,
)
from apps.organizations.models import Organization, OrganizationMembership
from apps.properties.domain import AccessCapability, AccessOrigin, AccessStatus, HierarchyRole, PropertyStatus
from apps.properties.models import Property, PropertyAccess
from apps.properties.services.records import create_property
from apps.tenancy.domain import (
    LeasePartyRole,
    LeaseStatus,
    LeaseType,
    SignatureMethod,
)
from apps.tenancy.models import (
    Lease,
    LeaseParty,
    LeaseSignatureEvidence,
)
from apps.tenancy.services.validation import validate_rentable_unit

pytestmark = pytest.mark.django_db


def _create_org(name: str = "Premier Real Estate") -> Organization:
    return Organization.objects.create(
        legal_name=f"{name} Ltd",
        display_name=name,
        slug=f"{name.lower().replace(' ', '-')}-{uuid.uuid4().hex[:6]}",
        organization_type="agency",
        status="active",
        country="KE",
    )


def _add_member(user, org: Organization, role: str = "manager") -> OrganizationMembership:
    return OrganizationMembership.objects.create(
        user=user,
        organization=org,
        role=role,
        status="active",
        source="creator",
        joined_at=timezone.now(),
    )


def _setup_owner_and_agent():
    owner = verified_user(email="owner@domain.com", password="Password123!")
    agent = verified_user(email="agent@agency.com", password="Password123!")
    org = _create_org()
    _add_member(agent, org, role="manager")

    prop = create_property(
        actor=owner,
        data=property_body(
            name="Alpha Apartments Unit 101",
            hierarchy_role=HierarchyRole.STANDALONE,
        ),
    )
    return owner, agent, org, prop


# ==============================================================================
# AREA 1: MANAGEMENT AGREEMENT AUTHORITY
# ==============================================================================


def test_mandated_managing_agent_possesses_lease_execution_authority():
    """Agreement proposed with can_execute_leases=True grants execution authority upon activation."""
    owner, agent, org, prop = _setup_owner_and_agent()

    # Owner proposes agreement granting lease execution power
    agreement = propose_agreement(
        actor=owner,
        property_id=prop.id,
        managing_org_id=org.id,
        can_execute_leases=True,
    )
    assert agreement.can_execute_leases is True
    assert agreement.status == AgreementStatus.PENDING_AGENT_ACCEPTANCE

    # Agent accepts and activates
    accepted = accept_agreement(actor=agent, agreement_id=agreement.id)
    assert accepted.can_execute_leases is True
    activated = activate_agreement(actor=agent, agreement_id=agreement.id)
    assert activated.status == AgreementStatus.ACTIVE
    assert activated.can_execute_leases is True

    # Mandated agent check succeeds
    mandate = verify_lease_execution_authority(agent, prop)
    assert mandate.id == activated.id


def test_ordinary_agent_without_mandate_lacks_lease_execution_authority():
    """Ordinary managing agent (can_execute_leases=False) cannot execute leases."""
    owner, agent, org, prop = _setup_owner_and_agent()

    agreement = propose_agreement(
        actor=owner,
        property_id=prop.id,
        managing_org_id=org.id,
        can_execute_leases=False,
    )
    accept_agreement(actor=agent, agreement_id=agreement.id)
    activate_agreement(actor=agent, agreement_id=agreement.id)

    with pytest.raises(OperationsError) as exc:
        verify_lease_execution_authority(agent, prop)
    assert exc.value.status_code == 403
    assert "lacks explicit contractual lease execution authority" in str(exc.value)


def test_unauthorized_mutation_of_can_execute_leases_is_blocked():
    """Direct ORM modification of can_execute_leases on active agreements is rejected."""
    owner, agent, org, prop = _setup_owner_and_agent()

    agreement = propose_agreement(
        actor=owner,
        property_id=prop.id,
        managing_org_id=org.id,
        can_execute_leases=False,
    )
    accept_agreement(actor=agent, agreement_id=agreement.id)
    activate_agreement(actor=agent, agreement_id=agreement.id)

    # Direct instance save mutation blocked
    agreement.can_execute_leases = True
    with pytest.raises(ValidationError, match="immutable contractual term"):
        agreement.save()

    # QuerySet bulk update blocked
    with pytest.raises(ValidationError, match="Direct update of 'can_execute_leases' is prohibited"):
        ManagementAgreement.objects.filter(pk=agreement.pk).update(can_execute_leases=True)


def test_agent_cannot_grant_themselves_authority_unilaterally():
    """An agent proposing can_execute_leases=True cannot activate without owner acceptance."""
    owner, agent, org, prop = _setup_owner_and_agent()

    # Agent proposes agreement requesting lease execution authority
    agreement = propose_agreement(
        actor=agent,
        property_id=prop.id,
        managing_org_id=org.id,
        can_execute_leases=True,
    )
    assert agreement.status == AgreementStatus.PENDING_OWNER_ACCEPTANCE

    # Agent tries to activate directly -> blocked
    with pytest.raises(OperationsError) as exc:
        activate_agreement(actor=agent, agreement_id=agreement.id)
    assert exc.value.status_code == 400

    # Agent tries to accept on behalf of owner -> blocked
    with pytest.raises(OperationsError) as exc:
        accept_agreement(actor=agent, agreement_id=agreement.id)
    assert exc.value.status_code == 403


def test_owner_explicit_revocation_of_lease_execution_authority():
    """Owner can explicitly revoke can_execute_leases without terminating general management."""
    owner, agent, org, prop = _setup_owner_and_agent()

    agreement = propose_agreement(
        actor=owner,
        property_id=prop.id,
        managing_org_id=org.id,
        can_execute_leases=True,
    )
    accept_agreement(actor=agent, agreement_id=agreement.id)
    activate_agreement(actor=agent, agreement_id=agreement.id)

    # Agent cannot revoke authority
    with pytest.raises(OperationsError) as exc:
        revoke_lease_execution_authority(actor=agent, agreement_id=agreement.id)
    assert exc.value.status_code == 403

    # Owner revokes authority
    revoked = revoke_lease_execution_authority(
        actor=owner,
        agreement_id=agreement.id,
        reason="Owner elected to handle lease executions directly.",
    )
    assert revoked.status == AgreementStatus.ACTIVE
    # Original contractual grant is immutably preserved
    assert revoked.can_execute_leases is True
    # Revocation evidence is captured
    assert revoked.lease_execution_revoked_at is not None
    assert revoked.lease_execution_revoked_by == owner
    assert revoked.lease_execution_revocation_reason == "Owner elected to handle lease executions directly."
    # Effective authority evaluates to False
    assert revoked.has_effective_lease_execution_authority is False

    # Attempting to clear revocation timestamp via save() is blocked
    revoked.lease_execution_revoked_at = None
    with pytest.raises(ValidationError, match="cannot be restored by clearing revocation timestamps"):
        revoked.save()

    # Attempting to clear revocation timestamp via QuerySet update is blocked
    with pytest.raises(ValidationError, match="Direct restoration of revoked authority via QuerySet\\.update\\(\\) is prohibited"):
        ManagementAgreement.objects.filter(pk=revoked.pk).update(lease_execution_revoked_at=None)

    # Operational management access remains intact
    delegated = DelegatedPropertyAccess.objects.get(agreement=agreement)
    assert delegated.status == AccessStatus.ACTIVE

    # But agent no longer has lease execution authority
    with pytest.raises(OperationsError) as exc:
        verify_lease_execution_authority(agent, prop)
    assert exc.value.status_code == 403


def test_agreement_termination_revokes_lease_execution_authority():
    """Terminating an agreement preserves contractual history but revokes effective authority."""
    owner, agent, org, prop = _setup_owner_and_agent()

    agreement = propose_agreement(
        actor=owner,
        property_id=prop.id,
        managing_org_id=org.id,
        can_execute_leases=True,
    )
    accept_agreement(actor=agent, agreement_id=agreement.id)
    activate_agreement(actor=agent, agreement_id=agreement.id)

    terminated = terminate_agreement(actor=owner, agreement_id=agreement.id, reason="Contract ended.")
    assert terminated.status == AgreementStatus.TERMINATED
    # Original contractual grant is preserved immutably
    assert terminated.can_execute_leases is True
    assert terminated.has_effective_lease_execution_authority is False

    with pytest.raises(OperationsError):
        verify_lease_execution_authority(agent, prop)


def test_lease_execution_authority_scoped_strictly_to_agreement_property():
    """Agreement authority for Property A cannot be used to execute leases on Property B."""
    owner, agent, org, prop_a = _setup_owner_and_agent()
    prop_b = create_property(
        actor=owner,
        data=property_body(name="Property B Standalone", hierarchy_role=HierarchyRole.STANDALONE),
    )

    agreement_a = propose_agreement(
        actor=owner,
        property_id=prop_a.id,
        managing_org_id=org.id,
        can_execute_leases=True,
    )
    accept_agreement(actor=agent, agreement_id=agreement_a.id)
    activate_agreement(actor=agent, agreement_id=agreement_a.id)

    # Valid on Property A
    assert verify_lease_execution_authority(agent, prop_a).id == agreement_a.id

    # Strictly forbidden on Property B
    with pytest.raises(OperationsError) as exc:
        verify_lease_execution_authority(agent, prop_b)
    assert exc.value.status_code == 403


# ==============================================================================
# AREA 2: CANONICAL RENTABLE-UNIT VALIDATION
# ==============================================================================


def test_canonical_rentable_unit_validation_paths():
    """Validate reusable rentable-unit domain validation across property hierarchies."""
    owner = verified_user(email="landlord@domain.com", password="Password123!")

    # 1. Valid Standalone
    standalone = create_property(
        actor=owner,
        data=property_body(name="Standalone Villa", hierarchy_role=HierarchyRole.STANDALONE),
    )
    validate_rentable_unit(standalone)  # Must pass without error

    # 2. Valid Child Unit under Building
    building = create_property(
        actor=owner,
        data=property_body(name="Sunrise Heights", hierarchy_role=HierarchyRole.BUILDING),
    )
    child_unit = create_property(
        actor=owner,
        data=property_body(
            name="Sunrise Unit 12B",
            hierarchy_role=HierarchyRole.UNIT,
            parent_id=building.id,
        ),
    )
    validate_rentable_unit(child_unit)  # Must pass without error

    # 3. Aggregate Building rejected
    with pytest.raises(ValidationError, match="is an aggregate container and cannot be leased directly"):
        validate_rentable_unit(building)

    # 4. Aggregate Estate rejected
    estate = create_property(
        actor=owner,
        data=property_body(name="Green Park Estate", hierarchy_role=HierarchyRole.ESTATE),
    )
    with pytest.raises(ValidationError, match="is an aggregate container and cannot be leased directly"):
        validate_rentable_unit(estate)

    # 5. Parent property with child units rejected to prevent ambiguous occupancy allocation
    # Attach child unit to standalone directly to test the subdivided child check in validate_rentable_unit
    Property.objects.filter(pk=child_unit.pk).update(parent=standalone)
    standalone.refresh_from_db()
    with pytest.raises(ValidationError, match="contains subdivided child units"):
        validate_rentable_unit(standalone)
    # Restore parent to building
    Property.objects.filter(pk=child_unit.pk).update(parent=building)

    # 6. Unit without parent rejected
    orphan_unit = Property(reference="PRP-ORPHAN", hierarchy_role=HierarchyRole.UNIT, status=PropertyStatus.ACTIVE)
    with pytest.raises(ValidationError, match="must belong to a parent building or estate"):
        validate_rentable_unit(orphan_unit)

    # Unit whose parent is another unit rejected
    nested_unit = Property(reference="PRP-NESTED", hierarchy_role=HierarchyRole.UNIT, parent=child_unit, status=PropertyStatus.ACTIVE)
    with pytest.raises(ValidationError, match="cannot have a parent that is also a unit"):
        validate_rentable_unit(nested_unit)

    # 7. Archived property rejected
    standalone_archived = create_property(
        actor=owner,
        data=property_body(name="Old Villa", hierarchy_role=HierarchyRole.STANDALONE),
    )
    standalone_archived.status = PropertyStatus.ARCHIVED
    standalone_archived.archived_at = timezone.now()
    standalone_archived.save()
    with pytest.raises(ValidationError, match="is archived and cannot be leased"):
        validate_rentable_unit(standalone_archived)


def test_lease_orm_creation_enforces_rentable_unit():
    """Lease save(), objects.create(), and bulk_create() reject unrentable properties."""
    owner = verified_user(email="landlord_orm@domain.com", password="Password123!")
    building = create_property(
        actor=owner,
        data=property_body(name="Sunrise Tower", hierarchy_role=HierarchyRole.BUILDING),
    )
    standalone = create_property(
        actor=owner,
        data=property_body(name="Sunrise Cottage", hierarchy_role=HierarchyRole.STANDALONE),
    )

    # 1. Lease.objects.create() on aggregate building fails
    with pytest.raises(ValidationError, match="is an aggregate container and cannot be leased directly"):
        Lease.objects.create(
            property=building,
            property_owner=owner,
            lease_type=LeaseType.RESIDENTIAL_FIXED,
            start_date=datetime.date(2026, 1, 1),
            term_end_date=datetime.date(2026, 12, 31),
            effective_end_date=datetime.date(2026, 12, 31),
            rent_amount_minor=5000000,
            rent_currency="KES",
            status=LeaseStatus.DRAFT,
            created_by=owner,
        )

    # 2. Lease.save() on aggregate building fails
    unrentable_lease = Lease(
        property=building,
        property_owner=owner,
        lease_type=LeaseType.RESIDENTIAL_FIXED,
        start_date=datetime.date(2026, 1, 1),
        term_end_date=datetime.date(2026, 12, 31),
        effective_end_date=datetime.date(2026, 12, 31),
        rent_amount_minor=5000000,
        rent_currency="KES",
        status=LeaseStatus.DRAFT,
        created_by=owner,
    )
    with pytest.raises(ValidationError, match="is an aggregate container and cannot be leased directly"):
        unrentable_lease.save()

    # 3. Lease.objects.bulk_create() on aggregate building fails
    with pytest.raises(ValidationError, match="is an aggregate container and cannot be leased directly"):
        Lease.objects.bulk_create([unrentable_lease])

    # 4. Lease.objects.bulk_create() on valid rentable property succeeds
    valid_lease = Lease(
        property=standalone,
        property_owner=owner,
        lease_type=LeaseType.RESIDENTIAL_FIXED,
        start_date=datetime.date(2026, 1, 1),
        term_end_date=datetime.date(2026, 12, 31),
        effective_end_date=datetime.date(2026, 12, 31),
        rent_amount_minor=5000000,
        rent_currency="KES",
        status=LeaseStatus.DRAFT,
        created_by=owner,
    )
    created_list = Lease.objects.bulk_create([valid_lease])
    assert len(created_list) == 1
    assert created_list[0].pk is not None


# ==============================================================================
# AREA 3: EXECUTED LEASE IMMUTABILITY
# ==============================================================================


def test_executed_lease_immutability_guards():
    """Executed lease contractual terms cannot be mutated or deleted."""
    owner = verified_user(email="owner2@domain.com", password="Password123!")
    tenant = verified_user(email="tenant2@domain.com", password="Password123!")
    prop = create_property(
        actor=owner,
        data=property_body(name="Unit 202", hierarchy_role=HierarchyRole.STANDALONE),
    )

    lease = Lease.objects.create(
        property=prop,
        property_owner=owner,
        lease_type=LeaseType.RESIDENTIAL_FIXED,
        start_date=datetime.date(2026, 1, 1),
        term_end_date=datetime.date(2026, 12, 31),
        effective_end_date=datetime.date(2026, 12, 31),
        rent_amount_minor=6000000,
        rent_currency="KES",
        payment_due_day=1,
        status=LeaseStatus.FULLY_EXECUTED,
        created_by=owner,
    )
    party = LeaseParty.objects.create(
        lease=lease,
        user=tenant,
        party_role=LeasePartyRole.PRIMARY_TENANT,
        has_signature_authority=True,
    )

    # 1. Instance save mutation of rent is blocked
    lease.rent_amount_minor = 5000000
    with pytest.raises(ValidationError, match="immutable on executed lease"):
        lease.save()

    # 2. Instance save mutation of dates is blocked
    lease.refresh_from_db()
    lease.start_date = datetime.date(2026, 2, 1)
    with pytest.raises(ValidationError, match="immutable on executed lease"):
        lease.save()

    # 3. QuerySet bulk update of rent is blocked
    with pytest.raises(ValidationError, match="Direct update of contractual fields"):
        Lease.objects.filter(pk=lease.pk).update(rent_amount_minor=5000000)

    # 4. bulk_update is blocked
    lease.refresh_from_db()
    lease.rent_amount_minor = 5000000
    with pytest.raises(ValidationError, match="Direct bulk_update of contractual fields"):
        Lease.objects.bulk_update([lease], ["rent_amount_minor"])

    # 5. Deletion of executed lease is blocked
    lease.refresh_from_db()
    with pytest.raises(ValidationError, match="Executed lease .* cannot be deleted"):
        lease.delete()

    with pytest.raises(ValidationError, match="Executed leases cannot be deleted"):
        Lease.objects.filter(pk=lease.pk).delete()

    # 6. Party mutation and deletion on executed lease is blocked
    party.party_role = LeasePartyRole.CO_TENANT
    with pytest.raises(ValidationError, match="Cannot mutate signatory party on executed lease"):
        party.save()

    party.refresh_from_db()
    with pytest.raises(ValidationError, match="Cannot delete party from executed lease"):
        party.delete()

    with pytest.raises(ValidationError, match="Parties on executed leases cannot be deleted"):
        LeaseParty.objects.filter(pk=party.pk).delete()


def test_early_termination_preserves_contractual_history():
    """Early termination modifies lifecycle status without corrupting original contractual terms."""
    owner = verified_user(email="owner3@domain.com", password="Password123!")
    prop = create_property(
        actor=owner,
        data=property_body(name="Unit 303", hierarchy_role=HierarchyRole.STANDALONE),
    )

    original_start = datetime.date(2026, 1, 1)
    original_term_end = datetime.date(2026, 12, 31)
    original_rent = 7500000

    lease = Lease.objects.create(
        property=prop,
        property_owner=owner,
        lease_type=LeaseType.RESIDENTIAL_FIXED,
        start_date=original_start,
        term_end_date=original_term_end,
        effective_end_date=original_term_end,
        rent_amount_minor=original_rent,
        rent_currency="KES",
        status=LeaseStatus.ACTIVE,
        created_by=owner,
    )

    # Execute early termination as of 2026-06-30
    term_date = datetime.date(2026, 6, 30)
    now = timezone.now()
    lease.status = LeaseStatus.TERMINATED_EARLY
    lease.actual_termination_date = term_date
    lease.effective_end_date = term_date
    lease.terminated_at = now
    lease.termination_reason = "Mutual early surrender."
    lease.save()

    lease.refresh_from_db()
    assert lease.status == LeaseStatus.TERMINATED_EARLY
    assert lease.effective_end_date == term_date
    assert lease.actual_termination_date == term_date
    # Original terms remain intact
    assert lease.start_date == original_start
    assert lease.term_end_date == original_term_end
    assert lease.rent_amount_minor == original_rent


# ==============================================================================
# AREA 4: SIGNATURE EVIDENCE UNIQUENESS & CONSTRAINTS
# ==============================================================================


def test_signature_evidence_unique_party_version_constraint():
    """Database constraint prevents duplicate signature evidence for same party and version."""
    owner = verified_user(email="owner4@domain.com", password="Password123!")
    tenant = verified_user(email="tenant4@domain.com", password="Password123!")
    prop = create_property(
        actor=owner,
        data=property_body(name="Unit 404", hierarchy_role=HierarchyRole.STANDALONE),
    )

    lease = Lease.objects.create(
        property=prop,
        property_owner=owner,
        lease_type=LeaseType.RESIDENTIAL_FIXED,
        start_date=datetime.date(2026, 1, 1),
        term_end_date=datetime.date(2026, 12, 31),
        effective_end_date=datetime.date(2026, 12, 31),
        rent_amount_minor=4500000,
        rent_currency="KES",
        status=LeaseStatus.PENDING_SIGNATURE,
        created_by=owner,
    )
    party = LeaseParty.objects.create(
        lease=lease,
        user=tenant,
        party_role=LeasePartyRole.PRIMARY_TENANT,
        has_signature_authority=True,
    )

    # 1. First signature for document_version=1 succeeds
    LeaseSignatureEvidence.objects.create(
        idempotency_key="sig-key-1",
        lease=lease,
        party=party,
        signatory_user=tenant,
        signatory_role=LeasePartyRole.PRIMARY_TENANT,
        document_version=1,
        contract_data_hash="a" * 64,
        rendered_document_hash="b" * 64,
        signature_method=SignatureMethod.CLICK_TO_SIGN,
    )

    # 2. Duplicate submission for same party and document_version=1 is rejected by DB UniqueConstraint
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            LeaseSignatureEvidence.objects.create(
                idempotency_key="sig-key-duplicate",
                lease=lease,
                party=party,
                signatory_user=tenant,
                signatory_role=LeasePartyRole.PRIMARY_TENANT,
                document_version=1,
                contract_data_hash="a" * 64,
                rendered_document_hash="b" * 64,
                signature_method=SignatureMethod.CLICK_TO_SIGN,
            )

    # 3. Legitimate re-signing of new document version (version 2) succeeds
    sig_v2 = LeaseSignatureEvidence.objects.create(
        idempotency_key="sig-key-v2",
        lease=lease,
        party=party,
        signatory_user=tenant,
        signatory_role=LeasePartyRole.PRIMARY_TENANT,
        document_version=2,
        contract_data_hash="c" * 64,
        rendered_document_hash="d" * 64,
        signature_method=SignatureMethod.CLICK_TO_SIGN,
    )
    assert sig_v2.document_version == 2
    # Both version 1 and version 2 records exist in database
    assert LeaseSignatureEvidence.objects.filter(lease=lease, party=party).count() == 2


def test_authorized_occupant_cannot_sign_evidence():
    """Database check constraint and model validation prevent authorized occupants from signing."""
    owner = verified_user(email="owner5@domain.com", password="Password123!")
    occupant = verified_user(email="occupant5@domain.com", password="Password123!")
    prop = create_property(
        actor=owner,
        data=property_body(name="Unit 505", hierarchy_role=HierarchyRole.STANDALONE),
    )

    lease = Lease.objects.create(
        property=prop,
        property_owner=owner,
        lease_type=LeaseType.RESIDENTIAL_FIXED,
        start_date=datetime.date(2026, 1, 1),
        term_end_date=datetime.date(2026, 12, 31),
        effective_end_date=datetime.date(2026, 12, 31),
        rent_amount_minor=4500000,
        rent_currency="KES",
        status=LeaseStatus.PENDING_SIGNATURE,
        created_by=owner,
    )
    occupant_party = LeaseParty.objects.create(
        lease=lease,
        user=occupant,
        party_role=LeasePartyRole.AUTHORIZED_OCCUPANT,
        has_signature_authority=False,
    )

    # Attempting to create signature evidence for occupant is rejected by validation and DB constraint
    evidence = LeaseSignatureEvidence(
        idempotency_key="sig-occupant",
        lease=lease,
        party=occupant_party,
        signatory_user=occupant,
        signatory_role=LeasePartyRole.AUTHORIZED_OCCUPANT,
        document_version=1,
        contract_data_hash="a" * 64,
        rendered_document_hash="b" * 64,
        signature_method=SignatureMethod.CLICK_TO_SIGN,
    )
    # 1. Model clean() validation blocks authorized occupant
    with pytest.raises(ValidationError, match="lacks signature authority"):
        evidence.clean()

    # 2. Database check constraint tenancy_sig_evidence_no_occupant blocks authorized occupant
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            super(LeaseSignatureEvidence, evidence).save()


def test_early_termination_temporal_exclusion_preservation():
    """Early terminated lease retains historical exclusion while releasing future dates."""
    owner = verified_user(email="owner_early@domain.com", password="Password123!")
    prop = create_property(
        actor=owner,
        data=property_body(name="Unit 909", hierarchy_role=HierarchyRole.STANDALONE),
    )

    # 1. Lease 1 is created, active for full year 2026: [2026-01-01, 2027-01-01)
    lease1 = Lease.objects.create(
        property=prop,
        property_owner=owner,
        lease_type=LeaseType.RESIDENTIAL_FIXED,
        start_date=datetime.date(2026, 1, 1),
        term_end_date=datetime.date(2026, 12, 31),
        effective_end_date=datetime.date(2027, 1, 1),
        rent_amount_minor=6000000,
        rent_currency="KES",
        status=LeaseStatus.ACTIVE,
        created_by=owner,
    )

    # 2. Early termination on 2026-06-30
    # Truncate effective_end_date to day after actual termination: 2026-07-01
    lease1.actual_termination_date = datetime.date(2026, 6, 30)
    lease1.effective_end_date = datetime.date(2026, 7, 1)
    lease1.status = LeaseStatus.TERMINATED_EARLY
    lease1.termination_reason = "Mutual early termination agreement."
    lease1.save(update_fields=["actual_termination_date", "effective_end_date", "status", "termination_reason", "updated_at"])

    # 3. An attempt to create a backdated or overlapping lease in the historical period [2026-05-01, 2026-08-01)
    # must fail at the PostgreSQL GiST exclusion constraint level
    with pytest.raises(IntegrityError, match="tenancy_lease_exclude_overlapping_contractual_leases"):
        with transaction.atomic():
            Lease.objects.create(
                property=prop,
                property_owner=owner,
                lease_type=LeaseType.RESIDENTIAL_FIXED,
                start_date=datetime.date(2026, 5, 1),
                term_end_date=datetime.date(2026, 7, 31),
                effective_end_date=datetime.date(2026, 8, 1),
                rent_amount_minor=6000000,
                rent_currency="KES",
                status=LeaseStatus.PENDING_SIGNATURE,
                created_by=owner,
            )

    # 4. A replacement lease starting on or after the truncated date [2026-07-01, 2027-01-01)
    # succeeds without exclusion conflict
    lease2 = Lease.objects.create(
        property=prop,
        property_owner=owner,
        lease_type=LeaseType.RESIDENTIAL_FIXED,
        start_date=datetime.date(2026, 7, 1),
        term_end_date=datetime.date(2026, 12, 31),
        effective_end_date=datetime.date(2027, 1, 1),
        rent_amount_minor=6500000,
        rent_currency="KES",
        status=LeaseStatus.PENDING_SIGNATURE,
        created_by=owner,
    )
    assert lease2.id is not None
    assert Lease.objects.filter(property=prop).count() == 2
