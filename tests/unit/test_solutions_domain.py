"""Rules that do not need a database."""

from datetime import UTC, datetime

from apps.solutions.domain import (
    INSURANCE_QUOTE_ADAPTERS,
    QUOTATION_AWAITING_PARTNER,
    QUOTATION_REQUEST_RECORDED,
    AuctionStatus,
    QuotationMode,
    contains_unchecked_credential_claim,
    event_metadata,
    public_investment_price,
    quotation_outcome,
    staff_auction_transition_allowed,
)


class _Product:
    def __init__(self, mode, configured, slug="motor-cover"):
        self.quotation_mode = mode
        self.api_configured = configured
        self.slug = slug


class _Opportunity:
    def __init__(self, **kwargs):
        self.price_minor = kwargs.get("price_minor")
        self.price_currency = "KES"
        self.data_source = kwargs.get("data_source", "")
        self.price_as_of = kwargs.get("price_as_of")


def test_auction_cannot_skip_approval():
    assert staff_auction_transition_allowed(AuctionStatus.DRAFT, AuctionStatus.PUBLISHED) is False
    assert staff_auction_transition_allowed(AuctionStatus.PENDING, AuctionStatus.PUBLISHED) is False
    assert staff_auction_transition_allowed(AuctionStatus.APPROVED, AuctionStatus.PUBLISHED) is True


def test_event_metadata_rejects_contact_text():
    try:
        event_metadata({"contact_email": "a@example.com"})
    except ValueError:
        rejected = True
    else:
        rejected = False
    assert rejected is True


def test_unchecked_credential_language_is_detected():
    assert contains_unchecked_credential_claim("Licensed auctioneer") is True
    assert contains_unchecked_credential_claim("Quantity surveying practice") is False


def test_request_mode_does_not_invent_a_premium():
    outcome = quotation_outcome(_Product(QuotationMode.REQUEST, False))
    assert outcome["state"] == QUOTATION_REQUEST_RECORDED
    assert "premium_minor" not in outcome


def test_api_mode_without_an_adapter_does_not_invent_a_premium():
    INSURANCE_QUOTE_ADAPTERS.clear()
    outcome = quotation_outcome(_Product(QuotationMode.API, True))
    assert outcome["state"] == QUOTATION_AWAITING_PARTNER
    assert "premium_minor" not in outcome


def test_a_configured_adapter_can_pass_a_partner_premium():
    INSURANCE_QUOTE_ADAPTERS["motor-cover"] = lambda product: {
        "partner_reference": "Q-9",
        "premium_minor": 150000,
        "summary": "Partner quotation",
    }
    try:
        outcome = quotation_outcome(_Product(QuotationMode.API, True))
    finally:
        INSURANCE_QUOTE_ADAPTERS.clear()
    assert outcome["premium_source"] == "authorised_partner_api"
    assert outcome["premium_minor"] == 150000


def test_investment_price_stays_hidden_without_a_source():
    bare = _Opportunity(price_minor=100)
    assert public_investment_price(bare) is None
    sourced = _Opportunity(
        price_minor=100,
        data_source="Partner feed",
        price_as_of=datetime(2026, 11, 11, tzinfo=UTC),
    )
    assert public_investment_price(sourced)["data_source"] == "Partner feed"
