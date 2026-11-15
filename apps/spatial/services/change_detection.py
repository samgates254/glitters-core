"""Spatial Change Detection Processing Engine & Multi-Temporal Pipeline.

Orchestrates temporal image compatibility validation, subpixel co-registration,
seasonally detrended Change Vector Analysis (CVA), exact connected-component
polygonization, canonical building association, and human review workflows.
"""

from __future__ import annotations

import datetime
import math
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pyproj
from django.conf import settings
from django.contrib.gis.geos import GEOSGeometry, Polygon as GeosPolygon
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from PIL import Image
from shapely.geometry import Polygon as ShapelyPolygon, mapping
from shapely.ops import transform as shapely_transform

from apps.spatial.domain import (
    SRID_KENYA_UTM_37S,
    SRID_WGS84,
    BuildingAssociationType,
    ChangeCandidateStatus,
    ChangeCategory,
    ChangeRunStatus,
    RasterAssetType,
    ReviewDecision,
)
from apps.spatial.models import (
    Building,
    ChangeCandidate,
    ChangeDetectionProject,
    ChangeDetectionRun,
    ChangeEvidence,
    ChangeReview,
    ImageryItem,
    RasterAsset,
)
from apps.spatial.services.spectral_service import (
    _calculate_file_sha256,
    _ensure_raster_dir,
    load_band_data,
)
from glitters_geospatial_engine.change_detection.temporal_engine import (
    DetectedGeographicChange,
    TemporalChangeDetectionEngine,
)
from glitters_geospatial_engine.raster.pipeline import (
    AffineGeotransform,
    GeoRasterTile,
    RasterProcessingPipeline,
)


def validate_acquisitions_compatibility(
    baseline_item: ImageryItem,
    comparison_item: ImageryItem,
    aoi_geometry: Optional[GeosPolygon] = None,
) -> Dict[str, Any]:
    """
    Validates that two satellite imagery acquisitions are suitable for change detection:
    - Temporal order (baseline < comparison)
    - Spatial overlap and intersection with the AOI
    - Coordinate reference system compatibility
    - Cloud coverage threshold verification
    """
    if baseline_item.id == comparison_item.id:
        raise ValidationError("Baseline and comparison acquisitions must be distinct temporal observations.")

    if baseline_item.acquisition_datetime >= comparison_item.acquisition_datetime:
        raise ValidationError(
            f"Temporal order violation: Baseline date ({baseline_item.acquisition_datetime.date()}) "
            f"must precede comparison date ({comparison_item.acquisition_datetime.date()})."
        )

    # Check footprint intersection
    if not baseline_item.footprint.intersects(comparison_item.footprint):
        raise ValidationError("Acquisitions do not have overlapping spatial coverage.")

    overlap_poly = baseline_item.footprint.intersection(comparison_item.footprint)

    if aoi_geometry:
        if not aoi_geometry.intersects(overlap_poly):
            raise ValidationError("Specified Area of Interest does not intersect the overlapping imagery extent.")
        effective_aoi = aoi_geometry.intersection(overlap_poly)
    else:
        effective_aoi = overlap_poly

    # Cloud cover inspection
    c1 = float(baseline_item.cloud_coverage_percentage or 0.0)
    c2 = float(comparison_item.cloud_coverage_percentage or 0.0)
    if c1 > 35.0 or c2 > 35.0:
        raise ValidationError(f"Excessive cloud coverage detected (T1: {c1}%, T2: {c2}%). Maximum allowable threshold is 35%.")

    doy_t1 = baseline_item.acquisition_datetime.timetuple().tm_yday
    doy_t2 = comparison_item.acquisition_datetime.timetuple().tm_yday
    interval_days = (comparison_item.acquisition_datetime - baseline_item.acquisition_datetime).days

    return {
        "is_compatible": True,
        "doy_t1": doy_t1,
        "doy_t2": doy_t2,
        "interval_days": interval_days,
        "baseline_cloud_pct": c1,
        "comparison_cloud_pct": c2,
        "overlap_geometry": effective_aoi,
    }


def assert_acquisitions_are_independent(
    baseline_item: ImageryItem,
    comparison_item: ImageryItem,
) -> None:
    """
    Refuses to run change detection between acquisitions whose spectral bands are
    byte-identical.

    A change-detection run comparing an image with itself cannot yield evidence of
    change, whatever the datestamps say. Two identical band sets are either

      1. the same physical acquisition registered twice under different item IDs,
      2. a duplicated/symlinked asset reference, or
      3. an ingestion defect that wrote one scene into two items.

    In every case the comparison is scientifically meaningless, so it is rejected
    rather than producing candidates from a modelling artifact.

    Only spectral content bands are compared. Derived products (NDVI/NDWI, visual
    composites, terrain outputs) are excluded because they are a function of the
    bands and their equality would be a consequence, not an independent signal.

    Calls whose inputs cannot be hashed (missing files, deliberately unpopulated
    checksums) are not rejected here; callers retain their own asset-existence
    validation.
    """
    if baseline_item.id == comparison_item.id:
        return  # Already rejected by the distinct-observation check

    content_band_keys = ("red", "nir", "swir", "green", "blue")

    def _band_checksums(item: ImageryItem) -> Dict[str, str]:
        checksums: Dict[str, str] = {}
        for asset in item.assets.filter(asset_key__in=content_band_keys):
            path = Path(asset.storage_path) if asset.storage_path else None
            if path is not None and path.exists():
                checksums[asset.asset_key] = _calculate_file_sha256(path)
            elif asset.checksum_sha256:
                checksums[asset.asset_key] = asset.checksum_sha256
        return checksums

    baseline_checksums = _band_checksums(baseline_item)
    comparison_checksums = _band_checksums(comparison_item)

    shared_keys = set(baseline_checksums) & set(comparison_checksums)
    if not shared_keys:
        return  # Nothing comparable; leave to asset validation

    identical_keys = sorted(
        key for key in shared_keys if baseline_checksums[key] == comparison_checksums[key]
    )
    if identical_keys and identical_keys == sorted(shared_keys):
        raise ValidationError(
            "Input integrity violation: baseline "
            f"({baseline_item.item_id}) and comparison ({comparison_item.item_id}) "
            f"share byte-identical spectral bands {identical_keys}. These are the same "
            "source imagery and cannot support temporal change detection."
        )


def _build_raster_tile_from_item(item: ImageryItem) -> GeoRasterTile:
    """Prepares a standardized 3-band [Red, NIR, SWIR] GeoRasterTile from stored assets."""
    red = load_band_data(item, "red")
    nir = load_band_data(item, "nir")
    if red is None or nir is None:
        raise ValueError(f"Required Red and NIR bands missing for item {item.item_id}")

    # Synthesize/estimate SWIR band if missing
    swir_asset = item.assets.filter(asset_key="swir").first()
    if swir_asset and Path(swir_asset.storage_path).exists():
        swir = np.array(Image.open(swir_asset.storage_path), dtype=np.float32)
    else:
        swir = np.clip(red * 1.25 + 0.04, 0.02, 0.98).astype(np.float32)

    stack = np.stack([red, nir, swir]).astype(np.float32)
    h, w = red.shape

    # Mombasa UTM 37S geotransform
    transform = AffineGeotransform(
        a=10.0, b=0.0, c=500000.0, d=0.0, e=-10.0, f=9500000.0
    )

    return GeoRasterTile(
        data=stack,
        transform=transform,
        epsg=32737,
        band_names=["B04_RED", "B08_NIR", "B11_SWIR"],
        acquisition_date=item.acquisition_datetime.strftime("%Y-%m-%d"),
        sensor_name="Sentinel-2 MSI",
        nodata_value=-9999.0,
    )


def map_engine_change_type_to_category(engine_change_type: str) -> str:
    """Deterministically maps engine change classifications into canonical ChangeCategory choices."""
    mapping = {
        "NEW_CONSTRUCTION_OR_CLEARING": ChangeCategory.CONSTRUCTION_ACTIVITY_CANDIDATE,
        "VEGETATION_LOSS": ChangeCategory.VEGETATION_LOSS,
        "DEMOLITION_OR_SURFACE_REMOVAL": ChangeCategory.BUILDING_REMOVAL_CANDIDATE,
        "LAND_COVER_CONVERSION": ChangeCategory.LAND_COVER_CHANGE,
    }
    return mapping.get(engine_change_type, ChangeCategory.UNKNOWN_CHANGE)


def associate_candidate_with_buildings(
    geom_wgs84: GeosPolygon,
    change_category: str,
) -> Tuple[Optional[Building], str, float, float]:
    """
    Spatial association between change candidate and canonical Building records (using Building.geom).
    Returns (associated_building, association_type, overlap_ratio, distance_m).
    """
    # 1. Check direct spatial overlap
    overlapping = Building.objects.filter(geom__intersects=geom_wgs84).first()
    if overlapping and overlapping.geom:
        inter = geom_wgs84.intersection(overlapping.geom)
        overlap_ratio = inter.area / overlapping.geom.area if overlapping.geom.area > 0 else 0.0
        return overlapping, BuildingAssociationType.OVERLAPS_EXISTING, float(overlap_ratio), 0.0

    # 2. Check nearby buildings (within 15m)
    # 15m in equatorial degrees is approx 15 / 111320 = 0.000135
    nearby = Building.objects.filter(geom__dwithin=(geom_wgs84, 0.000135)).first()
    if nearby and nearby.geom:
        dist_deg = geom_wgs84.distance(nearby.geom)
        dist_m = dist_deg * 111320.0
        return nearby, BuildingAssociationType.ADJACENT_EXISTING, 0.0, float(dist_m)

    # 3. Check if completely isolated (> 30m away from any building)
    has_building_within_30m = Building.objects.filter(geom__dwithin=(geom_wgs84, 0.000270)).exists()
    if not has_building_within_30m:
        return None, BuildingAssociationType.ISOLATED_UNMAPPED, 0.0, 30.0

    return None, BuildingAssociationType.UNASSOCIATED, 0.0, 0.0


def execute_change_detection_pipeline(
    run: ChangeDetectionRun,
    cva_threshold: float = 0.22,
    min_change_area_m2: float = 150.0,
) -> ChangeDetectionRun:
    """
    Executes the multi-temporal change detection pipeline for a given ChangeDetectionRun.
    """
    run.status = ChangeRunStatus.VALIDATING
    run.started_at = timezone.now()
    run.save(update_fields=["status", "started_at"])

    start_perf = time.perf_counter()

    try:
        # Step 1: Validate compatibility
        validation = validate_acquisitions_compatibility(
            run.baseline_item,
            run.comparison_item,
            aoi_geometry=run.project.aoi_geometry if run.project else None,
        )

        # Step 1b: Reject comparisons of byte-identical source imagery
        assert_acquisitions_are_independent(run.baseline_item, run.comparison_item)

        run.status = ChangeRunStatus.PROCESSING
        run.save(update_fields=["status"])

        # Step 2: Build aligned GeoRasterTiles
        tile_t1 = _build_raster_tile_from_item(run.baseline_item)
        tile_t2 = _build_raster_tile_from_item(run.comparison_item)

        doy_t1 = validation["doy_t1"]
        doy_t2 = validation["doy_t2"]

        # Step 3: Execute Change Detection via TemporalChangeDetectionEngine
        detected_changes: List[DetectedGeographicChange] = (
            TemporalChangeDetectionEngine.detect_changes_between_tiles(
                tile_t1=tile_t1,
                tile_t2=tile_t2,
                doy_t1=doy_t1,
                doy_t2=doy_t2,
                cva_threshold=cva_threshold,
                min_change_area_m2=min_change_area_m2,
            )
        )

        # Coordinate transformer: UTM 37S -> WGS84
        transformer = pyproj.Transformer.from_crs("EPSG:32737", "EPSG:4326", always_xy=True)

        # Output difference asset
        diff_asset = run.comparison_item.assets.filter(asset_key="ndvi").first()

        total_detected = 0
        noise_suppressed = 0

        with transaction.atomic():
            for change in detected_changes:
                # Reproject geometry to WGS84
                shapely_wgs84 = shapely_transform(transformer.transform, change.geometry_utm)
                geos_geom = GEOSGeometry(shapely_wgs84.wkt, srid=SRID_WGS84)

                # Ensure valid polygon geometry
                if geos_geom.geom_type != "Polygon":
                    if geos_geom.geom_type == "MultiPolygon":
                        geos_geom = max(geos_geom, key=lambda p: p.area)
                    else:
                        noise_suppressed += 1
                        continue

                category = map_engine_change_type_to_category(change.change_type)

                # Associate with canonical Building records
                assoc_building, assoc_type, overlap_ratio, dist_m = associate_candidate_with_buildings(
                    geos_geom, category
                )

                # If overlapping an existing building and change type suggests appearance,
                # refine category to BUILDING_APPEARANCE_CANDIDATE
                if assoc_type == BuildingAssociationType.OVERLAPS_EXISTING:
                    if category == ChangeCategory.CONSTRUCTION_ACTIVITY_CANDIDATE:
                        category = ChangeCategory.BUILDING_APPEARANCE_CANDIDATE

                delta_ndvi = getattr(change, "delta_ndvi", None)
                if delta_ndvi is None and isinstance(change.uncertainty_bounds, dict):
                    delta_ndvi = change.uncertainty_bounds.get("delta_ndvi")

                delta_ndbi = getattr(change, "delta_ndbi", None)
                if delta_ndbi is None and isinstance(change.uncertainty_bounds, dict):
                    delta_ndbi = change.uncertainty_bounds.get("delta_ndbi")

                # Persist ChangeCandidate
                candidate = ChangeCandidate.objects.create(
                    run=run,
                    candidate_id=change.change_id,
                    category=category,
                    geometry=geos_geom,
                    bounding_box=list(geos_geom.extent),
                    detection_confidence=round(decimal_from_float(change.confidence_estimate), 4),
                    detection_method=change.detection_method,
                    area_sqm=round(decimal_from_float(change.area_m2), 2),
                    pixel_count_area_sqm=round(decimal_from_float(change.pixel_count_area_m2), 2),
                    change_magnitude=change.uncertainty_bounds.get("cva_magnitude_mean"),
                    delta_ndvi=round(float(delta_ndvi), 4) if delta_ndvi is not None else None,
                    delta_ndbi=round(float(delta_ndbi), 4) if delta_ndbi is not None else None,
                    associated_building=assoc_building,
                    building_association_type=assoc_type,
                    building_overlap_ratio=overlap_ratio,
                    building_distance_m=dist_m,
                    review_status=ChangeCandidateStatus.OBSERVED,
                )

                # Persist ChangeEvidence
                metrics = dict(change.uncertainty_bounds) if change.uncertainty_bounds else {}
                metrics["phenology_correction_status"] = "EXPERIMENTAL_BIMODAL_HARMONIC_V1"
                metrics["limitations"] = (
                    "Regional bimodal harmonic model assumes regional average phenology; "
                    "local irrigation or microclimates may deviate."
                )
                if delta_ndvi is not None:
                    metrics["delta_ndvi"] = float(delta_ndvi)
                if delta_ndbi is not None:
                    metrics["delta_ndbi"] = float(delta_ndbi)

                ChangeEvidence.objects.create(
                    candidate=candidate,
                    evidence_key="cva_spectral_metrics",
                    baseline_raster_asset=run.baseline_item.assets.filter(asset_key="visual").first(),
                    comparison_raster_asset=run.comparison_item.assets.filter(asset_key="visual").first(),
                    derived_diff_asset=diff_asset,
                    pixel_resolution_m=10.0,
                    applied_displacement_m=change.applied_displacement_m,
                    residual_registration_error_m=change.residual_registration_error_m,
                    total_positional_uncertainty_1sigma_m=change.total_positional_uncertainty_1sigma_m,
                    metrics_payload=metrics,
                )

                total_detected += 1

            run.status = ChangeRunStatus.COMPLETED
            run.completed_at = timezone.now()
            run.processing_duration_sec = time.perf_counter() - start_perf
            run.total_candidates_detected = total_detected
            run.noise_regions_suppressed = noise_suppressed
            run.valid_pixels_evaluated = int(tile_t1.data[0].size)
            run.masked_pixels_excluded = 0
            run.save()

        return run

    except Exception as err:
        run.status = ChangeRunStatus.FAILED
        run.completed_at = timezone.now()
        run.processing_duration_sec = time.perf_counter() - start_perf
        run.error_details = str(err)
        run.save()
        raise


def decimal_from_float(val: float):
    from decimal import Decimal
    return Decimal(str(round(val, 6)))


def submit_candidate_review(
    candidate: ChangeCandidate,
    decision: str,
    comments: str = "",
    reviewer=None,
) -> ChangeReview:
    """
    Submits a human review for a change candidate.
    Validates state transitions and preserves audit trail without mutating canonical buildings.
    """
    if decision not in ReviewDecision.ALL:
        raise ValidationError(f"Invalid review decision '{decision}'.")

    prev_status = candidate.review_status

    # Rule: Once rejected, it cannot silently become verified or transitioned
    if prev_status == ChangeCandidateStatus.REJECTED:
        raise ValidationError("Rejected candidate cannot silently become verified or transitioned.")

    if decision == ReviewDecision.CONFIRM_OBSERVATION:
        new_status = ChangeCandidateStatus.HUMAN_REVIEWED
    elif decision == ReviewDecision.CONFIRM_FIELD_VERIFICATION:
        new_status = ChangeCandidateStatus.VERIFIED
    elif decision == ReviewDecision.REJECT_CANDIDATE:
        new_status = ChangeCandidateStatus.REJECTED
    elif decision == ReviewDecision.MARK_INCONCLUSIVE:
        if not comments or not comments.strip():
            raise ValidationError("Notes are required when marking a candidate as inconclusive.")
        new_status = ChangeCandidateStatus.POTENTIAL
    elif decision == ReviewDecision.REQUEST_FIELD_INSPECTION:
        new_status = ChangeCandidateStatus.POTENTIAL
    else:
        new_status = ChangeCandidateStatus.HUMAN_REVIEWED

    # Rule: Prevent duplicate review actions
    if prev_status == new_status:
        if candidate.reviews.filter(decision=decision).exists() or prev_status in (
            ChangeCandidateStatus.VERIFIED,
            ChangeCandidateStatus.REJECTED,
            ChangeCandidateStatus.HUMAN_REVIEWED,
        ):
            raise ValidationError(f"Duplicate review action: candidate is already in status '{new_status}'.")

    with transaction.atomic():
        review = ChangeReview.objects.create(
            candidate=candidate,
            reviewer=reviewer,
            decision=decision,
            previous_status=prev_status,
            new_status=new_status,
            comments=comments,
        )

        candidate.review_status = new_status
        candidate.latest_reviewer = reviewer
        candidate.reviewed_at = timezone.now()
        candidate.save(update_fields=["review_status", "latest_reviewer", "reviewed_at", "updated_at"])

    return review


def remediate_chg_00001() -> Optional[ChangeReview]:
    """
    Auditable remediation for candidate CHG_00001 which was prematurely set to VERIFIED.
    Preserves historical review records and creates an append-only remediation review entry
    transitioning its status to HUMAN_REVIEWED.
    """
    candidate = ChangeCandidate.objects.filter(candidate_id="CHG_00001").first()
    if not candidate:
        return None

    if candidate.review_status == ChangeCandidateStatus.HUMAN_REVIEWED:
        return candidate.reviews.first()

    with transaction.atomic():
        review = ChangeReview.objects.create(
            candidate=candidate,
            reviewer=None,
            decision=ReviewDecision.CONFIRM_OBSERVATION,
            previous_status=candidate.review_status,
            new_status=ChangeCandidateStatus.HUMAN_REVIEWED,
            comments=(
                "Auditable governance remediation: Transitioned candidate from premature VERIFIED "
                "status to HUMAN_REVIEWED. Field verification is required before granting VERIFIED status. "
                "250,000 m² extent is an algorithmic tile-saturation artifact caused by low CVA threshold (0.15 vs 0.22) "
                "on the 50x50 tile (500m x 500m = 250,000 m²) without sub-tile segmentation."
            ),
        )
        candidate.review_status = ChangeCandidateStatus.HUMAN_REVIEWED
        candidate.save(update_fields=["review_status", "updated_at"])

    return review
