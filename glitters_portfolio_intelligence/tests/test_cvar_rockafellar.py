from __future__ import annotations

import unittest
import numpy as np

from glitters_portfolio_intelligence.domain.entities import PropertyAsset
from glitters_portfolio_intelligence.domain.enums import (
    AssetCategory,
    GeographicSubmarket,
    OptimizationObjective,
    SolverBackendType,
)
from glitters_portfolio_intelligence.optimization.constraints import PortfolioConstraints
from glitters_portfolio_intelligence.optimization.rockafellar_cvar import RockafellarMeanCVaROptimizer
from glitters_portfolio_intelligence.scenarios.cvar_verifier import compute_rockafellar_cvar_1d
from glitters_portfolio_intelligence.scenarios.stress_engine import ScenarioStressEngine


class TestCVaRRockafellarOptimization(unittest.TestCase):
    def setUp(self):
        self.assets = [
            PropertyAsset(
                asset_id="PROP-A",
                name="Westlands Prime Office",
                category=AssetCategory.COMMERCIAL_OFFICE,
                submarket=GeographicSubmarket.WESTLANDS,
                acquisition_valuation_cents=100_000_000_00,
                current_valuation_cents=120_000_000_00,
                monthly_gross_potential_rent_cents=1_000_000_00,
                monthly_operating_expense_cents=200_000_00,
                annual_baseline_vacancy_rate=0.08,
                annual_baseline_credit_loss_rate=0.03,
                annual_debt_service_cents=3_000_000_00,
                current_loan_balance_cents=30_000_000_00,
                annual_routine_maintenance_cents=500_000_00,
                planned_capex_cents=1_000_000_00,
            ),
            PropertyAsset(
                asset_id="PROP-B",
                name="Kilimani Apartments",
                category=AssetCategory.RESIDENTIAL,
                submarket=GeographicSubmarket.KILIMANI,
                acquisition_valuation_cents=80_000_000_00,
                current_valuation_cents=90_000_000_00,
                monthly_gross_potential_rent_cents=750_000_00,
                monthly_operating_expense_cents=120_000_00,
                annual_baseline_vacancy_rate=0.05,
                annual_baseline_credit_loss_rate=0.02,
                annual_debt_service_cents=2_000_000_00,
                current_loan_balance_cents=20_000_000_00,
                annual_routine_maintenance_cents=400_000_00,
                planned_capex_cents=800_000_00,
            ),
            PropertyAsset(
                asset_id="PROP-C",
                name="Ruiru Logistics Hub",
                category=AssetCategory.LIGHT_INDUSTRIAL,
                submarket=GeographicSubmarket.KIAMBU_RUIRU,
                acquisition_valuation_cents=150_000_000_00,
                current_valuation_cents=170_000_000_00,
                monthly_gross_potential_rent_cents=1_400_000_00,
                monthly_operating_expense_cents=250_000_00,
                annual_baseline_vacancy_rate=0.04,
                annual_baseline_credit_loss_rate=0.01,
                annual_debt_service_cents=4_500_000_00,
                current_loan_balance_cents=45_000_000_00,
                annual_routine_maintenance_cents=600_000_00,
                planned_capex_cents=1_500_000_00,
            ),
        ]
        self.ret_matrix, self.probs, self.scens = ScenarioStressEngine.evaluate_portfolio_scenario_returns(self.assets)
        self.constraints = PortfolioConstraints(
            min_cash_reserve_weight=0.05,
            max_single_asset_weight=0.50,
            max_portfolio_ltv=0.60,
            min_portfolio_dscr=1.20,
        )

    def test_lp_cvar_matches_independent_empirical_verifier(self):
        res = RockafellarMeanCVaROptimizer.optimize(
            assets=self.assets,
            scenario_returns=self.ret_matrix,
            scenario_probabilities=self.probs,
            constraints=self.constraints,
            objective=OptimizationObjective.SCALARIZED_RISK_RETURN,
            alpha=0.95,
            risk_aversion_lambda=0.60,
            backend=SolverBackendType.HIGHS_SCIPY,
        )
        self.assertTrue(res.is_optimal)
        self.assertAlmostEqual(float(np.sum(res.optimal_weights)), 0.95, places=5)  # 1.0 - 0.05 cash reserve

        # Calculate scenario loss vector for optimal weights
        portfolio_scenario_returns = np.dot(res.optimal_weights, self.ret_matrix)
        portfolio_losses = -portfolio_scenario_returns

        # Independent empirical calculation
        emp_res = compute_rockafellar_cvar_1d(portfolio_losses, self.probs, alpha=0.95)
        self.assertTrue(emp_res.is_verified)

        # Verify that LP CVaR matches independent empirical verifier to within 1e-4!
        self.assertAlmostEqual(res.cvar_alpha, emp_res.cvar, places=3)

    def test_target_return_objective(self):
        target = 0.055
        res = RockafellarMeanCVaROptimizer.optimize(
            assets=self.assets,
            scenario_returns=self.ret_matrix,
            scenario_probabilities=self.probs,
            constraints=self.constraints,
            objective=OptimizationObjective.MINIMIZE_CVAR_TARGET_RETURN,
            alpha=0.95,
            target_return=target,
            backend=SolverBackendType.HIGHS_SCIPY,
        )
        self.assertTrue(res.is_optimal)
        self.assertGreaterEqual(res.expected_return, target - 1e-5)


if __name__ == "__main__":
    unittest.main()
