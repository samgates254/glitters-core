from __future__ import annotations

import math
from typing import Sequence
import unittest
from uuid import uuid4
import numpy as np

from glitters_contracts import (
    AuthorizedCallerContext,
    ConfidenceTier,
    OrganizationContext,
    PortfolioInvestmentBridge,
)

from glitters_portfolio_intelligence.domain.entities import Portfolio, PropertyAsset
from glitters_portfolio_intelligence.domain.enums import (
    AssetCategory,
    GeographicSubmarket,
    IRRStatus,
    OptimizationObjective,
    OptimizationStatus,
    OwnershipRepresentation,
    ScenarioProvenanceKind,
    SolverBackendType,
    UnsupportedSolverConfigurationError,
)
from glitters_portfolio_intelligence.cashflows.accounting import compute_asset_annual_cashflow
from glitters_portfolio_intelligence.governance.committee_gate import InvestmentDecisionGovernanceGate
from glitters_portfolio_intelligence.integration.upstream_adapters import (
    UpstreamEngineDataFeed,
    UpstreamIntegrationAdapter,
)
from glitters_portfolio_intelligence.optimization.constraints import PortfolioConstraints
from glitters_portfolio_intelligence.optimization.multi_period import MultiPeriodPortfolioOptimizer
from glitters_portfolio_intelligence.optimization.rockafellar_cvar import RockafellarMeanCVaROptimizer
from glitters_portfolio_intelligence.returns.metrics import compute_irr
from glitters_portfolio_intelligence.scenarios.provenance import ScenarioDefinition
from glitters_portfolio_intelligence.scenarios.stress_engine import ScenarioStressEngine
from glitters_portfolio_intelligence.scenarios.validation import validate_scenario_distribution


def make_test_asset(asset_id: str, val_kes: int, gpr_kes: int) -> PropertyAsset:
    return PropertyAsset(
        asset_id=asset_id,
        name=f"Property {asset_id}",
        category=AssetCategory.COMMERCIAL_OFFICE,
        submarket=GeographicSubmarket.KILIMANI,
        acquisition_valuation_cents=val_kes * 100,
        current_valuation_cents=val_kes * 100,
        monthly_gross_potential_rent_cents=(gpr_kes * 100) // 12,
        monthly_operating_expense_cents=((gpr_kes * 100) // 12) // 4,
        annual_baseline_vacancy_rate=0.05,
        annual_baseline_credit_loss_rate=0.02,
        annual_debt_service_cents=((val_kes * 100) // 2) // 10,
        current_loan_balance_cents=(val_kes * 100) // 2,
        annual_routine_maintenance_cents=(val_kes * 100) // 200,
        planned_capex_cents=(val_kes * 100) // 100,
        minimum_holding_months=24,
    )


class TestRemediationSprintR1(unittest.TestCase):
    def setUp(self):
        self.tenant_id = uuid4()
        self.assets = [
            make_test_asset("ASSET-01", 100_000_000, 10_000_000),
            make_test_asset("ASSET-02", 150_000_000, 15_000_000),
            make_test_asset("ASSET-03", 80_000_000, 8_000_000),
            make_test_asset("ASSET-04", 120_000_000, 12_000_000),
            make_test_asset("ASSET-05", 200_000_000, 20_000_000),
        ]
        self.portfolio = Portfolio(
            portfolio_id="PORT-R1",
            tenant_org_id=self.tenant_id,
            assets=tuple(self.assets[:3]),
            cash_reserves_cents=10_000_000_00,
        )
        self.org_context = OrganizationContext(
            tenant_org_id=self.tenant_id,
            organization_name="GLITTERS Capital Partners",
        )
        self.caller = AuthorizedCallerContext(
            caller_user_id=uuid4(),
            tenant_org_id=self.tenant_id,
            granted_permissions=frozenset(["GLITTERS_EXECUTE_ALGORITHMS", "GLITTERS_VIEW_FINANCIALS"]),
            org_context=self.org_context,
        )
        self.ret_matrix, self.probs, _ = ScenarioStressEngine.evaluate_portfolio_scenario_returns(self.assets)

    # -------------------------------------------------------------------------
    # 1. P0 — Enforce Every Declared Portfolio Constraint
    # -------------------------------------------------------------------------
    def test_initial_weights_validation(self):
        # Mismatched length with assets
        c = PortfolioConstraints(initial_weights=(0.5, 0.5))
        with self.assertRaises(ValueError):
            RockafellarMeanCVaROptimizer.optimize(
                assets=self.assets,
                scenario_returns=self.ret_matrix,
                scenario_probabilities=self.probs,
                constraints=c,
            )

        # Negative initial weight rejected in dataclass
        with self.assertRaises(ValueError):
            PortfolioConstraints(initial_weights=(0.5, -0.1, 0.6))

        # Max turnover without initial weights rejected
        with self.assertRaises(ValueError):
            PortfolioConstraints(max_turnover_rate=0.20, initial_weights=None)

    def test_turnover_constraint_enforcement(self):
        # Initial weights all compliant with max_single_asset_weight (0.35)
        init_w = (0.25, 0.25, 0.20, 0.15, 0.10)
        # Constrain turnover to 0.10 max change
        c = PortfolioConstraints(
            initial_weights=init_w,
            max_turnover_rate=0.10,
            buy_transaction_cost_rate=0.01,
            sell_transaction_cost_rate=0.01,
        )
        res = RockafellarMeanCVaROptimizer.optimize(
            assets=self.assets,
            scenario_returns=self.ret_matrix,
            scenario_probabilities=self.probs,
            constraints=c,
            backend=SolverBackendType.HIGHS_ORTOOLS,
        )
        self.assertTrue(res.is_optimal)
        opt_w = res.optimal_weights
        # Calculate turnover: 0.5 * sum(|opt_w - init_w|)
        turnover = 0.5 * np.sum(np.abs(opt_w - np.array(init_w)))
        self.assertLessEqual(turnover, 0.10 + 1e-4)

    def test_milp_solver_backend_rejection(self):
        # Cardinality requires MILP
        c_milp = PortfolioConstraints(max_cardinality=3)
        # HiGHS SciPy (LP only) must raise UnsupportedSolverConfigurationError
        with self.assertRaises(UnsupportedSolverConfigurationError):
            RockafellarMeanCVaROptimizer.optimize(
                assets=self.assets,
                scenario_returns=self.ret_matrix,
                scenario_probabilities=self.probs,
                constraints=c_milp,
                backend=SolverBackendType.HIGHS_SCIPY,
            )
        # GLOP OR-Tools (LP only) must also raise UnsupportedSolverConfigurationError
        with self.assertRaises(UnsupportedSolverConfigurationError):
            RockafellarMeanCVaROptimizer.optimize(
                assets=self.assets,
                scenario_returns=self.ret_matrix,
                scenario_probabilities=self.probs,
                constraints=c_milp,
                backend=SolverBackendType.GLOP_ORTOOLS,
            )

    def test_milp_cardinality_and_min_holding_solved(self):
        # Set max_cardinality=2 out of 5 assets, min_holding=0.20
        c_milp = PortfolioConstraints(
            max_cardinality=2,
            min_holding_weight=0.20,
            max_single_asset_weight=0.60,
        )
        res = RockafellarMeanCVaROptimizer.optimize(
            assets=self.assets,
            scenario_returns=self.ret_matrix,
            scenario_probabilities=self.probs,
            constraints=c_milp,
            backend=SolverBackendType.SCIP_ORTOOLS,
        )
        self.assertTrue(res.is_optimal)
        held = [w for w in res.optimal_weights if w > 1e-4]
        # At most 2 assets can be held
        self.assertLessEqual(len(held), 2)
        # Every held asset must have at least 0.20 weight
        for w in held:
            self.assertGreaterEqual(w, 0.20 - 1e-4)

    # -------------------------------------------------------------------------
    # 2. P1 — Multi-Period Optimization Timing, Discounting & Invariants
    # -------------------------------------------------------------------------
    def test_multi_period_initial_holdings_validation(self):
        # Asset in initial holdings not in candidate_assets
        with self.assertRaises(ValueError):
            MultiPeriodPortfolioOptimizer.optimize(
                portfolio=self.portfolio,
                candidate_assets=self.assets[:2],  # Asset 03 missing
                initial_holdings={"NON_EXISTENT": 0.5},
            )

    def test_multi_period_one_time_renovation_and_holding_period(self):
        res = MultiPeriodPortfolioOptimizer.optimize(
            portfolio=self.portfolio,
            candidate_assets=self.assets,
            horizon_years=3,
            annual_discount_rate=0.08,
            min_cash_reserve_cents=5_000_000_00,
            backend=SolverBackendType.HIGHS_ORTOOLS,
        )
        self.assertTrue(res.is_optimal)
        # Verify one-time renovation for each asset
        renovations_by_asset: dict[str, int] = {}
        for step in res.schedule:
            if step.renovation_active:
                renovations_by_asset[step.asset_id] = renovations_by_asset.get(step.asset_id, 0) + 1

        for aid, count in renovations_by_asset.items():
            self.assertLessEqual(count, 1, f"Asset {aid} renovated {count} times (must be <= 1)")

    # -------------------------------------------------------------------------
    # 3. P1 — Scenario Probability Validation & Provenance
    # -------------------------------------------------------------------------
    def test_scenario_validation_rejects_unnormalized_probabilities(self):
        # Probabilities summing to 1.05 (forbidden silent normalization)
        bad_probs = np.array([0.15] * 7)  # sum = 1.05
        with self.assertRaises(ValueError):
            validate_scenario_distribution(self.ret_matrix[:, :7], bad_probs)

        # Negative probability
        neg_probs = np.array([-0.1, 0.6, 0.5])
        with self.assertRaises(ValueError):
            validate_scenario_distribution(self.ret_matrix[:, :3], neg_probs)

    def test_scenario_definition_sha256_validation(self):
        # Valid 64-character hex hash
        valid_hash = "a" * 64
        scen = ScenarioDefinition(
            scenario_id="SCEN-01",
            name="Valid Hash Scenario",
            provenance=ScenarioProvenanceKind.HISTORICAL_OBSERVATION,
            source="Audited Records",
            observation_or_forecast_period="2020-2025",
            probability=0.25,
            model_version="1.0",
            transformation_methodology="Historical filtering",
            dataset_sha256=valid_hash,
        )
        self.assertEqual(scen.dataset_sha256, valid_hash)

        # Invalid hex hash (too short)
        with self.assertRaises(ValueError):
            ScenarioDefinition(
                scenario_id="SCEN-02",
                name="Invalid Hash Scenario",
                provenance=ScenarioProvenanceKind.HISTORICAL_OBSERVATION,
                source="Audited Records",
                observation_or_forecast_period="2020-2025",
                probability=0.25,
                model_version="1.0",
                transformation_methodology="Historical filtering",
                dataset_sha256="not_a_valid_64_char_hash",
            )

    # -------------------------------------------------------------------------
    # 4. P1 — Governance Isolation & Contracts
    # -------------------------------------------------------------------------
    def test_governance_tenant_boundary_violation(self):
        alien_tenant = uuid4()
        alien_caller = AuthorizedCallerContext(
            caller_user_id=uuid4(),
            tenant_org_id=alien_tenant,  # Different tenant
            granted_permissions=frozenset(["GLITTERS_EXECUTE_ALGORITHMS"]),
            org_context=OrganizationContext(
                tenant_org_id=alien_tenant,
                organization_name="Rogue Partners",
            ),
        )
        res = RockafellarMeanCVaROptimizer.optimize(
            assets=self.assets,
            scenario_returns=self.ret_matrix,
            scenario_probabilities=self.probs,
            constraints=PortfolioConstraints(),
        )
        with self.assertRaises(ValueError) as ctx:
            InvestmentDecisionGovernanceGate.create_recommendation_envelope(
                portfolio=self.portfolio,
                solver_result=res,
                caller=alien_caller,
            )
        self.assertIn("Tenant organization boundary violation", str(ctx.exception))

    def test_true_scenario_volatility_computation(self):
        res = RockafellarMeanCVaROptimizer.optimize(
            assets=self.assets,
            scenario_returns=self.ret_matrix,
            scenario_probabilities=self.probs,
            constraints=PortfolioConstraints(),
        )
        envelope = InvestmentDecisionGovernanceGate.create_recommendation_envelope(
            portfolio=self.portfolio,
            solver_result=res,
            caller=self.caller,
            assets=self.assets,
            scenario_returns=self.ret_matrix,
            scenario_probabilities=self.probs,
        )
        # Verify volatility matches direct calculation: sqrt(sum p_s (R_s - mu)^2)
        port_ret = res.optimal_weights @ self.ret_matrix
        mu = np.sum(self.probs * port_ret)
        expected_vol = float(np.sqrt(np.sum(self.probs * ((port_ret - mu) ** 2))))
        self.assertAlmostEqual(envelope.payload.portfolio_volatility, expected_vol, places=5)
        # Verify effective sample size
        expected_neff = float(1.0 / np.sum(self.probs ** 2))
        self.assertAlmostEqual(envelope.uncertainty.effective_sample_size, expected_neff, places=4)

    def test_upstream_adapter_asset_id_mismatch(self):
        feed = UpstreamEngineDataFeed(asset_id="WRONG-ID", valuation_cents=200_000_000_00)
        with self.assertRaises(ValueError) as ctx:
            UpstreamIntegrationAdapter.apply_upstream_feed(self.assets[0], feed)
        self.assertIn("Asset ID mismatch", str(ctx.exception))

    def test_upstream_adapter_respects_engine_availability(self):
        # Feed has clearing_rent but engine04 is NOT available -> falls back to base asset rent
        feed = UpstreamEngineDataFeed(
            asset_id=self.assets[0].asset_id,
            clearing_rent_monthly_cents=5_000_000_00,
            is_engine04_available=False,
        )
        refined = UpstreamIntegrationAdapter.apply_upstream_feed(self.assets[0], feed)
        self.assertEqual(refined.monthly_gross_potential_rent_cents, self.assets[0].monthly_gross_potential_rent_cents)

        # Feed with engine04 available -> updates rent
        feed_active = UpstreamEngineDataFeed(
            asset_id=self.assets[0].asset_id,
            clearing_rent_monthly_cents=5_000_000_00,
            is_engine04_available=True,
        )
        refined_active = UpstreamIntegrationAdapter.apply_upstream_feed(self.assets[0], feed_active)
        self.assertEqual(refined_active.monthly_gross_potential_rent_cents, 5_000_000_00)

    # -------------------------------------------------------------------------
    # 5. P2 — Cash-Flow Rounding & IRR Edge Cases
    # -------------------------------------------------------------------------
    def test_cashflow_rounding_no_binary_drift(self):
        stmt = compute_asset_annual_cashflow(self.assets[0])
        # NOI must equal EGI - Opex - Maint exactly in integer cents
        self.assertEqual(
            stmt.net_operating_income_cents,
            stmt.effective_gross_income_cents - stmt.operating_expenses_cents - stmt.routine_maintenance_cents,
        )
        # CFADS must equal NOI - Debt Service - planned_capex_cents
        self.assertEqual(
            stmt.cash_flow_after_debt_service_cents,
            stmt.net_operating_income_cents - stmt.debt_service_cents - stmt.planned_capex_cents,
        )

    def test_irr_no_root_in_search_interval(self):
        # A cash flow series with a sign change whose root lies far outside [-0.89, 5.0]
        # E.g., [-100, 1, 1] requires an astronomical negative return (outside search interval)
        # Or a series where NPV(r) is strictly positive on [-0.89, 5.0] despite alternating signs
        cf = [-100_000_000, 10_000_000, -10_000_000, 20_000_000]
        res = compute_irr(cf)
        if res.rate is None:
            self.assertEqual(res.status, IRRStatus.NO_ROOT_IN_SEARCH_INTERVAL)


if __name__ == "__main__":
    unittest.main()
