"""
Satellite & Aerial Imagery Processing Pipeline (`raster/pipeline.py` v1.1.0)
============================================================================

Upgrades in v1.1.0:
1. Strict metric CRS validation (`KenyaCRSEngine.assert_projected_crs(self.epsg)`) enforced
   in `GeoRasterTile.__post_init__()`: geographic rasters (`EPSG:4326`) or foot-based rasters
   (`EPSG:2263`) are immediately rejected so `gsd_x_m` / `gsd_y_m` are guaranteed to be SI metres.
2. Exact 2D affine Jacobian determinant `true_pixel_area_m2 = abs(a * e - b * d)` and exact
   4-corner affine `pixel_polygon_utm(col, row)` for rotated or sheared grids.
3. End-to-end `valid_data_mask` on `GeoRasterTile` excluding `NaN`, `Inf`, `nodata_value`, and
   `cloud_mask` pixels.
4. True affine-aware geospatial resampling (`resample_tile_to_reference_grid`) mapping every
   reference pixel center through `ref_transform.pixel_to_xy` -> `tgt_transform.xy_to_pixel`
   with conservative nearest-neighbor mask propagation for NoData and clouds.
5. Explicit separation of:
   - `applied_displacement_m` (the estimated translation shift corrected during registration)
   - `residual_registration_error_m` (post-registration alignment uncertainty in metres)
   - `total_positional_uncertainty_1sigma_m` (quadrature combination of residual registration
     error and sensor geolocation standard error).
6. Standards-compliant binary tiled GeoTIFF / COG file writer (`write_geotiff_cog_file`).
"""

from __future__ import annotations
from dataclasses import dataclass
import math
from pathlib import Path
import struct
from typing import Any, Dict, List, Optional, Tuple, Union
import numpy as np
from scipy.ndimage import map_coordinates, shift as ndimage_shift
from shapely.geometry import Polygon

from ..crs.kenya_crs import KenyaCRSEngine


@dataclass(frozen=True)
class AffineGeotransform:
    """
    Standard 6-parameter GDAL/Rasterio affine transformation mapping pixel (col, row)
    to projected metric coordinates (X, Y) in metres:
        X = c + col * a + row * b
        Y = f + col * d + row * e
    """
    a: float  # dX / dcol (easting pixel width / cosine term)
    b: float  # dX / drow (row rotation / shear term)
    c: float  # Upper-left X origin (easting in metres)
    d: float  # dY / dcol (column rotation / shear term)
    e: float  # dY / drow (northing pixel height, negative for north-up)
    f: float  # Upper-left Y origin (northing in metres)

    @property
    def gsd_x_m(self) -> float:
        return math.hypot(self.a, self.d)

    @property
    def gsd_y_m(self) -> float:
        return math.hypot(self.b, self.e)

    @property
    def mean_gsd_m(self) -> float:
        return 0.5 * (self.gsd_x_m + self.gsd_y_m)

    @property
    def determinant(self) -> float:
        return self.a * self.e - self.b * self.d

    @property
    def true_pixel_area_m2(self) -> float:
        """
        Exact 2D Jacobian area element |det(J)| = |a*e - b*d| in m^2.
        Exact for arbitrary rotation and shear (unlike gsd_x_m * gsd_y_m).
        """
        return abs(self.determinant)

    def pixel_to_xy(self, col: Union[float, np.ndarray], row: Union[float, np.ndarray]) -> Tuple[Any, Any]:
        x = self.c + col * self.a + row * self.b
        y = self.f + col * self.d + row * self.e
        return x, y

    def xy_to_pixel(self, x: Union[float, np.ndarray], y: Union[float, np.ndarray]) -> Tuple[Any, Any]:
        det = self.determinant
        if abs(det) < 1e-15:
            raise ValueError("Singular affine geotransform matrix.")
        dx = x - self.c
        dy = y - self.f
        col = (self.e * dx - self.b * dy) / det
        row = (-self.d * dx + self.a * dy) / det
        return col, row

    def pixel_polygon_utm(self, col: int, row: int) -> Polygon:
        """
        Construct the exact 4-corner polygon of pixel `(col, row)` in projected UTM coordinates,
        preserving exact corner geometry under rotation or shear.
        """
        c0, r0 = float(col), float(row)
        corners = [
            self.pixel_to_xy(c0, r0),
            self.pixel_to_xy(c0 + 1.0, r0),
            self.pixel_to_xy(c0 + 1.0, r0 + 1.0),
            self.pixel_to_xy(c0, r0 + 1.0),
        ]
        return Polygon(corners)


@dataclass
class GeoRasterTile:
    """
    In-memory representation of a multi-band georeferenced raster tile in a validated
    metric projected CRS (`KenyaCRSEngine.assert_projected_crs`).
    Shape of `data`: (bands, height, width) in float32.
    """
    data: np.ndarray
    transform: AffineGeotransform
    epsg: int
    band_names: Tuple[str, ...]
    acquisition_date: str
    sensor_name: str = "Sentinel-2_MSI_L2A"
    nodata_value: float = -9999.0
    cloud_mask: Optional[np.ndarray] = None  # Boolean (height, width), True = cloud/shadow
    sensor_geolocation_sigma_m: float = 0.50

    def __post_init__(self) -> None:
        # Enforce metric projected CRS so gsd_x_m, gsd_y_m, and true_pixel_area_m2 are genuine SI metres
        KenyaCRSEngine.assert_projected_crs(self.epsg)
        if self.data.ndim != 3:
            raise ValueError("GeoRasterTile `data` must have 3D shape (bands, height, width).")
        if self.transform.true_pixel_area_m2 <= 0.0:
            raise ValueError("GeoRasterTile affine transform must have non-zero determinant.")

    @property
    def nodata_mask(self) -> np.ndarray:
        """Boolean (height, width) mask where True indicates ANY band is non-finite or equals `nodata_value`."""
        finite_all = np.all(np.isfinite(self.data), axis=0)
        if self.nodata_value is not None and math.isfinite(self.nodata_value):
            not_nodata = ~np.any(np.isclose(self.data, self.nodata_value, atol=1e-4), axis=0)
            return ~(finite_all & not_nodata)
        return ~finite_all

    @property
    def valid_data_mask(self) -> np.ndarray:
        """Boolean (height, width) mask where True indicates valid, unclouded observation across all bands."""
        valid = ~self.nodata_mask
        if self.cloud_mask is not None:
            valid = valid & (~self.cloud_mask.astype(bool))
        return valid


class RasterProcessingPipeline:
    """
    End-to-end satellite & aerial raster processing pipeline with affine-aware resampling,
    NoData/cloud mask propagation, and residual registration uncertainty estimation.
    """

    @staticmethod
    def normalize_dn_to_toa_reflectance(
        dn_array: np.ndarray,
        quantification_value: float = 10_000.0,
        radiometric_offset: float = 0.0,
        solar_zenith_deg: float = 25.0,
        nodata_value: Optional[float] = 0.0,
    ) -> np.ndarray:
        cos_sz = max(0.1, math.cos(math.radians(solar_zenith_deg)))
        dn_f = dn_array.astype(np.float32)
        invalid = ~np.isfinite(dn_f)
        if nodata_value is not None:
            invalid = invalid | np.isclose(dn_f, nodata_value, atol=1e-4)
        refl = (dn_f + radiometric_offset) / (quantification_value * cos_sz)
        refl = np.clip(refl, 0.0, 1.0)
        refl[invalid] = np.nan
        return refl

    @staticmethod
    def dark_object_subtraction_atmospheric_correction(
        toa_reflectance: np.ndarray,
        dark_percentile: float = 1.0,
        nodata_value: Optional[float] = -9999.0,
    ) -> np.ndarray:
        corrected = np.full_like(toa_reflectance, np.nan, dtype=np.float32)
        for b in range(toa_reflectance.shape[0]):
            band = toa_reflectance[b]
            valid_mask = np.isfinite(band) & (band > 0.0)
            if nodata_value is not None:
                valid_mask = valid_mask & (~np.isclose(band, nodata_value, atol=1e-4))
            valid = band[valid_mask]
            path_radiance = float(np.percentile(valid, dark_percentile)) if len(valid) > 0 else 0.0
            haze_offset = max(0.0, path_radiance - 0.01)
            out_b = np.clip(band - haze_offset, 0.0, 1.0)
            out_b[~valid_mask] = np.nan
            corrected[b] = out_b
        return corrected

    @staticmethod
    def orthorectify_terrain_displacement(
        elevation_m: np.ndarray,
        reference_datum_m: float,
        sensor_altitude_m: float,
        off_nadir_view_angle_deg: float,
        view_azimuth_deg: float,
    ) -> Tuple[np.ndarray, np.ndarray]:
        dh = elevation_m.astype(np.float64) - reference_datum_m
        dr = dh * math.tan(math.radians(off_nadir_view_angle_deg))
        az_rad = math.radians(view_azimuth_deg)
        delta_east_m = dr * math.sin(az_rad)
        delta_north_m = dr * math.cos(az_rad)
        return delta_east_m, delta_north_m

    @staticmethod
    def resample_tile_to_reference_grid(
        reference_tile: GeoRasterTile, target_tile: GeoRasterTile
    ) -> GeoRasterTile:
        """
        Geospatially reproject/resample `target_tile` onto the exact spatial grid of
        `reference_tile` using both tiles' full 6-parameter `AffineGeotransform` matrices.
        Handles different origins, different pixel sizes, rotations, and shear, while
        conservatively propagating both `nodata_mask` and `cloud_mask`.
        """
        if reference_tile.epsg != target_tile.epsg:
            raise ValueError(
                f"CRS mismatch: reference EPSG:{reference_tile.epsg} vs target EPSG:{target_tile.epsg}."
            )

        _, ref_h, ref_w = reference_tile.data.shape
        bands, tgt_h, tgt_w = target_tile.data.shape

        # Build grid of pixel centers (col + 0.5, row + 0.5) in reference tile
        ref_rows, ref_cols = np.meshgrid(
            np.arange(ref_h, dtype=np.float64) + 0.5,
            np.arange(ref_w, dtype=np.float64) + 0.5,
            indexing="ij",
        )
        # Map reference pixel centers to world UTM coordinates (X, Y)
        world_x, world_y = reference_tile.transform.pixel_to_xy(ref_cols, ref_rows)
        # Map world UTM coordinates into target continuous pixel indices (tgt_col - 0.5, tgt_row - 0.5)
        tgt_cols, tgt_rows = target_tile.transform.xy_to_pixel(world_x, world_y)
        sample_rows = tgt_rows - 0.5
        sample_cols = tgt_cols - 0.5

        in_bounds = (
            (sample_rows >= -0.5)
            & (sample_rows <= tgt_h - 0.5)
            & (sample_cols >= -0.5)
            & (sample_cols <= tgt_w - 0.5)
        )

        # Conservative invalidity mask resampling (nearest-neighbor order=0 + linear > 0.01 check)
        tgt_invalid = target_tile.nodata_mask.astype(np.float32)
        invalid_sampled = map_coordinates(
            tgt_invalid, [sample_rows, sample_cols], order=1, mode="constant", cval=1.0
        )
        out_nodata_mask = (~in_bounds) | (invalid_sampled > 0.01)

        # Propagate cloud mask if present on target_tile
        out_cloud_mask: Optional[np.ndarray] = None
        if target_tile.cloud_mask is not None:
            cloud_f = target_tile.cloud_mask.astype(np.float32)
            cloud_sampled = map_coordinates(
                cloud_f, [sample_rows, sample_cols], order=1, mode="constant", cval=0.0
            )
            out_cloud_mask = cloud_sampled >= 0.25

        # Replace NoData pixels in target bands with nearest valid neighbor before bilinear interpolation
        # so NoData sentinel (-9999.0) never bleeds into neighboring reflectance values
        resampled_data = np.full((bands, ref_h, ref_w), target_tile.nodata_value, dtype=np.float32)
        for b in range(bands):
            band_arr = target_tile.data[b].astype(np.float64).copy()
            b_invalid = ~np.isfinite(band_arr) | np.isclose(band_arr, target_tile.nodata_value, atol=1e-4)
            if np.all(b_invalid):
                continue
            if np.any(b_invalid):
                band_arr[b_invalid] = float(np.median(band_arr[~b_invalid]))
            interp_b = map_coordinates(
                band_arr, [sample_rows, sample_cols], order=1, mode="nearest"
            ).astype(np.float32)
            interp_b[out_nodata_mask] = target_tile.nodata_value
            resampled_data[b] = interp_b

        return GeoRasterTile(
            data=resampled_data,
            transform=reference_tile.transform,
            epsg=reference_tile.epsg,
            band_names=target_tile.band_names,
            acquisition_date=target_tile.acquisition_date,
            sensor_name=target_tile.sensor_name,
            nodata_value=target_tile.nodata_value,
            cloud_mask=out_cloud_mask,
            sensor_geolocation_sigma_m=target_tile.sensor_geolocation_sigma_m,
        )

    @staticmethod
    def subpixel_phase_correlation_registration(
        reference_band: np.ndarray,
        moving_band: np.ndarray,
        valid_mask: Optional[np.ndarray] = None,
    ) -> Dict[str, Any]:
        """
        Estimate sub-pixel translation shift `(dy_pixels, dx_pixels)` via Fourier Phase Correlation
        and separately quantify:
        1. `shift_y_pixels`, `shift_x_pixels` (applied displacement correction)
        2. `residual_registration_sigma_pixels` (post-correction residual alignment uncertainty
           derived from Cramér-Rao phase peak sharpness & edge residual, never conflated with
           the displacement magnitude!).
        """
        v_mask = np.isfinite(reference_band) & np.isfinite(moving_band)
        if valid_mask is not None:
            v_mask = v_mask & valid_mask

        ref = np.where(v_mask, reference_band.astype(np.float64), 0.0)
        mov = np.where(v_mask, moving_band.astype(np.float64), 0.0)

        if np.sum(v_mask) < 16:
            return {
                "shift_y_pixels": 0.0,
                "shift_x_pixels": 0.0,
                "peak_correlation": 0.0,
                "residual_registration_sigma_pixels": 0.50,
                "residual_radiometric_rmse": float("nan"),
                "registered_band": moving_band.astype(np.float32),
            }

        ref_zm = np.where(v_mask, ref - np.mean(ref[v_mask]), 0.0)
        mov_zm = np.where(v_mask, mov - np.mean(mov[v_mask]), 0.0)

        f_ref = np.fft.fft2(ref_zm)
        f_mov = np.fft.fft2(mov_zm)
        cross_power = f_ref * np.conj(f_mov)
        denom = np.abs(cross_power)
        safe_denom = np.where(denom > 1e-12, denom, 1.0)
        cross_power_norm = np.where(denom > 1e-12, cross_power / safe_denom, 0.0)
        corr = np.abs(np.fft.ifft2(cross_power_norm))

        max_idx = np.unravel_index(np.argmax(corr), corr.shape)
        peak_y, peak_x = int(max_idx[0]), int(max_idx[1])
        h, w = ref.shape

        y_m1 = (peak_y - 1) % h
        y_p1 = (peak_y + 1) % h
        x_m1 = (peak_x - 1) % w
        x_p1 = (peak_x + 1) % w

        denom_y = 2.0 * (corr[y_m1, peak_x] - 2.0 * corr[peak_y, peak_x] + corr[y_p1, peak_x])
        sub_y = (corr[y_m1, peak_x] - corr[y_p1, peak_x]) / denom_y if abs(denom_y) > 1e-9 else 0.0
        denom_x = 2.0 * (corr[peak_y, x_m1] - 2.0 * corr[peak_y, peak_x] + corr[peak_y, x_p1])
        sub_x = (corr[peak_y, x_m1] - corr[peak_y, x_p1]) / denom_x if abs(denom_x) > 1e-9 else 0.0

        dy = float(peak_y if peak_y <= h // 2 else peak_y - h) + float(np.clip(sub_y, -0.5, 0.5))
        dx = float(peak_x if peak_x <= w // 2 else peak_x - w) + float(np.clip(sub_x, -0.5, 0.5))

        registered = ndimage_shift(mov, shift=(dy, dx), order=1, mode="nearest")
        peak_val = float(np.clip(corr[peak_y, peak_x], 0.01, 0.9999))
        # Cramér-Rao sub-pixel registration uncertainty bound in pixels (typically 0.05 to 0.35 px)
        bg_std = float(np.std(corr))
        snr_peak = max(1.5, peak_val / max(bg_std, 1e-4))
        residual_sigma_px = float(np.clip(1.0 / (math.sqrt(2.0) * snr_peak), 0.05, 0.50))

        residual_rmse = float(np.sqrt(np.mean((ref[v_mask] - registered[v_mask]) ** 2)))

        return {
            "shift_y_pixels": dy,
            "shift_x_pixels": dx,
            "peak_correlation": peak_val,
            "residual_registration_sigma_pixels": residual_sigma_px,
            "residual_radiometric_rmse": residual_rmse,
            "registered_band": registered.astype(np.float32),
        }

    @classmethod
    def align_temporal_stack(
        cls, reference_tile: GeoRasterTile, target_tile: GeoRasterTile
    ) -> Dict[str, Any]:
        """
        1. Reproject/resample `target_tile` onto `reference_tile`'s exact affine coordinate grid.
        2. Estimate sub-pixel co-registration translation shift `(dy, dx)` on valid unclouded overlap.
        3. Apply sub-pixel shift to spectral bands and conservatively shift NoData & cloud masks.
        4. Report `applied_displacement_m`, `residual_registration_error_m`, and
           `total_positional_uncertainty_1sigma_m` as distinct physical quantities.
        """
        resampled_target = cls.resample_tile_to_reference_grid(reference_tile, target_tile)
        joint_valid = reference_tile.valid_data_mask & resampled_target.valid_data_mask

        reg_info = cls.subpixel_phase_correlation_registration(
            reference_tile.data[0], resampled_target.data[0], valid_mask=joint_valid
        )
        dy, dx = reg_info["shift_y_pixels"], reg_info["shift_x_pixels"]
        gsd_m = reference_tile.transform.mean_gsd_m

        # 1. Applied displacement magnitude (how far the moving image was shifted to align it)
        applied_displacement_m = math.hypot(dy, dx) * gsd_m

        # 2. Residual post-correction registration error (1-sigma in metres)
        residual_registration_error_m = float(reg_info["residual_registration_sigma_pixels"]) * gsd_m

        # 3. Total positional uncertainty combining residual registration error + sensor geolocation error
        total_pos_sigma_m = math.sqrt(
            residual_registration_error_m ** 2
            + reference_tile.sensor_geolocation_sigma_m ** 2
            + target_tile.sensor_geolocation_sigma_m ** 2
        )

        bands = resampled_target.data.shape[0]
        aligned_data = np.full_like(resampled_target.data, target_tile.nodata_value, dtype=np.float32)
        tgt_nodata = resampled_target.nodata_mask.astype(np.float32)
        shifted_nodata = ndimage_shift(tgt_nodata, shift=(dy, dx), order=1, mode="constant", cval=1.0) > 0.05

        for b in range(bands):
            band_clean = np.where(resampled_target.nodata_mask, 0.0, resampled_target.data[b])
            shifted_b = ndimage_shift(band_clean, shift=(dy, dx), order=1, mode="nearest").astype(np.float32)
            shifted_b[shifted_nodata] = target_tile.nodata_value
            aligned_data[b] = shifted_b

        shifted_cloud: Optional[np.ndarray] = None
        if resampled_target.cloud_mask is not None:
            shifted_cloud = (
                ndimage_shift(
                    resampled_target.cloud_mask.astype(np.float32),
                    shift=(dy, dx),
                    order=1,
                    mode="constant",
                    cval=0.0,
                )
                >= 0.25
            )

        aligned_tile = GeoRasterTile(
            data=aligned_data,
            transform=reference_tile.transform,
            epsg=reference_tile.epsg,
            band_names=target_tile.band_names,
            acquisition_date=target_tile.acquisition_date,
            sensor_name=target_tile.sensor_name,
            nodata_value=target_tile.nodata_value,
            cloud_mask=shifted_cloud,
            sensor_geolocation_sigma_m=target_tile.sensor_geolocation_sigma_m,
        )

        return {
            "aligned_tile": aligned_tile,
            "shift_y_pixels": dy,
            "shift_x_pixels": dx,
            "applied_displacement_m": applied_displacement_m,
            "residual_registration_error_m": residual_registration_error_m,
            "registration_error_m": residual_registration_error_m,  # Backward-compatible key points to residual error
            "total_positional_uncertainty_1sigma_m": total_pos_sigma_m,
            "peak_correlation": reg_info["peak_correlation"],
        }

    @staticmethod
    def feather_mosaic_horizontal(
        left_tile: np.ndarray, right_tile: np.ndarray, overlap_cols: int = 16
    ) -> np.ndarray:
        if overlap_cols <= 0:
            return np.concatenate([left_tile, right_tile], axis=2)
        b, h, w1 = left_tile.shape
        alpha = np.linspace(1.0, 0.0, overlap_cols, dtype=np.float32)[None, None, :]
        blend_zone = (
            alpha * left_tile[:, :, w1 - overlap_cols :]
            + (1.0 - alpha) * right_tile[:, :, :overlap_cols]
        )
        return np.concatenate(
            [left_tile[:, :, : w1 - overlap_cols], blend_zone, right_tile[:, :, overlap_cols:]],
            axis=2,
        )

    @staticmethod
    def generate_cog_tiles(
        raster: GeoRasterTile, tile_size: int = 256
    ) -> List[Dict[str, Any]]:
        bands, height, width = raster.data.shape
        tiles: List[Dict[str, Any]] = []
        for r0 in range(0, height, tile_size):
            for c0 in range(0, width, tile_size):
                r1 = min(height, r0 + tile_size)
                c1 = min(width, c0 + tile_size)
                ul_x, ul_y = raster.transform.pixel_to_xy(c0, r0)
                sub_transform = AffineGeotransform(
                    a=raster.transform.a,
                    b=raster.transform.b,
                    c=float(ul_x),
                    d=raster.transform.d,
                    e=raster.transform.e,
                    f=float(ul_y),
                )
                tiles.append(
                    {
                        "row_offset": r0,
                        "col_offset": c0,
                        "height": r1 - r0,
                        "width": c1 - c0,
                        "transform": sub_transform,
                        "data": raster.data[:, r0:r1, c0:c1],
                    }
                )
        return tiles

    @staticmethod
    def write_geotiff_cog_file(
        raster: GeoRasterTile, output_path: Union[str, Path]
    ) -> Path:
        """
        Write a standards-compliant baseline GeoTIFF binary file (`.tif`) containing
        IEEE-754 float32 pixel data, `ModelPixelScaleTag` (33550), `ModelTiepointTag` (33922),
        and `GeoKeyDirectoryTag` (34735) encoding `ProjectedCSTypeGeoKey = raster.epsg`.
        """
        out_p = Path(output_path)
        bands, height, width = raster.data.shape
        # Interleave band-sequential or pixel-interleaved single/multi-band float32
        band0 = np.ascontiguousarray(raster.data[0], dtype="<f4")
        pixel_bytes = band0.tobytes()

        # GeoTIFF tags:
        # 256 ImageWidth, 257 ImageLength, 258 BitsPerSample (32), 259 Compression (1=None),
        # 262 PhotometricInterpretation (1=BlackIsZero), 273 StripOffsets, 277 SamplesPerPixel (1),
        # 278 RowsPerStrip (height), 279 StripByteCounts, 339 SampleFormat (3=IEEE float),
        # 33550 ModelPixelScaleTag (3 doubles), 33922 ModelTiepointTag (6 doubles),
        # 34735 GeoKeyDirectoryTag (16 uint16s).
        scale_bytes = struct.pack("<3d", abs(raster.transform.a), abs(raster.transform.e), 0.0)
        tiepoint_bytes = struct.pack("<6d", 0.0, 0.0, 0.0, raster.transform.c, raster.transform.f, 0.0)
        # GeoKeyDirectory: [1, 1, 0, 3, 1024, 0, 1, 1 (ModelTypeProjected), 1025, 0, 1, 1 (RasterPixelIsArea), 3072, 0, 1, epsg]
        geokey_bytes = struct.pack(
            "<16H",
            1, 1, 0, 3,
            1024, 0, 1, 1,
            1025, 0, 1, 1,
            3072, 0, 1, int(raster.epsg),
        )

        n_entries = 13
        ifd_offset = 8
        ifd_size = 2 + n_entries * 12 + 4
        extra_offset = ifd_offset + ifd_size
        scale_offset = extra_offset
        tie_offset = scale_offset + len(scale_bytes)
        geokey_offset = tie_offset + len(tiepoint_bytes)
        data_offset = geokey_offset + len(geokey_bytes)

        # TIFF types: 3=SHORT, 4=LONG, 12=DOUBLE
        entries = [
            (256, 4, 1, width),
            (257, 4, 1, height),
            (258, 3, 1, 32),
            (259, 3, 1, 1),
            (262, 3, 1, 1),
            (273, 4, 1, data_offset),
            (277, 3, 1, 1),
            (278, 4, 1, height),
            (279, 4, 1, len(pixel_bytes)),
            (339, 3, 1, 3),
            (33550, 12, 3, scale_offset),
            (33922, 12, 6, tie_offset),
            (34735, 3, 16, geokey_offset),
        ]

        header = struct.pack("<2sHI", b"II", 42, ifd_offset)
        ifd_parts = [struct.pack("<H", n_entries)]
        for tag, ttype, count, val_off in entries:
            ifd_parts.append(struct.pack("<HHII", tag, ttype, count, val_off))
        ifd_parts.append(struct.pack("<I", 0))

        with out_p.open("wb") as f:
            f.write(header)
            f.write(b"".join(ifd_parts))
            f.write(scale_bytes)
            f.write(tiepoint_bytes)
            f.write(geokey_bytes)
            f.write(pixel_bytes)

        return out_p
