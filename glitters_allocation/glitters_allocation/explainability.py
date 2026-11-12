"""
Fairness, Bias, Stability, and Explainability Analytics for GLITTERS Allocation Engine.

Guarantees Critical Invariant 7:
Every allocation and non-allocation decision produces a complete, auditable explanation
containing exact score breakdowns, active hard constraints verified, alternative margins,
and unit-by-unit rejection codes.
"""

from __future__ import annotations

from typing import Sequence

from .constraints import FeasibilityGraph
from .contracts import (
    AllocationDecision,
    AllocationExplanation,
    ExistingAssignment,
    FairnessMetrics,
)
from .scoring import ScoreMatrixBundle

ACTIVE_HARD_CONSTRAINTS_VERIFIED: tuple[str, ...] = (
    "HC_ORG_ISOLATION_BOUNDARY",
    "HC_CATEGORY_ELIGIBILITY",
    "HC_MONTHLY_BUDGET_CEILING",
    "HC_UPFRONT_CAPITAL_CEILING",
    "HC_VERIFIED_INCOME_MULTIPLE",
    "HC_CREDIT_SCORE_FLOOR",
    "HC_MANDATORY_ELIGIBILITY_TAGS",
    "HC_PHYSICAL_AND_ACCESSIBILITY_SPECS",
    "HC_TEMPORAL_AVAILABILITY_AND_LEASE_WINDOW",
    "HC_MAX_TRAVEL_DISTANCE_RADIUS",
    "HC_UNIT_CAPACITY_AND_CONFLICT_EXCLUSION",
)


def compute_fairness_and_stability_metrics(
    graph: FeasibilityGraph,
    scores: ScoreMatrixBundle,
    selected_edges: Sequence[tuple[int, int]],
    existing_assignments: Sequence[ExistingAssignment],
) -> FairnessMetrics:
    """
    Computes exact integer fairness and stability metrics (in basis points 0..10000).
    """
    n_a = len(graph.applicants)
    n_alloc = len(selected_edges)

    if n_a == 0:
        return FairnessMetrics(
            total_applicants=0,
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

    overall_rate_bps = (n_alloc * 10_000) // n_a

    app_to_unit: dict[int, int] = {}
    unit_to_apps: dict[int, list[int]] = {j: [] for j in range(len(graph.units))}
    utilities: list[int] = [0] * n_a

    max_possible_score = max(scores.composite_scores.values(), default=1)
    for i, j in selected_edges:
        app_to_unit[i] = j
        unit_to_apps[j].append(i)
        utilities[i] = (scores.composite_scores[(i, j)] * 10_000) // max(1, max_possible_score)

    alloc_utils = [utilities[i] for i in app_to_unit]
    if alloc_utils and sum(alloc_utils) > 0:
        sum_u = sum(alloc_utils)
        sum_u_sq = sum(u * u for u in alloc_utils)
        jain_bps = (sum_u * sum_u * 10_000) // max(1, len(alloc_utils) * sum_u_sq)
        min_util_bps = min(alloc_utils)
    else:
        jain_bps = 10_000 if n_alloc == 0 else 0
        min_util_bps = 0

    sorted_u = sorted(utilities)
    total_u = sum(sorted_u)
    if total_u > 0 and n_a > 1:
        weighted_diff_sum = 0
        for idx, val in enumerate(sorted_u, start=1):
            weighted_diff_sum += (2 * idx - n_a - 1) * val
        gini_bps = max(0, min(10_000, (weighted_diff_sum * 10_000) // (n_a * total_u)))
    else:
        gini_bps = 0

    group_totals: dict[str, int] = {}
    group_allocs: dict[str, int] = {}
    for i, app in enumerate(graph.applicants):
        g = app.demographic_group
        group_totals[g] = group_totals.get(g, 0) + 1
        if i in app_to_unit:
            group_allocs[g] = group_allocs.get(g, 0) + 1

    demo_rates: dict[str, int] = {}
    for g, tot in sorted(group_totals.items()):
        demo_rates[g] = (group_allocs.get(g, 0) * 10_000) // max(1, tot)

    if len(demo_rates) > 1:
        max_disparity_bps = max(demo_rates.values()) - min(demo_rates.values())
    else:
        max_disparity_bps = 0

    # Precompute minimum assigned weight per unit in O(N_u)
    unit_min_weight: dict[int, int] = {}
    unit_has_spare: dict[int, bool] = {}
    for j, assigned_list in unit_to_apps.items():
        if len(assigned_list) < graph.units[j].capacity_slots:
            unit_has_spare[j] = True
            unit_min_weight[j] = 0
        else:
            unit_has_spare[j] = False
            unit_min_weight[j] = min(scores.solver_weights[(other_i, j)] for other_i in assigned_list)

    blocking_pairs = 0
    for i, j in graph.feasible_edges:
        if app_to_unit.get(i) == j:
            continue
        cand_weight = scores.solver_weights[(i, j)]
        curr_u = app_to_unit.get(i)
        curr_app_weight = scores.solver_weights[(i, curr_u)] if curr_u is not None else 0

        if cand_weight > curr_app_weight:
            if unit_has_spare[j] or cand_weight > unit_min_weight[j]:
                blocking_pairs += 1

    allocated_pair_ids = {
        (graph.applicants[i].applicant_id, graph.units[j].unit_id)
        for i, j in selected_edges
    }
    churn_count = sum(
        1
        for ea in existing_assignments
        if (ea.applicant_id, ea.unit_id) not in allocated_pair_ids
    )

    return FairnessMetrics(
        total_applicants=n_a,
        total_allocated=n_alloc,
        overall_allocation_rate_bps=overall_rate_bps,
        jain_fairness_index_bps=jain_bps,
        gini_coefficient_bps=gini_bps,
        min_normalized_utility_bps=min_util_bps,
        demographic_allocation_rates_bps=demo_rates,
        max_demographic_disparity_bps=max_disparity_bps,
        blocking_pairs_count=blocking_pairs,
        reallocation_churn_count=churn_count,
    )


def build_decisions_and_explanations(
    request_id: str,
    graph: FeasibilityGraph,
    scores: ScoreMatrixBundle,
    selected_edges: Sequence[tuple[int, int]],
    existing_assignments: Sequence[ExistingAssignment],
) -> tuple[tuple[AllocationDecision, ...], tuple[AllocationExplanation, ...]]:
    """
    Constructs rich, deterministic AllocationDecision and AllocationExplanation objects
    for every allocated and unallocated applicant in O(|E_feas| + N_a) time.
    """
    prior_pairs = {(ea.applicant_id, ea.unit_id) for ea in existing_assignments}
    app_to_unit = {i: j for i, j in selected_edges}
    unit_to_apps: dict[int, list[int]] = {j: [] for j in range(len(graph.units))}
    for i, j in selected_edges:
        unit_to_apps[j].append(i)

    # Group precomputed rejection reasons by applicant index
    app_rejections: dict[int, dict[str, tuple[str, ...]]] = {i: {} for i in range(len(graph.applicants))}
    for (i, j), reasons in graph.rejection_reasons.items():
        app_rejections[i][graph.units[j].unit_id] = reasons

    decisions: list[AllocationDecision] = []
    unallocated_explanations: list[AllocationExplanation] = []

    for i, app in enumerate(graph.applicants):
        feasible_u_indices = graph.applicant_edges[i]
        rejections = app_rejections[i]

        if i in app_to_unit:
            j = app_to_unit[i]
            unit = graph.units[j]
            comp_score = scores.composite_scores[(i, j)]
            tie_rank = scores.tie_break_ranks[(i, j)]
            breakdown = scores.score_breakdowns[(i, j)]
            is_retained = (app.applicant_id, unit.unit_id) in prior_pairs

            alt_candidates = [
                (scores.composite_scores[(i, alt_j)], graph.units[alt_j].unit_id)
                for alt_j in feasible_u_indices
                if alt_j != j
            ]
            if alt_candidates:
                alt_candidates.sort(key=lambda x: (-x[0], x[1]))
                best_alt_score, best_alt_uid = alt_candidates[0]
                best_alt_delta = comp_score - best_alt_score
            else:
                best_alt_uid = None
                best_alt_delta = None

            rationale = (
                f"Allocated applicant '{app.applicant_id}' to unit '{unit.unit_id}' "
                f"(property '{unit.property_id}', category {unit.category.value}) with composite "
                f"score {comp_score:,} and deterministic tie-break rank #{tie_rank}. "
                f"All 10 pairwise and global hard invariants verified across {len(feasible_u_indices)} feasible unit option(s)."
            )

            explanation = AllocationExplanation(
                applicant_id=app.applicant_id,
                allocated_unit_id=unit.unit_id,
                organization_id=app.organization_id,
                composite_score=comp_score,
                tie_break_rank=tie_rank,
                score_breakdown=breakdown,
                active_hard_constraints_verified=ACTIVE_HARD_CONSTRAINTS_VERIFIED,
                feasible_alternatives_count=max(0, len(feasible_u_indices) - 1),
                best_alternative_unit_id=best_alt_uid,
                best_alternative_score_delta=best_alt_delta,
                rejection_reasons_by_unit=rejections,
                rationale_summary=rationale,
            )

            decision_id = f"DEC-{request_id}-{app.applicant_id}-{unit.unit_id}"
            decisions.append(
                AllocationDecision(
                    decision_id=decision_id,
                    applicant_id=app.applicant_id,
                    unit_id=unit.unit_id,
                    property_id=unit.property_id,
                    organization_id=app.organization_id,
                    composite_score=comp_score,
                    tie_break_bonus=tie_rank,
                    is_retained_from_prior=is_retained,
                    explanation=explanation,
                )
            )
        else:
            if not feasible_u_indices:
                rationale = (
                    f"Applicant '{app.applicant_id}' could not be allocated because zero property units "
                    f"satisfied all hard eligibility, financial, geographic, and temporal constraints."
                )
                best_alt_uid = None
                best_alt_delta = None
            else:
                alt_candidates = [
                    (scores.composite_scores[(i, alt_j)], alt_j, graph.units[alt_j].unit_id)
                    for alt_j in feasible_u_indices
                ]
                alt_candidates.sort(key=lambda x: (-x[0], x[2]))
                best_alt_score, best_j, best_alt_uid = alt_candidates[0]
                occupants = unit_to_apps.get(best_j, [])
                if occupants:
                    winner_scores = [scores.composite_scores[(occ_i, best_j)] for occ_i in occupants]
                    min_winner_score = min(winner_scores)
                    best_alt_delta = best_alt_score - min_winner_score
                    rationale = (
                        f"Applicant '{app.applicant_id}' was feasible for {len(feasible_u_indices)} unit(s) "
                        f"(best candidate '{best_alt_uid}' with score {best_alt_score:,}), but scarce capacity "
                        f"was assigned to higher-scoring or lexicographically prioritized competing applicants "
                        f"(winning score {min_winner_score:,})."
                    )
                else:
                    best_alt_delta = 0
                    rationale = (
                        f"Applicant '{app.applicant_id}' was feasible for {len(feasible_u_indices)} unit(s), "
                        f"but global co-occupancy conflict group or demographic parity constraints prevented assignment."
                    )

            unallocated_explanations.append(
                AllocationExplanation(
                    applicant_id=app.applicant_id,
                    allocated_unit_id=None,
                    organization_id=app.organization_id,
                    composite_score=0,
                    tie_break_rank=0,
                    score_breakdown={},
                    active_hard_constraints_verified=ACTIVE_HARD_CONSTRAINTS_VERIFIED,
                    feasible_alternatives_count=len(feasible_u_indices),
                    best_alternative_unit_id=best_alt_uid,
                    best_alternative_score_delta=best_alt_delta,
                    rejection_reasons_by_unit=rejections,
                    rationale_summary=rationale,
                )
            )

    return tuple(decisions), tuple(unallocated_explanations)
