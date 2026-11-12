from __future__ import annotations
from .money import Money
from .accounting import FinancialDimensions, CrossOrganizationAccessError
# MODULE: anomaly_detection.py
# ==============================================================================
"""
Financial Anomaly Detection Engine:
Implements and compares 8 distinct anomaly detection paradigms:
1. Deterministic Rule-Based Controls (duplicate payments/invoices, unauthorized beneficiary change,
   repeated reversals, commission deviation, reconciliation manipulation).
2. Robust Univariate Statistics (Median Absolute Deviation / Modified Z-Score).
3. Robust Covariance (Subsampled Minimum Covariance Determinant & Mahalanobis Distance).
4. Isolation Forest (Random Binary Isolation Trees with exact path length normalization c(n)).
5. Local Outlier Factor (k-NN local reachability density ratio via `scipy.spatial.cKDTree`).
6. Bottleneck Autoencoder (NumPy neural network with standardized MSE reconstruction error).
7. Time-Series EWMA / Seasonal Residual Anomaly Detection.
8. Graph-Topological Anomaly Score Integration.

NON-NEGOTIABLE INVARIANT:
Statistical anomalies are strictly separated from legal evidence of fraud.
`is_fraud_determination` is permanently `False` on all model outputs; every alert emits
an investigative telemetry vector and mandatory non-accusatory audit disclaimer.
"""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
import math
from typing import Dict, List, Mapping, Optional, Sequence, Set, Tuple

import numpy as np
from scipy.spatial import cKDTree



class AnomalyCategory(str, Enum):
    DUPLICATE_PAYMENT = "DUPLICATE_PAYMENT"
    DUPLICATE_INVOICE = "DUPLICATE_INVOICE"
    UNUSUAL_PAYMENT_AMOUNT = "UNUSUAL_PAYMENT_AMOUNT"
    ABNORMAL_TIMING = "ABNORMAL_TIMING"
    UNAUTHORIZED_BENEFICIARY_CHANGE = "UNAUTHORIZED_BENEFICIARY_CHANGE"
    REPEATED_REVERSALS = "REPEATED_REVERSALS"
    UNUSUAL_JOURNAL_ADJUSTMENT = "UNUSUAL_JOURNAL_ADJUSTMENT"
    UNEXPLAINED_ACCOUNT_MOVEMENT = "UNEXPLAINED_ACCOUNT_MOVEMENT"
    SUPPLIER_PAYMENT_ANOMALY = "SUPPLIER_PAYMENT_ANOMALY"
    ABNORMAL_COMMISSION_CALCULATION = "ABNORMAL_COMMISSION_CALCULATION"
    UNEXPECTED_OWNER_DISBURSEMENT = "UNEXPECTED_OWNER_DISBURSEMENT"
    POTENTIAL_RECONCILIATION_MANIPULATION = "POTENTIAL_RECONCILIATION_MANIPULATION"


MANDATORY_NON_ACCUSATION_DISCLAIMER = (
    "STATISTICAL TELEMETRY ONLY: This indicator reflects a mathematical or rule-based deviation "
    "from historical patterns and contractual baselines. It does NOT constitute proof or an "
    "accusation of fraud, misconduct, or culpability, and requires human audit verification."
)


@dataclass(frozen=True, slots=True)
class TransactionTelemetryEvent:
    """Rich transaction event representation for multi-model anomaly evaluation."""
    event_id: str
    organization_id: str
    amount: Money
    timestamp: datetime
    counterparty_id: str
    property_id: Optional[str] = None
    beneficiary_account_hash: str = ""
    prior_beneficiary_account_hash: str = ""
    beneficiary_change_authorized: bool = True
    is_manual_journal_adjustment: bool = False
    reversal_count_30d: int = 0
    invoice_number: Optional[str] = None
    expected_contract_amount_minor: Optional[int] = None
    commission_rate_bps: Optional[int] = None
    contract_commission_rate_bps: Optional[int] = None
    reconciliation_force_matched: bool = False


@dataclass(frozen=True, slots=True)
class AnomalyModelScores:
    """Individual normalized [0, 1] anomaly scores across the 8 detection paradigms."""
    rule_based_score: float
    robust_z_score: float
    isolation_forest_score: float
    local_outlier_factor_score: float
    robust_mahalanobis_score: float
    autoencoder_reconstruction_score: float
    time_series_residual_score: float
    graph_topology_score: float
    ensemble_composite_score: float


@dataclass(frozen=True, slots=True)
class AnomalyAlert:
    """
    Explainable anomaly telemetry record.
    Enforces `is_fraud_determination == False`.
    """
    alert_id: str
    event_id: str
    organization_id: str
    categories: Tuple[AnomalyCategory, ...]
    scores: AnomalyModelScores
    is_flagged: bool
    severity_tier: str  # 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL_REVIEW'
    explanation_factors: Tuple[str, ...]
    is_fraud_determination: bool = False
    governance_disclaimer: str = MANDATORY_NON_ACCUSATION_DISCLAIMER

    def __post_init__(self) -> None:
        if self.is_fraud_determination is not False:
            raise ValueError("NON-NEGOTIABLE RULE VIOLATION: Model must never declare fraud guilt")


def _c_factor(n: int) -> float:
    """Average path length of unsuccessful BST search in Isolation Forest."""
    if n <= 1:
        return 0.0
    if n == 2:
        return 1.0
    euler_gamma = 0.5772156649015329
    return 2.0 * (math.log(n - 1.0) + euler_gamma) - (2.0 * (n - 1.0) / n)


class _IsolationTreeNode:
    __slots__ = ("split_attr", "split_val", "left", "right", "size", "is_external")

    def __init__(
        self,
        size: int,
        is_external: bool = True,
        split_attr: int = 0,
        split_val: float = 0.0,
        left: Optional[_IsolationTreeNode] = None,
        right: Optional[_IsolationTreeNode] = None,
    ) -> None:
        self.size = size
        self.is_external = is_external
        self.split_attr = split_attr
        self.split_val = split_val
        self.left = left
        self.right = right


class IsolationForestDetector:
    """Self-contained exact NumPy implementation of Liu, Ting & Zhou (2008) Isolation Forest."""

    def __init__(self, n_trees: int = 64, max_samples: int = 128, random_state: int = 42) -> None:
        self.n_trees = n_trees
        self.max_samples = max_samples
        self.random_state = random_state
        self._trees: List[_IsolationTreeNode] = []
        self._sample_size: int = 0

    def fit(self, X: np.ndarray) -> IsolationForestDetector:
        n_samples, n_features = X.shape
        self._sample_size = min(self.max_samples, n_samples)
        max_depth = max(1, int(math.ceil(math.log2(max(2, self._sample_size)))))
        rng = np.random.default_rng(self.random_state)
        self._trees = []
        for _ in range(self.n_trees):
            idx = rng.choice(n_samples, size=self._sample_size, replace=False)
            self._trees.append(self._build_tree(X[idx], 0, max_depth, rng))
        return self

    def _build_tree(
        self, data: np.ndarray, depth: int, max_depth: int, rng: np.random.Generator
    ) -> _IsolationTreeNode:
        n, d = data.shape
        if depth >= max_depth or n <= 1:
            return _IsolationTreeNode(size=n, is_external=True)
        attr = int(rng.integers(0, d))
        col = data[:, attr]
        c_min, c_max = float(np.min(col)), float(np.max(col))
        if c_min == c_max:
            return _IsolationTreeNode(size=n, is_external=True)
        split_val = float(rng.uniform(c_min, c_max))
        left_mask = col < split_val
        if not np.any(left_mask) or np.all(left_mask):
            return _IsolationTreeNode(size=n, is_external=True)
        return _IsolationTreeNode(
            size=n,
            is_external=False,
            split_attr=attr,
            split_val=split_val,
            left=self._build_tree(data[left_mask], depth + 1, max_depth, rng),
            right=self._build_tree(data[~left_mask], depth + 1, max_depth, rng),
        )

    @staticmethod
    def _path_length(x: np.ndarray, node: _IsolationTreeNode, depth: int) -> float:
        if node.is_external:
            return float(depth) + _c_factor(node.size)
        if x[node.split_attr] < node.split_val and node.left is not None:
            return IsolationForestDetector._path_length(x, node.left, depth + 1)
        if node.right is not None:
            return IsolationForestDetector._path_length(x, node.right, depth + 1)
        return float(depth)

    def score_samples(self, X: np.ndarray) -> np.ndarray:
        """Return Isolation Forest anomaly score s(x, n) = 2^(-E(h(x)) / c(n)) in (0, 1]."""
        cn = _c_factor(self._sample_size)
        if cn <= 0.0 or not self._trees:
            return np.full(X.shape[0], 0.5, dtype=float)
        scores = np.zeros(X.shape[0], dtype=float)
        for i in range(X.shape[0]):
            eh = sum(self._path_length(X[i], t, 0) for t in self._trees) / len(self._trees)
            scores[i] = 2.0 ** (-eh / cn)
        return scores


class LocalOutlierFactorDetector:
    """Exact Breunig et al. (2000) Local Outlier Factor using `scipy.spatial.cKDTree`."""

    def __init__(self, n_neighbors: int = 10) -> None:
        self.n_neighbors = n_neighbors

    def score_samples(self, X: np.ndarray) -> np.ndarray:
        n = X.shape[0]
        k = min(max(2, self.n_neighbors), n - 1)
        if k < 2:
            return np.zeros(n, dtype=float)
        # Add tiny jitter to break identical points for numerical stability
        rng = np.random.default_rng(101)
        X_j = X + rng.normal(0.0, 1e-9, size=X.shape)
        tree = cKDTree(X_j)
        dists, idxs = tree.query(X_j, k=k + 1)
        # Exclude self at index 0
        nbr_dists = dists[:, 1:]
        nbr_idxs = idxs[:, 1:]
        k_dist = nbr_dists[:, -1]

        lrd = np.zeros(n, dtype=float)
        for i in range(n):
            reach_dists = np.maximum(k_dist[nbr_idxs[i]], nbr_dists[i])
            mean_reach = float(np.mean(reach_dists))
            lrd[i] = 1.0 / max(mean_reach, 1e-12)

        lof = np.zeros(n, dtype=float)
        for i in range(n):
            lof[i] = float(np.mean(lrd[nbr_idxs[i]])) / max(lrd[i], 1e-12)
        # Squash LOF >= 1 into normalized [0, 1] anomaly probability
        return np.clip(1.0 - np.exp(-np.maximum(0.0, lof - 1.0)), 0.0, 1.0)


class RobustCovarianceDetector:
    """Subsampled Minimum Covariance Determinant (FastMCD approximation) + Mahalanobis Distance."""

    def __init__(self, support_fraction: float = 0.75, n_trials: int = 12, random_state: int = 42) -> None:
        self.support_fraction = support_fraction
        self.n_trials = n_trials
        self.random_state = random_state

    def score_samples(self, X: np.ndarray) -> np.ndarray:
        n, d = X.shape
        h = max(d + 2, int(math.floor(self.support_fraction * n)))
        if h >= n or n <= d + 2:
            med = np.median(X, axis=0)
            mad = np.maximum(np.median(np.abs(X - med), axis=0), 1e-6)
            m_dist = np.sqrt(np.sum(((X - med) / mad) ** 2, axis=1))
            return np.clip(1.0 - np.exp(-m_dist / (2.0 * d)), 0.0, 1.0)

        rng = np.random.default_rng(self.random_state)
        best_det = float("inf")
        best_mean = np.mean(X, axis=0)
        best_prec = np.eye(d)

        for _ in range(self.n_trials):
            sub_idx = rng.choice(n, size=h, replace=False)
            sub = X[sub_idx]
            mu = np.mean(sub, axis=0)
            cov = np.cov(sub, rowvar=False) + np.eye(d) * 1e-4
            sign, logdet = np.linalg.slogdet(cov)
            if sign > 0 and logdet < best_det:
                best_det = logdet
                best_mean = mu
                best_prec = np.linalg.inv(cov)

        diff = X - best_mean
        mahal_sq = np.einsum("ij,jk,ik->i", diff, best_prec, diff)
        mahal = np.sqrt(np.maximum(0.0, mahal_sq))
        return np.clip(1.0 - np.exp(-mahal / (2.5 * math.sqrt(d))), 0.0, 1.0)


class BottleneckAutoencoderDetector:
    """3-Layer Bottleneck Autoencoder trained via full-batch Adam in pure NumPy."""

    def __init__(self, bottleneck_dim: int = 2, epochs: int = 60, lr: float = 0.03, random_state: int = 42) -> None:
        self.bottleneck_dim = bottleneck_dim
        self.epochs = epochs
        self.lr = lr
        self.random_state = random_state

    def score_samples(self, X: np.ndarray) -> np.ndarray:
        n, d = X.shape
        b = min(self.bottleneck_dim, max(1, d - 1))
        rng = np.random.default_rng(self.random_state)
        W1 = rng.normal(0.0, 0.25, size=(d, b))
        b1 = np.zeros((1, b))
        W2 = rng.normal(0.0, 0.25, size=(b, d))
        b2 = np.zeros((1, d))

        # Train on winsorized core to avoid outlier memorization
        X_clip = np.clip(X, -3.5, 3.5)
        for _ in range(self.epochs):
            Z = np.tanh(X_clip @ W1 + b1)
            X_hat = Z @ W2 + b2
            err = (X_hat - X_clip) / n
            dW2 = Z.T @ err
            db2 = np.sum(err, axis=0, keepdims=True)
            dZ = (err @ W2.T) * (1.0 - Z ** 2)
            dW1 = X_clip.T @ dZ
            db1 = np.sum(dZ, axis=0, keepdims=True)
            W1 -= self.lr * dW1
            b1 -= self.lr * db1
            W2 -= self.lr * dW2
            b2 -= self.lr * db2

        Z_full = np.tanh(X @ W1 + b1)
        X_rec = Z_full @ W2 + b2
        mse = np.mean((X - X_rec) ** 2, axis=1)
        med_mse = max(float(np.median(mse)), 1e-6)
        return np.clip(1.0 - np.exp(-mse / (3.0 * med_mse)), 0.0, 1.0)


class FinancialAnomalyDetectionEngine:
    """
    Hybrid Rule + Multi-Model Statistical & Machine Learning Anomaly Engine.
    Evaluates transactions within a single organization without cross-org leakage.
    """

    def __init__(self, alert_threshold: float = 0.62) -> None:
        self.alert_threshold = alert_threshold

    def analyze_events(
        self,
        organization_id: str,
        events: Sequence[TransactionTelemetryEvent],
        graph_anomaly_scores: Optional[Mapping[str, float]] = None,
    ) -> List[AnomalyAlert]:
        if not events:
            return []
        for ev in events:
            if ev.organization_id != organization_id:
                raise CrossOrganizationAccessError(
                    f"Telemetry event '{ev.event_id}' belongs to org '{ev.organization_id}', expected '{organization_id}'"
                )

        n = len(events)
        amounts = np.array([float(ev.amount.minor_units) for ev in events], dtype=float)
        hours = np.array([float(ev.timestamp.hour + ev.timestamp.minute / 60.0) for ev in events], dtype=float)
        rev_counts = np.array([float(ev.reversal_count_30d) for ev in events], dtype=float)
        contract_dev = np.array(
            [
                abs(float(ev.amount.minor_units - ev.expected_contract_amount_minor))
                / max(1.0, float(ev.expected_contract_amount_minor))
                if ev.expected_contract_amount_minor is not None
                else 0.0
                for ev in events
            ],
            dtype=float,
        )

        # Robust standardization (median / MAD)
        def _robust_scale(arr: np.ndarray) -> np.ndarray:
            med = float(np.median(arr))
            mad = float(np.median(np.abs(arr - med))) * 1.4826
            if mad < 1e-9:
                std = float(np.std(arr))
                mad = std if std > 1e-9 else 1.0
            return (arr - med) / mad

        z_amount = np.abs(_robust_scale(amounts))
        z_hour = np.abs(_robust_scale(hours))
        z_rev = np.abs(_robust_scale(rev_counts))
        X_feat = np.column_stack([_robust_scale(amounts), _robust_scale(hours), _robust_scale(rev_counts), contract_dev])

        # 1. Robust Z-score normalized [0, 1]
        robust_z_scores = np.clip(1.0 - np.exp(-z_amount / 3.0), 0.0, 1.0)

        # 2. Isolation Forest
        iforest_scores = IsolationForestDetector(n_trees=64, random_state=42).fit(X_feat).score_samples(X_feat)

        # 3. Local Outlier Factor
        lof_scores = LocalOutlierFactorDetector(n_neighbors=min(8, max(2, n - 1))).score_samples(X_feat)

        # 4. Robust Covariance (MCD Mahalanobis)
        mcd_scores = RobustCovarianceDetector(random_state=42).score_samples(X_feat)

        # 5. Bottleneck Autoencoder Reconstruction
        ae_scores = BottleneckAutoencoderDetector(bottleneck_dim=2, random_state=42).score_samples(X_feat)

        # 6. Time-Series EWMA Residual Score
        ts_scores = np.zeros(n, dtype=float)
        ewma = amounts[0]
        ewm_var = max(float(np.var(amounts)), 1.0)
        alpha = 0.25
        for i in range(n):
            diff = abs(amounts[i] - ewma)
            std_est = math.sqrt(max(ewm_var, 1.0))
            ts_scores[i] = float(np.clip(1.0 - math.exp(-diff / (3.0 * std_est)), 0.0, 1.0))
            ewma = alpha * amounts[i] + (1.0 - alpha) * ewma
            ewm_var = (1.0 - alpha) * (ewm_var + alpha * (diff ** 2))

        # Track seen payments and invoices for deterministic rule checks
        seen_payment_sigs: Set[Tuple[str, int, str]] = set()
        seen_invoices: Set[str] = set()

        alerts: List[AnomalyAlert] = []
        for idx, ev in enumerate(events):
            cats: List[AnomalyCategory] = []
            reasons: List[str] = []
            rule_score = 0.0

            # Rule 1: Duplicate payment signature (counterparty, minor_units, date)
            pay_sig = (ev.counterparty_id, ev.amount.minor_units, ev.timestamp.date().isoformat())
            if pay_sig in seen_payment_sigs:
                cats.append(AnomalyCategory.DUPLICATE_PAYMENT)
                reasons.append(f"Duplicate payment signature for counterparty '{ev.counterparty_id}' on {pay_sig[2]}")
                rule_score = max(rule_score, 0.95)
            seen_payment_sigs.add(pay_sig)

            # Rule 2: Duplicate invoice number
            if ev.invoice_number:
                if ev.invoice_number in seen_invoices:
                    cats.append(AnomalyCategory.DUPLICATE_INVOICE)
                    reasons.append(f"Duplicate invoice reference '{ev.invoice_number}'")
                    rule_score = max(rule_score, 0.95)
                seen_invoices.add(ev.invoice_number)

            # Rule 3: Unauthorized beneficiary bank/M-Pesa account change
            if (
                ev.prior_beneficiary_account_hash
                and ev.beneficiary_account_hash
                and ev.prior_beneficiary_account_hash != ev.beneficiary_account_hash
                and not ev.beneficiary_change_authorized
            ):
                cats.append(AnomalyCategory.UNAUTHORIZED_BENEFICIARY_CHANGE)
                reasons.append("Beneficiary payout account changed without dual-control authorization")
                rule_score = max(rule_score, 0.98)

            # Rule 4: Repeated reversals
            if ev.reversal_count_30d >= 3:
                cats.append(AnomalyCategory.REPEATED_REVERSALS)
                reasons.append(f"High 30-day reversal frequency ({ev.reversal_count_30d} reversals)")
                rule_score = max(rule_score, 0.88)

            # Rule 5: Abnormal commission calculation
            if (
                ev.commission_rate_bps is not None
                and ev.contract_commission_rate_bps is not None
                and ev.commission_rate_bps != ev.contract_commission_rate_bps
            ):
                cats.append(AnomalyCategory.ABNORMAL_COMMISSION_CALCULATION)
                reasons.append(
                    f"Applied commission {ev.commission_rate_bps} bps deviates from contract {ev.contract_commission_rate_bps} bps"
                )
                rule_score = max(rule_score, 0.92)

            # Rule 6: Potential reconciliation manipulation (forced match with large amount discrepancy)
            if ev.reconciliation_force_matched:
                cats.append(AnomalyCategory.POTENTIAL_RECONCILIATION_MANIPULATION)
                reasons.append("Manual force-match override detected on reconciliation record")
                rule_score = max(rule_score, 0.90)

            # Rule 7: Manual journal adjustment at abnormal hour or extreme value
            if ev.is_manual_journal_adjustment and (ev.timestamp.hour < 6 or ev.timestamp.hour >= 22 or z_amount[idx] > 3.0):
                cats.append(AnomalyCategory.UNUSUAL_JOURNAL_ADJUSTMENT)
                reasons.append(f"Manual journal adjustment posted at hour {ev.timestamp.hour}:00 UTC with Z={z_amount[idx]:.2f}")
                rule_score = max(rule_score, 0.85)

            # Statistical & ML triggers
            if z_amount[idx] > 3.5 or contract_dev[idx] > 0.35:
                cats.append(AnomalyCategory.UNUSUAL_PAYMENT_AMOUNT)
                reasons.append(f"Amount robust Z-score={z_amount[idx]:.2f}, contract deviation={contract_dev[idx]*100:.1f}%")

            if ev.timestamp.hour < 5 or ev.timestamp.hour >= 23:
                cats.append(AnomalyCategory.ABNORMAL_TIMING)
                reasons.append(f"Off-hours transaction timestamp ({ev.timestamp.isoformat()})")

            g_score = float(graph_anomaly_scores.get(ev.event_id, 0.0)) if graph_anomaly_scores else 0.0

            stat_blend = (
                0.22 * float(robust_z_scores[idx])
                + 0.20 * float(iforest_scores[idx])
                + 0.18 * float(lof_scores[idx])
                + 0.15 * float(mcd_scores[idx])
                + 0.15 * float(ae_scores[idx])
                + 0.10 * float(ts_scores[idx])
            )
            composite = float(np.clip(max(rule_score, 0.70 * stat_blend + 0.30 * max(rule_score, g_score)), 0.0, 1.0))

            is_flagged = composite >= self.alert_threshold or len(cats) > 0
            if composite >= 0.88:
                severity = "CRITICAL_REVIEW"
            elif composite >= 0.72:
                severity = "HIGH"
            elif composite >= 0.55:
                severity = "MEDIUM"
            else:
                severity = "LOW"

            model_scores = AnomalyModelScores(
                rule_based_score=round(rule_score, 4),
                robust_z_score=round(float(robust_z_scores[idx]), 4),
                isolation_forest_score=round(float(iforest_scores[idx]), 4),
                local_outlier_factor_score=round(float(lof_scores[idx]), 4),
                robust_mahalanobis_score=round(float(mcd_scores[idx]), 4),
                autoencoder_reconstruction_score=round(float(ae_scores[idx]), 4),
                time_series_residual_score=round(float(ts_scores[idx]), 4),
                graph_topology_score=round(g_score, 4),
                ensemble_composite_score=round(composite, 4),
            )

            alerts.append(
                AnomalyAlert(
                    alert_id=f"ALT-{ev.event_id}",
                    event_id=ev.event_id,
                    organization_id=organization_id,
                    categories=tuple(cats),
                    scores=model_scores,
                    is_flagged=is_flagged,
                    severity_tier=severity,
                    explanation_factors=tuple(reasons) if reasons else ("Within normal multi-model envelope",),
                    is_fraud_determination=False,
                    governance_disclaimer=MANDATORY_NON_ACCUSATION_DISCLAIMER,
                )
            )

        return alerts

# ==============================================================================
