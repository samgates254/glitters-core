"""God's Eye scene persistence, land profiles, and the provider registry."""

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.identity.models import User
from apps.organizations.domain import MembershipSource, MembershipStatus, Role
from apps.organizations.models import Organization, OrganizationMembership
from apps.spatial.models import ProviderQueryRecord


@pytest.fixture
def members(db):
    org_a = Organization.objects.create(
        legal_name="Nairobi Screening Desk",
        display_name="Nairobi Screening",
        slug="nairobi-screening",
        organization_type="agency",
    )
    org_b = Organization.objects.create(
        legal_name="Kisumu Screening Desk",
        display_name="Kisumu Screening",
        slug="kisumu-screening",
        organization_type="agency",
    )
    owner = User.objects.create_user(email="scene-owner@example.com", password="SecurePassword123!")
    other = User.objects.create_user(email="scene-other@example.com", password="SecurePassword123!")
    outsider = User.objects.create_user(
        email="scene-outsider@example.com",
        password="SecurePassword123!",
    )
    now = timezone.now()
    OrganizationMembership.objects.create(
        organization=org_a,
        user=owner,
        role=Role.OWNER,
        status=MembershipStatus.ACTIVE,
        source=MembershipSource.CREATOR,
        joined_at=now,
    )
    OrganizationMembership.objects.create(
        organization=org_b,
        user=other,
        role=Role.OWNER,
        status=MembershipStatus.ACTIVE,
        source=MembershipSource.CREATOR,
        joined_at=now,
    )
    return {"org_a": org_a, "org_b": org_b, "owner": owner, "other": other, "outsider": outsider}


def _client(user=None):
    client = APIClient()
    if user is not None:
        client.force_authenticate(user=user)
    return client


def _square(lon, lat, delta=0.001):
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [lon, lat],
                [lon + delta, lat],
                [lon + delta, lat + delta],
                [lon, lat + delta],
                [lon, lat],
            ]
        ],
    }


def _scene(place_key="nairobi", **overrides):
    payload = {
        "name": "Westlands camera",
        "place_key": place_key,
        "scene_mode": "3d",
        "camera_longitude": 36.8219,
        "camera_latitude": -1.2921,
        "camera_height_m": 3500,
        "heading_deg": 10,
        "pitch_deg": -35,
        "roll_deg": 0,
        "terrain_exaggeration": 1,
        "layer_state": [{"id": "terrain", "visible": True, "opacity": 1}],
        "bookmarks": [
            {
                "name": "Centre",
                "longitude": 36.8219,
                "latitude": -1.2921,
                "height_m": 2500,
                "heading_deg": 0,
                "pitch_deg": -40,
            }
        ],
    }
    payload.update(overrides)
    return payload


@pytest.mark.django_db
def test_anonymous_godseye_routes_are_unauthorized():
    client = _client()
    assert client.get("/api/v1/spatial/scenes/").status_code == 401
    assert client.get("/api/v1/spatial/scenes/places/").status_code == 401
    assert client.get("/api/v1/spatial/data-sources/").status_code == 401
    anonymous_profile = client.post(
        "/api/v1/spatial/land-intelligence/profiles/",
        {},
        format="json",
    )
    assert anonymous_profile.status_code == 401
    assert client.get("/api/v1/spatial/provider-queries/").status_code == 401


@pytest.mark.django_db
def test_scene_is_saved_and_hidden_from_another_organization(members):
    owner = _client(members["owner"])
    created = owner.post("/api/v1/spatial/scenes/", _scene(), format="json")
    assert created.status_code == 201
    body = created.json()
    assert body["place_key"] == "nairobi"
    assert "does not certify terrain elevation" in body["elevation_disclaimer"]
    scene_id = body["id"]

    places = owner.get("/api/v1/spatial/scenes/places/")
    assert places.status_code == 200
    zones = {place["utm_epsg"] for place in places.json()["places"]}
    assert zones == {32736, 32737}
    assert places.json()["utm_zones_differ"] is True

    patched = owner.patch(
        f"/api/v1/spatial/scenes/{scene_id}/",
        {"camera_height_m": 4200, "place_key": "kisumu"},
        format="json",
    )
    assert patched.status_code == 200
    assert patched.json()["place_key"] == "kisumu"
    assert float(patched.json()["camera_height_m"]) == 4200

    other = _client(members["other"])
    assert other.get(f"/api/v1/spatial/scenes/{scene_id}/").status_code == 404
    assert other.get("/api/v1/spatial/scenes/").json()["count"] == 0

    outsider = _client(members["outsider"])
    assert outsider.post("/api/v1/spatial/scenes/", _scene(), format="json").status_code == 403

    rejected = owner.post(
        "/api/v1/spatial/scenes/",
        _scene(layer_state=[{"id": "https://evil.example/tile", "visible": True, "opacity": 1}]),
        format="json",
    )
    assert rejected.status_code == 400


@pytest.mark.django_db
def test_land_profile_measures_nairobi_and_refuses_a_verified_class(members):
    owner = _client(members["owner"])
    created = owner.post(
        "/api/v1/spatial/land-intelligence/profiles/",
        {
            "name": "Westlands sketch",
            "boundary_class": "user_drawn",
            "source_note": "Drawn on the workstation",
            "geometry": _square(36.8210, -1.2925),
        },
        format="json",
    )
    assert created.status_code == 201
    body = created.json()
    assert body["legal_status"] == "NOT_A_REGISTERED_PARCEL"
    assert body["planning_status"] == "PLANNING STATUS NOT VERIFIED"
    assert body["geometry_evidence"] == "USER-SUPPLIED"
    assert body["measurement_evidence"] == "MODELLED"
    assert body["metric_epsg"] == 32737
    assert body["utm_zone"] == "37S"
    assert body["geojson"]["type"] == "Polygon"
    assert "evil.example" not in str(body)

    kisumu = owner.post(
        "/api/v1/spatial/land-intelligence/profiles/",
        {
            "name": "Kisumu sketch",
            "boundary_class": "imported_third_party",
            "geometry": _square(34.7680, -0.0917),
        },
        format="json",
    )
    assert kisumu.status_code == 201
    assert kisumu.json()["metric_epsg"] == 32736
    assert kisumu.json()["legal_status"] == "NOT_A_REGISTERED_PARCEL"

    verified = owner.post(
        "/api/v1/spatial/land-intelligence/profiles/",
        {
            "name": "Pretend title",
            "boundary_class": "verified_legal_parcel",
            "geometry": _square(36.8210, -1.2925),
        },
        format="json",
    )
    assert verified.status_code == 400

    other = _client(members["other"])
    assert other.get(f"/api/v1/spatial/land-intelligence/profiles/{body['id']}/").status_code == 404


@pytest.mark.django_db
def test_provider_registry_checks_without_calling_restricted_sources(members, monkeypatch):
    calls = []

    def fake_fetch(url, **kwargs):
        calls.append(url)
        if url.endswith("/collections"):
            payload = (
                b'{"collections":[{"id":"sentinel-2-c1-l2a","title":"Sentinel-2",'
                b'"license":"CC-BY-4.0"}]}'
            )
        elif url.endswith("/search"):
            payload = (
                b'{"type":"FeatureCollection","features":[{"id":"scene-1","collection":'
                b'"sentinel-2-c1-l2a","bbox":[36.8,-1.3,36.9,-1.2],"properties":'
                b'{"datetime":"2026-11-20T08:00:00Z","eo:cloud_cover":4},"assets":'
                b'{"visual":{"href":"https://example.com/visual.tif","type":"image/tiff"},'
                b'"local":{"href":"file:///tmp/secret.tif"}}}]}'
            )
        else:
            payload = b'{"type":"Catalog","links":[]}'
        return 200, payload

    monkeypatch.setattr("apps.spatial.services.providers.health.fetch_https", fake_fetch)
    monkeypatch.setattr("apps.spatial.services.providers.search.fetch_https", fake_fetch)
    owner = _client(members["owner"])
    listed = owner.get("/api/v1/spatial/data-sources/")
    assert listed.status_code == 200
    rows = {row["provider_id"]: row for row in listed.json()["results"]}
    assert rows["earth-search"]["service_status"] == "OPEN WITH CONDITIONS"
    assert rows["earth-search"]["operational_state"] == "not_checked"
    assert rows["nasa-firms"]["service_status"] == "AUTHENTICATED"
    assert rows["open-meteo"]["service_status"] == "FREE-TIER RESTRICTED"
    assert rows["ardhisasa"]["service_status"] == "UNAVAILABLE"
    assert rows["copernicus-dem"]["service_status"] == "NOT YET VERIFIED"

    firms = owner.post("/api/v1/spatial/data-sources/nasa-firms/check/", {}, format="json")
    assert firms.status_code == 200
    assert firms.json()["operational_state"] == "credentials_required"
    assert firms.json()["service_status"] == "AUTHENTICATED"
    assert calls == []

    checked = owner.post("/api/v1/spatial/data-sources/earth-search/check/", {}, format="json")
    assert checked.status_code == 200
    assert checked.json()["operational_state"] == "reachable"
    assert checked.json()["service_status"] == "OPEN WITH CONDITIONS"
    assert checked.json()["discovered_summary"]["collection_count"] == 1
    assert "sentinel-2-c1-l2a" in str(checked.json()["discovered_summary"])

    refused = owner.post(
        "/api/v1/spatial/data-sources/earth-search/catalog-search/",
        {
            "share_aoi_with_provider": False,
            "bbox": [36.80, -1.30, 36.85, -1.25],
            "collection": "sentinel-2-c1-l2a",
        },
        format="json",
    )
    assert refused.status_code == 400
    assert not any(url.endswith("/search") for url in calls)

    found = owner.post(
        "/api/v1/spatial/data-sources/earth-search/catalog-search/",
        {
            "share_aoi_with_provider": True,
            "bbox": [36.80, -1.30, 36.85, -1.25],
            "collection": "not-a-real-collection",
            "limit": 2,
        },
        format="json",
    )
    assert found.status_code == 400

    searched = owner.post(
        "/api/v1/spatial/data-sources/earth-search/catalog-search/",
        {
            "share_aoi_with_provider": True,
            "bbox": [36.80, -1.30, 36.85, -1.25],
            "collection": "sentinel-2-c1-l2a",
            "datetime": "2026-11-01/2026-11-27",
            "max_cloud_cover": 20,
            "limit": 2,
        },
        format="json",
    )
    assert searched.status_code == 200
    payload = searched.json()
    assert payload["item_count"] == 1
    assert payload["features"][0]["evidence"] == "OBSERVED"
    assert payload["features"][0]["datetime"] == "2026-11-20T08:00:00Z"
    assert payload["features"][0]["assets"] == [
        {"key": "visual", "href": "https://example.com/visual.tif", "type": "image/tiff"}
    ]
    assert "file://" not in str(payload)
    assert "scene-owner@example.com" not in str(payload)
    assert payload["not_shared"] == ["account", "organization", "parcel", "user"]

    other = _client(members["other"])
    assert other.get("/api/v1/spatial/provider-queries/").json()["count"] == 0
    audit = owner.get("/api/v1/spatial/provider-queries/")
    assert audit.status_code == 200
    assert audit.json()["count"] >= 1
    assert ProviderQueryRecord.objects.filter(organization=members["org_b"]).count() == 0
    stored = audit.json()["results"]
    assert all("visual.tif" not in str(row) for row in stored)
