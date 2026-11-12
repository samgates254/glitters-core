from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Optional, Sequence

from ..domain.entities import PropertyAsset, Portfolio
from ..domain.money import Money


@dataclass(frozen=True, slots=True)
class AnnualCashFlowStatement:
    """
    Exact property or portfolio-level annual cash flow statement in integer KES minor units (cents).
    Strictly prevents double counting of vacancy and credit/collection losses.
    """
    gross_potential_rent_cents: int
    vacancy_loss_cents: int
    credit_loss_cents: int
    other_income_cents: int
    effective_gross_income_cents: int
    operating_expenses_cents: int
    routine_maintenance_cents: int
    net_operating_income_cents: int
    debt_service_cents: int
    planned_capex_cents: int
    cash_flow_after_debt_service_cents: int

    def __post_init__(self) -> None:
        # Mathematical invariant checks
        expected_egi = self.gross_potential_rent_cents - self.vacancy_loss_cents - self.credit_loss_cents + self.other_income_cents
        if self.effective_gross_income_cents != expected_egi:
            raise ValueError(f"EGI mismatch: computed={self.effective_gross_income_cents} != expected={expected_egi}")
        expected_noi = self.effective_gross_income_cents - self.operating_expenses_cents - self.routine_maintenance_cents
        if self.net_operating_income_cents != expected_noi:
            raise ValueError(f"NOI mismatch: computed={self.net_operating_income_cents} != expected={expected_noi}")
        expected_cfads = self.net_operating_income_cents - self.debt_service_cents - self.planned_capex_cents
        if self.cash_flow_after_debt_service_cents != expected_cfads:
            raise ValueError(f"CFADS mismatch: computed={self.cash_flow_after_debt_service_cents} != expected={expected_cfads}")

    @property
    def dscr(self) -> Optional[float]:
        """
        Debt Service Coverage Ratio (DSCR = NOI / DebtService).
        Returns None if unlevered (debt service == 0).
        """
        if self.debt_service_cents <= 0:
            return None
        return self.net_operating_income_cents / self.debt_service_cents

    @property
    def is_unlevered(self) -> bool:
        return self.debt_service_cents == 0


def compute_asset_annual_cashflow(
    asset: PropertyAsset,
    vacancy_rate: Optional[float] = None,
    credit_loss_rate: Optional[float] = None,
    other_income_cents: int = 0,
) -> AnnualCashFlowStatement:
    """
    Computes an exact annual cash-flow statement for a single asset.

    Formula (Non-Double-Counting Multiplicative Model):
      GPR = 12 * monthly_gross_potential_rent_cents
      VacancyLoss = round(GPR * v)
      BilledRent = GPR - VacancyLoss
      CreditLoss = round(BilledRent * c)   # Evaluated strictly on occupied units
      EGI = GPR - VacancyLoss - CreditLoss + other_income
      NOI = EGI - OPEX - RoutineMaintenance
      CFADS = NOI - DebtService - CapEx
    """
    v = asset.annual_baseline_vacancy_rate if vacancy_rate is None else vacancy_rate
    c = asset.annual_baseline_credit_loss_rate if credit_loss_rate is None else credit_loss_rate

    gpr = asset.monthly_gross_potential_rent_cents * 12
    vac_loss = int(Decimal(str(gpr * v)).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN))
    billed_rent = gpr - vac_loss
    credit_loss = int(Decimal(str(billed_rent * c)).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN))
    egi = gpr - vac_loss - credit_loss + other_income_cents

    opex = asset.monthly_operating_expense_cents * 12
    maint = asset.annual_routine_maintenance_cents
    noi = egi - opex - maint

    debt = asset.annual_debt_service_cents
    capex = asset.planned_capex_cents
    cfads = noi - debt - capex

    return AnnualCashFlowStatement(
        gross_potential_rent_cents=gpr,
        vacancy_loss_cents=vac_loss,
        credit_loss_cents=credit_loss,
        other_income_cents=other_income_cents,
        effective_gross_income_cents=egi,
        operating_expenses_cents=opex,
        routine_maintenance_cents=maint,
        net_operating_income_cents=noi,
        debt_service_cents=debt,
        planned_capex_cents=capex,
        cash_flow_after_debt_service_cents=cfads,
    )


def compute_portfolio_annual_cashflow(
    portfolio: Portfolio,
    asset_cashflows: Optional[Sequence[AnnualCashFlowStatement]] = None,
) -> AnnualCashFlowStatement:
    """Rolls up asset cash-flow statements into a portfolio total."""
    if asset_cashflows is None:
        statements = [compute_asset_annual_cashflow(a) for a in portfolio.assets]
    else:
        statements = list(asset_cashflows)

    gpr = sum(s.gross_potential_rent_cents for s in statements)
    vac = sum(s.vacancy_loss_cents for s in statements)
    cred = sum(s.credit_loss_cents for s in statements)
    other = sum(s.other_income_cents for s in statements)
    egi = sum(s.effective_gross_income_cents for s in statements)
    opex = sum(s.operating_expenses_cents for s in statements)
    maint = sum(s.routine_maintenance_cents for s in statements)
    noi = sum(s.net_operating_income_cents for s in statements)
    debt = sum(s.debt_service_cents for s in statements)
    capex = sum(s.planned_capex_cents for s in statements)
    cfads = sum(s.cash_flow_after_debt_service_cents for s in statements)

    return AnnualCashFlowStatement(
        gross_potential_rent_cents=gpr,
        vacancy_loss_cents=vac,
        credit_loss_cents=cred,
        other_income_cents=other,
        effective_gross_income_cents=egi,
        operating_expenses_cents=opex,
        routine_maintenance_cents=maint,
        net_operating_income_cents=noi,
        debt_service_cents=debt,
        planned_capex_cents=capex,
        cash_flow_after_debt_service_cents=cfads,
    )
