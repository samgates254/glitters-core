"""Cesium quantized-mesh-1.0 tiles.

The byte layout follows ``createQuantizedMeshTerrainData`` in Cesium
TerrainProvider: little-endian header, zigzag-delta u then v then height,
high-water-mark triangle indices, and raw edge indices. Cesium builds skirts
from those edge indices. This module does not add a second skirt mesh and does
not write vertex-normal, water-mask, or metadata extensions.

Heights passed in are already in the rendering frame. This module does not
interpret a vertical datum.
"""

from __future__ import annotations

import math
import struct

from apps.spatial.services.compute.contracts import PermanentComputeError

WGS84_A = 6378137.0
WGS84_F = 1.0 / 298.257223563
WGS84_B = WGS84_A * (1.0 - WGS84_F)
WGS84_E2 = WGS84_F * (2.0 - WGS84_F)
QUANTIZED_MAX = 32767


def ecef(longitude: float, latitude: float, height: float) -> tuple[float, float, float]:
    lon = math.radians(longitude)
    lat = math.radians(latitude)
    sin_lat = math.sin(lat)
    cos_lat = math.cos(lat)
    radius = WGS84_A / math.sqrt(1.0 - WGS84_E2 * sin_lat * sin_lat)
    return (
        (radius + height) * cos_lat * math.cos(lon),
        (radius + height) * cos_lat * math.sin(lon),
        (radius * (1.0 - WGS84_E2) + height) * sin_lat,
    )


def _dot(left: tuple[float, float, float], right: tuple[float, float, float]) -> float:
    return left[0] * right[0] + left[1] * right[1] + left[2] * right[2]


def _cross(
    left: tuple[float, float, float], right: tuple[float, float, float]
) -> tuple[float, float, float]:
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def _mag(value: tuple[float, float, float]) -> float:
    return math.sqrt(_dot(value, value))


def _scale(value: tuple[float, float, float], factor: float) -> tuple[float, float, float]:
    return (value[0] * factor, value[1] * factor, value[2] * factor)


def _norm(value: tuple[float, float, float]) -> tuple[float, float, float]:
    magnitude = _mag(value)
    if magnitude == 0:
        raise PermanentComputeError("A terrain direction was zero.")
    return _scale(value, 1.0 / magnitude)


def _scaled_space(position: tuple[float, float, float]) -> tuple[float, float, float]:
    return (position[0] / WGS84_A, position[1] / WGS84_A, position[2] / WGS84_B)


def horizon_occlusion_point(
    positions: list[tuple[float, float, float]],
) -> tuple[float, float, float]:
    """Ellipsoid-scaled horizon point. A failed solution stays visible, not culled."""

    if not positions:
        raise PermanentComputeError("A terrain tile has no positions.")
    direction = (
        sum(item[0] for item in positions) / len(positions),
        sum(item[1] for item in positions) / len(positions),
        sum(item[2] for item in positions) / len(positions),
    )
    scaled_direction = _norm(_scaled_space(direction))
    result = 0.0
    for position in positions:
        scaled = _scaled_space(position)
        magnitude_squared = _dot(scaled, scaled)
        magnitude = math.sqrt(magnitude_squared)
        if magnitude == 0:
            return _scale(scaled_direction, 2.0)
        direction_at = _scale(scaled, 1.0 / magnitude)
        magnitude_squared = max(1.0, magnitude_squared)
        magnitude = max(1.0, magnitude)
        cos_alpha = _dot(direction_at, scaled_direction)
        sin_alpha = _mag(_cross(direction_at, scaled_direction))
        cos_beta = 1.0 / magnitude
        sin_beta = math.sqrt(max(0.0, magnitude_squared - 1.0)) * cos_beta
        denominator = cos_alpha * cos_beta - sin_alpha * sin_beta
        if denominator <= 1e-12:
            return _scale(scaled_direction, 2.0)
        candidate = 1.0 / denominator
        if candidate < 0 or not math.isfinite(candidate):
            return _scale(scaled_direction, 2.0)
        result = max(result, candidate)
    if result <= 0 or not math.isfinite(result):
        return _scale(scaled_direction, 2.0)
    return _scale(scaled_direction, result)


def zigzag_encode(value: int) -> int:
    return ((value << 1) ^ (value >> 31)) & 0xFFFF


def zigzag_decode(value: int) -> int:
    value &= 0xFFFF
    return (value >> 1) ^ -(value & 1)


def _quantize(numerator: float, span: float) -> int:
    if span <= 0:
        return 0
    coded = int(round((numerator / span) * QUANTIZED_MAX))
    return min(QUANTIZED_MAX, max(0, coded))


def _high_water(indices: list[int]) -> list[int]:
    highest = 0
    codes: list[int] = []
    for index in indices:
        code = highest - index
        if code < 0:
            raise PermanentComputeError("Terrain indices are not in encodable order.")
        codes.append(code)
        if code == 0:
            highest += 1
    return codes


def encode_mesh(
    *,
    longitudes: list[float],
    latitudes: list[float],
    heights: list[float],
    west: float,
    south: float,
    east: float,
    north: float,
    posts: int,
) -> bytes:
    """Encode one regular post grid. Posts are row-major, south to north, west to east."""

    count = posts * posts
    if posts < 2 or len(longitudes) != count or len(latitudes) != count or len(heights) != count:
        raise PermanentComputeError("The terrain post grid does not match its size.")
    if east <= west or north <= south:
        raise PermanentComputeError("The terrain tile bounds are empty.")
    if any(not math.isfinite(value) for value in heights):
        raise PermanentComputeError("A terrain post is not finite.")
    minimum = min(heights)
    maximum = max(heights)
    span_h = maximum - minimum
    u_values = [
        _quantize(longitudes[index] - west, east - west) for index in range(count)
    ]
    v_values = [
        _quantize(latitudes[index] - south, north - south) for index in range(count)
    ]
    h_values = [
        0 if span_h == 0 else _quantize(heights[index] - minimum, span_h) for index in range(count)
    ]
    for column in range(posts):
        u_values[column] = _quantize(column, posts - 1)
        u_values[(posts - 1) * posts + column] = _quantize(column, posts - 1)
        v_values[column] = 0
        v_values[(posts - 1) * posts + column] = QUANTIZED_MAX
    for row in range(posts):
        u_values[row * posts] = 0
        u_values[row * posts + posts - 1] = QUANTIZED_MAX
        v_values[row * posts] = _quantize(row, posts - 1)
        v_values[row * posts + posts - 1] = _quantize(row, posts - 1)

    triangles: list[int] = []
    for row in range(posts - 1):
        for column in range(posts - 1):
            base = row * posts + column
            triangles.extend(
                (base, base + 1, base + posts, base + 1, base + posts + 1, base + posts)
            )
    mapping: dict[int, int] = {}
    order: list[int] = []

    def mapped(index: int) -> int:
        found = mapping.get(index)
        if found is None:
            found = len(order)
            mapping[index] = found
            order.append(index)
        return found

    coded_indices = _high_water([mapped(index) for index in triangles])
    u_ordered = [u_values[index] for index in order]
    v_ordered = [v_values[index] for index in order]
    h_ordered = [h_values[index] for index in order]

    def edge(indices: list[int]) -> list[int]:
        return [mapping[index] for index in indices]

    west_edge = edge([row * posts for row in range(posts)])
    east_edge = edge([row * posts + posts - 1 for row in range(posts)])
    south_edge = edge(list(range(posts)))
    north_edge = edge([(posts - 1) * posts + column for column in range(posts)])

    positions = [ecef(longitudes[index], latitudes[index], heights[index]) for index in order]
    center = (
        sum(item[0] for item in positions) / len(positions),
        sum(item[1] for item in positions) / len(positions),
        sum(item[2] for item in positions) / len(positions),
    )
    radius = max(
        _mag((item[0] - center[0], item[1] - center[1], item[2] - center[2])) for item in positions
    )
    horizon = horizon_occlusion_point(positions)
    return _pack(
        center=center,
        minimum=minimum,
        maximum=maximum,
        sphere_center=center,
        radius=radius,
        horizon=horizon,
        u_values=u_ordered,
        v_values=v_ordered,
        h_values=h_ordered,
        indices=coded_indices,
        edges=(west_edge, south_edge, east_edge, north_edge),
    )


def _delta(values: list[int]) -> list[int]:
    previous = 0
    coded: list[int] = []
    for value in values:
        coded.append(zigzag_encode(value - previous))
        previous = value
    return coded


def _pack(
    *,
    center: tuple[float, float, float],
    minimum: float,
    maximum: float,
    sphere_center: tuple[float, float, float],
    radius: float,
    horizon: tuple[float, float, float],
    u_values: list[int],
    v_values: list[int],
    h_values: list[int],
    indices: list[int],
    edges: tuple[list[int], list[int], list[int], list[int]],
) -> bytes:
    vertex_count = len(u_values)
    if vertex_count > 65536:
        raise PermanentComputeError("A terrain tile has more vertices than the uint16 index path.")
    if len(indices) % 3 != 0:
        raise PermanentComputeError("Terrain triangles are incomplete.")
    payload = bytearray()
    payload += struct.pack("<3d", *center)
    payload += struct.pack("<2f", float(minimum), float(maximum))
    payload += struct.pack("<4d", *sphere_center, float(radius))
    payload += struct.pack("<3d", *horizon)
    payload += struct.pack("<I", vertex_count)
    payload += struct.pack(f"<{vertex_count}H", *_delta(u_values))
    payload += struct.pack(f"<{vertex_count}H", *_delta(v_values))
    payload += struct.pack(f"<{vertex_count}H", *_delta(h_values))
    if len(payload) % 2:
        payload += b"\x00"
    payload += struct.pack("<I", len(indices) // 3)
    payload += struct.pack(f"<{len(indices)}H", *indices)
    for edge in edges:
        payload += struct.pack("<I", len(edge))
        if edge:
            payload += struct.pack(f"<{len(edge)}H", *edge)
    return bytes(payload)


def decode_mesh(payload: bytes) -> dict:
    """Decode the same layout Cesium reads. Used to test the encoder."""

    if len(payload) < 92:
        raise PermanentComputeError("The terrain tile is shorter than its header.")
    view = memoryview(payload)
    offset = 0
    center = struct.unpack_from("<3d", view, offset)
    offset += 24
    minimum, maximum = struct.unpack_from("<2f", view, offset)
    offset += 8
    sphere = struct.unpack_from("<4d", view, offset)
    offset += 32
    horizon = struct.unpack_from("<3d", view, offset)
    offset += 24
    (vertex_count,) = struct.unpack_from("<I", view, offset)
    offset += 4
    if vertex_count < 3 or vertex_count > 65536:
        raise PermanentComputeError("The terrain tile vertex count is not usable.")
    needed = vertex_count * 6
    if offset + needed > len(payload):
        raise PermanentComputeError("The terrain tile vertex buffer is truncated.")

    def accumulate(start: int) -> list[int]:
        raw = struct.unpack_from(f"<{vertex_count}H", view, start)
        total = 0
        values: list[int] = []
        for item in raw:
            total += zigzag_decode(item)
            if total < 0 or total > 65535:
                raise PermanentComputeError("A terrain vertex delta left the uint16 range.")
            values.append(total)
        return values

    u_values = accumulate(offset)
    v_values = accumulate(offset + vertex_count * 2)
    h_values = accumulate(offset + vertex_count * 4)
    offset += needed
    if offset % 2:
        offset += 1
    if offset + 4 > len(payload):
        raise PermanentComputeError("The terrain tile index count is truncated.")
    (triangle_count,) = struct.unpack_from("<I", view, offset)
    offset += 4
    index_count = triangle_count * 3
    if offset + index_count * 2 > len(payload):
        raise PermanentComputeError("The terrain tile indices are truncated.")
    codes = struct.unpack_from(f"<{index_count}H", view, offset)
    offset += index_count * 2
    highest = 0
    indices: list[int] = []
    for code in codes:
        indices.append(highest - code)
        if code == 0:
            highest += 1
    edges: list[list[int]] = []
    for _name in ("west", "south", "east", "north"):
        if offset + 4 > len(payload):
            raise PermanentComputeError("The terrain tile edge index is truncated.")
        (edge_count,) = struct.unpack_from("<I", view, offset)
        offset += 4
        if edge_count < 2 or offset + edge_count * 2 > len(payload):
            raise PermanentComputeError("The terrain tile edge is truncated.")
        edges.append(list(struct.unpack_from(f"<{edge_count}H", view, offset)))
        offset += edge_count * 2
    if any(index < 0 or index >= vertex_count for index in indices):
        raise PermanentComputeError("A terrain index points outside the vertex buffer.")
    for edge in edges:
        if any(index < 0 or index >= vertex_count for index in edge):
            raise PermanentComputeError("A terrain edge index points outside the vertex buffer.")
    return {
        "center": center,
        "minimum_height": float(minimum),
        "maximum_height": float(maximum),
        "bounding_sphere": sphere,
        "horizon_occlusion_point": horizon,
        "u": u_values,
        "v": v_values,
        "height": h_values,
        "indices": indices,
        "edges": edges,
        "bytes_consumed": offset,
    }
