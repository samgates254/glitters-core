"""Server-controlled uploads. A client status or URL cannot mark a file stored."""

from __future__ import annotations

import hashlib
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import OperationalError, transaction
from django.utils import timezone

from apps.marketplace.domain import (
    ALT_MAX,
    CAPTION_MAX,
    CONFLICT,
    MEDIA_EXPIRED,
    MEDIA_INVALID,
    MEDIA_NOT_READY,
    MEDIA_NOT_STORED,
    MEDIA_OBJECT_MISSING,
    MEDIA_SIGNATURE,
    MEDIA_SIZE_MISMATCH,
    MEDIA_TOO_LARGE,
    NOT_FOUND,
    PERMISSION_DENIED,
    STORAGE_UNAVAILABLE,
    EventAction,
    MediaKind,
    MediaVisibility,
    SessionStatus,
    StorageProvider,
    UploadStatus,
)
from apps.marketplace.errors import MarketplaceError
from apps.marketplace.models import ListingMedia, MediaUploadSession, PropertyMedia
from apps.marketplace.services.access import ensure_actor, ensure_verified_email
from apps.marketplace.services.audit import record_event
from apps.marketplace.services.content import inspect_upload
from apps.marketplace.services.media import (
    _property_for_media,
    _short,
    _visibility,
    has_active_mandate,
)
from apps.marketplace.storage import (
    StorageObjectMissing,
    StorageUnavailable,
    get_object_storage,
)
from apps.properties.services.locking import is_deadlock

CLIENT_STORAGE_FIELDS = frozenset(
    {"upload_status", "storage_reference", "storage_url", "storage_key"}
)
_FAILURES = {
    "missing": (MEDIA_OBJECT_MISSING, 400),
    "size": (MEDIA_SIZE_MISMATCH, 400),
    "too_large": (MEDIA_TOO_LARGE, 400),
    "signature": (MEDIA_SIGNATURE, 400),
    "expired": (MEDIA_EXPIRED, 409),
    "cancelled": (MEDIA_NOT_READY, 409),
    "removed": (MEDIA_NOT_READY, 409),
}


def reject_client_storage_claims(data) -> None:
    if isinstance(data, dict) and CLIENT_STORAGE_FIELDS.intersection(data):
        raise MarketplaceError(MEDIA_INVALID, status_code=400)


def create_upload(*, actor, property_id, data: dict, request=None) -> dict:
    ensure_actor(actor)
    ensure_verified_email(actor)
    record, can_update = _property_for_media(actor, property_id)
    kind = data.get("kind")
    mime = data.get("mime_type")
    if not isinstance(mime, str):
        raise MarketplaceError(MEDIA_INVALID, status_code=400)
    visibility = _visibility(kind, mime.strip())
    _require_media_rights(kind, record, can_update, actor)
    size = data.get("size_bytes")
    if isinstance(size, bool) or not isinstance(size, int) or size < 1:
        raise MarketplaceError(MEDIA_INVALID, status_code=400)
    if size > _size_limit(kind):
        raise MarketplaceError(MEDIA_TOO_LARGE, status_code=400)
    cleanup_upload_sessions()
    storage = get_object_storage()
    media_id = uuid.uuid4()
    key = f"properties/{record.id}/media/{media_id}/original"
    lifetime = _seconds("MEDIA_UPLOAD_SESSION_SECONDS")
    put_seconds = min(_seconds("MEDIA_UPLOAD_PUT_SECONDS"), lifetime)
    expires_at = timezone.now() + timedelta(seconds=lifetime)
    try:
        with transaction.atomic():
            media = PropertyMedia.objects.create(
                id=media_id,
                property=record,
                kind=kind,
                mime_type=mime.strip(),
                storage_reference=key,
                storage_provider=storage.provider,
                storage_bucket=storage.bucket,
                storage_key=key,
                upload_status=UploadStatus.PENDING,
                visibility=visibility,
                caption=_short(data.get("caption"), CAPTION_MAX),
                alt_text=_short(data.get("alt_text"), ALT_MAX),
                created_by=actor,
            )
            session = MediaUploadSession.objects.create(
                property=record,
                media=media,
                created_by=actor,
                kind=kind,
                declared_mime=mime.strip(),
                status=SessionStatus.CREATED,
                storage_provider=storage.provider,
                storage_bucket=storage.bucket,
                storage_key=key,
                declared_size_bytes=size,
                expires_at=expires_at,
            )
            try:
                url = storage.presign_put(
                    key=key,
                    content_type=mime.strip(),
                    content_length=size,
                    expires_in=put_seconds,
                )
            except StorageUnavailable as exc:
                raise MarketplaceError(STORAGE_UNAVAILABLE, status_code=503) from exc
            record_event(
                actor=actor,
                property_record=record,
                action=EventAction.MEDIA_UPLOAD_STARTED,
                object_type="media_upload_session",
                object_id=session.id,
                request=request,
                organization_id=record.managing_organization_id,
                metadata={
                    "kind": kind,
                    "mime_type": mime.strip(),
                    "declared_size_bytes": size,
                    "provider": storage.provider,
                },
            )
    except OperationalError as exc:
        if is_deadlock(exc):
            raise MarketplaceError(CONFLICT, status_code=409) from exc
        raise
    return {
        "id": session.id,
        "media_id": media.id,
        "status": session.status,
        "expires_in": put_seconds,
        "upload": {
            "url": url,
            "method": "PUT",
            "headers": {"Content-Type": mime.strip(), "Content-Length": str(size)},
        },
    }


def complete_upload(*, actor, session_id, request=None) -> PropertyMedia:
    ensure_actor(actor)
    ensure_verified_email(actor)
    preview = _owned_session(actor, session_id)
    record, can_update = _property_for_media(actor, preview.property_id)
    _require_media_rights(preview.kind, record, can_update, actor)
    stored = None
    outcome = None
    delete_ids: list = []
    try:
        with transaction.atomic():
            media = PropertyMedia.objects.select_for_update().get(
                pk=preview.media_id,
                property=record,
            )
            session = MediaUploadSession.objects.select_for_update().get(pk=preview.id)
            if (
                session.status == SessionStatus.COMPLETED
                and media.upload_status == UploadStatus.STORED
                and media.verified_at is not None
            ):
                stored = media
            elif session.status == SessionStatus.COMPLETED:
                outcome = _FAILURES["removed"]
            elif session.status in {SessionStatus.REJECTED, SessionStatus.CANCELLED}:
                outcome = _FAILURES["cancelled"]
            elif session.status == SessionStatus.EXPIRED or _session_expired(session):
                if session.status != SessionStatus.EXPIRED:
                    _mark_expired(session, media, actor, request)
                if not session.object_deleted:
                    delete_ids.append(session.id)
                outcome = _FAILURES["expired"]
            elif media.upload_status == UploadStatus.REJECTED:
                _close(session, media, actor, request, SessionStatus.CANCELLED, "removed")
                if not session.object_deleted:
                    delete_ids.append(session.id)
                outcome = _FAILURES["removed"]
            else:
                stored, outcome, should_delete = _verify(session, media, actor, request)
                if should_delete and not session.object_deleted:
                    delete_ids.append(session.id)
    except OperationalError as exc:
        if is_deadlock(exc):
            raise MarketplaceError(CONFLICT, status_code=409) from exc
        raise
    for identifier in delete_ids:
        delete_session_object(identifier)
    if outcome is not None:
        message, status_code = outcome
        raise MarketplaceError(message, status_code=status_code)
    if stored is None:
        raise MarketplaceError(MEDIA_NOT_READY, status_code=409)
    return stored


def cancel_upload(*, actor, session_id, request=None) -> PropertyMedia:
    ensure_actor(actor)
    ensure_verified_email(actor)
    preview = _owned_session(actor, session_id)
    record, can_update = _property_for_media(actor, preview.property_id)
    _require_media_rights(preview.kind, record, can_update, actor)
    outcome = None
    delete_id = None
    media = None
    try:
        with transaction.atomic():
            media = PropertyMedia.objects.select_for_update().get(
                pk=preview.media_id,
                property=record,
            )
            session = MediaUploadSession.objects.select_for_update().get(pk=preview.id)
            if session.status == SessionStatus.CANCELLED:
                pass
            elif session.status != SessionStatus.CREATED:
                outcome = _FAILURES["cancelled"]
            elif _session_expired(session):
                _mark_expired(session, media, actor, request)
                delete_id = None if session.object_deleted else session.id
                outcome = _FAILURES["expired"]
            else:
                _close(session, media, actor, request, SessionStatus.CANCELLED, "cancelled")
                delete_id = None if session.object_deleted else session.id
    except OperationalError as exc:
        if is_deadlock(exc):
            raise MarketplaceError(CONFLICT, status_code=409) from exc
        raise
    if delete_id is not None:
        delete_session_object(delete_id)
    if outcome is not None:
        message, status_code = outcome
        raise MarketplaceError(message, status_code=status_code)
    return media


def download_property_media(*, actor, property_id, media_id, request=None) -> dict:
    ensure_actor(actor)
    ensure_verified_email(actor)
    record, can_update = _property_for_media(actor, property_id)
    media = _media_for_property(record, media_id)
    _require_media_rights(media.kind, record, can_update, actor)
    if media.visibility == MediaVisibility.PRIVATE and not can_update:
        raise MarketplaceError(PERMISSION_DENIED, status_code=403)
    if not _deliverable(media):
        raise MarketplaceError(MEDIA_NOT_STORED, status_code=409)
    expires = _seconds("MEDIA_UPLOAD_GET_SECONDS")
    try:
        url = get_object_storage().presign_get(key=media.storage_key, expires_in=expires)
    except StorageUnavailable as exc:
        raise MarketplaceError(STORAGE_UNAVAILABLE, status_code=503) from exc
    record_event(
        actor=actor,
        property_record=record,
        action=EventAction.MEDIA_DOWNLOADED,
        object_type="property_media",
        object_id=media.id,
        request=request,
        organization_id=record.managing_organization_id,
        metadata={"kind": media.kind},
    )
    return {"url": url, "expires_in": expires}


def public_listing_photo(*, reference: str, link_id) -> dict:
    from apps.marketplace.services.listings import public_listing

    listing = public_listing(reference)
    try:
        link = ListingMedia.objects.select_related("property_media").get(
            pk=link_id,
            listing=listing,
        )
    except (ListingMedia.DoesNotExist, ValueError, TypeError) as exc:
        raise MarketplaceError(NOT_FOUND, status_code=404) from exc
    media = link.property_media
    if (
        not _deliverable(media)
        or media.kind != MediaKind.PHOTOGRAPH
        or media.visibility != MediaVisibility.LISTING
    ):
        raise MarketplaceError(NOT_FOUND, status_code=404)
    expires = _seconds("MEDIA_UPLOAD_GET_SECONDS")
    try:
        url = get_object_storage().presign_get(key=media.storage_key, expires_in=expires)
    except StorageUnavailable as exc:
        raise MarketplaceError(STORAGE_UNAVAILABLE, status_code=503) from exc
    return {"url": url, "expires_in": expires}


def cleanup_upload_sessions() -> None:
    now = timezone.now()
    batch = _batch_size()
    expired_ids = list(
        MediaUploadSession.objects.filter(status=SessionStatus.CREATED, expires_at__lt=now)
        .order_by("expires_at")
        .values_list("id", flat=True)[:batch]
    )
    for session_id in expired_ids:
        try:
            _expire_if_due(session_id)
        except OperationalError:
            continue
    stale_ids = list(
        MediaUploadSession.objects.filter(
            status__in=[
                SessionStatus.REJECTED,
                SessionStatus.EXPIRED,
                SessionStatus.CANCELLED,
            ],
            object_deleted=False,
        )
        .order_by("updated_at")
        .values_list("id", flat=True)[:batch]
    )
    for session_id in stale_ids:
        delete_session_object(session_id)


def delete_session_object(session_id) -> None:
    session = (
        MediaUploadSession.objects.filter(pk=session_id, object_deleted=False)
        .only("id", "storage_key")
        .first()
    )
    if session is None:
        return
    try:
        get_object_storage().delete(session.storage_key)
    except StorageUnavailable:
        return
    MediaUploadSession.objects.filter(pk=session_id).update(
        object_deleted=True,
        updated_at=timezone.now(),
    )


def _verify(session, media, actor, request):
    if session.status not in SessionStatus.OPEN:
        return None, _FAILURES["cancelled"], False
    storage = get_object_storage()
    limit = _size_limit(media.kind)
    try:
        head = storage.head(session.storage_key)
    except StorageUnavailable as exc:
        raise MarketplaceError(STORAGE_UNAVAILABLE, status_code=503) from exc
    if head is None:
        _reject(session, media, actor, request, "missing")
        return None, _FAILURES["missing"], True
    if head.size > limit:
        _reject(session, media, actor, request, "too_large")
        return None, _FAILURES["too_large"], True
    if head.size != session.declared_size_bytes:
        _reject(session, media, actor, request, "size")
        return None, _FAILURES["size"], True
    session.status = SessionStatus.UPLOADED
    session.save(update_fields=["status", "updated_at"])
    session.status = SessionStatus.VERIFYING
    session.save(update_fields=["status", "updated_at"])
    try:
        data = storage.get_bytes(session.storage_key, maximum=limit)
    except StorageObjectMissing:
        _reject(session, media, actor, request, "missing")
        return None, _FAILURES["missing"], True
    except StorageUnavailable as exc:
        raise MarketplaceError(STORAGE_UNAVAILABLE, status_code=503) from exc
    if len(data) > limit:
        _reject(session, media, actor, request, "too_large")
        return None, _FAILURES["too_large"], True
    if len(data) != session.declared_size_bytes:
        _reject(session, media, actor, request, "size")
        return None, _FAILURES["size"], True
    try:
        inspect_upload(data, session.declared_mime)
    except MarketplaceError:
        _reject(session, media, actor, request, "signature")
        return None, _FAILURES["signature"], True
    digest = hashlib.sha256(data).hexdigest()
    now = timezone.now()
    media.upload_status = UploadStatus.STORED
    media.file_size_bytes = len(data)
    media.checksum_sha256 = digest
    media.verified_at = now
    media.save(update_fields=["upload_status", "file_size_bytes", "checksum_sha256", "verified_at"])
    session.status = SessionStatus.COMPLETED
    session.file_size_bytes = len(data)
    session.checksum_sha256 = digest
    session.verified_at = now
    session.completed_at = now
    session.save(
        update_fields=[
            "status",
            "file_size_bytes",
            "checksum_sha256",
            "verified_at",
            "completed_at",
            "updated_at",
        ]
    )
    record_event(
        actor=actor,
        property_record=media.property,
        action=EventAction.MEDIA_VERIFIED,
        object_type="property_media",
        object_id=media.id,
        request=request,
        organization_id=media.property.managing_organization_id,
        metadata={
            "kind": media.kind,
            "file_size_bytes": len(data),
            "provider": session.storage_provider,
        },
    )
    return media, None, False


def _reject(session, media, actor, request, code: str) -> None:
    now = timezone.now()
    session.status = SessionStatus.REJECTED
    session.rejected_at = now
    session.rejection_code = code
    session.save(update_fields=["status", "rejected_at", "rejection_code", "updated_at"])
    if media.upload_status != UploadStatus.STORED:
        media.upload_status = UploadStatus.REJECTED
        media.save(update_fields=["upload_status"])
    _audit_failure(session, media, actor, request, code)


def _mark_expired(session, media, actor, request) -> None:
    _close(session, media, actor, request, SessionStatus.EXPIRED, "expired")


def _close(session, media, actor, request, status: str, code: str) -> None:
    now = timezone.now()
    session.status = status
    session.rejection_code = code
    update_fields = ["status", "rejection_code", "updated_at"]
    if status == SessionStatus.EXPIRED:
        session.rejected_at = now
        update_fields.append("rejected_at")
    elif status == SessionStatus.CANCELLED:
        session.cancelled_at = now
        update_fields.append("cancelled_at")
    session.save(update_fields=update_fields)
    if media.upload_status == UploadStatus.PENDING:
        media.upload_status = UploadStatus.REJECTED
        media.save(update_fields=["upload_status"])
    _audit_failure(session, media, actor, request, code)


def _audit_failure(session, media, actor, request, code: str) -> None:
    record_event(
        actor=actor,
        property_record=media.property,
        action=EventAction.MEDIA_REJECTED,
        object_type="media_upload_session",
        object_id=session.id,
        request=request,
        organization_id=media.property.managing_organization_id,
        metadata={"kind": media.kind, "reason": code},
    )


def _expire_if_due(session_id) -> None:
    preview = MediaUploadSession.objects.only("id", "media_id").get(pk=session_id)
    delete_id = None
    with transaction.atomic():
        media = PropertyMedia.objects.select_for_update().get(pk=preview.media_id)
        session = MediaUploadSession.objects.select_for_update().get(pk=preview.id)
        if session.status != SessionStatus.CREATED or not _session_expired(session):
            return
        _mark_expired(session, media, None, None)
        if not session.object_deleted:
            delete_id = session.id
    if delete_id is not None:
        delete_session_object(delete_id)


def _owned_session(actor, session_id) -> MediaUploadSession:
    try:
        return MediaUploadSession.objects.get(pk=session_id, created_by=actor)
    except (MediaUploadSession.DoesNotExist, ValueError, TypeError) as exc:
        raise MarketplaceError(NOT_FOUND, status_code=404) from exc


def _media_for_property(record, media_id) -> PropertyMedia:
    try:
        return PropertyMedia.objects.get(pk=media_id, property=record)
    except (PropertyMedia.DoesNotExist, ValueError, TypeError) as exc:
        raise MarketplaceError(NOT_FOUND, status_code=404) from exc


def _require_media_rights(kind, record, can_update, actor) -> None:
    if kind == MediaKind.DOCUMENT and not can_update:
        raise MarketplaceError(PERMISSION_DENIED, status_code=403)
    if not can_update and not has_active_mandate(actor, record):
        raise MarketplaceError(PERMISSION_DENIED, status_code=403)


def _deliverable(media) -> bool:
    return (
        media.upload_status == UploadStatus.STORED
        and media.verified_at is not None
        and media.storage_provider in StorageProvider.VALUES
        and isinstance(media.storage_key, str)
        and media.storage_key != ""
        and "://" not in media.storage_key
    )


def _session_expired(session) -> bool:
    return session.expires_at <= timezone.now()


def _size_limit(kind: str) -> int:
    name = "MEDIA_MAX_IMAGE_BYTES" if kind == MediaKind.PHOTOGRAPH else "MEDIA_MAX_DOCUMENT_BYTES"
    return _seconds(name)


def _seconds(name: str) -> int:
    value = getattr(settings, name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ImproperlyConfigured("Media upload limits are not configured.")
    return value


def _batch_size() -> int:
    value = getattr(settings, "MEDIA_UPLOAD_CLEANUP_BATCH", 25)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return 25
    return min(value, 50)
