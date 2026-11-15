# GLITTERS / GOD'S EYE — M4 ARCHITECTURE SPECIFICATION
## Deliverable 3: Imagery Intelligence & Earth Observation Architecture

**Document Reference:** `docs/engineering/M4_IMAGERY_INTELLIGENCE_ARCHITECTURE.md`
**Classification:** Core Architectural Standard
**Milestone:** M4 — Advanced Spatial Intelligence & Digital Twin Ecosystem
**Target Systems:** GLITTERS Core (`apps.spatial`), TiTiler / Rio-Tiler, Object Storage (S3/MinIO), GOD'S EYE Workstation

---

## 1. Executive Summary & Vision

Imagery is the primary empirical source of ground truth in the physical built environment. Vector geometries (building footprints, parcel boundaries) are abstractions derived from or verified against optical, multispectral, and elevation raster assets.

GLITTERS M4 establishes an enterprise-grade Imagery Intelligence pipeline connecting:
1. **Satellite Constellations:** Sentinel-2 (multispectral 10m), PlanetScope (high-cadence 3m), and Maxar WorldView-3/Legion (very-high-resolution 0.3m optical).
2. **UAV / Drone Photogrammetry:** High-resolution orthomosaics ($< 3\text{ cm/px}$), Digital Surface Models (DSM), and Digital Terrain Models (DTM) from RTK survey missions.
3. **Cloud-Native Geospatial Architecture:** Zero-copy, streamable Cloud Optimized GeoTIFFs (COG), SpatioTemporal Asset Catalog (STAC v1.0.0) standard metadata, and dynamic on-the-fly analytical tile generation via TiTiler / rio-tiler.

```
Imagery Ingestion & Serving Architecture:
┌───────────────────────┐   Raw TIFFs    ┌───────────────────────────┐
│ Drone Flights / UAV   ├───────────────►│ COG Converter Worker      │
└───────────────────────┘                │ - Internal Tiling (256px) │
┌───────────────────────┐   Level-1C/2A  │ - Overviews (2x, 4x, 8x)  │
│ Satellite (Sentinel)  ├───────────────►│ - ZSTD / DEFLATE / LERC   │
└───────────────────────┘                └─────────────┬─────────────┘
                                                       │
                                                       ▼
┌───────────────────────┐   STAC Items   ┌───────────────────────────┐
│ PostGIS STAC Catalog  │◄───────────────┤ S3 / MinIO Object Storage │
│ (STAC v1.0.0 Registry)│                │ s3://glitters-imagery-cog/│
└───────────┬───────────┘                └─────────────┬─────────────┘
            │                                          │ Byte-Range HTTP GET
            │ Metadata Query                           │
            ▼                                          ▼
┌────────────────────────────────────────────────────────────────────┐
│ Dynamic Raster Engine (TiTiler / Rio-Tiler on FastAPI / Python)    │
│ - On-the-fly RGB True Color (B4,B3,B2)                             │
│ - Multispectral Indices: NDVI, NDWI, NBR                           │
│ - Elevation Relief / Hillshade Rendering                           │
└─────────────────────────────────┬──────────────────────────────────┘
                                  │ XYZ Tile Protocol (Web Mercator)
                                  ▼
┌────────────────────────────────────────────────────────────────────┐
│ GOD'S EYE Workstation (MapLibre GL / Deck.gl Raster Layer)         │
│ https://core.glitters.io/api/v1/spatial/imagery/{collection}/...   │
└────────────────────────────────────────────────────────────────────┘
```

---

## 2. SpatioTemporal Asset Catalog (STAC v1.0.0) Integration

GLITTERS standardizes all raster indexing on the Open Geospatial Consortium (OGC) approved STAC v1.0.0 specification. This enables interoperability with the global Earth Observation ecosystem.

### 2.1 STAC Object Model Hierarchy
1. **Catalog:** Top-level structural container (e.g., `urn:glitters:catalog:earth-observation`).
2. **Collection:** Logically grouped imagery sharing spatial/temporal bounds, provider, and sensor specs:
   - `ke-msa-drone-surveys` (Centimeter-grade drone orthomosaics).
   - `ke-msa-satellite-optical` (Sub-meter commercial Maxar satellite passes).
   - `ke-sentinel2-l2a` (Public 10m ESA Sentinel-2 multispectral baseline).
3. **Item:** Discrete spatio-temporal scene (e.g. flight mission on 2026-03-15 or satellite granule), containing:
   - Geometry (Footprint polygon in WGS 84).
   - Bounding box (`[minX, minY, maxX, maxY]`).
   - Temporal extent (`datetime` or `start_datetime` / `end_datetime`).
   - Multi-asset dictionary (COG URL, preview thumbnail, sensor metadata).

### 2.2 Data Model Definition (`apps.spatial.models.ImageryObservation`)
```python
# apps/spatial/models.py extension for STAC Cataloging

class ImageryCollection(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    collection_id = models.CharField(max_length=128, unique=True, db_index=True)
    title = models.CharField(max_length=255)
    description = models.TextField()
    provider_name = models.CharField(max_length=128)
    sensor_type = models.CharField(
        max_length=32,
        choices=[
            ('UAV_RGB', 'UAV Drone High-Res RGB'),
            ('UAV_LIDAR', 'UAV Airborne LiDAR DSM/DTM'),
            ('SATELLITE_OPTICAL_HR', 'High-Res Optical Satellite (<1m)'),
            ('SATELLITE_MULTISPECTRAL', 'Multispectral Satellite (10-30m)'),
        ]
    )
    ground_sample_distance_m = models.FloatField(help_text="Spatial resolution (GSD) in meters")
    spatial_extent = models.PolygonField(srid=4326)
    temporal_extent_start = models.DateTimeField()
    temporal_extent_end = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

class ImageryItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    collection = models.ForeignKey(ImageryCollection, on_delete=models.CASCADE, related_name='items')
    item_id = models.CharField(max_length=255, unique=True, db_index=True)
    footprint = models.PolygonField(srid=4326)
    centroid = models.PointField(srid=4326)
    acquisition_datetime = models.DateTimeField(db_index=True)
    cloud_cover_percentage = models.FloatField(default=0.0)
    sun_elevation_angle = models.FloatField(null=True, blank=True)

    # Asset links stored as structured STAC assets JSON
    assets = models.JSONField(
        help_text="STAC asset mapping with COG href, mime_type, roles, and band indices"
    )

    # Radiometric and flight parameters
    flight_metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['acquisition_datetime']),
            models.Index(fields=['collection', 'acquisition_datetime']),
        ]
```

---

## 3. Cloud-Optimized GeoTIFF (COG) Pipeline & Profile

Raw GeoTIFFs emitted by photogrammetry software (DJI Terra, Pix4D, Metashape) are monolithic, striped files requiring full file downloads to view. GLITTERS mandates automated conversion into streaming Cloud Optimized GeoTIFFs.

### 3.1 Strict COG Encoding Standards
- **Tile Matrix:** Internal tiling at $256 \times 256$ or $512 \times 512$ pixels.
- **Pyramids (Overviews):** Power-of-two overviews ($2, 4, 8, 16, 32, 64, 128$) downsampled using `AVERAGE` resampling for optical imagery, `MODE` for categorical classifications, and `BILINEAR` for continuous DSM elevation.
- **Compression:**
  - *Optical RGB:* `DEFLATE` with `PREDICTOR=2` or `ZSTD` with level 9 for maximum lossless compression.
  - *Elevation / DSM:* `LERC` (Limited Error Raster Compression) or `DEFLATE` with `PREDICTOR=3` (Floating point predictor) to preserve millimeter elevation accuracy.
- **Byte Order:** BigTIFF enabled for rasters exceeding $4\text{ GB}$.
- **Storage Protocol:** S3-compatible object store (AWS S3, MinIO, or Cloudflare R2) with HTTP Range Requests (`206 Partial Content`) enabled.

### 3.2 Conversion CLI Command (`convert_to_cog.py`)
```bash
gdal_translate \
    -of COG \
    -co TILED=YES \
    -co BLOCKSIZE=512 \
    -co COMPRESS=ZSTD \
    -co LEVEL=9 \
    -co PREDICTOR=2 \
    -co OVERVIEWS=AUTO \
    -co RESAMPLING=AVERAGE \
    -co NUM_THREADS=ALL_CPUS \
    input_orthomosaic.tif \
    output_orthomosaic_cog.tif
```

---

## 4. Dynamic Raster Serving Engine

Rather than pre-rendering hundreds of gigabytes of static map tiles for every zoom level, GLITTERS uses a dynamic streaming server powered by `rio-tiler` and `FastAPI` (embedded as a high-performance sidecar service in GLITTERS).

### 4.1 Byte-Range Streaming Mechanism
When GOD'S EYE requests tile `Z=18, X=152431, Y=112001`:
1. The dynamic tile server translates the XYZ tile coordinate into the bounding box in EPSG:3857.
2. The server calculates the matching overview level inside the COG.
3. The server executes 1 to 4 HTTP range requests (typically $32\text{ KB} - 128\text{ KB}$ each) directly against object storage to fetch only the needed tiles.
4. The server decodes, applies color correction or index calculation, encodes to WebP/PNG, and responds in $< 35\text{ ms}$.

### 4.2 Analytical Raster Pipelines
GOD'S EYE analysts can switch spectral visualization dynamically without downloading new datasets:
- **True Color RGB (TCI):** Standard natural color visualization (Red, Green, Blue bands).
- **Color Infrared (CIR / False Color):** Near-Infrared (NIR), Red, Green bands. Highlights healthy vegetation, crop stress, and turf vs. painted rooftops.
- **Normalized Difference Vegetation Index (NDVI):**
  $$\text{NDVI} = \frac{\text{NIR} - \text{Red}}{\text{NIR} + \text{Red}}$$
  Used to monitor illegal encroachment into gazetted mangrove forests and public parks in Mombasa.
- **Normalized Difference Water Index (NDWI):**
  $$\text{NDWI} = \frac{\text{Green} - \text{NIR}}{\text{Green} + \text{NIR}}$$
  Used to detect tidal flooding, urban pooling, and coastal erosion along the Likoni and Tudor Creek shorelines.
- **Elevation Hillshade & Slope:** Dynamic hillshade generated from DSM rasters with adjustable sun azimuth and altitude angles, exposing building massing and micro-topography.

---

## 5. Multi-Temporal Resolution-Aware Layering

GOD'S EYE provides seamless continuous zooming from global satellite overview to sub-centimeter construction inspection.

```
Zoom Level Hierarchy:
┌────────────────────────────────────────────────────────────────────────┐
│ Zoom 0 - 12: Regional Context (ESA Sentinel-2, 10m GSD)                │
│ Coverage: Entire Kenya / Coastal Region                                │
├────────────────────────────────────────────────────────────────────────┤
│ Zoom 13 - 17: Urban Texture (PlanetScope 3m / Maxar WorldView 0.5m)    │
│ Coverage: Greater Mombasa Metropolitan Area                           │
├────────────────────────────────────────────────────────────────────────┤
│ Zoom 18 - 22: Survey Inspection (Drone Photogrammetry, 2.5cm GSD)      │
│ Coverage: Project Parcels, Construction Sites, Port Terminals          │
└────────────────────────────────────────────────────────────────────────┘
```

### 5.1 Resolution-Aware Query Algorithm
When a client requests an imagery layer for viewport bounding box $B$ at zoom level $Z$:
1. Calculate target ground resolution $R = \frac{156543.03392 \times \cos(\text{lat})}{2^Z} \text{ meters/pixel}$.
2. Query `ImageryItem` table where `footprint && B` and `acquisition_datetime <= target_time`.
3. Filter items where `collection.ground_sample_distance_m <= R \times 2.0`.
4. If multiple matching items exist, order by `acquisition_datetime DESC` (or sort by least cloud cover) and composite the top result.

---

## 6. Integration with GOD'S EYE Workstation

GOD'S EYE consumes imagery tiles through standard XYZ Tile protocols in MapLibre GL and Deck.gl.

### 6.1 Tile URL Template
```
GET /api/v1/spatial/imagery/{collection_id}/tiles/{z}/{x}/{y}.webp?time=2026-03-01T00:00:00Z&pipeline=rgb
```

### 6.2 Workstation Features
- **Temporal Split-Screen Slider:** Slide between a 2020 satellite view and a 2026 drone orthophoto to visually verify building additions.
- **Pixel Inspector Tool:** Click any pixel to inspect spectral values, surface elevation (meters above sea level), and capture metadata.
- **Opacity & Blend Modes:** Overlay vector parcel boundaries over drone imagery with adjustable transparency and edge-enhancement filters.
