"""
Bill of Quantities (BOQ) Engine with Multi-Dimensional Provenance & Completeness Auditing.
Remediates CRITICAL 4:
- `validate_completeness()` verifies:
  1. All mandatory archetype sections are present.
  2. Every work item has strictly positive quantity (> 0).
  3. Every work item has complete, non-zero pricing (all required material unit prices exist and > 0,
     and total unit rate > 0).
- Computes all 6 auditor-required evidence coverage indicators:
  1. `verified_measurement_coverage`
  2. `verified_quotation_coverage`
  3. `verified_labour_rate_coverage`
  4. `verified_equipment_rate_coverage`
  5. `overall_cost_evidence_coverage` (requires BOTH verified quantity AND verified rates)
  6. `assumptions_requiring_qs_approval`
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from ..project_model import (
    MeasurementStatus,
    MoneyKES,
    ProjectArchetype,
    RateProvenanceStatus,
)
from ..procurement.quotations import QuotationVerificationStatus


REQUIRED_ARCHETYPE_SECTIONS: Dict[ProjectArchetype, Tuple[str, ...]] = {
    ProjectArchetype.RESIDENTIAL: (
        "Excavation", "Foundations", "Concrete Works", "Reinforcement",
        "Masonry", "Roofing", "Flooring", "Plastering", "Painting",
        "Electrical Installations", "Plumbing Installations", "Finishing Works"
    ),
    ProjectArchetype.COMMERCIAL: (
        "Excavation", "Foundations", "Concrete Works", "Reinforcement",
        "Masonry", "Roofing", "Flooring", "Plastering", "Painting",
        "Electrical Installations", "Plumbing Installations", "Finishing Works"
    ),
    ProjectArchetype.ROADS_CIVIL: (
        "Excavation", "Foundations", "Concrete Works", "Reinforcement"
    ),
}


@dataclass
class BOQWorkItem:
    item_code: str
    section: str
    description: str
    unit: str
    quantity: float
    measurement_status: MeasurementStatus
    material_Breakdown_per_unit: Dict[str, float] = field(default_factory=dict)
    labour_hours_per_unit: float = 1.0
    labour_hourly_rate_kes: float = 250.0
    equipment_hours_per_unit: float = 0.0
    equipment_hourly_rate_kes: float = 0.0
    overhead_and_profit_pct: float = 0.15
    rate_source_status: MeasurementStatus = MeasurementStatus.SYNTHETIC_BENCHMARK
    quotation_verification_status: QuotationVerificationStatus = QuotationVerificationStatus.SYNTHETIC_BENCHMARK_QUOTATION
    labour_rate_provenance: RateProvenanceStatus = RateProvenanceStatus.SYNTHETIC_BENCHMARK_RATE
    equipment_rate_provenance: RateProvenanceStatus = RateProvenanceStatus.SYNTHETIC_BENCHMARK_RATE

    def compute_unit_rate_kes(self, material_unit_prices_kes: Dict[str, float]) -> Dict[str, float | int]:
        if self.quantity <= 0:
            raise ValueError(f"BOQ item {self.item_code} must have strictly positive quantity (got {self.quantity}).")
        mat_cost = 0.0
        for mat_id, qty_per_unit in self.material_Breakdown_per_unit.items():
            if mat_id not in material_unit_prices_kes:
                raise KeyError(f"Missing quotation/unit price for material '{mat_id}' in BOQ item '{self.item_code}'")
            if material_unit_prices_kes[mat_id] <= 0:
                raise ValueError(f"Non-positive unit price ({material_unit_prices_kes[mat_id]}) for material '{mat_id}' in BOQ item '{self.item_code}'")
            mat_cost += qty_per_unit * material_unit_prices_kes[mat_id]
        lab_cost = self.labour_hours_per_unit * self.labour_hourly_rate_kes
        eq_cost = self.equipment_hours_per_unit * self.equipment_hourly_rate_kes
        direct_unit_cost = mat_cost + lab_cost + eq_cost
        if direct_unit_cost <= 0:
            raise ValueError(f"BOQ item '{self.item_code}' is unpriced (direct unit cost <= 0).")
        unit_rate_with_ohp = direct_unit_cost * (1.0 + self.overhead_and_profit_pct)
        total_money = MoneyKES.from_kes(unit_rate_with_ohp).multiply_quantity(self.quantity)
        return {
            "material_unit_cost_kes": round(mat_cost, 2),
            "labour_unit_cost_kes": round(lab_cost, 2),
            "equipment_unit_cost_kes": round(eq_cost, 2),
            "direct_unit_cost_kes": round(direct_unit_cost, 2),
            "unit_rate_kes": round(unit_rate_with_ohp, 2),
            "total_amount_kes": total_money.to_kes(),
            "total_amount_cents": total_money.cents,
        }


class BOQEngine:
    """
    Production-hardened BOQ Engine enforcing section completeness, positive quantities,
    complete pricing, and 6-dimensional evidence provenance coverage.
    """
    def __init__(self, project_id: str, archetype: ProjectArchetype = ProjectArchetype.RESIDENTIAL) -> None:
        self.project_id = project_id
        self.archetype = archetype
        self.items: Dict[str, BOQWorkItem] = {}

    def add_item(self, item: BOQWorkItem, allow_unvalidated_insert: bool = False) -> None:
        if item.item_code in self.items:
            raise ValueError(f"Duplicate BOQ item code: {item.item_code}")
        if not allow_unvalidated_insert and item.quantity <= 0:
            raise ValueError(f"BOQ item {item.item_code} must have strictly positive quantity.")
        self.items[item.item_code] = item

    def validate_completeness(
        self,
        material_unit_prices_kes: Optional[Dict[str, float]] = None,
    ) -> Tuple[bool, List[str]]:
        """
        Remediates CRITICAL 4:
        1. Checks that all mandatory archetype sections are present.
        2. Checks that no BOQ item has zero or negative quantity.
        3. Checks that no BOQ item is unpriced (zero direct cost or missing/non-positive material prices).
        """
        issues: List[str] = []
        if not self.items:
            return False, ["BOQ is completely empty."]

        present_sections = {item.section for item in self.items.values() if item.quantity > 0}
        required = REQUIRED_ARCHETYPE_SECTIONS.get(
            self.archetype,
            ("Excavation", "Foundations", "Concrete Works", "Reinforcement"),
        )
        for req in required:
            if req not in present_sections:
                issues.append(f"Missing mandatory BOQ section '{req}' for archetype {self.archetype.value}.")

        for code, item in self.items.items():
            if item.quantity <= 0:
                issues.append(f"BOQ item '{code}' has non-positive quantity ({item.quantity}).")
            if item.labour_hourly_rate_kes < 0 or item.equipment_hourly_rate_kes < 0:
                issues.append(f"BOQ item '{code}' has negative labour or equipment rate.")
            has_labour = (item.labour_hours_per_unit * item.labour_hourly_rate_kes) > 0
            has_equip = (item.equipment_hours_per_unit * item.equipment_hourly_rate_kes) > 0
            has_mat = len(item.material_Breakdown_per_unit) > 0
            if not (has_labour or has_equip or has_mat):
                issues.append(f"BOQ item '{code}' is completely unpriced (zero material, labour, and equipment inputs).")
            if material_unit_prices_kes is not None:
                for mat_id, req_q in item.material_Breakdown_per_unit.items():
                    if req_q > 0:
                        if mat_id not in material_unit_prices_kes:
                            issues.append(f"BOQ item '{code}' references unpriced material '{mat_id}'.")
                        elif material_unit_prices_kes[mat_id] <= 0:
                            issues.append(
                                f"BOQ item '{code}' material '{mat_id}' has non-positive price ({material_unit_prices_kes[mat_id]})."
                            )

        return (len(issues) == 0, issues)

    def evaluate_boq(self, material_unit_prices_kes: Dict[str, float]) -> Dict[str, object]:
        is_complete, completeness_issues = self.validate_completeness(material_unit_prices_kes)
        total_material_kes = 0.0
        total_labour_kes = 0.0
        total_equipment_kes = 0.0
        total_direct_kes = 0.0
        grand_total_cents = 0

        verified_measurement_cents = 0
        verified_quotation_mat_kes = 0.0
        verified_labour_rate_kes = 0.0
        verified_equipment_rate_kes = 0.0
        fully_verified_cost_cents = 0

        aggregated_materials: Dict[str, float] = {}
        status_breakdown_kes: Dict[str, float] = {s.value: 0.0 for s in MeasurementStatus}
        assumptions_requiring_qs_approval: List[str] = []
        item_summaries: List[Dict[str, object]] = []

        for code, item in self.items.items():
            cost_info = item.compute_unit_rate_kes(material_unit_prices_kes)
            q = item.quantity
            item_mat_kes = float(cost_info["material_unit_cost_kes"]) * q
            item_lab_kes = float(cost_info["labour_unit_cost_kes"]) * q
            item_eq_kes = float(cost_info["equipment_unit_cost_kes"]) * q
            item_dir_kes = float(cost_info["direct_unit_cost_kes"]) * q
            item_tot_cents = int(cost_info["total_amount_cents"])
            item_tot_kes = float(cost_info["total_amount_kes"])

            total_material_kes += item_mat_kes
            total_labour_kes += item_lab_kes
            total_equipment_kes += item_eq_kes
            total_direct_kes += item_dir_kes
            grand_total_cents += item_tot_cents
            status_breakdown_kes[item.measurement_status.value] += item_tot_kes

            qty_verified = (item.measurement_status == MeasurementStatus.VERIFIED_MEASUREMENT)
            quote_verified = (
                item.quotation_verification_status == QuotationVerificationStatus.VERIFIED_BINDING_QUOTATION
                or item_mat_kes == 0.0
            )
            lab_verified = (
                item.labour_rate_provenance == RateProvenanceStatus.VERIFIED_CONTRACT_RATE
                or item_lab_kes == 0.0
            )
            eq_verified = (
                item.equipment_rate_provenance == RateProvenanceStatus.VERIFIED_CONTRACT_RATE
                or item_eq_kes == 0.0
            )
            rate_verified = (
                item.rate_source_status == MeasurementStatus.VERIFIED_MEASUREMENT
                and quote_verified
                and lab_verified
                and eq_verified
            )

            if qty_verified:
                verified_measurement_cents += item_tot_cents
            else:
                assumptions_requiring_qs_approval.append(
                    f"Item '{code}' ({item.description}): Quantity ({q} {item.unit}) is {item.measurement_status.value}; requires signed QS site measurement."
                )

            if quote_verified:
                verified_quotation_mat_kes += item_mat_kes
            else:
                assumptions_requiring_qs_approval.append(
                    f"Item '{code}': Material rate status is {item.quotation_verification_status.value}; requires binding supplier quotation."
                )

            if lab_verified:
                verified_labour_rate_kes += item_lab_kes
            else:
                assumptions_requiring_qs_approval.append(
                    f"Item '{code}': Labour rate ({item.labour_hourly_rate_kes} KES/hr) is {item.labour_rate_provenance.value}; requires QS wage schedule approval."
                )

            if eq_verified:
                verified_equipment_rate_kes += item_eq_kes
            elif item_eq_kes > 0:
                assumptions_requiring_qs_approval.append(
                    f"Item '{code}': Equipment rate ({item.equipment_hourly_rate_kes} KES/hr) is {item.equipment_rate_provenance.value}; requires plant hire rate approval."
                )

            if qty_verified and rate_verified:
                fully_verified_cost_cents += item_tot_cents

            for mat_id, unit_q in item.material_Breakdown_per_unit.items():
                aggregated_materials[mat_id] = aggregated_materials.get(mat_id, 0.0) + unit_q * q

            item_summaries.append({
                "item_code": code,
                "section": item.section,
                "description": item.description,
                "quantity": q,
                "unit": item.unit,
                "measurement_status": item.measurement_status.value,
                "rate_source_status": item.rate_source_status.value,
                "quotation_verification_status": item.quotation_verification_status.value,
                "labour_rate_provenance": item.labour_rate_provenance.value,
                "equipment_rate_provenance": item.equipment_rate_provenance.value,
                "is_fully_evidence_backed": (qty_verified and rate_verified),
                **cost_info,
            })

        grand_total_kes = MoneyKES(grand_total_cents).to_kes()
        meas_cov = (verified_measurement_cents / grand_total_cents) if grand_total_cents > 0 else 0.0
        quote_cov = (verified_quotation_mat_kes / total_material_kes) if total_material_kes > 0 else 1.0
        lab_cov = (verified_labour_rate_kes / total_labour_kes) if total_labour_kes > 0 else 1.0
        eq_cov = (verified_equipment_rate_kes / total_equipment_kes) if total_equipment_kes > 0 else 1.0
        overall_cov = (fully_verified_cost_cents / grand_total_cents) if grand_total_cents > 0 else 0.0

        return {
            "project_id": self.project_id,
            "is_complete": is_complete,
            "completeness_issues": completeness_issues,
            "total_material_kes": round(total_material_kes, 2),
            "total_labour_kes": round(total_labour_kes, 2),
            "total_equipment_kes": round(total_equipment_kes, 2),
            "total_direct_cost_kes": round(total_direct_kes, 2),
            "grand_total_kes": grand_total_kes,
            "grand_total_cents": grand_total_cents,
            "verified_measurement_coverage": round(meas_cov, 4),
            "verified_quotation_coverage": round(quote_cov, 4),
            "verified_labour_rate_coverage": round(lab_cov, 4),
            "verified_equipment_rate_coverage": round(eq_cov, 4),
            "overall_cost_evidence_coverage": round(overall_cov, 4),
            # Alias retained for backward compatibility, now strictly equal to overall_cost_evidence_coverage
            "verified_measurement_cost_share": round(overall_cov, 4),
            "assumptions_requiring_qs_approval": assumptions_requiring_qs_approval,
            "cost_by_measurement_status_kes": {k: round(v, 2) for k, v in status_breakdown_kes.items()},
            "aggregated_material_requirements": {k: round(v, 3) for k, v in aggregated_materials.items()},
            "items": item_summaries,
        }
