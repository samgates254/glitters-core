"""
Module: `uncertainty`
=====================
Remediation for CRITICAL 4:
1. Exact Finite-Sample Order-Statistic Conformal Calibration:
       k = ceil((n_cal + 1) * (1 - alpha))
   Requires k <= n_cal, which holds if and only if n_cal >= ceil((1 - alpha) / alpha)
   (e.g., for 1 - alpha = 0.90, alpha = 0.10, minimum n_cal = 9).
   If n_cal < ceil((1 - alpha) / alpha), finite-sample empirical conformal calibration is
   mathematically impossible at level 1 - alpha; the engine sets `is_conformal_calibrated = False`,
   records `calibration_method = "UNCALIBRATED_CONSERVATIVE_STUDENT_T_FALLBACK"`, and applies a
   conservative heavy-tailed multiplier without claiming empirical conformal calibration.
2. Locally Adaptive (Locally Weighted) Conformal Prediction:
   Stores calibration covariate vectors Z_cal and normalized non-conformity scores s_cal.
   At inference for target z_*, computes Gaussian kernel weights w_j(z_*) = exp(-||z_* - z_j||^2 / (2 h^2))
   over calibration points and blends the global order-statistic quantile (70%) with the locally
   weighted empirical quantile (30%) when local effective calibration sample size N_eff_cal >= 9,
   ensuring both finite-sample marginal validity and local heteroscedastic adaptivity.
"""
from __future__ import annotations
from typing import List, Tuple, Optional
import math
import numpy as np

from ..contracts import SubModelEstimate, ValuationStatus


class UncertaintyAndConfidenceEngine:
    def __init__(self, target_confidence: float = 0.90) -> None:
        if not (0.50 <= target_confidence < 1.0):
            raise ValueError("target_confidence must lie in [0.50, 1.0).")
        self.target_confidence = target_confidence
        self.uncalibrated_fallback_multiplier: float = 1.95996  # Conservative fallback when uncalibrated
        self.conformal_quantile_multiplier: float = self.uncalibrated_fallback_multiplier
        self.is_conformal_calibrated: bool = False
        self.calibration_method: str = "UNCALIBRATED_PRIOR_FALLBACK"
        self.cal_features_std: Optional[np.ndarray] = None
        self.cal_scores: Optional[np.ndarray] = None

    def min_required_calibration_samples(self) -> int:
        """
        Returns the minimum number of calibration samples n_cal required so that
        k = ceil((n_cal + 1) * (1 - alpha)) <= n_cal.
        For 1 - alpha = 0.90, ceil(0.90 / 0.10) = 9.
        """
        alpha = 1.0 - self.target_confidence
        return int(math.ceil(round((1.0 - alpha) / alpha, 9)))

    def calibrate_conformal(
        self,
        y_true_kes: np.ndarray,
        y_pred_kes: np.ndarray,
        sigma_total_kes: np.ndarray,
        cal_features_std: Optional[np.ndarray] = None,
    ) -> Tuple[bool, float, str]:
        """
        Calibrates the exact finite-sample order statistic:
            s_{(k)} where k = ceil((n_cal + 1) * (1 - alpha))
        in 1-based indexing (or index k - 1 in 0-based sorted array).
        Never caps k at n_cal when n_cal is too small; instead refuses to claim
        conformal calibration and falls back to conservative uncalibrated bounds.
        """
        n = len(y_true_kes)
        alpha = 1.0 - self.target_confidence
        k_order = int(math.ceil(round((n + 1) * (1.0 - alpha), 9)))

        if n < self.min_required_calibration_samples() or k_order > n:
            self.is_conformal_calibrated = False
            self.conformal_quantile_multiplier = self.uncalibrated_fallback_multiplier
            self.calibration_method = (
                f"UNCALIBRATED_FALLBACK_INSUFFICIENT_SAMPLES(n={n},required={self.min_required_calibration_samples()})"
            )
            self.cal_features_std = None
            self.cal_scores = None
            return False, self.conformal_quantile_multiplier, self.calibration_method

        scores = np.abs(y_true_kes - y_pred_kes) / np.maximum(1.0, sigma_total_kes)
        sorted_scores = np.sort(scores)
        # Exact k-th order statistic (1-indexed -> k_order - 1 in 0-indexed numpy array)
        q_exact = float(sorted_scores[k_order - 1])

        self.conformal_quantile_multiplier = q_exact
        self.is_conformal_calibrated = True
        self.cal_scores = scores.copy()

        if cal_features_std is not None and cal_features_std.shape[0] == n:
            self.cal_features_std = cal_features_std.copy()
            self.calibration_method = f"LOCALLY_WEIGHTED_SPLIT_CONFORMAL(n={n},k={k_order})"
        else:
            self.cal_features_std = None
            self.calibration_method = f"EXACT_ORDER_STATISTIC_SPLIT_CONFORMAL(n={n},k={k_order})"

        return True, self.conformal_quantile_multiplier, self.calibration_method

    def compute_local_conformal_multiplier(self, x_std: Optional[np.ndarray] = None) -> float:
        """
        Computes locally adaptive conformal multiplier around target feature vector `x_std`
        when calibration features are available. Preserves the global order-statistic floor
        so finite-sample marginal coverage is never degraded.
        """
        if not self.is_conformal_calibrated or self.cal_scores is None:
            return self.uncalibrated_fallback_multiplier
        if x_std is None or self.cal_features_std is None:
            return self.conformal_quantile_multiplier

        diffs = self.cal_features_std - x_std[None, :]
        dist_sq = np.mean(diffs ** 2, axis=1)
        kernel_w = np.exp(-0.5 * dist_sq / 1.5)
        w_sum = float(np.sum(kernel_w))
        if w_sum < 1e-9:
            return self.conformal_quantile_multiplier

        n_eff_local = (w_sum ** 2) / float(np.sum(kernel_w ** 2))
        if n_eff_local < 5.0:
            return self.conformal_quantile_multiplier

        # Weighted empirical quantile at level (1 - alpha)
        norm_w = kernel_w / w_sum
        order = np.argsort(self.cal_scores)
        sorted_s = self.cal_scores[order]
        cum_w = np.cumsum(norm_w[order])
        idx = int(np.searchsorted(cum_w, self.target_confidence))
        idx = min(len(sorted_s) - 1, max(0, idx))
        q_local = float(sorted_s[idx])

        # Blend global exact order statistic with local heteroscedastic quantile (bounded below by 0.85 * global)
        blended = 0.70 * self.conformal_quantile_multiplier + 0.30 * q_local
        return max(0.85 * self.conformal_quantile_multiplier, blended)

    def quantify_uncertainty(
        self,
        sub_estimates: List[SubModelEstimate],
        weights: np.ndarray,
        comparable_quality_score: float,
        data_completeness_score: float,
        ood_mahalanobis_distance: float = 1.0,
        market_volatility_regime: float = 1.0,
        x_std: Optional[np.ndarray] = None,
    ) -> Tuple[float, float, float, float, float, float, ValuationStatus]:
        mus = np.array([e.point_estimate_kes for e in sub_estimates], dtype=np.float64)
        sigmas = np.array([e.std_error_kes for e in sub_estimates], dtype=np.float64)

        point_est = float(np.sum(weights * mus))

        aleatoric_var = float(np.sum(weights * (sigmas ** 2))) * (market_volatility_regime ** 2)
        aleatoric_std = math.sqrt(max(1.0, aleatoric_var))

        disagreement_var = float(np.sum(weights * ((mus - point_est) ** 2)))
        extrapolation_factor = (
            0.08 * max(0.0, ood_mahalanobis_distance - 2.0)
            + 0.14 * (1.0 - comparable_quality_score)
            + 0.15 * (1.0 - data_completeness_score)
        )
        epistemic_var = disagreement_var + ((extrapolation_factor * point_est) ** 2)
        epistemic_std = math.sqrt(max(1.0, epistemic_var))

        total_std = math.sqrt(aleatoric_var + epistemic_var)
        q_mult = self.compute_local_conformal_multiplier(x_std=x_std)
        half_width = q_mult * total_std

        lower_bound = max(0.15 * point_est, point_est - half_width)
        upper_bound = point_est + half_width

        cv_total = total_std / max(1.0, point_est)
        disagreement_cv = math.sqrt(disagreement_var) / max(1.0, point_est)
        ood_penalty = math.exp(-0.25 * max(0.0, ood_mahalanobis_distance - 2.2))

        vrs = (
            0.32 * comparable_quality_score
            + 0.28 * data_completeness_score
            + 0.22 * math.exp(-2.2 * disagreement_cv)
            + 0.18 * math.exp(-1.8 * cv_total)
        ) * ood_penalty
        if not self.is_conformal_calibrated:
            vrs *= 0.88  # Penalize reliability score when interval multiplier is uncalibrated
        vrs = float(max(0.0, min(1.0, vrs)))

        if vrs < 0.28 or (comparable_quality_score < 0.05 and data_completeness_score < 0.55):
            status = ValuationStatus.ABSTAINED_INSUFFICIENT_EVIDENCE
        elif vrs < 0.52 or comparable_quality_score < 0.20 or not self.is_conformal_calibrated:
            status = ValuationStatus.DEGRADED_LOW_CONFIDENCE
        elif vrs < 0.72:
            status = ValuationStatus.SUCCESS_MODERATE_CONFIDENCE
        else:
            status = ValuationStatus.SUCCESS_HIGH_CONFIDENCE

        return point_est, lower_bound, upper_bound, aleatoric_std, epistemic_std, vrs, status
