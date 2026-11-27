"""URL routes for spatial intelligence APIs."""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.spatial.compute_views import (
    ComputeJobViewSet,
    compute_adapters,
    compute_dashboard,
    compute_providers,
)
from apps.spatial.godseye_views import (
    GeospatialDataSourceViewSet,
    GeospatialSceneViewSet,
    LandIntelligenceProfileViewSet,
    ProviderQueryRecordViewSet,
)
from apps.spatial.views import (
    BuildingViewSet,
    ChangeCandidateViewSet,
    ChangeDetectionProjectViewSet,
    ChangeDetectionRunViewSet,
    DatasetRegistryViewSet,
    DigitalTwinViewSet,
    EntityLineageViewSet,
    ImageryCollectionViewSet,
    ImageryItemViewSet,
    LandParcelViewSet,
    RasterAssetViewSet,
    RasterProcessingJobViewSet,
    SpatialAnalysisProjectViewSet,
    SpatialAnalysisResultViewSet,
    SpatialAnalysisRunViewSet,
    SpatialEntityViewSet,
    SpatialScenarioViewSet,
    spatial_health_check,
)

router = DefaultRouter()
router.register("datasets", DatasetRegistryViewSet, basename="spatial-dataset")
router.register("entities", SpatialEntityViewSet, basename="spatial-entity")
router.register("buildings", BuildingViewSet, basename="spatial-building")
router.register("parcels", LandParcelViewSet, basename="spatial-parcel")
router.register("lineage", EntityLineageViewSet, basename="spatial-lineage")
router.register("digital-twins", DigitalTwinViewSet, basename="spatial-digital-twin")
router.register("imagery/collections", ImageryCollectionViewSet, basename="spatial-imagery-collection")
router.register("imagery/items", ImageryItemViewSet, basename="spatial-imagery-item")
router.register("rasters/jobs", RasterProcessingJobViewSet, basename="spatial-raster-job")
router.register("rasters", RasterAssetViewSet, basename="spatial-raster-asset")
router.register("change-detection/projects", ChangeDetectionProjectViewSet, basename="spatial-change-project")
router.register("change-detection/runs", ChangeDetectionRunViewSet, basename="spatial-change-run")
router.register("change-detection/candidates", ChangeCandidateViewSet, basename="spatial-change-candidate")
router.register("spatial-analysis/projects", SpatialAnalysisProjectViewSet, basename="spatial-analysis-project")
router.register("spatial-analysis/runs", SpatialAnalysisRunViewSet, basename="spatial-analysis-run")
router.register("spatial-analysis/results", SpatialAnalysisResultViewSet, basename="spatial-analysis-result")
router.register("spatial-analysis/scenarios", SpatialScenarioViewSet, basename="spatial-analysis-scenario")
router.register("scenes", GeospatialSceneViewSet, basename="spatial-scene")
router.register(
    "land-intelligence/profiles",
    LandIntelligenceProfileViewSet,
    basename="spatial-land-profile",
)
router.register("provider-queries", ProviderQueryRecordViewSet, basename="spatial-provider-query")
router.register("data-sources", GeospatialDataSourceViewSet, basename="spatial-data-source")
router.register("compute/jobs", ComputeJobViewSet, basename="spatial-compute-job")

urlpatterns = [
    path("health/", spatial_health_check, name="spatial-health"),
    path("compute/dashboard/", compute_dashboard, name="spatial-compute-dashboard"),
    path("compute/providers/", compute_providers, name="spatial-compute-providers"),
    path("compute/adapters/", compute_adapters, name="spatial-compute-adapters"),
    path(
        "imagery/search/",
        ImageryItemViewSet.as_view({"get": "search"}),
        name="spatial-imagery-search",
    ),
    path(
        "rasters/by-key/<str:asset_key>/tiles/<int:z>/<int:x>/<str:y>",
        RasterAssetViewSet.as_view({"get": "tile_by_key"}),
        name="spatial-raster-tile-by-key",
    ),
    path(
        "rasters/by-key/<str:asset_key>/tiles/<int:z>/<int:x>/<str:y>/",
        RasterAssetViewSet.as_view({"get": "tile_by_key"}),
        name="spatial-raster-tile-by-key-slash",
    ),
    path(
        "rasters/<uuid:pk>/tiles/<int:z>/<int:x>/<str:y>",
        RasterAssetViewSet.as_view({"get": "tile"}),
        name="spatial-raster-tile",
    ),
    path(
        "rasters/<uuid:pk>/tiles/<int:z>/<int:x>/<str:y>/",
        RasterAssetViewSet.as_view({"get": "tile"}),
        name="spatial-raster-tile-slash",
    ),
    path(
        "digital-twins/<uuid:pk>/tileset.json",
        DigitalTwinViewSet.as_view({"get": "tileset"}),
        name="spatial-digital-twin-tileset-json",
    ),
    path(
        "digital-twins/<uuid:pk>/tiles/<str:tile_name>",
        DigitalTwinViewSet.as_view({"get": "tile"}),
        name="spatial-digital-twin-tile-noslash",
    ),
    path(
        "buildings/<str:urn>/digital-twin/",
        BuildingViewSet.as_view({"get": "digital_twin_by_urn"}),
        name="spatial-building-digital-twin-urn",
    ),
    path("", include(router.urls)),
]
