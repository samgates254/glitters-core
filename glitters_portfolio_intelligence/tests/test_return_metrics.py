from __future__ import annotations

import unittest

from glitters_portfolio_intelligence.domain.enums import IRRStatus
from glitters_portfolio_intelligence.returns.metrics import (
    compute_cap_rate,
    compute_cash_on_cash,
    compute_npv,
    compute_irr,
    compute_equity_multiple,
    compute_roic,
    compute_multi_period_total_return,
)


class TestReturnMetrics(unittest.TestCase):
    def test_cap_rate_and_cash_on_cash(self):
        noi = 10_000_000_00        # KES 10M NOI
        valuation = 120_000_000_00  # KES 120M Valuation
        cap_rate = compute_cap_rate(noi, valuation)
        self.assertAlmostEqual(cap_rate, 10.0 / 120.0, places=5)

        cfads = 4_000_000_00
        equity = 40_000_000_00
        coc = compute_cash_on_cash(cfads, equity)
        self.assertAlmostEqual(coc, 0.10, places=5)

    def test_npv_deterministic(self):
        # Invest 100M, receive 10M for 3 years, discount at 10%
        # NPV = -100 + 10/1.1 + 10/1.1^2 + 10/1.1^3
        cfs = [-100_000_000_00, 10_000_000_00, 10_000_000_00, 10_000_000_00]
        npv = compute_npv(0.10, cfs)
        expected = -100e8 + 10e8/1.1 + 10e8/(1.1**2) + 10e8/(1.1**3)
        self.assertAlmostEqual(npv, expected, delta=1.0)

    def test_irr_standard_single_root(self):
        # Standard investment: -100M, +20M, +20M, +20M, +20M, +60M (IRR ~ 13.88%)
        cfs = [-100_000_000_00, 20_000_000_00, 20_000_000_00, 20_000_000_00, 20_000_000_00, 60_000_000_00]
        res = compute_irr(cfs)
        self.assertEqual(res.status, IRRStatus.CONVERGED)
        self.assertIsNotNone(res.rate)
        # Check that NPV at IRR is approximately 0
        npv_at_irr = compute_npv(res.rate, cfs)
        self.assertAlmostEqual(npv_at_irr, 0.0, delta=100.0)

    def test_irr_all_positive_and_all_negative(self):
        all_pos = [10_000_000_00, 20_000_000_00, 30_000_000_00]
        res_pos = compute_irr(all_pos)
        self.assertEqual(res_pos.status, IRRStatus.ALL_POSITIVE_CASHFLOWS)
        self.assertIsNone(res_pos.rate)

        all_neg = [-10_000_000_00, -20_000_000_00]
        res_neg = compute_irr(all_neg)
        self.assertEqual(res_neg.status, IRRStatus.ALL_NEGATIVE_CASHFLOWS)
        self.assertIsNone(res_neg.rate)

    def test_irr_multiple_sign_changes(self):
        # Multiple sign changes: -100, +250, -155
        # Classic multi-root case: roots at r = 10% and r = 45%
        cfs = [-100_000_00, 255_000_00, -155_000_00]
        res = compute_irr(cfs)
        # Must detect multiple roots or converge with diagnostic
        self.assertIn(res.status, [IRRStatus.MULTIPLE_ROOTS_DETECTED, IRRStatus.CONVERGED])

    def test_equity_multiple_and_roic(self):
        initial_equity = 50_000_000_00
        cum_dist = 25_000_000_00
        net_terminal_equity = 75_000_000_00

        em = compute_equity_multiple(initial_equity, cum_dist, net_terminal_equity)
        # (25 + 75) / 50 = 2.0x
        self.assertAlmostEqual(em, 2.0, places=4)

        noi = 15_000_000_00
        invested = 100_000_000_00
        roic = compute_roic(noi, invested, tax_rate=0.30)
        # 15M * 0.7 / 100M = 10.5%
        self.assertAlmostEqual(roic, 0.105, places=4)

    def test_multi_period_total_return(self):
        initial_val = 100_000_000_00
        terminal_val = 130_000_000_00  # +30% capital
        cum_noi = 40_000_000_00        # +40% income
        inc, cap, tot = compute_multi_period_total_return(initial_val, terminal_val, cum_noi)
        self.assertAlmostEqual(inc, 0.40, places=4)
        self.assertAlmostEqual(cap, 0.30, places=4)
        self.assertAlmostEqual(tot, 0.70, places=4)


if __name__ == "__main__":
    unittest.main()
