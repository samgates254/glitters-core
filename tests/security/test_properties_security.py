"""Authorization, isolation, coordinate privacy, and PostGIS boundaries."""

import threading

import pytest
from django.db import connection
from rest_framework.test import APIClient
from tests.identity_support import enable_confirmed_mfa, error_detail
from tests.organizations_support import ORGANIZATIONS, client_for, organization_body, verified_user
from tests.properties_support import (
    COUNTRIES,
    PROPERTIES,
    RESOLVE,
    address_for,
    kenya_chain,
    property_body,
)

from apps.identity.domain import ACCOUNT_BLOCKED
from apps.organizations.models import OrganizationMembership
from apps.properties.errors import PropertyError
from apps.properties.filters import parse_property_filters
from apps.properties.models import PropertyEvent
from apps.properties.selectors import search_properties
from apps.properties.services.records import archive_property

pytestmark = pytest.mark.django_db


def _organization(owner, **overrides) -> str:
    created = client_for(owner).post(ORGANIZATIONS, organization_body(**overrides), format="json")
    assert created.status_code == 201
    return created.json()["id"]


def _membership(organization_id, user, role, *, status="active", joined_at=None) -> None:
    OrganizationMembership.objects.create(
        organization_id=organization_id,
        user=user,
        role=role,
        status=status,
        source="invitation",
        joined_at=joined_at,
    )


def _create(client, **overrides):
    return client.post(PROPERTIES, property_body(**overrides), format="json")


def test_anonymous_property_and_location_routes_require_authentication(api_client) -> None:
    listed = api_client.get(PROPERTIES)
    assert listed.status_code == 401
    assert "errors" in listed.json()
    assert "request_id" in listed.json()
    countries = api_client.get(COUNTRIES)
    assert countries.status_code == 401


def test_csrf_is_required_for_property_and_location_writes(password) -> None:
    user = verified_user("owner@example.com", password)
    client = APIClient(enforce_csrf_checks=True)
    client.force_login(user)
    created = client.post(PROPERTIES, property_body(), format="json")
    assert created.status_code == 403
    assert error_detail(created) == "CSRF verification failed."
    resolved = client.post(RESOLVE, {"country": "KE", "street_line": "Mpaka Road"}, format="json")
    assert resolved.status_code == 403
    assert error_detail(resolved) == "CSRF verification failed."


def test_staff_without_mfa_cannot_call_property_routes(password) -> None:
    staff = verified_user("staff@example.com", password, is_staff=True)
    response = client_for(staff).get(PROPERTIES)
    assert response.status_code == 403
    assert error_detail(response) == "Multi-factor authentication is required."


def test_staff_flag_does_not_reveal_another_persons_property(password) -> None:
    owner = verified_user("owner@example.com", password)
    staff = verified_user("staff@example.com", password, is_staff=True)
    enable_confirmed_mfa(staff)
    property_id = _create(client_for(owner)).json()["id"]
    staff_client = client_for(staff)
    hidden = staff_client.get(f"{PROPERTIES}{property_id}/")
    assert hidden.status_code == 404
    assert error_detail(hidden) == "Not found."
    assert staff_client.get(PROPERTIES).json()["count"] == 0


def test_personal_properties_stay_invisible_to_other_users(password) -> None:
    owner = verified_user("owner@example.com", password)
    other = verified_user("other@example.com", password)
    _county, _sub, ward = kenya_chain()
    created = _create(
        client_for(owner),
        status="active",
        address=address_for(ward),
        coordinates={"latitude": -1.2921, "longitude": 36.8219},
    )
    assert created.status_code == 201
    property_id = created.json()["id"]
    other_client = client_for(other)
    assert other_client.get(f"{PROPERTIES}{property_id}/").status_code == 404
    assert other_client.get(PROPERTIES).json()["count"] == 0
    patch = other_client.patch(f"{PROPERTIES}{property_id}/", {"name": "Taken"}, format="json")
    assert patch.status_code == 404
    archive = other_client.post(f"{PROPERTIES}{property_id}/archive/")
    assert archive.status_code == 404


def test_organization_isolation_and_suspended_participants(password) -> None:
    owner = verified_user("owner@example.com", password)
    member = verified_user("member@example.com", password)
    viewer = verified_user("viewer@example.com", password)
    outsider = verified_user("outsider@example.com", password)
    other_owner = verified_user("other-owner@example.com", password)
    staff = verified_user("staff@example.com", password, is_staff=True)
    enable_confirmed_mfa(staff)
    organization_id = _organization(owner)
    other_organization_id = _organization(
        other_owner,
        legal_name="Other Agency Limited",
        display_name="Other Agency",
    )
    _membership(organization_id, member, "member", joined_at=owner.date_joined)
    _membership(organization_id, viewer, "viewer", joined_at=owner.date_joined)
    owner_client = client_for(owner)
    _county, _sub, ward = kenya_chain(name="Westlands")
    created = _create(
        owner_client,
        management_context="organization",
        organization_id=organization_id,
        name="Agency House",
        status="active",
        address=address_for(ward),
        coordinates={"latitude": -1.267, "longitude": 36.811},
    )
    assert created.status_code == 201
    property_id = created.json()["id"]
    assert "latitude" in created.json()["coordinates"]

    member_client = client_for(member)
    visible = member_client.get(f"{PROPERTIES}{property_id}/")
    assert visible.status_code == 200
    assert "coordinates" not in visible.json()
    assert visible.json()["has_coordinates"] is True
    denied = member_client.patch(f"{PROPERTIES}{property_id}/", {"name": "Nope"}, format="json")
    assert denied.status_code == 403
    assert error_detail(denied) == "You do not have permission for this action."
    assert member_client.get(f"{PROPERTIES}{property_id}/audit/").status_code == 403

    viewer_client = client_for(viewer)
    assert "coordinates" not in viewer_client.get(f"{PROPERTIES}{property_id}/").json()
    assert viewer_client.post(f"{PROPERTIES}{property_id}/archive/").status_code == 403

    stranger = client_for(outsider).get(f"{PROPERTIES}{property_id}/")
    assert stranger.status_code == 404
    empty = client_for(outsider).get(f"{PROPERTIES}?organization_id={organization_id}")
    assert empty.status_code == 200
    assert empty.json()["count"] == 0
    other_property = _create(
        client_for(other_owner),
        management_context="organization",
        organization_id=other_organization_id,
        name="Other agency",
    )
    assert other_property.status_code == 201
    other_list = client_for(other_owner).get(f"{PROPERTIES}?organization_id={organization_id}")
    assert other_list.json()["count"] == 0
    assert client_for(other_owner).get(f"{PROPERTIES}{property_id}/").status_code == 404
    assert owner_client.get(f"{PROPERTIES}{other_property.json()['id']}/").status_code == 404

    membership = OrganizationMembership.objects.get(organization_id=organization_id, user=member)
    membership.status = "suspended"
    membership.save(update_fields=["status"])
    blocked = member_client.get(f"{PROPERTIES}{property_id}/")
    assert blocked.status_code == 403
    assert error_detail(blocked) == "This membership is not active."
    assert member_client.get(PROPERTIES).json()["count"] == 0

    suspended = client_for(staff).post(
        f"{ORGANIZATIONS}{organization_id}/status/",
        {"status": "suspended"},
        format="json",
    )
    assert suspended.status_code == 200
    org_blocked = owner_client.get(f"{PROPERTIES}{property_id}/")
    assert org_blocked.status_code == 403
    assert error_detail(org_blocked) == "This organization is not active."
    assert owner_client.get(PROPERTIES).json()["results"] == []


def test_explicit_grant_does_not_follow_an_organization_party(password) -> None:
    owner = verified_user("owner@example.com", password)
    colleague = verified_user("colleague@example.com", password)
    agent = verified_user("agent@example.com", password)
    organization_id = _organization(owner)
    _membership(organization_id, colleague, "member", joined_at=owner.date_joined)
    personal_id = _create(client_for(owner), name="Private house").json()["id"]
    party = client_for(owner).post(
        f"{PROPERTIES}{personal_id}/relationships/",
        {
            "subject_kind": "organization",
            "organization_id": organization_id,
            "role": "representative",
        },
        format="json",
    )
    assert party.status_code == 201
    assert party.json()["legal_title_verified"] is False
    assert client_for(colleague).get(f"{PROPERTIES}{personal_id}/").status_code == 404

    org_property = _create(
        client_for(owner),
        management_context="organization",
        organization_id=organization_id,
        name="Managed block",
        hierarchy_role="standalone",
    )
    assert org_property.status_code == 201
    org_property_id = org_property.json()["id"]
    granted = client_for(owner).post(
        f"{PROPERTIES}{org_property_id}/relationships/",
        {
            "subject_kind": "person",
            "user_id": str(agent.id),
            "role": "manager",
            "access_capability": "manage",
        },
        format="json",
    )
    assert granted.status_code == 201
    agent_client = client_for(agent)
    updated = agent_client.patch(
        f"{PROPERTIES}{org_property_id}/",
        {"name": "Managed by grant"},
        format="json",
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Managed by grant"
    create_denied = agent_client.post(
        PROPERTIES,
        property_body(
            management_context="organization",
            organization_id=organization_id,
            hierarchy_role="standalone",
        ),
        format="json",
    )
    assert create_denied.status_code == 403


def test_suspended_account_cannot_use_property_routes(password) -> None:
    user = verified_user("ada@example.com", password)
    user.status = "suspended"
    user.save(update_fields=["status"])
    response = client_for(user).get(PROPERTIES)
    assert response.status_code == 403
    assert error_detail(response) == ACCOUNT_BLOCKED


def test_invalid_coordinates_are_rejected(password) -> None:
    user = verified_user("ada@example.com", password)
    client = client_for(user)
    property_id = _create(client).json()["id"]
    latitude = client.patch(
        f"{PROPERTIES}{property_id}/",
        {"coordinates": {"latitude": 120, "longitude": 36.8}},
        format="json",
    )
    assert latitude.status_code == 400
    assert error_detail(latitude) == "Enter a latitude between -90 and 90."
    extra = client.patch(
        f"{PROPERTIES}{property_id}/",
        {"coordinates": {"latitude": -1.2, "longitude": 36.8, "srid": 3857}},
        format="json",
    )
    assert extra.status_code == 400
    assert error_detail(extra) == "Coordinates are not valid."


def test_coordinate_privacy_on_search_detail_and_audit(password) -> None:
    owner = verified_user("owner@example.com", password)
    member = verified_user("member@example.com", password)
    organization_id = _organization(owner)
    _membership(organization_id, member, "member", joined_at=owner.date_joined)
    _county, _sub, ward = kenya_chain(name="Kilimani")
    owner_client = client_for(owner)
    near = _create(
        owner_client,
        management_context="organization",
        organization_id=organization_id,
        name="Near pin",
        status="active",
        address=address_for(ward),
        coordinates={"latitude": -1.2921, "longitude": 36.8219},
    )
    assert near.status_code == 201
    far = _create(
        owner_client,
        management_context="organization",
        organization_id=organization_id,
        name="Far pin",
        status="active",
        address=address_for(ward, street_line="Thika Road"),
        coordinates={"latitude": -1.20, "longitude": 36.82},
    )
    assert far.status_code == 201
    listed = owner_client.get(PROPERTIES)
    assert listed.status_code == 200
    assert all("coordinates" not in row for row in listed.json()["results"])
    nearby = owner_client.get(f"{PROPERTIES}?near=-1.2921,36.8219&radius_m=800")
    assert nearby.status_code == 200
    names = [row["name"] for row in nearby.json()["results"]]
    assert names == ["Near pin"]
    assert "coordinates" not in nearby.json()["results"][0]
    assert nearby.json()["results"][0]["distance_m"] == 0
    member_nearby = client_for(member).get(f"{PROPERTIES}?near=-1.2921,36.8219&radius_m=800")
    assert "coordinates" not in member_nearby.json()["results"][0]
    detail = owner_client.get(f"{PROPERTIES}{near.json()['id']}/")
    assert detail.json()["coordinates"]["longitude"] == pytest.approx(36.8219)
    member_detail = client_for(member).get(f"{PROPERTIES}{near.json()['id']}/")
    assert "coordinates" not in member_detail.json()
    audit = owner_client.get(f"{PROPERTIES}{near.json()['id']}/audit/")
    assert audit.status_code == 200
    blob = str(audit.json())
    assert "latitude" not in blob
    assert "36.8219" not in blob
    assert "coordinates_updated" in blob
    stored = PropertyEvent.objects.filter(property_id=near.json()["id"])
    assert stored.exists()
    assert all("latitude" not in event.metadata for event in stored)
    assert all("longitude" not in event.metadata for event in stored)


def test_postgis_uses_indexed_distance_and_bbox(password) -> None:
    owner = verified_user("owner@example.com", password)
    client = client_for(owner)
    _county, _sub, ward = kenya_chain(name="Lavington")
    inside = _create(
        client,
        name="Inside",
        status="active",
        address=address_for(ward),
        coordinates={"latitude": -1.2800, "longitude": 36.7800},
    )
    outside = _create(
        client,
        name="Outside",
        status="active",
        address=address_for(ward, street_line="Other Road"),
        coordinates={"latitude": -1.40, "longitude": 36.90},
    )
    assert inside.status_code == 201
    assert outside.status_code == 201
    boxed = client.get(f"{PROPERTIES}?bbox=36.77,-1.29,36.79,-1.27")
    assert boxed.status_code == 200
    assert [row["name"] for row in boxed.json()["results"]] == ["Inside"]
    assert "coordinates" not in boxed.json()["results"][0]
    filters = parse_property_filters({"near": "-1.2800,36.7800", "radius_m": "1000"})
    sql = str(search_properties(owner, filters).query).upper()
    assert "ST_DWITHIN" in sql
    bbox_sql = str(
        search_properties(
            owner,
            parse_property_filters({"bbox": "36.77,-1.29,36.79,-1.27"}),
        ).query
    ).upper()
    assert "ST_INTERSECTS" in bbox_sql or "&&" in bbox_sql
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT indexdef
            FROM pg_indexes
            WHERE tablename = 'property_location'
              AND indexdef ILIKE '%gist%'
            """
        )
        indexes = [row[0].lower() for row in cursor.fetchall()]
    assert indexes
    assert any("coordinates" in indexdef for indexdef in indexes)


def test_property_list_is_paginated(password) -> None:
    user = verified_user("ada@example.com", password)
    client = client_for(user)
    for index in range(26):
        created = _create(client, name=f"House {index:02d}")
        assert created.status_code == 201
    page = client.get(f"{PROPERTIES}?page_size=25")
    assert page.status_code == 200
    body = page.json()
    assert body["count"] == 26
    assert len(body["results"]) == 25
    assert body["next"]
    second = client.get(body["next"])
    assert second.status_code == 200
    assert len(second.json()["results"]) == 1


def test_schema_lists_property_and_location_routes(api_client) -> None:
    response = api_client.get("/api/v1/schema/?format=json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/api/v1/properties/" in paths
    assert "/api/v1/properties/{property_id}/archive/" in paths
    assert "/api/v1/properties/{property_id}/characteristics/" in paths
    assert "/api/v1/properties/{property_id}/relationships/" in paths
    assert "/api/v1/properties/{property_id}/audit/" in paths
    assert "/api/v1/locations/countries/" in paths
    assert "/api/v1/locations/countries/{country}/areas/" in paths
    assert "/api/v1/locations/areas/{area_id}/" in paths
    assert "/api/v1/locations/localities/" in paths
    assert "/api/v1/locations/search/" in paths
    assert "/api/v1/locations/addresses/resolve/" in paths


@pytest.mark.django_db(transaction=True)
def test_concurrent_archive_keeps_one_success(password) -> None:
    owner = verified_user("owner@example.com", password)
    created = _create(client_for(owner))
    property_id = created.json()["id"]
    results: list[int] = []

    def attempt() -> None:
        try:
            archive_property(actor=owner, property_id=property_id)
            results.append(200)
        except PropertyError as exc:
            results.append(exc.status_code)
        finally:
            connection.close()

    threads = [threading.Thread(target=attempt), threading.Thread(target=attempt)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == [200, 409]
    from apps.properties.models import Property

    assert Property.objects.get(pk=property_id).status == "archived"
