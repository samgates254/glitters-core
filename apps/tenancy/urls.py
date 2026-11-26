"""Tenancy API routes."""

from django.urls import path

from apps.tenancy.billing_views import (
    DepositObligationView,
    DepositReceiptReversalView,
    DepositReceiptView,
    LandlordStatementView,
    RentalLedgerView,
    RentAllocationReversalView,
    RentAllocationView,
    RentArrearsView,
    RentInvoiceView,
    RentScheduleView,
    TenantStatementView,
)
from apps.tenancy.views import (
    LeaseActivateView,
    LeaseApproveView,
    LeaseCancelView,
    LeaseCollectionView,
    LeaseDetailView,
    LeaseDocumentContentView,
    LeaseDocumentView,
    LeaseHistoryView,
    LeasePartyListView,
    LeaseRenewalAbandonView,
    LeaseRenewalView,
    LeaseReservationView,
    LeaseSignatureView,
    LeaseTerminateView,
    LeaseTerminationRequestView,
    LeaseTermsView,
    OccupancySummaryView,
)

app_name = "tenancy"

urlpatterns = [
    path("leases/", LeaseCollectionView.as_view(), name="leases"),
    path("leases/<uuid:lease_id>/", LeaseDetailView.as_view(), name="lease-detail"),
    path("leases/<uuid:lease_id>/parties/", LeasePartyListView.as_view(), name="lease-parties"),
    path("leases/<uuid:lease_id>/terms/", LeaseTermsView.as_view(), name="lease-terms"),
    path("leases/<uuid:lease_id>/approve/", LeaseApproveView.as_view(), name="lease-approve"),
    path("leases/<uuid:lease_id>/cancel/", LeaseCancelView.as_view(), name="lease-cancel"),
    path("leases/<uuid:lease_id>/renewals/", LeaseRenewalView.as_view(), name="lease-renewal"),
    path(
        "leases/<uuid:lease_id>/renewals/abandon/",
        LeaseRenewalAbandonView.as_view(),
        name="lease-renewal-abandon",
    ),
    path(
        "leases/<uuid:lease_id>/signatures/",
        LeaseSignatureView.as_view(),
        name="lease-signatures",
    ),
    path(
        "leases/<uuid:lease_id>/sign/",
        LeaseSignatureView.as_view(),
        name="lease-sign",
    ),
    path("leases/<uuid:lease_id>/activate/", LeaseActivateView.as_view(), name="lease-activate"),
    path(
        "leases/<uuid:lease_id>/termination-requests/",
        LeaseTerminationRequestView.as_view(),
        name="lease-termination-request",
    ),
    path("leases/<uuid:lease_id>/terminate/", LeaseTerminateView.as_view(), name="lease-terminate"),
    path("leases/<uuid:lease_id>/history/", LeaseHistoryView.as_view(), name="lease-history"),
    path(
        "leases/<uuid:lease_id>/document/content/",
        LeaseDocumentContentView.as_view(),
        name="lease-document-content",
    ),
    path("leases/<uuid:lease_id>/document/", LeaseDocumentView.as_view(), name="lease-document"),
    path(
        "leases/<uuid:lease_id>/reservation/",
        LeaseReservationView.as_view(),
        name="lease-reservation",
    ),
    path("summaries/occupancy/", OccupancySummaryView.as_view(), name="occupancy-summary"),
    path(
        "leases/<uuid:lease_id>/billing/schedule/",
        RentScheduleView.as_view(),
        name="lease-billing-schedule",
    ),
    path(
        "leases/<uuid:lease_id>/billing/invoices/",
        RentInvoiceView.as_view(),
        name="lease-billing-invoices",
    ),
    path(
        "leases/<uuid:lease_id>/billing/invoices/<uuid:invoice_id>/allocations/",
        RentAllocationView.as_view(),
        name="lease-billing-allocation",
    ),
    path(
        "leases/<uuid:lease_id>/billing/allocations/<uuid:allocation_id>/reversals/",
        RentAllocationReversalView.as_view(),
        name="lease-billing-allocation-reversal",
    ),
    path(
        "leases/<uuid:lease_id>/billing/deposit/",
        DepositObligationView.as_view(),
        name="lease-billing-deposit",
    ),
    path(
        "leases/<uuid:lease_id>/billing/deposit/receipts/",
        DepositReceiptView.as_view(),
        name="lease-billing-deposit-receipt",
    ),
    path(
        "leases/<uuid:lease_id>/billing/deposit/receipts/<uuid:receipt_id>/reversals/",
        DepositReceiptReversalView.as_view(),
        name="lease-billing-deposit-reversal",
    ),
    path(
        "leases/<uuid:lease_id>/billing/arrears/",
        RentArrearsView.as_view(),
        name="lease-billing-arrears",
    ),
    path(
        "leases/<uuid:lease_id>/billing/statements/tenant/",
        TenantStatementView.as_view(),
        name="lease-billing-tenant-statement",
    ),
    path(
        "leases/<uuid:lease_id>/billing/statements/landlord/",
        LandlordStatementView.as_view(),
        name="lease-billing-landlord-statement",
    ),
    path(
        "leases/<uuid:lease_id>/billing/ledger/",
        RentalLedgerView.as_view(),
        name="lease-billing-ledger",
    ),
]
