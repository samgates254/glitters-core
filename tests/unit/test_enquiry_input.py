"""Public enquiry input rules. These tests do not write an enquiry row."""

from django.conf import settings

from apps.marketplace.domain import (
    ENQUIRY_CONSENT_REQUIRED,
    ENQUIRY_EMAIL_INVALID,
    ENQUIRY_FIELD_REJECTED,
    ENQUIRY_MESSAGE_MAX,
    ENQUIRY_MESSAGE_REQUIRED,
    ENQUIRY_MESSAGE_TOO_LONG,
    ENQUIRY_NAME_REQUIRED,
    ENQUIRY_PHONE_INVALID,
)
from apps.marketplace.errors import MarketplaceError
from apps.marketplace.services.enquiries import clean_public_enquiry
from apps.marketplace.throttles import EnquiryAddressThrottle, EnquiryCreationThrottle


def _fields(**overrides) -> dict:
    body = {
        "contact_name": "Ada Lovelace",
        "contact_email": "Seeker@Example.com",
        "contact_phone": "+254 712 345 678",
        "message": "Is the apartment still available?",
        "consent": True,
    }
    body.update(overrides)
    return body


def _reject(fields: dict, detail: str) -> None:
    try:
        clean_public_enquiry(fields)
    except MarketplaceError as exc:
        assert exc.detail == detail
        assert exc.status_code == 400
        return
    raise AssertionError(f"expected {detail}")


def test_public_enquiry_normalizes_without_inventing_a_country_code() -> None:
    cleaned = clean_public_enquiry(_fields())
    assert cleaned["contact_name"] == "Ada Lovelace"
    assert cleaned["contact_email"] == "seeker@example.com"
    assert cleaned["contact_phone"] == "+254712345678"
    assert cleaned["message"] == "Is the apartment still available?"

    international = clean_public_enquiry(_fields(contact_phone="00 1 415 555 2671"))
    assert international["contact_phone"] == "+14155552671"
    omitted = clean_public_enquiry(
        {key: value for key, value in _fields().items() if key != "contact_phone"}
    )
    assert omitted["contact_phone"] is None
    blank = clean_public_enquiry(_fields(contact_phone="   "))
    assert blank["contact_phone"] is None


def test_local_and_malformed_numbers_are_rejected() -> None:
    for raw in ("0712345678", "712345678", "254712345678", "not-a-phone", "+12", 254712345678):
        _reject(_fields(contact_phone=raw), ENQUIRY_PHONE_INVALID)


def test_required_contact_text_and_consent_are_enforced() -> None:
    nameless = {key: value for key, value in _fields().items() if key != "contact_name"}
    unconsented = {key: value for key, value in _fields().items() if key != "consent"}
    _reject(_fields(contact_name="   "), ENQUIRY_NAME_REQUIRED)
    _reject(nameless, ENQUIRY_NAME_REQUIRED)
    _reject(_fields(contact_email="not-an-email"), ENQUIRY_EMAIL_INVALID)
    _reject(_fields(message=" \n\t"), ENQUIRY_MESSAGE_REQUIRED)
    _reject(_fields(message="x" * (ENQUIRY_MESSAGE_MAX + 1)), ENQUIRY_MESSAGE_TOO_LONG)
    accepted = clean_public_enquiry(_fields(message="x" * ENQUIRY_MESSAGE_MAX))
    assert accepted["message"] == "x" * ENQUIRY_MESSAGE_MAX
    _reject(_fields(consent=False), ENQUIRY_CONSENT_REQUIRED)
    _reject(unconsented, ENQUIRY_CONSENT_REQUIRED)
    _reject(_fields(consent="true"), ENQUIRY_CONSENT_REQUIRED)


def test_identity_and_assignment_fields_are_rejected() -> None:
    for extra in (
        {"advertiser_id": "00000000-0000-0000-0000-000000000001"},
        {"organization_id": "00000000-0000-0000-0000-000000000002"},
        {"advertising_organization_id": "00000000-0000-0000-0000-000000000003"},
        {"assigned_to": "00000000-0000-0000-0000-000000000004"},
        {"listing_owner": "00000000-0000-0000-0000-000000000005"},
        {"user_id": "00000000-0000-0000-0000-000000000006"},
        {"status": "closed"},
        {"consent_version": "1999-01"},
        {"consent_at": "2026-11-11T00:00:00Z"},
    ):
        _reject(_fields(**extra), ENQUIRY_FIELD_REJECTED)


def test_enquiry_throttle_rates_are_explicit() -> None:
    rates = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
    assert rates["enquiry_create"] == "5/hour"
    assert rates["enquiry_create_address"] == "30/hour"
    assert EnquiryCreationThrottle.scope == "enquiry_create"
    assert EnquiryAddressThrottle.scope == "enquiry_create_address"
