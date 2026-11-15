# GLITTERS / GOD'S EYE — M4 ARCHITECTURE SPECIFICATION
## Deliverable 8: Security, Governance & Data Sovereignty Architecture

**Document Reference:** `docs/engineering/M4_SECURITY_AND_DATA_GOVERNANCE.md`
**Classification:** Core Architectural Standard
**Milestone:** M4 — Advanced Spatial Intelligence & Digital Twin Ecosystem
**Target Systems:** GLITTERS Core (`apps.spatial`, `apps.tenancy`), PostgreSQL 16 RLS, HSM / Cryptographic Signatures

---

## 1. Executive Summary & Security Philosophy

Geospatial data intersects national security, critical infrastructure, personal property rights, and competitive commercial intelligence. A failure in spatial access control can expose classified maritime port infrastructure, reveal private financial land acquisitions, or corrupt statutory cadastral titles.

GLITTERS M4 establishes a zero-trust spatial data governance architecture enforcing:
1. **Multi-Tenant Row-Level Security (RLS):** Kernel-enforced spatial isolation preventing cross-tenant leakage even during complex topological join queries (`ST_Intersects`, `ST_DWithin`).
2. **5-Tier Data Sensitivity Classification:** Granular classification governing read, export, and analytical computation privileges.
3. **Surveyor Identity & Cryptographic Signing:** Hardware-backed or PKI digital signatures for statutory boundary alterations, complying with the Kenya Survey Act (Cap 299) and Kenya Data Protection Act (KDPA 2019).
4. **Tamper-Evident Merkle Audit Chain:** Cryptographic hash verification guaranteeing that no historical property deed or spatial boundary can be silently altered or retroactively deleted.

```
Zero-Trust Spatial Governance Pipeline:
┌────────────────────────────────────────────────────────┐
│ Client Request (GOD'S EYE Workstation / API Consumer)  │
│ Bearer JWT: [user_id, tenant_id, role, clearance_level]│
└───────────────────────────┬────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────┐
│ API Gateway & ABAC Policy Evaluator                    │
│ - Validates clearance vs Data Sensitivity Tier         │
│ - Injects `app.current_tenant_id` session variable     │
└───────────────────────────┬────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────┐
│ PostgreSQL 16 Native Row-Level Security (RLS) Engine   │
│ - POLICY: tenant_isolation_policy                      │
│ - Non-tenant spatial entities: Publicly readable       │
│ - Tenant-owned properties: Strictly scoped             │
└───────────────────────────┬────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────┐
│ Cryptographic Audit Engine (apps/audit/tamper_log.py)  │
│ - Merkle Tree Hash-Chaining for Geometry Mutations     │
│ - Licensed Surveyor Digital Signature Verification     │
└────────────────────────────────────────────────────────┘
```

---

## 2. PostgreSQL Row-Level Security (RLS) & Multi-Tenancy

In standard spatial databases, developer error in omitting `WHERE tenant_id = ...` leads to disastrous data leakage when running geographic intersection joins. GLITTERS mandates PostgreSQL database-level RLS.

### 2.1 RLS Implementation for Spatial & Property Associations
```sql
-- Enable RLS on Property and Association tables
ALTER TABLE spatial_property_building_association ENABLE ROW LEVEL SECURITY;
ALTER TABLE spatial_property_parcel_association ENABLE ROW LEVEL SECURITY;
ALTER TABLE property_property ENABLE ROW LEVEL SECURITY;

-- 1. Tenant Scoping Policy
CREATE POLICY tenant_spatial_isolation_policy ON spatial_property_building_association
    AS RESTRICTIVE
    FOR ALL
    TO glitters_app_role
    USING (
        tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
        OR current_setting('app.is_superuser', true) = 'true'
    );

-- 2. Public vs Private Spatial Entity Policy
-- Base spatial entities (OSM buildings, public roads) are visible to all tenants,
-- while private developmental geometries are visible only to the owning tenant.
ALTER TABLE spatial_entity ENABLE ROW LEVEL SECURITY;

CREATE POLICY spatial_entity_visibility_policy ON spatial_entity
    FOR SELECT
    TO glitters_app_role
    USING (
        classification_tier IN ('PUBLIC_OPEN', 'COMMERCIAL_PARTNER')
        OR owning_tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
        OR current_setting('app.is_superuser', true) = 'true'
    );
```

### 2.2 Proof of Cross-Tenant Join Protection
When Tenant A runs:
```sql
SELECT b.id, e.canonical_urn, p.property_name, p.valuation_kes
FROM spatial_building b
JOIN spatial_entity e ON b.spatialentity_ptr_id = e.id
LEFT JOIN spatial_property_building_association pba ON b.id = pba.building_id
LEFT JOIN property_property p ON pba.property_id = p.id
WHERE ST_Intersects(e.geometry, :search_polygon);
```
Even if Tenant B owns properties within `:search_polygon`, PostgreSQL's RLS engine drops Tenant B's association and property rows before returning the result. Tenant A sees only the public physical building geometry, with zero financial or ownership leakage.

---

## 3. Data Classification & Sensitivity Tiers

| Tier | Classification | Description | Access Control / Encryption |
|:---|:---|:---|:---|
| **Tier 1** | `PUBLIC_OPEN` | Public open data (OSM buildings, government gazetted roads, admin boundaries) | Publicly queryable; unencrypted at rest |
| **Tier 2** | `COMMERCIAL_PARTNER` | Commercial high-res satellite imagery, licensed business directories | Authenticated API clients; rate-limited |
| **Tier 3** | `TENANT_CONFIDENTIAL` | Internal real estate portfolios, unlisted lease agreements, private appraisals | Strict RLS; AES-256 field encryption |
| **Tier 4** | `REGULATED_CADASTRAL` | Official Land Registry titles, dispute filings, deed plans, survey marks | Surveyor/Gov Clearance; PKI verified |
| **Tier 5** | `RESTRICTED_DEFENSE` | Naval base perimeters, port security checkpoints, telecom trunk routes | High-Clearance Gov Only; Geofence-masked |

### 3.1 Dynamic Geofence Masking for Restricted Assets
When a non-authorized client queries areas overlapping `RESTRICTED_DEFENSE` zones (e.g. Kenya Navy Base Mtongwe or Kilindini Ammunition Berth):
- Vector features are blurred or omitted from MVT vector tiles.
- High-resolution drone orthomosaics are replaced with degraded 10-meter satellite tiles.
- Coordinates are snapped to 500-meter grid centroids.

---

## 4. Surveyor Credential Verification & Digital Signatures

Under Kenyan law (Survey Act Cap 299), any modification to legal boundary beacons or parcel coordinates must be executed by a registered licensed surveyor.

### 4.1 Surveyor Credential Verification
- Surveyors log in using multi-factor PKI authentication.
- GLITTERS validates the surveyor's license status against the Land Surveyors Board (LSB) API or internal cryptographic trust list.
- Unregistered users can submit proposed modifications, but they remain flagged `UNVERIFIED_DRAFT`.

### 4.2 Cryptographic Signing of Boundary Mutations
When a licensed surveyor approves a parcel realignment or subdivision:
1. The canonical WKT geometry is normalized and serialized:
   $$\text{Payload} = \text{SHA256}(\text{parcel\_number} \,\|\, \text{WKT\_normalized} \,\|\, \text{timestamp})$$
2. The surveyor's private key generates an ECDSA/Ed25519 signature over the payload.
3. The signature, surveyor license number, and public key certificate are permanently sealed in `CadastralSurveyMutation`.

---

## 5. Tamper-Evident Merkle Audit Engine

To provide court-admissible audit proof for land dispute litigation, spatial modifications are written to an append-only cryptographic ledger.

```
Merkle Audit Hash Chaining:
┌────────────────────────┐      ┌────────────────────────┐
│ Block N - 1            │      │ Block N                │
│ Prev Hash: 0x8a1f...   │◄─────┤ Prev Hash: 0x9b4c...   │
│ Entity: Parcel 104     │      │ Entity: Building 552   │
│ Geom Hash: 0x4f2a...   │      │ Geom Hash: 0x7e3d...   │
│ Block Hash: 0x9b4c...  │      │ Block Hash: 0x11e8...  │
└────────────────────────┘      └────────────────────────┘
```

### 5.1 Audit Verification Algorithm
```python
# apps/spatial/services/audit.py

import hashlib

def calculate_audit_block_hash(prev_hash, entity_urn, geometry_wkt, modified_by, timestamp):
    """
    Guarantees tamper-evidence. If any historical record is altered
    in the database, the hash chain breaks immediately.
    """
    hasher = hashlib.sha256()
    hasher.update(prev_hash.encode('utf-8'))
    hasher.update(entity_urn.encode('utf-8'))
    hasher.update(geometry_wkt.encode('utf-8'))
    hasher.update(modified_by.encode('utf-8'))
    hasher.update(timestamp.isoformat().encode('utf-8'))
    return hasher.hexdigest()
```

If an unauthorized DBA directly edits coordinates using raw `UPDATE spatial_entity SET geometry = ...`, the periodic automated integrity cron detects a checksum mismatch and triggers an emergency security alert.
