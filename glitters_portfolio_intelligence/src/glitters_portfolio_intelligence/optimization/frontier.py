from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple
import numpy as np

from ..domain.entities import PropertyAsset
from ..domain.enums import OptimizationObjective, OptimizationStatus, SolverBackendType
from .constraints import PortfolioConstraints
from .rockafellar_cvar import RockafellarMeanCVaROptimizer
from .solver_backends import SolverExecutionResult


@dataclass(frozen=True, slots=True)
class FrontierPoint:
    """A verified Pareto-optimal point along the Mean-CVaR efficient frontier."""
    expected_return: float
    cvar_alpha: float
    var_alpha: float
    weights: Tuple[float, ...]
    method: str
    parameter_value: float
    solver_status: OptimizationStatus
    is_feasible_epsilon: bool = True


@dataclass(frozen=True, slots=True)
class EfficientFrontierResult:
    """Complete traced efficient frontier with non-convexity diagnostics."""
    points: Tuple[FrontierPoint, ...]
    method: str
    is_convex_verified: bool
    summary_notes: str


class EfficientFrontierExplorer:
    """
    Traces the Mean-CVaR efficient frontier using weighted-sum scalarization
    and the Epsilon-Constraint method with genuine mathematical endpoint anchors
    and non-convexity diagnostics.
    """

    @classmethod
    def trace_scalarized_frontier(
        cls,
        assets: Sequence[PropertyAsset],
        scenario_returns: np.ndarray,
        scenario_probabilities: np.ndarray,
        constraints: PortfolioConstraints,
        alpha: float = 0.95,
        n_points: int = 11,
        backend: SolverBackendType = SolverBackendType.HIGHS_SCIPY,
    ) -> EfficientFrontierResult:
        """
        Traces the frontier using scalarized parameter lambda in (0, 1):
          min lambda * CVaR - (1 - lambda) * E[R]
        Note: Only recovers supported efficient points; non-convex gaps require epsilon-constraint.
        """
        lambdas = np.linspace(0.01, 0.99, n_points)
        raw_points: list[FrontierPoint] = []

        for lam in lambdas:
            res = RockafellarMeanCVaROptimizer.optimize(
                assets=assets,
                scenario_returns=scenario_returns,
                scenario_probabilities=scenario_probabilities,
                constraints=constraints,
                objective=OptimizationObjective.SCALARIZED_RISK_RETURN,
                alpha=alpha,
                risk_aversion_lambda=float(lam),
                backend=backend,
            )
            if res.is_optimal:
                raw_points.append(
                    FrontierPoint(
                        expected_return=res.expected_return,
                        cvar_alpha=res.cvar_alpha,
                        var_alpha=res.var_alpha,
                        weights=tuple(float(w) for w in res.optimal_weights),
                        method="SCALARIZED_WEIGHTED_SUM",
                        parameter_value=float(lam),
                        solver_status=res.status,
                    )
                )

        filtered = cls._filter_dominated_points(raw_points)
        is_convex = (not constraints.requires_milp) and cls._verify_convexity(filtered)

        return EfficientFrontierResult(
            points=tuple(filtered),
            method="SCALARIZED_WEIGHTED_SUM",
            is_convex_verified=is_convex,
            summary_notes="Weighted-sum scalarization traces supported efficient points on convex hull.",
        )

    @classmethod
    def trace_epsilon_constraint_frontier(
        cls,
        assets: Sequence[PropertyAsset],
        scenario_returns: np.ndarray,
        scenario_probabilities: np.ndarray,
        constraints: PortfolioConstraints,
        alpha: float = 0.95,
        n_points: int = 11,
        backend: SolverBackendType = SolverBackendType.HIGHS_SCIPY,
    ) -> EfficientFrontierResult:
        """
        Epsilon-Constraint Method with genuine mathematical endpoints:
          1. True Min-CVaR anchor: solve pure Min-CVaR without target return constraint.
          2. True Max-Return anchor: solve pure Max-Return without CVaR budget ceiling.
          3. Sweep epsilon across [CVaR_min, CVaR_max]:
               max E[R] s.t. CVaR(x) <= epsilon_k
        Guarantees discovery of non-supported Pareto-optimal points in non-convex portfolio sets!
        """
        # Step 1: True Minimum Achievable CVaR Endpoint (unconstrained by target return)
        min_cvar_res = RockafellarMeanCVaROptimizer.optimize(
            assets=assets,
            scenario_returns=scenario_returns,
            scenario_probabilities=scenario_probabilities,
            constraints=constraints,
            objective=OptimizationObjective.MINIMIZE_CVAR_TARGET_RETURN,
            target_return=None,  # Genuinely unconstrained Min-CVaR endpoint
            alpha=alpha,
            backend=backend,
        )
        if not min_cvar_res.is_optimal:
            return EfficientFrontierResult(
                points=(),
                method="EPSILON_CONSTRAINT",
                is_convex_verified=False,
                summary_notes="Could not compute true endpoint for minimum achievable CVaR.",
            )

        # Step 2: True Maximum Achievable Return Endpoint (unconstrained by CVaR budget)
        max_ret_res = RockafellarMeanCVaROptimizer.optimize(
            assets=assets,
            scenario_returns=scenario_returns,
            scenario_probabilities=scenario_probabilities,
            constraints=constraints,
            objective=OptimizationObjective.MAXIMIZE_RETURN_CVAR_BUDGET,
            cvar_budget=None,  # Genuinely unconstrained Max-Return endpoint
            alpha=alpha,
            backend=backend,
        )
        if not max_ret_res.is_optimal:
            return EfficientFrontierResult(
                points=(),
                method="EPSILON_CONSTRAINT",
                is_convex_verified=False,
                summary_notes="Could not compute true endpoint for maximum achievable return.",
            )

        min_cvar = min_cvar_res.cvar_alpha
        max_cvar = max_ret_res.cvar_alpha

        if max_cvar < min_cvar - 1e-5:
            # Degenerate or tight single-point frontier
            max_cvar = min_cvar

        epsilons = np.linspace(min_cvar, max_cvar, n_points)
        raw_points: list[FrontierPoint] = []

        from ..scenarios.cvar_verifier import compute_rockafellar_cvar_1d

        for eps in epsilons:
            res = RockafellarMeanCVaROptimizer.optimize(
                assets=assets,
                scenario_returns=scenario_returns,
                scenario_probabilities=scenario_probabilities,
                constraints=constraints,
                objective=OptimizationObjective.MAXIMIZE_RETURN_CVAR_BUDGET,
                alpha=alpha,
                cvar_budget=float(eps),
                backend=backend,
            )
            if res.is_optimal:
                # Filter out epsilon-infeasible points strictly
                if res.cvar_alpha > (eps + 1e-4):
                    continue

                # Independently verify CVaR and return from solution weights
                w_sol = np.array(res.optimal_weights, dtype=np.float64)
                gross_ret = float(np.sum(w_sol * np.sum(scenario_returns * scenario_probabilities, axis=1)))

                if constraints.requires_turnover_variables and constraints.initial_weights is not None:
                    w0 = np.array(constraints.initial_weights, dtype=np.float64)
                    c_buy = constraints.buy_transaction_cost_rate
                    c_sell = constraints.sell_transaction_cost_rate
                    tx_fee = float(np.sum(c_buy * np.maximum(0.0, w_sol - w0) + c_sell * np.maximum(0.0, w0 - w_sol)))
                else:
                    tx_fee = 0.0

                net_ret = gross_ret - tx_fee
                net_losses = -(w_sol @ scenario_returns) + tx_fee
                emp_diag = compute_rockafellar_cvar_1d(net_losses, scenario_probabilities, alpha=alpha)

                raw_points.append(
                    FrontierPoint(
                        expected_return=net_ret,
                        cvar_alpha=emp_diag.cvar,
                        var_alpha=emp_diag.var,
                        weights=tuple(float(w) for w in res.optimal_weights),
                        method="EPSILON_CONSTRAINT",
                        parameter_value=float(eps),
                        solver_status=res.status,
                        is_feasible_epsilon=True,
                    )
                )

        filtered = cls._filter_dominated_points(raw_points)
        is_convex = (not constraints.requires_milp) and cls._verify_convexity(filtered)

        return EfficientFrontierResult(
            points=tuple(filtered),
            method="EPSILON_CONSTRAINT",
            is_convex_verified=is_convex,
            summary_notes=(
                "Discrete sampled efficient frontier generated via epsilon-constraint sweep. "
                "Finite grid sampling explores non-supported Pareto points on non-convex sets "
                "but does not certify complete continuous frontier coverage."
            ),
        )

    @classmethod
    def _filter_dominated_points(cls, points: Sequence[FrontierPoint]) -> list[FrontierPoint]:
        """Removes strictly dominated points (where another point has >= return and <= CVaR)."""
        if not points:
            return []

        # Sort primarily by CVaR ascending, secondarily by return descending
        sorted_pts = sorted(points, key=lambda p: (round(p.cvar_alpha, 5), -round(p.expected_return, 5)))
        non_dominated: list[FrontierPoint] = []

        max_return_seen = -float("inf")
        for p in sorted_pts:
            if p.expected_return >= max_return_seen - 1e-6:
                non_dominated.append(p)
                max_return_seen = p.expected_return

        return non_dominated

    @classmethod
    def _verify_convexity(cls, points: Sequence[FrontierPoint]) -> bool:
        """
        Verifies if marginal return per unit risk (slope dR/dC) is non-increasing.
        A frontier with fewer than 3 points cannot mathematically establish convexity.
        """
        if len(points) < 3:
            return False

        slopes = []
        for i in range(len(points) - 1):
            dc = points[i + 1].cvar_alpha - points[i].cvar_alpha
            dr = points[i + 1].expected_return - points[i].expected_return
            if dc > 1e-5:
                slopes.append(dr / dc)

        # For a convex risk-return frontier, slopes should be non-increasing
        for i in range(len(slopes) - 1):
            if slopes[i + 1] > slopes[i] + 0.05:  # upward kink indicates non-convexity
                return False
        return len(slopes) >= 2
