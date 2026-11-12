from __future__ import annotations
from .money import Money
from .reconciliation import ReconciliationEngine, ReconciliationSource, StatementTransaction, MatchDecisionStatus
from .anomaly_detection import FinancialAnomalyDetectionEngine, TransactionTelemetryEvent, AnomalyCategory
from .forecasting import CashFlowForecastingEngine
# MODULE: evaluation.py
# ==============================================================================
"""
Scientific & Accounting Validation Suite, Integration Contracts for GLITTERS CORE,
and Clearly Labelled Synthetic Dataset Generator.
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import math
from typing import Dict, List, Mapping, Sequence, Tuple

import numpy as np
import pandas as pd
from .accounting import (
    DoubleEntryLedger,
    AccountingPeriod,
    PeriodStatus,
    create_standard_real_estate_coa,
)


@dataclass(frozen=True, slots=True)
class ScientificValidationMetrics:
    """Measurable scientific & accounting validation benchmarks (Section 15)."""
    accounting_invariant_pass_rate: float
    hash_chain_integrity_verified: bool
    reconciliation_precision: float
    reconciliation_recall: float
    reconciliation_f1: float
    anomaly_false_positive_rate: float
    anomaly_false_negative_rate: float
    anomaly_f1_score: float
    forecast_mase: float
    forecast_rmse_minor: float
    forecast_90pct_coverage_picp: float
    allocation_determinism_verified: bool
    numerical_precision_zero_drift_verified: bool
    concurrency_correctness_verified: bool
    idempotency_verified: bool


class ValidationEvaluator:
    """Computes classification, calibration, and forecast accuracy metrics."""

    @staticmethod
    def compute_precision_recall_f1(
        predicted_positives: Sequence[bool],
        ground_truth_positives: Sequence[bool],
    ) -> Tuple[float, float, float, float, float]:
        """Returns (precision, recall, f1, false_positive_rate, false_negative_rate)."""
        tp = sum(1 for p, g in zip(predicted_positives, ground_truth_positives) if p and g)
        fp = sum(1 for p, g in zip(predicted_positives, ground_truth_positives) if p and not g)
        fn = sum(1 for p, g in zip(predicted_positives, ground_truth_positives) if not p and g)
        tn = sum(1 for p, g in zip(predicted_positives, ground_truth_positives) if not p and not g)

        precision = tp / (tp + fp) if (tp + fp) > 0 else 1.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 1.0
        f1 = (2.0 * precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0
        fpr = fp / (fp + tn) if (fp + tn) > 0 else 0.0
        fnr = fn / (fn + tp) if (fn + tp) > 0 else 0.0
        return round(precision, 4), round(recall, 4), round(f1, 4), round(fpr, 4), round(fnr, 4)

    @staticmethod
    def evaluate_forecast_calibration(
        actual_minor: Sequence[int],
        training_series_minor: Sequence[int],
        quantiles: Sequence[HorizonQuantiles],
    ) -> Tuple[float, float, float]:
        """
        Compute Mean Absolute Scaled Error (MASE), RMSE, and 90% Prediction Interval Coverage
        Probability (PICP over [P05, P95]).
        """
        y_true = np.asarray(actual_minor, dtype=float)
        y_pred = np.asarray([q.p50_minor for q in quantiles], dtype=float)
        naive_mae = float(np.mean(np.abs(np.diff(np.asarray(training_series_minor, dtype=float)))))
        mae = float(np.mean(np.abs(y_true - y_pred)))
        mase = mae / max(naive_mae, 1.0)
        rmse = float(np.sqrt(np.mean((y_true - y_pred) ** 2)))
        covered = [
            1.0 if (q.p05_minor <= act <= q.p95_minor) else 0.0
            for act, q in zip(actual_minor, quantiles)
        ]
        picp = float(np.mean(covered)) if covered else 1.0
        return round(mase, 4), round(rmse, 2), round(picp, 4)


# ==============================================================================
# INTEGRATION CONTRACTS FOR GLITTERS CORE (ORM-Independent Command/Event DTOs)
# ==============================================================================

@dataclass(frozen=True, slots=True)
class GlittersCorePostingCommandContract:
    """
    Immutable DTO contract sent from GLITTERS CORE application service layer
    to `glitters_financial_intelligence` via Transactional Outbox / RPC.
    Independent of Django ORM models.
    """
    contract_version: str
    command_id: str
    idempotency_key: str
    organization_id: str
    period_id: str
    business_event_type: str  # e.g., 'RENT_INVOICE_ISSUED', 'MPESA_RECEIPT_SETTLED', 'OWNER_DISBURSEMENT'
    entry_date_iso: str
    source_reference: str
    base_currency: str
    lines_payload: Tuple[Mapping[str, object], ...]


POSTGRESQL_16_REFERENCE_DDL = """
-- PostgreSQL 16+ Reference Schema for GLITTERS Financial Intelligence Engine
CREATE TABLE IF NOT EXISTS gfi_accounting_periods (
    period_id VARCHAR(64) PRIMARY KEY,
    organization_id VARCHAR(64) NOT NULL,
    name VARCHAR(128) NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    status VARCHAR(24) NOT NULL CHECK (status IN ('OPEN', 'SOFT_CLOSED', 'LOCKED')),
    CONSTRAINT chk_period_dates CHECK (start_date <= end_date)
);

CREATE TABLE IF NOT EXISTS gfi_journal_entries (
    entry_id VARCHAR(64) PRIMARY KEY,
    organization_id VARCHAR(64) NOT NULL,
    period_id VARCHAR(64) NOT NULL REFERENCES gfi_accounting_periods(period_id),
    entry_date DATE NOT NULL,
    posted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    kind VARCHAR(24) NOT NULL CHECK (kind IN ('STANDARD', 'ACCRUAL', 'ADJUSTMENT', 'REVERSAL', 'CLOSING')),
    status VARCHAR(24) NOT NULL CHECK (status IN ('POSTED', 'REVERSED')),
    source_reference VARCHAR(128) NOT NULL,
    description TEXT NOT NULL,
    idempotency_key VARCHAR(128) NOT NULL UNIQUE,
    reverses_entry_id VARCHAR(64) REFERENCES gfi_journal_entries(entry_id),
    previous_hash CHAR(64) NOT NULL,
    entry_hash CHAR(64) NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS gfi_journal_lines (
    line_id UUID PRIMARY KEY,
    entry_id VARCHAR(64) NOT NULL REFERENCES gfi_journal_entries(entry_id) ON DELETE RESTRICT,
    organization_id VARCHAR(64) NOT NULL,
    account_code VARCHAR(32) NOT NULL,
    tx_currency CHAR(3) NOT NULL,
    debit_minor BIGINT NOT NULL CHECK (debit_minor >= 0),
    credit_minor BIGINT NOT NULL CHECK (credit_minor >= 0),
    functional_debit_minor BIGINT NOT NULL CHECK (functional_debit_minor >= 0),
    functional_credit_minor BIGINT NOT NULL CHECK (functional_credit_minor >= 0),
    property_id VARCHAR(64),
    tenant_id VARCHAR(64),
    owner_id VARCHAR(64),
    supplier_id VARCHAR(64),
    CONSTRAINT chk_exclusive_tx_side CHECK ((debit_minor > 0 AND credit_minor = 0) OR (debit_minor = 0 AND credit_minor > 0)),
    CONSTRAINT chk_exclusive_func_side CHECK ((functional_debit_minor > 0 AND functional_credit_minor = 0) OR (functional_debit_minor = 0 AND functional_credit_minor > 0))
);
"""


# ==============================================================================
# CLEARLY LABELLED SYNTHETIC DATASET GENERATOR
# ==============================================================================

def generate_synthetic_kenyan_real_estate_dataset(seed: int = 20261112) -> pd.DataFrame:
    """
    Generates a clearly labelled synthetic dataset (`is_synthetic=True`,
    `dataset_label='SYNTHETIC_GLITTERS_KENYA_MULTIORG_V1'`) representing multi-organization
    Kenyan real estate operations (Nairobi & Mombasa properties, M-Pesa Paybill receipts,
    bank transfers, maintenance invoices, agency commissions, and labelled anomaly injections).
    """
    rng = np.random.default_rng(seed)
    rows: List[Dict[str, object]] = []
    orgs = ["ORG-KE-NAIROBI-01", "ORG-KE-MOMBASA-02"]
    properties = {
        "ORG-KE-NAIROBI-01": ["PROP-WESTLANDS-TOWER", "PROP-KILIMANI-COURTS", "PROP-KAREN-VILLAS"],
        "ORG-KE-MOMBASA-02": ["PROP-NYALI-RESIDENCES", "PROP-DIANI-HOSPITALITY"],
    }
    channels = ["MPESA_PAYBILL", "BANK_EFT_PESALINK", "CARD_GATEWAY"]
    tx_types = [
        "RENTAL_INCOME",
        "SECURITY_DEPOSIT",
        "SERVICE_CHARGE",
        "MAINTENANCE_EXPENSE",
        "OWNER_DISBURSEMENT",
        "MANAGEMENT_COMMISSION",
        "CONSTRUCTION_DRAWDOWN",
        "HOSPITALITY_BOOKING",
    ]

    base_date = date(2026, 1, 1)
    for i in range(1, 241):
        org_id = orgs[0] if i <= 160 else orgs[1]
        prop_id = str(rng.choice(properties[org_id]))
        channel = str(rng.choice(channels, p=[0.65, 0.25, 0.10]))
        tx_type = str(rng.choice(tx_types, p=[0.42, 0.08, 0.12, 0.12, 0.10, 0.06, 0.05, 0.05]))
        day_offset = int(rng.integers(0, 300))
        tx_date = base_date + timedelta(days=day_offset)

        # Typical minor unit amounts in KES cents (e.g. KES 45,000.00 = 4,500,000 minor units)
        base_kes_major = int(rng.integers(15000, 180000))
        amount_minor = base_kes_major * 100
        is_injected_anomaly = False
        anomaly_reason = "NONE"

        if i in (25, 90, 145, 210):
            # Inject extreme amount statistical anomaly
            amount_minor = base_kes_major * 100 * 14
            is_injected_anomaly = True
            anomaly_reason = "SYNTHETIC_EXTREME_AMOUNT_SPIKE"
        elif i in (50, 185):
            is_injected_anomaly = True
            anomaly_reason = "SYNTHETIC_UNAUTHORIZED_BENEFICIARY_CHANGE"

        mpesa_code = f"SK{i:08d}" if channel == "MPESA_PAYBILL" else f"EFT{i:07d}"
        rows.append(
            {
                "dataset_label": "SYNTHETIC_GLITTERS_KENYA_MULTIORG_V1",
                "is_synthetic": True,
                "record_id": f"SYN-TX-{i:04d}",
                "organization_id": org_id,
                "property_id": prop_id,
                "transaction_date": tx_date.isoformat(),
                "transaction_type": tx_type,
                "payment_channel": channel,
                "external_reference": mpesa_code,
                "currency_code": "KES",
                "amount_minor_units": amount_minor,
                "amount_major_kes": f"{amount_minor / 100:.2f}",
                "is_injected_anomaly": is_injected_anomaly,
                "injected_anomaly_type": anomaly_reason,
            }
        )

    return pd.DataFrame(rows)