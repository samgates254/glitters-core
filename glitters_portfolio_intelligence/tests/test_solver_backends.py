from __future__ import annotations

import unittest
import numpy as np

from glitters_portfolio_intelligence.domain.entities import PropertyAsset
from glitters_portfolio_intelligence.domain.enums import (
    AssetCategory,
    GeographicSubmarket,
    OptimizationObjective,
    OptimizationStatus,
    SolverBackendType,
)
from glitters_portfolio_intelligence.optimization.constraints import PortfolioConstraints
from glitters_portfolio_intelligence.optimization.rockafellar_cvar import RockafellarMeanCVaROptimizer
from glitters_portfolio_intelligence.optimization.solver_backends import (
    ORTOOLS_AVAILABLE,
    PortfolioSolverBackend,
)
from glitters_portfolio_intelligence.scenarios.stress_engine import ScenarioStressEngine


class TestSolverBackends(unittest.TestCase):
    def setUp(self):
        self.assets = [
            PropertyAsset(
                asset_id="PROP-1",
                name="Westlands Asset",
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
                asset_id="PROP-2",
                name="Kilimani Asset",
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
        ]
        self.ret_matrix, self.probs, _ = ScenarioStressEngine.evaluate_portfolio_scenario_returns(self.assets)
        self.constraints = PortfolioConstraints(
            min_cash_reserve_weight=0.05,
            max_single_asset_weight=0.60,
        )

    def test_multi_solver_identical_solution_comparison(self):
        available = PortfolioSolverBackend.get_available_backends()
        self.assertIn(SolverBackendType.HIGHS_SCIPY, available)

        results = {}
        for b in available:
            res = RockafellarMeanCVaROptimizer.optimize(
                assets=self.assets,
                scenario_returns=self.ret_matrix,
                scenario_probabilities=self.probs,
                constraints=self.constraints,
                objective=OptimizationObjective.SCALARIZED_RISK_RETURN,
                alpha=0.95,
                risk_aversion_lambda=0.50,
                backend=b,
            )
            self.assertTrue(res.is_optimal, f"Solver {b.value} failed to find optimal solution")
            results[b] = res

        # Compare objective values and weights across all available solvers
        primary_res = results[SolverBackendType.HIGHS_SCIPY]
        for b, res in results.items():
            self.assertAlmostEqual(res.expected_return, primary_res.expected_return, places=3)
            self.assertAlmostEqual(res.cvar_alpha, primary_res.cvar_alpha, places=3)
            for w1, w2 in zip(res.optimal_weights, primary_res.optimal_weights):
                self.assertAlmostEqual(w1, w2, places=3)

    def test_infeasible_problem_detection(self):
        # Set impossible target return (e.g. 500% annual return)
        impossible_constraints = PortfolioConstraints(
            min_cash_reserve_weight=0.05,
            max_single_asset_weight=0.60,
        )
        res = RockafellarMeanCVaROptimizer.optimize(
            assets=self.assets,
            scenario_returns=self.ret_matrix,
            scenario_probabilities=self.probs,
            constraints=impossible_constraints,
            objective=OptimizationObjective.MINIMIZE_CVAR_TARGET_RETURN,
            target_return=5.00,  # 500% target return is infeasible
            backend=SolverBackendType.HIGHS_SCIPY,
        )
        self.assertEqual(res.status, OptimizationStatus.INFEASIBLE)
        self.assertFalse(res.is_optimal)


if __name__ == "__main__":
    unittest.main()
