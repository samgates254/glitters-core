"""Integration tests for spatial REST and GeoJSON API endpoints."""

import pytest
from django.contrib.gis.geos import MultiPolygon, Polygon
from rest_framework.test import APIClient

from apps.spatial.domain import (
    SRID_WGS84,
    BuildingClassification,
    DatasetCategory,
    LicenseType,
    UpdateFrequency,
)
from apps.spatial.models import (
    Building,
    DatasetRegistry,
    DatasetVersion,
    EntityLineage,
    LandParcel,
    SpatialIdentityMapping,
)


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def spatial_fixtures(db):
    extent = Polygon(
        (
            (39.5, -4.2),
            (39.8, -4.2),
            (39.8, -3.9),
            (39.5, -3.9),
            (39.5, -4.2),
        ),
        srid=SRID_WGS84,
    )
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
    multi_poly = MultiPolygon([poly], srid=SRID_WGS84)

    registry = DatasetRegistry.objects.create(
        dataset_urn="urn:glitters:dataset:ke:msa:test-api-dataset",
        name="API Test Dataset",
        category=DatasetCategory.BUILDINGS,
        update_frequency=UpdateFrequency.STATIC,
    )
    version = DatasetVersion.objects.create(
        dataset=registry,
        version_tag="2026.1",
        source_checksum_sha256="checksum12345",
        pipeline_git_commit="commit12345",
        license_type=LicenseType.ODBL,
        attribution_statement="OSM",
        spatial_extent=extent,
        feature_count=1,
    )
    building = Building.objects.create(
        dataset_version=version,
        name="Customs House",
        classification=BuildingClassification.CIVIC,
        height_m=18.0,
        geom=multi_poly,
    )
    SpatialIdentityMapping.objects.create(
        entity=building,
        provider_name="OSM",
        provider_external_id="way/1151899424",
    )
    parcel = LandParcel.objects.create(
        dataset_version=version,
        cadastral_identifier="MOMBASA/BLOCK_X/1",
        tenure_type="FREEHOLD",
        geom=multi_poly,
    )
    return registry, version, building, parcel


@pytest.mark.django_db
def test_datasets_api(api_client, spatial_fixtures):
    registry, _, _, _ = spatial_fixtures
    response = api_client.get("/api/v1/spatial/datasets/")
    assert response.status_code == 200
    data = response.json()
    results = data["results"] if isinstance(data, dict) and "results" in data else data
    assert len(results) >= 1
    assert any(d["dataset_urn"] == registry.dataset_urn for d in results)


@pytest.mark.django_db
def test_buildings_api_with_geojson(api_client, spatial_fixtures):
    _, _, building, _ = spatial_fixtures
    response = api_client.get(f"/api/v1/spatial/buildings/{building.id}/")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Customs House"
    assert data["classification"] == "CIVIC"
    assert data["geojson"] is not None
    assert data["geojson"]["type"] == "MultiPolygon"
    assert len(data["external_mappings"]) == 1
    assert data["external_mappings"][0]["provider_name"] == "OSM"
    assert data["external_mappings"][0]["provider_external_id"] == "way/1151899424"


@pytest.mark.django_db
def test_external_id_lookup_endpoint(api_client, spatial_fixtures):
    _, _, building, _ = spatial_fixtures
    # Lookup by OSM way ID
    response = api_client.get(
        "/api/v1/spatial/entities/lookup/?provider=OSM&id=way/1151899424"
    )
    assert response.status_code == 200
    data = response.json()
    assert data["canonical_urn"] == building.canonical_urn

    # Non-existent ID returns 404
    missing_response = api_client.get(
        "/api/v1/spatial/entities/lookup/?provider=OSM&id=way/non-existent"
    )
    assert missing_response.status_code == 404
