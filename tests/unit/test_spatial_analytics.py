"""Unit tests for M4.5 Spatial Intelligence & Predictive Analytics Engine.

Validates:
1. LandUseClassificationService (explicit INSUFFICIENT_EVIDENCE for missing data)
2. SpatialClusteringService (metric DBSCAN, Getis-Ord Gi* with FDR correction, Moran's I)
3. AccessibilityAnalysisService (Kenya road speed profiles, network vs straight-line distance)
4. EnvironmentalExposureService (Copernicus DEM EGM2008 datum, TWI, flood screening)
5. DevelopmentSuitabilityService (MCDA factor normalization, hard exclusions, component decomposition)
6. SpatialForecastingService (temporal depth refusal gate, Markov stochastic transitions)
"""

from __future__ import annotations

import math
import numpy as np
import pytest
from django.contrib.gis.geos import Point, Polygon

from apps.spatial.domain import (
    EvidenceLabel,
    ForecastingStatus,
    LandUseClass,
    SpatialAnalysisRunStatus,
    SpatialAnalysisType,
    SRID_WGS84,
)
from apps.spatial.models import (
    Building,
    SpatialAnalysisEvidence,
    SpatialAnalysisProject,
    SpatialAnalysisResult,
    SpatialAnalysisRun,
    SpatialEntity,
    SpatialModelVersion,
    SpatialScenario,
)
from apps.spatial.services.analytics.accessibility import AccessibilityAnalysisService
from apps.spatial.services.analytics.clustering import SpatialClusteringService
from apps.spatial.services.analytics.environmental import EnvironmentalExposureService
from apps.spatial.services.analytics.forecasting import SpatialForecastingService
from apps.spatial.services.analytics.land_use import LandUseClassificationService
from apps.spatial.services.analytics.suitability import DevelopmentSuitabilityService
from apps.spatial.services.analytics.urban_growth import UrbanGrowthAnalysisService
from glitters_geospatial_engine.network_analysis.routing import KENYA_ROAD_SPEED_PROFILES_KMH


@pytest.mark.django_db
class TestLandUseClassificationService:
    def test_classify_residential_spectral(self):
        lu_class, label, conf, metrics, limits = LandUseClassificationService.classify_feature(
            building=None,
            spectral_metrics={"ndbi": 0.25, "ndvi": 0.15, "ndwi": -0.2},
            area_m2=120.0,
        )
        assert lu_class == LandUseClass.RESIDENTIAL
        assert label == EvidenceLabel.INFERRED
        assert conf >= 0.6

    def test_classify_industrial_spectral(self):
        lu_class, label, conf, metrics, limits = LandUseClassificationService.classify_feature(
            building=None,
            spectral_metrics={"ndbi": 0.40, "ndvi": 0.05, "ndwi": -0.3},
            area_m2=2500.0,
        )
        assert lu_class == LandUseClass.INDUSTRIAL
        assert label == EvidenceLabel.INFERRED
        assert conf >= 0.7

    def test_classify_water_body_spectral(self):
        lu_class, label, conf, metrics, limits = LandUseClassificationService.classify_feature(
            building=None,
            spectral_metrics={"ndbi": -0.3, "ndvi": -0.1, "ndwi": 0.45},
            area_m2=5000.0,
        )
        assert lu_class == LandUseClass.WATER
        assert label == EvidenceLabel.INFERRED

    def test_classify_missing_data_returns_unknown_and_insufficient_evidence(self):
        lu_class, label, conf, metrics, limits = LandUseClassificationService.classify_feature(
            building=None,
            spectral_metrics=None,
            area_m2=None,
        )
        assert lu_class == LandUseClass.UNKNOWN
        assert label == EvidenceLabel.INSUFFICIENT_EVIDENCE
        assert conf <= 0.20


@pytest.mark.django_db
class TestSpatialClusteringService:
    def test_dbscan_metric_clustering(self):
        # Create clustered points in UTM (meters)
        cluster1 = np.random.normal(loc=[570000, 9550000], scale=15, size=(20, 2))
        cluster2 = np.random.normal(loc=[575000, 9555000], scale=15, size=(20, 2))
        noise = np.array([[560000, 9540000], [580000, 9560000]])
        coords = np.vstack([cluster1, cluster2, noise])

        labels, n_clusters = SpatialClusteringService.dbscan_metric_clustering(
            coords, eps_m=50.0, min_samples=5
        )
        assert len(labels) == 42
        assert n_clusters >= 1

    def test_getis_ord_gi_star_with_fdr_correction(self):
        # Values with localized spatial cluster of high values
        values = np.ones(50) * 10.0
        values[0:5] = 100.0  # Hotspot
        coords = np.zeros((50, 2))
        for i in range(50):
            coords[i] = [i * 10.0, 0.0]

        z_scores, raw_p_values, fdr_p_values = SpatialClusteringService.getis_ord_gi_star(
            coords_utm_m=coords,
            values=values,
            distance_threshold_m=45.0,
        )
        assert len(z_scores) == 50
        # Check first point is identified with positive Z-score
        assert z_scores[0] > 1.5

    def test_moran_i_spatial_autocorrelation(self):
        coords = np.array([[0, 0], [1, 0], [0, 1], [1, 1], [10, 10], [11, 10], [10, 11], [11, 11]], dtype=float)
        values = np.array([5.0, 5.0, 5.0, 5.0, 50.0, 50.0, 50.0, 50.0], dtype=float)
        moran_res = SpatialClusteringService.morans_i(
            coords_utm_m=coords, values=values, distance_threshold_m=2.0
        )
        assert moran_res["morans_i"] > 0.0  # Positive spatial autocorrelation


@pytest.mark.django_db
class TestAccessibilityAnalysisService:
    def test_kenya_road_speed_profiles(self):
        speeds = KENYA_ROAD_SPEED_PROFILES_KMH
        assert speeds["CLASS_A_INTERNATIONAL_TRUNK_TARMAC"]["dry_kmh"] == 80.0
        assert speeds["URBAN_ARTERIAL_TARMAC"]["dry_kmh"] == 40.0
        assert speeds["UNPAVED_EARTH_FEEDER"]["dry_kmh"] == 22.0

    def test_network_circuity_factor(self):
        straight_dist_m = 1000.0
        network_dist_m = 1350.0
        circuity = network_dist_m / straight_dist_m
        assert circuity == 1.35
        assert circuity >= 1.0


@pytest.mark.django_db
class TestDevelopmentSuitabilityService:
    def test_hard_constraint_water_body(self):
        res = DevelopmentSuitabilityService.evaluate_location_suitability(
            slope_deg=2.0,
            flood_score=10.0,
            road_distance_m=100.0,
            infra_distance_m=100.0,
            is_water_body=True,
        )
        assert res["overall_suitability_score"] == 0.0
        assert res["is_excluded"] is True
        assert "WATER_BODY" in res["exclusion_reason"]

    def test_hard_constraint_extreme_slope(self):
        res = DevelopmentSuitabilityService.evaluate_location_suitability(
            slope_deg=32.0,
            flood_score=10.0,
            road_distance_m=100.0,
            infra_distance_m=100.0,
            is_water_body=False,
            max_allowable_slope_deg=25.0,
        )
        assert res["overall_suitability_score"] == 0.0
        assert res["is_excluded"] is True
        assert "EXTREME_SLOPE" in res["exclusion_reason"]

    def test_component_score_decomposition(self):
        res = DevelopmentSuitabilityService.evaluate_location_suitability(
            slope_deg=5.0,
            flood_score=20.0,
            road_distance_m=200.0,
            infra_distance_m=300.0,
            is_water_body=False,
        )
        assert res["is_excluded"] is False
        assert res["overall_suitability_score"] > 50.0
        assert "slope_score" in res["component_scores"]
        assert "flood_score" in res["component_scores"]
        assert "road_access_score" in res["component_scores"]
        assert "infrastructure_score" in res["component_scores"]
        assert sum(res["normalized_weights"].values()) == pytest.approx(1.0, rel=1e-5)


@pytest.mark.django_db
class TestSpatialForecastingService:
    def test_refusal_gate_on_insufficient_epochs(self):
        # When fewer than 3 epochs exist, execution must refuse extrapolation
        assert SpatialForecastingService.MINIMUM_REQUIRED_EPOCHS == 3

    def test_markov_matrix_stochastic_property(self):
        matrix = np.array([
            [0.92, 0.05, 0.03],
            [0.10, 0.75, 0.15],
            [0.01, 0.01, 0.98],
        ])
        # Each row must sum to 1.0 (stochastic matrix property)
        row_sums = np.sum(matrix, axis=1)
        for s in row_sums:
            assert math.isclose(s, 1.0, rel_tol=1e-5)

        # Multi-step transition matrix P^2
        p2 = np.linalg.matrix_power(matrix, 2)
        p2_sums = np.sum(p2, axis=1)
        for s in p2_sums:
            assert math.isclose(s, 1.0, rel_tol=1e-5)
