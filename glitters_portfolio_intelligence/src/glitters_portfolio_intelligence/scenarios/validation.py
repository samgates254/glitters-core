from __future__ import annotations

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
