"""Spatial Analytics Orchestration & Pipeline Execution Service (M4.5 Phase 9).

Dispatches analytical runs to specialized services:
- LAND_USE_CLASSIFICATION -> LandUseClassificationService
- URBAN_GROWTH_ANALYSIS -> UrbanGrowthAnalysisService
- SPATIAL_CLUSTERING -> SpatialClusteringService
- ACCESSIBILITY_ANALYSIS -> AccessibilityAnalysisService
- ENVIRONMENTAL_EXPOSURE -> EnvironmentalExposureService
- DEVELOPMENT_SUITABILITY -> DevelopmentSuitabilityService
- SPATIAL_FORECASTING -> SpatialForecastingService

Guarantees atomic execution, timing, error capture, and provenance preservation.
"""

from __future__ import annotations

import logging
import time
from typing import List

from django.db import transaction
from django.utils import timezone

from apps.spatial.domain import SpatialAnalysisRunStatus, SpatialAnalysisType
from apps.spatial.models import SpatialAnalysisResult, SpatialAnalysisRun
from apps.spatial.services.analytics.accessibility import AccessibilityAnalysisService
from apps.spatial.services.analytics.clustering import SpatialClusteringService
from apps.spatial.services.analytics.environmental import EnvironmentalExposureService
from apps.spatial.services.analytics.forecasting import SpatialForecastingService
from apps.spatial.services.analytics.land_use import LandUseClassificationService
from apps.spatial.services.analytics.suitability import DevelopmentSuitabilityService
from apps.spatial.services.analytics.urban_growth import UrbanGrowthAnalysisService

logger = logging.getLogger(__name__)


def execute_spatial_analysis_run(run: SpatialAnalysisRun) -> SpatialAnalysisRun:
    """Executes the pipeline for a given SpatialAnalysisRun."""
    run.status = SpatialAnalysisRunStatus.RUNNING
    run.started_at = timezone.now()
    run.save(update_fields=["status", "started_at"])

    start_perf = time.perf_counter()

    try:
        results: List[SpatialAnalysisResult] = []

        with transaction.atomic():
            if run.analysis_type == SpatialAnalysisType.LAND_USE_CLASSIFICATION:
                results = LandUseClassificationService.execute_land_use_analysis(run)

            elif run.analysis_type == SpatialAnalysisType.URBAN_GROWTH_ANALYSIS:
                results = UrbanGrowthAnalysisService.execute_growth_analysis(run)

            elif run.analysis_type == SpatialAnalysisType.SPATIAL_CLUSTERING:
                results = SpatialClusteringService.execute_clustering_analysis(run)

            elif run.analysis_type == SpatialAnalysisType.ACCESSIBILITY_ANALYSIS:
                results = AccessibilityAnalysisService.execute_accessibility_analysis(run)

            elif run.analysis_type == SpatialAnalysisType.ENVIRONMENTAL_EXPOSURE:
                results = EnvironmentalExposureService.execute_exposure_analysis(run)

            elif run.analysis_type == SpatialAnalysisType.DEVELOPMENT_SUITABILITY:
                results = DevelopmentSuitabilityService.execute_suitability_analysis(run)

            elif run.analysis_type == SpatialAnalysisType.SPATIAL_FORECASTING:
                results = SpatialForecastingService.execute_forecasting_analysis(run)

            else:
                raise ValueError(f"Unsupported spatial analysis type '{run.analysis_type}'")

            run.status = SpatialAnalysisRunStatus.COMPLETED
            run.completed_at = timezone.now()
            run.processing_duration_sec = time.perf_counter() - start_perf
            run.total_results_count = len(results)
            run.save(
                update_fields=[
                    "status",
                    "completed_at",
                    "processing_duration_sec",
                    "total_results_count",
                    "summary_metrics",
                ]
            )

        return run

    except Exception as err:
        logger.exception("Spatial analysis run %s failed: %s", run.id, err)
        run.status = SpatialAnalysisRunStatus.FAILED
        run.completed_at = timezone.now()
        run.processing_duration_sec = time.perf_counter() - start_perf
        run.error_details = str(err)
        run.save(
            update_fields=[
                "status",
                "completed_at",
                "processing_duration_sec",
                "error_details",
            ]
        )
        raise
