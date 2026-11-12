"""
Procurement Requisition Model & Duplicate Requisition Blocker.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Set, Tuple


@dataclass(frozen=True)
class ProcurementRequisition:
    requisition_id: str
    project_id: str
    activity_id: str
    material_id: str
    quantity_required: float
    required_by_day: int


def deduplicate_requisitions(
    requisitions: List[ProcurementRequisition],
) -> Tuple[List[ProcurementRequisition], List[str]]:
    seen_ids: Set[str] = set()
    seen_signatures: Set[Tuple[str, str, str, int]] = set()
    clean_list: List[ProcurementRequisition] = []
    duplicates_flagged: List[str] = []

    for req in requisitions:
        sig = (req.project_id, req.activity_id, req.material_id, req.required_by_day)
        if req.requisition_id in seen_ids or sig in seen_signatures:
            duplicates_flagged.append(
                f"Duplicate requisition '{req.requisition_id}' for ({req.project_id}, {req.activity_id}, {req.material_id}) blocked."
            )
            continue
        seen_ids.add(req.requisition_id)
        seen_signatures.add(sig)
        clean_list.append(req)

    return clean_list, duplicates_flagged
