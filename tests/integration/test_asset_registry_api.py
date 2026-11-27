"""Local asset registry, quotas, and derived city surfaces. No rental calls."""

import json

import pytest
from django.contrib.gis.geos import Polygon
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework.test import APIClient

from apps.identity.models import User
from apps.organizations.domain import MembershipSource, MembershipStatus, Role
from apps.organizations.models import Organization, OrganizationMembership
from apps.spatial.models import ComputeJob, GeospatialAsset, GeospatialScene, SpatialAnalysisProject
from apps.spatial.services.compute.gltf_job import canonical_bytes, triangle_fixture
from apps.spatial.services.compute.storage import file_exists
from apps.spatial.services.terrain_surface import height_grid_from_asc

ASSETS = "/api/v1/spatial/assets/"
JOBS = "/api/v1/spatial/compute/jobs/"
ASC = """ncols 3
nrows 3
xllcorner 36.80
yllcorner -1.31
cellsize 0.01
NODATA_value -9999
1700 1701 1702
1690 1691 1692
1680 1681 1682
"""


@pytest.fixture
def members(db):
    org_a = Organization.objects.create(
        legal_name="Nairobi Asset Desk",
        display_name="Nairobi Assets",
        slug="nairobi-assets",
        organization_type="agency",
    )
    org_b = Organization.objects.create(
        legal_name="Kisumu Asset Desk",
        display_name="Kisumu Assets",
        slug="kisumu-assets",
        organization_type="agency",
    )
    owner = User.objects.create_user(email="asset-owner@example.com", password="SecurePassword123!")
    other = User.objects.create_user(email="asset-other@example.com", password="SecurePassword123!")
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
    return {"owner": owner, "other": other, "org_a": org_a, "org_b": org_b}


@pytest.fixture(autouse=True)
def roots(settings, tmp_path):
    settings.GLITTERS_COMPUTE_ROOT = str(tmp_path)
    settings.GLITTERS_COMPUTE_EAGER = True
    settings.GLITTERS_GPU_RENTAL_ENABLED = False
    settings.GLITTERS_REMOTE_RENDER_ENABLED = False
    settings.GLITTERS_ASSET_QUOTA_BYTES = 64_000_000
    settings.GLITTERS_ASSET_MAX_BYTES = 8_000_000
    return tmp_path


def _client(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


def _upload(client, asset_type, payload, filename="file.bin", **extra):
    data = {"asset_type": asset_type, "file": SimpleUploadedFile(filename, payload), **extra}
    return client.post(ASSETS, data, format="multipart")


def _report():
    return json.dumps({"title": "Window note", "body": "Not a survey certificate."}).encode()


def test_quota_isolation_and_soft_delete(members):
    owner = _client(members["owner"])
    other = _client(members["other"])
    created = _upload(owner, "report", _report(), filename="note.json")
    assert created.status_code == 201
    body = created.json()
    assert body["status"] == "published"
    assert body["provenance"] == "USER-SUPPLIED"
    assert "storage_key" not in body
    assert other.get(f"{ASSETS}{body['id']}/").status_code == 404
    content = owner.get(f"{ASSETS}{body['id']}/content/")
    assert content.status_code == 200
    assert content["X-Content-SHA256"] == body["sha256"]
    assert content["Cache-Control"] == "private, no-store"
    usage = owner.get(f"{ASSETS}usage/").json()
    assert usage["used_bytes"] == body["byte_size"]
    assert usage["paid_infrastructure"] is False
    assert usage["storage"] == "local-disk"
    replaced = _upload(
        owner, "report", _report() + b" ", filename="note2.json", replaces=body["id"]
    )
    assert replaced.status_code == 201
    assert replaced.json()["version"] == 2
    assert replaced.json()["parent_id"] == body["id"]
    deleted = owner.delete(f"{ASSETS}{body['id']}/")
    assert deleted.status_code == 204
    assert owner.get(f"{ASSETS}{body['id']}/content/").status_code == 404
    after = owner.get(f"{ASSETS}usage/").json()
    assert after["deleted_count"] == 1
    assert after["used_bytes"] == replaced.json()["byte_size"]
    retained = GeospatialAsset.objects.get(pk=body["id"])
    assert retained.deleted_at is not None
    assert file_exists(str(members["org_a"].id), retained.storage_key) is True
    audit = owner.get(f"{ASSETS}audit/").json()["events"]
    assert {row["action"] for row in audit} >= {"published", "downloaded", "deleted"}
    assert all("tmp" not in row["message"] and "/" not in row["message"] for row in audit)


def test_invalid_files_are_not_stored(members, settings):
    owner = _client(members["owner"])
    settings.GLITTERS_ASSET_QUOTA_BYTES = 20
    first = _upload(owner, "report", b'{"title":"A"}', filename="a.json")
    assert first.status_code == 201
    second = _upload(owner, "report", b'{"title":"BBBBBBBBBB"}', filename="b.json")
    assert second.status_code == 400
    assert GeospatialAsset.objects.count() == 1
    refused = [
        ("pointcloud", b"LASF-demo", "cloud.las"),
        ("cog", b"not-a-tiff", "bad.tif"),
        ("gltf", b"{}", "bad.gltf"),
        ("glb", b"NOPE", "bad.glb"),
        ("dem", b"not a grid", "bad.asc"),
        ("3dtiles", b'{"asset":{}}', "tileset.json"),
        ("terrain", b'{"format":"nope"}', "grid.json"),
    ]
    for asset_type, payload, name in refused:
        response = _upload(owner, asset_type, payload, filename=name)
        assert response.status_code == 400, asset_type
    assert GeospatialAsset.objects.count() == 1


def test_storage_failure_leaves_no_row(members, monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("disk")

    monkeypatch.setattr("apps.spatial.services.asset_registry.write_bytes", boom)
    response = _upload(_client(members["owner"]), "report", _report(), filename="note.json")
    assert response.status_code == 400
    assert GeospatialAsset.objects.count() == 0
    assert "disk" not in response.json()["errors"]["detail"]


def test_project_and_scene_must_belong_to_the_organization(members):
    owner = _client(members["owner"])
    ring = ((36.8, -1.31), (36.84, -1.31), (36.84, -1.27), (36.8, -1.27), (36.8, -1.31))
    project = SpatialAnalysisProject.objects.create(
        organization=members["org_a"],
        name="Nairobi window",
        aoi_geometry=Polygon(ring),
    )
    foreign = SpatialAnalysisProject.objects.create(
        organization=members["org_b"],
        name="Other window",
        aoi_geometry=Polygon(ring),
    )
    scene = GeospatialScene.objects.create(
        organization=members["org_a"],
        created_by=members["owner"],
        name="Nairobi camera",
        place_key="nairobi",
        scene_mode="3d",
        camera_longitude="36.821900",
        camera_latitude="-1.292100",
        camera_height_m="3868.00",
        heading_deg="0",
        pitch_deg="-40",
        roll_deg="0",
        terrain_exaggeration="1.0",
    )
    good = _upload(
        owner,
        "report",
        _report(),
        filename="owned.json",
        project_id=str(project.id),
        scene_id=str(scene.id),
    )
    assert good.status_code == 201
    assert good.json()["project_id"] == str(project.id)
    assert good.json()["scene_id"] == str(scene.id)
    blocked = _upload(
        owner,
        "report",
        _report(),
        filename="foreign.json",
        project_id=str(foreign.id),
    )
    assert blocked.status_code == 400
    assert GeospatialAsset.objects.filter(analysis_project=foreign).count() == 0


def test_typed_uploads_stay_honest(members):
    owner = _client(members["owner"])
    grid = canonical_bytes(height_grid_from_asc(ASC, place="kisumu"))
    terrain = _upload(owner, "terrain", grid, filename="kisumu.json")
    assert terrain.status_code == 201
    assert terrain.json()["asset_type"] == "terrain"
    assert terrain.json()["status"] == "published"
    note = terrain.json()["source_note"].lower()
    assert "not a surveyed" in note or "not a bare-earth" in note
    cog = _upload(owner, "cog", b"II*\x00rest", filename="window.tif")
    assert cog.status_code == 201
    assert cog.json()["status"] == "stored"
    assert "not certified" in cog.json()["source_note"]
    tiles = _upload(owner, "3dtiles", b'{"asset":{"version":"1.1"}}', filename="tileset.json")
    assert tiles.status_code == 201
    assert "not a generated city" in tiles.json()["source_note"]
    gltf = _upload(
        owner,
        "gltf",
        canonical_bytes(triangle_fixture()),
        filename="triangle.gltf",
    )
    assert gltf.status_code == 201
    assert "not a reconstruction" in gltf.json()["source_note"]


def test_compute_job_publishes_a_real_height_grid(members, monkeypatch):
    monkeypatch.setattr("apps.spatial.services.compute.surfaces.fetch_place_asc", lambda place: ASC)
    owner = _client(members["owner"])
    other = _client(members["other"])
    response = owner.post(
        JOBS,
        {"provider_id": "local_cpu", "operation": "publish_terrain_surface", "place": "nairobi"},
        format="json",
    )
    assert response.status_code == 201
    body = response.json()
    assert body["state"] == "SUCCEEDED"
    assert body["external_request_count"] == 0
    assert body["actual_cost_amount"] == "0.0000"
    assert body["output_asset_type"] == "terrain"
    assert body["provenance"]["geoid_applied"] is False
    assert body["provenance"]["photogrammetry"] is False
    assert body["provenance"]["source_kind"] == "DSM"
    output = owner.get(f"{JOBS}{body['id']}/output/")
    document = json.loads(b"".join(output.streaming_content))
    assert document["format"] == "glitters-height-grid-v1"
    assert document["heights"][0] == 1700
    assert document["survey"] is False
    assert other.get(f"{JOBS}{body['id']}/output/").status_code == 404
    replay = owner.post(
        JOBS,
        {"provider_id": "local_cpu", "operation": "publish_terrain_surface", "place": "nairobi"},
        format="json",
    )
    assert replay.status_code == 200
    assert replay.json()["id"] == body["id"]
    source = GeospatialAsset.objects.get(kind="source", asset_type="dem")
    assert source.provenance == "DOWNLOADED"
    derived = GeospatialAsset.objects.get(asset_type="terrain")
    assert derived.provenance == "MODELLED"
    assert derived.status == "published"
    assert source.sha256 != derived.sha256


def test_terrain_failure_publishes_nothing(members, monkeypatch):
    monkeypatch.setattr(
        "apps.spatial.services.compute.surfaces.fetch_place_asc",
        lambda place: "not a grid",
    )
    response = _client(members["owner"]).post(
        JOBS,
        {"provider_id": "local_cpu", "operation": "publish_terrain_surface", "place": "kisumu"},
        format="json",
    )
    assert response.status_code == 201
    assert response.json()["state"] == "FAILED"
    assert response.json()["output_published"] is False
    assert GeospatialAsset.objects.count() == 0


def test_building_job_publishes_extruded_footprints(members, monkeypatch):
    square = [
        [36.82, -1.292],
        [36.821, -1.292],
        [36.821, -1.293],
        [36.82, -1.293],
        [36.82, -1.292],
    ]
    report = {
        "limitation": "OpenStreetMap features inside a small box.",
        "queried_bbox": [36.8, -1.31, 36.84, -1.27],
        "geojson": {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "kind": "building",
                        "height_m": 3.2,
                        "height_basis": "ASSUMED_SINGLE_STOREY",
                        "height_note": "3.2 m is an assumption, not a measured height.",
                        "height_measured": False,
                        "osm_id": "way/42",
                        "name": "",
                        "source": "OpenStreetMap",
                    },
                    "geometry": {"type": "Polygon", "coordinates": [square]},
                }
            ],
        },
    }
    monkeypatch.setattr(
        "apps.spatial.services.compute.surfaces.load_surroundings",
        lambda extent: report,
    )
    response = _client(members["owner"]).post(
        JOBS,
        {"provider_id": "local_cpu", "operation": "publish_building_models", "place": "kisumu"},
        format="json",
    )
    assert response.status_code == 201
    body = response.json()
    assert body["state"] == "SUCCEEDED"
    assert body["external_request_count"] == 0
    assert body["provenance"]["representation"] == "extruded-footprint"
    assert body["provenance"]["photogrammetry"] is False
    output = _client(members["owner"]).get(f"{JOBS}{body['id']}/output/")
    document = json.loads(b"".join(output.streaming_content))
    feature = document["geojson"]["features"][0]
    assert feature["properties"]["height_measured"] is False
    assert feature["properties"]["height_basis"] == "ASSUMED_SINGLE_STOREY"
    assert feature["properties"]["osm_id"] == "way/42"
    assert "not a measured" in feature["properties"]["height_note"].lower()


def test_referenced_asset_cannot_be_deleted_and_rental_stays_off(members):
    owner = _client(members["owner"])
    job = owner.post(
        JOBS,
        {"provider_id": "local_cpu", "operation": "gltf_normalize", "fixture": "triangle-v1"},
        format="json",
    )
    assert job.status_code == 201
    assert job.json()["external_request_count"] == 0
    asset_id = GeospatialAsset.objects.get(kind="source").id
    blocked = owner.delete(f"{ASSETS}{asset_id}/")
    assert blocked.status_code == 409
    assert ComputeJob.objects.get(pk=job.json()["id"]).output_asset_id is not None
    commercial = owner.post(
        JOBS,
        {"provider_id": "runpod", "operation": "publish_terrain_surface", "place": "nairobi"},
        format="json",
    )
    assert commercial.status_code == 403
    assert commercial.json()["external_request_count"] == 0
    assert GeospatialAsset.objects.filter(asset_type="terrain").count() == 0


def test_anonymous_asset_routes_are_rejected():
    assert APIClient().get(f"{ASSETS}usage/").status_code in {401, 403}
