# GLITTERS / GOD'S EYE — ARCHITECTURAL RED-TEAM AUDIT
## M4.0 Architecture Decision Records (ADRs)

**Document Reference:** `docs/engineering/M4_0_ARCHITECTURE_DECISION_RECORDS.md`
**Classification:** Authoritative Engineering Standards
**Scope:** Binding architectural decisions resolving red-team audit findings
**Status:** APPROVED FOR GOVERNANCE

---

## ADR-01: Geometry Placement in Multi-Table Inheritance Hierarchy

### Context:
The authored M4 specifications assumed `SpatialEntity` possessed a unified `geometry` column. In the actual Django implementation, `SpatialEntity` is an abstract-intent base model with no geometry field. Geometry is stored polymorphically on child tables: `Building.geom` (MultiPolygon), `LandParcel.geom` (MultiPolygon), `Road.geom` (MultiLineString).

### Decision:
1. Retain geometry fields on specialized child models (`Building.geom`, `LandParcel.geom`, `Road.geom`).
2. Do **not** add a generic geometry column to `SpatialEntity`, as doing so would cause data duplication, coordinate synchronization bugs, and loose type-safety (e.g. allowing points where polygons are required).
3. If cross-entity spatial searches (e.g., "return all features of any type within this bounding box") are needed in the future, they must be implemented via a PostGIS SQL UNION view (`spatial_entity_unified_view`) rather than mutating base tables.

### Consequences:
- **Positive:** Preserves clean spatial typing (MultiPolygon vs MultiLineString) and avoids redundant storage.
- **Negative:** Developers must join to specific child tables when querying geometries.

---

## ADR-02: Decoupled Entity Lineage via URNs vs Hard Foreign Keys

### Context:
M4 specification proposed modeling cadastral lineage (`EntityLineage`) with ForeignKeys `parent_entity_id UUID REFERENCES spatial_entity(id)` and `child_entity_id UUID REFERENCES spatial_entity(id)`. In the actual codebase, `EntityLineage` is modeled using `predecessor_urn CharField` and `successor_urn CharField`.

### Decision:
1. Retain the URN-based lineage model (`predecessor_urn`, `successor_urn`).
2. Do **not** replace URNs with direct database ForeignKeys.

### Rationale:
Cadastral parcel histories span decades and multiple independent registration systems. A predecessor parcel might exist only in a historical government survey archive or external dataset version that is not loaded into the active `spatial_entity` table. Enforcing relational ForeignKeys would block the recording of historical lineages where the predecessor record is not actively instantiated.

### Consequences:
- **Positive:** Total flexibility to record external, historical, and multi-dataset ancestry graphs.
- **Negative:** Relational integrity must be validated via application-level sanity checks.

---

## ADR-03: Separation of Physical Ground Observations from Legal Title

### Context:
Spatial GIS tools frequently conflate physical structures with legal property ownership. For instance, assuming a building polygon automatically belongs to the owner of the underlying land parcel.

### Decision:
1. Strictly separate the physical twin from the legal title:
   - `SpatialEntity` / `Building`: Empirical physical structures (shape, height, location).
   - `LandParcel`: Cadastral boundary registered under the Land Registration Act.
   - `Property`: Commercial and financial asset profile (`apps.properties`).
2. Spatial intersection (`BuildingParcelIntersection`) indicates only physical topological overlap, **never** legal ownership.
3. Title ownership is established exclusively through statutory title registers, deed plans, or authenticated lease agreements (`PropertyBuildingAssociation`).

### Consequences:
- **Positive:** Prevents legally catastrophic assumptions in sectional title properties, informal settlements, and encroachment disputes.

---

## ADR-04: Qualified AI Change Candidate Taxonomy vs. Conclusive Legal Declarations

### Context:
The M4 change detection specification proposed labels such as `ENCROACHMENT_VIOLATION`. In practice, machine learning models frequently produce false positives caused by roof eaves, building shadows, or drone georeferencing shifts ($< 0.5\text{ m}$).

### Decision:
1. Machine learning pipelines are forbidden from asserting definitive legal terms.
2. The change detection taxonomy is permanently updated to qualified terms:
   - Replace `ENCROACHMENT_VIOLATION` $\rightarrow$ `CANDIDATE_SETBACK_ANOMALY`.
   - Replace `ILLEGAL_CONSTRUCTION` $\rightarrow$ `UNVERIFIED_NEW_STRUCTURE`.
   - Replace `UNAUTHORIZED_EXPANSION` $\rightarrow$ `HORIZONTAL_FOOTPRINT_OFFSET`.
3. An empirical candidate can only be promoted to a verified physical event or statutory violation following Human-in-the-Loop (HITL) review by a licensed surveyor.

---

## ADR-05: Isolation of C-Extension Raster Processing to Independent Sidecars

### Context:
`glitters-core` is pinned to Python 3.14 (`>=3.14,<3.15`). Installing heavy C-extension geospatial libraries (`GDAL`, `rasterio`, `scipy`) directly into the Django web environment risks build failures due to immature binary wheels on Linux.

### Decision:
1. `glitters-core` web and API workers will **not** install GDAL or rasterio directly.
2. Heavy raster operations, Cloud Optimized GeoTIFF (COG) generation, and dynamic tile serving will be delegated to an independent sidecar service (such as TiTiler running in a Python 3.11/3.12 container) or containerized Celery worker.
3. The Django backend interacts with raster intelligence strictly via HTTP STAC APIs or pre-rendered tile protocols.

---

## ADR-06: Reuse of `glitters-engines` vs Core Duplication

### Context:
The M4 specifications proposed re-implementing algorithms for NDVI, spectral indices, network routing, and change detection inside `glitters-core`. However, `glitters-engines` already contains tested implementations in `glitters_geospatial_engine`.

### Decision:
1. `glitters-core` shall treat `glitters_geospatial_engine` as an upstream dependency for mathematical, remote-sensing, and analytical algorithms.
2. Zero duplicate implementations of spectral indices, network analysis, or geometry metrics will be authored in `glitters-core`.

---

## ADR-07: PostgreSQL RLS Deferral and Transactional Scoping Standard

### Context:
Enabling PostgreSQL Row-Level Security (RLS) using session-level `SET app.current_tenant_id` creates severe connection-pool leakage risks across web requests.

### Decision:
1. Defer database-level PostgreSQL RLS in M4.1.
2. Maintain tenant isolation at the application layer through `organization_id` filtering in Django QuerySets, validated by automated security tests.
3. When database RLS is implemented in future phases, it must strictly use `SET LOCAL` inside atomic transaction blocks with a fail-closed default policy.

---

## ADR-08: Prohibition of Declarative Partitioning on MTI Base Tables

### Context:
PostgreSQL 16 declarative partitioning requires the partition key to be part of the primary key and all referencing foreign keys. Django's Multi-Table Inheritance (MTI) creates single-column foreign key pointers (`spatialentity_ptr_id REFERENCES spatial_entity(id)`), making declarative partitioning impossible without breaking Django's ORM.

### Decision:
1. Do **not** partition `spatial_entity` or its MTI child tables (`Building`, `LandParcel`).
2. Scale the database through partial GiST indexes, BRIN indexes for append-only logs, and Redis tile caching.
3. Declarative partitioning is permitted only on flat, un-inherited tables (e.g., `SpatialSourceFeature`).
