from __future__ import annotations
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
