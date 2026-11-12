"""
Module: `cost_valuation`
========================
Implements Depreciated Replacement Cost (DRC) calibrated to JBC/IQSK benchmarks
and parameterized by `ValuationMarketConfig`.
"""
from __future__ import annotations
from typing import Dict, Optional
import math

from ..contracts import PropertyRecord, PropertyCategory, SubModelEstimate
from ..config import ValuationMarketConfig
from ..feature_engineering import compute_leasehold_discount_factor, KENYA_HUBS
from ..data_validation import haversine_distance_km


JBC_BASE_CONSTRUCTION_COST_SQM: Dict[PropertyCategory, float] = {
    PropertyCategory.RESIDENTIAL: 58_000.0,
    PropertyCategory.APARTMENT_CONDO: 64_000.0,
    PropertyCategory.COMMERCIAL: 78_000.0,
    PropertyCategory.INDUSTRIAL: 46_000.0,
    PropertyCategory.INVESTMENT: 72_000.0,
    PropertyCategory.RENTAL: 56_000.0,
    PropertyCategory.DISTRESSED: 55_000.0,
    PropertyCategory.AGRICULTURAL: 32_000.0,
    PropertyCategory.VACANT_LAND: 0.0,
    PropertyCategory.DEVELOPMENT_LAND: 0.0,
}


class CostValuationEngine:
    def __init__(self, config: Optional[ValuationMarketConfig] = None) -> None:
        self.config = config or ValuationMarketConfig()

    def estimate_baseline_land_value_per_sqm(self, prop: PropertyRecord) -> float:
        dists = [
            haversine_distance_km(prop.latitude, prop.longitude, h_lat, h_lon)
            for h_lat, h_lon in KENYA_HUBS.values()
        ]
        d_cbd = min(dists)
        tier_base = {
            1: 68_000.0,
            2: 34_000.0,
            3: 14_500.0,
            4: 4_200.0,
        }.get(prop.neighbourhood_tier, 15_000.0)

        gravity_mult = 0.35 + 1.15 * math.exp(-d_cbd / 14.0)
        infra_mult = 0.65 + 0.55 * (0.5 * prop.infrastructure_score + 0.5 * prop.accessibility_index)
        zoning_mult = 0.75 + 0.18 * min(4.0, prop.permitted_plot_ratio)

        if prop.category == PropertyCategory.AGRICULTURAL:
            return max(250.0, 0.18 * tier_base * gravity_mult * infra_mult)
        return float(tier_base * gravity_mult * infra_mult * zoning_mult)

    def value_property(self, prop: PropertyRecord) -> SubModelEstimate:
        land_ppsqm = self.estimate_baseline_land_value_per_sqm(prop)
        scale_adj = (max(500.0, prop.land_area_sqm) / 500.0) ** (self.config.land_size_elasticity - 0.94)
        land_value = prop.land_area_sqm * land_ppsqm * scale_adj

        if prop.category == PropertyCategory.APARTMENT_CONDO:
            land_value = (prop.building_area_sqm / max(1.0, prop.permitted_plot_ratio)) * land_ppsqm

        if prop.building_area_sqm <= 1.0:
            rcn = 0.0
            d_phys = 0.0
            d_func = 0.0
            d_ext = 0.0
            depreciated_improvements = 0.0
        else:
            base_cost = JBC_BASE_CONSTRUCTION_COST_SQM.get(prop.category, 58_000.0)
            quality_multiplier = 0.65 + 0.75 * prop.construction_quality
            professional_fees_and_contingencies = 1.14
            rcn = prop.building_area_sqm * base_cost * quality_multiplier * professional_fees_and_contingencies

            economic_life = 60.0 if prop.construction_quality >= 0.7 else 45.0
            age_ratio = min(0.90, max(0.0, prop.building_age_years) / economic_life)
            ross_age_factor = 0.5 * (age_ratio + age_ratio ** 2)
            condition_penalty = (1.0 - prop.condition_score) * 0.65
            d_phys = min(0.85, 0.55 * ross_age_factor + 0.45 * condition_penalty)

            d_func = 0.0
            if prop.building_age_years > 25.0 and prop.condition_score < 0.60:
                d_func += 0.10
            if prop.utilities_score < 0.50:
                d_func += 0.08

            d_ext = max(0.0, (0.60 - 0.5 * (prop.accessibility_index + prop.infrastructure_score)) * 0.35)

            site_works = 0.06 * rcn * prop.utilities_score
            depreciated_improvements = rcn * (1.0 - d_phys) * (1.0 - d_func) * (1.0 - d_ext) + site_works

        lease_factor = compute_leasehold_discount_factor(
            prop.tenure_type, prop.unexpired_lease_years, discount_rate=self.config.leasehold_discount_rate
        )
        total_drc = (land_value + depreciated_improvements) * lease_factor

        prior_weight = 0.24 if prop.category in (PropertyCategory.INDUSTRIAL, PropertyCategory.RESIDENTIAL) else 0.14
        return SubModelEstimate(
            approach_name="depreciated_replacement_cost",
            point_estimate_kes=total_drc,
            std_error_kes=total_drc * 0.15,
            applicable_weight_prior=prior_weight,
            diagnostics={
                "land_value_kes": round(land_value, 2),
                "replacement_cost_new_kes": round(rcn, 2),
                "physical_depreciation_pct": round(d_phys, 4),
                "functional_obsolescence_pct": round(d_func, 4),
                "external_obsolescence_pct": round(d_ext, 4),
                "depreciated_improvements_kes": round(depreciated_improvements, 2),
            },
        )
