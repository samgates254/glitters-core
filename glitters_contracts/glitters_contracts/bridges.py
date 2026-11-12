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
