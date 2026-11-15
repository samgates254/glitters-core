"""Domain contracts, validation constants, and lifecycle transitions for property operations."""

from __future__ import annotations

import hashlib
import secrets
from typing import Any

# Error messages
NOT_FOUND = "Not found."
PERMISSION_DENIED = "You do not have permission for this action."
CONFLICT = "This change conflicts with another update."
STATUS_INVALID = "This status transition is not allowed."
UNAUTHORIZED_OWNER = "The user is not authorized to grant management authority for this property."
UNAUTHORIZED_AGENT = "The user is not authorized to manage agreements for this organization."
COUNTERPARTY_MISMATCH = "Only the designated counterparty can accept or reject this agreement proposal."
INITIATOR_MISMATCH = "Only the proposing party can revoke this agreement proposal."
ALREADY_RESOLVED = "This agreement has already been resolved and cannot be changed."
EFFECTIVE_DATE_INVALID = "The agreement has not reached its effective start date or has expired."
SPEND_LIMIT_INVALID = "Spend limit cannot be negative."
COMMISSION_INVALID = "Commission basis points must be between 0 and 10000 (0% - 100%)."
DATE_RANGE_INVALID = "Valid until date must be on or after valid from date."
ACTIVE_AGREEMENT_EXISTS = "An active management agreement already exists for this property."
PROPERTY_ARCHIVED = "Archived properties cannot be placed under a management agreement."
ORGANIZATION_NOT_ACTIVE = "The managing organization is not active."
PHYSICAL_STATE_INVALID = "This physical readiness state transition is not allowed."

REFERENCE_PREFIX = "PMA-"

SENSITIVE_METADATA_KEYS = frozenset(
    {
        "token",
        "raw_token",
        "token_hash",
        "password",
        "secret",
        "authorization",
        "cookie",
    }
)


class AgreementStatus:
    DRAFT = "draft"
    PENDING_OWNER_ACCEPTANCE = "pending_owner_acceptance"
    PENDING_AGENT_ACCEPTANCE = "pending_agent_acceptance"
    ACCEPTED = "accepted"
    ACTIVE = "active"
    REJECTED = "rejected"
    REVOKED = "revoked"
    TERMINATED = "terminated"
    EXPIRED = "expired"

    CHOICES = (
        (DRAFT, "Draft"),
        (PENDING_OWNER_ACCEPTANCE, "Pending Owner Acceptance"),
        (PENDING_AGENT_ACCEPTANCE, "Pending Agent Acceptance"),
        (ACCEPTED, "Accepted"),
        (ACTIVE, "Active"),
        (REJECTED, "Rejected"),
        (REVOKED, "Revoked"),
        (TERMINATED, "Terminated"),
        (EXPIRED, "Expired"),
    )
    VALUES = frozenset(value for value, _ in CHOICES)

    # Proposals pending action
    PENDING = frozenset({PENDING_OWNER_ACCEPTANCE, PENDING_AGENT_ACCEPTANCE})


AGREEMENT_TRANSITIONS: dict[str, frozenset[str]] = {
    AgreementStatus.DRAFT: frozenset(
        {
            AgreementStatus.PENDING_OWNER_ACCEPTANCE,
            AgreementStatus.PENDING_AGENT_ACCEPTANCE,
            AgreementStatus.REVOKED,
        }
    ),
    AgreementStatus.PENDING_OWNER_ACCEPTANCE: frozenset(
        {
            AgreementStatus.ACCEPTED,
            AgreementStatus.ACTIVE,
            AgreementStatus.REJECTED,
            AgreementStatus.REVOKED,
        }
    ),
    AgreementStatus.PENDING_AGENT_ACCEPTANCE: frozenset(
        {
            AgreementStatus.ACCEPTED,
            AgreementStatus.ACTIVE,
            AgreementStatus.REJECTED,
            AgreementStatus.REVOKED,
        }
    ),
    AgreementStatus.ACCEPTED: frozenset(
        {
            AgreementStatus.ACTIVE,
            AgreementStatus.REVOKED,
            AgreementStatus.TERMINATED,
        }
    ),
    AgreementStatus.ACTIVE: frozenset(
        {
            AgreementStatus.TERMINATED,
            AgreementStatus.EXPIRED,
        }
    ),
    AgreementStatus.REJECTED: frozenset(),
    AgreementStatus.REVOKED: frozenset(),
    AgreementStatus.TERMINATED: frozenset(),
    AgreementStatus.EXPIRED: frozenset(),
}


def agreement_transition_allowed(from_status: str, to_status: str) -> bool:
    return to_status in AGREEMENT_TRANSITIONS.get(from_status, frozenset())


class PhysicalState:
    READY_INSPECTED = "READY_INSPECTED"
    OCCUPIED_NORMAL = "OCCUPIED_NORMAL"
    OCCUPIED_HOLDOVER = "OCCUPIED_HOLDOVER"
    TURNOVER_PREPARATION = "TURNOVER_PREPARATION"
    REPAIR_HOLD = "REPAIR_HOLD"
    STRUCTURAL_OFFLINE = "STRUCTURAL_OFFLINE"
    OWNER_RESERVED = "OWNER_RESERVED"

    CHOICES = (
        (READY_INSPECTED, "Ready / Inspected"),
        (OCCUPIED_NORMAL, "Occupied (Normal)"),
        (OCCUPIED_HOLDOVER, "Occupied (Holdover)"),
        (TURNOVER_PREPARATION, "Turnover Preparation"),
        (REPAIR_HOLD, "Repair Hold"),
        (STRUCTURAL_OFFLINE, "Structural Offline"),
        (OWNER_RESERVED, "Owner Reserved"),
    )
    VALUES = frozenset(value for value, _ in CHOICES)


PHYSICAL_STATE_TRANSITIONS: dict[str, frozenset[str]] = {
    PhysicalState.TURNOVER_PREPARATION: frozenset(
        {
            PhysicalState.READY_INSPECTED,
            PhysicalState.REPAIR_HOLD,
            PhysicalState.STRUCTURAL_OFFLINE,
            PhysicalState.OWNER_RESERVED,
        }
    ),
    PhysicalState.READY_INSPECTED: frozenset(
        {
            PhysicalState.OCCUPIED_NORMAL,
            PhysicalState.TURNOVER_PREPARATION,
            PhysicalState.REPAIR_HOLD,
            PhysicalState.OWNER_RESERVED,
        }
    ),
    PhysicalState.OCCUPIED_NORMAL: frozenset(
        {
            PhysicalState.TURNOVER_PREPARATION,
            PhysicalState.OCCUPIED_HOLDOVER,
            PhysicalState.REPAIR_HOLD,
        }
    ),
    PhysicalState.OCCUPIED_HOLDOVER: frozenset(
        {
            PhysicalState.TURNOVER_PREPARATION,
            PhysicalState.REPAIR_HOLD,
            PhysicalState.OCCUPIED_NORMAL,
        }
    ),
    PhysicalState.REPAIR_HOLD: frozenset(
        {
            PhysicalState.TURNOVER_PREPARATION,
            PhysicalState.READY_INSPECTED,
            PhysicalState.OCCUPIED_NORMAL,
            PhysicalState.STRUCTURAL_OFFLINE,
        }
    ),
    PhysicalState.STRUCTURAL_OFFLINE: frozenset(
        {
            PhysicalState.REPAIR_HOLD,
            PhysicalState.TURNOVER_PREPARATION,
        }
    ),
    PhysicalState.OWNER_RESERVED: frozenset(
        {
            PhysicalState.TURNOVER_PREPARATION,
            PhysicalState.READY_INSPECTED,
        }
    ),
}


def physical_state_transition_allowed(from_state: str, to_state: str) -> bool:
    if from_state == to_state:
        return True
    return to_state in PHYSICAL_STATE_TRANSITIONS.get(from_state, frozenset())


class OperationsEventAction:
    AGREEMENT_DRAFTED = "agreement.drafted"
    AGREEMENT_PROPOSED = "agreement.proposed"
    AGREEMENT_ACCEPTED = "agreement.accepted"
    AGREEMENT_ACTIVATED = "agreement.activated"
    AGREEMENT_REJECTED = "agreement.rejected"
    AGREEMENT_REVOKED = "agreement.revoked"
    AGREEMENT_TERMINATED = "agreement.terminated"
    DELEGATED_ACCESS_GRANTED = "delegated_access.granted"
    DELEGATED_ACCESS_REVOKED = "delegated_access.revoked"
    PHYSICAL_STATE_UPDATED = "physical_state.updated"

    CHOICES = (
        (AGREEMENT_DRAFTED, "Agreement drafted"),
        (AGREEMENT_PROPOSED, "Agreement proposed"),
        (AGREEMENT_ACCEPTED, "Agreement accepted"),
        (AGREEMENT_ACTIVATED, "Agreement activated"),
        (AGREEMENT_REJECTED, "Agreement rejected"),
        (AGREEMENT_REVOKED, "Agreement revoked"),
        (AGREEMENT_TERMINATED, "Agreement terminated"),
        (DELEGATED_ACCESS_GRANTED, "Delegated access granted"),
        (DELEGATED_ACCESS_REVOKED, "Delegated access revoked"),
        (PHYSICAL_STATE_UPDATED, "Physical state updated"),
    )
    VALUES = frozenset(value for value, _ in CHOICES)


def generate_agreement_reference() -> str:
    return REFERENCE_PREFIX + secrets.token_hex(4).upper()


def compute_idempotency_key(
    action: str, object_type: str, object_id: str, request_id: str = ""
) -> str:
    seed = f"{action}:{object_type}:{object_id}:{request_id}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def clean_metadata(data: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    cleaned = {}
    for key, value in data.items():
        if not isinstance(key, str):
            continue
        lower_key = key.lower()
        if any(secret in lower_key for secret in SENSITIVE_METADATA_KEYS):
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            cleaned[key] = value
        elif isinstance(value, dict):
            cleaned[key] = clean_metadata(value)
        elif isinstance(value, (list, tuple)):
            cleaned[key] = [
                v if isinstance(v, (str, int, float, bool)) or v is None else str(v)
                for v in value
            ]
        else:
            cleaned[key] = str(value)
    return cleaned
