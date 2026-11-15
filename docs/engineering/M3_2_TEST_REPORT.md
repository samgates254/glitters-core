# GLITTERS GOD'S EYE — MILESTONE M3.2
## Spatial Test Suite Execution & Acceptance Report

**Document:** `M3_2_TEST_REPORT.md`
**Classification:** Internal Technical Architecture & Engineering Baseline
**Repository:** `~/Projects/glitters-core`
**Date:** 2026-11-14
**Author:** Principal Geospatial Architect & Core Engineering Review Board
**Status:** Approved Test Certification

---

## 1. Test Suite Summary & Environment

- **Target Repository**: `~/Projects/glitters-core`
- **Python Runtime**: Python 3.14.4 (via uv virtual environment)
- **Database Engine**: PostgreSQL 16.8 with PostGIS 3.5.2 (Docker container `glitters-postgres`, port 5434)
- **Django Version**: 5.2.19 with `django.contrib.gis.db.backends.postgis`
- **Execution Command**:
  ```bash
  uv run pytest tests/unit/test_spatial_domain.py tests/unit/test_spatial_crs.py tests/integration/test_spatial_models.py tests/integration/test_spatial_api.py tests/security/test_spatial_tenancy.py -v
  ```
- **Overall Result**: **22 passed in 24.57s (100% Success Rate)**

---

## 2. Detailed Test Inventory & Results

```
============================= test session starts ==============================
tests/integration/test_spatial_models.py::test_dataset_registry_and_version PASSED [  4%]
tests/integration/test_spatial_models.py::test_spatial_identity_mapping_uniqueness PASSED [  9%]
tests/integration/test_spatial_models.py::test_building_and_parcel_creation PASSED [ 13%]
tests/integration/test_spatial_models.py::test_survey_observation_3d PASSED [ 18%]
tests/integration/test_spatial_models.py::test_decoupled_mn_relationships PASSED [ 22%]
tests/integration/test_spatial_models.py::test_entity_lineage_acyclic_enforcement PASSED [ 27%]
tests/integration/test_spatial_models.py::test_spatial_quarantine_logging PASSED [ 31%]
tests/integration/test_spatial_api.py::test_datasets_api PASSED          [ 36%]
tests/integration/test_spatial_api.py::test_buildings_api_with_geojson PASSED [ 40%]
tests/integration/test_spatial_api.py::test_external_id_lookup_endpoint PASSED [ 45%]
tests/security/test_spatial_tenancy.py::test_cross_org_spatial_read_isolation PASSED [ 50%]
tests/security/test_spatial_tenancy.py::test_service_level_cross_org_mutation_blocked PASSED [ 54%]
tests/security/test_spatial_tenancy.py::test_surveyor_credential_gate PASSED [ 59%]
tests/unit/test_spatial_domain.py::test_build_canonical_urn PASSED       [ 63%]
tests/unit/test_spatial_domain.py::test_validate_canonical_urn_valid PASSED [ 68%]
tests/unit/test_spatial_domain.py::test_validate_canonical_urn_invalid PASSED [ 72%]
tests/unit/test_spatial_domain.py::test_parse_canonical_urn PASSED       [ 77%]
tests/unit/test_spatial_domain.py::test_parse_canonical_urn_raises_on_invalid PASSED [ 81%]
tests/unit/test_spatial_domain.py::test_domain_enum_integrity PASSED     [ 86%]
tests/unit/test_spatial_crs.py::test_get_optimal_kenya_utm_srid PASSED   [ 90%]
tests/unit/test_spatial_crs.py::test_orthometric_and_ellipsoidal_elevation_math PASSED [ 95%]
tests/unit/test_spatial_crs.py::test_calculate_planar_area_utm_sqm PASSED [100%]

============================= 22 passed in 24.57s ==============================
```

---

## 3. Test Coverage Breakdown

### 3.1 Domain & Canonical Identity (`test_spatial_domain.py`)
- Verified deterministic formatting: `urn:glitters:spatial:{entity}:{uuid}`.
- Verified parsing and rejection of invalid or legacy URN structures.
- Verified domain enum sets (`EntityType`, `LegalAuthorityLevel`, `VerticalDatum`, `LifecycleStatus`).

### 3.2 Nationwide CRS & Geodesy (`test_spatial_crs.py`)
- Verified dynamic UTM zone selection across Kenya:
  - Mombasa & Nairobi $\rightarrow$ UTM Zone 37S (EPSG:32737)
  - Kisumu $\rightarrow$ UTM Zone 36S (EPSG:32736)
  - Turkana $\rightarrow$ UTM Zone 36N (EPSG:32636)
  - Marsabit $\rightarrow$ UTM Zone 37N (EPSG:32637)
- Verified orthometric and ellipsoidal elevation conversions ($H = h - N$).
- Verified planar UTM polygon area calculations.

### 3.3 Models, Constraints & Lineage (`test_spatial_models.py`)
- Verified dataset catalog and immutable versioning linkage.
- Verified external identity mapping uniqueness constraint on `(provider_name, provider_external_id)`.
- Verified 3D `PointZ` observation storage on `SurveyObservation`.
- Verified M:N junction models (`PropertyParcelAssociation`, `BuildingParcelIntersection`).
- Verified acyclic lineage DAG enforcement (self-references and multi-generational circular loops are rejected with `ValidationError`).
- Verified quarantine log persistence.

### 3.4 REST & GeoJSON APIs (`test_spatial_api.py`)
- Verified dataset registry catalog listing.
- Verified building detail serialization with nested GeoJSON MultiPolygon output.
- Verified external provider ID lookup endpoint (`/api/v1/spatial/entities/lookup/?provider=OSM&id=way/1151899424`).

### 3.5 Multi-Tenant Security (`test_spatial_tenancy.py`)
- Verified cross-organization read isolation.
- Verified service-layer cross-organization mutation blocking.
- Verified surveyor credential verification gate.

---

## 4. Known Limitations

1. **Large Dataset Ingestion Pending**: The 72,864 Mombasa OSM building footprints were explicitly excluded from this milestone to ensure zero risk during schema establishment. Ingestion is scheduled for Milestone M3.3.
2. **Vector Tile Streaming**: Direct Mapbox Vector Tile (`MVT`) streaming will be operationalized via Martin in Milestone M3.3.

---
*Accepted and Certified for Milestone M3.2.*
