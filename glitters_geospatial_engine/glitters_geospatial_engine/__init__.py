"""
GLITTERS Geospatial Intelligence Engine (`glitters_geospatial_engine` v1.1.0)
=============================================================================

Production-hardened release resolving all v1.0.0 technical audit findings:
1. Full Arc 1960 & WGS84 Kenya UTM CRS support including EPSG:21096 (Arc 1960 / UTM Zone 36N).
2. Strict linear-unit metre validation (`unit_conversion_factor == 1.0`) enforced across all
   geometry, parcel, raster, building, terrain, and routing subsystems (rejects US survey feet
   such as EPSG:2263 as well as geographic degrees such as EPSG:4326).
3. End-to-end NoData and cloud mask propagation across raster ingestion, resampling, spectral
   indices, land-cover classification, building extraction, change detection, and DEM hydrology.
4. True affine-aware geospatial reprojection/resampling (`resample_tile_to_reference_grid`)
   accounting for origin offsets, resolution differences, rotation, and shear before registration.
5. Strict separation of applied registration shift vs. residual registration error and positional
   uncertainty in multi-temporal change detection.
6. Honest spatial grid naming (`GlittersHexGridIndex` & `GlittersHilbertGridIndex`) with explicit
   grid standard metadata.
7. Cryptographic/structured `CadastralVerificationEvidence` enforcement for any legal cadastre claim.
8. Exact 4-corner affine raster-to-vector connected-component polygonization (zero bounding-box
   area inflation) and network-corridor isochrone buffering.
9. Sparse CSR Local Moran's I and index-driven STRtree/cKDTree k-NN search.
10. Zero silent default imputation in `PropertySpatialFeatureVector` with explicit observation
    completeness masks, plus PostgreSQL 16 declarative partitioning composite PK & multi-tenant RLS.
"""

__version__ = "1.1.0"
__author__ = "GLITTERS Geospatial Algorithmic Engineering & Remote Sensing Research"
