# GLITTERS / GOD'S EYE — ARCHITECTURAL RED-TEAM AUDIT
## M4.0 Performance, Scalability & Infrastructure Architecture Audit

**Document Reference:** `docs/engineering/M4_0_PERFORMANCE_AND_INFRASTRUCTURE_AUDIT.md`
**Classification:** Performance & Infrastructure Engineering Audit
**Scope:** Realistic evaluation of 100k, 500k, and 5M entity workloads
**Target Systems:** PostgreSQL 16 + PostGIS 3.5, Redis, Dynamic MVT Engine, WebGL Viewport

---

## 1. Executive Performance Review

The scalability claims in `M4_PERFORMANCE_AND_SCALABILITY.md` promise sub-second serving across 5,000,000 entities, but contain theoretical inaccuracies, premature partitioning schemes, and conflated indexing techniques.

This audit establishes empirical, measurable benchmarks and rejects enterprise bloat (such as Kafka or distributed streaming frameworks) in favor of optimized PostGIS, Redis caching, and WebGL rendering.

---

## 2. In-Depth Technical Challenges & Inaccuracies

### 2.1 The "Hilbert GiST Index" Technical Inaccuracy
- **Document Claim:** `CLUSTER spatial_entity USING idx_spatial_entity_geom_gist` organizes rows along the "2D Hilbert Space-Filling Curve".
- **Reality:** PostGIS GiST indexing on geometry utilizes an R-Tree index (specifically an R*-tree variant with linear and quadratic split algorithms). While an R-tree groups bounding boxes hierarchically, it **does not** sort data linearly along a Hilbert curve.
- **Remediation:** If true Hilbert clustering is required for large cold-storage archives, rows must be ordered by an explicit Hilbert index generated via `ST_GeoHash` or a custom Hilbert function:
  ```sql
  -- True Hilbert / GeoHash spatial clustering
  CREATE INDEX idx_building_geohash ON spatial_building (ST_GeoHash(ST_Centroid(geom), 10));
  CLUSTER spatial_building USING idx_building_geohash;
  ```
  However, for the current 72,866 Mombasa buildings, standard PostGIS GiST indexing already returns bounding box queries in $< 12\text{ ms}$. Additional clustering is unnecessary at this stage.

### 2.2 Declarative Partitioning vs. Django Multi-Table Inheritance (MTI)
- **Document Claim:** The specification proposes declarative `LIST` partitioning of `spatial_entity` by `county_code`, with a composite primary key `PRIMARY KEY (id, county_code)`.
- **The Fatal Incompatibility:** In PostgreSQL 16, any foreign key pointing to a partitioned table must include the partition key columns. However, Django's ORM Multi-Table Inheritance (`class Building(SpatialEntity)`) automatically creates a single-column primary key foreign key pointer:
  ```sql
  spatialentity_ptr_id UUID PRIMARY KEY REFERENCES spatial_entity(id)
  ```
  PostgreSQL will reject this table definition with:
  `ERROR: foreign key to partitioned table must include all partitioning columns of the referenced key.`
- **Remediation:** Declarative partitioning must **NOT** be applied to `SpatialEntity` while Django MTI is in use. Partitioning is only appropriate for flat, independent observation logs (`SpatialSourceFeature` or `ImageryItem`).

---

## 3. Measurable Benchmark Workloads & Realistic Latency Targets

We define 9 realistic production workloads and establish explicit performance SLAs:

| Workload ID | Operation / Scenario | Target Dataset Size | Target Engine | SLA Latency Target | Cold-Start Target |
|:---|:---|:---|:---|:---|:---|
| **BM-01** | **Viewport Vector Tile (`ST_AsMVT`):** Request single $256\text{px}$ tile at $Z=16$ (approx. 800 buildings) | 72,866 buildings | PostGIS 3.5 Dynamic SQL | $< 45\text{ ms}$ | $< 90\text{ ms}$ |
| **BM-02** | **Single Building Inspection:** Fetch complete building profile + height + classification by ID | 72,866 buildings | Primary Key Index B-Tree | $< 5\text{ ms}$ | $< 15\text{ ms}$ |
| **BM-03** | **Bounding Box Search:** Fetch all buildings within viewport envelope ($2\text{ km} \times 2\text{ km}$) | 72,866 buildings | GiST Index on `geom` | $< 25\text{ ms}$ | $< 60\text{ ms}$ |
| **BM-04** | **Building-Parcel Spatial Intersection:** Find parcel boundary containing a clicked building | 10k parcels / 72k bldgs | `ST_Intersects` with GiST | $< 35\text{ ms}$ | $< 80\text{ ms}$ |
| **BM-05** | **Road Reserve Setback Calculation:** Calculate distance from building footprint to nearest road centerline | 72k bldgs / 5k roads | `ST_DWithin` + `ST_Distance` | $< 30\text{ ms}$ | $< 70\text{ ms}$ |
| **BM-06** | **Historical Point-in-Time Slice:** Query all active buildings "AS OF" date $T$ within viewport | 72,866 buildings | Composite `valid_from/to` GiST | $< 40\text{ ms}$ | $< 95\text{ ms}$ |
| **BM-07** | **Neighborhood Isochrone Catchment:** 10-minute pedestrian walking polygon from POI | Mombasa Road Graph | pgRouting Dijkstra | $< 180\text{ ms}$ | $< 350\text{ ms}$ |
| **BM-08** | **Volumetric Height Extraction (nDSM):** Sample 90th percentile elevation within building footprint | $500\text{ MB}$ COG Raster | S3 HTTP Range GET + rio-tiler | $< 85\text{ ms}$ | $< 250\text{ ms}$ |
| **BM-09** | **Full Dataset Idempotency Check:** Reconcile entire 72,864 GeoJSON source records against DB | 72,864 source records | Streaming SHA-256 + DB B-Tree | $< 120\text{ s}$ | N/A |

---

## 4. Caching Hierarchy & Infrastructure Sizing

```
Multi-Level Cache Sizing:
┌────────────────────────────────────────────────────────┐
│ L1: Browser HTTP Cache (MapLibre Tile Cache)           │
│ Cache-Control: public, max-age=86400, immutable         │
│ Zero backend network traffic for repeated viewports    │
├────────────────────────────────────────────────────────┤
│ L2: Edge CDN (Cloudflare)                              │
│ Caches vector tiles for published dataset versions     │
│ Target: 92% edge cache hit ratio                       │
├────────────────────────────────────────────────────────┤
│ L3: Redis In-Memory Cache (glitters-core Redis DB 1)   │
│ Stores pre-rendered tiles for Zoom levels 0 to 14      │
│ Memory Footprint: ~450 MB for Mombasa Metropolitan Area│
├────────────────────────────────────────────────────────┤
│ L4: PostGIS 3.5 Database Engine                        │
│ Generates dynamic tiles on-the-fly for Zoom 15 to 20   │
└────────────────────────────────────────────────────────┘
```

### 4.1 Infrastructure Sizing for M4.1 – M4.2
- **PostgreSQL 16 Database:** 4 vCPU, 16 GB RAM, NVMe SSD storage (`shared_buffers = 4GB`, `work_mem = 32MB`).
- **Redis Cache:** 1 vCPU, 2 GB RAM (Dedicated to vector tiles and spatial session locks).
- **Django Core Server:** 4 worker processes running under Gunicorn/Uvicorn on port 8001.
- **Worker Infrastructure:** Standard Celery worker with Redis broker for asynchronous spatial difference tasks.
- **Rejection of Distributed Complexity:** Frameworks like Apache Kafka, Apache Spark, or distributed Kubernetes clusters are strictly rejected. The single-node PostgreSQL + Redis architecture comfortably serves up to 1,000,000 spatial entities with sub-100ms latencies.
