"""
Integer Minor-Unit Monetary Representation (KES Cents) for GLITTERS Financial Precision.
Eliminates floating-point accumulation errors in BOQ, procurement, and contract obligations.
"""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP


@dataclass(frozen=True, order=True)
class MoneyKES:
    """
    Immutable monetary value stored as exact integer Kenyan Cents (1 KES = 100 cents).
    """
    cents: int

    def __post_init__(self) -> None:
        if not isinstance(self.cents, int):
            object.__setattr__(self, "cents", int(round(self.cents)))

    @classmethod
    def from_kes(cls, amount_kes: float | int | str | Decimal) -> MoneyKES:
        dec = Decimal(str(amount_kes)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return cls(cents=int(dec * 100))

    def to_kes(self) -> float:
        return round(self.cents / 100.0, 2)

    def to_decimal_kes(self) -> Decimal:
        return (Decimal(self.cents) / Decimal(100)).quantize(Decimal("0.01"))

    def __add__(self, other: MoneyKES) -> MoneyKES:
        if not isinstance(other, MoneyKES):
            return NotImplemented
        return MoneyKES(self.cents + other.cents)

    def __sub__(self, other: MoneyKES) -> MoneyKES:
        if not isinstance(other, MoneyKES):
            return NotImplemented
        return MoneyKES(self.cents - other.cents)

    def multiply_quantity(self, factor: float | Decimal) -> MoneyKES:
        dec = (Decimal(self.cents) * Decimal(str(factor))).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return MoneyKES(int(dec))
