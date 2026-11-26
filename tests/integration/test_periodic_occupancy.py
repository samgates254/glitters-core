"""Open-ended periodic leases reserve occupancy without a finite end."""

from __future__ import annotations

import threading
from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from tests.integration.test_tenancy_api import (
    LEASES,
    START,
    TERM_END,
    _approve,
    _client,
    _create,
    _ready,
    _sign,
    _unit,
    _user,
)

from apps.tenancy.domain import LeaseStatus
from apps.tenancy.models import Lease

pytestmark = pytest.mark.django_db


def _periodic(owner, prop, tenant, **extra):
    return _create(
        owner,
        prop,
        tenant,
        lease_type="residential_periodic",
        term_end_date=None,
        **extra,
    )


def _active_periodic(owner, tenant, prop) -> str:
    created = _periodic(owner, prop, tenant)
    assert created.status_code == 201
    lease_id = created.json()["id"]
    assert _approve(owner, lease_id).status_code == 200
    lease = Lease.objects.get(pk=lease_id)
    assert _sign(owner, lease_id, lease.parties.get(user=owner).id).status_code == 201
    assert _sign(tenant, lease_id, lease.parties.get(user=tenant).id).status_code == 201
    _ready(owner, lease_id)
    activated = _client(owner).post(f"{LEASES}{lease_id}/activate/", {}, format="json")
    assert activated.status_code == 200
    return lease_id


def test_fixed_lease_still_requires_a_term_end() -> None:
    owner = _user("owner")
    tenant = _user("tenant")
    prop = _unit(owner)
    refused = _create(owner, prop, tenant, term_end_date=None)
    assert refused.status_code == 400
    assert Lease.objects.filter(property=prop).count() == 0


def test_open_ended_approval_reserves_and_blocks_later_dates() -> None:
    owner = _user("owner")
    tenant = _user("tenant")
    other = _user("other")
    prop = _unit(owner, "Nairobi synthetic periodic")
    commercial = _create(
        owner,
        _unit(owner, "Kisumu synthetic periodic"),
        other,
        lease_type="commercial_periodic",
        term_end_date=None,
    )
    assert commercial.status_code == 201
    assert commercial.json()["term_end_date"] is None
    assert commercial.json()["effective_end_date"] is None

    created = _periodic(owner, prop, tenant)
    assert created.status_code == 201
    lease_id = created.json()["id"]
    reservation = _client(owner).get(f"{LEASES}{lease_id}/reservation/").json()
    assert reservation["occupancy_reserving"] is False
    assert reservation["open_ended"] is False

    approved = _approve(owner, lease_id)
    assert approved.status_code == 200
    body = approved.json()
    assert body["status"] == LeaseStatus.PENDING_SIGNATURE
    assert body["term_end_date"] is None
    assert body["effective_end_date"] is None
    assert len(body["contract_data_hash"]) == 64
    reserved = _client(owner).get(f"{LEASES}{lease_id}/reservation/").json()
    assert reserved["occupancy_reserving"] is True
    assert reserved["open_ended"] is True

    later = _create(
        owner,
        prop,
        other,
        start_date="2028-01-01",
        term_end_date="2028-12-31",
    )
    assert later.status_code == 201
    conflict = _approve(owner, later.json()["id"])
    assert conflict.status_code == 409
    assert Lease.objects.get(pk=later.json()["id"]).status == LeaseStatus.DRAFT
    original = Lease.objects.get(pk=lease_id)
    assert original.effective_end_date is None
    assert original.contract_data_hash == body["contract_data_hash"]

    with pytest.raises(ValidationError):
        original.effective_end_date = original.start_date + timedelta(days=400)
        original.save()
    original.refresh_from_db()
    with pytest.raises(ValidationError):
        Lease.objects.filter(pk=lease_id).update(effective_end_date=None)
    original.refresh_from_db()
    assert original.effective_end_date is None
    assert original.status == LeaseStatus.PENDING_SIGNATURE


def test_periodic_dates_follow_half_open_boundaries() -> None:
    owner = _user("owner")
    tenant = _user("tenant")
    nxt = _user("next")
    early = _user("early")
    prop = _unit(owner, "Kisumu synthetic boundary")
    fixed = _create(owner, prop, tenant)
    assert fixed.status_code == 201
    assert _approve(owner, fixed.json()["id"]).status_code == 200

    too_soon = _periodic(owner, prop, early, start_date="2027-08-31")
    assert too_soon.status_code == 201
    assert _approve(owner, too_soon.json()["id"]).status_code == 409

    adjacent = _periodic(owner, prop, nxt, start_date="2027-09-01")
    assert adjacent.status_code == 201
    approved = _approve(owner, adjacent.json()["id"])
    assert approved.status_code == 200
    assert approved.json()["effective_end_date"] is None
    assert approved.json()["start_date"] == "2027-09-01"
    assert Lease.objects.get(pk=fixed.json()["id"]).effective_end_date.isoformat() == "2027-09-01"
    assert TERM_END == "2027-08-31"
    assert START == "2026-09-01"


@pytest.mark.django_db(transaction=True)
def test_concurrent_open_ended_approvals_reserve_one_lease() -> None:
    owner = _user("owner")
    first_tenant = _user("tenant-a")
    second_tenant = _user("tenant-b")
    prop = _unit(owner)
    first = _periodic(owner, prop, first_tenant).json()["id"]
    second = _periodic(owner, prop, second_tenant, start_date="2027-01-01").json()["id"]
    results: list[int] = []

    def attempt(lease_id: str) -> None:
        try:
            results.append(_approve(owner, lease_id).status_code)
        finally:
            connection.close()

    threads = [
        threading.Thread(target=attempt, args=(first,)),
        threading.Thread(target=attempt, args=(second,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == [200, 409]
    reserved = Lease.objects.filter(property=prop, status=LeaseStatus.PENDING_SIGNATURE)
    assert reserved.count() == 1
    assert reserved.get().effective_end_date is None


def test_termination_releases_the_open_ended_tail() -> None:
    owner = _user("owner")
    tenant = _user("tenant")
    successor_tenant = _user("successor")
    prop = _unit(owner, "Nairobi synthetic open tail")
    lease_id = _active_periodic(owner, tenant, prop)
    signed = Lease.objects.get(pk=lease_id)
    digest = signed.contract_data_hash
    assert signed.term_end_date is None
    assert signed.effective_end_date is None

    stopped = _client(owner).post(
        f"{LEASES}{lease_id}/terminate/",
        {"termination_date": "2026-11-15", "reason": "Periodic tenancy ended."},
        format="json",
    )
    assert stopped.status_code == 200
    assert stopped.json()["status"] == LeaseStatus.TERMINATED_EARLY
    assert stopped.json()["effective_end_date"] == "2026-11-16"
    assert stopped.json()["term_end_date"] is None
    assert stopped.json()["contract_data_hash"] == digest
    assert stopped.json()["rent_amount_minor"] == 4_500_000

    overlap = _create(
        owner,
        prop,
        successor_tenant,
        start_date="2026-11-15",
        term_end_date="2027-11-14",
    )
    assert _approve(owner, overlap.json()["id"]).status_code == 409
    released = _create(
        owner,
        prop,
        successor_tenant,
        start_date="2026-11-16",
        term_end_date="2027-11-15",
    )
    approved = _approve(owner, released.json()["id"])
    assert approved.status_code == 200
    assert approved.json()["effective_end_date"] == "2027-11-16"
    signed.refresh_from_db()
    assert signed.contract_data_hash == digest
    assert signed.term_end_date is None
    assert signed.effective_end_date.isoformat() == "2026-11-16"
    with pytest.raises(ValidationError):
        signed.effective_end_date = signed.effective_end_date + timedelta(days=10)
        signed.save()
    signed.refresh_from_db()
    with pytest.raises(ValidationError):
        signed.effective_end_date = None
        signed.save()
    signed.refresh_from_db()
    assert signed.effective_end_date.isoformat() == "2026-11-16"
    assert signed.contract_data_hash == digest
