"""Lease draft, approval, cancellation, renewal, expiry, signing, activation, and termination.

These functions do not mint property grants and do not mount an HTTP route.
Occupancy conflicts are pre-checked under row locks. The GiST exclusion
constraint remains the backstop. Cancellation drops a draft or an unsigned
reservation out of that exclusion without rewriting contractual fields.
Renewal creates a new draft and does not rewrite the signed digest.
Signing stores evidence and reaches fully_executed only. Activation starts
occupancy. Termination keeps the signed digest and never extends the reserved
interval. Executed leases are terminated, not cancelled.
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timedelta
from typing import Any

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_ipv46_address
from django.db import IntegrityError, OperationalError, transaction
from django.utils import timezone

from apps.identity.domain import AccountStatus
from apps.identity.models import User
from apps.operations.domain import PhysicalState, physical_state_transition_allowed
from apps.operations.errors import OperationsError
from apps.operations.models import UnitPhysicalProfile
from apps.operations.services.agreements import (
    verify_lease_execution_authority,
    verify_owner_authority,
)
from apps.operations.services.physical_profiles import update_physical_profile
from apps.organizations.domain import Permission
from apps.organizations.errors import OrganizationError
from apps.organizations.services.authorization import authorize
from apps.properties.models import Property
from apps.properties.services.locking import is_deadlock, lock_properties
from apps.tenancy.domain import (
    ALREADY_SIGNED,
    CONFLICT,
    NOT_FOUND,
    NOT_READY_FOR_MOVE_IN,
    OVERLAPPING_RESERVATION,
    PERMISSION_DENIED,
    SIGNATURE_DEADLINE_EXPIRED,
    STATUS_INVALID,
    TERMS_HASH_MISMATCH,
    UNAUTHORIZED_SIGNATORY,
    HandoverStatus,
    HandoverType,
    LeasePartyRole,
    LeaseStatus,
    LeaseType,
    PartySignatureStatus,
    ScheduleStatus,
    SignatureMethod,
    TenancyEventAction,
)
from apps.tenancy.errors import TenancyError
from apps.tenancy.models import (
    HandoverProtocol,
    Lease,
    LeaseParty,
    LeaseSignatureEvidence,
    RentalApplication,
    RentScheduleEntry,
    TenancyEvent,
)
from apps.tenancy.services.audit import record_tenancy_event, tenancy_idempotency_key
from apps.tenancy.services.authority import (
    actor_can_maintain_reservations,
    require_actor,
    resolve_lease_write_authority,
)
from apps.tenancy.services.document import (
    compute_contract_data_hash,
    compute_rendered_document_hash,
    contract_payload_for_lease,
)
from apps.tenancy.services.validation import validate_rentable_unit

MAX_RENDERED_BYTES = 20_971_520
MAX_DEADLINE_HOURS = 14 * 24
MAX_COVENANT_CHARS = 20_000
_CURRENCY = re.compile(r"^[A-Z]{3}$")


def create_draft_lease(
    *,
    actor,
    property_id,
    payload: dict[str, Any],
    idempotency_key: str | None = None,
    organization_id=None,
) -> Lease:
    """Create one draft. A repeated client key returns that same draft.

    ``effective_end_date`` stays null. Drafts do not reserve occupancy.
    The landlord party is the direct owner, not the preparing agent.
    """

    require_actor(actor)
    if not isinstance(payload, dict):
        raise TenancyError("Lease terms are required.", status_code=400)
    client_key = _clean_client_key(idempotency_key)
    try:
        with transaction.atomic():
            property_record = _lock_one_property(property_id)
            authority = resolve_lease_write_authority(
                actor,
                property_record,
                permission=Permission.LEASES_PREPARE,
                organization_id=organization_id,
            )
            if client_key:
                replay = _replay_draft(actor, client_key, property_record.id)
                if replay is not None:
                    return replay
            _validate_unit(property_record)
            terms = _parse_terms(payload)
            parties = _parse_parties(payload.get("parties"), authority.property_owner)
            application = _load_application(payload.get("application_id"), property_record, parties)
            lease = Lease(
                property=property_record,
                property_owner=authority.property_owner,
                managing_organization=authority.managing_organization,
                management_agreement=authority.management_agreement,
                application=application,
                status=LeaseStatus.DRAFT,
                document_version=1,
                effective_end_date=None,
                created_by=actor,
                **terms,
            )
            lease.save()
            created = []
            for spec in parties:
                created.append(
                    LeaseParty.objects.create(
                        lease=lease,
                        user=spec["user"],
                        party_role=spec["party_role"],
                        has_signature_authority=spec["has_signature_authority"],
                    )
                )
            draft_key = (
                tenancy_idempotency_key("lease_drafted", actor.id, client_key)
                if client_key
                else tenancy_idempotency_key("lease_drafted", lease.id)
            )
            record_tenancy_event(
                actor=actor,
                lease=lease,
                action=TenancyEventAction.LEASE_DRAFTED,
                object_type="lease",
                object_id=lease.id,
                idempotency_key=draft_key,
                metadata={
                    "reference": lease.reference,
                    "property_id": str(property_record.id),
                    "lease_type": lease.lease_type,
                    "rent_amount_minor": lease.rent_amount_minor,
                    "rent_currency": lease.rent_currency,
                    "start_date": lease.start_date.isoformat(),
                    "term_end_date": lease.term_end_date.isoformat(),
                },
            )
            for party in created:
                record_tenancy_event(
                    actor=actor,
                    lease=lease,
                    action=TenancyEventAction.LEASE_PARTY_ADDED,
                    object_type="lease_party",
                    object_id=party.id,
                    idempotency_key=tenancy_idempotency_key(
                        "lease_party_added", lease.id, party.user_id, party.party_role
                    ),
                    metadata={
                        "party_role": party.party_role,
                        "user_id": str(party.user_id),
                        "has_signature_authority": party.has_signature_authority,
                    },
                )
            return lease
    except TenancyError:
        raise
    except DjangoValidationError as exc:
        _raise_validation(exc)
    except IntegrityError as exc:
        if client_key:
            replay = _replay_draft_outside(actor, client_key, property_id)
            if replay is not None:
                return replay
        raise TenancyError(CONFLICT, status_code=409) from exc
    except OperationalError as exc:
        if is_deadlock(exc):
            raise TenancyError(CONFLICT, status_code=409) from exc
        raise


def approve_lease_terms(
    *,
    actor,
    lease_id,
    rendered_document: bytes,
    rendered_document_uri: str = "",
    deadline_hours: int = 72,
    now: datetime | None = None,
) -> Lease:
    """Move a complete draft to pending signature and reserve the unit.

    Due reservations on the same unit are expired in this transaction before
    the new interval is stored. A later failure rolls that expiry back. The
    expiry key makes the retry idempotent.
    """

    require_actor(actor)
    moment = _aware_moment(now)
    document = _rendered_bytes(rendered_document)
    uri = _rendered_uri(rendered_document_uri)
    hours = _deadline_hours(deadline_hours)
    try:
        with transaction.atomic():
            hint = Lease.objects.filter(pk=lease_id).values("property_id").first()
            if hint is None:
                raise TenancyError(NOT_FOUND, status_code=404)
            _property_record, locked_leases = _lock_property_leases(hint["property_id"])
            _expire_loaded(locked_leases, moment, actor)
            lease = _locked_lease(lease_id)
            if lease.property_id != hint["property_id"]:
                raise TenancyError(CONFLICT, status_code=409)
            _assert_approval_authority(actor, lease)
            if lease.status != LeaseStatus.DRAFT:
                raise TenancyError(STATUS_INVALID, status_code=409)
            parties = list(lease.parties.select_related("user"))
            _require_approvable_terms(lease, parties)
            exclusive_end = lease.term_end_date + timedelta(days=1)
            if _reservation_conflict(lease, lease.start_date, exclusive_end):
                raise TenancyError(OVERLAPPING_RESERVATION, status_code=409)
            payload = contract_payload_for_lease(lease, parties)
            lease.contract_data_hash = compute_contract_data_hash(payload)
            lease.rendered_document_hash = compute_rendered_document_hash(document)
            lease.rendered_document_uri = uri
            lease.effective_end_date = exclusive_end
            lease.signature_deadline = moment + timedelta(hours=hours)
            lease.status = LeaseStatus.PENDING_SIGNATURE
            lease.save()
            record_tenancy_event(
                actor=actor,
                lease=lease,
                action=TenancyEventAction.LEASE_TERMS_APPROVED,
                object_type="lease",
                object_id=lease.id,
                idempotency_key=tenancy_idempotency_key(
                    "lease_terms_approved",
                    lease.id,
                    lease.document_version,
                    lease.contract_data_hash,
                ),
                metadata={
                    "reference": lease.reference,
                    "document_version": lease.document_version,
                    "contract_data_hash": lease.contract_data_hash,
                    "rendered_document_hash": lease.rendered_document_hash,
                    "signature_deadline": lease.signature_deadline.isoformat(),
                    "effective_end_date": lease.effective_end_date.isoformat(),
                },
            )
            return lease
    except TenancyError:
        raise
    except DjangoValidationError as exc:
        _raise_validation(exc)
    except IntegrityError as exc:
        if "tenancy_lease_exclude_overlapping_contractual_leases" in str(exc):
            raise TenancyError(OVERLAPPING_RESERVATION, status_code=409) from exc
        raise TenancyError(CONFLICT, status_code=409) from exc
    except OperationalError as exc:
        if is_deadlock(exc):
            raise TenancyError(CONFLICT, status_code=409) from exc
        raise


def expire_due_reservations(
    *,
    property_id=None,
    now: datetime | None = None,
    actor=None,
    trusted_system: bool = False,
) -> int:
    """Expire pending reservations whose signature deadline is at or before now.

    ``now >= signature_deadline`` is expired. A second call does not add an
    audit row or change an already expired lease. A full sweep requires
    ``trusted_system``. Callers naming one property must be allowed to
    prepare or approve that property, or hold direct management.
    """

    moment = _aware_moment(now)
    if not trusted_system:
        require_actor(actor)
        if property_id is None:
            raise TenancyError(PERMISSION_DENIED, status_code=403)
        property_record = Property.objects.filter(pk=property_id).first()
        if property_record is None:
            raise TenancyError(NOT_FOUND, status_code=404)
        if not actor_can_maintain_reservations(actor, property_record):
            raise TenancyError(PERMISSION_DENIED, status_code=403)
        with transaction.atomic():
            return _expire_property(property_record.id, moment, actor)

    due_property_ids = Lease.objects.filter(
        status=LeaseStatus.PENDING_SIGNATURE,
        signature_deadline__isnull=False,
        signature_deadline__lte=moment,
    )
    if property_id is not None:
        due_property_ids = due_property_ids.filter(property_id=property_id)
    property_ids = sorted(
        set(due_property_ids.values_list("property_id", flat=True)),
        key=str,
    )
    expired = 0
    for one_property_id in property_ids:
        with transaction.atomic():
            expired += _expire_property(one_property_id, moment, None)
    return expired


def sign_lease(
    *,
    actor,
    lease_id,
    party_id,
    rendered_document: bytes,
    signature_method: str,
    ip_address: str | None = None,
    user_agent: str = "",
    idempotency_key: str | None = None,
    now: datetime | None = None,
) -> LeaseSignatureEvidence:
    """Record one party's signature. The last required signature executes the lease.

    Execution here means fully_executed. It does not start occupancy.
    A repeated client key returns the original evidence. A second distinct
    attempt for the same party and document version is rejected.
    """

    require_actor(actor)
    moment = _aware_moment(now)
    document = _rendered_bytes(rendered_document)
    method = _signature_method(signature_method)
    address = _ip_address(ip_address)
    agent = _user_agent(user_agent)
    client_key = _clean_client_key(idempotency_key)
    try:
        with transaction.atomic():
            hint = Lease.objects.filter(pk=lease_id).values("property_id").first()
            if hint is None:
                raise TenancyError(NOT_FOUND, status_code=404)
            _lock_property_leases(hint["property_id"])
            lease = _locked_lease(lease_id)
            if lease.status != LeaseStatus.PENDING_SIGNATURE:
                raise TenancyError(STATUS_INVALID, status_code=409)
            deadline = lease.signature_deadline
            if deadline is None or moment >= deadline:
                raise TenancyError(SIGNATURE_DEADLINE_EXPIRED, status_code=409)
            party = _locked_party(party_id)
            if party.lease_id != lease.id or party.user_id != actor.id:
                raise TenancyError(UNAUTHORIZED_SIGNATORY, status_code=403)
            if (
                not party.has_signature_authority
                or party.party_role not in LeasePartyRole.SIGNATORY_ROLES
            ):
                raise TenancyError(UNAUTHORIZED_SIGNATORY, status_code=403)
            parties = list(lease.parties.select_related("user"))
            payload = contract_payload_for_lease(lease, parties)
            data_hash = compute_contract_data_hash(payload)
            rendered_hash = compute_rendered_document_hash(document)
            if (
                data_hash != lease.contract_data_hash
                or rendered_hash != lease.rendered_document_hash
            ):
                raise TenancyError(TERMS_HASH_MISMATCH, status_code=409)
            evidence_key = _signature_key(lease, party, client_key)
            existing = LeaseSignatureEvidence.objects.filter(
                lease=lease,
                party=party,
                document_version=lease.document_version,
            ).first()
            if existing is not None:
                if client_key and existing.idempotency_key == evidence_key:
                    return existing
                raise TenancyError(ALREADY_SIGNED, status_code=409)
            evidence = LeaseSignatureEvidence(
                idempotency_key=evidence_key,
                lease=lease,
                party=party,
                signatory_user=actor,
                signatory_role=party.party_role,
                document_version=lease.document_version,
                contract_data_hash=data_hash,
                rendered_document_hash=rendered_hash,
                rendered_document_uri=lease.rendered_document_uri,
                signature_method=method,
                ip_address=address,
                user_agent=agent,
            )
            evidence.save()
            party.signature_status = PartySignatureStatus.SIGNED
            party.signed_at = moment
            party.save(update_fields=["signature_status", "signed_at", "updated_at"])
            record_tenancy_event(
                actor=actor,
                lease=lease,
                action=TenancyEventAction.LEASE_PARTY_SIGNED,
                object_type="lease_signature_evidence",
                object_id=evidence.id,
                idempotency_key=tenancy_idempotency_key("event", evidence_key),
                metadata={
                    "party_role": party.party_role,
                    "document_version": lease.document_version,
                    "contract_data_hash": data_hash,
                    "rendered_document_hash": rendered_hash,
                },
            )
            _execute_if_complete(lease, moment, actor)
            return evidence
    except TenancyError:
        raise
    except DjangoValidationError as exc:
        _raise_validation(exc)
    except IntegrityError as exc:
        raise TenancyError(ALREADY_SIGNED, status_code=409) from exc
    except OperationalError as exc:
        if is_deadlock(exc):
            raise TenancyError(CONFLICT, status_code=409) from exc
        raise


def activate_lease(*, actor, lease_id, now: datetime | None = None) -> Lease:
    """Start occupancy after execution, an executed move-in, and a ready unit."""

    require_actor(actor)
    moment = _aware_moment(now)
    try:
        with transaction.atomic():
            hint = Lease.objects.filter(pk=lease_id).values("property_id").first()
            if hint is None:
                raise TenancyError(NOT_FOUND, status_code=404)
            _lock_property_leases(hint["property_id"])
            lease = _locked_lease(lease_id)
            _require_execution_authority(actor, lease.property, Permission.LEASES_EXECUTE)
            if lease.status != LeaseStatus.FULLY_EXECUTED:
                raise TenancyError(STATUS_INVALID, status_code=409)
            ready = HandoverProtocol.objects.filter(
                lease=lease,
                protocol_type=HandoverType.MOVE_IN,
                status=HandoverStatus.EXECUTED,
            ).exists()
            profile = UnitPhysicalProfile.objects.filter(property_id=lease.property_id).first()
            inspected = (
                profile is not None and profile.physical_state == PhysicalState.READY_INSPECTED
            )
            if not ready or not inspected:
                raise TenancyError(NOT_READY_FOR_MOVE_IN, status_code=409)
            lease.status = LeaseStatus.ACTIVE
            lease.activated_at = moment
            lease.save(update_fields=["status", "activated_at", "updated_at"])
            update_physical_profile(
                actor=actor,
                property_id=lease.property_id,
                new_state=PhysicalState.OCCUPIED_NORMAL,
            )
            record_tenancy_event(
                actor=actor,
                lease=lease,
                action=TenancyEventAction.LEASE_ACTIVATED,
                object_type="lease",
                object_id=lease.id,
                idempotency_key=tenancy_idempotency_key(
                    "lease_activated", lease.id, lease.document_version
                ),
                metadata={"reference": lease.reference, "activated_at": moment.isoformat()},
            )
            return lease
    except TenancyError:
        raise
    except OperationsError as exc:
        raise TenancyError(exc.detail, status_code=exc.status_code) from exc
    except DjangoValidationError as exc:
        _raise_validation(exc)
    except OperationalError as exc:
        if is_deadlock(exc):
            raise TenancyError(CONFLICT, status_code=409) from exc
        raise


def terminate_lease(
    *,
    actor,
    lease_id,
    termination_date: date,
    reason: str,
    now: datetime | None = None,
) -> Lease:
    """End occupancy without rewriting the signed contract.

    The exclusive occupancy end is the day after the termination date, and
    it is never moved later than the reservation already stored. Contractual
    term end, rent, and the contract digest stay as signed.
    """

    require_actor(actor)
    moment = _aware_moment(now)
    if isinstance(termination_date, datetime) or not isinstance(termination_date, date):
        raise TenancyError("The termination date must be a date.", status_code=400)
    if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 2000:
        raise TenancyError("A termination reason is required.", status_code=400)
    reason = reason.strip()
    try:
        with transaction.atomic():
            hint = Lease.objects.filter(pk=lease_id).values("property_id").first()
            if hint is None:
                raise TenancyError(NOT_FOUND, status_code=404)
            _lock_property_leases(hint["property_id"])
            lease = _locked_lease(lease_id)
            _require_execution_authority(actor, lease.property, Permission.LEASES_TERMINATE)
            allowed = {
                LeaseStatus.FULLY_EXECUTED,
                LeaseStatus.ACTIVE,
                LeaseStatus.UNDER_RENEWAL,
            }
            if lease.status not in allowed:
                raise TenancyError(STATUS_INVALID, status_code=409)
            if termination_date < lease.start_date:
                raise TenancyError(
                    "Termination cannot be before the lease start date.",
                    status_code=400,
                )
            if lease.term_end_date is not None and termination_date > lease.term_end_date:
                raise TenancyError(
                    "Termination cannot be after the contractual term end.",
                    status_code=400,
                )
            if lease.term_end_date is not None and termination_date == lease.term_end_date:
                new_status = LeaseStatus.TERMINATED_NORMAL
            else:
                new_status = LeaseStatus.TERMINATED_EARLY
            preserved_term_end = lease.term_end_date
            preserved_hash = lease.contract_data_hash
            preserved_rent = lease.rent_amount_minor
            exclusive_end = termination_date + timedelta(days=1)
            if lease.effective_end_date is not None and exclusive_end > lease.effective_end_date:
                exclusive_end = lease.effective_end_date
            if exclusive_end <= lease.start_date:
                raise TenancyError(
                    "Termination does not leave a valid occupancy interval.",
                    status_code=400,
                )
            lease.actual_termination_date = termination_date
            lease.effective_end_date = exclusive_end
            lease.terminated_at = moment
            lease.termination_reason = reason
            lease.status = new_status
            lease.save(
                update_fields=[
                    "status",
                    "actual_termination_date",
                    "effective_end_date",
                    "terminated_at",
                    "termination_reason",
                    "updated_at",
                ]
            )
            cancelled = RentScheduleEntry.objects.filter(
                lease=lease,
                status=ScheduleStatus.SCHEDULED,
                period_start__gte=termination_date,
            ).update(
                status=ScheduleStatus.CANCELLED,
                cancelled_at=moment,
                cancellation_reason=reason,
                updated_at=moment,
            )
            _release_occupancy(actor, lease.property_id)
            record_tenancy_event(
                actor=actor,
                lease=lease,
                action=TenancyEventAction.LEASE_TERMINATED,
                object_type="lease",
                object_id=lease.id,
                idempotency_key=tenancy_idempotency_key(
                    "lease_terminated",
                    lease.id,
                    lease.document_version,
                    termination_date.isoformat(),
                ),
                metadata={
                    "status": new_status,
                    "termination_date": termination_date.isoformat(),
                    "effective_end_date": exclusive_end.isoformat(),
                    "term_end_date": (
                        preserved_term_end.isoformat() if preserved_term_end else None
                    ),
                    "contract_data_hash": preserved_hash,
                    "rent_amount_minor": preserved_rent,
                },
            )
            if cancelled:
                record_tenancy_event(
                    actor=actor,
                    lease=lease,
                    action=TenancyEventAction.RENT_SCHEDULE_CANCELLED,
                    object_type="lease",
                    object_id=lease.id,
                    idempotency_key=tenancy_idempotency_key(
                        "rent_schedule_cancelled",
                        lease.id,
                        termination_date.isoformat(),
                    ),
                    metadata={"cancelled_count": cancelled},
                )
            lease.refresh_from_db()
            return lease
    except TenancyError:
        raise
    except OperationsError as exc:
        raise TenancyError(exc.detail, status_code=exc.status_code) from exc
    except DjangoValidationError as exc:
        _raise_validation(exc)
    except OperationalError as exc:
        if is_deadlock(exc):
            raise TenancyError(CONFLICT, status_code=409) from exc
        raise


def cancel_lease(
    *,
    actor,
    lease_id,
    reason: str,
    idempotency_key: str | None = None,
) -> Lease:
    """Cancel a draft or an unsigned reservation without rewriting its terms.

    Executed leases cannot be cancelled. Pending contractual fields stay as
    approved. A repeated client key returns the cancelled lease.
    """

    require_actor(actor)
    if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 2000:
        raise TenancyError("A cancellation reason is required.", status_code=400)
    reason = reason.strip()
    client_key = _clean_client_key(idempotency_key)
    try:
        with transaction.atomic():
            hint = Lease.objects.filter(pk=lease_id).values("property_id").first()
            if hint is None:
                raise TenancyError(NOT_FOUND, status_code=404)
            _lock_property_leases(hint["property_id"])
            lease = _locked_lease(lease_id)
            if not _may_cancel(actor, lease):
                raise TenancyError(PERMISSION_DENIED, status_code=403)
            event_key = None
            if client_key:
                event_key = tenancy_idempotency_key("lease_cancelled", lease.id, client_key)
                if TenancyEvent.objects.filter(idempotency_key=event_key).exists():
                    return lease
            cancellable = {LeaseStatus.DRAFT, LeaseStatus.PENDING_SIGNATURE}
            if lease.status not in cancellable:
                raise TenancyError(STATUS_INVALID, status_code=409)
            previous = lease.status
            preserved_hash = lease.contract_data_hash
            preserved_rent = lease.rent_amount_minor
            preserved_end = lease.effective_end_date
            lease.status = LeaseStatus.CANCELLED
            lease.save(update_fields=["status", "updated_at"])
            record_tenancy_event(
                actor=actor,
                lease=lease,
                action=TenancyEventAction.LEASE_CANCELLED,
                object_type="lease",
                object_id=lease.id,
                idempotency_key=event_key
                or tenancy_idempotency_key("lease_cancelled", lease.id, previous),
                metadata={
                    "previous_status": previous,
                    "reason": reason,
                    "contract_data_hash": preserved_hash,
                },
            )
            lease.refresh_from_db()
            if (
                lease.contract_data_hash != preserved_hash
                or lease.rent_amount_minor != preserved_rent
                or lease.effective_end_date != preserved_end
            ):
                raise TenancyError(CONFLICT, status_code=409)
            return lease
    except TenancyError:
        raise
    except DjangoValidationError as exc:
        _raise_validation(exc)
    except OperationalError as exc:
        if is_deadlock(exc):
            raise TenancyError(CONFLICT, status_code=409) from exc
        raise


def _may_cancel(actor, lease: Lease) -> bool:
    if lease.property_owner_id == actor.id or lease.created_by_id == actor.id:
        return True
    try:
        verify_owner_authority(actor, lease.property)
        return True
    except OperationsError:
        pass
    if lease.managing_organization_id is None:
        return False
    for permission in (Permission.LEASES_PREPARE, Permission.LEASES_APPROVE):
        try:
            authorize(actor, lease.managing_organization_id, permission)
            return True
        except OrganizationError:
            continue
    return False


_CLOSED_RENEWAL = (LeaseStatus.CANCELLED, LeaseStatus.EXPIRED_UNSIGNED)


def _live_renewals(lease: Lease):
    return lease.renewed_by.exclude(status__in=_CLOSED_RENEWAL)


def _authorize_renewal(actor, lease: Lease, organization_id) -> None:
    """Direct owners, or the current managing preparer. Membership is not title."""

    if organization_id is not None and organization_id != lease.managing_organization_id:
        raise TenancyError(PERMISSION_DENIED, status_code=403)
    if organization_id is None:
        try:
            verify_owner_authority(actor, lease.property)
        except OperationsError as exc:
            raise TenancyError(PERMISSION_DENIED, status_code=403) from exc
        return
    authority = resolve_lease_write_authority(
        actor,
        lease.property,
        permission=Permission.LEASES_PREPARE,
        organization_id=organization_id,
    )
    if lease.management_agreement_id != getattr(authority.management_agreement, "id", None):
        raise TenancyError(PERMISSION_DENIED, status_code=403)
    if lease.property_owner_id != authority.property_owner.id:
        raise TenancyError(PERMISSION_DENIED, status_code=403)


def _snapshot_contract(lease: Lease) -> dict[str, Any]:
    return {
        "contract_data_hash": lease.contract_data_hash,
        "rent_amount_minor": lease.rent_amount_minor,
        "term_end_date": lease.term_end_date,
        "effective_end_date": lease.effective_end_date,
        "document_version": lease.document_version,
        "start_date": lease.start_date,
    }


def _contract_unchanged(lease: Lease, snapshot: dict[str, Any]) -> bool:
    return all(getattr(lease, field) == value for field, value in snapshot.items())


def initiate_lease_renewal(
    *,
    actor,
    lease_id,
    payload: dict[str, Any],
    idempotency_key: str | None = None,
    organization_id=None,
) -> Lease:
    """Open a new draft that supersedes an active lease.

    The predecessor status becomes under_renewal. Its rent, dates, document
    version, and contract digest stay as signed. The new row starts at
    document version 1 and does not reserve occupancy until it is approved.
    """

    require_actor(actor)
    if not isinstance(payload, dict):
        raise TenancyError("Lease terms are required.", status_code=400)
    client_key = _clean_client_key(idempotency_key)
    try:
        with transaction.atomic():
            hint = Lease.objects.filter(pk=lease_id).values("property_id").first()
            if hint is None:
                raise TenancyError(NOT_FOUND, status_code=404)
            _lock_property_leases(hint["property_id"])
            predecessor = _locked_lease(lease_id)
            _authorize_renewal(actor, predecessor, organization_id)
            event_key = None
            if client_key:
                event_key = tenancy_idempotency_key(
                    "lease_renewal_initiated", predecessor.id, client_key
                )
                existing = TenancyEvent.objects.filter(idempotency_key=event_key).first()
                if existing is not None:
                    successor_id = (existing.metadata or {}).get("successor_lease_id")
                    successor = Lease.objects.filter(
                        pk=successor_id,
                        supersedes_lease=predecessor,
                    ).first()
                    if successor is None:
                        raise TenancyError(CONFLICT, status_code=409)
                    return successor
            allowed = {LeaseStatus.ACTIVE, LeaseStatus.UNDER_RENEWAL}
            if predecessor.status not in allowed:
                raise TenancyError(STATUS_INVALID, status_code=409)
            if predecessor.effective_end_date is None:
                raise TenancyError(STATUS_INVALID, status_code=409)
            if _live_renewals(predecessor).exists():
                raise TenancyError(
                    "This lease already has a renewal in progress.",
                    status_code=409,
                )
            snapshot = _snapshot_contract(predecessor)
            previous = predecessor.status
            if predecessor.status == LeaseStatus.ACTIVE:
                predecessor.status = LeaseStatus.UNDER_RENEWAL
                predecessor.save(update_fields=["status", "updated_at"])
            terms = _parse_terms(payload)
            parties = _parse_parties(payload.get("parties"), predecessor.property_owner)
            successor = Lease(
                property=predecessor.property,
                property_owner=predecessor.property_owner,
                managing_organization=predecessor.managing_organization,
                management_agreement=predecessor.management_agreement,
                supersedes_lease=predecessor,
                status=LeaseStatus.DRAFT,
                document_version=1,
                effective_end_date=None,
                created_by=actor,
                **terms,
            )
            successor.save()
            created_parties = [
                LeaseParty.objects.create(
                    lease=successor,
                    user=spec["user"],
                    party_role=spec["party_role"],
                    has_signature_authority=spec["has_signature_authority"],
                )
                for spec in parties
            ]
            record_tenancy_event(
                actor=actor,
                lease=predecessor,
                action=TenancyEventAction.LEASE_RENEWAL_INITIATED,
                object_type="lease",
                object_id=predecessor.id,
                idempotency_key=event_key
                or tenancy_idempotency_key("lease_renewal_initiated", predecessor.id, successor.id),
                metadata={
                    "successor_lease_id": str(successor.id),
                    "previous_status": previous,
                    "contract_data_hash": snapshot["contract_data_hash"],
                },
            )
            record_tenancy_event(
                actor=actor,
                lease=successor,
                action=TenancyEventAction.LEASE_DRAFTED,
                object_type="lease",
                object_id=successor.id,
                idempotency_key=tenancy_idempotency_key("lease_drafted", successor.id),
                metadata={
                    "reference": successor.reference,
                    "supersedes_lease_id": str(predecessor.id),
                    "rent_amount_minor": successor.rent_amount_minor,
                    "start_date": successor.start_date.isoformat(),
                    "term_end_date": successor.term_end_date.isoformat(),
                },
            )
            for party in created_parties:
                record_tenancy_event(
                    actor=actor,
                    lease=successor,
                    action=TenancyEventAction.LEASE_PARTY_ADDED,
                    object_type="lease_party",
                    object_id=party.id,
                    idempotency_key=tenancy_idempotency_key(
                        "lease_party_added", successor.id, party.user_id, party.party_role
                    ),
                    metadata={
                        "party_role": party.party_role,
                        "user_id": str(party.user_id),
                        "has_signature_authority": party.has_signature_authority,
                    },
                )
            predecessor.refresh_from_db()
            if not _contract_unchanged(predecessor, snapshot):
                raise TenancyError(CONFLICT, status_code=409)
            if predecessor.status != LeaseStatus.UNDER_RENEWAL:
                raise TenancyError(CONFLICT, status_code=409)
            return successor
    except TenancyError:
        raise
    except DjangoValidationError as exc:
        _raise_validation(exc)
    except IntegrityError as exc:
        if "tenancy_lease_exclude_overlapping_contractual_leases" in str(exc):
            raise TenancyError(OVERLAPPING_RESERVATION, status_code=409) from exc
        raise TenancyError(CONFLICT, status_code=409) from exc
    except OperationalError as exc:
        if is_deadlock(exc):
            raise TenancyError(CONFLICT, status_code=409) from exc
        raise


def abandon_lease_renewal(
    *,
    actor,
    lease_id,
    reason: str,
    idempotency_key: str | None = None,
    organization_id=None,
) -> Lease:
    """Return an under-renewal lease to active when no successor is still open.

    Contractual fields stay as they were. A live renewal draft must be
    cancelled first. This does not terminate the lease.
    """

    require_actor(actor)
    if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 2000:
        raise TenancyError("A reason is required.", status_code=400)
    reason = reason.strip()
    client_key = _clean_client_key(idempotency_key)
    try:
        with transaction.atomic():
            hint = Lease.objects.filter(pk=lease_id).values("property_id").first()
            if hint is None:
                raise TenancyError(NOT_FOUND, status_code=404)
            _lock_property_leases(hint["property_id"])
            lease = _locked_lease(lease_id)
            _authorize_renewal(actor, lease, organization_id)
            event_key = None
            if client_key:
                event_key = tenancy_idempotency_key("lease_renewal_abandoned", lease.id, client_key)
                if TenancyEvent.objects.filter(idempotency_key=event_key).exists():
                    if lease.status == LeaseStatus.ACTIVE and not _live_renewals(lease).exists():
                        return lease
                    raise TenancyError(CONFLICT, status_code=409)
            if lease.status != LeaseStatus.UNDER_RENEWAL:
                raise TenancyError(STATUS_INVALID, status_code=409)
            if _live_renewals(lease).exists():
                raise TenancyError(
                    "Cancel the renewal draft before returning the lease to active.",
                    status_code=409,
                )
            snapshot = _snapshot_contract(lease)
            lease.status = LeaseStatus.ACTIVE
            lease.save(update_fields=["status", "updated_at"])
            record_tenancy_event(
                actor=actor,
                lease=lease,
                action=TenancyEventAction.LEASE_RENEWAL_ABANDONED,
                object_type="lease",
                object_id=lease.id,
                idempotency_key=event_key
                or tenancy_idempotency_key("lease_renewal_abandoned", lease.id, reason),
                metadata={
                    "reason": reason,
                    "contract_data_hash": snapshot["contract_data_hash"],
                },
            )
            lease.refresh_from_db()
            if not _contract_unchanged(lease, snapshot) or lease.status != LeaseStatus.ACTIVE:
                raise TenancyError(CONFLICT, status_code=409)
            return lease
    except TenancyError:
        raise
    except DjangoValidationError as exc:
        _raise_validation(exc)
    except OperationalError as exc:
        if is_deadlock(exc):
            raise TenancyError(CONFLICT, status_code=409) from exc
        raise


def request_lease_termination(
    *,
    actor,
    lease_id,
    termination_date: date,
    reason: str,
) -> TenancyEvent:
    """Record a termination request without changing the signed contract.

    The lease status, dates, rent, and digest stay as they are. Executing
    the termination is a separate authorized call.
    """

    require_actor(actor)
    if isinstance(termination_date, datetime) or not isinstance(termination_date, date):
        raise TenancyError("The termination date must be a date.", status_code=400)
    if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 2000:
        raise TenancyError("A termination reason is required.", status_code=400)
    reason = reason.strip()
    try:
        with transaction.atomic():
            hint = Lease.objects.filter(pk=lease_id).values("property_id").first()
            if hint is None:
                raise TenancyError(NOT_FOUND, status_code=404)
            _lock_property_leases(hint["property_id"])
            lease = _locked_lease(lease_id)
            if not _may_request_termination(actor, lease):
                raise TenancyError(PERMISSION_DENIED, status_code=403)
            allowed = {
                LeaseStatus.FULLY_EXECUTED,
                LeaseStatus.ACTIVE,
                LeaseStatus.UNDER_RENEWAL,
            }
            if lease.status not in allowed:
                raise TenancyError(STATUS_INVALID, status_code=409)
            if termination_date < lease.start_date:
                raise TenancyError(
                    "Termination cannot be before the lease start date.",
                    status_code=400,
                )
            if lease.term_end_date is not None and termination_date > lease.term_end_date:
                raise TenancyError(
                    "Termination cannot be after the contractual term end.",
                    status_code=400,
                )
            preserved_status = lease.status
            event = record_tenancy_event(
                actor=actor,
                lease=lease,
                action=TenancyEventAction.LEASE_TERMINATION_REQUESTED,
                object_type="lease",
                object_id=lease.id,
                idempotency_key=tenancy_idempotency_key(
                    "lease_termination_requested",
                    lease.id,
                    actor.id,
                    termination_date.isoformat(),
                    reason,
                ),
                metadata={
                    "termination_date": termination_date.isoformat(),
                    "status": preserved_status,
                    "reason": reason,
                },
            )
            lease.refresh_from_db()
            if lease.status != preserved_status or lease.actual_termination_date is not None:
                raise TenancyError(CONFLICT, status_code=409)
            return event
    except TenancyError:
        raise
    except DjangoValidationError as exc:
        _raise_validation(exc)
    except OperationalError as exc:
        if is_deadlock(exc):
            raise TenancyError(CONFLICT, status_code=409) from exc
        raise


def _may_request_termination(actor, lease: Lease) -> bool:
    if lease.property_owner_id == actor.id or lease.created_by_id == actor.id:
        return True
    if lease.parties.filter(user_id=actor.id).exists():
        return True
    if lease.managing_organization_id is None:
        return False
    for permission in (Permission.LEASES_VIEW, Permission.LEASES_TERMINATE):
        try:
            authorize(actor, lease.managing_organization_id, permission)
            return True
        except OrganizationError:
            continue
    return False


def _expire_property(property_id, moment: datetime, actor) -> int:
    _property_record, leases = _lock_property_leases(property_id)
    return _expire_loaded(leases, moment, actor)


def _expire_loaded(leases, moment: datetime, actor) -> int:
    expired = 0
    for lease in leases:
        deadline = lease.signature_deadline
        if lease.status != LeaseStatus.PENDING_SIGNATURE:
            continue
        if deadline is None or deadline > moment:
            continue
        lease.status = LeaseStatus.EXPIRED_UNSIGNED
        lease.save(update_fields=["status", "updated_at"])
        record_tenancy_event(
            actor=actor,
            lease=lease,
            action=TenancyEventAction.LEASE_RESERVATION_EXPIRED,
            object_type="lease",
            object_id=lease.id,
            idempotency_key=tenancy_idempotency_key(
                "lease_reservation_expired",
                lease.id,
                lease.document_version,
                deadline.isoformat(),
            ),
            metadata={
                "reference": lease.reference,
                "signature_deadline": deadline.isoformat(),
                "source": "system" if actor is None else "actor",
            },
        )
        expired += 1
    return expired


def _assert_approval_authority(actor, lease: Lease) -> None:
    if lease.managing_organization_id is None:
        authority = resolve_lease_write_authority(
            actor,
            lease.property,
            permission=Permission.LEASES_APPROVE,
            organization_id=None,
        )
        if lease.property_owner_id != authority.property_owner.id:
            raise TenancyError(PERMISSION_DENIED, status_code=403)
        return
    authority = resolve_lease_write_authority(
        actor,
        lease.property,
        permission=Permission.LEASES_APPROVE,
        organization_id=lease.managing_organization_id,
    )
    if authority.management_agreement is None:
        raise TenancyError(PERMISSION_DENIED, status_code=403)
    if lease.management_agreement_id != authority.management_agreement.id:
        raise TenancyError(PERMISSION_DENIED, status_code=403)
    if lease.property_owner_id != authority.property_owner.id:
        raise TenancyError(PERMISSION_DENIED, status_code=403)


def _require_approvable_terms(lease: Lease, parties: list[LeaseParty]) -> None:
    if lease.term_end_date is None or lease.term_end_date < lease.start_date:
        raise TenancyError("A bounded term end date is required.", status_code=400)
    if lease.rent_amount_minor <= 0:
        raise TenancyError("Rent must be a positive number of minor units.", status_code=400)
    if not _CURRENCY.fullmatch(lease.rent_currency or ""):
        raise TenancyError("Rent currency must be three letters.", status_code=400)
    if not 1 <= lease.payment_due_day <= 28:
        raise TenancyError("Payment due day must be from 1 to 28.", status_code=400)
    if not 0 <= lease.grace_period_days <= 30:
        raise TenancyError("Grace period must be from 0 to 30 days.", status_code=400)
    if lease.deposit_amount_minor < 0 or lease.service_charge_minor < 0:
        raise TenancyError("Deposit and service charge cannot be negative.", status_code=400)
    primaries = [party for party in parties if party.party_role == LeasePartyRole.PRIMARY_TENANT]
    landlords = [party for party in parties if party.party_role == LeasePartyRole.LANDLORD]
    if len(primaries) != 1 or not primaries[0].has_signature_authority:
        raise TenancyError(
            "Approval needs one primary tenant with signature authority.",
            status_code=400,
        )
    if primaries[0].user_id == lease.property_owner_id:
        raise TenancyError("The primary tenant cannot be the property owner.", status_code=400)
    if primaries[0].user.status != AccountStatus.ACTIVE:
        raise TenancyError("The primary tenant is not an active account.", status_code=400)
    if (
        len(landlords) != 1
        or landlords[0].user_id != lease.property_owner_id
        or not landlords[0].has_signature_authority
    ):
        raise TenancyError(
            "Approval needs the verified property owner as the landlord.",
            status_code=400,
        )
    if landlords[0].user.status != AccountStatus.ACTIVE:
        raise TenancyError("The landlord is not an active account.", status_code=400)
    for party in parties:
        if party.has_signature_authority and party.user.status != AccountStatus.ACTIVE:
            raise TenancyError("A signatory is not an active account.", status_code=400)
        if party.party_role == LeasePartyRole.AUTHORIZED_OCCUPANT and party.has_signature_authority:
            raise TenancyError("An authorized occupant cannot sign the lease.", status_code=400)


def _reservation_conflict(lease: Lease, start: date, exclusive_end: date) -> bool:
    others = Lease.objects.filter(
        property_id=lease.property_id,
        status__in=LeaseStatus.OCCUPANCY_RESERVING,
    ).exclude(pk=lease.pk)
    for other in others:
        if _ranges_overlap(start, exclusive_end, other.start_date, other.effective_end_date):
            return True
    return False


def _ranges_overlap(start_a: date, end_a: date | None, start_b: date, end_b: date | None) -> bool:
    """Half-open intervals. A null end is unbounded."""

    if end_a is not None and start_b >= end_a:
        return False
    if end_b is not None and start_a >= end_b:
        return False
    return True


def _lock_property_leases(property_id):
    property_record = _lock_one_property(property_id)
    leases = list(
        Lease.objects.select_for_update(of=("self",))
        .filter(property_id=property_record.id)
        .order_by("id")
    )
    return property_record, leases


def _locked_lease(lease_id) -> Lease:
    try:
        return Lease.objects.select_for_update(of=("self",)).get(pk=lease_id)
    except Lease.DoesNotExist as exc:
        raise TenancyError(NOT_FOUND, status_code=404) from exc


def _locked_party(party_id) -> LeaseParty:
    try:
        return LeaseParty.objects.select_for_update(of=("self",)).get(pk=party_id)
    except LeaseParty.DoesNotExist as exc:
        raise TenancyError(NOT_FOUND, status_code=404) from exc


def _require_execution_authority(actor, property_record, permission: str) -> None:
    """Direct owners pass. Agents also need the named platform permission.

    The permission does not create a property grant. An agent whose agreement
    lacks can_execute_leases still fails the legal check before this permission.
    """

    try:
        agreement = verify_lease_execution_authority(actor, property_record)
    except OperationsError as exc:
        raise TenancyError(exc.detail, status_code=exc.status_code) from exc
    if agreement is None:
        return
    try:
        authorize(actor, agreement.managing_organization_id, permission)
    except OrganizationError as exc:
        raise TenancyError(PERMISSION_DENIED, status_code=403) from exc


def _execute_if_complete(lease: Lease, moment: datetime, actor) -> None:
    required = list(lease.parties.filter(has_signature_authority=True).values_list("id", flat=True))
    signed = set(
        LeaseSignatureEvidence.objects.filter(
            lease=lease,
            document_version=lease.document_version,
        ).values_list("party_id", flat=True)
    )
    if not required or any(party_id not in signed for party_id in required):
        return
    lease.status = LeaseStatus.FULLY_EXECUTED
    lease.signed_at = moment
    lease.save(update_fields=["status", "signed_at", "updated_at"])
    record_tenancy_event(
        actor=actor,
        lease=lease,
        action=TenancyEventAction.LEASE_FULLY_EXECUTED,
        object_type="lease",
        object_id=lease.id,
        idempotency_key=tenancy_idempotency_key(
            "lease_fully_executed", lease.id, lease.document_version
        ),
        metadata={
            "reference": lease.reference,
            "document_version": lease.document_version,
            "contract_data_hash": lease.contract_data_hash,
        },
    )


def _release_occupancy(actor, property_id) -> None:
    profile = UnitPhysicalProfile.objects.filter(property_id=property_id).first()
    if profile is None:
        return
    if not physical_state_transition_allowed(
        profile.physical_state, PhysicalState.TURNOVER_PREPARATION
    ):
        return
    update_physical_profile(
        actor=actor,
        property_id=property_id,
        new_state=PhysicalState.TURNOVER_PREPARATION,
    )


def _signature_key(lease: Lease, party: LeaseParty, client_key: str | None) -> str:
    parts: list[object] = [
        "lease_party_signed",
        lease.id,
        party.id,
        lease.document_version,
    ]
    if client_key:
        parts.append(client_key)
    return tenancy_idempotency_key(*parts)


def _signature_method(value: str) -> str:
    if value not in SignatureMethod.VALUES:
        raise TenancyError("The signature method is invalid.", status_code=400)
    return value


def _ip_address(value: str | None) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise TenancyError("The signature IP address is invalid.", status_code=400)
    try:
        validate_ipv46_address(value)
    except DjangoValidationError as exc:
        raise TenancyError("The signature IP address is invalid.", status_code=400) from exc
    return value


def _user_agent(value: str) -> str:
    if not isinstance(value, str):
        raise TenancyError("The signature user agent is invalid.", status_code=400)
    return value[:512]


def _lock_one_property(property_id) -> Property:
    try:
        locked = lock_properties(property_id)
    except Property.DoesNotExist as exc:
        raise TenancyError(NOT_FOUND, status_code=404) from exc
    record = locked.get(property_id)
    if record is None and locked:
        record = next(iter(locked.values()))
    if record is None:
        raise TenancyError(NOT_FOUND, status_code=404)
    return record


def _replay_draft(actor, client_key: str, property_id):
    key = tenancy_idempotency_key("lease_drafted", actor.id, client_key)
    existing = TenancyEvent.objects.filter(idempotency_key=key).select_related("lease").first()
    if existing is None or existing.lease_id is None:
        return None
    if existing.lease.property_id != property_id:
        raise TenancyError(CONFLICT, status_code=409)
    return existing.lease


def _replay_draft_outside(actor, client_key: str, property_id):
    try:
        property_uuid = Property.objects.filter(pk=property_id).values_list("id", flat=True).first()
    except ValueError, TypeError:
        return None
    if property_uuid is None:
        return None
    try:
        return _replay_draft(actor, client_key, property_uuid)
    except TenancyError as exc:
        if exc.status_code == 409:
            raise
        return None


def _validate_unit(property_record: Property) -> None:
    try:
        validate_rentable_unit(property_record)
    except DjangoValidationError as exc:
        _raise_validation(exc)


def _parse_terms(payload: dict[str, Any]) -> dict[str, Any]:
    lease_type = payload.get("lease_type", LeaseType.RESIDENTIAL_FIXED)
    if lease_type not in LeaseType.VALUES:
        raise TenancyError("The lease type is invalid.", status_code=400)
    start = _as_date(payload.get("start_date"), "The start date")
    term_end = _as_date(payload.get("term_end_date"), "The term end date")
    if term_end < start:
        raise TenancyError("The term end date cannot be before the start date.", status_code=400)
    currency = str(payload.get("rent_currency") or "KES").strip().upper()
    if not _CURRENCY.fullmatch(currency):
        raise TenancyError("Rent currency must be three letters.", status_code=400)
    covenants = payload.get("special_covenants", "")
    if not isinstance(covenants, str) or len(covenants) > MAX_COVENANT_CHARS:
        raise TenancyError("Special covenants are invalid.", status_code=400)
    return {
        "lease_type": lease_type,
        "start_date": start,
        "term_end_date": term_end,
        "rent_amount_minor": _positive_int(payload.get("rent_amount_minor"), "Rent"),
        "rent_currency": currency,
        "deposit_amount_minor": _non_negative(payload.get("deposit_amount_minor", 0), "Deposit"),
        "service_charge_minor": _non_negative(
            payload.get("service_charge_minor", 0), "Service charge"
        ),
        "payment_due_day": _bounded_int(
            payload.get("payment_due_day", 1), "Payment due day", 1, 28
        ),
        "grace_period_days": _bounded_int(
            payload.get("grace_period_days", 5), "Grace period", 0, 30
        ),
        "special_covenants": covenants,
    }


def _parse_parties(raw, property_owner) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw:
        raise TenancyError("A lease draft needs contracting parties.", status_code=400)
    specs: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise TenancyError("Each contracting party must be an object.", status_code=400)
        role = item.get("party_role")
        if role not in LeasePartyRole.VALUES:
            raise TenancyError("A contracting party role is invalid.", status_code=400)
        user_id = _as_uuid(item.get("user_id"), "A contracting party is invalid.")
        default_authority = role != LeasePartyRole.AUTHORIZED_OCCUPANT
        authority = item.get("has_signature_authority", default_authority)
        if not isinstance(authority, bool):
            raise TenancyError("Signature authority must be true or false.", status_code=400)
        if role == LeasePartyRole.AUTHORIZED_OCCUPANT and authority:
            raise TenancyError("An authorized occupant cannot sign the lease.", status_code=400)
        specs.append(
            {
                "user_id": user_id,
                "party_role": role,
                "has_signature_authority": authority,
            }
        )
    landlords = [spec for spec in specs if spec["party_role"] == LeasePartyRole.LANDLORD]
    if not landlords:
        specs.append(
            {
                "user_id": property_owner.id,
                "party_role": LeasePartyRole.LANDLORD,
                "has_signature_authority": True,
            }
        )
    elif (
        len(landlords) != 1
        or landlords[0]["user_id"] != property_owner.id
        or not landlords[0]["has_signature_authority"]
    ):
        raise TenancyError(
            "The landlord party must be the verified property owner.",
            status_code=400,
        )
    primaries = [spec for spec in specs if spec["party_role"] == LeasePartyRole.PRIMARY_TENANT]
    if (
        len(primaries) != 1
        or not primaries[0]["has_signature_authority"]
        or primaries[0]["user_id"] == property_owner.id
    ):
        raise TenancyError(
            "A lease draft needs one primary tenant who is not the property owner.",
            status_code=400,
        )
    seen: set[tuple] = set()
    for spec in specs:
        key = (spec["user_id"], spec["party_role"])
        if key in seen:
            raise TenancyError("A party role is duplicated.", status_code=400)
        seen.add(key)
    wanted = {spec["user_id"] for spec in specs}
    users = {user.id: user for user in User.objects.filter(id__in=wanted)}
    if len(users) != len(wanted):
        raise TenancyError("A contracting party does not exist.", status_code=400)
    for spec in specs:
        user = users[spec["user_id"]]
        if user.status != AccountStatus.ACTIVE:
            raise TenancyError("A contracting party is not an active account.", status_code=400)
        spec["user"] = user
    return specs


def _load_application(application_id, property_record: Property, parties: list[dict[str, Any]]):
    if application_id in (None, ""):
        return None
    try:
        application = RentalApplication.objects.get(pk=application_id)
    except RentalApplication.DoesNotExist as exc:
        raise TenancyError("The rental application was not found.", status_code=404) from exc
    except (ValueError, TypeError) as exc:
        raise TenancyError("The rental application is invalid.", status_code=400) from exc
    primary = next(
        party for party in parties if party["party_role"] == LeasePartyRole.PRIMARY_TENANT
    )
    same_unit = application.property_id == property_record.id
    same_tenant = application.applicant_id == primary["user_id"]
    if not same_unit or not same_tenant:
        raise TenancyError("The rental application does not match this draft.", status_code=400)
    return application


def _clean_client_key(value) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TenancyError("The idempotency key is invalid.", status_code=400)
    cleaned = value.strip()
    if not cleaned:
        return None
    if len(cleaned) > 200:
        raise TenancyError("The idempotency key is invalid.", status_code=400)
    return cleaned


def _as_date(value, label: str) -> date:
    if isinstance(value, datetime) or not isinstance(value, date):
        raise TenancyError(f"{label} must be a date.", status_code=400)
    return value


def _as_uuid(value, message: str):
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise TenancyError(message, status_code=400) from exc


def _positive_int(value, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise TenancyError(f"{label} must be a positive number of minor units.", status_code=400)
    return value


def _non_negative(value, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TenancyError(f"{label} cannot be negative.", status_code=400)
    return value


def _bounded_int(value, label: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise TenancyError(f"{label} is outside the allowed range.", status_code=400)
    return value


def _deadline_hours(value) -> int:
    hours_ok = isinstance(value, int) and not isinstance(value, bool)
    if not hours_ok or not 1 <= value <= MAX_DEADLINE_HOURS:
        raise TenancyError("The signature deadline must be from 1 to 336 hours.", status_code=400)
    return value


def _rendered_bytes(document) -> bytes:
    if isinstance(document, bytearray):
        document = bytes(document)
    if not isinstance(document, bytes) or not document:
        raise TenancyError("The rendered lease document is required.", status_code=400)
    if len(document) > MAX_RENDERED_BYTES:
        raise TenancyError("The rendered lease document is too large.", status_code=400)
    return document


def _rendered_uri(uri: str) -> str:
    if not isinstance(uri, str) or len(uri) > 255 or any(ord(char) < 32 for char in uri):
        raise TenancyError("The rendered document URI is invalid.", status_code=400)
    return uri


def _aware_moment(moment: datetime | None) -> datetime:
    if moment is None:
        return timezone.now()
    if not isinstance(moment, datetime) or timezone.is_naive(moment):
        raise TenancyError("The clock time must be timezone-aware.", status_code=400)
    return moment


def _raise_validation(exc: DjangoValidationError) -> None:
    if hasattr(exc, "message_dict"):
        parts = []
        for key, errors in exc.message_dict.items():
            for error in errors:
                parts.append(f"{key}: {error}")
        detail = " ".join(parts)
    else:
        detail = " ".join(exc.messages)
    status = 409 if STATUS_INVALID in detail or "immutable" in detail else 400
    raise TenancyError(detail or STATUS_INVALID, status_code=status) from exc
