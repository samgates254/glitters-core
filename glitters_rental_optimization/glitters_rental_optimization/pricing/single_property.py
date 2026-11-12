from __future__ import annotations
"""Single-Property Constrained Risk-Adjusted Rental Pricing Optimizer.

Phase 1 Audit Remediation (v2.0):
1. Standardized enum `PricingStatus.CONFLICTING_OWNER_POLICY` across spec, guide, and code.
2. Explicit workflow separation between active non-expiring leases (`LOCKED_CONTRACTUAL` when
   `months_remaining_on_lease > 3`) and expiring renewal windows (`RENEWAL_WORKFLOW_REQUIRED`
   when `1 <= months_remaining_on_lease <= 3`), delegating to `RenewalPricingEngine` so current
   contractual rent is never mutated while generating a forward-dated renewal recommendation.
3. Populates dimensionally consistent `weibull_scale_eta_months`, `instantaneous_hazard_monthly`,
   and `causal_identification_status` on every recommendation.
"""


from typing import Any, Mapping, Sequence
import numpy as np
from scipy.optimize import minimize_scalar

from ..demand.models import RentalDemandModel
from ..elasticity.estimator import HedonicElasticityEstimator
from ..kenyan_market import KENYAN_SEGMENT_PRIORS, validate_unit_specification_plausibility
from ..renewals.models import RenewalPricingEngine
from ..schemas import (
    ConfidenceTier,
    LeaseStatus,
    MarketComparableListing,
    PricingStatus,
    RentalMonetaryLedger,
    UnitPricingRecommendation,
    UnitSpecification,
)
from ..uncertainty.models import UncertaintyRiskEngine
from ..vacancy.models import VacancyOptimizationEngine


class SinglePropertyPricingOptimizer:
    """Determines optimal, economically defensible single-unit rental price."""

    def __init__(
        self,
        demand_model: RentalDemandModel | None = None,
        elasticity_estimator: HedonicElasticityEstimator | None = None,
        uncertainty_engine: UncertaintyRiskEngine | None = None,
        vacancy_engine: VacancyOptimizationEngine | None = None,
        renewal_engine: RenewalPricingEngine | None = None,
        cvar_risk_weight: float = 0.20,
    ) -> None:
        self.demand_model = demand_model or RentalDemandModel()
        self.elasticity_estimator = elasticity_estimator or HedonicElasticityEstimator()
        self.uncertainty_engine = uncertainty_engine or UncertaintyRiskEngine(n_simulations=500)
        self.vacancy_engine = vacancy_engine or VacancyOptimizationEngine(self.demand_model)
        self.renewal_engine = renewal_engine or RenewalPricingEngine(self.demand_model)
        self.cvar_risk_weight = cvar_risk_weight

    def optimize_unit_price(
        self,
        unit: UnitSpecification,
        comparables: Sequence[MarketComparableListing],
        observed_lease_records: Sequence[dict[str, float]] | None = None,
        calendar_month: int = 11,
        tenant_renewal_context: Mapping[str, Any] | None = None,
    ) -> UnitPricingRecommendation:
        """Optimizes single-unit rent while strictly enforcing contractual, renewal, owner, and safety rules."""
        spec_warnings = validate_unit_specification_plausibility(unit)
        val = self.elasticity_estimator.estimate_market_rent_and_elasticity(
            unit, comparables, observed_lease_records
        )
        achievable_mkt = val.achievable_market_rent_kes
        elasticity = val.price_elasticity

        # Rule 1a: Active non-expiring lease -> immutable LOCKED_CONTRACTUAL
        if (
            unit.lease_status == LeaseStatus.OCCUPIED_ACTIVE
            and unit.months_remaining_on_lease > 3
            and unit.contractual_rent_kes is not None
            and unit.contractual_rent_kes > 0
        ):
            locked_rent = unit.contractual_rent_kes
            eval_locked = self.demand_model.evaluate_at_price(
                locked_rent, unit, achievable_mkt, elasticity, calendar_month
            )
            unc_locked = self.uncertainty_engine.simulate_price_risk(
                locked_rent,
                unit,
                achievable_mkt,
                elasticity,
                val.elasticity_std_error,
                self.demand_model,
                calendar_month,
            )
            ledger = RentalMonetaryLedger(
                asking_rent_kes=unit.current_asking_rent_kes,
                achievable_market_rent_kes=achievable_mkt,
                contractual_rent_kes=locked_rent,
                recommended_rent_kes=locked_rent,
                actual_collected_rent_kes=unit.actual_collected_rent_kes,
                effective_rental_revenue_kes=eval_locked.expected_effective_rental_revenue_kes,
                net_operating_income_kes=eval_locked.expected_monthly_noi_kes,
            )
            return UnitPricingRecommendation(
                unit_id=unit.unit_id,
                property_id=unit.property_id,
                segment=unit.segment,
                county=unit.county,
                neighbourhood=unit.neighbourhood,
                status=PricingStatus.LOCKED_CONTRACTUAL,
                confidence_tier=val.confidence_tier,
                ledger=ledger,
                expected_application_arrival_rate=eval_locked.application_arrival_rate,
                expected_conversion_probability=eval_locked.conversion_probability,
                weibull_scale_eta_months=eval_locked.weibull_scale_eta_months,
                instantaneous_hazard_monthly=eval_locked.instantaneous_hazard_monthly,
                expected_vacancy_duration_months=eval_locked.expected_vacancy_duration_months,
                expected_occupancy_duration_months=eval_locked.expected_occupancy_duration_months,
                steady_state_occupancy_prob=eval_locked.steady_state_occupancy_prob,
                finite_horizon_occupancy_prob=eval_locked.finite_horizon_occupancy_prob,
                expected_renewal_probability=eval_locked.renewal_probability,
                price_elasticity=elasticity,
                causal_identification_status=val.causal_identification_status,
                rent_prediction_interval_80=val.prediction_interval_80,
                noi_cvar_10_kes=unc_locked.noi_cvar_10_kes,
                vacancy_cut_justified=False,
                shadow_price_owner_floor_kes=0.0,
                requires_owner_approval=False,
                effective_date_offset_months=0,
                diagnostics={
                    "workflow": "active_contractual_lock",
                    "reason": "Active contractual lease (>3 months remaining); contractual rent is immutable.",
                    "months_remaining_on_lease": unit.months_remaining_on_lease,
                    "spec_warnings": spec_warnings,
                },
            )

        # Rule 1b: Expiring lease (1..3 months remaining or EXPIRING_RENEWAL_WINDOW) -> Renewal Workflow
        if (
            unit.lease_status == LeaseStatus.EXPIRING_RENEWAL_WINDOW
            or (
                unit.lease_status == LeaseStatus.OCCUPIED_ACTIVE
                and 0 < unit.months_remaining_on_lease <= 3
            )
        ) and unit.contractual_rent_kes is not None:
            ren_rec = self.renewal_engine.recommend_renewal(
                unit=unit,
                achievable_market_rent_kes=achievable_mkt,
                price_elasticity=elasticity,
                tenant_context=tenant_renewal_context,
            )
            p_ren = ren_rec.recommended_renewal_rent_kes
            eval_ren = self.demand_model.evaluate_at_price(
                p_ren, unit, achievable_mkt, elasticity, calendar_month
            )
            unc_ren = self.uncertainty_engine.simulate_price_risk(
                p_ren,
                unit,
                achievable_mkt,
                elasticity,
                val.elasticity_std_error,
                self.demand_model,
                calendar_month,
            )
            ledger = RentalMonetaryLedger(
                asking_rent_kes=unit.current_asking_rent_kes,
                achievable_market_rent_kes=achievable_mkt,
                contractual_rent_kes=unit.contractual_rent_kes,
                recommended_rent_kes=p_ren,
                actual_collected_rent_kes=unit.actual_collected_rent_kes,
                effective_rental_revenue_kes=eval_ren.expected_effective_rental_revenue_kes,
                net_operating_income_kes=eval_ren.expected_monthly_noi_kes,
            )
            return UnitPricingRecommendation(
                unit_id=unit.unit_id,
                property_id=unit.property_id,
                segment=unit.segment,
                county=unit.county,
                neighbourhood=unit.neighbourhood,
                status=PricingStatus.RENEWAL_WORKFLOW_REQUIRED,
                confidence_tier=val.confidence_tier,
                ledger=ledger,
                expected_application_arrival_rate=eval_ren.application_arrival_rate,
                expected_conversion_probability=eval_ren.conversion_probability,
                weibull_scale_eta_months=eval_ren.weibull_scale_eta_months,
                instantaneous_hazard_monthly=eval_ren.instantaneous_hazard_monthly,
                expected_vacancy_duration_months=eval_ren.expected_vacancy_duration_months,
                expected_occupancy_duration_months=eval_ren.expected_occupancy_duration_months,
                steady_state_occupancy_prob=eval_ren.steady_state_occupancy_prob,
                finite_horizon_occupancy_prob=eval_ren.finite_horizon_occupancy_prob,
                expected_renewal_probability=ren_rec.expected_renewal_probability,
                price_elasticity=elasticity,
                causal_identification_status=val.causal_identification_status,
                rent_prediction_interval_80=val.prediction_interval_80,
                noi_cvar_10_kes=unc_ren.noi_cvar_10_kes,
                vacancy_cut_justified=False,
                shadow_price_owner_floor_kes=0.0,
                requires_owner_approval=True,
                effective_date_offset_months=max(1, unit.months_remaining_on_lease),
                diagnostics={
                    "workflow": "lease_renewal_bellman_offer",
                    "recommended_renewal_strategy": ren_rec.recommended_strategy,
                    "recommended_incentive_kes": ren_rec.recommended_incentive_kes,
                    "current_contractual_rent_immutable_until_expiry": True,
                    "spec_warnings": spec_warnings,
                },
            )

        # Rule 2: Safe fallback when data is insufficient (N_eff < 3.0) or out-of-distribution
        if val.confidence_tier in (
            ConfidenceTier.INSUFFICIENT_DATA_SAFE_FALLBACK,
            ConfidenceTier.OUT_OF_DISTRIBUTION,
        ):
            safe_anchor = (
                unit.contractual_rent_kes
                or (
                    unit.current_asking_rent_kes
                    if unit.current_asking_rent_kes
                    and unit.current_asking_rent_kes <= achievable_mkt * 1.4
                    else None
                )
                or achievable_mkt
            )
            if unit.owner_policy.min_rent_kes is not None:
                safe_anchor = max(safe_anchor, unit.owner_policy.min_rent_kes)
            if (
                unit.owner_policy.max_rent_kes is not None
                and not unit.owner_policy.has_conflict()
            ):
                safe_anchor = min(safe_anchor, unit.owner_policy.max_rent_kes)

            eval_safe = self.demand_model.evaluate_at_price(
                safe_anchor, unit, achievable_mkt, elasticity, calendar_month
            )
            unc_safe = self.uncertainty_engine.simulate_price_risk(
                safe_anchor,
                unit,
                achievable_mkt,
                elasticity,
                val.elasticity_std_error,
                self.demand_model,
                calendar_month,
            )
            ledger = RentalMonetaryLedger(
                asking_rent_kes=unit.current_asking_rent_kes,
                achievable_market_rent_kes=achievable_mkt,
                contractual_rent_kes=unit.contractual_rent_kes,
                recommended_rent_kes=safe_anchor,
                actual_collected_rent_kes=unit.actual_collected_rent_kes,
                effective_rental_revenue_kes=eval_safe.expected_effective_rental_revenue_kes,
                net_operating_income_kes=eval_safe.expected_monthly_noi_kes,
            )
            return UnitPricingRecommendation(
                unit_id=unit.unit_id,
                property_id=unit.property_id,
                segment=unit.segment,
                county=unit.county,
                neighbourhood=unit.neighbourhood,
                status=PricingStatus.SAFE_FALLBACK_ABSTENTION,
                confidence_tier=val.confidence_tier,
                ledger=ledger,
                expected_application_arrival_rate=eval_safe.application_arrival_rate,
                expected_conversion_probability=eval_safe.conversion_probability,
                weibull_scale_eta_months=eval_safe.weibull_scale_eta_months,
                instantaneous_hazard_monthly=eval_safe.instantaneous_hazard_monthly,
                expected_vacancy_duration_months=eval_safe.expected_vacancy_duration_months,
                expected_occupancy_duration_months=eval_safe.expected_occupancy_duration_months,
                steady_state_occupancy_prob=eval_safe.steady_state_occupancy_prob,
                finite_horizon_occupancy_prob=eval_safe.finite_horizon_occupancy_prob,
                expected_renewal_probability=eval_safe.renewal_probability,
                price_elasticity=elasticity,
                causal_identification_status=val.causal_identification_status,
                rent_prediction_interval_80=val.prediction_interval_80,
                noi_cvar_10_kes=unc_safe.noi_cvar_10_kes,
                vacancy_cut_justified=False,
                shadow_price_owner_floor_kes=0.0,
                requires_owner_approval=True,
                effective_date_offset_months=0,
                diagnostics={
                    "workflow": "safe_fallback_abstention",
                    "reason": "Safe fallback triggered due to N_eff < 3.0 or out-of-distribution specification.",
                    "effective_sample_size": val.effective_sample_size,
                    "mahalanobis_distance": val.mahalanobis_distance,
                    "spec_warnings": spec_warnings,
                },
            )

        # Rule 3: Construct feasible bounds [p_min, p_max] and detect policy conflicts
        prior = KENYAN_SEGMENT_PRIORS[unit.segment]
        cost_floor = (
            unit.routine_maintenance_cost_kes
            + unit.tenant_acquisition_cost_kes / max(unit.typical_lease_term_months, 6)
        ) * 1.15
        p_min = max(prior.min_plausible_rent_kes * 0.7, cost_floor, achievable_mkt * 0.55)
        p_max = min(prior.max_plausible_rent_kes * 1.2, achievable_mkt * 1.55)

        status = PricingStatus.OPTIMAL
        policy_conflict = unit.owner_policy.has_conflict()

        affordability_ceiling: float | None = None
        if unit.target_tenant_median_income_kes and unit.target_tenant_median_income_kes > 0:
            cap_ratio = min(
                prior.affordability_income_cap,
                unit.owner_policy.affordability_income_ratio_cap,
            )
            affordability_ceiling = unit.target_tenant_median_income_kes * cap_ratio
            p_max = min(p_max, affordability_ceiling)

        if policy_conflict:
            status = PricingStatus.CONFLICTING_OWNER_POLICY
            owner_floor = unit.owner_policy.min_rent_kes or p_min
            if affordability_ceiling is not None:
                p_min = min(owner_floor, affordability_ceiling)
                p_max = max(p_min, affordability_ceiling)
            else:
                p_min = owner_floor
                p_max = max(owner_floor, achievable_mkt * 1.15)
        else:
            if unit.owner_policy.min_rent_kes is not None:
                p_min = max(p_min, unit.owner_policy.min_rent_kes)
            if unit.owner_policy.max_rent_kes is not None:
                p_max = min(p_max, unit.owner_policy.max_rent_kes)
            if p_min > p_max:
                status = PricingStatus.CONFLICTING_OWNER_POLICY
                p_max = p_min

        def neg_objective(p: float) -> float:
            ev = self.demand_model.evaluate_at_price(
                p, unit, achievable_mkt, elasticity, calendar_month
            )
            comp_excess = max(0.0, (p - 1.12 * achievable_mkt) / achievable_mkt)
            comp_penalty = 0.35 * achievable_mkt * (comp_excess**2)
            occ_shortfall = max(
                0.0, unit.owner_policy.target_min_occupancy_rate - ev.finite_horizon_occupancy_prob
            )
            occ_penalty = 1.8 * achievable_mkt * (occ_shortfall**2)
            return -(ev.expected_monthly_noi_kes - comp_penalty - occ_penalty)

        unconstrained_res = minimize_scalar(
            neg_objective,
            bounds=(achievable_mkt * 0.55, achievable_mkt * 1.55),
            method="bounded",
        )
        p_unconstrained = float(unconstrained_res.x)

        if abs(p_max - p_min) < 1.0:
            p_opt = p_min
        else:
            res = minimize_scalar(neg_objective, bounds=(p_min, p_max), method="bounded")
            p_opt = float(res.x)

        if val.confidence_tier == ConfidenceTier.WEAKLY_SUPPORTED:
            p_opt = float(np.clip(0.55 * p_opt + 0.45 * achievable_mkt, p_min, p_max))

        # Vacancy Stopping Rule: do not recommend rent reduction below current asking unless justified
        vacancy_cut_justified = False
        if (
            unit.current_asking_rent_kes is not None
            and p_opt < unit.current_asking_rent_kes * 0.995
        ):
            vac_decision = self.vacancy_engine.evaluate_rent_reduction(
                unit=unit,
                candidate_new_rent_kes=p_opt,
                achievable_market_rent_kes=achievable_mkt,
                price_elasticity=elasticity,
                elasticity_std_error=val.elasticity_std_error,
                calendar_month=calendar_month,
            )
            vacancy_cut_justified = vac_decision.recommend_rent_reduction
            if not vacancy_cut_justified and unit.current_asking_rent_kes <= p_max:
                p_opt = max(p_min, unit.current_asking_rent_kes)

        shadow_price = 0.0
        if (
            unit.owner_policy.min_rent_kes is not None
            and abs(p_opt - unit.owner_policy.min_rent_kes) < 15.0
            and p_unconstrained < unit.owner_policy.min_rent_kes
        ):
            if status == PricingStatus.OPTIMAL:
                status = PricingStatus.CONSTRAINED_BY_OWNER_POLICY
            eps = 100.0
            shadow_price = (neg_objective(p_opt - eps) - neg_objective(p_opt)) / eps
        elif (
            affordability_ceiling is not None
            and abs(p_opt - affordability_ceiling) < 15.0
            and p_unconstrained > affordability_ceiling
        ):
            if status == PricingStatus.OPTIMAL:
                status = PricingStatus.AFFORDABILITY_CAPPED

        eval_opt = self.demand_model.evaluate_at_price(
            p_opt, unit, achievable_mkt, elasticity, calendar_month
        )
        unc_opt = self.uncertainty_engine.simulate_price_risk(
            p_opt,
            unit,
            achievable_mkt,
            elasticity,
            val.elasticity_std_error,
            self.demand_model,
            calendar_month,
        )

        ledger = RentalMonetaryLedger(
            asking_rent_kes=unit.current_asking_rent_kes,
            achievable_market_rent_kes=achievable_mkt,
            contractual_rent_kes=unit.contractual_rent_kes,
            recommended_rent_kes=p_opt,
            actual_collected_rent_kes=unit.actual_collected_rent_kes,
            effective_rental_revenue_kes=eval_opt.expected_effective_rental_revenue_kes,
            net_operating_income_kes=eval_opt.expected_monthly_noi_kes,
        )

        return UnitPricingRecommendation(
            unit_id=unit.unit_id,
            property_id=unit.property_id,
            segment=unit.segment,
            county=unit.county,
            neighbourhood=unit.neighbourhood,
            status=status,
            confidence_tier=val.confidence_tier,
            ledger=ledger,
            expected_application_arrival_rate=eval_opt.application_arrival_rate,
            expected_conversion_probability=eval_opt.conversion_probability,
            weibull_scale_eta_months=eval_opt.weibull_scale_eta_months,
            instantaneous_hazard_monthly=eval_opt.instantaneous_hazard_monthly,
            expected_vacancy_duration_months=eval_opt.expected_vacancy_duration_months,
            expected_occupancy_duration_months=eval_opt.expected_occupancy_duration_months,
            steady_state_occupancy_prob=eval_opt.steady_state_occupancy_prob,
            finite_horizon_occupancy_prob=eval_opt.finite_horizon_occupancy_prob,
            expected_renewal_probability=eval_opt.renewal_probability,
            price_elasticity=elasticity,
            causal_identification_status=val.causal_identification_status,
            rent_prediction_interval_80=val.prediction_interval_80,
            noi_cvar_10_kes=unc_opt.noi_cvar_10_kes,
            vacancy_cut_justified=vacancy_cut_justified,
            shadow_price_owner_floor_kes=round(shadow_price, 4),
            requires_owner_approval=True,
            effective_date_offset_months=0,
            diagnostics={
                "workflow": "open_market_single_unit_optimization",
                "unconstrained_optimal_rent_kes": round(p_unconstrained, 2),
                "feasible_bounds_kes": (round(p_min, 2), round(p_max, 2)),
                "effective_sample_size": round(val.effective_sample_size, 2),
                "first_stage_iv_f_stat": val.first_stage_f_stat,
                "spec_warnings": spec_warnings,
            },
        )


# ======================================================================================
