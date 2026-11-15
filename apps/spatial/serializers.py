"""Serializers for spatial domain entities and GeoJSON representations."""

from __future__ import annotations

import json

from rest_framework import serializers

from apps.spatial.models import (
    Building,
    ChangeCandidate,
    ChangeDetectionProject,
    ChangeDetectionRun,
    ChangeEvidence,
    ChangeReview,
    DatasetQualityReport,
    DatasetRegistry,
    DatasetVersion,
    DigitalTwinAsset,
    DigitalTwinAttributeProvenance,
    DigitalTwinGeneration,
    EntityLineage,
    ImageryCollection,
    ImageryItem,
    LandParcel,
    RasterAsset,
    RasterProcessingJob,
    SpatialAnalysisEvidence,
    SpatialAnalysisProject,
    SpatialAnalysisResult,
    SpatialAnalysisRun,
    SpatialEntity,
    SpatialIdentityMapping,
    SpatialScenario,
)


class DatasetQualityReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = DatasetQualityReport
        fields = [
            "id",
            "valid_geometries",
            "repaired_geometries",
            "quarantined_geometries",
            "duplicate_records",
            "horizontal_rmse_m",
            "vertical_rmse_m",
            "audit_notes",
            "audited_at",
        ]


class DatasetVersionSerializer(serializers.ModelSerializer):
    quality_report = DatasetQualityReportSerializer(read_only=True)
    spatial_extent_geojson = serializers.SerializerMethodField()

    class Meta:
        model = DatasetVersion
        fields = [
            "id",
            "version_tag",
            "source_url",
            "source_checksum_sha256",
            "pipeline_git_commit",
            "license_type",
            "attribution_statement",
            "min_zoom",
            "max_zoom",
            "feature_count",
            "status",
            "spatial_extent_geojson",
            "quality_report",
            "published_at",
        ]

    def get_spatial_extent_geojson(self, obj):
        if obj.spatial_extent:
            return json.loads(obj.spatial_extent.geojson)
        return None


class DatasetRegistrySerializer(serializers.ModelSerializer):
    versions = DatasetVersionSerializer(many=True, read_only=True)

    class Meta:
        model = DatasetRegistry
        fields = [
            "id",
            "dataset_urn",
            "name",
            "description",
            "category",
            "update_frequency",
            "versions",
            "created_at",
        ]


class SpatialIdentityMappingSerializer(serializers.ModelSerializer):
    class Meta:
        model = SpatialIdentityMapping
        fields = [
            "id",
            "provider_name",
            "provider_external_id",
            "confidence_score",
            "created_at",
        ]


class SpatialEntitySerializer(serializers.ModelSerializer):
    external_mappings = SpatialIdentityMappingSerializer(many=True, read_only=True)

    class Meta:
        model = SpatialEntity
        fields = [
            "id",
            "canonical_urn",
            "entity_type",
            "organization_id",
            "lifecycle_status",
            "valid_from",
            "valid_to",
            "superseded_at",
            "external_mappings",
            "created_at",
        ]


class BuildingSerializer(serializers.ModelSerializer):
    external_mappings = serializers.SerializerMethodField()
    geojson = serializers.SerializerMethodField()

    class Meta:
        model = Building
        fields = [
            "id",
            "canonical_urn",
            "name",
            "classification",
            "height_m",
            "levels_above_ground",
            "levels_below_ground",
            "roof_shape",
            "facade_material",
            "footprint_area_sqm",
            "legal_authority_level",
            "horizontal_rmse_m",
            "is_human_verified",
            "valid_from",
            "valid_to",
            "superseded_at",
            "geojson",
            "digital_twin",
            "external_mappings",
            "created_at",
        ]

    def get_digital_twin(self, obj):
        try:
            twin = getattr(obj, "digital_twin", None)
            if twin:
                return {
                    "id": str(twin.id),
                    "height_m": float(twin.height_m),
                    "height_source": twin.height_source,
                    "is_placeholder": twin.is_placeholder,
                    "lod_level": twin.lod_level,
                    "triangles_count": twin.triangles_count,
                    "vertex_count": twin.vertex_count,
                    "storage_path": twin.storage_path,
                }
        except Exception:
            pass
        return None

    def get_external_mappings(self, obj):
        return SpatialIdentityMappingSerializer(
            obj.external_mappings.all(), many=True
        ).data

    def get_geojson(self, obj):
        if obj.geom:
            return json.loads(obj.geom.geojson)
        return None


class LandParcelSerializer(serializers.ModelSerializer):
    external_mappings = serializers.SerializerMethodField()
    geojson = serializers.SerializerMethodField()

    class Meta:
        model = LandParcel
        fields = [
            "id",
            "canonical_urn",
            "cadastral_identifier",
            "title_number",
            "registry_section",
            "tenure_type",
            "registered_area_sqm",
            "computed_area_sqm",
            "legal_authority_level",
            "horizontal_rmse_m",
            "is_human_verified",
            "valid_from",
            "valid_to",
            "superseded_at",
            "geojson",
            "digital_twin",
            "external_mappings",
            "created_at",
        ]

    def get_digital_twin(self, obj):
        try:
            twin = getattr(obj, "digital_twin", None)
            if twin:
                return {
                    "id": str(twin.id),
                    "height_m": float(twin.height_m),
                    "height_source": twin.height_source,
                    "is_placeholder": twin.is_placeholder,
                    "lod_level": twin.lod_level,
                    "triangles_count": twin.triangles_count,
                    "vertex_count": twin.vertex_count,
                    "storage_path": twin.storage_path,
                }
        except Exception:
            pass
        return None

    def get_external_mappings(self, obj):
        return SpatialIdentityMappingSerializer(
            obj.external_mappings.all(), many=True
        ).data

    def get_geojson(self, obj):
        if obj.geom:
            return json.loads(obj.geom.geojson)
        return None


class EntityLineageSerializer(serializers.ModelSerializer):
    class Meta:
        model = EntityLineage
        fields = [
            "id",
            "predecessor_urn",
            "successor_urn",
            "lineage_type",
            "effective_date",
            "authorization_reference",
            "created_at",
        ]


class DigitalTwinAttributeProvenanceSerializer(serializers.ModelSerializer):
    class Meta:
        model = DigitalTwinAttributeProvenance
        fields = [
            "id",
            "attribute_name",
            "value",
            "source",
            "confidence_score",
            "verification_status",
            "created_at",
        ]


class DigitalTwinAssetSerializer(serializers.ModelSerializer):
    canonical_urn = serializers.CharField(source="entity.canonical_urn", read_only=True)
    provenance_records = DigitalTwinAttributeProvenanceSerializer(many=True, read_only=True)

    class Meta:
        model = DigitalTwinAsset
        fields = [
            "id",
            "canonical_urn",
            "format",
            "storage_path",
            "height_m",
            "height_source",
            "is_placeholder",
            "lod_level",
            "triangles_count",
            "vertex_count",
            "provenance_records",
            "created_at",
        ]


class DigitalTwinGenerationSerializer(serializers.ModelSerializer):
    class Meta:
        model = DigitalTwinGeneration
        fields = [
            "id",
            "status",
            "building_count",
            "tileset_urn",
            "tileset_path",
            "generator_version",
            "error_details",
            "duration_ms",
            "created_at",
            "updated_at",
        ]


class RasterAssetSerializer(serializers.ModelSerializer):
    tile_url_template = serializers.SerializerMethodField()

    class Meta:
        model = RasterAsset
        fields = [
            "id",
            "asset_key",
            "asset_type",
            "storage_path",
            "mime_type",
            "file_size_bytes",
            "checksum_sha256",
            "crs",
            "gsd_x_m",
            "gsd_y_m",
            "width_px",
            "height_px",
            "bands",
            "nodata_value",
            "min_val",
            "max_val",
            "tile_url_template",
            "created_at",
            "updated_at",
        ]

    def get_tile_url_template(self, obj):
        return f"/api/v1/spatial/rasters/{obj.id}/tiles/{{z}}/{{x}}/{{y}}/"


class ImageryItemSerializer(serializers.ModelSerializer):
    assets = RasterAssetSerializer(many=True, read_only=True)
    footprint_geojson = serializers.SerializerMethodField()

    class Meta:
        model = ImageryItem
        fields = [
            "id",
            "item_id",
            "canonical_urn",
            "collection_id",
            "acquisition_datetime",
            "processing_datetime",
            "footprint_geojson",
            "native_crs",
            "cloud_coverage_percentage",
            "sun_azimuth_deg",
            "sun_elevation_deg",
            "source_checksum",
            "stac_properties",
            "assets",
            "created_at",
            "updated_at",
        ]

    def get_footprint_geojson(self, obj):
        if obj.footprint:
            return json.loads(obj.footprint.geojson)
        return None


class ImageryCollectionSerializer(serializers.ModelSerializer):
    items_count = serializers.IntegerField(source="items.count", read_only=True)
    spatial_extent_geojson = serializers.SerializerMethodField()

    class Meta:
        model = ImageryCollection
        fields = [
            "id",
            "collection_id",
            "title",
            "description",
            "provider",
            "dataset_type",
            "license",
            "attribution",
            "spatial_extent_geojson",
            "time_start",
            "time_end",
            "native_crs",
            "gsd_m",
            "is_active",
            "stac_metadata",
            "items_count",
            "created_at",
            "updated_at",
        ]

    def get_spatial_extent_geojson(self, obj):
        if obj.spatial_extent:
            return json.loads(obj.spatial_extent.geojson)
        return None


class RasterProcessingJobSerializer(serializers.ModelSerializer):
    class Meta:
        model = RasterProcessingJob
        fields = [
            "id",
            "input_item_id",
            "operation",
            "config_parameters",
            "software_version",
            "output_asset_id",
            "status",
            "error_details",
            "duration_ms",
            "created_at",
            "updated_at",
        ]


class ChangeEvidenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChangeEvidence
        fields = [
            "id",
            "candidate_id",
            "evidence_key",
            "baseline_raster_asset_id",
            "comparison_raster_asset_id",
            "derived_diff_asset_id",
            "pixel_resolution_m",
            "applied_displacement_m",
            "residual_registration_error_m",
            "total_positional_uncertainty_1sigma_m",
            "metrics_payload",
            "created_at",
        ]


class ChangeReviewSerializer(serializers.ModelSerializer):
    reviewer_email = serializers.CharField(source="reviewer.email", read_only=True)

    class Meta:
        model = ChangeReview
        fields = [
            "id",
            "candidate_id",
            "reviewer_id",
            "reviewer_email",
            "decision",
            "previous_status",
            "new_status",
            "comments",
            "created_at",
        ]


class ChangeCandidateSerializer(serializers.ModelSerializer):
    geometry_geojson = serializers.SerializerMethodField()
    associated_building_urn = serializers.CharField(
        source="associated_building.canonical_urn", read_only=True, default=None
    )
    associated_building_name = serializers.CharField(
        source="associated_building.name", read_only=True, default=None
    )
    evidence = ChangeEvidenceSerializer(many=True, read_only=True)
    reviews = ChangeReviewSerializer(many=True, read_only=True)

    class Meta:
        model = ChangeCandidate
        fields = [
            "id",
            "run_id",
            "candidate_id",
            "category",
            "geometry_geojson",
            "bounding_box",
            "detection_confidence",
            "detection_method",
            "area_sqm",
            "pixel_count_area_sqm",
            "change_magnitude",
            "delta_ndvi",
            "delta_ndbi",
            "associated_building_id",
            "associated_building_urn",
            "associated_building_name",
            "building_association_type",
            "building_overlap_ratio",
            "building_distance_m",
            "review_status",
            "reviewed_at",
            "evidence",
            "reviews",
            "created_at",
            "updated_at",
        ]

    def get_geometry_geojson(self, obj):
        if obj.geometry:
            return json.loads(obj.geometry.geojson)
        return None


class ChangeDetectionRunSerializer(serializers.ModelSerializer):
    baseline_acquisition_date = serializers.DateTimeField(
        source="baseline_item.acquisition_datetime", read_only=True
    )
    comparison_acquisition_date = serializers.DateTimeField(
        source="comparison_item.acquisition_datetime", read_only=True
    )
    baseline_item_id = serializers.CharField(
        source="baseline_item.item_id", read_only=True
    )
    comparison_item_id = serializers.CharField(
        source="comparison_item.item_id", read_only=True
    )
    candidates_count = serializers.IntegerField(source="candidates.count", read_only=True)

    class Meta:
        model = ChangeDetectionRun
        fields = [
            "id",
            "project_id",
            "baseline_item_id",
            "comparison_item_id",
            "baseline_acquisition_date",
            "comparison_acquisition_date",
            "algorithm_identifier",
            "algorithm_version",
            "parameters",
            "status",
            "started_at",
            "completed_at",
            "processing_duration_sec",
            "total_candidates_detected",
            "noise_regions_suppressed",
            "valid_pixels_evaluated",
            "masked_pixels_excluded",
            "error_details",
            "candidates_count",
            "created_at",
            "updated_at",
        ]


class ChangeDetectionProjectSerializer(serializers.ModelSerializer):
    aoi_geojson = serializers.SerializerMethodField()
    runs_count = serializers.IntegerField(source="runs.count", read_only=True)
    organization_slug = serializers.CharField(source="organization.slug", read_only=True)

    class Meta:
        model = ChangeDetectionProject
        fields = [
            "id",
            "organization_id",
            "organization_slug",
            "name",
            "description",
            "aoi_geojson",
            "monitoring_purpose",
            "status",
            "runs_count",
            "created_at",
            "updated_at",
        ]

    def get_aoi_geojson(self, obj):
        if obj.aoi_geometry:
            return json.loads(obj.aoi_geometry.geojson)
        return None


class SpatialAnalysisEvidenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = SpatialAnalysisEvidence
        fields = [
            "id",
            "result_id",
            "evidence_type",
            "source_datasets",
            "confidence_score",
            "limitations",
            "explanation",
            "payload",
            "created_at",
        ]


class SpatialAnalysisResultSerializer(serializers.ModelSerializer):
    geometry_geojson = serializers.SerializerMethodField()
    run_id = serializers.UUIDField(source="run.id", read_only=True)
    associated_building_id = serializers.UUIDField(source="associated_building.id", read_only=True)
    associated_building_urn = serializers.CharField(source="associated_building.canonical_urn", read_only=True)
    associated_building_name = serializers.CharField(source="associated_building.name", read_only=True)
    raster_asset_id = serializers.UUIDField(source="raster_asset.id", read_only=True)
    evidence = SpatialAnalysisEvidenceSerializer(many=True, read_only=True)

    class Meta:
        model = SpatialAnalysisResult
        fields = [
            "id",
            "run_id",
            "result_id",
            "category",
            "evidence_label",
            "geometry_geojson",
            "bounding_box",
            "score",
            "confidence",
            "associated_building_id",
            "associated_building_urn",
            "associated_building_name",
            "raster_asset_id",
            "metrics_payload",
            "evidence",
            "created_at",
        ]

    def get_geometry_geojson(self, obj):
        if obj.geometry:
            return json.loads(obj.geometry.geojson)
        return None


class SpatialAnalysisRunSerializer(serializers.ModelSerializer):
    project_id = serializers.UUIDField(source="project.id", read_only=True)
    project_name = serializers.CharField(source="project.name", read_only=True)
    model_version_tag = serializers.CharField(source="model_version.version_tag", read_only=True)
    results_count = serializers.IntegerField(source="results.count", read_only=True)

    class Meta:
        model = SpatialAnalysisRun
        fields = [
            "id",
            "project_id",
            "project_name",
            "analysis_type",
            "model_version",
            "model_version_tag",
            "input_datasets",
            "parameters",
            "status",
            "started_at",
            "completed_at",
            "processing_duration_sec",
            "total_results_count",
            "results_count",
            "summary_metrics",
            "error_details",
            "created_at",
            "updated_at",
        ]


class SpatialAnalysisProjectSerializer(serializers.ModelSerializer):
    aoi_geojson = serializers.SerializerMethodField()
    runs_count = serializers.IntegerField(source="runs.count", read_only=True)
    organization_slug = serializers.CharField(source="organization.slug", read_only=True)

    class Meta:
        model = SpatialAnalysisProject
        fields = [
            "id",
            "organization_id",
            "organization_slug",
            "name",
            "description",
            "aoi_geojson",
            "status",
            "runs_count",
            "created_at",
            "updated_at",
        ]

    def get_aoi_geojson(self, obj):
        if obj.aoi_geometry:
            return json.loads(obj.aoi_geometry.geojson)
        return None


class SpatialScenarioSerializer(serializers.ModelSerializer):
    project_id = serializers.UUIDField(source="project.id", read_only=True)
    project_name = serializers.CharField(source="project.name", read_only=True)
    baseline_run_id = serializers.UUIDField(source="baseline_run.id", read_only=True)
    comparison_run_id = serializers.UUIDField(source="comparison_run.id", read_only=True)

    class Meta:
        model = SpatialScenario
        fields = [
            "id",
            "project_id",
            "project_name",
            "name",
            "description",
            "baseline_run_id",
            "comparison_run_id",
            "scenario_parameters",
            "delta_summary",
            "created_at",
        ]
