from django.urls import path

from apps.backoffice.views import (
    DashboardOverviewView,
    StaffRoleAuditView,
    StaffRoleGrantCollectionView,
    StaffRoleGrantRevokeView,
)

urlpatterns = [
    path("dashboard/overview/", DashboardOverviewView.as_view(), name="backoffice-overview"),
    path(
        "roles/assignments/", StaffRoleGrantCollectionView.as_view(), name="backoffice-role-grants"
    ),
    path(
        "roles/assignments/<uuid:grant_id>/revoke/",
        StaffRoleGrantRevokeView.as_view(),
        name="backoffice-role-grant-revoke",
    ),
    path("roles/audit/", StaffRoleAuditView.as_view(), name="backoffice-role-audit"),
]
