"""Response dictionaries. Public shapes omit private contact and private files."""

from __future__ import annotations

from apps.solutions.domain import DocumentVisibility, public_investment_price
from apps.solutions.models import AuctionDocument


def _money(amount, currency: str):
    if amount is None:
        return None
    return {"amount_minor": amount, "currency": currency}


def _documents(auction, *, public_only: bool) -> list[dict]:
    rows = auction.documents.all()
    if public_only:
        rows = [row for row in rows if row.visibility == DocumentVisibility.PUBLIC]
    return [
        {
            "id": str(row.id),
            "kind": row.kind,
            "content_type": row.content_type,
            "caption": row.caption,
            "visibility": row.visibility,
        }
        for row in rows
    ]


def _licence(auction) -> str:
    if not auction.auctioneer_licence_reference:
        return "No auctioneer licence reference has been supplied."
    if auction.auctioneer_verified:
        return "GLITTERS has recorded this auctioneer licence as checked."
    return (
        "This licence reference was supplied by the partner and has not been checked by GLITTERS."
    )


def public_auction(auction) -> dict:
    reserve = None
    if auction.publish_reserve:
        reserve = _money(auction.reserve_price_minor, auction.guide_currency)
    return {
        "reference": auction.reference,
        "title": auction.title,
        "category": auction.category,
        "description": auction.description,
        "county": auction.county,
        "town": auction.town,
        "location_label": auction.location_label,
        "size_label": auction.size_label,
        "tenure": auction.tenure,
        "guide_price": _money(auction.guide_price_minor, auction.guide_currency),
        "reserve_price": reserve,
        "reserve_publication": "published" if auction.publish_reserve else "withheld",
        "auction_at": auction.auction_at,
        "viewing_notes": auction.viewing_notes,
        "registration_deadline": auction.registration_deadline,
        "terms": auction.terms,
        "deposit_requirements": auction.deposit_requirements,
        "payment_instructions": auction.payment_instructions,
        "contact_name": auction.contact_name,
        "contact_email": auction.contact_email,
        "contact_phone": auction.contact_phone,
        "institution_name": auction.institution_name,
        "auctioneer_name": auction.auctioneer_name,
        "auctioneer_licence_reference": auction.auctioneer_licence_reference,
        "auctioneer_verification": "checked" if auction.auctioneer_verified else "not_checked",
        "auctioneer_licence_statement": _licence(auction),
        "status": auction.status,
        "documents": _documents(auction, public_only=True),
    }


def managed_auction(auction) -> dict:
    payload = public_auction(auction)
    payload["reserve_price"] = _money(auction.reserve_price_minor, auction.guide_currency)
    payload["publish_reserve"] = auction.publish_reserve
    payload["documents"] = _documents(auction, public_only=False)
    payload["submitting_organization_id"] = (
        None
        if auction.submitting_organization_id is None
        else str(auction.submitting_organization_id)
    )
    return payload


def public_project(project) -> dict:
    return {
        "slug": project.slug,
        "name": project.name,
        "developer_name": project.developer_name,
        "location_label": project.location_label,
        "description": project.description,
        "category": project.category,
        "status": project.status,
        "expected_completion": project.expected_completion,
        "units_summary": project.units_summary,
        "pricing_note": project.pricing_note,
        "brochure_url": project.brochure_url,
        "contact_name": project.contact_name,
        "contact_email": project.contact_email,
        "contact_phone": project.contact_phone,
    }


def public_product(product) -> dict:
    return {
        "slug": product.slug,
        "name": product.name,
        "line": product.line,
        "category": product.category,
        "summary": product.summary,
        "quotation_mode": product.quotation_mode,
        "api_configured": product.api_configured,
        "partner_name": product.partner_name,
        "quotation_notice": (
            "A quotation is issued by the authorised partner. "
            "GLITTERS does not calculate a premium."
        ),
    }


def public_investment(opportunity) -> dict:
    return {
        "reference": opportunity.reference,
        "name": opportunity.name,
        "symbol": opportunity.symbol,
        "category": opportunity.category,
        "summary": opportunity.summary,
        "market_note": opportunity.market_note,
        "price": public_investment_price(opportunity),
        "partner_name": opportunity.partner_name,
        "risk_disclosure": opportunity.risk_disclosure,
    }


def managed_investment(opportunity) -> dict:
    payload = public_investment(opportunity)
    payload["status"] = opportunity.status
    payload["stored_price_minor"] = opportunity.price_minor
    payload["data_source"] = opportunity.data_source
    payload["price_as_of"] = opportunity.price_as_of
    return payload


def public_category(category, providers) -> dict:
    return {
        "slug": category.slug,
        "name": category.name,
        "group": category.group,
        "description": category.description,
        "providers": [public_provider(provider) for provider in providers],
    }


def public_provider(provider) -> dict:
    checked = provider.credentials_checked
    return {
        "id": str(provider.id),
        "name": provider.name,
        "coverage": provider.coverage,
        "summary": provider.summary,
        "credentials_checked": checked,
        "credentials_note": provider.credentials_note if checked else "",
        "credentials_statement": (
            "Credentials have been checked."
            if checked
            else "Credentials have not been checked by GLITTERS."
        ),
        "portfolio_note": provider.portfolio_note,
        "contact_name": provider.contact_name,
        "contact_email": provider.contact_email,
        "contact_phone": provider.contact_phone,
    }


def public_created(enquiry, outcome: dict) -> dict:
    body = {
        "reference": enquiry.reference,
        "status": enquiry.status,
    }
    if outcome:
        authorised = outcome.get("premium_source") == "authorised_partner_api"
        body["quotation"] = {
            key: value for key, value in outcome.items() if key != "premium_minor" or authorised
        }
    return body


def _follow_ups(enquiry, *, include_notes: bool) -> list[dict]:
    rows = []
    for item in enquiry.follow_ups.all():
        rows.append(
            {
                "id": str(item.id),
                "channel": item.channel,
                "note": item.note if include_notes else "",
                "created_at": item.created_at,
            }
        )
    return rows


def managed_enquiry(enquiry, *, staff: bool) -> dict:
    return {
        "reference": enquiry.reference,
        "category": enquiry.category,
        "intent": enquiry.intent,
        "subject_reference": enquiry.subject_reference,
        "status": enquiry.status,
        "source": enquiry.source,
        "preferred_contact": enquiry.preferred_contact,
        "quotation_state": enquiry.quotation_state,
        "contact_name": enquiry.contact_name,
        "contact_email": enquiry.contact_email,
        "contact_phone": enquiry.contact_phone,
        "message": enquiry.message,
        "detail": enquiry.detail,
        "assigned_organization_id": (
            None
            if enquiry.assigned_organization_id is None
            else str(enquiry.assigned_organization_id)
        ),
        "internal_notes": enquiry.internal_notes if staff else "",
        "partner_response": enquiry.partner_response,
        "consent_at": enquiry.consent_at,
        "consent_version": enquiry.consent_version,
        "created_at": enquiry.created_at,
        "closed_at": enquiry.closed_at,
        "anonymized_at": enquiry.anonymized_at,
        "follow_ups": _follow_ups(enquiry, include_notes=True),
    }


def document_or_404(document: AuctionDocument):
    return document
