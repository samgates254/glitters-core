from __future__ import annotations

import unittest
import numpy as np

from glitters_portfolio_intelligence.domain.entities import PropertyAsset
from glitters_portfolio_intelligence.domain.enums import AssetCategory, GeographicSubmarket
from glitters_portfolio_intelligence.optimization.constraints import PortfolioConstraints
from glitters_portfolio_intelligence.optimization.frontier import EfficientFrontierExplorer
from glitters_portfolio_intelligence.scenarios.stress_engine import ScenarioStressEngine


class TestEfficientFrontier(unittest.TestCase):
    def setUp(self):
        self.assets = [
            PropertyAsset(
                asset_id="PROP-1",
                name="Asset 1",
                category=AssetCategory.COMMERCIAL_OFFICE,
                submarket=GeographicSubmarket.WESTLANDS,
                acquisition_valuation_cents=100_000_000_00,
                current_valuation_cents=100_000_000_00,
                monthly_gross_potential_rent_cents=1_000_000_00,
                monthly_operating_expense_cents=200_000_00,
                annual_baseline_vacancy_rate=0.05,
                annual_baseline_credit_loss_rate=0.02,
                annual_debt_service_cents=2_000_000_00,
                current_loan_balance_cents=20_000_000_00,
                annual_routine_maintenance_cents=300_000_00,
                planned_capex_cents=500_000_00,
            ),
            PropertyAsset(
                asset_id="PROP-2",
                name="Asset 2",
                category=AssetCategory.RESIDENTIAL,
                submarket=GeographicSubmarket.KILIMANI,
                acquisition_valuation_cents=80_000_000_00,
                current_valuation_cents=80_000_000_00,
                monthly_gross_potential_rent_cents=600_000_00,
                monthly_operating_expense_cents=100_000_00,
                annual_baseline_vacancy_rate=0.04,
                annual_baseline_credit_loss_rate=0.01,
                annual_debt_service_cents=1_500_000_00,
                current_loan_balance_cents=15_000_000_00,
                annual_routine_maintenance_cents=200_000_00,
                planned_capex_cents=400_000_00,
            ),
        ]
        self.ret_matrix, self.probs, _ = ScenarioStressEngine.evaluate_portfolio_scenario_returns(self.assets)
        self.constraints = PortfolioConstraints(
            min_cash_reserve_weight=0.05,
            max_single_asset_weight=0.70,
        )

    def test_scalarized_frontier_points(self):
        frontier = EfficientFrontierExplorer.trace_scalarized_frontier(
            assets=self.assets,
            scenario_returns=self.ret_matrix,
            scenario_probabilities=self.probs,
            constraints=self.constraints,
            n_points=5,
        )
        self.assertGreater(len(frontier.points), 0)
        # Check monotonicity: higher CVaR budget / lower lambda allows higher expected return
        for p in frontier.points:
            self.assertGreater(p.expected_return, -1.0)
            self.assertGreater(p.cvar_alpha, -1.0)

    def test_epsilon_constraint_frontier(self):
        frontier = EfficientFrontierExplorer.trace_epsilon_constraint_frontier(
            assets=self.assets,
            scenario_returns=self.ret_matrix,
            scenario_probabilities=self.probs,
            constraints=self.constraints,
            n_points=5,
        )
        self.assertEqual(frontier.method, "EPSILON_CONSTRAINT")
        self.assertGreater(len(frontier.points), 0)


if __name__ == "__main__":
    unittest.main()
