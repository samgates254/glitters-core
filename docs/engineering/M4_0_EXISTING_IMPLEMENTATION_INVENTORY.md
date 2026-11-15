# GLITTERS / GOD'S EYE — ARCHITECTURAL RED-TEAM AUDIT
## M4.0 Existing Implementation & Baseline Capability Inventory

**Document Reference:** `docs/engineering/M4_0_EXISTING_IMPLEMENTATION_INVENTORY.md`
**Classification:** Baseline Implementation Audit
**Scope:** Exhaustive inventory of backend, frontend, and engines assets
**Target Repositories:**
- Backend: `/home/mr-robot/Projects/glitters-core`
- Frontend: `/home/mr-robot/Projects/glitters-ui-lab`
- Engines: `/home/mr-robot/Projects/glitters-engines`

---

## 1. Repository Git Baseline & Worktree Integrity

| Repository | Active Branch | HEAD Commit Hash | Worktree State | Untracked Scope |
|:---|:---|:---|:---|:---|
| `glitters-core` | `feature/m7-tenancy-operations-preservation` | `7f61a984a49ccb7c6edd826f4717d4e7e20b5256` | Dirty (17 tracked modifications) | `apps/spatial/`, `apps/tenancy/`, `apps/operations/`, `apps/solutions/`, M3/M4 docs |
| `glitters-ui-lab` | `preservation/baseline-v1.0` | `2d8ed574bf20920f3dd7934e6aa88bde0f7811e5` | Dirty (6 tracked modifications) | `app/gods-eye/`, `app/property-atlas/`, `components/gods-eye/`, `lib/gods-eye/` |
| `glitters-engines` | `preservation/engines-m1` | `55dd87ce2dc6d2fcfd90c75ee9fb72a03f64b73f` | Clean tracked (1 untracked test) | `glitters_portfolio_intelligence/tests/test_remediation_sprint_r1_3.py` |

*Preservation Mandate Verification:* All existing branches, commits, staged changes, and untracked files are preserved with zero modifications or resets.

---

## 2. Backend Inventory (`glitters-core`)

### 2.1 Django Settings & Installed Apps (`config/settings/base.py`)
- **Installed Applications:**
  - `django.contrib.admin`, `auth`, `contenttypes`, `sessions`, `messages`, `staticfiles`, `gis` (GeoDjango).
  - `rest_framework`, `corsheaders`, `drf_spectacular`.
  - Core domain apps: `apps.core`, `apps.organizations`, `apps.properties`, `apps.tenancy`, `apps.marketplace`, `apps.operations`, `apps.solutions`, `apps.spatial`.
- **Database Engine:** `django.contrib.gis.db.backends.postgis` connected to PostgreSQL 16.9 + PostGIS 3.5.2 on port 5434.
- **Cache Engine:** Redis cache backend (`redis://127.0.0.1:6379/1`).

### 2.2 Spatial Domain Models (`apps/spatial/models.py`)

| Model Name | Table Name | Key Fields | Implementation Status | Row Count |
|:---|:---|:---|:---|:---|
| `SpatialEntity` | `spatial_entity` | `id`, `canonical_urn`, `entity_type`, `organization`, `dataset_version`, `lifecycle_status`, `valid_from`, `valid_to`, `superseded_by` | **Verified Implementation** | 72,866 |
| `Building` | `spatial_building` | `spatialentity_ptr_id`, `name`, `classification`, `height_m`, `levels_above_ground`, `footprint_area_sqm`, `geom` (MultiPolygon, SRID 4326) | **Verified Implementation** | 72,866 |
| `SpatialIdentityMapping` | `spatial_identity_mapping` | `entity_id`, `provider_name`, `provider_external_id`, `canonical_urn`, `created_at` | **Verified Implementation** | 72,864 |
| `SpatialSourceFeature` | `spatial_source_feature` | `dataset_version_id`, `provider_name`, `source_record_id`, `source_properties`, `source_geometry`, `repaired_geometry`, `disposition` | **Verified Implementation** | 72,864 |
| `DatasetRegistry` | `spatial_dataset_registry` | `dataset_urn`, `name`, `category`, `update_frequency` | **Verified Implementation** | 4 |
| `DatasetVersion` | `spatial_dataset_version` | `dataset`, `version_tag`, `source_checksum_sha256`, `feature_count`, `status` | **Verified Implementation** | 5 |
| `DatasetQualityReport` | `spatial_dataset_quality_report`| `version_id`, `valid_geometries`, `repaired_geometries`, `quarantined_geometries` | **Verified Implementation** | 1 |
| `SpatialQuarantine` | `spatial_quarantine` | `dataset_version`, `source_record_id`, `error_type`, `raw_payload`, `quarantined_at` | **Verified Implementation** | 0 |
| `EntityLineage` | `spatial_entity_lineage` | `predecessor_urn`, `successor_urn`, `lineage_type`, `effective_date`, `authorization_reference` | **Verified Implementation** | 3 |
| `LandParcel` | `spatial_land_parcel` | `spatialentity_ptr_id`, `parcel_number`, `cadastral_status`, `geom` (MultiPolygon) | **Existing Implementation (Empty)** | 0 |
| `Road` | `spatial_road` | `spatialentity_ptr_id`, `name`, `road_class`, `geom` (MultiLineString) | **Existing Implementation (Empty)** | 0 |
| `ConstructionSite` | `spatial_construction_site` | `spatialentity_ptr_id`, `project_name`, `stage`, `geom` (Polygon) | **Existing Implementation (Empty)** | 0 |
| `SurveyObservation` | `spatial_survey_observation` | `spatialentity_ptr_id`, `surveyor_registration_no`, `instrument_type`, `geom` (Geometry) | **Existing Implementation (Empty)** | 0 |
| `InfrastructureAsset` | `spatial_infrastructure_asset`| `spatialentity_ptr_id`, `asset_category`, `geom` (Geometry) | **Existing Implementation (Empty)** | 0 |
| `AdministrativeBoundary`| `spatial_administrative_boundary`| `spatialentity_ptr_id`, `admin_level`, `pcode`, `geom` (MultiPolygon) | **Existing Implementation (Empty)** | 0 |
| `TerrainFeature` | `spatial_terrain_feature` | `spatialentity_ptr_id`, `terrain_type`, `geom` (MultiPolygon) | **Existing Implementation (Empty)** | 0 |
| `ImageryObservation` | `spatial_imagery_observation` | `spatialentity_ptr_id`, `sensor_platform`, `cloud_cover_pct`, `geom` (Polygon) | **Existing Implementation (Empty)** | 0 |
| `BuildingParcelIntersection`| `spatial_building_parcel_intersection`| `building_id`, `parcel_id`, `intersection_area_sqm`, `relationship_type` | **Existing Implementation (Empty)** | 0 |
| `PropertyBuildingAssociation`| `spatial_property_building_association`| `property_id`, `building_id`, `association_type` | **Existing Implementation (Empty)** | 0 |
| `PropertyParcelAssociation`| `spatial_property_parcel_association`| `property_id`, `parcel_id`, `association_type` | **Existing Implementation (Empty)** | 0 |

### 2.3 Spatial Migrations Status
- `0001_initial.py`: Created complete spatial hierarchy, base models, MTI child models, association models, and indexes. Applied.
- `0002_spatialsourcefeature.py`: Added `repaired_geometry` to `SpatialSourceFeature`. Applied.
- `0003_datasetversion_status.py`: Added `status` FSM field (`REGISTERED`, `INGESTING`, `VALIDATING`, `PUBLISHED`, `FAILED`, `CANCELLED`) to `DatasetVersion`. Applied.

### 2.4 Spatial Services & API Endpoints
- **Services:**
  - `apps/spatial/services/ingestion.py`: Streaming GeoJSON validator, geometry repair (`make_valid`, `orient_polygon`), identity reconciliation FSM, transactional chunk bulk persistence.
  - `apps/spatial/services/validation.py`: Envelope check (`mombasa-envelope`), projection validation (UTM Zone 37S), geometry topology validation.
- **REST APIs (`apps/spatial/views.py`):**
  - `GET /api/v1/spatial/health/`: Readiness check verifying PostGIS connectivity.
  - `GET /api/v1/spatial/datasets/layers/`: Metadata catalog of published spatial layers.
  - `GET /api/v1/spatial/buildings/`: Bounding box filtered buildings GeoJSON.
  - `GET /api/v1/spatial/buildings/lookup/?urn=...`: Canonical URN lookup.
  - `GET /api/v1/spatial/buildings/tiles/{z}/{x}/{y}/`: High-performance dynamic PostGIS vector tiles (`ST_TileEnvelope` + `ST_AsMVT`).

---

## 3. Frontend Inventory (`glitters-ui-lab`)

### 3.1 Workstation Components & State Architecture
- **Framework:** Next.js 15.1 (App Router), React 19, Tailwind CSS 3.4.
- **Pages:**
  - `/gods-eye`: Primary geospatial intelligence workstation.
  - `/property-atlas`: Property search and administrative catalog.
- **Viewport Architecture (`components/gods-eye/viewport/`):**
  - `spatial-viewport.tsx`: Orchestrator managing seamless toggling between 3D and 2D modes.
  - `cesium-viewport.tsx`: CesiumJS 3D rendering engine. Integrates Cesium OSM 3D Buildings (Asset 96188), camera controls, tile streaming, and entity picking.
  - `maplibre-viewport.tsx`: MapLibre GL 2D/3D vector map engine. Renders vector tiles, building extrusions, range rings, and spatial zones.
- **Workstation Controls (`components/gods-eye/workstation/`):**
  - `left-explorer.tsx`: Layer tree, category filters, and active dataset toggles.
  - `top-command-bar.tsx`: Viewport mode switcher (3D / 2D / Split), coordinate telemetry, and search input.
  - `right-inspector.tsx`: Contextual building detail card displaying height, levels, area, classification, and canonical URN.
  - `bottom-workspace.tsx`: Measurement telemetry, spatial buffer tools, and activity logs.
- **Layer Registry (`lib/gods-eye/layers/layer-registry.ts`):**
  - Manages active layers including Cesium 3D Buildings, live PostGIS MVT tiles (`http://127.0.0.1:8001/api/v1/spatial/buildings/tiles/{z}/{x}/{y}/`), static PMTiles, urban corridors, and satellite imagery basemaps.

---

## 4. Engines Inventory (`glitters-engines`)

`glitters-engines` is an independent repository containing mature computational and domain engines that the authored M4 architecture improperly duplicated:

| Engine Package | Key Modules | Capabilities Directly Reusable in M4 |
|:---|:---|:---|
| `glitters_geospatial_engine` | `geometry.algorithms`<br>`evaluation.metrics`<br>`remote_sensing.indices`<br>`change_detection.temporal_engine`<br>`raster.pipeline`<br>`network_analysis.routing`<br>`terrain.hydrology` | - NDVI, NDWI, EVI, SAVI spectral index calculations.<br>- Planar area, perimeter, and boundary compactness metrics.<br>- Temporal difference and building change detection algorithms.<br>- Hydrological flow, drainage, and slope terrain analysis.<br>- Multi-modal network routing and shortest path calculation. |
| `glitters_construction_engine` | `scheduling.cpm`<br>`scheduling.pert`<br>`scheduling.rcpsp`<br>`cost.boq`<br>`cost.cost_estimation`<br>`project_model.engineering_specs` | - Critical Path Method (CPM) and PERT scheduling.<br>- Bill of Quantities (BOQ) parsing and cost estimation.<br>- Resource-constrained project scheduling for construction sites. |
| `glitters_financial_intelligence`| `accounting`<br>`financial_graph`<br>`forecasting`<br>`risk`<br>`reconciliation` | - Financial entity graph modeling.<br>- Multi-currency cashflow forecasting.<br>- Valuation risk and anomaly detection. |
| `glitters_valuation_engine` | `hedonic`<br>`dcf`<br>`comparable_sales` | - Automated Valuation Model (AVM) for land and buildings. |

---

## 5. Classification of Proposed M4 Capabilities

Every capability described in the ten M4 specifications is classified under one of the five mandatory audit categories:

### Category 1: Verified Implementation
1. PostgreSQL 16 + PostGIS 3.5 database foundation (`apps.core`).
2. Parent-child Multi-Table Inheritance model hierarchy (`SpatialEntity` + `Building`).
3. Ingestion and preservation of all 72,864 Mombasa OSM building footprints with SHA-256 verification.
4. Dataset versioning state machine (`DatasetVersionStatus` FSM).
5. Dynamic PostGIS vector tile endpoint (`/api/v1/spatial/buildings/tiles/{z}/{x}/{y}/`).
6. GOD'S EYE workstation dual-mode viewport (Cesium 3D + MapLibre 2D).
7. Building inspection card displaying canonical identity, height, and classification.
8. 41 / 41 passing spatial tests in `glitters-core`.

### Category 2: Existing Implementation with Limitations
1. **Building-Parcel Associations:** Models exist (`BuildingParcelIntersection`, `PropertyBuildingAssociation`) with 0 rows. No automated spatial topological intersection service has been executed to populate them.
2. **Entity Lineage:** `EntityLineage` model exists with 3 test rows, but uses `predecessor_urn` / `successor_urn` strings rather than relational foreign keys, and lacks an automated API query service.
3. **Temporal Tracking:** `valid_from` and `valid_to` exist on `SpatialEntity`, but `superseded_at` is missing, and the `BuildingViewSet` lacks an `?as_of=YYYY-MM-DD` query filter.
4. **Multi-Tenancy Isolation:** `organization_id` foreign key exists on `SpatialEntity` and passes isolation tests, but PostgreSQL database-level Row-Level Security (RLS) is not enabled.
5. **GOD'S EYE 3D Tiles:** Cesium renders public Cesium Ion Asset 96188; it does not yet stream internal PostGIS-derived 3D tiles.

### Category 3: Documented but Unimplemented Design
1. **Bi-Temporal Time-Travel API:** `?as_of=` and `?as_was=` query filters on REST and tile endpoints.
2. **Geometric Analytics Service:** Synchronous PostGIS calculation of compactness ($Q$), Gross Floor Area, and road reserve setbacks.
3. **Quarantine HITL Review Interface:** Web interface for inspecting invalid or quarantined spatial features.
4. **Cadastral Title Association:** Automated linkage between registered Land Titles and physical Building footprints.

### Category 4: Proposed Future Capability
1. **STAC v1.0.0 Catalog & TiTiler Dynamic Raster Server:** Streaming high-resolution drone orthomosaics and multispectral satellite imagery.
2. **Automated ML Change Detection Pipeline:** Computer vision differencing for building appearance, demolition, and construction progress.
3. **Natural Language Spatial Assistant:** LLM tool-calling orchestrator for spatial queries and camera navigation.
4. **Custom OGC 3D Tiles 1.1 Export Pipeline:** Backend generation of `.b3dm` / `.glb` quadtrees for all city structures.
5. **Merkle Tree Cryptographic Audit Ledger:** Block-hashed tamper-evident transaction chaining.

### Category 5: Unverified Claim Requiring Additional Evidence / Correction
1. **"Parent Geometry Column":** Claim that `SpatialEntity` possesses a `geometry` column. (Disproven: geometry is on child tables).
2. **"Hilbert GiST Index":** Claim that `CLUSTER ... USING gist` implements a Hilbert curve. (Disproven: standard GiST uses R-Trees).
3. **"Commercial Satellite Streaming":** Claim that live Maxar WorldView-3 / PlanetScope imagery is available without verified contracts. (Unverified: no enterprise API credentials exist).
4. **"Universal FAR/GCR Engine":** Claim that planning rules can be calculated universally across Kenya without local by-law engines. (Disproven: planning by-laws differ across jurisdictions).
