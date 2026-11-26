"""Rental finance HTTP API.

Views discover a visible lease and call the billing service. They do not
change lease status. Anonymous callers receive 401. A hidden lease is 404.
"""

from __future__ import annotations

from django.utils import timezone
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.tenancy.access import visible_lease
from apps.tenancy.domain import NOT_FOUND
from apps.tenancy.errors import TenancyError, raise_for_tenancy
from apps.tenancy.services.billing import (
    allocate_rent,
    call_finance,
    ensure_deposit_obligation,
    generate_rent_schedule,
    issue_rent_invoices,
    landlord_statement,
    list_rent_invoices,
    list_rent_schedule,
    read_deposit_obligation,
    record_deposit_receipt,
    rent_arrears,
    rental_ledger,
    reverse_deposit_receipt,
    reverse_rent_allocation,
    tenant_statement,
)


def _lease_or_404(user, lease_id):
    lease = visible_lease(user, lease_id)
    if lease is None:
        raise NotFound(NOT_FOUND)
    return lease


def _call(func, /, *args, **kwargs):
    try:
        return call_finance(func, *args, **kwargs)
    except TenancyError as exc:
        raise_for_tenancy(exc)


def _read(func, /, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except TenancyError as exc:
        raise_for_tenancy(exc)


def _query_as_of(request):
    raw = request.query_params.get("as_of")
    if raw in (None, ""):
        raw = timezone.now().date().isoformat()
    return _read(_parse_as_of, raw)


def _parse_as_of(raw: str):
    from datetime import date

    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise TenancyError("The as of date must be a date.", status_code=400) from exc


class RentScheduleView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, lease_id):
        lease = _lease_or_404(request.user, lease_id)
        return Response(_read(list_rent_schedule, lease))

    def post(self, request, lease_id):
        _lease_or_404(request.user, lease_id)
        result = _call(
            generate_rent_schedule,
            actor=request.user,
            lease_id=lease_id,
            payload=request.data,
        )
        return Response(result.payload, status=201 if result.created else 200)


class RentInvoiceView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, lease_id):
        lease = _lease_or_404(request.user, lease_id)
        return Response(_read(list_rent_invoices, lease))

    def post(self, request, lease_id):
        _lease_or_404(request.user, lease_id)
        result = _call(
            issue_rent_invoices,
            actor=request.user,
            lease_id=lease_id,
            payload=request.data,
        )
        return Response(result.payload, status=201 if result.created else 200)


class RentAllocationView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, lease_id, invoice_id):
        _lease_or_404(request.user, lease_id)
        result = _call(
            allocate_rent,
            actor=request.user,
            lease_id=lease_id,
            invoice_id=invoice_id,
            payload=request.data,
        )
        return Response(result.payload, status=201 if result.created else 200)


class RentAllocationReversalView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, lease_id, allocation_id):
        _lease_or_404(request.user, lease_id)
        result = _call(
            reverse_rent_allocation,
            actor=request.user,
            lease_id=lease_id,
            allocation_id=allocation_id,
            payload=request.data,
        )
        return Response(result.payload, status=201 if result.created else 200)


class DepositObligationView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, lease_id):
        lease = _lease_or_404(request.user, lease_id)
        return Response(_read(read_deposit_obligation, lease))

    def post(self, request, lease_id):
        _lease_or_404(request.user, lease_id)
        result = _call(
            ensure_deposit_obligation,
            actor=request.user,
            lease_id=lease_id,
            payload=request.data,
        )
        return Response(result.payload, status=201 if result.created else 200)


class DepositReceiptView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, lease_id):
        _lease_or_404(request.user, lease_id)
        result = _call(
            record_deposit_receipt,
            actor=request.user,
            lease_id=lease_id,
            payload=request.data,
        )
        return Response(result.payload, status=201 if result.created else 200)


class DepositReceiptReversalView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, lease_id, receipt_id):
        _lease_or_404(request.user, lease_id)
        result = _call(
            reverse_deposit_receipt,
            actor=request.user,
            lease_id=lease_id,
            receipt_id=receipt_id,
            payload=request.data,
        )
        return Response(result.payload, status=201 if result.created else 200)


class RentArrearsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, lease_id):
        lease = _lease_or_404(request.user, lease_id)
        return Response(_read(rent_arrears, lease, _query_as_of(request)))


class TenantStatementView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, lease_id):
        lease = _lease_or_404(request.user, lease_id)
        return Response(_read(tenant_statement, request.user, lease, _query_as_of(request)))


class LandlordStatementView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, lease_id):
        lease = _lease_or_404(request.user, lease_id)
        return Response(_read(landlord_statement, request.user, lease, _query_as_of(request)))


class RentalLedgerView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, lease_id):
        lease = _lease_or_404(request.user, lease_id)
        return Response(_read(rental_ledger, request.user, lease))
