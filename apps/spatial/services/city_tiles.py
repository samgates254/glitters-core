"""Extruded OpenStreetMap footprints as a 3D Tiles 1.0 b3dm tileset.

Every building keeps its height basis. A mapped tag, a level-derived assumption,
and a single-storey assumption stay distinct. ``height_measured`` is false.
The mesh is a procedural extrusion. It is not photogrammetry, a reconstruction,
or a survey. The coarse level is a bounding box of the same footprint, labeled
as a bounding box.
"""

from __future__ import annotations

import json
import math
import struct

from apps.spatial.services.city_models import ATTRIBUTION, LICENSE, validate_city_document
from apps.spatial.services.compute.contracts import PermanentComputeError
from apps.spatial.services.compute.gltf_job import canonical_bytes, sha256_hex
from apps.spatial.services.earcut import earcut
from apps.spatial.services.quantized_mesh import ecef

MAX_RING = 48
SPLIT_AT = 40


def _closed_ring(ring: list) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for item in ring:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        lon, lat = float(item[0]), float(item[1])
        if math.isfinite(lon) and math.isfinite(lat):
            points.append((lon, lat))
    if len(points) >= 2 and points[0] == points[-1]:
        points = points[:-1]
    if len(points) > MAX_RING:
        step = len(points) / MAX_RING
        points = [points[int(index * step)] for index in range(MAX_RING)]
    return points


def _buildings(document: dict) -> list[dict]:
    features = document["geojson"]["features"]
    rows: list[dict] = []
    for feature in features:
        properties = feature.get("properties") or {}
        if properties.get("kind") != "building":
            continue
        rings = feature.get("geometry", {}).get("coordinates") or []
        if not rings:
            continue
        outer = _closed_ring(rings[0])
        if len(outer) < 3:
            continue
        holes = [_closed_ring(ring) for ring in rings[1:]]
        holes = [ring for ring in holes if len(ring) >= 3]
        longitude = sum(point[0] for point in outer) / len(outer)
        latitude = sum(point[1] for point in outer) / len(outer)
        rows.append(
            {
                "osm_id": str(properties.get("osm_id") or ""),
                "name": str(properties.get("name") or ""),
                "height_m": float(properties["height_m"]),
                "height_basis": str(properties["height_basis"]),
                "height_note": str(properties.get("height_note") or ""),
                "longitude": longitude,
                "latitude": latitude,
                "outer": outer,
                "holes": holes,
            }
        )
    if not rows:
        raise PermanentComputeError("The city tileset has no extruded footprints.")
    return rows


def _box(building: dict) -> list[tuple[float, float]]:
    longitudes = [point[0] for point in building["outer"]]
    latitudes = [point[1] for point in building["outer"]]
    west, east = min(longitudes), max(longitudes)
    south, north = min(latitudes), max(latitudes)
    if east == west:
        east += 1e-6
    if north == south:
        north += 1e-6
    return [(west, south), (east, south), (east, north), (west, north)]


def _up(position: tuple[float, float, float]) -> tuple[float, float, float]:
    magnitude = math.sqrt(position[0] ** 2 + position[1] ** 2 + position[2] ** 2)
    if magnitude == 0:
        return (0.0, 0.0, 1.0)
    return (position[0] / magnitude, position[1] / magnitude, position[2] / magnitude)


def _triangulate(
    ring: list[tuple[float, float]], holes: list[list[tuple[float, float]]]
) -> list[int]:
    points = list(ring)
    hole_indices: list[int] = []
    for hole in holes:
        hole_indices.append(len(points))
        points.extend(hole)
    longitude = sum(point[0] for point in points) / len(points)
    latitude = sum(point[1] for point in points) / len(points)
    scale = math.cos(math.radians(latitude))
    flat: list[float] = []
    for lon, lat in points:
        flat.append((lon - longitude) * 111_320.0 * scale)
        flat.append((lat - latitude) * 110_540.0)
    return earcut(flat, hole_indices or None, 2)


def _append_extrusion(
    building: dict,
    ground_height,
    batch_id: int,
    *,
    simplify: bool,
    positions: list[tuple[float, float, float]],
    normals: list[tuple[float, float, float]],
    batches: list[int],
    indices: list[int],
) -> None:
    ring = _box(building) if simplify else building["outer"]
    holes = [] if simplify else building["holes"]
    cap = _triangulate(ring, holes)
    if len(cap) < 3:
        ring = _box(building)
        holes = []
        cap = _triangulate(ring, holes)
    if len(cap) < 3:
        raise PermanentComputeError("A building footprint could not be triangulated.")
    base = len(positions)
    grounds = [float(ground_height(lon, lat)) for lon, lat in ring]
    for hole in holes:
        grounds.extend(float(ground_height(lon, lat)) for lon, lat in hole)
    points = ring + [point for hole in holes for point in hole]
    for (lon, lat), ground in zip(points, grounds, strict=True):
        bottom = ecef(lon, lat, ground)
        top = ecef(lon, lat, ground + building["height_m"])
        positions.extend((bottom, top))
        normals.extend((_up(bottom), _up(top)))
        batches.extend((batch_id, batch_id))
    for corner in range(0, len(cap), 3):
        for offset in (0, 1, 2):
            indices.append(base + cap[corner + offset] * 2 + 1)
        for offset in (0, 2, 1):
            indices.append(base + cap[corner + offset] * 2)
    outline = list(ring)
    for hole in holes:
        outline.extend(hole)
    # Walls follow each ring, including holes, using the original point order.
    cursor = 0
    for part in [ring, *holes]:
        count = len(part)
        for edge in range(count):
            a_index = base + (cursor + edge) * 2
            b_index = base + (cursor + (edge + 1) % count) * 2
            indices.extend((a_index, b_index, b_index + 1, a_index, b_index + 1, a_index + 1))
        cursor += count


def _pad_json(document: dict) -> bytes:
    raw = json.dumps(document, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return raw + b" " * ((-len(raw)) % 8)


def _align(blob: bytearray, alignment: int) -> None:
    while len(blob) % alignment:
        blob.append(0)


def build_glb(
    positions: list[tuple[float, float, float]],
    normals: list[tuple[float, float, float]],
    batches: list[int],
    indices: list[int],
) -> tuple[bytes, tuple[float, float, float]]:
    count = len(positions)
    if count < 3 or len(normals) != count or len(batches) != count or len(indices) < 3:
        raise PermanentComputeError("The city mesh has no triangles.")
    if count > 65535 or max(indices) >= count:
        raise PermanentComputeError("The city mesh does not fit in uint16 indices.")
    center = (
        sum(item[0] for item in positions) / count,
        sum(item[1] for item in positions) / count,
        sum(item[2] for item in positions) / count,
    )
    relative = [
        (item[0] - center[0], item[1] - center[1], item[2] - center[2]) for item in positions
    ]
    minimum = [min(item[axis] for item in relative) for axis in range(3)]
    maximum = [max(item[axis] for item in relative) for axis in range(3)]
    binary = bytearray()
    position_offset = 0
    binary += struct.pack(f"<{count * 3}f", *[axis for item in relative for axis in item])
    _align(binary, 4)
    normal_offset = len(binary)
    binary += struct.pack(f"<{count * 3}f", *[axis for item in normals for axis in item])
    _align(binary, 4)
    batch_offset = len(binary)
    binary += struct.pack(f"<{count}H", *batches)
    _align(binary, 4)
    index_offset = len(binary)
    binary += struct.pack(f"<{len(indices)}H", *indices)
    _align(binary, 4)
    document = {
        "asset": {"version": "2.0", "generator": "glitters-extruded-footprint"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [
            {
                "primitives": [
                    {
                        "attributes": {"POSITION": 0, "NORMAL": 1, "_BATCHID": 2},
                        "indices": 3,
                        "mode": 4,
                    }
                ]
            }
        ],
        "accessors": [
            {
                "bufferView": 0,
                "componentType": 5126,
                "count": count,
                "type": "VEC3",
                "min": minimum,
                "max": maximum,
            },
            {"bufferView": 1, "componentType": 5126, "count": count, "type": "VEC3"},
            {"bufferView": 2, "componentType": 5123, "count": count, "type": "SCALAR"},
            {
                "bufferView": 3,
                "componentType": 5123,
                "count": len(indices),
                "type": "SCALAR",
            },
        ],
        "bufferViews": [
            {
                "buffer": 0,
                "byteOffset": position_offset,
                "byteLength": normal_offset - position_offset,
                "target": 34962,
            },
            {
                "buffer": 0,
                "byteOffset": normal_offset,
                "byteLength": batch_offset - normal_offset,
                "target": 34962,
            },
            {
                "buffer": 0,
                "byteOffset": batch_offset,
                "byteLength": index_offset - batch_offset,
                "target": 34962,
            },
            {
                "buffer": 0,
                "byteOffset": index_offset,
                "byteLength": len(binary) - index_offset,
                "target": 34963,
            },
        ],
        "buffers": [{"byteLength": len(binary)}],
        "extras": {
            "representation": "extruded-footprint",
            "photogrammetry": False,
            "rtc_center": list(center),
        },
    }
    json_chunk = json.dumps(document, separators=(",", ":")).encode("utf-8")
    json_chunk += b" " * ((-len(json_chunk)) % 4)
    body = struct.pack("<II", len(json_chunk), 0x4E4F534A) + json_chunk
    body += struct.pack("<II", len(binary), 0x004E4942) + bytes(binary)
    return struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body, center


def build_b3dm(buildings: list[dict], glb: bytes, center: tuple[float, float, float]) -> bytes:
    feature = _pad_json({"BATCH_LENGTH": len(buildings), "RTC_CENTER": list(center)})
    batch = _pad_json(
        {
            "osm_id": [item["osm_id"] for item in buildings],
            "name": [item["name"] for item in buildings],
            "height_m": [item["height_m"] for item in buildings],
            "height_basis": [item["height_basis"] for item in buildings],
            "height_note": [item["height_note"] for item in buildings],
            "height_measured": [False for _item in buildings],
            "model_kind": ["extruded_footprint" for _item in buildings],
            "geometry_lod": [item["geometry_lod"] for item in buildings],
        }
    )
    total = 28 + len(feature) + len(batch) + len(glb)
    header = struct.pack("<4s6I", b"b3dm", 1, total, len(feature), 0, len(batch), 0)
    return header + feature + batch + glb


def _mesh(
    buildings: list[dict], ground_height, *, simplify: bool
) -> tuple[bytes, tuple[float, float, float]]:
    positions: list[tuple[float, float, float]] = []
    normals: list[tuple[float, float, float]] = []
    batches: list[int] = []
    indices: list[int] = []
    tagged = []
    for batch_id, building in enumerate(buildings):
        _append_extrusion(
            building,
            ground_height,
            batch_id,
            simplify=simplify,
            positions=positions,
            normals=normals,
            batches=batches,
            indices=indices,
        )
        tagged.append({**building, "geometry_lod": "bounding-box" if simplify else "footprint"})
    glb, center = build_glb(positions, normals, batches, indices)
    return build_b3dm(tagged, glb, center), center


def _region(buildings: list[dict], ground_height) -> list[float]:
    longitudes = [item["longitude"] for item in buildings]
    latitudes = [item["latitude"] for item in buildings]
    grounds = [float(ground_height(item["longitude"], item["latitude"])) for item in buildings]
    lowest = min(grounds)
    highest = max(
        ground + item["height_m"] for ground, item in zip(grounds, buildings, strict=True)
    )
    return [
        math.radians(min(longitudes) - 0.0002),
        math.radians(min(latitudes) - 0.0002),
        math.radians(max(longitudes) + 0.0002),
        math.radians(max(latitudes) + 0.0002),
        lowest - 2.0,
        highest + 2.0,
    ]


def _groups(buildings: list[dict]) -> list[list[dict]]:
    if len(buildings) <= SPLIT_AT:
        return [buildings]
    longitudes = sorted(item["longitude"] for item in buildings)
    latitudes = sorted(item["latitude"] for item in buildings)
    mid_lon = longitudes[len(longitudes) // 2]
    mid_lat = latitudes[len(latitudes) // 2]
    groups: list[list[dict]] = [[], [], [], []]
    for item in buildings:
        east = 0 if item["longitude"] < mid_lon else 1
        north = 0 if item["latitude"] < mid_lat else 2
        index = east + north
        groups[index].append(item)
    present = [group for group in groups if group]
    if len(present) <= 1:
        return [buildings]
    return present


def build_city_tiles(document: dict, ground_height) -> dict[str, bytes]:
    """Build a tileset.json bundle. ``ground_height`` returns an ellipsoid height."""

    validate_city_document(document)
    buildings = _buildings(document)
    files: dict[str, bytes] = {}
    root_bytes, _center = _mesh(buildings, ground_height, simplify=True)
    files["lod0.b3dm"] = root_bytes
    children = []
    for index, group in enumerate(_groups(buildings)):
        payload, _group_center = _mesh(group, ground_height, simplify=False)
        name = f"c{index}.b3dm"
        files[name] = payload
        children.append(
            {
                "boundingVolume": {"region": _region(group, ground_height)},
                "geometricError": 0,
                "content": {"uri": name},
            }
        )
    tileset = {
        "asset": {"version": "1.0", "gltfUpAxis": "Z"},
        "geometricError": 240,
        "root": {
            "boundingVolume": {"region": _region(buildings, ground_height)},
            "geometricError": 80,
            "refine": "REPLACE",
            "content": {"uri": "lod0.b3dm"},
            "children": children,
        },
        "extras": {
            "representation": "extruded-footprint",
            "photogrammetry": False,
            "reconstruction": False,
            "survey": False,
            "height_measured": False,
            "attribution": ATTRIBUTION,
            "license": LICENSE,
            "source_name": "OpenStreetMap",
            "place": document.get("place"),
            "building_count": len(buildings),
            "height_basis_counts": document.get("height_basis_counts"),
            "limitation": (
                "Procedural extrusions of mapped footprints. "
                "A bounding box is the coarse level. Neither level is photogrammetry."
            ),
        },
    }
    files["tileset.json"] = json.dumps(tileset, separators=(",", ":")).encode("utf-8")
    listed = [
        {"path": path, "sha256": sha256_hex(payload), "bytes": len(payload)}
        for path, payload in sorted(files.items())
    ]
    files["manifest.json"] = canonical_bytes(
        {"format": "glitters-bundle-v1", "kind": "3dtiles", "files": listed}
    )
    return files
