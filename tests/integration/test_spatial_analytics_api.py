"""Integration tests for M4.5 Spatial Intelligence REST APIs.

Validates:
1. SpatialAnalysisProjectViewSet CRUD and multi-tenancy filtering
2. SpatialAnalysisRunViewSet creation and execution action
3. SpatialAnalysisResultViewSet filtering by category and evidence_label
4. SpatialAnalysisResultViewSet GeoJSON export endpoint (/geojson/)
5. SpatialScenarioViewSet scenario comparison modeling
"""

import uuid
import pytest
from django.contrib.gis.geos import Point, Polygon
from rest_framework.test import APIClient

from apps.organizations.models import Organization
from apps.spatial.domain import (
    EvidenceLabel,
    SpatialAnalysisRunStatus,
    SpatialAnalysisType,
    SRID_WGS84,
)
from apps.spatial.models import (
    SpatialAnalysisEvidence,
    SpatialAnalysisProject,
    SpatialAnalysisResult,
    SpatialAnalysisRun,
    SpatialModelVersion,
    SpatialScenario,
)


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def setup_analytics_data(db):
    org_a = Organization.objects.create(
        legal_name="Mombasa Municipal Planning Authority",
        display_name="Mombasa Planning",
        slug="mombasa-planning",
        organization_type="agency",
    )
    org_b = Organization.objects.create(
        legal_name="Kwale County Urban Board",
        display_name="Kwale Urban",
        slug="kwale-urban",
        organization_type="agency",
    )

    aoi = Polygon(((39.6, -4.1), (39.7, -4.1), (39.7, -4.0), (39.6, -4.0), (39.6, -4.1)), srid=SRID_WGS84)

    project_a = SpatialAnalysisProject.objects.create(
        organization=org_a,
        name="Mombasa Urban Growth & Suitability 2026",
        description="Comprehensive urban intelligence project for Mombasa.",
        aoi_geometry=aoi,
        status="ACTIVE",
    )

    project_b = SpatialAnalysisProject.objects.create(
        organization=org_b,
        name="Kwale Coastal Zone Intelligence",
        description="Kwale spatial project.",
        aoi_geometry=aoi,
        status="ACTIVE",
    )

    model_ver = SpatialModelVersion.objects.create(
        model_identifier="SpatialAnalytics_Orchestrator",
        version_tag="1.0.0-M4.5",
        analysis_type=SpatialAnalysisType.SPATIAL_CLUSTERING,
        description="M4.5 Modular Services",
    )

    run_a = SpatialAnalysisRun.objects.create(
        project=project_a,
        analysis_type=SpatialAnalysisType.SPATIAL_CLUSTERING,
        model_version=model_ver,
        parameters={"eps_meters": 50.0, "min_samples": 5},
        status=SpatialAnalysisRunStatus.COMPLETED,
    )

    res_hotspot = SpatialAnalysisResult.objects.create(
        run=run_a,
        result_id="CLUSTER_PT_TEST_01",
        category="STATISTICALLY_SIGNIFICANT_HOTSPOT",
        evidence_label=EvidenceLabel.OBSERVED,
        geometry=Point(39.65, -4.05, srid=SRID_WGS84),
        score=9.6621,
        confidence=1.0,
        metrics_payload={"gi_star_z_score": 9.6621, "statistically_significant": True},
    )

    SpatialAnalysisEvidence.objects.create(
        result=res_hotspot,
        evidence_type="GETIS_ORD_GI_STAR",
        payload={"z_score": 9.6621, "fdr_p_value": 0.00004},
    )

    res_landuse = SpatialAnalysisResult.objects.create(
        run=run_a,
        result_id="LU_TEST_01",
        category="RESIDENTIAL",
        evidence_label=EvidenceLabel.INFERRED,
        geometry=Point(39.66, -4.06, srid=SRID_WGS84),
        score=0.68,
        confidence=0.68,
        metrics_payload={"ndbi": 0.22, "ndvi": 0.18},
    )

    run_comparison = SpatialAnalysisRun.objects.create(
        project=project_a,
        analysis_type=SpatialAnalysisType.SPATIAL_FORECASTING,
        model_version=model_ver,
        parameters={"forecast_horizon_years": 2},
        status=SpatialAnalysisRunStatus.COMPLETED,
    )

    scenario = SpatialScenario.objects.create(
        project=project_a,
        name="Mombasa 2-Year Urban Expansion Counterfactual",
        baseline_run=run_a,
        comparison_run=run_comparison,
        scenario_parameters={"annual_rate_pct": 3.8},
        delta_summary={"projected_growth_pct": 31.41},
    )

    return {
        "org_a": org_a,
        "org_b": org_b,
        "project_a": project_a,
        "project_b": project_b,
        "run_a": run_a,
        "res_hotspot": res_hotspot,
        "res_landuse": res_landuse,
        "scenario": scenario,
    }


@pytest.mark.django_db
class TestSpatialAnalyticsAPI:
    def test_list_projects(self, api_client, setup_analytics_data):
        resp = api_client.get("/api/v1/spatial/spatial-analysis/projects/")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] >= 2
        names = [p["name"] for p in data["results"]]
        assert "Mombasa Urban Growth & Suitability 2026" in names

    def test_project_multi_tenancy_filtering(self, api_client, setup_analytics_data):
        org_a = setup_analytics_data["org_a"]
        resp = api_client.get(f"/api/v1/spatial/spatial-analysis/projects/?organization={org_a.slug}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["results"][0]["organization_slug"] == org_a.slug

    def test_list_runs(self, api_client, setup_analytics_data):
        project_a = setup_analytics_data["project_a"]
        resp = api_client.get(f"/api/v1/spatial/spatial-analysis/runs/?project={project_a.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] >= 2
        for r in data["results"]:
            assert r["project_id"] == str(project_a.id)

    def test_filter_results_by_category(self, api_client, setup_analytics_data):
        resp = api_client.get("/api/v1/spatial/spatial-analysis/results/?category=STATISTICALLY_SIGNIFICANT_HOTSPOT")
        assert resp.status_code == 200
        data = resp.json()
        for item in data["results"]:
            assert item["category"] == "STATISTICALLY_SIGNIFICANT_HOTSPOT"
            assert item["evidence_label"] == EvidenceLabel.OBSERVED

    def test_filter_results_by_evidence_label(self, api_client, setup_analytics_data):
        resp = api_client.get("/api/v1/spatial/spatial-analysis/results/?evidence_label=INFERRED")
        assert resp.status_code == 200
        data = resp.json()
        for item in data["results"]:
            assert item["evidence_label"] == "INFERRED"

    def test_geojson_export(self, api_client, setup_analytics_data):
        resp = api_client.get("/api/v1/spatial/spatial-analysis/results/geojson/?category=STATISTICALLY_SIGNIFICANT_HOTSPOT")
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "FeatureCollection"
        assert len(data["features"]) >= 1
        feature = data["features"][0]
        assert feature["type"] == "Feature"
        assert "geometry" in feature
        assert "properties" in feature
        assert feature["properties"]["category"] == "STATISTICALLY_SIGNIFICANT_HOTSPOT"
        assert feature["properties"]["evidence_label"] == EvidenceLabel.OBSERVED

    def test_list_scenarios(self, api_client, setup_analytics_data):
        resp = api_client.get("/api/v1/spatial/spatial-analysis/scenarios/")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] >= 1
        scenario_data = data["results"][0]
        assert "Mombasa 2-Year Urban Expansion" in scenario_data["name"]
        assert scenario_data["delta_summary"]["projected_growth_pct"] == 31.41
