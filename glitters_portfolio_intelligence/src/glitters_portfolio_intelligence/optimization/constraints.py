from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional, Sequence, Tuple
import numpy as np

from ..domain.entities import PropertyAsset
from ..domain.enums import AssetCategory, GeographicSubmarket


@dataclass(frozen=True, slots=True)
class PortfolioConstraints:
    """
    Comprehensive portfolio linear and mixed-integer constraints.
    All financial thresholds in integer KES cents or exact ratios.
    """
    min_cash_reserve_weight: float = 0.05
    max_single_asset_weight: float = 0.35
    max_portfolio_ltv: float = 0.70
    min_portfolio_dscr: float = 1.25
    max_capex_budget_cents: Optional[int] = None
    max_geographic_weights: Mapping[GeographicSubmarket, float] = field(default_factory=dict)
    max_category_weights: Mapping[AssetCategory, float] = field(default_factory=dict)
    initial_weights: Optional[Sequence[float]] = None
    max_turnover_rate: Optional[float] = None
    buy_transaction_cost_rate: float = 0.06
    sell_transaction_cost_rate: float = 0.04
    min_holding_weight: float = 0.0  # Threshold for binary cardinality activation
    max_cardinality: Optional[int] = None  # Max number of non-zero assets (requires MILP)


def build_linear_constraint_matrices(
    assets: Sequence[PropertyAsset],
    constraints: PortfolioConstraints,
    total_capital_cents: int,
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """
    Builds the linear inequality constraint matrix A_ub @ x <= b_ub for portfolio weights x.

    Returns:
      A_ub: 2D array of constraint coefficients (n_constraints, n_assets)
      b_ub: 1D array of upper bounds (n_constraints,)
      labels: List of human-readable descriptions for each constraint row
    """
    n_assets = len(assets)
    rows: list[list[float]] = []
    b_vals: list[float] = []
    labels: list[str] = []

    # 1. Single asset maximum weight: x_i <= max_single_asset_weight
    for i, a in enumerate(assets):
        row = [0.0] * n_assets
        row[i] = 1.0
        rows.append(row)
        b_vals.append(constraints.max_single_asset_weight)
        labels.append(f"Max single asset weight: {a.asset_id} <= {constraints.max_single_asset_weight:.2f}")

    # 2. Portfolio maximum LTV: sum_i x_i * LTV_i <= max_portfolio_ltv
    ltv_row = [a.current_ltv for a in assets]
    rows.append(ltv_row)
    b_vals.append(constraints.max_portfolio_ltv)
    labels.append(f"Portfolio LTV <= {constraints.max_portfolio_ltv:.2f}")

    # 3. Minimum portfolio DSCR: sum_i x_i * (DSCR_min * Debt_i - NOI_i) <= 0
    # Equivalent to: sum_i x_i * NOI_i >= DSCR_min * sum_i x_i * Debt_i
    dscr_min = constraints.min_portfolio_dscr
    dscr_coeffs = []
    for a in assets:
        ann_gpr = a.monthly_gross_potential_rent_cents * 12
        vac = int(ann_gpr * a.annual_baseline_vacancy_rate)
        cred = int((ann_gpr - vac) * a.annual_baseline_credit_loss_rate)
        egi = ann_gpr - vac - cred
        opex = a.monthly_operating_expense_cents * 12
        maint = a.annual_routine_maintenance_cents
        noi = egi - opex - maint
        debt = a.annual_debt_service_cents
        # We want: sum x_i * (dscr_min * debt - noi) <= 0
        dscr_coeffs.append(float(dscr_min * debt - noi) / 100.0)
    rows.append(dscr_coeffs)
    b_vals.append(0.0)
    labels.append(f"Portfolio DSCR >= {constraints.min_portfolio_dscr:.2f}")

    # 4. CapEx budget constraint: sum_i x_i * (total_capital_cents / val_i) * capex_i <= max_capex_budget_cents
    if constraints.max_capex_budget_cents is not None and constraints.max_capex_budget_cents > 0:
        capex_row = []
        for a in assets:
            val = max(1, a.current_valuation_cents)
            capex_frac = a.planned_capex_cents / val
            capex_row.append(capex_frac * total_capital_cents)
        rows.append(capex_row)
        b_vals.append(float(constraints.max_capex_budget_cents))
        labels.append(f"CapEx Budget <= KES {constraints.max_capex_budget_cents / 100:,.2f}")

    # 5. Geographic concentration constraints
    for submarket, max_w in constraints.max_geographic_weights.items():
        geo_row = [1.0 if a.submarket == submarket else 0.0 for a in assets]
        if sum(geo_row) > 0:
            rows.append(geo_row)
            b_vals.append(max_w)
            labels.append(f"Submarket limit: {submarket.value} <= {max_w:.2f}")

    # 6. Asset category concentration constraints
    for category, max_w in constraints.max_category_weights.items():
        cat_row = [1.0 if a.category == category else 0.0 for a in assets]
        if sum(cat_row) > 0:
            rows.append(cat_row)
            b_vals.append(max_w)
            labels.append(f"Category limit: {category.value} <= {max_w:.2f}")

    A_ub = np.array(rows, dtype=np.float64) if rows else np.zeros((0, n_assets), dtype=np.float64)
    b_ub = np.array(b_vals, dtype=np.float64) if b_vals else np.zeros((0,), dtype=np.float64)
    return A_ub, b_ub, labels
