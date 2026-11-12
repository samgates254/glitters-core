"""
Empirical Performance & Algorithmic Verification Benchmark Suite (`benchmarks/run_benchmarks.py` v1.1.0)
========================================================================================================

Executes reproducible benchmarks inside the sandbox VM measuring:
1. Spatial Indexing Build Time, Memory Footprint, and Query Latency across N=10,000 synthetic
   Kenyan parcels (STRtree R-Tree, 2D cKDTree, Planar Quadtree, Hilbert-Clustered BRIN vs.
   Unclustered BRIN, GlittersHexGridIndex, GlittersHilbertGridIndex, Geohash).
2. Sparse CSR Local Moran's I on N=5,000 parcels (verifying O(N * k) memory efficiency).
3. Remote Sensing, Affine Resampling & NoData-Aware Terrain Processing Throughput.
4. Road Network Routing Speedup (Dijkstra vs. A* vs. Contraction Hierarchies vs. Multi-Source
   Shortest Paths).
5. Realistic Noisy Synthetic Building Vectorization Benchmark (24 ground-truth structures with
   speckle noise, false-positive roof clutter, and cloud occlusion — explicitly labeled as
   `SYNTHETIC_VECTORIZATION_STRESS_TEST` rather than real-world satellite inference accuracy).
"""

from __future__ import annotations
import json
from pathlib import Path
import time
import tracemalloc
from typing import Any, Dict, List
import numpy as np
from shapely.geometry import box
from shapely.strtree import STRtree
from scipy.spatial import cKDTree

from glitters_geospatial_engine.geometry.algorithms import ComputationalGeometryEngine
from glitters_geospatial_engine.spatial_indexing.indexes import (
    BRINSpatialIndex,
    GlittersHexGridIndex,
    GlittersHilbertGridIndex,
    PlanarQuadtree,
    encode_geohash,
)
from glitters_geospatial_engine.remote_sensing.indices import SpectralIndicesEngine
from glitters_geospatial_engine.raster.pipeline import AffineGeotransform, GeoRasterTile, RasterProcessingPipeline
from glitters_geospatial_engine.terrain.hydrology import TerrainAndHydrologyEngine
from glitters_geospatial_engine.building_detection.detector import BuildingAndLandDetectionEngine
from glitters_geospatial_engine.uncertainty.propagation import (
    ScientificValidationFramework,
    UncertaintyEngine,
)
from glitters_geospatial_engine.datasets.synthetic_kenya import SyntheticKenyaDatasetGenerator


def benchmark_spatial_indexes(n_parcels: int = 10_000, n_queries: int = 200) -> Dict[str, Any]:
    rng = np.random.default_rng(2026)
    xs = rng.uniform(250_000.0, 270_000.0, size=n_parcels)
    ys = rng.uniform(9_850_000.0, 9_870_000.0, size=n_parcels)
    ws = rng.uniform(20.0, 60.0, size=n_parcels)
    hs = rng.uniform(25.0, 80.0, size=n_parcels)

    polys = [box(xs[i], ys[i], xs[i] + ws[i], ys[i] + hs[i]) for i in range(n_parcels)]
    bboxes = np.column_stack([xs, ys, xs + ws, ys + hs])
    centroids = np.column_stack([xs + 0.5 * ws, ys + 0.5 * hs])

    query_boxes = [
        (float(qx), float(qy), float(qx + 400.0), float(qy + 400.0))
        for qx, qy in zip(
            rng.uniform(251_000.0, 268_000.0, size=n_queries),
            rng.uniform(9_851_000.0, 9_868_000.0, size=n_queries),
        )
    ]
    query_polys = [box(*qb) for qb in query_boxes]

    results: Dict[str, Any] = {"n_parcels": n_parcels, "n_queries": n_queries, "indexes": {}}

    # 1. STRtree (Packed R-Tree)
    tracemalloc.start()
    t0 = time.perf_counter()
    strtree = STRtree(polys)
    build_ms = (time.perf_counter() - t0) * 1000.0
    _, peak_mem = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    t0 = time.perf_counter()
    total_hits = sum(len(strtree.query(qp, predicate="intersects")) for qp in query_polys)
    q_ms = ((time.perf_counter() - t0) * 1000.0) / n_queries
    results["indexes"]["STRtree_RTree"] = {
        "build_time_ms": round(build_ms, 3),
        "avg_query_latency_ms": round(q_ms, 4),
        "peak_memory_kb": round(peak_mem / 1024.0, 2),
        "total_hits": int(total_hits),
    }

    # 2. 2D cKDTree
    tracemalloc.start()
    t0 = time.perf_counter()
    kdtree = cKDTree(centroids)
    build_ms = (time.perf_counter() - t0) * 1000.0
    _, peak_mem = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    t0 = time.perf_counter()
    kd_hits = sum(len(kdtree.query_ball_point([(qb[0] + qb[2]) * 0.5, (qb[1] + qb[3]) * 0.5], r=250.0)) for qb in query_boxes)
    q_ms = ((time.perf_counter() - t0) * 1000.0) / n_queries
    results["indexes"]["cKDTree_2D"] = {
        "build_time_ms": round(build_ms, 3),
        "avg_query_latency_ms": round(q_ms, 4),
        "peak_memory_kb": round(peak_mem / 1024.0, 2),
        "total_hits": int(kd_hits),
    }

    # 3. Planar Quadtree
    tracemalloc.start()
    t0 = time.perf_counter()
    qt = PlanarQuadtree((250_000.0, 9_850_000.0, 270_100.0, 9_870_100.0), max_items=24, max_depth=8)
    for i in range(n_parcels):
        qt.insert(i, tuple(bboxes[i]))
    build_ms = (time.perf_counter() - t0) * 1000.0
    _, peak_mem = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    t0 = time.perf_counter()
    qt_hits = sum(len(qt.query_bbox(qb)) for qb in query_boxes)
    q_ms = ((time.perf_counter() - t0) * 1000.0) / n_queries
    results["indexes"]["Planar_Quadtree"] = {
        "build_time_ms": round(build_ms, 3),
        "avg_query_latency_ms": round(q_ms, 4),
        "peak_memory_kb": round(peak_mem / 1024.0, 2),
        "total_hits": int(qt_hits),
    }

    # 4. BRIN Unclustered vs. Hilbert-Clustered
    brin_unclustered = BRINSpatialIndex(bboxes, pages_per_range=128)
    t0 = time.perf_counter()
    unclust_blocks = sum(brin_unclustered.query_bbox(qb)[1] for qb in query_boxes)
    unclust_q_ms = ((time.perf_counter() - t0) * 1000.0) / n_queries

    hilbert_keys = [
        GlittersHilbertGridIndex.xy_to_hilbert(
            int((cx - 250_000.0) / 20_000.0 * 65535),
            int((cy - 9_850_000.0) / 20_000.0 * 65535),
            order=16,
        )
        for cx, cy in centroids
    ]
    order_idx = np.argsort(hilbert_keys)
    bboxes_sorted = bboxes[order_idx]
    brin_clustered = BRINSpatialIndex(bboxes_sorted, pages_per_range=128)
    t0 = time.perf_counter()
    clust_blocks = sum(brin_clustered.query_bbox(qb)[1] for qb in query_boxes)
    clust_q_ms = ((time.perf_counter() - t0) * 1000.0) / n_queries

    results["indexes"]["BRIN_Unclustered"] = {
        "avg_query_latency_ms": round(unclust_q_ms, 4),
        "avg_blocks_scanned_per_query": round(unclust_blocks / n_queries, 2),
        "total_blocks": len(brin_unclustered.summaries),
    }
    results["indexes"]["BRIN_Hilbert_Clustered"] = {
        "avg_query_latency_ms": round(clust_q_ms, 4),
        "avg_blocks_scanned_per_query": round(clust_blocks / n_queries, 2),
        "total_blocks": len(brin_clustered.summaries),
        "block_scan_reduction_pct": round((1.0 - clust_blocks / max(unclust_blocks, 1)) * 100.0, 2),
    }

    # 5. Grid Encoding Throughput (GLITTERS Axial Hex Res-9, GLITTERS Planar Hilbert Level-16, Geohash-8)
    lons = rng.uniform(36.70, 37.00, size=n_parcels)
    lats = rng.uniform(-1.40, -1.15, size=n_parcels)

    t0 = time.perf_counter()
    _ = [GlittersHexGridIndex.latlon_to_glitters_hex_id(lats[i], lons[i], 9) for i in range(n_parcels)]
    hex_ms = (time.perf_counter() - t0) * 1000.0

    t0 = time.perf_counter()
    _ = [GlittersHilbertGridIndex.encode_hilbert_cell_id(lons[i], lats[i], 16) for i in range(n_parcels)]
    hilb_ms = (time.perf_counter() - t0) * 1000.0

    t0 = time.perf_counter()
    _ = [encode_geohash(lons[i], lats[i], 8) for i in range(n_parcels)]
    gh_ms = (time.perf_counter() - t0) * 1000.0

    results["grid_encoding_10k_points_ms"] = {
        "GlittersHexGrid_Res9_ms": round(hex_ms, 2),
        "GlittersHilbertGrid_Level16_ms": round(hilb_ms, 2),
        "Geohash_Base32_Len8_ms": round(gh_ms, 2),
    }

    # 6. Sparse CSR Local Moran's I on N=5,000 points
    geom_engine = ComputationalGeometryEngine()
    t0 = time.perf_counter()
    m_res = geom_engine.local_morans_i(centroids[:5000], ws[:5000] * hs[:5000], bandwidth_m=400.0)
    moran_ms = (time.perf_counter() - t0) * 1000.0
    results["sparse_local_morans_i_5000_points"] = {
        "execution_time_ms": round(moran_ms, 2),
        "sparse_nnz_entries": m_res["sparse_nnz"],
        "dense_matrix_equivalent_entries": 5000 * 5000,
        "memory_sparsity_reduction_pct": round((1.0 - m_res["sparse_nnz"] / 25_000_000.0) * 100.0, 2),
    }
    return results


def benchmark_raster_and_remote_sensing() -> Dict[str, Any]:
    rng = np.random.default_rng(99)
    h, w = 1024, 1024
    nir = rng.uniform(0.10, 0.55, size=(h, w)).astype(np.float32)
    red = rng.uniform(0.03, 0.25, size=(h, w)).astype(np.float32)
    blue = rng.uniform(0.02, 0.18, size=(h, w)).astype(np.float32)
    swir1 = rng.uniform(0.08, 0.40, size=(h, w)).astype(np.float32)
    # Inject 2% NoData pixels (-9999.0) to verify NoData masking speed
    nodata_locs = rng.random((h, w)) < 0.02
    nir[nodata_locs] = -9999.0
    red[nodata_locs] = -9999.0

    t0 = time.perf_counter()
    ndvi_res = SpectralIndicesEngine.ndvi(nir, red, nodata_value=-9999.0)
    ndvi_ms = (time.perf_counter() - t0) * 1000.0

    t0 = time.perf_counter()
    _ = SpectralIndicesEngine.evi(nir, red, blue, nodata_value=-9999.0)
    evi_ms = (time.perf_counter() - t0) * 1000.0

    t0 = time.perf_counter()
    _ = SpectralIndicesEngine.bsi(swir1, red, nir, blue, nodata_value=-9999.0)
    bsi_ms = (time.perf_counter() - t0) * 1000.0

    dem = SyntheticKenyaDatasetGenerator.generate_escarpment_valley_dem(rows=256, cols=256)
    dem[0:10, 0:10] = -9999.0
    t0 = time.perf_counter()
    _ = TerrainAndHydrologyEngine.analyze_terrain(dem, cell_size_m=30.0, nodata_value=-9999.0)
    terrain_ms = (time.perf_counter() - t0) * 1000.0

    return {
        "tile_dimensions": f"{h}x{w} ({h * w:,} pixels, 2% NoData masked)",
        "ndvi_valid_pixel_ratio": round(float(np.mean(ndvi_res.valid_mask)), 4),
        "ndvi_with_jacobian_uncertainty_ms": round(ndvi_ms, 2),
        "ndvi_throughput_mpixels_per_sec": round((h * w / 1e6) / (ndvi_ms / 1000.0), 2),
        "evi_with_jacobian_uncertainty_ms": round(evi_ms, 2),
        "bsi_with_jacobian_uncertainty_ms": round(bsi_ms, 2),
        "dem_256x256_full_hydrology_pipeline_ms": round(terrain_ms, 2),
    }


def benchmark_network_routing() -> Dict[str, Any]:
    net, _ = SyntheticKenyaDatasetGenerator.generate_road_network_graph(grid_size=20, spacing_m=200.0)
    t0 = time.perf_counter()
    ch_stats = net.build_contraction_hierarchy()
    ch_build_ms = (time.perf_counter() - t0) * 1000.0

    pairs = [(0, 399), (15, 380), (40, 355), (100, 299), (5, 390)]
    dijk_times = []
    astar_times = []
    ch_times = []
    dijk_visited = 0
    astar_visited = 0

    for s, t in pairs:
        t0 = time.perf_counter()
        rd = net.shortest_path_dijkstra(s, t)
        dijk_times.append((time.perf_counter() - t0) * 1000.0)
        dijk_visited += rd.visited_nodes_count

        t0 = time.perf_counter()
        ra = net.shortest_path_astar(s, t)
        astar_times.append((time.perf_counter() - t0) * 1000.0)
        astar_visited += ra.visited_nodes_count

        t0 = time.perf_counter()
        _ = net.query_contraction_hierarchy(s, t)
        ch_times.append((time.perf_counter() - t0) * 1000.0)

    t0 = time.perf_counter()
    _ = net.multi_source_nearest_facility([0, 50, 100, 150, 200, 250, 300, 350])
    mssp_ms = (time.perf_counter() - t0) * 1000.0

    return {
        "graph_nodes": 400,
        "ch_precomputation_ms": round(ch_build_ms, 2),
        "ch_shortcuts_added": ch_stats["shortcuts_added"],
        "dijkstra_avg_ms": round(float(np.mean(dijk_times)), 4),
        "astar_avg_ms": round(float(np.mean(astar_times)), 4),
        "contraction_hierarchy_query_avg_ms": round(float(np.mean(ch_times)), 4),
        "dijkstra_avg_visited_nodes": round(dijk_visited / len(pairs), 1),
        "astar_avg_visited_nodes": round(astar_visited / len(pairs), 1),
        "mssp_all_400_nodes_to_8_facilities_ms": round(mssp_ms, 3),
    }


def benchmark_scientific_validation() -> Dict[str, Any]:
    """
    Synthetic vectorization & uncertainty stress test over 20 ground-truth buildings with
    injected boundary probability jitter, 2 cloud-occluded buildings (false negatives),
    and 2 high-probability bare-rock clutter patches (false positives).
    Explicitly labeled `SYNTHETIC_VECTORIZATION_STRESS_TEST`.
    """
    rng = np.random.default_rng(202611)
    h, w = 256, 256
    tf = AffineGeotransform(a=0.5, b=0.0, c=257_500.0, d=0.0, e=-0.5, f=9_858_000.0)
    prob_map = rng.uniform(0.02, 0.25, size=(h, w)).astype(np.float32)
    cloud_mask = np.zeros((h, w), dtype=bool)

    gt_boxes: List[Any] = []
    # Place 20 ground-truth buildings on a 4x5 grid
    idx = 0
    for r_slot in range(4):
        for c_slot in range(5):
            r0 = 15 + r_slot * 55
            c0 = 15 + c_slot * 45
            bh = int(rng.integers(14, 24))
            bw = int(rng.integers(16, 28))
            r1, c1 = r0 + bh, c0 + bw
            x0, y0 = tf.pixel_to_xy(c0, r0)
            x1, y1 = tf.pixel_to_xy(c1, r1)
            gt_boxes.append(box(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)))

            # Add realistic noisy probability interior + eroded boundary
            prob_map[r0:r1, c0:c1] = rng.uniform(0.62, 0.94, size=(bh, bw)).astype(np.float32)
            # Erode 1-pixel corner noise on some buildings
            prob_map[r0, c0:c0+3] = 0.35
            if idx in (3, 14):
                # Occlude 2 buildings under cloud mask -> tests cloud exclusion (2 expected FNs)
                cloud_mask[r0 - 2 : r1 + 2, c0 - 2 : c1 + 2] = True
            idx += 1

    # Inject 2 false-positive clutter objects in unclouded gaps
    prob_map[5:12, 230:244] = 0.78
    prob_map[235:245, 230:244] = 0.81

    tile = GeoRasterTile(
        data=np.full((3, h, w), 0.25, dtype=np.float32),
        transform=tf,
        epsg=32737,
        band_names=("Red", "Green", "Blue"),
        acquisition_date="2026-02-10",
        sensor_name="Synthetic_VHR_0.5m_StressTest",
        cloud_mask=cloud_mask,
    )

    detected_bldgs = BuildingAndLandDetectionEngine.extract_building_footprints_from_probability_map(
        prob_map, tile, prob_threshold=0.55
    )
    bldg_eval = ScientificValidationFramework.evaluate_building_detection_instances(
        [b.geometry_utm for b in detected_bldgs], gt_boxes, iou_threshold=0.50
    )

    sample_parcel = box(257_500.0, 9_857_000.0, 257_530.0, 9_857_050.0)
    ana_unc = UncertaintyEngine.analytical_shoelace_area_uncertainty(sample_parcel, vertex_sigma_m=0.15)
    mc_unc = UncertaintyEngine.monte_carlo_area_uncertainty(sample_parcel, vertex_sigma_m=0.15, n_simulations=1000)

    return {
        "benchmark_scope": "SYNTHETIC_VECTORIZATION_AND_UNCERTAINTY_STRESS_TEST (Not a claim of trained model accuracy on real satellite imagery)",
        "building_vectorization_stress_test_20_gt": {
            "precision": round(bldg_eval["precision"], 4),
            "recall": round(bldg_eval["recall"], 4),
            "f1": round(bldg_eval["f1"], 4),
            "mean_tp_iou": round(bldg_eval["mean_tp_iou"], 4),
            "tp": bldg_eval["tp"],
            "fp": bldg_eval["fp"],
            "fn": bldg_eval["fn"],
        },
        "area_uncertainty_validation_1500m2_parcel": {
            "analytical_sigma_m2": round(ana_unc["sigma_area_m2"], 3),
            "monte_carlo_1000_runs_sigma_m2": round(mc_unc["mc_std_area_m2"], 3),
            "analytical_95ci_halfwidth_m2": round(ana_unc["ci95_halfwidth_m2"], 3),
        },
    }


def run_all_benchmarks() -> Dict[str, Any]:
    report = {
        "spatial_indexing_benchmarks": benchmark_spatial_indexes(),
        "raster_and_remote_sensing_benchmarks": benchmark_raster_and_remote_sensing(),
        "network_routing_benchmarks": benchmark_network_routing(),
        "scientific_validation_benchmarks": benchmark_scientific_validation(),
    }
    out_path = Path(__file__).resolve().parent / "benchmark_results.json"
    out_path.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    run_all_benchmarks()
