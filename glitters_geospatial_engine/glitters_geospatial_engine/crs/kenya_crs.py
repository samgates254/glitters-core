"""
Coordinate Reference System (CRS) & Kenya Administrative Hierarchy Engine (`v1.1.0`)
====================================================================================

Handles WGS84 (EPSG:4326), Arc 1960 (EPSG:4210), all four WGS84 Kenya UTM zones
(EPSG:32636, EPSG:32637, EPSG:32736, EPSG:32737), and all four Survey of Kenya
Arc 1960 UTM zones (EPSG:21096 [36N], EPSG:21097 [37N], EPSG:21036 [36S], EPSG:21037 [37S]).

Enforces strict linear-unit metre validation (`unit_conversion_factor == 1.0`), rejecting
both geographic CRS definitions (degrees) and non-metric projected CRS definitions (such
as US survey feet EPSG:2263 or British feet).
"""

from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional, Tuple, Union
import math
import numpy as np
from pyproj import CRS, Geod, Transformer
import shapely
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shapely_transform


class GeodeticMetricCalculationError(ValueError):
    """
    Raised when a metric area, length, buffer, raster GSD, or distance operation is
    attempted on an unprojected geographic CRS (degrees) or a non-metre projected CRS
    (e.g. US survey feet or Clarke's foot).
    """


class ObservationAuthority(str, Enum):
    """Strict provenance classification separating public/inferred data from legal cadastre."""
    PUBLIC_SATELLITE_OBSERVATION = "PUBLIC_SATELLITE_OBSERVATION"
    PUBLIC_AERIAL_OBSERVATION = "PUBLIC_AERIAL_OBSERVATION"
    OPEN_COMMUNITY_MAPPING = "OPEN_COMMUNITY_MAPPING"
    INFERRED_GEOMETRIC_BOUNDARY = "INFERRED_GEOMETRIC_BOUNDARY"
    PRELIMINARY_ALGORITHMIC_SCREENING = "PRELIMINARY_ALGORITHMIC_SCREENING"
    LICENSED_SURVEY_PLAN_REFERENCE = "LICENSED_SURVEY_PLAN_REFERENCE"


class KenyaAdminLevel(str, Enum):
    """Constitutional & operational geographic hierarchy for Kenya."""
    NATIONAL = "NATIONAL"
    COUNTY = "COUNTY"
    SUB_COUNTY = "SUB_COUNTY"
    WARD = "WARD"
    LOCALITY = "LOCALITY"
    NEIGHBOURHOOD = "NEIGHBOURHOOD"
    PROPERTY = "PROPERTY"
    PARCEL = "PARCEL"
    BUILDING = "BUILDING"


SUPPORTED_KENYA_METRIC_EPSG_CODES: Tuple[int, ...] = (
    32636,  # WGS 84 / UTM zone 36N
    32637,  # WGS 84 / UTM zone 37N
    32736,  # WGS 84 / UTM zone 36S
    32737,  # WGS 84 / UTM zone 37S
    21096,  # Arc 1960 / UTM zone 36N
    21097,  # Arc 1960 / UTM zone 37N
    21036,  # Arc 1960 / UTM zone 36S
    21037,  # Arc 1960 / UTM zone 37S
)

# Official EPSG Registry definitions for Kenya
KENYA_EPSG_REGISTRY: Dict[str, Dict[str, Union[int, str]]] = {
    "WGS84_GEOGRAPHIC": {
        "epsg": 4326,
        "name": "WGS 84 Geographic 2D",
        "datum": "World Geodetic System 1984",
        "units": "degrees",
        "url": "https://epsg.io/4326",
    },
    "ARC1960_GEOGRAPHIC": {
        "epsg": 4210,
        "name": "Arc 1960 Geographic 2D (Clarke 1880 RGS)",
        "datum": "Arc 1960",
        "units": "degrees",
        "url": "https://epsg.io/4210",
    },
    "WGS84_UTM_36N": {
        "epsg": 32636,
        "name": "WGS 84 / UTM zone 36N (Lon 30E-36E, North of Equator)",
        "datum": "WGS84",
        "units": "metre",
        "url": "https://epsg.io/32636",
    },
    "WGS84_UTM_37N": {
        "epsg": 32637,
        "name": "WGS 84 / UTM zone 37N (Lon 36E-42E, North of Equator)",
        "datum": "WGS84",
        "units": "metre",
        "url": "https://epsg.io/32637",
    },
    "WGS84_UTM_36S": {
        "epsg": 32736,
        "name": "WGS 84 / UTM zone 36S (Lon 30E-36E, South of Equator)",
        "datum": "WGS84",
        "units": "metre",
        "url": "https://epsg.io/32736",
    },
    "WGS84_UTM_37S": {
        "epsg": 32737,
        "name": "WGS 84 / UTM zone 37S (Lon 36E-42E, South of Equator)",
        "datum": "WGS84",
        "units": "metre",
        "url": "https://epsg.io/32737",
    },
    "ARC1960_UTM_36N": {
        "epsg": 21096,
        "name": "Arc 1960 / UTM zone 36N (Lon 30E-36E, North of Equator)",
        "datum": "Arc 1960",
        "units": "metre",
        "url": "https://epsg.io/21096",
    },
    "ARC1960_UTM_37N": {
        "epsg": 21097,
        "name": "Arc 1960 / UTM zone 37N (Lon 36E-42E, North of Equator)",
        "datum": "Arc 1960",
        "units": "metre",
        "url": "https://epsg.io/21097",
    },
    "ARC1960_UTM_36S": {
        "epsg": 21036,
        "name": "Arc 1960 / UTM zone 36S (Lon 30E-36E, South of Equator)",
        "datum": "Arc 1960",
        "units": "metre",
        "url": "https://epsg.io/21036",
    },
    "ARC1960_UTM_37S": {
        "epsg": 21037,
        "name": "Arc 1960 / UTM zone 37S (Lon 36E-42E, South of Equator)",
        "datum": "Arc 1960",
        "units": "metre",
        "url": "https://epsg.io/21037",
    },
}

KENYA_WGS84_BOUNDS: Tuple[float, float, float, float] = (33.908, -4.724, 41.906, 5.462)

KENYA_COUNTY_REFERENCE: Dict[int, Dict[str, Union[str, int, Tuple[float, float]]]] = {
    1: {"name": "Mombasa", "utm_epsg": 32737, "arc1960_epsg": 21037, "centroid_lonlat": (39.6682, -4.0435)},
    16: {"name": "Machakos", "utm_epsg": 32737, "arc1960_epsg": 21037, "centroid_lonlat": (37.2634, -1.5177)},
    22: {"name": "Kiambu", "utm_epsg": 32737, "arc1960_epsg": 21037, "centroid_lonlat": (36.8356, -1.1714)},
    23: {"name": "Turkana", "utm_epsg": 32636, "arc1960_epsg": 21096, "centroid_lonlat": (35.5970, 3.1190)},
    27: {"name": "Uasin Gishu", "utm_epsg": 32636, "arc1960_epsg": 21096, "centroid_lonlat": (35.2698, 0.5143)},
    32: {"name": "Nakuru", "utm_epsg": 32737, "arc1960_epsg": 21037, "centroid_lonlat": (36.0800, -0.3031)},
    34: {"name": "Kajiado", "utm_epsg": 32737, "arc1960_epsg": 21037, "centroid_lonlat": (36.7820, -1.8524)},
    42: {"name": "Kisumu", "utm_epsg": 32736, "arc1960_epsg": 21036, "centroid_lonlat": (34.7680, -0.0917)},
    47: {"name": "Nairobi", "utm_epsg": 32737, "arc1960_epsg": 21037, "centroid_lonlat": (36.8219, -1.2921)},
}


@dataclass(frozen=True)
class KenyaAdminHierarchyTag:
    """Immutable administrative location tag adhering to Kenya's constitutional hierarchy."""
    country_code: str = "KEN"
    county_code: Optional[int] = None
    county_name: Optional[str] = None
    sub_county_code: Optional[str] = None
    sub_county_name: Optional[str] = None
    ward_code: Optional[str] = None
    ward_name: Optional[str] = None
    locality_name: Optional[str] = None
    neighbourhood_name: Optional[str] = None
    authority_source: str = "HDX COD-AB / IEBC Public Boundary Reference"


class KenyaCRSEngine:
    """
    Deterministic CRS validator, transformer, and geodesic calculator for Kenya.
    Strictly enforces projected metric coordinates (`unit == metre`, `conversion_factor == 1.0`).
    """

    def __init__(self) -> None:
        self._geod = Geod(ellps="WGS84")
        self._transformers: Dict[Tuple[int, int], Transformer] = {}

    @staticmethod
    def assert_projected_crs(
        crs_input: Union[int, str, CRS], require_kenya_epsg: bool = False
    ) -> CRS:
        """
        Verify that the supplied CRS is projected AND uses exact SI metre linear units
        (`unit_name in ('metre', 'meter', 'm')` and `unit_conversion_factor == 1.0`).
        Rejects both geographic CRS definitions (e.g. EPSG:4326, EPSG:4210) and non-metre
        projected CRS definitions (e.g. EPSG:2263 in US survey feet).
        """
        try:
            crs_obj = CRS.from_user_input(crs_input)
        except Exception as exc:
            raise GeodeticMetricCalculationError(f"Invalid CRS input '{crs_input}': {exc}") from exc

        if crs_obj.is_geographic or not crs_obj.is_projected:
            raise GeodeticMetricCalculationError(
                f"CRS '{crs_obj.to_string()}' is geographic/unprojected. "
                "Metric area, perimeter, buffer, raster GSD, and distance calculations are strictly "
                "prohibited on unprojected coordinates. Project to a Kenya UTM zone "
                "(EPSG:32636, 32637, 32736, 32737, 21096, 21097, 21036, or 21037)."
            )

        if len(crs_obj.axis_info) < 2:
            raise GeodeticMetricCalculationError(
                f"CRS '{crs_obj.to_string()}' does not define at least 2 planar spatial axes."
            )

        for ax in crs_obj.axis_info[:2]:
            unit_lower = (ax.unit_name or "").strip().lower()
            conv = float(ax.unit_conversion_factor or 0.0)
            if unit_lower not in ("metre", "meter", "m") or abs(conv - 1.0) > 1e-6:
                raise GeodeticMetricCalculationError(
                    f"CRS '{crs_obj.to_string()}' uses non-metre linear axis unit '{ax.unit_name}' "
                    f"(conversion_factor={conv}). Only exact SI metre projected CRS definitions "
                    "are permitted for GLITTERS metric operations."
                )

        if require_kenya_epsg:
            epsg_code = crs_obj.to_epsg()
            if epsg_code not in SUPPORTED_KENYA_METRIC_EPSG_CODES:
                raise GeodeticMetricCalculationError(
                    f"EPSG:{epsg_code} is not an authorized Kenya UTM CRS "
                    f"{SUPPORTED_KENYA_METRIC_EPSG_CODES}."
                )

        return crs_obj

    @classmethod
    def assert_metric_crs(
        cls, crs_input: Union[int, str, CRS], require_kenya_epsg: bool = False
    ) -> CRS:
        """Alias for `assert_projected_crs`."""
        return cls.assert_projected_crs(crs_input, require_kenya_epsg=require_kenya_epsg)

    @staticmethod
    def select_kenya_utm_epsg(lon: float, lat: float, datum: str = "WGS84") -> int:
        """
        Select the exact UTM zone EPSG code for any location in Kenya based on the
        36°E central meridian boundary (Zone 36 vs Zone 37) and Equator (0° N/S)
        for both WGS84 and Arc 1960 datums:
        - WGS84: 32636 (36N), 32637 (37N), 32736 (36S), 32737 (37S)
        - Arc 1960: 21096 (36N), 21097 (37N), 21036 (36S), 21037 (37S)
        """
        is_zone_37 = lon >= 36.0
        is_north = lat >= 0.0
        datum_upper = datum.upper()
        if datum_upper == "WGS84":
            if is_zone_37:
                return 32637 if is_north else 32737
            return 32636 if is_north else 32736
        elif datum_upper in ("ARC1960", "ARC_1960"):
            if is_zone_37:
                return 21097 if is_north else 21037
            return 21096 if is_north else 21036
        raise ValueError(f"Unsupported datum '{datum}'. Use 'WGS84' or 'ARC1960'.")

    @staticmethod
    def is_cross_zone_or_equator(bounds_lonlat: Tuple[float, float, float, float]) -> Dict[str, bool]:
        min_lon, min_lat, max_lon, max_lat = bounds_lonlat
        crosses_36e = (min_lon < 36.0) and (max_lon > 36.0)
        crosses_equator = (min_lat < 0.0) and (max_lat > 0.0)
        return {
            "crosses_utm_zone_boundary_36E": crosses_36e,
            "crosses_equator_0N": crosses_equator,
            "requires_geodesic_or_custom_tm": crosses_36e or crosses_equator,
        }

    def get_transformer(self, source_epsg: int, target_epsg: int) -> Transformer:
        key = (source_epsg, target_epsg)
        if key not in self._transformers:
            self._transformers[key] = Transformer.from_crs(
                CRS.from_epsg(source_epsg),
                CRS.from_epsg(target_epsg),
                always_xy=True,
            )
        return self._transformers[key]

    def transform_geometry(
        self, geom: BaseGeometry, source_epsg: int, target_epsg: int
    ) -> BaseGeometry:
        if source_epsg == target_epsg:
            return geom
        transformer = self.get_transformer(source_epsg, target_epsg)
        if hasattr(shapely, "transform"):
            def _transform_coords(coords):
                x, y = coords[:, 0], coords[:, 1]
                if len(x) == 1:
                    xt, yt = transformer.transform(float(x[0]), float(y[0]))
                    xt, yt = [xt], [yt]
                else:
                    xt, yt = transformer.transform(x, y)
                if coords.shape[1] > 2:
                    return np.column_stack([xt, yt, coords[:, 2]])
                return np.column_stack([xt, yt])
            return shapely.transform(geom, _transform_coords)
        return shapely_transform(transformer.transform, geom)

    def project_wgs84_to_local_kenya_utm(
        self, geom_wgs84: BaseGeometry, datum: str = "WGS84"
    ) -> Tuple[BaseGeometry, int]:
        centroid = geom_wgs84.centroid
        utm_epsg = self.select_kenya_utm_epsg(centroid.x, centroid.y, datum=datum)
        src_epsg = 4326 if datum.upper() == "WGS84" else 4210
        projected = self.transform_geometry(geom_wgs84, src_epsg, utm_epsg)
        return projected, utm_epsg

    def calculate_geodesic_area_and_perimeter(
        self, geom_wgs84: BaseGeometry
    ) -> Dict[str, float]:
        area_m2, perimeter_m = self._geod.geometry_area_perimeter(geom_wgs84)
        return {
            "geodesic_area_m2": abs(float(area_m2)),
            "geodesic_perimeter_m": abs(float(perimeter_m)),
            "geodesic_area_hectares": abs(float(area_m2)) / 10_000.0,
            "geodesic_area_acres": abs(float(area_m2)) / 4_046.8564224,
        }

    def calculate_utm_vs_geodesic_discrepancy(
        self, geom_wgs84: BaseGeometry
    ) -> Dict[str, float]:
        geod_stats = self.calculate_geodesic_area_and_perimeter(geom_wgs84)
        utm_geom, utm_epsg = self.project_wgs84_to_local_kenya_utm(geom_wgs84)
        utm_area = float(utm_geom.area)
        utm_perim = float(utm_geom.length)
        geod_area = geod_stats["geodesic_area_m2"]
        rel_error_ppm = (
            abs(utm_area - geod_area) / max(geod_area, 1e-9) * 1_000_000.0
        )
        return {
            "utm_epsg": utm_epsg,
            "utm_planar_area_m2": utm_area,
            "geodesic_ellipsoidal_area_m2": geod_area,
            "utm_planar_perimeter_m": utm_perim,
            "geodesic_ellipsoidal_perimeter_m": geod_stats["geodesic_perimeter_m"],
            "area_distortion_ppm": rel_error_ppm,
        }
