from __future__ import annotations
"""Uncertainty Quantification, 500-Path Monte Carlo Scenario Analysis & Robust CVaR Pricing.

Phase 1 Audit Remediation (v2.0):
- Standardized default simulation path count to `n_simulations = 500` to match the formal
  mathematical specification (`M >= 500`).
- Uses dimensionally consistent Weibull scale parameter `weibull_scale_eta_months` and
  monthly transition frequencies.
"""


from dataclasses import dataclass
import numpy as np

from ..demand.models import RentalDemandModel
from ..schemas import UnitSpecification


@dataclass(frozen=True)
class UncertaintySimulationResult:
    """Monte Carlo risk profile for a unit at a recommended rental price."""

    n_simulations: int
    mean_monthly_noi_kes: float
    std_monthly_noi_kes: float
    noi_p10_kes: float
    noi_p50_kes: float
    noi_p90_kes: float
    noi_cvar_10_kes: float
    vacancy_risk_prob_over_3m: float
    downside_scenario_noi_kes: float
    upside_scenario_noi_kes: float


class UncertaintyRiskEngine:
    """Executes vectorized 500-path Monte Carlo simulation for demand, vacancy, and NOI risk."""

    def __init__(self, n_simulations: int = 500, random_seed: int = 42) -> None:
        self.n_simulations = max(100, int(n_simulations))
        self.random_seed = random_seed

    def simulate_price_risk(
        self,
        price_kes: float,
        unit: UnitSpecification,
        achievable_market_rent_kes: float,
        price_elasticity: float,
        elasticity_std_error: float,
        demand_model: RentalDemandModel,
        calendar_month: int = 11,
    ) -> UncertaintySimulationResult:
        """Runs vectorized Monte Carlo draws over market rent, elasticity, and Weibull survival noise."""
        rng = np.random.default_rng(self.random_seed + abs(hash(unit.unit_id)) % 10000)

        mkt_draws = achievable_market_rent_kes * np.exp(
            rng.normal(0.0, 0.08, size=self.n_simulations)
        )
        elast_draws = np.clip(
            rng.normal(price_elasticity, elasticity_std_error, size=self.n_simulations),
            -4.5,
            -0.35,
        )

        base_eval = demand_model.evaluate_at_price(
            price_kes, unit, achievable_market_rent_kes, price_elasticity, calendar_month
        )

        log_rel_draws = np.log(price_kes / np.maximum(mkt_draws, 1.0))
        base_log_rel = np.log(price_kes / max(achievable_market_rent_kes, 1.0))
        rate_mult = np.exp(
            np.clip(
                elast_draws * log_rel_draws - price_elasticity * base_log_rel,
                -4.0,
                4.0,
            )
        )
        # Dimensionally consistent Weibull scale draws eta_sim [months] = eta_base / rate_mult
        sim_eta_months = np.clip(
            base_eval.weibull_scale_eta_months / np.maximum(rate_mult, 0.02),
            0.05,
            60.0,
        )
        sim_vac_months = sim_eta_months * rng.weibull(
            base_eval.weibull_shape_nu, size=self.n_simulations
        )

        H = float(demand_model.planning_horizon_months)
        occ_months = np.maximum(0.0, H - np.minimum(H, sim_vac_months))
        occ_frac = np.clip(
            0.5 * (occ_months / H) + 0.5 * base_eval.finite_horizon_occupancy_prob,
            0.01,
            0.995,
        )

        arrears_draws = np.clip(
            rng.normal(base_eval.expected_arrears_rate, 0.02, size=self.n_simulations),
            0.0,
            0.55,
        )

        eff_rev = occ_frac * price_kes * (1.0 - arrears_draws)
        mgmt_fee = eff_rev * (unit.management_fee_pct + unit.statutory_tax_rate_pct)
        vac_hold = (1.0 - occ_frac) * unit.vacancy_holding_cost_kes
        transition_costs = (
            base_eval.monthly_acquisition_frequency * unit.tenant_acquisition_cost_kes
            + base_eval.monthly_turnover_frequency * unit.turnover_refurbishment_cost_kes
        )

        sim_noi = (
            eff_rev - mgmt_fee - unit.routine_maintenance_cost_kes - vac_hold - transition_costs
        )

        p10 = float(np.percentile(sim_noi, 10))
        p50 = float(np.percentile(sim_noi, 50))
        p90 = float(np.percentile(sim_noi, 90))
        tail_mask = sim_noi <= p10
        cvar_10 = float(np.mean(sim_noi[tail_mask])) if np.any(tail_mask) else p10
        vac_over_3m = float(np.mean(sim_vac_months > 3.0))

        return UncertaintySimulationResult(
            n_simulations=self.n_simulations,
            mean_monthly_noi_kes=float(np.mean(sim_noi)),
            std_monthly_noi_kes=float(np.std(sim_noi)),
            noi_p10_kes=p10,
            noi_p50_kes=p50,
            noi_p90_kes=p90,
            noi_cvar_10_kes=cvar_10,
            vacancy_risk_prob_over_3m=vac_over_3m,
            downside_scenario_noi_kes=p10,
            upside_scenario_noi_kes=p90,
        )


# ======================================================================================
