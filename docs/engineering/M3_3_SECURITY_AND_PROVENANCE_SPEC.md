# GLITTERS CORE — MILESTONE M3.3
## Spatial Security, Tenancy & Provenance Specification

**Document:** `M3_3_SECURITY_AND_PROVENANCE_SPEC.md`
**Classification:** Technical Architecture Specification
**Repository:** `~/Projects/glitters-core`
**Date:** 2026-11-14
**Author:** Principal Geospatial Architect & Core Engineering Review Board
**Status:** Approved for Planning — Pre-Ingestion Architecture Gate

---

## 1. Tenancy Model: Public Baseline vs. Private Spatial Assets

GLITTERS implements a dual-layer spatial tenancy model enforced directly through `SpatialQuerySet.for_organization()`:

```
+-----------------------------------------------------------------------------------------+
|                                  SPATIAL TENANCY LAYERS                                 |
|                                                                                         |
|   +---------------------------------------------------------------------------------+   |
|   | Public Spatial Baseline (organization = NULL)                                   |   |
|   | - Mombasa OSM Building Footprints (72,864 records)                              |   |
|   | - ODbL Open Data, Publicly readable across all tenants                          |   |
|   | - Read-only for all organization users; mutable only via ingestion pipeline     |   |
|   +---------------------------------------------------------------------------------+   |
|                                                                                         |
|   +---------------------------------------------------------------------------------+   |
|   | Tenant Private Spatial Assets (organization = org_uuid)                         |   |
|   | - Proprietary Land Parcels, Internal Site Surveys, Leased Building Units        |   |
|   | - Strictly isolated by Organization foreign key                                 |   |
|   | - Never visible to cross-tenant users                                           |   |
|   +---------------------------------------------------------------------------------+   |
+-----------------------------------------------------------------------------------------+
```

### Security Invariants:
1. **Public Ingestion Isolation:** The 72,864 Mombasa OSM building records are ingested with `organization = NULL`.
2. **Universal Read Access:** Any authenticated organization member (or public API caller where permitted) can view baseline OSM buildings.
3. **Cross-Tenant Mutation Barrier:** Organization members can **never** update, reclassify, or delete public baseline buildings. Any attempt by tenant users to mutate an entity where `organization is NULL` raises a permission denial.
4. **Tenant Annotations / Associations:** When a tenant links a property to an OSM building via `PropertyBuildingAssociation`, the building remains public and unmutated; the junction record belongs to the tenant.

---

## 2. Provenance Architecture & Dataset Versioning

The Mombasa OSM dataset is governed by strict cryptographic provenance:

### 2.1 Cryptographic Source Verification
- **Authoritative Dataset File:**
  `/home/mr-robot/data/glitters-osm/processed/mombasa-buildings.geojson`
- **File Size:** $39,631,020\text{ bytes}$
- **Cryptographic Hash (SHA-256):**
  `953ad716384f772639ce0f04a32a0dff0a712bb9f7f345782924ee8bdb63a5fb`
- **Feature Count:** Exactly $72,864\text{ features}$.
- **Verification Gate:** The ingestion CLI computes the SHA-256 digest of the input file before opening. If the checksum does not match the registered version record, ingestion halts immediately.

### 2.2 Dataset Registry & Version Records
Every ingestion run references a canonical root dataset and registers an immutable version record:

```python
# Root Registry Record
DatasetRegistry.objects.get_or_create(
    dataset_urn="urn:glitters:dataset:ke:msa:osm:buildings",
    defaults={
        "name": "Mombasa OSM Building Footprints",
        "description": "Comprehensive building footprints in Mombasa County extracted from OpenStreetMap.",
        "category": DatasetCategory.BUILDINGS,
        "update_frequency": UpdateFrequency.STATIC,
    }
)

# Immutable Dataset Version Record
DatasetVersion.objects.create(
    dataset=registry,
    version_tag="2026.11-v1",
    source_url="https://www.openstreetmap.org",
    source_checksum_sha256="953ad716384f772639ce0f04a32a0dff0a712bb9f7f345782924ee8bdb63a5fb",
    pipeline_git_commit="7f61a98", # current commit
    license_type=LicenseType.ODBL,
    attribution_statement="© OpenStreetMap contributors",
    spatial_extent=Polygon.from_bbox((39.549644, -4.200105, 39.792528, -3.869816)),
    feature_count=72864,
)
```

---

## 3. Legal Authority vs. Positional Accuracy

A fundamental architectural principle of GLITTERS is the strict decoupling of **Legal Authority** from **Positional Accuracy**:

```
+-----------------------------------------------------------------------------------------+
|                                TWO INDEPENDENT DIMENSIONS                               |
|                                                                                         |
|   +---------------------------------------+   +-------------------------------------+   |
|   | 1. Positional Accuracy (horizontal_rmse_m)|   | 2. Legal Authority (legal_authority_level)  |
|   | - Physical survey accuracy in meters  |   | - Evidentiary standing under Kenya  |   |
|   | - High precision: 0.010m (RTK GNSS)   |   |   Land Act & Survey Act             |   |
|   | - Moderate: 1.000m (OSM digitizing)   |   | - Statutory Authoritative (Registry)|   |
|   | - Coarse: 5.000m (Sentinel satellite) |   | - Open Data (Observational only)    |   |
|   +---------------------------------------+   +-------------------------------------+   |
+-----------------------------------------------------------------------------------------+
```

### Application to Mombasa OSM Ingestion:
1. **`horizontal_rmse_m = 1.000`**: Reflects typical high-resolution aerial imagery tracing error in OpenStreetMap urban contexts.
2. **`legal_authority_level = LegalAuthorityLevel.OPEN_DATA`**:
   - Explicitly designates records as observational open data.
   - Prohibits automated legal parcel division or cadastral adjudication based on OSM footprints.
   - Prevents legal liability in property transactions.
3. **`is_human_verified = False`**: Indicates that while topologically valid, individual building identities have not yet undergone ground truthing by a registered GLITTERS field agent.
