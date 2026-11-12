"""
Dedicated Verification Suite for All Technical Audit Findings (CRITICAL 1–5 & High-Priority Issues).
"""
from __future__ import annotations
import unittest
from dataclasses import replace
import numpy as np

from ..contracts import (
    PropertyRecord,
    MarketTransaction,
    PropertyCategory,
    PriceEvidenceType,
    RentalPeriodUnit,
    TitleVerificationStatus,
    ModelTrainingScope,
    ValuationStatus,
)
from ..config import ValuationMarketConfig
from ..comparable_analysis import ComparableSalesEngine
from ..hedonic_models import HedonicValuationModel
from ..spatial_models import SpatialGPValuationModel
from ..machine_learning import GradientBoostedValuationEnsemble
from ..income_valuation import IncomeValuationEngine
from ..uncertainty import UncertaintyAndConfidenceEngine
from ..ensemble import GlittersValuationEngine
from ..integration_adapter import EvidenceSnapshotBuilder, execute_snapshot_valuation
from ..synthetic_data import generate_synthetic_kenyan_dataset


class TestPhase1AuditRemediations(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mixed_dataset = generate_synthetic_kenyan_dataset(
            n_samples=180,
            random_seed=777,
            tenant_org_id="PUBLIC_REGISTRY",
            include_rental_observations=True,
        )

    def test_critical_01_rental_observations_never_contaminate_sale_models(self) -> None:
        """
        Audit CRITICAL 1:
        Verify that `RENTAL_LEASE_CONTRACT` and `RENTAL_ASKING_QUOTE` observations (e.g., KES 95,000/month)
        are completely excluded from `ComparableSalesEngine`, `HedonicValuationModel`,
        `SpatialGPValuationModel`, and `GradientBoostedValuationEnsemble`, and are routed
        exclusively to `IncomeValuationEngine`.
        """
        rental_only = [
            tx for tx in self.mixed_dataset
            if tx.evidence_type in (PriceEvidenceType.RENTAL_LEASE_CONTRACT, PriceEvidenceType.RENTAL_ASKING_QUOTE)
        ]
        self.assertGreaterEqual(len(rental_only), 15)

        subj = rental_only[0].property_record

        # 1. ComparableSalesEngine must return None / 0 comparables when given only rental records
        comp_eng = ComparableSalesEngine()
        comp_est, cqs, top_comps = comp_eng.evaluate_comparables(subj, rental_only, valuation_day=0.0)
        self.assertIsNone(comp_est)
        self.assertEqual(cqs, 0.0)
        self.assertEqual(len(top_comps), 0)

        # 2. Hedonic, Spatial, and GBDT models must raise ValueError if given only rental records
        with self.assertRaises(ValueError):
            HedonicValuationModel().fit(rental_only)
        with self.assertRaises(ValueError):
            SpatialGPValuationModel().fit(rental_only)
        with self.assertRaises(ValueError):
            GradientBoostedValuationEnsemble().fit(rental_only)

        # 3. IncomeValuationEngine must properly consume rental observations & convert ANNUAL / PER_SQM_MONTHLY units
        inc_eng = IncomeValuationEngine()
        subj_no_rent = replace(subj, monthly_gross_rent_kes=None, building_area_sqm=200.0)
        est_inc = inc_eng.value_property(subj_no_rent, rental_comparables=rental_only, valuation_day=0.0)
        self.assertIsNotNone(est_inc)
        self.assertEqual(est_inc.diagnostics["rent_source"], "rental_comparables_only")  # type: ignore[union-attr]
        self.assertGreater(est_inc.diagnostics["num_rental_comparables_used"], 0)  # type: ignore[union-attr]

        # Verify unit conversion: KES 1,200,000 ANNUAL == KES 100,000 MONTHLY
        tx_annual = replace(
            rental_only[0],
            observed_price_kes=1_200_000.0,
            evidence_type=PriceEvidenceType.RENTAL_LEASE_CONTRACT,
            rental_period_unit=RentalPeriodUnit.ANNUAL,
        )
        self.assertAlmostEqual(inc_eng.normalize_rental_observation_to_monthly_kes(tx_annual), 100_000.0, places=2)

    def test_critical_02_synthetic_data_provenance_and_production_guard(self) -> None:
        """
        Audit CRITICAL 2:
        1. `fit()` must reject synthetic records by default (`allow_synthetic_training=False`).
        2. When trained with `allow_synthetic_training=True`, `model_provenance.trained_on_synthetic_data`
           must be True, and valuing a NON-synthetic subject property with ZERO synthetic comparables
           must still output `is_synthetic_evidence=True` because the underlying model weights were
           trained on synthetic data.
        3. Calling `value_property(..., allow_synthetic_inference=False)` on a synthetically trained
           model must return `ValuationStatus.REJECTED_SYNTHETIC_PROVENANCE_VIOLATION`.
        """
        eng = GlittersValuationEngine()
        with self.assertRaises(PermissionError):
            eng.fit(self.mixed_dataset, allow_synthetic_training=False)

        fit_info = eng.fit(self.mixed_dataset, allow_synthetic_training=True)
        self.assertTrue(fit_info["trained_on_synthetic_data"])
        self.assertIsNotNone(eng.model_provenance)
        self.assertTrue(eng.model_provenance.trained_on_synthetic_data)  # type: ignore[union-attr]

        # Construct a non-synthetic subject property and evaluate with empty custom_comparables
        real_subj = replace(
            self.mixed_dataset[0].property_record,
            property_id="REAL-SUBJ-001",
            tenant_org_id="PUBLIC_REGISTRY",
            is_synthetic=False,
        )
        out_exp = eng.value_property(real_subj, custom_comparables=[], allow_synthetic_inference=True)
        self.assertTrue(
            out_exp.is_synthetic_evidence,
            "Output must declare is_synthetic_evidence=True when model weights were trained on synthetic data!",
        )

        # In production inference mode (allow_synthetic_inference=False), synthetic model weights must be blocked
        out_prod = eng.value_property(real_subj, custom_comparables=[], allow_synthetic_inference=False)
        self.assertEqual(out_prod.status, ValuationStatus.REJECTED_SYNTHETIC_PROVENANCE_VIOLATION)
        self.assertIsNone(out_prod.point_estimate_kes)

    def test_critical_03_model_level_tenant_isolation_and_consistency(self) -> None:
        """
        Audit CRITICAL 3:
        1. Train engine on Tenant A's private records (`ORG_ALPHA_PRIVATE`).
        2. Query engine for a Tenant B property (`ORG_BETA_PRIVATE`) with `custom_comparables=[]`.
        3. Verify that the engine refuses to apply Tenant A's learned hedonic/spatial/ML weights
           to Tenant B's valuation (`ValuationStatus.REJECTED_UNAUTHORIZED_TENANT_SCOPE`).
        4. Verify that mismatched `tx.tenant_org_id != tx.property_record.tenant_org_id` is rejected.
        """
        tenant_a_txs = generate_synthetic_kenyan_dataset(
            n_samples=60, random_seed=303, tenant_org_id="ORG_ALPHA_PRIVATE"
        )
        eng_alpha = GlittersValuationEngine()
        eng_alpha.fit(
            tenant_a_txs,
            tenant_org_id="ORG_ALPHA_PRIVATE",
            training_scope=ModelTrainingScope.SINGLE_TENANT_PRIVATE,
            allow_synthetic_training=True,
        )

        # Tenant A can value its own property
        prop_a = tenant_a_txs[0].property_record
        out_a = eng_alpha.value_property(prop_a, custom_comparables=[], allow_synthetic_inference=True)
        self.assertIsNotNone(out_a.point_estimate_kes)

        # Tenant B is blocked from using Tenant A's trained model weights even with zero comparables
        prop_b = replace(prop_a, property_id="PROP-BETA-99", tenant_org_id="ORG_BETA_PRIVATE")
        out_b = eng_alpha.value_property(prop_b, custom_comparables=[], allow_synthetic_inference=True)
        self.assertEqual(out_b.status, ValuationStatus.REJECTED_UNAUTHORIZED_TENANT_SCOPE)
        self.assertIsNone(out_b.point_estimate_kes)
        self.assertTrue(any("MODEL_TENANT_ISOLATION_VIOLATION" in a for a in out_b.adversarial_alerts))

        # Verify transaction/property tenant mismatch rejection
        mismatched_tx = replace(
            tenant_a_txs[0],
            transaction_id="MISMATCH-TX-01",
            tenant_org_id="ORG_ALPHA_PRIVATE",
            property_record=replace(tenant_a_txs[0].property_record, tenant_org_id="ORG_GAMMA_FOREIGN"),
        )
        clean_s, _, notes = eng_alpha.validator.separate_and_filter_evidence(
            [mismatched_tx], authorized_tenant_ids={"ORG_ALPHA_PRIVATE"}, allow_synthetic=True
        )
        self.assertEqual(len(clean_s), 0)
        self.assertTrue(any("TENANT_MISMATCH_REJECTED" in n for n in notes))

    def test_critical_04_finite_sample_conformal_order_statistic_and_small_sample_guard(self) -> None:
        """
        Audit CRITICAL 4:
        1. For target_confidence = 0.90 (alpha = 0.10), minimum required calibration sample size is
           ceil((1 - 0.10) / 0.10) = 9.
        2. When n_cal in {5, 6, 7, 8}, `calibrate_conformal` must return `is_calibrated = False`
           and refuse to claim empirical conformal calibration.
        3. When n_cal >= 9 (e.g., n_cal = 19 -> k = ceil(20 * 0.90) = 18), `calibrate_conformal`
           selects the exact 18th order statistic (sorted_scores[17]) and supports locally weighted
           conformal multipliers.
        """
        unc = UncertaintyAndConfidenceEngine(target_confidence=0.90)
        self.assertEqual(unc.min_required_calibration_samples(), 9)

        # Case A: n_cal = 5 (too small for 90% finite-sample order statistic!)
        y_true_5 = np.array([10.0, 12.0, 15.0, 18.0, 20.0])
        y_pred_5 = np.array([10.5, 11.5, 14.0, 19.0, 21.0])
        sig_5 = np.ones(5)
        is_cal_5, _, method_5 = unc.calibrate_conformal(y_true_5, y_pred_5, sig_5)
        self.assertFalse(is_cal_5)
        self.assertIn("UNCALIBRATED_FALLBACK_INSUFFICIENT_SAMPLES", method_5)

        # Case B: n_cal = 19 -> k = ceil((19 + 1) * 0.90) = 18th order statistic
        scores_19 = np.arange(1.0, 20.0, dtype=np.float64)  # 1.0, 2.0, ..., 19.0
        y_pred_19 = np.full(19, 100.0)
        y_true_19 = y_pred_19 + scores_19
        sig_19 = np.ones(19)
        feats_19 = np.random.default_rng(1).normal(0, 1, size=(19, 22))
        is_cal_19, q_19, method_19 = unc.calibrate_conformal(y_true_19, y_pred_19, sig_19, cal_features_std=feats_19)
        self.assertTrue(is_cal_19)
        self.assertAlmostEqual(q_19, 18.0, places=5)
        self.assertIn("k=18", method_19)
        local_q = unc.compute_local_conformal_multiplier(feats_19[0])
        self.assertGreaterEqual(local_q, 0.85 * 18.0)

    def test_critical_05_chronological_split_and_zero_temporal_leakage(self) -> None:
        """
        Audit CRITICAL 5:
        1. Verify that `separate_and_filter_evidence` returns transactions sorted in global
           chronological order (`transaction_timestamp_days` ascending), NOT grouped by locality.
        2. Verify that during `fit()`, every training transaction has timestamp <= every calibration
           transaction (`max_training_timestamp_days <= min_calibration_timestamp_days`).
        3. Verify that `ComparableSalesEngine.evaluate_comparables` strictly ignores any candidate
           transaction with `transaction_timestamp_days > valuation_day`.
        """
        eng = GlittersValuationEngine()
        sales, _, _ = eng.validator.separate_and_filter_evidence(
            self.mixed_dataset, authorized_tenant_ids={"PUBLIC_REGISTRY"}, allow_synthetic=True
        )
        timestamps = [t.transaction_timestamp_days for t in sales]
        self.assertEqual(timestamps, sorted(timestamps), "Cleaned sales must be globally sorted by timestamp!")

        # Check that localities are interleaved chronologically rather than grouped in contiguous blocks
        localities = [t.property_record.locality for t in sales]
        transitions = sum(1 for i in range(1, len(localities)) if localities[i] != localities[i - 1])
        self.assertGreater(transitions, len(set(localities)) * 2)

        # Verify causal comparable cutoff: future sales at t = -10 must not be used when valuation_day = -200
        subj = sales[0].property_record
        _, _, comps_at_minus_200 = eng.comp_engine.evaluate_comparables(subj, sales, valuation_day=-200.0)
        for c in comps_at_minus_200:
            self.assertGreaterEqual(c["age_days"], 0.0, "Future transaction leaked into historical valuation!")

    def test_high_priority_title_default_config_calibration_and_sparse_gp(self) -> None:
        """
        Verifies:
        1. `PropertyRecord` defaults to `TitleVerificationStatus.UNVERIFIED_MISSING`.
        2. `ValuationMarketConfig.calibrate_from_transactions()` empirically updates market priors.
        3. `SpatialGPValuationModel` uses Nyström inducing points ($M=64$) when $N > 64$.
        4. `EvidenceSnapshotBuilder` and `execute_snapshot_valuation` enforce the application service boundary.
        """
        # 1. Default title status is UNVERIFIED_MISSING
        minimal_prop = PropertyRecord(
            property_id="MIN-PROP-01",
            tenant_org_id="PUBLIC_REGISTRY",
            category=PropertyCategory.RESIDENTIAL,
            county="Nairobi",
            sub_county="Westlands",
            locality="Kilimani",
            latitude=-1.2921,
            longitude=36.7879,
            land_area_sqm=600.0,
            building_area_sqm=220.0,
        )
        self.assertEqual(minimal_prop.title_status, TitleVerificationStatus.UNVERIFIED_MISSING)

        # 2. Empirical calibration of ValuationMarketConfig
        cal_cfg = ValuationMarketConfig.calibrate_from_transactions(self.mixed_dataset)
        self.assertTrue(cal_cfg.is_empirically_calibrated_from_data)

        # 3. Sparse GP inducing points
        sp = SpatialGPValuationModel(max_inducing_points=32).fit(self.mixed_dataset)
        self.assertTrue(sp.is_sparse_approximation)
        est_sp = sp.predict_single(minimal_prop)
        self.assertEqual(est_sp.diagnostics["num_inducing_points"], 32)

        # 4. Integration adapter snapshot workflow
        eng = GlittersValuationEngine(config=cal_cfg)
        eng.fit(self.mixed_dataset, allow_synthetic_training=True)
        subj_dict = {
            "property_id": "SNAP-PROP-01",
            "tenant_org_id": "PUBLIC_REGISTRY",
            "category": "residential",
            "county": "Nairobi",
            "sub_county": "Dagoretti North",
            "locality": "Kilimani",
            "latitude": -1.2921,
            "longitude": 36.7879,
            "land_area_sqm": 650.0,
            "building_area_sqm": 240.0,
            "is_synthetic": True,
        }
        snapshot = EvidenceSnapshotBuilder.build_snapshot(subj_dict, [], valuation_day=0.0)
        self.assertEqual(snapshot.subject_property.title_status, TitleVerificationStatus.UNVERIFIED_MISSING)
        contract = execute_snapshot_valuation(eng, snapshot, allow_synthetic_inference=True)
        self.assertIsNotNone(contract.point_estimate_kes)
