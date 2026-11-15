"""Membership changes, including the atomic ownership transfer."""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from apps.identity.domain import AccountStatus
from apps.organizations.domain import (
    CANNOT_CHANGE_MEMBERSHIP,
    LAST_OWNER,
    NOT_FOUND,
    NOT_THE_OWNER,
    OWNERSHIP_TRANSFER_REQUIRED,
    EventAction,
    MembershipStatus,
    Permission,
    Role,
    rank_exceeds,
)
from apps.organizations.errors import OrganizationError
from apps.organizations.models import OrganizationMembership
from apps.organizations.services.audit import record_event
from apps.organizations.services.authorization import (
    authorize,
    load_organization,
    lock_active_membership,
)
from apps.organizations.services.invitations import revoke_pending_for_member


def _target(organization, membership_id) -> OrganizationMembership:
    try:
        return (
            OrganizationMembership.objects.select_for_update(of=("self",))
            .select_related("user")
            .get(pk=membership_id, organization=organization)
        )
    except OrganizationMembership.DoesNotExist as exc:
        raise OrganizationError(NOT_FOUND, status_code=404) from exc


def _reject_owner_mutation(target: OrganizationMembership, new_role: str | None = None) -> None:
    if new_role == Role.OWNER or target.role == Role.OWNER:
        if new_role == Role.OWNER:
            raise OrganizationError(OWNERSHIP_TRANSFER_REQUIRED, status_code=403)
        raise OrganizationError(LAST_OWNER, status_code=409)


def _ensure_outranks(actor_role: str, target_role: str) -> None:
    if target_role == Role.OWNER:
        raise OrganizationError(LAST_OWNER, status_code=409)
    if not rank_exceeds(actor_role, target_role):
        raise OrganizationError(CANNOT_CHANGE_MEMBERSHIP, status_code=403)


def get_membership(*, user, organization_id, membership_id) -> OrganizationMembership:
    authorize(user, organization_id, Permission.ORGANIZATION_VIEW)
    with transaction.atomic():
        organization = load_organization(organization_id, lock=True)
        lock_active_membership(organization, user, Permission.ORGANIZATION_VIEW)
        membership = _target(organization, membership_id)
    if membership.status == MembershipStatus.REMOVED:
        raise OrganizationError(NOT_FOUND, status_code=404)
    return membership


def update_membership_role(*, actor, organization_id, membership_id, role: str, request=None):
    if role not in Role.VALUES:
        raise OrganizationError(CANNOT_CHANGE_MEMBERSHIP, status_code=400)
    with transaction.atomic():
        organization = load_organization(organization_id, lock=True)
        actor_membership = lock_active_membership(
            organization,
            actor,
            Permission.ORGANIZATION_MANAGE_ROLES,
        )
        target = _target(organization, membership_id)
        if target.user_id == actor.id:
            raise OrganizationError(CANNOT_CHANGE_MEMBERSHIP, status_code=403)
        if target.status not in {MembershipStatus.ACTIVE, MembershipStatus.SUSPENDED}:
            raise OrganizationError(CANNOT_CHANGE_MEMBERSHIP, status_code=409)
        _reject_owner_mutation(target, role)
        _ensure_outranks(actor_membership.role, target.role)
        if not rank_exceeds(actor_membership.role, role):
            raise OrganizationError(CANNOT_CHANGE_MEMBERSHIP, status_code=403)
        if target.role == role:
            return target
        previous = target.role
        target.role = role
        target.save(update_fields=["role", "updated_at"])
        record_event(
            actor=actor,
            organization=organization,
            action=EventAction.MEMBERSHIP_ROLE_CHANGED,
            object_type="organization_membership",
            object_id=target.id,
            request=request,
            metadata={"from_role": previous, "to_role": role},
        )
    return target


def suspend_membership(*, actor, organization_id, membership_id, request=None):
    with transaction.atomic():
        organization = load_organization(organization_id, lock=True)
        actor_membership = lock_active_membership(
            organization,
            actor,
            Permission.ORGANIZATION_MANAGE_MEMBERS,
        )
        target = _target(organization, membership_id)
        if target.user_id == actor.id:
            raise OrganizationError(CANNOT_CHANGE_MEMBERSHIP, status_code=403)
        _ensure_outranks(actor_membership.role, target.role)
        if target.status != MembershipStatus.ACTIVE:
            raise OrganizationError(CANNOT_CHANGE_MEMBERSHIP, status_code=409)
        target.status = MembershipStatus.SUSPENDED
        target.save(update_fields=["status", "updated_at"])
        revoke_pending_for_member(
            organization=organization,
            user=target.user,
            actor=actor,
            request=request,
            reason="membership_suspended",
        )
        record_event(
            actor=actor,
            organization=organization,
            action=EventAction.MEMBERSHIP_SUSPENDED,
            object_type="organization_membership",
            object_id=target.id,
            request=request,
            metadata={"role": target.role},
        )
    return target


def reactivate_membership(*, actor, organization_id, membership_id, request=None):
    with transaction.atomic():
        organization = load_organization(organization_id, lock=True)
        actor_membership = lock_active_membership(
            organization,
            actor,
            Permission.ORGANIZATION_MANAGE_MEMBERS,
        )
        target = _target(organization, membership_id)
        _ensure_outranks(actor_membership.role, target.role)
        if target.status != MembershipStatus.SUSPENDED:
            raise OrganizationError(CANNOT_CHANGE_MEMBERSHIP, status_code=409)
        if target.user.status != AccountStatus.ACTIVE:
            raise OrganizationError(CANNOT_CHANGE_MEMBERSHIP, status_code=409)
        target.status = MembershipStatus.ACTIVE
        if target.joined_at is None:
            target.joined_at = timezone.now()
        target.save(update_fields=["status", "joined_at", "updated_at"])
        record_event(
            actor=actor,
            organization=organization,
            action=EventAction.MEMBERSHIP_REACTIVATED,
            object_type="organization_membership",
            object_id=target.id,
            request=request,
            metadata={"role": target.role},
        )
    return target


def remove_membership(*, actor, organization_id, membership_id, request=None):
    with transaction.atomic():
        organization = load_organization(organization_id, lock=True)
        actor_membership = lock_active_membership(
            organization,
            actor,
            Permission.ORGANIZATION_VIEW,
        )
        target = _target(organization, membership_id)
        if target.status == MembershipStatus.REMOVED:
            raise OrganizationError(NOT_FOUND, status_code=404)
        leaving = target.user_id == actor.id
        if leaving:
            if target.role == Role.OWNER:
                raise OrganizationError(LAST_OWNER, status_code=409)
        else:
            lock_active_membership(
                organization,
                actor,
                Permission.ORGANIZATION_REMOVE_MEMBERS,
            )
            _ensure_outranks(actor_membership.role, target.role)
        target.status = MembershipStatus.REMOVED
        target.save(update_fields=["status", "updated_at"])
        revoke_pending_for_member(
            organization=organization,
            user=target.user,
            actor=actor,
            request=request,
            reason="membership_removed",
        )
        record_event(
            actor=actor,
            organization=organization,
            action=EventAction.MEMBERSHIP_REMOVED,
            object_type="organization_membership",
            object_id=target.id,
            request=request,
            metadata={"role": target.role, "self": leaving},
        )
    return target


def transfer_ownership(*, actor, organization_id, membership_id, request=None):
    with transaction.atomic():
        organization = load_organization(organization_id, lock=True)
        actor_membership = lock_active_membership(
            organization,
            actor,
            Permission.ORGANIZATION_TRANSFER_OWNERSHIP,
        )
        owner = actor_membership.role == Role.OWNER
        active = actor_membership.status == MembershipStatus.ACTIVE
        if not owner or not active:
            raise OrganizationError(NOT_THE_OWNER, status_code=409)
        target = _target(organization, membership_id)
        if target.id == actor_membership.id:
            raise OrganizationError("Choose another active member.", status_code=400)
        if target.status != MembershipStatus.ACTIVE or target.user.status != AccountStatus.ACTIVE:
            raise OrganizationError(CANNOT_CHANGE_MEMBERSHIP, status_code=409)
        previous_role = target.role
        # Demote first. The partial unique index allows zero owners inside this
        # transaction and rejects a second active owner at statement time.
        actor_membership.role = Role.ADMIN
        actor_membership.save(update_fields=["role", "updated_at"])
        target.role = Role.OWNER
        target.save(update_fields=["role", "updated_at"])
        record_event(
            actor=actor,
            organization=organization,
            action=EventAction.OWNERSHIP_TRANSFERRED,
            object_type="organization",
            object_id=organization.id,
            request=request,
            metadata={
                "from_membership_id": str(actor_membership.id),
                "to_membership_id": str(target.id),
                "from_user_id": str(actor.id),
                "to_user_id": str(target.user_id),
            },
        )
        record_event(
            actor=actor,
            organization=organization,
            action=EventAction.MEMBERSHIP_ROLE_CHANGED,
            object_type="organization_membership",
            object_id=actor_membership.id,
            request=request,
            metadata={"from_role": Role.OWNER, "to_role": Role.ADMIN},
        )
        record_event(
            actor=actor,
            organization=organization,
            action=EventAction.MEMBERSHIP_ROLE_CHANGED,
            object_type="organization_membership",
            object_id=target.id,
            request=request,
            metadata={"from_role": previous_role, "to_role": Role.OWNER},
        )
    return target
