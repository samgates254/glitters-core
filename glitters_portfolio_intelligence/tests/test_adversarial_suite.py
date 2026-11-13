from __future__ import annotations

import unittest
import numpy as np

from glitters_portfolio_intelligence.domain.entities import PropertyAsset, Portfolio
from glitters_portfolio_intelligence.domain.enums import (
    AssetCategory,
    GeographicSubmarket,
    OptimizationObjective,
    OptimizationStatus,
    SolverBackendType,
)
from glitters_portfolio_intelligence.optimization.constraints import PortfolioConstraints
from glitters_portfolio_intelligence.optimization.rockafellar_cvar import RockafellarMeanCVaROptimizer


class TestAdversarialSuite(unittest.TestCase):
    def test_negative_valuation_rejected(self):
        with self.assertRaises(ValueError):
            PropertyAsset(
                asset_id="BAD-01",
                name="Negative Valuation Asset",
                category=AssetCategory.COMMERCIAL_OFFICE,
                submarket=GeographicSubmarket.WESTLANDS,
                acquisition_valuation_cents=-100,
                current_valuation_cents=100,
                monthly_gross_potential_rent_cents=100,
                monthly_operating_expense_cents=10,
                annual_baseline_vacancy_rate=0.05,
                annual_baseline_credit_loss_rate=0.02,
                annual_debt_service_cents=0,
                current_loan_balance_cents=0,
                annual_routine_maintenance_cents=0,
                planned_capex_cents=0,
            )

    def test_invalid_vacancy_rates_rejected(self):
        with self.assertRaises(ValueError):
            PropertyAsset(
                asset_id="BAD-02",
                name="Invalid Vacancy Asset",
                category=AssetCategory.RESIDENTIAL,
                submarket=GeographicSubmarket.KILIMANI,
                acquisition_valuation_cents=1000,
                current_valuation_cents=1000,
                monthly_gross_potential_rent_cents=100,
                monthly_operating_expense_cents=10,
                annual_baseline_vacancy_rate=1.50,  # > 1.0 invalid
                annual_baseline_credit_loss_rate=0.02,
                annual_debt_service_cents=0,
                current_loan_balance_cents=0,
                annual_routine_maintenance_cents=0,
                planned_capex_cents=0,
            )

    def test_catastrophic_stress_optimization(self):
        # All scenarios have 100% loss (-1.00 return)
        asset = PropertyAsset(
            asset_id="PROP-01",
            name="Distressed Asset",
            category=AssetCategory.RETAIL,
            submarket=GeographicSubmarket.NAIROBI_CBD,
            acquisition_valuation_cents=10_000_000_00,
            current_valuation_cents=10_000_000_00,
            monthly_gross_potential_rent_cents=100_000_00,
            monthly_operating_expense_cents=20_000_00,
            annual_baseline_vacancy_rate=0.10,
            annual_baseline_credit_loss_rate=0.05,
            annual_debt_service_cents=500_000_00,
            current_loan_balance_cents=2_000_000_00,
            annual_routine_maintenance_cents=50_000_00,
            planned_capex_cents=0,
        )
        catastrophic_returns = np.array([[-1.0, -1.0, -1.0]])
        probs = np.array([0.33, 0.33, 0.34])
        constraints = PortfolioConstraints(
            min_cash_reserve_weight=0.05,
            max_single_asset_weight=1.0,
        )

        res = RockafellarMeanCVaROptimizer.optimize(
            assets=[asset],
            scenario_returns=catastrophic_returns,
            scenario_probabilities=probs,
            constraints=constraints,
            objective=OptimizationObjective.SCALARIZED_RISK_RETURN,
            alpha=0.95,
            risk_aversion_lambda=0.50,
            backend=SolverBackendType.HIGHS_SCIPY,
        )
        self.assertTrue(res.is_optimal)
        # CVaR under 100% loss should equal 1.0 (or -0.95 expected return)
        self.assertAlmostEqual(res.expected_return, -0.95, places=3)
        self.assertAlmostEqual(res.cvar_alpha, 0.95, places=3)


if __name__ == "__main__":
    unittest.main()
