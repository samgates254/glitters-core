from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import List, Optional, Sequence, Tuple
import numpy as np
from scipy.optimize import linprog

from ..domain.enums import OptimizationStatus, SolverBackendType, UnsupportedSolverConfigurationError

try:
    import ortools
    from ortools.linear_solver import pywraplp
    ORTOOLS_AVAILABLE = True
except ImportError:
    ortools = None
    pywraplp = None
    ORTOOLS_AVAILABLE = False


@dataclass(frozen=True, slots=True)
class SolverExecutionResult:
    """
    Rigorously certified mathematical optimization solver result.
    Never claims optimality unless verified by the underlying solver.
    """
    solver_type: SolverBackendType
    solver_version: str
    status: OptimizationStatus
    is_optimal: bool
    optimal_weights: np.ndarray
    expected_return: float
    cvar_alpha: float
    var_alpha: float
    objective_value: float
    optimality_gap: float
    solve_time_seconds: float
    active_constraints: Tuple[str, ...] = ()
    solver_specific_message: str = ""


class PortfolioSolverBackend:
    """Multi-solver abstraction supporting HiGHS (SciPy), and HiGHS, SCIP, CBC, GLOP (OR-Tools)."""

    @classmethod
    def get_available_backends(cls) -> list[SolverBackendType]:
        available = [SolverBackendType.HIGHS_SCIPY]
        if ORTOOLS_AVAILABLE:
            for b_type, name in [
                (SolverBackendType.HIGHS_ORTOOLS, "HIGHS"),
                (SolverBackendType.SCIP_ORTOOLS, "SCIP"),
                (SolverBackendType.CBC_ORTOOLS, "CBC"),
                (SolverBackendType.GLOP_ORTOOLS, "GLOP"),
            ]:
                s = pywraplp.Solver.CreateSolver(name)
                if s is not None:
                    available.append(b_type)
        return available

    @classmethod
    def solve_linear_cvar(
        cls,
        c_obj: np.ndarray,
        A_ub: np.ndarray,
        b_ub: np.ndarray,
        A_eq: np.ndarray,
        b_eq: np.ndarray,
        bounds: Sequence[Tuple[Optional[float], Optional[float]]],
        n_assets: int,
        n_scenarios: int,
        alpha: float,
        scenario_returns: np.ndarray,
        scenario_probs: np.ndarray,
        var_types: Optional[Sequence[str]] = None,
        constraint_labels: Sequence[str] | None = None,
        backend: SolverBackendType = SolverBackendType.HIGHS_SCIPY,
        time_limit_seconds: float = 30.0,
    ) -> SolverExecutionResult:
        """
        Solves the LP/MILP formulation using the selected backend.
        Variable layout: [x_1..x_N, gamma, u_1..u_S, ...]
        """
        start_t = time.perf_counter()

        if backend == SolverBackendType.HIGHS_SCIPY:
            return cls._solve_scipy_highs(
                c_obj=c_obj,
                A_ub=A_ub,
                b_ub=b_ub,
                A_eq=A_eq,
                b_eq=b_eq,
                bounds=bounds,
                n_assets=n_assets,
                n_scenarios=n_scenarios,
                alpha=alpha,
                scenario_returns=scenario_returns,
                scenario_probs=scenario_probs,
                var_types=var_types,
                constraint_labels=constraint_labels,
                start_t=start_t,
                time_limit_seconds=time_limit_seconds,
            )
        else:
            return cls._solve_ortools(
                backend=backend,
                c_obj=c_obj,
                A_ub=A_ub,
                b_ub=b_ub,
                A_eq=A_eq,
                b_eq=b_eq,
                bounds=bounds,
                n_assets=n_assets,
                n_scenarios=n_scenarios,
                alpha=alpha,
                scenario_returns=scenario_returns,
                scenario_probs=scenario_probs,
                var_types=var_types,
                constraint_labels=constraint_labels,
                start_t=start_t,
                time_limit_seconds=time_limit_seconds,
            )

    @classmethod
    def _solve_scipy_highs(
        cls,
        c_obj: np.ndarray,
        A_ub: np.ndarray,
        b_ub: np.ndarray,
        A_eq: np.ndarray,
        b_eq: np.ndarray,
        bounds: Sequence[Tuple[Optional[float], Optional[float]]],
        n_assets: int,
        n_scenarios: int,
        alpha: float,
        scenario_returns: np.ndarray,
        scenario_probs: np.ndarray,
        var_types: Optional[Sequence[str]],
        constraint_labels: Sequence[str] | None,
        start_t: float,
        time_limit_seconds: float,
    ) -> SolverExecutionResult:
        if var_types and any(vt in ("B", "I") for vt in var_types):
            raise UnsupportedSolverConfigurationError(
                "SciPy HiGHS backend in PortfolioSolverBackend does not support Mixed-Integer Linear Programming (MILP). "
                "For cardinality or semi-continuous minimum holding constraints, use SCIP_ORTOOLS, HIGHS_ORTOOLS, or CBC_ORTOOLS."
            )

        res = linprog(
            c=c_obj,
            A_ub=A_ub if len(A_ub) > 0 else None,
            b_ub=b_ub if len(b_ub) > 0 else None,
            A_eq=A_eq if len(A_eq) > 0 else None,
            b_eq=b_eq if len(b_eq) > 0 else None,
            bounds=bounds,
            method="highs",
            options={"time_limit": time_limit_seconds},
        )
        elapsed = time.perf_counter() - start_t

        status = OptimizationStatus.UNKNOWN
        is_opt = False
        gap = 0.0

        if res.status == 0:  # Optimization terminated successfully
            status = OptimizationStatus.OPTIMAL
            is_opt = True
        elif res.status == 1:
            status = OptimizationStatus.TIME_LIMIT
        elif res.status == 2:
            status = OptimizationStatus.INFEASIBLE
        elif res.status == 3:
            status = OptimizationStatus.UNBOUNDED

        if is_opt and res.x is not None:
            x_weights = res.x[:n_assets]
            gamma = float(res.x[n_assets])
            u_excess = res.x[n_assets + 1 : n_assets + 1 + n_scenarios]
            cvar = float(gamma + (1.0 / (1.0 - alpha)) * np.sum(scenario_probs * u_excess))
            exp_ret = float(np.sum(x_weights * np.sum(scenario_returns * scenario_probs, axis=1)))

            # Active constraints detection
            active: list[str] = []
            if constraint_labels and len(A_ub) > 0 and len(b_ub) > 0:
                slack = b_ub - (A_ub @ res.x)
                for idx, s in enumerate(slack):
                    if idx < len(constraint_labels) and abs(s) < 1e-5:
                        active.append(constraint_labels[idx])

            return SolverExecutionResult(
                solver_type=SolverBackendType.HIGHS_SCIPY,
                solver_version="SciPy HiGHS Dual-Simplex/IPM",
                status=status,
                is_optimal=is_opt,
                optimal_weights=x_weights,
                expected_return=exp_ret,
                cvar_alpha=cvar,
                var_alpha=gamma,
                objective_value=float(res.fun),
                optimality_gap=gap,
                solve_time_seconds=elapsed,
                active_constraints=tuple(active),
                solver_specific_message=res.message,
            )
        else:
            return SolverExecutionResult(
                solver_type=SolverBackendType.HIGHS_SCIPY,
                solver_version="SciPy HiGHS Dual-Simplex/IPM",
                status=status,
                is_optimal=False,
                optimal_weights=np.zeros(n_assets, dtype=np.float64),
                expected_return=0.0,
                cvar_alpha=0.0,
                var_alpha=0.0,
                objective_value=float("inf"),
                optimality_gap=float("inf"),
                solve_time_seconds=elapsed,
                solver_specific_message=res.message,
            )

    @classmethod
    def _solve_ortools(
        cls,
        backend: SolverBackendType,
        c_obj: np.ndarray,
        A_ub: np.ndarray,
        b_ub: np.ndarray,
        A_eq: np.ndarray,
        b_eq: np.ndarray,
        bounds: Sequence[Tuple[Optional[float], Optional[float]]],
        n_assets: int,
        n_scenarios: int,
        alpha: float,
        scenario_returns: np.ndarray,
        scenario_probs: np.ndarray,
        var_types: Optional[Sequence[str]],
        constraint_labels: Sequence[str] | None,
        start_t: float,
        time_limit_seconds: float,
    ) -> SolverExecutionResult:
        if not ORTOOLS_AVAILABLE:
            raise RuntimeError("Google OR-Tools is not installed.")

        if backend == SolverBackendType.GLOP_ORTOOLS and var_types and any(vt in ("B", "I") for vt in var_types):
            raise UnsupportedSolverConfigurationError(
                "GLOP solver backend does not support Mixed-Integer Linear Programming (MILP). "
                "For cardinality or semi-continuous minimum holding constraints, use SCIP_ORTOOLS, HIGHS_ORTOOLS, or CBC_ORTOOLS."
            )

        ortools_name = {
            SolverBackendType.HIGHS_ORTOOLS: "HIGHS",
            SolverBackendType.SCIP_ORTOOLS: "SCIP",
            SolverBackendType.CBC_ORTOOLS: "CBC",
            SolverBackendType.GLOP_ORTOOLS: "GLOP",
        }.get(backend, "GLOP")

        solver = pywraplp.Solver.CreateSolver(ortools_name)
        if solver is None:
            raise RuntimeError(f"Failed to create OR-Tools solver backend: {ortools_name}")

        solver.SetTimeLimit(int(time_limit_seconds * 1000))

        n_vars = len(c_obj)
        vars_list = []
        for i in range(n_vars):
            lb = bounds[i][0] if bounds[i][0] is not None else -solver.infinity()
            ub = bounds[i][1] if bounds[i][1] is not None else solver.infinity()
            vt = var_types[i] if var_types and i < len(var_types) else "C"
            if vt == "B":
                vars_list.append(solver.BoolVar(f"v_{i}"))
            elif vt == "I":
                vars_list.append(solver.IntVar(lb, ub, f"v_{i}"))
            else:
                vars_list.append(solver.NumVar(lb, ub, f"v_{i}"))

        # Objective
        obj = solver.Objective()
        for i in range(n_vars):
            obj.SetCoefficient(vars_list[i], float(c_obj[i]))
        obj.SetMinimization()

        # Inequality constraints: A_ub @ v <= b_ub
        if len(A_ub) > 0 and len(b_ub) > 0:
            for r_idx in range(len(b_ub)):
                ct = solver.Constraint(-solver.infinity(), float(b_ub[r_idx]))
                for c_idx in range(n_vars):
                    val = float(A_ub[r_idx, c_idx])
                    if val != 0.0:
                        ct.SetCoefficient(vars_list[c_idx], val)

        # Equality constraints: A_eq @ v == b_eq
        if len(A_eq) > 0 and len(b_eq) > 0:
            for r_idx in range(len(b_eq)):
                val_target = float(b_eq[r_idx])
                ct = solver.Constraint(val_target, val_target)
                for c_idx in range(n_vars):
                    val = float(A_eq[r_idx, c_idx])
                    if val != 0.0:
                        ct.SetCoefficient(vars_list[c_idx], val)

        solver_status = solver.Solve()
        elapsed = time.perf_counter() - start_t

        status_map = {
            pywraplp.Solver.OPTIMAL: OptimizationStatus.OPTIMAL,
            pywraplp.Solver.FEASIBLE: OptimizationStatus.FEASIBLE,
            pywraplp.Solver.INFEASIBLE: OptimizationStatus.INFEASIBLE,
            pywraplp.Solver.UNBOUNDED: OptimizationStatus.UNBOUNDED,
            pywraplp.Solver.NOT_SOLVED: OptimizationStatus.UNKNOWN,
        }
        status = status_map.get(solver_status, OptimizationStatus.UNKNOWN)
        is_opt = (status == OptimizationStatus.OPTIMAL)
        ortools_ver = ortools.__version__ if ortools is not None else "unknown"

        if is_opt or status == OptimizationStatus.FEASIBLE:
            sol_x = np.array([v.solution_value() for v in vars_list], dtype=np.float64)
            x_weights = sol_x[:n_assets]
            gamma = float(sol_x[n_assets])
            u_excess = sol_x[n_assets + 1 : n_assets + 1 + n_scenarios]
            cvar = float(gamma + (1.0 / (1.0 - alpha)) * np.sum(scenario_probs * u_excess))
            exp_ret = float(np.sum(x_weights * np.sum(scenario_returns * scenario_probs, axis=1)))

            gap = 0.0
            if not is_opt:
                try:
                    bb = float(solver.Objective().BestBound())
                    val = float(solver.Objective().Value())
                    gap = abs(val - bb) / (abs(val) + 1e-10)
                except Exception:
                    gap = float("nan")

            return SolverExecutionResult(
                solver_type=backend,
                solver_version=f"OR-Tools {ortools_ver} ({ortools_name})",
                status=status,
                is_optimal=is_opt,
                optimal_weights=x_weights,
                expected_return=exp_ret,
                cvar_alpha=cvar,
                var_alpha=gamma,
                objective_value=float(solver.Objective().Value()),
                optimality_gap=gap,
                solve_time_seconds=elapsed,
                solver_specific_message=f"OR-Tools status code: {solver_status}",
            )
        else:
            return SolverExecutionResult(
                solver_type=backend,
                solver_version=f"OR-Tools {ortools_ver} ({ortools_name})",
                status=status,
                is_optimal=False,
                optimal_weights=np.zeros(n_assets, dtype=np.float64),
                expected_return=0.0,
                cvar_alpha=0.0,
                var_alpha=0.0,
                objective_value=float("inf"),
                optimality_gap=float("inf"),
                solve_time_seconds=elapsed,
                solver_specific_message=f"OR-Tools status code: {solver_status}",
            )
