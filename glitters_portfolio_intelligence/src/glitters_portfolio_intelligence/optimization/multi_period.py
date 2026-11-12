from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping, Optional, Sequence, Tuple
import numpy as np

from ..domain.entities import Portfolio, PropertyAsset
from ..domain.enums import (
    DecisionAction,
    OwnershipRepresentation,
    OptimizationStatus,
    SolverBackendType,
    UnsupportedSolverConfigurationError,
)
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
    minimum holding periods, single-renovation limits, recurring maintenance, downtime revenue loss,
    and time-value-of-money discounting.
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
        initial_holding_ages_months: Optional[Mapping[str, int]] = None,
        lot_size_cents: Optional[int | Mapping[str, int]] = None,
        representation: OwnershipRepresentation = OwnershipRepresentation.FRACTIONAL_CAPITAL_WEIGHT,
        backend: SolverBackendType = SolverBackendType.HIGHS_ORTOOLS if ORTOOLS_AVAILABLE else SolverBackendType.HIGHS_SCIPY,
        time_limit_seconds: float = 30.0,
    ) -> MultiPeriodOptimizationResult:
        """
        Solves the dynamic multi-period MIP or LP problem.
        Enforces:
          - Initial holdings consistency with candidate_assets
          - Valid holding ages for existing positions (unverified ages enforce full lockup)
          - Fractional holding lock-up via binary event indicators
          - Routine maintenance and positive downtime revenue loss
          - Discrete investment lots when configured (or explicit rejection)
          - Beginning-of-period transaction timing & liquidity floor
        """
        if not math.isfinite(annual_discount_rate) or annual_discount_rate <= -1.0:
            raise ValueError(f"annual_discount_rate must be a finite float > -1.0, got {annual_discount_rate}")

        if min_cash_reserve_cents < 0:
            raise ValueError(f"min_cash_reserve_cents cannot be negative: {min_cash_reserve_cents}")

        if portfolio.cash_reserves_cents < 0:
            raise ValueError(f"portfolio.cash_reserves_cents cannot be negative: {portfolio.cash_reserves_cents}")

        if representation == OwnershipRepresentation.DISCRETE_INVESTMENT_LOTS:
            if lot_size_cents is None:
                raise UnsupportedSolverConfigurationError(
                    "OwnershipRepresentation.DISCRETE_INVESTMENT_LOTS requires explicit positive lot_size_cents configuration."
                )
            if isinstance(lot_size_cents, int) and lot_size_cents <= 0:
                raise ValueError(f"lot_size_cents must be positive, got {lot_size_cents}")

        all_assets = list(candidate_assets)
        n_assets = len(all_assets)
        T = horizon_years

        if T < 1:
            raise ValueError(f"horizon_years must be >= 1, got {T}")

        # Check if formulation requires MILP
        has_min_holding = any(a.minimum_holding_months > 12 for a in all_assets)
        requires_milp = (
            representation in (OwnershipRepresentation.BINARY_WHOLE_ASSET, OwnershipRepresentation.DISCRETE_INVESTMENT_LOTS)
            or has_min_holding
        )

        if requires_milp and backend in (SolverBackendType.HIGHS_SCIPY, SolverBackendType.GLOP_ORTOOLS):
            raise UnsupportedSolverConfigurationError(
                f"Solver backend {backend.value} does not support integer variables required for "
                f"{representation.value} and minimum holding lockup constraints. Use SCIP_ORTOOLS, HIGHS_ORTOOLS, or CBC_ORTOOLS."
            )

        if not ORTOOLS_AVAILABLE:
            raise RuntimeError("Google OR-Tools is required for multi-period portfolio trajectory optimization.")

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

        solver_name = {
            SolverBackendType.HIGHS_ORTOOLS: "HIGHS",
            SolverBackendType.SCIP_ORTOOLS: "SCIP",
            SolverBackendType.CBC_ORTOOLS: "CBC",
            SolverBackendType.GLOP_ORTOOLS: "GLOP",
        }.get(backend, "HIGHS")

        solver = pywraplp.Solver.CreateSolver(solver_name)
        if solver is None:
            solver = pywraplp.Solver.CreateSolver("SCIP" if requires_milp else "GLOP")
        if solver is None:
            raise RuntimeError(f"Failed to create OR-Tools solver backend: {solver_name}")

        solver.SetTimeLimit(int(time_limit_seconds * 1000))

        # Variables:
        h_vars = {}
        a_vars = {}
        d_vars = {}
        v_vars = {}
        z_acq = {}
        cash_vars = {}
        dist_vars = {}

        is_binary = (representation == OwnershipRepresentation.BINARY_WHOLE_ASSET)
        is_discrete = (representation == OwnershipRepresentation.DISCRETE_INVESTMENT_LOTS)

        # Baseline capital used for discrete lot fractioning
        total_base_capital_cents = max(
            portfolio.total_portfolio_value_cents,
            min_cash_reserve_cents * 2,
            100_000_000_00,
        )

        for t in range(1, T + 1):
            cash_vars[t] = solver.NumVar(float(min_cash_reserve_cents) / SCALE, solver.infinity(), f"cash_{t}")
            dist_vars[t] = solver.NumVar(0.0, solver.infinity(), f"dist_{t}")
            for i in range(n_assets):
                v_vars[i, t] = solver.BoolVar(f"v_{i}_{t}")
                if is_binary:
                    h_vars[i, t] = solver.BoolVar(f"h_{i}_{t}")
                    a_vars[i, t] = solver.BoolVar(f"a_{i}_{t}")
                    d_vars[i, t] = solver.BoolVar(f"d_{i}_{t}")
                elif is_discrete:
                    # Discrete lot variables
                    asset_lot_cents = (
                        lot_size_cents.get(all_assets[i].asset_id, 10_000_000_00)
                        if isinstance(lot_size_cents, Mapping)
                        else (lot_size_cents or 10_000_000_00)
                    )
                    max_lots = max(1, total_base_capital_cents // asset_lot_cents)
                    lot_var = solver.IntVar(0, max_lots, f"lot_h_{i}_{t}")
                    lot_a = solver.IntVar(0, max_lots, f"lot_a_{i}_{t}")
                    lot_d = solver.IntVar(0, max_lots, f"lot_d_{i}_{t}")
                    # Map lot variables to continuous fractions for balance consistency
                    frac_scale = asset_lot_cents / total_base_capital_cents
                    h_vars[i, t] = solver.NumVar(0.0, 1.0, f"h_{i}_{t}")
                    a_vars[i, t] = solver.NumVar(0.0, 1.0, f"a_{i}_{t}")
                    d_vars[i, t] = solver.NumVar(0.0, 1.0, f"d_{i}_{t}")
                    solver.Add(h_vars[i, t] == lot_var * frac_scale)
                    solver.Add(a_vars[i, t] == lot_a * frac_scale)
                    solver.Add(d_vars[i, t] == lot_d * frac_scale)
                else:
                    h_vars[i, t] = solver.NumVar(0.0, 1.0, f"h_{i}_{t}")
                    a_vars[i, t] = solver.NumVar(0.0, 1.0, f"a_{i}_{t}")
                    d_vars[i, t] = solver.NumVar(0.0, 1.0, f"d_{i}_{t}")

                # Binary acquisition event indicator for fractional holding locks
                if not is_binary and all_assets[i].minimum_holding_months > 12:
                    z_acq[i, t] = solver.BoolVar(f"z_acq_{i}_{t}")
                    solver.Add(a_vars[i, t] <= z_acq[i, t])

        # Constraints
        for i in range(n_assets):
            asset = all_assets[i]
            # 1. One-time renovation constraint across entire planning horizon
            solver.Add(sum(v_vars[i, t] for t in range(1, T + 1)) <= 1.0)

            # 2. Existing holding minimum holding lockup handling
            if h_0[i] > 0.0 and asset.minimum_holding_months > 0:
                if initial_holding_ages_months is not None and asset.asset_id in initial_holding_ages_months:
                    age_m = initial_holding_ages_months[asset.asset_id]
                    if age_m < 0:
                        raise ValueError(f"initial_holding_ages_months for {asset.asset_id} cannot be negative: {age_m}")
                    rem_m = max(0, asset.minimum_holding_months - age_m)
                    rem_periods = int(math.ceil(rem_m / 12.0))
                else:
                    # Explicitly unknown holding age: do not pretend holding period is verified
                    rem_periods = max(1, int(math.ceil(asset.minimum_holding_months / 12.0)))

                for t_lock in range(1, min(T, rem_periods) + 1):
                    solver.Add(d_vars[i, t_lock] == 0.0)

            # 3. New acquisition minimum holding period constraint
            hold_years = max(1, int(math.ceil(asset.minimum_holding_months / 12.0)))
            if hold_years > 1:
                for tau in range(1, T + 1):
                    max_s = min(T, tau + hold_years - 1)
                    for s in range(tau, max_s + 1):
                        if is_binary:
                            solver.Add(a_vars[i, tau] + d_vars[i, s] <= 1.0)
                        else:
                            solver.Add(d_vars[i, s] <= 1.0 - z_acq[i, tau])

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
                ann_maint = asset.annual_routine_maintenance_cents
                vac = asset.annual_baseline_vacancy_rate
                cred = asset.annual_baseline_credit_loss_rate

                # Exact gross rental income (EGI)
                ann_egi = ann_gpr * (1.0 - vac) * (1.0 - cred)

                # Net rent includes recurring maintenance and debt service
                net_rent = (ann_egi - ann_opex - ann_maint - asset.annual_debt_service_cents) / SCALE

                # Downtime penalty models lost gross rental income strictly, never benefiting negative cash flows
                downtime_lost_rent = (ann_egi * (asset.renovation_downtime_months / 12.0)) / SCALE

                acq_cost = (asset.acquisition_valuation_cents * (1.0 + asset.acquisition_cost_rate)) / SCALE
                disp_proceeds = (
                    asset.current_valuation_cents * (1.0 - asset.disposal_cost_rate)
                    - asset.current_loan_balance_cents
                ) / SCALE

                cash_expr += h_vars[i, t] * net_rent
                cash_expr -= v_vars[i, t] * ((asset.planned_capex_cents / SCALE) + downtime_lost_rent)
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
                periodic_cash.append(int(round(cash_vars[t].solution_value() * SCALE)))
                for i in range(n_assets):
                    asset = all_assets[i]
                    h_val = float(h_vars[i, t].solution_value())
                    a_val = float(a_vars[i, t].solution_value())
                    d_val = float(d_vars[i, t].solution_value())
                    v_val = float(v_vars[i, t].solution_value())

                    ann_gpr = asset.monthly_gross_potential_rent_cents * 12
                    ann_opex = asset.monthly_operating_expense_cents * 12
                    ann_maint = asset.annual_routine_maintenance_cents
                    vac = asset.annual_baseline_vacancy_rate
                    cred = asset.annual_baseline_credit_loss_rate
                    ann_egi = ann_gpr * (1.0 - vac) * (1.0 - cred)
                    net_rent_unscaled = ann_egi - ann_opex - ann_maint - asset.annual_debt_service_cents
                    downtime_lost_unscaled = ann_egi * (asset.renovation_downtime_months / 12.0)

                    net_cf_cents = int(round(h_val * net_rent_unscaled - (v_val * downtime_lost_unscaled if v_val > 0.5 else 0)))

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
                            cash_flow_generated_cents=net_cf_cents,
                            capital_expenditure_cents=int(round(v_val * asset.planned_capex_cents)),
                            renovation_active=(v_val > 0.5),
                        )
                    )

            terminal_wealth = int(
                round(
                    (
                        cash_vars[T].solution_value()
                        + sum(h_vars[i, T].solution_value() * (float(all_assets[i].net_equity_cents) / SCALE) for i in range(n_assets))
                    )
                    * SCALE
                )
            )
            total_distributed_cents = int(round(sum(dist_vars[t].solution_value() for t in range(1, T + 1)) * SCALE))
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
