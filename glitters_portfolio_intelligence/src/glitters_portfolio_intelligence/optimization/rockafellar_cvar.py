from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional, Sequence, Tuple
import numpy as np

from ..domain.entities import PropertyAsset
from ..domain.enums import OptimizationObjective, OptimizationStatus, SolverBackendType, UnsupportedSolverConfigurationError
from ..scenarios.cvar_verifier import CVaREmpiricalVerifier
from ..scenarios.validation import validate_scenario_distribution
from .constraints import PortfolioConstraints, build_linear_constraint_matrices
from .solver_backends import PortfolioSolverBackend, SolverExecutionResult


class RockafellarMeanCVaROptimizer:
    """
    Rockafellar-Uryasev (2000) Mean-CVaR Linear and Mixed-Integer Programming Optimizer.
    Solves for optimal portfolio weights x in [0, 1]^N, Value-at-Risk gamma,
    scenario excess loss variables u in R_+^S, optional turnover deviation variables,
    and optional binary selection variables for cardinality and minimum holding constraints.

    Consistent Return and Risk Accounting Convention:
      - All reported expected returns are NET of transaction costs:
          E[R_net] = E[R_gross] - C_tx, where C_tx = sum_i (c_buy * delta_i^+ + c_sell * delta_i^-)
      - Net scenario losses reflect deterministic transaction fees:
          L_{s, net} = -sum_i x_i * r_{is} + C_tx
      - Net CVaR represents the Conditional Value-at-Risk of the net loss distribution L_{s, net}.
      - Both net expected return and net CVaR are independently recomputed from the final weights
        using the discrete empirical CVaR verifier across all three objective modes.
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
        Formulates and executes the Rockafellar-Uryasev LP or MILP.
        Decision variables layout:
          - x_1 ... x_N: portfolio asset weights
          - gamma: Value-at-Risk threshold
          - u_1 ... u_S: scenario excess loss variables
          - delta_1^+ ... delta_N^+: buy deviations (if turnover active)
          - delta_1^- ... delta_N^-: sell deviations (if turnover active)
          - z_1 ... z_N: binary holding indicators in {0, 1} (if MILP active)
        """
        # Validate scenario distribution strictly
        scenario_returns, scenario_probabilities = validate_scenario_distribution(
            scenario_returns, scenario_probabilities, alpha=alpha
        )

        n_assets = len(assets)
        n_scens = len(scenario_probabilities)

        if constraints.initial_weights is not None:
            if len(constraints.initial_weights) != n_assets:
                raise ValueError(
                    f"initial_weights length ({len(constraints.initial_weights)}) must match number of assets ({n_assets})"
                )

        # Enforce backend validation for MILP
        has_milp = constraints.requires_milp
        if has_milp:
            if backend in (SolverBackendType.HIGHS_SCIPY, SolverBackendType.GLOP_ORTOOLS):
                raise UnsupportedSolverConfigurationError(
                    f"Solver backend {backend.value} does not support Mixed-Integer Linear Programming (MILP) "
                    "required for cardinality or minimum holding weight constraints. "
                    "Use SCIP_ORTOOLS, HIGHS_ORTOOLS, or CBC_ORTOOLS."
                )

        has_turnover = constraints.requires_turnover_variables

        # Determine variable indices
        # Base: x (0..N-1), gamma (N), u (N+1..N+S)
        n_base = n_assets + 1 + n_scens
        gamma_idx = n_assets
        u_idx_start = n_assets + 1

        buy_idx_start = n_base if has_turnover else -1
        sell_idx_start = (n_base + n_assets) if has_turnover else -1
        n_with_turnover = (n_base + 2 * n_assets) if has_turnover else n_base

        z_idx_start = n_with_turnover if has_milp else -1
        n_vars = (n_with_turnover + n_assets) if has_milp else n_with_turnover

        var_types = ["C"] * n_vars
        if has_milp:
            for i in range(n_assets):
                var_types[z_idx_start + i] = "B"

        # Expected returns per asset
        expected_returns = np.sum(scenario_returns * scenario_probabilities, axis=1)

        # Objective vector c_obj
        c_obj = np.zeros(n_vars, dtype=np.float64)

        c_buy = constraints.buy_transaction_cost_rate if has_turnover else 0.0
        c_sell = constraints.sell_transaction_cost_rate if has_turnover else 0.0

        if objective == OptimizationObjective.MINIMIZE_CVAR_TARGET_RETURN:
            # Objective: gamma + 1/(1 - alpha) * sum p_s * u_s (Minimize Net CVaR)
            c_obj[gamma_idx] = 1.0
            c_obj[u_idx_start : u_idx_start + n_scens] = scenario_probabilities / (1.0 - alpha)

        elif objective == OptimizationObjective.MAXIMIZE_RETURN_CVAR_BUDGET:
            # Objective: -E[R_net] = -sum x_i * E[r_i] + sum (c_buy delta_i^+ + c_sell delta_i^-)
            c_obj[:n_assets] = -expected_returns
            if has_turnover:
                for i in range(n_assets):
                    c_obj[buy_idx_start + i] = c_buy
                    c_obj[sell_idx_start + i] = c_sell

        elif objective == OptimizationObjective.SCALARIZED_RISK_RETURN:
            # Objective: lambda * Net_CVaR - (1 - lambda) * Net_E[R]
            c_obj[:n_assets] = -(1.0 - risk_aversion_lambda) * expected_returns
            c_obj[gamma_idx] = risk_aversion_lambda
            c_obj[u_idx_start : u_idx_start + n_scens] = (
                risk_aversion_lambda * scenario_probabilities / (1.0 - alpha)
            )
            if has_turnover:
                for i in range(n_assets):
                    c_obj[buy_idx_start + i] = (1.0 - risk_aversion_lambda) * c_buy
                    c_obj[sell_idx_start + i] = (1.0 - risk_aversion_lambda) * c_sell

        # Variable Bounds:
        bounds: list[Tuple[Optional[float], Optional[float]]] = []
        for _ in range(n_assets):
            bounds.append((0.0, constraints.max_single_asset_weight))
        bounds.append((-1e9, 1e9))  # gamma (unbounded VaR)
        for _ in range(n_scens):
            bounds.append((0.0, None))  # u_s >= 0

        if has_turnover:
            for _ in range(n_assets):
                bounds.append((0.0, 1.0))  # delta_i^+
            for _ in range(n_assets):
                bounds.append((0.0, 1.0))  # delta_i^-

        if has_milp:
            for _ in range(n_assets):
                bounds.append((0.0, 1.0))  # z_i in {0, 1}

        # Equality Constraints:
        eq_rows: list[list[float]] = []
        eq_b: list[float] = []

        # 1. Budget Equality: sum x_i + sum(c_buy delta^+ + c_sell delta^-) == 1 - cash_reserve
        budget_row = [0.0] * n_vars
        for i in range(n_assets):
            budget_row[i] = 1.0
        if has_turnover:
            for i in range(n_assets):
                budget_row[buy_idx_start + i] = c_buy
                budget_row[sell_idx_start + i] = c_sell
        eq_rows.append(budget_row)
        eq_b.append(1.0 - constraints.min_cash_reserve_weight)

        # 2. Turnover Definition Equalities: x_i - delta_i^+ + delta_i^- == w_i^(0)
        if has_turnover and constraints.initial_weights is not None:
            for i in range(n_assets):
                turn_row = [0.0] * n_vars
                turn_row[i] = 1.0
                turn_row[buy_idx_start + i] = -1.0
                turn_row[sell_idx_start + i] = 1.0
                eq_rows.append(turn_row)
                eq_b.append(float(constraints.initial_weights[i]))

        A_eq = np.array(eq_rows, dtype=np.float64)
        b_eq = np.array(eq_b, dtype=np.float64)

        # Inequality Constraints:
        ub_rows: list[list[float]] = []
        ub_b: list[float] = []
        labels: list[str] = []

        # 1. Rockafellar Scenario Loss Constraints:
        # L_{s, net} - gamma - u_s <= 0
        # <=> -sum_i x_i * r_{is} + sum_i (c_buy delta_i^+ + c_sell delta_i^-) - gamma - u_s <= 0
        for s in range(n_scens):
            row = [0.0] * n_vars
            for i in range(n_assets):
                row[i] = -float(scenario_returns[i, s])
            if has_turnover:
                for i in range(n_assets):
                    row[buy_idx_start + i] = c_buy
                    row[sell_idx_start + i] = c_sell
            row[gamma_idx] = -1.0
            row[u_idx_start + s] = -1.0
            ub_rows.append(row)
            ub_b.append(0.0)
            labels.append(f"Scenario loss upper bound s={s}")

        # 2. Target return constraint (if MINIMIZE_CVAR_TARGET_RETURN):
        # -E[R_net] <= -target_return
        # <=> -sum_i x_i * E[r_i] + sum_i (c_buy delta_i^+ + c_sell delta_i^-) <= -target_return
        if objective == OptimizationObjective.MINIMIZE_CVAR_TARGET_RETURN:
            if target_return is None:
                target_return = float(np.mean(expected_returns))
            ret_row = [0.0] * n_vars
            for i in range(n_assets):
                ret_row[i] = -float(expected_returns[i])
            if has_turnover:
                for i in range(n_assets):
                    ret_row[buy_idx_start + i] = c_buy
                    ret_row[sell_idx_start + i] = c_sell
            ub_rows.append(ret_row)
            ub_b.append(-float(target_return))
            labels.append(f"Target expected return >= {target_return:.4f}")

        # 3. CVaR Budget constraint (if MAXIMIZE_RETURN_CVAR_BUDGET):
        # Net CVaR <= cvar_budget
        if objective == OptimizationObjective.MAXIMIZE_RETURN_CVAR_BUDGET:
            if cvar_budget is None:
                cvar_budget = 0.15
            cvar_row = [0.0] * n_vars
            cvar_row[gamma_idx] = 1.0
            for s in range(n_scens):
                cvar_row[u_idx_start + s] = float(scenario_probabilities[s] / (1.0 - alpha))
            ub_rows.append(cvar_row)
            ub_b.append(float(cvar_budget))
            labels.append(f"CVaR Risk Budget <= {cvar_budget:.4f}")

        # 4. Maximum Turnover Rate Constraint: sum_i (delta_i^+ + delta_i^-) <= 2 * max_turnover_rate
        if has_turnover and constraints.max_turnover_rate is not None:
            to_row = [0.0] * n_vars
            for i in range(n_assets):
                to_row[buy_idx_start + i] = 1.0
                to_row[sell_idx_start + i] = 1.0
            ub_rows.append(to_row)
            ub_b.append(float(2.0 * constraints.max_turnover_rate))
            labels.append(f"Max turnover rate <= {constraints.max_turnover_rate:.4f}")

        # 5. Domain & Portfolio Linear Constraints:
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

        # 6. Mixed-Integer Holding & Cardinality Constraints:
        if has_milp:
            # x_i <= max_single_asset_weight * z_i  <=>  x_i - max_single_asset_weight * z_i <= 0
            for i in range(n_assets):
                card_ub_row = [0.0] * n_vars
                card_ub_row[i] = 1.0
                card_ub_row[z_idx_start + i] = -float(constraints.max_single_asset_weight)
                ub_rows.append(card_ub_row)
                ub_b.append(0.0)
                labels.append(f"MILP holding upper bound asset {assets[i].asset_id}")

            # -x_i + min_holding_weight * z_i <= 0
            if constraints.min_holding_weight > 0.0:
                for i in range(n_assets):
                    card_lb_row = [0.0] * n_vars
                    card_lb_row[i] = -1.0
                    card_lb_row[z_idx_start + i] = float(constraints.min_holding_weight)
                    ub_rows.append(card_lb_row)
                    ub_b.append(0.0)
                    labels.append(f"MILP min holding weight asset {assets[i].asset_id}")

            # sum_i z_i <= max_cardinality
            if constraints.max_cardinality is not None:
                card_sum_row = [0.0] * n_vars
                for i in range(n_assets):
                    card_sum_row[z_idx_start + i] = 1.0
                ub_rows.append(card_sum_row)
                ub_b.append(float(constraints.max_cardinality))
                labels.append(f"MILP max portfolio cardinality <= {constraints.max_cardinality}")

        A_ub = np.array(ub_rows, dtype=np.float64)
        b_ub = np.array(ub_b, dtype=np.float64)

        raw_result = PortfolioSolverBackend.solve_linear_cvar(
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
            var_types=var_types,
            constraint_labels=labels,
            backend=backend,
            time_limit_seconds=time_limit_seconds,
        )

        if not raw_result.is_optimal and raw_result.status != OptimizationStatus.FEASIBLE:
            return raw_result

        # Independent calculation of net expected return and empirical net CVaR/VaR
        x_sol = raw_result.optimal_weights
        gross_return = float(np.sum(x_sol * expected_returns))

        if has_turnover and constraints.initial_weights is not None:
            w0 = np.array(constraints.initial_weights, dtype=np.float64)
            d_plus = np.maximum(0.0, x_sol - w0)
            d_minus = np.maximum(0.0, w0 - x_sol)
            tx_cost = float(np.sum(c_buy * d_plus + c_sell * d_minus))
        else:
            tx_cost = 0.0

        net_return = gross_return - tx_cost

        # Net scenario loss vector: L_{s, net} = - (x @ scenario_returns[:, s]) + tx_cost
        port_scen_gross_ret = x_sol @ scenario_returns
        port_scen_net_losses = -port_scen_gross_ret + tx_cost

        cvar_diag = CVaREmpiricalVerifier.compute_empirical_cvar_from_losses(
            scenario_losses=port_scen_net_losses,
            scenario_probabilities=scenario_probabilities,
            alpha=alpha,
        )

        return replace(
            raw_result,
            expected_return=net_return,
            cvar_alpha=cvar_diag.cvar_alpha,
            var_alpha=cvar_diag.var_alpha,
        )
