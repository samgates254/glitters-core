"""Django management command for controlled spatial dataset ingestion with dry-run support."""

from __future__ import annotations

import hashlib
import os
import sys
from decimal import Decimal

from django.contrib.gis.geos import Polygon
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from apps.spatial.domain import (
    DatasetCategory,
    DatasetVersionStatus,
    LicenseType,
    ReconciliationDisposition,
    SRID_WGS84,
    UpdateFrequency,
)
from apps.spatial.models import (
    Building,
    DatasetQualityReport,
    DatasetRegistry,
    DatasetVersion,
    SpatialEntity,
    SpatialIdentityMapping,
    SpatialQuarantine,
    SpatialSourceFeature,
)
from apps.spatial.services.ingestion import (
    SpatialIngestionEngine,
    stream_geojson_features,
)


class Command(BaseCommand):
    help = "Ingests spatial datasets (such as Mombasa OSM buildings) with idempotent reconciliation and dry-run mode."

    def add_arguments(self, parser):
        parser.add_argument(
            "--source-file",
            type=str,
            default="/home/mr-robot/data/glitters-osm/processed/mombasa-buildings.geojson",
            help="Path to source GeoJSON file.",
        )
        parser.add_argument(
            "--dataset-urn",
            type=str,
            default="urn:glitters:dataset:ke:msa:osm:buildings",
            help="Canonical dataset registry URN.",
        )
        parser.add_argument(
            "--version-tag",
            type=str,
            default="2026.11-v1",
            help="Version tag for this dataset release.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=1000,
            help="Chunk batch size for MTI persistence.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Optional limit on number of features to process (for benchmarking).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simulate ingestion without writing to the database.",
        )

    def handle(self, *args, **options):
        source_file = options["source_file"]
        dataset_urn = options["dataset_urn"]
        version_tag = options["version_tag"]
        batch_size = options["batch_size"]
        limit = options["limit"]
        dry_run = options["dry_run"]

        if not os.path.exists(source_file):
            raise CommandError(f"Source file not found: {source_file}")

        self.stdout.write(self.style.NOTICE(f"=== GLITTERS SPATIAL INGESTION PIPELINE ==="))
        self.stdout.write(f"Source file: {source_file}")
        self.stdout.write(f"Dataset URN: {dataset_urn}")
        self.stdout.write(f"Version Tag: {version_tag}")
        self.stdout.write(f"Batch Size:  {batch_size}")
        self.stdout.write(f"Limit:       {limit or 'None (All)'}")
        self.stdout.write(f"Dry Run:     {dry_run}")

        # Compute SHA-256 checksum
        self.stdout.write("Calculating source file checksum...")
        hasher = hashlib.sha256()
        with open(source_file, "rb") as f:
            while chunk := f.read(1024 * 1024):
                hasher.update(chunk)
        source_checksum = hasher.hexdigest()
        self.stdout.write(f"Source SHA-256: {source_checksum}")

        # Baseline database counts before run
        counts_before = {
            "entities": SpatialEntity.objects.count(),
            "buildings": Building.objects.count(),
            "mappings": SpatialIdentityMapping.objects.count(),
            "source_features": SpatialSourceFeature.objects.count(),
            "quarantines": SpatialQuarantine.objects.count(),
        }

        # Prepare Dataset Registry and Version (Read-only if dry-run)
        if dry_run:
            registry = DatasetRegistry.objects.filter(dataset_urn=dataset_urn).first()
            if not registry:
                registry = DatasetRegistry(
                    dataset_urn=dataset_urn,
                    name="Mombasa OSM Building Footprints",
                    category=DatasetCategory.BUILDINGS,
                )
            version = DatasetVersion.objects.filter(
                dataset__dataset_urn=dataset_urn, version_tag=version_tag
            ).first()
            if not version:
                extent_poly = Polygon.from_bbox((39.549644, -4.200105, 39.792528, -3.869816))
                extent_poly.srid = SRID_WGS84
                version = DatasetVersion(
                    dataset=registry,
                    version_tag=version_tag,
                    source_url="https://www.openstreetmap.org",
                    source_checksum_sha256=source_checksum,
                    pipeline_git_commit="7f61a98",
                    license_type=LicenseType.ODBL,
                    attribution_statement="© OpenStreetMap contributors",
                    spatial_extent=extent_poly,
                    feature_count=0,
                )
        else:
            registry, _ = DatasetRegistry.objects.get_or_create(
                dataset_urn=dataset_urn,
                defaults={
                    "name": "Mombasa OSM Building Footprints",
                    "description": "Building footprints extracted from OpenStreetMap for Mombasa County.",
                    "category": DatasetCategory.BUILDINGS,
                    "update_frequency": UpdateFrequency.STATIC,
                },
            )

            version = DatasetVersion.objects.filter(
                dataset=registry, version_tag=version_tag
            ).first()

            if not version:
                extent_poly = Polygon.from_bbox((39.549644, -4.200105, 39.792528, -3.869816))
                extent_poly.srid = SRID_WGS84
                version = DatasetVersion.objects.create(
                    dataset=registry,
                    version_tag=version_tag,
                    source_url="https://www.openstreetmap.org",
                    source_checksum_sha256=source_checksum,
                    pipeline_git_commit="7f61a98",
                    license_type=LicenseType.ODBL,
                    attribution_statement="© OpenStreetMap contributors",
                    spatial_extent=extent_poly,
                    feature_count=0,
                )

        engine = SpatialIngestionEngine(
            dataset_version=version,
            batch_size=batch_size,
            dry_run=dry_run,
        )

        # Generator with optional limit
        def limited_features():
            count = 0
            for feat in stream_geojson_features(source_file):
                yield feat
                count += 1
                if limit and count >= limit:
                    break

        lock_acquired = False
        lock_id = (
            int(hashlib.md5(f"spatial_lock_{dataset_urn}".encode()).hexdigest()[:15], 16)
            & 0x7FFFFFFFFFFFFFFF
        )

        if not dry_run:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_try_advisory_lock(%s);", [lock_id])
                lock_acquired = cursor.fetchone()[0]
            if not lock_acquired:
                raise CommandError(
                    f"Lock acquisition failed: another ingestion process is running for {dataset_urn}."
                )

            version.status = DatasetVersionStatus.INGESTING
            version.save(update_fields=["status"])

        try:
            self.stdout.write(self.style.NOTICE("Starting streaming ingestion..."))
            result = engine.run(limited_features())

            # Update dataset version and create quality report if not dry-run
            if not dry_run:
                version.status = DatasetVersionStatus.VALIDATING
                version.save(update_fields=["status"])

                # Verification assertions before publishing
                if version.source_checksum_sha256 != source_checksum:
                    version.status = DatasetVersionStatus.FAILED
                    version.save(update_fields=["status"])
                    raise CommandError(
                        f"Checksum mismatch: expected {version.source_checksum_sha256}, got {source_checksum}"
                    )

                if not result.is_invariant_satisfied:
                    version.status = DatasetVersionStatus.FAILED
                    version.save(update_fields=["status"])
                    raise CommandError("Reconciliation invariant failed!")

                # Parity checks
                entity_cnt = SpatialEntity.objects.filter(dataset_version=version).count()
                mapping_cnt = SpatialIdentityMapping.objects.filter(entity__dataset_version=version).count()
                building_cnt = Building.objects.filter(dataset_version=version).count()
                if entity_cnt != mapping_cnt or entity_cnt != building_cnt:
                    version.status = DatasetVersionStatus.FAILED
                    version.save(update_fields=["status"])
                    raise CommandError(
                        f"Integrity check failed: entities={entity_cnt}, mappings={mapping_cnt}, buildings={building_cnt}"
                    )

                with transaction.atomic():
                    version.status = DatasetVersionStatus.PUBLISHED
                    version.feature_count = result.inserted + result.updated + result.unchanged
                    version.save(update_fields=["status", "feature_count"])

                    DatasetQualityReport.objects.update_or_create(
                        version=version,
                        defaults={
                            "valid_geometries": result.inserted + result.updated + result.unchanged,
                            "repaired_geometries": result.repaired_count,
                            "quarantined_geometries": result.quarantined,
                            "duplicate_records": result.unchanged,
                            "horizontal_rmse_m": Decimal("1.000"),
                            "audit_notes": f"Ingestion completed in {result.duration_seconds}s at {result.throughput_per_sec} rec/s.",
                        },
                    )
        except Exception:
            if not dry_run and version and version.id:
                try:
                    version.status = DatasetVersionStatus.FAILED
                    version.save(update_fields=["status"])
                except Exception:
                    pass
            raise
        finally:
            if lock_acquired:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_unlock(%s);", [lock_id])

        # Baseline database counts after run
        counts_after = {
            "entities": SpatialEntity.objects.count(),
            "buildings": Building.objects.count(),
            "mappings": SpatialIdentityMapping.objects.count(),
            "source_features": SpatialSourceFeature.objects.count(),
            "quarantines": SpatialQuarantine.objects.count(),
        }

        self.stdout.write(self.style.SUCCESS("\n=== INGESTION RECONCILIATION SUMMARY ==="))
        self.stdout.write(f"Total Input Records:   {result.total_input:,}")
        self.stdout.write(f"  INSERTED:            {result.inserted:,}")
        self.stdout.write(f"  UPDATED:             {result.updated:,}")
        self.stdout.write(f"  UNCHANGED:           {result.unchanged:,}")
        self.stdout.write(f"  QUARANTINED:         {result.quarantined:,}")
        self.stdout.write(f"Repaired Geometries:   {result.repaired_count:,}")
        self.stdout.write(f"Processing Duration:   {result.duration_seconds}s")
        self.stdout.write(f"Throughput:            {result.throughput_per_sec} rec/s")
        self.stdout.write(f"Peak Memory:           {result.peak_memory_mb} MB")
        self.stdout.write(f"Reconciliation Invariant: {result.is_invariant_satisfied} "
                          f"({result.total_input} == {result.inserted + result.updated + result.unchanged + result.quarantined})")

        self.stdout.write("\n=== ATTRIBUTE COMPLETENESS ===")
        self.stdout.write(f"Named Buildings:       {result.named_count:,} ({result.named_count / (result.total_input or 1):.2%})")
        self.stdout.write(f"With Building Levels:  {result.levels_count:,} ({result.levels_count / (result.total_input or 1):.2%})")
        self.stdout.write(f"With Height:           {result.height_count:,} ({result.height_count / (result.total_input or 1):.2%})")
        self.stdout.write(f"With Address Tags:     {result.address_count:,} ({result.address_count / (result.total_input or 1):.2%})")

        self.stdout.write("\n=== CLASSIFICATION DISTRIBUTION ===")
        for cls_name, count in sorted(result.classification_counts.items(), key=lambda x: x[1], reverse=True):
            self.stdout.write(f"  {cls_name:<16}: {count:,}")

        self.stdout.write("\n=== DATABASE WRITE EVIDENCE ===")
        for tbl in ["entities", "buildings", "mappings", "source_features", "quarantines"]:
            delta = counts_after[tbl] - counts_before[tbl]
            self.stdout.write(f"  {tbl:<16}: before={counts_before[tbl]:<4} after={counts_after[tbl]:<4} delta={delta}")
            if dry_run and delta != 0:
                self.stdout.write(self.style.ERROR(f"ALERT: Non-zero write detected in dry-run mode for {tbl}!"))

        if result.errors:
            self.stdout.write(self.style.WARNING(f"\nEncountered {len(result.errors)} errors (quarantined). Sample: {result.errors[:3]}"))
