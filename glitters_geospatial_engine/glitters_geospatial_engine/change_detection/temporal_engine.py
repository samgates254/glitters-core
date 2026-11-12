"""
Temporal Change Detection Engine (`change_detection/temporal_engine.py` v1.1.0)
===============================================================================

Upgrades in v1.1.0:
1. Exact raster-to-vector connected-component polygonization: builds each change geometry
   by uniting the exact 4-corner affine polygons (`transform.pixel_polygon_utm(col, row)`)
   of the detected component pixels rather than a bounding rectangle (`min_row..max_row`),
   guaranteeing `reported_area_m2 == geometry_utm.area` for irregular/L-shaped/concave changes!
2. Separates `applied_displacement_m` from `residual_registration_error_m` and
   `total_positional_uncertainty_1sigma_m`, using `total_positional_uncertainty_1sigma_m`
   to compute the 95% area confidence interval (`area_95ci_m2`).
3. Propagates NoData and cloud masks from both epochs (`t1` and `t2`).
"""

from __future__ import annotations
from dataclasses import dataclass
import math
from typing import Any, Dict, List, Optional
import numpy as np
from scipy.ndimage import label as ndimage_label
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from ..crs.kenya_crs import KenyaCRSEngine
from ..raster.pipeline import GeoRasterTile, RasterProcessingPipeline
from ..remote_sensing.indices import SpectralIndicesEngine


@dataclass(frozen=True)
class DetectedGeographicChange:
    """
    Auditable change-detection record where `area_m2` and `geometry_utm.area` are
    strictly reconciled via exact connected-component polygonization.
    """
    change_id: str
    change_type: str
    geometry_utm: BaseGeometry
    utm_epsg: int
    area_m2: float
    pixel_count_area_m2: float
    date_t1: str
    date_t2: str
    source_imagery_t1: str
    source_imagery_t2: str
    detection_method: str
    confidence_estimate: float
    uncertainty_bounds: Dict[str, float]
    applied_displacement_m: float
    residual_registration_error_m: float
    total_positional_uncertainty_1sigma_m: float
    registration_error_m: float  # Equal to residual_registration_error_m
    evidence_reference: str


class TemporalChangeDetectionEngine:
    """
    Multi-temporal geospatial change detection engine with affine reprojection,
    NoData/cloud exclusion, seasonal harmonic detrending, and exact pixel polygonization.
    """

    @staticmethod
    def kenya_bimodal_phenology_expectation(day_of_year: int) -> float:
        omega = 2.0 * math.pi / 365.25
        return (
            0.06 * math.cos(omega * (day_of_year - 140))
            + 0.08 * math.cos(2.0 * omega * (day_of_year - 140))
        )

    @classmethod
    def change_vector_analysis(
        cls,
        ndvi_t1: np.ndarray,
        ndbi_t1: np.ndarray,
        ndvi_t2: np.ndarray,
        ndbi_t2: np.ndarray,
        doy_t1: int = 45,
        doy_t2: int = 45,
        valid_mask: Optional[np.ndarray] = None,
    ) -> Dict[str, np.ndarray]:
        seasonal_shift = (
            cls.kenya_bimodal_phenology_expectation(doy_t2)
            - cls.kenya_bimodal_phenology_expectation(doy_t1)
        )
        d_ndvi = (ndvi_t2.astype(np.float64) - ndvi_t1.astype(np.float64)) - seasonal_shift
        d_ndbi = ndbi_t2.astype(np.float64) - ndbi_t1.astype(np.float64)

        if valid_mask is not None:
            d_ndvi = np.where(valid_mask, d_ndvi, np.nan)
            d_ndbi = np.where(valid_mask, d_ndbi, np.nan)

        magnitude = np.hypot(d_ndvi, d_ndbi)
        direction_rad = np.arctan2(d_ndbi, d_ndvi)
        return {
            "delta_ndvi_seasonally_adjusted": d_ndvi.astype(np.float32),
            "delta_ndbi": d_ndbi.astype(np.float32),
            "cva_magnitude": magnitude.astype(np.float32),
            "cva_direction_rad": direction_rad.astype(np.float32),
        }

    @classmethod
    def detect_changes_between_tiles(
        cls,
        tile_t1: GeoRasterTile,
        tile_t2: GeoRasterTile,
        doy_t1: int = 45,
        doy_t2: int = 45,
        cva_threshold: float = 0.28,
        min_change_area_m2: float = 100.0,
    ) -> List[DetectedGeographicChange]:
        """
        Co-register `tile_t2` onto `tile_t1`'s affine grid, propagate NoData and cloud masks,
        execute seasonally detrended CVA, and vectorize exact pixel-union polygons.
        """
        KenyaCRSEngine.assert_projected_crs(tile_t1.epsg)
        KenyaCRSEngine.assert_projected_crs(tile_t2.epsg)

        alignment = RasterProcessingPipeline.align_temporal_stack(tile_t1, tile_t2)
        aligned_t2: GeoRasterTile = alignment["aligned_tile"]
        applied_disp_m = float(alignment["applied_displacement_m"])
        residual_reg_err_m = float(alignment["residual_registration_error_m"])
        total_pos_sigma_m = float(alignment["total_positional_uncertainty_1sigma_m"])

        red_1, nir_1, swir_1 = tile_t1.data[0], tile_t1.data[1], tile_t1.data[2]
        red_2, nir_2, swir_2 = aligned_t2.data[0], aligned_t2.data[1], aligned_t2.data[2]

        ndvi_1 = SpectralIndicesEngine.ndvi(
            nir_1, red_1, nodata_value=tile_t1.nodata_value, external_valid_mask=tile_t1.valid_data_mask
        )
        ndbi_1 = SpectralIndicesEngine.ndbi(
            swir_1, nir_1, nodata_value=tile_t1.nodata_value, external_valid_mask=tile_t1.valid_data_mask
        )
        ndvi_2 = SpectralIndicesEngine.ndvi(
            nir_2, red_2, nodata_value=aligned_t2.nodata_value, external_valid_mask=aligned_t2.valid_data_mask
        )
        ndbi_2 = SpectralIndicesEngine.ndbi(
            swir_2, nir_2, nodata_value=aligned_t2.nodata_value, external_valid_mask=aligned_t2.valid_data_mask
        )

        valid_mask = (
            ndvi_1.valid_mask
            & ndbi_1.valid_mask
            & ndvi_2.valid_mask
            & ndbi_2.valid_mask
            & tile_t1.valid_data_mask
            & aligned_t2.valid_data_mask
        )

        cva = cls.change_vector_analysis(
            ndvi_1.values, ndbi_1.values, ndvi_2.values, ndbi_2.values, doy_t1, doy_t2, valid_mask
        )
        mag = cva["cva_magnitude"]
        d_ndvi = cva["delta_ndvi_seasonally_adjusted"]
        d_ndbi = cva["delta_ndbi"]

        change_mask = valid_mask & np.isfinite(mag) & (mag >= cva_threshold)
        labeled, n_comp = ndimage_label(change_mask)
        px_area_m2 = tile_t1.transform.true_pixel_area_m2

        changes: List[DetectedGeographicChange] = []
        for cid in range(1, n_comp + 1):
            ys, xs = np.nonzero(labeled == cid)
            pixel_count_area_m2 = float(len(xs) * px_area_m2)
            if pixel_count_area_m2 < min_change_area_m2:
                continue

            mean_mag = float(np.mean(mag[ys, xs]))
            mean_dndvi = float(np.mean(d_ndvi[ys, xs]))
            mean_dndbi = float(np.mean(d_ndbi[ys, xs]))

            if mean_dndbi > 0.12 and mean_dndvi < -0.10:
                ctype = "NEW_CONSTRUCTION_OR_CLEARING"
            elif mean_dndvi < -0.20:
                ctype = "VEGETATION_LOSS"
            elif mean_dndbi < -0.15:
                ctype = "DEMOLITION_OR_SURFACE_REMOVAL"
            else:
                ctype = "LAND_COVER_CONVERSION"

            # Exact connected-component raster-to-vector polygonization using all 4 affine pixel corners
            pixel_polys = [
                tile_t1.transform.pixel_polygon_utm(int(c_idx), int(r_idx))
                for r_idx, c_idx in zip(ys, xs)
            ]
            poly_utm = unary_union(pixel_polys)
            exact_geom_area_m2 = float(poly_utm.area)

            radiometric_sigma = float(
                np.mean(ndvi_1.uncertainty_1sigma[ys, xs]) + np.mean(ndvi_2.uncertainty_1sigma[ys, xs])
            )
            snr = (mean_mag - cva_threshold) / max(radiometric_sigma, 0.02)
            conf = float(np.clip(1.0 / (1.0 + math.exp(-1.5 * snr)), 0.50, 0.99))

            changes.append(
                DetectedGeographicChange(
                    change_id=f"CHG_{cid:05d}",
                    change_type=ctype,
                    geometry_utm=poly_utm,
                    utm_epsg=tile_t1.epsg,
                    area_m2=exact_geom_area_m2,
                    pixel_count_area_m2=pixel_count_area_m2,
                    date_t1=tile_t1.acquisition_date,
                    date_t2=tile_t2.acquisition_date,
                    source_imagery_t1=f"{tile_t1.sensor_name}:{tile_t1.acquisition_date}",
                    source_imagery_t2=f"{tile_t2.sensor_name}:{tile_t2.acquisition_date}",
                    detection_method="Seasonally_Detrended_Change_Vector_Analysis_CVA",
                    confidence_estimate=conf,
                    uncertainty_bounds={
                        "cva_magnitude_mean": mean_mag,
                        "radiometric_1sigma": radiometric_sigma,
                        "applied_displacement_m": applied_disp_m,
                        "residual_registration_1sigma_m": residual_reg_err_m,
                        "total_positional_1sigma_m": total_pos_sigma_m,
                        "area_95ci_m2": 1.96 * total_pos_sigma_m * float(poly_utm.length),
                    },
                    applied_displacement_m=applied_disp_m,
                    residual_registration_error_m=residual_reg_err_m,
                    total_positional_uncertainty_1sigma_m=total_pos_sigma_m,
                    registration_error_m=residual_reg_err_m,
                    evidence_reference=(
                        f"CVA(|dNDVI_adj|={mean_dndvi:+.3f}, |dNDBI|={mean_dndbi:+.3f}, "
                        f"mag={mean_mag:.3f}, res_reg_err={residual_reg_err_m:.3f}m, "
                        f"pos_sigma={total_pos_sigma_m:.3f}m)"
                    ),
                )
            )
        return changes
