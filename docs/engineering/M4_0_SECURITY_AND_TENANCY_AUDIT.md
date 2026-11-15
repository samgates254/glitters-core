# GLITTERS / GOD'S EYE — ARCHITECTURAL RED-TEAM AUDIT
## M4.0 Security, Multi-Tenancy & Cryptographic Governance Audit

**Document Reference:** `docs/engineering/M4_0_SECURITY_AND_TENANCY_AUDIT.md`
**Classification:** Security Architecture Red-Team Evaluation
**Target Systems:** GLITTERS Core (`apps.tenancy`, `apps.spatial`), PostgreSQL 16 RLS Engine, API Gateway

---

## 1. Executive Summary & Core Security Warnings

The security architecture proposed in `M4_SECURITY_AND_DATA_GOVERNANCE.md` introduces advanced concepts (PostgreSQL Row-Level Security, digital signatures, Merkle hash chains, sensitive infrastructure geofence masking).

However, the red-team audit reveals **three critical security risks and operational hazards**:
1. **Connection Pooling Tenant Context Leakage:** Implementing PostgreSQL RLS via session-level `SET app.current_tenant_id` without strict transactional cleanup will cause cross-tenant data leakage under pooled web connections.
2. **Conflation of Cryptographic Signatures with Legal Statutory Authority:** The specification assumes a digital cryptographic signature legally authorizes a boundary modification. In Kenyan law, statutory validity requires statutory authority, not just a cryptographic key.
3. **Sensitive Infrastructure Masking Bypasses:** The proposed geofence blurring only addresses vector tiles and imagery, leaving unrestricted access via raw bounding-box REST APIs, spatial export endpoints, and analytical calculations.

---

## 2. PostgreSQL Row-Level Security (RLS) Evaluation

### 2.1 The Connection Pool Pollution Hazard
The M4 specification proposed enabling PostgreSQL RLS using:
```sql
CREATE POLICY tenant_spatial_isolation_policy ON spatial_property_building_association
    AS RESTRICTIVE FOR ALL TO glitters_app_role
    USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid);
```

#### The Vulnerability Scenario:
1. In production, Django uses persistent connections (`CONN_MAX_AGE > 0`) or PgBouncer in transaction/session pooling mode.
2. Request 1 arrives from Tenant A. Middleware executes:
   ```sql
   SET app.current_tenant_id = 'tenant-a-uuid';
   ```
3. Request 1 completes. The connection is returned to the pool **with the session variable still set to Tenant A**.
4. Request 2 arrives from Tenant B (or an unauthenticated public user). If the application fails to overwrite or clear the setting, Request 2 runs with Tenant A's identity, exposing confidential land parcels and lease appraisals.
5. If an unhandled exception occurs before setting the variable, the query either crashes or silently inherits the prior tenant's context.

### 2.2 Mandatory Safe RLS Architecture
To safely implement PostgreSQL RLS, GLITTERS must enforce:
1. **Transaction-Scoped Settings:** Never use session-level `SET`. Use `SET LOCAL`:
   ```sql
   BEGIN;
   SET LOCAL app.current_tenant_id = 'tenant-a-uuid';
   -- Execute tenant queries
   COMMIT; -- Automatically clears variable upon transaction commit/rollback
   ```
2. **Django Atomic Block Context Manager:**
   ```python
   # apps/core/tenancy.py
   from django.db import transaction, connection
   from contextlib import contextmanager

   @contextmanager
   def tenant_context(tenant_id):
       with transaction.atomic():
           with connection.cursor() as cursor:
               cursor.execute("SET LOCAL app.current_tenant_id = %s;", [str(tenant_id)])
           yield
   ```
3. **Fail-Closed Default:** If `app.current_tenant_id` is null or empty, the RLS policy must deny all rows on tenant-confidential tables.

### 2.3 Verdict on RLS in M4.1
**Recommendation:** **DEFER database-level RLS activation in M4.1.**
The current application-layer tenant isolation (filtering via `organization_id` in Django QuerySets) is proven by 380 passing tests, including `test_cross_org_spatial_read_isolation`. Activating RLS before connection pooling middleware is verified in staging introduces high regression risk.

---

## 3. Cryptographic Signatures vs. Statutory Legal Authority

The M4 specification repeatedly asserts that an Ed25519 or ECDSA digital signature "seals statutory boundary mutations" and guarantees legal title validity.

### 3.1 The Legal Reality under Kenyan Cadastral Law
Under the **Survey Act (Cap 299)**, **Land Registration Act (2012)**, and **Sectional Properties Act (2020)**:
1. **Statutory Jurisdiction:** A signature is only legally valid if the signing individual was gazetted and held an active, un-suspended license issued by the **Land Surveyors Board (LSB)** on the date of execution.
2. **Deed Plan Approval:** No boundary modification creates statutory legal title until it receives an official **Survey Plan Number (FR Number)** and is authenticated by the **Director of Surveys** at Survey of Kenya (Ruaraka).
3. **Cryptographic Distinction:**

| Dimension | Cryptographic Signature (Ed25519) | Statutory Legal Authority (Kenya Cadastre) |
|:---|:---|:---|
| **What it Proves** | The private key was used to sign the hash of the geometry. | The boundary was approved under statutory authority of the Survey Act. |
| **Tamper Evidence** | High (any coordinate change breaks the hash). | Paper/sealed registry record holds legal precedence in court. |
| **Revocation** | Key compromised / certificate expired. | License suspended / court-ordered boundary cancellation. |
| **Legal Effect** | Procedural evidence of submission. | Conclusive legal title to real property. |

### 3.2 Required Architecture Correction
All models recording surveyor submissions must store:
- `surveyor_license_number` (Validated against LSB registry).
- `director_of_surveys_approval_ref` (Statutory FR reference).
- `statutory_status`: `PRELIMINARY_SURVEY_SUBMISSION` vs `AUTHENTICATED_GOVERNMENT_CADASTRE`.
Machine algorithms must never promote a preliminary survey to an authenticated statutory title.

---

## 4. Sensitive Infrastructure Protection & Bypass Prevention

The specification proposes geofence blurring for `RESTRICTED_DEFENSE` areas (e.g. Kenya Navy Base Mtongwe, Kilindini Port security berths).

### 4.1 Identified Vulnerability & Bypass Routes
The specification only proposed masking vector tiles and high-resolution drone orthomosaics. This creates trivial bypass routes:
1. **Raw GeoJSON API Bypass:** A client queries `GET /api/v1/spatial/buildings/?bbox=39.63,-4.09,39.66,-4.06` and receives raw precision coordinates.
2. **Spatial Analytics Bypass:** An analyst runs `compute_spatial_metrics` on a restricted defense building and reconstructs its footprint from perimeter and area calculations.
3. **Spatial Assistant Bypass:** A user asks the AI assistant: "List all buildings on the western shore of Kilindini harbour with height > 20m".

### 4.2 Comprehensive Masking Enforcement
Sensitive infrastructure masking must be enforced at the root database query layer (`SpatialQuerySet`):
```python
# apps/spatial/models.py

class SpatialQuerySet(models.QuerySet):
    def visible_to(self, user):
        if user.has_perm('spatial.view_restricted_defense'):
            return self
        # Intersect with restricted zones and mask or exclude
        return self.exclude(
            classification_tier='RESTRICTED_DEFENSE'
        ).exclude(
            geom__intersects=RestrictedZone.objects.filter(category='DEFENSE').values('geom')
        )
```
This single gate ensures vector tiles, REST APIs, analytics, exports, and the AI assistant cannot bypass security geofences.
