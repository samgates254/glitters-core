"""
Deterministic Integer Objective Scoring & Cryptographic Tie-Breaking Engine.

Guarantees Critical Invariants:
- Invariant 5: Every allocation must be reproducible from the same input snapshot and policy version.
- Invariant 6: Equal-scoring candidates must have deterministic tie-breaking.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import numpy as np

from .constraints import FeasibilityGraph
from .contracts import AllocationPolicy, ApplicantProfile, ExistingAssignment, PropertyUnit

# Scale factor for composite utility scores: all sub-scores are normalized to integer [0, 1000]
# and multiplied by policy weights (which are in [0, 100_000]).
# Thus composite_score is in [0, ~10^9].
# Tie-break rank is scaled to [1, 999], and Composite Score is shifted by * 1000 so that
# tie-breaking NEVER overrides a strictly higher composite optimization score!
TIE_BREAK_MODULO: int = 1_000


def deterministic_pair_tie_hash(
    policy_id: str,
    policy_version: str,
    applicant_id: str,
    unit_id: str,
) -> bytes:
    """
    Computes a 256-bit SHA-256 digest for (policy_id, policy_version, applicant_id, unit_id).
    Guarantees a strict total order over all (applicant, unit) pairs with zero platform dependency.
    """
    payload = f"{policy_id}:{policy_version}:{applicant_id}:{unit_id}".encode("utf-8")
    return hashlib.sha256(payload).digest()


@dataclass(frozen=True)
class ScoreMatrixBundle:
    """
    Holds exact 64-bit integer composite scores, tie-break ranks, and combined solver weights
    for all feasible edges in the FeasibilityGraph.
    """

    # Maps (a_idx, u_idx) -> composite_score (before tie-break scaling)
    composite_scores: dict[tuple[int, int], int]
    # Maps (a_idx, u_idx) -> breakdown dict of sub-components
    score_breakdowns: dict[tuple[int, int], dict[str, int]]
    # Maps (a_idx, u_idx) -> strict rank 1..E among all feasible edges (higher rank = preferred on ties)
    tie_break_ranks: dict[tuple[int, int], int]
    # Maps (a_idx, u_idx) -> final 64-bit integer weight passed to exact solvers:
    # W_{i,j} = composite_score * E_scale + tie_break_rank
    solver_weights: dict[tuple[int, int], int]
    edge_scale_multiplier: int


def compute_pair_subscores(
    applicant: ApplicantProfile,
    unit: PropertyUnit,
    distance_km: float,
    policy: AllocationPolicy,
    is_prior_assignment: bool,
) -> tuple[int, dict[str, int]]:
    """
    Computes exact integer sub-scores (each in 0..1000) and weighted composite score
    using purely integer arithmetic (except precomputed distance quantized to integer meters).
    """
    # 1. Base Coverage Reward (always 1000 for any feasible allocation)
    coverage_sub = 1_000

    # 2. Preference Match Score (0..1000)
    # Combines explicit unit preference (0..500), zone preference (0..250), and amenity overlap (0..250)
    explicit_pref = min(1_000, applicant.explicit_unit_preferences.get(unit.unit_id, 0))
    zone_pref = 1_000 if (not applicant.preferred_zones or unit.zone_code in applicant.preferred_zones) else 200
    if applicant.preferred_amenities:
        matched_amenities = len(applicant.preferred_amenities.intersection(unit.amenities))
        amenity_pref = (matched_amenities * 1_000) // len(applicant.preferred_amenities)
    else:
        amenity_pref = 1_000
    preference_sub = (explicit_pref * 50 + zone_pref * 25 + amenity_pref * 25) // 100

    # 3. Affordability Fit Score (0..1000)
    # Rewards healthy rent-to-budget margin without under-utilizing subsidized stock
    if applicant.max_monthly_budget_cents > 0:
        margin_cents = max(0, applicant.max_monthly_budget_cents - unit.total_monthly_cost_cents)
        affordability_sub = min(1_000, 500 + (margin_cents * 500) // applicant.max_monthly_budget_cents)
    else:
        affordability_sub = 1_000

    # 4. Geographic Proximity Score (0..1000)
    # Quantize distance to integer meters for platform-independent reproducibility
    dist_meters = int(round(distance_km * 1_000.0))
    max_meters = max(1, int(round(applicant.max_travel_distance_km * 1_000.0)))
    proximity_sub = max(0, min(1_000, 1_000 - (dist_meters * 1_000) // max_meters))

    # 5. Applicant Urgency & Vulnerability Priority Score (0..1000)
    # Urgency (1..10) + Vulnerability (0..10) -> max 20 -> scaled to 0..1000
    urgency_sub = min(1_000, ((applicant.urgency_level + applicant.vulnerability_priority) * 1_000) // 20)

    # 6. Owner Priority Tier Score (0..1000)
    owner_sub = min(1_000, (unit.owner_priority_tier * 1_000) // 5)

    # 7. Stability / Reallocation Churn Bonus
    retention_bonus = policy.penalty_reallocation_churn * 1_000 if is_prior_assignment else 0

    breakdown = {
        "coverage_component": coverage_sub * policy.weight_allocation_coverage,
        "preference_component": preference_sub * policy.weight_preference_match,
        "affordability_component": affordability_sub * policy.weight_affordability_fit,
        "proximity_component": proximity_sub * policy.weight_distance_proximity,
        "urgency_component": urgency_sub * policy.weight_urgency_priority,
        "owner_tier_component": owner_sub * policy.weight_owner_tier,
        "stability_retention_bonus": retention_bonus,
    }

    composite_score = sum(breakdown.values())
    return composite_score, breakdown


def build_score_matrix(
    graph: FeasibilityGraph,
    policy: AllocationPolicy,
    existing_assignments: tuple[ExistingAssignment, ...] = (),
) -> ScoreMatrixBundle:
    """
    Computes composite scores and strict cryptographic tie-break ranks for all feasible edges.
    Because `tie_break_rank` is strictly bounded in `[1, E]` and `composite_score` is multiplied
    by `E_scale = E + 2`, two candidate allocations with equal composite scores have a mathematically
    unique total weight `W_{i,j}`, while no tie-breaker can ever flip a strictly better composite score.
    """
    prior_pairs: set[tuple[str, str]] = {
        (ea.applicant_id, ea.unit_id) for ea in existing_assignments
    }

    composite_scores: dict[tuple[int, int], int] = {}
    score_breakdowns: dict[tuple[int, int], dict[str, int]] = {}
    edge_hashes: list[tuple[bytes, str, str, tuple[int, int]]] = []

    for i, j in graph.feasible_edges:
        app = graph.applicants[i]
        unit = graph.units[j]
        dist_km = float(graph.distance_matrix_km[i, j])
        is_prior = (app.applicant_id, unit.unit_id) in prior_pairs

        comp_score, breakdown = compute_pair_subscores(app, unit, dist_km, policy, is_prior)
        composite_scores[(i, j)] = comp_score
        score_breakdowns[(i, j)] = breakdown

        digest = deterministic_pair_tie_hash(
            policy.policy_id,
            policy.policy_version,
            app.applicant_id,
            unit.unit_id,
        )
        edge_hashes.append((digest, app.applicant_id, unit.unit_id, (i, j)))

    # Sort ascending by cryptographic digest (with applicant_id, unit_id as secondary total order)
    edge_hashes.sort(key=lambda item: (item[0], item[1], item[2]))

    num_edges = len(edge_hashes)
    edge_scale = max(1_000, num_edges + 2)

    tie_break_ranks: dict[tuple[int, int], int] = {}
    solver_weights: dict[tuple[int, int], int] = {}

    for rank_idx, (_, _, _, edge_key) in enumerate(edge_hashes, start=1):
        # Higher rank_idx means stronger tie-break preference
        tie_break_ranks[edge_key] = rank_idx
        solver_weights[edge_key] = composite_scores[edge_key] * edge_scale + rank_idx

    return ScoreMatrixBundle(
        composite_scores=composite_scores,
        score_breakdowns=score_breakdowns,
        tie_break_ranks=tie_break_ranks,
        solver_weights=solver_weights,
        edge_scale_multiplier=edge_scale,
    )
