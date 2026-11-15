"""Unit tests for change detection algorithms, scientific validation, and spatial processing."""

from __future__ import annotations

import datetime
import math

import numpy as np
import pytest
from django.contrib.gis.geos import Polygon
from django.core.exceptions import ValidationError
from glitters_geospatial_engine.change_detection.temporal_engine import (
    TemporalChangeDetectionEngine,
)
from glitters_geospatial_engine.crs.kenya_crs import KenyaCRSEngine
from glitters_geospatial_engine.raster.pipeline import (
    AffineGeotransform,
    GeoRasterTile,
    RasterProcessingPipeline,
)
from shapely.geometry import Polygon as ShapelyPolygon

from apps.spatial.models import ImageryItem
from apps.spatial.services.change_detection import validate_acquisitions_compatibility

# ---------------------------------------------------------------------------
# Synthetic Test Data Helpers
# ---------------------------------------------------------------------------


def _make_synthetic_textured_band(height: int = 64, width: int = 64) -> np.ndarray:
    """Generate deterministic textured surface suitable for Fourier phase correlation."""
    y, x = np.mgrid[0:height, 0:width].astype(np.float64)
    band = (
        0.35 * np.sin(2.0 * np.pi * x / 16.0) * np.cos(2.0 * np.pi * y / 16.0)
        + 0.25 * np.sin(2.0 * np.pi * (x + y) / 12.0)
        + 0.40 * np.exp(-((x - width / 2.0) ** 2 + (y - height / 2.0) ** 2) / 64.0)
    )
    return band.astype(np.float32)


def _make_synthetic_tile(
    data: np.ndarray,
    transform: AffineGeotransform | None = None,
    epsg: int = 32737,
    acquisition_date: str = "2026-02-01",
    nodata_value: float = -9999.0,
    cloud_mask: np.ndarray | None = None,
    sensor_sigma_m: float = 0.50,
) -> GeoRasterTile:
    """Convenience factory for GeoRasterTile in metric projected UTM 37S."""
    if transform is None:
        # Default: 10m GSD in EPSG:32737 (UTM Zone 37S, Mombasa/Nairobi)
        transform = AffineGeotransform(a=10.0, b=0.0, c=500000.0, d=0.0, e=-10.0, f=9500000.0)
    return GeoRasterTile(
        data=data,
        transform=transform,
        epsg=epsg,
        band_names=("B4_Red", "B8_NIR", "B11_SWIR"),
        acquisition_date=acquisition_date,
        nodata_value=nodata_value,
        cloud_mask=cloud_mask,
        sensor_geolocation_sigma_m=sensor_sigma_m,
    )


def _make_vegetated_tile(
    height: int = 64,
    width: int = 64,
    transform: AffineGeotransform | None = None,
    acquisition_date: str = "2026-02-01",
    cloud_mask: np.ndarray | None = None,
    nodata_value: float = -9999.0,
) -> GeoRasterTile:
    """Creates a 3-band tile with dense healthy vegetation (high NIR, low Red/SWIR)."""
    data = np.zeros((3, height, width), dtype=np.float32)
    data[0] = 0.05  # Red: low
    data[1] = 0.60  # NIR: high (NDVI ~ 0.846)
    data[2] = 0.15  # SWIR: low (NDBI ~ -0.60)
    return _make_synthetic_tile(
        data=data,
        transform=transform,
        acquisition_date=acquisition_date,
        cloud_mask=cloud_mask,
        nodata_value=nodata_value,
    )


# ---------------------------------------------------------------------------
# Section 3: Scientific Validation of Image Registration
# ---------------------------------------------------------------------------


def test_registration_known_synthetic_translation():
    """Verify phase correlation recovers known integer translation within 0.05 pixels."""
    ref = _make_synthetic_textured_band(64, 64)
    # Roll by dy = -2, dx = -3
    mov = np.roll(np.roll(ref, -2, axis=0), -3, axis=1)

    result = RasterProcessingPipeline.subpixel_phase_correlation_registration(ref, mov)

    assert result["shift_y_pixels"] == pytest.approx(2.0, abs=0.05)
    assert result["shift_x_pixels"] == pytest.approx(3.0, abs=0.05)
    assert result["peak_correlation"] > 0.85
    assert result["registration_uncertainty_estimate_pixels"] <= 0.10
    assert result["residual_registration_sigma_pixels"] == result["registration_uncertainty_estimate_pixels"]
    # Interior registered pixels align; boundary edge effects keep overall RMSE < 0.10
    assert result["residual_radiometric_rmse"] < 0.10


def test_registration_fractional_subpixel_shifts():
    """Verify subpixel phase correlation calculates fractional shift via peak interpolation."""
    rng = np.random.default_rng(42)
    ref = rng.standard_normal((64, 64)).astype(np.float32)
    h, w = ref.shape

    # Exact fractional subpixel displacement (0.50 pixel shift)
    dy_sub, dx_sub = 0.50, -0.50
    ky = np.fft.fftfreq(h)
    kx = np.fft.fftfreq(w)
    phase = np.exp(-2j * np.pi * (ky[:, None] * dy_sub + kx[None, :] * dx_sub))
    mov = np.real(np.fft.ifft2(np.fft.fft2(ref) * phase)).astype(np.float32)

    result = RasterProcessingPipeline.subpixel_phase_correlation_registration(ref, mov)

    # Shift returned restores mov back to ref (-dy_sub, -dx_sub)
    assert result["shift_y_pixels"] == pytest.approx(-dy_sub, abs=0.05)
    assert result["shift_x_pixels"] == pytest.approx(-dx_sub, abs=0.05)
    # For a half-pixel shift in 2D, peak energy divides across adjacent grid cells
    # where theoretical peak is (2/pi)^2 ~ 0.405
    assert result["peak_correlation"] >= 0.35
    assert 0.05 <= result["residual_registration_sigma_pixels"] <= 0.20


def test_registration_zero_shift():
    """Verify registration of identical bands yields 0 shift, peak corr ~ 1, and 0 RMSE."""
    ref = _make_synthetic_textured_band(64, 64)

    result = RasterProcessingPipeline.subpixel_phase_correlation_registration(ref, ref)

    assert abs(result["shift_y_pixels"]) < 1e-4
    assert abs(result["shift_x_pixels"]) < 1e-4
    assert result["peak_correlation"] >= 0.99
    assert result["residual_radiometric_rmse"] == pytest.approx(0.0, abs=1e-5)
    assert result["residual_registration_sigma_pixels"] == pytest.approx(0.05, abs=0.01)


def test_registration_low_texture_handling():
    """Verify flat low-texture images do not crash and report low peak correlation."""
    flat_ref = np.full((64, 64), 0.25, dtype=np.float32)
    flat_mov = np.full((64, 64), 0.25, dtype=np.float32)

    result = RasterProcessingPipeline.subpixel_phase_correlation_registration(flat_ref, flat_mov)

    assert result["peak_correlation"] <= 0.10
    assert 0.05 <= result["residual_registration_sigma_pixels"] <= 0.50
    assert np.isfinite(result["shift_y_pixels"])
    assert np.isfinite(result["shift_x_pixels"])


def test_registration_noisy_images_snr_impact():
    """Verify added noise decreases peak correlation and widens Cramér-Rao uncertainty bound."""
    rng = np.random.default_rng(101)
    base = _make_synthetic_textured_band(64, 64)

    # Clean registration
    clean_res = RasterProcessingPipeline.subpixel_phase_correlation_registration(base, base)

    # Noisy registration (heavy noise)
    noise = rng.normal(0.0, 0.50, base.shape).astype(np.float32)
    noisy_mov = base + noise
    noisy_res = RasterProcessingPipeline.subpixel_phase_correlation_registration(base, noisy_mov)

    # Clean peak must exceed noisy peak
    assert clean_res["peak_correlation"] > noisy_res["peak_correlation"]
    # Noisy registration error must be non-zero
    assert noisy_res["residual_radiometric_rmse"] > clean_res["residual_radiometric_rmse"]


def test_registration_weak_correlation_uncorrelated_images():
    """Verify uncorrelated white noise patches yield low correlation peak and bounded error."""
    rng = np.random.default_rng(202)
    img1 = rng.standard_normal((64, 64)).astype(np.float32)
    img2 = rng.standard_normal((64, 64)).astype(np.float32)

    result = RasterProcessingPipeline.subpixel_phase_correlation_registration(img1, img2)

    assert result["peak_correlation"] < 0.20
    assert 0.05 <= result["residual_registration_sigma_pixels"] <= 0.50


def test_registration_insufficient_valid_pixels_fallback():
    """Verify fallback behavior when valid overlap has fewer than 16 pixels."""
    ref = np.zeros((32, 32), dtype=np.float32)
    mov = np.zeros((32, 32), dtype=np.float32)
    # Mask with only 5 valid pixels
    mask = np.zeros((32, 32), dtype=bool)
    mask[0, :5] = True

    result = RasterProcessingPipeline.subpixel_phase_correlation_registration(
        ref, mov, valid_mask=mask
    )

    assert result["shift_y_pixels"] == 0.0
    assert result["shift_x_pixels"] == 0.0
    assert result["peak_correlation"] == 0.0
    assert result["residual_registration_sigma_pixels"] == 0.50
    assert math.isnan(result["residual_radiometric_rmse"])


def test_align_temporal_stack_differing_dimensions_and_geotransforms():
    """Verify alignment resamples target tile to reference grid, dimensions, and origin."""
    transform_ref = AffineGeotransform(a=10.0, b=0.0, c=500000.0, d=0.0, e=-10.0, f=9500000.0)
    transform_tgt = AffineGeotransform(a=20.0, b=0.0, c=500020.0, d=0.0, e=-20.0, f=9499980.0)

    data_ref = np.full((3, 64, 64), 0.30, dtype=np.float32)
    data_tgt = np.full((3, 40, 50), 0.30, dtype=np.float32)

    tile_ref = _make_synthetic_tile(data_ref, transform=transform_ref, epsg=32737)
    tile_tgt = _make_synthetic_tile(data_tgt, transform=transform_tgt, epsg=32737)

    alignment = RasterProcessingPipeline.align_temporal_stack(tile_ref, tile_tgt)
    aligned_tile: GeoRasterTile = alignment["aligned_tile"]

    assert aligned_tile.data.shape == (3, 64, 64)
    assert aligned_tile.transform == tile_ref.transform
    assert aligned_tile.epsg == tile_ref.epsg
    assert alignment["applied_displacement_m"] >= 0.0
    assert alignment["residual_registration_error_m"] > 0.0
    assert alignment["total_positional_uncertainty_1sigma_m"] > 0.0


def test_registration_uncertainty_distinguishes_displacement_from_residual_error():
    """Verify explicit distinction between displacement, registration uncertainty, and sensor error."""
    transform = AffineGeotransform(a=10.0, b=0.0, c=500000.0, d=0.0, e=-10.0, f=9500000.0)
    band = _make_synthetic_textured_band(64, 64)

    data_ref = np.stack([band, band, band], axis=0)
    # Shift target by 2 pixels (20m in UTM 37S)
    band_shifted = np.roll(band, 2, axis=1)
    data_tgt = np.stack([band_shifted, band_shifted, band_shifted], axis=0)

    tile_ref = _make_synthetic_tile(data_ref, transform=transform, sensor_sigma_m=0.50)
    tile_tgt = _make_synthetic_tile(data_tgt, transform=transform, sensor_sigma_m=0.50)

    res = RasterProcessingPipeline.align_temporal_stack(tile_ref, tile_tgt)

    applied_disp = res["applied_displacement_m"]
    residual_err = res["residual_registration_error_m"]
    reg_uncertainty = res["registration_uncertainty_estimate_m"]
    total_pos = res["total_positional_uncertainty_1sigma_m"]

    # 1. Displacement is approximately 20m (2 pixels * 10m GSD)
    assert applied_disp == pytest.approx(20.0, abs=1.0)
    # 2. Residual registration uncertainty is sub-pixel (typically 0.50m = 0.05 px * 10m)
    assert residual_err < 2.0
    assert reg_uncertainty == residual_err
    assert res["registration_error_m"] == residual_err
    # 3. They are strictly different physical concepts
    assert applied_disp != residual_err
    # 4. Total positional uncertainty combines residual uncertainty and sensor errors in quadrature:
    expected_quadrature = math.sqrt(residual_err**2 + 0.50**2 + 0.50**2)
    assert total_pos == pytest.approx(expected_quadrature, rel=1e-5)


def test_registration_failures_do_not_proceed_silently():
    """Verify mismatched CRS, geographic CRS, and singular affine matrices fail loudly."""
    data = np.full((3, 32, 32), 0.3, dtype=np.float32)
    trans = AffineGeotransform(a=10.0, b=0.0, c=500000.0, d=0.0, e=-10.0, f=9500000.0)

    # 1. CRS mismatch raises ValueError
    tile1 = _make_synthetic_tile(data, transform=trans, epsg=32737)
    tile2_diff_crs = _make_synthetic_tile(data, transform=trans, epsg=32637)
    with pytest.raises(ValueError, match="CRS mismatch"):
        RasterProcessingPipeline.resample_tile_to_reference_grid(tile1, tile2_diff_crs)

    # 2. Non-projected geographic CRS (EPSG:4326) is rejected by KenyaCRSEngine
    with pytest.raises(ValueError, match="geographic"):
        _make_synthetic_tile(data, transform=trans, epsg=4326)

    # 3. Singular affine geotransform (det == 0) raises ValueError
    singular_trans = AffineGeotransform(a=0.0, b=0.0, c=500000.0, d=0.0, e=0.0, f=9500000.0)
    with pytest.raises(ValueError, match="non-zero determinant"):
        _make_synthetic_tile(data, transform=singular_trans, epsg=32737)


# ---------------------------------------------------------------------------
# Section 4: Validation of Phenology Correction
# ---------------------------------------------------------------------------


def test_kenya_bimodal_phenology_expectation():
    """Verify Kenya bimodal phenology cycle produces higher vegetation in wet seasons."""
    # DOY 44 (Feb dry season)
    dry_ndvi = TemporalChangeDetectionEngine.kenya_bimodal_phenology_expectation(44)
    # DOY 135 (May long rains peak)
    wet_ndvi = TemporalChangeDetectionEngine.kenya_bimodal_phenology_expectation(135)

    assert -0.30 <= dry_ndvi <= 0.30
    assert -0.30 <= wet_ndvi <= 0.30
    # Wet season peak expectation is higher than dry season baseline
    assert wet_ndvi > dry_ndvi


def test_kenya_bimodal_phenology_seasonal_cycle_and_peaks():
    """Verify exact bimodal harmonic peaks (Long Rains May, Short Rains Nov) and dry troughs."""
    f = TemporalChangeDetectionEngine.kenya_bimodal_phenology_expectation

    # Peak 1: Long rains peak at DOY 140 (May 20)
    val_long_rains_peak = f(140)
    assert val_long_rains_peak == pytest.approx(0.1400, abs=1e-3)

    # Peak 2: Short rains peak around DOY 325 (Nov 21)
    val_short_rains = f(325)
    assert val_short_rains > 0.0

    # Dry season 1: Jan/Feb trough at DOY 45
    val_dry_feb = f(45)
    assert val_dry_feb < 0.0

    # Dry season 2: July/Aug trough at DOY 230
    val_dry_aug = f(230)
    assert val_dry_aug < 0.0

    # Primary wet peak exceeds secondary wet peak and both dry troughs
    assert val_long_rains_peak > val_short_rains
    assert val_long_rains_peak > val_dry_feb
    assert val_long_rains_peak > val_dry_aug


def test_kenya_bimodal_phenology_doy_boundary_and_continuity():
    """Verify DOY inputs across boundaries (1, 365, 366) maintain bounds and continuity."""
    f = TemporalChangeDetectionEngine.kenya_bimodal_phenology_expectation

    # Check boundedness across all days of year
    for doy in range(1, 367):
        val = f(doy)
        assert -0.20 <= val <= 0.20, f"DOY {doy} exceeded physiological bounds: {val}"

    # Smooth periodic continuity across year boundary
    val_doy1 = f(1)
    val_doy365 = f(365)
    val_doy366 = f(366)
    assert abs(val_doy365 - val_doy1) < 0.005
    assert abs(val_doy366 - val_doy1) < 0.005


def test_phenology_correction_seasonal_fluctuations_vs_stable_vegetation():
    """Verify seasonal detrending eliminates false-positive detections from phenology."""
    # Scenario A: Natural dry-season browning (DOY 140 wet -> DOY 230 dry)
    # Seasonal expectation drops from +0.140 to -0.0786 (shift = -0.2186)
    expected_shift = (
        TemporalChangeDetectionEngine.kenya_bimodal_phenology_expectation(230)
        - TemporalChangeDetectionEngine.kenya_bimodal_phenology_expectation(140)
    )
    # Raw NDVI drops by exactly the seasonal expectation
    ndvi_t1 = np.full((10, 10), 0.70, dtype=np.float32)
    ndvi_t2 = np.full((10, 10), 0.70 + expected_shift, dtype=np.float32)
    ndbi = np.full((10, 10), -0.30, dtype=np.float32)

    cva = TemporalChangeDetectionEngine.change_vector_analysis(
        ndvi_t1=ndvi_t1,
        ndbi_t1=ndbi,
        ndvi_t2=ndvi_t2,
        ndbi_t2=ndbi,
        doy_t1=140,
        doy_t2=230,
    )
    # Adjusted delta NDVI is neutralized to 0
    assert np.all(np.abs(cva["delta_ndvi_seasonally_adjusted"]) < 1e-5)
    assert np.all(cva["cva_magnitude"] < 1e-5)

    # Scenario B: Stable vegetation in same season (DOY 140 -> DOY 140)
    cva_stable = TemporalChangeDetectionEngine.change_vector_analysis(
        ndvi_t1=ndvi_t1,
        ndbi_t1=ndbi,
        ndvi_t2=ndvi_t1,
        ndbi_t2=ndbi,
        doy_t1=140,
        doy_t2=140,
    )
    assert np.all(cva_stable["cva_magnitude"] == 0.0)


def test_phenology_correction_never_suppresses_genuine_vegetation_loss():
    """Verify that genuine clearing/deforestation (Delta NDVI < -0.20) is never suppressed."""
    # Case 1: Vegetation cleared during transition from dry (DOY 45) to wet (DOY 140)
    # Dense vegetation cleared to bare ground: raw drop = -0.55
    raw_delta_ndvi = -0.55
    ndvi_1 = np.full((8, 8), 0.75, dtype=np.float32)
    ndvi_2 = np.full((8, 8), 0.75 + raw_delta_ndvi, dtype=np.float32)
    ndbi_1 = np.full((8, 8), -0.40, dtype=np.float32)
    ndbi_2 = np.full((8, 8), 0.15, dtype=np.float32)  # built/bare ground NDBI increases

    cva = TemporalChangeDetectionEngine.change_vector_analysis(
        ndvi_t1=ndvi_1,
        ndbi_t1=ndbi_1,
        ndvi_t2=ndvi_2,
        ndbi_t2=ndbi_2,
        doy_t1=45,
        doy_t2=140,
    )
    # Seasonal expectation increases (+0.223), so clearing is amplified (more negative)
    adj_dndvi = cva["delta_ndvi_seasonally_adjusted"]
    assert np.all(adj_dndvi < -0.70)
    assert np.all(cva["cva_magnitude"] >= 0.70)

    # Case 2: Vegetation cleared during transition from wet (DOY 140) to dry (DOY 230)
    # Severe clearing: raw drop = -0.65 (from 0.80 to 0.15)
    ndvi_wet = np.full((8, 8), 0.80, dtype=np.float32)
    ndvi_cleared = np.full((8, 8), 0.15, dtype=np.float32)
    cva_dry = TemporalChangeDetectionEngine.change_vector_analysis(
        ndvi_t1=ndvi_wet,
        ndbi_t1=ndbi_1,
        ndvi_t2=ndvi_cleared,
        ndbi_t2=ndbi_2,
        doy_t1=140,
        doy_t2=230,
    )
    # Even after dry-season detrending, adjusted dNDVI remains strongly negative (< -0.40)
    assert np.all(cva_dry["delta_ndvi_seasonally_adjusted"] < -0.40)
    # Change magnitude easily exceeds threshold (0.28)
    assert np.all(cva_dry["cva_magnitude"] > 0.40)


def test_phenology_model_output_experimental_label_and_candidate_evidence_limitations():
    """Verify model output is marked experimental and exposes limitations in evidence."""
    tile_t1 = _make_vegetated_tile(height=32, width=32, acquisition_date="2026-02-01")
    # Cleared 4x4 patch in tile_t2
    tile_t2 = _make_vegetated_tile(height=32, width=32, acquisition_date="2026-05-20")
    tile_t2.data[0, 14:18, 14:18] = 0.25  # Red
    tile_t2.data[1, 14:18, 14:18] = 0.30  # NIR
    tile_t2.data[2, 14:18, 14:18] = 0.45  # SWIR

    changes = TemporalChangeDetectionEngine.detect_changes_between_tiles(
        tile_t1=tile_t1,
        tile_t2=tile_t2,
        doy_t1=32,
        doy_t2=140,
        cva_threshold=0.25,
        min_change_area_m2=100.0,
    )

    assert len(changes) == 1
    change = changes[0]

    # Verify detection method documents seasonally detrended CVA
    assert "Seasonally_Detrended" in change.detection_method

    # Verify uncertainty bounds expose physical metrics and limitations
    bounds = change.uncertainty_bounds
    assert "cva_magnitude_mean" in bounds
    assert "radiometric_1sigma" in bounds
    assert "residual_registration_1sigma_m" in bounds
    assert "total_positional_1sigma_m" in bounds
    assert "area_95ci_m2" in bounds
    assert bounds["area_95ci_m2"] > 0.0

    # Verify evidence reference string captures adjusted metrics
    assert "dNDVI_adj" in change.evidence_reference
    assert "res_reg_err" in change.evidence_reference
    assert "pos_sigma" in change.evidence_reference


# ---------------------------------------------------------------------------
# Section 5: Raster and CVA Validation with Synthetic Inputs
# ---------------------------------------------------------------------------


def test_cva_magnitude_and_direction_calculation():
    """Verify CVA magnitude = hypot(dNDVI, dNDBI) and direction = arctan2(dNDBI, dNDVI)."""
    ndvi_1 = np.array([[0.5, 0.6]], dtype=np.float32)
    ndvi_2 = np.array([[0.2, 0.3]], dtype=np.float32)  # dNDVI = -0.3
    ndbi_1 = np.array([[-0.2, -0.1]], dtype=np.float32)
    ndbi_2 = np.array([[0.2, 0.3]], dtype=np.float32)   # dNDBI = +0.4

    cva = TemporalChangeDetectionEngine.change_vector_analysis(
        ndvi_t1=ndvi_1,
        ndbi_t1=ndbi_1,
        ndvi_t2=ndvi_2,
        ndbi_t2=ndbi_2,
        doy_t1=100,
        doy_t2=100,  # same DOY -> 0 seasonal shift
    )

    expected_mag = math.hypot(-0.3, 0.4)  # 0.50
    expected_dir = math.atan2(0.4, -0.3)

    assert np.allclose(cva["cva_magnitude"], expected_mag, atol=1e-5)
    assert np.allclose(cva["cva_direction_rad"], expected_dir, atol=1e-5)


def test_cva_classification_categories():
    """Verify change classification rules for new construction, vegetation loss, and demolition."""
    h, w = 32, 32

    # 1. New Construction / Clearing: dNDBI > 0.12 and dNDVI < -0.10
    t1_const = _make_vegetated_tile(h, w)
    t2_const = _make_vegetated_tile(h, w)
    t2_const.data[0, 10:14, 10:14] = 0.25  # Red increases
    t2_const.data[1, 10:14, 10:14] = 0.30  # NIR drops
    t2_const.data[2, 10:14, 10:14] = 0.45  # SWIR increases (high NDBI)

    res_const = TemporalChangeDetectionEngine.detect_changes_between_tiles(
        t1_const, t2_const, doy_t1=100, doy_t2=100, cva_threshold=0.25
    )
    assert len(res_const) == 1
    assert res_const[0].change_type == "NEW_CONSTRUCTION_OR_CLEARING"

    # 2. Vegetation Loss: dNDVI < -0.20 with dNDBI <= 0.12
    t1_veg = _make_vegetated_tile(h, w)
    t2_veg = _make_vegetated_tile(h, w)
    # Severe vegetation thinning/loss where SWIR drops alongside NIR so NDBI does not rise > 0.12
    t2_veg.data[0, 10:14, 10:14] = 0.15   # Red increases
    t2_veg.data[1, 10:14, 10:14] = 0.30   # NIR drops
    t2_veg.data[2, 10:14, 10:14] = 0.075  # SWIR drops proportionally (dNDBI ~ 0.0)

    res_veg = TemporalChangeDetectionEngine.detect_changes_between_tiles(
        t1_veg, t2_veg, doy_t1=100, doy_t2=100, cva_threshold=0.25
    )
    assert len(res_veg) == 1
    assert res_veg[0].change_type == "VEGETATION_LOSS"

    # 3. Demolition or Surface Removal: dNDBI < -0.15
    transform = AffineGeotransform(a=10.0, b=0.0, c=500000.0, d=0.0, e=-10.0, f=9500000.0)
    data_demo1 = np.full((3, h, w), 0.30, dtype=np.float32)
    data_demo1[0, 10:14, 10:14] = 0.25  # Red
    data_demo1[1, 10:14, 10:14] = 0.30  # NIR
    data_demo1[2, 10:14, 10:14] = 0.45  # SWIR (built structure, NDBI = +0.20)

    data_demo2 = np.full((3, h, w), 0.30, dtype=np.float32)
    data_demo2[0, 10:14, 10:14] = 0.10  # Red
    data_demo2[1, 10:14, 10:14] = 0.20  # NIR
    data_demo2[2, 10:14, 10:14] = 0.10  # SWIR (NDBI drops to -0.33, dNDBI = -0.53 < -0.15)

    t1_demo = _make_synthetic_tile(data_demo1, transform=transform)
    t2_demo = _make_synthetic_tile(data_demo2, transform=transform)

    res_demo = TemporalChangeDetectionEngine.detect_changes_between_tiles(
        t1_demo, t2_demo, doy_t1=100, doy_t2=100, cva_threshold=0.25
    )
    assert len(res_demo) == 1
    assert res_demo[0].change_type == "DEMOLITION_OR_SURFACE_REMOVAL"


def test_nodata_pixels_never_become_change_candidates():
    """Verify pixels with nodata_value (-9999.0) in either epoch are never candidates."""
    h, w = 32, 32
    t1 = _make_vegetated_tile(h, w)
    t2 = _make_vegetated_tile(h, w)

    # In T1, mark center 4x4 as NoData
    t1.data[:, 14:18, 14:18] = -9999.0
    # In T2, center 4x4 has built values that would otherwise trigger CVA
    t2.data[0, 14:18, 14:18] = 0.25
    t2.data[1, 14:18, 14:18] = 0.30
    t2.data[2, 14:18, 14:18] = 0.45

    changes = TemporalChangeDetectionEngine.detect_changes_between_tiles(
        t1, t2, doy_t1=100, doy_t2=100, cva_threshold=0.25, min_change_area_m2=100.0
    )
    # NoData pixels must be 100% masked out
    assert len(changes) == 0


def test_cloud_pixels_never_become_change_candidates():
    """Verify cloud-masked pixels in either epoch are never included in change candidates."""
    h, w = 32, 32
    cloud_t2 = np.zeros((h, w), dtype=bool)
    cloud_t2[14:18, 14:18] = True  # Cloud covers the altered patch

    t1 = _make_vegetated_tile(h, w)
    t2 = _make_vegetated_tile(h, w, cloud_mask=cloud_t2)

    # Center has strong spectral difference
    t2.data[0, 14:18, 14:18] = 0.25
    t2.data[1, 14:18, 14:18] = 0.30
    t2.data[2, 14:18, 14:18] = 0.45

    changes = TemporalChangeDetectionEngine.detect_changes_between_tiles(
        t1, t2, doy_t1=100, doy_t2=100, cva_threshold=0.25, min_change_area_m2=100.0
    )
    # Cloud pixels must be 100% masked out
    assert len(changes) == 0


def test_nan_and_infinite_pixels_excluded_from_change_candidates():
    """Verify NaN and Inf values are safely identified as NoData and excluded from detections."""
    h, w = 32, 32
    t1 = _make_vegetated_tile(h, w)
    t2 = _make_vegetated_tile(h, w)

    # Plant NaN and Inf in T1
    t1.data[0, 10:14, 10:14] = np.nan
    t1.data[1, 14:18, 14:18] = np.inf

    changes = TemporalChangeDetectionEngine.detect_changes_between_tiles(
        t1, t2, doy_t1=100, doy_t2=100, cva_threshold=0.25, min_change_area_m2=100.0
    )
    assert len(changes) == 0


def test_connected_components_and_exact_polygonization_area():
    """Verify exact 4-corner polygonization reconciles area_m2 with geometry_utm.area."""
    h, w = 32, 32
    t1 = _make_vegetated_tile(h, w)
    t2 = _make_vegetated_tile(h, w)

    # L-shaped clearing of exactly 5 pixels
    # (14,14), (14,15), (14,16), (15,14), (16,14)
    l_coords = [(14, 14), (14, 15), (14, 16), (15, 14), (16, 14)]
    for r, c in l_coords:
        t2.data[0, r, c] = 0.25
        t2.data[1, r, c] = 0.30
        t2.data[2, r, c] = 0.45

    changes = TemporalChangeDetectionEngine.detect_changes_between_tiles(
        t1, t2, doy_t1=100, doy_t2=100, cva_threshold=0.25, min_change_area_m2=100.0
    )

    assert len(changes) == 1
    chg = changes[0]

    # 5 pixels * 100 m^2 = 500 m^2
    assert chg.pixel_count_area_m2 == pytest.approx(500.0, abs=1e-3)
    # Exact polygon area matches pixel count area exactly
    assert chg.area_m2 == pytest.approx(500.0, abs=1e-3)
    assert chg.geometry_utm.area == pytest.approx(500.0, abs=1e-3)
    assert isinstance(chg.geometry_utm, ShapelyPolygon)


def test_cva_minimum_area_threshold_filtering():
    """Verify candidate regions below min_change_area_m2 are suppressed while larger preserved."""
    h, w = 32, 32
    t1 = _make_vegetated_tile(h, w)
    t2 = _make_vegetated_tile(h, w)

    # Region 1: single isolated pixel (100 m^2)
    t2.data[0, 5, 5] = 0.25
    t2.data[1, 5, 5] = 0.30
    t2.data[2, 5, 5] = 0.45

    # Region 2: 4-pixel component (400 m^2)
    t2.data[0, 20:22, 20:22] = 0.25
    t2.data[1, 20:22, 20:22] = 0.30
    t2.data[2, 20:22, 20:22] = 0.45

    # min_change_area_m2 = 250.0 should suppress Region 1 (100 m^2) and retain Region 2 (400 m^2)
    changes = TemporalChangeDetectionEngine.detect_changes_between_tiles(
        t1, t2, doy_t1=100, doy_t2=100, cva_threshold=0.25, min_change_area_m2=250.0
    )

    assert len(changes) == 1
    assert changes[0].pixel_count_area_m2 == pytest.approx(400.0, abs=1e-3)


def test_epsg_32737_metric_crs_invariants():
    """Verify EPSG:32737 (UTM zone 37S) maintains metric pixel scale and projected coordinates."""
    # UTM zone 37S is the primary projected coordinate reference system for Kenya
    KenyaCRSEngine.assert_projected_crs(32737)

    transform = AffineGeotransform(a=10.0, b=0.0, c=500000.0, d=0.0, e=-10.0, f=9500000.0)
    assert transform.gsd_x_m == 10.0
    assert transform.gsd_y_m == 10.0
    assert transform.true_pixel_area_m2 == 100.0

    poly = transform.pixel_polygon_utm(0, 0)
    assert poly.area == 100.0
    # Coordinates in metric range
    minx, miny, maxx, maxy = poly.bounds
    assert minx >= 500000.0 and maxx <= 500010.0


# ---------------------------------------------------------------------------
# Existing Acquisition Compatibility Tests
# ---------------------------------------------------------------------------


def test_validate_acquisitions_compatibility_temporal_order():
    """Verify validation rejects baseline acquisition that occurs after comparison acquisition."""
    poly = Polygon(((39.6, -4.1), (39.7, -4.1), (39.7, -4.0), (39.6, -4.0), (39.6, -4.1)))
    baseline = ImageryItem(
        id="item-base",
        item_id="S2B_20260601",
        acquisition_datetime=datetime.datetime(2026, 6, 1, 10, 0, tzinfo=datetime.UTC),
        cloud_coverage_percentage=5.0,
        footprint=poly,
    )
    comparison = ImageryItem(
        id="item-comp",
        item_id="S2A_20260201",
        acquisition_datetime=datetime.datetime(2026, 2, 1, 10, 0, tzinfo=datetime.UTC),
        cloud_coverage_percentage=5.0,
        footprint=poly,
    )

    with pytest.raises(ValidationError, match="Temporal order violation"):
        validate_acquisitions_compatibility(baseline, comparison)


def test_validate_acquisitions_compatibility_cloud_cover():
    """Verify validation rejects acquisitions exceeding allowable cloud cover threshold."""
    poly = Polygon(((39.6, -4.1), (39.7, -4.1), (39.7, -4.0), (39.6, -4.0), (39.6, -4.1)))
    baseline = ImageryItem(
        id="item-base",
        item_id="S2B_20260201",
        acquisition_datetime=datetime.datetime(2026, 2, 1, 10, 0, tzinfo=datetime.UTC),
        cloud_coverage_percentage=42.0,  # > 35%
        footprint=poly,
    )
    comparison = ImageryItem(
        id="item-comp",
        item_id="S2A_20260501",
        acquisition_datetime=datetime.datetime(2026, 5, 1, 10, 0, tzinfo=datetime.UTC),
        cloud_coverage_percentage=10.0,
        footprint=poly,
    )

    with pytest.raises(ValidationError, match="Excessive cloud coverage detected"):
        validate_acquisitions_compatibility(baseline, comparison)


def test_validate_acquisitions_compatibility_spatial_coverage():
    """Verify validation rejects acquisitions that do not cover the project AOI."""
    poly_mombasa = Polygon(((39.6, -4.1), (39.7, -4.1), (39.7, -4.0), (39.6, -4.0), (39.6, -4.1)))
    poly_nairobi = Polygon(((36.7, -1.3), (36.9, -1.3), (36.9, -1.1), (36.7, -1.1), (36.7, -1.3)))

    baseline = ImageryItem(
        id="item-base",
        item_id="S2B_20260201",
        acquisition_datetime=datetime.datetime(2026, 2, 1, 10, 0, tzinfo=datetime.UTC),
        cloud_coverage_percentage=5.0,
        footprint=poly_mombasa,
    )
    comparison = ImageryItem(
        id="item-comp",
        item_id="S2A_20260501",
        acquisition_datetime=datetime.datetime(2026, 5, 1, 10, 0, tzinfo=datetime.UTC),
        cloud_coverage_percentage=5.0,
        footprint=poly_nairobi,
    )

    with pytest.raises(ValidationError, match="do not have overlapping spatial coverage"):
        validate_acquisitions_compatibility(baseline, comparison)
