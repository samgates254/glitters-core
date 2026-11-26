from datetime import date
from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from apps.construction.domain import (
    BoQStatus,
    ProjectRole,
    ProjectStatus,
)
from apps.construction.errors import ConstructionError
from apps.construction.models import (
    RFI,
    ApprovedVariation,
    BoQItem,
    BoQSection,
    ProjectMilestone,
    Tender,
    TenderBid,
)
from apps.construction.services import (
    add_project_member,
    approve_boq,
    award_tender,
    calculate_boq,
    close_defect,
    close_rfi,
    create_boq,
    create_inspection,
    create_project,
    record_defect,
    respond_rfi,
    revise_boq,
    set_milestone_dependencies,
    submit_and_approve_variation,
    submit_inspection,
    submit_rfi,
    transition_project,
)
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

    approved = approve_boq(actor=actor, boq=boq)
    assert approved.status == BoQStatus.APPROVED
    with pytest.raises(ConstructionError):
        calculate_boq(approved)
    revision = revise_boq(actor=actor, boq=approved)
    assert revision.revision == 2
    assert revision.sections.filter(code="A").exists()


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

    variation = ApprovedVariation.objects.create(
        project=project,
        reference="VAR-001",
        reason="Design change",
        scope_modification="Add canopy",
        cost_impact=Decimal("250000.00"),
        requested_by=actor,
    )
    approved = submit_and_approve_variation(actor=actor, variation=variation)
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
