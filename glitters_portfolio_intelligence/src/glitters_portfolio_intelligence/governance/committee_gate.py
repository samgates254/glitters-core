from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Optional, Sequence, Tuple
from uuid import UUID, uuid4

from glitters_contracts import (
    AbstentionReason,
    AlgorithmDecisionEnvelope,
    AlgorithmExecutionEnvelope,
    AlgorithmicExecutionStatus,
    AuthorizedCallerContext,
    ConfidenceTier,
    ContractVersion,
    DecisionExplanation,
    EngineAuditEvent,
    HumanApprovalRequirement,
    PortfolioInvestmentBridge,
    UncertaintyBounds,
)

from ..domain.entities import Portfolio, PropertyAsset
from ..optimization.solver_backends import SolverExecutionResult


class InvestmentDecisionGovernanceGate:
    """
    Enforces strict GLITTERS governance:
    Engine 08 is strictly advisory and CANNOT execute transactions or post financial entries.
    All binding capital deployment or rebalancing requires Investment Committee sign-off.
    """

    @classmethod
    def create_recommendation_envelope(
        cls,
        portfolio: Portfolio,
        solver_result: SolverExecutionResult,
        caller: AuthorizedCallerContext,
        policy_id: str = "POL-PORTFOLIO-ALLOCATION-2026",
        threshold_for_committee_cents: int = 10_000_000_00,  # KES 10M
    ) -> AlgorithmDecisionEnvelope[PortfolioInvestmentBridge]:
        """
        Packages solver results into an auditable AlgorithmDecisionEnvelope[PortfolioInvestmentBridge].
        """
        now = datetime.now(timezone.utc)
        execution_id = uuid4()
        decision_id = uuid4()

        # Build holding pairs: (asset_id, weight)
        holdings: list[tuple[str, float]] = []
        for i, a in enumerate(portfolio.assets):
            if i < len(solver_result.optimal_weights):
                w = float(solver_result.optimal_weights[i])
                holdings.append((a.asset_id, round(w, 4)))

        # Determine if committee approval is required
        total_val_cents = portfolio.total_portfolio_value_cents
        rebalancing_needed = any(abs(w - (1.0 / max(1, len(portfolio.assets)))) > 0.05 for _, w in holdings)
        requires_committee = (
            total_val_cents >= threshold_for_committee_cents
            or rebalancing_needed
            or not solver_result.is_optimal
        )

        bridge_dto = PortfolioInvestmentBridge(
            portfolio_id=portfolio.portfolio_id,
            tenant_org_id=portfolio.tenant_org_id,
            recommended_holdings=tuple(holdings),
            expected_annual_return=solver_result.expected_return,
            portfolio_cvar_95=solver_result.cvar_alpha,
            portfolio_volatility=abs(solver_result.cvar_alpha * 0.75),  # Parametric volatility proxy
            total_capital_deployed_cents=total_val_cents,
            rebalancing_required=rebalancing_needed,
            requires_investment_committee_approval=requires_committee,
            generated_at_utc=now,
        )

        approval_req = HumanApprovalRequirement(
            requires_human_approval=requires_committee,
            approval_role="INVESTMENT_COMMITTEE",
            approval_reason="Capital deployment or portfolio rebalancing requires human fiduciary sign-off.",
            escalation_timeout_hours=48,
        )

        payload_bytes = json.dumps({
            "portfolio_id": bridge_dto.portfolio_id,
            "expected_return": bridge_dto.expected_annual_return,
            "cvar_95": bridge_dto.portfolio_cvar_95,
            "total_capital_cents": bridge_dto.total_capital_deployed_cents,
        }, sort_keys=True).encode("utf-8")
        result_hash = hashlib.sha256(payload_bytes).hexdigest()

        audit_event = EngineAuditEvent(
            event_id=uuid4(),
            execution_id=execution_id,
            tenant_org_id=caller.tenant_org_id,
            timestamp_utc=now,
            engine_id="glitters_portfolio_intelligence",
            action_type="GENERATE_PORTFOLIO_RECOMMENDATION",
            result_hash_sha256=result_hash,
            provenance_hash_sha256=hashlib.sha256(b"engine08_provenance_v0.8.0").hexdigest(),
            requires_human_approval=requires_committee,
        )

        status_mapped = (
            AlgorithmicExecutionStatus.OPTIMAL
            if solver_result.is_optimal
            else AlgorithmicExecutionStatus.INFEASIBLE
        )

        explanation = DecisionExplanation(
            summary_narrative=(
                f"Optimized portfolio recommendation for {len(portfolio.assets)} assets using Rockafellar-Uryasev Mean-CVaR. "
                f"Expected annual return: {solver_result.expected_return:.2%}, CVaR_95: {solver_result.cvar_alpha:.2%}."
            ),
            feature_attributions={"expected_return": solver_result.expected_return, "cvar_95": solver_result.cvar_alpha},
            active_constraints=list(solver_result.active_constraints),
        )

        return AlgorithmDecisionEnvelope[PortfolioInvestmentBridge](
            decision_id=decision_id,
            execution_id=execution_id,
            tenant_org_id=caller.tenant_org_id,
            engine_id="glitters_portfolio_intelligence",
            engine_version="0.8.0",
            status=status_mapped,
            confidence_tier=ConfidenceTier.HIGH_CONFIDENCE if solver_result.is_optimal else ConfidenceTier.WEAKLY_SUPPORTED,
            uncertainty=UncertaintyBounds(
                point_estimate=solver_result.expected_return,
                lower_bound=solver_result.expected_return - solver_result.cvar_alpha,
                upper_bound=solver_result.expected_return + solver_result.cvar_alpha,
                confidence_level=0.95,
                interval_type="ROCKAFELLAR_CVAR_95",
                effective_sample_size=100.0,
                epistemic_status="CONVERGED_OPTIMAL" if solver_result.is_optimal else "FAILED_OR_INFEASIBLE",
            ),
            explanation=explanation,
            payload=bridge_dto,
            requires_human_approval=requires_committee,
            audit_event=audit_event,
            result_hash_sha256=result_hash,
            execution_duration_ms=solver_result.solve_time_seconds * 1000.0,
            contract_version=ContractVersion.V1_1,
            abstention_reason=AbstentionReason.NONE if solver_result.is_optimal else AbstentionReason.INFEASIBLE,
            human_approval_requirement=approval_req,
        )
