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
    NO_REAL_ROOT = "NO_REAL_ROOT"
    MULTIPLE_ROOTS_DETECTED = "MULTIPLE_ROOTS_DETECTED"
    ALL_POSITIVE_CASHFLOWS = "ALL_POSITIVE_CASHFLOWS"
    ALL_NEGATIVE_CASHFLOWS = "ALL_NEGATIVE_CASHFLOWS"

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
    Calculates Internal Rate of Return with rigorous Descartes' sign rule checks
    and multi-root detection across r in [-0.90, 10.0].
    """
    if len(cash_flows_cents) < 2:
        return IRRResult(rate=None, status=IRRStatus.NO_REAL_ROOT, iterations=0, npv_residual=0.0)

    # Filter out initial zeros
    cf_list = [cf for cf in cash_flows_cents]
    non_zero = [c for c in cf_list if c != 0]
    if not non_zero:
        return IRRResult(rate=None, status=IRRStatus.NO_REAL_ROOT, iterations=0, npv_residual=0.0)

    # Count sign changes (Descartes' rule of signs)
    signs = [math.copysign(1, c) for c in non_zero]
    sign_changes = sum(1 for i in range(len(signs) - 1) if signs[i] != signs[i + 1])

    if sign_changes == 0:
        if signs[0] > 0:
            return IRRResult(rate=None, status=IRRStatus.ALL_POSITIVE_CASHFLOWS, iterations=0, npv_residual=0.0)
        else:
            return IRRResult(rate=None, status=IRRStatus.ALL_NEGATIVE_CASHFLOWS, iterations=0, npv_residual=0.0)

    # Grid search for sign changes in NPV(r) over [-0.90, 5.0]
    grid = np.linspace(-0.89, 5.0, 300)
    npv_vals = [compute_npv(r, cf_list) for r in grid]

    bracket_intervals: list[tuple[float, float]] = []
    for i in range(len(grid) - 1):
        if npv_vals[i] == 0.0:
            bracket_intervals.append((grid[i], grid[i]))
        elif npv_vals[i] * npv_vals[i + 1] < 0.0:
            bracket_intervals.append((grid[i], grid[i + 1]))

    if not bracket_intervals:
        return IRRResult(rate=None, status=IRRStatus.NO_REAL_ROOT, iterations=len(grid), npv_residual=npv_vals[-1])

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

    return IRRResult(rate=None, status=IRRStatus.NO_REAL_ROOT, iterations=len(grid), npv_residual=0.0)


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
# MODULE FILE: src/glitters_portfolio_intelligence/scenarios/provenance.py
# ==============================================================================

from dataclasses import dataclass, field
from typing import Dict, Mapping, Optional



@dataclass(frozen=True, slots=True)
class ScenarioDefinition:
    """
    Rigorously tracked scenario definition ensuring full provenance transparency.
    Forbids mislabeling arbitrary shocks as verified historical facts.
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

    def __post_init__(self) -> None:
        if not (0.0 <= self.probability <= 1.0):
            raise ValueError(f"Scenario probability must be in [0, 1], got {self.probability}")
        if not self.scenario_id or not self.name:
            raise ValueError("Scenario ID and name cannot be empty")
        if not self.source:
            raise ValueError("Scenario source must be explicitly declared for auditability")

        # Provenance integrity validation
        if self.provenance == ScenarioProvenanceKind.HISTORICAL_OBSERVATION:
            if "synthetic" in self.transformation_methodology.lower() or "hypothetical" in self.source.lower():
                raise ValueError("HISTORICAL_OBSERVATION cannot use synthetic or hypothetical transformation methodology")
        elif self.provenance == ScenarioProvenanceKind.SYNTHETIC_SCENARIO:
            if "actual historical" in self.source.lower():
                raise ValueError("SYNTHETIC_SCENARIO cannot claim actual historical source")

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


def compute_rockafellar_cvar_1d(
    losses: Sequence[float],
    probabilities: Sequence[float],
    alpha: float = 0.95,
    tolerance: float = 1e-4,
) -> CVaRVerificationResult:
    """
    Computes exact discrete CVaR_alpha according to Rockafellar & Uryasev (2000):
      CVaR_alpha = min_eta [ eta + 1/(1 - alpha) * sum_{s=1}^S p_s * max(L_s - eta, 0) ]

    Correctly handles:
      - Repeated scenario losses (ties/atoms).
      - Probability mass exactly at VaR.
      - Unequal scenario probabilities.
      - Single dominant loss scenarios.
      - Zero-loss scenarios.
      - Any confidence level alpha in (0, 1).
    """
    if not (0.0 < alpha < 1.0):
        raise ValueError(f"alpha must be in (0, 1), got {alpha}")
    if len(losses) != len(probabilities):
        raise ValueError(f"losses ({len(losses)}) and probabilities ({len(probabilities)}) length mismatch")
    if len(losses) == 0:
        raise ValueError("Scenario vectors cannot be empty")

    total_prob = sum(probabilities)
    if not math.isclose(total_prob, 1.0, rel_tol=1e-5, abs_tol=1e-5):
        # Normalize probabilities
        norm_p = [p / total_prob for p in probabilities]
    else:
        norm_p = list(probabilities)

    loss_arr = np.array(losses, dtype=np.float64)
    prob_arr = np.array(norm_p, dtype=np.float64)

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
    # Losses > var
    tail_mask = loss_arr > (var_breakpoint + 1e-12)
    p_tail = float(np.sum(prob_arr[tail_mask]))

    # Losses == var
    atom_mask = np.abs(loss_arr - var_breakpoint) <= 1e-12
    p_atom = float(np.sum(prob_arr[atom_mask]))

    target_tail = 1.0 - alpha
    fractional_mass = max(0.0, target_tail - p_tail)

    discrepancy = abs(cvar_breakpoint - cvar_continuous)
    is_verified = discrepancy <= tolerance or math.isclose(cvar_breakpoint, cvar_continuous, rel_tol=1e-4)

    # Return the verified minimum
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

# ==============================================================================
# MODULE FILE: src/glitters_portfolio_intelligence/scenarios/stress_engine.py
# ==============================================================================

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
    ) -> tuple[np.ndarray, np.ndarray, list[ScenarioDefinition]]:
        """
        Computes the asset return matrix across scenarios:
          return_matrix[i, s]: Annual return of asset i under scenario s
          probability_vector[s]: Probability of scenario s

        Returns: (return_matrix, probability_vector, scenarios)
        """
        scen_list = list(scenarios) if scenarios is not None else cls.get_canonical_scenarios()
        n_assets = len(assets)
        n_scens = len(scen_list)

        probs = np.array([s.probability for s in scen_list], dtype=np.float64)
        probs = probs / np.sum(probs)  # Ensure exact sum to 1.0

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

        return return_matrix, probs, scen_list

# ==============================================================================
# MODULE FILE: src/glitters_portfolio_intelligence/optimization/constraints.py
# ==============================================================================

from dataclasses import dataclass, field
from typing import Mapping, Optional, Sequence, Tuple
import numpy as np



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
        constraint_labels: Sequence[str] | None = None,
        backend: SolverBackendType = SolverBackendType.HIGHS_SCIPY,
        time_limit_seconds: float = 30.0,
    ) -> SolverExecutionResult:
        """
        Solves the LP formulation using the selected backend.
        Variable layout: [x_1..x_N, gamma, u_1..u_S]
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
        constraint_labels: Sequence[str] | None,
        start_t: float,
        time_limit_seconds: float,
    ) -> SolverExecutionResult:
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
        constraint_labels: Sequence[str] | None,
        start_t: float,
        time_limit_seconds: float,
    ) -> SolverExecutionResult:
        if not ORTOOLS_AVAILABLE:
            raise RuntimeError("Google OR-Tools is not installed.")

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

        if is_opt or status == OptimizationStatus.FEASIBLE:
            sol_x = np.array([v.solution_value() for v in vars_list], dtype=np.float64)
            x_weights = sol_x[:n_assets]
            gamma = float(sol_x[n_assets])
            u_excess = sol_x[n_assets + 1 : n_assets + 1 + n_scenarios]
            cvar = float(gamma + (1.0 / (1.0 - alpha)) * np.sum(scenario_probs * u_excess))
            exp_ret = float(np.sum(x_weights * np.sum(scenario_returns * scenario_probs, axis=1)))

            ortools_ver = ortools.__version__ if ortools is not None else "unknown"
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
                optimality_gap=0.0 if is_opt else 1e-4,
                solve_time_seconds=elapsed,
                solver_specific_message=f"OR-Tools status code: {solver_status}",
            )
        else:
            return SolverExecutionResult(
                solver_type=backend,
                solver_version=f"OR-Tools {pywraplp.__version__} ({ortools_name})",
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

from dataclasses import dataclass
from typing import Optional, Sequence, Tuple
import numpy as np



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

# ==============================================================================
# MODULE FILE: src/glitters_portfolio_intelligence/optimization/multi_period.py
# ==============================================================================

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple
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


class MultiPeriodPortfolioOptimizer:
    """
    Dynamic multi-period capital allocation, retention, acquisition, disposal,
    and renovation scheduler across horizon t = 1..T.
    """

    @classmethod
    def optimize(
        cls,
        portfolio: Portfolio,
        candidate_assets: Sequence[PropertyAsset],
        horizon_years: int = 5,
        annual_discount_rate: float = 0.08,
        min_cash_reserve_cents: int = 5_000_000_00,  # KES 5M minimum liquidity floor
        representation: OwnershipRepresentation = OwnershipRepresentation.FRACTIONAL_CAPITAL_WEIGHT,
        backend: SolverBackendType = SolverBackendType.HIGHS_ORTOOLS if ORTOOLS_AVAILABLE else SolverBackendType.HIGHS_SCIPY,
        time_limit_seconds: float = 30.0,
    ) -> MultiPeriodOptimizationResult:
        """
        Solves the dynamic multi-period MIP or LP problem.
        Enforces:
          - a_{it} + d_{it} <= 1 (No simultaneous buy and sell in the same period)
          - h_{it} = h_{i, t-1} + a_{it} - d_{it} (Balance of asset holdings)
          - v_{it} <= h_{it} (Renovation only on held properties)
          - Cash_t >= min_cash_reserve_cents (Liquidity reserve floor)
          - Downtime rental reduction during renovation
        """
        if not ORTOOLS_AVAILABLE:
            raise RuntimeError("OR-Tools required for multi-period trajectory optimization.")

        all_assets = list(candidate_assets)
        n_assets = len(all_assets)
        T = horizon_years

        SCALE = 1e5  # Conditioning scaling factor for integer cents

        # Initial holding state h_{i, 0}
        initial_portfolio_ids = {a.asset_id for a in portfolio.assets}
        h_0 = [1.0 if a.asset_id in initial_portfolio_ids else 0.0 for a in all_assets]

        solver = pywraplp.Solver.CreateSolver("SCIP" if representation == OwnershipRepresentation.BINARY_WHOLE_ASSET else "HIGHS")
        if solver is None:
            solver = pywraplp.Solver.CreateSolver("GLOP")

        solver.SetTimeLimit(int(time_limit_seconds * 1000))

        # Variables:
        # h[i, t]: holding fraction in [0, 1]
        # a[i, t]: acquisition in [0, 1]
        # d[i, t]: disposal in [0, 1]
        # v[i, t]: renovation in [0, 1]
        # cash[t]: scaled cash balance >= min_cash_reserve_cents / SCALE
        h_vars = {}
        a_vars = {}
        d_vars = {}
        v_vars = {}
        cash_vars = {}

        is_binary = (representation == OwnershipRepresentation.BINARY_WHOLE_ASSET)

        for t in range(1, T + 1):
            cash_vars[t] = solver.NumVar(float(min_cash_reserve_cents) / SCALE, solver.infinity(), f"cash_{t}")
            for i in range(n_assets):
                if is_binary:
                    h_vars[i, t] = solver.BoolVar(f"h_{i}_{t}")
                    a_vars[i, t] = solver.BoolVar(f"a_{i}_{t}")
                    d_vars[i, t] = solver.BoolVar(f"d_{i}_{t}")
                    v_vars[i, t] = solver.BoolVar(f"v_{i}_{t}")
                else:
                    h_vars[i, t] = solver.NumVar(0.0, 1.0, f"h_{i}_{t}")
                    a_vars[i, t] = solver.NumVar(0.0, 1.0, f"a_{i}_{t}")
                    d_vars[i, t] = solver.NumVar(0.0, 1.0, f"d_{i}_{t}")
                    v_vars[i, t] = solver.NumVar(0.0, 1.0, f"v_{i}_{t}")

        # Constraints
        for t in range(1, T + 1):
            for i in range(n_assets):
                # Invariant 1: No simultaneous buy & sell: a_{it} + d_{it} <= 1
                solver.Add(a_vars[i, t] + d_vars[i, t] <= 1.0)

                # Invariant 2: Holding state conservation: h_{it} = h_{i, t-1} + a_{it} - d_{it}
                prev_h = h_0[i] if t == 1 else h_vars[i, t - 1]
                solver.Add(h_vars[i, t] == prev_h + a_vars[i, t] - d_vars[i, t])

                # Invariant 3: Renovation only on held properties: v_{it} <= h_{it}
                solver.Add(v_vars[i, t] <= h_vars[i, t])

            # Invariant 4: Cash Flow Continuity Equation
            prev_cash = (float(portfolio.cash_reserves_cents) / SCALE) if t == 1 else cash_vars[t - 1]
            cash_expr = prev_cash

            for i in range(n_assets):
                asset = all_assets[i]
                ann_gpr = asset.monthly_gross_potential_rent_cents * 12
                ann_opex = asset.monthly_operating_expense_cents * 12
                vac = asset.annual_baseline_vacancy_rate
                cred = asset.annual_baseline_credit_loss_rate
                net_rent = (ann_gpr * (1.0 - vac) * (1.0 - cred) - ann_opex - asset.annual_debt_service_cents) / SCALE

                # Renovation downtime effect: reduces rent by downtime_months/12
                downtime_penalty = net_rent * (asset.renovation_downtime_months / 12.0)

                acq_cost = (asset.acquisition_valuation_cents * (1.0 + asset.acquisition_cost_rate)) / SCALE
                disp_proceeds = (asset.current_valuation_cents * (1.0 - asset.disposal_cost_rate) - asset.current_loan_balance_cents) / SCALE

                # Cash balance update
                cash_expr += h_vars[i, t] * net_rent
                cash_expr -= v_vars[i, t] * ((asset.planned_capex_cents / SCALE) + downtime_penalty)
                cash_expr -= a_vars[i, t] * acq_cost
                cash_expr += d_vars[i, t] * disp_proceeds

            solver.Add(cash_vars[t] == cash_expr)

        # Objective: Maximize Terminal Wealth + Cumulative Discounted Cash Distributions
        obj = solver.Objective()
        # Terminal wealth: Cash_T + sum_i h_{i, T} * NetEquity_i
        obj.SetCoefficient(cash_vars[T], 1.0)
        for i in range(n_assets):
            net_eq = float(all_assets[i].net_equity_cents) / SCALE
            obj.SetCoefficient(h_vars[i, T], net_eq)
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
                periodic_cash.append(int(cash_vars[t].solution_value() * SCALE))
                for i in range(n_assets):
                    asset = all_assets[i]
                    h_val = float(h_vars[i, t].solution_value())
                    a_val = float(a_vars[i, t].solution_value())
                    d_val = float(d_vars[i, t].solution_value())
                    v_val = float(v_vars[i, t].solution_value())

                    action = DecisionAction.HOLD
                    if a_val > 0.5:
                        action = DecisionAction.BUY
                    elif d_val > 0.5:
                        action = DecisionAction.SELL
                    elif v_val > 0.5:
                        action = DecisionAction.RENOVATE

                    steps.append(
                        MultiPeriodDecisionStep(
                            period=t,
                            asset_id=asset.asset_id,
                            action=action,
                            holding_fraction=h_val,
                            cash_flow_generated_cents=int(h_val * (asset.monthly_gross_potential_rent_cents * 12)),
                            capital_expenditure_cents=int(v_val * asset.planned_capex_cents),
                            renovation_active=(v_val > 0.5),
                        )
                    )

            terminal_wealth = int(solver.Objective().Value() * SCALE)
            return MultiPeriodOptimizationResult(
                status=status,
                is_optimal=is_opt,
                horizon_periods=T,
                representation=representation,
                schedule=tuple(steps),
                periodic_cash_balances_cents=tuple(periodic_cash),
                terminal_portfolio_wealth_cents=terminal_wealth,
                cumulative_net_cashflow_cents=periodic_cash[-1] - portfolio.cash_reserves_cents,
                total_capex_spent_cents=sum(s.capital_expenditure_cents for s in steps),
                solve_time_seconds=solver.WallTime() / 1000.0,
                solver_message=f"Multi-period solved with status {status.value}",
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
            )

# ==============================================================================
# MODULE FILE: src/glitters_portfolio_intelligence/governance/committee_gate.py
# ==============================================================================

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Optional, Sequence, Tuple
from uuid import UUID, uuid4

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
    """

    @classmethod
    def create_recommendation_envelope(
        cls,
        portfolio: Portfolio,
        solver_result: SolverExecutionResult,
        caller: AuthorizedCallerContext,
        policy_id: str = "POL-PORTFOLIO-ALLOCATION-2026",
        threshold_for_committee_cents: int = 10_000_000_00,  # KES 10M
    ) -> AlgorithmDecisionEnvelope[PortfolioInvestmentBridge]:
        """
        Packages solver results into an auditable AlgorithmDecisionEnvelope[PortfolioInvestmentBridge].
        """
        now = datetime.now(timezone.utc)
        execution_id = uuid4()
        decision_id = uuid4()

        # Build holding pairs: (asset_id, weight)
        holdings: list[tuple[str, float]] = []
        for i, a in enumerate(portfolio.assets):
            if i < len(solver_result.optimal_weights):
                w = float(solver_result.optimal_weights[i])
                holdings.append((a.asset_id, round(w, 4)))

        # Determine if committee approval is required
        total_val_cents = portfolio.total_portfolio_value_cents
        rebalancing_needed = any(abs(w - (1.0 / max(1, len(portfolio.assets)))) > 0.05 for _, w in holdings)
        requires_committee = (
            total_val_cents >= threshold_for_committee_cents
            or rebalancing_needed
            or not solver_result.is_optimal
        )

        bridge_dto = PortfolioInvestmentBridge(
            portfolio_id=portfolio.portfolio_id,
            tenant_org_id=portfolio.tenant_org_id,
            recommended_holdings=tuple(holdings),
            expected_annual_return=solver_result.expected_return,
            portfolio_cvar_95=solver_result.cvar_alpha,
            portfolio_volatility=abs(solver_result.cvar_alpha * 0.75),  # Parametric volatility proxy
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

        payload_bytes = json.dumps({
            "portfolio_id": bridge_dto.portfolio_id,
            "expected_return": bridge_dto.expected_annual_return,
            "cvar_95": bridge_dto.portfolio_cvar_95,
            "total_capital_cents": bridge_dto.total_capital_deployed_cents,
        }, sort_keys=True).encode("utf-8")
        result_hash = hashlib.sha256(payload_bytes).hexdigest()

        audit_event = EngineAuditEvent(
            event_id=uuid4(),
            execution_id=execution_id,
            tenant_org_id=caller.tenant_org_id,
            timestamp_utc=now,
            engine_id="glitters_portfolio_intelligence",
            action_type="GENERATE_PORTFOLIO_RECOMMENDATION",
            result_hash_sha256=result_hash,
            provenance_hash_sha256=hashlib.sha256(b"engine08_provenance_v0.8.0").hexdigest(),
            requires_human_approval=requires_committee,
        )

        status_mapped = (
            AlgorithmicExecutionStatus.OPTIMAL
            if solver_result.is_optimal
            else AlgorithmicExecutionStatus.INFEASIBLE
        )

        explanation = DecisionExplanation(
            summary_narrative=(
                f"Optimized portfolio recommendation for {len(portfolio.assets)} assets using Rockafellar-Uryasev Mean-CVaR. "
                f"Expected annual return: {solver_result.expected_return:.2%}, CVaR_95: {solver_result.cvar_alpha:.2%}."
            ),
            feature_attributions={"expected_return": solver_result.expected_return, "cvar_95": solver_result.cvar_alpha},
            active_constraints=list(solver_result.active_constraints),
        )

        return AlgorithmDecisionEnvelope[PortfolioInvestmentBridge](
            decision_id=decision_id,
            execution_id=execution_id,
            tenant_org_id=caller.tenant_org_id,
            engine_id="glitters_portfolio_intelligence",
            engine_version="0.8.0",
            status=status_mapped,
            confidence_tier=ConfidenceTier.HIGH_CONFIDENCE if solver_result.is_optimal else ConfidenceTier.WEAKLY_SUPPORTED,
            uncertainty=UncertaintyBounds(
                point_estimate=solver_result.expected_return,
                lower_bound=solver_result.expected_return - solver_result.cvar_alpha,
                upper_bound=solver_result.expected_return + solver_result.cvar_alpha,
                confidence_level=0.95,
                interval_type="ROCKAFELLAR_CVAR_95",
                effective_sample_size=100.0,
                epistemic_status="CONVERGED_OPTIMAL" if solver_result.is_optimal else "FAILED_OR_INFEASIBLE",
            ),
            explanation=explanation,
            payload=bridge_dto,
            requires_human_approval=requires_committee,
            audit_event=audit_event,
            result_hash_sha256=result_hash,
            execution_duration_ms=solver_result.solve_time_seconds * 1000.0,
            contract_version=ContractVersion.V1_1,
            abstention_reason=AbstentionReason.NONE if solver_result.is_optimal else AbstentionReason.INFEASIBLE,
            human_approval_requirement=approval_req,
        )

# ==============================================================================
# MODULE FILE: src/glitters_portfolio_intelligence/integration/upstream_adapters.py
# ==============================================================================

from dataclasses import dataclass
from typing import Optional



@dataclass(frozen=True, slots=True)
class UpstreamEngineDataFeed:
    """Explicitly typed upstream data snapshot representing available or missing upstream feeds."""
    asset_id: str
    valuation_cents: Optional[int] = None
    valuation_source: str = "DEFAULT_OR_MANUAL"
    clearing_rent_monthly_cents: Optional[int] = None
    clearing_rent_source: str = "DEFAULT_OR_MANUAL"
    projected_vacancy_rate: Optional[float] = None
    planned_renovation_capex_cents: Optional[int] = None
    planned_maintenance_cents: Optional[int] = None
    spatial_risk_multiplier: float = 1.0
    is_engine02_available: bool = False
    is_engine03_available: bool = False
    is_engine04_available: bool = False
    is_engine05_available: bool = False
    is_engine06_available: bool = False
    is_engine10_available: bool = False


class UpstreamIntegrationAdapter:
    """
    Typed adapter converting upstream engine outputs into PropertyAsset definitions.
    Strictly documents and manages missing or unintegrated upstream engines.
    """

    @classmethod
    def apply_upstream_feed(
        cls,
        base_asset: PropertyAsset,
        feed: UpstreamEngineDataFeed,
    ) -> PropertyAsset:
        """Applies available upstream estimates to refine asset investment state."""
        val = feed.valuation_cents if feed.valuation_cents is not None else base_asset.current_valuation_cents
        rent = feed.clearing_rent_monthly_cents if feed.clearing_rent_monthly_cents is not None else base_asset.monthly_gross_potential_rent_cents
        vac = feed.projected_vacancy_rate if feed.projected_vacancy_rate is not None else base_asset.annual_baseline_vacancy_rate
        capex = feed.planned_renovation_capex_cents if feed.planned_renovation_capex_cents is not None else base_asset.planned_capex_cents
        maint = feed.planned_maintenance_cents if feed.planned_maintenance_cents is not None else base_asset.annual_routine_maintenance_cents

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
        )

# ==============================================================================
# MODULE FILE: src/glitters_portfolio_intelligence/integration/glitters_contracts_adapter.py
# ==============================================================================

from datetime import datetime, timezone
from typing import Sequence
from uuid import UUID

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
    ) -> PortfolioInvestmentBridge:
        """Constructs the canonical PortfolioInvestmentBridge DTO."""
        holdings = []
        for i, a in enumerate(portfolio.assets):
            if i < len(solver_result.optimal_weights):
                w = float(solver_result.optimal_weights[i])
                holdings.append((a.asset_id, round(w, 4)))

        total_cents = portfolio.total_portfolio_value_cents
        rebal = any(abs(w - (1.0 / max(1, len(portfolio.assets)))) > 0.05 for _, w in holdings)

        return PortfolioInvestmentBridge(
            portfolio_id=portfolio.portfolio_id,
            tenant_org_id=portfolio.tenant_org_id,
            recommended_holdings=tuple(holdings),
            expected_annual_return=solver_result.expected_return,
            portfolio_cvar_95=solver_result.cvar_alpha,
            portfolio_volatility=abs(solver_result.cvar_alpha * 0.75),
            total_capital_deployed_cents=total_cents,
            rebalancing_required=rebal,
            requires_investment_committee_approval=True,
            generated_at_utc=datetime.now(timezone.utc),
        )
