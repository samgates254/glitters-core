"""HTTP adapters. Authorization is repeated in the services."""

from __future__ import annotations

from django.db import transaction
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.locations.errors import LocationError, raise_for_location
from apps.organizations.errors import OrganizationError, raise_for_organization
from apps.properties.domain import ARCHIVED, PERMISSION_DENIED, PropertyStatus
from apps.properties.errors import PropertyError, raise_for_property
from apps.properties.filters import parse_property_filters
from apps.properties.models import Property, PropertyEvent
from apps.properties.selectors import search_properties
from apps.properties.serializers import (
    AccessSerializer,
    CharacteristicSerializer,
    PartySerializer,
    PartyUpdateSerializer,
    PartyWriteSerializer,
    PropertyCreateSerializer,
    PropertyDetailSerializer,
    PropertyEventSerializer,
    PropertySummarySerializer,
    PropertyWriteSerializer,
    RevokePartySerializer,
)
from apps.properties.services.authorization import decide
from apps.properties.services.characteristics import replace_characteristics
from apps.properties.services.records import archive_property, create_property, update_property
from apps.properties.services.relationships import (
    add_party,
    list_relationships,
    revoke_access,
    revoke_party,
    update_party,
)


def _call(func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except PropertyError as exc:
        raise_for_property(exc)
    except LocationError as exc:
        raise_for_location(exc)
    except OrganizationError as exc:
        raise_for_organization(exc)


def _page(request, queryset, serializer_class, context=None):
    from rest_framework.pagination import PageNumberPagination

    paginator = PageNumberPagination()
    paginator.page_size = 25
    paginator.page_size_query_param = "page_size"
    paginator.max_page_size = 100
    page = paginator.paginate_queryset(queryset, request, view=request.parser_context.get("view"))
    return paginator.get_paginated_response(
        serializer_class(page, many=True, context=context or {}).data
    )


def _detail(record, *, reveal: bool):
    return PropertyDetailSerializer(record, context={"reveal_coordinates": reveal}).data


@method_decorator(csrf_protect, name="dispatch")
class PropertyAPIView(APIView):
    """DRF marks handlers CSRF-exempt. These views opt back into the check."""

    permission_classes = [IsAuthenticated]


class PropertyCollectionView(PropertyAPIView):
    @extend_schema(
        parameters=[
            OpenApiParameter(name="status", type=str, required=False),
            OpenApiParameter(name="property_type", type=str, required=False),
            OpenApiParameter(name="management_context", type=str, required=False),
            OpenApiParameter(name="organization_id", type=str, required=False),
            OpenApiParameter(name="parent_id", type=str, required=False),
            OpenApiParameter(name="country", type=str, required=False),
            OpenApiParameter(name="administrative_area_id", type=str, required=False),
            OpenApiParameter(
                name="near",
                type=str,
                required=False,
                description=(
                    "Latitude,longitude in WGS84. Requires radius_m. The point is not returned."
                ),
            ),
            OpenApiParameter(name="radius_m", type=int, required=False),
            OpenApiParameter(
                name="bbox",
                type=str,
                required=False,
                description="min_lon,min_lat,max_lon,max_lat. The stored point is not returned.",
            ),
        ],
        responses={200: PropertySummarySerializer(many=True)},
        tags=["properties"],
    )
    def get(self, request):
        filters = _call(parse_property_filters, request.query_params)
        queryset = _call(search_properties, request.user, filters)
        return _page(request, queryset, PropertySummarySerializer)

    @extend_schema(
        request=PropertyCreateSerializer,
        responses={201: PropertyDetailSerializer},
        tags=["properties"],
    )
    def post(self, request):
        serializer = PropertyCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        record = _call(
            create_property,
            actor=request.user,
            data=serializer.validated_data,
            request=request,
        )
        decision = _call(decide, request.user, record.id)
        return Response(_detail(record, reveal=decision.can_read_coordinates), status=201)


class PropertyDetailView(PropertyAPIView):
    @extend_schema(responses={200: PropertyDetailSerializer}, tags=["properties"])
    def get(self, request, property_id):
        decision = _call(decide, request.user, property_id)
        return Response(_detail(decision.record, reveal=decision.can_read_coordinates))

    @extend_schema(
        request=PropertyWriteSerializer,
        responses={200: PropertyDetailSerializer},
        tags=["properties"],
    )
    def patch(self, request, property_id):
        serializer = PropertyWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        record = _call(
            update_property,
            actor=request.user,
            property_id=property_id,
            data=serializer.validated_data,
            request=request,
        )
        decision = _call(decide, request.user, record.id)
        return Response(_detail(record, reveal=decision.can_read_coordinates))


class PropertyArchiveView(PropertyAPIView):
    @extend_schema(request=None, responses={200: PropertyDetailSerializer}, tags=["properties"])
    def post(self, request, property_id):
        record = _call(
            archive_property,
            actor=request.user,
            property_id=property_id,
            request=request,
        )
        decision = _call(decide, request.user, record.id)
        return Response(_detail(record, reveal=decision.can_read_coordinates))


class PropertyCharacteristicView(PropertyAPIView):
    @extend_schema(responses={200: CharacteristicSerializer(many=True)}, tags=["properties"])
    def get(self, request, property_id):
        decision = _call(decide, request.user, property_id)
        rows = decision.record.attributes.order_by("code")
        return Response(CharacteristicSerializer(rows, many=True).data)

    @extend_schema(tags=["properties"])
    def put(self, request, property_id):
        decision = _call(decide, request.user, property_id)
        if not decision.can_update:
            raise_for_property(PropertyError(PERMISSION_DENIED, status_code=403))
        items = request.data.get(
            "characteristics",
            request.data if isinstance(request.data, list) else None,
        )

        def _replace():
            with transaction.atomic():
                record = Property.objects.select_for_update().get(pk=property_id)
                if record.status == PropertyStatus.ARCHIVED:
                    raise PropertyError(ARCHIVED, status_code=409)
                return replace_characteristics(
                    record,
                    items,
                    actor=request.user,
                    request=request,
                )

        stored = _call(_replace)
        return Response(CharacteristicSerializer(stored, many=True).data)


class PropertyRelationshipView(PropertyAPIView):
    @extend_schema(tags=["properties"])
    def get(self, request, property_id):
        payload = _call(list_relationships, request.user, property_id)
        body = {"parties": PartySerializer(payload["parties"], many=True).data}
        if "access" in payload:
            body["access"] = AccessSerializer(payload["access"], many=True).data
        return Response(body)

    @extend_schema(
        request=PartyWriteSerializer,
        responses={201: PartySerializer},
        tags=["properties"],
    )
    def post(self, request, property_id):
        serializer = PartyWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        party = _call(
            add_party,
            actor=request.user,
            property_id=property_id,
            data=serializer.validated_data,
            request=request,
        )
        return Response(PartySerializer(party).data, status=201)


class PropertyRelationshipDetailView(PropertyAPIView):
    @extend_schema(
        request=PartyUpdateSerializer,
        responses={200: PartySerializer},
        tags=["properties"],
    )
    def patch(self, request, property_id, party_id):
        serializer = PartyUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        party = _call(
            update_party,
            actor=request.user,
            property_id=property_id,
            party_id=party_id,
            data=serializer.validated_data,
            request=request,
        )
        return Response(PartySerializer(party).data)


class PropertyRelationshipRevokeView(PropertyAPIView):
    @extend_schema(
        request=RevokePartySerializer,
        responses={200: PartySerializer},
        tags=["properties"],
    )
    def post(self, request, property_id, party_id):
        serializer = RevokePartySerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        party = _call(
            revoke_party,
            actor=request.user,
            property_id=property_id,
            party_id=party_id,
            revoke_access=serializer.validated_data.get("revoke_access", False),
            request=request,
        )
        return Response(PartySerializer(party).data)


class PropertyAccessRevokeView(PropertyAPIView):
    @extend_schema(request=None, responses={200: AccessSerializer}, tags=["properties"])
    def post(self, request, property_id, access_id):
        grant = _call(
            revoke_access,
            actor=request.user,
            property_id=property_id,
            access_id=access_id,
            request=request,
        )
        return Response(AccessSerializer(grant).data)


class PropertyAuditView(PropertyAPIView):
    @extend_schema(responses={200: PropertyEventSerializer(many=True)}, tags=["properties"])
    def get(self, request, property_id):
        decision = _call(decide, request.user, property_id)
        if not decision.can_read_audit:
            raise_for_property(PropertyError(PERMISSION_DENIED, status_code=403))
        events = PropertyEvent.objects.filter(property_id=decision.record.id).order_by(
            "-created_at"
        )
        return _page(request, events, PropertyEventSerializer)
