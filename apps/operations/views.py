"""HTTP API views for property operations and management agreements."""

from __future__ import annotations

import uuid

from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.organizations.domain import MembershipStatus, Permission, permissions_for
from apps.organizations.models import OrganizationMembership
from apps.operations.domain import NOT_FOUND, PERMISSION_DENIED, AgreementStatus
from apps.operations.errors import OperationsError, raise_for_operations
from apps.operations.models import ManagementAgreement, UnitPhysicalProfile
from apps.operations.serializers import (
    AgreementActionReasonSerializer,
    ManagedPortfolioPropertySerializer,
    ManagementAgreementCreateSerializer,
    ManagementAgreementSerializer,
    UnitPhysicalProfileSerializer,
    UnitPhysicalProfileUpdateSerializer,
)
from apps.operations.services.agreements import (
    accept_agreement,
    activate_agreement,
    propose_agreement,
    reject_agreement,
    revoke_agreement,
    revoke_lease_execution_authority,
    sync_agreement_expiry,
    terminate_agreement,
)
from apps.operations.services.physical_profiles import (
    check_operations_authority,
    get_or_create_physical_profile,
    update_physical_profile,
)
from apps.properties.models import Property, PropertyAccess


def _call(func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except OperationsError as exc:
        raise_for_operations(exc)


class ManagementAgreementListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        org_id = request.query_params.get("organization_id")
        property_id = request.query_params.get("property_id")
        status_filter = request.query_params.get("status")

        # Resolve user's active organizations with operations.view or operations.manage
        user_memberships = OrganizationMembership.objects.filter(
            user=user,
            status=MembershipStatus.ACTIVE,
        ).select_related("organization")

        viewable_org_ids = [
            m.organization_id
            for m in user_memberships
            if Permission.OPERATIONS_VIEW in permissions_for(m.role)
        ]

        # Properties where user has direct PropertyAccess
        user_prop_ids = PropertyAccess.objects.filter(
            user=user,
            status="active",
        ).values_list("property_id", flat=True)

        queryset = ManagementAgreement.objects.filter(
            Q(managing_organization_id__in=viewable_org_ids)
            | Q(property_id__in=user_prop_ids)
            | Q(granting_user=user)
            | Q(created_by=user)
        ).distinct()

        if org_id:
            queryset = queryset.filter(managing_organization_id=org_id)
        if property_id:
            queryset = queryset.filter(property_id=property_id)
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        serializer = ManagementAgreementSerializer(queryset, many=True)
        return Response({"agreements": serializer.data})

    def post(self, request):
        serializer = ManagementAgreementCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        agreement = _call(
            propose_agreement,
            actor=request.user,
            property_id=data["property_id"],
            managing_org_id=data["managing_organization_id"],
            spend_limit_minor=data.get("spend_limit_minor", 1_000_000),
            commission_basis_points=data.get("commission_basis_points", 800),
            valid_from=data.get("valid_from"),
            valid_until=data.get("valid_until"),
            can_execute_leases=data.get("can_execute_leases", False),
            granting_user_id=data.get("granting_user_id"),
            assigned_agent_id=data.get("assigned_agent_id"),
            as_draft=data.get("as_draft", False),
            request=request,
        )
        return Response(
            ManagementAgreementSerializer(agreement).data,
            status=status.HTTP_201_CREATED,
        )


class ManagementAgreementDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        try:
            agreement = ManagementAgreement.objects.select_related(
                "property", "managing_organization", "granting_user", "assigned_agent"
            ).get(pk=pk)
        except ManagementAgreement.DoesNotExist:
            return Response({"detail": NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)

        # Access check
        user = request.user
        has_access = (
            user.id == agreement.granting_user_id
            or user.id == agreement.created_by_id
            or (agreement.assigned_agent_id and user.id == agreement.assigned_agent_id)
            or OrganizationMembership.objects.filter(
                organization=agreement.managing_organization,
                user=user,
                status=MembershipStatus.ACTIVE,
            ).exists()
            or PropertyAccess.objects.filter(
                property=agreement.property,
                user=user,
                status="active",
            ).exists()
        )
        if not has_access:
            return Response({"detail": PERMISSION_DENIED}, status=status.HTTP_403_FORBIDDEN)

        return Response(ManagementAgreementSerializer(agreement).data)


class ManagementAgreementAcceptView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        agreement = _call(accept_agreement, actor=request.user, agreement_id=pk, request=request)
        return Response(ManagementAgreementSerializer(agreement).data)


class ManagementAgreementRejectView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        serializer = AgreementActionReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reason = serializer.validated_data.get("reason", "")
        agreement = _call(
            reject_agreement,
            actor=request.user,
            agreement_id=pk,
            reason=reason,
            request=request,
        )
        return Response(ManagementAgreementSerializer(agreement).data)


class ManagementAgreementActivateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        agreement = _call(activate_agreement, actor=request.user, agreement_id=pk, request=request)
        return Response(ManagementAgreementSerializer(agreement).data)


class ManagementAgreementRevokeView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        agreement = _call(revoke_agreement, actor=request.user, agreement_id=pk, request=request)
        return Response(ManagementAgreementSerializer(agreement).data)


class ManagementAgreementTerminateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        serializer = AgreementActionReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reason = serializer.validated_data.get("reason", "")
        agreement = _call(
            terminate_agreement,
            actor=request.user,
            agreement_id=pk,
            reason=reason,
            request=request,
        )
        return Response(ManagementAgreementSerializer(agreement).data)


class ManagementAgreementRevokeAuthorityView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        serializer = AgreementActionReasonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reason = serializer.validated_data.get("reason", "")
        agreement = _call(
            revoke_lease_execution_authority,
            actor=request.user,
            agreement_id=pk,
            reason=reason,
            request=request,
        )
        return Response(ManagementAgreementSerializer(agreement).data)


class UnitPhysicalProfileView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, property_id):
        try:
            property_record = Property.objects.get(pk=property_id)
        except Property.DoesNotExist:
            return Response({"detail": NOT_FOUND}, status=status.HTTP_404_NOT_FOUND)

        try:
            check_operations_authority(request.user, property_record)
        except OperationsError as exc:
            raise_for_operations(exc)

        profile = get_or_create_physical_profile(property_record)
        return Response(UnitPhysicalProfileSerializer(profile).data)

    def patch(self, request, property_id):
        serializer = UnitPhysicalProfileUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        profile = _call(
            update_physical_profile,
            actor=request.user,
            property_id=property_id,
            new_state=data.get("physical_state"),
            target_rent_minor=data.get("target_rent_minor"),
            target_currency=data.get("target_currency"),
            notes=data.get("notes"),
            request=request,
        )
        return Response(UnitPhysicalProfileSerializer(profile).data)


class ManagedPortfolioView(APIView):
    """View portfolio of properties managed by the user's organization."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        org_id = request.query_params.get("organization_id")

        user_memberships = OrganizationMembership.objects.filter(
            user=user,
            status=MembershipStatus.ACTIVE,
        )
        if org_id:
            user_memberships = user_memberships.filter(organization_id=org_id)

        valid_org_ids = [
            m.organization_id
            for m in user_memberships
            if Permission.OPERATIONS_VIEW in permissions_for(m.role)
        ]

        if not valid_org_ids:
            return Response({"portfolio": []})

        # Properties managed via active management agreements
        active_agreements = ManagementAgreement.objects.filter(
            managing_organization_id__in=valid_org_ids,
            status=AgreementStatus.ACTIVE,
        ).select_related("property")

        valid_active_agreements = []
        for a in active_agreements:
            if a.valid_until is not None and timezone.localdate() > a.valid_until:
                sync_agreement_expiry(a)
            else:
                valid_active_agreements.append(a)

        agr_prop_ids = [a.property_id for a in valid_active_agreements]
        agreement_map = {a.property_id: a.reference for a in valid_active_agreements}

        # Also include properties directly owned/managed by the org
        direct_properties = Property.objects.filter(
            managing_organization_id__in=valid_org_ids
        ).exclude(pk__in=agr_prop_ids)

        all_properties = list(
            Property.objects.filter(pk__in=agr_prop_ids)
            .select_related("physical_profile")
        ) + list(direct_properties.select_related("physical_profile"))

        results = []
        for prop in all_properties:
            profile = getattr(prop, "physical_profile", None)
            results.append(
                {
                    "id": prop.id,
                    "name": prop.name,
                    "reference": prop.reference,
                    "property_type": prop.property_type,
                    "property_subtype": prop.property_subtype,
                    "management_context": prop.management_context,
                    "status": prop.status,
                    "physical_state": profile.physical_state if profile else "TURNOVER_PREPARATION",
                    "ready_at": profile.ready_at if profile else None,
                    "target_rent_minor": profile.target_rent_minor if profile else None,
                    "target_currency": profile.target_currency if profile else "KES",
                    "active_agreement_reference": agreement_map.get(prop.id),
                }
            )

        serializer = ManagedPortfolioPropertySerializer(results, many=True)
        return Response({"portfolio": serializer.data})
