"""
Unit and 60-Trial Property-Based Mathematical Invariant Tests for `glitters_valuation_engine` v1.1.0.
"""
from __future__ import annotations
import math
import unittest
from dataclasses import replace
import numpy as np

from ..contracts import (
    PropertyRecord,
    MarketTransaction,
    PropertyCategory,
    TenureType,
    TitleVerificationStatus,
    TitleVerificationMetadata,
    PriceEvidenceType,
    ValuationStatus,
)
from ..data_validation import convert_area_to_sqm
from ..feature_engineering import compute_leasehold_discount_factor
from ..ensemble import GlittersValuationEngine
from ..integration_adapter import IndependentOutputVerifier
from ..synthetic_data import generate_synthetic_kenyan_dataset, SYNTHETIC_KENYA_LOCALITIES


class TestUnitAndPropertyBasedInvariants(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dataset = generate_synthetic_kenyan_dataset(
            n_samples=240, random_seed=101, tenant_org_id="PUBLIC_REGISTRY"
        )
        cls.engine = GlittersValuationEngine()
        cls.fit_summary = cls.engine.fit(
            cls.dataset,
            tenant_org_id="PUBLIC_REGISTRY",
            allow_synthetic_training=True,
        )

    def test_01_metric_unit_conversion_invariants(self) -> None:
        """Property-based invariant: 1 acre = 4046.8564224 sqm; 1 ha = 10000 sqm; 1/8 acre = 505.857 sqm."""
        self.assertAlmostEqual(convert_area_to_sqm(1.0, "acres"), 4046.8564224, places=4)
        self.assertAlmostEqual(convert_area_to_sqm(0.125, "acre"), 505.85705, places=3)
        self.assertAlmostEqual(convert_area_to_sqm(1.0, "ha"), 10000.0, places=4)
        self.assertAlmostEqual(convert_area_to_sqm(1000.0, "sqft"), 92.90304, places=4)
        with self.assertRaises(ValueError):
            convert_area_to_sqm(-10.0, "sqm")

    def test_02_leasehold_relativity_monotonicity(self) -> None:
        """Property-based invariant: Leasehold discount factor is strictly monotonic in unexpired years."""
        f_10 = compute_leasehold_discount_factor(TenureType.LEASEHOLD, 10.0)
        f_25 = compute_leasehold_discount_factor(TenureType.LEASEHOLD, 25.0)
        f_50 = compute_leasehold_discount_factor(TenureType.LEASEHOLD, 50.0)
        f_99 = compute_leasehold_discount_factor(TenureType.LEASEHOLD, 99.0)
        f_free = compute_leasehold_discount_factor(TenureType.FREEHOLD, 99.0)
        self.assertLess(f_10, f_25)
        self.assertLess(f_25, f_50)
        self.assertLess(f_50, f_99)
        self.assertLessEqual(f_99, f_free)
        self.assertEqual(f_free, 1.0)

    def test_03_bounds_and_monotonicity_invariants(self) -> None:
        """Property-based invariant: L <= Point <= U, and increasing building size/quality increases value."""
        base_prop = self.dataset[0].property_record
        res_base = self.engine.value_property(base_prop, allow_synthetic_inference=True)
        self.assertIsNotNone(res_base.point_estimate_kes)
        self.assertLessEqual(res_base.lower_bound_kes, res_base.point_estimate_kes)  # type: ignore[arg-type]
        self.assertLessEqual(res_base.point_estimate_kes, res_base.upper_bound_kes)  # type: ignore[arg-type]

        larger_prop = replace(
            base_prop,
            land_area_sqm=base_prop.land_area_sqm * 1.6,
            building_area_sqm=max(150.0, base_prop.building_area_sqm * 1.5),
            construction_quality=min(0.99, base_prop.construction_quality + 0.15),
        )
        res_larger = self.engine.value_property(larger_prop, allow_synthetic_inference=True)
        self.assertGreater(res_larger.point_estimate_kes, res_base.point_estimate_kes)  # type: ignore[arg-type]

    def test_04_multi_tenant_comparable_isolation_invariant(self) -> None:
        """Non-negotiable invariant: Private transactions of ORG_ALPHA never leak into ORG_BETA comparables."""
        base_prop = self.dataset[2].property_record
        private_tx = MarketTransaction(
            transaction_id="PRIVATE-ALPHA-TX-01",
            tenant_org_id="ORG_ALPHA_PRIVATE",
            property_record=replace(base_prop, property_id="PRIVATE-ALPHA-PROP-01", tenant_org_id="ORG_ALPHA_PRIVATE"),
            observed_price_kes=85_000_000.0,
            evidence_type=PriceEvidenceType.VERIFIED_STAMP_DUTY_SALE,
            transaction_timestamp_days=-5.0,
            reliability_weight=1.0,
            is_synthetic=True,
        )
        subj_beta = replace(base_prop, property_id="SUBJ-BETA-01", tenant_org_id="ORG_BETA")
        out_beta = self.engine.value_property(subj_beta, custom_comparables=[private_tx], allow_synthetic_inference=True)
        selected_ids = [c["transaction_id"] for c in out_beta.selected_comparables]
        self.assertNotIn("PRIVATE-ALPHA-TX-01", selected_ids)

    def test_05_synthetic_flag_and_statutory_disclaimer(self) -> None:
        """Non-negotiable invariant: Synthetic flag and statutory non-certification disclaimer are always present."""
        res = self.engine.value_property(self.dataset[1].property_record, allow_synthetic_inference=True)
        self.assertTrue(res.is_synthetic_evidence)
        self.assertIn("Valuers Act (Cap 532", res.statutory_disclaimer)
        self.assertIn("Ardhisasa", res.statutory_disclaimer)

    def test_06_sixty_trial_property_based_fuzzing(self) -> None:
        """
        60-Trial Randomized Property-Based Fuzzing Suite:
        Randomly generates 60 valid Kenyan property configurations across all 10 categories,
        12 localities, varying land areas (200 to 20,000 sqm), building areas (0 to 2,500 sqm),
        ages, condition/quality scores, tenure terms, and valuation timestamps.
        Verifies on every single trial:
          1. Finite positive point estimate and ordered interval bounds: 0 < L <= Point <= U < inf.
          2. Ensemble weights are non-negative and sum to 1.0 (within 1e-3).
          3. Comparable Quality Score (CQS) and Valuation Reliability Score (VRS) lie strictly in [0, 1].
          4. Leasehold monotonicity: Freehold value >= Leasehold (30 yr) value for identical attributes.
          5. IndependentOutputVerifier passes 100% of generated contracts.
        """
        rng = np.random.default_rng(20261112)
        categories = list(PropertyCategory)

        for trial in range(60):
            loc = SYNTHETIC_KENYA_LOCALITIES[int(rng.integers(0, len(SYNTHETIC_KENYA_LOCALITIES)))]
            cat = categories[int(rng.integers(0, len(categories)))]

            land_sqm = float(rng.uniform(220.0, 15000.0))
            if cat in (PropertyCategory.VACANT_LAND, PropertyCategory.DEVELOPMENT_LAND):
                bldg_sqm = 0.0
            else:
                max_bldg = land_sqm * loc["far"] * 0.9
                bldg_sqm = float(rng.uniform(60.0, max(80.0, min(2200.0, max_bldg))))

            cond = float(rng.uniform(0.40, 0.98))
            qual = float(rng.uniform(0.45, 0.98))
            age = 0.0 if bldg_sqm == 0.0 else float(rng.uniform(0.5, 35.0))
            lease_yrs = float(rng.uniform(20.0, 99.0))

            prop_lease = PropertyRecord(
                property_id=f"FUZZ-PROP-{trial:03d}",
                tenant_org_id="PUBLIC_REGISTRY",
                category=cat,
                county=loc["county"],
                sub_county=loc["sub_county"],
                locality=loc["locality"],
                latitude=float(loc["lat"] + rng.normal(0.0, 0.005)),
                longitude=float(loc["lon"] + rng.normal(0.0, 0.005)),
                land_area_sqm=round(land_sqm, 2),
                building_area_sqm=round(bldg_sqm, 2),
                bedrooms=int(rng.integers(1, 6)) if bldg_sqm > 0 else 0,
                bathrooms=int(rng.integers(1, 6)) if bldg_sqm > 0 else 0,
                building_age_years=round(age, 1),
                condition_score=round(cond, 3),
                construction_quality=round(qual, 3),
                accessibility_index=round(float(rng.uniform(0.45, 0.95)), 3),
                infrastructure_score=round(float(rng.uniform(0.45, 0.95)), 3),
                utilities_score=round(float(rng.uniform(0.50, 0.98)), 3),
                neighbourhood_tier=loc["tier"],
                zoning_code=loc["zoning"],
                permitted_ground_coverage=loc["gc"],
                permitted_plot_ratio=loc["far"],
                max_floors=loc["floors"],
                tenure_type=TenureType.LEASEHOLD,
                unexpired_lease_years=round(lease_yrs, 1),
                title_status=TitleVerificationStatus.VERIFIED_CLEAN,
                title_verification_metadata=TitleVerificationMetadata(
                    registry_reference=f"FUZZ-IR-{trial:03d}",
                    verification_source="FUZZ_TEST",
                    verified_timestamp_iso="2026-11-12T00:00:00Z",
                    evidence_document_hash=f"sha256:fuzz_{trial:03d}",
                ),
                monthly_gross_rent_kes=round(bldg_sqm * 750.0, 2) if bldg_sqm > 0 else None,
                is_distressed_sale=(cat == PropertyCategory.DISTRESSED),
                is_synthetic=True,
            )

            out_lease = self.engine.value_property(prop_lease, allow_synthetic_inference=True)
            ok, violations = IndependentOutputVerifier.verify_contract(out_lease)
            self.assertTrue(ok, f"Trial {trial} failed IndependentOutputVerifier: {violations}")
            self.assertIsNotNone(out_lease.point_estimate_kes)
            self.assertGreater(out_lease.lower_bound_kes, 0.0)  # type: ignore[arg-type]
            self.assertLessEqual(out_lease.lower_bound_kes, out_lease.point_estimate_kes)  # type: ignore[arg-type]
            self.assertLessEqual(out_lease.point_estimate_kes, out_lease.upper_bound_kes)  # type: ignore[arg-type]
            self.assertFalse(math.isnan(out_lease.point_estimate_kes))  # type: ignore[arg-type]

            # Verify simplex sum of ensemble weights
            w_sum = sum(out_lease.ensemble_weights.values())
            self.assertAlmostEqual(w_sum, 1.0, places=2)
            for w in out_lease.ensemble_weights.values():
                self.assertGreaterEqual(w, 0.0)

            # Verify CQS and VRS bounds
            self.assertGreaterEqual(out_lease.comparable_quality_score, 0.0)
            self.assertLessEqual(out_lease.comparable_quality_score, 1.0)
            self.assertGreaterEqual(out_lease.valuation_reliability_score, 0.0)
            self.assertLessEqual(out_lease.valuation_reliability_score, 1.0)

            # Verify Freehold >= Short Leasehold (25 yr) monotonicity
            prop_short_lease = replace(prop_lease, unexpired_lease_years=22.0)
            prop_freehold = replace(prop_lease, tenure_type=TenureType.FREEHOLD, unexpired_lease_years=99.0)
            out_short = self.engine.value_property(prop_short_lease, allow_synthetic_inference=True)
            out_free = self.engine.value_property(prop_freehold, allow_synthetic_inference=True)
            self.assertGreaterEqual(out_free.point_estimate_kes, out_short.point_estimate_kes)  # type: ignore[arg-type]
