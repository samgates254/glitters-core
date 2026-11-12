from __future__ import annotations
"""Rental Demand, Dimensionally Consistent Weibull Survival & DTMC Occupancy Engine.

Phase 1 Audit Remediation (v2.0 — CRITICAL 1 & HIGH 4 Fixes):
1. CRITICAL 1 Fix (Dimensional Consistency of Weibull Survival Hazard):
   - Let r_i(P) = lambda_i(P) * q_i(P) be the thinned Poisson clearing rate in [month^-1].
   - Defines characteristic Weibull scale parameter eta_i(P) = 1.0 / r_i(P) in [months].
   - Cumulative hazard: H_i(P, tau) = (tau / eta_i(P))**nu_s [dimensionless].
   - Instantaneous hazard: h_i(P, tau) = (nu_s / eta_i(P)) * (tau / eta_i(P))**(nu_s - 1) [month^-1].
   - Expected vacancy duration: E[V_i(P)] = eta_i(P) * Gamma(1 + 1/nu_s) [months].
2. HIGH 4 Fix (Full Finite-Horizon Discrete-Time Markov Chain State Transitions):
   - Models the 4-state monthly transition matrix across S0=VACANT, S1=OCCUPIED,
     S2=EXPIRING, S3=TURNOVER_MAINT over planning horizon H (e.g., 12 months),
     explicitly accounting for initial lease remaining duration, mid-horizon lease expiry,
     renewal probability, early default/termination hazard, and refurbishment turnover cycles.
"""


from dataclasses import dataclass
import math
import numpy as np

from ..kenyan_market import KENYAN_MONTHLY_SEASONALITY, KENYAN_SEGMENT_PRIORS
from ..schemas import LeaseStatus, UnitSpecification


@dataclass(frozen=True)
class DemandCurveEvaluation:
    """Complete evaluation of structural demand, Weibull survival, DTMC occupancy & monthly NOI."""

    rental_price_kes: float
    demand_intensity: float
    application_arrival_rate: float  # [arrivals / month]
    conversion_probability: float  # [dimensionless in (0,1)]
    thinned_clearing_rate_monthly: float  # r_i(P) = lambda * q [month^-1]
    weibull_scale_eta_months: float  # eta_i(P) = 1 / r_i(P) [months]
    weibull_shape_nu: float  # nu_s [dimensionless]
    instantaneous_hazard_monthly: float  # h_i(P, tau) = (nu/eta)*(tau/eta)**(nu-1) [month^-1]
    expected_vacancy_duration_months: float  # E[V_i(P)] = eta * Gamma(1 + 1/nu) [months]
    expected_occupancy_duration_months: float  # E[L_i(P)] [months]
    steady_state_occupancy_prob: float
    steady_state_vacancy_prob: float
    finite_horizon_occupancy_prob: float  # Computed via 4-state DTMC over horizon H
    monthly_acquisition_frequency: float  # Expected new-lease transitions per month over H
    monthly_turnover_frequency: float  # Expected departure/turnover transitions per month over H
    renewal_probability: float
    expected_arrears_rate: float
    expected_gross_revenue_kes: float
    expected_effective_rental_revenue_kes: float
    expected_monthly_noi_kes: float


class RentalDemandModel:
    """Combines NHPP inquiry arrivals, RUM logit conversion, dimensionally consistent Weibull survival, and 4-state DTMC."""

    def __init__(self, planning_horizon_months: int = 12) -> None:
        self.planning_horizon_months = max(1, int(planning_horizon_months))

    def _compute_dtmc_finite_horizon_metrics(
        self,
        unit: UnitSpecification,
        weibull_scale_eta_months: float,
        weibull_shape_nu: float,
        renewal_prob: float,
        early_churn_monthly_prob: float,
    ) -> tuple[float, float, float]:
        """Simulates the 4-state monthly Discrete-Time Markov Chain over planning_horizon_months."""
        H = self.planning_horizon_months
        tau_curr_months = max(0.25, unit.current_vacancy_days / 30.0)

        eta = max(weibull_scale_eta_months, 0.02)
        h_delta = ((tau_curr_months + 1.0) / eta) ** weibull_shape_nu - (
            tau_curr_months / eta
        ) ** weibull_shape_nu
        p_lease = float(np.clip(1.0 - math.exp(-max(h_delta, 1e-6)), 0.002, 0.98))

        lease_term = float(max(unit.typical_lease_term_months, 6))
        gamma_exp = 1.0 / max(lease_term - 1.0, 1.0)
        p_churn = float(np.clip(early_churn_monthly_prob, 0.001, 0.15))
        if p_churn + gamma_exp > 0.95:
            gamma_exp = max(0.05, 0.95 - p_churn)

        p_ready = 0.85

        T = np.array(
            [
                [1.0 - p_lease, p_lease, 0.0, 0.0],
                [0.0, 1.0 - p_churn - gamma_exp, gamma_exp, p_churn],
                [0.0, renewal_prob, 0.0, 1.0 - renewal_prob],
                [p_ready, 0.0, 0.0, 1.0 - p_ready],
            ],
            dtype=float,
        )

        pi = np.zeros(4, dtype=float)
        if unit.lease_status == LeaseStatus.VACANT:
            pi[0] = 1.0
        elif unit.lease_status == LeaseStatus.UNDER_MAINTENANCE:
            pi[3] = 1.0
        elif unit.lease_status == LeaseStatus.EXPIRING_RENEWAL_WINDOW or (
            unit.lease_status == LeaseStatus.OCCUPIED_ACTIVE
            and 0 < unit.months_remaining_on_lease <= 2
        ):
            pi[2] = 1.0
        else:
            pi[1] = 1.0

        occ_accum = 0.0
        acq_accum = 0.0
        turn_accum = 0.0

        for step in range(1, H + 1):
            if (
                unit.lease_status == LeaseStatus.OCCUPIED_ACTIVE
                and 0 < unit.months_remaining_on_lease <= H
                and step == unit.months_remaining_on_lease
            ):
                mass_occ = pi[1]
                pi[1] = 0.0
                pi[2] += mass_occ

            acq_step = pi[0] * p_lease
            turn_step = pi[1] * p_churn + pi[2] * (1.0 - renewal_prob)

            pi = pi @ T
            occ_accum += float(pi[1] + pi[2])
            acq_accum += float(acq_step)
            turn_accum += float(turn_step)

        finite_occ = float(np.clip(occ_accum / H, 0.01, 0.995))
        monthly_acq_freq = float(acq_accum / H)
        monthly_turn_freq = float(turn_accum / H)
        return finite_occ, monthly_acq_freq, monthly_turn_freq

    def evaluate_at_price(
        self,
        price_kes: float,
        unit: UnitSpecification,
        achievable_market_rent_kes: float,
        price_elasticity: float,
        calendar_month: int = 11,
        competing_prices_kes: list[float] | None = None,
    ) -> DemandCurveEvaluation:
        """Evaluates all demand, dimensionally consistent Weibull survival, DTMC occupancy & monthly NOI quantities."""
        if price_kes <= 0:
            raise ValueError("Candidate rental price must be strictly positive.")

        prior = KENYAN_SEGMENT_PRIORS[unit.segment]
        season_mult = KENYAN_MONTHLY_SEASONALITY.get(unit.segment, {}).get(calendar_month, 1.0)

        log_rel_price = math.log(price_kes / max(achievable_market_rent_kes, 1.0))
        pos_log_rel = max(0.0, log_rel_price)

        borehole_adj = (
            0.08
            if unit.has_borehole_water is True
            else (-0.06 if unit.has_borehole_water is False else 0.0)
        )
        gen_adj = 0.07 if unit.has_backup_generator is True else 0.0
        quality_boost = 1.0 + borehole_adj + gen_adj + 0.04 * (unit.security_tier - 3)

        # 1. Application Arrival Rate \lambda_i(P, t) [arrivals / month]
        exponent = price_elasticity * log_rel_price - 0.5 * prior.nonlinear_cliff_gamma * (
            pos_log_rel**2
        )
        exponent = float(np.clip(exponent, -12.0, 3.0))
        arrival_rate = (
            prior.base_monthly_arrival_rate * quality_boost * season_mult * math.exp(exponent)
        )
        arrival_rate = float(np.clip(arrival_rate, 0.005, 45.0))

        # 2. Random Utility Multinomial Logit (MNL) Conversion Probability q_i(P) [dimensionless]
        rel_gap = (price_kes - achievable_market_rent_kes) / max(achievable_market_rent_kes, 1.0)
        affordability_penalty = 0.0
        if unit.target_tenant_median_income_kes and unit.target_tenant_median_income_kes > 0:
            rent_to_income = price_kes / unit.target_tenant_median_income_kes
            cap = min(
                prior.affordability_income_cap,
                unit.owner_policy.affordability_income_ratio_cap,
            )
            if rent_to_income > cap:
                affordability_penalty = 6.5 * (rent_to_income - cap)

        utility_unit = -2.2 * rel_gap - affordability_penalty + 0.15 * (unit.security_tier - 3)
        utility_unit = float(np.clip(utility_unit, -12.0, 5.0))

        if competing_prices_kes:
            comp_exp_sum = sum(
                math.exp(
                    float(
                        np.clip(
                            -2.2
                            * (cp - achievable_market_rent_kes)
                            / max(achievable_market_rent_kes, 1.0),
                            -8.0,
                            4.0,
                        )
                    )
                )
                for cp in competing_prices_kes
            ) / max(len(competing_prices_kes), 1)
        else:
            comp_exp_sum = 1.0

        base_logit_offset = math.log(
            prior.base_conversion_prob / max(1.0 - prior.base_conversion_prob, 1e-4)
        )
        exp_u = math.exp(base_logit_offset + utility_unit)
        conversion_prob = float(np.clip(exp_u / (1.0 + comp_exp_sum + exp_u), 0.002, 0.94))

        # 3. CRITICAL 1 Fix: Dimensionally Consistent Weibull Scale eta_i(P) [months] & Hazard h_i(P, tau) [month^-1]
        stigma_factor = math.exp(-0.0025 * max(0, unit.current_vacancy_days - 45))
        thinned_clearing_rate = max(
            1e-6, arrival_rate * conversion_prob * stigma_factor
        )  # [month^-1]
        weibull_scale_eta = 1.0 / thinned_clearing_rate  # [months]
        nu = prior.weibull_duration_shape  # [dimensionless]

        tau_eval_months = max(0.5, unit.current_vacancy_days / 30.0)
        instantaneous_hazard = (nu / weibull_scale_eta) * (
            (tau_eval_months / weibull_scale_eta) ** (nu - 1.0)
        )  # [month^-1]

        gamma_factor = math.gamma(1.0 + 1.0 / nu)
        # Exact theoretical expected Weibull vacancy duration E[V_i(P)] = eta_i(P) * Gamma(1 + 1/nu_s) [months]
        expected_vacancy_months = float(weibull_scale_eta * gamma_factor)

        # 4. Renewal Probability & Expected Occupancy Duration
        renewal_logit = 1.15 - 2.6 * max(0.0, rel_gap) - 0.8 * min(0.0, rel_gap)
        renewal_prob = float(np.clip(1.0 / (1.0 + math.exp(-renewal_logit)), 0.15, 0.94))

        arrears_stress = max(0.0, rel_gap) * 0.14 + (0.05 if affordability_penalty > 0 else 0.0)
        expected_arrears = float(np.clip(unit.baseline_arrears_rate + arrears_stress, 0.005, 0.45))

        lease_term = float(max(unit.typical_lease_term_months, prior.typical_lease_months))
        early_churn_term_prob = float(np.clip(0.03 + 0.12 * max(0.0, rel_gap), 0.01, 0.35))
        early_churn_monthly_prob = 1.0 - (
            (1.0 - early_churn_term_prob) ** (1.0 / max(lease_term, 1.0))
        )

        expected_occupancy_months = float(
            (lease_term * (1.0 - early_churn_term_prob)) / max(1.0 - renewal_prob, 0.08)
        )

        # 5. Steady-State and 4-State DTMC Finite-Horizon Occupancy
        steady_occ = expected_occupancy_months / (
            expected_occupancy_months + expected_vacancy_months
        )
        steady_vac = 1.0 - steady_occ

        finite_occ, monthly_acq_freq, monthly_turn_freq = (
            self._compute_dtmc_finite_horizon_metrics(
                unit=unit,
                weibull_scale_eta_months=weibull_scale_eta,
                weibull_shape_nu=nu,
                renewal_prob=renewal_prob,
                early_churn_monthly_prob=early_churn_monthly_prob,
            )
        )

        # 6. Strictly Monthly Normalized ERR and NOI (KES / month)
        gross_rev = price_kes
        effective_rental_revenue = finite_occ * price_kes * (1.0 - expected_arrears)

        mgmt_and_tax = effective_rental_revenue * (
            unit.management_fee_pct + unit.statutory_tax_rate_pct
        )
        vacancy_holding = (1.0 - finite_occ) * unit.vacancy_holding_cost_kes
        expected_monthly_acq_cost = monthly_acq_freq * unit.tenant_acquisition_cost_kes
        expected_monthly_turn_cost = monthly_turn_freq * unit.turnover_refurbishment_cost_kes

        expected_noi = (
            effective_rental_revenue
            - mgmt_and_tax
            - unit.routine_maintenance_cost_kes
            - vacancy_holding
            - expected_monthly_acq_cost
            - expected_monthly_turn_cost
        )

        return DemandCurveEvaluation(
            rental_price_kes=price_kes,
            demand_intensity=arrival_rate * (1.0 + conversion_prob),
            application_arrival_rate=arrival_rate,
            conversion_probability=conversion_prob,
            thinned_clearing_rate_monthly=thinned_clearing_rate,
            weibull_scale_eta_months=weibull_scale_eta,
            weibull_shape_nu=nu,
            instantaneous_hazard_monthly=instantaneous_hazard,
            expected_vacancy_duration_months=expected_vacancy_months,
            expected_occupancy_duration_months=expected_occupancy_months,
            steady_state_occupancy_prob=steady_occ,
            steady_state_vacancy_prob=steady_vac,
            finite_horizon_occupancy_prob=finite_occ,
            monthly_acquisition_frequency=monthly_acq_freq,
            monthly_turnover_frequency=monthly_turn_freq,
            renewal_probability=renewal_prob,
            expected_arrears_rate=expected_arrears,
            expected_gross_revenue_kes=gross_rev,
            expected_effective_rental_revenue_kes=effective_rental_revenue,
            expected_monthly_noi_kes=expected_noi,
        )


# ======================================================================================
