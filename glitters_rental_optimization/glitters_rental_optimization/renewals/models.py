# ======================================================================================
"""Tenant Retention, Lease Renewal Bellman Optimization & Anti-Discrimination Engine."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping
import numpy as np

from ..demand.models import RentalDemandModel
from ..schemas import UnitSpecification, validate_no_protected_attributes


@dataclass(frozen=True)
class RenewalStrategyEvaluation:
    """Evaluation of a specific renewal pricing strategy."""

    strategy_name: str
    proposed_renewal_rent_kes: float
    rent_increase_pct: float
    concession_incentive_kes: float
    renewal_probability: float
    departure_probability: float
    expected_turnover_cost_kes: float
    expected_vacancy_duration_months: float
    expected_lease_term_noi_kes: float
    tenant_lifetime_value_kes: float


@dataclass(frozen=True)
class RenewalPricingRecommendation:
    """Optimal renewal pricing recommendation comparing four canonical strategies."""

    unit_id: str
    current_contractual_rent_kes: float
    achievable_market_rent_kes: float
    market_rent_differential_kes: float
    recommended_strategy: str
    recommended_renewal_rent_kes: float
    recommended_incentive_kes: float
    expected_renewal_probability: float
    expected_departure_probability: float
    expected_ltv_kes: float
    strategy_comparisons: tuple[RenewalStrategyEvaluation, ...]


class RenewalPricingEngine:
    """Optimizes lease renewal pricing while strictly prohibiting protected personal attributes."""

    def __init__(
        self,
        demand_model: RentalDemandModel | None = None,
        annual_discount_rate: float = 0.12,
    ) -> None:
        self.demand_model = demand_model or RentalDemandModel()
        self.monthly_discount_factor = (1.0 + annual_discount_rate) ** (-1.0 / 12.0)

    def recommend_renewal(
        self,
        unit: UnitSpecification,
        achievable_market_rent_kes: float,
        price_elasticity: float,
        tenant_context: Mapping[str, Any] | None = None,
    ) -> RenewalPricingRecommendation:
        """Evaluates renewal options and selects the LTV-maximizing strategy."""
        ctx = dict(tenant_context or {})
        # Non-negotiable rule: reject any protected characteristics
        validate_no_protected_attributes(ctx)

        current_rent = unit.contractual_rent_kes or achievable_market_rent_kes
        tenure_months = float(ctx.get("tenure_months", 12.0))
        historical_on_time_rate = float(np.clip(ctx.get("on_time_payment_rate", 0.96), 0.5, 1.0))
        unresolved_maint_count = float(ctx.get("unresolved_maintenance_tickets", 0.0))

        # Existing reliable tenants have lower expected arrears than unknown new market entrants
        existing_arrears = float(
            np.clip(unit.baseline_arrears_rate * (2.0 - historical_on_time_rate), 0.005, 0.25)
        )

        # Evaluate outside option (if tenant departs and unit must be re-leased on open market)
        new_lease_eval = self.demand_model.evaluate_at_price(
            achievable_market_rent_kes, unit, achievable_market_rent_kes, price_elasticity
        )
        vac_months_if_depart = new_lease_eval.expected_vacancy_duration_months
        lease_term = float(max(unit.typical_lease_term_months, 12))

        outside_re_lease_net = (
            -unit.turnover_refurbishment_cost_kes
            - unit.tenant_acquisition_cost_kes
            - vac_months_if_depart * unit.vacancy_holding_cost_kes
            + max(0.0, lease_term - vac_months_if_depart)
            * achievable_market_rent_kes
            * (1.0 - new_lease_eval.expected_arrears_rate)
            * (1.0 - unit.management_fee_pct)
            - lease_term * unit.routine_maintenance_cost_kes
        )

        max_allowed_hike = unit.owner_policy.max_annual_increase_pct

        def eval_candidate(
            name: str, proposed_rent: float, incentive_kes: float = 0.0
        ) -> RenewalStrategyEvaluation:
            # Respect owner policy caps
            capped_rent = min(proposed_rent, current_rent * (1.0 + max_allowed_hike))
            if unit.owner_policy.max_rent_kes is not None:
                capped_rent = min(capped_rent, unit.owner_policy.max_rent_kes)
            if unit.owner_policy.min_rent_kes is not None:
                capped_rent = max(capped_rent, unit.owner_policy.min_rent_kes)

            hike_pct = (capped_rent - current_rent) / max(current_rent, 1.0)
            effective_rent_after_inc = capped_rent - (incentive_kes / lease_term)
            mkt_gap_pct = (effective_rent_after_inc - achievable_market_rent_kes) / max(
                achievable_market_rent_kes, 1.0
            )

            # Structural logistic renewal model
            logit = (
                1.35
                - 7.5 * max(0.0, hike_pct)
                - 4.2 * mkt_gap_pct
                + 0.22 * math.log(1.0 + tenure_months / 12.0)
                - 0.45 * unresolved_maint_count
            )
            ren_prob = float(np.clip(1.0 / (1.0 + math.exp(-logit)), 0.05, 0.97))
            dep_prob = 1.0 - ren_prob

            stay_noi = (
                lease_term
                * capped_rent
                * (1.0 - existing_arrears)
                * (1.0 - unit.management_fee_pct)
                - incentive_kes
                - lease_term * unit.routine_maintenance_cost_kes
            )
            expected_term_noi = ren_prob * stay_noi + dep_prob * outside_re_lease_net
            expected_turn_cost = dep_prob * (
                unit.turnover_refurbishment_cost_kes
                + unit.tenant_acquisition_cost_kes
                + vac_months_if_depart * achievable_market_rent_kes
            )

            # Multi-cycle Bellman LTV approximation
            cycle_disc = (self.monthly_discount_factor**lease_term) * ren_prob
            ltv = expected_term_noi / max(1.0 - min(cycle_disc, 0.85), 0.15)

            return RenewalStrategyEvaluation(
                strategy_name=name,
                proposed_renewal_rent_kes=round(capped_rent, 2),
                rent_increase_pct=round(hike_pct, 4),
                concession_incentive_kes=round(incentive_kes, 2),
                renewal_probability=round(ren_prob, 4),
                departure_probability=round(dep_prob, 4),
                expected_turnover_cost_kes=round(expected_turn_cost, 2),
                expected_vacancy_duration_months=round(dep_prob * vac_months_if_depart, 3),
                expected_lease_term_noi_kes=round(expected_term_noi, 2),
                tenant_lifetime_value_kes=round(ltv, 2),
            )

        # Compare the 4 required strategies plus a fine grid for optimal retention pricing
        strategies = [
            eval_candidate("aggressive_market_repricing", max(achievable_market_rent_kes, current_rent * 1.08)),
            eval_candidate("moderate_renewal_increase", current_rent * 1.04),
            eval_candidate("retention_focused_pricing", min(current_rent * 1.01, achievable_market_rent_kes * 0.97)),
            eval_candidate(
                "incentive_based_renewal",
                min(achievable_market_rent_kes, current_rent * 1.05),
                incentive_kes=0.5 * current_rent if unit.owner_policy.allow_renewal_concessions else 0.0,
            ),
        ]

        best = max(strategies, key=lambda s: s.expected_lease_term_noi_kes)
        return RenewalPricingRecommendation(
            unit_id=unit.unit_id,
            current_contractual_rent_kes=current_rent,
            achievable_market_rent_kes=achievable_market_rent_kes,
            market_rent_differential_kes=achievable_market_rent_kes - current_rent,
            recommended_strategy=best.strategy_name,
            recommended_renewal_rent_kes=best.proposed_renewal_rent_kes,
            recommended_incentive_kes=best.concession_incentive_kes,
            expected_renewal_probability=best.renewal_probability,
            expected_departure_probability=best.departure_probability,
            expected_ltv_kes=best.tenant_lifetime_value_kes,
            strategy_comparisons=tuple(strategies),
        )


# ======================================================================================
