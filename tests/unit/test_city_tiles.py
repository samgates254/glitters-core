"""3D Tiles extrusions stay labeled as extrusions. No photogrammetry claim."""

import json

import pytest

from apps.spatial.services.asset_validation import _validate_b3dm, validate_tileset_bundle
from apps.spatial.services.city_models import city_document
from apps.spatial.services.city_tiles import build_city_tiles
from apps.spatial.services.compute.contracts import PermanentComputeError


def _square(lon: float, lat: float) -> dict:
    ring = [
        [lon, lat],
        [lon + 0.0004, lat],
        [lon + 0.0004, lat - 0.0004],
        [lon, lat - 0.0004],
        [lon, lat],
    ]
    return {"type": "Polygon", "coordinates": [ring]}


def _feature(osm_id: str, basis: str, height: float, lon: float, note: str) -> dict:
    return {
        "type": "Feature",
        "properties": {
            "kind": "building",
            "height_m": height,
            "height_basis": basis,
            "height_note": note,
            "height_measured": False,
            "osm_id": osm_id,
            "name": osm_id,
            "source": "OpenStreetMap",
        },
        "geometry": _square(lon, -1.292),
    }


def _document():
    report = {
        "limitation": "OpenStreetMap features inside a small box.",
        "queried_bbox": [36.8, -1.31, 36.84, -1.27],
        "geojson": {
            "type": "FeatureCollection",
            "features": [
                _feature(
                    "way/101987509",
                    "OSM_HEIGHT_TAG",
                    120,
                    36.821,
                    "Mapped height tag. Not a surveyed height.",
                ),
                _feature(
                    "way/101987517",
                    "ASSUMED_FROM_LEVELS",
                    28.8,
                    36.822,
                    "3.2 m per mapped level. Not a measured height.",
                ),
            ],
        },
    }
    return city_document("nairobi", report)


def test_city_tiles_keep_height_sources_and_reject_photogrammetry():
    document = _document()
    files = build_city_tiles(document, lambda longitude, latitude: 1694.3)
    checked = validate_tileset_bundle(files)
    assert "lod0.b3dm" in checked["content"]
    tileset = json.loads(files["tileset.json"])
    assert tileset["asset"]["version"] == "1.0"
    assert tileset["extras"]["photogrammetry"] is False
    assert tileset["extras"]["representation"] == "extruded-footprint"
    assert tileset["extras"]["survey"] is False
    assert "OpenStreetMap" in tileset["extras"]["attribution"]
    child = files["c0.b3dm"]
    batch = _validate_b3dm(child)
    assert batch["batches"] == 2
    start = 28
    feature_len = int.from_bytes(child[12:16], "little")
    batch_len = int.from_bytes(child[20:24], "little")
    table = json.loads(child[start + feature_len : start + feature_len + batch_len])
    assert table["osm_id"] == ["way/101987509", "way/101987517"]
    assert table["height_basis"] == ["OSM_HEIGHT_TAG", "ASSUMED_FROM_LEVELS"]
    assert table["height_m"] == [120, 28.8]
    assert table["height_measured"] == [False, False]
    assert set(table["model_kind"]) == {"extruded_footprint"}
    assert table["geometry_lod"] == ["footprint", "footprint"]


def test_a_broken_dependency_is_rejected():
    files = build_city_tiles(_document(), lambda longitude, latitude: 1694.3)
    files["c0.b3dm"] = b"not-a-tile"
    with pytest.raises(PermanentComputeError):
        validate_tileset_bundle(files)
