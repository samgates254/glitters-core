"""
Module: `hedonic_models`
========================
Implements Regularized Semi-Log Hedonic Regression with Duan's Smearing and
Empirical Bayes Locality Random Intercepts.
Remediation (CRITICAL 1): Strictly filters out any non-sale observations (`RENTAL_LEASE_CONTRACT`,
`RENTAL_ASKING_QUOTE`) before training so rental cash flows never contaminate capital sale models.
"""
from __future__ import annotations
from typing import List, Optional, Dict
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
from ..feature_engineering import FeatureEngineeringPipeline, compute_leasehold_discount_factor
from ..comparable_analysis import SALE_EVIDENCE_RELIABILITY_PRIOR


class HedonicValuationModel:
    def __init__(
        self,
        l2_lambda: float = 1.5,
        l1_ratio: float = 0.15,
        config: Optional[ValuationMarketConfig] = None,
    ) -> None:
        self.l2_lambda = l2_lambda
        self.l1_ratio = l1_ratio
        self.config = config or ValuationMarketConfig()
        self.fe = FeatureEngineeringPipeline(config=self.config)
        self.beta: Optional[np.ndarray] = None
        self.intercept: float = 0.0
        self.feature_means: Optional[np.ndarray] = None
        self.feature_stds: Optional[np.ndarray] = None
        self.locality_offsets: Dict[str, float] = {}
        self.duan_smearing_factor: float = 1.0
        self.residual_sigma: float = 0.18
        self.xtx_inv: Optional[np.ndarray] = None
        self.is_fitted: bool = False

    def fit(self, transactions: List[MarketTransaction]) -> "HedonicValuationModel":
        # CRITICAL 1: Strictly restrict training to capital sale observations
        sale_txs = [tx for tx in transactions if tx.evidence_type in CAPITAL_SALE_EVIDENCE_TYPES]
        if len(sale_txs) < 5:
            raise ValueError(
                f"At least 5 capital sale transactions required to fit HedonicValuationModel; got {len(sale_txs)} "
                f"(out of {len(transactions)} total input records; rental observations are excluded)."
            )

        props = [tx.property_record for tx in sale_txs]
        X_raw = self.fe.transform_batch(props)

        y_log = np.zeros(len(sale_txs), dtype=np.float64)
        sample_weights = np.zeros(len(sale_txs), dtype=np.float64)

        for i, tx in enumerate(sale_txs):
            p = tx.observed_price_kes
            if tx.evidence_type == PriceEvidenceType.ASKING_LISTING_PRICE:
                p *= self.config.asking_price_negotiation_haircut
            y_log[i] = math.log(max(10_000.0, p))
            sample_weights[i] = SALE_EVIDENCE_RELIABILITY_PRIOR[tx.evidence_type] * tx.reliability_weight

        self.feature_means = np.mean(X_raw, axis=0)
        self.feature_stds = np.std(X_raw, axis=0)
        self.feature_stds = np.where(self.feature_stds < 1e-6, 1.0, self.feature_stds)
        X_std = (X_raw - self.feature_means) / self.feature_stds

        W = np.diag(sample_weights / np.mean(sample_weights))
        n, d = X_std.shape
        X_aug = np.column_stack([np.ones(n), X_std])
        reg_matrix = np.eye(d + 1) * self.l2_lambda
        reg_matrix[0, 0] = 1e-6

        xtwx = X_aug.T @ W @ X_aug + reg_matrix
        xtwy = X_aug.T @ W @ y_log
        theta = np.linalg.solve(xtwx, xtwy)

        l1_thresh = self.l2_lambda * self.l1_ratio / max(1.0, float(n))
        beta_raw = theta[1:]
        theta[1:] = np.sign(beta_raw) * np.maximum(0.0, np.abs(beta_raw) - l1_thresh)

        self.intercept = float(theta[0])
        self.beta = theta[1:]
        self.xtx_inv = np.linalg.pinv(xtwx)

        base_preds = self.intercept + X_std @ self.beta
        raw_resids = y_log - base_preds
        self.locality_offsets = {}
        by_loc: Dict[str, List[float]] = {}
        for tx, r in zip(sale_txs, raw_resids):
            by_loc.setdefault(tx.property_record.locality, []).append(float(r))

        tau_sq = 0.04
        sigma_sq = float(np.var(raw_resids)) + 1e-4
        for loc, r_list in by_loc.items():
            n_loc = len(r_list)
            shrinkage = (n_loc * tau_sq) / (n_loc * tau_sq + sigma_sq)
            self.locality_offsets[loc] = float(shrinkage * np.mean(r_list))

        final_preds = np.array([
            base_preds[i] + self.locality_offsets.get(sale_txs[i].property_record.locality, 0.0)
            for i in range(n)
        ])
        final_resids = y_log - final_preds
        self.residual_sigma = float(np.sqrt(np.average(final_resids ** 2, weights=sample_weights)))

        clipped_resids = np.clip(final_resids, -1.2, 1.2)
        self.duan_smearing_factor = float(np.average(np.exp(clipped_resids), weights=sample_weights))
        self.is_fitted = True
        return self

    def predict_single(self, prop: PropertyRecord) -> SubModelEstimate:
        if not self.is_fitted or self.beta is None or self.feature_means is None or self.feature_stds is None:
            raise RuntimeError("HedonicValuationModel must be fitted before calling predict_single.")

        x_raw = self.fe.extract_features(prop)
        x_std = (x_raw - self.feature_means) / self.feature_stds
        loc_offset = self.locality_offsets.get(prop.locality, 0.0)
        log_pred = float(self.intercept + np.dot(x_std, self.beta) + loc_offset)

        lease_f = compute_leasehold_discount_factor(prop.tenure_type, prop.unexpired_lease_years, discount_rate=self.config.leasehold_discount_rate)
        point_est = float(math.exp(log_pred) * self.duan_smearing_factor * lease_f)

        x_aug = np.concatenate([[1.0], x_std])
        leverage_var = float(x_aug.T @ self.xtx_inv @ x_aug) * (self.residual_sigma ** 2) if self.xtx_inv is not None else 0.01
        unseen_loc_var = 0.0 if prop.locality in self.locality_offsets else 0.025
        total_log_se = math.sqrt(self.residual_sigma ** 2 + leverage_var + unseen_loc_var)

        std_error_kes = point_est * math.sqrt(max(1e-4, math.exp(min(1.0, total_log_se ** 2)) - 1.0))

        return SubModelEstimate(
            approach_name="hedonic_pricing",
            point_estimate_kes=point_est,
            std_error_kes=std_error_kes,
            applicable_weight_prior=0.22,
            diagnostics={
                "log_prediction": round(log_pred, 4),
                "locality_offset": round(loc_offset, 4),
                "duan_smearing_factor": round(self.duan_smearing_factor, 4),
                "total_log_se": round(total_log_se, 4),
            },
        )

    def feature_contributions_kes(self, prop: PropertyRecord) -> Dict[str, float]:
        if not self.is_fitted or self.beta is None or self.feature_means is None or self.feature_stds is None:
            return {}
        x_raw = self.fe.extract_features(prop)
        x_std = (x_raw - self.feature_means) / self.feature_stds
        base_val = math.exp(self.intercept) * self.duan_smearing_factor
        pred_est = self.predict_single(prop).point_estimate_kes
        total_diff = pred_est - base_val

        log_contribs = x_std * self.beta
        loc_offset = self.locality_offsets.get(prop.locality, 0.0)
        denom = float(np.sum(np.abs(log_contribs)) + abs(loc_offset) + 1e-9)
        scale = abs(total_diff) / denom if denom > 0 else 0.0

        attributions: Dict[str, float] = {}
        for name, lc in zip(self.fe.FEATURE_NAMES, log_contribs):
            attributions[name] = float(lc * scale)
        attributions["locality_spatial_premium"] = float(loc_offset * scale)
        return attributions
