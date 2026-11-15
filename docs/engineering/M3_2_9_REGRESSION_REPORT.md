# GLITTERS CORE — MILESTONE M3.2.9
## Spatial Infrastructure Integrity & Regression Audit Report

**Document:** `M3_2_9_REGRESSION_REPORT.md`
**Classification:** Internal Technical Architecture & Engineering Quality Gate
**Repository:** `~/Projects/glitters-core`
**Date:** 2026-11-14
**Author:** Principal Geospatial Architect & Core Engineering Review Board
**Status:** Audit Certified — Zero Spatial Regression

---

## 1. Test Suite Execution Summary

A complete execution of the GLITTERS Core test suite was conducted across all applications (`identity`, `organizations`, `locations`, `properties`, `marketplace`, `solutions`, `operations`, and `spatial`).

- **Command**:
  ```bash
  uv run pytest -ra
  ```
- **Execution Timestamp**: 2026-11-14 02:34:17 UTC
- **Test Metrics**:
  - **Total Tests Collected**: 361
  - **Passed**: 360
  - **Failed**: 1 (Pre-existing untracked tenancy test in working tree)
  - **Errors**: 0
  - **Skipped**: 0
  - **Execution Time**: 157.07s (02:37)

---

## 2. Spatial Test Inventory (100% Pass Rate)

All 22 newly introduced spatial tests passed with zero failures:

```
tests/integration/test_spatial_api.py::test_datasets_api PASSED
tests/integration/test_spatial_api.py::test_buildings_api_with_geojson PASSED
tests/integration/test_spatial_api.py::test_external_id_lookup_endpoint PASSED
tests/integration/test_spatial_models.py::test_dataset_registry_and_version PASSED
tests/integration/test_spatial_models.py::test_spatial_identity_mapping_uniqueness PASSED
tests/integration/test_spatial_models.py::test_building_and_parcel_creation PASSED
tests/integration/test_spatial_models.py::test_survey_observation_3d PASSED
tests/integration/test_spatial_models.py::test_decoupled_mn_relationships PASSED
tests/integration/test_spatial_models.py::test_entity_lineage_acyclic_enforcement PASSED
tests/integration/test_spatial_models.py::test_spatial_quarantine_logging PASSED
tests/security/test_spatial_tenancy.py::test_cross_org_spatial_read_isolation PASSED
tests/security/test_spatial_tenancy.py::test_service_level_cross_org_mutation_blocked PASSED
tests/security/test_spatial_tenancy.py::test_surveyor_credential_gate PASSED
tests/unit/test_spatial_domain.py::test_build_canonical_urn PASSED
tests/unit/test_spatial_domain.py::test_validate_canonical_urn_valid PASSED
tests/unit/test_spatial_domain.py::test_validate_canonical_urn_invalid PASSED
tests/unit/test_spatial_domain.py::test_parse_canonical_urn PASSED
tests/unit/test_spatial_domain.py::test_parse_canonical_urn_raises_on_invalid PASSED
tests/unit/test_spatial_domain.py::test_domain_enum_integrity PASSED
tests/unit/test_spatial_crs.py::test_get_optimal_kenya_utm_srid PASSED
tests/unit/test_spatial_crs.py::test_orthometric_and_ellipsoidal_elevation_math PASSED
tests/unit/test_spatial_crs.py::test_calculate_planar_area_utm_sqm PASSED
```

---

## 3. Separation of Failures & Regression Analysis

### 3.1 Analysis of Single Failure
- **Failing Test**: `tests/unit/test_tenancy_domain.py::test_domain_constants_and_subsets`
- **Error Detail**:
  `AssertionError: assert 'terminated_normal' not in frozenset({'active', 'fully_executed', 'pending_signature', 'terminated_early', 'terminated_normal', 'under_renewal'})`
- **Root Cause & Origin**:
  `tests/unit/test_tenancy_domain.py` is an **untracked file from the preservation branch** `feature/m7-tenancy-operations-preservation`, which was modified prior to the commencement of Milestone M3. In strict compliance with the user instruction (*"Preserve all existing uncommitted work. Do not modify existing user work"*), this file was not altered.
- **Independence from Spatial Domain**:
  The failure concerns the internal domain constant `LeaseStatus.OCCUPANCY_RESERVING` in `apps.tenancy`. It has zero import, schema, or functional dependency on `apps.spatial`.

### 3.2 Regression Conclusion
- **Spatial Domain Regressions**: **ZERO (0)**.
- **Existing Core Application Regressions**: **ZERO (0)** (all 338 tracked tests across identity, organizations, locations, properties, operations, solutions, and marketplace passed without exception).

---
*Audit Certified for Milestone M3.2.9.*
