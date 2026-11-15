"""Listing rules. A listing is not a property, a title, or a payment."""

from __future__ import annotations

import secrets
from datetime import timedelta
from decimal import Decimal

from apps.properties.domain import PropertyType

NOT_FOUND = "Not found."
PERMISSION_DENIED = "You do not have permission for this action."
TRANSITION_INVALID = "This listing status change is not allowed."
MANDATE_INVALID = "This authorization does not cover the listing."
MANDATE_SCOPE = "This authorization does not cover this listing type."
MANDATE_EXPIRED = "This authorization has expired."
MANDATE_REVOKED = "This authorization has been revoked."
MANDATE_PENDING = "This authorization has not been accepted."
PROPERTY_NOT_ACTIVE = "The property is not active."
PROPERTY_ARCHIVED = "Archived properties cannot be advertised."
TYPE_NOT_ELIGIBLE = "This listing type is not available for the property type."
PRICE_REQUIRED = "A price is required before submission."
PRICE_INVALID = "This price is not valid."
AMOUNT_INVALID = "Enter the amount as a whole number of minor units."
CURRENCY_INVALID = "This currency is not supported."
PERIOD_INVALID = "This billing period is not valid for the listing type."
MEDIA_REQUIRED = "A stored photograph is required before submission."
MEDIA_INVALID = "This media reference is not valid."
MEDIA_PRIVATE = "A private document cannot be attached to a listing."
MEDIA_OBJECT_MISSING = "The uploaded file was not found."
MEDIA_SIZE_MISMATCH = "The uploaded file size does not match."
MEDIA_SIGNATURE = "The uploaded file is not a supported image or document."
MEDIA_EXPIRED = "This upload session has expired."
MEDIA_NOT_READY = "This upload cannot be completed."
MEDIA_TOO_LARGE = "This file is larger than the allowed size."
MEDIA_NOT_STORED = "This file is not available."
STORAGE_UNAVAILABLE = "Object storage is unavailable."
FIELDS_REQUIRED = "A title and description are required before submission."
AI_REVIEW = "An assisted description must be accepted before submission."
HOLD = "This listing is held for moderation."
EXPIRY_NOT_DUE = "This listing has not reached its expiry time."
LISTING_OPEN = "This property already has an open listing of this type."
FILTER_INVALID = "This filter is not valid."
CONFLICT = "This change conflicts with another update."
VERIFY_EMAIL_FIRST = "Verify your email before continuing."
ORG_NOT_ACTIVE = "This organization is not active."
MEMBERSHIP_NOT_ACTIVE = "This membership is not active."
ACCOUNT_NOT_FOUND = "That account was not found."
ORGANIZATION_NOT_FOUND = "That organization was not found."
SELF_DECLARATION_NOTICE = (
    "This is a self-declaration of authority to advertise. GLITTERS has not verified legal title."
)
REPRESENTATION_NOTICE = (
    "This mandate authorizes marketplace representation within its scope. "
    "It is not proof of legal ownership. GLITTERS has not verified legal title."
)
MODERATION_NOTICE = (
    "Platform review decides whether a listing may be published. "
    "It does not verify legal title, business registration, or an agent licence."
)

REFERENCE_PREFIX = "LST-"
PUBLICATION_TERM = timedelta(days=90)
MAX_REFERENCE_ATTEMPTS = 5
EXPIRE_BATCH = 500
TITLE_MAX = 200
DESCRIPTION_MAX = 8000
HIGHLIGHTS_MAX = 2000
REASON_MAX = 2000
EVIDENCE_MAX = 200
CAPTION_MAX = 300
ALT_MAX = 300
STORAGE_MAX = 500

CURRENCIES = frozenset({"KES", "UGX", "TZS", "RWF", "USD"})

SENSITIVE_METADATA_KEYS = frozenset(
    {
        "token",
        "raw_token",
        "token_hash",
        "password",
        "secret",
        "authorization",
        "cookie",
        "latitude",
        "longitude",
        "lat",
        "lon",
        "coordinates",
        "geom",
        "point",
        "srid",
        "wkt",
        "geojson",
        "evidence",
        "evidence_reference",
        "document",
        "storage_reference",
        "storage_key",
        "upload_url",
        "presigned",
        "presigned_url",
        "signed_url",
        "access_key",
        "secret_access_key",
        "aws_access_key_id",
        "aws_secret_access_key",
    }
)


class ListingType:
    SALE = "sale"
    LONG_TERM_RENTAL = "long_term_rental"
    SHORT_TERM = "short_term"
    COMMERCIAL_LEASE = "commercial_lease"
    LAND_SALE = "land_sale"
    DEVELOPMENT = "development"

    CHOICES = (
        (SALE, "Property sale"),
        (LONG_TERM_RENTAL, "Long-term residential rental"),
        (SHORT_TERM, "Short-term accommodation"),
        (COMMERCIAL_LEASE, "Commercial leasing"),
        (LAND_SALE, "Land sale"),
        (DEVELOPMENT, "Development opportunity"),
    )
    VALUES = frozenset(value for value, _label in CHOICES)


class ListingScope:
    ANY = "any"
    VALUES = frozenset({ANY}) | ListingType.VALUES


class ListingStatus:
    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    PUBLISHED = "published"
    REJECTED = "rejected"
    PAUSED = "paused"
    WITHDRAWN = "withdrawn"
    EXPIRED = "expired"
    COMPLETED = "completed"

    CHOICES = (
        (DRAFT, "Draft"),
        (PENDING_REVIEW, "Pending review"),
        (PUBLISHED, "Published"),
        (REJECTED, "Rejected"),
        (PAUSED, "Paused"),
        (WITHDRAWN, "Withdrawn"),
        (EXPIRED, "Expired"),
        (COMPLETED, "Completed"),
    )
    VALUES = frozenset(value for value, _label in CHOICES)
    OPEN = frozenset({DRAFT, PENDING_REVIEW, PUBLISHED, PAUSED, REJECTED})
    TERMINAL = frozenset({WITHDRAWN, EXPIRED, COMPLETED})


class DescriptionOrigin:
    HUMAN = "human"
    AI_ASSISTED = "ai_assisted"

    CHOICES = (
        (HUMAN, "Human"),
        (AI_ASSISTED, "AI assisted"),
    )
    VALUES = frozenset(value for value, _label in CHOICES)


class PricePresentation:
    ASKING = "asking"
    PRICE_ON_APPLICATION = "price_on_application"

    CHOICES = (
        (ASKING, "Asking price"),
        (PRICE_ON_APPLICATION, "Price on application"),
    )
    VALUES = frozenset(value for value, _label in CHOICES)


class BillingPeriod:
    SALE = "sale"
    NIGHT = "night"
    WEEK = "week"
    MONTH = "month"
    YEAR = "year"

    CHOICES = (
        (SALE, "One-time sale price"),
        (NIGHT, "Per night"),
        (WEEK, "Per week"),
        (MONTH, "Per month"),
        (YEAR, "Per year"),
    )
    VALUES = frozenset(value for value, _label in CHOICES)


class MandateStatus:
    DRAFT = "draft"
    PENDING_ACCEPTANCE = "pending_acceptance"
    ACTIVE = "active"
    REJECTED = "rejected"
    REVOKED = "revoked"
    EXPIRED = "expired"

    CHOICES = (
        (DRAFT, "Draft"),
        (PENDING_ACCEPTANCE, "Pending acceptance"),
        (ACTIVE, "Active"),
        (REJECTED, "Rejected"),
        (REVOKED, "Revoked"),
        (EXPIRED, "Expired"),
    )
    VALUES = frozenset(value for value, _label in CHOICES)


class MediaKind:
    PHOTOGRAPH = "photograph"
    DOCUMENT = "document"

    CHOICES = (
        (PHOTOGRAPH, "Photograph"),
        (DOCUMENT, "Document"),
    )
    VALUES = frozenset(value for value, _label in CHOICES)


class MediaVisibility:
    LISTING = "listing"
    PRIVATE = "private"

    CHOICES = (
        (LISTING, "Listing"),
        (PRIVATE, "Private"),
    )
    VALUES = frozenset(value for value, _label in CHOICES)


class UploadStatus:
    PENDING = "pending"
    STORED = "stored"
    REJECTED = "rejected"

    CHOICES = (
        (PENDING, "Pending"),
        (STORED, "Stored"),
        (REJECTED, "Rejected"),
    )
    VALUES = frozenset(value for value, _label in CHOICES)


class SessionStatus:
    CREATED = "created"
    UPLOADED = "uploaded"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"

    CHOICES = (
        (CREATED, "Created"),
        (UPLOADED, "Uploaded"),
        (VERIFYING, "Verifying"),
        (COMPLETED, "Completed"),
        (REJECTED, "Rejected"),
        (EXPIRED, "Expired"),
        (CANCELLED, "Cancelled"),
    )
    VALUES = frozenset(value for value, _label in CHOICES)
    OPEN = frozenset({CREATED, UPLOADED, VERIFYING})


class StorageProvider:
    R2 = "r2"
    MEMORY = "memory"

    CHOICES = (
        (R2, "Cloudflare R2"),
        (MEMORY, "Memory"),
    )
    VALUES = frozenset(value for value, _label in CHOICES)


PHOTOGRAPH_MIMES = frozenset({"image/jpeg", "image/png", "image/webp"})
DOCUMENT_MIME = "application/pdf"


def verified_photo_filters() -> dict[str, object]:
    """Photographs that passed server-side verification and may be listed."""

    return {
        "property_media__kind": MediaKind.PHOTOGRAPH,
        "property_media__upload_status": UploadStatus.STORED,
        "property_media__visibility": MediaVisibility.LISTING,
        "property_media__verified_at__isnull": False,
    }


_RESIDENTIAL = frozenset(
    {PropertyType.RESIDENTIAL, PropertyType.HOSPITALITY, PropertyType.MIXED_USE}
)
_COMMERCIAL = frozenset({PropertyType.COMMERCIAL, PropertyType.INDUSTRIAL, PropertyType.MIXED_USE})
_SALE_TYPES = frozenset({ListingType.SALE, ListingType.LAND_SALE, ListingType.DEVELOPMENT})
_MONTHLY_TYPES = frozenset({ListingType.LONG_TERM_RENTAL, ListingType.COMMERCIAL_LEASE})

LISTING_PROPERTY_TYPES = {
    ListingType.SALE: PropertyType.VALUES,
    ListingType.LONG_TERM_RENTAL: _RESIDENTIAL,
    ListingType.SHORT_TERM: _RESIDENTIAL,
    ListingType.COMMERCIAL_LEASE: _COMMERCIAL,
    ListingType.LAND_SALE: frozenset({PropertyType.LAND}),
    ListingType.DEVELOPMENT: frozenset({PropertyType.LAND, PropertyType.MIXED_USE}),
}

PERIODS_FOR_TYPE = {
    ListingType.SALE: frozenset({BillingPeriod.SALE}),
    ListingType.LAND_SALE: frozenset({BillingPeriod.SALE}),
    ListingType.DEVELOPMENT: frozenset({BillingPeriod.SALE}),
    ListingType.LONG_TERM_RENTAL: frozenset({BillingPeriod.MONTH, BillingPeriod.YEAR}),
    ListingType.COMMERCIAL_LEASE: frozenset({BillingPeriod.MONTH, BillingPeriod.YEAR}),
    ListingType.SHORT_TERM: frozenset({BillingPeriod.NIGHT, BillingPeriod.WEEK}),
}

ADVERTISER_TRANSITIONS = {
    ListingStatus.DRAFT: frozenset({ListingStatus.PENDING_REVIEW, ListingStatus.WITHDRAWN}),
    ListingStatus.PENDING_REVIEW: frozenset({ListingStatus.WITHDRAWN}),
    ListingStatus.PUBLISHED: frozenset(
        {ListingStatus.PAUSED, ListingStatus.WITHDRAWN, ListingStatus.COMPLETED}
    ),
    ListingStatus.REJECTED: frozenset({ListingStatus.DRAFT, ListingStatus.WITHDRAWN}),
    ListingStatus.PAUSED: frozenset(
        {ListingStatus.PUBLISHED, ListingStatus.WITHDRAWN, ListingStatus.COMPLETED}
    ),
    ListingStatus.WITHDRAWN: frozenset(),
    ListingStatus.EXPIRED: frozenset(),
    ListingStatus.COMPLETED: frozenset(),
}

MODERATION_TRANSITIONS = {
    ListingStatus.PENDING_REVIEW: frozenset({ListingStatus.PUBLISHED, ListingStatus.REJECTED}),
    ListingStatus.PUBLISHED: frozenset({ListingStatus.PAUSED}),
    ListingStatus.PAUSED: frozenset({ListingStatus.PENDING_REVIEW}),
}

SYSTEM_TRANSITIONS = {
    ListingStatus.PUBLISHED: frozenset({ListingStatus.EXPIRED, ListingStatus.WITHDRAWN}),
    ListingStatus.PAUSED: frozenset({ListingStatus.EXPIRED, ListingStatus.WITHDRAWN}),
    ListingStatus.PENDING_REVIEW: frozenset({ListingStatus.WITHDRAWN}),
}

PUBLIC_ATTRIBUTE_CODES = ("bedrooms", "bathrooms", "floor_area_sqm", "plot_area_sqm")
SORTS = frozenset({"published_at", "-published_at", "price", "-price"})
GEOSPATIAL_PARAMS = frozenset(
    {"near", "bbox", "lat", "lon", "latitude", "longitude", "radius_m", "distance", "radius"}
)


class EventAction:
    LISTING_CREATED = "listing.created"
    LISTING_UPDATED = "listing.updated"
    LISTING_SUBMITTED = "listing.submitted"
    LISTING_APPROVED = "listing.approved"
    LISTING_REJECTED = "listing.rejected"
    LISTING_PUBLISHED = "listing.published"
    LISTING_PAUSED = "listing.paused"
    LISTING_RESUMED = "listing.resumed"
    LISTING_WITHDRAWN = "listing.withdrawn"
    LISTING_COMPLETED = "listing.completed"
    LISTING_EXPIRED = "listing.expired"
    MANDATE_CREATED = "mandate.created"
    MANDATE_ACCEPTED = "mandate.accepted"
    MANDATE_REJECTED = "mandate.rejected"
    MANDATE_REVOKED = "mandate.revoked"
    MANDATE_EXPIRED = "mandate.expired"
    MEDIA_ADDED = "media.added"
    MEDIA_REMOVED = "media.removed"
    MEDIA_UPLOAD_STARTED = "media.upload_started"
    MEDIA_VERIFIED = "media.verified"
    MEDIA_REJECTED = "media.rejected"
    MEDIA_DOWNLOADED = "media.downloaded"

    CHOICES = (
        (LISTING_CREATED, "Listing created"),
        (LISTING_UPDATED, "Listing updated"),
        (LISTING_SUBMITTED, "Listing submitted"),
        (LISTING_APPROVED, "Listing approved"),
        (LISTING_REJECTED, "Listing rejected"),
        (LISTING_PUBLISHED, "Listing published"),
        (LISTING_PAUSED, "Listing paused"),
        (LISTING_RESUMED, "Listing resumed"),
        (LISTING_WITHDRAWN, "Listing withdrawn"),
        (LISTING_COMPLETED, "Listing completed"),
        (LISTING_EXPIRED, "Listing expired"),
        (MANDATE_CREATED, "Mandate created"),
        (MANDATE_ACCEPTED, "Mandate accepted"),
        (MANDATE_REJECTED, "Mandate rejected"),
        (MANDATE_REVOKED, "Mandate revoked"),
        (MANDATE_EXPIRED, "Mandate expired"),
        (MEDIA_ADDED, "Media added"),
        (MEDIA_REMOVED, "Media removed"),
        (MEDIA_UPLOAD_STARTED, "Upload started"),
        (MEDIA_VERIFIED, "Media verified"),
        (MEDIA_REJECTED, "Media rejected"),
        (MEDIA_DOWNLOADED, "Media downloaded"),
    )
    VALUES = frozenset(value for value, _label in CHOICES)


def new_reference() -> str:
    return REFERENCE_PREFIX + secrets.token_hex(4).upper()


def property_type_allows(property_type: str, listing_type: str) -> bool:
    allowed = LISTING_PROPERTY_TYPES.get(listing_type)
    return allowed is not None and property_type in allowed


def period_allows(listing_type: str, billing_period: str) -> bool:
    return billing_period in PERIODS_FOR_TYPE.get(listing_type, frozenset())


def transition_allowed(current: str, new: str, channel: str) -> bool:
    table = {
        "advertiser": ADVERTISER_TRANSITIONS,
        "moderation": MODERATION_TRANSITIONS,
        "system": SYSTEM_TRANSITIONS,
    }.get(channel, {})
    return new in table.get(current, frozenset())


def sale_like(listing_type: str) -> bool:
    return listing_type in _SALE_TYPES


def monthly_like(listing_type: str) -> bool:
    return listing_type in _MONTHLY_TYPES


def parse_minor_amount(value, *, allow_null: bool) -> int | None:
    if value is None and allow_null:
        return None
    if isinstance(value, bool) or isinstance(value, float) or not isinstance(value, int):
        raise ValueError(AMOUNT_INVALID)
    if value < 0:
        raise ValueError(PRICE_INVALID)
    return value


def parse_area(value) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, int | float | str | Decimal):
        raise ValueError(FILTER_INVALID)
    try:
        amount = Decimal(str(value))
    except (ArithmeticError, ValueError) as exc:
        raise ValueError(FILTER_INVALID) from exc
    if amount < 0:
        raise ValueError(FILTER_INVALID)
    return amount


def authority_notice(self_declared: bool) -> str:
    if self_declared:
        return SELF_DECLARATION_NOTICE
    return REPRESENTATION_NOTICE


def clean_metadata(metadata: dict | None) -> dict:
    cleaned = {}
    for key, value in (metadata or {}).items():
        normalized = str(key).lower()
        if normalized in SENSITIVE_METADATA_KEYS:
            continue
        if isinstance(value, dict):
            cleaned[str(key)] = clean_metadata(value)
        elif isinstance(value, list):
            cleaned[str(key)] = [
                clean_metadata(item) if isinstance(item, dict) else item for item in value
            ]
        else:
            cleaned[str(key)] = value
    return cleaned


def advertiser_label(user) -> str:
    name = f"{getattr(user, 'first_name', '')} {getattr(user, 'last_name', '')}".strip()
    return name or "Advertiser"


# The enquiry row's TextField max_length is not enforced by PostgreSQL.
# The public service rejects a longer message before insert.
ENQUIRY_NAME_MAX = 160
ENQUIRY_MESSAGE_MAX = 5000
ENQUIRY_PHONE_MAX = 32
ENQUIRY_CONSENT_VERSION = "2026-11"
ENQUIRY_CHANNEL_WEB = "web"
ENQUIRY_NAME_REQUIRED = "Enter a contact name."
ENQUIRY_EMAIL_INVALID = "Enter a valid email address."
ENQUIRY_MESSAGE_REQUIRED = "Enter a message."
ENQUIRY_MESSAGE_TOO_LONG = "This message is too long."
ENQUIRY_CONSENT_REQUIRED = "Consent is required."
ENQUIRY_PHONE_INVALID = "Enter a phone number in international format."
ENQUIRY_FIELD_REJECTED = "This field is not accepted."
ENQUIRY_INVALID = "This enquiry could not be accepted."
ENQUIRY_TRANSITION_INVALID = "This enquiry status change is not allowed."
ASSIGNEE_NOT_ELIGIBLE = "This assignee is not eligible."
PUBLIC_ENQUIRY_FIELDS = frozenset(
    {
        "contact_name",
        "contact_email",
        "contact_phone",
        "message",
        "consent",
    }
)


class EnquiryStatus:
    NEW = "new"
    CONTACTED = "contacted"
    QUALIFIED = "qualified"
    UNQUALIFIED = "unqualified"
    CONVERTED = "converted"
    SPAM = "spam"
    CLOSED = "closed"

    CHOICES = (
        (NEW, "New"),
        (CONTACTED, "Contacted"),
        (QUALIFIED, "Qualified"),
        (UNQUALIFIED, "Unqualified"),
        (CONVERTED, "Converted"),
        (SPAM, "Spam"),
        (CLOSED, "Closed"),
    )
    VALUES = frozenset(value for value, _label in CHOICES)
    TERMINAL = frozenset({CONVERTED, SPAM, CLOSED})


ENQUIRY_TRANSITIONS = {
    EnquiryStatus.NEW: frozenset(
        {
            EnquiryStatus.CONTACTED,
            EnquiryStatus.SPAM,
            EnquiryStatus.CLOSED,
        }
    ),
    EnquiryStatus.CONTACTED: frozenset(
        {
            EnquiryStatus.QUALIFIED,
            EnquiryStatus.UNQUALIFIED,
            EnquiryStatus.SPAM,
            EnquiryStatus.CLOSED,
        }
    ),
    EnquiryStatus.QUALIFIED: frozenset(
        {
            EnquiryStatus.CONVERTED,
            EnquiryStatus.SPAM,
            EnquiryStatus.CLOSED,
        }
    ),
    EnquiryStatus.UNQUALIFIED: frozenset(
        {
            EnquiryStatus.CLOSED,
        }
    ),
    EnquiryStatus.CONVERTED: frozenset(),
    EnquiryStatus.SPAM: frozenset(),
    EnquiryStatus.CLOSED: frozenset(),
}


class EnquiryEventAction:
    CREATED = "enquiry.created"
    VIEWED = "enquiry.viewed"
    ASSIGNED = "enquiry.assigned"
    UNASSIGNED = "enquiry.unassigned"
    CONTACTED = "enquiry.contacted"
    STATUS_CHANGED = "enquiry.status_changed"
    ANONYMIZED = "enquiry.anonymized"

    CHOICES = (
        (CREATED, "Enquiry created"),
        (VIEWED, "Enquiry viewed"),
        (ASSIGNED, "Enquiry assigned"),
        (UNASSIGNED, "Enquiry unassigned"),
        (CONTACTED, "Enquiry contacted"),
        (STATUS_CHANGED, "Enquiry status changed"),
        (ANONYMIZED, "Enquiry anonymized"),
    )
    VALUES = frozenset(value for value, _label in CHOICES)


def enquiry_transition_allowed(current: str, new: str) -> bool:
    """Return whether a status change is on the enquiry graph.

    The database accepts any known status. The enquiry service must call
    this before writing a new status. Assignment does not use this graph.
    """

    return new in ENQUIRY_TRANSITIONS.get(current, frozenset())


# Identifiers and codes only. Contact text, messages, and secrets are absent
# on purpose. A check constraint cannot allowlist JSON keys without a stored
# function, so the model applies this before insert.
ENQUIRY_EVENT_METADATA_KEYS = frozenset(
    {
        "from_status",
        "to_status",
        "assignee_id",
        "previous_assignee_id",
        "channel",
        "consent_version",
        "reason_code",
    }
)
_ENQUIRY_METADATA_VALUE_MAX = 64


def enquiry_event_metadata(metadata: dict | None) -> dict:
    """Return allowlisted enquiry-event metadata or refuse the write.

    Values are short codes and identifiers. The stored row must not carry a
    contact name, email, phone, message, or credential.
    """

    if metadata is None:
        return {}
    if not isinstance(metadata, dict):
        raise ValueError("Enquiry event metadata must be an object.")
    cleaned: dict = {}
    for key, value in metadata.items():
        if key not in ENQUIRY_EVENT_METADATA_KEYS:
            raise ValueError("Enquiry event metadata contains a field that is not allowed.")
        if isinstance(value, bool) or value is None or isinstance(value, int):
            cleaned[key] = value
            continue
        if (
            isinstance(value, str)
            and len(value) <= _ENQUIRY_METADATA_VALUE_MAX
            and "@" not in value
            and "\n" not in value
            and "\x00" not in value
        ):
            cleaned[key] = value
            continue
        raise ValueError("Enquiry event metadata contains a value that is not allowed.")
    return cleaned
