"""
Input/Output Contracts and Immutable Domain Models for GLITTERS Allocation Engine.

All financial values are denominated in integer minor currency units (e.g., Kenyan Cents:
1 KES = 100 cents) to eliminate IEEE-754 floating-point drift across architectures.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping

from .exceptions import MaliciousInputError


class PropertyCategory(str, Enum):
    RESIDENTIAL_AFFORDABLE = "RESIDENTIAL_AFFORDABLE"
    RESIDENTIAL_MARKET = "RESIDENTIAL_MARKET"
    RESIDENTIAL_EXECUTIVE = "RESIDENTIAL_EXECUTIVE"
    COMMERCIAL_RETAIL = "COMMERCIAL_RETAIL"
    COMMERCIAL_OFFICE = "COMMERCIAL_OFFICE"
    INDUSTRIAL_WAREHOUSE = "INDUSTRIAL_WAREHOUSE"
    MIXED_USE = "MIXED_USE"


class SolverRegime(str, Enum):
    AUTO = "AUTO"
    MIN_COST_MAX_FLOW = "MIN_COST_MAX_FLOW"
    HUNGARIAN_ASSIGNMENT = "HUNGARIAN_ASSIGNMENT"
    MILP_HIGHS = "MILP_HIGHS"
    CPSAT_EXACT = "CPSAT_EXACT"
    LEXICOGRAPHIC_CPSAT = "LEXICOGRAPHIC_CPSAT"
    LAGRANGIAN_DECOMPOSITION = "LAGRANGIAN_DECOMPOSITION"


class AllocationStatus(str, Enum):
    OPTIMAL = "OPTIMAL"
    FEASIBLE_BOUNDED = "FEASIBLE_BOUNDED"
    INFEASIBLE = "INFEASIBLE"
    EMPTY_FEASIBLE = "EMPTY_FEASIBLE"
    REJECTED_POLICY_CHANGE = "REJECTED_POLICY_CHANGE"
    REJECTED_STALE_SNAPSHOT = "REJECTED_STALE_SNAPSHOT"


MAX_SAFE_FINANCIAL_CENTS: int = 10_000_000_000_000  # 100 Billion KES in cents


def _validate_identifier(val: str, field_name: str) -> None:
    if not isinstance(val, str) or not val.strip():
        raise MaliciousInputError(f"Field '{field_name}' must be a non-empty string.")
    if len(val) > 256 or "\x00" in val:
        raise MaliciousInputError(f"Field '{field_name}' exceeds length limit or contains null bytes.")


def _validate_non_negative_int(val: int, field_name: str, max_val: int = MAX_SAFE_FINANCIAL_CENTS) -> None:
    if isinstance(val, bool) or not isinstance(val, int):
        raise MaliciousInputError(f"Field '{field_name}' must be an exact integer, got {type(val).__name__}.")
    if val < 0 or val > max_val:
        raise MaliciousInputError(
            f"Field '{field_name}'={val} out of valid bounds [0, {max_val}]."
        )


def _validate_float_coord(val: float, field_name: str, low: float, high: float) -> None:
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        raise MaliciousInputError(f"Field '{field_name}' must be numeric.")
    if math.isnan(val) or math.isinf(val) or val < low or val > high:
        raise MaliciousInputError(
            f"Field '{field_name}'={val} is NaN, Inf, or outside geographic bounds [{low}, {high}]."
        )


def _quantize_float_micro(val: float) -> int:
    """Quantizes float to integer micro-units (1e-6 precision) for canonical hashing."""
    return int(round(float(val) * 1_000_000.0))


@dataclass(frozen=True)
class PropertyUnit:
    """Represents a concrete rentable/allocatable property unit or multi-slot asset."""

    unit_id: str
    property_id: str
    organization_id: str
    category: PropertyCategory
    zone_code: str
    latitude: float
    longitude: float
    monthly_rent_cents: int
    security_deposit_cents: int
    service_charge_cents: int
    min_credit_score: int
    min_income_multiple_bps: int  # Basis points: 30000 = 3.0x monthly rent required income
    capacity_slots: int = 1  # Number of identical co-tenancy/parcel slots (usually 1)
    bedrooms: int = 1
    floor_area_sqm: int = 50
    available_from_day: int = 0  # Epoch day index (e.g. days since 2026-01-01)
    available_until_day: int = 3650
    min_lease_days: int = 180
    max_lease_days: int = 1825
    accessibility_features: frozenset[str] = field(default_factory=frozenset)
    amenities: frozenset[str] = field(default_factory=frozenset)
    required_eligibility_tags: frozenset[str] = field(default_factory=frozenset)
    excluded_conflict_groups: frozenset[str] = field(default_factory=frozenset)
    owner_priority_tier: int = 1  # 1..5

    def __post_init__(self) -> None:
        _validate_identifier(self.unit_id, "unit_id")
        _validate_identifier(self.property_id, "property_id")
        _validate_identifier(self.organization_id, "organization_id")
        if not isinstance(self.category, PropertyCategory):
            raise MaliciousInputError("Field 'category' must be a valid PropertyCategory enum.")
        _validate_identifier(self.zone_code, "zone_code")
        _validate_float_coord(self.latitude, "latitude", -90.0, 90.0)
        _validate_float_coord(self.longitude, "longitude", -180.0, 180.0)
        _validate_non_negative_int(self.monthly_rent_cents, "monthly_rent_cents")
        _validate_non_negative_int(self.security_deposit_cents, "security_deposit_cents")
        _validate_non_negative_int(self.service_charge_cents, "service_charge_cents")
        _validate_non_negative_int(self.min_credit_score, "min_credit_score", 1000)
        _validate_non_negative_int(self.min_income_multiple_bps, "min_income_multiple_bps", 100_000)
        _validate_non_negative_int(self.capacity_slots, "capacity_slots", 10_000)
        if self.capacity_slots < 1:
            raise MaliciousInputError("capacity_slots must be >= 1.")
        _validate_non_negative_int(self.bedrooms, "bedrooms", 100)
        _validate_non_negative_int(self.floor_area_sqm, "floor_area_sqm", 1_000_000)
        _validate_non_negative_int(self.available_from_day, "available_from_day", 365_000)
        _validate_non_negative_int(self.available_until_day, "available_until_day", 365_000)
        if self.available_until_day < self.available_from_day:
            raise MaliciousInputError(
                f"Unit {self.unit_id}: available_until_day ({self.available_until_day}) < available_from_day ({self.available_from_day})."
            )
        _validate_non_negative_int(self.min_lease_days, "min_lease_days", 365_000)
        _validate_non_negative_int(self.max_lease_days, "max_lease_days", 365_000)
        if self.max_lease_days < self.min_lease_days:
            raise MaliciousInputError(f"Unit {self.unit_id}: max_lease_days < min_lease_days.")
        _validate_non_negative_int(self.owner_priority_tier, "owner_priority_tier", 10)
        if self.owner_priority_tier < 1:
            raise MaliciousInputError("owner_priority_tier must be >= 1.")

        object.__setattr__(self, "accessibility_features", frozenset(self.accessibility_features))
        object.__setattr__(self, "amenities", frozenset(self.amenities))
        object.__setattr__(self, "required_eligibility_tags", frozenset(self.required_eligibility_tags))
        object.__setattr__(self, "excluded_conflict_groups", frozenset(self.excluded_conflict_groups))

    @property
    def total_monthly_cost_cents(self) -> int:
        return self.monthly_rent_cents + self.service_charge_cents

    def canonical_tuple(self) -> tuple[Any, ...]:
        """Includes EVERY field of PropertyUnit so any attribute mutation changes the snapshot hash."""
        return (
            self.unit_id,
            self.property_id,
            self.organization_id,
            self.category.value,
            self.zone_code,
            _quantize_float_micro(self.latitude),
            _quantize_float_micro(self.longitude),
            self.monthly_rent_cents,
            self.security_deposit_cents,
            self.service_charge_cents,
            self.min_credit_score,
            self.min_income_multiple_bps,
            self.capacity_slots,
            self.bedrooms,
            self.floor_area_sqm,
            self.available_from_day,
            self.available_until_day,
            self.min_lease_days,
            self.max_lease_days,
            sorted(self.accessibility_features),
            sorted(self.amenities),
            sorted(self.required_eligibility_tags),
            sorted(self.excluded_conflict_groups),
            self.owner_priority_tier,
        )


@dataclass(frozen=True)
class ApplicantProfile:
    """Represents an applicant, tenant, buyer, investor, or corporate lessee."""

    applicant_id: str
    organization_id: str
    allowed_categories: frozenset[PropertyCategory]
    max_monthly_budget_cents: int
    max_upfront_capital_cents: int
    verified_monthly_income_cents: int
    credit_score: int
    preferred_latitude: float
    preferred_longitude: float
    max_travel_distance_km: float
    min_bedrooms: int = 0
    min_floor_area_sqm: int = 1
    desired_move_in_day: int = 0
    latest_move_in_day: int = 3650
    desired_lease_days: int = 365
    required_accessibility: frozenset[str] = field(default_factory=frozenset)
    preferred_amenities: frozenset[str] = field(default_factory=frozenset)
    preferred_zones: frozenset[str] = field(default_factory=frozenset)
    eligibility_tags: frozenset[str] = field(default_factory=frozenset)
    conflict_group_id: str | None = None
    urgency_level: int = 1  # 1..10
    vulnerability_priority: int = 0  # 0..10
    demographic_group: str = "UNSPECIFIED"
    explicit_unit_preferences: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_identifier(self.applicant_id, "applicant_id")
        _validate_identifier(self.organization_id, "organization_id")
        _validate_non_negative_int(self.max_monthly_budget_cents, "max_monthly_budget_cents")
        _validate_non_negative_int(self.max_upfront_capital_cents, "max_upfront_capital_cents")
        _validate_non_negative_int(self.verified_monthly_income_cents, "verified_monthly_income_cents")
        _validate_non_negative_int(self.credit_score, "credit_score", 1000)
        _validate_float_coord(self.preferred_latitude, "preferred_latitude", -90.0, 90.0)
        _validate_float_coord(self.preferred_longitude, "preferred_longitude", -180.0, 180.0)
        _validate_float_coord(self.max_travel_distance_km, "max_travel_distance_km", 0.0, 20_000.0)
        _validate_non_negative_int(self.min_bedrooms, "min_bedrooms", 100)
        _validate_non_negative_int(self.min_floor_area_sqm, "min_floor_area_sqm", 1_000_000)
        _validate_non_negative_int(self.desired_move_in_day, "desired_move_in_day", 365_000)
        _validate_non_negative_int(self.latest_move_in_day, "latest_move_in_day", 365_000)
        if self.latest_move_in_day < self.desired_move_in_day:
            raise MaliciousInputError(
                f"Applicant {self.applicant_id}: latest_move_in_day < desired_move_in_day."
            )
        _validate_non_negative_int(self.desired_lease_days, "desired_lease_days", 365_000)
        if self.desired_lease_days < 1:
            raise MaliciousInputError(f"Applicant {self.applicant_id}: desired_lease_days must be >= 1.")
        _validate_non_negative_int(self.urgency_level, "urgency_level", 100)
        _validate_non_negative_int(self.vulnerability_priority, "vulnerability_priority", 100)
        if self.conflict_group_id is not None:
            _validate_identifier(self.conflict_group_id, "conflict_group_id")
        _validate_identifier(self.demographic_group, "demographic_group")

        clean_prefs: dict[str, int] = {}
        for uid, score in self.explicit_unit_preferences.items():
            _validate_identifier(uid, "explicit_unit_preferences.key")
            _validate_non_negative_int(score, f"explicit_unit_preferences[{uid}]", 10_000)
            clean_prefs[uid] = score

        object.__setattr__(self, "allowed_categories", frozenset(self.allowed_categories))
        object.__setattr__(self, "required_accessibility", frozenset(self.required_accessibility))
        object.__setattr__(self, "preferred_amenities", frozenset(self.preferred_amenities))
        object.__setattr__(self, "preferred_zones", frozenset(self.preferred_zones))
        object.__setattr__(self, "eligibility_tags", frozenset(self.eligibility_tags))
        object.__setattr__(self, "explicit_unit_preferences", MappingProxyType(clean_prefs))

    def canonical_tuple(self) -> tuple[Any, ...]:
        """Includes EVERY field of ApplicantProfile so any attribute mutation changes the snapshot hash."""
        return (
            self.applicant_id,
            self.organization_id,
            sorted(c.value for c in self.allowed_categories),
            self.max_monthly_budget_cents,
            self.max_upfront_capital_cents,
            self.verified_monthly_income_cents,
            self.credit_score,
            _quantize_float_micro(self.preferred_latitude),
            _quantize_float_micro(self.preferred_longitude),
            _quantize_float_micro(self.max_travel_distance_km),
            self.min_bedrooms,
            self.min_floor_area_sqm,
            self.desired_move_in_day,
            self.latest_move_in_day,
            self.desired_lease_days,
            sorted(self.required_accessibility),
            sorted(self.preferred_amenities),
            sorted(self.preferred_zones),
            sorted(self.eligibility_tags),
            self.conflict_group_id or "",
            self.urgency_level,
            self.vulnerability_priority,
            self.demographic_group,
            sorted(self.explicit_unit_preferences.items()),
        )


@dataclass(frozen=True)
class OrganizationQuota:
    """Regulatory or contractual floor/ceiling quotas per category or demographic group."""

    organization_id: str
    category: PropertyCategory | None = None
    demographic_group: str | None = None
    min_allocations: int = 0
    max_allocations: int | None = None

    def __post_init__(self) -> None:
        _validate_identifier(self.organization_id, "organization_id")
        if self.category is not None and not isinstance(self.category, PropertyCategory):
            raise MaliciousInputError("Quota category must be a PropertyCategory or None.")
        if self.demographic_group is not None:
            _validate_identifier(self.demographic_group, "demographic_group")
        _validate_non_negative_int(self.min_allocations, "min_allocations", 1_000_000)
        if self.max_allocations is not None:
            _validate_non_negative_int(self.max_allocations, "max_allocations", 1_000_000)
            if self.max_allocations < self.min_allocations:
                raise MaliciousInputError("max_allocations cannot be less than min_allocations.")

    def canonical_tuple(self) -> tuple[Any, ...]:
        return (
            self.organization_id,
            self.category.value if self.category is not None else "",
            self.demographic_group or "",
            self.min_allocations,
            self.max_allocations if self.max_allocations is not None else -1,
        )


@dataclass(frozen=True)
class ExistingAssignment:
    """Represents a prior valid assignment used for incremental reallocation & stability bounds."""

    applicant_id: str
    unit_id: str
    organization_id: str
    locked: bool = False

    def __post_init__(self) -> None:
        _validate_identifier(self.applicant_id, "applicant_id")
        _validate_identifier(self.unit_id, "unit_id")
        _validate_identifier(self.organization_id, "organization_id")
        if not isinstance(self.locked, bool):
            raise MaliciousInputError("Field 'locked' must be a boolean.")

    def canonical_tuple(self) -> tuple[Any, ...]:
        return (
            self.applicant_id,
            self.unit_id,
            self.organization_id,
            self.locked,
        )


@dataclass(frozen=True)
class AllocationPolicy:
    """
    Versioned, immutable optimization policy governing weights, constraints, and solver selection.
    All weights are integer basis points (10000 = 1.0) to guarantee exact cross-platform integer arithmetic.
    """

    policy_id: str
    policy_version: str
    organization_id: str
    allow_cross_organization_federation: bool = False
    authorized_federated_organizations: frozenset[str] = field(default_factory=frozenset)
    solver_regime: SolverRegime = SolverRegime.AUTO

    # Integer weights (0..1,000,000) for objective components
    weight_allocation_coverage: int = 50_000
    weight_preference_match: int = 15_000
    weight_affordability_fit: int = 10_000
    weight_distance_proximity: int = 10_000
    weight_urgency_priority: int = 10_000
    weight_owner_tier: int = 5_000
    penalty_reallocation_churn: int = 25_000

    # Fairness & Stability parameters
    enforce_demographic_proportionality: bool = False
    max_demographic_disparity_bps: int = 2_000
    max_reallocation_churn_count: int | None = None
    time_limit_seconds: float = 10.0
    OptimalityGapToleranceBps: int = 10

    def __post_init__(self) -> None:
        _validate_identifier(self.policy_id, "policy_id")
        _validate_identifier(self.policy_version, "policy_version")
        _validate_identifier(self.organization_id, "organization_id")
        if not isinstance(self.solver_regime, SolverRegime):
            raise MaliciousInputError("Field 'solver_regime' must be a valid SolverRegime enum.")
        _validate_non_negative_int(self.weight_allocation_coverage, "weight_allocation_coverage", 1_000_000)
        _validate_non_negative_int(self.weight_preference_match, "weight_preference_match", 1_000_000)
        _validate_non_negative_int(self.weight_affordability_fit, "weight_affordability_fit", 1_000_000)
        _validate_non_negative_int(self.weight_distance_proximity, "weight_distance_proximity", 1_000_000)
        _validate_non_negative_int(self.weight_urgency_priority, "weight_urgency_priority", 1_000_000)
        _validate_non_negative_int(self.weight_owner_tier, "weight_owner_tier", 1_000_000)
        _validate_non_negative_int(self.penalty_reallocation_churn, "penalty_reallocation_churn", 1_000_000)
        _validate_non_negative_int(self.max_demographic_disparity_bps, "max_demographic_disparity_bps", 10_000)
        if self.max_reallocation_churn_count is not None:
            _validate_non_negative_int(self.max_reallocation_churn_count, "max_reallocation_churn_count", 1_000_000)
        _validate_float_coord(self.time_limit_seconds, "time_limit_seconds", 0.05, 86_400.0)
        _validate_non_negative_int(self.OptimalityGapToleranceBps, "OptimalityGapToleranceBps", 10_000)
        object.__setattr__(
            self, "authorized_federated_organizations", frozenset(self.authorized_federated_organizations)
        )

    def canonical_hash(self) -> str:
        """
        Computes a complete SHA-256 digest over EVERY policy parameter, including
        time_limit_seconds (quantized to integer milliseconds) and OptimalityGapToleranceBps.
        """
        payload = {
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "organization_id": self.organization_id,
            "allow_cross_organization_federation": self.allow_cross_organization_federation,
            "authorized_federated_organizations": sorted(self.authorized_federated_organizations),
            "solver_regime": self.solver_regime.value,
            "weights": [
                self.weight_allocation_coverage,
                self.weight_preference_match,
                self.weight_affordability_fit,
                self.weight_distance_proximity,
                self.weight_urgency_priority,
                self.weight_owner_tier,
                self.penalty_reallocation_churn,
            ],
            "enforce_demographic_proportionality": self.enforce_demographic_proportionality,
            "max_demographic_disparity_bps": self.max_demographic_disparity_bps,
            "max_reallocation_churn_count": (
                self.max_reallocation_churn_count if self.max_reallocation_churn_count is not None else -1
            ),
            "time_limit_ms": int(round(self.time_limit_seconds * 1_000.0)),
            "optimality_gap_tolerance_bps": self.OptimalityGapToleranceBps,
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class InventorySnapshot:
    """Versioned point-in-time snapshot of units, applicants, quotas, and active assignments."""

    snapshot_id: str
    snapshot_sequence: int
    timestamp_iso: str
    units: tuple[PropertyUnit, ...]
    applicants: tuple[ApplicantProfile, ...]
    quotas: tuple[OrganizationQuota, ...] = field(default_factory=tuple)
    existing_assignments: tuple[ExistingAssignment, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        _validate_identifier(self.snapshot_id, "snapshot_id")
        _validate_non_negative_int(self.snapshot_sequence, "snapshot_sequence", 1_000_000_000)
        _validate_identifier(self.timestamp_iso, "timestamp_iso")

        object.__setattr__(self, "units", tuple(self.units))
        object.__setattr__(self, "applicants", tuple(self.applicants))
        object.__setattr__(self, "quotas", tuple(self.quotas))
        object.__setattr__(self, "existing_assignments", tuple(self.existing_assignments))

        seen_units: set[str] = set()
        for u in self.units:
            if u.unit_id in seen_units:
                raise MaliciousInputError(f"Duplicate unit_id '{u.unit_id}' in InventorySnapshot.")
            seen_units.add(u.unit_id)

        seen_apps: set[str] = set()
        for a in self.applicants:
            if a.applicant_id in seen_apps:
                raise MaliciousInputError(f"Duplicate applicant_id '{a.applicant_id}' in InventorySnapshot.")
            seen_apps.add(a.applicant_id)

    def canonical_hash(self) -> str:
        """
        Computes a complete SHA-256 digest over EVERY field of EVERY unit, applicant, quota,
        and existing assignment in the snapshot. Any change to coordinates, preferences,
        eligibility tags, accessibility features, quotas, or locked assignments strictly
        changes this digest.
        """
        unit_keys = sorted(u.canonical_tuple() for u in self.units)
        app_keys = sorted(a.canonical_tuple() for a in self.applicants)
        quota_keys = sorted(q.canonical_tuple() for q in self.quotas)
        existing_keys = sorted(ea.canonical_tuple() for ea in self.existing_assignments)

        payload = {
            "snapshot_id": self.snapshot_id,
            "snapshot_sequence": self.snapshot_sequence,
            "timestamp_iso": self.timestamp_iso,
            "units": unit_keys,
            "applicants": app_keys,
            "quotas": quota_keys,
            "existing_assignments": existing_keys,
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class AllocationBatchInput:
    """Top-level input contract to the allocation engine."""

    request_id: str
    expected_snapshot_sequence: int
    expected_policy_version: str
    snapshot: InventorySnapshot
    policy: AllocationPolicy

    def __post_init__(self) -> None:
        _validate_identifier(self.request_id, "request_id")


@dataclass(frozen=True)
class ConstraintViolation:
    """Detailed record explaining why an applicant-unit pair or global quota was infeasible."""

    constraint_code: str
    description: str
    applicant_id: str | None = None
    unit_id: str | None = None
    actual_value: Any = None
    threshold_value: Any = None


@dataclass(frozen=True)
class AllocationExplanation:
    """Human- and machine-auditable explanation for a single allocation or non-allocation decision."""

    applicant_id: str
    allocated_unit_id: str | None
    organization_id: str
    composite_score: int
    tie_break_rank: int
    score_breakdown: Mapping[str, int]
    active_hard_constraints_verified: tuple[str, ...]
    feasible_alternatives_count: int
    best_alternative_unit_id: str | None
    best_alternative_score_delta: int | None
    rejection_reasons_by_unit: Mapping[str, tuple[str, ...]]
    rationale_summary: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "score_breakdown", MappingProxyType(dict(self.score_breakdown)))
        object.__setattr__(
            self,
            "rejection_reasons_by_unit",
            MappingProxyType({k: tuple(v) for k, v in self.rejection_reasons_by_unit.items()}),
        )


@dataclass(frozen=True)
class AllocationDecision:
    """A verified, deterministic assignment of an applicant to a property unit."""

    decision_id: str
    applicant_id: str
    unit_id: str
    property_id: str
    organization_id: str
    composite_score: int
    tie_break_bonus: int
    is_retained_from_prior: bool
    explanation: AllocationExplanation


@dataclass(frozen=True)
class FairnessMetrics:
    """Quantitative fairness, dispersion, and stability metrics across the batch allocation."""

    total_applicants: int
    total_allocated: int
    overall_allocation_rate_bps: int
    jain_fairness_index_bps: int
    gini_coefficient_bps: int
    min_normalized_utility_bps: int
    demographic_allocation_rates_bps: Mapping[str, int]
    max_demographic_disparity_bps: int
    blocking_pairs_count: int
    reallocation_churn_count: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "demographic_allocation_rates_bps",
            MappingProxyType(dict(self.demographic_allocation_rates_bps)),
        )


@dataclass(frozen=True)
class AllocationBatchResult:
    """Immutable, cryptographically traceable result of an allocation run."""

    request_id: str
    status: AllocationStatus
    algorithm_version: str
    solver_regime_used: SolverRegime
    is_proven_optimal: bool
    optimality_gap_bps: int
    input_snapshot_hash: str
    policy_version_hash: str
    deterministic_result_hash: str
    execution_time_ms: float
    decisions: tuple[AllocationDecision, ...]
    unallocated_explanations: tuple[AllocationExplanation, ...]
    global_infeasibility_diagnostics: tuple[ConstraintViolation, ...]
    fairness_metrics: FairnessMetrics
