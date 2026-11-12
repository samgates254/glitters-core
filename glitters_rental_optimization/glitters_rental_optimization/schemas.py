from __future__ import annotations
"""Core domain schemas, strict monetary taxonomy, adapter hardening & anti-discrimination guardrails.

Phase 1 Audit Remediation (v2.0):
1. Standardized enum `PricingStatus.CONFLICTING_OWNER_POLICY` across spec, guide, and code.
2. Standardized deterministic lattice ordering `classify_worst_applicable_confidence_tier()`
   with `N_eff < 3.0` as the single abstention threshold.
3. Hardened input parsing (`parse_optional_tri_state_bool`, `verify_transaction_evidence`)
   so string `"False"` never evaluates to `True`, missing attributes remain explicitly `None`
   (`UNKNOWN`), and transaction verification requires cryptographic/ledger evidence records
   rather than caller-supplied booleans.
4. Added competing-risks survival censoring state (`ListingLifecycleStatus`) and `unit_identifier`
   to `MarketComparableListing` so distinct units in the same building are never falsely deduplicated.
5. Added outcome-level fairness audit (`OutcomeFairnessAuditReport`, `audit_portfolio_pricing_fairness`)
   to detect geographic/income proxy discrimination.
"""


from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence
import numpy as np


class PropertySegment(str, Enum):
    """Supported Kenyan property segments."""

    RESIDENTIAL_APARTMENT = "residential_apartment"
    STANDALONE_HOUSE = "standalone_house"
    BEDSITTER = "bedsitter"
    STUDIO = "studio"
    COMMERCIAL_BUILDING = "commercial_building"
    RETAIL_SPACE = "retail_space"
    OFFICE = "office"
    INDUSTRIAL_PROPERTY = "industrial_property"
    WAREHOUSE = "warehouse"
    HOSPITALITY_SHORT_STAY = "hospitality_short_stay"
    MIXED_USE_DEVELOPMENT = "mixed_use_development"
    STUDENT_ACCOMMODATION = "student_accommodation"


class LeaseStatus(str, Enum):
    """Current occupancy and contractual state of a unit."""

    VACANT = "vacant"
    OCCUPIED_ACTIVE = "occupied_active"
    EXPIRING_RENEWAL_WINDOW = "expiring_renewal_window"
    UNDER_MAINTENANCE = "under_maintenance"


class ListingLifecycleStatus(str, Enum):
    """Competing-risks survival status for market comparable listings."""

    VERIFIED_LEASE_EXECUTED = "verified_lease_executed"  # delta = 1 (exact clearing event)
    VERIFIED_WITHDRAWN_UNLEASED = "verified_withdrawn_unleased"  # delta = 2 (right-censored)
    ACTIVE_OR_UNVERIFIED_PORTAL_AD = "active_or_unverified_portal_ad"  # delta = 0 (latent competing risk)


class ConfidenceTier(str, Enum):
    """Epistemic and statistical support classification for pricing recommendations."""

    OUT_OF_DISTRIBUTION = "out_of_distribution"
    INSUFFICIENT_DATA_SAFE_FALLBACK = "insufficient_data_safe_fallback"
    WEAKLY_SUPPORTED = "weakly_supported"
    MODERATE_CONFIDENCE = "moderate_confidence"
    HIGH_CONFIDENCE = "high_confidence"


CONFIDENCE_TIER_PRECEDENCE_RANK: dict[ConfidenceTier, int] = {
    ConfidenceTier.OUT_OF_DISTRIBUTION: 0,
    ConfidenceTier.INSUFFICIENT_DATA_SAFE_FALLBACK: 1,
    ConfidenceTier.WEAKLY_SUPPORTED: 2,
    ConfidenceTier.MODERATE_CONFIDENCE: 3,
    ConfidenceTier.HIGH_CONFIDENCE: 4,
}


def classify_worst_applicable_confidence_tier(
    effective_sample_size: float,
    mahalanobis_distance: float,
    relative_pi_width: float,
    area_out_of_bounds: bool = False,
) -> ConfidenceTier:
    """Deterministically assigns the WORST (minimum rank) applicable confidence tier.

    Reconciles any overlap across sample size, feature distance, and interval width:
    - OUT_OF_DISTRIBUTION: area_out_of_bounds or d_M > 5.0
    - INSUFFICIENT_DATA_SAFE_FALLBACK: N_eff < 3.0
    - WEAKLY_SUPPORTED: 3.0 <= N_eff < 8.0 or 3.8 < d_M <= 5.0 or W_PI > 0.35
    - MODERATE_CONFIDENCE: 8.0 <= N_eff < 20.0 or 2.8 < d_M <= 3.8 or 0.20 < W_PI <= 0.35
    - HIGH_CONFIDENCE: N_eff >= 20.0 and d_M <= 2.8 and W_PI <= 0.20
    """
    if area_out_of_bounds or mahalanobis_distance > 5.0:
        return ConfidenceTier.OUT_OF_DISTRIBUTION
    if effective_sample_size < 3.0:
        return ConfidenceTier.INSUFFICIENT_DATA_SAFE_FALLBACK

    # Evaluate each dimension independently and take the worst (lowest rank) tier
    if effective_sample_size < 8.0:
        sample_tier = ConfidenceTier.WEAKLY_SUPPORTED
    elif effective_sample_size < 20.0:
        sample_tier = ConfidenceTier.MODERATE_CONFIDENCE
    else:
        sample_tier = ConfidenceTier.HIGH_CONFIDENCE

    if mahalanobis_distance > 3.8:
        dist_tier = ConfidenceTier.WEAKLY_SUPPORTED
    elif mahalanobis_distance > 2.8:
        dist_tier = ConfidenceTier.MODERATE_CONFIDENCE
    else:
        dist_tier = ConfidenceTier.HIGH_CONFIDENCE

    if relative_pi_width > 0.35:
        width_tier = ConfidenceTier.WEAKLY_SUPPORTED
    elif relative_pi_width > 0.20:
        width_tier = ConfidenceTier.MODERATE_CONFIDENCE
    else:
        width_tier = ConfidenceTier.HIGH_CONFIDENCE

    candidates = (sample_tier, dist_tier, width_tier)
    return min(candidates, key=lambda t: CONFIDENCE_TIER_PRECEDENCE_RANK[t])


class PricingStatus(str, Enum):
    """Operational status returned by the optimization engine."""

    OPTIMAL = "optimal"
    LOCKED_CONTRACTUAL = "locked_contractual"
    RENEWAL_WORKFLOW_REQUIRED = "renewal_workflow_required"
    CONSTRAINED_BY_OWNER_POLICY = "constrained_by_owner_policy"
    CONFLICTING_OWNER_POLICY = "conflicting_owner_policy"
    AFFORDABILITY_CAPPED = "affordability_capped"
    SAFE_FALLBACK_ABSTENTION = "safe_fallback_abstention"


class CausalIdentificationStatus(str, Enum):
    """Diagnostic status for Double Machine Learning (DML) IV price-elasticity identification."""

    IDENTIFIED_STRONG_IV = "identified_strong_iv"
    ABSTAINED_WEAK_OR_MISSING_IV = "abstained_weak_or_missing_iv"
    ABSTAINED_EXCLUSION_REJECTED = "abstained_exclusion_rejected"


# Protected personal attributes strictly forbidden in any pricing or renewal model
FORBIDDEN_PROTECTED_ATTRIBUTES: frozenset[str] = frozenset(
    {
        "gender",
        "sex",
        "race",
        "ethnicity",
        "tribe",
        "religion",
        "marital_status",
        "family_status",
        "pregnancy",
        "nationality",
        "national_origin",
        "disability",
        "hiv_status",
        "political_affiliation",
        "sexual_orientation",
        "age_group",
        "caste",
    }
)


def validate_no_protected_attributes(features: Mapping[str, Any]) -> None:
    """Raises ValueError if any protected personal attribute is present in features."""
    lower_keys = {str(k).strip().lower() for k in features.keys()}
    violations = lower_keys.intersection(FORBIDDEN_PROTECTED_ATTRIBUTES)
    if violations:
        sorted_v = ", ".join(sorted(violations))
        raise ValueError(
            f"Fair-housing & anti-discrimination violation: forbidden protected "
            f"attribute(s) detected in pricing/renewal payload: [{sorted_v}]"
        )


def parse_optional_tri_state_bool(val: Any, field_name: str = "attribute") -> bool | None:
    """Safely parses a tri-state boolean (True, False, or None for unknown).

    Never uses naive `bool(val)` which turns `"False"` or `"0"` into `True`, and
    never fabricates `True` when an attribute is missing (`None`).
    """
    if val is None:
        return None
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, np.integer)):
        if val == 1:
            return True
        if val == 0:
            return False
        raise ValueError(f"Invalid integer boolean value for {field_name}: {val}")
    if isinstance(val, str):
        norm = val.strip().lower()
        if norm in ("true", "1", "yes", "y"):
            return True
        if norm in ("false", "0", "no", "n"):
            return False
        if norm in ("", "none", "null", "unknown", "na", "n/a"):
            return None
        raise ValueError(f"Ambiguous string boolean value for {field_name}: {val!r}")
    raise TypeError(f"Unsupported type for tri-state boolean {field_name}: {type(val).__name__}")


def verify_transaction_evidence(
    lease_contract_id: str | None,
    payment_receipt_hash: str | None,
    verified_rent_kes: float | None,
) -> bool:
    """Determines whether a comparable record represents a verified lease transaction.

    Never trusts a caller-supplied boolean flag; requires non-empty contract ID,
    payment receipt/ledger hash, and positive clearing rent.
    """
    if (
        lease_contract_id is not None
        and len(str(lease_contract_id).strip()) >= 4
        and payment_receipt_hash is not None
        and len(str(payment_receipt_hash).strip()) >= 6
        and verified_rent_kes is not None
        and float(verified_rent_kes) > 0.0
    ):
        return True
    return False


@dataclass(frozen=True)
class RentalMonetaryLedger:
    """Explicitly separates the 7 distinct monthly rental monetary quantities (in KES/month)."""

    asking_rent_kes: float | None
    achievable_market_rent_kes: float
    contractual_rent_kes: float | None
    recommended_rent_kes: float
    actual_collected_rent_kes: float | None
    effective_rental_revenue_kes: float
    net_operating_income_kes: float
    currency: str = "KES"
    accounting_period: str = "MONTHLY"

    def __post_init__(self) -> None:
        if self.currency != "KES":
            raise ValueError(f"Currency must be 'KES', got '{self.currency}'.")
        if self.accounting_period != "MONTHLY":
            raise ValueError("All ledger quantities must be normalized to 'MONTHLY'.")
        if self.achievable_market_rent_kes <= 0:
            raise ValueError("achievable_market_rent_kes must be strictly positive.")
        if self.recommended_rent_kes <= 0:
            raise ValueError("recommended_rent_kes must be strictly positive.")
        if self.asking_rent_kes is not None and self.asking_rent_kes <= 0:
            raise ValueError("asking_rent_kes must be positive when provided.")
        if self.contractual_rent_kes is not None and self.contractual_rent_kes < 0:
            raise ValueError("contractual_rent_kes cannot be negative.")
        if self.actual_collected_rent_kes is not None and self.actual_collected_rent_kes < 0:
            raise ValueError("actual_collected_rent_kes cannot be negative.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "currency": self.currency,
            "accounting_period": self.accounting_period,
            "asking_rent_kes": self.asking_rent_kes,
            "achievable_market_rent_kes": round(self.achievable_market_rent_kes, 2),
            "contractual_rent_kes": self.contractual_rent_kes,
            "recommended_rent_kes": round(self.recommended_rent_kes, 2),
            "actual_collected_rent_kes": self.actual_collected_rent_kes,
            "effective_rental_revenue_kes": round(self.effective_rental_revenue_kes, 2),
            "net_operating_income_kes": round(self.net_operating_income_kes, 2),
        }


@dataclass(frozen=True)
class OwnerPricingPolicy:
    """Owner-defined and statutory pricing constraints for a unit or property."""

    min_rent_kes: float | None = None
    max_rent_kes: float | None = None
    max_annual_increase_pct: float = 0.10
    max_discount_from_asking_pct: float = 0.15
    target_min_occupancy_rate: float = 0.85
    target_min_annual_yield_pct: float = 5.0
    max_monthly_turnover_rate: float = 0.25
    allow_renewal_concessions: bool = True
    affordability_income_ratio_cap: float = 0.35

    def has_conflict(self) -> bool:
        """Returns True if owner min_rent exceeds owner max_rent."""
        if self.min_rent_kes is not None and self.max_rent_kes is not None:
            return self.min_rent_kes > self.max_rent_kes
        return False


@dataclass(frozen=True)
class UnitSpecification:
    """Complete structural, financial, and operational specification of a rental unit.

    Amenity booleans (`has_borehole_water`, `has_backup_generator`, `has_solar_water_heating`)
    support `bool | None` where `None` explicitly represents UNKNOWN (never defaulted to True).
    All recurring costs (`routine_maintenance_cost_kes`, `vacancy_holding_cost_kes`) are monthly,
    while `tenant_acquisition_cost_kes` and `turnover_refurbishment_cost_kes` are one-off per-event
    transition costs converted to monthly expected values via the DTMC transition model.
    """

    unit_id: str
    property_id: str
    landlord_id: str
    management_org_id: str
    segment: PropertySegment
    county: str
    neighbourhood: str
    floor_area_sqm: float
    bedrooms: int
    bathrooms: float
    has_borehole_water: bool | None
    has_backup_generator: bool | None
    has_solar_water_heating: bool | None
    security_tier: int  # 1 (basic) to 5 (24/7 manned + biometric + CCTV)
    parking_bays: int
    furnishing_level: int  # 0=unfurnished, 1=semi, 2=fully furnished
    lease_status: LeaseStatus
    current_asking_rent_kes: float | None = None
    contractual_rent_kes: float | None = None
    actual_collected_rent_kes: float | None = None
    months_remaining_on_lease: int = 0
    current_vacancy_days: int = 0
    typical_lease_term_months: int = 12
    routine_maintenance_cost_kes: float = 2500.0  # Monthly recurring (KES/mo)
    vacancy_holding_cost_kes: float = 1800.0  # Monthly recurring while vacant (KES/mo)
    tenant_acquisition_cost_kes: float = 15000.0  # One-off per new lease execution (KES/event)
    turnover_refurbishment_cost_kes: float = 25000.0  # One-off per tenant departure (KES/event)
    management_fee_pct: float = 0.08
    statutory_tax_rate_pct: float = 0.0  # Optional Kenyan MRI tax rate if modelled net-of-tax
    baseline_arrears_rate: float = 0.03
    target_tenant_median_income_kes: float | None = None
    asset_valuation_kes: float | None = None
    owner_policy: OwnerPricingPolicy = field(default_factory=OwnerPricingPolicy)
    extra_metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_no_protected_attributes(self.extra_metadata)
        if self.floor_area_sqm <= 0:
            raise ValueError(f"Unit {self.unit_id}: floor_area_sqm must be > 0.")
        if not (1 <= self.security_tier <= 5):
            raise ValueError(f"Unit {self.unit_id}: security_tier must be in 1..5.")


@dataclass(frozen=True)
class MarketComparableListing:
    """External or internal market comparable record with competing-risks survival metadata."""

    listing_id: str
    segment: PropertySegment
    county: str
    neighbourhood: str
    floor_area_sqm: float
    bedrooms: int
    asking_rent_kes: float
    verified_transaction_rent_kes: float | None
    listing_age_days: int
    is_verified_transaction: bool
    has_borehole_water: bool | None = None
    has_backup_generator: bool | None = None
    security_tier: int = 3
    source_portal: str = "portal_scrape"
    property_id: str | None = None
    unit_identifier: str | None = None  # Preserves distinct physical units in the same building
    lifecycle_status: ListingLifecycleStatus = (
        ListingLifecycleStatus.ACTIVE_OR_UNVERIFIED_PORTAL_AD
    )
    lease_contract_id: str | None = None
    payment_receipt_hash: str | None = None
    latitude: float | None = None
    longitude: float | None = None


@dataclass(frozen=True)
class UnitPricingRecommendation:
    """Comprehensive output of the single-unit or portfolio pricing engine.

    Never mutates an active lease directly; includes `effective_date_iso` and
    `requires_owner_approval=True` for GLITTERS CORE workflow enforcement.
    """

    unit_id: str
    property_id: str
    segment: PropertySegment
    county: str
    neighbourhood: str
    status: PricingStatus
    confidence_tier: ConfidenceTier
    ledger: RentalMonetaryLedger
    expected_application_arrival_rate: float
    expected_conversion_probability: float
    weibull_scale_eta_months: float
    instantaneous_hazard_monthly: float
    expected_vacancy_duration_months: float
    expected_occupancy_duration_months: float
    steady_state_occupancy_prob: float
    finite_horizon_occupancy_prob: float
    expected_renewal_probability: float
    price_elasticity: float
    causal_identification_status: CausalIdentificationStatus
    rent_prediction_interval_80: tuple[float, float]
    noi_cvar_10_kes: float
    vacancy_cut_justified: bool
    shadow_price_owner_floor_kes: float
    requires_owner_approval: bool = True
    effective_date_offset_months: int = 0
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OutcomeFairnessAuditReport:
    """Outcome-level fairness and proxy-discrimination audit across neighbourhood income tiers."""

    passed_outcome_fairness: bool
    low_income_tier_mean_markup_ratio: float
    high_income_tier_mean_markup_ratio: float
    markup_disparity_difference: float
    max_allowed_markup_disparity: float
    disparate_impact_ratio: float
    min_required_disparate_impact_ratio: float
    summary_message: str


def audit_portfolio_pricing_fairness(
    units: Sequence[UnitSpecification],
    recommendations: Sequence[UnitPricingRecommendation],
    max_allowed_markup_disparity: float = 0.05,
    min_disparate_impact_ratio: float = 0.80,
) -> OutcomeFairnessAuditReport:
    """Evaluates outcome-level pricing fairness across socio-economic neighbourhood tiers.

    Checks that lower-income neighbourhoods/segments are not subjected to higher relative
    markups over achievable market rent (`recommended_rent / achievable_market_rent`) than
    higher-income neighbourhoods.
    """
    if not units or not recommendations:
        return OutcomeFairnessAuditReport(
            passed_outcome_fairness=True,
            low_income_tier_mean_markup_ratio=1.0,
            high_income_tier_mean_markup_ratio=1.0,
            markup_disparity_difference=0.0,
            max_allowed_markup_disparity=max_allowed_markup_disparity,
            disparate_impact_ratio=1.0,
            min_required_disparate_impact_ratio=min_disparate_impact_ratio,
            summary_message="Empty portfolio; fairness audit trivially satisfied.",
        )

    incomes = [
        u.target_tenant_median_income_kes or (r.ledger.achievable_market_rent_kes * 3.2)
        for u, r in zip(units, recommendations)
    ]
    median_inc = float(np.median(incomes))

    low_markups = []
    high_markups = []
    for inc, r in zip(incomes, recommendations):
        if r.status == PricingStatus.LOCKED_CONTRACTUAL:
            continue
        ratio = r.ledger.recommended_rent_kes / max(r.ledger.achievable_market_rent_kes, 1.0)
        if inc <= median_inc:
            low_markups.append(ratio)
        else:
            high_markups.append(ratio)

    mean_low = float(np.mean(low_markups)) if low_markups else 1.0
    mean_high = float(np.mean(high_markups)) if high_markups else 1.0

    # Positive difference means low-income tier is charged a higher relative markup than high-income tier
    disparity_diff = max(0.0, mean_low - mean_high)
    # Disparate impact ratio of favorable (at-or-below market) pricing
    favorable_low = (
        float(np.mean([m <= 1.03 for m in low_markups])) if low_markups else 1.0
    )
    favorable_high = (
        float(np.mean([m <= 1.03 for m in high_markups])) if high_markups else 1.0
    )
    di_ratio = (
        min(1.25, favorable_low / max(favorable_high, 0.05))
        if favorable_high > 0
        else 1.0
    )

    passed = (disparity_diff <= max_allowed_markup_disparity) and (
        di_ratio >= min_disparate_impact_ratio
    )
    msg = (
        f"Outcome fairness passed: low-income markup={mean_low:.4f}, high-income markup={mean_high:.4f} "
        f"(disparity={disparity_diff:.4f} <= {max_allowed_markup_disparity:.2f}, DI ratio={di_ratio:.3f})."
        if passed
        else f"Outcome fairness warning: disparity={disparity_diff:.4f} or DI ratio={di_ratio:.3f} outside bounds."
    )
    return OutcomeFairnessAuditReport(
        passed_outcome_fairness=passed,
        low_income_tier_mean_markup_ratio=round(mean_low, 4),
        high_income_tier_mean_markup_ratio=round(mean_high, 4),
        markup_disparity_difference=round(disparity_diff, 4),
        max_allowed_markup_disparity=max_allowed_markup_disparity,
        disparate_impact_ratio=round(di_ratio, 4),
        min_required_disparate_impact_ratio=min_disparate_impact_ratio,
        summary_message=msg,
    )


# ======================================================================================
