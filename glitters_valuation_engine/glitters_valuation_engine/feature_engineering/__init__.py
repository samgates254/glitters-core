"""
Module: `feature_engineering`
=============================
Constructs spatial, structural, zoning, tenure, and macroeconomic covariates
parameterized by `ValuationMarketConfig`.
"""
from __future__ import annotations
from typing import Dict, List, Tuple, Optional
import math
import numpy as np

from ..contracts import PropertyRecord, TenureType, TitleVerificationStatus
from ..config import ValuationMarketConfig
from ..data_validation import haversine_distance_km

KENYA_HUBS: Dict[str, Tuple[float, float]] = {
    "Nairobi_CBD": (-1.286389, 36.817223),
    "Westlands_Node": (-1.2647, 36.8028),
    "Upper_Hill_Financial": (-1.2986, 36.8142),
    "JKIA_Airport": (-1.3192, 36.9278),
    "Mombasa_CBD": (-4.0435, 39.6682),
    "Kisumu_CBD": (-0.1022, 34.7617),
    "Nakuru_CBD": (-0.2833, 36.0667),
}


def compute_leasehold_discount_factor(
    tenure_type: TenureType,
    unexpired_years: float,
    discount_rate: float = 0.09,
) -> float:
    """
    Actuarial leasehold relativity factor relative to perpetual freehold:
        f_lease(T) = 1 - (1 + r)^(-T)
    with statutory renewal friction deduction when T < 25 years.
    """
    if tenure_type == TenureType.FREEHOLD:
        return 1.0
    if tenure_type == TenureType.INFORMAL_UNREGISTERED:
        return 0.62
    if tenure_type == TenureType.COMMUNITY_CUSTOMARY:
        return 0.75
    t = max(1.0, float(unexpired_years))
    pv_factor = 1.0 - (1.0 + discount_rate) ** (-t)
    if t < 25.0:
        renewal_penalty = 0.08 * ((25.0 - t) / 25.0)
        pv_factor = max(0.25, pv_factor - renewal_penalty)
    return float(min(1.0, pv_factor))


class FeatureEngineeringPipeline:
    """
    Transforms `PropertyRecord` objects into 22-dimensional feature vectors
    governed by `ValuationMarketConfig`.
    """

    FEATURE_NAMES: List[str] = [
        "log_land_area_sqm",
        "log_building_area_sqm",
        "has_building",
        "bedrooms",
        "bathrooms",
        "age_decay_factor",
        "condition_score",
        "construction_quality",
        "structural_composite",
        "accessibility_index",
        "infrastructure_score",
        "utilities_score",
        "locational_quality_index",
        "inv_tier_score",
        "permitted_plot_ratio",
        "permitted_ground_coverage",
        "unused_development_ratio",
        "leasehold_relativity_factor",
        "dist_nearest_cbd_km",
        "exp_cbd_gravity",
        "land_x_tier_interaction",
        "bldg_x_quality_interaction",
    ]

    def __init__(self, config: Optional[ValuationMarketConfig] = None) -> None:
        self.config = config or ValuationMarketConfig()

    def extract_features(self, prop: PropertyRecord) -> np.ndarray:
        log_land = math.log(max(1.0, prop.land_area_sqm))
        log_bldg = math.log(1.0 + max(0.0, prop.building_area_sqm))
        has_bldg = 1.0 if prop.building_area_sqm > 5.0 else 0.0

        age_decay = math.exp(-self.config.age_depreciation_decay_lambda * max(0.0, prop.building_age_years))
        struct_comp = has_bldg * prop.condition_score * prop.construction_quality * age_decay

        loc_quality = (
            0.40 * prop.accessibility_index
            + 0.35 * prop.infrastructure_score
            + 0.25 * prop.utilities_score
        )
        inv_tier = 1.0 / max(1.0, float(prop.neighbourhood_tier))

        max_allowable_bldg = prop.land_area_sqm * prop.permitted_plot_ratio
        current_utilization = min(1.0, prop.building_area_sqm / max(1.0, max_allowable_bldg))
        unused_dev_ratio = max(0.0, 1.0 - current_utilization) * prop.permitted_plot_ratio

        # Title risk factor (tenure relativity is applied structurally as an actuarial multiplier)
        lease_factor = 1.0
        if prop.title_status == TitleVerificationStatus.UNVERIFIED_MISSING:
            lease_factor = 0.90
        elif prop.title_status == TitleVerificationStatus.ENCUMBERED_MORTGAGE:
            lease_factor = 0.97

        dists = [
            haversine_distance_km(prop.latitude, prop.longitude, h_lat, h_lon)
            for h_lat, h_lon in KENYA_HUBS.values()
        ]
        dist_nearest_cbd = min(dists)
        exp_cbd_gravity = math.exp(-dist_nearest_cbd / 12.0)

        land_x_tier = log_land * inv_tier
        bldg_x_qual = log_bldg * prop.construction_quality * prop.condition_score

        return np.array([
            log_land,
            log_bldg,
            has_bldg,
            float(prop.bedrooms),
            float(prop.bathrooms),
            age_decay,
            prop.condition_score,
            prop.construction_quality,
            struct_comp,
            prop.accessibility_index,
            prop.infrastructure_score,
            prop.utilities_score,
            loc_quality,
            inv_tier,
            prop.permitted_plot_ratio,
            prop.permitted_ground_coverage,
            unused_dev_ratio,
            lease_factor,
            dist_nearest_cbd,
            exp_cbd_gravity,
            land_x_tier,
            bldg_x_qual,
        ], dtype=np.float64)

    def transform_batch(self, properties: List[PropertyRecord]) -> np.ndarray:
        if not properties:
            return np.zeros((0, len(self.FEATURE_NAMES)), dtype=np.float64)
        return np.vstack([self.extract_features(p) for p in properties])
