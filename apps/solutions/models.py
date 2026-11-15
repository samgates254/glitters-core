"""Commercial records. Publication is a staff action, not a partner action."""

from __future__ import annotations

import uuid
from pathlib import Path

from django.conf import settings
from django.db import models

from apps.organizations.models import Organization
from apps.solutions.domain import (
    PREFERRED,
    AuctionCategory,
    AuctionStatus,
    CatalogueStatus,
    DocumentKind,
    DocumentVisibility,
    EnquiryCategory,
    EnquiryEventAction,
    EnquiryIntent,
    EnquiryStatus,
    InsuranceCategory,
    InsuranceLine,
    InvestmentCategory,
    ProjectCategory,
    ProjectStatus,
    QuotationMode,
    ServiceGroup,
    Tenure,
    event_metadata,
)


def _present(field: str):
    return ~models.Q(**{field: ""}) & models.Q(**{f"{field}__isnull": False})


def _document_path(instance, filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in {".pdf", ".jpg", ".jpeg", ".png"}:
        suffix = ""
    return f"solutions/private/{uuid.uuid4().hex}{suffix}"


class AuctionListing(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reference = models.CharField(max_length=16, unique=True)
    title = models.CharField(max_length=200)
    category = models.CharField(max_length=32, choices=AuctionCategory.CHOICES)
    description = models.TextField()
    county = models.CharField(max_length=80)
    town = models.CharField(max_length=80, blank=True, default="")
    location_label = models.CharField(max_length=200, blank=True, default="")
    size_label = models.CharField(max_length=80, blank=True, default="")
    tenure = models.CharField(max_length=16, choices=Tenure.CHOICES, default=Tenure.UNKNOWN)
    guide_price_minor = models.BigIntegerField(null=True, blank=True)
    guide_currency = models.CharField(max_length=3, default="KES")
    reserve_price_minor = models.BigIntegerField(null=True, blank=True)
    publish_reserve = models.BooleanField(default=False)
    auction_at = models.DateTimeField(null=True, blank=True)
    viewing_notes = models.TextField(blank=True, default="")
    registration_deadline = models.DateTimeField(null=True, blank=True)
    terms = models.TextField(blank=True, default="")
    deposit_requirements = models.TextField(blank=True, default="")
    payment_instructions = models.TextField(blank=True, default="")
    contact_name = models.CharField(max_length=160, blank=True, default="")
    contact_email = models.EmailField(blank=True, default="")
    contact_phone = models.CharField(max_length=32, blank=True, default="")
    institution = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="auction_institutions",
    )
    institution_name = models.CharField(max_length=200, blank=True, default="")
    auctioneer = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="auction_auctioneers",
    )
    auctioneer_name = models.CharField(max_length=200, blank=True, default="")
    auctioneer_licence_reference = models.CharField(max_length=80, blank=True, default="")
    auctioneer_verified = models.BooleanField(default=False)
    submitting_organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="submitted_auctions",
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="submitted_auctions",
    )
    status = models.CharField(
        max_length=32,
        choices=AuctionStatus.CHOICES,
        default=AuctionStatus.DRAFT,
    )
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "solutions_auction"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=sorted(AuctionStatus.VALUES)),
                name="solutions_auction_status",
            ),
            models.CheckConstraint(
                condition=models.Q(category__in=sorted(AuctionCategory.VALUES)),
                name="solutions_auction_category",
            ),
            models.CheckConstraint(
                condition=models.Q(tenure__in=sorted(Tenure.VALUES)),
                name="solutions_auction_tenure",
            ),
            models.CheckConstraint(
                condition=models.Q(guide_price_minor__isnull=True)
                | models.Q(guide_price_minor__gte=0),
                name="solutions_auction_guide_nonnegative",
            ),
            models.CheckConstraint(
                condition=models.Q(reserve_price_minor__isnull=True)
                | models.Q(reserve_price_minor__gte=0),
                name="solutions_auction_reserve_nonnegative",
            ),
            models.CheckConstraint(
                condition=models.Q(publish_reserve=False)
                | models.Q(reserve_price_minor__isnull=False),
                name="solutions_auction_reserve_flag",
            ),
            models.CheckConstraint(
                condition=models.Q(auctioneer_verified=False)
                | ~models.Q(auctioneer_licence_reference=""),
                name="solutions_auction_licence_when_verified",
            ),
            models.CheckConstraint(
                condition=~models.Q(status__in=sorted(AuctionStatus.PUBLIC))
                | models.Q(published_at__isnull=False),
                name="solutions_auction_published_timestamp",
            ),
        ]

    def __str__(self) -> str:
        return self.reference


class AuctionDocument(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    auction = models.ForeignKey(
        AuctionListing,
        on_delete=models.CASCADE,
        related_name="documents",
    )
    kind = models.CharField(max_length=16, choices=DocumentKind.CHOICES)
    visibility = models.CharField(
        max_length=16,
        choices=DocumentVisibility.CHOICES,
        default=DocumentVisibility.PRIVATE,
    )
    original_name = models.CharField(max_length=200, blank=True, default="")
    content_type = models.CharField(max_length=80)
    upload = models.FileField(upload_to=_document_path)
    caption = models.CharField(max_length=300, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "solutions_auction_document"
        ordering = ["created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(kind__in=sorted(DocumentKind.VALUES)),
                name="solutions_document_kind",
            ),
            models.CheckConstraint(
                condition=models.Q(visibility__in=sorted(DocumentVisibility.VALUES)),
                name="solutions_document_visibility",
            ),
        ]


class DevelopmentProject(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    slug = models.SlugField(max_length=80, unique=True)
    name = models.CharField(max_length=200)
    developer_name = models.CharField(max_length=200, blank=True, default="")
    developer = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="development_projects",
    )
    location_label = models.CharField(max_length=200)
    description = models.TextField()
    category = models.CharField(max_length=32, choices=ProjectCategory.CHOICES)
    status = models.CharField(
        max_length=32,
        choices=ProjectStatus.CHOICES,
        default=ProjectStatus.DRAFT,
    )
    expected_completion = models.DateField(null=True, blank=True)
    units_summary = models.CharField(max_length=300, blank=True, default="")
    pricing_note = models.CharField(max_length=300, blank=True, default="")
    brochure_url = models.URLField(blank=True, default="")
    contact_name = models.CharField(max_length=160, blank=True, default="")
    contact_email = models.EmailField(blank=True, default="")
    contact_phone = models.CharField(max_length=32, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "solutions_project"
        ordering = ["name"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=sorted(ProjectStatus.VALUES)),
                name="solutions_project_status",
            ),
            models.CheckConstraint(
                condition=models.Q(category__in=sorted(ProjectCategory.VALUES)),
                name="solutions_project_category",
            ),
        ]

    def __str__(self) -> str:
        return self.name


class InsuranceProduct(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    slug = models.SlugField(max_length=80, unique=True)
    name = models.CharField(max_length=200)
    line = models.CharField(max_length=16, choices=InsuranceLine.CHOICES)
    category = models.CharField(max_length=32, choices=InsuranceCategory.CHOICES)
    summary = models.TextField()
    quotation_mode = models.CharField(
        max_length=16,
        choices=QuotationMode.CHOICES,
        default=QuotationMode.REQUEST,
    )
    api_configured = models.BooleanField(default=False)
    partner = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="insurance_products",
    )
    partner_name = models.CharField(max_length=200, blank=True, default="")
    status = models.CharField(
        max_length=16,
        choices=CatalogueStatus.CHOICES,
        default=CatalogueStatus.DRAFT,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "solutions_insurance_product"
        ordering = ["name"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(line__in=sorted(InsuranceLine.VALUES)),
                name="solutions_insurance_line",
            ),
            models.CheckConstraint(
                condition=models.Q(category__in=sorted(InsuranceCategory.VALUES)),
                name="solutions_insurance_category",
            ),
            models.CheckConstraint(
                condition=models.Q(quotation_mode__in=sorted(QuotationMode.VALUES)),
                name="solutions_insurance_mode",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=sorted(CatalogueStatus.VALUES)),
                name="solutions_insurance_status",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        line=InsuranceLine.GENERAL,
                        category__in=sorted(InsuranceCategory.GENERAL),
                    )
                    | models.Q(line=InsuranceLine.LIFE, category__in=sorted(InsuranceCategory.LIFE))
                ),
                name="solutions_insurance_line_category",
            ),
        ]

    def __str__(self) -> str:
        return self.name


class InvestmentOpportunity(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reference = models.CharField(max_length=16, unique=True)
    name = models.CharField(max_length=200)
    symbol = models.CharField(max_length=32, blank=True, default="")
    category = models.CharField(max_length=16, choices=InvestmentCategory.CHOICES)
    summary = models.TextField()
    market_note = models.TextField(blank=True, default="")
    price_minor = models.BigIntegerField(null=True, blank=True)
    price_currency = models.CharField(max_length=3, default="KES")
    data_source = models.CharField(max_length=200, blank=True, default="")
    price_as_of = models.DateTimeField(null=True, blank=True)
    risk_disclosure = models.TextField()
    partner = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="investment_opportunities",
    )
    partner_name = models.CharField(max_length=200, blank=True, default="")
    status = models.CharField(
        max_length=16,
        choices=CatalogueStatus.CHOICES,
        default=CatalogueStatus.DRAFT,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "solutions_investment"
        ordering = ["name"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(category__in=sorted(InvestmentCategory.VALUES)),
                name="solutions_investment_category",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=sorted(CatalogueStatus.VALUES)),
                name="solutions_investment_status",
            ),
            models.CheckConstraint(
                condition=models.Q(price_minor__isnull=True) | models.Q(price_minor__gte=0),
                name="solutions_investment_price_nonnegative",
            ),
        ]

    def __str__(self) -> str:
        return self.reference


class ServiceCategory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    group = models.CharField(max_length=32, choices=ServiceGroup.CHOICES)
    slug = models.SlugField(max_length=80, unique=True)
    name = models.CharField(max_length=120)
    description = models.TextField()

    class Meta:
        db_table = "solutions_service_category"
        ordering = ["name"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(group__in=sorted(ServiceGroup.VALUES)),
                name="solutions_service_group",
            ),
        ]

    def __str__(self) -> str:
        return self.name


class ServiceProvider(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    category = models.ForeignKey(
        ServiceCategory,
        on_delete=models.PROTECT,
        related_name="providers",
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="service_providers",
    )
    name = models.CharField(max_length=200)
    coverage = models.CharField(max_length=200, blank=True, default="")
    summary = models.TextField(blank=True, default="")
    credentials_note = models.CharField(max_length=300, blank=True, default="")
    credentials_checked = models.BooleanField(default=False)
    portfolio_note = models.TextField(blank=True, default="")
    contact_name = models.CharField(max_length=160, blank=True, default="")
    contact_email = models.EmailField(blank=True, default="")
    contact_phone = models.CharField(max_length=32, blank=True, default="")
    status = models.CharField(
        max_length=16,
        choices=CatalogueStatus.CHOICES,
        default=CatalogueStatus.DRAFT,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "solutions_service_provider"
        ordering = ["name"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=sorted(CatalogueStatus.VALUES)),
                name="solutions_provider_status",
            ),
        ]

    def __str__(self) -> str:
        return self.name


class CommercialEnquiry(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reference = models.CharField(max_length=16, unique=True)
    category = models.CharField(max_length=16, choices=EnquiryCategory.CHOICES)
    intent = models.CharField(max_length=32, choices=EnquiryIntent.CHOICES)
    subject_reference = models.CharField(max_length=80)
    auction = models.ForeignKey(
        AuctionListing,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="enquiries",
    )
    project = models.ForeignKey(
        DevelopmentProject,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="enquiries",
    )
    insurance_product = models.ForeignKey(
        InsuranceProduct,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="enquiries",
    )
    investment = models.ForeignKey(
        InvestmentOpportunity,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="enquiries",
    )
    service_category = models.ForeignKey(
        ServiceCategory,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="enquiries",
    )
    service_provider = models.ForeignKey(
        ServiceProvider,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="enquiries",
    )
    contact_name = models.CharField(max_length=160, null=True, blank=True)
    contact_email = models.EmailField(max_length=254, null=True, blank=True)
    contact_phone = models.CharField(max_length=32, null=True, blank=True)
    message = models.TextField(null=True, blank=True)
    detail = models.CharField(max_length=300, null=True, blank=True)
    preferred_contact = models.CharField(max_length=16)
    status = models.CharField(
        max_length=32,
        choices=EnquiryStatus.CHOICES,
        default=EnquiryStatus.NEW,
    )
    assigned_organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="commercial_enquiries",
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_commercial_enquiries",
    )
    internal_notes = models.TextField(blank=True, default="")
    partner_response = models.TextField(blank=True, default="")
    source = models.CharField(max_length=16, default="web")
    quotation_state = models.CharField(max_length=64, blank=True, default="")
    consent_at = models.DateTimeField()
    consent_version = models.CharField(max_length=32)
    closed_at = models.DateTimeField(null=True, blank=True)
    anonymized_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "solutions_enquiry"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(category__in=sorted(EnquiryCategory.VALUES)),
                name="solutions_enquiry_category",
            ),
            models.CheckConstraint(
                condition=models.Q(intent__in=sorted(EnquiryIntent.VALUES)),
                name="solutions_enquiry_intent",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=sorted(EnquiryStatus.VALUES)),
                name="solutions_enquiry_status",
            ),
            models.CheckConstraint(
                condition=models.Q(preferred_contact__in=sorted(PREFERRED)),
                name="solutions_enquiry_preferred",
            ),
            models.CheckConstraint(
                condition=(
                    (
                        models.Q(anonymized_at__isnull=True)
                        & _present("contact_name")
                        & _present("contact_email")
                        & _present("message")
                    )
                    | models.Q(
                        anonymized_at__isnull=False,
                        contact_name__isnull=True,
                        contact_email__isnull=True,
                        contact_phone__isnull=True,
                        message__isnull=True,
                        detail__isnull=True,
                        internal_notes="",
                        partner_response="",
                    )
                ),
                name="solutions_enquiry_privacy",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status=EnquiryStatus.CLOSED, closed_at__isnull=False)
                    | (~models.Q(status=EnquiryStatus.CLOSED) & models.Q(closed_at__isnull=True))
                ),
                name="solutions_enquiry_closed",
            ),
        ]
        indexes = [
            models.Index(
                fields=["category", "status", "created_at"],
                name="solutions_enq_cat_status",
            ),
            models.Index(
                fields=["assigned_organization", "status"],
                name="solutions_enq_org_status",
            ),
        ]

    def __str__(self) -> str:
        return self.reference


class CommercialEnquiryEventQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValueError("Enquiry events are append-only.")

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValueError("Enquiry events are append-only.")

    def delete(self):
        raise ValueError("Enquiry events are append-only.")

    def bulk_create(self, objs, **kwargs):
        if (
            kwargs.get("update_conflicts")
            or kwargs.get("update_fields")
            or kwargs.get("unique_fields")
        ):
            raise ValueError("Enquiry events are append-only.")
        for event in objs:
            event.metadata = event_metadata(event.metadata)
        return super().bulk_create(objs, **kwargs)


class CommercialEnquiryEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    enquiry = models.ForeignKey(
        CommercialEnquiry,
        on_delete=models.PROTECT,
        related_name="events",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="commercial_enquiry_events",
    )
    action = models.CharField(max_length=64, choices=EnquiryEventAction.CHOICES)
    request_id = models.CharField(max_length=128, blank=True, default="")
    metadata = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = CommercialEnquiryEventQuerySet.as_manager()

    class Meta:
        db_table = "solutions_enquiry_event"
        ordering = ["created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(action__in=sorted(EnquiryEventAction.VALUES)),
                name="solutions_enquiry_event_action",
            ),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("Enquiry events are append-only.")
        self.metadata = event_metadata(self.metadata)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Enquiry events are append-only.")


class CommercialFollowUp(models.Model):
    """A working note. The text is cleared when the enquiry is anonymized."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    enquiry = models.ForeignKey(
        CommercialEnquiry,
        on_delete=models.PROTECT,
        related_name="follow_ups",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="commercial_follow_ups",
    )
    channel = models.CharField(max_length=16)
    note = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "solutions_follow_up"
        ordering = ["created_at"]
