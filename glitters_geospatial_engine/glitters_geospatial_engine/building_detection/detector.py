"""
Building Footprint Vectorization & Land-Cover Detection Engine (`building_detection/detector.py` v1.1.0)
========================================================================================================

Upgrades in v1.1.0:
1. Enforces metric CRS validation via `KenyaCRSEngine.assert_projected_crs(raster_tile.epsg)`.
2. Respects `raster_tile.valid_data_mask` so NoData and cloud-obscured pixels are never
   vectorized into false building footprints or misclassified in land-cover composition.
3. Computes true pixel area via the 2D affine Jacobian determinant `transform.true_pixel_area_m2`
   (`|a*e - b*d|`) and constructs pixel polygons from all four affine-transformed corners
   (`transform.pixel_polygon_utm(col, row)`), preserving rotated/sheared grid geometry.
4. Reports both raw pixel union area (`pixel_count_area_m2`) and regularized polygon area
   (`area_m2`), plus boundary-uncertainty-penalized calibrated confidence.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence
import math
import numpy as np
from scipy.ndimage import label as ndimage_label
from shapely.affinity import rotate as shapely_rotate
from shapely.geometry import Polygon
from shapely.ops import unary_union
from shapely.strtree import STRtree

from ..crs.kenya_crs import KenyaCRSEngine
from ..raster.pipeline import GeoRasterTile
from ..remote_sensing.indices import SpectralIndicesEngine


class InsufficientSpatialResolutionError(ValueError):
    """
    Raised when an algorithm attempts individual building footprint polygon extraction
    from imagery whose Ground Sample Distance (GSD) exceeds 2.0 metres.
    """


@dataclass(frozen=True)
class DetectedBuildingFootprint:
    building_id: str
    geometry_utm: Polygon
    utm_epsg: int
    area_m2: float
    pixel_count_area_m2: float
    perimeter_m: float
    rectangularity: float
    raw_mean_probability: float
    confidence: float
    source_sensor: str
    source_gsd_m: float
    observation_date: str


class BuildingAndLandDetectionEngine:
    """
    Resolution-gated building footprint vectorization/regularization subsystem and
    NoData-aware multi-spectral land-cover classifier.
    """

    MAX_GSD_FOR_INDIVIDUAL_BUILDINGS_M: float = 2.0
    MIN_BUILDING_AREA_M2: float = 9.0

    @classmethod
    def check_resolution_sufficiency(cls, gsd_m: float, task: str) -> Dict[str, Any]:
        if task == "INDIVIDUAL_BUILDING_FOOTPRINT" and gsd_m > cls.MAX_GSD_FOR_INDIVIDUAL_BUILDINGS_M:
            raise InsufficientSpatialResolutionError(
                f"Imagery GSD is {gsd_m:.2f} m, which exceeds the {cls.MAX_GSD_FOR_INDIVIDUAL_BUILDINGS_M:.1f} m "
                "physical resolution limit for reliable individual building footprint extraction. "
                "Use `classify_land_cover_spectral_rules` for macro built-up density instead."
            )
        tier = (
            "VHR_PRECISE_FOOTPRINT"
            if gsd_m <= 0.5
            else ("HR_DETECTION_WITH_BOUNDARY_UNCERTAINTY" if gsd_m <= 2.0 else "MACRO_BUILT_UP_ONLY")
        )
        return {"gsd_m": gsd_m, "task": task, "capability_tier": tier}

    @classmethod
    def extract_building_footprints_from_probability_map(
        cls,
        prob_map: np.ndarray,
        raster_tile: GeoRasterTile,
        prob_threshold: float = 0.55,
        regularize_orthogonal: bool = True,
    ) -> List[DetectedBuildingFootprint]:
        """
        Vectorize and regularize building footprint polygons from a segmentation probability map
        on high-resolution imagery (GSD <= 2.0 m), excluding all NoData and cloud-masked pixels
        and constructing exact 4-corner affine polygons.
        """
        KenyaCRSEngine.assert_projected_crs(raster_tile.epsg)
        gsd_m = raster_tile.transform.mean_gsd_m
        cls.check_resolution_sufficiency(gsd_m, "INDIVIDUAL_BUILDING_FOOTPRINT")

        valid_mask = raster_tile.valid_data_mask & np.isfinite(prob_map)
        binary_mask = ((prob_map >= prob_threshold) & valid_mask).astype(np.int32)
        labeled, num_features = ndimage_label(binary_mask)
        pixel_area_m2 = raster_tile.transform.true_pixel_area_m2

        footprints: List[DetectedBuildingFootprint] = []
        for comp_id in range(1, num_features + 1):
            ys, xs = np.nonzero(labeled == comp_id)
            comp_area_m2 = float(len(xs) * pixel_area_m2)
            if comp_area_m2 < cls.MIN_BUILDING_AREA_M2:
                continue

            raw_prob = float(np.mean(prob_map[ys, xs]))
            # Construct exact 4-corner affine-transformed pixel polygons (exact under rotation/shear)
            pixel_polys = [
                raster_tile.transform.pixel_polygon_utm(int(c_idx), int(r_idx))
                for r_idx, c_idx in zip(ys, xs)
            ]
            raw_union = unary_union(pixel_polys)
            if not isinstance(raw_union, Polygon):
                continue

            poly_utm = cls.regularize_building_polygon(raw_union) if regularize_orthogonal else raw_union
            mrr = poly_utm.minimum_rotated_rectangle
            rect_ratio = float(poly_utm.area / max(mrr.area, 1e-9))

            # Calibrate confidence by penalizing boundary-to-interior perimeter ratio and GSD
            boundary_uncertainty_factor = math.exp(-0.15 * (gsd_m / 0.5))
            calibrated_conf = float(np.clip(raw_prob * boundary_uncertainty_factor, 0.05, 0.99))

            footprints.append(
                DetectedBuildingFootprint(
                    building_id=f"BLDG_{comp_id:05d}",
                    geometry_utm=poly_utm,
                    utm_epsg=raster_tile.epsg,
                    area_m2=float(poly_utm.area),
                    pixel_count_area_m2=comp_area_m2,
                    perimeter_m=float(poly_utm.length),
                    rectangularity=min(1.0, rect_ratio),
                    raw_mean_probability=raw_prob,
                    confidence=calibrated_conf,
                    source_sensor=raster_tile.sensor_name,
                    source_gsd_m=gsd_m,
                    observation_date=raster_tile.acquisition_date,
                )
            )
        return footprints

    @staticmethod
    def regularize_building_polygon(poly: Polygon, simplify_tol_m: float = 0.35) -> Polygon:
        mrr = poly.minimum_rotated_rectangle
        if not isinstance(mrr, Polygon):
            return poly
        coords = list(mrr.exterior.coords)
        if len(coords) < 4:
            return poly
        dx = coords[1][0] - coords[0][0]
        dy = coords[1][1] - coords[0][1]
        angle_deg = math.degrees(math.atan2(dy, dx))

        rotated = shapely_rotate(poly, -angle_deg, origin="centroid")
        simplified = rotated.simplify(simplify_tol_m, preserve_topology=True)
        restored = shapely_rotate(simplified, angle_deg, origin=poly.centroid)
        return restored if isinstance(restored, Polygon) else poly

    @staticmethod
    def calculate_parcel_building_density(
        parcel_poly_utm: Polygon,
        building_footprints_utm: Sequence[Polygon],
        crs_epsg: Optional[int] = None,
    ) -> Dict[str, Any]:
        if crs_epsg is not None:
            KenyaCRSEngine.assert_projected_crs(crs_epsg)
        parcel_area = max(float(parcel_poly_utm.area), 1e-9)
        if not building_footprints_utm:
            return {
                "building_count": 0,
                "total_building_footprint_m2": 0.0,
                "building_coverage_ratio": 0.0,
                "open_space_ratio": 1.0,
                "largest_building_m2": 0.0,
            }

        tree = STRtree(building_footprints_utm)
        cand_idxs = tree.query(parcel_poly_utm, predicate="intersects")
        total_bldg_area = 0.0
        largest_bldg = 0.0
        count = 0

        for idx in cand_idxs:
            inter = parcel_poly_utm.intersection(building_footprints_utm[int(idx)])
            a = float(inter.area)
            if a >= 1.0:
                count += 1
                total_bldg_area += a
                largest_bldg = max(largest_bldg, a)

        bcr = min(1.0, total_bldg_area / parcel_area)
        return {
            "building_count": count,
            "total_building_footprint_m2": total_bldg_area,
            "building_coverage_ratio": bcr,
            "open_space_ratio": max(0.0, 1.0 - bcr),
            "largest_building_m2": largest_bldg,
        }

    @staticmethod
    def classify_land_cover_spectral_rules(
        blue: np.ndarray,
        green: np.ndarray,
        red: np.ndarray,
        nir: np.ndarray,
        swir1: np.ndarray,
        nodata_value: Optional[float] = -9999.0,
        external_valid_mask: Optional[np.ndarray] = None,
    ) -> Dict[str, Any]:
        """
        NoData-aware hierarchical Land-Cover classifier:
        Class 0 is strictly reserved for NoData / cloud-masked / invalid pixels, and
        valid land-cover fractions are normalized over `valid_pixel_count` (never over NoData).
        """
        ndvi_res = SpectralIndicesEngine.ndvi(nir, red, nodata_value=nodata_value, external_valid_mask=external_valid_mask)
        mndwi_res = SpectralIndicesEngine.mndwi(green, swir1, nodata_value=nodata_value, external_valid_mask=external_valid_mask)
        ndbi_res = SpectralIndicesEngine.ndbi(swir1, nir, nodata_value=nodata_value, external_valid_mask=external_valid_mask)
        bsi_res = SpectralIndicesEngine.bsi(swir1, red, nir, blue, nodata_value=nodata_value, external_valid_mask=external_valid_mask)

        valid = ndvi_res.valid_mask & mndwi_res.valid_mask & ndbi_res.valid_mask & bsi_res.valid_mask
        ndvi = ndvi_res.values
        mndwi = mndwi_res.values
        ndbi = ndbi_res.values
        bsi = bsi_res.values

        classes = np.zeros(ndvi.shape, dtype=np.uint8)
        water_mask = valid & (mndwi > 0.15)
        dense_veg_mask = valid & (~water_mask) & (ndvi >= 0.45)
        sparse_veg_mask = valid & (~water_mask) & (ndvi >= 0.20) & (ndvi < 0.45)
        bare_soil_mask = valid & (~water_mask) & (ndvi < 0.20) & (bsi > 0.12)
        built_up_mask = valid & (~water_mask) & (ndvi < 0.20) & (~bare_soil_mask) & (ndbi > -0.02)
        residual_bare = valid & (~water_mask) & (~dense_veg_mask) & (~sparse_veg_mask) & (~built_up_mask) & (~bare_soil_mask)

        classes[water_mask] = 1
        classes[dense_veg_mask] = 2
        classes[sparse_veg_mask] = 3
        classes[built_up_mask] = 4
        classes[bare_soil_mask | residual_bare] = 5

        valid_px = int(np.sum(valid))
        denom = max(1, valid_px)
        composition = {
            "valid_pixel_count": valid_px,
            "nodata_or_cloud_fraction": float(1.0 - (valid_px / max(1, classes.size))),
            "water_fraction": float(np.sum(classes == 1) / denom) if valid_px > 0 else float("nan"),
            "dense_vegetation_fraction": float(np.sum(classes == 2) / denom) if valid_px > 0 else float("nan"),
            "sparse_vegetation_fraction": float(np.sum(classes == 3) / denom) if valid_px > 0 else float("nan"),
            "built_up_fraction": float(np.sum(classes == 4) / denom) if valid_px > 0 else float("nan"),
            "bare_soil_fraction": float(np.sum(classes == 5) / denom) if valid_px > 0 else float("nan"),
        }
        return {"class_map": classes, "valid_mask": valid, "composition": composition}
