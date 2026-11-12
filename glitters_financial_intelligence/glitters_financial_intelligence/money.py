from __future__ import annotations
from .currencies import CurrencySpec, get_currency, CURRENCY_REGISTRY
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
