from datetime import date
from decimal import Decimal
from io import BytesIO
from zipfile import ZipFile

import pytest
from django.test import override_settings
from rest_framework.test import APIClient

from apps.construction.domain import (
    BoQStatus,
    BudgetStatus,
    ProjectRole,
    ProjectStatus,
)
from apps.construction.errors import ConstructionError
from apps.construction.models import (
    RFI,
    BoQ,
    BoQItem,
    BoQSection,
    CostRecord,
    ProjectBudget,
    ProjectMilestone,
    Tender,
    TenderBid,
)
from apps.construction.services import (
    add_project_member,
    approve_boq,
    approve_budget,
    approve_site_diary,
    approve_variation,
    award_tender,
    boq_csv_export,
    calculate_boq,
    close_defect,
    close_engineering_issue,
    close_rfi,
    create_boq,
    create_boq_item,
    create_boq_section,
    create_budget_category,
    create_inspection,
    create_project,
    create_project_budget,
    create_site_diary,
    project_cost_report,
    project_cost_report_csv,
    record_defect,
    record_engineering_issue,
    record_project_cost,
    request_variation,
    respond_rfi,
    review_boq,
    review_variation,
    revise_boq,
    revise_budget,
    set_milestone_dependencies,
    submit_boq,
    submit_budget,
    submit_inspection,
    submit_rfi,
    transition_project,
)
from apps.construction.views import authorized_boq_or_404
from apps.identity.models import User
from apps.organizations.domain import MembershipSource, MembershipStatus, Role
from apps.organizations.models import Organization, OrganizationMembership


def make_actor(email="actor@example.com"):
    return User.objects.create_user(email=email, password="StrongPassword123!")


def make_org(actor, suffix="one"):
    org = Organization.objects.create(
        legal_name=f"Construction Co {suffix}",
        display_name=f"Construction Co {suffix}",
        slug=f"construction-co-{suffix}",
        organization_type="contractor",
    )
    OrganizationMembership.objects.create(
        organization=org,
        user=actor,
        role=Role.OWNER,
        status=MembershipStatus.ACTIVE,
        source=MembershipSource.CREATOR,
        joined_at=org.created_at,
    )
    return org


def add_org_member(org, user):
    return OrganizationMembership.objects.create(
        organization=org,
        user=user,
        role=Role.MEMBER,
        status=MembershipStatus.ACTIVE,
        source=MembershipSource.INVITATION,
        joined_at=org.created_at,
    )


@pytest.mark.django_db
def test_project_lifecycle_and_membership_are_authorized():
    actor = make_actor()
    org = make_org(actor)
    outsider = make_actor("outsider@example.com")

    project = create_project(
        actor=actor,
        organization=org,
        code="CP-001",
        name="Nairobi Test Block",
        classification="building",
        budget=Decimal("1000000.00"),
        project_manager=actor,
    )
    add_project_member(actor=actor, project=project, user=actor, role=ProjectRole.CIVIL_ENGINEER)

    assert project.status == ProjectStatus.DRAFT
    project = transition_project(actor=actor, project=project, target_status=ProjectStatus.PLANNED)
    project = transition_project(actor=actor, project=project, target_status=ProjectStatus.APPROVED)

    with pytest.raises(ConstructionError):
        transition_project(actor=outsider, project=project, target_status=ProjectStatus.ACTIVE)

    project = transition_project(actor=actor, project=project, target_status=ProjectStatus.ACTIVE)
    assert project.status == ProjectStatus.ACTIVE


@pytest.mark.django_db
def test_inspection_defect_workflow():
    actor = make_actor("engineer@example.com")
    org = make_org(actor, "inspection")
    project = create_project(
        actor=actor,
        organization=org,
        code="CP-002",
        name="Road Works",
        classification="civil",
        project_manager=actor,
    )
    add_project_member(actor=actor, project=project, user=actor, role=ProjectRole.CIVIL_ENGINEER)
    inspection = create_inspection(
        actor=actor,
        project=project,
        title="Foundation inspection",
        inspector=actor,
        inspection_date=date.today(),
    )
    submit_inspection(actor=actor, inspection=inspection)
    defect = record_defect(
        actor=actor,
        inspection=inspection,
        reference="DEF-001",
        description="Concrete honeycomb",
        rectification_order="Patch and retest",
        assigned_to=actor,
    )
    defect = close_defect(actor=actor, defect=defect)

    assert inspection.status == "submitted"
    assert defect.status == "closed"


@pytest.mark.django_db
def test_boq_calculation_revision_and_lock():
    actor = make_actor("qs@example.com")
    org = make_org(actor, "quantity")
    project = create_project(
        actor=actor,
        organization=org,
        code="CP-003",
        name="Office Fitout",
        classification="interior",
        project_manager=actor,
    )
    add_project_member(actor=actor, project=project, user=actor, role=ProjectRole.QUANTITY_SURVEYOR)
    boq = create_boq(
        actor=actor,
        project=project,
        title="Main BoQ",
        tax_percent=Decimal("16"),
        contingency_percent=Decimal("5"),
    )
    section = BoQSection.objects.create(boq=boq, code="A", title="Preliminaries")
    BoQItem.objects.create(
        section=section,
        item_code="A1",
        description="Concrete",
        unit="m3",
        quantity=Decimal("10.0000"),
        unit_rate=Decimal("123.4567"),
    )
    calculate_boq(boq)

    boq.refresh_from_db()
    assert boq.subtotal == Decimal("1234.57")
    assert boq.contingency_amount == Decimal("61.73")
    assert boq.tax_amount == Decimal("207.41")
    assert boq.grand_total == Decimal("1503.71")

    reviewer = make_actor("qs-reviewer@example.com")
    approver = make_actor("qs-approver@example.com")
    add_org_member(org, reviewer)
    add_org_member(org, approver)
    add_project_member(
        actor=actor, project=project, user=reviewer, role=ProjectRole.PROJECT_MANAGER
    )
    add_project_member(
        actor=actor, project=project, user=approver, role=ProjectRole.PROJECT_MANAGER
    )
    submitted = submit_boq(actor=actor, boq=boq)
    reviewed = review_boq(actor=reviewer, boq=submitted)
    approved = approve_boq(actor=approver, boq=reviewed)
    assert approved.status == BoQStatus.APPROVED
    with pytest.raises(ConstructionError):
        calculate_boq(approved)
    with pytest.raises(ValueError):
        BoQItem.objects.filter(section=section).update(unit_rate=Decimal("1"))
    with pytest.raises(ValueError):
        BoQ.objects.filter(pk=approved.pk).update(title="Tampered")
    revision = revise_boq(actor=actor, boq=approved)
    assert revision.revision == 2
    assert revision.sections.filter(code="A").exists()
    copied_item = BoQItem.objects.get(section__boq=revision)
    assert copied_item.quantity == Decimal("10.0000")
    assert copied_item.unit_rate == Decimal("123.4567")
    assert BoQ.objects.get(pk=approved.pk).status == BoQStatus.SUPERSEDED


@pytest.mark.django_db
def test_rfi_lifecycle_and_api_cross_tenant_isolation():
    actor = make_actor("pm@example.com")
    org = make_org(actor, "rfi")
    project = create_project(
        actor=actor,
        organization=org,
        code="CP-004",
        name="Warehouse",
        classification="industrial",
        project_manager=actor,
    )
    rfi = RFI.objects.create(
        project=project,
        reference="RFI-001",
        requesting_party=actor,
        responsible_recipient=actor,
        subject="Rebar spacing",
        question="Confirm spacing",
        due_date=date.today(),
    )
    submit_rfi(actor=actor, rfi=rfi)
    respond_rfi(actor=actor, rfi=rfi, response="Use 150 mm")
    close_rfi(actor=actor, rfi=rfi)
    assert rfi.status == "closed"

    outsider = make_actor("foreign@example.com")
    make_org(outsider, "foreign")
    client = APIClient()
    client.force_authenticate(user=outsider)
    response = client.get(f"/api/v1/construction/projects/{project.id}/")
    assert response.status_code in (404, 403)


@pytest.mark.django_db
def test_site_diary_and_engineering_issue_workflow():
    actor = make_actor("site-engineer@example.com")
    org = make_org(actor, "site")
    project = create_project(
        actor=actor,
        organization=org,
        code="CP-006",
        name="Kisumu Bridge",
        classification="civil",
        project_manager=actor,
    )
    diary = create_site_diary(
        actor=actor,
        project=project,
        diary_date=date.today(),
        work_completed="Pile cap preparation",
        weather_and_conditions="Dry",
        safety_observations="PPE compliance observed",
    )
    approved = approve_site_diary(actor=actor, diary=diary)
    assert approved.approved_at is not None

    issue = record_engineering_issue(
        actor=actor,
        project=project,
        reference="ISS-001",
        category="quality",
        severity="high",
        description="Foundation reinforcement spacing requires review",
        responsible_party=actor,
        resolution_deadline=date.today(),
    )
    closed = close_engineering_issue(
        actor=actor,
        issue=issue,
        resolution_notes="Reviewed and corrected before pour",
    )
    assert closed.status == "closed"


@pytest.mark.django_db
def test_variation_tender_and_milestone_controls():
    actor = make_actor("manager@example.com")
    org = make_org(actor, "controls")
    project = create_project(
        actor=actor,
        organization=org,
        code="CP-005",
        name="Control Site",
        classification="building",
        project_manager=actor,
    )

    qs = make_actor("variation-qs@example.com")
    approver = make_actor("variation-approver@example.com")
    add_org_member(org, qs)
    add_org_member(org, approver)
    add_project_member(actor=actor, project=project, user=qs, role=ProjectRole.QUANTITY_SURVEYOR)
    add_project_member(
        actor=actor, project=project, user=approver, role=ProjectRole.PROJECT_MANAGER
    )
    variation = request_variation(
        actor=qs,
        project=project,
        reference="VAR-001",
        reason="Design change",
        scope_modification="Add canopy",
        cost_impact=Decimal("250000.00"),
    )
    reviewed = review_variation(actor=actor, variation=variation)
    approved = approve_variation(actor=approver, variation=reviewed)
    assert approved.status == "approved"

    contractor = make_org(make_actor("contractor@example.com"), "bidder")
    tender = Tender.objects.create(
        project=project,
        title="Canopy package",
        scope="Supply and install",
        status="invited",
        created_by=actor,
    )
    bid_actor = make_actor("bid@example.com")
    TenderBid.objects.create(
        tender=tender,
        bidder=contractor,
        amount=Decimal("200000.00"),
        submitted_by=bid_actor,
    )
    awarded = award_tender(actor=actor, tender=tender, contractor=contractor)
    assert awarded.status == "awarded"

    first = ProjectMilestone.objects.create(
        project=project,
        name="Foundation",
        planned_start=date.today(),
        planned_end=date.today(),
    )
    second = ProjectMilestone.objects.create(
        project=project,
        name="Structure",
        planned_start=date.today(),
        planned_end=date.today(),
    )
    set_milestone_dependencies(actor=actor, milestone=second, dependent_ids={str(first.id)})
    assert second.dependencies.filter(id=first.id).exists()


@pytest.mark.django_db
def test_boq_submission_review_separation_and_negative_values():
    owner = make_actor("boq-owner@example.com")
    org = make_org(owner, "boq-flow")
    qs = make_actor("boq-qs@example.com")
    reviewer = make_actor("boq-reviewer@example.com")
    approver = make_actor("boq-approver@example.com")
    for member in (qs, reviewer, approver):
        add_org_member(org, member)
    project = create_project(
        actor=owner,
        organization=org,
        code="CP-BOQ-01",
        name="Commercial Building",
        classification="commercial",
        project_manager=owner,
    )
    add_project_member(actor=owner, project=project, user=qs, role=ProjectRole.QUANTITY_SURVEYOR)
    add_project_member(
        actor=owner, project=project, user=reviewer, role=ProjectRole.PROJECT_MANAGER
    )
    add_project_member(
        actor=owner, project=project, user=approver, role=ProjectRole.PROJECT_MANAGER
    )
    boq = create_boq(actor=qs, project=project, title="Measured Works", reference="QS-01")
    section = create_boq_section(actor=qs, boq=boq, code="A", title="Concrete")
    with pytest.raises(ConstructionError):
        create_boq_item(
            actor=qs,
            section=section,
            item_code="A1",
            description="Concrete",
            unit="m3",
            quantity=Decimal("-1"),
            unit_rate=Decimal("100"),
        )
    item = create_boq_item(
        actor=qs,
        section=section,
        item_code="A1",
        description="Concrete",
        unit="m3",
        quantity=Decimal("2.3456"),
        unit_rate=Decimal("100.0050"),
    )
    assert item.amount == Decimal("234.57")
    submitted = submit_boq(actor=qs, boq=boq)
    with pytest.raises(ConstructionError):
        create_boq_item(
            actor=qs,
            section=section,
            item_code="A2",
            description="Late edit",
            unit="m3",
            quantity=Decimal("1"),
            unit_rate=Decimal("1"),
        )
    reviewed = review_boq(actor=reviewer, boq=submitted)
    with pytest.raises(ConstructionError):
        approve_boq(actor=reviewer, boq=reviewed)
    approved = approve_boq(actor=approver, boq=reviewed)
    assert approved.status == BoQStatus.APPROVED


@pytest.mark.django_db
def test_boq_api_tenant_validation_and_exports():
    owner = make_actor("api-boq-owner@example.com")
    org = make_org(owner, "boq-api")
    qs = make_actor("api-boq-qs@example.com")
    add_org_member(org, qs)
    project = create_project(
        actor=owner,
        organization=org,
        code="CP-BOQ-API",
        name="API Project",
        classification="civil",
        project_manager=owner,
    )
    add_project_member(actor=owner, project=project, user=qs, role=ProjectRole.QUANTITY_SURVEYOR)
    client = APIClient()
    client.force_authenticate(user=qs)
    with override_settings(ROOT_URLCONF="apps.construction.test_urls"):
        response = client.post(
            f"/api/v1/construction/projects/{project.id}/boqs/",
            {"title": "API BoQ", "reference": "API-BOQ", "currency": "KES"},
            format="json",
        )
        assert response.status_code == 201, response.data
        boq_id = response.data["id"]
        response = client.post(
            f"/api/v1/construction/projects/{project.id}/boqs/{boq_id}/sections/",
            {"code": "A", "title": "Earthworks", "sequence": 1},
            format="json",
        )
        assert response.status_code == 201, response.data
        section_id = response.data["id"]
        response = client.post(
            f"/api/v1/construction/projects/{project.id}/boqs/{boq_id}/sections/{section_id}/items/",
            {
                "item_code": "A1",
                "description": "Excavation",
                "unit": "m3",
                "quantity": "-2",
                "unit_rate": "15.00",
            },
            format="json",
        )
        assert response.status_code == 400
        response = client.post(
            f"/api/v1/construction/projects/{project.id}/boqs/{boq_id}/sections/{section_id}/items/",
            {
                "item_code": "A1",
                "description": "Excavation",
                "unit": "m3",
                "quantity": "2",
                "unit_rate": "15.00",
            },
            format="json",
        )
        assert response.status_code == 201, response.data
        boq = BoQ.objects.get(pk=boq_id)
        assert boq.grand_total == Decimal("30.00")
        assert BoQ.objects.filter(pk=boq_id, project=project).exists()
        assert authorized_boq_or_404(qs, project.id, boq_id).id == boq.id
        assert "Excavation" in boq_csv_export(boq)
        csv_response = client.get(
            f"/api/v1/construction/projects/{project.id}/boqs/{boq_id}/export/?output=csv"
        )
        xlsx_response = client.get(
            f"/api/v1/construction/projects/{project.id}/boqs/{boq_id}/export/?output=xlsx"
        )
        assert csv_response.status_code == 200, csv_response.data
        assert "Excavation" in csv_response.content.decode()
        assert xlsx_response.status_code == 200, xlsx_response.data
        with ZipFile(BytesIO(xlsx_response.content)) as archive:
            assert "xl/worksheets/sheet1.xml" in archive.namelist()
        pdf_response = client.get(
            f"/api/v1/construction/projects/{project.id}/boqs/{boq_id}/export/?output=pdf"
        )
        assert pdf_response.status_code == 406
        outsider = make_actor("api-boq-outsider@example.com")
        make_org(outsider, "boq-outsider")
        client.force_authenticate(user=outsider)
        response = client.get(f"/api/v1/construction/projects/{project.id}/boqs/{boq_id}/sections/")
        assert response.status_code == 404


@pytest.mark.django_db
def test_budget_approval_cost_ledger_and_variation_workflow():
    owner = make_actor("budget-owner@example.com")
    org = make_org(owner, "budget-flow")
    qs = make_actor("budget-qs@example.com")
    reviewer = make_actor("budget-reviewer@example.com")
    approver = make_actor("budget-approver@example.com")
    for member in (qs, reviewer, approver):
        add_org_member(org, member)
    project = create_project(
        actor=owner,
        organization=org,
        code="CP-BUDGET-01",
        name="Budget Project",
        classification="building",
        project_manager=owner,
    )
    add_project_member(actor=owner, project=project, user=qs, role=ProjectRole.QUANTITY_SURVEYOR)
    add_project_member(
        actor=owner, project=project, user=reviewer, role=ProjectRole.PROJECT_MANAGER
    )
    add_project_member(
        actor=owner, project=project, user=approver, role=ProjectRole.PROJECT_MANAGER
    )
    budget = create_project_budget(actor=qs, project=project, name="Base cost plan")
    category = create_budget_category(
        actor=qs, budget=budget, name="Earthworks", planned_amount=Decimal("1000.005")
    )
    assert category.planned_amount == Decimal("1000.01")
    submitted = submit_budget(actor=qs, budget=budget)
    with pytest.raises(ConstructionError):
        approve_budget(actor=qs, budget=submitted)
    approved_budget = approve_budget(actor=owner, budget=submitted)
    assert approved_budget.total == Decimal("1000.01")
    project.refresh_from_db()
    assert project.approved_budget == Decimal("1000.01")
    commitment = record_project_cost(
        actor=owner,
        category=category,
        record_type="commitment",
        amount=Decimal("350.005"),
        reference="PO-100",
    )
    actual = record_project_cost(
        actor=qs,
        category=category,
        record_type="actual",
        amount=Decimal("125.50"),
        reference="INV-100",
    )
    assert commitment.amount == Decimal("350.01")
    assert actual.amount == Decimal("125.50")
    assert project_cost_report(project)["committed_costs"] == "350.01"
    assert project_cost_report(project)["actual_costs"] == "125.50"
    category.refresh_from_db()
    assert category.committed_amount == Decimal("350.01")
    assert category.actual_amount == Decimal("125.50")
    assert "Earthworks" in project_cost_report_csv(project)
    with pytest.raises(ConstructionError):
        record_project_cost(
            actor=qs,
            category=category,
            record_type="actual",
            amount=Decimal("-1"),
            reference="INV-BAD",
        )
    with pytest.raises(ValueError):
        CostRecord.objects.filter(pk=actual.pk).update(amount=Decimal("0"))
    variation = request_variation(
        actor=qs,
        project=project,
        reference="VAR-COST-1",
        reason="Changed site levels",
        scope_modification="Additional fill",
        cost_impact=Decimal("240.00"),
        budget_category=category,
    )
    reviewed = review_variation(actor=reviewer, variation=variation)
    approved_variation = approve_variation(actor=approver, variation=reviewed)
    assert approved_variation.status == "approved"
    assert project_cost_report(project)["approved_variations"] == "240.00"
    revision = revise_budget(actor=qs, budget=approved_budget)
    assert revision.revision == 2
    assert revision.total == Decimal("1000.01")
    assert ProjectBudget.objects.get(pk=approved_budget.pk).status == BudgetStatus.APPROVED
    revision = submit_budget(actor=qs, budget=revision)
    revision = approve_budget(actor=owner, budget=revision)
    assert revision.status == BudgetStatus.APPROVED
    assert ProjectBudget.objects.get(pk=approved_budget.pk).status == BudgetStatus.SUPERSEDED


@pytest.mark.django_db
def test_budget_api_approval_and_cost_report():
    owner = make_actor("budget-api-owner@example.com")
    org = make_org(owner, "budget-api")
    qs = make_actor("budget-api-qs@example.com")
    add_org_member(org, qs)
    project = create_project(
        actor=owner,
        organization=org,
        code="CP-BUDGET-API",
        name="Budget API project",
        classification="commercial",
        project_manager=owner,
    )
    add_project_member(actor=owner, project=project, user=qs, role=ProjectRole.QUANTITY_SURVEYOR)
    qs_client = APIClient()
    qs_client.force_authenticate(user=qs)
    owner_client = APIClient()
    owner_client.force_authenticate(user=owner)
    with override_settings(ROOT_URLCONF="apps.construction.test_urls"):
        response = qs_client.post(
            f"/api/v1/construction/projects/{project.id}/budgets/",
            {"name": "Cost plan", "currency": "KES"},
            format="json",
        )
        assert response.status_code == 201, response.data
        budget_id = response.data["id"]
        response = qs_client.post(
            f"/api/v1/construction/projects/{project.id}/budgets/{budget_id}/categories/",
            {"name": "Materials", "planned_amount": "500.00"},
            format="json",
        )
        assert response.status_code == 201, response.data
        category_id = response.data["id"]
        response = qs_client.post(
            f"/api/v1/construction/projects/{project.id}/budgets/{budget_id}/submit/",
            {},
            format="json",
        )
        assert response.status_code == 200, response.data
        response = qs_client.post(
            f"/api/v1/construction/projects/{project.id}/budgets/{budget_id}/approve/",
            {},
            format="json",
        )
        assert response.status_code == 403
        response = owner_client.post(
            f"/api/v1/construction/projects/{project.id}/budgets/{budget_id}/approve/",
            {},
            format="json",
        )
        assert response.status_code == 200, response.data
        response = qs_client.post(
            f"/api/v1/construction/projects/{project.id}/budgets/{budget_id}/costs/",
            {
                "category": category_id,
                "record_type": "actual",
                "amount": "125.25",
                "reference": "AP-INV-01",
            },
            format="json",
        )
        assert response.status_code == 201, response.data
        response = qs_client.get(
            f"/api/v1/construction/projects/{project.id}/cost-report/?output=json"
        )
        assert response.status_code == 200
        assert response.data["actual_costs"] == "125.25"
        outsider = make_actor("budget-api-outsider@example.com")
        make_org(outsider, "budget-api-outsider")
        qs_client.force_authenticate(user=outsider)
        response = qs_client.get(
            f"/api/v1/construction/projects/{project.id}/budgets/{budget_id}/categories/"
        )
        assert response.status_code == 404
