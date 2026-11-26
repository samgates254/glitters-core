"""Rent schedules, invoices, allocations, deposits, arrears, and reversals."""

from __future__ import annotations

import inspect
import threading
from pathlib import Path

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from tests.integration.test_tenancy_api import (
    LEASES,
    _agreement,
    _client,
    _create,
    _executed,
    _member,
    _organization,
    _unit,
    _user,
)

from apps.tenancy.domain import LeaseStatus, ScheduleStatus
from apps.tenancy.models import (
    Lease,
    RentalLedgerEntry,
    RentAllocation,
    RentInvoice,
    RentScheduleEntry,
)
from apps.tenancy.services import billing

pytestmark = pytest.mark.django_db

RENT = 4_500_000
SERVICE = 100_000


def _billing(lease_id, suffix: str) -> str:
    return f"{LEASES}{lease_id}/billing/{suffix}"


def _signed(owner, tenant, prop, **extra) -> str:
    return (
        _executed(owner, tenant, prop) if not extra else _signed_with(owner, tenant, prop, **extra)
    )


def _signed_with(owner, tenant, prop, **extra) -> str:
    created = _create(owner, prop, tenant, **extra)
    assert created.status_code == 201
    lease_id = created.json()["id"]
    from tests.integration.test_tenancy_api import _approve, _sign

    assert _approve(owner, lease_id).status_code == 200
    lease = Lease.objects.get(pk=lease_id)
    assert _sign(owner, lease_id, lease.parties.get(user=owner).id).status_code == 201
    assert _sign(tenant, lease_id, lease.parties.get(user=tenant).id).status_code == 201
    return lease_id


def test_billing_module_does_not_call_lease_transitions() -> None:
    source = Path(inspect.getfile(billing)).read_text(encoding="utf-8")
    assert "services.leases" not in source
    for name in (
        "approve_lease_terms",
        "sign_lease",
        "activate_lease",
        "terminate_lease",
        "cancel_lease",
    ):
        assert name not in source


def test_schedule_is_monthly_idempotent_and_bounded() -> None:
    owner = _user("owner")
    tenant = _user("tenant")
    prop = _unit(owner, "Nairobi synthetic rent roll")
    lease_id = _signed_with(
        owner,
        tenant,
        prop,
        service_charge_minor=SERVICE,
        payment_due_day=5,
        deposit_amount_minor=9_000_000,
    )
    signed = Lease.objects.get(pk=lease_id)
    digest = signed.contract_data_hash
    assert signed.status == LeaseStatus.FULLY_EXECUTED

    draft = _create(owner, _unit(owner), _user("other"))
    refused = _client(owner).post(_billing(draft.json()["id"], "schedule/"), {}, format="json")
    assert refused.status_code == 409
    assert RentScheduleEntry.objects.filter(lease_id=draft.json()["id"]).count() == 0

    client = _client(owner)
    first = client.post(
        _billing(lease_id, "schedule/"),
        {"idempotency_key": "schedule-window-one"},
        format="json",
    )
    assert first.status_code == 201
    body = first.json()
    assert body["entry_count"] == 12
    assert "schedule-window-one" not in first.content.decode()
    assert "ip_address" not in body
    totals = {row["total_due_minor"] for row in body["entries"]}
    assert totals == {RENT + SERVICE}
    assert body["entries"][0]["period_start"] == "2026-09-01"
    assert body["entries"][0]["due_date"] == "2026-09-05"
    assert body["entries"][0]["period_end"] == "2026-09-30"
    assert body["entries"][-1]["period_start"] == "2027-08-01"
    assert body["entries"][-1]["period_end"] == "2027-08-31"
    assert body["entries"][-1]["due_date"] == "2027-08-05"
    sequences = [row["sequence_number"] for row in body["entries"]]
    assert sequences == list(range(1, 13))

    replay = client.post(
        _billing(lease_id, "schedule/"),
        {"idempotency_key": "schedule-window-one"},
        format="json",
    )
    assert replay.status_code == 200
    assert [row["id"] for row in replay.json()["entries"]] == [row["id"] for row in body["entries"]]
    moved = client.post(
        _billing(lease_id, "schedule/"),
        {"through_date": "2026-10-31", "idempotency_key": "schedule-window-one"},
        format="json",
    )
    assert moved.status_code == 409
    assert RentScheduleEntry.objects.filter(lease_id=lease_id).count() == 12

    again = client.post(_billing(lease_id, "schedule/"), {}, format="json")
    assert again.status_code == 200
    assert again.json()["entry_count"] == 12
    lease = Lease.objects.get(pk=lease_id)
    assert lease.status == LeaseStatus.FULLY_EXECUTED
    assert lease.contract_data_hash == digest
    assert lease.rent_amount_minor == RENT
    history = client.get(f"{LEASES}{lease_id}/history/").json()
    assert "schedule-window-one" not in str(history)


def test_open_ended_schedule_requires_a_window_and_extends_without_duplicates() -> None:
    owner = _user("owner")
    tenant = _user("tenant")
    prop = _unit(owner, "Kisumu synthetic periodic rent")
    lease_id = _signed_with(
        owner,
        tenant,
        prop,
        lease_type="residential_periodic",
        term_end_date=None,
        payment_due_day=28,
    )
    client = _client(owner)
    missing = client.post(_billing(lease_id, "schedule/"), {}, format="json")
    assert missing.status_code == 400
    assert RentScheduleEntry.objects.filter(lease_id=lease_id).count() == 0

    too_long = client.post(
        _billing(lease_id, "schedule/"),
        {"through_date": "2029-09-30"},
        format="json",
    )
    assert too_long.status_code == 400
    assert RentScheduleEntry.objects.filter(lease_id=lease_id).count() == 0

    first = client.post(
        _billing(lease_id, "schedule/"),
        {"through_date": "2026-10-31"},
        format="json",
    )
    assert first.status_code == 201
    assert first.json()["entry_count"] == 2
    second = client.post(
        _billing(lease_id, "schedule/"),
        {"through_date": "2026-12-31"},
        format="json",
    )
    assert second.status_code == 201
    assert second.json()["entry_count"] == 4
    assert [row["sequence_number"] for row in second.json()["entries"]] == [1, 2, 3, 4]
    assert RentScheduleEntry.objects.filter(lease_id=lease_id).count() == 4
    assert Lease.objects.get(pk=lease_id).term_end_date is None


def test_partial_month_keeps_the_full_rent_and_clamps_the_due_day() -> None:
    owner = _user("owner")
    tenant = _user("tenant")
    prop = _unit(owner)
    lease_id = _signed_with(
        owner,
        tenant,
        prop,
        start_date="2026-09-20",
        term_end_date="2026-09-25",
        payment_due_day=28,
        rent_amount_minor=RENT,
    )
    created = _client(owner).post(_billing(lease_id, "schedule/"), {}, format="json")
    assert created.status_code == 201
    row = created.json()["entries"][0]
    assert created.json()["entry_count"] == 1
    assert row["period_start"] == "2026-09-20"
    assert row["period_end"] == "2026-09-25"
    assert row["due_date"] == "2026-09-25"
    assert row["base_rent_minor"] == RENT
    assert row["total_due_minor"] == RENT


def test_invoices_allocations_arrears_and_reversals() -> None:
    owner = _user("owner")
    tenant = _user("tenant")
    stranger = _user("stranger")
    prop = _unit(owner, "Nairobi synthetic ledger")
    lease_id = _signed(owner, tenant, prop)
    owner_client = _client(owner)
    assert owner_client.post(_billing(lease_id, "schedule/"), {}, format="json").status_code == 201
    issued = owner_client.post(
        _billing(lease_id, "invoices/"),
        {"as_of": "2026-09-01", "idempotency_key": "invoice-september"},
        format="json",
    )
    assert issued.status_code == 201
    assert issued.json()["invoice_count"] == 1
    invoice = issued.json()["invoices"][0]
    assert invoice["due_date"] == "2026-09-01"
    assert invoice["total_minor"] == RENT
    assert invoice["balance_minor"] == RENT
    assert "invoice-september" not in issued.content.decode()
    replay = owner_client.post(
        _billing(lease_id, "invoices/"),
        {"as_of": "2026-09-01", "idempotency_key": "invoice-september"},
        format="json",
    )
    assert replay.status_code == 200
    assert replay.json()["invoices"][0]["id"] == invoice["id"]
    other_key = owner_client.post(
        _billing(lease_id, "invoices/"),
        {"as_of": "2026-09-01", "idempotency_key": "invoice-again"},
        format="json",
    )
    assert other_key.status_code == 400
    assert RentInvoice.objects.filter(lease_id=lease_id).count() == 1
    entry = RentScheduleEntry.objects.get(lease_id=lease_id, sequence_number=1)
    assert entry.status == ScheduleStatus.BILLED

    early = owner_client.get(_billing(lease_id, "arrears/?as_of=2026-08-31"))
    assert early.status_code == 200
    assert early.json()["arrears_minor"] == 0
    due = owner_client.get(_billing(lease_id, "arrears/?as_of=2026-09-01"))
    assert due.json()["arrears_minor"] == RENT

    tenant_client = _client(tenant)
    assert (
        tenant_client.post(
            _billing(lease_id, f"invoices/{invoice['id']}/allocations/"),
            {"amount_minor": 1, "allocated_on": "2026-09-01", "idempotency_key": "tenant-pay"},
            format="json",
        ).status_code
        == 403
    )
    assert _client().get(_billing(lease_id, "arrears/?as_of=2026-09-01")).status_code == 401
    assert _client(stranger).get(_billing(lease_id, "arrears/?as_of=2026-09-01")).status_code == 404

    not_a_number = owner_client.post(
        _billing(lease_id, f"invoices/{invoice['id']}/allocations/"),
        {"amount_minor": True, "allocated_on": "2026-09-01", "idempotency_key": "bad-amount"},
        format="json",
    )
    assert not_a_number.status_code == 400

    partial = owner_client.post(
        _billing(lease_id, f"invoices/{invoice['id']}/allocations/"),
        {
            "amount_minor": 2_000_000,
            "allocated_on": "2026-09-03",
            "memo": "Internal cash book",
            "idempotency_key": "alloc-partial",
        },
        format="json",
    )
    assert partial.status_code == 201
    assert partial.json()["balance_minor"] == RENT - 2_000_000
    assert "alloc-partial" not in partial.content.decode()
    same = owner_client.post(
        _billing(lease_id, f"invoices/{invoice['id']}/allocations/"),
        {
            "amount_minor": 2_000_000,
            "allocated_on": "2026-09-03",
            "idempotency_key": "alloc-partial",
        },
        format="json",
    )
    assert same.status_code == 200
    assert same.json()["id"] == partial.json()["id"]
    changed = owner_client.post(
        _billing(lease_id, f"invoices/{invoice['id']}/allocations/"),
        {
            "amount_minor": 1,
            "allocated_on": "2026-09-03",
            "idempotency_key": "alloc-partial",
        },
        format="json",
    )
    assert changed.status_code == 409
    over = owner_client.post(
        _billing(lease_id, f"invoices/{invoice['id']}/allocations/"),
        {
            "amount_minor": RENT,
            "allocated_on": "2026-09-04",
            "idempotency_key": "alloc-over",
        },
        format="json",
    )
    assert over.status_code == 409

    reversal = owner_client.post(
        _billing(lease_id, f"allocations/{partial.json()['id']}/reversals/"),
        {"amount_minor": 500_000, "idempotency_key": "reverse-part", "memo": "Corrected"},
        format="json",
    )
    assert reversal.status_code == 201
    assert reversal.json()["reversal_of"] == partial.json()["id"]
    assert reversal.json()["balance_minor"] == RENT - 1_500_000
    too_much = owner_client.post(
        _billing(lease_id, f"allocations/{partial.json()['id']}/reversals/"),
        {"amount_minor": 2_000_000, "idempotency_key": "reverse-too-much"},
        format="json",
    )
    assert too_much.status_code == 409
    nested = owner_client.post(
        _billing(lease_id, f"allocations/{reversal.json()['id']}/reversals/"),
        {"amount_minor": 1, "idempotency_key": "reverse-the-reversal"},
        format="json",
    )
    assert nested.status_code == 409

    rest = owner_client.post(
        _billing(lease_id, f"invoices/{invoice['id']}/allocations/"),
        {
            "amount_minor": RENT - 1_500_000,
            "allocated_on": "2026-09-06",
            "idempotency_key": "alloc-rest",
        },
        format="json",
    )
    assert rest.status_code == 201
    assert rest.json()["balance_minor"] == 0
    cleared = owner_client.get(_billing(lease_id, "arrears/?as_of=2026-09-06"))
    assert cleared.json()["arrears_minor"] == 0

    undo = owner_client.post(
        _billing(lease_id, f"allocations/{rest.json()['id']}/reversals/"),
        {"amount_minor": RENT - 1_500_000, "idempotency_key": "undo-rest"},
        format="json",
    )
    assert undo.status_code == 201
    restored = owner_client.get(_billing(lease_id, "arrears/?as_of=2026-09-06"))
    assert restored.json()["arrears_minor"] == RENT - 1_500_000

    tenant_view = tenant_client.get(_billing(lease_id, "statements/tenant/?as_of=2026-09-06"))
    assert tenant_view.status_code == 200
    assert tenant_view.json()["arrears_minor"] == RENT - 1_500_000
    assert "Internal cash book" not in tenant_view.content.decode()
    assert "example.com" not in tenant_view.content.decode()
    landlord_denied = tenant_client.get(_billing(lease_id, "statements/landlord/?as_of=2026-09-06"))
    assert landlord_denied.status_code == 403
    assert tenant_client.get(_billing(lease_id, "ledger/")).status_code == 403

    landlord = owner_client.get(_billing(lease_id, "statements/landlord/?as_of=2026-09-06"))
    assert landlord.status_code == 200
    assert landlord.json()["billed_minor"] == RENT
    assert landlord.json()["outstanding_minor"] == RENT - 1_500_000
    assert landlord.json()["arrears_minor"] == RENT - 1_500_000
    assert "idempotency_key" not in landlord.content.decode()
    ledger = owner_client.get(_billing(lease_id, "ledger/"))
    assert ledger.status_code == 200
    kinds = {row["entry_type"] for row in ledger.json()["entries"]}
    assert "invoice_issued" in kinds
    assert "allocation_recorded" in kinds
    assert "allocation_reversed" in kinds
    assert "idempotency_key" not in ledger.content.decode()
    lease = Lease.objects.get(pk=lease_id)
    assert lease.status == LeaseStatus.FULLY_EXECUTED
    with pytest.raises(ValidationError):
        RentAllocation.objects.update(amount_minor=1)
    with pytest.raises(ValidationError):
        RentalLedgerEntry.objects.update(amount_minor=1)


def test_deposit_receipts_are_capped_and_reversible() -> None:
    owner = _user("owner")
    tenant = _user("tenant")
    prop = _unit(owner)
    lease_id = _signed_with(owner, tenant, prop, deposit_amount_minor=9_000_000)
    client = _client(owner)
    missing = client.get(_billing(lease_id, "deposit/"))
    assert missing.status_code == 404
    created = client.post(_billing(lease_id, "deposit/"), {}, format="json")
    assert created.status_code == 201
    assert created.json()["required_minor"] == 9_000_000
    assert created.json()["received_minor"] == 0
    replay = client.post(_billing(lease_id, "deposit/"), {}, format="json")
    assert replay.status_code == 200
    assert replay.json()["id"] == created.json()["id"]

    zero_lease = _signed(owner, tenant, _unit(owner))
    zero = _client(owner).post(_billing(zero_lease, "deposit/"), {}, format="json")
    assert zero.status_code == 201
    assert zero.json()["required_minor"] == 0
    refused = _client(owner).post(
        _billing(zero_lease, "deposit/receipts/"),
        {"amount_minor": 1, "received_on": "2026-09-01", "idempotency_key": "zero-deposit"},
        format="json",
    )
    assert refused.status_code == 409

    first = client.post(
        _billing(lease_id, "deposit/receipts/"),
        {
            "amount_minor": 4_000_000,
            "received_on": "2026-09-02",
            "memo": "Cash book",
            "idempotency_key": "deposit-part",
        },
        format="json",
    )
    assert first.status_code == 201
    assert first.json()["received_minor"] == 4_000_000
    again = client.post(
        _billing(lease_id, "deposit/receipts/"),
        {
            "amount_minor": 4_000_000,
            "received_on": "2026-09-02",
            "idempotency_key": "deposit-part",
        },
        format="json",
    )
    assert again.status_code == 200
    assert again.json()["id"] == first.json()["id"]
    excess = client.post(
        _billing(lease_id, "deposit/receipts/"),
        {"amount_minor": 6_000_000, "received_on": "2026-09-03", "idempotency_key": "deposit-over"},
        format="json",
    )
    assert excess.status_code == 409
    undone = client.post(
        _billing(lease_id, f"deposit/receipts/{first.json()['id']}/reversals/"),
        {"amount_minor": 4_000_000, "idempotency_key": "deposit-undo"},
        format="json",
    )
    assert undone.status_code == 201
    assert undone.json()["received_minor"] == 0
    replaced = client.post(
        _billing(lease_id, "deposit/receipts/"),
        {"amount_minor": 9_000_000, "received_on": "2026-09-04", "idempotency_key": "deposit-full"},
        format="json",
    )
    assert replaced.status_code == 201
    assert replaced.json()["received_minor"] == 9_000_000
    statement = _client(tenant).get(_billing(lease_id, "statements/tenant/?as_of=2026-09-04"))
    assert statement.status_code == 200
    assert statement.json()["deposit"]["received_minor"] == 9_000_000
    assert "Cash book" not in statement.content.decode()
    landlord = client.get(_billing(lease_id, "statements/landlord/?as_of=2026-09-04"))
    assert landlord.status_code == 200
    assert landlord.json()["deposit"]["received_minor"] == 9_000_000


def test_manager_can_bill_and_termination_does_not_rebuild_cancelled_periods() -> None:
    owner = _user("owner")
    tenant = _user("tenant")
    manager = _user("manager")
    outsider = _user("outsider")
    prop = _unit(owner, "Nakuru synthetic billing")
    organization = _organization()
    _member(manager, organization, role="manager")
    _agreement(owner, manager, prop, organization, execute=False)
    lease_id = _signed(owner, tenant, prop)
    digest = Lease.objects.get(pk=lease_id).contract_data_hash
    blocked = _client(outsider).post(_billing(lease_id, "schedule/"), {}, format="json")
    assert blocked.status_code == 404
    assert (
        _client(tenant).post(_billing(lease_id, "schedule/"), {}, format="json").status_code == 403
    )
    no_agreement = _user("nomandate")
    other = _organization("Other")
    _member(no_agreement, other, role="manager")
    denied = _client(no_agreement).post(_billing(lease_id, "schedule/"), {}, format="json")
    assert denied.status_code == 404

    scheduled = _client(manager).post(_billing(lease_id, "schedule/"), {}, format="json")
    assert scheduled.status_code == 201
    september = RentScheduleEntry.objects.get(lease_id=lease_id, sequence_number=1)
    invoiced = _client(manager).post(
        _billing(lease_id, "invoices/"),
        {"schedule_entry_id": str(september.id), "idempotency_key": "bill-september"},
        format="json",
    )
    assert invoiced.status_code == 201
    stopped = _client(owner).post(
        f"{LEASES}{lease_id}/terminate/",
        {"termination_date": "2026-11-01", "reason": "Ended before the later rent fell due."},
        format="json",
    )
    assert stopped.status_code == 200
    assert stopped.json()["status"] == LeaseStatus.TERMINATED_EARLY
    assert stopped.json()["contract_data_hash"] == digest
    september.refresh_from_db()
    assert september.status == ScheduleStatus.BILLED
    november = RentScheduleEntry.objects.get(lease_id=lease_id, period_start="2026-11-01")
    assert november.status == ScheduleStatus.CANCELLED
    october = RentScheduleEntry.objects.get(lease_id=lease_id, period_start="2026-10-01")
    assert october.status == ScheduleStatus.SCHEDULED

    rebuilt = _client(owner).post(_billing(lease_id, "schedule/"), {}, format="json")
    assert rebuilt.status_code == 200
    assert (
        RentScheduleEntry.objects.filter(lease_id=lease_id, period_start="2026-11-01").count() == 1
    )
    cancelled = _client(owner).post(
        _billing(lease_id, "invoices/"),
        {"schedule_entry_id": str(november.id), "idempotency_key": "bill-cancelled"},
        format="json",
    )
    assert cancelled.status_code == 409
    lease = Lease.objects.get(pk=lease_id)
    assert lease.status == LeaseStatus.TERMINATED_EARLY
    assert lease.contract_data_hash == digest
    assert lease.effective_end_date.isoformat() == "2026-11-02"


@pytest.mark.django_db(transaction=True)
def test_concurrent_allocations_cannot_oversubscribe_an_invoice() -> None:
    owner = _user("owner")
    tenant = _user("tenant")
    prop = _unit(owner)
    lease_id = _signed(owner, tenant, prop)
    assert (
        _client(owner).post(_billing(lease_id, "schedule/"), {}, format="json").status_code == 201
    )
    issued = _client(owner).post(
        _billing(lease_id, "invoices/"),
        {"as_of": "2026-09-01", "idempotency_key": "concurrent-invoice"},
        format="json",
    )
    assert issued.status_code == 201
    invoice_id = issued.json()["invoices"][0]["id"]
    results: list[int] = []

    def attempt(key: str) -> None:
        try:
            response = _client(owner).post(
                _billing(lease_id, f"invoices/{invoice_id}/allocations/"),
                {
                    "amount_minor": RENT,
                    "allocated_on": "2026-09-02",
                    "idempotency_key": key,
                },
                format="json",
            )
            results.append(response.status_code)
        finally:
            connection.close()

    threads = [
        threading.Thread(target=attempt, args=("alloc-a",)),
        threading.Thread(target=attempt, args=("alloc-b",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == [201, 409]
    assert (
        RentAllocation.objects.filter(invoice_id=invoice_id, reversal_of__isnull=True).count() == 1
    )
    invoice = RentInvoice.objects.get(pk=invoice_id)
    assert (
        invoice.total_minor
        - sum(row.amount_minor for row in invoice.allocations.all() if row.reversal_of_id is None)
        == 0
    )


@pytest.mark.django_db(transaction=True)
def test_concurrent_deposit_receipts_cannot_exceed_the_obligation() -> None:
    owner = _user("owner")
    tenant = _user("tenant")
    prop = _unit(owner)
    lease_id = _signed_with(owner, tenant, prop, deposit_amount_minor=9_000_000)
    assert _client(owner).post(_billing(lease_id, "deposit/"), {}, format="json").status_code == 201
    results: list[int] = []

    def attempt(key: str) -> None:
        try:
            response = _client(owner).post(
                _billing(lease_id, "deposit/receipts/"),
                {
                    "amount_minor": 9_000_000,
                    "received_on": "2026-09-02",
                    "idempotency_key": key,
                },
                format="json",
            )
            results.append(response.status_code)
        finally:
            connection.close()

    threads = [
        threading.Thread(target=attempt, args=("receipt-a",)),
        threading.Thread(target=attempt, args=("receipt-b",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == [201, 409]
    from apps.tenancy.models import DepositReceipt

    receipts = DepositReceipt.objects.filter(
        deposit_obligation__lease_id=lease_id,
        reversal_of__isnull=True,
    )
    assert receipts.count() == 1
    assert receipts.get().amount_minor == 9_000_000
