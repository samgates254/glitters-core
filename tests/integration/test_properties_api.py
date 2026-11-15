"""Property lifecycle, hierarchy, characteristics, and parties."""

import pytest
from tests.identity_support import error_detail
from tests.organizations_support import ORGANIZATIONS, client_for, organization_body, verified_user
from tests.properties_support import PROPERTIES, address_for, kenya_chain, property_body

from apps.organizations.models import OrganizationMembership
from apps.properties.models import Property, PropertyEvent


def _create(client, **overrides):
    return client.post(PROPERTIES, property_body(**overrides), format="json")


@pytest.mark.django_db
def test_personal_property_lifecycle_and_characteristics(password) -> None:
    owner = verified_user("ada@example.com", password)
    client = client_for(owner)
    created = _create(client, description="Internal note")
    assert created.status_code == 201
    body = created.json()
    assert body["status"] == "draft"
    assert body["management_context"] == "personal"
    assert body["organization_id"] is None
    assert body["reference"].startswith("GLT-")
    assert "coordinates" not in body
    assert body["has_coordinates"] is False
    property_id = body["id"]
    _county, _sub, ward = kenya_chain()
    activated = client.patch(
        f"{PROPERTIES}{property_id}/",
        {
            "status": "active",
            "address": address_for(ward),
            "coordinates": {"latitude": -1.2921, "longitude": 36.8219},
            "characteristics": [
                {"code": "bedrooms", "value": 3},
                {"code": "floor_area_sqm", "value": "85.50"},
            ],
        },
        format="json",
    )
    assert activated.status_code == 200
    assert activated.json()["status"] == "active"
    assert activated.json()["coordinates"]["latitude"] == pytest.approx(-1.2921)
    assert activated.json()["address"]["country"] == "KE"
    characteristics = client.get(f"{PROPERTIES}{property_id}/characteristics/")
    assert characteristics.status_code == 200
    assert {row["code"] for row in characteristics.json()} == {"bedrooms", "floor_area_sqm"}
    listed = client.get(PROPERTIES)
    assert listed.status_code == 200
    assert listed.json()["count"] == 1
    assert "coordinates" not in listed.json()["results"][0]
    invalid = client.put(
        f"{PROPERTIES}{property_id}/characteristics/",
        {"characteristics": [{"code": "warehouse_area_sqm", "value": "10"}]},
        format="json",
    )
    assert invalid.status_code == 409
    bad_subtype = client.patch(
        f"{PROPERTIES}{property_id}/",
        {"property_subtype": "warehouse"},
        format="json",
    )
    assert bad_subtype.status_code == 400
    assert error_detail(bad_subtype) == "This subtype is not valid for the property type."
    archived = client.post(f"{PROPERTIES}{property_id}/archive/")
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"
    again = client.post(f"{PROPERTIES}{property_id}/archive/")
    assert again.status_code == 409
    changed = client.patch(f"{PROPERTIES}{property_id}/", {"name": "After"}, format="json")
    assert changed.status_code == 409
    active_without_address = _create(client, status="active")
    assert active_without_address.status_code == 400
    assert "request_id" in active_without_address.json()


@pytest.mark.django_db
def test_hierarchy_and_organization_property(password) -> None:
    owner = verified_user("owner@example.com", password)
    member = verified_user("member@example.com", password)
    owner_client = client_for(owner)
    created_org = owner_client.post(ORGANIZATIONS, organization_body(), format="json")
    organization_id = created_org.json()["id"]
    OrganizationMembership.objects.create(
        organization_id=organization_id,
        user=member,
        role="member",
        status="active",
        source="invitation",
        joined_at=owner.date_joined,
    )
    estate = _create(
        owner_client,
        management_context="organization",
        organization_id=organization_id,
        name="Ridge Estate",
        hierarchy_role="estate",
        property_type="mixed_use",
        property_subtype="mixed_development",
    )
    assert estate.status_code == 201
    estate_id = estate.json()["id"]
    unit_without_parent = _create(
        owner_client,
        management_context="organization",
        organization_id=organization_id,
        hierarchy_role="unit",
        name="Loose Unit",
    )
    assert unit_without_parent.status_code == 400
    building = _create(
        owner_client,
        management_context="organization",
        organization_id=organization_id,
        hierarchy_role="building",
        parent_id=estate_id,
        name="Block A",
        property_type="residential",
        property_subtype="apartment",
    )
    assert building.status_code == 201
    unit = _create(
        owner_client,
        management_context="organization",
        organization_id=organization_id,
        hierarchy_role="unit",
        parent_id=building.json()["id"],
        name="A-12",
        property_subtype="apartment",
    )
    assert unit.status_code == 201
    personal_child = _create(
        owner_client,
        hierarchy_role="unit",
        parent_id=building.json()["id"],
        name="Personal inside org",
    )
    assert personal_child.status_code == 400
    blocked = owner_client.post(f"{PROPERTIES}{estate_id}/archive/")
    assert blocked.status_code == 409
    assert error_detail(blocked) == "Archive subordinate properties first."
    member_client = client_for(member)
    denied = member_client.post(
        PROPERTIES,
        property_body(
            management_context="organization",
            organization_id=organization_id,
            hierarchy_role="standalone",
        ),
        format="json",
    )
    assert denied.status_code == 403
    visible = member_client.get(f"{PROPERTIES}{unit.json()['id']}/")
    assert visible.status_code == 200
    assert "coordinates" not in visible.json()
    archive_denied = member_client.post(f"{PROPERTIES}{unit.json()['id']}/archive/")
    assert archive_denied.status_code == 403


@pytest.mark.django_db
def test_claimed_owner_is_not_verified_and_can_be_revoked(password) -> None:
    owner = verified_user("ada@example.com", password)
    other = verified_user("ben@example.com", password)
    client = client_for(owner)
    property_id = _create(client).json()["id"]
    claimed = client.post(
        f"{PROPERTIES}{property_id}/relationships/",
        {
            "subject_kind": "person",
            "user_id": str(owner.id),
            "role": "owner",
            "access_capability": "manage",
        },
        format="json",
    )
    assert claimed.status_code == 201
    assert claimed.json()["status"] == "claimed"
    assert claimed.json()["legal_title_verified"] is False
    verified = client.patch(
        f"{PROPERTIES}{property_id}/relationships/{claimed.json()['id']}/",
        {"status": "verified"},
        format="json",
    )
    assert verified.status_code == 400
    assert error_detail(verified) == "A relationship cannot be marked verified through this API."
    pending = client.patch(
        f"{PROPERTIES}{property_id}/relationships/{claimed.json()['id']}/",
        {"status": "pending_verification"},
        format="json",
    )
    assert pending.status_code == 200
    assert pending.json()["status"] == "pending_verification"
    assert pending.json()["legal_title_verified"] is False
    grant = client.post(
        f"{PROPERTIES}{property_id}/relationships/",
        {
            "subject_kind": "person",
            "user_id": str(other.id),
            "role": "authorized_contact",
            "access_capability": "view",
        },
        format="json",
    )
    assert grant.status_code == 201
    other_client = client_for(other)
    seen = other_client.get(f"{PROPERTIES}{property_id}/")
    assert seen.status_code == 200
    assert "coordinates" not in seen.json()
    assert "access" not in other_client.get(f"{PROPERTIES}{property_id}/relationships/").json()
    edit = other_client.patch(f"{PROPERTIES}{property_id}/", {"name": "Taken"}, format="json")
    assert edit.status_code == 403
    organization_id = client.post(ORGANIZATIONS, organization_body(), format="json").json()["id"]
    org_party = client.post(
        f"{PROPERTIES}{property_id}/relationships/",
        {
            "subject_kind": "organization",
            "organization_id": organization_id,
            "role": "representative",
            "access_capability": "manage",
        },
        format="json",
    )
    assert org_party.status_code == 400
    assert "does not grant" in error_detail(org_party)
    revoked = client.post(
        f"{PROPERTIES}{property_id}/relationships/{grant.json()['id']}/revoke/",
        {"revoke_access": True},
        format="json",
    )
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"
    hidden = other_client.get(f"{PROPERTIES}{property_id}/")
    assert hidden.status_code == 404
    relationships = client.get(f"{PROPERTIES}{property_id}/relationships/")
    assert "access" in relationships.json()
    last = relationships.json()["access"]
    manage_ids = [
        row["id"] for row in last if row["status"] == "active" and row["capability"] == "manage"
    ]
    refused = client.post(f"{PROPERTIES}{property_id}/access/{manage_ids[0]}/revoke/")
    assert refused.status_code == 409
    assert error_detail(refused) == "A personal property must keep a manager."


@pytest.mark.django_db
def test_lifecycle_and_context_cannot_be_rewritten(password) -> None:
    owner = verified_user("ada@example.com", password)
    client = client_for(owner)
    _county, _sub, ward = kenya_chain(name="Lifecycle")
    created = _create(
        client,
        status="active",
        address=address_for(ward),
    )
    assert created.status_code == 201
    property_id = created.json()["id"]
    back = client.patch(f"{PROPERTIES}{property_id}/", {"status": "draft"}, format="json")
    assert back.status_code == 400
    assert error_detail(back) == "This status change is not allowed."
    archived = client.patch(f"{PROPERTIES}{property_id}/", {"status": "archived"}, format="json")
    assert archived.status_code == 400
    assert error_detail(archived) == "Archive this property with the archive operation."
    context = client.patch(
        f"{PROPERTIES}{property_id}/",
        {"management_context": "organization"},
        format="json",
    )
    assert context.status_code == 400
    assert error_detail(context) == "Management context cannot be changed."


@pytest.mark.django_db
def test_duplicate_reference_is_not_client_controlled(password) -> None:
    owner = verified_user("ada@example.com", password)
    client = client_for(owner)
    first = _create(client).json()
    second = _create(client, name="Second").json()
    assert first["reference"] != second["reference"]
    assert Property.objects.count() == 2
    event = PropertyEvent.objects.filter(property_id=first["id"], action="property.created").get()
    assert "latitude" not in event.metadata
    assert event.metadata["coordinates_updated"] is False
