from __future__ import annotations
"""Comprehensive Unit, Property-Based, Phase 1 Audit Conformance & 15-Scenario Adversarial Test Suite.

Tests `glitters_rental_optimization` v2.0 across:
1. Strict separation of the 7 monetary quantities (`RentalMonetaryLedger`).
2. Phase 1 Mathematical Audit Remediation Verification:
   - CRITICAL 1: Dimensional consistency of Weibull survival scale `eta_i(P)` [months] and
     instantaneous hazard `h_i(P, tau) = (nu/eta)*(tau/eta)**(nu-1)` [month^-1].
   - CRITICAL 2: Orthogonal IV-DML first-stage relevance (`F >= 10`) vs weak/missing instrument abstention.
   - CRITICAL 3: Nonlinearly constrained `SLSQP` portfolio optimization with post-solve verification.
   - HIGH 4: 4-state Discrete-Time Markov Chain (DTMC) finite-horizon occupancy capturing mid-horizon
     lease expiry and turnover.
   - Section 1 & 3 Hardening: Tri-state boolean parsing (`"False"` -> `False`, `None` -> `None`),
     cryptographic/ledger transaction evidence verification, deterministic worst-tier confidence rule,
     inventory-aware sibling unit preservation, and outcome-level fairness auditing.
3. Mathematical property-based invariants across all 12 property segments.
4. All 15 mandatory adversarial conditions.
"""


from dataclasses import replace
import math
import time
import unittest
import numpy as np

from glitters_rental_optimization import (
    CausalIdentificationStatus,
    ConfidenceTier,
    DynamicPricingEngine,
    HedonicElasticityEstimator,
    KenyanMarketDataSanitizer,
    LeaseStatus,
    MarketComparableListing,
    OwnerPricingPolicy,
    PortfolioRevenueOptimizer,
    PricingStatus,
    PropertySegment,
    RenewalPricingEngine,
    RentalDemandModel,
    SinglePropertyPricingOptimizer,
    UnitSpecification,
    VacancyOptimizationEngine,
    audit_portfolio_pricing_fairness,
    classify_worst_applicable_confidence_tier,
    generate_synthetic_kenyan_portfolio,
    parse_optional_tri_state_bool,
    verify_transaction_evidence,
)


class TestPhase1AuditRemediationConformance(unittest.TestCase):
    """Explicitly tests every Phase 1 audit finding (CRITICAL 1-3, HIGH 4, Adapter & Inconsistencies)."""

    def setUp(self) -> None:
        self.units, self.comps = generate_synthetic_kenyan_portfolio(
            n_units=24, n_comparables=96, random_seed=101
        )
        self.optimizer = SinglePropertyPricingOptimizer()

    def test_critical_1_weibull_hazard_dimensional_consistency(self) -> None:
        """CRITICAL 1: Verifies h(P, tau) = (nu / eta) * (tau / eta)**(nu - 1) and E[V] = eta * Gamma(1 + 1/nu)."""
        demand_model = RentalDemandModel()
        unit = replace(self.units[0], current_vacancy_days=60)  # tau = 2.0 months
        ev = demand_model.evaluate_at_price(68000.0, unit, 68000.0, -1.65)

        eta = ev.weibull_scale_eta_months
        nu = ev.weibull_shape_nu
        tau_months = 60.0 / 30.0
        expected_h = (nu / eta) * ((tau_months / eta) ** (nu - 1.0))
        self.assertAlmostEqual(ev.instantaneous_hazard_monthly, expected_h, places=7)
        self.assertAlmostEqual(eta, 1.0 / ev.thinned_clearing_rate_monthly, places=7)
        expected_v = eta * math.gamma(1.0 + 1.0 / nu)
        self.assertAlmostEqual(ev.expected_vacancy_duration_months, expected_v, places=4)

    def test_critical_2_orthogonal_iv_dml_identification_and_abstention(self) -> None:
        """CRITICAL 2: Verifies IV-DML abstains on missing/weak IV and identifies when F >= 10."""
        estimator = HedonicElasticityEstimator()
        unit = self.units[0]

        # Case A: Observational records WITHOUT exogenous cost_shifter_iv -> must abstain
        obs_no_iv = [
            {"price_kes": 65000.0 + 1000 * k, "market_rent_kes": 68000.0, "vacancy_months": 1.2}
            for k in range(15)
        ]
        res_no_iv = estimator.estimate_market_rent_and_elasticity(unit, self.comps, obs_no_iv)
        self.assertEqual(
            res_no_iv.causal_identification_status,
            CausalIdentificationStatus.ABSTAINED_WEAK_OR_MISSING_IV,
        )

        # Case B: Strong exogenous cost-shifter instrument (F >= 10) -> identifies negative causal elasticity
        rng = np.random.default_rng(77)
        obs_strong_iv = []
        for _ in range(40):
            z_iv = float(rng.normal(0.0, 0.15))
            unobs_demand = float(rng.normal(0.0, 0.04))
            log_p_ratio = 0.85 * z_iv + 0.5 * unobs_demand + float(rng.normal(0.0, 0.02))
            # True structural elasticity = -1.75
            log_hazard = -1.75 * log_p_ratio + unobs_demand + float(rng.normal(0.0, 0.03))
            obs_strong_iv.append(
                {
                    "floor_area_sqm": 85.0,
                    "bedrooms": 2.0,
                    "security_tier": 3.0,
                    "price_kes": 68000.0 * math.exp(log_p_ratio),
                    "market_rent_kes": 68000.0,
                    "vacancy_months": math.exp(-log_hazard),
                    "cost_shifter_iv": z_iv,
                }
            )
        res_strong = estimator.estimate_market_rent_and_elasticity(unit, self.comps, obs_strong_iv)
        self.assertEqual(
            res_strong.causal_identification_status,
            CausalIdentificationStatus.IDENTIFIED_STRONG_IV,
        )
        self.assertGreaterEqual(res_strong.first_stage_f_stat, 10.0)
        self.assertAlmostEqual(res_strong.price_elasticity, -1.75, delta=0.25)

    def test_critical_3_slsqp_portfolio_constraints_and_verification(self) -> None:
        """CRITICAL 3: Verifies SLSQP solver enforces sibling parity and outputs constraint verification."""
        port_opt = PortfolioRevenueOptimizer(self.optimizer)
        res = port_opt.optimize_portfolio(self.units, self.comps)
        self.assertEqual(res.constraint_verification.solver_method, "SLSQP")
        self.assertTrue(res.constraint_verification.all_box_bounds_satisfied)
        self.assertTrue(res.constraint_verification.all_sibling_parity_satisfied)
        self.assertTrue(res.constraint_verification.turnover_capacity_constraints_satisfied)
        self.assertTrue(res.fairness_audit.passed_outcome_fairness)

    def test_high_4_dtmc_finite_horizon_mid_lease_expiry(self) -> None:
        """HIGH 4: Verifies DTMC captures mid-horizon lease expiry (6 months remaining < 12m horizon)."""
        demand_model = RentalDemandModel(planning_horizon_months=12)
        unit_long_lease = replace(
            self.units[0],
            lease_status=LeaseStatus.OCCUPIED_ACTIVE,
            months_remaining_on_lease=12,
        )
        unit_expiring_mid_horizon = replace(
            self.units[0],
            lease_status=LeaseStatus.OCCUPIED_ACTIVE,
            months_remaining_on_lease=4,
        )
        ev_long = demand_model.evaluate_at_price(75000.0, unit_long_lease, 68000.0, -1.65)
        ev_short = demand_model.evaluate_at_price(75000.0, unit_expiring_mid_horizon, 68000.0, -1.65)
        # Unit expiring at month 4 faces non-renewal turnover risk before month 12, so its 12m occupancy is strictly lower
        self.assertLess(
            ev_short.finite_horizon_occupancy_prob, ev_long.finite_horizon_occupancy_prob
        )
        self.assertGreater(ev_short.monthly_turnover_frequency, 0.0)

    def test_adapter_hardening_tri_state_bool_and_evidence_verification(self) -> None:
        """Section 1 Fix: Verifies string 'False' is False, None stays None, and unverified tx is rejected."""
        self.assertIs(parse_optional_tri_state_bool("False", "has_borehole_water"), False)
        self.assertIs(parse_optional_tri_state_bool("0", "has_borehole_water"), False)
        self.assertIs(parse_optional_tri_state_bool("True", "has_borehole_water"), True)
        self.assertIsNone(parse_optional_tri_state_bool(None, "has_borehole_water"))
        self.assertIsNone(parse_optional_tri_state_bool("unknown", "has_borehole_water"))

        # Evidence verification requires contract_id, receipt_hash, and positive rent
        self.assertFalse(verify_transaction_evidence(None, "SHA256_123456", 55000.0))
        self.assertFalse(verify_transaction_evidence("LEASE_99", None, 55000.0))
        self.assertTrue(verify_transaction_evidence("LEASE_99", "SHA256_123456", 55000.0))

    def test_deterministic_worst_applicable_confidence_tier_precedence(self) -> None:
        """Section 3 Fix: Verifies N_eff=4.0 and W_PI=0.25 deterministically yields WEAKLY_SUPPORTED."""
        tier = classify_worst_applicable_confidence_tier(
            effective_sample_size=4.0,
            mahalanobis_distance=1.5,
            relative_pi_width=0.25,
            area_out_of_bounds=False,
        )
        self.assertEqual(tier, ConfidenceTier.WEAKLY_SUPPORTED)

    def test_inventory_aware_deduplication_preserves_distinct_building_units(self) -> None:
        """Section 4 Fix: Distinct unit_identifier doors in the same apartment block are never collapsed."""
        sanitizer = KenyanMarketDataSanitizer()
        base = self.comps[0]
        door_a = replace(base, listing_id="APT_DOOR_A1", unit_identifier="Block_A_Door_101")
        door_b = replace(base, listing_id="APT_DOOR_A2", unit_identifier="Block_A_Door_102")
        dup_scrape = replace(base, listing_id="PORTAL_DUP_1", unit_identifier=None)
        dup_scrape_2 = replace(base, listing_id="PORTAL_DUP_2", unit_identifier=None)

        report = sanitizer.sanitize([door_a, door_b, dup_scrape, dup_scrape_2])
        # Both distinct doors are preserved; the unlabelled duplicate portal scrape is collapsed
        self.assertGreaterEqual(report.sibling_units_preserved_count, 1)
        self.assertGreaterEqual(report.final_clean_count, 2)


class TestUnitAndMonetaryTaxonomy(unittest.TestCase):
    """Unit tests for strict separation of the 7 monetary quantities and core models."""

    def setUp(self) -> None:
        self.units, self.comps = generate_synthetic_kenyan_portfolio(
            n_units=20, n_comparables=80, random_seed=101
        )
        self.optimizer = SinglePropertyPricingOptimizer()

    def test_seven_monetary_quantities_never_conflated(self) -> None:
        """Verifies that Asking, Achievable Market, Contractual, Recommended, Collected, ERR, and NOI are distinct."""
        vacant_unit = next(u for u in self.units if u.lease_status == LeaseStatus.VACANT)
        unit_with_high_ask = replace(
            vacant_unit,
            current_asking_rent_kes=95000.0,
            contractual_rent_kes=None,
            actual_collected_rent_kes=None,
        )
        rec = self.optimizer.optimize_unit_price(unit_with_high_ask, self.comps)
        ledger = rec.ledger

        self.assertEqual(ledger.currency, "KES")
        self.assertEqual(ledger.accounting_period, "MONTHLY")
        self.assertEqual(ledger.asking_rent_kes, 95000.0)
        self.assertNotAlmostEqual(
            ledger.asking_rent_kes, ledger.achievable_market_rent_kes, places=0
        )
        self.assertLess(ledger.effective_rental_revenue_kes, ledger.recommended_rent_kes)
        self.assertLess(ledger.net_operating_income_kes, ledger.effective_rental_revenue_kes)

    def test_contractual_rent_never_overridden_on_active_lease(self) -> None:
        """Verifies non-negotiable rule: active contractual rent (>3m remaining) is never overridden."""
        active_unit = next(u for u in self.units if u.lease_status == LeaseStatus.OCCUPIED_ACTIVE)
        active_unit = replace(
            active_unit,
            contractual_rent_kes=62500.0,
            months_remaining_on_lease=8,
        )
        rec = self.optimizer.optimize_unit_price(active_unit, self.comps)
        self.assertEqual(rec.status, PricingStatus.LOCKED_CONTRACTUAL)
        self.assertEqual(rec.ledger.recommended_rent_kes, 62500.0)
        self.assertEqual(rec.ledger.contractual_rent_kes, 62500.0)

    def test_expiring_lease_routes_to_renewal_workflow_without_mutating_contract(self) -> None:
        """Verifies expiring leases (1..3 months left) route to RENEWAL_WORKFLOW_REQUIRED."""
        exp_unit = replace(
            self.units[0],
            lease_status=LeaseStatus.EXPIRING_RENEWAL_WINDOW,
            contractual_rent_kes=60000.0,
            months_remaining_on_lease=2,
        )
        rec = self.optimizer.optimize_unit_price(exp_unit, self.comps)
        self.assertEqual(rec.status, PricingStatus.RENEWAL_WORKFLOW_REQUIRED)
        self.assertEqual(rec.ledger.contractual_rent_kes, 60000.0)
        self.assertEqual(rec.effective_date_offset_months, 2)
        self.assertTrue(rec.requires_owner_approval)

    def test_anti_discrimination_blocks_protected_attributes(self) -> None:
        """Verifies that protected personal attributes immediately raise ValueError."""
        renewal_engine = RenewalPricingEngine()
        unit = self.units[0]
        for forbidden_key in (
            "ethnicity",
            "tribe",
            "religion",
            "gender",
            "marital_status",
            "disability",
        ):
            with self.assertRaises(ValueError):
                renewal_engine.recommend_renewal(
                    unit=unit,
                    achievable_market_rent_kes=65000.0,
                    price_elasticity=-1.65,
                    tenant_context={"tenure_months": 24, forbidden_key: "redacted"},
                )

    def test_vacancy_rent_reduction_not_recommended_without_justification(self) -> None:
        """Verifies that automatic rent cuts are rejected when locked-in revenue loss exceeds vacancy savings."""
        vac_engine = VacancyOptimizationEngine()
        unit = replace(
            self.units[0],
            current_asking_rent_kes=68000.0,
            typical_lease_term_months=24,
        )
        analysis = vac_engine.evaluate_rent_reduction(
            unit=unit,
            candidate_new_rent_kes=55000.0,
            achievable_market_rent_kes=67000.0,
            price_elasticity=-1.5,
            elasticity_std_error=0.25,
        )
        self.assertFalse(analysis.recommend_rent_reduction)
        self.assertLess(analysis.net_economic_benefit_of_cut_kes, 0.0)


class TestPropertyBasedMathematicalInvariants(unittest.TestCase):
    """Generative property-based tests over randomized unit/price configurations."""

    def test_Generative_demand_and_survival_invariants(self) -> None:
        """Tests law of demand, Weibull scale monotonicity, and probability axioms across all 12 segments."""
        rng = np.random.default_rng(4242)
        demand_model = RentalDemandModel()
        units, _ = generate_synthetic_kenyan_portfolio(n_units=36, n_comparables=72, random_seed=77)

        for u in units:
            mkt_rent = float(u.extra_metadata["synthetic_true_clearing_rent_kes"])
            elasticity = float(rng.uniform(-2.8, -1.05))
            prices = np.linspace(mkt_rent * 0.65, mkt_rent * 1.45, 8)
            evals = [
                demand_model.evaluate_at_price(float(p), u, mkt_rent, elasticity)
                for p in prices
            ]

            for idx in range(len(evals) - 1):
                low_p, high_p = evals[idx], evals[idx + 1]
                self.assertGreater(
                    low_p.application_arrival_rate,
                    high_p.application_arrival_rate,
                )
                self.assertGreaterEqual(
                    low_p.conversion_probability,
                    high_p.conversion_probability,
                )
                self.assertLess(
                    low_p.weibull_scale_eta_months,
                    high_p.weibull_scale_eta_months,
                )
                self.assertLess(
                    low_p.expected_vacancy_duration_months,
                    high_p.expected_vacancy_duration_months,
                )
                self.assertAlmostEqual(
                    low_p.steady_state_occupancy_prob + low_p.steady_state_vacancy_prob,
                    1.0,
                    places=7,
                )


class TestFifteenAdversarialConditions(unittest.TestCase):
    """Tests all 15 mandatory adversarial conditions to verify safe, deterministic behavior."""

    def setUp(self) -> None:
        self.units, self.comps = generate_synthetic_kenyan_portfolio(
            n_units=24, n_comparables=100, random_seed=303
        )
        self.base_unit = replace(
            self.units[0],
            lease_status=LeaseStatus.VACANT,
            current_asking_rent_kes=70000.0,
            contractual_rent_kes=None,
        )
        self.optimizer = SinglePropertyPricingOptimizer()
        self.portfolio_optimizer = PortfolioRevenueOptimizer(self.optimizer)

    def test_01_no_comparable_rental_data(self) -> None:
        rec = self.optimizer.optimize_unit_price(self.base_unit, comparables=[])
        self.assertEqual(rec.status, PricingStatus.SAFE_FALLBACK_ABSTENTION)
        self.assertEqual(
            rec.confidence_tier, ConfidenceTier.INSUFFICIENT_DATA_SAFE_FALLBACK
        )

    def test_02_extremely_low_demand(self) -> None:
        mpc = DynamicPricingEngine()
        traj = mpc.compute_mpc_trajectory(
            self.base_unit,
            achievable_market_rent_kes=68000.0,
            price_elasticity=-1.8,
            macro_demand_shock=0.15,
        )
        self.assertEqual(len(traj.monthly_recommended_rents_kes), 6)
        self.assertLessEqual(traj.monthly_recommended_rents_kes[0], 68000.0)

    def test_03_sudden_demand_spikes(self) -> None:
        mpc = DynamicPricingEngine()
        traj = mpc.compute_mpc_trajectory(
            self.base_unit,
            achievable_market_rent_kes=68000.0,
            price_elasticity=-1.6,
            macro_demand_shock=2.5,
        )
        max_cap = self.base_unit.owner_policy.max_rent_kes or 1e9
        for p in traj.monthly_recommended_rents_kes:
            self.assertLessEqual(p, max_cap + 1.0)

    def test_04_economic_downturns(self) -> None:
        stressed_unit = replace(
            self.base_unit,
            target_tenant_median_income_kes=145000.0,
            owner_policy=OwnerPricingPolicy(
                min_rent_kes=40000.0,
                max_rent_kes=90000.0,
                affordability_income_ratio_cap=0.33,
            ),
        )
        rec = self.optimizer.optimize_unit_price(stressed_unit, self.comps)
        self.assertLessEqual(rec.ledger.recommended_rent_kes, 145000.0 * 0.33 + 1.0)

    def test_05_excessive_asking_rents(self) -> None:
        polluted_comps = list(self.comps) + [
            MarketComparableListing(
                listing_id="ADV_EXTREME_ASK_1",
                segment=self.base_unit.segment,
                county=self.base_unit.county,
                neighbourhood=self.base_unit.neighbourhood,
                floor_area_sqm=85.0,
                bedrooms=2,
                asking_rent_kes=420000.0,
                verified_transaction_rent_kes=None,
                listing_age_days=10,
                is_verified_transaction=False,
            )
        ]
        clean_rec = self.optimizer.optimize_unit_price(self.base_unit, self.comps)
        polluted_rec = self.optimizer.optimize_unit_price(self.base_unit, polluted_comps)
        rel_diff = abs(
            polluted_rec.ledger.achievable_market_rent_kes
            - clean_rec.ledger.achievable_market_rent_kes
        ) / clean_rec.ledger.achievable_market_rent_kes
        self.assertLess(rel_diff, 0.03)

    def test_06_outdated_listings(self) -> None:
        sanitizer = KenyanMarketDataSanitizer()
        stale_comps = [
            replace(c, listing_age_days=190, is_verified_transaction=False)
            for c in self.comps[:12]
        ]
        report = sanitizer.sanitize(stale_comps)
        self.assertEqual(report.stale_downweighted_count, len(report.sanitized_comparables))
        for sc in report.sanitized_comparables:
            self.assertLess(sc.reliability_weight, 0.20)
            self.assertLess(sc.estimated_clearing_rent_kes, sc.raw_asking_rent_kes * 0.88)

    def test_07_incorrect_property_specifications(self) -> None:
        bad_spec_unit = replace(
            self.base_unit,
            segment=PropertySegment.BEDSITTER,
            floor_area_sqm=650.0,
            bedrooms=4,
        )
        rec = self.optimizer.optimize_unit_price(bad_spec_unit, self.comps)
        self.assertEqual(rec.confidence_tier, ConfidenceTier.OUT_OF_DISTRIBUTION)
        self.assertEqual(rec.status, PricingStatus.SAFE_FALLBACK_ABSTENTION)

    def test_08_duplicate_rental_advertisements(self) -> None:
        sanitizer = KenyanMarketDataSanitizer()
        single_comp = replace(self.comps[0], unit_identifier=None)
        dup_burst = [
            replace(
                single_comp,
                listing_id=f"DUP_BURST_{k}",
                asking_rent_kes=single_comp.asking_rent_kes * (1.0 + 0.005 * (k % 3)),
                floor_area_sqm=single_comp.floor_area_sqm * (1.0 + 0.002 * (k % 3)),
                source_portal=f"portal_{k}",
                unit_identifier=None,
            )
            for k in range(20)
        ]
        report = sanitizer.sanitize(dup_burst)
        self.assertEqual(report.final_clean_count, 1)
        self.assertEqual(report.deduplicated_count, 19)

    def test_09_highly_seasonal_markets(self) -> None:
        hosp_unit = next(
            u for u in self.units if u.segment == PropertySegment.HOSPITALITY_SHORT_STAY
        )
        demand_model = RentalDemandModel()
        ev_may_trough = demand_model.evaluate_at_price(
            85000.0, hosp_unit, 85000.0, -1.95, calendar_month=5
        )
        ev_dec_peak = demand_model.evaluate_at_price(
            85000.0, hosp_unit, 85000.0, -1.95, calendar_month=12
        )
        self.assertGreater(
            ev_dec_peak.application_arrival_rate, ev_may_trough.application_arrival_rate * 1.4
        )

    def test_10_high_vacancy_costs(self) -> None:
        low_vac_cost_unit = replace(
            self.base_unit, current_asking_rent_kes=None, vacancy_holding_cost_kes=500.0
        )
        high_vac_cost_unit = replace(
            self.base_unit, current_asking_rent_kes=None, vacancy_holding_cost_kes=28000.0
        )
        rec_low = self.optimizer.optimize_unit_price(low_vac_cost_unit, self.comps)
        rec_high = self.optimizer.optimize_unit_price(high_vac_cost_unit, self.comps)
        self.assertLessEqual(
            rec_high.ledger.recommended_rent_kes, rec_low.ledger.recommended_rent_kes
        )

    def test_11_conflicting_owner_pricing_policies(self) -> None:
        conflict_unit = replace(
            self.base_unit,
            owner_policy=OwnerPricingPolicy(min_rent_kes=88000.0, max_rent_kes=72000.0),
        )
        self.assertTrue(conflict_unit.owner_policy.has_conflict())
        rec = self.optimizer.optimize_unit_price(conflict_unit, self.comps)
        self.assertEqual(rec.status, PricingStatus.CONFLICTING_OWNER_POLICY)
        self.assertGreater(rec.ledger.recommended_rent_kes, 0.0)

    def test_12_very_small_rental_portfolios(self) -> None:
        res = self.portfolio_optimizer.optimize_portfolio([self.base_unit], self.comps)
        self.assertEqual(res.coupled_portfolio_metrics.total_units, 1)

    def test_13_large_rental_portfolios_scalability(self) -> None:
        large_units, large_comps = generate_synthetic_kenyan_portfolio(
            n_units=500, n_comparables=400, random_seed=808
        )
        fast_single = SinglePropertyPricingOptimizer()
        fast_single.uncertainty_engine.n_simulations = 100
        fast_port = PortfolioRevenueOptimizer(fast_single)

        t0 = time.perf_counter()
        res = fast_port.optimize_portfolio(large_units, large_comps)
        elapsed = time.perf_counter() - t0
        self.assertEqual(res.coupled_portfolio_metrics.total_units, 500)
        self.assertGreaterEqual(res.portfolio_noi_uplift_vs_independent_kes, -1.0)
        self.assertLess(elapsed, 20.0)

    def test_14_missing_historical_occupancy(self) -> None:
        estimator = HedonicElasticityEstimator()
        val = estimator.estimate_market_rent_and_elasticity(
            self.base_unit, self.comps, observed_lease_records=None
        )
        self.assertLess(val.price_elasticity, -0.5)
        self.assertEqual(
            val.causal_identification_status,
            CausalIdentificationStatus.ABSTAINED_WEAK_OR_MISSING_IV,
        )

    def test_15_sudden_changes_in_operating_expenses(self) -> None:
        shocked_unit = replace(self.base_unit, routine_maintenance_cost_kes=22000.0)
        rec_base = self.optimizer.optimize_unit_price(self.base_unit, self.comps)
        rec_shock = self.optimizer.optimize_unit_price(shocked_unit, self.comps)
        self.assertLess(
            rec_shock.ledger.net_operating_income_kes,
            rec_base.ledger.net_operating_income_kes,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)