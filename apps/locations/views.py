"""HTTP adapters for location reference data."""

from __future__ import annotations

from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.locations.errors import LocationError, raise_for_location
from apps.locations.selectors import (
    area_hierarchy,
    areas_for_country,
    countries,
    country_or_error,
    search_localities,
    search_locations,
)
from apps.locations.serializers import (
    AddressResolveSerializer,
    AdministrativeAreaSerializer,
    CountrySerializer,
    LevelSerializer,
    LocalitySerializer,
    LocationSearchSerializer,
    ResolvedAddressSerializer,
)
from apps.locations.services.access import ensure_active_account
from apps.locations.services.resolution import resolve_address


def _call(func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except LocationError as exc:
        raise_for_location(exc)


def _page(request, queryset, serializer_class):
    from rest_framework.pagination import PageNumberPagination

    paginator = PageNumberPagination()
    paginator.page_size = 25
    paginator.page_size_query_param = "page_size"
    paginator.max_page_size = 100
    page = paginator.paginate_queryset(queryset, request, view=request.parser_context.get("view"))
    return paginator.get_paginated_response(serializer_class(page, many=True).data)


@method_decorator(csrf_protect, name="dispatch")
class LocationAPIView(APIView):
    """DRF marks handlers CSRF-exempt. These views opt back into the check."""

    permission_classes = [IsAuthenticated]


class CountryListView(LocationAPIView):
    @extend_schema(responses={200: CountrySerializer(many=True)}, tags=["locations"])
    def get(self, request):
        _call(ensure_active_account, request.user)
        return _page(request, countries(), CountrySerializer)


class AdministrativeAreaListView(LocationAPIView):
    @extend_schema(
        parameters=[
            OpenApiParameter(name="parent_id", type=str, required=False),
            OpenApiParameter(name="level_code", type=str, required=False),
        ],
        responses={200: AdministrativeAreaSerializer(many=True)},
        tags=["locations"],
    )
    def get(self, request, country):
        _call(ensure_active_account, request.user)
        selected = _call(country_or_error, country)
        queryset = _call(
            areas_for_country,
            selected,
            parent_id=request.query_params.get("parent_id"),
            level_code=request.query_params.get("level_code"),
        )
        return _page(request, queryset, AdministrativeAreaSerializer)


class AdministrativeAreaHierarchyView(LocationAPIView):
    @extend_schema(tags=["locations"])
    def get(self, request, area_id):
        _call(ensure_active_account, request.user)
        area, ancestors, children, levels = _call(area_hierarchy, area_id)
        return Response(
            {
                "area": AdministrativeAreaSerializer(area).data,
                "ancestors": AdministrativeAreaSerializer(ancestors, many=True).data,
                "children": AdministrativeAreaSerializer(children, many=True).data,
                "levels": LevelSerializer(levels, many=True).data,
            }
        )


class LocalitySearchView(LocationAPIView):
    @extend_schema(
        parameters=[
            OpenApiParameter(name="q", type=str, required=True),
            OpenApiParameter(name="country", type=str, required=False),
            OpenApiParameter(name="administrative_area_id", type=str, required=False),
        ],
        responses={200: LocalitySerializer(many=True)},
        tags=["locations"],
    )
    def get(self, request):
        _call(ensure_active_account, request.user)
        queryset = _call(
            search_localities,
            query=request.query_params.get("q", ""),
            country=request.query_params.get("country"),
            administrative_area_id=request.query_params.get("administrative_area_id"),
        )
        return _page(request, queryset, LocalitySerializer)


class LocationSearchView(LocationAPIView):
    @extend_schema(
        parameters=[
            OpenApiParameter(name="q", type=str, required=True),
            OpenApiParameter(name="country", type=str, required=False),
        ],
        responses={200: LocationSearchSerializer(many=True)},
        tags=["locations"],
    )
    def get(self, request):
        _call(ensure_active_account, request.user)
        queryset = _call(
            search_locations,
            query=request.query_params.get("q", ""),
            country=request.query_params.get("country"),
        )
        return _page(request, queryset, LocationSearchSerializer)


class AddressResolveView(LocationAPIView):
    @extend_schema(
        request=AddressResolveSerializer,
        responses={200: ResolvedAddressSerializer},
        tags=["locations"],
    )
    def post(self, request):
        _call(ensure_active_account, request.user)
        serializer = AddressResolveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        resolved = _call(resolve_address, serializer.validated_data)
        return Response(ResolvedAddressSerializer.from_resolved(resolved))
