"""
Module: `synthetic_data`
========================
Remediation for CRITICAL 2 & Title Verification Defaults:
- Generates explicitly labelled synthetic Kenyan records with `is_synthetic=True` and
  `tenant_org_id="SYNTHETIC_SANDBOX"` (never `"PUBLIC_REGISTRY"` unless explicitly overridden for a test).
- Attaches explicit `TitleVerificationMetadata` to verified synthetic records.
- Optionally includes synthetic rental observations (`include_rental_observations=True`) to
  test sale-vs-rental segregation and income-pathway routing.
"""
from __future__ import annotations
from typing import List, Dict, Any, Optional
import math
import numpy as np

from ..contracts import (
    PropertyRecord,
    MarketTransaction,
    PropertyCategory,
    TenureType,
    TitleVerificationStatus,
    TitleVerificationMetadata,
    PriceEvidenceType,
    RentalPeriodUnit,
)
from ..cost_valuation import CostValuationEngine
from ..income_valuation import IncomeValuationEngine
from ..land_valuation import LandAndDevelopmentValuationEngine


SYNTHETIC_KENYA_LOCALITIES: List[Dict[str, Any]] = [
    {"county": "Nairobi", "sub_county": "Westlands", "locality": "Westlands_Core", "lat": -1.2647, "lon": 36.8028, "tier": 1, "zoning": "MIXED_USE", "gc": 0.50, "far": 3.5, "floors": 12},
    {"county": "Nairobi", "sub_county": "Dagoretti North", "locality": "Kilimani", "lat": -1.2921, "lon": 36.7879, "tier": 2, "zoning": "RES_HIGH", "gc": 0.45, "far": 3.0, "floors": 10},
    {"county": "Nairobi", "sub_county": "Langata", "locality": "Karen", "lat": -1.3197, "lon": 36.7085, "tier": 1, "zoning": "RES_LOW", "gc": 0.25, "far": 0.5, "floors": 2},
    {"county": "Nairobi", "sub_county": "Embakasi East", "locality": "Industrial_Area", "lat": -1.3095, "lon": 36.8525, "tier": 2, "zoning": "INDUSTRIAL", "gc": 0.65, "far": 2.0, "floors": 4},
    {"county": "Kiambu", "sub_county": "Kiambaa", "locality": "Ruaka", "lat": -1.2065, "lon": 36.7745, "tier": 3, "zoning": "RES_HIGH", "gc": 0.45, "far": 2.4, "floors": 7},
    {"county": "Kiambu", "sub_county": "Ruiru", "locality": "Tatu_Ruiru", "lat": -1.1485, "lon": 36.9605, "tier": 3, "zoning": "MIXED_USE", "gc": 0.40, "far": 1.8, "floors": 5},
    {"county": "Mombasa", "sub_county": "Nyali", "locality": "Nyali_Beachside", "lat": -4.0225, "lon": 39.7125, "tier": 1, "zoning": "RES_MID", "gc": 0.35, "far": 1.6, "floors": 5},
    {"county": "Kisumu", "sub_county": "Kisumu Central", "locality": "Milimani_Kisumu", "lat": -0.1095, "lon": 34.7565, "tier": 2, "zoning": "RES_MID", "gc": 0.35, "far": 1.5, "floors": 4},
    {"county": "Nakuru", "sub_county": "Nakuru East", "locality": "Milimani_Nakuru", "lat": -0.2725, "lon": 36.0785, "tier": 2, "zoning": "RES_MID", "gc": 0.35, "far": 1.4, "floors": 4},
    {"county": "Machakos", "sub_county": "Mavoko", "locality": "Syokimau", "lat": -1.3625, "lon": 36.9325, "tier": 3, "zoning": "RES_MID", "gc": 0.40, "far": 1.5, "floors": 4},
    {"county": "Kajiado", "sub_county": "Kajiado East", "locality": "Kitengela", "lat": -1.4745, "lon": 36.9585, "tier": 4, "zoning": "RES_MID", "gc": 0.35, "far": 1.2, "floors": 3},
    {"county": "Uasin Gishu", "sub_county": "Ainabkoi", "locality": "Elgon_View_Eldoret", "lat": 0.5085, "lon": 35.2895, "tier": 2, "zoning": "RES_LOW", "gc": 0.30, "far": 1.0, "floors": 3},
]


def generate_synthetic_kenyan_dataset(
    n_samples: int = 360,
    random_seed: int = 2026,
    tenant_org_id: str = "SYNTHETIC_SANDBOX",
    include_rental_observations: bool = False,
) -> List[MarketTransaction]:
    rng = np.random.default_rng(random_seed)
    cost_eng = CostValuationEngine()
    inc_eng = IncomeValuationEngine()
    land_eng = LandAndDevelopmentValuationEngine()

    categories = [
        PropertyCategory.RESIDENTIAL,
        PropertyCategory.APARTMENT_CONDO,
        PropertyCategory.COMMERCIAL,
        PropertyCategory.INDUSTRIAL,
        PropertyCategory.VACANT_LAND,
        PropertyCategory.DEVELOPMENT_LAND,
        PropertyCategory.AGRICULTURAL,
        PropertyCategory.RENTAL,
        PropertyCategory.INVESTMENT,
        PropertyCategory.DISTRESSED,
    ]
    cat_probs = [0.28, 0.18, 0.10, 0.06, 0.10, 0.06, 0.05, 0.07, 0.06, 0.04]

    transactions: List[MarketTransaction] = []

    for i in range(n_samples):
        loc = SYNTHETIC_KENYA_LOCALITIES[i % len(SYNTHETIC_KENYA_LOCALITIES)]
        cat = categories[int(rng.choice(len(categories), p=cat_probs))]

        lat = float(loc["lat"] + rng.normal(0.0, 0.008))
        lon = float(loc["lon"] + rng.normal(0.0, 0.008))

        if cat == PropertyCategory.APARTMENT_CONDO:
            land_sqm = float(rng.uniform(450.0, 1200.0))
            bldg_sqm = float(rng.uniform(75.0, 240.0))
            beds = int(max(1, min(5, round(bldg_sqm / 48.0))))
            baths = beds
            tenure = TenureType.SECTIONAL_TITLE
            lease_yrs = float(rng.uniform(65.0, 99.0))
        elif cat in (PropertyCategory.VACANT_LAND, PropertyCategory.DEVELOPMENT_LAND):
            land_sqm = float(rng.choice([505.86, 1011.71, 2023.43, 4046.86]) * rng.uniform(0.95, 1.15))
            bldg_sqm = 0.0
            beds, baths = 0, 0
            tenure = TenureType.FREEHOLD if loc["tier"] >= 3 else TenureType.LEASEHOLD
            lease_yrs = 99.0 if tenure == TenureType.FREEHOLD else float(rng.uniform(45.0, 95.0))
        elif cat == PropertyCategory.AGRICULTURAL:
            land_sqm = float(rng.uniform(8093.7, 24281.1))
            bldg_sqm = float(rng.uniform(0.0, 120.0))
            beds, baths = 2 if bldg_sqm > 50 else 0, 1 if bldg_sqm > 50 else 0
            tenure = TenureType.FREEHOLD
            lease_yrs = 99.0
        elif cat in (PropertyCategory.COMMERCIAL, PropertyCategory.INDUSTRIAL, PropertyCategory.INVESTMENT):
            land_sqm = float(rng.uniform(1011.7, 4046.8))
            bldg_sqm = float(land_sqm * rng.uniform(0.45, 1.35))
            beds, baths = 0, int(max(2, round(bldg_sqm / 150.0)))
            tenure = TenureType.LEASEHOLD
            lease_yrs = float(rng.uniform(40.0, 95.0))
        else:
            land_sqm = float(rng.choice([505.86, 1011.71, 2023.43]) * rng.uniform(0.92, 1.10))
            bldg_sqm = float(rng.uniform(160.0, 480.0))
            beds = int(max(2, min(6, round(bldg_sqm / 68.0))))
            baths = beds
            tenure = TenureType.FREEHOLD if loc["tier"] in (1, 3, 4) else TenureType.LEASEHOLD
            lease_yrs = 99.0 if tenure == TenureType.FREEHOLD else float(rng.uniform(35.0, 95.0))

        age_yrs = 0.0 if bldg_sqm == 0.0 else float(rng.uniform(1.0, 28.0))
        cond = float(np.clip(rng.normal(0.78 - 0.008 * age_yrs, 0.08), 0.35, 0.98))
        qual = float(np.clip(rng.normal(0.82 if loc["tier"] == 1 else 0.72, 0.08), 0.45, 0.98))
        access = float(np.clip(rng.normal(0.85 - 0.06 * (loc["tier"] - 1), 0.07), 0.40, 0.98))
        infra = float(np.clip(rng.normal(0.84 - 0.07 * (loc["tier"] - 1), 0.07), 0.38, 0.98))
        utils = float(np.clip(rng.normal(0.86 - 0.06 * (loc["tier"] - 1), 0.06), 0.42, 0.99))

        monthly_rent: Optional[float] = None
        if bldg_sqm > 0:
            base_r = 950.0 if cat in (PropertyCategory.COMMERCIAL, PropertyCategory.INVESTMENT) else 620.0
            tier_m = {1: 1.40, 2: 1.12, 3: 0.85, 4: 0.62}[loc["tier"]]
            monthly_rent = round(float(bldg_sqm * base_r * tier_m * (0.65 + 0.45 * qual)), 2)

        synth_title_meta = TitleVerificationMetadata(
            registry_reference=f"SYNTH-ARDHISASA-IR-{i+1:05d}",
            verification_source="SYNTHETIC_BENCHMARK_SIMULATOR",
            verified_timestamp_iso="2026-11-01T00:00:00Z",
            evidence_document_hash=f"sha256:synth_doc_{i+1:05d}",
        )

        prop = PropertyRecord(
            property_id=f"SYNTH-KE-PROP-{i+1:04d}",
            tenant_org_id=tenant_org_id,
            category=cat,
            county=loc["county"],
            sub_county=loc["sub_county"],
            locality=loc["locality"],
            latitude=round(lat, 6),
            longitude=round(lon, 6),
            land_area_sqm=round(land_sqm, 2),
            building_area_sqm=round(bldg_sqm, 2),
            bedrooms=beds,
            bathrooms=baths,
            building_age_years=round(age_yrs, 1),
            condition_score=round(cond, 3),
            construction_quality=round(qual, 3),
            accessibility_index=round(access, 3),
            infrastructure_score=round(infra, 3),
            utilities_score=round(utils, 3),
            neighbourhood_tier=loc["tier"],
            zoning_code=loc["zoning"],
            permitted_ground_coverage=loc["gc"],
            permitted_plot_ratio=loc["far"],
            max_floors=loc["floors"],
            tenure_type=tenure,
            unexpired_lease_years=round(lease_yrs, 1),
            title_status=TitleVerificationStatus.VERIFIED_CLEAN,
            title_verification_metadata=synth_title_meta,
            monthly_gross_rent_kes=monthly_rent,
            vacancy_rate=0.07 if loc["tier"] <= 2 else 0.11,
            operating_expense_ratio=0.18 if bldg_sqm > 0 else 0.05,
            is_distressed_sale=(cat == PropertyCategory.DISTRESSED),
            is_synthetic=True,
        )

        drc_val = cost_eng.value_property(prop).point_estimate_kes
        inc_est = inc_eng.value_property(prop)
        rlv_est = land_eng.evaluate_residual_land_value(prop)

        if cat in (PropertyCategory.COMMERCIAL, PropertyCategory.INDUSTRIAL, PropertyCategory.INVESTMENT, PropertyCategory.RENTAL) and inc_est:
            fundamental_val = 0.58 * inc_est.point_estimate_kes + 0.42 * drc_val
        elif cat in (PropertyCategory.VACANT_LAND, PropertyCategory.DEVELOPMENT_LAND) and rlv_est:
            fundamental_val = 0.55 * rlv_est.point_estimate_kes + 0.45 * drc_val
        elif inc_est:
            fundamental_val = 0.70 * drc_val + 0.30 * inc_est.point_estimate_kes
        else:
            fundamental_val = drc_val

        tx_day = float(rng.uniform(-720.0, 0.0))
        time_trend = math.exp(0.00017 * tx_day)
        market_noise = math.exp(float(rng.normal(0.0, 0.065)))

        is_asking = (i % 7 == 0)
        evidence_type = (
            PriceEvidenceType.ASKING_LISTING_PRICE
            if is_asking
            else (
                PriceEvidenceType.VERIFIED_STAMP_DUTY_SALE
                if i % 2 == 0
                else PriceEvidenceType.VERIFIED_CONTRACT_SALE
            )
        )
        asking_markup = 1.095 if is_asking else 1.0
        distress_factor = 0.78 if cat == PropertyCategory.DISTRESSED else 1.0
        obs_price = round(float(fundamental_val * time_trend * market_noise * asking_markup * distress_factor), 2)

        transactions.append(
            MarketTransaction(
                transaction_id=f"SYNTH-KE-TX-{i+1:04d}",
                tenant_org_id=tenant_org_id,
                property_record=prop,
                observed_price_kes=obs_price,
                evidence_type=evidence_type,
                transaction_timestamp_days=round(tx_day, 1),
                reliability_weight=0.45 if is_asking else 0.98,
                is_synthetic=True,
            )
        )

        # Optionally emit companion rental evidence record for improved properties
        if include_rental_observations and monthly_rent is not None and (i % 5 == 0):
            rent_ev = (
                PriceEvidenceType.RENTAL_LEASE_CONTRACT
                if (i % 10 == 0)
                else PriceEvidenceType.RENTAL_ASKING_QUOTE
            )
            rent_obs = monthly_rent * (1.0 if rent_ev == PriceEvidenceType.RENTAL_LEASE_CONTRACT else 1.075)
            transactions.append(
                MarketTransaction(
                    transaction_id=f"SYNTH-KE-RENT-{i+1:04d}",
                    tenant_org_id=tenant_org_id,
                    property_record=prop,
                    observed_price_kes=round(float(rent_obs), 2),
                    evidence_type=rent_ev,
                    transaction_timestamp_days=round(tx_day, 1),
                    reliability_weight=0.92 if rent_ev == PriceEvidenceType.RENTAL_LEASE_CONTRACT else 0.50,
                    rental_period_unit=RentalPeriodUnit.MONTHLY,
                    is_synthetic=True,
                )
            )

    return transactions
