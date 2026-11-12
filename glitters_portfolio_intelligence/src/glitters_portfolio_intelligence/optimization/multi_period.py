from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping, Optional, Sequence, Tuple
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
    and renovation scheduler across horizon t = 1..T with beginning-of-period transaction timing,
    minimum holding periods, single-renovation limits, and time-value-of-money discounting.
    """

    @classmethod
    def optimize(
        cls,
        portfolio: Portfolio,
        candidate_assets: Sequence[PropertyAsset],
        horizon_years: int = 5,
        annual_discount_rate: float = 0.08,
        min_cash_reserve_cents: int = 5_000_000_00,  # KES 5M minimum liquidity floor
        initial_holdings: Optional[Mapping[str, float]] = None,
        representation: OwnershipRepresentation = OwnershipRepresentation.FRACTIONAL_CAPITAL_WEIGHT,
        backend: SolverBackendType = SolverBackendType.HIGHS_ORTOOLS if ORTOOLS_AVAILABLE else SolverBackendType.HIGHS_SCIPY,
        time_limit_seconds: float = 30.0,
    ) -> MultiPeriodOptimizationResult:
        """
        Solves the dynamic multi-period MIP or LP problem.
        Enforces:
          - Initial holdings consistency with candidate_assets
          - Beginning-of-period transaction timing:
            * Disposals d_{it} <= h_{i, t-1}
            * Holdings conservation: h_{it} = h_{i, t-1} + a_{it} - d_{it}
            * Acquired assets generate rental cash flows in period t
          - One-time renovation limit: sum_{t=1}^T v_{it} <= 1
          - Minimum holding period: a_{i, tau} + d_{i, s} <= 1 for s in tau..tau+H_i-1
          - Cash continuity and liquidity floor
          - Present-value discounting of distributions and terminal wealth
        """
        if not ORTOOLS_AVAILABLE:
            raise RuntimeError("OR-Tools required for multi-period trajectory optimization.")

        all_assets = list(candidate_assets)
        n_assets = len(all_assets)
        T = horizon_years

        if T < 1:
            raise ValueError(f"horizon_years must be >= 1, got {T}")

        SCALE = 1e5  # Conditioning scaling factor for integer cents

        cand_ids = {a.asset_id for a in all_assets}

        # Validate that any declared initial holding belongs to candidate_assets
        if initial_holdings is not None:
            for aid, frac in initial_holdings.items():
                if frac > 0.0 and aid not in cand_ids:
                    raise ValueError(f"Asset '{aid}' in initial_holdings is not in candidate_assets")

        for a in portfolio.assets:
            if a.asset_id not in cand_ids:
                raise ValueError(f"Asset '{a.asset_id}' in portfolio is not in candidate_assets")

        # Initial holding state h_{i, 0}
        h_0 = []
        for a in all_assets:
            if initial_holdings is not None and a.asset_id in initial_holdings:
                frac = float(initial_holdings[a.asset_id])
            elif any(p.asset_id == a.asset_id for p in portfolio.assets):
                match = next(p for p in portfolio.assets if p.asset_id == a.asset_id)
                frac = float(match.initial_ownership_fraction)
            else:
                frac = 0.0
            if not (0.0 <= frac <= 1.0):
                raise ValueError(f"Initial holding fraction for {a.asset_id} must be in [0, 1], got {frac}")
            h_0.append(frac)

        solver = pywraplp.Solver.CreateSolver(
            "SCIP" if representation == OwnershipRepresentation.BINARY_WHOLE_ASSET else "HIGHS"
        )
        if solver is None:
            solver = pywraplp.Solver.CreateSolver("GLOP")

        solver.SetTimeLimit(int(time_limit_seconds * 1000))

        # Variables:
        h_vars = {}
        a_vars = {}
        d_vars = {}
        v_vars = {}
        cash_vars = {}
        dist_vars = {}

        is_binary = (representation == OwnershipRepresentation.BINARY_WHOLE_ASSET)

        for t in range(1, T + 1):
            cash_vars[t] = solver.NumVar(float(min_cash_reserve_cents) / SCALE, solver.infinity(), f"cash_{t}")
            dist_vars[t] = solver.NumVar(0.0, solver.infinity(), f"dist_{t}")
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
        for i in range(n_assets):
            # One-time renovation constraint across entire planning horizon
            solver.Add(sum(v_vars[i, t] for t in range(1, T + 1)) <= 1.0)

            # Minimum holding period constraint:
            hold_months = all_assets[i].minimum_holding_months
            hold_years = max(1, int(math.ceil(hold_months / 12.0)))
            for tau in range(1, T + 1):
                max_s = min(T, tau + hold_years - 1)
                for s in range(tau, max_s + 1):
                    solver.Add(a_vars[i, tau] + d_vars[i, s] <= 1.0)

        for t in range(1, T + 1):
            for i in range(n_assets):
                prev_h = h_0[i] if t == 1 else h_vars[i, t - 1]

                # Beginning-of-period disposal cannot exceed existing holding
                solver.Add(d_vars[i, t] <= prev_h)

                # No simultaneous buy & sell in same period
                solver.Add(a_vars[i, t] + d_vars[i, t] <= 1.0)

                # Holding state conservation: h_{it} = h_{i, t-1} + a_{it} - d_{it}
                solver.Add(h_vars[i, t] == prev_h + a_vars[i, t] - d_vars[i, t])

                # Renovation only on held properties
                solver.Add(v_vars[i, t] <= h_vars[i, t])

            # Cash Flow Continuity Equation:
            # cash_t = cash_{t-1} + NetRent_t - Capex_t - Downtime_t - Acq_t + Disp_t - dist_t
            prev_cash = (float(portfolio.cash_reserves_cents) / SCALE) if t == 1 else cash_vars[t - 1]
            cash_expr = prev_cash

            for i in range(n_assets):
                asset = all_assets[i]
                ann_gpr = asset.monthly_gross_potential_rent_cents * 12
                ann_opex = asset.monthly_operating_expense_cents * 12
                vac = asset.annual_baseline_vacancy_rate
                cred = asset.annual_baseline_credit_loss_rate
                net_rent = (ann_gpr * (1.0 - vac) * (1.0 - cred) - ann_opex - asset.annual_debt_service_cents) / SCALE

                downtime_penalty = net_rent * (asset.renovation_downtime_months / 12.0)
                acq_cost = (asset.acquisition_valuation_cents * (1.0 + asset.acquisition_cost_rate)) / SCALE
                disp_proceeds = (
                    asset.current_valuation_cents * (1.0 - asset.disposal_cost_rate)
                    - asset.current_loan_balance_cents
                ) / SCALE

                # Holding h_{it} reflects post-rebalance holding and generates rent in period t
                cash_expr += h_vars[i, t] * net_rent
                cash_expr -= v_vars[i, t] * ((asset.planned_capex_cents / SCALE) + downtime_penalty)
                cash_expr -= a_vars[i, t] * acq_cost
                cash_expr += d_vars[i, t] * disp_proceeds

            cash_expr -= dist_vars[t]
            solver.Add(cash_vars[t] == cash_expr)

        # Objective: Discounted cash distributions + discounted terminal portfolio wealth
        beta_T = 1.0 / ((1.0 + annual_discount_rate) ** T)

        obj = solver.Objective()
        for t in range(1, T + 1):
            beta_t = 1.0 / ((1.0 + annual_discount_rate) ** t)
            obj.SetCoefficient(dist_vars[t], beta_t)

        obj.SetCoefficient(cash_vars[T], beta_T)
        for i in range(n_assets):
            net_eq = float(all_assets[i].net_equity_cents) / SCALE
            obj.SetCoefficient(h_vars[i, T], beta_T * net_eq)
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

            terminal_wealth = int(
                (
                    cash_vars[T].solution_value()
                    + sum(h_vars[i, T].solution_value() * (float(all_assets[i].net_equity_cents) / SCALE) for i in range(n_assets))
                )
                * SCALE
            )
            total_distributed_cents = int(sum(dist_vars[t].solution_value() for t in range(1, T + 1)) * SCALE)
            return MultiPeriodOptimizationResult(
                status=status,
                is_optimal=is_opt,
                horizon_periods=T,
                representation=representation,
                schedule=tuple(steps),
                periodic_cash_balances_cents=tuple(periodic_cash),
                terminal_portfolio_wealth_cents=terminal_wealth,
                cumulative_net_cashflow_cents=(periodic_cash[-1] + total_distributed_cents) - portfolio.cash_reserves_cents,
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
