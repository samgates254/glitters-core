"""Identity rules that do not belong to HTTP or to a model save method."""

from __future__ import annotations

import re

E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")
_SEPARATORS_RE = re.compile(r"[\s().-]")

LOGIN_FAILED = "Invalid email or password."
ACCOUNT_BLOCKED = "This account cannot sign in."
VERIFY_EMAIL = "Verify your email before signing in."
GENERIC_REGISTER = "If the email can be used, we sent verification instructions."
GENERIC_RESET = "If the account can be recovered, we sent instructions."
GENERIC_RESEND = "If the account is waiting for verification, we sent instructions."
TOKEN_INVALID = "This token is invalid or expired."  # noqa: S105
CODE_INVALID = "This code is invalid or expired."
PHONE_UNAVAILABLE = "This phone number cannot be verified."
CURRENT_PASSWORD_FAILED = "Current password is not correct."  # noqa: S105
MFA_REQUIRED_FOR_STAFF = "Privileged accounts cannot disable multi-factor authentication."
AUTHENTICATION_REQUIRED = "Authentication is required."

AUTH_THROTTLE_RATES = {
    "auth_login": "10/min",
    "auth_register": "5/hour",
    "auth_password_reset": "5/hour",
    "auth_verify": "10/hour",
    "auth_mfa": "10/min",
    "auth_phone": "5/hour",
}


class AccountStatus:
    PENDING_VERIFICATION = "pending_verification"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DEACTIVATED = "deactivated"
    SECURITY_RESTRICTED = "security_restricted"

    CHOICES = (
        (PENDING_VERIFICATION, "Pending verification"),
        (ACTIVE, "Active"),
        (SUSPENDED, "Suspended"),
        (DEACTIVATED, "Deactivated"),
        (SECURITY_RESTRICTED, "Security restricted"),
    )
    VALUES = frozenset(value for value, _label in CHOICES)


# Self-service and verification paths. Operator actions pass force=True.
TRANSITIONS: dict[str, frozenset[str]] = {
    AccountStatus.PENDING_VERIFICATION: frozenset({AccountStatus.ACTIVE}),
    AccountStatus.ACTIVE: frozenset(
        {
            AccountStatus.SUSPENDED,
            AccountStatus.DEACTIVATED,
            AccountStatus.SECURITY_RESTRICTED,
        }
    ),
    AccountStatus.SUSPENDED: frozenset({AccountStatus.ACTIVE}),
    AccountStatus.DEACTIVATED: frozenset({AccountStatus.ACTIVE}),
    AccountStatus.SECURITY_RESTRICTED: frozenset({AccountStatus.ACTIVE}),
}

RECOVERABLE_STATUSES = frozenset(
    {
        AccountStatus.ACTIVE,
        AccountStatus.PENDING_VERIFICATION,
        AccountStatus.DEACTIVATED,
        AccountStatus.SECURITY_RESTRICTED,
    }
)


def normalize_email(email: str) -> str:
    """Compare the whole address case-insensitively by storing it lowercase."""

    return str(email or "").strip().lower()


def normalize_phone(value: str) -> str:
    """Return E.164.

    A number without a country code is interpreted as Kenyan. Every other
    country must already include the leading plus and country code.
    """

    cleaned = _SEPARATORS_RE.sub("", str(value or "").strip())
    if cleaned.startswith("00"):
        cleaned = f"+{cleaned[2:]}"
    if cleaned.startswith("+"):
        normalized = cleaned
    elif cleaned.startswith("254"):
        normalized = f"+{cleaned}"
    elif cleaned.startswith("0") and len(cleaned) == 10 and cleaned[1] in {"1", "7"}:
        normalized = f"+254{cleaned[1:]}"
    elif len(cleaned) == 9 and cleaned[0] in {"1", "7"}:
        normalized = f"+254{cleaned}"
    else:
        raise ValueError("Enter a phone number in international format.")
    if E164_RE.fullmatch(normalized) is None:
        raise ValueError("Enter a phone number in international format.")
    return normalized


def transition_allowed(current: str, new_status: str, *, force: bool = False) -> bool:
    if new_status not in AccountStatus.VALUES:
        return False
    if force:
        return True
    return new_status in TRANSITIONS.get(current, frozenset())
