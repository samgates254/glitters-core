from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN
import math
from typing import Sequence


@dataclass(frozen=True, slots=True)
class Money:
    """
    Exact monetary representation using integer minor units (e.g. KES cents).
    Strictly forbids fractional cents in accounting obligations.
    """
    cents: int
    currency: str = "KES"

    def __post_init__(self) -> None:
        if not isinstance(self.cents, int):
            raise TypeError(f"cents must be an exact integer, got {type(self.cents).__name__}: {self.cents}")

    @classmethod
    def from_major(cls, major_amount: float | Decimal | int, currency: str = "KES") -> Money:
        """Controlled boundary conversion from major units (e.g. KES) to integer minor units (cents)."""
        dec = Decimal(str(major_amount))
        cents = int((dec * Decimal(100)).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN))
        return cls(cents=cents, currency=currency)

    @property
    def major_amount(self) -> Decimal:
        return (Decimal(self.cents) / Decimal(100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)

    def __add__(self, other: Money) -> Money:
        if not isinstance(other, Money) or other.currency != self.currency:
            raise TypeError(f"Cannot add {other} to {self}")
        return Money(cents=self.cents + other.cents, currency=self.currency)

    def __sub__(self, other: Money) -> Money:
        if not isinstance(other, Money) or other.currency != self.currency:
            raise TypeError(f"Cannot subtract {other} from {self}")
        return Money(cents=self.cents - other.cents, currency=self.currency)

    def __mul__(self, scalar: int | float | Decimal) -> Money:
        if isinstance(scalar, int):
            return Money(cents=self.cents * scalar, currency=self.currency)
        dec_scalar = Decimal(str(scalar))
        new_cents = int((Decimal(self.cents) * dec_scalar).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN))
        return Money(cents=new_cents, currency=self.currency)

    def __rmul__(self, scalar: int | float | Decimal) -> Money:
        return self.__mul__(scalar)

    def __neg__(self) -> Money:
        return Money(cents=-self.cents, currency=self.currency)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return False
        return self.cents == other.cents and self.currency == other.currency

    def __lt__(self, other: Money) -> bool:
        if not isinstance(other, Money) or other.currency != self.currency:
            raise TypeError(f"Cannot compare {self} with {other}")
        return self.cents < other.cents

    def __le__(self, other: Money) -> bool:
        return self == other or self < other

    def __gt__(self, other: Money) -> bool:
        return not (self <= other)

    def __ge__(self, other: Money) -> bool:
        return not (self < other)

    def __repr__(self) -> str:
        sign = "-" if self.cents < 0 else ""
        abs_c = abs(self.cents)
        major = abs_c // 100
        minor = abs_c % 100
        return f"{self.currency} {sign}{major:,}.{minor:02d}"


def allocate_cents(total_cents: int, weights: Sequence[float]) -> list[int]:
    """
    Allocates an exact integer monetary amount according to floating-point weights
    using the Hare-Niemeyer (Largest Remainder) method.

    Guarantees:
      1. sum(result) == total_cents exactly (zero leakage).
      2. Each allocation is an exact integer.
      3. Non-negative weights yield non-negative allocations for total_cents >= 0.
    """
    if len(weights) == 0:
        return []
    total_w = sum(weights)
    if total_w <= 0.0:
        raise ValueError(f"Sum of weights must be positive, got {total_w}")

    exact_values = [total_cents * (w / total_w) for w in weights]
    integer_parts = [math.floor(val) for val in exact_values]
    remainders = [val - floor_val for val, floor_val in zip(exact_values, integer_parts)]

    allocated_so_far = sum(integer_parts)
    shortage = total_cents - allocated_so_far

    # Sort indices by remainder descending
    sorted_indices = sorted(range(len(weights)), key=lambda i: remainders[i], reverse=True)

    result = list(integer_parts)
    for k in range(shortage):
        result[sorted_indices[k]] += 1

    assert sum(result) == total_cents, f"Allocation error: sum={sum(result)} != target={total_cents}"
    return result
