"""Integration tests for operations REST API endpoints."""

from __future__ import annotations

import datetime
import uuid

import pytest
from django.utils import timezone
from rest_framework.test import APIClient
from tests.organizations_support import client_for, verified_user
from tests.properties_support import property_body

from apps.organizations.models import Organization, OrganizationMembership
from apps.operations.domain import AgreementStatus, PhysicalState
from apps.operations.models import ManagementAgreement, UnitPhysicalProfile
from apps.properties.models import PropertyAccess
from apps.properties.services.records import create_property

pytestmark = pytest.mark.django_db

AGREEMENTS_URL = "/api/v1/operations/agreements/"
PORTFOLIO_URL = "/api/v1/operations/portfolio/"


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
        organization=org,
        user=user,
        role=role,
        status="active",
        source="creator",
        joined_at=timezone.now(),
    )


def test_api_agreement_proposal_by_owner_and_acceptance_by_agent() -> None:
    owner = verified_user("api.owner@example.com", "Password-123!")
    agent = verified_user("api.agent@example.com", "Password-123!")
    prop = create_property(actor=owner, data=property_body(name="Riverside Suite"))
    org = _create_org()
    _add_member(agent, org, role="manager")

    owner_client = client_for(owner)
    agent_client = client_for(agent)

    # 1. Owner proposes agreement via API
    resp = owner_client.post(
        AGREEMENTS_URL,
        {
            "property_id": str(prop.id),
            "managing_organization_id": str(org.id),
            "spend_limit_minor": 750_000,
            "commission_basis_points": 850,
            "valid_from": str(timezone.now().date()),
        },
        format="json",
    )
    assert resp.status_code == 201
    agreement_id = resp.data["id"]
    assert resp.data["status"] == AgreementStatus.PENDING_AGENT_ACCEPTANCE
    assert resp.data["granting_user_id"] == str(owner.id)

    # 2. Both parties can see the agreement in list
    list_resp = agent_client.get(AGREEMENTS_URL)
    assert list_resp.status_code == 200
    ids = [a["id"] for a in list_resp.data["agreements"]]
    assert agreement_id in ids

    # 3. Agent accepts agreement
    accept_url = f"{AGREEMENTS_URL}{agreement_id}/accept/"
    accept_resp = agent_client.post(accept_url, format="json")
    assert accept_resp.status_code == 200
    assert accept_resp.data["status"] == AgreementStatus.ACCEPTED

    # 4. Agent activates agreement
    activate_url = f"{AGREEMENTS_URL}{agreement_id}/activate/"
    activate_resp = agent_client.post(activate_url, format="json")
    assert activate_resp.status_code == 200
    assert activate_resp.data["status"] == AgreementStatus.ACTIVE

    # 5. Check managed portfolio contains the property
    portfolio_resp = agent_client.get(PORTFOLIO_URL)
    assert portfolio_resp.status_code == 200
    portfolio_props = portfolio_resp.data["portfolio"]
    assert any(p["id"] == str(prop.id) for p in portfolio_props)


def test_api_agreement_rejection() -> None:
    owner = verified_user("reject.owner@example.com", "Password-123!")
    agent = verified_user("reject.agent@example.com", "Password-123!")
    prop = create_property(actor=owner, data=property_body(name="Reject Flat"))
    org = _create_org()
    _add_member(agent, org, role="manager")

    agent_client = client_for(agent)
    owner_client = client_for(owner)

    # Agent proposes
    resp = agent_client.post(
        AGREEMENTS_URL,
        {
            "property_id": str(prop.id),
            "managing_organization_id": str(org.id),
            "granting_user_id": str(owner.id),
            "valid_from": str(timezone.now().date()),
        },
        format="json",
    )
    assert resp.status_code == 201
    agreement_id = resp.data["id"]

    # Owner rejects
    reject_url = f"{AGREEMENTS_URL}{agreement_id}/reject/"
    rej_resp = owner_client.post(reject_url, {"reason": "Not interested at this time"}, format="json")
    assert rej_resp.status_code == 200
    assert rej_resp.data["status"] == AgreementStatus.REJECTED


def test_api_agreement_termination() -> None:
    owner = verified_user("term.api.owner@example.com", "Password-123!")
    agent = verified_user("term.api.agent@example.com", "Password-123!")
    prop = create_property(actor=owner, data=property_body(name="Term Flat"))
    org = _create_org()
    _add_member(agent, org, role="manager")

    owner_client = client_for(owner)
    agent_client = client_for(agent)

    # Create, accept, activate
    resp = owner_client.post(
        AGREEMENTS_URL,
        {
            "property_id": str(prop.id),
            "managing_organization_id": str(org.id),
            "valid_from": str(timezone.now().date()),
        },
        format="json",
    )
    agreement_id = resp.data["id"]
    agent_client.post(f"{AGREEMENTS_URL}{agreement_id}/accept/")
    agent_client.post(f"{AGREEMENTS_URL}{agreement_id}/activate/")

    # Terminate via API
    term_resp = owner_client.post(
        f"{AGREEMENTS_URL}{agreement_id}/terminate/",
        {"reason": "Mutual termination"},
        format="json",
    )
    assert term_resp.status_code == 200
    assert term_resp.data["status"] == AgreementStatus.TERMINATED


def test_api_unit_physical_profile_read_and_patch() -> None:
    owner = verified_user("phys.owner@example.com", "Password-123!")
    prop = create_property(actor=owner, data=property_body(name="Penthouse 10A"))
    owner_client = client_for(owner)

    profile_url = f"/api/v1/operations/units/{prop.id}/physical-profile/"

    # GET default profile
    get_resp = owner_client.get(profile_url)
    assert get_resp.status_code == 200
    assert get_resp.data["physical_state"] == PhysicalState.TURNOVER_PREPARATION

    # PATCH to READY_INSPECTED
    patch_resp = owner_client.patch(
        profile_url,
        {
            "physical_state": PhysicalState.READY_INSPECTED,
            "target_rent_minor": 150_000_00,
            "target_currency": "KES",
            "notes": "Keys handed over to security.",
        },
        format="json",
    )
    assert patch_resp.status_code == 200
    assert patch_resp.data["physical_state"] == PhysicalState.READY_INSPECTED
    assert patch_resp.data["ready_at"] is not None
    assert patch_resp.data["target_rent_minor"] == 150_000_00
