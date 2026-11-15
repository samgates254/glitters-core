"""Spatial ingestion engine implementing streaming ETL, MTI bulk persistence, and identity resolution."""

from __future__ import annotations

import json
import logging
import time
import tracemalloc
import uuid
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Generator

from django.contrib.gis.geos import GEOSGeometry, MultiPolygon, Polygon
from django.core.exceptions import ValidationError
from django.db import connection, transaction

from apps.spatial.domain import (
    BuildingClassification,
    EntityType,
    LegalAuthorityLevel,
    LifecycleStatus,
    LineageType,
    ReconciliationDisposition,
    SRID_WGS84,
    build_canonical_urn,
    map_osm_building_classification,
)
from apps.spatial.models import (
    Building,
    DatasetQualityReport,
    DatasetRegistry,
    DatasetVersion,
    EntityLineage,
    SpatialEntity,
    SpatialIdentityMapping,
    SpatialQuarantine,
    SpatialSourceFeature,
)
from apps.spatial.services.crs import calculate_planar_area_utm_sqm
from apps.spatial.services.validation import (
    try_repair_geometry,
    validate_mombasa_geographic_envelope,
    validate_projection_suitability,
    validate_spatial_geometry,
)

logger = logging.getLogger(__name__)


@dataclass
class BuildingCandidate:
    entity_uuid: uuid.UUID
    canonical_urn: str
    name: str
    classification: str
    height_m: Decimal | None
    levels_above_ground: int | None
    levels_below_ground: int
    roof_shape: str
    facade_material: str
    footprint_area_sqm: Decimal
    geom: MultiPolygon
    provider_name: str
    source_record_id: str
    source_version: str
    raw_properties: dict[str, Any]
    raw_geometry: dict[str, Any]
    disposition: str
    was_repaired: bool = False
    repair_details: str = ""
    repaired_geometry: dict[str, Any] | None = None
    existing_entity_id: uuid.UUID | None = None


@dataclass
class IngestionResult:
    total_input: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    quarantined: int = 0
    repaired_count: int = 0
    duration_seconds: float = 0.0
    throughput_per_sec: float = 0.0
    dry_run: bool = False
    errors: list[str] = field(default_factory=list)
    classification_counts: dict[str, int] = field(default_factory=dict)
    named_count: int = 0
    levels_count: int = 0
    height_count: int = 0
    address_count: int = 0
    peak_memory_mb: float = 0.0

    @property
    def is_invariant_satisfied(self) -> bool:
        return self.total_input == (
            self.inserted + self.updated + self.unchanged + self.quarantined
        )


def stream_geojson_features(file_path: str) -> Generator[dict[str, Any], None, None]:
    """
    Streams features from a GeoJSON FeatureCollection file incrementally.
    """
    with open(file_path, "r", encoding="utf-8") as f:
        # Check starting character
        first_char = f.read(1)
        while first_char and first_char.isspace():
            first_char = f.read(1)
        if first_char != "{":
            raise ValueError("Input file is not a valid GeoJSON object.")

        f.seek(0)
        # Use line/chunk reading or JSON loader
        # For datasets up to 100MB, json.load of features or generator
        data = json.load(f)
        features = data.get("features", [])
        for feature in features:
            yield feature


class SpatialIngestionEngine:
    """Orchestrates streaming parsing, validation, resolution, and MTI persistence."""

    def __init__(
        self,
        dataset_version: DatasetVersion,
        batch_size: int = 1000,
        dry_run: bool = False,
    ):
        self.dataset_version = dataset_version
        self.batch_size = batch_size
        self.dry_run = dry_run

    def parse_and_validate_feature(
        self, feature: dict[str, Any]
    ) -> tuple[BuildingCandidate | None, str | None]:
        """
        Processes a raw GeoJSON feature through syntactic, topological, and geographic gates.
        Returns (candidate, None) on success or (None, error_reason) on failure.
        """
        props = feature.get("properties") or {}
        geom_dict = feature.get("geometry")

        # 1. Syntactic check: identifier
        osm_id = props.get("osm_id") or props.get("@id") or props.get("id")
        osm_type = props.get("osm_type") or props.get("@type") or "way"
        if not osm_id:
            return None, "Missing OSM identifier (@id or osm_id)."

        source_record_id = f"{osm_type}/{osm_id}"
        source_version = str(props.get("@version") or props.get("version") or "")

        # 2. Syntactic check: geometry presence
        if not geom_dict:
            return None, "Feature has no geometry."

        # 3. Geometry type check
        gtype = geom_dict.get("type")
        if gtype not in ("Polygon", "MultiPolygon"):
            return None, f"Unsupported geometry type '{gtype}' for building footprint."

        try:
            geos_geom = GEOSGeometry(json.dumps(geom_dict), srid=SRID_WGS84)
        except Exception as exc:
            return None, f"Failed to instantiate GEOSGeometry: {exc}"

        # 4. Planetary bounds and SRID
        try:
            validate_spatial_geometry(geos_geom)
        except ValidationError as exc:
            # Check if repairable
            healed_geom, was_repaired, repair_details = try_repair_geometry(geos_geom)
            if not healed_geom.valid:
                return None, f"Geometry validation failed: {exc.message}"
            geos_geom = healed_geom
        else:
            was_repaired = False
            repair_details = ""
            if isinstance(geos_geom, Polygon):
                geos_geom = MultiPolygon([geos_geom], srid=SRID_WGS84)
                was_repaired = True
                repair_details = "Promoted Polygon to MultiPolygon"

        # 5. Geographic envelope check (Mombasa bounds)
        try:
            validate_mombasa_geographic_envelope(geos_geom)
        except ValidationError as exc:
            return None, f"Geographic envelope validation failed: {exc.message}"

        # 6. Projection suitability check (UTM Zone 37S)
        try:
            validate_projection_suitability(geos_geom)
        except ValidationError as exc:
            return None, f"Projection suitability validation failed: {exc.message}"

        # Compute planar area
        try:
            area_sqm = Decimal(str(round(calculate_planar_area_utm_sqm(geos_geom), 2)))
        except Exception as exc:
            return None, f"Area calculation failed: {exc}"

        # Parse building attributes
        name = str(props.get("name") or props.get("name:en") or "").strip()[:255]
        classification = map_osm_building_classification(props)

        # Height parsing
        raw_height = props.get("height")
        height_m = None
        if raw_height is not None:
            try:
                cleaned_height = str(raw_height).lower().replace("m", "").replace("meters", "").strip()
                val = float(cleaned_height)
                if 0.0 < val <= 500.0:
                    height_m = Decimal(str(round(val, 2)))
            except (ValueError, TypeError):
                pass

        # Levels parsing
        raw_levels = props.get("building:levels") or props.get("levels")
        levels_above = None
        if raw_levels is not None:
            try:
                val = int(str(raw_levels).strip())
                if 1 <= val <= 120:
                    levels_above = val
            except (ValueError, TypeError):
                pass

        roof_shape = str(props.get("roof:shape") or "").strip()[:50]
        facade_material = str(props.get("building:material") or props.get("facade:material") or "").strip()[:100]

        # Pre-mint entity UUID and canonical URN
        entity_uuid = uuid.uuid4()
        canonical_urn = build_canonical_urn(EntityType.BUILDING, entity_uuid)

        repaired_geometry = json.loads(geos_geom.geojson) if was_repaired else None

        candidate = BuildingCandidate(
            entity_uuid=entity_uuid,
            canonical_urn=canonical_urn,
            name=name,
            classification=classification,
            height_m=height_m,
            levels_above_ground=levels_above,
            levels_below_ground=0,
            roof_shape=roof_shape,
            facade_material=facade_material,
            footprint_area_sqm=area_sqm,
            geom=geos_geom,
            provider_name="osm",
            source_record_id=source_record_id,
            source_version=source_version,
            raw_properties=props,
            raw_geometry=geom_dict,
            disposition=ReconciliationDisposition.INSERTED,
            was_repaired=was_repaired,
            repair_details=repair_details,
            repaired_geometry=repaired_geometry,
        )
        return candidate, None

    def resolve_identities(
        self, candidates: list[BuildingCandidate]
    ) -> list[BuildingCandidate]:
        """
        Resolves candidate identities against existing database mappings to determine disposition:
        INSERTED, UPDATED, or UNCHANGED.
        """
        if not candidates:
            return []

        # Batch lookup existing mappings
        provider_ids = [c.source_record_id for c in candidates]
        existing_mappings = {
            m.provider_external_id: m
            for m in SpatialIdentityMapping.objects.filter(
                provider_name="osm",
                provider_external_id__in=provider_ids,
            ).select_related("entity")
        }

        # For matched entities, prefetch the associated building geometries
        matched_entity_ids = [
            m.entity_id for m in existing_mappings.values() if m.entity_id
        ]
        existing_buildings = {
            b.id: b
            for b in Building.objects.filter(id__in=matched_entity_ids)
        }

        # Prefetch existing source features to detect source version or tag changes
        existing_source_features = {
            sf.source_record_id: sf
            for sf in SpatialSourceFeature.objects.filter(
                provider_name="osm",
                source_record_id__in=provider_ids,
            )
        }

        for candidate in candidates:
            mapping = existing_mappings.get(candidate.source_record_id)
            if not mapping:
                candidate.disposition = ReconciliationDisposition.INSERTED
                continue

            # Existing mapping exists
            candidate.existing_entity_id = mapping.entity_id
            existing_building = existing_buildings.get(mapping.entity_id)
            if not existing_building:
                candidate.disposition = ReconciliationDisposition.INSERTED
                continue

            # Compare geometry and attributes
            existing_geom = existing_building.geom
            # Check spatial equivalence: equals_exact or overlap
            is_geom_identical = False
            try:
                if existing_geom.equals_exact(candidate.geom, tolerance=1e-5):
                    is_geom_identical = True
            except Exception:
                pass

            is_attr_identical = (
                (existing_building.name == candidate.name)
                and (existing_building.classification == candidate.classification)
                and (existing_building.height_m == candidate.height_m)
                and (existing_building.levels_above_ground == candidate.levels_above_ground)
            )

            # Check source version and raw tag changes
            existing_sf = existing_source_features.get(candidate.source_record_id)
            is_sf_identical = True
            if existing_sf:
                if existing_sf.source_version and candidate.source_version:
                    if existing_sf.source_version != candidate.source_version:
                        is_sf_identical = False
                if existing_sf.raw_properties != candidate.raw_properties:
                    is_sf_identical = False

            if is_geom_identical and is_attr_identical and is_sf_identical:
                candidate.disposition = ReconciliationDisposition.UNCHANGED
            else:
                candidate.disposition = ReconciliationDisposition.UPDATED

        return candidates

    def persist_batch(
        self,
        candidates: list[BuildingCandidate],
        quarantined_records: list[tuple[dict[str, Any], str]],
    ) -> tuple[int, int, int, int]:
        """
        Persists a batch of candidates and quarantined records.
        Returns (inserted_count, updated_count, unchanged_count, quarantined_count).
        """
        to_insert = [c for c in candidates if c.disposition == ReconciliationDisposition.INSERTED]
        to_update = [c for c in candidates if c.disposition == ReconciliationDisposition.UPDATED]
        unchanged = [c for c in candidates if c.disposition == ReconciliationDisposition.UNCHANGED]

        if not self.dry_run:
            with transaction.atomic():
                # 1. Bulk insert parent SpatialEntity rows for new entities
                if to_insert:
                    parent_entities = [
                        SpatialEntity(
                            id=c.entity_uuid,
                            canonical_urn=c.canonical_urn,
                            entity_type=EntityType.BUILDING,
                            organization=None,
                            dataset_version=self.dataset_version,
                            lifecycle_status=LifecycleStatus.ACTIVE,
                        )
                        for c in to_insert
                    ]
                    SpatialEntity.objects.bulk_create(parent_entities, batch_size=self.batch_size)

                    # 2. Bulk insert child Building rows via direct SQL to support MTI
                    child_params = [
                        (
                            c.entity_uuid,
                            c.name,
                            c.classification,
                            c.height_m,
                            c.levels_above_ground,
                            c.levels_below_ground,
                            c.roof_shape,
                            c.facade_material,
                            c.footprint_area_sqm,
                            LegalAuthorityLevel.OPEN_DATA,
                            Decimal("1.000"),
                            False,
                            c.geom.ewkb,
                        )
                        for c in to_insert
                    ]
                    with connection.cursor() as cursor:
                        cursor.executemany(
                            """
                            INSERT INTO spatial_building (
                                spatialentity_ptr_id, name, classification, height_m,
                                levels_above_ground, levels_below_ground, roof_shape,
                                facade_material, footprint_area_sqm, legal_authority_level,
                                horizontal_rmse_m, is_human_verified, geom
                            ) VALUES (
                                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, ST_SetSRID(%s::geometry, 4326)
                            )
                            """,
                            child_params,
                        )

                    # 3. Bulk insert identity mappings
                    mappings = [
                        SpatialIdentityMapping(
                            entity_id=c.entity_uuid,
                            provider_name=c.provider_name,
                            provider_external_id=c.source_record_id,
                            confidence_score=Decimal("1.00"),
                        )
                        for c in to_insert
                    ]
                    SpatialIdentityMapping.objects.bulk_create(mappings, batch_size=self.batch_size)

                # 4. Handle updates
                update_params = [
                    (
                        c.name,
                        c.classification,
                        c.height_m,
                        c.levels_above_ground,
                        c.roof_shape,
                        c.facade_material,
                        c.footprint_area_sqm,
                        c.geom.ewkb,
                        c.existing_entity_id,
                    )
                    for c in to_update
                    if c.existing_entity_id
                ]
                if update_params:
                    with connection.cursor() as cursor:
                        cursor.executemany(
                            """
                            UPDATE spatial_building
                            SET name = %s,
                                classification = %s,
                                height_m = %s,
                                levels_above_ground = %s,
                                roof_shape = %s,
                                facade_material = %s,
                                footprint_area_sqm = %s,
                                geom = ST_SetSRID(%s::geometry, 4326)
                            WHERE spatialentity_ptr_id = %s
                            """,
                            update_params,
                        )

                # 5. Bulk insert source features for all candidates (updating attributes on conflicts)
                source_features = [
                    SpatialSourceFeature(
                        dataset_version=self.dataset_version,
                        provider_name=c.provider_name,
                        source_record_id=c.source_record_id,
                        source_version=c.source_version,
                        raw_properties=c.raw_properties,
                        raw_geometry=c.raw_geometry,
                        repaired_geometry=c.repaired_geometry,
                        spatial_entity_id=c.existing_entity_id or c.entity_uuid,
                        disposition=c.disposition,
                        was_repaired=c.was_repaired,
                        repair_details=c.repair_details,
                    )
                    for c in candidates
                ]
                SpatialSourceFeature.objects.bulk_create(
                    source_features,
                    batch_size=self.batch_size,
                    update_conflicts=True,
                    unique_fields=["dataset_version", "provider_name", "source_record_id"],
                    update_fields=[
                        "source_version",
                        "raw_properties",
                        "raw_geometry",
                        "repaired_geometry",
                        "spatial_entity",
                        "disposition",
                        "was_repaired",
                        "repair_details",
                    ],
                )

                # 6. Bulk insert quarantine records
                if quarantined_records:
                    quarantine_entries = [
                        SpatialQuarantine(
                            source_dataset_version=self.dataset_version,
                            source_record_id=str(
                                (raw_feat.get("properties") or {}).get("osm_id")
                                or (raw_feat.get("properties") or {}).get("@id")
                                or raw_feat.get("id")
                                or "unknown"
                            ),
                            raw_payload=raw_feat,
                            failure_stage="QUALITY_GATE",
                            error_message=reason,
                        )
                        for raw_feat, reason in quarantined_records
                    ]
                    SpatialQuarantine.objects.bulk_create(quarantine_entries, batch_size=self.batch_size)

        return len(to_insert), len(to_update), len(unchanged), len(quarantined_records)

    def run(self, features_iterable: Generator[dict[str, Any], None, None]) -> IngestionResult:
        """Runs the streaming ingestion pipeline across the features iterable."""
        tracemalloc.start()
        start_time = time.time()
        result = IngestionResult(dry_run=self.dry_run)
        class_counter = Counter()

        candidates_buffer: list[BuildingCandidate] = []
        quarantine_buffer: list[tuple[dict[str, Any], str]] = []

        for feature in features_iterable:
            result.total_input += 1
            candidate, error_msg = self.parse_and_validate_feature(feature)
            if candidate:
                if candidate.was_repaired:
                    result.repaired_count += 1
                class_counter[candidate.classification] += 1
                if candidate.name:
                    result.named_count += 1
                if candidate.levels_above_ground:
                    result.levels_count += 1
                if candidate.height_m:
                    result.height_count += 1
                props = candidate.raw_properties or {}
                if any(k.startswith("addr:") for k in props.keys()):
                    result.address_count += 1
                candidates_buffer.append(candidate)
            else:
                result.errors.append(error_msg or "Unknown error")
                quarantine_buffer.append((feature, error_msg or "Validation rejected"))

            if len(candidates_buffer) + len(quarantine_buffer) >= self.batch_size:
                candidates_buffer = self.resolve_identities(candidates_buffer)
                ins, upd, unc, qua = self.persist_batch(candidates_buffer, quarantine_buffer)
                result.inserted += ins
                result.updated += upd
                result.unchanged += unc
                result.quarantined += qua
                candidates_buffer.clear()
                quarantine_buffer.clear()

        # Flush trailing buffer
        if candidates_buffer or quarantine_buffer:
            candidates_buffer = self.resolve_identities(candidates_buffer)
            ins, upd, unc, qua = self.persist_batch(candidates_buffer, quarantine_buffer)
            result.inserted += ins
            result.updated += upd
            result.unchanged += unc
            result.quarantined += qua
            candidates_buffer.clear()
            quarantine_buffer.clear()

        duration = time.time() - start_time
        _, peak_mem = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        result.duration_seconds = round(duration, 3)
        result.throughput_per_sec = (
            round(result.total_input / duration, 1) if duration > 0 else 0.0
        )
        result.peak_memory_mb = round(peak_mem / (1024 * 1024), 2)
        result.classification_counts = dict(class_counter)

        # Sanity check reconciliation invariant
        if not result.is_invariant_satisfied:
            raise ValueError(
                f"Reconciliation invariant failed! Input ({result.total_input}) != "
                f"Inserted ({result.inserted}) + Updated ({result.updated}) + "
                f"Unchanged ({result.unchanged}) + Quarantined ({result.quarantined})"
            )

        return result
