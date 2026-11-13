from __future__ import annotations

import unittest
from decimal import Decimal

from glitters_portfolio_intelligence.domain.money import Money, allocate_cents
from glitters_portfolio_intelligence.domain.entities import PropertyAsset, Portfolio
from glitters_portfolio_intelligence.domain.enums import AssetCategory, GeographicSubmarket
from glitters_portfolio_intelligence.cashflows.accounting import (
    AnnualCashFlowStatement,
    compute_asset_annual_cashflow,
    compute_portfolio_annual_cashflow,
)


class TestMoneyAndCashFlows(unittest.TestCase):
    def test_money_arithmetic_exactness(self):
        m1 = Money(cents=100_000_50)  # KES 100,000.50
        m2 = Money(cents=50_000_25)   # KES 50,000.25

        m_sum = m1 + m2
        self.assertEqual(m_sum.cents, 150_000_75)
        self.assertEqual(m_sum.major_amount, Decimal("150000.75"))

        m_diff = m1 - m2
        self.assertEqual(m_diff.cents, 50_000_25)

        m_scaled = m1 * 2
        self.assertEqual(m_scaled.cents, 200_001_00)

        # Rejects non-integer cents
        with self.assertRaises(TypeError):
            Money(cents=100.5)  # type: ignore

    def test_allocate_cents_hare_niemeyer_conservation(self):
        total_cents = 100_000_00  # KES 100,000.00
        weights = [0.333333, 0.333333, 0.333334]
        alloc = allocate_cents(total_cents, weights)

        self.assertEqual(len(alloc), 3)
        self.assertEqual(sum(alloc), total_cents)
        # All allocations are exact integers
        for a in alloc:
            self.assertIsInstance(a, int)

        # Test edge case: unequal prime weights
        alloc2 = allocate_cents(1001, [0.17, 0.43, 0.40])
        self.assertEqual(sum(alloc2), 1001)

    def test_cashflow_statement_multiplicative_non_double_counting(self):
        asset = PropertyAsset(
            asset_id="PROP-001",
            name="Westlands Commercial Tower",
            category=AssetCategory.COMMERCIAL_OFFICE,
            submarket=GeographicSubmarket.WESTLANDS,
            acquisition_valuation_cents=500_000_000_00,
            current_valuation_cents=600_000_000_00,
            monthly_gross_potential_rent_cents=4_000_000_00,  # KES 4M/month
            monthly_operating_expense_cents=800_000_00,       # KES 800k/month
            annual_baseline_vacancy_rate=0.10,                # 10% vacancy
            annual_baseline_credit_loss_rate=0.05,            # 5% credit loss
            annual_debt_service_cents=15_000_000_00,          # KES 15M/yr
            current_loan_balance_cents=200_000_000_00,
            annual_routine_maintenance_cents=2_000_000_00,
            planned_capex_cents=5_000_000_00,
        )

        stmt = compute_asset_annual_cashflow(asset, other_income_cents=500_000_00)

        gpr = 48_000_000_00
        self.assertEqual(stmt.gross_potential_rent_cents, gpr)
        # Vacancy: 10% of 48M = 4.8M
        self.assertEqual(stmt.vacancy_loss_cents, 4_800_000_00)
        # Billed: 48M - 4.8M = 43.2M
        # Credit loss: 5% of 43.2M = 2.16M (evaluated strictly on occupied units!)
        self.assertEqual(stmt.credit_loss_cents, 2_160_000_00)
        # EGI: 48M - 4.8M - 2.16M + 0.5M = 41.54M
        self.assertEqual(stmt.effective_gross_income_cents, 41_540_000_00)

        # NOI: 41.54M - 9.6M (OPEX) - 2M (maint) = 29.94M
        self.assertEqual(stmt.net_operating_income_cents, 29_940_000_00)

        # CFADS: 29.94M - 15M (debt) - 5M (CapEx) = 9.94M
        self.assertEqual(stmt.cash_flow_after_debt_service_cents, 9_940_000_00)

        # DSCR: 29.94M / 15M = 1.996
        self.assertAlmostEqual(stmt.dscr, 29_940_000_00 / 15_000_000_00, places=4)
        self.assertFalse(stmt.is_unlevered)

    def test_dscr_unlevered_zero_debt_service(self):
        # Asset with 0 debt service
        asset = PropertyAsset(
            asset_id="PROP-UNLEVERED",
            name="Karen Residential Villa",
            category=AssetCategory.RESIDENTIAL,
            submarket=GeographicSubmarket.KAREN,
            acquisition_valuation_cents=80_000_000_00,
            current_valuation_cents=95_000_000_00,
            monthly_gross_potential_rent_cents=500_000_00,
            monthly_operating_expense_cents=80_000_00,
            annual_baseline_vacancy_rate=0.05,
            annual_baseline_credit_loss_rate=0.02,
            annual_debt_service_cents=0,  # UNLEVERED
            current_loan_balance_cents=0,
            annual_routine_maintenance_cents=300_000_00,
            planned_capex_cents=0,
        )
        stmt = compute_asset_annual_cashflow(asset)
        self.assertIsNone(stmt.dscr)
        self.assertTrue(stmt.is_unlevered)


if __name__ == "__main__":
    unittest.main()
