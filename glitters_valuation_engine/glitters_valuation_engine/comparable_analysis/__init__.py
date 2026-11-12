"""
Module: `comparable_analysis`
=============================
Implements the Comparable Sales Intelligence Engine with:
- Strict exclusion of rental evidence types (`RENTAL_LEASE_CONTRACT`, `RENTAL_ASKING_QUOTE`) (CRITICAL 1)
- Strict causal temporal filter `comp_tx.transaction_timestamp_days <= valuation_day` (CRITICAL 5)
- Configurable elasticities, appreciation rates, and asking-price haircuts via `ValuationMarketConfig`
"""
from __future__ import annotations
from typing import List, Dict, Any, Optional, Tuple
import math
import numpy as np

from ..contracts import (
    PropertyRecord,
    MarketTransaction,
    PriceEvidenceType,
    CAPITAL_SALE_EVIDENCE_TYPES,
    SubModelEstimate,
)
from ..config import ValuationMarketConfig
from ..data_validation import haversine_distance_km
from ..feature_engineering import compute_leasehold_discount_factor


SALE_EVIDENCE_RELIABILITY_PRIOR: Dict[PriceEvidenceType, float] = {
    PriceEvidenceType.VERIFIED_STAMP_DUTY_SALE: 1.00,
    PriceEvidenceType.VERIFIED_CONTRACT_SALE: 0.95,
    PriceEvidenceType.VALUER_HISTORICAL_RECORD: 0.88,
    PriceEvidenceType.ASKING_LISTING_PRICE: 0.40,
}

# Backwards compatibility exports
EVIDENCE_RELIABILITY_PRIOR = SALE_EVIDENCE_RELIABILITY_PRIOR
ASKING_PRICE_NEGOTIATION_HAIRCUT = 0.915


class ComparableSalesEngine:
    """
    Identifies, filters, weights, and adjusts comparable CAPITAL SALE transactions.
    Never admits rental observations (`RENTAL_LEASE_CONTRACT` or `RENTAL_ASKING_QUOTE`).
    """

    def __init__(
        self,
        config: Optional[ValuationMarketConfig] = None,
        max_comparables: int = 8,
    ) -> None:
        self.config = config or ValuationMarketConfig()
        self.max_radius_km = self.config.max_comparable_radius_km
        self.spatial_bandwidth_km = self.config.spatial_bandwidth_km
        self.temporal_decay_lambda = math.log(2.0) / max(1.0, self.config.temporal_half_life_days)
        self.max_comparables = max_comparables
        self.daily_appreciation_rate = self.config.quarterly_appreciation_rate / 91.25

    def _compute_adjusted_price(
        self,
        subject: PropertyRecord,
        comp_tx: MarketTransaction,
        valuation_day: float = 0.0,
    ) -> Tuple[float, Dict[str, float]]:
        comp = comp_tx.property_record
        base_price = float(comp_tx.observed_price_kes)
        adjustments: Dict[str, float] = {}

        # 1. Asking price haircut
        if comp_tx.evidence_type == PriceEvidenceType.ASKING_LISTING_PRICE:
            haircut_delta = base_price * (self.config.asking_price_negotiation_haircut - 1.0)
            base_price += haircut_delta
            adjustments["asking_price_haircut"] = haircut_delta

        # 2. Time appreciation adjustment from transaction date to valuation date
        age_days = max(0.0, valuation_day - comp_tx.transaction_timestamp_days)
        time_factor = math.exp(self.daily_appreciation_rate * min(age_days, 1095.0))
        time_delta = base_price * (time_factor - 1.0)
        price_after_time = base_price + time_delta
        adjustments["time_trend_adjustment"] = time_delta

        # 3. Land & Building decomposition using configurable parameters
        comp_has_bldg = comp.building_area_sqm > 5.0
        subj_has_bldg = subject.building_area_sqm > 5.0

        base_bldg_cost_sqm = (
            self.config.base_building_cost_sqm_kes
            + self.config.quality_building_cost_premium_sqm_kes * comp.construction_quality
        )
        comp_est_bldg_val = (
            comp.building_area_sqm
            * base_bldg_cost_sqm
            * comp.condition_score
            * math.exp(-self.config.age_depreciation_decay_lambda * comp.building_age_years)
        ) if comp_has_bldg else 0.0

        comp_est_bldg_val = min(comp_est_bldg_val, 0.78 * price_after_time)
        comp_implied_land_val = max(0.22 * price_after_time, price_after_time - comp_est_bldg_val)

        land_ratio = max(1.0, subject.land_area_sqm) / max(1.0, comp.land_area_sqm)
        land_scale = (land_ratio ** self.config.land_size_elasticity) - 1.0
        land_scale = max(-0.65, min(1.50, land_scale))
        land_delta = comp_implied_land_val * land_scale
        adjustments["land_size_adjustment"] = land_delta

        if subj_has_bldg and comp_has_bldg:
            subj_bldg_cost_sqm = (
                self.config.base_building_cost_sqm_kes
                + self.config.quality_building_cost_premium_sqm_kes * subject.construction_quality
            )
            subj_bldg_val = (
                subject.building_area_sqm
                * subj_bldg_cost_sqm
                * subject.condition_score
                * math.exp(-self.config.age_depreciation_decay_lambda * subject.building_age_years)
            )
            bldg_delta = subj_bldg_val - comp_est_bldg_val
        elif subj_has_bldg and not comp_has_bldg:
            subj_bldg_cost_sqm = (
                self.config.base_building_cost_sqm_kes
                + self.config.quality_building_cost_premium_sqm_kes * subject.construction_quality
            )
            bldg_delta = (
                subject.building_area_sqm
                * subj_bldg_cost_sqm
                * subject.condition_score
                * math.exp(-self.config.age_depreciation_decay_lambda * subject.building_age_years)
            )
        elif not subj_has_bldg and comp_has_bldg:
            bldg_delta = -comp_est_bldg_val
        else:
            bldg_delta = 0.0
        adjustments["building_and_condition_adjustment"] = bldg_delta

        loc_diff = (
            0.5 * (subject.accessibility_index - comp.accessibility_index)
            + 0.5 * (subject.infrastructure_score - comp.infrastructure_score)
        )
        loc_delta = price_after_time * (self.config.infrastructure_accessibility_elasticity * loc_diff)
        adjustments["infrastructure_accessibility_adjustment"] = loc_delta

        subj_lease = compute_leasehold_discount_factor(
            subject.tenure_type, subject.unexpired_lease_years, discount_rate=self.config.leasehold_discount_rate
        )
        comp_lease = compute_leasehold_discount_factor(
            comp.tenure_type, comp.unexpired_lease_years, discount_rate=self.config.leasehold_discount_rate
        )
        raw_adjusted = max(100_000.0, price_after_time + land_delta + bldg_delta + loc_delta)
        tenure_delta = raw_adjusted * ((subj_lease / max(0.3, comp_lease)) - 1.0)
        adjustments["tenure_adjustment"] = tenure_delta

        final_adjusted = max(100_000.0, raw_adjusted + tenure_delta)
        return final_adjusted, adjustments

    def evaluate_comparables(
        self,
        subject: PropertyRecord,
        candidate_pool: List[MarketTransaction],
        valuation_day: float = 0.0,
    ) -> Tuple[Optional[SubModelEstimate], float, List[Dict[str, Any]]]:
        """
        Selects, weights, and adjusts comparable capital sale transactions.
        Strictly excludes:
        1. Any rental evidence (`RENTAL_LEASE_CONTRACT`, `RENTAL_ASKING_QUOTE`) (CRITICAL 1).
        2. Any future transaction (`tx.transaction_timestamp_days > valuation_day`) (CRITICAL 5).
        """
        scored_candidates: List[Dict[str, Any]] = []

        for tx in candidate_pool:
            # CRITICAL 1: Never allow rental observations into comparable property sales
            if tx.evidence_type not in CAPITAL_SALE_EVIDENCE_TYPES:
                continue
            # CRITICAL 5: Never allow future transactions relative to valuation_day
            if tx.transaction_timestamp_days > valuation_day:
                continue
            if tx.property_record.property_id == subject.property_id:
                continue

            same_category = (tx.property_record.category == subject.category)
            compatible_land = (
                subject.category.value in ("vacant_land", "development_land", "agricultural")
                and tx.property_record.category.value in ("vacant_land", "development_land", "agricultural")
            )
            compatible_res = (
                subject.category.value in ("residential", "apartment_condo", "distressed")
                and tx.property_record.category.value in ("residential", "apartment_condo")
            )
            if not (same_category or compatible_land or compatible_res):
                continue

            dist_km = haversine_distance_km(
                subject.latitude,
                subject.longitude,
                tx.property_record.latitude,
                tx.property_record.longitude,
            )
            if dist_km > self.max_radius_km:
                continue

            age_days = valuation_day - tx.transaction_timestamp_days
            spatial_kernel = math.exp(-0.5 * (dist_km / self.spatial_bandwidth_km) ** 2)
            temporal_kernel = math.exp(-self.temporal_decay_lambda * age_days)

            d_land = abs(math.log(max(1.0, subject.land_area_sqm)) - math.log(max(1.0, tx.property_record.land_area_sqm)))
            d_bldg = abs(
                math.log(1.0 + subject.building_area_sqm) - math.log(1.0 + tx.property_record.building_area_sqm)
            )
            d_qual = abs(subject.construction_quality - tx.property_record.construction_quality)
            d_tier = abs(float(subject.neighbourhood_tier - tx.property_record.neighbourhood_tier))

            struct_dist_sq = (d_land / 0.65) ** 2 + (d_bldg / 0.75) ** 2 + (d_qual / 0.25) ** 2 + (d_tier / 1.0) ** 2
            structural_kernel = math.exp(-0.5 * struct_dist_sq)

            evidence_prior = SALE_EVIDENCE_RELIABILITY_PRIOR[tx.evidence_type] * tx.reliability_weight
            raw_weight = spatial_kernel * temporal_kernel * structural_kernel * evidence_prior

            if raw_weight < 1e-4:
                continue

            adj_price, adjustments = self._compute_adjusted_price(subject, tx, valuation_day)
            gross_adj_sum = sum(abs(v) for v in adjustments.values())
            gross_adj_pct = gross_adj_sum / max(1.0, tx.observed_price_kes)

            adj_penalty = math.exp(-0.8 * max(0.0, gross_adj_pct - 0.25))
            effective_kernel_weight = raw_weight * adj_penalty

            scored_candidates.append({
                "transaction_id": tx.transaction_id,
                "property_id": tx.property_record.property_id,
                "locality": tx.property_record.locality,
                "distance_km": round(dist_km, 3),
                "age_days": round(age_days, 1),
                "evidence_type": tx.evidence_type.value,
                "observed_price_kes": round(tx.observed_price_kes, 2),
                "adjusted_price_kes": round(adj_price, 2),
                "gross_adjustment_pct": round(gross_adj_pct, 4),
                "raw_kernel_weight": effective_kernel_weight,
                "adjustments": {k: round(v, 2) for k, v in adjustments.items()},
                "is_synthetic": tx.is_synthetic or tx.property_record.is_synthetic,
            })

        if not scored_candidates:
            return None, 0.0, []

        scored_candidates.sort(key=lambda c: c["raw_kernel_weight"], reverse=True)
        top_comps = scored_candidates[: self.max_comparables]

        weights = np.array([c["raw_kernel_weight"] for c in top_comps], dtype=np.float64)
        adj_prices = np.array([c["adjusted_price_kes"] for c in top_comps], dtype=np.float64)
        w_sum = float(np.sum(weights))
        norm_weights = weights / w_sum

        for c, nw in zip(top_comps, norm_weights):
            c["normalized_weight"] = round(float(nw), 4)

        point_estimate = float(np.sum(norm_weights * adj_prices))
        n_eff = (w_sum ** 2) / float(np.sum(weights ** 2))
        mean_gross_adj = float(np.sum(norm_weights * np.array([c["gross_adjustment_pct"] for c in top_comps])))

        cqs = (
            (1.0 - math.exp(-w_sum / 1.4))
            * min(1.0, n_eff / 3.5)
            * math.exp(-0.75 * mean_gross_adj)
        )
        cqs = float(max(0.0, min(1.0, cqs)))

        if len(top_comps) > 1:
            weighted_var = float(np.sum(norm_weights * (adj_prices - point_estimate) ** 2))
            dispersion_se = math.sqrt(weighted_var / max(1.0, n_eff - 0.5))
        else:
            dispersion_se = 0.18 * point_estimate

        std_error = max(0.04 * point_estimate, dispersion_se + 0.08 * (1.0 - cqs) * point_estimate)

        return (
            SubModelEstimate(
                approach_name="comparable_sales",
                point_estimate_kes=point_estimate,
                std_error_kes=std_error,
                applicable_weight_prior=0.35 * cqs + 0.05,
                diagnostics={
                    "num_comparables": len(top_comps),
                    "effective_sample_size": round(n_eff, 2),
                    "comparable_quality_score": round(cqs, 4),
                    "mean_gross_adjustment_pct": round(mean_gross_adj, 4),
                },
            ),
            cqs,
            top_comps,
        )
