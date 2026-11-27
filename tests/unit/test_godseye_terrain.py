"""Elevation windows and mapped surroundings stay labeled as screening data."""

import numpy as np
import pytest

from apps.spatial.services.providers.http import ProviderTransportError
from apps.spatial.services.surroundings import height_fields, load_surroundings
from apps.spatial.services.terrain import (
    copernicus_tile_id,
    parse_aaigrid,
    screen_grid,
    window_transect,
)


def test_nairobi_and_kisumu_use_different_dem_tiles():
    assert copernicus_tile_id(-1.2921, 36.8219) == "Copernicus_DSM_COG_10_S02_00_E036_00_DEM"
    assert copernicus_tile_id(-0.0917, 34.7680) == "Copernicus_DSM_COG_10_S01_00_E034_00_DEM"
    assert copernicus_tile_id(-4.0615, 39.6682) == "Copernicus_DSM_COG_10_S05_00_E039_00_DEM"


def test_grid_reports_a_plane_and_refuses_an_empty_window():
    grid = np.array([[1600.0, 1630.0], [1600.0, 1630.0]], dtype=np.float64)
    stats = screen_grid(grid, cellsize_deg=30 / 111320, nodata=-32767, latitude=-1.29)
    assert stats["sample_count"] == 4
    assert stats["elevation_min_m"] == 1600
    assert stats["elevation_max_m"] == 1630
    assert stats["slope_max_deg"] > 20
    transect = window_transect(
        grid, cellsize_deg=30 / 111320, nodata=-32767, latitude=-1.29
    )
    assert [point["elevation_m"] for point in transect] == [1600.0, 1630.0]
    assert transect[-1]["distance_m"] > transect[0]["distance_m"]
    assert window_transect(
        np.array([[-32767.0]], dtype=np.float64),
        cellsize_deg=0.0003,
        nodata=-32767,
        latitude=-1.29,
    ) == []

    empty = screen_grid(
        np.array([[-32767.0]], dtype=np.float64),
        cellsize_deg=0.0003,
        nodata=-32767,
        latitude=-1.29,
    )
    assert empty["sample_count"] == 0


def test_aaigrid_parser_rejects_a_short_grid():
    text = "ncols 2\nnrows 2\ncellsize 0.0003\n1 2\n"
    with pytest.raises(ProviderTransportError, match="did not match"):
        parse_aaigrid(text)
    grid, cell, nodata = parse_aaigrid(
        "ncols 1\nnrows 1\ncellsize 0.0003\nnodata_value -9999\n1661.5\n"
    )
    assert grid.shape == (1, 1)
    assert cell == pytest.approx(0.0003)
    assert nodata == -9999


def test_missing_height_is_an_assumption_and_a_tag_is_not():
    assumed, basis, note = height_fields({})
    assert assumed == 3.2
    assert basis == "ASSUMED_SINGLE_STOREY"
    assert "assumption" in note
    mapped, mapped_basis, _note = height_fields({"height": "12 m"})
    assert mapped == 12
    assert mapped_basis == "OSM_HEIGHT_TAG"
    levels, level_basis, _note = height_fields({"building:levels": "2"})
    assert levels == 6.4
    assert level_basis == "ASSUMED_FROM_LEVELS"


def test_surroundings_keep_only_the_returned_geometries(monkeypatch, tmp_path):
    body = b"""
    {"elements":[
      {"type":"way","tags":{"building":"yes","building:levels":"2"},
       "geometry":[{"lon":36.82,"lat":-1.292},{"lon":36.821,"lat":-1.292},
                   {"lon":36.821,"lat":-1.293},{"lon":36.82,"lat":-1.293}]},
      {"type":"way","tags":{"highway":"residential","name":"Example Road"},
       "geometry":[{"lon":36.82,"lat":-1.292},{"lon":36.822,"lat":-1.292}]},
      {"type":"node","tags":{"amenity":"school","name":"Example School"},"lon":36.821,"lat":-1.2925}
    ]}
    """

    def fake_fetch(url, **kwargs):
        assert kwargs["allowed_hosts"] == frozenset(
            {"overpass-api.de", "overpass.openstreetmap.fr"}
        )
        assert url.startswith("https://overpass-api.de/")
        return 200, body

    monkeypatch.setattr("apps.spatial.services.surroundings.fetch_https", fake_fetch)
    monkeypatch.setattr(
        "apps.spatial.services.surroundings._cache_file",
        lambda key: tmp_path / f"{key}.json",
    )
    payload = load_surroundings((36.82, -1.293, 36.821, -1.292))
    assert payload["building_count"] == 1
    assert payload["road_count"] == 1
    assert payload["amenity_count"] == 1
    assert payload["land_cover"] is None
    building = payload["geojson"]["features"][0]
    assert building["properties"]["height_basis"] == "ASSUMED_FROM_LEVELS"
    assert building["properties"]["height_m"] == 6.4
    assert "not a survey" in payload["limitation"].lower()


def test_overpass_uses_the_second_endpoint_when_the_first_is_at_capacity(
    monkeypatch, tmp_path
):
    calls = []

    def fake_fetch(url, **kwargs):
        calls.append(url)
        assert kwargs["allowed_hosts"] == frozenset(
            {"overpass-api.de", "overpass.openstreetmap.fr"}
        )
        if "overpass-api.de" in url:
            raise ProviderTransportError("The provider returned HTTP 502.", 502)
        return 200, b'{"elements":[]}'

    monkeypatch.setattr("apps.spatial.services.surroundings.fetch_https", fake_fetch)
    monkeypatch.setattr(
        "apps.spatial.services.surroundings._cache_file",
        lambda key: tmp_path / f"{key}.json",
    )
    payload = load_surroundings((36.82, -1.293, 36.821, -1.292))
    assert calls[0].startswith("https://overpass-api.de/")
    assert "overpass.openstreetmap.fr" in calls[1]
    assert payload["downloaded"] is True
    assert payload["building_count"] == 0
    assert payload["land_cover"] is None
