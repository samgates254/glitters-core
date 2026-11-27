"""EGM2008 orthometric heights to WGS84 ellipsoid heights.

Copernicus GLO-30 is a digital surface model. Its stored values are orthometric
heights on EGM2008. They are not a surveyed bare-earth DTM. Cesium quantized-mesh
terrain and globe positions are heights above the WGS84 ellipsoid:

    ellipsoidal height = EGM2008 height - geoid_offset

``geoid_offset`` is the PROJ geographic-to-vertical grid value. Adding it to an
ellipsoidal height produces the EGM2008 height. The classic geoid undulation N
in ``h = H + N`` is ``N = -geoid_offset``.

The grid in ``apps/spatial/data/egm2008_kenya_25.bin`` is a window of the public
NGA / PROJ file ``us_nga_egm08_25.tif`` (SHA-256
4191d471eefebf24091b56dbc604353cb3b8cf8cc70e448bb9ae56a272bef17a). That file is
not committed. Bilinear interpolation is evaluated half a cell below the
point-registered index. That is the sample PROJ 9.7.1 ``vgridshift`` and GDAL
bilinear both return for this GeoTIFF. Outside the window the conversion is
refused. No undulation is invented.
"""

from __future__ import annotations

import math
import struct
from pathlib import Path

import numpy as np

from apps.spatial.services.compute.contracts import PermanentComputeError

GRID_PATH = Path(__file__).resolve().parent.parent / "data" / "egm2008_kenya_25.bin"
GRID_STEP_DEG = 1.0 / 24.0
GEOID_PRODUCT_BOUND_M = 1.0
SOURCE_PRODUCT_STATEMENT_M = 4.0

VERTICAL_UNCERTAINTY = {
    "source_product": "Copernicus DEM GLO-30",
    "source_kind": "DSM",
    "source_is_dtm": False,
    "source_is_survey": False,
    "source_vertical_datum": "EGM2008",
    "rendering_frame": "WGS84-ellipsoid",
    "formula": "h = H - geoid_offset",
    "geoid_model": "EGM2008 2.5 arc-minute NGA grid us_nga_egm08_25",
    "geoid_method": "bilinear, half-cell shift matching PROJ vgridshift",
    "geoid_grid_spacing_deg": GRID_STEP_DEG,
    "geoid_product_bound_m": GEOID_PRODUCT_BOUND_M,
    "geoid_bound_note": (
        "One metre is a product bound for this Kenya window. "
        "It is not a standard deviation from a local control network."
    ),
    "source_product_statement": (
        "The Copernicus GLO-30 product statement quotes absolute vertical "
        "accuracy better than 4 metres LE90. This job did not remeasure it."
    ),
    "source_product_statement_m": SOURCE_PRODUCT_STATEMENT_M,
    "estimated_ground": (
        "No bare-earth elevation was estimated. The DSM includes canopy and structures."
    ),
}

_GRID: tuple[float, float, float, int, int, np.ndarray] | None = None


def _load() -> tuple[float, float, float, int, int, np.ndarray]:
    global _GRID
    if _GRID is not None:
        return _GRID
    payload = GRID_PATH.read_bytes()
    if len(payload) < 40 or payload[:4] != b"EGM8":
        raise PermanentComputeError("The EGM2008 Kenya window is missing or truncated.")
    _magic, version, origin_lon, origin_lat, step, width, height = struct.unpack_from(
        "<4sIdddII", payload, 0
    )
    count = int(width) * int(height)
    if version != 1 or count <= 0 or 40 + count * 4 != len(payload):
        raise PermanentComputeError("The EGM2008 Kenya window does not match its header.")
    if abs(float(step) - GRID_STEP_DEG) > 1e-12:
        raise PermanentComputeError("The EGM2008 Kenya window spacing is not 2.5 arc-minutes.")
    values = np.frombuffer(payload, dtype="<f4", offset=40, count=count)
    _GRID = (float(origin_lon), float(origin_lat), float(step), int(width), int(height), values)
    return _GRID


def geoid_offset_m(longitude: float, latitude: float) -> float:
    """PROJ grid value at one point. EGM2008 height = ellipsoid height + this value."""

    if not math.isfinite(longitude) or not math.isfinite(latitude):
        raise PermanentComputeError("The geoid sample is not a finite coordinate.")
    origin_lon, origin_lat, step, width, height, values = _load()
    column = (float(longitude) - origin_lon) / step - 0.5
    row = (origin_lat - float(latitude)) / step - 0.5
    if column < 0 or row < 0 or column > width - 1 or row > height - 1:
        raise PermanentComputeError(
            "That coordinate is outside the Kenya EGM2008 window. No geoid value was invented."
        )
    column0 = int(math.floor(column))
    row0 = int(math.floor(row))
    column1 = min(column0 + 1, width - 1)
    row1 = min(row0 + 1, height - 1)
    if column1 == column0 or row1 == row0:
        raise PermanentComputeError(
            "That coordinate is outside the Kenya EGM2008 window. No geoid value was invented."
        )
    tx = column - column0
    ty = row - row0
    north = float(values[row0 * width + column0]) * (1 - tx)
    north += float(values[row0 * width + column1]) * tx
    south = float(values[row1 * width + column0]) * (1 - tx)
    south += float(values[row1 * width + column1]) * tx
    return north * (1 - ty) + south * ty


def ellipsoidal_height_m(orthometric_m: float, longitude: float, latitude: float) -> float:
    """Convert one EGM2008 orthometric height into a WGS84 ellipsoid height."""

    if not math.isfinite(orthometric_m):
        raise PermanentComputeError("The source elevation is not finite.")
    return float(orthometric_m) - geoid_offset_m(longitude, latitude)


def vertical_record(
    source_elevation_m: float,
    longitude: float,
    latitude: float,
    *,
    exaggeration: float = 1.0,
) -> dict:
    """Keep the source height and the separate quantities used for rendering.

    ``estimated_ground_elevation_m`` stays null. A DSM is not turned into a DTM.
    ``terrain_exaggeration`` scales only the rendered ellipsoid height.
    """

    if not math.isfinite(exaggeration) or exaggeration < 0 or exaggeration > 8:
        raise PermanentComputeError("Terrain exaggeration must stay between 0 and 8.")
    offset = geoid_offset_m(longitude, latitude)
    rendering = float(source_elevation_m) - offset
    return {
        "source_elevation_m": float(source_elevation_m),
        "source_vertical_datum": "EGM2008",
        "source_kind": "DSM",
        "geoid_offset_m": offset,
        "geoid_undulation_m": -offset,
        "rendering_elevation_m": rendering,
        "rendering_frame": "WGS84-ellipsoid",
        "surface_model_elevation_m": float(source_elevation_m),
        "estimated_ground_elevation_m": None,
        "estimated_ground_reason": VERTICAL_UNCERTAINTY["estimated_ground"],
        "terrain_exaggeration": float(exaggeration),
        "exaggerated_rendering_elevation_m": rendering * float(exaggeration),
        "geoid_applied": True,
        "bare_earth": False,
        "survey": False,
        "photogrammetry": False,
        "vertical_uncertainty": VERTICAL_UNCERTAINTY,
    }
