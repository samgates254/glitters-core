"""
Multi-Project Resource Allocation, Hungarian Assignment, and 1D Cutting-Stock Engine.
Includes automatic fallback when `ortools` is not installed.
"""
from __future__ import annotations
import math
from typing import Dict, List, Tuple
import numpy as np
from scipy.optimize import linear_sum_assignment

try:
    from ortools.sat.python import cp_model
    HAS_ORTOOLS = True
except ImportError:
    cp_model = None
    HAS_ORTOOLS = False

from ..project_model import ConstructionProject, DependencyType, ResourcePoolItem
from ..scheduling import InfeasibleScheduleError, CriticalPathEngine


class ResourceAllocationEngine:
    @staticmethod
    def solve_multi_project_allocation(
        projects: List[ConstructionProject],
        shared_resource_pool: Dict[str, ResourcePoolItem],
        max_time_seconds: float = 10.0,
    ) -> Dict[str, object]:
        if not projects:
            raise ValueError("At least one project is required for multi-project resource allocation.")

        if not HAS_ORTOOLS:
            # Priority-ordered multi-project SSGS fallback
            horizon = sum(sum(a.duration_days for a in p.activities.values()) for p in projects) + 100
            usage = {r_id: np.zeros(horizon, dtype=float) for r_id in shared_resource_pool}
            project_summaries = {}
            for p in sorted(projects, key=lambda x: -x.priority_weight):
                topo = CriticalPathEngine.topological_sort_and_validate(p)
                preds = {aid: [] for aid in p.activities}
                for dep in p.dependencies:
                    preds[dep.successor_id].append((dep.predecessor_id, dep.dep_type, dep.lag_days))
                s_map, e_map = {}, {}
                for aid in topo:
                    act = p.activities[aid]
                    for r_id, req in act.resource_demands.items():
                        if r_id in shared_resource_pool and req > shared_resource_pool[r_id].max_capacity_per_day + 1e-9:
                            raise InfeasibleScheduleError(f"Demand exceeds pool capacity for {r_id}")
                    est = act.earliest_start_window
                    for pid_u, dtype, lag in preds[aid]:
                        if dtype == DependencyType.FS:
                            est = max(est, e_map[pid_u] + lag)
                        elif dtype == DependencyType.SS:
                            est = max(est, s_map[pid_u] + lag)
                        elif dtype == DependencyType.FF:
                            est = max(est, e_map[pid_u] + lag - act.duration_days)
                        else:
                            est = max(est, s_map[pid_u] + lag - act.duration_days)
                    t = max(0, est)
                    d = act.duration_days
                    while t + d < horizon:
                        ok = True
                        for r_id, req in act.resource_demands.items():
                            if req > 0 and r_id in shared_resource_pool:
                                if np.any(usage[r_id][t : t + d] + req > shared_resource_pool[r_id].max_capacity_per_day + 1e-9):
                                    ok = False
                                    break
                        if ok:
                            break
                        t += 1
                    for r_id, req in act.resource_demands.items():
                        if req > 0 and r_id in usage:
                            usage[r_id][t : t + d] += req
                    s_map[aid], e_map[aid] = t, t + d
                p_end = max(e_map.values()) if e_map else 0
                project_summaries[p.project_id] = {
                    "completion_days": p_end,
                    "contractual_deadline_days": p.contractual_deadline_days,
                    "delay_days": max(0, p_end - p.contractual_deadline_days),
                    "activity_starts": s_map,
                    "activity_finishes": e_map,
                }
            return {"solver_status": "FEASIBLE", "projects": project_summaries}

        model = cp_model.CpModel()
        SCALE = 100
        horizon = sum(sum(a.duration_days for a in p.activities.values()) for p in projects) + 50

        proj_Intervals = {r_id: [] for r_id in shared_resource_pool}
        proj_Demands = {r_id: [] for r_id in shared_resource_pool}
        project_results_vars = {}

        for p in projects:
            s_vars = {}
            e_vars = {}
            for aid, act in p.activities.items():
                for r_id, req in act.resource_demands.items():
                    if r_id in shared_resource_pool and req > shared_resource_pool[r_id].max_capacity_per_day + 1e-9:
                        raise InfeasibleScheduleError(
                            f"Project '{p.project_id}' activity '{aid}' demands {req} of '{r_id}', "
                            f"exceeding shared pool capacity {shared_resource_pool[r_id].max_capacity_per_day}."
                        )
                s = model.NewIntVar(act.earliest_start_window, horizon, f"S_{p.project_id}_{aid}")
                e = model.NewIntVar(act.earliest_start_window, horizon, f"E_{p.project_id}_{aid}")
                iv = model.NewIntervalVar(s, act.duration_days, e, f"IV_{p.project_id}_{aid}")
                s_vars[aid] = s
                e_vars[aid] = e
                for r_id, req in act.resource_demands.items():
                    if req > 0 and r_id in shared_resource_pool:
                        proj_Intervals[r_id].append(iv)
                        proj_Demands[r_id].append(int(round(req * SCALE)))

            for dep in p.dependencies:
                u, v = dep.predecessor_id, dep.successor_id
                if dep.dep_type == DependencyType.FS:
                    model.Add(s_vars[v] >= e_vars[u] + dep.lag_days)
                elif dep.dep_type == DependencyType.SS:
                    model.Add(s_vars[v] >= s_vars[u] + dep.lag_days)
                elif dep.dep_type == DependencyType.FF:
                    model.Add(e_vars[v] >= e_vars[u] + dep.lag_days)
                else:
                    model.Add(e_vars[v] >= s_vars[u] + dep.lag_days)

            p_end = model.NewIntVar(0, horizon, f"end_{p.project_id}")
            model.AddMaxEquality(p_end, list(e_vars.values()))
            p_delay = model.NewIntVar(0, horizon, f"delay_{p.project_id}")
            model.Add(p_delay >= p_end - p.contractual_deadline_days)
            project_results_vars[p.project_id] = (p, s_vars, e_vars, p_end, p_delay)

        for r_id, r_item in shared_resource_pool.items():
            if proj_Intervals[r_id]:
                model.AddCumulative(
                    proj_Intervals[r_id],
                    proj_Demands[r_id],
                    int(round(r_item.max_capacity_per_day * SCALE)),
                )

        obj_terms = []
        for pid, (p, _, _, p_end, p_delay) in project_results_vars.items():
            w = int(round(p.priority_weight * 100))
            obj_terms.append(p_delay * w * 1000 + p_end * w)
        model.Minimize(sum(obj_terms))

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = max_time_seconds
        status = solver.Solve(model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            raise InfeasibleScheduleError("Multi-project shared resource allocation is infeasible.")

        project_summaries = {}
        for pid, (p, s_vars, e_vars, p_end, p_delay) in project_results_vars.items():
            project_summaries[pid] = {
                "completion_days": int(solver.Value(p_end)),
                "contractual_deadline_days": p.contractual_deadline_days,
                "delay_days": int(solver.Value(p_delay)),
                "activity_starts": {aid: int(solver.Value(s_vars[aid])) for aid in p.activities},
                "activity_finishes": {aid: int(solver.Value(e_vars[aid])) for aid in p.activities},
            }

        return {
            "solver_status": "OPTIMAL" if status == cp_model.OPTIMAL else "FEASIBLE",
            "projects": project_summaries,
        }

    @staticmethod
    def optimize_worker_task_assignment(
        cost_matrix_kes: np.ndarray,
        qualification_mask: np.ndarray,
        worker_names: List[str],
        task_names: List[str],
    ) -> Dict[str, object]:
        if cost_matrix_kes.shape != qualification_mask.shape:
            raise ValueError("Cost matrix and qualification mask dimensions must match.")
        n_workers, n_tasks = cost_matrix_kes.shape
        if n_workers < n_tasks:
            raise InfeasibleScheduleError(
                f"Labour shortage: {n_workers} available specialists cannot cover {n_tasks} simultaneous tasks."
            )

        BIG_M = 1e12
        penalized_cost = np.where(qualification_mask, cost_matrix_kes, BIG_M)
        row_ind, col_ind = linear_sum_assignment(penalized_cost)

        assignments = []
        total_cost_kes = 0.0
        for r, c in zip(row_ind, col_ind):
            if not qualification_mask[r, c]:
                raise InfeasibleScheduleError(
                    f"Infeasible assignment: No qualified specialist available for task '{task_names[c]}'."
                )
            cost_val = float(cost_matrix_kes[r, c])
            total_cost_kes += cost_val
            assignments.append({
                "worker": worker_names[r],
                "task": task_names[c],
                "cost_kes": round(cost_val, 2),
            })

        return {
            "is_optimal": True,
            "total_assignment_cost_kes": round(total_cost_kes, 2),
            "assignments": assignments,
        }

    @staticmethod
    def optimize_1d_cutting_stock(
        stock_length_m: float,
        required_pieces: List[Tuple[float, int]],
        kerf_loss_m: float = 0.003,
    ) -> Dict[str, object]:
        expanded_lengths: List[float] = []
        for length_m, count in required_pieces:
            if length_m <= 0 or length_m > stock_length_m:
                raise ValueError(
                    f"Piece length {length_m}m cannot be cut from stock length {stock_length_m}m."
                )
            expanded_lengths.extend([length_m] * int(count))

        expanded_lengths.sort(reverse=True)
        bins: List[List[float]] = []
        bin_remaining: List[float] = []

        for piece in expanded_lengths:
            best_bin_idx = -1
            best_rem = stock_length_m + 1.0
            for idx, rem in enumerate(bin_remaining):
                needed = piece + (kerf_loss_m if bins[idx] else 0.0)
                if rem >= needed - 1e-9 and (rem - needed) < best_rem:
                    best_rem = rem - needed
                    best_bin_idx = idx
            if best_bin_idx >= 0:
                needed = piece + (kerf_loss_m if bins[best_bin_idx] else 0.0)
                bins[best_bin_idx].append(piece)
                bin_remaining[best_bin_idx] -= needed
            else:
                bins.append([piece])
                bin_remaining.append(stock_length_m - piece)

        total_stock_used_m = len(bins) * stock_length_m
        total_net_pieces_m = sum(expanded_lengths)
        total_offcut_waste_m = total_stock_used_m - total_net_pieces_m
        waste_pct = (total_offcut_waste_m / total_stock_used_m) if total_stock_used_m > 0 else 0.0

        return {
            "stock_length_m": stock_length_m,
            "total_stock_bars_required": len(bins),
            "theoretical_min_bars": math.ceil(total_net_pieces_m / stock_length_m),
            "total_net_length_m": round(total_net_pieces_m, 3),
            "total_stock_length_m": round(total_stock_used_m, 3),
            "total_waste_length_m": round(total_offcut_waste_m, 3),
            "cutting_waste_fraction": round(waste_pct, 4),
            "cutting_patterns_sample": bins[:10],
        }
