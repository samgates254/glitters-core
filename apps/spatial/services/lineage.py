"""URN-based entity lineage traversal service for GLITTERS Core.

Traverses cadastral mutations, subdivisions, consolidations, and realignments
with bounded recursion, cycle protection, and cross-tenant access control.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set

from apps.organizations.models import Organization
from apps.spatial.models import EntityLineage, SpatialEntity

logger = logging.getLogger(__name__)

MAX_TRAVERSAL_DEPTH = 10
DEFAULT_TRAVERSAL_DEPTH = 5


def traverse_entity_lineage(
    entity_urn: str,
    organization: Optional[Organization] = None,
    max_depth: int = DEFAULT_TRAVERSAL_DEPTH,
) -> Dict[str, Any]:
    """
    Performs bounded bi-directional lineage traversal for a canonical URN.

    Protections:
    - Cycle detection via visited set
    - Hard depth ceiling (max 10)
    - Tenant isolation checking on related entities
    """
    if not entity_urn or not isinstance(entity_urn, str):
        raise ValueError("entity_urn must be a non-empty string.")

    depth_limit = max(1, min(max_depth, MAX_TRAVERSAL_DEPTH))
    cycle_detected = False

    # Check if target entity exists and is accessible
    base_entity = SpatialEntity.objects.filter(canonical_urn=entity_urn).first()
    entity_accessible = True
    entity_type = None

    if base_entity:
        entity_type = base_entity.entity_type
        if base_entity.organization_id is not None:
            if organization is None or base_entity.organization_id != organization.id:
                entity_accessible = False

    def is_urn_accessible(target_urn: str) -> bool:
        """Verifies whether the caller is authorized to view details of target_urn."""
        rel_entity = SpatialEntity.objects.filter(canonical_urn=target_urn).first()
        if not rel_entity:
            # External or historical URN not in active database is viewable as public provenance
            return True
        if rel_entity.organization_id is None:
            return True
        return organization is not None and rel_entity.organization_id == organization.id

    # 1. Traverse Predecessors (Ancestors): Who did this entity originate from?
    predecessors: List[Dict[str, Any]] = []
    visited_ancestors: Set[str] = {entity_urn}

    def _traverse_ancestors(current_urn: str, current_depth: int) -> None:
        nonlocal cycle_detected
        if current_depth > depth_limit:
            return

        lineage_records = EntityLineage.objects.filter(successor_urn=current_urn).order_by(
            "-effective_date"
        )
        for record in lineage_records:
            pred_urn = record.predecessor_urn
            accessible = is_urn_accessible(pred_urn)

            predecessors.append({
                "lineage_id": str(record.id),
                "relationship": record.lineage_type,
                "predecessor_urn": pred_urn if accessible else "urn:glitters:restricted:tenant_entity",
                "successor_urn": record.successor_urn,
                "effective_date": record.effective_date.isoformat() if record.effective_date else None,
                "authorization_reference": record.authorization_reference,
                "depth": current_depth,
                "accessible": accessible,
            })

            if pred_urn in visited_ancestors:
                cycle_detected = True
                logger.warning(
                    "Cycle detected during lineage traversal: URN %s already visited.", pred_urn
                )
                continue

            visited_ancestors.add(pred_urn)
            _traverse_ancestors(pred_urn, current_depth + 1)

    _traverse_ancestors(entity_urn, 1)

    # 2. Traverse Successors (Descendants): What did this entity become?
    successors: List[Dict[str, Any]] = []
    visited_descendants: Set[str] = {entity_urn}

    def _traverse_descendants(current_urn: str, current_depth: int) -> None:
        nonlocal cycle_detected
        if current_depth > depth_limit:
            return

        lineage_records = EntityLineage.objects.filter(predecessor_urn=current_urn).order_by(
            "-effective_date"
        )
        for record in lineage_records:
            succ_urn = record.successor_urn
            accessible = is_urn_accessible(succ_urn)

            successors.append({
                "lineage_id": str(record.id),
                "relationship": record.lineage_type,
                "predecessor_urn": record.predecessor_urn,
                "successor_urn": succ_urn if accessible else "urn:glitters:restricted:tenant_entity",
                "effective_date": record.effective_date.isoformat() if record.effective_date else None,
                "authorization_reference": record.authorization_reference,
                "depth": current_depth,
                "accessible": accessible,
            })

            if succ_urn in visited_descendants:
                cycle_detected = True
                logger.warning(
                    "Cycle detected during lineage traversal: URN %s already visited.", succ_urn
                )
                continue

            visited_descendants.add(succ_urn)
            _traverse_descendants(succ_urn, current_depth + 1)

    _traverse_descendants(entity_urn, 1)

    return {
        "entity_urn": entity_urn,
        "exists_in_database": base_entity is not None,
        "entity_type": entity_type,
        "accessible": entity_accessible,
        "traversal_depth_limit": depth_limit,
        "predecessors_count": len(predecessors),
        "successors_count": len(successors),
        "predecessors": predecessors,
        "successors": successors,
        "cycle_detected": cycle_detected,
    }
