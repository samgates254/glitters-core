"""
Independent Schedule & Material Feasibility Validator.
Remediates CRITICAL 5 & HIGH 7:
Independently validates any generated schedule against:
1. Generalized precedence constraints (FS, SS, FF, SF + lags)
2. Activity earliest-start and latest-finish windows
3. EngineeringSpecification compliance (rejecting None strengths or unverified approvals)
4. Daily renewable resource capacity limits and resource availability windows
5. Non-renewable material total supply caps (`nonrenewable_material_caps`)
6. Time-phased cumulative material delivery schedules (`scheduled_material_deliveries`)
"""
from __future__ import annotations
from dataclasses import dataclass, field
import datetime
from typing import Dict, List, Optional, Tuple
import numpy as np
from ..project_model import ConstructionProject, DependencyType


class InfeasibleScheduleError(RuntimeError):
    """Explicitly raised when resource, material, precedence, budget, or deadline constraints are mutually infeasible."""
    pass


@dataclass(frozen=True)
class ScheduledActivity:
    activity_id: str
    selected_mode_id: str
    start_day: int
    finish_day: int
    duration_days: int
    direct_cost_kes: float
    resource_usage: Dict[str, float]
    material_consumption: Dict[str, float] = field(default_factory=dict)
    calendar_start_date: Optional[datetime.date] = None
    calendar_finish_date: Optional[datetime.date] = None


def validate_schedule_feasibility(
    project: ConstructionProject,
    schedule: Dict[str, ScheduledActivity],
    enforce_contractual_deadline: bool = False,
    enforce_budget_ceiling: bool = False,
) -> Tuple[bool, List[str]]:
    """
    Independent mathematical verification of a candidate schedule.
    Raises or returns all constraint violations if any activity, resource, material, or precedence rule is broken.
    """
    violations: List[str] = []

    if set(schedule.keys()) != set(project.activities.keys()):
        violations.append("Schedule does not cover the exact set of project activities.")
        return False, violations

    horizon = max((sa.finish_day for sa in schedule.values()), default=0) + 1

    # 1. Activity windows & mode/engineering compliance
    for aid, sa in schedule.items():
        act = project.activities[aid]
        if sa.finish_day - sa.start_day != sa.duration_days:
            violations.append(f"Activity '{aid}': finish_day - start_day != duration_days.")
        if sa.start_day < act.earliest_start_window:
            violations.append(
                f"Activity '{aid}': start_day ({sa.start_day}) < earliest_start_window ({act.earliest_start_window})."
            )
        if act.latest_finish_window is not None and sa.finish_day > act.latest_finish_window:
            violations.append(
                f"Activity '{aid}': finish_day ({sa.finish_day}) > latest_finish_window ({act.latest_finish_window})."
            )
        # Verify mode & engineering spec
        matched_mode = next((m for m in act.modes if m.mode_id == sa.selected_mode_id), None)
        if matched_mode is not None and act.engineering_spec is not None:
            ok_spec, spec_errs = act.engineering_spec.validate_compliance(
                concrete_mpa=matched_mode.concrete_mpa_offered,
                steel_mpa=matched_mode.steel_mpa_offered,
            )
            if not ok_spec:
                violations.extend(spec_errs)

    # 2. Precedence constraints
    for dep in project.dependencies:
        u = schedule[dep.predecessor_id]
        v = schedule[dep.successor_id]
        lag = dep.lag_days
        if dep.dep_type == DependencyType.FS and v.start_day < u.finish_day + lag:
            violations.append(f"FS violation on ({dep.predecessor_id} -> {dep.successor_id}).")
        elif dep.dep_type == DependencyType.SS and v.start_day < u.start_day + lag:
            violations.append(f"SS violation on ({dep.predecessor_id} -> {dep.successor_id}).")
        elif dep.dep_type == DependencyType.FF and v.finish_day < u.finish_day + lag:
            violations.append(f"FF violation on ({dep.predecessor_id} -> {dep.successor_id}).")
        elif dep.dep_type == DependencyType.SF and v.finish_day < u.start_day + lag:
            violations.append(f"SF violation on ({dep.predecessor_id} -> {dep.successor_id}).")

    # 3. Renewable daily resource capacities & availability windows
    for r_id, r_pool in project.resources.items():
        daily_profile = np.zeros(max(1, horizon + 1), dtype=float)
        for aid, sa in schedule.items():
            req = sa.resource_usage.get(r_id, 0.0)
            if req > 0 and sa.duration_days > 0:
                if sa.start_day < r_pool.available_from_day or sa.finish_day > r_pool.available_until_day:
                    violations.append(
                        f"Activity '{aid}' uses resource '{r_id}' during [{sa.start_day}, {sa.finish_day}], "
                        f"outside availability window [{r_pool.available_from_day}, {r_pool.available_until_day}]."
                    )
                daily_profile[sa.start_day : sa.finish_day] += req
        max_used = float(np.max(daily_profile)) if len(daily_profile) > 0 else 0.0
        if max_used > r_pool.max_capacity_per_day + 1e-6:
            peak_day = int(np.argmax(daily_profile))
            violations.append(
                f"Renewable resource '{r_id}' overloaded on day {peak_day}: "
                f"used {max_used:.2f} > capacity {r_pool.max_capacity_per_day:.2f}."
            )

    # 4. Non-renewable material total caps & cumulative delivery schedules (CRITICAL 5)
    total_mat_consumed: Dict[str, float] = {}
    for aid, sa in schedule.items():
        for mat_id, qty in sa.material_consumption.items():
            if qty > 0:
                total_mat_consumed[mat_id] = total_mat_consumed.get(mat_id, 0.0) + qty

    for mat_id, cap in project.nonrenewable_material_caps.items():
        used = total_mat_consumed.get(mat_id, 0.0)
        if used > cap + 1e-6:
            violations.append(
                f"Non-renewable material '{mat_id}' total consumption ({used:.2f}) exceeds supply cap ({cap:.2f})."
            )

    for mat_id, deliveries in project.scheduled_material_deliveries.items():
        # Check at every activity start day t that cumulative deliveries <= t cover cumulative consumption <= t
        relevant_starts = sorted({sa.start_day for sa in schedule.values() if sa.material_consumption.get(mat_id, 0.0) > 0})
        for t in relevant_starts:
            cum_delivered = sum(q for d_day, q in deliveries if d_day <= t)
            cum_consumed = sum(
                sa.material_consumption.get(mat_id, 0.0)
                for sa in schedule.values()
                if sa.start_day <= t
            )
            if cum_consumed > cum_delivered + 1e-6:
                violations.append(
                    f"Material stockout for '{mat_id}' at day {t}: cumulative demand ({cum_consumed:.2f}) "
                    f"exceeds cumulative deliveries ({cum_delivered:.2f})."
                )

    makespan = max((sa.finish_day for sa in schedule.values()), default=0)
    if enforce_contractual_deadline and makespan > project.contractual_deadline_days:
        violations.append(
            f"Schedule makespan ({makespan}d) exceeds contractual deadline ({project.contractual_deadline_days}d)."
        )

    total_direct = sum(sa.direct_cost_kes for sa in schedule.values())
    total_cost = total_direct + makespan * project.daily_overhead_kes
    if enforce_budget_ceiling and total_cost > project.budget_ceiling_kes + 1e-6:
        violations.append(
            f"Total cost ({total_cost:,.2f} KES) exceeds budget ceiling ({project.budget_ceiling_kes:,.2f} KES)."
        )

    return (len(violations) == 0, violations)
