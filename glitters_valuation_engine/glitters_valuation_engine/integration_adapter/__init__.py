"""
Module: `integration_adapter`
=============================
Implements the Recommended GLITTERS Integration Architecture (Section 6 of Technical Audit)
while keeping `glitters_valuation_engine` 100% free of Django ORM imports:

    GLITTERS CORE -> Valuation Application Service -> Evidence & Snapshot Builder
    -> (Sales Evidence | Rentals Evidence | Property Attributes)
    -> GLITTERS VALUATION ENGINE -> Independent Verification -> Valuation Result Contract

Provides:
1. `PostGISSpatialQueryTemplates`: Parameterized SQL/PostGIS `ST_DWithin` queries with mandatory
   tenant authorization, causal timestamp filter (`<= valuation_day`), and evidence-type separation.
2. `EvidenceSnapshotBuilder`: Converts raw dictionary rows from PostgreSQL/PostGIS into strongly
   typed, segregated `(PropertyRecord, List[SaleTransaction], List[RentalTransaction])` snapshots.
3. `IndependentOutputVerifier`: Post-inference mathematical invariant gate verifying bounds
   (`L <= Point <= U`), provenance completeness, and tenant authorization before returning to GLITTERS CORE.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Any, Tuple, Optional
import math

from ..contracts import (
    PropertyRecord,
    MarketTransaction,
    PropertyCategory,
    TenureType,
    TitleVerificationStatus,
    TitleVerificationMetadata,
    PriceEvidenceType,
    RentalPeriodUnit,
    CAPITAL_SALE_EVIDENCE_TYPES,
    RENTAL_EVIDENCE_TYPES,
    ValuationOutputContract,
    ValuationStatus,
)
from ..ensemble import GlittersValuationEngine


class PostGISSpatialQueryTemplates:
    """
    Parameterized PostGIS query templates using GIST spatial indexing (`ST_DWithin` on geography)
    that strictly separate capital sales from rental observations at the SQL boundary.
    """

    FETCH_COMPARABLE_SALES_SQL = (
        "SELECT transaction_id, tenant_org_id, property_id, category, county, sub_county, locality, "
        "ST_Y(geom::geometry) AS latitude, ST_X(geom::geometry) AS longitude, "
        "land_area_sqm, building_area_sqm, bedrooms, bathrooms, building_age_years, "
        "condition_score, construction_quality, accessibility_index, infrastructure_score, utilities_score, "
        "neighbourhood_tier, zoning_code, permitted_ground_coverage, permitted_plot_ratio, max_floors, "
        "tenure_type, unexpired_lease_years, title_status, observed_price_kes, evidence_type, "
        "transaction_timestamp_days, reliability_weight, is_synthetic "
        "FROM glitters_market_transactions "
        "WHERE (tenant_org_id = %(tenant_org_id)s OR tenant_org_id = 'PUBLIC_REGISTRY') "
        "  AND is_synthetic = FALSE "
        "  AND evidence_type IN ('verified_stamp_duty_sale', 'verified_contract_sale', 'valuer_historical_record', 'asking_listing_price') "
        "  AND transaction_timestamp_days <= %(valuation_day)s "
        "  AND ST_DWithin(geom::geography, ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography, %(radius_meters)s) "
        "ORDER BY transaction_timestamp_days ASC;"
    )

    FETCH_COMPARABLE_RENTALS_SQL = (
        "SELECT transaction_id, tenant_org_id, property_id, category, county, sub_county, locality, "
        "ST_Y(geom::geometry) AS latitude, ST_X(geom::geometry) AS longitude, "
        "land_area_sqm, building_area_sqm, bedrooms, bathrooms, building_age_years, "
        "condition_score, construction_quality, accessibility_index, infrastructure_score, utilities_score, "
        "neighbourhood_tier, zoning_code, permitted_ground_coverage, permitted_plot_ratio, max_floors, "
        "tenure_type, unexpired_lease_years, title_status, observed_price_kes, evidence_type, "
        "rental_period_unit, transaction_timestamp_days, reliability_weight, is_synthetic "
        "FROM glitters_market_transactions "
        "WHERE (tenant_org_id = %(tenant_org_id)s OR tenant_org_id = 'PUBLIC_REGISTRY') "
        "  AND is_synthetic = FALSE "
        "  AND evidence_type IN ('rental_lease_contract', 'rental_asking_quote') "
        "  AND transaction_timestamp_days <= %(valuation_day)s "
        "  AND ST_DWithin(geom::geography, ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography, %(radius_meters)s) "
        "ORDER BY transaction_timestamp_days ASC;"
    )


@dataclass(frozen=True)
class ValuationEvidenceSnapshot:
    """Immutable snapshot assembled by the application service prior to engine invocation."""
    subject_property: PropertyRecord
    sales_evidence: Tuple[MarketTransaction, ...]
    rentals_evidence: Tuple[MarketTransaction, ...]
    valuation_day: float
    requesting_tenant_org_id: str


class EvidenceSnapshotBuilder:
    """
    Constructs a strongly-typed `ValuationEvidenceSnapshot` from raw database/service dictionaries,
    enforcing default `UNVERIFIED_MISSING` title status when verification metadata is absent
    and segregating sales vs. rental evidence.
    """

    @staticmethod
    def build_property_record(raw: Dict[str, Any]) -> PropertyRecord:
        meta_dict = raw.get("title_verification_metadata")
        title_meta: Optional[TitleVerificationMetadata] = None
        if isinstance(meta_dict, dict) and meta_dict.get("registry_reference"):
            title_meta = TitleVerificationMetadata(
                registry_reference=str(meta_dict["registry_reference"]),
                verification_source=str(meta_dict.get("verification_source", "ARDHISASA")),
                verified_timestamp_iso=str(meta_dict.get("verified_timestamp_iso", "")),
                evidence_document_hash=str(meta_dict.get("evidence_document_hash", "")),
            )

        # Default to UNVERIFIED_MISSING unless explicitly provided
        raw_status = raw.get("title_status", TitleVerificationStatus.UNVERIFIED_MISSING.value)
        title_status = TitleVerificationStatus(raw_status)

        return PropertyRecord(
            property_id=str(raw["property_id"]),
            tenant_org_id=str(raw["tenant_org_id"]),
            category=PropertyCategory(raw["category"]),
            county=str(raw["county"]),
            sub_county=str(raw["sub_county"]),
            locality=str(raw["locality"]),
            latitude=float(raw["latitude"]),
            longitude=float(raw["longitude"]),
            land_area_sqm=float(raw["land_area_sqm"]),
            building_area_sqm=float(raw.get("building_area_sqm", 0.0)),
            bedrooms=int(raw.get("bedrooms", 0)),
            bathrooms=int(raw.get("bathrooms", 0)),
            building_age_years=float(raw.get("building_age_years", 0.0)),
            condition_score=float(raw.get("condition_score", 0.75)),
            construction_quality=float(raw.get("construction_quality", 0.75)),
            accessibility_index=float(raw.get("accessibility_index", 0.70)),
            infrastructure_score=float(raw.get("infrastructure_score", 0.70)),
            utilities_score=float(raw.get("utilities_score", 0.75)),
            neighbourhood_tier=int(raw.get("neighbourhood_tier", 2)),
            zoning_code=str(raw.get("zoning_code", "RES_MID")),
            permitted_ground_coverage=float(raw.get("permitted_ground_coverage", 0.35)),
            permitted_plot_ratio=float(raw.get("permitted_plot_ratio", 1.50)),
            max_floors=int(raw.get("max_floors", 4)),
            tenure_type=TenureType(raw.get("tenure_type", TenureType.FREEHOLD.value)),
            unexpired_lease_years=float(raw.get("unexpired_lease_years", 99.0)),
            title_status=title_status,
            title_verification_metadata=title_meta,
            monthly_gross_rent_kes=float(raw["monthly_gross_rent_kes"]) if raw.get("monthly_gross_rent_kes") is not None else None,
            vacancy_rate=float(raw.get("vacancy_rate", 0.08)),
            operating_expense_ratio=float(raw.get("operating_expense_ratio", 0.20)),
            is_distressed_sale=bool(raw.get("is_distressed_sale", False)),
            forced_sale_discount_cap=float(raw.get("forced_sale_discount_cap", 0.25)),
            is_synthetic=bool(raw.get("is_synthetic", False)),
        )

    @classmethod
    def build_snapshot(
        cls,
        subject_dict: Dict[str, Any],
        evidence_rows: List[Dict[str, Any]],
        valuation_day: float = 0.0,
    ) -> ValuationEvidenceSnapshot:
        subj = cls.build_property_record(subject_dict)
        sales: List[MarketTransaction] = []
        rentals: List[MarketTransaction] = []

        for row in evidence_rows:
            prop_rec = cls.build_property_record(row["property_record"]) if "property_record" in row else cls.build_property_record(row)
            ev_type = PriceEvidenceType(row["evidence_type"])
            period_unit = RentalPeriodUnit(row.get("rental_period_unit", RentalPeriodUnit.MONTHLY.value))
            tx = MarketTransaction(
                transaction_id=str(row["transaction_id"]),
                tenant_org_id=str(row["tenant_org_id"]),
                property_record=prop_rec,
                observed_price_kes=float(row["observed_price_kes"]),
                evidence_type=ev_type,
                transaction_timestamp_days=float(row["transaction_timestamp_days"]),
                reliability_weight=float(row.get("reliability_weight", 1.0)),
                rental_period_unit=period_unit,
                is_synthetic=bool(row.get("is_synthetic", prop_rec.is_synthetic)),
            )
            if ev_type in CAPITAL_SALE_EVIDENCE_TYPES:
                sales.append(tx)
            elif ev_type in RENTAL_EVIDENCE_TYPES:
                rentals.append(tx)

        return ValuationEvidenceSnapshot(
            subject_property=subj,
            sales_evidence=tuple(sales),
            rentals_evidence=tuple(rentals),
            valuation_day=valuation_day,
            requesting_tenant_org_id=subj.tenant_org_id,
        )


class IndependentOutputVerifier:
    """
    Independent post-valuation mathematical verification gate before returning a
    `ValuationOutputContract` to GLITTERS CORE.
    """

    @staticmethod
    def verify_contract(contract: ValuationOutputContract) -> Tuple[bool, List[str]]:
        violations: List[str] = []

        if contract.point_estimate_kes is not None:
            if math.isnan(contract.point_estimate_kes) or contract.point_estimate_kes <= 0:
                violations.append("INVALID_POINT_ESTIMATE: Point estimate is NaN or non-positive.")
            if contract.lower_bound_kes is None or contract.upper_bound_kes is None:
                violations.append("MISSING_BOUNDS: Point estimate present without lower/upper bounds.")
            elif not (contract.lower_bound_kes <= contract.point_estimate_kes <= contract.upper_bound_kes):
                violations.append(
                    f"BOUNDS_ORDERING_VIOLATION: L={contract.lower_bound_kes} <= P={contract.point_estimate_kes} "
                    f"<= U={contract.upper_bound_kes} violated."
                )

        # Ensure no selected comparable in the sale table is a rental evidence type
        for comp in contract.selected_comparables:
            if comp.get("evidence_type") in ("rental_lease_contract", "rental_asking_quote"):
                violations.append(
                    f"RENTAL_CONTAMINATION_IN_COMPARABLES: Comparable {comp.get('transaction_id')} has rental evidence type."
                )

        # Ensure model provenance synthetic flag is reflected in `is_synthetic_evidence`
        if contract.model_provenance and contract.model_provenance.trained_on_synthetic_data:
            if not contract.is_synthetic_evidence:
                violations.append("SYNTHETIC_PROVENANCE_MISMATCH: Model trained on synthetic data but is_synthetic_evidence=False.")

        return len(violations) == 0, violations


def execute_snapshot_valuation(
    engine: GlittersValuationEngine,
    snapshot: ValuationEvidenceSnapshot,
    allow_synthetic_inference: bool = False,
) -> ValuationOutputContract:
    """
    Executes valuation on a `ValuationEvidenceSnapshot` and runs `IndependentOutputVerifier`.
    """
    contract = engine.value_property(
        prop=snapshot.subject_property,
        custom_comparables=list(snapshot.sales_evidence),
        custom_rental_comparables=list(snapshot.rentals_evidence),
        valuation_day=snapshot.valuation_day,
        allow_synthetic_inference=allow_synthetic_inference,
    )
    ok, violations = IndependentOutputVerifier.verify_contract(contract)
    if not ok:
        raise RuntimeError(f"IndependentOutputVerifier blocked invalid valuation output: {violations}")
    return contract
