import unittest
from datetime import datetime, timezone, date
from uuid import uuid4
import hashlib

from glitters_contracts import (
    ContractVersion,
    OrganizationContext,
    AuthorizedCallerContext,
    ProvenanceMetadata,
    IdempotencyMetadata,
    HumanApprovalRequirement,
    AbstentionReason,
    AlgorithmExecutionEnvelope,
    AlgorithmDecisionEnvelope,
    AlgorithmicExecutionStatus,
    ConfidenceTier,
    UncertaintyBounds,
    DecisionExplanation,
    EngineAuditEvent,
    TenantCreditEvidence,
    TitleDeedEvidence,
    TenantRiskHandoffBridge,
    PortfolioInvestmentBridge,
    RecommendationCandidateHandoffBridge,
    PredictiveMaintenanceHandoffBridge,
    schemas_v1,
)


class TestV1_1Contracts(unittest.TestCase):
    def setUp(self):
        self.tenant_id = uuid4()
        self.org_context = OrganizationContext(
            tenant_org_id=self.tenant_id,
            organization_name="GLITTERS Capital Real Estate LLC",
            subscription_tier="ENTERPRISE",
            authorized_features=frozenset(["TENANT_SCORING", "PORTFOLIO_OPTIMIZATION", "RECOMMENDATIONS", "MAINTENANCE"]),
        )
        self.caller = AuthorizedCallerContext(
            caller_user_id=uuid4(),
            tenant_org_id=self.tenant_id,
            granted_permissions=frozenset(["GLITTERS_EXECUTE_ALGORITHMS", "GLITTERS_APPROVE_DECISIONS"]),
            org_context=self.org_context,
        )

    def test_organization_context_and_caller(self):
        self.assertEqual(self.caller.tenant_org_id, self.tenant_id)
        self.assertIsNotNone(self.caller.org_context)
        self.assertTrue(self.caller.has_permission("GLITTERS_EXECUTE_ALGORITHMS"))
        self.assertFalse(self.caller.has_permission("UNAUTHORIZED_PERMISSION"))

    def test_v1_1_execution_envelope(self):
        prov = ProvenanceMetadata(
            source_datasets=("tenants_v1.parquet", "credit_bureau_sync.parquet"),
            dataset_sha256_hashes=(hashlib.sha256(b"d1").hexdigest(), hashlib.sha256(b"d2").hexdigest()),
            model_version="weibull_ridge_v1.0.0",
            model_weights_sha256=hashlib.sha256(b"weights").hexdigest(),
            is_synthetic_training=False,
            is_synthetic_inference=False,
            execution_environment="ISOLATED_CONTAINER",
        )
        envelope = AlgorithmExecutionEnvelope(
            execution_id=uuid4(),
            tenant_org_id=self.tenant_id,
            authorized_caller=self.caller,
            engine_id="glitters_tenant_risk",
            engine_version="1.0.0",
            policy_id="risk_policy_ke_2026",
            policy_version=1,
            idempotency_key="idemp_12345",
            created_at_utc=datetime.now(timezone.utc),
            input_snapshot={"applicant_id": "app_999"},
            input_snapshot_sha256=hashlib.sha256(b"app_999").hexdigest(),
            contract_version=ContractVersion.V1_1,
            provenance=prov,
        )
        self.assertEqual(envelope.contract_version, ContractVersion.V1_1)
        self.assertFalse(envelope.provenance.is_synthetic_training)
        self.assertEqual(len(envelope.provenance.dataset_sha256_hashes), 2)

    def test_v1_1_decision_envelope_abstention_and_human_approval(self):
        approval = HumanApprovalRequirement(
            requires_human_approval=True,
            approval_role="SENIOR_RISK_UNDERWRITER",
            approval_reason="Adverse CRB listing detected on high-rent applicant",
            escalation_timeout_hours=24,
        )
        audit = EngineAuditEvent(
            event_id=uuid4(),
            execution_id=uuid4(),
            tenant_org_id=self.tenant_id,
            timestamp_utc=datetime.now(timezone.utc),
            engine_id="glitters_tenant_risk",
            action_type="EVALUATE_TENANT_RISK",
            result_hash_sha256=hashlib.sha256(b"out").hexdigest(),
            provenance_hash_sha256=hashlib.sha256(b"prov").hexdigest(),
            requires_human_approval=True,
        )
        decision = AlgorithmDecisionEnvelope(
            decision_id=uuid4(),
            execution_id=audit.execution_id,
            tenant_org_id=self.tenant_id,
            engine_id="glitters_tenant_risk",
            engine_version="1.0.0",
            status=AlgorithmicExecutionStatus.OPTIMAL,
            confidence_tier=ConfidenceTier.HIGH_CONFIDENCE,
            uncertainty=UncertaintyBounds(
                point_estimate=0.92,
                lower_bound=0.85,
                upper_bound=0.96,
                confidence_level=0.95,
                interval_type="CONFORMAL",
                effective_sample_size=120.0,
                epistemic_status="ROBUST",
            ),
            explanation=DecisionExplanation(
                summary_narrative="Robust continuous income and pristine repayment history",
                feature_attributions={"income_stability": 0.45, "debt_to_income": 0.35},
            ),
            payload={"score": 780, "tier": "LOW_RISK"},
            requires_human_approval=True,
            audit_event=audit,
            result_hash_sha256=hashlib.sha256(b"res").hexdigest(),
            execution_duration_ms=45.2,
            contract_version=ContractVersion.V1_1,
            abstention_reason=AbstentionReason.NONE,
            human_approval_requirement=approval,
        )
        self.assertEqual(decision.contract_version, ContractVersion.V1_1)
        self.assertTrue(decision.human_approval_requirement.requires_human_approval)
        self.assertEqual(decision.human_approval_requirement.approval_role, "SENIOR_RISK_UNDERWRITER")

    def test_tenant_credit_and_title_evidence_verification(self):
        # Valid CRB evidence
        valid_crb = TenantCreditEvidence(
            applicant_id="app_123",
            crb_listing_status="PERFORMING",
            crb_score=750,
            report_reference="CRB-KEN-2026-9988",
            verified_at_utc=datetime.now(timezone.utc),
            crb_provider_hash_sha256=hashlib.sha256(b"metropol_signed_report").hexdigest(),
        )
        self.assertTrue(valid_crb.verify())

        # Invalid CRB evidence (invalid hash length)
        invalid_crb = TenantCreditEvidence(
            applicant_id="app_123",
            crb_listing_status="PERFORMING",
            crb_score=750,
            report_reference="CRB-KEN-2026-9988",
            verified_at_utc=datetime.now(timezone.utc),
            crb_provider_hash_sha256="short_invalid_hash",
        )
        self.assertFalse(invalid_crb.verify())

        # Valid Title Deed evidence
        valid_title = TitleDeedEvidence(
            title_number="NAIROBI/BLOCK102/450",
            registry_name="Nairobi Land Registry",
            registered_owner_hash=hashlib.sha256(b"Owner Name").hexdigest(),
            encumbrances_search_reference="SEARCH-NLIMS-2026-8812",
            verified_at_utc=datetime.now(timezone.utc),
            search_certificate_sha256=hashlib.sha256(b"nlims_certificate_pdf").hexdigest(),
        )
        self.assertTrue(valid_title.verify())

    def test_new_cross_engine_bridges(self):
        now = datetime.now(timezone.utc)
        risk_bridge = TenantRiskHandoffBridge(
            applicant_id="app_555",
            application_id="lease_app_001",
            tenant_org_id=str(self.tenant_id),
            risk_score=85.5,
            risk_tier="LOW_RISK",
            default_probability_12m=0.018,
            recommended_max_rent_cents=120_000_00,
            recommended_deposit_months=1,
            is_eligible=True,
            requires_human_review=False,
            adverse_factors=(),
            scoring_timestamp_utc=now,
        )
        self.assertEqual(risk_bridge.risk_tier, "LOW_RISK")
        self.assertTrue(risk_bridge.is_eligible)

        portfolio_bridge = PortfolioInvestmentBridge(
            portfolio_id="port_alpha",
            tenant_org_id=str(self.tenant_id),
            recommended_holdings=(("prop_01", 0.4), ("prop_02", 0.35), ("prop_03", 0.25)),
            expected_annual_return=0.142,
            portfolio_cvar_95=-0.045,
            portfolio_volatility=0.082,
            total_capital_deployed_cents=500_000_000_00,
            rebalancing_required=True,
            requires_investment_committee_approval=True,
            generated_at_utc=now,
        )
        self.assertEqual(len(portfolio_bridge.recommended_holdings), 3)

        rec_bridge = RecommendationCandidateHandoffBridge(
            applicant_id="app_555",
            property_id="prop_01",
            utility_score=0.915,
            rank=1,
            affordability_ratio=0.28,
            preference_match_tier="PERFECT_MATCH",
            explanation_summary="Exact match on budget, bedrooms, and location.",
        )
        self.assertEqual(rec_bridge.rank, 1)

        maint_bridge = PredictiveMaintenanceHandoffBridge(
            asset_id="asset_lift_01",
            component_id="comp_motor_bearings",
            component_type="ELEVATOR_HOIST_MOTOR",
            current_health_index=0.62,
            failure_probability_30d=0.28,
            estimated_remaining_useful_life_days=45,
            estimated_repair_cost_cents=180_000_00,
            maintenance_urgency="URGENT",
            recommended_work_order_type="BEARING_REPLACEMENT",
            requires_immediate_dispatch=False,
            assessed_at_utc=now,
        )
        self.assertEqual(maint_bridge.maintenance_urgency, "URGENT")

    def test_schemas_v1_pydantic_models(self):
        profile = schemas_v1.UserPreferenceProfile(
            user_id="usr_001",
            tenant_org_id=str(self.tenant_id),
            search_intent=schemas_v1.SearchIntent.RENTAL_DISCOVERY,
            transaction_type=schemas_v1.TransactionType.RENTAL_LONG_TERM,
            allowed_categories={schemas_v1.PropertyCategory.APARTMENT},
            budget=schemas_v1.BudgetPreference(
                min_budget_kes=50000.0,
                target_budget_kes=80000.0,
                max_budget_kes=100000.0,
                is_hard_cap=True,
                weight=2.0,
            ),
            location=schemas_v1.LocationPreference(
                preferred_zones={"Westlands", "Parklands"},
                anchor_coordinates=schemas_v1.GeoPoint(lat=-1.2683, lon=36.8111),
                ideal_radius_km=3.0,
                max_radius_km=5.0,
                is_hard_radius=False,
                tier=schemas_v1.PreferenceTier.STRONG,
                weight=1.8,
            ),
            space=schemas_v1.SpacePreference(
                min_bedrooms=2,
                bedrooms_tier=schemas_v1.PreferenceTier.HARD,
            ),
        )
        self.assertEqual(profile.search_intent, schemas_v1.SearchIntent.RENTAL_DISCOVERY)
        self.assertEqual(profile.space.min_bedrooms, 2)
        self.assertEqual(profile.budget.target_budget_kes, 80000.0)


if __name__ == "__main__":
    unittest.main()
