"""Spatial models implementing canonical spatial entities, dataset versioning, and lineage."""

from __future__ import annotations

import uuid
from decimal import Decimal

from django.conf import settings
from django.contrib.gis.db import models
from django.core.exceptions import ValidationError

from apps.organizations.models import Organization
from apps.spatial.domain import (
    SRID_WGS84,
    BeaconType,
    BoundaryClass,
    BuildingAssociationType,
    BuildingClassification,
    ChangeCandidateStatus,
    ChangeCategory,
    ChangeProjectStatus,
    ChangeRunStatus,
    CommercialUse,
    ConstructionPhase,
    DatasetCategory,
    DatasetFormat,
    DatasetVersionStatus,
    DigitalTwinFormat,
    DigitalTwinStatus,
    EntityType,
    EvidenceLabel,
    ForecastingStatus,
    HeightSource,
    ImageryDatasetType,
    InfrastructureType,
    LandUseClass,
    LegalAuthorityLevel,
    LicenseType,
    LifecycleStatus,
    LineageType,
    PropertyBuildingAssocType,
    PropertyParcelAssocType,
    ProvenanceClass,
    ProviderOperationalState,
    ProviderServiceStatus,
    RasterAssetType,
    RasterJobStatus,
    RasterOperation,
    ReconciliationDisposition,
    ReviewDecision,
    RoadClass,
    RoadSurface,
    SceneMode,
    ScenePlace,
    SpatialAnalysisRunStatus,
    SpatialAnalysisType,
    UpdateFrequency,
    VerticalDatum,
    build_canonical_urn,
    validate_canonical_urn,
)
from apps.spatial.services.compute.contracts import PROVIDER_CHOICES, STATE_CHOICES
from apps.spatial.services.crs import calculate_planar_area_utm_sqm
from apps.spatial.services.validation import (
    check_acyclic_lineage,
    validate_spatial_geometry,
)


class SpatialQuerySet(models.QuerySet):
    def for_organization(self, organization: Organization | None, user=None):
        """Scopes query to public data plus the specified organization's assets."""
        public_filter = models.Q(organization__isnull=True)
        if organization is None:
            return self.filter(public_filter)
        return self.filter(public_filter | models.Q(organization=organization))

    def active(self):
        """Filters active entities that are not superseded or demolished."""
        return self.filter(lifecycle_status=LifecycleStatus.ACTIVE)


class DatasetRegistry(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset_urn = models.CharField(max_length=255, unique=True)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    category = models.CharField(max_length=50, choices=DatasetCategory.CHOICES)
    update_frequency = models.CharField(
        max_length=50, choices=UpdateFrequency.CHOICES, default=UpdateFrequency.STATIC
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "spatial_dataset_registry"
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.dataset_urn})"


class DatasetVersion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset = models.ForeignKey(
        DatasetRegistry, on_delete=models.PROTECT, related_name="versions"
    )
    version_tag = models.CharField(max_length=50)
    source_url = models.TextField(blank=True, default="")
    source_checksum_sha256 = models.CharField(max_length=64)
    pipeline_git_commit = models.CharField(max_length=40)
    license_type = models.CharField(max_length=50, choices=LicenseType.CHOICES)
    attribution_statement = models.TextField()
    spatial_extent = models.PolygonField(srid=SRID_WGS84, spatial_index=True)
    min_zoom = models.PositiveSmallIntegerField(default=0)
    max_zoom = models.PositiveSmallIntegerField(default=22)
    feature_count = models.PositiveIntegerField(default=0)
    status = models.CharField(
        max_length=20,
        choices=DatasetVersionStatus.CHOICES,
        default=DatasetVersionStatus.REGISTERED,
        db_index=True,
    )
    published_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "spatial_dataset_version"
        ordering = ["-published_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["dataset", "version_tag"], name="uq_dataset_version"
            )
        ]

    def __str__(self) -> str:
        return f"{self.dataset.name} @ {self.version_tag}"


class DatasetQualityReport(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    version = models.OneToOneField(
        DatasetVersion, on_delete=models.CASCADE, related_name="quality_report"
    )
    valid_geometries = models.PositiveIntegerField()
    repaired_geometries = models.PositiveIntegerField(default=0)
    quarantined_geometries = models.PositiveIntegerField(default=0)
    duplicate_records = models.PositiveIntegerField(default=0)
    horizontal_rmse_m = models.DecimalField(
        max_digits=6, decimal_places=3, null=True, blank=True
    )
    vertical_rmse_m = models.DecimalField(
        max_digits=6, decimal_places=3, null=True, blank=True
    )
    audit_notes = models.TextField(blank=True, default="")
    audited_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "spatial_dataset_quality_report"

    def __str__(self) -> str:
        return f"Quality Report for {self.version}"


class SpatialEntity(models.Model):
    """Concrete base entity establishing canonical identity and tenancy anchor."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    canonical_urn = models.CharField(max_length=255, unique=True, editable=False)
    entity_type = models.CharField(max_length=50, choices=EntityType.CHOICES)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="spatial_entities",
    )
    dataset_version = models.ForeignKey(
        DatasetVersion, on_delete=models.PROTECT, related_name="entities"
    )
    lifecycle_status = models.CharField(
        max_length=32,
        choices=LifecycleStatus.CHOICES,
        default=LifecycleStatus.ACTIVE,
    )
    valid_from = models.DateTimeField(auto_now_add=True)
    valid_to = models.DateTimeField(null=True, blank=True)
    superseded_by = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="superseded_entities",
    )
    superseded_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = SpatialQuerySet.as_manager()

    class Meta:
        db_table = "spatial_entity"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["canonical_urn"], name="idx_spatial_entity_urn"),
            models.Index(fields=["organization"], name="idx_spatial_entity_org"),
            models.Index(fields=["entity_type"], name="idx_spatial_entity_type"),
        ]

    def save(self, *args, **kwargs):
        if not self.canonical_urn:
            self.canonical_urn = build_canonical_urn(self.entity_type, self.id)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.canonical_urn


class SpatialIdentityMapping(models.Model):
    """Maps external provider IDs (OSM, GERS, Cadastre) to the canonical base entity."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entity = models.ForeignKey(
        SpatialEntity, on_delete=models.CASCADE, related_name="external_mappings"
    )
    provider_name = models.CharField(max_length=50)
    provider_external_id = models.CharField(max_length=255)
    confidence_score = models.DecimalField(max_digits=3, decimal_places=2, default=1.00)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "spatial_identity_mapping"
        constraints = [
            models.UniqueConstraint(
                fields=["provider_name", "provider_external_id"],
                name="uq_spatial_provider_id",
            )
        ]
        indexes = [
            models.Index(fields=["entity"], name="idx_id_map_entity"),
            models.Index(
                fields=["provider_name", "provider_external_id"],
                name="idx_id_map_lookup",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.provider_name}:{self.provider_external_id} -> {self.entity.canonical_urn}"


class SpatialSourceFeature(models.Model):
    """Preserves raw external source feature properties, geometry, and reconciliation disposition."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset_version = models.ForeignKey(
        DatasetVersion, on_delete=models.CASCADE, related_name="source_features"
    )
    provider_name = models.CharField(max_length=50)
    source_record_id = models.CharField(max_length=255)
    source_version = models.CharField(max_length=50, blank=True, default="")
    raw_properties = models.JSONField(default=dict)
    raw_geometry = models.JSONField(default=dict)
    repaired_geometry = models.JSONField(null=True, blank=True, default=None)
    spatial_entity = models.ForeignKey(
        SpatialEntity,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="source_features",
    )
    disposition = models.CharField(
        max_length=32,
        choices=ReconciliationDisposition.CHOICES,
        default=ReconciliationDisposition.INSERTED,
    )
    was_repaired = models.BooleanField(default=False)
    repair_details = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "spatial_source_feature"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["dataset_version", "provider_name", "source_record_id"],
                name="uq_dataset_source_feature",
            )
        ]
        indexes = [
            models.Index(
                fields=["provider_name", "source_record_id"],
                name="idx_src_feat_lookup",
            ),
            models.Index(
                fields=["dataset_version", "disposition"],
                name="idx_src_feat_disposition",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.provider_name}:{self.source_record_id} ({self.disposition})"



class EntityLineage(models.Model):
    """Directed acyclic graph recording historical transformations."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    predecessor_urn = models.CharField(max_length=255)
    successor_urn = models.CharField(max_length=255)
    lineage_type = models.CharField(max_length=50, choices=LineageType.CHOICES)
    effective_date = models.DateField()
    authorization_reference = models.CharField(max_length=100, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "spatial_entity_lineage"
        indexes = [
            models.Index(fields=["predecessor_urn"], name="idx_lineage_pred"),
            models.Index(fields=["successor_urn"], name="idx_lineage_succ"),
        ]

    def clean(self):
        super().clean()
        if not validate_canonical_urn(self.predecessor_urn):
            raise ValidationError(f"Invalid predecessor URN: {self.predecessor_urn}")
        if not validate_canonical_urn(self.successor_urn):
            raise ValidationError(f"Invalid successor URN: {self.successor_urn}")
        check_acyclic_lineage(self.predecessor_urn, self.successor_urn)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.predecessor_urn} -> {self.successor_urn} ({self.lineage_type})"


# =============================================================================
# Specialized Spatial Entity Models
# =============================================================================


class LandParcel(SpatialEntity):
    cadastral_identifier = models.CharField(max_length=100, unique=True)
    title_number = models.CharField(max_length=100, blank=True, default="")
    registry_section = models.CharField(max_length=100, blank=True, default="")
    tenure_type = models.CharField(max_length=50)
    registered_area_sqm = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    computed_area_sqm = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    legal_authority_level = models.CharField(
        max_length=50,
        choices=LegalAuthorityLevel.CHOICES,
        default=LegalAuthorityLevel.STATUTORY_AUTHORITATIVE,
    )
    horizontal_rmse_m = models.DecimalField(
        max_digits=6, decimal_places=3, default=0.050
    )
    is_human_verified = models.BooleanField(default=False)
    geom = models.MultiPolygonField(srid=SRID_WGS84, spatial_index=True)

    class Meta:
        db_table = "spatial_land_parcel"
        ordering = ["cadastral_identifier"]

    def clean(self):
        super().clean()
        validate_spatial_geometry(self.geom)

    def save(self, *args, **kwargs):
        self.entity_type = EntityType.LAND_PARCEL
        if self.geom and not self.computed_area_sqm:
            self.computed_area_sqm = round(calculate_planar_area_utm_sqm(self.geom), 2)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"Parcel {self.cadastral_identifier}"


class Building(SpatialEntity):
    name = models.CharField(max_length=255, blank=True, default="")
    classification = models.CharField(
        max_length=50,
        choices=BuildingClassification.CHOICES,
        default=BuildingClassification.OTHER,
    )
    height_m = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    levels_above_ground = models.PositiveSmallIntegerField(null=True, blank=True)
    levels_below_ground = models.PositiveSmallIntegerField(default=0)
    roof_shape = models.CharField(max_length=50, blank=True, default="")
    facade_material = models.CharField(max_length=100, blank=True, default="")
    footprint_area_sqm = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )
    legal_authority_level = models.CharField(
        max_length=50,
        choices=LegalAuthorityLevel.CHOICES,
        default=LegalAuthorityLevel.OPEN_DATA,
    )
    horizontal_rmse_m = models.DecimalField(
        max_digits=6, decimal_places=3, default=1.000
    )
    is_human_verified = models.BooleanField(default=False)
    geom = models.MultiPolygonField(srid=SRID_WGS84, spatial_index=True)

    class Meta:
        db_table = "spatial_building"
        ordering = ["name"]

    def clean(self):
        super().clean()
        validate_spatial_geometry(self.geom)

    def save(self, *args, **kwargs):
        self.entity_type = EntityType.BUILDING
        if self.geom and not self.footprint_area_sqm:
            self.footprint_area_sqm = round(calculate_planar_area_utm_sqm(self.geom), 2)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name or f"Building {self.id}"


class Road(SpatialEntity):
    road_name = models.CharField(max_length=255, blank=True, default="")
    road_class = models.CharField(
        max_length=50, choices=RoadClass.CHOICES, default=RoadClass.LOCAL
    )
    surface_type = models.CharField(
        max_length=50, choices=RoadSurface.CHOICES, default=RoadSurface.ASPHALT
    )
    carriageway_width_m = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    right_of_way_width_m = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    governing_authority = models.CharField(max_length=50, blank=True, default="")
    geom = models.MultiLineStringField(srid=SRID_WGS84, spatial_index=True)

    class Meta:
        db_table = "spatial_road"

    def clean(self):
        super().clean()
        validate_spatial_geometry(self.geom)

    def save(self, *args, **kwargs):
        self.entity_type = EntityType.ROAD
        super().save(*args, **kwargs)


class ConstructionSite(SpatialEntity):
    project_name = models.CharField(max_length=255)
    permit_number = models.CharField(max_length=100, blank=True, default="")
    contractor_name = models.CharField(max_length=255, blank=True, default="")
    development_phase = models.CharField(
        max_length=50,
        choices=ConstructionPhase.CHOICES,
        default=ConstructionPhase.FOUNDATION,
    )
    hazard_buffer_m = models.DecimalField(max_digits=6, decimal_places=2, default=10.00)
    geom = models.PolygonField(srid=SRID_WGS84, spatial_index=True)

    class Meta:
        db_table = "spatial_construction_site"

    def clean(self):
        super().clean()
        validate_spatial_geometry(self.geom)

    def save(self, *args, **kwargs):
        self.entity_type = EntityType.CONSTRUCTION_SITE
        super().save(*args, **kwargs)


class SurveyObservation(SpatialEntity):
    beacon_code = models.CharField(max_length=50)
    beacon_type = models.CharField(
        max_length=50, choices=BeaconType.CHOICES, default=BeaconType.IRON_PIN_CONCRETE
    )
    vertical_datum = models.CharField(
        max_length=50,
        choices=VerticalDatum.CHOICES,
        default=VerticalDatum.EGM2008_ORTHOMETRIC,
    )
    elevation_orthometric_m = models.DecimalField(max_digits=8, decimal_places=3)
    elevation_ellipsoidal_m = models.DecimalField(
        max_digits=8, decimal_places=3, null=True, blank=True
    )
    horizontal_rmse_m = models.DecimalField(
        max_digits=6, decimal_places=3, default=0.010
    )
    vertical_rmse_m = models.DecimalField(
        max_digits=6, decimal_places=3, default=0.020
    )
    surveyor_license = models.CharField(max_length=100)
    observation_timestamp = models.DateTimeField()
    geom_point_z = models.PointField(dim=3, srid=SRID_WGS84, spatial_index=True)

    class Meta:
        db_table = "spatial_survey_observation"

    def clean(self):
        super().clean()
        validate_spatial_geometry(self.geom_point_z, expected_dim=3)

    def save(self, *args, **kwargs):
        self.entity_type = EntityType.SURVEY_OBSERVATION
        super().save(*args, **kwargs)


class InfrastructureAsset(SpatialEntity):
    asset_type = models.CharField(max_length=50, choices=InfrastructureType.CHOICES)
    network_name = models.CharField(max_length=255)
    operator = models.CharField(max_length=100)
    depth_m = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    capacity_rating = models.CharField(max_length=100, blank=True, default="")
    geom = models.GeometryField(srid=SRID_WGS84, spatial_index=True)

    class Meta:
        db_table = "spatial_infrastructure_asset"

    def clean(self):
        super().clean()
        validate_spatial_geometry(self.geom)

    def save(self, *args, **kwargs):
        self.entity_type = EntityType.INFRASTRUCTURE_ASSET
        super().save(*args, **kwargs)


class AdministrativeBoundary(SpatialEntity):
    administrative_area = models.ForeignKey(
        "locations.AdministrativeArea",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="spatial_boundaries",
    )
    boundary_name = models.CharField(max_length=255)
    admin_level = models.CharField(max_length=50)
    geom = models.MultiPolygonField(srid=SRID_WGS84, spatial_index=True)

    class Meta:
        db_table = "spatial_administrative_boundary"

    def clean(self):
        super().clean()
        validate_spatial_geometry(self.geom)

    def save(self, *args, **kwargs):
        self.entity_type = EntityType.ADMINISTRATIVE_BOUNDARY
        super().save(*args, **kwargs)


class TerrainFeature(SpatialEntity):
    feature_type = models.CharField(max_length=50)
    elevation_m = models.DecimalField(
        max_digits=7, decimal_places=2, null=True, blank=True
    )
    flood_risk_category = models.CharField(max_length=50, blank=True, default="")
    slope_gradient_pct = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    geom = models.GeometryField(srid=SRID_WGS84, spatial_index=True)

    class Meta:
        db_table = "spatial_terrain_feature"

    def clean(self):
        super().clean()
        validate_spatial_geometry(self.geom)

    def save(self, *args, **kwargs):
        self.entity_type = EntityType.TERRAIN_FEATURE
        super().save(*args, **kwargs)


class ImageryObservation(SpatialEntity):
    sensor_type = models.CharField(max_length=50)
    capture_timestamp = models.DateTimeField()
    ground_sample_distance_cm = models.DecimalField(max_digits=6, decimal_places=2)
    flight_altitude_m = models.DecimalField(
        max_digits=7, decimal_places=2, null=True, blank=True
    )
    stac_item_id = models.CharField(max_length=255, blank=True, default="")
    cog_storage_uri = models.TextField(blank=True, default="")
    geom_footprint = models.PolygonField(srid=SRID_WGS84, spatial_index=True)

    class Meta:
        db_table = "spatial_imagery_observation"

    def clean(self):
        super().clean()
        validate_spatial_geometry(self.geom_footprint)

    def save(self, *args, **kwargs):
        self.entity_type = EntityType.IMAGERY_OBSERVATION
        super().save(*args, **kwargs)


# =============================================================================
# Decoupled Many-to-Many Relationships
# =============================================================================


class PropertyParcelAssociation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    property = models.ForeignKey(
        "properties.Property",
        on_delete=models.CASCADE,
        related_name="parcel_associations",
    )
    parcel = models.ForeignKey(
        LandParcel, on_delete=models.CASCADE, related_name="property_associations"
    )
    association_type = models.CharField(
        max_length=50,
        choices=PropertyParcelAssocType.CHOICES,
        default=PropertyParcelAssocType.FULL_OWNERSHIP,
    )
    undivided_share_ratio = models.DecimalField(
        max_digits=5, decimal_places=4, default=1.0000
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "spatial_property_parcel_association"
        constraints = [
            models.UniqueConstraint(
                fields=["property", "parcel"], name="uq_property_parcel"
            )
        ]

    def __str__(self) -> str:
        return f"{self.property.reference} <-> {self.parcel.cadastral_identifier}"


class BuildingParcelIntersection(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    building = models.ForeignKey(
        Building, on_delete=models.CASCADE, related_name="parcel_intersections"
    )
    parcel = models.ForeignKey(
        LandParcel, on_delete=models.CASCADE, related_name="building_intersections"
    )
    intersection_area_sqm = models.DecimalField(max_digits=10, decimal_places=2)
    percentage_of_building = models.DecimalField(max_digits=5, decimal_places=2)
    is_primary_parcel = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "spatial_building_parcel_intersection"
        constraints = [
            models.UniqueConstraint(
                fields=["building", "parcel"], name="uq_building_parcel"
            )
        ]

    def __str__(self) -> str:
        return f"Building {self.building_id} on Parcel {self.parcel.cadastral_identifier} ({self.percentage_of_building}%)"


class PropertyBuildingAssociation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    property = models.ForeignKey(
        "properties.Property",
        on_delete=models.CASCADE,
        related_name="building_associations",
    )
    building = models.ForeignKey(
        Building, on_delete=models.CASCADE, related_name="property_associations"
    )
    association_type = models.CharField(
        max_length=50,
        choices=PropertyBuildingAssocType.CHOICES,
        default=PropertyBuildingAssocType.ENTIRE_BUILDING,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "spatial_property_building_association"
        constraints = [
            models.UniqueConstraint(
                fields=["property", "building"], name="uq_property_building"
            )
        ]

    def __str__(self) -> str:
        return f"{self.property.reference} <-> Building {self.building_id}"


# =============================================================================
# Quarantine Ledger
# =============================================================================


class SpatialQuarantine(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_dataset_version = models.ForeignKey(
        DatasetVersion,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="quarantined_records",
    )
    source_record_id = models.CharField(max_length=255, blank=True, default="")
    raw_payload = models.JSONField()
    failure_stage = models.CharField(max_length=50)
    error_message = models.TextField()
    quarantined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "spatial_quarantine"
        ordering = ["-quarantined_at"]

    def __str__(self) -> str:
        return f"Quarantined {self.source_record_id}: {self.failure_stage}"


# =============================================================================
# Digital Twin Models (M4.2)
# =============================================================================


class DigitalTwinGeneration(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    status = models.CharField(
        max_length=50,
        choices=DigitalTwinStatus.CHOICES,
        default=DigitalTwinStatus.PENDING,
        db_index=True,
    )
    area_of_interest = models.PolygonField(
        srid=SRID_WGS84, null=True, blank=True, spatial_index=True
    )
    building_count = models.PositiveIntegerField(default=0)
    tileset_urn = models.CharField(max_length=255, blank=True, default="")
    tileset_path = models.TextField(blank=True, default="")
    generator_version = models.CharField(max_length=50, default="1.0.0")
    error_details = models.TextField(blank=True, default="")
    duration_ms = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "spatial_digital_twin_generation"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"DigitalTwinGeneration {self.id} ({self.status}) - {self.building_count} buildings"


class DigitalTwinAsset(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entity = models.OneToOneField(
        SpatialEntity,
        on_delete=models.CASCADE,
        related_name="digital_twin",
        null=True,
        blank=True,
    )
    generation = models.ForeignKey(
        DigitalTwinGeneration,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assets",
    )
    format = models.CharField(
        max_length=50,
        choices=DigitalTwinFormat.CHOICES,
        default=DigitalTwinFormat.GLTF_BINARY,
    )
    storage_path = models.TextField(blank=True, default="")
    bounding_box = models.PolygonField(
        srid=SRID_WGS84, null=True, blank=True, spatial_index=True
    )
    height_m = models.DecimalField(max_digits=6, decimal_places=2)
    height_source = models.CharField(
        max_length=50,
        choices=HeightSource.CHOICES,
        default=HeightSource.UNKNOWN_HEIGHT,
    )
    is_placeholder = models.BooleanField(default=False)
    lod_level = models.PositiveSmallIntegerField(default=1)
    triangles_count = models.PositiveIntegerField(default=0)
    vertex_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "spatial_digital_twin_asset"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        urn = self.entity.canonical_urn if self.entity else "unattached"
        return f"Twin {urn} ({self.height_m}m, {self.height_source})"


class DigitalTwinAttributeProvenance(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    asset = models.ForeignKey(
        DigitalTwinAsset,
        on_delete=models.CASCADE,
        related_name="provenance_records",
    )
    attribute_name = models.CharField(max_length=100)
    value = models.TextField()
    source = models.CharField(max_length=100)
    confidence_score = models.DecimalField(max_digits=4, decimal_places=3, default=1.000)
    verification_status = models.CharField(max_length=50, default="UNVERIFIED")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "spatial_digital_twin_provenance"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.attribute_name}={self.value} ({self.source}, conf={self.confidence_score})"


# =============================================================================
# Earth Observation & Raster Intelligence Models (M4.3)
# =============================================================================


class ImageryCollection(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    collection_id = models.CharField(max_length=100, unique=True, db_index=True)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    provider = models.CharField(max_length=100, db_index=True)
    dataset_type = models.CharField(
        max_length=50,
        choices=ImageryDatasetType.CHOICES,
        default=ImageryDatasetType.OPTICAL_MULTISPECTRAL,
    )
    license = models.CharField(max_length=50, default="ODbL")
    attribution = models.TextField(blank=True, default="")
    spatial_extent = models.PolygonField(
        srid=SRID_WGS84, null=True, blank=True, spatial_index=True
    )
    time_start = models.DateTimeField(null=True, blank=True)
    time_end = models.DateTimeField(null=True, blank=True)
    native_crs = models.CharField(max_length=50, default="EPSG:4326")
    gsd_m = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    stac_metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "spatial_imagery_collection"
        ordering = ["collection_id"]

    def __str__(self) -> str:
        return f"{self.title} ({self.collection_id})"


class ImageryItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    collection = models.ForeignKey(
        ImageryCollection, on_delete=models.CASCADE, related_name="items"
    )
    item_id = models.CharField(max_length=255, db_index=True)
    canonical_urn = models.CharField(max_length=255, unique=True, db_index=True)
    acquisition_datetime = models.DateTimeField(db_index=True)
    processing_datetime = models.DateTimeField(null=True, blank=True)
    footprint = models.PolygonField(srid=SRID_WGS84, spatial_index=True)
    native_crs = models.CharField(max_length=50, default="EPSG:32737")
    cloud_coverage_percentage = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    sun_azimuth_deg = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    sun_elevation_deg = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    source_checksum = models.CharField(max_length=64, blank=True, default="")
    stac_properties = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "spatial_imagery_item"
        ordering = ["-acquisition_datetime"]
        constraints = [
            models.UniqueConstraint(
                fields=["collection", "item_id"], name="uq_imagery_collection_item"
            )
        ]

    def __str__(self) -> str:
        return f"{self.item_id} ({self.acquisition_datetime.date()})"


class RasterAsset(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    item = models.ForeignKey(
        ImageryItem, on_delete=models.CASCADE, related_name="assets", null=True, blank=True
    )
    asset_key = models.CharField(max_length=50, db_index=True)
    asset_type = models.CharField(
        max_length=50, choices=RasterAssetType.CHOICES, default=RasterAssetType.COG
    )
    storage_path = models.TextField()
    mime_type = models.CharField(
        max_length=100, default="image/tiff; application=geotiff; profile=cloud-optimized"
    )
    file_size_bytes = models.BigIntegerField(default=0)
    checksum_sha256 = models.CharField(max_length=64, blank=True, default="")
    crs = models.CharField(max_length=50, default="EPSG:32737")
    gsd_x_m = models.DecimalField(max_digits=8, decimal_places=2, default=10.00)
    gsd_y_m = models.DecimalField(max_digits=8, decimal_places=2, default=10.00)
    width_px = models.PositiveIntegerField(default=0)
    height_px = models.PositiveIntegerField(default=0)
    bands = models.JSONField(default=list, blank=True)
    nodata_value = models.FloatField(null=True, blank=True)
    min_val = models.FloatField(null=True, blank=True)
    max_val = models.FloatField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "spatial_raster_asset"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"RasterAsset {self.asset_key} ({self.crs}, {self.width_px}x{self.height_px})"


class RasterProcessingJob(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="raster_processing_jobs",
    )
    input_item = models.ForeignKey(
        ImageryItem,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="processing_jobs",
    )
    operation = models.CharField(
        max_length=50, choices=RasterOperation.CHOICES, default=RasterOperation.INGESTION
    )
    config_parameters = models.JSONField(default=dict, blank=True)
    software_version = models.CharField(max_length=50, default="1.1.0")
    output_asset = models.ForeignKey(
        RasterAsset,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="derived_from_job",
    )
    status = models.CharField(
        max_length=50,
        choices=RasterJobStatus.CHOICES,
        default=RasterJobStatus.PENDING,
        db_index=True,
    )
    error_details = models.TextField(blank=True, default="")
    duration_ms = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "spatial_raster_processing_job"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Job {self.operation} ({self.status}) - {self.id}"


class ChangeDetectionProject(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.PROTECT, related_name="change_projects"
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    aoi_geometry = models.PolygonField(srid=SRID_WGS84, spatial_index=True)
    monitoring_purpose = models.CharField(max_length=100, default="URBAN_GROWTH_MONITORING")
    status = models.CharField(
        max_length=50,
        choices=ChangeProjectStatus.CHOICES,
        default=ChangeProjectStatus.ACTIVE,
        db_index=True,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "spatial_change_project"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.name} ({self.status})"


class ChangeDetectionRun(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        ChangeDetectionProject, on_delete=models.CASCADE, related_name="runs"
    )
    baseline_item = models.ForeignKey(
        ImageryItem, on_delete=models.PROTECT, related_name="baseline_change_runs"
    )
    comparison_item = models.ForeignKey(
        ImageryItem, on_delete=models.PROTECT, related_name="comparison_change_runs"
    )
    algorithm_identifier = models.CharField(
        max_length=100, default="Seasonally_Detrended_Change_Vector_Analysis_CVA"
    )
    algorithm_version = models.CharField(max_length=50, default="1.1.0")
    parameters = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=50,
        choices=ChangeRunStatus.CHOICES,
        default=ChangeRunStatus.PENDING,
        db_index=True,
    )
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    processing_duration_sec = models.FloatField(default=0.0)
    total_candidates_detected = models.PositiveIntegerField(default=0)
    noise_regions_suppressed = models.PositiveIntegerField(default=0)
    valid_pixels_evaluated = models.BigIntegerField(default=0)
    masked_pixels_excluded = models.BigIntegerField(default=0)
    error_details = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "spatial_change_run"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Run {self.id} [{self.status}] - {self.algorithm_identifier}"


class ChangeCandidate(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(
        ChangeDetectionRun, on_delete=models.CASCADE, related_name="candidates"
    )
    candidate_id = models.CharField(max_length=100, db_index=True)
    category = models.CharField(
        max_length=60, choices=ChangeCategory.CHOICES, db_index=True
    )
    geometry = models.PolygonField(srid=SRID_WGS84, spatial_index=True)
    bounding_box = models.JSONField(default=list)
    detection_confidence = models.DecimalField(max_digits=5, decimal_places=4, default=0.5000)
    detection_method = models.CharField(
        max_length=100, default="Seasonally_Detrended_Change_Vector_Analysis_CVA"
    )
    area_sqm = models.DecimalField(max_digits=12, decimal_places=2, default=0.0)
    pixel_count_area_sqm = models.DecimalField(max_digits=12, decimal_places=2, default=0.0)
    change_magnitude = models.FloatField(null=True, blank=True)
    delta_ndvi = models.FloatField(null=True, blank=True)
    delta_ndbi = models.FloatField(null=True, blank=True)

    # Building association
    associated_building = models.ForeignKey(
        Building,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="change_candidates",
    )
    building_association_type = models.CharField(
        max_length=50,
        choices=BuildingAssociationType.CHOICES,
        default=BuildingAssociationType.UNASSOCIATED,
    )
    building_overlap_ratio = models.FloatField(default=0.0)
    building_distance_m = models.FloatField(default=0.0)

    # Review status
    review_status = models.CharField(
        max_length=50,
        choices=ChangeCandidateStatus.CHOICES,
        default=ChangeCandidateStatus.OBSERVED,
        db_index=True,
    )
    latest_reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_candidates",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "spatial_change_candidate"
        ordering = ["-detection_confidence", "-area_sqm"]

    def __str__(self) -> str:
        return f"{self.candidate_id} [{self.category} / {self.review_status}] ({self.area_sqm} m²)"


class ChangeEvidence(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    candidate = models.ForeignKey(
        ChangeCandidate, on_delete=models.CASCADE, related_name="evidence"
    )
    evidence_key = models.CharField(max_length=50)
    baseline_raster_asset = models.ForeignKey(
        RasterAsset, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    comparison_raster_asset = models.ForeignKey(
        RasterAsset, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    derived_diff_asset = models.ForeignKey(
        RasterAsset, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    pixel_resolution_m = models.DecimalField(max_digits=6, decimal_places=2, default=10.0)
    applied_displacement_m = models.FloatField(default=0.0)
    residual_registration_error_m = models.FloatField(default=0.0)
    total_positional_uncertainty_1sigma_m = models.FloatField(default=0.0)
    metrics_payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "spatial_change_evidence"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Evidence {self.evidence_key} for {self.candidate_id}"


class ChangeReview(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    candidate = models.ForeignKey(
        ChangeCandidate, on_delete=models.CASCADE, related_name="reviews"
    )
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    decision = models.CharField(max_length=50, choices=ReviewDecision.CHOICES)
    previous_status = models.CharField(max_length=50)
    new_status = models.CharField(max_length=50)
    comments = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "spatial_change_review"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Review {self.decision} on {self.candidate_id} by {self.reviewer}"


class SpatialModelVersion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    model_identifier = models.CharField(max_length=100, db_index=True)
    version_tag = models.CharField(max_length=50, default="1.0.0")
    analysis_type = models.CharField(max_length=60, choices=SpatialAnalysisType.CHOICES, db_index=True)
    description = models.TextField(blank=True, default="")
    parameters_schema = models.JSONField(default=dict, blank=True)
    training_metadata = models.JSONField(default=dict, blank=True)
    limitations = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "spatial_model_version"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.model_identifier} (v{self.version_tag}) [{self.analysis_type}]"


class SpatialAnalysisProject(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.PROTECT, related_name="spatial_analysis_projects"
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    aoi_geometry = models.PolygonField(srid=SRID_WGS84, spatial_index=True)
    status = models.CharField(max_length=50, default="ACTIVE", db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "spatial_analysis_project"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        org_name = getattr(self.organization, "display_name", getattr(self.organization, "legal_name", str(self.organization_id)))
        return f"{self.name} ({org_name})"


class SpatialAnalysisRun(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        SpatialAnalysisProject, on_delete=models.CASCADE, related_name="runs"
    )
    analysis_type = models.CharField(
        max_length=60, choices=SpatialAnalysisType.CHOICES, db_index=True
    )
    model_version = models.ForeignKey(
        SpatialModelVersion, on_delete=models.SET_NULL, null=True, blank=True, related_name="runs"
    )
    input_datasets = models.JSONField(default=list, blank=True)
    parameters = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=50,
        choices=SpatialAnalysisRunStatus.CHOICES,
        default=SpatialAnalysisRunStatus.PENDING,
        db_index=True,
    )
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    processing_duration_sec = models.FloatField(default=0.0)
    total_results_count = models.PositiveIntegerField(default=0)
    summary_metrics = models.JSONField(default=dict, blank=True)
    error_details = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "spatial_analysis_run"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"AnalysisRun {self.id} [{self.analysis_type}] ({self.status})"


class SpatialAnalysisResult(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(
        SpatialAnalysisRun, on_delete=models.CASCADE, related_name="results"
    )
    result_id = models.CharField(max_length=100, db_index=True)
    category = models.CharField(max_length=100, db_index=True)
    evidence_label = models.CharField(
        max_length=50,
        choices=EvidenceLabel.CHOICES,
        default=EvidenceLabel.ESTIMATED,
        db_index=True,
    )
    geometry = models.GeometryField(srid=SRID_WGS84, spatial_index=True, null=True, blank=True)
    bounding_box = models.JSONField(default=list, blank=True)
    score = models.FloatField(null=True, blank=True)
    confidence = models.DecimalField(max_digits=5, decimal_places=4, default=0.5000)
    associated_building = models.ForeignKey(
        Building,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="spatial_analysis_results",
    )
    raster_asset = models.ForeignKey(
        RasterAsset,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="analysis_results",
    )
    metrics_payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "spatial_analysis_result"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Result {self.result_id} [{self.category}] - {self.evidence_label}"


class SpatialAnalysisEvidence(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    result = models.ForeignKey(
        SpatialAnalysisResult, on_delete=models.CASCADE, related_name="evidence"
    )
    evidence_type = models.CharField(max_length=100, db_index=True)
    source_datasets = models.JSONField(default=list, blank=True)
    confidence_score = models.FloatField(default=0.5)
    limitations = models.TextField(blank=True, default="")
    explanation = models.TextField(blank=True, default="")
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "spatial_analysis_evidence"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Evidence {self.evidence_type} for {self.result.result_id}"


class SpatialScenario(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        SpatialAnalysisProject, on_delete=models.CASCADE, related_name="scenarios"
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    baseline_run = models.ForeignKey(
        SpatialAnalysisRun, on_delete=models.CASCADE, related_name="baseline_scenarios"
    )
    comparison_run = models.ForeignKey(
        SpatialAnalysisRun, on_delete=models.CASCADE, related_name="comparison_scenarios"
    )
    scenario_parameters = models.JSONField(default=dict, blank=True)
    delta_summary = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "spatial_scenario"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Scenario {self.name} ({self.project.name})"


class GeospatialScene(models.Model):
    """Saved God's Eye camera. A scene is not a parcel and does not certify elevation."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.PROTECT, related_name="geospatial_scenes"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="geospatial_scenes",
    )
    name = models.CharField(max_length=120)
    place_key = models.CharField(max_length=16, choices=ScenePlace.CHOICES)
    scene_mode = models.CharField(
        max_length=16, choices=SceneMode.CHOICES, default=SceneMode.GLOBE_3D
    )
    camera_longitude = models.DecimalField(max_digits=9, decimal_places=6)
    camera_latitude = models.DecimalField(max_digits=8, decimal_places=6)
    camera_height_m = models.DecimalField(max_digits=10, decimal_places=2)
    heading_deg = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    pitch_deg = models.DecimalField(max_digits=5, decimal_places=2, default=-35)
    roll_deg = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    terrain_exaggeration = models.DecimalField(max_digits=3, decimal_places=1, default=1)
    layer_state = models.JSONField(default=list, blank=True)
    bookmarks = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "spatial_geospatial_scene"
        ordering = ["-updated_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(place_key__in=sorted(ScenePlace.ALL)),
                name="spatial_scene_place_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(scene_mode__in=sorted(SceneMode.ALL)),
                name="spatial_scene_mode_valid",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "-updated_at"], name="spatial_scene_org_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.place_key})"


class LandIntelligenceProfile(models.Model):
    """Measured user or imported outline. Never a verified land-registry parcel."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.PROTECT, related_name="land_intelligence_profiles"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="land_intelligence_profiles",
    )
    name = models.CharField(max_length=120)
    boundary_class = models.CharField(max_length=32, choices=BoundaryClass.CHOICES)
    geometry = models.PolygonField(srid=SRID_WGS84, spatial_index=True)
    vertex_count = models.PositiveIntegerField()
    area_sqm = models.DecimalField(max_digits=14, decimal_places=2)
    perimeter_m = models.DecimalField(max_digits=14, decimal_places=2)
    centroid_longitude = models.DecimalField(max_digits=9, decimal_places=6)
    centroid_latitude = models.DecimalField(max_digits=8, decimal_places=6)
    bbox_min_longitude = models.DecimalField(max_digits=9, decimal_places=6)
    bbox_min_latitude = models.DecimalField(max_digits=8, decimal_places=6)
    bbox_max_longitude = models.DecimalField(max_digits=9, decimal_places=6)
    bbox_max_latitude = models.DecimalField(max_digits=8, decimal_places=6)
    metric_epsg = models.PositiveIntegerField()
    utm_zone = models.CharField(max_length=8)
    geometry_evidence = models.CharField(max_length=32, default=ProvenanceClass.USER_SUPPLIED)
    measurement_evidence = models.CharField(max_length=32, default=ProvenanceClass.MODELLED)
    measurement_note = models.TextField()
    source_note = models.CharField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "spatial_land_intelligence_profile"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(boundary_class__in=sorted(BoundaryClass.ALL)),
                name="spatial_land_boundary_class_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(geometry_evidence=ProvenanceClass.USER_SUPPLIED),
                name="spatial_land_geometry_user_supplied",
            ),
            models.CheckConstraint(
                condition=models.Q(measurement_evidence=ProvenanceClass.MODELLED),
                name="spatial_land_measurement_modelled",
            ),
            models.CheckConstraint(
                condition=models.Q(area_sqm__gt=0) & models.Q(perimeter_m__gt=0),
                name="spatial_land_metrics_positive",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "-created_at"], name="spatial_land_org_idx"),
        ]

    @property
    def legal_status(self) -> str:
        return "NOT_A_REGISTERED_PARCEL"

    @property
    def planning_status(self) -> str:
        return "PLANNING STATUS NOT VERIFIED"

    def __str__(self) -> str:
        return f"{self.name} ({self.boundary_class})"


class GeospatialDataSource(models.Model):
    """Provider control record. A successful ping does not make a source commercially free."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    provider_id = models.SlugField(max_length=64, unique=True)
    official_name = models.CharField(max_length=160)
    dataset_name = models.CharField(max_length=200)
    license_identifier = models.CharField(max_length=200)
    commercial_use = models.CharField(max_length=32, choices=CommercialUse.CHOICES)
    attribution = models.TextField()
    authentication_type = models.CharField(max_length=64)
    cost_classification = models.CharField(max_length=200)
    rate_limit_note = models.TextField()
    monthly_allowance_note = models.TextField()
    geographic_coverage = models.CharField(max_length=200)
    temporal_coverage = models.CharField(max_length=200)
    resolution_note = models.TextField()
    freshness_note = models.TextField()
    service_status = models.CharField(max_length=32, choices=ProviderServiceStatus.CHOICES)
    processing_note = models.TextField()
    caching_policy = models.TextField()
    catalog_url = models.URLField(max_length=400, blank=True, default="")
    check_mode = models.CharField(max_length=32, default="none")
    stac_search = models.BooleanField(default=False)
    credential_required = models.BooleanField(default=False)
    allowed_host = models.CharField(max_length=200, blank=True, default="")
    operational_state = models.CharField(
        max_length=32,
        choices=ProviderOperationalState.CHOICES,
        default=ProviderOperationalState.NOT_CHECKED,
    )
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_http_status = models.PositiveIntegerField(null=True, blank=True)
    last_error = models.CharField(max_length=300, blank=True, default="")
    discovered_summary = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "spatial_geospatial_data_source"
        ordering = ["official_name"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(service_status__in=sorted(ProviderServiceStatus.ALL)),
                name="spatial_datasource_status_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(commercial_use__in=sorted(CommercialUse.ALL)),
                name="spatial_datasource_commercial_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(operational_state__in=sorted(ProviderOperationalState.ALL)),
                name="spatial_datasource_ops_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.official_name} ({self.service_status})"


class ProviderQueryRecord(models.Model):
    """Audit of a member's provider call. The upstream payload is not stored."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.PROTECT, related_name="provider_query_records"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="provider_query_records",
    )
    provider = models.ForeignKey(
        GeospatialDataSource, on_delete=models.PROTECT, related_name="query_records"
    )
    query_kind = models.CharField(max_length=32)
    http_status = models.PositiveIntegerField(null=True, blank=True)
    operational_state = models.CharField(max_length=32, blank=True, default="")
    item_count = models.PositiveIntegerField(default=0)
    collection_id = models.CharField(max_length=120, blank=True, default="")
    bbox = models.JSONField(null=True, blank=True)
    error = models.CharField(max_length=300, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "spatial_provider_query_record"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["organization", "-created_at"], name="spatial_provider_query_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.query_kind} {self.provider_id_label}"

    @property
    def provider_id_label(self) -> str:
        return self.provider.provider_id


class GeospatialAsset(models.Model):
    """One organization file. The storage key is relative and is not an API field."""

    KIND_CHOICES = [("source", "Source"), ("derived", "Derived")]
    PROVENANCE_CHOICES = [("USER-SUPPLIED", "User supplied"), ("MODELLED", "Modelled")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.PROTECT, related_name="geospatial_assets"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="geospatial_assets",
    )
    kind = models.CharField(max_length=16, choices=KIND_CHOICES)
    media_type = models.CharField(max_length=128)
    byte_size = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)
    storage_key = models.CharField(max_length=300, blank=True, default="")
    provenance = models.CharField(max_length=32, choices=PROVENANCE_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "spatial_geospatial_asset"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["organization", "-created_at"], name="spatial_asset_org_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(kind__in=["source", "derived"]),
                name="spatial_asset_kind_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(provenance__in=["USER-SUPPLIED", "MODELLED"]),
                name="spatial_asset_prov_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.kind} {self.sha256[:12]}"


class ComputeEntitlement(models.Model):
    """An owner decision. A row here does not enable a billable GPU request."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="compute_entitlements"
    )
    provider_id = models.CharField(max_length=32, choices=PROVIDER_CHOICES)
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="compute_entitlements_granted",
    )
    note = models.CharField(max_length=300, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "spatial_compute_entitlement"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "provider_id"],
                name="spatial_compute_entitlement_uq",
            ),
            models.CheckConstraint(
                condition=models.Q(provider_id__in=[item for item, _label in PROVIDER_CHOICES]),
                name="spatial_compute_ent_provider",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.provider_id} entitlement"


class ComputeJob(models.Model):
    """One isolated processing job. The row is the durable record, not the broker."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.PROTECT, related_name="compute_jobs"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="compute_jobs",
    )
    provider_id = models.CharField(max_length=32, choices=PROVIDER_CHOICES)
    operation = models.CharField(max_length=64)
    state = models.CharField(max_length=16, choices=STATE_CHOICES, default="PENDING")
    progress = models.PositiveSmallIntegerField(default=0)
    attempt = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=1)
    error_class = models.CharField(max_length=64, blank=True, default="")
    error_detail = models.CharField(max_length=300, blank=True, default="")
    input_asset = models.ForeignKey(
        GeospatialAsset,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="compute_jobs_as_input",
    )
    output_asset = models.ForeignKey(
        GeospatialAsset,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="compute_jobs_as_output",
    )
    source_retained = models.BooleanField(default=True)
    cleanup = models.JSONField(default=dict, blank=True)
    estimated_cost_amount = models.DecimalField(
        max_digits=12, decimal_places=4, null=True, blank=True
    )
    actual_cost_amount = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal("0"))
    external_request_count = models.PositiveIntegerField(default=0)
    preflight = models.JSONField(default=dict, blank=True)
    provenance = models.JSONField(default=dict, blank=True)
    idempotency_key = models.CharField(max_length=80, blank=True, default="")
    simulate = models.CharField(max_length=16, blank=True, default="")
    capability_hash = models.CharField(max_length=64, blank=True, default="")
    capability_expires_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "spatial_compute_job"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["organization", "-created_at"], name="spatial_compute_job_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(state__in=[item for item, _label in STATE_CHOICES]),
                name="spatial_compute_state_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(provider_id__in=[item for item, _label in PROVIDER_CHOICES]),
                name="spatial_compute_provider_ok",
            ),
            models.UniqueConstraint(
                fields=["organization", "idempotency_key"],
                condition=~models.Q(idempotency_key=""),
                name="spatial_compute_idem_uniq",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.operation} {self.state}"


class ComputeJobEvent(models.Model):
    """Append-only job audit. Messages must not contain credentials or file paths."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(ComputeJob, on_delete=models.CASCADE, related_name="events")
    organization = models.ForeignKey(
        Organization, on_delete=models.PROTECT, related_name="compute_job_events"
    )
    state = models.CharField(max_length=16)
    message = models.CharField(max_length=300)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "spatial_compute_job_event"
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["job", "created_at"], name="spatial_compute_evt_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.state} {self.message[:40]}"
