"""Canonical lease contract payload and integrity digests.

The payload is sorted compact JSON encoded as UTF-8, then hashed with
SHA-256. This is not RFC 8785 (JSON Canonicalization Scheme). JCS rules for
numbers, unicode escapes, and whitespace are not implemented here.

Included:
- ``contract_version``: schema version of this payload, not the lease row version
- ``document_version``: the lease document version presented to signatories
- ``management_agreement_id``: agreement UUID string, or JSON null
- ``parties``: ``user_id``, ``party_role``, ``has_signature_authority``,
  sorted by ``(party_role, user_id)``
- ``terms``: lease type, property, owner, managing organization, rent,
  deposit, service charge, due day, grace days, covenants, start date,
  and contractual term end

Excluded lifecycle and storage fields:
effective end, status, signature deadline, signed/activated/terminated
timestamps, actual termination and handover dates, created-by, row
timestamps, both stored digests, the rendered URI, termination reason,
and the lease reference. Those values must not change the contract digest.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

CONTRACT_SCHEMA_VERSION = 1

CONTRACT_DATA_HASH_HELP = (
    "SHA-256 hex digest of the canonical contract payload. "
    "The payload is sorted compact JSON (UTF-8). It is not RFC 8785 / JCS."
)
RENDERED_DOCUMENT_HASH_HELP = (
    "SHA-256 hex digest of the exact rendered document bytes presented to signatories. "
    "The platform stores the digest and does not generate a PDF/A."
)

CANONICAL_EXCLUDED_FIELDS = (
    "activated_at",
    "actual_handover_date",
    "actual_termination_date",
    "contract_data_hash",
    "created_at",
    "created_by_id",
    "effective_end_date",
    "id",
    "reference",
    "rendered_document_hash",
    "rendered_document_uri",
    "signature_deadline",
    "signed_at",
    "status",
    "terminated_at",
    "termination_reason",
    "updated_at",
)


def canonical_contract_json(payload: dict[str, Any]) -> str:
    """Return the exact UTF-8 JSON text that is hashed."""

    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def compute_contract_data_hash(payload: dict[str, Any]) -> str:
    """Return the SHA-256 hex digest of the canonical payload."""

    raw = canonical_contract_json(payload).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def compute_rendered_document_hash(document: bytes) -> str:
    """Return the SHA-256 hex digest of the exact rendered bytes."""

    if not isinstance(document, (bytes, bytearray)):
        raise TypeError("Rendered document bytes are required.")
    return hashlib.sha256(bytes(document)).hexdigest()


def build_canonical_contract_data(
    *,
    document_version: int,
    management_agreement_id: str | None,
    parties: list[dict[str, Any]],
    terms: dict[str, Any],
) -> dict[str, Any]:
    """Build the payload. Callers pass already-normalized terms and parties."""

    projected = [
        {
            "has_signature_authority": bool(party["has_signature_authority"]),
            "party_role": party["party_role"],
            "user_id": str(party["user_id"]),
        }
        for party in parties
    ]
    projected.sort(key=lambda row: (row["party_role"], row["user_id"]))
    return {
        "contract_version": CONTRACT_SCHEMA_VERSION,
        "document_version": document_version,
        "management_agreement_id": management_agreement_id,
        "parties": projected,
        "terms": terms,
    }


def contract_terms_from_lease(lease) -> dict[str, Any]:
    """Project the contractual columns. Lifecycle columns are not copied."""

    return {
        "deposit_amount_minor": lease.deposit_amount_minor,
        "grace_period_days": lease.grace_period_days,
        "lease_type": lease.lease_type,
        "managing_organization_id": (
            str(lease.managing_organization_id) if lease.managing_organization_id else None
        ),
        "payment_due_day": lease.payment_due_day,
        "property_id": str(lease.property_id),
        "property_owner_id": str(lease.property_owner_id),
        "rent_amount_minor": lease.rent_amount_minor,
        "rent_currency": lease.rent_currency,
        "service_charge_minor": lease.service_charge_minor,
        "special_covenants": lease.special_covenants,
        "start_date": lease.start_date.isoformat(),
        "term_end_date": lease.term_end_date.isoformat() if lease.term_end_date else None,
    }


def contract_payload_for_lease(lease, parties) -> dict[str, Any]:
    """Build the payload for a persisted lease and its parties."""

    agreement_id = str(lease.management_agreement_id) if lease.management_agreement_id else None
    party_rows = [
        {
            "has_signature_authority": party.has_signature_authority,
            "party_role": party.party_role,
            "user_id": party.user_id,
        }
        for party in parties
    ]
    return build_canonical_contract_data(
        document_version=lease.document_version,
        management_agreement_id=agreement_id,
        parties=party_rows,
        terms=contract_terms_from_lease(lease),
    )
