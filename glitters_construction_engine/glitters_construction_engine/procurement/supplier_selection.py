"""
Multi-Supplier Procurement MILP Optimizer with Production Evidence Gate.
Remediates CRITICAL 3:
- In production mode (`allow_synthetic_or_unverified_quotes=False`, the default),
  strictly blocks any `SupplierQuotation` that is synthetic, unverified, expired, or missing a SHA-256 hash.
"""
from __future__ import annotations
import datetime
from typing import Dict, List
import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from ..project_model.money import MoneyKES
from .quotations import QuotationVerificationStatus, SupplierQuotation
from .requisitions import ProcurementRequisition, deduplicate_requisitions


class ProcurementEngine:
    """
    Production-hardened Multi-Supplier Procurement Optimizer.
    """

    @staticmethod
    def deduplicate_requisitions(requisitions: List[ProcurementRequisition]):
        return deduplicate_requisitions(requisitions)

    @staticmethod
    def optimize_supplier_selection_milp(
        material_id: str,
        total_demand_quantity: float,
        required_within_days: int,
        quotations: List[SupplierQuotation],
        min_acceptable_reliability: float = 0.80,
        expected_delay_cost_per_day_kes: float = 50000.0,
        allow_synthetic_or_unverified_quotes: bool = False,
        reference_date: datetime.date = datetime.date(2026, 11, 12),
    ) -> Dict[str, object]:
        from ..scheduling import InfeasibleScheduleError

        if total_demand_quantity <= 0:
            raise ValueError("Total demand quantity must be strictly positive.")

        rejected_quotes: List[Dict[str, object]] = []
        eligible: List[SupplierQuotation] = []

        for q in quotations:
            if q.material_id != material_id:
                continue
            if not allow_synthetic_or_unverified_quotes:
                ok_prod, reasons = q.is_valid_for_production_procurement(reference_date=reference_date)
                if not ok_prod:
                    rejected_quotes.append({
                        "supplier_id": q.supplier_id,
                        "quotation_ref": q.quotation_ref,
                        "verification_status": q.verification_status.value,
                        "reasons": reasons,
                    })
                    continue
            if q.lead_time_days > required_within_days:
                rejected_quotes.append({
                    "supplier_id": q.supplier_id,
                    "quotation_ref": q.quotation_ref,
                    "verification_status": q.verification_status.value,
                    "reasons": [f"Lead time {q.lead_time_days}d exceeds required window {required_within_days}d."],
                })
                continue
            if q.reliability_score < min_acceptable_reliability or q.max_supply_capacity <= 0:
                rejected_quotes.append({
                    "supplier_id": q.supplier_id,
                    "quotation_ref": q.quotation_ref,
                    "verification_status": q.verification_status.value,
                    "reasons": [f"Reliability {q.reliability_score} < {min_acceptable_reliability} or non-positive capacity."],
                })
                continue
            eligible.append(q)

        if not eligible:
            raise InfeasibleScheduleError(
                f"Procurement blocked for '{material_id}': Zero eligible verified quotations satisfy "
                f"production verification, expiry, lead time (<= {required_within_days}d), and reliability "
                f"(>= {min_acceptable_reliability:.2f}). Rejected quotes: {len(rejected_quotes)}."
            )

        total_cap = sum(q.max_supply_capacity for q in eligible)
        if total_cap < total_demand_quantity - 1e-6:
            raise InfeasibleScheduleError(
                f"Material shortage for '{material_id}': Total verified supplier capacity ({total_cap:.1f}) "
                f"is less than project demand ({total_demand_quantity:.1f})."
            )

        m = len(eligible)
        num_vars = 2 * m
        c = np.zeros(num_vars, dtype=float)
        integrality = np.zeros(num_vars, dtype=int)
        lb = np.zeros(num_vars, dtype=float)
        ub = np.zeros(num_vars, dtype=float)

        for i, q in enumerate(eligible):
            risk_surcharge_per_unit = (
                (1.0 - q.reliability_score)
                * expected_delay_cost_per_day_kes
                * (q.lead_time_days / max(1.0, total_demand_quantity))
            )
            c[i] = q.unit_price_kes + risk_surcharge_per_unit
            c[m + i] = q.fixed_delivery_fee_kes
            ub[i] = q.max_supply_capacity
            ub[m + i] = 1.0
            integrality[m + i] = 1

        rows = []
        lhs = []
        rhs = []

        r_dem = np.zeros(num_vars, dtype=float)
        r_dem[:m] = 1.0
        rows.append(r_dem)
        lhs.append(total_demand_quantity)
        rhs.append(np.inf)

        for i, q in enumerate(eligible):
            r_moq = np.zeros(num_vars, dtype=float)
            r_moq[i] = 1.0
            r_moq[m + i] = -q.min_order_quantity
            rows.append(r_moq)
            lhs.append(0.0)
            rhs.append(np.inf)

            r_cap = np.zeros(num_vars, dtype=float)
            r_cap[i] = 1.0
            r_cap[m + i] = -q.max_supply_capacity
            rows.append(r_cap)
            lhs.append(-np.inf)
            rhs.append(0.0)

        A = np.vstack(rows)
        res = milp(c=c, integrality=integrality, bounds=Bounds(lb, ub), constraints=LinearConstraint(A, lhs, rhs))
        if not res.success:
            raise InfeasibleScheduleError(f"Procurement MILP infeasible for '{material_id}': {res.message}")

        allocations = []
        total_purchase_cents = 0
        total_delivery_cents = 0
        weighted_rel = 0.0
        total_ordered = 0.0

        for i, q in enumerate(eligible):
            qty = float(res.x[i])
            y_val = int(round(res.x[m + i]))
            if y_val == 1 and qty > 1e-6:
                p_money = MoneyKES.from_kes(q.unit_price_kes).multiply_quantity(qty)
                d_money = MoneyKES.from_kes(q.fixed_delivery_fee_kes)
                sub_money = p_money + d_money
                total_purchase_cents += p_money.cents
                total_delivery_cents += d_money.cents
                weighted_rel += qty * q.reliability_score
                total_ordered += qty
                allocations.append({
                    "supplier_id": q.supplier_id,
                    "supplier_name": q.supplier_name,
                    "quotation_ref": q.quotation_ref,
                    "county": q.county,
                    "ordered_quantity": round(qty, 2),
                    "unit_price_kes": q.unit_price_kes,
                    "fixed_delivery_fee_kes": q.fixed_delivery_fee_kes,
                    "lead_time_days": q.lead_time_days,
                    "reliability_score": q.reliability_score,
                    "verification_status": q.verification_status.value,
                    "subtotal_kes": sub_money.to_kes(),
                    "subtotal_cents": sub_money.cents,
                })

        tot_money = MoneyKES(total_purchase_cents + total_delivery_cents)
        return {
            "material_id": material_id,
            "execution_mode": "SIMULATION" if allow_synthetic_or_unverified_quotes else "PRODUCTION",
            "production_procurement_authorized": not allow_synthetic_or_unverified_quotes,
            "is_optimal": True,
            "total_demand_quantity": total_demand_quantity,
            "total_ordered_quantity": round(total_ordered, 2),
            "total_purchase_cost_kes": MoneyKES(total_purchase_cents).to_kes(),
            "total_delivery_fees_kes": MoneyKES(total_delivery_cents).to_kes(),
            "total_procurement_cost_kes": tot_money.to_kes(),
            "total_procurement_cost_cents": tot_money.cents,
            "portfolio_reliability_score": round(weighted_rel / max(1e-9, total_ordered), 4),
            "allocations": allocations,
            "rejected_quotations": rejected_quotes,
        }
