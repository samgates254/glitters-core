"""Unit tests for operations domain services: Management agreements, delegated access, and physical readiness."""

from __future__ import annotations

import datetime
import uuid

import pytest
from django.utils import timezone
from tests.organizations_support import verified_user
from tests.properties_support import property_body

from apps.organizations.models import Organization, OrganizationMembership
from apps.operations.domain import (
    ACTIVE_AGREEMENT_EXISTS,
    DATE_RANGE_INVALID,
    EFFECTIVE_DATE_INVALID,
    INITIATOR_MISMATCH,
    PERMISSION_DENIED,
    PHYSICAL_STATE_INVALID,
    UNAUTHORIZED_AGENT,
    UNAUTHORIZED_OWNER,
    AgreementStatus,
    PhysicalState,
)
from apps.operations.errors import OperationsError
from apps.operations.models import DelegatedPropertyAccess, ManagementAgreement, UnitPhysicalProfile
from apps.operations.services.agreements import (
    accept_agreement,
    activate_agreement,
    propose_agreement,
    reject_agreement,
    revoke_agreement,
    terminate_agreement,
    verify_agent_authority,
    verify_owner_authority,
)
from apps.operations.services.physical_profiles import (
    get_or_create_physical_profile,
    update_physical_profile,
)
from apps.properties.domain import AccessCapability, AccessOrigin, AccessStatus
from apps.properties.models import PropertyAccess, PropertyParty
from apps.properties.services.records import create_property

pytestmark = pytest.mark.django_db


def _create_org(name: str = "Prime PM Firm") -> Organization:
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
        organization=org,
        user=user,
        role=role,
        status="active",
        source="creator",
        joined_at=timezone.now(),
    )


def test_anti_hijacking_unverified_claimant_cannot_propose() -> None:
    owner = verified_user("legit.owner@example.com", "Password-123!")
    attacker = verified_user("attacker@example.com", "Password-123!")
    prop = create_property(actor=owner, data=property_body(name="Prime Villa"))
    org = _create_org()
    _add_member(attacker, org, role="manager")

    # Attacker records an unverified party claim claiming to be the owner
    PropertyParty.objects.create(
        property=prop,
        subject_kind="person",
        user=attacker,
        role="owner",
        status="claimed",
        claimed_by=attacker,
    )

    # Attacker claims they can propose agreement as owner on another PM firm
    other_org = _create_org("Other PM")
    with pytest.raises(OperationsError) as exc:
        propose_agreement(
            actor=attacker,
            property_id=prop.id,
            managing_org_id=other_org.id,
        )
    assert exc.value.status_code == 403


def test_verified_owner_initiates_agreement_and_agent_accepts() -> None:
    owner = verified_user("owner@example.com", "Password-123!")
    agent = verified_user("agent@example.com", "Password-123!")
    prop = create_property(actor=owner, data=property_body(name="Kilimani Flat"))
    org = _create_org()
    _add_member(agent, org, role="manager")

    # Owner proposes agreement
    agreement = propose_agreement(
        actor=owner,
        property_id=prop.id,
        managing_org_id=org.id,
        spend_limit_minor=500_000,
        commission_basis_points=1000,
        valid_from=timezone.now().date(),
    )
    assert agreement.status == AgreementStatus.PENDING_AGENT_ACCEPTANCE
    assert agreement.granting_user == owner
    assert agreement.accepted_by_owner_at is not None
    assert agreement.accepted_by_agent_at is None

    # Agent accepts
    accepted = accept_agreement(actor=agent, agreement_id=agreement.id)
    assert accepted.status == AgreementStatus.ACCEPTED
    assert accepted.accepted_by_agent_at is not None
    assert accepted.assigned_agent == agent

    # Controlled activation
    activated = activate_agreement(actor=agent, agreement_id=agreement.id)
    assert activated.status == AgreementStatus.ACTIVE
    assert activated.activated_at is not None

    # Verify PropertyAccess was created for agent WITHOUT mutating Property
    prop.refresh_from_db()
    assert prop.management_context == "personal"
    assert prop.managing_organization is None

    grant = PropertyAccess.objects.get(property=prop, user=agent, status=AccessStatus.ACTIVE)
    assert grant.capability == AccessCapability.MANAGE
    assert grant.origin == AccessOrigin.INHERITED

    delegation = DelegatedPropertyAccess.objects.get(agreement=activated, user=agent)
    assert delegation.status == AccessStatus.ACTIVE
    assert delegation.property_access == grant


def test_agent_initiates_agreement_and_owner_accepts() -> None:
    owner = verified_user("owner2@example.com", "Password-123!")
    agent = verified_user("agent2@example.com", "Password-123!")
    prop = create_property(actor=owner, data=property_body(name="Lavington House"))
    org = _create_org()
    _add_member(agent, org, role="manager")

    # Agent proposes agreement
    agreement = propose_agreement(
        actor=agent,
        property_id=prop.id,
        managing_org_id=org.id,
        granting_user_id=owner.id,
        valid_from=timezone.now().date(),
    )
    assert agreement.status == AgreementStatus.PENDING_OWNER_ACCEPTANCE
    assert agreement.accepted_by_agent_at is not None
    assert agreement.accepted_by_owner_at is None

    # Random stranger cannot accept
    stranger = verified_user("stranger@example.com", "Password-123!")
    with pytest.raises(OperationsError) as exc:
        accept_agreement(actor=stranger, agreement_id=agreement.id)
    assert exc.value.status_code == 403

    # Owner accepts
    accepted = accept_agreement(actor=owner, agreement_id=agreement.id)
    assert accepted.status == AgreementStatus.ACCEPTED
    assert accepted.accepted_by_owner_at is not None

    # Owner activates
    activated = activate_agreement(actor=owner, agreement_id=agreement.id)
    assert activated.status == AgreementStatus.ACTIVE


def test_activation_blocked_if_effective_start_date_in_future() -> None:
    owner = verified_user("future.owner@example.com", "Password-123!")
    agent = verified_user("future.agent@example.com", "Password-123!")
    prop = create_property(actor=owner, data=property_body(name="Future Tower"))
    org = _create_org()
    _add_member(agent, org, role="manager")

    future_start = timezone.now().date() + datetime.timedelta(days=30)
    agreement = propose_agreement(
        actor=owner,
        property_id=prop.id,
        managing_org_id=org.id,
        valid_from=future_start,
    )
    accept_agreement(actor=agent, agreement_id=agreement.id)

    # Activating in advance must be rejected
    with pytest.raises(OperationsError, match="in the future"):
        activate_agreement(actor=agent, agreement_id=agreement.id)


def test_termination_revokes_delegated_access() -> None:
    owner = verified_user("term.owner@example.com", "Password-123!")
    agent = verified_user("term.agent@example.com", "Password-123!")
    prop = create_property(actor=owner, data=property_body(name="Term House"))
    org = _create_org()
    _add_member(agent, org, role="manager")

    agreement = propose_agreement(
        actor=owner,
        property_id=prop.id,
        managing_org_id=org.id,
        valid_from=timezone.now().date(),
    )
    accept_agreement(actor=agent, agreement_id=agreement.id)
    activated = activate_agreement(actor=agent, agreement_id=agreement.id)

    # Verify agent has active grant
    assert PropertyAccess.objects.filter(
        property=prop, user=agent, status=AccessStatus.ACTIVE
    ).exists()

    # Terminate agreement
    terminated = terminate_agreement(
        actor=owner,
        agreement_id=activated.id,
        reason="Owner sold the property",
    )
    assert terminated.status == AgreementStatus.TERMINATED
    assert terminated.terminated_at is not None

    # Underlying grant and delegation must now be REVOKED
    grant = PropertyAccess.objects.get(property=prop, user=agent)
    assert grant.status == AccessStatus.REVOKED
    assert grant.revoked_at is not None

    delegation = DelegatedPropertyAccess.objects.get(agreement=activated, user=agent)
    assert delegation.status == AccessStatus.REVOKED
    assert delegation.revoked_at is not None


def test_rejection_and_revocation_lifecycles() -> None:
    owner = verified_user("rej.owner@example.com", "Password-123!")
    agent = verified_user("rej.agent@example.com", "Password-123!")
    prop = create_property(actor=owner, data=property_body(name="Rejection Estate"))
    org = _create_org()
    _add_member(agent, org, role="manager")

    # Proposal 1: Counterparty rejects
    agr1 = propose_agreement(
        actor=agent,
        property_id=prop.id,
        managing_org_id=org.id,
        granting_user_id=owner.id,
        valid_from=timezone.now().date(),
    )
    rejected = reject_agreement(actor=owner, agreement_id=agr1.id, reason="Commission too high")
    assert rejected.status == AgreementStatus.REJECTED

    # Proposal 2: Proposing party revokes before counterparty responds
    agr2 = propose_agreement(
        actor=agent,
        property_id=prop.id,
        managing_org_id=org.id,
        granting_user_id=owner.id,
        valid_from=timezone.now().date(),
    )
    # Non-initiator cannot revoke
    with pytest.raises(OperationsError, match="Only the proposing party"):
        revoke_agreement(actor=owner, agreement_id=agr2.id)

    # Agent revokes
    revoked = revoke_agreement(actor=agent, agreement_id=agr2.id)
    assert revoked.status == AgreementStatus.REVOKED


def test_unit_physical_profile_lifecycle() -> None:
    owner = verified_user("unit.owner@example.com", "Password-123!")
    prop = create_property(actor=owner, data=property_body(name="Unit 4B"))

    # Initial profile is created lazily or upon request
    profile = get_or_create_physical_profile(prop)
    assert profile.physical_state == PhysicalState.TURNOVER_PREPARATION
    assert profile.ready_at is None

    # Update to READY_INSPECTED
    updated = update_physical_profile(
        actor=owner,
        property_id=prop.id,
        new_state=PhysicalState.READY_INSPECTED,
        target_rent_minor=120_000_00,
        target_currency="KES",
        notes="Deep cleaned and inspected.",
    )
    assert updated.physical_state == PhysicalState.READY_INSPECTED
    assert updated.ready_at is not None
    assert updated.target_rent_minor == 120_000_00

    # Disallowed transition raises error
    # Cannot transition directly from READY_INSPECTED to STRUCTURAL_OFFLINE
    with pytest.raises(OperationsError, match="transition"):
        update_physical_profile(
            actor=owner,
            property_id=prop.id,
            new_state=PhysicalState.STRUCTURAL_OFFLINE,
        )


def test_expired_agreement_blocks_activation() -> None:
    owner = verified_user("expired.owner@example.com", "Password-123!")
    agent = verified_user("expired.agent@example.com", "Password-123!")
    prop = create_property(actor=owner, data=property_body(name="Past House"))
    org = _create_org()
    _add_member(agent, org, role="manager")

    past_date = timezone.now().date() - datetime.timedelta(days=10)
    agreement = propose_agreement(
        actor=owner,
        property_id=prop.id,
        managing_org_id=org.id,
        valid_from=past_date - datetime.timedelta(days=30),
        valid_until=past_date,
    )
    accept_agreement(actor=agent, agreement_id=agreement.id)

    with pytest.raises(OperationsError, match="expired"):
        activate_agreement(actor=agent, agreement_id=agreement.id)


def test_one_active_agreement_per_property_enforced() -> None:
    owner = verified_user("dual.owner@example.com", "Password-123!")
    agent1 = verified_user("agent.one@example.com", "Password-123!")
    agent2 = verified_user("agent.two@example.com", "Password-123!")
    prop = create_property(actor=owner, data=property_body(name="Single Mgr House"))
    org1 = _create_org("Agency One")
    org2 = _create_org("Agency Two")
    _add_member(agent1, org1, role="manager")
    _add_member(agent2, org2, role="manager")

    # Agreement 1 active
    agr1 = propose_agreement(
        actor=owner,
        property_id=prop.id,
        managing_org_id=org1.id,
        valid_from=timezone.now().date(),
    )
    accept_agreement(actor=agent1, agreement_id=agr1.id)
    activate_agreement(actor=agent1, agreement_id=agr1.id)

    # Attempt to propose another agreement on same property while agr1 is ACTIVE
    with pytest.raises(OperationsError, match="active management agreement already exists"):
        propose_agreement(
            actor=owner,
            property_id=prop.id,
            managing_org_id=org2.id,
            valid_from=timezone.now().date(),
        )


def test_managing_agent_can_update_physical_profile() -> None:
    owner = verified_user("ag.owner@example.com", "Password-123!")
    agent = verified_user("ag.agent@example.com", "Password-123!")
    stranger = verified_user("ag.stranger@example.com", "Password-123!")
    prop = create_property(actor=owner, data=property_body(name="Agent Managed Flat"))
    org = _create_org()
    _add_member(agent, org, role="manager")

    # Create & activate agreement
    agreement = propose_agreement(
        actor=owner,
        property_id=prop.id,
        managing_org_id=org.id,
        valid_from=timezone.now().date(),
    )
    accept_agreement(actor=agent, agreement_id=agreement.id)
    activate_agreement(actor=agent, agreement_id=agreement.id)

    # Agent updates physical profile
    updated = update_physical_profile(
        actor=agent,
        property_id=prop.id,
        new_state=PhysicalState.READY_INSPECTED,
        target_rent_minor=85_000_00,
    )
    assert updated.physical_state == PhysicalState.READY_INSPECTED
    assert updated.updated_by == agent

    # Unrelated stranger cannot update physical profile
    with pytest.raises(OperationsError, match="permission"):
        update_physical_profile(
            actor=stranger,
            property_id=prop.id,
            new_state=PhysicalState.TURNOVER_PREPARATION,
        )
