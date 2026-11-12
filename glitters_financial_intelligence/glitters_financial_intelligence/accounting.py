from __future__ import annotations
from .currencies import CurrencySpec, get_currency
from .money import Money, ExchangeRate, MonetaryPrecisionError, CurrencyMismatchError
# MODULE: accounting.py
# ==============================================================================
"""
Double-Entry Accounting Engine, Chart of Accounts, Journal Validation,
Cryptographic Ledger Integrity, Accrual Schedules, and Financial Statements.
"""
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
import hashlib
import threading
import uuid
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple



class AccountType(str, Enum):
    ASSET = "ASSET"
    LIABILITY = "LIABILITY"
    EQUITY = "EQUITY"
    REVENUE = "REVENUE"
    EXPENSE = "EXPENSE"

    @property
    def is_debit_normal(self) -> bool:
        return self in (AccountType.ASSET, AccountType.EXPENSE)


class CashFlowCategory(str, Enum):
    NONE = "NONE"
    CASH_AND_EQUIVALENTS = "CASH_AND_EQUIVALENTS"
    OPERATING = "OPERATING"
    INVESTING = "INVESTING"
    FINANCING = "FINANCING"


class PeriodStatus(str, Enum):
    OPEN = "OPEN"
    SOFT_CLOSED = "SOFT_CLOSED"
    LOCKED = "LOCKED"


class EntryStatus(str, Enum):
    DRAFT = "DRAFT"
    POSTED = "POSTED"
    REVERSED = "REVERSED"


class EntryKind(str, Enum):
    STANDARD = "STANDARD"
    ACCRUAL = "ACCRUAL"
    ADJUSTMENT = "ADJUSTMENT"
    REVERSAL = "REVERSAL"
    CLOSING = "CLOSING"


class AccountingValidationError(ValueError):
    """Raised when a journal entry or ledger action violates accounting invariants."""


class CrossOrganizationAccessError(PermissionError):
    """Raised when an operation attempts to cross organization boundaries."""


class PeriodClosedError(AccountingValidationError):
    """Raised when attempting to post to a closed or locked accounting period."""


@dataclass(frozen=True, slots=True)
class FinancialDimensions:
    """Multi-dimensional analytical tags attached to every journal line."""
    organization_id: str
    property_id: Optional[str] = None
    unit_id: Optional[str] = None
    tenant_id: Optional[str] = None
    owner_id: Optional[str] = None
    supplier_id: Optional[str] = None
    project_id: Optional[str] = None
    channel: Optional[str] = None  # e.g., 'MPESA', 'BANK_EFT', 'CARD', 'INTERNAL'


@dataclass(frozen=True, slots=True)
class Account:
    """Formal Chart of Accounts node."""
    account_id: str
    organization_id: str
    code: str
    name: str
    account_type: AccountType
    currency_code: str
    is_contra: bool = False
    is_postable: bool = True
    is_active: bool = True
    parent_code: Optional[str] = None
    cash_flow_category: CashFlowCategory = CashFlowCategory.OPERATING
    requires_property_dimension: bool = False

    def __post_init__(self) -> None:
        get_currency(self.currency_code)

    @property
    def normal_side(self) -> str:
        debit_normal = self.account_type.is_debit_normal
        if self.is_contra:
            debit_normal = not debit_normal
        return "DEBIT" if debit_normal else "CREDIT"

    def signed_normal_balance(self, debit_minor: int, credit_minor: int) -> int:
        if self.normal_side == "DEBIT":
            return debit_minor - credit_minor
        return credit_minor - debit_minor


@dataclass(frozen=True, slots=True)
class AccountingPeriod:
    """Fiscal accounting period with strict date boundaries and lock status."""
    period_id: str
    organization_id: str
    name: str
    start_date: date
    end_date: date
    status: PeriodStatus = PeriodStatus.OPEN

    def contains(self, target_date: date) -> bool:
        return self.start_date <= target_date <= self.end_date


@dataclass(frozen=True, slots=True)
class JournalLine:
    """
    A single debit or credit line within a JournalEntry.
    Invariants:
      - debit.minor_units >= 0 and credit.minor_units >= 0
      - Exactly one of debit or credit is > 0 (exclusive non-zero line invariant)
      - functional_debit and functional_credit are expressed in the organization's base currency
    """
    line_id: str
    account_code: str
    debit: Money
    credit: Money
    functional_debit: Money
    functional_credit: Money
    dimensions: FinancialDimensions
    description: str = ""
    exchange_rate: Optional[ExchangeRate] = None

    def __post_init__(self) -> None:
        if self.debit.currency_code != self.credit.currency_code:
            raise AccountingValidationError("Line debit and credit must share transaction currency")
        if self.functional_debit.currency_code != self.functional_credit.currency_code:
            raise AccountingValidationError("Line functional debit and credit must share base currency")
        if self.debit.minor_units < 0 or self.credit.minor_units < 0:
            raise AccountingValidationError("Debit and credit amounts must be non-negative")
        if self.functional_debit.minor_units < 0 or self.functional_credit.minor_units < 0:
            raise AccountingValidationError("Functional debit and credit amounts must be non-negative")
        if (self.debit.minor_units > 0 and self.credit.minor_units > 0) or (
            self.debit.minor_units == 0 and self.credit.minor_units == 0
        ):
            raise AccountingValidationError(
                "Each journal line must have strictly positive debit XOR strictly positive credit"
            )
        if (self.functional_debit.minor_units > 0 and self.functional_credit.minor_units > 0) or (
            self.functional_debit.minor_units == 0 and self.functional_credit.minor_units == 0
        ):
            raise AccountingValidationError(
                "Each journal line must have strictly positive functional debit XOR functional credit"
            )


@dataclass(frozen=True, slots=True)
class JournalEntry:
    """
    Immutable Journal Entry record. Once posted, acquires a cryptographic hash linking
    to the prior entry in the organization's ledger chain.
    """
    entry_id: str
    organization_id: str
    period_id: str
    entry_date: date
    posted_at: datetime
    kind: EntryKind
    status: EntryStatus
    source_reference: str
    description: str
    lines: Tuple[JournalLine, ...]
    idempotency_key: str
    reverses_entry_id: Optional[str] = None
    reversed_by_entry_id: Optional[str] = None
    previous_hash: str = "GENESIS"
    entry_hash: str = ""

    def compute_canonical_hash(self, previous_hash: str) -> str:
        hasher = hashlib.sha256()
        payload = [
            f"prev={previous_hash}",
            f"id={self.entry_id}",
            f"org={self.organization_id}",
            f"period={self.period_id}",
            f"date={self.entry_date.isoformat()}",
            f"kind={self.kind.value}",
            f"ref={self.source_reference}",
            f"idem={self.idempotency_key}",
            f"rev={self.reverses_entry_id or ''}",
        ]
        for idx, ln in enumerate(self.lines):
            payload.append(
                f"L{idx}:{ln.account_code}:D={ln.debit.minor_units}:{ln.debit.currency_code}:"
                f"C={ln.credit.minor_units}:FD={ln.functional_debit.minor_units}:"
                f"FC={ln.functional_credit.minor_units}:ORG={ln.dimensions.organization_id}:"
                f"PROP={ln.dimensions.property_id or ''}"
            )
        hasher.update("|".join(payload).encode("utf-8"))
        return hasher.hexdigest()


@dataclass(frozen=True, slots=True)
class TrialBalanceRow:
    account_code: str
    account_name: str
    account_type: AccountType
    debit_minor: int
    credit_minor: int
    net_normal_minor: int
    currency_code: str


@dataclass(frozen=True, slots=True)
class TrialBalanceReport:
    organization_id: str
    base_currency: str
    as_of_date: date
    rows: Tuple[TrialBalanceRow, ...]
    total_debits_minor: int
    total_credits_minor: int

    @property
    def is_balanced(self) -> bool:
        return self.total_debits_minor == self.total_credits_minor


@dataclass(frozen=True, slots=True)
class FinancialStatements:
    """Formal Balance Sheet, Income Statement, and Cash Flow Statement in base currency minor units."""
    organization_id: str
    base_currency: str
    as_of_date: date
    # Income Statement
    total_revenue_minor: int
    total_expenses_minor: int
    net_income_minor: int
    # Balance Sheet
    total_assets_minor: int
    total_liabilities_minor: int
    retained_earnings_minor: int
    total_equity_including_net_income_minor: int
    accounting_equation_holds: bool
    # Cash Flow Statement
    operating_cash_flow_minor: int
    investing_cash_flow_minor: int
    financing_cash_flow_minor: int
    net_cash_change_minor: int
    ending_cash_balance_minor: int
    cash_reconciles: bool


class JournalValidator:
    """
    Pre-posting formal validation engine.
    Enforces:
      1. Minimum 2 lines.
      2. Organization boundary isolation across Entry, Period, Accounts, and Dimensions.
      3. Period open status and date containment (unless authorized ADJUSTMENT in SOFT_CLOSED).
      4. Account existence, active status, leaf postability, and required dimensions.
      5. Functional currency consistency and exact double-entry zero-sum invariant:
         sum(functional_debits) == sum(functional_credits)
         and if single-currency transaction: sum(debits) == sum(credits).
    """

    @staticmethod
    def validate_before_posting(
        entry: JournalEntry,
        accounts: Mapping[str, Account],
        period: AccountingPeriod,
        base_currency: str,
        allow_soft_closed_adjustment: bool = False,
    ) -> None:
        if len(entry.lines) < 2:
            raise AccountingValidationError("Journal entry must contain at least 2 lines")

        if period.organization_id != entry.organization_id:
            raise CrossOrganizationAccessError(
                f"Period org '{period.organization_id}' does not match entry org '{entry.organization_id}'"
            )

        if period.status == PeriodStatus.LOCKED:
            raise PeriodClosedError(f"Period '{period.period_id}' is LOCKED and cannot accept postings")
        if period.status == PeriodStatus.SOFT_CLOSED:
            if not (allow_soft_closed_adjustment and entry.kind in (EntryKind.ADJUSTMENT, EntryKind.CLOSING)):
                raise PeriodClosedError(
                    f"Period '{period.period_id}' is SOFT_CLOSED; only authorized ADJUSTMENT/CLOSING entries allowed"
                )

        if not period.contains(entry.entry_date):
            raise AccountingValidationError(
                f"Entry date {entry.entry_date} falls outside period {period.name} ({period.start_date}..{period.end_date})"
            )

        total_func_debit = 0
        total_func_credit = 0
        tx_currencies: Set[str] = set()
        total_tx_debit = 0
        total_tx_credit = 0

        for line in entry.lines:
            if line.dimensions.organization_id != entry.organization_id:
                raise CrossOrganizationAccessError(
                    f"Line '{line.line_id}' organization '{line.dimensions.organization_id}' "
                    f"violates entry organization '{entry.organization_id}'"
                )
            if line.account_code not in accounts:
                raise AccountingValidationError(f"Unknown account code '{line.account_code}'")
            acct = accounts[line.account_code]
            if acct.organization_id != entry.organization_id:
                raise CrossOrganizationAccessError(
                    f"Account '{acct.code}' belongs to org '{acct.organization_id}', not '{entry.organization_id}'"
                )
            if not acct.is_active:
                raise AccountingValidationError(f"Account '{acct.code}' is inactive")
            if not acct.is_postable:
                raise AccountingValidationError(f"Account '{acct.code}' is a summary header and not directly postable")
            if acct.requires_property_dimension and not line.dimensions.property_id:
                raise AccountingValidationError(
                    f"Account '{acct.code}' ({acct.name}) requires a property_id dimension"
                )
            if line.debit.currency_code != acct.currency_code and acct.currency_code != base_currency:
                raise CurrencyMismatchError(
                    f"Line currency {line.debit.currency_code} does not match foreign currency account {acct.code} ({acct.currency_code})"
                )
            if line.functional_debit.currency_code != base_currency:
                raise CurrencyMismatchError(
                    f"Functional currency {line.functional_debit.currency_code} must match org base currency {base_currency}"
                )

            # Verify FX conversion consistency if transaction currency != base currency
            if line.debit.currency_code == base_currency:
                if line.debit.minor_units != line.functional_debit.minor_units or (
                    line.credit.minor_units != line.functional_credit.minor_units
                ):
                    raise AccountingValidationError(
                        f"Line '{line.line_id}' is in base currency {base_currency} but functional amounts differ"
                    )
            else:
                if line.exchange_rate is None:
                    raise AccountingValidationError(
                        f"Foreign currency line '{line.line_id}' ({line.debit.currency_code}) requires explicit ExchangeRate"
                    )
                expected_fd = line.exchange_rate.convert(line.debit)
                expected_fc = line.exchange_rate.convert(line.credit)
                if expected_fd != line.functional_debit or expected_fc != line.functional_credit:
                    raise AccountingValidationError(
                        f"Line '{line.line_id}' functional amount does not match ExchangeRate conversion"
                    )

            total_func_debit += line.functional_debit.minor_units
            total_func_credit += line.functional_credit.minor_units
            tx_currencies.add(line.debit.currency_code)
            total_tx_debit += line.debit.minor_units
            total_tx_credit += line.credit.minor_units

        if total_func_debit != total_func_credit:
            raise AccountingValidationError(
                f"Double-entry invariant violated in base currency ({base_currency}): "
                f"sum(Debits)={total_func_debit} != sum(Credits)={total_func_credit}"
            )

        if len(tx_currencies) == 1 and total_tx_debit != total_tx_credit:
            ccy = next(iter(tx_currencies))
            raise AccountingValidationError(
                f"Single-currency double-entry invariant violated ({ccy}): "
                f"sum(Debits)={total_tx_debit} != sum(Credits)={total_tx_credit}"
            )


class DoubleEntryLedger:
    """
    Thread-safe, immutable, hash-chained Double-Entry Ledger Engine for an organization.
    Supports multi-currency transactions, posting rules, controlled reversals, accruals,
    period closing, trial balance, and full financial statements.
    """

    def __init__(self, organization_id: str, base_currency: str = "KES") -> None:
        if not organization_id or not organization_id.strip():
            raise ValueError("organization_id is required")
        self.organization_id = organization_id.strip()
        self.base_currency = get_currency(base_currency).code
        self._accounts: Dict[str, Account] = {}
        self._periods: Dict[str, AccountingPeriod] = {}
        self._entries: Dict[str, JournalEntry] = {}
        self._entry_order: List[str] = []
        self._idempotency_index: Dict[str, str] = {}
        self._lock = threading.RLock()

    def register_account(self, account: Account) -> None:
        with self._lock:
            if account.organization_id != self.organization_id:
                raise CrossOrganizationAccessError(
                    f"Cannot register account from org '{account.organization_id}' into ledger '{self.organization_id}'"
                )
            self._accounts[account.code] = account

    def register_period(self, period: AccountingPeriod) -> None:
        with self._lock:
            if period.organization_id != self.organization_id:
                raise CrossOrganizationAccessError(
                    f"Cannot register period from org '{period.organization_id}' into ledger '{self.organization_id}'"
                )
            self._periods[period.period_id] = period

    def set_period_status(self, period_id: str, status: PeriodStatus) -> AccountingPeriod:
        with self._lock:
            if period_id not in self._periods:
                raise AccountingValidationError(f"Unknown period_id '{period_id}'")
            old = self._periods[period_id]
            if old.status == PeriodStatus.LOCKED and status != PeriodStatus.LOCKED:
                raise PeriodClosedError(f"Period '{period_id}' is permanently LOCKED and cannot be reopened")
            updated = AccountingPeriod(
                period_id=old.period_id,
                organization_id=old.organization_id,
                name=old.name,
                start_date=old.start_date,
                end_date=old.end_date,
                status=status,
            )
            self._periods[period_id] = updated
            return updated

    def build_line(
        self,
        account_code: str,
        debit_minor: int = 0,
        credit_minor: int = 0,
        currency_code: Optional[str] = None,
        exchange_rate: Optional[ExchangeRate] = None,
        property_id: Optional[str] = None,
        unit_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        owner_id: Optional[str] = None,
        supplier_id: Optional[str] = None,
        project_id: Optional[str] = None,
        channel: Optional[str] = None,
        description: str = "",
    ) -> JournalLine:
        ccy = currency_code or self.base_currency
        debit = Money(debit_minor, ccy)
        credit = Money(credit_minor, ccy)
        if ccy == self.base_currency:
            f_debit = debit
            f_credit = credit
        else:
            if exchange_rate is None:
                raise AccountingValidationError(f"ExchangeRate required for foreign currency {ccy}")
            f_debit = exchange_rate.convert(debit)
            f_credit = exchange_rate.convert(credit)
        dims = FinancialDimensions(
            organization_id=self.organization_id,
            property_id=property_id,
            unit_id=unit_id,
            tenant_id=tenant_id,
            owner_id=owner_id,
            supplier_id=supplier_id,
            project_id=project_id,
            channel=channel,
        )
        return JournalLine(
            line_id=str(uuid.uuid4()),
            account_code=account_code,
            debit=debit,
            credit=credit,
            functional_debit=f_debit,
            functional_credit=f_credit,
            dimensions=dims,
            description=description,
            exchange_rate=exchange_rate,
        )

    def post_entry(
        self,
        period_id: str,
        entry_date: date,
        source_reference: str,
        description: str,
        lines: Sequence[JournalLine],
        idempotency_key: str,
        kind: EntryKind = EntryKind.STANDARD,
        reverses_entry_id: Optional[str] = None,
        allow_soft_closed_adjustment: bool = False,
        entry_id: Optional[str] = None,
    ) -> JournalEntry:
        """
        Atomically validate and post an immutable journal entry.
        Idempotent with respect to `idempotency_key`: if already posted with identical
        canonical lines, returns the existing posted JournalEntry; if payload differs, raises error.
        """
        if not idempotency_key or not idempotency_key.strip():
            raise AccountingValidationError("idempotency_key is mandatory for posting")

        with self._lock:
            if idempotency_key in self._idempotency_index:
                existing_id = self._idempotency_index[idempotency_key]
                existing = self._entries[existing_id]
                # Verify payload compatibility
                if (
                    existing.source_reference != source_reference
                    or existing.entry_date != entry_date
                    or len(existing.lines) != len(lines)
                ):
                    raise AccountingValidationError(
                        f"Idempotency key '{idempotency_key}' reused with conflicting journal payload"
                    )
                return existing

            if period_id not in self._periods:
                raise AccountingValidationError(f"Unknown period_id '{period_id}'")
            period = self._periods[period_id]

            prev_hash = (
                self._entries[self._entry_order[-1]].entry_hash
                if self._entry_order
                else "GENESIS"
            )
            new_id = entry_id or f"JE-{ len(self._entry_order) + 1:06d}"
            draft = JournalEntry(
                entry_id=new_id,
                organization_id=self.organization_id,
                period_id=period_id,
                entry_date=entry_date,
                posted_at=datetime.now(timezone.utc),
                kind=kind,
                status=EntryStatus.POSTED,
                source_reference=source_reference,
                description=description,
                lines=tuple(lines),
                idempotency_key=idempotency_key,
                reverses_entry_id=reverses_entry_id,
                previous_hash=prev_hash,
            )

            JournalValidator.validate_before_posting(
                entry=draft,
                accounts=self._accounts,
                period=period,
                base_currency=self.base_currency,
                allow_soft_closed_adjustment=allow_soft_closed_adjustment,
            )

            computed_hash = draft.compute_canonical_hash(prev_hash)
            posted = JournalEntry(
                entry_id=draft.entry_id,
                organization_id=draft.organization_id,
                period_id=draft.period_id,
                entry_date=draft.entry_date,
                posted_at=draft.posted_at,
                kind=draft.kind,
                status=EntryStatus.POSTED,
                source_reference=draft.source_reference,
                description=draft.description,
                lines=draft.lines,
                idempotency_key=draft.idempotency_key,
                reverses_entry_id=draft.reverses_entry_id,
                previous_hash=prev_hash,
                entry_hash=computed_hash,
            )

            self._entries[posted.entry_id] = posted
            self._entry_order.append(posted.entry_id)
            self._idempotency_index[idempotency_key] = posted.entry_id
            return posted

    def reverse_entry(
        self,
        original_entry_id: str,
        reversal_period_id: str,
        reversal_date: date,
        reason: str,
        idempotency_key: str,
    ) -> JournalEntry:
        """
        Create a controlled, immutable reversal entry that swaps debits and credits
        of `original_entry_id` without mutating the historical entry's lines or hash.
        """
        with self._lock:
            if original_entry_id not in self._entries:
                raise AccountingValidationError(f"Original entry '{original_entry_id}' not found")
            orig = self._entries[original_entry_id]
            # Check if already reversed
            for eid in self._entry_order:
                e = self._entries[eid]
                if e.reverses_entry_id == original_entry_id and e.idempotency_key != idempotency_key:
                    raise AccountingValidationError(
                        f"Entry '{original_entry_id}' has already been reversed by '{e.entry_id}'"
                    )

            rev_lines: List[JournalLine] = []
            for ln in orig.lines:
                rev_lines.append(
                    JournalLine(
                        line_id=str(uuid.uuid4()),
                        account_code=ln.account_code,
                        debit=ln.credit,
                        credit=ln.debit,
                        functional_debit=ln.functional_credit,
                        functional_credit=ln.functional_debit,
                        dimensions=ln.dimensions,
                        description=f"REVERSAL of {orig.entry_id}: {ln.description}",
                        exchange_rate=ln.exchange_rate,
                    )
                )

            return self.post_entry(
                period_id=reversal_period_id,
                entry_date=reversal_date,
                source_reference=f"REV-{orig.source_reference}",
                description=f"Reversal of {orig.entry_id}: {reason}",
                lines=rev_lines,
                idempotency_key=idempotency_key,
                kind=EntryKind.REVERSAL,
                reverses_entry_id=orig.entry_id,
            )

    def verify_hash_chain_integrity(self) -> bool:
        """Verify cryptographic hash chain and double-entry balance across the entire ledger."""
        with self._lock:
            prev_hash = "GENESIS"
            for eid in self._entry_order:
                entry = self._entries[eid]
                if entry.previous_hash != prev_hash:
                    return False
                expected = entry.compute_canonical_hash(prev_hash)
                if entry.entry_hash != expected:
                    return False
                fd = sum(ln.functional_debit.minor_units for ln in entry.lines)
                fc = sum(ln.functional_credit.minor_units for ln in entry.lines)
                if fd != fc:
                    return False
                prev_hash = entry.entry_hash
            return True

    def generate_trial_balance(
        self,
        as_of_date: Optional[date] = None,
        period_id: Optional[str] = None,
        property_id: Optional[str] = None,
    ) -> TrialBalanceReport:
        """Compute exact Trial Balance in organization base currency."""
        with self._lock:
            debits_by_acct: Dict[str, int] = {code: 0 for code in self._accounts}
            credits_by_acct: Dict[str, int] = {code: 0 for code in self._accounts}

            for eid in self._entry_order:
                entry = self._entries[eid]
                if as_of_date is not None and entry.entry_date > as_of_date:
                    continue
                if period_id is not None and entry.period_id != period_id:
                    continue
                for ln in entry.lines:
                    if property_id is not None and ln.dimensions.property_id != property_id:
                        continue
                    debits_by_acct[ln.account_code] += ln.functional_debit.minor_units
                    credits_by_acct[ln.account_code] += ln.functional_credit.minor_units

            rows: List[TrialBalanceRow] = []
            total_d = 0
            total_c = 0
            for code in sorted(self._accounts.keys()):
                acct = self._accounts[code]
                d = debits_by_acct[code]
                c = credits_by_acct[code]
                if d == 0 and c == 0:
                    continue
                net_normal = acct.signed_normal_balance(d, c)
                rows.append(
                    TrialBalanceRow(
                        account_code=code,
                        account_name=acct.name,
                        account_type=acct.account_type,
                        debit_minor=d,
                        credit_minor=c,
                        net_normal_minor=net_normal,
                        currency_code=self.base_currency,
                    )
                )
                total_d += d
                total_c += c

            return TrialBalanceReport(
                organization_id=self.organization_id,
                base_currency=self.base_currency,
                as_of_date=as_of_date or date.today(),
                rows=tuple(rows),
                total_debits_minor=total_d,
                total_credits_minor=total_c,
            )

    def generate_financial_statements(self, as_of_date: Optional[date] = None) -> FinancialStatements:
        """
        Generate exact Income Statement, Balance Sheet, and Direct/Classified Cash Flow Statement.
        Verifies the fundamental accounting equation:
          Assets == Liabilities + Equity + (Revenue - Expenses)
        """
        with self._lock:
            tb = self.generate_trial_balance(as_of_date=as_of_date)
            assets = 0
            liabilities = 0
            equity = 0
            revenue = 0
            expenses = 0

            for r in tb.rows:
                acct = self._accounts[r.account_code]
                # Signed contribution to its major category (contra accounts subtract from category)
                signed_val = (
                    (r.debit_minor - r.credit_minor)
                    if acct.account_type.is_debit_normal
                    else (r.credit_minor - r.debit_minor)
                )
                if acct.account_type == AccountType.ASSET:
                    assets += signed_val
                elif acct.account_type == AccountType.LIABILITY:
                    liabilities += signed_val
                elif acct.account_type == AccountType.EQUITY:
                    equity += signed_val
                elif acct.account_type == AccountType.REVENUE:
                    revenue += signed_val
                elif acct.account_type == AccountType.EXPENSE:
                    expenses += signed_val

            net_income = revenue - expenses
            total_equity_incl = equity + net_income
            eq_holds = assets == (liabilities + total_equity_incl)

            # Cash Flow Classification by inspecting entries touching CASH_AND_EQUIVALENTS accounts
            op_cf = 0
            inv_cf = 0
            fin_cf = 0
            ending_cash = 0

            for eid in self._entry_order:
                entry = self._entries[eid]
                if as_of_date is not None and entry.entry_date > as_of_date:
                    continue
                cash_delta = 0
                non_cash_categories: List[CashFlowCategory] = []
                for ln in entry.lines:
                    acct = self._accounts[ln.account_code]
                    if acct.cash_flow_category == CashFlowCategory.CASH_AND_EQUIVALENTS:
                        cash_delta += ln.functional_debit.minor_units - ln.functional_credit.minor_units
                    else:
                        non_cash_categories.append(acct.cash_flow_category)
                if cash_delta != 0:
                    ending_cash += cash_delta
                    if CashFlowCategory.INVESTING in non_cash_categories:
                        inv_cf += cash_delta
                    elif CashFlowCategory.FINANCING in non_cash_categories:
                        fin_cf += cash_delta
                    else:
                        op_cf += cash_delta

            net_cf = op_cf + inv_cf + fin_cf
            return FinancialStatements(
                organization_id=self.organization_id,
                base_currency=self.base_currency,
                as_of_date=as_of_date or date.today(),
                total_revenue_minor=revenue,
                total_expenses_minor=expenses,
                net_income_minor=net_income,
                total_assets_minor=assets,
                total_liabilities_minor=liabilities,
                retained_earnings_minor=equity,
                total_equity_including_net_income_minor=total_equity_incl,
                accounting_equation_holds=eq_holds,
                operating_cash_flow_minor=op_cf,
                investing_cash_flow_minor=inv_cf,
                financing_cash_flow_minor=fin_cf,
                net_cash_change_minor=net_cf,
                ending_cash_balance_minor=ending_cash,
                cash_reconciles=(net_cf == ending_cash),
            )

    def generate_accrual_schedule(
        self,
        total_amount: Money,
        period_ids: Sequence[str],
        period_dates: Sequence[date],
        deferral_account_code: str,
        recognition_account_code: str,
        source_reference: str,
        property_id: Optional[str] = None,
        is_revenue_accrual: bool = True,
    ) -> List[JournalEntry]:
        """
        Recognize prepaid rent / deferred revenue or prepaid expenses across N periods
        using Hamilton exact minor-unit allocation so sum(period_recognitions) == total_amount.
        """
        if len(period_ids) != len(period_dates) or not period_ids:
            raise AccountingValidationError("period_ids and period_dates must be non-empty and equal length")
        allocations = total_amount.allocate_equal(len(period_ids), tie_break_keys=list(period_ids))
        posted_entries: List[JournalEntry] = []
        for pid, pdate, share in zip(period_ids, period_dates, allocations):
            if share.minor_units == 0:
                continue
            if is_revenue_accrual:
                # Debit Unearned/Deferred Revenue (Liability), Credit Rental Revenue
                dr_code, cr_code = deferral_account_code, recognition_account_code
            else:
                # Debit Expense, Credit Prepaid Asset / Accrued Liability
                dr_code, cr_code = recognition_account_code, deferral_account_code
            lines = [
                self.build_line(dr_code, debit_minor=share.minor_units, currency_code=share.currency_code, property_id=property_id),
                self.build_line(cr_code, credit_minor=share.minor_units, currency_code=share.currency_code, property_id=property_id),
            ]
            je = self.post_entry(
                period_id=pid,
                entry_date=pdate,
                source_reference=source_reference,
                description=f"Scheduled accrual recognition ({pid})",
                lines=lines,
                idempotency_key=f"ACCRUAL-{source_reference}-{pid}",
                kind=EntryKind.ACCRUAL,
            )
            posted_entries.append(je)
        return posted_entries

    @property
    def entries(self) -> List[JournalEntry]:
        with self._lock:
            return [self._entries[eid] for eid in self._entry_order]


@dataclass(frozen=True, slots=True)
class KenyanTaxConfig:
    """
    Explicitly parameterized tax configuration for Kenyan real estate operations.
    Separates accounting mechanics from statutory rates, which MUST be supplied and
    verified by qualified tax professionals rather than invented by the engine.
    """
    vat_rate: Optional[Decimal] = None
    withholding_tax_rate: Optional[Decimal] = None
    monthly_rental_income_rate: Optional[Decimal] = None


def create_standard_real_estate_coa(organization_id: str, base_currency: str = "KES") -> List[Account]:
    """
    Create a comprehensive Chart of Accounts supporting GLITTERS' real estate,
    hospitality, construction, agency commission, and property management operations.
    """
    ccy = base_currency
    return [
        # Assets (1000-1999)
        Account("A-1010", organization_id, "1010", "Operating Bank Account", AccountType.ASSET, ccy, cash_flow_category=CashFlowCategory.CASH_AND_EQUIVALENTS),
        Account("A-1020", organization_id, "1020", "M-Pesa Paybill Clearing Account", AccountType.ASSET, ccy, cash_flow_category=CashFlowCategory.CASH_AND_EQUIVALENTS),
        Account("A-1030", organization_id, "1030", "Client Trust / Escrow Bank Account", AccountType.ASSET, ccy, cash_flow_category=CashFlowCategory.CASH_AND_EQUIVALENTS),
        Account("A-1100", organization_id, "1100", "Tenant Accounts Receivable", AccountType.ASSET, ccy, requires_property_dimension=True),
        Account("A-1110", organization_id, "1110", "Allowance for Doubtful Tenant Accounts", AccountType.ASSET, ccy, is_contra=True, requires_property_dimension=True),
        Account("A-1200", organization_id, "1200", "Prepaid Insurance & Licenses", AccountType.ASSET, ccy),
        Account("A-1500", organization_id, "1500", "Construction Work-in-Progress (CIP)", AccountType.ASSET, ccy, cash_flow_category=CashFlowCategory.INVESTING, requires_property_dimension=True),
        Account("A-1600", organization_id, "1600", "Investment Property Cost", AccountType.ASSET, ccy, cash_flow_category=CashFlowCategory.INVESTING, requires_property_dimension=True),
        # Liabilities (2000-2999)
        Account("L-2010", organization_id, "2010", "Supplier Accounts Payable", AccountType.LIABILITY, ccy),
        Account("L-2050", organization_id, "2050", "Tenant Security Deposits Held", AccountType.LIABILITY, ccy, requires_property_dimension=True),
        Account("L-2100", organization_id, "2100", "Owner Disbursement Payable", AccountType.LIABILITY, ccy, requires_property_dimension=True),
        Account("L-2200", organization_id, "2200", "Unearned / Deferred Rental Income", AccountType.LIABILITY, ccy, requires_property_dimension=True),
        Account("L-2300", organization_id, "2300", "Unidentified Receipts Suspense", AccountType.LIABILITY, ccy),
        Account("L-2400", organization_id, "2400", "Tax Output / Withholding Payable", AccountType.LIABILITY, ccy),
        Account("L-2500", organization_id, "2500", "Construction Retention Payable", AccountType.LIABILITY, ccy, requires_property_dimension=True),
        # Equity (3000-3999)
        Account("E-3010", organization_id, "3010", "Organization Share Capital", AccountType.EQUITY, ccy, cash_flow_category=CashFlowCategory.FINANCING),
        Account("E-3100", organization_id, "3100", "Retained Earnings", AccountType.EQUITY, ccy, cash_flow_category=CashFlowCategory.FINANCING),
        # Revenue (4000-4999)
        Account("R-4010", organization_id, "4010", "Residential & Commercial Rental Income", AccountType.REVENUE, ccy, requires_property_dimension=True),
        Account("R-4020", organization_id, "4020", "Service Charge & Utility Recovery Income", AccountType.REVENUE, ccy, requires_property_dimension=True),
        Account("R-4030", organization_id, "4030", "Property Management Commission Revenue", AccountType.REVENUE, ccy, requires_property_dimension=True),
        Account("R-4040", organization_id, "4040", "Property Sales & Agency Commission Revenue", AccountType.REVENUE, ccy, requires_property_dimension=True),
        Account("R-4050", organization_id, "4050", "Hospitality & Short-Stay Room Revenue", AccountType.REVENUE, ccy, requires_property_dimension=True),
        Account("R-4060", organization_id, "4060", "Late Payment Penalty Revenue", AccountType.REVENUE, ccy, requires_property_dimension=True),
        # Expenses (5000-5999)
        Account("X-5010", organization_id, "5010", "Property Repairs & Maintenance Expense", AccountType.EXPENSE, ccy, requires_property_dimension=True),
        Account("X-5020", organization_id, "5020", "Property Insurance Expense", AccountType.EXPENSE, ccy, requires_property_dimension=True),
        Account("X-5030", organization_id, "5030", "Property Utilities & Security Expense", AccountType.EXPENSE, ccy, requires_property_dimension=True),
        Account("X-5040", organization_id, "5040", "Hospitality Operating Expense", AccountType.EXPENSE, ccy, requires_property_dimension=True),
        Account("X-5100", organization_id, "5100", "Organizational Administrative & Payroll Expense", AccountType.EXPENSE, ccy),
    ]

# ==============================================================================
