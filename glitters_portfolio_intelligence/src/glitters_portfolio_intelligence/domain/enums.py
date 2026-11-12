from __future__ import annotations

from enum import Enum


class AssetCategory(str, Enum):
    RESIDENTIAL = "RESIDENTIAL"
    COMMERCIAL_OFFICE = "COMMERCIAL_OFFICE"
    RETAIL = "RETAIL"
    LIGHT_INDUSTRIAL = "LIGHT_INDUSTRIAL"
    LAND_DEVELOPMENT = "LAND_DEVELOPMENT"
    MIXED_USE = "MIXED_USE"


class GeographicSubmarket(str, Enum):
    NAIROBI_CBD = "NAIROBI_CBD"
    WESTLANDS = "WESTLANDS"
    KILIMANI = "KILIMANI"
    KAREN = "KAREN"
    UPPER_HILL = "UPPER_HILL"
    MOMBASA_NYALI = "MOMBASA_NYALI"
    KIAMBU_RUIRU = "KIAMBU_RUIRU"
    MACHAKOS_ATHI_RIVER = "MACHAKOS_ATHI_RIVER"
    KISUMU_CENTRAL = "KISUMU_CENTRAL"
    OTHER_KENYA = "OTHER_KENYA"


class DecisionAction(str, Enum):
    BUY = "BUY"
    HOLD = "HOLD"
    SELL = "SELL"
    RENOVATE = "RENOVATE"
    REFINANCE = "REFINANCE"


class OwnershipRepresentation(str, Enum):
    """Explicit semantic model for investment decisions."""
    FRACTIONAL_CAPITAL_WEIGHT = "FRACTIONAL_CAPITAL_WEIGHT"  # Continuous weights x_i in [0, 1]
    BINARY_WHOLE_ASSET = "BINARY_WHOLE_ASSET"                # Binary indicator z_i in {0, 1}
    DISCRETE_INVESTMENT_LOTS = "DISCRETE_INVESTMENT_LOTS"    # Integer multiples of lot size


class OptimizationObjective(str, Enum):
    MINIMIZE_CVAR_TARGET_RETURN = "MINIMIZE_CVAR_TARGET_RETURN"
    MAXIMIZE_RETURN_CVAR_BUDGET = "MAXIMIZE_RETURN_CVAR_BUDGET"
    SCALARIZED_RISK_RETURN = "SCALARIZED_RISK_RETURN"
    EPSILON_CONSTRAINT = "EPSILON_CONSTRAINT"


class SolverBackendType(str, Enum):
    HIGHS_SCIPY = "HIGHS_SCIPY"
    HIGHS_ORTOOLS = "HIGHS_ORTOOLS"
    SCIP_ORTOOLS = "SCIP_ORTOOLS"
    CBC_ORTOOLS = "CBC_ORTOOLS"
    GLOP_ORTOOLS = "GLOP_ORTOOLS"


class OptimizationStatus(str, Enum):
    """Strict mathematical solver termination status."""
    OPTIMAL = "OPTIMAL"
    FEASIBLE = "FEASIBLE"
    INFEASIBLE = "INFEASIBLE"
    UNBOUNDED = "UNBOUNDED"
    TIME_LIMIT = "TIME_LIMIT"
    UNKNOWN = "UNKNOWN"


class ScenarioProvenanceKind(str, Enum):
    """Epistemic classification of scenario data origin."""
    HISTORICAL_OBSERVATION = "HISTORICAL_OBSERVATION"
    STATISTICAL_FORECAST = "STATISTICAL_FORECAST"
    EXPERT_ASSUMPTION = "EXPERT_ASSUMPTION"
    SYNTHETIC_SCENARIO = "SYNTHETIC_SCENARIO"


class IRRStatus(str, Enum):
    """Diagnostic status for multi-period IRR numerical root finding."""
    CONVERGED = "CONVERGED"
    NO_REAL_ROOT = "NO_REAL_ROOT"
    MULTIPLE_ROOTS_DETECTED = "MULTIPLE_ROOTS_DETECTED"
    ALL_POSITIVE_CASHFLOWS = "ALL_POSITIVE_CASHFLOWS"
    ALL_NEGATIVE_CASHFLOWS = "ALL_NEGATIVE_CASHFLOWS"
