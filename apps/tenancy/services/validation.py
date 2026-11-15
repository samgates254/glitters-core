"""Validation services for rentable property units and hierarchy integrity."""

from __future__ import annotations

from django.core.exceptions import ValidationError

from apps.properties.domain import HierarchyRole, PropertyStatus
from apps.properties.models import Property
from apps.tenancy.domain import INVALID_UNIT


def validate_rentable_unit(property_record: Property) -> None:
    """Validate that a property is an eligible canonical rentable unit.

    Invariants:
    1. Property must not be archived.
    2. Hierarchy role must be either UNIT or STANDALONE. Aggregate containers
       (BUILDING, ESTATE) cannot be leased directly.
    3. Standalone properties or units that have registered child units cannot be
       leased directly to prevent ambiguous parent/child occupancy allocation.
    4. If the property is a UNIT, it must have a parent property (BUILDING or ESTATE),
       and its parent cannot be another UNIT.
    """
    if property_record.status == PropertyStatus.ARCHIVED:
        raise ValidationError(
            {"property": f"Property {property_record.reference} is archived and cannot be leased."}
        )

    if property_record.hierarchy_role not in (HierarchyRole.UNIT, HierarchyRole.STANDALONE):
        raise ValidationError(
            {
                "property": (
                    f"{INVALID_UNIT} "
                    f"Property hierarchy role '{property_record.hierarchy_role}' is an aggregate container "
                    f"and cannot be leased directly. Only 'unit' or 'standalone' properties are rentable."
                )
            }
        )

    # Prevent ambiguous parent/child occupancy allocation:
    # A property with subdivided children cannot be leased as a single entity.
    if property_record.children.filter(
        status__in=[PropertyStatus.ACTIVE, PropertyStatus.DRAFT]
    ).exists():
        raise ValidationError(
            {
                "property": (
                    f"Property {property_record.reference} contains subdivided child units. "
                    f"Rentable units must be leased individually to prevent ambiguous occupancy allocation."
                )
            }
        )

    # If it is a UNIT, validate parent relationship
    if property_record.hierarchy_role == HierarchyRole.UNIT:
        if property_record.parent_id is None:
            raise ValidationError(
                {"property": f"Unit {property_record.reference} must belong to a parent building or estate."}
            )
        if property_record.parent.hierarchy_role == HierarchyRole.UNIT:
            raise ValidationError(
                {"property": f"Unit {property_record.reference} cannot have a parent that is also a unit."}
            )
