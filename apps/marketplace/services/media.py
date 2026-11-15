"""Property media authority. Bytes are verified through the upload service."""

from __future__ import annotations

from django.db import OperationalError, transaction
from django.db.models import Q
from django.utils import timezone

from apps.marketplace.domain import (
    CONFLICT,
    DOCUMENT_MIME,
    MEDIA_INVALID,
    NOT_FOUND,
    PERMISSION_DENIED,
    PHOTOGRAPH_MIMES,
    EventAction,
    MandateStatus,
    MediaKind,
    MediaVisibility,
    SessionStatus,
    UploadStatus,
)
from apps.marketplace.errors import MarketplaceError
from apps.marketplace.models import ListingMandate, ListingMedia, MediaUploadSession, PropertyMedia
from apps.marketplace.services.access import ensure_actor, publish_roles
from apps.marketplace.services.audit import record_event
from apps.organizations.domain import MembershipStatus, OrganizationStatus
from apps.organizations.models import OrganizationMembership
from apps.properties.errors import PropertyError
from apps.properties.models import Property
from apps.properties.services.authorization import decide
from apps.properties.services.locking import is_deadlock


def remove_property_media(*, actor, property_id, media_id, request=None) -> PropertyMedia:
    ensure_actor(actor)
    record, can_update = _property_for_media(actor, property_id)
    if not can_update and not has_active_mandate(actor, record):
        raise MarketplaceError(PERMISSION_DENIED, status_code=403)
    try:
        with transaction.atomic():
            try:
                media = PropertyMedia.objects.select_for_update().get(
                    pk=media_id,
                    property=record,
                )
            except (PropertyMedia.DoesNotExist, ValueError, TypeError) as exc:
                raise MarketplaceError(NOT_FOUND, status_code=404) from exc
            if media.kind == MediaKind.DOCUMENT and not can_update:
                raise MarketplaceError(PERMISSION_DENIED, status_code=403)
            media.upload_status = UploadStatus.REJECTED
            media.save(update_fields=["upload_status"])
            ListingMedia.objects.filter(property_media=media).delete()
            delete_ids = _cancel_open_sessions(media)
            record_event(
                actor=actor,
                property_record=record,
                action=EventAction.MEDIA_REMOVED,
                object_type="property_media",
                object_id=media.id,
                request=request,
                organization_id=record.managing_organization_id,
                metadata={"kind": media.kind, "scope": "property"},
            )
    except OperationalError as exc:
        if is_deadlock(exc):
            raise MarketplaceError(CONFLICT, status_code=409) from exc
        raise
    from apps.marketplace.services.uploads import delete_session_object

    for session_id in delete_ids:
        delete_session_object(session_id)
    return media


def _cancel_open_sessions(media) -> list:
    now = timezone.now()
    identifiers = []
    sessions = MediaUploadSession.objects.select_for_update().filter(media=media)
    for session in sessions:
        if session.status in SessionStatus.OPEN:
            session.status = SessionStatus.CANCELLED
            session.cancelled_at = now
            session.rejection_code = "removed"
            session.save(update_fields=["status", "cancelled_at", "rejection_code", "updated_at"])
        if not session.object_deleted:
            identifiers.append(session.id)
    return identifiers


def list_property_media(*, actor, property_id):
    ensure_actor(actor)
    record, can_update = _property_for_media(actor, property_id)
    queryset = PropertyMedia.objects.filter(property=record).order_by("created_at")
    if can_update:
        return queryset
    if has_active_mandate(actor, record):
        return queryset.filter(kind=MediaKind.PHOTOGRAPH, visibility=MediaVisibility.LISTING)
    raise MarketplaceError(PERMISSION_DENIED, status_code=403)


def _property_for_media(actor, property_id):
    """Return the property and whether the actor may see private documents.

    A property grant is one path. An active listing mandate is another path
    for photographs only. Callers who have neither receive 404.
    """

    try:
        decision = decide(actor, property_id)
    except PropertyError as exc:
        if exc.status_code != 404:
            raise
    else:
        return decision.record, decision.can_update
    try:
        record = Property.objects.get(pk=property_id)
    except (Property.DoesNotExist, ValueError, TypeError) as exc:
        raise MarketplaceError(NOT_FOUND, status_code=404) from exc
    if not has_active_mandate(actor, record):
        raise MarketplaceError(NOT_FOUND, status_code=404)
    return record, False


def has_active_mandate(actor, record) -> bool:
    today = timezone.localdate()
    window = Q(valid_from__lte=today) & (Q(valid_until__isnull=True) | Q(valid_until__gte=today))
    if ListingMandate.objects.filter(
        window,
        property=record,
        representative_user=actor,
        status=MandateStatus.ACTIVE,
    ).exists():
        return True
    org_ids = OrganizationMembership.objects.filter(
        user=actor,
        status=MembershipStatus.ACTIVE,
        organization__status=OrganizationStatus.ACTIVE,
        role__in=publish_roles(),
    ).values("organization_id")
    return ListingMandate.objects.filter(
        window,
        property=record,
        representative_organization_id__in=org_ids,
        status=MandateStatus.ACTIVE,
    ).exists()


def _visibility(kind, mime: str) -> str:
    if kind == MediaKind.PHOTOGRAPH and mime in PHOTOGRAPH_MIMES:
        return MediaVisibility.LISTING
    if kind == MediaKind.DOCUMENT and mime == DOCUMENT_MIME:
        return MediaVisibility.PRIVATE
    raise MarketplaceError(MEDIA_INVALID, status_code=400)


def _short(value, maximum: int) -> str:
    if value in (None, ""):
        return ""
    if not isinstance(value, str) or len(value.strip()) > maximum:
        raise MarketplaceError(MEDIA_INVALID, status_code=400)
    return value.strip()
