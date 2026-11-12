"""
Domain Contracts, Evidence Taxonomies, Provenance Metadata, and Tenant Scope Definitions
for `glitters_valuation_engine` v1.1.0.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Any, FrozenSet


class PropertyCategory(str, Enum):
    RESIDENTIAL = "residential"
    COMMERCIAL = "commercial"
    INDUSTRIAL = "industrial"
    AGRICULTURAL = "agricultural"
    VACANT_LAND = "vacant_land"
    APARTMENT_CONDO = "apartment_condo"
    RENTAL = "rental"
    DEVELOPMENT_LAND = "development_land"
    INVESTMENT = "investment"
    DISTRESSED = "distressed"


class TenureType(str, Enum):
    FREEHOLD = "freehold"
    LEASEHOLD = "leasehold"
    SECTIONAL_TITLE = "sectional_title"
    COMMUNITY_CUSTOMARY = "community_customary"
    INFORMAL_UNREGISTERED = "informal_unregistered"


class TitleVerificationStatus(str, Enum):
    VERIFIED_CLEAN = "verified_clean"
    ENCUMBERED_MORTGAGE = "encumbered_mortgage"
    CAVEAT_DISPUTED = "caveat_disputed"
    UNVERIFIED_MISSING = "unverified_missing"


class PriceEvidenceType(str, Enum):
    VERIFIED_STAMP_DUTY_SALE = "verified_stamp_duty_sale"
    VERIFIED_CONTRACT_SALE = "verified_contract_sale"
    VALUER_HISTORICAL_RECORD = "valuer_historical_record"
    ASKING_LISTING_PRICE = "asking_listing_price"
    RENTAL_LEASE_CONTRACT = "rental_lease_contract"
    RENTAL_ASKING_QUOTE = "rental_asking_quote"


CAPITAL_SALE_EVIDENCE_TYPES: FrozenSet[PriceEvidenceType] = frozenset({
    PriceEvidenceType.VERIFIED_STAMP_DUTY_SALE,
    PriceEvidenceType.VERIFIED_CONTRACT_SALE,
    PriceEvidenceType.VALUER_HISTORICAL_RECORD,
    PriceEvidenceType.ASKING_LISTING_PRICE,
})

VERIFIED_CAPITAL_SALE_EVIDENCE_TYPES: FrozenSet[PriceEvidenceType] = frozenset({
    PriceEvidenceType.VERIFIED_STAMP_DUTY_SALE,
    PriceEvidenceType.VERIFIED_CONTRACT_SALE,
    PriceEvidenceType.VALUER_HISTORICAL_RECORD,
})

RENTAL_EVIDENCE_TYPES: FrozenSet[PriceEvidenceType] = frozenset({
    PriceEvidenceType.RENTAL_LEASE_CONTRACT,
    PriceEvidenceType.RENTAL_ASKING_QUOTE,
})


class RentalPeriodUnit(str, Enum):
    MONTHLY = "monthly"
    ANNUAL = "annual"
    PER_SQM_MONTHLY = "per_sqm_monthly"


class ModelTrainingScope(str, Enum):
    PUBLIC_REGISTRY_ONLY = "PUBLIC_REGISTRY_ONLY"
    SINGLE_TENANT_PRIVATE = "SINGLE_TENANT_PRIVATE"
    AUTHORIZED_POOLED = "AUTHORIZED_POOLED"


class ValuationStatus(str, Enum):
    SUCCESS_HIGH_CONFIDENCE = "SUCCESS_HIGH_CONFIDENCE"
    SUCCESS_MODERATE_CONFIDENCE = "SUCCESS_MODERATE_CONFIDENCE"
    DEGRADED_LOW_CONFIDENCE = "DEGRADED_LOW_CONFIDENCE"
    ABSTAINED_INSUFFICIENT_EVIDENCE = "ABSTAINED_INSUFFICIENT_EVIDENCE"
    REJECTED_DISPUTED_TITLE = "REJECTED_DISPUTED_TITLE"
    REJECTED_INVALID_INPUT = "REJECTED_INVALID_INPUT"
    REJECTED_UNAUTHORIZED_TENANT_SCOPE = "REJECTED_UNAUTHORIZED_TENANT_SCOPE"
    REJECTED_SYNTHETIC_PROVENANCE_VIOLATION = "REJECTED_SYNTHETIC_PROVENANCE_VIOLATION"


@dataclass(frozen=True)
class TitleVerificationMetadata:
    """
    Explicit legal title verification audit record required to substantiate
    `TitleVerificationStatus.VERIFIED_CLEAN` or `ENCUMBERED_MORTGAGE`.
    """
    registry_reference: str
    verification_source: str          # e.g. "ARDHISASA_OFFICIAL_SEARCH", "COURT_REGISTRY"
    verified_timestamp_iso: str       # ISO-8601 timestamp of search
    evidence_document_hash: str       # SHA-256 of official search certificate


@dataclass(frozen=True)
class PropertyRecord:
    """
    Canonical representation of a subject property or historical record.
    Remediation Note: `title_status` defaults to `TitleVerificationStatus.UNVERIFIED_MISSING`
    so a record constructed without explicit legal verification never silently appears clean.
    """
    property_id: str
    tenant_org_id: str
    category: PropertyCategory
    county: str
    sub_county: str
    locality: str
    latitude: float
    longitude: float
    land_area_sqm: float
    building_area_sqm: float = 0.0
    bedrooms: int = 0
    bathrooms: int = 0
    building_age_years: float = 0.0
    condition_score: float = 0.75          # [0.0, 1.0]
    construction_quality: float = 0.75     # [0.0, 1.0]
    accessibility_index: float = 0.70      # [0.0, 1.0]
    infrastructure_score: float = 0.70     # [0.0, 1.0]
    utilities_score: float = 0.75          # [0.0, 1.0]
    neighbourhood_tier: int = 2            # 1=Prime, 2=Upper-mid, 3=Mid, 4=Peri-urban
    zoning_code: str = "RES_MID"
    permitted_ground_coverage: float = 0.35
    permitted_plot_ratio: float = 1.50
    max_floors: int = 4
    tenure_type: TenureType = TenureType.FREEHOLD
    unexpired_lease_years: float = 99.0
    title_status: TitleVerificationStatus = TitleVerificationStatus.UNVERIFIED_MISSING
    title_verification_metadata: Optional[TitleVerificationMetadata] = None
    monthly_gross_rent_kes: Optional[float] = None
    vacancy_rate: float = 0.08
    operating_expense_ratio: float = 0.20
    is_distressed_sale: bool = False
    forced_sale_discount_cap: float = 0.25
    is_synthetic: bool = False


@dataclass(frozen=True)
class MarketTransaction:
    """
    Historical capital sale, asking listing, or rental observation with explicit
    evidence typing and rental period metadata.
    """
    transaction_id: str
    tenant_org_id: str
    property_record: PropertyRecord
    observed_price_kes: float
    evidence_type: PriceEvidenceType
    transaction_timestamp_days: float      # Days relative to valuation epoch (e.g. 0 = current)
    reliability_weight: float = 1.0        # [0.0, 1.0]
    rental_period_unit: RentalPeriodUnit = RentalPeriodUnit.MONTHLY
    is_synthetic: bool = False


@dataclass(frozen=True)
class ModelProvenance:
    """Immutable provenance record attached to trained model state and every output contract."""
    model_version: str
    training_scope: ModelTrainingScope
    authorized_tenant_ids: Tuple[str, ...]
    trained_on_synthetic_data: bool
    training_dataset_sha256: str
    num_sale_training_records: int
    num_rental_reference_records: int
    num_calibration_records: int
    is_conformal_calibrated: bool
    calibration_method: str
    min_training_timestamp_days: float
    max_training_timestamp_days: float


@dataclass
class SubModelEstimate:
    """Output of an individual valuation approach."""
    approach_name: str
    point_estimate_kes: float
    std_error_kes: float
    applicable_weight_prior: float
    diagnostics: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ValuationOutputContract:
    """
    Production Integration Contract returned by the GLITTERS Valuation Engine.
    Includes full model provenance, tenant scope verification, calibration status,
    and statutory non-certification disclaimers.
    """
    property_id: str
    tenant_org_id: str
    status: ValuationStatus
    currency: str
    point_estimate_kes: Optional[float]
    lower_bound_kes: Optional[float]
    upper_bound_kes: Optional[float]
    confidence_level: float
    is_conformal_calibrated: bool
    calibration_method: str
    aleatoric_uncertainty_kes: Optional[float]
    epistemic_uncertainty_kes: Optional[float]
    comparable_quality_score: float
    valuation_reliability_score: float
    sub_model_estimates: Dict[str, float]
    ensemble_weights: Dict[str, float]
    feature_attributions_kes: Dict[str, float]
    selected_comparables: List[Dict[str, Any]]
    validation_flags: List[str]
    adversarial_alerts: List[str]
    model_version: str
    model_provenance: Optional[ModelProvenance]
    is_synthetic_evidence: bool
    statutory_disclaimer: str = (
        "AUTOMATED QUANTITATIVE ESTIMATE ONLY: This output is generated by the GLITTERS "
        "algorithmic valuation engine and does NOT constitute a statutory valuation report "
        "certified by a Registered and Practising Valuer under the Valuers Act (Cap 532, Laws of Kenya) "
        "or the Institution of Surveyors of Kenya (ISK) Valuation Standards (2021). "
        "Model predictions never override official Ministry of Lands (Ardhisasa) title verification."
    )
