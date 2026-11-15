"""An old invitation must not restore a removed or suspended membership."""

from datetime import timedelta

import pytest
from django.utils import timezone
from tests.identity_support import error_detail, latest_mail_value
from tests.organizations_support import (
    ACCEPT,
    ORGANIZATIONS,
    client_for,
    organization_body,
    verified_user,
)

from apps.organizations.models import OrganizationInvitation, OrganizationMembership

pytestmark = pytest.mark.django_db


def _create(client):
    response = client.post(ORGANIZATIONS, organization_body(), format="json")
    assert response.status_code == 201, response.content
    return response.json()


def _invite(client, organization_id, email, role="member"):
    return client.post(
        f"{ORGANIZATIONS}{organization_id}/invitations/",
        {"email": email, "role": role},
        format="json",
    )


def _accept(client, token):
    return client.post(ACCEPT, {"token": token}, format="json")


def test_removed_member_needs_a_fresh_invitation(password, locmem_email) -> None:
    owner = verified_user("owner@example.com", password)
    guest = verified_user("guest@example.com", password)
    owner_client = client_for(owner)
    organization_id = _create(owner_client)["id"]
    assert _invite(owner_client, organization_id, guest.email, "manager").status_code == 201
    accepted_token = latest_mail_value("token:")
    assert _accept(client_for(guest), accepted_token).status_code == 200
    membership = OrganizationMembership.objects.get(user=guest)
    assert (
        owner_client.post(
            f"{ORGANIZATIONS}{organization_id}/members/{membership.id}/remove/",
        ).status_code
        == 200
    )
    replay = _accept(client_for(guest), accepted_token)
    assert replay.status_code == 400
    assert error_detail(replay) == "This invitation is invalid or expired."
    assert OrganizationMembership.objects.get(user=guest).status == "removed"
    assert _invite(owner_client, organization_id, guest.email, "member").status_code == 201
    assert OrganizationMembership.objects.get(user=guest).status == "invited"
    assert OrganizationInvitation.objects.filter(email=guest.email, status="pending").count() == 1
    fresh = latest_mail_value("token:")
    assert fresh != accepted_token
    restored = _accept(client_for(guest), fresh)
    assert restored.status_code == 200
    assert restored.json()["status"] == "active"
    assert restored.json()["role"] == "member"


def test_suspended_member_is_not_reactivated_by_an_old_token(password, locmem_email) -> None:
    owner = verified_user("owner@example.com", password)
    guest = verified_user("guest@example.com", password)
    owner_client = client_for(owner)
    organization_id = _create(owner_client)["id"]
    assert _invite(owner_client, organization_id, guest.email).status_code == 201
    old_token = latest_mail_value("token:")
    assert _accept(client_for(guest), old_token).status_code == 200
    membership = OrganizationMembership.objects.get(user=guest)
    assert (
        owner_client.post(
            f"{ORGANIZATIONS}{organization_id}/members/{membership.id}/suspend/",
        ).status_code
        == 200
    )
    replay = _accept(client_for(guest), old_token)
    assert replay.status_code == 400
    assert OrganizationMembership.objects.get(user=guest).status == "suspended"
    reactivate = owner_client.post(
        f"{ORGANIZATIONS}{organization_id}/members/{membership.id}/reactivate/",
    )
    assert reactivate.status_code == 200
    assert OrganizationMembership.objects.get(user=guest).status == "active"
    assert (
        owner_client.post(
            f"{ORGANIZATIONS}{organization_id}/members/{membership.id}/suspend/",
        ).status_code
        == 200
    )
    assert _invite(owner_client, organization_id, guest.email, "viewer").status_code == 201
    assert OrganizationMembership.objects.get(user=guest).status == "invited"
    refused = owner_client.post(
        f"{ORGANIZATIONS}{organization_id}/members/{membership.id}/reactivate/",
    )
    assert refused.status_code == 409
    assert _accept(client_for(guest), latest_mail_value("token:")).status_code == 200
    assert OrganizationMembership.objects.get(user=guest).status == "active"
    assert OrganizationMembership.objects.get(user=guest).role == "viewer"


def test_pending_token_cannot_activate_a_suspended_or_removed_row(password, locmem_email) -> None:
    owner = verified_user("owner@example.com", password)
    guest = verified_user("guest@example.com", password)
    owner_client = client_for(owner)
    organization_id = _create(owner_client)["id"]
    assert _invite(owner_client, organization_id, guest.email).status_code == 201
    token = latest_mail_value("token:")
    membership = OrganizationMembership.objects.get(user=guest)
    membership.status = "suspended"
    membership.save(update_fields=["status", "updated_at"])
    refused = _accept(client_for(guest), token)
    assert refused.status_code == 403
    assert error_detail(refused) == (
        "This membership needs a new invitation before it can be restored."
    )
    membership.refresh_from_db()
    assert membership.status == "suspended"
    assert OrganizationInvitation.objects.get(email=guest.email).status == "pending"
    membership.status = "removed"
    membership.save(update_fields=["status", "updated_at"])
    removed = _accept(client_for(guest), token)
    assert removed.status_code == 403
    assert OrganizationMembership.objects.get(user=guest).status == "removed"
    assert OrganizationInvitation.objects.get(email=guest.email).status == "pending"


def test_revoked_and_expired_invitations_do_not_restore_access(password, locmem_email) -> None:
    owner = verified_user("owner@example.com", password)
    guest = verified_user("guest@example.com", password)
    owner_client = client_for(owner)
    organization_id = _create(owner_client)["id"]
    created = _invite(owner_client, organization_id, guest.email)
    assert created.status_code == 201
    invitation = OrganizationInvitation.objects.get(email=guest.email, status="pending")
    revoked = owner_client.post(
        f"{ORGANIZATIONS}{organization_id}/invitations/{invitation.id}/revoke/",
    )
    assert revoked.status_code == 200
    assert _accept(client_for(guest), latest_mail_value("token:")).status_code == 400
    assert OrganizationMembership.objects.get(user=guest).status == "removed"
    assert _invite(owner_client, organization_id, guest.email).status_code == 201
    invitation = OrganizationInvitation.objects.get(email=guest.email, status="pending")
    invitation.expires_at = timezone.now() - timedelta(minutes=1)
    invitation.save(update_fields=["expires_at", "updated_at"])
    expired = _accept(client_for(guest), latest_mail_value("token:"))
    assert expired.status_code == 400
    assert OrganizationMembership.objects.get(user=guest).status == "invited"
    previously = _accept(client_for(guest), latest_mail_value("token:"))
    assert previously.status_code == 400
    assert OrganizationMembership.objects.get(user=guest).status == "invited"
