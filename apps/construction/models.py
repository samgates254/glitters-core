from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models

from apps.construction.domain import (
    BoQStatus,
    DefectStatus,
    DrawingStatus,
    InspectionStatus,
    ProjectRole,
    ProjectStatus,
    RFIStatus,
    TenderStatus,
    VariationStatus,
)


class AppendOnlyQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValueError("Construction audit events are append-only.")

    def delete(self):
        raise ValueError("Construction audit events are append-only.")


class ConstructionProject(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.PROTECT, related_name="construction_projects"
    )
    property = models.ForeignKey(
        "properties.Property",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="construction_projects",
    )
    code = models.CharField(max_length=32)
    name = models.CharField(max_length=200)
    client_organization = models.ForeignKey(
        "organizations.Organization",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="client_construction_projects",
    )
    project_type = models.CharField(max_length=64, blank=True, default="")
    scope = models.TextField(blank=True, default="")
    classification = models.CharField(max_length=64)
    description = models.TextField(blank=True, default="")
    site_location_reference = models.CharField(max_length=128, blank=True, default="")
    client_name = models.CharField(max_length=200, blank=True, default="")
    budget = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    approved_budget = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    completion_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    risk_rating = models.CharField(max_length=16, default="medium")
    start_date = models.DateField(null=True, blank=True)
    expected_completion_date = models.DateField(null=True, blank=True)
    actual_completion_date = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=32,
        choices=((v, v.title()) for v in sorted(ProjectStatus.VALUES)),
        default=ProjectStatus.DRAFT,
    )
    project_manager = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="managed_construction_projects",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_construction_projects",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "construction_project"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "code"], name="construction_project_org_code_unique"
            ),
            models.CheckConstraint(
                condition=models.Q(budget__gte=0), name="construction_project_budget_nonnegative"
            ),
            models.CheckConstraint(
                condition=models.Q(approved_budget__gte=0),
                name="construction_proj_approved_budget",
            ),
            models.CheckConstraint(
                condition=models.Q(completion_percent__gte=0)
                & models.Q(completion_percent__lte=100),
                name="construction_proj_completion",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=sorted(ProjectStatus.VALUES)),
                name="construction_project_status_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(expected_completion_date__isnull=True)
                | models.Q(start_date__isnull=True)
                | models.Q(expected_completion_date__gte=models.F("start_date")),
                name="construction_project_date_range",
            ),
        ]
        indexes = [models.Index(fields=["organization", "status"], name="cproject_org_status_idx")]

    def __str__(self):
        return f"{self.code} - {self.name}"


class ProjectMember(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        ConstructionProject, on_delete=models.CASCADE, related_name="members"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="construction_project_memberships",
    )
    role = models.CharField(max_length=32, choices=ProjectRole.CHOICES)
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "construction_project_member"
        constraints = [
            models.UniqueConstraint(
                fields=["project", "user", "role"], name="construction_project_member_unique"
            ),
            models.CheckConstraint(
                condition=models.Q(role__in=sorted(ProjectRole.VALUES)),
                name="construction_project_member_role_valid",
            ),
        ]


class ProjectPhase(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        ConstructionProject, on_delete=models.CASCADE, related_name="phases"
    )
    name = models.CharField(max_length=200)
    sequence = models.PositiveIntegerField(default=1)
    planned_start = models.DateField(null=True, blank=True)
    planned_end = models.DateField(null=True, blank=True)
    actual_start = models.DateField(null=True, blank=True)
    actual_end = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "sequence"], name="construction_phase_sequence_unique"
            ),
            models.CheckConstraint(
                condition=models.Q(planned_end__isnull=True)
                | models.Q(planned_start__isnull=True)
                | models.Q(planned_end__gte=models.F("planned_start")),
                name="construction_phase_plan_range",
            ),
            models.CheckConstraint(
                condition=models.Q(actual_end__isnull=True)
                | models.Q(actual_start__isnull=True)
                | models.Q(actual_end__gte=models.F("actual_start")),
                name="construction_phase_actual_range",
            ),
        ]


class ProjectActivity(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        ConstructionProject, on_delete=models.CASCADE, related_name="activities"
    )
    phase = models.ForeignKey(
        ProjectPhase,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="activities",
    )
    name = models.CharField(max_length=200)
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    planned_start = models.DateField(null=True, blank=True)
    planned_end = models.DateField(null=True, blank=True)
    actual_start = models.DateField(null=True, blank=True)
    actual_end = models.DateField(null=True, blank=True)
    progress_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    dependencies = models.ManyToManyField(
        "self", symmetrical=False, blank=True, related_name="dependent_activities"
    )
    blocker_notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(progress_percent__gte=0) & models.Q(progress_percent__lte=100),
                name="construction_activity_progress",
            ),
            models.CheckConstraint(
                condition=models.Q(planned_end__isnull=True)
                | models.Q(planned_start__isnull=True)
                | models.Q(planned_end__gte=models.F("planned_start")),
                name="construction_activity_plan_range",
            ),
        ]


class ProjectMilestone(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        ConstructionProject, on_delete=models.CASCADE, related_name="milestones"
    )
    name = models.CharField(max_length=200)
    planned_start = models.DateField()
    planned_end = models.DateField()
    actual_start = models.DateField(null=True, blank=True)
    actual_end = models.DateField(null=True, blank=True)
    progress_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    completed_confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    dependencies = models.ManyToManyField(
        "self", symmetrical=False, blank=True, related_name="dependents"
    )
    blockers = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(planned_end__gte=models.F("planned_start")),
                name="construction_milestone_plan_range",
            ),
            models.CheckConstraint(
                condition=models.Q(progress_percent__gte=0) & models.Q(progress_percent__lte=100),
                name="construction_milestone_progress_range",
            ),
        ]


class EngineeringInspection(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        ConstructionProject, on_delete=models.CASCADE, related_name="inspections"
    )
    title = models.CharField(max_length=200)
    inspector = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="construction_inspections"
    )
    status = models.CharField(
        max_length=16, choices=InspectionStatus.CHOICES, default=InspectionStatus.DRAFT
    )
    observations = models.TextField(blank=True, default="")
    safety_observations = models.TextField(blank=True, default="")
    inspection_date = models.DateField()
    report_approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-inspection_date"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=sorted(InspectionStatus.VALUES)),
                name="construction_inspection_status_valid",
            )
        ]


class Defect(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    inspection = models.ForeignKey(
        EngineeringInspection, on_delete=models.CASCADE, related_name="defects"
    )
    reference = models.CharField(max_length=32)
    description = models.TextField()
    status = models.CharField(
        max_length=16, choices=DefectStatus.CHOICES, default=DefectStatus.OPEN
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="assigned_construction_defects",
    )
    rectification_order = models.TextField(blank=True, default="")
    evidence_digest = models.CharField(max_length=64, blank=True, default="")
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    closed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["inspection", "reference"], name="construction_defect_ref_unique"
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=sorted(DefectStatus.VALUES)),
                name="construction_defect_status_valid",
            ),
        ]


class InspectionChecklist(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    inspection = models.ForeignKey(
        EngineeringInspection, on_delete=models.CASCADE, related_name="checklist_items"
    )
    item = models.CharField(max_length=200)
    outcome = models.CharField(max_length=16, default="pending")
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(outcome__in=["pending", "pass", "fail", "not_applicable"]),
                name="construction_checklist_outcome_valid",
            )
        ]


class EngineeringIssue(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        ConstructionProject, on_delete=models.CASCADE, related_name="engineering_issues"
    )
    inspection = models.ForeignKey(
        EngineeringInspection,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="issues",
    )
    reference = models.CharField(max_length=32)
    category = models.CharField(max_length=64)
    severity = models.CharField(max_length=16)
    description = models.TextField()
    responsible_party = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    resolution_deadline = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=16, default="open")
    corrective_action = models.TextField(blank=True, default="")
    resolution_notes = models.TextField(blank=True, default="")
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    closed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "reference"], name="construction_issue_ref_unique"
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=["open", "in_progress", "resolved", "closed"]),
                name="construction_issue_status_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(severity__in=["low", "medium", "high", "critical"]),
                name="construction_issue_severity_valid",
            ),
        ]


class SiteDiary(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        ConstructionProject, on_delete=models.CASCADE, related_name="site_diaries"
    )
    diary_date = models.DateField()
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="construction_site_diaries",
    )
    weather_and_conditions = models.TextField(blank=True, default="")
    work_completed = models.TextField(blank=True, default="")
    labor_summary = models.JSONField(default=dict, blank=True)
    contractor_activity = models.TextField(blank=True, default="")
    equipment_usage = models.TextField(blank=True, default="")
    material_deliveries = models.TextField(blank=True, default="")
    delays = models.TextField(blank=True, default="")
    instructions = models.TextField(blank=True, default="")
    safety_observations = models.TextField(blank=True, default="")
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "diary_date"], name="construction_diary_day_unique"
            )
        ]
        ordering = ["-diary_date"]


class BoQ(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(ConstructionProject, on_delete=models.PROTECT, related_name="boqs")
    title = models.CharField(max_length=200)
    revision = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=16, choices=BoQStatus.CHOICES, default=BoQStatus.DRAFT)
    currency = models.CharField(max_length=3, default="KES")
    tax_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    contingency_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    subtotal = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    contingency_amount = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    grand_total = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "title", "revision"], name="construction_boq_revision_unique"
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=sorted(BoQStatus.VALUES)),
                name="construction_boq_status_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(tax_percent__gte=0) & models.Q(contingency_percent__gte=0),
                name="construction_boq_rates_nonnegative",
            ),
        ]


class BoQSection(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    boq = models.ForeignKey(BoQ, on_delete=models.CASCADE, related_name="sections")
    code = models.CharField(max_length=32)
    title = models.CharField(max_length=200)
    sequence = models.PositiveIntegerField(default=1)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["boq", "code"], name="construction_boq_section_code_unique"
            )
        ]


class BoQItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    section = models.ForeignKey(BoQSection, on_delete=models.CASCADE, related_name="items")
    item_code = models.CharField(max_length=32)
    description = models.TextField()
    unit = models.CharField(max_length=32)
    quantity = models.DecimalField(max_digits=16, decimal_places=4)
    unit_rate = models.DecimalField(max_digits=16, decimal_places=4)
    amount = models.DecimalField(max_digits=16, decimal_places=2, default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["section", "item_code"], name="construction_boq_item_code_unique"
            ),
            models.CheckConstraint(
                condition=models.Q(quantity__gte=0), name="construction_boq_quantity_nonnegative"
            ),
            models.CheckConstraint(
                condition=models.Q(unit_rate__gte=0), name="construction_boq_rate_nonnegative"
            ),
        ]


class ProjectBudget(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        ConstructionProject, on_delete=models.PROTECT, related_name="budgets"
    )
    name = models.CharField(max_length=200)
    revision = models.PositiveIntegerField(default=1)
    total = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    approved_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "name", "revision"], name="construction_budget_revision_unique"
            ),
            models.CheckConstraint(
                condition=models.Q(total__gte=0), name="construction_budget_total_nonnegative"
            ),
        ]


class BudgetCategory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    budget = models.ForeignKey(ProjectBudget, on_delete=models.CASCADE, related_name="categories")
    name = models.CharField(max_length=200)
    planned_amount = models.DecimalField(max_digits=16, decimal_places=2)
    committed_amount = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    actual_amount = models.DecimalField(max_digits=16, decimal_places=2, default=0)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(planned_amount__gte=0)
                & models.Q(committed_amount__gte=0)
                & models.Q(actual_amount__gte=0),
                name="construction_budget_amounts_nonnegative",
            )
        ]


class ApprovedVariation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        ConstructionProject, on_delete=models.PROTECT, related_name="variations"
    )
    reference = models.CharField(max_length=32)
    reason = models.TextField()
    scope_modification = models.TextField()
    cost_impact = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    time_impact_days = models.IntegerField(default=0)
    status = models.CharField(
        max_length=16, choices=VariationStatus.CHOICES, default=VariationStatus.REQUESTED
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "reference"], name="construction_variation_ref_unique"
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=sorted(VariationStatus.VALUES)),
                name="construction_variation_status_valid",
            ),
        ]


class ContractorAssignment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        ConstructionProject, on_delete=models.PROTECT, related_name="contractor_assignments"
    )
    contractor = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        related_name="construction_assignments",
    )
    scope = models.TextField()
    appointed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    appointed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "contractor"], name="construction_contractor_assignment_unique"
            )
        ]


class Tender(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        ConstructionProject, on_delete=models.PROTECT, related_name="tenders"
    )
    title = models.CharField(max_length=200)
    scope = models.TextField()
    status = models.CharField(
        max_length=16, choices=TenderStatus.CHOICES, default=TenderStatus.DRAFT
    )
    awarded_contractor = models.ForeignKey(
        "organizations.Organization",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=sorted(TenderStatus.VALUES)),
                name="construction_tender_status_valid",
            )
        ]


class TenderPackage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tender = models.ForeignKey(Tender, on_delete=models.CASCADE, related_name="packages")
    title = models.CharField(max_length=200)
    eligibility_criteria = models.TextField(blank=True, default="")
    submission_deadline = models.DateTimeField()
    bid_opening_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class TenderDocument(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tender = models.ForeignKey(Tender, on_delete=models.CASCADE, related_name="documents")
    bidder = models.ForeignKey(
        "organizations.Organization",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    document_type = models.CharField(max_length=32)
    file = models.FileField(upload_to="construction/tenders/%Y/%m/")
    integrity_digest = models.CharField(max_length=64)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)


class TenderBid(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tender = models.ForeignKey(Tender, on_delete=models.CASCADE, related_name="bids")
    bidder = models.ForeignKey(
        "organizations.Organization", on_delete=models.PROTECT, related_name="+"
    )
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    technical_score = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tender", "bidder"], name="construction_tender_bid_unique"
            ),
            models.CheckConstraint(
                condition=models.Q(amount__gte=0), name="construction_bid_amount_nonnegative"
            ),
        ]


class WorkPackage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        ConstructionProject, on_delete=models.PROTECT, related_name="work_packages"
    )
    name = models.CharField(max_length=200)
    scope = models.TextField()
    contractor = models.ForeignKey(
        "organizations.Organization",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    progress_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(progress_percent__gte=0) & models.Q(progress_percent__lte=100),
                name="construction_work_progress_range",
            )
        ]


class WorkOrder(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    work_package = models.ForeignKey(
        WorkPackage, on_delete=models.PROTECT, related_name="work_orders"
    )
    reference = models.CharField(max_length=32)
    scope = models.TextField()
    status = models.CharField(max_length=16, default="issued")
    issued_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["work_package", "reference"], name="construction_work_order_ref_unique"
            )
        ]


class CostRecord(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        ConstructionProject, on_delete=models.PROTECT, related_name="cost_records"
    )
    category = models.ForeignKey(
        BudgetCategory, on_delete=models.PROTECT, related_name="cost_records"
    )
    record_type = models.CharField(max_length=16)
    amount = models.DecimalField(max_digits=16, decimal_places=2)
    reference = models.CharField(max_length=64)
    description = models.TextField(blank=True, default="")
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(record_type__in=["commitment", "actual", "forecast"]),
                name="construction_cost_type_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(amount__gte=0), name="construction_cost_amount_nonnegative"
            ),
        ]


class ProjectHandover(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        ConstructionProject, on_delete=models.PROTECT, related_name="handovers"
    )
    reference = models.CharField(max_length=32)
    description = models.TextField()
    evidence_digest = models.CharField(max_length=64, blank=True, default="")
    accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    accepted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "reference"], name="construction_handover_ref_unique"
            )
        ]


class RFI(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(ConstructionProject, on_delete=models.PROTECT, related_name="rfis")
    reference = models.CharField(max_length=32)
    requesting_party = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    responsible_recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    subject = models.CharField(max_length=200)
    question = models.TextField()
    due_date = models.DateField()
    priority = models.CharField(max_length=16, default="normal")
    status = models.CharField(max_length=16, choices=RFIStatus.CHOICES, default=RFIStatus.DRAFT)
    response = models.TextField(blank=True, default="")
    submitted_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "reference"], name="construction_rfi_ref_unique"
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=sorted(RFIStatus.VALUES)),
                name="construction_rfi_status_valid",
            ),
        ]


class RFIResponse(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    rfi = models.ForeignKey(RFI, on_delete=models.CASCADE, related_name="responses")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    response_type = models.CharField(max_length=16, default="response")
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(response_type__in=["response", "clarification"]),
                name="construction_rfi_response_type",
            )
        ]


class TechnicalDocument(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        ConstructionProject, on_delete=models.CASCADE, related_name="technical_documents"
    )
    drawing_number = models.CharField(max_length=64)
    title = models.CharField(max_length=200)
    discipline = models.CharField(max_length=64)
    revision = models.CharField(max_length=32, default="A")
    classification = models.CharField(max_length=64, default="drawing")
    status = models.CharField(
        max_length=16, choices=DrawingStatus.CHOICES, default=DrawingStatus.DRAFT
    )
    file = models.FileField(upload_to="construction/documents/%Y/%m/")
    integrity_digest = models.CharField(max_length=64)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    supersedes = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="superseded_by"
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "drawing_number", "revision"],
                name="construction_drawing_revision_unique",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=sorted(DrawingStatus.VALUES)),
                name="construction_drawing_status_valid",
            ),
        ]


class AuditEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        ConstructionProject, on_delete=models.CASCADE, related_name="audit_events"
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    action = models.CharField(max_length=64)
    entity_type = models.CharField(max_length=64)
    entity_id = models.UUIDField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if self.pk and AuditEvent.objects.filter(pk=self.pk).exists():
            raise ValueError("Construction audit events are immutable.")
        super().save(*args, **kwargs)
