"""One bounded OpenStreetMap query for an outline.

Roads, amenities, and building footprints are mapped data. Missing heights are
assumptions and are labeled as such. Nothing is stored as a parcel.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from urllib.parse import urlencode

from apps.spatial.services.providers.http import ProviderTransportError, fetch_https

OVERPASS_HOSTS = frozenset({"overpass-api.de", "overpass.openstreetmap.fr"})
OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.openstreetmap.fr/api/interpreter",
)
MAX_BUILDINGS = 250
MAX_ROADS = 120
MAX_AMENITIES = 40
ASSUMED_LEVEL_M = 3.2
LIMITATION = (
    "OpenStreetMap features inside a small box around the outline. "
    "They are not a survey, a title search, or a complete infrastructure inventory. "
    "Heights without an OSM height tag are assumptions for extrusion only."
)


def _meters(raw: str) -> float | None:
    text = raw.strip().lower().replace(",", ".")
    for suffix in ("meter", "meters", "m"):
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()
    try:
        value = float(text)
    except ValueError:
        return None
    if 2 <= value <= 400:
        return round(value, 1)
    return None


def height_fields(tags: dict) -> tuple[float, str, str]:
    mapped = tags.get("height")
    if isinstance(mapped, str):
        meters = _meters(mapped)
        if meters is not None:
            return meters, "OSM_HEIGHT_TAG", "Mapped height tag. Not a surveyed height."
    levels = tags.get("building:levels")
    if isinstance(levels, str):
        try:
            count = float(levels)
        except ValueError:
            count = 0
        if 1 <= count <= 80:
            return (
                round(count * ASSUMED_LEVEL_M, 1),
                "ASSUMED_FROM_LEVELS",
                "3.2 m per mapped level. Not a measured height.",
            )
    return (
        ASSUMED_LEVEL_M,
        "ASSUMED_SINGLE_STOREY",
        "No height was mapped. 3.2 m is an extrusion assumption, not a measured height.",
    )


def _ring(geometry: list) -> list[list[float]] | None:
    ring = []
    for node in geometry:
        if not isinstance(node, dict):
            return None
        lon = node.get("lon")
        lat = node.get("lat")
        if not isinstance(lon, (int, float)) or not isinstance(lat, (int, float)):
            return None
        ring.append([float(lon), float(lat)])
    if len(ring) < 2:
        return None
    return ring


def _features(payload: dict) -> tuple[list[dict], dict[str, int], bool]:
    elements = payload.get("elements")
    if not isinstance(elements, list):
        raise ProviderTransportError("OpenStreetMap did not return a feature list.")
    buildings: list[dict] = []
    roads: list[dict] = []
    amenities: list[dict] = []
    truncated = False
    for element in elements:
        if not isinstance(element, dict):
            continue
        tags = element.get("tags") if isinstance(element.get("tags"), dict) else {}
        kind = element.get("type")
        element_id = element.get("id")
        osm_id = ""
        if kind in {"node", "way", "relation"} and isinstance(element_id, int):
            osm_id = f"{kind}/{element_id}"
        if kind == "node" and tags.get("amenity") and len(amenities) < MAX_AMENITIES:
            lon = element.get("lon")
            lat = element.get("lat")
            if isinstance(lon, (int, float)) and isinstance(lat, (int, float)):
                amenities.append(
                    {
                        "type": "Feature",
                        "properties": {
                            "kind": "amenity",
                            "amenity": str(tags.get("amenity"))[:64],
                            "name": str(tags.get("name") or "")[:120],
                            "osm_id": osm_id,
                            "source": "OpenStreetMap",
                        },
                        "geometry": {"type": "Point", "coordinates": [float(lon), float(lat)]},
                    }
                )
            elif len(amenities) >= MAX_AMENITIES:
                truncated = True
            continue
        ring = _ring(element.get("geometry") or [])
        if ring is None:
            continue
        if tags.get("building") and len(buildings) < MAX_BUILDINGS:
            if ring[0] != ring[-1]:
                ring.append(ring[0])
            if len(ring) < 4:
                continue
            height, basis, note = height_fields(tags)
            buildings.append(
                {
                    "type": "Feature",
                    "properties": {
                        "kind": "building",
                        "height_m": height,
                        "height_basis": basis,
                        "height_measured": False,
                        "height_note": note,
                        "name": str(tags.get("name") or "")[:120],
                        "osm_id": osm_id,
                        "source": "OpenStreetMap",
                    },
                    "geometry": {"type": "Polygon", "coordinates": [ring]},
                }
            )
        elif tags.get("highway") and len(roads) < MAX_ROADS:
            roads.append(
                {
                    "type": "Feature",
                    "properties": {
                        "kind": "road",
                        "highway": str(tags.get("highway"))[:64],
                        "name": str(tags.get("name") or "")[:120],
                        "osm_id": osm_id,
                        "source": "OpenStreetMap",
                    },
                    "geometry": {"type": "LineString", "coordinates": ring},
                }
            )
        elif len(buildings) >= MAX_BUILDINGS or len(roads) >= MAX_ROADS:
            truncated = True
    counts = {
        "building_count": len(buildings),
        "road_count": len(roads),
        "amenity_count": len(amenities),
    }
    return buildings + roads + amenities, counts, truncated


def _query_box(
    extent: tuple[float, float, float, float],
) -> tuple[float, float, float, float, bool]:
    west, south, east, north = extent
    pad = 0.004
    west, south, east, north = west - pad, south - pad, east + pad, north + pad
    clipped = False
    if east - west > 0.03:
        center = (extent[0] + extent[2]) / 2
        west, east = center - 0.015, center + 0.015
        clipped = True
    if north - south > 0.03:
        center = (extent[1] + extent[3]) / 2
        south, north = center - 0.015, center + 0.015
        clipped = True
    return west, south, east, north, clipped


def _cache_file(key: str) -> Path:
    root = Path(tempfile.gettempdir()) / "glitters-godseye-osm"
    root.mkdir(parents=True, exist_ok=True)
    path = (root / f"{key}.json").resolve()
    if path.parent != root.resolve():
        raise ProviderTransportError("The map-context cache path was rejected.")
    return path


def _fetch_overpass(query: str) -> tuple[str, bytes]:
    """One query. A second public endpoint is used only if the first cannot answer."""

    body = urlencode({"data": query}).encode()
    last_error = ProviderTransportError("OpenStreetMap could not be reached.")
    for url in OVERPASS_URLS:
        try:
            status, payload = fetch_https(
                url,
                allowed_hosts=OVERPASS_HOSTS,
                max_bytes=1_500_000,
                timeout=25,
                method="POST",
                body=body,
                content_type="application/x-www-form-urlencoded",
            )
        except ProviderTransportError as exc:
            last_error = exc
            continue
        if status == 200:
            return url, payload
        last_error = ProviderTransportError(f"OpenStreetMap returned HTTP {status}.", status)
    raise last_error


def load_surroundings(extent: tuple[float, float, float, float]) -> dict:
    west, south, east, north, clipped = _query_box(extent)
    digest = hashlib.sha256(f"{west:.4f},{south:.4f},{east:.4f},{north:.4f}".encode()).hexdigest()
    cached = _cache_file(digest[:24])
    if cached.exists():
        payload = json.loads(cached.read_text(encoding="utf-8"))
        payload["from_cache"] = True
        return payload
    query = (
        f"[out:json][timeout:20];("
        f'way["building"]({south:.5f},{west:.5f},{north:.5f},{east:.5f});'
        f'way["highway"]({south:.5f},{west:.5f},{north:.5f},{east:.5f});'
        f'node["amenity"~"^(school|hospital|clinic|marketplace|pharmacy)$"]'
        f"({south:.5f},{west:.5f},{north:.5f},{east:.5f});"
        f");out tags geom 450;"
    )
    endpoint, body = _fetch_overpass(query)
    try:
        parsed = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ProviderTransportError(
            "OpenStreetMap returned a response that was not JSON."
        ) from exc
    features, counts, truncated = _features(parsed)
    note = LIMITATION
    if clipped:
        note = f"{note} The query was clipped to a neighborhood around the centroid."
    if truncated:
        note = f"{note} Extra features were omitted so the map stays small."
    payload = {
        "downloaded": True,
        "from_cache": False,
        "source_name": "OpenStreetMap",
        "endpoint": endpoint,
        "license": "ODbL-1.0",
        "attribution": "© OpenStreetMap contributors",
        "queried_bbox": [round(west, 5), round(south, 5), round(east, 5), round(north, 5)],
        "clipped": clipped,
        "truncated": truncated,
        "limitation": note,
        "land_cover": None,
        "land_cover_note": "No land-cover raster was downloaded for this outline.",
        "geojson": {"type": "FeatureCollection", "features": features},
        **counts,
    }
    cached.write_text(json.dumps(payload), encoding="utf-8")
    return payload
