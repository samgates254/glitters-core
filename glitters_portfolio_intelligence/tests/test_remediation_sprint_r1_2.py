from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Sequence
import unittest
from uuid import uuid4
import numpy as np

from glitters_contracts import (
    AbstentionReason,
    AlgorithmicExecutionStatus,
    AuthorizedCallerContext,
    ConfidenceTier,
    OrganizationContext,
    PortfolioInvestmentBridge,
)

from glitters_portfolio_intelligence.domain.entities import Portfolio, PropertyAsset
from glitters_portfolio_intelligence.domain.enums import (
    AssetCategory,
    DecisionAction,
    GeographicSubmarket,
    OptimizationObjective,
    OptimizationStatus,
    OwnershipRepresentation,
    ScenarioProvenanceKind,
    SolverBackendType,
    UnsupportedSolverConfigurationError,
)
from glitters_portfolio_intelligence.governance.committee_gate import InvestmentDecisionGovernanceGate
from glitters_portfolio_intelligence.optimization.constraints import PortfolioConstraints
from glitters_portfolio_intelligence.optimization.frontier import EfficientFrontierExplorer
from glitters_portfolio_intelligence.optimization.multi_period import MultiPeriodPortfolioOptimizer
from glitters_portfolio_intelligence.optimization.rockafellar_cvar import RockafellarMeanCVaROptimizer
from glitters_portfolio_intelligence.optimization.solver_backends import (
    PortfolioSolverBackend,
    SolverExecutionResult,
)
from glitters_portfolio_intelligence.scenarios.cvar_verifier import compute_rockafellar_cvar_1d
from glitters_portfolio_intelligence.scenarios.provenance import (
    EvidenceArtifactRegistry,
    ScenarioDefinition,
    default_evidence_registry,
)
from glitters_portfolio_intelligence.scenarios.stress_engine import ScenarioStressEngine
from glitters_portfolio_intelligence.scenarios.validation import validate_scenario_distribution


def make_test_asset(
    asset_id: str,
    val_kes: int = 100_000_000,
    rent_kes: int = 800_000,
    opex_kes: int = 150_000,
    capex_kes: int = 5_000_000,
    submarket: GeographicSubmarket = GeographicSubmarket.WESTLANDS,
    category: AssetCategory = AssetCategory.COMMERCIAL_OFFICE,
    initial_frac: float = 0.0,
) -> PropertyAsset:
    return PropertyAsset(
        asset_id=asset_id,
        name=f"Property {asset_id}",
        submarket=submarket,
        category=category,
        acquisition_valuation_cents=val_kes * 100,
        current_valuation_cents=val_kes * 100,
        monthly_gross_potential_rent_cents=rent_kes * 100,
        monthly_operating_expense_cents=opex_kes * 100,
        annual_baseline_vacancy_rate=0.05,
        annual_baseline_credit_loss_rate=0.01,
        annual_debt_service_cents=0,
        current_loan_balance_cents=0,
        annual_routine_maintenance_cents=500_000_00,
        planned_capex_cents=capex_kes * 100,
        initial_ownership_fraction=initial_frac,
    )


class TestRemediationSprintR1_2(unittest.TestCase):
    def setUp(self) -> None:
        self.tenant_id = uuid4()
        self.caller = AuthorizedCallerContext(
            caller_user_id=uuid4(),
            tenant_org_id=self.tenant_id,
            granted_permissions=frozenset(["portfolio:read", "portfolio:optimize", "portfolio:recommend"]),
        )
        self.org_ctx = OrganizationContext(
            tenant_org_id=self.tenant_id,
            organization_name="GLITTERS Test Org",
        )

    # =========================================================================
    # 1. P0: Confidence and Evidence Verification
    # =========================================================================
    def test_missing_provenance_never_defaults_to_historical(self):
        """Missing provenance must never default to historical observation; it defaults to EXPERT_ASSUMPTION."""
        dummy_opt = SolverExecutionResult(
            solver_type=SolverBackendType.HIGHS_SCIPY,
            solver_version="HiGHS 1.5",
            status=OptimizationStatus.OPTIMAL,
            is_optimal=True,
            optimal_weights=np.array([1.0]),
            expected_return=0.10,
            cvar_alpha=0.05,
            var_alpha=0.04,
            objective_value=0.03,
            optimality_gap=0.0,
            solve_time_seconds=0.01,
            active_constraints=(),
        )
        portfolio = Portfolio(
            portfolio_id="PORT-EV-01",
            tenant_org_id=str(self.tenant_id),
            assets=(make_test_asset("ASSET-01"),),
            cash_reserves_cents=10_000_000_00,
        )

        env = InvestmentDecisionGovernanceGate.create_recommendation_envelope(
            portfolio=portfolio,
            solver_result=dummy_opt,
            caller=self.caller,
            provenance=None,  # Omitted provenance
            scenario_evidence_verified=None,  # Omitted flag
        )
        # Omitted provenance defaults to EXPERT_ASSUMPTION -> MODERATE_CONFIDENCE (never HIGH_CONFIDENCE)
        self.assertEqual(env.confidence_tier, ConfidenceTier.MODERATE_CONFIDENCE)

    def test_caller_boolean_alone_cannot_authenticate_evidence(self):
        """A caller-supplied Boolean True alone cannot produce HIGH_CONFIDENCE without registered artifact content."""
        dummy_opt = SolverExecutionResult(
            solver_type=SolverBackendType.HIGHS_SCIPY,
            solver_version="HiGHS 1.5",
            status=OptimizationStatus.OPTIMAL,
            is_optimal=True,
            optimal_weights=np.array([1.0]),
            expected_return=0.10,
            cvar_alpha=0.05,
            var_alpha=0.04,
            objective_value=0.03,
            optimality_gap=0.0,
            solve_time_seconds=0.01,
            active_constraints=(),
        )
        portfolio = Portfolio(
            portfolio_id="PORT-EV-02",
            tenant_org_id=str(self.tenant_id),
            assets=(make_test_asset("ASSET-02"),),
            cash_reserves_cents=10_000_000_00,
        )

        isolated_registry = EvidenceArtifactRegistry()  # Empty registry
        env = InvestmentDecisionGovernanceGate.create_recommendation_envelope(
            portfolio=portfolio,
            solver_result=dummy_opt,
            caller=self.caller,
            provenance=ScenarioProvenanceKind.HISTORICAL_OBSERVATION,
            scenario_evidence_verified=True,  # Caller says True, but no artifact is registered!
            evidence_registry=isolated_registry,
            dataset_identifier="NON_EXISTENT_DATASET",
            dataset_sha256="0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        )
        self.assertEqual(env.confidence_tier, ConfidenceTier.MODERATE_CONFIDENCE)

    def test_evidence_artifact_content_digest_verification(self):
        """Artifact authentication must compute actual SHA256 from content and detect byte corruption."""
        registry = EvidenceArtifactRegistry()
        content = b"HassConsult Q4 2023 Real Estate Price Index Data"
        expected_digest = hashlib.sha256(content).hexdigest()

        # Register artifact
        actual_digest = registry.register_artifact("HASS-2023-Q4", content)
        self.assertEqual(actual_digest, expected_digest)

        # Authenticated match
        self.assertTrue(registry.is_artifact_authenticated("HASS-2023-Q4", expected_digest))

        # Corrupted / mismatched declared hash fails authentication
        tampered_digest = "a" * 64
        self.assertFalse(registry.is_artifact_authenticated("HASS-2023-Q4", tampered_digest))

        # Nonexistent dataset identifier fails authentication
        self.assertFalse(registry.is_artifact_authenticated("UNKNOWN-DATASET", expected_digest))

    def test_reclassify_unverified_historical_scenarios(self):
        """Unauthenticated historical scenarios are downgraded to EXPERT_ASSUMPTION before optimization."""
        sc_unverified = ScenarioDefinition(
            scenario_id="SCEN-HIST-FAKE",
            name="Claimed Historical Data Without Artifact",
            provenance=ScenarioProvenanceKind.HISTORICAL_OBSERVATION,
            source="Unverified internal paper",
            observation_or_forecast_period="2020",
            probability=0.25,
            model_version="1.0",
            transformation_methodology="Direct observation",
            dataset_identifier="FAKE-DATASET",
            dataset_sha256="ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
        )
        isolated_registry = EvidenceArtifactRegistry()
        self.assertFalse(sc_unverified.is_evidence_authenticated(isolated_registry))

        reclassified = sc_unverified.reclassify_if_unverified(isolated_registry)
        self.assertEqual(reclassified.provenance, ScenarioProvenanceKind.EXPERT_ASSUMPTION)
        self.assertIn("Reclassified", reclassified.transformation_methodology)

    def test_synthetic_scenario_cannot_receive_historical_confidence(self):
        """Synthetic scenarios always produce WEAKLY_SUPPORTED confidence tier."""
        dummy_opt = SolverExecutionResult(
            solver_type=SolverBackendType.HIGHS_SCIPY,
            solver_version="HiGHS 1.5",
            status=OptimizationStatus.OPTIMAL,
            is_optimal=True,
            optimal_weights=np.array([1.0]),
            expected_return=0.10,
            cvar_alpha=0.05,
            var_alpha=0.04,
            objective_value=0.03,
            optimality_gap=0.0,
            solve_time_seconds=0.01,
            active_constraints=(),
        )
        portfolio = Portfolio(
            portfolio_id="PORT-EV-SYN",
            tenant_org_id=str(self.tenant_id),
            assets=(make_test_asset("ASSET-SYN"),),
            cash_reserves_cents=10_000_000_00,
        )

        env = InvestmentDecisionGovernanceGate.create_recommendation_envelope(
            portfolio=portfolio,
            solver_result=dummy_opt,
            caller=self.caller,
            provenance=ScenarioProvenanceKind.SYNTHETIC_SCENARIO,
            scenario_evidence_verified=False,
        )
        self.assertEqual(env.confidence_tier, ConfidenceTier.WEAKLY_SUPPORTED)

    # =========================================================================
    # 2. P0: Discrete Investment Lots Mathematics
    # =========================================================================
    def test_discrete_lot_fraction_denominated_by_asset_current_valuation(self):
        """Lot size must be denominated on individual asset current valuation, not total portfolio capital."""
        asset1 = make_test_asset("A1", val_kes=100_000_000, initial_frac=0.0)  # KES 100M
        asset2 = make_test_asset("A2", val_kes=50_000_000, initial_frac=0.0)   # KES 50M
        portfolio = Portfolio(
            portfolio_id="PORT-LOT",
            tenant_org_id=str(self.tenant_id),
            assets=(asset1, asset2),
            cash_reserves_cents=200_000_000_00,
        )

        # Lot sizes: KES 10M for A1 (10%), KES 10M for A2 (20%)
        lot_sizes = {
            "A1": 10_000_000_00,  # 10%
            "A2": 10_000_000_00,  # 20%
        }

        frac1 = lot_sizes["A1"] / asset1.current_valuation_cents
        frac2 = lot_sizes["A2"] / asset2.current_valuation_cents
        self.assertAlmostEqual(frac1, 0.10)
        self.assertAlmostEqual(frac2, 0.20)

        # Solve multi-period with these discrete lots using HiGHS via OR-Tools
        res = MultiPeriodPortfolioOptimizer.optimize(
            portfolio=portfolio,
            candidate_assets=[asset1, asset2],
            horizon_years=2,
            representation=OwnershipRepresentation.DISCRETE_INVESTMENT_LOTS,
            lot_size_cents=lot_sizes,
            backend=SolverBackendType.HIGHS_ORTOOLS,
        )
        self.assertEqual(res.solver_backend, SolverBackendType.HIGHS_ORTOOLS)
        self.assertTrue(res.is_optimal)

        # Verify that all holdings are integer multiples of individual asset lot fractions
        for step in res.schedule:
            if step.asset_id == "A1":
                num_lots = round(step.holding_fraction / 0.10)
                self.assertAlmostEqual(step.holding_fraction, num_lots * 0.10, places=3)
            elif step.asset_id == "A2":
                num_lots = round(step.holding_fraction / 0.20)
                self.assertAlmostEqual(step.holding_fraction, num_lots * 0.20, places=3)

    def test_missing_asset_in_lot_mapping_rejected(self):
        """Missing lot size for any asset must raise ValueError (no hidden KES 10M default)."""
        asset1 = make_test_asset("A1", val_kes=100_000_000)
        asset2 = make_test_asset("A2", val_kes=50_000_000)
        portfolio = Portfolio(
            portfolio_id="PORT-LOT-MISS",
            tenant_org_id=str(self.tenant_id),
            assets=(asset1, asset2),
            cash_reserves_cents=100_000_000_00,
        )

        incomplete_lots = {"A1": 10_000_000_00}

        with self.assertRaises(ValueError) as ctx:
            MultiPeriodPortfolioOptimizer.optimize(
                portfolio=portfolio,
                candidate_assets=[asset1, asset2],
                horizon_years=2,
                representation=OwnershipRepresentation.DISCRETE_INVESTMENT_LOTS,
                lot_size_cents=incomplete_lots,
                backend=SolverBackendType.HIGHS_ORTOOLS,
            )
        self.assertIn("Missing explicit lot size for asset 'A2'", str(ctx.exception))

    def test_invalid_lot_sizes_rejected(self):
        """Non-positive lot sizes or lot size exceeding asset valuation must be rejected."""
        asset = make_test_asset("A1", val_kes=100_000_000)
        portfolio = Portfolio(
            portfolio_id="PORT-LOT-INV",
            tenant_org_id=str(self.tenant_id),
            assets=(asset,),
            cash_reserves_cents=100_000_000_00,
        )

        # Zero lot size
        with self.assertRaises(ValueError) as ctx1:
            MultiPeriodPortfolioOptimizer.optimize(
                portfolio=portfolio,
                candidate_assets=[asset],
                horizon_years=1,
                representation=OwnershipRepresentation.DISCRETE_INVESTMENT_LOTS,
                lot_size_cents={"A1": 0},
                backend=SolverBackendType.HIGHS_ORTOOLS,
            )
        self.assertIn("must be a positive integer cents", str(ctx1.exception))

        # Lot size > current valuation
        with self.assertRaises(ValueError) as ctx2:
            MultiPeriodPortfolioOptimizer.optimize(
                portfolio=portfolio,
                candidate_assets=[asset],
                horizon_years=1,
                representation=OwnershipRepresentation.DISCRETE_INVESTMENT_LOTS,
                lot_size_cents={"A1": 150_000_000_00},  # Exceeds KES 100M
                backend=SolverBackendType.HIGHS_ORTOOLS,
            )
        self.assertIn("cannot exceed asset current valuation", str(ctx2.exception))

    def test_incompatible_initial_holdings_rejected(self):
        """Initial holdings that are not integer multiples of lot fraction must be rejected."""
        # Initial holding 0.15 is not an integer multiple of 0.10 lot fraction
        asset = make_test_asset("A1", val_kes=100_000_000, initial_frac=0.15)
        portfolio = Portfolio(
            portfolio_id="PORT-LOT-INCOMP",
            tenant_org_id=str(self.tenant_id),
            assets=(asset,),
            cash_reserves_cents=100_000_000_00,
        )

        with self.assertRaises(ValueError) as ctx:
            MultiPeriodPortfolioOptimizer.optimize(
                portfolio=portfolio,
                candidate_assets=[asset],
                horizon_years=1,
                representation=OwnershipRepresentation.DISCRETE_INVESTMENT_LOTS,
                lot_size_cents={"A1": 10_000_000_00},  # 0.10 lot
                backend=SolverBackendType.HIGHS_ORTOOLS,
            )
        self.assertIn("not an integer multiple of lot size", str(ctx.exception))

    # =========================================================================
    # 3. P0: Multi-Period Fractional Renovation Economics
    # =========================================================================
    def test_fractional_renovation_capex_proportional_to_ownership(self):
        """An investor holding a fraction of an asset must only pay their fractional share of planned CapEx."""
        asset = make_test_asset("A_RENOV", val_kes=100_000_000, capex_kes=10_000_000, initial_frac=0.20)
        portfolio = Portfolio(
            portfolio_id="PORT-RENOV-PROP",
            tenant_org_id=str(self.tenant_id),
            assets=(asset,),
            cash_reserves_cents=50_000_000_00,
        )

        res = MultiPeriodPortfolioOptimizer.optimize(
            portfolio=portfolio,
            candidate_assets=[asset],
            horizon_years=2,
            funding_model="PROPORTIONAL_OWNERSHIP",
            backend=SolverBackendType.HIGHS_ORTOOLS,
        )
        self.assertTrue(res.is_optimal)
        for step in res.schedule:
            if step.renovation_active:
                expected_capex = int(round(step.holding_fraction * asset.planned_capex_cents))
                self.assertEqual(step.capital_expenditure_cents, expected_capex)

    def test_full_asset_renovation_funding_model(self):
        """Under FULL_ASSET model, investor pays full asset CapEx regardless of ownership fraction."""
        asset = make_test_asset("A_FULL", val_kes=100_000_000, capex_kes=10_000_000, initial_frac=0.20)
        portfolio = Portfolio(
            portfolio_id="PORT-FULL",
            tenant_org_id=str(self.tenant_id),
            assets=(asset,),
            cash_reserves_cents=50_000_000_00,
        )

        res = MultiPeriodPortfolioOptimizer.optimize(
            portfolio=portfolio,
            candidate_assets=[asset],
            horizon_years=2,
            funding_model="FULL_ASSET",
            backend=SolverBackendType.HIGHS_ORTOOLS,
        )
        self.assertTrue(res.is_optimal)
        for step in res.schedule:
            if step.renovation_active:
                self.assertEqual(step.capital_expenditure_cents, asset.planned_capex_cents)

    # =========================================================================
    # 4. P0: Solver Backend Selection and Action Reporting
    # =========================================================================
    def test_highs_scipy_rejected_in_multi_period_trajectory(self):
        """Multi-period optimization contains integer / binary decision variables and must reject HIGHS_SCIPY."""
        asset = make_test_asset("A1", val_kes=100_000_000)
        portfolio = Portfolio(
            portfolio_id="PORT-REJ-SCIPY",
            tenant_org_id=str(self.tenant_id),
            assets=(asset,),
            cash_reserves_cents=100_000_000_00,
        )

        with self.assertRaises(UnsupportedSolverConfigurationError) as ctx:
            MultiPeriodPortfolioOptimizer.optimize(
                portfolio=portfolio,
                candidate_assets=[asset],
                horizon_years=2,
                backend=SolverBackendType.HIGHS_SCIPY,
            )
        self.assertIn("HIGHS_SCIPY is a SciPy-based solver backend and does not support multi-period", str(ctx.exception))

    def test_action_reporting_accuracy_non_zero_rebalancing(self):
        """Small fractional acquisitions and disposals must be reported as BUY/SELL, never masked as HOLD."""
        asset1 = make_test_asset("A1", val_kes=100_000_000, initial_frac=0.0)
        asset2 = make_test_asset("A2", val_kes=100_000_000, initial_frac=0.50)
        portfolio = Portfolio(
            portfolio_id="PORT-ACT-REP",
            tenant_org_id=str(self.tenant_id),
            assets=(asset1, asset2),
            cash_reserves_cents=20_000_000_00,
        )

        res = MultiPeriodPortfolioOptimizer.optimize(
            portfolio=portfolio,
            candidate_assets=[asset1, asset2],
            horizon_years=2,
            backend=SolverBackendType.HIGHS_ORTOOLS,
        )
        self.assertTrue(res.is_optimal)
        for step in res.schedule:
            if step.acquisition_fraction > 1e-4:
                self.assertIn(DecisionAction.BUY, step.actions)
                self.assertEqual(step.action, DecisionAction.BUY)
            if step.disposal_fraction > 1e-4:
                self.assertIn(DecisionAction.SELL, step.actions)
                self.assertEqual(step.action, DecisionAction.SELL)

    # =========================================================================
    # 5. P0: Epistemic Governance and Statistical Uncertainty
    # =========================================================================
    def test_designed_stress_scenarios_epistemic_integrity(self):
        """Designed stress scenarios must report dispersion without manufacturing formal statistical CIs."""
        engine = ScenarioStressEngine()
        assets = [make_test_asset("A1"), make_test_asset("A2")]
        portfolio = Portfolio(
            portfolio_id="PORT-DISP",
            tenant_org_id=str(self.tenant_id),
            assets=tuple(assets),
            cash_reserves_cents=10_000_000_00,
        )

        canonical = engine.get_canonical_scenarios()
        returns, probs, _ = engine.evaluate_portfolio_scenario_returns(assets, canonical)
        constraints = PortfolioConstraints(max_single_asset_weight=0.70)

        opt_res = RockafellarMeanCVaROptimizer.optimize(
            assets=assets,
            scenario_returns=returns,
            scenario_probabilities=probs,
            constraints=constraints,
        )

        env = InvestmentDecisionGovernanceGate.create_recommendation_envelope(
            portfolio=portfolio,
            solver_result=opt_res,
            caller=self.caller,
            scenario_returns=returns,
            scenario_probabilities=probs,
            scenarios=canonical,
        )

        # Check epistemic status
        self.assertGreater(env.payload.portfolio_volatility, 0.0)
        self.assertEqual(env.uncertainty.interval_type, "UNAVAILABLE_DESIGNED_STRESS_SCENARIOS")
        self.assertEqual(env.uncertainty.confidence_level, 0.0)
        self.assertAlmostEqual(env.uncertainty.lower_bound, opt_res.expected_return, places=5)
        self.assertAlmostEqual(env.uncertainty.upper_bound, opt_res.expected_return, places=5)
        self.assertFalse(math.isnan(env.uncertainty.lower_bound))
        self.assertFalse(math.isnan(env.uncertainty.upper_bound))

    def test_missing_scenario_uncertainty_sentinel(self):
        """When scenarios are missing, portfolio_volatility is -1.0 sentinel and confidence_level is 0.0."""
        dummy_opt = SolverExecutionResult(
            solver_type=SolverBackendType.HIGHS_SCIPY,
            solver_version="HiGHS 1.5",
            status=OptimizationStatus.OPTIMAL,
            is_optimal=True,
            optimal_weights=np.array([1.0]),
            expected_return=0.12,
            cvar_alpha=0.04,
            var_alpha=0.03,
            objective_value=0.02,
            optimality_gap=0.0,
            solve_time_seconds=0.01,
            active_constraints=(),
        )
        portfolio = Portfolio(
            portfolio_id="PORT-MISS-SCEN",
            tenant_org_id=str(self.tenant_id),
            assets=(make_test_asset("A1"),),
            cash_reserves_cents=10_000_000_00,
        )

        env = InvestmentDecisionGovernanceGate.create_recommendation_envelope(
            portfolio=portfolio,
            solver_result=dummy_opt,
            caller=self.caller,
            scenario_returns=None,
            scenario_probabilities=None,
        )

        self.assertEqual(env.payload.portfolio_volatility, -1.0)
        self.assertEqual(env.uncertainty.interval_type, "UNAVAILABLE_MISSING_SCENARIOS")
        self.assertEqual(env.uncertainty.confidence_level, 0.0)
        self.assertEqual(env.uncertainty.lower_bound, dummy_opt.expected_return)
        self.assertEqual(env.uncertainty.upper_bound, dummy_opt.expected_return)

    # =========================================================================
    # 6. P0: Deterministic Cryptographic Canonical Provenance Hash
    # =========================================================================
    def test_canonical_provenance_hash_sensitivity(self):
        """Modifying any financial, scenario, or assumption parameter alters the canonical provenance hash."""
        dummy_opt = SolverExecutionResult(
            solver_type=SolverBackendType.HIGHS_SCIPY,
            solver_version="HiGHS 1.5",
            status=OptimizationStatus.OPTIMAL,
            is_optimal=True,
            optimal_weights=np.array([1.0]),
            expected_return=0.12,
            cvar_alpha=0.04,
            var_alpha=0.03,
            objective_value=0.02,
            optimality_gap=0.0,
            solve_time_seconds=0.01,
            active_constraints=(),
        )
        asset_base = make_test_asset("A1", val_kes=100_000_000, rent_kes=800_000)
        port_base = Portfolio(
            portfolio_id="PORT-SENS-01",
            tenant_org_id=str(self.tenant_id),
            assets=(asset_base,),
            cash_reserves_cents=10_000_000_00,
        )

        env1 = InvestmentDecisionGovernanceGate.create_recommendation_envelope(
            portfolio=port_base,
            solver_result=dummy_opt,
            caller=self.caller,
        )
        h1 = env1.audit_event.provenance_hash_sha256

        # Change asset rent
        asset_mod_rent = make_test_asset("A1", val_kes=100_000_000, rent_kes=900_000)
        port_mod_rent = Portfolio(
            portfolio_id="PORT-SENS-01",
            tenant_org_id=str(self.tenant_id),
            assets=(asset_mod_rent,),
            cash_reserves_cents=10_000_000_00,
        )
        env2 = InvestmentDecisionGovernanceGate.create_recommendation_envelope(
            portfolio=port_mod_rent,
            solver_result=dummy_opt,
            caller=self.caller,
        )
        h2 = env2.audit_event.provenance_hash_sha256
        self.assertNotEqual(h1, h2)

        # Result hash is distinct from provenance hash
        self.assertNotEqual(env1.audit_event.result_hash_sha256, h1)

    # =========================================================================
    # 7. P0: Scenario Probability Handling and Matrix Validation
    # =========================================================================
    def test_scenario_probabilities_not_summing_to_one_rejected(self):
        """Scenario probabilities that do not sum to 1.0 must be rejected (no silent normalization)."""
        engine = ScenarioStressEngine()
        assets = [make_test_asset("A1")]
        # Create scenarios whose probabilities sum to 0.85
        scenarios = [
            ScenarioDefinition(
                scenario_id="S1",
                name="S1",
                provenance=ScenarioProvenanceKind.EXPERT_ASSUMPTION,
                source="Expert",
                observation_or_forecast_period="2026",
                probability=0.50,
                model_version="1.0",
                transformation_methodology="Test",
            ),
            ScenarioDefinition(
                scenario_id="S2",
                name="S2",
                provenance=ScenarioProvenanceKind.EXPERT_ASSUMPTION,
                source="Expert",
                observation_or_forecast_period="2026",
                probability=0.35,  # Sum = 0.85 != 1.0
                model_version="1.0",
                transformation_methodology="Test",
            ),
        ]
        with self.assertRaises(ValueError) as ctx:
            engine.evaluate_portfolio_scenario_returns(assets, scenarios)
        self.assertIn("must sum to 1.0", str(ctx.exception))

    def test_negative_or_non_finite_probabilities_rejected(self):
        """Negative, NaN, or Inf probabilities must be rejected."""
        returns = np.array([[0.10, 0.05]])
        with self.assertRaises(ValueError) as ctx1:
            validate_scenario_distribution(returns, np.array([-0.1, 1.1]))
        self.assertIn("non-negative", str(ctx1.exception).lower())

        with self.assertRaises(ValueError) as ctx2:
            validate_scenario_distribution(returns, np.array([float("nan"), 0.5]))
        self.assertIn("finite", str(ctx2.exception).lower())

    def test_asset_count_mismatch_with_scenario_matrix_rejected(self):
        """Return matrix row count mismatch against asset count must be rejected."""
        assets = [make_test_asset("A1"), make_test_asset("A2")]  # 2 assets
        returns = np.array([[0.10, 0.05]])  # 1 row
        probs = np.array([0.5, 0.5])
        constraints = PortfolioConstraints()

        with self.assertRaises(ValueError) as ctx:
            RockafellarMeanCVaROptimizer.optimize(
                assets=assets,
                scenario_returns=returns,
                scenario_probabilities=probs,
                constraints=constraints,
            )
        self.assertIn("does not match asset count", str(ctx.exception))

    # =========================================================================
    # 8. P0: Objective Parameter Validation and Epsilon Constraint
    # =========================================================================
    def test_rockafellar_cvar_parameter_validation(self):
        """Optimizer must validate alpha in (0, 1), lambda in [0, 1], and time_limit > 0."""
        assets = [make_test_asset("A1")]
        returns = np.array([[0.10, 0.05]])
        probs = np.array([0.5, 0.5])
        constraints = PortfolioConstraints()

        # Invalid alpha
        with self.assertRaises(ValueError) as ctx1:
            RockafellarMeanCVaROptimizer.optimize(
                assets=assets,
                scenario_returns=returns,
                scenario_probabilities=probs,
                constraints=constraints,
                alpha=1.0,
            )
        self.assertIn("alpha must be strictly in (0, 1)", str(ctx1.exception))

        # Invalid lambda
        with self.assertRaises(ValueError) as ctx2:
            RockafellarMeanCVaROptimizer.optimize(
                assets=assets,
                scenario_returns=returns,
                scenario_probabilities=probs,
                constraints=constraints,
                risk_aversion_lambda=-0.1,
            )
        self.assertIn("risk_aversion_lambda must be a finite float in [0, 1]", str(ctx2.exception))

        # Invalid time limit
        with self.assertRaises(ValueError) as ctx3:
            RockafellarMeanCVaROptimizer.optimize(
                assets=assets,
                scenario_returns=returns,
                scenario_probabilities=probs,
                constraints=constraints,
                time_limit_seconds=0.0,
            )
        self.assertIn("time_limit_seconds must be a finite positive float", str(ctx3.exception))

    def test_epsilon_constraint_objective_implementation(self):
        """OptimizationObjective.EPSILON_CONSTRAINT must be implemented and reject missing cvar_budget."""
        assets = [make_test_asset("A1"), make_test_asset("A2")]
        returns = np.array([
            [0.15, -0.05, 0.08],
            [0.08, 0.02, 0.06],
        ])
        probs = np.array([0.3, 0.3, 0.4])
        constraints = PortfolioConstraints(max_single_asset_weight=0.70)

        # Missing cvar_budget raises ValueError
        with self.assertRaises(ValueError) as ctx:
            RockafellarMeanCVaROptimizer.optimize(
                assets=assets,
                scenario_returns=returns,
                scenario_probabilities=probs,
                constraints=constraints,
                objective=OptimizationObjective.EPSILON_CONSTRAINT,
                cvar_budget=None,
            )
        self.assertIn("requires explicit finite cvar_budget", str(ctx.exception))

        # Valid cvar_budget solves correctly
        res = RockafellarMeanCVaROptimizer.optimize(
            assets=assets,
            scenario_returns=returns,
            scenario_probabilities=probs,
            constraints=constraints,
            objective=OptimizationObjective.EPSILON_CONSTRAINT,
            cvar_budget=0.05,
        )
        self.assertTrue(res.is_optimal)
        self.assertLessEqual(res.cvar_alpha, 0.05 + 1e-4)

    # =========================================================================
    # 9. P0: Genuinely Unconstrained Frontier Endpoints and Convexity
    # =========================================================================
    def test_frontier_unconstrained_endpoints_without_sentinels(self):
        """Frontier exploration uses genuinely unconstrained LP endpoints without -1e9/1e9 sentinels."""
        assets = [
            make_test_asset("A1", val_kes=100_000_000, rent_kes=900_000),
            make_test_asset("A2", val_kes=80_000_000, rent_kes=500_000),
            make_test_asset("A3", val_kes=120_000_000, rent_kes=700_000),
        ]
        engine = ScenarioStressEngine()
        canonical = engine.get_canonical_scenarios()
        returns, probs, _ = engine.evaluate_portfolio_scenario_returns(assets, canonical)
        constraints = PortfolioConstraints()

        res = EfficientFrontierExplorer.trace_epsilon_constraint_frontier(
            assets=assets,
            scenario_returns=returns,
            scenario_probabilities=probs,
            constraints=constraints,
            n_points=5,
        )

        self.assertGreaterEqual(len(res.points), 2)
        # All points must have verified empirical CVaR and return
        for p in res.points:
            self.assertGreater(p.expected_return, -1.0)
            self.assertLess(p.expected_return, 1.0)
            self.assertGreaterEqual(p.cvar_alpha, -1.0)

        # Infeasible / empty points check
        self.assertIn("discrete sampled efficient frontier", res.summary_notes.lower())

    def test_convexity_verification_minimum_points(self):
        """_verify_convexity must return False if fewer than 3 efficient points exist."""
        explorer = EfficientFrontierExplorer()
        self.assertFalse(explorer._verify_convexity([]))
        self.assertFalse(explorer._verify_convexity([(0.05, 0.08)]))
        self.assertFalse(explorer._verify_convexity([(0.05, 0.08), (0.06, 0.09)]))
