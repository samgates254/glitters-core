"""High-Performance Raster Tile Server Engine.

Computes analytical Web Mercator (EPSG:3857) bounding boxes, intersects raster extents,
resamples pixel matrices, applies spectral colormaps (NDVI, NDWI, Hillshade, Hypsometric),
and serves 256x256 RGBA PNG tiles for GOD'S EYE workstation viewport streaming.
"""

from __future__ import annotations

import io
import math
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
from PIL import Image

from apps.spatial.models import RasterAsset


def tile_to_bbox_wgs84(z: int, x: int, y: int) -> Tuple[float, float, float, float]:
    """Analytical conversion of XYZ Web Mercator tile coordinates to WGS84 bounding box (min_lon, min_lat, max_lon, max_lat)."""
    n = 2.0 ** z
    lon_min = x / n * 360.0 - 180.0
    lon_max = (x + 1) / n * 360.0 - 180.0

    lat_max_rad = math.atan(math.sinh(math.pi * (1.0 - 2.0 * y / n)))
    lat_max = math.degrees(lat_max_rad)

    lat_min_rad = math.atan(math.sinh(math.pi * (1.0 - 2.0 * (y + 1) / n)))
    lat_min = math.degrees(lat_min_rad)

    return (lon_min, lat_min, lon_max, lat_max)


def bbox_intersects(
    b1: Tuple[float, float, float, float], b2: Tuple[float, float, float, float]
) -> bool:
    """Check if two bounding boxes (min_lon, min_lat, max_lon, max_lat) intersect."""
    return not (b1[2] < b2[0] or b1[0] > b2[2] or b1[3] < b2[1] or b1[1] > b2[3])


def apply_ndvi_colormap(values: np.ndarray, nodata_mask: Optional[np.ndarray] = None) -> np.ndarray:
    """Applies a continuous 4-stop spectral vegetation colormap (Water -> Soil -> Sparse -> Canopy)."""
    h, w = values.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)

    # Normalize [-0.2, 0.8] to [0.0, 1.0]
    norm = np.clip((values + 0.2) / 1.0, 0.0, 1.0)

    # Water/Non-vegetated (blue-grey / deep purple) -> Soil (tan) -> Sparse (yellow-green) -> Lush Canopy (deep green)
    # Stop 0 (norm=0.0): (44, 53, 64)
    # Stop 1 (norm=0.3): (210, 180, 140)
    # Stop 2 (norm=0.6): (144, 201, 75)
    # Stop 3 (norm=1.0): (19, 107, 34)

    # Segment 1: [0.0, 0.3]
    m1 = norm <= 0.3
    t1 = norm / 0.3
    rgba[m1, 0] = (44 + t1[m1] * (210 - 44)).astype(np.uint8)
    rgba[m1, 1] = (53 + t1[m1] * (180 - 53)).astype(np.uint8)
    rgba[m1, 2] = (64 + t1[m1] * (140 - 64)).astype(np.uint8)
    rgba[m1, 3] = 230

    # Segment 2: [0.3, 0.6]
    m2 = (norm > 0.3) & (norm <= 0.6)
    t2 = (norm - 0.3) / 0.3
    rgba[m2, 0] = (210 + t2[m2] * (144 - 210)).astype(np.uint8)
    rgba[m2, 1] = (180 + t2[m2] * (201 - 180)).astype(np.uint8)
    rgba[m2, 2] = (140 + t2[m2] * (75 - 140)).astype(np.uint8)
    rgba[m2, 3] = 240

    # Segment 3: [0.6, 1.0]
    m3 = norm > 0.6
    t3 = (norm - 0.6) / 0.4
    rgba[m3, 0] = (144 + t3[m3] * (19 - 144)).astype(np.uint8)
    rgba[m3, 1] = (201 + t3[m3] * (107 - 201)).astype(np.uint8)
    rgba[m3, 2] = (75 + t3[m3] * (34 - 75)).astype(np.uint8)
    rgba[m3, 3] = 255

    if nodata_mask is not None:
        rgba[nodata_mask, :] = 0

    return rgba


def apply_ndwi_colormap(values: np.ndarray, nodata_mask: Optional[np.ndarray] = None) -> np.ndarray:
    """Applies a high-contrast water index colormap."""
    h, w = values.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    norm = np.clip((values + 0.5) / 1.0, 0.0, 1.0)

    # Dry land (beige) -> Saturated soil (cyan) -> Deep Water (deep royal blue)
    m_land = norm < 0.5
    t_land = norm / 0.5
    rgba[m_land, 0] = (180 + t_land[m_land] * (60 - 180)).astype(np.uint8)
    rgba[m_land, 1] = (170 + t_land[m_land] * (160 - 170)).astype(np.uint8)
    rgba[m_land, 2] = (140 + t_land[m_land] * (190 - 140)).astype(np.uint8)
    rgba[m_land, 3] = 220

    m_water = norm >= 0.5
    t_water = (norm - 0.5) / 0.5
    rgba[m_water, 0] = (60 + t_water[m_water] * (10 - 60)).astype(np.uint8)
    rgba[m_water, 1] = (160 + t_water[m_water] * (50 - 160)).astype(np.uint8)
    rgba[m_water, 2] = (190 + t_water[m_water] * (220 - 190)).astype(np.uint8)
    rgba[m_water, 3] = 255

    if nodata_mask is not None:
        rgba[nodata_mask, :] = 0

    return rgba


def apply_hillshade_colormap(values: np.ndarray, nodata_mask: Optional[np.ndarray] = None) -> np.ndarray:
    """Applies an analytical monochrome relief shading colormap."""
    h, w = values.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    # Expects values in [0, 255] or [0, 1]
    if values.max() <= 1.0:
        val_u8 = (np.clip(values, 0.0, 1.0) * 255.0).astype(np.uint8)
    else:
        val_u8 = np.clip(values, 0, 255).astype(np.uint8)

    rgba[:, :, 0] = val_u8
    rgba[:, :, 1] = val_u8
    rgba[:, :, 2] = val_u8
    rgba[:, :, 3] = 255

    if nodata_mask is not None:
        rgba[nodata_mask, :] = 0

    return rgba


def apply_terrain_colormap(values: np.ndarray, nodata_mask: Optional[np.ndarray] = None) -> np.ndarray:
    """Applies a coastal hypsometric tint colormap (Sea -> Coastal Plain -> Inland Hills)."""
    h, w = values.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    # Range 0m to 100m for Mombasa
    norm = np.clip(values / 100.0, 0.0, 1.0)

    # 0m: Deep teal (20, 60, 70)
    # 20m: Emerald green (50, 160, 80)
    # 50m: Golden ochre (200, 180, 80)
    # 100m: Terracotta brown (160, 80, 50)
    m1 = norm <= 0.2
    t1 = norm / 0.2
    rgba[m1, 0] = (20 + t1[m1] * (50 - 20)).astype(np.uint8)
    rgba[m1, 1] = (60 + t1[m1] * (160 - 60)).astype(np.uint8)
    rgba[m1, 2] = (70 + t1[m1] * (80 - 70)).astype(np.uint8)
    rgba[m1, 3] = 240

    m2 = (norm > 0.2) & (norm <= 0.5)
    t2 = (norm - 0.2) / 0.3
    rgba[m2, 0] = (50 + t2[m2] * (200 - 50)).astype(np.uint8)
    rgba[m2, 1] = (160 + t2[m2] * (180 - 160)).astype(np.uint8)
    rgba[m2, 2] = (80 + t2[m2] * (80 - 80)).astype(np.uint8)
    rgba[m2, 3] = 250

    m3 = norm > 0.5
    t3 = (norm - 0.5) / 0.5
    rgba[m3, 0] = (200 + t3[m3] * (160 - 200)).astype(np.uint8)
    rgba[m3, 1] = (180 + t3[m3] * (80 - 180)).astype(np.uint8)
    rgba[m3, 2] = (80 + t3[m3] * (50 - 80)).astype(np.uint8)
    rgba[m3, 3] = 255

    if nodata_mask is not None:
        rgba[nodata_mask, :] = 0

    return rgba


def render_raster_tile(
    asset: RasterAsset,
    z: int,
    x: int,
    y: int,
    tile_size: int = 256,
) -> bytes:
    """
    Renders a 256x256 Web Mercator tile for the requested RasterAsset.
    Returns raw PNG bytes. Returns fully transparent PNG if tile does not intersect.
    """
    tile_bbox = tile_to_bbox_wgs84(z, x, y)

    # Determine asset geographic extent in WGS84
    if asset.item and asset.item.footprint:
        asset_bbox = tuple(asset.item.footprint.extent)
    else:
        # Fallback to standard Mombasa extent
        asset_bbox = (38.9998, -4.6118, 39.9896, -3.6179)

    # If completely disjoint, return blank transparent PNG
    if not bbox_intersects(tile_bbox, asset_bbox):
        transparent_img = Image.new("RGBA", (tile_size, tile_size), (0, 0, 0, 0))
        buffer = io.BytesIO()
        transparent_img.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()

    # Read the raster file
    file_path = Path(asset.storage_path)
    if not file_path.exists():
        transparent_img = Image.new("RGBA", (tile_size, tile_size), (0, 0, 0, 0))
        buffer = io.BytesIO()
        transparent_img.save(buffer, format="PNG")
        return buffer.getvalue()

    # Load raster with PIL or numpy
    # Check if this is a float32 TIFF or RGB TIFF
    pil_img = Image.open(file_path)

    asset_min_lon, asset_min_lat, asset_max_lon, asset_max_lat = asset_bbox
    tile_min_lon, tile_min_lat, tile_max_lon, tile_max_lat = tile_bbox

    # Calculate pixel window in the source raster corresponding to the tile
    w_src, h_src = pil_img.size

    # Source coordinate ratios
    # Note: image coordinates: y=0 is top (max_lat), y=h_src is bottom (min_lat)
    x0_ratio = (tile_min_lon - asset_min_lon) / (asset_max_lon - asset_min_lon)
    x1_ratio = (tile_max_lon - asset_min_lon) / (asset_max_lon - asset_min_lon)
    y0_ratio = (asset_max_lat - tile_max_lat) / (asset_max_lat - asset_min_lat)
    y1_ratio = (asset_max_lat - tile_min_lat) / (asset_max_lat - asset_min_lat)

    px0 = int(round(x0_ratio * w_src))
    px1 = int(round(x1_ratio * w_src))
    py0 = int(round(y0_ratio * h_src))
    py1 = int(round(y1_ratio * h_src))

    # Clamp to valid image bounds
    px0_c = max(0, min(w_src - 1, px0))
    px1_c = max(px0_c + 1, min(w_src, px1))
    py0_c = max(0, min(h_src - 1, py0))
    py1_c = max(py0_c + 1, min(h_src, py1))

    crop = pil_img.crop((px0_c, py0_c, px1_c, py1_c))
    resized_crop = crop.resize((tile_size, tile_size), resample=Image.Resampling.BILINEAR)

    # Process based on mode and asset_key
    if pil_img.mode in ("RGB", "RGBA"):
        out_img = resized_crop.convert("RGBA")
    else:
        # Float32 or single band
        arr = np.array(resized_crop, dtype=np.float32)
        nodata_mask = np.isnan(arr)
        if asset.nodata_value is not None:
            nodata_mask = nodata_mask | (arr == asset.nodata_value)

        key = asset.asset_key.lower()
        if "ndvi" in key:
            rgba_arr = apply_ndvi_colormap(arr, nodata_mask)
        elif "ndwi" in key:
            rgba_arr = apply_ndwi_colormap(arr, nodata_mask)
        elif "hillshade" in key:
            rgba_arr = apply_hillshade_colormap(arr, nodata_mask)
        elif "dem" in key or "elevation" in key:
            rgba_arr = apply_terrain_colormap(arr, nodata_mask)
        else:
            # Normalized grayscale
            min_v = asset.min_val if asset.min_val is not None else float(np.nanmin(arr))
            max_v = asset.max_val if asset.max_val is not None else float(np.nanmax(arr))
            spread = max_v - min_v if max_v > min_v else 1.0
            norm = np.clip((arr - min_v) / spread, 0.0, 1.0)
            rgba_arr = apply_hillshade_colormap(norm, nodata_mask)

        out_img = Image.fromarray(rgba_arr, mode="RGBA")

    buffer = io.BytesIO()
    out_img.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
