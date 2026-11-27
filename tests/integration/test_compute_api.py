"""CPU fixture pipeline and mocked rental. No commercial call is billable."""

import http.client
import json
import urllib.request

import pytest
from django.test.utils import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.identity.models import User
from apps.organizations.domain import MembershipSource, MembershipStatus, Role
from apps.organizations.models import Organization, OrganizationMembership
from apps.spatial.models import ComputeEntitlement, ComputeJob, GeospatialAsset
from apps.spatial.services.compute.gltf_job import triangle_fixture
from apps.spatial.services.compute.storage import read_bytes

JOBS = "/api/v1/spatial/compute/jobs/"


@pytest.fixture
def members(db):
    org_a = Organization.objects.create(
        legal_name="Nairobi Compute Desk",
        display_name="Nairobi Compute",
        slug="nairobi-compute",
        organization_type="agency",
    )
    org_b = Organization.objects.create(
        legal_name="Kisumu Compute Desk",
        display_name="Kisumu Compute",
        slug="kisumu-compute",
        organization_type="agency",
    )
    owner = User.objects.create_user(
        email="compute-owner@example.com",
        password="SecurePassword123!",
    )
    other = User.objects.create_user(
        email="compute-other@example.com",
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
    return {"owner": owner, "other": other, "org_a": org_a, "org_b": org_b}


@pytest.fixture(autouse=True)
def compute_settings(settings, tmp_path):
    settings.GLITTERS_COMPUTE_ROOT = str(tmp_path)
    settings.GLITTERS_COMPUTE_EAGER = True
    settings.GLITTERS_GPU_RENTAL_ENABLED = False
    settings.GLITTERS_REMOTE_RENDER_ENABLED = False
    settings.GLITTERS_COMPUTE_MAX_BYTES = 2_000_000
    return tmp_path


def _client(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


def _refuse_network(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("external request")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    monkeypatch.setattr(http.client, "HTTPSConnection", boom)
    monkeypatch.setattr(http.client, "HTTPConnection", boom)


def _assert_private(payload):
    text = json.dumps(payload)
    assert "capability_hash" not in text
    assert "storage_key" not in text
    assert "DATABASE_URL" not in text
    assert "/home/" not in text
    assert "glitters-compute" not in text


def _post(client, **extra):
    body = {"provider_id": "local_cpu", "operation": "gltf_normalize", "fixture": "triangle-v1"}
    body.update(extra)
    return client.post(JOBS, body, format="json")


@pytest.mark.django_db
def test_cpu_fixture_publishes_a_verified_copy(members):
    anonymous = APIClient()
    assert anonymous.get(JOBS).status_code == 401
    assert anonymous.get("/api/v1/spatial/compute/dashboard/").status_code == 401

    owner = _client(members["owner"])
    created = _post(owner)
    assert created.status_code == 201, created.content
    body = created.json()
    _assert_private(body)
    assert body["state"] == "SUCCEEDED"
    assert body["progress"] == 100
    assert body["attempt"] == 1
    assert body["actual_cost_amount"] == "0.0000"
    assert body["estimated_cost_amount"] is None
    assert "not a quote" in body["estimated_cost_note"].lower()
    assert body["external_request_count"] == 0
    assert body["source_retained"] is True
    assert body["output_published"] is True
    assert body["cleanup"]["rented_instance"] is False
    assert body["cleanup"]["released"] is True
    assert body["provenance"]["reconstruction"] is False
    assert body["provenance"]["photogrammetry"] is False
    assert body["provenance"]["production_credentials_sent"] is False
    assert body["provenance"]["processing_gpu_required_to_view"] is False
    assert body["provenance"]["mesh_count"] == 1
    assert body["input_sha256"] != body["output_sha256"]

    job = ComputeJob.objects.get(pk=body["id"])
    assert job.capability_hash == ""
    source = read_bytes(str(job.organization_id), job.input_asset.storage_key)
    assert json.loads(source)["meshes"]
    output = owner.get(f"{JOBS}{body['id']}/output/")
    assert output.status_code == 200
    assert output["X-Content-SHA256"] == body["output_sha256"]
    assert output["Cache-Control"] == "private, no-store"
    published = json.loads(b"".join(output.streaming_content))
    assert published["extras"]["glitters"]["reconstruction"] is False
    assert len(published["meshes"]) == 1

    other = _client(members["other"])
    assert other.get(f"{JOBS}{body['id']}/").status_code == 404
    assert other.get(f"{JOBS}{body['id']}/output/").status_code == 404

    dashboard = owner.get("/api/v1/spatial/compute/dashboard/")
    assert dashboard.status_code == 200
    summary = dashboard.json()
    _assert_private(summary)
    assert summary["by_state"]["SUCCEEDED"] == 1
    assert summary["actual_cost"]["amount"] == "0.0000"
    assert summary["actual_cost"]["currency"] is None
    assert summary["estimated_cost"]["amount"] is None
    assert summary["actual_cost"]["external_request_count"] == 0
    assert summary["remote_render"]["enabled"] is False
    assert all(row["usable"] is False for row in summary["adapters"])
    other_dashboard = other.get("/api/v1/spatial/compute/dashboard/")
    assert other_dashboard.json()["by_state"]["SUCCEEDED"] == 0


@pytest.mark.django_db
def test_invalid_gltf_keeps_the_source_and_publishes_nothing(members):
    owner = _client(members["owner"])
    response = owner.post(
        JOBS,
        {
            "provider_id": "local_cpu",
            "operation": "gltf_normalize",
            "document": {"asset": {"version": "1.0"}},
        },
        format="json",
    )
    assert response.status_code == 201, response.content
    body = response.json()
    assert body["state"] == "FAILED"
    assert body["output_published"] is False
    assert body["source_retained"] is True
    assert body["external_request_count"] == 0
    assert body["actual_cost_amount"] == "0.0000"
    job = ComputeJob.objects.get(pk=body["id"])
    assert job.output_asset_id is None
    assert read_bytes(str(job.organization_id), job.input_asset.storage_key)
    assert owner.get(f"{JOBS}{body['id']}/output/").status_code == 404


@pytest.mark.django_db
def test_mock_rented_gpu_retries_then_publishes(members, monkeypatch):
    _refuse_network(monkeypatch)
    owner = _client(members["owner"])
    response = _post(owner, provider_id="mock_rented_gpu", simulate="fail_once")
    assert response.status_code == 201, response.content
    body = response.json()
    assert body["state"] == "SUCCEEDED"
    assert body["attempt"] == 2
    assert body["output_published"] is True
    assert body["external_request_count"] == 0
    assert body["actual_cost_amount"] == "0.0000"
    assert body["cleanup"]["rented_instance"] is False
    events = owner.get(f"{JOBS}{body['id']}/events/").json()["events"]
    assert any("Simulated rented GPU failure" in event["message"] for event in events)
    assert any("Output published" in event["message"] for event in events)


@pytest.mark.django_db
def test_mock_rented_gpu_failure_retains_the_source(members, monkeypatch):
    _refuse_network(monkeypatch)
    owner = _client(members["owner"])
    response = _post(owner, provider_id="mock_rented_gpu", simulate="fail")
    assert response.status_code == 201, response.content
    body = response.json()
    assert body["state"] == "FAILED"
    assert body["attempt"] == 2
    assert body["output_published"] is False
    assert body["source_retained"] is True
    assert body["cleanup"]["released"] is True
    assert body["cleanup"]["rented_instance"] is False
    assert body["external_request_count"] == 0
    job = ComputeJob.objects.get(pk=body["id"])
    stored = json.loads(read_bytes(str(job.organization_id), job.input_asset.storage_key))
    assert stored["asset"]["version"] == triangle_fixture()["asset"]["version"]
    derived = GeospatialAsset.objects.filter(organization=job.organization, kind="derived")
    assert derived.count() == 0


@pytest.mark.django_db
def test_commercial_providers_are_refused_even_when_entitled(members, monkeypatch, settings):
    _refuse_network(monkeypatch)
    settings.GLITTERS_GPU_RENTAL_ENABLED = True
    ComputeEntitlement.objects.create(
        organization=members["org_a"],
        provider_id="runpod",
        granted_by=members["owner"],
        note="Recorded decision. Does not open a connector.",
    )
    owner = _client(members["owner"])
    for provider_id in ("runpod", "vast", "enterprise"):
        response = _post(owner, provider_id=provider_id)
        assert response.status_code == 403, response.content
        body = response.json()
        _assert_private(body)
        assert body["state"] == "FAILED"
        assert body["external_request_count"] == 0
        assert body["error_class"] == "ProviderNotConfigured"
        assert "no billable request" in body["error_detail"].lower()
    assert GeospatialAsset.objects.count() == 0
    providers = owner.get("/api/v1/spatial/compute/providers/").json()["providers"]
    runpod = next(row for row in providers if row["id"] == "runpod")
    assert runpod["provision"] is False
    assert runpod["entitled"] is True
    assert runpod["external_request_count"] == 0


@pytest.mark.django_db
def test_missing_local_gpu_does_not_rent(members, monkeypatch):
    _refuse_network(monkeypatch)
    unavailable = {
        "status": "unavailable",
        "vram_mb": None,
        "billable": False,
        "reason": "nvidia-smi is not installed.",
    }
    monkeypatch.setattr("apps.spatial.services.compute.preflight.probe_gpu", lambda: unavailable)
    monkeypatch.setattr("apps.spatial.services.compute.runner.probe_gpu", lambda: unavailable)
    owner = _client(members["owner"])
    response = _post(owner, provider_id="local_gpu")
    assert response.status_code == 409, response.content
    body = response.json()
    assert body["provider_id"] == "local_gpu"
    assert body["state"] == "FAILED"
    assert body["error_class"] == "ComputeUnavailable"
    assert body["fallback_provider_id"] == "local_cpu"
    assert body["source_retained"] is True
    assert body["external_request_count"] == 0
    assert ComputeJob.objects.filter(provider_id__in=["runpod", "vast", "enterprise"]).count() == 0


@pytest.mark.django_db
def test_cancel_queued_job_retains_the_source(members, monkeypatch):
    monkeypatch.setattr("apps.spatial.tasks.run_compute_job.delay", lambda job_id: job_id)
    with override_settings(GLITTERS_COMPUTE_EAGER=False):
        owner = _client(members["owner"])
        response = _post(owner)
    assert response.status_code == 201, response.content
    body = response.json()
    assert body["state"] == "QUEUED"
    cancelled = owner.post(f"{JOBS}{body['id']}/cancel/")
    assert cancelled.status_code == 200, cancelled.content
    assert cancelled.json()["state"] == "CANCELLED"
    assert cancelled.json()["source_retained"] is True
    assert cancelled.json()["cleanup"]["released"] is True
    job = ComputeJob.objects.get(pk=body["id"])
    assert job.capability_hash == ""
    assert read_bytes(str(job.organization_id), job.input_asset.storage_key)
    again = owner.post(f"{JOBS}{body['id']}/cancel/")
    assert again.status_code == 409


@pytest.mark.django_db
def test_oversized_document_is_not_queued(members, settings):
    settings.GLITTERS_COMPUTE_MAX_BYTES = 64
    owner = _client(members["owner"])
    response = owner.post(
        JOBS,
        {
            "provider_id": "local_cpu",
            "operation": "gltf_normalize",
            "document": {"asset": {"version": "2.0"}, "note": "x" * 80},
        },
        format="json",
    )
    assert response.status_code == 400
    assert ComputeJob.objects.count() == 0
    assert "not queued" in response.content.decode().lower()


@pytest.mark.django_db
def test_idempotent_submit_returns_the_same_job(members):
    owner = _client(members["owner"])
    first = _post(owner, idempotency_key="fixture-once")
    second = _post(owner, idempotency_key="fixture-once")
    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert ComputeJob.objects.count() == 1
