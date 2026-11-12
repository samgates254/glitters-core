r"""
Module: `ensemble`
==================
Master Regime-Aware Precision-Weighted Ensemble & `GlittersValuationEngine` Orchestrator.
Phase 1 Remediations:
- CRITICAL 1: Segregates `sale_reference_pool` and `rental_reference_pool`. Rental observations
  are routed exclusively to `IncomeValuationEngine` and never contaminate comparable sales,
  hedonic, spatial GP, or GBDT capital value models.
- CRITICAL 2: Rejects synthetic observations in `fit()` by default (`allow_synthetic_training=False`).
  Tracks `ModelProvenance` (including `trained_on_synthetic_data` and dataset SHA-256 hash) and sets
  `is_synthetic_evidence=True` on every output whenever the subject, any comparable, OR the trained
  model itself touched synthetic data.
- CRITICAL 3: Enforces model-level tenant scope isolation (`ModelTrainingScope` + `authorized_tenant_ids`).
  A model trained on Tenant A's private records blocks valuation requests from Tenant B with
  `ValuationStatus.REJECTED_UNAUTHORIZED_TENANT_SCOPE`.
- CRITICAL 4: Uses exact finite-sample order-statistic conformal calibration ($n_{\text{cal}} \ge 9$ for 90%
  coverage) and locally adaptive KNN conformal scaling, exposing `is_conformal_calibrated` and
  `calibration_method` in `ValuationOutputContract`.
- CRITICAL 5: Performs chronological ordering before splitting training and calibration cohorts, and
  evaluates each calibration observation using ONLY historical reference transactions ($t_{\text{comp}} \le t_{\text{cal}}$)
  to eliminate temporal lookahead leakage.
"""
from __future__ import annotations
from typing import List, Dict, Any, Optional, Set, Iterable
import hashlib
import math
import numpy as np

from ..contracts import (
    PropertyRecord,
    MarketTransaction,
    PropertyCategory,
    SubModelEstimate,
    ValuationOutputContract,
    ValuationStatus,
    TitleVerificationStatus,
    ModelTrainingScope,
    ModelProvenance,
)
from ..config import ValuationMarketConfig
from ..data_validation import DataValidationEngine
from ..feature_engineering import FeatureEngineeringPipeline
from ..comparable_analysis import ComparableSalesEngine
from ..hedonic_models import HedonicValuationModel
from ..spatial_models import SpatialGPValuationModel
from ..machine_learning import GradientBoostedValuationEnsemble
from ..income_valuation import IncomeValuationEngine
from ..cost_valuation import CostValuationEngine
from ..land_valuation import LandAndDevelopmentValuationEngine
from ..uncertainty import UncertaintyAndConfidenceEngine
from ..explainability import ValuationExplainabilityEngine


CATEGORY_REGIME_PRIORS: Dict[PropertyCategory, Dict[str, float]] = {
    PropertyCategory.RESIDENTIAL: {
        "comparable_sales": 0.30,
        "hedonic_pricing": 0.20,
        "spatial_gp_kriging": 0.18,
        "machine_learning_gbdt": 0.18,
        "income_capitalization_dcf": 0.04,
        "depreciated_replacement_cost": 0.08,
        "residual_land_hbu": 0.02,
    },
    PropertyCategory.APARTMENT_CONDO: {
        "comparable_sales": 0.34,
        "hedonic_pricing": 0.22,
        "spatial_gp_kriging": 0.16,
        "machine_learning_gbdt": 0.18,
        "income_capitalization_dcf": 0.08,
        "depreciated_replacement_cost": 0.02,
        "residual_land_hbu": 0.00,
    },
    PropertyCategory.COMMERCIAL: {
        "comparable_sales": 0.18,
        "hedonic_pricing": 0.14,
        "spatial_gp_kriging": 0.12,
        "machine_learning_gbdt": 0.14,
        "income_capitalization_dcf": 0.30,
        "depreciated_replacement_cost": 0.08,
        "residual_land_hbu": 0.04,
    },
    PropertyCategory.INDUSTRIAL: {
        "comparable_sales": 0.16,
        "hedonic_pricing": 0.14,
        "spatial_gp_kriging": 0.10,
        "machine_learning_gbdt": 0.12,
        "income_capitalization_dcf": 0.24,
        "depreciated_replacement_cost": 0.20,
        "residual_land_hbu": 0.04,
    },
    PropertyCategory.VACANT_LAND: {
        "comparable_sales": 0.32,
        "hedonic_pricing": 0.18,
        "spatial_gp_kriging": 0.20,
        "machine_learning_gbdt": 0.10,
        "income_capitalization_dcf": 0.00,
        "depreciated_replacement_cost": 0.05,
        "residual_land_hbu": 0.15,
    },
    PropertyCategory.DEVELOPMENT_LAND: {
        "comparable_sales": 0.22,
        "hedonic_pricing": 0.12,
        "spatial_gp_kriging": 0.14,
        "machine_learning_gbdt": 0.08,
        "income_capitalization_dcf": 0.00,
        "depreciated_replacement_cost": 0.04,
        "residual_land_hbu": 0.40,
    },
    PropertyCategory.AGRICULTURAL: {
        "comparable_sales": 0.34,
        "hedonic_pricing": 0.22,
        "spatial_gp_kriging": 0.20,
        "machine_learning_gbdt": 0.12,
        "income_capitalization_dcf": 0.04,
        "depreciated_replacement_cost": 0.08,
        "residual_land_hbu": 0.00,
    },
    PropertyCategory.RENTAL: {
        "comparable_sales": 0.20,
        "hedonic_pricing": 0.14,
        "spatial_gp_kriging": 0.12,
        "machine_learning_gbdt": 0.14,
        "income_capitalization_dcf": 0.34,
        "depreciated_replacement_cost": 0.06,
        "residual_land_hbu": 0.00,
    },
    PropertyCategory.INVESTMENT: {
        "comparable_sales": 0.18,
        "hedonic_pricing": 0.14,
        "spatial_gp_kriging": 0.12,
        "machine_learning_gbdt": 0.14,
        "income_capitalization_dcf": 0.34,
        "depreciated_replacement_cost": 0.04,
        "residual_land_hbu": 0.04,
    },
    PropertyCategory.DISTRESSED: {
        "comparable_sales": 0.28,
        "hedonic_pricing": 0.18,
        "spatial_gp_kriging": 0.14,
        "machine_learning_gbdt": 0.14,
        "income_capitalization_dcf": 0.10,
        "depreciated_replacement_cost": 0.12,
        "residual_land_hbu": 0.04,
    },
}


class GlittersValuationEngine:
    """
    Production-grade, isolated multi-paradigm property valuation intelligence engine
    with strict sale/rental separation, tenant-scoped model weights, immutable data
    provenance, and causal conformal calibration.
    """

    def __init__(
        self,
        model_version: str = "glitters-ve-1.1.0-ke",
        config: Optional[ValuationMarketConfig] = None,
    ) -> None:
        self.model_version = model_version
        self.config = config or ValuationMarketConfig()
        self.validator = DataValidationEngine()
        self.fe = FeatureEngineeringPipeline(config=self.config)
        self.comp_engine = ComparableSalesEngine(config=self.config)
        self.hedonic_model = HedonicValuationModel(config=self.config)
        self.spatial_model = SpatialGPValuationModel(config=self.config)
        self.ml_model = GradientBoostedValuationEnsemble(config=self.config)
        self.income_engine = IncomeValuationEngine(config=self.config)
        self.cost_engine = CostValuationEngine(config=self.config)
        self.land_engine = LandAndDevelopmentValuationEngine(config=self.config)
        self.uncertainty_engine = UncertaintyAndConfidenceEngine(target_confidence=0.90)
        self.explain_engine = ValuationExplainabilityEngine()

        self.sale_reference_pool: List[MarketTransaction] = []
        self.rental_reference_pool: List[MarketTransaction] = []
        self.feature_cov_inv: Optional[np.ndarray] = None
        self.feature_center: Optional[np.ndarray] = None
        self.feature_scale: Optional[np.ndarray] = None
        self.market_volatility_regime: float = 1.0
        self.model_provenance: Optional[ModelProvenance] = None
        self.is_trained: bool = False

    @staticmethod
    def _compute_dataset_sha256(transactions: Iterable[MarketTransaction]) -> str:
        hasher = hashlib.sha256()
        for tx in sorted(transactions, key=lambda t: t.transaction_id):
            payload = (
                f"{tx.transaction_id}|{tx.tenant_org_id}|{tx.evidence_type.value}|"
                f"{tx.observed_price_kes:.2f}|{tx.transaction_timestamp_days:.2f}|{tx.is_synthetic}\n"
            )
            hasher.update(payload.encode("utf-8"))
        return hasher.hexdigest()

    def fit(
        self,
        transactions: List[MarketTransaction],
        tenant_org_id: str = "PUBLIC_REGISTRY",
        training_scope: Optional[ModelTrainingScope] = None,
        authorized_tenant_ids: Optional[Set[str]] = None,
        allow_synthetic_training: bool = False,
        calibrate_market_config: bool = False,
    ) -> Dict[str, Any]:
        """
        Offline training pipeline:
        1. Enforces `allow_synthetic_training=False` by default (CRITICAL 2): raises ValueError
           if synthetic observations are present and `allow_synthetic_training` is False.
        2. Binds trained weights to an explicit `ModelTrainingScope` and `authorized_tenant_ids` (CRITICAL 3).
        3. Separates capital sales (`clean_sales`) from rental evidence (`clean_rentals`) in global
           chronological order (CRITICAL 1 & 5).
        4. Performs a strictly chronological train/calibration split and calibrates conformal intervals
           using only historical transactions available at each calibration timestamp (CRITICAL 4 & 5).
        """
        any_input_synthetic = any(
            tx.is_synthetic or tx.property_record.is_synthetic or tx.tenant_org_id == "SYNTHETIC_SANDBOX"
            for tx in transactions
        )
        if any_input_synthetic and not allow_synthetic_training:
            raise PermissionError(
                "SYNTHETIC_TRAINING_FORBIDDEN: Input dataset contains synthetic records (is_synthetic=True), "
                "but allow_synthetic_training=False. Production models must never be trained on synthetic data. "
                "Pass allow_synthetic_training=True only in authorized experimental/benchmark runs."
            )

        # Determine model training scope and authorized tenants (CRITICAL 3)
        if training_scope is None:
            if authorized_tenant_ids is not None and len(authorized_tenant_ids) > 1:
                resolved_scope = ModelTrainingScope.AUTHORIZED_POOLED
            elif tenant_org_id not in ("PUBLIC_REGISTRY", "SYNTHETIC_SANDBOX"):
                resolved_scope = ModelTrainingScope.SINGLE_TENANT_PRIVATE
            else:
                resolved_scope = ModelTrainingScope.PUBLIC_REGISTRY_ONLY
        else:
            resolved_scope = training_scope

        if resolved_scope == ModelTrainingScope.PUBLIC_REGISTRY_ONLY:
            auth_tenants: Set[str] = {"PUBLIC_REGISTRY"}
        elif resolved_scope == ModelTrainingScope.SINGLE_TENANT_PRIVATE:
            auth_tenants = {tenant_org_id}
        else:
            auth_tenants = set(authorized_tenant_ids or {tenant_org_id})

        clean_sales, clean_rentals, audit_notes = self.validator.separate_and_filter_evidence(
            transactions=transactions,
            authorized_tenant_ids=auth_tenants,
            allow_public_pool=True,
            allow_synthetic=allow_synthetic_training,
        )

        if len(clean_sales) < 10:
            raise ValueError(
                f"Insufficient clean capital sale transactions ({len(clean_sales)}) to train engine "
                f"(rental records segregated: {len(clean_rentals)})."
            )

        if calibrate_market_config:
            self.config = ValuationMarketConfig.calibrate_from_transactions(clean_sales, base_config=self.config)
            self.fe = FeatureEngineeringPipeline(config=self.config)
            self.comp_engine = ComparableSalesEngine(config=self.config)
            self.hedonic_model = HedonicValuationModel(config=self.config)
            self.spatial_model = SpatialGPValuationModel(config=self.config)
            self.ml_model = GradientBoostedValuationEnsemble(config=self.config)
            self.income_engine = IncomeValuationEngine(config=self.config)
            self.cost_engine = CostValuationEngine(config=self.config)
            self.land_engine = LandAndDevelopmentValuationEngine(config=self.config)

        # CRITICAL 5: `clean_sales` is guaranteed sorted in global chronological order:
        # t_1 <= t_2 <= ... <= t_N.
        n_total = len(clean_sales)
        min_cal_req = self.uncertainty_engine.min_required_calibration_samples()
        desired_cal = max(min_cal_req, int(0.18 * n_total))
        # Ensure at least 8 training records remain for GBDT/Hedonic/Spatial fitting
        if n_total - desired_cal >= 8:
            n_cal = desired_cal
        else:
            n_cal = max(0, n_total - 8)

        if n_cal > 0:
            train_sales = clean_sales[:-n_cal]
            cal_sales = clean_sales[-n_cal:]
        else:
            train_sales = clean_sales
            cal_sales = []

        # Store chronological training pools
        self.sale_reference_pool = list(train_sales)
        max_train_day = train_sales[-1].transaction_timestamp_days
        self.rental_reference_pool = [
            r for r in clean_rentals if r.transaction_timestamp_days <= max_train_day
        ]

        # Fit statistical, spatial, and ML sub-models strictly on `train_sales`
        self.hedonic_model.fit(train_sales)
        self.spatial_model.fit(train_sales)
        self.ml_model.fit(train_sales)

        X_train = self.fe.transform_batch([t.property_record for t in train_sales])
        self.feature_center = np.mean(X_train, axis=0)
        self.feature_scale = np.where(np.std(X_train, axis=0) < 1e-6, 1.0, np.std(X_train, axis=0))
        Z_train = (X_train - self.feature_center) / self.feature_scale
        cov = np.cov(Z_train, rowvar=False) + np.eye(Z_train.shape[1]) * 0.05
        self.feature_cov_inv = np.linalg.pinv(cov)

        trained_on_synth = any(
            t.is_synthetic or t.property_record.is_synthetic or t.tenant_org_id == "SYNTHETIC_SANDBOX"
            for t in ( clean_sales + clean_rentals )
        )
        dataset_hash = self._compute_dataset_sha256(clean_sales + clean_rentals)

        # Provisional provenance during calibration pass
        self.model_provenance = ModelProvenance(
            model_version=self.model_version,
            training_scope=resolved_scope,
            authorized_tenant_ids=tuple(sorted(auth_tenants)),
            trained_on_synthetic_data=trained_on_synth,
            training_dataset_sha256=dataset_hash,
            num_sale_training_records=len(train_sales),
            num_rental_reference_records=len(clean_rentals),
            num_calibration_records=len(cal_sales),
            is_conformal_calibrated=False,
            calibration_method="CALIBRATING",
            min_training_timestamp_days=float(train_sales[0].transaction_timestamp_days),
            max_training_timestamp_days=float(max_train_day),
        )
        self.is_trained = True

        # CRITICAL 4 & 5: Causal Conformal Calibration Pass
        # Each calibration observation is evaluated at its own historical transaction_timestamp_days,
        # and only comparables with t_comp <= tx.transaction_timestamp_days are visible.
        y_true_cal: List[float] = []
        y_pred_cal: List[float] = []
        sigma_cal: List[float] = []
        z_cal_list: List[np.ndarray] = []

        # Progressive historical reference pool: starts with train_sales and appends prior cal_sales
        # strictly when their timestamp is <= current calibration timestamp
        rolling_sale_pool = list(train_sales)
        for tx in cal_sales:
            causal_comps = [
                c for c in rolling_sale_pool
                if c.transaction_timestamp_days <= tx.transaction_timestamp_days
            ]
            res = self.value_property(
                tx.property_record,
                custom_comparables=causal_comps,
                valuation_day=tx.transaction_timestamp_days,
                allow_synthetic_inference=allow_synthetic_training,
            )
            if (
                res.point_estimate_kes is not None
                and res.aleatoric_uncertainty_kes is not None
                and res.epistemic_uncertainty_kes is not None
            ):
                y_true_cal.append(tx.observed_price_kes)
                y_pred_cal.append(res.point_estimate_kes)
                sigma_cal.append(math.hypot(res.aleatoric_uncertainty_kes, res.epistemic_uncertainty_kes))
                x_raw = self.fe.extract_features(tx.property_record)
                z_cal_list.append((x_raw - self.feature_center) / self.feature_scale)
            rolling_sale_pool.append(tx)

        # After calibration is complete, all clean chronological sales and rentals can serve future inference
        self.sale_reference_pool = list(clean_sales)
        self.rental_reference_pool = list(clean_rentals)

        cal_features_arr = np.vstack(z_cal_list) if z_cal_list else None
        is_cal, q_mult, cal_method = self.uncertainty_engine.calibrate_conformal(
            np.array(y_true_cal, dtype=np.float64),
            np.array(y_pred_cal, dtype=np.float64),
            np.array(sigma_cal, dtype=np.float64),
            cal_features_std=cal_features_arr,
        )

        self.model_provenance = ModelProvenance(
            model_version=self.model_version,
            training_scope=resolved_scope,
            authorized_tenant_ids=tuple(sorted(auth_tenants)),
            trained_on_synthetic_data=trained_on_synth,
            training_dataset_sha256=dataset_hash,
            num_sale_training_records=len(train_sales),
            num_rental_reference_records=len(clean_rentals),
            num_calibration_records=len(y_true_cal),
            is_conformal_calibrated=is_cal,
            calibration_method=cal_method,
            min_training_timestamp_days=float(clean_sales[0].transaction_timestamp_days),
            max_training_timestamp_days=float(clean_sales[-1].transaction_timestamp_days),
        )

        return {
            "num_input_transactions": len(transactions),
            "num_clean_sale_transactions": len(clean_sales),
            "num_clean_rental_transactions": len(clean_rentals),
            "num_train": len(train_sales),
            "num_calibration": len(y_true_cal),
            "is_conformal_calibrated": is_cal,
            "calibration_method": cal_method,
            "conformal_multiplier": round(q_mult, 4),
            "training_scope": resolved_scope.value,
            "trained_on_synthetic_data": trained_on_synth,
            "training_dataset_sha256": dataset_hash,
            "morans_i": round(self.spatial_model.morans_i, 4),
            "audit_notes_count": len(audit_notes),
        }

    def _compute_ood_mahalanobis(self, prop: PropertyRecord) -> Tuple[float, Optional[np.ndarray]]:
        if self.feature_center is None or self.feature_cov_inv is None or self.feature_scale is None:
            return 1.0, None
        x = self.fe.extract_features(prop)
        z = (x - self.feature_center) / self.feature_scale
        d_sq = float(z.T @ self.feature_cov_inv @ z) / float(len(x))
        return math.sqrt(max(0.0, d_sq)), z

    def _is_tenant_authorized_for_model(self, requesting_tenant_id: str) -> bool:
        """
        Remediation for CRITICAL 3:
        Checks whether `requesting_tenant_id` is authorized to use the trained model weights.
        - If the model was trained on `PUBLIC_REGISTRY_ONLY`, any tenant may query it.
        - If the model was trained on `SINGLE_TENANT_PRIVATE` or `AUTHORIZED_POOLED`, ONLY
          tenants in `model_provenance.authorized_tenant_ids` may query it.
        """
        if self.model_provenance is None:
            return True
        if self.model_provenance.training_scope == ModelTrainingScope.PUBLIC_REGISTRY_ONLY:
            return True
        return requesting_tenant_id in self.model_provenance.authorized_tenant_ids

    def value_property(
        self,
        prop: PropertyRecord,
        custom_comparables: Optional[List[MarketTransaction]] = None,
        custom_rental_comparables: Optional[List[MarketTransaction]] = None,
        valuation_day: float = 0.0,
        allow_synthetic_inference: bool = True,
    ) -> ValuationOutputContract:
        """
        Online single-property valuation inference with tenant-scope verification,
        synthetic provenance propagation, sale/rental evidence separation, and
        conformal uncertainty quantification.
        """
        model_is_synth = bool(self.model_provenance and self.model_provenance.trained_on_synthetic_data)

        # 1. Model-Level Tenant Isolation Gate (CRITICAL 3)
        if not self._is_tenant_authorized_for_model(prop.tenant_org_id):
            return ValuationOutputContract(
                property_id=prop.property_id,
                tenant_org_id=prop.tenant_org_id,
                status=ValuationStatus.REJECTED_UNAUTHORIZED_TENANT_SCOPE,
                currency="KES",
                point_estimate_kes=None,
                lower_bound_kes=None,
                upper_bound_kes=None,
                confidence_level=self.uncertainty_engine.target_confidence,
                is_conformal_calibrated=self.uncertainty_engine.is_conformal_calibrated,
                calibration_method=self.uncertainty_engine.calibration_method,
                aleatoric_uncertainty_kes=None,
                epistemic_uncertainty_kes=None,
                comparable_quality_score=0.0,
                valuation_reliability_score=0.0,
                sub_model_estimates={},
                ensemble_weights={},
                feature_attributions_kes={},
                selected_comparables=[],
                validation_flags=[],
                adversarial_alerts=[
                    f"MODEL_TENANT_ISOLATION_VIOLATION: Requesting tenant '{prop.tenant_org_id}' is not authorized "
                    f"to query model trained under scope '{self.model_provenance.training_scope.value if self.model_provenance else 'UNKNOWN'}' "
                    f"for tenants {list(self.model_provenance.authorized_tenant_ids) if self.model_provenance else []}."
                ],
                model_version=self.model_version,
                model_provenance=self.model_provenance,
                is_synthetic_evidence=prop.is_synthetic or model_is_synth,
            )

        # 2. Synthetic Provenance Gate in Production Mode (CRITICAL 2)
        if not allow_synthetic_inference and (prop.is_synthetic or model_is_synth):
            return ValuationOutputContract(
                property_id=prop.property_id,
                tenant_org_id=prop.tenant_org_id,
                status=ValuationStatus.REJECTED_SYNTHETIC_PROVENANCE_VIOLATION,
                currency="KES",
                point_estimate_kes=None,
                lower_bound_kes=None,
                upper_bound_kes=None,
                confidence_level=self.uncertainty_engine.target_confidence,
                is_conformal_calibrated=self.uncertainty_engine.is_conformal_calibrated,
                calibration_method=self.uncertainty_engine.calibration_method,
                aleatoric_uncertainty_kes=None,
                epistemic_uncertainty_kes=None,
                comparable_quality_score=0.0,
                valuation_reliability_score=0.0,
                sub_model_estimates={},
                ensemble_weights={},
                feature_attributions_kes={},
                selected_comparables=[],
                validation_flags=[],
                adversarial_alerts=[
                    "SYNTHETIC_PROVENANCE_BLOCKED: Model or subject property contains synthetic data while "
                    "allow_synthetic_inference=False."
                ],
                model_version=self.model_version,
                model_provenance=self.model_provenance,
                is_synthetic_evidence=True,
            )

        # 3. Validate Subject Property Physical, Coordinate, and Legal Title Status
        is_valid, val_flags, adv_alerts, completeness_score = self.validator.validate_subject_property(prop)

        if not is_valid:
            rej_status = (
                ValuationStatus.REJECTED_DISPUTED_TITLE
                if prop.title_status == TitleVerificationStatus.CAVEAT_DISPUTED
                else ValuationStatus.REJECTED_INVALID_INPUT
            )
            return ValuationOutputContract(
                property_id=prop.property_id,
                tenant_org_id=prop.tenant_org_id,
                status=rej_status,
                currency="KES",
                point_estimate_kes=None,
                lower_bound_kes=None,
                upper_bound_kes=None,
                confidence_level=self.uncertainty_engine.target_confidence,
                is_conformal_calibrated=self.uncertainty_engine.is_conformal_calibrated,
                calibration_method=self.uncertainty_engine.calibration_method,
                aleatoric_uncertainty_kes=None,
                epistemic_uncertainty_kes=None,
                comparable_quality_score=0.0,
                valuation_reliability_score=0.0,
                sub_model_estimates={},
                ensemble_weights={},
                feature_attributions_kes={},
                selected_comparables=[],
                validation_flags=val_flags,
                adversarial_alerts=adv_alerts,
                model_version=self.model_version,
                model_provenance=self.model_provenance,
                is_synthetic_evidence=prop.is_synthetic or model_is_synth,
            )

        # 4. Separate and filter candidate comparable pools (CRITICAL 1, 3, 5)
        raw_pool = custom_comparables if custom_comparables is not None else self.sale_reference_pool
        comp_sales_pool, extracted_rentals, comp_notes = self.validator.separate_and_filter_evidence(
            transactions=raw_pool,
            authorized_tenant_ids={prop.tenant_org_id},
            allow_public_pool=True,
            allow_synthetic=allow_synthetic_inference,
            max_timestamp_days=valuation_day,
        )
        val_flags.extend(comp_notes[:3])

        rental_pool = (
            custom_rental_comparables
            if custom_rental_comparables is not None
            else (self.rental_reference_pool + extracted_rentals)
        )

        sub_estimates: List[SubModelEstimate] = []

        # Approach 1: Comparable Sales Approach (strictly capital sales <= valuation_day)
        comp_est, cqs, top_comps = self.comp_engine.evaluate_comparables(prop, comp_sales_pool, valuation_day)
        if comp_est is not None:
            sub_estimates.append(comp_est)
        else:
            val_flags.append("ZERO_LOCAL_COMPARABLES: Relying on spatial/structural/cost/residual models.")

        # Approaches 2, 3, 4: Hedonic, Sparse Spatial GP, and GBDT
        if self.is_trained:
            sub_estimates.append(self.hedonic_model.predict_single(prop))
            sub_estimates.append(self.spatial_model.predict_single(prop))
            sub_estimates.append(self.ml_model.predict_single(prop))

        # Approach 5: Income Capitalization & DCF (consumes rental_pool!)
        inc_est = self.income_engine.value_property(
            prop, rental_comparables=rental_pool, valuation_day=valuation_day
        )
        if inc_est is not None:
            sub_estimates.append(inc_est)

        # Approach 6: Depreciated Replacement Cost (DRC)
        cost_est = self.cost_engine.value_property(prop)
        sub_estimates.append(cost_est)

        # Approach 7: Residual Land & Highest-and-Best-Use (HBU)
        land_est = self.land_engine.evaluate_residual_land_value(prop)
        if land_est is not None:
            if prop.building_area_sqm > 0 and (
                land_est.point_estimate_kes > 0.85 * cost_est.point_estimate_kes
                and prop.building_area_sqm < 0.25 * prop.land_area_sqm * prop.permitted_plot_ratio
            ):
                val_flags.append(
                    "HIGH_REDEVELOPMENT_POTENTIAL: Residual HBU value significantly exceeds existing structure DRC."
                )
            sub_estimates.append(land_est)

        # Out-of-Distribution (OOD) Mahalanobis check
        ood_dist, z_std = self._compute_ood_mahalanobis(prop)
        if ood_dist > 1.55:
            adv_alerts.append(
                f"OUT_OF_DISTRIBUTION_PROPERTY: Covariate Mahalanobis distance ({ood_dist:.2f}) indicates "
                "luxury/atypical architecture or emerging locality outside dense training support."
            )

        # Regime-Aware Precision-Weighted Ensemble
        regime_priors = CATEGORY_REGIME_PRIORS.get(prop.category, CATEGORY_REGIME_PRIORS[PropertyCategory.RESIDENTIAL])
        raw_weights = []
        for est in sub_estimates:
            cat_prior = regime_priors.get(est.approach_name, 0.10)
            if est.approach_name == "comparable_sales":
                cat_prior *= (0.35 + 0.95 * cqs)
            elif est.approach_name == "residual_land_hbu" and "HIGH_REDEVELOPMENT_POTENTIAL" in " ".join(val_flags):
                cat_prior *= 2.5

            rel_precision = 1.0 / max(0.04, (est.std_error_kes / max(1.0, est.point_estimate_kes)) ** 2)
            raw_weights.append(cat_prior * math.sqrt(rel_precision))

        w_arr = np.array(raw_weights, dtype=np.float64)
        w_arr = w_arr / np.sum(w_arr)

        # Quantify Uncertainty & Locally Adaptive Conformal Prediction Intervals
        point_est, low_b, high_b, aleatoric_std, epistemic_std, vrs, status = (
            self.uncertainty_engine.quantify_uncertainty(
                sub_estimates=sub_estimates,
                weights=w_arr,
                comparable_quality_score=cqs,
                data_completeness_score=completeness_score,
                ood_mahalanobis_distance=ood_dist,
                market_volatility_regime=self.market_volatility_regime,
                x_std=z_std,
            )
        )

        if not self.uncertainty_engine.is_conformal_calibrated:
            val_flags.append(
                f"UNCALIBRATED_PREDICTION_INTERVAL: {self.uncertainty_engine.calibration_method}"
            )

        if prop.category == PropertyCategory.DISTRESSED or prop.is_distressed_sale:
            fs_discount = min(0.35, max(0.10, prop.forced_sale_discount_cap))
            val_flags.append(
                f"DISTRESSED_FORCED_SALE_ADJUSTMENT: Applied {fs_discount*100:.1f}% liquidity haircut "
                "(aligned with Kenyan Land Act s.97 statutory reserve floor >= 75% of market value)."
            )
            point_est *= (1.0 - fs_discount)
            low_b *= (1.0 - fs_discount)
            high_b *= (1.0 - fs_discount)

        hedonic_attr = self.hedonic_model.feature_contributions_kes(prop) if self.is_trained else {}
        blended_attr = self.explain_engine.compute_blended_feature_attributions(hedonic_attr, point_est, prop)

        # CRITICAL 2: Propagate synthetic flag from subject, comparables, OR model training provenance
        any_synthetic = (
            prop.is_synthetic
            or model_is_synth
            or any(c.get("is_synthetic", False) for c in top_comps)
        )
        if any_synthetic:
            val_flags.append(
                "SYNTHETIC_PROVENANCE_ACTIVE: Valuation influenced by synthetic property, comparable, or model weights."
            )

        return ValuationOutputContract(
            property_id=prop.property_id,
            tenant_org_id=prop.tenant_org_id,
            status=status,
            currency="KES",
            point_estimate_kes=round(point_est, 2),
            lower_bound_kes=round(low_b, 2),
            upper_bound_kes=round(high_b, 2),
            confidence_level=self.uncertainty_engine.target_confidence,
            is_conformal_calibrated=self.uncertainty_engine.is_conformal_calibrated,
            calibration_method=self.uncertainty_engine.calibration_method,
            aleatoric_uncertainty_kes=round(aleatoric_std, 2),
            epistemic_uncertainty_kes=round(epistemic_std, 2),
            comparable_quality_score=round(cqs, 4),
            valuation_reliability_score=round(vrs, 4),
            sub_model_estimates={e.approach_name: round(e.point_estimate_kes, 2) for e in sub_estimates},
            ensemble_weights={e.approach_name: round(float(w), 4) for e, w in zip(sub_estimates, w_arr)},
            feature_attributions_kes=blended_attr,
            selected_comparables=top_comps,
            validation_flags=val_flags,
            adversarial_alerts=adv_alerts,
            model_version=self.model_version,
            model_provenance=self.model_provenance,
            is_synthetic_evidence=any_synthetic,
        )
