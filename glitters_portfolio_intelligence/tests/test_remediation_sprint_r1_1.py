from __future__ import annotations

import math
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
    GeographicSubmarket,
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
from glitters_portfolio_intelligence.optimization.constraints import (
    PortfolioConstraints,
    build_linear_constraint_matrices,
)
from glitters_portfolio_intelligence.optimization.frontier import EfficientFrontierExplorer
from glitters_portfolio_intelligence.optimization.multi_period import MultiPeriodPortfolioOptimizer
from glitters_portfolio_intelligence.optimization.rockafellar_cvar import RockafellarMeanCVaROptimizer
from glitters_portfolio_intelligence.optimization.solver_backends import (
    PortfolioSolverBackend,
    SolverExecutionResult,
)
from glitters_portfolio_intelligence.scenarios.cvar_verifier import compute_rockafellar_cvar_1d
from glitters_portfolio_intelligence.scenarios.provenance import ScenarioDefinition
from glitters_portfolio_intelligence.scenarios.stress_engine import ScenarioStressEngine
from glitters_portfolio_intelligence.scenarios.validation import validate_scenario_distribution


def make_asset(
    asset_id: str,
    val_kes: int = 100_000_000,
    gpr_kes: int = 12_000_000,
    capex_kes: int = 5_000_000,
    maint_kes: int = 500_000,
    debt_service_kes: int = 3_000_000,
    submarket: GeographicSubmarket = GeographicSubmarket.WESTLANDS,
    category: AssetCategory = AssetCategory.COMMERCIAL_OFFICE,
    min_holding_months: int = 24,
) -> PropertyAsset:
    return PropertyAsset(
        asset_id=asset_id,
        name=f"Property {asset_id}",
        category=category,
        submarket=submarket,
        acquisition_valuation_cents=val_kes * 100,
        current_valuation_cents=val_kes * 100,
        monthly_gross_potential_rent_cents=(gpr_kes * 100) // 12,
        monthly_operating_expense_cents=((gpr_kes * 100) // 12) // 4,
        annual_baseline_vacancy_rate=0.06,
        annual_baseline_credit_loss_rate=0.02,
        annual_debt_service_cents=debt_service_kes * 100,
        current_loan_balance_cents=(val_kes * 100) // 2,
        annual_routine_maintenance_cents=maint_kes * 100,
        planned_capex_cents=capex_kes * 100,
        renovation_downtime_months=6,
        minimum_holding_months=min_holding_months,
    )


class TestRemediationSprintR1_1(unittest.TestCase):
    def setUp(self):
        self.tenant_id = uuid4()
        self.org_context = OrganizationContext(
            tenant_org_id=self.tenant_id,
            organization_name="Sidian Real Estate Holdings",
        )
        self.caller = AuthorizedCallerContext(
            caller_user_id=uuid4(),
            tenant_org_id=self.tenant_id,
            granted_permissions=frozenset(["GLITTERS_EXECUTE_ALGORITHMS", "GLITTERS_VIEW_FINANCIALS"]),
            org_context=self.org_context,
        )

    # 1. Zero CapEx budget enforcement
    def test_zero_capex_budget_enforcement(self):
        asset_with_capex = make_asset("CAPEX-01", capex_kes=2_000_000)
        constraints = PortfolioConstraints(
            max_capex_budget_cents=0,  # Zero CapEx explicitly enforced!
            min_cash_reserve_weight=0.0,
            max_single_asset_weight=1.0,
        )

        assets = [asset_with_capex]
        ret_matrix = np.array([[0.10, 0.12]])
        probs = np.array([0.5, 0.5])

        # A portfolio requiring positive CapEx cannot satisfy a 0 CapEx budget when forced to invest
        # If we demand a return >= 0.10, it must invest in the asset, violating the 0 budget
        res = RockafellarMeanCVaROptimizer.optimize(
            assets=assets,
            scenario_returns=ret_matrix,
            scenario_probabilities=probs,
            constraints=constraints,
            objective=OptimizationObjective.MINIMIZE_CVAR_TARGET_RETURN,
            target_return=0.10,
        )
        self.assertFalse(res.is_optimal)
        self.assertEqual(res.status, OptimizationStatus.INFEASIBLE)

    # 2. Constraint validation rejects NaN, inf, and invalid parameters
    def test_constraint_validation_rejects_nan_inf_invalid(self):
        invalid_cases = [
            {"min_cash_reserve_weight": float("nan")},
            {"min_cash_reserve_weight": 1.5},
            {"max_single_asset_weight": -0.1},
            {"max_portfolio_ltv": float("inf")},
            {"min_portfolio_dscr": -1.0},
            {"max_capex_budget_cents": -500},
            {"max_turnover_rate": float("nan")},
            {"max_turnover_rate": 2.5},
            {"buy_transaction_cost_rate": -0.01},
            {"sell_transaction_cost_rate": float("nan")},
            {"min_holding_weight": 1.2},
            {"max_cardinality": 0},
            {"max_geographic_weights": {GeographicSubmarket.WESTLANDS: float("nan")}},
            {"max_category_weights": {AssetCategory.RESIDENTIAL: -0.2}},
            {"initial_weights": (0.5, float("nan"))},
            {"initial_weights": (0.5, -0.2)},
        ]
        for kwargs in invalid_cases:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    PortfolioConstraints(**kwargs)

    # 3. Fee consistency across all three objective modes
    def test_fee_consistency_across_all_three_objective_modes(self):
        a1 = make_asset("A1", capex_kes=0)
        a2 = make_asset("A2", capex_kes=0)
        assets = [a1, a2]
        ret_matrix = np.array([
            [0.10, 0.12, 0.08, 0.15],
            [0.14, 0.06, 0.11, 0.09],
        ])
        probs = np.array([0.25, 0.25, 0.25, 0.25])

        w0 = (1.0, 0.0)
        c_buy = 0.015
        c_sell = 0.010
        constraints = PortfolioConstraints(
            initial_weights=w0,
            buy_transaction_cost_rate=c_buy,
            sell_transaction_cost_rate=c_sell,
            max_turnover_rate=1.0,
            min_cash_reserve_weight=0.0,
            max_single_asset_weight=1.0,
        )

        modes = [
            (OptimizationObjective.MINIMIZE_CVAR_TARGET_RETURN, {"target_return": 0.08}),
            (OptimizationObjective.MAXIMIZE_RETURN_CVAR_BUDGET, {"cvar_budget": 0.05}),
            (OptimizationObjective.SCALARIZED_RISK_RETURN, {"risk_aversion_lambda": 0.5}),
        ]

        for mode, extra_kwargs in modes:
            with self.subTest(mode=mode.value):
                res = RockafellarMeanCVaROptimizer.optimize(
                    assets=assets,
                    scenario_returns=ret_matrix,
                    scenario_probabilities=probs,
                    constraints=constraints,
                    objective=mode,
                    **extra_kwargs,
                )
                self.assertTrue(res.is_optimal)
                w = res.optimal_weights
                # Calculate deterministic fee independently
                d_plus = np.maximum(0.0, w - np.array(w0))
                d_minus = np.maximum(0.0, np.array(w0) - w)
                tx_cost = float(np.sum(c_buy * d_plus + c_sell * d_minus))

                expected_gross = float(np.mean(w @ ret_matrix))
                expected_net = expected_gross - tx_cost
                self.assertAlmostEqual(res.expected_return, expected_net, places=4)

                # Net losses reflect fees: L_{s, net} = - (w @ ret[:, s]) + tx_cost
                net_losses = -(w @ ret_matrix) + tx_cost
                cvar_verif = compute_rockafellar_cvar_1d(net_losses, probs, alpha=0.95)
                self.assertAlmostEqual(res.cvar_alpha, cvar_verif.cvar, places=4)

    # 4. CVaR recomputation including transaction costs
    def test_cvar_recomputation_net_of_fees(self):
        a1 = make_asset("A1", capex_kes=0)
        assets = [a1]
        ret_matrix = np.array([[0.05, -0.10, -0.20, 0.15]])
        probs = np.array([0.40, 0.30, 0.20, 0.10])

        constraints = PortfolioConstraints(
            initial_weights=(0.0,),
            buy_transaction_cost_rate=0.02,
            sell_transaction_cost_rate=0.01,
            max_turnover_rate=1.0,
            min_cash_reserve_weight=0.0,
            max_single_asset_weight=1.0,
        )

        res = RockafellarMeanCVaROptimizer.optimize(
            assets=assets,
            scenario_returns=ret_matrix,
            scenario_probabilities=probs,
            constraints=constraints,
            objective=OptimizationObjective.SCALARIZED_RISK_RETURN,
            risk_aversion_lambda=0.5,
        )
        self.assertTrue(res.is_optimal)
        tx_fee = 0.02 * res.optimal_weights[0]
        net_losses = -(res.optimal_weights @ ret_matrix) + tx_fee
        emp = compute_rockafellar_cvar_1d(net_losses, probs, alpha=0.95)
        self.assertAlmostEqual(res.cvar_alpha, emp.cvar, places=4)

    # 5. Fractional minimum holding periods binary lock
    def test_fractional_minimum_holding_periods_binary_lock(self):
        a1 = make_asset("LOCK-01", val_kes=50_000_000, min_holding_months=24)  # 2 years lock
        portfolio = Portfolio(
            portfolio_id="PORT-LOCK",
            tenant_org_id=str(self.tenant_id),
            assets=(),
            cash_reserves_cents=100_000_000_00,
        )
        # Solve multi-period with 3-year horizon
        res = MultiPeriodPortfolioOptimizer.optimize(
            portfolio=portfolio,
            candidate_assets=[a1],
            horizon_years=3,
            min_cash_reserve_cents=10_000_000_00,
            representation=OwnershipRepresentation.FRACTIONAL_CAPITAL_WEIGHT,
        )
        self.assertTrue(res.is_optimal)

        # In period 1, if acquired, disposal cannot occur in period 1 or period 2
        p1_step = [s for s in res.schedule if s.period == 1 and s.asset_id == "LOCK-01"][0]
        p2_step = [s for s in res.schedule if s.period == 2 and s.asset_id == "LOCK-01"][0]
        if p1_step.action == p1_step.action.BUY:
            self.assertNotEqual(p2_step.action, p2_step.action.SELL)

    # 6. Existing holdings with missing acquisition ages lock disposal
    def test_existing_holdings_missing_acquisition_ages(self):
        a1 = make_asset("EXIST-01", val_kes=50_000_000, min_holding_months=24)
        portfolio = Portfolio(
            portfolio_id="PORT-EXIST",
            tenant_org_id=str(self.tenant_id),
            assets=(a1,),
            cash_reserves_cents=50_000_000_00,
        )
        # Without initial_holding_ages_months, optimizer locks disposal for H_i = ceil(24/12) = 2 periods
        res = MultiPeriodPortfolioOptimizer.optimize(
            portfolio=portfolio,
            candidate_assets=[a1],
            horizon_years=3,
            min_cash_reserve_cents=5_000_000_00,
            initial_holding_ages_months=None,  # explicitly missing/unknown!
        )
        self.assertTrue(res.is_optimal)
        p1_step = [s for s in res.schedule if s.period == 1 and s.asset_id == "EXIST-01"][0]
        p2_step = [s for s in res.schedule if s.period == 2 and s.asset_id == "EXIST-01"][0]
        self.assertNotEqual(p1_step.action, p1_step.action.SELL)
        self.assertNotEqual(p2_step.action, p2_step.action.SELL)

    # 7. Discrete investment lots implementation and rejection
    def test_discrete_investment_lots_implementation_and_rejection(self):
        a1 = make_asset("LOT-01", val_kes=10_000_000)
        portfolio = Portfolio(
            portfolio_id="PORT-LOT",
            tenant_org_id=str(self.tenant_id),
            assets=(),
            cash_reserves_cents=30_000_000_00,
        )

        # 1. Missing lot_size_cents raises UnsupportedSolverConfigurationError
        with self.assertRaises(UnsupportedSolverConfigurationError):
            MultiPeriodPortfolioOptimizer.optimize(
                portfolio=portfolio,
                candidate_assets=[a1],
                horizon_years=2,
                representation=OwnershipRepresentation.DISCRETE_INVESTMENT_LOTS,
                lot_size_cents=None,
            )

        # 2. LP solver rejects integer lot representation
        with self.assertRaises(UnsupportedSolverConfigurationError):
            MultiPeriodPortfolioOptimizer.optimize(
                portfolio=portfolio,
                candidate_assets=[a1],
                horizon_years=2,
                representation=OwnershipRepresentation.DISCRETE_INVESTMENT_LOTS,
                lot_size_cents=5_000_000_00,
                backend=SolverBackendType.GLOP_ORTOOLS,  # GLOP is LP-only
            )

        # 3. MILP solver successfully solves with discrete lots
        res = MultiPeriodPortfolioOptimizer.optimize(
            portfolio=portfolio,
            candidate_assets=[a1],
            horizon_years=2,
            representation=OwnershipRepresentation.DISCRETE_INVESTMENT_LOTS,
            lot_size_cents=5_000_000_00,
            backend=SolverBackendType.HIGHS_ORTOOLS,
        )
        self.assertTrue(res.is_optimal)

    # 8. Multi-period recurring maintenance accounting
    def test_multi_period_recurring_maintenance(self):
        # Asset with KES 1M maintenance
        maint_kes = 1_000_000
        a1 = make_asset("MAINT-01", val_kes=50_000_000, maint_kes=maint_kes, capex_kes=0)
        portfolio = Portfolio(
            portfolio_id="PORT-MAINT",
            tenant_org_id=str(self.tenant_id),
            assets=(a1,),
            cash_reserves_cents=10_000_000_00,
        )

        res = MultiPeriodPortfolioOptimizer.optimize(
            portfolio=portfolio,
            candidate_assets=[a1],
            horizon_years=1,
            min_cash_reserve_cents=1_000_000_00,
        )
        self.assertTrue(res.is_optimal)
        step = res.schedule[0]
        # Net CF must account for routine maintenance
        gpr_cents = a1.monthly_gross_potential_rent_cents * 12
        opex_cents = a1.monthly_operating_expense_cents * 12
        egi_cents = gpr_cents * (1.0 - a1.annual_baseline_vacancy_rate) * (1.0 - a1.annual_baseline_credit_loss_rate)
        expected_cf_cents = int(round(egi_cents - opex_cents - a1.annual_routine_maintenance_cents - a1.annual_debt_service_cents))
        self.assertEqual(step.cash_flow_generated_cents, expected_cf_cents)

    # 9. Negative net rent with renovation downtime does not become a cash benefit
    def test_negative_net_rent_with_renovation_downtime(self):
        # Asset with high debt service where net cash flow is negative
        a_neg = make_asset(
            "NEG-01",
            val_kes=50_000_000,
            gpr_kes=2_000_000,  # low rent
            debt_service_kes=8_000_000,  # massive debt service
            capex_kes=1_000_000,
        )
        portfolio = Portfolio(
            portfolio_id="PORT-NEG",
            tenant_org_id=str(self.tenant_id),
            assets=(a_neg,),
            cash_reserves_cents=50_000_000_00,
        )
        res = MultiPeriodPortfolioOptimizer.optimize(
            portfolio=portfolio,
            candidate_assets=[a_neg],
            horizon_years=2,
            min_cash_reserve_cents=5_000_000_00,
        )
        self.assertTrue(res.is_optimal)
        for s in res.schedule:
            # Renovation downtime must reduce cash flow, never increase it
            if s.renovation_active:
                self.assertLess(s.cash_flow_generated_cents, 0)

    # 10. Multi-period cash reconciliation
    def test_multi_period_cash_reconciliation(self):
        a1 = make_asset("REC-01", val_kes=40_000_000, capex_kes=0)
        c0 = 20_000_000_00
        portfolio = Portfolio(
            portfolio_id="PORT-REC",
            tenant_org_id=str(self.tenant_id),
            assets=(a1,),
            cash_reserves_cents=c0,
        )
        res = MultiPeriodPortfolioOptimizer.optimize(
            portfolio=portfolio,
            candidate_assets=[a1],
            horizon_years=3,
            min_cash_reserve_cents=5_000_000_00,
        )
        self.assertTrue(res.is_optimal)
        # Cash at period T reconciles with cumulative net cashflow
        c_prev = c0
        for t in range(1, 4):
            c_curr = res.periodic_cash_balances_cents[t - 1]
            cf_t = sum(s.cash_flow_generated_cents for s in res.schedule if s.period == t)
            capex_t = sum(s.capital_expenditure_cents for s in res.schedule if s.period == t)
            # Cash balance must equal previous cash + net CF - capex (with distribution >= 0)
            self.assertLessEqual(c_curr, c_prev + cf_t - capex_t + 10)
            c_prev = c_curr

    # 11. Backend selection and MILP rejection
    def test_backend_selection_and_milp_rejection(self):
        # Cardinality constraint requires MILP solver
        constraints = PortfolioConstraints(
            max_cardinality=1,
            min_cash_reserve_weight=0.0,
            max_single_asset_weight=1.0,
        )
        a1 = make_asset("M1", capex_kes=0)
        a2 = make_asset("M2", capex_kes=0)
        assets = [a1, a2]
        ret_matrix = np.array([[0.10, 0.12], [0.14, 0.08]])
        probs = np.array([0.5, 0.5])

        # GLOP does not support integer variables -> must raise UnsupportedSolverConfigurationError
        with self.assertRaises(UnsupportedSolverConfigurationError):
            RockafellarMeanCVaROptimizer.optimize(
                assets=assets,
                scenario_returns=ret_matrix,
                scenario_probabilities=probs,
                constraints=constraints,
                objective=OptimizationObjective.SCALARIZED_RISK_RETURN,
                backend=SolverBackendType.GLOP_ORTOOLS,
            )

    # 12. True epsilon-frontier endpoints
    def test_true_epsilon_frontier_endpoints(self):
        a1 = make_asset("F1", capex_kes=0)
        a2 = make_asset("F2", capex_kes=0)
        assets = [a1, a2]
        ret_matrix = np.array([
            [0.08, 0.09, 0.07],
            [0.18, 0.04, 0.02],
        ])
        probs = np.array([0.4, 0.3, 0.3])

        constraints = PortfolioConstraints(
            min_cash_reserve_weight=0.0,
            max_single_asset_weight=1.0,
        )
        frontier = EfficientFrontierExplorer.trace_epsilon_constraint_frontier(
            assets=assets,
            scenario_returns=ret_matrix,
            scenario_probabilities=probs,
            constraints=constraints,
            n_points=5,
        )
        self.assertGreaterEqual(len(frontier.points), 2)
        # Point 0 is genuine Min-CVaR anchor
        # Point N-1 is genuine Max-Return anchor
        min_cvar_pt = frontier.points[0]
        max_ret_pt = frontier.points[-1]

        for pt in frontier.points:
            self.assertGreaterEqual(pt.cvar_alpha, min_cvar_pt.cvar_alpha - 1e-6)
            self.assertLessEqual(pt.expected_return, max_ret_pt.expected_return + 1e-6)

    # 13. Frontier non-supported efficient points and convexity
    def test_frontier_non_supported_efficient_points_and_convexity(self):
        a1 = make_asset("C1", capex_kes=0)
        a2 = make_asset("C2", capex_kes=0)
        assets = [a1, a2]
        ret_matrix = np.array([
            [0.05, 0.06, 0.07],
            [0.15, 0.12, 0.10],
        ])
        probs = np.array([0.33, 0.33, 0.34])

        constraints = PortfolioConstraints(
            min_cash_reserve_weight=0.0,
            max_single_asset_weight=1.0,
        )
        frontier = EfficientFrontierExplorer.trace_epsilon_constraint_frontier(
            assets=assets,
            scenario_returns=ret_matrix,
            scenario_probabilities=probs,
            constraints=constraints,
            n_points=5,
        )
        # Verify Pareto non-domination: no point has both higher CVaR and lower return than another
        for i in range(len(frontier.points) - 1):
            p1 = frontier.points[i]
            p2 = frontier.points[i + 1]
            self.assertLessEqual(p1.cvar_alpha, p2.cvar_alpha + 1e-6)
            self.assertLessEqual(p1.expected_return, p2.expected_return + 1e-6)

    # 14. Frontier independent CVaR verification
    def test_frontier_independent_cvar_verification(self):
        a1 = make_asset("V1", capex_kes=0)
        a2 = make_asset("V2", capex_kes=0)
        assets = [a1, a2]
        ret_matrix = np.array([[0.10, 0.05], [0.08, 0.14]])
        probs = np.array([0.6, 0.4])

        constraints = PortfolioConstraints(
            min_cash_reserve_weight=0.0,
            max_single_asset_weight=1.0,
        )
        frontier = EfficientFrontierExplorer.trace_epsilon_constraint_frontier(
            assets=assets,
            scenario_returns=ret_matrix,
            scenario_probabilities=probs,
            constraints=constraints,
            n_points=3,
        )
        for pt in frontier.points:
            losses = -(pt.weights @ ret_matrix)
            emp = compute_rockafellar_cvar_1d(losses, probs, alpha=0.95)
            self.assertAlmostEqual(pt.cvar_alpha, emp.cvar, places=4)

    # 15. Confidence tier provenance gating
    def test_confidence_tier_provenance_gating(self):
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
            portfolio_id="PORT-CONF",
            tenant_org_id=str(self.tenant_id),
            assets=(make_asset("CONF-01", capex_kes=0),),
            cash_reserves_cents=10_000_000_00,
        )

        # 1. Verified historical evidence -> HIGH_CONFIDENCE
        env_high = InvestmentDecisionGovernanceGate.create_recommendation_envelope(
            portfolio=portfolio,
            solver_result=dummy_opt,
            caller=self.caller,
            provenance=ScenarioProvenanceKind.HISTORICAL_OBSERVATION,
            scenario_evidence_verified=True,
        )
        self.assertEqual(env_high.confidence_tier, ConfidenceTier.HIGH_CONFIDENCE)

        # 2. Unverified historical evidence -> MODERATE_CONFIDENCE
        env_mod = InvestmentDecisionGovernanceGate.create_recommendation_envelope(
            portfolio=portfolio,
            solver_result=dummy_opt,
            caller=self.caller,
            provenance=ScenarioProvenanceKind.HISTORICAL_OBSERVATION,
            scenario_evidence_verified=False,
        )
        self.assertEqual(env_mod.confidence_tier, ConfidenceTier.MODERATE_CONFIDENCE)

        # 3. Synthetic scenario -> WEAKLY_SUPPORTED
        env_syn = InvestmentDecisionGovernanceGate.create_recommendation_envelope(
            portfolio=portfolio,
            solver_result=dummy_opt,
            caller=self.caller,
            provenance=ScenarioProvenanceKind.SYNTHETIC_SCENARIO,
            scenario_evidence_verified=False,
        )
        self.assertEqual(env_syn.confidence_tier, ConfidenceTier.WEAKLY_SUPPORTED)

        # 4. Solver failed / not optimal -> WEAKLY_SUPPORTED
        dummy_infeasible = SolverExecutionResult(
            solver_type=SolverBackendType.HIGHS_SCIPY,
            solver_version="HiGHS 1.5",
            status=OptimizationStatus.INFEASIBLE,
            is_optimal=False,
            optimal_weights=np.array([1.0]),
            expected_return=0.0,
            cvar_alpha=0.0,
            var_alpha=0.0,
            objective_value=0.0,
            optimality_gap=0.0,
            solve_time_seconds=0.01,
            active_constraints=(),
        )
        env_inf = InvestmentDecisionGovernanceGate.create_recommendation_envelope(
            portfolio=portfolio,
            solver_result=dummy_infeasible,
            caller=self.caller,
            provenance=ScenarioProvenanceKind.HISTORICAL_OBSERVATION,
            scenario_evidence_verified=True,
        )
        self.assertEqual(env_inf.confidence_tier, ConfidenceTier.WEAKLY_SUPPORTED)

    # 16. Missing scenario uncertainty
    def test_missing_scenario_uncertainty(self):
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
            portfolio_id="PORT-UNC",
            tenant_org_id=str(self.tenant_id),
            assets=(make_asset("UNC-01", capex_kes=0),),
            cash_reserves_cents=10_000_000_00,
        )
        # Without scenario inputs, uncertainty is explicitly unavailable/unsupported, never falling back to CVaR
        env = InvestmentDecisionGovernanceGate.create_recommendation_envelope(
            portfolio=portfolio,
            solver_result=dummy_opt,
            caller=self.caller,
            scenario_returns=None,
            scenario_probabilities=None,
        )
        self.assertEqual(env.payload.portfolio_volatility, 0.0)
        self.assertTrue(math.isnan(env.uncertainty.lower_bound))
        self.assertTrue(math.isnan(env.uncertainty.upper_bound))
        self.assertEqual(env.uncertainty.interval_type, "UNAVAILABLE_MISSING_SCENARIOS")

    # 17. Time-limit, unbounded and unknown solver statuses
    def test_distinct_solver_status_mapping(self):
        portfolio = Portfolio(
            portfolio_id="PORT-STAT",
            tenant_org_id=str(self.tenant_id),
            assets=(make_asset("STAT-01", capex_kes=0),),
            cash_reserves_cents=10_000_000_00,
        )

        test_cases = [
            (OptimizationStatus.TIME_LIMIT, AlgorithmicExecutionStatus.CONFLICTING_CONSTRAINTS, AbstentionReason.SOLVER_TIMEOUT),
            (OptimizationStatus.UNBOUNDED, AlgorithmicExecutionStatus.ABSTAINED_POLICY_VIOLATION, AbstentionReason.HARD_CONSTRAINT_VIOLATION),
            (OptimizationStatus.UNKNOWN, AlgorithmicExecutionStatus.ABSTAINED_INSUFFICIENT_DATA, AbstentionReason.INSUFFICIENT_DATA),
        ]

        for opt_status, exp_exec_status, exp_reason in test_cases:
            with self.subTest(opt_status=opt_status.value):
                dummy = SolverExecutionResult(
                    solver_type=SolverBackendType.HIGHS_SCIPY,
                    solver_version="HiGHS 1.5",
                    status=opt_status,
                    is_optimal=False,
                    optimal_weights=np.array([1.0]),
                    expected_return=0.0,
                    cvar_alpha=0.0,
                    var_alpha=0.0,
                    objective_value=0.0,
                    optimality_gap=0.0,
                    solve_time_seconds=0.01,
                    active_constraints=(),
                )
                env = InvestmentDecisionGovernanceGate.create_recommendation_envelope(
                    portfolio=portfolio,
                    solver_result=dummy,
                    caller=self.caller,
                )
                self.assertEqual(env.status, exp_exec_status)
                self.assertEqual(env.abstention_reason, exp_reason)

    # 18. Asset-order and weight-length mismatch
    def test_asset_order_and_weight_length_mismatch(self):
        portfolio = Portfolio(
            portfolio_id="PORT-LEN",
            tenant_org_id=str(self.tenant_id),
            assets=(make_asset("L1", capex_kes=0), make_asset("L2", capex_kes=0)),
            cash_reserves_cents=10_000_000_00,
        )
        # Optimal weights length is 1, but portfolio has 2 assets -> mismatch raises ValueError
        dummy = SolverExecutionResult(
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
        with self.assertRaises(ValueError):
            InvestmentDecisionGovernanceGate.create_recommendation_envelope(
                portfolio=portfolio,
                solver_result=dummy,
                caller=self.caller,
            )

    # 19. Committee approval conditions
    def test_committee_approval_conditions(self):
        a1 = make_asset("APP-01", val_kes=10_000_000, capex_kes=0)
        portfolio = Portfolio(
            portfolio_id="PORT-APP",
            tenant_org_id=str(self.tenant_id),
            assets=(a1,),
            cash_reserves_cents=10_000_000_00,
        )
        dummy = SolverExecutionResult(
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
        # Even with high threshold (KES 100M), any capital deployment requires human committee approval
        env = InvestmentDecisionGovernanceGate.create_recommendation_envelope(
            portfolio=portfolio,
            solver_result=dummy,
            caller=self.caller,
            threshold_for_committee_cents=100_000_000_00,
        )
        self.assertTrue(env.requires_human_approval)
        self.assertEqual(env.human_approval_requirement.approval_role, "INVESTMENT_COMMITTEE")

    # 20. Historical scenario evidence identifiers
    def test_historical_scenario_evidence_identifiers(self):
        # Canonical scenarios have supporting dataset identifiers and SHA256 hashes
        canonical = ScenarioStressEngine.get_canonical_scenarios()
        for sc in canonical:
            if sc.provenance == ScenarioProvenanceKind.HISTORICAL_OBSERVATION:
                self.assertTrue(sc.has_verified_historical_evidence)
                self.assertIsNotNone(sc.dataset_identifier)
                self.assertEqual(len(sc.dataset_sha256), 64)

        # Unverified historical scenario can be reclassified to EXPERT_ASSUMPTION
        sc_unverified = ScenarioDefinition(
            scenario_id="UNVERIF-01",
            name="Unverified Historical Claim",
            provenance=ScenarioProvenanceKind.HISTORICAL_OBSERVATION,
            source="Unpublished internal report",
            observation_or_forecast_period="2021",
            probability=0.05,
            model_version="1.0",
            transformation_methodology="Simple mean shift",
        )
        self.assertFalse(sc_unverified.has_verified_historical_evidence)
        reclassified = sc_unverified.reclassify_if_unverified()
        self.assertEqual(reclassified.provenance, ScenarioProvenanceKind.EXPERT_ASSUMPTION)

    # 21. Upstream Engine 10 and geospatial feed behavior
    def test_upstream_engine10_and_spatial_risk(self):
        a1 = make_asset("UP-01", val_kes=100_000_000, capex_kes=0)
        # Feed declares Engine 10 predictive maintenance and geospatial spatial risk multiplier
        feed = UpstreamEngineDataFeed(
            asset_id="UP-01",
            planned_maintenance_cents=2_500_000_00,
            is_engine10_available=True,
            is_engine06_available=False,
            spatial_risk_multiplier=1.25,
            is_engine03_available=True,
        )
        adapter = UpstreamIntegrationAdapter()
        adapted = adapter.apply_upstream_feed(a1, feed)

        # Engine 10 maintenance applied
        self.assertEqual(adapted.annual_routine_maintenance_cents, 2_500_000_00)
        # Geospatial risk multiplier applied to baseline vacancy
        expected_vac = min(0.95, a1.annual_baseline_vacancy_rate * 1.25)
        self.assertAlmostEqual(adapted.annual_baseline_vacancy_rate, expected_vac, places=4)


if __name__ == "__main__":
    unittest.main()
