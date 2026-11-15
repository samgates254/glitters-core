"""Location API representations. None of these include a coordinate."""

from __future__ import annotations

from rest_framework import serializers

from apps.locations.models import AdministrativeArea, AdministrativeLevel, Country, Locality
from apps.locations.services.resolution import ResolvedAddress


class CountrySerializer(serializers.ModelSerializer):
    class Meta:
        model = Country
        fields = ("iso_alpha2", "iso_alpha3", "name")


class LevelSerializer(serializers.ModelSerializer):
    class Meta:
        model = AdministrativeLevel
        fields = ("depth", "code", "label")


class AdministrativeAreaSerializer(serializers.ModelSerializer):
    country = serializers.CharField(source="country_id", read_only=True)
    level_code = serializers.CharField(source="level.code", read_only=True)
    level_depth = serializers.IntegerField(source="level.depth", read_only=True)
    parent_id = serializers.UUIDField(read_only=True, allow_null=True)

    class Meta:
        model = AdministrativeArea
        fields = ("id", "name", "code", "country", "level_code", "level_depth", "parent_id")


class LocalitySerializer(serializers.ModelSerializer):
    country = serializers.CharField(source="country_id", read_only=True)
    administrative_area_id = serializers.UUIDField(read_only=True, allow_null=True)

    class Meta:
        model = Locality
        fields = ("id", "name", "kind", "country", "administrative_area_id")


class LocationSearchSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    kind = serializers.CharField(source="result_kind")
    country_code = serializers.CharField()
    level_code = serializers.CharField(allow_blank=True)
    parent_id = serializers.UUIDField(allow_null=True)


class AddressResolveSerializer(serializers.Serializer):
    country = serializers.CharField()
    administrative_area_id = serializers.UUIDField(required=False, allow_null=True)
    locality_id = serializers.UUIDField(required=False, allow_null=True)
    street_line = serializers.CharField(required=False, allow_blank=True, max_length=200)
    building_name = serializers.CharField(required=False, allow_blank=True, max_length=120)
    unit_label = serializers.CharField(required=False, allow_blank=True, max_length=40)
    postal_code = serializers.CharField(required=False, allow_blank=True, max_length=16)


class ResolvedAddressSerializer(serializers.Serializer):
    country = serializers.CharField()
    country_name = serializers.CharField()
    administrative_area_id = serializers.UUIDField(allow_null=True)
    administrative_area_name = serializers.CharField()
    administrative_path = serializers.ListField(child=serializers.CharField())
    locality_id = serializers.UUIDField(allow_null=True)
    locality_name = serializers.CharField()
    street_line = serializers.CharField()
    building_name = serializers.CharField()
    unit_label = serializers.CharField()
    postal_code = serializers.CharField()

    @staticmethod
    def from_resolved(resolved: ResolvedAddress) -> dict:
        return {
            "country": resolved.country_code,
            "country_name": resolved.country_name,
            "administrative_area_id": resolved.administrative_area_id,
            "administrative_area_name": resolved.administrative_area_name,
            "administrative_path": list(resolved.administrative_path),
            "locality_id": resolved.locality_id,
            "locality_name": resolved.locality_name,
            "street_line": resolved.street_line,
            "building_name": resolved.building_name,
            "unit_label": resolved.unit_label,
            "postal_code": resolved.postal_code,
        }
