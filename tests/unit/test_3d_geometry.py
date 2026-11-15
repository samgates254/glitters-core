"""Unit tests for 3D digital twin geometry, triangulation, and height provenance."""

from __future__ import annotations

import math
from unittest.mock import MagicMock

import pytest
from django.contrib.gis.geos import MultiPolygon, Polygon

from apps.spatial.domain import BuildingClassification, HeightSource
from apps.spatial.models import Building
from apps.spatial.services.earcut import earcut
from apps.spatial.services.twin_reconstruction import (
    MeshData,
    compute_enu_transform_matrix,
    determine_building_height,
    extrude_building,
    extrude_building_polygon,
    geodetic_to_ecef,
    project_to_local_enu,
)


def test_earcut_simple_triangle():
    data = [0.0, 0.0, 10.0, 0.0, 5.0, 10.0]
    triangles = earcut(data)
    assert len(triangles) == 3
    assert set(triangles) == {0, 1, 2}


def test_earcut_quad():
    data = [0.0, 0.0, 10.0, 0.0, 10.0, 10.0, 0.0, 10.0]
    triangles = earcut(data)
    assert len(triangles) == 6  # 2 triangles * 3 indices


def test_earcut_concave_l_shape():
    data = [0.0, 0.0, 10.0, 0.0, 10.0, 5.0, 5.0, 5.0, 5.0, 10.0, 0.0, 10.0]
    triangles = earcut(data)
    assert len(triangles) == 12  # 4 triangles


def test_earcut_polygon_with_hole():
    # Outer 10x10 square, hole 4x4 square in middle
    # Outer: 4 points (0..3), Hole: 4 points (4..7)
    outer = [0.0, 0.0, 10.0, 0.0, 10.0, 10.0, 0.0, 10.0]
    hole = [3.0, 3.0, 7.0, 3.0, 7.0, 7.0, 3.0, 7.0]
    flat_data = outer + hole
    hole_indices = [4]

    triangles = earcut(flat_data, hole_indices)
    assert len(triangles) >= 6
    assert len(triangles) % 3 == 0


def test_determine_building_height_explicit_survey_verified():
    b = MagicMock(spec=Building)
    b.height_m = 24.50
    b.is_human_verified = True
    b.levels_above_ground = 7
    b.classification = BuildingClassification.COMMERCIAL

    h, src, placeholder = determine_building_height(b)
    assert h == 24.50
    assert src == HeightSource.SURVEY_VERIFIED_HEIGHT
    assert placeholder is False


def test_determine_building_height_explicit_source_reported():
    b = MagicMock(spec=Building)
    b.height_m = 12.00
    b.is_human_verified = False
    b.levels_above_ground = None
    b.classification = BuildingClassification.RESIDENTIAL

    h, src, placeholder = determine_building_height(b)
    assert h == 12.00
    assert src == HeightSource.SOURCE_REPORTED_HEIGHT
    assert placeholder is False


def test_determine_building_height_level_derived():
    # Commercial: 3.5m per level
    b_comm = MagicMock(spec=Building)
    b_comm.height_m = None
    b_comm.levels_above_ground = 4
    b_comm.classification = BuildingClassification.COMMERCIAL
    h, src, placeholder = determine_building_height(b_comm)
    assert h == 14.00
    assert src == HeightSource.LEVEL_DERIVED_HEIGHT
    assert placeholder is False

    # Residential: 3.0m per level
    b_res = MagicMock(spec=Building)
    b_res.height_m = None
    b_res.levels_above_ground = 3
    b_res.classification = BuildingClassification.RESIDENTIAL
    h, src, placeholder = determine_building_height(b_res)
    assert h == 9.00
    assert src == HeightSource.LEVEL_DERIVED_HEIGHT
    assert placeholder is False


def test_determine_building_height_unknown_fallback():
    b = MagicMock(spec=Building)
    b.height_m = None
    b.levels_above_ground = None
    b.classification = BuildingClassification.OTHER

    h, src, placeholder = determine_building_height(b)
    assert h == 3.50
    assert src == HeightSource.UNKNOWN_HEIGHT
    assert placeholder is True


def test_geodetic_to_ecef_mombasa():
    lon, lat = 39.6682, -4.0615
    x, y, z = geodetic_to_ecef(lon, lat, height_m=10.0)
    # Radius from Earth center should be ~ 6378 km
    r = math.sqrt(x * x + y * y + z * z)
    assert 6370000.0 < r < 6385000.0
    # Equator proximity (-4 deg lat) means z is small negative
    assert z < 0.0
    assert abs(z) < 500000.0


def test_compute_enu_transform_matrix_orthogonality():
    lon, lat = 39.6682, -4.0615
    m = compute_enu_transform_matrix(lon, lat, 0.0)
    assert len(m) == 16

    # Extract column vectors
    e = [m[0], m[1], m[2]]
    n = [m[4], m[5], m[6]]
    u = [m[8], m[9], m[10]]

    def dot(v1, v2):
        return sum(a * b for a, b in zip(v1, v2))

    def norm(v):
        return math.sqrt(dot(v, v))

    # Unit vectors
    assert abs(norm(e) - 1.0) < 1e-6
    assert abs(norm(n) - 1.0) < 1e-6
    assert abs(norm(u) - 1.0) < 1e-6

    # Orthogonality
    assert abs(dot(e, n)) < 1e-6
    assert abs(dot(e, u)) < 1e-6
    assert abs(dot(n, u)) < 1e-6


def test_extrude_building_polygon_mesh():
    # Simple rectangular polygon
    poly = Polygon(((39.66, -4.06), (39.67, -4.06), (39.67, -4.05), (39.66, -4.05), (39.66, -4.06)))
    mesh = MeshData()
    extrude_building_polygon(poly, height_m=10.0, origin_lon=39.66, origin_lat=-4.06, batch_id=0.0, mesh=mesh)

    # 4 walls * 2 tris = 8 tris
    # roof = 2 tris
    # floor = 2 tris
    # total tris = 12
    tri_count = len(mesh.indices) // 3
    assert tri_count == 12

    # Check bounds
    assert mesh.min_bounds[2] == 0.0
    assert mesh.max_bounds[2] == 10.0
