"""Authorization boundaries for organizations."""

import threading

import pytest
from django.db import connection
from rest_framework.test import APIClient
from tests.identity_support import enable_confirmed_mfa, error_detail
from tests.organizations_support import ORGANIZATIONS, client_for, organization_body, verified_user

from apps.organizations.errors import OrganizationError
from apps.organizations.models import OrganizationMembership
from apps.organizations.services.invitations import create_invitation
from apps.organizations.services.memberships import suspend_membership, transfer_ownership

pytestmark = pytest.mark.django_db


def test_anonymous_organization_routes_require_authentication(api_client) -> None:
    response = api_client.get(ORGANIZATIONS)
    assert response.status_code == 401
    assert "request_id" in response.json()
    assert "errors" in response.json()


def test_csrf_is_required_for_organization_writes(password) -> None:
    user = verified_user("owner@example.com", password)
    client = APIClient(enforce_csrf_checks=True)
    client.force_login(user)
    response = client.post(ORGANIZATIONS, organization_body(), format="json")
    assert response.status_code == 403
    assert error_detail(response) == "CSRF verification failed."


def test_staff_without_mfa_cannot_call_the_api(password) -> None:
    staff = verified_user("staff@example.com", password, is_staff=True)
    response = client_for(staff).get(ORGANIZATIONS)
    assert response.status_code == 403
    assert error_detail(response) == "Multi-factor authentication is required."


def test_staff_viewer_does_not_become_an_organization_admin(password) -> None:
    owner = verified_user("owner@example.com", password)
    staff = verified_user("staff@example.com", password, is_staff=True)
    enable_confirmed_mfa(staff)
    created = client_for(owner).post(ORGANIZATIONS, organization_body(), format="json")
    organization_id = created.json()["id"]
    staff_membership = OrganizationMembership.objects.create(
        organization_id=organization_id,
        user=staff,
        role="viewer",
        status="active",
        source="platform",
        joined_at=owner.date_joined,
    )
    staff_client = client_for(staff)
    permissions = staff_client.get(f"{ORGANIZATIONS}{organization_id}/permissions/")
    assert permissions.status_code == 200
    assert permissions.json()["role"] == "viewer"
    assert permissions.json()["permissions"] == ["organization.view"]
    invite = staff_client.post(
        f"{ORGANIZATIONS}{organization_id}/invitations/",
        {"email": "new@example.com", "role": "member"},
        format="json",
    )
    assert invite.status_code == 403
    status = staff_client.post(
        f"{ORGANIZATIONS}{organization_id}/status/",
        {"status": "suspended"},
        format="json",
    )
    assert status.status_code == 200
    assert status.json()["status"] == "suspended"
    assert "legal_name" not in status.json()
    assert staff_membership.role == "viewer"


def test_suspended_organization_blocks_members_and_acceptance(password, locmem_email) -> None:
    from tests.identity_support import latest_mail_value

    owner = verified_user("owner@example.com", password)
    guest = verified_user("guest@example.com", password)
    staff = verified_user("staff@example.com", password, is_staff=True)
    enable_confirmed_mfa(staff)
    owner_client = client_for(owner)
    created = owner_client.post(ORGANIZATIONS, organization_body(), format="json")
    organization_id = created.json()["id"]
    invited = owner_client.post(
        f"{ORGANIZATIONS}{organization_id}/invitations/",
        {"email": guest.email, "role": "member"},
        format="json",
    )
    assert invited.status_code == 201
    token = latest_mail_value("token:")
    suspended = client_for(staff).post(
        f"{ORGANIZATIONS}{organization_id}/status/",
        {"status": "suspended"},
        format="json",
    )
    assert suspended.status_code == 200
    assert owner_client.get(ORGANIZATIONS).json()["count"] == 0
    blocked = owner_client.get(f"{ORGANIZATIONS}{organization_id}/members/")
    assert blocked.status_code == 403
    assert error_detail(blocked) == "This organization is not active."
    accept = client_for(guest).post("/api/v1/invitations/accept/", {"token": token}, format="json")
    assert accept.status_code == 403
    assert OrganizationMembership.objects.filter(user=guest, status="active").count() == 0


def test_schema_lists_organization_routes(api_client) -> None:
    response = api_client.get("/api/v1/schema/?format=json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/api/v1/organizations/" in paths
    assert "/api/v1/organizations/{organization_id}/ownership-transfer/" in paths
    assert "/api/v1/invitations/accept/" in paths


@pytest.mark.django_db(transaction=True)
def test_concurrent_ownership_transfer_keeps_one_owner(password) -> None:
    owner = verified_user("owner@example.com", password)
    first = verified_user("first@example.com", password)
    second = verified_user("second@example.com", password)
    created = client_for(owner).post(ORGANIZATIONS, organization_body(), format="json")
    organization_id = created.json()["id"]
    organization = OrganizationMembership.objects.get(user=owner).organization
    now = owner.date_joined
    first_membership = OrganizationMembership.objects.create(
        organization=organization,
        user=first,
        role="admin",
        status="active",
        source="invitation",
        joined_at=now,
    )
    second_membership = OrganizationMembership.objects.create(
        organization=organization,
        user=second,
        role="manager",
        status="active",
        source="invitation",
        joined_at=now,
    )
    results: list[int] = []

    def attempt(membership_id) -> None:
        try:
            transfer_ownership(
                actor=owner,
                organization_id=organization_id,
                membership_id=membership_id,
            )
            results.append(200)
        except OrganizationError as exc:
            results.append(exc.status_code)
        finally:
            connection.close()

    threads = [
        threading.Thread(target=attempt, args=(first_membership.id,)),
        threading.Thread(target=attempt, args=(second_membership.id,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results)[0] == 200
    assert sorted(results)[1] in {403, 409}
    owners = OrganizationMembership.objects.filter(
        organization=organization,
        role="owner",
        status="active",
    )
    assert owners.count() == 1


@pytest.mark.django_db(transaction=True)
def test_concurrent_invitation_and_suspension(password) -> None:
    owner = verified_user("owner@example.com", password)
    member = verified_user("member@example.com", password)
    created = client_for(owner).post(ORGANIZATIONS, organization_body(), format="json").json()
    organization_id = created["id"]
    membership = OrganizationMembership.objects.create(
        organization_id=organization_id,
        user=member,
        role="member",
        status="active",
        source="invitation",
        joined_at=owner.date_joined,
    )
    invite_results: list[int] = []
    suspend_results: list[int] = []

    def invite() -> None:
        try:
            create_invitation(
                actor=owner,
                organization_id=organization_id,
                email="guest@example.com",
                role="member",
            )
            invite_results.append(201)
        except OrganizationError as exc:
            invite_results.append(exc.status_code)
        finally:
            connection.close()

    def suspend() -> None:
        try:
            suspend_membership(
                actor=owner,
                organization_id=organization_id,
                membership_id=membership.id,
            )
            suspend_results.append(200)
        except OrganizationError as exc:
            suspend_results.append(exc.status_code)
        finally:
            connection.close()

    invite_threads = [threading.Thread(target=invite) for _ in range(2)]
    suspend_threads = [threading.Thread(target=suspend) for _ in range(2)]
    for thread in invite_threads + suspend_threads:
        thread.start()
    for thread in invite_threads + suspend_threads:
        thread.join()
    assert sorted(invite_results) == [201, 409]
    assert sorted(suspend_results)[0] == 200
    assert sorted(suspend_results)[1] == 409
    assert OrganizationMembership.objects.get(pk=membership.id).status == "suspended"
