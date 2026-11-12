# GLITTERS Engine 08: Portfolio Optimization & Investment Intelligence

**Package**: `glitters-portfolio-intelligence` (`glitters_portfolio_intelligence`)
**Version**: `0.8.0`
**License**: Proprietary — GLITTERS Algorithmic Platform
**Integration**: `glitters_contracts` v1.1.0

---

## 1. Overview & Mathematical Core

`glitters_portfolio_intelligence` is an isolated operations research and quantitative investment platform engineered for real estate portfolio allocation in Kenya.

### Key Capabilities
1. **Rockafellar-Uryasev (2000) Mean-CVaR Optimization**:
   Linear programming formulation minimizing Conditional Value-at-Risk ($CVaR_\alpha$) or maximizing expected return under explicit risk budgets.
2. **Exact Monetary Precision**:
   Balance sheet obligations, rental cash flows, CapEx, debt service, and disbursements are calculated in integer KES minor units (cents), eliminating floating-point drift.
3. **Multiplicative Non-Double-Counting Cash Flow Model**:
   $$EGI = GPR \times (1 - v) \times (1 - c) + OI$$
   Credit loss is evaluated strictly on occupied units.
4. **9 Canonical Reproducible Macroeconomic & Real-Estate Stress Scenarios**:
   Full provenance tracking distinguishing `HISTORICAL_OBSERVATION`, `STATISTICAL_FORECAST`, `EXPERT_ASSUMPTION`, and `SYNTHETIC_SCENARIO`.
5. **Independent 1-D Empirical CVaR Verifier**:
   Verifies LP solver results using exact 1-D convex optimization handling discrete atoms, repeated losses, and fractional probability mass at VaR.
6. **Multi-Period Trajectory Optimization**:
   Dynamic rebalancing across horizon $T$ preventing simultaneous buy/sell actions, enforcing minimum liquidity reserves, and modeling renovation downtime.
7. **Certified Multi-Solver Architecture**:
   Evaluates HiGHS (SciPy), and HiGHS, SCIP, CBC, GLOP (OR-Tools) with explicit optimality status verification.
8. **Fiduciary Governance Gate**:
   Advisory recommendations only. Emits `PortfolioInvestmentBridge` with `HumanApprovalRequirement` for binding committee approvals.

---

## 2. Installation & Quickstart

```bash
pip install dist/glitters_portfolio_intelligence-0.8.0-py3-none-any.whl
```

### Basic Usage

```python
from glitters_portfolio_intelligence import (
    PropertyAsset,
    Portfolio,
    AssetCategory,
    GeographicSubmarket,
    PortfolioConstraints,
    RockafellarMeanCVaROptimizer,
    ScenarioStressEngine,
    OptimizationObjective,
)

# 1. Define Property Assets
asset = PropertyAsset(
    asset_id="PROP-WESTLANDS-01",
    name="Westlands Commercial Tower",
    category=AssetCategory.COMMERCIAL_OFFICE,
    submarket=GeographicSubmarket.WESTLANDS,
    acquisition_valuation_cents=100_000_000_00,
    current_valuation_cents=120_000_000_00,
    monthly_gross_potential_rent_cents=1_000_000_00,
    monthly_operating_expense_cents=200_000_00,
    annual_baseline_vacancy_rate=0.08,
    annual_baseline_credit_loss_rate=0.03,
    annual_debt_service_cents=3_000_000_00,
    current_loan_balance_cents=30_000_000_00,
    annual_routine_maintenance_cents=500_000_00,
    planned_capex_cents=1_000_000_00,
)

# 2. Evaluate Scenario Returns
ret_matrix, probs, scens = ScenarioStressEngine.evaluate_portfolio_scenario_returns([asset])

# 3. Optimize Portfolio
constraints = PortfolioConstraints(
    min_cash_reserve_weight=0.05,
    max_single_asset_weight=0.50,
)
result = RockafellarMeanCVaROptimizer.optimize(
    assets=[asset],
    scenario_returns=ret_matrix,
    scenario_probabilities=probs,
    constraints=constraints,
    objective=OptimizationObjective.SCALARIZED_RISK_RETURN,
    alpha=0.95,
)

print(f"Optimal Expected Return: {result.expected_return:.2%}")
print(f"Portfolio CVaR_95: {result.cvar_alpha:.2%}")
```
