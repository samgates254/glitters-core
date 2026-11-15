"""HTTP adapters for organization services."""

from __future__ import annotations

from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.organizations.domain import (
    INVITATION_SENT,
    Permission,
    permission_names,
    reserved_permissions,
    role_catalogue,
)
from apps.organizations.errors import OrganizationError, raise_for_organization
from apps.organizations.permissions import HasOrganizationPermission
from apps.organizations.selectors import (
    accessible_organizations,
    audit_events,
    members_of,
    memberships_for_user,
    pending_invitations,
)
from apps.organizations.serializers import (
    InvitationCreateSerializer,
    InvitationInspectSerializer,
    InvitationSerializer,
    InvitationTokenSerializer,
    MembershipRoleSerializer,
    MembershipSerializer,
    MessageSerializer,
    MyMembershipSerializer,
    OrganizationCreateSerializer,
    OrganizationEventSerializer,
    OrganizationSerializer,
    OrganizationStatusResultSerializer,
    OrganizationStatusSerializer,
    OrganizationUpdateSerializer,
    OwnershipTransferSerializer,
    PermissionSerializer,
    RoleCatalogueSerializer,
)
from apps.organizations.services.authorization import authorize, ensure_active_account
from apps.organizations.services.invitations import (
    accept_invitation,
    create_invitation,
    get_invitation,
    inspect_invitation,
    resend_invitation,
    revoke_invitation,
)
from apps.organizations.services.memberships import (
    get_membership,
    reactivate_membership,
    remove_membership,
    suspend_membership,
    transfer_ownership,
    update_membership_role,
)
from apps.organizations.services.organizations import (
    change_organization_status,
    create_organization,
    get_organization,
    update_organization,
)


def _call(func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except OrganizationError as exc:
        raise_for_organization(exc)


def _page(request, queryset, serializer_class):
    from rest_framework.pagination import PageNumberPagination

    paginator = PageNumberPagination()
    paginator.page_size = 25
    paginator.page_size_query_param = "page_size"
    paginator.max_page_size = 100
    page = paginator.paginate_queryset(queryset, request, view=request.parser_context.get("view"))
    return paginator.get_paginated_response(serializer_class(page, many=True).data)


@method_decorator(csrf_protect, name="dispatch")
class OrganizationAPIView(APIView):
    """DRF marks handlers CSRF-exempt. These views opt back into the check."""

    permission_classes = [IsAuthenticated]


def _organization_permissions(view, permission: str):
    view.required_permission = permission
    return [IsAuthenticated(), HasOrganizationPermission()]


@extend_schema_view(
    get=extend_schema(responses={200: OrganizationSerializer(many=True)}, tags=["organizations"]),
    post=extend_schema(
        request=OrganizationCreateSerializer,
        responses={201: OrganizationSerializer},
        tags=["organizations"],
    ),
)
class OrganizationCollectionView(OrganizationAPIView):
    def get(self, request):
        _call(ensure_active_account, request.user)
        return _page(request, accessible_organizations(request.user), OrganizationSerializer)

    def post(self, request):
        serializer = OrganizationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        organization = _call(
            create_organization,
            user=request.user,
            fields=serializer.validated_data,
            request=request,
        )
        return Response(OrganizationSerializer(organization).data, status=201)


@extend_schema_view(
    get=extend_schema(responses={200: OrganizationSerializer}, tags=["organizations"]),
    patch=extend_schema(
        request=OrganizationUpdateSerializer,
        responses={200: OrganizationSerializer},
        tags=["organizations"],
    ),
)
class OrganizationDetailView(OrganizationAPIView):
    def get_permissions(self):
        permission = (
            Permission.ORGANIZATION_UPDATE
            if self.request.method == "PATCH"
            else Permission.ORGANIZATION_VIEW
        )
        return _organization_permissions(self, permission)

    def get(self, request, organization_id):
        organization = _call(get_organization, user=request.user, organization_id=organization_id)
        return Response(OrganizationSerializer(organization).data)

    def patch(self, request, organization_id):
        serializer = OrganizationUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        organization = _call(
            update_organization,
            user=request.user,
            organization_id=organization_id,
            fields=serializer.validated_data,
            request=request,
        )
        return Response(OrganizationSerializer(organization).data)


class OrganizationStatusView(OrganizationAPIView):
    @extend_schema(
        request=OrganizationStatusSerializer,
        responses={200: OrganizationStatusResultSerializer},
        tags=["organizations"],
    )
    def post(self, request, organization_id):
        serializer = OrganizationStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        organization = _call(
            change_organization_status,
            actor=request.user,
            organization_id=organization_id,
            status=serializer.validated_data["status"],
            request=request,
        )
        return Response({"id": organization.id, "status": organization.status})


class MyMembershipListView(OrganizationAPIView):
    @extend_schema(responses={200: MyMembershipSerializer(many=True)}, tags=["memberships"])
    def get(self, request):
        _call(ensure_active_account, request.user)
        return _page(request, memberships_for_user(request.user), MyMembershipSerializer)


class MembershipCollectionView(OrganizationAPIView):
    def get_permissions(self):
        return _organization_permissions(self, Permission.ORGANIZATION_VIEW)

    @extend_schema(responses={200: MembershipSerializer(many=True)}, tags=["memberships"])
    def get(self, request, organization_id):
        _call(authorize, request.user, organization_id, Permission.ORGANIZATION_VIEW)
        organization = request.glitters_organization
        return _page(request, members_of(organization), MembershipSerializer)


class MembershipDetailView(OrganizationAPIView):
    def get_permissions(self):
        permission = (
            Permission.ORGANIZATION_MANAGE_ROLES
            if self.request.method == "PATCH"
            else Permission.ORGANIZATION_VIEW
        )
        return _organization_permissions(self, permission)

    @extend_schema(responses={200: MembershipSerializer}, tags=["memberships"])
    def get(self, request, organization_id, membership_id):
        membership = _call(
            get_membership,
            user=request.user,
            organization_id=organization_id,
            membership_id=membership_id,
        )
        return Response(MembershipSerializer(membership).data)

    @extend_schema(
        request=MembershipRoleSerializer,
        responses={200: MembershipSerializer},
        tags=["memberships"],
    )
    def patch(self, request, organization_id, membership_id):
        serializer = MembershipRoleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        membership = _call(
            update_membership_role,
            actor=request.user,
            organization_id=organization_id,
            membership_id=membership_id,
            role=serializer.validated_data["role"],
            request=request,
        )
        return Response(MembershipSerializer(membership).data)


class MembershipSuspendView(OrganizationAPIView):
    def get_permissions(self):
        return _organization_permissions(self, Permission.ORGANIZATION_MANAGE_MEMBERS)

    @extend_schema(request=None, responses={200: MembershipSerializer}, tags=["memberships"])
    def post(self, request, organization_id, membership_id):
        membership = _call(
            suspend_membership,
            actor=request.user,
            organization_id=organization_id,
            membership_id=membership_id,
            request=request,
        )
        return Response(MembershipSerializer(membership).data)


class MembershipReactivateView(OrganizationAPIView):
    def get_permissions(self):
        return _organization_permissions(self, Permission.ORGANIZATION_MANAGE_MEMBERS)

    @extend_schema(request=None, responses={200: MembershipSerializer}, tags=["memberships"])
    def post(self, request, organization_id, membership_id):
        membership = _call(
            reactivate_membership,
            actor=request.user,
            organization_id=organization_id,
            membership_id=membership_id,
            request=request,
        )
        return Response(MembershipSerializer(membership).data)


class MembershipRemoveView(OrganizationAPIView):
    def get_permissions(self):
        # Self-service leave is enforced in the service. Removal of another
        # member requires organization.remove_members.
        return _organization_permissions(self, Permission.ORGANIZATION_VIEW)

    @extend_schema(request=None, responses={200: MembershipSerializer}, tags=["memberships"])
    def post(self, request, organization_id, membership_id):
        membership = _call(
            remove_membership,
            actor=request.user,
            organization_id=organization_id,
            membership_id=membership_id,
            request=request,
        )
        return Response(MembershipSerializer(membership).data)


class OwnershipTransferView(OrganizationAPIView):
    def get_permissions(self):
        return _organization_permissions(self, Permission.ORGANIZATION_TRANSFER_OWNERSHIP)

    @extend_schema(
        request=OwnershipTransferSerializer,
        responses={200: MembershipSerializer},
        tags=["memberships"],
    )
    def post(self, request, organization_id):
        serializer = OwnershipTransferSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        membership = _call(
            transfer_ownership,
            actor=request.user,
            organization_id=organization_id,
            membership_id=serializer.validated_data["membership_id"],
            request=request,
        )
        return Response(MembershipSerializer(membership).data)


class InvitationCollectionView(OrganizationAPIView):
    def get_permissions(self):
        return _organization_permissions(self, Permission.ORGANIZATION_INVITE_MEMBERS)

    @extend_schema(responses={200: InvitationSerializer(many=True)}, tags=["invitations"])
    def get(self, request, organization_id):
        _call(authorize, request.user, organization_id, Permission.ORGANIZATION_INVITE_MEMBERS)
        queryset = pending_invitations(request.glitters_organization)
        return _page(request, queryset, InvitationSerializer)

    @extend_schema(
        request=InvitationCreateSerializer,
        responses={201: InvitationSerializer},
        tags=["invitations"],
    )
    def post(self, request, organization_id):
        serializer = InvitationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        invitation = _call(
            create_invitation,
            actor=request.user,
            organization_id=organization_id,
            email=serializer.validated_data["email"],
            role=serializer.validated_data["role"],
            request=request,
        )
        return Response(InvitationSerializer(invitation).data, status=201)


class InvitationDetailView(OrganizationAPIView):
    def get_permissions(self):
        return _organization_permissions(self, Permission.ORGANIZATION_INVITE_MEMBERS)

    @extend_schema(responses={200: InvitationSerializer}, tags=["invitations"])
    def get(self, request, organization_id, invitation_id):
        invitation = _call(
            get_invitation,
            user=request.user,
            organization_id=organization_id,
            invitation_id=invitation_id,
            request=request,
        )
        return Response(InvitationSerializer(invitation).data)


class InvitationResendView(OrganizationAPIView):
    def get_permissions(self):
        return _organization_permissions(self, Permission.ORGANIZATION_INVITE_MEMBERS)

    @extend_schema(request=None, responses={200: MessageSerializer}, tags=["invitations"])
    def post(self, request, organization_id, invitation_id):
        _call(
            resend_invitation,
            actor=request.user,
            organization_id=organization_id,
            invitation_id=invitation_id,
            request=request,
        )
        return Response({"detail": INVITATION_SENT})


class InvitationRevokeView(OrganizationAPIView):
    def get_permissions(self):
        return _organization_permissions(self, Permission.ORGANIZATION_INVITE_MEMBERS)

    @extend_schema(request=None, responses={200: InvitationSerializer}, tags=["invitations"])
    def post(self, request, organization_id, invitation_id):
        invitation = _call(
            revoke_invitation,
            actor=request.user,
            organization_id=organization_id,
            invitation_id=invitation_id,
            request=request,
        )
        return Response(InvitationSerializer(invitation).data)


class InvitationAcceptView(OrganizationAPIView):
    @extend_schema(
        request=InvitationTokenSerializer,
        responses={200: MembershipSerializer},
        tags=["invitations"],
    )
    def post(self, request):
        serializer = InvitationTokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        membership = _call(
            accept_invitation,
            user=request.user,
            raw_token=serializer.validated_data["token"],
            request=request,
        )
        return Response(MembershipSerializer(membership).data)


class InvitationInspectView(OrganizationAPIView):
    @extend_schema(
        request=InvitationTokenSerializer,
        responses={200: InvitationInspectSerializer},
        tags=["invitations"],
    )
    def post(self, request):
        serializer = InvitationTokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = _call(
            inspect_invitation,
            user=request.user,
            raw_token=serializer.validated_data["token"],
            request=request,
        )
        return Response(InvitationInspectSerializer(payload).data)


class OrganizationPermissionView(OrganizationAPIView):
    def get_permissions(self):
        return _organization_permissions(self, Permission.ORGANIZATION_VIEW)

    @extend_schema(responses={200: PermissionSerializer}, tags=["authorization"])
    def get(self, request, organization_id):
        membership = _call(
            authorize,
            request.user,
            organization_id,
            Permission.ORGANIZATION_VIEW,
        )
        return Response(
            {
                "organization_id": membership.organization_id,
                "role": membership.role,
                "permissions": permission_names(membership.role),
            }
        )


class RoleCatalogueView(OrganizationAPIView):
    @extend_schema(responses={200: RoleCatalogueSerializer}, tags=["authorization"])
    def get(self, request):
        _call(ensure_active_account, request.user)
        return Response(
            {
                "roles": role_catalogue(),
                "reserved_permissions": reserved_permissions(),
            }
        )


class OrganizationAuditView(OrganizationAPIView):
    def get_permissions(self):
        return _organization_permissions(self, Permission.ORGANIZATION_VIEW_AUDIT)

    @extend_schema(responses={200: OrganizationEventSerializer(many=True)}, tags=["audit"])
    def get(self, request, organization_id):
        _call(authorize, request.user, organization_id, Permission.ORGANIZATION_VIEW_AUDIT)
        queryset = audit_events(request.glitters_organization)
        return _page(request, queryset, OrganizationEventSerializer)
