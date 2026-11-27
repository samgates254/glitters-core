from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.backoffice.errors import BackofficeError, raise_for_backoffice
from apps.backoffice.serializers import (
    DashboardOverviewSerializer,
    StaffRoleEventSerializer,
    StaffRoleGrantCreateSerializer,
    StaffRoleGrantSerializer,
)
from apps.backoffice.services import (
    dashboard_overview,
    grant_staff_role,
    revoke_staff_role,
    role_events_for_administrator,
    role_grants_for_administrator,
)


class BackofficePagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100


def _call(service, **kwargs):
    try:
        return service(**kwargs)
    except BackofficeError as exc:
        raise_for_backoffice(exc)


def _page(queryset, request, view, serializer):
    paginator = BackofficePagination()
    page = paginator.paginate_queryset(queryset, request, view=view)
    return paginator.get_paginated_response(serializer(page, many=True).data)


class DashboardOverviewView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: DashboardOverviewSerializer}, tags=["backoffice"])
    def get(self, request):
        data = _call(dashboard_overview, actor=request.user)
        return Response(DashboardOverviewSerializer(data).data)


class StaffRoleGrantCollectionView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: StaffRoleGrantSerializer(many=True)}, tags=["backoffice"])
    def get(self, request):
        grants = _call(role_grants_for_administrator, actor=request.user)
        return _page(grants, request, self, StaffRoleGrantSerializer)

    @extend_schema(
        request=StaffRoleGrantCreateSerializer,
        responses={201: StaffRoleGrantSerializer},
        tags=["backoffice"],
    )
    def post(self, request):
        serializer = StaffRoleGrantCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        grant = _call(
            grant_staff_role,
            actor=request.user,
            **serializer.validated_data,
        )
        return Response(
            StaffRoleGrantSerializer(grant).data,
            status=status.HTTP_201_CREATED,
        )


class StaffRoleGrantRevokeView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=None, responses={200: StaffRoleGrantSerializer}, tags=["backoffice"])
    def post(self, request, grant_id):
        grant = _call(revoke_staff_role, actor=request.user, grant_id=grant_id)
        return Response(StaffRoleGrantSerializer(grant).data)


class StaffRoleAuditView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: StaffRoleEventSerializer(many=True)}, tags=["backoffice"])
    def get(self, request):
        events = _call(role_events_for_administrator, actor=request.user)
        return _page(events, request, self, StaffRoleEventSerializer)
