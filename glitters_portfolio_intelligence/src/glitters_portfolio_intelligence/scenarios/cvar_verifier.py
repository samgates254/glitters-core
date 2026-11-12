from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Sequence, Tuple
import numpy as np
from scipy.optimize import minimize_scalar

from .validation import validate_scenario_distribution


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
