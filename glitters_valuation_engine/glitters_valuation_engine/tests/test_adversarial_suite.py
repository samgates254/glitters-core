"""
15-Scenario Adversarial Test Suite (Section 6 Requirements) for `glitters_valuation_engine` v1.1.0.
"""
from __future__ import annotations
import unittest
from dataclasses import replace

from ..contracts import (
    PropertyCategory,
    TitleVerificationStatus,
    ValuationStatus,
)
from ..ensemble import GlittersValuationEngine
from ..synthetic_data import generate_synthetic_kenyan_dataset


class TestAdversarialValuationSuite(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dataset = generate_synthetic_kenyan_dataset(
            n_samples=240, random_seed=2026, tenant_org_id="PUBLIC_REGISTRY"
        )
        cls.engine = GlittersValuationEngine()
        cls.engine.fit(cls.dataset, tenant_org_id="PUBLIC_REGISTRY", allow_synthetic_training=True)
        cls.base_prop = cls.dataset[1].property_record

    def test_case_01_no_comparable_transactions(self) -> None:
        """Case 1: Zero comparable transactions -> falls back to spatial/hedonic/cost/income and degrades CQS=0."""
        out = self.engine.value_property(self.base_prop, custom_comparables=[], allow_synthetic_inference=True)
        self.assertEqual(out.comparable_quality_score, 0.0)
        self.assertIn("ZERO_LOCAL_COMPARABLES", " ".join(out.validation_flags))
        self.assertIsNotNone(out.point_estimate_kes)

    def test_case_02_emerging_neighbourhood(self) -> None:
        """Case 2: Property in a brand-new emerging locality unseen during training."""
        emerging_prop = replace(
            self.base_prop,
            property_id="ADV-02-EMERGING",
            locality="Konza_Technopolis_Phase2_Unseen",
            county="Machakos",
            latitude=-1.6850,
            longitude=37.1850,
            neighbourhood_tier=3,
        )
        out = self.engine.value_property(emerging_prop, allow_synthetic_inference=True)
        self.assertIsNotNone(out.point_estimate_kes)
        self.assertGreater(out.epistemic_uncertainty_kes, 0.0)  # type: ignore[arg-type]

    def test_case_03_extremely_expensive_trophy_property(self) -> None:
        """Case 3: Ultra-luxury trophy estate in Muthaiga/Karen -> triggers OOD alert and wider epistemic interval."""
        trophy_prop = replace(
            self.base_prop,
            property_id="ADV-03-TROPHY",
            locality="Karen",
            latitude=-1.3197,
            longitude=36.7085,
            land_area_sqm=40468.5,
            building_area_sqm=2800.0,
            bedrooms=12,
            bathrooms=14,
            construction_quality=0.99,
            condition_score=0.99,
            neighbourhood_tier=1,
        )
        out = self.engine.value_property(trophy_prop, allow_synthetic_inference=True)
        self.assertIsNotNone(out.point_estimate_kes)
        self.assertTrue(any("OUT_OF_DISTRIBUTION_PROPERTY" in a for a in out.adversarial_alerts))

    def test_case_04_unusual_architectural_design(self) -> None:
        """Case 4: Unusual architectural design (tiny footprint but extreme height/quality mismatch)."""
        unusual_prop = replace(
            self.base_prop,
            property_id="ADV-04-UNUSUAL-ARCH",
            land_area_sqm=600.0,
            building_area_sqm=1450.0,
            bedrooms=1,
            bathrooms=12,
            construction_quality=0.99,
            building_age_years=0.5,
        )
        out = self.engine.value_property(unusual_prop, allow_synthetic_inference=True)
        self.assertTrue(any("OUT_OF_DISTRIBUTION_PROPERTY" in a for a in out.adversarial_alerts))

    def test_case_05_incomplete_measurements(self) -> None:
        """Case 5: Residential property with missing (0.0) building floor area -> flagged & reliability penalized."""
        incomplete_prop = replace(
            self.base_prop,
            property_id="ADV-05-INCOMPLETE-BLDG",
            category=PropertyCategory.RESIDENTIAL,
            building_area_sqm=0.0,
        )
        out = self.engine.value_property(incomplete_prop, allow_synthetic_inference=True)
        self.assertTrue(any("MISSING_BUILDING_FLOOR_AREA" in f for f in out.validation_flags))
        self.assertLess(out.valuation_reliability_score, 0.70)

    def test_case_06_incorrect_property_coordinates(self) -> None:
        """Case 6: Swapped lat/lon or Indian Ocean coordinates -> rejected before valuation."""
        swapped_prop = replace(
            self.base_prop,
            property_id="ADV-06-SWAPPED-COORDS",
            latitude=36.8219,
            longitude=-1.2921,
        )
        out = self.engine.value_property(swapped_prop, allow_synthetic_inference=True)
        self.assertEqual(out.status, ValuationStatus.REJECTED_INVALID_INPUT)
        self.assertIsNone(out.point_estimate_kes)
        self.assertTrue(any("SWAPPED_COORDINATES_DETECTED" in a for a in out.adversarial_alerts))

    def test_case_07_duplicate_listings(self) -> None:
        """Case 7: Duplicate comparable listings within 30m and 45 days -> deduplicated automatically."""
        tx_orig = self.dataset[5]
        tx_dup1 = replace(tx_orig, transaction_id="ADV-07-DUP-1", transaction_timestamp_days=tx_orig.transaction_timestamp_days - 5.0)
        tx_dup2 = replace(tx_orig, transaction_id="ADV-07-DUP-2", transaction_timestamp_days=tx_orig.transaction_timestamp_days - 10.0)
        clean_pool, notes = self.engine.validator.deduplicate_and_filter_transactions(
            [tx_orig, tx_dup1, tx_dup2], tenant_org_id="PUBLIC_REGISTRY", allow_synthetic=True
        )
        self.assertEqual(len(clean_pool), 1)
        self.assertTrue(any("DUPLICATE_DETECTED" in n for n in notes))

    def test_case_08_fraudulent_transaction_records(self) -> None:
        """Case 8: Wash-sale / corrupt price outlier (150x normal price per sqm) -> rejected by robust MAD filter."""
        loc_txs = [t for t in self.dataset if t.property_record.locality == self.base_prop.locality][:8]
        fraud_tx = replace(
            loc_txs[0],
            transaction_id="ADV-08-FRAUD-TX",
            observed_price_kes=loc_txs[0].observed_price_kes * 150.0,
            property_record=replace(loc_txs[0].property_record, latitude=loc_txs[0].property_record.latitude + 0.01),
        )
        clean_pool, notes = self.engine.validator.deduplicate_and_filter_transactions(
            loc_txs + [fraud_tx], tenant_org_id="PUBLIC_REGISTRY", allow_synthetic=True
        )
        clean_ids = [t.transaction_id for t in clean_pool]
        self.assertNotIn("ADV-08-FRAUD-TX", clean_ids)
        self.assertTrue(any("FRAUD_OR_CORRUPT_OUTLIER_REJECTED" in n for n in notes))

    def test_case_09_outdated_comparable_sales(self) -> None:
        """Case 9: Comparable sales are 4 years old (-1460 days) -> temporal kernel decays CQS & widens interval."""
        local_comps = self.dataset
        stale_pool = [
            replace(t, transaction_timestamp_days=-1460.0)
            for t in local_comps
        ]
        out_fresh = self.engine.value_property(self.base_prop, custom_comparables=local_comps, allow_synthetic_inference=True)
        out_stale = self.engine.value_property(self.base_prop, custom_comparables=stale_pool, allow_synthetic_inference=True)
        self.assertLess(out_stale.comparable_quality_score, out_fresh.comparable_quality_score)

    def test_case_10_extreme_market_volatility(self) -> None:
        """Case 10: High macroeconomic/interest-rate volatility regime -> widens prediction interval."""
        self.engine.market_volatility_regime = 1.0
        out_calm = self.engine.value_property(self.base_prop, allow_synthetic_inference=True)
        self.engine.market_volatility_regime = 1.85
        out_volatile = self.engine.value_property(self.base_prop, allow_synthetic_inference=True)
        self.engine.market_volatility_regime = 1.0

        width_calm = (out_calm.upper_bound_kes or 0.0) - (out_calm.lower_bound_kes or 0.0)
        width_volatile = (out_volatile.upper_bound_kes or 0.0) - (out_volatile.lower_bound_kes or 0.0)
        self.assertGreater(width_volatile, width_calm)

    def test_case_11_unusual_development_potential(self) -> None:
        """Case 11: Small older bungalow on high-FAR commercial/mixed-use land -> flags HIGH_REDEVELOPMENT_POTENTIAL."""
        redev_prop = replace(
            self.base_prop,
            property_id="ADV-11-REDEV-BUNGALOW",
            category=PropertyCategory.RESIDENTIAL,
            locality="Kilimani",
            zoning_code="MIXED_USE",
            land_area_sqm=2023.4,
            building_area_sqm=140.0,
            building_age_years=42.0,
            condition_score=0.42,
            permitted_plot_ratio=4.0,
            permitted_ground_coverage=0.50,
            max_floors=14,
            neighbourhood_tier=1,
        )
        out = self.engine.value_property(redev_prop, allow_synthetic_inference=True)
        self.assertTrue(any("HIGH_REDEVELOPMENT_POTENTIAL" in f for f in out.validation_flags))
        self.assertGreater(out.ensemble_weights.get("residual_land_hbu", 0.0), 0.05)

    def test_case_12_highly_heterogeneous_neighbourhood(self) -> None:
        """Case 12: Neighbourhood with highly divergent comparable prices -> increases comparable std error & interval width."""
        het_pool = []
        for idx, mult in enumerate([0.45, 0.65, 1.0, 1.55, 2.10]):
            t = self.dataset[idx]
            het_pool.append(
                replace(
                    t,
                    transaction_id=f"HET-TX-{idx}",
                    property_record=replace(
                        self.base_prop,
                        property_id=f"HET-PROP-{idx}",
                        latitude=self.base_prop.latitude + 0.001 * (idx + 1),
                    ),
                    observed_price_kes=25_000_000.0 * mult,
                )
            )
        out = self.engine.value_property(self.base_prop, custom_comparables=het_pool, allow_synthetic_inference=True)
        self.assertIsNotNone(out.point_estimate_kes)
        rel_width = ((out.upper_bound_kes or 0.0) - (out.lower_bound_kes or 0.0)) / (out.point_estimate_kes or 1.0)
        self.assertGreater(rel_width, 0.25)

    def test_case_13_incorrect_land_size_units(self) -> None:
        """Case 13: 0.25 acres entered raw as 0.25 sqm -> rejected with SUSPECTED_LAND_UNIT_ERROR."""
        unit_err_prop = replace(
            self.base_prop,
            property_id="ADV-13-UNIT-ERR",
            category=PropertyCategory.RESIDENTIAL,
            land_area_sqm=0.25,
        )
        out = self.engine.value_property(unit_err_prop, allow_synthetic_inference=True)
        self.assertEqual(out.status, ValuationStatus.REJECTED_INVALID_INPUT)
        self.assertTrue(any("SUSPECTED_LAND_UNIT_ERROR" in a for a in out.adversarial_alerts))

    def test_case_14_missing_construction_information(self) -> None:
        """Case 14: Unverified/missing construction quality & condition -> flagged and reliability score reduced."""
        missing_const_prop = replace(
            self.base_prop,
            property_id="ADV-14-MISSING-CONST",
            condition_score=0.01,
            construction_quality=0.01,
        )
        out = self.engine.value_property(missing_const_prop, allow_synthetic_inference=True)
        self.assertTrue(any("MISSING_CONSTRUCTION_QUALITY" in f for f in out.validation_flags))
        self.assertLess(out.valuation_reliability_score, 0.72)

    def test_case_15_disputed_or_unclear_ownership_records(self) -> None:
        """Case 15: Active caveat or court dispute on title -> strictly blocked with REJECTED_DISPUTED_TITLE."""
        disputed_prop = replace(
            self.base_prop,
            property_id="ADV-15-DISPUTED-TITLE",
            title_status=TitleVerificationStatus.CAVEAT_DISPUTED,
        )
        out = self.engine.value_property(disputed_prop, allow_synthetic_inference=True)
        self.assertEqual(out.status, ValuationStatus.REJECTED_DISPUTED_TITLE)
        self.assertIsNone(out.point_estimate_kes)
        self.assertTrue(any("DISPUTED_TITLE_CAVEAT" in a for a in out.adversarial_alerts))
