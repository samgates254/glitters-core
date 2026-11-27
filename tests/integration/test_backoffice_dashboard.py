from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient
from tests.identity_support import enable_confirmed_mfa

from apps.backoffice.domain import AccessScope, StaffRole
from apps.backoffice.models import StaffRoleEvent, StaffRoleGrant
from apps.construction.domain import BudgetStatus, ProjectStatus
from apps.construction.models import (
    BudgetCategory,
    ConstructionProject,
    CostRecord,
    ProjectBudget,
)
from apps.identity.models import User
from apps.marketplace.domain import EnquiryStatus, ListingStatus, MandateStatus
from apps.marketplace.models import Enquiry, Listing, ListingMandate
from apps.organizations.models import Organization
from apps.properties.domain import PropertyStatus
from apps.properties.models import Property
from apps.tenancy.domain import LeaseStatus
from apps.tenancy.models import Lease, RentAllocation, RentInvoice

pytestmark = pytest.mark.django_db

OVERVIEW = "/api/v1/backoffice/dashboard/overview/"
ROLE_ASSIGNMENTS = "/api/v1/backoffice/roles/assignments/"


def _organization(suffix: str) -> Organization:
    return Organization.objects.create(
        legal_name=f"Operations {suffix} Limited",
        display_name=f"Operations {suffix}",
        slug=f"operations-{suffix}",
        organization_type="agency",
    )


def _property(*, organization: Organization, creator: User, suffix: str) -> Property:
    return Property.objects.create(
        reference=f"COL-{suffix}",
        name=f"Managed property {suffix}",
        property_type="residential",
        property_subtype="apartment",
        status=PropertyStatus.ACTIVE,
        hierarchy_role="standalone",
        management_context="organization",
        managing_organization=organization,
        created_by=creator,
    )


def _listing(*, property: Property, organization: Organization, creator: User) -> Listing:
    now = timezone.now()
    mandate = ListingMandate.objects.create(
        property=property,
        principal_user=creator,
        representative_user=creator,
        granting_user=creator,
        listing_scope="sale",
        valid_from=date.today(),
        evidence_reference="Test mandate",
        status=MandateStatus.ACTIVE,
        self_declared=True,
        accepted_at=now,
    )
    return Listing.objects.create(
        reference=f"L-{property.reference}",
        property=property,
        listing_type="sale",
        status=ListingStatus.PUBLISHED,
        title=f"Listing {property.reference}",
        advertiser=creator,
        advertising_organization=organization,
        mandate=mandate,
        published_at=now,
        expires_at=now + timedelta(days=30),
    )


def _enquiry(*, listing: Listing, organization: Organization) -> Enquiry:
    return Enquiry.objects.create(
        listing=listing,
        advertiser=listing.advertiser,
        organization=organization,
        contact_name="Private contact",
        contact_email="private-contact@example.test",
        message="Private enquiry details",
        consent_at=timezone.now(),
        consent_version="test-v1",
        status=EnquiryStatus.NEW,
    )


def _client(user: User) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _grant(
    *,
    client: APIClient,
    user: User,
    role: str,
    organization: Organization | None,
    scope: str = AccessScope.ORGANIZATION,
):
    return client.post(
        ROLE_ASSIGNMENTS,
        {
            "user_id": str(user.id),
            "role": role,
            "scope": scope,
            "organization_id": str(organization.id) if organization else None,
        },
        format="json",
    )


def _widget(response, metric_id: str) -> dict:
    return next(widget for widget in response.data["widgets"] if widget["id"] == metric_id)


def test_scoped_sales_dashboard_returns_aggregates_without_cross_organization_or_pii():
    root = User.objects.create_superuser(
        email="root-dashboard@example.test",
        password="StrongPassword123!",
    )
    enable_confirmed_mfa(root)
    staff = User.objects.create_user(
        email="sales-dashboard@example.test",
        password="StrongPassword123!",
    )
    enable_confirmed_mfa(staff)
    organization_a = _organization("a")
    organization_b = _organization("b")
    property_a = _property(organization=organization_a, creator=root, suffix="A")
    property_b = _property(organization=organization_b, creator=root, suffix="B")
    listing_a = _listing(property=property_a, organization=organization_a, creator=root)
    listing_b = _listing(property=property_b, organization=organization_b, creator=root)
    _enquiry(listing=listing_a, organization=organization_a)
    _enquiry(listing=listing_b, organization=organization_b)

    granted = _grant(
        client=_client(root),
        user=staff,
        role=StaffRole.SALES_MANAGER,
        organization=organization_a,
    )
    assert granted.status_code == 201, granted.data

    response = _client(staff).get(OVERVIEW)

    assert response.status_code == 200, response.data
    assert response.data["scope"] == {"type": "organizations", "organization_count": 1}
    property_widget = _widget(response, "properties")
    assert property_widget["value"] == {"total": 1, "active": 1, "draft": 0, "archived": 0}
    assert property_widget["source"] == ["properties.Property"]
    assert property_widget["scope"] == response.data["scope"]
    assert property_widget["as_of"] is not None
    assert _widget(response, "marketplace_listings")["value"]["published"] == 1
    assert _widget(response, "marketplace_enquiries")["value"]["total"] == 1
    assert not {widget["id"] for widget in response.data["widgets"]} & {
        "rental_finance",
        "construction_costs",
    }

    response_text = response.content.decode()
    assert "private-contact@example.test" not in response_text
    assert "Private enquiry details" not in response_text
    assert Property.objects.filter(access_grants__user=staff).count() == 0


def test_role_grants_require_mfa_superadministrator_and_are_audited_on_revocation():
    root_without_mfa = User.objects.create_superuser(
        email="root-no-mfa@example.test",
        password="StrongPassword123!",
    )
    root_with_mfa = User.objects.create_superuser(
        email="root-with-mfa@example.test",
        password="StrongPassword123!",
    )
    enable_confirmed_mfa(root_with_mfa)
    staff = User.objects.create_user(
        email="role-target@example.test",
        password="StrongPassword123!",
    )
    enable_confirmed_mfa(staff)
    organization = _organization("roles")

    response = _grant(
        client=_client(root_without_mfa),
        user=staff,
        role=StaffRole.OPERATIONS_ADMIN,
        organization=organization,
    )
    assert response.status_code == 403
    response = _grant(
        client=_client(staff),
        user=staff,
        role=StaffRole.OPERATIONS_ADMIN,
        organization=organization,
    )
    assert response.status_code == 403

    response = _grant(
        client=_client(root_with_mfa),
        user=staff,
        role=StaffRole.OPERATIONS_ADMIN,
        organization=organization,
    )
    assert response.status_code == 201, response.data
    grant_id = response.data["id"]
    revoke = _client(root_with_mfa).post(f"{ROLE_ASSIGNMENTS}{grant_id}/revoke/")
    assert revoke.status_code == 200, revoke.data
    assert revoke.data["is_active"] is False
    assert StaffRoleEvent.objects.filter(grant_id=grant_id).count() == 2
    assert _client(staff).get(OVERVIEW).status_code == 403

    duplicate_revoke = _client(root_with_mfa).post(f"{ROLE_ASSIGNMENTS}{grant_id}/revoke/")
    assert duplicate_revoke.status_code == 409
    assert StaffRoleGrant.objects.get(pk=grant_id).revoked_by == root_with_mfa


def test_unassigned_and_mfa_unconfirmed_accounts_cannot_read_dashboard():
    organization = _organization("access")
    user = User.objects.create_user(
        email="no-dashboard-role@example.test",
        password="StrongPassword123!",
    )
    client = _client(user)
    assert client.get(OVERVIEW).status_code == 403

    user_with_role = User.objects.create_user(
        email="no-dashboard-mfa@example.test",
        password="StrongPassword123!",
    )
    root = User.objects.create_superuser(
        email="dashboard-provisioner@example.test",
        password="StrongPassword123!",
    )
    enable_confirmed_mfa(root)
    response = _grant(
        client=_client(root),
        user=user_with_role,
        role=StaffRole.SALES_MANAGER,
        organization=organization,
    )
    assert response.status_code == 201, response.data
    assert _client(user_with_role).get(OVERVIEW).status_code == 403


def test_each_widget_uses_the_organization_scope_of_its_own_role():
    root = User.objects.create_superuser(
        email="root-mixed-scope@example.test",
        password="StrongPassword123!",
    )
    enable_confirmed_mfa(root)
    staff = User.objects.create_user(
        email="mixed-scope@example.test",
        password="StrongPassword123!",
    )
    enable_confirmed_mfa(staff)
    sales_organization = _organization("mixed-sales")
    finance_organization = _organization("mixed-finance")
    sales_property = _property(
        organization=sales_organization,
        creator=root,
        suffix="MIX-S",
    )
    _property(
        organization=finance_organization,
        creator=root,
        suffix="MIX-F",
    )
    lease = Lease.objects.create(
        property=sales_property,
        property_owner=root,
        managing_organization=sales_organization,
        start_date=date.today() - timedelta(days=30),
        rent_amount_minor=1_000,
        status=LeaseStatus.ACTIVE,
        created_by=root,
    )
    RentInvoice.objects.create(
        lease=lease,
        due_date=date.today() - timedelta(days=1),
        total_minor=1_000,
        currency="KES",
        idempotency_key="mixed-scope-invoice",
        issued_by=root,
    )

    for role, organization in (
        (StaffRole.SALES_MANAGER, sales_organization),
        (StaffRole.FINANCE_ADMIN, finance_organization),
    ):
        response = _grant(
            client=_client(root),
            user=staff,
            role=role,
            organization=organization,
        )
        assert response.status_code == 201, response.data

    response = _client(staff).get(OVERVIEW)

    assert response.status_code == 200, response.data
    assert response.data["scope"] == {"type": "organizations", "organization_count": 2}
    assert _widget(response, "properties")["value"]["total"] == 1
    assert _widget(response, "rental_finance")["value"]["by_currency"] == []


def test_finance_dashboard_uses_rental_reversals_and_construction_ledger_by_currency():
    root = User.objects.create_superuser(
        email="finance-root@example.test",
        password="StrongPassword123!",
    )
    enable_confirmed_mfa(root)
    finance_user = User.objects.create_user(
        email="finance-dashboard@example.test",
        password="StrongPassword123!",
    )
    enable_confirmed_mfa(finance_user)
    organization = _organization("finance")
    managed_property = _property(organization=organization, creator=root, suffix="FIN")

    lease = Lease.objects.create(
        property=managed_property,
        property_owner=root,
        managing_organization=organization,
        start_date=date.today() - timedelta(days=30),
        rent_amount_minor=1_000,
        status=LeaseStatus.ACTIVE,
        created_by=root,
    )
    invoice = RentInvoice.objects.create(
        lease=lease,
        due_date=date.today() - timedelta(days=1),
        total_minor=1_000,
        currency="KES",
        idempotency_key="dashboard-invoice-one",
        issued_by=root,
    )
    allocation = RentAllocation.objects.create(
        invoice=invoice,
        amount_minor=400,
        allocated_on=date.today(),
        idempotency_key="dashboard-allocation-one",
        created_by=root,
    )
    RentAllocation.objects.create(
        invoice=invoice,
        amount_minor=100,
        allocated_on=date.today(),
        reversal_of=allocation,
        idempotency_key="dashboard-allocation-reversal-one",
        created_by=root,
    )

    project = ConstructionProject.objects.create(
        organization=organization,
        code="COL-001",
        name="Finance dashboard project",
        classification="commercial",
        status=ProjectStatus.ACTIVE,
        created_by=root,
    )
    budget = ProjectBudget.objects.create(
        project=project,
        name="Approved baseline",
        revision=1,
        currency="KES",
        status=BudgetStatus.DRAFT,
        total=Decimal("1000.00"),
        created_by=root,
    )
    category = BudgetCategory.objects.create(
        budget=budget,
        name="Materials",
        planned_amount=Decimal("1000.00"),
    )
    ProjectBudget.objects.filter(pk=budget.pk).update(
        status=BudgetStatus.APPROVED,
        approved_at=timezone.now(),
        approved_by=root,
    )
    CostRecord.objects.create(
        project=project,
        category=category,
        record_type="commitment",
        amount=Decimal("250.00"),
        reference="COL-PO-001",
        recorded_by=root,
    )
    CostRecord.objects.create(
        project=project,
        category=category,
        record_type="actual",
        amount=Decimal("125.50"),
        reference="COL-INV-001",
        recorded_by=root,
    )

    response = _grant(
        client=_client(root),
        user=finance_user,
        role=StaffRole.FINANCE_ADMIN,
        organization=organization,
    )
    assert response.status_code == 201, response.data

    response = _client(finance_user).get(OVERVIEW)

    assert response.status_code == 200, response.data
    rental = _widget(response, "rental_finance")
    assert rental["value"]["by_currency"] == [
        {
            "currency": "KES",
            "invoice_count": 1,
            "amount_demanded_minor": 1_000,
            "net_allocations_minor": 300,
            "overdue_invoice_count": 1,
            "overdue_balance_minor": 700,
        }
    ]
    assert "not proof of a cash receipt" in rental["value"]["interpretation"]
    construction = _widget(response, "construction_costs")
    assert construction["value"]["by_currency"] == [
        {
            "currency": "KES",
            "approved_budget": "1000.00",
            "committed_cost": "250.00",
            "actual_cost": "125.50",
            "forecast_input": "0",
        }
    ]
    assert rental["as_of"] is not None
    assert construction["as_of"] is not None
    assert {widget["id"] for widget in response.data["widgets"]} == {
        "rental_finance",
        "construction_costs",
    }
