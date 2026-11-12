"""
Module: `data_validation`
=========================
Enforces Kenyan geographic bounds, metric unit standardization, title/ownership
integrity checks (including `TitleVerificationMetadata` audit validation),
tenant-consistency validation, chronological-order-preserving deduplication &
MAD fraud screening, and strict separation of capital sale vs. rental evidence.
"""
from __future__ import annotations
from typing import List, Tuple, Dict, Optional, Set
import math
import numpy as np

from ..contracts import (
    PropertyRecord,
    MarketTransaction,
    TitleVerificationStatus,
    PriceEvidenceType,
    TenureType,
    CAPITAL_SALE_EVIDENCE_TYPES,
    RENTAL_EVIDENCE_TYPES,
)

KENYA_LAT_MIN = -4.75
KENYA_LAT_MAX = 5.05
KENYA_LON_MIN = 33.90
KENYA_LON_MAX = 41.95

COUNTY_ENVELOPES: Dict[str, Tuple[float, float, float, float]] = {
    "Nairobi": (-1.45, -1.15, 36.65, 37.10),
    "Kiambu": (-1.30, -0.75, 36.55, 37.25),
    "Mombasa": (-4.15, -3.90, 39.55, 39.78),
    "Kisumu": (-0.35, 0.20, 34.45, 35.25),
    "Nakuru": (-1.05, 0.25, 35.45, 36.60),
    "Machakos": (-1.80, -0.75, 36.85, 37.90),
    "Kajiado": (-3.05, -1.25, 36.05, 37.90),
    "Uasin Gishu": (0.10, 0.90, 34.80, 35.65),
    "Kilifi": (-4.05, -2.30, 39.10, 40.25),
}

UNIT_CONVERSION_TO_SQM: Dict[str, float] = {
    "sqm": 1.0,
    "m2": 1.0,
    "sqft": 0.09290304,
    "ft2": 0.09290304,
    "acres": 4046.8564224,
    "acre": 4046.8564224,
    "hectares": 10000.0,
    "ha": 10000.0,
}


def convert_area_to_sqm(value: float, unit: str = "sqm") -> float:
    """Converts land or building area from acres, hectares, or sq.ft into standard metric m^2."""
    u = unit.strip().lower()
    if u not in UNIT_CONVERSION_TO_SQM:
        raise ValueError(f"Unsupported area unit '{unit}'. Expected one of {list(UNIT_CONVERSION_TO_SQM.keys())}.")
    if value <= 0 or math.isnan(value) or math.isinf(value):
        raise ValueError(f"Area value must be positive and finite; got {value}.")
    return float(value * UNIT_CONVERSION_TO_SQM[u])


def haversine_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two WGS84 coordinates in kilometres."""
    r_earth = 6371.0088
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    return 2.0 * r_earth * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))


def haversine_matrix_km(coords1: np.ndarray, coords2: np.ndarray) -> np.ndarray:
    """Vectorized great-circle distance matrix (N1 x N2) in kilometres."""
    r_earth = 6371.0088
    lat1 = np.radians(coords1[:, 0])[:, None]
    lon1 = np.radians(coords1[:, 1])[:, None]
    lat2 = np.radians(coords2[:, 0])[None, :]
    lon2 = np.radians(coords2[:, 1])[None, :]
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat / 2.0) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2.0) ** 2
    a = np.clip(a, 0.0, 1.0)
    return 2.0 * r_earth * np.arctan2(np.sqrt(a), np.sqrt(1.0 - a))


class DataValidationEngine:
    """
    Validates property inputs and market transactions against physical, geographic,
    legal, tenant-scope, and evidence-type invariants.
    """

    def validate_subject_property(
        self, prop: PropertyRecord
    ) -> Tuple[bool, List[str], List[str], float]:
        flags: List[str] = []
        alerts: List[str] = []
        completeness_penalties = 0.0

        # 1. Coordinate & Geographic Validation
        if not (KENYA_LAT_MIN <= prop.latitude <= KENYA_LAT_MAX and KENYA_LON_MIN <= prop.longitude <= KENYA_LON_MAX):
            if (KENYA_LAT_MIN <= prop.longitude <= KENYA_LAT_MAX and KENYA_LON_MIN <= prop.latitude <= KENYA_LON_MAX):
                alerts.append("SWAPPED_COORDINATES_DETECTED: Latitude and longitude appear transposed.")
            else:
                alerts.append("OUT_OF_KENYA_BOUNDS: Coordinates fall outside Kenyan national territory.")
            return False, flags, alerts, 0.0

        if prop.county in COUNTY_ENVELOPES:
            lat_min, lat_max, lon_min, lon_max = COUNTY_ENVELOPES[prop.county]
            if not (lat_min <= prop.latitude <= lat_max and lon_min <= prop.longitude <= lon_max):
                alerts.append(
                    f"COUNTY_COORDINATE_MISMATCH: Coordinates ({prop.latitude:.4f}, {prop.longitude:.4f}) "
                    f"lie outside declared county '{prop.county}' envelope."
                )
                return False, flags, alerts, 0.1

        # 2. Legal Ownership & Title Verification (Remediation: verify TitleVerificationMetadata)
        if prop.title_status == TitleVerificationStatus.CAVEAT_DISPUTED:
            alerts.append(
                "DISPUTED_TITLE_CAVEAT: Property has an active caveat, court injunction, or disputed ownership. "
                "Automated market valuation is blocked pending legal resolution."
            )
            return False, flags, alerts, 0.2

        if prop.title_status == TitleVerificationStatus.UNVERIFIED_MISSING:
            flags.append("UNVERIFIED_TITLE_RECORD: Title registry status is unverified; applying title risk haircut.")
            completeness_penalties += 0.18
        elif prop.title_status in (TitleVerificationStatus.VERIFIED_CLEAN, TitleVerificationStatus.ENCUMBERED_MORTGAGE):
            meta = prop.title_verification_metadata
            if meta is None or not meta.registry_reference.strip() or not meta.evidence_document_hash.strip():
                flags.append(
                    "MISSING_TITLE_VERIFICATION_METADATA: Title status declared clean/encumbered without "
                    "supporting Ardhisasa/registry reference or document hash; treating as unverified for risk scoring."
                )
                completeness_penalties += 0.12

        if prop.tenure_type == TenureType.LEASEHOLD and prop.unexpired_lease_years < 15.0:
            flags.append(
                f"SHORT_UNEXPIRED_LEASE: Leasehold has only {prop.unexpired_lease_years:.1f} years remaining; "
                "statutory lease extension risk applies."
            )

        # 3. Land & Building Measurement Consistency
        if prop.land_area_sqm <= 0.0 or math.isnan(prop.land_area_sqm):
            alerts.append("INVALID_LAND_AREA: Land area must be strictly positive.")
            return False, flags, alerts, 0.0

        if prop.land_area_sqm < 15.0 and prop.category.value != "apartment_condo":
            alerts.append(
                f"SUSPECTED_LAND_UNIT_ERROR: Declared land_area_sqm={prop.land_area_sqm:.3f} m^2 is smaller than "
                "a standard Kenyan 1/8-acre (505.86 m^2) or 30x60 ft plot. Suspect acres entered as m^2."
            )
            return False, flags, alerts, 0.1

        if prop.building_area_sqm < 0.0 or math.isnan(prop.building_area_sqm):
            alerts.append("INVALID_BUILDING_AREA: Building area cannot be negative or NaN.")
            return False, flags, alerts, 0.0

        if prop.building_area_sqm > 0 and prop.land_area_sqm > 0:
            implied_far = prop.building_area_sqm / prop.land_area_sqm
            max_plausible_far = max(prop.permitted_plot_ratio * 2.5, 12.0)
            if implied_far > max_plausible_far and prop.category.value != "apartment_condo":
                alerts.append(
                    f"IMPLAUSIBLE_PLOT_RATIO: Implied FAR ({implied_far:.2f}) exceeds physical/zoning envelope "
                    f"({max_plausible_far:.2f}). Suspect sqft/sqm unit mismatch."
                )
                return False, flags, alerts, 0.15

        # 4. Missing Construction / Structural Information
        if prop.category.value in ("residential", "commercial", "industrial", "apartment_condo"):
            if prop.building_area_sqm <= 0.0:
                flags.append("MISSING_BUILDING_FLOOR_AREA: Building floor area is zero/missing for improved category.")
                completeness_penalties += 0.30
            if not (0.05 <= prop.condition_score <= 1.0):
                flags.append("MISSING_OR_EXTREME_CONDITION: Condition score outside normal bounds.")
                completeness_penalties += 0.15
            if not (0.05 <= prop.construction_quality <= 1.0):
                flags.append("MISSING_CONSTRUCTION_QUALITY: Construction quality score missing or unverified.")
                completeness_penalties += 0.15

        completeness_score = max(0.0, min(1.0, 1.0 - completeness_penalties))
        return True, flags, alerts, completeness_score

    def separate_and_filter_evidence(
        self,
        transactions: List[MarketTransaction],
        authorized_tenant_ids: Set[str],
        allow_public_pool: bool = True,
        allow_synthetic: bool = False,
        max_timestamp_days: Optional[float] = None,
    ) -> Tuple[List[MarketTransaction], List[MarketTransaction], List[str]]:
        """
        Remediation for CRITICAL 1, 2, 3, and 5:
        1. Validates consistency between `tx.tenant_org_id` and `tx.property_record.tenant_org_id`.
        2. Rejects synthetic transactions (`tx.is_synthetic` or `tx.property_record.is_synthetic`)
           unless `allow_synthetic=True`.
        3. Enforces tenant scope isolation (`authorized_tenant_ids` + optional `"PUBLIC_REGISTRY"`).
        4. Enforces temporal causal cutoff `tx.transaction_timestamp_days <= max_timestamp_days`
           when provided (eliminating lookahead leakage).
        5. Deduplicates and screens MAD outliers while STRICTLY preserving global chronological order
           (`transaction_timestamp_days` ascending).
        6. Strictly separates capital sale transactions (`sale_transactions`) from rental evidence
           (`rental_transactions`) so rental quotes/leases NEVER contaminate capital sale models.
        """
        audit_notes: List[str] = []
        allowed_tenants = set(authorized_tenant_ids)
        if allow_public_pool:
            allowed_tenants.add("PUBLIC_REGISTRY")
        if allow_synthetic:
            allowed_tenants.add("SYNTHETIC_SANDBOX")

        valid_Scoped: List[MarketTransaction] = []
        for tx in transactions:
            # Check tenant consistency between transaction wrapper and embedded property record (CRITICAL 3)
            if tx.tenant_org_id != tx.property_record.tenant_org_id:
                audit_notes.append(
                    f"TENANT_MISMATCH_REJECTED: Transaction {tx.transaction_id} tenant '{tx.tenant_org_id}' "
                    f"!= property_record tenant '{tx.property_record.tenant_org_id}'."
                )
                continue

            # Check synthetic provenance policy (CRITICAL 2)
            is_synth = tx.is_synthetic or tx.property_record.is_synthetic or tx.tenant_org_id == "SYNTHETIC_SANDBOX"
            if is_synth and not allow_synthetic:
                audit_notes.append(
                    f"SYNTHETIC_EVIDENCE_REJECTED: Record {tx.transaction_id} is synthetic and "
                    "allow_synthetic=False."
                )
                continue

            # Check multi-tenant authorization scope (CRITICAL 3)
            if tx.tenant_org_id not in allowed_tenants:
                audit_notes.append(f"TENANT_ISOLATION_BLOCKED: Filtered foreign tenant record {tx.transaction_id}.")
                continue

            # Check causal timestamp bound (CRITICAL 5)
            if max_timestamp_days is not None and tx.transaction_timestamp_days > max_timestamp_days:
                continue

            if tx.observed_price_kes <= 0.0 or math.isnan(tx.observed_price_kes) or math.isinf(tx.observed_price_kes):
                audit_notes.append(f"INVALID_OBSERVED_PRICE: Rejected {tx.transaction_id}.")
                continue

            valid_Scoped.append(tx)

        # Partition strictly into Capital Sales vs. Rental Evidence (CRITICAL 1)
        raw_sales = [t for t in valid_Scoped if t.evidence_type in CAPITAL_SALE_EVIDENCE_TYPES]
        raw_rentals = [t for t in valid_Scoped if t.evidence_type in RENTAL_EVIDENCE_TYPES]

        clean_sales = self._deduplicate_and_mad_filter_chronological(raw_sales, audit_notes, label="SALE")
        clean_rentals = self._deduplicate_and_mad_filter_chronological(raw_rentals, audit_notes, label="RENTAL")

        return clean_sales, clean_rentals, audit_notes

    def _deduplicate_and_mad_filter_chronological(
        self,
        records: List[MarketTransaction],
        audit_notes: List[str],
        label: str = "SALE",
    ) -> List[MarketTransaction]:
        if not records:
            return []

        # Step 1: Deduplicate (preferring more recent / higher reliability record when duplicate within 35m & 45d)
        deduped: List[MarketTransaction] = []
        for candidate in sorted(records, key=lambda x: (x.transaction_timestamp_days, x.reliability_weight), reverse=True):
            is_dup = False
            for kept in deduped:
                dist_km = haversine_distance_km(
                    candidate.property_record.latitude,
                    candidate.property_record.longitude,
                    kept.property_record.latitude,
                    kept.property_record.longitude,
                )
                time_diff = abs(candidate.transaction_timestamp_days - kept.transaction_timestamp_days)
                area_ref = max(1.0, kept.property_record.land_area_sqm)
                area_rel_diff = abs(candidate.property_record.land_area_sqm - kept.property_record.land_area_sqm) / area_ref
                if dist_km < 0.035 and time_diff <= 45.0 and area_rel_diff < 0.03:
                    is_dup = True
                    audit_notes.append(
                        f"DUPLICATE_DETECTED ({label}): Dropped duplicate record {candidate.transaction_id} "
                        f"(matches {kept.transaction_id})."
                    )
                    break
            if not is_dup:
                deduped.append(candidate)

        # Step 2: Locality-stratified MAD outlier screening that records rejected IDs
        by_locality: Dict[str, List[MarketTransaction]] = {}
        for tx in deduped:
            by_locality.setdefault(tx.property_record.locality, []).append(tx)

        rejected_ids: Set[str] = set()
        for loc, loc_txs in by_locality.items():
            if len(loc_txs) < 4:
                continue
            log_ppsqm = np.array([
                math.log(tx.observed_price_kes / max(1.0, tx.property_record.land_area_sqm + 2.5 * tx.property_record.building_area_sqm))
                for tx in loc_txs
            ])
            med = float(np.median(log_ppsqm))
            mad = float(np.median(np.abs(log_ppsqm - med)))
            robust_sigma = max(0.12, 1.4826 * mad)
            for tx, val in zip(loc_txs, log_ppsqm):
                z_rob = abs(val - med) / robust_sigma
                if z_rob > 4.2:
                    rejected_ids.add(tx.transaction_id)
                    audit_notes.append(
                        f"FRAUD_OR_CORRUPT_OUTLIER_REJECTED ({label}): Transaction {tx.transaction_id} in {loc} "
                        f"has robust z-score {z_rob:.2f} (> 4.2)."
                    )

        # Step 3: Re-sort survivors strictly in global chronological order (CRITICAL 5)
        survivors = [tx for tx in deduped if tx.transaction_id not in rejected_ids]
        survivors.sort(key=lambda tx: (tx.transaction_timestamp_days, tx.transaction_id))
        return survivors

    def deduplicate_and_filter_transactions(
        self,
        transactions: List[MarketTransaction],
        tenant_org_id: str,
        allow_public_pool: bool = True,
        allow_synthetic: bool = True,
        max_timestamp_days: Optional[float] = None,
    ) -> Tuple[List[MarketTransaction], List[str]]:
        """
        Backwards-compatible wrapper that returns ONLY clean capital sale transactions
        in global chronological order (never allowing rental observations into sale pools).
        """
        clean_sales, clean_rentals, notes = self.separate_and_filter_evidence(
            transactions=transactions,
            authorized_tenant_ids={tenant_org_id},
            allow_public_pool=allow_public_pool,
            allow_synthetic=allow_synthetic,
            max_timestamp_days=max_timestamp_days,
        )
        if clean_rentals:
            notes.append(
                f"RENTAL_EVIDENCE_SEGREGATED: {len(clean_rentals)} rental observations excluded from capital sale pool."
            )
        return clean_sales, notes
