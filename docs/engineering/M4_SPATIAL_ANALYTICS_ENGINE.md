# GLITTERS / GOD'S EYE — M4 ARCHITECTURE SPECIFICATION
## Deliverable 6: Spatial Analytics & Computational Services Engine

**Document Reference:** `docs/engineering/M4_SPATIAL_ANALYTICS_ENGINE.md`
**Classification:** Core Architectural Standard
**Milestone:** M4 — Advanced Spatial Intelligence & Digital Twin Ecosystem
**Target Systems:** GLITTERS Core (`apps.spatial.analytics`), PostGIS 3.5, Celery Workers, Redis

---

## 1. Executive Summary & Computational Philosophy

Spatial analytics in GLITTERS is not a static reporting feature; it is an active computational engine that derives high-value economic, structural, legal, and environmental intelligence from raw geometries.

### Design Principles:
1. **Push Computation to Data:** Geometric operations (intersections, buffers, distance matrices, voronoi diagrams) are executed in PostgreSQL/PostGIS at the database layer rather than serializing millions of coordinates into Python.
2. **Tiered Execution Pipeline:**
   - **Hot Path (Synchronous $< 100\text{ ms}$):** Point queries, single-building geometry metrics, setback compliance, parcel zoning checks.
   - **Cold Path (Asynchronous Celery Task):** City-wide isochrone calculation, cut-and-fill volumetric earthworks, multi-epoch change audits, and dense Voronoi catchment modeling.
3. **Deterministic Formulae:** All indices (GCR, FAR, Compactness, Solar Yield) are calculated from mathematically proven, reproducible equations.

```
Analytics Processing Pipeline:
┌────────────────────────┐      ┌────────────────────────┐
│ Synchronous Request    │      │ Asynchronous Request   │
│ (e.g. Building Metrics)│      │ (e.g. Cut & Fill 50km²)│
└───────────┬────────────┘      └───────────┬────────────┘
            │                               │
            ▼                               ▼
┌────────────────────────┐      ┌────────────────────────┐
│ Fast PostGIS SQL View  │      │ Celery Background Task │
│ (Indexed GiST Queries) │      │ (Redis Task Queue)     │
└───────────┬────────────┘      └───────────┬────────────┘
            │                               │
            │                               ▼
            │                   ┌────────────────────────┐
            │                   │ Chunked PostGIS Worker │
            │                   │ + NumPy / SciPy Engine │
            │                   └───────────┬────────────┘
            │                               │
            ▼                               ▼
┌────────────────────────────────────────────────────────┐
│ Unified JSON Response / Materialized Cache             │
│ (Redis TTL Cached / Saved to Property Profile)         │
└────────────────────────────────────────────────────────┘
```

---

## 2. Building Intelligence Analytics

Every building footprint ingested into GLITTERS is enriched with structural and morphological metrics:

### 2.1 Morphological & Geometric Indices
- **Planar Area ($A$) and Perimeter ($P$):**
  $$A = \text{ST\_Area}(\text{ST\_Transform}(\text{geom}, 32737)) \quad [\text{m}^2]$$
  $$P = \text{ST\_Perimeter}(\text{ST\_Transform}(\text{geom}, 32737)) \quad [\text{m}]$$
  *(Projected into UTM Zone 37S / EPSG:32737 for accurate metric measurement in Mombasa).*
- **Isoperimetric Quotient (Compactness $Q$):**
  $$Q = \frac{4 \pi A}{P^2}$$
  $Q = 1.0$ for a perfect circle; rectangular buildings typically score $0.60 - 0.78$; irregular or sprawling informal structures score $< 0.45$.
- **Gross Floor Area (GFA) & Estimated Volume ($V$):**
  $$\text{GFA} = A \times \text{levels}$$
  $$V = A \times H \quad [\text{m}^3]$$
- **Solar Roof Potential:**
  Evaluates rooftop solar suitability based on unobstructed flat or north/south pitched roof area, minus shadow occlusions cast by adjacent structures:
  $$E_{\text{solar}} = A_{\text{effective}} \times G_{\text{annual}} \times \eta \times \text{PR} \quad [\text{kWh/year}]$$
  where $G_{\text{annual}} \approx 2050\text{ kWh/m}^2$ for coastal Kenya, $\eta = 0.20$ (panel efficiency), and $\text{PR} = 0.75$ (performance ratio).

### 2.2 Regulatory Setback Verification SQL
```sql
-- Compute whether a building breaches the statutory 6-meter road reserve setback
SELECT
    b.id,
    b.canonical_urn,
    r.name AS road_name,
    ST_Distance(
        ST_Transform(e.geometry, 32737),
        ST_Transform(r.geometry, 32737)
    ) AS distance_to_road_m,
    CASE
        WHEN ST_Distance(ST_Transform(e.geometry, 32737), ST_Transform(r.geometry, 32737)) < 6.0
        THEN true ELSE false
    END AS is_setback_violation
FROM spatial_building b
JOIN spatial_entity e ON b.spatialentity_ptr_id = e.id
JOIN spatial_road r ON ST_DWithin(ST_Transform(e.geometry, 32737), ST_Transform(r.geometry, 32737), 15.0)
WHERE b.id = :building_id;
```

---

## 3. Land & Cadastral Intelligence Analytics

Land analytics evaluate legal utilization, development density, and subdivision feasibility:

### 3.1 Planning & Zoning Compliance Metrics
- **Ground Coverage Ratio (GCR):**
  $$\text{GCR} = \frac{\sum A_{\text{footprints}}}{A_{\text{parcel}}}$$
  Measures horizontal plot coverage against municipal zoning limits (e.g., max 60% for residential zone R2).
- **Floor Area Ratio (FAR / Plot Ratio):**
  $$\text{FAR} = \frac{\sum \text{GFA}}{A_{\text{parcel}}}$$
  Controls vertical density (e.g., max FAR 3.5 in Mombasa Commercial CBD).
- **Subdivision Feasibility Engine:**
  Evaluates whether a parcel can be legally subdivided into $N$ daughter parcels:
  1. Checks if $A_{\text{parcel}} \ge N \times \text{Area}_{\text{min\_lot\_size}}$.
  2. Evaluates road frontage:
     $$\text{Frontage} = \text{ST\_Length}(\text{ST\_Intersection}(\text{parcel\_boundary}, \text{road\_reserve}))$$
     Requires $\text{Frontage} \ge N \times 9.0\text{ meters}$.
  3. Checks geometry aspect ratio and polygon convexity.

### 3.2 Automated Cadastral Encroachment Detection
```sql
-- Identify all structures projecting outside their registered parcel boundary
SELECT
    b.id AS building_id,
    p.id AS parcel_id,
    p.parcel_number,
    ST_Area(ST_Transform(ST_Difference(e.geometry, p.boundary), 32737)) AS encroached_area_m2,
    ST_AsGeoJSON(ST_Difference(e.geometry, p.boundary)) AS encroached_polygon
FROM spatial_building b
JOIN spatial_entity e ON b.spatialentity_ptr_id = e.id
JOIN spatial_land_parcel p ON ST_Intersects(e.geometry, p.boundary)
WHERE NOT ST_CoveredBy(e.geometry, p.boundary)
  AND ST_Area(ST_Transform(ST_Difference(e.geometry, p.boundary), 32737)) > 0.50; -- Threshold 0.5m²
```

---

## 4. Construction Intelligence & Volumetric Earthworks

For construction monitoring, GLITTERS calculates physical progression and earth-moving volumes:

### 4.1 Volumetric Cut-and-Fill Calculation
Given pre-construction DTM ($Z_{\text{base}}$) and active drone survey DSM ($Z_{\text{current}}$):
$$\Delta V_{\text{cut}} = \iint_{\text{site}} \max(0, Z_{\text{base}} - Z_{\text{current}}) \, dx \, dy$$
$$\Delta V_{\text{fill}} = \iint_{\text{site}} \max(0, Z_{\text{current}} - Z_{\text{base}}) \, dx \, dy$$
$$\text{Net Volume Delta} = \Delta V_{\text{fill}} - \Delta V_{\text{cut}} \quad [\text{m}^3]$$
This metric is vital for validating contractor billing on major infrastructure, such as port dredging or highway embankments.

---

## 5. Urban & Demographic Intelligence

To guide municipal planning and real estate investment, GLITTERS calculates neighborhood accessibility and vulnerability:

### 5.1 Pedestrian Walkability & Isochrones
- Uses PostGIS `pgRouting` over topological road networks:
  - Generates 5, 10, and 15-minute walking service catchments ($4.5\text{ km/h}$ average walking speed).
  - Calculates access to essential amenities: healthcare facilities, primary schools, fresh produce markets, and transit stops.
- **Informal Settlement Vulnerability Index:**
  Combines building density ($> 180\text{ structures/hectare}$), low compactness ($Q < 0.40$), lack of formal road frontage ($> 50\text{ m}$ to nearest motorable road), and high flood risk to prioritize municipal infrastructure upgrades.

---

## 6. Analytical Architecture & Performance Specifications

| Operation | SLA / Target | Execution Engine | Caching Strategy |
|:---|:---|:---|:---|
| Single-Building Metrics | $< 25\text{ ms}$ | PostGIS indexed query | Redis key 24h TTL |
| Parcel Encroachment Check | $< 60\text{ ms}$ | PostGIS spatial difference | Invalidated on geometry update |
| Bounding Box Aggregates (10k items) | $< 180\text{ ms}$ | PostGIS spatial CTE + ST_ClusterKMeans | Redis viewport cache (1h) |
| Multi-Epoch Volumetric Cut & Fill | $< 8\text{ s}$ | Celery worker (NumPy/GDAL) | Persisted in `ConstructionSurveyReport` |
| Neighborhood 15-Min Isochrone | $< 250\text{ ms}$ | pgRouting Dijkstra traversal | Redis route cache (7 days) |
