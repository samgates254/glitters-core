"""Serializers for God's Eye scenes, land profiles, and the provider registry."""

from __future__ import annotations

import json

from rest_framework import serializers

from apps.spatial.domain import SURVEY_DISCLAIMER
from apps.spatial.models import (
    GeospatialDataSource,
    GeospatialScene,
    LandIntelligenceProfile,
    ProviderQueryRecord,
)
from apps.spatial.services.land_profile import SpatialInputError, measure_polygon, measurement_error
from apps.spatial.services.scenes import clean_scene_camera


class GeospatialSceneSerializer(serializers.ModelSerializer):
    organization_id = serializers.UUIDField(required=False, write_only=True)
    elevation_disclaimer = serializers.SerializerMethodField()

    class Meta:
        model = GeospatialScene
        fields = [
            "id",
            "organization_id",
            "name",
            "place_key",
            "scene_mode",
            "camera_longitude",
            "camera_latitude",
            "camera_height_m",
            "heading_deg",
            "pitch_deg",
            "roll_deg",
            "terrain_exaggeration",
            "layer_state",
            "bookmarks",
            "elevation_disclaimer",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at", "elevation_disclaimer"]

    def get_elevation_disclaimer(self, obj) -> str:
        return "This scene stores a camera. It does not certify terrain elevation."

    def validate(self, attrs):
        if self.instance is not None:
            merged = {
                "place_key": attrs.get("place_key", self.instance.place_key),
                "scene_mode": attrs.get("scene_mode", self.instance.scene_mode),
                "camera_longitude": attrs.get("camera_longitude", self.instance.camera_longitude),
                "camera_latitude": attrs.get("camera_latitude", self.instance.camera_latitude),
                "camera_height_m": attrs.get("camera_height_m", self.instance.camera_height_m),
                "heading_deg": attrs.get("heading_deg", self.instance.heading_deg),
                "pitch_deg": attrs.get("pitch_deg", self.instance.pitch_deg),
                "roll_deg": attrs.get("roll_deg", self.instance.roll_deg),
                "terrain_exaggeration": attrs.get(
                    "terrain_exaggeration", self.instance.terrain_exaggeration
                ),
                "bookmarks": attrs.get("bookmarks", self.instance.bookmarks),
                "layer_state": attrs.get("layer_state", self.instance.layer_state),
            }
            cleaned = clean_scene_camera(merged)
            attrs.update(cleaned)
            return attrs
        return clean_scene_camera(attrs)


class LandIntelligenceProfileSerializer(serializers.ModelSerializer):
    organization_id = serializers.UUIDField(required=False, write_only=True)
    geometry = serializers.JSONField(write_only=True)
    geojson = serializers.SerializerMethodField()
    legal_status = serializers.ReadOnlyField()
    planning_status = serializers.ReadOnlyField()
    survey_disclaimer = serializers.SerializerMethodField()

    class Meta:
        model = LandIntelligenceProfile
        fields = [
            "id",
            "organization_id",
            "name",
            "boundary_class",
            "geometry",
            "geojson",
            "vertex_count",
            "area_sqm",
            "perimeter_m",
            "centroid_longitude",
            "centroid_latitude",
            "bbox_min_longitude",
            "bbox_min_latitude",
            "bbox_max_longitude",
            "bbox_max_latitude",
            "metric_epsg",
            "utm_zone",
            "geometry_evidence",
            "measurement_evidence",
            "measurement_note",
            "survey_disclaimer",
            "legal_status",
            "planning_status",
            "source_note",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "vertex_count",
            "area_sqm",
            "perimeter_m",
            "centroid_longitude",
            "centroid_latitude",
            "bbox_min_longitude",
            "bbox_min_latitude",
            "bbox_max_longitude",
            "bbox_max_latitude",
            "metric_epsg",
            "utm_zone",
            "geometry_evidence",
            "measurement_evidence",
            "measurement_note",
            "legal_status",
            "planning_status",
            "created_at",
        ]

    def get_geojson(self, obj):
        if obj.geometry is None:
            return None
        return json.loads(obj.geometry.geojson)

    def get_survey_disclaimer(self, obj) -> str:
        return SURVEY_DISCLAIMER

    def validate(self, attrs):
        try:
            attrs["_measured"] = measure_polygon(attrs.get("geometry"))
        except SpatialInputError as exc:
            raise measurement_error(exc) from None
        return attrs

    def create(self, validated_data):
        measured = validated_data.pop("_measured")
        validated_data.pop("geometry", None)
        validated_data.pop("geometry_evidence", None)
        validated_data.pop("measurement_evidence", None)
        validated_data.update(measured)
        return super().create(validated_data)


class GeospatialDataSourceSerializer(serializers.ModelSerializer):
    class Meta:
        model = GeospatialDataSource
        fields = [
            "provider_id",
            "official_name",
            "dataset_name",
            "license_identifier",
            "commercial_use",
            "attribution",
            "authentication_type",
            "cost_classification",
            "rate_limit_note",
            "monthly_allowance_note",
            "geographic_coverage",
            "temporal_coverage",
            "resolution_note",
            "freshness_note",
            "service_status",
            "processing_note",
            "caching_policy",
            "catalog_url",
            "check_mode",
            "stac_search",
            "credential_required",
            "operational_state",
            "last_checked_at",
            "last_success_at",
            "last_http_status",
            "last_error",
            "discovered_summary",
            "updated_at",
        ]
        read_only_fields = fields


class ProviderQueryRecordSerializer(serializers.ModelSerializer):
    provider_id = serializers.CharField(source="provider.provider_id", read_only=True)

    class Meta:
        model = ProviderQueryRecord
        fields = [
            "id",
            "provider_id",
            "query_kind",
            "http_status",
            "operational_state",
            "item_count",
            "collection_id",
            "bbox",
            "error",
            "created_at",
        ]
        read_only_fields = fields
