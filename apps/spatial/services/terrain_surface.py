"""Bounded Copernicus DSM window as a Cesium height grid.

The grid is the downloaded surface model. Geoid separation is not applied.
Heights are not a bare-earth survey.
"""

from __future__ import annotations

import math

import numpy as np

from apps.spatial.services.compute.contracts import PermanentComputeError
from apps.spatial.services.terrain import (
    HEADER_KEYS,
    LIMITATION,
    copernicus_tile_id,
    parse_aaigrid,
)

FORMAT = "glitters-height-grid-v1"
ATTRIBUTION = "Copernicus DEM, European Space Agency / European Union"
LICENSE = "Copernicus-DEM"
SOURCE_NAME = "Copernicus DEM GLO-30"
MAX_SPAN_DEG = 0.08
MAX_CELLS = 250_000
NODATA_FAIL_FRACTION = 0.02
HALF_DEG = 0.02

PLACES = {
    "nairobi": {"longitude": 36.8219, "latitude": -1.2921, "label": "Nairobi"},
    "kisumu": {"longitude": 34.7680, "latitude": -0.0917, "label": "Kisumu"},
}

SURFACE_LIMITATION = (
    f"{LIMITATION} Geoid separation was not applied. "
    "Scene heights are the DSM values. "
    "The patch edge meets the WGS84 ellipsoid. "
    "This is not photogrammetry and not a surveyed bare-earth terrain."
)


def place_extent(place: str) -> tuple[float, float, float, float]:
    spec = PLACES.get(place)
    if spec is None:
        raise PermanentComputeError("Choose Nairobi or Kisumu for this surface.")
    lon = float(spec["longitude"])
    lat = float(spec["latitude"])
    return (lon - HALF_DEG, lat - HALF_DEG, lon + HALF_DEG, lat + HALF_DEG)


def fetch_place_asc(place: str) -> str:
    """Read one real COG window. Tests replace this function."""

    from apps.spatial.services.terrain import _cache_path, _window, tile_url, translate_window

    spec = PLACES[place]
    window = _window(place_extent(place))
    tile_id = copernicus_tile_id(float(spec["latitude"]), float(spec["longitude"]))
    cached = _cache_path(tile_id, window)
    if not cached.exists() or cached.stat().st_size == 0:
        translate_window(tile_url(tile_id), window, cached)
    return cached.read_text(encoding="utf-8")


def _corners(header: dict[str, float], cell: float) -> tuple[float, float]:
    if "xllcorner" in header:
        west = header["xllcorner"]
    elif "xllcenter" in header:
        west = header["xllcenter"] - cell / 2
    else:
        raise PermanentComputeError("The elevation window has no west origin.")
    if "yllcorner" in header:
        south = header["yllcorner"]
    elif "yllcenter" in header:
        south = header["yllcenter"] - cell / 2
    else:
        raise PermanentComputeError("The elevation window has no south origin.")
    return west, south


def _header(text: str) -> dict[str, float]:
    header: dict[str, float] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split()
        key = parts[0].lower()
        if key in HEADER_KEYS and key not in header:
            header[key] = float(parts[-1])
            continue
        break
    return header


def _fill_rare_nodata(grid: np.ndarray, valid: np.ndarray) -> int:
    missing = int((~valid).sum())
    if missing == 0:
        return 0
    points_y, points_x = np.where(valid)
    holes_y, holes_x = np.where(~valid)
    for row, col in zip(holes_y, holes_x, strict=True):
        distance = (points_y - row) ** 2 + (points_x - col) ** 2
        nearest = int(np.argmin(distance))
        grid[row, col] = grid[points_y[nearest], points_x[nearest]]
    return missing


def height_grid_from_asc(text: str, *, place: str) -> dict:
    """Turn one AAIGrid window into a height document. No heights are invented."""

    if place not in PLACES:
        raise PermanentComputeError("Choose Nairobi or Kisumu for this surface.")
    try:
        grid, cellsize, nodata = parse_aaigrid(text)
    except Exception as exc:
        raise PermanentComputeError("The elevation window could not be parsed.") from exc
    header = _header(text)
    nrows, ncols = int(grid.shape[0]), int(grid.shape[1])
    if nrows * ncols > MAX_CELLS:
        raise PermanentComputeError("The elevation window is too large to publish.")
    west, south = _corners(header, cellsize)
    east = west + ncols * cellsize
    north = south + nrows * cellsize
    if east - west > MAX_SPAN_DEG or north - south > MAX_SPAN_DEG or east <= west or north <= south:
        raise PermanentComputeError("The elevation window is outside the publishable span.")
    valid = np.isfinite(grid) & (grid > nodata + 1) & (grid > -500)
    if int(valid.sum()) == 0:
        raise PermanentComputeError(
            "The elevation window contained no valid samples. No surface was invented."
        )
    missing_fraction = 1.0 - float(valid.mean())
    if missing_fraction > NODATA_FAIL_FRACTION:
        raise PermanentComputeError(
            "The elevation window has too much missing data. No surface was invented."
        )
    filled = _fill_rare_nodata(grid, valid)
    flat = [round(float(value), 2) for value in grid.reshape(-1)]
    samples = np.asarray(flat, dtype=np.float64)
    spec = PLACES[place]
    tile_id = copernicus_tile_id(float(spec["latitude"]), float(spec["longitude"]))
    return {
        "format": FORMAT,
        "place": place,
        "label": spec["label"],
        "west": west,
        "south": south,
        "east": east,
        "north": north,
        "center_longitude": (west + east) / 2,
        "center_latitude": (south + north) / 2,
        "width": ncols,
        "height": nrows,
        "cellsize_deg": cellsize,
        "nodata": nodata,
        "vertical_datum": "EGM2008",
        "horizontal_crs": "EPSG:4326",
        "source_kind": "DSM",
        "source_name": SOURCE_NAME,
        "resolution_m": 30,
        "geoid_applied": False,
        "bare_earth": False,
        "survey": False,
        "photogrammetry": False,
        "reconstruction": False,
        "tile_id": tile_id,
        "attribution": ATTRIBUTION,
        "license": LICENSE,
        "limitation": SURFACE_LIMITATION,
        "elevation_min_m": round(float(samples.min()), 2),
        "elevation_max_m": round(float(samples.max()), 2),
        "elevation_mean_m": round(float(samples.mean()), 2),
        "sample_count": int(samples.size),
        "nodata_count": filled,
        "nodata_filled": filled,
        "heights": flat,
    }


def _required_number(document: dict, key: str) -> float:
    value = document.get(key)
    numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
    if not numeric or not math.isfinite(float(value)):
        raise PermanentComputeError(f"The height grid field {key} is not a number.")
    return float(value)


def validate_height_grid(document: dict) -> None:
    if not isinstance(document, dict) or document.get("format") != FORMAT:
        raise PermanentComputeError("A glitters height grid is required.")
    if document.get("source_kind") != "DSM":
        raise PermanentComputeError("Only a DSM height grid can be published here.")
    if document.get("geoid_applied") is not False:
        raise PermanentComputeError("Geoid separation was not applied and cannot be claimed.")
    if document.get("bare_earth") is not False or document.get("survey") is not False:
        raise PermanentComputeError("This grid is not a surveyed bare-earth terrain.")
    if document.get("vertical_datum") != "EGM2008":
        raise PermanentComputeError("The vertical datum must stay EGM2008.")
    if document.get("horizontal_crs") != "EPSG:4326":
        raise PermanentComputeError("The horizontal CRS must stay EPSG:4326.")
    width = document.get("width")
    height = document.get("height")
    heights = document.get("heights")
    if isinstance(width, bool) or isinstance(height, bool):
        raise PermanentComputeError("The height grid dimensions are not valid.")
    if not isinstance(width, int) or not isinstance(height, int) or width < 2 or height < 2:
        raise PermanentComputeError("The height grid dimensions are not valid.")
    if not isinstance(heights, list) or len(heights) != width * height:
        raise PermanentComputeError("The height grid does not match its dimensions.")
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        for value in heights
    ):
        raise PermanentComputeError("The height grid contains a non-finite sample.")
    west = _required_number(document, "west")
    south = _required_number(document, "south")
    east = _required_number(document, "east")
    north = _required_number(document, "north")
    if east <= west or north <= south or east - west > MAX_SPAN_DEG or north - south > MAX_SPAN_DEG:
        raise PermanentComputeError("The height grid extent is not publishable.")
    cell = _required_number(document, "cellsize_deg")
    if cell <= 0:
        raise PermanentComputeError("The height grid cell size is not valid.")
    if abs((east - west) - width * cell) > 1e-6 or abs((north - south) - height * cell) > 1e-6:
        raise PermanentComputeError("The height grid extent does not match its cells.")
    samples = np.asarray(heights, dtype=np.float64)
    lowest = _required_number(document, "elevation_min_m")
    highest = _required_number(document, "elevation_max_m")
    average = _required_number(document, "elevation_mean_m")
    if abs(float(samples.min()) - lowest) > 0.02:
        raise PermanentComputeError("The recorded minimum elevation does not match the grid.")
    if abs(float(samples.max()) - highest) > 0.02:
        raise PermanentComputeError("The recorded maximum elevation does not match the grid.")
    if abs(float(samples.mean()) - average) > 0.05:
        raise PermanentComputeError("The recorded mean elevation does not match the grid.")


def sample_height(document: dict, longitude: float, latitude: float) -> float | None:
    """Bilinear sample at cell centers. Outside the patch returns None."""

    west = float(document["west"])
    south = float(document["south"])
    east = float(document["east"])
    north = float(document["north"])
    if longitude < west or longitude > east or latitude < south or latitude > north:
        return None
    cell = float(document["cellsize_deg"])
    width = int(document["width"])
    height = int(document["height"])
    heights = document["heights"]
    col_f = min(max((longitude - west) / cell - 0.5, 0.0), width - 1)
    row_f = min(max((north - latitude) / cell - 0.5, 0.0), height - 1)
    col0 = int(math.floor(col_f))
    row0 = int(math.floor(row_f))
    col1 = min(col0 + 1, width - 1)
    row1 = min(row0 + 1, height - 1)
    tx = col_f - col0
    ty = row_f - row0

    def at(row: int, col: int) -> float:
        return float(heights[row * width + col])

    north_row = at(row0, col0) * (1 - tx) + at(row0, col1) * tx
    south_row = at(row1, col0) * (1 - tx) + at(row1, col1) * tx
    return north_row * (1 - ty) + south_row * ty
