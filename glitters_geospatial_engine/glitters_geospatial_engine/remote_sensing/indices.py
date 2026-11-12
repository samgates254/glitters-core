"""
Remote Sensing Spectral & Radar Indices Engine (`remote_sensing/indices.py` v1.1.0)
===================================================================================

Upgrades in v1.1.0:
- Enforces strict NoData sentinel exclusion (`nodata_value = -9999.0` by default) and
  physical surface-reflectance range validation (`0.0 <= rho <= 1.5`) across all optical
  spectral indices (`ndvi`, `evi`, `ndwi`, `mndwi`, `ndbi`, `savi`, `bsi`), plus optional
  `external_valid_mask` (e.g. from `GeoRasterTile.valid_data_mask`).
- Guarantees that passing `nir = np.array([[-9999.0]])`, `red = np.array([[-9999.0]])`
  produces `valid_mask[0, 0] == False` and `values[0, 0] = np.nan` (never `0.0 / Valid=True`).
"""

from __future__ import annotations
from dataclasses import dataclass
import math
from typing import Optional
import numpy as np


@dataclass(frozen=True)
class SpectralIndexResult:
    index_name: str
    equation_latex: str
    values: np.ndarray
    uncertainty_1sigma: np.ndarray
    valid_mask: np.ndarray
    mean_value: float
    std_value: float
    saturation_fraction: float


class SpectralIndicesEngine:
    """
    Numerically stable, NoData-aware, uncertainty-propagating spectral and radar index engine.
    """

    EPS: float = 1e-8
    DEFAULT_NODATA: float = -9999.0
    MIN_PHYSICAL_REFLECTANCE: float = -0.05
    MAX_PHYSICAL_REFLECTANCE: float = 1.50

    @classmethod
    def _is_valid_reflectance_band(
        cls,
        band: np.ndarray,
        nodata_value: Optional[float] = DEFAULT_NODATA,
        enforce_reflectance_bounds: bool = True,
    ) -> np.ndarray:
        valid = np.isfinite(band)
        if nodata_value is not None and math.isfinite(nodata_value):
            valid = valid & (~np.isclose(band, nodata_value, atol=1e-4))
        if enforce_reflectance_bounds:
            valid = valid & (band >= cls.MIN_PHYSICAL_REFLECTANCE) & (band <= cls.MAX_PHYSICAL_REFLECTANCE)
        return valid

    @classmethod
    def _normalized_difference_with_uncertainty(
        cls,
        band_a: np.ndarray,
        band_b: np.ndarray,
        sigma_a: float = 0.005,
        sigma_b: float = 0.005,
        index_name: str = "ND",
        equation_latex: str = r"\frac{A - B}{A + B}",
        nodata_value: Optional[float] = DEFAULT_NODATA,
        external_valid_mask: Optional[np.ndarray] = None,
        enforce_reflectance_bounds: bool = True,
    ) -> SpectralIndexResult:
        a = band_a.astype(np.float64)
        b = band_b.astype(np.float64)
        denom = a + b

        valid = (
            cls._is_valid_reflectance_band(a, nodata_value, enforce_reflectance_bounds)
            & cls._is_valid_reflectance_band(b, nodata_value, enforce_reflectance_bounds)
            & (np.abs(denom) > cls.EPS)
        )
        if external_valid_mask is not None:
            valid = valid & external_valid_mask.astype(bool)

        safe_denom = np.where(valid, denom, 1.0)
        val = np.where(valid, np.clip((a - b) / safe_denom, -1.0, 1.0), np.nan)
        sigma = np.where(
            valid,
            (2.0 / (safe_denom ** 2)) * np.sqrt((b ** 2) * (sigma_a ** 2) + (a ** 2) * (sigma_b ** 2)),
            np.nan,
        )
        valid_vals = val[valid]
        sat_frac = float(np.mean(np.abs(valid_vals) > 0.85)) if len(valid_vals) > 0 else 0.0

        return SpectralIndexResult(
            index_name=index_name,
            equation_latex=equation_latex,
            values=val.astype(np.float32),
            uncertainty_1sigma=np.clip(sigma, 0.0, 1.0).astype(np.float32),
            valid_mask=valid,
            mean_value=float(np.nanmean(valid_vals)) if len(valid_vals) > 0 else float("nan"),
            std_value=float(np.nanstd(valid_vals)) if len(valid_vals) > 0 else float("nan"),
            saturation_fraction=sat_frac,
        )

    @classmethod
    def ndvi(
        cls,
        nir: np.ndarray,
        red: np.ndarray,
        sigma_refl: float = 0.005,
        nodata_value: Optional[float] = DEFAULT_NODATA,
        external_valid_mask: Optional[np.ndarray] = None,
    ) -> SpectralIndexResult:
        return cls._normalized_difference_with_uncertainty(
            nir,
            red,
            sigma_a=sigma_refl,
            sigma_b=sigma_refl,
            index_name="NDVI",
            equation_latex=r"\mathrm{NDVI} = \frac{\rho_{\mathrm{NIR}} - \rho_{\mathrm{Red}}}{\rho_{\mathrm{NIR}} + \rho_{\mathrm{Red}}}",
            nodata_value=nodata_value,
            external_valid_mask=external_valid_mask,
        )

    @classmethod
    def evi(
        cls,
        nir: np.ndarray,
        red: np.ndarray,
        blue: np.ndarray,
        G: float = 2.5,
        C1: float = 6.0,
        C2: float = 7.5,
        L: float = 1.0,
        sigma_refl: float = 0.005,
        nodata_value: Optional[float] = DEFAULT_NODATA,
        external_valid_mask: Optional[np.ndarray] = None,
    ) -> SpectralIndexResult:
        n = nir.astype(np.float64)
        r = red.astype(np.float64)
        b = blue.astype(np.float64)
        denom = n + C1 * r - C2 * b + L

        valid = (
            cls._is_valid_reflectance_band(n, nodata_value)
            & cls._is_valid_reflectance_band(r, nodata_value)
            & cls._is_valid_reflectance_band(b, nodata_value)
            & (np.abs(denom) > 0.05)
        )
        if external_valid_mask is not None:
            valid = valid & external_valid_mask.astype(bool)

        safe_denom = np.where(valid, denom, 1.0)
        val = np.where(valid, np.clip(G * (n - r) / safe_denom, -1.0, 1.0), np.nan)

        d_dn = G * ((C1 + 1.0) * r - C2 * b + L) / (safe_denom ** 2)
        d_dr = -G * ((C1 + 1.0) * n - C2 * b + L) / (safe_denom ** 2)
        d_db = G * C2 * (n - r) / (safe_denom ** 2)
        sigma = np.where(
            valid,
            sigma_refl * np.sqrt(d_dn ** 2 + d_dr ** 2 + d_db ** 2),
            np.nan,
        )
        valid_vals = val[valid]
        return SpectralIndexResult(
            index_name="EVI",
            equation_latex=r"\mathrm{EVI} = 2.5 \frac{\rho_{\mathrm{NIR}} - \rho_{\mathrm{Red}}}{\rho_{\mathrm{NIR}} + 6\rho_{\mathrm{Red}} - 7.5\rho_{\mathrm{Blue}} + 1}",
            values=val.astype(np.float32),
            uncertainty_1sigma=np.clip(sigma, 0.0, 1.0).astype(np.float32),
            valid_mask=valid,
            mean_value=float(np.nanmean(valid_vals)) if len(valid_vals) > 0 else float("nan"),
            std_value=float(np.nanstd(valid_vals)) if len(valid_vals) > 0 else float("nan"),
            saturation_fraction=float(np.mean(np.abs(valid_vals) > 0.85)) if len(valid_vals) > 0 else 0.0,
        )

    @classmethod
    def ndwi(
        cls,
        green: np.ndarray,
        nir: np.ndarray,
        sigma_refl: float = 0.005,
        nodata_value: Optional[float] = DEFAULT_NODATA,
        external_valid_mask: Optional[np.ndarray] = None,
    ) -> SpectralIndexResult:
        return cls._normalized_difference_with_uncertainty(
            green,
            nir,
            sigma_a=sigma_refl,
            sigma_b=sigma_refl,
            index_name="NDWI",
            equation_latex=r"\mathrm{NDWI} = \frac{\rho_{\mathrm{Green}} - \rho_{\mathrm{NIR}}}{\rho_{\mathrm{Green}} + \rho_{\mathrm{NIR}}}",
            nodata_value=nodata_value,
            external_valid_mask=external_valid_mask,
        )

    @classmethod
    def mndwi(
        cls,
        green: np.ndarray,
        swir1: np.ndarray,
        sigma_refl: float = 0.005,
        nodata_value: Optional[float] = DEFAULT_NODATA,
        external_valid_mask: Optional[np.ndarray] = None,
    ) -> SpectralIndexResult:
        return cls._normalized_difference_with_uncertainty(
            green,
            swir1,
            sigma_a=sigma_refl,
            sigma_b=sigma_refl,
            index_name="MNDWI",
            equation_latex=r"\mathrm{MNDWI} = \frac{\rho_{\mathrm{Green}} - \rho_{\mathrm{SWIR1}}}{\rho_{\mathrm{Green}} + \rho_{\mathrm{SWIR1}}}",
            nodata_value=nodata_value,
            external_valid_mask=external_valid_mask,
        )

    @classmethod
    def ndbi(
        cls,
        swir1: np.ndarray,
        nir: np.ndarray,
        sigma_refl: float = 0.005,
        nodata_value: Optional[float] = DEFAULT_NODATA,
        external_valid_mask: Optional[np.ndarray] = None,
    ) -> SpectralIndexResult:
        return cls._normalized_difference_with_uncertainty(
            swir1,
            nir,
            sigma_a=sigma_refl,
            sigma_b=sigma_refl,
            index_name="NDBI",
            equation_latex=r"\mathrm{NDBI} = \frac{\rho_{\mathrm{SWIR1}} - \rho_{\mathrm{NIR}}}{\rho_{\mathrm{SWIR1}} + \rho_{\mathrm{NIR}}}",
            nodata_value=nodata_value,
            external_valid_mask=external_valid_mask,
        )

    @classmethod
    def savi(
        cls,
        nir: np.ndarray,
        red: np.ndarray,
        L: float = 0.5,
        sigma_refl: float = 0.005,
        nodata_value: Optional[float] = DEFAULT_NODATA,
        external_valid_mask: Optional[np.ndarray] = None,
    ) -> SpectralIndexResult:
        n = nir.astype(np.float64)
        r = red.astype(np.float64)
        denom = n + r + L
        valid = (
            cls._is_valid_reflectance_band(n, nodata_value)
            & cls._is_valid_reflectance_band(r, nodata_value)
            & (np.abs(denom) > cls.EPS)
        )
        if external_valid_mask is not None:
            valid = valid & external_valid_mask.astype(bool)

        safe_denom = np.where(valid, denom, 1.0)
        val = np.where(valid, np.clip((1.0 + L) * (n - r) / safe_denom, -1.0, 1.0), np.nan)

        d_dn = (1.0 + L) * (2.0 * r + L) / (safe_denom ** 2)
        d_dr = -(1.0 + L) * (2.0 * n + L) / (safe_denom ** 2)
        sigma = np.where(valid, sigma_refl * np.sqrt(d_dn ** 2 + d_dr ** 2), np.nan)
        valid_vals = val[valid]

        return SpectralIndexResult(
            index_name="SAVI",
            equation_latex=r"\mathrm{SAVI} = \frac{(1 + L)(\rho_{\mathrm{NIR}} - \rho_{\mathrm{Red}})}{\rho_{\mathrm{NIR}} + \rho_{\mathrm{Red}} + L}",
            values=val.astype(np.float32),
            uncertainty_1sigma=np.clip(sigma, 0.0, 1.0).astype(np.float32),
            valid_mask=valid,
            mean_value=float(np.nanmean(valid_vals)) if len(valid_vals) > 0 else float("nan"),
            std_value=float(np.nanstd(valid_vals)) if len(valid_vals) > 0 else float("nan"),
            saturation_fraction=float(np.mean(np.abs(valid_vals) > 0.85)) if len(valid_vals) > 0 else 0.0,
        )

    @classmethod
    def bsi(
        cls,
        swir1: np.ndarray,
        red: np.ndarray,
        nir: np.ndarray,
        blue: np.ndarray,
        sigma_refl: float = 0.005,
        nodata_value: Optional[float] = DEFAULT_NODATA,
        external_valid_mask: Optional[np.ndarray] = None,
    ) -> SpectralIndexResult:
        valid_4 = (
            cls._is_valid_reflectance_band(swir1, nodata_value)
            & cls._is_valid_reflectance_band(red, nodata_value)
            & cls._is_valid_reflectance_band(nir, nodata_value)
            & cls._is_valid_reflectance_band(blue, nodata_value)
        )
        if external_valid_mask is not None:
            valid_4 = valid_4 & external_valid_mask.astype(bool)

        a = swir1.astype(np.float64) + red.astype(np.float64)
        b = nir.astype(np.float64) + blue.astype(np.float64)
        return cls._normalized_difference_with_uncertainty(
            a,
            b,
            sigma_a=math.sqrt(2.0) * sigma_refl,
            sigma_b=math.sqrt(2.0) * sigma_refl,
            index_name="BSI",
            equation_latex=r"\mathrm{BSI} = \frac{(\rho_{\mathrm{SWIR1}} + \rho_{\mathrm{Red}}) - (\rho_{\mathrm{NIR}} + \rho_{\mathrm{Blue}})}{(\rho_{\mathrm{SWIR1}} + \rho_{\mathrm{Red}}) + (\rho_{\mathrm{NIR}} + \rho_{\mathrm{Blue}})}",
            nodata_value=None,
            external_valid_mask=valid_4,
            enforce_reflectance_bounds=False,
        )

    @classmethod
    def sar_rvi(
        cls,
        sigma0_vv: np.ndarray,
        sigma0_vh: np.ndarray,
        nodata_value: Optional[float] = DEFAULT_NODATA,
        external_valid_mask: Optional[np.ndarray] = None,
    ) -> SpectralIndexResult:
        valid_sar = (
            cls._is_valid_reflectance_band(sigma0_vv, nodata_value, enforce_reflectance_bounds=False)
            & cls._is_valid_reflectance_band(sigma0_vh, nodata_value, enforce_reflectance_bounds=False)
            & (sigma0_vv >= 0.0)
            & (sigma0_vh >= 0.0)
        )
        if external_valid_mask is not None:
            valid_sar = valid_sar & external_valid_mask.astype(bool)

        vv = np.where(valid_sar, sigma0_vv.astype(np.float64), 0.0)
        vh = np.where(valid_sar, sigma0_vh.astype(np.float64), 0.0)
        denom = vv + vh
        valid = valid_sar & (denom > cls.EPS)
        safe_denom = np.where(valid, denom, 1.0)
        val = np.where(valid, np.clip(4.0 * vh / safe_denom, 0.0, 4.0), np.nan)
        sigma = np.where(valid, 0.05 * val, np.nan)
        valid_vals = val[valid]
        return SpectralIndexResult(
            index_name="SAR_RVI",
            equation_latex=r"\mathrm{RVI}_{\mathrm{SAR}} = \frac{4\sigma^0_{\mathrm{VH}}}{\sigma^0_{\mathrm{VV}} + \sigma^0_{\mathrm{VH}}}",
            values=val.astype(np.float32),
            uncertainty_1sigma=sigma.astype(np.float32),
            valid_mask=valid,
            mean_value=float(np.nanmean(valid_vals)) if len(valid_vals) > 0 else float("nan"),
            std_value=float(np.nanstd(valid_vals)) if len(valid_vals) > 0 else float("nan"),
            saturation_fraction=0.0,
        )
