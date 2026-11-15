"""Inherited property grants keep their source and follow revocation."""

import pytest
from tests.identity_support import error_detail
from tests.organizations_support import ORGANIZATIONS, client_for, organization_body, verified_user
from tests.properties_support import PROPERTIES, property_body

from apps.properties.models import PropertyAccess

pytestmark = pytest.mark.django_db


def _create(client, **overrides):
    response = client.post(PROPERTIES, property_body(**overrides), format="json")
    assert response.status_code == 201, response.content
    return response.json()


def _access(client, property_id):
    body = client.get(f"{PROPERTIES}{property_id}/relationships/").json()
    return body["access"]


def test_personal_grants_record_origin_and_cascade(password) -> None:
    owner = verified_user("ada@example.com", password)
    viewer = verified_user("viewer@example.com", password)
    extra = verified_user("extra@example.com", password)
    direct = verified_user("direct@example.com", password)
    client = client_for(owner)
    estate = _create(client, name="Ridge", hierarchy_role="estate")
    creator = _access(client, estate["id"])
    assert len(creator) == 1
    assert creator[0]["origin"] == "direct"
    assert creator[0]["inherited_from"] is None
    assert creator[0]["capability"] == "manage"
    granted = client.post(
        f"{PROPERTIES}{estate['id']}/relationships/",
        {
            "subject_kind": "person",
            "user_id": str(viewer.id),
            "role": "authorized_contact",
            "access_capability": "view",
        },
        format="json",
    )
    assert granted.status_code == 201
    building = _create(
        client,
        name="Block A",
        hierarchy_role="building",
        parent_id=estate["id"],
    )
    unit = _create(
        client,
        name="A-1",
        hierarchy_role="unit",
        parent_id=building["id"],
    )
    building_access = _access(client, building["id"])
    viewer_copy = next(row for row in building_access if row["user_id"] == str(viewer.id))
    owner_copy = next(row for row in building_access if row["user_id"] == str(owner.id))
    assert viewer_copy["origin"] == "inherited"
    assert viewer_copy["inherited_from"] is not None
    assert owner_copy["origin"] == "inherited"
    parent_view = next(
        row for row in _access(client, estate["id"]) if row["user_id"] == str(viewer.id)
    )
    assert viewer_copy["inherited_from"] == parent_view["id"]
    child_direct = client.post(
        f"{PROPERTIES}{building['id']}/relationships/",
        {
            "subject_kind": "person",
            "user_id": str(direct.id),
            "role": "authorized_contact",
            "access_capability": "manage",
        },
        format="json",
    )
    assert child_direct.status_code == 201
    revoked = client.post(f"{PROPERTIES}{estate['id']}/access/{parent_view['id']}/revoke/")
    assert revoked.status_code == 200
    building_after = _access(client, building["id"])
    active_viewers = [
        row
        for row in building_after
        if row["user_id"] == str(viewer.id) and row["status"] == "active"
    ]
    assert active_viewers == []
    direct_row = next(row for row in building_after if row["user_id"] == str(direct.id))
    assert direct_row["origin"] == "direct"
    assert direct_row["status"] == "active"
    unit_viewer = PropertyAccess.objects.filter(
        property_id=unit["id"],
        user=viewer,
        status="active",
    )
    assert not unit_viewer.exists()
    owner_unit = PropertyAccess.objects.get(property_id=unit["id"], user=owner, status="active")
    assert owner_unit.origin == "inherited"
    second = client.post(
        f"{PROPERTIES}{estate['id']}/relationships/",
        {
            "subject_kind": "person",
            "user_id": str(extra.id),
            "role": "authorized_contact",
            "access_capability": "manage",
        },
        format="json",
    )
    assert second.status_code == 201
    owner_grant = next(
        row
        for row in _access(client, estate["id"])
        if row["user_id"] == str(owner.id) and row["status"] == "active"
    )
    removed = client.post(f"{PROPERTIES}{estate['id']}/access/{owner_grant['id']}/revoke/")
    assert removed.status_code == 200
    unit_managers = PropertyAccess.objects.filter(
        property_id=unit["id"],
        status="active",
        capability="manage",
    )
    assert not unit_managers.exists()
    owner_left = PropertyAccess.objects.filter(
        property_id=unit["id"],
        user=owner,
        status="active",
    )
    assert not owner_left.exists()
    last = client_for(extra)
    estate_manage = PropertyAccess.objects.get(
        property_id=estate["id"],
        user=extra,
        status="active",
    )
    refused = last.post(f"{PROPERTIES}{estate['id']}/access/{estate_manage.id}/revoke/")
    assert refused.status_code == 409
    assert error_detail(refused) == "A personal property must keep a manager."


def test_organization_children_do_not_copy_personal_grants(password) -> None:
    owner = verified_user("owner@example.com", password)
    client = client_for(owner)
    organization = client.post(ORGANIZATIONS, organization_body(), format="json")
    assert organization.status_code == 201
    organization_id = organization.json()["id"]
    estate = _create(
        client,
        management_context="organization",
        organization_id=organization_id,
        hierarchy_role="estate",
        property_type="mixed_use",
        property_subtype="mixed_development",
        name="Org estate",
    )
    unit = _create(
        client,
        management_context="organization",
        organization_id=organization_id,
        hierarchy_role="unit",
        parent_id=estate["id"],
        name="Org unit",
    )
    assert not PropertyAccess.objects.filter(property_id__in=[estate["id"], unit["id"]]).exists()
