"""Auction listings. Partners submit drafts. Staff publish them."""

from __future__ import annotations

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.solutions.domain import (
    INVALID,
    NOT_FOUND,
    PUBLISH_BLOCKED,
    STATUS_INVALID,
    AuctionCategory,
    AuctionStatus,
    DocumentKind,
    DocumentVisibility,
    Tenure,
    new_reference,
    staff_auction_transition_allowed,
)
from apps.solutions.errors import SolutionsError
from apps.solutions.models import AuctionDocument, AuctionListing
from apps.solutions.services.access import (
    ensure_staff,
    operating_organization_ids,
    require_operator,
)
from apps.solutions.services.fields import (
    active_organization,
    choice,
    flag,
    optional_datetime,
    optional_email,
    optional_money,
    optional_phone,
    reject_unknown,
    require_mapping,
    text,
)

PARTNER_FIELDS = frozenset(
    {
        "organization_id",
        "title",
        "category",
        "description",
        "county",
        "town",
        "location_label",
        "size_label",
        "tenure",
        "guide_price_minor",
        "reserve_price_minor",
        "auction_at",
        "viewing_notes",
        "registration_deadline",
        "terms",
        "deposit_requirements",
        "payment_instructions",
        "contact_name",
        "contact_email",
        "contact_phone",
        "institution_name",
        "auctioneer_name",
        "auctioneer_licence_reference",
    }
)
STAFF_FIELDS = PARTNER_FIELDS | frozenset(
    {
        "publish_reserve",
        "auctioneer_verified",
        "institution_id",
        "auctioneer_id",
    }
)
PARTNER_MUTABLE = frozenset({AuctionStatus.DRAFT, AuctionStatus.PENDING})
STAFF_MUTABLE = frozenset(
    {
        AuctionStatus.DRAFT,
        AuctionStatus.PENDING,
        AuctionStatus.APPROVED,
        AuctionStatus.PUBLISHED,
        AuctionStatus.REGISTRATION_OPEN,
        AuctionStatus.SCHEDULED,
    }
)


def public_auctions():
    return AuctionListing.objects.filter(status__in=AuctionStatus.PUBLIC).order_by(
        "-auction_at",
        "-published_at",
    )


def public_auction(reference: str) -> AuctionListing:
    auction = public_auctions().filter(reference=reference).first()
    if auction is None:
        raise SolutionsError(NOT_FOUND, status_code=404)
    return auction


def partner_auctions(user):
    return AuctionListing.objects.filter(
        submitting_organization_id__in=operating_organization_ids(user)
    ).order_by("-created_at")


def _values(fields, *, staff: bool) -> dict:
    body = require_mapping(fields)
    reject_unknown(body, STAFF_FIELDS if staff else PARTNER_FIELDS)
    organization = None
    if "organization_id" in body:
        organization = active_organization(body.get("organization_id"))
    reserve = optional_money(body.get("reserve_price_minor"))
    publish_reserve = flag(body.get("publish_reserve")) if staff else False
    if publish_reserve and reserve is None:
        raise SolutionsError(INVALID, status_code=400)
    verified = flag(body.get("auctioneer_verified")) if staff else False
    licence = text(body.get("auctioneer_licence_reference", ""), maximum=80)
    if verified and licence == "":
        raise SolutionsError(INVALID, status_code=400)
    return {
        "submitting_organization": organization,
        "title": text(body.get("title"), maximum=200, required=True),
        "category": choice(body.get("category"), AuctionCategory.VALUES),
        "description": text(body.get("description"), maximum=8000, required=True),
        "county": text(body.get("county"), maximum=80, required=True),
        "town": text(body.get("town", ""), maximum=80),
        "location_label": text(body.get("location_label", ""), maximum=200),
        "size_label": text(body.get("size_label", ""), maximum=80),
        "tenure": choice(body.get("tenure", Tenure.UNKNOWN), Tenure.VALUES),
        "guide_price_minor": optional_money(body.get("guide_price_minor")),
        "reserve_price_minor": reserve,
        "publish_reserve": publish_reserve,
        "auction_at": optional_datetime(body.get("auction_at")),
        "viewing_notes": text(body.get("viewing_notes", ""), maximum=4000),
        "registration_deadline": optional_datetime(body.get("registration_deadline")),
        "terms": text(body.get("terms", ""), maximum=8000),
        "deposit_requirements": text(body.get("deposit_requirements", ""), maximum=4000),
        "payment_instructions": text(body.get("payment_instructions", ""), maximum=4000),
        "contact_name": text(body.get("contact_name", ""), maximum=160),
        "contact_email": optional_email(body.get("contact_email", "")),
        "contact_phone": optional_phone(body.get("contact_phone", "")),
        "institution_name": text(body.get("institution_name", ""), maximum=200),
        "auctioneer_name": text(body.get("auctioneer_name", ""), maximum=200),
        "auctioneer_licence_reference": licence,
        "auctioneer_verified": verified,
        "institution": active_organization(body.get("institution_id")) if staff else None,
        "auctioneer": active_organization(body.get("auctioneer_id")) if staff else None,
    }


def _ready_for_public(auction: AuctionListing, status: str) -> None:
    named = auction.institution_name or auction.auctioneer_name or auction.contact_email
    if not named:
        raise SolutionsError(PUBLISH_BLOCKED, status_code=400)
    if status == AuctionStatus.SCHEDULED and auction.auction_at is None:
        raise SolutionsError(PUBLISH_BLOCKED, status_code=400)


def create_partner_auction(*, actor, fields: object) -> AuctionListing:
    values = _values(fields, staff=False)
    organization = values["submitting_organization"]
    if organization is None:
        raise SolutionsError(INVALID, status_code=400)
    require_operator(actor, organization.id)
    return _insert(values, actor=actor)


def create_staff_auction(*, actor, fields: object) -> AuctionListing:
    ensure_staff(actor)
    values = _values(fields, staff=True)
    return _insert(values, actor=actor)


def _insert(values: dict, *, actor) -> AuctionListing:
    for _ in range(5):
        try:
            return AuctionListing.objects.create(
                reference=new_reference("AUC"),
                submitted_by=actor,
                status=AuctionStatus.DRAFT,
                **values,
            )
        except IntegrityError:
            continue
    raise SolutionsError(INVALID, status_code=400)


def _owned(user, reference: str, *, staff: bool) -> AuctionListing:
    auction = AuctionListing.objects.filter(reference=reference).first()
    if auction is None:
        raise SolutionsError(NOT_FOUND, status_code=404)
    if staff:
        ensure_staff(user)
        return auction
    if auction.submitting_organization_id not in operating_organization_ids(user):
        raise SolutionsError(NOT_FOUND, status_code=404)
    return auction


def update_partner_auction(*, actor, reference: str, fields: object) -> AuctionListing:
    auction = _owned(actor, reference, staff=False)
    if auction.status not in PARTNER_MUTABLE:
        raise SolutionsError(STATUS_INVALID, status_code=400)
    values = _values(fields, staff=False)
    if values["submitting_organization"] is None:
        raise SolutionsError(INVALID, status_code=400)
    if values["submitting_organization"].id != auction.submitting_organization_id:
        raise SolutionsError(INVALID, status_code=400)
    for key in ("institution", "auctioneer", "publish_reserve", "auctioneer_verified"):
        values.pop(key)
    return _apply(auction, values)


def update_staff_auction(*, actor, reference: str, fields: object) -> AuctionListing:
    auction = _owned(actor, reference, staff=True)
    if auction.status not in STAFF_MUTABLE:
        raise SolutionsError(STATUS_INVALID, status_code=400)
    return _apply(auction, _values(fields, staff=True))


def _apply(auction: AuctionListing, values: dict) -> AuctionListing:
    for key, value in values.items():
        setattr(auction, key, value)
    auction.save()
    return auction


def set_partner_status(*, actor, reference: str) -> AuctionListing:
    """Partners have one forward step: draft to pending verification."""

    auction = _owned(actor, reference, staff=False)
    if auction.status != AuctionStatus.DRAFT:
        raise SolutionsError(STATUS_INVALID, status_code=400)
    auction.status = AuctionStatus.PENDING
    auction.save(update_fields=["status", "updated_at"])
    return auction


def set_staff_status(*, actor, reference: str, status: str) -> AuctionListing:
    ensure_staff(actor)
    if status not in AuctionStatus.VALUES:
        raise SolutionsError(INVALID, status_code=400)
    with transaction.atomic():
        auction = AuctionListing.objects.select_for_update().filter(reference=reference).first()
        if auction is None:
            raise SolutionsError(NOT_FOUND, status_code=404)
        if not staff_auction_transition_allowed(auction.status, status):
            raise SolutionsError(STATUS_INVALID, status_code=400)
        if status in AuctionStatus.PUBLIC:
            _ready_for_public(auction, status)
            if auction.published_at is None:
                auction.published_at = timezone.now()
        auction.status = status
        auction.save(update_fields=["status", "published_at", "updated_at"])
    return auction


def store_document(
    *,
    actor,
    reference: str,
    upload,
    content_type: str,
    kind: str,
    visibility: str,
    caption: str,
    staff: bool,
) -> AuctionDocument:
    from django.conf import settings
    from django.core.files.base import ContentFile

    auction = _owned(actor, reference, staff=staff)
    if not staff and auction.status not in PARTNER_MUTABLE:
        raise SolutionsError(STATUS_INVALID, status_code=400)
    if kind not in DocumentKind.VALUES:
        raise SolutionsError(INVALID, status_code=400)
    declared = content_type if isinstance(content_type, str) else ""
    signatures = {
        "application/pdf": (b"%PDF", DocumentKind.DOCUMENT),
        "image/jpeg": (b"\xff\xd8\xff", DocumentKind.IMAGE),
        "image/png": (b"\x89PNG\r\n\x1a\n", DocumentKind.IMAGE),
    }
    if declared not in signatures:
        raise SolutionsError(INVALID, status_code=400)
    signature, expected_kind = signatures[declared]
    if kind != expected_kind:
        raise SolutionsError(INVALID, status_code=400)
    if upload is None:
        raise SolutionsError(INVALID, status_code=400)
    content = upload.read()
    limit = settings.MEDIA_MAX_DOCUMENT_BYTES
    if not content or len(content) > limit or not content.startswith(signature):
        raise SolutionsError(INVALID, status_code=400)
    chosen = visibility if staff else DocumentVisibility.PRIVATE
    if chosen not in DocumentVisibility.VALUES:
        raise SolutionsError(INVALID, status_code=400)
    document = AuctionDocument(
        auction=auction,
        kind=kind,
        visibility=chosen,
        original_name=text(getattr(upload, "name", ""), maximum=200)[:200],
        content_type=declared,
        caption=text(caption, maximum=300),
    )
    suffix = ".pdf" if declared == "application/pdf" else ".img"
    document.upload.save(f"upload{suffix}", ContentFile(content), save=True)
    return document


def public_document(document_id):
    document = (
        AuctionDocument.objects.select_related("auction")
        .filter(pk=document_id, visibility=DocumentVisibility.PUBLIC)
        .first()
    )
    if document is None or document.auction.status not in AuctionStatus.PUBLIC:
        raise SolutionsError(NOT_FOUND, status_code=404)
    return document


def private_document(*, actor, document_id):
    document = AuctionDocument.objects.select_related("auction").filter(pk=document_id).first()
    if document is None:
        raise SolutionsError(NOT_FOUND, status_code=404)
    if getattr(actor, "is_staff", False) or getattr(actor, "is_superuser", False):
        ensure_staff(actor)
        return document
    auction = document.auction
    allowed = operating_organization_ids(actor)
    related = {
        auction.submitting_organization_id,
        auction.institution_id,
        auction.auctioneer_id,
    }
    if not allowed.intersection(related):
        raise SolutionsError(NOT_FOUND, status_code=404)
    return document
