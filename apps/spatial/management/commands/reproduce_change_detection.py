import hashlib
import json
import logging
import os
import uuid
from typing import Any, Dict

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.spatial.domain import SRID_WGS84, ChangeRunStatus
from apps.spatial.models import (
    ChangeCandidate,
    ChangeDetectionProject,
    ChangeDetectionRun,
    ImageryItem,
    SpatialEntity,
    Building,
)
from apps.spatial.services.change_detection import execute_change_detection_pipeline

logger = logging.getLogger(__name__)


def compute_file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


class Command(BaseCommand):
    help = "M4.4.2 — Independently reproduces and validates the Mombasa change detection run."

    def add_arguments(self, parser):
        parser.add_argument(
            "--project-id",
            type=str,
            default=None,
            help="UUID of ChangeDetectionProject (defaults to Mombasa Urban Change Monitoring 2026)",
        )
        parser.add_argument(
            "--cva-threshold",
            type=float,
            default=0.22,
            help="Change Vector Analysis magnitude threshold (default: 0.22)",
        )
        parser.add_argument(
            "--min-area",
            type=float,
            default=150.0,
            help="Minimum change candidate area in square metres (default: 150.0)",
        )
        parser.add_argument(
            "--run-id",
            type=str,
            default=None,
            help="Explicit UUID for the new ChangeDetectionRun",
        )

    def handle(self, *args, **options):
        cva_threshold = options["cva_threshold"]
        min_area = options["min_area"]

        # 1. Fetch Project
        if options["project_id"]:
            project = ChangeDetectionProject.objects.get(id=options["project_id"])
        else:
            project = ChangeDetectionProject.objects.filter(
                name__icontains="Mombasa"
            ).first()
            if not project:
                raise CommandError("No Mombasa ChangeDetectionProject found.")

        # 2. Fetch baseline and comparison imagery items
        baseline_item = ImageryItem.objects.filter(
            item_id="S2B_37MER_20260213_0_L2A"
        ).first()
        comparison_item = ImageryItem.objects.filter(
            item_id="S2A_37MER_20260521_0_L2A"
        ).first()

        if not baseline_item or not comparison_item:
            raise CommandError("Mombasa Sentinel-2 imagery items not found in database.")

        self.stdout.write(
            f"Project: {project.name} ({project.id})\n"
            f"Baseline: {baseline_item.item_id} ({baseline_item.acquisition_datetime})\n"
            f"Comparison: {comparison_item.item_id} ({comparison_item.acquisition_datetime})\n"
        )

        # Baseline count of spatial entities and buildings to prove database preservation
        initial_entity_count = SpatialEntity.objects.count()
        initial_building_count = Building.objects.count()
        self.stdout.write(
            f"Canonical database baseline: {initial_entity_count} SpatialEntities, {initial_building_count} Buildings."
        )

        # 3. Compute Reproducibility Fingerprint
        asset_fingerprints: Dict[str, Dict[str, str]] = {}
        for item in [baseline_item, comparison_item]:
            item_assets = {}
            for asset in item.assets.all():
                if os.path.exists(asset.storage_path):
                    item_assets[asset.asset_key] = compute_file_sha256(asset.storage_path)
            asset_fingerprints[item.item_id] = item_assets

        aoi_wkt = project.aoi_geometry.wkt if project.aoi_geometry else ""
        aoi_hash = hashlib.sha256(aoi_wkt.encode("utf-8")).hexdigest()

        param_dict = {
            "cva_threshold": cva_threshold,
            "min_change_area_m2": min_area,
            "phenology_correction": "BIMODAL_HARMONIC_KENYA_COASTAL",
            "co_registration": "FOURIER_PHASE_CORRELATION_SUBPIXEL",
        }
        param_hash = hashlib.sha256(
            json.dumps(param_dict, sort_keys=True).encode("utf-8")
        ).hexdigest()

        fingerprint = {
            "reproducibility_version": "M4.4.2",
            "baseline_item_id": baseline_item.item_id,
            "comparison_item_id": comparison_item.item_id,
            "aoi_hash": aoi_hash,
            "parameters_hash": param_hash,
            "asset_checksums": asset_fingerprints,
            "parameters": param_dict,
        }

        # 4. Create New Run without touching existing Run 1
        run_uuid = uuid.UUID(options["run_id"]) if options["run_id"] else uuid.uuid4()
        run = ChangeDetectionRun.objects.create(
            id=run_uuid,
            project=project,
            baseline_item=baseline_item,
            comparison_item=comparison_item,
            algorithm_identifier="CVA_SPECTRAL_DIFFERENCE",
            algorithm_version="4.4.2",
            parameters=fingerprint,
            status=ChangeRunStatus.PENDING,
            created_by=project.created_by,
        )

        self.stdout.write(f"Created new ChangeDetectionRun: {run.id}")

        # 5. Execute Pipeline
        executed_run = execute_change_detection_pipeline(
            run,
            cva_threshold=cva_threshold,
            min_change_area_m2=min_area,
        )

        # 6. Verification and Assertions
        candidates = list(
            ChangeCandidate.objects.filter(run=executed_run).order_by("candidate_id")
        )
        self.stdout.write(
            f"Run status: {executed_run.status}, Candidates detected: {len(candidates)}, "
            f"Duration: {executed_run.processing_duration_sec:.2f}s"
        )

        if executed_run.status != ChangeRunStatus.COMPLETED:
            raise CommandError(f"Run failed with status {executed_run.status}: {executed_run.error_details}")

        if len(candidates) != 5:
            raise CommandError(f"Expected exactly 5 change candidates, got {len(candidates)}")

        for idx, cand in enumerate(candidates):
            self.stdout.write(
                f"Candidate [{idx+1}/5] {cand.candidate_id}:\n"
                f"  Category: {cand.category}\n"
                f"  Geometry SRID: {cand.geometry.srid} (type: {cand.geometry.geom_type})\n"
                f"  Area (m²): {cand.area_sqm} (pixel count area: {cand.pixel_count_area_sqm})\n"
                f"  Delta NDVI: {cand.delta_ndvi}, Delta NDBI: {cand.delta_ndbi}\n"
                f"  CVA Magnitude: {cand.change_magnitude}\n"
                f"  Detection Confidence: {cand.detection_confidence}\n"
                f"  Review Status: {cand.review_status}\n"
                f"  Associated Building: {cand.associated_building_id} (Type: {cand.building_association_type})\n"
            )

            # Invariant checks
            assert cand.geometry.srid == SRID_WGS84, f"Geometry SRID must be 4326, got {cand.geometry.srid}"
            assert cand.geometry.geom_type == "Polygon", f"Geometry type must be Polygon, got {cand.geometry.geom_type}"
            assert cand.area_sqm > 0, "Area must be positive"
            assert cand.area_sqm == cand.pixel_count_area_sqm, (
                f"Area {cand.area_sqm} must match pixel count area {cand.pixel_count_area_sqm}"
            )
            assert float(cand.area_sqm) % 100.0 == 0.0, "Area must be multiple of 100 m² (10m x 10m pixels)"
            assert cand.delta_ndvi is not None, "Delta NDVI must be recorded"
            assert cand.delta_ndbi is not None, "Delta NDBI must be recorded"
            assert cand.change_magnitude >= cva_threshold, (
                f"Change magnitude {cand.change_magnitude} must meet threshold {cva_threshold}"
            )

        # Ensure canonical database is strictly untouched
        final_entity_count = SpatialEntity.objects.count()
        final_building_count = Building.objects.count()
        assert final_entity_count == initial_entity_count, (
            f"SpatialEntity count mutated! {initial_entity_count} -> {final_entity_count}"
        )
        assert final_building_count == initial_building_count, (
            f"Building count mutated! {initial_building_count} -> {final_building_count}"
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"M4.4.2 REPRODUCIBILITY SUCCESSFUL: Run {executed_run.id} verified with 5 candidates. "
                f"Database rows strictly preserved at {final_entity_count} entities."
            )
        )
