"""Commercial catalogue rules.

GLITTERS records partner-approved opportunities and routes enquiries.
It does not price insurance, quote a market, take a bid, or settle a sale.
A licence reference stored here is a label supplied by a partner. The
`verified` flags are the only claims this platform makes about credentials.
"""

from __future__ import annotations

import re
import secrets

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

NOT_FOUND = "Not found."
PERMISSION_DENIED = "You do not have access to this record."
INVALID = "This request could not be accepted."
FIELD_REJECTED = "This field is not accepted."
CONSENT_REQUIRED = "Consent is required."
STATUS_INVALID = "This status change is not available."
PUBLISH_BLOCKED = "This record cannot be published yet."
CREDENTIAL_CLAIM = (
    "A provider cannot be described as licensed or certified until the credential is checked."
)
MEDICAL_REJECTED = "Medical information is not collected on this form."
RESERVE_UNPUBLISHED = "A reserve price needs an explicit decision to publish it."
API_NOT_CONFIGURED = "No authorised quotation API is configured for this product."

CONSENT_VERSION = "2026-11-solutions"
CHANNEL_WEB = "web"
ACK_SENT = "sent"
ACK_FAILED = "failed"

NAME_MAX = 160
MESSAGE_MAX = 5000
PHONE_MAX = 32
DETAIL_MAX = 300
NOTE_MAX = 2000

PREFERRED_EMAIL = "email"
PREFERRED_PHONE = "phone"
PREFERRED = frozenset({PREFERRED_EMAIL, PREFERRED_PHONE})


class AuctionStatus:
    DRAFT = "draft"
    PENDING = "pending_verification"
    APPROVED = "approved"
    PUBLISHED = "published"
    REGISTRATION_OPEN = "registration_open"
    SCHEDULED = "auction_scheduled"
    SOLD = "sold"
    WITHDRAWN = "withdrawn"
    EXPIRED = "expired"

    CHOICES = (
        (DRAFT, "Draft"),
        (PENDING, "Pending verification"),
        (APPROVED, "Approved"),
        (PUBLISHED, "Published"),
        (REGISTRATION_OPEN, "Registration open"),
        (SCHEDULED, "Auction scheduled"),
        (SOLD, "Sold"),
        (WITHDRAWN, "Withdrawn"),
        (EXPIRED, "Expired"),
    )
    VALUES = frozenset(value for value, _label in CHOICES)
    PUBLIC = frozenset({PUBLISHED, REGISTRATION_OPEN, SCHEDULED, SOLD})


PARTNER_AUCTION_TRANSITIONS = {
    AuctionStatus.DRAFT: frozenset({AuctionStatus.PENDING}),
}

STAFF_AUCTION_TRANSITIONS = {
    AuctionStatus.DRAFT: frozenset(
        {AuctionStatus.PENDING, AuctionStatus.APPROVED, AuctionStatus.WITHDRAWN}
    ),
    AuctionStatus.PENDING: frozenset(
        {AuctionStatus.DRAFT, AuctionStatus.APPROVED, AuctionStatus.WITHDRAWN}
    ),
    AuctionStatus.APPROVED: frozenset(
        {AuctionStatus.PUBLISHED, AuctionStatus.DRAFT, AuctionStatus.WITHDRAWN}
    ),
    AuctionStatus.PUBLISHED: frozenset(
        {
            AuctionStatus.REGISTRATION_OPEN,
            AuctionStatus.SCHEDULED,
            AuctionStatus.SOLD,
            AuctionStatus.WITHDRAWN,
            AuctionStatus.EXPIRED,
        }
    ),
    AuctionStatus.REGISTRATION_OPEN: frozenset(
        {
            AuctionStatus.SCHEDULED,
            AuctionStatus.SOLD,
            AuctionStatus.WITHDRAWN,
            AuctionStatus.EXPIRED,
        }
    ),
    AuctionStatus.SCHEDULED: frozenset(
        {AuctionStatus.SOLD, AuctionStatus.WITHDRAWN, AuctionStatus.EXPIRED}
    ),
    AuctionStatus.SOLD: frozenset(),
    AuctionStatus.WITHDRAWN: frozenset(),
    AuctionStatus.EXPIRED: frozenset(),
}


class AuctionCategory:
    RESIDENTIAL = "residential"
    COMMERCIAL = "commercial"
    LAND = "land"
    MIXED = "mixed"
    OTHER = "other"
    CHOICES = (
        (RESIDENTIAL, "Residential"),
        (COMMERCIAL, "Commercial"),
        (LAND, "Land"),
        (MIXED, "Mixed use"),
        (OTHER, "Other"),
    )
    VALUES = frozenset(value for value, _label in CHOICES)


class Tenure:
    FREEHOLD = "freehold"
    LEASEHOLD = "leasehold"
    UNKNOWN = "unknown"
    CHOICES = (
        (FREEHOLD, "Freehold"),
        (LEASEHOLD, "Leasehold"),
        (UNKNOWN, "Not stated"),
    )
    VALUES = frozenset(value for value, _label in CHOICES)


class DocumentKind:
    IMAGE = "image"
    DOCUMENT = "document"
    CHOICES = ((IMAGE, "Image"), (DOCUMENT, "Document"))
    VALUES = frozenset(value for value, _label in CHOICES)


class DocumentVisibility:
    PUBLIC = "public"
    PRIVATE = "private"
    CHOICES = ((PUBLIC, "Public"), (PRIVATE, "Private"))
    VALUES = frozenset(value for value, _label in CHOICES)


class ProjectCategory:
    RESIDENTIAL = "residential"
    COMMERCIAL = "commercial"
    MIXED = "mixed"
    LAND = "land"
    OTHER = "other"
    CHOICES = AuctionCategory.CHOICES
    VALUES = AuctionCategory.VALUES


class ProjectStatus:
    DRAFT = "draft"
    UPCOMING = "upcoming"
    LAUNCHING = "launching_soon"
    UNDER_DEVELOPMENT = "under_development"
    AVAILABLE = "available"
    FULL = "fully_subscribed"
    COMPLETED = "completed"
    CHOICES = (
        (DRAFT, "Draft"),
        (UPCOMING, "Upcoming"),
        (LAUNCHING, "Launching soon"),
        (UNDER_DEVELOPMENT, "Under development"),
        (AVAILABLE, "Available"),
        (FULL, "Fully subscribed or sold out"),
        (COMPLETED, "Completed"),
    )
    VALUES = frozenset(value for value, _label in CHOICES)
    PUBLIC = VALUES - {DRAFT}


class InsuranceLine:
    GENERAL = "general"
    LIFE = "life"
    CHOICES = ((GENERAL, "General insurance"), (LIFE, "Life insurance"))
    VALUES = frozenset(value for value, _label in CHOICES)


class InsuranceCategory:
    MOTOR = "motor"
    HOME = "home"
    FIRE = "fire"
    BUSINESS = "business"
    TRAVEL = "travel"
    PERSONAL_ACCIDENT = "personal_accident"
    OTHER = "other"
    INDIVIDUAL_LIFE = "individual_life"
    FAMILY = "family_protection"
    OTHER_LIFE = "other_life"
    CHOICES = (
        (MOTOR, "Motor"),
        (HOME, "Home and property"),
        (FIRE, "Fire"),
        (BUSINESS, "Business"),
        (TRAVEL, "Travel"),
        (PERSONAL_ACCIDENT, "Personal accident"),
        (OTHER, "Other partner product"),
        (INDIVIDUAL_LIFE, "Individual life cover"),
        (FAMILY, "Family protection"),
        (OTHER_LIFE, "Other life product"),
    )
    VALUES = frozenset(value for value, _label in CHOICES)
    GENERAL = frozenset({MOTOR, HOME, FIRE, BUSINESS, TRAVEL, PERSONAL_ACCIDENT, OTHER})
    LIFE = frozenset({INDIVIDUAL_LIFE, FAMILY, OTHER_LIFE})


class QuotationMode:
    REQUEST = "request"
    API = "api"
    CHOICES = (
        (REQUEST, "Request a quote"),
        (API, "Authorised partner API"),
    )
    VALUES = frozenset(value for value, _label in CHOICES)


class CatalogueStatus:
    DRAFT = "draft"
    PUBLISHED = "published"
    WITHDRAWN = "withdrawn"
    CHOICES = (
        (DRAFT, "Draft"),
        (PUBLISHED, "Published"),
        (WITHDRAWN, "Withdrawn"),
    )
    VALUES = frozenset(value for value, _label in CHOICES)


class InvestmentCategory:
    EQUITY = "equity"
    BOND = "bond"
    REIT = "reit"
    OTHER = "other"
    CHOICES = (
        (EQUITY, "NSE-listed equity"),
        (BOND, "Bond"),
        (REIT, "REIT"),
        (OTHER, "Other eligible product"),
    )
    VALUES = frozenset(value for value, _label in CHOICES)


class ServiceGroup:
    PROFESSIONAL = "professional_legal"
    CONSTRUCTION = "construction"
    FACILITIES = "facilities"
    ADMINISTRATION = "administration"
    CHOICES = (
        (PROFESSIONAL, "Professional and legal services"),
        (CONSTRUCTION, "Construction and development"),
        (FACILITIES, "Property and facilities management"),
        (ADMINISTRATION, "Property administration and compliance"),
    )
    VALUES = frozenset(value for value, _label in CHOICES)


# Every service the client named. Descriptions do not call anyone licensed.
SERVICE_CATALOGUE = (
    (
        ServiceGroup.PROFESSIONAL,
        "legal-services",
        "Legal services",
        "Request an introduction for property-related legal work.",
    ),
    (
        ServiceGroup.PROFESSIONAL,
        "quantity-surveyors",
        "Quantity surveyors",
        "Request an introduction to a quantity surveyor.",
    ),
    (
        ServiceGroup.PROFESSIONAL,
        "land-surveyors",
        "Land surveyors",
        "Request an introduction to a land surveyor.",
    ),
    (
        ServiceGroup.PROFESSIONAL,
        "valuation",
        "Valuation",
        "Request an introduction for a property valuation.",
    ),
    (
        ServiceGroup.PROFESSIONAL,
        "due-diligence",
        "Due diligence",
        "Request an introduction for property due diligence.",
    ),
    (
        ServiceGroup.CONSTRUCTION,
        "contractors",
        "Contractors",
        "Request a quotation from a construction contractor.",
    ),
    (
        ServiceGroup.CONSTRUCTION,
        "renovations",
        "Renovations",
        "Request a quotation for renovation work.",
    ),
    (
        ServiceGroup.CONSTRUCTION,
        "interior-design",
        "Interior design",
        "Request a quotation for interior design.",
    ),
    (
        ServiceGroup.CONSTRUCTION,
        "landscaping",
        "Landscaping",
        "Request a quotation for landscaping.",
    ),
    (
        ServiceGroup.CONSTRUCTION,
        "painters",
        "Painters",
        "Request a quotation for painting.",
    ),
    (
        ServiceGroup.CONSTRUCTION,
        "borehole-drilling",
        "Borehole drilling",
        "Request a quotation for borehole drilling.",
    ),
    (
        ServiceGroup.CONSTRUCTION,
        "borehole-maintenance",
        "Borehole maintenance",
        "Request a quotation for borehole maintenance.",
    ),
    (
        ServiceGroup.CONSTRUCTION,
        "biodigesters",
        "Biodigesters",
        "Request a quotation for a biodigester.",
    ),
    (
        ServiceGroup.FACILITIES,
        "property-management",
        "Property management",
        "Request property management support.",
    ),
    (
        ServiceGroup.FACILITIES,
        "caretakers",
        "Caretakers",
        "Request caretaker services.",
    ),
    (
        ServiceGroup.FACILITIES,
        "cleaning-services",
        "Cleaning services",
        "Request cleaning services.",
    ),
    (
        ServiceGroup.FACILITIES,
        "security-services",
        "Security services",
        "Request security services.",
    ),
    (
        ServiceGroup.FACILITIES,
        "cctv-installation",
        "CCTV installation",
        "Request CCTV installation.",
    ),
    (
        ServiceGroup.FACILITIES,
        "fire-equipment",
        "Fire equipment",
        "Request fire equipment supply or servicing.",
    ),
    (
        ServiceGroup.ADMINISTRATION,
        "land-rates",
        "Land rates assistance",
        "Request assistance with land rates.",
    ),
    (
        ServiceGroup.ADMINISTRATION,
        "landlord-kra-returns",
        "Landlord KRA returns",
        "Request assistance with landlord KRA returns.",
    ),
)


class EnquiryCategory:
    AUCTION = "auction"
    INSURANCE = "insurance"
    PROJECT = "project"
    INVESTMENT = "investment"
    SERVICE = "service"
    CHOICES = (
        (AUCTION, "Auction"),
        (INSURANCE, "Insurance"),
        (PROJECT, "Project"),
        (INVESTMENT, "Investment"),
        (SERVICE, "Professional service"),
    )
    VALUES = frozenset(value for value, _label in CHOICES)


class EnquiryIntent:
    INFORMATION = "information"
    VIEWING = "viewing"
    REGISTER = "register_interest"
    PARTICIPATION = "participation"
    CONTACT = "contact"
    QUOTE = "quote"
    CHOICES = (
        (INFORMATION, "Request information"),
        (VIEWING, "Book a viewing"),
        (REGISTER, "Register interest"),
        (PARTICIPATION, "Request participation instructions"),
        (CONTACT, "Contact the responsible party"),
        (QUOTE, "Request a quotation"),
    )
    VALUES = frozenset(value for value, _label in CHOICES)


AUCTION_INTENTS = frozenset(
    {
        EnquiryIntent.INFORMATION,
        EnquiryIntent.VIEWING,
        EnquiryIntent.REGISTER,
        EnquiryIntent.PARTICIPATION,
        EnquiryIntent.CONTACT,
    }
)
QUOTE_INTENTS = frozenset({EnquiryIntent.QUOTE, EnquiryIntent.INFORMATION})
INFO_INTENTS = frozenset({EnquiryIntent.INFORMATION})


class EnquiryStatus:
    NEW = "new"
    SUBMITTED = "submitted_to_partner"
    REVIEW = "under_review"
    QUOTED = "quotation_provided"
    ACCEPTED = "accepted"
    CLOSED = "closed"
    CHOICES = (
        (NEW, "New"),
        (SUBMITTED, "Submitted to partner"),
        (REVIEW, "Under review"),
        (QUOTED, "Quotation provided"),
        (ACCEPTED, "Accepted by customer"),
        (CLOSED, "Closed"),
    )
    VALUES = frozenset(value for value, _label in CHOICES)


ENQUIRY_TRANSITIONS = {
    EnquiryStatus.NEW: frozenset(
        {EnquiryStatus.SUBMITTED, EnquiryStatus.REVIEW, EnquiryStatus.CLOSED}
    ),
    EnquiryStatus.SUBMITTED: frozenset(
        {EnquiryStatus.REVIEW, EnquiryStatus.QUOTED, EnquiryStatus.CLOSED}
    ),
    EnquiryStatus.REVIEW: frozenset({EnquiryStatus.QUOTED, EnquiryStatus.CLOSED}),
    EnquiryStatus.QUOTED: frozenset({EnquiryStatus.ACCEPTED, EnquiryStatus.CLOSED}),
    EnquiryStatus.ACCEPTED: frozenset({EnquiryStatus.CLOSED}),
    EnquiryStatus.CLOSED: frozenset(),
}


class EnquiryEventAction:
    CREATED = "enquiry.created"
    ASSIGNED = "enquiry.assigned"
    STATUS_CHANGED = "enquiry.status_changed"
    FOLLOWED_UP = "enquiry.followed_up"
    ANONYMIZED = "enquiry.anonymized"
    CHOICES = (
        (CREATED, "Created"),
        (ASSIGNED, "Assigned"),
        (STATUS_CHANGED, "Status changed"),
        (FOLLOWED_UP, "Followed up"),
        (ANONYMIZED, "Anonymized"),
    )
    VALUES = frozenset(value for value, _label in CHOICES)


EVENT_METADATA_KEYS = frozenset(
    {
        "from_status",
        "to_status",
        "channel",
        "consent_version",
        "acknowledgement",
        "quotation_outcome",
        "organization_id",
        "reason_code",
    }
)
_METADATA_VALUE_MAX = 64

# Empty on purpose. A premium is returned only by an adapter a licensed
# partner has configured. No adapter is registered in this release.
INSURANCE_QUOTE_ADAPTERS: dict = {}

QUOTATION_REQUEST_RECORDED = "request_recorded"
QUOTATION_AWAITING_PARTNER = "awaiting_authorised_partner"

LIFE_DETAILS = frozenset({"individual", "family", "partner_product"})
MEDICAL_KEYS = frozenset(
    {
        "medical_details",
        "medical_history",
        "health_conditions",
        "diagnosis",
        "medications",
        "date_of_birth",
    }
)
CREDENTIAL_WORDS = ("licensed", "certified", "licence no", "license no")

PUBLIC_ENQUIRY_FIELDS = frozenset(
    {
        "contact_name",
        "contact_email",
        "contact_phone",
        "message",
        "consent",
        "preferred_contact",
        "intent",
        "detail",
    }
)


def new_reference(prefix: str) -> str:
    return prefix + secrets.token_hex(4).upper()


def enquiry_transition_allowed(current: str, new: str) -> bool:
    return new in ENQUIRY_TRANSITIONS.get(current, frozenset())


def staff_auction_transition_allowed(current: str, new: str) -> bool:
    return new in STAFF_AUCTION_TRANSITIONS.get(current, frozenset())


def event_metadata(metadata: dict | None) -> dict:
    """Allow short codes only. Contact text cannot be stored on an event."""

    if metadata is None:
        return {}
    if not isinstance(metadata, dict):
        raise ValueError("Enquiry event metadata must be an object.")
    cleaned: dict = {}
    for key, value in metadata.items():
        if key not in EVENT_METADATA_KEYS:
            raise ValueError("Enquiry event metadata contains a field that is not allowed.")
        if isinstance(value, bool) or value is None:
            cleaned[key] = value
            continue
        if (
            isinstance(value, str)
            and len(value) <= _METADATA_VALUE_MAX
            and "@" not in value
            and "\n" not in value
            and "\x00" not in value
        ):
            cleaned[key] = value
            continue
        raise ValueError("Enquiry event metadata contains a value that is not allowed.")
    return cleaned


def contains_unchecked_credential_claim(text: str) -> bool:
    lowered = text.casefold()
    return any(word in lowered for word in CREDENTIAL_WORDS)


def public_investment_price(opportunity) -> dict | None:
    """Publish a price only when an authorised source and timestamp exist."""

    if (
        opportunity.price_minor is None
        or not opportunity.data_source
        or opportunity.price_as_of is None
    ):
        return None
    return {
        "amount_minor": opportunity.price_minor,
        "currency": opportunity.price_currency,
        "data_source": opportunity.data_source,
        "as_of": opportunity.price_as_of,
    }


def quotation_outcome(product) -> dict:
    """Choose request-a-quote, or pass through one configured partner adapter.

    The adapter registry ships empty. Nothing in this module invents a
    premium when the registry has no adapter for the product.
    """

    if product.quotation_mode != QuotationMode.API or not product.api_configured:
        return {"mode": QuotationMode.REQUEST, "state": QUOTATION_REQUEST_RECORDED}
    adapter = INSURANCE_QUOTE_ADAPTERS.get(product.slug)
    if adapter is None:
        return {"mode": QuotationMode.API, "state": QUOTATION_AWAITING_PARTNER}
    raw = adapter(product)
    if not isinstance(raw, dict):
        return {"mode": QuotationMode.API, "state": QUOTATION_AWAITING_PARTNER}
    passed: dict = {"mode": QuotationMode.API, "state": "partner_quotation"}
    reference = raw.get("partner_reference")
    if isinstance(reference, str) and reference.strip() and "@" not in reference:
        passed["partner_reference"] = reference.strip()[:64]
    summary = raw.get("summary")
    if isinstance(summary, str) and summary.strip():
        passed["summary"] = summary.strip()[:300]
    premium = raw.get("premium_minor")
    if isinstance(premium, int) and not isinstance(premium, bool) and premium >= 0:
        passed["premium_minor"] = premium
        passed["currency"] = "KES"
        passed["premium_source"] = "authorised_partner_api"
    if len(passed) == 2:
        return {"mode": QuotationMode.API, "state": QUOTATION_AWAITING_PARTNER}
    return passed
