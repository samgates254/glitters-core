from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence, Tuple
import numpy as np

from ..domain.entities import PropertyAsset
from ..domain.enums import OptimizationObjective, OptimizationStatus, SolverBackendType
from .constraints import PortfolioConstraints, build_linear_constraint_matrices
from .solver_backends import PortfolioSolverBackend, SolverExecutionResult


class RockafellarMeanCVaROptimizer:
    """
    Rockafellar-Uryasev (2000) Mean-CVaR Linear Programming Optimizer.
    Solves for the optimal portfolio weights x in [0, 1]^N, Value-at-Risk gamma,
    and scenario excess loss variables u in R_+^S.
    """

    @classmethod
    def optimize(
        cls,
        assets: Sequence[PropertyAsset],
        scenario_returns: np.ndarray,      # shape (n_assets, n_scenarios)
        scenario_probabilities: np.ndarray, # shape (n_scenarios,)
        constraints: PortfolioConstraints,
        objective: OptimizationObjective = OptimizationObjective.SCALARIZED_RISK_RETURN,
        alpha: float = 0.95,
        target_return: Optional[float] = None,
        cvar_budget: Optional[float] = None,
        risk_aversion_lambda: float = 0.50, # In [0, 1]
        total_capital_cents: int = 100_000_000_00, # Default KES 100M
        backend: SolverBackendType = SolverBackendType.HIGHS_SCIPY,
        time_limit_seconds: float = 30.0,
    ) -> SolverExecutionResult:
        """
        Formulates and executes the Rockafellar-Uryasev LP.
        Decision variables vector:
          z = [x_1, ..., x_N, gamma, u_1, ..., u_S]
          Total variables = N + 1 + S
        """
        n_assets = len(assets)
        n_scens = len(scenario_probabilities)
        n_vars = n_assets + 1 + n_scens

        # Expected return vector for assets
        expected_returns = np.sum(scenario_returns * scenario_probabilities, axis=1)

        # Objective vector c_obj
        c_obj = np.zeros(n_vars, dtype=np.float64)

        if objective == OptimizationObjective.MINIMIZE_CVAR_TARGET_RETURN:
            # Objective: gamma + 1/(1 - alpha) * sum p_s * u_s
            c_obj[n_assets] = 1.0
            c_obj[n_assets + 1 :] = scenario_probabilities / (1.0 - alpha)

        elif objective == OptimizationObjective.MAXIMIZE_RETURN_CVAR_BUDGET:
            # Objective: -sum x_i * E[r_i]
            c_obj[:n_assets] = -expected_returns

        elif objective == OptimizationObjective.SCALARIZED_RISK_RETURN:
            # Objective: lambda * [gamma + 1/(1-alpha) sum p_s u_s] - (1 - lambda) * sum x_i E[r_i]
            c_obj[:n_assets] = -(1.0 - risk_aversion_lambda) * expected_returns
            c_obj[n_assets] = risk_aversion_lambda
            c_obj[n_assets + 1 :] = risk_aversion_lambda * scenario_probabilities / (1.0 - alpha)

        # Variable Bounds:
        # x_i: [0, max_single_asset_weight]
        # gamma: [-inf, inf]
        # u_s: [0, inf]
        bounds: list[Tuple[Optional[float], Optional[float]]] = []
        for _ in range(n_assets):
            bounds.append((0.0, constraints.max_single_asset_weight))
        bounds.append((-1e9, 1e9))  # gamma (unbounded VaR)
        for _ in range(n_scens):
            bounds.append((0.0, None))  # u_s >= 0

        # Equality Constraints:
        # sum_i x_i == 1.0 - min_cash_reserve_weight
        eq_rows = []
        eq_b = []
        budget_row = [0.0] * n_vars
        for i in range(n_assets):
            budget_row[i] = 1.0
        eq_rows.append(budget_row)
        eq_b.append(1.0 - constraints.min_cash_reserve_weight)

        A_eq = np.array(eq_rows, dtype=np.float64)
        b_eq = np.array(eq_b, dtype=np.float64)

        # Inequality Constraints:
        ub_rows: list[list[float]] = []
        ub_b: list[float] = []
        labels: list[str] = []

        # 1. Rockafellar Scenario Loss Constraints:
        # -sum_i x_i * r_{is} - gamma - u_s <= 0
        # for s = 1..S
        for s in range(n_scens):
            row = [0.0] * n_vars
            for i in range(n_assets):
                row[i] = -float(scenario_returns[i, s])
            row[n_assets] = -1.0  # -gamma
            row[n_assets + 1 + s] = -1.0  # -u_s
            ub_rows.append(row)
            ub_b.append(0.0)
            labels.append(f"Scenario loss upper bound s={s}")

        # 2. Target return constraint (if MINIMIZE_CVAR_TARGET_RETURN):
        # -sum_i x_i * E[r_i] <= -target_return
        if objective == OptimizationObjective.MINIMIZE_CVAR_TARGET_RETURN:
            if target_return is None:
                target_return = float(np.mean(expected_returns))
            ret_row = [0.0] * n_vars
            for i in range(n_assets):
                ret_row[i] = -float(expected_returns[i])
            ub_rows.append(ret_row)
            ub_b.append(-float(target_return))
            labels.append(f"Target expected return >= {target_return:.4f}")

        # 3. CVaR Budget constraint (if MAXIMIZE_RETURN_CVAR_BUDGET):
        # gamma + 1/(1 - alpha) * sum p_s * u_s <= cvar_budget
        if objective == OptimizationObjective.MAXIMIZE_RETURN_CVAR_BUDGET:
            if cvar_budget is None:
                cvar_budget = 0.15  # Default 15% CVaR risk ceiling
            cvar_row = [0.0] * n_vars
            cvar_row[n_assets] = 1.0
            for s in range(n_scens):
                cvar_row[n_assets + 1 + s] = float(scenario_probabilities[s] / (1.0 - alpha))
            ub_rows.append(cvar_row)
            ub_b.append(float(cvar_budget))
            labels.append(f"CVaR Risk Budget <= {cvar_budget:.4f}")

        # 4. Domain & Portfolio Linear Constraints:
        A_domain, b_domain, domain_labels = build_linear_constraint_matrices(
            assets=assets,
            constraints=constraints,
            total_capital_cents=total_capital_cents,
        )
        for r_idx in range(len(b_domain)):
            full_row = [0.0] * n_vars
            for c_idx in range(n_assets):
                full_row[c_idx] = float(A_domain[r_idx, c_idx])
            ub_rows.append(full_row)
            ub_b.append(float(b_domain[r_idx]))
            labels.append(domain_labels[r_idx])

        A_ub = np.array(ub_rows, dtype=np.float64)
        b_ub = np.array(ub_b, dtype=np.float64)

        return PortfolioSolverBackend.solve_linear_cvar(
            c_obj=c_obj,
            A_ub=A_ub,
            b_ub=b_ub,
            A_eq=A_eq,
            b_eq=b_eq,
            bounds=bounds,
            n_assets=n_assets,
            n_scenarios=n_scens,
            alpha=alpha,
            scenario_returns=scenario_returns,
            scenario_probs=scenario_probabilities,
            constraint_labels=labels,
            backend=backend,
            time_limit_seconds=time_limit_seconds,
        )
