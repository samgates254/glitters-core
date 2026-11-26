"""Tenancy API routes."""

from django.urls import path

from apps.tenancy.views import (
    LeaseActivateView,
    LeaseApproveView,
    LeaseCancelView,
    LeaseCollectionView,
    LeaseDetailView,
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
    path("leases/<uuid:lease_id>/document/", LeaseDocumentView.as_view(), name="lease-document"),
    path(
        "leases/<uuid:lease_id>/reservation/",
        LeaseReservationView.as_view(),
        name="lease-reservation",
    ),
    path("summaries/occupancy/", OccupancySummaryView.as_view(), name="occupancy-summary"),
]
