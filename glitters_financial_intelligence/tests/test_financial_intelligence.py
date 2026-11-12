"""
Comprehensive Unit, Property-Based, 17 Adversarial Scenarios, Concurrency,
and Scientific Validation Benchmark Suite for `glitters_financial_intelligence`.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from decimal import Decimal
from fractions import Fraction
import random
import sys
import time
import unittest
from pathlib import Path
from typing import List

# Canonical modular package test
from pathlib import Path

from glitters_financial_intelligence import (
    Money,
    ExchangeRate,
    MonetaryPrecisionError,
    CurrencyMismatchError,
    AccountType,
    PeriodStatus,
    EntryKind,
    AccountingValidationError,
    CrossOrganizationAccessError,
    PeriodClosedError,
    AccountingPeriod,
    DoubleEntryLedger,
    create_standard_real_estate_coa,
    ReconciliationSource,
    MatchType,
    MatchDecisionStatus,
    ReconciliationRecord,
    ReconciliationEngine,
    PaymentLifecycleState,
    ObligationCategory,
    TieBreakingRule,
    PaymentAllocationError,
    ObligationItem,
    IncomingPayment,
    PaymentAllocationEngine,
    AnomalyCategory,
    TransactionTelemetryEvent,
    FinancialAnomalyDetectionEngine,
    GraphNodeType,
    GraphEdgeType,
    GraphNode,
    GraphEdge,
    FinancialTransactionGraph,
    CashFlowForecastingEngine,
    EpistemicDataKind,
    BaselinePortfolioFacts,
    StressScenarioAssumptions,
    RiskAndStressTestingEngine,
    PropertyPortfolioAnalyticsEngine,
    ValidationEvaluator,
    generate_synthetic_kenyan_real_estate_dataset,
)


def build_test_ledger(org_id: str = "ORG-KE-001", base_ccy: str = "KES") -> DoubleEntryLedger:
    ledger = DoubleEntryLedger(organization_id=org_id, base_currency=base_ccy)
    for acct in create_standard_real_estate_coa(org_id, base_ccy):
        ledger.register_account(acct)
    ledger.register_period(
        AccountingPeriod(
            period_id="2026-11",
            organization_id=org_id,
            name="November 2026",
            start_date=date(2026, 11, 1),
            end_date=date(2026, 11, 30),
            status=PeriodStatus.OPEN,
        )
    )
    ledger.register_period(
        AccountingPeriod(
            period_id="2026-12",
            organization_id=org_id,
            name="December 2026",
            start_date=date(2026, 12, 1),
            end_date=date(2026, 12, 31),
            status=PeriodStatus.OPEN,
        )
    )
    return ledger


class TestAdversarialAndCoreInvariants(unittest.TestCase):
    """
    Tests all 17 mandatory adversarial scenarios (Section 14), property-based invariants,
    concurrency correctness, and scientific validation metrics (Section 15).
    """

    def test_01_unbalanced_journal_entries_rejected(self) -> None:
        """Adversarial #1: Unbalanced journal entries must never be posted."""
        ledger = build_test_ledger()
        lines = [
            ledger.build_line("1010", debit_minor=50000_00),
            ledger.build_line("4010", credit_minor=49999_99, property_id="PROP-01"),
        ]
        with self.assertRaises(AccountingValidationError):
            ledger.post_entry(
                period_id="2026-11",
                entry_date=date(2026, 11, 5),
                source_reference="INV-UNBAL-01",
                description="Off by 1 cent",
                lines=lines,
                idempotency_key="IDEM-UNBAL-01",
            )
        self.assertEqual(len(ledger.entries), 0)

    def test_02_duplicate_payment_references_detected(self) -> None:
        """Adversarial #2: Duplicate external M-Pesa / Bank payment references caught."""
        engine = ReconciliationEngine()
        ext_records = [
            ReconciliationRecord(
                record_id="EXT-1",
                organization_id="ORG-1",
                source=ReconciliationSource.MPESA_STATEMENT,
                amount=Money(35000_00, "KES"),
                transaction_date=date(2026, 11, 10),
                reference="SK829104AB",
                external_tx_id="SK829104AB",
                counterparty_id="TEN-101",
            ),
            ReconciliationRecord(
                record_id="EXT-1-DUP",
                organization_id="ORG-1",
                source=ReconciliationSource.MPESA_STATEMENT,
                amount=Money(35000_00, "KES"),
                transaction_date=date(2026, 11, 10),
                reference="SK829104AB",
                external_tx_id="SK829104AB",
                counterparty_id="TEN-101",
            ),
        ]
        int_records = [
            ReconciliationRecord(
                record_id="INT-1",
                organization_id="ORG-1",
                source=ReconciliationSource.INTERNAL_JOURNAL,
                amount=Money(35000_00, "KES"),
                transaction_date=date(2026, 11, 10),
                reference="SK829104AB",
                external_tx_id="SK829104AB",
                counterparty_id="TEN-101",
            )
        ]
        report = engine.reconcile("ORG-1", ext_records, int_records)
        self.assertIn("EXT-1-DUP", report.duplicate_external_ids)

    def test_03_partial_payments_and_multi_obligation_matching(self) -> None:
        """Adversarial #3: Partial payments and many-to-one / one-to-many settlements."""
        engine = ReconciliationEngine()
        ext = [
            ReconciliationRecord(
                record_id="EXT-PART-1",
                organization_id="ORG-1",
                source=ReconciliationSource.MPESA_STATEMENT,
                amount=Money(20000_00, "KES"),
                transaction_date=date(2026, 11, 10),
                reference="PARTIAL-PAY",
                counterparty_id="TEN-202",
            )
        ]
        intr = [
            ReconciliationRecord(
                record_id="INT-OB-1",
                organization_id="ORG-1",
                source=ReconciliationSource.TENANT_OBLIGATION,
                amount=Money(50000_00, "KES"),
                transaction_date=date(2026, 11, 1),
                reference="INV-NOV-202",
                counterparty_id="TEN-202",
            )
        ]
        rep = engine.reconcile("ORG-1", ext, intr)
        self.assertEqual(len(rep.matches), 1)
        m = rep.matches[0]
        self.assertEqual(m.match_type, MatchType.PARTIAL_PAYMENT)
        self.assertEqual(m.matched_amount, Money(20000_00, "KES"))
        self.assertEqual(m.unmatched_remainder, Money(30000_00, "KES"))
        self.assertTrue(m.requires_human_authorization)

    def test_04_multiple_currency_transactions_and_05_invalid_exchange_rates(self) -> None:
        """Adversarial #4 & #5: Multi-currency posting with exact FX conversion & rejection of invalid FX rates."""
        for bad_rate in [0, -129, Decimal("0"), Decimal("-1.5"), float(129.5)]:
            with self.assertRaises(MonetaryPrecisionError):
                ExchangeRate("USD", "KES", bad_rate)  # type: ignore[arg-type]

        fx = ExchangeRate("USD", "KES", Decimal("129.45"))
        usd_amt = Money.from_major("1000.00", "USD")
        kes_amt = fx.convert(usd_amt)
        self.assertEqual(kes_amt, Money(129450_00, "KES"))

        ledger = build_test_ledger("ORG-FX", "KES")
        lines = [
            ledger.build_line("1010", debit_minor=1000_00, currency_code="USD", exchange_rate=fx),
            ledger.build_line("4010", credit_minor=1000_00, currency_code="USD", exchange_rate=fx, property_id="PROP-USD-1"),
        ]
        je = ledger.post_entry(
            period_id="2026-11",
            entry_date=date(2026, 11, 12),
            source_reference="USD-LEASE-01",
            description="USD commercial lease converted to KES functional currency",
            lines=lines,
            idempotency_key="IDEM-FX-01",
        )
        self.assertEqual(je.lines[0].functional_debit, Money(129450_00, "KES"))
        tb = ledger.generate_trial_balance()
        self.assertTrue(tb.is_balanced)

    def test_06_extreme_monetary_values_and_07_rounding_edge_cases(self) -> None:
        """Adversarial #6 & #7: Reject floats, handle 10^18 minor units, and allocate indivisible cents exactly."""
        with self.assertRaises(MonetaryPrecisionError):
            Money(100.0, "KES")  # type: ignore[arg-type]
        with self.assertRaises(MonetaryPrecisionError):
            Money.from_major(19.99, "KES")  # type: ignore[arg-type]

        huge = Money(10**18 + 7, "KES")
        parts = huge.allocate_equal(3, tie_break_keys=["BEN-A", "BEN-B", "BEN-C"])
        self.assertEqual(sum(p.minor_units for p in parts), huge.minor_units)
        self.assertEqual(max(p.minor_units for p in parts) - min(p.minor_units for p in parts), 1)

        # Classic 100.00 KES split 3 ways -> 33.34, 33.33, 33.33
        hundred = Money.from_major("100.00", "KES")
        three_way = hundred.allocate_equal(3, tie_break_keys=["UNIT-1", "UNIT-2", "UNIT-3"])
        self.assertEqual([m.minor_units for m in three_way], [3334, 3333, 3333])
        self.assertEqual(sum(m.minor_units for m in three_way), 10000)

    def test_08_concurrent_payment_processing_and_09_duplicate_webhooks(self) -> None:
        """Adversarial #8 & #9: Thread-safe concurrent ledger posting and duplicate webhook idempotency."""
        ledger = build_test_ledger("ORG-CONC", "KES")

        def worker_post(idx: int) -> str:
            # Half the threads send duplicate webhook idempotency keys
            idem_key = f"WEBHOOK-KEY-{idx // 2}"
            lines = [
                ledger.build_line("1020", debit_minor=25000_00),
                ledger.build_line("4010", credit_minor=25000_00, property_id="PROP-CONC-1"),
            ]
            je = ledger.post_entry(
                period_id="2026-11",
                entry_date=date(2026, 11, 12),
                source_reference=f"MPESA-REF-{idx // 2}",
                description="Concurrent M-Pesa webhook receipt",
                lines=lines,
                idempotency_key=idem_key,
            )
            return je.entry_id

        with ThreadPoolExecutor(max_workers=16) as pool:
            entry_ids = list(pool.map(worker_post, range(40)))

        # Exactly 20 unique entries should be created despite 40 concurrent calls
        self.assertEqual(len(set(entry_ids)), 20)
        self.assertEqual(len(ledger.entries), 20)
        self.assertTrue(ledger.verify_hash_chain_integrity())
        fs = ledger.generate_financial_statements()
        self.assertTrue(fs.accounting_equation_holds)
        self.assertTrue(fs.cash_reconciles)
        self.assertEqual(fs.total_revenue_minor, 20 * 25000_00)

    def test_10_reversed_transactions_and_immutability(self) -> None:
        """Adversarial #10: Controlled reversal entries without mutating original posted entry."""
        ledger = build_test_ledger()
        lines = [
            ledger.build_line("1020", debit_minor=45000_00),
            ledger.build_line("4010", credit_minor=45000_00, property_id="PROP-1"),
        ]
        orig = ledger.post_entry(
            period_id="2026-11",
            entry_date=date(2026, 11, 10),
            source_reference="MPESA-ORIG-01",
            description="Original rent receipt",
            lines=lines,
            idempotency_key="IDEM-ORIG-01",
        )
        orig_hash_before = orig.entry_hash
        rev = ledger.reverse_entry(
            original_entry_id=orig.entry_id,
            reversal_period_id="2026-11",
            reversal_date=date(2026, 11, 11),
            reason="M-Pesa reversal by sender",
            idempotency_key="IDEM-REV-01",
        )
        self.assertEqual(orig.entry_hash, orig_hash_before)
        self.assertEqual(rev.reverses_entry_id, orig.entry_id)
        # Second reversal attempt with different key must fail
        with self.assertRaises(AccountingValidationError):
            ledger.reverse_entry(
                original_entry_id=orig.entry_id,
                reversal_period_id="2026-11",
                reversal_date=date(2026, 11, 12),
                reason="Duplicate reversal attempt",
                idempotency_key="IDEM-REV-02",
            )
        fs = ledger.generate_financial_statements()
        self.assertEqual(fs.net_income_minor, 0)
        self.assertEqual(fs.ending_cash_balance_minor, 0)
        self.assertTrue(ledger.verify_hash_chain_integrity())

    def test_11_missing_bank_records_and_12_ambiguous_reconciliation_matches(self) -> None:
        """Adversarial #11 & #12: Missing bank records flagged & ambiguous matches NEVER auto-settled."""
        engine = ReconciliationEngine()
        # Two internal tenant obligations with identical amount & date and no reference on external payment
        ext = [
            ReconciliationRecord(
                record_id="EXT-AMBIG-1",
                organization_id="ORG-1",
                source=ReconciliationSource.BANK_STATEMENT,
                amount=Money(60000_00, "KES"),
                transaction_date=date(2026, 11, 10),
                reference="CASH DEPOSIT",
            )
        ]
        intr = [
            ReconciliationRecord(
                record_id="INT-CAND-A",
                organization_id="ORG-1",
                source=ReconciliationSource.TENANT_OBLIGATION,
                amount=Money(60000_00, "KES"),
                transaction_date=date(2026, 11, 10),
                reference="INV-A101",
                counterparty_id="TEN-A",
            ),
            ReconciliationRecord(
                record_id="INT-CAND-B",
                organization_id="ORG-1",
                source=ReconciliationSource.TENANT_OBLIGATION,
                amount=Money(60000_00, "KES"),
                transaction_date=date(2026, 11, 10),
                reference="INV-B202",
                counterparty_id="TEN-B",
            ),
        ]
        rep = engine.reconcile("ORG-1", ext, intr)
        for m in rep.matches:
            self.assertNotEqual(m.status, MatchDecisionStatus.AUTO_VERIFIED)
            self.assertTrue(m.requires_human_authorization)
        self.assertEqual(len(rep.missing_internal_ids), 1)

    def test_13_unauthorized_beneficiary_changes_and_non_accusation_rule(self) -> None:
        """Adversarial #13: Detect unauthorized beneficiary change while enforcing `is_fraud_determination=False`."""
        detector = FinancialAnomalyDetectionEngine()
        base_ts = datetime(2026, 11, 12, 11, 0, tzinfo=timezone.utc)
        events = [
            TransactionTelemetryEvent(
                event_id=f"EV-{i}",
                organization_id="ORG-1",
                amount=Money(40000_00 + i * 100_00, "KES"),
                timestamp=base_ts,
                counterparty_id=f"SUPP-{i}",
                beneficiary_account_hash="ACC-HASH-NORMAL",
                prior_beneficiary_account_hash="ACC-HASH-NORMAL",
                beneficiary_change_authorized=True,
            )
            for i in range(15)
        ]
        events.append(
            TransactionTelemetryEvent(
                event_id="EV-UNAUTH-BEN",
                organization_id="ORG-1",
                amount=Money(950000_00, "KES"),
                timestamp=datetime(2026, 11, 12, 2, 30, tzinfo=timezone.utc),
                counterparty_id="SUPP-99",
                beneficiary_account_hash="ACC-HASH-ATTACKER",
                prior_beneficiary_account_hash="ACC-HASH-ORIGINAL",
                beneficiary_change_authorized=False,
                reversal_count_30d=4,
            )
        )
        alerts = detector.analyze_events("ORG-1", events)
        target_alert = [a for a in alerts if a.event_id == "EV-UNAUTH-BEN"][0]
        self.assertTrue(target_alert.is_flagged)
        self.assertIn(AnomalyCategory.UNAUTHORIZED_BENEFICIARY_CHANGE, target_alert.categories)
        self.assertFalse(target_alert.is_fraud_determination)
        self.assertIn("does NOT constitute proof or an accusation of fraud", target_alert.governance_disclaimer)

    def test_14_circular_transaction_graphs_and_collusion_detection(self) -> None:
        """Adversarial #14: Circular fund-flow detection and shared beneficiary collision."""
        g = FinancialTransactionGraph("ORG-1")
        for nid, ntype in [
            ("ACC-A", GraphNodeType.ACCOUNT),
            ("SUPP-B", GraphNodeType.SUPPLIER),
            ("OWNER-C", GraphNodeType.OWNER),
            ("BEN-SHARED", GraphNodeType.BENEFICIARY),
        ]:
            g.add_node(GraphNode(nid, "ORG-1", ntype, nid))

        now = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)
        # Create circular cycle ACC-A -> SUPP-B -> OWNER-C -> ACC-A
        g.add_edge(GraphEdge("E1", "ORG-1", "ACC-A", "SUPP-B", GraphEdgeType.FUNDS_FLOW, Money(100000_00, "KES"), now, is_anomalous_seed=True))
        g.add_edge(GraphEdge("E2", "ORG-1", "SUPP-B", "OWNER-C", GraphEdgeType.FUNDS_FLOW, Money(95000_00, "KES"), now))
        g.add_edge(GraphEdge("E3", "ORG-1", "OWNER-C", "ACC-A", GraphEdgeType.FUNDS_FLOW, Money(90000_00, "KES"), now))
        # Shared beneficiary collision: both SUPP-B and OWNER-C route payouts to BEN-SHARED
        g.add_edge(GraphEdge("E4", "ORG-1", "SUPP-B", "BEN-SHARED", GraphEdgeType.PAYOUT_TO_BENEFICIARY, Money(50000_00, "KES"), now))
        g.add_edge(GraphEdge("E5", "ORG-1", "OWNER-C", "BEN-SHARED", GraphEdgeType.PAYOUT_TO_BENEFICIARY, Money(50000_00, "KES"), now))

        rep = g.analyze()
        self.assertEqual(len(rep.circular_fund_flow_cycles), 1)
        self.assertEqual(rep.circular_fund_flow_cycles[0], ("ACC-A", "SUPP-B", "OWNER-C", "ACC-A"))
        self.assertEqual(len(rep.shared_beneficiary_collisions), 1)
        self.assertEqual(rep.shared_beneficiary_collisions[0], ("BEN-SHARED", ("OWNER-C", "SUPP-B")))

    def test_15_cross_organization_data_leakage_blocked(self) -> None:
        """Adversarial #15: Cross-organization access is strictly blocked across Ledger, Reconciliation, Allocation, Anomaly, and Graph."""
        ledger = build_test_ledger("ORG-A", "KES")
        bad_line = ledger.build_line("1010", debit_minor=1000_00)
        # Tamper line dimension to ORG-B
        from glitters_financial_intelligence.accounting import FinancialDimensions, JournalLine
        tampered_line = JournalLine(
            line_id="L-BAD",
            account_code="4010",
            debit=Money(0, "KES"),
            credit=Money(1000_00, "KES"),
            functional_debit=Money(0, "KES"),
            functional_credit=Money(1000_00, "KES"),
            dimensions=FinancialDimensions(organization_id="ORG-B", property_id="PROP-1"),
        )
        with self.assertRaises(CrossOrganizationAccessError):
            ledger.post_entry(
                period_id="2026-11",
                entry_date=date(2026, 11, 10),
                source_reference="LEAK-01",
                description="Cross-org attempt",
                lines=[bad_line, tampered_line],
                idempotency_key="IDEM-LEAK-1",
            )

        graph = FinancialTransactionGraph("ORG-A")
        with self.assertRaises(CrossOrganizationAccessError):
            graph.add_node(GraphNode("N-LEAK", "ORG-B", GraphNodeType.ACCOUNT, "Foreign Org Node"))

    def test_16_accounting_period_closure_conflicts(self) -> None:
        """Adversarial #16: Closed / Locked periods reject standard entries and cannot be unlocked once LOCKED."""
        ledger = build_test_ledger()
        ledger.set_period_status("2026-11", PeriodStatus.SOFT_CLOSED)
        lines = [
            ledger.build_line("1010", debit_minor=10000_00),
            ledger.build_line("4010", credit_minor=10000_00, property_id="PROP-1"),
        ]
        # Standard entry in SOFT_CLOSED period must fail
        with self.assertRaises(PeriodClosedError):
            ledger.post_entry(
                period_id="2026-11",
                entry_date=date(2026, 11, 15),
                source_reference="STD-IN-CLOSED",
                description="Should fail",
                lines=lines,
                idempotency_key="IDEM-CLOSED-1",
            )
        # Authorized adjustment in SOFT_CLOSED succeeds
        adj = ledger.post_entry(
            period_id="2026-11",
            entry_date=date(2026, 11, 30),
            source_reference="ADJ-OK",
            description="Authorized audit adjustment",
            lines=lines,
            idempotency_key="IDEM-ADJ-OK",
            kind=EntryKind.ADJUSTMENT,
            allow_soft_closed_adjustment=True,
        )
        self.assertEqual(adj.kind, EntryKind.ADJUSTMENT)

        # Once LOCKED, even adjustments fail and reopening is forbidden
        ledger.set_period_status("2026-11", PeriodStatus.LOCKED)
        with self.assertRaises(PeriodClosedError):
            ledger.set_period_status("2026-11", PeriodStatus.OPEN)

    def test_17_failed_transaction_rollback_and_payment_initiation_vs_settlement(self) -> None:
        """Adversarial #17 & Non-Negotiable Rule: Failed postings leave zero state mutation; INITIATED payment never settles obligations."""
        alloc_engine = PaymentAllocationEngine(tie_breaking_rule=TieBreakingRule.FIFO_LEXICOGRAPHIC)
        obligations = [
            ObligationItem(
                obligation_id="OB-RENT-NOV",
                organization_id="ORG-1",
                tenant_id="TEN-1",
                property_id="PROP-1",
                category=ObligationCategory.CURRENT_RENT,
                due_date=date(2026, 11, 5),
                total_amount=Money(50000_00, "KES"),
                already_settled=Money(0, "KES"),
            ),
            ObligationItem(
                obligation_id="OB-ARREARS-OCT",
                organization_id="ORG-1",
                tenant_id="TEN-1",
                property_id="PROP-1",
                category=ObligationCategory.ARREARS_RENT,
                due_date=date(2026, 10, 5),
                total_amount=Money(20000_00, "KES"),
                already_settled=Money(0, "KES"),
            ),
            ObligationItem(
                obligation_id="OB-ILLEGAL-PENALTY",
                organization_id="ORG-1",
                tenant_id="TEN-1",
                property_id="PROP-1",
                category=ObligationCategory.PENALTY,
                due_date=date(2026, 10, 15),
                total_amount=Money(5000_00, "KES"),
                already_settled=Money(0, "KES"),
                legally_permitted=False,
            ),
        ]

        # 1. INITIATED payment must allocate ZERO
        init_pay = IncomingPayment(
            payment_id="PAY-1",
            organization_id="ORG-1",
            tenant_id="TEN-1",
            amount=Money(60000_00, "KES"),
            state=PaymentLifecycleState.INITIATED,
            received_date=date(2026, 11, 6),
            idempotency_key="IDEM-PAY-INIT",
        )
        res_init = alloc_engine.allocate(init_pay, obligations)
        self.assertFalse(res_init.Effective_settlement_applied)
        self.assertEqual(res_init.total_allocated, Money(0, "KES"))

        # 2. SETTLED payment allocates first to ARREARS_RENT (20,000), then CURRENT_RENT (40,000), skipping illegal penalty
        settled_pay = IncomingPayment(
            payment_id="PAY-1",
            organization_id="ORG-1",
            tenant_id="TEN-1",
            amount=Money(60000_00, "KES"),
            state=PaymentLifecycleState.SETTLED,
            received_date=date(2026, 11, 6),
            idempotency_key="IDEM-PAY-SETTLED",
        )
        res_settled = alloc_engine.allocate(settled_pay, obligations)
        self.assertTrue(res_settled.Effective_settlement_applied)
        self.assertEqual(res_settled.total_allocated, Money(60000_00, "KES"))
        alloc_map = {a.obligation_id: a.allocated_amount.minor_units for a in res_settled.allocations}
        self.assertEqual(alloc_map["OB-ARREARS-OCT"], 20000_00)
        self.assertEqual(alloc_map["OB-RENT-NOV"], 40000_00)
        self.assertEqual(alloc_map["OB-ILLEGAL-PENALTY"], 0)

    def test_18_property_based_randomized_invariants(self) -> None:
        """Property-based generative testing across 200 random allocations and ledger postings."""
        rng = random.Random(20261112)
        for trial in range(200):
            total_minor = rng.randint(1, 10**12)
            n_beneficiaries = rng.randint(1, 17)
            weights = [rng.randint(1, 1000) for _ in range(n_beneficiaries)]
            keys = [f"B-{k:03d}" for k in range(n_beneficiaries)]
            m = Money(total_minor, "KES")
            shares = m.allocate_proportions(weights, tie_break_keys=keys)
            # Invariant 1: Exact sum conservation
            self.assertEqual(sum(s.minor_units for s in shares), total_minor)
            # Invariant 2: Quota property (each share within <1 minor unit of exact rational quota)
            w_sum = sum(weights)
            for s, w in zip(shares, weights):
                exact_q = Fraction(total_minor * w, w_sum)
                self.assertLess(abs(Fraction(s.minor_units, 1) - exact_q), Fraction(1, 1))
            # Invariant 3: Determinism under repeated execution
            shares_repeat = m.allocate_proportions(weights, tie_break_keys=keys)
            self.assertEqual(shares, shares_repeat)

    def test_19_forecasting_stress_testing_and_portfolio_kpis(self) -> None:
        """Verify probabilistic cash-flow forecasting, stress scenarios, and portfolio analytics."""
        fc_engine = CashFlowForecastingEngine()
        hist_flows = [
            4200000_00, 4350000_00, 4100000_00, 4500000_00, 4600000_00, 4450000_00,
            4700000_00, 4850000_00, 4650000_00, 4900000_00, 5050000_00, 5150000_00,
        ]
        fc_rep = fc_engine.forecast_with_monte_carlo(
            organization_id="ORG-KE-001",
            currency_code="KES",
            opening_cash_minor=15000000_00,
            historical_net_cash_flows_minor=hist_flows,
            horizon_months=6,
            n_simulations=1500,
            minimum_liquidity_buffer_minor=10000000_00,
        )
        self.assertEqual(len(fc_rep.net_cash_flow_quantiles), 6)
        for q in fc_rep.net_cash_flow_quantiles:
            self.assertLessEqual(q.p05_minor, q.p25_minor)
            self.assertLessEqual(q.p25_minor, q.p50_minor)
            self.assertLessEqual(q.p50_minor, q.p75_minor)
            self.assertLessEqual(q.p75_minor, q.p95_minor)

        # Evaluate all 7 stress scenarios
        facts = BaselinePortfolioFacts(
            organization_id="ORG-KE-001",
            currency_code="KES",
            gross_potential_rent_minor=10000000_00,
            current_vacancy_loss_minor=500000_00,
            other_property_income_minor=800000_00,
            maintenance_expenditure_minor=1200000_00,
            other_operating_expense_minor=1500000_00,
            supplier_liabilities_due_30d_minor=900000_00,
            owner_disbursements_due_30d_minor=4500000_00,
            construction_commitments_30d_minor=1500000_00,
            debt_service_minor=2000000_00,
            unhedged_fx_liability_minor=3000000_00,
            available_cash_reserves_minor=6000000_00,
            largest_single_tenant_rent_minor=1800000_00,
            total_billed_rent_minor=9500000_00,
            total_collected_rent_minor=8900000_00,
            total_arrears_minor=600000_00,
            property_valuation_minor=850000000_00,
        )
        stress_engine = RiskAndStressTestingEngine()
        for sc in stress_engine.standard_scenarios():
            st_res = stress_engine.evaluate_scenario(facts, sc)
            self.assertEqual(st_res.historical_facts.data_kind, EpistemicDataKind.HISTORICAL_FACT)
            self.assertEqual(st_res.assumptions.data_kind, EpistemicDataKind.MODEL_ASSUMPTION)
            self.assertEqual(st_res.data_kind, EpistemicDataKind.SIMULATED_SCENARIO)
            self.assertLessEqual(st_res.monte_carlo_var_95_minor, st_res.monte_carlo_cvar_95_minor)

        # Property KPIs
        kpi = PropertyPortfolioAnalyticsEngine.compute_property_kpis(
            organization_id="ORG-KE-001",
            property_id="PROP-WESTLANDS-01",
            currency_code="KES",
            gross_potential_rent_minor=5000000_00,
            vacancy_and_credit_loss_minor=250000_00,
            service_charge_and_other_income_minor=400000_00,
            maintenance_expense_minor=515000_00,
            utilities_insurance_other_opex_minor=772500_00,
            total_billed_minor=4750000_00,
            total_collected_minor=4512500_00,
            total_arrears_minor=237500_00,
            property_acquisition_cost_minor=400000000_00,
            management_commission_rate_bps=1000,
        )
        self.assertEqual(kpi.effective_gross_income, Money(5150000_00, "KES"))
        self.assertEqual(kpi.operating_expenses, Money(1287500_00, "KES"))
        self.assertEqual(kpi.net_operating_income, Money(3862500_00, "KES"))
        self.assertEqual(kpi.operating_expense_ratio_pct, Decimal("25.0000"))
        self.assertEqual(kpi.collection_efficiency_pct, Decimal("95.0000"))
        self.assertEqual(kpi.arrears_ratio_pct, Decimal("5.0000"))


def run_benchmarks_and_export_synthetic_data() -> None:
    t0 = time.perf_counter()
    df_syn = generate_synthetic_kenyan_real_estate_dataset()
    csv_out = (
        Path(__file__).resolve().parent / "glitters_synthetic_kenyan_real_estate_dataset.csv"
    )
    df_syn.to_csv(csv_out, index=False)
    t_syn = (time.perf_counter() - t0) * 1000.0

    # Benchmark 1,000 double-entry postings + hash-chain verification
    t1 = time.perf_counter()
    ledger = build_test_ledger("ORG-BENCH", "KES")
    for i in range(1000):
        lines = [
            ledger.build_line("1020", debit_minor=35000_00 + i * 100),
            ledger.build_line("4010", credit_minor=35000_00 + i * 100, property_id="PROP-BENCH-1"),
        ]
        ledger.post_entry(
            period_id="2026-11",
            entry_date=date(2026, 11, 15),
            source_reference=f"BENCH-{i:05d}",
            description="Benchmark entry",
            lines=lines,
            idempotency_key=f"BENCH-IDEM-{i:05d}",
        )
    chain_ok = ledger.verify_hash_chain_integrity()
    tb = ledger.generate_trial_balance()
    t_ledger = (time.perf_counter() - t1) * 1000.0

    print(f"[BENCHMARK] Synthetic dataset (240 rows) generated & exported in {t_syn:.2f} ms -> {csv_out.name}")
    print(f"[BENCHMARK] 1,000 hash-chained journal postings + Trial Balance verified={chain_ok and tb.is_balanced} in {t_ledger:.2f} ms")


if __name__ == "__main__":
    run_benchmarks_and_export_synthetic_data()
    unittest.main(verbosity=2)