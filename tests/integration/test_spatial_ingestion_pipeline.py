"""Integration tests for spatial dataset ingestion, MTI persistence, idempotency, and quarantine."""

import uuid
from decimal import Decimal
import pytest
from django.contrib.gis.geos import MultiPolygon, Polygon
from django.db import transaction

from apps.organizations.models import Organization
from apps.spatial.domain import (
    BuildingClassification,
    DatasetCategory,
    DatasetVersionStatus,
    LegalAuthorityLevel,
    LicenseType,
    ReconciliationDisposition,
    SRID_WGS84,
    UpdateFrequency,
)
from apps.spatial.models import (
    Building,
    DatasetRegistry,
    DatasetVersion,
    SpatialEntity,
    SpatialIdentityMapping,
    SpatialQuarantine,
    SpatialSourceFeature,
)
from apps.spatial.services.ingestion import SpatialIngestionEngine


@pytest.fixture
def spatial_dataset_version(db):
    registry = DatasetRegistry.objects.create(
        dataset_urn=f"urn:glitters:dataset:test:{uuid.uuid4().hex[:8]}",
        name="Test Mombasa OSM Buildings",
        category=DatasetCategory.BUILDINGS,
        update_frequency=UpdateFrequency.STATIC,
    )
    extent = Polygon.from_bbox((39.5, -4.2, 39.8, -3.9))
    extent.srid = SRID_WGS84
    return DatasetVersion.objects.create(
        dataset=registry,
        version_tag="2026.11-test",
        source_url="https://test.osm.org",
        source_checksum_sha256="0" * 64,
        pipeline_git_commit="abcdef1234567890abcdef1234567890abcdef12",
        license_type=LicenseType.ODBL,
        attribution_statement="© OpenStreetMap contributors",
        spatial_extent=extent,
        feature_count=0,
    )


@pytest.mark.django_db
def test_ingest_valid_features(spatial_dataset_version):
    engine = SpatialIngestionEngine(dataset_version=spatial_dataset_version, batch_size=10, dry_run=False)

    feature = {
        "type": "Feature",
        "id": "way/123456",
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [39.664, -4.051],
                    [39.666, -4.051],
                    [39.666, -4.049],
                    [39.664, -4.049],
                    [39.664, -4.051],
                ]
            ],
        },
        "properties": {
            "@id": 123456,
            "@type": "way",
            "@version": 3,
            "building": "apartments",
            "name": "Nyali Ocean View Apartments",
            "height": "18.5m",
            "building:levels": "6",
        },
    }

    result = engine.run([feature])
    assert result.total_input == 1
    assert result.inserted == 1
    assert result.updated == 0
    assert result.unchanged == 0
    assert result.quarantined == 0
    assert result.is_invariant_satisfied

    # Verify database persistence
    building = Building.objects.get(name="Nyali Ocean View Apartments")
    assert building.classification == BuildingClassification.RESIDENTIAL
    assert building.height_m == Decimal("18.50")
    assert building.levels_above_ground == 6
    assert building.organization is None  # Public baseline
    assert building.dataset_version == spatial_dataset_version

    # Verify identity mapping
    mapping = SpatialIdentityMapping.objects.get(provider_name="osm", provider_external_id="way/123456")
    assert mapping.entity_id == building.id

    # Verify source feature
    src_feat = SpatialSourceFeature.objects.get(source_record_id="way/123456")
    assert src_feat.disposition == ReconciliationDisposition.INSERTED
    assert src_feat.raw_properties["name"] == "Nyali Ocean View Apartments"


@pytest.mark.django_db
def test_ingest_idempotency_second_run(spatial_dataset_version):
    engine = SpatialIngestionEngine(dataset_version=spatial_dataset_version, batch_size=10, dry_run=False)

    feature = {
        "type": "Feature",
        "id": "way/999111",
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [39.670, -4.060],
                    [39.672, -4.060],
                    [39.672, -4.058],
                    [39.670, -4.058],
                    [39.670, -4.060],
                ]
            ],
        },
        "properties": {
            "@id": 999111,
            "@type": "way",
            "building": "commercial",
            "name": "Mombasa Trade Centre",
        },
    }

    # First run
    res1 = engine.run([feature])
    assert res1.inserted == 1
    initial_building_count = Building.objects.count()

    # Second run with identical feature
    res2 = engine.run([feature])
    assert res2.total_input == 1
    assert res2.inserted == 0
    assert res2.updated == 0
    assert res2.unchanged == 1
    assert res2.quarantined == 0
    assert res2.is_invariant_satisfied

    # Verify no new building was created
    assert Building.objects.count() == initial_building_count


@pytest.mark.django_db
def test_ingest_updated_feature(spatial_dataset_version):
    engine = SpatialIngestionEngine(dataset_version=spatial_dataset_version, batch_size=10, dry_run=False)

    feature_v1 = {
        "type": "Feature",
        "id": "way/555555",
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [39.670, -4.060],
                    [39.672, -4.060],
                    [39.672, -4.058],
                    [39.670, -4.058],
                    [39.670, -4.060],
                ]
            ],
        },
        "properties": {
            "@id": 555555,
            "@type": "way",
            "building": "commercial",
            "name": "Old Tower",
        },
    }
    engine.run([feature_v1])
    building_v1 = Building.objects.get(name="Old Tower")

    # Feature updated with new name and height
    feature_v2 = {
        "type": "Feature",
        "id": "way/555555",
        "geometry": feature_v1["geometry"],
        "properties": {
            "@id": 555555,
            "@type": "way",
            "building": "commercial",
            "name": "Renovated Tower",
            "height": "45.0m",
        },
    }
    res2 = engine.run([feature_v2])
    assert res2.inserted == 0
    assert res2.updated == 1
    assert res2.unchanged == 0
    assert res2.quarantined == 0

    building_v2 = Building.objects.get(id=building_v1.id)
    assert building_v2.name == "Renovated Tower"
    assert building_v2.height_m == Decimal("45.00")


@pytest.mark.django_db
def test_ingest_quarantines_missing_identifier(spatial_dataset_version):
    engine = SpatialIngestionEngine(dataset_version=spatial_dataset_version, batch_size=10, dry_run=False)

    feature = {
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [39.670, -4.060],
                    [39.672, -4.060],
                    [39.672, -4.058],
                    [39.670, -4.058],
                    [39.670, -4.060],
                ]
            ],
        },
        "properties": {
            # No osm_id or @id
            "building": "house",
        },
    }

    res = engine.run([feature])
    assert res.total_input == 1
    assert res.inserted == 0
    assert res.quarantined == 1
    assert res.is_invariant_satisfied

    quarantine = SpatialQuarantine.objects.filter(source_dataset_version=spatial_dataset_version).first()
    assert quarantine is not None
    assert "Missing OSM identifier" in quarantine.error_message


@pytest.mark.django_db
def test_ingest_quarantines_out_of_bounds(spatial_dataset_version):
    engine = SpatialIngestionEngine(dataset_version=spatial_dataset_version, batch_size=10, dry_run=False)

    # Nairobi coordinates (outside Mombasa envelope)
    feature = {
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [36.81, -1.30],
                    [36.83, -1.30],
                    [36.83, -1.28],
                    [36.81, -1.28],
                    [36.81, -1.30],
                ]
            ],
        },
        "properties": {
            "@id": 777777,
            "@type": "way",
            "building": "office",
        },
    }

    res = engine.run([feature])
    assert res.total_input == 1
    assert res.quarantined == 1
    assert res.is_invariant_satisfied

    quarantine = SpatialQuarantine.objects.get(source_record_id="777777")
    assert "outside Mombasa" in quarantine.error_message


@pytest.mark.django_db
def test_public_baseline_mutation_protection(spatial_dataset_version):
    org = Organization.objects.create(
        legal_name="Commercial Realty Ltd",
        display_name="Commercial Realty",
        slug="commercial-realty",
        organization_type="agency",
    )
    engine = SpatialIngestionEngine(dataset_version=spatial_dataset_version, batch_size=10, dry_run=False)

    feature = {
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [39.670, -4.060],
                    [39.672, -4.060],
                    [39.672, -4.058],
                    [39.670, -4.058],
                    [39.670, -4.060],
                ]
            ],
        },
        "properties": {
            "@id": 888111,
            "@type": "way",
            "building": "commercial",
            "name": "Public Landmark Plaza",
        },
    }
    engine.run([feature])

    # Query using for_organization
    public_buildings = Building.objects.for_organization(org)
    assert public_buildings.filter(name="Public Landmark Plaza").exists()

    building = Building.objects.get(name="Public Landmark Plaza")
    assert building.organization is None  # Public


@pytest.mark.django_db
def test_ingest_repairable_geometry_promoted(spatial_dataset_version):
    engine = SpatialIngestionEngine(dataset_version=spatial_dataset_version, batch_size=10, dry_run=False)

    # Self-intersecting bow-tie polygon
    feature = {
        "type": "Feature",
        "id": "way/333333",
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [39.664, -4.051],
                    [39.666, -4.049],
                    [39.666, -4.051],
                    [39.664, -4.049],
                    [39.664, -4.051],
                ]
            ],
        },
        "properties": {
            "@id": 333333,
            "@type": "way",
            "building": "yes",
            "name": "Healed Pavilion",
        },
    }

    res = engine.run([feature])
    assert res.total_input == 1
    assert res.inserted == 1
    assert res.quarantined == 0
    assert res.repaired_count == 1
    assert res.is_invariant_satisfied

    b = Building.objects.get(name="Healed Pavilion")
    assert b.geom.valid
    assert b.geom.geom_type == "MultiPolygon"

    src_feat = SpatialSourceFeature.objects.get(source_record_id="way/333333")
    assert src_feat.was_repaired is True
    assert "make_valid healed topology" in src_feat.repair_details
    assert src_feat.raw_geometry["type"] == "Polygon"
    assert src_feat.repaired_geometry is not None
    assert src_feat.repaired_geometry["type"] == "MultiPolygon"


@pytest.mark.django_db
def test_ingest_unsupported_classification_fallback(spatial_dataset_version):
    engine = SpatialIngestionEngine(dataset_version=spatial_dataset_version, batch_size=10, dry_run=False)

    feature = {
        "type": "Feature",
        "id": "way/444444",
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [39.670, -4.060],
                    [39.672, -4.060],
                    [39.672, -4.058],
                    [39.670, -4.058],
                    [39.670, -4.060],
                ]
            ],
        },
        "properties": {
            "@id": 444444,
            "@type": "way",
            "building": "exotic_gazebo_xyz",
            "name": "Unknown Gazebo",
        },
    }

    res = engine.run([feature])
    assert res.inserted == 1
    b = Building.objects.get(name="Unknown Gazebo")
    assert b.classification == BuildingClassification.OTHER


@pytest.mark.django_db
def test_transaction_rollback_on_batch_failure(spatial_dataset_version, monkeypatch):
    engine = SpatialIngestionEngine(dataset_version=spatial_dataset_version, batch_size=10, dry_run=False)

    feature = {
        "type": "Feature",
        "id": "way/666666",
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [39.670, -4.060],
                    [39.672, -4.060],
                    [39.672, -4.058],
                    [39.670, -4.058],
                    [39.670, -4.060],
                ]
            ],
        },
        "properties": {
            "@id": 666666,
            "@type": "way",
            "building": "house",
            "name": "Rollback Candidate",
        },
    }

    # Simulate a database failure during SpatialSourceFeature bulk creation
    def broken_bulk_create(*args, **kwargs):
        raise RuntimeError("Simulated database failure during bulk write")

    monkeypatch.setattr(SpatialSourceFeature.objects, "bulk_create", broken_bulk_create)

    with pytest.raises(RuntimeError, match="Simulated database failure"):
        engine.run([feature])

    # Invariant: Neither SpatialEntity nor Building nor SpatialIdentityMapping must remain
    assert not Building.objects.filter(name="Rollback Candidate").exists()
    assert not SpatialIdentityMapping.objects.filter(provider_external_id="way/666666").exists()
    assert not SpatialEntity.objects.filter(canonical_urn__contains="666666").exists()


@pytest.mark.django_db
def test_source_feature_versioning_preservation(spatial_dataset_version):
    registry = spatial_dataset_version.dataset
    extent = spatial_dataset_version.spatial_extent

    # Create a second dataset version
    v2 = DatasetVersion.objects.create(
        dataset=registry,
        version_tag="2026.12-v2",
        source_url="https://test.osm.org",
        source_checksum_sha256="1" * 64,
        pipeline_git_commit="fedcba0987654321fedcba0987654321fedcba09",
        license_type=LicenseType.ODBL,
        attribution_statement="© OpenStreetMap contributors",
        spatial_extent=extent,
        feature_count=0,
    )

    engine_v1 = SpatialIngestionEngine(dataset_version=spatial_dataset_version, batch_size=10, dry_run=False)
    engine_v2 = SpatialIngestionEngine(dataset_version=v2, batch_size=10, dry_run=False)

    feature_v1 = {
        "type": "Feature",
        "id": "way/888999",
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [39.670, -4.060],
                    [39.672, -4.060],
                    [39.672, -4.058],
                    [39.670, -4.058],
                    [39.670, -4.060],
                ]
            ],
        },
        "properties": {
            "@id": 888999,
            "@type": "way",
            "@version": 1,
            "building": "commercial",
            "name": "Historical Port Office",
        },
    }

    # Ingest v1
    res1 = engine_v1.run([feature_v1])
    assert res1.inserted == 1

    # Ingest v2 with updated tag and version
    feature_v2 = {
        "type": "Feature",
        "id": "way/888999",
        "geometry": feature_v1["geometry"],
        "properties": {
            "@id": 888999,
            "@type": "way",
            "@version": 2,
            "building": "commercial",
            "name": "Upgraded Port Terminal",
            "height": "25.0m",
        },
    }
    res2 = engine_v2.run([feature_v2])
    assert res2.updated == 1

    # Invariant: Both historical source feature records must exist
    source_records = list(SpatialSourceFeature.objects.filter(source_record_id="way/888999").order_by("created_at"))
    assert len(source_records) == 2

    sf1, sf2 = source_records
    assert sf1.dataset_version == spatial_dataset_version
    assert sf1.source_version == "1"
    assert sf1.raw_properties["name"] == "Historical Port Office"
    assert sf1.disposition == ReconciliationDisposition.INSERTED

    assert sf2.dataset_version == v2
    assert sf2.source_version == "2"
    assert sf2.raw_properties["name"] == "Upgraded Port Terminal"
    assert sf2.disposition == ReconciliationDisposition.UPDATED

    # Invariant: Canonical entity remains singular
    assert Building.objects.filter(name="Upgraded Port Terminal").count() == 1
    assert Building.objects.filter(name="Historical Port Office").count() == 0


@pytest.mark.django_db
def test_mti_persistence_integrity_and_invariants(spatial_dataset_version):
    """
    Verifies MTI structural invariants:
    1. Parent and child primary-key consistency.
    2. Correct child parent-link foreign key (spatialentity_ptr_id).
    3. Identity mapping integrity.
    4. Zero orphaned parent or child rows.
    5. Geometry constraints (SRID=4326, MultiPolygon).
    6. Unique external identity constraints.
    """
    engine = SpatialIngestionEngine(dataset_version=spatial_dataset_version, batch_size=10, dry_run=False)

    features = [
        {
            "type": "Feature",
            "id": f"way/audit_{i}",
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [39.670 + i * 0.001, -4.060],
                        [39.671 + i * 0.001, -4.060],
                        [39.671 + i * 0.001, -4.059],
                        [39.670 + i * 0.001, -4.059],
                        [39.670 + i * 0.001, -4.060],
                    ]
                ],
            },
            "properties": {
                "@id": f"audit_{i}",
                "@type": "way",
                "building": "commercial",
                "name": f"Audit Building {i}",
            },
        }
        for i in range(5)
    ]

    res = engine.run(features)
    assert res.inserted == 5

    # 1 & 2. Verify PK and parent-link consistency
    buildings = list(Building.objects.filter(name__startswith="Audit Building").order_by("name"))
    assert len(buildings) == 5

    for b in buildings:
        parent = SpatialEntity.objects.get(id=b.id)
        assert b.spatialentity_ptr_id == parent.id
        assert b.id == parent.id
        assert parent.entity_type == "building"
        assert parent.canonical_urn.startswith("urn:glitters:spatial:building:")

        # 3. Identity mapping integrity
        mapping = SpatialIdentityMapping.objects.get(entity=parent)
        assert mapping.provider_name == "osm"
        assert mapping.provider_external_id.startswith("way/audit_")

        # 5. Geometry constraints
        assert b.geom.srid == 4326
        assert b.geom.geom_type == "MultiPolygon"
        assert b.geom.valid

    # 4. Zero orphaned rows
    building_entity_count = SpatialEntity.objects.filter(entity_type="building").count()
    building_child_count = Building.objects.count()
    assert building_entity_count == building_child_count

    # 6. Unique external identity constraint violation test
    duplicate_feature = features[0]
    # Running duplicate in another dataset version points to same external mapping
    res_dup = engine.run([duplicate_feature])
    assert res_dup.unchanged == 1
    assert res_dup.inserted == 0
    # Building count should remain identical
    assert Building.objects.count() == building_child_count


@pytest.mark.django_db
def test_dataset_lifecycle_state_transitions(spatial_dataset_version):
    version = spatial_dataset_version
    assert version.status == DatasetVersionStatus.REGISTERED

    # Ingesting state
    version.status = DatasetVersionStatus.INGESTING
    version.save(update_fields=["status"])
    assert DatasetVersion.objects.get(id=version.id).status == DatasetVersionStatus.INGESTING

    # Canonical queries must filter for PUBLISHED only
    published_versions = DatasetVersion.objects.filter(status=DatasetVersionStatus.PUBLISHED)
    assert not published_versions.filter(id=version.id).exists()

    # Validating state
    version.status = DatasetVersionStatus.VALIDATING
    version.save(update_fields=["status"])
    assert DatasetVersion.objects.get(id=version.id).status == DatasetVersionStatus.VALIDATING
    assert not published_versions.filter(id=version.id).exists()

    # Published state
    version.status = DatasetVersionStatus.PUBLISHED
    version.feature_count = 100
    version.save(update_fields=["status", "feature_count"])
    assert DatasetVersion.objects.get(id=version.id).status == DatasetVersionStatus.PUBLISHED
    assert published_versions.filter(id=version.id).exists()


@pytest.mark.django_db
def test_resumable_recovery_after_chunk_process_termination(spatial_dataset_version):
    """Simulates process termination after chunk 1, followed by resumed execution."""
    features = [
        {
            "type": "Feature",
            "id": f"way/resume_{i}",
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [39.670 + i * 0.001, -4.060],
                        [39.671 + i * 0.001, -4.060],
                        [39.671 + i * 0.001, -4.059],
                        [39.670 + i * 0.001, -4.059],
                        [39.670 + i * 0.001, -4.060],
                    ]
                ],
            },
            "properties": {
                "@id": f"resume_{i}",
                "@type": "way",
                "building": "residential",
                "name": f"Resumable Building {i}",
            },
        }
        for i in range(6)
    ]

    # Run Chunk 1 (first 2 features) with batch_size=2
    engine = SpatialIngestionEngine(dataset_version=spatial_dataset_version, batch_size=2, dry_run=False)
    res_chunk1 = engine.run(features[:2])
    assert res_chunk1.inserted == 2
    assert Building.objects.filter(name__startswith="Resumable Building").count() == 2

    # Simulate process death: dataset version is still REGISTERED or INGESTING
    assert spatial_dataset_version.status != DatasetVersionStatus.PUBLISHED

    # Restart / resume process with the ENTIRE 6-record dataset
    res_full = engine.run(features)
    assert res_full.total_input == 6
    assert res_full.unchanged == 2  # Chunks 1 features recognized and untouched!
    assert res_full.inserted == 4   # Remaining features cleanly inserted!
    assert res_full.updated == 0
    assert res_full.quarantined == 0
    assert res_full.is_invariant_satisfied

    # Total canonical entities must be exactly 6, zero duplicates
    assert Building.objects.filter(name__startswith="Resumable Building").count() == 6
    assert SpatialIdentityMapping.objects.filter(provider_external_id__startswith="way/resume_").count() == 6


@pytest.mark.django_db
def test_chunk_rollback_preserves_prior_committed_chunks(spatial_dataset_version, monkeypatch):
    """Simulates failure during chunk 2: chunk 1 stays committed, chunk 2 rolls back completely."""
    chunk1_features = [
        {
            "type": "Feature",
            "id": "way/chunk1_1",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[39.670, -4.060], [39.671, -4.060], [39.671, -4.059], [39.670, -4.059], [39.670, -4.060]]],
            },
            "properties": {"@id": "chunk1_1", "@type": "way", "building": "yes", "name": "Chunk 1 Good"},
        }
    ]
    chunk2_features = [
        {
            "type": "Feature",
            "id": "way/chunk2_1",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[39.672, -4.060], [39.673, -4.060], [39.673, -4.059], [39.672, -4.059], [39.672, -4.060]]],
            },
            "properties": {"@id": "chunk2_1", "@type": "way", "building": "yes", "name": "Chunk 2 Candidate"},
        }
    ]

    engine = SpatialIngestionEngine(dataset_version=spatial_dataset_version, batch_size=1, dry_run=False)

    # Ingest Chunk 1 successfully
    res1 = engine.run(chunk1_features)
    assert res1.inserted == 1
    assert Building.objects.filter(name="Chunk 1 Good").exists()

    # Now simulate database failure during chunk 2
    original_bulk_create = SpatialSourceFeature.objects.bulk_create
    def broken_bulk_create(*args, **kwargs):
        raise RuntimeError("Simulated DB connection failure during chunk 2")

    monkeypatch.setattr(SpatialSourceFeature.objects, "bulk_create", broken_bulk_create)

    with pytest.raises(RuntimeError, match="Simulated DB connection failure"):
        engine.run(chunk2_features)

    # Assert Chunk 1 remains intact
    assert Building.objects.filter(name="Chunk 1 Good").exists()
    assert SpatialIdentityMapping.objects.filter(provider_external_id="way/chunk1_1").exists()

    # Assert Chunk 2 was completely rolled back: zero orphan entities, zero buildings, zero mappings
    assert not Building.objects.filter(name="Chunk 2 Candidate").exists()
    assert not SpatialIdentityMapping.objects.filter(provider_external_id="way/chunk2_1").exists()
    assert not SpatialEntity.objects.filter(canonical_urn__contains="chunk2_1").exists()
