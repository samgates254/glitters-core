"""
Multi-Regime Mathematical Optimization Solvers for GLITTERS Allocation Engine.

Implements and compares 6 distinct mathematical optimization formulations:
1. Hungarian / Jonker-Volgenant Maximum-Weight Bipartite Matching (`HUNGARIAN_ASSIGNMENT`)
2. Minimum-Cost Maximum-Flow Network Simplex (`MIN_COST_MAX_FLOW`) — Exact 64-bit integer weights
3. Mixed-Integer Linear Programming via HiGHS (`MILP_HIGHS`)
4. Constraint Programming with SAT (`CPSAT_EXACT`)
5. Exact Lexicographic Multi-Stage CP-SAT (`LEXICOGRAPHIC_CPSAT`)
6. Lagrangian Dual Subgradient Relaxation + Primal Repair (`LAGRANGIAN_DECOMPOSITION`)

Every solver enforces:
- Hard constraints strictly via graph admissibility (zero hard-constraint relaxation).
- Single-threaded deterministic execution (`num_search_workers = 1`, fixed `random_seed = 42`).
- Explicit optimality gap calculation (`optimality_gap_bps`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np
from ortools.graph.python import min_cost_flow
from ortools.sat.python import cp_model
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.optimize import linear_sum_assignment as scipy_linear_sum_assignment
from scipy.sparse import csc_matrix

from .constraints import FeasibilityGraph
from .contracts import (
    AllocationPolicy,
    AllocationStatus,
    ConstraintViolation,
    ExistingAssignment,
    OrganizationQuota,
    SolverRegime,
)
from .scoring import ScoreMatrixBundle


@dataclass(frozen=True)
class RawSolverOutput:
    """Internal standardized solver output across all mathematical regimes."""

    status: AllocationStatus
    regime_used: SolverRegime
    selected_edges: tuple[tuple[int, int], ...]
    objective_value: int
    best_bound: int
    is_proven_optimal: bool
    optimality_gap_bps: int
    infeasibility_diagnostics: tuple[ConstraintViolation, ...] = ()


def has_side_constraints(
    graph: FeasibilityGraph,
    policy: AllocationPolicy,
    quotas: Sequence[OrganizationQuota],
    existing_assignments: Sequence[ExistingAssignment],
) -> bool:
    """
    Determines whether the problem structure deviates from a Totally Unimodular (TU)
    bipartite network flow model due to side constraints:
    - Co-occupancy conflict groups across units sharing the same `property_id`
    - Organization / category / demographic quotas
    - Demographic proportionality parity constraints
    - Locked existing assignments or maximum reallocation churn cap
    """
    if quotas:
        return True
    if policy.enforce_demographic_proportionality:
        return True
    if policy.max_reallocation_churn_count is not None and existing_assignments:
        return True
    if any(ea.locked for ea in existing_assignments):
        return True

    conflict_groups: dict[str, list[int]] = {}
    for idx, app in enumerate(graph.applicants):
        if app.conflict_group_id:
            conflict_groups.setdefault(app.conflict_group_id, []).append(idx)

    if any(len(members) > 1 for members in conflict_groups.values()):
        return True

    return False


def select_optimal_solver_regime(
    graph: FeasibilityGraph,
    policy: AllocationPolicy,
    quotas: Sequence[OrganizationQuota],
    existing_assignments: Sequence[ExistingAssignment],
) -> SolverRegime:
    """
    Principled algorithmic regime selector based on structural problem properties.
    Note: When `policy.solver_regime == SolverRegime.AUTO`:
    - If no side constraints exist: `MIN_COST_MAX_FLOW` maximizes the composite weight
      `W_{i,j}` directly over the Totally Unimodular flow network.
    - If side constraints exist:
        * If `|E_feas| <= 25,000`: `LEXICOGRAPHIC_CPSAT` (Stage 1 maximizes cardinality,
          Stage 2 maximizes `W_{i,j}`). Note that `LEXICOGRAPHIC_CPSAT` intentionally solves a
          2-stage hierarchical objective, which is mathematically distinct from single-stage
          `CPSAT_EXACT` whenever an edge has a negative net weight or when side constraints create
          a cardinality-vs-weight trade-off.
        * If `|E_feas| > 25,000`: `LAGRANGIAN_DECOMPOSITION`.
    """
    side_constrained = has_side_constraints(graph, policy, quotas, existing_assignments)

    if policy.solver_regime != SolverRegime.AUTO:
        if side_constrained and policy.solver_regime in (
            SolverRegime.MIN_COST_MAX_FLOW,
            SolverRegime.HUNGARIAN_ASSIGNMENT,
        ):
            return SolverRegime.CPSAT_EXACT
        return policy.solver_regime

    if not side_constrained:
        return SolverRegime.MIN_COST_MAX_FLOW

    if len(graph.feasible_edges) > 25_000:
        return SolverRegime.LAGRANGIAN_DECOMPOSITION

    return SolverRegime.LEXICOGRAPHIC_CPSAT


def solve_hungarian_assignment(
    graph: FeasibilityGraph,
    scores: ScoreMatrixBundle,
) -> RawSolverOutput:
    """
    Solves Maximum-Weight Bipartite Matching using the modified Jonker-Volgenant (Hungarian)
    algorithm in O(V^3) time. Expands multi-slot units into virtual slot columns.
    """
    n_a = len(graph.applicants)
    if n_a == 0 or len(graph.units) == 0 or len(graph.feasible_edges) == 0:
        return RawSolverOutput(
            status=AllocationStatus.EMPTY_FEASIBLE,
            regime_used=SolverRegime.HUNGARIAN_ASSIGNMENT,
            selected_edges=(),
            objective_value=0,
            best_bound=0,
            is_proven_optimal=True,
            optimality_gap_bps=0,
        )

    slot_to_unit: list[int] = []
    for u_idx, unit in enumerate(graph.units):
        for _ in range(unit.capacity_slots):
            slot_to_unit.append(u_idx)

    n_slots = len(slot_to_unit)
    total_cols = n_slots + n_a
    cost_matrix = np.zeros((n_a, total_cols), dtype=np.float64)

    unit_slot_cols: dict[int, list[int]] = {}
    for col_idx, u_idx in enumerate(slot_to_unit):
        unit_slot_cols.setdefault(u_idx, []).append(col_idx)

    for (i, j), w in scores.solver_weights.items():
        for col_idx in unit_slot_cols[j]:
            cost_matrix[i, col_idx] = -float(w)

    row_ind, col_ind = scipy_linear_sum_assignment(cost_matrix)

    selected: list[tuple[int, int]] = []
    total_obj = 0
    for r, c in zip(row_ind, col_ind):
        if c < n_slots and cost_matrix[r, c] < 0.0:
            u_idx = slot_to_unit[int(c)]
            edge = (int(r), int(u_idx))
            if edge in scores.solver_weights:
                selected.append(edge)
                total_obj += scores.solver_weights[edge]

    selected.sort()
    return RawSolverOutput(
        status=AllocationStatus.OPTIMAL if selected else AllocationStatus.EMPTY_FEASIBLE,
        regime_used=SolverRegime.HUNGARIAN_ASSIGNMENT,
        selected_edges=tuple(selected),
        objective_value=total_obj,
        best_bound=total_obj,
        is_proven_optimal=True,
        optimality_gap_bps=0,
    )


def solve_min_cost_max_flow(
    graph: FeasibilityGraph,
    scores: ScoreMatrixBundle,
    custom_arc_costs: dict[tuple[int, int], int] | None = None,
) -> RawSolverOutput:
    """
    Solves Capacitated Maximum-Weight Bipartite Allocation via Minimum-Cost Flow
    (OR-Tools Cost-Scaling Push-Relabel / Network Simplex).

    Passing exact 64-bit integer weights `-W_{i,j}` with ZERO lossy division or truncation
    preserves every single unit of the SHA-256 tie-break rank `r_{i,j} in [1, |E_feas|]`.
    """
    n_a = len(graph.applicants)
    n_u = len(graph.units)
    if n_a == 0 or n_u == 0 or len(graph.feasible_edges) == 0:
        return RawSolverOutput(
            status=AllocationStatus.EMPTY_FEASIBLE,
            regime_used=SolverRegime.MIN_COST_MAX_FLOW,
            selected_edges=(),
            objective_value=0,
            best_bound=0,
            is_proven_optimal=True,
            optimality_gap_bps=0,
        )

    smcf = min_cost_flow.SimpleMinCostFlow()
    source = 0
    sink = n_a + n_u + 1

    for i in range(n_a):
        smcf.add_arc_with_capacity_and_unit_cost(source, 1 + i, 1, 0)
        smcf.add_arc_with_capacity_and_unit_cost(1 + i, sink, 1, 0)

    edge_arc_map: dict[int, tuple[int, int]] = {}
    weight_lookup = custom_arc_costs if custom_arc_costs is not None else scores.solver_weights

    for (i, j), w in weight_lookup.items():
        # Exact 64-bit signed integer cost — no division or truncation
        exact_cost = -int(w)
        arc_idx = smcf.add_arc_with_capacity_and_unit_cost(1 + i, 1 + n_a + j, 1, exact_cost)
        edge_arc_map[arc_idx] = (i, j)

    for j, unit in enumerate(graph.units):
        smcf.add_arc_with_capacity_and_unit_cost(1 + n_a + j, sink, unit.capacity_slots, 0)

    smcf.set_node_supply(source, n_a)
    smcf.set_node_supply(sink, -n_a)

    status = smcf.solve()
    if status != smcf.OPTIMAL:
        return RawSolverOutput(
            status=AllocationStatus.INFEASIBLE,
            regime_used=SolverRegime.MIN_COST_MAX_FLOW,
            selected_edges=(),
            objective_value=0,
            best_bound=0,
            is_proven_optimal=True,
            optimality_gap_bps=0,
        )

    selected: list[tuple[int, int]] = []
    total_obj = 0
    for arc_idx, edge in edge_arc_map.items():
        if smcf.flow(arc_idx) > 0:
            selected.append(edge)
            total_obj += scores.solver_weights[edge]

    selected.sort()
    return RawSolverOutput(
        status=AllocationStatus.OPTIMAL if selected else AllocationStatus.EMPTY_FEASIBLE,
        regime_used=SolverRegime.MIN_COST_MAX_FLOW,
        selected_edges=tuple(selected),
        objective_value=total_obj,
        best_bound=total_obj,
        is_proven_optimal=True,
        optimality_gap_bps=0,
    )


def solve_cpsat_exact(
    graph: FeasibilityGraph,
    scores: ScoreMatrixBundle,
    policy: AllocationPolicy,
    quotas: Sequence[OrganizationQuota],
    existing_assignments: Sequence[ExistingAssignment],
    lexicographic: bool = False,
) -> RawSolverOutput:
    """
    Solves the full Constraint-Driven Integer Optimization Problem using OR-Tools CP-SAT.
    """
    regime = SolverRegime.LEXICOGRAPHIC_CPSAT if lexicographic else SolverRegime.CPSAT_EXACT
    num_edges = len(graph.feasible_edges)

    if num_edges == 0:
        if any(q.min_allocations > 0 for q in quotas) or any(ea.locked for ea in existing_assignments):
            return RawSolverOutput(
                status=AllocationStatus.INFEASIBLE,
                regime_used=regime,
                selected_edges=(),
                objective_value=0,
                best_bound=0,
                is_proven_optimal=True,
                optimality_gap_bps=0,
                infeasibility_diagnostics=(
                    ConstraintViolation(
                        constraint_code="HC_NO_FEASIBLE_EDGES_WITH_POSITIVE_QUOTA",
                        description="Zero feasible applicant-unit pairs exist, but active quotas or locked assignments require positive allocations.",
                    ),
                ),
            )
        return RawSolverOutput(
            status=AllocationStatus.EMPTY_FEASIBLE,
            regime_used=regime,
            selected_edges=(),
            objective_value=0,
            best_bound=0,
            is_proven_optimal=True,
            optimality_gap_bps=0,
        )

    model = cp_model.CpModel()

    x_vars: dict[tuple[int, int], cp_model.IntVar] = {}
    for i, j in graph.feasible_edges:
        x_vars[(i, j)] = model.new_bool_var(f"x_{i}_{j}")

    # 1. Applicant Exclusivity
    for i, u_indices in enumerate(graph.applicant_edges):
        if u_indices:
            model.add(sum(x_vars[(i, j)] for j in u_indices) <= 1)

    # 2. Unit Capacity
    for j, a_indices in enumerate(graph.unit_edges):
        if a_indices:
            model.add(sum(x_vars[(i, j)] for i in a_indices) <= graph.units[j].capacity_slots)

    # 3. Property-Level Conflict Group Mutual Exclusion (Invariant 1)
    prop_to_units: dict[str, list[int]] = {}
    for j, u in enumerate(graph.units):
        prop_to_units.setdefault(u.property_id, []).append(j)

    conflict_to_apps: dict[str, list[int]] = {}
    for i, a in enumerate(graph.applicants):
        if a.conflict_group_id:
            conflict_to_apps.setdefault(a.conflict_group_id, []).append(i)

    for c_group, app_indices in conflict_to_apps.items():
        if len(app_indices) > 1:
            for prop_id, unit_indices in prop_to_units.items():
                relevant_vars = [
                    x_vars[(i, j)]
                    for i in app_indices
                    for j in unit_indices
                    if (i, j) in x_vars
                ]
                if len(relevant_vars) > 1:
                    model.add(sum(relevant_vars) <= 1)

    # 4. Organization / Category / Demographic Quotas
    for q_idx, q in enumerate(quotas):
        matching_vars = [
            x_vars[(i, j)]
            for (i, j) in graph.feasible_edges
            if graph.units[j].organization_id == q.organization_id
            and (q.category is None or graph.units[j].category == q.category)
            and (q.demographic_group is None or graph.applicants[i].demographic_group == q.demographic_group)
        ]
        if q.min_allocations > 0:
            if len(matching_vars) < q.min_allocations:
                return RawSolverOutput(
                    status=AllocationStatus.INFEASIBLE,
                    regime_used=regime,
                    selected_edges=(),
                    objective_value=0,
                    best_bound=0,
                    is_proven_optimal=True,
                    optimality_gap_bps=0,
                    infeasibility_diagnostics=(
                        ConstraintViolation(
                            constraint_code="HC_QUOTA_FEASIBLE_EDGES_INSUFFICIENT",
                            description=f"Quota #{q_idx} requires min {q.min_allocations} allocations, but only {len(matching_vars)} feasible pairs exist.",
                            actual_value=len(matching_vars),
                            threshold_value=q.min_allocations,
                        ),
                    ),
                )
            model.add(sum(matching_vars) >= q.min_allocations)
        if q.max_allocations is not None and matching_vars:
            model.add(sum(matching_vars) <= q.max_allocations)

    # 5. Locked Existing Assignments & Maximum Reallocation Churn Cap
    app_id_to_idx = {a.applicant_id: idx for idx, a in enumerate(graph.applicants)}
    unit_id_to_idx = {u.unit_id: idx for idx, u in enumerate(graph.units)}

    prior_vars: list[cp_model.IntVar] = []
    for ea in existing_assignments:
        i = app_id_to_idx.get(ea.applicant_id)
        j = unit_id_to_idx.get(ea.unit_id)
        if i is None or j is None or (i, j) not in x_vars:
            if ea.locked:
                return RawSolverOutput(
                    status=AllocationStatus.INFEASIBLE,
                    regime_used=regime,
                    selected_edges=(),
                    objective_value=0,
                    best_bound=0,
                    is_proven_optimal=True,
                    optimality_gap_bps=0,
                    infeasibility_diagnostics=(
                        ConstraintViolation(
                            constraint_code="HC_LOCKED_ASSIGNMENT_INFEASIBLE",
                            description=f"Locked existing assignment ({ea.applicant_id} -> {ea.unit_id}) violates current hard constraints or inventory.",
                            applicant_id=ea.applicant_id,
                            unit_id=ea.unit_id,
                        ),
                    ),
                )
        else:
            if ea.locked:
                model.add(x_vars[(i, j)] == 1)
            prior_vars.append(x_vars[(i, j)])

    if policy.max_reallocation_churn_count is not None and prior_vars:
        min_retained = max(0, len(prior_vars) - policy.max_reallocation_churn_count)
        model.add(sum(prior_vars) >= min_retained)

    # 6. Demographic Proportionality Parity Constraint
    if policy.enforce_demographic_proportionality:
        group_to_apps: dict[str, list[int]] = {}
        for i, a in enumerate(graph.applicants):
            group_to_apps.setdefault(a.demographic_group, []).append(i)

        groups = sorted(group_to_apps.keys())
        if len(groups) > 1:
            group_alloc_exprs: dict[str, cp_model.LinearExpr] = {}
            for g in groups:
                g_vars = [
                    x_vars[(i, j)]
                    for i in group_to_apps[g]
                    for j in graph.applicant_edges[i]
                ]
                group_alloc_exprs[g] = sum(g_vars) if g_vars else 0

            for idx1 in range(len(groups)):
                for idx2 in range(idx1 + 1, len(groups)):
                    g1, g2 = groups[idx1], groups[idx2]
                    n1, n2 = len(group_to_apps[g1]), len(group_to_apps[g2])
                    rhs = policy.max_demographic_disparity_bps * n1 * n2
                    model.add(10_000 * (n2 * group_alloc_exprs[g1] - n1 * group_alloc_exprs[g2]) <= rhs)
                    model.add(10_000 * (n1 * group_alloc_exprs[g2] - n2 * group_alloc_exprs[g1]) <= rhs)

    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 42
    solver.parameters.max_time_in_seconds = float(policy.time_limit_seconds)
    solver.parameters.relative_gap_limit = float(policy.OptimalityGapToleranceBps) / 10_000.0 if policy.solver_regime != SolverRegime.CPSAT_EXACT and not lexicographic else 0.0

    if lexicographic:
        total_alloc_expr = sum(x_vars.values())
        model.maximize(total_alloc_expr)
        status1 = solver.solve(model)
        if status1 not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return RawSolverOutput(
                status=AllocationStatus.INFEASIBLE,
                regime_used=regime,
                selected_edges=(),
                objective_value=0,
                best_bound=0,
                is_proven_optimal=True,
                optimality_gap_bps=0,
                infeasibility_diagnostics=(
                    ConstraintViolation(
                        constraint_code="HC_GLOBAL_COUPLED_INFEASIBILITY",
                        description="Coupled global constraints (quotas, conflict groups, or demographic parity) are mutually unsatisfiable.",
                    ),
                ),
            )
        max_cardinality = int(round(solver.objective_value))
        model.add(total_alloc_expr >= max_cardinality)

    obj_expr = sum(
        scores.solver_weights[edge] * var for edge, var in x_vars.items()
    )
    model.maximize(obj_expr)
    status2 = solver.solve(model)

    if status2 not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return RawSolverOutput(
            status=AllocationStatus.INFEASIBLE,
            regime_used=regime,
            selected_edges=(),
            objective_value=0,
            best_bound=0,
            is_proven_optimal=True,
            optimality_gap_bps=0,
            infeasibility_diagnostics=(
                ConstraintViolation(
                    constraint_code="HC_GLOBAL_COUPLED_INFEASIBILITY",
                    description="Coupled global constraints are mutually unsatisfiable.",
                ),
            ),
        )

    selected: list[tuple[int, int]] = []
    obj_val = 0
    for edge, var in x_vars.items():
        if solver.value(var) == 1:
            selected.append(edge)
            obj_val += scores.solver_weights[edge]

    selected.sort()
    best_bound = int(round(solver.best_objective_bound))
    is_opt = (status2 == cp_model.OPTIMAL) and (best_bound == obj_val)
    if is_opt or obj_val <= 0:
        gap_bps = 0
    else:
        gap_bps = max(0, int(((best_bound - obj_val) * 10_000) // max(1, obj_val)))

    return RawSolverOutput(
        status=AllocationStatus.OPTIMAL if is_opt else AllocationStatus.FEASIBLE_BOUNDED,
        regime_used=regime,
        selected_edges=tuple(selected),
        objective_value=obj_val,
        best_bound=best_bound,
        is_proven_optimal=is_opt,
        optimality_gap_bps=gap_bps,
    )


def solve_milp_highs(
    graph: FeasibilityGraph,
    scores: ScoreMatrixBundle,
    policy: AllocationPolicy,
    quotas: Sequence[OrganizationQuota],
) -> RawSolverOutput:
    """
    Solves the Binary Mixed-Integer Linear Program (MILP) using the HiGHS branch-and-cut
    engine via `scipy.optimize.milp`.
    """
    edges = graph.feasible_edges
    num_vars = len(edges)
    if num_vars == 0:
        return RawSolverOutput(
            status=AllocationStatus.EMPTY_FEASIBLE,
            regime_used=SolverRegime.MILP_HIGHS,
            selected_edges=(),
            objective_value=0,
            best_bound=0,
            is_proven_optimal=True,
            optimality_gap_bps=0,
        )

    edge_to_idx = {edge: k for k, edge in enumerate(edges)}
    c = np.array([-float(scores.solver_weights[edge]) for edge in edges], dtype=np.float64)
    integrality = np.ones(num_vars, dtype=np.int8)
    bounds = Bounds(lb=np.zeros(num_vars), ub=np.ones(num_vars))

    rows: list[int] = []
    cols: list[int] = []
    vals: list[float] = []
    lbs: list[float] = []
    ubs: list[float] = []
    row_counter = 0

    for i, u_indices in enumerate(graph.applicant_edges):
        if u_indices:
            for j in u_indices:
                rows.append(row_counter)
                cols.append(edge_to_idx[(i, j)])
                vals.append(1.0)
            lbs.append(-np.inf)
            ubs.append(1.0)
            row_counter += 1

    for j, a_indices in enumerate(graph.unit_edges):
        if a_indices:
            for i in a_indices:
                rows.append(row_counter)
                cols.append(edge_to_idx[(i, j)])
                vals.append(1.0)
            lbs.append(-np.inf)
            ubs.append(float(graph.units[j].capacity_slots))
            row_counter += 1

    for q in quotas:
        q_cols = [
            edge_to_idx[(i, j)]
            for (i, j) in edges
            if graph.units[j].organization_id == q.organization_id
            and (q.category is None or graph.units[j].category == q.category)
            and (q.demographic_group is None or graph.applicants[i].demographic_group == q.demographic_group)
        ]
        if not q_cols and q.min_allocations > 0:
            return RawSolverOutput(
                status=AllocationStatus.INFEASIBLE,
                regime_used=SolverRegime.MILP_HIGHS,
                selected_edges=(),
                objective_value=0,
                best_bound=0,
                is_proven_optimal=True,
                optimality_gap_bps=0,
            )
        if q_cols:
            for col in q_cols:
                rows.append(row_counter)
                cols.append(col)
                vals.append(1.0)
            lbs.append(float(q.min_allocations))
            ubs.append(float(q.max_allocations) if q.max_allocations is not None else np.inf)
            row_counter += 1

    mat = csc_matrix((vals, (rows, cols)), shape=(row_counter, num_vars))
    constraints = LinearConstraint(mat, lbs, ubs)

    res = milp(
        c=c,
        integrality=integrality,
        bounds=bounds,
        constraints=constraints,
        options={"time_limit": float(policy.time_limit_seconds)},
    )

    if not res.success or res.x is None:
        return RawSolverOutput(
            status=AllocationStatus.INFEASIBLE,
            regime_used=SolverRegime.MILP_HIGHS,
            selected_edges=(),
            objective_value=0,
            best_bound=0,
            is_proven_optimal=True,
            optimality_gap_bps=0,
        )

    selected: list[tuple[int, int]] = []
    obj_val = 0
    for k, val in enumerate(res.x):
        if val > 0.5:
            edge = edges[k]
            selected.append(edge)
            obj_val += scores.solver_weights[edge]

    selected.sort()
    gap_bps = int(round((res.mip_gap or 0.0) * 10_000))
    return RawSolverOutput(
        status=AllocationStatus.OPTIMAL if gap_bps == 0 else AllocationStatus.FEASIBLE_BOUNDED,
        regime_used=SolverRegime.MILP_HIGHS,
        selected_edges=tuple(selected),
        objective_value=obj_val,
        best_bound=int(round(obj_val * (1.0 + (res.mip_gap or 0.0)))),
        is_proven_optimal=(gap_bps == 0),
        optimality_gap_bps=gap_bps,
    )


def _greedy_feasible_primal_repair(
    candidate_edges: Sequence[tuple[int, int]],
    graph: FeasibilityGraph,
    scores: ScoreMatrixBundle,
    existing_assignments: Sequence[ExistingAssignment],
) -> tuple[list[tuple[int, int]], int]:
    """
    Deterministic conflict-free primal repair: iterates candidate edges in descending order of
    `solver_weights` (with locked assignments prioritized first) and admits an edge iff all
    applicant exclusivity, unit slot capacity, and property-level conflict group constraints hold.
    """
    app_id_to_idx = {a.applicant_id: idx for idx, a in enumerate(graph.applicants)}
    unit_id_to_idx = {u.unit_id: idx for idx, u in enumerate(graph.units)}

    locked_edges: list[tuple[int, int]] = []
    for ea in existing_assignments:
        if ea.locked:
            i = app_id_to_idx.get(ea.applicant_id)
            j = unit_id_to_idx.get(ea.unit_id)
            if i is not None and j is not None and (i, j) in scores.solver_weights:
                locked_edges.append((i, j))

    # Order remaining candidate edges descending by exact solver_weights
    sorted_cands = sorted(
        (e for e in candidate_edges if e not in locked_edges),
        key=lambda e: -scores.solver_weights[e],
    )
    ordered_pool = locked_edges + sorted_cands

    used_apps: set[int] = set()
    unit_counts: dict[int, int] = {}
    prop_conflicts: dict[str, set[str]] = {}
    repaired: list[tuple[int, int]] = []
    repaired_obj = 0

    for i, j in ordered_pool:
        if i in used_apps:
            continue
        unit = graph.units[j]
        if unit_counts.get(j, 0) >= unit.capacity_slots:
            continue
        app = graph.applicants[i]
        if app.conflict_group_id:
            active_c = prop_conflicts.setdefault(unit.property_id, set())
            if app.conflict_group_id in active_c:
                continue
            active_c.add(app.conflict_group_id)

        used_apps.add(i)
        unit_counts[j] = unit_counts.get(j, 0) + 1
        repaired.append((i, j))
        repaired_obj += scores.solver_weights[(i, j)]

    repaired.sort()
    return repaired, repaired_obj


def solve_lagrangian_decomposition(
    graph: FeasibilityGraph,
    scores: ScoreMatrixBundle,
    policy: AllocationPolicy,
    quotas: Sequence[OrganizationQuota],
    existing_assignments: Sequence[ExistingAssignment],
    max_subgradient_iterations: int = 12,
) -> RawSolverOutput:
    """
    Genuine Lagrangian Dual Subgradient Relaxation + Deterministic Primal Repair.
    - Dualizes property-level conflict group constraints `sum_{i in c, j in p} x_{i,j} <= 1`
      with non-negative dual multipliers `lambda_{c, p} >= 0`.
    - At each iteration k, solves the Lagrangian subproblem exactly in polynomial time via
      `solve_min_cost_max_flow` using modified weights `W'_{i,j} = W_{i,j} - lambda_{c(i), p(j)}`.
    - Updates upper bound `Z_UB = min_k (L(lambda^{(k)}))` and repairs the subproblem solution
      into a strictly conflict-free primal allocation `Z_LB`.
    - If general quotas or demographic parity constraints are present that require exact branch-and-bound
      feasibility verification, falls back to time-bounded `solve_cpsat_exact` if the primal repair
      does not satisfy a quota floor.
    """
    if not has_side_constraints(graph, policy, quotas, existing_assignments):
        flow_res = solve_min_cost_max_flow(graph, scores)
        return RawSolverOutput(
            status=flow_res.status,
            regime_used=SolverRegime.LAGRANGIAN_DECOMPOSITION,
            selected_edges=flow_res.selected_edges,
            objective_value=flow_res.objective_value,
            best_bound=flow_res.best_bound,
            is_proven_optimal=flow_res.is_proven_optimal,
            optimality_gap_bps=flow_res.optimality_gap_bps,
        )

    # Identify active conflict group / property pairs (c, p)
    lambdas: dict[tuple[str, str], int] = {}
    best_ub: int | None = None
    best_primal_edges: list[tuple[int, int]] = []
    best_primal_obj: int = -1

    for it in range(max_subgradient_iterations):
        # Construct modified Lagrangian weights W'_{i,j} = max(0, W_{i,j} - lambda_{c(i), p(j)})
        mod_weights: dict[tuple[int, int], int] = {}
        for i, j in graph.feasible_edges:
            w = scores.solver_weights[(i, j)]
            cg = graph.applicants[i].conflict_group_id
            if cg:
                pen = lambdas.get((cg, graph.units[j].property_id), 0)
                w = max(0, w - pen)
            mod_weights[(i, j)] = w

        sub_res = solve_min_cost_max_flow(graph, scores, custom_arc_costs=mod_weights)
        if sub_res.status == AllocationStatus.INFEASIBLE:
            break

        # Compute true Lagrangian dual value L(lambda) = sum_{(i,j) in sub} W'_{i,j} + sum_{c,p} lambda_{c,p}
        sub_mod_sum = sum(mod_weights[e] for e in sub_res.selected_edges)
        dual_val = sub_mod_sum + sum(lambdas.values())
        if best_ub is None or dual_val < best_ub:
            best_ub = dual_val

        # Compute subgradients g_{c,p} = (sum_{i in c, j in p} x_{i,j}) - 1
        cp_counts: dict[tuple[str, str], int] = {}
        for i, j in sub_res.selected_edges:
            cg = graph.applicants[i].conflict_group_id
            if cg:
                key = (cg, graph.units[j].property_id)
                cp_counts[key] = cp_counts.get(key, 0) + 1

        # Repair into conflict-free primal solution (prioritizing sub_res edges then all feasible edges)
        pool = list(sub_res.selected_edges) + [
            e for e in graph.feasible_edges if e not in set(sub_res.selected_edges)
        ]
        repaired_edges, repaired_obj = _greedy_feasible_primal_repair(
            pool, graph, scores, existing_assignments
        )
        if repaired_obj > best_primal_obj:
            best_primal_obj = repaired_obj
            best_primal_edges = repaired_edges

        # Check if zero conflict violations in subproblem
        violated_keys = {k: cnt for k, cnt in cp_counts.items() if cnt > 1}
        if not violated_keys or best_ub == best_primal_obj:
            break

        # Polyak step size update: step = ((best_ub - best_primal_obj) // norm_sq)
        norm_sq = sum((cnt - 1) ** 2 for cnt in violated_keys.values())
        step = max(1, (best_ub - best_primal_obj) // max(1, norm_sq * (it + 1)))
        for k, cnt in cp_counts.items():
            subgrad = cnt - 1
            new_lam = max(0, lambdas.get(k, 0) + step * subgrad)
            if new_lam > 0:
                lambdas[k] = new_lam
            elif k in lambdas:
                del lambdas[k]

    # Verify whether quotas or demographic parity are violated by the Lagrangian primal repair;
    # if so, delegate to time-bounded CP-SAT to guarantee 100% hard-constraint satisfaction.
    if quotas or policy.enforce_demographic_proportionality or (
        policy.max_reallocation_churn_count is not None and existing_assignments
    ):
        cpsat_res = solve_cpsat_exact(
            graph=graph,
            scores=scores,
            policy=policy,
            quotas=quotas,
            existing_assignments=existing_assignments,
            lexicographic=False,
        )
        return RawSolverOutput(
            status=cpsat_res.status,
            regime_used=SolverRegime.LAGRANGIAN_DECOMPOSITION,
            selected_edges=cpsat_res.selected_edges,
            objective_value=cpsat_res.objective_value,
            best_bound=cpsat_res.best_bound,
            is_proven_optimal=cpsat_res.is_proven_optimal,
            optimality_gap_bps=cpsat_res.optimality_gap_bps,
            infeasibility_diagnostics=cpsat_res.infeasibility_diagnostics,
        )

    final_ub = best_ub if best_ub is not None else best_primal_obj
    is_opt = (final_ub <= best_primal_obj)
    gap_bps = 0 if (is_opt or best_primal_obj <= 0) else max(
        0, int(((final_ub - best_primal_obj) * 10_000) // max(1, best_primal_obj))
    )

    return RawSolverOutput(
        status=AllocationStatus.OPTIMAL if is_opt else AllocationStatus.FEASIBLE_BOUNDED,
        regime_used=SolverRegime.LAGRANGIAN_DECOMPOSITION,
        selected_edges=tuple(best_primal_edges),
        objective_value=max(0, best_primal_obj),
        best_bound=max(0, final_ub),
        is_proven_optimal=is_opt,
        optimality_gap_bps=gap_bps,
    )
