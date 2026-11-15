# GLITTERS CORE — MILESTONE M3.3
## Spatial Data Quality, Validation & Quarantine Specification

**Document:** `M3_3_DATA_QUALITY_SPEC.md`
**Classification:** Technical Architecture Specification
**Repository:** `~/Projects/glitters-core`
**Date:** 2026-11-14
**Author:** Principal Geospatial Architect & Core Engineering Review Board
**Status:** Approved for Planning — Pre-Ingestion Architecture Gate

---

## 1. Quality Objectives & Quality Gates

The Mombasa OSM ingestion pipeline enforces a zero-tolerance policy against corrupt, unprojectable, or topologically invalid spatial data. Every record passing into the canonical database must be verified through automated quality gates.

```
+-----------------------------------------------------------------------------------------+
|                                    QUALITY GATES                                        |
|                                                                                         |
|   +-----------------------+     +-----------------------+     +---------------------+   |
|   | Gate 1: Syntactic     | --> | Gate 2: Geometric     | --> | Gate 3: Geographic  |   |
|   |    JSON / Schema      |     |    GEOS Validity      |     |    Bounding Box     |   |
|   +-----------------------+     +-----------------------+     +---------------------+   |
|               |                             |                             |             |
|         (fail) v                      (fail) v                      (fail) v            |
|   +---------------------------------------------------------------------------------+   |
|   |                               Spatial Quarantine                                |   |
|   +---------------------------------------------------------------------------------+   |
+-----------------------------------------------------------------------------------------+
```

---

## 2. Gate-by-Gate Verification Rules

### Gate 1: Syntactic & Structural Schema Validation
- **Requirement:** Each record must be a valid GeoJSON `Feature` with a recognized `geometry` object and `properties` dictionary.
- **Identifier Check:** The feature must possess a valid, non-empty OSM identifier (`osm_id` as positive integer or string).
- **Geometry Type Check:** Geometry type must be `Polygon` or `MultiPolygon`.
  - Non-polygonal geometries (`Point`, `LineString`, `GeometryCollection`) representing non-building features are rejected.
  - Valid `Polygon` geometries are automatically converted to single-part `MultiPolygon` geometries to satisfy the schema constraint:
    $$\text{MultiPolygon}([P])$$

### Gate 2: Geometric & Topological Validity
- **GEOS Evaluation:** Execute `geom.valid`.
- **Topological Integrity Invariants:**
  - No self-intersections (bow-tie polygons).
  - No interior rings extending outside exterior rings.
  - Rings must be closed: first coordinate equals last coordinate.
  - Ring orientation: exterior ring must be counter-clockwise, interior holes clockwise (OGC SFS standard).
- **Automated Repair Policy:**
  - If `geom.valid` is `False`, invoke `try_repair_geometry(geom)` utilizing GEOS `make_valid()`.
  - **Acceptable Repair:** If `make_valid()` produces a valid `Polygon` or `MultiPolygon` preserving $> 90\%$ of the original planar area, the repaired geometry is accepted. The record is flagged with `repaired = True`.
  - **Unacceptable Repair:** If `make_valid()` collapses the geometry into linear/punctual elements (`LineString`, `Point`) or produces an empty geometry, the record fails Gate 2 and is diverted to quarantine.

### Gate 3: Geographic & Coordinate Envelope Validation
- **Coordinate Reference System:** Must be EPSG:4326 (WGS 84).
- **Coordinate Dimension:** 2D $(X, Y)$ coordinates. If Z-coordinates are present in raw data, they are evaluated against ellipsoidal datum or safely stripped to 2D footprint.
- **Geographic Envelope Invariant:** Coordinates must fall strictly within the Mombasa County operational envelope:
  $$\text{Longitude} \in [39.400000, 39.950000], \quad \text{Latitude} \in [-4.350000, -3.700000]$$
  Any feature outside this envelope is rejected as an out-of-bounds spatial anomaly.

---

## 3. Attribute Sanitization & Normalization

| Field | Source Tag(s) | Sanitization Rule | Fallback Value |
| :--- | :--- | :--- | :--- |
| **`name`** | `name`, `name:en` | Trim whitespace. Truncate to 255 chars. | `""` (empty string) |
| **`classification`** | `building` | Normalization mapping via `map_osm_building_classification`. | `BuildingClassification.OTHER` |
| **`height_m`** | `height` | Parse floating point. Strip units (`m`, `meters`). Reject if $\le 0$ or $> 300\text{ m}$. | `None` |
| **`levels_above_ground`** | `building:levels`, `levels` | Parse integer. Reject non-numeric or values $< 1$ or $> 100$. | `None` |
| **`roof_shape`** | `roof:shape` | Standardize lowercase tokens (`flat`, `gabled`, `hipped`, `pitched`). | `""` |
| **`facade_material`** | `building:material`, `building:facade` | Standardize tokens (`concrete`, `brick`, `glass`, `wood`). | `""` |
| **`footprint_area_sqm`** | Computed | Calculated planar area via `calculate_planar_area_utm_sqm(geom)` in EPSG:32737. | Computed on insert |

---

## 4. Quarantine Architecture & Diagnostic Retention

Any feature failing any quality gate is stored in the persistent database table `spatial_quarantine`.

### Quarantine Table Fields:
- `id`: UUID primary key.
- `source_dataset_version`: ForeignKey to the active `DatasetVersion`.
- `source_record_id`: Provider ID (e.g. `way/99887766`).
- `raw_payload`: Complete original JSON payload of the feature.
- `failure_stage`: Specific gate failure tag:
  - `SCHEMA_MALFORMED`
  - `INVALID_GEOMETRY_UNREPAIRABLE`
  - `COORDINATES_OUT_OF_BOUNDS`
  - `DIMENSION_MISMATCH`
  - `PROJECTION_ERROR`
- `error_message`: Full diagnostic string including GEOS validity reason (e.g. `Self-intersection at (39.664, -4.051)`).
- `quarantined_at`: Timestamp.

---

## 5. Strict Ingestion Reconciliation Invariant

To ensure complete accounting and mathematical verification, every ingestion run enforces the following conservation equation:

$$\mathbf{INPUT} = \mathbf{VALID} + \mathbf{REPAIRED} + \mathbf{DEDUPLICATED} + \mathbf{QUARANTINED}$$

### Rigorous Mutually Exclusive Bucket Definitions:
1. **$\mathbf{VALID}$**: Features that passed all schema, topological, and coordinate checks on the first attempt without repair, and were promoted or staged as new entities.
2. **$\mathbf{REPAIRED}$**: Features with initially invalid geometry that were successfully healed into valid polygons via `make_valid()` and promoted.
3. **$\mathbf{DEDUPLICATED}$**: Features matching an existing canonical entity with identical geometry and attributes, resulting in zero mutation.
4. **$\mathbf{QUARANTINED}$**: Features that failed syntactic parsing, unrepairable geometry checks, or coordinate boundary limits, diverted to the quarantine ledger.

**Accounting Gate:** If $\mathbf{VALID} + \mathbf{REPAIRED} + \mathbf{DEDUPLICATED} + \mathbf{QUARANTINED} \neq \mathbf{INPUT}$, the ingestion pipeline aborts with an integrity error.
