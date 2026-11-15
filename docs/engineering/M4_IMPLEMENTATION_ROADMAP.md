# GLITTERS / GOD'S EYE — M4 ARCHITECTURE SPECIFICATION
## Deliverable 10: Phased Implementation Roadmap, Risk Register & Acceptance Criteria

**Document Reference:** `docs/engineering/M4_IMPLEMENTATION_ROADMAP.md`
**Classification:** Master Execution Plan
**Milestone:** M4 — Advanced Spatial Intelligence & Digital Twin Ecosystem
**Target Systems:** GLITTERS Core (`glitters-core`), GOD'S EYE Workstation (`glitters-ui-lab`)

---

## 1. Executive Program Overview

Milestone 4 elevates GLITTERS and GOD'S EYE from an interactive geospatial map viewer into an authoritative digital twin ecosystem and spatial intelligence operating system.

The program is structured into 5 cohesive engineering phases designed to maintain zero downtime, guarantee backward compatibility, and avoid destabilizing existing production assets (such as the 72,866 ingested Mombasa entities or the multi-tenant real estate modules).

```
M4 Implementation Master Timeline:
┌────────────────────────────────────────────────────────────────────────┐
│ Phase 4.1: Spatio-Temporal Model & Lineage Engine                      │
│ Bi-temporal fields, recursive lineage graph, time-travel API           │
├────────────────────────────────────────────────────────────────────────┤
│ Phase 4.2: Digital Twin Core & 3D Streaming Mesh                       │
│ Height inference, LoD 1/2/3 3D Tiles 1.1, Deck.gl/Three.js rendering   │
├────────────────────────────────────────────────────────────────────────┤
│ Phase 4.3: Earth Observation, STAC & Dynamic Raster Engine             │
│ COG converter, STAC v1.0 catalog, TiTiler streaming, Sentinel-2/Drone   │
├────────────────────────────────────────────────────────────────────────┤
│ Phase 4.4: Spatial Change Detection & HITL Verification               │
│ Computer vision pipeline, ChangeCandidate FSM, GOD'S EYE Review UI    │
├────────────────────────────────────────────────────────────────────────┤
│ Phase 4.5: Spatial Analytics Engine & Natural Language Assistant       │
│ PostGIS analytics, pgRouting isochrones, LLM tool calling orchestrator │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Phased Execution Plan & Detailed Work Breakdown

### Phase 4.1: Spatio-Temporal Foundation & Lineage Engine
*Target Duration: Sprint 1 (Weeks 1 - 2)*
- **Objectives:**
  1. Implement bi-temporal columns (`valid_from`, `valid_to`, `recorded_at`, `superseded_at`, `observed_at`) across `SpatialEntity` and subclasses via Django migrations.
  2. Implement `EntityLineage` model for tracking cadastral subdivisions, consolidations, and boundary realignments.
  3. Author PostgreSQL temporal exclusion constraints (`EXCLUDE USING gist`) to prevent overlapping valid-time records for the same canonical URN.
  4. Expose time-travel query parameter (`?as_of=YYYY-MM-DD`) across spatial REST APIs and dynamic vector tile endpoints.
  5. Backfill existing 72,866 Mombasa OSM building records with `valid_from = 2026-11-14T00:00:00Z` and `confidence_tier = 'UNVERIFIED_OPEN_DATA'`.

### Phase 4.2: Digital Twin Core & 3D Streaming Infrastructure
*Target Duration: Sprint 2 (Weeks 3 - 4)*
- **Objectives:**
  1. Build volumetric height estimation service calculating roof elevation from `building:levels`, building classification, and photogrammetric nDSM rasters.
  2. Author OGC 3D Tiles 1.1 export pipeline generating batched 3D models (`.b3dm` / binary `.glb`) organized in a hierarchical quadtree BVH.
  3. Upgrade GOD'S EYE Next.js workstation (`glitters-ui-lab`) with Deck.gl `Tile3DLayer` and `MapLibre` 3D terrain integration.
  4. Implement client-side dynamic sun-position shadow casting engine based on geographical coordinates and local time of day.
  5. Deliver interactive 3D building inspection card showing gross volume, floor area, height, and cadastral parcel association.

### Phase 4.3: Earth Observation, STAC & Dynamic Raster Engine
*Target Duration: Sprint 3 (Weeks 5 - 6)*
- **Objectives:**
  1. Implement STAC v1.0.0 metadata models (`ImageryCollection`, `ImageryItem`) in `apps.spatial`.
  2. Deploy automated Cloud Optimized GeoTIFF (COG) conversion pipeline with ZSTD compression and multi-resolution overview generation.
  3. Embed high-performance `rio-tiler` dynamic raster server into GLITTERS Core sidecar architecture.
  4. Ingest baseline Sentinel-2 Level-2A imagery granules and historical drone orthomosaics for Mombasa Island.
  5. Add split-screen time-slider comparison tool to GOD'S EYE workstation.

### Phase 4.4: Automated Spatial Change Detection & HITL Interface
*Target Duration: Sprint 4 (Weeks 7 - 8)*
- **Objectives:**
  1. Implement `ChangeCandidate` quarantine model and transition FSM (`PENDING` $\rightarrow$ `IN_REVIEW` $\rightarrow$ `ACCEPTED` / `REJECTED`).
  2. Author Celery task pipeline for bi-temporal raster differencing and footprint appearance/demolition detection.
  3. Build construction stage classification engine (Earthworks $\rightarrow$ Foundation $\rightarrow$ Superstructure $\rightarrow$ Roof $\rightarrow$ Complete).
  4. Implement Surveyor Verification Interface in GOD'S EYE with pre-aligned raster chip comparison and digital signature submission.
  5. Author automated promotion workflow converting accepted candidates into canonical `SpatialEntity` revisions.

### Phase 4.5: Spatial Analytics Engine & Natural Language Assistant
*Target Duration: Sprint 5 (Weeks 9 - 10)*
- **Objectives:**
  1. Implement synchronous building metrics calculation service (isoperimetric compactness, solar roof potential, FAR, GCR).
  2. Implement cadastral encroachment detection checking building footprints against parcel boundaries and statutory road reserves.
  3. Deploy `pgRouting` network topology service for multi-modal pedestrian isochrones and service catchment modeling.
  4. Build LLM Spatial Assistant orchestrator with JSON-schema tool calling interface (`spatial_search_entities`, `cadastral_parcel_lookup`, etc.).
  5. Embed conversational chat interface in GOD'S EYE workstation with fly-to camera controls and viewport context synchronization.

---

## 3. Risk Register & Mitigation Matrix

| ID | Risk Description | Severity | Probability | Mitigation Strategy |
|:---|:---|:---|:---|:---|
| **R-01** | Bi-temporal table bloat slows down live queries | High | Medium | Enforce PostgreSQL declarative range partitioning by date and county; maintain partial GiST index on active records (`superseded_at IS NULL`). |
| **R-02** | 3D tile streaming exceeds browser GPU memory limits | High | High | Implement strict Screen Space Error (SSE) thresholds; quantize coordinates in glTF; cull geometries outside viewport camera frustum. |
| **R-03** | False positive storm in automated change detection | Medium | High | Isolate all AI inferences in `ChangeCandidate` quarantine; enforce minimum confidence score ($\ge 0.85$) for human queue; implement shadow suppression filters. |
| **R-04** | Dynamic raster tile server creates CPU bottlenecks | Medium | Medium | Pre-calculate overviews in COG; utilize HTTP Range Requests for partial reads; cache generated WebP tiles in Redis with 30-day TTL. |
| **R-05** | Natural language assistant outputs hallucinated spatial facts | Critical | Medium | Strict tool-calling guardrails; LLM is forbidden from calculating geometries; all facts must originate from PostGIS SQL execution. |
| **R-06** | Cross-tenant data leakage in complex spatial joins | Critical | Low | PostgreSQL Row-Level Security (RLS) policies enforced at database engine level; automated regression tests auditing tenant boundaries. |

---

## 4. Phase-by-Phase Acceptance Criteria & Sign-Off Gates

### Acceptance Gate 4.1 (Temporal Engine)
- [ ] Bi-temporal fields present on all spatial tables without breaking existing 380 unit/integration tests.
- [ ] Point-in-time query (`?as_of=2024-01-01`) accurately filters entities created after that date.
- [ ] Entity lineage recursive CTE executes in $< 15\text{ ms}$ for 10-level parcel subdivision graphs.

### Acceptance Gate 4.2 (Digital Twin & 3D)
- [ ] All 72,866 Mombasa buildings render at 60 FPS in GOD'S EYE 3D viewport using OGC 3D Tiles.
- [ ] Height estimation algorithm accurately separates single-storey dwellings from multi-storey towers.
- [ ] Solar shadow rendering matches real-world solar azimuth within $\pm 0.5^\circ$ for any selected hour.

### Acceptance Gate 4.3 (Earth Observation)
- [ ] Raw drone orthomosaic converted to compliant COG in $< 120\text{ s}$ per $1\text{ GB}$.
- [ ] TiTiler dynamic tile requests resolve in $< 45\text{ ms}$ average latency.
- [ ] STAC catalog passes 100% validation against official STAC v1.0.0 JSON schemas.

### Acceptance Gate 4.4 (Change Detection & HITL)
- [ ] Zero unverified ML predictions inserted directly into canonical tables.
- [ ] Surveyor verification in GOD'S EYE successfully promotes `ChangeCandidate` to canonical `SpatialEntity` with complete audit provenance.
- [ ] Model false-positive bank correctly captures surveyor rejections for future training runs.

### Acceptance Gate 4.5 (Analytics & Assistant)
- [ ] PostGIS analytical queries (FAR, GCR, Setbacks) return in $< 50\text{ ms}$ per building.
- [ ] Natural language assistant correctly compiles and answers complex spatial queries without geometric hallucination.
- [ ] System passes 100% of multi-tenant security isolation audits under penetration testing.
