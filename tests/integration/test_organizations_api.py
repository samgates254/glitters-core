"""Organization, membership, and invitation API flows."""

from datetime import timedelta

import pytest
from django.utils import timezone
from tests.identity_support import error_detail, latest_mail_value
from tests.organizations_support import (
    ACCEPT,
    INSPECT,
    MY_MEMBERSHIPS,
    ORGANIZATIONS,
    ROLES,
    client_for,
    organization_body,
    verified_user,
)

from apps.identity.domain import AccountStatus
from apps.identity.services.lifecycle import change_status
from apps.identity.services.secrets import hash_secret
from apps.organizations.models import (
    OrganizationEvent,
    OrganizationInvitation,
    OrganizationMembership,
)

pytestmark = pytest.mark.django_db


def _create(client, **overrides):
    response = client.post(ORGANIZATIONS, organization_body(**overrides), format="json")
    assert response.status_code == 201, response.content
    return response.json()


def _invite(client, organization_id, email, role="member"):
    return client.post(
        f"{ORGANIZATIONS}{organization_id}/invitations/",
        {"email": email, "role": role},
        format="json",
    )


def _accept(client):
    return client.post(ACCEPT, {"token": latest_mail_value("token:")}, format="json")


def test_individual_is_not_forced_into_an_organization(password) -> None:
    user = verified_user("ada@example.com", password)
    client = client_for(user)
    listed = client.get(ORGANIZATIONS)
    roles = client.get(ROLES)
    mine = client.get(MY_MEMBERSHIPS)
    assert listed.status_code == 200
    assert listed.json()["count"] == 0
    assert roles.status_code == 200
    assert "property.publish" not in roles.json()["reserved_permissions"]
    assert "leads.view" not in roles.json()["reserved_permissions"]
    assert "leads.manage" not in roles.json()["reserved_permissions"]
    assert "listing.manage" in roles.json()["reserved_permissions"]
    assert "property.create" not in roles.json()["reserved_permissions"]
    assert mine.json()["count"] == 0
    assert client.get("/api/v1/auth/me/").status_code == 200


def test_create_lists_and_isolates_organizations(password) -> None:
    owner = verified_user("owner@example.com", password)
    stranger = verified_user("stranger@example.com", password)
    owner_client = client_for(owner)
    created = _create(
        owner_client,
        registration_reference="PVT-219",
        contact_phone="0712345678",
        contact_email="office@acme.test",
        region="Nairobi",
    )
    assert created["status"] == "active"
    assert created["slug"] == "acme-properties"
    assert created["registration_reference"] == "PVT-219"
    assert created["contact_phone"] == "+254712345678"
    assert created["country"] == "KE"
    listed = owner_client.get(ORGANIZATIONS)
    assert listed.json()["count"] == 1
    assert client_for(stranger).get(ORGANIZATIONS).json()["count"] == 0
    detail = owner_client.get(f"{ORGANIZATIONS}{created['id']}/")
    assert detail.status_code == 200
    assert detail.json()["legal_name"] == "Acme Properties Limited"
    events = OrganizationEvent.objects.filter(organization_id=created["id"])
    assert set(events.values_list("action", flat=True)) >= {
        "organization.created",
        "membership.activated",
    }
    assert not events.filter(action="organization.status_changed").exists()
    membership = OrganizationMembership.objects.get(organization_id=created["id"])
    assert membership.role == "owner"
    assert membership.status == "active"
    assert owner.is_staff is False


def test_unverified_account_cannot_create_an_organization(password) -> None:
    user = verified_user("ada@example.com", password)
    user.email_verified_at = None
    user.save(update_fields=["email_verified_at", "updated_at"])
    response = client_for(user).post(ORGANIZATIONS, organization_body(), format="json")
    assert response.status_code == 403
    assert error_detail(response) == "Verify your email before continuing."


def test_slug_collision_and_profile_versus_settings(password) -> None:
    owner = verified_user("owner@example.com", password)
    client = client_for(owner)
    first = _create(client)
    conflict = client.post(
        ORGANIZATIONS,
        organization_body(display_name="Other", slug="acme-properties"),
        format="json",
    )
    assert conflict.status_code == 409
    renamed = client.patch(
        f"{ORGANIZATIONS}{first['id']}/",
        {"display_name": "Acme Homes", "region": "Mombasa"},
        format="json",
    )
    assert renamed.status_code == 200
    assert renamed.json()["display_name"] == "Acme Homes"
    invalid = client.patch(
        f"{ORGANIZATIONS}{first['id']}/",
        {"country": "KENYA", "website": "notaurl"},
        format="json",
    )
    assert invalid.status_code == 400


def test_membership_invitation_acceptance_and_directory(password, locmem_email) -> None:
    owner = verified_user("owner@example.com", password)
    member = verified_user("member@example.com", password)
    viewer = verified_user("viewer@example.com", password)
    owner_client = client_for(owner)
    organization = _create(owner_client)
    organization_id = organization["id"]
    invited = _invite(owner_client, organization_id, "Member@Example.com", "manager")
    assert invited.status_code == 201, invited.content
    assert "token" not in invited.json()
    token = latest_mail_value("token:")
    stored = OrganizationInvitation.objects.get(email="member@example.com")
    assert stored.token_hash == hash_secret(token)
    assert token not in stored.token_hash
    created_event = OrganizationEvent.objects.get(action="invitation.created")
    assert token not in str(created_event.metadata)
    assert created_event.metadata["email"] == "member@example.com"
    pending = OrganizationMembership.objects.get(user=member)
    assert pending.status == "invited"
    inspect = client_for(member).post(INSPECT, {"token": token}, format="json")
    assert inspect.status_code == 200
    assert inspect.json()["email_matches"] is True
    assert "email" not in inspect.json()
    assert inspect.json()["organization_display_name"] == "Acme Properties"
    accepted = client_for(member).post(ACCEPT, {"token": token}, format="json")
    assert accepted.status_code == 200
    assert accepted.json()["role"] == "manager"
    assert accepted.json()["status"] == "active"
    replay = client_for(member).post(ACCEPT, {"token": token}, format="json")
    assert replay.status_code == 400
    assert OrganizationMembership.objects.filter(user=member, status="active").count() == 1
    second = _invite(owner_client, organization_id, viewer.email, "viewer")
    assert second.status_code == 201
    assert _accept(client_for(viewer)).status_code == 200
    duplicate = _invite(owner_client, organization_id, viewer.email, "member")
    assert duplicate.status_code == 409
    members = owner_client.get(f"{ORGANIZATIONS}{organization_id}/members/")
    assert members.json()["count"] == 3
    mine = client_for(member).get(MY_MEMBERSHIPS)
    assert mine.json()["count"] == 1
    assert mine.json()["results"][0]["organization"]["id"] == organization_id


def test_expired_revoked_resend_and_email_mismatch(password, locmem_email) -> None:
    owner = verified_user("owner@example.com", password)
    guest = verified_user("guest@example.com", password)
    other = verified_user("other@example.com", password)
    owner_client = client_for(owner)
    organization_id = _create(owner_client)["id"]
    assert _invite(owner_client, organization_id, guest.email, "member").status_code == 201
    invitation = OrganizationInvitation.objects.get(email=guest.email)
    invitation.expires_at = timezone.now() - timedelta(minutes=1)
    invitation.save(update_fields=["expires_at", "updated_at"])
    expired = client_for(guest).post(ACCEPT, {"token": latest_mail_value("token:")}, format="json")
    assert expired.status_code == 400
    invitation.refresh_from_db()
    assert invitation.status == "expired"
    again = _invite(owner_client, organization_id, guest.email, "member")
    assert again.status_code == 201
    mismatch = client_for(other).post(ACCEPT, {"token": latest_mail_value("token:")}, format="json")
    assert mismatch.status_code == 403
    assert error_detail(mismatch) == "This invitation does not match the signed-in account."
    assert not OrganizationMembership.objects.filter(user=other).exists()
    invitation.refresh_from_db()
    assert invitation.status == "expired"
    current = OrganizationInvitation.objects.get(email=guest.email, status="pending")
    old_token = latest_mail_value("token:")
    resent = owner_client.post(
        f"{ORGANIZATIONS}{organization_id}/invitations/{current.id}/resend/",
        format="json",
    )
    assert resent.status_code == 200
    assert latest_mail_value("token:") != old_token
    stale = client_for(guest).post(ACCEPT, {"token": old_token}, format="json")
    assert stale.status_code == 400
    revoked = owner_client.post(
        f"{ORGANIZATIONS}{organization_id}/invitations/{current.id}/revoke/",
        format="json",
    )
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"
    replay = client_for(guest).post(
        ACCEPT,
        {"token": latest_mail_value("token:")},
        format="json",
    )
    assert replay.status_code == 400
    assert OrganizationMembership.objects.get(user=guest).status == "removed"


def test_unverified_matching_email_does_not_consume_the_invitation(password, locmem_email) -> None:
    owner = verified_user("owner@example.com", password)
    guest = verified_user("guest@example.com", password)
    guest.email_verified_at = None
    guest.save(update_fields=["email_verified_at", "updated_at"])
    owner_client = client_for(owner)
    organization_id = _create(owner_client)["id"]
    assert _invite(owner_client, organization_id, guest.email).status_code == 201
    response = _accept(client_for(guest))
    assert response.status_code == 403
    assert error_detail(response) == "Verify your email before continuing."
    assert OrganizationInvitation.objects.get().status == "pending"


def test_roles_permissions_and_ownership_transfer(password, locmem_email) -> None:
    owner = verified_user("owner@example.com", password)
    admin = verified_user("admin@example.com", password)
    manager = verified_user("manager@example.com", password)
    member = verified_user("member@example.com", password)
    owner_client = client_for(owner)
    organization = _create(owner_client)
    organization_id = organization["id"]
    for person, role in ((admin, "admin"), (manager, "manager"), (member, "member")):
        assert _invite(owner_client, organization_id, person.email, role).status_code == 201
        assert _accept(client_for(person)).status_code == 200
    owner_permissions = owner_client.get(f"{ORGANIZATIONS}{organization_id}/permissions/")
    assert owner_permissions.status_code == 200
    assert "organization.transfer_ownership" in owner_permissions.json()["permissions"]
    assert "property.create" in owner_permissions.json()["permissions"]
    assert "property.update" in owner_permissions.json()["permissions"]
    assert "property.archive" in owner_permissions.json()["permissions"]
    assert "property.publish" in owner_permissions.json()["permissions"]
    assert "leads.view" in owner_permissions.json()["permissions"]
    assert "leads.manage" in owner_permissions.json()["permissions"]
    assert "listing.manage" not in owner_permissions.json()["permissions"]
    member_client = client_for(member)
    member_permissions = member_client.get(f"{ORGANIZATIONS}{organization_id}/permissions/")
    assert member_permissions.json()["permissions"] == ["organization.view"]
    assert "leads.view" not in member_permissions.json()["permissions"]
    assert "leads.manage" not in member_permissions.json()["permissions"]
    denied = member_client.patch(
        f"{ORGANIZATIONS}{organization_id}/",
        {"display_name": "Taken"},
        format="json",
    )
    assert denied.status_code == 403
    manager_client = client_for(manager)
    updated = manager_client.patch(
        f"{ORGANIZATIONS}{organization_id}/",
        {"display_name": "Acme Desk"},
        format="json",
    )
    assert updated.status_code == 200
    settings_denied = manager_client.patch(
        f"{ORGANIZATIONS}{organization_id}/",
        {"legal_name": "Renamed Limited"},
        format="json",
    )
    assert settings_denied.status_code == 403
    legal = client_for(admin).patch(
        f"{ORGANIZATIONS}{organization_id}/",
        {"legal_name": "Acme Properties Ltd"},
        format="json",
    )
    assert legal.status_code == 200
    assert legal.json()["legal_name"] == "Acme Properties Ltd"
    too_high = manager_client.post(
        f"{ORGANIZATIONS}{organization_id}/invitations/",
        {"email": "new@example.com", "role": "admin"},
        format="json",
    )
    assert too_high.status_code == 403
    viewer_invite = manager_client.post(
        f"{ORGANIZATIONS}{organization_id}/invitations/",
        {"email": "viewer@example.com", "role": "viewer"},
        format="json",
    )
    assert viewer_invite.status_code == 201
    owner_membership = OrganizationMembership.objects.get(user=owner)
    self_demotion = owner_client.patch(
        f"{ORGANIZATIONS}{organization_id}/members/{owner_membership.id}/",
        {"role": "admin"},
        format="json",
    )
    assert self_demotion.status_code == 403
    admin_membership = OrganizationMembership.objects.get(user=admin)
    owner_blocked = client_for(admin).patch(
        f"{ORGANIZATIONS}{organization_id}/members/{owner_membership.id}/",
        {"role": "admin"},
        format="json",
    )
    assert owner_blocked.status_code == 409
    member_membership = OrganizationMembership.objects.get(user=member)
    promoted = owner_client.patch(
        f"{ORGANIZATIONS}{organization_id}/members/{member_membership.id}/",
        {"role": "manager"},
        format="json",
    )
    assert promoted.status_code == 200
    assert promoted.json()["role"] == "manager"
    cannot_remove_owner = client_for(admin).post(
        f"{ORGANIZATIONS}{organization_id}/members/{owner_membership.id}/remove/",
        format="json",
    )
    assert cannot_remove_owner.status_code == 409
    cannot_suspend_owner = client_for(admin).post(
        f"{ORGANIZATIONS}{organization_id}/members/{owner_membership.id}/suspend/",
        format="json",
    )
    assert cannot_suspend_owner.status_code == 409
    leave = client_for(admin).post(
        f"{ORGANIZATIONS}{organization_id}/members/{admin_membership.id}/remove/",
        format="json",
    )
    assert leave.status_code == 200
    assert leave.json()["status"] == "removed"
    transferred = owner_client.post(
        f"{ORGANIZATIONS}{organization_id}/ownership-transfer/",
        {"membership_id": str(member_membership.id)},
        format="json",
    )
    assert transferred.status_code == 200
    assert transferred.json()["role"] == "owner"
    owners = OrganizationMembership.objects.filter(
        organization_id=organization_id,
        role="owner",
        status="active",
    )
    assert owners.count() == 1
    assert owners.get().user_id == member.id
    former = owner_client.post(
        f"{ORGANIZATIONS}{organization_id}/ownership-transfer/",
        {"membership_id": str(admin_membership.id)},
        format="json",
    )
    assert former.status_code == 403
    assert OrganizationEvent.objects.filter(action="ownership.transferred").count() == 1


def test_suspend_reactivate_and_manager_cannot_remove(password, locmem_email) -> None:
    owner = verified_user("owner@example.com", password)
    manager = verified_user("manager@example.com", password)
    member = verified_user("member@example.com", password)
    owner_client = client_for(owner)
    organization_id = _create(owner_client)["id"]
    assert _invite(owner_client, organization_id, manager.email, "manager").status_code == 201
    assert _accept(client_for(manager)).status_code == 200
    assert _invite(owner_client, organization_id, member.email, "member").status_code == 201
    assert _accept(client_for(member)).status_code == 200
    member_membership = OrganizationMembership.objects.get(user=member)
    suspended = client_for(manager).post(
        f"{ORGANIZATIONS}{organization_id}/members/{member_membership.id}/suspend/",
        format="json",
    )
    assert suspended.status_code == 200
    blocked = client_for(member).get(f"{ORGANIZATIONS}{organization_id}/")
    assert blocked.status_code == 403
    assert error_detail(blocked) == "This membership is not active."
    still_listed = client_for(member).get(MY_MEMBERSHIPS)
    assert still_listed.json()["results"][0]["status"] == "suspended"
    removal = client_for(manager).post(
        f"{ORGANIZATIONS}{organization_id}/members/{member_membership.id}/remove/",
        format="json",
    )
    assert removal.status_code == 403
    restored = owner_client.post(
        f"{ORGANIZATIONS}{organization_id}/members/{member_membership.id}/reactivate/",
        format="json",
    )
    assert restored.status_code == 200
    assert client_for(member).get(f"{ORGANIZATIONS}{organization_id}/").status_code == 200


def test_owner_can_deactivate_and_staff_controls_other_statuses(password) -> None:
    from tests.identity_support import enable_confirmed_mfa

    owner = verified_user("owner@example.com", password)
    staff = verified_user("staff@example.com", password, is_staff=True)
    enable_confirmed_mfa(staff)
    owner_client = client_for(owner)
    staff_client = client_for(staff)
    organization_id = _create(owner_client)["id"]
    hidden = staff_client.get(f"{ORGANIZATIONS}{organization_id}/")
    assert hidden.status_code == 403
    assert "legal_name" not in hidden.json()
    refused = owner_client.post(
        f"{ORGANIZATIONS}{organization_id}/status/",
        {"status": "suspended"},
        format="json",
    )
    assert refused.status_code == 409
    deactivated = owner_client.post(
        f"{ORGANIZATIONS}{organization_id}/status/",
        {"status": "deactivated"},
        format="json",
        HTTP_X_REQUEST_ID="trace-org-1",
    )
    assert deactivated.status_code == 200
    assert deactivated.json() == {"id": organization_id, "status": "deactivated"}
    assert owner_client.get(f"{ORGANIZATIONS}{organization_id}/").status_code == 403
    visible = owner_client.get(MY_MEMBERSHIPS)
    assert visible.json()["results"][0]["organization"]["status"] == "deactivated"
    event = OrganizationEvent.objects.get(action="organization.status_changed")
    assert event.request_id == "trace-org-1"
    assert event.metadata == {"from_status": "active", "to_status": "deactivated"}
    restored = staff_client.post(
        f"{ORGANIZATIONS}{organization_id}/status/",
        {"status": "active"},
        format="json",
    )
    assert restored.status_code == 200
    review = staff_client.post(
        f"{ORGANIZATIONS}{organization_id}/status/",
        {"status": "pending_verification"},
        format="json",
    )
    assert review.status_code == 200
    assert owner_client.get(f"{ORGANIZATIONS}{organization_id}/permissions/").status_code == 403
    rejected = staff_client.post(
        f"{ORGANIZATIONS}{organization_id}/status/",
        {"status": "rejected"},
        format="json",
    )
    assert rejected.status_code == 200
    terminal = staff_client.post(
        f"{ORGANIZATIONS}{organization_id}/status/",
        {"status": "active"},
        format="json",
    )
    assert terminal.status_code == 409


def test_suspended_account_cannot_use_organization_routes(password) -> None:
    owner = verified_user("owner@example.com", password)
    client = client_for(owner)
    organization_id = _create(client)["id"]
    change_status(owner, AccountStatus.SUSPENDED, force=True)
    response = client.get(f"{ORGANIZATIONS}{organization_id}/")
    assert response.status_code == 403
    assert error_detail(response) == "This account cannot sign in."


def test_second_organization_does_not_inherit_the_first_role(password, locmem_email) -> None:
    owner = verified_user("owner@example.com", password)
    other = verified_user("other@example.com", password)
    owner_client = client_for(owner)
    first = _create(owner_client)["id"]
    second = _create(
        client_for(other),
        legal_name="Beta Homes Limited",
        display_name="Beta Homes",
        organization_type="developer",
    )["id"]
    assert _invite(client_for(other), second, owner.email, "viewer").status_code == 201
    assert _accept(owner_client).status_code == 200
    first_permissions = owner_client.get(f"{ORGANIZATIONS}{first}/permissions/").json()
    second_permissions = owner_client.get(f"{ORGANIZATIONS}{second}/permissions/").json()
    assert first_permissions["role"] == "owner"
    assert second_permissions["role"] == "viewer"
    blocked = owner_client.patch(
        f"{ORGANIZATIONS}{second}/",
        {"display_name": "Mine"},
        format="json",
    )
    assert blocked.status_code == 403
    listed = owner_client.get(ORGANIZATIONS)
    assert listed.json()["count"] == 2


def test_cross_organization_membership_lookup_is_not_found(password, locmem_email) -> None:
    first_owner = verified_user("one@example.com", password)
    second_owner = verified_user("two@example.com", password)
    first = _create(client_for(first_owner))["id"]
    second = _create(
        client_for(second_owner),
        legal_name="Beta Homes Limited",
        display_name="Beta Homes",
    )["id"]
    foreign = OrganizationMembership.objects.get(organization_id=second)
    response = client_for(first_owner).get(f"{ORGANIZATIONS}{first}/members/{foreign.id}/")
    assert response.status_code == 404
    assert "email" not in response.json()["errors"]
    outside = client_for(first_owner).get(f"{ORGANIZATIONS}{second}/")
    assert outside.status_code == 403
    assert "legal_name" not in outside.json()


def test_pagination_and_audit_visibility(password) -> None:
    owner = verified_user("owner@example.com", password)
    client = client_for(owner)
    _create(client, display_name="Alpha Agency", legal_name="Alpha Agency Limited")
    _create(client, display_name="Beta Agency", legal_name="Beta Agency Limited")
    page = client.get(f"{ORGANIZATIONS}?page_size=1")
    assert page.status_code == 200
    assert page.json()["count"] == 2
    assert len(page.json()["results"]) == 1
    organization_id = page.json()["results"][0]["id"]
    audit = client.get(f"{ORGANIZATIONS}{organization_id}/audit/")
    assert audit.status_code == 200
    assert audit.json()["count"] >= 1
    stranger = client_for(verified_user("stranger@example.com", password))
    assert stranger.get(f"{ORGANIZATIONS}{organization_id}/audit/").status_code == 403


def test_active_member_acceptance_does_not_change_role(password) -> None:
    owner = verified_user("owner@example.com", password)
    member = verified_user("member@example.com", password)
    organization_id = _create(client_for(owner))["id"]
    organization = OrganizationMembership.objects.get().organization
    OrganizationMembership.objects.create(
        organization=organization,
        user=member,
        role="viewer",
        status="active",
        source="invitation",
        joined_at=timezone.now(),
    )
    invitation = OrganizationInvitation.objects.create(
        organization=organization,
        email=member.email,
        role="admin",
        invited_by=owner,
        token_hash=hash_secret("already-member-token"),
        expires_at=timezone.now() + timedelta(days=1),
    )
    response = client_for(member).post(ACCEPT, {"token": "already-member-token"}, format="json")
    assert response.status_code == 409
    invitation.refresh_from_db()
    assert invitation.status == "pending"
    assert OrganizationMembership.objects.get(user=member).role == "viewer"
    assert organization_id


def test_lookup_validation_and_membership_edges(password, locmem_email) -> None:
    from uuid import uuid4

    owner = verified_user("owner@example.com", password)
    member = verified_user("member@example.com", password)
    stranger = verified_user("stranger@example.com", password)
    client = client_for(owner)
    organization_id = _create(client, slug="acme-properties")["id"]
    other_id = _create(
        client,
        legal_name="Beta Homes Limited",
        display_name="Beta Homes",
        slug="beta-homes",
    )["id"]
    missing = client.get(f"{ORGANIZATIONS}{uuid4()}/")
    assert missing.status_code == 404
    assert client.patch(f"{ORGANIZATIONS}{organization_id}/", {}, format="json").status_code == 400
    phone = client.patch(
        f"{ORGANIZATIONS}{organization_id}/",
        {"contact_phone": "123"},
        format="json",
    )
    assert phone.status_code == 400
    updated = client.patch(
        f"{ORGANIZATIONS}{organization_id}/",
        {
            "contact_email": "Desk@Acme.test",
            "contact_phone": "0712345678",
            "website": "https://acme.test",
            "address_line": "Ngong Road",
            "legal_name": "Acme Properties PLC",
            "organization_type": "landlord",
            "registration_reference": "LL-9",
            "slug": "acme-homes",
            "country": "ke",
        },
        format="json",
    )
    assert updated.status_code == 200
    assert updated.json()["contact_email"] == "desk@acme.test"
    assert updated.json()["slug"] == "acme-homes"
    assert updated.json()["country"] == "KE"
    collision = client.patch(
        f"{ORGANIZATIONS}{other_id}/",
        {"slug": "acme-homes"},
        format="json",
    )
    assert collision.status_code == 409
    same_status = client.post(
        f"{ORGANIZATIONS}{organization_id}/status/",
        {"status": "active"},
        format="json",
    )
    assert same_status.status_code == 409
    stranger_status = client_for(stranger).post(
        f"{ORGANIZATIONS}{organization_id}/status/",
        {"status": "deactivated"},
        format="json",
    )
    assert stranger_status.status_code == 403
    assert _invite(client, organization_id, member.email, "member").status_code == 201
    pending = client.get(f"{ORGANIZATIONS}{organization_id}/invitations/")
    assert pending.status_code == 200
    assert pending.json()["count"] == 1
    invitation_id = pending.json()["results"][0]["id"]
    detail = client.get(f"{ORGANIZATIONS}{organization_id}/invitations/{invitation_id}/")
    assert detail.status_code == 200
    assert detail.json()["email"] == member.email
    assert "token_hash" not in detail.json()
    assert client.get(f"{ORGANIZATIONS}{organization_id}/invitations/{uuid4()}/").status_code == 404
    bad_inspect = client.post(INSPECT, {"token": "missing-token"}, format="json")
    assert bad_inspect.status_code == 400
    empty_accept = client.post(ACCEPT, {"token": " "}, format="json")
    assert empty_accept.status_code == 400
    assert _accept(client_for(member)).status_code == 200
    membership = OrganizationMembership.objects.get(user=member)
    fetched = client.get(f"{ORGANIZATIONS}{organization_id}/members/{membership.id}/")
    assert fetched.status_code == 200
    assert fetched.json()["email"] == member.email
    same_role = client.patch(
        f"{ORGANIZATIONS}{organization_id}/members/{membership.id}/",
        {"role": "member"},
        format="json",
    )
    assert same_role.status_code == 200
    owner_membership = OrganizationMembership.objects.get(
        user=owner,
        organization_id=organization_id,
    )
    self_suspend = client.post(
        f"{ORGANIZATIONS}{organization_id}/members/{owner_membership.id}/suspend/",
        format="json",
    )
    assert self_suspend.status_code == 403
    self_transfer = client.post(
        f"{ORGANIZATIONS}{organization_id}/ownership-transfer/",
        {"membership_id": str(owner_membership.id)},
        format="json",
    )
    assert self_transfer.status_code == 400
    removed = client.post(
        f"{ORGANIZATIONS}{organization_id}/members/{membership.id}/remove/",
        format="json",
    )
    assert removed.status_code == 200
    hidden = client.get(f"{ORGANIZATIONS}{organization_id}/members/{membership.id}/")
    assert hidden.status_code == 404
    reactivate = client.post(
        f"{ORGANIZATIONS}{organization_id}/members/{membership.id}/reactivate/",
        format="json",
    )
    assert reactivate.status_code == 409
