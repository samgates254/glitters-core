# GLITTERS GOD'S EYE — MILESTONE M3.2
## Spatial Security & Multi-Tenant Authorization Report

**Document:** `M3_2_SECURITY_REPORT.md`
**Classification:** Internal Technical Architecture & Engineering Baseline
**Repository:** `~/Projects/glitters-core`
**Date:** 2026-11-14
**Author:** Principal Geospatial Architect & Core Engineering Review Board
**Status:** Approved Security Certification

---

## 1. Security Architecture & Tenancy Alignment

The spatial architecture strictly respects the existing `glitters-core` tenancy model:
- **No Secondary Tenancy System**: All organization-owned spatial records anchor directly to `apps.organizations.models.Organization`.
- **Three-Tier Access Scoping**:
  1. `PUBLIC` (`organization_id = NULL`): Globally readable for basemap layers (e.g. OpenStreetMap, gazetted county boundaries).
  2. `ORGANIZATION_INTERNAL` (`organization_id = <UUID>`): Proprietary development boundaries, internal valuations, confidential site scans accessible only to active organization members holding `property.view` or `property.manage`.
  3. `RESTRICTED_SURVEY`: Boundary beacons and dispute evidence accessible only to licensed land surveyors.

---

## 2. Negative Security Verification & Empirical Test Results

Security enforcement was verified through automated negative security tests in `tests/security/test_spatial_tenancy.py`:

```
============================= test session starts ==============================
tests/security/test_spatial_tenancy.py::test_cross_org_spatial_read_isolation PASSED
tests/security/test_spatial_tenancy.py::test_service_level_cross_org_mutation_blocked PASSED
tests/security/test_spatial_tenancy.py::test_surveyor_credential_gate PASSED
```

### 2.1 Threat Vector 1: Cross-Organization Read Isolation
- **Test**: `test_cross_org_spatial_read_isolation`
- **Result**: PASSED. When User B (authenticated under Organization B) requests `/api/v1/spatial/buildings/`, Organization A's private building (`Confidential Port Terminal HQ`) is completely omitted from the result set. Only public buildings are returned.

### 2.2 Threat Vector 2: Cross-Organization Mutation Blocking
- **Test**: `test_service_level_cross_org_mutation_blocked`
- **Result**: PASSED. When User B attempts to execute a management or boundary mutation against Organization A's asset, `SpatialAccessService.assert_can_manage()` intercepts the operation and raises `django.core.exceptions.PermissionDenied(CROSS_ORG_ACCESS_DENIED)`.

### 2.3 Threat Vector 3: Surveyor Credential Gating
- **Test**: `test_surveyor_credential_gate`
- **Result**: PASSED. Unverified users or users with empty license numbers are strictly blocked from accessing geodetic survey observation beacons.

---

## 3. Connection Pooling & Background Worker Safety

1. **Connection Pooling (`CONN_MAX_AGE=60`, PgBouncer)**:
   - Evaluated the hazard of PostgreSQL Row-Level Security session leaks (`SET LOCAL app.current_organization`).
   - Multi-tenant isolation is enforced deterministically at the **Django QuerySet and Service Layer** (`SpatialQuerySet.for_organization()`), ensuring complete immunity to connection pool session residue.
2. **Celery Worker Execution**:
   - Spatial background jobs explicitly receive `organization_id` in their payload and execute within isolated service contexts.

---
*Accepted and Certified for Milestone M3.2.*
