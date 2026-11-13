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
    acquisition_fraction: float = 0.0
    disposal_fraction: float = 0.0
    actions: Tuple[DecisionAction, ...] = ()


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
    solver_backend: Optional[SolverBackendType] = None
    solver_name: str = ""
    solver_version: str = ""


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
        funding_model: str = "PROPORTIONAL_OWNERSHIP",
        backend: SolverBackendType = SolverBackendType.HIGHS_ORTOOLS if ORTOOLS_AVAILABLE else SolverBackendType.HIGHS_SCIPY,
        time_limit_seconds: float = 30.0,
    ) -> MultiPeriodOptimizationResult:
        """
        Solves the dynamic multi-period MIP or LP problem.
        Enforces:
          - Initial holdings consistency with candidate_assets
          - Valid holding ages for existing positions (unverified ages enforce full lockup)
          - Fractional holding lock-up via binary event indicators
          - Proportional vs full-asset renovation funding economics
          - Discrete investment lots based on individual asset current valuation
          - Beginning-of-period transaction timing & liquidity floor
        """
        if not math.isfinite(annual_discount_rate) or annual_discount_rate <= -1.0:
            raise ValueError(f"annual_discount_rate must be a finite float > -1.0, got {annual_discount_rate}")

        if min_cash_reserve_cents < 0:
            raise ValueError(f"min_cash_reserve_cents cannot be negative: {min_cash_reserve_cents}")

        if portfolio.cash_reserves_cents < 0:
            raise ValueError(f"portfolio.cash_reserves_cents cannot be negative: {portfolio.cash_reserves_cents}")

        if funding_model not in ("PROPORTIONAL_OWNERSHIP", "FULL_ASSET"):
            raise ValueError(f"Unsupported funding_model: {funding_model}. Must be PROPORTIONAL_OWNERSHIP or FULL_ASSET.")

        all_assets = list(candidate_assets)
        n_assets = len(all_assets)
        T = horizon_years

        if T < 1:
            raise ValueError(f"horizon_years must be >= 1, got {T}")

        is_discrete = (representation == OwnershipRepresentation.DISCRETE_INVESTMENT_LOTS)
        is_binary = (representation == OwnershipRepresentation.BINARY_WHOLE_ASSET)

        # Validate discrete investment lot parameters strictly based on individual asset current valuation
        fraction_per_lot_map: dict[str, float] = {}
        max_lots_map: dict[str, int] = {}
        lot_cents_map: dict[str, int] = {}

        if is_discrete:
            if lot_size_cents is None:
                raise UnsupportedSolverConfigurationError(
                    "OwnershipRepresentation.DISCRETE_INVESTMENT_LOTS requires explicit positive lot_size_cents configuration."
                )
            for a in all_assets:
                if isinstance(lot_size_cents, Mapping):
                    if a.asset_id not in lot_size_cents:
                        raise ValueError(f"Missing explicit lot size for asset '{a.asset_id}' in lot_size_cents mapping.")
                    a_lot = lot_size_cents[a.asset_id]
                else:
                    a_lot = lot_size_cents

                if not isinstance(a_lot, int) or a_lot <= 0:
                    raise ValueError(f"lot_size_cents for asset {a.asset_id} must be a positive integer cents, got {a_lot}")
                if a_lot > a.current_valuation_cents:
                    raise ValueError(
                        f"lot_size_cents ({a_lot}) cannot exceed asset current valuation "
                        f"({a.current_valuation_cents}) for asset {a.asset_id}"
                    )
                f_lot = a_lot / float(a.current_valuation_cents)
                m_lots = int(a.current_valuation_cents // a_lot)
                fraction_per_lot_map[a.asset_id] = f_lot
                max_lots_map[a.asset_id] = m_lots
                lot_cents_map[a.asset_id] = a_lot

        # Check if formulation requires MILP
        has_min_holding = any(a.minimum_holding_months > 12 for a in all_assets)
        requires_milp = (
            representation in (OwnershipRepresentation.BINARY_WHOLE_ASSET, OwnershipRepresentation.DISCRETE_INVESTMENT_LOTS)
            or has_min_holding
            or (funding_model == "PROPORTIONAL_OWNERSHIP")
        )

        if backend == SolverBackendType.HIGHS_SCIPY:
            raise UnsupportedSolverConfigurationError(
                "HIGHS_SCIPY is a SciPy-based solver backend and does not support multi-period trajectory optimization. "
                "Use an OR-Tools backend such as HIGHS_ORTOOLS, SCIP_ORTOOLS, CBC_ORTOOLS, or GLOP_ORTOOLS."
            )

        if requires_milp and backend == SolverBackendType.GLOP_ORTOOLS:
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

        # Initial holding fractions h_0
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

            # Check integer lot compatibility for discrete representation
            if is_discrete and frac > 0.0:
                f_lot = fraction_per_lot_map[a.asset_id]
                k0 = frac / f_lot
                if abs(k0 - round(k0)) > 1e-4:
                    raise ValueError(
                        f"Initial holding fraction {frac} for asset {a.asset_id} is not an integer multiple "
                        f"of lot size fraction {f_lot:.6f} (lot size: {lot_cents_map[a.asset_id]} cents)."
                    )

            h_0.append(frac)

        solver_backend_map = {
            SolverBackendType.HIGHS_ORTOOLS: "HIGHS",
            SolverBackendType.SCIP_ORTOOLS: "SCIP",
            SolverBackendType.CBC_ORTOOLS: "CBC",
            SolverBackendType.GLOP_ORTOOLS: "GLOP",
        }
        if backend not in solver_backend_map:
            raise UnsupportedSolverConfigurationError(f"Unsupported solver backend for multi-period optimization: {backend.value}")

        solver_name = solver_backend_map[backend]
        solver = pywraplp.Solver.CreateSolver(solver_name)
        if solver is None:
            raise UnsupportedSolverConfigurationError(
                f"Requested solver backend '{solver_name}' is unavailable in the current OR-Tools installation."
            )

        solver.SetTimeLimit(int(time_limit_seconds * 1000))

        # Variables:
        h_vars = {}
        a_vars = {}
        d_vars = {}
        v_vars = {}
        v_prop = {}
        z_acq = {}
        cash_vars = {}
        dist_vars = {}

        for t in range(1, T + 1):
            cash_vars[t] = solver.NumVar(float(min_cash_reserve_cents) / SCALE, solver.infinity(), f"cash_{t}")
            dist_vars[t] = solver.NumVar(0.0, solver.infinity(), f"dist_{t}")
            for i in range(n_assets):
                asset = all_assets[i]
                v_vars[i, t] = solver.BoolVar(f"v_{i}_{t}")

                # Linearized proportional renovation variable: v_prop[i, t] = v[i, t] * h[i, t]
                v_prop[i, t] = solver.NumVar(0.0, 1.0, f"v_prop_{i}_{t}")

                if is_binary:
                    h_vars[i, t] = solver.BoolVar(f"h_{i}_{t}")
                    a_vars[i, t] = solver.BoolVar(f"a_{i}_{t}")
                    d_vars[i, t] = solver.BoolVar(f"d_{i}_{t}")
                elif is_discrete:
                    # Discrete lot variables based strictly on current valuation
                    f_lot = fraction_per_lot_map[asset.asset_id]
                    m_lots = max_lots_map[asset.asset_id]
                    lot_var = solver.IntVar(0, m_lots, f"lot_h_{i}_{t}")
                    lot_a = solver.IntVar(0, m_lots, f"lot_a_{i}_{t}")
                    lot_d = solver.IntVar(0, m_lots, f"lot_d_{i}_{t}")

                    h_vars[i, t] = solver.NumVar(0.0, 1.0, f"h_{i}_{t}")
                    a_vars[i, t] = solver.NumVar(0.0, 1.0, f"a_{i}_{t}")
                    d_vars[i, t] = solver.NumVar(0.0, 1.0, f"d_{i}_{t}")
                    solver.Add(h_vars[i, t] == lot_var * f_lot)
                    solver.Add(a_vars[i, t] == lot_a * f_lot)
                    solver.Add(d_vars[i, t] == lot_d * f_lot)
                else:
                    h_vars[i, t] = solver.NumVar(0.0, 1.0, f"h_{i}_{t}")
                    a_vars[i, t] = solver.NumVar(0.0, 1.0, f"a_{i}_{t}")
                    d_vars[i, t] = solver.NumVar(0.0, 1.0, f"d_{i}_{t}")

                # Link proportional renovation variable
                solver.Add(v_prop[i, t] <= v_vars[i, t])
                solver.Add(v_prop[i, t] <= h_vars[i, t])
                solver.Add(v_prop[i, t] >= h_vars[i, t] - (1.0 - v_vars[i, t]))

                # Binary acquisition event indicator for fractional holding locks
                if not is_binary and asset.minimum_holding_months > 12:
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
                if funding_model == "PROPORTIONAL_OWNERSHIP":
                    cash_expr -= v_prop[i, t] * ((asset.planned_capex_cents / SCALE) + downtime_lost_rent)
                else:
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
                    v_prop_val = float(v_prop[i, t].solution_value()) if funding_model == "PROPORTIONAL_OWNERSHIP" else v_val

                    ann_gpr = asset.monthly_gross_potential_rent_cents * 12
                    ann_opex = asset.monthly_operating_expense_cents * 12
                    ann_maint = asset.annual_routine_maintenance_cents
                    vac = asset.annual_baseline_vacancy_rate
                    cred = asset.annual_baseline_credit_loss_rate
                    ann_egi = ann_gpr * (1.0 - vac) * (1.0 - cred)
                    net_rent_unscaled = ann_egi - ann_opex - ann_maint - asset.annual_debt_service_cents
                    downtime_lost_unscaled = ann_egi * (asset.renovation_downtime_months / 12.0)

                    effective_downtime = v_prop_val * downtime_lost_unscaled
                    net_cf_cents = int(round(h_val * net_rent_unscaled - effective_downtime))

                    ACTION_TOLERANCE = 1e-4
                    actions_list: list[DecisionAction] = []
                    if a_val > ACTION_TOLERANCE:
                        actions_list.append(DecisionAction.BUY)
                    if d_val > ACTION_TOLERANCE:
                        actions_list.append(DecisionAction.SELL)
                    if v_val > ACTION_TOLERANCE:
                        actions_list.append(DecisionAction.RENOVATE)

                    if DecisionAction.BUY in actions_list:
                        primary_action = DecisionAction.BUY
                    elif DecisionAction.SELL in actions_list:
                        primary_action = DecisionAction.SELL
                    elif DecisionAction.RENOVATE in actions_list:
                        primary_action = DecisionAction.RENOVATE
                    else:
                        primary_action = DecisionAction.HOLD

                    steps.append(
                        MultiPeriodDecisionStep(
                            period=t,
                            asset_id=asset.asset_id,
                            action=primary_action,
                            holding_fraction=h_val,
                            cash_flow_generated_cents=net_cf_cents,
                            capital_expenditure_cents=int(round(v_prop_val * asset.planned_capex_cents)),
                            renovation_active=(v_val > 0.5),
                            acquisition_fraction=a_val,
                            disposal_fraction=d_val,
                            actions=tuple(actions_list) if actions_list else (DecisionAction.HOLD,),
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
                solver_backend=backend,
                solver_name=solver_name,
                solver_version="Google OR-Tools pywraplp",
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
                solver_backend=backend,
                solver_name=solver_name,
                solver_version="Google OR-Tools pywraplp",
            )
