"""
GLITTERS ENGINE 08: PORTFOLIO OPTIMIZATION & INVESTMENT INTELLIGENCE (v0.8.0)
CONSOLIDATED STANDALONE REFERENCE IMPLEMENTATION
Auto-generated from canonical modular package: glitters_portfolio_intelligence
Proprietary - GLITTERS Algorithmic Platform
"""
from __future__ import annotations

# ==============================================================================
# MODULE FILE: src/glitters_portfolio_intelligence/domain/enums.py
# ==============================================================================

from enum import Enum


class AssetCategory(str, Enum):
    RESIDENTIAL = "RESIDENTIAL"
    COMMERCIAL_OFFICE = "COMMERCIAL_OFFICE"
    RETAIL = "RETAIL"
    LIGHT_INDUSTRIAL = "LIGHT_INDUSTRIAL"
    LAND_DEVELOPMENT = "LAND_DEVELOPMENT"
    MIXED_USE = "MIXED_USE"


class GeographicSubmarket(str, Enum):
    NAIROBI_CBD = "NAIROBI_CBD"
    WESTLANDS = "WESTLANDS"
    KILIMANI = "KILIMANI"
    KAREN = "KAREN"
    UPPER_HILL = "UPPER_HILL"
    MOMBASA_NYALI = "MOMBASA_NYALI"
    KIAMBU_RUIRU = "KIAMBU_RUIRU"
    MACHAKOS_ATHI_RIVER = "MACHAKOS_ATHI_RIVER"
    KISUMU_CENTRAL = "KISUMU_CENTRAL"
    OTHER_KENYA = "OTHER_KENYA"


class DecisionAction(str, Enum):
    BUY = "BUY"
    HOLD = "HOLD"
    SELL = "SELL"
    RENOVATE = "RENOVATE"
    REFINANCE = "REFINANCE"


class OwnershipRepresentation(str, Enum):
    """Explicit semantic model for investment decisions."""
    FRACTIONAL_CAPITAL_WEIGHT = "FRACTIONAL_CAPITAL_WEIGHT"  # Continuous weights x_i in [0, 1]
    BINARY_WHOLE_ASSET = "BINARY_WHOLE_ASSET"                # Binary indicator z_i in {0, 1}
    DISCRETE_INVESTMENT_LOTS = "DISCRETE_INVESTMENT_LOTS"    # Integer multiples of lot size


class OptimizationObjective(str, Enum):
    MINIMIZE_CVAR_TARGET_RETURN = "MINIMIZE_CVAR_TARGET_RETURN"
    MAXIMIZE_RETURN_CVAR_BUDGET = "MAXIMIZE_RETURN_CVAR_BUDGET"
    SCALARIZED_RISK_RETURN = "SCALARIZED_RISK_RETURN"
    EPSILON_CONSTRAINT = "EPSILON_CONSTRAINT"


class SolverBackendType(str, Enum):
    HIGHS_SCIPY = "HIGHS_SCIPY"
    HIGHS_ORTOOLS = "HIGHS_ORTOOLS"
    SCIP_ORTOOLS = "SCIP_ORTOOLS"
    CBC_ORTOOLS = "CBC_ORTOOLS"
    GLOP_ORTOOLS = "GLOP_ORTOOLS"


class OptimizationStatus(str, Enum):
    """Strict mathematical solver termination status."""
    OPTIMAL = "OPTIMAL"
    FEASIBLE = "FEASIBLE"
    INFEASIBLE = "INFEASIBLE"
    UNBOUNDED = "UNBOUNDED"
    TIME_LIMIT = "TIME_LIMIT"
    UNKNOWN = "UNKNOWN"


class ScenarioProvenanceKind(str, Enum):
    """Epistemic classification of scenario data origin."""
    HISTORICAL_OBSERVATION = "HISTORICAL_OBSERVATION"
    STATISTICAL_FORECAST = "STATISTICAL_FORECAST"
    EXPERT_ASSUMPTION = "EXPERT_ASSUMPTION"
    SYNTHETIC_SCENARIO = "SYNTHETIC_SCENARIO"


class IRRStatus(str, Enum):
    """Diagnostic status for multi-period IRR numerical root finding."""
    CONVERGED = "CONVERGED"
    NO_ROOT_IN_SEARCH_INTERVAL = "NO_ROOT_IN_SEARCH_INTERVAL"
    MULTIPLE_ROOTS_DETECTED = "MULTIPLE_ROOTS_DETECTED"
    ALL_POSITIVE_CASHFLOWS = "ALL_POSITIVE_CASHFLOWS"
    ALL_NEGATIVE_CASHFLOWS = "ALL_NEGATIVE_CASHFLOWS"


class UnsupportedSolverConfigurationError(Exception):
    """Raised when an LP solver backend is requested to solve a MILP or unsupported formulation."""
    pass

# ==============================================================================
# MODULE FILE: src/glitters_portfolio_intelligence/domain/money.py
# ==============================================================================

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN
import math
from typing import Sequence


@dataclass(frozen=True, slots=True)
class Money:
    """
    Exact monetary representation using integer minor units (e.g. KES cents).
    Strictly forbids fractional cents in accounting obligations.
    """
    cents: int
    currency: str = "KES"

    def __post_init__(self) -> None:
        if not isinstance(self.cents, int):
            raise TypeError(f"cents must be an exact integer, got {type(self.cents).__name__}: {self.cents}")

    @classmethod
    def from_major(cls, major_amount: float | Decimal | int, currency: str = "KES") -> Money:
        """Controlled boundary conversion from major units (e.g. KES) to integer minor units (cents)."""
        dec = Decimal(str(major_amount))
        cents = int((dec * Decimal(100)).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN))
        return cls(cents=cents, currency=currency)

    @property
    def major_amount(self) -> Decimal:
        return (Decimal(self.cents) / Decimal(100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)

    def __add__(self, other: Money) -> Money:
        if not isinstance(other, Money) or other.currency != self.currency:
            raise TypeError(f"Cannot add {other} to {self}")
        return Money(cents=self.cents + other.cents, currency=self.currency)

    def __sub__(self, other: Money) -> Money:
        if not isinstance(other, Money) or other.currency != self.currency:
            raise TypeError(f"Cannot subtract {other} from {self}")
        return Money(cents=self.cents - other.cents, currency=self.currency)

    def __mul__(self, scalar: int | float | Decimal) -> Money:
        if isinstance(scalar, int):
            return Money(cents=self.cents * scalar, currency=self.currency)
        dec_scalar = Decimal(str(scalar))
        new_cents = int((Decimal(self.cents) * dec_scalar).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN))
        return Money(cents=new_cents, currency=self.currency)

    def __rmul__(self, scalar: int | float | Decimal) -> Money:
        return self.__mul__(scalar)

    def __neg__(self) -> Money:
        return Money(cents=-self.cents, currency=self.currency)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return False
        return self.cents == other.cents and self.currency == other.currency

    def __lt__(self, other: Money) -> bool:
        if not isinstance(other, Money) or other.currency != self.currency:
            raise TypeError(f"Cannot compare {self} with {other}")
        return self.cents < other.cents

    def __le__(self, other: Money) -> bool:
        return self == other or self < other

    def __gt__(self, other: Money) -> bool:
        return not (self <= other)

    def __ge__(self, other: Money) -> bool:
        return not (self < other)

    def __repr__(self) -> str:
        sign = "-" if self.cents < 0 else ""
        abs_c = abs(self.cents)
        major = abs_c // 100
        minor = abs_c % 100
        return f"{self.currency} {sign}{major:,}.{minor:02d}"


def allocate_cents(total_cents: int, weights: Sequence[float]) -> list[int]:
    """
    Allocates an exact integer monetary amount according to floating-point weights
    using the Hare-Niemeyer (Largest Remainder) method.

    Guarantees:
      1. sum(result) == total_cents exactly (zero leakage).
      2. Each allocation is an exact integer.
      3. Non-negative weights yield non-negative allocations for total_cents >= 0.
    """
    if len(weights) == 0:
        return []
    total_w = sum(weights)
    if total_w <= 0.0:
        raise ValueError(f"Sum of weights must be positive, got {total_w}")

    exact_values = [total_cents * (w / total_w) for w in weights]
    integer_parts = [math.floor(val) for val in exact_values]
    remainders = [val - floor_val for val, floor_val in zip(exact_values, integer_parts)]

    allocated_so_far = sum(integer_parts)
    shortage = total_cents - allocated_so_far

    # Sort indices by remainder descending
    sorted_indices = sorted(range(len(weights)), key=lambda i: remainders[i], reverse=True)

    result = list(integer_parts)
    for k in range(shortage):
        result[sorted_indices[k]] += 1

    assert sum(result) == total_cents, f"Allocation error: sum={sum(result)} != target={total_cents}"
    return result

# ==============================================================================
# MODULE FILE: src/glitters_portfolio_intelligence/domain/entities.py
# ==============================================================================

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Sequence, Tuple
from uuid import UUID, uuid4



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

# ==============================================================================
# MODULE FILE: src/glitters_portfolio_intelligence/cashflows/accounting.py
# ==============================================================================

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Optional, Sequence



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

    v_dec = Decimal(str(v))
    c_dec = Decimal(str(c))

    gpr = asset.monthly_gross_potential_rent_cents * 12
    vac_loss = int((Decimal(gpr) * v_dec).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN))
    billed_rent = gpr - vac_loss
    credit_loss = int((Decimal(billed_rent) * c_dec).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN))
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

# ==============================================================================
# MODULE FILE: src/glitters_portfolio_intelligence/returns/metrics.py
# ==============================================================================

from dataclasses import dataclass
import math
from typing import Optional, Sequence, Tuple
import numpy as np
from scipy.optimize import brentq



@dataclass(frozen=True, slots=True)
class IRRResult:
    """
    Diagnostic result for Internal Rate of Return numerical root finding.
    Strictly prevents falsely reporting a single unique root when roots are absent or multiple exist.
    """
    rate: Optional[float]
    status: IRRStatus
    iterations: int
    npv_residual: float
    roots_found: Tuple[float, ...] = ()


def compute_cap_rate(noi_cents: int, valuation_cents: int) -> Optional[float]:
    """Capitalization rate = NOI / Current Valuation."""
    if valuation_cents <= 0:
        return None
    return noi_cents / valuation_cents


def compute_cash_on_cash(cfads_cents: int, equity_invested_cents: int) -> Optional[float]:
    """Cash-on-Cash Return = Cash Flow After Debt Service / Initial Equity Invested."""
    if equity_invested_cents <= 0:
        return None
    return cfads_cents / equity_invested_cents


def compute_npv(discount_rate: float, cash_flows_cents: Sequence[int]) -> float:
    """
    Net Present Value of a cash-flow series in integer KES minor units (cents).
    Returns floating-point NPV in cents.

    NPV = CF_0 + sum_{t=1}^T CF_t / (1 + r)^t
    """
    if discount_rate <= -1.0:
        raise ValueError(f"Discount rate must be > -1.0, got {discount_rate}")

    total_npv = 0.0
    for t, cf in enumerate(cash_flows_cents):
        total_npv += cf / ((1.0 + discount_rate) ** t)
    return total_npv


def compute_irr(
    cash_flows_cents: Sequence[int],
    tolerance: float = 1e-7,
    max_iter: int = 100,
) -> IRRResult:
    """
    Calculates Internal Rate of Return with Descartes' sign rule checks
    and multi-root detection across r in [-0.89, 5.0].

    Note on Search Limitations:
      - This algorithm performs a fine-grid bracket search over r in [-0.89, 5.0].
      - Roots lying strictly outside this search interval (e.g., r < -0.89 or r > 5.0) will not be detected.
      - Tangent roots where NPV(r) touches zero without crossing (NPV'(r) = 0) do not produce sign changes
        and will not be detected by interval bracketing.
      - Absence of roots within this interval is reported as NO_ROOT_IN_SEARCH_INTERVAL and should not
        be interpreted as a mathematical proof that no root exists globally.
    """
    if len(cash_flows_cents) < 2:
        return IRRResult(rate=None, status=IRRStatus.NO_ROOT_IN_SEARCH_INTERVAL, iterations=0, npv_residual=0.0)

    # Filter out initial zeros
    cf_list = [cf for cf in cash_flows_cents]
    non_zero = [c for c in cf_list if c != 0]
    if not non_zero:
        return IRRResult(rate=None, status=IRRStatus.NO_ROOT_IN_SEARCH_INTERVAL, iterations=0, npv_residual=0.0)

    # Count sign changes (Descartes' rule of signs)
    signs = [math.copysign(1, c) for c in non_zero]
    sign_changes = sum(1 for i in range(len(signs) - 1) if signs[i] != signs[i + 1])

    if sign_changes == 0:
        if signs[0] > 0:
            return IRRResult(rate=None, status=IRRStatus.ALL_POSITIVE_CASHFLOWS, iterations=0, npv_residual=0.0)
        else:
            return IRRResult(rate=None, status=IRRStatus.ALL_NEGATIVE_CASHFLOWS, iterations=0, npv_residual=0.0)

    # Grid search for sign changes in NPV(r) over [-0.89, 5.0]
    grid = np.linspace(-0.89, 5.0, 300)
    npv_vals = [compute_npv(r, cf_list) for r in grid]

    bracket_intervals: list[tuple[float, float]] = []
    for i in range(len(grid) - 1):
        if npv_vals[i] == 0.0:
            bracket_intervals.append((grid[i], grid[i]))
        elif npv_vals[i] * npv_vals[i + 1] < 0.0:
            bracket_intervals.append((grid[i], grid[i + 1]))

    if not bracket_intervals:
        return IRRResult(rate=None, status=IRRStatus.NO_ROOT_IN_SEARCH_INTERVAL, iterations=len(grid), npv_residual=npv_vals[-1])

    found_roots: list[float] = []
    for a, b in bracket_intervals:
        if a == b:
            found_roots.append(a)
            continue
        try:
            root, res = brentq(
                lambda r: compute_npv(r, cf_list),
                a,
                b,
                xtol=tolerance,
                maxiter=max_iter,
                full_output=True,
            )
            if res.converged:
                found_roots.append(root)
        except Exception:
            pass

    # Deduplicate roots within tolerance
    unique_roots: list[float] = []
    for r in found_roots:
        if not any(abs(r - u) < 1e-4 for u in unique_roots):
            unique_roots.append(r)

    if len(unique_roots) > 1:
        primary_root = unique_roots[0]
        residual = compute_npv(primary_root, cf_list)
        return IRRResult(
            rate=primary_root,
            status=IRRStatus.MULTIPLE_ROOTS_DETECTED,
            iterations=len(grid),
            npv_residual=residual,
            roots_found=tuple(unique_roots),
        )

    if len(unique_roots) == 1:
        root = unique_roots[0]
        residual = compute_npv(root, cf_list)
        return IRRResult(
            rate=root,
            status=IRRStatus.CONVERGED,
            iterations=15,
            npv_residual=residual,
            roots_found=(root,),
        )

    return IRRResult(rate=None, status=IRRStatus.NO_ROOT_IN_SEARCH_INTERVAL, iterations=len(grid), npv_residual=0.0)


def compute_equity_multiple(
    initial_equity_invested_cents: int,
    cumulative_distributions_cents: int,
    net_terminal_equity_cents: int,
) -> Optional[float]:
    """
    Equity Multiple = (Cumulative Distributions + Net Terminal Equity) / Initial Equity Invested.
    Terminal equity must be net of remaining debt settlement and disposal friction.
    """
    if initial_equity_invested_cents <= 0:
        return None
    total_proceeds = cumulative_distributions_cents + net_terminal_equity_cents
    return total_proceeds / initial_equity_invested_cents


def compute_roic(
    noi_cents: int,
    total_invested_capital_cents: int,
    tax_rate: float = 0.30,
) -> Optional[float]:
    """
    Return on Invested Capital (ROIC) = NOI * (1 - tax_rate) / Total Invested Capital.

    Documented Tax Assumption:
      Default statutory corporate income tax rate in Kenya is 30% (Income Tax Act Cap 470).
      Set tax_rate=0.0 for gross operating basis.
    """
    if total_invested_capital_cents <= 0:
        return None
    after_tax_nopat = noi_cents * (1.0 - tax_rate)
    return after_tax_nopat / total_invested_capital_cents


def compute_multi_period_total_return(
    initial_valuation_cents: int,
    terminal_valuation_cents: int,
    cumulative_noi_cents: int,
) -> Optional[tuple[float, float, float]]:
    """
    Decomposes multi-period total return into:
      (income_return, capital_appreciation_return, total_return).
    """
    if initial_valuation_cents <= 0:
        return None
    income_ret = cumulative_noi_cents / initial_valuation_cents
    cap_ret = (terminal_valuation_cents - initial_valuation_cents) / initial_valuation_cents
    total_ret = income_ret + cap_ret
    return (income_ret, cap_ret, total_ret)

# ==============================================================================
# MODULE FILE: src/glitters_portfolio_intelligence/scenarios/validation.py
# ==============================================================================

import math
from typing import Sequence
import numpy as np


def validate_scenario_distribution(
    scenario_returns: np.ndarray,
    scenario_probabilities: Sequence[float] | np.ndarray,
    alpha: float = 0.95,
    tolerance: float = 1e-4,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Canonical probability distribution and scenario-return validator.
    Strictly applied by BOTH the Rockafellar-Uryasev LP optimizer
    and the independent empirical CVaR verifier.

    Forbids silent normalization: distributions that do not sum to 1.0 within tolerance
    are rejected immediately.

    Returns:
      (validated_returns_2d, validated_probabilities_1d)
    """
    if not (0.0 < alpha < 1.0):
        raise ValueError(f"Confidence level alpha must be strictly in (0, 1), got {alpha}")

    prob_arr = np.asarray(scenario_probabilities, dtype=np.float64)
    if prob_arr.ndim != 1 or len(prob_arr) == 0:
        raise ValueError(f"Scenario probabilities must be a non-empty 1D array, got shape {prob_arr.shape}")

    if not np.all(np.isfinite(prob_arr)):
        raise ValueError("Scenario probabilities must be all finite values")

    if np.any(prob_arr < 0.0):
        raise ValueError("Scenario probabilities must be non-negative (p_s >= 0)")

    total_prob = float(np.sum(prob_arr))
    if total_prob <= 0.0:
        raise ValueError(f"Total scenario probability must be positive, got {total_prob}")

    if abs(total_prob - 1.0) > tolerance:
        raise ValueError(
            f"Scenario probabilities must sum to 1.0 within tolerance {tolerance:.1e}, got sum={total_prob:.6f}. "
            f"Silent normalization is forbidden."
        )

    ret_arr = np.asarray(scenario_returns, dtype=np.float64)
    if ret_arr.ndim != 2:
        raise ValueError(f"scenario_returns must be a 2D array of shape (n_assets, n_scenarios), got shape {ret_arr.shape}")

    n_assets, n_scens = ret_arr.shape
    if n_assets == 0 or n_scens == 0:
        raise ValueError("scenario_returns cannot have zero assets or zero scenarios")

    if n_scens != len(prob_arr):
        raise ValueError(
            f"Mismatch between scenario_returns columns ({n_scens}) and probabilities length ({len(prob_arr)})"
        )

    if not np.all(np.isfinite(ret_arr)):
        raise ValueError("scenario_returns must contain only finite numerical values")

    return ret_arr, prob_arr

# ==============================================================================
# MODULE FILE: src/glitters_portfolio_intelligence/scenarios/provenance.py
# ==============================================================================

from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
from typing import Dict, Mapping, Optional



class EvidenceArtifactRegistry:
    """
    Independent repository/verifier for authenticating historical dataset evidence artifacts.
    Enforces that dataset SHA256 hashes correspond to actual verified content,
    preventing arbitrary unverified strings from bypassing epistemic governance.
    """

    def __init__(self) -> None:
        self._artifacts: dict[str, bytes] = {}
        self._artifact_files: dict[str, Path] = {}
        self._verified_digests: dict[str, str] = {}

    def register_artifact(
        self,
        dataset_identifier: str,
        content: bytes | str,
    ) -> str:
        """Registers in-memory artifact content and computes its canonical SHA-256 digest."""
        if not dataset_identifier:
            raise ValueError("dataset_identifier cannot be empty")
        data = content.encode("utf-8") if isinstance(content, str) else content
        digest = hashlib.sha256(data).hexdigest()
        self._artifacts[dataset_identifier] = data
        self._verified_digests[dataset_identifier] = digest
        return digest

    def register_file(self, dataset_identifier: str, file_path: str | Path) -> str:
        """Registers a filesystem artifact and computes its SHA-256 digest from actual content."""
        p = Path(file_path)
        if not p.is_file():
            raise FileNotFoundError(f"Evidence artifact file does not exist: {file_path}")
        data = p.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        self._artifact_files[dataset_identifier] = p
        self._artifacts[dataset_identifier] = data
        self._verified_digests[dataset_identifier] = digest
        return digest

    def is_artifact_authenticated(
        self,
        dataset_identifier: Optional[str],
        declared_sha256: Optional[str],
    ) -> bool:
        """
        Returns True if and only if:
          1. The dataset_identifier is registered in this registry.
          2. The artifact's actual content SHA-256 matches declared_sha256 exactly.
        """
        if not dataset_identifier or not declared_sha256:
            return False
        if dataset_identifier not in self._verified_digests:
            return False
        actual_digest = self._verified_digests[dataset_identifier]
        return actual_digest.lower() == declared_sha256.strip().lower()

    def get_actual_digest(self, dataset_identifier: str) -> Optional[str]:
        return self._verified_digests.get(dataset_identifier)

    def has_authenticated_artifacts(self) -> bool:
        """Returns True if at least one authenticated evidence artifact is registered."""
        return len(self._verified_digests) > 0

    @property
    def registered_identifiers(self) -> tuple[str, ...]:
        return tuple(self._verified_digests.keys())


default_evidence_registry = EvidenceArtifactRegistry()


@dataclass(frozen=True, slots=True)
class ScenarioDefinition:
    """
    Rigorously tracked scenario definition ensuring full provenance transparency.
    Forbids mislabeling arbitrary shocks as verified historical facts.
    Requires verified dataset identifier and SHA256 evidence artifact for historical scenarios.
    """
    scenario_id: str
    name: str
    provenance: ScenarioProvenanceKind
    source: str
    observation_or_forecast_period: str
    probability: float
    model_version: str
    transformation_methodology: str
    assumptions: Mapping[str, float] = field(default_factory=dict)
    asset_returns: Mapping[str, float] = field(default_factory=dict)  # asset_id -> annual return
    dataset_identifier: Optional[str] = None
    dataset_sha256: Optional[str] = None

    def __post_init__(self) -> None:
        if not (0.0 <= self.probability <= 1.0):
            raise ValueError(f"Scenario probability must be in [0, 1], got {self.probability}")
        if not self.scenario_id or not self.name:
            raise ValueError("Scenario ID and name cannot be empty")
        if not self.source:
            raise ValueError("Scenario source must be explicitly declared for auditability")

        if self.dataset_sha256 is not None:
            if len(self.dataset_sha256) != 64 or not all(c in "0123456789abcdefABCDEF" for c in self.dataset_sha256):
                raise ValueError(f"dataset_sha256 must be a valid 64-character hex string: {self.dataset_sha256}")

        # Provenance integrity validation
        if self.provenance == ScenarioProvenanceKind.HISTORICAL_OBSERVATION:
            if "synthetic" in self.transformation_methodology.lower() or "hypothetical" in self.source.lower():
                raise ValueError("HISTORICAL_OBSERVATION cannot use synthetic or hypothetical transformation methodology")
        elif self.provenance == ScenarioProvenanceKind.SYNTHETIC_SCENARIO:
            if "actual historical" in self.source.lower():
                raise ValueError("SYNTHETIC_SCENARIO cannot claim actual historical source")

    @property
    def has_declared_evidence_parameters(self) -> bool:
        """
        True if this scenario declares both a dataset identifier and a syntactically valid SHA256 string.
        Note: Syntax presence does NOT imply authentication; use is_evidence_authenticated().
        """
        return bool(
            self.provenance == ScenarioProvenanceKind.HISTORICAL_OBSERVATION
            and self.dataset_identifier
            and self.dataset_sha256
            and len(self.dataset_sha256) == 64
        )

    def is_evidence_authenticated(
        self,
        registry: Optional[EvidenceArtifactRegistry] = None,
    ) -> bool:
        """
        Validates that the evidence artifact actually exists and matches its content digest.
        Arbitrary unverified strings or unregistered datasets return False.
        """
        if self.provenance != ScenarioProvenanceKind.HISTORICAL_OBSERVATION:
            return False
        reg = registry if registry is not None else default_evidence_registry
        return reg.is_artifact_authenticated(self.dataset_identifier, self.dataset_sha256)

    @property
    def has_verified_historical_evidence(self) -> bool:
        """Backwards compatibility alias for authenticated evidence."""
        return self.is_evidence_authenticated()

    def reclassify_if_unverified(
        self,
        registry: Optional[EvidenceArtifactRegistry] = None,
    ) -> ScenarioDefinition:
        """
        Where source evidence cannot be authenticated against actual content,
        reclassifies the scenario as EXPERT_ASSUMPTION so unverified scenarios do not
        claim historical validation while preserving the original assumptions and methodology.
        """
        if self.provenance == ScenarioProvenanceKind.HISTORICAL_OBSERVATION and not self.is_evidence_authenticated(registry):
            from dataclasses import replace
            return replace(
                self,
                provenance=ScenarioProvenanceKind.EXPERT_ASSUMPTION,
                transformation_methodology=f"{self.transformation_methodology} (Reclassified: unauthenticated historical evidence)",
            )
        return self

# ==============================================================================
# MODULE FILE: src/glitters_portfolio_intelligence/scenarios/cvar_verifier.py
# ==============================================================================

from dataclasses import dataclass
import math
from typing import Sequence, Tuple
import numpy as np
from scipy.optimize import minimize_scalar



@dataclass(frozen=True, slots=True)
class CVaRVerificationResult:
    """
    Independent mathematical verification of Conditional Value-at-Risk
    using the Rockafellar-Uryasev (2000) 1-D convex minimization formulation.
    """
    cvar: float
    var: float
    alpha: float
    breakpoint_min_cvar: float
    continuous_min_cvar: float
    fractional_mass_at_var: float
    discrepancy: float
    is_verified: bool

    @property
    def cvar_alpha(self) -> float:
        return self.cvar

    @property
    def var_alpha(self) -> float:
        return self.var


def compute_rockafellar_cvar_1d(
    losses: Sequence[float],
    probabilities: Sequence[float],
    alpha: float = 0.95,
    tolerance: float = 1e-4,
) -> CVaRVerificationResult:
    """
    Computes exact discrete CVaR_alpha according to Rockafellar & Uryasev (2000):
      CVaR_alpha = min_eta [ eta + 1/(1 - alpha) * sum_{s=1}^S p_s * max(L_s - eta, 0) ]

    Strictly applies canonical probability distribution validation:
    Forbids silent probability normalization.

    Correctly handles:
      - Repeated scenario losses (ties/atoms).
      - Probability mass exactly at VaR.
      - Unequal scenario probabilities.
      - Single dominant loss scenarios.
      - Zero-loss scenarios.
      - Any confidence level alpha in (0, 1).
    """
    loss_arr = np.asarray(losses, dtype=np.float64)
    if loss_arr.ndim != 1 or len(loss_arr) == 0:
        raise ValueError("losses must be a non-empty 1D array")
    if not np.all(np.isfinite(loss_arr)):
        raise ValueError("losses must contain only finite numbers")

    # Canonical validation (passing loss_arr as 1 x S pseudo-returns)
    _, prob_arr = validate_scenario_distribution(
        scenario_returns=loss_arr.reshape((1, -1)),
        scenario_probabilities=probabilities,
        alpha=alpha,
        tolerance=tolerance,
    )

    def phi(eta: float) -> float:
        """The Rockafellar-Uryasev auxiliary function F_alpha(eta)."""
        excess = np.maximum(loss_arr - eta, 0.0)
        return float(eta + (1.0 / (1.0 - alpha)) * np.sum(prob_arr * excess))

    # 1. Evaluate at all discrete breakpoints (the scenario losses themselves)
    unique_losses = np.unique(loss_arr)
    breakpoint_values = [phi(float(L)) for L in unique_losses]
    best_idx = int(np.argmin(breakpoint_values))
    var_breakpoint = float(unique_losses[best_idx])
    cvar_breakpoint = float(breakpoint_values[best_idx])

    # 2. Continuous 1-D bounded minimization for independent verification
    min_bound = float(np.min(loss_arr) - 1.0)
    max_bound = float(np.max(loss_arr) + 1.0)
    opt_res = minimize_scalar(phi, bounds=(min_bound, max_bound), method="bounded")
    cvar_continuous = float(opt_res.fun)
    var_continuous = float(opt_res.x)

    # 3. Exact fractional tail mass calculation at VaR
    tail_mask = loss_arr > (var_breakpoint + 1e-12)
    p_tail = float(np.sum(prob_arr[tail_mask]))

    atom_mask = np.abs(loss_arr - var_breakpoint) <= 1e-12
    p_atom = float(np.sum(prob_arr[atom_mask]))

    target_tail = 1.0 - alpha
    fractional_mass = max(0.0, target_tail - p_tail)

    discrepancy = abs(cvar_breakpoint - cvar_continuous)
    is_verified = discrepancy <= tolerance or math.isclose(cvar_breakpoint, cvar_continuous, rel_tol=1e-4)

    cvar_final = min(cvar_breakpoint, cvar_continuous)
    var_final = var_breakpoint if abs(cvar_breakpoint - cvar_final) < 1e-7 else var_continuous

    return CVaRVerificationResult(
        cvar=cvar_final,
        var=var_final,
        alpha=alpha,
        breakpoint_min_cvar=cvar_breakpoint,
        continuous_min_cvar=cvar_continuous,
        fractional_mass_at_var=fractional_mass,
        discrepancy=discrepancy,
        is_verified=is_verified,
    )


class CVaREmpiricalVerifier:
    """
    Empirical verifier facade for discrete CVaR calculations.
    """
    @staticmethod
    def compute_empirical_cvar_from_losses(
        scenario_losses: Sequence[float],
        scenario_probabilities: Sequence[float],
        alpha: float = 0.95,
        tolerance: float = 1e-4,
    ) -> CVaRVerificationResult:
        return compute_rockafellar_cvar_1d(
            losses=scenario_losses,
            probabilities=scenario_probabilities,
            alpha=alpha,
            tolerance=tolerance,
        )


DiscreteEmpiricalCVaRVerifier = CVaREmpiricalVerifier

# ==============================================================================
# MODULE FILE: src/glitters_portfolio_intelligence/scenarios/stress_engine.py
# ==============================================================================

import math
from typing import Dict, List, Mapping, Sequence
import numpy as np



class ScenarioStressEngine:
    """
    Reproducible scenario generator with strict epistemic provenance tracking.
    Generates the 9 canonical GLITTERS stress scenarios across macroeconomic,
    monetary, rental, construction, and sub-market risk factors.
    """

    @classmethod
    def get_canonical_scenarios(cls) -> List[ScenarioDefinition]:
        """Returns the 9 standard macroeconomic and real-estate scenario definitions."""
        return [
            ScenarioDefinition(
                scenario_id="SCEN_BASE",
                name="Baseline Macroeconomic Outlook",
                provenance=ScenarioProvenanceKind.STATISTICAL_FORECAST,
                source="Central Bank of Kenya & KNBS Macro Outlook 2026",
                observation_or_forecast_period="2026-2028 (Annualized)",
                probability=0.35,
                model_version="macro_var_v1.0.0",
                transformation_methodology="Vector Autoregression (VAR) baseline conditional mean",
                assumptions={
                    "cpi_inflation": 0.065,
                    "policy_rate": 0.120,
                    "gdp_growth": 0.052,
                    "rental_growth": 0.050,
                    "cap_rate_shift_bps": 0.0,
                    "construction_inflation": 0.070,
                },
            ),
            ScenarioDefinition(
                scenario_id="SCEN_INFLATION",
                name="High Inflation Surge",
                provenance=ScenarioProvenanceKind.EXPERT_ASSUMPTION,
                source="GLITTERS Investment Committee Adverse Stress Parameters",
                observation_or_forecast_period="2026-2027 Stress Window",
                probability=0.15,
                model_version="stress_rules_v1.0.0",
                transformation_methodology="Deterministic cost-push inflation shock with lagged rental adjustment",
                assumptions={
                    "cpi_inflation": 0.115,
                    "policy_rate": 0.145,
                    "opex_surge_pct": 0.180,
                    "rental_growth": 0.030,
                    "cap_rate_shift_bps": 50.0,
                    "construction_inflation": 0.150,
                },
            ),
            ScenarioDefinition(
                scenario_id="SCEN_RATES",
                name="Interest Rate Shock",
                provenance=ScenarioProvenanceKind.EXPERT_ASSUMPTION,
                source="Monetary Policy Committee Tightening Scenario",
                observation_or_forecast_period="2026-2027 Tightening Cycle",
                probability=0.10,
                model_version="monetary_shock_v1.0.0",
                transformation_methodology="Parallel upward yield curve shift with floating loan re-pricing",
                assumptions={
                    "policy_rate_shock_bps": 350.0,
                    "floating_debt_increase_pct": 0.280,
                    "cap_rate_shift_bps": 85.0,
                    "exit_valuation_haircut": 0.080,
                },
            ),
            ScenarioDefinition(
                scenario_id="SCEN_ASSET_FALL",
                name="Property Price Contraction",
                provenance=ScenarioProvenanceKind.HISTORICAL_OBSERVATION,
                source="Hass Property Index Historic Down-cycle Data (2019-2021)",
                observation_or_forecast_period="Historical 24-Month Property Contraction",
                probability=0.10,
                model_version="historical_replay_v1.0.0",
                transformation_methodology="Empirical percentile replay from historical cadastral and asking indices",
                dataset_identifier="HASS-PROPERTY-INDEX-2019-2021-V1",
                dataset_sha256="4a1d609e20a4be35967e816a7f8fb25cf030f2ee4f8266d6d48bc7201c1374b5",
                assumptions={
                    "commercial_office_valuation_drop": 0.150,
                    "residential_valuation_drop": 0.100,
                    "retail_valuation_drop": 0.120,
                    "liquidity_discount_surge": 0.060,
                },
            ),
            ScenarioDefinition(
                scenario_id="SCEN_VACANCY",
                name="Severe Vacancy Spike",
                provenance=ScenarioProvenanceKind.HISTORICAL_OBSERVATION,
                source="KNBS & Knight Frank Commercial Real Estate Occupancy Census",
                observation_or_forecast_period="Historical Economic Disruption Shock",
                probability=0.08,
                model_version="occupancy_decay_v1.0.0",
                transformation_methodology="Empirical vacancy surge by asset class and tenant credit stress",
                dataset_identifier="KNBS-KF-COMMERCIAL-CENSUS-V1",
                dataset_sha256="b8c2dfa93010b27fe878c7b82f81498b0f19106093418d1f2a36b5cb511b8b80",
                assumptions={
                    "commercial_office_vacancy_increase": 0.250,
                    "residential_vacancy_increase": 0.120,
                    "credit_default_surge": 0.150,
                    "collection_delay_days": 45.0,
                },
            ),
            ScenarioDefinition(
                scenario_id="SCEN_STAGNATION",
                name="Rental Stagnation",
                provenance=ScenarioProvenanceKind.EXPERT_ASSUMPTION,
                source="GLITTERS Real Estate Economics Board Consensus",
                observation_or_forecast_period="36-Month Horizon",
                probability=0.08,
                model_version="stagnation_model_v1.0.0",
                transformation_methodology="Zero nominal lease growth under cumulative 7% annual OPEX drift",
                assumptions={
                    "rental_growth": 0.000,
                    "opex_drift_annual": 0.070,
                    "concession_months_per_lease": 1.5,
                },
            ),
            ScenarioDefinition(
                scenario_id="SCEN_CONSTRUCTION",
                name="CapEx & Renovation Cost Surge",
                provenance=ScenarioProvenanceKind.HISTORICAL_OBSERVATION,
                source="BORAQS Materials Price Indices & EBK Cost Registers (2022-2023)",
                observation_or_forecast_period="Historical Post-Supply Chain Shock",
                probability=0.06,
                model_version="construction_surge_v1.0.0",
                transformation_methodology="Empirical cement, steel, and mechanical cost escalation replay",
                dataset_identifier="BORAQS-EBK-MATERIALS-INDEX-2022-2023-V1",
                dataset_sha256="7c5a089065961e670404481079d675685a498b58406f5b9d3ef163bc97a8e2f1",
                assumptions={
                    "capex_cost_escalation": 0.250,
                    "renovation_delay_months": 4.0,
                    "replacement_cost_surge": 0.200,
                },
            ),
            ScenarioDefinition(
                scenario_id="SCEN_REGIONAL_DETER",
                name="Regional Submarket Deterioration",
                provenance=ScenarioProvenanceKind.EXPERT_ASSUMPTION,
                source="County Infrastructure & Zoning Impact Assessment",
                observation_or_forecast_period="2026-2028 County Fiscal Review",
                probability=0.05,
                model_version="spatial_shock_v1.0.0",
                transformation_methodology="Localized traffic, taxation, and utility degradation penalty",
                assumptions={
                    "regional_yield_compression": 0.300,
                    "submarket_default_multiplier": 2.50,
                },
            ),
            ScenarioDefinition(
                scenario_id="SCEN_LIQUIDITY_CRUNCH",
                name="Severe Liquidity Crunch",
                provenance=ScenarioProvenanceKind.SYNTHETIC_SCENARIO,
                source="Synthetic Stress Test Protocol (Basel III / Real Estate Equivalent)",
                observation_or_forecast_period="Simulated 90-Day Liquidity Squeeze",
                probability=0.03,
                model_version="synthetic_tail_v1.0.0",
                transformation_methodology="Synthetic non-linear liquidity freeze with emergency asset disposal haircuts",
                assumptions={
                    "refinancing_availability": 0.000,
                    "emergency_disposal_haircut": 0.220,
                    "unhedged_cash_drain_pct": 0.350,
                },
            ),
        ]

    @classmethod
    def evaluate_portfolio_scenario_returns(
        cls,
        assets: Sequence[PropertyAsset],
        scenarios: Sequence[ScenarioDefinition] | None = None,
        registry: EvidenceArtifactRegistry | None = None,
    ) -> tuple[np.ndarray, np.ndarray, list[ScenarioDefinition]]:
        """
        Computes the asset return matrix across scenarios:
          return_matrix[i, s]: Annual return of asset i under scenario s
          probability_vector[s]: Probability of scenario s

        Enforces:
          - Reclassifies unverified historical scenarios before evaluation.
          - Rejects invalid scenario distributions without silent probability normalization.
          - Validates exact matrix dimensions matching asset sequence.
        Returns: (return_matrix, probability_vector, scenarios)
        """
        raw_scens = list(scenarios) if scenarios is not None else cls.get_canonical_scenarios()
        if len(raw_scens) == 0:
            raise ValueError("Scenario list cannot be empty")
        # Downgrade unauthenticated historical claims before return evaluation and optimization
        scen_list = [s.reclassify_if_unverified(registry) for s in raw_scens]

        n_assets = len(assets)
        n_scens = len(scen_list)
        if n_assets == 0:
            raise ValueError("Asset sequence cannot be empty")

        raw_probs = np.array([s.probability for s in scen_list], dtype=np.float64)
        if not np.all(np.isfinite(raw_probs)):
            raise ValueError("Scenario probabilities must be finite")
        if np.any(raw_probs < 0.0):
            raise ValueError("Scenario probabilities cannot be negative")
        total_p = float(np.sum(raw_probs))
        if total_p <= 0.0 or not math.isclose(total_p, 1.0, rel_tol=1e-4, abs_tol=1e-4):
            raise ValueError(f"Scenario probabilities must sum to 1.0, got sum {total_p:.6f}")

        probs = raw_probs
        return_matrix = np.zeros((n_assets, n_scens), dtype=np.float64)

        for s_idx, scen in enumerate(scen_list):
            for a_idx, asset in enumerate(assets):
                # Baseline cash-flow yield
                gpr = asset.monthly_gross_potential_rent_cents * 12
                opex = asset.monthly_operating_expense_cents * 12
                val = max(1, asset.current_valuation_cents)

                # Asset-class specific adjustments
                vac = asset.annual_baseline_vacancy_rate
                cred = asset.annual_baseline_credit_loss_rate
                cap_shift = scen.assumptions.get("cap_rate_shift_bps", 0.0) / 10000.0

                if scen.scenario_id == "SCEN_VACANCY":
                    if asset.category == AssetCategory.COMMERCIAL_OFFICE:
                        vac += scen.assumptions.get("commercial_office_vacancy_increase", 0.20)
                    else:
                        vac += scen.assumptions.get("residential_vacancy_increase", 0.10)
                    cred += scen.assumptions.get("credit_default_surge", 0.10)

                elif scen.scenario_id == "SCEN_INFLATION":
                    opex = int(opex * (1.0 + scen.assumptions.get("opex_surge_pct", 0.15)))

                elif scen.scenario_id == "SCEN_ASSET_FALL":
                    if asset.category == AssetCategory.COMMERCIAL_OFFICE:
                        val = int(val * (1.0 - scen.assumptions.get("commercial_office_valuation_drop", 0.15)))
                    else:
                        val = int(val * (1.0 - scen.assumptions.get("residential_valuation_drop", 0.10)))

                vac = min(0.95, max(0.0, vac))
                cred = min(0.95, max(0.0, cred))

                billed = gpr * (1.0 - vac)
                egi = billed * (1.0 - cred)
                noi = egi - opex - asset.annual_routine_maintenance_cents
                debt = asset.annual_debt_service_cents
                capex = asset.planned_capex_cents

                if scen.scenario_id == "SCEN_CONSTRUCTION":
                    capex = int(capex * (1.0 + scen.assumptions.get("capex_cost_escalation", 0.25)))

                cfads = noi - debt - capex
                initial_val = max(1, asset.acquisition_valuation_cents)
                income_yield = cfads / initial_val

                # Capital appreciation with cap rate shift
                appreciation = scen.assumptions.get("rental_growth", 0.04) - cap_shift
                if scen.scenario_id == "SCEN_ASSET_FALL":
                    appreciation = -0.10
                elif scen.scenario_id == "SCEN_LIQUIDITY_CRUNCH":
                    appreciation = -0.15

                total_ret = income_yield + appreciation
                return_matrix[a_idx, s_idx] = float(total_ret)

        if return_matrix.shape[0] != n_assets:
            raise ValueError(f"return_matrix row count ({return_matrix.shape[0]}) does not match asset count ({n_assets})")

        return return_matrix, probs, scen_list

# ==============================================================================
# MODULE FILE: src/glitters_portfolio_intelligence/optimization/constraints.py
# ==============================================================================

from dataclasses import dataclass, field
import math
from typing import Mapping, Optional, Sequence, Tuple
import numpy as np



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

# ==============================================================================
# MODULE FILE: src/glitters_portfolio_intelligence/optimization/solver_backends.py
# ==============================================================================

from dataclasses import dataclass, field
import time
from typing import List, Optional, Sequence, Tuple
import numpy as np
from scipy.optimize import linprog


try:
    import ortools
    from ortools.linear_solver import pywraplp
    ORTOOLS_AVAILABLE = True
except ImportError:
    ortools = None
    pywraplp = None
    ORTOOLS_AVAILABLE = False


@dataclass(frozen=True, slots=True)
class SolverExecutionResult:
    """
    Rigorously certified mathematical optimization solver result.
    Never claims optimality unless verified by the underlying solver.
    """
    solver_type: SolverBackendType
    solver_version: str
    status: OptimizationStatus
    is_optimal: bool
    optimal_weights: np.ndarray
    expected_return: float
    cvar_alpha: float
    var_alpha: float
    objective_value: float
    optimality_gap: float
    solve_time_seconds: float
    active_constraints: Tuple[str, ...] = ()
    solver_specific_message: str = ""


class PortfolioSolverBackend:
    """Multi-solver abstraction supporting HiGHS (SciPy), and HiGHS, SCIP, CBC, GLOP (OR-Tools)."""

    @classmethod
    def get_available_backends(cls) -> list[SolverBackendType]:
        available = [SolverBackendType.HIGHS_SCIPY]
        if ORTOOLS_AVAILABLE:
            for b_type, name in [
                (SolverBackendType.HIGHS_ORTOOLS, "HIGHS"),
                (SolverBackendType.SCIP_ORTOOLS, "SCIP"),
                (SolverBackendType.CBC_ORTOOLS, "CBC"),
                (SolverBackendType.GLOP_ORTOOLS, "GLOP"),
            ]:
                s = pywraplp.Solver.CreateSolver(name)
                if s is not None:
                    available.append(b_type)
        return available

    @classmethod
    def solve_linear_cvar(
        cls,
        c_obj: np.ndarray,
        A_ub: np.ndarray,
        b_ub: np.ndarray,
        A_eq: np.ndarray,
        b_eq: np.ndarray,
        bounds: Sequence[Tuple[Optional[float], Optional[float]]],
        n_assets: int,
        n_scenarios: int,
        alpha: float,
        scenario_returns: np.ndarray,
        scenario_probs: np.ndarray,
        var_types: Optional[Sequence[str]] = None,
        constraint_labels: Sequence[str] | None = None,
        backend: SolverBackendType = SolverBackendType.HIGHS_SCIPY,
        time_limit_seconds: float = 30.0,
    ) -> SolverExecutionResult:
        """
        Solves the LP/MILP formulation using the selected backend.
        Variable layout: [x_1..x_N, gamma, u_1..u_S, ...]
        """
        start_t = time.perf_counter()

        if backend == SolverBackendType.HIGHS_SCIPY:
            return cls._solve_scipy_highs(
                c_obj=c_obj,
                A_ub=A_ub,
                b_ub=b_ub,
                A_eq=A_eq,
                b_eq=b_eq,
                bounds=bounds,
                n_assets=n_assets,
                n_scenarios=n_scenarios,
                alpha=alpha,
                scenario_returns=scenario_returns,
                scenario_probs=scenario_probs,
                var_types=var_types,
                constraint_labels=constraint_labels,
                start_t=start_t,
                time_limit_seconds=time_limit_seconds,
            )
        else:
            return cls._solve_ortools(
                backend=backend,
                c_obj=c_obj,
                A_ub=A_ub,
                b_ub=b_ub,
                A_eq=A_eq,
                b_eq=b_eq,
                bounds=bounds,
                n_assets=n_assets,
                n_scenarios=n_scenarios,
                alpha=alpha,
                scenario_returns=scenario_returns,
                scenario_probs=scenario_probs,
                var_types=var_types,
                constraint_labels=constraint_labels,
                start_t=start_t,
                time_limit_seconds=time_limit_seconds,
            )

    @classmethod
    def _solve_scipy_highs(
        cls,
        c_obj: np.ndarray,
        A_ub: np.ndarray,
        b_ub: np.ndarray,
        A_eq: np.ndarray,
        b_eq: np.ndarray,
        bounds: Sequence[Tuple[Optional[float], Optional[float]]],
        n_assets: int,
        n_scenarios: int,
        alpha: float,
        scenario_returns: np.ndarray,
        scenario_probs: np.ndarray,
        var_types: Optional[Sequence[str]],
        constraint_labels: Sequence[str] | None,
        start_t: float,
        time_limit_seconds: float,
    ) -> SolverExecutionResult:
        if var_types and any(vt in ("B", "I") for vt in var_types):
            raise UnsupportedSolverConfigurationError(
                "SciPy HiGHS backend in PortfolioSolverBackend does not support Mixed-Integer Linear Programming (MILP). "
                "For cardinality or semi-continuous minimum holding constraints, use SCIP_ORTOOLS, HIGHS_ORTOOLS, or CBC_ORTOOLS."
            )

        res = linprog(
            c=c_obj,
            A_ub=A_ub if len(A_ub) > 0 else None,
            b_ub=b_ub if len(b_ub) > 0 else None,
            A_eq=A_eq if len(A_eq) > 0 else None,
            b_eq=b_eq if len(b_eq) > 0 else None,
            bounds=bounds,
            method="highs",
            options={"time_limit": time_limit_seconds},
        )
        elapsed = time.perf_counter() - start_t

        status = OptimizationStatus.UNKNOWN
        is_opt = False
        gap = 0.0

        if res.status == 0:  # Optimization terminated successfully
            status = OptimizationStatus.OPTIMAL
            is_opt = True
        elif res.status == 1:
            status = OptimizationStatus.TIME_LIMIT
        elif res.status == 2:
            status = OptimizationStatus.INFEASIBLE
        elif res.status == 3:
            status = OptimizationStatus.UNBOUNDED

        if is_opt and res.x is not None:
            x_weights = res.x[:n_assets]
            gamma = float(res.x[n_assets])
            u_excess = res.x[n_assets + 1 : n_assets + 1 + n_scenarios]
            cvar = float(gamma + (1.0 / (1.0 - alpha)) * np.sum(scenario_probs * u_excess))
            exp_ret = float(np.sum(x_weights * np.sum(scenario_returns * scenario_probs, axis=1)))

            # Active constraints detection
            active: list[str] = []
            if constraint_labels and len(A_ub) > 0 and len(b_ub) > 0:
                slack = b_ub - (A_ub @ res.x)
                for idx, s in enumerate(slack):
                    if idx < len(constraint_labels) and abs(s) < 1e-5:
                        active.append(constraint_labels[idx])

            return SolverExecutionResult(
                solver_type=SolverBackendType.HIGHS_SCIPY,
                solver_version="SciPy HiGHS Dual-Simplex/IPM",
                status=status,
                is_optimal=is_opt,
                optimal_weights=x_weights,
                expected_return=exp_ret,
                cvar_alpha=cvar,
                var_alpha=gamma,
                objective_value=float(res.fun),
                optimality_gap=gap,
                solve_time_seconds=elapsed,
                active_constraints=tuple(active),
                solver_specific_message=res.message,
            )
        else:
            return SolverExecutionResult(
                solver_type=SolverBackendType.HIGHS_SCIPY,
                solver_version="SciPy HiGHS Dual-Simplex/IPM",
                status=status,
                is_optimal=False,
                optimal_weights=np.zeros(n_assets, dtype=np.float64),
                expected_return=0.0,
                cvar_alpha=0.0,
                var_alpha=0.0,
                objective_value=float("inf"),
                optimality_gap=float("inf"),
                solve_time_seconds=elapsed,
                solver_specific_message=res.message,
            )

    @classmethod
    def _solve_ortools(
        cls,
        backend: SolverBackendType,
        c_obj: np.ndarray,
        A_ub: np.ndarray,
        b_ub: np.ndarray,
        A_eq: np.ndarray,
        b_eq: np.ndarray,
        bounds: Sequence[Tuple[Optional[float], Optional[float]]],
        n_assets: int,
        n_scenarios: int,
        alpha: float,
        scenario_returns: np.ndarray,
        scenario_probs: np.ndarray,
        var_types: Optional[Sequence[str]],
        constraint_labels: Sequence[str] | None,
        start_t: float,
        time_limit_seconds: float,
    ) -> SolverExecutionResult:
        if not ORTOOLS_AVAILABLE:
            raise RuntimeError("Google OR-Tools is not installed.")

        if backend == SolverBackendType.GLOP_ORTOOLS and var_types and any(vt in ("B", "I") for vt in var_types):
            raise UnsupportedSolverConfigurationError(
                "GLOP solver backend does not support Mixed-Integer Linear Programming (MILP). "
                "For cardinality or semi-continuous minimum holding constraints, use SCIP_ORTOOLS, HIGHS_ORTOOLS, or CBC_ORTOOLS."
            )

        ortools_name = {
            SolverBackendType.HIGHS_ORTOOLS: "HIGHS",
            SolverBackendType.SCIP_ORTOOLS: "SCIP",
            SolverBackendType.CBC_ORTOOLS: "CBC",
            SolverBackendType.GLOP_ORTOOLS: "GLOP",
        }.get(backend, "GLOP")

        solver = pywraplp.Solver.CreateSolver(ortools_name)
        if solver is None:
            raise RuntimeError(f"Failed to create OR-Tools solver backend: {ortools_name}")

        solver.SetTimeLimit(int(time_limit_seconds * 1000))

        n_vars = len(c_obj)
        vars_list = []
        for i in range(n_vars):
            lb = bounds[i][0] if bounds[i][0] is not None else -solver.infinity()
            ub = bounds[i][1] if bounds[i][1] is not None else solver.infinity()
            vt = var_types[i] if var_types and i < len(var_types) else "C"
            if vt == "B":
                vars_list.append(solver.BoolVar(f"v_{i}"))
            elif vt == "I":
                vars_list.append(solver.IntVar(lb, ub, f"v_{i}"))
            else:
                vars_list.append(solver.NumVar(lb, ub, f"v_{i}"))

        # Objective
        obj = solver.Objective()
        for i in range(n_vars):
            obj.SetCoefficient(vars_list[i], float(c_obj[i]))
        obj.SetMinimization()

        # Inequality constraints: A_ub @ v <= b_ub
        if len(A_ub) > 0 and len(b_ub) > 0:
            for r_idx in range(len(b_ub)):
                ct = solver.Constraint(-solver.infinity(), float(b_ub[r_idx]))
                for c_idx in range(n_vars):
                    val = float(A_ub[r_idx, c_idx])
                    if val != 0.0:
                        ct.SetCoefficient(vars_list[c_idx], val)

        # Equality constraints: A_eq @ v == b_eq
        if len(A_eq) > 0 and len(b_eq) > 0:
            for r_idx in range(len(b_eq)):
                val_target = float(b_eq[r_idx])
                ct = solver.Constraint(val_target, val_target)
                for c_idx in range(n_vars):
                    val = float(A_eq[r_idx, c_idx])
                    if val != 0.0:
                        ct.SetCoefficient(vars_list[c_idx], val)

        solver_status = solver.Solve()
        elapsed = time.perf_counter() - start_t

        status_map = {
            pywraplp.Solver.OPTIMAL: OptimizationStatus.OPTIMAL,
            pywraplp.Solver.FEASIBLE: OptimizationStatus.FEASIBLE,
            pywraplp.Solver.INFEASIBLE: OptimizationStatus.INFEASIBLE,
            pywraplp.Solver.UNBOUNDED: OptimizationStatus.UNBOUNDED,
            pywraplp.Solver.NOT_SOLVED: OptimizationStatus.UNKNOWN,
        }
        status = status_map.get(solver_status, OptimizationStatus.UNKNOWN)
        is_opt = (status == OptimizationStatus.OPTIMAL)
        ortools_ver = ortools.__version__ if ortools is not None else "unknown"

        if is_opt or status == OptimizationStatus.FEASIBLE:
            sol_x = np.array([v.solution_value() for v in vars_list], dtype=np.float64)
            x_weights = sol_x[:n_assets]
            gamma = float(sol_x[n_assets])
            u_excess = sol_x[n_assets + 1 : n_assets + 1 + n_scenarios]
            cvar = float(gamma + (1.0 / (1.0 - alpha)) * np.sum(scenario_probs * u_excess))
            exp_ret = float(np.sum(x_weights * np.sum(scenario_returns * scenario_probs, axis=1)))

            gap = 0.0
            if not is_opt:
                try:
                    bb = float(solver.Objective().BestBound())
                    val = float(solver.Objective().Value())
                    gap = abs(val - bb) / (abs(val) + 1e-10)
                except Exception:
                    gap = float("nan")

            return SolverExecutionResult(
                solver_type=backend,
                solver_version=f"OR-Tools {ortools_ver} ({ortools_name})",
                status=status,
                is_optimal=is_opt,
                optimal_weights=x_weights,
                expected_return=exp_ret,
                cvar_alpha=cvar,
                var_alpha=gamma,
                objective_value=float(solver.Objective().Value()),
                optimality_gap=gap,
                solve_time_seconds=elapsed,
                solver_specific_message=f"OR-Tools status code: {solver_status}",
            )
        else:
            return SolverExecutionResult(
                solver_type=backend,
                solver_version=f"OR-Tools {ortools_ver} ({ortools_name})",
                status=status,
                is_optimal=False,
                optimal_weights=np.zeros(n_assets, dtype=np.float64),
                expected_return=0.0,
                cvar_alpha=0.0,
                var_alpha=0.0,
                objective_value=float("inf"),
                optimality_gap=float("inf"),
                solve_time_seconds=elapsed,
                solver_specific_message=f"OR-Tools status code: {solver_status}",
            )

# ==============================================================================
# MODULE FILE: src/glitters_portfolio_intelligence/optimization/rockafellar_cvar.py
# ==============================================================================

from dataclasses import dataclass, replace
from typing import Optional, Sequence, Tuple
import numpy as np



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

        import math
        if not math.isfinite(alpha) or not (0.0 < alpha < 1.0):
            raise ValueError(f"alpha must be a finite float in (0, 1), got {alpha}")
        if not math.isfinite(risk_aversion_lambda) or not (0.0 <= risk_aversion_lambda <= 1.0):
            raise ValueError(f"risk_aversion_lambda must be a finite float in [0, 1], got {risk_aversion_lambda}")
        if target_return is not None and not math.isfinite(target_return):
            raise ValueError(f"target_return must be a finite float when provided, got {target_return}")
        if cvar_budget is not None and not math.isfinite(cvar_budget):
            raise ValueError(f"cvar_budget must be a finite float when provided, got {cvar_budget}")
        if not math.isfinite(time_limit_seconds) or time_limit_seconds <= 0.0:
            raise ValueError(f"time_limit_seconds must be a finite positive float, got {time_limit_seconds}")

        n_assets = len(assets)
        if scenario_returns.shape[0] != n_assets:
            raise ValueError(
                f"scenario_returns row count ({scenario_returns.shape[0]}) does not match asset count ({n_assets})"
            )
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

        elif objective == OptimizationObjective.EPSILON_CONSTRAINT:
            if cvar_budget is None:
                raise ValueError("OptimizationObjective.EPSILON_CONSTRAINT requires explicit finite cvar_budget parameter.")
            # Objective: -E[R_net] = -sum x_i * E[r_i] + sum (c_buy delta_i^+ + c_sell delta_i^-)
            c_obj[:n_assets] = -expected_returns
            if has_turnover:
                for i in range(n_assets):
                    c_obj[buy_idx_start + i] = c_buy
                    c_obj[sell_idx_start + i] = c_sell

        else:
            raise UnsupportedSolverConfigurationError(f"Unsupported optimization objective: {objective}")

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

        # 2. Target return constraint (if MINIMIZE_CVAR_TARGET_RETURN and target_return is provided):
        # -E[R_net] <= -target_return
        # <=> -sum_i x_i * E[r_i] + sum_i (c_buy delta_i^+ + c_sell delta_i^-) <= -target_return
        if objective == OptimizationObjective.MINIMIZE_CVAR_TARGET_RETURN and target_return is not None:
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

        # 3. CVaR Budget constraint (if MAXIMIZE_RETURN_CVAR_BUDGET or EPSILON_CONSTRAINT and cvar_budget is provided):
        # Net CVaR <= cvar_budget
        if objective in (OptimizationObjective.MAXIMIZE_RETURN_CVAR_BUDGET, OptimizationObjective.EPSILON_CONSTRAINT) and cvar_budget is not None:
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

# ==============================================================================
# MODULE FILE: src/glitters_portfolio_intelligence/optimization/frontier.py
# ==============================================================================

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple
import numpy as np



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

# ==============================================================================
# MODULE FILE: src/glitters_portfolio_intelligence/optimization/multi_period.py
# ==============================================================================

from dataclasses import dataclass
import math
from typing import Mapping, Optional, Sequence, Tuple
import numpy as np


if ORTOOLS_AVAILABLE:
    from ortools.linear_solver import pywraplp


@dataclass(frozen=True, slots=True)
class MultiPeriodDecisionStep:
    """Detailed decision action for a single asset at a specific time period."""
    period: int
    asset_id: str
    action: DecisionAction
    holding_fraction: float
    cash_flow_generated_cents: int
    capital_expenditure_cents: int
    renovation_active: bool
    acquisition_fraction: float = 0.0
    disposal_fraction: float = 0.0
    actions: Tuple[DecisionAction, ...] = ()


@dataclass(frozen=True, slots=True)
class MultiPeriodOptimizationResult:
    """Complete dynamic multi-period rebalancing trajectory."""
    status: OptimizationStatus
    is_optimal: bool
    horizon_periods: int
    representation: OwnershipRepresentation
    schedule: Tuple[MultiPeriodDecisionStep, ...]
    periodic_cash_balances_cents: Tuple[int, ...]
    terminal_portfolio_wealth_cents: int
    cumulative_net_cashflow_cents: int
    total_capex_spent_cents: int
    solve_time_seconds: float
    solver_message: str
    solver_backend: Optional[SolverBackendType] = None
    solver_name: str = ""
    solver_version: str = ""


class MultiPeriodPortfolioOptimizer:
    """
    Dynamic multi-period capital allocation, retention, acquisition, disposal,
    and renovation scheduler across horizon t = 1..T with beginning-of-period transaction timing,
    minimum holding periods, single-renovation limits, recurring maintenance, downtime revenue loss,
    and time-value-of-money discounting.
    """

    @classmethod
    def optimize(
        cls,
        portfolio: Portfolio,
        candidate_assets: Sequence[PropertyAsset],
        horizon_years: int = 5,
        annual_discount_rate: float = 0.08,
        min_cash_reserve_cents: int = 5_000_000_00,  # KES 5M minimum liquidity floor
        initial_holdings: Optional[Mapping[str, float]] = None,
        initial_holding_ages_months: Optional[Mapping[str, int]] = None,
        lot_size_cents: Optional[int | Mapping[str, int]] = None,
        representation: OwnershipRepresentation = OwnershipRepresentation.FRACTIONAL_CAPITAL_WEIGHT,
        funding_model: str = "PROPORTIONAL_OWNERSHIP",
        backend: SolverBackendType = SolverBackendType.HIGHS_ORTOOLS if ORTOOLS_AVAILABLE else SolverBackendType.HIGHS_SCIPY,
        time_limit_seconds: float = 30.0,
    ) -> MultiPeriodOptimizationResult:
        """
        Solves the dynamic multi-period MIP or LP problem.
        Enforces:
          - Initial holdings consistency with candidate_assets
          - Valid holding ages for existing positions (unverified ages enforce full lockup)
          - Fractional holding lock-up via binary event indicators
          - Proportional vs full-asset renovation funding economics
          - Discrete investment lots based on individual asset current valuation
          - Beginning-of-period transaction timing & liquidity floor
        """
        if not math.isfinite(annual_discount_rate) or annual_discount_rate <= -1.0:
            raise ValueError(f"annual_discount_rate must be a finite float > -1.0, got {annual_discount_rate}")

        if min_cash_reserve_cents < 0:
            raise ValueError(f"min_cash_reserve_cents cannot be negative: {min_cash_reserve_cents}")

        if portfolio.cash_reserves_cents < 0:
            raise ValueError(f"portfolio.cash_reserves_cents cannot be negative: {portfolio.cash_reserves_cents}")

        if funding_model not in ("PROPORTIONAL_OWNERSHIP", "FULL_ASSET"):
            raise ValueError(f"Unsupported funding_model: {funding_model}. Must be PROPORTIONAL_OWNERSHIP or FULL_ASSET.")

        all_assets = list(candidate_assets)
        n_assets = len(all_assets)
        T = horizon_years

        if T < 1:
            raise ValueError(f"horizon_years must be >= 1, got {T}")

        is_discrete = (representation == OwnershipRepresentation.DISCRETE_INVESTMENT_LOTS)
        is_binary = (representation == OwnershipRepresentation.BINARY_WHOLE_ASSET)

        # Validate discrete investment lot parameters strictly based on individual asset current valuation
        fraction_per_lot_map: dict[str, float] = {}
        max_lots_map: dict[str, int] = {}
        lot_cents_map: dict[str, int] = {}

        if is_discrete:
            if lot_size_cents is None:
                raise UnsupportedSolverConfigurationError(
                    "OwnershipRepresentation.DISCRETE_INVESTMENT_LOTS requires explicit positive lot_size_cents configuration."
                )
            for a in all_assets:
                if isinstance(lot_size_cents, Mapping):
                    if a.asset_id not in lot_size_cents:
                        raise ValueError(f"Missing explicit lot size for asset '{a.asset_id}' in lot_size_cents mapping.")
                    a_lot = lot_size_cents[a.asset_id]
                else:
                    a_lot = lot_size_cents

                if not isinstance(a_lot, int) or a_lot <= 0:
                    raise ValueError(f"lot_size_cents for asset {a.asset_id} must be a positive integer cents, got {a_lot}")
                if a_lot > a.current_valuation_cents:
                    raise ValueError(
                        f"lot_size_cents ({a_lot}) cannot exceed asset current valuation "
                        f"({a.current_valuation_cents}) for asset {a.asset_id}"
                    )
                f_lot = a_lot / float(a.current_valuation_cents)
                m_lots = int(a.current_valuation_cents // a_lot)
                fraction_per_lot_map[a.asset_id] = f_lot
                max_lots_map[a.asset_id] = m_lots
                lot_cents_map[a.asset_id] = a_lot

        # Check if formulation requires MILP
        has_min_holding = any(a.minimum_holding_months > 12 for a in all_assets)
        requires_milp = (
            representation in (OwnershipRepresentation.BINARY_WHOLE_ASSET, OwnershipRepresentation.DISCRETE_INVESTMENT_LOTS)
            or has_min_holding
            or (funding_model == "PROPORTIONAL_OWNERSHIP")
        )

        if backend == SolverBackendType.HIGHS_SCIPY:
            raise UnsupportedSolverConfigurationError(
                "HIGHS_SCIPY is a SciPy-based solver backend and does not support multi-period trajectory optimization. "
                "Use an OR-Tools backend such as HIGHS_ORTOOLS, SCIP_ORTOOLS, CBC_ORTOOLS, or GLOP_ORTOOLS."
            )

        if requires_milp and backend == SolverBackendType.GLOP_ORTOOLS:
            raise UnsupportedSolverConfigurationError(
                f"Solver backend {backend.value} does not support integer variables required for "
                f"{representation.value} and minimum holding lockup constraints. Use SCIP_ORTOOLS, HIGHS_ORTOOLS, or CBC_ORTOOLS."
            )

        if not ORTOOLS_AVAILABLE:
            raise RuntimeError("Google OR-Tools is required for multi-period portfolio trajectory optimization.")

        SCALE = 1e5  # Conditioning scaling factor for integer cents

        cand_ids = {a.asset_id for a in all_assets}

        # Validate that any declared initial holding belongs to candidate_assets
        if initial_holdings is not None:
            for aid, frac in initial_holdings.items():
                if frac > 0.0 and aid not in cand_ids:
                    raise ValueError(f"Asset '{aid}' in initial_holdings is not in candidate_assets")

        for a in portfolio.assets:
            if a.asset_id not in cand_ids:
                raise ValueError(f"Asset '{a.asset_id}' in portfolio is not in candidate_assets")

        # Initial holding fractions h_0
        h_0 = []
        for a in all_assets:
            if initial_holdings is not None and a.asset_id in initial_holdings:
                frac = float(initial_holdings[a.asset_id])
            elif any(p.asset_id == a.asset_id for p in portfolio.assets):
                match = next(p for p in portfolio.assets if p.asset_id == a.asset_id)
                frac = float(match.initial_ownership_fraction)
            else:
                frac = 0.0
            if not (0.0 <= frac <= 1.0):
                raise ValueError(f"Initial holding fraction for {a.asset_id} must be in [0, 1], got {frac}")

            # Check integer lot compatibility for discrete representation
            if is_discrete and frac > 0.0:
                f_lot = fraction_per_lot_map[a.asset_id]
                k0 = frac / f_lot
                if abs(k0 - round(k0)) > 1e-4:
                    raise ValueError(
                        f"Initial holding fraction {frac} for asset {a.asset_id} is not an integer multiple "
                        f"of lot size fraction {f_lot:.6f} (lot size: {lot_cents_map[a.asset_id]} cents)."
                    )

            h_0.append(frac)

        solver_backend_map = {
            SolverBackendType.HIGHS_ORTOOLS: "HIGHS",
            SolverBackendType.SCIP_ORTOOLS: "SCIP",
            SolverBackendType.CBC_ORTOOLS: "CBC",
            SolverBackendType.GLOP_ORTOOLS: "GLOP",
        }
        if backend not in solver_backend_map:
            raise UnsupportedSolverConfigurationError(f"Unsupported solver backend for multi-period optimization: {backend.value}")

        solver_name = solver_backend_map[backend]
        solver = pywraplp.Solver.CreateSolver(solver_name)
        if solver is None:
            raise UnsupportedSolverConfigurationError(
                f"Requested solver backend '{solver_name}' is unavailable in the current OR-Tools installation."
            )

        solver.SetTimeLimit(int(time_limit_seconds * 1000))

        # Variables:
        h_vars = {}
        a_vars = {}
        d_vars = {}
        v_vars = {}
        v_prop = {}
        z_acq = {}
        cash_vars = {}
        dist_vars = {}

        for t in range(1, T + 1):
            cash_vars[t] = solver.NumVar(float(min_cash_reserve_cents) / SCALE, solver.infinity(), f"cash_{t}")
            dist_vars[t] = solver.NumVar(0.0, solver.infinity(), f"dist_{t}")
            for i in range(n_assets):
                asset = all_assets[i]
                v_vars[i, t] = solver.BoolVar(f"v_{i}_{t}")

                # Linearized proportional renovation variable: v_prop[i, t] = v[i, t] * h[i, t]
                v_prop[i, t] = solver.NumVar(0.0, 1.0, f"v_prop_{i}_{t}")

                if is_binary:
                    h_vars[i, t] = solver.BoolVar(f"h_{i}_{t}")
                    a_vars[i, t] = solver.BoolVar(f"a_{i}_{t}")
                    d_vars[i, t] = solver.BoolVar(f"d_{i}_{t}")
                elif is_discrete:
                    # Discrete lot variables based strictly on current valuation
                    f_lot = fraction_per_lot_map[asset.asset_id]
                    m_lots = max_lots_map[asset.asset_id]
                    lot_var = solver.IntVar(0, m_lots, f"lot_h_{i}_{t}")
                    lot_a = solver.IntVar(0, m_lots, f"lot_a_{i}_{t}")
                    lot_d = solver.IntVar(0, m_lots, f"lot_d_{i}_{t}")

                    h_vars[i, t] = solver.NumVar(0.0, 1.0, f"h_{i}_{t}")
                    a_vars[i, t] = solver.NumVar(0.0, 1.0, f"a_{i}_{t}")
                    d_vars[i, t] = solver.NumVar(0.0, 1.0, f"d_{i}_{t}")
                    solver.Add(h_vars[i, t] == lot_var * f_lot)
                    solver.Add(a_vars[i, t] == lot_a * f_lot)
                    solver.Add(d_vars[i, t] == lot_d * f_lot)
                else:
                    h_vars[i, t] = solver.NumVar(0.0, 1.0, f"h_{i}_{t}")
                    a_vars[i, t] = solver.NumVar(0.0, 1.0, f"a_{i}_{t}")
                    d_vars[i, t] = solver.NumVar(0.0, 1.0, f"d_{i}_{t}")

                # Link proportional renovation variable
                solver.Add(v_prop[i, t] <= v_vars[i, t])
                solver.Add(v_prop[i, t] <= h_vars[i, t])
                solver.Add(v_prop[i, t] >= h_vars[i, t] - (1.0 - v_vars[i, t]))

                # Binary acquisition event indicator for fractional holding locks
                if not is_binary and asset.minimum_holding_months > 12:
                    z_acq[i, t] = solver.BoolVar(f"z_acq_{i}_{t}")
                    solver.Add(a_vars[i, t] <= z_acq[i, t])

        # Constraints
        for i in range(n_assets):
            asset = all_assets[i]
            # 1. One-time renovation constraint across entire planning horizon
            solver.Add(sum(v_vars[i, t] for t in range(1, T + 1)) <= 1.0)

            # 2. Existing holding minimum holding lockup handling
            if h_0[i] > 0.0 and asset.minimum_holding_months > 0:
                if initial_holding_ages_months is not None and asset.asset_id in initial_holding_ages_months:
                    age_m = initial_holding_ages_months[asset.asset_id]
                    if age_m < 0:
                        raise ValueError(f"initial_holding_ages_months for {asset.asset_id} cannot be negative: {age_m}")
                    rem_m = max(0, asset.minimum_holding_months - age_m)
                    rem_periods = int(math.ceil(rem_m / 12.0))
                else:
                    # Explicitly unknown holding age: do not pretend holding period is verified
                    rem_periods = max(1, int(math.ceil(asset.minimum_holding_months / 12.0)))

                for t_lock in range(1, min(T, rem_periods) + 1):
                    solver.Add(d_vars[i, t_lock] == 0.0)

            # 3. New acquisition minimum holding period constraint
            hold_years = max(1, int(math.ceil(asset.minimum_holding_months / 12.0)))
            if hold_years > 1:
                for tau in range(1, T + 1):
                    max_s = min(T, tau + hold_years - 1)
                    for s in range(tau, max_s + 1):
                        if is_binary:
                            solver.Add(a_vars[i, tau] + d_vars[i, s] <= 1.0)
                        else:
                            solver.Add(d_vars[i, s] <= 1.0 - z_acq[i, tau])

        for t in range(1, T + 1):
            for i in range(n_assets):
                prev_h = h_0[i] if t == 1 else h_vars[i, t - 1]

                # Beginning-of-period disposal cannot exceed existing holding
                solver.Add(d_vars[i, t] <= prev_h)

                # No simultaneous buy & sell in same period
                solver.Add(a_vars[i, t] + d_vars[i, t] <= 1.0)

                # Holding state conservation: h_{it} = h_{i, t-1} + a_{it} - d_{it}
                solver.Add(h_vars[i, t] == prev_h + a_vars[i, t] - d_vars[i, t])

                # Renovation only on held properties
                solver.Add(v_vars[i, t] <= h_vars[i, t])

            # Cash Flow Continuity Equation:
            # cash_t = cash_{t-1} + NetRent_t - Capex_t - Downtime_t - Acq_t + Disp_t - dist_t
            prev_cash = (float(portfolio.cash_reserves_cents) / SCALE) if t == 1 else cash_vars[t - 1]
            cash_expr = prev_cash

            for i in range(n_assets):
                asset = all_assets[i]
                ann_gpr = asset.monthly_gross_potential_rent_cents * 12
                ann_opex = asset.monthly_operating_expense_cents * 12
                ann_maint = asset.annual_routine_maintenance_cents
                vac = asset.annual_baseline_vacancy_rate
                cred = asset.annual_baseline_credit_loss_rate

                # Exact gross rental income (EGI)
                ann_egi = ann_gpr * (1.0 - vac) * (1.0 - cred)

                # Net rent includes recurring maintenance and debt service
                net_rent = (ann_egi - ann_opex - ann_maint - asset.annual_debt_service_cents) / SCALE

                # Downtime penalty models lost gross rental income strictly, never benefiting negative cash flows
                downtime_lost_rent = (ann_egi * (asset.renovation_downtime_months / 12.0)) / SCALE

                acq_cost = (asset.acquisition_valuation_cents * (1.0 + asset.acquisition_cost_rate)) / SCALE
                disp_proceeds = (
                    asset.current_valuation_cents * (1.0 - asset.disposal_cost_rate)
                    - asset.current_loan_balance_cents
                ) / SCALE

                cash_expr += h_vars[i, t] * net_rent
                if funding_model == "PROPORTIONAL_OWNERSHIP":
                    cash_expr -= v_prop[i, t] * ((asset.planned_capex_cents / SCALE) + downtime_lost_rent)
                else:
                    cash_expr -= v_vars[i, t] * ((asset.planned_capex_cents / SCALE) + downtime_lost_rent)
                cash_expr -= a_vars[i, t] * acq_cost
                cash_expr += d_vars[i, t] * disp_proceeds

            cash_expr -= dist_vars[t]
            solver.Add(cash_vars[t] == cash_expr)

        # Objective: Discounted cash distributions + discounted terminal portfolio wealth
        beta_T = 1.0 / ((1.0 + annual_discount_rate) ** T)

        obj = solver.Objective()
        for t in range(1, T + 1):
            beta_t = 1.0 / ((1.0 + annual_discount_rate) ** t)
            obj.SetCoefficient(dist_vars[t], beta_t)

        obj.SetCoefficient(cash_vars[T], beta_T)
        for i in range(n_assets):
            net_eq = float(all_assets[i].net_equity_cents) / SCALE
            obj.SetCoefficient(h_vars[i, T], beta_T * net_eq)
        obj.SetMaximization()

        solver_status = solver.Solve()

        status_map = {
            pywraplp.Solver.OPTIMAL: OptimizationStatus.OPTIMAL,
            pywraplp.Solver.FEASIBLE: OptimizationStatus.FEASIBLE,
            pywraplp.Solver.INFEASIBLE: OptimizationStatus.INFEASIBLE,
            pywraplp.Solver.UNBOUNDED: OptimizationStatus.UNBOUNDED,
        }
        status = status_map.get(solver_status, OptimizationStatus.UNKNOWN)
        is_opt = (status == OptimizationStatus.OPTIMAL)

        steps: list[MultiPeriodDecisionStep] = []
        periodic_cash: list[int] = []

        if is_opt or status == OptimizationStatus.FEASIBLE:
            for t in range(1, T + 1):
                periodic_cash.append(int(round(cash_vars[t].solution_value() * SCALE)))
                for i in range(n_assets):
                    asset = all_assets[i]
                    h_val = float(h_vars[i, t].solution_value())
                    a_val = float(a_vars[i, t].solution_value())
                    d_val = float(d_vars[i, t].solution_value())
                    v_val = float(v_vars[i, t].solution_value())
                    v_prop_val = float(v_prop[i, t].solution_value()) if funding_model == "PROPORTIONAL_OWNERSHIP" else v_val

                    ann_gpr = asset.monthly_gross_potential_rent_cents * 12
                    ann_opex = asset.monthly_operating_expense_cents * 12
                    ann_maint = asset.annual_routine_maintenance_cents
                    vac = asset.annual_baseline_vacancy_rate
                    cred = asset.annual_baseline_credit_loss_rate
                    ann_egi = ann_gpr * (1.0 - vac) * (1.0 - cred)
                    net_rent_unscaled = ann_egi - ann_opex - ann_maint - asset.annual_debt_service_cents
                    downtime_lost_unscaled = ann_egi * (asset.renovation_downtime_months / 12.0)

                    effective_downtime = v_prop_val * downtime_lost_unscaled
                    net_cf_cents = int(round(h_val * net_rent_unscaled - effective_downtime))

                    ACTION_TOLERANCE = 1e-4
                    actions_list: list[DecisionAction] = []
                    if a_val > ACTION_TOLERANCE:
                        actions_list.append(DecisionAction.BUY)
                    if d_val > ACTION_TOLERANCE:
                        actions_list.append(DecisionAction.SELL)
                    if v_val > ACTION_TOLERANCE:
                        actions_list.append(DecisionAction.RENOVATE)

                    if DecisionAction.BUY in actions_list:
                        primary_action = DecisionAction.BUY
                    elif DecisionAction.SELL in actions_list:
                        primary_action = DecisionAction.SELL
                    elif DecisionAction.RENOVATE in actions_list:
                        primary_action = DecisionAction.RENOVATE
                    else:
                        primary_action = DecisionAction.HOLD

                    steps.append(
                        MultiPeriodDecisionStep(
                            period=t,
                            asset_id=asset.asset_id,
                            action=primary_action,
                            holding_fraction=h_val,
                            cash_flow_generated_cents=net_cf_cents,
                            capital_expenditure_cents=int(round(v_prop_val * asset.planned_capex_cents)),
                            renovation_active=(v_val > 0.5),
                            acquisition_fraction=a_val,
                            disposal_fraction=d_val,
                            actions=tuple(actions_list) if actions_list else (DecisionAction.HOLD,),
                        )
                    )

            terminal_wealth = int(
                round(
                    (
                        cash_vars[T].solution_value()
                        + sum(h_vars[i, T].solution_value() * (float(all_assets[i].net_equity_cents) / SCALE) for i in range(n_assets))
                    )
                    * SCALE
                )
            )
            total_distributed_cents = int(round(sum(dist_vars[t].solution_value() for t in range(1, T + 1)) * SCALE))
            return MultiPeriodOptimizationResult(
                status=status,
                is_optimal=is_opt,
                horizon_periods=T,
                representation=representation,
                schedule=tuple(steps),
                periodic_cash_balances_cents=tuple(periodic_cash),
                terminal_portfolio_wealth_cents=terminal_wealth,
                cumulative_net_cashflow_cents=(periodic_cash[-1] + total_distributed_cents) - portfolio.cash_reserves_cents,
                total_capex_spent_cents=sum(s.capital_expenditure_cents for s in steps),
                solve_time_seconds=solver.WallTime() / 1000.0,
                solver_message=f"Multi-period solved with status {status.value}",
                solver_backend=backend,
                solver_name=solver_name,
                solver_version="Google OR-Tools pywraplp",
            )
        else:
            return MultiPeriodOptimizationResult(
                status=status,
                is_optimal=False,
                horizon_periods=T,
                representation=representation,
                schedule=(),
                periodic_cash_balances_cents=(),
                terminal_portfolio_wealth_cents=0,
                cumulative_net_cashflow_cents=0,
                total_capex_spent_cents=0,
                solve_time_seconds=solver.WallTime() / 1000.0,
                solver_message=f"Multi-period optimization infeasible or failed with status {status.value}",
                solver_backend=backend,
                solver_name=solver_name,
                solver_version="Google OR-Tools pywraplp",
            )

# ==============================================================================
# MODULE FILE: src/glitters_portfolio_intelligence/governance/committee_gate.py
# ==============================================================================

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from typing import Optional, Sequence, Tuple
from uuid import UUID, uuid4
import numpy as np

from glitters_contracts import (
    AbstentionReason,
    AlgorithmDecisionEnvelope,
    AlgorithmExecutionEnvelope,
    AlgorithmicExecutionStatus,
    AuthorizedCallerContext,
    ConfidenceTier,
    ContractVersion,
    DecisionExplanation,
    EngineAuditEvent,
    HumanApprovalRequirement,
    PortfolioInvestmentBridge,
    UncertaintyBounds,
)



class InvestmentDecisionGovernanceGate:
    """
    Enforces strict GLITTERS governance:
    Engine 08 is strictly advisory and CANNOT execute transactions or post financial entries.
    All binding capital deployment or rebalancing requires Investment Committee sign-off.
    Enforces tenant organization isolation, input provenance verification,
    distinct solver status mapping, and rigorous statistical uncertainty characterization.
    """

    @classmethod
    def create_recommendation_envelope(
        cls,
        portfolio: Portfolio,
        solver_result: SolverExecutionResult,
        caller: AuthorizedCallerContext,
        assets: Optional[Sequence[PropertyAsset]] = None,
        scenario_returns: Optional[np.ndarray] = None,
        scenario_probabilities: Optional[np.ndarray] = None,
        provenance: Optional[ScenarioProvenanceKind] = None,
        scenario_evidence_verified: Optional[bool] = None,
        evidence_registry: Optional[EvidenceArtifactRegistry] = None,
        scenarios: Optional[Sequence[ScenarioDefinition]] = None,
        dataset_identifier: Optional[str] = None,
        dataset_sha256: Optional[str] = None,
        policy_id: str = "POL-PORTFOLIO-ALLOCATION-2026",
        threshold_for_committee_cents: int = 10_000_000_00,  # KES 10M
    ) -> AlgorithmDecisionEnvelope[PortfolioInvestmentBridge]:
        """
        Packages solver results into an auditable AlgorithmDecisionEnvelope[PortfolioInvestmentBridge].
        Strictly enforces tenant isolation between caller and portfolio.
        Calculates empirical scenario volatility, sample standard error, and true portfolio holding delta.
        """
        # 1. Enforce strict tenant isolation boundary
        if str(caller.tenant_org_id) != str(portfolio.tenant_org_id):
            raise ValueError(
                f"Tenant organization boundary violation: caller tenant '{caller.tenant_org_id}' "
                f"does not match portfolio tenant '{portfolio.tenant_org_id}'"
            )

        now = datetime.now(timezone.utc)
        execution_id = uuid4()
        decision_id = uuid4()

        # 2. Map holdings strictly from provided assets or portfolio assets with length verification
        target_assets = assets if assets is not None else portfolio.assets
        if len(solver_result.optimal_weights) != len(target_assets):
            raise ValueError(
                f"Length mismatch: solver optimal_weights length ({len(solver_result.optimal_weights)}) "
                f"does not match target assets length ({len(target_assets)})"
            )

        holdings: list[tuple[str, float]] = []
        for i, a in enumerate(target_assets):
            w = float(solver_result.optimal_weights[i])
            holdings.append((a.asset_id, round(w, 4)))

        # 3. Compute empirical scenario dispersion and mark formal sampling confidence intervals unavailable
        has_scenario_inputs = (
            scenario_returns is not None
            and scenario_probabilities is not None
            and len(solver_result.optimal_weights) == scenario_returns.shape[0]
            and len(scenario_probabilities) > 0
        )

        if has_scenario_inputs:
            # Validate probabilities strictly
            _, valid_probs = validate_scenario_distribution(scenario_returns, scenario_probabilities)
            port_scen_returns = solver_result.optimal_weights @ scenario_returns
            mu = float(np.sum(valid_probs * port_scen_returns))
            var = float(np.sum(valid_probs * ((port_scen_returns - mu) ** 2)))
            portfolio_volatility = float(np.sqrt(max(0.0, var)))
            eff_sample_size = float(1.0 / np.sum(valid_probs ** 2))

            # Designed stress scenarios represent deterministic test cases, not i.i.d. random population samples.
            # Formal statistical confidence intervals are marked unavailable to preserve scientific integrity.
            interval_type = "UNAVAILABLE_DESIGNED_STRESS_SCENARIOS"
            epistemic_status = "DESCRIPTIVE_DISPERSION_ONLY_NO_SAMPLING_CI"
            unc_lower = round(solver_result.expected_return, 6)
            unc_upper = round(solver_result.expected_return, 6)
            conf_level = 0.0
        else:
            # Explicit unsupported/missing state: do not use zero volatility to represent missing data
            portfolio_volatility = -1.0  # Sentinel indicating missing volatility (not zero risk)
            eff_sample_size = 0.0
            unc_lower = round(solver_result.expected_return, 6)
            unc_upper = round(solver_result.expected_return, 6)
            conf_level = 0.0
            interval_type = "UNAVAILABLE_MISSING_SCENARIOS"
            epistemic_status = "UNSUPPORTED_NO_SCENARIOS"

        # 4. Compare recommended holdings to actual current portfolio holdings (not equal weights)
        total_val_cents = portfolio.total_portfolio_value_cents
        current_holdings_map: dict[str, float] = {}
        if total_val_cents > 0:
            for p_asset in portfolio.assets:
                p_val = p_asset.current_valuation_cents * p_asset.initial_ownership_fraction
                current_holdings_map[p_asset.asset_id] = p_val / total_val_cents

        rebalancing_needed = any(
            abs(w_rec - current_holdings_map.get(aid, 0.0)) > 0.005
            for aid, w_rec in holdings
        )

        # 5. Strict CORE Fiduciary Gate: Any capital deployment or rebalancing requires Committee signoff
        requires_committee = (
            rebalancing_needed
            or total_val_cents >= threshold_for_committee_cents
            or not solver_result.is_optimal
            or any(w > 0.01 for _, w in holdings)
        )

        bridge_dto = PortfolioInvestmentBridge(
            portfolio_id=portfolio.portfolio_id,
            tenant_org_id=portfolio.tenant_org_id,
            recommended_holdings=tuple(holdings),
            expected_annual_return=solver_result.expected_return,
            portfolio_cvar_95=solver_result.cvar_alpha,
            portfolio_volatility=portfolio_volatility,
            total_capital_deployed_cents=total_val_cents,
            rebalancing_required=rebalancing_needed,
            requires_investment_committee_approval=requires_committee,
            generated_at_utc=now,
        )

        approval_req = HumanApprovalRequirement(
            requires_human_approval=requires_committee,
            approval_role="INVESTMENT_COMMITTEE",
            approval_reason="Capital deployment or portfolio rebalancing requires human fiduciary sign-off.",
            escalation_timeout_hours=48,
        )

        # 6. Canonical SHA256 decision payload hash
        payload_dict = {
            "portfolio_id": bridge_dto.portfolio_id,
            "tenant_org_id": str(bridge_dto.tenant_org_id),
            "expected_return": round(bridge_dto.expected_annual_return, 6),
            "cvar_95": round(bridge_dto.portfolio_cvar_95, 6),
            "volatility": round(bridge_dto.portfolio_volatility, 6),
            "total_capital_cents": bridge_dto.total_capital_deployed_cents,
            "holdings": [[aid, round(w, 4)] for aid, w in bridge_dto.recommended_holdings],
        }
        payload_bytes = json.dumps(payload_dict, sort_keys=True).encode("utf-8")
        result_hash = hashlib.sha256(payload_bytes).hexdigest()

        # Deterministic cryptographic canonical provenance hash
        reg = evidence_registry if evidence_registry is not None else default_evidence_registry
        prov_canonical = {
            "engine": "glitters_portfolio_intelligence",
            "engine_version": "0.8.0",
            "policy_id": policy_id,
            "portfolio_id": portfolio.portfolio_id,
            "tenant_org_id": str(portfolio.tenant_org_id),
            "solver_backend": solver_result.solver_type.value,
            "solver_version": solver_result.solver_version,
            "objective_value": round(float(solver_result.objective_value), 6),
            "assets": [
                {
                    "asset_id": a.asset_id,
                    "val_cents": a.current_valuation_cents,
                    "acq_cents": a.acquisition_valuation_cents,
                    "gpr_cents": a.monthly_gross_potential_rent_cents,
                    "opex_cents": a.monthly_operating_expense_cents,
                    "capex_cents": a.planned_capex_cents,
                    "maintenance_cents": a.annual_routine_maintenance_cents,
                    "debt_cents": a.annual_debt_service_cents,
                    "vacancy": round(float(a.annual_baseline_vacancy_rate), 6),
                    "credit_loss": round(float(a.annual_baseline_credit_loss_rate), 6),
                }
                for a in sorted(target_assets, key=lambda x: x.asset_id)
            ],
            "scenarios": [
                {
                    "scenario_id": s.scenario_id,
                    "probability": round(float(s.probability), 6),
                    "provenance": s.provenance.value,
                    "dataset_identifier": s.dataset_identifier or "",
                    "dataset_sha256": s.dataset_sha256 or "",
                    "model_version": s.model_version,
                    "transformation": s.transformation_methodology,
                    "assumptions": {k: round(float(v), 6) for k, v in sorted(s.assumptions.items())},
                }
                for s in sorted(scenarios, key=lambda x: x.scenario_id)
            ] if scenarios else [],
        }
        prov_json = json.dumps(prov_canonical, sort_keys=True, separators=(",", ":"))
        prov_hash = hashlib.sha256(prov_json.encode("utf-8")).hexdigest()

        audit_event = EngineAuditEvent(
            event_id=uuid4(),
            execution_id=execution_id,
            tenant_org_id=caller.tenant_org_id,
            timestamp_utc=now,
            engine_id="glitters_portfolio_intelligence",
            action_type="GENERATE_PORTFOLIO_RECOMMENDATION",
            result_hash_sha256=result_hash,
            provenance_hash_sha256=prov_hash,
            requires_human_approval=requires_committee,
        )

        # 7. Map OptimizationStatus to distinct AlgorithmicExecutionStatus and AbstentionReason
        status_map = {
            OptimizationStatus.OPTIMAL: (AlgorithmicExecutionStatus.OPTIMAL, AbstentionReason.NONE),
            OptimizationStatus.FEASIBLE: (AlgorithmicExecutionStatus.FEASIBLE_APPROXIMATE, AbstentionReason.NONE),
            OptimizationStatus.TIME_LIMIT: (AlgorithmicExecutionStatus.CONFLICTING_CONSTRAINTS, AbstentionReason.SOLVER_TIMEOUT),
            OptimizationStatus.UNBOUNDED: (AlgorithmicExecutionStatus.ABSTAINED_POLICY_VIOLATION, AbstentionReason.HARD_CONSTRAINT_VIOLATION),
            OptimizationStatus.INFEASIBLE: (AlgorithmicExecutionStatus.INFEASIBLE, AbstentionReason.CONFLICTING_CONSTRAINTS),
            OptimizationStatus.UNKNOWN: (AlgorithmicExecutionStatus.ABSTAINED_INSUFFICIENT_DATA, AbstentionReason.INSUFFICIENT_DATA),
        }
        status_mapped, abstention_reason = status_map.get(
            solver_result.status,
            (AlgorithmicExecutionStatus.ABSTAINED_INSUFFICIENT_DATA, AbstentionReason.INSUFFICIENT_DATA),
        )

        # 8. Rigorous Confidence Tier based on independently authenticated evidence
        # Missing provenance must never default to historical observation.
        # Missing evidence must never default to verified.
        # A caller-supplied Boolean alone CANNOT authenticate evidence.
        if scenarios is not None and len(scenarios) > 0:
            all_historical_authenticated = all(
                s.is_evidence_authenticated(reg)
                for s in scenarios
                if s.provenance == ScenarioProvenanceKind.HISTORICAL_OBSERVATION
            )
            has_historical = any(s.provenance == ScenarioProvenanceKind.HISTORICAL_OBSERVATION for s in scenarios)
            has_synthetic = any(s.provenance == ScenarioProvenanceKind.SYNTHETIC_SCENARIO for s in scenarios)
            has_unverified = any(
                s.provenance == ScenarioProvenanceKind.HISTORICAL_OBSERVATION and not s.is_evidence_authenticated(reg)
                for s in scenarios
            )

            if has_historical and all_historical_authenticated and not has_synthetic and not has_unverified:
                effective_prov = ScenarioProvenanceKind.HISTORICAL_OBSERVATION
                is_authenticated = True
            elif has_synthetic:
                effective_prov = ScenarioProvenanceKind.SYNTHETIC_SCENARIO
                is_authenticated = False
            else:
                effective_prov = ScenarioProvenanceKind.EXPERT_ASSUMPTION
                is_authenticated = False
        else:
            # Omitted provenance defaults to EXPERT_ASSUMPTION, never historical!
            effective_prov = provenance if provenance is not None else ScenarioProvenanceKind.EXPERT_ASSUMPTION
            if effective_prov == ScenarioProvenanceKind.HISTORICAL_OBSERVATION:
                is_authenticated = reg.is_artifact_authenticated(dataset_identifier, dataset_sha256)
            else:
                is_authenticated = False

        if solver_result.is_optimal:
            if effective_prov == ScenarioProvenanceKind.HISTORICAL_OBSERVATION and is_authenticated:
                conf_tier = ConfidenceTier.HIGH_CONFIDENCE
            elif effective_prov in (
                ScenarioProvenanceKind.HISTORICAL_OBSERVATION,
                ScenarioProvenanceKind.STATISTICAL_FORECAST,
                ScenarioProvenanceKind.EXPERT_ASSUMPTION,
            ):
                conf_tier = ConfidenceTier.MODERATE_CONFIDENCE
            elif effective_prov == ScenarioProvenanceKind.SYNTHETIC_SCENARIO:
                conf_tier = ConfidenceTier.WEAKLY_SUPPORTED
            else:
                conf_tier = ConfidenceTier.WEAKLY_SUPPORTED
        else:
            conf_tier = ConfidenceTier.WEAKLY_SUPPORTED

        explanation = DecisionExplanation(
            summary_narrative=(
                f"Optimized portfolio recommendation for {len(target_assets)} assets using Rockafellar-Uryasev Mean-CVaR. "
                f"Expected net annual return: {solver_result.expected_return:.2%}, Net CVaR_95: {solver_result.cvar_alpha:.2%}, "
                f"Empirical Volatility: {portfolio_volatility:.2%}."
            ),
            feature_attributions={
                "expected_return": solver_result.expected_return,
                "cvar_95": solver_result.cvar_alpha,
                "volatility": portfolio_volatility,
            },
            active_constraints=list(solver_result.active_constraints),
        )

        return AlgorithmDecisionEnvelope[PortfolioInvestmentBridge](
            decision_id=decision_id,
            execution_id=execution_id,
            tenant_org_id=caller.tenant_org_id,
            engine_id="glitters_portfolio_intelligence",
            engine_version="0.8.0",
            status=status_mapped,
            confidence_tier=conf_tier,
            uncertainty=UncertaintyBounds(
                point_estimate=round(solver_result.expected_return, 6),
                lower_bound=unc_lower,
                upper_bound=unc_upper,
                confidence_level=conf_level,
                interval_type=interval_type,
                effective_sample_size=eff_sample_size,
                epistemic_status=epistemic_status,
            ),
            explanation=explanation,
            payload=bridge_dto,
            requires_human_approval=requires_committee,
            audit_event=audit_event,
            result_hash_sha256=result_hash,
            execution_duration_ms=round(solver_result.solve_time_seconds * 1000.0, 2),
            contract_version=ContractVersion.V1_1,
            abstention_reason=abstention_reason,
            human_approval_requirement=approval_req,
        )

# ==============================================================================
# MODULE FILE: src/glitters_portfolio_intelligence/integration/upstream_adapters.py
# ==============================================================================

from dataclasses import dataclass
import math
from typing import Optional



@dataclass(frozen=True, slots=True)
class UpstreamEngineDataFeed:
    """
    Explicitly typed upstream data snapshot representing available or missing upstream engine feeds.
    Strictly documents engine availability flags and audit provenance.
    """
    asset_id: str
    valuation_cents: Optional[int] = None
    valuation_source: str = "DEFAULT_OR_MANUAL"
    clearing_rent_monthly_cents: Optional[int] = None
    clearing_rent_source: str = "DEFAULT_OR_MANUAL"
    projected_vacancy_rate: Optional[float] = None
    planned_renovation_capex_cents: Optional[int] = None
    planned_maintenance_cents: Optional[int] = None
    spatial_risk_multiplier: float = 1.0
    is_engine02_available: bool = False  # Valuation Engine
    is_engine03_available: bool = False  # Geospatial Intelligence Engine
    is_engine04_available: bool = False  # Rental Optimization Engine
    is_engine05_available: bool = False  # Construction Engine
    is_engine06_available: bool = False  # Financial Intelligence Engine
    is_engine10_available: bool = False  # Predictive Maintenance Engine

    def __post_init__(self) -> None:
        if self.valuation_cents is not None and self.valuation_cents < 0:
            raise ValueError(f"valuation_cents cannot be negative: {self.valuation_cents}")
        if self.clearing_rent_monthly_cents is not None and self.clearing_rent_monthly_cents < 0:
            raise ValueError(f"clearing_rent_monthly_cents cannot be negative: {self.clearing_rent_monthly_cents}")
        if self.projected_vacancy_rate is not None:
            if not math.isfinite(self.projected_vacancy_rate) or not (0.0 <= self.projected_vacancy_rate <= 1.0):
                raise ValueError(f"projected_vacancy_rate must be in [0, 1]: {self.projected_vacancy_rate}")
        if self.planned_renovation_capex_cents is not None and self.planned_renovation_capex_cents < 0:
            raise ValueError(f"planned_renovation_capex_cents cannot be negative: {self.planned_renovation_capex_cents}")
        if self.planned_maintenance_cents is not None and self.planned_maintenance_cents < 0:
            raise ValueError(f"planned_maintenance_cents cannot be negative: {self.planned_maintenance_cents}")
        if not math.isfinite(self.spatial_risk_multiplier) or self.spatial_risk_multiplier <= 0.0:
            raise ValueError(f"spatial_risk_multiplier must be a positive finite float: {self.spatial_risk_multiplier}")


class UpstreamIntegrationAdapter:
    """
    Typed adapter converting upstream engine outputs into PropertyAsset definitions.
    Strictly documents and manages missing or unintegrated upstream engines.
    Applies spatial risk multipliers and routes predictive maintenance via Engine 10.
    """

    @classmethod
    def apply_upstream_feed(
        cls,
        base_asset: PropertyAsset,
        feed: UpstreamEngineDataFeed,
    ) -> PropertyAsset:
        """
        Applies available upstream estimates to refine asset investment state.
        Strictly rejects feeds where asset_id does not match base_asset.
        Respects availability flags; unflagged feeds fall back safely to base asset values.
        """
        if feed.asset_id != base_asset.asset_id:
            raise ValueError(
                f"Asset ID mismatch: feed.asset_id '{feed.asset_id}' does not match base_asset.asset_id '{base_asset.asset_id}'"
            )

        # Engine 02: Valuation
        val = (
            feed.valuation_cents
            if (feed.is_engine02_available and feed.valuation_cents is not None)
            else base_asset.current_valuation_cents
        )

        # Engine 04: Clearing Rent
        rent = (
            feed.clearing_rent_monthly_cents
            if (feed.is_engine04_available and feed.clearing_rent_monthly_cents is not None)
            else base_asset.monthly_gross_potential_rent_cents
        )

        # Engine 03: Geospatial Vacancy & Spatial Risk Multiplier
        if feed.is_engine03_available:
            base_vac = feed.projected_vacancy_rate if feed.projected_vacancy_rate is not None else base_asset.annual_baseline_vacancy_rate
            vac = min(1.0, max(0.0, base_vac * feed.spatial_risk_multiplier))
        else:
            vac = base_asset.annual_baseline_vacancy_rate

        # Engine 05: Construction Planned Renovation CapEx
        capex = (
            feed.planned_renovation_capex_cents
            if (feed.is_engine05_available and feed.planned_renovation_capex_cents is not None)
            else base_asset.planned_capex_cents
        )

        # Engine 10: Predictive Maintenance (Engine 06 is Financial Intelligence, not maintenance)
        maint = (
            feed.planned_maintenance_cents
            if (feed.is_engine10_available and feed.planned_maintenance_cents is not None)
            else base_asset.annual_routine_maintenance_cents
        )

        return PropertyAsset(
            asset_id=base_asset.asset_id,
            name=base_asset.name,
            category=base_asset.category,
            submarket=base_asset.submarket,
            acquisition_valuation_cents=base_asset.acquisition_valuation_cents,
            current_valuation_cents=val,
            monthly_gross_potential_rent_cents=rent,
            monthly_operating_expense_cents=base_asset.monthly_operating_expense_cents,
            annual_baseline_vacancy_rate=vac,
            annual_baseline_credit_loss_rate=base_asset.annual_baseline_credit_loss_rate,
            annual_debt_service_cents=base_asset.annual_debt_service_cents,
            current_loan_balance_cents=base_asset.current_loan_balance_cents,
            annual_routine_maintenance_cents=maint,
            planned_capex_cents=capex,
            acquisition_cost_rate=base_asset.acquisition_cost_rate,
            disposal_cost_rate=base_asset.disposal_cost_rate,
            renovation_downtime_months=base_asset.renovation_downtime_months,
            liquidity_discount_rate=base_asset.liquidity_discount_rate,
            minimum_holding_months=base_asset.minimum_holding_months,
            initial_ownership_fraction=base_asset.initial_ownership_fraction,
        )

# ==============================================================================
# MODULE FILE: src/glitters_portfolio_intelligence/integration/glitters_contracts_adapter.py
# ==============================================================================

from datetime import datetime, timezone
from typing import Optional, Sequence
from uuid import UUID
import numpy as np

from glitters_contracts import (
    AuthorizedCallerContext,
    PortfolioInvestmentBridge,
)



class GlittersContractsAdapter:
    """Seamless interoperability adapter with glitters_contracts v1.1.0."""

    @classmethod
    def emit_portfolio_bridge(
        cls,
        portfolio: Portfolio,
        solver_result: SolverExecutionResult,
        assets: Optional[Sequence[PropertyAsset]] = None,
        scenario_returns: Optional[np.ndarray] = None,
        scenario_probabilities: Optional[np.ndarray] = None,
    ) -> PortfolioInvestmentBridge:
        """Constructs the canonical PortfolioInvestmentBridge DTO with exact holdings and volatility."""
        target_assets = assets if assets is not None else portfolio.assets
        if len(solver_result.optimal_weights) != len(target_assets):
            raise ValueError(
                f"Length mismatch: solver optimal_weights ({len(solver_result.optimal_weights)}) "
                f"does not match target assets ({len(target_assets)})"
            )

        holdings = []
        for i, a in enumerate(target_assets):
            w = float(solver_result.optimal_weights[i])
            holdings.append((a.asset_id, round(w, 4)))

        total_cents = portfolio.total_portfolio_value_cents
        current_holdings_map: dict[str, float] = {}
        if total_cents > 0:
            for p_asset in portfolio.assets:
                p_val = p_asset.current_valuation_cents * p_asset.initial_ownership_fraction
                current_holdings_map[p_asset.asset_id] = p_val / total_cents

        rebal = any(
            abs(w_rec - current_holdings_map.get(aid, 0.0)) > 0.005
            for aid, w_rec in holdings
        )

        # Compute empirical scenario volatility if provided
        if (
            scenario_returns is not None
            and scenario_probabilities is not None
            and len(solver_result.optimal_weights) == scenario_returns.shape[0]
            and len(scenario_probabilities) > 0
        ):
            _, valid_probs = validate_scenario_distribution(scenario_returns, scenario_probabilities)
            port_scen_returns = solver_result.optimal_weights @ scenario_returns
            mu = float(np.sum(valid_probs * port_scen_returns))
            var = float(np.sum(valid_probs * ((port_scen_returns - mu) ** 2)))
            volatility = float(np.sqrt(max(0.0, var)))
        else:
            volatility = 0.0

        return PortfolioInvestmentBridge(
            portfolio_id=portfolio.portfolio_id,
            tenant_org_id=portfolio.tenant_org_id,
            recommended_holdings=tuple(holdings),
            expected_annual_return=solver_result.expected_return,
            portfolio_cvar_95=solver_result.cvar_alpha,
            portfolio_volatility=volatility,
            total_capital_deployed_cents=total_cents,
            rebalancing_required=rebal,
            requires_investment_committee_approval=True,
            generated_at_utc=datetime.now(timezone.utc),
        )
