# GLITTERS GOD'S EYE — MILESTONE M3.2
## Spatial Security & Multi-Tenant Authorization Specification

**Document:** `M3_2_SECURITY_DESIGN.md`
**Classification:** Internal Technical Architecture & Engineering Baseline
**Repository:** `~/Projects/glitters-core`
**Date:** 2026-11-14
**Author:** Principal Geospatial Architect & Core Engineering Review Board
**Status:** Approved Security Specification

---

## 1. Threat Model & Security Objectives

In enterprise geospatial intelligence, spatial data carries severe commercial and statutory risks:
1. **Commercial Asset Espionage**: Competitor real estate developers discovering off-market acquisition boundaries, unannounced pipeline parcels, or private yield projections.
2. **Statutory Privacy Violations**: Violations of the *Kenyan Data Protection Act (2019)* via unauthorized exposure of individual land title ownership details.
3. **Cross-Tenant Data Leakage via Connection Pools**: In pooled database architectures, session-level state (`SET LOCAL app.current_organization`) leaking across concurrent requests or Celery background worker tasks.

### Core Security Mandates
- **Zero Cross-Tenant Leakage**: An organization must never access or infer another organization's private spatial boundaries.
- **Empirical RLS Honesty**: Do not claim database RLS security in production until explicit session-clearing hooks and automated pooling tests are fully certified.
- **Strict Service-Layer Defense**: Primary authorization is enforced deterministically within Django's service and QuerySet layers.

---

## 2. Spatial Data Classification & Access Scopes

Spatial entities in `spatial.spatial_entity` belong to one of three strictly demarcated access tiers:

```
+---------------------------------------------------------------------------------------------------------+
|                                      SPATIAL DATA ACCESS CLASSIFICATION                                 |
+-------------------+--------------------+------------------------+---------------------------------------+
| Access Tier       | organization_id    | Read Permission        | Mutation Permission                   |
+-------------------+--------------------+------------------------+---------------------------------------+
| PUBLIC            | NULL               | Any authenticated user | System ETL / Platform Admin only      |
|                   |                    | (Public basemap tiles) |                                       |
+-------------------+--------------------+------------------------+---------------------------------------+
| ORGANIZATION_     | Organization UUID  | Active members with    | Active members with                   |
| INTERNAL          |                    | `property.view`        | `property.manage`                     |
+-------------------+--------------------+------------------------+---------------------------------------+
| RESTRICTED_       | Organization UUID  | Licensed Surveyors or  | Licensed Surveyors with explicit      |
| SURVEY            | or NULL (Gov)      | designated compliance  | statutory authorization               |
+-------------------+--------------------+------------------------+---------------------------------------+
```

### 2.1 Public Baseline Data
- **Entities**: OpenStreetMap building footprints, gazetted national and county administrative boundaries, public highway centerlines.
- **Rules**: Available to all authenticated API callers and anonymous vector tile consumers via Martin. Never exposes private ownership or financial underwriting.

### 2.2 Organization-Internal Spatial Assets
- **Entities**: Proposed master-plan perimeters, confidential land parcel assemblies, unapproved building footprints, proprietary drone orthomosaics.
- **Rules**: Accessible only to users holding active `OrganizationMembership` in the owning organization.

### 2.3 Restricted Statutory Records
- **Entities**: High-precision boundary beacons (`SurveyObservation`), official boundary dispute overlays, unredacted deed plans.
- **Rules**: Access is gated behind explicit credential verification (e.g. Registered Land Surveyor license number).

---

## 3. Application-Layer Multi-Tenancy Architecture

Because database connection pooling is active (`CONN_MAX_AGE=60`), multi-tenancy is enforced primarily at the Django application layer using scoped QuerySets:

### 3.1 Scoped Spatial QuerySet (`SpatialQuerySet`)
```python
class SpatialQuerySet(models.QuerySet):
    def for_organization(self, organization: Organization | None, user=None):
        """
        Enforces tenant isolation:
        - If organization is None: returns only PUBLIC data.
        - If organization is provided: returns PUBLIC data PLUS records owned by that organization.
        - If user lacks surveyor credentials: filters out RESTRICTED_SURVEY records.
        """
        public_filter = models.Q(organization__isnull=True)
        if organization is None:
            return self.filter(public_filter)

        org_filter = models.Q(organization=organization)
        return self.filter(public_filter | org_filter)
```

### 3.2 Service Layer Authorization (`SpatialAccessService`)
Before any create, update, or delete operation on `SpatialEntity` or specialized models:
1. Asserts caller is an active member of `request.organization`.
2. Validates permission `property.manage` for edits.
3. Rejects any attempt to reassign `organization_id` to another organization (Anti-Hijacking Check).

---

## 4. Connection Pooling, Celery Workers & RLS Hazards

### 4.1 The Connection Pool Hazard (PgBouncer & `CONN_MAX_AGE`)
If PostgreSQL Row-Level Security (RLS) is implemented via:
`SET LOCAL app.current_organization = '<UUID>';`

In an environment where:
- Django keeps connections open (`CONN_MAX_AGE = 60`), or
- PgBouncer operates in transaction or session pooling mode,

A connection that experiences an unhandled Python exception before the transaction commits may **retain session variables**. Subsequent queries routed to that connection could execute under the previous tenant's identity!

### 4.2 Background Worker Context (Celery)
Celery tasks (e.g. drone orthomosaic processing, batch footprint imports) execute outside the HTTP request lifecycle:
- Celery workers do not have `request.organization` or HTTP headers.
- **Mandatory Pattern**: All Celery tasks must explicitly receive `organization_id: str` and `actor_user_id: str` in their task payload.
- The task must initialize tenant context at entry and execute `DISCARD ALL` in a `finally` block before returning the connection to the worker pool.

### 4.3 Empirical Evaluation of PostgreSQL RLS
- **Current State**: `pg_policies` has **0 rows** in `glitters-core`.
- **Decision**:
  - Phase 1 (M3.2) implements multi-tenancy strictly at the **Django Application & Service Layer**.
  - Database RLS will be evaluated exclusively for **read-only Martin vector tile queries**, where Martin connects via dedicated, unpooled read-only database connections with mandatory session cleanup triggers.

---

## 5. Negative Security Test Suite

To prove security enforcement, the following negative test scenarios are automated in `tests/security/test_spatial_tenancy.py`:

| Test Name | Threat Vector Tested | Expected Result |
| :--- | :--- | :--- |
| `test_cross_org_spatial_read_blocked` | Org B user attempts to read Org A's private parcel by UUID. | HTTP 404 (Not Found) or 403 (Forbidden); zero geometry returned. |
| `test_cross_org_spatial_mutation_blocked` | Org B user attempts to update coordinates of Org A's building. | HTTP 403 Forbidden; transaction rejected. |
| `test_unauthenticated_private_spatial_denied` | Anonymous client queries private spatial endpoint. | HTTP 401 Unauthorized. |
| `test_surveyor_credential_gate` | Non-surveyor attempts to access raw beacon error ellipses. | HTTP 403 Forbidden (`SURVEYOR_CREDENTIAL_REQUIRED`). |
| `test_connection_pool_session_leak_prevented` | Repeated queries across alternating orgs on a shared connection. | Zero leakage of previous tenant's data across connection reuse. |

---
*Ready for Architectural Review and Stakeholder Approval.*
