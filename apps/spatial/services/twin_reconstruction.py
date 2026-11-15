"""3D Volumetric Digital Twin Reconstruction Engine.

Transforms 2D building footprints into semantically tagged, metric 3D meshes
with explicit height provenance tracking (OGC / CityGML LoD 1).
"""

from __future__ import annotations

import math
from typing import Any

from django.contrib.gis.geos import GEOSGeometry, MultiPolygon, Polygon

from apps.spatial.domain import BuildingClassification, HeightSource
from apps.spatial.models import Building
from apps.spatial.services.earcut import earcut

WGS84_A = 6378137.0  # WGS84 semi-major axis in meters
WGS84_F = 1.0 / 298.257223563  # WGS84 flattening
WGS84_E2 = 2.0 * WGS84_F - WGS84_F * WGS84_F  # First eccentricity squared


def determine_building_height(building: Building) -> tuple[float, str, bool]:
    """
    Deterministically resolves building height, strictly recording provenance.

    Returns:
        (height_m, height_source, is_placeholder)
    """
    # 1. Direct explicit height
    if building.height_m is not None and float(building.height_m) > 0:
        height = round(float(building.height_m), 2)
        source = (
            HeightSource.SURVEY_VERIFIED_HEIGHT
            if building.is_human_verified
            else HeightSource.SOURCE_REPORTED_HEIGHT
        )
        return height, source, False

    # 2. Storey-level derived height
    if building.levels_above_ground is not None and building.levels_above_ground > 0:
        levels = int(building.levels_above_ground)
        if building.classification in {
            BuildingClassification.COMMERCIAL,
            BuildingClassification.INDUSTRIAL,
        }:
            level_height = 3.5
        else:
            level_height = 3.0
        height = round(levels * level_height, 2)
        return height, HeightSource.LEVEL_DERIVED_HEIGHT, False

    # 3. Honest fallback - never invent ground truth
    if building.classification == BuildingClassification.COMMERCIAL:
        fallback = 4.5
    elif building.classification == BuildingClassification.INDUSTRIAL:
        fallback = 6.0
    elif building.classification == BuildingClassification.INSTITUTIONAL:
        fallback = 4.0
    else:
        fallback = 3.5

    return fallback, HeightSource.UNKNOWN_HEIGHT, True


def geodetic_to_ecef(lon_deg: float, lat_deg: float, height_m: float = 0.0) -> tuple[float, float, float]:
    """Converts WGS84 longitude, latitude, height into Earth-Centered Earth-Fixed (ECEF) coordinates."""
    lam = math.radians(lon_deg)
    phi = math.radians(lat_deg)
    sin_phi = math.sin(phi)
    cos_phi = math.cos(phi)
    sin_lam = math.sin(lam)
    cos_lam = math.cos(lam)

    n = WGS84_A / math.sqrt(1.0 - WGS84_E2 * sin_phi * sin_phi)
    x = (n + height_m) * cos_phi * cos_lam
    y = (n + height_m) * cos_phi * sin_lam
    z = (n * (1.0 - WGS84_E2) + height_m) * sin_phi
    return x, y, z


def compute_enu_transform_matrix(
    origin_lon_deg: float, origin_lat_deg: float, origin_height_m: float = 0.0
) -> list[float]:
    """
    Computes 4x4 column-major affine transform matrix converting local ENU (East-North-Up)
    Cartesian coordinates into WGS84 ECEF coordinates.
    """
    lam = math.radians(origin_lon_deg)
    phi = math.radians(origin_lat_deg)
    sin_phi = math.sin(phi)
    cos_phi = math.cos(phi)
    sin_lam = math.sin(lam)
    cos_lam = math.cos(lam)

    # East unit vector in ECEF
    e_x = -sin_lam
    e_y = cos_lam
    e_z = 0.0

    # North unit vector in ECEF
    n_x = -sin_phi * cos_lam
    n_y = -sin_phi * sin_lam
    n_z = cos_phi

    # Up unit vector in ECEF
    u_x = cos_phi * cos_lam
    u_y = cos_phi * sin_lam
    u_z = sin_phi

    # Origin in ECEF
    x0, y0, z0 = geodetic_to_ecef(origin_lon_deg, origin_lat_deg, origin_height_m)

    # Column-major 4x4 matrix
    return [
        e_x, e_y, e_z, 0.0,
        n_x, n_y, n_z, 0.0,
        u_x, u_y, u_z, 0.0,
        x0,  y0,  z0,  1.0,
    ]


def project_to_local_enu(
    lon_deg: float, lat_deg: float, origin_lon_deg: float, origin_lat_deg: float
) -> tuple[float, float]:
    """Projects geographic coordinates into local metric East-North displacement from origin."""
    d_lon_rad = math.radians(lon_deg - origin_lon_deg)
    d_lat_rad = math.radians(lat_deg - origin_lat_deg)
    origin_lat_rad = math.radians(origin_lat_deg)

    x = d_lon_rad * WGS84_A * math.cos(origin_lat_rad)
    y = d_lat_rad * WGS84_A
    return x, y


class MeshData:
    def __init__(self):
        self.positions: list[float] = []  # 3 floats per vertex [x, y, z]
        self.normals: list[float] = []    # 3 floats per vertex [nx, ny, nz]
        self.batch_ids: list[float] = []   # 1 float per vertex
        self.indices: list[int] = []       # 3 ints per triangle
        self.min_bounds = [float("inf"), float("inf"), float("inf")]
        self.max_bounds = [-float("inf"), -float("inf"), -float("inf")]

    def add_vertex(self, x: float, y: float, z: float, nx: float, ny: float, nz: float, batch_id: float) -> int:
        idx = len(self.positions) // 3
        self.positions.extend([x, y, z])
        self.normals.extend([nx, ny, nz])
        self.batch_ids.append(batch_id)

        if x < self.min_bounds[0]: self.min_bounds[0] = x
        if y < self.min_bounds[1]: self.min_bounds[1] = y
        if z < self.min_bounds[2]: self.min_bounds[2] = z
        if x > self.max_bounds[0]: self.max_bounds[0] = x
        if y > self.max_bounds[1]: self.max_bounds[1] = y
        if z > self.max_bounds[2]: self.max_bounds[2] = z

        return idx

    def add_triangle(self, i1: int, i2: int, i3: int):
        self.indices.extend([i1, i2, i3])


def extrude_building_polygon(
    poly: Polygon,
    height_m: float,
    origin_lon: float,
    origin_lat: float,
    batch_id: float,
    mesh: MeshData,
    base_elevation_m: float = 0.0,
):
    """
    Extrudes a 2D Polygon (with optional holes) into a 3D manifold mesh.
    Adds vertices, normals, and triangle indices directly to `mesh`.
    """
    roof_z = base_elevation_m + height_m
    floor_z = base_elevation_m

    # 1. Process exterior and interior rings
    rings = [poly[i] for i in range(len(poly))]

    # Convert rings to local ENU coordinates
    local_rings: list[list[tuple[float, float]]] = []
    for r in rings:
        coords = list(r.coords)
        if len(coords) > 1 and coords[0] == coords[-1]:
            coords = coords[:-1]
        ring_pts = [project_to_local_enu(pt[0], pt[1], origin_lon, origin_lat) for pt in coords]
        local_rings.append(ring_pts)

    if not local_rings or len(local_rings[0]) < 3:
        return

    # 2. Extrude vertical walls for each ring
    for ring_idx, ring in enumerate(local_rings):
        is_outer = (ring_idx == 0)
        n_pts = len(ring)
        for i in range(n_pts):
            p0 = ring[i]
            p1 = ring[(i + 1) % n_pts]

            dx = p1[0] - p0[0]
            dy = p1[1] - p0[1]
            length = math.sqrt(dx * dx + dy * dy)
            if length < 1e-6:
                continue

            # Outward normal for CCW outer ring: (dy/L, -dx/L, 0)
            # Inward normal for holes
            nx = dy / length if is_outer else -dy / length
            ny = -dx / length if is_outer else dx / length
            nz = 0.0

            # 4 vertices for wall quad
            v0 = mesh.add_vertex(p0[0], p0[1], floor_z, nx, ny, nz, batch_id)
            v1 = mesh.add_vertex(p1[0], p1[1], floor_z, nx, ny, nz, batch_id)
            v2 = mesh.add_vertex(p1[0], p1[1], roof_z, nx, ny, nz, batch_id)
            v3 = mesh.add_vertex(p0[0], p0[1], roof_z, nx, ny, nz, batch_id)

            if is_outer:
                mesh.add_triangle(v0, v1, v2)
                mesh.add_triangle(v0, v2, v3)
            else:
                # Invert winding for hole walls
                mesh.add_triangle(v0, v2, v1)
                mesh.add_triangle(v0, v3, v2)

    # 3. Triangulate roof cap and floor cap using earcut
    flat_data: list[float] = []
    hole_indices: list[int] = []
    vertex_count = 0

    for r_idx, ring in enumerate(local_rings):
        if r_idx > 0:
            hole_indices.append(vertex_count)
        for pt in ring:
            flat_data.extend([pt[0], pt[1]])
            vertex_count += 1

    tri_indices = earcut(flat_data, hole_indices, dim=2)

    # All unique 2D points in order
    all_pts: list[tuple[float, float]] = []
    for ring in local_rings:
        all_pts.extend(ring)

    # Add roof cap (normal +Z)
    roof_vert_map: dict[int, int] = {}
    for i in range(0, len(tri_indices), 3):
        t0, t1, t2 = tri_indices[i], tri_indices[i + 1], tri_indices[i + 2]
        for t in (t0, t1, t2):
            if t not in roof_vert_map:
                pt = all_pts[t]
                roof_vert_map[t] = mesh.add_vertex(
                    pt[0], pt[1], roof_z, 0.0, 0.0, 1.0, batch_id
                )
        mesh.add_triangle(roof_vert_map[t0], roof_vert_map[t1], roof_vert_map[t2])

    # Add floor cap (normal -Z, inverted winding)
    floor_vert_map: dict[int, int] = {}
    for i in range(0, len(tri_indices), 3):
        t0, t1, t2 = tri_indices[i], tri_indices[i + 1], tri_indices[i + 2]
        for t in (t0, t1, t2):
            if t not in floor_vert_map:
                pt = all_pts[t]
                floor_vert_map[t] = mesh.add_vertex(
                    pt[0], pt[1], floor_z, 0.0, 0.0, -1.0, batch_id
                )
        mesh.add_triangle(floor_vert_map[t0], floor_vert_map[t2], floor_vert_map[t1])


def extrude_building(
    building: Building,
    origin_lon: float,
    origin_lat: float,
    batch_id: float,
    mesh: MeshData,
) -> tuple[float, str, bool, int]:
    """
    Extrudes a Building model instance into the provided MeshData.
    Returns: (height_m, height_source, is_placeholder, triangle_count)
    """
    height_m, height_source, is_placeholder = determine_building_height(building)
    start_tri_count = len(mesh.indices) // 3

    geom = building.geom
    if isinstance(geom, MultiPolygon):
        for poly in geom:
            extrude_building_polygon(poly, height_m, origin_lon, origin_lat, batch_id, mesh)
    elif isinstance(geom, Polygon):
        extrude_building_polygon(geom, height_m, origin_lon, origin_lat, batch_id, mesh)

    tri_count = (len(mesh.indices) // 3) - start_tri_count
    return height_m, height_source, is_placeholder, tri_count
