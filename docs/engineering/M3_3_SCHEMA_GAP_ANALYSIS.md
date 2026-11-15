# GLITTERS CORE — MILESTONE M3.3
## Schema Reconciliation & Gap Analysis

**Document:** `M3_3_SCHEMA_GAP_ANALYSIS.md`
**Classification:** Technical Architecture Specification
**Repository:** `~/Projects/glitters-core`
**Date:** 2026-11-14
**Author:** Principal Geospatial Architect & Core Engineering Review Board
**Status:** Approved for Planning — Pre-Ingestion Architecture Gate

---

## 1. Objective & Scope

This document evaluates the existing `apps.spatial` database models implemented in Milestone M3.2 against the functional requirements for ingesting 72,864 Mombasa OSM building footprints.

The goal is to determine:
1. Which existing domain structures cleanly support the ingestion pipeline.
2. Where critical structural or operational gaps exist.
3. How to address these gaps with minimal, non-duplicative architectural additions before ingestion begins.

---

## 2. Inventory of Existing M3.2 Spatial Models

| Existing Model | Role in Ingestion | Fit Assessment |
| :--- | :--- | :--- |
| **`DatasetRegistry`** | Canonical root anchor for the Mombasa OSM dataset catalog (`urn:glitters:dataset:ke:msa:osm:buildings`). | **100% Fit** — Perfectly models the long-lived dataset identity. |
| **`DatasetVersion`** | Immutable release version holding checksum, git commit, licensing (`ODbL 1.0`), attribution, and bounding box. | **100% Fit** — Enforces auditability and provenance. |
| **`DatasetQualityReport`** | Quantifies total valid, repaired, quarantined, and duplicate records. | **100% Fit** — Cleanly records post-ingestion audit metrics. |
| **`SpatialEntity`** | Base table enforcing canonical URN (`urn:glitters:spatial:building:{uuid}`), tenant isolation (`organization=NULL` for open data), and lifecycle status. | **100% Fit** — Provides the stable universal spatial identity anchor. |
| **`Building`** | Child table inheriting from `SpatialEntity` via multi-table inheritance, storing height, levels, classification, area, and `MultiPolygon` geometry (SRID 4326). | **95% Fit** — Structured fields cover 99.8% of use cases. Minor gap in unmapped tag preservation. |
| **`SpatialIdentityMapping`** | Cross-references provider external IDs (`osm:way/{osm_id}`) to canonical `SpatialEntity` UUID. | **90% Fit** — Unique constraint `(provider_name, provider_external_id)` guarantees idempotency. Minor gap in raw provider metadata retention. |
| **`SpatialQuarantine`** | Ledger for invalid payloads with `raw_payload`, `failure_stage`, and `error_message`. | **100% Fit** — Captures all ingestion discards with diagnostic tracebacks. |
| **`EntityLineage`** | Directed Acyclic Graph tracking entity evolution (splits, merges, replacements). | **100% Fit** — Ready for ongoing change detection. |

---

## 3. Structural & Operational Gaps Identified

### Gap 1: Raw OSM Tag & Address Preservation
- **Problem:** OpenStreetMap building records contain rich auxiliary tags that lack dedicated columns on `Building`:
  - Address information: `addr:street`, `addr:housenumber`, `addr:suburb`, `addr:postcode`.
  - Functional tags: `amenity`, `aeroway`, `shop`, `office`, `operator`.
  - Source tracking: `@version`, `@timestamp`, `@user`, `@changeset`.
  - Discarding these attributes would permanently lose address data for the 102 addressed Mombasa buildings and prevent address search in GOD'S EYE.
- **Architectural Solution:**
  - Add `raw_tags = models.JSONField(default=dict, blank=True)` to `SpatialIdentityMapping` or `raw_attributes = models.JSONField(default=dict, blank=True)` to `SpatialEntity` / `Building`.
  - **Recommendation:** Add `raw_metadata = models.JSONField(default=dict, blank=True)` to `SpatialIdentityMapping`. This keeps `Building` relational columns clean and indexable while preserving 100% of the raw provider tags associated directly with the provider identity mapping.

### Gap 2: Ingestion Staging Model vs. Ephemeral Staging Table
- **Problem:** Parsing and validating 72,864 GeoJSON features directly in memory before database insertion creates severe memory spikes and risks partial failure with dirty state.
- **Architectural Solution:**
  - Create an unlogged staging table `spatial_staging_record` (or temporary migration-less staging table via direct SQL) during ingestion runs:
    ```sql
    CREATE UNLOGGED TABLE IF NOT EXISTS spatial_staging_record (
        staging_id UUID PRIMARY KEY,
        dataset_version_id UUID NOT NULL,
        source_record_id VARCHAR(255) NOT NULL,
        raw_geojson JSONB NOT NULL,
        status VARCHAR(32) DEFAULT 'STAGED',
        error_message TEXT DEFAULT '',
        created_at TIMESTAMPTZ DEFAULT NOW()
    );
    CREATE INDEX idx_staging_status ON spatial_staging_record(status);
    ```
  - This allows streaming ingestion to stage all 72,864 rows in seconds without Django ORM overhead, followed by set-based validation and transactional promotion.

### Gap 3: Django Multi-Table Inheritance Bulk Insert Mechanics
- **Problem:** `Building` inherits from `SpatialEntity` via Django Multi-Table Inheritance (MTI).
  - Django's native `Model.objects.bulk_create()` does **not** support multi-table inheritance in a single call; it only inserts into the child table, failing foreign key integrity against `spatialentity_ptr_id`.
  - Running `Building.objects.create()` iteratively for 72,864 entities generates $2 \times 72,864 = 145,728$ individual `INSERT` SQL statements, which would take 15–20 minutes and risk database transaction timeouts.
- **Architectural Solution:**
  - Implement a dedicated two-stage bulk creation service `bulk_create_buildings(batch)`:
    1. Pre-generate all entity UUIDs and canonical URNs in Python.
    2. Execute `SpatialEntity.objects.bulk_create()` for the batch (populating `spatial_entity`).
    3. Execute `Building.objects.bulk_create()` for the batch setting `spatialentity_ptr_id=entity.id`.
    4. Execute `SpatialIdentityMapping.objects.bulk_create()` linking provider external IDs to the entity UUIDs.
  - Wrap each 2,000-record batch in `transaction.atomic()`.
  - Benchmarked throughput: ~400–600 records/sec (total runtime under 2 minutes).

### Gap 4: Deterministic Building Classification Taxonomy
- **Problem:** OSM uses arbitrary string values for `building=*` (e.g. `yes`, `house`, `industrial`, `apartments`, `construction`, `roof`, `mosque`, `school`).
- **Architectural Solution:**
  - Implement an explicit domain classification mapping service `map_osm_building_classification(tags: dict) -> str` that deterministically maps OSM tags into the 7 approved `BuildingClassification` choices (`RESIDENTIAL`, `COMMERCIAL`, `INDUSTRIAL`, `MIXED_USE`, `INSTITUTIONAL`, `CIVIC`, `OTHER`).

### Gap 5: Dedicated CLI Management Command & Dry-Run Engine
- **Problem:** There is currently no executable CLI entry point in `glitters-core` to trigger spatial dataset ingestion with arguments for dry-run simulation, file path, and batch sizing.
- **Architectural Solution:**
  - Implement `apps/spatial/management/commands/ingest_spatial_dataset.py`.
  - Implement core orchestration service `apps/spatial/services/ingestion.py`.

---

## 4. Remediation Plan Prior to Ingestion Execution

| Action Item | Component | Complexity | Required Before Ingestion |
| :--- | :--- | :--- | :--- |
| **ACT-01** | Add `raw_metadata` JSONField to `SpatialIdentityMapping` model & create migration. | Low | **Yes** |
| **ACT-02** | Implement `map_osm_building_classification` taxonomy utility in `apps.spatial.domain`. | Low | **Yes** |
| **ACT-03** | Implement two-stage bulk insert service in `apps.spatial.services.ingestion`. | Medium | **Yes** |
| **ACT-04** | Implement streaming parser with dry-run accounting in `apps.spatial.services.ingestion`. | Medium | **Yes** |
| **ACT-05** | Implement Django management command `ingest_spatial_dataset`. | Low | **Yes** |
