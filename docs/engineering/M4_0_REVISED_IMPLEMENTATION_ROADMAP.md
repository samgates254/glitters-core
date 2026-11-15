# GLITTERS / GOD'S EYE — ARCHITECTURAL RED-TEAM AUDIT
## M4.0 Revised Implementation Roadmap & Execution Plan

**Document Reference:** `docs/engineering/M4_0_REVISED_IMPLEMENTATION_ROADMAP.md`
**Classification:** Master Phased Engineering Plan
**Scope:** Controlled, gated implementation sequence for M4
**Status:** PROPOSED FOR IMPLEMENTATION AUTHORIZATION

---

## 1. Executive Strategy & Scope Bounding

The original 10-week roadmap proposed simultaneous development across multiple cutting-edge domains (computer vision, STAC servers, 3D tiles, LLM agents) without addressing core database query flaws or integrating existing engines.

The **Revised Roadmap** adopts a strict **Gate-Controlled Engineering Approach**:
- **Sprint 1 (M4.1):** Zero greenfield ML or raster services. Focus exclusively on **Foundation Stabilization**, resolving parent/child MTI query mismatches, bi-temporal schema enhancements (`superseded_at`), and time-travel query filters.
- **Sprint 2 (M4.2):** Deliver deterministic PostGIS geometric metrics and automated building-parcel topological intersection.
- **Sprint 3 (M4.3):** Integrate `glitters_geospatial_engine` and deploy a standalone TiTiler raster sidecar.
- **Sprint 4 (M4.4):** Deploy the `ChangeCandidate` quarantine queue and GOD'S EYE surveyor verification interface.
- **Sprint 5 (M4.5):** Deploy the parameter-validated Spatial Assistant.

```
Revised Phased Timeline:
[Sprint 1: M4.1 Foundation] ──► Gate 4.1 ──► [Sprint 2: M4.2 Analytics] ──► Gate 4.2
                                                                                 │
[Sprint 5: M4.5 Assistant]  ◄── Gate 4.4 ◄── [Sprint 4: M4.4 Changes]   ◄── Gate 4.3 ◄── [Sprint 3: M4.3 Engines & Raster]
```

---

## 2. Sprint-by-Sprint Execution Specifications

### Sprint 1: M4.1 — Foundation Stabilization & Bi-Temporal Query Engine
- **Objective:** Fix MTI query contradictions, add bi-temporal fields, and enable point-in-time time-travel queries.
- **Duration:** 10 Working Days (Weeks 1 – 2)
- **Work Packages:**
  1. **WP-4.1.1 (MTI Query Refactor):** Audit all spatial querysets and raw SQL in `apps.spatial`. Ensure all geometry operations target `Building.geom` (or `LandParcel.geom`), completely eliminating invalid `e.geometry` references.
  2. **WP-4.1.2 (Bi-Temporal Schema Migration):** Add `superseded_at` (DateTimeField, nullable, db_indexed) to `SpatialEntity` via non-destructive Django migration.
  3. **WP-4.1.3 (Time-Travel API Engine):** Add `?as_of=YYYY-MM-DD` and `?as_was=YYYY-MM-DD` query parameter support to `BuildingViewSet` and the dynamic vector tile endpoint `/api/v1/spatial/buildings/tiles/{z}/{x}/{y}/`.
  4. **WP-4.1.4 (Lineage Traversal Endpoint):** Expose recursive URN-based lineage traversal API (`/api/v1/spatial/lineage/<urn>/`) using the existing `EntityLineage` table.
- **Rollback Strategy:** Revert migration `0004` (dropping `superseded_at`); spatial tables and the 72,866 ingested rows remain 100% intact.
- **Acceptance Gate 4.1:** All 380 baseline tests pass, new bi-temporal tests pass, and `as_of` filters return mathematically verified historical slices.

---

### Sprint 2: M4.2 — Core Spatial Analytics & Parcel Topological Linkage
- **Objective:** Connect physical building footprints to registered land parcels and compute core geometric metrics.
- **Duration:** 10 Working Days (Weeks 3 – 4)
- **Work Packages:**
  1. **WP-4.2.1 (PostGIS Geometric Service):** Implement `apps/spatial/services/metrics.py` computing area (EPSG:32737), perimeter, isoperimetric compactness ($Q$), and estimated GFA via optimized database SQL.
  2. **WP-4.2.2 (Topological Intersection Worker):** Author Celery worker executing `ST_Intersection` between `spatial_building.geom` and `spatial_land_parcel.geom`, populating `BuildingParcelIntersection` with exact overlap metrics.
  3. **WP-4.2.3 (Road Reserve Setback Engine):** Author spatial service checking distance to nearest `spatial_road` centerline, flagging structures within 6m.
  4. **WP-4.2.4 (GOD'S EYE Workstation Inspector Integration):** Connect `glitters-ui-lab` right inspector to live metrics API, displaying real-time PostGIS metrics upon clicking any 3D/2D building.
- **Acceptance Gate 4.2:** Metric calculation returns in $< 25\text{ ms}$; parcel intersections accurately link buildings with $> 80\%$ topological overlap.

---

### Sprint 3: M4.3 — Geospatial Engine Integration & Standalone Raster Sidecar
- **Objective:** Integrate `glitters_geospatial_engine` without polluting Python 3.14 core, and deploy containerized TiTiler raster server.
- **Duration:** 10 Working Days (Weeks 5 – 6)
- **Work Packages:**
  1. **WP-4.3.1 (Engine Adapter Layer):** Create adapter in `apps/spatial/adapters/` importing mathematical metrics and spectral index formulas from `glitters_geospatial_engine`.
  2. **WP-4.3.2 (Containerized TiTiler Deployment):** Deploy lightweight FastAPI/rio-tiler container running Python 3.11 with GDAL wheels, isolated from the Django core process.
  3. **WP-4.3.3 (COG Ingestion Pipeline):** Convert sample Mombasa drone orthomosaics to Cloud-Optimized GeoTIFFs (COG) with internal tiling and ZSTD compression.
  4. **WP-4.3.4 (GOD'S EYE Basemap Selector):** Add dynamic raster XYZ layer in `glitters-ui-lab` layer registry.
- **Acceptance Gate 4.3:** Raster tiles resolve in $< 45\text{ ms}$; zero GDAL/C-extension dependencies in `glitters-core` environment.

---

### Sprint 4: M4.4 — Change Detection Pipeline & Surveyor HITL Interface
- **Objective:** Deploy quarantined change detection and surveyor verification UI in GOD'S EYE.
- **Duration:** 10 Working Days (Weeks 7 – 8)
- **Work Packages:**
  1. **WP-4.4.1 (ChangeCandidate Model & FSM):** Create `ChangeCandidate` quarantine table with statuses `PENDING`, `IN_REVIEW`, `ACCEPTED`, `REJECTED_FALSE_POSITIVE`.
  2. **WP-4.4.2 (Qualified Change Taxonomy):** Enforce non-conclusive terminology (`CANDIDATE_SETBACK_ANOMALY`, `UNVERIFIED_NEW_STRUCTURE`).
  3. **WP-4.4.3 (Surveyor Review Interface):** Build review pane in GOD'S EYE workstation showing side-by-side historical image chips and digital signature submission.
  4. **WP-4.4.4 (Canonical Promotion Service):** Transactional promotion pipeline converting accepted candidates into canonical `Building` records.
- **Acceptance Gate 4.4:** Zero direct canonical mutations from AI; 100% of promotions signed by verified surveyor credentials.

---

### Sprint 5: M4.5 — Secure Spatial Assistant Orchestrator
- **Objective:** Deploy natural language spatial assistant in GOD'S EYE with strict anti-hallucination guardrails.
- **Duration:** 10 Working Days (Weeks 9 – 10)
- **Work Packages:**
  1. **WP-4.5.1 (Typed Tool Registry):** Implement JSON-schema tool caller mapping natural language requests to fixed Django service calls.
  2. **WP-4.5.2 (Anti-Hallucination & Permission Gate):** Prohibit LLM from generating raw SQL; enforce tenant context and read-only execution.
  3. **WP-4.5.3 (Workstation Navigation Dispatcher):** Connect assistant to GOD'S EYE camera controls (fly-to, highlight, time-slider).
- **Acceptance Gate 4.5:** 100% of spatial answers grounded in PostGIS queries; zero SQL injection or cross-tenant leakage vulnerabilities.
