"""Property queries start from the caller's accessible set.

Spatial filters use the GiST index on the geography point. They never scan
an unfiltered table in Python, and they never add rows the caller cannot see.
"""

from __future__ import annotations

from django.contrib.gis.db.models.functions import Distance
from django.contrib.gis.measure import D
from django.db.models import Q

from apps.locations.selectors import descendant_area_ids
from apps.organizations.domain import MembershipStatus, OrganizationStatus
from apps.organizations.models import OrganizationMembership
from apps.organizations.services.authorization import ensure_active_account
from apps.properties.domain import FILTER_INVALID, AccessStatus, ManagementContext
from apps.properties.errors import PropertyError
from apps.properties.filters import PropertyFilters
from apps.properties.models import Property, PropertyAccess


def accessible_properties(user):
    ensure_active_account(user)
    access_ids = PropertyAccess.objects.filter(user=user, status=AccessStatus.ACTIVE).values(
        "property_id"
    )
    member_organizations = OrganizationMembership.objects.filter(
        user=user,
        status=MembershipStatus.ACTIVE,
        organization__status=OrganizationStatus.ACTIVE,
    ).values("organization_id")
    blocked_organizations = (
        OrganizationMembership.objects.filter(user=user)
        .exclude(status=MembershipStatus.ACTIVE)
        .values("organization_id")
    )
    return Property.objects.filter(
        Q(management_context=ManagementContext.PERSONAL, id__in=access_ids)
        | Q(
            management_context=ManagementContext.ORGANIZATION,
            managing_organization_id__in=member_organizations,
        )
        | (
            Q(
                management_context=ManagementContext.ORGANIZATION,
                managing_organization__status=OrganizationStatus.ACTIVE,
                id__in=access_ids,
            )
            & ~Q(managing_organization_id__in=blocked_organizations)
        )
    )


def search_properties(user, filters: PropertyFilters):
    queryset = accessible_properties(user).select_related(
        "location__address__country",
        "location__address__administrative_area",
        "managing_organization",
        "parent",
    )
    if filters.status:
        queryset = queryset.filter(status=filters.status)
    if filters.property_type:
        queryset = queryset.filter(property_type=filters.property_type)
    if filters.management_context:
        queryset = queryset.filter(management_context=filters.management_context)
    if filters.organization_id:
        queryset = queryset.filter(managing_organization_id=filters.organization_id)
    if filters.parent_id:
        queryset = queryset.filter(parent_id=filters.parent_id)
    if filters.country:
        queryset = queryset.filter(location__address__country_id=filters.country)
    if filters.administrative_area_id:
        area_ids = descendant_area_ids(filters.administrative_area_id)
        if not area_ids:
            raise PropertyError(FILTER_INVALID, status_code=400)
        queryset = queryset.filter(location__address__administrative_area_id__in=area_ids)
    if filters.bbox is not None:
        queryset = queryset.filter(location__coordinates__intersects=filters.bbox)
    if filters.near is not None:
        queryset = queryset.filter(
            location__coordinates__dwithin=(filters.near, D(m=filters.radius_m))
        ).annotate(distance_m=Distance("location__coordinates", filters.near))
        return queryset.order_by("distance_m", "id")
    return queryset.order_by("-created_at", "id")
