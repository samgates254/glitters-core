from __future__ import annotations
"""Data-quality, inventory-aware deduplication, competing-risks censoring & Kenyan market priors.

Phase 1 Audit Remediation (v2.0):
1. Inventory-aware deduplication: preserves genuinely distinct units in the same apartment block
   when `unit_identifier` differs, while collapsing duplicate cross-portal scrapes of the same unit.
2. Competing-risks survival censoring: distinguishes `VERIFIED_LEASE_EXECUTED`,
   `VERIFIED_WITHDRAWN_UNLEASED`, and `ACTIVE_OR_UNVERIFIED_PORTAL_AD` rather than assuming all
   180-day portal ads failed to clear.
3. Configurable asking-to-transaction discount assumptions updated via empirical Bayes shrinkage
   when matched verified lease transactions exist in the submarket.
"""


from dataclasses import dataclass
import math
from typing import Sequence
import numpy as np

from .schemas import (
    ListingLifecycleStatus,
    MarketComparableListing,
    PropertySegment,
    UnitSpecification,
)


@dataclass(frozen=True)
class SegmentPriorParameters:
    """Configurable initial econometric priors calibrated to Kenyan property segments.

    Note: `initial_asking_to_tx_discount_assumption` represents a configurable prior assumption
    that is dynamically updated with empirical transaction pairs when available.
    """

    segment: PropertySegment
    prior_elasticity: float
    nonlinear_cliff_gamma: float
    base_monthly_arrival_rate: float
    base_conversion_prob: float
    weibull_duration_shape: float
    initial_asking_to_tx_discount_assumption: float
    min_plausible_area_sqm: float
    max_plausible_area_sqm: float
    min_plausible_rent_kes: float
    max_plausible_rent_kes: float
    affordability_income_cap: float
    typical_lease_months: int

    @property
    def asking_to_transaction_discount(self) -> float:
        return self.initial_asking_to_tx_discount_assumption


KENYAN_SEGMENT_PRIORS: dict[PropertySegment, SegmentPriorParameters] = {
    PropertySegment.BEDSITTER: SegmentPriorParameters(
        segment=PropertySegment.BEDSITTER,
        prior_elasticity=-2.45,
        nonlinear_cliff_gamma=4.5,
        base_monthly_arrival_rate=6.5,
        base_conversion_prob=0.28,
        weibull_duration_shape=0.92,
        initial_asking_to_tx_discount_assumption=0.06,
        min_plausible_area_sqm=11.0,
        max_plausible_area_sqm=38.0,
        min_plausible_rent_kes=4500.0,
        max_plausible_rent_kes=28000.0,
        affordability_income_cap=0.32,
        typical_lease_months=12,
    ),
    PropertySegment.STUDIO: SegmentPriorParameters(
        segment=PropertySegment.STUDIO,
        prior_elasticity=-2.10,
        nonlinear_cliff_gamma=4.0,
        base_monthly_arrival_rate=5.4,
        base_conversion_prob=0.26,
        weibull_duration_shape=0.94,
        initial_asking_to_tx_discount_assumption=0.07,
        min_plausible_area_sqm=20.0,
        max_plausible_area_sqm=60.0,
        min_plausible_rent_kes=12000.0,
        max_plausible_rent_kes=65000.0,
        affordability_income_cap=0.33,
        typical_lease_months=12,
    ),
    PropertySegment.RESIDENTIAL_APARTMENT: SegmentPriorParameters(
        segment=PropertySegment.RESIDENTIAL_APARTMENT,
        prior_elasticity=-1.65,
        nonlinear_cliff_gamma=3.5,
        base_monthly_arrival_rate=4.2,
        base_conversion_prob=0.24,
        weibull_duration_shape=0.90,
        initial_asking_to_tx_discount_assumption=0.08,
        min_plausible_area_sqm=40.0,
        max_plausible_area_sqm=380.0,
        min_plausible_rent_kes=18000.0,
        max_plausible_rent_kes=450000.0,
        affordability_income_cap=0.35,
        typical_lease_months=12,
    ),
    PropertySegment.STANDALONE_HOUSE: SegmentPriorParameters(
        segment=PropertySegment.STANDALONE_HOUSE,
        prior_elasticity=-1.25,
        nonlinear_cliff_gamma=2.8,
        base_monthly_arrival_rate=2.1,
        base_conversion_prob=0.19,
        weibull_duration_shape=0.85,
        initial_asking_to_tx_discount_assumption=0.12,
        min_plausible_area_sqm=110.0,
        max_plausible_area_sqm=1200.0,
        min_plausible_rent_kes=60000.0,
        max_plausible_rent_kes=1500000.0,
        affordability_income_cap=0.38,
        typical_lease_months=24,
    ),
    PropertySegment.STUDENT_ACCOMMODATION: SegmentPriorParameters(
        segment=PropertySegment.STUDENT_ACCOMMODATION,
        prior_elasticity=-2.60,
        nonlinear_cliff_gamma=5.0,
        base_monthly_arrival_rate=7.8,
        base_conversion_prob=0.32,
        weibull_duration_shape=0.95,
        initial_asking_to_tx_discount_assumption=0.05,
        min_plausible_area_sqm=9.0,
        max_plausible_area_sqm=45.0,
        min_plausible_rent_kes=4000.0,
        max_plausible_rent_kes=35000.0,
        affordability_income_cap=0.35,
        typical_lease_months=10,
    ),
    PropertySegment.RETAIL_SPACE: SegmentPriorParameters(
        segment=PropertySegment.RETAIL_SPACE,
        prior_elasticity=-1.35,
        nonlinear_cliff_gamma=3.0,
        base_monthly_arrival_rate=2.4,
        base_conversion_prob=0.20,
        weibull_duration_shape=0.86,
        initial_asking_to_tx_discount_assumption=0.11,
        min_plausible_area_sqm=15.0,
        max_plausible_area_sqm=2500.0,
        min_plausible_rent_kes=20000.0,
        max_plausible_rent_kes=3500000.0,
        affordability_income_cap=0.45,
        typical_lease_months=36,
    ),
    PropertySegment.OFFICE: SegmentPriorParameters(
        segment=PropertySegment.OFFICE,
        prior_elasticity=-1.30,
        nonlinear_cliff_gamma=2.9,
        base_monthly_arrival_rate=2.0,
        base_conversion_prob=0.18,
        weibull_duration_shape=0.84,
        initial_asking_to_tx_discount_assumption=0.13,
        min_plausible_area_sqm=25.0,
        max_plausible_area_sqm=5000.0,
        min_plausible_rent_kes=30000.0,
        max_plausible_rent_kes=6000000.0,
        affordability_income_cap=0.45,
        typical_lease_months=36,
    ),
    PropertySegment.COMMERCIAL_BUILDING: SegmentPriorParameters(
        segment=PropertySegment.COMMERCIAL_BUILDING,
        prior_elasticity=-1.18,
        nonlinear_cliff_gamma=2.6,
        base_monthly_arrival_rate=1.5,
        base_conversion_prob=0.16,
        weibull_duration_shape=0.82,
        initial_asking_to_tx_discount_assumption=0.14,
        min_plausible_area_sqm=150.0,
        max_plausible_area_sqm=10000.0,
        min_plausible_rent_kes=150000.0,
        max_plausible_rent_kes=12000000.0,
        affordability_income_cap=0.50,
        typical_lease_months=60,
    ),
    PropertySegment.INDUSTRIAL_PROPERTY: SegmentPriorParameters(
        segment=PropertySegment.INDUSTRIAL_PROPERTY,
        prior_elasticity=-1.10,
        nonlinear_cliff_gamma=2.5,
        base_monthly_arrival_rate=1.3,
        base_conversion_prob=0.17,
        weibull_duration_shape=0.82,
        initial_asking_to_tx_discount_assumption=0.12,
        min_plausible_area_sqm=200.0,
        max_plausible_area_sqm=15000.0,
        min_plausible_rent_kes=120000.0,
        max_plausible_rent_kes=10000000.0,
        affordability_income_cap=0.50,
        typical_lease_months=60,
    ),
    PropertySegment.WAREHOUSE: SegmentPriorParameters(
        segment=PropertySegment.WAREHOUSE,
        prior_elasticity=-1.15,
        nonlinear_cliff_gamma=2.5,
        base_monthly_arrival_rate=1.4,
        base_conversion_prob=0.18,
        weibull_duration_shape=0.83,
        initial_asking_to_tx_discount_assumption=0.12,
        min_plausible_area_sqm=250.0,
        max_plausible_area_sqm=20000.0,
        min_plausible_rent_kes=140000.0,
        max_plausible_rent_kes=12000000.0,
        affordability_income_cap=0.50,
        typical_lease_months=48,
    ),
    PropertySegment.HOSPITALITY_SHORT_STAY: SegmentPriorParameters(
        segment=PropertySegment.HOSPITALITY_SHORT_STAY,
        prior_elasticity=-1.95,
        nonlinear_cliff_gamma=4.2,
        base_monthly_arrival_rate=8.5,
        base_conversion_prob=0.30,
        weibull_duration_shape=0.96,
        initial_asking_to_tx_discount_assumption=0.07,
        min_plausible_area_sqm=18.0,
        max_plausible_area_sqm=350.0,
        min_plausible_rent_kes=25000.0,
        max_plausible_rent_kes=900000.0,
        affordability_income_cap=0.45,
        typical_lease_months=6,
    ),
    PropertySegment.MIXED_USE_DEVELOPMENT: SegmentPriorParameters(
        segment=PropertySegment.MIXED_USE_DEVELOPMENT,
        prior_elasticity=-1.50,
        nonlinear_cliff_gamma=3.2,
        base_monthly_arrival_rate=3.2,
        base_conversion_prob=0.22,
        weibull_duration_shape=0.88,
        initial_asking_to_tx_discount_assumption=0.09,
        min_plausible_area_sqm=35.0,
        max_plausible_area_sqm=2000.0,
        min_plausible_rent_kes=25000.0,
        max_plausible_rent_kes=2500000.0,
        affordability_income_cap=0.40,
        typical_lease_months=24,
    ),
}


# Seasonal demand multipliers across calendar months (1=Jan .. 12=Dec)
KENYAN_MONTHLY_SEASONALITY: dict[PropertySegment, dict[int, float]] = {
    seg: {
        1: 1.18,
        2: 1.08,
        3: 1.00,
        4: 0.92,
        5: 0.96,
        6: 0.94,
        7: 0.98,
        8: 1.04,
        9: 1.15,
        10: 1.03,
        11: 0.91,
        12: 0.81,
    }
    for seg in PropertySegment
}
KENYAN_MONTHLY_SEASONALITY[PropertySegment.HOSPITALITY_SHORT_STAY] = {
    1: 1.10,
    2: 1.00,
    3: 0.90,
    4: 0.85,
    5: 0.82,
    6: 0.95,
    7: 1.22,
    8: 1.25,
    9: 1.10,
    10: 0.95,
    11: 0.92,
    12: 1.30,
}


@dataclass(frozen=True)
class SanitizedComparable:
    """Cleaned comparable record with estimated clearing rent and competing-risks reliability weight."""

    listing_id: str
    segment: PropertySegment
    county: str
    neighbourhood: str
    floor_area_sqm: float
    bedrooms: int
    raw_asking_rent_kes: float
    estimated_clearing_rent_kes: float
    reliability_weight: float
    has_borehole_water: bool | None
    has_backup_generator: bool | None
    security_tier: int
    is_verified_transaction: bool
    lifecycle_status: ListingLifecycleStatus
    competing_risk_clearing_prob: float


@dataclass(frozen=True)
class DataQualityReport:
    """Diagnostic summary of comparable listing sanitization."""

    raw_count: int
    deduplicated_count: int
    sibling_units_preserved_count: int
    specification_rejected_count: int
    mad_outlier_rejected_count: int
    stale_downweighted_count: int
    final_clean_count: int
    effective_sample_size: float
    empirical_asking_to_tx_discount: float
    sanitized_comparables: tuple[SanitizedComparable, ...]


class KenyanMarketDataSanitizer:
    """Sanitizes noisy Kenyan rental listings with inventory-aware deduplication and competing-risks censoring."""

    def __init__(
        self,
        stale_threshold_days: int = 45,
        max_age_days: int = 240,
        mad_z_threshold: float = 3.5,
    ) -> None:
        self.stale_threshold_days = stale_threshold_days
        self.max_age_days = max_age_days
        self.mad_z_threshold = mad_z_threshold

    def sanitize(
        self,
        listings: Sequence[MarketComparableListing],
        target_segment: PropertySegment | None = None,
        target_county: str | None = None,
        target_neighbourhood: str | None = None,
    ) -> DataQualityReport:
        """Executes 4-stage sanitization with inventory-aware deduplication and competing-risks weighting."""
        raw_count = len(listings)
        if raw_count == 0:
            return DataQualityReport(0, 0, 0, 0, 0, 0, 0, 0.0, 0.08, ())

        filtered: list[MarketComparableListing] = []
        for item in listings:
            if target_segment is not None and item.segment != target_segment:
                continue
            if target_county is not None and item.county.lower() != target_county.lower():
                continue
            filtered.append(item)

        if not filtered:
            return DataQualityReport(raw_count, 0, 0, 0, 0, 0, 0, 0.0, 0.08, ())

        # Stage 1: Physical specification & plausibility checks
        spec_valid: list[MarketComparableListing] = []
        spec_rejected = 0
        for item in filtered:
            prior = KENYAN_SEGMENT_PRIORS[item.segment]
            invalid_area = not (
                prior.min_plausible_area_sqm
                <= item.floor_area_sqm
                <= prior.max_plausible_area_sqm
            )
            invalid_rent = not (
                prior.min_plausible_rent_kes
                <= item.asking_rent_kes
                <= prior.max_plausible_rent_kes * 1.5
            )
            invalid_beds = (
                item.segment in (PropertySegment.BEDSITTER, PropertySegment.STUDIO)
                and item.bedrooms > 1
            ) or item.bedrooms < 0
            if invalid_area or invalid_rent or invalid_beds or item.listing_age_days > self.max_age_days:
                spec_rejected += 1
            else:
                spec_valid.append(item)

        # Stage 2: Inventory-aware spatial-structural deduplication across portals
        # Distinct verified `unit_identifier` records in the same building are preserved as genuine sibling supply.
        spec_valid_sorted = sorted(
            spec_valid,
            key=lambda x: (not x.is_verified_transaction, x.listing_age_days, x.listing_id),
        )
        deduped: list[MarketComparableListing] = []
        siblings_preserved = 0
        for cand in spec_valid_sorted:
            is_dup = False
            for kept in deduped:
                same_loc = (
                    cand.county.lower() == kept.county.lower()
                    and cand.neighbourhood.lower() == kept.neighbourhood.lower()
                    and cand.segment == kept.segment
                    and cand.bedrooms == kept.bedrooms
                )
                if same_loc:
                    area_diff = abs(cand.floor_area_sqm - kept.floor_area_sqm) / max(
                        kept.floor_area_sqm, 1.0
                    )
                    price_diff = abs(cand.asking_rent_kes - kept.asking_rent_kes) / max(
                        kept.asking_rent_kes, 1.0
                    )
                    if area_diff <= 0.05 and price_diff <= 0.06:
                        # Check if both have explicit distinct unit identifiers in the same property
                        if (
                            cand.unit_identifier is not None
                            and kept.unit_identifier is not None
                            and cand.unit_identifier.strip().lower()
                            != kept.unit_identifier.strip().lower()
                        ):
                            siblings_preserved += 1
                            continue
                        is_dup = True
                        break
            if not is_dup:
                deduped.append(cand)

        dedup_removed = len(spec_valid) - len(deduped)

        if not deduped:
            return DataQualityReport(
                raw_count=raw_count,
                deduplicated_count=dedup_removed,
                sibling_units_preserved_count=siblings_preserved,
                specification_rejected_count=spec_rejected,
                mad_outlier_rejected_count=0,
                stale_downweighted_count=0,
                final_clean_count=0,
                effective_sample_size=0.0,
                empirical_asking_to_tx_discount=0.08,
                sanitized_comparables=(),
            )

        # Stage 3: Robust Huber/MAD outlier filter on log price-per-sqm
        log_ppsm = np.array(
            [math.log(c.asking_rent_kes / c.floor_area_sqm) for c in deduped],
            dtype=float,
        )
        median_val = float(np.median(log_ppsm))
        mad_val = float(np.median(np.abs(log_ppsm - median_val)))
        robust_sigma = max(1.4826 * mad_val, 0.12)

        inliers: list[MarketComparableListing] = []
        mad_rejected = 0
        for idx, comp in enumerate(deduped):
            if len(deduped) >= 4 and abs(log_ppsm[idx] - median_val) > self.mad_z_threshold * robust_sigma:
                mad_rejected += 1
            else:
                inliers.append(comp)

        # Empirical Bayes update of the asking-to-transaction discount from verified pairs
        verified_discounts = [
            max(0.0, (c.asking_rent_kes - c.verified_transaction_rent_kes) / c.asking_rent_kes)
            for c in inliers
            if c.is_verified_transaction
            and c.verified_transaction_rent_kes is not None
            and c.asking_rent_kes > 0
        ]
        default_prior_disc = (
            KENYAN_SEGMENT_PRIORS[target_segment].initial_asking_to_tx_discount_assumption
            if target_segment is not None
            else 0.08
        )
        if verified_discounts:
            n_v = len(verified_discounts)
            w_emp = n_v / (n_v + 5.0)
            empirical_disc = float(
                w_emp * float(np.mean(verified_discounts)) + (1.0 - w_emp) * default_prior_disc
            )
        else:
            empirical_disc = default_prior_disc

        # Stage 4: Competing-risks survival censoring & stale listing adjustment
        sanitized: list[SanitizedComparable] = []
        stale_count = 0
        for comp in inliers:
            prior = KENYAN_SEGMENT_PRIORS[comp.segment]
            seg_disc = (
                empirical_disc
                if target_segment == comp.segment
                else prior.initial_asking_to_tx_discount_assumption
            )

            if comp.is_verified_transaction and comp.verified_transaction_rent_kes is not None:
                clearing_rent = comp.verified_transaction_rent_kes
                base_weight = 1.0
                lifecycle = ListingLifecycleStatus.VERIFIED_LEASE_EXECUTED
                clearing_prob = 1.0
            elif comp.lifecycle_status == ListingLifecycleStatus.VERIFIED_WITHDRAWN_UNLEASED:
                # Right-censored withdrawal without lease execution
                extra_disc = min(0.16, max(0.0, (comp.listing_age_days - 20) / 365.0) * 0.22)
                clearing_rent = comp.asking_rent_kes * (1.0 - min(0.32, seg_disc + extra_disc))
                base_weight = 0.45
                lifecycle = ListingLifecycleStatus.VERIFIED_WITHDRAWN_UNLEASED
                clearing_prob = 0.0
            else:
                # Unverified portal ad: competing risks between unrecorded lease, withdrawal, or stale ghost ad
                extra_stale_disc = min(
                    0.18, max(0.0, (comp.listing_age_days - 30) / 365.0) * 0.24
                )
                disc = min(0.32, seg_disc + extra_stale_disc)
                clearing_rent = comp.asking_rent_kes * (1.0 - disc)
                base_weight = 0.72
                lifecycle = ListingLifecycleStatus.ACTIVE_OR_UNVERIFIED_PORTAL_AD
                # Latent probability that an unverified portal ad reflects an active clearing benchmark
                clearing_prob = float(
                    np.clip(math.exp(-0.008 * max(0, comp.listing_age_days - 15)), 0.15, 0.90)
                )

            if comp.listing_age_days > self.stale_threshold_days:
                stale_count += 1
                decay = math.exp(-0.018 * (comp.listing_age_days - self.stale_threshold_days))
            else:
                decay = 1.0

            loc_bonus = (
                1.0
                if (
                    target_neighbourhood is None
                    or comp.neighbourhood.lower() == target_neighbourhood.lower()
                )
                else 0.55
            )
            weight = float(np.clip(base_weight * decay * loc_bonus, 0.04, 1.0))

            sanitized.append(
                SanitizedComparable(
                    listing_id=comp.listing_id,
                    segment=comp.segment,
                    county=comp.county,
                    neighbourhood=comp.neighbourhood,
                    floor_area_sqm=comp.floor_area_sqm,
                    bedrooms=comp.bedrooms,
                    raw_asking_rent_kes=comp.asking_rent_kes,
                    estimated_clearing_rent_kes=clearing_rent,
                    reliability_weight=weight,
                    has_borehole_water=comp.has_borehole_water,
                    has_backup_generator=comp.has_backup_generator,
                    security_tier=comp.security_tier,
                    is_verified_transaction=comp.is_verified_transaction,
                    lifecycle_status=lifecycle,
                    competing_risk_clearing_prob=clearing_prob,
                )
            )

        weights = np.array([s.reliability_weight for s in sanitized], dtype=float)
        if weights.size > 0 and np.sum(weights**2) > 0:
            n_eff = float((np.sum(weights) ** 2) / np.sum(weights**2))
        else:
            n_eff = 0.0

        return DataQualityReport(
            raw_count=raw_count,
            deduplicated_count=dedup_removed,
            sibling_units_preserved_count=siblings_preserved,
            specification_rejected_count=spec_rejected,
            mad_outlier_rejected_count=mad_rejected,
            stale_downweighted_count=stale_count,
            final_clean_count=len(sanitized),
            effective_sample_size=n_eff,
            empirical_asking_to_tx_discount=round(empirical_disc, 4),
            sanitized_comparables=tuple(sanitized),
        )


def validate_unit_specification_plausibility(unit: UnitSpecification) -> list[str]:
    """Checks whether a unit's physical specification contradicts its segment definition."""
    warnings: list[str] = []
    prior = KENYAN_SEGMENT_PRIORS[unit.segment]
    if unit.floor_area_sqm < prior.min_plausible_area_sqm * 0.75:
        warnings.append(
            f"floor_area_sqm ({unit.floor_area_sqm}) is unusually small for {unit.segment.value}."
        )
    if unit.floor_area_sqm > prior.max_plausible_area_sqm * 1.35:
        warnings.append(
            f"floor_area_sqm ({unit.floor_area_sqm}) exceeds plausible bounds for {unit.segment.value}."
        )
    if unit.segment in (PropertySegment.BEDSITTER, PropertySegment.STUDIO) and unit.bedrooms > 1:
        warnings.append(
            f"Segment {unit.segment.value} specified with {unit.bedrooms} bedrooms (expected <= 1)."
        )
    return warnings


# ======================================================================================
