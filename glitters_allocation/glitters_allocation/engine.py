"""
Main Orchestration Engine and Independent Post-Solve Invariant Verifier for GLITTERS.

Enforces all 10 Critical Invariants:
1. A property unit cannot be allocated to incompatible applicants simultaneously.
2. Organizations cannot access another organization's private allocation data.
3. Hard eligibility conditions cannot be overridden by optimization scores.
4. Financial limits cannot be bypassed by preference scoring.
5. Every allocation must be reproducible from the same input snapshot and policy version.
6. Equal-scoring candidates must have deterministic tie-breaking.
7. Every allocation must provide an explanation.
8. Failed optimization must never produce partial unauthorized allocations.
9. Repeated execution must not duplicate allocation results (Idempotency).
10. Every result must be traceable to its input data and algorithm version.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time

from .constraints import (
    FeasibilityGraph,
    build_feasibility_graph,
    evaluate_pair_hard_constraints,
    haversine_distance_km,
    validate_quotas_consistency,
    verify_tenant_isolation,
)
from .contracts import (
    AllocationBatchInput,
    AllocationBatchResult,
    AllocationDecision,
    AllocationPolicy,
    AllocationStatus,
    ExistingAssignment,
    FairnessMetrics,
    InventorySnapshot,
    SolverRegime,
)
from .exceptions import (
    InvariantCheckFailureError,
    StaleSnapshotError,
)
from .explainability import (
    build_decisions_and_explanations,
    compute_fairness_and_stability_metrics,
)
from .scoring import ScoreMatrixBundle, build_score_matrix
from .solvers import (
    RawSolverOutput,
    select_optimal_solver_regime,
    solve_cpsat_exact,
    solve_hungarian_assignment,
    solve_lagrangian_decomposition,
    solve_milp_highs,
    solve_min_cost_max_flow,
)

ALGORITHM_VERSION: str = "GLITTERS-ALLOC-ENGINE-v1.0.0"


def verify_post_solve_invariants(
    batch_input: AllocationBatchInput,
    decisions: tuple[AllocationDecision, ...],
) -> None:
    """
    Independent O(N) post-solve audit barrier (Critical Invariants 1, 2, 3, 4, 7, 8).
    If any solver bug or numerical anomaly were ever to produce a single invalid assignment,
    this barrier aborts the transaction and raises InvariantCheckFailureError before any
    output can leave the engine.
    """
    snapshot = batch_input.snapshot
    policy = batch_input.policy

    unit_map = {u.unit_id: u for u in snapshot.units}
    app_map = {a.applicant_id: a for a in snapshot.applicants}

    seen_applicants: set[str] = set()
    unit_occupancy: dict[str, int] = {}
    property_conflict_groups: dict[str, set[str]] = {}

    for dec in decisions:
        # Check explanation presence (Invariant 7)
        if dec.explanation is None or not dec.explanation.rationale_summary:
            raise InvariantCheckFailureError(
                f"Decision for applicant '{dec.applicant_id}' is missing mandatory explanation."
            )

        # Check applicant uniqueness
        if dec.applicant_id in seen_applicants:
            raise InvariantCheckFailureError(
                f"Applicant '{dec.applicant_id}' allocated to multiple units simultaneously."
            )
        seen_applicants.add(dec.applicant_id)

        if dec.applicant_id not in app_map or dec.unit_id not in unit_map:
            raise InvariantCheckFailureError(
                f"Decision references unknown applicant '{dec.applicant_id}' or unit '{dec.unit_id}'."
            )

        app = app_map[dec.applicant_id]
        unit = unit_map[dec.unit_id]

        # Check unit capacity (Invariant 1)
        unit_occupancy[unit.unit_id] = unit_occupancy.get(unit.unit_id, 0) + 1
        if unit_occupancy[unit.unit_id] > unit.capacity_slots:
            raise InvariantCheckFailureError(
                f"Unit '{unit.unit_id}' capacity ({unit.capacity_slots}) exceeded by allocations."
            )

        # Check conflict group mutual exclusion at property level (Invariant 1)
        if app.conflict_group_id:
            active_groups = property_conflict_groups.setdefault(unit.property_id, set())
            if app.conflict_group_id in active_groups:
                raise InvariantCheckFailureError(
                    f"Incompatible applicants in conflict group '{app.conflict_group_id}' "
                    f"co-allocated to property '{unit.property_id}'."
                )
            active_groups.add(app.conflict_group_id)

        # Re-verify all 10 pairwise hard constraints from scratch (Invariants 2, 3, 4)
        dist_km = haversine_distance_km(
            app.preferred_latitude,
            app.preferred_longitude,
            unit.latitude,
            unit.longitude,
        )
        ok, reasons = evaluate_pair_hard_constraints(app, unit, dist_km, policy)
        if not ok:
            raise InvariantCheckFailureError(
                f"Allocated pair ({app.applicant_id}, {unit.unit_id}) violates hard constraints: {reasons}",
                {"applicant_id": app.applicant_id, "unit_id": unit.unit_id, "reasons": reasons},
            )


def compute_deterministic_result_hash(
    status: AllocationStatus,
    algorithm_version: str,
    input_snapshot_hash: str,
    policy_version_hash: str,
    decisions: tuple[AllocationDecision, ...],
) -> str:
    """Computes a cryptographic SHA-256 commitment over the complete allocation output."""
    payload = {
        "status": status.value,
        "algorithm_version": algorithm_version,
        "input_snapshot_hash": input_snapshot_hash,
        "policy_version_hash": policy_version_hash,
        "decisions": [
            {
                "applicant_id": d.applicant_id,
                "unit_id": d.unit_id,
                "property_id": d.property_id,
                "organization_id": d.organization_id,
                "composite_score": d.composite_score,
                "tie_break_bonus": d.tie_break_bonus,
            }
            for d in decisions
        ],
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class GlittersAllocationEngine:
    """
    Production-grade, thread-safe, deterministic property allocation and optimization engine.

    Features:
    - Strict Snapshot & Policy Version Pinning (prevents stale snapshot or mid-flight policy drift).
    - Thread-Safe Idempotency Registry keyed by `(request_id, input_snapshot_hash, policy_version_hash)`
      guaranteeing Invariant 9 (repeated or concurrent identical requests never duplicate allocations).
    - Incremental Warm-Start Reallocation after unit cancellations or dynamic inventory updates.
    - Full Multi-Organization Partitioned Execution when running across independent organizations.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._idempotency_cache: dict[tuple[str, str, str], AllocationBatchResult] = {}

    def allocate(self, batch_input: AllocationBatchInput) -> AllocationBatchResult:
        """
        Executes a complete, atomic, deterministic allocation run for `batch_input`.
        """
        t0 = time.perf_counter()

        snapshot = batch_input.snapshot
        policy = batch_input.policy

        # 1. Verify Snapshot Sequence & Policy Version Pinning (Adversarial Stale Snapshot / Policy Drift Check)
        if batch_input.expected_snapshot_sequence != snapshot.snapshot_sequence:
            raise StaleSnapshotError(
                f"Expected snapshot_sequence={batch_input.expected_snapshot_sequence}, "
                f"but received snapshot with sequence={snapshot.snapshot_sequence}.",
                {
                    "expected": batch_input.expected_snapshot_sequence,
                    "actual": snapshot.snapshot_sequence,
                },
            )

        if batch_input.expected_policy_version != policy.policy_version:
            raise StaleSnapshotError(
                f"Policy version changed from expected '{batch_input.expected_policy_version}' "
                f"to '{policy.policy_version}' prior to optimization.",
                {
                    "expected": batch_input.expected_policy_version,
                    "actual": policy.policy_version,
                },
            )

        # 2. Verify Multi-Tenant Organization Isolation (Invariant 2)
        verify_tenant_isolation(
            applicants=snapshot.applicants,
            units=snapshot.units,
            quotas=snapshot.quotas,
            existing_assignments=snapshot.existing_assignments,
            policy=policy,
        )

        # 3. Compute Canonical Input & Policy Hashes (Invariant 5 & Invariant 10)
        input_hash = snapshot.canonical_hash()
        policy_hash = policy.canonical_hash()
        cache_key = (batch_input.request_id, input_hash, policy_hash)

        with self._lock:
            if cache_key in self._idempotency_cache:
                return self._idempotency_cache[cache_key]

        # 4. Validate Global Quota Consistency
        quota_violations = validate_quotas_consistency(
            quotas=snapshot.quotas,
            units=snapshot.units,
            applicants=snapshot.applicants,
        )
        if quota_violations:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            empty_fairness = FairnessMetrics(
                total_applicants=len(snapshot.applicants),
                total_allocated=0,
                overall_allocation_rate_bps=0,
                jain_fairness_index_bps=10_000,
                gini_coefficient_bps=0,
                min_normalized_utility_bps=0,
                demographic_allocation_rates_bps={},
                max_demographic_disparity_bps=0,
                blocking_pairs_count=0,
                reallocation_churn_count=0,
            )
            res_hash = compute_deterministic_result_hash(
                AllocationStatus.INFEASIBLE,
                ALGORITHM_VERSION,
                input_hash,
                policy_hash,
                (),
            )
            infeasible_result = AllocationBatchResult(
                request_id=batch_input.request_id,
                status=AllocationStatus.INFEASIBLE,
                algorithm_version=ALGORITHM_VERSION,
                solver_regime_used=policy.solver_regime,
                is_proven_optimal=True,
                optimality_gap_bps=0,
                input_snapshot_hash=input_hash,
                policy_version_hash=policy_hash,
                deterministic_result_hash=res_hash,
                execution_time_ms=elapsed_ms,
                decisions=(),
                unallocated_explanations=(),
                global_infeasibility_diagnostics=tuple(quota_violations),
                fairness_metrics=empty_fairness,
            )
            with self._lock:
                self._idempotency_cache[cache_key] = infeasible_result
            return infeasible_result

        # 5. Build Canonical Admissibility Graph & Deterministic Score Matrix
        graph: FeasibilityGraph = build_feasibility_graph(
            applicants=snapshot.applicants,
            units=snapshot.units,
            policy=policy,
        )
        scores: ScoreMatrixBundle = build_score_matrix(
            graph=graph,
            policy=policy,
            existing_assignments=snapshot.existing_assignments,
        )

        # 6. Select and Execute Mathematical Optimization Regime
        regime = select_optimal_solver_regime(
            graph=graph,
            policy=policy,
            quotas=snapshot.quotas,
            existing_assignments=snapshot.existing_assignments,
        )

        if regime == SolverRegime.HUNGARIAN_ASSIGNMENT:
            raw_out: RawSolverOutput = solve_hungarian_assignment(graph, scores)
        elif regime == SolverRegime.MIN_COST_MAX_FLOW:
            raw_out = solve_min_cost_max_flow(graph, scores)
        elif regime == SolverRegime.MILP_HIGHS:
            raw_out = solve_milp_highs(graph, scores, policy, snapshot.quotas)
        elif regime == SolverRegime.CPSAT_EXACT:
            raw_out = solve_cpsat_exact(
                graph,
                scores,
                policy,
                snapshot.quotas,
                snapshot.existing_assignments,
                lexicographic=False,
            )
        elif regime == SolverRegime.LEXICOGRAPHIC_CPSAT:
            raw_out = solve_cpsat_exact(
                graph,
                scores,
                policy,
                snapshot.quotas,
                snapshot.existing_assignments,
                lexicographic=True,
            )
        elif regime == SolverRegime.LAGRANGIAN_DECOMPOSITION:
            raw_out = solve_lagrangian_decomposition(
                graph,
                scores,
                policy,
                snapshot.quotas,
                snapshot.existing_assignments,
            )
        else:
            raw_out = solve_min_cost_max_flow(graph, scores)

        # 7. If Solver Reports Global Infeasibility, Guarantee Zero Partial Unauthorized Allocations (Invariant 8)
        if raw_out.status == AllocationStatus.INFEASIBLE:
            _, unalloc_explanations = build_decisions_and_explanations(
                request_id=batch_input.request_id,
                graph=graph,
                scores=scores,
                selected_edges=(),
                existing_assignments=snapshot.existing_assignments,
            )
            fairness = compute_fairness_and_stability_metrics(
                graph=graph,
                scores=scores,
                selected_edges=(),
                existing_assignments=snapshot.existing_assignments,
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            res_hash = compute_deterministic_result_hash(
                AllocationStatus.INFEASIBLE,
                ALGORITHM_VERSION,
                input_hash,
                policy_hash,
                (),
            )
            infeasible_result = AllocationBatchResult(
                request_id=batch_input.request_id,
                status=AllocationStatus.INFEASIBLE,
                algorithm_version=ALGORITHM_VERSION,
                solver_regime_used=raw_out.regime_used,
                is_proven_optimal=raw_out.is_proven_optimal,
                optimality_gap_bps=raw_out.optimality_gap_bps,
                input_snapshot_hash=input_hash,
                policy_version_hash=policy_hash,
                deterministic_result_hash=res_hash,
                execution_time_ms=elapsed_ms,
                decisions=(),
                unallocated_explanations=unalloc_explanations,
                global_infeasibility_diagnostics=raw_out.infeasibility_diagnostics,
                fairness_metrics=fairness,
            )
            with self._lock:
                self._idempotency_cache[cache_key] = infeasible_result
            return infeasible_result

        # 8. Build Decisions, Explanations, and Fairness Metrics
        decisions, unalloc_explanations = build_decisions_and_explanations(
            request_id=batch_input.request_id,
            graph=graph,
            scores=scores,
            selected_edges=raw_out.selected_edges,
            existing_assignments=snapshot.existing_assignments,
        )

        # 9. Post-Solve Independent Verification Barrier (Invariants 1, 2, 3, 4, 7, 8)
        verify_post_solve_invariants(batch_input, decisions)

        fairness = compute_fairness_and_stability_metrics(
            graph=graph,
            scores=scores,
            selected_edges=raw_out.selected_edges,
            existing_assignments=snapshot.existing_assignments,
        )

        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        res_hash = compute_deterministic_result_hash(
            raw_out.status,
            ALGORITHM_VERSION,
            input_hash,
            policy_hash,
            decisions,
        )

        result = AllocationBatchResult(
            request_id=batch_input.request_id,
            status=raw_out.status,
            algorithm_version=ALGORITHM_VERSION,
            solver_regime_used=raw_out.regime_used,
            is_proven_optimal=raw_out.is_proven_optimal,
            optimality_gap_bps=raw_out.optimality_gap_bps,
            input_snapshot_hash=input_hash,
            policy_version_hash=policy_hash,
            deterministic_result_hash=res_hash,
            execution_time_ms=elapsed_ms,
            decisions=decisions,
            unallocated_explanations=unalloc_explanations,
            global_infeasibility_diagnostics=(),
            fairness_metrics=fairness,
        )

        with self._lock:
            self._idempotency_cache[cache_key] = result
        return result

    def reallocate_incremental(
        self,
        request_id: str,
        prior_snapshot: InventorySnapshot,
        prior_result: AllocationBatchResult,
        withdrawn_unit_ids: frozenset[str] = frozenset(),
        withdrawn_applicant_ids: frozenset[str] = frozenset(),
        new_units: tuple = (),
        new_applicants: tuple = (),
        policy: AllocationPolicy | None = None,
        lock_unaffected_assignments: bool = False,
    ) -> AllocationBatchResult:
        """
        Incremental Reallocation Algorithm after dynamic cancellations or inventory updates.
        Constructs a successor snapshot preserving active assignments from `prior_result`
        (excluding withdrawn units/applicants) and resolves the minimum-perturbation
        warm-start allocation problem.
        """
        if policy is None:
            raise ValueError("Policy must be provided for incremental reallocation.")

        remaining_units = tuple(
            u for u in prior_snapshot.units if u.unit_id not in withdrawn_unit_ids
        ) + tuple(new_units)

        remaining_apps = tuple(
            a for a in prior_snapshot.applicants if a.applicant_id not in withdrawn_applicant_ids
        ) + tuple(new_applicants)

        surviving_assignments = tuple(
            ExistingAssignment(
                applicant_id=d.applicant_id,
                unit_id=d.unit_id,
                organization_id=d.organization_id,
                locked=lock_unaffected_assignments,
            )
            for d in prior_result.decisions
            if d.unit_id not in withdrawn_unit_ids and d.applicant_id not in withdrawn_applicant_ids
        )

        new_seq = prior_snapshot.snapshot_sequence + 1
        next_snapshot = InventorySnapshot(
            snapshot_id=f"{prior_snapshot.snapshot_id}-INC-{new_seq}",
            snapshot_sequence=new_seq,
            timestamp_iso=prior_snapshot.timestamp_iso,
            units=remaining_units,
            applicants=remaining_apps,
            quotas=prior_snapshot.quotas,
            existing_assignments=surviving_assignments,
        )

        batch_input = AllocationBatchInput(
            request_id=request_id,
            expected_snapshot_sequence=new_seq,
            expected_policy_version=policy.policy_version,
            snapshot=next_snapshot,
            policy=policy,
        )
        return self.allocate(batch_input)
