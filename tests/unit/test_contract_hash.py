"""Canonical contract payload. These tests do not persist a lease."""

from __future__ import annotations

import hashlib
import json

from apps.tenancy.models import Lease, LeaseSignatureEvidence
from apps.tenancy.services.document import (
    CANONICAL_EXCLUDED_FIELDS,
    CONTRACT_DATA_HASH_HELP,
    RENDERED_DOCUMENT_HASH_HELP,
    build_canonical_contract_data,
    canonical_contract_json,
    compute_contract_data_hash,
    compute_rendered_document_hash,
)

LANDLORD = "00000000-0000-4000-8000-000000000001"
TENANT = "00000000-0000-4000-8000-000000000002"
PROPERTY = "00000000-0000-4000-8000-000000000010"
AGREEMENT = "00000000-0000-4000-8000-000000000020"


def _terms(**overrides) -> dict:
    terms = {
        "deposit_amount_minor": 5000000,
        "grace_period_days": 5,
        "lease_type": "residential_fixed",
        "managing_organization_id": None,
        "payment_due_day": 1,
        "property_id": PROPERTY,
        "property_owner_id": LANDLORD,
        "rent_amount_minor": 7500000,
        "rent_currency": "KES",
        "service_charge_minor": 0,
        "special_covenants": "café quiet hours",
        "start_date": "2026-06-01",
        "term_end_date": "2027-05-31",
    }
    terms.update(overrides)
    return terms


def _parties() -> list[dict]:
    return [
        {
            "has_signature_authority": True,
            "party_role": "primary_tenant",
            "user_id": TENANT,
        },
        {
            "has_signature_authority": True,
            "party_role": "landlord",
            "user_id": LANDLORD,
        },
    ]


def _payload(**overrides) -> dict:
    body = {
        "document_version": 1,
        "management_agreement_id": None,
        "parties": _parties(),
        "terms": _terms(),
    }
    body.update(overrides)
    return build_canonical_contract_data(**body)


def test_party_order_and_unicode_are_stable() -> None:
    forward = compute_contract_data_hash(_payload())
    reversed_parties = list(reversed(_parties()))
    backward = compute_contract_data_hash(_payload(parties=reversed_parties))
    assert forward == backward
    encoded = canonical_contract_json(_payload())
    assert "café" in encoded
    assert "\\u00e9" not in encoded
    again = compute_contract_data_hash(json.loads(encoded))
    assert again == forward


def test_commercial_changes_alter_the_digest() -> None:
    original = compute_contract_data_hash(_payload())
    changed_rent = compute_contract_data_hash(_payload(terms=_terms(rent_amount_minor=7600000)))
    changed_version = compute_contract_data_hash(_payload(document_version=2))
    changed_agreement = compute_contract_data_hash(_payload(management_agreement_id=AGREEMENT))
    assert len({original, changed_rent, changed_version, changed_agreement}) == 4


def test_lifecycle_fields_are_absent() -> None:
    payload = _payload()
    encoded = canonical_contract_json(payload)
    for field in CANONICAL_EXCLUDED_FIELDS:
        assert field not in payload
        assert field not in payload["terms"]
        assert f'"{field}"' not in encoded
    assert payload["management_agreement_id"] is None
    assert json.loads(encoded)["management_agreement_id"] is None
    assert payload["contract_version"] == 1
    assert payload["document_version"] == 1


def test_rendered_bytes_use_sha256() -> None:
    document = "Lease\n café".encode()
    digest = compute_rendered_document_hash(document)
    assert digest == hashlib.sha256(document).hexdigest()
    assert compute_rendered_document_hash(b"") == hashlib.sha256(b"").hexdigest()
    assert digest != compute_rendered_document_hash(document + b" ")


def test_help_text_does_not_claim_rfc8785_or_pdf_generation() -> None:
    for model, field_name, expected in (
        (Lease, "contract_data_hash", CONTRACT_DATA_HASH_HELP),
        (Lease, "rendered_document_hash", RENDERED_DOCUMENT_HASH_HELP),
        (LeaseSignatureEvidence, "contract_data_hash", CONTRACT_DATA_HASH_HELP),
        (LeaseSignatureEvidence, "rendered_document_hash", RENDERED_DOCUMENT_HASH_HELP),
    ):
        help_text = model._meta.get_field(field_name).help_text
        assert help_text == expected
        assert "not RFC 8785" in help_text or "does not generate a PDF/A" in help_text
        assert "hash of canonical RFC 8785" not in help_text
