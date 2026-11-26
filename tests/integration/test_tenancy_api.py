"""Authenticated tenancy API, organization isolation, and lease workflows."""

from __future__ import annotations

import base64
import threading
import uuid
from datetime import date

import pytest
from django.db import connection
from rest_framework.test import APIClient
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
from apps.tenancy.domain import HandoverStatus, HandoverType, LeaseStatus, TenancyEventAction
from apps.tenancy.models import HandoverProtocol, Lease, LeaseSignatureEvidence

pytestmark = pytest.mark.django_db

PASSWORD = "Password-123!"
LEASES = "/api/v1/tenancy/leases/"
SUMMARY = "/api/v1/tenancy/summaries/occupancy/"
DOCUMENT = b"%PDF-1.7\nGLITTERS executed lease\n"
DOCUMENT_B64 = base64.b64encode(DOCUMENT).decode("ascii")
OTHER_B64 = base64.b64encode(b"%PDF-1.7\nrewritten\n").decode("ascii")
START = "2026-09-01"
TERM_END = "2027-08-31"


def _user(prefix: str):
    return verified_user(f"{prefix}-{uuid.uuid4().hex[:8]}@example.com", PASSWORD)


def _client(user=None) -> APIClient:
    client = APIClient()
    if user is not None:
        client.force_authenticate(user=user)
    return client


def _unit(owner, name: str | None = None):
    label = name or f"Unit {uuid.uuid4().hex[:8]}"
    return create_property(actor=owner, data=property_body(name=label))


def _organization(name: str = "Prime PM") -> Organization:
    slug = uuid.uuid4().hex[:6]
    return Organization.objects.create(
        legal_name=f"{name} {slug} Ltd",
        display_name=f"{name} {slug}",
        slug=f"{name.lower().replace(' ', '-')}-{slug}",
        organization_type="agency",
        status="active",
        country="KE",
    )


def _member(user, organization, role: str = "manager") -> OrganizationMembership:
    from django.utils import timezone

    return OrganizationMembership.objects.create(
        organization=organization,
        user=user,
        role=role,
        status="active",
        source="creator",
        joined_at=timezone.now(),
    )


def _agreement(owner, agent, prop, organization, *, execute: bool = False):
    from django.utils import timezone

    proposed = propose_agreement(
        actor=owner,
        property_id=prop.id,
        managing_org_id=organization.id,
        can_execute_leases=execute,
        valid_from=timezone.now().date(),
    )
    accepted = accept_agreement(actor=agent, agreement_id=proposed.id)
    return activate_agreement(actor=owner, agreement_id=accepted.id)


def _body(prop, tenant, **overrides) -> dict:
    body = {
        "property_id": str(prop.id),
        "lease_type": "residential_fixed",
        "start_date": START,
        "term_end_date": TERM_END,
        "rent_amount_minor": 4_500_000,
        "parties": [
            {
                "user_id": str(tenant.id),
                "party_role": "primary_tenant",
                "has_signature_authority": True,
            }
        ],
    }
    body.update(overrides)
    return body


def _create(owner, prop, tenant, **extra):
    response = _client(owner).post(LEASES, _body(prop, tenant, **extra), format="json")
    return response


def _approve(actor, lease_id, document=DOCUMENT_B64):
    return _client(actor).post(
        f"{LEASES}{lease_id}/approve/",
        {"rendered_document_base64": document, "deadline_hours": 72},
        format="json",
    )


def _sign(actor, lease_id, party_id, *, document=DOCUMENT_B64, key=None):
    body = {
        "party_id": str(party_id),
        "rendered_document_base64": document,
        "signature_method": "click_to_sign",
    }
    if key:
        body["idempotency_key"] = key
    return _client(actor).post(f"{LEASES}{lease_id}/sign/", body, format="json")


def test_anonymous_requests_are_rejected() -> None:
    anonymous = _client()
    assert anonymous.get(LEASES).status_code == 401
    assert anonymous.post(LEASES, {}, format="json").status_code == 401
    assert anonymous.get(SUMMARY).status_code == 401
    missing = uuid.uuid4()
    assert anonymous.get(f"{LEASES}{missing}/").status_code == 401


def test_owner_creates_lists_and_reads_a_draft() -> None:
    owner = _user("owner")
    tenant = _user("tenant")
    prop = _unit(owner, "Nairobi synthetic walk-up")
    created = _create(owner, prop, tenant, idempotency_key="draft-nairobi")
    assert created.status_code == 201
    lease_id = created.json()["id"]
    assert created.json()["status"] == LeaseStatus.DRAFT
    assert created.json()["effective_end_date"] is None
    assert created.json()["contract_data_hash"] == ""

    replay = _create(owner, prop, tenant, idempotency_key="draft-nairobi")
    assert replay.status_code == 200
    assert replay.json()["id"] == lease_id

    listed = _client(owner).get(LEASES)
    assert listed.status_code == 200
    assert lease_id in {row["id"] for row in listed.json()["results"]}

    detail = _client(owner).get(f"{LEASES}{lease_id}/")
    terms = _client(owner).get(f"{LEASES}{lease_id}/terms/")
    parties = _client(owner).get(f"{LEASES}{lease_id}/parties/")
    reservation = _client(owner).get(f"{LEASES}{lease_id}/reservation/")
    history = _client(owner).get(f"{LEASES}{lease_id}/history/")
    assert detail.status_code == 200
    assert terms.json()["rent_amount_minor"] == 4_500_000
    assert terms.json()["term_end_date"] == TERM_END
    roles = {row["party_role"] for row in parties.json()["parties"]}
    assert roles == {"primary_tenant", "landlord"}
    assert reservation.json()["occupancy_reserving"] is False
    actions = {row["action"] for row in history.json()["results"]}
    assert TenancyEventAction.LEASE_DRAFTED in actions
    patched = _client(owner).patch(
        f"{LEASES}{lease_id}/",
        {"rent_amount_minor": 1},
        format="json",
    )
    assert patched.status_code == 405


def test_private_leases_stay_hidden_from_other_organizations() -> None:
    owner = _user("owner")
    tenant = _user("tenant")
    stranger = _user("stranger")
    other_manager = _user("other-manager")
    prop = _unit(owner)
    created = _create(owner, prop, tenant)
    lease_id = created.json()["id"]
    other = _organization("Other PM")
    _member(other_manager, other, role="manager")

    for actor in (stranger, other_manager):
        client = _client(actor)
        assert client.get(f"{LEASES}{lease_id}/").status_code == 404
        assert client.get(f"{LEASES}{lease_id}/document/").status_code == 404
        assert client.get(f"{LEASES}{lease_id}/history/").status_code == 404
        assert client.get(f"{LEASES}{lease_id}/signatures/").status_code == 404
        assert lease_id not in {row["id"] for row in client.get(LEASES).json()["results"]}

    refused = _client(stranger).post(LEASES, _body(prop, tenant), format="json")
    assert refused.status_code == 403
    assert Lease.objects.filter(property=prop).count() == 1


def test_invalid_terms_and_unapproved_drafts_do_not_reserve() -> None:
    owner = _user("owner")
    tenant = _user("tenant")
    prop = _unit(owner)
    invalid = _client(owner).post(
        LEASES,
        _body(prop, tenant, rent_amount_minor=0),
        format="json",
    )
    assert invalid.status_code == 400
    created = _create(owner, prop, tenant)
    lease_id = created.json()["id"]
    empty = _client(owner).post(
        f"{LEASES}{lease_id}/approve/",
        {"rendered_document_base64": ""},
        format="json",
    )
    assert empty.status_code == 400
    lease = Lease.objects.get(pk=lease_id)
    assert lease.status == LeaseStatus.DRAFT
    assert lease.effective_end_date is None

    tenant_approval = _approve(tenant, lease_id)
    assert tenant_approval.status_code == 403
    lease.refresh_from_db()
    assert lease.status == LeaseStatus.DRAFT


def test_approval_reserves_once_and_rejects_a_conflict() -> None:
    owner = _user("owner")
    tenant = _user("tenant")
    other = _user("other")
    prop = _unit(owner)
    first = _create(owner, prop, tenant).json()["id"]
    second = _create(owner, prop, other).json()["id"]
    approved = _approve(owner, first)
    assert approved.status_code == 200
    body = approved.json()
    assert body["status"] == LeaseStatus.PENDING_SIGNATURE
    assert body["effective_end_date"] == "2027-09-01"
    assert len(body["contract_data_hash"]) == 64
    assert body["contract_data_hash"] != "f" * 64
    reservation = _client(owner).get(f"{LEASES}{first}/reservation/").json()
    assert reservation["occupancy_reserving"] is True

    again = _approve(owner, first)
    assert again.status_code == 409
    conflict = _approve(owner, second)
    assert conflict.status_code == 409
    assert Lease.objects.get(pk=second).status == LeaseStatus.DRAFT
    assert Lease.objects.get(pk=first).contract_data_hash == body["contract_data_hash"]


@pytest.mark.django_db(transaction=True)
def test_concurrent_approvals_reserve_one_lease() -> None:
    owner = _user("owner")
    tenant = _user("tenant")
    prop = _unit(owner)
    lease_id = _create(owner, prop, tenant).json()["id"]
    results: list[int] = []

    def attempt() -> None:
        try:
            results.append(_approve(owner, lease_id).status_code)
        finally:
            connection.close()

    threads = [threading.Thread(target=attempt), threading.Thread(target=attempt)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == [200, 409]
    assert Lease.objects.get(pk=lease_id).status == LeaseStatus.PENDING_SIGNATURE


def test_signing_rejects_the_wrong_document_and_replays_one_party() -> None:
    owner = _user("owner")
    tenant = _user("tenant")
    stranger = _user("stranger")
    prop = _unit(owner)
    lease_id = _create(owner, prop, tenant).json()["id"]
    approved = _approve(owner, lease_id).json()
    lease = Lease.objects.get(pk=lease_id)
    owner_party = lease.parties.get(user=owner)
    tenant_party = lease.parties.get(user=tenant)

    mismatch = _sign(owner, lease_id, owner_party.id, document=OTHER_B64)
    assert mismatch.status_code == 409
    assert LeaseSignatureEvidence.objects.filter(lease=lease).count() == 0

    forged = _sign(stranger, lease_id, tenant_party.id)
    assert forged.status_code == 404

    first = _sign(owner, lease_id, owner_party.id, key="owner-sign")
    assert first.status_code == 201
    replay = _sign(owner, lease_id, owner_party.id, key="owner-sign")
    assert replay.status_code == 200
    assert replay.json()["id"] == first.json()["id"]
    assert "ip_address" not in replay.json()
    assert "user_agent" not in replay.json()
    duplicate = _sign(owner, lease_id, owner_party.id, key="owner-other")
    assert duplicate.status_code == 409

    tenant_signed = _sign(tenant, lease_id, tenant_party.id)
    assert tenant_signed.status_code == 201
    lease.refresh_from_db()
    assert lease.status == LeaseStatus.FULLY_EXECUTED
    assert lease.activated_at is None
    assert lease.contract_data_hash == approved["contract_data_hash"]
    document = _client(tenant).get(f"{LEASES}{lease_id}/document/").json()
    assert document["contract_data_hash"] == approved["contract_data_hash"]
    assert document["rendered_document_hash"] == approved["rendered_document_hash"]
    assert "ip_address" not in document
    signatures = _client(owner).get(f"{LEASES}{lease_id}/signatures/").json()
    assert len(signatures["evidence"]) == 2
    assert all("ip_address" not in row for row in signatures["evidence"])


def _ready(owner, lease_id) -> None:
    lease = Lease.objects.get(pk=lease_id)
    HandoverProtocol.objects.create(
        lease=lease,
        protocol_type=HandoverType.MOVE_IN,
        handover_date=date(2026, 9, 1),
        physical_condition_summary="Synthetic handover. Keys received.",
        status=HandoverStatus.EXECUTED,
        created_by=owner,
    )
    update_physical_profile(
        actor=owner,
        property_id=lease.property_id,
        new_state=PhysicalState.READY_INSPECTED,
    )


def _executed(owner, tenant, prop):
    lease_id = _create(owner, prop, tenant).json()["id"]
    assert _approve(owner, lease_id).status_code == 200
    lease = Lease.objects.get(pk=lease_id)
    assert _sign(owner, lease_id, lease.parties.get(user=owner).id).status_code == 201
    assert _sign(tenant, lease_id, lease.parties.get(user=tenant).id).status_code == 201
    return lease_id


def test_activation_and_termination_follow_authority() -> None:
    owner = _user("owner")
    tenant = _user("tenant")
    manager = _user("manager")
    prop = _unit(owner, "Kisumu synthetic court")
    organization = _organization()
    _member(manager, organization, role="manager")
    _agreement(owner, manager, prop, organization, execute=True)
    lease_id = _executed(owner, tenant, prop)

    too_soon = _client(owner).post(f"{LEASES}{lease_id}/activate/", {}, format="json")
    assert too_soon.status_code == 409
    blocked = _client(manager).post(f"{LEASES}{lease_id}/activate/", {}, format="json")
    assert blocked.status_code == 403
    assert Lease.objects.get(pk=lease_id).status == LeaseStatus.FULLY_EXECUTED

    _ready(owner, lease_id)
    activated = _client(owner).post(f"{LEASES}{lease_id}/activate/", {}, format="json")
    assert activated.status_code == 200
    assert activated.json()["status"] == LeaseStatus.ACTIVE
    assert activated.json()["contract_data_hash"]
    profile = UnitPhysicalProfile.objects.get(property_id=prop.id)
    assert profile.physical_state == PhysicalState.OCCUPIED_NORMAL

    early = _client(owner).post(
        f"{LEASES}{lease_id}/terminate/",
        {"termination_date": "2026-08-01", "reason": "Before the start."},
        format="json",
    )
    assert early.status_code == 400
    tenant_stop = _client(tenant).post(
        f"{LEASES}{lease_id}/terminate/",
        {"termination_date": "2026-11-15", "reason": "Tenant cannot execute this."},
        format="json",
    )
    assert tenant_stop.status_code == 403
    manager_stop = _client(manager).post(
        f"{LEASES}{lease_id}/terminate/",
        {"termination_date": "2026-11-15", "reason": "Manager lacks leases.terminate."},
        format="json",
    )
    assert manager_stop.status_code == 403

    requested = _client(tenant).post(
        f"{LEASES}{lease_id}/termination-requests/",
        {"termination_date": "2026-11-15", "reason": "Relocating for work."},
        format="json",
    )
    assert requested.status_code == 200
    repeat = _client(tenant).post(
        f"{LEASES}{lease_id}/termination-requests/",
        {"termination_date": "2026-11-15", "reason": "Relocating for work."},
        format="json",
    )
    assert repeat.status_code == 200
    assert repeat.json()["id"] == requested.json()["id"]
    lease = Lease.objects.get(pk=lease_id)
    assert lease.status == LeaseStatus.ACTIVE
    assert lease.events.filter(action=TenancyEventAction.LEASE_TERMINATION_REQUESTED).count() == 1

    approved_hash = lease.contract_data_hash
    terminated = _client(owner).post(
        f"{LEASES}{lease_id}/terminate/",
        {"termination_date": "2026-11-15", "reason": "Owner accepts the request."},
        format="json",
    )
    assert terminated.status_code == 200
    assert terminated.json()["status"] == LeaseStatus.TERMINATED_EARLY
    assert terminated.json()["effective_end_date"] == "2026-11-16"
    assert terminated.json()["term_end_date"] == TERM_END
    assert terminated.json()["contract_data_hash"] == approved_hash
    assert terminated.json()["rent_amount_minor"] == 4_500_000
    history = _client(tenant).get(f"{LEASES}{lease_id}/history/").json()
    actions = {row["action"] for row in history["results"]}
    assert TenancyEventAction.LEASE_TERMINATED in actions
    assert TenancyEventAction.LEASE_TERMINATION_REQUESTED in actions


def test_one_owner_manages_nairobi_and_kisumu_without_a_region_filter() -> None:
    owner = _user("owner")
    tenant_a = _user("tenant-a")
    tenant_b = _user("tenant-b")
    nairobi = _unit(owner, "Nairobi synthetic block")
    kisumu = _unit(owner, "Kisumu synthetic block")
    first = _create(owner, nairobi, tenant_a)
    second = _create(owner, kisumu, tenant_b)
    assert first.status_code == 201
    assert second.status_code == 201
    listed = _client(owner).get(LEASES).json()["results"]
    properties = {row["property_id"] for row in listed}
    assert str(nairobi.id) in properties
    assert str(kisumu.id) in properties
    summary = _client(owner).get(SUMMARY)
    assert summary.status_code == 200
    payload = summary.json()
    assert payload["property_count"] >= 2
    assert payload["lease_count"] >= 2
    assert "user_id" not in payload
    assert "results" not in payload
    nairobi_only = _client(owner).get(LEASES, {"property_id": str(nairobi.id)}).json()
    assert {row["property_id"] for row in nairobi_only["results"]} == {str(nairobi.id)}
