from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import (
    BigIntegerField,
    Case,
    Count,
    F,
    Max,
    Q,
    Sum,
    Value,
    When,
)
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.backoffice.domain import (
    CONSTRUCTION_WIDGET_ROLES,
    ENQUIRY_WIDGET_ROLES,
    FINANCE_WIDGET_ROLES,
    MARKETPLACE_WIDGET_ROLES,
    PROPERTY_WIDGET_ROLES,
    RENTAL_WIDGET_ROLES,
    AccessScope,
    RoleEventAction,
    StaffRole,
)
from apps.backoffice.errors import BackofficeError
from apps.backoffice.models import StaffRoleEvent, StaffRoleGrant
from apps.construction.domain import BudgetStatus, ProjectStatus
from apps.construction.models import ConstructionProject, CostRecord, ProjectBudget
from apps.identity.domain import AccountStatus
from apps.marketplace.domain import ListingStatus
from apps.marketplace.models import Enquiry, Listing
from apps.organizations.domain import OrganizationStatus
from apps.organizations.models import Organization
from apps.properties.domain import PropertyStatus
from apps.properties.models import Property
from apps.tenancy.domain import LeaseStatus
from apps.tenancy.models import Lease, RentAllocation, RentInvoice


def _require_active_mfa(actor) -> None:
    if not getattr(actor, "is_authenticated", False):
        raise BackofficeError("Authentication is required.", status_code=401)
    if getattr(actor, "status", None) != AccountStatus.ACTIVE:
        raise BackofficeError("An active account is required.", status_code=403)
    if not actor.has_confirmed_mfa():
        raise BackofficeError("Confirmed multi-factor authentication is required.", status_code=403)


def _require_role_administrator(actor) -> None:
    _require_active_mfa(actor)
    if not actor.is_staff or not actor.is_superuser:
        raise BackofficeError(
            "Only an MFA-confirmed platform superadministrator can manage staff roles.",
            status_code=403,
        )


def _record_role_event(*, grant, actor, action) -> StaffRoleEvent:
    return StaffRoleEvent.objects.create(
        grant=grant,
        action=action,
        actor=actor,
        metadata={
            "role": grant.role,
            "scope": grant.scope,
            "organization_id": str(grant.organization_id) if grant.organization_id else None,
        },
    )


@transaction.atomic
def grant_staff_role(
    *,
    actor,
    user_id,
    role: str,
    scope: str,
    organization_id=None,
    expires_at=None,
) -> StaffRoleGrant:
    _require_role_administrator(actor)
    now = timezone.now()
    if role not in StaffRole.VALUES:
        raise BackofficeError("The requested staff role is not recognized.")
    if scope not in AccessScope.VALUES:
        raise BackofficeError("The requested access scope is not recognized.")
    if scope == AccessScope.ORGANIZATION and organization_id is None:
        raise BackofficeError("An organization-scoped role requires an organization.")
    if scope == AccessScope.PLATFORM and organization_id is not None:
        raise BackofficeError("A platform-scoped role cannot target an organization.")
    if expires_at is not None and timezone.is_naive(expires_at):
        raise BackofficeError("Role grant expiry must include a timezone.")
    if expires_at is not None and expires_at <= now:
        raise BackofficeError("A role grant expiry must be in the future.")

    user_model = get_user_model()
    try:
        subject = user_model.objects.select_for_update().get(pk=user_id)
    except user_model.DoesNotExist as exc:
        raise BackofficeError("The target account was not found.", status_code=404) from exc
    if subject.status != AccountStatus.ACTIVE:
        raise BackofficeError("A role can only be granted to an active account.")

    organization = None
    if scope == AccessScope.ORGANIZATION:
        try:
            organization = Organization.objects.select_for_update().get(
                pk=organization_id,
                status=OrganizationStatus.ACTIVE,
            )
        except Organization.DoesNotExist as exc:
            raise BackofficeError(
                "The target active organization was not found.",
                status_code=404,
            ) from exc
    active_grant = StaffRoleGrant.objects.filter(
        user=subject,
        role=role,
        scope=scope,
        organization=organization,
        revoked_at__isnull=True,
    ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
    if active_grant.exists():
        raise BackofficeError(
            "An active grant already exists for this role and scope.", status_code=409
        )

    grant = StaffRoleGrant.objects.create(
        user=subject,
        role=role,
        scope=scope,
        organization=organization,
        granted_by=actor,
        expires_at=expires_at,
    )
    _record_role_event(grant=grant, actor=actor, action=RoleEventAction.GRANTED)
    return grant


@transaction.atomic
def revoke_staff_role(*, actor, grant_id) -> StaffRoleGrant:
    _require_role_administrator(actor)
    try:
        grant = StaffRoleGrant.objects.select_for_update().get(pk=grant_id)
    except StaffRoleGrant.DoesNotExist as exc:
        raise BackofficeError("The role grant was not found.", status_code=404) from exc
    if grant.revoked_at is not None:
        raise BackofficeError("The role grant has already been revoked.", status_code=409)
    grant.revoked_at = timezone.now()
    grant.revoked_by = actor
    grant.save(update_fields=["revoked_at", "revoked_by"])
    _record_role_event(grant=grant, actor=actor, action=RoleEventAction.REVOKED)
    return grant


def _latest(queryset, field: str):
    return queryset.aggregate(value=Max(field))["value"]


def _latest_of(*values):
    present = [value for value in values if value is not None]
    return max(present) if present else None


def _status_totals(queryset) -> tuple[dict[str, int], int]:
    totals = {
        row["status"]: row["count"] for row in queryset.values("status").annotate(count=Count("pk"))
    }
    return totals, sum(totals.values())


def _metric(*, metric_id, label, value, sources, scope, as_of):
    return {
        "id": metric_id,
        "label": label,
        "value": value,
        "source": sources,
        "scope": scope,
        "as_of": as_of,
    }


def _resolve_access(actor):
    _require_active_mfa(actor)
    now = timezone.now()
    grants = list(
        StaffRoleGrant.objects.filter(user=actor, revoked_at__isnull=True)
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
        .select_related("organization")
    )
    if not grants:
        raise BackofficeError("No active backoffice role has been assigned.", status_code=403)

    active_organization_ids = list(
        Organization.objects.filter(status=OrganizationStatus.ACTIVE).values_list(
            "id", flat=True
        )
    )
    active_organization_id_set = set(active_organization_ids)
    role_scopes = defaultdict(set)
    platform_roles = set()
    for grant in grants:
        if grant.scope == AccessScope.PLATFORM:
            platform_roles.add(grant.role)
        elif grant.organization_id in active_organization_id_set:
            role_scopes[grant.role].add(grant.organization_id)

    authorized_organization_ids = set().union(*role_scopes.values()) if role_scopes else set()
    if platform_roles:
        authorized_organization_ids.update(active_organization_id_set)
    if not authorized_organization_ids:
        raise BackofficeError("No active organization scope is available.", status_code=403)

    return role_scopes, platform_roles, active_organization_ids, authorized_organization_ids


def _widget_scope(role_scopes, platform_roles, active_organization_ids, widget_roles):
    allowed_organization_ids = set()
    allowed_platform_roles = platform_roles & widget_roles
    if allowed_platform_roles:
        allowed_organization_ids.update(active_organization_ids)
    for role in widget_roles - allowed_platform_roles:
        allowed_organization_ids.update(role_scopes.get(role, set()))
    if not allowed_organization_ids:
        return None

    return (
        sorted(allowed_organization_ids, key=str),
        {
            "type": "platform" if allowed_platform_roles else "organizations",
            "organization_count": len(allowed_organization_ids),
        },
    )


def _property_metrics(organization_ids, scope):
    properties = Property.objects.filter(managing_organization_id__in=organization_ids)
    status_totals, total = _status_totals(properties)
    return _metric(
        metric_id="properties",
        label="Managed properties",
        value={
            "total": total,
            "active": status_totals.get(PropertyStatus.ACTIVE, 0),
            "draft": status_totals.get(PropertyStatus.DRAFT, 0),
            "archived": status_totals.get(PropertyStatus.ARCHIVED, 0),
        },
        sources=["properties.Property"],
        scope=scope,
        as_of=_latest(properties, "updated_at"),
    )


def _marketplace_metrics(organization_ids, scope):
    listing_scope = Q(advertising_organization_id__in=organization_ids) | Q(
        property__managing_organization_id__in=organization_ids
    )
    listings = Listing.objects.filter(listing_scope)
    status_totals, total = _status_totals(listings)

    return _metric(
        metric_id="marketplace_listings",
        label="Marketplace listings",
        value={
            "total": total,
            "published": status_totals.get(ListingStatus.PUBLISHED, 0),
            "pending_review": status_totals.get(ListingStatus.PENDING_REVIEW, 0),
        },
        sources=["marketplace.Listing"],
        scope=scope,
        as_of=_latest(listings, "updated_at"),
    )


def _enquiry_metrics(organization_ids, scope):
    enquiry_scope = (
        Q(organization_id__in=organization_ids)
        | Q(listing__advertising_organization_id__in=organization_ids)
        | Q(listing__property__managing_organization_id__in=organization_ids)
    )
    enquiries = Enquiry.objects.filter(enquiry_scope)
    status_totals, total = _status_totals(enquiries)
    return _metric(
        metric_id="marketplace_enquiries",
        label="Marketplace enquiry activity",
        value={
            "total": total,
            "by_status": status_totals,
        },
        sources=["marketplace.Enquiry"],
        scope=scope,
        as_of=_latest(enquiries, "updated_at"),
    )


def _lease_queryset(organization_ids):
    lease_scope = Q(managing_organization_id__in=organization_ids) | Q(
        property__managing_organization_id__in=organization_ids
    )
    return Lease.objects.filter(lease_scope)


def _rental_metrics(organization_ids, scope):
    leases = _lease_queryset(organization_ids)
    active = leases.filter(status=LeaseStatus.ACTIVE).count()
    under_renewal = leases.filter(status=LeaseStatus.UNDER_RENEWAL).count()
    return _metric(
        metric_id="rental_leases",
        label="Managed rental agreements",
        value={
            "total": leases.count(),
            "active": active,
            "under_renewal": under_renewal,
        },
        sources=["tenancy.Lease"],
        scope=scope,
        as_of=_latest(leases, "updated_at"),
    )


def _rental_finance_metrics(organization_ids, scope):
    invoices = RentInvoice.objects.filter(lease__in=_lease_queryset(organization_ids))
    invoice_totals = {
        row["currency"]: {
            "invoice_count": row["invoice_count"],
            "amount_demanded_minor": row["amount_demanded_minor"] or 0,
            "net_allocations_minor": 0,
            "overdue_invoice_count": 0,
            "overdue_balance_minor": 0,
        }
        for row in invoices.values("currency").annotate(
            invoice_count=Count("pk"),
            amount_demanded_minor=Sum("total_minor"),
        )
    }

    allocations = RentAllocation.objects.filter(invoice__in=invoices)
    for row in allocations.values("invoice__currency").annotate(
        gross=Sum("amount_minor", filter=Q(reversal_of__isnull=True)),
        reversed=Sum("amount_minor", filter=Q(reversal_of__isnull=False)),
    ):
        currency = row["invoice__currency"]
        invoice_totals.setdefault(
            currency,
            {
                "invoice_count": 0,
                "amount_demanded_minor": 0,
                "net_allocations_minor": 0,
                "overdue_invoice_count": 0,
                "overdue_balance_minor": 0,
            },
        )["net_allocations_minor"] = (row["gross"] or 0) - (row["reversed"] or 0)

    net_allocated = Coalesce(
        Sum(
            Case(
                When(
                    allocations__reversal_of__isnull=True,
                    then=F("allocations__amount_minor"),
                ),
                When(
                    allocations__reversal_of__isnull=False,
                    then=-F("allocations__amount_minor"),
                ),
                default=Value(0),
                output_field=BigIntegerField(),
            )
        ),
        Value(0, output_field=BigIntegerField()),
    )
    overdue = (
        invoices.filter(due_date__lt=timezone.localdate())
        .annotate(net_allocated_minor=net_allocated)
        .filter(net_allocated_minor__lt=F("total_minor"))
        .values("currency", "total_minor", "net_allocated_minor")
    )
    for row in overdue:
        values = invoice_totals.setdefault(
            row["currency"],
            {
                "invoice_count": 0,
                "amount_demanded_minor": 0,
                "net_allocations_minor": 0,
                "overdue_invoice_count": 0,
                "overdue_balance_minor": 0,
            },
        )
        values["overdue_invoice_count"] += 1
        values["overdue_balance_minor"] += row["total_minor"] - row["net_allocated_minor"]

    invoice_latest = _latest(invoices, "issued_at")
    allocation_latest = _latest(allocations, "created_at")
    return _metric(
        metric_id="rental_finance",
        label="Rental invoice and allocation summary",
        value={
            "by_currency": [
                {"currency": currency, **values}
                for currency, values in sorted(invoice_totals.items())
            ],
            "interpretation": (
                "Invoice amounts are demands. Rent allocations are bookkeeping entries, "
                "not proof of a cash receipt."
            ),
        },
        sources=["tenancy.RentInvoice", "tenancy.RentAllocation"],
        scope=scope,
        as_of=_latest_of(invoice_latest, allocation_latest),
    )


def _construction_metrics(organization_ids, scope):
    projects = ConstructionProject.objects.filter(organization_id__in=organization_ids)
    status_totals, total = _status_totals(projects)
    return _metric(
        metric_id="construction_projects",
        label="Construction projects",
        value={
            "total": total,
            "active": status_totals.get(ProjectStatus.ACTIVE, 0),
            "by_status": status_totals,
        },
        sources=["construction.ConstructionProject"],
        scope=scope,
        as_of=_latest(projects, "updated_at"),
    )


def _construction_finance_metrics(organization_ids, scope):
    approved_budgets = ProjectBudget.objects.filter(
        project__organization_id__in=organization_ids,
        status=BudgetStatus.APPROVED,
    )
    approved_by_currency = {
        row["currency"]: row["approved_amount"] or Decimal("0")
        for row in approved_budgets.values("currency").annotate(approved_amount=Sum("total"))
    }
    costs = CostRecord.objects.filter(project__organization_id__in=organization_ids)
    cost_by_currency = {
        row["category__budget__currency"]: row
        for row in costs.values("category__budget__currency").annotate(
            commitment=Sum("amount", filter=Q(record_type="commitment")),
            actual=Sum("amount", filter=Q(record_type="actual")),
            forecast=Sum("amount", filter=Q(record_type="forecast")),
        )
    }
    currencies = sorted(set(approved_by_currency) | set(cost_by_currency))
    by_currency = []
    for currency in currencies:
        costs_for_currency = cost_by_currency.get(currency, {})
        by_currency.append(
            {
                "currency": currency,
                "approved_budget": format(approved_by_currency.get(currency, Decimal("0")), "f"),
                "committed_cost": format(costs_for_currency.get("commitment") or Decimal("0"), "f"),
                "actual_cost": format(costs_for_currency.get("actual") or Decimal("0"), "f"),
                "forecast_input": format(costs_for_currency.get("forecast") or Decimal("0"), "f"),
            }
        )
    budget_latest = _latest(approved_budgets, "approved_at") or _latest(
        approved_budgets, "created_at"
    )
    cost_latest = _latest(costs, "recorded_at")
    return _metric(
        metric_id="construction_costs",
        label="Construction budget and cost inputs",
        value={"by_currency": by_currency},
        sources=["construction.ProjectBudget", "construction.CostRecord"],
        scope=scope,
        as_of=_latest_of(budget_latest, cost_latest),
    )


def dashboard_overview(*, actor):
    role_scopes, platform_roles, active_organization_ids, authorized_organization_ids = (
        _resolve_access(actor)
    )
    has_platform_scope = bool(platform_roles)
    has_organization_scope = bool(role_scopes)
    scope = {
        "type": (
            "mixed"
            if has_platform_scope and has_organization_scope
            else "platform"
            if has_platform_scope
            else "organizations"
        ),
        "organization_count": len(authorized_organization_ids),
    }
    widgets = []

    for role_set, metric_builder in (
        (PROPERTY_WIDGET_ROLES, _property_metrics),
        (MARKETPLACE_WIDGET_ROLES, _marketplace_metrics),
        (ENQUIRY_WIDGET_ROLES, _enquiry_metrics),
        (RENTAL_WIDGET_ROLES, _rental_metrics),
        (FINANCE_WIDGET_ROLES, _rental_finance_metrics),
        (CONSTRUCTION_WIDGET_ROLES, _construction_metrics),
        (FINANCE_WIDGET_ROLES, _construction_finance_metrics),
    ):
        widget_scope = _widget_scope(
            role_scopes,
            platform_roles,
            active_organization_ids,
            role_set,
        )
        if widget_scope is not None:
            organization_ids, metric_scope = widget_scope
            widgets.append(metric_builder(organization_ids, metric_scope))

    return {
        "generated_at": timezone.now(),
        "scope": scope,
        "widgets": widgets,
    }


def role_grants_for_administrator(*, actor):
    _require_role_administrator(actor)
    return StaffRoleGrant.objects.select_related("user", "organization", "granted_by", "revoked_by")


def role_events_for_administrator(*, actor):
    _require_role_administrator(actor)
    return StaffRoleEvent.objects.select_related("actor", "grant")
