"""Integration tests for Earth Observation STAC and Raster Intelligence REST APIs."""

import datetime
from pathlib import Path
import numpy as np
import pytest
from django.contrib.gis.geos import Polygon
from rest_framework.test import APIClient

from apps.spatial.domain import ImageryDatasetType, RasterAssetType
from apps.spatial.models import ImageryCollection, ImageryItem, RasterAsset
from apps.spatial.services.spectral_service import _ensure_raster_dir
from glitters_geospatial_engine.raster.pipeline import (
    AffineGeotransform,
    GeoRasterTile,
    RasterProcessingPipeline,
)


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def setup_imagery_test_data(db):
    """Sets up realistic STAC collection, item, and raster assets in the test DB."""
    poly = Polygon(((38.99, -4.61), (38.99, -3.61), (39.99, -3.61), (39.99, -4.61), (38.99, -4.61)), srid=4326)
    coll = ImageryCollection.objects.create(
        collection_id="sentinel-2-l2a",
        title="Sentinel-2 Level-2A Surface Reflectance",
        provider="ESA / Copernicus / AWS Open Data",
        dataset_type=ImageryDatasetType.OPTICAL_MULTISPECTRAL,
        spatial_extent=poly,
        time_start=datetime.datetime(2026, 1, 1, 0, 0, tzinfo=datetime.timezone.utc),
        native_crs="EPSG:32737",
        gsd_m=10.0,
    )
    item = ImageryItem.objects.create(
        collection=coll,
        item_id="TEST_S2B_FIXTURE_L2A",
        canonical_urn="urn:glitters:imagery:sentinel2:TEST_S2B_FIXTURE_L2A",
        acquisition_datetime=datetime.datetime(2026, 2, 13, 7, 39, 44, tzinfo=datetime.timezone.utc),
        footprint=poly,
        native_crs="EPSG:32737",
        cloud_coverage_percentage=2.9,
    )

    out_dir = _ensure_raster_dir(item.item_id)
    visual_path = out_dir / "visual.tif"
    red_path = out_dir / "red.tif"
    nir_path = out_dir / "nir.tif"

    data = np.full((50, 50), 0.5, dtype=np.float32)
    transform = AffineGeotransform(a=10.0, b=0.0, c=500000.0, d=0.0, e=-10.0, f=9500000.0)
    for p, name in [(visual_path, "VISUAL"), (red_path, "RED"), (nir_path, "NIR")]:
        tile = GeoRasterTile(
            data=data[np.newaxis, :, :],
            transform=transform,
            epsg=32737,
            band_names=[name],
            acquisition_date="2026-02-13",
        )
        RasterProcessingPipeline.write_geotiff_cog_file(tile, str(p))

    visual_asset = RasterAsset.objects.create(
        item=item,
        asset_key="visual",
        asset_type=RasterAssetType.COG,
        storage_path=str(visual_path),
        file_size_bytes=visual_path.stat().st_size,
        crs="EPSG:32737",
        width_px=50,
        height_px=50,
        bands=[{"name": "VISUAL", "data_type": "float32"}],
    )
    RasterAsset.objects.create(
        item=item,
        asset_key="red",
        asset_type=RasterAssetType.COG,
        storage_path=str(red_path),
        file_size_bytes=red_path.stat().st_size,
        crs="EPSG:32737",
        width_px=50,
        height_px=50,
        bands=[{"name": "RED", "data_type": "float32"}],
    )
    RasterAsset.objects.create(
        item=item,
        asset_key="nir",
        asset_type=RasterAssetType.COG,
        storage_path=str(nir_path),
        file_size_bytes=nir_path.stat().st_size,
        crs="EPSG:32737",
        width_px=50,
        height_px=50,
        bands=[{"name": "NIR", "data_type": "float32"}],
    )

    yield coll, item, visual_asset

    # Clean up test files
    import shutil
    if out_dir.exists():
        shutil.rmtree(out_dir, ignore_errors=True)


@pytest.mark.django_db
def test_imagery_collections_endpoint(api_client, setup_imagery_test_data):
    """GET /api/v1/spatial/imagery/collections/ returns registered collections."""
    resp = api_client.get("/api/v1/spatial/imagery/collections/")
    assert resp.status_code == 200
    data = resp.json()
    assert "results" in data
    assert data["count"] >= 1


@pytest.mark.django_db
def test_stac_collection_and_items(api_client, setup_imagery_test_data):
    """GET /api/v1/spatial/imagery/collections/{id}/stac/ and items."""
    coll, item, visual_asset = setup_imagery_test_data

    # STAC collection endpoint
    resp = api_client.get(f"/api/v1/spatial/imagery/collections/{coll.collection_id}/stac/")
    assert resp.status_code == 200
    stac_data = resp.json()
    assert stac_data["stac_version"] == "1.0.0"
    assert stac_data["id"] == coll.collection_id

    # Items FeatureCollection
    resp_items = api_client.get(f"/api/v1/spatial/imagery/collections/{coll.collection_id}/items/")
    assert resp_items.status_code == 200
    fc = resp_items.json()
    assert fc["type"] == "FeatureCollection"
    assert len(fc["features"]) >= 1


@pytest.mark.django_db
def test_stac_search_endpoint(api_client, setup_imagery_test_data):
    """GET /api/v1/spatial/imagery/search/ filters STAC items."""
    resp = api_client.get("/api/v1/spatial/imagery/search/?bbox=39.0,-4.6,40.0,-3.6&max_cloud_cover=10.0")
    assert resp.status_code == 200
    data = resp.json()
    assert data["type"] == "FeatureCollection"
    assert len(data["features"]) >= 1


@pytest.mark.django_db
def test_raster_tile_endpoint(api_client, setup_imagery_test_data):
    """GET /api/v1/spatial/rasters/by-key/visual/tiles/12/2499/2094.png serves valid PNG."""
    resp = api_client.get("/api/v1/spatial/rasters/by-key/visual/tiles/12/2499/2094.png")
    assert resp.status_code == 200
    assert resp["Content-Type"] == "image/png"
    assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.django_db
def test_raster_job_execution_api(api_client, setup_imagery_test_data):
    """POST /api/v1/spatial/rasters/jobs/ triggers spectral index job."""
    coll, item, visual_asset = setup_imagery_test_data

    resp = api_client.post(
        "/api/v1/spatial/rasters/jobs/",
        data={"item_id": item.item_id, "operation": "savi", "parameters": {"L": 0.5}},
        format="json",
    )
    assert resp.status_code in (200, 201)
    data = resp.json()
    assert data["status"] == "COMPLETED"
    assert data["output_asset_id"] is not None
