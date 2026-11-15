"""Property API representations.

PropertySummarySerializer has no coordinate field. The detail serializer
includes one only when the view sets reveal_coordinates after authorization.
"""

from __future__ import annotations

from django.core.exceptions import ObjectDoesNotExist
from rest_framework import serializers

from apps.properties.domain import (
    HierarchyRole,
    ManagementContext,
    PartyRole,
    PartyStatus,
    PropertyStatus,
    PropertyType,
    rounded_distance_metres,
)
from apps.properties.models import PropertyAccess, PropertyAttribute, PropertyEvent, PropertyParty


class AddressOutputSerializer(serializers.Serializer):
    country = serializers.CharField()
    country_name = serializers.CharField()
    administrative_area_id = serializers.UUIDField(allow_null=True)
    administrative_area_name = serializers.CharField(allow_blank=True)
    locality_id = serializers.UUIDField(allow_null=True)
    locality_name = serializers.CharField(allow_blank=True)
    street_line = serializers.CharField(allow_blank=True)
    building_name = serializers.CharField(allow_blank=True)
    unit_label = serializers.CharField(allow_blank=True)
    postal_code = serializers.CharField(allow_blank=True)


class CharacteristicInputSerializer(serializers.Serializer):
    code = serializers.CharField()
    value = serializers.JSONField()


class CharacteristicSerializer(serializers.ModelSerializer):
    # method_name avoids defining get_value, which would hide Field.get_value.
    value = serializers.SerializerMethodField(method_name="display_value")

    class Meta:
        model = PropertyAttribute
        fields = ("code", "value", "unit")

    def display_value(self, obj):
        if obj.value_integer is not None:
            return obj.value_integer
        if obj.value_decimal is not None:
            return str(obj.value_decimal)
        return obj.value_text


class PropertyCreateSerializer(serializers.Serializer):
    management_context = serializers.ChoiceField(choices=ManagementContext.CHOICES)
    organization_id = serializers.UUIDField(required=False, allow_null=True)
    property_type = serializers.ChoiceField(choices=PropertyType.CHOICES)
    property_subtype = serializers.CharField(max_length=32)
    name = serializers.CharField(max_length=200)
    description = serializers.CharField(required=False, allow_blank=True, max_length=4000)
    hierarchy_role = serializers.ChoiceField(choices=HierarchyRole.CHOICES)
    parent_id = serializers.UUIDField(required=False, allow_null=True)
    status = serializers.ChoiceField(choices=PropertyStatus.CHOICES, required=False)
    address = serializers.DictField(required=False, allow_null=True)
    coordinates = serializers.DictField(required=False, allow_null=True)
    characteristics = CharacteristicInputSerializer(many=True, required=False)


class PropertyWriteSerializer(serializers.Serializer):
    management_context = serializers.ChoiceField(choices=ManagementContext.CHOICES, required=False)
    organization_id = serializers.UUIDField(required=False, allow_null=True)
    property_type = serializers.ChoiceField(choices=PropertyType.CHOICES, required=False)
    property_subtype = serializers.CharField(required=False, max_length=32)
    name = serializers.CharField(required=False, max_length=200)
    description = serializers.CharField(required=False, allow_blank=True, max_length=4000)
    hierarchy_role = serializers.ChoiceField(choices=HierarchyRole.CHOICES, required=False)
    parent_id = serializers.UUIDField(required=False, allow_null=True)
    status = serializers.ChoiceField(choices=PropertyStatus.CHOICES, required=False)
    address = serializers.DictField(required=False, allow_null=True)
    coordinates = serializers.DictField(required=False, allow_null=True)
    characteristics = CharacteristicInputSerializer(many=True, required=False)


class PropertySummarySerializer(serializers.Serializer):
    """List and search shape. There is intentionally no coordinates field."""

    id = serializers.UUIDField()
    reference = serializers.CharField()
    name = serializers.CharField()
    property_type = serializers.CharField()
    property_subtype = serializers.CharField()
    status = serializers.CharField()
    hierarchy_role = serializers.CharField()
    parent_id = serializers.UUIDField(allow_null=True)
    management_context = serializers.CharField()
    organization_id = serializers.UUIDField(source="managing_organization_id", allow_null=True)
    has_coordinates = serializers.SerializerMethodField()
    country = serializers.SerializerMethodField()
    administrative_area_id = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()
    distance_m = serializers.SerializerMethodField()

    def get_has_coordinates(self, obj) -> bool:
        location = _location(obj)
        return bool(location and location.coordinates is not None)

    def get_country(self, obj):
        address = _address(obj)
        return None if address is None else address.country_id

    def get_administrative_area_id(self, obj):
        address = _address(obj)
        return None if address is None else address.administrative_area_id

    def get_distance_m(self, obj):
        if not hasattr(obj, "distance_m"):
            return None
        value = obj.distance_m
        if value is None:
            return None
        metres = value.m if hasattr(value, "m") else value
        return rounded_distance_metres(metres)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if not hasattr(instance, "distance_m"):
            data.pop("distance_m", None)
        return data


class PropertyDetailSerializer(PropertySummarySerializer):
    description = serializers.CharField()
    created_by_id = serializers.UUIDField()
    archived_at = serializers.DateTimeField(allow_null=True)
    address = serializers.SerializerMethodField()
    coordinates = serializers.SerializerMethodField()

    def get_address(self, obj):
        address = _address(obj)
        if address is None:
            return None
        area = address.administrative_area
        locality = address.locality
        return AddressOutputSerializer(
            {
                "country": address.country_id,
                "country_name": address.country.name,
                "administrative_area_id": None if area is None else area.id,
                "administrative_area_name": "" if area is None else area.name,
                "locality_id": None if locality is None else locality.id,
                "locality_name": "" if locality is None else locality.name,
                "street_line": address.street_line,
                "building_name": address.building_name,
                "unit_label": address.unit_label,
                "postal_code": address.postal_code,
            }
        ).data

    def get_coordinates(self, obj):
        location = _location(obj)
        if location is None or location.coordinates is None:
            return None
        return {
            "latitude": location.coordinates.y,
            "longitude": location.coordinates.x,
        }

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if not self.context.get("reveal_coordinates") or data.get("coordinates") is None:
            data.pop("coordinates", None)
        return data


class PartySerializer(serializers.ModelSerializer):
    user_id = serializers.UUIDField(read_only=True, allow_null=True)
    organization_id = serializers.UUIDField(read_only=True, allow_null=True)
    legal_title_verified = serializers.SerializerMethodField()

    class Meta:
        model = PropertyParty
        fields = (
            "id",
            "subject_kind",
            "user_id",
            "organization_id",
            "role",
            "status",
            "legal_title_verified",
            "note",
            "created_at",
            "revoked_at",
        )

    def get_legal_title_verified(self, obj) -> bool:
        return obj.status == PartyStatus.VERIFIED


class PartyWriteSerializer(serializers.Serializer):
    subject_kind = serializers.ChoiceField(choices=["person", "organization"])
    user_id = serializers.UUIDField(required=False, allow_null=True)
    organization_id = serializers.UUIDField(required=False, allow_null=True)
    role = serializers.ChoiceField(choices=PartyRole.CHOICES)
    note = serializers.CharField(required=False, allow_blank=True, max_length=500)
    access_capability = serializers.ChoiceField(
        choices=["view", "manage"],
        required=False,
        allow_null=True,
    )


class PartyUpdateSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=PartyRole.CHOICES, required=False)
    note = serializers.CharField(required=False, allow_blank=True, max_length=500)
    status = serializers.ChoiceField(
        choices=["pending_verification", "rejected", "verified"],
        required=False,
    )


class AccessSerializer(serializers.ModelSerializer):
    user_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = PropertyAccess
        fields = (
            "id",
            "user_id",
            "capability",
            "status",
            "origin",
            "inherited_from",
            "created_at",
            "revoked_at",
        )


class PropertyEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = PropertyEvent
        fields = (
            "id",
            "action",
            "object_type",
            "object_id",
            "request_id",
            "metadata",
            "created_at",
        )


class RevokePartySerializer(serializers.Serializer):
    revoke_access = serializers.BooleanField(required=False, default=False)


def _location(obj):
    try:
        return obj.location
    except ObjectDoesNotExist:
        return None


def _address(obj):
    location = _location(obj)
    if location is None:
        return None
    return location.address
