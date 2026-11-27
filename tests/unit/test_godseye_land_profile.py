"""Screening measurements for Nairobi and Kisumu outlines."""

import pytest
from django.contrib.gis.geos import Polygon

from apps.spatial.domain import SRID_KENYA_UTM_36S, SRID_KENYA_UTM_37S, SRID_WGS84
from apps.spatial.services.land_profile import SpatialInputError, measure_polygon
from apps.spatial.services.scenes import reference_places


def _square(lon: float, lat: float, delta: float = 0.001) -> dict:
    ring = [
        [lon, lat],
        [lon + delta, lat],
        [lon + delta, lat + delta],
        [lon, lat + delta],
        [lon, lat],
    ]
    return {"type": "Polygon", "coordinates": [ring]}


def test_nairobi_and_kisumu_use_different_utm_zones():
    nairobi = measure_polygon(_square(36.8210, -1.2925))
    kisumu = measure_polygon(_square(34.7680, -0.0917))
    assert nairobi["metric_epsg"] == SRID_KENYA_UTM_37S
    assert nairobi["utm_zone"] == "37S"
    assert kisumu["metric_epsg"] == SRID_KENYA_UTM_36S
    assert kisumu["utm_zone"] == "36S"
    assert 8_000 < float(nairobi["area_sqm"]) < 20_000
    assert nairobi["geometry_evidence"] == "USER-SUPPLIED"
    assert nairobi["measurement_evidence"] == "MODELLED"
    assert "not a cadastral survey" in nairobi["measurement_note"]


def test_reference_places_do_not_share_a_zone():
    places = {place["place_key"]: place for place in reference_places()}
    assert places["nairobi"]["utm_epsg"] == SRID_KENYA_UTM_37S
    assert places["kisumu"]["utm_epsg"] == SRID_KENYA_UTM_36S
    assert "Not a parcel" in places["kisumu"]["note"]


def test_cross_zone_and_equator_outlines_are_rejected():
    with pytest.raises(SpatialInputError, match="UTM zone"):
        measure_polygon(_square(35.95, -0.50, delta=0.10))
    with pytest.raises(SpatialInputError, match="equator"):
        measure_polygon(_square(36.82, -0.01, delta=0.02))


def test_invalid_and_oversized_outlines_are_rejected():
    bowtie = {
        "type": "Polygon",
        "coordinates": [
            [
                [36.82, -1.29],
                [36.83, -1.28],
                [36.83, -1.29],
                [36.82, -1.28],
                [36.82, -1.29],
            ]
        ],
    }
    with pytest.raises(SpatialInputError, match="invalid"):
        measure_polygon(bowtie)
    with pytest.raises(SpatialInputError, match="20 square kilometres"):
        measure_polygon(_square(36.8, -1.3, delta=0.5))
    with pytest.raises(SpatialInputError, match="registered parcel"):
        measure_polygon({"type": "MultiPolygon", "coordinates": []})


def test_polygon_round_trip_uses_4326():
    measured = measure_polygon(_square(36.8210, -1.2925))
    assert isinstance(measured["geometry"], Polygon)
    assert measured["geometry"].srid == SRID_WGS84
