from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Mapping, Optional, Sequence, Tuple
import numpy as np

from ..domain.entities import PropertyAsset
from ..domain.enums import AssetCategory, GeographicSubmarket
from ..cashflows.accounting import compute_asset_annual_cashflow


@dataclass(frozen=True, slots=True)
class PortfolioConstraints:
    """
    Comprehensive portfolio linear and mixed-integer constraints.
    Validates all parameters strictly; no declared constraint is silently ignored.
    Enforces finite values and legitimate boundaries for all constraint fields.
    """
    min_cash_reserve_weight: float = 0.05
    max_single_asset_weight: float = 0.35
    max_portfolio_ltv: float = 0.70
    min_portfolio_dscr: float = 1.25
    max_capex_budget_cents: Optional[int] = None
    max_geographic_weights: Mapping[GeographicSubmarket, float] = field(default_factory=dict)
    max_category_weights: Mapping[AssetCategory, float] = field(default_factory=dict)
    initial_weights: Optional[Tuple[float, ...]] = None
    max_turnover_rate: Optional[float] = None
    buy_transaction_cost_rate: float = 0.06
    sell_transaction_cost_rate: float = 0.04
    min_holding_weight: float = 0.0
    max_cardinality: Optional[int] = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.min_cash_reserve_weight) or not (0.0 <= self.min_cash_reserve_weight < 1.0):
            raise ValueError(f"min_cash_reserve_weight must be a finite float in [0, 1), got {self.min_cash_reserve_weight}")

        if not math.isfinite(self.max_single_asset_weight) or not (0.0 < self.max_single_asset_weight <= 1.0):
            raise ValueError(f"max_single_asset_weight must be a finite float in (0, 1], got {self.max_single_asset_weight}")

        if not math.isfinite(self.max_portfolio_ltv) or self.max_portfolio_ltv < 0.0:
            raise ValueError(f"max_portfolio_ltv must be a finite non-negative float: {self.max_portfolio_ltv}")

        if not math.isfinite(self.min_portfolio_dscr) or self.min_portfolio_dscr < 0.0:
            raise ValueError(f"min_portfolio_dscr must be a finite non-negative float: {self.min_portfolio_dscr}")

        if self.max_capex_budget_cents is not None:
            if not isinstance(self.max_capex_budget_cents, int) or self.max_capex_budget_cents < 0:
                raise ValueError(f"max_capex_budget_cents must be an integer >= 0, got {self.max_capex_budget_cents}")

        if not math.isfinite(self.buy_transaction_cost_rate) or not (0.0 <= self.buy_transaction_cost_rate <= 1.0):
            raise ValueError(f"buy_transaction_cost_rate must be a finite float in [0, 1]: {self.buy_transaction_cost_rate}")

        if not math.isfinite(self.sell_transaction_cost_rate) or not (0.0 <= self.sell_transaction_cost_rate <= 1.0):
            raise ValueError(f"sell_transaction_cost_rate must be a finite float in [0, 1]: {self.sell_transaction_cost_rate}")

        if not math.isfinite(self.min_holding_weight) or not (0.0 <= self.min_holding_weight <= self.max_single_asset_weight):
            raise ValueError(
                f"min_holding_weight ({self.min_holding_weight}) must be a finite float in [0, max_single_asset_weight ({self.max_single_asset_weight})]"
            )

        if self.max_cardinality is not None:
            if not isinstance(self.max_cardinality, int) or self.max_cardinality < 1:
                raise ValueError(f"max_cardinality must be an integer >= 1, got {self.max_cardinality}")

        for submarket, w in self.max_geographic_weights.items():
            if not isinstance(submarket, GeographicSubmarket):
                raise ValueError(f"Invalid geographic submarket key: {submarket}")
            if not math.isfinite(w) or not (0.0 <= w <= 1.0):
                raise ValueError(f"Geographic weight for {submarket} must be a finite float in [0, 1], got {w}")

        for category, w in self.max_category_weights.items():
            if not isinstance(category, AssetCategory):
                raise ValueError(f"Invalid asset category key: {category}")
            if not math.isfinite(w) or not (0.0 <= w <= 1.0):
                raise ValueError(f"Category weight for {category} must be a finite float in [0, 1], got {w}")

        if self.max_turnover_rate is not None:
            if not math.isfinite(self.max_turnover_rate) or self.max_turnover_rate < 0.0:
                raise ValueError(f"max_turnover_rate must be a finite non-negative float: {self.max_turnover_rate}")
            if self.initial_weights is None:
                raise ValueError("max_turnover_rate requires initial_weights to be specified")

        if self.initial_weights is not None:
            if not self.initial_weights:
                raise ValueError("initial_weights cannot be empty when provided")
            for w in self.initial_weights:
                if not math.isfinite(w) or w < 0.0:
                    raise ValueError(f"Initial weight must be finite and >= 0, got {w}")
            sum_w = sum(self.initial_weights)
            if sum_w > 1.0 + 1e-4:
                raise ValueError(f"Sum of initial weights cannot exceed 1.0, got {sum_w:.4f}")

    @property
    def requires_milp(self) -> bool:
        """Returns True if constraints require integer/binary decision variables."""
        return (self.max_cardinality is not None) or (self.min_holding_weight > 0.0)

    @property
    def requires_turnover_variables(self) -> bool:
        """Returns True if buy/sell turnover variables are required."""
        return (self.initial_weights is not None) and (
            self.max_turnover_rate is not None
            or self.buy_transaction_cost_rate > 0.0
            or self.sell_transaction_cost_rate > 0.0
        )


def build_linear_constraint_matrices(
    assets: Sequence[PropertyAsset],
    constraints: PortfolioConstraints,
    total_capital_cents: int,
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """
    Builds the linear inequality constraint matrix A_ub @ x <= b_ub for portfolio weights x.
    Uses exact cash-flow accounting for DSCR consistency.
    Enforces explicit zero CapEx budget as a legitimate upper bound.

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
    # Uses exact compute_asset_annual_cashflow to guarantee 100% consistency with accounting statements
    dscr_min = constraints.min_portfolio_dscr
    dscr_coeffs = []
    for a in assets:
        stmt = compute_asset_annual_cashflow(a)
        noi = stmt.net_operating_income_cents
        debt = stmt.debt_service_cents
        dscr_coeffs.append(float(dscr_min * debt - noi) / 100.0)
    rows.append(dscr_coeffs)
    b_vals.append(0.0)
    labels.append(f"Portfolio DSCR >= {constraints.min_portfolio_dscr:.2f}")

    # 4. CapEx budget constraint: sum_i x_i * (total_capital_cents / val_i) * capex_i <= max_capex_budget_cents
    # Zero budget is enforced as a legitimate upper bound
    if constraints.max_capex_budget_cents is not None and constraints.max_capex_budget_cents >= 0:
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
