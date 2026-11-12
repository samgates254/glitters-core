"""
Dimensionally Validated Unit Conversion Engine.
Remediates Section 4 Audit Finding:
- Removes invalid `("m3", "cm"): 1.0` mapping.
- Enforces dimensional homogeneity (LENGTH, AREA, VOLUME, MASS) before applying scale factors.
"""
from __future__ import annotations
from typing import Dict, Tuple

# Map each supported unit to (PhysicalDimension, factor_to_SI_base_unit)
# SI Base units: LENGTH -> m, AREA -> m2, VOLUME -> m3, MASS -> kg
UNIT_DIMENSIONS: Dict[str, Tuple[str, float]] = {
    # Length (base: m)
    "mm": ("LENGTH", 1e-3),
    "cm": ("LENGTH", 1e-2),
    "m": ("LENGTH", 1.0),
    "km": ("LENGTH", 1e3),
    # Area (base: m2)
    "mm2": ("AREA", 1e-6),
    "cm2": ("AREA", 1e-4),
    "m2": ("AREA", 1.0),
    "sm": ("AREA", 1.0),
    "ha": ("AREA", 1e4),
    # Volume (base: m3)
    "mm3": ("VOLUME", 1e-9),
    "cm3": ("VOLUME", 1e-6),
    "litres": ("VOLUME", 1e-3),
    "m3": ("VOLUME", 1.0),
    "cm_vol": ("VOLUME", 1.0),  # Explicit cubic-metre alias only
    # Mass (base: kg)
    "g": ("MASS", 1e-3),
    "kg": ("MASS", 1.0),
    "bags_50kg": ("MASS", 50.0),
    "tonnes": ("MASS", 1e3),
}


def convert_units_dimensionally(value: float, from_unit: str, to_unit: str) -> float:
    if from_unit not in UNIT_DIMENSIONS:
        raise ValueError(f"Unknown or unsupported source unit: '{from_unit}'")
    if to_unit not in UNIT_DIMENSIONS:
        raise ValueError(f"Unknown or unsupported target unit: '{to_unit}'")
    dim_from, factor_from = UNIT_DIMENSIONS[from_unit]
    dim_to, factor_to = UNIT_DIMENSIONS[to_unit]
    if dim_from != dim_to:
        raise ValueError(
            f"Dimensional mismatch: Cannot convert '{from_unit}' ({dim_from}) to '{to_unit}' ({dim_to})."
        )
    if from_unit == to_unit:
        return float(value)
    base_value = float(value) * factor_from
    return base_value / factor_to
