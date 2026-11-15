"""Integration tests for 3D Digital Twin GLB packing, tileset generation, and REST APIs."""

from __future__ import annotations

import json
import struct
import uuid
from pathlib import Path

import pytest
from django.contrib.gis.geos import MultiPolygon, Polygon
from django.test import Client
from django.urls import reverse

from apps.organizations.models import Organization
from apps.spatial.domain import (
    BuildingClassification,
    DatasetCategory,
    DatasetVersionStatus,
    DigitalTwinFormat,
    DigitalTwinStatus,
    HeightSource,
    LicenseType,
    SRID_WGS84,
)
from apps.spatial.models import (
    Building,
    DatasetRegistry,
    DatasetVersion,
    DigitalTwinAsset,
    DigitalTwinAttributeProvenance,
    DigitalTwinGeneration,
)
from apps.spatial.services.tileset_generator import (
    generate_digital_twin_tileset,
    pack_mesh_to_glb,
)
from apps.spatial.services.twin_reconstruction import (
    MeshData,
    extrude_building,
)


@pytest.fixture
def spatial_organization(db):
    return Organization.objects.create(
        id=uuid.uuid4(),
        legal_name="Mombasa Twin Testing Corp",
        display_name="Mombasa Twin Corp",
        organization_type="developer",
    )


@pytest.fixture
def spatial_dataset(db):
    reg = DatasetRegistry.objects.create(
        dataset_urn="urn:glitters:dataset:test:twin",
        name="Mombasa Test Twin Dataset",
        category=DatasetCategory.BASEMAP,
    )
    extent = Polygon.from_bbox((39.65, -4.08, 39.70, -4.04))
    ver = DatasetVersion.objects.create(
        dataset=reg,
        version_tag="2026.11-test",
        source_checksum_sha256="a" * 64,
        pipeline_git_commit="b" * 40,
        license_type=LicenseType.ODBL,
        attribution_statement="Test Twin",
        spatial_extent=extent,
        status=DatasetVersionStatus.PUBLISHED,
    )
    return ver


@pytest.fixture
def sample_buildings(db, spatial_dataset):
    b1_id = uuid.uuid4()
    poly1 = Polygon(((39.660, -4.060), (39.661, -4.060), (39.661, -4.059), (39.660, -4.059), (39.660, -4.060)))
    b1 = Building.objects.create(
        id=b1_id,
        canonical_urn=f"urn:glitters:spatial:building:{b1_id}",
        name="Kilindini Commercial Tower",
        classification=BuildingClassification.COMMERCIAL,
        height_m=28.5,
        levels_above_ground=8,
        is_human_verified=True,
        dataset_version=spatial_dataset,
        geom=MultiPolygon(poly1),
    )

    b2_id = uuid.uuid4()
    poly2 = Polygon(((39.662, -4.062), (39.663, -4.062), (39.663, -4.061), (39.662, -4.061), (39.662, -4.062)))
    b2 = Building.objects.create(
        id=b2_id,
        canonical_urn=f"urn:glitters:spatial:building:{b2_id}",
        name="Old Town Residential Villa",
        classification=BuildingClassification.RESIDENTIAL,
        height_m=None,
        levels_above_ground=2,
        is_human_verified=False,
        dataset_version=spatial_dataset,
        geom=MultiPolygon(poly2),
    )

    b3_id = uuid.uuid4()
    poly3 = Polygon(((39.664, -4.064), (39.665, -4.064), (39.665, -4.063), (39.664, -4.063), (39.664, -4.064)))
    b3 = Building.objects.create(
        id=b3_id,
        canonical_urn=f"urn:glitters:spatial:building:{b3_id}",
        name="Unclassified Shed",
        classification=BuildingClassification.OTHER,
        height_m=None,
        levels_above_ground=None,
        is_human_verified=False,
        dataset_version=spatial_dataset,
        geom=MultiPolygon(poly3),
    )

    return [b1, b2, b3]


@pytest.mark.django_db
def test_glb_packing_binary_specification(sample_buildings):
    mesh = MeshData()
    origin_lon, origin_lat = 39.66, -4.06
    metadata = []

    for idx, b in enumerate(sample_buildings):
        h, src, placeholder, _ = extrude_building(b, origin_lon, origin_lat, float(idx), mesh)
        metadata.append({
            "canonical_urn": b.canonical_urn,
            "id": b.canonical_urn,
            "name": b.name,
            "classification": b.classification,
            "height_m": h,
            "height_source": src,
            "is_placeholder": placeholder,
        })

    glb_bytes = pack_mesh_to_glb(mesh, metadata)
    assert len(glb_bytes) >= 20

    # Header check
    magic, version, length = struct.unpack("<III", glb_bytes[:12])
    assert magic == 0x46546C67  # 'glTF'
    assert version == 2
    assert length == len(glb_bytes)

    # Chunk 0 check (JSON)
    json_len, chunk0_type = struct.unpack("<II", glb_bytes[12:20])
    assert chunk0_type == 0x4E4F534A  # 'JSON'
    json_data = json.loads(glb_bytes[20:20 + json_len].decode("utf-8").strip())
    assert json_data["asset"]["version"] == "2.0"
    assert "batchTable" in json_data["extras"]
    assert len(json_data["extras"]["batchTable"]["canonical_urn"]) == 3

    # Chunk 1 check (BIN)
    chunk1_offset = 20 + json_len
    bin_len, chunk1_type = struct.unpack("<II", glb_bytes[chunk1_offset:chunk1_offset + 8])
    assert chunk1_type == 0x004E4942  # 'BIN\0'
    assert len(glb_bytes) == chunk1_offset + 8 + bin_len


@pytest.mark.django_db
def test_generate_digital_twin_tileset(sample_buildings, tmp_path):
    gen = generate_digital_twin_tileset(sample_buildings, output_base_dir=tmp_path)
    assert gen.status == DigitalTwinStatus.COMPLETED
    assert gen.building_count == 3
    assert Path(gen.tileset_path).exists()

    # Verify database persistence
    assets = DigitalTwinAsset.objects.filter(generation=gen)
    assert assets.count() == 3

    tower_asset = assets.get(entity=sample_buildings[0])
    assert tower_asset.height_m == 28.5
    assert tower_asset.height_source == HeightSource.SURVEY_VERIFIED_HEIGHT
    assert tower_asset.is_placeholder is False

    villa_asset = assets.get(entity=sample_buildings[1])
    assert villa_asset.height_m == 6.0  # 2 levels * 3.0m
    assert villa_asset.height_source == HeightSource.LEVEL_DERIVED_HEIGHT
    assert villa_asset.is_placeholder is False

    shed_asset = assets.get(entity=sample_buildings[2])
    assert shed_asset.height_source == HeightSource.UNKNOWN_HEIGHT
    assert shed_asset.is_placeholder is True

    # Provenance records check
    prov = DigitalTwinAttributeProvenance.objects.filter(asset=tower_asset)
    assert prov.count() >= 2
    height_prov = prov.get(attribute_name="height_m")
    assert height_prov.source == HeightSource.SURVEY_VERIFIED_HEIGHT


@pytest.mark.django_db
def test_digital_twin_api_endpoints(sample_buildings, tmp_path):
    gen = generate_digital_twin_tileset(sample_buildings, output_base_dir=tmp_path)
    client = Client()

    # 1. List digital twins
    res = client.get("/api/v1/spatial/digital-twins/")
    assert res.status_code == 200
    assert res.json()["count"] >= 1

    # 2. Get digital twin detail
    res = client.get(f"/api/v1/spatial/digital-twins/{gen.id}/")
    assert res.status_code == 200
    assert res.json()["building_count"] == 3

    # 3. Get tileset.json
    res = client.get(f"/api/v1/spatial/digital-twins/{gen.id}/tileset/")
    assert res.status_code == 200
    assert res.headers["Content-Type"] == "application/json"
    tileset = res.json()
    assert tileset["asset"]["version"] == "1.1"
    assert "boundingVolume" in tileset["root"]

    # 4. Get binary tile
    res = client.get(f"/api/v1/spatial/digital-twins/{gen.id}/tiles/tile_0.glb")
    assert res.status_code == 200
    tile_content = b"".join(res.streaming_content)
    assert tile_content[:4] == b"glTF"

    # 5. Get active tileset
    res = client.get("/api/v1/spatial/digital-twins/active-tileset/")
    assert res.status_code == 200
    active_tileset = res.json()
    assert "/tiles/tile_0.glb" in active_tileset["root"]["content"]["uri"]

    # 6. Building digital-twin action by URN
    urn = sample_buildings[0].canonical_urn
    res = client.get(f"/api/v1/spatial/buildings/{urn}/digital-twin/")
    assert res.status_code == 200
    twin_data = res.json()
    assert twin_data["canonical_urn"] == urn
    assert twin_data["height_source"] == HeightSource.SURVEY_VERIFIED_HEIGHT
    assert len(twin_data["provenance_records"]) >= 2
