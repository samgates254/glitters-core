-- =============================================================================
-- GLITTERS GEOSPATIAL INTELLIGENCE ENGINE — PRODUCTION POSTGIS 3.4+ / PG 16+ DDL (v1.1.0)
-- =============================================================================
-- Remediation of v1.0.0 Audit Findings:
-- 1. Declarative Partitioning Primary Key Fix: Uses `PRIMARY KEY (parcel_obs_id, local_utm_epsg)`
--    on the partitioned table paired with an unpartitioned global identity table
--    `glitters_parcel_identity_registry (parcel_obs_id UUID PRIMARY KEY, ...)` to guarantee
--    both PostgreSQL 16 declarative partitioning compliance and global UUID uniqueness.
-- 2. Full Arc 1960 & WGS84 Kenya UTM Zone Coverage: Includes EPSG:21096 (Arc 1960 / UTM Zone 36N)
--    in constraints and partition `glitters_parcel_obs_utm36n FOR VALUES IN (32636, 21096)`.
-- 3. Enum Reconciliation: Includes `'PRELIMINARY_ALGORITHMIC_SCREENING'` in `observation_authority`.
-- 4. Cadastral Evidence Enforcement: Database CHECK constraint requires `survey_plan_reference`,
--    `authenticated_document_sha256` (64 hex chars), and `licensed_surveyor_id` whenever
--    `is_legally_verified_cadastre = TRUE`.
-- 5. Honest Spatial Grid Naming: Columns named `glitters_hex_res9_index` and `glitters_hilbert_lvl16`.
-- 6. Multi-Tenant Isolation & Row-Level Security (RLS): Enforces `tenant_org_id` and `visibility_scope`
--    policies for GLITTERS CORE multi-tenant deployment.

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS postgis_raster;
CREATE EXTENSION IF NOT EXISTS btree_gist;

-- 1. Kenyan Administrative Hierarchy (ADM0 -> ADM1 County -> ADM2 Sub-County -> ADM3 Ward)
CREATE TABLE IF NOT EXISTS glitters_kenya_admin_boundaries (
    admin_id            UUID PRIMARY KEY,
    admin_level         VARCHAR(24) NOT NULL CHECK (
        admin_level IN ('NATIONAL', 'COUNTY', 'SUB_COUNTY', 'WARD', 'LOCALITY', 'NEIGHBOURHOOD')
    ),
    pcode               VARCHAR(32) UNIQUE NOT NULL,
    name                VARCHAR(128) NOT NULL,
    parent_pcode        VARCHAR(32),
    county_code         SMALLINT CHECK (county_code BETWEEN 1 AND 47),
    primary_utm_epsg    INTEGER NOT NULL CHECK (
        primary_utm_epsg IN (32636, 32637, 32736, 32737, 21096, 21097, 21036, 21037)
    ),
    geom_wgs84          GEOMETRY(MultiPolygon, 4326) NOT NULL,
    authority_source    VARCHAR(128) NOT NULL DEFAULT 'HDX_COD_AB_IEBC_PUBLIC',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_admin_geom_gist
    ON glitters_kenya_admin_boundaries USING GIST (geom_wgs84);

-- 2. Global Parcel Observation Identity Registry (Unpartitioned Table Enforcing Global UUID Uniqueness)
CREATE TABLE IF NOT EXISTS glitters_parcel_identity_registry (
    parcel_obs_id       UUID PRIMARY KEY,
    local_utm_epsg      INTEGER NOT NULL CHECK (
        local_utm_epsg IN (32636, 32637, 32736, 32737, 21096, 21097, 21036, 21037)
    ),
    tenant_org_id       UUID NOT NULL,
    external_parcel_ref VARCHAR(64) NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (parcel_obs_id, local_utm_epsg)
);

-- 3. Partitioned Land Parcel Observations Table (Compliant with PostgreSQL 16 Declarative Partitioning)
CREATE TABLE IF NOT EXISTS glitters_parcel_observations (
    parcel_obs_id                   UUID NOT NULL,
    local_utm_epsg                  INTEGER NOT NULL CHECK (
        local_utm_epsg IN (32636, 32637, 32736, 32737, 21096, 21097, 21036, 21037)
    ),
    tenant_org_id                   UUID NOT NULL,
    visibility_scope                VARCHAR(24) NOT NULL DEFAULT 'TENANT_PRIVATE' CHECK (
        visibility_scope IN ('PUBLIC_OPEN', 'TENANT_PRIVATE', 'SHARED_CONSORTIUM')
    ),
    external_parcel_ref             VARCHAR(64) NOT NULL,
    county_code                     SMALLINT NOT NULL CHECK (county_code BETWEEN 1 AND 47),
    ward_pcode                      VARCHAR(32),
    observation_authority           VARCHAR(48) NOT NULL CHECK (
        observation_authority IN (
            'PUBLIC_SATELLITE_OBSERVATION',
            'PUBLIC_AERIAL_OBSERVATION',
            'OPEN_COMMUNITY_MAPPING',
            'INFERRED_GEOMETRIC_BOUNDARY',
            'PRELIMINARY_ALGORITHMIC_SCREENING',
            'LICENSED_SURVEY_PLAN_REFERENCE'
        )
    ),
    is_legally_verified_cadastre    BOOLEAN NOT NULL DEFAULT FALSE,
    survey_plan_reference           VARCHAR(64),
    issuing_authority               VARCHAR(128),
    authenticated_document_sha256   CHAR(64),
    licensed_surveyor_id            VARCHAR(64),
    verification_timestamp          TIMESTAMPTZ,
    -- Non-negotiable database constraint: legal verification requires LICENSED_SURVEY_PLAN_REFERENCE + full documentary proof
    CONSTRAINT chk_cadastral_verification_evidence CHECK (
        (is_legally_verified_cadastre = FALSE)
        OR (
            is_legally_verified_cadastre = TRUE
            AND observation_authority = 'LICENSED_SURVEY_PLAN_REFERENCE'
            AND survey_plan_reference IS NOT NULL
            AND issuing_authority IS NOT NULL
            AND authenticated_document_sha256 ~ '^[0-9a-fA-F]{64}$'
            AND licensed_surveyor_id IS NOT NULL
            AND verification_timestamp IS NOT NULL
        )
    ),
    geom_wgs84                      GEOMETRY(Polygon, 4326) NOT NULL,
    centroid_wgs84                  GEOMETRY(Point, 4326) GENERATED ALWAYS AS (ST_Centroid(geom_wgs84)) STORED,
    glitters_hex_res9_index         VARCHAR(24) NOT NULL,
    glitters_hilbert_lvl16          BIGINT NOT NULL,
    geohash_8                       CHAR(8) NOT NULL,
    utm_planar_area_m2              DOUBLE PRECISION NOT NULL CHECK (utm_planar_area_m2 > 0),
    geodesic_ellipsoid_area_m2      DOUBLE PRECISION NOT NULL CHECK (geodesic_ellipsoid_area_m2 > 0),
    utm_perimeter_m                 DOUBLE PRECISION NOT NULL CHECK (utm_perimeter_m > 0),
    area_uncertainty_1sigma_m2      DOUBLE PRECISION NOT NULL CHECK (area_uncertainty_1sigma_m2 >= 0),
    polsby_popper_compactness       DOUBLE PRECISION NOT NULL CHECK (polsby_popper_compactness BETWEEN 0.0 AND 1.0001),
    valid_from                      DATE NOT NULL,
    valid_to                        DATE,
    created_at                      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (parcel_obs_id, local_utm_epsg),
    FOREIGN KEY (parcel_obs_id, local_utm_epsg)
        REFERENCES glitters_parcel_identity_registry (parcel_obs_id, local_utm_epsg)
        ON DELETE CASCADE
) PARTITION BY LIST (local_utm_epsg);

-- Partitions per Kenyan UTM Zone (including EPSG:21096 in UTM 36N)
CREATE TABLE IF NOT EXISTS glitters_parcel_obs_utm37s
    PARTITION OF glitters_parcel_observations FOR VALUES IN (32737, 21037);
CREATE TABLE IF NOT EXISTS glitters_parcel_obs_utm36s
    PARTITION OF glitters_parcel_observations FOR VALUES IN (32736, 21036);
CREATE TABLE IF NOT EXISTS glitters_parcel_obs_utm37n
    PARTITION OF glitters_parcel_observations FOR VALUES IN (32637, 21097);
CREATE TABLE IF NOT EXISTS glitters_parcel_obs_utm36n
    PARTITION OF glitters_parcel_observations FOR VALUES IN (32636, 21096);

-- Multi-Tenant Row-Level Security (RLS)
ALTER TABLE glitters_parcel_observations ENABLE ROW LEVEL SECURITY;

CREATE POLICY glitters_parcel_tenant_isolation_policy
    ON glitters_parcel_observations
    USING (
        visibility_scope = 'PUBLIC_OPEN'
        OR tenant_org_id = NULLIF(current_setting('app.current_tenant_org_id', true), '')::uuid
    )
    WITH CHECK (
        tenant_org_id = NULLIF(current_setting('app.current_tenant_org_id', true), '')::uuid
    );

-- GiST R-Tree Index on Polygon Geometry
CREATE INDEX IF NOT EXISTS idx_parcel_geom_gist
    ON glitters_parcel_observations USING GIST (geom_wgs84);

-- SP-GiST Quadtree/KD-Tree Index on Centroid Points
CREATE INDEX IF NOT EXISTS idx_parcel_centroid_spgist
    ON glitters_parcel_observations USING SPGIST (centroid_wgs84);

-- BRIN Block Range Index on Hilbert-sorted key and bounding boxes
CREATE INDEX IF NOT EXISTS idx_parcel_geom_brin
    ON glitters_parcel_observations USING BRIN (geom_wgs84) WITH (pages_per_range = 64);

-- B-Tree Index on GLITTERS Axial Hexagonal Cell ID and Tenant ID
CREATE INDEX IF NOT EXISTS idx_parcel_glitters_hex_res9
    ON glitters_parcel_observations USING BTREE (tenant_org_id, glitters_hex_res9_index);

-- 4. Multi-Temporal Change Detection Audit Log with Multi-Tenant RLS
CREATE TABLE IF NOT EXISTS glitters_detected_geographic_changes (
    change_id                               UUID PRIMARY KEY,
    tenant_org_id                           UUID NOT NULL,
    visibility_scope                        VARCHAR(24) NOT NULL DEFAULT 'TENANT_PRIVATE' CHECK (
        visibility_scope IN ('PUBLIC_OPEN', 'TENANT_PRIVATE', 'SHARED_CONSORTIUM')
    ),
    change_type                             VARCHAR(48) NOT NULL,
    geom_wgs84                              GEOMETRY(Geometry, 4326) NOT NULL,
    utm_epsg                                INTEGER NOT NULL CHECK (
        utm_epsg IN (32636, 32637, 32736, 32737, 21096, 21097, 21036, 21037)
    ),
    area_m2                                 DOUBLE PRECISION NOT NULL CHECK (area_m2 > 0),
    pixel_count_area_m2                     DOUBLE PRECISION NOT NULL CHECK (pixel_count_area_m2 > 0),
    observation_date_t1                     DATE NOT NULL,
    observation_date_t2                     DATE NOT NULL CHECK (observation_date_t2 >= observation_date_t1),
    source_imagery_t1                       VARCHAR(128) NOT NULL,
    source_imagery_t2                       VARCHAR(128) NOT NULL,
    detection_method                        VARCHAR(64) NOT NULL,
    confidence_estimate                     DOUBLE PRECISION NOT NULL CHECK (confidence_estimate BETWEEN 0.0 AND 1.0),
    applied_displacement_m                  DOUBLE PRECISION NOT NULL CHECK (applied_displacement_m >= 0.0),
    residual_registration_error_m           DOUBLE PRECISION NOT NULL CHECK (residual_registration_error_m >= 0.0),
    total_positional_uncertainty_1sigma_m   DOUBLE PRECISION NOT NULL CHECK (total_positional_uncertainty_1sigma_m >= 0.0),
    uncertainty_bounds_json                 JSONB NOT NULL,
    evidence_reference                      TEXT NOT NULL,
    detected_at                             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE glitters_detected_geographic_changes ENABLE ROW LEVEL SECURITY;

CREATE POLICY glitters_changes_tenant_isolation_policy
    ON glitters_detected_geographic_changes
    USING (
        visibility_scope = 'PUBLIC_OPEN'
        OR tenant_org_id = NULLIF(current_setting('app.current_tenant_org_id', true), '')::uuid
    );

CREATE INDEX IF NOT EXISTS idx_changes_geom_gist
    ON glitters_detected_geographic_changes USING GIST (geom_wgs84);
CREATE INDEX IF NOT EXISTS idx_changes_dates_brin
    ON glitters_detected_geographic_changes USING BRIN (observation_date_t1, observation_date_t2);
