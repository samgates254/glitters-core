"""Building footprint checks. Assumed heights stay labeled as assumptions."""

import pytest

from apps.spatial.services.city_models import city_document, valid_polygon
from apps.spatial.services.compute.contracts import PermanentComputeError


def _square(lon: float, lat: float) -> dict:
    ring = [
        [lon, lat],
        [lon + 0.001, lat],
        [lon + 0.001, lat - 0.001],
        [lon, lat - 0.001],
        [lon, lat],
    ]
    return {"type": "Polygon", "coordinates": [ring]}


def test_open_ring_is_rejected():
    ring = [[[0, 0], [1, 0], [1, 1], [0, 1]]]
    assert valid_polygon({"type": "Polygon", "coordinates": ring}) is False


def test_city_document_keeps_basis_and_rejects_a_bad_ring():
    report = {
        "limitation": "OpenStreetMap features inside a small box.",
        "queried_bbox": [36.8, -1.31, 36.84, -1.27],
        "geojson": {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "kind": "building",
                        "height_m": 12,
                        "height_basis": "OSM_HEIGHT_TAG",
                        "height_note": "Mapped height tag. Not a surveyed height.",
                        "height_measured": False,
                        "osm_id": "way/10",
                        "name": "Tagged",
                        "source": "OpenStreetMap",
                    },
                    "geometry": _square(36.82, -1.292),
                },
                {
                    "type": "Feature",
                    "properties": {
                        "kind": "building",
                        "height_m": 3.2,
                        "height_basis": "ASSUMED_SINGLE_STOREY",
                        "height_note": "3.2 m is an assumption, not a measured height.",
                        "height_measured": False,
                        "osm_id": "way/11",
                        "name": "",
                        "source": "OpenStreetMap",
                    },
                    "geometry": _square(36.822, -1.292),
                },
                {
                    "type": "Feature",
                    "properties": {
                        "kind": "building",
                        "height_m": 9,
                        "height_basis": "OSM_HEIGHT_TAG",
                        "height_note": "Mapped height tag. Not a surveyed height.",
                        "height_measured": False,
                        "osm_id": "way/12",
                        "name": "Broken",
                        "source": "OpenStreetMap",
                    },
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[0, 0], [0, 0], [0, 0], [0, 0]]],
                    },
                },
            ],
        },
    }
    document = city_document("nairobi", report)
    assert document["photogrammetry"] is False
    assert document["representation"] == "extruded-footprint"
    assert document["building_count"] == 2
    assert document["rejected_count"] == 1
    assert document["height_basis_counts"]["ASSUMED_SINGLE_STOREY"] == 1
    assert document["height_basis_counts"]["OSM_HEIGHT_TAG"] == 1
    for feature in document["geojson"]["features"]:
        assert feature["properties"]["height_measured"] is False
        assert "way/" in feature["properties"]["osm_id"]


def test_city_document_fails_closed_when_nothing_is_valid():
    report = {"geojson": {"type": "FeatureCollection", "features": []}, "limitation": "none"}
    with pytest.raises(PermanentComputeError, match="No valid building"):
        city_document("kisumu", report)
