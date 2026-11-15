"""Unit tests for nationwide Kenya CRS dynamic UTM resolution and elevations."""

import pytest
from django.contrib.gis.geos import MultiPolygon, Polygon

from apps.spatial.domain import (
    SRID_KENYA_UTM_36N,
    SRID_KENYA_UTM_36S,
    SRID_KENYA_UTM_37N,
    SRID_KENYA_UTM_37S,
    SRID_WGS84,
)
from apps.spatial.services.crs import (
    calculate_ellipsoidal_elevation,
    calculate_orthometric_elevation,
    calculate_planar_area_utm_sqm,
    get_optimal_kenya_utm_srid,
)


def test_get_optimal_kenya_utm_srid():
    # Mombasa (Coastal Kenya South): 39.6682 E, -4.0435 S -> UTM Zone 37S
    assert get_optimal_kenya_utm_srid(39.6682, -4.0435) == SRID_KENYA_UTM_37S

    # Nairobi (Central Kenya South): 36.8219 E, -1.2921 S -> UTM Zone 37S
    assert get_optimal_kenya_utm_srid(36.8219, -1.2921) == SRID_KENYA_UTM_37S

    # Kisumu (Western Kenya South): 34.7680 E, -0.0917 S -> UTM Zone 36S
    assert get_optimal_kenya_utm_srid(34.7680, -0.0917) == SRID_KENYA_UTM_36S

    # Turkana / Lodwar (Western Kenya North): 35.5975 E, 3.1191 N -> UTM Zone 36N
    assert get_optimal_kenya_utm_srid(35.5975, 3.1191) == SRID_KENYA_UTM_36N

    # Marsabit / Isiolo North (Eastern Kenya North): 37.9899 E, 2.3283 N -> UTM Zone 37N
    assert get_optimal_kenya_utm_srid(37.9899, 2.3283) == SRID_KENYA_UTM_37N


def test_orthometric_and_ellipsoidal_elevation_math():
    # GNSS ellipsoidal height h = 55.450m, geoid undulation N = -12.300m
    # H = h - N = 55.450 - (-12.300) = 67.750m
    orthometric = calculate_orthometric_elevation(55.450, -12.300)
    assert orthometric == 67.750

    # Reverse: h = H + N = 67.750 + (-12.300) = 55.450m
    ellipsoidal = calculate_ellipsoidal_elevation(67.750, -12.300)
    assert ellipsoidal == 55.450


def test_calculate_planar_area_utm_sqm():
    # 0.001 x 0.001 degree square in Mombasa (~110m x ~110m = ~12,000 sqm)
    poly = Polygon(
        (
            (39.660, -4.050),
            (39.661, -4.050),
            (39.661, -4.051),
            (39.660, -4.051),
            (39.660, -4.050),
        ),
        srid=SRID_WGS84,
    )
    multi_poly = MultiPolygon([poly], srid=SRID_WGS84)
    area_sqm = calculate_planar_area_utm_sqm(multi_poly)
    # Area should be roughly between 11,500 and 12,500 square meters
    assert 11000.0 < area_sqm < 13000.0
