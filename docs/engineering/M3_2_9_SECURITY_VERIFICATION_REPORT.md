# GLITTERS CORE — MILESTONE M3.2.9
## Security Verification, Lineage DAG & Version Integrity Audit

**Document:** `M3_2_9_SECURITY_VERIFICATION_REPORT.md`
**Classification:** Internal Technical Architecture & Engineering Quality Gate
**Repository:** `~/Projects/glitters-core`
**Date:** 2026-11-14
**Author:** Principal Geospatial Architect & Core Engineering Review Board
**Status:** Audit Certified — Security & Invariants Verified

---

## 1. Multi-Tenant Authorization Audit Across Separate Organizations

An empirical security audit was executed with two distinct organizations (`Organization A` and `Organization B`) using separate authenticated user identities:

### 1.1 Read Isolation
- **Condition**: Organization A owns a private commercial building (`Confidential Port Terminal HQ`). A public municipal building (`Public Fort Jesus Museum`, `organization=None`) also exists.
- **Action**: User B (Owner of Organization B) queries `/api/v1/spatial/buildings/`.
- **Observed Result**:
  - Public building is returned.
  - Organization A's private building is **strictly omitted**.
- **QuerySet Mechanism**:
  `SpatialQuerySet.for_organization(organization)` automatically filters `Q(organization__isnull=True) | Q(organization=organization)`, preventing data leakage at the query boundary.

### 1.2 Mutation Blocking
- **Condition**: User B attempts to edit or delete Organization A's building.
- **Observed Result**:
  `SpatialAccessService.assert_can_manage()` intercepts the operation and raises `django.core.exceptions.PermissionDenied("You do not have permission to access spatial assets belonging to another organization.")`.

### 1.3 Public Foundation Data Protection
- **Condition**: User A attempts to edit a public foundation building (`organization=None`).
- **Observed Result**:
  `SpatialAccessService.assert_can_manage()` asserts `user.is_staff`. Non-staff users are blocked with `PermissionDenied("Only platform administrators can modify public spatial data.")`.

### 1.4 Surveyor Credential Gating
- **Condition**: User attempts to query or register a restricted `SurveyObservation` geodetic beacon.
- **Observed Result**:
  Empty, whitespace, or invalid surveyor license strings are intercepted with `PermissionDenied("Licensed surveyor credentials required to access restricted survey observations.")`.

---

## 2. Entity Lineage DAG Invariant Audit

To ensure spatial lineage remains an acyclic directed graph (DAG), cyclic transformations were tested against `spatial_entity_lineage`:

```
[ Valid Chain: A -> B -> C -> D ]  --> PASSED
[ Self-Reference: D -> D ]          --> REJECTED (ValidationError: Acyclic lineage violation)
[ Direct Cycle: B -> A ]            --> REJECTED (ValidationError: Acyclic lineage violation)
[ Multi-Hop Cycle: D -> A ]         --> REJECTED (ValidationError: Acyclic lineage violation)
```

- **Validation Engine**: `apps.spatial.services.validation.check_acyclic_lineage()` performs breadth-first cycle detection across all transitive ancestor and descendant nodes prior to saving.

---

## 3. Dataset Version Integrity Audit

Dataset version immutability was tested against `spatial_dataset_registry` and `spatial_dataset_version`:

1. **Simultaneous Historical Preservation**:
   Registering Version `2.0.0` with new checksums preserved Version `1.0.0` completely intact. `DatasetRegistry.versions.count()` evaluates to 2.
2. **Duplicate Version Collision Rejection**:
   Attempting to publish a duplicate `version_tag = '1.0.0'` on the same dataset was blocked by PostgreSQL unique constraint:
   `psycopg.errors.UniqueViolation: duplicate key value violates unique constraint "uq_dataset_version"`.

---
*Audit Certified for Milestone M3.2.9.*
