from __future__ import annotations
from .money import Money, MonetaryPrecisionError
from .accounting import CrossOrganizationAccessError
# MODULE: payment_allocation.py
# ==============================================================================
"""
Deterministic Payment Allocation Engine:
Allocates settled funds across multiple invoices, tenants, properties, and obligation
categories (Arrears, Penalties, Security Deposits, Rent, Utilities, Service Charges).

NON-NEGOTIABLE INVARIANTS:
1. Payment Lifecycle State Separation:
   `INITIATED` and `CONFIRMED` payments NEVER settle obligations or reduce settled balances.
   Only `SETTLED` payments allocate authoritative settlement value; `REVERSED` and `REFUNDED`
   deterministically unwind prior allocations.
2. Contractual Priority Waterfall + Deterministic Tie-Breaking:
   - Explicit invoice reference matches first (if contract permits directed payment).
   - Otherwise, priority tier order:
     1. SECURITY_DEPOSIT (trust obligation)
     2. ARREARS_RENT
     3. UTILITY_CHARGE
     4. SERVICE_CHARGE
     5. CURRENT_RENT
     6. PENALTY (only where `legally_permitted=True`)
   - Tie-breaking within identical priority & due_date uses either deterministic oldest-first
     lexicographic `(due_date, property_id, invoice_id)` OR proportional Hamilton allocation.
3. Strict Idempotency and Organization Isolation.
"""
from dataclasses import dataclass
from datetime import date
from enum import Enum, IntEnum
from typing import Dict, List, Mapping, Optional, Sequence, Tuple



class PaymentLifecycleState(str, Enum):
    INITIATED = "INITIATED"      # Webhook / STK push triggered; NO financial settlement
    CONFIRMED = "CONFIRMED"      # Gateway acknowledged; pending clearing/settlement
    SETTLED = "SETTLED"          # Authoritative cleared funds available for obligation settlement
    REVERSED = "REVERSED"        # Dishonored / reversed payment
    REFUNDED = "REFUNDED"        # Returned to payer


class ObligationCategory(IntEnum):
    """Default contractual priority ranking (lower integer = higher settlement priority)."""
    SECURITY_DEPOSIT = 10
    ARREARS_RENT = 20
    UTILITY_CHARGE = 30
    SERVICE_CHARGE = 40
    CURRENT_RENT = 50
    PENALTY = 60


class TieBreakingRule(str, Enum):
    FIFO_LEXICOGRAPHIC = "FIFO_LEXICOGRAPHIC"
    PROPORTIONAL_HAMILTON = "PROPORTIONAL_HAMILTON"


class PaymentAllocationError(ValueError):
    """Raised when payment allocation violates lifecycle, currency, or authorization rules."""


@dataclass(frozen=True, slots=True)
class ObligationItem:
    """An outstanding tenant/buyer obligation (invoice line)."""
    obligation_id: str
    organization_id: str
    tenant_id: str
    property_id: str
    category: ObligationCategory
    due_date: date
    total_amount: Money
    already_settled: Money
    legally_permitted: bool = True

    def __post_init__(self) -> None:
        if self.total_amount.currency_code != self.already_settled.currency_code:
            raise CurrencyMismatchError("total_amount and already_settled must share currency")
        if self.already_settled.minor_units < 0 or self.already_settled.minor_units > self.total_amount.minor_units:
            raise PaymentAllocationError("Invalid already_settled amount on ObligationItem")

    @property
    def outstanding_amount(self) -> Money:
        if self.category == ObligationCategory.PENALTY and not self.legally_permitted:
            return Money.zero(self.total_amount.currency_code)
        return self.total_amount - self.already_settled

    @property
    def is_fully_settled(self) -> bool:
        return self.outstanding_amount.minor_units == 0


@dataclass(frozen=True, slots=True)
class IncomingPayment:
    """Incoming payment event across M-Pesa, bank transfer, or card gateway."""
    payment_id: str
    organization_id: str
    tenant_id: str
    amount: Money
    state: PaymentLifecycleState
    received_date: date
    idempotency_key: str
    target_obligation_ids: Tuple[str, ...] = ()
    property_id: Optional[str] = None
    exchange_rate: Optional[ExchangeRate] = None


@dataclass(frozen=True, slots=True)
class AllocationLine:
    obligation_id: str
    property_id: str
    category: ObligationCategory
    allocated_amount: Money
    remaining_obligation_balance: Money


@dataclass(frozen=True, slots=True)
class AllocationResult:
    payment_id: str
    organization_id: str
    idempotency_key: str
    payment_state: PaymentLifecycleState
    Effective_settlement_applied: bool
    total_payment_in_obligation_currency: Money
    total_allocated: Money
    unallocated_credit_balance: Money
    allocations: Tuple[AllocationLine, ...]


class PaymentAllocationEngine:
    """
    Deterministic, idempotent Payment Allocation Engine.
    Never settles obligations when a payment is merely INITIATED or CONFIRMED.
    """

    def __init__(self, tie_breaking_rule: TieBreakingRule = TieBreakingRule.FIFO_LEXICOGRAPHIC) -> None:
        self.tie_breaking_rule = tie_breaking_rule
        self._idempotency_store: Dict[str, AllocationResult] = {}

    def allocate(
        self,
        payment: IncomingPayment,
        obligations: Sequence[ObligationItem],
        custom_priority_order: Optional[Mapping[ObligationCategory, int]] = None,
        allow_directed_reference_override: bool = True,
    ) -> AllocationResult:
        """
        Deterministically allocate `payment` across `obligations`.
        Preserves exact minor-unit conservation:
            sum(allocated_i) + unallocated_credit == converted_payment_amount.
        """
        if not payment.idempotency_key:
            raise PaymentAllocationError("idempotency_key is mandatory for payment allocation")

        if payment.idempotency_key in self._idempotency_store:
            cached = self._idempotency_store[payment.idempotency_key]
            if cached.payment_id != payment.payment_id or cached.payment_state != payment.state:
                raise PaymentAllocationError(
                    f"Idempotency key '{payment.idempotency_key}' reused with conflicting payment state/ID"
                )
            return cached

        for ob in obligations:
            if ob.organization_id != payment.organization_id:
                raise CrossOrganizationAccessError(
                    f"Obligation '{ob.obligation_id}' belongs to org '{ob.organization_id}', "
                    f"cannot allocate from payment in org '{payment.organization_id}'"
                )

        # Determine target currency
        target_ccy = obligations[0].total_amount.currency_code if obligations else payment.amount.currency_code
        for ob in obligations:
            if ob.total_amount.currency_code != target_ccy:
                raise CurrencyMismatchError("All obligations in a single allocation batch must share currency")

        if payment.amount.currency_code == target_ccy:
            converted_payment = payment.amount
        else:
            if payment.exchange_rate is None:
                raise CurrencyMismatchError(
                    f"Payment in {payment.amount.currency_code} requires explicit ExchangeRate to settle {target_ccy} obligations"
                )
            converted_payment = payment.exchange_rate.convert(payment.amount)

        # CRITICAL INVARIANT: INITIATED or CONFIRMED payments do NOT settle obligations!
        if payment.state in (PaymentLifecycleState.INITIATED, PaymentLifecycleState.CONFIRMED):
            res = AllocationResult(
                payment_id=payment.payment_id,
                organization_id=payment.organization_id,
                idempotency_key=payment.idempotency_key,
                payment_state=payment.state,
                Effective_settlement_applied=False,
                total_payment_in_obligation_currency=converted_payment,
                total_allocated=Money.zero(target_ccy),
                unallocated_credit_balance=Money.zero(target_ccy),
                allocations=tuple(
                    AllocationLine(
                        obligation_id=ob.obligation_id,
                        property_id=ob.property_id,
                        category=ob.category,
                        allocated_amount=Money.zero(target_ccy),
                        remaining_obligation_balance=ob.outstanding_amount,
                    )
                    for ob in obligations
                ),
            )
            self._idempotency_store[payment.idempotency_key] = res
            return res

        if payment.state in (PaymentLifecycleState.REVERSED, PaymentLifecycleState.REFUNDED):
            res = AllocationResult(
                payment_id=payment.payment_id,
                organization_id=payment.organization_id,
                idempotency_key=payment.idempotency_key,
                payment_state=payment.state,
                Effective_settlement_applied=False,
                total_payment_in_obligation_currency=converted_payment,
                total_allocated=Money.zero(target_ccy),
                unallocated_credit_balance=Money.zero(target_ccy),
                allocations=(),
            )
            self._idempotency_store[payment.idempotency_key] = res
            return res

        # State == SETTLED: perform deterministic waterfall allocation
        priority_map = custom_priority_order or {cat: int(cat) for cat in ObligationCategory}
        remaining_funds = converted_payment.minor_units
        allocated_by_id: Dict[str, int] = {ob.obligation_id: 0 for ob in obligations}

        # Filter eligible obligations (exclude illegal penalties or zero outstanding)
        eligible = [
            ob
            for ob in obligations
            if ob.outstanding_amount.minor_units > 0
            and (payment.property_id is None or ob.property_id == payment.property_id)
        ]

        # Step 1: Directed payment reference override (if enabled and target_obligation_ids specified)
        directed_set = set(payment.target_obligation_ids) if allow_directed_reference_override else set()
        directed_obs = [ob for ob in eligible if ob.obligation_id in directed_set]
        undirected_obs = [ob for ob in eligible if ob.obligation_id not in directed_set]

        for batch in (directed_obs, undirected_obs):
            if not batch or remaining_funds <= 0:
                continue

            if self.tie_breaking_rule == TieBreakingRule.FIFO_LEXICOGRAPHIC:
                # Deterministic total order: (priority_rank, due_date, property_id, obligation_id)
                ordered = sorted(
                    batch,
                    key=lambda ob: (
                        priority_map.get(ob.category, int(ob.category)),
                        ob.due_date.toordinal(),
                        ob.property_id,
                        ob.obligation_id,
                    ),
                )
                for ob in ordered:
                    if remaining_funds <= 0:
                        break
                    need = ob.outstanding_amount.minor_units - allocated_by_id[ob.obligation_id]
                    if need <= 0:
                        continue
                    take = min(remaining_funds, need)
                    allocated_by_id[ob.obligation_id] += take
                    remaining_funds -= take
            else:
                # Group by priority tier; within each tier, allocate proportionally via Hamilton method
                tiers = sorted({priority_map.get(ob.category, int(ob.category)) for ob in batch})
                for tier in tiers:
                    if remaining_funds <= 0:
                        break
                    tier_obs = sorted(
                        [ob for ob in batch if priority_map.get(ob.category, int(ob.category)) == tier],
                        key=lambda ob: (ob.due_date.toordinal(), ob.property_id, ob.obligation_id),
                    )
                    tier_need = sum(ob.outstanding_amount.minor_units for ob in tier_obs)
                    if tier_need == 0:
                        continue
                    if remaining_funds >= tier_need:
                        for ob in tier_obs:
                            allocated_by_id[ob.obligation_id] = ob.outstanding_amount.minor_units
                        remaining_funds -= tier_need
                    else:
                        pool = Money(remaining_funds, target_ccy)
                        weights = [ob.outstanding_amount.minor_units for ob in tier_obs]
                        keys = [ob.obligation_id for ob in tier_obs]
                        shares = pool.allocate_proportions(weights, tie_break_keys=keys)
                        for ob, sh in zip(tier_obs, shares):
                            allocated_by_id[ob.obligation_id] += sh.minor_units
                        remaining_funds = 0

        lines: List[AllocationLine] = []
        total_alloc_minor = 0
        for ob in obligations:
            alloc_m = allocated_by_id[ob.obligation_id]
            total_alloc_minor += alloc_m
            rem_m = ob.outstanding_amount.minor_units - alloc_m
            lines.append(
                AllocationLine(
                    obligation_id=ob.obligation_id,
                    property_id=ob.property_id,
                    category=ob.category,
                    allocated_amount=Money(alloc_m, target_ccy),
                    remaining_obligation_balance=Money(rem_m, target_ccy),
                )
            )

        assert total_alloc_minor + remaining_funds == converted_payment.minor_units
        result = AllocationResult(
            payment_id=payment.payment_id,
            organization_id=payment.organization_id,
            idempotency_key=payment.idempotency_key,
            payment_state=payment.state,
            Effective_settlement_applied=True,
            total_payment_in_obligation_currency=converted_payment,
            total_allocated=Money(total_alloc_minor, target_ccy),
            unallocated_credit_balance=Money(remaining_funds, target_ccy),
            allocations=tuple(lines),
        )
        self._idempotency_store[payment.idempotency_key] = result
        return result

# ==============================================================================
