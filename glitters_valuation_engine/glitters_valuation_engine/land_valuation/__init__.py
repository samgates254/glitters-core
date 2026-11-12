"""
Module: `land_valuation`
========================
Residual Land Valuation and Highest-and-Best-Use (HBU) Optimization under PLUPA 2019.
"""
from __future__ import annotations
from typing import Dict, Any, Optional, List

from ..contracts import PropertyRecord, PropertyCategory, SubModelEstimate
from ..config import ValuationMarketConfig
from ..feature_engineering import compute_leasehold_discount_factor


CANDIDATE_USES: List[Dict[str, Any]] = [
    {
        "use_name": "high_density_residential_apartments",
        "allowed_zones": {"RES_HIGH", "RES_MID", "MIXED_USE"},
        "sale_price_per_nsa_sqm": 118_000.0,
        "construction_cost_per_gfa_sqm": 62_000.0,
        "efficiency_nsa_to_gfa": 0.80,
        "construction_months": 24,
        "developer_profit_margin_on_gdv": 0.18,
    },
    {
        "use_name": "townhouse_gated_villa_development",
        "allowed_zones": {"RES_LOW", "RES_MID"},
        "sale_price_per_nsa_sqm": 135_000.0,
        "construction_cost_per_gfa_sqm": 68_000.0,
        "efficiency_nsa_to_gfa": 0.86,
        "construction_months": 18,
        "developer_profit_margin_on_gdv": 0.17,
    },
    {
        "use_name": "grade_a_commercial_mixed_use",
        "allowed_zones": {"COMMERCIAL", "MIXED_USE"},
        "sale_price_per_nsa_sqm": 162_000.0,
        "construction_cost_per_gfa_sqm": 78_000.0,
        "efficiency_nsa_to_gfa": 0.78,
        "construction_months": 28,
        "developer_profit_margin_on_gdv": 0.20,
    },
    {
        "use_name": "light_industrial_warehousing_park",
        "allowed_zones": {"INDUSTRIAL", "COMMERCIAL"},
        "sale_price_per_nsa_sqm": 78_000.0,
        "construction_cost_per_gfa_sqm": 44_000.0,
        "efficiency_nsa_to_gfa": 0.90,
        "construction_months": 15,
        "developer_profit_margin_on_gdv": 0.16,
    },
]


class LandAndDevelopmentValuationEngine:
    def __init__(self, config: Optional[ValuationMarketConfig] = None) -> None:
        self.config = config or ValuationMarketConfig()
        self.annual_finance_rate = self.config.construction_finance_rate

    def evaluate_residual_land_value(self, prop: PropertyRecord) -> Optional[SubModelEstimate]:
        if prop.category == PropertyCategory.APARTMENT_CONDO:
            return None

        tier_price_mult = {1: 1.38, 2: 1.12, 3: 0.85, 4: 0.62}.get(prop.neighbourhood_tier, 1.0)
        infra_access_mult = 0.70 + 0.40 * (0.5 * prop.accessibility_index + 0.5 * prop.infrastructure_score)

        best_rlv = -1e18
        best_scheme: Optional[Dict[str, Any]] = None

        for scheme in CANDIDATE_USES:
            if prop.zoning_code not in scheme["allowed_zones"] and prop.category not in (
                PropertyCategory.VACANT_LAND,
                PropertyCategory.DEVELOPMENT_LAND,
            ):
                continue

            max_gfa_by_far = prop.land_area_sqm * prop.permitted_plot_ratio
            max_gfa_by_gc = prop.land_area_sqm * prop.permitted_ground_coverage * float(prop.max_floors)
            buildable_gfa = min(max_gfa_by_far, max_gfa_by_gc)
            net_saleable_area = buildable_gfa * scheme["efficiency_nsa_to_gfa"]

            unit_sale_price = scheme["sale_price_per_nsa_sqm"] * tier_price_mult * infra_access_mult
            gdv = net_saleable_area * unit_sale_price

            hard_cost = buildable_gfa * scheme["construction_cost_per_gfa_sqm"]
            soft_statutory_fees = 0.125 * hard_cost
            years_const = scheme["construction_months"] / 12.0
            finance_cost = (hard_cost + soft_statutory_fees) * 0.55 * self.annual_finance_rate * years_const
            marketing_legal_cost = 0.035 * gdv
            developer_profit = scheme["developer_profit_margin_on_gdv"] * gdv

            net_proceeds_at_completion = gdv - (hard_cost + soft_statutory_fees + finance_cost + marketing_legal_cost + developer_profit)
            rlv = net_proceeds_at_completion / ((1.0 + 0.12) ** (0.5 * years_const))

            if rlv > best_rlv:
                best_rlv = rlv
                best_scheme = {
                    "selected_hbu_scheme": scheme["use_name"],
                    "buildable_gfa_sqm": round(buildable_gfa, 2),
                    "gross_development_value_kes": round(gdv, 2),
                    "hard_and_soft_costs_kes": round(hard_cost + soft_statutory_fees, 2),
                    "finance_cost_kes": round(finance_cost, 2),
                    "developer_profit_kes": round(developer_profit, 2),
                }

        if best_rlv <= 50_000.0 or best_scheme is None:
            return None

        lease_factor = compute_leasehold_discount_factor(
            prop.tenure_type, prop.unexpired_lease_years, discount_rate=self.config.leasehold_discount_rate
        )
        adjusted_rlv = best_rlv * lease_factor

        if prop.category in (PropertyCategory.VACANT_LAND, PropertyCategory.DEVELOPMENT_LAND):
            prior_weight = 0.36
        elif prop.building_area_sqm < 0.15 * (prop.land_area_sqm * prop.permitted_plot_ratio):
            prior_weight = 0.22
        else:
            prior_weight = 0.08

        return SubModelEstimate(
            approach_name="residual_land_hbu",
            point_estimate_kes=adjusted_rlv,
            std_error_kes=adjusted_rlv * 0.17,
            applicable_weight_prior=prior_weight,
            diagnostics=best_scheme,
        )
