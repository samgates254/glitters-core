from __future__ import annotations

import unittest
import numpy as np

from glitters_portfolio_intelligence.scenarios.cvar_verifier import compute_rockafellar_cvar_1d


class TestCVaREmpiricalVerifier(unittest.TestCase):
    def test_repeated_scenario_losses_and_mass_at_var(self):
        # 10 scenarios with repeated losses: [0.05, 0.05, 0.10, 0.10, 0.10, 0.10, 0.20, 0.20, 0.30, 0.30]
        # Equal probabilities 0.10 each
        losses = [0.05, 0.05, 0.10, 0.10, 0.10, 0.10, 0.20, 0.20, 0.30, 0.30]
        probs = [0.10] * 10
        alpha = 0.70  # tail is upper 30% (indices 7, 8, 9: 0.20, 0.30, 0.30)

        res = compute_rockafellar_cvar_1d(losses, probs, alpha=alpha)
        self.assertTrue(res.is_verified)
        self.assertAlmostEqual(res.discrepancy, 0.0, places=4)
        # Check that CVaR is higher than VaR
        self.assertGreaterEqual(res.cvar, res.var)

    def test_unequal_scenario_probabilities(self):
        losses = [0.02, 0.05, 0.15, 0.25, 0.50]
        probs = [0.40, 0.30, 0.15, 0.10, 0.05]

        for alpha in [0.80, 0.90, 0.95]:
            res = compute_rockafellar_cvar_1d(losses, probs, alpha=alpha)
            self.assertTrue(res.is_verified)
            self.assertGreaterEqual(res.cvar, res.var)

    def test_single_dominant_loss_scenario(self):
        # 9 benign scenarios with 0 loss, 1 catastrophic scenario with 80% loss (prob 0.02)
        losses = [0.0] * 9 + [0.80]
        probs = [0.98 / 9.0] * 9 + [0.02]

        # At alpha = 0.95: tail mass 0.05 includes the 0.02 catastrophic loss plus 0.03 of 0-loss atom
        res = compute_rockafellar_cvar_1d(losses, probs, alpha=0.95)
        self.assertTrue(res.is_verified)
        # Catastrophic loss contributes 0.02 * 0.80 / 0.05 = 0.32
        expected_cvar = (0.02 * 0.80) / 0.05
        self.assertAlmostEqual(res.cvar, expected_cvar, places=3)

    def test_zero_loss_scenarios(self):
        # All zero loss
        losses = [0.0, 0.0, 0.0, 0.0, 0.0]
        probs = [0.20] * 5

        res = compute_rockafellar_cvar_1d(losses, probs, alpha=0.95)
        self.assertTrue(res.is_verified)
        self.assertAlmostEqual(res.cvar, 0.0, places=6)
        self.assertAlmostEqual(res.var, 0.0, places=6)

    def test_different_confidence_levels_monotonicity(self):
        # CVaR must be strictly monotonic non-decreasing with alpha
        losses = [0.01, 0.04, 0.08, 0.12, 0.16, 0.22, 0.35, 0.48]
        probs = [0.125] * 8

        res_90 = compute_rockafellar_cvar_1d(losses, probs, alpha=0.90)
        res_95 = compute_rockafellar_cvar_1d(losses, probs, alpha=0.95)
        res_99 = compute_rockafellar_cvar_1d(losses, probs, alpha=0.99)

        self.assertTrue(res_90.is_verified)
        self.assertTrue(res_95.is_verified)
        self.assertTrue(res_99.is_verified)

        self.assertLessEqual(res_90.cvar, res_95.cvar)
        self.assertLessEqual(res_95.cvar, res_99.cvar)


if __name__ == "__main__":
    unittest.main()
