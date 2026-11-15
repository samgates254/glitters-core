"""URL routes for property operations."""

from django.urls import path

from apps.operations.views import (
    ManagedPortfolioView,
    ManagementAgreementAcceptView,
    ManagementAgreementActivateView,
    ManagementAgreementDetailView,
    ManagementAgreementListCreateView,
    ManagementAgreementRejectView,
    ManagementAgreementRevokeAuthorityView,
    ManagementAgreementRevokeView,
    ManagementAgreementTerminateView,
    UnitPhysicalProfileView,
)

app_name = "operations"

urlpatterns = [
    path("agreements/", ManagementAgreementListCreateView.as_view(), name="agreements-list-create"),
    path("agreements/<uuid:pk>/", ManagementAgreementDetailView.as_view(), name="agreements-detail"),
    path("agreements/<uuid:pk>/accept/", ManagementAgreementAcceptView.as_view(), name="agreements-accept"),
    path("agreements/<uuid:pk>/reject/", ManagementAgreementRejectView.as_view(), name="agreements-reject"),
    path("agreements/<uuid:pk>/activate/", ManagementAgreementActivateView.as_view(), name="agreements-activate"),
    path("agreements/<uuid:pk>/revoke/", ManagementAgreementRevokeView.as_view(), name="agreements-revoke"),
    path("agreements/<uuid:pk>/terminate/", ManagementAgreementTerminateView.as_view(), name="agreements-terminate"),
    path("agreements/<uuid:pk>/revoke-authority/", ManagementAgreementRevokeAuthorityView.as_view(), name="agreements-revoke-authority"),
    path("portfolio/", ManagedPortfolioView.as_view(), name="managed-portfolio"),
    path("units/<uuid:property_id>/physical-profile/", UnitPhysicalProfileView.as_view(), name="unit-physical-profile"),
]
