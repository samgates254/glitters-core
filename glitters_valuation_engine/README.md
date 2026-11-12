# GLITTERS Valuation Engine (`glitters_valuation_engine` v1.1.0)

**Phase 1 Release: Evidence Integrity, Multi-Tenant Model Isolation & Statistical Correctness**

`glitters_valuation_engine` is an isolated, Django-ORM-independent Python package providing multi-paradigm property valuation, uncertainty quantification, and market intelligence for Kenya's real estate ecosystem.

## Phase 1 Audit Remediations Implemented
1. **Strict Sale vs. Rental Evidence Separation (CRITICAL 1)**: Capital sale observations (`VERIFIED_STAMP_DUTY_SALE`, `VERIFIED_CONTRACT_SALE`, `VALUER_HISTORICAL_RECORD`, `ASKING_LISTING_PRICE`) and rental observations (`RENTAL_LEASE_CONTRACT`, `RENTAL_ASKING_QUOTE`) are strictly segregated. Attempted injection of rental observations into comparable sales, hedonic, spatial GP, or ML capital-value pipelines is rejected, while rental observations are routed into `IncomeValuationEngine.estimate_market_rent_from_comparables()` with explicit period conversions.
2. **Immutable Synthetic Data Provenance (CRITICAL 2)**: `GlittersValuationEngine.fit()` rejects synthetic records (`is_synthetic=True`) by default (`allow_synthetic_training=False`). Synthetic records carry `tenant_org_id="SYNTHETIC_SANDBOX"` rather than `"PUBLIC_REGISTRY"`, and every `ValuationOutputContract` records `ModelProvenance` (`trained_on_synthetic_data`, `training_dataset_sha256`, `synthetic_evidence_influenced_valuation`).
3. **Model-Level Multi-Tenant Isolation (CRITICAL 3)**: Fitted models bind to an immutable `ModelTrainingScope` (`PUBLIC_REGISTRY_ONLY`, `SINGLE_TENANT_PRIVATE`, or `AUTHORIZED_POOLED`) with an explicit `authorized_tenant_ids` set. Valuing a property for Tenant B using a model trained on Tenant A's private data is blocked with `ValuationStatus.REJECTED_UNAUTHORIZED_TENANT_SCOPE`.
4. **Finite-Sample & Locally Adaptive Conformal Calibration (CRITICAL 4)**: Implements exact order-statistic conformal calibration $k = \lceil (n_{\text{cal}} + 1)(1 - \alpha) \rceil$, requiring $n_{\text{cal}} \ge \lceil (1 - \alpha)/\alpha \rceil$ (e.g., $n_{\text{cal}} \ge 9$ for $\alpha = 0.10$). When $n_{\text{cal}}$ is insufficient, `is_conformal_calibrated=False` is explicitly reported and conservative fallback intervals are flagged. Implements locally weighted conformal multipliers $\hat{q}_{1-\alpha}(\mathbf{x}_*)$ scaled by local KNN residual dispersion.
5. **Chronological Holdout & Zero Temporal Lookahead Leakage (CRITICAL 5)**: Deduplication and locality MAD outlier screening preserve global chronological order ($t_1 \le t_2 \le \dots \le t_N$). Calibration splits occur strictly along the time axis after training, and historical calibration predictions only access reference comparables with $t_{\text{comp}} \le t_{\text{cal}}$.
6. **Configurable Market Priors, Sparse Inducing-Point Spatial GP & Default Unverified Title**:
   - All market parameters live in `ValuationMarketConfig` (`config.py`) with `calibrate_from_transactions()` to estimate elasticities and asking spreads from empirical data.
   - `SpatialGPValuationModel` uses Nyström / K-means inducing points ($M \le 64$) for $\mathcal{O}(N M^2)$ training and $\mathcal{O}(M)$ inference plus vectorized Haversine distance matrices.
   - `PropertyRecord.title_status` defaults to `TitleVerificationStatus.UNVERIFIED_MISSING` and requires a `TitleVerificationMetadata` record (`registry_reference`, `verification_source`, `verified_timestamp_iso`, `evidence_document_hash`) before `VERIFIED_CLEAN` is honoured without penalty.

## Installation & Testing

```bash
pip install --dry-run --no-build-isolation --no-deps .
python3 -m unittest discover -s glitters_valuation_engine/tests -t . -v
```
