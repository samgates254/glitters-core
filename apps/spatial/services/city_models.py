"""Validated extruded footprints for one city window.

These are OpenStreetMap polygons with labeled heights. They are not a
photogrammetric reconstruction and assumed heights are not measurements.
"""

from __future__ import annotations

import math

from apps.spatial.services.compute.contracts import PermanentComputeError
from apps.spatial.services.surroundings import LIMITATION, load_surroundings
from apps.spatial.services.terrain_surface import place_extent

FORMAT = "glitters-city-models-v1"
ATTRIBUTION = "© OpenStreetMap contributors"
LICENSE = "ODbL-1.0"


def _finite_pair(value: object) -> tuple[float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    lon, lat = value[0], value[1]
    if isinstance(lon, bool) or isinstance(lat, bool):
        return None
    if not isinstance(lon, (int, float)) or not isinstance(lat, (int, float)):
        return None
    lon_f, lat_f = float(lon), float(lat)
    if not math.isfinite(lon_f) or not math.isfinite(lat_f):
        return None
    if not -180 <= lon_f <= 180 or not -90 <= lat_f <= 90:
        return None
    return lon_f, lat_f


def _ring_area(ring: list[tuple[float, float]]) -> float:
    area = 0.0
    for index in range(len(ring) - 1):
        x1, y1 = ring[index]
        x2, y2 = ring[index + 1]
        area += x1 * y2 - x2 * y1
    return abs(area) / 2


def valid_polygon(geometry: object) -> bool:
    if not isinstance(geometry, dict) or geometry.get("type") != "Polygon":
        return False
    rings = geometry.get("coordinates")
    if not isinstance(rings, list) or not rings:
        return False
    for ring in rings:
        if not isinstance(ring, list) or len(ring) < 4:
            return False
        points: list[tuple[float, float]] = []
        for item in ring:
            pair = _finite_pair(item)
            if pair is None:
                return False
            points.append(pair)
        if points[0] != points[-1]:
            return False
        if _ring_area(points) <= 1e-12:
            return False
    return True


def valid_linestring(geometry: object) -> bool:
    if not isinstance(geometry, dict) or geometry.get("type") != "LineString":
        return False
    coords = geometry.get("coordinates")
    if not isinstance(coords, list) or len(coords) < 2:
        return False
    return all(_finite_pair(item) is not None for item in coords)


def valid_point(geometry: object) -> bool:
    if not isinstance(geometry, dict) or geometry.get("type") != "Point":
        return False
    return _finite_pair(geometry.get("coordinates")) is not None


def _accept(feature: dict) -> bool:
    geometry = feature.get("geometry")
    properties = feature.get("properties")
    if not isinstance(properties, dict):
        return False
    kind = properties.get("kind")
    if kind == "building":
        if not valid_polygon(geometry):
            return False
        basis = properties.get("height_basis")
        if basis not in {"OSM_HEIGHT_TAG", "ASSUMED_FROM_LEVELS", "ASSUMED_SINGLE_STOREY"}:
            return False
        height = properties.get("height_m")
        if isinstance(height, bool) or not isinstance(height, (int, float)):
            return False
        if not 2 <= float(height) <= 400:
            return False
        properties["height_measured"] = False
        note = str(properties.get("height_note", "")).lower()
        if basis != "OSM_HEIGHT_TAG" and "measured" in note and "not a measured" not in note:
            return False
        return True
    if kind == "road":
        return valid_linestring(geometry)
    if kind == "amenity":
        return valid_point(geometry)
    return False


def city_document(place: str, report: dict | None = None) -> dict:
    if report is None:
        report = load_surroundings(place_extent(place))
    collection = report.get("geojson") if isinstance(report, dict) else None
    features = collection.get("features") if isinstance(collection, dict) else None
    if not isinstance(features, list):
        raise PermanentComputeError("OpenStreetMap did not return a feature collection.")
    kept: list[dict] = []
    rejected = 0
    counts = {"OSM_HEIGHT_TAG": 0, "ASSUMED_FROM_LEVELS": 0, "ASSUMED_SINGLE_STOREY": 0}
    for feature in features:
        if not isinstance(feature, dict) or not _accept(feature):
            rejected += 1
            continue
        kept.append(feature)
        basis = feature["properties"].get("height_basis")
        if basis in counts:
            counts[basis] += 1
    buildings = counts["OSM_HEIGHT_TAG"] + counts["ASSUMED_FROM_LEVELS"]
    buildings += counts["ASSUMED_SINGLE_STOREY"]
    if buildings < 1:
        raise PermanentComputeError(
            "No valid building footprints were published. Nothing was invented."
        )
    roads = sum(1 for feature in kept if feature["properties"].get("kind") == "road")
    note = str(report.get("limitation") or LIMITATION)
    note = (
        f"{note} Representation is extruded footprints, not photogrammetry. "
        "Assumed heights are not measured heights. "
        f"{rejected} features were rejected."
    )
    return {
        "format": FORMAT,
        "place": place,
        "representation": "extruded-footprint",
        "photogrammetry": False,
        "reconstruction": False,
        "survey": False,
        "license": LICENSE,
        "attribution": ATTRIBUTION,
        "source_name": "OpenStreetMap",
        "limitation": note,
        "height_basis_counts": counts,
        "building_count": buildings,
        "road_count": roads,
        "rejected_count": rejected,
        "queried_bbox": report.get("queried_bbox"),
        "geojson": {"type": "FeatureCollection", "features": kept},
    }


def validate_city_document(document: dict) -> None:
    if not isinstance(document, dict) or document.get("format") != FORMAT:
        raise PermanentComputeError("A city model document is required.")
    if document.get("representation") != "extruded-footprint":
        raise PermanentComputeError("Only extruded footprints are published by this job.")
    if document.get("photogrammetry") is not False or document.get("reconstruction") is not False:
        raise PermanentComputeError("Extruded footprints are not a photogrammetric reconstruction.")
    if document.get("survey") is not False:
        raise PermanentComputeError("These buildings are not a survey.")
    if int(document.get("building_count") or 0) < 1:
        raise PermanentComputeError("The city document has no buildings.")
    features = (document.get("geojson") or {}).get("features")
    if not isinstance(features, list):
        raise PermanentComputeError("The city document has no features.")
    for feature in features:
        if not isinstance(feature, dict) or not _accept(feature):
            raise PermanentComputeError("A city feature failed validation.")
        props = feature["properties"]
        if props.get("kind") == "building" and props.get("height_measured") is not False:
            raise PermanentComputeError("Assumed or mapped OSM heights were labeled as measured.")
