"""Explicit API payloads. Public listings use an allowlist."""

from __future__ import annotations

from django.core.exceptions import ObjectDoesNotExist
from django.urls import reverse

from apps.marketplace.domain import (
    MODERATION_NOTICE,
    PUBLIC_ATTRIBUTE_CODES,
    MediaKind,
    MediaVisibility,
    UploadStatus,
    advertiser_label,
    authority_notice,
)


def public_listing(listing) -> dict:
    attributes = _attributes(listing.property)
    return {
        "reference": listing.reference,
        "listing_type": listing.listing_type,
        "title": listing.title,
        "description": listing.description,
        "highlights": listing.highlights,
        "description_origin": listing.description_origin,
        "published_at": listing.published_at,
        "expires_at": listing.expires_at,
        "property": {
            "property_type": listing.property.property_type,
            "property_subtype": listing.property.property_subtype,
            "bedrooms": attributes.get("bedrooms"),
            "bathrooms": attributes.get("bathrooms"),
            "floor_area_sqm": attributes.get("floor_area_sqm"),
            "land_area_sqm": attributes.get("plot_area_sqm"),
        },
        "location": _location(listing.property),
        "price": _current_price(listing),
        "advertiser": {
            "display_name": advertiser_label(listing.advertiser),
            "organization_name": (
                listing.advertising_organization.display_name
                if listing.advertising_organization_id
                else None
            ),
        },
        "media": _public_media(listing),
    }


def publisher_listing(listing) -> dict:
    payload = public_listing(listing)
    payload.update(
        {
            "id": listing.id,
            "status": listing.status,
            "property_id": listing.property_id,
            "property_reference": listing.property.reference,
            "mandate_id": listing.mandate_id,
            "advertiser_id": listing.advertiser_id,
            "advertising_organization_id": listing.advertising_organization_id,
            "description_origin": listing.description_origin,
            "ai_approved_at": listing.ai_approved_at,
            "moderation_hold": listing.moderation_hold,
            "rejection_reason": listing.rejection_reason,
            "submitted_at": listing.submitted_at,
            "paused_at": listing.paused_at,
            "withdrawn_at": listing.withdrawn_at,
            "completed_at": listing.completed_at,
            "rejected_at": listing.rejected_at,
            "created_at": listing.created_at,
            "updated_at": listing.updated_at,
            "price_history": _price_history(listing),
        }
    )
    return payload


def moderation_listing(listing) -> dict:
    payload = publisher_listing(listing)
    payload["moderation_note"] = listing.moderation_note
    payload["moderation_notice"] = MODERATION_NOTICE
    return payload


def mandate_payload(mandate) -> dict:
    return {
        "id": mandate.id,
        "property_id": mandate.property_id,
        "listing_scope": mandate.listing_scope,
        "status": mandate.status,
        "self_declared": mandate.self_declared,
        "authority": "self_declaration" if mandate.self_declared else "representation",
        "authority_notice": authority_notice(mandate.self_declared),
        "valid_from": mandate.valid_from,
        "valid_until": mandate.valid_until,
        "evidence_reference": mandate.evidence_reference,
        "principal_user_id": mandate.principal_user_id,
        "principal_organization_id": mandate.principal_organization_id,
        "representative_user_id": mandate.representative_user_id,
        "representative_organization_id": mandate.representative_organization_id,
        "granting_user_id": mandate.granting_user_id,
        "accepted_at": mandate.accepted_at,
        "rejected_at": mandate.rejected_at,
        "revoked_at": mandate.revoked_at,
        "created_at": mandate.created_at,
    }


def enquiry_inbox(enquiry) -> dict:
    """Contact fields for an authorized reader.

    The enquiry id is the reference returned when the enquiry was created.
    Assignment is a user id only, and only when one is stored. Organization
    profiles, advertiser accounts, and event rows are omitted.
    """

    assignment = None
    if enquiry.assigned_to_id is not None:
        assignment = {"user_id": enquiry.assigned_to_id}
    return {
        "id": enquiry.id,
        "listing_reference": enquiry.listing.reference,
        "contact_name": enquiry.contact_name,
        "contact_email": enquiry.contact_email,
        "contact_phone": enquiry.contact_phone,
        "message": enquiry.message,
        "status": enquiry.status,
        "consent_at": enquiry.consent_at,
        "consent_version": enquiry.consent_version,
        "created_at": enquiry.created_at,
        "assignment": assignment,
    }


def property_media_payload(media) -> dict:
    reference = media.storage_key or ""
    if "://" in reference:
        reference = ""
    return {
        "id": media.id,
        "property_id": media.property_id,
        "kind": media.kind,
        "mime_type": media.mime_type,
        "asset_reference": reference,
        "upload_status": media.upload_status,
        "visibility": media.visibility,
        "caption": media.caption,
        "alt_text": media.alt_text,
        "created_at": media.created_at,
    }


def _attributes(prop) -> dict:
    found = {}
    for row in prop.attributes.all():
        if row.code not in PUBLIC_ATTRIBUTE_CODES:
            continue
        if row.value_integer is not None:
            found[row.code] = row.value_integer
        elif row.value_decimal is not None:
            found[row.code] = str(row.value_decimal)
    return found


def _location(prop) -> dict:
    empty = {"country": None, "administrative_area": None, "locality": None}
    try:
        location = prop.location
    except ObjectDoesNotExist:
        return empty
    address = location.address
    if address is None:
        return empty
    area = address.administrative_area
    locality = address.locality
    return {
        "country": address.country_id,
        "administrative_area": None if area is None else area.name,
        "locality": None if locality is None else locality.name,
    }


def _current_price(listing):
    cache = getattr(listing, "_prefetched_objects_cache", {}).get("prices")
    price = None
    if cache is not None:
        price = next((row for row in cache if row.superseded_at is None), None)
    else:
        price = listing.prices.filter(superseded_at__isnull=True).first()
    if price is None:
        return None
    return _price_row(price)


def _price_history(listing) -> list[dict]:
    # The listing prefetch keeps only the current price. History is a separate read.
    rows = listing.prices.model.objects.filter(listing=listing).order_by("created_at", "id")
    return [_price_row(row) for row in rows]


def _price_row(price) -> dict:
    return {
        "amount_minor": price.amount_minor,
        "currency": price.currency,
        "presentation": price.presentation,
        "negotiable": price.negotiable,
        "billing_period": price.billing_period,
        "created_at": price.created_at,
        "superseded_at": price.superseded_at,
    }


def _public_media(listing) -> list[dict]:
    rows = []
    for link in listing.listing_media.all():
        media = link.property_media
        if (
            media.kind != MediaKind.PHOTOGRAPH
            or media.upload_status != UploadStatus.STORED
            or media.visibility != MediaVisibility.LISTING
            or media.verified_at is None
            or not media.storage_key
            or "://" in media.storage_key
        ):
            continue
        rows.append(
            {
                "mime_type": media.mime_type,
                "asset_reference": reverse(
                    "marketplace-listing-photo",
                    kwargs={"reference": listing.reference, "link_id": link.id},
                ),
                "caption": media.caption,
                "alt_text": media.alt_text,
                "sort_order": link.sort_order,
                "featured": link.featured,
            }
        )
    return rows
