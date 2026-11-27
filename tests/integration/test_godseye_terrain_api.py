"""Terrain and surroundings stay inside the caller's organization."""

from pathlib import Path

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.identity.models import User
from apps.organizations.domain import MembershipSource, MembershipStatus, Role
from apps.organizations.models import Organization, OrganizationMembership


@pytest.fixture
def members(db):
    org_a = Organization.objects.create(
        legal_name="Nairobi Terrain Desk",
        display_name="Nairobi Terrain",
        slug="nairobi-terrain",
        organization_type="agency",
    )
    org_b = Organization.objects.create(
        legal_name="Kisumu Terrain Desk",
        display_name="Kisumu Terrain",
        slug="kisumu-terrain",
        organization_type="agency",
    )
    owner = User.objects.create_user(
        email="terrain-owner@example.com",
        password="SecurePassword123!",
    )
    other = User.objects.create_user(
        email="terrain-other@example.com",
        password="SecurePassword123!",
    )
    now = timezone.now()
    for org, user in ((org_a, owner), (org_b, other)):
        OrganizationMembership.objects.create(
            organization=org,
            user=user,
            role=Role.OWNER,
            status=MembershipStatus.ACTIVE,
            source=MembershipSource.CREATOR,
            joined_at=now,
        )
    return {"owner": owner, "other": other}


def _client(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


def _profile(client):
    return client.post(
        "/api/v1/spatial/land-intelligence/profiles/",
        {
            "name": "Terrain sketch",
            "boundary_class": "user_drawn",
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [36.821, -1.293],
                        [36.822, -1.293],
                        [36.822, -1.292],
                        [36.821, -1.292],
                        [36.821, -1.293],
                    ]
                ],
            },
        },
        format="json",
    )


@pytest.mark.django_db
def test_terrain_and_surroundings_are_org_scoped(members, monkeypatch, tmp_path):
    grid = tmp_path / "window.asc"
    grid.write_text(
        "ncols 2\nnrows 2\nxllcorner 36.82\nyllcorner -1.293\ncellsize 0.0003\n"
        "1660 1662\n1661 1664\n",
        encoding="utf-8",
    )

    def fake_translate(url, window, destination: Path):
        assert url.startswith("/vsicurl/https://copernicus-dem-30m.s3.eu-central-1.amazonaws.com/")
        assert "evil" not in url
        destination.write_text(grid.read_text(encoding="utf-8"), encoding="utf-8")

    def fake_fetch(url, **kwargs):
        assert "overpass-api.de" in kwargs["allowed_hosts"]
        return 200, b'{"elements":[]}'

    monkeypatch.setattr("apps.spatial.services.terrain.translate_window", fake_translate)
    monkeypatch.setattr("apps.spatial.services.surroundings.fetch_https", fake_fetch)
    monkeypatch.setattr(
        "apps.spatial.services.surroundings._cache_file",
        lambda key: tmp_path / f"osm-{key}.json",
    )

    anonymous = APIClient()
    assert anonymous.get("/api/v1/spatial/land-intelligence/profiles/").status_code == 401

    owner = _client(members["owner"])
    created = _profile(owner)
    assert created.status_code == 201
    profile_id = created.json()["id"]

    terrain = owner.get(f"/api/v1/spatial/land-intelligence/profiles/{profile_id}/terrain/")
    assert terrain.status_code == 200
    body = terrain.json()
    assert body["downloaded"] is True
    assert body["tile_id"] == "Copernicus_DSM_COG_10_S02_00_E036_00_DEM"
    assert body["source_kind"] == "DSM"
    assert body["elevation_min_m"] == 1660
    assert body["land_cover"] is None
    assert "not a bare-earth survey" in body["limitation"].lower()
    assert body["transect"]
    assert {point["elevation_m"] for point in body["transect"]} <= {1660, 1661, 1662, 1664}
    assert "not a surveyed profile" in body["transect_note"].lower()

    context = owner.get(f"/api/v1/spatial/land-intelligence/profiles/{profile_id}/surroundings/")
    assert context.status_code == 200
    assert context.json()["building_count"] == 0
    assert context.json()["license"] == "ODbL-1.0"
    assert context.json()["land_cover"] is None

    other = _client(members["other"])
    hidden = f"/api/v1/spatial/land-intelligence/profiles/{profile_id}/terrain/"
    assert other.get(hidden).status_code == 404
    assert (
        other.get(
            f"/api/v1/spatial/land-intelligence/profiles/{profile_id}/surroundings/"
        ).status_code
        == 404
    )
    assert anonymous.get(hidden).status_code == 401
