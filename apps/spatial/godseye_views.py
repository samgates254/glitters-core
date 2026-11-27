"""Authenticated God's Eye scene, land-profile, and provider endpoints."""

from __future__ import annotations

from django.shortcuts import get_object_or_404
from rest_framework import exceptions, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.organizations.models import Organization
from apps.spatial.godseye_serializers import (
    GeospatialDataSourceSerializer,
    GeospatialSceneSerializer,
    LandIntelligenceProfileSerializer,
    ProviderQueryRecordSerializer,
)
from apps.spatial.models import (
    GeospatialDataSource,
    GeospatialScene,
    LandIntelligenceProfile,
    ProviderQueryRecord,
)
from apps.spatial.services.providers.boundaries import load_published_boundary
from apps.spatial.services.providers.health import run_provider_check
from apps.spatial.services.providers.http import ProviderTransportError
from apps.spatial.services.providers.registry import record_query, sync_provider_catalog
from apps.spatial.services.providers.search import search_provider_catalog
from apps.spatial.services.request_scope import (
    bind_member_organization,
    resolve_request_org_ids,
    scope_member_orgs,
)
from apps.spatial.services.scenes import reference_places
from apps.spatial.throttles import SpatialComputeRateThrottle


def _require_membership(request) -> list:
    org_ids = resolve_request_org_ids(request)
    if not org_ids:
        raise exceptions.PermissionDenied("An active organization membership is required.")
    return org_ids


def _one_organization(request) -> Organization:
    org_ids = _require_membership(request)
    if len(org_ids) != 1:
        raise exceptions.ValidationError({"organization_id": "Select one of your organizations."})
    return Organization.objects.get(pk=org_ids[0])


class GeospatialSceneViewSet(viewsets.ModelViewSet):
    """Saved cameras. Cross-organization rows are hidden."""

    serializer_class = GeospatialSceneSerializer
    permission_classes = [permissions.IsAuthenticated]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]
    queryset = GeospatialScene.objects.select_related("organization").all()

    def get_queryset(self):
        return scope_member_orgs(super().get_queryset(), self.request, "organization_id")

    def perform_create(self, serializer):
        _require_membership(self.request)
        bind_member_organization(self.request, serializer)

    def perform_update(self, serializer):
        serializer.validated_data.pop("organization", None)
        serializer.validated_data.pop("organization_id", None)
        serializer.save()

    @action(detail=False, methods=["get"], url_path="places")
    def places(self, request):
        _require_membership(request)
        places = reference_places()
        zones = {place["utm_epsg"] for place in places}
        return Response(
            {
                "places": places,
                "utm_zones_differ": len(zones) > 1,
                "note": "City cameras are not parcels, titles, or survey marks.",
            }
        )


class LandIntelligenceProfileViewSet(viewsets.ModelViewSet):
    """User-supplied outlines and UTM screening measurements."""

    serializer_class = LandIntelligenceProfileSerializer
    permission_classes = [permissions.IsAuthenticated]
    http_method_names = ["get", "post", "head", "options"]
    queryset = LandIntelligenceProfile.objects.select_related("organization").all()

    def get_queryset(self):
        return scope_member_orgs(super().get_queryset(), self.request, "organization_id")

    def perform_create(self, serializer):
        _require_membership(self.request)
        bind_member_organization(self.request, serializer)


class GeospatialDataSourceViewSet(viewsets.ReadOnlyModelViewSet):
    """Provider control center. Licence class does not change because a host answered."""

    serializer_class = GeospatialDataSourceSerializer
    permission_classes = [permissions.IsAuthenticated]
    queryset = GeospatialDataSource.objects.all()
    lookup_field = "provider_id"

    def get_queryset(self):
        _require_membership(self.request)
        sync_provider_catalog()
        return super().get_queryset()

    def get_throttles(self):
        if self.action in {"check", "catalog_search", "boundary"}:
            return [SpatialComputeRateThrottle()]
        return super().get_throttles()

    @action(detail=True, methods=["post"], url_path="check")
    def check(self, request, provider_id=None):
        _require_membership(request)
        get_object_or_404(self.get_queryset(), provider_id=provider_id)
        organization = None
        org_ids = resolve_request_org_ids(request)
        if len(org_ids) == 1:
            organization = Organization.objects.get(pk=org_ids[0])
        row = run_provider_check(provider_id, user=request.user, organization=organization)
        return Response(self.get_serializer(row).data)

    @action(detail=True, methods=["post"], url_path="catalog-search")
    def catalog_search(self, request, provider_id=None):
        organization = _one_organization(request)
        get_object_or_404(self.get_queryset(), provider_id=provider_id)
        try:
            payload = search_provider_catalog(
                provider_id=provider_id,
                organization=organization,
                user=request.user,
                payload=request.data,
            )
        except ProviderTransportError as exc:
            return Response(
                {"errors": {"detail": exc.message}},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except LookupError:
            raise exceptions.NotFound("That data source is not registered.") from None
        return Response(payload, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get"], url_path="boundary")
    def boundary(self, request, provider_id=None):
        """Simplified public geometry from the provider host. It is not stored."""

        organization = _one_organization(request)
        get_object_or_404(self.get_queryset(), provider_id=provider_id)
        try:
            payload = load_published_boundary(provider_id)
        except ProviderTransportError as exc:
            record_query(
                provider=get_object_or_404(GeospatialDataSource, provider_id=provider_id),
                organization=organization,
                user=request.user,
                query_kind="boundary",
                http_status=exc.http_status,
                operational_state="unreachable",
                error=exc.message,
            )
            return Response(
                {"errors": {"detail": exc.message}},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except LookupError:
            raise exceptions.NotFound("That data source is not registered.") from None
        record_query(
            provider=get_object_or_404(GeospatialDataSource, provider_id=provider_id),
            organization=organization,
            user=request.user,
            query_kind="boundary",
            http_status=200,
            operational_state="reachable",
            item_count=int(payload["feature_count"]),
        )
        return Response(payload)


class ProviderQueryRecordViewSet(viewsets.ReadOnlyModelViewSet):
    """Audit rows for the caller's organizations. Upstream payloads are not stored."""

    serializer_class = ProviderQueryRecordSerializer
    permission_classes = [permissions.IsAuthenticated]
    queryset = ProviderQueryRecord.objects.select_related("provider").all()

    def get_queryset(self):
        return scope_member_orgs(super().get_queryset(), self.request, "organization_id")
