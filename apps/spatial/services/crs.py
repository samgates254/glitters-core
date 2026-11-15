"""Coordinate Reference System (CRS) transformations and geodesic calculation utilities."""

from __future__ import annotations

from django.contrib.gis.geos import GEOSGeometry

from apps.spatial.domain import (
    SRID_KENYA_UTM_36N,
    SRID_KENYA_UTM_36S,
    SRID_KENYA_UTM_37N,
    SRID_KENYA_UTM_37S,
    SRID_WGS84,
)


def get_optimal_kenya_utm_srid(longitude: float, latitude: float) -> int:
    """
    Dynamically determines the authoritative projected UTM SRID for planar operations.

    Kenya spans four UTM zones:
    - UTM Zone 36N (EPSG:32636): Western Kenya North of Equator (Turkana, Busia North)
    - UTM Zone 36S (EPSG:32736): Western Kenya South of Equator (Kisumu, Kisii, Migori)
    - UTM Zone 37N (EPSG:32637): Central/East Kenya North of Equator (Isiolo, Marsabit, Garissa North)
    - UTM Zone 37S (EPSG:32737): Central/East Kenya South of Equator (Nairobi, Mombasa, Nakuru)
    """
    zone = int((longitude + 180) / 6) + 1
    if zone not in (36, 37):
        # Fallback to standard UTM computation for international coordinates
        if latitude >= 0:
            return 32600 + zone
        return 32700 + zone

    if zone == 36:
        return SRID_KENYA_UTM_36N if latitude >= 0 else SRID_KENYA_UTM_36S
    return SRID_KENYA_UTM_37N if latitude >= 0 else SRID_KENYA_UTM_37S


def transform_to_optimal_utm(geom: GEOSGeometry) -> GEOSGeometry:
    """Transforms a WGS 84 GEOSGeometry into its optimal local UTM projection."""
    if geom.srid != SRID_WGS84:
        raise ValueError(f"Geometry must be in SRID {SRID_WGS84} before UTM transformation.")
    centroid = geom.centroid
    target_srid = get_optimal_kenya_utm_srid(centroid.x, centroid.y)
    transformed = geom.clone()
    transformed.transform(target_srid)
    return transformed


def calculate_planar_area_utm_sqm(geom: GEOSGeometry) -> float:
    """Computes area in square meters using dynamic local UTM projection."""
    utm_geom = transform_to_optimal_utm(geom)
    return float(utm_geom.area)


def calculate_orthometric_elevation(
    ellipsoidal_height_m: float, geoid_undulation_m: float
) -> float:
    """
    Computes orthometric height (H) above Mean Sea Level from GNSS ellipsoidal height (h).
    Formula: H = h - N (where N is geoid undulation, e.g. from EGM2008).
    """
    return round(ellipsoidal_height_m - geoid_undulation_m, 3)


def calculate_ellipsoidal_elevation(
    orthometric_height_m: float, geoid_undulation_m: float
) -> float:
    """
    Computes GNSS ellipsoidal height (h) from orthometric height (H).
    Formula: h = H + N.
    """
    return round(orthometric_height_m + geoid_undulation_m, 3)
