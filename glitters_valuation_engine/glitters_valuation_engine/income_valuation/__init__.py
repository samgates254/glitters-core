"""
Module: `income_valuation`
==========================
Remediation for CRITICAL 1:
Dedicated Income & Rental Evidence Pathway.
- Consumes `RENTAL_LEASE_CONTRACT` and `RENTAL_ASKING_QUOTE` observations with explicit
  period conversion (`MONTHLY`, `ANNUAL`, `PER_SQM_MONTHLY`) and rental negotiation haircut
  to estimate market rent for a subject property when contractual rent is absent or needs
  market benchmarking.
- Converts Net Operating Income (NOI) into capital value via Direct Capitalization and
  10-Year Discounted Cash Flow (DCF).
"""
from __future__ import annotations
from typing import Optional, Dict, List, Tuple
import math
import numpy as np

from ..contracts import (
    PropertyRecord,
    MarketTransaction,
    PropertyCategory,
    PriceEvidenceType,
    RentalPeriodUnit,
    RENTAL_EVIDENCE_TYPES,
    SubModelEstimate,
)
from ..config import ValuationMarketConfig
from ..data_validation import haversine_distance_km
from ..feature_engineering import compute_leasehold_discount_factor


KENYA_BASE_CAP_RATES: Dict[PropertyCategory, float] = {
    PropertyCategory.RESIDENTIAL: 0.055,
    PropertyCategory.APARTMENT_CONDO: 0.068,
    PropertyCategory.RENTAL: 0.072,
    PropertyCategory.COMMERCIAL: 0.085,
    PropertyCategory.INDUSTRIAL: 0.090,
    PropertyCategory.INVESTMENT: 0.082,
    PropertyCategory.DISTRESSED: 0.105,
    PropertyCategory.AGRICULTURAL: 0.045,
    PropertyCategory.VACANT_LAND: 0.035,
    PropertyCategory.DEVELOPMENT_LAND: 0.040,
}

DEFAULT_MARKET_RENT_KES_PER_SQM: Dict[PropertyCategory, float] = {
    PropertyCategory.RESIDENTIAL: 650.0,
    PropertyCategory.APARTMENT_CONDO: 780.0,
    PropertyCategory.RENTAL: 720.0,
    PropertyCategory.COMMERCIAL: 1350.0,
    PropertyCategory.INDUSTRIAL: 580.0,
    PropertyCategory.INVESTMENT: 1150.0,
    PropertyCategory.DISTRESSED: 600.0,
}

RENTAL_EVIDENCE_RELIABILITY: Dict[PriceEvidenceType, float] = {
    PriceEvidenceType.RENTAL_LEASE_CONTRACT: 0.95,
    PriceEvidenceType.RENTAL_ASKING_QUOTE: 0.50,
}


class IncomeValuationEngine:
    """
    Converts contractual or comparable-derived rental income into capital value
    using Direct Capitalization and a 10-Year DCF model.
    """

    def __init__(
        self,
        config: Optional[ValuationMarketConfig] = None,
        holding_period_years: int = 10,
    ) -> None:
        self.config = config or ValuationMarketConfig()
        self.holding_period_years = holding_period_years

    def normalize_rental_observation_to_monthly_kes(self, tx: MarketTransaction) -> float:
        """
        Converts a `MarketTransaction` with `RENTAL_LEASE_CONTRACT` or `RENTAL_ASKING_QUOTE`
        into standardized monthly effective market rent (KES/month), applying the rental
        negotiation haircut to asking quotes.
        """
        if tx.evidence_type not in RENTAL_EVIDENCE_TYPES:
            raise ValueError(f"Expected rental evidence type, got {tx.evidence_type}.")
        raw = float(tx.observed_price_kes)
        if tx.rental_period_unit == RentalPeriodUnit.ANNUAL:
            monthly = raw / 12.0
        elif tx.rental_period_unit == RentalPeriodUnit.PER_SQM_MONTHLY:
            monthly = raw * max(1.0, tx.property_record.building_area_sqm)
        else:
            monthly = raw

        if tx.evidence_type == PriceEvidenceType.RENTAL_ASKING_QUOTE:
            monthly *= self.config.rental_asking_quote_haircut
        return monthly

    def estimate_market_rent_from_comparables(
        self,
        subject: PropertyRecord,
        rental_pool: List[MarketTransaction],
        valuation_day: float = 0.0,
    ) -> Tuple[Optional[float], int]:
        """
        Estimates monthly market rent (KES/month) for `subject` using nearby
        `RENTAL_LEASE_CONTRACT` and `RENTAL_ASKING_QUOTE` observations with
        timestamp <= valuation_day.
        """
        if subject.building_area_sqm <= 5.0 or not rental_pool:
            return None, 0

        weights: List[float] = []
        implied_rents: List[float] = []

        for tx in rental_pool:
            if tx.evidence_type not in RENTAL_EVIDENCE_TYPES:
                continue
            if tx.transaction_timestamp_days > valuation_day:
                continue
            if tx.property_record.building_area_sqm <= 5.0:
                continue

            dist_km = haversine_distance_km(
                subject.latitude,
                subject.longitude,
                tx.property_record.latitude,
                tx.property_record.longitude,
            )
            if dist_km > self.config.max_comparable_radius_km:
                continue

            age_days = max(0.0, valuation_day - tx.transaction_timestamp_days)
            w_spatial = math.exp(-0.5 * (dist_km / self.config.spatial_bandwidth_km) ** 2)
            w_temp = math.exp(-(math.log(2.0) / 180.0) * age_days)
            w_rel = RENTAL_EVIDENCE_RELIABILITY.get(tx.evidence_type, 0.5) * tx.reliability_weight
            w = w_spatial * w_temp * w_rel
            if w < 1e-3:
                continue

            comp_monthly_kes = self.normalize_rental_observation_to_monthly_kes(tx)
            comp_rent_per_sqm = comp_monthly_kes / max(1.0, tx.property_record.building_area_sqm)

            # Adjust for quality and condition differential
            qual_ratio = (0.5 * subject.construction_quality + 0.5 * subject.condition_score) / max(
                0.25, 0.5 * tx.property_record.construction_quality + 0.5 * tx.property_record.condition_score
            )
            subj_implied_monthly = comp_rent_per_sqm * subject.building_area_sqm * qual_ratio
            weights.append(w)
            implied_rents.append(subj_implied_monthly)

        if not weights:
            return None, 0

        w_arr = np.array(weights, dtype=np.float64)
        r_arr = np.array(implied_rents, dtype=np.float64)
        return float(np.sum(w_arr * r_arr) / np.sum(w_arr)), len(weights)

    def value_property(
        self,
        prop: PropertyRecord,
        rental_comparables: Optional[List[MarketTransaction]] = None,
        valuation_day: float = 0.0,
    ) -> Optional[SubModelEstimate]:
        if prop.building_area_sqm <= 5.0 and prop.monthly_gross_rent_kes is None:
            return None

        comp_rent_kes, n_rent_comps = self.estimate_market_rent_from_comparables(
            prop, rental_comparables or [], valuation_day=valuation_day
        )

        # 1. Determine Potential Gross Income (PGI)
        if prop.monthly_gross_rent_kes is not None and prop.monthly_gross_rent_kes > 0:
            if comp_rent_kes is not None:
                # Blend 75% contractual subject rent + 25% market rental comparable benchmark
                blended_monthly = 0.75 * float(prop.monthly_gross_rent_kes) + 0.25 * comp_rent_kes
                rent_source = "blended_contract_and_rental_comparables"
            else:
                blended_monthly = float(prop.monthly_gross_rent_kes)
                rent_source = "observed_subject_rent"
            annual_pgi = 12.0 * blended_monthly
            rent_is_observed = True
        elif comp_rent_kes is not None:
            annual_pgi = 12.0 * comp_rent_kes
            rent_is_observed = True
            rent_source = "rental_comparables_only"
        else:
            base_rent_sqm = DEFAULT_MARKET_RENT_KES_PER_SQM.get(prop.category, 650.0)
            tier_mult = {1: 1.45, 2: 1.15, 3: 0.85, 4: 0.60}.get(prop.neighbourhood_tier, 1.0)
            qual_mult = 0.65 + 0.50 * (0.5 * prop.construction_quality + 0.5 * prop.condition_score)
            monthly_imputed = prop.building_area_sqm * base_rent_sqm * tier_mult * qual_mult
            annual_pgi = 12.0 * monthly_imputed
            rent_is_observed = False
            rent_source = "parametric_tier_imputation"

        # 2. Effective Gross Income (EGI) and Net Operating Income (NOI)
        vacancy = max(0.02, min(0.45, prop.vacancy_rate))
        opex_ratio = max(0.08, min(0.55, prop.operating_expense_ratio))
        maintenance_reserve_ratio = 0.035 + 0.0015 * min(40.0, prop.building_age_years)

        egi = annual_pgi * (1.0 - vacancy)
        total_expense_ratio = min(0.65, opex_ratio + maintenance_reserve_ratio)
        noi_year1 = egi * (1.0 - total_expense_ratio)

        if noi_year1 <= 0:
            return None

        # 3. Risk-adjusted capitalization rate and discount rate
        base_cap = KENYA_BASE_CAP_RATES.get(prop.category, 0.075)
        tier_spread = (prop.neighbourhood_tier - 2) * 0.005
        illiquidity_spread = (1.0 - prop.accessibility_index) * 0.008
        cap_rate = max(0.04, min(0.16, base_cap + tier_spread + illiquidity_spread))

        discount_rate = max(
            cap_rate + 0.035,
            self.config.cbk_risk_free_rate + self.config.real_estate_risk_premium + tier_spread,
        )
        terminal_cap_rate = cap_rate + 0.0075

        # 4. Direct Capitalization Value
        v_direct_cap = noi_year1 / cap_rate

        # 5. 10-Year DCF Valuation
        pv_cashflows = 0.0
        g = min(discount_rate - 0.025, self.config.long_term_rent_escalation)
        for t in range(1, self.holding_period_years + 1):
            noi_t = noi_year1 * ((1.0 + g) ** (t - 1))
            pv_cashflows += noi_t / ((1.0 + discount_rate) ** t)

        noi_terminal = noi_year1 * ((1.0 + g) ** self.holding_period_years)
        terminal_value = noi_terminal / terminal_cap_rate
        pv_terminal = terminal_value / ((1.0 + discount_rate) ** self.holding_period_years)
        v_dcf = pv_cashflows + pv_terminal

        lease_factor = compute_leasehold_discount_factor(
            prop.tenure_type, prop.unexpired_lease_years, discount_rate=self.config.leasehold_discount_rate
        )
        blended_income_val = 0.5 * (v_direct_cap + v_dcf) * lease_factor

        if prop.category in (PropertyCategory.COMMERCIAL, PropertyCategory.INDUSTRIAL, PropertyCategory.INVESTMENT, PropertyCategory.RENTAL):
            prior_weight = 0.35 if rent_is_observed else 0.22
        else:
            prior_weight = 0.18 if rent_is_observed else 0.10

        rel_se = 0.11 if rent_is_observed else 0.19
        return SubModelEstimate(
            approach_name="income_capitalization_dcf",
            point_estimate_kes=blended_income_val,
            std_error_kes=blended_income_val * rel_se,
            applicable_weight_prior=prior_weight,
            diagnostics={
                "noi_year1_kes": round(noi_year1, 2),
                "going_in_cap_rate": round(cap_rate, 4),
                "discount_rate": round(discount_rate, 4),
                "direct_cap_value_kes": round(v_direct_cap, 2),
                "dcf_10yr_value_kes": round(v_dcf, 2),
                "rent_is_observed": rent_is_observed,
                "rent_source": rent_source,
                "num_rental_comparables_used": n_rent_comps,
            },
        )
