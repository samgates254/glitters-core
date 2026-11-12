from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Optional, Sequence, Tuple
from uuid import UUID, uuid4
import numpy as np

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
from ..domain.enums import OptimizationStatus, ScenarioProvenanceKind
from ..optimization.solver_backends import SolverExecutionResult


class InvestmentDecisionGovernanceGate:
    """
    Enforces strict GLITTERS governance:
    Engine 08 is strictly advisory and CANNOT execute transactions or post financial entries.
    All binding capital deployment or rebalancing requires Investment Committee sign-off.
    Enforces tenant organization isolation and auditable algorithmic decision envelopes.
    """

    @classmethod
    def create_recommendation_envelope(
        cls,
        portfolio: Portfolio,
        solver_result: SolverExecutionResult,
        caller: AuthorizedCallerContext,
        assets: Optional[Sequence[PropertyAsset]] = None,
        scenario_returns: Optional[np.ndarray] = None,
        scenario_probabilities: Optional[np.ndarray] = None,
        provenance: Optional[ScenarioProvenanceKind] = None,
        policy_id: str = "POL-PORTFOLIO-ALLOCATION-2026",
        threshold_for_committee_cents: int = 10_000_000_00,  # KES 10M
    ) -> AlgorithmDecisionEnvelope[PortfolioInvestmentBridge]:
        """
        Packages solver results into an auditable AlgorithmDecisionEnvelope[PortfolioInvestmentBridge].
        Strictly enforces tenant isolation between caller and portfolio.
        Computes empirical scenario volatility and effective sample size.
        """
        # 1. Enforce strict tenant isolation boundary
        if str(caller.tenant_org_id) != str(portfolio.tenant_org_id):
            raise ValueError(
                f"Tenant organization boundary violation: caller tenant '{caller.tenant_org_id}' "
                f"does not match portfolio tenant '{portfolio.tenant_org_id}'"
            )

        now = datetime.now(timezone.utc)
        execution_id = uuid4()
        decision_id = uuid4()

        # 2. Map holdings strictly from provided assets or portfolio assets
        target_assets = assets if assets is not None else portfolio.assets
        holdings: list[tuple[str, float]] = []
        for i, a in enumerate(target_assets):
            if i < len(solver_result.optimal_weights):
                w = float(solver_result.optimal_weights[i])
                holdings.append((a.asset_id, round(w, 4)))

        # 3. Compute empirical scenario volatility and effective sample size
        n_scens = len(scenario_probabilities) if scenario_probabilities is not None else 0
        if (
            scenario_returns is not None
            and scenario_probabilities is not None
            and len(solver_result.optimal_weights) == scenario_returns.shape[0]
            and n_scens > 0
        ):
            # Portfolio scenario returns: R_s^{port} = sum_i x_i R_{is}
            port_scen_returns = solver_result.optimal_weights @ scenario_returns
            mu = float(np.sum(scenario_probabilities * port_scen_returns))
            var = float(np.sum(scenario_probabilities * ((port_scen_returns - mu) ** 2)))
            portfolio_volatility = float(np.sqrt(max(0.0, var)))
            eff_sample_size = float(1.0 / np.sum(scenario_probabilities ** 2))
        else:
            # Fallback to conservative CVaR-derived risk estimate if raw scenarios omitted
            portfolio_volatility = abs(solver_result.cvar_alpha)
            eff_sample_size = float(max(1, len(holdings)))

        # 4. Determine if committee approval is required
        total_val_cents = portfolio.total_portfolio_value_cents
        rebalancing_needed = any(abs(w - (1.0 / max(1, len(target_assets)))) > 0.05 for _, w in holdings)
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
            portfolio_volatility=portfolio_volatility,
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

        # 5. Canonical SHA256 decision payload hash
        payload_dict = {
            "portfolio_id": bridge_dto.portfolio_id,
            "tenant_org_id": str(bridge_dto.tenant_org_id),
            "expected_return": round(bridge_dto.expected_annual_return, 6),
            "cvar_95": round(bridge_dto.portfolio_cvar_95, 6),
            "volatility": round(bridge_dto.portfolio_volatility, 6),
            "total_capital_cents": bridge_dto.total_capital_deployed_cents,
            "holdings": [[aid, round(w, 4)] for aid, w in bridge_dto.recommended_holdings],
        }
        payload_bytes = json.dumps(payload_dict, sort_keys=True).encode("utf-8")
        result_hash = hashlib.sha256(payload_bytes).hexdigest()

        # Dynamic provenance hash
        prov_data = f"engine08_{portfolio.portfolio_id}_{portfolio.tenant_org_id}_{solver_result.solver_type.value}_{solver_result.solver_version}_{policy_id}"
        prov_hash = hashlib.sha256(prov_data.encode("utf-8")).hexdigest()

        audit_event = EngineAuditEvent(
            event_id=uuid4(),
            execution_id=execution_id,
            tenant_org_id=caller.tenant_org_id,
            timestamp_utc=now,
            engine_id="glitters_portfolio_intelligence",
            action_type="GENERATE_PORTFOLIO_RECOMMENDATION",
            result_hash_sha256=result_hash,
            provenance_hash_sha256=prov_hash,
            requires_human_approval=requires_committee,
        )

        # 6. Map OptimizationStatus to AlgorithmicExecutionStatus and AbstentionReason
        status_map = {
            OptimizationStatus.OPTIMAL: (AlgorithmicExecutionStatus.OPTIMAL, AbstentionReason.NONE),
            OptimizationStatus.FEASIBLE: (AlgorithmicExecutionStatus.FEASIBLE_APPROXIMATE, AbstentionReason.NONE),
            OptimizationStatus.TIME_LIMIT: (AlgorithmicExecutionStatus.INFEASIBLE, AbstentionReason.SOLVER_TIMEOUT),
            OptimizationStatus.INFEASIBLE: (AlgorithmicExecutionStatus.INFEASIBLE, AbstentionReason.CONFLICTING_CONSTRAINTS),
            OptimizationStatus.UNBOUNDED: (AlgorithmicExecutionStatus.INFEASIBLE, AbstentionReason.HARD_CONSTRAINT_VIOLATION),
            OptimizationStatus.UNKNOWN: (AlgorithmicExecutionStatus.INFEASIBLE, AbstentionReason.CONFLICTING_CONSTRAINTS),
        }
        status_mapped, abstention_reason = status_map.get(
            solver_result.status,
            (AlgorithmicExecutionStatus.INFEASIBLE, AbstentionReason.CONFLICTING_CONSTRAINTS),
        )

        # 7. Confidence Tier based on data provenance
        if solver_result.is_optimal:
            if provenance in (None, ScenarioProvenanceKind.HISTORICAL_OBSERVATION, ScenarioProvenanceKind.STATISTICAL_FORECAST):
                conf_tier = ConfidenceTier.HIGH_CONFIDENCE
            elif provenance == ScenarioProvenanceKind.EXPERT_ASSUMPTION:
                conf_tier = ConfidenceTier.MODERATE_CONFIDENCE
            elif provenance == ScenarioProvenanceKind.SYNTHETIC_SCENARIO:
                conf_tier = ConfidenceTier.WEAKLY_SUPPORTED
            else:
                conf_tier = ConfidenceTier.MODERATE_CONFIDENCE
        else:
            conf_tier = ConfidenceTier.WEAKLY_SUPPORTED

        explanation = DecisionExplanation(
            summary_narrative=(
                f"Optimized portfolio recommendation for {len(target_assets)} assets using Rockafellar-Uryasev Mean-CVaR. "
                f"Expected annual return: {solver_result.expected_return:.2%}, CVaR_95: {solver_result.cvar_alpha:.2%}, "
                f"Volatility: {portfolio_volatility:.2%}."
            ),
            feature_attributions={
                "expected_return": solver_result.expected_return,
                "cvar_95": solver_result.cvar_alpha,
                "volatility": portfolio_volatility,
            },
            active_constraints=list(solver_result.active_constraints),
        )

        return AlgorithmDecisionEnvelope[PortfolioInvestmentBridge](
            decision_id=decision_id,
            execution_id=execution_id,
            tenant_org_id=caller.tenant_org_id,
            engine_id="glitters_portfolio_intelligence",
            engine_version="0.8.0",
            status=status_mapped,
            confidence_tier=conf_tier,
            uncertainty=UncertaintyBounds(
                point_estimate=solver_result.expected_return,
                lower_bound=solver_result.expected_return - solver_result.cvar_alpha,
                upper_bound=solver_result.expected_return + solver_result.cvar_alpha,
                confidence_level=0.95,
                interval_type="ROCKAFELLAR_CVAR_95",
                effective_sample_size=eff_sample_size,
                epistemic_status="CONVERGED_OPTIMAL" if solver_result.is_optimal else "FAILED_OR_INFEASIBLE",
            ),
            explanation=explanation,
            payload=bridge_dto,
            requires_human_approval=requires_committee,
            audit_event=audit_event,
            result_hash_sha256=result_hash,
            execution_duration_ms=solver_result.solve_time_seconds * 1000.0,
            contract_version=ContractVersion.V1_1,
            abstention_reason=abstention_reason,
            human_approval_requirement=approval_req,
        )
