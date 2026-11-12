"""
Module: `machine_learning`
==========================
Implements Gradient Boosted Regression Trees + Bootstrap Random Forest Disagreement Tracker.
Remediation (CRITICAL 1): Strictly excludes rental observations (`RENTAL_LEASE_CONTRACT`,
`RENTAL_ASKING_QUOTE`) before fitting capital value trees.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional
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


@dataclass
class TreeNode:
    feature_idx: int = -1
    threshold: float = 0.0
    left: Optional["TreeNode"] = None
    right: Optional["TreeNode"] = None
    leaf_value: float = 0.0
    is_leaf: bool = True


class SecondOrderDecisionTree:
    def __init__(self, max_depth: int = 4, min_samples_leaf: int = 5, reg_lambda: float = 2.0) -> None:
        self.max_depth = max_depth
        self.min_samples_leaf = min_samples_leaf
        self.reg_lambda = reg_lambda
        self.root: Optional[TreeNode] = None

    def _build(self, X: np.ndarray, g: np.ndarray, h: np.ndarray, depth: int, rng: np.random.Generator) -> TreeNode:
        n, d = X.shape
        G_tot = float(np.sum(g))
        H_tot = float(np.sum(h))
        leaf_val = -G_tot / (H_tot + self.reg_lambda)

        if depth >= self.max_depth or n < 2 * self.min_samples_leaf:
            return TreeNode(leaf_value=leaf_val, is_leaf=True)

        best_gain = 0.0
        best_feat = -1
        best_thresh = 0.0
        best_left_mask: Optional[np.ndarray] = None

        feat_indices = rng.choice(d, size=max(1, int(0.75 * d)), replace=False)
        parent_score = (G_tot ** 2) / (H_tot + self.reg_lambda)

        for f_idx in feat_indices:
            col = X[:, f_idx]
            quantiles = np.unique(np.percentile(col, [15, 30, 45, 60, 75, 85]))
            for thresh in quantiles:
                left_mask = col <= thresh
                n_left = int(np.sum(left_mask))
                n_right = n - n_left
                if n_left < self.min_samples_leaf or n_right < self.min_samples_leaf:
                    continue
                G_L = float(np.sum(g[left_mask]))
                H_L = float(np.sum(h[left_mask]))
                G_R = G_tot - G_L
                H_R = H_tot - H_L
                gain = 0.5 * (
                    (G_L ** 2) / (H_L + self.reg_lambda)
                    + (G_R ** 2) / (H_R + self.reg_lambda)
                    - parent_score
                )
                if gain > best_gain:
                    best_gain = gain
                    best_feat = int(f_idx)
                    best_thresh = float(thresh)
                    best_left_mask = left_mask

        if best_feat == -1 or best_left_mask is None or best_gain < 1e-4:
            return TreeNode(leaf_value=leaf_val, is_leaf=True)

        right_mask = ~best_left_mask
        left_child = self._build(X[best_left_mask], g[best_left_mask], h[best_left_mask], depth + 1, rng)
        right_child = self._build(X[right_mask], g[right_mask], h[right_mask], depth + 1, rng)
        return TreeNode(
            feature_idx=best_feat,
            threshold=best_thresh,
            left=left_child,
            right=right_child,
            leaf_value=leaf_val,
            is_leaf=False,
        )

    def fit(self, X: np.ndarray, g: np.ndarray, h: np.ndarray, rng: np.random.Generator) -> "SecondOrderDecisionTree":
        self.root = self._build(X, g, h, 0, rng)
        return self

    def _predict_row(self, node: TreeNode, x: np.ndarray) -> float:
        curr = node
        while not curr.is_leaf:
            if x[curr.feature_idx] <= curr.threshold:
                curr = curr.left  # type: ignore[assignment]
            else:
                curr = curr.right  # type: ignore[assignment]
        return curr.leaf_value

    def predict(self, X: np.ndarray) -> np.ndarray:
        if self.root is None:
            return np.zeros(X.shape[0], dtype=np.float64)
        return np.array([self._predict_row(self.root, X[i]) for i in range(X.shape[0])], dtype=np.float64)


class GradientBoostedValuationEnsemble:
    def __init__(
        self,
        n_estimators: int = 35,
        learning_rate: float = 0.12,
        max_depth: int = 4,
        random_seed: int = 42,
        config: Optional[ValuationMarketConfig] = None,
    ) -> None:
        self.n_estimators = n_estimators
        self.learning_rate = learning_rate
        self.max_depth = max_depth
        self.random_seed = random_seed
        self.config = config or ValuationMarketConfig()
        self.fe = FeatureEngineeringPipeline(config=self.config)
        self.base_score: float = 0.0
        self.mean_trees: List[SecondOrderDecisionTree] = []
        self.rf_trees: List[SecondOrderDecisionTree] = []
        self.lower_quantile_offset: float = -0.18
        self.upper_quantile_offset: float = 0.18
        self.is_fitted: bool = False

    def fit(self, transactions: List[MarketTransaction]) -> "GradientBoostedValuationEnsemble":
        # CRITICAL 1: Strictly exclude rental observations from capital-value tree training
        sale_txs = [tx for tx in transactions if tx.evidence_type in CAPITAL_SALE_EVIDENCE_TYPES]
        if len(sale_txs) < 8:
            raise ValueError(
                f"GradientBoostedValuationEnsemble requires at least 8 capital sale transactions; got {len(sale_txs)}."
            )

        rng = np.random.default_rng(self.random_seed)
        props = [tx.property_record for tx in sale_txs]
        X = self.fe.transform_batch(props)

        y_log = np.zeros(len(sale_txs), dtype=np.float64)
        weights = np.zeros(len(sale_txs), dtype=np.float64)
        for i, tx in enumerate(sale_txs):
            p = tx.observed_price_kes
            if tx.evidence_type == PriceEvidenceType.ASKING_LISTING_PRICE:
                p *= self.config.asking_price_negotiation_haircut
            y_log[i] = math.log(max(10_000.0, p))
            weights[i] = SALE_EVIDENCE_RELIABILITY_PRIOR[tx.evidence_type] * tx.reliability_weight

        self.base_score = float(np.average(y_log, weights=weights))
        f_pred = np.full_like(y_log, self.base_score)

        self.mean_trees = []
        n = len(sale_txs)
        for _ in range(self.n_estimators):
            g = weights * (f_pred - y_log)
            h = weights.copy()
            sub_idx = rng.choice(n, size=max(6, int(0.85 * n)), replace=False)
            tree = SecondOrderDecisionTree(max_depth=self.max_depth, min_samples_leaf=4, reg_lambda=2.0)
            tree.fit(X[sub_idx], g[sub_idx], h[sub_idx], rng)
            f_pred += self.learning_rate * tree.predict(X)
            self.mean_trees.append(tree)

        self.rf_trees = []
        for _ in range(12):
            boot_idx = rng.choice(n, size=n, replace=True)
            g_rf = weights[boot_idx] * (self.base_score - y_log[boot_idx])
            h_rf = weights[boot_idx].copy()
            rf_tree = SecondOrderDecisionTree(max_depth=self.max_depth + 1, min_samples_leaf=3, reg_lambda=0.5)
            rf_tree.fit(X[boot_idx], g_rf, h_rf, rng)
            self.rf_trees.append(rf_tree)

        resids = y_log - f_pred
        self.lower_quantile_offset = float(np.percentile(resids, 10))
        self.upper_quantile_offset = float(np.percentile(resids, 90))
        self.is_fitted = True
        return self

    def predict_single(self, prop: PropertyRecord) -> SubModelEstimate:
        if not self.is_fitted:
            raise RuntimeError("GradientBoostedValuationEnsemble must be fitted first.")
        X = self.fe.extract_features(prop).reshape(1, -1)
        log_pred = self.base_score + sum(
            self.learning_rate * float(t.predict(X)[0]) for t in self.mean_trees
        )

        rf_preds = np.array([self.base_score + float(t.predict(X)[0]) for t in self.rf_trees])
        epistemic_log_std = float(np.std(rf_preds))
        aleatoric_log_std = 0.5 * (self.upper_quantile_offset - self.lower_quantile_offset) / 1.28155

        total_log_std = math.sqrt(epistemic_log_std ** 2 + aleatoric_log_std ** 2)
        lease_f = compute_leasehold_discount_factor(prop.tenure_type, prop.unexpired_lease_years, discount_rate=self.config.leasehold_discount_rate)
        point_est = float(math.exp(log_pred + 0.5 * (total_log_std ** 2)) * lease_f)
        std_error_kes = point_est * math.sqrt(max(1e-4, math.exp(min(1.0, total_log_std ** 2)) - 1.0))

        return SubModelEstimate(
            approach_name="machine_learning_gbdt",
            point_estimate_kes=point_est,
            std_error_kes=std_error_kes,
            applicable_weight_prior=0.25,
            diagnostics={
                "log_prediction": round(log_pred, 4),
                "epistemic_log_std": round(epistemic_log_std, 4),
                "aleatoric_log_std": round(aleatoric_log_std, 4),
                "n_boosted_trees": len(self.mean_trees),
            },
        )
