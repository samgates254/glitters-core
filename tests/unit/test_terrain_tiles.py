"""Quantized-mesh tiles from a small EGM2008 DSM. No Copernicus request."""

import json

import pytest

from apps.spatial.services.asset_validation import validate_terrain_bundle
from apps.spatial.services.compute.contracts import PermanentComputeError
from apps.spatial.services.quantized_mesh import decode_mesh
from apps.spatial.services.terrain_surface import height_grid_from_asc
from apps.spatial.services.terrain_tiles import build_terrain_bundle, rendering_sample

ASC = """ncols 3
nrows 3
xllcorner 36.80
yllcorner -1.31
cellsize 0.01
NODATA_value -9999
1700 1701 1702
1690 1691 1692
1680 1681 1682
"""


def _meters(decoded: dict, index: int) -> float:
    span = decoded["maximum_height"] - decoded["minimum_height"]
    return decoded["minimum_height"] + decoded["height"][index] / 32767.0 * span


def _edge_meters(payload: bytes, edge: int) -> list[float]:
    decoded = decode_mesh(payload)
    return [_meters(decoded, index) for index in decoded["edges"][edge]]


def test_bundle_converts_orthometric_heights_and_keeps_the_source():
    document = height_grid_from_asc(ASC, place="nairobi")
    assert document["geoid_applied"] is False
    rendered, record = rendering_sample(
        document, document["center_longitude"], document["center_latitude"]
    )
    assert record is not None
    assert record["source_elevation_m"] == pytest.approx(
        record["rendering_elevation_m"] + record["geoid_offset_m"]
    )
    assert record["estimated_ground_elevation_m"] is None
    assert record["edge_is_survey"] is False
    assert rendered == pytest.approx(record["rendering_elevation_m"])
    files = build_terrain_bundle(document)
    checked = validate_terrain_bundle(files)
    assert checked["tiles"] >= 1
    layer = json.loads(files["layer.json"])
    vertical = json.loads(files["vertical.json"])
    assert layer["format"] == "quantized-mesh-1.0"
    assert any(path.startswith("0/") for path in files)
    assert any(path.startswith("15/") for path in files)
    assert "not a global elevation model" in vertical["limitation"]
    assert layer["scheme"] == "tms"
    assert layer["projection"] == "EPSG:4326"
    assert "Not a surveyed DTM" in layer["attribution"]
    assert vertical["geoid_applied_to_mesh"] is True
    assert vertical["source_geoid_applied"] is False
    assert vertical["estimated_ground_elevation_m"] is None
    assert vertical["survey"] is False
    assert vertical["bare_earth"] is False
    assert vertical["photogrammetry"] is False
    assert vertical["center"]["source_elevation_m"] == record["source_elevation_m"]
    listed = {row["path"] for row in json.loads(files["manifest.json"])["files"]}
    assert "manifest.json" not in listed
    assert "layer.json" in listed


def test_shared_edges_agree_within_quantization():
    document = height_grid_from_asc(ASC, place="nairobi")
    files = build_terrain_bundle(document)
    grouped: dict[tuple[int, int], dict[int, str]] = {}
    for path in files:
        if not path.endswith(".terrain"):
            continue
        level_text, x_text, name = path.split("/")
        grouped.setdefault((int(level_text), int(name.split(".")[0])), {})[int(x_text)] = path
    pairs = 0
    for row in grouped.values():
        for x_index, path in row.items():
            neighbor = row.get(x_index + 1)
            if neighbor is None:
                continue
            east = _edge_meters(files[path], 2)
            west = _edge_meters(files[neighbor], 0)
            assert len(east) == len(west)
            assert len(east) >= 2
            for left, right in zip(east, west, strict=True):
                assert abs(left - right) < 0.5
            pairs += 1
    assert pairs >= 1


def test_source_grid_must_keep_the_geoid_unapplied():
    document = height_grid_from_asc(ASC, place="nairobi")
    document["geoid_applied"] = True
    with pytest.raises(PermanentComputeError, match="geoid_applied"):
        build_terrain_bundle(document)


def test_a_missing_tile_fails_validation():
    document = height_grid_from_asc(ASC, place="nairobi")
    files = build_terrain_bundle(document)
    missing = next(path for path in files if path.endswith(".terrain"))
    del files[missing]
    with pytest.raises(PermanentComputeError, match="missing"):
        validate_terrain_bundle(files)
