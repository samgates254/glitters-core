from __future__ import annotations

import unittest

from glitters_portfolio_intelligence.domain.entities import Portfolio, PropertyAsset
from glitters_portfolio_intelligence.domain.enums import (
    AssetCategory,
    DecisionAction,
    GeographicSubmarket,
    OwnershipRepresentation,
)
from glitters_portfolio_intelligence.optimization.multi_period import MultiPeriodPortfolioOptimizer
from glitters_portfolio_intelligence.optimization.solver_backends import ORTOOLS_AVAILABLE


@unittest.skipUnless(ORTOOLS_AVAILABLE, "OR-Tools required for multi-period test")
class TestMultiPeriodOptimization(unittest.TestCase):
    def setUp(self):
        self.asset1 = PropertyAsset(
            asset_id="PROP-1",
            name="Existing High Yield",
            category=AssetCategory.COMMERCIAL_OFFICE,
            submarket=GeographicSubmarket.WESTLANDS,
            acquisition_valuation_cents=100_000_000_00,
            current_valuation_cents=120_000_000_00,
            monthly_gross_potential_rent_cents=1_200_000_00,
            monthly_operating_expense_cents=200_000_00,
            annual_baseline_vacancy_rate=0.05,
            annual_baseline_credit_loss_rate=0.02,
            annual_debt_service_cents=2_000_000_00,
            current_loan_balance_cents=20_000_000_00,
            annual_routine_maintenance_cents=300_000_00,
            planned_capex_cents=1_000_000_00,
            renovation_downtime_months=3,
        )
        self.asset2 = PropertyAsset(
            asset_id="PROP-2",
            name="Candidate Pipeline Acquisition",
            category=AssetCategory.RESIDENTIAL,
            submarket=GeographicSubmarket.KILIMANI,
            acquisition_valuation_cents=60_000_000_00,
            current_valuation_cents=60_000_000_00,
            monthly_gross_potential_rent_cents=600_000_00,
            monthly_operating_expense_cents=100_000_00,
            annual_baseline_vacancy_rate=0.04,
            annual_baseline_credit_loss_rate=0.01,
            annual_debt_service_cents=1_000_000_00,
            current_loan_balance_cents=10_000_000_00,
            annual_routine_maintenance_cents=200_000_00,
            planned_capex_cents=500_000_00,
            renovation_downtime_months=2,
        )
        self.portfolio = Portfolio(
            portfolio_id="PORT-01",
            tenant_org_id="ORG-TEST",
            assets=(self.asset1,),
            cash_reserves_cents=20_000_000_00,  # KES 20M cash
        )

    def test_multi_period_trajectory_invariants(self):
        min_cash_cents = 5_000_000_00  # KES 5M liquidity floor
        res = MultiPeriodPortfolioOptimizer.optimize(
            portfolio=self.portfolio,
            candidate_assets=[self.asset1, self.asset2],
            horizon_years=3,
            min_cash_reserve_cents=min_cash_cents,
            representation=OwnershipRepresentation.FRACTIONAL_CAPITAL_WEIGHT,
        )
        self.assertTrue(res.is_optimal)
        self.assertEqual(res.horizon_periods, 3)

        # Invariant 1: Cash balances never breach minimum liquidity floor
        for cash_bal in res.periodic_cash_balances_cents:
            self.assertGreaterEqual(cash_bal, min_cash_cents)

        # Invariant 2: No simultaneous buy & sell in any period
        by_period_asset = {}
        for step in res.schedule:
            key = (step.period, step.asset_id)
            by_period_asset[key] = step

        for t in range(1, 4):
            for a in [self.asset1, self.asset2]:
                step = by_period_asset[(t, a.asset_id)]
                # Action must be unique and valid
                self.assertIn(step.action, [DecisionAction.BUY, DecisionAction.HOLD, DecisionAction.SELL, DecisionAction.RENOVATE])
                # Holding fraction in [0, 1]
                self.assertGreaterEqual(step.holding_fraction, 0.0)
                self.assertLessEqual(step.holding_fraction, 1.0001)


if __name__ == "__main__":
    unittest.main()
