from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple
import numpy as np

from ..domain.entities import Portfolio, PropertyAsset
from ..domain.enums import DecisionAction, OwnershipRepresentation, OptimizationStatus, SolverBackendType
from .solver_backends import ORTOOLS_AVAILABLE

if ORTOOLS_AVAILABLE:
    from ortools.linear_solver import pywraplp


@dataclass(frozen=True, slots=True)
class MultiPeriodDecisionStep:
    """Detailed decision action for a single asset at a specific time period."""
    period: int
    asset_id: str
    action: DecisionAction
    holding_fraction: float
    cash_flow_generated_cents: int
    capital_expenditure_cents: int
    renovation_active: bool


@dataclass(frozen=True, slots=True)
class MultiPeriodOptimizationResult:
    """Complete dynamic multi-period rebalancing trajectory."""
    status: OptimizationStatus
    is_optimal: bool
    horizon_periods: int
    representation: OwnershipRepresentation
    schedule: Tuple[MultiPeriodDecisionStep, ...]
    periodic_cash_balances_cents: Tuple[int, ...]
    terminal_portfolio_wealth_cents: int
    cumulative_net_cashflow_cents: int
    total_capex_spent_cents: int
    solve_time_seconds: float
    solver_message: str


class MultiPeriodPortfolioOptimizer:
    """
    Dynamic multi-period capital allocation, retention, acquisition, disposal,
    and renovation scheduler across horizon t = 1..T.
    """

    @classmethod
    def optimize(
        cls,
        portfolio: Portfolio,
        candidate_assets: Sequence[PropertyAsset],
        horizon_years: int = 5,
        annual_discount_rate: float = 0.08,
        min_cash_reserve_cents: int = 5_000_000_00,  # KES 5M minimum liquidity floor
        representation: OwnershipRepresentation = OwnershipRepresentation.FRACTIONAL_CAPITAL_WEIGHT,
        backend: SolverBackendType = SolverBackendType.HIGHS_ORTOOLS if ORTOOLS_AVAILABLE else SolverBackendType.HIGHS_SCIPY,
        time_limit_seconds: float = 30.0,
    ) -> MultiPeriodOptimizationResult:
        """
        Solves the dynamic multi-period MIP or LP problem.
        Enforces:
          - a_{it} + d_{it} <= 1 (No simultaneous buy and sell in the same period)
          - h_{it} = h_{i, t-1} + a_{it} - d_{it} (Balance of asset holdings)
          - v_{it} <= h_{it} (Renovation only on held properties)
          - Cash_t >= min_cash_reserve_cents (Liquidity reserve floor)
          - Downtime rental reduction during renovation
        """
        if not ORTOOLS_AVAILABLE:
            raise RuntimeError("OR-Tools required for multi-period trajectory optimization.")

        all_assets = list(candidate_assets)
        n_assets = len(all_assets)
        T = horizon_years

        SCALE = 1e5  # Conditioning scaling factor for integer cents

        # Initial holding state h_{i, 0}
        initial_portfolio_ids = {a.asset_id for a in portfolio.assets}
        h_0 = [1.0 if a.asset_id in initial_portfolio_ids else 0.0 for a in all_assets]

        solver = pywraplp.Solver.CreateSolver("SCIP" if representation == OwnershipRepresentation.BINARY_WHOLE_ASSET else "HIGHS")
        if solver is None:
            solver = pywraplp.Solver.CreateSolver("GLOP")

        solver.SetTimeLimit(int(time_limit_seconds * 1000))

        # Variables:
        # h[i, t]: holding fraction in [0, 1]
        # a[i, t]: acquisition in [0, 1]
        # d[i, t]: disposal in [0, 1]
        # v[i, t]: renovation in [0, 1]
        # cash[t]: scaled cash balance >= min_cash_reserve_cents / SCALE
        h_vars = {}
        a_vars = {}
        d_vars = {}
        v_vars = {}
        cash_vars = {}

        is_binary = (representation == OwnershipRepresentation.BINARY_WHOLE_ASSET)

        for t in range(1, T + 1):
            cash_vars[t] = solver.NumVar(float(min_cash_reserve_cents) / SCALE, solver.infinity(), f"cash_{t}")
            for i in range(n_assets):
                if is_binary:
                    h_vars[i, t] = solver.BoolVar(f"h_{i}_{t}")
                    a_vars[i, t] = solver.BoolVar(f"a_{i}_{t}")
                    d_vars[i, t] = solver.BoolVar(f"d_{i}_{t}")
                    v_vars[i, t] = solver.BoolVar(f"v_{i}_{t}")
                else:
                    h_vars[i, t] = solver.NumVar(0.0, 1.0, f"h_{i}_{t}")
                    a_vars[i, t] = solver.NumVar(0.0, 1.0, f"a_{i}_{t}")
                    d_vars[i, t] = solver.NumVar(0.0, 1.0, f"d_{i}_{t}")
                    v_vars[i, t] = solver.NumVar(0.0, 1.0, f"v_{i}_{t}")

        # Constraints
        for t in range(1, T + 1):
            for i in range(n_assets):
                # Invariant 1: No simultaneous buy & sell: a_{it} + d_{it} <= 1
                solver.Add(a_vars[i, t] + d_vars[i, t] <= 1.0)

                # Invariant 2: Holding state conservation: h_{it} = h_{i, t-1} + a_{it} - d_{it}
                prev_h = h_0[i] if t == 1 else h_vars[i, t - 1]
                solver.Add(h_vars[i, t] == prev_h + a_vars[i, t] - d_vars[i, t])

                # Invariant 3: Renovation only on held properties: v_{it} <= h_{it}
                solver.Add(v_vars[i, t] <= h_vars[i, t])

            # Invariant 4: Cash Flow Continuity Equation
            prev_cash = (float(portfolio.cash_reserves_cents) / SCALE) if t == 1 else cash_vars[t - 1]
            cash_expr = prev_cash

            for i in range(n_assets):
                asset = all_assets[i]
                ann_gpr = asset.monthly_gross_potential_rent_cents * 12
                ann_opex = asset.monthly_operating_expense_cents * 12
                vac = asset.annual_baseline_vacancy_rate
                cred = asset.annual_baseline_credit_loss_rate
                net_rent = (ann_gpr * (1.0 - vac) * (1.0 - cred) - ann_opex - asset.annual_debt_service_cents) / SCALE

                # Renovation downtime effect: reduces rent by downtime_months/12
                downtime_penalty = net_rent * (asset.renovation_downtime_months / 12.0)

                acq_cost = (asset.acquisition_valuation_cents * (1.0 + asset.acquisition_cost_rate)) / SCALE
                disp_proceeds = (asset.current_valuation_cents * (1.0 - asset.disposal_cost_rate) - asset.current_loan_balance_cents) / SCALE

                # Cash balance update
                cash_expr += h_vars[i, t] * net_rent
                cash_expr -= v_vars[i, t] * ((asset.planned_capex_cents / SCALE) + downtime_penalty)
                cash_expr -= a_vars[i, t] * acq_cost
                cash_expr += d_vars[i, t] * disp_proceeds

            solver.Add(cash_vars[t] == cash_expr)

        # Objective: Maximize Terminal Wealth + Cumulative Discounted Cash Distributions
        obj = solver.Objective()
        # Terminal wealth: Cash_T + sum_i h_{i, T} * NetEquity_i
        obj.SetCoefficient(cash_vars[T], 1.0)
        for i in range(n_assets):
            net_eq = float(all_assets[i].net_equity_cents) / SCALE
            obj.SetCoefficient(h_vars[i, T], net_eq)
        obj.SetMaximization()

        solver_status = solver.Solve()

        status_map = {
            pywraplp.Solver.OPTIMAL: OptimizationStatus.OPTIMAL,
            pywraplp.Solver.FEASIBLE: OptimizationStatus.FEASIBLE,
            pywraplp.Solver.INFEASIBLE: OptimizationStatus.INFEASIBLE,
            pywraplp.Solver.UNBOUNDED: OptimizationStatus.UNBOUNDED,
        }
        status = status_map.get(solver_status, OptimizationStatus.UNKNOWN)
        is_opt = (status == OptimizationStatus.OPTIMAL)

        steps: list[MultiPeriodDecisionStep] = []
        periodic_cash: list[int] = []

        if is_opt or status == OptimizationStatus.FEASIBLE:
            for t in range(1, T + 1):
                periodic_cash.append(int(cash_vars[t].solution_value() * SCALE))
                for i in range(n_assets):
                    asset = all_assets[i]
                    h_val = float(h_vars[i, t].solution_value())
                    a_val = float(a_vars[i, t].solution_value())
                    d_val = float(d_vars[i, t].solution_value())
                    v_val = float(v_vars[i, t].solution_value())

                    action = DecisionAction.HOLD
                    if a_val > 0.5:
                        action = DecisionAction.BUY
                    elif d_val > 0.5:
                        action = DecisionAction.SELL
                    elif v_val > 0.5:
                        action = DecisionAction.RENOVATE

                    steps.append(
                        MultiPeriodDecisionStep(
                            period=t,
                            asset_id=asset.asset_id,
                            action=action,
                            holding_fraction=h_val,
                            cash_flow_generated_cents=int(h_val * (asset.monthly_gross_potential_rent_cents * 12)),
                            capital_expenditure_cents=int(v_val * asset.planned_capex_cents),
                            renovation_active=(v_val > 0.5),
                        )
                    )

            terminal_wealth = int(solver.Objective().Value() * SCALE)
            return MultiPeriodOptimizationResult(
                status=status,
                is_optimal=is_opt,
                horizon_periods=T,
                representation=representation,
                schedule=tuple(steps),
                periodic_cash_balances_cents=tuple(periodic_cash),
                terminal_portfolio_wealth_cents=terminal_wealth,
                cumulative_net_cashflow_cents=periodic_cash[-1] - portfolio.cash_reserves_cents,
                total_capex_spent_cents=sum(s.capital_expenditure_cents for s in steps),
                solve_time_seconds=solver.WallTime() / 1000.0,
                solver_message=f"Multi-period solved with status {status.value}",
            )
        else:
            return MultiPeriodOptimizationResult(
                status=status,
                is_optimal=False,
                horizon_periods=T,
                representation=representation,
                schedule=(),
                periodic_cash_balances_cents=(),
                terminal_portfolio_wealth_cents=0,
                cumulative_net_cashflow_cents=0,
                total_capex_spent_cents=0,
                solve_time_seconds=solver.WallTime() / 1000.0,
                solver_message=f"Multi-period optimization infeasible or failed with status {status.value}",
            )
