"""
Construction Cost Minimization, Value Engineering & Cash-Flow NPV Optimization Engine.
Strictly enforces approved EngineeringSpecification constraints so that cheaper non-compliant
materials or structural classes are NEVER selected.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Optional
import numpy as np
from scipy.optimize import milp, LinearConstraint, Bounds
from ..project_model import ConstructionProject, EngineeringSpecification
from ..scheduling import InfeasibleScheduleError


@dataclass(frozen=True)
class ValueEngineeringAlternative:
    option_id: str
    work_package_id: str
    description: str
    material_cost_kes: float
    labour_cost_kes: float
    equipment_cost_kes: float
    transport_and_procurement_kes: float
    expected_waste_pct: float
    expected_rework_prob: float
    rework_penalty_kes: float
    concrete_mpa_provided: Optional[float] = None
    steel_mpa_provided: Optional[float] = None

    @property
    def total_risk_adjusted_direct_cost_kes(self) -> float:
        mat_with_waste = self.material_cost_kes * (1.0 + self.expected_waste_pct)
        rework_expected = self.expected_rework_prob * self.rework_penalty_kes
        return (
            mat_with_waste
            + self.labour_cost_kes
            + self.equipment_cost_kes
            + self.transport_and_procurement_kes
            + rework_expected
        )


class CostOptimizationEngine:
    """
    Minimizes total lifecycle construction cost (materials, labour, equipment, transport,
    procurement, site overheads, financing/cash-flow cost, waste, rework, and delay penalties)
    while strictly enforcing licensed engineering specifications and contractual budgets.
    """

    @staticmethod
    def optimize_value_engineering_milp(
        work_package_specs: Dict[str, EngineeringSpecification],
        alternatives_by_package: Dict[str, List[ValueEngineeringAlternative]],
        budget_ceiling_kes: float,
        contingency_reserve_pct: float = 0.05,
    ) -> Dict[str, object]:
        """
        Selects exactly one engineering-compliant alternative per work package to minimize
        total risk-adjusted direct + waste + rework cost subject to the budget ceiling.
        Any alternative violating concrete strength, steel yield, or licensed engineer approval
        is strictly pruned before optimization (Rule: Never violate engineering specifications to reduce costs).
        """
        filtered_packages: Dict[str, List[ValueEngineeringAlternative]] = {}
        rejected_audit: List[Dict[str, object]] = []

        for pkg_id, spec in work_package_specs.items():
            options = alternatives_by_package.get(pkg_id, [])
            valid_opts: List[ValueEngineeringAlternative] = []
            for opt in options:
                ok, reasons = spec.validate_compliance(
                    concrete_mpa=opt.concrete_mpa_provided,
                    steel_mpa=opt.steel_mpa_provided,
                )
                if ok:
                    valid_opts.append(opt)
                else:
                    rejected_audit.append({
                        "work_package_id": pkg_id,
                        "rejected_option_id": opt.option_id,
                        "reasons": reasons,
                    })
            if not valid_opts:
                raise InfeasibleScheduleError(
                    f"Work package '{pkg_id}' has zero alternatives complying with EngineeringSpecification '{spec.spec_id}'."
                )
            filtered_packages[pkg_id] = valid_opts

        # Formulate 0-1 integer programming problem across compliant options
        pkg_ids = sorted(filtered_packages.keys())
        flat_opts: List[Tuple[str, ValueEngineeringAlternative]] = []
        pkg_to_indices: Dict[str, List[int]] = {pid: [] for pid in pkg_ids}

        for pid in pkg_ids:
            for opt in filtered_packages[pid]:
                idx = len(flat_opts)
                flat_opts.append((pid, opt))
                pkg_to_indices[pid].append(idx)

        n_vars = len(flat_opts)
        c = np.array([opt.total_risk_adjusted_direct_cost_kes for _, opt in flat_opts], dtype=float)
        integrality = np.ones(n_vars, dtype=int)
        lb = np.zeros(n_vars, dtype=float)
        ub = np.ones(n_vars, dtype=float)

        rows = []
        lhs = []
        rhs = []
        for pid in pkg_ids:
            row = np.zeros(n_vars, dtype=float)
            for idx in pkg_to_indices[pid]:
                row[idx] = 1.0
            rows.append(row)
            lhs.append(1.0)
            rhs.append(1.0)

        A = np.vstack(rows)
        res = milp(c=c, integrality=integrality, bounds=Bounds(lb, ub), constraints=LinearConstraint(A, lhs, rhs))
        if not res.success:
            raise InfeasibleScheduleError(f"Value engineering MILP failed: {res.message}")

        selected_options = []
        base_subtotal_kes = 0.0
        for idx, (pid, opt) in enumerate(flat_opts):
            if res.x[idx] > 0.5:
                cost_val = opt.total_risk_adjusted_direct_cost_kes
                base_subtotal_kes += cost_val
                selected_options.append({
                    "work_package_id": pid,
                    "selected_option_id": opt.option_id,
                    "description": opt.description,
                    "risk_adjusted_cost_kes": round(cost_val, 2),
                })

        contingency_kes = base_subtotal_kes * contingency_reserve_pct
        total_with_contingency_kes = base_subtotal_kes + contingency_kes
        if total_with_contingency_kes > budget_ceiling_kes + 1e-6:
            raise InfeasibleScheduleError(
                f"Budget ceiling exceeded: Minimum engineering-compliant cost ({total_with_contingency_kes:,.2f} KES "
                f"incl. {contingency_reserve_pct*100:.1f}% contingency) exceeds ceiling ({budget_ceiling_kes:,.2f} KES)."
            )

        return {
            "is_optimal": True,
            "selected_options": selected_options,
            "rejected_non_compliant_options": rejected_audit,
            "risk_adjusted_direct_subtotal_kes": round(base_subtotal_kes, 2),
            "contingency_reserve_kes": round(contingency_kes, 2),
            "total_compliant_cost_kes": round(total_with_contingency_kes, 2),
            "budget_ceiling_kes": round(budget_ceiling_kes, 2),
            "budget_headroom_kes": round(budget_ceiling_kes - total_with_contingency_kes, 2),
        }

    @staticmethod
    def optimize_cash_flow_schedule(
        project: ConstructionProject,
        cpm_metrics: Dict[str, object],
    ) -> Dict[str, object]:
        """
        Optimizes activity start times within their available float [ES_i, LS_i]
        to minimize the Net Present Value (NPV) of cash outflows and working-capital financing cost
        under Kenyan commercial borrowing rates.
        Since discount factor exp(-r * t) is strictly decreasing in t, delaying non-critical
        high-expenditure activities toward LS_i while preserving precedence minimizes financing cost.
        """
        daily_rate = project.financing_annual_interest_rate / 365.0
        metrics = cpm_metrics["metrics"]

        early_cash_flow_npv = 0.0
        late_cash_flow_npv = 0.0
        financing_cost_early = 0.0
        financing_cost_late = 0.0
        horizon = cpm_metrics["project_completion_days"]

        for aid, act in project.activities.items():
            m = metrics[aid]
            cost = act.normal_cost_kes
            mid_early = m.earliest_start + 0.5 * act.duration_days
            mid_late = m.latest_start + 0.5 * act.duration_days

            early_cash_flow_npv += cost * float(np.exp(-daily_rate * mid_early))
            late_cash_flow_npv += cost * float(np.exp(-daily_rate * mid_late))

            financing_cost_early += cost * ((1.0 + daily_rate) ** max(0.0, horizon - mid_early) - 1.0)
            financing_cost_late += cost * ((1.0 + daily_rate) ** max(0.0, horizon - mid_late) - 1.0)

        return {
            "project_id": project.project_id,
            "early_start_npv_kes": round(early_cash_flow_npv, 2),
            "late_start_optimized_npv_kes": round(late_cash_flow_npv, 2),
            "early_schedule_financing_cost_kes": round(financing_cost_early, 2),
            "optimized_schedule_financing_cost_kes": round(financing_cost_late, 2),
            "working_capital_interest_savings_kes": round(financing_cost_early - financing_cost_late, 2),
        }
