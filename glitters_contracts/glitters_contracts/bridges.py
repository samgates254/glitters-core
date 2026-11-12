from __future__ import annotations
from dataclasses import dataclass
from datetime import date, datetime
from typing import Mapping, Optional, Sequence
from uuid import UUID


@dataclass(frozen=True, slots=True)
class GeospatialValuationFeatureVector:
    """Standardized bridge DTO: Engine 3 -> Engine 2 & Engine 4."""
    parcel_id: str
    centroid_lat: float
    centroid_lon: float
    utm_zone: int
    area_sqm: float
    perimeter_m: float
    compactness_ratio: float
    dem_mean_elevation_m: float
    dem_slope_degrees: float
    flood_risk_score: float
    distance_to_major_road_m: float
    distance_to_cbd_m: float
    nearest_hospital_dist_m: float
    nearest_school_dist_m: float
    road_accessibility_15min_reach: float
    local_morans_i: float


@dataclass(frozen=True, slots=True)
class AssetValuationRentalBridge:
    """Standardized bridge DTO: Engine 2 -> Engine 4."""
    property_id: str
    asset_valuation_kes_cents: int
    valuation_confidence_tier: str
    minimum_gross_yield_bps: int
    minimum_acceptable_annual_rent_cents: int
    hedonic_quality_score: float
    is_conformal_calibrated: bool


@dataclass(frozen=True, slots=True)
class RentalClearingAllocationBridge:
    """Standardized bridge DTO: Engine 4 -> Engine 1."""
    unit_id: str
    property_id: str
    recommended_monthly_rent_cents: int
    minimum_monthly_income_required_cents: int
    optimal_occupancy_target_rate: float
    pricing_status: str


@dataclass(frozen=True, slots=True)
class ProjectMilestoneMoveInBridge:
    """Standardized bridge DTO: Engine 5 -> Engine 1."""
    project_id: str
    unit_id: str
    earliest_move_in_date: date
    latest_move_in_date: date
    milestone_completion_confidence: float
    critical_path_slack_days: int


@dataclass(frozen=True, slots=True)
class ConstructionProcurementFinancialBridge:
    """Standardized bridge DTO: Engine 5 -> Engine 6."""
    purchase_order_id: str
    vendor_account_id: str
    project_id: str
    committed_amount_cents: int
    currency: str
    item_category: str
    boraqs_certified: bool
    evidence_document_sha256: str


@dataclass(frozen=True, slots=True)
class TenantRiskHandoffBridge:
    """Standardized bridge DTO: Engine 07 -> Engine 01 & Engine 04."""
    applicant_id: str
    application_id: str
    tenant_org_id: str
    risk_score: float
    risk_tier: str
    default_probability_12m: float
    recommended_max_rent_cents: int
    recommended_deposit_months: int
    is_eligible: bool
    requires_human_review: bool
    adverse_factors: tuple[str, ...]
    scoring_timestamp_utc: datetime


@dataclass(frozen=True, slots=True)
class PortfolioInvestmentBridge:
    """Standardized bridge DTO: Engine 08 -> GLITTERS CORE & Engine 02."""
    portfolio_id: str
    tenant_org_id: str
    recommended_holdings: tuple[tuple[str, float], ...]
    expected_annual_return: float
    portfolio_cvar_95: float
    portfolio_volatility: float
    total_capital_deployed_cents: int
    rebalancing_required: bool
    requires_investment_committee_approval: bool
    generated_at_utc: datetime


@dataclass(frozen=True, slots=True)
class RecommendationCandidateHandoffBridge:
    """Standardized bridge DTO: Engine 09 -> Engine 01."""
    applicant_id: str
    property_id: str
    utility_score: float
    rank: int
    affordability_ratio: float
    preference_match_tier: str
    explanation_summary: str


@dataclass(frozen=True, slots=True)
class PredictiveMaintenanceHandoffBridge:
    """Standardized bridge DTO: Engine 10 -> Engine 05 & Engine 08."""
    asset_id: str
    component_id: str
    component_type: str
    current_health_index: float
    failure_probability_30d: float
    estimated_remaining_useful_life_days: int
    estimated_repair_cost_cents: int
    maintenance_urgency: str
    recommended_work_order_type: str
    requires_immediate_dispatch: bool
    assessed_at_utc: datetime
