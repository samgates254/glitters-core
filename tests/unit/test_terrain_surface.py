"""Height-grid math. These tests do not download a DEM."""

import pytest

from apps.spatial.services.compute.contracts import PermanentComputeError
from apps.spatial.services.terrain_surface import (
    height_grid_from_asc,
    sample_height,
    validate_height_grid,
)

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


def test_asc_window_becomes_a_dsm_grid():
    document = height_grid_from_asc(ASC, place="nairobi")
    validate_height_grid(document)
    assert document["source_kind"] == "DSM"
    assert document["geoid_applied"] is False
    assert document["bare_earth"] is False
    assert document["survey"] is False
    assert document["vertical_datum"] == "EGM2008"
    assert document["heights"][0] == 1700
    assert document["heights"][-1] == 1682
    assert document["nodata_filled"] == 0
    assert sample_height(document, 36.805, -1.285) == pytest.approx(1700, abs=0.01)
    assert sample_height(document, 0, 0) is None


def test_missing_dem_is_not_invented():
    empty = """ncols 2
nrows 2
xllcorner 36.8
yllcorner -1.3
cellsize 0.01
NODATA_value -9999
-9999 -9999
-9999 -9999
"""
    with pytest.raises(PermanentComputeError, match="no valid samples"):
        height_grid_from_asc(empty, place="kisumu")


def test_bilinear_sample_uses_cell_centers():
    document = {
        "format": "glitters-height-grid-v1",
        "source_kind": "DSM",
        "geoid_applied": False,
        "bare_earth": False,
        "survey": False,
        "vertical_datum": "EGM2008",
        "horizontal_crs": "EPSG:4326",
        "west": 0.0,
        "south": 0.0,
        "east": 0.02,
        "north": 0.02,
        "width": 2,
        "height": 2,
        "cellsize_deg": 0.01,
        "elevation_min_m": 10.0,
        "elevation_max_m": 40.0,
        "elevation_mean_m": 25.0,
        "heights": [10, 20, 30, 40],
    }
    validate_height_grid(document)
    assert sample_height(document, 0.005, 0.015) == pytest.approx(10)
    assert sample_height(document, 0.01, 0.01) == pytest.approx(25)
    assert sample_height(document, -0.1, 0.01) is None


def test_survey_flag_is_rejected():
    document = {
        "format": "glitters-height-grid-v1",
        "source_kind": "DSM",
        "geoid_applied": False,
        "bare_earth": False,
        "survey": True,
        "vertical_datum": "EGM2008",
        "horizontal_crs": "EPSG:4326",
        "west": 0.0,
        "south": 0.0,
        "east": 0.02,
        "north": 0.02,
        "width": 2,
        "height": 2,
        "cellsize_deg": 0.01,
        "elevation_min_m": 10.0,
        "elevation_max_m": 40.0,
        "elevation_mean_m": 25.0,
        "heights": [10, 20, 30, 40],
    }
    with pytest.raises(PermanentComputeError, match="not a surveyed"):
        validate_height_grid(document)
