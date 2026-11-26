"""Organization scope for spatial HTTP requests.

Anonymous callers see organization-null public rows only. Authenticated callers
also see rows owned by their active memberships. A query parameter or header
cannot select an organization the caller does not belong to.
"""

from __future__ import annotations

import uuid

from django.db import models
from rest_framework import exceptions

from apps.organizations.domain import MembershipStatus
from apps.organizations.models import Organization, OrganizationMembership


def resolve_request_org_ids(request) -> list[uuid.UUID]:
    """Return organization ids the caller may use for this request."""
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return []

    member_org_ids = list(
        OrganizationMembership.objects.filter(
            user=user,
            status=MembershipStatus.ACTIVE,
        ).values_list("organization_id", flat=True)
    )

    req_org = getattr(request, "organization", None)
    if req_org:
        req_org_id = req_org.id if hasattr(req_org, "id") else uuid.UUID(str(req_org))
        if req_org_id not in member_org_ids:
            raise exceptions.PermissionDenied("Cross-organization access denied.")
        return [req_org_id]

    user_org = getattr(user, "organization", None)
    if user_org:
        user_org_id = user_org.id if hasattr(user_org, "id") else uuid.UUID(str(user_org))
        if user_org_id not in member_org_ids:
            raise exceptions.PermissionDenied("Cross-organization access denied.")
        return [user_org_id]

    org_param = request.query_params.get("organization_id") or request.headers.get(
        "X-Organization-Id"
    )
    if not org_param and hasattr(request, "data"):
        body_org = request.data.get("organization_id") if hasattr(request.data, "get") else None
        if body_org:
            org_param = str(body_org)
    if org_param:
        try:
            target_id = uuid.UUID(str(org_param))
        except (ValueError, TypeError) as exc:
            raise exceptions.ValidationError("Invalid organization ID.") from exc
        if target_id not in member_org_ids:
            raise exceptions.PermissionDenied("Cross-organization access denied.")
        return [target_id]

    return member_org_ids


def scope_public_or_member(queryset, request):
    """Public rows, plus the caller's organizations when authenticated."""
    public = models.Q(organization__isnull=True)
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return queryset.filter(public)
    org_ids = resolve_request_org_ids(request)
    if not org_ids:
        return queryset.filter(public)
    return queryset.filter(public | models.Q(organization_id__in=org_ids))


def scope_member_orgs(queryset, request, lookup: str):
    """Keep only rows whose organization lookup is one of the caller's memberships."""
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return queryset.none()
    org_ids = resolve_request_org_ids(request)
    return queryset.filter(**{f"{lookup}__in": org_ids})


def hidden_private_urns(request):
    """Canonical URNs of private entities the caller must not see."""
    from apps.spatial.models import SpatialEntity

    hidden = SpatialEntity.objects.exclude(organization__isnull=True)
    user = getattr(request, "user", None)
    if user and user.is_authenticated:
        org_ids = resolve_request_org_ids(request)
        if org_ids:
            hidden = hidden.exclude(organization_id__in=org_ids)
    return hidden.values("canonical_urn")


def bind_member_organization(request, serializer, *, created_by_field: str = "created_by"):
    """Force a write onto one organization the caller belongs to."""
    org_ids = resolve_request_org_ids(request)
    requested = serializer.validated_data.pop("organization", None)
    if requested is None:
        requested = serializer.validated_data.pop("organization_id", None)
    if requested is not None:
        requested_id = getattr(requested, "id", requested)
        if requested_id not in org_ids:
            raise exceptions.PermissionDenied("Cross-organization access denied.")
        organization = (
            requested if hasattr(requested, "id") else Organization.objects.get(pk=requested_id)
        )
    elif len(org_ids) == 1:
        organization = Organization.objects.get(pk=org_ids[0])
    else:
        raise exceptions.ValidationError({"organization_id": "Select one of your organizations."})
    save_kwargs = {"organization": organization}
    if created_by_field and getattr(request.user, "is_authenticated", False):
        save_kwargs[created_by_field] = request.user
    serializer.save(**save_kwargs)
    return organization
