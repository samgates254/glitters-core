"""
Workforce & Labour Gang Optimization Engine.
Optimizes crew composition across Civil Engineers, Structural Engineers, Architects,
Quantity Surveyors, Site Supervisors, Skilled Artisans (masons, steel fixers, carpenters,
electricians, plumbers), and General Labourers.
Models Kenyan statutory regular/overtime rates and site congestion productivity attenuation.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List
import math
import numpy as np
from scipy.optimize import milp, LinearConstraint, Bounds
from ..scheduling import InfeasibleScheduleError


@dataclass(frozen=True)
class LabourRoleSpec:
    role_id: str
    title: str
    daily_wage_kes: float
    overtime_hourly_rate_kes: float
    max_available_headcount: int
    base_productivity_units_per_day: float
    min_supervision_ratio: float = 0.0  # e.g., 1 site supervisor per 20 workers


class LabourOptimizationEngine:
    """
    Optimizes daily crew sizing, overtime hours, and supervision ratios while modeling
    site crowding productivity losses:
      eta(N, A_site) = exp(-beta * max(0, (N / A_site) - rho_0))
    where rho_0 = 0.04 workers/m2 (1 worker per 25 m2 optimal workspace).
    """

    @staticmethod
    def compute_crowding_efficiency(
        total_workers_on_site: int,
        active_work_area_m2: float,
        optimal_density_workers_per_m2: float = 0.04,
        crowding_decay_beta: float = 4.5,
    ) -> float:
        if active_work_area_m2 <= 0:
            raise ValueError("Active work area must be positive.")
        density = total_workers_on_site / active_work_area_m2
        excess = max(0.0, density - optimal_density_workers_per_m2)
        return float(math.exp(-crowding_decay_beta * excess))

    @staticmethod
    def optimize_crew_allocation(
        required_work_units: float,
        target_duration_days: int,
        skilled_role: LabourRoleSpec,
        general_role: LabourRoleSpec,
        supervisor_role: LabourRoleSpec,
        skilled_to_general_ratio: Tuple[int, int] = (1, 2),
        max_overtime_hours_per_day: float = 3.0,
    ) -> Dict[str, object]:
        """
        Finds the minimum-cost integer crew size (skilled artisans, general labourers, site supervisors)
        and daily overtime hours to complete `required_work_units` within `target_duration_days`.
        """
        if target_duration_days <= 0 or required_work_units <= 0:
            raise ValueError("Target duration and required work units must be strictly positive.")

        # Decision variables:
        # x0: number of skilled workers (integer)
        # x1: number of general labourers (integer)
        # x2: number of site supervisors (integer)
        # x3: total skilled overtime person-hours across duration (continuous/integer)
        c = np.array([
            skilled_role.daily_wage_kes * target_duration_days,
            general_role.daily_wage_kes * target_duration_days,
            supervisor_role.daily_wage_kes * target_duration_days,
            skilled_role.overtime_hourly_rate_kes,
        ], dtype=float)

        integrality = np.array([1, 1, 1, 0], dtype=int)
        lb = np.zeros(4, dtype=float)
        ub = np.array([
            float(skilled_role.max_available_headcount),
            float(general_role.max_available_headcount),
            float(supervisor_role.max_available_headcount),
            float(skilled_role.max_available_headcount * max_overtime_hours_per_day * target_duration_days),
        ], dtype=float)

        # Productivity constraint:
        # target_duration_days * (p_skilled * x0 + p_general * x1) + (p_skilled / 8.0 * 0.85) * x3 >= required_work_units
        # Note: Overtime hours have 85% fatigue efficiency factor
        p_s = skilled_role.base_productivity_units_per_day
        p_g = general_role.base_productivity_units_per_day
        p_ot = (p_s / 8.0) * 0.85

        # Gang ratio constraint: x1 >= (general_ratio / skilled_ratio) * x0  => -r*x0 + x1 >= 0
        ratio = skilled_to_general_ratio[1] / float(max(1, skilled_to_general_ratio[0]))

        # Supervision constraint: x2 >= 0.05 * (x0 + x1) => -0.05*x0 - 0.05*x1 + x2 >= 0
        # Overtime bound linked to chosen x0: x3 <= max_ot * D * x0 => max_ot*D*x0 - x3 >= 0
        A = np.array([
            [target_duration_days * p_s, target_duration_days * p_g, 0.0, p_ot],
            [-ratio, 1.0, 0.0, 0.0],
            [-0.05, -0.05, 1.0, 0.0],
            [max_overtime_hours_per_day * target_duration_days, 0.0, 0.0, -1.0],
        ], dtype=float)
        lhs = np.array([required_work_units, 0.0, 0.0, 0.0], dtype=float)
        rhs = np.array([np.inf, np.inf, np.inf, np.inf], dtype=float)

        res = milp(c=c, integrality=integrality, bounds=Bounds(lb, ub), constraints=LinearConstraint(A, lhs, rhs))
        if not res.success:
            raise InfeasibleScheduleError(
                f"Labour shortage: Cannot achieve {required_work_units} units in {target_duration_days} days "
                f"with max {skilled_role.max_available_headcount} skilled and {general_role.max_available_headcount} general workers."
            )

        x_skilled = int(round(res.x[0]))
        x_general = int(round(res.x[1]))
        x_sup = max(1, int(round(res.x[2])))
        ot_hours = float(res.x[3])
        total_cost = float(res.fun)

        return {
            "is_optimal": True,
            "skilled_workers": x_skilled,
            "general_labourers": x_general,
            "site_supervisors": x_sup,
            "total_overtime_person_hours": round(ot_hours, 2),
            "total_labour_cost_kes": round(total_cost, 2),
        }
