"""Integration tests for Temporal Spatial Engine (WP-4.1.2, WP-4.1.3, WP-4.1.4)."""

from datetime import datetime, timedelta
import pytest
from django.contrib.gis.geos import MultiPolygon, Polygon
from django.core.exceptions import ValidationError
from django.utils import timezone
from rest_framework.test import APIClient

from apps.organizations.models import Organization
from apps.spatial.domain import (
    BuildingClassification,
    DatasetCategory,
    DatasetVersionStatus,
    LicenseType,
    UpdateFrequency,
)
from apps.spatial.models import (
    Building,
    DatasetRegistry,
    DatasetVersion,
    EntityLineage,
    SpatialEntity,
)
from apps.spatial.services.temporal import (
    get_as_of,
    get_as_was,
    get_current_record,
    parse_iso_datetime,
    supersede_record,
    validate_temporal_transition,
)


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def temporal_dataset(db):
    extent = Polygon.from_bbox((39.5, -4.2, 39.8, -3.9))
    registry = DatasetRegistry.objects.create(
        dataset_urn="urn:glitters:dataset:test:temporal",
        name="Temporal Test Dataset",
        category=DatasetCategory.BUILDINGS,
        update_frequency=UpdateFrequency.STATIC,
    )
    version = DatasetVersion.objects.create(
        dataset=registry,
        version_tag="2026.1",
        source_checksum_sha256="checksum-temporal-123",
        license_type=LicenseType.ODBL,
        status=DatasetVersionStatus.PUBLISHED,
        spatial_extent=extent,
        feature_count=10,
    )
    return version


@pytest.fixture
def temporal_entities(temporal_dataset):
    poly = Polygon(((39.664, -4.062), (39.665, -4.062), (39.665, -4.063), (39.664, -4.063), (39.664, -4.062)), srid=4326)
    multi_poly = MultiPolygon([poly], srid=4326)

    # Active building
    b_active = Building.objects.create(
        dataset_version=temporal_dataset,
        name="Active Structure",
        classification=BuildingClassification.COMMERCIAL,
        height_m=20.0,
        geom=multi_poly,
    )

    # Historical building that was superseded
    b_old = Building.objects.create(
        dataset_version=temporal_dataset,
        name="Old Bungalow",
        classification=BuildingClassification.RESIDENTIAL,
        height_m=5.0,
        geom=multi_poly,
    )
    b_new = Building.objects.create(
        dataset_version=temporal_dataset,
        name="Modern Apartment Tower",
        classification=BuildingClassification.RESIDENTIAL,
        height_m=35.0,
        geom=multi_poly,
    )

    # Mark b_old created 3 days ago, and superseded by b_new 1 day ago
    created_time = timezone.now() - timedelta(days=3)
    supersede_time = timezone.now() - timedelta(days=1)

    SpatialEntity.objects.filter(id=b_old.id).update(
        created_at=created_time,
        superseded_at=supersede_time,
        valid_to=supersede_time,
        superseded_by=b_new,
    )
    b_old.refresh_from_db()

    return b_active, b_old, b_new


# -----------------------------------------------------------------------------
# WP-4.1.2: Temporal Service Layer Tests
# -----------------------------------------------------------------------------

@pytest.mark.django_db
def test_temporal_datetime_parsing():
    parsed = parse_iso_datetime("2026-06-15T10:30:00Z")
    assert isinstance(parsed, datetime)
    assert parsed.year == 2026
    assert parsed.month == 6
    assert parsed.day == 15
    assert not timezone.is_naive(parsed)

    with pytest.raises(ValidationError, match="Invalid ISO-8601"):
        parse_iso_datetime("not-a-valid-date")


@pytest.mark.django_db
def test_get_current_record_filters_superseded(temporal_entities):
    b_active, b_old, b_new = temporal_entities
    qs = get_current_record(Building.objects.all())
    ids = list(qs.values_list("id", flat=True))

    assert b_active.id in ids
    assert b_new.id in ids
    assert b_old.id not in ids  # Superseded record must be excluded


@pytest.mark.django_db
def test_supersede_record_atomic_operation(temporal_dataset):
    poly = Polygon(((39.66, -4.06), (39.67, -4.06), (39.67, -4.05), (39.66, -4.05), (39.66, -4.06)), srid=4326)
    m_poly = MultiPolygon([poly], srid=4326)

    orig = Building.objects.create(
        dataset_version=temporal_dataset,
        name="Single Storey House",
        geom=m_poly,
    )
    succ = Building.objects.create(
        dataset_version=temporal_dataset,
        name="Multi Storey Complex",
        geom=m_poly,
    )

    t_event = timezone.now()
    updated_orig, _ = supersede_record(orig, succ, effective_time=t_event)

    assert updated_orig.superseded_at == t_event
    assert updated_orig.superseded_by_id == succ.id
    assert updated_orig.valid_to == t_event

    # Re-superseding should raise ValidationError
    with pytest.raises(ValidationError, match="already superseded"):
        supersede_record(updated_orig, succ)


@pytest.mark.django_db
def test_validate_temporal_transition():
    now = timezone.now()
    entity = SpatialEntity(valid_from=now)

    # Valid transition: valid_to > valid_from
    validate_temporal_transition(entity, new_valid_from=now + timedelta(days=1), new_valid_to=now + timedelta(days=10))

    # Invalid transition: valid_to <= valid_from
    with pytest.raises(ValidationError, match="valid_to .* must be strictly greater than valid_from"):
        validate_temporal_transition(entity, new_valid_from=now + timedelta(days=5), new_valid_to=now + timedelta(days=2))


# -----------------------------------------------------------------------------
# WP-4.1.3: Time-Travel API Filter Tests
# -----------------------------------------------------------------------------

@pytest.mark.django_db
def test_building_api_default_excludes_superseded(api_client, temporal_entities):
    b_active, b_old, b_new = temporal_entities
    resp = api_client.get("/api/v1/spatial/buildings/")
    assert resp.status_code == 200
    data = resp.json()
    results = data["results"] if "results" in data else data
    ids = [r["id"] for r in results]

    assert str(b_active.id) in ids
    assert str(b_new.id) in ids
    assert str(b_old.id) not in ids  # Superseded record must not appear by default


@pytest.mark.django_db
def test_building_api_as_was_retrieves_historical_record(api_client, temporal_entities):
    b_active, b_old, b_new = temporal_entities

    # When querying "as_was" 2 days ago (before b_old was superseded):
    historical_time = (timezone.now() - timedelta(days=2)).isoformat()
    resp = api_client.get(f"/api/v1/spatial/buildings/?as_was={historical_time}")
    assert resp.status_code == 200
    data = resp.json()
    results = data["results"] if "results" in data else data
    ids = [r["id"] for r in results]

    # At that transaction time, b_old was active and not superseded
    assert str(b_old.id) in ids


@pytest.mark.django_db
def test_building_api_invalid_temporal_parameters(api_client):
    resp = api_client.get("/api/v1/spatial/buildings/?as_of=invalid-timestamp")
    assert resp.status_code == 400
    assert "as_of" in str(resp.json())

    resp = api_client.get("/api/v1/spatial/buildings/?as_was=invalid-timestamp")
    assert resp.status_code == 400
    assert "as_was" in str(resp.json())


@pytest.mark.django_db
def test_vector_tiles_with_temporal_filters(api_client, temporal_entities):
    # Vector tile at zoom 16 in Mombasa
    resp = api_client.get("/api/v1/spatial/buildings/tiles/16/19245/16120/")
    assert resp.status_code == 200
    assert resp["Content-Type"] == "application/vnd.mapbox-vector-tile"

    # Tile with as_was parameter
    time_param = timezone.now().isoformat()
    resp_temporal = api_client.get(f"/api/v1/spatial/buildings/tiles/16/19245/16120/?as_was={time_param}")
    assert resp_temporal.status_code == 200

    # Tile with invalid parameter returns 400
    resp_invalid = api_client.get("/api/v1/spatial/buildings/tiles/16/19245/16120/?as_was=invalid")
    assert resp_invalid.status_code == 400


# -----------------------------------------------------------------------------
# WP-4.1.4: Lineage Traversal API Tests
# -----------------------------------------------------------------------------

@pytest.mark.django_db
def test_entity_lineage_recursive_traversal(api_client):
    urn_root = "urn:glitters:spatial:land_parcel:018f3a9e-0000-7000-8000-000000000001"
    urn_daughter1 = "urn:glitters:spatial:land_parcel:018f3a9e-0000-7000-8000-000000000002"
    urn_daughter2 = "urn:glitters:spatial:land_parcel:018f3a9e-0000-7000-8000-000000000003"
    urn_granddaughter = "urn:glitters:spatial:land_parcel:018f3a9e-0000-7000-8000-000000000004"

    EntityLineage.objects.create(
        predecessor_urn=urn_root,
        successor_urn=urn_daughter1,
        lineage_type="SUBDIVISION",
        effective_date="2025-01-01",
        authorization_reference="FR-1001",
    )
    EntityLineage.objects.create(
        predecessor_urn=urn_root,
        successor_urn=urn_daughter2,
        lineage_type="SUBDIVISION",
        effective_date="2025-01-01",
        authorization_reference="FR-1001",
    )
    EntityLineage.objects.create(
        predecessor_urn=urn_daughter1,
        successor_urn=urn_granddaughter,
        lineage_type="SUBDIVISION",
        effective_date="2026-01-01",
        authorization_reference="FR-1002",
    )

    # Query descendants of root
    resp = api_client.get(f"/api/v1/spatial/entities/{urn_root}/lineage/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["entity_urn"] == urn_root
    assert data["successors_count"] == 3
    succ_urns = [s["successor_urn"] for s in data["successors"]]
    assert urn_daughter1 in succ_urns
    assert urn_daughter2 in succ_urns
    assert urn_granddaughter in succ_urns
    assert data["cycle_detected"] is False

    # Query ancestors of granddaughter
    resp2 = api_client.get(f"/api/v1/spatial/lineage/tree/{urn_granddaughter}/")
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["predecessors_count"] == 2
    pred_urns = [p["predecessor_urn"] for p in data2["predecessors"]]
    assert urn_daughter1 in pred_urns
    assert urn_root in pred_urns


@pytest.mark.django_db
def test_entity_lineage_cycle_protection(api_client):
    urn_a = "urn:glitters:spatial:land_parcel:018f3a9e-0000-7000-8000-000000000010"
    urn_b = "urn:glitters:spatial:land_parcel:018f3a9e-0000-7000-8000-000000000020"

    # Insert cycle directly via bulk_create to bypass model-level full_clean()
    EntityLineage.objects.bulk_create([
        EntityLineage(
            predecessor_urn=urn_a,
            successor_urn=urn_b,
            lineage_type="SUBDIVISION",
            effective_date="2026-01-01",
        ),
        EntityLineage(
            predecessor_urn=urn_b,
            successor_urn=urn_a,
            lineage_type="SUBDIVISION",
            effective_date="2026-01-01",
        ),
    ])

    resp = api_client.get(f"/api/v1/spatial/entities/{urn_a}/lineage/")
    assert resp.status_code == 200
    data = resp.json()
    # Cycle must be detected and halted without infinite loop
    assert data["cycle_detected"] is True


@pytest.mark.django_db
def test_cross_tenant_lineage_masking(api_client, temporal_dataset):
    org_private = Organization.objects.create(
        legal_name="Private Holdings",
        display_name="Private Holdings",
        slug="private-holdings",
        organization_type="developer",
    )
    urn_public = "urn:glitters:spatial:land_parcel:018f3a9e-0000-7000-8000-000000000030"
    urn_private = "urn:glitters:spatial:building:018f3a9e-0000-7000-8000-000000000040"

    # Create actual private entity in database
    poly = Polygon(((39.664, -4.062), (39.665, -4.062), (39.665, -4.063), (39.664, -4.063), (39.664, -4.062)), srid=4326)
    Building.objects.create(
        dataset_version=temporal_dataset,
        organization=org_private,
        canonical_urn=urn_private,
        name="Private Sovereign Asset",
        geom=MultiPolygon([poly], srid=4326),
    )

    EntityLineage.objects.create(
        predecessor_urn=urn_public,
        successor_urn=urn_private,
        lineage_type="SUBDIVISION",
        effective_date="2026-01-01",
    )

    # Unauthenticated / non-tenant client requests lineage
    resp = api_client.get(f"/api/v1/spatial/entities/{urn_public}/lineage/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["successors_count"] == 1
    # Private successor URN must be masked for non-tenant
    assert data["successors"][0]["successor_urn"] == "urn:glitters:restricted:tenant_entity"
    assert data["successors"][0]["accessible"] is False
