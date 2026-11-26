"""Canonical lease contract data and integrity hashing."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from apps.tenancy.models import Lease


def build_canonical_contract_data(lease: Lease) -> dict[str, Any]:
    """Build the stable contractual payload used for signatures.

    Internal scheduling fields such as effective_end_date, lifecycle state,
    and signature timestamps are deliberately excluded. Party rows are sorted
    so database ordering cannot change the resulting digest.
    """
    parties = [
        {
            "has_signature_authority": party.has_signature_authority,
            "party_role": party.party_role,
            "user_id": str(party.user_id),
        }
        for party in lease.parties.all()
    ]
    parties.sort(key=lambda party: (party["party_role"], party["user_id"]))

    return {
        "contract_version": 1,
        "parties": parties,
        "terms": {
            "deposit_amount_minor": lease.deposit_amount_minor,
            "grace_period_days": lease.grace_period_days,
            "lease_type": lease.lease_type,
            "managing_organization_id": (
                str(lease.managing_organization_id)
                if lease.managing_organization_id is not None
                else None
            ),
            "payment_due_day": lease.payment_due_day,
            "property_id": str(lease.property_id),
            "property_owner_id": str(lease.property_owner_id),
            "rent_amount_minor": lease.rent_amount_minor,
            "rent_currency": lease.rent_currency,
            "service_charge_minor": lease.service_charge_minor,
            "special_covenants": lease.special_covenants,
            "start_date": lease.start_date.isoformat(),
            "term_end_date": (
                lease.term_end_date.isoformat() if lease.term_end_date is not None else None
            ),
        },
    }


def canonical_contract_json(lease: Lease) -> str:
    """Serialize the fixed contract payload as deterministic UTF-8 JSON.

    The payload uses fixed ASCII object keys and integer monetary values, so
    sorted compact JSON has the canonical representation required here without
    introducing floating-point number formatting.
    """
    return json.dumps(
        build_canonical_contract_data(lease),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def compute_contract_data_hash(lease: Lease) -> str:
    """Return the SHA-256 hex digest of a lease canonical contract payload."""
    return hashlib.sha256(canonical_contract_json(lease).encode("utf-8")).hexdigest()
