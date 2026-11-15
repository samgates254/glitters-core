"""Security and cross-organization isolation tests for operations."""

from __future__ import annotations

import uuid

import pytest
from django.db import connection
from django.utils import timezone
from rest_framework.test import APIClient
from tests.organizations_support import client_for, verified_user
from tests.properties_support import property_body

from apps.organizations.domain import MembershipStatus
from apps.organizations.models import Organization, OrganizationMembership
from apps.operations.domain import AgreementStatus
from apps.operations.errors import OperationsError
from apps.operations.models import ManagementAgreement, OperationsEvent
from apps.operations.services.agreements import (
    accept_agreement,
    activate_agreement,
    propose_agreement,
    terminate_agreement,
)
from apps.properties.domain import AccessOrigin, AccessStatus
from apps.properties.models import PropertyAccess, PropertyParty
from apps.properties.services.records import create_property

pytestmark = pytest.mark.django_db

AGREEMENTS_URL = "/api/v1/operations/agreements/"


def _create_org(name: str) -> Organization:
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


def test_postgresql_btree_gist_extension_installed() -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT extname FROM pg_extension WHERE extname = 'btree_gist';")
        row = cursor.fetchone()
        assert row is not None
        assert row[0] == "btree_gist"


def test_cross_organization_isolation_in_agreements() -> None:
    owner = verified_user("cross.owner@example.com", "Password-123!")
    agent_a = verified_user("agent.a@example.com", "Password-123!")
    agent_b = verified_user("agent.b@example.com", "Password-123!")

    org_a = _create_org("Org A Management")
    org_b = _create_org("Org B Management")

    _add_member(agent_a, org_a, role="manager")
    _add_member(agent_b, org_b, role="manager")

    prop = create_property(actor=owner, data=property_body(name="Secured Residence"))

    # Owner proposes to Org A
    agreement = propose_agreement(
        actor=owner,
        property_id=prop.id,
        managing_org_id=org_a.id,
        valid_from=timezone.now().date(),
    )

    # Agent B (from Org B) tries to accept Org A's agreement
    with pytest.raises(OperationsError) as exc:
        accept_agreement(actor=agent_b, agreement_id=agreement.id)
    assert exc.value.status_code == 403

    # Agent B via API cannot view details of Org A's agreement
    client_b = client_for(agent_b)
    detail_resp = client_b.get(f"{AGREEMENTS_URL}{agreement.id}/")
    assert detail_resp.status_code == 403

    # Agent B in list cannot see Org A's agreement
    list_resp = client_b.get(AGREEMENTS_URL)
    assert list_resp.status_code == 200
    ids = [a["id"] for a in list_resp.data["agreements"]]
    assert str(agreement.id) not in ids


def test_anti_hijacking_unverified_property_party_claimant_blocked() -> None:
    legit_owner = verified_user("real.owner@example.com", "Password-123!")
    imposter = verified_user("imposter@example.com", "Password-123!")
    agent = verified_user("firm.agent@example.com", "Password-123!")

    org = _create_org("Target Management")
    _add_member(agent, org, role="manager")

    prop = create_property(actor=legit_owner, data=property_body(name="High Value Estate"))

    # Imposter registers unverified "owner" claim
    PropertyParty.objects.create(
        property=prop,
        subject_kind="person",
        user=imposter,
        role="owner",
        status="claimed",
        claimed_by=imposter,
    )

    # Imposter attempts to propose management agreement to PM org
    with pytest.raises(OperationsError) as exc:
        propose_agreement(
            actor=imposter,
            property_id=prop.id,
            managing_org_id=org.id,
        )
    assert exc.value.status_code == 403

    # Agent attempts to propose agreement naming imposter as granting_user
    with pytest.raises(OperationsError) as exc:
        propose_agreement(
            actor=agent,
            property_id=prop.id,
            managing_org_id=org.id,
            granting_user_id=imposter.id,
        )
    assert exc.value.status_code == 403


def test_property_management_context_and_org_remain_immutable() -> None:
    owner = verified_user("immutable.owner@example.com", "Password-123!")
    agent = verified_user("immutable.agent@example.com", "Password-123!")
    prop = create_property(actor=owner, data=property_body(name="Context Test House"))
    org = _create_org("Immutable Org")
    _add_member(agent, org, role="manager")

    assert prop.management_context == "personal"
    assert prop.managing_organization is None

    # Propose, accept, activate
    agreement = propose_agreement(
        actor=owner,
        property_id=prop.id,
        managing_org_id=org.id,
        valid_from=timezone.now().date(),
    )
    accept_agreement(actor=agent, agreement_id=agreement.id)
    activate_agreement(actor=agent, agreement_id=agreement.id)

    # Refresh property from database
    prop.refresh_from_db()
    # Invariant: management_context must NEVER be mutated to 'organization'
    assert prop.management_context == "personal"
    # Invariant: managing_organization must remain NULL for personal properties
    assert prop.managing_organization is None

    # Terminate agreement
    terminate_agreement(actor=owner, agreement_id=agreement.id, reason="Done")
    prop.refresh_from_db()
    assert prop.management_context == "personal"
    assert prop.managing_organization is None


def test_migration_consistency_check() -> None:
    from django.core.management import call_command

    # Ensures schema matches model declarations exactly with no uncommitted migrations
    call_command("makemigrations", "--check", "--dry-run")


def test_unaffiliated_tenant_denied_from_operations() -> None:
    tenant = verified_user("tenant@example.com", "Password-123!")
    client = client_for(tenant)

    # Empty portfolio returned for user without operational memberships
    resp = client.get("/api/v1/operations/portfolio/")
    assert resp.status_code == 200
    assert resp.data["portfolio"] == []


def test_f01_direct_owner_authority_provenance_and_sub_delegation_blocked() -> None:
    owner = verified_user("f01.owner@example.com", "Password-123!")
    agent = verified_user("f01.agent@example.com", "Password-123!")
    sub_agent_org = _create_org("Sub Agency")
    org = _create_org("Prime Agency")
    _add_member(agent, org, role="manager")

    prop = create_property(actor=owner, data=property_body(name="F01 Manor"))

    # 1. Direct authorized owner can propose agreement
    agreement = propose_agreement(
        actor=owner,
        property_id=prop.id,
        managing_org_id=org.id,
        valid_from=timezone.now().date(),
    )
    accept_agreement(actor=agent, agreement_id=agreement.id)
    activate_agreement(actor=agent, agreement_id=agreement.id)

    # Agent now holds inherited PropertyAccess
    inherited_grant = PropertyAccess.objects.get(property=prop, user=agent)
    assert inherited_grant.origin == AccessOrigin.INHERITED
    assert inherited_grant.status == AccessStatus.ACTIVE

    # 2 & 3. Inherited management agent cannot propose an agreement as owner or sub-delegate
    # Terminate agreement 1 to avoid ACTIVE_AGREEMENT_EXISTS check
    terminate_agreement(actor=owner, agreement_id=agreement.id, reason="Testing")
    # Restore inherited grant to active status artificially to simulate attack with lingering grant
    inherited_grant.status = AccessStatus.ACTIVE
    inherited_grant.revoked_at = None
    inherited_grant.save(update_fields=["status", "revoked_at"])

    with pytest.raises(OperationsError) as exc:
        propose_agreement(
            actor=agent,
            property_id=prop.id,
            managing_org_id=sub_agent_org.id,
            valid_from=timezone.now().date(),
        )
    assert exc.value.status_code == 403

    # 4. Revoked direct grant cannot authorize a proposal
    owner_grant = PropertyAccess.objects.get(property=prop, user=owner)
    owner_grant.status = AccessStatus.REVOKED
    owner_grant.revoked_at = timezone.now()
    owner_grant.save(update_fields=["status", "revoked_at"])

    with pytest.raises(OperationsError) as exc:
        propose_agreement(
            actor=owner,
            property_id=prop.id,
            managing_org_id=org.id,
            valid_from=timezone.now().date(),
        )
    assert exc.value.status_code == 403

    # 5. Unverified PropertyParty claimant remains unauthorized
    claimant = verified_user("f01.claimant@example.com", "Password-123!")
    PropertyParty.objects.create(
        property=prop,
        subject_kind="person",
        user=claimant,
        role="owner",
        status="claimed",
        claimed_by=claimant,
    )
    with pytest.raises(OperationsError) as exc:
        propose_agreement(
            actor=claimant,
            property_id=prop.id,
            managing_org_id=org.id,
            valid_from=timezone.now().date(),
        )
    assert exc.value.status_code == 403


def test_f02_agreement_expiry_enforcement_and_boundaries() -> None:
    import datetime
    from apps.operations.services.agreements import (
        is_agreement_expired,
        is_grant_expired,
        sync_agreement_expiry,
    )
    from apps.properties.services.authorization import decide

    owner = verified_user("f02.owner@example.com", "Password-123!")
    agent = verified_user("f02.agent@example.com", "Password-123!")
    prop = create_property(actor=owner, data=property_body(name="F02 Heights"))
    org = _create_org("F02 Agency")
    _add_member(agent, org, role="manager")

    today = timezone.localdate()
    # Boundary: valid_until set to today
    agreement = propose_agreement(
        actor=owner,
        property_id=prop.id,
        managing_org_id=org.id,
        valid_from=today - datetime.timedelta(days=10),
        valid_until=today,
    )
    accept_agreement(actor=agent, agreement_id=agreement.id)
    agreement = activate_agreement(actor=agent, agreement_id=agreement.id)

    # 7. Exact boundary behavior: ON valid_until (today), agreement is STILL VALID
    assert not is_agreement_expired(agreement, target_date=today)
    decision = decide(agent, prop.id)
    assert decision.can_update is True

    # 1 & 4. When date exceeds valid_until (today + 1 day), authority is IMMEDIATELY revoked
    tomorrow = today + datetime.timedelta(days=1)
    assert is_agreement_expired(agreement, target_date=tomorrow)

    # Authorization layer dynamically blocks access without relying on Celery worker
    agent_grant = PropertyAccess.objects.get(property=prop, user=agent)
    assert is_grant_expired(agent_grant, target_date=tomorrow)

    # 3. Direct owner access remains 100% unaffected
    owner_decision = decide(owner, prop.id)
    assert owner_decision.can_update is True

    # 5. Idempotent cleanup via sync_agreement_expiry
    assert sync_agreement_expiry(agreement, target_date=tomorrow) is True
    agreement.refresh_from_db()
    assert agreement.status == AgreementStatus.EXPIRED

    # Calling again is idempotent
    assert sync_agreement_expiry(agreement, target_date=tomorrow) is False

    # 6. Expiry did not touch owner grant or other properties
    owner_grant = PropertyAccess.objects.get(property=prop, user=owner)
    assert owner_grant.status == AccessStatus.ACTIVE


def test_f03_corporate_ownership_authorization_path() -> None:
    corporate_owner = verified_user("corp.owner@example.com", "Password-123!")
    corporate_admin = verified_user("corp.admin@example.com", "Password-123!")
    ordinary_member = verified_user("corp.member@example.com", "Password-123!")
    inactive_member = verified_user("corp.inactive@example.com", "Password-123!")
    unrelated_user = verified_user("corp.unrelated@example.com", "Password-123!")
    pm_agent = verified_user("corp.pm@example.com", "Password-123!")

    holding_org = _create_org("Holding Co")
    unrelated_org = _create_org("Unrelated Co")
    pm_firm = _create_org("External PM Firm")

    _add_member(corporate_owner, holding_org, role="owner")
    _add_member(corporate_admin, holding_org, role="admin")
    _add_member(ordinary_member, holding_org, role="member")
    inactive_membership = _add_member(inactive_member, holding_org, role="admin")
    inactive_membership.status = MembershipStatus.SUSPENDED
    inactive_membership.save(update_fields=["status"])

    _add_member(unrelated_user, unrelated_org, role="owner")
    _add_member(pm_agent, pm_firm, role="manager")

    # Create organization-owned property
    prop_data = property_body(
        name="Commercial Plaza",
        management_context="organization",
        organization_id=str(holding_org.id),
    )
    prop = create_property(actor=corporate_owner, data=prop_data)

    # 1. Authorized corporate rep (owner or admin) can propose management agreement
    agreement = propose_agreement(
        actor=corporate_owner,
        property_id=prop.id,
        managing_org_id=pm_firm.id,
        valid_from=timezone.now().date(),
    )
    assert agreement.status == AgreementStatus.PENDING_AGENT_ACCEPTANCE
    assert agreement.managing_organization == pm_firm

    # 2. Ordinary organization member without owner/admin role is rejected
    with pytest.raises(OperationsError) as exc:
        propose_agreement(
            actor=ordinary_member,
            property_id=prop.id,
            managing_org_id=pm_firm.id,
            valid_from=timezone.now().date(),
        )
    assert exc.value.status_code == 403

    # 3. Former or inactive organization member is rejected
    with pytest.raises(OperationsError) as exc:
        propose_agreement(
            actor=inactive_member,
            property_id=prop.id,
            managing_org_id=pm_firm.id,
            valid_from=timezone.now().date(),
        )
    assert exc.value.status_code == 403

    # 4. Representative of unrelated organization is rejected
    with pytest.raises(OperationsError) as exc:
        propose_agreement(
            actor=unrelated_user,
            property_id=prop.id,
            managing_org_id=pm_firm.id,
            valid_from=timezone.now().date(),
        )
    assert exc.value.status_code == 403

    # 5. External PM firm must accept before activation
    accept_agreement(actor=pm_agent, agreement_id=agreement.id)
    activated = activate_agreement(actor=pm_agent, agreement_id=agreement.id)
    assert activated.status == AgreementStatus.ACTIVE


def test_f04_audit_event_bulk_update_blocked() -> None:
    user = verified_user("f04.user@example.com", "Password-123!")
    prop = create_property(actor=user, data=property_body(name="F04 Property"))

    event = OperationsEvent.objects.create(
        idempotency_key="key-f04",
        actor=user,
        property=prop,
        action="agreement.drafted",
        object_type="management_agreement",
        object_id="id-f04",
    )

    # bulk_update() must be blocked by AppendOnlyQuerySet
    event.action = "agreement.activated"
    with pytest.raises(ValueError, match="Operations events are append-only"):
        OperationsEvent.objects.bulk_update([event], ["action"])
