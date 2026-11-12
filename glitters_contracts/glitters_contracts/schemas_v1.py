"""
GLITTERS Engine 09: Versioned Schemas and Cross-Engine Contracts.
Integrates with glitters_contracts for CORE, Engine 01, Engine 02, Engine 03, and Engine 04.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ContractVersion(str, Enum):
    V1_0 = "1.0.0"
    V1_1 = "1.1.0"


class TransactionType(str, Enum):
    RENTAL_LONG_TERM = "RENTAL_LONG_TERM"
    SHORT_STAY = "SHORT_STAY"
    RESIDENTIAL_SALE = "RESIDENTIAL_SALE"
    COMMERCIAL_LEASE = "COMMERCIAL_LEASE"
    COMMERCIAL_SALE = "COMMERCIAL_SALE"
    LAND_SALE = "LAND_SALE"
    DEVELOPMENT_JV = "DEVELOPMENT_JV"


class PropertyCategory(str, Enum):
    APARTMENT = "APARTMENT"
    TOWNHOUSE = "TOWNHOUSE"
    VILLA = "VILLA"
    STUDIO = "STUDIO"
    OFFICE_SUITE = "OFFICE_SUITE"
    RETAIL_UNIT = "RETAIL_UNIT"
    WAREHOUSE_INDUSTRIAL = "WAREHOUSE_INDUSTRIAL"
    SERVICED_APARTMENT = "SERVICED_APARTMENT"
    RAW_LAND = "RAW_LAND"
    MIXED_USE_PLOT = "MIXED_USE_PLOT"


class AvailabilityStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    AVAILABLE_SOON = "AVAILABLE_SOON"
    UNDER_OFFER = "UNDER_OFFER"
    ALLOCATED = "ALLOCATED"
    UNAVAILABLE = "UNAVAILABLE"


class PropertyCondition(str, Enum):
    NEW_BUILD = "NEW_BUILD"
    EXCELLENT = "EXCELLENT"
    GOOD = "GOOD"
    NEEDS_RENOVATION = "NEEDS_RENOVATION"
    SHELL_AND_CORE = "SHELL_AND_CORE"
    VACANT_LAND = "VACANT_LAND"


class PreferenceTier(str, Enum):
    HARD = "HARD"
    STRONG = "STRONG"
    SOFT = "SOFT"
    OPTIONAL = "OPTIONAL"


class SearchIntent(str, Enum):
    RESIDENTIAL_HOME = "RESIDENTIAL_HOME"
    RENTAL_DISCOVERY = "RENTAL_DISCOVERY"
    COMMERCIAL_OPERATIONS = "COMMERCIAL_OPERATIONS"
    INVESTMENT_YIELD = "INVESTMENT_YIELD"
    SHORT_STAY_BOOKING = "SHORT_STAY_BOOKING"
    CORPORATE_RELOCATION = "CORPORATE_RELOCATION"
    LAND_DEVELOPMENT = "LAND_DEVELOPMENT"


class FeedbackActionType(str, Enum):
    VIEWED = "VIEWED"
    SAVED = "SAVED"
    SHORTLISTED = "SHORTLISTED"
    CONTACTED = "CONTACTED"
    REJECTED = "REJECTED"
    NOT_RELEVANT = "NOT_RELEVANT"
    PREFERENCE_UPDATED = "PREFERENCE_UPDATED"


# ============================================================================
# CROSS-ENGINE ENRICHMENT CONTRACTS (ENGINES 02, 03, 04, AND ENGINE 01 HANDOFF)
# ============================================================================

class Engine03GeoFeatures(BaseModel):
    """Contract provided by GLITTERS Engine 03 (Geospatial & Transport Intelligence)."""
    model_config = ConfigDict(frozen=True)

    transit_access_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    nearest_arterial_road_km: Optional[float] = Field(default=None, ge=0.0)
    commute_minutes_to_cbd: Optional[float] = Field(default=None, ge=0.0)
    flood_risk_index: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    ambient_noise_db: Optional[float] = Field(default=None, ge=0.0)
    green_canopy_ratio: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    security_patrol_coverage: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class Engine02ValuationEstimate(BaseModel):
    """Contract provided by GLITTERS Engine 02 (Automated Valuation & Appraisal)."""
    model_config = ConfigDict(frozen=True)

    estimated_fair_value_kes: Optional[float] = Field(default=None, gt=0.0)
    valuation_confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    projected_5yr_cagr: Optional[float] = Field(default=None, ge=-0.5, le=1.0)


class Engine04RentalPricing(BaseModel):
    """Contract provided by GLITTERS Engine 04 (Rental Pricing & Yield Intelligence)."""
    model_config = ConfigDict(frozen=True)

    estimated_monthly_rent_kes: Optional[float] = Field(default=None, gt=0.0)
    estimated_occupancy_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    fair_nightly_adr_kes: Optional[float] = Field(default=None, gt=0.0)


class Engine01CandidateHandoffItem(BaseModel):
    """
    Output handoff record from Engine 09 to Engine 01 (Constrained Allocation).
    Engine 09 never allocates inventory; it passes calibrated utilities to Engine 01.
    """
    model_config = ConfigDict(frozen=True)

    applicant_id: str
    tenant_org_id: str
    property_id: str
    calibrated_utility: float = Field(ge=0.0, le=1.0)
    confidence_score: float = Field(ge=0.0, le=1.0)
    hard_constraints_verified: bool


# ============================================================================
# DELIVERABLE 2: PROPERTY FEATURE SCHEMA (MISSINGNESS-AWARE)
# ============================================================================

class GeoPoint(BaseModel):
    model_config = ConfigDict(frozen=True)
    lat: float = Field(ge=-90.0, le=90.0)
    lon: float = Field(ge=-180.0, le=180.0)


class LeaseConditions(BaseModel):
    model_config = ConfigDict(frozen=True)
    min_lease_months: Optional[int] = Field(default=None, ge=0)
    max_lease_months: Optional[int] = Field(default=None, ge=1)
    deposit_months: Optional[float] = Field(default=None, ge=0.0)
    pets_allowed: Optional[bool] = None
    subletting_allowed: Optional[bool] = None
    commercial_use_permitted: Optional[bool] = None


class PropertyFeatureVector(BaseModel):
    """
    Authoritative Property Feature Representation supplied by CORE and enriched by Engines 02-04.
    Explicitly preserves None for unobserved attributes (never defaults missing numbers to 0).
    """
    model_config = ConfigDict(frozen=True)

    property_id: str
    canonical_building_unit_hash: Optional[str] = None
    core_snapshot_id: str
    tenant_scope_id: str = "PUBLIC_MARKETPLACE"

    # Location & Classification
    coordinates: GeoPoint
    zone_id: str
    neighborhood_name: str
    city: str
    property_category: PropertyCategory
    transaction_type: TransactionType
    title_description: str = ""

    # Commercial & Pricing Facts (Authoritative from CORE)
    price_kes: float = Field(gt=0.0)
    service_charge_monthly_kes: Optional[float] = Field(default=None, ge=0.0)
    availability_status: AvailabilityStatus
    available_from: Optional[date] = None

    # Physical Attributes (Optional = Explicitly Missing if None)
    bedrooms: Optional[int] = Field(default=None, ge=0)
    bathrooms: Optional[float] = Field(default=None, ge=0.0)
    floor_area_sqm: Optional[float] = Field(default=None, gt=0.0)
    land_area_acres: Optional[float] = Field(default=None, gt=0.0)
    max_occupancy_persons: Optional[int] = Field(default=None, ge=1)
    parking_bays: Optional[int] = Field(default=None, ge=0)

    # Amenities, Accessibility & Condition
    amenities: Optional[Set[str]] = None
    accessibility_features: Optional[Set[str]] = None
    condition: Optional[PropertyCondition] = None
    lease_conditions: Optional[LeaseConditions] = None
    zoning_code: Optional[str] = None

    # Trust, Verification & Freshness Metadata
    verified_listing: bool = False
    listing_quality_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    listed_at: datetime
    last_verified_at: datetime
    historical_interaction_count: int = Field(default=0, ge=0)

    # Cross-Engine Enrichments
    geo_features: Optional[Engine03GeoFeatures] = None
    valuation_estimate: Optional[Engine02ValuationEstimate] = None
    rental_pricing: Optional[Engine04RentalPricing] = None

    @property
    def effective_cost_kes(self) -> float:
        if self.transaction_type in {
            TransactionType.RENTAL_LONG_TERM,
            TransactionType.COMMERCIAL_LEASE,
        }:
            return self.price_kes + (self.service_charge_monthly_kes or 0.0)
        return self.price_kes

    def observability_mask(self) -> Dict[str, bool]:
        """Returns explicit boolean observability state for every non-mandatory feature."""
        return {
            "bedrooms": self.bedrooms is not None,
            "bathrooms": self.bathrooms is not None,
            "floor_area_sqm": self.floor_area_sqm is not None,
            "land_area_acres": self.land_area_acres is not None,
            "max_occupancy_persons": self.max_occupancy_persons is not None,
            "parking_bays": self.parking_bays is not None,
            "amenities": self.amenities is not None,
            "accessibility_features": self.accessibility_features is not None,
            "condition": self.condition is not None,
            "lease_conditions": self.lease_conditions is not None,
            "listing_quality_score": self.listing_quality_score is not None,
            "geo_features": self.geo_features is not None,
            "valuation_estimate": self.valuation_estimate is not None,
            "rental_pricing": self.rental_pricing is not None,
        }


# ============================================================================
# DELIVERABLE 3: USER PREFERENCE & CONTEXT SCHEMA
# ============================================================================

class CriterionPreference(BaseModel):
    """Wraps a specific requirement with its explicit tier and relative importance weight."""
    model_config = ConfigDict(frozen=True)

    tier: PreferenceTier = PreferenceTier.STRONG
    weight: float = Field(default=1.0, gt=0.0, le=10.0)


class BudgetPreference(BaseModel):
    model_config = ConfigDict(frozen=True)

    min_budget_kes: Optional[float] = Field(default=None, ge=0.0)
    target_budget_kes: float = Field(gt=0.0)
    max_budget_kes: float = Field(gt=0.0)
    is_hard_cap: bool = True
    weight: float = Field(default=2.0, gt=0.0, le=10.0)

    @model_validator(mode="after")
    def validate_budget_order(self) -> "BudgetPreference":
        if self.target_budget_kes > self.max_budget_kes:
            raise ValueError("target_budget_kes cannot exceed max_budget_kes")
        if self.min_budget_kes is not None and self.min_budget_kes > self.target_budget_kes:
            raise ValueError("min_budget_kes cannot exceed target_budget_kes")
        return self


class LocationPreference(BaseModel):
    model_config = ConfigDict(frozen=True)

    preferred_zones: Set[str] = Field(default_factory=set)
    anchor_coordinates: Optional[GeoPoint] = None
    ideal_radius_km: float = Field(default=3.0, gt=0.0)
    max_radius_km: Optional[float] = Field(default=None, gt=0.0)
    is_hard_radius: bool = False
    max_commute_minutes_cbd: Optional[float] = Field(default=None, gt=0.0)
    tier: PreferenceTier = PreferenceTier.STRONG
    weight: float = Field(default=1.8, gt=0.0, le=10.0)


class SpacePreference(BaseModel):
    model_config = ConfigDict(frozen=True)

    min_bedrooms: Optional[int] = Field(default=None, ge=0)
    bedrooms_tier: PreferenceTier = PreferenceTier.HARD
    min_bathrooms: Optional[float] = Field(default=None, ge=0.0)
    bathrooms_tier: PreferenceTier = PreferenceTier.SOFT
    min_floor_area_sqm: Optional[float] = Field(default=None, gt=0.0)
    floor_area_tier: PreferenceTier = PreferenceTier.STRONG
    min_land_area_acres: Optional[float] = Field(default=None, gt=0.0)
    land_area_tier: PreferenceTier = PreferenceTier.STRONG
    min_occupancy_persons: Optional[int] = Field(default=None, ge=1)
    occupancy_tier: PreferenceTier = PreferenceTier.HARD
    min_parking_bays: Optional[int] = Field(default=None, ge=0)
    parking_tier: PreferenceTier = PreferenceTier.SOFT
    weight: float = Field(default=1.5, gt=0.0, le=10.0)


class InvestmentObjective(BaseModel):
    model_config = ConfigDict(frozen=True)

    target_gross_yield: float = Field(default=0.07, ge=0.0, le=0.50)
    min_5yr_cagr: float = Field(default=0.05, ge=0.0, le=0.50)
    require_undervalued_vs_avm: bool = False
    weight: float = Field(default=2.0, gt=0.0, le=10.0)


class UserPreferenceProfile(BaseModel):
    """
    Structured User or Organization Preference Profile.
    Strictly excludes protected demographic or inferred sensitive attributes.
    """
    model_config = ConfigDict(frozen=True)

    user_id: str
    tenant_org_id: str = "PUBLIC_MARKETPLACE"
    search_intent: SearchIntent
    transaction_type: TransactionType
    allowed_categories: Set[PropertyCategory]
    category_is_hard: bool = True

    budget: BudgetPreference
    location: LocationPreference
    space: SpacePreference = Field(default_factory=SpacePreference)

    # Amenities & Accessibility mapped to PreferenceTier
    desired_amenities: Dict[str, PreferenceTier] = Field(default_factory=dict)
    required_accessibility: Dict[str, PreferenceTier] = Field(default_factory=dict)
    acceptable_conditions: Set[PropertyCondition] = Field(default_factory=set)
    condition_tier: PreferenceTier = PreferenceTier.SOFT

    # Lease / Duration Constraints
    desired_lease_months: Optional[int] = Field(default=None, ge=1)
    latest_move_in_date: Optional[date] = None
    requires_pet_friendly: bool = False
    pet_tier: PreferenceTier = PreferenceTier.HARD
    requires_verified_listing: bool = False

    # Missingness & Personalization Controls
    strict_missing_hard_constraints: bool = False
    enable_personalization: bool = True
    diversity_lambda: float = Field(default=0.78, ge=0.0, le=1.0)
    popularity_debias_strength: float = Field(default=0.35, ge=0.0, le=1.0)
    freshness_boost_weight: float = Field(default=0.10, ge=0.0, le=0.5)
    text_query: Optional[str] = None
    investment_objective: Optional[InvestmentObjective] = None


# ============================================================================
# DELIVERABLE 7 & 8: EXPLAINABILITY & RESPONSE SCHEMAS
# ============================================================================

class ExclusionReasonCode(str, Enum):
    UNAVAILABLE_STATUS = "UNAVAILABLE_STATUS"
    MOVE_IN_DATE_TOO_LATE = "MOVE_IN_DATE_TOO_LATE"
    TRANSACTION_TYPE_MISMATCH = "TRANSACTION_TYPE_MISMATCH"
    PROPERTY_CATEGORY_MISMATCH = "PROPERTY_CATEGORY_MISMATCH"
    BUDGET_HARD_CAP_EXCEEDED = "BUDGET_HARD_CAP_EXCEEDED"
    LOCATION_OUTSIDE_HARD_RADIUS = "LOCATION_OUTSIDE_HARD_RADIUS"
    INSUFFICIENT_BEDROOMS_HARD = "INSUFFICIENT_BEDROOMS_HARD"
    INSUFFICIENT_FLOOR_AREA_HARD = "INSUFFICIENT_FLOOR_AREA_HARD"
    INSUFFICIENT_LAND_AREA_HARD = "INSUFFICIENT_LAND_AREA_HARD"
    INSUFFICIENT_OCCUPANCY_HARD = "INSUFFICIENT_OCCUPANCY_HARD"
    MISSING_HARD_AMENITY = "MISSING_HARD_AMENITY"
    MISSING_HARD_ACCESSIBILITY = "MISSING_HARD_ACCESSIBILITY"
    LEASE_DURATION_INCOMPATIBLE = "LEASE_DURATION_INCOMPATIBLE"
    PETS_PROHIBITED_HARD = "PETS_PROHIBITED_HARD"
    UNVERIFIED_LISTING_REJECTED = "UNVERIFIED_LISTING_REJECTED"
    MISSING_REQUIRED_ATTRIBUTE = "MISSING_REQUIRED_ATTRIBUTE"
    DUPLICATE_LISTING_SUPPRESSED = "DUPLICATE_LISTING_SUPPRESSED"
    USER_EXPLICITLY_REJECTED = "USER_EXPLICITLY_REJECTED"
    TENANT_SCOPE_VIOLATION = "TENANT_SCOPE_VIOLATION"


class RejectedCandidateAudit(BaseModel):
    model_config = ConfigDict(frozen=True)

    property_id: str
    exclusion_codes: List[ExclusionReasonCode]
    detailed_reasons: List[str]


class RankedPropertyRecommendation(BaseModel):
    model_config = ConfigDict(frozen=True)

    rank: int = Field(ge=1)
    property_id: str
    core_snapshot_id: str
    total_relevance_score: float = Field(ge=0.0, le=1.0)
    raw_utility_score: float = Field(ge=0.0, le=1.0)
    confidence_score: float = Field(ge=0.0, le=1.0)
    feature_contributions: Dict[str, float]
    satisfied_hard_constraints: List[str]
    preference_matches: List[str]
    preference_mismatches: List[str]
    missing_property_information: List[str]
    explanation: str
    similar_alternative_ids: List[str] = Field(default_factory=list)


class RecommendationResponseV1(BaseModel):
    model_config = ConfigDict(frozen=True)

    contract_version: ContractVersion = ContractVersion.V1_0
    request_user_id: str
    tenant_org_id: str
    generated_at: datetime
    recommendations: List[RankedPropertyRecommendation]
    excluded_candidates: List[RejectedCandidateAudit]
    engine01_handoff_payload: List[Engine01CandidateHandoffItem]
    diagnostics: Dict[str, float]
