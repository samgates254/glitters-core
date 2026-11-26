"""Authorized cancellation of drafts and unsigned reservations."""

from __future__ import annotations

import uuid

import pytest
from tests.integration.test_tenancy_api import (
    LEASES,
    _agreement,
    _approve,
    _client,
    _create,
    _executed,
    _member,
    _organization,
    _ready,
    _unit,
    _user,
)

from apps.tenancy.domain import LeaseStatus, TenancyEventAction
from apps.tenancy.models import Lease

pytestmark = pytest.mark.django_db


def _cancel(actor, lease_id, reason="Owner withdrew the offer.", key=None):
    body = {"reason": reason}
    if key is not None:
        body["idempotency_key"] = key
    return _client(actor).post(f"{LEASES}{lease_id}/cancel/", body, format="json")


def test_anonymous_cancellation_is_rejected() -> None:
    assert _client().post(f"{LEASES}{uuid.uuid4()}/cancel/", {}, format="json").status_code == 401


def test_owner_cancels_a_draft_without_rewriting_terms() -> None:
    owner = _user("owner")
    tenant = _user("tenant")
    prop = _unit(owner, "Nairobi synthetic cancellation")
    created = _create(owner, prop, tenant)
    assert created.status_code == 201
    lease_id = created.json()["id"]
    original = Lease.objects.get(pk=lease_id)
    original_rent = original.rent_amount_minor
    original_hash = original.contract_data_hash
    original_end = original.effective_end_date
    original_term = original.term_end_date

    blank = _cancel(owner, lease_id, reason="   ")
    assert blank.status_code == 400
    assert Lease.objects.get(pk=lease_id).status == LeaseStatus.DRAFT

    cancelled = _cancel(owner, lease_id, key="cancel-draft")
    assert cancelled.status_code == 200
    body = cancelled.json()
    assert body["status"] == LeaseStatus.CANCELLED
    assert body["rent_amount_minor"] == original_rent
    assert body["contract_data_hash"] == original_hash
    assert body["effective_end_date"] is None
    assert body["term_end_date"] == original_term.isoformat()
    assert "idempotency_key" not in body
    assert "ip_address" not in body
    assert "user_agent" not in body

    replay = _cancel(owner, lease_id, key="cancel-draft")
    assert replay.status_code == 200
    assert replay.json()["id"] == lease_id
    again = _cancel(owner, lease_id)
    assert again.status_code == 409
    other_key = _cancel(owner, lease_id, key="cancel-draft-other")
    assert other_key.status_code == 409

    lease = Lease.objects.get(pk=lease_id)
    assert lease.status == LeaseStatus.CANCELLED
    assert lease.rent_amount_minor == original_rent
    assert lease.contract_data_hash == original_hash
    assert lease.effective_end_date == original_end
    assert lease.term_end_date == original_term
    events = lease.events.filter(action=TenancyEventAction.LEASE_CANCELLED)
    assert events.count() == 1
    metadata = events.get().metadata
    assert metadata["previous_status"] == LeaseStatus.DRAFT
    assert metadata["reason"] == "Owner withdrew the offer."
    assert metadata["contract_data_hash"] == original_hash
    assert "ip_address" not in metadata
    assert "user_agent" not in metadata
    history = _client(owner).get(f"{LEASES}{lease_id}/history/").json()
    assert all("idempotency_key" not in row for row in history["results"])
    tenant_replay = _cancel(tenant, lease_id, key="cancel-draft")
    assert tenant_replay.status_code == 403
    assert lease.events.filter(action=TenancyEventAction.LEASE_CANCELLED).count() == 1


def test_cancelling_a_reservation_releases_the_dates() -> None:
    owner = _user("owner")
    tenant = _user("tenant")
    other = _user("other")
    prop = _unit(owner, "Kisumu synthetic reservation")
    reserved_id = _create(owner, prop, tenant).json()["id"]
    waiting_id = _create(owner, prop, other).json()["id"]
    approved = _approve(owner, reserved_id)
    assert approved.status_code == 200
    approved_body = approved.json()
    assert approved_body["effective_end_date"] == "2027-09-01"
    assert _approve(owner, waiting_id).status_code == 409

    cancelled = _cancel(owner, reserved_id, reason="Reservation withdrawn before signing.")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == LeaseStatus.CANCELLED
    assert cancelled.json()["contract_data_hash"] == approved_body["contract_data_hash"]
    assert cancelled.json()["rent_amount_minor"] == approved_body["rent_amount_minor"]
    assert cancelled.json()["effective_end_date"] == "2027-09-01"
    assert cancelled.json()["term_end_date"] == approved_body["term_end_date"]
    reservation = _client(owner).get(f"{LEASES}{reserved_id}/reservation/").json()
    assert reservation["occupancy_reserving"] is False

    released = _approve(owner, waiting_id)
    assert released.status_code == 200
    assert released.json()["status"] == LeaseStatus.PENDING_SIGNATURE
    reserved = Lease.objects.get(pk=reserved_id)
    assert reserved.status == LeaseStatus.CANCELLED
    assert reserved.contract_data_hash == approved_body["contract_data_hash"]
    assert reserved.rent_amount_minor == approved_body["rent_amount_minor"]
    assert reserved.effective_end_date.isoformat() == "2027-09-01"


def test_executed_and_active_leases_cannot_be_cancelled() -> None:
    owner = _user("owner")
    tenant = _user("tenant")
    prop = _unit(owner)
    executed_id = _executed(owner, tenant, prop)
    executed = Lease.objects.get(pk=executed_id)
    digest = executed.contract_data_hash
    refused = _cancel(owner, executed_id, reason="This must use termination.")
    assert refused.status_code == 409
    executed.refresh_from_db()
    assert executed.status == LeaseStatus.FULLY_EXECUTED
    assert executed.contract_data_hash == digest
    assert executed.events.filter(action=TenancyEventAction.LEASE_CANCELLED).count() == 0

    _ready(owner, executed_id)
    activated = _client(owner).post(f"{LEASES}{executed_id}/activate/", {}, format="json")
    assert activated.status_code == 200
    active_hash = activated.json()["contract_data_hash"]
    still_refused = _cancel(owner, executed_id, reason="Active occupancy is not a draft.")
    assert still_refused.status_code == 409
    executed.refresh_from_db()
    assert executed.status == LeaseStatus.ACTIVE
    assert executed.contract_data_hash == active_hash
    assert executed.events.filter(action=TenancyEventAction.LEASE_CANCELLED).count() == 0


def test_cancellation_follows_preparation_authority() -> None:
    owner = _user("owner")
    tenant = _user("tenant")
    manager = _user("manager")
    clerk = _user("clerk")
    outsider = _user("outsider")
    staff = _user("staff")
    staff.is_staff = True
    staff.save(update_fields=["is_staff"])
    prop = _unit(owner, "Nairobi synthetic managed unit")
    organization = _organization()
    other = _organization("Other PM")
    _member(owner, organization, role="owner")
    _member(manager, organization, role="manager")
    _member(clerk, organization, role="member")
    _member(outsider, other, role="admin")
    _agreement(owner, manager, prop, organization)

    created = _create(owner, prop, tenant, organization_id=str(organization.id))
    assert created.status_code == 201
    lease_id = created.json()["id"]
    assert created.json()["managing_organization_id"] == str(organization.id)
    digest = Lease.objects.get(pk=lease_id).contract_data_hash

    assert _cancel(tenant, lease_id).status_code == 403
    assert _cancel(clerk, lease_id).status_code == 404
    assert _cancel(outsider, lease_id).status_code == 404
    assert _cancel(staff, lease_id).status_code == 404
    assert Lease.objects.get(pk=lease_id).status == LeaseStatus.DRAFT

    cancelled = _cancel(manager, lease_id, reason="Managing preparer withdrew the draft.")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == LeaseStatus.CANCELLED
    lease = Lease.objects.get(pk=lease_id)
    assert lease.created_by_id == owner.id
    assert lease.contract_data_hash == digest
    assert lease.rent_amount_minor == 4_500_000
    assert lease.events.filter(action=TenancyEventAction.LEASE_CANCELLED).count() == 1
