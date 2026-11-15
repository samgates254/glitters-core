# GLITTERS GOD'S EYE — MILESTONE M3.2
## Spatial Database Schema Implementation & Acceptance Report

**Document:** `M3_2_SCHEMA_REPORT.md`
**Classification:** Internal Technical Architecture & Engineering Baseline
**Repository:** `~/Projects/glitters-core`
**Date:** 2026-11-14
**Author:** Principal Geospatial Architect & Core Engineering Review Board
**Status:** Approved Operational Schema

---

## 1. Executive Summary

Milestone M3.2 successfully establishes the foundational spatial database infrastructure inside `glitters-core`. Moving beyond static prototype files, the platform now possesses an enterprise-grade PostGIS spatial domain anchored by the **Concrete Base Entity Pattern**, **Immutable Dataset Versioning**, **Multi-Provider Identity Mapping**, and **Strict Database-Level Geometry Integrity**.

The implementation was applied, validated, and verified against the running PostgreSQL 16.8 + PostGIS 3.5.2 engine (`glitters-postgres`).

---

## 2. Implemented Schema Architecture & Table Inventory

The dedicated PostgreSQL schema `spatial` comprises 19 operational tables and views:

```
+---------------------------------------------------------------------------------------------------------+
|                                        OPERATIONAL TABLE INVENTORY                                      |
+---------------------------------------+-----------------------------+-----------------------------------+
| Table Name                            | Primary Geometry Column     | Domain Role                       |
+---------------------------------------+-----------------------------+-----------------------------------+
| spatial_dataset_registry              | N/A                         | Catalog of spatial data products  |
| spatial_dataset_version               | spatial_extent (Polygon)    | Immutable releases & SHA-256 hash |
| spatial_dataset_quality_report        | N/A                         | Validation & audit metrics ledger |
| spatial_entity                        | N/A                         | Canonical UUID anchor & tenancy   |
| spatial_identity_mapping              | N/A                         | External IDs (OSM, GERS, Cadastre)|
| spatial_entity_lineage                | N/A                         | Directed Acyclic Lineage Graph    |
| spatial_land_parcel                   | geom (MultiPolygon)         | Cadastral parcels & legal titles  |
| spatial_building                      | geom (MultiPolygon)         | 2D/3D physical building footprints|
| spatial_road                          | geom (MultiLineString)      | Transportation corridors          |
| spatial_construction_site             | geom (Polygon)              | Active site perimeters & permits  |
| spatial_survey_observation            | geom_point_z (PointZ 3D)    | Geodetic control & benchmarks     |
| spatial_infrastructure_asset          | geom (Geometry)             | Civil utilities & networks        |
| spatial_administrative_boundary       | geom (MultiPolygon)         | Jurisdictional governance zones   |
| spatial_terrain_feature               | geom (Geometry)             | Hydrology, floodplains, contours  |
| spatial_imagery_observation           | geom_footprint (Polygon)    | Drone flights & satellite scenes  |
| spatial_property_parcel_association   | N/A                         | Property <-> LandParcel (M:N)     |
| spatial_building_parcel_intersection  | N/A                         | Building <-> LandParcel (M:N)     |
| spatial_property_building_association | N/A                         | Property <-> Building (M:N)       |
| spatial_quarantine                    | N/A                         | Unsanitary geometry intercept log |
+---------------------------------------+-----------------------------+-----------------------------------+
```

---

## 3. Mandatory Safeguards & Geometric Integrity

### 3.1 Constraints Enforced at Database Level
Every spatial table enforces strict database-level check constraints that cannot be bypassed by raw SQL or bulk loading:
- `CHECK (ST_IsValid(geom))`: Prevents self-intersecting rings, figure-eights, and collapsed edges.
- `CHECK (ST_SRID(geom) = 4326)`: Enforces canonical WGS 84 spatial reference.
- `CHECK (NOT ST_IsEmpty(geom))`: Forbids degenerate empty geometry records.
- `CHECK (levels_above_ground >= 0 AND levels_below_ground >= 0)`: Structural sanity.

### 3.2 High-Speed GiST Spatial Indexing
GiST spatial indexes are active across all 9 geometry-bearing tables (e.g. `spatial_building_geom_ce938d14_id`, `spatial_land_parcel_geom_...`).

### 3.3 Dynamic Geodesic Area Calculation
Area calculation is decoupled from fixed projected zones:
- Computed ground footprint area on `spatial_building` and `spatial_land_parcel` is dynamically computed on the WGS 84 ellipsoid via geodesic projection:
  $$\text{computed\_area\_sqm} = \text{ST\_Area}(\text{geom::geography})$$

---

## 4. Referential Integrity & Polymorphic Identity

External identities (such as OpenStreetMap `way/1151899424` or Overture GERS `8f2a...`) resolve via `spatial_identity_mapping`:
- Foreign key: `entity_id REFERENCES spatial_entity(id) ON DELETE CASCADE`.
- Uniqueness constraint: `UniqueConstraint(fields=['provider_name', 'provider_external_id'])` prevents duplicate mapping collisions.
- Concrete multi-table inheritance links `spatial_building.spatialentity_ptr_id` directly to `spatial_entity.id`, ensuring 100% database-level referential integrity without generic foreign keys.

---
*Accepted and Certified for Milestone M3.2.*
