"""
Module: `spatial_models`
========================
Remediation for Spatial GP Scalability & Rental Separation:
1. Vectorized Global Moran's I computation (eliminating slow Python nested loops).
2. Nyström / Inducing-Point Sparse Gaussian Process Regression (Subset of Regressors / Deterministic
   Training Conditional approximation with M <= max_inducing_points landmark knots):
   - Reduces training complexity from O(N^3) to O(N M^2 + M^3) and memory from O(N^2) to O(N M).
   - Reduces online inference complexity from O(N) to O(M).
   - Automatically uses exact Cholesky Kriging when N <= max_inducing_points and switches to
     Sparse Inducing-Point Kriging when N > max_inducing_points.
3. Strictly excludes rental observations (`RENTAL_LEASE_CONTRACT`, `RENTAL_ASKING_QUOTE`) from
   capital-value spatial training (CRITICAL 1).
"""
from __future__ import annotations
from typing import List, Tuple, Optional
import math
import numpy as np

from ..contracts import (
    PropertyRecord,
    MarketTransaction,
    PriceEvidenceType,
    CAPITAL_SALE_EVIDENCE_TYPES,
    SubModelEstimate,
)
from ..config import ValuationMarketConfig
from ..data_validation import haversine_matrix_km
from ..feature_engineering import FeatureEngineeringPipeline, compute_leasehold_discount_factor


def compute_morans_i(
    coords_lat_lon: np.ndarray,
    residuals: np.ndarray,
    bandwidth_km: float = 3.0,
    max_subsample: int = 800,
) -> Tuple[float, float]:
    """
    Vectorized Global Moran's I statistic and normal-approximation z-score.
    For massive datasets (N > max_subsample), evaluates on a stratified spatial subsample
    to bound memory at O(min(N, max_subsample)^2).
    """
    n_full = len(residuals)
    if n_full < 5:
        return 0.0, 0.0

    if n_full > max_subsample:
        idx = np.linspace(0, n_full - 1, max_subsample, dtype=int)
        coords = coords_lat_lon[idx]
        res = residuals[idx]
    else:
        coords = coords_lat_lon
        res = residuals

    n = len(res)
    e = res - np.mean(res)
    denom = float(np.sum(e ** 2))
    if denom < 1e-12:
        return 0.0, 0.0

    dist_mat = haversine_matrix_km(coords, coords)
    bw = max(0.2, bandwidth_km)
    W = np.where((dist_mat > 0.0) & (dist_mat <= 3.5 * bw), np.exp(-dist_mat / bw), 0.0)
    np.fill_diagonal(W, 0.0)

    s0 = float(np.sum(W))
    if s0 < 1e-9:
        return 0.0, 0.0

    moran_i = (float(n) / s0) * float(e.T @ W @ e) / denom
    expected_i = -1.0 / (n - 1.0)
    s1 = 0.5 * float(np.sum((W + W.T) ** 2))
    var_i = max(1e-6, (n * s1 - 3.0 * s0 ** 2) / ((n - 1.0) * (n + 1.0) * (s0 ** 2)))
    z_score = (moran_i - expected_i) / math.sqrt(var_i)
    return float(moran_i), float(z_score)


class SpatialGPValuationModel:
    """
    Scalable Universal Kriging / Spatial Gaussian Process Model with Matérn(nu = 3/2)
    covariance kernel and Nyström Inducing-Point Approximation for large N:
        K_{NN} approx K_{NM} K_{MM}^{-1} K_{MN} + sigma_n^2 I_N
    By the Woodbury matrix identity, inversion requires only an M x M matrix:
        Sigma_M = K_{MM} + (1 / sigma_n^2) K_{MN} K_{NM}   in R^{M x M},
    yielding O(N M^2 + M^3) training complexity and O(M) online prediction complexity.
    """

    def __init__(
        self,
        length_scale_km: float = 2.8,
        signal_variance: float = 0.12,
        nugget_variance: float = 0.025,
        max_inducing_points: int = 64,
        config: Optional[ValuationMarketConfig] = None,
    ) -> None:
        self.length_scale_km = length_scale_km
        self.signal_variance = signal_variance
        self.nugget_variance = nugget_variance
        self.max_inducing_points = max_inducing_points
        self.config = config or ValuationMarketConfig()
        self.fe = FeatureEngineeringPipeline(config=self.config)

        self.inducing_coords: Optional[np.ndarray] = None
        self.trend_beta: Optional[np.ndarray] = None
        self.trend_intercept: float = 0.0
        self.feat_mean: Optional[np.ndarray] = None
        self.feat_std: Optional[np.ndarray] = None
        self.alpha_inducing: Optional[np.ndarray] = None
        self.L_mm: Optional[np.ndarray] = None
        self.L_sigma_m: Optional[np.ndarray] = None
        self.is_sparse_approximation: bool = False
        self.morans_i: float = 0.0
        self.morans_z: float = 0.0
        self.is_fitted: bool = False

    def _matern32_matrix(self, coords1: np.ndarray, coords2: np.ndarray) -> np.ndarray:
        dist_mat = haversine_matrix_km(coords1, coords2)
        sqrt3_r = (math.sqrt(3.0) / max(0.1, self.length_scale_km)) * dist_mat
        return self.signal_variance * (1.0 + sqrt3_r) * np.exp(-sqrt3_r)

    def _select_inducing_points(self, coords: np.ndarray) -> np.ndarray:
        """Selects M spatially stratified inducing landmarks across the coordinate distribution."""
        n = coords.shape[0]
        if n <= self.max_inducing_points:
            return coords.copy()
        # Sort by spatial Hilbert-like key (lat + lon) and take evenly spaced landmarks
        order = np.argsort(coords[:, 0] * 100.0 + coords[:, 1])
        idx = np.linspace(0, n - 1, self.max_inducing_points, dtype=int)
        return coords[order[idx]].copy()

    def fit(self, transactions: List[MarketTransaction]) -> "SpatialGPValuationModel":
        # CRITICAL 1: Strictly filter to capital sale observations
        sale_txs = [tx for tx in transactions if tx.evidence_type in CAPITAL_SALE_EVIDENCE_TYPES]
        if len(sale_txs) < 5:
            raise ValueError(
                f"SpatialGPValuationModel requires at least 5 capital sale transactions; got {len(sale_txs)}."
            )

        props = [tx.property_record for tx in sale_txs]
        X_raw = self.fe.transform_batch(props)
        coords = np.array([[p.latitude, p.longitude] for p in props], dtype=np.float64)
        y_log = np.array([
            math.log(max(10_000.0, tx.observed_price_kes * (
                self.config.asking_price_negotiation_haircut if tx.evidence_type == PriceEvidenceType.ASKING_LISTING_PRICE else 1.0
            )))
            for tx in sale_txs
        ], dtype=np.float64)

        self.feat_mean = np.mean(X_raw, axis=0)
        self.feat_std = np.where(np.std(X_raw, axis=0) < 1e-6, 1.0, np.std(X_raw, axis=0))
        X_std = (X_raw - self.feat_mean) / self.feat_std

        n, d = X_std.shape
        X_aug = np.column_stack([np.ones(n), X_std])
        reg = np.eye(d + 1) * 2.5
        reg[0, 0] = 1e-5
        theta = np.linalg.solve(X_aug.T @ X_aug + reg, X_aug.T @ y_log)
        self.trend_intercept = float(theta[0])
        self.trend_beta = theta[1:]

        trend_residuals = y_log - (self.trend_intercept + X_std @ self.trend_beta)
        self.morans_i, self.morans_z = compute_morans_i(coords, trend_residuals, bandwidth_km=self.length_scale_km)

        if n <= self.max_inducing_points:
            self.is_sparse_approximation = False
            self.inducing_coords = coords
            K = self._matern32_matrix(coords, coords) + np.eye(n) * self.nugget_variance
            self.L_mm = np.linalg.cholesky(K)
            self.L_sigma_m = self.L_mm
            y_tmp = np.linalg.solve(self.L_mm, trend_residuals)
            self.alpha_inducing = np.linalg.solve(self.L_mm.T, y_tmp)
        else:
            # Inducing-Point Sparse GP (DTC / Nyström approximation in O(N M^2 + M^3))
            self.is_sparse_approximation = True
            Z = self._select_inducing_points(coords)
            m = Z.shape[0]
            self.inducing_coords = Z
            K_mm = self._matern32_matrix(Z, Z) + np.eye(m) * 1e-5
            K_nm = self._matern32_matrix(coords, Z)
            self.L_mm = np.linalg.cholesky(K_mm)

            # Sigma_M = K_mm + (1 / sigma_n^2) K_mn K_nm
            inv_nug = 1.0 / max(1e-5, self.nugget_variance)
            Sigma_M = K_mm + inv_nug * (K_nm.T @ K_nm)
            self.L_sigma_m = np.linalg.cholesky(Sigma_M)

            # alpha_inducing = Sigma_M^{-1} (1 / sigma_n^2) K_mn residuals
            rhs = inv_nug * (K_nm.T @ trend_residuals)
            tmp = np.linalg.solve(self.L_sigma_m, rhs)
            self.alpha_inducing = np.linalg.solve(self.L_sigma_m.T, tmp)

        self.is_fitted = True
        return self

    def predict_single(self, prop: PropertyRecord) -> SubModelEstimate:
        if (
            not self.is_fitted
            or self.inducing_coords is None
            or self.alpha_inducing is None
            or self.L_mm is None
            or self.L_sigma_m is None
            or self.feat_mean is None
            or self.feat_std is None
            or self.trend_beta is None
        ):
            raise RuntimeError("SpatialGPValuationModel must be fitted first.")

        x_raw = self.fe.extract_features(prop)
        x_std = (x_raw - self.feat_mean) / self.feat_std
        mean_trend = float(self.trend_intercept + np.dot(x_std, self.trend_beta))

        s_star = np.array([[prop.latitude, prop.longitude]], dtype=np.float64)
        k_star_m = self._matern32_matrix(s_star, self.inducing_coords).ravel()

        gp_mean_offset = float(np.dot(k_star_m, self.alpha_inducing))

        if not self.is_sparse_approximation:
            v_vec = np.linalg.solve(self.L_mm, k_star_m)
            gp_post_var = max(1e-4, (self.signal_variance + self.nugget_variance) - float(np.dot(v_vec, v_vec)))
        else:
            # DTC posterior variance: k(**) - k_{*m} (K_mm^{-1} - Sigma_M^{-1}) k_{m*} + sigma_n^2
            v_mm = np.linalg.solve(self.L_mm, k_star_m)
            v_sig = np.linalg.solve(self.L_sigma_m, k_star_m)
            var_reduction = max(0.0, float(np.dot(v_mm, v_mm) - np.dot(v_sig, v_sig)))
            gp_post_var = max(1e-4, (self.signal_variance + self.nugget_variance) - var_reduction)

        log_pred = mean_trend + gp_mean_offset
        lease_f = compute_leasehold_discount_factor(prop.tenure_type, prop.unexpired_lease_years, discount_rate=self.config.leasehold_discount_rate)
        point_est = float(math.exp(log_pred + 0.5 * gp_post_var) * lease_f)
        std_error_kes = point_est * math.sqrt(max(1e-4, math.exp(gp_post_var) - 1.0))

        spatial_info_gain = 1.0 - (gp_post_var / (self.signal_variance + self.nugget_variance))
        prior_weight = 0.12 + 0.20 * max(0.0, min(1.0, spatial_info_gain))

        return SubModelEstimate(
            approach_name="spatial_gp_kriging",
            point_estimate_kes=point_est,
            std_error_kes=std_error_kes,
            applicable_weight_prior=prior_weight,
            diagnostics={
                "gp_mean_offset_log": round(gp_mean_offset, 4),
                "gp_posterior_std_log": round(math.sqrt(gp_post_var), 4),
                "spatial_information_gain": round(spatial_info_gain, 4),
                "num_inducing_points": int(self.inducing_coords.shape[0]),
                "is_sparse_inducing_gp": self.is_sparse_approximation,
                "global_morans_i": round(self.morans_i, 4),
                "global_morans_z": round(self.morans_z, 2),
            },
        )
