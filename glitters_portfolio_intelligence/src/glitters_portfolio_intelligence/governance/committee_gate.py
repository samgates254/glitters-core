from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
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
from ..scenarios.validation import validate_scenario_distribution


class InvestmentDecisionGovernanceGate:
    """
    Enforces strict GLITTERS governance:
    Engine 08 is strictly advisory and CANNOT execute transactions or post financial entries.
    All binding capital deployment or rebalancing requires Investment Committee sign-off.
    Enforces tenant organization isolation, input provenance verification,
    distinct solver status mapping, and rigorous statistical uncertainty characterization.
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
        scenario_evidence_verified: Optional[bool] = None,
        policy_id: str = "POL-PORTFOLIO-ALLOCATION-2026",
        threshold_for_committee_cents: int = 10_000_000_00,  # KES 10M
    ) -> AlgorithmDecisionEnvelope[PortfolioInvestmentBridge]:
        """
        Packages solver results into an auditable AlgorithmDecisionEnvelope[PortfolioInvestmentBridge].
        Strictly enforces tenant isolation between caller and portfolio.
        Calculates empirical scenario volatility, sample standard error, and true portfolio holding delta.
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

        # 2. Map holdings strictly from provided assets or portfolio assets with length verification
        target_assets = assets if assets is not None else portfolio.assets
        if len(solver_result.optimal_weights) != len(target_assets):
            raise ValueError(
                f"Length mismatch: solver optimal_weights length ({len(solver_result.optimal_weights)}) "
                f"does not match target assets length ({len(target_assets)})"
            )

        holdings: list[tuple[str, float]] = []
        for i, a in enumerate(target_assets):
            w = float(solver_result.optimal_weights[i])
            holdings.append((a.asset_id, round(w, 4)))

        # 3. Compute empirical scenario volatility, sample standard error, and effective sample size
        has_scenario_inputs = (
            scenario_returns is not None
            and scenario_probabilities is not None
            and len(solver_result.optimal_weights) == scenario_returns.shape[0]
            and len(scenario_probabilities) > 0
        )

        if has_scenario_inputs:
            # Validate probabilities strictly
            _, valid_probs = validate_scenario_distribution(scenario_returns, scenario_probabilities)
            port_scen_returns = solver_result.optimal_weights @ scenario_returns
            mu = float(np.sum(valid_probs * port_scen_returns))
            var = float(np.sum(valid_probs * ((port_scen_returns - mu) ** 2)))
            portfolio_volatility = float(np.sqrt(max(0.0, var)))
            eff_sample_size = float(1.0 / np.sum(valid_probs ** 2))
            # Standard error of the expected return point estimate
            std_err = portfolio_volatility / math.sqrt(max(1.0, eff_sample_size))
            unc_lower = solver_result.expected_return - 1.96 * std_err
            unc_upper = solver_result.expected_return + 1.96 * std_err
            interval_type = "STANDARD_ERROR_NORMAL_95"
            epistemic_status = "ESTIMATED_SAMPLE_CONFIDENCE_INTERVAL" if solver_result.is_optimal else "FAILED_OR_INFEASIBLE"
        else:
            # Explicit unsupported/missing state: NEVER use CVaR as fallback volatility!
            portfolio_volatility = 0.0
            eff_sample_size = 0.0
            unc_lower = float("nan")
            unc_upper = float("nan")
            interval_type = "UNAVAILABLE_MISSING_SCENARIOS"
            epistemic_status = "UNSUPPORTED_NO_SCENARIOS"

        # 4. Compare recommended holdings to actual current portfolio holdings (not equal weights)
        total_val_cents = portfolio.total_portfolio_value_cents
        current_holdings_map: dict[str, float] = {}
        if total_val_cents > 0:
            for p_asset in portfolio.assets:
                p_val = p_asset.current_valuation_cents * p_asset.initial_ownership_fraction
                current_holdings_map[p_asset.asset_id] = p_val / total_val_cents

        rebalancing_needed = any(
            abs(w_rec - current_holdings_map.get(aid, 0.0)) > 0.005
            for aid, w_rec in holdings
        )

        # 5. Strict CORE Fiduciary Gate: Any capital deployment or rebalancing requires Committee signoff
        requires_committee = (
            rebalancing_needed
            or total_val_cents >= threshold_for_committee_cents
            or not solver_result.is_optimal
            or any(w > 0.01 for _, w in holdings)
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

        # 6. Canonical SHA256 decision payload hash
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

        # 7. Map OptimizationStatus to distinct AlgorithmicExecutionStatus and AbstentionReason
        status_map = {
            OptimizationStatus.OPTIMAL: (AlgorithmicExecutionStatus.OPTIMAL, AbstentionReason.NONE),
            OptimizationStatus.FEASIBLE: (AlgorithmicExecutionStatus.FEASIBLE_APPROXIMATE, AbstentionReason.NONE),
            OptimizationStatus.TIME_LIMIT: (AlgorithmicExecutionStatus.CONFLICTING_CONSTRAINTS, AbstentionReason.SOLVER_TIMEOUT),
            OptimizationStatus.UNBOUNDED: (AlgorithmicExecutionStatus.ABSTAINED_POLICY_VIOLATION, AbstentionReason.HARD_CONSTRAINT_VIOLATION),
            OptimizationStatus.INFEASIBLE: (AlgorithmicExecutionStatus.INFEASIBLE, AbstentionReason.CONFLICTING_CONSTRAINTS),
            OptimizationStatus.UNKNOWN: (AlgorithmicExecutionStatus.ABSTAINED_INSUFFICIENT_DATA, AbstentionReason.INSUFFICIENT_DATA),
        }
        status_mapped, abstention_reason = status_map.get(
            solver_result.status,
            (AlgorithmicExecutionStatus.ABSTAINED_INSUFFICIENT_DATA, AbstentionReason.INSUFFICIENT_DATA),
        )

        # 8. Rigorous Confidence Tier based on evidenced provenance (not just solver optimality)
        effective_prov = provenance
        effective_verified = scenario_evidence_verified

        if effective_prov is None:
            # When caller does not specify provenance (legacy default), treat as historical
            effective_prov = ScenarioProvenanceKind.HISTORICAL_OBSERVATION
            effective_verified = (scenario_evidence_verified is not False)
        elif effective_verified is None:
            effective_verified = False

        if solver_result.is_optimal:
            if effective_prov == ScenarioProvenanceKind.HISTORICAL_OBSERVATION and effective_verified:
                conf_tier = ConfidenceTier.HIGH_CONFIDENCE
            elif effective_prov in (
                ScenarioProvenanceKind.HISTORICAL_OBSERVATION,
                ScenarioProvenanceKind.STATISTICAL_FORECAST,
                ScenarioProvenanceKind.EXPERT_ASSUMPTION,
            ):
                conf_tier = ConfidenceTier.MODERATE_CONFIDENCE
            elif effective_prov == ScenarioProvenanceKind.SYNTHETIC_SCENARIO:
                conf_tier = ConfidenceTier.WEAKLY_SUPPORTED
            else:
                conf_tier = ConfidenceTier.WEAKLY_SUPPORTED
        else:
            conf_tier = ConfidenceTier.WEAKLY_SUPPORTED

        explanation = DecisionExplanation(
            summary_narrative=(
                f"Optimized portfolio recommendation for {len(target_assets)} assets using Rockafellar-Uryasev Mean-CVaR. "
                f"Expected net annual return: {solver_result.expected_return:.2%}, Net CVaR_95: {solver_result.cvar_alpha:.2%}, "
                f"Empirical Volatility: {portfolio_volatility:.2%}."
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
                lower_bound=unc_lower,
                upper_bound=unc_upper,
                confidence_level=0.95,
                interval_type=interval_type,
                effective_sample_size=eff_sample_size,
                epistemic_status=epistemic_status,
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
