"""Integration tests for Change Detection REST APIs."""

import datetime
import pytest
from django.contrib.gis.geos import MultiPolygon, Polygon
from rest_framework.test import APIClient

from apps.organizations.models import Organization
from apps.spatial.domain import (
    BuildingAssociationType,
    ChangeCandidateStatus,
    ChangeCategory,
    ChangeProjectStatus,
    ChangeRunStatus,
    ImageryDatasetType,
    ReviewDecision,
)
from apps.spatial.models import (
    ChangeCandidate,
    ChangeDetectionProject,
    ChangeDetectionRun,
    ChangeEvidence,
    ImageryCollection,
    ImageryItem,
)


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def setup_change_data(db):
    """Sets up an Organization, Change Project, Run, Candidate, and Evidence."""
    org_a = Organization.objects.create(
        legal_name="County Government of Mombasa",
        display_name="County Government of Mombasa",
        slug="county-mombasa",
        organization_type="agency",
    )
    org_b = Organization.objects.create(
        legal_name="Kwale County Lands Registry",
        display_name="Kwale County Lands",
        slug="kwale-lands",
        organization_type="agency",
    )

    poly = Polygon(((39.6, -4.1), (39.7, -4.1), (39.7, -4.0), (39.6, -4.0), (39.6, -4.1)))
    mpoly = MultiPolygon([poly])

    coll = ImageryCollection.objects.create(
        collection_id="sentinel-2-l2a-test",
        title="Sentinel-2 Level-2A",
        provider="ESA / Copernicus",
        dataset_type=ImageryDatasetType.OPTICAL_MULTISPECTRAL,
        spatial_extent=poly,
        time_start=datetime.datetime(2026, 1, 1, 0, 0, tzinfo=datetime.timezone.utc),
        native_crs="EPSG:32737",
        gsd_m=10.0,
    )
    item_base = ImageryItem.objects.create(
        collection=coll,
        item_id="S2B_TEST_20260213",
        canonical_urn="urn:glitters:imagery:sentinel2:S2B_TEST_20260213",
        acquisition_datetime=datetime.datetime(2026, 2, 13, 7, 39, 44, tzinfo=datetime.timezone.utc),
        footprint=poly,
        native_crs="EPSG:32737",
        cloud_coverage_percentage=2.9,
    )
    item_comp = ImageryItem.objects.create(
        collection=coll,
        item_id="S2A_TEST_20260521",
        canonical_urn="urn:glitters:imagery:sentinel2:S2A_TEST_20260521",
        acquisition_datetime=datetime.datetime(2026, 5, 21, 7, 39, 44, tzinfo=datetime.timezone.utc),
        footprint=poly,
        native_crs="EPSG:32737",
        cloud_coverage_percentage=3.1,
    )

    project = ChangeDetectionProject.objects.create(
        organization=org_a,
        name="Mombasa Island Urban Change Monitoring",
        status=ChangeProjectStatus.ACTIVE,
        aoi_geometry=poly,
    )

    run = ChangeDetectionRun.objects.create(
        project=project,
        baseline_item=item_base,
        comparison_item=item_comp,
        algorithm_identifier="CVA_SPECTRAL_VECTOR",
        algorithm_version="1.0.0",
        status=ChangeRunStatus.COMPLETED,
        total_candidates_detected=1,
        processing_duration_sec=0.65,
        parameters={"cva_threshold": 0.25},
    )

    candidate = ChangeCandidate.objects.create(
        run=run,
        candidate_id="CHG_TEST_001",
        category=ChangeCategory.LAND_COVER_CHANGE,
        review_status=ChangeCandidateStatus.POTENTIAL,
        detection_confidence=0.88,
        area_sqm=12500.0,
        building_association_type=BuildingAssociationType.ISOLATED_UNMAPPED,
        geometry=poly,
        delta_ndvi=0.29,
        change_magnitude=0.38,
    )

    evidence = ChangeEvidence.objects.create(
        candidate=candidate,
        evidence_key="CVA_SPECTRAL_VECTOR",
        metrics_payload={"ndvi_delta": 0.29, "cva_mag": 0.38},
    )

    return {
        "org_a": org_a,
        "org_b": org_b,
        "project": project,
        "run": run,
        "candidate": candidate,
        "evidence": evidence,
    }


def test_list_change_projects(api_client, setup_change_data):
    """Test retrieving change detection projects."""
    res = api_client.get("/api/v1/spatial/change-detection/projects/")
    assert res.status_code == 200
    data = res.json()
    items = data["results"] if isinstance(data, dict) and "results" in data else data
    assert len(items) >= 1
    assert any(p["name"] == "Mombasa Island Urban Change Monitoring" for p in items)


def test_list_change_runs(api_client, setup_change_data):
    """Test retrieving change detection runs."""
    res = api_client.get("/api/v1/spatial/change-detection/runs/")
    assert res.status_code == 200
    data = res.json()
    items = data["results"] if isinstance(data, dict) and "results" in data else data
    assert len(items) >= 1
    assert any(r["total_candidates_detected"] >= 1 for r in items)


def test_change_candidates_geojson(api_client, setup_change_data):
    """Test retrieving change candidates in GeoJSON FeatureCollection format."""
    res = api_client.get("/api/v1/spatial/change-detection/candidates/geojson/")
    assert res.status_code == 200
    geojson = res.json()
    assert geojson["type"] == "FeatureCollection"
    assert len(geojson["features"]) >= 1
    feat = geojson["features"][0]
    assert feat["type"] == "Feature"
    assert "geometry" in feat
    assert "properties" in feat
    props = feat["properties"]
    assert props["candidate_id"] == "CHG_TEST_001"
    assert props["category"] == ChangeCategory.LAND_COVER_CHANGE
    assert props["confidence"] == 0.88


def test_candidate_evidence_endpoint(api_client, setup_change_data):
    """Test retrieving evidence artifacts for a candidate."""
    cand = setup_change_data["candidate"]
    res = api_client.get(f"/api/v1/spatial/change-detection/candidates/{cand.id}/evidence/")
    assert res.status_code == 200
    data = res.json()
    assert len(data) >= 1
    assert data[0]["evidence_key"] == "CVA_SPECTRAL_VECTOR"


def test_candidate_review_submission(api_client, setup_change_data):
    """Test human observation review decision submission transitions to HUMAN_REVIEWED, not VERIFIED."""
    cand = setup_change_data["candidate"]
    payload = {
        "decision": ReviewDecision.CONFIRM_OBSERVATION,
        "notes": "Verified clearing via engineering inspection.",
    }
    res = api_client.post(
        f"/api/v1/spatial/change-detection/candidates/{cand.id}/review/",
        data=payload,
        format="json",
    )
    assert res.status_code == 200
    resp_data = res.json()
    assert resp_data["candidate_status"] == ChangeCandidateStatus.HUMAN_REVIEWED
    assert resp_data["review"]["decision"] == ReviewDecision.CONFIRM_OBSERVATION
    assert resp_data["review"]["previous_status"] == ChangeCandidateStatus.POTENTIAL
    assert resp_data["review"]["new_status"] == ChangeCandidateStatus.HUMAN_REVIEWED

    # Re-fetch candidate to verify state persistence
    cand.refresh_from_db()
    assert cand.review_status == ChangeCandidateStatus.HUMAN_REVIEWED


def test_candidate_field_verification_lifecycle(api_client, setup_change_data):
    """Test explicit field verification transitions candidate to VERIFIED."""
    cand = setup_change_data["candidate"]
    # First transition to HUMAN_REVIEWED
    res1 = api_client.post(
        f"/api/v1/spatial/change-detection/candidates/{cand.id}/review/",
        data={"decision": ReviewDecision.CONFIRM_OBSERVATION, "notes": "Observation confirmed."},
        format="json",
    )
    assert res1.status_code == 200
    assert res1.json()["candidate_status"] == ChangeCandidateStatus.HUMAN_REVIEWED

    # Explicit ground truth / field verification authorizes VERIFIED
    res2 = api_client.post(
        f"/api/v1/spatial/change-detection/candidates/{cand.id}/review/",
        data={"decision": ReviewDecision.CONFIRM_FIELD_VERIFICATION, "notes": "Surveyor ground truth authorized."},
        format="json",
    )
    assert res2.status_code == 200
    assert res2.json()["candidate_status"] == ChangeCandidateStatus.VERIFIED
    cand.refresh_from_db()
    assert cand.review_status == ChangeCandidateStatus.VERIFIED


def test_candidate_mark_inconclusive_lifecycle(api_client, setup_change_data):
    """Test MARK_INCONCLUSIVE transitions candidate to POTENTIAL with notes."""
    cand = setup_change_data["candidate"]
    # Move to HUMAN_REVIEWED first
    api_client.post(
        f"/api/v1/spatial/change-detection/candidates/{cand.id}/review/",
        data={"decision": ReviewDecision.CONFIRM_OBSERVATION},
        format="json",
    )
    cand.refresh_from_db()
    assert cand.review_status == ChangeCandidateStatus.HUMAN_REVIEWED

    # MARK_INCONCLUSIVE without notes is rejected
    res_no_notes = api_client.post(
        f"/api/v1/spatial/change-detection/candidates/{cand.id}/review/",
        data={"decision": ReviewDecision.MARK_INCONCLUSIVE, "notes": ""},
        format="json",
    )
    assert res_no_notes.status_code == 400

    # MARK_INCONCLUSIVE with notes transitions back to POTENTIAL
    res_inconclusive = api_client.post(
        f"/api/v1/spatial/change-detection/candidates/{cand.id}/review/",
        data={"decision": ReviewDecision.MARK_INCONCLUSIVE, "notes": "High cloud distortion requires multi-epoch review."},
        format="json",
    )
    assert res_inconclusive.status_code == 200
    assert res_inconclusive.json()["candidate_status"] == ChangeCandidateStatus.POTENTIAL
    cand.refresh_from_db()
    assert cand.review_status == ChangeCandidateStatus.POTENTIAL


def test_rejected_candidate_cannot_be_verified(api_client, setup_change_data):
    """Test that once rejected, a candidate cannot silently become verified."""
    cand = setup_change_data["candidate"]
    res_reject = api_client.post(
        f"/api/v1/spatial/change-detection/candidates/{cand.id}/review/",
        data={"decision": ReviewDecision.REJECT_CANDIDATE, "notes": "Agricultural phenology artifact."},
        format="json",
    )
    assert res_reject.status_code == 200
    assert res_reject.json()["candidate_status"] == ChangeCandidateStatus.REJECTED

    # Attempting to confirm observation on rejected candidate must fail
    res_confirm = api_client.post(
        f"/api/v1/spatial/change-detection/candidates/{cand.id}/review/",
        data={"decision": ReviewDecision.CONFIRM_OBSERVATION},
        format="json",
    )
    assert res_confirm.status_code == 400
    assert "Rejected" in res_confirm.json()["error"]

    # Attempting field verification on rejected candidate must fail
    res_verify = api_client.post(
        f"/api/v1/spatial/change-detection/candidates/{cand.id}/review/",
        data={"decision": ReviewDecision.CONFIRM_FIELD_VERIFICATION},
        format="json",
    )
    assert res_verify.status_code == 400

    cand.refresh_from_db()
    assert cand.review_status == ChangeCandidateStatus.REJECTED


def test_duplicate_review_actions_prevented(api_client, setup_change_data):
    """Ensure duplicate review actions are prevented and audit log remains append-only."""
    cand = setup_change_data["candidate"]
    # First observation review
    res1 = api_client.post(
        f"/api/v1/spatial/change-detection/candidates/{cand.id}/review/",
        data={"decision": ReviewDecision.CONFIRM_OBSERVATION},
        format="json",
    )
    assert res1.status_code == 200

    # Duplicate observation review must be rejected
    res2 = api_client.post(
        f"/api/v1/spatial/change-detection/candidates/{cand.id}/review/",
        data={"decision": ReviewDecision.CONFIRM_OBSERVATION},
        format="json",
    )
    assert res2.status_code == 400
    assert "Duplicate review action" in res2.json()["error"]


def test_candidate_review_invalid_decision(api_client, setup_change_data):
    """Test invalid review decision rejection."""
    cand = setup_change_data["candidate"]
    payload = {
        "decision": "NON_EXISTENT_DECISION",
        "notes": "Invalid review test",
    }
    res = api_client.post(
        f"/api/v1/spatial/change-detection/candidates/{cand.id}/review/",
        data=payload,
        format="json",
    )
    assert res.status_code == 400
    assert "error" in res.json()


def test_remediation_of_chg_00001(setup_change_data):
    """Verify that candidate CHG_00001 is remediated to HUMAN_REVIEWED and audit trail preserved."""
    from apps.spatial.services.change_detection import remediate_chg_00001

    run = setup_change_data["run"]
    poly = setup_change_data["candidate"].geometry

    chg_00001 = ChangeCandidate.objects.create(
        run=run,
        candidate_id="CHG_00001",
        category=ChangeCategory.LAND_COVER_CHANGE,
        geometry=poly,
        review_status=ChangeCandidateStatus.VERIFIED,
        area_sqm=250000.0,
    )
    # Add historical reviews
    chg_00001.reviews.create(
        decision=ReviewDecision.CONFIRM_OBSERVATION,
        previous_status=ChangeCandidateStatus.OBSERVED,
        new_status=ChangeCandidateStatus.VERIFIED,
        comments="Premature verification",
    )
    assert chg_00001.reviews.count() == 1

    # Execute remediation
    review = remediate_chg_00001()
    assert review is not None

    chg_00001.refresh_from_db()
    assert chg_00001.review_status == ChangeCandidateStatus.HUMAN_REVIEWED
    # Historical review was preserved + 1 new remediation entry
    assert chg_00001.reviews.count() == 2
    latest = chg_00001.reviews.first()
    assert latest.previous_status == ChangeCandidateStatus.VERIFIED
    assert latest.new_status == ChangeCandidateStatus.HUMAN_REVIEWED
    assert "250,000 m²" in latest.comments
    assert "algorithmic tile-saturation artifact" in latest.comments


def test_delta_ndvi_and_delta_ndbi_populated_on_candidate(setup_change_data):
    """Test that delta_ndvi and delta_ndbi are populated on ChangeCandidate from change."""
    from apps.spatial.services.change_detection import execute_change_detection_pipeline
    from unittest.mock import MagicMock, patch
    import numpy as np
    from shapely.geometry import Polygon as ShapelyPolygon
    from glitters_geospatial_engine.change_detection.temporal_engine import DetectedGeographicChange

    run = setup_change_data["run"]
    # Mock change with delta_ndvi and delta_ndbi
    poly_utm = ShapelyPolygon([(500000, 9500000), (500100, 9500000), (500100, 9500100), (500000, 9500100)])
    mock_change = DetectedGeographicChange(
        change_id="CHG_DELTA_TEST_001",
        change_type="NEW_CONSTRUCTION_OR_CLEARING",
        geometry_utm=poly_utm,
        utm_epsg=32737,
        area_m2=10000.0,
        pixel_count_area_m2=10000.0,
        date_t1="2026-02-13",
        date_t2="2026-05-21",
        source_imagery_t1="S2:2026-02-13",
        source_imagery_t2="S2:2026-05-21",
        detection_method="Seasonally_Detrended_Change_Vector_Analysis_CVA",
        confidence_estimate=0.92,
        uncertainty_bounds={
            "cva_magnitude_mean": 0.35,
            "delta_ndvi": -0.25,
            "delta_ndbi": 0.28,
            "radiometric_1sigma": 0.02,
        },
        applied_displacement_m=0.0,
        residual_registration_error_m=0.2,
        total_positional_uncertainty_1sigma_m=0.5,
        registration_error_m=0.2,
        evidence_reference="CVA test",
        delta_ndvi=-0.25,
        delta_ndbi=0.28,
    )

    dummy_tile = MagicMock()
    dummy_tile.data = [np.zeros((10, 10), dtype=np.float32)]

    with patch(
        "apps.spatial.services.change_detection._build_raster_tile_from_item",
        return_value=dummy_tile,
    ), patch(
        "glitters_geospatial_engine.change_detection.temporal_engine.TemporalChangeDetectionEngine.detect_changes_between_tiles",
        return_value=[mock_change],
    ):
        execute_change_detection_pipeline(run, cva_threshold=0.22, min_change_area_m2=100.0)

    cand = ChangeCandidate.objects.get(candidate_id="CHG_DELTA_TEST_001")
    assert cand.delta_ndvi == -0.25
    assert cand.delta_ndbi == 0.28
    assert cand.change_magnitude == 0.35

    evidence = cand.evidence.first()
    assert evidence is not None
    assert evidence.metrics_payload["phenology_correction_status"] == "EXPERIMENTAL_BIMODAL_HARMONIC_V1"
    assert "limitations" in evidence.metrics_payload
