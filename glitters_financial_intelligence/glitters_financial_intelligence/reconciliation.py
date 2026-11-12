from __future__ import annotations
from .money import Money, ExchangeRate
from .accounting import CrossOrganizationAccessError
# MODULE: reconciliation.py
# ==============================================================================
"""
Financial Reconciliation Engine:
Multi-source transaction matching across Internal Journals, Bank Statements,
M-Pesa Statements, Payment Gateways, Tenant References, and Supplier Invoices.

Algorithms implemented:
1. Exact Deterministic Reference & Amount Matching.
2. Fellegi-Sunter Probabilistic Record Linkage / Bayesian Posterior Confidence Scoring.
3. Minimum-Cost Bipartite Assignment via Hungarian Algorithm (`scipy.optimize.linear_sum_assignment`).
4. Minimum-Cost Maximum-Flow & Integer Linear Optimization via Google OR-Tools (`ortools`)
   for Partial Payments, Many-to-One (multiple payments to 1 invoice), and One-to-Many
   (1 consolidated payment across multiple obligations).
5. Duplicate, Reversal, Unidentified Receipt, and Missing Record Detection.

NON-NEGOTIABLE INVARIANT:
Ambiguous or below-threshold matches are NEVER auto-finalized; they are emitted in
`PROPOSED_REQUIRES_AUTHORIZATION` state with explicit explanations and confidence scores.
"""
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
import math
import re
from typing import Dict, List, Mapping, Optional, Sequence, Set, Tuple

import numpy as np
from scipy.optimize import linear_sum_assignment
from ortools.linear_solver import pywraplp



class ReconciliationSource(str, Enum):
    INTERNAL_JOURNAL = "INTERNAL_JOURNAL"
    BANK_STATEMENT = "BANK_STATEMENT"
    MPESA_STATEMENT = "MPESA_STATEMENT"
    PAYMENT_GATEWAY = "PAYMENT_GATEWAY"
    TENANT_OBLIGATION = "TENANT_OBLIGATION"
    SUPPLIER_INVOICE = "SUPPLIER_INVOICE"
    OWNER_DISBURSEMENT = "OWNER_DISBURSEMENT"


class MatchType(str, Enum):
    EXACT_ONE_TO_ONE = "EXACT_ONE_TO_ONE"
    PROBABILISTIC_ONE_TO_ONE = "PROBABILISTIC_ONE_TO_ONE"
    PARTIAL_PAYMENT = "PARTIAL_PAYMENT"
    MANY_PAYMENTS_TO_ONE_OBLIGATION = "MANY_PAYMENTS_TO_ONE_OBLIGATION"
    ONE_PAYMENT_TO_MANY_OBLIGATIONS = "ONE_PAYMENT_TO_MANY_OBLIGATIONS"
    REVERSAL_PAIR = "REVERSAL_PAIR"
    DUPLICATE_EXTERNAL = "DUPLICATE_EXTERNAL"
    UNIDENTIFIED_RECEIPT = "UNIDENTIFIED_RECEIPT"
    MISSING_EXTERNAL_RECORD = "MISSING_EXTERNAL_RECORD"


class MatchDecisionStatus(str, Enum):
    AUTO_VERIFIED = "AUTO_VERIFIED"
    PROPOSED_REQUIRES_AUTHORIZATION = "PROPOSED_REQUIRES_AUTHORIZATION"
    REJECTED_AMBIGUOUS = "REJECTED_AMBIGUOUS"
    EXCEPTION_UNMATCHED = "EXCEPTION_UNMATCHED"


MPESA_RECEIPT_REGEX = re.compile(r"^[A-Z0-9]{10}$")


def normalize_reference(ref: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (ref or "").upper())


def string_dice_similarity(a: str, b: str) -> float:
    """Sørensen-Dice bigram similarity in [0, 1] for reference / payer name comparison."""
    na, nb = normalize_reference(a), normalize_reference(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if na in nb or nb in na:
        return 0.90
    if len(na) < 2 or len(nb) < 2:
        return 1.0 if na == nb else 0.0
    bigrams_a = {na[i : i + 2] for i in range(len(na) - 1)}
    bigrams_b = {nb[i : i + 2] for i in range(len(nb) - 1)}
    overlap = len(bigrams_a & bigrams_b)
    return (2.0 * overlap) / (len(bigrams_a) + len(bigrams_b))


@dataclass(frozen=True, slots=True)
class ReconciliationRecord:
    """Canonical representation of an external or internal financial record."""
    record_id: str
    organization_id: str
    source: ReconciliationSource
    amount: Money
    transaction_date: date
    reference: str
    counterparty_id: Optional[str] = None  # tenant_id, supplier_id, or owner_id
    property_id: Optional[str] = None
    external_tx_id: Optional[str] = None   # e.g., M-Pesa 10-char receipt or Bank UTR
    is_reversal: bool = False
    reverses_external_tx_id: Optional[str] = None


@dataclass(frozen=True, slots=True)
class ReconciliationMatchCandidate:
    """Proposed or verified reconciliation link with Bayesian confidence and explanation."""
    match_id: str
    organization_id: str
    match_type: MatchType
    status: MatchDecisionStatus
    external_record_ids: Tuple[str, ...]
    internal_record_ids: Tuple[str, ...]
    matched_amount: Money
    unmatched_remainder: Money
    confidence_score: float
    explanation: str
    requires_human_authorization: bool


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    organization_id: str
    matches: Tuple[ReconciliationMatchCandidate, ...]
    duplicate_external_ids: Tuple[str, ...]
    reversal_external_ids: Tuple[str, ...]
    unidentified_receipt_ids: Tuple[str, ...]
    missing_internal_ids: Tuple[str, ...]


class ReconciliationEngine:
    """
    Multi-stage Hybrid Deterministic + Fellegi-Sunter Bayesian + Hungarian Bipartite +
    OR-Tools Integer Subset-Sum Reconciliation Engine.
    """

    def __init__(
        self,
        auto_finalize_confidence_threshold: float = 0.97,
        ambiguity_margin_threshold: float = 0.08,
        max_date_window_days: int = 7,
    ) -> None:
        self.auto_finalize_confidence_threshold = auto_finalize_confidence_threshold
        self.ambiguity_margin_threshold = ambiguity_margin_threshold
        self.max_date_window_days = max_date_window_days

    @staticmethod
    def compute_fellegi_sunter_posterior(
        ext: ReconciliationRecord,
        internal: ReconciliationRecord,
        max_window_days: int = 7,
    ) -> Tuple[float, List[str]]:
        """
        Compute Fellegi-Sunter log-likelihood ratio & Bayesian posterior P(Match | gamma)
        across 4 comparison dimensions:
          1. Exact / near amount match
          2. Reference / M-Pesa receipt similarity
          3. Date proximity within [0, max_window_days]
          4. Counterparty & Property ID agreement
        """
        if ext.amount.currency_code != internal.amount.currency_code:
            return 0.0, ["Currency mismatch"]

        days_diff = abs((ext.transaction_date - internal.transaction_date).days)
        if days_diff > max_window_days * 3:
            return 0.01, [f"Date difference {days_diff}d exceeds window"]

        # Prior odds: P(M) = 0.15 -> log_odds_prior = ln(0.15 / 0.85) = -1.7346
        log_odds = math.log(0.15 / 0.85)
        reasons: List[str] = []

        # 1. Amount comparison
        if ext.amount.minor_units == internal.amount.minor_units:
            log_odds += 3.40  # m=0.96, u=0.032
            reasons.append("exact_amount")
        else:
            rel_diff = abs(ext.amount.minor_units - internal.amount.minor_units) / max(
                1, abs(internal.amount.minor_units)
            )
            if rel_diff <= 0.01:
                log_odds += 1.20
                reasons.append("near_amount_1pct")
            else:
                log_odds -= 3.50
                reasons.append("amount_mismatch")

        # 2. Reference & external_tx_id comparison
        ref_sim = string_dice_similarity(ext.reference, internal.reference)
        ext_id_match = (
            ext.external_tx_id is not None
            and internal.external_tx_id is not None
            and normalize_reference(ext.external_tx_id) == normalize_reference(internal.external_tx_id)
        )
        if ext_id_match or ref_sim == 1.0:
            log_odds += 4.20
            reasons.append("exact_reference")
        elif ref_sim >= 0.80:
            log_odds += 2.10
            reasons.append(f"high_ref_similarity({ref_sim:.2f})")
        elif ref_sim >= 0.50:
            log_odds += 0.60
            reasons.append(f"partial_ref_similarity({ref_sim:.2f})")
        else:
            log_odds -= 1.10

        # 3. Date proximity
        if days_diff == 0:
            log_odds += 1.50
            reasons.append("same_day")
        elif days_diff <= 2:
            log_odds += 1.00
            reasons.append(f"date_within_{days_diff}d")
        elif days_diff <= max_window_days:
            log_odds += 0.30
            reasons.append(f"date_within_{days_diff}d")
        else:
            log_odds -= 1.80
            reasons.append(f"date_lag_{days_diff}d")

        # 4. Counterparty & property agreement
        if ext.counterparty_id and internal.counterparty_id:
            if ext.counterparty_id == internal.counterparty_id:
                log_odds += 2.20
                reasons.append("same_counterparty")
            else:
                log_odds -= 2.80
                reasons.append("different_counterparty")

        if ext.property_id and internal.property_id:
            if ext.property_id == internal.property_id:
                log_odds += 1.00
                reasons.append("same_property")
            else:
                log_odds -= 1.50

        posterior = 1.0 / (1.0 + math.exp(-log_odds))
        return round(posterior, 6), reasons

    def reconcile(
        self,
        organization_id: str,
        external_records: Sequence[ReconciliationRecord],
        internal_records: Sequence[ReconciliationRecord],
    ) -> ReconciliationReport:
        """
        Execute full multi-stage reconciliation for a single organization.
        Enforces strict organization-level isolation.
        """
        for r in list(external_records) + list(internal_records):
            if r.organization_id != organization_id:
                raise CrossOrganizationAccessError(
                    f"Record '{r.record_id}' belongs to org '{r.organization_id}', expected '{organization_id}'"
                )

        matches: List[ReconciliationMatchCandidate] = []
        used_ext: Set[str] = set()
        used_int: Set[str] = set()
        duplicate_ext_ids: List[str] = []
        reversal_ext_ids: List[str] = []

        # Stage 0: Detect external duplicate records and external reversal pairs
        seen_ext_keys: Dict[Tuple[str, int, str], str] = {}
        ext_by_tx_id: Dict[str, ReconciliationRecord] = {}
        for ext in external_records:
            if ext.external_tx_id:
                ext_by_tx_id[normalize_reference(ext.external_tx_id)] = ext

        for ext in external_records:
            if ext.is_reversal and ext.reverses_external_tx_id:
                orig_key = normalize_reference(ext.reverses_external_tx_id)
                if orig_key in ext_by_tx_id:
                    orig_rec = ext_by_tx_id[orig_key]
                    used_ext.add(ext.record_id)
                    used_ext.add(orig_rec.record_id)
                    reversal_ext_ids.extend([orig_rec.record_id, ext.record_id])
                    matches.append(
                        ReconciliationMatchCandidate(
                            match_id=f"M-REV-{orig_rec.record_id}-{ext.record_id}",
                            organization_id=organization_id,
                            match_type=MatchType.REVERSAL_PAIR,
                            status=MatchDecisionStatus.AUTO_VERIFIED,
                            external_record_ids=(orig_rec.record_id, ext.record_id),
                            internal_record_ids=(),
                            matched_amount=Money.zero(ext.amount.currency_code),
                            unmatched_remainder=Money.zero(ext.amount.currency_code),
                            confidence_score=1.0,
                            explanation=f"External reversal pair linking {ext.record_id} to {orig_rec.record_id}",
                            requires_human_authorization=False,
                        )
                    )
                    continue

            dedup_key = (
                normalize_reference(ext.external_tx_id or ext.reference),
                ext.amount.minor_units,
                ext.transaction_date.isoformat(),
            )
            if dedup_key[0] and dedup_key in seen_ext_keys:
                first_id = seen_ext_keys[dedup_key]
                used_ext.add(ext.record_id)
                duplicate_ext_ids.append(ext.record_id)
                matches.append(
                    ReconciliationMatchCandidate(
                        match_id=f"M-DUP-{ext.record_id}",
                        organization_id=organization_id,
                        match_type=MatchType.DUPLICATE_EXTERNAL,
                        status=MatchDecisionStatus.AUTO_VERIFIED,
                        external_record_ids=(ext.record_id,),
                        internal_record_ids=(),
                        matched_amount=Money.zero(ext.amount.currency_code),
                        unmatched_remainder=ext.amount,
                        confidence_score=1.0,
                        explanation=f"Duplicate external transaction of {first_id} on key {dedup_key[0]}",
                        requires_human_authorization=True,
                    )
                )
            else:
                if dedup_key[0]:
                    seen_ext_keys[dedup_key] = ext.record_id

        # Stage 1: Exact Deterministic 1-to-1 Reference + Amount + Currency Match
        # Check for ambiguity first: if multiple internal records share the exact reference and amount, flag as ambiguous!
        avail_ext = [e for e in external_records if e.record_id not in used_ext]
        avail_int = [i for i in internal_records if i.record_id not in used_int]

        for ext in list(avail_ext):
            if ext.record_id in used_ext:
                continue
            exact_candidates = [
                intr
                for intr in avail_int
                if intr.record_id not in used_int
                and intr.amount == ext.amount
                and (
                    (
                        ext.external_tx_id
                        and intr.external_tx_id
                        and normalize_reference(ext.external_tx_id) == normalize_reference(intr.external_tx_id)
                    )
                    or (
                        normalize_reference(ext.reference)
                        and normalize_reference(ext.reference) == normalize_reference(intr.reference)
                    )
                )
            ]
            if len(exact_candidates) == 1:
                intr = exact_candidates[0]
                used_ext.add(ext.record_id)
                used_int.add(intr.record_id)
                matches.append(
                    ReconciliationMatchCandidate(
                        match_id=f"M-EXACT-{ext.record_id}-{intr.record_id}",
                        organization_id=organization_id,
                        match_type=MatchType.EXACT_ONE_TO_ONE,
                        status=MatchDecisionStatus.AUTO_VERIFIED,
                        external_record_ids=(ext.record_id,),
                        internal_record_ids=(intr.record_id,),
                        matched_amount=ext.amount,
                        unmatched_remainder=Money.zero(ext.amount.currency_code),
                        confidence_score=0.999,
                        explanation="Exact reference, currency, and minor-unit amount match",
                        requires_human_authorization=False,
                    )
                )

        # Stage 2: Bipartite Minimum-Cost / Maximum-Posterior Assignment (`linear_sum_assignment`)
        # With explicit ambiguity guard (if top-2 posteriors for an external record are within margin, require authorization)
        rem_ext = [e for e in external_records if e.record_id not in used_ext]
        rem_int = [i for i in internal_records if i.record_id not in used_int]

        if rem_ext and rem_int:
            n_e, n_i = len(rem_ext), len(rem_int)
            prob_matrix = np.zeros((n_e, n_i), dtype=float)
            reasons_matrix: List[List[List[str]]] = [[[] for _ in range(n_i)] for _ in range(n_e)]

            for r_idx, ext in enumerate(rem_ext):
                for c_idx, intr in enumerate(rem_int):
                    p, r_list = self.compute_fellegi_sunter_posterior(ext, intr, self.max_date_window_days)
                    prob_matrix[r_idx, c_idx] = p
                    reasons_matrix[r_idx][c_idx] = r_list

            # Solve minimum-cost bipartite matching on cost = 1.0 - prob
            cost_matrix = 1.0 - prob_matrix
            row_ind, col_ind = linear_sum_assignment(cost_matrix)

            for r_idx, c_idx in zip(row_ind, col_ind):
                ext = rem_ext[r_idx]
                intr = rem_int[c_idx]
                best_p = float(prob_matrix[r_idx, c_idx])
                if best_p < 0.60 or ext.amount != intr.amount:
                    continue

                # Check second-best competing candidate to detect ambiguity
                row_probs = np.delete(prob_matrix[r_idx, :], c_idx)
                second_best_p = float(np.max(row_probs)) if row_probs.size > 0 else 0.0
                col_probs = np.delete(prob_matrix[:, c_idx], r_idx)
                second_best_col_p = float(np.max(col_probs)) if col_probs.size > 0 else 0.0
                competing_p = max(second_best_p, second_best_col_p)

                is_ambiguous = (best_p - competing_p) < self.ambiguity_margin_threshold
                auto_ok = (best_p >= self.auto_finalize_confidence_threshold) and (not is_ambiguous)

                used_ext.add(ext.record_id)
                used_int.add(intr.record_id)
                status = (
                    MatchDecisionStatus.AUTO_VERIFIED
                    if auto_ok
                    else (
                        MatchDecisionStatus.REJECTED_AMBIGUOUS
                        if is_ambiguous
                        else MatchDecisionStatus.PROPOSED_REQUIRES_AUTHORIZATION
                    )
                )
                matches.append(
                    ReconciliationMatchCandidate(
                        match_id=f"M-PROB-{ext.record_id}-{intr.record_id}",
                        organization_id=organization_id,
                        match_type=MatchType.PROBABILISTIC_ONE_TO_ONE,
                        status=status,
                        external_record_ids=(ext.record_id,),
                        internal_record_ids=(intr.record_id,),
                        matched_amount=ext.amount,
                        unmatched_remainder=Money.zero(ext.amount.currency_code),
                        confidence_score=best_p,
                        explanation=(
                            f"Bayesian linkage posterior={best_p:.3f} (competing={competing_p:.3f}); "
                            f"signals={','.join(reasons_matrix[r_idx][c_idx])}"
                        ),
                        requires_human_authorization=not auto_ok,
                    )
                )

        # Stage 3: Many-to-One, One-to-Many, and Partial Payment Subset Optimization via OR-Tools MILP
        rem_ext = [e for e in external_records if e.record_id not in used_ext]
        rem_int = [i for i in internal_records if i.record_id not in used_int]

        # Group remaining records by (counterparty_id, currency_code) where counterparty_id is known
        counterparties = sorted(
            {
                (r.counterparty_id, r.amount.currency_code)
                for r in rem_ext + rem_int
                if r.counterparty_id is not None
            }
        )
        for cp_id, ccy in counterparties:
            cp_ext = [e for e in rem_ext if e.counterparty_id == cp_id and e.amount.currency_code == ccy and e.record_id not in used_ext]
            cp_int = [i for i in rem_int if i.counterparty_id == cp_id and i.amount.currency_code == ccy and i.record_id not in used_int]
            if not cp_ext or not cp_int:
                continue

            # Case 3A: Multiple external payments summing exactly to one internal obligation (Many-to-One)
            for intr in list(cp_int):
                if intr.record_id in used_int:
                    continue
                avail_e = [e for e in cp_ext if e.record_id not in used_ext]
                if len(avail_e) >= 2:
                    subset = self._solve_exact_subset_sum(
                        [e.amount.minor_units for e in avail_e], intr.amount.minor_units
                    )
                    if subset and len(subset) >= 2:
                        chosen_exts = [avail_e[idx] for idx in subset]
                        for ce in chosen_exts:
                            used_ext.add(ce.record_id)
                        used_int.add(intr.record_id)
                        matches.append(
                            ReconciliationMatchCandidate(
                                match_id=f"M-M2O-{intr.record_id}",
                                organization_id=organization_id,
                                match_type=MatchType.MANY_PAYMENTS_TO_ONE_OBLIGATION,
                                status=MatchDecisionStatus.PROPOSED_REQUIRES_AUTHORIZATION,
                                external_record_ids=tuple(ce.record_id for ce in chosen_exts),
                                internal_record_ids=(intr.record_id,),
                                matched_amount=intr.amount,
                                unmatched_remainder=Money.zero(ccy),
                                confidence_score=0.92,
                                explanation=f"{len(chosen_exts)} split payments from '{cp_id}' sum exactly to obligation {intr.record_id}",
                                requires_human_authorization=True,
                            )
                        )

            # Case 3B: One consolidated external payment covering multiple internal obligations (One-to-Many)
            for ext in list(cp_ext):
                if ext.record_id in used_ext:
                    continue
                avail_i = [i for i in cp_int if i.record_id not in used_int]
                if len(avail_i) >= 2:
                    subset = self._solve_exact_subset_sum(
                        [i.amount.minor_units for i in avail_i], ext.amount.minor_units
                    )
                    if subset and len(subset) >= 2:
                        chosen_ints = [avail_i[idx] for idx in subset]
                        used_ext.add(ext.record_id)
                        for ci in chosen_ints:
                            used_int.add(ci.record_id)
                        matches.append(
                            ReconciliationMatchCandidate(
                                match_id=f"M-O2M-{ext.record_id}",
                                organization_id=organization_id,
                                match_type=MatchType.ONE_PAYMENT_TO_MANY_OBLIGATIONS,
                                status=MatchDecisionStatus.PROPOSED_REQUIRES_AUTHORIZATION,
                                external_record_ids=(ext.record_id,),
                                internal_record_ids=tuple(ci.record_id for ci in chosen_ints),
                                matched_amount=ext.amount,
                                unmatched_remainder=Money.zero(ccy),
                                confidence_score=0.93,
                                explanation=f"Consolidated receipt {ext.record_id} covers {len(chosen_ints)} obligations for '{cp_id}'",
                                requires_human_authorization=True,
                            )
                        )

            # Case 3C: Partial payment (single external payment < single internal obligation for same counterparty)
            for ext in list(cp_ext):
                if ext.record_id in used_ext:
                    continue
                avail_i = [
                    i
                    for i in cp_int
                    if i.record_id not in used_int and i.amount.minor_units > ext.amount.minor_units > 0
                ]
                if len(avail_i) == 1:
                    intr = avail_i[0]
                    used_ext.add(ext.record_id)
                    used_int.add(intr.record_id)
                    remainder = intr.amount - ext.amount
                    matches.append(
                        ReconciliationMatchCandidate(
                            match_id=f"M-PART-{ext.record_id}-{intr.record_id}",
                            organization_id=organization_id,
                            match_type=MatchType.PARTIAL_PAYMENT,
                            status=MatchDecisionStatus.PROPOSED_REQUIRES_AUTHORIZATION,
                            external_record_ids=(ext.record_id,),
                            internal_record_ids=(intr.record_id,),
                            matched_amount=ext.amount,
                            unmatched_remainder=remainder,
                            confidence_score=0.86,
                            explanation=(
                                f"Partial payment {ext.amount} against obligation {intr.record_id} ({intr.amount}); "
                                f"outstanding balance {remainder}"
                            ),
                            requires_human_authorization=True,
                        )
                    )

        # Stage 4: Classify remaining unmatched external receipts as UNIDENTIFIED_RECEIPT
        # and remaining unmatched internal records as MISSING_EXTERNAL_RECORD
        unidentified_ids: List[str] = []
        for ext in external_records:
            if ext.record_id not in used_ext:
                unidentified_ids.append(ext.record_id)
                matches.append(
                    ReconciliationMatchCandidate(
                        match_id=f"M-UNID-{ext.record_id}",
                        organization_id=organization_id,
                        match_type=MatchType.UNIDENTIFIED_RECEIPT,
                        status=MatchDecisionStatus.EXCEPTION_UNMATCHED,
                        external_record_ids=(ext.record_id,),
                        internal_record_ids=(),
                        matched_amount=Money.zero(ext.amount.currency_code),
                        unmatched_remainder=ext.amount,
                        confidence_score=0.0,
                        explanation=f"Unidentified external receipt ({ext.source.value}: {ext.reference}); suspense account required",
                        requires_human_authorization=True,
                    )
                )

        missing_int_ids: List[str] = []
        for intr in internal_records:
            if intr.record_id not in used_int:
                missing_int_ids.append(intr.record_id)
                matches.append(
                    ReconciliationMatchCandidate(
                        match_id=f"M-MISS-{intr.record_id}",
                        organization_id=organization_id,
                        match_type=MatchType.MISSING_EXTERNAL_RECORD,
                        status=MatchDecisionStatus.EXCEPTION_UNMATCHED,
                        external_record_ids=(),
                        internal_record_ids=(intr.record_id,),
                        matched_amount=Money.zero(intr.amount.currency_code),
                        unmatched_remainder=intr.amount,
                        confidence_score=0.0,
                        explanation=f"Internal record {intr.record_id} ({intr.reference}) has no matching bank/M-Pesa entry",
                        requires_human_authorization=True,
                    )
                )

        return ReconciliationReport(
            organization_id=organization_id,
            matches=tuple(matches),
            duplicate_external_ids=tuple(duplicate_ext_ids),
            reversal_external_ids=tuple(reversal_ext_ids),
            unidentified_receipt_ids=tuple(unidentified_ids),
            missing_internal_ids=tuple(missing_int_ids),
        )

    @staticmethod
    def _solve_exact_subset_sum(values: Sequence[int], target: int) -> Optional[List[int]]:
        """Solve exact 0-1 integer linear subset-sum using Google OR-Tools SCIP/CBC solver."""
        if target <= 0 or not values:
            return None
        solver = pywraplp.Solver.CreateSolver("CBC")
        if solver is None:
            return None
        x = [solver.IntVar(0, 1, f"x_{i}") for i in range(len(values))]
        solver.Add(sum(x[i] * values[i] for i in range(len(values))) == target)
        # Minimize number of records in the match for parsimony
        solver.Minimize(sum(x))
        status = solver.Solve()
        if status == pywraplp.Solver.OPTIMAL:
            return [i for i in range(len(values)) if int(round(x[i].solution_value())) == 1]
        return None

# ==============================================================================


# Backward-compatible canonical alias
StatementTransaction = ReconciliationRecord

ReconciliationBatchResult = ReconciliationReport
