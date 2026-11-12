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
from ..scenarios.provenance import EvidenceArtifactRegistry, ScenarioDefinition, default_evidence_registry
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
        evidence_registry: Optional[EvidenceArtifactRegistry] = None,
        scenarios: Optional[Sequence[ScenarioDefinition]] = None,
        dataset_identifier: Optional[str] = None,
        dataset_sha256: Optional[str] = None,
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

        # 3. Compute empirical scenario dispersion and mark formal sampling confidence intervals unavailable
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

            # Designed stress scenarios represent deterministic test cases, not i.i.d. random population samples.
            # Formal statistical confidence intervals are marked unavailable to preserve scientific integrity.
            interval_type = "UNAVAILABLE_DESIGNED_STRESS_SCENARIOS"
            epistemic_status = "DESCRIPTIVE_DISPERSION_ONLY_NO_SAMPLING_CI"
            unc_lower = round(solver_result.expected_return, 6)
            unc_upper = round(solver_result.expected_return, 6)
            conf_level = 0.0
        else:
            # Explicit unsupported/missing state: do not use zero volatility to represent missing data
            portfolio_volatility = -1.0  # Sentinel indicating missing volatility (not zero risk)
            eff_sample_size = 0.0
            unc_lower = round(solver_result.expected_return, 6)
            unc_upper = round(solver_result.expected_return, 6)
            conf_level = 0.0
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

        # Deterministic cryptographic canonical provenance hash
        reg = evidence_registry if evidence_registry is not None else default_evidence_registry
        prov_canonical = {
            "engine": "glitters_portfolio_intelligence",
            "engine_version": "0.8.0",
            "policy_id": policy_id,
            "portfolio_id": portfolio.portfolio_id,
            "tenant_org_id": str(portfolio.tenant_org_id),
            "solver_backend": solver_result.solver_type.value,
            "solver_version": solver_result.solver_version,
            "objective_value": round(float(solver_result.objective_value), 6),
            "assets": [
                {
                    "asset_id": a.asset_id,
                    "val_cents": a.current_valuation_cents,
                    "acq_cents": a.acquisition_valuation_cents,
                    "gpr_cents": a.monthly_gross_potential_rent_cents,
                    "opex_cents": a.monthly_operating_expense_cents,
                    "capex_cents": a.planned_capex_cents,
                    "maintenance_cents": a.annual_routine_maintenance_cents,
                    "debt_cents": a.annual_debt_service_cents,
                    "vacancy": round(float(a.annual_baseline_vacancy_rate), 6),
                    "credit_loss": round(float(a.annual_baseline_credit_loss_rate), 6),
                }
                for a in sorted(target_assets, key=lambda x: x.asset_id)
            ],
            "scenarios": [
                {
                    "scenario_id": s.scenario_id,
                    "probability": round(float(s.probability), 6),
                    "provenance": s.provenance.value,
                    "dataset_identifier": s.dataset_identifier or "",
                    "dataset_sha256": s.dataset_sha256 or "",
                    "model_version": s.model_version,
                    "transformation": s.transformation_methodology,
                    "assumptions": {k: round(float(v), 6) for k, v in sorted(s.assumptions.items())},
                }
                for s in sorted(scenarios, key=lambda x: x.scenario_id)
            ] if scenarios else [],
        }
        prov_json = json.dumps(prov_canonical, sort_keys=True, separators=(",", ":"))
        prov_hash = hashlib.sha256(prov_json.encode("utf-8")).hexdigest()

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

        # 8. Rigorous Confidence Tier based on independently authenticated evidence
        # Missing provenance must never default to historical observation.
        # Missing evidence must never default to verified.
        # A caller-supplied Boolean alone CANNOT authenticate evidence.
        if scenarios is not None and len(scenarios) > 0:
            all_historical_authenticated = all(
                s.is_evidence_authenticated(reg)
                for s in scenarios
                if s.provenance == ScenarioProvenanceKind.HISTORICAL_OBSERVATION
            )
            has_historical = any(s.provenance == ScenarioProvenanceKind.HISTORICAL_OBSERVATION for s in scenarios)
            has_synthetic = any(s.provenance == ScenarioProvenanceKind.SYNTHETIC_SCENARIO for s in scenarios)
            has_unverified = any(
                s.provenance == ScenarioProvenanceKind.HISTORICAL_OBSERVATION and not s.is_evidence_authenticated(reg)
                for s in scenarios
            )

            if has_historical and all_historical_authenticated and not has_synthetic and not has_unverified:
                effective_prov = ScenarioProvenanceKind.HISTORICAL_OBSERVATION
                is_authenticated = True
            elif has_synthetic:
                effective_prov = ScenarioProvenanceKind.SYNTHETIC_SCENARIO
                is_authenticated = False
            else:
                effective_prov = ScenarioProvenanceKind.EXPERT_ASSUMPTION
                is_authenticated = False
        else:
            # Omitted provenance defaults to EXPERT_ASSUMPTION, never historical!
            effective_prov = provenance if provenance is not None else ScenarioProvenanceKind.EXPERT_ASSUMPTION
            if effective_prov == ScenarioProvenanceKind.HISTORICAL_OBSERVATION:
                is_authenticated = reg.is_artifact_authenticated(dataset_identifier, dataset_sha256)
            else:
                is_authenticated = False

        if solver_result.is_optimal:
            if effective_prov == ScenarioProvenanceKind.HISTORICAL_OBSERVATION and is_authenticated:
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
                point_estimate=round(solver_result.expected_return, 6),
                lower_bound=unc_lower,
                upper_bound=unc_upper,
                confidence_level=conf_level,
                interval_type=interval_type,
                effective_sample_size=eff_sample_size,
                epistemic_status=epistemic_status,
            ),
            explanation=explanation,
            payload=bridge_dto,
            requires_human_approval=requires_committee,
            audit_event=audit_event,
            result_hash_sha256=result_hash,
            execution_duration_ms=round(solver_result.solve_time_seconds * 1000.0, 2),
            contract_version=ContractVersion.V1_1,
            abstention_reason=abstention_reason,
            human_approval_requirement=approval_req,
        )
