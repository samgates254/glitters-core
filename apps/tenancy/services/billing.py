"""Transactional rental bookkeeping.

Schedules, invoices, allocations, deposits, arrears, and statements live here.
This module does not approve, sign, activate, terminate, or cancel a lease, and
it does not call a payment provider. Amounts are integer minor units. A reversal
is a new row. Grace days do not delay arrears.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from django.db import IntegrityError, OperationalError, transaction
from django.utils import timezone

from apps.operations.domain import AgreementStatus
from apps.operations.errors import OperationsError
from apps.operations.models import ManagementAgreement
from apps.operations.services.agreements import is_agreement_expired, verify_owner_authority
from apps.organizations.domain import Permission
from apps.organizations.errors import OrganizationError
from apps.organizations.services.authorization import authorize
from apps.properties.services.locking import is_deadlock
from apps.tenancy.domain import (
    NOT_FOUND,
    PERMISSION_DENIED,
    LeasePartyRole,
    LeaseStatus,
    LedgerEntryType,
    ScheduleStatus,
    TenancyEventAction,
)
from apps.tenancy.errors import TenancyError
from apps.tenancy.models import (
    DepositObligation,
    DepositReceipt,
    Lease,
    RentalLedgerEntry,
    RentAllocation,
    RentInvoice,
    RentInvoiceLine,
    RentScheduleEntry,
    TenancyEvent,
)
from apps.tenancy.services.audit import record_tenancy_event, tenancy_idempotency_key
from apps.tenancy.services.authority import require_actor

MAX_SCHEDULE_MONTHS = 36
MAX_SCHEDULE_STEPS = 600
_TENANT_ROLES = frozenset(
    {
        LeasePartyRole.PRIMARY_TENANT,
        LeasePartyRole.CO_TENANT,
        LeasePartyRole.AUTHORIZED_OCCUPANT,
    }
)
_CURRENCY_OK = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ")


@dataclass(frozen=True)
class FinanceResult:
    payload: dict
    created: bool


def generate_rent_schedule(*, actor, lease_id, payload: dict) -> FinanceResult:
    """Create missing monthly obligations through the term, or through a given date."""

    body = _body(payload)
    through_date = _optional_date(body.get("through_date"), "through date")
    client_key = _optional_key(body.get("idempotency_key"))
    with transaction.atomic():
        lease = _prepare_lease(actor, lease_id)
        limit = _schedule_limit(lease, through_date)
        if client_key:
            replayed = _schedule_replay(lease, client_key, limit)
            if replayed is not None:
                _lease_unchanged(lease)
                return FinanceResult(_rows_payload(lease, replayed), created=False)
        created = _create_missing_periods(actor, lease, limit)
        window = _window_rows(lease, limit)
        _record_schedule_event(actor, lease, client_key, limit, window, created)
        _lease_unchanged(lease)
        return FinanceResult(_rows_payload(lease, window), created=bool(created))


def list_rent_schedule(lease: Lease) -> dict:
    rows = list(RentScheduleEntry.objects.filter(lease=lease).order_by("sequence_number"))
    return _rows_payload(lease, rows)


def issue_rent_invoices(*, actor, lease_id, payload: dict) -> FinanceResult:
    """Bill scheduled entries. One invoice lines up with one schedule entry."""

    body = _body(payload)
    as_of = _optional_date(body.get("as_of"), "as of")
    entry_id = body.get("schedule_entry_id")
    if (as_of is None) == (entry_id in (None, "")):
        raise TenancyError(
            "Provide an as of date or one schedule entry.",
            status_code=400,
        )
    client_key = _required_key(body.get("idempotency_key"))
    selector = f"as_of:{as_of.isoformat()}" if as_of else f"entry:{entry_id}"
    with transaction.atomic():
        lease = _prepare_lease(actor, lease_id)
        batch_hash = tenancy_idempotency_key("invoice_batch", lease.id, client_key)
        prior = list(
            RentInvoice.objects.filter(lease=lease, batch_hash=batch_hash).order_by(
                "due_date", "id"
            )
        )
        if prior:
            if any(row.selector != selector for row in prior):
                raise TenancyError("The idempotency key was already used.", status_code=409)
            _lease_unchanged(lease)
            return FinanceResult(_invoice_payload(lease, prior), created=False)
        entries = _entries_to_bill(lease, as_of=as_of, entry_id=entry_id)
        if not entries:
            raise TenancyError("There is no scheduled rent in that window.", status_code=400)
        moment = timezone.now()
        created = []
        for entry in entries:
            created.append(_issue_one(actor, lease, entry, batch_hash, selector, moment))
        _lease_unchanged(lease)
        return FinanceResult(_invoice_payload(lease, created), created=True)


def list_rent_invoices(lease: Lease) -> dict:
    rows = list(RentInvoice.objects.filter(lease=lease).order_by("due_date", "id"))
    return _invoice_payload(lease, rows)


def allocate_rent(*, actor, lease_id, invoice_id, payload: dict) -> FinanceResult:
    """Apply a positive minor-unit allocation. Two callers cannot over-allocate."""

    body = _body(payload)
    amount = _positive_minor(body.get("amount_minor"), "allocation")
    allocated_on = _required_date(body.get("allocated_on"), "allocated on")
    memo = _memo(body.get("memo"))
    client_key = _required_key(body.get("idempotency_key"))
    with transaction.atomic():
        lease = _prepare_lease(actor, lease_id)
        _date_on_or_after_start(allocated_on, lease, "allocated on")
        key = tenancy_idempotency_key("allocation", lease.id, client_key)
        invoice = _locked_invoice(lease, invoice_id)
        existing = RentAllocation.objects.filter(idempotency_key=key).first()
        if existing is not None:
            _same_allocation(existing, invoice, amount)
            _lease_unchanged(lease)
            return FinanceResult(_allocation_payload(existing), created=False)
        _lock_allocations(invoice)
        balance = invoice.total_minor - _net_allocated(invoice.id)
        if amount > balance:
            raise TenancyError("The allocation exceeds the invoice balance.", status_code=409)
        row = RentAllocation(
            invoice=invoice,
            amount_minor=amount,
            allocated_on=allocated_on,
            memo=memo,
            idempotency_key=key,
            created_by=actor,
        )
        row.save()
        _ledger(
            actor=actor,
            lease=lease,
            entry_type=LedgerEntryType.ALLOCATION_RECORDED,
            amount=amount,
            currency=invoice.currency,
            object_type="rent_allocation",
            object_id=row.id,
            key_parts=("ledger_allocation", row.id),
            metadata={"invoice_id": str(invoice.id), "allocated_on": allocated_on.isoformat()},
        )
        _lease_unchanged(lease)
        return FinanceResult(_allocation_payload(row), created=True)


def reverse_rent_allocation(*, actor, lease_id, allocation_id, payload: dict) -> FinanceResult:
    body = _body(payload)
    amount = _positive_minor(body.get("amount_minor"), "reversal")
    memo = _memo(body.get("memo"))
    client_key = _required_key(body.get("idempotency_key"))
    with transaction.atomic():
        lease = _prepare_lease(actor, lease_id)
        hint = RentAllocation.objects.filter(pk=allocation_id).values("invoice_id").first()
        if hint is None:
            raise TenancyError(NOT_FOUND, status_code=404)
        invoice = _locked_invoice(lease, hint["invoice_id"])
        original = _locked_allocation(allocation_id)
        if original.invoice_id != invoice.id:
            raise TenancyError(NOT_FOUND, status_code=404)
        if original.reversal_of_id is not None:
            raise TenancyError("A reversal cannot be reversed.", status_code=409)
        key = tenancy_idempotency_key("allocation_reversal", original.id, client_key)
        existing = RentAllocation.objects.filter(idempotency_key=key).first()
        if existing is not None:
            if existing.reversal_of_id != original.id or existing.amount_minor != amount:
                raise TenancyError("The idempotency key was already used.", status_code=409)
            _lease_unchanged(lease)
            return FinanceResult(_allocation_payload(existing), created=False)
        _lock_allocations(invoice)
        remaining = original.amount_minor - _reversed_amount(original.id)
        if amount > remaining:
            raise TenancyError(
                "The reversal exceeds the original allocation.",
                status_code=409,
            )
        row = RentAllocation(
            invoice=invoice,
            amount_minor=amount,
            allocated_on=original.allocated_on,
            memo=memo,
            reversal_of=original,
            idempotency_key=key,
            created_by=actor,
        )
        row.save()
        _ledger(
            actor=actor,
            lease=lease,
            entry_type=LedgerEntryType.ALLOCATION_REVERSED,
            amount=amount,
            currency=invoice.currency,
            object_type="rent_allocation",
            object_id=row.id,
            key_parts=("ledger_allocation_reversal", row.id),
            metadata={"invoice_id": str(invoice.id), "reversal_of": str(original.id)},
        )
        _lease_unchanged(lease)
        return FinanceResult(_allocation_payload(row), created=True)


def read_deposit_obligation(lease: Lease) -> dict:
    row = DepositObligation.objects.filter(lease=lease).first()
    if row is None:
        raise TenancyError(NOT_FOUND, status_code=404)
    return _obligation_payload(row)


def ensure_deposit_obligation(*, actor, lease_id, payload: dict) -> FinanceResult:
    """Record the contractual deposit once. A second call returns that row."""

    _body(payload)
    with transaction.atomic():
        lease = _prepare_lease(actor, lease_id)
        existing = (
            DepositObligation.objects.select_for_update(of=("self",)).filter(lease=lease).first()
        )
        if existing is not None:
            if existing.required_amount_minor != lease.deposit_amount_minor:
                raise TenancyError(
                    "The deposit obligation does not match the lease.",
                    status_code=409,
                )
            _lease_unchanged(lease)
            return FinanceResult(_obligation_payload(existing), created=False)
        tenant = _primary_tenant(lease)
        row = DepositObligation(
            lease=lease,
            tenant_user=tenant,
            required_amount_minor=lease.deposit_amount_minor,
            currency=lease.rent_currency,
            due_date=lease.start_date,
        )
        row.save()
        record_tenancy_event(
            actor=actor,
            lease=lease,
            action=TenancyEventAction.DEPOSIT_OBLIGATION_CREATED,
            object_type="deposit_obligation",
            object_id=row.id,
            idempotency_key=tenancy_idempotency_key("deposit_obligation", lease.id),
            metadata={
                "required_amount_minor": row.required_amount_minor,
                "currency": row.currency,
                "due_date": row.due_date.isoformat(),
            },
        )
        if row.required_amount_minor > 0:
            _ledger(
                actor=actor,
                lease=lease,
                entry_type=LedgerEntryType.DEPOSIT_OBLIGATION_RECORDED,
                amount=row.required_amount_minor,
                currency=row.currency,
                object_type="deposit_obligation",
                object_id=row.id,
                key_parts=("ledger_deposit_obligation", row.id),
                metadata={"due_date": row.due_date.isoformat()},
            )
        _lease_unchanged(lease)
        return FinanceResult(_obligation_payload(row), created=True)


def record_deposit_receipt(*, actor, lease_id, payload: dict) -> FinanceResult:
    body = _body(payload)
    amount = _positive_minor(body.get("amount_minor"), "deposit receipt")
    received_on = _required_date(body.get("received_on"), "received on")
    memo = _memo(body.get("memo"))
    client_key = _required_key(body.get("idempotency_key"))
    with transaction.atomic():
        lease = _prepare_lease(actor, lease_id)
        _date_on_or_after_start(received_on, lease, "received on")
        obligation = _locked_obligation(lease)
        key = tenancy_idempotency_key("deposit_receipt", obligation.id, client_key)
        existing = DepositReceipt.objects.filter(idempotency_key=key).first()
        if existing is not None:
            if existing.deposit_obligation_id != obligation.id or existing.amount_minor != amount:
                raise TenancyError("The idempotency key was already used.", status_code=409)
            _lease_unchanged(lease)
            return FinanceResult(_receipt_payload(existing), created=False)
        _lock_receipts(obligation)
        received = _net_received(obligation.id)
        if received + amount > obligation.required_amount_minor:
            raise TenancyError(
                "The deposit receipt exceeds the obligation.",
                status_code=409,
            )
        row = DepositReceipt(
            deposit_obligation=obligation,
            amount_minor=amount,
            received_on=received_on,
            memo=memo,
            idempotency_key=key,
            created_by=actor,
        )
        row.save()
        _ledger(
            actor=actor,
            lease=lease,
            entry_type=LedgerEntryType.DEPOSIT_RECEIPT_RECORDED,
            amount=amount,
            currency=obligation.currency,
            object_type="deposit_receipt",
            object_id=row.id,
            key_parts=("ledger_deposit_receipt", row.id),
            metadata={"received_on": received_on.isoformat()},
        )
        _lease_unchanged(lease)
        return FinanceResult(_receipt_payload(row), created=True)


def reverse_deposit_receipt(*, actor, lease_id, receipt_id, payload: dict) -> FinanceResult:
    body = _body(payload)
    amount = _positive_minor(body.get("amount_minor"), "reversal")
    memo = _memo(body.get("memo"))
    client_key = _required_key(body.get("idempotency_key"))
    with transaction.atomic():
        lease = _prepare_lease(actor, lease_id)
        obligation = _locked_obligation(lease)
        original = _locked_receipt(receipt_id)
        if original.deposit_obligation_id != obligation.id:
            raise TenancyError(NOT_FOUND, status_code=404)
        if original.reversal_of_id is not None:
            raise TenancyError("A reversal cannot be reversed.", status_code=409)
        key = tenancy_idempotency_key("deposit_receipt_reversal", original.id, client_key)
        existing = DepositReceipt.objects.filter(idempotency_key=key).first()
        if existing is not None:
            if existing.reversal_of_id != original.id or existing.amount_minor != amount:
                raise TenancyError("The idempotency key was already used.", status_code=409)
            _lease_unchanged(lease)
            return FinanceResult(_receipt_payload(existing), created=False)
        _lock_receipts(obligation)
        remaining = original.amount_minor - _reversed_receipts(original.id)
        if amount > remaining:
            raise TenancyError("The reversal exceeds the original receipt.", status_code=409)
        row = DepositReceipt(
            deposit_obligation=obligation,
            amount_minor=amount,
            received_on=original.received_on,
            memo=memo,
            reversal_of=original,
            idempotency_key=key,
            created_by=actor,
        )
        row.save()
        _ledger(
            actor=actor,
            lease=lease,
            entry_type=LedgerEntryType.DEPOSIT_RECEIPT_REVERSED,
            amount=amount,
            currency=obligation.currency,
            object_type="deposit_receipt",
            object_id=row.id,
            key_parts=("ledger_deposit_reversal", row.id),
            metadata={"reversal_of": str(original.id)},
        )
        _lease_unchanged(lease)
        return FinanceResult(_receipt_payload(row), created=True)


def rent_arrears(lease: Lease, as_of: date) -> dict:
    balances = _invoice_balances(lease)
    overdue = [
        row for row in balances if row["due_date"] <= as_of.isoformat() and row["balance_minor"] > 0
    ]
    return {
        "lease_id": str(lease.id),
        "as_of": as_of.isoformat(),
        "currency": lease.rent_currency,
        "invoice_count": len(overdue),
        "arrears_minor": sum(row["balance_minor"] for row in overdue),
        "invoices": overdue,
    }


def tenant_statement(actor, lease: Lease, as_of: date) -> dict:
    _require_tenant_reader(actor, lease)
    balances = _invoice_balances(lease)
    allocations = _public_allocations(lease, include_memo=False)
    deposit = _deposit_view(lease, include_memo=False)
    arrears = rent_arrears(lease, as_of)
    return {
        "lease_id": str(lease.id),
        "as_of": as_of.isoformat(),
        "currency": lease.rent_currency,
        "invoices": balances,
        "allocations": allocations,
        "deposit": deposit,
        "arrears_minor": arrears["arrears_minor"],
    }


def landlord_statement(actor, lease: Lease, as_of: date) -> dict:
    _require_billing_writer(actor, lease)
    balances = _invoice_balances(lease)
    billed = sum(row["total_minor"] for row in balances)
    allocated = sum(row["allocated_minor"] for row in balances)
    arrears = rent_arrears(lease, as_of)
    deposit = _deposit_view(lease, include_memo=True)
    return {
        "lease_id": str(lease.id),
        "as_of": as_of.isoformat(),
        "currency": lease.rent_currency,
        "billed_minor": billed,
        "allocated_minor": allocated,
        "outstanding_minor": billed - allocated,
        "arrears_minor": arrears["arrears_minor"],
        "invoices": balances,
        "deposit": deposit,
    }


def rental_ledger(actor, lease: Lease) -> dict:
    _require_billing_writer(actor, lease)
    rows = RentalLedgerEntry.objects.filter(lease=lease).order_by("created_at", "id")
    return {
        "lease_id": str(lease.id),
        "entries": [
            {
                "id": str(row.id),
                "entry_type": row.entry_type,
                "amount_minor": row.amount_minor,
                "currency": row.currency,
                "object_type": row.object_type,
                "object_id": row.object_id,
                "metadata": row.metadata,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ],
    }


def _prepare_lease(actor, lease_id) -> Lease:
    require_actor(actor)
    try:
        lease = Lease.objects.select_for_update(of=("self",)).get(pk=lease_id)
    except Lease.DoesNotExist as exc:
        raise TenancyError(NOT_FOUND, status_code=404) from exc
    _require_billing_writer(actor, lease)
    if lease.status not in LeaseStatus.EXECUTED:
        raise TenancyError(
            "Rent can be billed only after the lease is fully executed.",
            status_code=409,
        )
    _require_currency(lease)
    lease._finance_snapshot = {
        "status": lease.status,
        "contract_data_hash": lease.contract_data_hash,
        "rent_amount_minor": lease.rent_amount_minor,
        "term_end_date": lease.term_end_date,
        "effective_end_date": lease.effective_end_date,
    }
    return lease


def _lease_unchanged(lease: Lease) -> None:
    snapshot = lease._finance_snapshot
    lease.refresh_from_db()
    current = {
        "status": lease.status,
        "contract_data_hash": lease.contract_data_hash,
        "rent_amount_minor": lease.rent_amount_minor,
        "term_end_date": lease.term_end_date,
        "effective_end_date": lease.effective_end_date,
    }
    if current != snapshot:
        raise TenancyError("This change conflicts with another update.", status_code=409)


def _require_billing_writer(actor, lease: Lease) -> None:
    require_actor(actor)
    try:
        verify_owner_authority(actor, lease.property)
        return
    except OperationsError:
        pass
    agreements = ManagementAgreement.objects.filter(
        property_id=lease.property_id,
        status=AgreementStatus.ACTIVE,
    ).select_related("granting_user", "managing_organization")
    for agreement in agreements:
        if is_agreement_expired(agreement):
            continue
        try:
            verify_owner_authority(agreement.granting_user, lease.property)
        except OperationsError:
            continue
        try:
            authorize(actor, agreement.managing_organization_id, Permission.LEASES_PREPARE)
            return
        except OrganizationError:
            continue
    raise TenancyError(PERMISSION_DENIED, status_code=403)


def _require_tenant_reader(actor, lease: Lease) -> None:
    try:
        _require_billing_writer(actor, lease)
        return
    except TenancyError as exc:
        if exc.status_code != 403:
            raise
    if lease.parties.filter(user=actor, party_role__in=_TENANT_ROLES).exists():
        return
    raise TenancyError(PERMISSION_DENIED, status_code=403)


def _schedule_limit(lease: Lease, through_date: date | None) -> date:
    if lease.term_end_date is None:
        if through_date is None:
            raise TenancyError("An open-ended lease needs a through date.", status_code=400)
        limit = through_date
    elif through_date is None:
        limit = lease.term_end_date
    elif through_date > lease.term_end_date:
        raise TenancyError("The through date cannot be after the term end.", status_code=400)
    else:
        limit = through_date
    if limit < lease.start_date:
        raise TenancyError(
            "The through date cannot be before the lease start date.",
            status_code=400,
        )
    return limit


def _schedule_replay(lease: Lease, client_key: str, limit: date):
    key = tenancy_idempotency_key("rent_schedule_generated", lease.id, client_key)
    existing = TenancyEvent.objects.filter(idempotency_key=key).first()
    if existing is None:
        return None
    metadata = existing.metadata or {}
    if metadata.get("through_date") != limit.isoformat():
        raise TenancyError("The idempotency key was already used.", status_code=409)
    raw_ids = metadata.get("entry_ids") or []
    rows = list(
        RentScheduleEntry.objects.filter(lease=lease, pk__in=raw_ids).order_by("sequence_number")
    )
    if len(rows) != len(raw_ids):
        raise TenancyError("This change conflicts with another update.", status_code=409)
    return rows


def _record_schedule_event(actor, lease, client_key, limit, window, created) -> None:
    if not created and not client_key:
        return
    entry_ids = [str(row.id) for row in window]
    metadata = {
        "through_date": limit.isoformat(),
        "entry_ids": entry_ids,
        "created_count": len(created),
    }
    if client_key:
        event_key = tenancy_idempotency_key("rent_schedule_generated", lease.id, client_key)
    else:
        event_key = tenancy_idempotency_key(
            "rent_schedule_generated",
            lease.id,
            limit.isoformat(),
            created[0].id,
        )
    record_tenancy_event(
        actor=actor,
        lease=lease,
        action=TenancyEventAction.RENT_SCHEDULE_GENERATED,
        object_type="lease",
        object_id=lease.id,
        idempotency_key=event_key,
        metadata=metadata,
    )


def _window_rows(lease: Lease, limit: date) -> list[RentScheduleEntry]:
    return list(
        RentScheduleEntry.objects.filter(lease=lease, period_start__lte=limit).order_by(
            "sequence_number"
        )
    )


def _rows_payload(lease: Lease, rows: list[RentScheduleEntry]) -> dict:
    return {
        "lease_id": str(lease.id),
        "entry_count": len(rows),
        "entries": [_schedule_row(row) for row in rows],
    }


def _create_missing_periods(actor, lease: Lease, limit: date) -> list[RentScheduleEntry]:
    existing = {
        row.period_start: row
        for row in RentScheduleEntry.objects.select_for_update(of=("self",)).filter(lease=lease)
    }
    next_sequence = 0
    for row in existing.values():
        if row.sequence_number > next_sequence:
            next_sequence = row.sequence_number
    plan = _new_periods(lease, limit, existing)
    if lease.rent_amount_minor <= 0:
        raise TenancyError("The lease rent must be a positive minor amount.", status_code=409)
    created = []
    for period_start, period_end, due in plan:
        next_sequence += 1
        total = lease.rent_amount_minor + lease.service_charge_minor
        row = RentScheduleEntry(
            lease=lease,
            sequence_number=next_sequence,
            period_start=period_start,
            period_end=period_end,
            due_date=due,
            base_rent_minor=lease.rent_amount_minor,
            service_charge_minor=lease.service_charge_minor,
            total_due_minor=total,
            currency=lease.rent_currency,
            status=ScheduleStatus.SCHEDULED,
        )
        row.save()
        _ledger(
            actor=actor,
            lease=lease,
            entry_type=LedgerEntryType.SCHEDULE_GENERATED,
            amount=total,
            currency=lease.rent_currency,
            object_type="rent_schedule_entry",
            object_id=row.id,
            key_parts=("ledger_schedule", lease.id, period_start.isoformat()),
            metadata={
                "sequence_number": next_sequence,
                "period_start": period_start.isoformat(),
                "period_end": period_end.isoformat(),
                "due_date": due.isoformat(),
            },
        )
        created.append(row)
    return created


def _new_periods(lease: Lease, limit: date, existing: dict) -> list[tuple[date, date, date]]:
    due_day = lease.payment_due_day
    cursor = lease.start_date
    planned: list[tuple[date, date, date]] = []
    steps = 0
    while cursor <= limit:
        steps += 1
        if steps > MAX_SCHEDULE_STEPS:
            raise TenancyError("The schedule window is too long.", status_code=400)
        month_last = calendar.monthrange(cursor.year, cursor.month)[1]
        month_end = date(cursor.year, cursor.month, month_last)
        period_end = month_end if month_end <= limit else limit
        due = date(cursor.year, cursor.month, due_day)
        if due < cursor:
            due = cursor
        if due > period_end:
            due = period_end
        skipped = cursor in existing
        terminated = (
            lease.actual_termination_date is not None and cursor >= lease.actual_termination_date
        )
        if not skipped and not terminated:
            planned.append((cursor, period_end, due))
            if len(planned) > MAX_SCHEDULE_MONTHS:
                raise TenancyError(
                    "A schedule call covers at most 36 months.",
                    status_code=400,
                )
        cursor = period_end + timedelta(days=1)
    return planned


def _entries_to_bill(lease: Lease, *, as_of: date | None, entry_id) -> list[RentScheduleEntry]:
    queryset = RentScheduleEntry.objects.select_for_update(of=("self",)).filter(lease=lease)
    if entry_id not in (None, ""):
        try:
            entry = queryset.get(pk=entry_id)
        except RentScheduleEntry.DoesNotExist as exc:
            raise TenancyError(NOT_FOUND, status_code=404) from exc
        _ensure_billable(entry)
        return [entry]
    return list(
        queryset.filter(due_date__lte=as_of, status=ScheduleStatus.SCHEDULED).order_by(
            "sequence_number"
        )
    )


def _ensure_billable(entry: RentScheduleEntry) -> None:
    if entry.status == ScheduleStatus.CANCELLED:
        raise TenancyError("A cancelled schedule entry cannot be invoiced.", status_code=409)
    if entry.status != ScheduleStatus.SCHEDULED or _has_invoice_line(entry):
        raise TenancyError("This schedule entry is already invoiced.", status_code=409)


def _has_invoice_line(entry: RentScheduleEntry) -> bool:
    return RentInvoiceLine.objects.filter(schedule_entry=entry).exists()


def _issue_one(actor, lease, entry, batch_hash: str, selector: str, moment) -> RentInvoice:
    key = tenancy_idempotency_key("invoice", entry.id)
    invoice = RentInvoice(
        lease=lease,
        due_date=entry.due_date,
        total_minor=entry.total_due_minor,
        currency=entry.currency,
        idempotency_key=key,
        batch_hash=batch_hash,
        selector=selector,
        issued_by=actor,
    )
    invoice.save()
    RentInvoiceLine(
        invoice=invoice,
        schedule_entry=entry,
        base_rent_minor=entry.base_rent_minor,
        service_charge_minor=entry.service_charge_minor,
        amount_minor=entry.total_due_minor,
    ).save()
    entry.status = ScheduleStatus.BILLED
    entry.billed_at = moment
    entry.save(update_fields=["status", "billed_at", "updated_at"])
    _ledger(
        actor=actor,
        lease=lease,
        entry_type=LedgerEntryType.INVOICE_ISSUED,
        amount=invoice.total_minor,
        currency=invoice.currency,
        object_type="rent_invoice",
        object_id=invoice.id,
        key_parts=("ledger_invoice", invoice.id),
        metadata={"schedule_entry_id": str(entry.id), "due_date": entry.due_date.isoformat()},
    )
    return invoice


def _locked_invoice(lease: Lease, invoice_id) -> RentInvoice:
    try:
        return RentInvoice.objects.select_for_update(of=("self",)).get(pk=invoice_id, lease=lease)
    except RentInvoice.DoesNotExist as exc:
        raise TenancyError(NOT_FOUND, status_code=404) from exc


def _locked_allocation(allocation_id) -> RentAllocation:
    try:
        return RentAllocation.objects.select_for_update(of=("self",)).get(pk=allocation_id)
    except RentAllocation.DoesNotExist as exc:
        raise TenancyError(NOT_FOUND, status_code=404) from exc


def _locked_obligation(lease: Lease) -> DepositObligation:
    try:
        return DepositObligation.objects.select_for_update(of=("self",)).get(lease=lease)
    except DepositObligation.DoesNotExist as exc:
        raise TenancyError("Record the deposit obligation first.", status_code=409) from exc


def _locked_receipt(receipt_id) -> DepositReceipt:
    try:
        return DepositReceipt.objects.select_for_update(of=("self",)).get(pk=receipt_id)
    except DepositReceipt.DoesNotExist as exc:
        raise TenancyError(NOT_FOUND, status_code=404) from exc


def _lock_allocations(invoice: RentInvoice) -> None:
    list(
        RentAllocation.objects.select_for_update(of=("self",))
        .filter(invoice=invoice)
        .order_by("id")
    )


def _lock_receipts(obligation: DepositObligation) -> None:
    list(
        DepositReceipt.objects.select_for_update(of=("self",))
        .filter(deposit_obligation=obligation)
        .order_by("id")
    )


def _net_allocated(invoice_id) -> int:
    net = 0
    for amount, reversal_id in RentAllocation.objects.filter(invoice_id=invoice_id).values_list(
        "amount_minor", "reversal_of_id"
    ):
        net += -amount if reversal_id else amount
    return net


def _reversed_amount(allocation_id) -> int:
    total = 0
    for amount in RentAllocation.objects.filter(reversal_of_id=allocation_id).values_list(
        "amount_minor", flat=True
    ):
        total += amount
    return total


def _net_received(obligation_id) -> int:
    net = 0
    rows = DepositReceipt.objects.filter(deposit_obligation_id=obligation_id).values_list(
        "amount_minor", "reversal_of_id"
    )
    for amount, reversal_id in rows:
        net += -amount if reversal_id else amount
    return net


def _reversed_receipts(receipt_id) -> int:
    total = 0
    for amount in DepositReceipt.objects.filter(reversal_of_id=receipt_id).values_list(
        "amount_minor", flat=True
    ):
        total += amount
    return total


def _same_allocation(existing: RentAllocation, invoice: RentInvoice, amount: int) -> None:
    if existing.invoice_id != invoice.id or existing.amount_minor != amount:
        raise TenancyError("The idempotency key was already used.", status_code=409)
    if existing.reversal_of_id is not None:
        raise TenancyError("The idempotency key was already used.", status_code=409)


def _primary_tenant(lease: Lease):
    primaries = list(lease.parties.filter(party_role=LeasePartyRole.PRIMARY_TENANT))
    if len(primaries) != 1:
        raise TenancyError("The lease needs one primary tenant.", status_code=409)
    return primaries[0].user


def _require_currency(lease: Lease) -> None:
    currency = lease.rent_currency
    if (
        not isinstance(currency, str)
        or len(currency) != 3
        or any(char not in _CURRENCY_OK for char in currency)
    ):
        raise TenancyError("The lease currency is invalid.", status_code=409)


def _ledger(
    *,
    actor,
    lease,
    entry_type,
    amount,
    currency,
    object_type,
    object_id,
    key_parts,
    metadata,
):
    RentalLedgerEntry(
        lease=lease,
        entry_type=entry_type,
        amount_minor=amount,
        currency=currency,
        object_type=object_type,
        object_id=str(object_id),
        idempotency_key=tenancy_idempotency_key(*key_parts),
        actor=actor,
        metadata=metadata,
    ).save()


def _invoice_balances(lease: Lease) -> list[dict]:
    invoices = list(RentInvoice.objects.filter(lease=lease).order_by("due_date", "id"))
    return [_balance_row(invoice) for invoice in invoices]


def _balance_row(invoice: RentInvoice) -> dict:
    allocated = _net_allocated(invoice.id)
    return {
        "id": str(invoice.id),
        "due_date": invoice.due_date.isoformat(),
        "total_minor": invoice.total_minor,
        "allocated_minor": allocated,
        "balance_minor": invoice.total_minor - allocated,
        "currency": invoice.currency,
    }


def _public_allocations(lease: Lease, *, include_memo: bool) -> list[dict]:
    rows = RentAllocation.objects.filter(invoice__lease=lease).order_by("allocated_on", "id")
    published = []
    for row in rows:
        item = {
            "id": str(row.id),
            "invoice_id": str(row.invoice_id),
            "amount_minor": row.amount_minor,
            "allocated_on": row.allocated_on.isoformat(),
            "reversal_of": None if row.reversal_of_id is None else str(row.reversal_of_id),
        }
        if include_memo:
            item["memo"] = row.memo
        published.append(item)
    return published


def _deposit_view(lease: Lease, *, include_memo: bool) -> dict | None:
    obligation = DepositObligation.objects.filter(lease=lease).first()
    if obligation is None:
        return None
    receipts = []
    for row in obligation.receipts.order_by("received_on", "id"):
        item = {
            "id": str(row.id),
            "amount_minor": row.amount_minor,
            "received_on": row.received_on.isoformat(),
            "reversal_of": None if row.reversal_of_id is None else str(row.reversal_of_id),
        }
        if include_memo:
            item["memo"] = row.memo
        receipts.append(item)
    return {
        "id": str(obligation.id),
        "required_minor": obligation.required_amount_minor,
        "received_minor": _net_received(obligation.id),
        "currency": obligation.currency,
        "due_date": obligation.due_date.isoformat(),
        "receipts": receipts,
    }


def _schedule_row(row: RentScheduleEntry) -> dict:
    return {
        "id": str(row.id),
        "sequence_number": row.sequence_number,
        "period_start": row.period_start.isoformat(),
        "period_end": row.period_end.isoformat(),
        "due_date": row.due_date.isoformat(),
        "base_rent_minor": row.base_rent_minor,
        "service_charge_minor": row.service_charge_minor,
        "total_due_minor": row.total_due_minor,
        "currency": row.currency,
        "status": row.status,
    }


def _invoice_payload(lease: Lease, rows: list[RentInvoice]) -> dict:
    return {
        "lease_id": str(lease.id),
        "invoice_count": len(rows),
        "invoices": [_balance_row(row) for row in rows],
    }


def _allocation_payload(row: RentAllocation) -> dict:
    return {
        "id": str(row.id),
        "invoice_id": str(row.invoice_id),
        "amount_minor": row.amount_minor,
        "allocated_on": row.allocated_on.isoformat(),
        "reversal_of": None if row.reversal_of_id is None else str(row.reversal_of_id),
        "balance_minor": row.invoice.total_minor - _net_allocated(row.invoice_id),
    }


def _obligation_payload(row: DepositObligation) -> dict:
    return {
        "id": str(row.id),
        "lease_id": str(row.lease_id),
        "required_minor": row.required_amount_minor,
        "currency": row.currency,
        "due_date": row.due_date.isoformat(),
        "received_minor": _net_received(row.id),
    }


def _receipt_payload(row: DepositReceipt) -> dict:
    return {
        "id": str(row.id),
        "deposit_obligation_id": str(row.deposit_obligation_id),
        "amount_minor": row.amount_minor,
        "received_on": row.received_on.isoformat(),
        "reversal_of": None if row.reversal_of_id is None else str(row.reversal_of_id),
        "received_minor": _net_received(row.deposit_obligation_id),
    }


def _body(payload) -> dict:
    if not isinstance(payload, dict):
        raise TenancyError("The request body is invalid.", status_code=400)
    return payload


def _optional_key(value) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TenancyError("The idempotency key is invalid.", status_code=400)
    cleaned = value.strip()
    if not cleaned:
        return None
    if len(cleaned) > 200:
        raise TenancyError("The idempotency key is invalid.", status_code=400)
    return cleaned


def _required_key(value) -> str:
    cleaned = _optional_key(value)
    if cleaned is None:
        raise TenancyError("An idempotency key is required.", status_code=400)
    return cleaned


def _positive_minor(value, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise TenancyError(
            f"The {label} must be a positive integer in minor units.",
            status_code=400,
        )
    if value > 9_000_000_000_000_000:
        raise TenancyError(f"The {label} is too large.", status_code=400)
    return value


def _memo(value) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise TenancyError("The memo must be text.", status_code=400)
    cleaned = value.strip()
    if len(cleaned) > 200:
        raise TenancyError("The memo must be 200 characters or fewer.", status_code=400)
    return cleaned


def _optional_date(value, label: str) -> date | None:
    if value in (None, ""):
        return None
    return _required_date(value, label)


def _required_date(value, label: str) -> date:
    if isinstance(value, datetime) or not isinstance(value, date):
        if not isinstance(value, str):
            raise TenancyError(f"The {label} must be a date.", status_code=400)
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise TenancyError(f"The {label} must be a date.", status_code=400) from exc
    return value


def _date_on_or_after_start(value: date, lease: Lease, label: str) -> None:
    if value < lease.start_date:
        raise TenancyError(
            f"The {label} cannot be before the lease start date.",
            status_code=400,
        )


def call_finance(func, /, *args, **kwargs):
    """Run a finance operation and preserve database check failures."""

    try:
        return func(*args, **kwargs)
    except TenancyError:
        raise
    except OperationalError as exc:
        if is_deadlock(exc):
            raise TenancyError(
                "This change conflicts with another update.",
                status_code=409,
            ) from exc
        raise
    except IntegrityError as exc:
        message = str(exc).lower()
        if "duplicate key" in message or "unique constraint" in message:
            raise TenancyError(
                "This change conflicts with another update.",
                status_code=409,
            ) from exc
        raise
