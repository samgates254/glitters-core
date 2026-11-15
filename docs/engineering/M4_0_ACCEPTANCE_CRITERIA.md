# GLITTERS / GOD'S EYE — ARCHITECTURAL RED-TEAM AUDIT
## M4.0 Acceptance Criteria & Verification Standard

**Document Reference:** `docs/engineering/M4_0_ACCEPTANCE_CRITERIA.md`
**Classification:** Quality Assurance & Acceptance Specification
**Milestone Focus:** M4.1 Foundation Stabilization & Bi-Temporal Engine
**Status:** BINDING VERIFICATION MANDATE

---

## 1. Acceptance Gates Overview

To prevent premature feature development on unstable foundations, M4.1 is subject to five rigorous, testable acceptance gates:
1. **Gate 1: Database & Data Provenance Integrity**
2. **Gate 2: Query Correctness & Schema Alignment**
3. **Gate 3: Bi-Temporal & Time-Travel Engine**
4. **Gate 4: Security & Multi-Tenant Isolation**
5. **Gate 5: Performance, Latency & Regression Protection**

Implementation authorization for subsequent milestones (M4.2 through M4.5) is strictly contingent upon passing all five gates.

---

## 2. Gate 1: Database & Data Provenance Integrity

| Verification Criterion | Verification Method | Expected Result | Pass / Fail Rule |
|:---|:---|:---|:---|
| **AC-1.1: Canonical Entity Count** | `SpatialEntity.objects.count()` | Exactly 72,866 rows | Any variation fails gate |
| **AC-1.2: Building Entity Count** | `Building.objects.count()` | Exactly 72,866 rows | Any variation fails gate |
| **AC-1.3: Identity Mapping Count** | `SpatialIdentityMapping.objects.count()` | Exactly 72,864 rows | Any variation fails gate |
| **AC-1.4: Source Feature Count** | `SpatialSourceFeature.objects.count()` | Exactly 72,864 rows | Any variation fails gate |
| **AC-1.5: Zero Orphan Records** | SQL Left Join between child and parent tables | 0 orphaned building or mapping rows | Any count $> 0$ fails gate |
| **AC-1.6: Ingestion Idempotency** | Re-run ingestion command with `--batch-size 1000` | 72,864 UNCHANGED, 0 INSERTED, 0 UPDATED | Any write fails gate |
| **AC-1.7: Synthetic Fixture Preservation** | Query private fixture `5e4b56d0-5be7-4351-87ac-cdf9caa3300e` | Retains org `0060ec60-3af6-4218-96c0-91ca4885a29e` and geometry | Any mutation fails gate |

---

## 3. Gate 2: Query Correctness & Schema Alignment

| Verification Criterion | Verification Method | Expected Result | Pass / Fail Rule |
|:---|:---|:---|:---|
| **AC-2.1: Elimination of `e.geometry`** | Codebase-wide grep for `e.geometry` in `apps.spatial` | Zero occurrences in active Python code or raw SQL | Any occurrence fails gate |
| **AC-2.2: Child Table Geometry Targeting** | ViewSet and service tests querying `Building.geom` | Queries execute successfully without `UndefinedColumn` | Any SQL exception fails gate |
| **AC-2.3: URN-Based Lineage Traversal** | Query `EntityLineage` using `predecessor_urn` | Traverses 3-level parcel subdivision in $< 15\text{ ms}$ | Broken traversal fails gate |
| **AC-2.4: Clean Django Checks** | Execute `uv run python manage.py check` | `System check identified no issues (0 silenced)` | Any warning/error fails gate |
| **AC-2.5: Zero Unapplied Migrations** | Execute `uv run python manage.py showmigrations` | All spatial migrations marked `[X]` | Any unapplied migration fails |

---

## 4. Gate 3: Bi-Temporal & Time-Travel Engine

| Verification Criterion | Verification Method | Expected Result | Pass / Fail Rule |
|:---|:---|:---|:---|
| **AC-3.1: `superseded_at` Field Exists** | Model reflection on `SpatialEntity` | Field exists, is nullable DateTime, and has `db_index=True` | Missing field fails gate |
| **AC-3.2: Point-in-Time Slice (`as_of`)** | Request `GET /api/v1/spatial/buildings/?as_of=2024-01-01` | Returns only entities where `valid_from <= 2024-01-01` and (`valid_to IS NULL OR valid_to > 2024-01-01`) | Incorrect entities fails gate |
| **AC-3.3: Vector Tile Time-Travel** | Request `/api/v1/spatial/buildings/tiles/16/19245/16120/?as_of=2024-01-01` | PostGIS MVT dynamically clips features active on that date | Failure to filter fails gate |
| **AC-3.4: Active State Invariant** | Query `superseded_at IS NULL AND valid_to IS NULL` | Returns 72,866 active canonical entities | Any deviation fails gate |

---

## 5. Gate 4: Security & Multi-Tenant Isolation

| Verification Criterion | Verification Method | Expected Result | Pass / Fail Rule |
|:---|:---|:---|:---|
| **AC-4.1: Cross-Org Read Isolation** | Authenticate as Tenant B; query Tenant A building | HTTP 404 Not Found or excluded from GeoJSON | Any leakage fails gate |
| **AC-4.2: Public Layer Shared Read** | Unauthenticated request for Mombasa OSM buildings | HTTP 200 OK with public building footprints | Denial fails gate |
| **AC-4.3: Connection Context Hygiene** | Execute 100 alternating multi-tenant requests in loop | Zero cross-tenant leakage across reused connections | Any leaked row fails gate |
| **AC-4.4: Read-Only Assistant Sandbox** | Attempt `DROP TABLE` or `UPDATE` in assistant query | Database permissions reject write operations | Write execution fails gate |

---

## 6. Gate 5: Performance, Latency & Regression Protection

| Verification Criterion | Verification Method | Benchmark Target | Pass / Fail Rule |
|:---|:---|:---|:---|
| **AC-5.1: Vector Tile Serving Latency** | Benchmark `GET /api/v1/spatial/buildings/tiles/16/19245/16120/` | P95 latency $< 45\text{ ms}$ on local DB | P95 $> 90\text{ ms}$ fails gate |
| **AC-5.2: Single Building Lookup Latency** | Benchmark `GET /api/v1/spatial/buildings/lookup/?urn=...` | P95 latency $< 10\text{ ms}$ | P95 $> 25\text{ ms}$ fails gate |
| **AC-5.3: Viewport Bounding Box Latency**| Benchmark `GET /api/v1/spatial/buildings/?bbox=...` | P95 latency $< 35\text{ ms}$ | P95 $> 80\text{ ms}$ fails gate |
| **AC-5.4: Baseline Test Regression Zero**| Execute `uv run pytest` across entire test suite | Exactly 380 passed, 0 failed, 0 errors | Any failing test fails gate |
| **AC-5.5: Spatial Test Suite 100%** | Execute `uv run pytest tests/*spatial*` | 100% passing rate | Any failing test fails gate |
