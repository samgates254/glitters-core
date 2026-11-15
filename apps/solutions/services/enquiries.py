"""Commercial enquiries. Public responses never include the contact record."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping

from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.template.loader import render_to_string
from django.utils import timezone

from apps.identity.domain import E164_RE, normalize_email
from apps.properties.services.audit import request_correlation_id
from apps.solutions.domain import (
    CONSENT_REQUIRED,
    CONSENT_VERSION,
    DETAIL_MAX,
    FIELD_REJECTED,
    INVALID,
    LIFE_DETAILS,
    MEDICAL_KEYS,
    MEDICAL_REJECTED,
    MESSAGE_MAX,
    NAME_MAX,
    NOT_FOUND,
    NOTE_MAX,
    PHONE_MAX,
    PUBLIC_ENQUIRY_FIELDS,
    STATUS_INVALID,
    AuctionStatus,
    EnquiryEventAction,
    EnquiryStatus,
    InsuranceLine,
    enquiry_transition_allowed,
    event_metadata,
    new_reference,
    quotation_outcome,
)
from apps.solutions.errors import SolutionsError
from apps.solutions.models import (
    CommercialEnquiry,
    CommercialEnquiryEvent,
    CommercialFollowUp,
)
from apps.solutions.services.access import ensure_staff, operating_organization_ids
from apps.solutions.services.fields import active_organization, require_mapping, text

logger = logging.getLogger("glitters.solutions")
_SEPARATORS = re.compile(r"[\s().-]")


def clean_public_enquiry(fields: object, *, intents: frozenset[str], life: bool) -> dict:
    body = require_mapping(fields)
    if set(body) & MEDICAL_KEYS or (life and _looks_medical(body)):
        raise SolutionsError(MEDICAL_REJECTED, status_code=400)
    if set(body) - PUBLIC_ENQUIRY_FIELDS:
        raise SolutionsError(FIELD_REJECTED, status_code=400)
    if body.get("consent") is not True:
        raise SolutionsError(CONSENT_REQUIRED, status_code=400)
    intent = body.get("intent")
    if not isinstance(intent, str) or intent not in intents:
        raise SolutionsError(INVALID, status_code=400)
    preferred = body.get("preferred_contact")
    if preferred not in {"email", "phone"}:
        raise SolutionsError(INVALID, status_code=400)
    detail = _detail(body.get("detail"), life=life)
    return {
        "contact_name": _name(body.get("contact_name")),
        "contact_email": _email(body.get("contact_email")),
        "contact_phone": _phone(body.get("contact_phone")),
        "message": _message(body.get("message")),
        "preferred_contact": preferred,
        "intent": intent,
        "detail": detail,
    }


def _looks_medical(body: Mapping) -> bool:
    detail = body.get("detail")
    if not isinstance(detail, str):
        return False
    return detail.strip() not in LIFE_DETAILS and detail.strip() != ""


def create_for_subject(
    *,
    subject: dict,
    fields: object,
    request=None,
) -> tuple[CommercialEnquiry, dict]:
    cleaned = clean_public_enquiry(fields, intents=subject["intents"], life=subject["life"])
    outcome = quotation_outcome(subject["product"]) if subject.get("product") is not None else {}
    organization = subject.get("organization")
    status = EnquiryStatus.SUBMITTED if organization is not None else EnquiryStatus.NEW
    state = outcome.get("state", "")
    try:
        with transaction.atomic():
            enquiry = _insert(
                cleaned,
                subject=subject,
                organization=organization,
                status=status,
                state=state,
                request=request,
            )
            transaction.on_commit(lambda: _acknowledge(enquiry.pk, outcome))
    except IntegrityError:
        logger.warning("Commercial enquiry was not stored.")
        raise SolutionsError(INVALID, status_code=400) from None
    logger.info("Commercial enquiry stored. reference=%s", enquiry.reference)
    return enquiry, outcome


def _insert(cleaned, *, subject, organization, status, state, request) -> CommercialEnquiry:
    for _ in range(5):
        try:
            enquiry = CommercialEnquiry.objects.create(
                reference=new_reference("ENQ"),
                category=subject["category"],
                intent=cleaned["intent"],
                subject_reference=subject["reference"],
                auction=subject.get("auction"),
                project=subject.get("project"),
                insurance_product=subject.get("product"),
                investment=subject.get("investment"),
                service_category=subject.get("service_category"),
                service_provider=subject.get("service_provider"),
                contact_name=cleaned["contact_name"],
                contact_email=cleaned["contact_email"],
                contact_phone=cleaned["contact_phone"],
                message=cleaned["message"],
                detail=cleaned["detail"],
                preferred_contact=cleaned["preferred_contact"],
                status=status,
                assigned_organization=organization,
                source="web",
                quotation_state=state,
                consent_at=timezone.now(),
                consent_version=CONSENT_VERSION,
            )
        except IntegrityError:
            continue
        CommercialEnquiryEvent.objects.create(
            enquiry=enquiry,
            actor=None,
            action=EnquiryEventAction.CREATED,
            request_id=request_correlation_id(request),
            metadata=event_metadata(
                {
                    "channel": "web",
                    "consent_version": CONSENT_VERSION,
                    "quotation_outcome": state,
                }
            ),
        )
        if organization is not None:
            CommercialEnquiryEvent.objects.create(
                enquiry=enquiry,
                actor=None,
                action=EnquiryEventAction.ASSIGNED,
                request_id=request_correlation_id(request),
                metadata=event_metadata(
                    {
                        "organization_id": str(organization.id),
                        "to_status": status,
                    }
                ),
            )
        return enquiry
    raise SolutionsError(INVALID, status_code=400)


def _acknowledge(enquiry_id, outcome: dict) -> None:
    enquiry = CommercialEnquiry.objects.filter(pk=enquiry_id).first()
    if enquiry is None or not enquiry.contact_email:
        return
    partner_premium = outcome.get("premium_source") == "authorised_partner_api"
    premium = outcome.get("premium_minor") if partner_premium else None
    context = {
        "reference": enquiry.reference,
        "state": outcome.get("state", ""),
        "summary": outcome.get("summary", ""),
        "premium_minor": premium,
        "currency": outcome.get("currency", ""),
    }
    try:
        send_mail(
            subject=f"GLITTERS enquiry {enquiry.reference}",
            message=render_to_string("solutions/enquiry_acknowledgement.txt", context),
            from_email=None,
            recipient_list=[enquiry.contact_email],
            html_message=render_to_string("solutions/enquiry_acknowledgement.html", context),
            fail_silently=False,
        )
        acknowledgement = "sent"
    except Exception:
        logger.warning("Commercial enquiry acknowledgement failed. reference=%s", enquiry.reference)
        acknowledgement = "failed"
    CommercialEnquiryEvent.objects.create(
        enquiry=enquiry,
        actor=None,
        action=EnquiryEventAction.FOLLOWED_UP,
        metadata=event_metadata({"acknowledgement": acknowledgement, "channel": "email"}),
    )


def staff_queryset():
    return CommercialEnquiry.objects.all().order_by("-created_at")


def partner_queryset(user):
    return CommercialEnquiry.objects.filter(
        assigned_organization_id__in=operating_organization_ids(user)
    ).order_by("-created_at")


def get_for_staff(actor, reference: str) -> CommercialEnquiry:
    ensure_staff(actor)
    enquiry = staff_queryset().filter(reference=reference).first()
    if enquiry is None:
        raise SolutionsError(NOT_FOUND, status_code=404)
    return enquiry


def get_for_partner(actor, reference: str) -> CommercialEnquiry:
    enquiry = partner_queryset(actor).filter(reference=reference).first()
    if enquiry is None:
        raise SolutionsError(NOT_FOUND, status_code=404)
    return enquiry


def update_enquiry(
    *,
    actor,
    reference: str,
    fields: object,
    staff: bool,
    request=None,
) -> CommercialEnquiry:
    body = require_mapping(fields)
    allowed = {"status", "note", "anonymize"}
    if staff:
        allowed |= {"assigned_organization_id"}
    if set(body) - allowed:
        raise SolutionsError(FIELD_REJECTED, status_code=400)
    with transaction.atomic():
        if staff:
            enquiry = (
                CommercialEnquiry.objects.select_for_update().filter(reference=reference).first()
            )
            ensure_staff(actor)
        else:
            enquiry = (
                CommercialEnquiry.objects.select_for_update()
                .filter(
                    reference=reference,
                    assigned_organization_id__in=operating_organization_ids(actor),
                )
                .first()
            )
        if enquiry is None:
            raise SolutionsError(NOT_FOUND, status_code=404)
        if enquiry.anonymized_at is not None:
            raise SolutionsError(STATUS_INVALID, status_code=400)
        if body.get("anonymize") is True:
            if not staff:
                raise SolutionsError(FIELD_REJECTED, status_code=400)
            _anonymize(enquiry, actor, request)
            return enquiry
        note = body.get("note")
        if note is not None:
            cleaned = text(note, maximum=NOTE_MAX, required=True)
            if staff:
                enquiry.internal_notes = cleaned
            else:
                enquiry.partner_response = cleaned
            CommercialFollowUp.objects.create(
                enquiry=enquiry,
                actor=actor,
                channel="staff" if staff else "partner",
                note=cleaned,
            )
            CommercialEnquiryEvent.objects.create(
                enquiry=enquiry,
                actor=actor,
                action=EnquiryEventAction.FOLLOWED_UP,
                request_id=request_correlation_id(request),
                metadata=event_metadata(
                    {
                        "channel": "staff" if staff else "partner",
                        "reason_code": "note",
                    }
                ),
            )
        new_status = body.get("status")
        if isinstance(new_status, str) and new_status != enquiry.status:
            if not enquiry_transition_allowed(enquiry.status, new_status):
                raise SolutionsError(STATUS_INVALID, status_code=400)
            previous = enquiry.status
            enquiry.status = new_status
            if new_status == EnquiryStatus.CLOSED:
                enquiry.closed_at = timezone.now()
            CommercialEnquiryEvent.objects.create(
                enquiry=enquiry,
                actor=actor,
                action=EnquiryEventAction.STATUS_CHANGED,
                request_id=request_correlation_id(request),
                metadata=event_metadata({"from_status": previous, "to_status": new_status}),
            )
        if staff and "assigned_organization_id" in body:
            organization = active_organization(body.get("assigned_organization_id"))
            enquiry.assigned_organization = organization
            CommercialEnquiryEvent.objects.create(
                enquiry=enquiry,
                actor=actor,
                action=EnquiryEventAction.ASSIGNED,
                request_id=request_correlation_id(request),
                metadata=event_metadata(
                    {"organization_id": "" if organization is None else str(organization.id)}
                ),
            )
        enquiry.save()
    return enquiry


def _anonymize(enquiry: CommercialEnquiry, actor, request) -> None:
    if enquiry.status != EnquiryStatus.CLOSED:
        raise SolutionsError(STATUS_INVALID, status_code=400)
    enquiry.contact_name = None
    enquiry.contact_email = None
    enquiry.contact_phone = None
    enquiry.message = None
    enquiry.detail = None
    enquiry.internal_notes = ""
    enquiry.partner_response = ""
    enquiry.anonymized_at = timezone.now()
    enquiry.save()
    enquiry.follow_ups.update(note="")
    CommercialEnquiryEvent.objects.create(
        enquiry=enquiry,
        actor=actor,
        action=EnquiryEventAction.ANONYMIZED,
        request_id=request_correlation_id(request),
        metadata=event_metadata({"reason_code": "retention"}),
    )


def subject_for_auction(auction) -> dict:
    from apps.solutions.domain import AUCTION_INTENTS, EnquiryCategory

    if auction.status not in AuctionStatus.PUBLIC:
        raise SolutionsError(NOT_FOUND, status_code=404)
    return {
        "category": EnquiryCategory.AUCTION,
        "intents": AUCTION_INTENTS,
        "reference": auction.reference,
        "auction": auction,
        "organization": (
            auction.submitting_organization or auction.institution or auction.auctioneer
        ),
        "life": False,
    }


def subject_for_project(project) -> dict:
    from apps.solutions.domain import INFO_INTENTS, EnquiryCategory

    return {
        "category": EnquiryCategory.PROJECT,
        "intents": INFO_INTENTS,
        "reference": project.slug,
        "project": project,
        "organization": project.developer,
        "life": False,
    }


def subject_for_product(product) -> dict:
    from apps.solutions.domain import QUOTE_INTENTS, EnquiryCategory

    return {
        "category": EnquiryCategory.INSURANCE,
        "intents": QUOTE_INTENTS,
        "reference": product.slug,
        "product": product,
        "organization": product.partner,
        "life": product.line == InsuranceLine.LIFE,
    }


def subject_for_investment(opportunity) -> dict:
    from apps.solutions.domain import QUOTE_INTENTS, EnquiryCategory

    return {
        "category": EnquiryCategory.INVESTMENT,
        "intents": QUOTE_INTENTS,
        "reference": opportunity.reference,
        "investment": opportunity,
        "organization": opportunity.partner,
        "life": False,
    }


def subject_for_service(category, provider=None) -> dict:
    from apps.solutions.domain import QUOTE_INTENTS, EnquiryCategory

    organization = provider.organization if provider is not None else None
    reference = category.slug if provider is None else str(provider.id)
    return {
        "category": EnquiryCategory.SERVICE,
        "intents": QUOTE_INTENTS,
        "reference": reference,
        "service_category": category,
        "service_provider": provider,
        "organization": organization,
        "life": False,
    }


def _name(value: object) -> str:
    if not isinstance(value, str):
        raise SolutionsError(INVALID, status_code=400)
    cleaned = value.strip()
    if cleaned == "" or len(cleaned) > NAME_MAX or "\x00" in cleaned:
        raise SolutionsError(INVALID, status_code=400)
    return cleaned


def _email(value: object) -> str:
    if not isinstance(value, str):
        raise SolutionsError(INVALID, status_code=400)
    normalized = normalize_email(value)
    if normalized == "" or len(normalized) > 254 or "\n" in normalized or "\x00" in normalized:
        raise SolutionsError(INVALID, status_code=400)
    try:
        validate_email(normalized)
    except ValidationError:
        raise SolutionsError(INVALID, status_code=400) from None
    return normalized


def _phone(value: object) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise SolutionsError(INVALID, status_code=400)
    compact = _SEPARATORS.sub("", value.strip())
    if compact.startswith("00"):
        compact = f"+{compact[2:]}"
    if E164_RE.fullmatch(compact) is None or len(compact) > PHONE_MAX:
        raise SolutionsError(INVALID, status_code=400)
    return compact


def _message(value: object) -> str:
    if not isinstance(value, str):
        raise SolutionsError(INVALID, status_code=400)
    cleaned = value.strip()
    if cleaned == "" or "\x00" in cleaned or len(cleaned) > MESSAGE_MAX:
        raise SolutionsError(INVALID, status_code=400)
    return cleaned


def _detail(value: object, *, life: bool) -> str | None:
    if value is None or value == "":
        if life:
            raise SolutionsError(INVALID, status_code=400)
        return None
    if not isinstance(value, str):
        raise SolutionsError(INVALID, status_code=400)
    cleaned = value.strip()
    if life:
        if cleaned not in LIFE_DETAILS:
            raise SolutionsError(MEDICAL_REJECTED, status_code=400)
        return cleaned
    if len(cleaned) > DETAIL_MAX or "\x00" in cleaned:
        raise SolutionsError(INVALID, status_code=400)
    return cleaned
