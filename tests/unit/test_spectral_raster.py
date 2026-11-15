"""Unit tests for remote sensing spectral indices, terrain analysis, and COG file generation."""

import datetime
from pathlib import Path
import numpy as np
import pytest
from django.contrib.gis.geos import Polygon
from PIL import Image

from apps.spatial.domain import ImageryDatasetType, RasterJobStatus
from apps.spatial.models import ImageryCollection, ImageryItem, RasterAsset
from apps.spatial.services.spectral_service import (
    _ensure_raster_dir,
    run_spectral_index_job,
    run_terrain_analysis_job,
)
from glitters_geospatial_engine.raster.pipeline import (
    AffineGeotransform,
    GeoRasterTile,
    RasterProcessingPipeline,
)


@pytest.mark.django_db
def test_spectral_indices_computation(tmp_path):
    """Validates NDVI and SAVI execution with SpectralIndicesEngine and GeoTIFF output."""
    poly = Polygon(((39.0, -4.6), (39.0, -3.6), (40.0, -3.6), (40.0, -4.6), (39.0, -4.6)), srid=4326)
    coll = ImageryCollection.objects.create(
        collection_id="unit-test-s2",
        title="Unit Test S2",
        dataset_type=ImageryDatasetType.OPTICAL_MULTISPECTRAL,
    )
    item = ImageryItem.objects.create(
        collection=coll,
        item_id="UNIT_TEST_S2_ITEM",
        canonical_urn="urn:glitters:imagery:test:s2_002",
        acquisition_datetime=datetime.datetime(2026, 2, 13, 10, 0, tzinfo=datetime.timezone.utc),
        footprint=poly,
        native_crs="EPSG:32737",
    )

    # Create dummy Red and NIR raster files
    out_dir = _ensure_raster_dir(item.item_id)
    red_path = out_dir / "red.tif"
    nir_path = out_dir / "nir.tif"

    red_data = np.full((20, 20), 0.15, dtype=np.float32)
    nir_data = np.full((20, 20), 0.60, dtype=np.float32)
    transform = AffineGeotransform(a=10.0, b=0.0, c=500000.0, d=0.0, e=-10.0, f=9500000.0)

    tile_red = GeoRasterTile(
        data=red_data[np.newaxis, :, :],
        transform=transform,
        epsg=32737,
        band_names=["RED"],
        acquisition_date="2026-02-13",
    )
    tile_nir = GeoRasterTile(
        data=nir_data[np.newaxis, :, :],
        transform=transform,
        epsg=32737,
        band_names=["NIR"],
        acquisition_date="2026-02-13",
    )
    RasterProcessingPipeline.write_geotiff_cog_file(tile_red, str(red_path))
    RasterProcessingPipeline.write_geotiff_cog_file(tile_nir, str(nir_path))

    RasterAsset.objects.create(
        item=item,
        asset_key="red",
        storage_path=str(red_path),
        file_size_bytes=red_path.stat().st_size,
        crs="EPSG:32737",
        width_px=20,
        height_px=20,
    )
    RasterAsset.objects.create(
        item=item,
        asset_key="nir",
        storage_path=str(nir_path),
        file_size_bytes=nir_path.stat().st_size,
        crs="EPSG:32737",
        width_px=20,
        height_px=20,
    )

    # Run NDVI job
    job = run_spectral_index_job(item, "ndvi")
    assert job.status == RasterJobStatus.COMPLETED
    assert job.output_asset is not None
    assert job.output_asset.asset_key == "ndvi"
    assert Path(job.output_asset.storage_path).exists()
    assert 0.55 < job.output_asset.min_val < 0.65

    # Run SAVI job
    savi_job = run_spectral_index_job(item, "savi", parameters={"L": 0.5})
    assert savi_job.status == RasterJobStatus.COMPLETED
    assert savi_job.output_asset is not None


@pytest.mark.django_db
def test_terrain_analysis_computation():
    """Validates Hillshade and Slope calculation with TerrainAndHydrologyEngine."""
    poly = Polygon(((39.0, -4.6), (39.0, -3.6), (40.0, -3.6), (40.0, -4.6), (39.0, -4.6)), srid=4326)
    coll = ImageryCollection.objects.create(
        collection_id="unit-test-dem",
        title="Unit Test DEM",
        dataset_type=ImageryDatasetType.DIGITAL_ELEVATION_MODEL,
    )
    item = ImageryItem.objects.create(
        collection=coll,
        item_id="UNIT_TEST_DEM_ITEM",
        canonical_urn="urn:glitters:imagery:test:dem_002",
        acquisition_datetime=datetime.datetime(2021, 1, 1, 0, 0, tzinfo=datetime.timezone.utc),
        footprint=poly,
        native_crs="EPSG:32737",
    )

    out_dir = _ensure_raster_dir(item.item_id)
    dem_path = out_dir / "dem.tif"

    dem_data = np.linspace(5.0, 50.0, 400).reshape(20, 20).astype(np.float32)
    transform = AffineGeotransform(a=30.0, b=0.0, c=500000.0, d=0.0, e=-30.0, f=9500000.0)
    tile_dem = GeoRasterTile(
        data=dem_data[np.newaxis, :, :],
        transform=transform,
        epsg=32737,
        band_names=["DEM"],
        acquisition_date="2021-01-01",
    )
    RasterProcessingPipeline.write_geotiff_cog_file(tile_dem, str(dem_path))

    RasterAsset.objects.create(
        item=item,
        asset_key="dem",
        storage_path=str(dem_path),
        file_size_bytes=dem_path.stat().st_size,
        crs="EPSG:32737",
        width_px=20,
        height_px=20,
    )

    job = run_terrain_analysis_job(item)
    assert job.status == RasterJobStatus.COMPLETED
    assert job.output_asset is not None
    assert job.output_asset.asset_key == "hillshade"
    assert RasterAsset.objects.filter(item=item, asset_key="slope").exists()
