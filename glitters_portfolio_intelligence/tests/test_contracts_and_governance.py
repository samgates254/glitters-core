from __future__ import annotations

import unittest
from uuid import uuid4
import numpy as np

from glitters_contracts import (
    AuthorizedCallerContext,
    ConfidenceTier,
    ContractVersion,
    OrganizationContext,
    PortfolioInvestmentBridge,
)

from glitters_portfolio_intelligence.domain.entities import Portfolio, PropertyAsset
from glitters_portfolio_intelligence.domain.enums import (
    AssetCategory,
    GeographicSubmarket,
    OptimizationStatus,
    SolverBackendType,
)
from glitters_portfolio_intelligence.governance.committee_gate import InvestmentDecisionGovernanceGate
from glitters_portfolio_intelligence.integration.upstream_adapters import (
    UpstreamEngineDataFeed,
    UpstreamIntegrationAdapter,
)
from glitters_portfolio_intelligence.optimization.solver_backends import SolverExecutionResult


class TestContractsAndGovernance(unittest.TestCase):
    def setUp(self):
        self.tenant_id = uuid4()
        self.org_context = OrganizationContext(
            tenant_org_id=self.tenant_id,
            organization_name="GLITTERS Capital Partners",
        )
        self.caller = AuthorizedCallerContext(
            caller_user_id=uuid4(),
            tenant_org_id=self.tenant_id,
            granted_permissions=frozenset(["GLITTERS_EXECUTE_ALGORITHMS", "GLITTERS_VIEW_FINANCIALS"]),
            org_context=self.org_context,
        )
        self.asset = PropertyAsset(
            asset_id="PROP-01",
            name="Westlands Hub",
            category=AssetCategory.COMMERCIAL_OFFICE,
            submarket=GeographicSubmarket.WESTLANDS,
            acquisition_valuation_cents=100_000_000_00,
            current_valuation_cents=120_000_000_00,
            monthly_gross_potential_rent_cents=1_000_000_00,
            monthly_operating_expense_cents=200_000_00,
            annual_baseline_vacancy_rate=0.08,
            annual_baseline_credit_loss_rate=0.03,
            annual_debt_service_cents=3_000_000_00,
            current_loan_balance_cents=30_000_000_00,
            annual_routine_maintenance_cents=500_000_00,
            planned_capex_cents=1_000_000_00,
        )
        self.portfolio = Portfolio(
            portfolio_id="PORT-KE-001",
            tenant_org_id=str(self.tenant_id),
            assets=(self.asset,),
            cash_reserves_cents=10_000_000_00,
        )

    def test_governance_decision_envelope_and_human_approval(self):
        dummy_result = SolverExecutionResult(
            solver_type=SolverBackendType.HIGHS_SCIPY,
            solver_version="HiGHS 1.5",
            status=OptimizationStatus.OPTIMAL,
            is_optimal=True,
            optimal_weights=np.array([0.95]),
            expected_return=0.125,
            cvar_alpha=0.045,
            var_alpha=0.030,
            objective_value=0.025,
            optimality_gap=0.0,
            solve_time_seconds=0.012,
            active_constraints=("Budget constraint",),
        )

        envelope = InvestmentDecisionGovernanceGate.create_recommendation_envelope(
            portfolio=self.portfolio,
            solver_result=dummy_result,
            caller=self.caller,
        )

        self.assertEqual(envelope.contract_version, ContractVersion.V1_1)
        self.assertIsInstance(envelope.payload, PortfolioInvestmentBridge)
        self.assertEqual(envelope.payload.portfolio_id, "PORT-KE-001")
        # In R1.2: Omitted provenance defaults to EXPERT_ASSUMPTION -> MODERATE_CONFIDENCE
        self.assertEqual(envelope.confidence_tier, ConfidenceTier.MODERATE_CONFIDENCE)

        # Human Approval Requirement enforced
        self.assertTrue(envelope.requires_human_approval)
        self.assertIsNotNone(envelope.human_approval_requirement)
        self.assertEqual(envelope.human_approval_requirement.approval_role, "INVESTMENT_COMMITTEE")

        # Cryptographic audit event generated
        self.assertEqual(len(envelope.audit_event.result_hash_sha256), 64)
        self.assertEqual(envelope.audit_event.engine_id, "glitters_portfolio_intelligence")

    def test_upstream_adapter_graceful_missing_data(self):
        # When upstream engines are offline / missing
        missing_feed = UpstreamEngineDataFeed(
            asset_id="PROP-01",
            is_engine02_available=False,
            is_engine04_available=False,
        )
        updated_asset = UpstreamIntegrationAdapter.apply_upstream_feed(self.asset, missing_feed)
        # Keeps original baseline values cleanly
        self.assertEqual(updated_asset.current_valuation_cents, self.asset.current_valuation_cents)
        self.assertEqual(updated_asset.monthly_gross_potential_rent_cents, self.asset.monthly_gross_potential_rent_cents)

        # When upstream engine 02 provides updated valuation
        active_feed = UpstreamEngineDataFeed(
            asset_id="PROP-01",
            valuation_cents=135_000_000_00,
            is_engine02_available=True,
        )
        updated_asset2 = UpstreamIntegrationAdapter.apply_upstream_feed(self.asset, active_feed)
        self.assertEqual(updated_asset2.current_valuation_cents, 135_000_000_00)


if __name__ == "__main__":
    unittest.main()
