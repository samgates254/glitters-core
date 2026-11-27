"""Self-hosted quantized-mesh terrain for one bounded DSM window.

Tiles use Cesium's global geographic scheme. Files are named with TMS Y, and
``layer.json`` ``available`` ranges use that same Y. Only tiles that intersect
the DSM window are published. Levels 0 through 9 are ancestor tiles so the
quadtree can descend. Their posts are coarse. A post outside the DSM is
ellipsoid height 0. That is not a global elevation model. The outer margin
fades the ellipsoid height to 0 so the patch meets the ellipsoid. That fade is
a rendering seam. It is not surveyed terrain and it does not change the
preserved source heights.
"""

from __future__ import annotations

import json

from apps.spatial.services.compute.contracts import PermanentComputeError
from apps.spatial.services.compute.gltf_job import canonical_bytes, sha256_hex
from apps.spatial.services.quantized_mesh import encode_mesh
from apps.spatial.services.terrain_surface import ATTRIBUTION, LICENSE, sample_height
from apps.spatial.services.vertical_datum import VERTICAL_UNCERTAINTY, vertical_record

FORMAT = "quantized-mesh-1.0"
LEVELS = tuple(range(16))
POSTS = {
    0: 5,
    1: 5,
    2: 5,
    3: 5,
    4: 9,
    5: 9,
    6: 9,
    7: 9,
    8: 17,
    9: 17,
    10: 17,
    11: 17,
    12: 25,
    13: 25,
    14: 33,
    15: 33,
}
EDGE_MARGIN_DEG = 0.008
SCHEME = "tms"


def geographic_counts(level: int) -> tuple[int, int]:
    if level < 0 or level > 22:
        raise PermanentComputeError("That terrain level is outside the geographic scheme.")
    return 2 * (1 << level), 1 << level


def tile_rectangle(level: int, x_index: int, cesium_y: int) -> tuple[float, float, float, float]:
    x_tiles, y_tiles = geographic_counts(level)
    if x_index < 0 or cesium_y < 0 or x_index >= x_tiles or cesium_y >= y_tiles:
        raise PermanentComputeError("That terrain tile is outside the geographic scheme.")
    width = 360.0 / x_tiles
    height = 180.0 / y_tiles
    west = -180.0 + x_index * width
    north = 90.0 - cesium_y * height
    return west, north - height, west + width, north


def tms_y(level: int, cesium_y: int) -> int:
    _x_tiles, y_tiles = geographic_counts(level)
    return y_tiles - cesium_y - 1


def edge_factor(
    longitude: float,
    latitude: float,
    west: float,
    south: float,
    east: float,
    north: float,
    margin: float = EDGE_MARGIN_DEG,
) -> float:
    """1 inside the inner window, 0 on and outside the DSM edge."""

    distance = min(longitude - west, east - longitude, latitude - south, north - latitude)
    if distance >= margin:
        return 1.0
    if distance <= 0:
        return 0.0
    return distance / margin


def rendering_sample(
    document: dict, longitude: float, latitude: float
) -> tuple[float, dict | None]:
    """Ellipsoid height for one post. Outside the DSM the rendered value is 0.

    The returned record still carries the unfaded conversion when the source
    sample exists. ``estimated_ground_elevation_m`` stays null.
    """

    source = sample_height(document, longitude, latitude)
    if source is None:
        return 0.0, None
    record = vertical_record(source, longitude, latitude, exaggeration=1.0)
    factor = edge_factor(
        longitude,
        latitude,
        float(document["west"]),
        float(document["south"]),
        float(document["east"]),
        float(document["north"]),
    )
    rendered = float(record["rendering_elevation_m"]) * factor
    record["edge_factor"] = factor
    record["rendered_with_edge_m"] = rendered
    record["edge_is_survey"] = False
    record["terrain_exaggeration"] = 1.0
    return rendered, record


def _intersects(
    rectangle: tuple[float, float, float, float],
    bounds: tuple[float, float, float, float],
) -> bool:
    west, south, east, north = rectangle
    bound_west, bound_south, bound_east, bound_north = bounds
    return not (
        east < bound_west or west > bound_east or north < bound_south or south > bound_north
    )


def _posts(
    west: float, south: float, east: float, north: float, posts: int
) -> tuple[list[float], list[float], list[float]]:
    longitudes: list[float] = []
    latitudes: list[float] = []
    for row in range(posts):
        if posts == 1:
            latitude = (south + north) / 2
        else:
            latitude = south + (north - south) * row / (posts - 1)
        for column in range(posts):
            if posts == 1:
                longitude = (west + east) / 2
            else:
                longitude = west + (east - west) * column / (posts - 1)
            longitudes.append(longitude)
            latitudes.append(latitude)
    return longitudes, latitudes, []


def build_terrain_bundle(document: dict) -> dict[str, bytes]:
    """Build layer.json, vertical.json, a manifest, and the intersecting tiles."""

    if document.get("source_kind") != "DSM" or document.get("vertical_datum") != "EGM2008":
        raise PermanentComputeError("Terrain tiles require the EGM2008 DSM height grid.")
    if document.get("geoid_applied") is not False:
        raise PermanentComputeError("The source grid must keep geoid_applied false.")
    bounds = (
        float(document["west"]),
        float(document["south"]),
        float(document["east"]),
        float(document["north"]),
    )
    files: dict[str, bytes] = {}
    available: list[list[dict[str, int]]] = [[] for _level in range(max(LEVELS) + 1)]
    outside_posts = 0
    faded_posts = 0
    tile_count = 0
    for level in LEVELS:
        x_tiles, y_tiles = geographic_counts(level)
        width = 360.0 / x_tiles
        height = 180.0 / y_tiles
        x_start = max(0, int((bounds[0] - (-180.0)) / width) - 1)
        x_end = min(x_tiles - 1, int((bounds[2] - (-180.0)) / width) + 1)
        y_start = max(0, int((90.0 - bounds[3]) / height) - 1)
        y_end = min(y_tiles - 1, int((90.0 - bounds[1]) / height) + 1)
        posts = POSTS[level]
        for cesium_y in range(y_start, y_end + 1):
            for x_index in range(x_start, x_end + 1):
                rectangle = tile_rectangle(level, x_index, cesium_y)
                if not _intersects(rectangle, bounds):
                    continue
                longitudes, latitudes, _unused = _posts(*rectangle, posts)
                heights: list[float] = []
                for longitude, latitude in zip(longitudes, latitudes, strict=True):
                    rendered, record = rendering_sample(document, longitude, latitude)
                    heights.append(rendered)
                    if record is None:
                        outside_posts += 1
                    elif record["edge_factor"] < 1:
                        faded_posts += 1
                encoded = encode_mesh(
                    longitudes=longitudes,
                    latitudes=latitudes,
                    heights=heights,
                    west=rectangle[0],
                    south=rectangle[1],
                    east=rectangle[2],
                    north=rectangle[3],
                    posts=posts,
                )
                file_y = tms_y(level, cesium_y)
                files[f"{level}/{x_index}/{file_y}.terrain"] = encoded
                available[level].append(
                    {"startX": x_index, "startY": file_y, "endX": x_index, "endY": file_y}
                )
                tile_count += 1
    if tile_count < 1:
        raise PermanentComputeError("No terrain tiles intersect the DSM window.")
    center_longitude = float(document["center_longitude"])
    center_latitude = float(document["center_latitude"])
    _rendered, center_record = rendering_sample(document, center_longitude, center_latitude)
    if center_record is None:
        raise PermanentComputeError("The DSM center has no elevation. No tile was invented.")
    vertical = {
        "format": "glitters-vertical-v1",
        "place": document.get("place"),
        "source_kind": "DSM",
        "source_vertical_datum": "EGM2008",
        "rendering_frame": "WGS84-ellipsoid",
        "formula": "h = H - geoid_offset",
        "geoid_applied_to_mesh": True,
        "source_geoid_applied": False,
        "source_heights_preserved": True,
        "bare_earth": False,
        "survey": False,
        "photogrammetry": False,
        "reconstruction": False,
        "terrain_exaggeration": 1.0,
        "estimated_ground_elevation_m": None,
        "edge_transition": (
            "Linear fade of the ellipsoid height to 0 across the outer "
            f"{EDGE_MARGIN_DEG} degree margin. This is a rendering seam, not surveyed terrain."
        ),
        "edge_margin_deg": EDGE_MARGIN_DEG,
        "edge_is_survey": False,
        "source_elevation_min_m": document["elevation_min_m"],
        "source_elevation_max_m": document["elevation_max_m"],
        "source_elevation_mean_m": document["elevation_mean_m"],
        "center": center_record,
        "levels": list(LEVELS),
        "posts_by_level": {str(level): POSTS[level] for level in LEVELS},
        "tile_count": tile_count,
        "outside_posts": outside_posts,
        "faded_posts": faded_posts,
        "nodata_filled": document.get("nodata_filled", 0),
        "scheme": SCHEME,
        "projection": "EPSG:4326",
        "attribution": ATTRIBUTION,
        "license": LICENSE,
        "vertical_uncertainty": VERTICAL_UNCERTAINTY,
        "limitation": (
            "Quantized-mesh tiles resample the 30 m DSM. Finer levels are bilinear "
            "resamples, not a finer survey. Levels 0-9 are ancestor tiles with coarse "
            "posts so the viewer can descend. They are not a global elevation model. "
            "The DSM is not bare earth."
        ),
    }
    files["vertical.json"] = canonical_bytes(vertical)
    layer = {
        "tilejson": "2.1.0",
        "format": FORMAT,
        "version": "1.0.0",
        "scheme": SCHEME,
        "tiles": ["{z}/{x}/{y}.terrain"],
        "projection": "EPSG:4326",
        "attribution": (
            f"{ATTRIBUTION}. EGM2008 DSM converted to WGS84 ellipsoid heights. Not a surveyed DTM."
        ),
        "available": available,
        "glitters": {
            "place": document.get("place"),
            "source_kind": "DSM",
            "geoid_applied_to_mesh": True,
            "survey": False,
            "bare_earth": False,
            "photogrammetry": False,
        },
    }
    files["layer.json"] = json.dumps(layer, sort_keys=True, separators=(",", ":")).encode("utf-8")
    listed = [
        {"path": path, "sha256": sha256_hex(payload), "bytes": len(payload)}
        for path, payload in sorted(files.items())
    ]
    manifest = {
        "format": "glitters-bundle-v1",
        "kind": "terrain",
        "files": listed,
    }
    files["manifest.json"] = canonical_bytes(manifest)
    return files
