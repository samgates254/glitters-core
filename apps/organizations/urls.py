"""Organization, membership, invitation, and authorization routes."""

from django.urls import path

from apps.organizations.views import (
    InvitationAcceptView,
    InvitationCollectionView,
    InvitationDetailView,
    InvitationInspectView,
    InvitationResendView,
    InvitationRevokeView,
    MembershipCollectionView,
    MembershipDetailView,
    MembershipReactivateView,
    MembershipRemoveView,
    MembershipSuspendView,
    MyMembershipListView,
    OrganizationAuditView,
    OrganizationCollectionView,
    OrganizationDetailView,
    OrganizationPermissionView,
    OrganizationStatusView,
    OwnershipTransferView,
    RoleCatalogueView,
)

urlpatterns = [
    path("organizations/", OrganizationCollectionView.as_view(), name="organization-list"),
    path(
        "organizations/<uuid:organization_id>/",
        OrganizationDetailView.as_view(),
        name="organization-detail",
    ),
    path(
        "organizations/<uuid:organization_id>/status/",
        OrganizationStatusView.as_view(),
        name="organization-status",
    ),
    path("me/memberships/", MyMembershipListView.as_view(), name="my-memberships"),
    path(
        "organizations/<uuid:organization_id>/members/",
        MembershipCollectionView.as_view(),
        name="organization-members",
    ),
    path(
        "organizations/<uuid:organization_id>/members/<uuid:membership_id>/",
        MembershipDetailView.as_view(),
        name="organization-member-detail",
    ),
    path(
        "organizations/<uuid:organization_id>/members/<uuid:membership_id>/suspend/",
        MembershipSuspendView.as_view(),
        name="organization-member-suspend",
    ),
    path(
        "organizations/<uuid:organization_id>/members/<uuid:membership_id>/reactivate/",
        MembershipReactivateView.as_view(),
        name="organization-member-reactivate",
    ),
    path(
        "organizations/<uuid:organization_id>/members/<uuid:membership_id>/remove/",
        MembershipRemoveView.as_view(),
        name="organization-member-remove",
    ),
    path(
        "organizations/<uuid:organization_id>/ownership-transfer/",
        OwnershipTransferView.as_view(),
        name="organization-ownership-transfer",
    ),
    path(
        "organizations/<uuid:organization_id>/invitations/",
        InvitationCollectionView.as_view(),
        name="organization-invitations",
    ),
    path(
        "organizations/<uuid:organization_id>/invitations/<uuid:invitation_id>/",
        InvitationDetailView.as_view(),
        name="organization-invitation-detail",
    ),
    path(
        "organizations/<uuid:organization_id>/invitations/<uuid:invitation_id>/resend/",
        InvitationResendView.as_view(),
        name="organization-invitation-resend",
    ),
    path(
        "organizations/<uuid:organization_id>/invitations/<uuid:invitation_id>/revoke/",
        InvitationRevokeView.as_view(),
        name="organization-invitation-revoke",
    ),
    path("invitations/accept/", InvitationAcceptView.as_view(), name="invitation-accept"),
    path("invitations/inspect/", InvitationInspectView.as_view(), name="invitation-inspect"),
    path(
        "organizations/<uuid:organization_id>/permissions/",
        OrganizationPermissionView.as_view(),
        name="organization-permissions",
    ),
    path("organization-roles/", RoleCatalogueView.as_view(), name="organization-roles"),
    path(
        "organizations/<uuid:organization_id>/audit/",
        OrganizationAuditView.as_view(),
        name="organization-audit",
    ),
]
