"""Renewal opens a new lease row and leaves the signed contract alone."""

from __future__ import annotations

import uuid

import pytest
from tests.integration.test_lease_cancellation import _cancel
from tests.integration.test_tenancy_api import (
    LEASES,
    START,
    TERM_END,
    _agreement,
    _approve,
    _client,
    _create,
    _executed,
    _member,
    _organization,
    _ready,
    _sign,
    _unit,
    _user,
)

from apps.tenancy.domain import LeaseStatus, TenancyEventAction
from apps.tenancy.models import Lease

pytestmark = pytest.mark.django_db


def _active(owner, tenant, prop) -> str:
    lease_id = _executed(owner, tenant, prop)
    _ready(owner, lease_id)
    activated = _client(owner).post(f"{LEASES}{lease_id}/activate/", {}, format="json")
    assert activated.status_code == 200
    return lease_id


def _terms(tenant, **overrides) -> dict:
    body = {
        "lease_type": "residential_fixed",
        "start_date": "2027-09-01",
        "term_end_date": "2028-08-31",
        "rent_amount_minor": 5_000_000,
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


def _renew(actor, lease_id, tenant, **extra):
    return _client(actor).post(
        f"{LEASES}{lease_id}/renewals/",
        _terms(tenant, **extra),
        format="json",
    )


def _abandon(actor, lease_id, key=None):
    body = {"reason": "The tenant declined the offered renewal."}
    if key is not None:
        body["idempotency_key"] = key
    return _client(actor).post(f"{LEASES}{lease_id}/renewals/abandon/", body, format="json")


def test_anonymous_renewal_is_rejected() -> None:
    missing = uuid.uuid4()
    anonymous = _client()
    assert anonymous.post(f"{LEASES}{missing}/renewals/", {}, format="json").status_code == 401
    assert (
        anonymous.post(
            f"{LEASES}{missing}/renewals/abandon/",
            {},
            format="json",
        ).status_code
        == 401
    )


def test_renewal_creates_a_new_row_and_keeps_the_signed_digest() -> None:
    owner = _user("owner")
    tenant = _user("tenant")
    prop = _unit(owner, "Nairobi synthetic renewal")
    lease_id = _active(owner, tenant, prop)
    signed = Lease.objects.get(pk=lease_id)
    digest = signed.contract_data_hash
    assert digest
    assert signed.document_version == 1
    assert signed.effective_end_date.isoformat() == "2027-09-01"

    refused = _renew(tenant, lease_id, tenant, idempotency_key="renew-nairobi")
    assert refused.status_code == 403
    assert Lease.objects.filter(supersedes_lease_id=lease_id).count() == 0

    opened = _renew(owner, lease_id, tenant, idempotency_key="renew-nairobi")
    assert opened.status_code == 201
    successor = opened.json()
    assert successor["id"] != lease_id
    assert successor["status"] == LeaseStatus.DRAFT
    assert successor["supersedes_lease_id"] == lease_id
    assert successor["document_version"] == 1
    assert successor["contract_data_hash"] == ""
    assert successor["rent_amount_minor"] == 5_000_000
    assert successor["effective_end_date"] is None
    assert successor["start_date"] == "2027-09-01"

    replay = _renew(owner, lease_id, tenant, idempotency_key="renew-nairobi")
    assert replay.status_code == 200
    assert replay.json()["id"] == successor["id"]
    predecessor = Lease.objects.get(pk=lease_id)
    assert predecessor.status == LeaseStatus.UNDER_RENEWAL
    assert predecessor.contract_data_hash == digest
    assert predecessor.rent_amount_minor == 4_500_000
    assert predecessor.term_end_date.isoformat() == TERM_END
    assert predecessor.effective_end_date.isoformat() == "2027-09-01"
    assert predecessor.document_version == 1
    assert predecessor.events.filter(action=TenancyEventAction.LEASE_RENEWAL_INITIATED).count() == 1

    again = _renew(owner, lease_id, tenant, idempotency_key="renew-nairobi-2")
    assert again.status_code == 409
    assert Lease.objects.filter(supersedes_lease_id=lease_id).count() == 1

    approved = _approve(owner, successor["id"])
    assert approved.status_code == 200
    assert approved.json()["status"] == LeaseStatus.PENDING_SIGNATURE
    assert approved.json()["effective_end_date"] == "2028-09-01"
    predecessor.refresh_from_db()
    assert predecessor.contract_data_hash == digest
    assert predecessor.status == LeaseStatus.UNDER_RENEWAL
    assert predecessor.rent_amount_minor == 4_500_000


def test_an_overlapping_renewal_does_not_reserve_or_rewrite_the_original() -> None:
    owner = _user("owner")
    tenant = _user("tenant")
    prop = _unit(owner, "Kisumu synthetic renewal")
    lease_id = _active(owner, tenant, prop)
    digest = Lease.objects.get(pk=lease_id).contract_data_hash

    opened = _renew(
        owner,
        lease_id,
        tenant,
        start_date=START,
        term_end_date=TERM_END,
        rent_amount_minor=5_100_000,
    )
    assert opened.status_code == 201
    successor_id = opened.json()["id"]
    blocked = _approve(owner, successor_id)
    assert blocked.status_code == 409
    successor = Lease.objects.get(pk=successor_id)
    assert successor.status == LeaseStatus.DRAFT
    assert successor.contract_data_hash == ""
    predecessor = Lease.objects.get(pk=lease_id)
    assert predecessor.status == LeaseStatus.UNDER_RENEWAL
    assert predecessor.contract_data_hash == digest
    assert predecessor.rent_amount_minor == 4_500_000
    assert predecessor.effective_end_date.isoformat() == "2027-09-01"

    blocked_abandon = _abandon(owner, lease_id)
    assert blocked_abandon.status_code == 409
    predecessor.refresh_from_db()
    assert predecessor.status == LeaseStatus.UNDER_RENEWAL

    cancelled = _cancel(owner, successor_id, reason="Overlapping offer withdrawn.")
    assert cancelled.status_code == 200
    predecessor.refresh_from_db()
    assert predecessor.contract_data_hash == digest
    assert predecessor.status == LeaseStatus.UNDER_RENEWAL

    abandoned = _abandon(owner, lease_id, key="abandon-kisumu")
    assert abandoned.status_code == 200
    assert abandoned.json()["status"] == LeaseStatus.ACTIVE
    assert abandoned.json()["contract_data_hash"] == digest
    assert abandoned.json()["rent_amount_minor"] == 4_500_000
    assert abandoned.json()["term_end_date"] == TERM_END
    replay = _abandon(owner, lease_id, key="abandon-kisumu")
    assert replay.status_code == 200
    assert replay.json()["id"] == lease_id
    predecessor.refresh_from_db()
    assert predecessor.status == LeaseStatus.ACTIVE
    assert predecessor.events.filter(action=TenancyEventAction.LEASE_RENEWAL_ABANDONED).count() == 1


def test_unsigned_and_hidden_leases_cannot_be_renewed() -> None:
    owner = _user("owner")
    tenant = _user("tenant")
    stranger = _user("stranger")
    prop = _unit(owner)
    draft_id = _create(owner, prop, tenant).json()["id"]
    assert _renew(owner, draft_id, tenant).status_code == 409
    assert Lease.objects.get(pk=draft_id).status == LeaseStatus.DRAFT

    executed_id = _executed(owner, tenant, prop)
    assert _renew(owner, executed_id, tenant).status_code == 409
    executed = Lease.objects.get(pk=executed_id)
    assert executed.status == LeaseStatus.FULLY_EXECUTED
    assert executed.events.filter(action=TenancyEventAction.LEASE_RENEWAL_INITIATED).count() == 0

    active_id = _active(owner, tenant, _unit(owner, "Nairobi synthetic second"))
    assert _renew(stranger, active_id, tenant).status_code == 404
    assert Lease.objects.get(pk=active_id).status == LeaseStatus.ACTIVE


def test_managing_preparer_can_renew_without_taking_title() -> None:
    owner = _user("owner")
    tenant = _user("tenant")
    manager = _user("manager")
    clerk = _user("clerk")
    prop = _unit(owner, "Nairobi synthetic managed renewal")
    organization = _organization()
    _member(owner, organization, role="owner")
    _member(manager, organization, role="manager")
    _member(clerk, organization, role="member")
    _agreement(owner, manager, prop, organization, execute=True)

    created = _create(owner, prop, tenant, organization_id=str(organization.id))
    assert created.status_code == 201
    lease_id = created.json()["id"]
    assert _approve(owner, lease_id).status_code == 200
    lease = Lease.objects.get(pk=lease_id)
    assert _sign(owner, lease_id, lease.parties.get(user=owner).id).status_code == 201
    assert _sign(tenant, lease_id, lease.parties.get(user=tenant).id).status_code == 201
    _ready(owner, lease_id)
    activated = _client(owner).post(f"{LEASES}{lease_id}/activate/", {}, format="json")
    assert activated.status_code == 200
    digest = Lease.objects.get(pk=lease_id).contract_data_hash

    assert _renew(clerk, lease_id, tenant, organization_id=str(organization.id)).status_code == 404
    hidden = _renew(manager, lease_id, tenant)
    assert hidden.status_code == 403
    opened = _renew(
        manager,
        lease_id,
        tenant,
        organization_id=str(organization.id),
        idempotency_key="managed-renewal",
    )
    assert opened.status_code == 201
    successor = Lease.objects.get(pk=opened.json()["id"])
    assert successor.property_owner_id == owner.id
    assert successor.managing_organization_id == organization.id
    assert successor.created_by_id == manager.id
    assert successor.supersedes_lease_id == Lease.objects.get(pk=lease_id).id
    predecessor = Lease.objects.get(pk=lease_id)
    assert predecessor.property_owner_id == owner.id
    assert predecessor.contract_data_hash == digest
    assert predecessor.status == LeaseStatus.UNDER_RENEWAL
    assert predecessor.rent_amount_minor == 4_500_000
