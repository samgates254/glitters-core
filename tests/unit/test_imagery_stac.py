"""Unit tests for STAC catalog serialization, spatial search, and raster tile calculations."""

import datetime
import pytest
from django.contrib.gis.geos import Polygon

from apps.spatial.domain import ImageryDatasetType, RasterAssetType
from apps.spatial.models import ImageryCollection, ImageryItem, RasterAsset
from apps.spatial.services.raster_tile_server import (
    apply_hillshade_colormap,
    apply_ndvi_colormap,
    apply_ndwi_colormap,
    bbox_intersects,
    tile_to_bbox_wgs84,
)
from apps.spatial.services.stac import (
    search_imagery_items,
    serialize_stac_collection,
    serialize_stac_item,
)


@pytest.mark.django_db
def test_tile_math_and_intersection():
    """Validates analytical Web Mercator tile math and bounding box intersections."""
    # Mombasa tile at zoom 12
    bbox = tile_to_bbox_wgs84(12, 2499, 2094)
    min_lon, min_lat, max_lon, max_lat = bbox

    # Must contain Mombasa Island (approx 39.668, -4.061)
    assert min_lon <= 39.668 <= max_lon
    assert min_lat <= -4.061 <= max_lat

    mombasa_extent = (38.9998, -4.6118, 39.9896, -3.6179)
    assert bbox_intersects(bbox, mombasa_extent) is True

    # London tile should not intersect Mombasa
    london_bbox = tile_to_bbox_wgs84(12, 2048, 1360)
    assert bbox_intersects(london_bbox, mombasa_extent) is False


@pytest.mark.django_db
def test_colormap_generation():
    """Validates continuous colormaps for NDVI, NDWI, and Hillshade."""
    import numpy as np

    # NDVI test array from -0.5 to 1.0
    arr = np.linspace(-0.5, 1.0, 100).reshape(10, 10).astype(np.float32)
    rgba = apply_ndvi_colormap(arr)
    assert rgba.shape == (10, 10, 4)
    assert rgba.dtype == np.uint8
    # High vegetation should have high green channel
    assert rgba[9, 9, 1] > 100

    # NDWI test
    rgba_water = apply_ndwi_colormap(arr)
    assert rgba_water.shape == (10, 10, 4)
    assert rgba_water.dtype == np.uint8

    # Hillshade test
    hs_arr = np.linspace(0, 255, 100).reshape(10, 10).astype(np.float32)
    rgba_hs = apply_hillshade_colormap(hs_arr)
    assert rgba_hs.shape == (10, 10, 4)
    assert rgba_hs[0, 0, 0] == 0
    assert rgba_hs[9, 9, 0] == 255


@pytest.mark.django_db
def test_stac_collection_and_item_serialization():
    """Validates STAC 1.0.0 Collection and Item serialization."""
    poly = Polygon(((39.0, -4.6), (39.0, -3.6), (40.0, -3.6), (40.0, -4.6), (39.0, -4.6)), srid=4326)
    collection = ImageryCollection.objects.create(
        collection_id="test-sentinel-2",
        title="Test Sentinel-2 Collection",
        description="Testing collection",
        provider="ESA",
        dataset_type=ImageryDatasetType.OPTICAL_MULTISPECTRAL,
        spatial_extent=poly,
        time_start=datetime.datetime(2026, 1, 1, 0, 0, tzinfo=datetime.timezone.utc),
        native_crs="EPSG:32737",
        gsd_m=10.0,
    )

    stac_coll = serialize_stac_collection(collection, base_url="http://testserver")
    assert stac_coll["stac_version"] == "1.0.0"
    assert stac_coll["id"] == "test-sentinel-2"
    assert stac_coll["type"] == "Collection"
    assert "extent" in stac_coll

    item = ImageryItem.objects.create(
        collection=collection,
        item_id="TEST_S2_ITEM_001",
        canonical_urn="urn:glitters:imagery:test:001",
        acquisition_datetime=datetime.datetime(2026, 2, 13, 10, 0, tzinfo=datetime.timezone.utc),
        footprint=poly,
        native_crs="EPSG:32737",
        cloud_coverage_percentage=1.5,
    )

    asset = RasterAsset.objects.create(
        item=item,
        asset_key="visual",
        asset_type=RasterAssetType.COG,
        storage_path="/tmp/fake_visual.tif",
        file_size_bytes=1024,
        crs="EPSG:32737",
        width_px=100,
        height_px=100,
    )

    stac_item = serialize_stac_item(item, base_url="http://testserver")
    assert stac_item["stac_version"] == "1.0.0"
    assert stac_item["type"] == "Feature"
    assert stac_item["id"] == "TEST_S2_ITEM_001"
    assert stac_item["properties"]["eo:cloud_cover"] == 1.5
    assert "visual" in stac_item["assets"]
    assert "glitters:tile_url" in stac_item["assets"]["visual"]

    # Test spatial search
    res = search_imagery_items(bbox=[39.1, -4.5, 39.9, -3.7], max_cloud_cover=2.0)
    assert item in res

    # Cloud cover filter exclusion
    res_cloud = search_imagery_items(max_cloud_cover=1.0)
    assert item not in res_cloud
