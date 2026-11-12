"""
Re-export imagery processing interfaces under `imagery/processing.py` for clean
modular symmetry with the GLITTERS architecture specification.
"""

from ..raster.pipeline import (
    AffineGeotransform,
    GeoRasterTile,
    RasterProcessingPipeline,
)

__all__ = ["AffineGeotransform", "GeoRasterTile", "RasterProcessingPipeline"]
