"""
Standardized Property Spatial Feature Vector (`spatial_features/feature_vector.py` v1.1.0)
==========================================================================================

Upgrades in v1.1.0:
1. Eliminates ALL silent hard-coded default imputations (`1650.0`, `6.5`, `0.15`, `0.85`).
   When a terrain or land-cover observation is missing (`None` or `NaN`), the feature vector
   records `float("nan")` (`None` in JSON) and sets the corresponding entry in
   `feature_observation_mask` to `False`.
2. Exposes explicit data-quality & completeness diagnostics:
   - `feature_observation_mask: Tuple[bool, ...]`
   - `observed_feature_count: int`
   - `completeness_ratio: float`
   - `missing_feature_names: Tuple[str, ...]`
3. Propagates physical/analytical uncertainties for network distances, travel times,
   and observed environmental indices rather than populating uncertainties on missing fields.
"""

from __future__ import annotations
from dataclasses import dataclass, field
import math
from typing import Any, Dict, Optional, Sequence, Tuple
import numpy as np
from shapely.geometry import Polygon

from ..building_detection.detector import BuildingAndLandDetectionEngine
from ..crs.kenya_crs import KenyaAdminHierarchyTag, KenyaCRSEngine
from ..network_analysis.routing import SpatialNetworkEngine
from ..parcels.intelligence import ParcelIntelligenceEngine, ParcelRecord


def _opt_float(d: Optional[Dict[str, Any]], key: str) -> float:
    if d is None or key not in d or d[key] is None:
        return float("nan")
    val = float(d[key])
    return val if math.isfinite(val) else float("nan")


@dataclass(frozen=True)
class PropertySpatialFeatureVector:
    schema_version: str
    property_id: str
    parcel_id: str
    utm_epsg: int
    centroid_wgs84_lonlat: Tuple[float, float]
    admin_hierarchy: KenyaAdminHierarchyTag
    observation_authority: str
    is_legally_verified_cadastre: bool
    feature_names: Tuple[str, ...]
    feature_values: Tuple[float, ...]
    feature_uncertainties_1sigma: Tuple[float, ...]
    feature_observation_mask: Tuple[bool, ...]
    observed_feature_count: int
    completeness_ratio: float
    missing_feature_names: Tuple[str, ...]
    metadata_disclaimers: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        features_dict: Dict[str, Optional[float]] = {}
        unc_dict: Dict[str, Optional[float]] = {}
        for name, val, unc, obs in zip(
            self.feature_names,
            self.feature_values,
            self.feature_uncertainties_1sigma,
            self.feature_observation_mask,
        ):
            features_dict[name] = float(val) if (obs and math.isfinite(val)) else None
            unc_dict[name] = float(unc) if (obs and math.isfinite(unc)) else None

        return {
            "schema_version": self.schema_version,
            "property_id": self.property_id,
            "parcel_id": self.parcel_id,
            "utm_epsg": self.utm_epsg,
            "centroid_wgs84_lonlat": self.centroid_wgs84_lonlat,
            "admin_hierarchy": {
                "country_code": self.admin_hierarchy.country_code,
                "county_code": self.admin_hierarchy.county_code,
                "county_name": self.admin_hierarchy.county_name,
                "sub_county_name": self.admin_hierarchy.sub_county_name,
                "ward_name": self.admin_hierarchy.ward_name,
                "locality_name": self.admin_hierarchy.locality_name,
            },
            "observation_authority": self.observation_authority,
            "is_legally_verified_cadastre": self.is_legally_verified_cadastre,
            "data_completeness": {
                "observed_feature_count": self.observed_feature_count,
                "total_feature_count": len(self.feature_names),
                "completeness_ratio": self.completeness_ratio,
                "missing_feature_names": list(self.missing_feature_names),
            },
            "features": features_dict,
            "uncertainties_1sigma": unc_dict,
            "feature_observation_mask": dict(zip(self.feature_names, self.feature_observation_mask)),
            "metadata_disclaimers": self.metadata_disclaimers,
        }


class PropertySpatialIntelligenceExtractor:
    """
    Synthesizes geometry, building footprints, road network accessibility, terrain,
    and land-cover composition into a `PropertySpatialFeatureVector` with zero silent imputation.
    """

    SCHEMA_VERSION: str = "glitters.spatial_features.v1.1.0"

    FEATURE_NAMES: Tuple[str, ...] = (
        "parcel_utm_area_m2",
        "parcel_geodesic_area_m2",
        "parcel_utm_perimeter_m",
        "parcel_polsby_popper_compactness",
        "building_count",
        "building_footprint_area_m2",
        "building_coverage_ratio",
        "open_space_ratio",
        "largest_building_area_m2",
        "nearest_road_access_walk_m",
        "arterial_road_network_distance_m",
        "arterial_road_straight_line_distance_m",
        "arterial_road_detour_index",
        "arterial_road_travel_time_min",
        "nearest_hospital_network_distance_m",
        "nearest_hospital_travel_time_min",
        "nearest_school_network_distance_m",
        "nearest_school_travel_time_min",
        "utility_grid_proximity_m",
        "neighbourhood_parcel_count_500m",
        "neighbourhood_built_up_ratio",
        "elevation_mean_m",
        "slope_mean_deg",
        "slope_max_deg",
        "topographic_wetness_index_mean",
        "preliminary_flood_susceptibility_score",
        "preliminary_construction_suitability_score",
        "land_cover_vegetation_fraction",
        "land_cover_built_up_fraction",
        "land_cover_bare_soil_fraction",
        "land_cover_water_fraction",
        "mean_ndvi",
    )

    def __init__(self) -> None:
        self.crs_engine = KenyaCRSEngine()
        self.parcel_engine = ParcelIntelligenceEngine()

    def extract_feature_vector(
        self,
        property_id: str,
        parcel: ParcelRecord,
        admin_tag: KenyaAdminHierarchyTag,
        building_footprints_utm: Sequence[Polygon],
        surrounding_parcels_utm: Sequence[Polygon],
        network_engine: SpatialNetworkEngine,
        arterial_node_id: int,
        hospital_node_id: int,
        school_node_id: int,
        utility_distance_m: Optional[float] = None,
        terrain_summary: Optional[Dict[str, Any]] = None,
        land_cover_summary: Optional[Dict[str, Any]] = None,
        vertex_sigma_m: float = 0.15,
    ) -> PropertySpatialFeatureVector:
        p_metrics = self.parcel_engine.calculate_parcel_metrics(parcel)
        b_metrics = BuildingAndLandDetectionEngine.calculate_parcel_building_density(
            parcel.geometry_utm, building_footprints_utm, crs_epsg=parcel.utm_epsg
        )

        cx, cy = parcel.geometry_utm.centroid.x, parcel.geometry_utm.centroid.y
        centroid_wgs84 = self.crs_engine.transform_geometry(
            parcel.geometry_utm.centroid, parcel.utm_epsg, 4326
        )

        entry_node, walk_dist_m = network_engine.snap_point_to_nearest_node(cx, cy)
        route_arterial = network_engine.shortest_path_astar(entry_node, arterial_node_id)
        route_hospital = network_engine.shortest_path_astar(entry_node, hospital_node_id)
        route_school = network_engine.shortest_path_astar(entry_node, school_node_id)

        buf_500 = parcel.geometry_utm.buffer(500.0)
        nb_count = sum(1 for sp in surrounding_parcels_utm if buf_500.intersects(sp))

        n_verts = max(4, len(parcel.geometry_utm.exterior.coords) - 1)
        sigma_area_m2 = float(p_metrics["utm_perimeter_m"] * vertex_sigma_m / np.sqrt(n_verts))
        sigma_perim_m = float(np.sqrt(n_verts) * vertex_sigma_m)

        # Extract optional terrain & land-cover observations WITHOUT fallback fabrication
        elev_mean = _opt_float(terrain_summary, "elevation_mean_m")
        slope_mean = _opt_float(terrain_summary, "slope_mean_deg")
        slope_max = _opt_float(terrain_summary, "slope_max_deg")
        twi_mean = _opt_float(terrain_summary, "twi_mean")
        flood_score = _opt_float(terrain_summary, "flood_susceptibility_score")
        suit_score = _opt_float(terrain_summary, "construction_suitability_score")

        built_up_frac = _opt_float(land_cover_summary, "built_up_fraction")
        bare_soil_frac = _opt_float(land_cover_summary, "bare_soil_fraction")
        water_frac = _opt_float(land_cover_summary, "water_fraction")
        mean_ndvi = _opt_float(land_cover_summary, "mean_ndvi")

        dense_v = _opt_float(land_cover_summary, "dense_vegetation_fraction")
        sparse_v = _opt_float(land_cover_summary, "sparse_vegetation_fraction")
        if math.isfinite(dense_v) or math.isfinite(sparse_v):
            veg_frac = (dense_v if math.isfinite(dense_v) else 0.0) + (sparse_v if math.isfinite(sparse_v) else 0.0)
        else:
            veg_frac = _opt_float(land_cover_summary, "vegetation_fraction")

        util_dist = float(utility_distance_m) if (utility_distance_m is not None and math.isfinite(utility_distance_m)) else float("nan")

        raw_values = (
            float(p_metrics["utm_area_m2"]),
            float(p_metrics["geodesic_area_m2"]),
            float(p_metrics["utm_perimeter_m"]),
            float(p_metrics["polsby_popper_compactness"]),
            float(b_metrics["building_count"]),
            float(b_metrics["total_building_footprint_m2"]),
            float(b_metrics["building_coverage_ratio"]),
            float(b_metrics["open_space_ratio"]),
            float(b_metrics["largest_building_m2"]),
            float(walk_dist_m),
            float(route_arterial.network_distance_m),
            float(route_arterial.straight_line_distance_m),
            float(route_arterial.detour_circuity_index),
            float(route_arterial.travel_time_min),
            float(route_hospital.network_distance_m),
            float(route_hospital.travel_time_min),
            float(route_school.network_distance_m),
            float(route_school.travel_time_min),
            util_dist,
            float(nb_count),
            built_up_frac,
            elev_mean,
            slope_mean,
            slope_max,
            twi_mean,
            flood_score,
            suit_score,
            veg_frac,
            built_up_frac,
            bare_soil_frac,
            water_frac,
            mean_ndvi,
        )

        # Propagate uncertainties proportional to measured quantities, setting NaN where unobserved
        bcr_sigma = sigma_area_m2 / max(float(p_metrics["utm_area_m2"]), 1.0)
        raw_uncertainties = (
            sigma_area_m2,
            sigma_area_m2,
            sigma_perim_m,
            float(2.0 * sigma_perim_m / max(float(p_metrics["utm_perimeter_m"]), 1.0) * p_metrics["polsby_popper_compactness"]),
            0.0,
            sigma_area_m2 * 0.25,
            bcr_sigma,
            bcr_sigma,
            sigma_area_m2 * 0.15,
            vertex_sigma_m,
            max(1.0, 0.015 * float(route_arterial.network_distance_m)),
            vertex_sigma_m,
            0.015 * float(route_arterial.detour_circuity_index),
            0.12 * float(route_arterial.travel_time_min),
            max(1.0, 0.015 * float(route_hospital.network_distance_m)),
            0.12 * float(route_hospital.travel_time_min),
            max(1.0, 0.015 * float(route_school.network_distance_m)),
            0.12 * float(route_school.travel_time_min),
            vertex_sigma_m if math.isfinite(util_dist) else float("nan"),
            0.0,
            0.025 if math.isfinite(built_up_frac) else float("nan"),
            _opt_float(terrain_summary, "elevation_sigma_m") if math.isfinite(_opt_float(terrain_summary, "elevation_sigma_m")) else (2.0 if math.isfinite(elev_mean) else float("nan")),
            0.5 if math.isfinite(slope_mean) else float("nan"),
            1.0 if math.isfinite(slope_max) else float("nan"),
            0.25 if math.isfinite(twi_mean) else float("nan"),
            0.05 if math.isfinite(flood_score) else float("nan"),
            0.05 if math.isfinite(suit_score) else float("nan"),
            0.025 if math.isfinite(veg_frac) else float("nan"),
            0.025 if math.isfinite(built_up_frac) else float("nan"),
            0.025 if math.isfinite(bare_soil_frac) else float("nan"),
            0.020 if math.isfinite(water_frac) else float("nan"),
            _opt_float(land_cover_summary, "ndvi_sigma") if math.isfinite(_opt_float(land_cover_summary, "ndvi_sigma")) else (0.015 if math.isfinite(mean_ndvi) else float("nan")),
        )

        obs_mask = tuple(bool(math.isfinite(v)) for v in raw_values)
        obs_count = sum(1 for m in obs_mask if m)
        comp_ratio = float(obs_count / len(self.FEATURE_NAMES))
        missing_names = tuple(
            name for name, is_obs in zip(self.FEATURE_NAMES, obs_mask) if not is_obs
        )

        return PropertySpatialFeatureVector(
            schema_version=self.SCHEMA_VERSION,
            property_id=property_id,
            parcel_id=parcel.parcel_id,
            utm_epsg=parcel.utm_epsg,
            centroid_wgs84_lonlat=(float(centroid_wgs84.x), float(centroid_wgs84.y)),
            admin_hierarchy=admin_tag,
            observation_authority=parcel.authority.value,
            is_legally_verified_cadastre=parcel.is_legally_verified_cadastre,
            feature_names=self.FEATURE_NAMES,
            feature_values=raw_values,
            feature_uncertainties_1sigma=raw_uncertainties,
            feature_observation_mask=obs_mask,
            observed_feature_count=obs_count,
            completeness_ratio=comp_ratio,
            missing_feature_names=missing_names,
            metadata_disclaimers={
                "cadastral_disclaimer": (
                    "Geometrically inferred or public parcel boundaries are NOT legally verified "
                    "cadastral records and do not imply legal property ownership."
                ),
                "environmental_disclaimer": (
                    "Flood susceptibility and construction suitability scores are preliminary "
                    "algorithmic screenings and do not replace official engineering/NEMA/WRA certification."
                ),
            },
        )
