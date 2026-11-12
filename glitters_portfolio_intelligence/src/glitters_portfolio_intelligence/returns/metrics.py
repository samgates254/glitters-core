from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Optional, Sequence, Tuple
import numpy as np
from scipy.optimize import brentq

from ..domain.enums import IRRStatus


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
