from __future__ import annotations

from decimal import Decimal


class ProjectStatus:
    DRAFT = "draft"
    PLANNED = "planned"
    APPROVED = "approved"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    COMPLETED = "completed"
    ARCHIVED = "archived"
    VALUES = frozenset({DRAFT, PLANNED, APPROVED, ACTIVE, SUSPENDED, COMPLETED, ARCHIVED})


PROJECT_TRANSITIONS = {
    ProjectStatus.DRAFT: frozenset({ProjectStatus.PLANNED}),
    ProjectStatus.PLANNED: frozenset({ProjectStatus.APPROVED}),
    ProjectStatus.APPROVED: frozenset({ProjectStatus.ACTIVE, ProjectStatus.SUSPENDED}),
    ProjectStatus.ACTIVE: frozenset({ProjectStatus.SUSPENDED, ProjectStatus.COMPLETED}),
    ProjectStatus.SUSPENDED: frozenset({ProjectStatus.ACTIVE, ProjectStatus.ARCHIVED}),
    ProjectStatus.COMPLETED: frozenset({ProjectStatus.ARCHIVED}),
    ProjectStatus.ARCHIVED: frozenset(),
}


class ProjectRole:
    PROJECT_ADMIN = "project_admin"
    PROJECT_MANAGER = "project_manager"
    CIVIL_ENGINEER = "civil_engineer"
    STRUCTURAL_ENGINEER = "structural_engineer"
    ARCHITECT = "architect"
    QUANTITY_SURVEYOR = "quantity_surveyor"
    CONTRACTOR = "contractor"
    SITE_INSPECTOR = "site_inspector"
    CLIENT_VIEWER = "client_viewer"
    CHOICES = (
        (PROJECT_ADMIN, "Project administrator"),
        (PROJECT_MANAGER, "Project manager"),
        (CIVIL_ENGINEER, "Civil engineer"),
        (STRUCTURAL_ENGINEER, "Structural engineer"),
        (ARCHITECT, "Architect"),
        (QUANTITY_SURVEYOR, "Quantity surveyor"),
        (CONTRACTOR, "Contractor"),
        (SITE_INSPECTOR, "Site inspector"),
        (CLIENT_VIEWER, "Client viewer"),
    )
    VALUES = frozenset(value for value, _ in CHOICES)


class InspectionStatus:
    DRAFT = "draft"
    SUBMITTED = "submitted"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    CHOICES = (
        (DRAFT, "Draft"),
        (SUBMITTED, "Submitted"),
        (IN_REVIEW, "In review"),
        (APPROVED, "Approved"),
    )
    VALUES = frozenset(value for value, _ in CHOICES)


class DefectStatus:
    OPEN = "open"
    ASSIGNED = "assigned"
    RECTIFIED = "rectified"
    CLOSED = "closed"
    CHOICES = ((OPEN, "Open"), (ASSIGNED, "Assigned"), (RECTIFIED, "Rectified"), (CLOSED, "Closed"))
    VALUES = frozenset(value for value, _ in CHOICES)


class BoQStatus:
    DRAFT = "draft"
    SUBMITTED = "submitted"
    APPROVED = "approved"
    SUPERSEDED = "superseded"
    CHOICES = (
        (DRAFT, "Draft"),
        (SUBMITTED, "Submitted"),
        (APPROVED, "Approved"),
        (SUPERSEDED, "Superseded"),
    )
    VALUES = frozenset(value for value, _ in CHOICES)


class VariationStatus:
    REQUESTED = "requested"
    REVIEW = "review"
    APPROVED = "approved"
    REJECTED = "rejected"
    CHOICES = (
        (REQUESTED, "Requested"),
        (REVIEW, "In review"),
        (APPROVED, "Approved"),
        (REJECTED, "Rejected"),
    )
    VALUES = frozenset(value for value, _ in CHOICES)


class RFIStatus:
    DRAFT = "draft"
    SUBMITTED = "submitted"
    RESPONDING = "responding"
    CLARIFICATION = "clarification"
    CLOSED = "closed"
    CHOICES = (
        (DRAFT, "Draft"),
        (SUBMITTED, "Submitted"),
        (RESPONDING, "Responding"),
        (CLARIFICATION, "Clarification"),
        (CLOSED, "Closed"),
    )
    VALUES = frozenset(value for value, _ in CHOICES)


class TenderStatus:
    DRAFT = "draft"
    INVITED = "invited"
    CLOSED = "closed"
    AWARDED = "awarded"
    CANCELLED = "cancelled"
    CHOICES = (
        (DRAFT, "Draft"),
        (INVITED, "Invited"),
        (CLOSED, "Closed"),
        (AWARDED, "Awarded"),
        (CANCELLED, "Cancelled"),
    )
    VALUES = frozenset(value for value, _ in CHOICES)


class DrawingStatus:
    DRAFT = "draft"
    SUBMITTED = "submitted"
    APPROVED = "approved"
    SUPERSEDED = "superseded"
    CHOICES = (
        (DRAFT, "Draft"),
        (SUBMITTED, "Submitted"),
        (APPROVED, "Approved"),
        (SUPERSEDED, "Superseded"),
    )
    VALUES = frozenset(value for value, _ in CHOICES)


MONEY_QUANTUM = Decimal("0.01")


def money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANTUM)
