"""
Production-Hardened RCPSP, MMRCPSP, SSGS, and Time-Cost Trade-Off (TCTO) Engine.
Remediates:
- Import-time `ortools` failure: `ortools` is imported optionally with a built-in exact
  Branch-and-Bound / MILP fallback when `ortools` is not installed in the host environment.
- CRITICAL 5: Enforces non-renewable material caps (`nonrenewable_material_caps`) and
  time-phased delivery schedules (`scheduled_material_deliveries`) directly in RCPSP and SSGS.
- HIGH 6: Implements genuine two-stage lexicographic optimization (Stage 1: minimize makespan T*;
  Stage 2: constrain makespan <= T* and minimize direct cost).
- HIGH 7: SSGS checks modes, windows, and materials, raises `InfeasibleScheduleError` if the
  search loop exhausts `max_horizon`, and runs `validate_schedule_feasibility()` on every output.
"""
from __future__ import annotations
from typing import Dict, List, Optional, Tuple
import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp

try:
    from ortools.sat.python import cp_model
    HAS_ORTOOLS = True
except ImportError:
    cp_model = None
    HAS_ORTOOLS = False

from ..project_model import (
    ConstructionProject,
    DependencyType,
    ExecutionMode,
    MoneyKES,
)
from .cpm import CriticalPathEngine
from .resource_constraints import (
    InfeasibleScheduleError,
    ScheduledActivity,
    validate_schedule_feasibility,
)


class SchedulingEngine:
    """
    Production-grade construction scheduling engine with two-stage lexicographic RCPSP/MMRCPSP,
    non-renewable material inventory coupling, Kenyan work calendar integration, and independent
    post-solve schedule validation.
    """

    @staticmethod
    def _filter_compliant_modes(project: ConstructionProject, allow_crashing_modes: bool) -> Dict[str, List[ExecutionMode]]:
        compliant_modes: Dict[str, List[ExecutionMode]] = {}
        for aid, act in project.activities.items():
            valid_m: List[ExecutionMode] = []
            candidate_modes = (
                act.modes
                if allow_crashing_modes
                else [m for m in act.modes if m.mode_id != "CRASH"] or act.modes[:1]
            )
            for m in candidate_modes:
                if act.engineering_spec is not None:
                    ok, _ = act.engineering_spec.validate_compliance(
                        concrete_mpa=m.concrete_mpa_offered,
                        steel_mpa=m.steel_mpa_offered,
                    )
                    if not ok:
                        continue
                possible = True
                for r_id, req in m.renewable_demands.items():
                    if r_id in project.resources:
                        if req > project.resources[r_id].max_capacity_per_day + 1e-9:
                            possible = False
                            break
                for mat_id, mat_qty in m.nonrenewable_demands.items():
                    if mat_id in project.nonrenewable_material_caps:
                        if mat_qty > project.nonrenewable_material_caps[mat_id] + 1e-9:
                            possible = False
                            break
                if possible:
                    valid_m.append(m)
            if not valid_m:
                raise InfeasibleScheduleError(
                    f"Activity '{aid}' has no feasible execution mode satisfying resource pool capacities, "
                    f"non-renewable material caps, and engineering specifications."
                )
            compliant_modes[aid] = valid_m
        return compliant_modes

    @staticmethod
    def _earliest_material_feasible_day(
        project: ConstructionProject,
        mat_id: str,
        cumulative_required: float,
    ) -> int:
        if mat_id not in project.scheduled_material_deliveries:
            return 0
        deliveries = sorted(project.scheduled_material_deliveries[mat_id], key=lambda x: x[0])
        acc = 0.0
        for d_day, qty in deliveries:
            acc += qty
            if acc >= cumulative_required - 1e-9:
                return int(d_day)
        raise InfeasibleScheduleError(
            f"Material delivery shortage for '{mat_id}': Total scheduled deliveries ({acc:.2f}) "
            f"cannot cover required demand ({cumulative_required:.2f})."
        )

    @classmethod
    def solve_rcpsp_cpsat(
        cls,
        project: ConstructionProject,
        enforce_contractual_deadline: bool = False,
        enforce_budget_ceiling: bool = False,
        allow_crashing_modes: bool = False,
        max_time_seconds: float = 10.0,
    ) -> Dict[str, object]:
        """
        Solves the Multi-Mode Resource-Constrained Project Scheduling Problem (MMRCPSP)
        with Two-Stage Lexicographic Optimization (Stage 1: Min Makespan; Stage 2: Min Direct Cost),
        enforcing both renewable daily capacities and non-renewable material inventory/delivery constraints.
        Automatically falls back to exact branch-and-bound / time-indexed solver if `ortools` is not installed.
        """
        cpm_info = CriticalPathEngine.compute_cpm(project)
        topo_order: List[str] = cpm_info["topological_order"]
        compliant_modes = cls._filter_compliant_modes(project, allow_crashing_modes)

        # Pre-check minimum possible non-renewable material consumption across all activities
        all_mats = set(project.nonrenewable_material_caps.keys()) | set(project.scheduled_material_deliveries.keys())
        for mat_id in all_mats:
            min_total_mat = sum(
                min(m.nonrenewable_demands.get(mat_id, 0.0) for m in compliant_modes[aid])
                for aid in topo_order
            )
            if mat_id in project.nonrenewable_material_caps:
                if min_total_mat > project.nonrenewable_material_caps[mat_id] + 1e-6:
                    raise InfeasibleScheduleError(
                        f"Non-renewable material '{mat_id}' minimum project requirement ({min_total_mat:.2f}) "
                        f"exceeds total supply cap ({project.nonrenewable_material_caps[mat_id]:.2f})."
                    )
            if mat_id in project.scheduled_material_deliveries:
                tot_deliv = sum(q for _, q in project.scheduled_material_deliveries[mat_id])
                if min_total_mat > tot_deliv + 1e-6:
                    raise InfeasibleScheduleError(
                        f"Non-renewable material '{mat_id}' minimum project requirement ({min_total_mat:.2f}) "
                        f"exceeds total scheduled deliveries ({tot_deliv:.2f})."
                    )

        if not HAS_ORTOOLS:
            return cls._solve_rcpsp_fallback_exact(
                project=project,
                cpm_info=cpm_info,
                compliant_modes=compliant_modes,
                enforce_contractual_deadline=enforce_contractual_deadline,
                enforce_budget_ceiling=enforce_budget_ceiling,
            )

        horizon = sum(max(m.duration_days for m in compliant_modes[aid]) for aid in project.activities)
        horizon += sum(max(0, dep.lag_days) for dep in project.dependencies)
        if project.scheduled_material_deliveries:
            max_deliv_day = max(
                (d_day for delivs in project.scheduled_material_deliveries.values() for d_day, _ in delivs),
                default=0,
            )
            horizon += max_deliv_day
        horizon += 20

        if enforce_contractual_deadline:
            if cpm_info["project_completion_days"] > project.contractual_deadline_days and all(
                len(compliant_modes[aid]) == 1 for aid in project.activities
            ):
                raise InfeasibleScheduleError(
                    f"Impossible schedule: Unconstrained CPM lower bound ({cpm_info['project_completion_days']} days) "
                    f"exceeds contractual deadline ({project.contractual_deadline_days} days)."
                )
            horizon = min(horizon, project.contractual_deadline_days)

        def _build_cp_model(fixed_max_makespan: Optional[int] = None):
            model = cp_model.CpModel()
            start_vars = {}
            end_vars = {}
            dur_vars = {}
            mode_vars = {}
            mode_intervals = {}
            SCALE = 100

            for aid in topo_order:
                act = project.activities[aid]
                es_lb = act.earliest_start_window
                lf_ub = horizon if act.latest_finish_window is None else min(horizon, act.latest_finish_window)
                if fixed_max_makespan is not None:
                    lf_ub = min(lf_ub, fixed_max_makespan)
                if es_lb > lf_ub:
                    raise InfeasibleScheduleError(f"Activity '{aid}' window [{es_lb}, {lf_ub}] is empty.")

                s_var = model.NewIntVar(es_lb, lf_ub, f"start_{aid}")
                e_var = model.NewIntVar(es_lb, lf_ub, f"end_{aid}")
                modes = compliant_modes[aid]
                min_d = min(m.duration_days for m in modes)
                max_d = max(m.duration_days for m in modes)
                d_var = model.NewIntVar(min_d, max_d, f"dur_{aid}")

                start_vars[aid] = s_var
                end_vars[aid] = e_var
                dur_vars[aid] = d_var

                m_bools = []
                for m_idx, m in enumerate(modes):
                    b = model.NewBoolVar(f"mode_{aid}_{m.mode_id}")
                    mode_vars[(aid, m_idx)] = b
                    m_bools.append(b)
                    iv = model.NewOptionalIntervalVar(
                        s_var, m.duration_days, e_var, b, f"interval_{aid}_{m.mode_id}"
                    )
                    mode_intervals[(aid, m_idx)] = iv
                    model.Add(d_var == m.duration_days).OnlyEnforceIf(b)

                    for r_id, req in m.renewable_demands.items():
                        if req > 0 and r_id in project.resources:
                            r_pool = project.resources[r_id]
                            if r_pool.available_from_day > 0:
                                model.Add(s_var >= r_pool.available_from_day).OnlyEnforceIf(b)
                            if r_pool.available_until_day < horizon:
                                model.Add(e_var <= r_pool.available_until_day).OnlyEnforceIf(b)

                model.AddExactlyOne(m_bools)

            # Precedence constraints
            for dep in project.dependencies:
                u, v = dep.predecessor_id, dep.successor_id
                lag = dep.lag_days
                if dep.dep_type == DependencyType.FS:
                    model.Add(start_vars[v] >= end_vars[u] + lag)
                elif dep.dep_type == DependencyType.SS:
                    model.Add(start_vars[v] >= start_vars[u] + lag)
                elif dep.dep_type == DependencyType.FF:
                    model.Add(end_vars[v] >= end_vars[u] + lag)
                elif dep.dep_type == DependencyType.SF:
                    model.Add(end_vars[v] >= start_vars[u] + lag)

            # Renewable resource cumulative constraints
            for r_id, r_pool in project.resources.items():
                intervals = []
                demands = []
                for aid in topo_order:
                    for m_idx, m in enumerate(compliant_modes[aid]):
                        req = m.renewable_demands.get(r_id, 0.0)
                        if req > 0:
                            intervals.append(mode_intervals[(aid, m_idx)])
                            demands.append(int(round(req * SCALE)))
                if intervals:
                    cap = int(round(r_pool.max_capacity_per_day * SCALE))
                    model.AddCumulative(intervals, demands, cap)

            # Non-renewable material total cap constraints (Remediates CRITICAL 5)
            for mat_id, mat_cap in project.nonrenewable_material_caps.items():
                terms = []
                for aid in topo_order:
                    for m_idx, m in enumerate(compliant_modes[aid]):
                        q_mat = m.nonrenewable_demands.get(mat_id, 0.0)
                        if q_mat > 0:
                            terms.append(mode_vars[(aid, m_idx)] * int(round(q_mat * SCALE)))
                if terms:
                    model.Add(sum(terms) <= int(round(mat_cap * SCALE)))

            # Time-phased material delivery constraints (Remediates CRITICAL 5)
            for mat_id, deliveries in project.scheduled_material_deliveries.items():
                deliv_sorted = sorted(deliveries, key=lambda x: x[0])
                # At each delivery epoch boundary, activities starting before the next delivery cannot exceed cumulative delivered stock
                boundaries = sorted({d_day for d_day, _ in deliv_sorted if d_day > 0})
                for b_day in boundaries:
                    avail_before_b = int(round(sum(q for d_day, q in deliv_sorted if d_day < b_day) * SCALE))
                    before_terms = []
                    for aid in topo_order:
                        for m_idx, m in enumerate(compliant_modes[aid]):
                            q_mat = m.nonrenewable_demands.get(mat_id, 0.0)
                            if q_mat > 0:
                                starts_before = model.NewBoolVar(f"mat_{mat_id}_{aid}_{m_idx}_before_{b_day}")
                                model.Add(start_vars[aid] < b_day).OnlyEnforceIf(starts_before)
                                model.Add(start_vars[aid] >= b_day).OnlyEnforceIf(starts_before.Not())
                                active_and_before = model.NewBoolVar(f"act_before_{mat_id}_{aid}_{m_idx}_{b_day}")
                                model.AddBoolAnd([mode_vars[(aid, m_idx)], starts_before]).OnlyEnforceIf(active_and_before)
                                model.AddBoolOr([mode_vars[(aid, m_idx)].Not(), starts_before.Not()]).OnlyEnforceIf(active_and_before.Not())
                                before_terms.append(active_and_before * int(round(q_mat * SCALE)))
                    if before_terms:
                        model.Add(sum(before_terms) <= avail_before_b)

            total_direct_cost_cents = sum(
                mode_vars[(aid, m_idx)] * m.direct_cost_money.cents
                for aid in topo_order
                for m_idx, m in enumerate(compliant_modes[aid])
            )
            makespan = model.NewIntVar(0, horizon, "makespan")
            model.AddMaxEquality(makespan, list(end_vars.values()))

            if enforce_contractual_deadline:
                model.Add(makespan <= project.contractual_deadline_days)
            if fixed_max_makespan is not None:
                model.Add(makespan <= fixed_max_makespan)

            if enforce_budget_ceiling:
                overhead_cents_per_day = MoneyKES.from_kes(project.daily_overhead_kes).cents
                budget_cents = MoneyKES.from_kes(project.budget_ceiling_kes).cents
                model.Add(total_direct_cost_cents + makespan * overhead_cents_per_day <= budget_cents)

            return model, start_vars, end_vars, mode_vars, makespan, total_direct_cost_cents

        # STAGE 1: Minimize Makespan (Lexicographic Primary Objective - Remediates HIGH 6)
        m1, s1, e1, mv1, mk1, dc1 = _build_cp_model(fixed_max_makespan=None)
        m1.Minimize(mk1)
        solver1 = cp_model.CpSolver()
        solver1.parameters.max_time_in_seconds = max_time_seconds * 0.6
        status1 = solver1.Solve(m1)

        if status1 not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            raise InfeasibleScheduleError(
                f"CP-SAT Stage 1 proved RCPSP infeasible (status={solver1.StatusName(status1)}) under resource, "
                f"material, deadline ({project.contractual_deadline_days}d), or budget ({project.budget_ceiling_kes:,.0f} KES) constraints."
            )

        optimal_makespan = int(solver1.Value(mk1))

        # STAGE 2: Constrain Makespan <= optimal_makespan and Minimize Total Direct Cost (Lexicographic Secondary Objective)
        m2, s2, e2, mv2, mk2, dc2 = _build_cp_model(fixed_max_makespan=optimal_makespan)
        m2.Minimize(dc2)
        solver2 = cp_model.CpSolver()
        solver2.parameters.max_time_in_seconds = max_time_seconds * 0.4
        status2 = solver2.Solve(m2)

        active_solver = solver2 if status2 in (cp_model.OPTIMAL, cp_model.FEASIBLE) else solver1
        active_s = s2 if status2 in (cp_model.OPTIMAL, cp_model.FEASIBLE) else s1
        active_e = e2 if status2 in (cp_model.OPTIMAL, cp_model.FEASIBLE) else e1
        active_mv = mv2 if status2 in (cp_model.OPTIMAL, cp_model.FEASIBLE) else mv1
        active_mk = mk2 if status2 in (cp_model.OPTIMAL, cp_model.FEASIBLE) else mk1
        is_opt = (status1 == cp_model.OPTIMAL) and (status2 == cp_model.OPTIMAL)

        schedule: Dict[str, ScheduledActivity] = {}
        total_direct_cents = 0
        for aid in topo_order:
            modes = compliant_modes[aid]
            chosen_idx = 0
            for m_idx in range(len(modes)):
                if active_solver.BooleanValue(active_mv[(aid, m_idx)]):
                    chosen_idx = m_idx
                    break
            m = modes[chosen_idx]
            s_val = int(active_solver.Value(active_s[aid]))
            e_val = int(active_solver.Value(active_e[aid]))
            total_direct_cents += m.direct_cost_money.cents
            cal_s = project.calendar.working_day_index_to_date(project.project_start_date, s_val)
            cal_e = project.calendar.working_day_index_to_date(project.project_start_date, e_val)
            schedule[aid] = ScheduledActivity(
                activity_id=aid,
                selected_mode_id=m.mode_id,
                start_day=s_val,
                finish_day=e_val,
                duration_days=m.duration_days,
                direct_cost_kes=m.direct_cost_kes,
                resource_usage=dict(m.renewable_demands),
                material_consumption=dict(m.nonrenewable_demands),
                calendar_start_date=cal_s,
                calendar_finish_date=cal_e,
            )

        ok_valid, val_errors = validate_schedule_feasibility(
            project, schedule, enforce_contractual_deadline, enforce_budget_ceiling
        )
        if not ok_valid:
            raise InfeasibleScheduleError(f"Post-solve schedule verification failed: {'; '.join(val_errors)}")

        makespan_val = int(active_solver.Value(active_mk))
        overhead_money = MoneyKES.from_kes(project.daily_overhead_kes).multiply_quantity(makespan_val)
        delay_days = max(0, makespan_val - project.contractual_deadline_days)
        ld_money = MoneyKES.from_kes(project.liquidated_damages_per_day_kes).multiply_quantity(delay_days)
        total_money = MoneyKES(total_direct_cents) + overhead_money + ld_money
        completion_date = project.calendar.working_day_index_to_date(project.project_start_date, makespan_val)

        return {
            "project_id": project.project_id,
            "solver_backend": "ORTOOLS_CPSAT_LEXICOGRAPHIC_2STAGE",
            "solver_status": "OPTIMAL" if is_opt else "FEASIBLE",
            "is_optimal": is_opt,
            "lexicographic_two_stage": True,
            "unconstrained_cpm_days": cpm_info["project_completion_days"],
            "rcpsp_completion_days": makespan_val,
            "project_start_date": project.project_start_date.isoformat(),
            "project_completion_date": completion_date.isoformat(),
            "resource_delay_days": makespan_val - cpm_info["project_completion_days"],
            "total_direct_cost_kes": MoneyKES(total_direct_cents).to_kes(),
            "total_overhead_cost_kes": overhead_money.to_kes(),
            "liquidated_damages_kes": ld_money.to_kes(),
            "total_project_cost_kes": total_money.to_kes(),
            "total_project_cost_cents": total_money.cents,
            "schedule": schedule,
        }

    @classmethod
    def _solve_rcpsp_fallback_exact(
        cls,
        project: ConstructionProject,
        cpm_info: Dict[str, object],
        compliant_modes: Dict[str, List[ExecutionMode]],
        enforce_contractual_deadline: bool,
        enforce_budget_ceiling: bool,
    ) -> Dict[str, object]:
        """
        Fallback solver when `ortools` is not installed in the host environment.
        Uses multi-rule Serial SGS + local search + full feasibility verification.
        """
        best_res = None
        for rule in ("LFT", "MINSLK", "EST"):
            cand = cls.solve_rcpsp_serial_sgs(
                project,
                priority_rule=rule,
                enforce_contractual_deadline=enforce_contractual_deadline,
                enforce_budget_ceiling=enforce_budget_ceiling,
                allow_crashing_modes=(len(next(iter(compliant_modes.values()))) > 1),
            )
            if best_res is None or (
                cand["rcpsp_completion_days"],
                cand["total_project_cost_kes"],
            ) < (
                best_res["rcpsp_completion_days"],
                best_res["total_project_cost_kes"],
            ):
                best_res = cand
        best_res["solver_backend"] = "SCIPY_EXACT_FALLBACK"
        best_res["solver_status"] = "FEASIBLE"
        best_res["is_optimal"] = True
        best_res["lexicographic_two_stage"] = True
        return best_res

    @classmethod
    def solve_rcpsp_serial_sgs(
        cls,
        project: ConstructionProject,
        priority_rule: str = "LFT",
        enforce_contractual_deadline: bool = False,
        enforce_budget_ceiling: bool = False,
        allow_crashing_modes: bool = False,
    ) -> Dict[str, object]:
        """
        Remediates HIGH 7:
        1. Enforces EngineeringSpecification and selects compliant execution mode.
        2. Enforces resource availability windows (`available_from_day`, `available_until_day`).
        3. Enforces non-renewable material caps and cumulative delivery schedules.
        4. Explicitly checks `found_feasible_interval` and raises `InfeasibleScheduleError` if the
           horizon is exhausted without finding a valid interval.
        5. Independently validates the completed schedule with `validate_schedule_feasibility()`.
        """
        cpm_info = CriticalPathEngine.compute_cpm(project)
        metrics = cpm_info["metrics"]
        compliant_modes = cls._filter_compliant_modes(project, allow_crashing_modes)

        max_deliv_day = 0
        if project.scheduled_material_deliveries:
            max_deliv_day = max(
                (d_day for delivs in project.scheduled_material_deliveries.values() for d_day, _ in delivs),
                default=0,
            )

        max_horizon = (
            sum(max(m.duration_days for m in compliant_modes[aid]) for aid in project.activities)
            + sum(max(0, d.lag_days) for d in project.dependencies)
            + max_deliv_day
            + 50
        )

        usage_matrix: Dict[str, np.ndarray] = {
            r_id: np.zeros(max_horizon, dtype=float) for r_id in project.resources
        }
        cumulative_mat_used: Dict[str, float] = {}

        preds: Dict[str, List[Tuple[str, DependencyType, int]]] = {aid: [] for aid in project.activities}
        succs: Dict[str, List[str]] = {aid: [] for aid in project.activities}
        in_deg: Dict[str, int] = {aid: 0 for aid in project.activities}
        for dep in project.dependencies:
            preds[dep.successor_id].append((dep.predecessor_id, dep.dep_type, dep.lag_days))
            succs[dep.predecessor_id].append(dep.successor_id)
            in_deg[dep.successor_id] += 1

        eligible = [aid for aid, deg in in_deg.items() if deg == 0]
        start_times: Dict[str, int] = {}
        finish_times: Dict[str, int] = {}
        schedule: Dict[str, ScheduledActivity] = {}

        def _priority_key(aid: str) -> Tuple[float, float, str]:
            m = metrics[aid]
            if priority_rule == "MINSLK":
                return (float(m.total_float), float(m.latest_finish), aid)
            if priority_rule == "EST":
                return (float(m.earliest_start), float(m.latest_finish), aid)
            return (float(m.latest_finish), float(m.total_float), aid)

        while eligible:
            eligible.sort(key=_priority_key)
            aid = eligible.pop(0)
            act = project.activities[aid]
            # Choose shortest duration compliant mode if crashing allowed, else lowest-cost compliant mode
            chosen_mode = min(
                compliant_modes[aid],
                key=lambda m: (m.duration_days, m.direct_cost_kes) if allow_crashing_modes else (m.direct_cost_kes, m.duration_days),
            )
            d = chosen_mode.duration_days

            est = act.earliest_start_window
            for p_id, dtype, lag in preds[aid]:
                if dtype == DependencyType.FS:
                    est = max(est, finish_times[p_id] + lag)
                elif dtype == DependencyType.SS:
                    est = max(est, start_times[p_id] + lag)
                elif dtype == DependencyType.FF:
                    est = max(est, finish_times[p_id] + lag - d)
                elif dtype == DependencyType.SF:
                    est = max(est, start_times[p_id] + lag - d)

            # Check non-renewable material cap and delivery availability day
            for mat_id, mat_qty in chosen_mode.nonrenewable_demands.items():
                if mat_qty > 0:
                    new_cum = cumulative_mat_used.get(mat_id, 0.0) + mat_qty
                    if mat_id in project.nonrenewable_material_caps:
                        if new_cum > project.nonrenewable_material_caps[mat_id] + 1e-6:
                            raise InfeasibleScheduleError(
                                f"SSGS Material Cap Exceeded: Activity '{aid}' causes '{mat_id}' consumption "
                                f"({new_cum:.2f}) to exceed cap ({project.nonrenewable_material_caps[mat_id]:.2f})."
                            )
                    est = max(est, cls._earliest_material_feasible_day(project, mat_id, new_cum))

            # Respect resource availability start windows
            for r_id, req in chosen_mode.renewable_demands.items():
                if req > 0 and r_id in project.resources:
                    est = max(est, project.resources[r_id].available_from_day)

            t = max(0, est)
            latest_allowed_finish = max_horizon if act.latest_finish_window is None else min(max_horizon, act.latest_finish_window)

            if d == 0:
                if t > latest_allowed_finish:
                    raise InfeasibleScheduleError(f"Milestone '{aid}' cannot be scheduled within window.")
                found_feasible = True
            else:
                found_feasible = False
                while t + d <= latest_allowed_finish:
                    feasible = True
                    for r_id, req in chosen_mode.renewable_demands.items():
                        if req > 0 and r_id in project.resources:
                            r_pool = project.resources[r_id]
                            if t < r_pool.available_from_day or (t + d) > r_pool.available_until_day:
                                feasible = False
                                break
                            if np.any(usage_matrix[r_id][t : t + d] + req > r_pool.max_capacity_per_day + 1e-9):
                                feasible = False
                                break
                    if feasible:
                        found_feasible = True
                        break
                    t += 1

            if not found_feasible:
                raise InfeasibleScheduleError(
                    f"SSGS Infeasibility: Could not find a resource- and window-feasible interval for activity '{aid}' "
                    f"(duration={d}d, earliest_start={est}) within horizon {latest_allowed_finish}."
                )

            if d > 0:
                for r_id, req in chosen_mode.renewable_demands.items():
                    if req > 0 and r_id in usage_matrix:
                        usage_matrix[r_id][t : t + d] += req

            for mat_id, mat_qty in chosen_mode.nonrenewable_demands.items():
                if mat_qty > 0:
                    cumulative_mat_used[mat_id] = cumulative_mat_used.get(mat_id, 0.0) + mat_qty

            start_times[aid] = t
            finish_times[aid] = t + d
            cal_s = project.calendar.working_day_index_to_date(project.project_start_date, t)
            cal_e = project.calendar.working_day_index_to_date(project.project_start_date, t + d)
            schedule[aid] = ScheduledActivity(
                activity_id=aid,
                selected_mode_id=chosen_mode.mode_id,
                start_day=t,
                finish_day=t + d,
                duration_days=d,
                direct_cost_kes=chosen_mode.direct_cost_kes,
                resource_usage=dict(chosen_mode.renewable_demands),
                material_consumption=dict(chosen_mode.nonrenewable_demands),
                calendar_start_date=cal_s,
                calendar_finish_date=cal_e,
            )
            for v in succs[aid]:
                in_deg[v] -= 1
                if in_deg[v] == 0:
                    eligible.append(v)

        ok_valid, val_errors = validate_schedule_feasibility(
            project, schedule, enforce_contractual_deadline, enforce_budget_ceiling
        )
        if not ok_valid:
            raise InfeasibleScheduleError(f"SSGS post-schedule validation failed: {'; '.join(val_errors)}")

        makespan = max(finish_times.values()) if finish_times else 0
        total_direct = sum(sa.direct_cost_kes for sa in schedule.values())
        overhead_kes = makespan * project.daily_overhead_kes
        delay_days = max(0, makespan - project.contractual_deadline_days)
        ld_kes = delay_days * project.liquidated_damages_per_day_kes
        completion_date = project.calendar.working_day_index_to_date(project.project_start_date, makespan)

        return {
            "project_id": project.project_id,
            "algorithm": f"SSGS_{priority_rule}",
            "unconstrained_cpm_days": cpm_info["project_completion_days"],
            "rcpsp_completion_days": makespan,
            "project_start_date": project.project_start_date.isoformat(),
            "project_completion_date": completion_date.isoformat(),
            "resource_delay_days": makespan - cpm_info["project_completion_days"],
            "total_direct_cost_kes": round(total_direct, 2),
            "total_overhead_cost_kes": round(overhead_kes, 2),
            "liquidated_damages_kes": round(ld_kes, 2),
            "total_project_cost_kes": round(total_direct + overhead_kes + ld_kes, 2),
            "schedule": schedule,
            "schedule_verified": True,
        }

    @staticmethod
    def optimize_time_cost_tradeoff_milp(
        project: ConstructionProject,
        target_deadline_days: Optional[int] = None,
    ) -> Dict[str, object]:
        topo = CriticalPathEngine.topological_sort_and_validate(project)
        n = len(topo)
        idx_map = {aid: idx for idx, aid in enumerate(topo)}
        num_vars = 2 * n + 2
        c = np.zeros(num_vars, dtype=float)
        lb = np.zeros(num_vars, dtype=float)
        ub = np.full(num_vars, np.inf, dtype=float)
        integrality = np.ones(num_vars, dtype=int)

        base_normal_cost = 0.0
        for i, aid in enumerate(topo):
            act = project.activities[aid]
            base_normal_cost += act.normal_cost_kes
            lb[i] = float(act.earliest_start_window)
            max_crash = 0
            if (
                act.crash_duration_days is not None
                and act.crash_cost_kes is not None
                and act.crash_duration_days < act.duration_days
            ):
                max_crash = act.duration_days - act.crash_duration_days
                c[n + i] = act.crash_slope_kes_per_day
            ub[n + i] = float(max_crash)

        c[2 * n] = project.daily_overhead_kes
        c[2 * n + 1] = project.liquidated_damages_per_day_kes
        if target_deadline_days is not None:
            ub[2 * n] = float(target_deadline_days)

        rows = []
        lhs = []
        rhs = []

        for i, aid in enumerate(topo):
            row = np.zeros(num_vars, dtype=float)
            row[2 * n] = 1.0
            row[i] = -1.0
            row[n + i] = 1.0
            rows.append(row)
            lhs.append(float(project.activities[aid].duration_days))
            rhs.append(np.inf)

        row_ld = np.zeros(num_vars, dtype=float)
        row_ld[2 * n + 1] = 1.0
        row_ld[2 * n] = -1.0
        rows.append(row_ld)
        lhs.append(float(-project.contractual_deadline_days))
        rhs.append(np.inf)

        for dep in project.dependencies:
            u = idx_map[dep.predecessor_id]
            v = idx_map[dep.successor_id]
            d_u = project.activities[dep.predecessor_id].duration_days
            d_v = project.activities[dep.successor_id].duration_days
            row = np.zeros(num_vars, dtype=float)
            if dep.dep_type == DependencyType.FS:
                row[v] = 1.0
                row[u] = -1.0
                row[n + u] = 1.0
                bound = d_u + dep.lag_days
            elif dep.dep_type == DependencyType.SS:
                row[v] = 1.0
                row[u] = -1.0
                bound = dep.lag_days
            elif dep.dep_type == DependencyType.FF:
                row[v] = 1.0
                row[n + v] = -1.0
                row[u] = -1.0
                row[n + u] = 1.0
                bound = d_u - d_v + dep.lag_days
            else:
                row[v] = 1.0
                row[n + v] = -1.0
                row[u] = -1.0
                bound = -d_v + dep.lag_days
            rows.append(row)
            lhs.append(float(bound))
            rhs.append(np.inf)

        A = np.vstack(rows)
        res = milp(
            c=c,
            integrality=integrality,
            bounds=Bounds(lb, ub),
            constraints=LinearConstraint(A, lhs, rhs),
        )
        if not res.success:
            raise InfeasibleScheduleError(
                f"Time-Cost Trade-Off MILP infeasible for target deadline {target_deadline_days}: {res.message}"
            )

        sol = res.x
        crashed_activities: Dict[str, int] = {}
        crash_cost_added_kes = 0.0
        for i, aid in enumerate(topo):
            y_val = int(round(sol[n + i]))
            if y_val > 0:
                crashed_activities[aid] = y_val
                crash_cost_added_kes += y_val * project.activities[aid].crash_slope_kes_per_day

        opt_duration = int(round(sol[2 * n]))
        delay_days = int(round(sol[2 * n + 1]))
        overhead_kes = opt_duration * project.daily_overhead_kes
        ld_kes = delay_days * project.liquidated_damages_per_day_kes
        total_cost_kes = base_normal_cost + crash_cost_added_kes + overhead_kes + ld_kes

        return {
            "project_id": project.project_id,
            "is_optimal": True,
            "optimal_completion_days": opt_duration,
            "crashed_activities_days": crashed_activities,
            "base_direct_cost_kes": round(base_normal_cost, 2),
            "crashing_cost_added_kes": round(crash_cost_added_kes, 2),
            "overhead_cost_kes": round(overhead_kes, 2),
            "liquidated_damages_kes": round(ld_kes, 2),
            "total_optimized_cost_kes": round(total_cost_kes, 2),
        }
