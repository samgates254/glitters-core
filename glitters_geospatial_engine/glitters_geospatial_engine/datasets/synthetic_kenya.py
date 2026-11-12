"""
Synthetic Kenya-Calibrated Geospatial Datasets Generator (`datasets/synthetic_kenya.py`)
========================================================================================

Generates realistic, deterministic, purely synthetic geospatial datasets calibrated
to Kenya's coordinate reference systems (Nairobi / Kiambu UTM Zone 37S EPSG:32737,
Turkana UTM Zone 36N EPSG:32636, Kisumu UTM Zone 36S EPSG:32736) for unit testing,
property-based testing, adversarial validation, and performance benchmarking.

Clearly marked as `SYNTHETIC_BENCHMARK_DATASET` so synthetic data is never confused
with real-world observations or government cadastral records.
"""

from __future__ import annotations
from typing import Any, Dict, List, Tuple
import numpy as np
from shapely.geometry import LineString, Polygon, box

from ..crs.kenya_crs import KenyaAdminHierarchyTag, ObservationAuthority
from ..network_analysis.routing import SpatialNetworkEngine
from ..parcels.intelligence import ParcelRecord
from ..raster.pipeline import AffineGeotransform, GeoRasterTile


class SyntheticKenyaDatasetGenerator:
    """
    Deterministic generator for synthetic Kenyan parcel fabrics, building footprints,
    multi-temporal Sentinel-2 / VHR rasters, Copernicus-like DEMs, and road networks.
    """

    @staticmethod
    def generate_nairobi_parcel_fabric(
        n_rows: int = 10,
        n_cols: int = 10,
        origin_easting_m: float = 257_500.0,
        origin_northing_m: float = 9_857_000.0,
        lot_width_m: float = 30.0,
        lot_depth_m: float = 50.0,
        road_corridor_m: float = 12.0,
        inject_anomalies: bool = True,
    ) -> Dict[str, Any]:
        """
        Generate a synthetic urban/peri-urban parcel block in Nairobi/Kiambu (EPSG:32737,
        WGS84 / UTM Zone 37S) with known ground-truth areas, plus controlled overlaps,
        interstitial sliver gaps, and duplicate records when `inject_anomalies=True`.
        """
        parcels: List[ParcelRecord] = []
        idx = 1
        for r in range(n_rows):
            block_offset_y = (r // 2) * road_corridor_m
            y0 = origin_northing_m + r * lot_depth_m + block_offset_y
            y1 = y0 + lot_depth_m
            for c in range(n_cols):
                block_offset_x = (c // 5) * road_corridor_m
                x0 = origin_easting_m + c * lot_width_m + block_offset_x
                x1 = x0 + lot_width_m
                poly = box(x0, y0, x1, y1)
                parcels.append(
                    ParcelRecord(
                        parcel_id=f"SYN_NRB_{idx:05d}",
                        geometry_utm=poly,
                        utm_epsg=32737,
                        observation_date="2026-01-15",
                        authority=ObservationAuthority.INFERRED_GEOMETRIC_BOUNDARY,
                        is_legally_verified_cadastre=False,
                        stated_deed_area_m2=lot_width_m * lot_depth_m,
                        metadata={"dataset_type": "SYNTHETIC_BENCHMARK_DATASET"},
                    )
                )
                idx += 1

        injected_info: Dict[str, str] = {}
        if inject_anomalies and len(parcels) >= 6:
            # 1. Inject an encroaching parcel overlapping parcel 0 by 2.5m x 50m = 125 m^2
            p0_bounds = parcels[0].geometry_utm.bounds
            overlap_poly = box(p0_bounds[2] - 2.5, p0_bounds[1], p0_bounds[2] + 27.5, p0_bounds[3])
            parcels[1] = ParcelRecord(
                parcel_id=parcels[1].parcel_id,
                geometry_utm=overlap_poly,
                utm_epsg=32737,
                observation_date="2026-01-15",
                authority=ObservationAuthority.INFERRED_GEOMETRIC_BOUNDARY,
                is_legally_verified_cadastre=False,
                stated_deed_area_m2=lot_width_m * lot_depth_m,
            )
            injected_info["substantive_overlap_pair"] = f"{parcels[0].parcel_id} & {parcels[1].parcel_id}"

            # 2. Inject a 0.8m interstitial gap between parcel 2 and parcel 3
            p3_bounds = parcels[3].geometry_utm.bounds
            gap_poly = box(p3_bounds[0] + 0.8, p3_bounds[1], p3_bounds[2], p3_bounds[3])
            parcels[3] = ParcelRecord(
                parcel_id=parcels[3].parcel_id,
                geometry_utm=gap_poly,
                utm_epsg=32737,
                observation_date="2026-01-15",
                authority=ObservationAuthority.INFERRED_GEOMETRIC_BOUNDARY,
                is_legally_verified_cadastre=False,
                stated_deed_area_m2=lot_width_m * lot_depth_m,
            )
            injected_info["interstitial_gap_between"] = f"{parcels[2].parcel_id} & {parcels[3].parcel_id}"

            # 3. Inject a near-duplicate record of parcel 4 shifted by 0.08 m
            p4_b = parcels[4].geometry_utm.bounds
            dup_poly = box(p4_b[0] + 0.08, p4_b[1] + 0.05, p4_b[2] + 0.08, p4_b[3] + 0.05)
            parcels.append(
                ParcelRecord(
                    parcel_id="SYN_NRB_DUP_00005",
                    geometry_utm=dup_poly,
                    utm_epsg=32737,
                    observation_date="2026-01-15",
                    authority=ObservationAuthority.INFERRED_GEOMETRIC_BOUNDARY,
                    is_legally_verified_cadastre=False,
                )
            )
            injected_info["duplicate_pair"] = f"{parcels[4].parcel_id} & SYN_NRB_DUP_00005"

        return {"parcels": parcels, "injected_anomalies": injected_info}

    @staticmethod
    def generate_bitemporal_multiband_tiles(
        height: int = 128,
        width: int = 128,
        gsd_m: float = 0.5,
        origin_easting_m: float = 257_500.0,
        origin_northing_m: float = 9_858_000.0,
        seed: int = 42,
    ) -> Tuple[GeoRasterTile, GeoRasterTile, Dict[str, Any]]:
        """
        Generate a synthetic bi-temporal 4-band (Blue, Green, Red, NIR, SWIR1 -> 5 bands)
        georeferenced imagery pair in EPSG:32737 with known new building construction,
        vegetation clearing, and sub-pixel shift for testing registration, building
        extraction, and CVA change detection.
        """
        rng = np.random.default_rng(seed)
        transform = AffineGeotransform(
            a=gsd_m, b=0.0, c=origin_easting_m, d=0.0, e=-gsd_m, f=origin_northing_m
        )
        # Bands: (0=Red, 1=NIR, 2=SWIR1, 3=Green, 4=Blue)
        data_t1 = np.zeros((5, height, width), dtype=np.float32)
        # Base healthy vegetation background: low Red (0.06), high NIR (0.42), moderate SWIR1 (0.16)
        data_t1[0] = 0.06 + rng.normal(0.0, 0.004, (height, width))
        data_t1[1] = 0.42 + rng.normal(0.0, 0.008, (height, width))
        data_t1[2] = 0.16 + rng.normal(0.0, 0.005, (height, width))
        data_t1[3] = 0.08 + rng.normal(0.0, 0.004, (height, width))
        data_t1[4] = 0.05 + rng.normal(0.0, 0.003, (height, width))

        # Existing buildings at T1: (r: 20..40, c: 20..45) and (r: 70..90, c: 30..50)
        for r0, r1, c0, c1 in [(20, 40, 20, 45), (70, 90, 30, 50)]:
            data_t1[0, r0:r1, c0:c1] = 0.24
            data_t1[1, r0:r1, c0:c1] = 0.18
            data_t1[2, r0:r1, c0:c1] = 0.36

        # Epoch T2: copy T1 and inject a NEW construction complex at (r: 50..85, c: 75..110)
        data_t2 = data_t1.copy()
        data_t2[0, 50:85, 75:110] = 0.26
        data_t2[1, 50:85, 75:110] = 0.14
        data_t2[2, 50:85, 75:110] = 0.39

        tile_t1 = GeoRasterTile(
            data=np.clip(data_t1, 0.01, 0.99),
            transform=transform,
            epsg=32737,
            band_names=("Red", "NIR", "SWIR1", "Green", "Blue"),
            acquisition_date="2025-02-10",
            sensor_name="Synthetic_VHR_Multispectral_0.5m",
        )
        tile_t2 = GeoRasterTile(
            data=np.clip(data_t2, 0.01, 0.99),
            transform=transform,
            epsg=32737,
            band_names=("Red", "NIR", "SWIR1", "Green", "Blue"),
            acquisition_date="2026-02-10",
            sensor_name="Synthetic_VHR_Multispectral_0.5m",
        )

        # Ground-truth building probability map at T2
        prob_map_t2 = np.full((height, width), 0.05, dtype=np.float32)
        prob_map_t2[20:40, 20:45] = 0.92
        prob_map_t2[70:90, 30:50] = 0.89
        prob_map_t2[50:85, 75:110] = 0.94

        gt_info = {
            "new_construction_Window_rc": (50, 85, 75, 110),
            "new_construction_area_m2": float((85 - 50) * (110 - 75) * (gsd_m ** 2)),
            "prob_map_t2": prob_map_t2,
        }
        return tile_t1, tile_t2, gt_info

    @staticmethod
    def generate_escarpment_valley_dem(
        rows: int = 64, cols: int = 64, cell_size_m: float = 30.0
    ) -> np.ndarray:
        """
        Generate a synthetic 30m DEM resembling the Kikuyu Escarpment / Nairobi River
        valley gradient (elevation 1,620 m to 1,850 m) with an incised river channel
        and 3 artificial closed depressions (sinks) to test Priority-Flood conditioning.
        """
        y = np.linspace(0.0, 1.0, rows)[:, None]
        x = np.linspace(0.0, 1.0, cols)[None, :]
        # Regional tilt from NW (high) to SE (low) plus parabolic valley along x = 0.5
        dem = 1800.0 - 140.0 * y + 45.0 * ((x - 0.5) ** 2) * 4.0
        # Inject 3 closed artificial pits
        dem[rows // 3, cols // 2] -= 12.0
        dem[rows // 2, max(1, cols // 2 - 1)] -= 9.5
        dem[min(rows - 2, (3 * rows) // 4), min(cols - 2, cols // 2 + 1)] -= 15.0
        return dem.astype(np.float32)

    @staticmethod
    def generate_road_network_graph(
        grid_size: int = 8,
        spacing_m: float = 250.0,
        origin_easting_m: float = 257_500.0,
        origin_northing_m: float = 9_857_000.0,
    ) -> Tuple[SpatialNetworkEngine, Dict[str, int]]:
        """
        Build a synthetic hierarchical Kenyan road network graph with Class A trunk roads,
        urban collector tarmac roads, and unpaved murram feeder streets.
        """
        net = SpatialNetworkEngine(utm_epsg=32737)
        for r in range(grid_size):
            for c in range(grid_size):
                nid = r * grid_size + c
                net.add_node(nid, origin_easting_m + c * spacing_m, origin_northing_m + r * spacing_m)

        for r in range(grid_size):
            for c in range(grid_size):
                u = r * grid_size + c
                if c + 1 < grid_size:
                    v = r * grid_size + (c + 1)
                    rclass = (
                        "CLASS_A_INTERNATIONAL_TRUNK_TARMAC"
                        if r == 0
                        else ("URBAN_ARTERIAL_TARMAC" if r % 2 == 0 else "GRADED_MURRAM_ALL_WEATHER")
                    )
                    net.add_edge(u, v, length_m=spacing_m * 1.08, road_class=rclass)
                if r + 1 < grid_size:
                    v = (r + 1) * grid_size + c
                    rclass = (
                        "CLASS_B_NATIONAL_TRUNK_TARMAC"
                        if c == 0
                        else ("URBAN_ARTERIAL_TARMAC" if c % 2 == 0 else "GRADED_MURRAM_ALL_WEATHER")
                    )
                    net.add_edge(u, v, length_m=spacing_m * 1.08, road_class=rclass)

        landmarks = {
            "arterial_node": 0,
            "hospital_node": grid_size - 1,
            "school_node": (grid_size // 2) * grid_size + (grid_size // 2),
        }
        return net, landmarks
