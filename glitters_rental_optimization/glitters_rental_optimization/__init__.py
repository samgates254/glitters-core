from __future__ import annotations
"""GLITTERS Rental Pricing & Revenue Optimization Engine (`glitters_rental_optimization` v2.0).

Phase 1 Mathematical Integrity & Implementation Conformance release.
"""

from .demand.models import DemandCurveEvaluation, RentalDemandModel
from .elasticity.estimator import HedonicElasticityEstimator, MarketValuationResult
from .evaluation.backtesting import (
    BacktestValidationReport,
    TimeBasedBacktester,
    generate_synthetic_kenyan_portfolio,
)
from .kenyan_market import (
    KENYAN_MONTHLY_SEASONALITY,
    KENYAN_SEGMENT_PRIORS,
    DataQualityReport,
    KenyanMarketDataSanitizer,
    SanitizedComparable,
    validate_unit_specification_plausibility,
)
from .optimization.dynamic_pricing import (
    DynamicPriceTrajectory,
    DynamicPricingEngine,
    MPCStateVector,
)
from .portfolio.optimizer import (
    PortfolioConstraintVerificationReport,
    PortfolioMetricsSummary,
    PortfolioOptimizationResult,
    PortfolioRevenueOptimizer,
)
from .pricing.single_property import SinglePropertyPricingOptimizer
from .renewals.models import (
    RenewalPricingEngine,
    RenewalPricingRecommendation,
    RenewalStrategyEvaluation,
)
from .schemas import (
    CONFIDENCE_TIER_PRECEDENCE_RANK,
    FORBIDDEN_PROTECTED_ATTRIBUTES,
    CausalIdentificationStatus,
    ConfidenceTier,
    LeaseStatus,
    ListingLifecycleStatus,
    MarketComparableListing,
    OutcomeFairnessAuditReport,
    OwnerPricingPolicy,
    PricingStatus,
    PropertySegment,
    RentalMonetaryLedger,
    UnitPricingRecommendation,
    UnitSpecification,
    audit_portfolio_pricing_fairness,
    classify_worst_applicable_confidence_tier,
    parse_optional_tri_state_bool,
    validate_no_protected_attributes,
    verify_transaction_evidence,
)
from .uncertainty.models import UncertaintyRiskEngine, UncertaintySimulationResult
from .vacancy.models import VacancyDecisionAnalysis, VacancyOptimizationEngine

__all__ = [
    "BacktestValidationReport",
    "CONFIDENCE_TIER_PRECEDENCE_RANK",
    "CausalIdentificationStatus",
    "ConfidenceTier",
    "DataQualityReport",
    "DemandCurveEvaluation",
    "DynamicPriceTrajectory",
    "DynamicPricingEngine",
    "FORBIDDEN_PROTECTED_ATTRIBUTES",
    "HedonicElasticityEstimator",
    "KENYAN_MONTHLY_SEASONALITY",
    "KENYAN_SEGMENT_PRIORS",
    "KenyanMarketDataSanitizer",
    "LeaseStatus",
    "ListingLifecycleStatus",
    "MPCStateVector",
    "MarketComparableListing",
    "MarketValuationResult",
    "OutcomeFairnessAuditReport",
    "OwnerPricingPolicy",
    "PortfolioConstraintVerificationReport",
    "PortfolioMetricsSummary",
    "PortfolioOptimizationResult",
    "PortfolioRevenueOptimizer",
    "PricingStatus",
    "PropertySegment",
    "RenewalPricingEngine",
    "RenewalPricingRecommendation",
    "RenewalStrategyEvaluation",
    "RentalDemandModel",
    "RentalMonetaryLedger",
    "SanitizedComparable",
    "SinglePropertyPricingOptimizer",
    "TimeBasedBacktester",
    "UncertaintyRiskEngine",
    "UncertaintySimulationResult",
    "UnitPricingRecommendation",
    "UnitSpecification",
    "VacancyDecisionAnalysis",
    "VacancyOptimizationEngine",
    "audit_portfolio_pricing_fairness",
    "classify_worst_applicable_confidence_tier",
    "generate_synthetic_kenyan_portfolio",
    "parse_optional_tri_state_bool",
    "validate_no_protected_attributes",
    "validate_unit_specification_plausibility",
    "verify_transaction_evidence",
]

__version__ = "2.0.0"


# ======================================================================================
