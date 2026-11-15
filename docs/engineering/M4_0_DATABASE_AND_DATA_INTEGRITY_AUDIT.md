# GLITTERS / GOD'S EYE — ARCHITECTURAL RED-TEAM AUDIT
## M4.0 Database, Provenance & Data Integrity Reconciliation Audit

**Document Reference:** `docs/engineering/M4_0_DATABASE_AND_DATA_INTEGRITY_AUDIT.md`
**Classification:** Mandatory Data Integrity Audit Gate
**Database Instance:** PostgreSQL 16.9 + PostGIS 3.5.2 (`glitters` database on port 5434)
**Execution Environment:** Production-Equivalent Django Ingestion Engine (`apps.spatial`)

---

## 1. Executive Reconciliation Summary

A complete, non-destructive database audit was executed across all spatial tables to verify data integrity following the M3.3 production ingestion of the Mombasa OpenStreetMap dataset.

The database state reconciles exactly:
$$\text{SpatialEntity Count (72,866)} = \text{Mombasa OSM Features (72,864)} + \text{Pre-Existing Test Fixtures (2)}$$

```
Reconciliation Flow:
┌────────────────────────────────────────────────────────┐
│ Canonical SpatialEntity: 72,866 Rows                   │
├───────────────────────────┬────────────────────────────┤
│ 72,864 Ingested Mombasa   │ 2 Pre-Existing Synthetic   │
│ OSM Building Footprints   │ Tenancy Test Fixtures      │
├───────────────────────────┼────────────────────────────┤
│ - Building: 72,864        │ - Building: 2              │
│ - IdentityMapping: 72,864 │ - IdentityMapping: 0       │
│ - SourceFeature: 72,864   │ - SourceFeature: 0         │
│ - Quarantine: 0           │ - Quarantine: 0            │
└───────────────────────────┴────────────────────────────┘
```

---

## 2. Table-by-Table Exact Inventory & Reconciliation

| Model / Table | Expected Count | Actual Count | Match Status | Verification Command Evidence |
|:---|:---|:---|:---|:---|
| `SpatialEntity` (`spatial_entity`) | 72,866 | 72,866 | **EXACT MATCH** | `SpatialEntity.objects.count()` |
| `Building` (`spatial_building`) | 72,866 | 72,866 | **EXACT MATCH** | `Building.objects.count()` |
| `SpatialIdentityMapping` (`spatial_identity_mapping`) | 72,864 | 72,864 | **EXACT MATCH** | `SpatialIdentityMapping.objects.count()` |
| `SpatialSourceFeature` (`spatial_source_feature`) | 72,864 | 72,864 | **EXACT MATCH** | `SpatialSourceFeature.objects.count()` |
| `SpatialQuarantine` (`spatial_quarantine`) | 0 | 0 | **EXACT MATCH** | `SpatialQuarantine.objects.count()` |
| `DatasetRegistry` (`spatial_dataset_registry`) | 4 | 4 | **EXACT MATCH** | `DatasetRegistry.objects.count()` |
| `DatasetVersion` (`spatial_dataset_version`) | 5 | 5 | **EXACT MATCH** | `DatasetVersion.objects.count()` |
| `DatasetQualityReport` (`spatial_dataset_quality_report`) | 1 | 1 | **EXACT MATCH** | `DatasetQualityReport.objects.count()` |
| `EntityLineage` (`spatial_entity_lineage`) | 3 | 3 | **EXACT MATCH** | `EntityLineage.objects.count()` |

---

## 3. Pre-Existing Synthetic Fixture Audit

Two pre-existing canonical records were created during M3.2 security testing (`tests/security/test_spatial_tenancy.py`) and verified to precede the Mombasa OSM ingestion:

### Record 1: Private Tenant A Building
- **Entity ID:** `5e4b56d0-5be7-4351-87ac-cdf9caa3300e`
- **Canonical URN:** `urn:glitters:spatial:building:5e4b56d0-5be7-4351-87ac-cdf9caa3300e`
- **Entity Type:** `building`
- **Organization ID:** `0060ec60-3af6-4218-96c0-91ca4885a29e` (Assigned to Organization "Private A")
- **Creation Time:** `2026-11-13 23:36:16.250852+00:00`
- **Building Attributes:** `name='Private A'`, `classification='OTHER'`, `height_m=None`, `geom_type=MultiPolygon`
- **Source Feature Link:** None (Synthetic test fixture; 0 source features, 0 identity mappings).

### Record 2: Public Shared Building
- **Entity ID:** `9092c9ca-be2f-452c-b8b1-19c1178c806b`
- **Canonical URN:** `urn:glitters:spatial:building:9092c9ca-be2f-452c-b8b1-19c1178c806b`
- **Entity Type:** `building`
- **Organization ID:** `None` (Public unassigned building)
- **Creation Time:** `2026-11-13 23:36:16.243118+00:00`
- **Building Attributes:** `name='Public'`, `classification='OTHER'`, `height_m=None`, `geom_type=MultiPolygon`
- **Source Feature Link:** None (Synthetic test fixture; 0 source features, 0 identity mappings).

*Conclusion:* Neither synthetic record belongs to the Mombasa OpenStreetMap dataset, and neither record was mutated or corrupted during ingestion.

---

## 4. Production Dataset Version & Provenance Audit

### 4.1 Production Dataset Registry
- **Registry ID:** `5401c604-8bec-44a8-af53-93f395c8653d`
- **Dataset URN:** `urn:glitters:dataset:ke:msa:osm:buildings`
- **Name:** Mombasa OSM Building Footprints
- **Category:** `BUILDING_FOOTPRINTS`

### 4.2 Production Dataset Version (`2026.11-v1`)
- **Version ID:** `6f464719-35d2-4c62-8e57-127f56a13462`
- **Version Tag:** `2026.11-v1`
- **Status:** `PUBLISHED`
- **Source Checksum (SHA-256):** `953ad716384f772639ce0f04a32a0dff0a712bb9f7f345782924ee8bdb63a5fb`
- **Feature Count:** 72,864
- **Quality Report:** 72,864 valid geometries, 0 repaired, 0 quarantined, 0 duplicates.
- **Ingestion Velocity:** 521.7s runtime at 139.7 features/second.

---

## 5. Structural & Referential Integrity Checks

### 5.1 Orphan Audit
1. **Orphaned Buildings (Child without Parent):**
   ```sql
   SELECT count(*) FROM spatial_building b
   LEFT JOIN spatial_entity e ON b.spatialentity_ptr_id = e.id
   WHERE e.id IS NULL;
   -- Result: 0
   ```
2. **Unspecialized Entities (Parent without Building Child):**
   ```sql
   SELECT count(*) FROM spatial_entity e
   LEFT JOIN spatial_building b ON e.id = b.spatialentity_ptr_id
   WHERE b.spatialentity_ptr_id IS NULL;
   -- Result: 0
   ```
3. **Unmapped Source Features (Raw record without Identity Mapping):**
   ```sql
   SELECT count(*) FROM spatial_source_feature sf
   LEFT JOIN spatial_identity_mapping im ON sf.source_record_id = im.provider_external_id
   WHERE im.id IS NULL;
   -- Result: 0
   ```
4. **Duplicate External IDs in Mapping Table:**
   - Total rows: 72,864.
   - Distinct `provider_external_id`: 72,864.
   - Duplicate count: **0**.
5. **Duplicate Canonical URNs:**
   - Total rows: 72,864.
   - Distinct `canonical_urn`: 72,864.
   - Duplicate count: **0**.

---

## 6. Crucial Architectural Distinctions & Invariant Guarantees

### 6.1 Source Identity vs. Canonical Identity Separation
- **Source Identity:** Refers to the foreign provider's identifier (e.g. `provider_name = 'OPENSTREETMAP'`, `provider_external_id = 'way/100078028'`). Stored exclusively in `SpatialIdentityMapping` and `SpatialSourceFeature`.
- **Canonical Identity:** Refers to GLITTERS internal persistent URN (e.g. `urn:glitters:spatial:building:018f3a9e-0000-7000-8000-000000000042`).
- *Guarantee:* If an external provider changes their ID, deletes their record, or re-numbers their geometries, the canonical URN remains immutable in GLITTERS.

### 6.2 Source Disappearance vs. Physical Demolition
- When a future dataset import $V_2$ lacks a feature present in $V_1$:
  - The reconciliation engine marks the disposition as `ABSENT_FROM_SOURCE` or retains the existing entity.
  - **Invariant:** Source disappearance *never* triggers physical demolition (`lifecycle_status = 'DEMOLISHED'`). Physical demolition requires empirical sensor evidence (e.g. LiDAR or photogrammetry confirming clearance) or statutory demolition order.

### 6.3 Idempotency Invariant Verification
A full dry-run of the identical 72,864 GeoJSON source file executed against the database produced:
- `INSERTED`: 0
- `UPDATED`: 0
- `UNCHANGED`: 72,864
- `QUARANTINED`: 0
- Net database write delta: **0 rows**.
This guarantees that repeated ingestion runs cannot create duplicate entities or corrupt existing canonical URNs.
