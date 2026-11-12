"""
Terrain, Hydrology & Environmental Intelligence Engine (`terrain/hydrology.py` v1.1.0)
======================================================================================

Upgrades in v1.1.0:
1. Enforces metric CRS validation via `KenyaCRSEngine.assert_projected_crs(crs_epsg)`.
2. Establishes a consistent NoData propagation policy (`nodata_value = -9999.0`):
   NoData and non-finite elevation cells are masked prior to finite-difference gradients,
   Priority-Flood conditioning, D8 flow routing, and TWI calculation, and remain `np.nan`
   (or `0` in integer masks) in all output rasters with an explicit `valid_mask`.
"""

from __future__ import annotations
from dataclasses import dataclass
import heapq
import math
from typing import Dict, List, Optional, Tuple
import numpy as np
from scipy.ndimage import distance_transform_edt, uniform_filter

from ..crs.kenya_crs import KenyaCRSEngine


@dataclass(frozen=True)
class TerrainAnalysisResult:
    valid_mask: np.ndarray
    slope_deg: np.ndarray
    slope_pct: np.ndarray
    aspect_deg: np.ndarray
    tpi: np.ndarray
    tri_m: np.ndarray
    landform_class: np.ndarray
    conditioned_dem_m: np.ndarray
    d8_flow_dir: np.ndarray
    flow_accumulation_cells: np.ndarray
    specific_catchment_area_m: np.ndarray
    topographic_wetness_index: np.ndarray
    flood_susceptibility_score: np.ndarray
    construction_suitability_score: np.ndarray
    engineering_disclaimer: str


class TerrainAndHydrologyEngine:
    """
    NoData-aware hydrological and geomorphometric analyzer for SRTM 30m, Copernicus GLO-30 DEM,
    and LiDAR Digital Terrain Models in projected metric coordinates.
    """

    ENGINEERING_DISCLAIMER: str = (
        "PRELIMINARY ALGORITHMIC RISK SCREENING ONLY: Derived from Digital Elevation Model "
        "topography (TWI, slope, and D8 flow accumulation). Does NOT constitute an official "
        "Water Resources Authority (WRA/WARMA), NEMA Environmental Impact Assessment, or "
        "licensed civil/geotechnical engineering certification."
    )

    _D8_OFFSETS: List[Tuple[int, int, int]] = [
        (0, 1, 1),
        (1, 1, 2),
        (1, 0, 4),
        (1, -1, 8),
        (0, -1, 16),
        (-1, -1, 32),
        (-1, 0, 64),
        (-1, 1, 128),
    ]

    @staticmethod
    def compute_valid_dem_mask(
        dem_m: np.ndarray, nodata_value: Optional[float] = -9999.0
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        Return `(valid_mask, interpolated_working_dem)` where `valid_mask` is False at all
        `NaN`, `Inf`, and `nodata_value` cells, and `interpolated_working_dem` fills invalid
        cells with nearest valid neighbour elevation so 3x3 kernels do not suffer from
        -9999.0 cliff spikes before re-masking outputs with `np.nan`.
        """
        dem_f = dem_m.astype(np.float64)
        valid = np.isfinite(dem_f)
        if nodata_value is not None and math.isfinite(nodata_value):
            valid = valid & (~np.isclose(dem_f, nodata_value, atol=1e-3))

        if not np.any(valid):
            return valid, np.zeros_like(dem_f)
        if np.all(valid):
            return valid, dem_f

        # Fill invalid cells with nearest valid cell value for boundary-safe stencil evaluation
        indices = distance_transform_edt(~valid, return_distances=False, return_indices=True)
        filled_dem = dem_f[tuple(indices)]
        return valid, filled_dem

    @classmethod
    def horn_slope_and_aspect(
        cls,
        dem_m: np.ndarray,
        cell_size_x_m: float = 30.0,
        cell_size_y_m: float = 30.0,
        nodata_value: Optional[float] = -9999.0,
    ) -> Dict[str, np.ndarray]:
        valid, clean_dem = cls.compute_valid_dem_mask(dem_m, nodata_value=nodata_value)
        z = np.pad(clean_dem, pad_width=1, mode="edge")
        z_nw = z[0:-2, 0:-2]
        z_n  = z[0:-2, 1:-1]
        z_ne = z[0:-2, 2:]
        z_w  = z[1:-1, 0:-2]
        z_e  = z[1:-1, 2:]
        z_sw = z[2:,   0:-2]
        z_s  = z[2:,   1:-1]
        z_se = z[2:,   2:]

        dz_dx = ((z_ne + 2.0 * z_e + z_se) - (z_nw + 2.0 * z_w + z_sw)) / (8.0 * cell_size_x_m)
        dz_dy = ((z_nw + 2.0 * z_n + z_ne) - (z_sw + 2.0 * z_s + z_se)) / (8.0 * cell_size_y_m)

        rise_run = np.hypot(dz_dx, dz_dy)
        slope_rad = np.where(valid, np.arctan(rise_run), np.nan)
        slope_deg = np.where(valid, np.degrees(slope_rad), np.nan)
        slope_pct = np.where(valid, rise_run * 100.0, np.nan)

        aspect_rad = np.arctan2(dz_dx, dz_dy)
        aspect_deg = np.mod(np.degrees(aspect_rad) + 360.0, 360.0)
        aspect_deg = np.where(rise_run < 1e-7, -1.0, aspect_deg)
        aspect_deg = np.where(valid, aspect_deg, np.nan)

        return {
            "valid_mask": valid,
            "dz_dx": np.where(valid, dz_dx, np.nan).astype(np.float32),
            "dz_dy": np.where(valid, dz_dy, np.nan).astype(np.float32),
            "slope_rad": slope_rad.astype(np.float32),
            "slope_deg": slope_deg.astype(np.float32),
            "slope_pct": slope_pct.astype(np.float32),
            "aspect_deg": aspect_deg.astype(np.float32),
        }

    @classmethod
    def priority_flood_pit_fill(
        cls,
        dem_m: np.ndarray,
        epsilon: float = 1e-4,
        nodata_value: Optional[float] = -9999.0,
    ) -> np.ndarray:
        valid, clean_dem = cls.compute_valid_dem_mask(dem_m, nodata_value=nodata_value)
        h, w = clean_dem.shape
        filled = clean_dem.copy()
        visited = ~valid.copy()
        pq: List[Tuple[float, int, int]] = []

        for r in range(h):
            for c in range(w):
                if not valid[r, c]:
                    continue
                is_edge = (r == 0) or (r == h - 1) or (c == 0) or (c == w - 1)
                if not is_edge:
                    # Also seed cells adjacent to internal NoData voids
                    for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                        if not valid[r + dr, c + dc]:
                            is_edge = True
                            break
                if is_edge and not visited[r, c]:
                    visited[r, c] = True
                    heapq.heappush(pq, (float(filled[r, c]), r, c))

        neighbors = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
        while pq:
            elev, r, c = heapq.heappop(pq)
            for dr, dc in neighbors:
                nr, nc = r + dr, c + dc
                if 0 <= nr < h and 0 <= nc < w and not visited[nr, nc]:
                    visited[nr, nc] = True
                    if filled[nr, nc] <= elev:
                        filled[nr, nc] = elev + epsilon
                    heapq.heappush(pq, (float(filled[nr, nc]), nr, nc))

        filled[~valid] = np.nan
        return filled.astype(np.float32)

    @classmethod
    def d8_flow_direction_and_accumulation(
        cls,
        conditioned_dem_m: np.ndarray,
        cell_size_m: float = 30.0,
        valid_mask: Optional[np.ndarray] = None,
    ) -> Dict[str, np.ndarray]:
        h, w = conditioned_dem_m.shape
        valid = np.isfinite(conditioned_dem_m) if valid_mask is None else (valid_mask & np.isfinite(conditioned_dem_m))
        flow_dir = np.zeros((h, w), dtype=np.uint8)
        downslope_target = np.full((h, w, 2), -1, dtype=np.int32)

        for r in range(h):
            for c in range(w):
                if not valid[r, c]:
                    continue
                z0 = float(conditioned_dem_m[r, c])
                max_drop = 0.0
                best_code = 0
                best_nr, best_nc = -1, -1
                for dr, dc, code in cls._D8_OFFSETS:
                    nr, nc = r + dr, c + dc
                    if 0 <= nr < h and 0 <= nc < w and valid[nr, nc]:
                        dist = cell_size_m * (1.41421356237 if (dr != 0 and dc != 0) else 1.0)
                        drop = (z0 - float(conditioned_dem_m[nr, nc])) / dist
                        if drop > max_drop:
                            max_drop = drop
                            best_code = code
                            best_nr, best_nc = nr, nc
                flow_dir[r, c] = best_code
                downslope_target[r, c, 0] = best_nr
                downslope_target[r, c, 1] = best_nc

        safe_elev = np.where(valid, conditioned_dem_m, -1e12)
        flat_indices = np.argsort(-safe_elev.ravel())
        accum = np.where(valid, 1.0, np.nan)
        for flat_idx in flat_indices:
            r, c = divmod(int(flat_idx), w)
            if not valid[r, c]:
                continue
            nr, nc = int(downslope_target[r, c, 0]), int(downslope_target[r, c, 1])
            if nr >= 0 and nc >= 0 and valid[nr, nc]:
                accum[nr, nc] += accum[r, c]

        specific_catchment_area_m = accum * cell_size_m
        return {
            "d8_flow_dir": flow_dir,
            "downslope_target": downslope_target,
            "flow_accumulation_cells": accum.astype(np.float32),
            "specific_catchment_area_m": specific_catchment_area_m.astype(np.float32),
        }

    @classmethod
    def delineate_watershed(
        cls, downslope_target: np.ndarray, pour_point_rc: Tuple[int, int]
    ) -> np.ndarray:
        h, w, _ = downslope_target.shape
        upslope_children: Dict[Tuple[int, int], List[Tuple[int, int]]] = {}
        for r in range(h):
            for c in range(w):
                nr, nc = int(downslope_target[r, c, 0]), int(downslope_target[r, c, 1])
                if nr >= 0 and nc >= 0:
                    upslope_children.setdefault((nr, nc), []).append((r, c))

        watershed_mask = np.zeros((h, w), dtype=bool)
        queue = [pour_point_rc]
        while queue:
            curr = queue.pop()
            r, c = curr
            if 0 <= r < h and 0 <= c < w and not watershed_mask[r, c]:
                watershed_mask[r, c] = True
                queue.extend(upslope_children.get(curr, []))
        return watershed_mask

    @classmethod
    def analyze_terrain(
        cls,
        dem_m: np.ndarray,
        cell_size_m: float = 30.0,
        nodata_value: Optional[float] = -9999.0,
        crs_epsg: int = 32737,
    ) -> TerrainAnalysisResult:
        """
        Execute full geomorphometric and hydrological screening pipeline on a DEM array,
        validating metric CRS and preserving NoData masks throughout.
        """
        KenyaCRSEngine.assert_projected_crs(crs_epsg)
        valid, clean_dem = cls.compute_valid_dem_mask(dem_m, nodata_value=nodata_value)
        sa = cls.horn_slope_and_aspect(dem_m, cell_size_m, cell_size_m, nodata_value=nodata_value)
        slope_rad = sa["slope_rad"]
        slope_deg = sa["slope_deg"]
        slope_pct = sa["slope_pct"]
        aspect_deg = sa["aspect_deg"]

        mean_nb = uniform_filter(clean_dem, size=5, mode="nearest")
        tpi = np.where(valid, clean_dem - mean_nb, np.nan).astype(np.float32)
        tri = np.where(
            valid,
            np.sqrt(uniform_filter((clean_dem - mean_nb) ** 2, size=3, mode="nearest")),
            np.nan,
        ).astype(np.float32)

        # Landform classification: 0=NoData, 1=Valley/Depression, 2=Flat Plain, 3=Gentle Slope, 4=Steep Slope, 5=Ridge
        landform = np.zeros(dem_m.shape, dtype=np.uint8)
        landform[valid] = 2
        landform[valid & (tpi < -2.0)] = 1
        landform[valid & (tpi >= -2.0) & (tpi <= 2.0) & (slope_deg >= 3.0) & (slope_deg < 15.0)] = 3
        landform[valid & (slope_deg >= 15.0)] = 4
        landform[valid & (tpi > 2.0)] = 5

        conditioned = cls.priority_flood_pit_fill(dem_m, nodata_value=nodata_value)
        hydro = cls.d8_flow_direction_and_accumulation(conditioned, cell_size_m, valid_mask=valid)
        sca = hydro["specific_catchment_area_m"]

        tan_beta = np.maximum(np.tan(np.where(valid, slope_rad, 0.0)), 0.001)
        twi = np.where(valid, np.log(np.where(valid, sca, 1.0) / tan_beta), np.nan).astype(np.float32)

        if np.any(valid):
            twi_valid = twi[valid]
            twi_min, twi_max = float(np.min(twi_valid)), float(np.max(twi_valid))
            twi_norm = (twi - twi_min) / max(twi_max - twi_min, 1e-6)
        else:
            twi_norm = np.full_like(twi, np.nan)

        slope_factor = np.clip(1.0 - (slope_deg / 15.0), 0.0, 1.0)
        flood_score = np.where(
            valid, np.clip(0.65 * twi_norm + 0.35 * slope_factor, 0.0, 1.0), np.nan
        ).astype(np.float32)

        slope_suitability = np.exp(-((slope_deg / 12.0) ** 2))
        suitability = np.where(
            valid, np.clip(0.55 * slope_suitability + 0.45 * (1.0 - flood_score), 0.0, 1.0), np.nan
        ).astype(np.float32)

        return TerrainAnalysisResult(
            valid_mask=valid,
            slope_deg=slope_deg,
            slope_pct=slope_pct,
            aspect_deg=aspect_deg,
            tpi=tpi,
            tri_m=tri,
            landform_class=landform,
            conditioned_dem_m=conditioned,
            d8_flow_dir=hydro["d8_flow_dir"],
            flow_accumulation_cells=hydro["flow_accumulation_cells"],
            specific_catchment_area_m=sca,
            topographic_wetness_index=twi,
            flood_susceptibility_score=flood_score,
            construction_suitability_score=suitability,
            engineering_disclaimer=cls.ENGINEERING_DISCLAIMER,
        )
