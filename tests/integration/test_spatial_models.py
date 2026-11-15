"""Integration tests for spatial models, database constraints, and relationships."""

import uuid
from datetime import date, datetime, timezone
import pytest
from django.contrib.gis.geos import MultiLineString, MultiPolygon, Point, Polygon
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from apps.organizations.models import Organization
from apps.properties.models import Property
from apps.spatial.domain import (
    SRID_WGS84,
    BeaconType,
    BuildingClassification,
    ConstructionPhase,
    DatasetCategory,
    DatasetFormat,
    InfrastructureType,
    LegalAuthorityLevel,
    LicenseType,
    LifecycleStatus,
    LineageType,
    PropertyBuildingAssocType,
    PropertyParcelAssocType,
    RoadClass,
    RoadSurface,
    UpdateFrequency,
    VerticalDatum,
)
from apps.spatial.models import (
    AdministrativeBoundary,
    Building,
    BuildingParcelIntersection,
    ConstructionSite,
    DatasetQualityReport,
    DatasetRegistry,
    DatasetVersion,
    EntityLineage,
    ImageryObservation,
    InfrastructureAsset,
    LandParcel,
    PropertyBuildingAssociation,
    PropertyParcelAssociation,
    Road,
    SpatialEntity,
    SpatialIdentityMapping,
    SpatialQuarantine,
    SurveyObservation,
    TerrainFeature,
)


@pytest.fixture
def sample_extent():
    return Polygon(
        (
            (39.5, -4.2),
            (39.8, -4.2),
            (39.8, -3.9),
            (39.5, -3.9),
            (39.5, -4.2),
        ),
        srid=SRID_WGS84,
    )


@pytest.fixture
def dataset_and_version(db, sample_extent):
    registry = DatasetRegistry.objects.create(
        dataset_urn="urn:glitters:dataset:ke:msa:test-dataset",
        name="Mombasa Test Spatial Baseline",
        category=DatasetCategory.BUILDINGS,
        update_frequency=UpdateFrequency.STATIC,
    )
    version = DatasetVersion.objects.create(
        dataset=registry,
        version_tag="2026.1",
        source_checksum_sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        pipeline_git_commit="4f385c2d1234567890abcdef1234567890abcdef",
        license_type=LicenseType.ODBL,
        attribution_statement="OpenStreetMap contributors",
        spatial_extent=sample_extent,
        feature_count=100,
    )
    DatasetQualityReport.objects.create(
        version=version,
        valid_geometries=100,
        repaired_geometries=0,
        quarantined_geometries=0,
        duplicate_records=0,
        horizontal_rmse_m=1.000,
    )
    return registry, version


@pytest.fixture
def sample_polygon():
    poly = Polygon(
        (
            (39.664, -4.062),
            (39.665, -4.062),
            (39.665, -4.063),
            (39.664, -4.063),
            (39.664, -4.062),
        ),
        srid=SRID_WGS84,
    )
    return MultiPolygon([poly], srid=SRID_WGS84)


@pytest.mark.django_db
def test_dataset_registry_and_version(dataset_and_version):
    registry, version = dataset_and_version
    assert registry.dataset_urn == "urn:glitters:dataset:ke:msa:test-dataset"
    assert version.dataset == registry
    assert version.quality_report.valid_geometries == 100


@pytest.mark.django_db
def test_spatial_identity_mapping_uniqueness(dataset_and_version):
    _, version = dataset_and_version
    entity = SpatialEntity.objects.create(
        entity_type="building",
        dataset_version=version,
    )
    assert entity.canonical_urn == f"urn:glitters:spatial:building:{entity.id}"

    # First mapping succeeds
    SpatialIdentityMapping.objects.create(
        entity=entity,
        provider_name="OSM",
        provider_external_id="way/1151899424",
    )

    # Duplicate mapping on same provider + external id fails
    with pytest.raises(IntegrityError):
        SpatialIdentityMapping.objects.create(
            entity=entity,
            provider_name="OSM",
            provider_external_id="way/1151899424",
        )


@pytest.mark.django_db
def test_building_and_parcel_creation(dataset_and_version, sample_polygon):
    _, version = dataset_and_version

    parcel = LandParcel.objects.create(
        dataset_version=version,
        cadastral_identifier="MOMBASA/BLOCK_XXII/104",
        tenure_type="FREEHOLD",
        geom=sample_polygon,
    )
    assert parcel.computed_area_sqm is not None
    assert parcel.computed_area_sqm > 0
    assert parcel.canonical_urn.startswith("urn:glitters:spatial:land_parcel:")

    building = Building.objects.create(
        dataset_version=version,
        name="Old Town Heritage Commercial",
        classification=BuildingClassification.COMMERCIAL,
        height_m=14.50,
        levels_above_ground=4,
        geom=sample_polygon,
    )
    assert building.footprint_area_sqm is not None
    assert building.footprint_area_sqm > 0
    assert building.canonical_urn.startswith("urn:glitters:spatial:building:")


@pytest.mark.django_db
def test_survey_observation_3d(dataset_and_version):
    _, version = dataset_and_version
    point_z = Point(39.664215, -4.062831, 15.420, srid=SRID_WGS84)

    beacon = SurveyObservation.objects.create(
        dataset_version=version,
        beacon_code="SK_BM_402",
        beacon_type=BeaconType.IRON_PIN_CONCRETE,
        vertical_datum=VerticalDatum.EGM2008_ORTHOMETRIC,
        elevation_orthometric_m=15.420,
        elevation_ellipsoidal_m=3.120,
        surveyor_license="RLS-KE-088",
        observation_timestamp=datetime(2026, 8, 14, 10, 0, tzinfo=timezone.utc),
        geom_point_z=point_z,
    )
    assert beacon.geom_point_z.hasz
    assert beacon.geom_point_z.z == 15.420


@pytest.mark.django_db
def test_decoupled_mn_relationships(dataset_and_version, sample_polygon):
    _, version = dataset_and_version

    parcel = LandParcel.objects.create(
        dataset_version=version,
        cadastral_identifier="MOMBASA/BLOCK_XVII/99",
        tenure_type="FREEHOLD",
        geom=sample_polygon,
    )
    building = Building.objects.create(
        dataset_version=version,
        name="Seaport Storage Warehouse",
        geom=sample_polygon,
    )

    # BuildingParcelIntersection
    intersection = BuildingParcelIntersection.objects.create(
        building=building,
        parcel=parcel,
        intersection_area_sqm=1200.50,
        percentage_of_building=100.0,
        is_primary_parcel=True,
    )
    assert intersection.is_primary_parcel
    assert intersection.percentage_of_building == 100.0


@pytest.mark.django_db
def test_entity_lineage_acyclic_enforcement():
    urn_a = f"urn:glitters:spatial:land_parcel:{uuid.uuid4()}"
    urn_b = f"urn:glitters:spatial:land_parcel:{uuid.uuid4()}"
    urn_c = f"urn:glitters:spatial:land_parcel:{uuid.uuid4()}"

    # Valid step 1: A -> B (subdivision)
    EntityLineage.objects.create(
        predecessor_urn=urn_a,
        successor_urn=urn_b,
        lineage_type=LineageType.SUBDIVISION,
        effective_date=date(2026, 1, 1),
    )

    # Valid step 2: B -> C
    EntityLineage.objects.create(
        predecessor_urn=urn_b,
        successor_urn=urn_c,
        lineage_type=LineageType.SUBDIVISION,
        effective_date=date(2026, 6, 1),
    )

    # Self link fails: C -> C
    with pytest.raises(ValidationError):
        EntityLineage.objects.create(
            predecessor_urn=urn_c,
            successor_urn=urn_c,
            lineage_type=LineageType.CORRECTION,
            effective_date=date(2026, 6, 2),
        )

    # Cycle fails: C -> A
    with pytest.raises(ValidationError):
        EntityLineage.objects.create(
            predecessor_urn=urn_c,
            successor_urn=urn_a,
            lineage_type=LineageType.AMALGAMATION,
            effective_date=date(2026, 6, 3),
        )


@pytest.mark.django_db
def test_spatial_quarantine_logging(dataset_and_version):
    _, version = dataset_and_version
    record = SpatialQuarantine.objects.create(
        source_dataset_version=version,
        source_record_id="way/bad-9999",
        raw_payload={"type": "Feature", "properties": {"id": 9999}},
        failure_stage="TOPOLOGY_INVALID",
        error_message="Self-intersection at [39.66, -4.05]",
    )
    assert record.id is not None
    assert record.failure_stage == "TOPOLOGY_INVALID"
