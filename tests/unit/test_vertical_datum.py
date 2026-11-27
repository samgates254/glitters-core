"""EGM2008 grid offset against PROJ and GDAL bilinear fixtures. No network."""

import hashlib

import pytest

from apps.spatial.services.compute.contracts import PermanentComputeError
from apps.spatial.services.vertical_datum import (
    GRID_PATH,
    ellipsoidal_height_m,
    geoid_offset_m,
    vertical_record,
)

GRID_SHA256 = "5f206f60751e241c714e739fc07023f5357302e76ee56a70bd4867cc5d9ca4c8"

# PROJ 9.7.1 vgridshift and GDAL bilinear on us_nga_egm08_25.tif.
FIXTURES = (
    (36.8219, -1.2921, -16.05348541),
    (34.7680, -0.0917, -17.58934245),
    (36.8019, -1.3121, -16.03217187),
    (36.8419, -1.2721, -16.06912863),
    (34.7480, -0.1117, -17.69242266),
    (36.80, -1.31, -16.02827583),
    (36.8125, -1.270833333333329, -16.01941109),
)


def test_committed_window_matches_the_recorded_digest():
    digest = hashlib.sha256(GRID_PATH.read_bytes()).hexdigest()
    assert digest == GRID_SHA256
    assert GRID_PATH.stat().st_size == 308600


@pytest.mark.parametrize(("longitude", "latitude", "offset"), FIXTURES)
def test_geoid_offset_matches_proj_bilinear(longitude, latitude, offset):
    assert geoid_offset_m(longitude, latitude) == pytest.approx(offset, abs=1e-6)


def test_ellipsoidal_height_subtracts_the_grid_offset():
    # offset is negative here, so h = H - offset sits above the orthometric height.
    rendered = ellipsoidal_height_m(1000.0, 36.80, -1.31)
    assert rendered == pytest.approx(1016.02827583, abs=1e-6)


def test_vertical_record_keeps_source_and_refuses_a_ground_estimate():
    record = vertical_record(1678.24, 36.8219, -1.2921, exaggeration=2.0)
    assert record["source_elevation_m"] == pytest.approx(1678.24)
    assert record["source_vertical_datum"] == "EGM2008"
    assert record["source_kind"] == "DSM"
    assert record["rendering_frame"] == "WGS84-ellipsoid"
    assert record["geoid_applied"] is True
    assert record["estimated_ground_elevation_m"] is None
    assert record["survey"] is False
    assert record["bare_earth"] is False
    assert record["photogrammetry"] is False
    expected = 1678.24 - record["geoid_offset_m"]
    assert record["rendering_elevation_m"] == pytest.approx(expected)
    assert record["surface_model_elevation_m"] == pytest.approx(1678.24)
    assert record["exaggerated_rendering_elevation_m"] == pytest.approx(expected * 2)
    assert record["vertical_uncertainty"]["source_is_dtm"] is False
    assert record["vertical_uncertainty"]["source_product_statement_m"] == 4.0


def test_outside_the_kenya_window_is_refused():
    with pytest.raises(PermanentComputeError, match="outside the Kenya"):
        geoid_offset_m(10.0, 0.0)


def test_exaggeration_outside_the_render_range_is_refused():
    with pytest.raises(PermanentComputeError, match="exaggeration"):
        vertical_record(100.0, 36.8219, -1.2921, exaggeration=9)
