"""Lease preparation authority.

Membership is not title. An inherited property grant is not title.
This module does not create PropertyAccess rows.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.contrib.auth.models import AbstractBaseUser

from apps.identity.domain import AccountStatus
from apps.operations.domain import AgreementStatus
from apps.operations.errors import OperationsError
from apps.operations.models import ManagementAgreement
from apps.operations.services.agreements import is_agreement_expired, verify_owner_authority
from apps.organizations.domain import Permission
from apps.organizations.errors import OrganizationError
from apps.organizations.services.authorization import authorize, ensure_active_account
from apps.tenancy.domain import PERMISSION_DENIED
from apps.tenancy.errors import TenancyError


@dataclass(frozen=True)
class LeaseAuthority:
    """Who the lease records as owner, and which agreement authorized the actor."""

    property_owner: object
    managing_organization: object | None
    management_agreement: object | None


def require_actor(actor) -> None:
    try:
        ensure_active_account(actor)
    except OrganizationError as exc:
        raise TenancyError(exc.detail, status_code=exc.status_code) from exc
    if getattr(actor, "status", None) != AccountStatus.ACTIVE:
        raise TenancyError(PERMISSION_DENIED, status_code=403)
    if not isinstance(actor, AbstractBaseUser):
        raise TenancyError(PERMISSION_DENIED, status_code=401)


def resolve_lease_write_authority(
    actor,
    property_record,
    *,
    permission: str,
    organization_id=None,
) -> LeaseAuthority:
    """Resolve a personal direct grant or an active management agreement.

    The personal path requires the actor's own DIRECT MANAGE grant.
    The organization path requires an active membership with `permission`
    and an ACTIVE agreement for that organization. The agreement's granting
    user must already hold DIRECT MANAGE. Neither path inserts a grant.
    """

    require_actor(actor)
    if organization_id is None:
        try:
            verify_owner_authority(actor, property_record)
        except OperationsError as exc:
            raise TenancyError(PERMISSION_DENIED, status_code=403) from exc
        return LeaseAuthority(actor, None, None)

    try:
        membership = authorize(actor, organization_id, permission)
    except OrganizationError as exc:
        raise TenancyError(exc.detail, status_code=exc.status_code) from exc

    agreement = (
        ManagementAgreement.objects.select_related("granting_user", "managing_organization")
        .filter(
            property=property_record,
            managing_organization_id=membership.organization_id,
            status=AgreementStatus.ACTIVE,
        )
        .first()
    )
    if agreement is None or is_agreement_expired(agreement):
        raise TenancyError(PERMISSION_DENIED, status_code=403)
    try:
        verify_owner_authority(agreement.granting_user, property_record)
    except OperationsError as exc:
        raise TenancyError(PERMISSION_DENIED, status_code=403) from exc
    return LeaseAuthority(
        agreement.granting_user,
        agreement.managing_organization,
        agreement,
    )


def actor_can_maintain_reservations(actor, property_record) -> bool:
    """Direct owners, preparers, and approvers may expire a reservation."""

    try:
        require_actor(actor)
    except TenancyError:
        return False
    try:
        verify_owner_authority(actor, property_record)
        return True
    except OperationsError:
        pass

    agreements = ManagementAgreement.objects.filter(
        property=property_record,
        status=AgreementStatus.ACTIVE,
    ).select_related("managing_organization", "granting_user")
    for agreement in agreements:
        if is_agreement_expired(agreement):
            continue
        try:
            verify_owner_authority(agreement.granting_user, property_record)
        except OperationsError:
            continue
        for permission in (Permission.LEASES_PREPARE, Permission.LEASES_APPROVE):
            try:
                authorize(actor, agreement.managing_organization_id, permission)
                return True
            except OrganizationError:
                continue
    return False
