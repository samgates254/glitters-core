"""Tenancy domain constants, choices, state machines, and reference generators."""

from __future__ import annotations

import secrets
from typing import Any

# Error messages
NOT_FOUND = "Not found."
PERMISSION_DENIED = "You do not have permission for this action."
CONFLICT = "This change conflicts with another update or an existing reservation."
STATUS_INVALID = "This status transition is not allowed."
INVALID_UNIT = "A lease can only be established for an individual unit or standalone property."
OVERLAPPING_RESERVATION = "This property already has an active lease or pending reservation for the requested dates."
SIGNATURE_DEADLINE_EXPIRED = "The signature window for this lease proposal has expired."
TERMS_CHANGED = "The lease terms have changed and require re-signing."
NOT_READY_FOR_MOVE_IN = "The unit physical profile is not certified ready for move-in."
UNAUTHORIZED_SIGNATORY = "The user is not an authorized signatory for this lease party."
TERMS_HASH_MISMATCH = "The signature evidence does not match the current contract document hash."
ALREADY_SIGNED = "This party has already signed the current document version."

LEASE_PREFIX = "LSE-"
APPLICATION_PREFIX = "APP-"
AMENDMENT_PREFIX = "AMD-"


def generate_lease_reference() -> str:
    return f"{LEASE_PREFIX}{secrets.token_hex(6).upper()}"


def generate_application_reference() -> str:
    return f"{APPLICATION_PREFIX}{secrets.token_hex(6).upper()}"


def generate_amendment_reference() -> str:
    return f"{AMENDMENT_PREFIX}{secrets.token_hex(6).upper()}"


class ApplicationStatus:
    DRAFT = "draft"
    SUBMITTED = "submitted"
    UNDER_REVIEW = "under_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"
    CONVERTED_TO_LEASE = "converted_to_lease"

    CHOICES = (
        (DRAFT, "Draft"),
        (SUBMITTED, "Submitted"),
        (UNDER_REVIEW, "Under Review"),
        (APPROVED, "Approved"),
        (REJECTED, "Rejected"),
        (WITHDRAWN, "Withdrawn"),
        (CONVERTED_TO_LEASE, "Converted to Lease"),
    )
    VALUES = frozenset(val for val, _ in CHOICES)


class EmploymentStatus:
    EMPLOYED = "employed"
    SELF_EMPLOYED = "self_employed"
    BUSINESS_OWNER = "business_owner"
    STUDENT = "student"
    RETIRED = "retired"
    OTHER = "other"

    CHOICES = (
        (EMPLOYED, "Employed"),
        (SELF_EMPLOYED, "Self-Employed"),
        (BUSINESS_OWNER, "Business Owner"),
        (STUDENT, "Student"),
        (RETIRED, "Retired"),
        (OTHER, "Other"),
    )
    VALUES = frozenset(val for val, _ in CHOICES)


class IdDocumentType:
    NATIONAL_ID = "national_id"
    PASSPORT = "passport"
    ALIEN_ID = "alien_id"

    CHOICES = (
        (NATIONAL_ID, "National ID"),
        (PASSPORT, "Passport"),
        (ALIEN_ID, "Alien ID"),
    )
    VALUES = frozenset(val for val, _ in CHOICES)


class LeaseType:
    RESIDENTIAL_FIXED = "residential_fixed"
    RESIDENTIAL_PERIODIC = "residential_periodic"
    COMMERCIAL_FIXED = "commercial_fixed"
    COMMERCIAL_PERIODIC = "commercial_periodic"

    CHOICES = (
        (RESIDENTIAL_FIXED, "Residential Fixed Term"),
        (RESIDENTIAL_PERIODIC, "Residential Periodic"),
        (COMMERCIAL_FIXED, "Commercial Fixed Term"),
        (COMMERCIAL_PERIODIC, "Commercial Periodic"),
    )
    VALUES = frozenset(val for val, _ in CHOICES)


class LeaseStatus:
    DRAFT = "draft"
    PENDING_SIGNATURE = "pending_signature"
    FULLY_EXECUTED = "fully_executed"
    ACTIVE = "active"
    UNDER_RENEWAL = "under_renewal"
    TERMINATED_NORMAL = "terminated_normal"
    TERMINATED_EARLY = "terminated_early"
    EXPIRED_UNSIGNED = "expired_unsigned"
    CANCELLED = "cancelled"

    CHOICES = (
        (DRAFT, "Draft"),
        (PENDING_SIGNATURE, "Pending Signature"),
        (FULLY_EXECUTED, "Fully Executed"),
        (ACTIVE, "Active"),
        (UNDER_RENEWAL, "Under Renewal"),
        (TERMINATED_NORMAL, "Terminated (Normal Expiry)"),
        (TERMINATED_EARLY, "Terminated (Early Termination)"),
        (EXPIRED_UNSIGNED, "Expired Unsigned"),
        (CANCELLED, "Cancelled"),
    )
    VALUES = frozenset(val for val, _ in CHOICES)

    # Statuses that reserve temporal occupancy or preserve historical occupancy in the GiST exclusion constraint
    OCCUPANCY_RESERVING = frozenset({
        PENDING_SIGNATURE,
        FULLY_EXECUTED,
        ACTIVE,
        UNDER_RENEWAL,
        TERMINATED_NORMAL,
        TERMINATED_EARLY,
    })

    # Statuses where the lease has reached legal execution and contractual terms are immutable
    EXECUTED = frozenset({FULLY_EXECUTED, ACTIVE, UNDER_RENEWAL, TERMINATED_NORMAL, TERMINATED_EARLY})


class LeasePartyRole:
    PRIMARY_TENANT = "primary_tenant"
    CO_TENANT = "co_tenant"
    GUARANTOR = "guarantor"
    LANDLORD = "landlord"
    MANAGING_AGENT = "managing_agent"
    AUTHORIZED_OCCUPANT = "authorized_occupant"

    CHOICES = (
        (PRIMARY_TENANT, "Primary Tenant"),
        (CO_TENANT, "Co-Tenant"),
        (GUARANTOR, "Guarantor"),
        (LANDLORD, "Landlord"),
        (MANAGING_AGENT, "Managing Agent"),
        (AUTHORIZED_OCCUPANT, "Authorized Occupant"),
    )
    VALUES = frozenset(val for val, _ in CHOICES)

    # Roles permitted to have contractual signature authority
    SIGNATORY_ROLES = frozenset({PRIMARY_TENANT, CO_TENANT, GUARANTOR, LANDLORD, MANAGING_AGENT})


class PartySignatureStatus:
    PENDING = "pending"
    SIGNED = "signed"
    INVALIDATED = "invalidated"
    EXEMPT = "exempt"

    CHOICES = (
        (PENDING, "Pending"),
        (SIGNED, "Signed"),
        (INVALIDATED, "Invalidated"),
        (EXEMPT, "Exempt"),
    )
    VALUES = frozenset(val for val, _ in CHOICES)


class SignatureMethod:
    OTP_SMS_CONFIRMED = "otp_sms_confirmed"
    PASSWORD_CONFIRMED = "password_confirmed"
    CLICK_TO_SIGN = "click_to_sign"
    BIOMETRIC_OR_ID_VERIFIED = "biometric_or_id_verified"

    CHOICES = (
        (OTP_SMS_CONFIRMED, "OTP SMS Confirmed"),
        (PASSWORD_CONFIRMED, "Password Confirmed"),
        (CLICK_TO_SIGN, "Click to Sign"),
        (BIOMETRIC_OR_ID_VERIFIED, "Biometric/ID Verified"),
    )
    VALUES = frozenset(val for val, _ in CHOICES)


class AmendmentType:
    RENT_ADJUSTMENT = "rent_adjustment"
    TERM_EXTENSION = "term_extension"
    TERM_CONTRACTION = "term_contraction"
    TERMS_VARIATION = "terms_variation"

    CHOICES = (
        (RENT_ADJUSTMENT, "Rent Adjustment"),
        (TERM_EXTENSION, "Term Extension"),
        (TERM_CONTRACTION, "Term Contraction"),
        (TERMS_VARIATION, "Terms Variation"),
    )
    VALUES = frozenset(val for val, _ in CHOICES)


class AmendmentStatus:
    DRAFT = "draft"
    PENDING_SIGNATURE = "pending_signature"
    EXECUTED = "executed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"

    CHOICES = (
        (DRAFT, "Draft"),
        (PENDING_SIGNATURE, "Pending Signature"),
        (EXECUTED, "Executed"),
        (REJECTED, "Rejected"),
        (CANCELLED, "Cancelled"),
    )
    VALUES = frozenset(val for val, _ in CHOICES)


class ScheduleStatus:
    SCHEDULED = "scheduled"
    BILLED = "billed"
    CANCELLED = "cancelled"

    CHOICES = (
        (SCHEDULED, "Scheduled"),
        (BILLED, "Billed"),
        (CANCELLED, "Cancelled"),
    )
    VALUES = frozenset(val for val, _ in CHOICES)


class DepositStatus:
    HELD_PENDING = "held_pending"
    PARTIALLY_DEDUCTED = "partially_deducted"
    REFUND_DUE = "refund_due"
    SETTLED = "settled"

    CHOICES = (
        (HELD_PENDING, "Held Pending"),
        (PARTIALLY_DEDUCTED, "Partially Deducted"),
        (REFUND_DUE, "Refund Due"),
        (SETTLED, "Settled"),
    )
    VALUES = frozenset(val for val, _ in CHOICES)


class DeductionType:
    DAMAGE_REPAIR = "damage_repair"
    CLEANING = "cleaning"
    UNPAID_UTILITIES = "unpaid_utilities"
    HOLDOVER_DAMAGES = "holdover_damages"
    CONTRACTUAL_BREACH = "contractual_breach"

    CHOICES = (
        (DAMAGE_REPAIR, "Damage Repair"),
        (CLEANING, "Cleaning"),
        (UNPAID_UTILITIES, "Unpaid Utilities"),
        (HOLDOVER_DAMAGES, "Holdover Damages"),
        (CONTRACTUAL_BREACH, "Contractual Breach"),
    )
    VALUES = frozenset(val for val, _ in CHOICES)


class DeductionDisputeStatus:
    PROPOSED = "proposed"
    ACCEPTED_BY_TENANT = "accepted_by_tenant"
    DISPUTED = "disputed"
    ARBITRATED = "arbitrated"

    CHOICES = (
        (PROPOSED, "Proposed"),
        (ACCEPTED_BY_TENANT, "Accepted by Tenant"),
        (DISPUTED, "Disputed"),
        (ARBITRATED, "Arbitrated"),
    )
    VALUES = frozenset(val for val, _ in CHOICES)


class HandoverType:
    MOVE_IN = "move_in"
    MOVE_OUT = "move_out"

    CHOICES = (
        (MOVE_IN, "Move-In Handover"),
        (MOVE_OUT, "Move-Out Handover"),
    )
    VALUES = frozenset(val for val, _ in CHOICES)


class HandoverStatus:
    DRAFT = "draft"
    PENDING_TENANT_SIGNATURE = "pending_tenant_signature"
    PENDING_AGENT_SIGNATURE = "pending_agent_signature"
    EXECUTED = "executed"
    FAILED_HOLDOVER = "failed_holdover"
    DISPUTED = "disputed"

    CHOICES = (
        (DRAFT, "Draft"),
        (PENDING_TENANT_SIGNATURE, "Pending Tenant Signature"),
        (PENDING_AGENT_SIGNATURE, "Pending Agent Signature"),
        (EXECUTED, "Executed"),
        (FAILED_HOLDOVER, "Failed (Tenant Holdover)"),
        (DISPUTED, "Disputed"),
    )
    VALUES = frozenset(val for val, _ in CHOICES)


class TenancyEventAction:
    APPLICATION_SUBMITTED = "application_submitted"
    APPLICATION_VETTED = "application_vetted"
    APPLICATION_CONVERTED = "application_converted"
    LEASE_DRAFTED = "lease_drafted"
    LEASE_TERMS_APPROVED = "lease_terms_approved"
    LEASE_PARTY_ADDED = "lease_party_added"
    LEASE_PARTY_SIGNED = "lease_party_signed"
    LEASE_TERMS_INVALIDATED = "lease_terms_invalidated"
    LEASE_FULLY_EXECUTED = "lease_fully_executed"
    LEASE_ACTIVATED = "lease_activated"
    LEASE_RENEWAL_INITIATED = "lease_renewal_initiated"
    LEASE_AMENDED = "lease_amended"
    LEASE_TERMINATED = "lease_terminated"
    LEASE_RESERVATION_EXPIRED = "lease_reservation_expired"
    RENT_SCHEDULE_GENERATED = "rent_schedule_generated"
    RENT_SCHEDULE_CANCELLED = "rent_schedule_cancelled"
    DEPOSIT_OBLIGATION_CREATED = "deposit_obligation_created"
    DEPOSIT_DEDUCTION_PROPOSED = "deposit_deduction_proposed"
    DEPOSIT_DEDUCTION_RESOLVED = "deposit_deduction_resolved"
    HANDOVER_PROTOCOL_CREATED = "handover_protocol_created"
    HANDOVER_PROTOCOL_EXECUTED = "handover_protocol_executed"
    HANDOVER_HOLDOVER_RECORDED = "handover_holdover_recorded"

    CHOICES = (
        (APPLICATION_SUBMITTED, "Application Submitted"),
        (APPLICATION_VETTED, "Application Vetted"),
        (APPLICATION_CONVERTED, "Application Converted"),
        (LEASE_DRAFTED, "Lease Drafted"),
        (LEASE_TERMS_APPROVED, "Lease Terms Approved"),
        (LEASE_PARTY_ADDED, "Lease Party Added"),
        (LEASE_PARTY_SIGNED, "Lease Party Signed"),
        (LEASE_TERMS_INVALIDATED, "Lease Terms Invalidated"),
        (LEASE_FULLY_EXECUTED, "Lease Fully Executed"),
        (LEASE_ACTIVATED, "Lease Activated"),
        (LEASE_RENEWAL_INITIATED, "Lease Renewal Initiated"),
        (LEASE_AMENDED, "Lease Amended"),
        (LEASE_TERMINATED, "Lease Terminated"),
        (LEASE_RESERVATION_EXPIRED, "Lease Reservation Expired"),
        (RENT_SCHEDULE_GENERATED, "Rent Schedule Generated"),
        (RENT_SCHEDULE_CANCELLED, "Rent Schedule Cancelled"),
        (DEPOSIT_OBLIGATION_CREATED, "Deposit Obligation Created"),
        (DEPOSIT_DEDUCTION_PROPOSED, "Deposit Deduction Proposed"),
        (DEPOSIT_DEDUCTION_RESOLVED, "Deposit Deduction Resolved"),
        (HANDOVER_PROTOCOL_CREATED, "Handover Protocol Created"),
        (HANDOVER_PROTOCOL_EXECUTED, "Handover Protocol Executed"),
        (HANDOVER_HOLDOVER_RECORDED, "Handover Holdover Recorded"),
    )
    VALUES = frozenset(val for val, _ in CHOICES)
