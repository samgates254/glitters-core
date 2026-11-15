# GLITTERS / GOD'S EYE — ARCHITECTURAL RED-TEAM AUDIT
## M4.0 Implementation Priority Matrix & Dependency Graph

**Document Reference:** `docs/engineering/M4_0_IMPLEMENTATION_PRIORITY_MATRIX.md`
**Classification:** Engineering Prioritization Standard
**Scope:** Strict classification of all M4 capabilities across P0, P1, P2, and P3 tiers
**Status:** BINDING EXECUTION DIRECTIVE

---

## 1. Capability Priority Classification

### P0 — Foundation (Mandatory for Data Integrity, Security & Correctness)
Capabilities that must be resolved before expanding any new spatial intelligence features:

| Capability ID | Capability Name | Business Purpose | Technical Dependencies | Existing Implementation | Estimated Complexity | Core Risks | Required Tests | Acceptance Criteria |
|:---|:---|:---|:---|:---|:---|:---|:---|:---|
| **P0-1** | **MTI Query Alignment** | Fix SQL/ORM queries assuming `e.geometry` on parent table | None (uses existing models) | Models exist; queries in docs flawed | Low (1-2 days) | Runtime SQL errors if uncorrected | Unit tests on `Building.geom` queries | Zero `UndefinedColumn` errors across all endpoints |
| **P0-2** | **Bi-Temporal Schema Stabilization** | Enable point-in-time "AS OF" and audit "AS WAS" tracking | `SpatialEntity` model | `valid_from/to` exist; `superseded_at` missing | Low (2-3 days) | Inaccurate historical reconstructions | Migration tests, time-travel query tests | `?as_of=YYYY-MM-DD` returns exact historical time-slice |
| **P0-3** | **Tenant Context Isolation Verification** | Ensure multi-tenant queries never leak private assets | Django ORM / Auth | `organization_id` exists; passes 380 tests | Low (1 day) | Cross-tenant data leakage | Security penetration tests with multi-org fixtures | 100% isolation in cross-org join queries |
| **P0-4** | **Reconciliation & Idempotency Lock** | Prevent duplicate entity creation during re-ingestion | PostGIS Unique Index | Verified: 72,864 mapped, 0 duplicates | None (Verified) | Duplicate URNs | Idempotency regression tests | Second ingestion yields exactly 0 writes and 0 new URNs |

---

### P1 — Core Capability (Required for First Spatial Intelligence Release)
High-value capabilities ready to build on the stabilized P0 foundation:

| Capability ID | Capability Name | Business Purpose | Technical Dependencies | Existing Implementation | Estimated Complexity | Core Risks | Required Tests | Acceptance Criteria |
|:---|:---|:---|:---|:---|:---|:---|:---|:---|
| **P1-1** | **Geometric Analytics Service** | Compute area, perimeter, compactness ($Q$), and GFA | PostGIS 3.5 functions | Formulas defined in docs | Medium (3-4 days) | Inaccurate area if projected incorrectly | CRS projection tests (EPSG:32737) | Returns metrics in $< 25\text{ ms}$ for any building |
| **P1-2** | **Cadastral Parcel Association Engine** | Link physical buildings to underlying land parcels | `LandParcel`, `BuildingParcelIntersection` | Models exist with 0 rows | Medium (4-5 days) | Ambiguous overlap on multi-lot parcels | Topological intersection tests | Populates intersection table with exact overlap sqm |
| **P1-3** | **GOD'S EYE 3D Inspection Upgrade** | Display live building height, GFA, and parcel links | REST API / Next.js | Inspector card exists in UI | Low (2-3 days) | UI state desynchronization | Frontend component and Cypress tests | Clicking 3D building reveals live PostGIS metrics |
| **P1-4** | **Road Reserve Setback Analysis** | Detect structures within 6m of gazetted road reserves | `spatial_road` geometry | Schema exists; roads unpopulated | Medium (3-4 days) | False positives on curved roads | Buffer difference tests | Computes distance to nearest road centerline in $< 30\text{ ms}$ |

---

### P2 — Advanced Capability (Dependent on Proven P0/P1 Foundations)
Capabilities that provide deep intelligence but require external data or sidecar services:

| Capability ID | Capability Name | Business Purpose | Technical Dependencies | Existing Implementation | Estimated Complexity | Core Risks | Required Tests | Acceptance Criteria |
|:---|:---|:---|:---|:---|:---|:---|:---|:---|
| **P2-1** | **STAC & COG Raster Sidecar** | Stream drone orthomosaics and public satellite tiles | TiTiler / Python 3.11 sidecar | Architecture specified | High (2-3 weeks) | Memory leaks in raster worker | Tile response time tests | Serves WebP raster tiles in $< 45\text{ ms}$ |
| **P2-2** | **Change Candidate Review Queue** | Manage and inspect detected physical changes | Celery, `ChangeCandidate` | None (Unbuilt) | High (2 weeks) | Unverified predictions polluting canonical | FSM transition and audit tests | Surveyor review interface promotes candidate with digital signature |
| **P2-3** | **pgRouting Multi-Modal Isochrones** | Pedestrian 5/10/15-min service catchments | pgRouting extension | Architecture specified | Medium (1-2 weeks)| Missing topology edges | Network traversal tests | Generates walking polygon in $< 200\text{ ms}$ |
| **P2-4** | **Natural Language Spatial Assistant** | Natural language queries in GOD'S EYE workstation | LLM API, Tool Registry | Specification authored | High (3 weeks) | Geographic hallucination | Parameter validation tests | Translates query to PostGIS call without raw SQL execution |

---

### P3 — Research / Deferred (High Cost, High Risk, or Insufficiently Justified)
Features that must be explicitly deferred from M4.1 and M4.2:

| Capability ID | Capability Name | Reason for Deferral |
|:---|:---|:---|
| **P3-1** | **PostgreSQL Declarative Partitioning** | Incompatible with Django MTI foreign keys; unnecessary for current 72,866 entity volume. |
| **P3-2** | **Commercial Satellite Live Streaming** | No enterprise procurement contract or tasking budget for Maxar/Planet feeds. |
| **P3-3** | **Universal Statutory Zoning Engine** | Planning bylaws differ across counties; requires dedicated legal rules engine. |
| **P3-4** | **In-Browser Real-Time Solar Raytracing**| High GPU memory consumption; client WebGL frame rate degradation. |
| **P3-5** | **PostgreSQL Database-Level RLS** | Connection pool context leakage hazard; application-layer isolation is currently sufficient. |

---

## 2. Master Capability Dependency Graph

```
Dependency Progression:
┌────────────────────────────────────────────────────────┐
│ P0: FOUNDATION STABILIZATION (M4.1)                    │
│ 1. MTI Query Alignment (ADR-01)                        │
│ 2. Bi-Temporal Schema (valid_from/to + superseded_at)  │
│ 3. Tenant Context Safeguards (ADR-07)                  │
│ 4. Verified Ingestion Idempotency (M3.3 Reconciled)    │
└───────────────────────────┬────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────┐
│ P1: CORE ANALYTICAL SERVICES (M4.2)                    │
│ 1. PostGIS Geometric Metrics (Area, Compactness, GFA)  │
│ 2. Building-Parcel Topological Intersection Service    │
│ 3. Road Reserve Setback Compliance Engine              │
│ 4. GOD'S EYE Workstation Real-Time Inspector Binding   │
└───────────────────────────┬────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────┐
│ P2: ADVANCED SATELLITE & AI CAPABILITIES (M4.3 - M4.5) │
│ 1. Standalone TiTiler / COG Raster Sidecar             │
│ 2. Integration of `glitters_geospatial_engine`         │
│ 3. Change Candidate Quarantine & Surveyor HITL UI      │
│ 4. Parameterized Natural Language Spatial Assistant    │
└────────────────────────────────────────────────────────┘
```
