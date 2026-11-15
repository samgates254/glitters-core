"""Listing lifecycle. Publication is a moderated transition, not a property status."""

from __future__ import annotations

from django.contrib.postgres.search import SearchVector
from django.db import IntegrityError, OperationalError, transaction
from django.utils import timezone

from apps.identity.domain import ACCOUNT_BLOCKED, AccountStatus
from apps.marketplace.domain import (
    AI_REVIEW,
    CONFLICT,
    CURRENCIES,
    DESCRIPTION_MAX,
    EXPIRE_BATCH,
    EXPIRY_NOT_DUE,
    FIELDS_REQUIRED,
    HIGHLIGHTS_MAX,
    HOLD,
    LISTING_OPEN,
    MANDATE_EXPIRED,
    MANDATE_INVALID,
    MANDATE_PENDING,
    MANDATE_SCOPE,
    MAX_REFERENCE_ATTEMPTS,
    MEDIA_INVALID,
    MEDIA_PRIVATE,
    MEDIA_REQUIRED,
    NOT_FOUND,
    PERIOD_INVALID,
    PRICE_INVALID,
    PRICE_REQUIRED,
    PROPERTY_ARCHIVED,
    PROPERTY_NOT_ACTIVE,
    PUBLICATION_TERM,
    REASON_MAX,
    TITLE_MAX,
    TRANSITION_INVALID,
    TYPE_NOT_ELIGIBLE,
    VERIFY_EMAIL_FIRST,
    BillingPeriod,
    DescriptionOrigin,
    EventAction,
    ListingStatus,
    ListingType,
    MandateStatus,
    MediaKind,
    MediaVisibility,
    PricePresentation,
    UploadStatus,
    new_reference,
    parse_minor_amount,
    period_allows,
    property_type_allows,
    transition_allowed,
)
from apps.marketplace.errors import MarketplaceError
from apps.marketplace.models import Listing, ListingMedia, ListingPrice, PropertyMedia
from apps.marketplace.services.access import (
    ensure_actor,
    ensure_moderator,
    ensure_verified_email,
    mandate_in_force,
    require_listing_manager,
    require_representative,
)
from apps.marketplace.services.audit import record_event
from apps.marketplace.services.discovery import prepared
from apps.organizations.domain import (
    MembershipStatus,
    OrganizationStatus,
    Permission,
    permissions_for,
)
from apps.organizations.models import OrganizationMembership
from apps.properties.domain import PropertyStatus
from apps.properties.models import Property
from apps.properties.services.locking import is_deadlock, lock_properties

PRICE_LOCKED = "A price can be changed while the listing is a draft."
ALREADY_EXPIRED = "This listing has expired."


def _conflict(exc: OperationalError) -> None:
    if is_deadlock(exc):
        raise MarketplaceError(CONFLICT, status_code=409) from exc
    raise exc


def _words(value, *, maximum: int, allow_blank: bool) -> str:
    if value is None and allow_blank:
        return ""
    if not isinstance(value, str):
        raise MarketplaceError(FIELDS_REQUIRED, status_code=400)
    text = value.strip()
    if (not text and not allow_blank) or len(text) > maximum:
        raise MarketplaceError(FIELDS_REQUIRED, status_code=400)
    return text


def create_listing(*, actor, data: dict, request=None) -> Listing:
    ensure_actor(actor)
    ensure_verified_email(actor)
    _persist_mandate_expiry(data.get("mandate_id"), actor, request)
    try:
        with transaction.atomic():
            listing = _create_locked(actor, data, request)
            _refresh_search(listing.id)
        return load_managed(actor, listing.id)
    except OperationalError as exc:
        _conflict(exc)


def _create_locked(actor, data, request) -> Listing:
    from apps.marketplace.models import ListingMandate

    try:
        mandate = (
            ListingMandate.objects.select_for_update(of=("self",))
            .select_related(
                "property",
                "representative_user",
                "representative_organization",
            )
            .get(pk=data.get("mandate_id"))
        )
    except (ListingMandate.DoesNotExist, ValueError, TypeError) as exc:
        raise MarketplaceError(NOT_FOUND, status_code=404) from exc
    require_representative(actor, mandate)
    property_id = data.get("property_id")
    if str(mandate.property_id) != str(property_id):
        raise MarketplaceError(MANDATE_INVALID, status_code=400)
    record = lock_properties(mandate.property_id)[mandate.property_id]
    listing_type = data.get("listing_type")
    if listing_type not in ListingType.VALUES:
        raise MarketplaceError(TYPE_NOT_ELIGIBLE, status_code=400)
    if not property_type_allows(record.property_type, listing_type):
        raise MarketplaceError(TYPE_NOT_ELIGIBLE, status_code=400)
    if record.status == PropertyStatus.ARCHIVED:
        raise MarketplaceError(PROPERTY_ARCHIVED, status_code=409)
    # A draft may be prepared before valid_from. Publication still requires the mandate
    # to be in force.
    _require_covering_mandate(mandate, listing_type, require_in_force=False)
    title = _words(data.get("title"), maximum=TITLE_MAX, allow_blank=False)
    description = _words(data.get("description", ""), maximum=DESCRIPTION_MAX, allow_blank=True)
    highlights = _words(data.get("highlights", ""), maximum=HIGHLIGHTS_MAX, allow_blank=True)
    origin = data.get("description_origin") or DescriptionOrigin.HUMAN
    if origin not in DescriptionOrigin.VALUES:
        raise MarketplaceError(FIELDS_REQUIRED, status_code=400)
    organization = mandate.representative_organization
    listing = _insert_listing(
        property=record,
        listing_type=listing_type,
        status=ListingStatus.DRAFT,
        title=title,
        description=description,
        highlights=highlights,
        description_origin=origin,
        advertiser=actor,
        advertising_organization=organization,
        mandate=mandate,
    )
    if "price" in data and data["price"] is not None:
        _replace_price(listing, actor, data["price"], request, initial=True)
    record_event(
        actor=actor,
        property_record=record,
        listing=listing,
        action=EventAction.LISTING_CREATED,
        object_type="listing",
        object_id=listing.id,
        request=request,
        organization_id=listing.advertising_organization_id,
        metadata={"listing_type": listing_type, "description_origin": origin},
    )
    return listing


def _insert_listing(**kwargs) -> Listing:
    for _attempt in range(MAX_REFERENCE_ATTEMPTS):
        try:
            with transaction.atomic():
                return Listing.objects.create(reference=new_reference(), **kwargs)
        except IntegrityError as exc:
            text = str(exc).lower()
            if "listing_one_open" in text:
                raise MarketplaceError(LISTING_OPEN, status_code=409) from exc
            if "reference" not in text:
                raise
    raise MarketplaceError(CONFLICT, status_code=409)


def update_listing(*, actor, listing_id, data: dict, request=None) -> Listing:
    ensure_actor(actor)
    try:
        with transaction.atomic():
            listing = _lock_managed(actor, listing_id)
            if listing.status != ListingStatus.DRAFT:
                if "price" in data:
                    raise MarketplaceError(PRICE_LOCKED, status_code=409)
                raise MarketplaceError(TRANSITION_INVALID, status_code=409)
            changed = _apply_edits(listing, data)
            if "price" in data:
                _replace_price(listing, actor, data["price"], request, initial=False)
                changed = True
            if not changed:
                raise MarketplaceError("No changes were submitted.", status_code=400)
            listing.save()
            record_event(
                actor=actor,
                property_record=listing.property,
                listing=listing,
                action=EventAction.LISTING_UPDATED,
                object_type="listing",
                object_id=listing.id,
                request=request,
                organization_id=listing.advertising_organization_id,
                metadata={"status": listing.status},
            )
            listing_id = listing.id
        _refresh_search(listing_id)
        return load_managed(actor, listing_id)
    except OperationalError as exc:
        _conflict(exc)


def _apply_edits(listing, data) -> bool:
    changed = False
    if "title" in data:
        listing.title = _words(data.get("title"), maximum=TITLE_MAX, allow_blank=False)
        changed = True
    if "description" in data:
        listing.description = _words(
            data.get("description"),
            maximum=DESCRIPTION_MAX,
            allow_blank=True,
        )
        changed = True
    if "highlights" in data:
        listing.highlights = _words(
            data.get("highlights"),
            maximum=HIGHLIGHTS_MAX,
            allow_blank=True,
        )
        changed = True
    if "listing_type" in data and data["listing_type"] != listing.listing_type:
        listing_type = data["listing_type"]
        if listing_type not in ListingType.VALUES:
            raise MarketplaceError(TYPE_NOT_ELIGIBLE, status_code=400)
        if not property_type_allows(listing.property.property_type, listing_type):
            raise MarketplaceError(TYPE_NOT_ELIGIBLE, status_code=400)
        _require_covering_mandate(listing.mandate, listing_type)
        current = _current_price(listing)
        if current is not None and not period_allows(listing_type, current.billing_period):
            raise MarketplaceError(PERIOD_INVALID, status_code=400)
        listing.listing_type = listing_type
        changed = True
    if "description_origin" in data and data["description_origin"] != listing.description_origin:
        if data["description_origin"] not in DescriptionOrigin.VALUES:
            raise MarketplaceError(FIELDS_REQUIRED, status_code=400)
        listing.description_origin = data["description_origin"]
        listing.ai_approved_at = None
        changed = True
    if changed and any(key in data for key in ("title", "description", "highlights")):
        if listing.description_origin == DescriptionOrigin.AI_ASSISTED:
            listing.ai_approved_at = None
    return changed


def accept_ai_description(*, actor, listing_id, request=None) -> Listing:
    ensure_actor(actor)
    try:
        with transaction.atomic():
            listing = _lock_managed(actor, listing_id)
            if listing.status != ListingStatus.DRAFT:
                raise MarketplaceError(TRANSITION_INVALID, status_code=409)
            if listing.description_origin != DescriptionOrigin.AI_ASSISTED:
                raise MarketplaceError(AI_REVIEW, status_code=400)
            listing.ai_approved_at = timezone.now()
            listing.save(update_fields=["ai_approved_at", "updated_at"])
            record_event(
                actor=actor,
                property_record=listing.property,
                listing=listing,
                action=EventAction.LISTING_UPDATED,
                object_type="listing",
                object_id=listing.id,
                request=request,
                organization_id=listing.advertising_organization_id,
                metadata={"ai_accepted": True},
            )
            return load_managed(actor, listing.id)
    except OperationalError as exc:
        _conflict(exc)


def submit_listing(*, actor, listing_id, request=None) -> Listing:
    _prepare_publication(actor, listing_id, request)
    return _advertiser_transition(
        actor,
        listing_id,
        request,
        new_status=ListingStatus.PENDING_REVIEW,
        action=EventAction.LISTING_SUBMITTED,
        before=_assert_publishable,
        fields=lambda listing: {
            "submitted_at": timezone.now(),
            "rejection_reason": "",
            "rejected_at": None,
        },
    )


def revise_listing(*, actor, listing_id, request=None) -> Listing:
    return _advertiser_transition(
        actor,
        listing_id,
        request,
        new_status=ListingStatus.DRAFT,
        action=EventAction.LISTING_UPDATED,
        metadata={"revised": True},
    )


def pause_listing(*, actor, listing_id, request=None) -> Listing:
    return _advertiser_transition(
        actor,
        listing_id,
        request,
        new_status=ListingStatus.PAUSED,
        action=EventAction.LISTING_PAUSED,
        fields=lambda listing: {"paused_at": timezone.now()},
    )


def resume_listing(*, actor, listing_id, request=None) -> Listing:
    _prepare_publication(actor, listing_id, request)

    def before(listing):
        if listing.moderation_hold:
            raise MarketplaceError(HOLD, status_code=409)
        if listing.expires_at and listing.expires_at <= timezone.now():
            raise MarketplaceError(ALREADY_EXPIRED, status_code=409)
        _assert_publishable(listing)

    return _advertiser_transition(
        actor,
        listing_id,
        request,
        new_status=ListingStatus.PUBLISHED,
        action=EventAction.LISTING_RESUMED,
        before=before,
    )


def withdraw_listing(*, actor, listing_id, request=None) -> Listing:
    return _advertiser_transition(
        actor,
        listing_id,
        request,
        new_status=ListingStatus.WITHDRAWN,
        action=EventAction.LISTING_WITHDRAWN,
        fields=lambda listing: {"withdrawn_at": timezone.now()},
    )


def complete_listing(*, actor, listing_id, request=None) -> Listing:
    return _advertiser_transition(
        actor,
        listing_id,
        request,
        new_status=ListingStatus.COMPLETED,
        action=EventAction.LISTING_COMPLETED,
        fields=lambda listing: {"completed_at": timezone.now()},
    )


def expire_listing(*, actor, listing_id, request=None) -> Listing:
    ensure_actor(actor)
    try:
        with transaction.atomic():
            listing = _lock_managed(actor, listing_id)
            _expire(listing, actor, request)
            return load_managed(actor, listing.id)
    except OperationalError as exc:
        _conflict(exc)


def renew_listing(*, actor, listing_id, request=None) -> Listing:
    ensure_actor(actor)
    preview = _preview(actor, listing_id)
    _persist_mandate_expiry(preview.mandate_id, actor, request)
    try:
        with transaction.atomic():
            source = _lock_managed(actor, listing_id)
            if source.status in {ListingStatus.PUBLISHED, ListingStatus.PAUSED} and (
                source.expires_at and source.expires_at <= timezone.now()
            ):
                _expire(source, actor, request)
            if source.status != ListingStatus.EXPIRED:
                raise MarketplaceError(TRANSITION_INVALID, status_code=409)
            require_representative(actor, source.mandate)
            _require_covering_mandate(source.mandate, source.listing_type)
            created = _insert_listing(
                property=source.property,
                listing_type=source.listing_type,
                status=ListingStatus.DRAFT,
                title=source.title,
                description=source.description,
                highlights=source.highlights,
                description_origin=source.description_origin,
                ai_approved_at=source.ai_approved_at,
                advertiser=actor,
                advertising_organization=source.mandate.representative_organization,
                mandate=source.mandate,
            )
            price = _current_price(source)
            if price is not None:
                ListingPrice.objects.create(
                    listing=created,
                    amount_minor=price.amount_minor,
                    currency=price.currency,
                    presentation=price.presentation,
                    negotiable=price.negotiable,
                    billing_period=price.billing_period,
                    created_by=actor,
                )
            links = source.listing_media.select_related("property_media").order_by(
                "sort_order",
                "id",
            )
            for link in links:
                ListingMedia.objects.create(
                    listing=created,
                    property_media=link.property_media,
                    sort_order=link.sort_order,
                    featured=link.featured,
                )
            record_event(
                actor=actor,
                property_record=created.property,
                listing=created,
                action=EventAction.LISTING_CREATED,
                object_type="listing",
                object_id=created.id,
                request=request,
                organization_id=created.advertising_organization_id,
                metadata={"renewed_from": source.reference},
            )
            created_id = created.id
        _refresh_search(created_id)
        return load_managed(actor, created_id)
    except OperationalError as exc:
        _conflict(exc)


def approve_listing(*, actor, listing_id, request=None) -> Listing:
    ensure_moderator(actor)
    _prepare_publication(actor, listing_id, request, staff=True)

    def before(listing):
        _assert_publishable(listing)

    return _moderated(
        actor,
        listing_id,
        request,
        new_status=ListingStatus.PUBLISHED,
        action=EventAction.LISTING_APPROVED,
        before=before,
        fields=lambda listing: {
            "published_at": timezone.now(),
            "expires_at": timezone.now() + PUBLICATION_TERM,
            "rejection_reason": "",
        },
        also=EventAction.LISTING_PUBLISHED,
    )


def reject_listing(*, actor, listing_id, data: dict, request=None) -> Listing:
    ensure_moderator(actor)
    reason = _words(data.get("reason"), maximum=REASON_MAX, allow_blank=False)
    return _moderated(
        actor,
        listing_id,
        request,
        new_status=ListingStatus.REJECTED,
        action=EventAction.LISTING_REJECTED,
        fields=lambda listing: {"rejection_reason": reason, "rejected_at": timezone.now()},
        metadata={"reason": reason},
    )


def suspend_listing(*, actor, listing_id, data: dict, request=None) -> Listing:
    ensure_moderator(actor)
    reason = _words(data.get("reason"), maximum=REASON_MAX, allow_blank=False)
    try:
        with transaction.atomic():
            listing = _lock_for_moderation(listing_id)
            now = timezone.now()
            if listing.status == ListingStatus.PAUSED:
                listing.moderation_hold = True
                listing.moderation_note = reason
                listing.paused_at = listing.paused_at or now
                listing.save(
                    update_fields=["moderation_hold", "moderation_note", "paused_at", "updated_at"]
                )
                record_event(
                    actor=actor,
                    property_record=listing.property,
                    listing=listing,
                    action=EventAction.LISTING_PAUSED,
                    object_type="listing",
                    object_id=listing.id,
                    request=request,
                    organization_id=listing.advertising_organization_id,
                    metadata={"moderation": True, "reason": reason},
                )
            elif listing.status == ListingStatus.PUBLISHED:
                _move(
                    listing,
                    ListingStatus.PAUSED,
                    channel="moderation",
                    actor=actor,
                    request=request,
                    action=EventAction.LISTING_PAUSED,
                    metadata={"moderation": True, "reason": reason},
                    paused_at=now,
                    moderation_hold=True,
                    moderation_note=reason,
                )
            else:
                raise MarketplaceError(TRANSITION_INVALID, status_code=409)
            return load_for_staff(listing.id)
    except OperationalError as exc:
        _conflict(exc)


def reinstate_listing(*, actor, listing_id, request=None) -> Listing:
    ensure_moderator(actor)
    try:
        with transaction.atomic():
            listing = _lock_for_moderation(listing_id)
            if listing.status != ListingStatus.PAUSED or not listing.moderation_hold:
                raise MarketplaceError(TRANSITION_INVALID, status_code=409)
            now = timezone.now()
            _move(
                listing,
                ListingStatus.PENDING_REVIEW,
                channel="moderation",
                actor=actor,
                request=request,
                action=EventAction.LISTING_SUBMITTED,
                metadata={"reinstated": True},
                submitted_at=now,
                published_at=None,
                expires_at=None,
                moderation_note="",
            )
            return load_for_staff(listing.id)
    except OperationalError as exc:
        _conflict(exc)


def expire_due(*, actor, request=None) -> int:
    ensure_moderator(actor)
    now = timezone.now()
    identifiers = list(
        Listing.objects.filter(
            status__in=[ListingStatus.PUBLISHED, ListingStatus.PAUSED],
            expires_at__lte=now,
        )
        .order_by("expires_at", "id")
        .values_list("id", flat=True)[:EXPIRE_BATCH]
    )
    expired = 0
    for listing_id in identifiers:
        try:
            with transaction.atomic():
                listing = Listing.objects.select_for_update().get(pk=listing_id)
                if listing.status in {ListingStatus.PUBLISHED, ListingStatus.PAUSED} and (
                    listing.expires_at and listing.expires_at <= timezone.now()
                ):
                    _expire(listing, actor, request)
                    expired += 1
        except OperationalError as exc:
            _conflict(exc)
    return expired


def public_listing(reference: str) -> Listing:
    listing = (
        Listing.objects.filter(reference=reference)
        .only("id", "status", "expires_at", "reference")
        .first()
    )
    if listing is None:
        raise MarketplaceError(NOT_FOUND, status_code=404)
    if (
        listing.status in {ListingStatus.PUBLISHED, ListingStatus.PAUSED}
        and listing.expires_at
        and listing.expires_at <= timezone.now()
    ):
        try:
            with transaction.atomic():
                locked = Listing.objects.select_for_update().get(pk=listing.id)
                if (
                    locked.status in {ListingStatus.PUBLISHED, ListingStatus.PAUSED}
                    and locked.expires_at
                    and locked.expires_at <= timezone.now()
                ):
                    _expire(locked, None, None)
        except OperationalError as exc:
            _conflict(exc)
        raise MarketplaceError(NOT_FOUND, status_code=404)
    from apps.marketplace.services.discovery import eligible_listings

    row = eligible_listings().filter(reference=reference).first()
    if row is None:
        raise MarketplaceError(NOT_FOUND, status_code=404)
    return row


def load_managed(actor, listing_id) -> Listing:
    listing = _fetch(listing_id)
    require_listing_manager(actor, listing)
    return listing


def load_for_staff(listing_id) -> Listing:
    return _fetch(listing_id)


def withdraw_for_mandate(mandate, actor, request, *, reason: str) -> None:
    rows = list(
        Listing.objects.select_for_update()
        .filter(
            mandate=mandate,
            status__in=[
                ListingStatus.PUBLISHED,
                ListingStatus.PAUSED,
                ListingStatus.PENDING_REVIEW,
            ],
        )
        .order_by("id")
    )
    now = timezone.now()
    for listing in rows:
        _move(
            listing,
            ListingStatus.WITHDRAWN,
            channel="system",
            actor=actor,
            request=request,
            action=EventAction.LISTING_WITHDRAWN,
            metadata={"reason": reason},
            withdrawn_at=now,
        )


def _advertiser_transition(
    actor,
    listing_id,
    request,
    *,
    new_status,
    action,
    before=None,
    fields=None,
    metadata=None,
) -> Listing:
    ensure_actor(actor)
    preview = _preview(actor, listing_id)
    # Mandate expiry is committed on its own. A later invalid transition must not roll it back.
    _persist_mandate_expiry(preview.mandate_id, actor, request)
    preview.refresh_from_db(fields=["status", "expires_at"])
    if preview.status in {ListingStatus.PUBLISHED, ListingStatus.PAUSED} and (
        preview.expires_at and preview.expires_at <= timezone.now()
    ):
        try:
            with transaction.atomic():
                listing = _lock_managed(actor, listing_id)
                if listing.status in {ListingStatus.PUBLISHED, ListingStatus.PAUSED} and (
                    listing.expires_at and listing.expires_at <= timezone.now()
                ):
                    _expire(listing, actor, request)
        except OperationalError as exc:
            _conflict(exc)
        raise MarketplaceError(ALREADY_EXPIRED, status_code=409)
    try:
        with transaction.atomic():
            listing = _lock_managed(actor, listing_id)
            if before is not None:
                before(listing)
            extra = fields(listing) if fields else {}
            _move(
                listing,
                new_status,
                channel="advertiser",
                actor=actor,
                request=request,
                action=action,
                metadata=metadata,
                **extra,
            )
            return load_managed(actor, listing.id)
    except OperationalError as exc:
        _conflict(exc)


def _moderated(
    actor,
    listing_id,
    request,
    *,
    new_status,
    action,
    before=None,
    fields=None,
    metadata=None,
    also=None,
) -> Listing:
    try:
        with transaction.atomic():
            listing = _lock_for_moderation(listing_id)
            if before is not None:
                before(listing)
            extra = fields(listing) if fields else {}
            _move(
                listing,
                new_status,
                channel="moderation",
                actor=actor,
                request=request,
                action=action,
                metadata=metadata,
                **extra,
            )
            if also:
                record_event(
                    actor=actor,
                    property_record=listing.property,
                    listing=listing,
                    action=also,
                    object_type="listing",
                    object_id=listing.id,
                    request=request,
                    organization_id=listing.advertising_organization_id,
                    metadata={"status": listing.status},
                )
            return load_for_staff(listing.id)
    except OperationalError as exc:
        _conflict(exc)


def _prepare_publication(actor, listing_id, request, *, staff: bool = False) -> None:
    listing = _preview(actor, listing_id, staff=staff)
    _persist_mandate_expiry(listing.mandate_id, actor, request)


def _persist_mandate_expiry(mandate_id, actor, request) -> None:
    if not mandate_id:
        return
    from apps.marketplace.models import ListingMandate
    from apps.marketplace.services.mandates import expire_mandate_if_due

    try:
        with transaction.atomic():
            mandate = ListingMandate.objects.select_for_update().get(pk=mandate_id)
            expire_mandate_if_due(mandate, actor, request)
    except ListingMandate.DoesNotExist, ValueError, TypeError:
        return
    except OperationalError as exc:
        _conflict(exc)


def _preview(actor, listing_id, *, staff: bool = False) -> Listing:
    try:
        listing = Listing.objects.select_related("mandate").get(pk=listing_id)
    except (Listing.DoesNotExist, ValueError, TypeError) as exc:
        raise MarketplaceError(NOT_FOUND, status_code=404) from exc
    if staff:
        return listing
    require_listing_manager(actor, listing)
    return listing


def _expire(listing, actor, request) -> None:
    if listing.status not in {ListingStatus.PUBLISHED, ListingStatus.PAUSED}:
        raise MarketplaceError(TRANSITION_INVALID, status_code=409)
    if listing.expires_at is None or listing.expires_at > timezone.now():
        raise MarketplaceError(EXPIRY_NOT_DUE, status_code=409)
    _move(
        listing,
        ListingStatus.EXPIRED,
        channel="system",
        actor=actor,
        request=request,
        action=EventAction.LISTING_EXPIRED,
        metadata={"reason": "elapsed"},
    )


def _move(listing, new_status, *, channel, actor, request, action, metadata=None, **fields) -> None:
    if not transition_allowed(listing.status, new_status, channel):
        raise MarketplaceError(TRANSITION_INVALID, status_code=409)
    listing.status = new_status
    if new_status != ListingStatus.PAUSED:
        listing.moderation_hold = False
    for key, value in fields.items():
        setattr(listing, key, value)
    listing.save()
    record_event(
        actor=actor,
        property_record=listing.property,
        listing=listing,
        action=action,
        object_type="listing",
        object_id=listing.id,
        request=request,
        organization_id=listing.advertising_organization_id,
        metadata={"status": new_status, **(metadata or {})},
    )


def _assert_publishable(listing) -> None:
    record = listing.property
    if record.status != PropertyStatus.ACTIVE:
        raise MarketplaceError(PROPERTY_NOT_ACTIVE, status_code=409)
    if listing.mandate.status == MandateStatus.EXPIRED:
        raise MarketplaceError(MANDATE_EXPIRED, status_code=409)
    if listing.mandate.status == MandateStatus.REVOKED:
        raise MarketplaceError("This authorization has been revoked.", status_code=409)
    if listing.mandate.status == MandateStatus.PENDING_ACCEPTANCE:
        raise MarketplaceError(MANDATE_PENDING, status_code=409)
    if not mandate_in_force(listing.mandate):
        raise MarketplaceError(MANDATE_INVALID, status_code=409)
    _require_covering_mandate(listing.mandate, listing.listing_type)
    if listing.mandate.property_id != listing.property_id:
        raise MarketplaceError(MANDATE_INVALID, status_code=409)
    if not listing.title.strip() or not listing.description.strip():
        raise MarketplaceError(FIELDS_REQUIRED, status_code=400)
    if (
        listing.description_origin == DescriptionOrigin.AI_ASSISTED
        and listing.ai_approved_at is None
    ):
        raise MarketplaceError(AI_REVIEW, status_code=400)
    _assert_advertiser(listing)
    price = _current_price(listing)
    if price is None:
        raise MarketplaceError(PRICE_REQUIRED, status_code=400)
    if not period_allows(listing.listing_type, price.billing_period):
        raise MarketplaceError(PERIOD_INVALID, status_code=400)
    if not _has_photo(listing):
        raise MarketplaceError(MEDIA_REQUIRED, status_code=400)


def _assert_advertiser(listing) -> None:
    advertiser = listing.advertiser
    if advertiser.status != AccountStatus.ACTIVE:
        raise MarketplaceError(ACCOUNT_BLOCKED, status_code=403)
    if advertiser.email_verified_at is None:
        raise MarketplaceError(VERIFY_EMAIL_FIRST, status_code=403)
    if listing.advertising_organization_id is None:
        if listing.mandate.representative_user_id != advertiser.id:
            raise MarketplaceError(MANDATE_INVALID, status_code=409)
        return
    organization = listing.advertising_organization
    if organization.status != OrganizationStatus.ACTIVE:
        raise MarketplaceError("This organization is not active.", status_code=403)
    if listing.mandate.representative_organization_id != organization.id:
        raise MarketplaceError(MANDATE_INVALID, status_code=409)
    membership = OrganizationMembership.objects.filter(
        organization=organization,
        user=advertiser,
    ).first()
    if membership is None or membership.status != MembershipStatus.ACTIVE:
        raise MarketplaceError("This membership is not active.", status_code=403)
    if Permission.PROPERTY_PUBLISH not in permissions_for(membership.role):
        raise MarketplaceError("You do not have permission for this action.", status_code=403)


def _require_covering_mandate(
    mandate,
    listing_type: str,
    *,
    require_in_force: bool = True,
) -> None:
    if mandate.status == MandateStatus.REVOKED:
        raise MarketplaceError("This authorization has been revoked.", status_code=409)
    if mandate.status == MandateStatus.EXPIRED:
        raise MarketplaceError(MANDATE_EXPIRED, status_code=409)
    if mandate.status != MandateStatus.ACTIVE:
        raise MarketplaceError(MANDATE_PENDING, status_code=409)
    today = timezone.localdate()
    if mandate.valid_until and mandate.valid_until < today:
        raise MarketplaceError(MANDATE_EXPIRED, status_code=409)
    if require_in_force and not mandate_in_force(mandate, today):
        raise MarketplaceError(MANDATE_INVALID, status_code=409)
    if mandate.listing_scope not in {listing_type, "any"}:
        raise MarketplaceError(MANDATE_SCOPE, status_code=400)


def _replace_price(listing, actor, payload, request, *, initial: bool) -> ListingPrice:
    if listing.status != ListingStatus.DRAFT:
        raise MarketplaceError(PRICE_LOCKED, status_code=409)
    if not isinstance(payload, dict):
        raise MarketplaceError(PRICE_INVALID, status_code=400)
    presentation = payload.get("presentation")
    if presentation not in PricePresentation.VALUES:
        raise MarketplaceError(PRICE_INVALID, status_code=400)
    currency = payload.get("currency")
    if not isinstance(currency, str) or currency not in CURRENCIES:
        raise MarketplaceError("This currency is not supported.", status_code=400)
    period = payload.get("billing_period")
    if period not in BillingPeriod.VALUES or not period_allows(listing.listing_type, period):
        raise MarketplaceError(PERIOD_INVALID, status_code=400)
    negotiable = payload.get("negotiable")
    if not isinstance(negotiable, bool):
        raise MarketplaceError(PRICE_INVALID, status_code=400)
    allow_null = presentation == PricePresentation.PRICE_ON_APPLICATION
    try:
        amount = parse_minor_amount(payload.get("amount"), allow_null=allow_null)
    except ValueError as exc:
        raise MarketplaceError(str(exc), status_code=400) from exc
    if allow_null and amount is not None:
        raise MarketplaceError(PRICE_INVALID, status_code=400)
    if not allow_null and amount is None:
        raise MarketplaceError(PRICE_INVALID, status_code=400)
    now = timezone.now()
    current = (
        ListingPrice.objects.select_for_update()
        .filter(listing=listing, superseded_at__isnull=True)
        .first()
    )
    if current is not None:
        current.superseded_at = now
        current.save(update_fields=["superseded_at"])
    price = ListingPrice.objects.create(
        listing=listing,
        amount_minor=amount,
        currency=currency,
        presentation=presentation,
        negotiable=negotiable,
        billing_period=period,
        created_by=actor,
    )
    if not initial:
        record_event(
            actor=actor,
            property_record=listing.property,
            listing=listing,
            action=EventAction.LISTING_UPDATED,
            object_type="listing_price",
            object_id=price.id,
            request=request,
            organization_id=listing.advertising_organization_id,
            metadata={
                "currency": currency,
                "presentation": presentation,
                "billing_period": period,
                "amount_minor": amount,
            },
        )
    return price


def _current_price(listing):
    cache = getattr(listing, "_prefetched_objects_cache", {}).get("prices")
    if cache is not None:
        for price in cache:
            if price.superseded_at is None:
                return price
        return None
    return listing.prices.filter(superseded_at__isnull=True).first()


def _has_photo(listing) -> bool:
    from apps.marketplace.domain import verified_photo_filters

    return ListingMedia.objects.filter(listing=listing, **verified_photo_filters()).exists()


def _refresh_search(listing_id) -> None:
    Listing.objects.filter(pk=listing_id).update(
        search_vector=SearchVector("title", "description", "highlights", config="simple")
    )


def _lock_managed(actor, listing_id) -> Listing:
    try:
        listing = (
            Listing.objects.select_for_update(of=("self",))
            .select_related(
                "property",
                "mandate",
                "mandate__representative_user",
                "mandate__representative_organization",
                "advertiser",
                "advertising_organization",
            )
            .get(pk=listing_id)
        )
    except (Listing.DoesNotExist, ValueError, TypeError) as exc:
        raise MarketplaceError(NOT_FOUND, status_code=404) from exc
    require_listing_manager(actor, listing)
    return listing


def _lock_for_moderation(listing_id) -> Listing:
    try:
        return (
            Listing.objects.select_for_update(of=("self",))
            .select_related(
                "property",
                "mandate",
                "advertiser",
                "advertising_organization",
            )
            .get(pk=listing_id)
        )
    except (Listing.DoesNotExist, ValueError, TypeError) as exc:
        raise MarketplaceError(NOT_FOUND, status_code=404) from exc


def _fetch(listing_id) -> Listing:
    try:
        return prepared(Listing.objects.all()).get(pk=listing_id)
    except (Listing.DoesNotExist, ValueError, TypeError) as exc:
        raise MarketplaceError(NOT_FOUND, status_code=404) from exc


def _property(property_id) -> Property:
    try:
        return Property.objects.get(pk=property_id)
    except (Property.DoesNotExist, ValueError, TypeError) as exc:
        raise MarketplaceError(NOT_FOUND, status_code=404) from exc


def attach_media(*, actor, listing_id, data: dict, request=None) -> Listing:
    ensure_actor(actor)
    try:
        with transaction.atomic():
            listing = _lock_managed(actor, listing_id)
            if listing.status != ListingStatus.DRAFT:
                raise MarketplaceError(TRANSITION_INVALID, status_code=409)
            try:
                media = PropertyMedia.objects.select_for_update().get(
                    pk=data.get("property_media_id"),
                )
            except (PropertyMedia.DoesNotExist, ValueError, TypeError) as exc:
                raise MarketplaceError(NOT_FOUND, status_code=404) from exc
            if media.property_id != listing.property_id:
                raise MarketplaceError(MEDIA_INVALID, status_code=400)
            if media.kind != MediaKind.PHOTOGRAPH or media.visibility != MediaVisibility.LISTING:
                raise MarketplaceError(MEDIA_PRIVATE, status_code=400)
            if media.upload_status != UploadStatus.STORED or media.verified_at is None:
                raise MarketplaceError(MEDIA_REQUIRED, status_code=400)
            sort_order = data.get("sort_order", 0)
            sort_invalid = (
                isinstance(sort_order, bool)
                or not isinstance(sort_order, int)
                or not 0 <= sort_order <= 1000
            )
            if sort_invalid:
                raise MarketplaceError(MEDIA_INVALID, status_code=400)
            featured = data.get("featured", False)
            if not isinstance(featured, bool):
                raise MarketplaceError(MEDIA_INVALID, status_code=400)
            if featured:
                ListingMedia.objects.filter(listing=listing, featured=True).update(featured=False)
            link = ListingMedia.objects.create(
                listing=listing,
                property_media=media,
                sort_order=sort_order,
                featured=featured,
            )
            record_event(
                actor=actor,
                property_record=listing.property,
                listing=listing,
                action=EventAction.MEDIA_ADDED,
                object_type="listing_media",
                object_id=link.id,
                request=request,
                organization_id=listing.advertising_organization_id,
                metadata={"kind": media.kind, "scope": "listing"},
            )
            return load_managed(actor, listing.id)
    except IntegrityError as exc:
        raise MarketplaceError("This photograph is already attached.", status_code=409) from exc
    except OperationalError as exc:
        _conflict(exc)


def detach_media(*, actor, listing_id, link_id, request=None) -> Listing:
    ensure_actor(actor)
    try:
        with transaction.atomic():
            listing = _lock_managed(actor, listing_id)
            if listing.status != ListingStatus.DRAFT:
                raise MarketplaceError(TRANSITION_INVALID, status_code=409)
            try:
                link = ListingMedia.objects.select_for_update().get(pk=link_id, listing=listing)
            except (ListingMedia.DoesNotExist, ValueError, TypeError) as exc:
                raise MarketplaceError(NOT_FOUND, status_code=404) from exc
            link_id = link.id
            link.delete()
            record_event(
                actor=actor,
                property_record=listing.property,
                listing=listing,
                action=EventAction.MEDIA_REMOVED,
                object_type="listing_media",
                object_id=link_id,
                request=request,
                organization_id=listing.advertising_organization_id,
                metadata={"scope": "listing"},
            )
            return load_managed(actor, listing.id)
    except OperationalError as exc:
        _conflict(exc)
