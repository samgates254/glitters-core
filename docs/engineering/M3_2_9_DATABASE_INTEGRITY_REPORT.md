# GLITTERS CORE — MILESTONE M3.2.9
## PostGIS Database Integrity & Geometry Enforcement Report

**Document:** `M3_2_9_DATABASE_INTEGRITY_REPORT.md`
**Classification:** Internal Technical Architecture & Engineering Quality Gate
**Repository:** `~/Projects/glitters-core`
**Date:** 2026-11-14
**Author:** Principal Geospatial Architect & Core Engineering Review Board
**Status:** Audit Certified — Database Constraints Verified

---

## 1. Database & Extension Environment

Direct empirical inspection of the running database container (`glitters-postgres` on port 5434) confirms:

- **Database Name**: `glitters`
- **PostgreSQL Version**:
  `PostgreSQL 16.9 (Debian 16.9-1.pgdg110+1) on x86_64-pc-linux-gnu, 64-bit`
- **PostGIS Full Version**:
  `POSTGIS="3.5.2 dea6d0a" [EXTENSION] PGSQL="160" GEOS="3.9.0-CAPI-1.16.2" PROJ="7.2.1" LIBXML="2.9.10" LIBJSON="0.15" LIBPROTOBUF="1.3.3" WAGYU="0.5.0 (Internal)" TOPOLOGY`

---

## 2. Geometry Column Inventory & SRID Verification

Querying PostGIS `geometry_columns` across the database confirms:

```
+---------------------------------+-------------------+-----------------+------+-----------------+
| Table Name                      | Geometry Column   | Coord Dimension | SRID | Geometry Type   |
+---------------------------------+-------------------+-----------------+------+-----------------+
| spatial_administrative_boundary | geom              | 2               | 4326 | MULTIPOLYGON    |
| spatial_building                | geom              | 2               | 4326 | MULTIPOLYGON    |
| spatial_construction_site       | geom              | 2               | 4326 | POLYGON         |
| spatial_dataset_version         | spatial_extent    | 2               | 4326 | POLYGON         |
| spatial_imagery_observation     | geom_footprint    | 2               | 4326 | POLYGON         |
| spatial_infrastructure_asset    | geom              | 2               | 4326 | GEOMETRY        |
| spatial_land_parcel             | geom              | 2               | 4326 | MULTIPOLYGON    |
| spatial_road                    | geom              | 2               | 4326 | MULTILINESTRING |
| spatial_survey_observation      | geom_point_z      | 3               | 4326 | POINT (3D)      |
| spatial_terrain_feature         | geom              | 2               | 4326 | GEOMETRY        |
+---------------------------------+-------------------+-----------------+------+-----------------+
```

---

## 3. Spatial GiST Indexes

Querying `pg_indexes` confirms 10 dedicated GiST indexes:

1. `spatial_administrative_boundary_geom_dd30db5b_id` on `spatial_administrative_boundary (geom)`
2. `spatial_building_geom_ce938d14_id` on `spatial_building (geom)`
3. `spatial_construction_site_geom_92113448_id` on `spatial_construction_site (geom)`
4. `spatial_dataset_version_spatial_extent_de2cb969_id` on `spatial_dataset_version (spatial_extent)`
5. `spatial_imagery_observation_geom_footprint_6f4535d4_id` on `spatial_imagery_observation (geom_footprint)`
6. `spatial_infrastructure_asset_geom_8dfd6772_id` on `spatial_infrastructure_asset (geom)`
7. `spatial_land_parcel_geom_53406de3_id` on `spatial_land_parcel (geom)`
8. `spatial_road_geom_db2301f3_id` on `spatial_road (geom)`
9. `spatial_survey_observation_geom_point_z_09217ef7_id` on `spatial_survey_observation (geom_point_z gist_geometry_ops_nd)` *(3D bounding-box spatial index)*
10. `spatial_terrain_feature_geom_7a631a95_id` on `spatial_terrain_feature (geom)`

---

## 4. Database-Level Geometry Enforcement Tests

To verify that unvalidated data cannot enter production tables during bulk loading (`COPY`) or raw SQL, direct SQL insertions were executed against `spatial_building`, bypassing all Django model validation.

### Test 1: Self-Intersecting Invalid Polygon (Bowtie)
- **SQL Payload**:
  `ST_SetSRID(ST_GeomFromText('MULTIPOLYGON(((0 0, 0 2, 2 0, 2 2, 0 0)))'), 4326)`
- **Database Response**:
  `psycopg.errors.CheckViolation: new row for relation "spatial_building" violates check constraint "chk_building_geom_valid"`
- **Result**: **REJECTED & ENFORCED**.

### Test 2: Incorrect SRID (3857 instead of 4326)
- **SQL Payload**:
  `ST_SetSRID(ST_GeomFromText('MULTIPOLYGON(((39.6 0, 39.6 1, 39.7 1, 39.7 0, 39.6 0)))'), 3857)`
- **Database Response**:
  `psycopg.errors.InvalidParameterValue: Geometry SRID (3857) does not match column SRID (4326)`
- **Result**: **REJECTED & ENFORCED**.

### Test 3: NULL Geometry in NOT NULL Column
- **SQL Payload**:
  `VALUES (..., NULL)`
- **Database Response**:
  `psycopg.errors.NotNullViolation: null value in column "geom" of relation "spatial_building" violates not-null constraint`
- **Result**: **REJECTED & ENFORCED**.

### Test 4: Incorrect Geometry Type (Point into MultiPolygon)
- **SQL Payload**:
  `ST_SetSRID(ST_GeomFromText('POINT(39.664 -4.062)'), 4326)`
- **Database Response**:
  `psycopg.errors.InvalidParameterValue: Geometry type (Point) does not match column type (MultiPolygon)`
- **Result**: **REJECTED & ENFORCED**.

---
*Audit Certified for Milestone M3.2.9.*
