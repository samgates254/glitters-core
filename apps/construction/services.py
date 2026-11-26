from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.utils import timezone

from apps.construction.domain import (
    MONEY_QUANTUM,
    PROJECT_TRANSITIONS,
    BoQStatus,
    DefectStatus,
    InspectionStatus,
    ProjectRole,
    ProjectStatus,
    RFIStatus,
    TenderStatus,
    VariationStatus,
)
from apps.construction.errors import ConstructionError
from apps.construction.models import (
    AuditEvent,
    BoQ,
    BoQItem,
    BoQSection,
    ConstructionProject,
    Defect,
    EngineeringInspection,
    ProjectMember,
    ProjectMilestone,
    TenderBid,
)
from apps.construction.permissions import PROJECT_MANAGEMENT_ROLES, has_org_access, has_project_role


def _require_org(user, organization):
    if not has_org_access(user, organization):
        raise ConstructionError("You do not have access to this organization.", status_code=403)


def _require_project_role(user, project, roles):
    if not has_project_role(user, project, roles):
        raise ConstructionError(
            "You do not have permission for this project action.", status_code=403
        )


def audit(project, actor, action, entity_type, entity_id=None, **metadata):
    AuditEvent.objects.create(
        project=project,
        actor=actor,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        metadata=metadata,
    )


@transaction.atomic
def create_project(
    *,
    actor,
    organization,
    code,
    name,
    classification,
    description="",
    property=None,
    budget=Decimal("0"),
    start_date=None,
    expected_completion_date=None,
    project_manager=None,
):
    _require_org(actor, organization)
    if budget < 0:
        raise ConstructionError("Budget cannot be negative.")
    project = ConstructionProject.objects.create(
        organization=organization,
        property=property,
        code=code.strip().upper(),
        name=name,
        classification=classification,
        description=description,
        budget=budget,
        start_date=start_date,
        expected_completion_date=expected_completion_date,
        project_manager=project_manager,
        created_by=actor,
    )
    audit(project, actor, "project.created", "project", project.id)
    return project


@transaction.atomic
def transition_project(*, actor, project, target_status):
    project = ConstructionProject.objects.select_for_update().get(pk=project.pk)
    _require_project_role(actor, project, PROJECT_MANAGEMENT_ROLES)
    allowed = PROJECT_TRANSITIONS.get(project.status, frozenset())
    if target_status not in allowed:
        raise ConstructionError(
            f"Project transition {project.status} -> {target_status} is not allowed."
        )
    if (
        target_status in (ProjectStatus.APPROVED, ProjectStatus.COMPLETED)
        and not project.project_manager
    ):
        raise ConstructionError("A project manager is required before approval or completion.")
    previous_status = project.status
    project.status = target_status
    project.version += 1
    project.save(update_fields=["status", "version", "updated_at"])
    audit(
        project,
        actor,
        "project.status_changed",
        "project",
        project.id,
        from_status=previous_status,
        to_status=target_status,
    )
    return project


@transaction.atomic
def add_project_member(*, actor, project, user, role):
    _require_project_role(actor, project, PROJECT_MANAGEMENT_ROLES)
    if role not in ProjectRole.VALUES:
        raise ConstructionError("Unknown construction project role.")
    member = ProjectMember.objects.create(project=project, user=user, role=role, added_by=actor)
    audit(project, actor, "project_member.added", "project_member", member.id, role=role)
    return member


@transaction.atomic
def create_inspection(
    *, actor, project, title, inspector, inspection_date, observations="", safety_observations=""
):
    _require_project_role(
        actor,
        project,
        {ProjectRole.PROJECT_MANAGER, ProjectRole.CIVIL_ENGINEER, ProjectRole.SITE_INSPECTOR},
    )
    inspection = EngineeringInspection.objects.create(
        project=project,
        title=title,
        inspector=inspector,
        inspection_date=inspection_date,
        observations=observations,
        safety_observations=safety_observations,
    )
    audit(project, actor, "inspection.created", "inspection", inspection.id)
    return inspection


@transaction.atomic
def submit_inspection(*, actor, inspection):
    project = inspection.project
    _require_project_role(
        actor,
        project,
        {ProjectRole.CIVIL_ENGINEER, ProjectRole.SITE_INSPECTOR, ProjectRole.PROJECT_MANAGER},
    )
    if inspection.status != InspectionStatus.DRAFT:
        raise ConstructionError("Only draft inspections can be submitted.")
    inspection.status = InspectionStatus.SUBMITTED
    inspection.submitted_at = timezone.now()
    inspection.save(update_fields=["status", "submitted_at"])
    audit(project, actor, "inspection.submitted", "inspection", inspection.id)
    return inspection


@transaction.atomic
def record_defect(
    *, actor, inspection, reference, description, rectification_order="", assigned_to=None
):
    project = inspection.project
    _require_project_role(
        actor,
        project,
        {ProjectRole.CIVIL_ENGINEER, ProjectRole.SITE_INSPECTOR, ProjectRole.PROJECT_MANAGER},
    )
    defect = Defect.objects.create(
        inspection=inspection,
        reference=reference,
        description=description,
        rectification_order=rectification_order,
        assigned_to=assigned_to,
        status=DefectStatus.ASSIGNED if assigned_to else DefectStatus.OPEN,
    )
    audit(project, actor, "defect.recorded", "defect", defect.id)
    return defect


@transaction.atomic
def close_defect(*, actor, defect):
    project = defect.inspection.project
    _require_project_role(
        actor,
        project,
        {ProjectRole.CIVIL_ENGINEER, ProjectRole.SITE_INSPECTOR, ProjectRole.PROJECT_MANAGER},
    )
    if defect.status not in (DefectStatus.RECTIFIED, DefectStatus.ASSIGNED, DefectStatus.OPEN):
        raise ConstructionError("Defect cannot be closed in its current state.")
    defect.status = DefectStatus.CLOSED
    defect.closed_by = actor
    defect.closed_at = timezone.now()
    defect.save(update_fields=["status", "closed_by", "closed_at"])
    audit(project, actor, "defect.closed", "defect", defect.id)
    return defect


def calculate_boq(boq):
    if boq.status == BoQStatus.APPROVED:
        raise ConstructionError("Approved BoQs are locked.")
    subtotal = Decimal("0")
    for item in BoQItem.objects.filter(section__boq=boq).select_related("section").iterator():
        amount = (item.quantity * item.unit_rate).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
        if item.amount != amount:
            item.amount = amount
            item.save(update_fields=["amount"])
        subtotal += amount
    subtotal = subtotal.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
    contingency = (subtotal * boq.contingency_percent / Decimal("100")).quantize(
        MONEY_QUANTUM, rounding=ROUND_HALF_UP
    )
    taxable = subtotal + contingency
    tax = (taxable * boq.tax_percent / Decimal("100")).quantize(
        MONEY_QUANTUM, rounding=ROUND_HALF_UP
    )
    grand = (taxable + tax).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
    boq.subtotal = subtotal
    boq.contingency_amount = contingency
    boq.tax_amount = tax
    boq.grand_total = grand
    boq.save(update_fields=["subtotal", "contingency_amount", "tax_amount", "grand_total"])
    return subtotal, contingency, tax, grand


@transaction.atomic
def create_boq(
    *, actor, project, title, tax_percent=Decimal("0"), contingency_percent=Decimal("0")
):
    _require_project_role(
        actor, project, {ProjectRole.PROJECT_MANAGER, ProjectRole.QUANTITY_SURVEYOR}
    )
    if tax_percent < 0 or contingency_percent < 0:
        raise ConstructionError("Tax and contingency percentages cannot be negative.")
    boq = BoQ.objects.create(
        project=project,
        title=title,
        tax_percent=tax_percent,
        contingency_percent=contingency_percent,
        created_by=actor,
    )
    audit(project, actor, "boq.created", "boq", boq.id)
    return boq


@transaction.atomic
def approve_boq(*, actor, boq):
    project = boq.project
    _require_project_role(actor, project, PROJECT_MANAGEMENT_ROLES)
    if boq.status == BoQStatus.APPROVED:
        raise ConstructionError("BoQ is already approved.")
    calculate_boq(boq)
    boq.status = BoQStatus.APPROVED
    boq.approved_at = timezone.now()
    boq.approved_by = actor
    boq.save(update_fields=["status", "approved_at", "approved_by"])
    audit(project, actor, "boq.approved", "boq", boq.id, revision=boq.revision)
    return boq


@transaction.atomic
def revise_boq(*, actor, boq):
    if boq.status != BoQStatus.APPROVED:
        raise ConstructionError("Only an approved BoQ can be revised.")
    _require_project_role(
        actor, boq.project, {ProjectRole.PROJECT_MANAGER, ProjectRole.QUANTITY_SURVEYOR}
    )
    boq.status = BoQStatus.SUPERSEDED
    boq.save(update_fields=["status"])
    revision = BoQ.objects.create(
        project=boq.project,
        title=boq.title,
        revision=boq.revision + 1,
        currency=boq.currency,
        tax_percent=boq.tax_percent,
        contingency_percent=boq.contingency_percent,
        created_by=actor,
    )
    for source_section in boq.sections.all():
        section = BoQSection.objects.create(
            boq=revision,
            code=source_section.code,
            title=source_section.title,
            sequence=source_section.sequence,
        )
        for source_item in source_section.items.all():
            BoQItem.objects.create(
                section=section,
                item_code=source_item.item_code,
                description=source_item.description,
                unit=source_item.unit,
                quantity=source_item.quantity,
                unit_rate=source_item.unit_rate,
                amount=source_item.amount,
            )
    audit(
        boq.project, actor, "boq.revision_created", "boq", revision.id, source_revision=boq.revision
    )
    return revision


@transaction.atomic
def submit_and_approve_variation(*, actor, variation):
    project = variation.project
    if variation.status == VariationStatus.APPROVED:
        raise ConstructionError("Variation is already approved.")
    _require_project_role(actor, project, PROJECT_MANAGEMENT_ROLES)
    if variation.status not in (VariationStatus.REQUESTED, VariationStatus.REVIEW):
        raise ConstructionError("Variation cannot be approved in its current state.")
    variation.status = VariationStatus.APPROVED
    variation.approved_by = actor
    variation.approved_at = timezone.now()
    variation.save(update_fields=["status", "approved_by", "approved_at"])
    audit(
        project,
        actor,
        "variation.approved",
        "variation",
        variation.id,
        cost_impact=str(variation.cost_impact),
    )
    return variation


def validate_milestone_dependencies(project, milestone, dependent_ids):
    candidate_id = str(milestone.id)
    if candidate_id in dependent_ids:
        raise ConstructionError("A milestone cannot depend on itself.")
    ids = set(dependent_ids)
    milestones = {
        str(value.id): value for value in ProjectMilestone.objects.filter(project=project)
    }
    if not ids.issubset(milestones):
        raise ConstructionError("Dependencies must belong to the same project.")
    for dependency_id in ids:
        seen = set()
        stack = [dependency_id]
        while stack:
            node_id = stack.pop()
            if node_id == candidate_id:
                raise ConstructionError("Milestone dependencies contain a cycle.")
            if node_id in seen:
                continue
            seen.add(node_id)
            node = milestones.get(node_id)
            if node:
                stack.extend(
                    str(dep_id) for dep_id in node.dependencies.values_list("id", flat=True)
                )
    return


@transaction.atomic
def set_milestone_dependencies(*, actor, milestone, dependent_ids):
    _require_project_role(actor, milestone.project, PROJECT_MANAGEMENT_ROLES)
    validate_milestone_dependencies(milestone.project, milestone, dependent_ids)
    dependencies = list(
        ProjectMilestone.objects.filter(project=milestone.project, id__in=dependent_ids)
    )
    milestone.dependencies.set(dependencies)
    audit(milestone.project, actor, "milestone.dependencies_changed", "milestone", milestone.id)
    return milestone


@transaction.atomic
def update_milestone_progress(*, actor, milestone, progress_percent, blockers=""):
    _require_project_role(
        actor, milestone.project, {ProjectRole.PROJECT_MANAGER, ProjectRole.CONTRACTOR}
    )
    if progress_percent < 0 or progress_percent > 100:
        raise ConstructionError("Progress percent must be between 0 and 100.")
    milestone.progress_percent = Decimal(str(progress_percent)).quantize(MONEY_QUANTUM)
    milestone.blockers = blockers
    if progress_percent == 100:
        milestone.completed_confirmed_by = actor
        if milestone.actual_end is None:
            milestone.actual_end = timezone.now().date()
    milestone.save(
        update_fields=["progress_percent", "blockers", "completed_confirmed_by", "actual_end"]
    )
    audit(
        milestone.project,
        actor,
        "milestone.progress_updated",
        "milestone",
        milestone.id,
        progress=str(milestone.progress_percent),
    )
    return milestone


@transaction.atomic
def submit_rfi(*, actor, rfi):
    project = rfi.project
    _require_project_role(
        actor,
        project,
        {
            ProjectRole.PROJECT_MANAGER,
            ProjectRole.CIVIL_ENGINEER,
            ProjectRole.ARCHITECT,
            ProjectRole.QUANTITY_SURVEYOR,
        },
    )
    if rfi.status != RFIStatus.DRAFT:
        raise ConstructionError("Only draft RFIs can be submitted.")
    rfi.status = RFIStatus.SUBMITTED
    rfi.submitted_at = timezone.now()
    rfi.save(update_fields=["status", "submitted_at"])
    audit(project, actor, "rfi.submitted", "rfi", rfi.id)
    return rfi


@transaction.atomic
def respond_rfi(*, actor, rfi, response):
    project = rfi.project
    if rfi.responsible_recipient_id != actor.id:
        _require_project_role(actor, project, PROJECT_MANAGEMENT_ROLES)
    if rfi.status == RFIStatus.CLOSED:
        raise ConstructionError("Closed RFIs cannot be changed.")
    rfi.status = RFIStatus.RESPONDING
    rfi.response = response
    rfi.save(update_fields=["status", "response"])
    audit(project, actor, "rfi.responded", "rfi", rfi.id)
    return rfi


@transaction.atomic
def close_rfi(*, actor, rfi):
    project = rfi.project
    _require_project_role(actor, project, PROJECT_MANAGEMENT_ROLES)
    rfi.status = RFIStatus.CLOSED
    rfi.closed_at = timezone.now()
    rfi.save(update_fields=["status", "closed_at"])
    audit(project, actor, "rfi.closed", "rfi", rfi.id)
    return rfi


@transaction.atomic
def award_tender(*, actor, tender, contractor):
    project = tender.project
    _require_project_role(actor, project, PROJECT_MANAGEMENT_ROLES)
    if tender.status not in (TenderStatus.INVITED, TenderStatus.CLOSED):
        raise ConstructionError("Tender cannot be awarded in its current state.")
    if not TenderBid.objects.filter(tender=tender, bidder=contractor).exists():
        raise ConstructionError("Only a bidder can receive the award.")
    tender.status = TenderStatus.AWARDED
    tender.awarded_contractor = contractor
    tender.save(update_fields=["status", "awarded_contractor"])
    audit(project, actor, "tender.awarded", "tender", tender.id, contractor=str(contractor.id))
    return tender


def project_report(project):
    return {
        "project": {
            "id": str(project.id),
            "code": project.code,
            "name": project.name,
            "status": project.status,
            "budget": str(project.budget),
        },
        "milestones": [
            {
                "id": str(item.id),
                "name": item.name,
                "progress_percent": str(item.progress_percent),
                "blockers": item.blockers,
            }
            for item in project.milestones.all()
        ],
        "inspections": [
            {
                "id": str(item.id),
                "title": item.title,
                "status": item.status,
                "inspection_date": item.inspection_date.isoformat(),
                "open_defects": item.defects.exclude(status=DefectStatus.CLOSED).count(),
            }
            for item in project.inspections.all()
        ],
        "boq_total": str(
            max(
                (boq.grand_total for boq in project.boqs.filter(status=BoQStatus.APPROVED)),
                default=Decimal("0"),
            )
        ),
        "variations": [
            {
                "id": str(item.id),
                "reference": item.reference,
                "status": item.status,
                "cost_impact": str(item.cost_impact),
            }
            for item in project.variations.all()
        ],
        "source_revision": project.version,
    }
