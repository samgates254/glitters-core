"""Windowed Copernicus DEM GLO-30 screening.

The grid is a 30 m digital surface model. It is not a bare-earth survey.
"""

from __future__ import annotations

import math
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from apps.spatial.services.providers.http import ProviderTransportError

DEM_HOST = "copernicus-dem-30m.s3.eu-central-1.amazonaws.com"
TILE_ID = re.compile(r"^Copernicus_DSM_COG_10_[NS]\d{2}_00_[EW]\d{3}_00_DEM$")
HEADER_KEYS = {
    "ncols",
    "nrows",
    "xllcorner",
    "yllcorner",
    "xllcenter",
    "yllcenter",
    "cellsize",
    "nodata_value",
}
LIMITATION = (
    "Copernicus DEM GLO-30 is a 30 m surface model in EGM2008. "
    "It includes buildings and vegetation. It is not a bare-earth survey. "
    "Slope is a screening gradient, not an engineering surface."
)


def copernicus_tile_id(latitude: float, longitude: float) -> str:
    """Name the 1-degree tile by its southwest corner."""

    south = math.floor(latitude)
    west = math.floor(longitude)
    ns = "S" if south < 0 else "N"
    ew = "W" if west < 0 else "E"
    tile_id = f"Copernicus_DSM_COG_10_{ns}{abs(south):02d}_00_{ew}{abs(west):03d}_00_DEM"
    if not TILE_ID.fullmatch(tile_id):
        raise ProviderTransportError("The elevation tile name was not valid.")
    return tile_id


def tile_url(tile_id: str) -> str:
    if not TILE_ID.fullmatch(tile_id):
        raise ProviderTransportError("The elevation tile name was not valid.")
    return f"/vsicurl/https://{DEM_HOST}/{tile_id}/{tile_id}.tif"


def parse_aaigrid(text: str) -> tuple[np.ndarray, float, float]:
    """Return the grid, cell size in degrees, and nodata value."""

    header: dict[str, float] = {}
    rows: list[list[float]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split()
        key = parts[0].lower()
        if key in HEADER_KEYS and len(rows) == 0:
            header[key] = float(parts[-1])
            continue
        rows.append([float(value) for value in parts])
    if "ncols" not in header or "nrows" not in header or "cellsize" not in header:
        raise ProviderTransportError("The elevation window had no grid header.")
    expected = int(header["nrows"])
    width = int(header["ncols"])
    if len(rows) != expected or any(len(row) != width for row in rows):
        raise ProviderTransportError("The elevation window did not match its header.")
    grid = np.asarray(rows, dtype=np.float64)
    return grid, float(header["cellsize"]), float(header.get("nodata_value", -32767))


def screen_grid(
    grid: np.ndarray,
    *,
    cellsize_deg: float,
    nodata: float,
    latitude: float,
) -> dict[str, float | int]:
    valid = np.isfinite(grid) & (grid > nodata + 1) & (grid > -500)
    samples = grid[valid]
    if samples.size == 0:
        return {"sample_count": 0, "nodata_count": int(grid.size)}
    y_m = cellsize_deg * 110540.0
    x_m = cellsize_deg * 111320.0 * max(0.2, math.cos(math.radians(latitude)))
    filled = np.where(valid, grid, np.nan)
    dz_y, dz_x = np.gradient(filled, y_m, x_m)
    slope = np.degrees(np.arctan(np.hypot(dz_x, dz_y)))
    slope_samples = slope[np.isfinite(slope)]
    return {
        "sample_count": int(samples.size),
        "nodata_count": int(grid.size - samples.size),
        "elevation_min_m": round(float(samples.min()), 2),
        "elevation_max_m": round(float(samples.max()), 2),
        "elevation_mean_m": round(float(samples.mean()), 2),
        "slope_min_deg": round(float(slope_samples.min()), 2) if slope_samples.size else 0.0,
        "slope_max_deg": round(float(slope_samples.max()), 2) if slope_samples.size else 0.0,
        "slope_mean_deg": round(float(slope_samples.mean()), 2) if slope_samples.size else 0.0,
    }


def window_transect(
    grid: np.ndarray,
    *,
    cellsize_deg: float,
    nodata: float,
    latitude: float,
) -> list[dict[str, float]]:
    """West-to-east samples from the middle row of a downloaded window."""

    if grid.size == 0:
        return []
    row = grid[grid.shape[0] // 2]
    x_m = cellsize_deg * 111320.0 * max(0.2, math.cos(math.radians(latitude)))
    stride = max(1, int(np.ceil(row.size / 40)))
    points: list[dict[str, float]] = []
    for index in range(0, int(row.size), stride):
        value = float(row[index])
        if not math.isfinite(value) or value <= nodata + 1 or value <= -500:
            continue
        points.append(
            {
                "distance_m": round(index * x_m, 1),
                "elevation_m": round(value, 2),
            }
        )
    return points


def _window(extent: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    west, south, east, north = extent
    pad = 0.0008
    west -= pad
    south -= pad
    east += pad
    north += pad
    if east - west > 0.08 or north - south > 0.08:
        raise ProviderTransportError("The outline is too large for one elevation window.")
    if east <= west or north <= south:
        raise ProviderTransportError("The outline has no area.")
    return west, south, east, north


def _cache_path(tile_id: str, window: tuple[float, float, float, float]) -> Path:
    west, south, east, north = window
    name = f"{tile_id}_{west:.4f}_{south:.4f}_{east:.4f}_{north:.4f}.asc"
    root = Path(tempfile.gettempdir()) / "glitters-godseye-dem"
    root.mkdir(parents=True, exist_ok=True)
    path = (root / name).resolve()
    if path.parent != root.resolve():
        raise ProviderTransportError("The elevation cache path was rejected.")
    return path


def translate_window(
    url: str,
    window: tuple[float, float, float, float],
    destination: Path,
) -> None:
    """Read one COG window. The URL is built from the tile id, not from the caller."""

    program = shutil.which("gdal_translate")
    if not program:
        raise ProviderTransportError("gdal_translate is not available on this machine.")
    west, south, east, north = window
    command = [
        program,
        "-q",
        "-projwin",
        f"{west:.6f}",
        f"{north:.6f}",
        f"{east:.6f}",
        f"{south:.6f}",
        "-of",
        "AAIGrid",
        url,
        str(destination),
    ]
    env = os.environ.copy()
    env["GDAL_HTTP_TIMEOUT"] = "25"
    env["GDAL_HTTP_MAX_RETRY"] = "1"
    env["CPL_VSIL_CURL_ALLOWED_EXTENSIONS"] = ".tif"
    try:
        completed = subprocess.run(  # noqa: S603
            command,
            check=False,
            capture_output=True,
            timeout=45,
            text=True,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise ProviderTransportError("The elevation window timed out.") from exc
    if completed.returncode != 0 or not destination.exists():
        detail = (completed.stderr or completed.stdout or "").strip().splitlines()
        message = detail[-1] if detail else "The elevation window could not be read."
        raise ProviderTransportError(message[:240])


def screen_extent(
    extent: tuple[float, float, float, float],
    *,
    latitude: float,
    longitude: float,
) -> dict:
    window = _window(extent)
    tile_id = copernicus_tile_id(latitude, longitude)
    cached = _cache_path(tile_id, window)
    if not cached.exists() or cached.stat().st_size == 0:
        translate_window(tile_url(tile_id), window, cached)
    grid, cellsize, nodata = parse_aaigrid(cached.read_text(encoding="utf-8"))
    stats = screen_grid(grid, cellsize_deg=cellsize, nodata=nodata, latitude=latitude)
    payload = {
        "downloaded": stats["sample_count"] > 0,
        "source_name": "Copernicus DEM GLO-30",
        "source_kind": "DSM",
        "tile_id": tile_id,
        "resolution_m": 30,
        "vertical_datum": "EGM2008",
        "horizontal_crs": "EPSG:4326",
        "attribution": "Copernicus DEM, European Space Agency / European Union",
        "limitation": LIMITATION,
        "land_cover": None,
        "land_cover_note": "No land-cover raster was downloaded for this outline.",
    }
    payload.update(stats)
    payload["transect"] = (
        window_transect(grid, cellsize_deg=cellsize, nodata=nodata, latitude=latitude)
        if stats["sample_count"]
        else []
    )
    payload["transect_note"] = (
        "West-to-east samples from the middle row of the downloaded DSM window. "
        "Not a surveyed profile."
    )
    if stats["sample_count"] == 0:
        payload["limitation"] = (
            "The elevation window contained no valid samples. No elevation was invented."
        )
    return payload
