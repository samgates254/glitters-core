from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Sequence, Tuple
from uuid import UUID, uuid4

from .enums import AssetCategory, GeographicSubmarket
from .money import Money


@dataclass(frozen=True, slots=True)
class PropertyAsset:
    """
    Representation of an individual real-estate investment asset.
    All balance sheet obligations and cash flows are integer KES minor units (cents).
    """
    asset_id: str
    name: str
    category: AssetCategory
    submarket: GeographicSubmarket
    acquisition_valuation_cents: int
    current_valuation_cents: int
    monthly_gross_potential_rent_cents: int
    monthly_operating_expense_cents: int
    annual_baseline_vacancy_rate: float
    annual_baseline_credit_loss_rate: float
    annual_debt_service_cents: int
    current_loan_balance_cents: int
    annual_routine_maintenance_cents: int
    planned_capex_cents: int
    acquisition_cost_rate: float = 0.06
    disposal_cost_rate: float = 0.04
    renovation_downtime_months: int = 3
    liquidity_discount_rate: float = 0.05
    minimum_holding_months: int = 12
    initial_ownership_fraction: float = 1.0

    def __post_init__(self) -> None:
        if self.acquisition_valuation_cents < 0:
            raise ValueError(f"acquisition_valuation_cents must be >= 0: {self.acquisition_valuation_cents}")
        if self.current_valuation_cents < 0:
            raise ValueError(f"current_valuation_cents must be >= 0: {self.current_valuation_cents}")
        if not (0.0 <= self.annual_baseline_vacancy_rate <= 1.0):
            raise ValueError(f"annual_baseline_vacancy_rate must be in [0, 1]: {self.annual_baseline_vacancy_rate}")
        if not (0.0 <= self.annual_baseline_credit_loss_rate <= 1.0):
            raise ValueError(f"annual_baseline_credit_loss_rate must be in [0, 1]: {self.annual_baseline_credit_loss_rate}")
        if self.current_loan_balance_cents < 0:
            raise ValueError(f"current_loan_balance_cents must be >= 0: {self.current_loan_balance_cents}")
        if self.annual_debt_service_cents < 0:
            raise ValueError(f"annual_debt_service_cents must be >= 0: {self.annual_debt_service_cents}")
        if not (0.0 <= self.initial_ownership_fraction <= 1.0):
            raise ValueError(f"initial_ownership_fraction must be in [0, 1]: {self.initial_ownership_fraction}")

    @property
    def current_ltv(self) -> float:
        """Loan-to-Value ratio."""
        if self.current_valuation_cents == 0:
            return 0.0 if self.current_loan_balance_cents == 0 else 1.0
        return self.current_loan_balance_cents / self.current_valuation_cents

    @property
    def net_equity_cents(self) -> int:
        """Current equity after hypothetical debt settlement and disposal frictions."""
        disposal_friction_cents = int(self.current_valuation_cents * self.disposal_cost_rate)
        liquidity_haircut_cents = int(self.current_valuation_cents * self.liquidity_discount_rate)
        return max(0, self.current_valuation_cents - self.current_loan_balance_cents - disposal_friction_cents - liquidity_haircut_cents)


@dataclass(frozen=True, slots=True)
class Portfolio:
    """Multi-asset real estate portfolio container."""
    portfolio_id: str
    tenant_org_id: str
    assets: Tuple[PropertyAsset, ...]
    cash_reserves_cents: int
    created_at_utc: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if self.cash_reserves_cents < 0:
            raise ValueError(f"cash_reserves_cents cannot be negative: {self.cash_reserves_cents}")

    @property
    def total_asset_valuation_cents(self) -> int:
        return sum(a.current_valuation_cents for a in self.assets)

    @property
    def total_portfolio_value_cents(self) -> int:
        return self.total_asset_valuation_cents + self.cash_reserves_cents

    @property
    def total_debt_cents(self) -> int:
        return sum(a.current_loan_balance_cents for a in self.assets)

    @property
    def portfolio_ltv(self) -> float:
        total_val = self.total_asset_valuation_cents
        if total_val == 0:
            return 0.0
        return self.total_debt_cents / total_val

    @property
    def asset_count(self) -> int:
        return len(self.assets)
