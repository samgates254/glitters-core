# GLITTERS / GOD'S EYE — M4 ARCHITECTURE SPECIFICATION
## Deliverable 9: Performance, Scalability & Storage Optimization Architecture

**Document Reference:** `docs/engineering/M4_PERFORMANCE_AND_SCALABILITY.md`
**Classification:** Core Architectural Standard
**Milestone:** M4 — Advanced Spatial Intelligence & Digital Twin Ecosystem
**Target Systems:** PostgreSQL 16 + PostGIS 3.5, Redis, Cloudflare CDN, GOD'S EYE Workstation

---

## 1. Executive Summary & Scale Targets

GLITTERS is designed to scale horizontally and vertically from local city datasets (72,864 Mombasa buildings) to nationwide geospatial intelligence across Kenya and East Africa.

### Rigorous Performance SLAs:
- **Vector Tile Serving ($ST\_AsMVT$):**
  - Edge/CDN Cache Hit: $< 15\text{ ms}$.
  - Redis L3 Cache Hit: $< 35\text{ ms}$.
  - PostGIS Dynamic Tile Generation: $< 95\text{ ms}$ for $Z \ge 14$ containing up to 5,000 features.
- **Complex Spatial Intersections ($ST\_Intersects / ST\_DWithin$):** $< 50\text{ ms}$ within a standard urban viewport.
- **Concurrent Load:** 500+ simultaneous GOD'S EYE workstation analytical sessions without query queue degradation.
- **Scalability Targets:**
  - Milestone 3.3 (Current): 72,866 buildings (Mombasa Island & Mainland).
  - Milestone 4.2 (Nairobi Metro): 650,000+ buildings.
  - Milestone 4.5 (National Kenya Cadastre): 5,000,000+ parcels and structures.

```
Multi-Tier Caching & Serving Architecture:
┌────────────────────────────────────────────────────────┐
│ GOD'S EYE Client Workstation (MapLibre GL / Deck.gl)   │
│ [L1 Browser Cache: Cache-Control: max-age=86400]       │
└───────────────────────────┬────────────────────────────┘
                            │ Cache Miss
                            ▼
┌────────────────────────────────────────────────────────┐
│ L2 Global Edge CDN (Cloudflare / Fastly)               │
│ - Serves immutable tiles for published dataset versions│
└───────────────────────────┬────────────────────────────┘
                            │ Cache Miss
                            ▼
┌────────────────────────────────────────────────────────┐
│ L3 Redis Vector Tile In-Memory Cache                   │
│ - Key: tile:{dataset}:{z}:{x}:{y}:{filter_hash}        │
│ - Pre-warmed for Z0 - Z14 (Regional & City Overviews)  │
└───────────────────────────┬────────────────────────────┘
                            │ Cache Miss
                            ▼
┌────────────────────────────────────────────────────────┐
│ L4 High-Performance PostGIS 3.5 Engine                 │
│ - ST_TileEnvelope & ST_AsMVT Dynamic Generation        │
│ - Declarative Spatial Partitioning                     │
│ - GiST & Hilbert Curve Clustered Storage               │
└────────────────────────────────────────────────────────┘
```

---

## 2. PostgreSQL / PostGIS Spatial Indexing & Partitioning

As dataset sizes scale into millions of rows, monolithic tables degrade index traversal times and trigger full table scans. GLITTERS implements multi-tier declarative partitioning and advanced indexing.

### 2.1 Declarative Administrative Partitioning
The primary `spatial_entity` table is partitioned using declarative `LIST` partitioning by administrative county FIPS/URN:

```sql
-- Partition root
CREATE TABLE spatial_entity (
    id UUID NOT NULL DEFAULT gen_random_uuid(),
    canonical_urn VARCHAR(255) NOT NULL,
    county_code VARCHAR(16) NOT NULL, -- e.g., 'KE-01' (Mombasa), 'KE-30' (Nairobi)
    geometry GEOMETRY(Geometry, 4326) NOT NULL,
    valid_from TIMESTAMPTZ NOT NULL,
    valid_to TIMESTAMPTZ,
    superseded_at TIMESTAMPTZ,
    -- additional fields...
    PRIMARY KEY (id, county_code)
) PARTITION BY LIST (county_code);

-- County Partitions
CREATE TABLE spatial_entity_ke_mombasa PARTITION OF spatial_entity
    FOR VALUES IN ('KE-01', 'urn:glitters:admin:ke:01');

CREATE TABLE spatial_entity_ke_nairobi PARTITION OF spatial_entity
    FOR VALUES IN ('KE-30', 'urn:glitters:admin:ke:30');
```

### 2.2 Advanced Indexing Hierarchy
1. **GiST (Generalized Search Tree) with Hilbert Curve Ordering:**
   - Standard GiST indices suffer from node overlap when data is inserted arbitrarily.
   - GLITTERS clusters physical disk storage along the 2D Hilbert Space-Filling Curve:
     ```sql
     CREATE INDEX idx_spatial_entity_geom_gist ON spatial_entity USING GIST (geometry);
     CLUSTER spatial_entity USING idx_spatial_entity_geom_gist;
     ```
   - *Result:* Adjacent geographic features are stored on adjacent 8KB disk pages, reducing disk I/O seek operations by up to $70\%$.
2. **Partial GiST Index for Active State:**
   - Excludes historical superseded records from live workstation queries:
     ```sql
     CREATE INDEX idx_spatial_entity_live_geom ON spatial_entity USING GIST (geometry)
     WHERE superseded_at IS NULL AND valid_to IS NULL;
     ```
3. **BRIN (Block Range Index) for Historical Audit Logs:**
   - For append-only tables like `SpatialSourceFeature` and `CadastralSurveyMutation`, BRIN indices provide 99% smaller index footprints than B-trees while executing fast chronological range scans:
     ```sql
     CREATE INDEX idx_source_feature_brin_time ON spatial_source_feature USING BRIN (ingested_at);
     ```

---

## 3. Dynamic Vector Tile (MVT) Optimization

Sending raw geometries with high vertex counts over Web Mercator tiles degrades mobile network bandwidth and GPU frame rates.

### 3.1 Scale-Appropriate Geometry Simplification
In PostGIS `ST_AsMVT` queries, geometries are dynamically generalized using `ST_SimplifyPreserveTopology` based on the zoom level ground pixel resolution:

$$\epsilon(Z) = \frac{40075016.68 \times \cos(\text{lat})}{256 \times 2^Z} \times 0.5 \quad [\text{degrees / meters equivalent}]$$

```sql
-- Dynamic tile generation SQL in apps/spatial/views.py
WITH tile_bounds AS (
    SELECT ST_TileEnvelope(:z, :x, :y) AS env
),
simplified_features AS (
    SELECT
        b.id,
        b.canonical_urn,
        b.building_type,
        b.height,
        b.levels,
        -- Simplify geometry based on zoom level:
        -- High zoom (Z >= 17): No simplification
        -- Mid zoom (Z 14 - 16): 0.5m tolerance
        -- Low zoom (Z < 14): 2.0m tolerance
        ST_AsMVTGeom(
            CASE
                WHEN :z >= 17 THEN ST_Transform(e.geometry, 3857)
                WHEN :z >= 14 THEN ST_SimplifyPreserveTopology(ST_Transform(e.geometry, 3857), 0.5)
                ELSE ST_SimplifyPreserveTopology(ST_Transform(e.geometry, 3857), 2.0)
            END,
            tb.env,
            4096, -- 4096 x 4096 internal vector tile coordinate space
            256,  -- 256px buffer to avoid edge-clipping artifacts
            true  -- Clip geometries to tile boundary
        ) AS mvt_geom
    FROM spatial_building b
    JOIN spatial_entity e ON b.spatialentity_ptr_id = e.id
    JOIN tile_bounds tb ON e.geometry && ST_Transform(tb.env, 4326)
    WHERE e.superseded_at IS NULL
)
SELECT ST_AsMVT(simplified_features, 'buildings', 4096, 'mvt_geom') AS mvt_bytes
FROM simplified_features
WHERE mvt_geom IS NOT NULL;
```

### 3.2 Attribute Pruning
Vector tiles strip verbose nested JSON metadata (such as raw OSM string tags or internal validation logs), transmitting only high-utility styling and identification tokens (`id`, `urn`, `type`, `height`, `levels`, `status`). Full entity property profiles are lazily loaded on-demand when an analyst clicks or selects an entity in the UI.

---

## 4. Multi-Level Tile Caching Architecture

### 4.1 Cache Hierarchy & Key Formulation
Tiles are addressed by an immutable cache key incorporating the published dataset version:
```
Key: tile:v1:{dataset_slug}:{z}:{x}:{y}:{feature_filter_hash}
Example: tile:v1:msa-osm-buildings:15:19245:16120:all
```

- **Zoom 0 to 12 (Regional Views):** Fully pre-rendered and saved to Redis on dataset publication. Never hits PostgreSQL during user sessions.
- **Zoom 13 to 15 (Metropolitan Views):** Rendered on first request and cached in Redis with a 30-day TTL.
- **Zoom 16+ (Inspection Views):** Dynamic on-demand PostGIS execution with 24-hour Redis caching for active viewports.
- **Cache Invalidation:** Because dataset versions are immutable (`2026.11-v1`, `2026.11-v2`), cache invalidation is handled by simply routing tile requests to the new published dataset slug. Historical tiles are never evicted prematurely.

---

## 5. PostgreSQL Server Configuration & Performance Tuning

For production environments hosting 1,000,000+ spatial entities on standard dedicated infrastructure ($8\text{ vCPU}, 32\text{ GB RAM}$):

```ini
# PostgreSQL 16 postgresql.conf for High-Throughput PostGIS

# Memory Configuration
shared_buffers = 8GB                  # 25% of total RAM
effective_cache_size = 24GB           # 75% of total RAM
work_mem = 64MB                       # Per-query sort/join buffer
maintenance_work_mem = 2GB            # For fast GiST index rebuilds and VACUUM

# Parallel Query Execution
max_worker_processes = 8
max_parallel_workers = 8
max_parallel_workers_per_gather = 4
parallel_tuple_cost = 0.1
parallel_setup_cost = 1000.0

# PostGIS Costing Adjustments (SSD / NVMe optimized)
random_page_cost = 1.1                 # NVMe random read is near-instant
seq_page_cost = 1.0

# Statistics & Planner
default_statistics_target = 500       # Granular histograms for spatial distribution
```
