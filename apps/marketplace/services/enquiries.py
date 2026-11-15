"""Public enquiry creation and authorized reads.

Advertiser and organization are copied from the eligible listing. The request
cannot supply them. Contact text is stored on the enquiry row and is not
written to event metadata or to the log.

Reads use the caller's account and membership rows. A client organization id,
advertiser id, or permission claim is not a scope. Assignment is not a grant.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, OperationalError, transaction
from django.db.models import Q
from django.utils import timezone

from apps.identity.domain import E164_RE, AccountStatus, normalize_email
from apps.marketplace.domain import (
    ASSIGNEE_NOT_ELIGIBLE,
    CONFLICT,
    ENQUIRY_CHANNEL_WEB,
    ENQUIRY_CONSENT_REQUIRED,
    ENQUIRY_CONSENT_VERSION,
    ENQUIRY_EMAIL_INVALID,
    ENQUIRY_FIELD_REJECTED,
    ENQUIRY_INVALID,
    ENQUIRY_MESSAGE_MAX,
    ENQUIRY_MESSAGE_REQUIRED,
    ENQUIRY_MESSAGE_TOO_LONG,
    ENQUIRY_NAME_MAX,
    ENQUIRY_NAME_REQUIRED,
    ENQUIRY_PHONE_INVALID,
    ENQUIRY_PHONE_MAX,
    ENQUIRY_TRANSITION_INVALID,
    MEMBERSHIP_NOT_ACTIVE,
    NOT_FOUND,
    ORG_NOT_ACTIVE,
    PERMISSION_DENIED,
    PUBLIC_ENQUIRY_FIELDS,
    EnquiryEventAction,
    EnquiryStatus,
    enquiry_transition_allowed,
)
from apps.marketplace.errors import MarketplaceError
from apps.marketplace.filters import EnquiryFilters
from apps.marketplace.models import Enquiry, EnquiryEvent
from apps.marketplace.services.access import (
    ensure_actor,
    ensure_verified_email,
    lead_reader_roles,
    require_lead_manager,
)
from apps.marketplace.services.discovery import eligible_listings
from apps.organizations.domain import MembershipStatus, OrganizationStatus
from apps.organizations.models import OrganizationMembership
from apps.properties.services.audit import request_correlation_id
from apps.properties.services.locking import is_deadlock

logger = logging.getLogger("glitters.marketplace")
_SEPARATORS = re.compile(r"[\s().-]")


def clean_public_enquiry(fields: object) -> dict[str, str | None]:
    """Normalize the public body. Unknown keys are refused."""

    if not isinstance(fields, Mapping):
        raise MarketplaceError(ENQUIRY_INVALID, status_code=400)
    if set(fields) - PUBLIC_ENQUIRY_FIELDS:
        raise MarketplaceError(ENQUIRY_FIELD_REJECTED, status_code=400)
    if fields.get("consent") is not True:
        raise MarketplaceError(ENQUIRY_CONSENT_REQUIRED, status_code=400)
    return {
        "contact_name": _name(fields.get("contact_name")),
        "contact_email": _email(fields.get("contact_email")),
        "contact_phone": _phone(fields.get("contact_phone")),
        "message": _message(fields.get("message")),
    }


def create_public_enquiry(*, reference: str, fields: object, request=None) -> Enquiry:
    """Store one enquiry and its created event, or store neither."""

    cleaned = clean_public_enquiry(fields)
    try:
        with transaction.atomic():
            listing = _lock_eligible(reference)
            if listing is None:
                raise MarketplaceError(NOT_FOUND, status_code=404)
            enquiry = Enquiry.objects.create(
                listing=listing,
                advertiser_id=listing.advertiser_id,
                organization_id=listing.advertising_organization_id,
                contact_name=cleaned["contact_name"],
                contact_email=cleaned["contact_email"],
                contact_phone=cleaned["contact_phone"],
                message=cleaned["message"],
                status=EnquiryStatus.NEW,
                consent_at=timezone.now(),
                consent_version=ENQUIRY_CONSENT_VERSION,
            )
            EnquiryEvent.objects.create(
                enquiry=enquiry,
                actor=None,
                action=EnquiryEventAction.CREATED,
                request_id=request_correlation_id(request),
                metadata={
                    "channel": ENQUIRY_CHANNEL_WEB,
                    "consent_version": ENQUIRY_CONSENT_VERSION,
                },
            )
    except MarketplaceError:
        raise
    except IntegrityError, ValueError:
        logger.warning("Public enquiry was not stored.")
        raise MarketplaceError(ENQUIRY_INVALID, status_code=400) from None
    except OperationalError as exc:
        if is_deadlock(exc):
            raise MarketplaceError(CONFLICT, status_code=409) from None
        logger.warning("Public enquiry was not stored.")
        raise
    logger.info("Public enquiry stored. id=%s", enquiry.id)
    return enquiry


def _lock_eligible(reference: str):
    return eligible_listings().select_for_update(of=("self",)).filter(reference=reference).first()


def _name(value: object) -> str:
    if not isinstance(value, str):
        raise MarketplaceError(ENQUIRY_NAME_REQUIRED, status_code=400)
    text = value.strip()
    if text == "" or len(text) > ENQUIRY_NAME_MAX or "\x00" in text:
        raise MarketplaceError(ENQUIRY_NAME_REQUIRED, status_code=400)
    return text


def _email(value: object) -> str:
    if not isinstance(value, str):
        raise MarketplaceError(ENQUIRY_EMAIL_INVALID, status_code=400)
    normalized = normalize_email(value)
    if normalized == "" or len(normalized) > 254 or "\n" in normalized or "\x00" in normalized:
        raise MarketplaceError(ENQUIRY_EMAIL_INVALID, status_code=400)
    try:
        validate_email(normalized)
    except ValidationError:
        raise MarketplaceError(ENQUIRY_EMAIL_INVALID, status_code=400) from None
    return normalized


def _phone(value: object) -> str | None:
    """Drop an empty number. Do not add a country code."""

    if value is None:
        return None
    if not isinstance(value, str):
        raise MarketplaceError(ENQUIRY_PHONE_INVALID, status_code=400)
    raw = value.strip()
    if raw == "":
        return None
    cleaned = _SEPARATORS.sub("", raw)
    if cleaned.startswith("00"):
        cleaned = f"+{cleaned[2:]}"
    if E164_RE.fullmatch(cleaned) is None or len(cleaned) > ENQUIRY_PHONE_MAX:
        raise MarketplaceError(ENQUIRY_PHONE_INVALID, status_code=400)
    return cleaned


def authorized_enquiries(user):
    """Enquiries this account may read.

    The queryset is the authorization boundary. Personal rows require the
    caller to be the recorded advertiser. Organization rows require an
    active membership in an active organization whose role grants
    leads.view or leads.manage. Assigned users are not added.
    """

    ensure_actor(user)
    ensure_verified_email(user)
    personal = Q(organization_id__isnull=True, advertiser_id=user.pk)
    organization_ids = OrganizationMembership.objects.filter(
        user_id=user.pk,
        status=MembershipStatus.ACTIVE,
        organization__status=OrganizationStatus.ACTIVE,
        role__in=lead_reader_roles(),
    ).values("organization_id")
    return (
        Enquiry.objects.filter(personal | Q(organization_id__in=organization_ids))
        .select_related("listing")
        .order_by("-created_at", "-id")
    )


def list_enquiries(user, filters: EnquiryFilters):
    """Apply inbox filters to the authorized queryset. Filters only narrow it."""

    queryset = authorized_enquiries(user)
    if filters.status:
        queryset = queryset.filter(status=filters.status)
    if filters.listing_reference:
        queryset = queryset.filter(listing__reference=filters.listing_reference)
    if filters.created_after is not None:
        queryset = queryset.filter(created_at__gte=filters.created_after)
    if filters.created_before is not None:
        queryset = queryset.filter(created_at__lte=filters.created_before)
    return queryset


def get_authorized_enquiry(user, enquiry_id) -> Enquiry:
    """Return one authorized enquiry, or refuse without returning the row."""

    enquiry = authorized_enquiries(user).filter(pk=enquiry_id).first()
    if enquiry is not None:
        return enquiry
    _explain_hidden_enquiry(user, enquiry_id)
    raise MarketplaceError(NOT_FOUND, status_code=404)


def _explain_hidden_enquiry(user, enquiry_id) -> None:
    """Distinguish a same-organization denial from a stranger.

    A missing id and a personal enquiry produce the same 404. The enquiry
    body is not loaded. Membership is read from the database on this request.
    """

    organization_id = (
        Enquiry.objects.filter(pk=enquiry_id).values_list("organization_id", flat=True).first()
    )
    if organization_id is None:
        return
    membership = (
        OrganizationMembership.objects.select_related("organization")
        .filter(organization_id=organization_id, user_id=user.pk)
        .first()
    )
    if membership is None:
        return
    if membership.organization.status != OrganizationStatus.ACTIVE:
        raise MarketplaceError(ORG_NOT_ACTIVE, status_code=403)
    if membership.status != MembershipStatus.ACTIVE:
        raise MarketplaceError(MEMBERSHIP_NOT_ACTIVE, status_code=403)
    raise MarketplaceError(PERMISSION_DENIED, status_code=403)


def _message(value: object) -> str:
    if not isinstance(value, str):
        raise MarketplaceError(ENQUIRY_MESSAGE_REQUIRED, status_code=400)
    text = value.strip()
    if text == "" or "\x00" in text:
        raise MarketplaceError(ENQUIRY_MESSAGE_REQUIRED, status_code=400)
    if len(text) > ENQUIRY_MESSAGE_MAX:
        raise MarketplaceError(ENQUIRY_MESSAGE_TOO_LONG, status_code=400)
    return text


def assign_enquiry(
    *,
    user,
    enquiry_id,
    user_id: object,
    request=None,
) -> Enquiry:
    """Assign or reassign an enquiry, or clear assignment.

    Personal enquiries stay with the personal advertiser; unrelated assignees
    are rejected. Organization enquiries require leads.manage on an active
    membership in an active organization; assignees must be active members
    in that same organization. Assignment does not grant read access or transfer
    ownership, and does not alter status.
    """

    ensure_actor(user)
    ensure_verified_email(user)
    try:
        with transaction.atomic():
            enquiry = (
                Enquiry.objects.select_for_update(of=("self",))
                .select_related("listing")
                .filter(pk=enquiry_id)
                .first()
            )
            if enquiry is None:
                raise MarketplaceError(NOT_FOUND, status_code=404)
            require_lead_manager(user, enquiry)

            if enquiry.organization_id is None:
                if user_id is not None and str(user_id) != str(user.pk):
                    raise MarketplaceError(ASSIGNEE_NOT_ELIGIBLE, status_code=400)
                target_user = user if user_id is not None and str(user_id) == str(user.pk) else None
            else:
                if user_id is not None:
                    target_membership = (
                        OrganizationMembership.objects.select_related("organization", "user")
                        .filter(organization_id=enquiry.organization_id, user_id=user_id)
                        .first()
                    )
                    if target_membership is None:
                        raise MarketplaceError(ASSIGNEE_NOT_ELIGIBLE, status_code=400)
                    if target_membership.organization.status != OrganizationStatus.ACTIVE:
                        raise MarketplaceError(ORG_NOT_ACTIVE, status_code=403)
                    if (
                        target_membership.status != MembershipStatus.ACTIVE
                        or target_membership.user.status != AccountStatus.ACTIVE
                    ):
                        raise MarketplaceError(ASSIGNEE_NOT_ELIGIBLE, status_code=400)
                    target_user = target_membership.user
                else:
                    target_user = None

            previous_assignee_id = enquiry.assigned_to_id
            target_user_id = target_user.pk if target_user is not None else None

            if previous_assignee_id != target_user_id:
                enquiry.assigned_to = target_user
                enquiry.save(update_fields=["assigned_to", "updated_at"])

                event_metadata: dict = {}
                if target_user_id is not None:
                    action = EnquiryEventAction.ASSIGNED
                    event_metadata["assignee_id"] = str(target_user_id)
                    if previous_assignee_id is not None:
                        event_metadata["previous_assignee_id"] = str(previous_assignee_id)
                else:
                    action = EnquiryEventAction.UNASSIGNED
                    event_metadata["previous_assignee_id"] = str(previous_assignee_id)

                EnquiryEvent.objects.create(
                    enquiry=enquiry,
                    actor=user,
                    action=action,
                    request_id=request_correlation_id(request),
                    metadata=event_metadata,
                )
    except MarketplaceError:
        raise
    except OperationalError as exc:
        if is_deadlock(exc):
            raise MarketplaceError(CONFLICT, status_code=409) from None
        raise
    return enquiry


def transition_enquiry(
    *,
    user,
    enquiry_id,
    new_status: str,
    reason_code: str | None = None,
    request=None,
) -> Enquiry:
    """Advance an enquiry along the canonical status graph.

    Statuses cannot skip lifecycle stages or reopen once closed, converted,
    or marked as spam. An enquiry closed sets closed_at on the server; clients
    cannot supply or clear closed timestamps.
    """

    ensure_actor(user)
    ensure_verified_email(user)
    try:
        with transaction.atomic():
            enquiry = (
                Enquiry.objects.select_for_update(of=("self",))
                .select_related("listing")
                .filter(pk=enquiry_id)
                .first()
            )
            if enquiry is None:
                raise MarketplaceError(NOT_FOUND, status_code=404)
            require_lead_manager(user, enquiry)

            if not enquiry_transition_allowed(enquiry.status, new_status):
                raise MarketplaceError(ENQUIRY_TRANSITION_INVALID, status_code=400)

            previous_status = enquiry.status
            enquiry.status = new_status
            update_fields = ["status", "updated_at"]

            if new_status == EnquiryStatus.CLOSED:
                enquiry.closed_at = timezone.now()
                update_fields.append("closed_at")
            elif enquiry.closed_at is not None:
                enquiry.closed_at = None
                update_fields.append("closed_at")

            enquiry.save(update_fields=update_fields)

            event_metadata: dict = {
                "from_status": previous_status,
                "to_status": new_status,
            }
            if reason_code is not None:
                clean_reason = str(reason_code).strip()
                if (
                    not clean_reason
                    or len(clean_reason) > 64
                    or "@" in clean_reason
                    or "\n" in clean_reason
                    or "\x00" in clean_reason
                ):
                    raise MarketplaceError("This reason code is not valid.", status_code=400)
                event_metadata["reason_code"] = clean_reason

            EnquiryEvent.objects.create(
                enquiry=enquiry,
                actor=user,
                action=EnquiryEventAction.STATUS_CHANGED,
                request_id=request_correlation_id(request),
                metadata=event_metadata,
            )
    except MarketplaceError:
        raise
    except OperationalError as exc:
        if is_deadlock(exc):
            raise MarketplaceError(CONFLICT, status_code=409) from None
        raise
    return enquiry
