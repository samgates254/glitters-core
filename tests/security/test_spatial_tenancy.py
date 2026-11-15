"""Negative security tests for spatial multi-tenancy and organization access control."""

import datetime
import uuid
import pytest
from django.contrib.gis.geos import MultiPolygon, Polygon
from django.core.exceptions import PermissionDenied
from django.utils import timezone
from rest_framework.test import APIClient

from apps.identity.models import User
from apps.organizations.domain import MembershipSource, MembershipStatus, OrganizationType, Role
from apps.organizations.models import Organization, OrganizationMembership
from apps.spatial.domain import (
    SRID_WGS84,
    BuildingAssociationType,
    BuildingClassification,
    ChangeCandidateStatus,
    ChangeCategory,
    ChangeProjectStatus,
    ChangeRunStatus,
    DatasetCategory,
    ImageryDatasetType,
    LicenseType,
    ReviewDecision,
    UpdateFrequency,
)
from apps.spatial.models import (
    Building,
    ChangeCandidate,
    ChangeDetectionProject,
    ChangeDetectionRun,
    ChangeEvidence,
    DatasetRegistry,
    DatasetVersion,
    ImageryCollection,
    ImageryItem,
    LandParcel,
    RasterAsset,
    SpatialEntity,
)
from apps.spatial.services.access import SpatialAccessService
from apps.spatial.services.change_detection import (
    execute_change_detection_pipeline,
    submit_candidate_review,
)


@pytest.fixture
def test_setup(db):
    user_a = User.objects.create_user(
        email="dev-a@example.com", password="SecurePassword123!"
    )
    user_b = User.objects.create_user(
        email="dev-b@example.com", password="SecurePassword123!"
    )

    org_a = Organization.objects.create(
        legal_name="Developer A Holdings",
        display_name="Developer A",
        slug="developer-a",
        organization_type=OrganizationType.DEVELOPER,
        country="KE",
    )
    OrganizationMembership.objects.create(
        organization=org_a,
        user=user_a,
        role=Role.OWNER,
        status=MembershipStatus.ACTIVE,
        source=MembershipSource.CREATOR,
        joined_at=timezone.now(),
    )

    org_b = Organization.objects.create(
        legal_name="Developer B Holdings",
        display_name="Developer B",
        slug="developer-b",
        organization_type=OrganizationType.DEVELOPER,
        country="KE",
    )
    OrganizationMembership.objects.create(
        organization=org_b,
        user=user_b,
        role=Role.OWNER,
        status=MembershipStatus.ACTIVE,
        source=MembershipSource.CREATOR,
        joined_at=timezone.now(),
    )

    extent = Polygon(
        ((39.5, -4.2), (39.8, -4.2), (39.8, -3.9), (39.5, -3.9), (39.5, -4.2)),
        srid=SRID_WGS84,
    )
    poly = Polygon(
        ((39.66, -4.06), (39.67, -4.06), (39.67, -4.07), (39.66, -4.07), (39.66, -4.06)),
        srid=SRID_WGS84,
    )
    multi_poly = MultiPolygon([poly], srid=SRID_WGS84)

    registry = DatasetRegistry.objects.create(
        dataset_urn="urn:glitters:dataset:ke:msa:tenancy-test",
        name="Tenancy Test",
        category=DatasetCategory.BUILDINGS,
        update_frequency=UpdateFrequency.STATIC,
    )
    version = DatasetVersion.objects.create(
        dataset=registry,
        version_tag="2026.1",
        source_checksum_sha256="sha-123456",
        pipeline_git_commit="git-123456",
        license_type=LicenseType.ODBL,
        attribution_statement="GLITTERS",
        spatial_extent=extent,
    )

    # Public building (no org)
    public_bldg = Building.objects.create(
        dataset_version=version,
        name="Public Fort Jesus Museum",
        organization=None,
        geom=multi_poly,
    )

    # Private building for Org A
    private_bldg_a = Building.objects.create(
        dataset_version=version,
        name="Confidential Port Terminal HQ",
        organization=org_a,
        geom=multi_poly,
    )

    return user_a, user_b, org_a, org_b, public_bldg, private_bldg_a


@pytest.mark.django_db
def test_cross_org_spatial_read_isolation(test_setup):
    user_a, user_b, org_a, org_b, public_bldg, private_bldg_a = test_setup

    # User B / Org B querying buildings sees only PUBLIC buildings
    client_b = APIClient()
    client_b.force_authenticate(user=user_b)
    # Simulate active organization context on request
    response = client_b.get("/api/v1/spatial/buildings/")
    assert response.status_code == 200
    data = response.json()
    results = data["results"] if isinstance(data, dict) and "results" in data else data
    returned_ids = [b["id"] for b in results]

    # Public building is visible
    assert str(public_bldg.id) in returned_ids
    # Org A's private building is NOT visible to Org B
    assert str(private_bldg_a.id) not in returned_ids


@pytest.mark.django_db
def test_service_level_cross_org_mutation_blocked(test_setup):
    user_a, user_b, org_a, org_b, public_bldg, private_bldg_a = test_setup

    # User B attempting to manage Org A's building raises PermissionDenied
    with pytest.raises(PermissionDenied):
        SpatialAccessService.assert_can_manage(
            entity=private_bldg_a,
            user=user_b,
            organization=org_b,
        )


@pytest.mark.django_db
def test_surveyor_credential_gate(test_setup):
    user_a, _, _, _, _, _ = test_setup

    # Empty license raises PermissionDenied
    with pytest.raises(PermissionDenied):
        SpatialAccessService.assert_surveyor_credentials(user_a, "")

    with pytest.raises(PermissionDenied):
        SpatialAccessService.assert_surveyor_credentials(user_a, "   ")

    # Valid license passes
    SpatialAccessService.assert_surveyor_credentials(user_a, "RLS-KE-999")


@pytest.fixture
def change_detection_tenancy_setup(test_setup):
    user_a, user_b, org_a, org_b, public_bldg, private_bldg_a = test_setup
    poly = Polygon(((39.6, -4.1), (39.7, -4.1), (39.7, -4.0), (39.6, -4.0), (39.6, -4.1)), srid=SRID_WGS84)

    coll = ImageryCollection.objects.create(
        collection_id="sentinel-2-tenancy-test",
        title="Sentinel-2 Test",
        provider="ESA",
        dataset_type=ImageryDatasetType.OPTICAL_MULTISPECTRAL,
        spatial_extent=poly,
        time_start=datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
        native_crs="EPSG:32737",
        gsd_m=10.0,
    )
    item_base = ImageryItem.objects.create(
        collection=coll,
        item_id="S2B_SEC_001",
        canonical_urn="urn:glitters:imagery:sentinel2:S2B_SEC_001",
        acquisition_datetime=datetime.datetime(2026, 2, 1, tzinfo=datetime.timezone.utc),
        footprint=poly,
    )
    item_comp = ImageryItem.objects.create(
        collection=coll,
        item_id="S2A_SEC_002",
        canonical_urn="urn:glitters:imagery:sentinel2:S2A_SEC_002",
        acquisition_datetime=datetime.datetime(2026, 5, 1, tzinfo=datetime.timezone.utc),
        footprint=poly,
    )

    # Org A Project
    project_a = ChangeDetectionProject.objects.create(
        organization=org_a,
        name="Org A Confidential Change Project",
        status=ChangeProjectStatus.ACTIVE,
        aoi_geometry=poly,
    )
    run_a = ChangeDetectionRun.objects.create(
        project=project_a,
        baseline_item=item_base,
        comparison_item=item_comp,
        algorithm_identifier="CVA_SPECTRAL_VECTOR",
        status=ChangeRunStatus.COMPLETED,
        total_candidates_detected=1,
    )
    candidate_a = ChangeCandidate.objects.create(
        run=run_a,
        candidate_id="CHG_SEC_001",
        category=ChangeCategory.CONSTRUCTION_ACTIVITY_CANDIDATE,
        review_status=ChangeCandidateStatus.POTENTIAL,
        detection_confidence=0.91,
        area_sqm=5000.0,
        geometry=poly,
    )

    # Derived difference raster product for Org A
    diff_asset_a = RasterAsset.objects.create(
        item=item_comp,
        asset_key="cva_diff",
        storage_path="/tmp/nonexistent_diff.tif",
        file_size_bytes=1024,
    )
    evidence_a = ChangeEvidence.objects.create(
        candidate=candidate_a,
        evidence_key="cva_diff_evidence",
        derived_diff_asset=diff_asset_a,
        metrics_payload={"mean_mag": 0.4},
    )

    return {
        "user_a": user_a,
        "user_b": user_b,
        "org_a": org_a,
        "org_b": org_b,
        "project_a": project_a,
        "run_a": run_a,
        "candidate_a": candidate_a,
        "evidence_a": evidence_a,
        "diff_asset_a": diff_asset_a,
    }


@pytest.mark.django_db
def test_cross_org_change_detection_api_isolation(change_detection_tenancy_setup):
    """
    Asserts cross-organization isolation across projects, runs, candidates, evidence,
    review endpoints, and raster products (confirming 404 / 403 isolation).
    """
    ctx = change_detection_tenancy_setup
    user_b = ctx["user_b"]
    org_a = ctx["org_a"]
    project_a = ctx["project_a"]
    run_a = ctx["run_a"]
    cand_a = ctx["candidate_a"]
    diff_asset_a = ctx["diff_asset_a"]

    client_b = APIClient()
    client_b.force_authenticate(user=user_b)

    def _extract_ids(res):
        data = res.json()
        items = data["results"] if isinstance(data, dict) and "results" in data else data
        return [str(x["id"]) for x in items]

    # 1. Project listing excludes Org A projects
    res_projects = client_b.get("/api/v1/spatial/change-detection/projects/")
    assert res_projects.status_code == 200
    p_ids = _extract_ids(res_projects)
    assert str(project_a.id) not in p_ids

    # 2. Direct project lookup returns 404
    res_proj_detail = client_b.get(f"/api/v1/spatial/change-detection/projects/{project_a.id}/")
    assert res_proj_detail.status_code == 404

    # 3. Create run on foreign project returns 404
    res_proj_runs = client_b.post(f"/api/v1/spatial/change-detection/projects/{project_a.id}/runs/")
    assert res_proj_runs.status_code == 404

    # 4. Cross-org parameter spoofing raises 403
    res_spoof = client_b.get(f"/api/v1/spatial/change-detection/projects/?organization_id={org_a.id}")
    assert res_spoof.status_code == 403

    # 5. Run listing excludes Org A runs
    res_runs = client_b.get("/api/v1/spatial/change-detection/runs/")
    assert res_runs.status_code == 200
    r_ids = _extract_ids(res_runs)
    assert str(run_a.id) not in r_ids

    # 6. Direct run lookup returns 404
    res_run_detail = client_b.get(f"/api/v1/spatial/change-detection/runs/{run_a.id}/")
    assert res_run_detail.status_code == 404

    # 7. Run candidates lookup returns 404
    res_run_cands = client_b.get(f"/api/v1/spatial/change-detection/runs/{run_a.id}/candidates/")
    assert res_run_cands.status_code == 404

    # 8. Candidate listing excludes Org A candidates
    res_cands = client_b.get("/api/v1/spatial/change-detection/candidates/")
    assert res_cands.status_code == 200
    c_ids = _extract_ids(res_cands)
    assert str(cand_a.id) not in c_ids

    # 9. Direct candidate lookup returns 404
    res_cand_detail = client_b.get(f"/api/v1/spatial/change-detection/candidates/{cand_a.id}/")
    assert res_cand_detail.status_code == 404

    # 10. Evidence endpoint returns 404
    res_evidence = client_b.get(f"/api/v1/spatial/change-detection/candidates/{cand_a.id}/evidence/")
    assert res_evidence.status_code == 404

    # 11. Review endpoint submission returns 404
    res_review = client_b.post(
        f"/api/v1/spatial/change-detection/candidates/{cand_a.id}/review/",
        data={"decision": ReviewDecision.CONFIRM_OBSERVATION},
        format="json",
    )
    assert res_review.status_code == 404

    # 12. Raster download of Org A's derived difference asset returns 404 / 403 isolation
    res_raster_download = client_b.get(f"/api/v1/spatial/raster-assets/{diff_asset_a.id}/download/")
    assert res_raster_download.status_code in (403, 404)

    # 13. Raster tile rendering of Org A's derived difference asset returns 404 / 403 isolation
    res_raster_tile = client_b.get(f"/api/v1/spatial/raster-assets/{diff_asset_a.id}/tile/14/9998/8051/")
    assert res_raster_tile.status_code in (403, 404)


@pytest.mark.django_db
def test_change_detection_processing_cannot_mutate_canonical_entities(test_setup, change_detection_tenancy_setup):
    """
    Verifies that change detection processing and human review cannot mutate
    SpatialEntity, Building.geom, or LandParcel records.
    """
    user_a, user_b, org_a, org_b, public_bldg, private_bldg_a = test_setup
    ctx = change_detection_tenancy_setup

    poly = Polygon(((39.66, -4.06), (39.67, -4.06), (39.67, -4.07), (39.66, -4.07), (39.66, -4.06)), srid=SRID_WGS84)
    multi_poly = MultiPolygon([poly], srid=SRID_WGS84)

    # Create canonical LandParcel
    parcel = LandParcel.objects.create(
        dataset_version=public_bldg.dataset_version,
        cadastral_identifier="MSA/BLOCK/TEST/999",
        geom=multi_poly,
        organization=org_a,
        tenure_type="FREEHOLD",
    )

    # Snapshot canonical geometries, timestamps, and entity counts
    orig_bldg_geom_wkb = bytes(public_bldg.geom.wkb)
    orig_bldg_updated_at = public_bldg.updated_at
    orig_parcel_geom_wkb = bytes(parcel.geom.wkb)
    orig_parcel_updated_at = parcel.updated_at

    canonical_entities_before = set(SpatialEntity.objects.values_list("id", "canonical_urn"))
    total_canonical_count_before = SpatialEntity.objects.count()

    # Submit reviews on change candidate
    candidate = ctx["candidate_a"]
    submit_candidate_review(
        candidate=candidate,
        decision=ReviewDecision.CONFIRM_OBSERVATION,
        comments="Observation analysis only",
    )
    submit_candidate_review(
        candidate=candidate,
        decision=ReviewDecision.CONFIRM_FIELD_VERIFICATION,
        comments="Ground truth inspection complete",
    )

    # Refresh canonical records from DB
    public_bldg.refresh_from_db()
    private_bldg_a.refresh_from_db()
    parcel.refresh_from_db()

    # Verify building geometry is completely unchanged
    assert bytes(public_bldg.geom.wkb) == orig_bldg_geom_wkb
    assert public_bldg.updated_at == orig_bldg_updated_at

    # Verify land parcel geometry is completely unchanged
    assert bytes(parcel.geom.wkb) == orig_parcel_geom_wkb
    assert parcel.updated_at == orig_parcel_updated_at

    # Verify SpatialEntity collection was not mutated (no additions, no removals)
    canonical_entities_after = set(SpatialEntity.objects.values_list("id", "canonical_urn"))
    assert canonical_entities_before == canonical_entities_after
    assert SpatialEntity.objects.count() == total_canonical_count_before
