# GLITTERS / GOD'S EYE — M4 ARCHITECTURE SPECIFICATION
## Deliverable 2: Spatio-Temporal Data Model & Lineage Architecture

**Document Reference:** `docs/engineering/M4_TEMPORAL_SPATIAL_MODEL.md`
**Classification:** Core Architectural Standard
**Milestone:** M4 — Advanced Spatial Intelligence & Digital Twin Ecosystem
**Target Systems:** GLITTERS Core (`apps.spatial`), PostGIS 3.5, GOD'S EYE Workstation (`glitters-ui-lab`)

---

## 1. Executive Summary & Problem Formulation

Traditional GIS systems model spatial data as static snapshots: an entity either exists at its current geometry or is overwritten during subsequent imports. This failure mode results in permanent loss of historical built-environment context, inability to track construction progress or unauthorized encroachments, and corruption of legal audit trails.

GLITTERS M4 establishes an immutable, bi-temporal spatio-temporal data foundation. Every physical and legal entity in GLITTERS is modeled across three distinct temporal dimensions:
1. **Valid Time (Physical Reality):** The interval during which the entity existed or was configured in the real world.
2. **Transaction Time (System Truth):** The interval during which the knowledge was asserted and active within the database.
3. **Observation Time (Sensor Reality):** The discrete instant at which an optical satellite, airborne drone, ground LiDAR, surveyor total station, or human inspector captured the underlying evidence.

```
Temporal Dimension Space:
┌────────────────────────────────────────────────────────────────────────┐
│ VALID TIME (Physical World Interval)                                   │
│ [valid_from ────────────────────────────────────────────── valid_to]   │
└──────────────────────────────────▲─────────────────────────────────────┘
                                   │
┌──────────────────────────────────┴─────────────────────────────────────┐
│ TRANSACTION TIME (Database Assertion Interval)                         │
│ [recorded_at ──────────────────────────────────────── superseded_at]   │
└──────────────────────────────────▲─────────────────────────────────────┘
                                   │
┌──────────────────────────────────┴─────────────────────────────────────┐
│ OBSERVATION TIME (Sensor / Evidence Instant)                           │
│ [observed_at]  (e.g., Satellite pass, Drone flight, Total-station fix)  │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Temporal Dimensions Specification

### 2.1 Schema Definition & PostgreSQL Range Types

To guarantee microsecond-accurate time intervals and efficient indexing, GLITTERS utilizes PostgreSQL native range types (`tstzrange`) paired with GiST indices (`btree_gist` extension).

```python
# Conceptual representation in apps/spatial/domain.py & models.py

from django.contrib.gis.db import models
from django.contrib.postgres.fields import DateTimeRangeField
from django.utils import timezone
import uuid

class TemporalSpatialEntity(models.Model):
    """
    Abstract foundation for bi-temporally tracked spatial entities.
    Enforces valid-time and transaction-time intervals with audit provenance.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    canonical_urn = models.CharField(max_length=255, db_index=True)

    # 1. Valid Time: When did this feature exist in the physical world?
    valid_from = models.DateTimeField(db_index=True)
    valid_to = models.DateTimeField(null=True, blank=True, db_index=True)

    # 2. Transaction Time: When did GLITTERS assert this record?
    recorded_at = models.DateTimeField(default=timezone.now, db_index=True)
    superseded_at = models.DateTimeField(null=True, blank=True, db_index=True)

    # 3. Observation Time: When was the physical evidence captured?
    observed_at = models.DateTimeField(null=True, blank=True, db_index=True)

    # Provenance and Confidence Classification
    confidence_tier = models.CharField(
        max_length=32,
        choices=[
            ('UNVERIFIED_OPEN_DATA', 'Tier 0: Unverified Open Source (e.g. OSM)'),
            ('ALGORITHMIC_INFERRED', 'Tier 1: Machine Learning Inferred (AI Detection)'),
            ('SENSOR_OBSERVED', 'Tier 2: Direct Sensor Observation (Photogrammetry/LiDAR)'),
            ('SURVEYOR_VERIFIED', 'Tier 3: Licensed Surveyor Ground Truth'),
            ('AUTHORITATIVE_CADASTRE', 'Tier 4: Statutory Government Cadastre (Ministry of Lands)'),
        ],
        default='UNVERIFIED_OPEN_DATA',
        db_index=True
    )
    confidence_score = models.FloatField(default=0.50) # 0.00 to 1.00

    # Dataset and Ingestion Lineage
    dataset_version = models.ForeignKey(
        'spatial.DatasetVersion',
        on_delete=models.PROTECT,
        related_name='temporal_entities'
    )
    source_identity = models.CharField(max_length=255, null=True, blank=True, db_index=True)

    class Meta:
        abstract = True
        indexes = [
            models.Index(fields=['canonical_urn', 'valid_from', 'valid_to']),
            models.Index(fields=['canonical_urn', 'recorded_at', 'superseded_at']),
        ]
```

### 2.2 Temporal Invariants
1. **Valid Interval Consistency:** `valid_to IS NULL OR valid_to > valid_from`.
2. **Transaction Interval Consistency:** `superseded_at IS NULL OR superseded_at > recorded_at`.
3. **Current Truth Assertion:** A record is considered current in the system if and only if `superseded_at IS NULL`.
4. **Point-in-Time Non-Overlapping Invariant:** For any given `canonical_urn`, no two records with `superseded_at IS NULL` may have overlapping `[valid_from, valid_to)` intervals.

---

## 3. Spatio-Temporal Physical Lifecycle Events

Physical structures and cadastral plots undergo continuous transformation. GLITTERS models state transitions via explicit lifecycle events rather than destructive updates.

```
Lifecycle State Machine:
┌─────────────────┐       Permit Issued / Site Cleared
│   PLANNED       ├─────────────────────────────────────────┐
└────────┬────────┘                                         │
         │ Groundbreaking Observed                          │
         ▼                                                  ▼
┌─────────────────┐       Structural Progression      ┌───────────────┐
│ UNDER_CONST     ├──────────────────────────────────►│ COMPLETED     │
└────────┬────────┘                                   └───────┬───────┘
         │ Abandoned / Work Halted                            │
         ▼                                                    │ Renovation /
┌─────────────────┐                                           │ Subdivision
│ STALLED         │                                           ▼
└─────────────────┘                                   ┌───────────────┐
         │ Demolition / Clearance                     │ MODIFIED      │
         └───────────────────────────────────────────►└───────┬───────┘
                                                              │
                                                              ▼
                                                      ┌───────────────┐
                                                      │ DEMOLISHED    │
                                                      └───────────────┘
```

### 3.1 Event Taxonomy
1. **APPEARANCE (Groundbreaking / Construction):**
   - Triggered when optical change detection, drone orthophoto, or survey report identifies a new footprint not present in previous epochs.
   - Action: Create new `SpatialEntity` with `valid_from = observation_date`, `lifecycle_status = 'UNDER_CONSTRUCTION'`.
2. **MODIFICATION (Geometry Expansion / Vertical Addition):**
   - Triggered when footprint expands (e.g. extension built) or height increases.
   - Action: Close previous version by setting `valid_to = observation_date`. Insert successor record with new geometry, incremented revision, and `valid_from = observation_date`.
3. **SOURCE DISAPPEARANCE vs. DEMOLITION:**
   - *Source Disappearance:* Feature was present in OSM dataset $V_1$ but omitted in OSM dataset $V_2$. Does **not** mean physical demolition; it indicates source divergence. The entity is marked `SOURCE_ABSENT` but retained in the canonical twin.
   - *Physical Demolition:* Sensor imagery confirms structural collapse or parcel clearing. Marked `DEMOLISHED` with `valid_to = observation_date`.
4. **CADASTRAL SUBDIVISION / CONSOLIDATION:**
   - *Subdivision:* A parent parcel is divided into two or more daughter parcels.
   - *Consolidation:* Multiple parcels are merged into a single registered title.
   - Modeled via `EntityLineage` DAG (Directed Acyclic Graph) preserving parent-to-child lineage.

---

## 4. Cadastral Lineage Model (`EntityLineage`)

To trace how land parcels evolve over decades of titles, mutations, subdivisions, and court-ordered boundary realignments, GLITTERS enforces a formal lineage graph:

```sql
CREATE TABLE spatial_entity_lineage (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    parent_entity_id UUID NOT NULL REFERENCES spatial_entity(id) ON DELETE RESTRICT,
    child_entity_id UUID NOT NULL REFERENCES spatial_entity(id) ON DELETE RESTRICT,
    lineage_type VARCHAR(32) NOT NULL, -- 'SUBDIVISION', 'CONSOLIDATION', 'REALIGNMENT', 'REPLACED_BY_SURVEY'
    effective_date DATE NOT NULL,
    legal_instrument_reference VARCHAR(128), -- Survey Plan No / Deed Plan / Title Number
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    confidence_tier VARCHAR(32) NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX idx_lineage_parent ON spatial_entity_lineage(parent_entity_id);
CREATE INDEX idx_lineage_child ON spatial_entity_lineage(child_entity_id);
```

### 4.1 Lineage Traversal Query (Recursive CTE)
To find all descendant parcels stemming from an ancestral grant:
```sql
WITH RECURSIVE parcel_ancestry AS (
    -- Anchor member: root parcel
    SELECT parent_entity_id, child_entity_id, lineage_type, effective_date, 1 AS depth
    FROM spatial_entity_lineage
    WHERE parent_entity_id = '018f3a9e-0000-7000-8000-000000000001'

    UNION ALL

    -- Recursive member: daughter parcels
    SELECT l.parent_entity_id, l.child_entity_id, l.lineage_type, l.effective_date, pa.depth + 1
    FROM spatial_entity_lineage l
    INNER JOIN parcel_ancestry pa ON l.parent_entity_id = pa.child_entity_id
    WHERE pa.depth < 10 -- Cycle protection
)
SELECT * FROM parcel_ancestry ORDER BY depth, effective_date;
```

---

## 5. Temporal Query Engine & Time-Travel Execution

GOD'S EYE requires seamless point-in-time rendering and time-lapse replay.

### 5.1 Point-in-Time (AS OF) Spatial Query
Retrieve all buildings in a neighborhood as they physically existed on June 1st, 2024, based on the knowledge known to the system today:

```sql
SELECT
    e.id,
    e.canonical_urn,
    b.height,
    b.building_type,
    ST_AsGeoJSON(e.geometry) as geometry
FROM spatial_entity e
JOIN spatial_building b ON e.id = b.spatialentity_ptr_id
WHERE
    -- Valid Time constraint (physical reality)
    e.valid_from <= '2024-06-01T00:00:00Z'
    AND (e.valid_to IS NULL OR e.valid_to > '2024-06-01T00:00:00Z')
    -- Transaction Time constraint (current system truth)
    AND e.superseded_at IS NULL
    -- Spatial Bounding Box constraint
    AND e.geometry && ST_MakeEnvelope(39.60, -4.08, 39.75, -4.00, 4326);
```

### 5.2 System As-Was (Retroactive Audit) Query
Retrieve what the database told an analyst on January 15th, 2025, regardless of later corrections:

```sql
SELECT
    e.id,
    e.canonical_urn,
    b.building_type,
    e.recorded_at,
    e.superseded_at
FROM spatial_entity e
JOIN spatial_building b ON e.id = b.spatialentity_ptr_id
WHERE
    e.recorded_at <= '2025-01-15T00:00:00Z'
    AND (e.superseded_at IS NULL OR e.superseded_at > '2025-01-15T00:00:00Z')
    AND e.canonical_urn = 'urn:glitters:entity:building:ke:msa:osm:way:123456789';
```

---

## 6. Multi-Tier Confidence & Verification Pipeline

Physical truth is probabilistic; legal truth is statutory. GLITTERS preserves this distinction across 5 rigorous confidence tiers:

| Tier | Classification | Typical Error ($\sigma$) | Source Examples | Legal Admissibility |
|:---|:---|:---|:---|:---|
| **Tier 0** | `UNVERIFIED_OPEN_DATA` | $\pm 3.0\text{ m} - 10.0\text{ m}$ | OpenStreetMap crowd tags | Exploratory / Contextual only |
| **Tier 1** | `ALGORITHMIC_INFERRED` | $\pm 1.5\text{ m} - 5.0\text{ m}$ | YOLOv11 / SAM satellite building masks | Unconfirmed intelligence |
| **Tier 2** | `SENSOR_OBSERVED` | $\pm 0.1\text{ m} - 0.5\text{ m}$ | RTK Drone photogrammetry, aerial LiDAR | Engineering grade |
| **Tier 3** | `SURVEYOR_VERIFIED` | $\pm 0.01\text{ m} - 0.05\text{ m}$ | Licensed surveyor GNSS total-station survey | Court-admissible ground truth |
| **Tier 4** | `AUTHORITATIVE_CADASTRE`| $\pm 0.005\text{ m} - 0.02\text{ m}$| Ministry of Lands Cadastral Deed Registry | Conclusive statutory legal title |

### 6.1 Conflict Resolution Strategy
When an incoming dataset conflicts geometrically with an existing canonical entity:
1. **Lower Tier vs. Higher Tier:** A Tier 0 (OSM) footprint *never* overwrites or displaces a Tier 3 (Surveyor) or Tier 4 (Cadastral) boundary. The Tier 0 observation is tagged as `CONFUSED_BY_HIGHER_AUTHORITY` and mapped without altering the canonical geometry.
2. **Same Tier Conflict:** When two Tier 2 drone surveys show geometric variation ($> 1.0\text{ m}$), a spatial dispute event is registered in `spatial_quarantine` or `SpatialDisputeAudit`, prompting human-in-the-loop verification.

---

## 7. Storage Engine & Partitioning Strategy

Because bi-temporal modeling accumulates revisions over time, table bloat must be prevented.
- **Partitioning Strategy:** Declarative Range Partitioning by `valid_from` (annual intervals for historical archives, sub-partitioned by County FIPS/URN).
- **Active Hot Table:** `valid_to IS NULL AND superseded_at IS NULL` maintained via partial GiST index:
  ```sql
  CREATE INDEX idx_spatial_entity_active_hot
  ON spatial_entity USING GIST (geometry)
  WHERE valid_to IS NULL AND superseded_at IS NULL;
  ```
This design guarantees that active workstation queries run at sub-millisecond speeds over millions of records, while historical time-travel scans touch only partitioned cold tables.
