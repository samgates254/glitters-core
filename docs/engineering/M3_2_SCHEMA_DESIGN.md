# GLITTERS GOD'S EYE — MILESTONE M3.2
## Spatial Schema Design Specification & DDL Reference

**Document:** `M3_2_SCHEMA_DESIGN.md`
**Classification:** Internal Technical Architecture & Engineering Baseline
**Repository:** `~/Projects/glitters-core`
**Date:** 2026-11-14
**Author:** Principal Geospatial Architect & Core Engineering Review Board
**Status:** Approved Schema Specification

---

## 1. Schema Namespacing & Overview

All geospatial infrastructure inside `glitters-core` is isolated within the dedicated PostgreSQL schema `spatial`. This guarantees strict separation between commercial transactional tables (`public`) and spatial intelligence tables (`spatial`).

```
+----------------------------------------------------------------------------------------------------+
|                                      SPATIAL SCHEMA ARCHITECTURE                                   |
+--------------------------+-----------------------+-------------------------------------------------+
| Table Category           | Tables                | Architectural Purpose                           |
+--------------------------+-----------------------+-------------------------------------------------+
| Identity & Lineage       | spatial_entity        | Canonical UUID anchor table                     |
|                          | identity_mapping      | Polymorphic external mapping (OSM, GERS, etc.)  |
|                          | entity_lineage        | Historical DAG (mergers, subdivisions)          |
+--------------------------+-----------------------+-------------------------------------------------+
| Dataset Catalog          | dataset_registry      | Master dataset products                         |
|                          | dataset_version       | Immutable releases with SHA-256 hashes          |
|                          | dataset_quality_report| Audit metrics (valid, repaired, quarantined)    |
+--------------------------+-----------------------+-------------------------------------------------+
| Specialized Entities     | building              | Physical 2D/3D building structures              |
|                          | land_parcel           | Cadastral boundaries & legal titles             |
|                          | road                  | Transportation corridors & centerlines          |
|                          | construction_site     | Active development perimeters & permits         |
|                          | survey_observation    | High-precision geodetic beacons (PointZ)        |
|                          | infrastructure_asset  | Utilities (water, storm drainage, power, telecom|
|                          | administrative_boundary| County, ward, and municipal planning zones      |
|                          | terrain_feature       | Contours, floodplains, slopes                   |
|                          | imagery_observation   | Drone flight footprints & satellite scenes      |
+--------------------------+-----------------------+-------------------------------------------------+
| Decoupled M:N Junctions  | property_parcel_assoc | Property <-> LandParcel (Sectional/Full)        |
|                          | building_parcel_inter | Building <-> LandParcel (Straddles/Encroach)    |
|                          | property_building_assoc| Property <-> Building (Units/Complexes)        |
+--------------------------+-----------------------+-------------------------------------------------+
| Integrity & Quarantine   | spatial_quarantine    | Unsanitary geometry intercept ledger            |
+--------------------------+-----------------------+-------------------------------------------------+
```

---

## 2. Complete PostgreSQL PostGIS DDL Specification

```sql
-- GLITTERS GOD'S EYE: Production Spatial Schema DDL
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "postgis";

CREATE SCHEMA IF NOT EXISTS spatial;

-- ============================================================================
-- 1. DATASET REGISTRY & IMMUTABLE VERSIONING
-- ============================================================================

CREATE TABLE spatial.dataset_registry (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    dataset_urn VARCHAR(255) UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    category VARCHAR(50) NOT NULL, -- 'BASEMAP', 'BUILDINGS', 'CADASTRE', 'INFRASTRUCTURE', 'IMAGERY'
    update_frequency VARCHAR(50) NOT NULL, -- 'STATIC', 'MONTHLY', 'STREAMING'
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE spatial.dataset_version (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    dataset_id UUID NOT NULL REFERENCES spatial.dataset_registry(id) ON DELETE RESTRICT,
    version_tag VARCHAR(50) NOT NULL, -- '2026.03.1', 'v1.0.0'
    source_url TEXT,
    source_checksum_sha256 VARCHAR(64) NOT NULL,
    pipeline_git_commit VARCHAR(40) NOT NULL,
    license_type VARCHAR(50) NOT NULL, -- 'ODbL', 'PROPRIETARY_GOVERNMENT', 'GLITTERS_IP'
    attribution_statement TEXT NOT NULL,
    spatial_extent GEOMETRY(Polygon, 4326) NOT NULL,
    min_zoom INT NOT NULL DEFAULT 0,
    max_zoom INT NOT NULL DEFAULT 22,
    feature_count INT NOT NULL DEFAULT 0,
    published_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_dataset_version UNIQUE (dataset_id, version_tag)
);
CREATE INDEX idx_dataset_version_extent ON spatial.dataset_version USING GIST(spatial_extent);

CREATE TABLE spatial.dataset_quality_report (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    version_id UUID UNIQUE NOT NULL REFERENCES spatial.dataset_version(id) ON DELETE CASCADE,
    valid_geometries INT NOT NULL,
    repaired_geometries INT NOT NULL DEFAULT 0,
    quarantined_geometries INT NOT NULL DEFAULT 0,
    duplicate_records INT NOT NULL DEFAULT 0,
    horizontal_rmse_m NUMERIC(6, 3),
    vertical_rmse_m NUMERIC(6, 3),
    audit_notes TEXT,
    audited_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================================
-- 2. CANONICAL SPATIAL IDENTITY & LINEAGE
-- ============================================================================

CREATE TABLE spatial.spatial_entity (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    canonical_urn VARCHAR(255) UNIQUE NOT NULL,
    entity_type VARCHAR(50) NOT NULL, -- 'BUILDING', 'LAND_PARCEL', 'ROAD', etc.
    organization_id UUID REFERENCES public.organization(id) ON DELETE SET NULL,
    dataset_version_id UUID NOT NULL REFERENCES spatial.dataset_version(id) ON DELETE RESTRICT,
    lifecycle_status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE', -- 'PLANNED', 'ACTIVE', 'SUPERSEDED', 'DEMOLISHED'
    valid_from TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    valid_to TIMESTAMPTZ,
    superseded_by UUID REFERENCES spatial.spatial_entity(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_spatial_entity_urn ON spatial.spatial_entity(canonical_urn);
CREATE INDEX idx_spatial_entity_org ON spatial.spatial_entity(organization_id);

CREATE TABLE spatial.identity_mapping (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    entity_id UUID NOT NULL REFERENCES spatial.spatial_entity(id) ON DELETE CASCADE,
    provider_name VARCHAR(50) NOT NULL, -- 'OSM', 'OVERTURE', 'KENYA_CADASTRE', 'SURVEY_OF_KENYA'
    provider_external_id VARCHAR(255) NOT NULL,
    confidence_score NUMERIC(3, 2) NOT NULL DEFAULT 1.00,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_spatial_provider_id UNIQUE (provider_name, provider_external_id)
);
CREATE INDEX idx_id_mapping_entity ON spatial.identity_mapping(entity_id);
CREATE INDEX idx_id_mapping_lookup ON spatial.identity_mapping(provider_name, provider_external_id);

CREATE TABLE spatial.entity_lineage (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    predecessor_urn VARCHAR(255) NOT NULL,
    successor_urn VARCHAR(255) NOT NULL,
    lineage_type VARCHAR(50) NOT NULL, -- 'SUBDIVISION', 'AMALGAMATION', 'REDEVELOPMENT', 'CORRECTION'
    effective_date DATE NOT NULL,
    authorization_reference VARCHAR(100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_lineage_pred ON spatial.entity_lineage(predecessor_urn);
CREATE INDEX idx_lineage_succ ON spatial.entity_lineage(successor_urn);

-- ============================================================================
-- 3. CORE SPECIALIZED SPATIAL ENTITIES
-- ============================================================================

-- A. Land Parcel (Cadastral)
CREATE TABLE spatial.land_parcel (
    entity_ptr_id UUID PRIMARY KEY REFERENCES spatial.spatial_entity(id) ON DELETE CASCADE,
    cadastral_identifier VARCHAR(100) UNIQUE NOT NULL,
    title_number VARCHAR(100),
    registry_section VARCHAR(100),
    tenure_type VARCHAR(50) NOT NULL, -- 'FREEHOLD', 'LEASEHOLD_99', 'COMMUNAL'
    registered_area_sqm NUMERIC(12, 2),
    computed_area_sqm NUMERIC(12, 2) GENERATED ALWAYS AS (
        ST_Area(geom::geography)
    ) STORED,
    legal_authority_level VARCHAR(50) NOT NULL DEFAULT 'STATUTORY_AUTHORITATIVE',
    horizontal_rmse_m NUMERIC(6, 3) NOT NULL DEFAULT 0.05,
    is_human_verified BOOLEAN NOT NULL DEFAULT FALSE,
    geom GEOMETRY(MultiPolygon, 4326) NOT NULL,
    CONSTRAINT chk_parcel_geom_valid CHECK (ST_IsValid(geom)),
    CONSTRAINT chk_parcel_geom_srid CHECK (ST_SRID(geom) = 4326),
    CONSTRAINT chk_parcel_geom_bounds CHECK (
        ST_XMin(geom) >= -180 AND ST_XMax(geom) <= 180 AND
        ST_YMin(geom) >= -90 AND ST_YMax(geom) <= 90
    )
);
CREATE INDEX idx_parcel_geom ON spatial.land_parcel USING GIST(geom);

-- B. Building (Physical Structure)
CREATE TABLE spatial.building (
    entity_ptr_id UUID PRIMARY KEY REFERENCES spatial.spatial_entity(id) ON DELETE CASCADE,
    name VARCHAR(255),
    classification VARCHAR(50) NOT NULL, -- 'RESIDENTIAL', 'COMMERCIAL', 'INDUSTRIAL', etc.
    height_m NUMERIC(6, 2),
    levels_above_ground INT,
    levels_below_ground INT DEFAULT 0,
    roof_shape VARCHAR(50),
    facade_material VARCHAR(100),
    footprint_area_sqm NUMERIC(10, 2) GENERATED ALWAYS AS (
        ST_Area(geom::geography)
    ) STORED,
    legal_authority_level VARCHAR(50) NOT NULL DEFAULT 'OPEN_DATA',
    horizontal_rmse_m NUMERIC(6, 3) NOT NULL DEFAULT 1.00,
    is_human_verified BOOLEAN NOT NULL DEFAULT FALSE,
    geom GEOMETRY(MultiPolygon, 4326) NOT NULL,
    CONSTRAINT chk_building_geom_valid CHECK (ST_IsValid(geom)),
    CONSTRAINT chk_building_geom_srid CHECK (ST_SRID(geom) = 4326),
    CONSTRAINT chk_building_geom_bounds CHECK (
        ST_XMin(geom) >= -180 AND ST_XMax(geom) <= 180 AND
        ST_YMin(geom) >= -90 AND ST_YMax(geom) <= 90
    )
);
CREATE INDEX idx_building_geom ON spatial.building USING GIST(geom);
CREATE INDEX idx_building_class ON spatial.building(classification);

-- C. Road (Transportation Corridor)
CREATE TABLE spatial.road (
    entity_ptr_id UUID PRIMARY KEY REFERENCES spatial.spatial_entity(id) ON DELETE CASCADE,
    road_name VARCHAR(255),
    road_class VARCHAR(50) NOT NULL, -- 'HIGHWAY', 'PRIMARY', 'LOCAL', 'SERVICE'
    surface_type VARCHAR(50) NOT NULL, -- 'ASPHALT', 'MURRAM', 'PAVED'
    carriageway_width_m NUMERIC(5, 2),
    right_of_way_width_m NUMERIC(5, 2),
    governing_authority VARCHAR(50),
    geom GEOMETRY(MultiLineString, 4326) NOT NULL,
    CONSTRAINT chk_road_geom_valid CHECK (ST_IsValid(geom)),
    CONSTRAINT chk_road_geom_srid CHECK (ST_SRID(geom) = 4326)
);
CREATE INDEX idx_road_geom ON spatial.road USING GIST(geom);

-- D. Construction Site (Active Development)
CREATE TABLE spatial.construction_site (
    entity_ptr_id UUID PRIMARY KEY REFERENCES spatial.spatial_entity(id) ON DELETE CASCADE,
    project_name VARCHAR(255) NOT NULL,
    permit_number VARCHAR(100),
    contractor_name VARCHAR(255),
    development_phase VARCHAR(50) NOT NULL, -- 'FOUNDATION', 'SUPERSTRUCTURE', 'FITOUT'
    hazard_buffer_m NUMERIC(6, 2) DEFAULT 10.0,
    geom GEOMETRY(Polygon, 4326) NOT NULL,
    CONSTRAINT chk_const_geom_valid CHECK (ST_IsValid(geom)),
    CONSTRAINT chk_const_geom_srid CHECK (ST_SRID(geom) = 4326)
);
CREATE INDEX idx_construction_geom ON spatial.construction_site USING GIST(geom);

-- E. Survey Observation (3D Geodetic Control & Beacons)
CREATE TABLE spatial.survey_observation (
    entity_ptr_id UUID PRIMARY KEY REFERENCES spatial.spatial_entity(id) ON DELETE CASCADE,
    beacon_code VARCHAR(50) NOT NULL,
    beacon_type VARCHAR(50) NOT NULL, -- 'IRON_PIN_CONCRETE', 'BRASS_PLAQUE', 'PILLAR'
    vertical_datum VARCHAR(50) NOT NULL DEFAULT 'EGM2008_ORTHOMETRIC',
    elevation_orthometric_m NUMERIC(8, 3) NOT NULL,
    elevation_ellipsoidal_m NUMERIC(8, 3),
    horizontal_rmse_m NUMERIC(6, 3) NOT NULL DEFAULT 0.010,
    vertical_rmse_m NUMERIC(6, 3) NOT NULL DEFAULT 0.020,
    surveyor_license VARCHAR(100) NOT NULL,
    observation_timestamp TIMESTAMPTZ NOT NULL,
    geom_point_z GEOMETRY(PointZ, 4326) NOT NULL,
    CONSTRAINT chk_survey_geom_valid CHECK (ST_IsValid(geom_point_z)),
    CONSTRAINT chk_survey_geom_srid CHECK (ST_SRID(geom_point_z) = 4326)
);
CREATE INDEX idx_survey_geom ON spatial.survey_observation USING GIST(geom_point_z);

-- F. Infrastructure Asset (Utilities)
CREATE TABLE spatial.infrastructure_asset (
    entity_ptr_id UUID PRIMARY KEY REFERENCES spatial.spatial_entity(id) ON DELETE CASCADE,
    asset_type VARCHAR(50) NOT NULL, -- 'WATER_MAIN', 'STORM_DRAIN', 'SEWER', 'GRID_CABLE'
    network_name VARCHAR(255) NOT NULL,
    operator VARCHAR(100) NOT NULL,
    depth_m NUMERIC(5, 2),
    capacity_rating VARCHAR(100),
    geom GEOMETRY(Geometry, 4326) NOT NULL,
    CONSTRAINT chk_infra_geom_valid CHECK (ST_IsValid(geom)),
    CONSTRAINT chk_infra_geom_srid CHECK (ST_SRID(geom) = 4326)
);
CREATE INDEX idx_infra_geom ON spatial.infrastructure_asset USING GIST(geom);

-- G. Administrative Boundary
CREATE TABLE spatial.administrative_boundary (
    entity_ptr_id UUID PRIMARY KEY REFERENCES spatial.spatial_entity(id) ON DELETE CASCADE,
    administrative_area_id UUID REFERENCES public.administrative_area(id) ON DELETE SET NULL,
    boundary_name VARCHAR(255) NOT NULL,
    admin_level VARCHAR(50) NOT NULL, -- 'COUNTRY', 'COUNTY', 'SUB_COUNTY', 'WARD'
    geom GEOMETRY(MultiPolygon, 4326) NOT NULL,
    CONSTRAINT chk_admin_geom_valid CHECK (ST_IsValid(geom)),
    CONSTRAINT chk_admin_geom_srid CHECK (ST_SRID(geom) = 4326)
);
CREATE INDEX idx_admin_geom ON spatial.administrative_boundary USING GIST(geom);

-- ============================================================================
-- 4. DECOUPLED M:N RELATIONSHIPS
-- ============================================================================

CREATE TABLE spatial.property_parcel_association (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    property_id UUID NOT NULL REFERENCES public.property(id) ON DELETE CASCADE,
    parcel_id UUID NOT NULL REFERENCES spatial.land_parcel(entity_ptr_id) ON DELETE CASCADE,
    association_type VARCHAR(50) NOT NULL DEFAULT 'FULL_OWNERSHIP',
    undivided_share_ratio NUMERIC(5, 4) NOT NULL DEFAULT 1.0000,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_property_parcel UNIQUE (property_id, parcel_id)
);

CREATE TABLE spatial.building_parcel_intersection (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    building_id UUID NOT NULL REFERENCES spatial.building(entity_ptr_id) ON DELETE CASCADE,
    parcel_id UUID NOT NULL REFERENCES spatial.land_parcel(entity_ptr_id) ON DELETE CASCADE,
    intersection_area_sqm NUMERIC(10, 2) NOT NULL,
    percentage_of_building NUMERIC(5, 2) NOT NULL,
    is_primary_parcel BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_building_parcel UNIQUE (building_id, parcel_id)
);

CREATE TABLE spatial.property_building_association (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    property_id UUID NOT NULL REFERENCES public.property(id) ON DELETE CASCADE,
    building_id UUID NOT NULL REFERENCES spatial.building(entity_ptr_id) ON DELETE CASCADE,
    association_type VARCHAR(50) NOT NULL DEFAULT 'ENTIRE_BUILDING',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_property_building UNIQUE (property_id, building_id)
);

-- ============================================================================
-- 5. GEOMETRY SANITY & QUARANTINE LEDGER
-- ============================================================================

CREATE TABLE spatial.spatial_quarantine (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_dataset_version_id UUID REFERENCES spatial.dataset_version(id) ON DELETE SET NULL,
    source_record_id VARCHAR(255),
    raw_payload JSONB NOT NULL,
    failure_stage VARCHAR(50) NOT NULL, -- 'PARSING', 'TOPOLOGY_INVALID', 'DIMENSION_MISMATCH'
    error_message TEXT NOT NULL,
    quarantined_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

---
*Ready for Architectural Review and Stakeholder Approval.*
