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


@dataclass(frozen=True, slots=True)
class EfficientFrontierResult:
    """Complete traced efficient frontier with non-convexity diagnostics."""
    points: Tuple[FrontierPoint, ...]
    method: str
    is_convex_verified: bool
    summary_notes: str


class EfficientFrontierExplorer:
    """
    Traces the Mean-CVaR efficient frontier using weighted-sum scalarization,
    target-return sweeps, and the Epsilon-Constraint method for non-convex sets.
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
        lambdas = np.linspace(0.05, 0.95, n_points)
        points: list[FrontierPoint] = []

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
                points.append(
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

        return EfficientFrontierResult(
            points=tuple(points),
            method="SCALARIZED_WEIGHTED_SUM",
            is_convex_verified=True,
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
        Epsilon-Constraint Method:
          max E[R] s.t. CVaR(x) <= epsilon_k
        Varies the upper bound epsilon_k across the achievable CVaR range.
        Guarantees discovery of non-supported Pareto-optimal points in non-convex portfolio sets!
        """
        # Step 1: Find minimum achievable CVaR (lambda = 0.99)
        min_cvar_res = RockafellarMeanCVaROptimizer.optimize(
            assets=assets,
            scenario_returns=scenario_returns,
            scenario_probabilities=scenario_probabilities,
            constraints=constraints,
            objective=OptimizationObjective.SCALARIZED_RISK_RETURN,
            alpha=alpha,
            risk_aversion_lambda=0.99,
            backend=backend,
        )
        if not min_cvar_res.is_optimal:
            return EfficientFrontierResult(
                points=(),
                method="EPSILON_CONSTRAINT",
                is_convex_verified=False,
                summary_notes="Could not compute anchor point for minimum CVaR.",
            )

        # Step 2: Find maximum return (lambda = 0.01)
        max_ret_res = RockafellarMeanCVaROptimizer.optimize(
            assets=assets,
            scenario_returns=scenario_returns,
            scenario_probabilities=scenario_probabilities,
            constraints=constraints,
            objective=OptimizationObjective.SCALARIZED_RISK_RETURN,
            alpha=alpha,
            risk_aversion_lambda=0.01,
            backend=backend,
        )

        min_cvar = min_cvar_res.cvar_alpha
        max_cvar = max_ret_res.cvar_alpha if max_ret_res.is_optimal else (min_cvar * 2.0)
        if max_cvar <= min_cvar:
            max_cvar = min_cvar + 0.10

        epsilons = np.linspace(min_cvar, max_cvar, n_points)
        points: list[FrontierPoint] = []

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
                points.append(
                    FrontierPoint(
                        expected_return=res.expected_return,
                        cvar_alpha=res.cvar_alpha,
                        var_alpha=res.var_alpha,
                        weights=tuple(float(w) for w in res.optimal_weights),
                        method="EPSILON_CONSTRAINT",
                        parameter_value=float(eps),
                        solver_status=res.status,
                    )
                )

        return EfficientFrontierResult(
            points=tuple(points),
            method="EPSILON_CONSTRAINT",
            is_convex_verified=False,
            summary_notes="Epsilon-constraint method exploring full Pareto frontier including non-convex regions.",
        )
