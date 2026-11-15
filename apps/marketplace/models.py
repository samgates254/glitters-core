"""Marketplace offers. Property facts stay on the property record."""

from __future__ import annotations

import uuid

from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField
from django.db import models

from apps.marketplace.domain import (
    CURRENCIES,
    BillingPeriod,
    DescriptionOrigin,
    EnquiryEventAction,
    EnquiryStatus,
    EventAction,
    ListingScope,
    ListingStatus,
    ListingType,
    MandateStatus,
    MediaKind,
    MediaVisibility,
    PricePresentation,
    SessionStatus,
    StorageProvider,
    UploadStatus,
    enquiry_event_metadata,
)
from apps.organizations.models import Organization
from apps.properties.models import Property


class AppendOnlyQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValueError("Listing events are append-only.")

    def delete(self):
        raise ValueError("Listing events are append-only.")


class ListingMandate(models.Model):
    """Authority to advertise one property. It is not proof of legal title."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    property = models.ForeignKey(
        Property,
        on_delete=models.PROTECT,
        related_name="listing_mandates",
    )
    principal_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="listing_mandates_as_principal",
    )
    principal_organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="listing_mandates_as_principal",
    )
    representative_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="listing_mandates_as_representative",
    )
    representative_organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="listing_mandates_as_representative",
    )
    granting_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="listing_mandates_granted",
    )
    accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="listing_mandates_accepted",
    )
    listing_scope = models.CharField(max_length=32)
    valid_from = models.DateField()
    valid_until = models.DateField(null=True, blank=True)
    evidence_reference = models.CharField(max_length=200, blank=True, default="")
    status = models.CharField(max_length=32, choices=MandateStatus.CHOICES)
    self_declared = models.BooleanField(default=False)
    accepted_at = models.DateTimeField(null=True, blank=True)
    rejected_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "listing_mandate"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(listing_scope__in=sorted(ListingScope.VALUES)),
                name="listing_mandate_scope_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=sorted(MandateStatus.VALUES)),
                name="listing_mandate_status_valid",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(principal_user__isnull=False, principal_organization__isnull=True)
                    | models.Q(principal_user__isnull=True, principal_organization__isnull=False)
                ),
                name="listing_mandate_one_principal",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        representative_user__isnull=False,
                        representative_organization__isnull=True,
                    )
                    | models.Q(
                        representative_user__isnull=True,
                        representative_organization__isnull=False,
                    )
                ),
                name="listing_mandate_one_representative",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(self_declared=False)
                    | models.Q(
                        self_declared=True,
                        principal_user__isnull=False,
                        representative_user__isnull=False,
                        principal_organization__isnull=True,
                        representative_organization__isnull=True,
                        principal_user_id=models.F("representative_user_id"),
                    )
                    | models.Q(
                        self_declared=True,
                        principal_organization__isnull=False,
                        representative_organization__isnull=False,
                        principal_user__isnull=True,
                        representative_user__isnull=True,
                        principal_organization_id=models.F("representative_organization_id"),
                    )
                ),
                name="listing_mandate_self_declared_match",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(valid_until__isnull=True)
                    | models.Q(valid_until__gte=models.F("valid_from"))
                ),
                name="listing_mandate_dates",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status=MandateStatus.ACTIVE, accepted_at__isnull=False)
                    | ~models.Q(status=MandateStatus.ACTIVE)
                ),
                name="listing_mandate_active_accepted",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status=MandateStatus.REVOKED, revoked_at__isnull=False)
                    | ~models.Q(status=MandateStatus.REVOKED)
                ),
                name="listing_mandate_revoked_timestamp",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status=MandateStatus.REJECTED, rejected_at__isnull=False)
                    | ~models.Q(status=MandateStatus.REJECTED)
                ),
                name="listing_mandate_rejected_timestamp",
            ),
        ]
        indexes = [
            models.Index(fields=["property", "status"], name="listing_mandate_property"),
            models.Index(fields=["representative_user", "status"], name="listing_mandate_rep_user"),
            models.Index(
                fields=["representative_organization", "status"],
                name="listing_mandate_rep_org",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.listing_scope} {self.property_id} {self.status}"


class Listing(models.Model):
    """A commercial offer for a canonical property. Status is not stored on the property."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reference = models.CharField(max_length=20, unique=True)
    property = models.ForeignKey(Property, on_delete=models.PROTECT, related_name="listings")
    listing_type = models.CharField(max_length=32, choices=ListingType.CHOICES)
    status = models.CharField(
        max_length=32,
        choices=ListingStatus.CHOICES,
        default=ListingStatus.DRAFT,
    )
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    highlights = models.TextField(blank=True, default="")
    description_origin = models.CharField(
        max_length=16,
        choices=DescriptionOrigin.CHOICES,
        default=DescriptionOrigin.HUMAN,
    )
    ai_approved_at = models.DateTimeField(null=True, blank=True)
    advertiser = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="advertised_listings",
    )
    advertising_organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="advertised_listings",
    )
    mandate = models.ForeignKey(ListingMandate, on_delete=models.PROTECT, related_name="listings")
    moderation_hold = models.BooleanField(default=False)
    rejection_reason = models.TextField(blank=True, default="")
    moderation_note = models.TextField(blank=True, default="")
    submitted_at = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    withdrawn_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    paused_at = models.DateTimeField(null=True, blank=True)
    rejected_at = models.DateTimeField(null=True, blank=True)
    search_vector = SearchVectorField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "listing"
        ordering = ["-published_at", "-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(listing_type__in=sorted(ListingType.VALUES)),
                name="listing_type_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=sorted(ListingStatus.VALUES)),
                name="listing_status_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(description_origin__in=sorted(DescriptionOrigin.VALUES)),
                name="listing_description_origin_valid",
            ),
            models.CheckConstraint(condition=~models.Q(title=""), name="listing_title_present"),
            models.CheckConstraint(
                condition=(
                    models.Q(description_origin=DescriptionOrigin.AI_ASSISTED)
                    | models.Q(ai_approved_at__isnull=True)
                ),
                name="listing_ai_approval_origin",
            ),
            models.CheckConstraint(
                condition=(models.Q(moderation_hold=False) | models.Q(status=ListingStatus.PAUSED)),
                name="listing_hold_only_when_paused",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status=ListingStatus.PUBLISHED, expires_at__isnull=False)
                    | ~models.Q(status=ListingStatus.PUBLISHED)
                ),
                name="listing_published_expiry",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status=ListingStatus.PUBLISHED, published_at__isnull=False)
                    | ~models.Q(status=ListingStatus.PUBLISHED)
                ),
                name="listing_published_timestamp",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status=ListingStatus.WITHDRAWN, withdrawn_at__isnull=False)
                    | ~models.Q(status=ListingStatus.WITHDRAWN)
                ),
                name="listing_withdrawn_timestamp",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status=ListingStatus.COMPLETED, completed_at__isnull=False)
                    | ~models.Q(status=ListingStatus.COMPLETED)
                ),
                name="listing_completed_timestamp",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status=ListingStatus.EXPIRED, expires_at__isnull=False)
                    | ~models.Q(status=ListingStatus.EXPIRED)
                ),
                name="listing_expired_timestamp",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status=ListingStatus.REJECTED, rejected_at__isnull=False)
                    | ~models.Q(status=ListingStatus.REJECTED)
                ),
                name="listing_rejected_timestamp",
            ),
            models.UniqueConstraint(
                fields=["property", "listing_type"],
                condition=models.Q(status__in=sorted(ListingStatus.OPEN)),
                name="listing_one_open_type",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "published_at"], name="listing_status_published"),
            models.Index(fields=["listing_type", "status"], name="listing_type_status"),
            models.Index(fields=["property", "status"], name="listing_property_status"),
            models.Index(fields=["advertiser", "status"], name="listing_advertiser_status"),
            models.Index(
                fields=["advertising_organization", "status"],
                name="listing_org_status",
            ),
            GinIndex(fields=["search_vector"], name="listing_search_gin"),
        ]

    def __str__(self) -> str:
        return self.reference


class ListingPrice(models.Model):
    """One asking-price row. A change inserts a new row and supersedes the previous."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    listing = models.ForeignKey(Listing, on_delete=models.CASCADE, related_name="prices")
    amount_minor = models.BigIntegerField(null=True, blank=True)
    currency = models.CharField(max_length=3)
    presentation = models.CharField(max_length=32, choices=PricePresentation.CHOICES)
    negotiable = models.BooleanField(default=False)
    billing_period = models.CharField(max_length=16, choices=BillingPeriod.CHOICES)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="listing_prices_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    superseded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "listing_price"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(currency__in=sorted(CURRENCIES)),
                name="listing_price_currency_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(presentation__in=sorted(PricePresentation.VALUES)),
                name="listing_price_presentation_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(billing_period__in=sorted(BillingPeriod.VALUES)),
                name="listing_price_period_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(amount_minor__isnull=True) | models.Q(amount_minor__gte=0),
                name="listing_price_amount_nonnegative",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        presentation=PricePresentation.PRICE_ON_APPLICATION,
                        amount_minor__isnull=True,
                    )
                    | models.Q(
                        presentation=PricePresentation.ASKING,
                        amount_minor__isnull=False,
                    )
                ),
                name="listing_price_amount_matches_presentation",
            ),
            models.UniqueConstraint(
                fields=["listing"],
                condition=models.Q(superseded_at__isnull=True),
                name="listing_price_one_current",
            ),
        ]
        indexes = [
            models.Index(fields=["listing", "superseded_at"], name="listing_price_current"),
        ]

    def __str__(self) -> str:
        return f"{self.currency} {self.listing_id}"


class PropertyMedia(models.Model):
    """A verified object in private storage. Image bytes are not stored in PostgreSQL."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    property = models.ForeignKey(Property, on_delete=models.CASCADE, related_name="media")
    kind = models.CharField(max_length=16, choices=MediaKind.CHOICES)
    mime_type = models.CharField(max_length=64)
    storage_reference = models.CharField(max_length=500)
    storage_provider = models.CharField(max_length=16, blank=True, default="")
    storage_bucket = models.CharField(max_length=128, blank=True, default="")
    storage_key = models.CharField(max_length=500, blank=True, default="")
    file_size_bytes = models.PositiveBigIntegerField(null=True, blank=True)
    checksum_sha256 = models.CharField(max_length=64, blank=True, default="")
    verified_at = models.DateTimeField(null=True, blank=True)
    upload_status = models.CharField(max_length=16, choices=UploadStatus.CHOICES)
    visibility = models.CharField(max_length=16, choices=MediaVisibility.CHOICES)
    caption = models.CharField(max_length=300, blank=True, default="")
    alt_text = models.CharField(max_length=300, blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="property_media_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "property_media"
        ordering = ["created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(upload_status__in=sorted(UploadStatus.VALUES)),
                name="property_media_upload_valid",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        kind=MediaKind.PHOTOGRAPH,
                        mime_type__in=sorted({"image/jpeg", "image/png", "image/webp"}),
                        visibility=MediaVisibility.LISTING,
                    )
                    | models.Q(
                        kind=MediaKind.DOCUMENT,
                        mime_type="application/pdf",
                        visibility=MediaVisibility.PRIVATE,
                    )
                ),
                name="property_media_kind_mime",
            ),
            models.CheckConstraint(
                condition=~models.Q(storage_reference=""),
                name="property_media_reference_present",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(storage_provider="")
                    | models.Q(storage_provider__in=sorted(StorageProvider.VALUES))
                ),
                name="property_media_provider_known",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(upload_status=UploadStatus.STORED)
                    | models.Q(storage_provider="")
                    | (
                        models.Q(verified_at__isnull=False)
                        & models.Q(file_size_bytes__gt=0)
                        & ~models.Q(checksum_sha256="")
                        & ~models.Q(storage_key="")
                        & ~models.Q(storage_bucket="")
                    )
                ),
                name="property_media_stored_is_verified",
            ),
            models.UniqueConstraint(
                fields=["storage_key"],
                condition=~models.Q(storage_key=""),
                name="property_media_storage_key_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["property", "kind"], name="property_media_property_kind"),
        ]

    def __str__(self) -> str:
        return f"{self.kind} {self.property_id}"


class MediaUploadSession(models.Model):
    """One server-controlled upload. The presigned URL is not a column."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    property = models.ForeignKey(
        Property,
        on_delete=models.CASCADE,
        related_name="upload_sessions",
    )
    media = models.ForeignKey(
        PropertyMedia,
        on_delete=models.CASCADE,
        related_name="upload_sessions",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="media_upload_sessions",
    )
    kind = models.CharField(max_length=16, choices=MediaKind.CHOICES)
    declared_mime = models.CharField(max_length=64)
    status = models.CharField(max_length=16, choices=SessionStatus.CHOICES)
    storage_provider = models.CharField(max_length=16, choices=StorageProvider.CHOICES)
    storage_bucket = models.CharField(max_length=128)
    storage_key = models.CharField(max_length=500, unique=True)
    declared_size_bytes = models.PositiveBigIntegerField()
    file_size_bytes = models.PositiveBigIntegerField(null=True, blank=True)
    checksum_sha256 = models.CharField(max_length=64, blank=True, default="")
    expires_at = models.DateTimeField()
    verified_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    rejected_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    rejection_code = models.CharField(max_length=32, blank=True, default="")
    object_deleted = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "media_upload_session"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=sorted(SessionStatus.VALUES)),
                name="media_upload_status_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(storage_provider__in=sorted(StorageProvider.VALUES)),
                name="media_upload_provider_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(declared_size_bytes__gt=0),
                name="media_upload_declared_size",
            ),
            models.CheckConstraint(
                condition=~models.Q(storage_key=""),
                name="media_upload_key_present",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(status=SessionStatus.COMPLETED)
                    | (
                        models.Q(verified_at__isnull=False)
                        & models.Q(completed_at__isnull=False)
                        & models.Q(file_size_bytes__gt=0)
                        & ~models.Q(checksum_sha256="")
                    )
                ),
                name="media_upload_completed_verified",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "expires_at"], name="media_upload_status_expiry"),
            models.Index(fields=["property", "created_at"], name="media_upload_property"),
        ]

    def __str__(self) -> str:
        return f"{self.status} {self.media_id}"


class ListingMedia(models.Model):
    """Photographs selected for one listing. Documents cannot be selected."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    listing = models.ForeignKey(Listing, on_delete=models.CASCADE, related_name="listing_media")
    property_media = models.ForeignKey(
        PropertyMedia,
        on_delete=models.PROTECT,
        related_name="listing_links",
    )
    sort_order = models.PositiveIntegerField(default=0)
    featured = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "listing_media"
        ordering = ["sort_order", "created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["listing", "property_media"],
                name="listing_media_once",
            ),
            models.UniqueConstraint(
                fields=["listing"],
                condition=models.Q(featured=True),
                name="listing_media_one_featured",
            ),
            models.CheckConstraint(
                condition=models.Q(sort_order__gte=0, sort_order__lte=1000),
                name="listing_media_sort_range",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.listing_id} {self.sort_order}"


class ListingEvent(models.Model):
    """Append-only marketplace audit. Metadata must not contain secrets or coordinates."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="listing_events",
    )
    listing = models.ForeignKey(
        Listing,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="events",
    )
    property = models.ForeignKey(Property, on_delete=models.CASCADE, related_name="listing_events")
    organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="listing_events",
    )
    action = models.CharField(max_length=64, choices=EventAction.CHOICES)
    object_type = models.CharField(max_length=64)
    object_id = models.CharField(max_length=36)
    request_id = models.CharField(max_length=128, blank=True, default="")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    metadata = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        db_table = "listing_event"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(action__in=sorted(EventAction.VALUES)),
                name="listing_event_action_valid",
            ),
        ]
        indexes = [
            models.Index(fields=["listing", "created_at"], name="listing_event_listing"),
            models.Index(fields=["property", "created_at"], name="listing_event_property"),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("Listing events are append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Listing events are append-only.")

    def __str__(self) -> str:
        return self.action


def _text_present(field: str) -> models.Q:
    """True only for a non-null, non-empty string.

    A comparison with NULL is NULL. PostgreSQL treats a NULL check result as
    success, so the NULL test has to contribute FALSE before the comparison.
    """

    return models.Q(**{f"{field}__isnull": False}) & ~models.Q(**{field: ""})


class Enquiry(models.Model):
    """Private contact request for one listing.

    The database enforces the privacy shape, the status vocabulary, and the
    closed timestamp. It does not enforce the status graph, trim contact
    text, or limit a message to 5000 characters. The enquiry service must
    copy advertiser and organization from the listing, refuse client-supplied
    substitutes, and refuse to clear anonymized_at.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    listing = models.ForeignKey(
        Listing,
        on_delete=models.PROTECT,
        related_name="enquiries",
    )
    advertiser = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="marketplace_enquiries_received",
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="marketplace_enquiries",
    )

    contact_name = models.CharField(max_length=160, null=True, blank=True)
    contact_email = models.EmailField(max_length=254, null=True, blank=True)
    contact_phone = models.CharField(max_length=32, null=True, blank=True)
    message = models.TextField(max_length=5000, null=True, blank=True)

    status = models.CharField(
        max_length=24,
        choices=EnquiryStatus.CHOICES,
        default=EnquiryStatus.NEW,
    )

    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_marketplace_enquiries",
    )

    consent_at = models.DateTimeField()
    consent_version = models.CharField(max_length=32)

    closed_at = models.DateTimeField(null=True, blank=True)
    anonymized_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "marketplace_enquiry"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=sorted(EnquiryStatus.VALUES)),
                name="marketplace_enquiry_status_valid",
            ),
            models.CheckConstraint(
                condition=(
                    (
                        models.Q(anonymized_at__isnull=True)
                        & _text_present("contact_name")
                        & _text_present("contact_email")
                        & _text_present("message")
                        & (models.Q(contact_phone__isnull=True) | _text_present("contact_phone"))
                    )
                    | models.Q(
                        anonymized_at__isnull=False,
                        contact_name__isnull=True,
                        contact_email__isnull=True,
                        contact_phone__isnull=True,
                        message__isnull=True,
                    )
                ),
                name="marketplace_enquiry_privacy_state",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status=EnquiryStatus.CLOSED,
                        closed_at__isnull=False,
                    )
                    | (~models.Q(status=EnquiryStatus.CLOSED) & models.Q(closed_at__isnull=True))
                ),
                name="marketplace_enquiry_closed_timestamp",
            ),
        ]
        indexes = [
            models.Index(
                fields=["listing", "created_at"],
                name="mkt_enquiry_listing_created",
            ),
            models.Index(
                fields=["advertiser", "created_at"],
                name="mkt_enquiry_advertiser",
            ),
            models.Index(
                fields=["organization", "status", "created_at"],
                name="mkt_enquiry_org_status",
            ),
            models.Index(
                fields=["assigned_to", "status"],
                name="mkt_enquiry_assignee",
            ),
        ]

    def __str__(self) -> str:
        return f"Enquiry {self.id}"


class EnquiryEventQuerySet(models.QuerySet):
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
            event.metadata = enquiry_event_metadata(event.metadata)
        return super().bulk_create(objs, **kwargs)


class EnquiryEvent(models.Model):
    """Append-only enquiry audit. Metadata must never contain contact PII."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    enquiry = models.ForeignKey(
        Enquiry,
        on_delete=models.PROTECT,
        related_name="events",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="marketplace_enquiry_events",
    )

    action = models.CharField(
        max_length=64,
        choices=EnquiryEventAction.CHOICES,
    )
    request_id = models.CharField(max_length=128, blank=True, default="")
    metadata = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = EnquiryEventQuerySet.as_manager()

    class Meta:
        db_table = "marketplace_enquiry_event"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(action__in=sorted(EnquiryEventAction.VALUES)),
                name="mkt_enquiry_event_action_valid",
            ),
        ]
        indexes = [
            models.Index(
                fields=["enquiry", "created_at"],
                name="mkt_enquiry_event_created",
            ),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("Enquiry events are append-only.")
        self.metadata = enquiry_event_metadata(self.metadata)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Enquiry events are append-only.")

    def __str__(self) -> str:
        return self.action
