# GLITTERS M3.3 — Production Ingestion Execution Report

## Executive Summary

Under the authorized Master Spatial Data Ingestion Mandate, the initial production ingestion of the complete Mombasa OpenStreetMap building footprints dataset was executed on **2026-11-14**.

All **72,864** source records were parsed, validated, resolved, persisted, and published into the canonical PostgreSQL 16 / PostGIS 3.5 database.

---

## 1. Execution Parameters & Environmental Baseline

- **Management Command:** `python manage.py ingest_spatial_dataset`
- **Execution Mode:** Production (non-dry-run)
- **Source Dataset Path:** `/home/mr-robot/data/glitters-osm/processed/mombasa-buildings.geojson`
- **Source File Size:** 39,631,020 bytes
- **Verified SHA-256 Checksum:** `953ad716384f772639ce0f04a32a0dff0a712bb9f7f345782924ee8bdb63a5fb`
- **Canonical Dataset URN:** `urn:glitters:dataset:ke:msa:osm:buildings`
- **Version Tag:** `2026.11-v1`
- **Batch Chunk Size:** 1,000 records per transaction
- **Total Chunks Processed:** 73 chunks (72 chunks $\times$ 1,000 + 1 trailing chunk of 864)
- **Database Engine:** PostgreSQL 16.9 + PostGIS 3.5.2 (GEOS 3.12.1, PROJ 9.4.0)

---

## 2. Ingestion Execution Metrics & Throughput

| Ingestion Metric | Value |
| :--- | :--- |
| **Total Input Features** | **72,864** |
| **Terminal Disposition: INSERTED** | **72,864 (100.0%)** |
| **Terminal Disposition: UPDATED** | **0 (0.0%)** |
| **Terminal Disposition: UNCHANGED** | **0 (0.0%)** |
| **Terminal Disposition: QUARANTINED** | **0 (0.0%)** |
| **Geometry Repairs Performed** | **0 (100% synthetically valid WGS-84 MultiPolygons)** |
| **Processing Duration** | **521.706 seconds (~8 minutes 41 seconds)** |
| **Average Ingestion Throughput** | **139.7 records / second** |
| **Peak Memory Consumption** | **270.81 MB** |
| **Reconciliation Invariant Verification** | **Passed ($72,864 = 72,864 + 0 + 0 + 0$)** |

---

## 3. Database Write Delta & Table Accounting

| Table Name | Count Before Ingestion | Count After Ingestion | Net Delta | State Integrity Verification |
| :--- | :--- | :--- | :--- | :--- |
| `spatial_entity` | 2 | 72,866 | **+72,864** | Parent entity UUIDs minted with canonical URNs |
| `spatial_building` | 2 | 72,866 | **+72,864** | Multi-Table Inheritance child rows linked via FK |
| `spatial_identity_mapping` | 0 | 72,864 | **+72,864** | Canonical UUID $\leftrightarrow$ OSM provider ID (`way/<osm_id>`) |
| `spatial_source_feature` | 0 | 72,864 | **+72,864** | Verbatim raw properties & geometry preserved |
| `spatial_quarantine` | 0 | 0 | **0** | Zero validation failures or rejected records |
| `spatial_dataset_version` | 5 | 5 | **0 (Updated)** | Status transitioned: `REGISTERED` $\rightarrow$ `INGESTING` $\rightarrow$ `VALIDATING` $\rightarrow$ `PUBLISHED` |
| `spatial_dataset_quality_report`| 0 | 1 | **+1** | Automated quality report linked to published version |

---

## 4. Attribute Completeness Breakdown

An audit of the ingested 72,864 buildings reveals the real-world attribute distribution captured from OpenStreetMap:

| Attribute Dimension | Populated Count | Completeness Ratio | Notes |
| :--- | :--- | :--- | :--- |
| **Named Buildings** | 269 | 0.37% | Specific named landmarks, towers, hotels, and schools |
| **Floor / Level Tags** | 212 | 0.29% | Normalized above-ground stories (1 to 11 stories) |
| **Height Tags** | 12 | 0.02% | Explicit physical metric heights recorded |
| **Address Tags (`addr:*`)** | 102 | 0.14% | Street name, house number, or postal code tags |

### Classification Distribution:
- **OTHER (General Unclassified):** 72,071 (98.91%)
- **RESIDENTIAL:** 447 (0.61%)
- **INDUSTRIAL / WAREHOUSING:** 191 (0.26%)
- **COMMERCIAL:** 95 (0.13%)
- **INSTITUTIONAL (Schools / Healthcare):** 52 (0.07%)
- **CIVIC (Government / Public):** 8 (0.01%)

---

## 5. Dataset Version Publication

Upon streaming chunk completion, the automated publication validation gate executed:
1. Verified source file checksum matches `953ad716384f772639ce0f04a32a0dff0a712bb9f7f345782924ee8bdb63a5fb`.
2. Verified invariant $72,864 = 72,864 + 0 + 0 + 0$.
3. Verified parity: $\text{Entities} (72,864) == \text{Buildings} (72,864) == \text{Mappings} (72,864) == \text{SourceFeatures} (72,864)$.
4. Marked `DatasetVersion.status = DatasetVersionStatus.PUBLISHED`.
5. Created canonical `DatasetQualityReport` (`valid_geometries = 72,864`, `horizontal_rmse_m = 1.000m`).
