# GLITTERS M3.4 — Spatial API Architecture & Security Specification

## Executive Summary

GLITTERS M3.4 establishes the versioned REST API architecture for spatial intelligence, dataset discovery, building lookups, bounding-box queries, and multi-tenant isolation in `apps.spatial`.

---

## 1. Spatial API Endpoints Specification

All endpoints are versioned under `/api/v1/spatial/`.

### 1.1 Dataset Catalogue
- **Route:** `GET /api/v1/spatial/datasets/`
- **Action:** Lists registered spatial datasets and version history.
- **Permissions:** `AllowAny` (Public read-only).

### 1.2 Spatial Layer Catalogue
- **Route:** `GET /api/v1/spatial/datasets/layers/`
- **Action:** Surfaces active published geospatial layers for frontend map rendering.
- **Response Format:**
  ```json
  {
    "layers": [
      {
        "id": "buildings-2026.11-v1",
        "name": "Mombasa OSM Building Footprints",
        "dataset_urn": "urn:glitters:dataset:ke:msa:osm:buildings",
        "version_tag": "2026.11-v1",
        "category": "BUILDINGS",
        "license": "ODbL",
        "attribution": "© OpenStreetMap contributors",
        "feature_count": 72864,
        "min_zoom": 0,
        "max_zoom": 22,
        "published_at": "2026-11-14T00:11:28.014132+00:00",
        "extent": { "type": "Polygon", "coordinates": [...] },
        "tile_url_template": "/api/v1/spatial/buildings/tiles/{z}/{x}/{y}/"
      }
    ]
  }
  ```

### 1.3 Building Search & Bounding-Box Query
- **Route:** `GET /api/v1/spatial/buildings/`
- **Query Parameters:**
  - `bbox`: `min_lon,min_lat,max_lon,max_lat` (filters footprints intersecting bounding box)
  - `classification`: `RESIDENTIAL`, `COMMERCIAL`, `INDUSTRIAL`, etc.
  - `search`: string match on building name, external ID, or canonical URN
- **Pagination:** Standard DRF page-number pagination enforced to prevent unrestricted full-dataset dumps.

### 1.4 Deterministic Building Lookup
- **Route:** `GET /api/v1/spatial/buildings/lookup/?id={identifier}&provider={provider}`
- **Action:** Resolves an entity deterministically by:
  1. External provider ID (e.g. `1151899424` or `way/1151899424` under provider `osm`).
  2. Canonical URN (e.g. `urn:glitters:spatial:building:856c1362-f681-43e0-8b74-7b4e881c13c0`).
  3. Canonical UUID primary key.
- **Enriched Provenance Payload:** Returns canonical building attributes, GeoJSON footprint geometry, external mappings, dataset version metadata, and verbatim source feature properties.

### 1.5 Spatial Platform Health Check
- **Route:** `GET /api/v1/spatial/health/`
- **Action:** Validates PostGIS database connectivity, engine version, and published building counts.
- **Response Format:**
  ```json
  {
    "status": "healthy",
    "postgis_version": "3.5 USE_GEOS=1 USE_PROJ=1 USE_STATS=1",
    "published_building_count": 72864,
    "database_engine": "PostgreSQL 16 + PostGIS 3.5"
  }
  ```

---

## 2. Spatial Tenancy and Security Model

1. **Public vs. Tenant Scoping:**
   - Canonical buildings with `organization = NULL` represent public baseline open data (e.g. Mombasa OSM buildings). Public buildings are visible to all users and anonymous callers.
   - Buildings with `organization = <Org_UUID>` represent private, tenant-scoped properties. They are filtered strictly via `Building.objects.for_organization(request.organization)` and are invisible to cross-organization callers.
2. **Publication Isolation:**
   - Endpoints automatically exclude datasets in `INGESTING`, `FAILED`, or `CANCELLED` states. Incomplete or corrupted datasets cannot leak into consumer querysets.
3. **Mutation Restrictions:**
   - `BuildingViewSet` and `SpatialEntityViewSet` inherit `ReadOnlyModelViewSet`. Public or API clients cannot mutate spatial geometries or attributes over standard HTTP methods. Mutations require authenticated surveyor credentials and dedicated audit endpoints.
