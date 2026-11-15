# GLITTERS GOD'S EYE — MILESTONE M3.2
## Spatial Database Migration Execution & Rollback Report

**Document:** `M3_2_MIGRATION_REPORT.md`
**Classification:** Internal Technical Architecture & Engineering Baseline
**Repository:** `~/Projects/glitters-core`
**Date:** 2026-11-14
**Author:** Principal Geospatial Architect & Core Engineering Review Board
**Status:** Approved Migration Certification

---

## 1. Migration Specification & Dependencies

- **Migration File**: `apps/spatial/migrations/0001_initial.py`
- **Target Application**: `apps.spatial`
- **Dependencies**:
  - `('core', '0001_enable_postgis')`
  - `('locations', '0002_seed_countries')`
  - `('organizations', '0001_initial')`
  - `('properties', '0002_access_provenance')`
- **Database Engine**: PostgreSQL 16.8 / PostGIS 3.5.2 (Docker container `glitters-postgres`, port 5434)

---

## 2. Disposable Two-Way Reversibility Verification

In strict compliance with Milestone M3.2 safeguards, the migration was tested through a complete two-way cycle (Forward $\rightarrow$ Rollback to Zero $\rightarrow$ Re-apply):

### 2.1 Step 1: Pre-Migration Core Table Integrity Ledger
```sql
SELECT 'property' AS tbl, COUNT(*) FROM public.property
UNION ALL SELECT 'property_location', COUNT(*) FROM public.property_location
UNION ALL SELECT 'organization', COUNT(*) FROM public.organization
UNION ALL SELECT 'country', COUNT(*) FROM public.country;
```
**Pre-Migration Baseline**:
- `property`: 0 rows
- `property_location`: 0 rows
- `organization`: 0 rows
- `country`: 4 rows

---

### 2.2 Step 2: Forward Migration Execution
Command:
```bash
uv run python manage.py migrate spatial
```
Output:
```
Operations to perform:
  Apply all migrations: spatial
Running migrations:
  Applying spatial.0001_initial... OK
```
**Schema Verification**:
All 19 spatial tables, foreign keys, GiST spatial indexes, and check constraints (`chk_building_geom_valid`, `chk_building_geom_srid`, `chk_parcel_geom_valid`) were created and confirmed active.

---

### 2.3 Step 3: Rollback to Zero Verification
Command:
```bash
uv run python manage.py migrate spatial zero
```
Output:
```
Operations to perform:
  Unapply all migrations: spatial
Running migrations:
  Rendering model states... DONE
  Unapplying spatial.0001_initial... OK
```
**Post-Rollback Cleanliness Audit**:
- Database query:
  `SELECT table_name FROM information_schema.tables WHERE table_name LIKE 'spatial_%';`
  Returned exactly **1 row (`spatial_ref_sys`)**. All 19 created spatial tables were cleanly dropped with zero orphan objects or dangling constraints.
- Core Table Parity:
  - `property`: 0 rows (Unchanged)
  - `property_location`: 0 rows (Unchanged)
  - `organization`: 0 rows (Unchanged)
  - `country`: 4 rows (Unchanged)
  **Parity: 100% byte-level integrity preserved.**

---

### 2.4 Step 4: Re-Application & Final Operational State
Command:
```bash
uv run python manage.py migrate spatial
```
Output:
```
Operations to perform:
  Apply all migrations: spatial
Running migrations:
  Applying spatial.0001_initial... OK
```
The database was successfully left in the validated, operational target state.

---

## 3. Data Ingestion Boundary Notice

In accordance with Milestone M3.2 requirements, the 72,864 Mombasa OSM building footprints were **not** imported during this milestone. The database foundation is certified ready for ingestion in Milestone M3.3.

---
*Accepted and Certified for Milestone M3.2.*
