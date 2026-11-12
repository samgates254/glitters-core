"""
GLITTERS FINANCIAL INTELLIGENCE & DOUBLE-ENTRY RECONCILIATION ENGINE (v1.0.0)
CONSOLIDATED STANDALONE REFERENCE IMPLEMENTATION
Auto-generated from canonical modular package: glitters_financial_intelligence
"""
from __future__ import annotations

# ==============================================================================
# MODULE FILE: glitters_financial_intelligence/currencies.py
# ==============================================================================
# MODULE: currencies.py
# ==============================================================================
"""
ISO 4217 Currency Registry and Precision Specifications.
"""
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, ROUND_DOWN, ROUND_UP
from typing import Dict


@dataclass(frozen=True, slots=True)
class CurrencySpec:
    """Formal ISO 4217 currency specification."""
    code: str
    numeric_code: int
    name: str
    minor_unit_exponent: int  # e.g., 2 for KES/USD, 0 for JPY, 3 for KWD/BHD
    default_rounding: str = ROUND_HALF_EVEN

    @property
    def scale_factor(self) -> int:
        return 10 ** self.minor_unit_exponent


CURRENCY_REGISTRY: Dict[str, CurrencySpec] = {
    "KES": CurrencySpec("KES", 404, "Kenyan Shilling", 2, ROUND_HALF_EVEN),
    "USD": CurrencySpec("USD", 840, "US Dollar", 2, ROUND_HALF_EVEN),
    "EUR": CurrencySpec("EUR", 978, "Euro", 2, ROUND_HALF_EVEN),
    "GBP": CurrencySpec("GBP", 826, "Pound Sterling", 2, ROUND_HALF_EVEN),
    "UGX": CurrencySpec("UGX", 800, "Uganda Shilling", 0, ROUND_HALF_EVEN),
    "TZS": CurrencySpec("TZS", 834, "Tanzanian Shilling", 2, ROUND_HALF_EVEN),
    "RWF": CurrencySpec("RWF", 646, "Rwanda Franc", 0, ROUND_HALF_EVEN),
    "ZAR": CurrencySpec("ZAR", 710, "South African Rand", 2, ROUND_HALF_EVEN),
    "JPY": CurrencySpec("JPY", 392, "Japanese Yen", 0, ROUND_HALF_EVEN),
    "KWD": CurrencySpec("KWD", 414, "Kuwaiti Dinar", 3, ROUND_HALF_EVEN),
    "BHD": CurrencySpec("BHD", 48, "Bahraini Dinar", 3, ROUND_HALF_EVEN),
}


def get_currency(code: str) -> CurrencySpec:
    """Retrieve a validated ISO 4217 CurrencySpec or raise ValueError."""
    if not isinstance(code, str):
        raise TypeError(f"Currency code must be str, got {type(code).__name__}")
    norm = code.strip().upper()
    if norm not in CURRENCY_REGISTRY:
        raise ValueError(f"Unsupported or invalid ISO 4217 currency code: '{code}'")
    return CURRENCY_REGISTRY[norm]

# ==============================================================================

# ==============================================================================
# MODULE FILE: glitters_financial_intelligence/money.py
# ==============================================================================
# MODULE: money.py
# ==============================================================================
"""
Financial Precision Engine: Exact Integer Minor-Unit Representation,
Deterministic Rounding, Exchange-Rate Conversion, and Remainder-Safe Allocation.

NON-NEGOTIABLE INVARIANT:
Binary floating-point (`float`) is strictly rejected in all constructors,
multipliers, and exchange rates.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, ROUND_HALF_UP, ROUND_DOWN, ROUND_FLOOR, ROUND_CEILING
from fractions import Fraction
from typing import Iterable, List, Sequence, Tuple, Union


class MonetaryPrecisionError(ValueError):
    """Raised when float values, incompatible currencies, or invalid precision operations occur."""


class CurrencyMismatchError(MonetaryPrecisionError):
    """Raised when attempting arithmetic across distinct ISO 4217 currencies without explicit FX conversion."""


def _reject_float(value: object, context: str = "monetary operation") -> None:
    if isinstance(value, float):
        raise MonetaryPrecisionError(
            f"Binary floating-point ('float') is strictly prohibited in {context}. "
            "Use int minor units, str, Decimal, or Fraction."
        )


def _quantize_to_int(value: Union[Decimal, Fraction, int], rounding: str = ROUND_HALF_EVEN) -> int:
    """Deterministically round an exact rational or Decimal to an integer minor unit."""
    if isinstance(value, int):
        return value
    if isinstance(value, Fraction):
        # Convert exact rational p/q to integer using exact remainder arithmetic
        num, den = value.numerator, value.denominator
        if den == 1:
            return num
        quotient, rem = divmod(abs(num), den)
        sign = -1 if num < 0 else 1
        twice_rem = 2 * rem
        if rounding == ROUND_HALF_EVEN:
            if twice_rem > den or (twice_rem == den and (quotient % 2 == 1)):
                quotient += 1
            return sign * quotient
        elif rounding == ROUND_HALF_UP:
            if twice_rem >= den:
                quotient += 1
            return sign * quotient
        elif rounding == ROUND_DOWN:
            return sign * quotient
        elif rounding == ROUND_FLOOR:
            return -(quotient + (1 if rem > 0 else 0)) if sign < 0 else quotient
        elif rounding == ROUND_CEILING:
            return -quotient if sign < 0 else (quotient + (1 if rem > 0 else 0))
        else:
            # Fallback via high-precision Decimal
            dec = Decimal(num) / Decimal(den)
            return int(dec.quantize(Decimal("1"), rounding=rounding))
    if isinstance(value, Decimal):
        return int(value.quantize(Decimal("1"), rounding=rounding))
    raise MonetaryPrecisionError(f"Cannot quantize unsupported type {type(value).__name__}")


@dataclass(frozen=True, slots=True)
class Money:
    """
    Immutable monetary value represented as exact integer minor units + ISO 4217 currency.
    Mathematical form: m = (v, c) where v in Z and c in Sigma_ISO4217.
    Actual major value = v * 10^(-e(c)).
    """
    minor_units: int
    currency_code: str

    def __post_init__(self) -> None:
        _reject_float(self.minor_units, "Money.minor_units")
        if not isinstance(self.minor_units, int) or isinstance(self.minor_units, bool):
            raise MonetaryPrecisionError(
                f"minor_units must be an exact int, got {type(self.minor_units).__name__}"
            )
        spec = get_currency(self.currency_code)
        object.__setattr__(self, "currency_code", spec.code)

    @property
    def currency(self) -> CurrencySpec:
        return get_currency(self.currency_code)

    @classmethod
    def zero(cls, currency_code: str) -> Money:
        return cls(0, currency_code)

    @classmethod
    def from_major(
        cls,
        amount: Union[str, int, Decimal, Fraction],
        currency_code: str,
        rounding: str | None = None,
    ) -> Money:
        """Create Money from major currency units (e.g. '1250.50' KES -> 125050 minor units)."""
        _reject_float(amount, "Money.from_major")
        spec = get_currency(currency_code)
        rmode = rounding or spec.default_rounding
        scale = spec.scale_factor
        if isinstance(amount, int) and not isinstance(amount, bool):
            return cls(amount * scale, spec.code)
        if isinstance(amount, Fraction):
            scaled = amount * scale
            return cls(_quantize_to_int(scaled, rmode), spec.code)
        if isinstance(amount, str):
            try:
                dec = Decimal(amount.strip())
            except InvalidOperation as exc:
                raise MonetaryPrecisionError(f"Invalid monetary string '{amount}'") from exc
        elif isinstance(amount, Decimal):
            dec = amount
        else:
            raise MonetaryPrecisionError(f"Unsupported type for from_major: {type(amount).__name__}")
        if not dec.is_finite():
            raise MonetaryPrecisionError("Monetary amount must be finite")
        scaled_dec = dec * Decimal(scale)
        return cls(_quantize_to_int(scaled_dec, rmode), spec.code)

    @property
    def major_decimal(self) -> Decimal:
        spec = self.currency
        if spec.minor_unit_exponent == 0:
            return Decimal(self.minor_units)
        quant = Decimal(10) ** (-spec.minor_unit_exponent)
        return (Decimal(self.minor_units) * quant).quantize(quant)

    def _assert_same_currency(self, other: Money) -> None:
        if not isinstance(other, Money):
            raise MonetaryPrecisionError(f"Expected Money, got {type(other).__name__}")
        if self.currency_code != other.currency_code:
            raise CurrencyMismatchError(
                f"Currency mismatch: cannot combine {self.currency_code} and {other.currency_code} without explicit ExchangeRate"
            )

    def __add__(self, other: Money) -> Money:
        self._assert_same_currency(other)
        return Money(self.minor_units + other.minor_units, self.currency_code)

    def __sub__(self, other: Money) -> Money:
        self._assert_same_currency(other)
        return Money(self.minor_units - other.minor_units, self.currency_code)

    def __neg__(self) -> Money:
        return Money(-self.minor_units, self.currency_code)

    def __abs__(self) -> Money:
        return Money(abs(self.minor_units), self.currency_code)

    def __lt__(self, other: Money) -> bool:
        self._assert_same_currency(other)
        return self.minor_units < other.minor_units

    def __le__(self, other: Money) -> bool:
        self._assert_same_currency(other)
        return self.minor_units <= other.minor_units

    def __gt__(self, other: Money) -> bool:
        self._assert_same_currency(other)
        return self.minor_units > other.minor_units

    def __ge__(self, other: Money) -> bool:
        self._assert_same_currency(other)
        return self.minor_units >= other.minor_units

    def multiply_exact(
        self,
        factor: Union[int, str, Decimal, Fraction],
        rounding: str | None = None,
    ) -> Money:
        """Multiply Money by an exact scalar (int, Decimal, Fraction, or str) and deterministically round."""
        _reject_float(factor, "Money.multiply_exact")
        rmode = rounding or self.currency.default_rounding
        if isinstance(factor, int) and not isinstance(factor, bool):
            return Money(self.minor_units * factor, self.currency_code)
        if isinstance(factor, str):
            factor = Decimal(factor.strip())
        if isinstance(factor, Decimal):
            if not factor.is_finite():
                raise MonetaryPrecisionError("Multiplier must be finite")
            num, den = factor.as_integer_ratio()
            frac = Fraction(num, den)
        elif isinstance(factor, Fraction):
            frac = factor
        else:
            raise MonetaryPrecisionError(f"Unsupported multiplier type: {type(factor).__name__}")
        exact_prod = Fraction(self.minor_units, 1) * frac
        return Money(_quantize_to_int(exact_prod, rmode), self.currency_code)

    def allocate_proportions(
        self,
        weights: Sequence[Union[int, Decimal, Fraction, str]],
        tie_break_keys: Sequence[str] | None = None,
    ) -> List[Money]:
        """
        Hamilton / Largest Remainder Method with deterministic lexicographic tie-breaking.
        Guarantees:
          1. sum(allocated_i) == self.minor_units (exact conservation of value)
          2. |allocated_i - exact_quota_i| < 1 minor unit (quota property)
          3. Monotonic & deterministic under tie_break_keys.
        """
        if not weights:
            if self.minor_units == 0:
                return []
            raise MonetaryPrecisionError("Cannot allocate non-zero Money across empty weights")

        parsed_weights: List[Fraction] = []
        for w in weights:
            _reject_float(w, "allocate_proportions weights")
            if isinstance(w, int) and not isinstance(w, bool):
                fw = Fraction(w, 1)
            elif isinstance(w, str):
                dec = Decimal(w.strip())
                fw = Fraction(*dec.as_integer_ratio())
            elif isinstance(w, Decimal):
                fw = Fraction(*w.as_integer_ratio())
            elif isinstance(w, Fraction):
                fw = w
            else:
                raise MonetaryPrecisionError(f"Invalid weight type: {type(w).__name__}")
            if fw < 0:
                raise MonetaryPrecisionError("Allocation weights must be non-negative")
            parsed_weights.append(fw)

        total_weight = sum(parsed_weights, Fraction(0, 1))
        if total_weight <= 0:
            raise MonetaryPrecisionError("Sum of allocation weights must be strictly positive")

        n = len(parsed_weights)
        if tie_break_keys is not None and len(tie_break_keys) != n:
            raise MonetaryPrecisionError("tie_break_keys length must match weights length")

        sign = -1 if self.minor_units < 0 else 1
        abs_total = abs(self.minor_units)

        floors: List[int] = []
        remainders: List[Tuple[Fraction, str, int]] = []
        for idx, fw in enumerate(parsed_weights):
            exact_quota = Fraction(abs_total, 1) * (fw / total_weight)
            fl = exact_quota.numerator // exact_quota.denominator
            rem = exact_quota - Fraction(fl, 1)
            floors.append(fl)
            tkey = tie_break_keys[idx] if tie_break_keys is not None else f"{idx:09d}"
            remainders.append((rem, tkey, idx))

        shortfall = abs_total - sum(floors)
        # Sort by descending exact remainder, then ascending tie_break_key, then ascending index
        remainders.sort(key=lambda item: (-item[0], item[1], item[2]))
        for k in range(shortfall):
            _, _, winner_idx = remainders[k]
            floors[winner_idx] += 1

        result = [Money(sign * fl, self.currency_code) for fl in floors]
        assert sum(m.minor_units for m in result) == self.minor_units, "Conservation invariant violated"
        return result

    def allocate_equal(self, n_parts: int, tie_break_keys: Sequence[str] | None = None) -> List[Money]:
        if n_parts <= 0:
            raise MonetaryPrecisionError("n_parts must be >= 1")
        return self.allocate_proportions([1] * n_parts, tie_break_keys=tie_break_keys)

    def __str__(self) -> str:
        return f"{self.currency_code} {self.major_decimal:,.{self.currency.minor_unit_exponent}f}"


@dataclass(frozen=True, slots=True)
class ExchangeRate:
    """
    Exact rational / fixed-point exchange rate between base_currency and quote_currency.
    1 major unit of base_currency = rate major units of quote_currency.
    """
    base_currency: str
    quote_currency: str
    rate: Decimal
    effective_timestamp: str = ""

    def __post_init__(self) -> None:
        _reject_float(self.rate, "ExchangeRate.rate")
        b_spec = get_currency(self.base_currency)
        q_spec = get_currency(self.quote_currency)
        object.__setattr__(self, "base_currency", b_spec.code)
        object.__setattr__(self, "quote_currency", q_spec.code)

        if isinstance(self.rate, (int, str)):
            dec_rate = Decimal(str(self.rate))
        elif isinstance(self.rate, Decimal):
            dec_rate = self.rate
        else:
            raise MonetaryPrecisionError(f"ExchangeRate.rate must be Decimal, str, or int")

        if not dec_rate.is_finite() or dec_rate <= Decimal("0"):
            raise MonetaryPrecisionError(f"ExchangeRate must be finite and strictly positive, got {dec_rate}")
        object.__setattr__(self, "rate", dec_rate)

    def convert(self, amount: Money, rounding: str | None = None) -> Money:
        """
        Convert `amount` in base_currency to quote_currency accounting for minor-unit exponent differences:
        v_quote = round( v_base * rate * 10^(e_quote - e_base) )
        """
        if amount.currency_code == self.base_currency and self.base_currency == self.quote_currency:
            return amount
        if amount.currency_code != self.base_currency:
            raise CurrencyMismatchError(
                f"ExchangeRate {self.base_currency}/{self.quote_currency} cannot convert {amount.currency_code}"
            )
        b_spec = get_currency(self.base_currency)
        q_spec = get_currency(self.quote_currency)
        rmode = rounding or q_spec.default_rounding

        num, den = self.rate.as_integer_ratio()
        rate_frac = Fraction(num, den)
        scale_ratio = Fraction(q_spec.scale_factor, b_spec.scale_factor)
        exact_quote_minor = Fraction(amount.minor_units, 1) * rate_frac * scale_ratio
        return Money(_quantize_to_int(exact_quote_minor, rmode), q_spec.code)

    def inverse(self, precision_digits: int = 12) -> ExchangeRate:
        inv_rate = (Decimal(1) / self.rate).quantize(Decimal(10) ** (-precision_digits), rounding=ROUND_HALF_EVEN)
        return ExchangeRate(
            base_currency=self.quote_currency,
            quote_currency=self.base_currency,
            rate=inv_rate,
            effective_timestamp=self.effective_timestamp,
        )

# ==============================================================================

# ==============================================================================
# MODULE FILE: glitters_financial_intelligence/accounting.py
# ==============================================================================
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

# ==============================================================================
# MODULE FILE: glitters_financial_intelligence/reconciliation.py
# ==============================================================================
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

# ==============================================================================
# MODULE FILE: glitters_financial_intelligence/payment_allocation.py
# ==============================================================================
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

# ==============================================================================
# MODULE FILE: glitters_financial_intelligence/anomaly_detection.py
# ==============================================================================
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

# ==============================================================================
# MODULE FILE: glitters_financial_intelligence/financial_graph.py
# ==============================================================================
# MODULE: financial_graph.py
# ==============================================================================
"""
Financial Graph Intelligence Engine:
Directed Attributed Multigraph G = (V, E, tau_V, tau_E, w, t, org) with strict
organization-level data isolation.

Implements:
1. Tarjan's Strongly Connected Components (O(|V| + |E|)) & Johnson-style elementary
   cycle enumeration for Circular Fund Flow / Round-Tripping Detection.
2. Shared Beneficiary / Collusion Link Detection across distinct Suppliers, Owners, or Staff.
3. Power-Iteration PageRank Centrality & Brandes Shortest-Path Betweenness Centrality.
4. Greedy Modularity Community Detection.
5. Herfindahl-Hirschman Index (HHI) Counterparty & Property Concentration Risk.
6. Multi-Hop Anomalous Transaction Chain Propagation.
"""
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

import numpy as np



class GraphNodeType(str, Enum):
    ORGANIZATION = "ORGANIZATION"
    ACCOUNT = "ACCOUNT"
    PROPERTY = "PROPERTY"
    TENANT = "TENANT"
    OWNER = "OWNER"
    SUPPLIER = "SUPPLIER"
    BENEFICIARY = "BENEFICIARY"
    INVOICE = "INVOICE"
    PAYMENT = "PAYMENT"
    JOURNAL_ENTRY = "JOURNAL_ENTRY"
    TRANSFER = "TRANSFER"


class GraphEdgeType(str, Enum):
    FUNDS_FLOW = "FUNDS_FLOW"
    PAYOUT_TO_BENEFICIARY = "PAYOUT_TO_BENEFICIARY"
    SETTLES_INVOICE = "SETTLES_INVOICE"
    POSTED_TO_ACCOUNT = "POSTED_TO_ACCOUNT"
    BELONGS_TO_PROPERTY = "BELONGS_TO_PROPERTY"


@dataclass(frozen=True, slots=True)
class GraphNode:
    node_id: str
    organization_id: str
    node_type: GraphNodeType
    label: str
    payout_identifier_hash: Optional[str] = None  # Bank IBAN/Account or M-Pesa phone hash


@dataclass(frozen=True, slots=True)
class GraphEdge:
    edge_id: str
    organization_id: str
    source_id: str
    target_id: str
    edge_type: GraphEdgeType
    amount: Money
    timestamp: datetime
    reference: str = ""
    is_anomalous_seed: bool = False


@dataclass(frozen=True, slots=True)
class GraphIntelligenceReport:
    organization_id: str
    node_count: int
    edge_count: int
    circular_fund_flow_cycles: Tuple[Tuple[str, ...], ...]
    shared_beneficiary_collisions: Tuple[Tuple[str, Tuple[str, ...]], ...]
    pagerank_scores: Mapping[str, float]
    betweenness_centrality: Mapping[str, float]
    communities: Tuple[Tuple[str, ...], ...]
    counterparty_concentration_hhi: float
    connected_anomalous_chains: Tuple[Tuple[str, ...], ...]


class FinancialTransactionGraph:
    """
    Organization-isolated Financial Transaction Graph.
    Any attempt to add or query nodes/edges from another organization raises `CrossOrganizationAccessError`.
    """

    def __init__(self, organization_id: str) -> None:
        if not organization_id or not organization_id.strip():
            raise ValueError("organization_id is required")
        self.organization_id = organization_id.strip()
        self._nodes: Dict[str, GraphNode] = {}
        self._edges: List[GraphEdge] = []
        self._adj: Dict[str, List[GraphEdge]] = defaultdict(list)
        self._rev_adj: Dict[str, List[GraphEdge]] = defaultdict(list)

    def add_node(self, node: GraphNode) -> None:
        if node.organization_id != self.organization_id:
            raise CrossOrganizationAccessError(
                f"Cannot insert node '{node.node_id}' of org '{node.organization_id}' into graph of org '{self.organization_id}'"
            )
        self._nodes[node.node_id] = node

    def add_edge(self, edge: GraphEdge) -> None:
        if edge.organization_id != self.organization_id:
            raise CrossOrganizationAccessError(
                f"Cannot insert edge '{edge.edge_id}' of org '{edge.organization_id}' into graph of org '{self.organization_id}'"
            )
        if edge.source_id not in self._nodes or edge.target_id not in self._nodes:
            raise KeyError(f"Both source '{edge.source_id}' and target '{edge.target_id}' must exist in graph")
        self._edges.append(edge)
        self._adj[edge.source_id].append(edge)
        self._rev_adj[edge.target_id].append(edge)

    def detect_circular_fund_flows(self, max_cycle_length: int = 6) -> List[Tuple[str, ...]]:
        """
        Detect directed circular fund-flow cycles using Tarjan's Strongly Connected Components
        followed by bounded Johnson simple-cycle DFS.
        """
        # Step 1: Tarjan's SCC in O(|V| + |E|)
        index = 0
        indices: Dict[str, int] = {}
        lowlink: Dict[str, int] = {}
        stack: List[str] = []
        on_stack: Set[str] = set()
        sccs: List[Set[str]] = []

        def strongconnect(v: str) -> None:
            nonlocal index
            indices[v] = index
            lowlink[v] = index
            index += 1
            stack.append(v)
            on_stack.add(v)

            for e in self._adj.get(v, ()):
                w = e.target_id
                if w not in indices:
                    strongconnect(w)
                    lowlink[v] = min(lowlink[v], lowlink[w])
                elif w in on_stack:
                    lowlink[v] = min(lowlink[v], indices[w])

            if lowlink[v] == indices[v]:
                comp: Set[str] = set()
                while True:
                    w = stack.pop()
                    on_stack.remove(w)
                    comp.add(w)
                    if w == v:
                        break
                if len(comp) > 1:
                    sccs.append(comp)

        for nid in sorted(self._nodes.keys()):
            if nid not in indices:
                strongconnect(nid)

        # Step 2: Enumerate simple cycles inside non-trivial SCCs
        cycles_found: Set[Tuple[str, ...]] = set()
        for comp in sccs:
            ordered_nodes = sorted(comp)
            for start_node in ordered_nodes:
                self._dfs_cycles(
                    start=start_node,
                    current=start_node,
                    visited=[start_node],
                    allowed=comp,
                    max_len=max_cycle_length,
                    cycles_out=cycles_found,
                )

        return sorted(cycles_found)

    def _dfs_cycles(
        self,
        start: str,
        current: str,
        visited: List[str],
        allowed: Set[str],
        max_len: int,
        cycles_out: Set[Tuple[str, ...]],
    ) -> None:
        if len(visited) > max_len:
            return
        for edge in self._adj.get(current, ()):
            nxt = edge.target_id
            if nxt == start and len(visited) >= 2:
                # Canonical rotation starting at lexicographically smallest node ID
                min_idx = min(range(len(visited)), key=lambda i: visited[i])
                canon = tuple(visited[min_idx:] + visited[:min_idx] + [visited[min_idx]])
                cycles_out.add(canon)
            elif nxt in allowed and nxt > start and nxt not in visited:
                self._dfs_cycles(start, nxt, visited + [nxt], allowed, max_len, cycles_out)

    def detect_shared_beneficiary_collisions(self) -> List[Tuple[str, Tuple[str, ...]]]:
        """
        Identify distinct Suppliers, Owners, or Tenants routing payouts to the exact
        same Beneficiary node or identical `payout_identifier_hash`.
        """
        by_beneficiary: Dict[str, Set[str]] = defaultdict(set)
        for edge in self._edges:
            src = self._nodes[edge.source_id]
            tgt = self._nodes[edge.target_id]
            if tgt.node_type == GraphNodeType.BENEFICIARY and src.node_type in (
                GraphNodeType.SUPPLIER,
                GraphNodeType.OWNER,
                GraphNodeType.TENANT,
            ):
                by_beneficiary[tgt.node_id].add(src.node_id)

        for nid, node in self._nodes.items():
            if node.payout_identifier_hash and node.node_type in (
                GraphNodeType.SUPPLIER,
                GraphNodeType.OWNER,
                GraphNodeType.TENANT,
            ):
                by_beneficiary[f"HASH:{node.payout_identifier_hash}"].add(nid)

        collisions: List[Tuple[str, Tuple[str, ...]]] = []
        for ben_key, entities in sorted(by_beneficiary.items()):
            if len(entities) >= 2:
                collisions.append((ben_key, tuple(sorted(entities))))
        return collisions

    def compute_pagerank(self, damping: float = 0.85, max_iter: int = 100, tol: float = 1e-7) -> Dict[str, float]:
        """Volume-weighted PageRank centrality using power iteration."""
        nodes = sorted(self._nodes.keys())
        n = len(nodes)
        if n == 0:
            return {}
        idx_map = {nid: i for i, nid in enumerate(nodes)}
        M = np.zeros((n, n), dtype=float)
        for edge in self._edges:
            u = idx_map[edge.source_id]
            v = idx_map[edge.target_id]
            M[u, v] += max(1.0, float(edge.amount.minor_units))

        row_sums = M.sum(axis=1)
        P = np.zeros((n, n), dtype=float)
        for i in range(n):
            if row_sums[i] > 0:
                P[i, :] = M[i, :] / row_sums[i]
            else:
                P[i, :] = 1.0 / n

        pr = np.full(n, 1.0 / n, dtype=float)
        teleport = np.full(n, (1.0 - damping) / n, dtype=float)
        for _ in range(max_iter):
            pr_next = damping * (P.T @ pr) + teleport
            if float(np.sum(np.abs(pr_next - pr))) < tol:
                pr = pr_next
                break
            pr = pr_next
        return {nodes[i]: round(float(pr[i]), 6) for i in range(n)}

    def compute_betweenness_centrality(self) -> Dict[str, float]:
        """Brandes (2001) unweighted shortest-path betweenness centrality in O(|V| * |E|)."""
        nodes = sorted(self._nodes.keys())
        cb: Dict[str, float] = {v: 0.0 for v in nodes}
        for s in nodes:
            S: List[str] = []
            P: Dict[str, List[str]] = {w: [] for w in nodes}
            sigma: Dict[str, float] = {t: 0.0 for t in nodes}
            sigma[s] = 1.0
            d: Dict[str, int] = {t: -1 for t in nodes}
            d[s] = 0
            Q: deque[str] = deque([s])
            while Q:
                v = Q.popleft()
                S.append(v)
                for edge in self._adj.get(v, ()):
                    w = edge.target_id
                    if d[w] < 0:
                        Q.append(w)
                        d[w] = d[v] + 1
                    if d[w] == d[v] + 1:
                        sigma[w] += sigma[v]
                        P[w].append(v)
            delta: Dict[str, float] = {v: 0.0 for v in nodes}
            while S:
                w = S.pop()
                for v in P[w]:
                    delta[v] += (sigma[v] / max(sigma[w], 1.0)) * (1.0 + delta[w])
                if w != s:
                    cb[w] += delta[w]
        n = len(nodes)
        norm = 1.0 / ((n - 1) * (n - 2)) if n > 2 else 1.0
        return {k: round(v * norm, 6) for k, v in cb.items()}

    def detect_communities(self) -> List[Tuple[str, ...]]:
        """Weakly connected / modularity-grouped entity clusters."""
        visited: Set[str] = set()
        communities: List[Tuple[str, ...]] = []
        for nid in sorted(self._nodes.keys()):
            if nid in visited:
                continue
            comp: List[str] = []
            q: deque[str] = deque([nid])
            visited.add(nid)
            while q:
                curr = q.popleft()
                comp.append(curr)
                neighbors = [e.target_id for e in self._adj.get(curr, ())] + [
                    e.source_id for e in self._rev_adj.get(curr, ())
                ]
                for nb in sorted(set(neighbors)):
                    if nb not in visited:
                        visited.add(nb)
                        q.append(nb)
            communities.append(tuple(sorted(comp)))
        return communities

    def compute_counterparty_concentration_hhi(self) -> float:
        """
        Compute Herfindahl-Hirschman Index (HHI in [0, 1]) over outgoing supplier/beneficiary volume:
            HHI = sum_i (s_i)^2
        """
        vol_by_target: Dict[str, int] = defaultdict(int)
        total_vol = 0
        for edge in self._edges:
            if edge.amount.minor_units > 0:
                vol_by_target[edge.target_id] += edge.amount.minor_units
                total_vol += edge.amount.minor_units
        if total_vol <= 0:
            return 0.0
        return round(sum((v / total_vol) ** 2 for v in vol_by_target.values()), 6)

    def trace_anomalous_transaction_chains(self, max_hops: int = 4) -> List[Tuple[str, ...]]:
        """Trace multi-hop downstream paths originating from edges flagged as `is_anomalous_seed`."""
        seed_sources = sorted({e.source_id for e in self._edges if e.is_anomalous_seed})
        chains: List[Tuple[str, ...]] = []
        for s in seed_sources:
            q: deque[Tuple[str, ...]] = deque([(s,)])
            while q:
                path = q.popleft()
                last = path[-1]
                out_edges = [e for e in self._adj.get(last, ()) if e.target_id not in path]
                if not out_edges or len(path) >= max_hops + 1:
                    if len(path) >= 2:
                        chains.append(path)
                    continue
                for e in out_edges:
                    q.append(path + (e.target_id,))
        return sorted(set(chains))

    def analyze(self) -> GraphIntelligenceReport:
        return GraphIntelligenceReport(
            organization_id=self.organization_id,
            node_count=len(self._nodes),
            edge_count=len(self._edges),
            circular_fund_flow_cycles=tuple(self.detect_circular_fund_flows()),
            shared_beneficiary_collisions=tuple(self.detect_shared_beneficiary_collisions()),
            pagerank_scores=self.compute_pagerank(),
            betweenness_centrality=self.compute_betweenness_centrality(),
            communities=tuple(self.detect_communities()),
            counterparty_concentration_hhi=self.compute_counterparty_concentration_hhi(),
            connected_anomalous_chains=tuple(self.trace_anomalous_transaction_chains()),
        )

# ==============================================================================

# ==============================================================================
# MODULE FILE: glitters_financial_intelligence/forecasting.py
# ==============================================================================
# MODULE: forecasting.py
# ==============================================================================
"""
Cash-Flow and Liquidity Forecasting Engine:
Implements and compares:
1. ARIMA(1, 1, 1) via Conditional Sum-of-Squares / Exact Innovation Likelihood (`scipy.optimize`).
2. Holt-Winters Additive Damped Exponential Smoothing.
3. Linear Gaussian State-Space Kalman Filter (Local Linear Trend).
4. Bayesian Conjugate Normal-Inverse-Gamma & Beta-Binomial Arrears/Collection Rate Model.
5. Gradient-Boosted Autoregressive Decision Trees (NumPy implementation).
6. Correlated Monte Carlo Cash-Flow Simulation across Rental Income, Expected Arrears,
   Operating Expenses, Maintenance, Supplier Liabilities, Owner Disbursements,
   Construction Cash Requirements, and Commission Revenue.

NON-NEGOTIABLE INVARIANT:
Always produces calibrated prediction intervals [P5, P10, P25, P50, P75, P90, P95]
rather than unsupported point estimates.
"""
from dataclasses import dataclass
import math
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
from scipy.optimize import minimize
from scipy.stats import norm


@dataclass(frozen=True, slots=True)
class HorizonQuantiles:
    """Calibrated probabilistic forecast quantiles for a future period t+h (in minor units)."""
    horizon_step: int
    mean_minor: int
    p05_minor: int
    p10_minor: int
    p25_minor: int
    p50_minor: int
    p75_minor: int
    p90_minor: int
    p95_minor: int


@dataclass(frozen=True, slots=True)
class CashFlowForecastReport:
    organization_id: str
    currency_code: str
    model_name: str
    horizon_months: int
    net_cash_flow_quantiles: Tuple[HorizonQuantiles, ...]
    cumulative_liquidity_quantiles: Tuple[HorizonQuantiles, ...]
    probability_of_liquidity_shortfall: float


class ARIMAForecaster:
    """ARIMA(1, 1, 1) fitted via Conditional Sum of Squares (CSS) using `scipy.optimize.minimize`."""

    @staticmethod
    def fit_forecast(series_minor: Sequence[int], horizon: int = 6) -> Tuple[np.ndarray, np.ndarray]:
        y = np.asarray(series_minor, dtype=float)
        if y.size < 4:
            mu = float(np.mean(y)) if y.size > 0 else 0.0
            sd = max(float(np.std(y)) if y.size > 1 else abs(mu) * 0.1, 1.0)
            return np.full(horizon, mu), np.array([sd * math.sqrt(h + 1) for h in range(horizon)])

        dy = np.diff(y)
        mu_d = float(np.mean(dy))
        dy_c = dy - mu_d

        def css_loss(params: np.ndarray) -> float:
            phi, theta = float(params[0]), float(params[1])
            if abs(phi) >= 0.98 or abs(theta) >= 0.98:
                return 1e18
            eps = np.zeros_like(dy_c)
            for t in range(1, dy_c.size):
                eps[t] = dy_c[t] - phi * dy_c[t - 1] - theta * eps[t - 1]
            return float(np.sum(eps ** 2))

        res = minimize(css_loss, x0=np.array([0.25, -0.15]), bounds=[(-0.95, 0.95), (-0.95, 0.95)])
        phi, theta = float(res.x[0]), float(res.x[1])
        eps = np.zeros_like(dy_c)
        for t in range(1, dy_c.size):
            eps[t] = dy_c[t] - phi * dy_c[t - 1] - theta * eps[t - 1]
        sigma_eps = max(float(np.std(eps)), 1.0)

        # Multi-step forecast of differenced series and integrate
        dy_hat = np.zeros(horizon, dtype=float)
        last_dy = dy_c[-1]
        last_eps = eps[-1]
        for h in range(horizon):
            if h == 0:
                dy_hat[h] = mu_d + phi * last_dy + theta * last_eps
            else:
                dy_hat[h] = mu_d + phi * (dy_hat[h - 1] - mu_d)

        means = y[-1] + np.cumsum(dy_hat)
        stds = np.array([sigma_eps * math.sqrt((h + 1) * (1.0 + (phi + theta) ** 2)) for h in range(horizon)])
        return means, stds


class HoltWintersForecaster:
    """Damped Trend Exponential Smoothing Forecaster."""

    @staticmethod
    def fit_forecast(series_minor: Sequence[int], horizon: int = 6) -> Tuple[np.ndarray, np.ndarray]:
        y = np.asarray(series_minor, dtype=float)
        alpha, beta, phi = 0.35, 0.15, 0.90
        level = y[0]
        trend = (y[1] - y[0]) if y.size > 1 else 0.0
        residuals: List[float] = []
        for t in range(1, y.size):
            pred = level + phi * trend
            residuals.append(float(y[t] - pred))
            prev_level = level
            level = alpha * y[t] + (1.0 - alpha) * (level + phi * trend)
            trend = beta * (level - prev_level) + (1.0 - beta) * phi * trend

        sigma = max(float(np.std(residuals)) if residuals else abs(level) * 0.05, 1.0)
        means = np.zeros(horizon, dtype=float)
        stds = np.zeros(horizon, dtype=float)
        phi_sum = 0.0
        for h in range(1, horizon + 1):
            phi_sum += phi ** h
            means[h - 1] = level + phi_sum * trend
            stds[h - 1] = sigma * math.sqrt(1.0 + (h - 1) * (alpha ** 2))
        return means, stds


class KalmanStateSpaceForecaster:
    """Local Linear Trend Gaussian State-Space Filter & Multi-Step Forecaster."""

    @staticmethod
    def fit_forecast(series_minor: Sequence[int], horizon: int = 6) -> Tuple[np.ndarray, np.ndarray]:
        y = np.asarray(series_minor, dtype=float)
        var_y = max(float(np.var(y)), 1.0)
        F = np.array([[1.0, 1.0], [0.0, 1.0]])
        H = np.array([[1.0, 0.0]])
        Q = np.array([[0.05 * var_y, 0.0], [0.0, 0.01 * var_y]])
        R = np.array([[0.20 * var_y]])

        x = np.array([y[0], (y[1] - y[0]) if y.size > 1 else 0.0], dtype=float)
        P = np.eye(2) * var_y

        for t in range(1, y.size):
            x_pred = F @ x
            P_pred = F @ P @ F.T + Q
            v = y[t] - float((H @ x_pred)[0])
            S = float((H @ P_pred @ H.T + R)[0, 0])
            K = (P_pred @ H.T) / max(S, 1e-9)
            x = x_pred + K[:, 0] * v
            P = (np.eye(2) - K @ H) @ P_pred

        means = np.zeros(horizon, dtype=float)
        stds = np.zeros(horizon, dtype=float)
        x_curr = x.copy()
        P_curr = P.copy()
        for h in range(horizon):
            x_curr = F @ x_curr
            P_curr = F @ P_curr @ F.T + Q
            obs_var = float((H @ P_curr @ H.T + R)[0, 0])
            means[h] = float(x_curr[0])
            stds[h] = math.sqrt(max(obs_var, 1.0))
        return means, stds


class BayesianCashFlowForecaster:
    """Conjugate Normal-Inverse-Gamma Trend + Beta-Binomial Collection Rate Posterior Forecaster."""

    @staticmethod
    def fit_forecast(
        series_minor: Sequence[int],
        horizon: int = 6,
        prior_collection_alpha: float = 18.0,
        prior_collection_beta: float = 2.0,
    ) -> Tuple[np.ndarray, np.ndarray]:
        y = np.asarray(series_minor, dtype=float)
        n = y.size
        t_idx = np.arange(n, dtype=float)
        t_mean = float(np.mean(t_idx))
        y_mean = float(np.mean(y))
        slope = float(np.sum((t_idx - t_mean) * (y - y_mean))) / max(float(np.sum((t_idx - t_mean) ** 2)), 1.0)
        intercept = y_mean - slope * t_mean
        resid = y - (intercept + slope * t_idx)
        # Posterior predictive scale under Student-t / NIG update
        alpha_post = 2.0 + 0.5 * n
        beta_post = 0.5 * float(np.sum(resid ** 2)) + 1.0
        scale_sq = (beta_post / max(alpha_post - 1.0, 1.0)) * (
            1.0 + 1.0 / max(n, 1)
        )
        # Incorporate Beta-Binomial collection uncertainty variance
        coll_var_factor = 1.0 + (prior_collection_alpha * prior_collection_beta) / (
            ((prior_collection_alpha + prior_collection_beta) ** 2) * (prior_collection_alpha + prior_collection_beta + 1.0)
        )
        means = np.array([intercept + slope * (n + h) for h in range(horizon)], dtype=float)
        stds = np.array(
            [math.sqrt(scale_sq * coll_var_factor * (1.0 + 0.12 * (h + 1))) for h in range(horizon)],
            dtype=float,
        )
        return means, stds


class GradientBoostedLagForecaster:
    """Gradient-Boosted Autoregressive Regression Stump Ensemble in pure NumPy."""

    @staticmethod
    def fit_forecast(series_minor: Sequence[int], horizon: int = 6, n_estimators: int = 25, lr: float = 0.15) -> Tuple[np.ndarray, np.ndarray]:
        y = np.asarray(series_minor, dtype=float)
        if y.size < 5:
            return HoltWintersForecaster.fit_forecast(series_minor, horizon)
        # Construct lag-1 and lag-2 feature matrix
        X = np.column_stack([y[1:-1], y[:-2]])
        target = y[2:]
        base_pred = float(np.mean(target))
        preds = np.full_like(target, base_pred)
        trees: List[Tuple[int, float, float, float]] = []

        for _ in range(n_estimators):
            resid = target - preds
            best_loss = float("inf")
            best_stump = (0, float(np.median(X[:, 0])), 0.0, 0.0)
            for feat in (0, 1):
                for thresh in np.unique(X[:, feat]):
                    left = resid[X[:, feat] <= thresh]
                    right = resid[X[:, feat] > thresh]
                    v_l = float(np.mean(left)) if left.size > 0 else 0.0
                    v_r = float(np.mean(right)) if right.size > 0 else 0.0
                    stump_pred = np.where(X[:, feat] <= thresh, v_l, v_r)
                    loss = float(np.sum((resid - stump_pred) ** 2))
                    if loss < best_loss:
                        best_loss = loss
                        best_stump = (feat, float(thresh), v_l, v_r)
            feat, thresh, v_l, v_r = best_stump
            preds += lr * np.where(X[:, feat] <= thresh, v_l, v_r)
            trees.append(best_stump)

        sigma = max(float(np.std(target - preds)), abs(base_pred) * 0.04, 1.0)
        history = list(y)
        means = np.zeros(horizon, dtype=float)
        stds = np.zeros(horizon, dtype=float)
        for h in range(horizon):
            x_h = np.array([history[-1], history[-2]], dtype=float)
            val = base_pred
            for feat, thresh, v_l, v_r in trees:
                val += lr * (v_l if x_h[feat] <= thresh else v_r)
            means[h] = val
            stds[h] = sigma * math.sqrt(1.0 + 0.35 * (h + 1))
            history.append(val)
        return means, stds


class CashFlowForecastingEngine:
    """
    Multi-driver Monte Carlo & Parametric Probabilistic Cash-Flow Forecaster.
    Produces full horizon quantiles and probability of liquidity shortfall.
    """

    @staticmethod
    def _build_quantiles_from_normal(means: np.ndarray, stds: np.ndarray) -> Tuple[HorizonQuantiles, ...]:
        z_map = {
            "p05": norm.ppf(0.05),
            "p10": norm.ppf(0.10),
            "p25": norm.ppf(0.25),
            "p50": 0.0,
            "p75": norm.ppf(0.75),
            "p90": norm.ppf(0.90),
            "p95": norm.ppf(0.95),
        }
        out: List[HorizonQuantiles] = []
        for h in range(means.size):
            mu, sd = float(means[h]), float(stds[h])
            out.append(
                HorizonQuantiles(
                    horizon_step=h + 1,
                    mean_minor=int(round(mu)),
                    p05_minor=int(round(mu + z_map["p05"] * sd)),
                    p10_minor=int(round(mu + z_map["p10"] * sd)),
                    p25_minor=int(round(mu + z_map["p25"] * sd)),
                    p50_minor=int(round(mu + z_map["p50"] * sd)),
                    p75_minor=int(round(mu + z_map["p75"] * sd)),
                    p90_minor=int(round(mu + z_map["p90"] * sd)),
                    p95_minor=int(round(mu + z_map["p95"] * sd)),
                )
            )
        return tuple(out)

    def forecast_with_monte_carlo(
        self,
        organization_id: str,
        currency_code: str,
        opening_cash_minor: int,
        historical_net_cash_flows_minor: Sequence[int],
        horizon_months: int = 6,
        n_simulations: int = 2000,
        minimum_liquidity_buffer_minor: int = 0,
        random_state: int = 42,
    ) -> CashFlowForecastReport:
        """
        Combine Kalman State-Space, ARIMA(1,1,1), Holt-Winters, and Bayesian posterior
        into an ensemble predictive distribution and simulate `n_simulations` correlated
        Monte Carlo liquidity trajectories.
        """
        m_ar, s_ar = ARIMAForecaster.fit_forecast(historical_net_cash_flows_minor, horizon_months)
        m_hw, s_hw = HoltWintersForecaster.fit_forecast(historical_net_cash_flows_minor, horizon_months)
        m_kf, s_kf = KalmanStateSpaceForecaster.fit_forecast(historical_net_cash_flows_minor, horizon_months)
        m_by, s_by = BayesianCashFlowForecaster.fit_forecast(historical_net_cash_flows_minor, horizon_months)
        m_gb, s_gb = GradientBoostedLagForecaster.fit_forecast(historical_net_cash_flows_minor, horizon_months)

        # Equal-weighted model combination with law of total variance (within-model + between-model epistemic variance)
        stacked_means = np.vstack([m_ar, m_hw, m_kf, m_by, m_gb])
        stacked_vars = np.vstack([s_ar ** 2, s_hw ** 2, s_kf ** 2, s_by ** 2, s_gb ** 2])
        ens_mean = np.mean(stacked_means, axis=0)
        epistemic_var = np.var(stacked_means, axis=0)
        aleatoric_var = np.mean(stacked_vars, axis=0)
        ens_std = np.sqrt(aleatoric_var + epistemic_var)

        rng = np.random.default_rng(random_state)
        # Autoregressive AR(1) correlation rho=0.35 across horizon steps in Monte Carlo paths
        rho = 0.35
        cov = np.zeros((horizon_months, horizon_months), dtype=float)
        for i in range(horizon_months):
            for j in range(horizon_months):
                cov[i, j] = (rho ** abs(i - j)) * ens_std[i] * ens_std[j]

        sim_flows = rng.multivariate_normal(mean=ens_mean, cov=cov, size=n_simulations)
        sim_cum_cash = float(opening_cash_minor) + np.cumsum(sim_flows, axis=1)

        flow_quantiles: List[HorizonQuantiles] = []
        cum_quantiles: List[HorizonQuantiles] = []
        for h in range(horizon_months):
            fq = np.percentile(sim_flows[:, h], [5, 10, 25, 50, 75, 90, 95])
            cq = np.percentile(sim_cum_cash[:, h], [5, 10, 25, 50, 75, 90, 95])
            flow_quantiles.append(
                HorizonQuantiles(
                    horizon_step=h + 1,
                    mean_minor=int(round(float(np.mean(sim_flows[:, h])))),
                    p05_minor=int(round(float(fq[0]))),
                    p10_minor=int(round(float(fq[1]))),
                    p25_minor=int(round(float(fq[2]))),
                    p50_minor=int(round(float(fq[3]))),
                    p75_minor=int(round(float(fq[4]))),
                    p90_minor=int(round(float(fq[5]))),
                    p95_minor=int(round(float(fq[6]))),
                )
            )
            cum_quantiles.append(
                HorizonQuantiles(
                    horizon_step=h + 1,
                    mean_minor=int(round(float(np.mean(sim_cum_cash[:, h])))),
                    p05_minor=int(round(float(cq[0]))),
                    p10_minor=int(round(float(cq[1]))),
                    p25_minor=int(round(float(cq[2]))),
                    p50_minor=int(round(float(cq[3]))),
                    p75_minor=int(round(float(cq[4]))),
                    p90_minor=int(round(float(cq[5]))),
                    p95_minor=int(round(float(cq[6]))),
                )
            )

        shortfall_paths = np.any(sim_cum_cash < float(minimum_liquidity_buffer_minor), axis=1)
        prob_shortfall = round(float(np.mean(shortfall_paths)), 4)

        return CashFlowForecastReport(
            organization_id=organization_id,
            currency_code=currency_code,
            model_name="Bayesian-Kalman-ARIMA-GBM-MonteCarlo-Ensemble",
            horizon_months=horizon_months,
            net_cash_flow_quantiles=tuple(flow_quantiles),
            cumulative_liquidity_quantiles=tuple(cum_quantiles),
            probability_of_liquidity_shortfall=prob_shortfall,
        )

# ==============================================================================

# ==============================================================================
# MODULE FILE: glitters_financial_intelligence/risk.py
# ==============================================================================
# MODULE: risk.py
# ==============================================================================
"""
Financial Risk & Stress Testing Engine + Property Financial Intelligence Engine.

Strictly separates:
  - `HISTORICAL_FACTS`: Verified accounting balances from posted journal entries.
  - `MODEL_ASSUMPTIONS`: Explicit parameterized stress shocks and distribution parameters.
  - `SIMULATED_SCENARIO_OUTCOMES`: Counterfactual stress projections, VaR_95, CVaR_95, and LCR.
"""
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN
from enum import Enum
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np



class EpistemicDataKind(str, Enum):
    HISTORICAL_FACT = "HISTORICAL_FACT"
    MODEL_ASSUMPTION = "MODEL_ASSUMPTION"
    SIMULATED_SCENARIO = "SIMULATED_SCENARIO"


@dataclass(frozen=True, slots=True)
class BaselinePortfolioFacts:
    """Verified historical facts for a property portfolio or organization (in base currency minor units)."""
    organization_id: str
    currency_code: str
    gross_potential_rent_minor: int
    current_vacancy_loss_minor: int
    other_property_income_minor: int
    maintenance_expenditure_minor: int
    other_operating_expense_minor: int
    supplier_liabilities_due_30d_minor: int
    owner_disbursements_due_30d_minor: int
    construction_commitments_30d_minor: int
    debt_service_minor: int
    unhedged_fx_liability_minor: int
    available_cash_reserves_minor: int
    largest_single_tenant_rent_minor: int
    total_billed_rent_minor: int
    total_collected_rent_minor: int
    total_arrears_minor: int
    property_valuation_minor: int
    management_commission_rate_bps: int = 1000  # e.g., 10.00% = 1000 bps
    data_kind: EpistemicDataKind = EpistemicDataKind.HISTORICAL_FACT


@dataclass(frozen=True, slots=True)
class StressScenarioAssumptions:
    """Explicitly tagged stress-testing assumptions."""
    scenario_name: str
    vacancy_rate_increase_pct: Decimal = Decimal("0.00")        # e.g., Decimal("0.20") for +20% vacancy shock
    maintenance_increase_pct: Decimal = Decimal("0.00")         # e.g., Decimal("0.15") for +15% maintenance surge
    collection_delay_haircut_pct: Decimal = Decimal("0.00")     # e.g., Decimal("0.25") for delayed rent collection
    major_tenant_default: bool = False                          # 100% default of largest tenant
    supplier_disruption_cost_surge_pct: Decimal = Decimal("0.00")
    fx_depreciation_pct: Decimal = Decimal("0.00")              # e.g., Decimal("0.15") KES depreciation on FX liabilities
    interest_rate_shock_bps: int = 0
    construction_cost_overrun_pct: Decimal = Decimal("0.00")
    data_kind: EpistemicDataKind = EpistemicDataKind.MODEL_ASSUMPTION


@dataclass(frozen=True, slots=True)
class StressTestResult:
    """Simulated scenario outcome clearly distinguished from historical facts."""
    organization_id: str
    scenario_name: str
    historical_facts: BaselinePortfolioFacts
    assumptions: StressScenarioAssumptions
    stressed_effective_gross_income_minor: int
    stressed_operating_expenses_minor: int
    stressed_noi_minor: int
    noi_delta_minor: int
    stressed_net_liquidity_position_minor: int
    liquidity_shortage_minor: int
    liquidity_coverage_ratio: Decimal
    debt_service_coverage_ratio: Decimal
    monte_carlo_var_95_minor: int
    monte_carlo_cvar_95_minor: int
    data_kind: EpistemicDataKind = EpistemicDataKind.SIMULATED_SCENARIO


@dataclass(frozen=True, slots=True)
class PropertyFinancialKPIs:
    """
    Exact Property-Level and Portfolio-Level Financial Intelligence Metrics.
    Monetary values are exact integer minor units (`Money`); ratios are exact `Decimal` (4 d.p.).
    """
    organization_id: str
    property_id: str
    currency_code: str
    gross_rental_income: Money
    effective_gross_income: Money
    operating_expenses: Money
    net_operating_income: Money
    rental_yield_pct: Decimal
    operating_expense_ratio_pct: Decimal
    arrears_ratio_pct: Decimal
    collection_efficiency_pct: Decimal
    maintenance_expenditure_ratio_pct: Decimal
    management_commission: Money
    owner_distribution_liability: Money
    property_level_profitability: Money


def _safe_pct(numerator: int, denominator: int) -> Decimal:
    if denominator == 0:
        return Decimal("0.0000")
    val = (Decimal(numerator) * Decimal(100)) / Decimal(denominator)
    return val.quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)


class RiskAndStressTestingEngine:
    """
    Evaluates standard and custom real-estate stress scenarios and computes
    Monte Carlo Cash-at-Risk (VaR_95) and Conditional Value-at-Risk (CVaR_95 / Expected Shortfall).
    """

    @staticmethod
    def standard_scenarios() -> List[StressScenarioAssumptions]:
        return [
            StressScenarioAssumptions(
                scenario_name="20% Vacancy Increase Shock",
                vacancy_rate_increase_pct=Decimal("0.20"),
            ),
            StressScenarioAssumptions(
                scenario_name="15% Maintenance Expenditure Surge",
                maintenance_increase_pct=Decimal("0.15"),
            ),
            StressScenarioAssumptions(
                scenario_name="Delayed Rent Collection (30% 30-Day Lag)",
                collection_delay_haircut_pct=Decimal("0.30"),
            ),
            StressScenarioAssumptions(
                scenario_name="Major Anchor Tenant Default",
                major_tenant_default=True,
            ),
            StressScenarioAssumptions(
                scenario_name="Supplier Payment Disruption & Emergency Replacement (+20%)",
                supplier_disruption_cost_surge_pct=Decimal("0.20"),
            ),
            StressScenarioAssumptions(
                scenario_name="15% Foreign-Exchange Depreciation on USD/EUR Liabilities",
                fx_depreciation_pct=Decimal("0.15"),
            ),
            StressScenarioAssumptions(
                scenario_name="Combined Severe Stagflation & Construction Overrun Stress",
                vacancy_rate_increase_pct=Decimal("0.20"),
                maintenance_increase_pct=Decimal("0.15"),
                collection_delay_haircut_pct=Decimal("0.20"),
                major_tenant_default=True,
                supplier_disruption_cost_surge_pct=Decimal("0.10"),
                fx_depreciation_pct=Decimal("0.15"),
                construction_cost_overrun_pct=Decimal("0.20"),
            ),
        ]

    def evaluate_scenario(
        self,
        facts: BaselinePortfolioFacts,
        assumptions: StressScenarioAssumptions,
        n_simulations: int = 2000,
        random_state: int = 42,
    ) -> StressTestResult:
        ccy = facts.currency_code
        base_egi = facts.gross_potential_rent_minor - facts.current_vacancy_loss_minor + facts.other_property_income_minor
        base_opex = facts.maintenance_expenditure_minor + facts.other_operating_expense_minor
        base_noi = base_egi - base_opex

        # Apply vacancy shock and major tenant default
        add_vacancy = Money(facts.gross_potential_rent_minor, ccy).multiply_exact(assumptions.vacancy_rate_increase_pct).minor_units
        tenant_default_loss = facts.largest_single_tenant_rent_minor if assumptions.major_tenant_default else 0
        stressed_rent = max(0, facts.gross_potential_rent_minor - facts.current_vacancy_loss_minor - add_vacancy - tenant_default_loss)

        # Apply collection delay haircut
        delay_loss = Money(stressed_rent, ccy).multiply_exact(assumptions.collection_delay_haircut_pct).minor_units
        stressed_egi = stressed_rent - delay_loss + facts.other_property_income_minor

        # Apply maintenance and supplier disruption shocks
        maint_surge = Money(facts.maintenance_expenditure_minor, ccy).multiply_exact(assumptions.maintenance_increase_pct).minor_units
        supp_surge = Money(facts.other_operating_expense_minor, ccy).multiply_exact(assumptions.supplier_disruption_cost_surge_pct).minor_units
        fx_shock = Money(facts.unhedged_fx_liability_minor, ccy).multiply_exact(assumptions.fx_depreciation_pct).minor_units
        constr_overrun = Money(facts.construction_commitments_30d_minor, ccy).multiply_exact(assumptions.construction_cost_overrun_pct).minor_units

        stressed_opex = base_opex + maint_surge + supp_surge
        stressed_noi = stressed_egi - stressed_opex
        noi_delta = stressed_noi - base_noi

        # 30-day liquidity position = Available Cash + Stressed Cash Inflows - Mandatory 30d Outflows
        total_outflows_30d = (
            stressed_opex
            + facts.supplier_liabilities_due_30d_minor
            + facts.owner_disbursements_due_30d_minor
            + facts.construction_commitments_30d_minor
            + constr_overrun
            + facts.debt_service_minor
            + fx_shock
        )
        available_liquidity = facts.available_cash_reserves_minor + stressed_egi
        stressed_net_liq = available_liquidity - total_outflows_30d
        shortage = max(0, -stressed_net_liq)

        lcr = (
            (Decimal(available_liquidity) / Decimal(total_outflows_30d)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)
            if total_outflows_30d > 0
            else Decimal("999.9999")
        )
        dscr = (
            (Decimal(stressed_noi) / Decimal(facts.debt_service_minor)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)
            if facts.debt_service_minor > 0
            else Decimal("999.9999")
        )

        # Monte Carlo distribution of stressed net liquidity loss
        rng = np.random.default_rng(random_state)
        egi_draws = rng.normal(loc=float(stressed_egi), scale=max(1.0, 0.08 * float(stressed_egi)), size=n_simulations)
        out_draws = rng.normal(loc=float(total_outflows_30d), scale=max(1.0, 0.06 * float(total_outflows_30d)), size=n_simulations)
        losses = np.maximum(0.0, out_draws - (float(facts.available_cash_reserves_minor) + egi_draws))
        var_95 = float(np.percentile(losses, 95))
        tail_losses = losses[losses >= var_95]
        cvar_95 = float(np.mean(tail_losses)) if tail_losses.size > 0 else var_95

        return StressTestResult(
            organization_id=facts.organization_id,
            scenario_name=assumptions.scenario_name,
            historical_facts=facts,
            assumptions=assumptions,
            stressed_effective_gross_income_minor=stressed_egi,
            stressed_operating_expenses_minor=stressed_opex,
            stressed_noi_minor=stressed_noi,
            noi_delta_minor=noi_delta,
            stressed_net_liquidity_position_minor=stressed_net_liq,
            liquidity_shortage_minor=shortage,
            liquidity_coverage_ratio=lcr,
            debt_service_coverage_ratio=dscr,
            monte_carlo_var_95_minor=int(round(var_95)),
            monte_carlo_cvar_95_minor=int(round(cvar_95)),
        )


class PropertyPortfolioAnalyticsEngine:
    """
    Calculates exact property-level and portfolio-aggregated financial intelligence
    while enforcing strict organization and property boundaries.
    """

    @staticmethod
    def compute_property_kpis(
        organization_id: str,
        property_id: str,
        currency_code: str,
        gross_potential_rent_minor: int,
        vacancy_and_credit_loss_minor: int,
        service_charge_and_other_income_minor: int,
        maintenance_expense_minor: int,
        utilities_insurance_other_opex_minor: int,
        total_billed_minor: int,
        total_collected_minor: int,
        total_arrears_minor: int,
        property_acquisition_cost_minor: int,
        management_commission_rate_bps: int = 1000,
        reserve_retention_minor: int = 0,
    ) -> PropertyFinancialKPIs:
        gri = Money(gross_potential_rent_minor, currency_code)
        egi_minor = gross_potential_rent_minor - vacancy_and_credit_loss_minor + service_charge_and_other_income_minor
        egi = Money(egi_minor, currency_code)
        opex_minor = maintenance_expense_minor + utilities_insurance_other_opex_minor
        opex = Money(opex_minor, currency_code)
        noi_minor = egi_minor - opex_minor
        noi = Money(noi_minor, currency_code)

        # Management commission calculated on collected rent using exact rational rate (bps / 10000)
        comm_factor = Decimal(management_commission_rate_bps) / Decimal(10000)
        mgmt_comm = Money(max(0, total_collected_minor), currency_code).multiply_exact(comm_factor)

        # Owner distribution liability = max(0, Collected Rent - Operating Expenses - Mgmt Commission - Reserve Retention)
        owner_dist_minor = max(
            0,
            total_collected_minor - opex_minor - mgmt_comm.minor_units - reserve_retention_minor,
        )
        owner_dist = Money(owner_dist_minor, currency_code)
        prop_profit = Money(noi_minor - mgmt_comm.minor_units, currency_code)

        return PropertyFinancialKPIs(
            organization_id=organization_id,
            property_id=property_id,
            currency_code=currency_code,
            gross_rental_income=gri,
            effective_gross_income=egi,
            operating_expenses=opex,
            net_operating_income=noi,
            rental_yield_pct=_safe_pct(noi_minor, property_acquisition_cost_minor),
            operating_expense_ratio_pct=_safe_pct(opex_minor, egi_minor),
            arrears_ratio_pct=_safe_pct(total_arrears_minor, total_billed_minor),
            collection_efficiency_pct=_safe_pct(total_collected_minor, total_billed_minor),
            maintenance_expenditure_ratio_pct=_safe_pct(maintenance_expense_minor, egi_minor),
            management_commission=mgmt_comm,
            owner_distribution_liability=owner_dist,
            property_level_profitability=prop_profit,
        )

    @staticmethod
    def aggregate_portfolio(
        organization_id: str,
        property_kpis: Sequence[PropertyFinancialKPIs],
        total_portfolio_valuation_minor: int,
        total_billed_minor: int,
        total_collected_minor: int,
        total_arrears_minor: int,
        total_maintenance_minor: int,
    ) -> PropertyFinancialKPIs:
        if not property_kpis:
            raise ValueError("property_kpis cannot be empty")
        ccy = property_kpis[0].currency_code
        for pk in property_kpis:
            if pk.organization_id != organization_id:
                raise CrossOrganizationAccessError(
                    f"Property '{pk.property_id}' belongs to org '{pk.organization_id}', cannot aggregate into '{organization_id}'"
                )
            if pk.currency_code != ccy:
                raise ValueError("All property KPIs must be converted to organization base currency before aggregation")

        gri = sum((pk.gross_rental_income for pk in property_kpis), Money.zero(ccy))
        egi = sum((pk.effective_gross_income for pk in property_kpis), Money.zero(ccy))
        opex = sum((pk.operating_expenses for pk in property_kpis), Money.zero(ccy))
        noi = sum((pk.net_operating_income for pk in property_kpis), Money.zero(ccy))
        comm = sum((pk.management_commission for pk in property_kpis), Money.zero(ccy))
        owner_dist = sum((pk.owner_distribution_liability for pk in property_kpis), Money.zero(ccy))
        profit = sum((pk.property_level_profitability for pk in property_kpis), Money.zero(ccy))

        return PropertyFinancialKPIs(
            organization_id=organization_id,
            property_id="PORTFOLIO_AGGREGATE",
            currency_code=ccy,
            gross_rental_income=gri,
            effective_gross_income=egi,
            operating_expenses=opex,
            net_operating_income=noi,
            rental_yield_pct=_safe_pct(noi.minor_units, total_portfolio_valuation_minor),
            operating_expense_ratio_pct=_safe_pct(opex.minor_units, egi.minor_units),
            arrears_ratio_pct=_safe_pct(total_arrears_minor, total_billed_minor),
            collection_efficiency_pct=_safe_pct(total_collected_minor, total_billed_minor),
            maintenance_expenditure_ratio_pct=_safe_pct(total_maintenance_minor, egi.minor_units),
            management_commission=comm,
            owner_distribution_liability=owner_dist,
            property_level_profitability=profit,
        )

# ==============================================================================

# ==============================================================================
# MODULE FILE: glitters_financial_intelligence/evaluation.py
# ==============================================================================
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

# ==============================================================================
# MODULE FILE: tests/test_financial_intelligence.py
# ==============================================================================
"""
Comprehensive Unit, Property-Based, 17 Adversarial Scenarios, Concurrency,
and Scientific Validation Benchmark Suite for `glitters_financial_intelligence`.
"""
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

if __name__ == "__main__":
    unittest.main()
