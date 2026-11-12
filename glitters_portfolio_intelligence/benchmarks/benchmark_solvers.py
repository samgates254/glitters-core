from __future__ import annotations

import time
import numpy as np

from glitters_portfolio_intelligence.domain.entities import PropertyAsset
from glitters_portfolio_intelligence.domain.enums import (
    AssetCategory,
    GeographicSubmarket,
    OptimizationObjective,
    SolverBackendType,
)
from glitters_portfolio_intelligence.optimization.constraints import PortfolioConstraints
from glitters_portfolio_intelligence.optimization.rockafellar_cvar import RockafellarMeanCVaROptimizer
from glitters_portfolio_intelligence.optimization.solver_backends import PortfolioSolverBackend
from glitters_portfolio_intelligence.scenarios.stress_engine import ScenarioStressEngine


def generate_synthetic_benchmark_portfolio(n_assets: int) -> list[PropertyAsset]:
    """Generates a reproducible synthetic benchmark portfolio for runtime benchmarking."""
    np.random.seed(42)
    categories = list(AssetCategory)
    submarkets = list(GeographicSubmarket)
    assets = []

    for i in range(n_assets):
        cat = categories[i % len(categories)]
        sub = submarkets[i % len(submarkets)]
        val_cents = int(np.random.uniform(50_000_000, 300_000_000) * 100)
        debt_cents = int(val_cents * np.random.uniform(0.10, 0.50))
        gpr_cents = int(val_cents * np.random.uniform(0.08, 0.12) / 12.0)
        opex_cents = int(gpr_cents * np.random.uniform(0.15, 0.25))

        assets.append(
            PropertyAsset(
                asset_id=f"BENCH-PROP-{i+1:04d}",
                name=f"Benchmark Asset {i+1}",
                category=cat,
                submarket=sub,
                acquisition_valuation_cents=val_cents,
                current_valuation_cents=val_cents,
                monthly_gross_potential_rent_cents=gpr_cents,
                monthly_operating_expense_cents=opex_cents,
                annual_baseline_vacancy_rate=float(np.random.uniform(0.03, 0.10)),
                annual_baseline_credit_loss_rate=float(np.random.uniform(0.01, 0.04)),
                annual_debt_service_cents=int(debt_cents * 0.12),
                current_loan_balance_cents=debt_cents,
                annual_routine_maintenance_cents=int(val_cents * 0.005),
                planned_capex_cents=int(val_cents * 0.01),
            )
        )
    return assets


def run_solver_benchmarks():
    print("=" * 75)
    print("GLITTERS ENGINE 08: SOLVER PERFORMANCE & OPTIMALITY BENCHMARK")
    print("=" * 75)

    asset_counts = [10, 50, 100]
    backends = PortfolioSolverBackend.get_available_backends()
    print(f"Available Backends: {[b.value for b in backends]}")

    constraints = PortfolioConstraints(
        min_cash_reserve_weight=0.05,
        max_single_asset_weight=0.25,
        max_portfolio_ltv=0.65,
        min_portfolio_dscr=1.20,
    )

    for n in asset_counts:
        print(f"\n--- Testing with N = {n} Assets, S = 9 Scenarios ---")
        portfolio = generate_synthetic_benchmark_portfolio(n)
        ret_matrix, probs, _ = ScenarioStressEngine.evaluate_portfolio_scenario_returns(portfolio)

        for b in backends:
            times = []
            res = None
            for _ in range(3):
                t0 = time.perf_counter()
                res = RockafellarMeanCVaROptimizer.optimize(
                    assets=portfolio,
                    scenario_returns=ret_matrix,
                    scenario_probabilities=probs,
                    constraints=constraints,
                    objective=OptimizationObjective.SCALARIZED_RISK_RETURN,
                    alpha=0.95,
                    risk_aversion_lambda=0.50,
                    backend=b,
                )
                times.append(time.perf_counter() - t0)

            avg_time_ms = (sum(times) / len(times)) * 1000.0
            print(
                f"  [{b.value:14s}] Status: {res.status.value:7s} | "
                f"Optimal: {str(res.is_optimal):5s} | "
                f"E[R]: {res.expected_return:6.2%} | "
                f"CVaR_95: {res.cvar_alpha:6.2%} | "
                f"Avg Time: {avg_time_ms:6.2f} ms"
            )


if __name__ == "__main__":
    run_solver_benchmarks()
