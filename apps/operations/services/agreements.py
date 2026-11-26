"""Bilateral management agreement domain services and authority delegation."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from django.contrib.auth import get_user_model
from django.db import IntegrityError, OperationalError, transaction
from django.utils import timezone

from apps.organizations.domain import (
    MembershipStatus,
    OrganizationStatus,
    Permission,
    permissions_for,
)
from apps.organizations.models import Organization, OrganizationMembership
from apps.organizations.services.authorization import ensure_active_account
from apps.operations.domain import (
    ACTIVE_AGREEMENT_EXISTS,
    AGREEMENT_TRANSITIONS,
    ALREADY_RESOLVED,
    COMMISSION_INVALID,
    CONFLICT,
    COUNTERPARTY_MISMATCH,
    DATE_RANGE_INVALID,
    EFFECTIVE_DATE_INVALID,
    INITIATOR_MISMATCH,
    NOT_FOUND,
    ORGANIZATION_NOT_ACTIVE,
    PERMISSION_DENIED,
    PROPERTY_ARCHIVED,
    SPEND_LIMIT_INVALID,
    STATUS_INVALID,
    UNAUTHORIZED_AGENT,
    UNAUTHORIZED_OWNER,
    AgreementStatus,
    OperationsEventAction,
    agreement_transition_allowed,
)
from apps.operations.errors import OperationsError
from apps.operations.models import DelegatedPropertyAccess, ManagementAgreement
from apps.operations.services.audit import record_operations_event
from apps.properties.domain import (
    AccessCapability,
    AccessOrigin,
    AccessStatus,
    PropertyStatus,
)
from apps.properties.models import Property, PropertyAccess
from apps.properties.services.locking import is_deadlock, lock_properties


User = get_user_model()


def verify_owner_authority(user, property_record: Property) -> PropertyAccess:
    """Verify that a user possesses verified legal management authority over a property.

    Crucial: Unverified claims in PropertyParty NEVER satisfy this check.
    Inherited delegated grants from prior agreements NEVER satisfy this check.
    Organization membership is not property title and never mints a DIRECT grant.
    The user must already possess an explicit active direct PropertyAccess with MANAGE capability.
    """
    ensure_active_account(user)

    # Check direct PropertyAccess grant strictly (inherited grants cannot authorize further delegation)
    direct_grant = (
        PropertyAccess.objects.filter(
            property=property_record,
            user=user,
            capability=AccessCapability.MANAGE,
            status=AccessStatus.ACTIVE,
            origin=AccessOrigin.DIRECT,
        )
        .order_by("-created_at")
        .first()
    )
    if direct_grant is not None:
        return direct_grant

    raise OperationsError(UNAUTHORIZED_OWNER, status_code=403)


def is_agreement_expired(agreement: ManagementAgreement, target_date: date | None = None) -> bool:
    """Determine whether an active agreement has passed its valid_until date.

    Boundary rule: The agreement remains valid ON valid_until (inclusive).
    It is expired if and only if target_date > valid_until.
    """
    if agreement.valid_until is None:
        return False
    check_date = target_date or timezone.localdate()
    return check_date > agreement.valid_until


def sync_agreement_expiry(agreement: ManagementAgreement, target_date: date | None = None) -> bool:
    """Idempotently sync expiry state on an active agreement and revoke delegated grants.

    Returns True if the agreement was expired and revoked, False otherwise.
    """
    if agreement.status != AgreementStatus.ACTIVE:
        return False

    if not is_agreement_expired(agreement, target_date):
        return False

    now = timezone.now()
    agreement.status = AgreementStatus.EXPIRED
    agreement.save(update_fields=["status", "updated_at"])

    # Revoke delegated access grants
    delegated_grants = DelegatedPropertyAccess.objects.filter(
        agreement=agreement,
        status=AccessStatus.ACTIVE,
    ).select_related("property_access")

    for delegation in delegated_grants:
        delegation.status = AccessStatus.REVOKED
        delegation.revoked_at = now
        delegation.save(update_fields=["status", "revoked_at"])

        grant = delegation.property_access
        if grant.status == AccessStatus.ACTIVE:
            grant.status = AccessStatus.REVOKED
            grant.revoked_at = now
            grant.save(update_fields=["status", "revoked_at"])

    record_operations_event(
        actor=None,
        property_record=agreement.property,
        organization=agreement.managing_organization,
        action=OperationsEventAction.AGREEMENT_TERMINATED,
        object_type="management_agreement",
        object_id=agreement.id,
        metadata={
            "reference": agreement.reference,
            "reason": "Automatic expiration past valid_until",
            "valid_until": str(agreement.valid_until),
        },
    )
    return True


def is_grant_expired(grant: PropertyAccess, target_date: date | None = None) -> bool:
    """Check if an inherited PropertyAccess grant belongs to an expired or non-active agreement."""
    if grant.origin != AccessOrigin.INHERITED:
        return False

    delegation = (
        grant.operational_delegations.filter(status=AccessStatus.ACTIVE)
        .select_related("agreement")
        .first()
    )
    if delegation is None:
        # No active delegation found for this inherited grant
        return True

    agreement = delegation.agreement
    if agreement.status != AgreementStatus.ACTIVE:
        return True

    return is_agreement_expired(agreement, target_date)


def verify_agent_authority(user, organization: Organization) -> OrganizationMembership:
    """Verify that a user has authorized operational management rights within a PM firm."""
    ensure_active_account(user)
    if organization.status != OrganizationStatus.ACTIVE:
        raise OperationsError(ORGANIZATION_NOT_ACTIVE, status_code=403)

    membership = OrganizationMembership.objects.filter(
        organization=organization,
        user=user,
        status=MembershipStatus.ACTIVE,
    ).first()
    if membership is None:
        raise OperationsError(UNAUTHORIZED_AGENT, status_code=403)

    granted = permissions_for(membership.role)
    if Permission.OPERATIONS_MANAGE not in granted:
        raise OperationsError(UNAUTHORIZED_AGENT, status_code=403)

    return membership


def propose_agreement(
    *,
    actor,
    property_id: uuid.UUID | str,
    managing_org_id: uuid.UUID | str,
    spend_limit_minor: int = 1_000_000,
    commission_basis_points: int = 800,
    valid_from: date | None = None,
    valid_until: date | None = None,
    can_execute_leases: bool = False,
    granting_user_id: uuid.UUID | str | None = None,
    assigned_agent_id: uuid.UUID | str | None = None,
    as_draft: bool = False,
    request=None,
) -> ManagementAgreement:
    """Create or propose a bilateral ManagementAgreement.

    Can be proposed either by the verified owner or by the managing PM firm.
    """
    ensure_active_account(actor)

    if valid_from is None:
        valid_from = timezone.now().date()
    if valid_until is not None and valid_until < valid_from:
        raise OperationsError(DATE_RANGE_INVALID, status_code=400)
    if spend_limit_minor < 0:
        raise OperationsError(SPEND_LIMIT_INVALID, status_code=400)
    if not (0 <= commission_basis_points <= 10000):
        raise OperationsError(COMMISSION_INVALID, status_code=400)

    try:
        with transaction.atomic():
            locked = lock_properties(property_id)
            property_record = locked.get(property_id)
            if property_record is None:
                try:
                    property_record = Property.objects.select_for_update().get(pk=property_id)
                except Property.DoesNotExist as exc:
                    raise OperationsError(NOT_FOUND, status_code=404) from exc

            if property_record.status == PropertyStatus.ARCHIVED:
                raise OperationsError(PROPERTY_ARCHIVED, status_code=409)

            try:
                managing_org = Organization.objects.get(pk=managing_org_id)
            except Organization.DoesNotExist as exc:
                raise OperationsError("Managing organization not found.", status_code=404) from exc

            # Ensure no conflicting active agreement exists
            if ManagementAgreement.objects.filter(
                property=property_record,
                status=AgreementStatus.ACTIVE,
            ).exists():
                raise OperationsError(ACTIVE_AGREEMENT_EXISTS, status_code=409)

            # Determine proposal direction (Owner-initiated vs Agent-initiated)
            actor_is_owner = False
            try:
                owner_grant = verify_owner_authority(actor, property_record)
                actor_is_owner = True
            except OperationsError:
                owner_grant = None

            actor_is_agent = False
            try:
                verify_agent_authority(actor, managing_org)
                actor_is_agent = True
            except OperationsError:
                pass

            if not actor_is_owner and not actor_is_agent:
                raise OperationsError(PERMISSION_DENIED, status_code=403)

            now = timezone.now()
            if as_draft:
                initial_status = AgreementStatus.DRAFT
                accepted_owner_at = now if actor_is_owner else None
                accepted_agent_at = now if actor_is_agent else None
            elif actor_is_owner:
                # Owner initiates -> waits for agent acceptance
                initial_status = AgreementStatus.PENDING_AGENT_ACCEPTANCE
                accepted_owner_at = now
                accepted_agent_at = None
            else:
                # Agent initiates -> waits for verified owner acceptance
                initial_status = AgreementStatus.PENDING_OWNER_ACCEPTANCE
                accepted_owner_at = None
                accepted_agent_at = now

            # Resolve granting_user (must be verified owner)
            if actor_is_owner:
                granting_user = actor
            else:
                if granting_user_id is None:
                    # Find verified owner holding direct PropertyAccess
                    active_owner_grant = (
                        PropertyAccess.objects.filter(
                            property=property_record,
                            capability=AccessCapability.MANAGE,
                            status=AccessStatus.ACTIVE,
                            origin=AccessOrigin.DIRECT,
                        )
                        .order_by("-created_at")
                        .first()
                    )
                    if active_owner_grant is None:
                        raise OperationsError(UNAUTHORIZED_OWNER, status_code=400)
                    granting_user = active_owner_grant.user
                else:
                    try:
                        granting_user = User.objects.get(pk=granting_user_id)
                    except User.DoesNotExist as exc:
                        raise OperationsError("Granting user not found.", status_code=404) from exc
                # Validate owner authority
                verify_owner_authority(granting_user, property_record)

            # Resolve assigned agent
            assigned_agent = None
            if assigned_agent_id is not None:
                try:
                    assigned_agent = User.objects.get(pk=assigned_agent_id)
                    verify_agent_authority(assigned_agent, managing_org)
                except User.DoesNotExist as exc:
                    raise OperationsError("Assigned agent not found.", status_code=404) from exc
            elif actor_is_agent:
                assigned_agent = actor

            agreement = ManagementAgreement.objects.create(
                property=property_record,
                managing_organization=managing_org,
                granting_user=granting_user,
                assigned_agent=assigned_agent,
                spend_limit_minor=spend_limit_minor,
                commission_basis_points=commission_basis_points,
                valid_from=valid_from,
                valid_until=valid_until,
                can_execute_leases=can_execute_leases,
                status=initial_status,
                accepted_by_owner_at=accepted_owner_at,
                accepted_by_agent_at=accepted_agent_at,
                created_by=actor,
            )

            record_operations_event(
                actor=actor,
                property_record=property_record,
                organization=managing_org,
                action=(
                    OperationsEventAction.AGREEMENT_DRAFTED
                    if as_draft
                    else OperationsEventAction.AGREEMENT_PROPOSED
                ),
                object_type="management_agreement",
                object_id=agreement.id,
                request=request,
                metadata={
                    "reference": agreement.reference,
                    "status": agreement.status,
                    "initiated_by": "owner" if actor_is_owner else "agent",
                    "spend_limit_minor": spend_limit_minor,
                    "commission_basis_points": commission_basis_points,
                    "can_execute_leases": can_execute_leases,
                },
            )

            return agreement
    except OperationalError as exc:
        if is_deadlock(exc):
            raise OperationsError(CONFLICT, status_code=409) from exc
        raise


def accept_agreement(
    *,
    actor,
    agreement_id: uuid.UUID | str,
    request=None,
) -> ManagementAgreement:
    """Counterparty accepts a proposed ManagementAgreement."""
    ensure_active_account(actor)

    try:
        with transaction.atomic():
            agreement = (
                ManagementAgreement.objects.select_for_update()
                .select_related("property", "managing_organization", "granting_user")
                .get(pk=agreement_id)
            )

            if agreement.status in {
                AgreementStatus.ACTIVE,
                AgreementStatus.REJECTED,
                AgreementStatus.REVOKED,
                AgreementStatus.TERMINATED,
                AgreementStatus.EXPIRED,
            }:
                raise OperationsError(ALREADY_RESOLVED, status_code=409)

            now = timezone.now()

            if agreement.status == AgreementStatus.PENDING_OWNER_ACCEPTANCE:
                # Must be verified owner
                if actor.id != agreement.granting_user_id:
                    # Also permit if actor holds direct manage access
                    verify_owner_authority(actor, agreement.property)
                    agreement.granting_user = actor
                else:
                    verify_owner_authority(actor, agreement.property)

                agreement.accepted_by_owner_at = now
                agreement.status = AgreementStatus.ACCEPTED

            elif agreement.status == AgreementStatus.PENDING_AGENT_ACCEPTANCE:
                # Must be authorized agent in managing organization
                verify_agent_authority(actor, agreement.managing_organization)
                agreement.accepted_by_agent_at = now
                if agreement.assigned_agent is None:
                    agreement.assigned_agent = actor
                agreement.status = AgreementStatus.ACCEPTED

            else:
                raise OperationsError(STATUS_INVALID, status_code=400)

            agreement.save(
                update_fields=[
                    "status",
                    "granting_user",
                    "assigned_agent",
                    "accepted_by_owner_at",
                    "accepted_by_agent_at",
                    "updated_at",
                ]
            )

            record_operations_event(
                actor=actor,
                property_record=agreement.property,
                organization=agreement.managing_organization,
                action=OperationsEventAction.AGREEMENT_ACCEPTED,
                object_type="management_agreement",
                object_id=agreement.id,
                request=request,
                metadata={
                    "reference": agreement.reference,
                    "status": agreement.status,
                },
            )

            return agreement
    except ManagementAgreement.DoesNotExist as exc:
        raise OperationsError(NOT_FOUND, status_code=404) from exc
    except OperationalError as exc:
        if is_deadlock(exc):
            raise OperationsError(CONFLICT, status_code=409) from exc
        raise


def activate_agreement(
    *,
    actor,
    agreement_id: uuid.UUID | str,
    request=None,
) -> ManagementAgreement:
    """Activate an accepted agreement and provision delegated PropertyAccess."""
    ensure_active_account(actor)

    try:
        with transaction.atomic():
            agreement = (
                ManagementAgreement.objects.select_for_update(of=("self",))
                .select_related("property", "managing_organization", "granting_user")
                .get(pk=agreement_id)
            )

            # Check authorization to activate (owner or managing org agent)
            is_owner = actor.id == agreement.granting_user_id
            is_agent = False
            try:
                verify_agent_authority(actor, agreement.managing_organization)
                is_agent = True
            except OperationsError:
                pass

            if not is_owner and not is_agent:
                raise OperationsError(PERMISSION_DENIED, status_code=403)

            if agreement.status != AgreementStatus.ACCEPTED:
                if agreement.status == AgreementStatus.ACTIVE:
                    return agreement
                raise OperationsError(
                    f"Cannot activate agreement in state '{agreement.status}'. Must be accepted first.",
                    status_code=400,
                )

            # Validate effective dates
            today = timezone.now().date()
            if agreement.valid_from > today:
                raise OperationsError(
                    f"Agreement effective start date {agreement.valid_from} is in the future.",
                    status_code=400,
                )
            if agreement.valid_until is not None and agreement.valid_until < today:
                raise OperationsError(EFFECTIVE_DATE_INVALID, status_code=400)

            # Re-verify owner authority
            owner_grant = verify_owner_authority(agreement.granting_user, agreement.property)

            # Determine agent user to receive operational delegation
            agent_user = agreement.assigned_agent
            if agent_user is None:
                if is_agent:
                    agent_user = actor
                    agreement.assigned_agent = actor
                else:
                    raise OperationsError("An assigned agent user is required for activation.", status_code=400)

            # Check if active agreement already exists on this property
            if (
                ManagementAgreement.objects.filter(
                    property=agreement.property,
                    status=AgreementStatus.ACTIVE,
                )
                .exclude(pk=agreement.pk)
                .exists()
            ):
                raise OperationsError(ACTIVE_AGREEMENT_EXISTS, status_code=409)

            now = timezone.now()
            agreement.status = AgreementStatus.ACTIVE
            agreement.activated_at = now
            agreement.save(update_fields=["status", "assigned_agent", "activated_at", "updated_at"])

            # Provision delegated PropertyAccess without mutating Property.management_context!
            existing_grant = PropertyAccess.objects.filter(
                property=agreement.property,
                user=agent_user,
                status=AccessStatus.ACTIVE,
            ).first()

            if existing_grant is None:
                # Create delegated grant inherited from verified owner grant
                property_grant = PropertyAccess.objects.create(
                    property=agreement.property,
                    user=agent_user,
                    capability=AccessCapability.MANAGE,
                    status=AccessStatus.ACTIVE,
                    origin=AccessOrigin.INHERITED,
                    inherited_from=owner_grant,
                    granted_by=agreement.granting_user,
                )
            else:
                property_grant = existing_grant
                if existing_grant.capability != AccessCapability.MANAGE:
                    existing_grant.capability = AccessCapability.MANAGE
                    existing_grant.save(update_fields=["capability"])

            expires_at = (
                timezone.datetime.combine(
                    agreement.valid_until,
                    timezone.datetime.max.time(),
                    tzinfo=timezone.get_current_timezone(),
                )
                if agreement.valid_until
                else None
            )

            # Link operational delegation
            delegated_access, _ = DelegatedPropertyAccess.objects.update_or_create(
                agreement=agreement,
                user=agent_user,
                defaults={
                    "property_access": property_grant,
                    "organization": agreement.managing_organization,
                    "status": AccessStatus.ACTIVE,
                    "expires_at": expires_at,
                    "revoked_at": None,
                },
            )

            record_operations_event(
                actor=actor,
                property_record=agreement.property,
                organization=agreement.managing_organization,
                action=OperationsEventAction.AGREEMENT_ACTIVATED,
                object_type="management_agreement",
                object_id=agreement.id,
                request=request,
                metadata={
                    "reference": agreement.reference,
                    "delegated_user_id": str(agent_user.id),
                    "access_grant_id": str(property_grant.id),
                },
            )

            return agreement
    except ManagementAgreement.DoesNotExist as exc:
        raise OperationsError(NOT_FOUND, status_code=404) from exc
    except OperationalError as exc:
        if is_deadlock(exc):
            raise OperationsError(CONFLICT, status_code=409) from exc
        raise


def reject_agreement(
    *,
    actor,
    agreement_id: uuid.UUID | str,
    reason: str = "",
    request=None,
) -> ManagementAgreement:
    """Counterparty rejects a proposed ManagementAgreement."""
    ensure_active_account(actor)

    try:
        with transaction.atomic():
            agreement = (
                ManagementAgreement.objects.select_for_update()
                .select_related("property", "managing_organization")
                .get(pk=agreement_id)
            )

            if agreement.status not in AgreementStatus.PENDING:
                raise OperationsError(
                    f"Cannot reject agreement in state '{agreement.status}'.",
                    status_code=400,
                )

            # Verify caller is counterparty
            if agreement.status == AgreementStatus.PENDING_OWNER_ACCEPTANCE:
                if actor.id != agreement.granting_user_id:
                    verify_owner_authority(actor, agreement.property)
            elif agreement.status == AgreementStatus.PENDING_AGENT_ACCEPTANCE:
                verify_agent_authority(actor, agreement.managing_organization)

            agreement.status = AgreementStatus.REJECTED
            agreement.save(update_fields=["status", "updated_at"])

            record_operations_event(
                actor=actor,
                property_record=agreement.property,
                organization=agreement.managing_organization,
                action=OperationsEventAction.AGREEMENT_REJECTED,
                object_type="management_agreement",
                object_id=agreement.id,
                request=request,
                metadata={
                    "reference": agreement.reference,
                    "reason": reason,
                },
            )

            return agreement
    except ManagementAgreement.DoesNotExist as exc:
        raise OperationsError(NOT_FOUND, status_code=404) from exc
    except OperationalError as exc:
        if is_deadlock(exc):
            raise OperationsError(CONFLICT, status_code=409) from exc
        raise


def revoke_agreement(
    *,
    actor,
    agreement_id: uuid.UUID | str,
    request=None,
) -> ManagementAgreement:
    """Proposing party withdraws/revokes an un-activated agreement."""
    ensure_active_account(actor)

    try:
        with transaction.atomic():
            agreement = (
                ManagementAgreement.objects.select_for_update()
                .select_related("property", "managing_organization")
                .get(pk=agreement_id)
            )

            if agreement.status in {
                AgreementStatus.ACTIVE,
                AgreementStatus.TERMINATED,
                AgreementStatus.EXPIRED,
                AgreementStatus.REJECTED,
                AgreementStatus.REVOKED,
            }:
                raise OperationsError(ALREADY_RESOLVED, status_code=409)

            # Check initiator
            if actor.id != agreement.created_by_id:
                raise OperationsError(INITIATOR_MISMATCH, status_code=403)

            agreement.status = AgreementStatus.REVOKED
            agreement.save(update_fields=["status", "updated_at"])

            record_operations_event(
                actor=actor,
                property_record=agreement.property,
                organization=agreement.managing_organization,
                action=OperationsEventAction.AGREEMENT_REVOKED,
                object_type="management_agreement",
                object_id=agreement.id,
                request=request,
                metadata={"reference": agreement.reference},
            )

            return agreement
    except ManagementAgreement.DoesNotExist as exc:
        raise OperationsError(NOT_FOUND, status_code=404) from exc
    except OperationalError as exc:
        if is_deadlock(exc):
            raise OperationsError(CONFLICT, status_code=409) from exc
        raise


def terminate_agreement(
    *,
    actor,
    agreement_id: uuid.UUID | str,
    reason: str = "",
    request=None,
) -> ManagementAgreement:
    """Terminate an active ManagementAgreement and revoke all delegated operational access."""
    ensure_active_account(actor)

    try:
        with transaction.atomic():
            agreement = (
                ManagementAgreement.objects.select_for_update()
                .select_related("property", "managing_organization")
                .get(pk=agreement_id)
            )

            if agreement.status != AgreementStatus.ACTIVE:
                raise OperationsError(
                    f"Only active agreements can be terminated. Current state: '{agreement.status}'.",
                    status_code=400,
                )

            # Verify caller is verified owner or authorized managing agent
            is_owner = False
            try:
                verify_owner_authority(actor, agreement.property)
                is_owner = True
            except OperationsError:
                pass

            is_agent = False
            try:
                verify_agent_authority(actor, agreement.managing_organization)
                is_agent = True
            except OperationsError:
                pass

            if not is_owner and not is_agent:
                raise OperationsError(PERMISSION_DENIED, status_code=403)

            now = timezone.now()
            agreement.status = AgreementStatus.TERMINATED
            agreement.terminated_at = now
            agreement.termination_reason = reason
            agreement.save(
                update_fields=["status", "terminated_at", "termination_reason", "updated_at"]
            )

            # Revoke delegated property access
            delegated_grants = DelegatedPropertyAccess.objects.filter(
                agreement=agreement,
                status=AccessStatus.ACTIVE,
            ).select_related("property_access")

            for delegation in delegated_grants:
                delegation.status = AccessStatus.REVOKED
                delegation.revoked_at = now
                delegation.save(update_fields=["status", "revoked_at"])

                # Revoke underlying PropertyAccess if inherited from agreement
                grant = delegation.property_access
                if grant.status == AccessStatus.ACTIVE:
                    grant.status = AccessStatus.REVOKED
                    grant.revoked_at = now
                    grant.save(update_fields=["status", "revoked_at"])

            record_operations_event(
                actor=actor,
                property_record=agreement.property,
                organization=agreement.managing_organization,
                action=OperationsEventAction.AGREEMENT_TERMINATED,
                object_type="management_agreement",
                object_id=agreement.id,
                request=request,
                metadata={
                    "reference": agreement.reference,
                    "reason": reason,
                    "revoked_grants_count": delegated_grants.count(),
                },
            )

            return agreement
    except ManagementAgreement.DoesNotExist as exc:
        raise OperationsError(NOT_FOUND, status_code=404) from exc
    except OperationalError as exc:
        if is_deadlock(exc):
            raise OperationsError(CONFLICT, status_code=409) from exc
        raise


def revoke_lease_execution_authority(
    *,
    actor,
    agreement_id: uuid.UUID | str,
    reason: str = "",
    request=None,
) -> ManagementAgreement:
    """Explicitly revoke lease-execution authority from an active ManagementAgreement.

    Can only be executed by the verified property owner. Leaves general operational
    management access intact while revoking the power to execute leases.
    """
    ensure_active_account(actor)

    try:
        with transaction.atomic():
            agreement = (
                ManagementAgreement.objects.select_for_update()
                .select_related("property", "managing_organization")
                .get(pk=agreement_id)
            )

            if agreement.status != AgreementStatus.ACTIVE:
                raise OperationsError(
                    f"Only active agreements can have authority revoked. Current state: '{agreement.status}'.",
                    status_code=400,
                )

            # Strictly require verified owner authority
            verify_owner_authority(actor, agreement.property)

            if not agreement.can_execute_leases:
                raise OperationsError(
                    "Lease execution authority was not granted in this agreement's accepted terms.",
                    status_code=400,
                )

            if agreement.lease_execution_revoked_at is not None:
                raise OperationsError(
                    "Lease execution authority on this agreement has already been revoked.",
                    status_code=400,
                )

            now = timezone.now()
            # Do NOT mutate can_execute_leases! Keep original contractual grant intact.
            agreement.lease_execution_revoked_at = now
            agreement.lease_execution_revoked_by = actor
            agreement.lease_execution_revocation_reason = reason
            agreement.save(
                update_fields=[
                    "lease_execution_revoked_at",
                    "lease_execution_revoked_by",
                    "lease_execution_revocation_reason",
                    "updated_at",
                ]
            )

            record_operations_event(
                actor=actor,
                property_record=agreement.property,
                organization=agreement.managing_organization,
                action=OperationsEventAction.DELEGATED_ACCESS_REVOKED,
                object_type="management_agreement",
                object_id=agreement.id,
                request=request,
                metadata={
                    "reference": agreement.reference,
                    "revoked_authority": "can_execute_leases",
                    "revoked_at": now.isoformat(),
                    "revoked_by_id": str(actor.id),
                    "reason": reason,
                    "original_grant_preserved": True,
                },
            )

            return agreement
    except ManagementAgreement.DoesNotExist as exc:
        raise OperationsError(NOT_FOUND, status_code=404) from exc
    except OperationalError as exc:
        if is_deadlock(exc):
            raise OperationsError(CONFLICT, status_code=409) from exc
        raise


def verify_lease_execution_authority(
    user,
    property_record: Property,
) -> ManagementAgreement | None:
    """Verify that a user possesses legitimate legal authority to execute leases for a property.

    Returns None if user is the direct verified owner (inherent authority).
    Returns the active ManagementAgreement if user is a designated managing agent with effective authority.
    Raises OperationsError if user lacks authority.
    """
    # 1. Direct verified owner check
    try:
        verify_owner_authority(user, property_record)
        return None
    except OperationsError:
        pass

    # 2. Managing agent check via active ManagementAgreement
    active_agreements = ManagementAgreement.objects.filter(
        property=property_record,
        status=AgreementStatus.ACTIVE,
    ).select_related("managing_organization")

    matching_agreement = None
    for agr in active_agreements:
        if is_agreement_expired(agr):
            continue
        try:
            verify_agent_authority(user, agr.managing_organization)
            matching_agreement = agr
            break
        except OperationsError:
            continue

    if matching_agreement is None:
        raise OperationsError(
            "User does not possess management access for this property.",
            status_code=403,
        )

    if not matching_agreement.has_effective_lease_execution_authority:
        if matching_agreement.lease_execution_revoked_at is not None:
            raise OperationsError(
                "Managing agent lease execution authority was explicitly revoked by the owner.",
                status_code=403,
            )
        raise OperationsError(
            "Managing agent lacks explicit contractual lease execution authority (can_execute_leases=False).",
            status_code=403,
        )

    return matching_agreement
