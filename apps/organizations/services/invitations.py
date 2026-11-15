"""Invitation creation, delivery, and one-time acceptance."""

from __future__ import annotations

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.identity.domain import normalize_email
from apps.identity.models import User
from apps.identity.services.secrets import expiry, hash_secret, new_token
from apps.organizations.domain import (
    ALREADY_HAS_MEMBERSHIP,
    ALREADY_MEMBER,
    CONFLICT,
    DUPLICATE_INVITATION,
    INVITATION_INVALID,
    INVITATION_MISMATCH,
    INVITATION_TTL,
    MEMBERSHIP_NEEDS_INVITATION,
    NOT_FOUND,
    ORG_NOT_ACTIVE,
    ROLE_NOT_ASSIGNABLE,
    VERIFY_EMAIL_FIRST,
    EventAction,
    InvitationStatus,
    MembershipSource,
    MembershipStatus,
    OrganizationStatus,
    Permission,
    Role,
    rank_exceeds,
)
from apps.organizations.errors import OrganizationError
from apps.organizations.models import OrganizationInvitation, OrganizationMembership
from apps.organizations.services.audit import record_event
from apps.organizations.services.authorization import (
    ensure_active_account,
    load_organization,
    lock_active_membership,
)
from apps.organizations.services.delivery import send_organization_invitation


def _integrity(exc: IntegrityError) -> OrganizationError:
    text = str(exc).lower()
    if "invitation" in text:
        return OrganizationError(DUPLICATE_INVITATION, status_code=409)
    if "membership" in text:
        return OrganizationError(ALREADY_HAS_MEMBERSHIP, status_code=409)
    return OrganizationError(CONFLICT, status_code=409)


def _mark_expired(invitation: OrganizationInvitation, actor, request) -> None:
    if invitation.status != InvitationStatus.PENDING or invitation.expires_at > timezone.now():
        return
    invitation.status = InvitationStatus.EXPIRED
    invitation.save(update_fields=["status", "updated_at"])
    record_event(
        actor=actor,
        organization=invitation.organization,
        action=EventAction.INVITATION_EXPIRED,
        object_type="organization_invitation",
        object_id=invitation.id,
        request=request,
        metadata={"email": invitation.email, "reason": "elapsed"},
    )


def _expire_stale(organization, email: str, actor, request) -> None:
    stale = OrganizationInvitation.objects.select_for_update(of=("self",)).filter(
        organization=organization,
        email=email,
        status=InvitationStatus.PENDING,
        expires_at__lte=timezone.now(),
    )
    for invitation in stale:
        _mark_expired(invitation, actor, request)


def _lock_invitation(organization, invitation_id) -> OrganizationInvitation:
    try:
        return OrganizationInvitation.objects.select_for_update(of=("self",)).get(
            pk=invitation_id,
            organization=organization,
        )
    except OrganizationInvitation.DoesNotExist as exc:
        raise OrganizationError(NOT_FOUND, status_code=404) from exc


def _prepare_known_user(organization, email: str, role: str) -> None:
    user = User.objects.filter(email=email).first()
    if user is None:
        return
    membership = (
        OrganizationMembership.objects.select_for_update(of=("self",))
        .filter(organization=organization, user=user)
        .first()
    )
    if membership is None:
        OrganizationMembership.objects.create(
            organization=organization,
            user=user,
            role=role,
            status=MembershipStatus.INVITED,
            source=MembershipSource.INVITATION,
        )
        return
    if membership.status == MembershipStatus.ACTIVE:
        raise OrganizationError(ALREADY_HAS_MEMBERSHIP, status_code=409)
    # A fresh invitation is an explicit decision. It may move a removed or
    # suspended row back to invited. Acceptance still refuses those statuses
    # unless this invitation has set invited. It does not restore access.
    membership.role = role
    membership.status = MembershipStatus.INVITED
    membership.source = MembershipSource.INVITATION
    membership.joined_at = None
    membership.save(update_fields=["role", "status", "source", "joined_at", "updated_at"])


def _store_invitation(
    *,
    organization,
    actor,
    actor_role: str,
    email: str,
    role: str,
    raw_token: str,
    request,
):
    if role == Role.OWNER or not rank_exceeds(actor_role, role):
        raise OrganizationError(ROLE_NOT_ASSIGNABLE, status_code=403)
    _expire_stale(organization, email, actor, request)
    if OrganizationInvitation.objects.filter(
        organization=organization,
        email=email,
        status=InvitationStatus.PENDING,
    ).exists():
        raise OrganizationError(DUPLICATE_INVITATION, status_code=409)
    _prepare_known_user(organization, email, role)
    invitation = OrganizationInvitation.objects.create(
        organization=organization,
        email=email,
        role=role,
        invited_by=actor,
        token_hash=hash_secret(raw_token),
        expires_at=expiry(INVITATION_TTL),
        status=InvitationStatus.PENDING,
    )
    record_event(
        actor=actor,
        organization=organization,
        action=EventAction.INVITATION_CREATED,
        object_type="organization_invitation",
        object_id=invitation.id,
        request=request,
        metadata={"email": email, "role": role},
    )
    return invitation


def create_invitation(*, actor, organization_id, email: str, role: str, request=None):
    ensure_active_account(actor)
    normalized = normalize_email(email)
    if not normalized:
        raise OrganizationError("Enter an email address.", status_code=400)
    if role not in Role.VALUES or role == Role.OWNER:
        raise OrganizationError(ROLE_NOT_ASSIGNABLE, status_code=403)
    raw_token = new_token()
    try:
        with transaction.atomic():
            organization = load_organization(organization_id, lock=True)
            actor_membership = lock_active_membership(
                organization,
                actor,
                Permission.ORGANIZATION_INVITE_MEMBERS,
            )
            if not rank_exceeds(actor_membership.role, role):
                raise OrganizationError(ROLE_NOT_ASSIGNABLE, status_code=403)
            invitation = _store_invitation(
                organization=organization,
                actor=actor,
                actor_role=actor_membership.role,
                email=normalized,
                role=role,
                raw_token=raw_token,
                request=request,
            )
    except IntegrityError as exc:
        raise _integrity(exc) from None
    send_organization_invitation(
        organization=invitation.organization,
        email=normalized,
        role=role,
        raw_token=raw_token,
    )
    return invitation


def resend_invitation(*, actor, organization_id, invitation_id, request=None):
    ensure_active_account(actor)
    raw_token = new_token()
    error = None
    invitation = None
    with transaction.atomic():
        organization = load_organization(organization_id, lock=True)
        lock_active_membership(organization, actor, Permission.ORGANIZATION_INVITE_MEMBERS)
        invitation = _lock_invitation(organization, invitation_id)
        _mark_expired(invitation, actor, request)
        if invitation.status != InvitationStatus.PENDING:
            error = OrganizationError(INVITATION_INVALID, status_code=400)
        else:
            invitation.token_hash = hash_secret(raw_token)
            invitation.expires_at = expiry(INVITATION_TTL)
            invitation.save(update_fields=["token_hash", "expires_at", "updated_at"])
            record_event(
                actor=actor,
                organization=organization,
                action=EventAction.INVITATION_RESENT,
                object_type="organization_invitation",
                object_id=invitation.id,
                request=request,
                metadata={"email": invitation.email, "role": invitation.role},
            )
    if error is not None:
        raise error
    send_organization_invitation(
        organization=invitation.organization,
        email=invitation.email,
        role=invitation.role,
        raw_token=raw_token,
    )
    return invitation


def revoke_invitation(*, actor, organization_id, invitation_id, request=None):
    ensure_active_account(actor)
    error = None
    with transaction.atomic():
        organization = load_organization(organization_id, lock=True)
        lock_active_membership(organization, actor, Permission.ORGANIZATION_INVITE_MEMBERS)
        invitation = _lock_invitation(organization, invitation_id)
        _mark_expired(invitation, actor, request)
        if invitation.status != InvitationStatus.PENDING:
            error = OrganizationError(INVITATION_INVALID, status_code=400)
        else:
            now = timezone.now()
            invitation.status = InvitationStatus.REVOKED
            invitation.revoked_at = now
            invitation.save(update_fields=["status", "revoked_at", "updated_at"])
            invited_user = User.objects.filter(email=invitation.email).first()
            if invited_user is not None:
                membership = (
                    OrganizationMembership.objects.select_for_update(of=("self",))
                    .filter(
                        organization=organization,
                        user=invited_user,
                        status=MembershipStatus.INVITED,
                    )
                    .first()
                )
                if membership is not None:
                    membership.status = MembershipStatus.REMOVED
                    membership.save(update_fields=["status", "updated_at"])
                    record_event(
                        actor=actor,
                        organization=organization,
                        action=EventAction.MEMBERSHIP_REMOVED,
                        object_type="organization_membership",
                        object_id=membership.id,
                        request=request,
                        metadata={"role": membership.role, "reason": "invitation_revoked"},
                    )
            record_event(
                actor=actor,
                organization=organization,
                action=EventAction.INVITATION_REVOKED,
                object_type="organization_invitation",
                object_id=invitation.id,
                request=request,
                metadata={"email": invitation.email, "role": invitation.role},
            )
    if error is not None:
        raise error
    return invitation


def _acceptance_block(invitation, user) -> OrganizationError | None:
    email_matches = normalize_email(user.email) == invitation.email
    if not email_matches:
        return OrganizationError(INVITATION_MISMATCH, status_code=403)
    if user.email_verified_at is None:
        return OrganizationError(VERIFY_EMAIL_FIRST, status_code=403)
    return None


def _complete_acceptance(invitation, user, request) -> OrganizationMembership:
    organization = load_organization(invitation.organization_id, lock=True)
    if organization.status != OrganizationStatus.ACTIVE:
        raise OrganizationError(ORG_NOT_ACTIVE, status_code=403)
    if invitation.role == Role.OWNER or not rank_exceeds(Role.OWNER, invitation.role):
        raise OrganizationError(ROLE_NOT_ASSIGNABLE, status_code=403)
    membership = (
        OrganizationMembership.objects.select_for_update(of=("self",))
        .filter(organization=organization, user=user)
        .first()
    )
    if membership is not None and membership.status == MembershipStatus.ACTIVE:
        raise OrganizationError(ALREADY_MEMBER, status_code=409)
    if membership is not None and membership.status != MembershipStatus.INVITED:
        raise OrganizationError(MEMBERSHIP_NEEDS_INVITATION, status_code=403)
    now = timezone.now()
    if membership is None:
        membership = OrganizationMembership(
            organization=organization,
            user=user,
            source=MembershipSource.INVITATION,
        )
    membership.role = invitation.role
    membership.status = MembershipStatus.ACTIVE
    membership.source = MembershipSource.INVITATION
    if membership.joined_at is None:
        membership.joined_at = now
    membership.save()
    invitation.status = InvitationStatus.ACCEPTED
    invitation.accepted_at = now
    invitation.save(update_fields=["status", "accepted_at", "updated_at"])
    record_event(
        actor=user,
        organization=organization,
        action=EventAction.INVITATION_ACCEPTED,
        object_type="organization_invitation",
        object_id=invitation.id,
        request=request,
        metadata={"email": invitation.email, "role": invitation.role},
    )
    record_event(
        actor=user,
        organization=organization,
        action=EventAction.MEMBERSHIP_ACTIVATED,
        object_type="organization_membership",
        object_id=membership.id,
        request=request,
        metadata={"role": membership.role, "source": MembershipSource.INVITATION},
    )
    return membership


def revoke_pending_for_member(*, organization, user, actor, request, reason: str) -> None:
    """Revoke outstanding tokens when a membership is suspended or removed.

    The caller already holds the organization row lock. Invitations are locked
    after that, which matches accept_invitation and avoids a deadlock.
    """

    email = normalize_email(user.email)
    pending = OrganizationInvitation.objects.select_for_update(of=("self",)).filter(
        organization=organization,
        email=email,
        status=InvitationStatus.PENDING,
    )
    now = timezone.now()
    for invitation in pending:
        invitation.status = InvitationStatus.REVOKED
        invitation.revoked_at = now
        invitation.save(update_fields=["status", "revoked_at", "updated_at"])
        record_event(
            actor=actor,
            organization=organization,
            action=EventAction.INVITATION_REVOKED,
            object_type="organization_invitation",
            object_id=invitation.id,
            request=request,
            metadata={"email": invitation.email, "role": invitation.role, "reason": reason},
        )


def _lock_token(raw_token: str):
    """Lock the organization before the invitation so accept cannot deadlock revoke."""

    token = str(raw_token or "").strip()
    if not token:
        return None
    preview = (
        OrganizationInvitation.objects.filter(token_hash=hash_secret(token))
        .only("id", "organization_id")
        .first()
    )
    if preview is None:
        return None
    load_organization(preview.organization_id, lock=True)
    return (
        OrganizationInvitation.objects.select_for_update(of=("self",))
        .select_related("organization")
        .get(pk=preview.id)
    )


def accept_invitation(*, user, raw_token: str, request=None) -> OrganizationMembership:
    ensure_active_account(user)
    error = None
    membership = None
    with transaction.atomic():
        invitation = _lock_token(raw_token)
        if invitation is None or invitation.status != InvitationStatus.PENDING:
            error = OrganizationError(INVITATION_INVALID, status_code=400)
        else:
            _mark_expired(invitation, user, request)
            if invitation.status != InvitationStatus.PENDING:
                error = OrganizationError(INVITATION_INVALID, status_code=400)
            else:
                error = _acceptance_block(invitation, user)
                if error is None:
                    membership = _complete_acceptance(invitation, user, request)
    if error is not None:
        raise error
    return membership


def inspect_invitation(*, user, raw_token: str, request=None) -> dict:
    ensure_active_account(user)
    with transaction.atomic():
        invitation = _lock_token(raw_token)
        if invitation is None:
            raise OrganizationError(INVITATION_INVALID, status_code=400)
        _mark_expired(invitation, user, request)
        payload = {
            "organization_display_name": invitation.organization.display_name,
            "role": invitation.role,
            "status": invitation.status,
            "expires_at": invitation.expires_at,
            "email_matches": normalize_email(user.email) == invitation.email,
        }
    return payload


def get_invitation(*, user, organization_id, invitation_id, request=None):
    error = None
    with transaction.atomic():
        organization = load_organization(organization_id, lock=True)
        lock_active_membership(organization, user, Permission.ORGANIZATION_INVITE_MEMBERS)
        invitation = _lock_invitation(organization, invitation_id)
        _mark_expired(invitation, user, request)
        if invitation.organization_id != organization.id:
            error = OrganizationError(NOT_FOUND, status_code=404)
    if error is not None:
        raise error
    return invitation
