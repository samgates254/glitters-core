"""Authenticated tenancy HTTP API.

Views enforce discovery and call the lease services. They do not reserve
units, hash contracts, or change lease status on their own.
"""

from __future__ import annotations

import uuid

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_ipv46_address
from django.db.models import Count
from rest_framework.exceptions import NotFound
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.tenancy.access import leases_visible_to, visible_lease
from apps.tenancy.domain import NOT_FOUND, LeaseStatus, PartySignatureStatus
from apps.tenancy.errors import TenancyError, raise_for_tenancy
from apps.tenancy.models import Lease, TenancyEvent
from apps.tenancy.serializers import (
    CancellationSerializer,
    DraftLeaseSerializer,
    LeaseDetailSerializer,
    LeaseListSerializer,
    LeasePartySerializer,
    RenderedDocumentSerializer,
    SignatureEvidenceSerializer,
    SignLeaseSerializer,
    TenancyEventSerializer,
    TerminationSerializer,
)
from apps.tenancy.services.audit import tenancy_idempotency_key
from apps.tenancy.services.leases import (
    activate_lease,
    approve_lease_terms,
    cancel_lease,
    create_draft_lease,
    request_lease_termination,
    sign_lease,
    terminate_lease,
)


def _call(func, /, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except TenancyError as exc:
        raise_for_tenancy(exc)


def _lease_or_404(user, lease_id):
    lease = visible_lease(user, lease_id)
    if lease is None:
        raise NotFound(NOT_FOUND)
    return lease


def _page(request, queryset, serializer_class):
    paginator = PageNumberPagination()
    paginator.page_size = 25
    paginator.page_size_query_param = "page_size"
    paginator.max_page_size = 100
    page = paginator.paginate_queryset(queryset, request, view=request.parser_context["view"])
    return paginator.get_paginated_response(serializer_class(page, many=True).data)


def _uuid_param(value: str | None, label: str):
    if value in (None, ""):
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise TenancyError(f"The {label} is invalid.", status_code=400) from exc


def _status_param(value: str | None) -> str | None:
    if value in (None, ""):
        return None
    if value not in LeaseStatus.VALUES:
        raise TenancyError("The lease status is invalid.", status_code=400)
    return value


def _request_ip(request) -> str | None:
    value = request.META.get("REMOTE_ADDR")
    if not isinstance(value, str) or not value:
        return None
    try:
        validate_ipv46_address(value)
    except DjangoValidationError:
        return None
    return value


def _user_agent(request) -> str:
    value = request.META.get("HTTP_USER_AGENT", "")
    return value if isinstance(value, str) else ""


class LeaseCollectionView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            property_id = _uuid_param(request.query_params.get("property_id"), "property id")
            organization_id = _uuid_param(
                request.query_params.get("organization_id"),
                "organization id",
            )
            status_name = _status_param(request.query_params.get("status"))
        except TenancyError as exc:
            raise_for_tenancy(exc)
        queryset = leases_visible_to(request.user).order_by("-created_at", "id")
        if property_id is not None:
            queryset = queryset.filter(property_id=property_id)
        if organization_id is not None:
            queryset = queryset.filter(managing_organization_id=organization_id)
        if status_name is not None:
            queryset = queryset.filter(status=status_name)
        return _page(request, queryset, LeaseListSerializer)

    def post(self, request):
        serializer = DraftLeaseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        idempotency_key = data.get("idempotency_key") or None
        existed = False
        if idempotency_key:
            draft_key = tenancy_idempotency_key("lease_drafted", request.user.id, idempotency_key)
            existed = TenancyEvent.objects.filter(idempotency_key=draft_key).exists()
        lease = _call(
            create_draft_lease,
            actor=request.user,
            property_id=data["property_id"],
            payload=serializer.payload(),
            idempotency_key=idempotency_key,
            organization_id=data.get("organization_id"),
        )
        body = LeaseDetailSerializer(lease).data
        return Response(body, status=200 if existed else 201)


class LeaseDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, lease_id):
        lease = _lease_or_404(request.user, lease_id)
        return Response(LeaseDetailSerializer(lease).data)


class LeasePartyListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, lease_id):
        lease = _lease_or_404(request.user, lease_id)
        parties = lease.parties.order_by("created_at", "id")
        return Response({"parties": LeasePartySerializer(parties, many=True).data})


class LeaseTermsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, lease_id):
        lease = _lease_or_404(request.user, lease_id)
        return Response(
            {
                "lease_id": str(lease.id),
                "lease_type": lease.lease_type,
                "start_date": lease.start_date,
                "term_end_date": lease.term_end_date,
                "rent_amount_minor": lease.rent_amount_minor,
                "rent_currency": lease.rent_currency,
                "deposit_amount_minor": lease.deposit_amount_minor,
                "service_charge_minor": lease.service_charge_minor,
                "payment_due_day": lease.payment_due_day,
                "grace_period_days": lease.grace_period_days,
                "special_covenants": lease.special_covenants,
                "document_version": lease.document_version,
            }
        )


class LeaseApproveView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, lease_id):
        _lease_or_404(request.user, lease_id)
        serializer = RenderedDocumentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        lease = _call(
            approve_lease_terms,
            actor=request.user,
            lease_id=lease_id,
            rendered_document=serializer.document(),
            rendered_document_uri=data.get("rendered_document_uri", ""),
            deadline_hours=data.get("deadline_hours", 72),
        )
        return Response(LeaseDetailSerializer(lease).data)


class LeaseSignatureView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, lease_id):
        lease = _lease_or_404(request.user, lease_id)
        parties = lease.parties.order_by("created_at", "id")
        evidence = lease.signature_evidence.order_by("signed_at", "id")
        return Response(
            {
                "lease_id": str(lease.id),
                "status": lease.status,
                "document_version": lease.document_version,
                "parties": LeasePartySerializer(parties, many=True).data,
                "evidence": SignatureEvidenceSerializer(evidence, many=True).data,
            }
        )

    def post(self, request, lease_id):
        lease = _lease_or_404(request.user, lease_id)
        serializer = SignLeaseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        party = lease.parties.filter(pk=data["party_id"]).first()
        already_signed = party is not None and party.signature_status == PartySignatureStatus.SIGNED
        evidence = _call(
            sign_lease,
            actor=request.user,
            lease_id=lease.id,
            party_id=data["party_id"],
            rendered_document=serializer.document(),
            signature_method=data["signature_method"],
            ip_address=_request_ip(request),
            user_agent=_user_agent(request),
            idempotency_key=data.get("idempotency_key") or None,
        )
        code = 200 if already_signed else 201
        return Response(SignatureEvidenceSerializer(evidence).data, status=code)


class LeaseActivateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, lease_id):
        _lease_or_404(request.user, lease_id)
        lease = _call(activate_lease, actor=request.user, lease_id=lease_id)
        return Response(LeaseDetailSerializer(lease).data)


class LeaseCancelView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, lease_id):
        _lease_or_404(request.user, lease_id)
        serializer = CancellationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        lease = _call(
            cancel_lease,
            actor=request.user,
            lease_id=lease_id,
            reason=data["reason"],
            idempotency_key=data.get("idempotency_key") or None,
        )
        return Response(LeaseDetailSerializer(lease).data)


class LeaseTerminationRequestView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, lease_id):
        _lease_or_404(request.user, lease_id)
        serializer = TerminationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        event = _call(
            request_lease_termination,
            actor=request.user,
            lease_id=lease_id,
            termination_date=data["termination_date"],
            reason=data["reason"],
        )
        return Response(TenancyEventSerializer(event).data)


class LeaseTerminateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, lease_id):
        _lease_or_404(request.user, lease_id)
        serializer = TerminationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        lease = _call(
            terminate_lease,
            actor=request.user,
            lease_id=lease_id,
            termination_date=data["termination_date"],
            reason=data["reason"],
        )
        return Response(LeaseDetailSerializer(lease).data)


class LeaseHistoryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, lease_id):
        lease = _lease_or_404(request.user, lease_id)
        events = lease.events.order_by("-created_at", "-id")
        return _page(request, events, TenancyEventSerializer)


class LeaseDocumentView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, lease_id):
        lease = _lease_or_404(request.user, lease_id)
        return Response(
            {
                "lease_id": str(lease.id),
                "document_version": lease.document_version,
                "contract_data_hash": lease.contract_data_hash,
                "rendered_document_hash": lease.rendered_document_hash,
                "rendered_document_uri": lease.rendered_document_uri,
                "signature_deadline": lease.signature_deadline,
                "status": lease.status,
            }
        )


class LeaseReservationView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, lease_id):
        lease = _lease_or_404(request.user, lease_id)
        return Response(
            {
                "lease_id": str(lease.id),
                "status": lease.status,
                "start_date": lease.start_date,
                "effective_end_date": lease.effective_end_date,
                "signature_deadline": lease.signature_deadline,
                "occupancy_reserving": lease.status in LeaseStatus.OCCUPANCY_RESERVING,
            }
        )


class OccupancySummaryView(APIView):
    """Counts for an authorized caller. No tenant identity, address, or document."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        visible = Lease.objects.filter(pk__in=leases_visible_to(request.user).values("pk"))
        by_status = {
            row["status"]: row["total"]
            for row in visible.order_by().values("status").annotate(total=Count("pk"))
        }
        return Response(
            {
                "lease_count": sum(by_status.values()),
                "by_status": by_status,
                "occupancy_reserving_count": visible.filter(
                    status__in=LeaseStatus.OCCUPANCY_RESERVING
                ).count(),
                "property_count": visible.values("property_id").distinct().count(),
            }
        )
