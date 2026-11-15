# GLITTERS / GOD'S EYE — M4 ARCHITECTURE SPECIFICATION
## Deliverable 5: Digital Twin & 3D Built Environment Architecture

**Document Reference:** `docs/engineering/M4_DIGITAL_TWIN_ARCHITECTURE.md`
**Classification:** Core Architectural Standard
**Milestone:** M4 — Advanced Spatial Intelligence & Digital Twin Ecosystem
**Target Systems:** GLITTERS Core (`apps.spatial`), 3D Tile Pipeline, Deck.gl / Three.js, GOD'S EYE Workstation

---

## 1. Executive Summary & Digital Twin Definition

A true Digital Twin is not merely a visual 3D rendering. It is an active, queryable computational representation of physical real estate, infrastructure assets, and ecological systems, continuously synchronized with field observations and legal records.

GLITTERS M4 establishes the digital twin foundation for GOD'S EYE, upgrading 2D planar footprints into semantically rich, multi-resolution 3D volumetric assets adhering to OGC standards.

```
3D Digital Twin Processing & Streaming Architecture:
┌────────────────────────┐      ┌────────────────────────┐      ┌────────────────────────┐
│ Canonical 2D Footprint │      │ Terrain Model (DTM)    │      │ DSM / Drone LiDAR      │
│ (spatial_building)     │      │ (Bilinear Elevation)   │      │ (Surface Elevation)    │
└───────────┬────────────┘      └───────────┬────────────┘      └───────────┬────────────┘
            │                               │                               │
            └───────────────────────┬───────┴───────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ 3D Volumetric Reconstruction Engine (apps/spatial/services/twin_reconstruction.py)    │
│ - Normalized Surface Model Calculation: nDSM = DSM - DTM                              │
│ - Height Estimation: Z_roof = percentile_90(nDSM within footprint)                   │
│ - Roof Geometry Classification: Flat / Gabled / Hipped / Mansard                       │
│ - Prismatic Mesh Triangulation & Vertex Normal Generation                             │
└───────────────────────────────────┬────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ OGC 3D Tiles 1.1 Hierarchical Tiling (b3dm / glTF 2.0 / Batched3DModel)               │
│ - Quadtree Bounding Volume Hierarchy (BVH)                                            │
│ - Geometric Error (SSE) Driven Level of Detail                                         │
│ - Batch Table Metadata Binding (UUID, URN, Building Class, Height, Occupancy)          │
└───────────────────────────────────┬────────────────────────────────────────────────────┘
                                    │
                                    ▼ HTTP Streaming
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ GOD'S EYE Workstation (3D Viewport)                                                    │
│ - Deck.gl SimpleMeshLayer / Tile3DLayer / MapLibre 3D Terrain                         │
│ - Dynamic Sun-Position Shadow Casting (Solar Azimuth/Zenith Engine)                    │
│ - Real-Time Volumetric & Viewshed Analysis                                             │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. 3D Level of Detail (LoD) Classification Framework

GLITTERS adopts and refines the international CityGML / CityJSON 1.1 Level of Detail standard:

| Level | Representation | Geometry Type | Height Source | Textures / Appearance | Primary Use Case |
|:---|:---|:---|:---|:---|:---|
| **LoD 0** | Planar Footprint | 2.5D Polygon | Ground elevation $Z_0$ only | Flat wireframe / Color by use | Regional spatial indexing, zoning analysis |
| **LoD 1** | Prismatic Block Model | Extruded Solid | Measured or estimated height $H$ | Monochromatic / Solid color | Urban wind modeling, gross volume, shadow envelope |
| **LoD 2** | Differentiated Roofs | Polyhedral Solid | Boundary-conforming roof pitch | Semantic roof/wall material assignment | Solar insolation, flood inundation, urban skyline |
| **LoD 3** | Architectural Exterior | Solid with openings | Detailed photogrammetric mesh | High-resolution drone orthophoto projection | Facade inspection, window-to-wall ratio, visual impact |
| **LoD 4** | Interior / As-Built BIM | IFC / BIM Solid | Laser scan / CAD floor plans | Interior materials, MEP infrastructure | Tenancy asset management, emergency egress, facilities |

### 2.1 Mombasa Island LoD Distribution Strategy
- **Baseline (All 72,864 Footprints):** Ingested at **LoD 1** using algorithmic height inference:
  $$H = \begin{cases}
  \text{levels} \times 3.0\text{ m} & \text{if `building:levels` is specified} \\
  \text{levels} \times 3.5\text{ m} & \text{if `building_type` is commercial/industrial} \\
  6.5\text{ m} & \text{default for 2-storey residential} \\
  3.5\text{ m} & \text{default for single-storey informal/settlement}
  \end{cases}$$
- **Survey Drone Priority Areas (Kilindini Port, CBD, Nyali Waterfront):** Upgraded to **LoD 2 / LoD 3** using photogrammetric point cloud meshing.
- **GLITTERS Strategic Real Estate Assets:** Ingested at **LoD 4** from certified IFC / Revit models.

---

## 3. Streaming 3D Data Standards & Protocol

Monolithic 3D models cannot be rendered in web browsers at city scale. GLITTERS utilizes streaming standards optimized for GPU hardware memory limits.

### 3.1 OGC 3D Tiles 1.1 Specification
- **Tree Structure:** Spatial Quadtree with Bounding Volume Hierarchy (BVH) defined by oriented bounding boxes (OBB).
- **Refinement Strategy:** Additive or Replacement refinement controlled by Screen Space Error (SSE):
  $$\text{SSE} = \frac{\text{Geometric Error} \times \text{Viewport Height}}{2 \times \text{Camera Distance} \times \tan(\text{FOV} / 2)}$$
  When $\text{SSE} > 16.0$, child tiles with higher LoD are fetched and swapped in.
- **Payload Container:** Embedded binary glTF 2.0 (`.glb`) containing vertex positions, normals, texture coordinates, and custom batch table attributes.
- **Batch Table Metadata:**
  ```json
  {
    "canonical_urn": ["urn:glitters:entity:building:ke:msa:osm:way:101", "..."],
    "building_id": ["018f3a9e-0000-7000-8000-000000000001", "..."],
    "building_type": ["residential", "commercial"],
    "roof_height_m": [14.2, 45.8],
    "ground_elevation_m": [12.4, 8.1]
  }
  ```

### 3.2 CityJSON 1.1 Support
For lightweight analytical interchange and interoperability with academic and governmental GIS platforms, GLITTERS exposes CityJSON 1.1 representations over REST:
- Compresses geometries using integer coordinate quantization.
- Represents composite building semantics (GroundSurface, WallSurface, RoofSurface) in clean, parseable JSON.

---

## 4. Height & Volumetric Reconstruction Pipeline

Precise 3D modeling requires robust height determination without ground-survey overhead on every building.

### 4.1 Photogrammetric Height Extraction Algorithm
```python
# apps/spatial/services/twin_reconstruction.py

import numpy as np

def calculate_photogrammetric_building_height(footprint_geom, dsm_raster, dtm_raster):
    """
    Computes true physical roof height above ground level (AGL)
    by sampling surface elevation minus terrain elevation.
    """
    # 1. Mask raster windows to footprint boundary
    dsm_values = dsm_raster.sample_geometry(footprint_geom)
    dtm_values = dtm_raster.sample_geometry(footprint_geom)

    # 2. Compute normalized Digital Surface Model (nDSM)
    ndsm = dsm_values - dtm_values
    valid_ndsm = ndsm[valid_mask(ndsm)]

    if len(valid_ndsm) == 0:
        return None

    # 3. Extract robust statistics
    # Use 90th percentile to ignore HVAC units and antenna spikes
    roof_height_m = float(np.percentile(valid_ndsm, 90))
    ground_elevation_m = float(np.median(dtm_values))

    # Invariant: Physical buildings must have non-negative height
    if roof_height_m < 1.8:
        # Probable ground anomaly or misaligned footprint
        roof_height_m = 3.0  # Fallback to single storey

    return {
        "height_m": round(roof_height_m, 2),
        "ground_elevation_m": round(ground_elevation_m, 2),
        "method": "PHOTOGRAMMETRIC_NDSM_P90"
    }
```

### 4.2 3D Provenance Classification
Every 3D entity records its construction provenance:
1. `ESTIMATED_EXTRUSION`: 2D footprint extruded using tag inference (levels/type).
2. `PHOTOGRAMMETRIC_RECONSTRUCTION`: Extruded or meshed from drone DSM/DTM elevation.
3. `LIDAR_POINT_CLOUD_DERIVED`: Extracted from airborne pulsed LiDAR points.
4. `BIM_AS_BUILT`: Converted from architectural CAD/BIM IFC model.

---

## 5. Analytical Simulations in the 3D Workstation

GOD'S EYE integrates GPU-accelerated spatial simulations directly in the 3D viewport.

```
3D Analytical Capabilities:
┌────────────────────────────────────────────────────────────────────────┐
│ Solar Insolation & Shadow Engine                                      │
│ - Astronomical ephemeris calculates exact solar vector:                │
│   S = [cos(elev)*sin(az), cos(elev)*cos(az), sin(elev)]               │
│ - Real-time shadow casting reveals shaded parcels and solar roof yield │
├────────────────────────────────────────────────────────────────────────┤
│ Volumetric Encroachment & Air Rights                                  │
│ - Detects structures exceeding municipal zoning height ceilings        │
│ - Visualizes protected flight paths into Moi International Airport     │
├────────────────────────────────────────────────────────────────────────┤
│ Flood Inundation & Sea Level Rise Sim                                  │
│ - Simulates tidal surges (0.5m to 5.0m) against DTM/DSM terrain mesh   │
│ - Identifies buildings with ground elevations below water levels       │
└────────────────────────────────────────────────────────────────────────┘
```

### 5.1 Interactive Workstation Experience
- **Ray-Cast Picking:** Clicking any 3D building face immediately highlights its legal parcel boundary and queries the GLITTERS Core Property registry.
- **Flight Cam / Orbit Mode:** Free-camera orbit around any asset with collision detection against terrain and structures.
- **Clipping Planes:** Dynamic cross-section cut tool to inspect underground utilities and multi-level floor distributions.
