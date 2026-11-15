"""Unit tests for reusable spatial query services (WP-4.1.1)."""

import pytest
from django.contrib.gis.geos import MultiPolygon, Polygon
from django.core.exceptions import ValidationError

from apps.organizations.models import Organization
from apps.spatial.domain import BuildingClassification, DatasetCategory, DatasetVersionStatus, LicenseType, UpdateFrequency
from apps.spatial.models import Building, DatasetRegistry, DatasetVersion
from apps.spatial.services.queries import (
    get_building_by_urn,
    get_specialized_entity,
    query_buildings_in_bbox,
    query_buildings_intersecting_geometry,
    validate_bounding_box,
)


@pytest.fixture
def spatial_query_dataset(db):
    extent = Polygon.from_bbox((39.5, -4.2, 39.8, -3.9))
    registry = DatasetRegistry.objects.create(
        dataset_urn="urn:glitters:dataset:test:query-service",
        name="Query Service Test",
        category=DatasetCategory.BUILDINGS,
        update_frequency=UpdateFrequency.STATIC,
    )
    version = DatasetVersion.objects.create(
        dataset=registry,
        version_tag="2026.1",
        source_checksum_sha256="test-query-checksum",
        license_type=LicenseType.ODBL,
        status=DatasetVersionStatus.PUBLISHED,
        spatial_extent=extent,
        feature_count=2,
    )
    return version


@pytest.fixture
def test_buildings(spatial_query_dataset):
    poly1 = Polygon(((39.66, -4.06), (39.67, -4.06), (39.67, -4.05), (39.66, -4.05), (39.66, -4.06)), srid=4326)
    poly2 = Polygon(((39.70, -4.02), (39.71, -4.02), (39.71, -4.01), (39.70, -4.01), (39.70, -4.02)), srid=4326)

    b1 = Building.objects.create(
        dataset_version=spatial_query_dataset,
        name="Fort Jesus Museum",
        classification=BuildingClassification.CIVIC,
        height_m=12.5,
        geom=MultiPolygon([poly1], srid=4326),
    )
    b2 = Building.objects.create(
        dataset_version=spatial_query_dataset,
        name="Nyali Residence",
        classification=BuildingClassification.RESIDENTIAL,
        height_m=8.0,
        geom=MultiPolygon([poly2], srid=4326),
    )
    return b1, b2


@pytest.mark.django_db
def test_validate_bounding_box_valid():
    poly = validate_bounding_box(39.6, -4.1, 39.7, -4.0)
    assert isinstance(poly, Polygon)
    assert poly.srid == 4326
    assert poly.extent == (39.6, -4.1, 39.7, -4.0)


@pytest.mark.django_db
def test_validate_bounding_box_invalid_ranges():
    with pytest.raises(ValidationError, match="Longitude values"):
        validate_bounding_box(-190.0, -4.0, 39.0, -4.0)

    with pytest.raises(ValidationError, match="Latitude values"):
        validate_bounding_box(39.0, -95.0, 39.0, 4.0)

    with pytest.raises(ValidationError, match="min_lon .* cannot be greater than max_lon"):
        validate_bounding_box(40.0, -4.0, 39.0, -4.0)

    with pytest.raises(ValidationError, match="min_lat .* cannot be greater than max_lat"):
        validate_bounding_box(39.0, 2.0, 40.0, 1.0)


@pytest.mark.django_db
def test_query_buildings_in_bbox(test_buildings):
    b1, b2 = test_buildings
    # Envelope covering b1 only
    results, count = query_buildings_in_bbox((39.65, -4.07, 39.68, -4.04))
    assert count >= 1
    ids = [b.id for b in results]
    assert b1.id in ids
    assert b2.id not in ids


@pytest.mark.django_db
def test_query_buildings_intersecting_geometry(test_buildings):
    b1, _ = test_buildings
    search_poly = Polygon(((39.655, -4.065), (39.675, -4.065), (39.675, -4.045), (39.655, -4.045), (39.655, -4.065)), srid=4326)
    results, count = query_buildings_intersecting_geometry(search_poly)
    assert count >= 1
    assert any(b.id == b1.id for b in results)


@pytest.mark.django_db
def test_query_buildings_intersecting_invalid_geometry():
    # Bow-tie self-intersecting polygon
    invalid_wkt = "POLYGON((0 0, 0 2, 2 0, 2 2, 0 0))"
    with pytest.raises(ValidationError, match="topologically invalid"):
        query_buildings_intersecting_geometry(invalid_wkt)


@pytest.mark.django_db
def test_get_building_by_urn(test_buildings):
    b1, _ = test_buildings
    found = get_building_by_urn(b1.canonical_urn)
    assert found is not None
    assert found.id == b1.id
    assert found.name == "Fort Jesus Museum"


@pytest.mark.django_db
def test_get_specialized_entity(test_buildings):
    b1, _ = test_buildings
    specialized = get_specialized_entity(b1.canonical_urn)
    assert specialized is not None
    assert specialized["entity_type"] == "building"
    assert specialized["specialized_entity"].id == b1.id
    assert specialized["geometry"].geom_type == "MultiPolygon"


@pytest.mark.django_db
def test_tenant_isolation_in_queries(spatial_query_dataset):
    org_a = Organization.objects.create(
        legal_name="Tenant A", display_name="Tenant A", slug="tenant-a", organization_type="developer"
    )
    org_b = Organization.objects.create(
        legal_name="Tenant B", display_name="Tenant B", slug="tenant-b", organization_type="developer"
    )

    poly = Polygon(((39.66, -4.06), (39.67, -4.06), (39.67, -4.05), (39.66, -4.05), (39.66, -4.06)), srid=4326)
    b_private = Building.objects.create(
        dataset_version=spatial_query_dataset,
        organization=org_a,
        name="Private Warehouse A",
        geom=MultiPolygon([poly], srid=4326),
    )

    # Tenant B querying cannot find Tenant A's private building
    found_by_b = get_building_by_urn(b_private.canonical_urn, organization=org_b)
    assert found_by_b is None

    # Tenant A querying finds it
    found_by_a = get_building_by_urn(b_private.canonical_urn, organization=org_a)
    assert found_by_a is not None
    assert found_by_a.id == b_private.id
