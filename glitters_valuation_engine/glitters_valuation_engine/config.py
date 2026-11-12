"""
Module: `config`
================
Configurable Econometric & Market Prior Configuration (`ValuationMarketConfig`).
Replaces hard-coded constants across comparable analysis, feature engineering,
income, cost, and land valuation modules, and provides an empirical calibration
routine (`calibrate_from_transactions`) to fit priors from verified Kenyan sales.
"""
from __future__ import annotations
from dataclasses import dataclass, replace
from typing import List
import math
import numpy as np

from .contracts import (
    MarketTransaction,
    PriceEvidenceType,
    VERIFIED_CAPITAL_SALE_EVIDENCE_TYPES,
)


@dataclass(frozen=True)
class ValuationMarketConfig:
    """
    Configurable econometric and valuation parameters.
    All priors are explicitly documented as initial assumptions that can be
    overridden per deployment or calibrated empirically via `calibrate_from_transactions()`.
    """
    asking_price_negotiation_haircut: float = 0.915     # 8.5% empirical negotiation discount prior
    rental_asking_quote_haircut: float = 0.930          # 7.0% rental quote negotiation discount prior
    quarterly_appreciation_rate: float = 0.016          # 1.6% quarterly (~6.5% p.a.) appreciation prior
    land_size_elasticity: float = 0.82                  # Diminishing marginal returns exponent on land area
    leasehold_discount_rate: float = 0.090              # Real leasehold relativity discount rate
    age_depreciation_decay_lambda: float = 0.018        # Structural exponential age decay parameter
    infrastructure_accessibility_elasticity: float = 0.22
    base_building_cost_sqm_kes: float = 45_000.0
    quality_building_cost_premium_sqm_kes: float = 40_000.0
    cbk_risk_free_rate: float = 0.115
    real_estate_risk_premium: float = 0.035
    long_term_rent_escalation: float = 0.052
    construction_finance_rate: float = 0.155
    spatial_bandwidth_km: float = 2.2
    max_comparable_radius_km: float = 8.0
    temporal_half_life_days: float = 180.0
    is_empirically_calibrated_from_data: bool = False

    @classmethod
    def calibrate_from_transactions(
        cls,
        transactions: List[MarketTransaction],
        base_config: "ValuationMarketConfig | None" = None,
    ) -> "ValuationMarketConfig":
        """
        Empirically estimates the asking-to-verified sale negotiation haircut,
        quarterly appreciation rate, and land-size elasticity from a transaction pool.
        """
        cfg = base_config or cls()
        verified_sales = [
            t for t in transactions if t.evidence_type in VERIFIED_CAPITAL_SALE_EVIDENCE_TYPES
        ]
        asking_listings = [
            t for t in transactions if t.evidence_type == PriceEvidenceType.ASKING_LISTING_PRICE
        ]

        if len(verified_sales) < 15:
            return cfg

        # 1. Estimate asking-price haircut from median log price-per-sqm spread
        if len(asking_listings) >= 5:
            ver_ppsqm = np.median([
                t.observed_price_kes / max(1.0, t.property_record.land_area_sqm + 2.0 * t.property_record.building_area_sqm)
                for t in verified_sales
            ])
            ask_ppsqm = np.median([
                t.observed_price_kes / max(1.0, t.property_record.land_area_sqm + 2.0 * t.property_record.building_area_sqm)
                for t in asking_listings
            ])
            if ask_ppsqm > 0:
                raw_ratio = float(ver_ppsqm / ask_ppsqm)
                haircut = float(np.clip(raw_ratio, 0.78, 0.98))
            else:
                haircut = cfg.asking_price_negotiation_haircut
        else:
            haircut = cfg.asking_price_negotiation_haircut

        # 2. Estimate quarterly appreciation rate and land elasticity via OLS on log verified sales
        y = np.array([math.log(max(10_000.0, t.observed_price_kes)) for t in verified_sales])
        log_land = np.array([math.log(max(1.0, t.property_record.land_area_sqm)) for t in verified_sales])
        log_bldg = np.array([math.log(1.0 + max(0.0, t.property_record.building_area_sqm)) for t in verified_sales])
        t_quarters = np.array([t.transaction_timestamp_days / 91.25 for t in verified_sales])
        tier_inv = np.array([1.0 / max(1.0, float(t.property_record.neighbourhood_tier)) for t in verified_sales])

        X = np.column_stack([np.ones(len(verified_sales)), log_land, log_bldg, t_quarters, tier_inv])
        reg = np.eye(X.shape[1]) * 1.0
        reg[0, 0] = 1e-6
        beta = np.linalg.solve(X.T @ X + reg, X.T @ y)

        est_land_elasticity = float(np.clip(beta[1], 0.55, 1.05))
        est_quarterly_app = float(np.clip(beta[3], -0.04, 0.06))

        return replace(
            cfg,
            asking_price_negotiation_haircut=round(haircut, 4),
            quarterly_appreciation_rate=round(est_quarterly_app, 4),
            land_size_elasticity=round(est_land_elasticity, 4),
            is_empirically_calibrated_from_data=True,
        )
