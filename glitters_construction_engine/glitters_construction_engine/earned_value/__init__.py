"""
Construction Progress Intelligence, Earned Value Management (EVM),
and Statistical Anomaly Detection Engine.
Strictly enforces the Non-Negotiable Rule:
Never present simulated progress as actual progress.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List
import math
import numpy as np


class ProgressDataOrigin(str, Enum):
    ACTUAL_SITE_VERIFIED = "ACTUAL_SITE_VERIFIED"
    SIMULATED_PROJECTION = "SIMULATED_PROJECTION"


@dataclass(frozen=True)
class ActivityProgressRecord:
    activity_id: str
    planned_quantity: float
    actual_quantity: float
    budget_at_completion_kes: float
    planned_pct_complete_at_status_date: float  # in [0, 1]
    actual_pct_complete_at_status_date: float   # in [0, 1]
    actual_cost_incurred_kes: float
    planned_material_units: float
    actual_material_units: float
    planned_labour_hours: float
    actual_labour_hours: float
    rework_incidents_count: int = 0
    unit_procurement_price_paid_kes: float = 0.0
    benchmark_unit_procurement_price_kes: float = 0.0
    data_origin: ProgressDataOrigin = ProgressDataOrigin.ACTUAL_SITE_VERIFIED


class EarnedValueAndAnomalyEngine:
    """
    Computes full PMI/ISO 21508 Earned Value Management metrics (PV, EV, AC, CV, SV, CPI, SPI,
    EAC, ETC, VAC, TCPI) and runs multi-dimensional anomaly detection across all 6 construction
    risk vectors:
      1. Unusual material consumption (pilferage/over-wastage)
      2. Unexpected cost increases
      3. Labour productivity deterioration
      4. Schedule slippage
      5. Procurement irregularities
      6. Repeated rework
    """

    @staticmethod
    def evaluate_project_progress(
        records: List[ActivityProgressRecord],
        allow_simulated_data: bool = False,
    ) -> Dict[str, object]:
        if not records:
            raise ValueError("Progress records list cannot be empty.")

        for r in records:
            if r.data_origin == ProgressDataOrigin.SIMULATED_PROJECTION and not allow_simulated_data:
                raise ValueError(
                    f"Non-Negotiable Rule Violation: Activity '{r.activity_id}' has data_origin=SIMULATED_PROJECTION "
                    f"and cannot be reported as actual site progress."
                )

        bac = sum(r.budget_at_completion_kes for r in records)
        pv = sum(r.budget_at_completion_kes * np.clip(r.planned_pct_complete_at_status_date, 0.0, 1.0) for r in records)
        ev = sum(r.budget_at_completion_kes * np.clip(r.actual_pct_complete_at_status_date, 0.0, 1.0) for r in records)
        ac = sum(r.actual_cost_incurred_kes for r in records)

        cv = ev - ac
        sv = ev - pv
        cpi = (ev / ac) if ac > 1e-9 else 1.0
        spi = (ev / pv) if pv > 1e-9 else 1.0
        csi = cpi * spi  # Critical Ratio / Cost-Schedule Index

        # Three standard EAC formulations
        eac_cpi = (bac / cpi) if cpi > 1e-9 else float("inf")
        eac_remaining_budgetRate = ac + (bac - ev)
        eac_composite = ac + ((bac - ev) / max(1e-9, csi))
        etc = max(0.0, eac_cpi - ac)
        vac = bac - eac_cpi
        tcpi_bac = ((bac - ev) / (bac - ac)) if (bac - ac) > 1e-9 else float("inf")

        # Anomaly Detection per activity and cross-sectionally (Robust MAD Z-score + threshold rules)
        anomalies: List[Dict[str, object]] = []
        mat_overrun_ratios = []
        for r in records:
            prog_ratio = max(0.01, r.actual_pct_complete_at_status_date)
            expected_mat_to_date = r.planned_material_units * prog_ratio
            mat_ratio = (r.actual_material_units / expected_mat_to_date) if expected_mat_to_date > 1e-6 else 1.0
            mat_overrun_ratios.append(mat_ratio)

        med_mat = float(np.median(mat_overrun_ratios))
        mad_mat = float(np.median(np.abs(np.array(mat_overrun_ratios) - med_mat))) + 1e-6

        for idx, r in enumerate(records):
            act_ev = r.budget_at_completion_kes * r.actual_pct_complete_at_status_date
            act_pv = r.budget_at_completion_kes * r.planned_pct_complete_at_status_date
            act_cpi = (act_ev / r.actual_cost_incurred_kes) if r.actual_cost_incurred_kes > 1e-6 else 1.0
            act_spi = (act_ev / act_pv) if act_pv > 1e-6 else 1.0

            # 1. Unusual material consumption
            mat_ratio = mat_overrun_ratios[idx]
            mod_z = 0.6745 * (mat_ratio - med_mat) / mad_mat
            if mat_ratio > 1.15 or mod_z > 3.0:
                anomalies.append({
                    "activity_id": r.activity_id,
                    "anomaly_type": "UNUSUAL_MATERIAL_CONSUMPTION",
                    "severity": "HIGH" if mat_ratio > 1.25 else "MEDIUM",
                    "metric_value": round(mat_ratio, 3),
                    "detail": f"Material consumption is {(mat_ratio - 1.0)*100:.1f}% above earned progress expectation (robust Z={mod_z:.2f}).",
                })

            # 2. Unexpected cost increase
            if act_cpi < 0.85 and r.actual_cost_incurred_kes > 0:
                anomalies.append({
                    "activity_id": r.activity_id,
                    "anomaly_type": "UNEXPECTED_COST_INCREASE",
                    "severity": "HIGH" if act_cpi < 0.75 else "MEDIUM",
                    "metric_value": round(act_cpi, 3),
                    "detail": f"Activity CPI degraded to {act_cpi:.2f} (AC={r.actual_cost_incurred_kes:,.0f} vs EV={act_ev:,.0f} KES).",
                })

            # 3. Labour productivity deterioration
            expected_lab_hours = r.planned_labour_hours * max(0.01, r.actual_pct_complete_at_status_date)
            if expected_lab_hours > 0 and (r.actual_labour_hours / expected_lab_hours) > 1.20:
                prod_index = expected_lab_hours / r.actual_labour_hours
                anomalies.append({
                    "activity_id": r.activity_id,
                    "anomaly_type": "LABOUR_PRODUCTIVITY_DETERIORATION",
                    "severity": "HIGH" if prod_index < 0.70 else "MEDIUM",
                    "metric_value": round(prod_index, 3),
                    "detail": f"Labour efficiency dropped to {prod_index*100:.1f}% of baseline productivity.",
                })

            # 4. Schedule slippage
            if act_spi < 0.85 and act_pv > 0:
                anomalies.append({
                    "activity_id": r.activity_id,
                    "anomaly_type": "SCHEDULE_SLIPPAGE",
                    "severity": "HIGH" if act_spi < 0.75 else "MEDIUM",
                    "metric_value": round(act_spi, 3),
                    "detail": f"Activity SPI slipped to {act_spi:.2f} ({r.actual_pct_complete_at_status_date*100:.1f}% vs planned {r.planned_pct_complete_at_status_date*100:.1f}%).",
                })

            # 5. Procurement irregularities
            if (
                r.benchmark_unit_procurement_price_kes > 0
                and r.unit_procurement_price_paid_kes > r.benchmark_unit_procurement_price_kes * 1.12
            ):
                premium_pct = (r.unit_procurement_price_paid_kes / r.benchmark_unit_procurement_price_kes - 1.0) * 100.0
                anomalies.append({
                    "activity_id": r.activity_id,
                    "anomaly_type": "PROCUREMENT_IRREGULARITY",
                    "severity": "HIGH" if premium_pct > 20.0 else "MEDIUM",
                    "metric_value": round(premium_pct, 2),
                    "detail": f"Unit procurement price paid ({r.unit_procurement_price_paid_kes:,.2f} KES) exceeds benchmark by {premium_pct:.1f}%.",
                })

            # 6. Repeated rework
            if r.rework_incidents_count >= 2:
                anomalies.append({
                    "activity_id": r.activity_id,
                    "anomaly_type": "REPEATED_REWORK",
                    "severity": "HIGH" if r.rework_incidents_count >= 3 else "MEDIUM",
                    "metric_value": r.rework_incidents_count,
                    "detail": f"Activity recorded {r.rework_incidents_count} rework cycles; quality audit required.",
                })

        return {
            "data_origin_verified": all(r.data_origin == ProgressDataOrigin.ACTUAL_SITE_VERIFIED for r in records),
            "budget_at_completion_kes": round(bac, 2),
            "planned_value_pv_kes": round(pv, 2),
            "earned_value_ev_kes": round(ev, 2),
            "actual_cost_ac_kes": round(ac, 2),
            "cost_variance_cv_kes": round(cv, 2),
            "schedule_variance_sv_kes": round(sv, 2),
            "cost_performance_index_cpi": round(cpi, 4),
            "schedule_performance_index_spi": round(spi, 4),
            "cost_schedule_index_csi": round(csi, 4),
            "estimate_at_completion_cpi_kes": round(eac_cpi, 2),
            "estimate_at_completion_composite_kes": round(eac_composite, 2),
            "estimate_at_completion_remaining_plan_kes": round(eac_remaining_budgetRate, 2),
            "estimate_to_complete_etc_kes": round(etc, 2),
            "variance_at_completion_vac_kes": round(vac, 2),
            "to_complete_performance_index_tcpi": round(tcpi_bac, 4) if math.isfinite(tcpi_bac) else None,
            "anomalies_detected_count": len(anomalies),
            "anomalies": anomalies,
        }
